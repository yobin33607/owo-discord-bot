/* 
 * Login Security (My Account tab)
 * 2FA (TOTP + backup codes) management and passkey (WebAuthn) management.
 */

// ─── Helpers ───────────────────────────────────────────

function arrayBufToB64url(buf) {
    const bytes = new Uint8Array(buf);
    let bin = '';
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function credentialToJSON(cred) {
    if (cred.toJSON) return cred.toJSON();
    const r = {};
    if (cred.response.clientDataJSON) r.clientDataJSON = arrayBufToB64url(cred.response.clientDataJSON);
    if (cred.response.attestationObject) r.attestationObject = arrayBufToB64url(cred.response.attestationObject);
    if (cred.response.authenticatorData) r.authenticatorData = arrayBufToB64url(cred.response.authenticatorData);
    if (cred.response.signature) r.signature = arrayBufToB64url(cred.response.signature);
    if (cred.response.userHandle) r.userHandle = arrayBufToB64url(cred.response.userHandle);
    return { id: cred.id, rawId: arrayBufToB64url(cred.rawId), type: cred.type, response: r };
}

// ─── Status ────────────────────────────────────────────

async function loadSecurityStatus() {
    const el = document.getElementById('security2fa-status');
    if (!el) return;
    try {
        const r = await fetch('/api/auth/security');
        const data = await r.json();
        if (!data.success) {
            el.innerHTML = '<div class="no-data">Failed to load security status.</div>';
            return;
        }
        renderSecurityStatus(data);
    } catch (e) {
        el.innerHTML = '<div class="no-data">Failed to load security status.</div>';
    }
}

function renderSecurityStatus(data) {
    const el = document.getElementById('security2fa-status');

    // ── 2FA row ──
    const twofaState = data.totp_enabled
        ? '<span style="color:#00ff88;font-weight:600;">✅ Enabled</span>' +
          (data.backup_codes_remaining > 0
              ? ` &nbsp;·&nbsp; <span style="color:#888;">${data.backup_codes_remaining} backup code${data.backup_codes_remaining === 1 ? '' : 's'} left</span>`
              : ' &nbsp;·&nbsp; <span style="color:#ff6b6b;">no backup codes left — generate new ones by re-enabling</span>')
        : '<span style="color:#ff6b6b;font-weight:600;">Off</span>';

    const twofaBtn = data.totp_enabled
        ? '<button class="btn-control red" onclick="openTwofaDisable()">Disable 2FA</button>'
        : '<button class="btn-control green" onclick="startTwofaSetup()">Enable 2FA</button>';

    // ── Passkeys ──
    let passkeyRows = '<div class="no-data" style="padding:8px 0;">No passkeys registered.</div>';
    if (data.passkeys && data.passkeys.length) {
        passkeyRows = data.passkeys.map(p => {
            const created = p.created_at ? new Date(p.created_at * 1000).toLocaleString() : 'Unknown';
            return `
                <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
                    <div>
                        <div style="font-weight:600;">🔑 ${escapeHtml(p.device || 'Unknown device')}</div>
                        <div style="font-size:0.75rem;color:#888;">Added ${escapeHtml(created)}</div>
                    </div>
                    <button class="btn-control red" onclick="removePasskey('${escapeHtml(p.id)}')">Remove</button>
                </div>`;
        }).join('');
    }

    el.innerHTML = `
        <div style="display:flex;flex-wrap:wrap;gap:16px;margin-bottom:18px;align-items:center;justify-content:space-between;">
            <div>
                <div style="font-weight:600;margin-bottom:4px;">Authenticator App (2FA)</div>
                <div style="font-size:0.85rem;color:#aaa;">${twofaState}</div>
                <div style="font-size:0.75rem;color:#666;margin-top:4px;">After enabling, every password login requires a 6-digit code. Passkey logins skip this step.</div>
            </div>
            <div>${twofaBtn}</div>
        </div>
        <div style="border-top:1px solid rgba(255,255,255,0.08);padding-top:16px;">
            <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;margin-bottom:6px;">
                <div>
                    <div style="font-weight:600;">Passkeys</div>
                    <div style="font-size:0.75rem;color:#666;">Sign in with your device's fingerprint, face, or security key — no password needed.</div>
                </div>
                <button class="btn-control" onclick="addPasskey()">＋ Add Passkey</button>
            </div>
            <div>${passkeyRows}</div>
        </div>`;
}

// ─── 2FA enable flow ──────────────────────────────────

async function startTwofaSetup() {
    try {
        const r = await fetch('/api/auth/2fa/setup', { method: 'POST' });
        const data = await r.json();
        if (!data.success) {
            alert(data.error || 'Failed to start 2FA setup');
            return;
        }
        document.getElementById('twofa-setup-qr').innerHTML =
            data.qr ? `<img src="${data.qr}" alt="2FA QR code" style="border-radius:8px;max-width:220px;">`
                    : '<div class="no-data">QR unavailable</div>';
        document.getElementById('twofa-setup-secret').textContent = data.secret || '';
        document.getElementById('twofa-setup-code').value = '';
        document.getElementById('twofa-setup-result').textContent = '';
        document.getElementById('twofa-setup-modal').style.display = 'flex';
        setTimeout(() => document.getElementById('twofa-setup-code').focus(), 50);
    } catch (e) {
        alert('Failed to start 2FA setup');
    }
}

function closeTwofaSetup() {
    document.getElementById('twofa-setup-modal').style.display = 'none';
}

async function confirmTwofaEnable() {
    const code = document.getElementById('twofa-setup-code').value.trim();
    const resultEl = document.getElementById('twofa-setup-result');
    if (!code) {
        resultEl.textContent = 'Enter the 6-digit code.';
        return;
    }
    resultEl.textContent = '';
    try {
        const r = await fetch('/api/auth/2fa/enable', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: code })
        });
        const data = await r.json();
        if (!data.success) {
            resultEl.textContent = '❌ ' + (data.error || 'Failed to enable 2FA');
            return;
        }
        closeTwofaSetup();
        showBackupCodes(data.backup_codes || []);
        loadSecurityStatus();
    } catch (e) {
        resultEl.textContent = '❌ Connection error';
    }
}

function showBackupCodes(codes) {
    document.getElementById('twofa-backup-codes').innerHTML = codes.map(c =>
        `<div style="font-family:var(--font-mono);background:#0a0a0c;border:1px solid #333;border-radius:6px;padding:10px;text-align:center;font-size:0.85rem;color:#fff;">${escapeHtml(c)}</div>`
    ).join('');
    document.getElementById('twofa-backup-modal').style.display = 'flex';
}

function closeBackupModal() {
    document.getElementById('twofa-backup-modal').style.display = 'none';
}

function copyBackupCodes() {
    const els = document.querySelectorAll('#twofa-backup-codes div');
    const text = Array.from(els).map(e => e.textContent.trim()).join('\n');
    if (navigator.clipboard && text) {
        navigator.clipboard.writeText(text).then(() => showToast('Backup codes copied'));
    }
}

// ─── 2FA disable flow ─────────────────────────────────

function openTwofaDisable() {
    document.getElementById('twofa-disable-code').value = '';
    document.getElementById('twofa-disable-result').textContent = '';
    document.getElementById('twofa-disable-modal').style.display = 'flex';
    setTimeout(() => document.getElementById('twofa-disable-code').focus(), 50);
}

function closeTwofaDisable() {
    document.getElementById('twofa-disable-modal').style.display = 'none';
}

async function confirmTwofaDisable() {
    const code = document.getElementById('twofa-disable-code').value.trim();
    const resultEl = document.getElementById('twofa-disable-result');
    if (!code) {
        resultEl.textContent = 'Enter the 6-digit code.';
        return;
    }
    resultEl.textContent = '';
    try {
        const r = await fetch('/api/auth/2fa/disable', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: code })
        });
        const data = await r.json();
        if (!data.success) {
            resultEl.textContent = '❌ ' + (data.error || 'Failed to disable 2FA');
            return;
        }
        closeTwofaDisable();
        showToast('2FA disabled');
        loadSecurityStatus();
    } catch (e) {
        resultEl.textContent = '❌ Connection error';
    }
}

// ─── Passkey management ───────────────────────────────

async function addPasskey() {
    if (!window.PublicKeyCredential) {
        alert('Passkeys are not supported in this browser.');
        return;
    }
    const device = (prompt('Name this passkey (e.g. "Phone", "Laptop"):') || '').trim() || 'Browser';
    try {
        const r = await fetch('/api/auth/passkey/register/options');
        const data = await r.json();
        if (!data.success || !data.options) {
            alert(data.error || 'Failed to start passkey registration');
            return;
        }
        const cred = await navigator.credentials.create({ publicKey: data.options });
        const vr = await fetch('/api/auth/passkey/register/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ credential: credentialToJSON(cred), device: device })
        });
        const vd = await vr.json();
        if (vd.success) {
            showToast('Passkey added!');
            loadSecurityStatus();
        } else {
            alert(vd.error || 'Failed to register passkey');
        }
    } catch (e) {
        if (e.name !== 'NotAllowedError') {
            alert('Passkey registration failed: ' + (e.message || e));
        }
    }
}

async function removePasskey(id) {
    if (!confirm('Remove this passkey? You can no longer sign in with it.')) return;
    try {
        const r = await fetch('/api/auth/passkey/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: id })
        });
        const data = await r.json();
        if (data.success) {
            showToast('Passkey removed');
            loadSecurityStatus();
        } else {
            alert(data.error || 'Failed to remove passkey');
        }
    } catch (e) {
        alert('Failed to remove passkey');
    }
}

// Expose for nav hook
window.loadSecurityStatus = loadSecurityStatus;
