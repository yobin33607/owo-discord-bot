"""
Limey API Key Manager
=====================
Manage API keys that allow external access to the dashboard's
data endpoints. Keys are tied to a specific user role
(view, manage, admin) and inherit that role's permissions.

Usage (from your code):
    from utils.api_keys import generate_key, validate_key, list_keys, revoke_key
"""

import json
import secrets
import time
from datetime import datetime

from utils.github_data_store import ghd

KEY_PREFIX = "lmk_"  # Limey API Key prefix


# ─────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────

def _load_keys():
    """Load all API keys from the GitHub data repo."""
    data = ghd.read_json("config/api_keys.json", default={"keys": {}})
    if data is None:
        return {}
    return data.get("keys", {})


def _save_keys(keys):
    """Save all API keys to the GitHub data repo."""
    ghd.write_json("config/api_keys.json", {"keys": keys}, message="Update API keys")


# ─────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────

def generate_key(label="", role="view", created_by=""):
    """Generate a new API key.

    Args:
        label: Human-readable name for the key (e.g. "My Script")
        role: Permission role the key inherits (view, manage, admin)
        created_by: Username of the dashboard user who created it

    Returns:
        dict with key metadata (including the full key string)
    """
    if role not in ("view", "manage", "admin"):
        role = "view"

    key_id = secrets.token_hex(12)       # 24-char unique ID
    key_secret = secrets.token_urlsafe(32)  # 43-char secret
    full_key = f"{KEY_PREFIX}{key_id}{key_secret}"

    now = time.time()
    key_entry = {
        "id": key_id,
        "label": label or f"API Key {key_id[:8]}",
        "key": full_key,           # stored so we can look it up
        "role": role,
        "created_by": created_by,
        "created_at": now,
        "last_used_at": None,
        "revoked": False,
    }

    keys = _load_keys()
    keys[key_id] = key_entry
    _save_keys(keys)

    return {
        "id": key_id,
        "label": key_entry["label"],
        "key": full_key,
        "role": role,
        "created_by": created_by,
        "created_at": now,
        "last_used_at": None,
        "revoked": False,
    }


def validate_key(full_key):
    """Validate an API key and return its metadata + role.

    Args:
        full_key: The complete API key string (e.g. "lmk_...")

    Returns:
        dict with role and key metadata if valid, None otherwise.
    """
    if not full_key or not full_key.startswith(KEY_PREFIX):
        return None

    # Extract the key_id from the prefix + 24 hex chars
    key_id = full_key[len(KEY_PREFIX):len(KEY_PREFIX) + 24]

    keys = _load_keys()
    entry = keys.get(key_id)

    if not entry:
        return None
    if entry.get("revoked", False):
        return None
    if entry.get("key") != full_key:
        return None

    # Update last used timestamp in memory only (avoid disk I/O on every call)
    entry["last_used_at"] = time.time()

    return {
        "id": entry["id"],
        "label": entry.get("label", ""),
        "role": entry.get("role", "view"),
        "created_by": entry.get("created_by", ""),
    }


def list_keys(include_revoked=False):
    """List all API keys (without exposing the secret key)."""
    keys = _load_keys()
    result = []
    for kid, entry in keys.items():
        if entry.get("revoked") and not include_revoked:
            continue
        result.append({
            "id": entry["id"],
            "label": entry.get("label", ""),
            "role": entry.get("role", "view"),
            "created_by": entry.get("created_by", ""),
            "created_at": entry.get("created_at"),
            "last_used_at": entry.get("last_used_at"),
            "revoked": entry.get("revoked", False),
            # Never expose the full key in listings
            "key_prefix": entry.get("key", "")[:16] + "...",
        })
    # Sort: newest first
    result.sort(key=lambda k: k.get("created_at", 0), reverse=True)
    return result


def revoke_key(key_id):
    """Revoke an API key so it can no longer be used.

    Returns True if the key was found and revoked, False otherwise.
    """
    keys = _load_keys()
    if key_id not in keys:
        return False
    keys[key_id]["revoked"] = True
    _save_keys(keys)
    return True


def delete_key(key_id):
    """Permanently delete an API key from storage.

    Returns True if the key was found and deleted, False otherwise.
    """
    keys = _load_keys()
    if key_id not in keys:
        return False
    del keys[key_id]
    _save_keys(keys)
    return True
