"""
Dashboard Login Security
========================
Passkeys (WebAuthn) and optional TOTP 2FA with one-time backup codes for the
dashboard login.

Per-user security data lives on the user objects inside config/auth.json
(the same GitHub-backed store that already holds dashboard passwords):

    user['totp_secret']    base32 TOTP secret (absent = 2FA off)
    user['backup_codes']   list of SHA-256 hex hashes of one-time codes
    user['passkeys']       [{id, public_key, alg, sign_count, device, created_at}]
    user['ext_credentials'] [{id, hash, label, created_at}] — browser-extension
                            login credentials; only the SHA-256 hash is stored

All functions are pure and side-effect free except where noted — the routes in
dashboard/app.py own the session/request handling and config persistence.

WebAuthn requires a secure context (HTTPS or localhost). The RP id and origin
are derived from the dashboard's own URL at request time.
"""

import base64
import hashlib
import io
import re
import secrets
import time
from typing import Optional

from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
)
from webauthn.helpers import (
    options_to_json,
    bytes_to_base64url,
    base64url_to_bytes,
    parse_cbor,
    encode_cbor,
    decode_credential_public_key,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
    AttestationConveyancePreference,
    PublicKeyCredentialDescriptor,
)

RP_NAME = "Limey"
# Seconds a code from the authenticator app stays valid on either side of the
# current 30s window (accepts the previous/next window too).
TOTP_VALID_WINDOW = 1


# ── TOTP (authenticator app) ───────────────────────────

def generate_totp_secret() -> str:
    """Random base32 TOTP secret (16 bytes of entropy)."""
    import pyotp
    return pyotp.random_base32()


def totp_uri(secret: str, username: str, issuer: str = "Limey") -> str:
    """otpauth:// provisioning URI shown in authenticator apps / QR code."""
    import pyotp
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def totp_verify(secret: Optional[str], code) -> bool:
    """Verify a 6-digit authenticator code against a TOTP secret."""
    if not secret or not code:
        return False
    import pyotp
    try:
        return pyotp.TOTP(secret).verify(str(code).strip().replace(" ", ""), valid_window=TOTP_VALID_WINDOW)
    except Exception:
        return False


def totp_qr_data_url(uri: str) -> str:
    """Render an otpauth URI as a PNG data URL for inline display."""
    import qrcode
    qr = qrcode.make(uri)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ── Backup codes ───────────────────────────────────────

BACKUP_CODE_RE = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")


def generate_backup_codes(count: int = 10) -> list:
    """Plaintext backup codes (XXXX-XXXX-XXXX). Return them once; store only hashes."""
    codes = []
    for _ in range(count):
        chunk = "-".join(secrets.token_hex(2).upper()[:4] for _ in range(3))
        codes.append(chunk)
    return codes


def hash_backup_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def verify_backup_code(user: dict, code) -> bool:
    """Check a code against the user's stored hashes; consume it on success.

    Mutates user['backup_codes'] in place (the caller persists the config).
    """
    code = (code or "").strip().upper()
    if not code or not BACKUP_CODE_RE.match(code) or not user.get("backup_codes"):
        return False
    hashed = hash_backup_code(code)
    codes = user["backup_codes"]
    if hashed in codes:
        codes.remove(hashed)
        user["backup_codes"] = codes
        return True
    return False


# ── Browser-extension login credentials ────────────────
# The Limey browser extension stores a high-entropy token it was given during
# pairing (My Account → Extension Login). Only its SHA-256 hash lives on the
# server. A successful login is treated like a passkey: possession of the
# token is the factor, so the TOTP step is skipped.


def generate_ext_token() -> str:
    """High-entropy credential handed to the extension exactly once."""
    return secrets.token_urlsafe(32)


def hash_ext_token(token: str) -> str:
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


def find_user_by_ext_token(cfg, token) -> Optional[dict]:
    """Find the user whose ext_credentials contains this token's hash."""
    if not token:
        return None
    hashed = hash_ext_token(token)
    for user in (cfg or {}).get("users", []):
        for cred in user.get("ext_credentials") or []:
            if cred.get("hash") == hashed:
                return user
    return None


# ── WebAuthn (passkeys) ────────────────────────────────

def build_rp(origin: str):
    """Derive (rp_id, expected_origin) from the request's URL root.

    rp_id is the registrable domain (browser will compare it against the
    credential's stored RP id); expected_origin is what the browser sends in
    clientDataJSON and what verification must match exactly.
    """
    from urllib.parse import urlparse
    host = urlparse(origin).hostname or "localhost"
    return host, origin.rstrip("/")


def new_challenge() -> bytes:
    return secrets.token_bytes(32)


def _descriptors(credential_ids):
    return [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(cid))
        for cid in (credential_ids or [])
    ]


def registration_options(origin: str, username: str, exclude_credential_ids=None):
    """Options for the browser's navigator.credentials.create() ceremony.

    Returns (options_dict, challenge_bytes, expected_origin). The challenge
    must be kept server-side and passed to verify_registration later.
    """
    rp_id, expected_origin = build_rp(origin)
    user_id = hashlib.sha256(username.encode("utf-8")).digest()
    challenge = new_challenge()
    exclude = exclude_credential_ids or None
    opts = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_id=user_id,
        user_name=username,
        user_display_name=username,
        challenge=challenge,
        timeout=60000,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=_descriptors(exclude) if exclude else None,
    )
    return json_loads(options_to_json(opts)), challenge, expected_origin


# COSE algorithm id -> raw r||s signature length in bytes. These are the only
# algorithms whose WebAuthn signatures use the raw encoding; Ed25519 (-8) and
# RSA (-257/-258/-259) use a single fixed-size/different format and must never
# be reinterpreted as raw r||s (Ed25519 signatures are also 64 bytes!).
_EC_RAW_ALGS = {-7: 64, -35: 96, -36: 132}


def _raw_sig_to_der(sig: bytes, alg=None) -> bytes:
    """Convert a raw r||s ECDSA signature to DER-encoded form.

    WebAuthn authenticators/browsers transmit ES256/ES384/ES512 signatures as
    raw r||s (RFC 9053), but py_webauthn hands them straight to `cryptography`'s
    EC verify, which on the pinned cryptography version only accepts DER-encoded
    ECDSA signatures. Converting here keeps passkeys working regardless of the
    installed cryptography version.

    Conversion is gated on `alg`: only COSE algorithms that use raw r||s are
    converted, and only when the length matches. Anything else (already-DER,
    Ed25519, RSA, unknown) passes through untouched.
    """
    expected_len = _EC_RAW_ALGS.get(alg)
    if expected_len is None or not sig or len(sig) != expected_len:
        return sig
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    half = expected_len // 2
    r = int.from_bytes(sig[:half], "big", signed=False)
    s = int.from_bytes(sig[half:], "big", signed=False)
    return encode_dss_signature(r, s)


def _passkey_alg(passkey: dict):
    """COSE algorithm id for a stored passkey (or None if unknown)."""
    alg = passkey.get("alg")
    if alg is not None:
        return alg
    try:
        return decode_credential_public_key(base64url_to_bytes(passkey["public_key"])).alg
    except Exception:
        return None


def _normalize_authentication_credential(credential: dict, alg=None) -> dict:
    """Normalize an assertion's signature from raw r||s to DER (if needed)."""
    try:
        cred = dict(credential)
        resp = dict(cred.get("response") or {})
        sig_b64 = resp.get("signature")
        if not sig_b64:
            return credential
        resp["signature"] = bytes_to_base64url(
            _raw_sig_to_der(base64url_to_bytes(sig_b64), alg)
        )
        cred["response"] = resp
        return cred
    except Exception:
        return credential


def _normalize_registration_credential(credential: dict) -> dict:
    """Normalize any signature inside a registration's attestation object.

    With attestation conveyance "none" (our default) authenticators send the
    "none" format, which has no signature to check. Some platforms instead send
    packed/self attestation, whose `attStmt.sig` is a raw r||s ECDSA signature
    — convert it to DER so verification succeeds on any cryptography version.
    When attStmt carries an explicit alg it is honored; when absent (fido-u2f /
    apple formats are ES256-only by spec), only 64-byte signatures are touched.
    """
    try:
        cred = dict(credential)
        resp = dict(cred.get("response") or {})
        att_b64 = resp.get("attestationObject")
        if not att_b64:
            return credential
        att = parse_cbor(base64url_to_bytes(att_b64))
        att_stmt = att.get("attStmt") if isinstance(att, dict) else None
        if not isinstance(att_stmt, dict) or not att_stmt.get("sig"):
            return credential
        alg = att_stmt.get("alg")
        if alg is None:
            # fido-u2f/apple are ES256-only — safe to treat 64-byte as raw ES256
            alg = -7
        att_stmt["sig"] = _raw_sig_to_der(att_stmt["sig"], alg)
        att["attStmt"] = att_stmt
        resp["attestationObject"] = bytes_to_base64url(encode_cbor(att))
        cred["response"] = resp
        return cred
    except Exception:
        return credential


def verify_registration(origin: str, challenge: bytes, credential: dict, rp_id: str) -> dict:
    """Verify a registration response; returns the credential to store.

    Raises webauthn.exceptions on failure.
    """
    credential = _normalize_registration_credential(credential)
    verification = verify_registration_response(
        credential=credential,
        expected_challenge=challenge,
        expected_origin=origin,
        expected_rp_id=rp_id,
        require_user_verification=False,
    )
    return {
        "id": bytes_to_base64url(verification.credential_id),
        "public_key": bytes_to_base64url(verification.credential_public_key),
        "alg": int(decode_credential_public_key(verification.credential_public_key).alg),
        "sign_count": verification.sign_count,
    }


def authentication_options(origin: str, user: Optional[dict] = None):
    """Options for the browser's navigator.credentials.get() ceremony.

    When `user` is given, only that user's passkeys are offered; otherwise the
    authenticator may present any discoverable credential registered to this RP.
    Returns (options_dict, challenge_bytes, expected_origin).
    """
    rp_id, expected_origin = build_rp(origin)
    challenge = new_challenge()
    allow = None
    if user:
        ids = [p["id"] for p in (user.get("passkeys") or [])]
        if ids:
            allow = _descriptors(ids)
    opts = generate_authentication_options(
        rp_id=rp_id,
        challenge=challenge,
        timeout=60000,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return json_loads(options_to_json(opts)), challenge, expected_origin


def verify_authentication(origin: str, challenge: bytes, credential: dict, rp_id: str, passkey: dict) -> int:
    """Verify an assertion; returns the new sign count to persist.

    Raises webauthn.exceptions on failure.
    """
    credential = _normalize_authentication_credential(credential, _passkey_alg(passkey))
    verification = verify_authentication_response(
        credential=credential,
        expected_challenge=challenge,
        expected_origin=origin,
        expected_rp_id=rp_id,
        credential_public_key=base64url_to_bytes(passkey["public_key"]),
        credential_current_sign_count=passkey.get("sign_count", 0),
        require_user_verification=False,
    )
    return verification.new_sign_count


def find_user_by_passkey(cfg: dict, credential: dict):
    """Find (user, passkey) matching the credential id from a login attempt."""
    cred_id = (credential or {}).get("id") or ""
    if not cred_id:
        return None, None
    for user in cfg.get("users", []):
        for pk in user.get("passkeys") or []:
            if pk.get("id") == cred_id:
                return user, pk
    return None, None


def json_loads(s: str) -> dict:
    import json
    return json.loads(s)
