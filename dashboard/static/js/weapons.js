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

// ─── Mass Dismantle (Weapons) Page ─────────────────────
let _weaponsPollInterval = null;
let _lastWeaponsJson = '';

const WEAPON_STATUS_META = {
    idle:     { label: 'IDLE', color: '#64748b' },
    fetching: { label: 'FETCHING…', color: '#ffcc00' },
    done:     { label: 'READY', color: '#00ff88' },
    empty:    { label: 'NO WEAPONS', color: '#64748b' },
    error:    { label: 'ERROR', color: '#ff4d4d' }
};

function weaponsTimeAgo(ts) {
    if (!ts) return 'never';
    const diff = Math.floor(Date.now() / 1000 - ts);
    if (diff < 10) return 'just now';
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    return `${Math.floor(diff / 3600)}h ago`;
}

window.loadWeapons = function() {
    window.stopWeaponsPolling();
    fetchWeaponsStatus(true);
    _weaponsPollInterval = setInterval(() => fetchWeaponsStatus(false), 3000);
};

window.stopWeaponsPolling = function() {
    if (_weaponsPollInterval) {
        clearInterval(_weaponsPollInterval);
        _weaponsPollInterval = null;
    }
};

function fetchWeaponsStatus(showLoading) {
    const q = currentAccountId ? `?id=${currentAccountId}` : '';
    fetch(`/api/weapons/status${q}`)
        .then(r => r.json())
        .then(data => {
            if (!data.success) {
                renderWeaponsError(data.error || 'Failed to load weapons');
                return;
            }
            renderWeapons(data);
        })
        .catch(() => {
            if (showLoading) renderWeaponsError('Could not reach the weapons API.');
        });
}

function renderWeaponsError(message) {
    const list = document.getElementById('weapons-list');
    if (list) list.innerHTML = `<div class="no-data">${escapeHtml(message)}</div>`;
    const kpi = document.getElementById('weapons-kpi');
    if (kpi) kpi.innerHTML = '';
}

function renderWeapons(s) {
    const nameEl = document.getElementById('weapons-account');
    if (nameEl) nameEl.textContent = `ACCOUNT: ${s.account_name || 'Unknown'}`;

    const readyEl = document.getElementById('weapons-ready');
    if (readyEl) {
        readyEl.textContent = s.account_ready ? 'READY' : 'CONNECTING…';
        readyEl.className = 'quest-ready-badge ' + (s.account_ready ? 'on' : 'off');
    }

    const status = s.status || 'idle';
    const meta = WEAPON_STATUS_META[status] || WEAPON_STATUS_META.idle;
    const statusChip = document.getElementById('weapons-status');
    if (statusChip) {
        statusChip.textContent = meta.label;
        statusChip.style.color = meta.color;
        statusChip.style.borderColor = meta.color + '55';
        statusChip.style.background = meta.color + '18';
    }

    const weapons = s.weapons || [];

    // KPI cards
    const kpi = document.getElementById('weapons-kpi');
    if (kpi) {
        kpi.innerHTML = `
            <div class="kpi-card quest-kpi">
                <div class="quest-kpi-icon">⚔️</div>
                <div class="quest-kpi-data"><h3>Weapons</h3><p>${weapons.length}</p><span>${s.paginated ? 'paginated — re-fetch for more' : 'on this account'}</span></div>
            </div>
            <div class="kpi-card quest-kpi">
                <div class="quest-kpi-icon">🕐</div>
                <div class="quest-kpi-data"><h3>Last Fetch</h3><p style="font-size:1rem;">${weaponsTimeAgo(s.last_fetch)}</p><span>owo weapons</span></div>
            </div>
            <div class="kpi-card quest-kpi">
                <div class="quest-kpi-icon" style="color:${meta.color};">${status === 'fetching' ? '⏳' : '📋'}</div>
                <div class="quest-kpi-data"><h3>Status</h3><p style="font-size:1rem; color:${meta.color};">${meta.label}</p><span>${s.waiting ? 'awaiting response…' : '—'}</span></div>
            </div>
        `;
    }

    // Weapon cards
    const list = document.getElementById('weapons-list');
    if (list) {
        if (status === 'fetching') {
            list.innerHTML = '<div class="no-data">Fetching weapons… the selfbot is typing <code>owo weapons</code>.</div>';
        } else if (status === 'empty') {
            list.innerHTML = '<div class="no-data">No weapons in this account\u2019s inventory.</div>';
        } else if (weapons.length === 0) {
            list.innerHTML = status === 'error'
                ? `<div class="no-data">⚠️ ${escapeHtml(s.last_error || 'Could not load weapons.')} — hit <strong>FETCH WEAPONS</strong> to retry.</div>`
                : '<div class="no-data">Hit <strong>FETCH WEAPONS</strong> to load this account\u2019s weapons.</div>';
        } else {
            list.innerHTML = weapons.map((w, i) => {
                const wid = String(w.id).replace(/['"\\]/g, '');
                return `
                    <div class="weapon-card">
                        <div class="weapon-card-top">
                            <span class="weapon-index">#${i + 1}</span>
                            <span class="weapon-name">${escapeHtml(w.name || 'Unknown Weapon')}</span>
                        </div>
                        <div class="weapon-id mono">ID: ${escapeHtml(w.id)}</div>
                        <div class="weapon-actions">
                            <button class="btn-control" onclick="weaponAction('sell', '${wid}', this)" title="owo sell ${escapeHtml(w.id)}"><span class="icon-svg"
                                    style="--icon: url('/static/assets/limey_icons/coins.svg');"></span> <span class="btn-text">SELL</span></button>
                            <button class="btn-control red" onclick="weaponAction('dismantle', '${wid}', this)" title="owo dismantle ${escapeHtml(w.id)}"><span class="icon-svg"
                                    style="--icon: url('/static/assets/limey_icons/battle-net.svg');"></span> <span class="btn-text">DISMANTLE</span></button>
                        </div>
                    </div>
                `;
            }).join('');
        }
    }

    // Logs
    const logsEl = document.getElementById('weapons-logs');
    if (logsEl) {
        const logs = s.logs || [];
        const json = JSON.stringify(logs.slice(0, 40));
        if (json !== _lastWeaponsJson) {
            _lastWeaponsJson = json;
            logsEl.innerHTML = logs.length === 0
                ? '<div class="no-data">No activity yet.</div>'
                : logs.map(l => `
                    <div class="quest-log-line">
                        <span class="quest-log-time">[${escapeHtml(l.time)}]</span>
                        <span class="quest-log-level ${(l.level || 'INFO').toLowerCase()}">${escapeHtml(l.level || 'INFO')}</span>
                        <span class="quest-log-msg">${escapeHtml(l.message)}</span>
                    </div>`).join('');
        }
    }
}

// ─── Actions ────────────────────────────────────────────

window.weaponsFetch = function(el) {
    const btn = el || document.querySelector('[onclick*="weaponsFetch"]');
    if (btn) { btn.disabled = true; }
    showToast('⏳ Fetching weapons…', 'info');
    fetch('/api/weapons/fetch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: currentAccountId })
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast('Fetching weapons…', 'info');
                fetchWeaponsStatus(false);
            } else {
                showToast('❌ ' + (data.error || 'Failed to fetch weapons'), 'error');
            }
        })
        .catch(() => showToast('❌ Error fetching weapons', 'error'))
        .finally(() => { if (btn) btn.disabled = false; });
};

window.weaponAction = function(action, weaponId, el) {
    const label = action === 'sell' ? 'SELL' : 'DISMANTLE';
    const verb = action === 'sell' ? 'Sell' : 'Dismantle';
    if (action === 'dismantle') {
        if (!confirm(`💥 DISMANTLE weapon ${weaponId}?\n\nThis destroys the weapon permanently and CANNOT be undone.\n\nContinue?`)) return;
    } else {
        if (!confirm(`💰 SELL weapon ${weaponId}?\n\nContinue?`)) return;
    }
    if (el) { el.disabled = true; }
    fetch('/api/weapons/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: currentAccountId, action, weapon_id: weaponId })
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast(`✅ owo ${action} ${weaponId} sent`, 'success');
                fetchWeaponsStatus(false);
            } else {
                showToast('❌ ' + (data.error || `${verb} failed`), 'error');
                if (el) el.disabled = false;
            }
        })
        .catch(() => {
            showToast(`❌ Error sending ${verb.toLowerCase()}`, 'error');
            if (el) el.disabled = false;
        });
};

window.weaponsBulk = function(action, el) {
    if (action === 'dismantle') {
        if (!confirm('💥 DISMANTLE ALL WEAPONS?\n\nThis destroys EVERY weapon on the account and CANNOT be undone.\n\nContinue?')) return;
    } else {
        if (!confirm('💰 SELL ALL ITEMS?\n\nNote: owo sell all sells every sellable item, not just weapons.\n\nContinue?')) return;
    }
    if (el) { el.disabled = true; }
    fetch('/api/weapons/bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: currentAccountId, action })
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast(`✅ owo ${action} all sent`, 'success');
                fetchWeaponsStatus(false);
            } else {
                showToast('❌ ' + (data.error || 'Bulk action failed'), 'error');
            }
        })
        .catch(() => showToast('❌ Error sending bulk action', 'error'))
        .finally(() => { if (el) el.disabled = false; });
};
