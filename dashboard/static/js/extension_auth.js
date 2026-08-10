/*
 * Extension Login management (My Account tab)
 * Pairing / status / revocation for the Limey browser-extension credentials.
 */

async function loadExtAuthStatus() {
    const el = document.getElementById('ext-auth-status');
    if (!el) return;
    let extDetected = false;
    if (window.limeyExtBridge) {
        extDetected = await window.limeyExtBridge.waitForInstalled(1500).catch(() => false);
    }
    let creds = [];
    try {
        const r = await fetch('/api/auth/extension');
        const d = await r.json();
        if (d.success) creds = d.credentials || [];
    } catch (e) { /* fall through to error row */ }

    let rows;
    if (creds.length) {
        rows = creds.map(c => `
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
                <div>
                    <div style="font-weight:600;">🔌 ${escapeHtml(c.label || 'Extension')}</div>
                    <div style="font-size:0.75rem;color:#888;">Linked ${c.created_at ? new Date(c.created_at * 1000).toLocaleString() : 'Unknown'}</div>
                </div>
                <button class="btn-control red" onclick="revokeExtensionCredential('${escapeHtml(c.id)}')">Remove</button>
            </div>`).join('');
    } else {
        rows = '<div class="no-data" style="padding:8px 0;">No extensions linked yet.</div>';
    }

    const extState = extDetected
        ? '<span style="color:#00ff88;font-weight:600;">✅ Extension detected</span>'
        : '<span style="color:#ff6b6b;font-weight:600;">Extension not detected</span> — install the Limey extension and reload this page.';

    el.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px;flex-wrap:wrap;">
            <div style="font-size:0.85rem;">${extState}</div>
            <div style="font-size:0.75rem;color:#666;">Linked: ${creds.length}</div>
        </div>
        ${rows}`;
}

async function pairExtension() {
    const resultEl = document.getElementById('ext-auth-result');
    resultEl.textContent = '';
    if (!window.limeyExtBridge) {
        resultEl.textContent = '❌ Extension bridge not loaded — reload the page.';
        return;
    }
    const installed = await window.limeyExtBridge.waitForInstalled(2000).catch(() => false);
    if (!installed) {
        resultEl.textContent = '❌ Extension not detected. Install the Limey extension, then reload this page.';
        return;
    }
    const label = (prompt('Name this extension login (e.g. "My Chrome"):') || '').trim().slice(0, 40) || 'Browser extension';
    try {
        const r = await fetch('/api/auth/extension/pair', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label: label })
        });
        const d = await r.json();
        if (!d.success) {
            resultEl.textContent = '❌ ' + (d.error || 'Failed to pair extension');
            return;
        }
        const res = await window.limeyExtBridge.request('store', { credential: d.token }, 2500);
        if (res && res.ok) {
            resultEl.textContent = '✅ Extension connected! The "Login with Extension" button now appears on the sign-in page.';
            showToast('Extension connected');
        } else {
            resultEl.textContent = '❌ The site issued a credential but the extension did not confirm storing it. Try again, or reinstall the extension.';
            // Don't leave a useless credential behind — revoke it so the cap
            // isn't consumed by a pairing the extension never received.
            fetch('/api/auth/extension/revoke', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: d.id })
            }).catch(() => {});
        }
        loadExtAuthStatus();
    } catch (e) {
        resultEl.textContent = '❌ Connection error';
    }
}

async function testExtensionConnection() {
    const resultEl = document.getElementById('ext-auth-result');
    if (!window.limeyExtBridge) {
        resultEl.textContent = '❌ Extension bridge not loaded — reload the page.';
        return;
    }
    const installed = await window.limeyExtBridge.waitForInstalled(1500).catch(() => false);
    if (!installed) {
        resultEl.textContent = '❌ Extension not detected.';
        return;
    }
    const res = await window.limeyExtBridge.request('hello', {}, 1500);
    if (res && res.ok) {
        resultEl.textContent = '✅ Extension reachable (v' + (res.version || '?') + ').';
    } else {
        resultEl.textContent = '❌ Extension did not respond to the handshake.';
    }
    loadExtAuthStatus();
}

async function revokeExtensionCredential(id) {
    if (!confirm('Remove this linked extension? It can no longer sign you in.')) return;
    try {
        const r = await fetch('/api/auth/extension/revoke', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: id })
        });
        const d = await r.json();
        if (d.success) {
            showToast('Extension unlinked');
            // Forget the credential inside the extension too, so the login
            // button disappears right away instead of failing on click.
            if (window.limeyExtBridge) {
                window.limeyExtBridge.request('clear', {}, 1000).catch(() => {});
            }
            loadExtAuthStatus();
        } else {
            alert(d.error || 'Failed to remove');
        }
    } catch (e) {
        alert('Failed to remove');
    }
}

// Expose for the My Account nav hook
window.loadExtAuthStatus = loadExtAuthStatus;
