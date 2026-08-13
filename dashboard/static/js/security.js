/* 
# This file is part of Limey.
# Copyright (c) 2025-Present Limey
#
# Limey is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with Limey. If not, see <https://www.gnu.org/licenses/>.
*/

let pendingCaptchas = {};
let pendingInterval = null;
let _manualSolvePopup = null;

async function testSecurity(btn) {
    const q = currentAccountId ? `?id=${currentAccountId}` : '';
    const original = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> TESTING...';
    btn.disabled = true;
    try {
        const res = await fetch(`/api/security/test${q}`, { method: 'POST' });
        const d = await res.json();
        if (d.status === 'success') {
            btn.style.borderColor = 'var(--success)';
            btn.innerHTML = '<i class="fa-solid fa-check"></i> SIGNALS SENT';
        } else {
            alert("Test failed: " + d.message);
            btn.innerHTML = original;
        }
    } catch (e) {
        alert("Request failed");
        btn.innerHTML = original;
    } finally {
        setTimeout(() => {
            btn.innerHTML = original;
            btn.disabled = false;
            btn.style.border = '';
        }, 3000);
    }
}

async function fetchSecuritySummary() {
    if (!document.getElementById('security').classList.contains('active-view')) return;
    const container = document.getElementById('security-accounts-grid');
    if (!container) return;
    let html = '';
    for (const acc of accountsList) {
        try {
            const res = await fetch(`/api/stats?id=${acc.id}`);
            const d = await res.json();
            if (!d || !d.security) continue;
            const isActive = acc.id === currentAccountId;
            const statusColor = d.status === "PAUSED" ? "var(--danger)" : (d.status === "OFFLINE" ? "#8b8fa3" : "var(--success)");
            html += `
                <div class="sec-account-card ${d.status === "PAUSED" ? 'alert-active' : ''} ${isActive ? 'selected' : ''}">
                    <div class="sec-acc-header">
                        <div class="sec-acc-info">
                            ${acc.avatar ? `<img src="${acc.avatar}" class="account-avatar-lg" alt="">` : '<span class="icon-svg account-avatar-lg account-avatar-fallback" style="--icon: url(\'/static/assets/limey_icons/discord.svg\');"></span>'}
                            <div class="sec-acc-text">
                                <div class="sec-acc-name">${acc.username}</div>
                                <div class="sec-acc-id">User ID · ${acc.id}</div>
                                <div class="sec-acc-status" style="color:${statusColor}">${d.status}</div>
                            </div>
                        </div>
                    </div>
                    <div class="sec-acc-stats">
                        <div class="sec-mini-stat">
                            <span class="icon-svg" style="--icon: url('/static/assets/limey_icons/check-to-slot.svg'); background-color: var(--success);"></span>
                            <div class="val">${d.security.captchas}</div>
                            <div class="lbl">Solved</div>
                        </div>
                        <div class="sec-mini-stat">
                            <span class="icon-svg" style="--icon: url('/static/assets/limey_icons/user-slash.svg'); background-color: var(--danger);"></span>
                            <div class="val">${d.security.bans}</div>
                            <div class="lbl">Bans</div>
                        </div>
                        <div class="sec-mini-stat">
                            <span class="icon-svg" style="--icon: url('/static/assets/limey_icons/warning.svg'); background-color: var(--warning);"></span>
                            <div class="val">${d.security.warnings}</div>
                            <div class="lbl">Warns</div>
                        </div>
                    </div>
                </div>
            `;
        } catch (e) {}
    }
    container.innerHTML = html || '<div class="no-data">Initializing system details...</div>';
}

let _captchaPollFailStreak = 0;
let _lastCaptchaPollFailAt = 0;

async function updatePendingCaptchas() {
    // Back off after repeated failures (server down) instead of polling every 2s
    if (_captchaPollFailStreak >= 3 && (Date.now() - _lastCaptchaPollFailAt) < 10000) return;
    try {
        const res = await fetch('/api/captcha/pending');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const ct = res.headers.get('content-type') || '';
        if (!ct.includes('application/json')) throw new Error('Non-JSON response');
        const data = await res.json();
        _captchaPollFailStreak = 0;
        const pending = data.pending || [];
        const newPending = {};
        pending.forEach(p => {
            newPending[p.account_id] = {
                accountId: p.account_id,
                accountName: p.account_name || p.account_id,
                createdAt: p.created_at
            };
        });
        Object.keys(pendingCaptchas).forEach(id => {
            if (!newPending[id]) delete pendingCaptchas[id];
        });
        Object.keys(newPending).forEach(id => {
            if (!pendingCaptchas[id]) pendingCaptchas[id] = newPending[id];
        });
        updateNotificationUI();
    } catch (e) {
        _captchaPollFailStreak++;
        _lastCaptchaPollFailAt = Date.now();
        // Keep the last known pending captchas; log only the first failure of a streak.
        if (_captchaPollFailStreak === 1 || _captchaPollFailStreak === 3) {
            console.warn('Failed to fetch pending captchas (server unreachable?) — keeping last data:', e.message || e);
        }
    }
}

function updateNotificationUI() {
    const count = Object.keys(pendingCaptchas).length;
    const bell = document.getElementById('notification-bell');
    const badge = document.getElementById('notification-badge');
    if (bell) {
        if (count > 0) {
            bell.classList.add('has-alert');
            badge.textContent = count;
            badge.style.display = 'block';
        } else {
            bell.classList.remove('has-alert');
            badge.style.display = 'none';
        }
    }
    renderPendingDropdown();
    renderSecurityCards();
}

function renderPendingDropdown() {
    const dropdown = document.getElementById('notification-dropdown');
    if (!dropdown) return;
    const count = Object.keys(pendingCaptchas).length;
    if (count === 0) {
        dropdown.innerHTML = '<div class="no-data">No pending captchas</div>';
        return;
    }
    let html = '';
    const now = Date.now() / 1000;
    Object.values(pendingCaptchas).forEach(p => {
        const elapsed = now - p.createdAt;
        const remaining = Math.max(0, 600 - elapsed);
        const urgencyClass = getUrgencyClass(remaining);
        const timeStr = formatTime(remaining);
        html += `
            <div class="pending-item ${urgencyClass}">
                <span class="pending-name">${p.accountName}</span>
                <span class="pending-timer">${timeStr}</span>
                <button class="btn-proxy-sm solve-btn" onclick="triggerManualSolve('${p.accountId}')">Solve</button>
            </div>
        `;
    });
    dropdown.innerHTML = html;
}

function renderSecurityCards() {
    const container = document.getElementById('captcha-cards-container');
    if (!container) return;
    const count = Object.keys(pendingCaptchas).length;
    if (count === 0) {
        container.innerHTML = '<div class="no-data">No pending captchas</div>';
        return;
    }
    let html = '';
    const now = Date.now() / 1000;
    Object.values(pendingCaptchas).forEach(p => {
        const elapsed = now - p.createdAt;
        const remaining = Math.max(0, 600 - elapsed);
        const urgencyClass = getUrgencyClass(remaining);
        const timeStr = formatTime(remaining);
        html += `
            <div class="captcha-card ${urgencyClass}">
                <div class="captcha-card-header">
                    <span class="captcha-account">${p.accountName}</span>
                    <span class="captcha-timer">${timeStr}</span>
                </div>
                <div class="captcha-card-body">
                    <button class="btn-control gold" onclick="solveFromCard('${p.accountId}')">Solve</button>
                    <button class="btn-control" onclick="dismissCaptchaCard('${p.accountId}')">Dismiss</button>
                </div>
            </div>
        `;
    });
    container.innerHTML = html;
}

function dismissCaptchaCard(accountId) {
    if (pendingCaptchas[accountId]) {
        delete pendingCaptchas[accountId];
        updateNotificationUI();
    }
}

function getUrgencyClass(seconds) {
    if (seconds > 300) return 'urgency-green';
    if (seconds > 120) return 'urgency-yellow';
    if (seconds > 60) return 'urgency-orange';
    if (seconds > 30) return 'urgency-red';
    return 'urgency-critical';
}

function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}m ${secs}s`;
}

window.triggerManualSolve = async function(accountId) {
    try {
        const res = await fetch('/api/captcha/oauth_url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account_id: accountId })
        });
        const data = await res.json();
        if (!data.success || !data.url) {
            showToast('Failed to get captcha URL', 'error');
            return;
        }
        const popup = window.open(data.url, '_blank', 'width=420,height=600,resizable=yes,scrollbars=yes');
        if (!popup) {
            window.open(data.url, '_blank');
        } else {
            _manualSolvePopup = popup;
        }
        showToast('Captcha page opened in new window', 'info');
    } catch (e) {
        showToast('Error opening captcha', 'error');
    }
};

window.pollForCaptchas = async function() {
    await updatePendingCaptchas();
};

function startPendingTimer() {
    if (pendingInterval) clearInterval(pendingInterval);
    pendingInterval = setInterval(() => {
        if (Object.keys(pendingCaptchas).length > 0) {
            renderPendingDropdown();
            renderSecurityCards();
        }
    }, 1000);
}

window.toggleNotificationDropdown = function() {
    const dropdown = document.getElementById('notification-dropdown');
    if (!dropdown) return;
    if (dropdown.style.display === 'block') {
        dropdown.style.display = 'none';
    } else {
        dropdown.style.display = 'block';
        document.addEventListener('click', function closeDropdown(e) {
            const bell = document.getElementById('notification-bell');
            if (bell && !bell.contains(e.target) && !dropdown.contains(e.target)) {
                dropdown.style.display = 'none';
                document.removeEventListener('click', closeDropdown);
            }
        });
    }
};

window.cancelManualSolve = function() {
    if (_manualSolvePopup) {
        try { _manualSolvePopup.close(); } catch (e) {}
        _manualSolvePopup = null;
    }
};

// ── Embedded hCaptcha ───────────────────────────────────
// Renders a real hCaptcha widget inline in the dashboard and submits
// the token to the backend, which verifies it against owobot.com.
const HCAPTCHA_SITEKEY = 'a6a1d5ce-612d-472d-8e37-7601408fbc09';

let _embeddedCaptchaPending = null;      // { accountId, username }
let _embeddedCaptchaWidgetId = null;     // rendered hCaptcha widget id
let _embeddedCaptchaRenderedFor = null;  // accountId currently rendered
let _hcaptchaWaiters = [];

window.hcaptchaOnload = function() {
    _hcaptchaWaiters.forEach(cb => { try { cb(); } catch (e) {} });
    _hcaptchaWaiters = [];
    maybeRenderEmbeddedCaptcha();
};

function whenHcaptchaReady(cb) {
    if (typeof window.hcaptcha !== 'undefined') { cb(); return; }
    _hcaptchaWaiters.push(cb);
    let tries = 0;
    const poll = setInterval(() => {
        tries++;
        if (typeof window.hcaptcha !== 'undefined') {
            clearInterval(poll);
            cb();
        } else if (tries > 50) {
            clearInterval(poll);
            const idx = _hcaptchaWaiters.indexOf(cb);
            if (idx !== -1) _hcaptchaWaiters.splice(idx, 1);
            showToast('hCaptcha failed to load – check your connection', 'error');
        }
    }, 200);
}

window.openEmbeddedCaptcha = function(accountId, username) {
    const section = document.getElementById('captcha-solver-section');
    if (!section) return;
    _embeddedCaptchaPending = { accountId: accountId, username: username || accountId };
    section.style.display = 'block';
    maybeRenderEmbeddedCaptcha();
};

// User-initiated solve (e.g. from a pending captcha card) – scrolls to the widget.
window.solveFromCard = function(accountId) {
    openEmbeddedCaptcha(accountId);
    setTimeout(() => {
        const section = document.getElementById('captcha-solver-section');
        if (section) {
            try { section.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) {}
        }
    }, 100);
};

window.maybeRenderEmbeddedCaptcha = function() {
    if (!_embeddedCaptchaPending) return;
    const secView = document.getElementById('security');
    if (!secView || !secView.classList.contains('active-view')) return; // wait until visible
    const container = document.getElementById('hcaptcha-container');
    if (!container) return;
    whenHcaptchaReady(() => {
        if (!_embeddedCaptchaPending) return;
        const { accountId } = _embeddedCaptchaPending;
        // Already showing a live widget for this account – keep it
        if (_embeddedCaptchaRenderedFor === accountId && _embeddedCaptchaWidgetId !== null) return;
        resetEmbeddedCaptchaWidget();
        container.innerHTML = '';
        try {
            _embeddedCaptchaWidgetId = hcaptcha.render(container, {
                sitekey: HCAPTCHA_SITEKEY,
                theme: 'dark',
                size: 'normal',
                callback: (token) => submitEmbeddedCaptcha(accountId, token),
                'expired-callback': () => showToast('Captcha expired – please solve it again', 'warning'),
                'error-callback': () => showToast('Captcha error – click Reload to try again', 'error'),
            });
            _embeddedCaptchaRenderedFor = accountId;
        } catch (e) {
            console.error('hCaptcha render failed:', e);
            container.innerHTML = '<div class="no-data">Failed to load captcha – <a href="javascript:void(0)" onclick="reloadEmbeddedCaptcha()">click to retry</a></div>';
            _embeddedCaptchaWidgetId = null;
        }
    });
};

window.submitEmbeddedCaptcha = async function(accountId, token) {
    try {
        const res = await fetch('/api/captcha_solve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account_id: accountId, token: token })
        });
        const d = await res.json();
        if (d.success) {
            showToast('Captcha verified – bot resumed!', 'success');
            closeEmbeddedCaptcha();
            pollForCaptchas();
        } else {
            showToast(d.error || 'Invalid captcha – please try again', 'error');
            resetEmbeddedCaptchaWidget();
        }
    } catch (e) {
        showToast('Failed to submit captcha', 'error');
        resetEmbeddedCaptchaWidget();
    }
};

function resetEmbeddedCaptchaWidget() {
    if (_embeddedCaptchaWidgetId !== null && typeof hcaptcha !== 'undefined') {
        try { hcaptcha.reset(_embeddedCaptchaWidgetId); } catch (e) {}
    }
    _embeddedCaptchaWidgetId = null;
    _embeddedCaptchaRenderedFor = null;
    const container = document.getElementById('hcaptcha-container');
    if (container) container.innerHTML = '<div class="no-data">Loading hCaptcha…</div>';
}

function closeEmbeddedCaptcha() {
    _embeddedCaptchaPending = null;
    const section = document.getElementById('captcha-solver-section');
    if (section) section.style.display = 'none';
    resetEmbeddedCaptchaWidget();
}

window.cancelEmbeddedCaptcha = function() {
    closeEmbeddedCaptcha();
    cancelManualSolve();
};

window.reloadEmbeddedCaptcha = function() {
    if (!_embeddedCaptchaPending) return;
    resetEmbeddedCaptchaWidget();
    maybeRenderEmbeddedCaptcha();
};

startPendingTimer();