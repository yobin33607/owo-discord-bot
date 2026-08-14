/*
 * One-Time Login Links (My Account tab, admin only)
 * Generate single-use magic links that sign a chosen user in exactly once.
 */

async function loadOneTimeLinks() {
    const card = document.getElementById('otl-card');
    if (!card) return;
    // Admins only — the API enforces this too; the card is just hidden otherwise.
    if (typeof currentUserRole !== 'undefined' && currentUserRole !== 'admin') {
        card.style.display = 'none';
        return;
    }
    card.style.display = '';

    populateUserSelect();

    const list = document.getElementById('otl-list');
    try {
        const r = await fetch('/api/auth/one-time-links');
        const d = await r.json();
        if (!d.success) {
            list.innerHTML = `<div class="no-data">${escapeHtml(d.error || 'Failed to load one-time links.')}</div>`;
            return;
        }
        renderOneTimeLinks(d.links || []);
    } catch (e) {
        list.innerHTML = '<div class="no-data">Failed to load one-time links.</div>';
    }
}

function renderOneTimeLinks(links) {
    const list = document.getElementById('otl-list');
    if (!links.length) {
        list.innerHTML = '<div class="no-data">No active one-time login links.</div>';
        return;
    }
    list.innerHTML = links.map(l => {
        const created = l.created_at ? new Date(l.created_at * 1000).toLocaleString() : 'Unknown';
        let expiry = 'Never';
        if (l.expires_at) {
            const exp = new Date(l.expires_at * 1000);
            expiry = exp.toLocaleString();
            if (exp.getTime() < Date.now()) expiry += ' (expired)';
        }
        return `
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
                <div>
                    <div style="font-weight:600;">🔗 ${escapeHtml(l.label || 'Login link')}</div>
                    <div style="font-size:0.75rem;color:#888;">
                        For <strong>${escapeHtml(l.username)}</strong> &middot; created ${escapeHtml(created)}
                        &middot; expires ${escapeHtml(expiry)} &middot; by ${escapeHtml(l.created_by || '—')}
                    </div>
                </div>
                <button class="btn-control red" onclick="revokeOneTimeLink('${escapeHtml(l.id)}')">Revoke</button>
            </div>`;
    }).join('');
}

async function populateUserSelect() {
    const sel = document.getElementById('otl-username');
    if (!sel || sel.dataset.populated) return;
    try {
        const r = await fetch('/api/auth/users');
        const d = await r.json();
        if (!d.users || !d.users.length) {
            sel.innerHTML = '<option value="">No users configured</option>';
            return;
        }
        sel.innerHTML = d.users.map(u =>
            `<option value="${escapeHtml(u.username)}">${escapeHtml(u.username)} (${escapeHtml(u.role)})</option>`
        ).join('');
        sel.dataset.populated = '1';
    } catch (e) {
        sel.innerHTML = '<option value="">Failed to load users</option>';
    }
}

async function generateOneTimeLink() {
    const resultEl = document.getElementById('otl-result');
    resultEl.textContent = '';
    const username = document.getElementById('otl-username').value;
    const label = document.getElementById('otl-label').value.trim();
    const ttl = document.getElementById('otl-ttl').value;

    if (!username) {
        resultEl.innerHTML = '<span style="color:#ff6b6b;">Choose a user to issue the link for.</span>';
        return;
    }

    const btn = document.querySelector('#otl-generate .btn-control');
    const oldText = btn ? btn.textContent : '';
    if (btn) { btn.textContent = 'Generating…'; btn.style.pointerEvents = 'none'; }

    try {
        const r = await fetch('/api/auth/one-time-links', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: username, label: label, ttl_hours: ttl })
        });
        const d = await r.json();
        if (!d.success) {
            resultEl.innerHTML = '<span style="color:#ff6b6b;">❌ ' + escapeHtml(d.error || 'Failed to generate link') + '</span>';
            return;
        }
        // The raw token is only returned once — show it with a copy button.
        resultEl.innerHTML = `
            <div style="background:#0a0a0c;border:1px solid #333;border-radius:6px;padding:10px;word-break:break-all;font-family:var(--font-mono);font-size:0.75rem;color:#00ff88;margin-bottom:8px;">
                ${escapeHtml(d.url)}
            </div>
            <button class="btn-control" onclick="copyOneTimeLink('${escapeHtml(d.url)}')">📋 Copy Link</button>
            <span style="font-size:0.75rem;color:#888;margin-left:8px;">Works once, then dies.</span>`;
        document.getElementById('otl-label').value = '';
        loadOneTimeLinks();
    } catch (e) {
        resultEl.innerHTML = '<span style="color:#ff6b6b;">❌ Connection error</span>';
    } finally {
        if (btn) { btn.textContent = oldText; btn.style.pointerEvents = 'auto'; }
    }
}

function copyOneTimeLink(url) {
    if (navigator.clipboard && url) {
        navigator.clipboard.writeText(url).then(() => showToast('Login link copied'));
    }
}

async function revokeOneTimeLink(id) {
    if (!confirm('Revoke this one-time login link? It can no longer be used.')) return;
    try {
        const r = await fetch('/api/auth/one-time-links/' + encodeURIComponent(id), { method: 'DELETE' });
        const d = await r.json();
        if (d.success) {
            showToast('Login link revoked');
            loadOneTimeLinks();
        } else {
            alert(d.error || 'Failed to revoke link');
        }
    } catch (e) {
        alert('Failed to revoke link');
    }
}

// Expose for the My Account nav hook
window.loadOneTimeLinks = loadOneTimeLinks;
