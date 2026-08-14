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

let _accountsFailStreak = 0;
let _lastAccountsFailAt = 0;

// Server-binding state for the account form (scan -> pick -> bind)
let accountGuilds = [];

async function populateAccountWorkerDropdown(selectedId = '') {
    const sel = document.getElementById('acct-form-worker');
    if (!sel) return;
    sel.innerHTML = '<option value="">Run on main server</option>';
    try {
        const response = await fetch('/api/workers');
        const data = await response.json();
        if (!response.ok || !data.success) return;
        const workers = (data.workers || []).filter(w => !w.revoked);
        sel.innerHTML += workers.map(w =>
            `<option value="${accountEsc(w.id)}">${accountEsc(w.name)}${w.online ? ' (online)' : ' (offline)'}</option>`
        ).join('');
        sel.value = selectedId || '';
    } catch (error) {
        // Worker linking is optional; keep the main-server option available.
    }
}

function accountEsc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

function getAccountFormToken() {
    const index = parseInt(document.getElementById('acct-form-index').value, 10);
    let token = document.getElementById('acct-form-token').value.trim();
    if (!token && index >= 0 && accountConfigList[index]) {
        token = accountConfigList[index].token || '';
    }
    return token;
}

function setGuildStatus(text, cls) {
    const el = document.getElementById('acct-guild-status');
    if (!el) return;
    el.textContent = text;
    el.className = 'acct-guild-status' + (cls ? ' ' + cls : '');
}

window.scanAccountGuilds = async function() {
    const token = getAccountFormToken();
    if (!token) {
        setGuildStatus('Enter (or keep) a token first, then scan.', 'err');
        showToast('Token required to scan servers', 'error');
        return;
    }
    const proxy_id = document.getElementById('acct-form-proxy').value || null;
    setGuildStatus('Scanning your servers…', '');
    try {
        const res = await fetch('/api/accounts/scan-guilds', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token, proxy_id }),
        });
        const data = await res.json();
        if (!data.success) {
            setGuildStatus(data.error || 'Scan failed', 'err');
            showToast(data.error || 'Scan failed', 'error');
            return;
        }
        accountGuilds = data.guilds || [];
        const sel = document.getElementById('acct-form-guild');
        sel.innerHTML = '<option value="">All servers (no binding)</option>' +
            accountGuilds.map(g =>
                `<option value="${accountEsc(g.id)}">${accountEsc(g.name)} (${accountEsc(g.id)})</option>`
            ).join('');
        // Keep the account's existing binding selected after a rescan
        const accIndex = parseInt(document.getElementById('acct-form-index').value, 10);
        const curAcc = (accIndex >= 0 && accountConfigList[accIndex]) ? accountConfigList[accIndex] : null;
        if (curAcc && curAcc.guild_id) {
            sel.value = String(curAcc.guild_id);
        }
        setGuildStatus(accountGuilds.length
            ? `Found ${accountGuilds.length} server${accountGuilds.length === 1 ? '' : 's'} — pick one, or leave as All servers.`
            : 'No servers found for this token.', 'ok');
    } catch (e) {
        setGuildStatus('Scan failed — is the dashboard reachable?', 'err');
    }
};

window.loadGuildChannels = async function() {
    const sel = document.getElementById('acct-form-guild');
    const guild_id = sel ? sel.value : '';
    if (!guild_id) {
        setGuildStatus('Pick a server first (Scan Servers → choose one).', 'err');
        return;
    }
    const token = getAccountFormToken();
    if (!token) {
        setGuildStatus('Token required to load channels.', 'err');
        return;
    }
    const channelsInput = document.getElementById('acct-form-channels');
    if (channelsInput.value.trim() &&
        !confirm('Replace the current channel IDs with this server\'s text channels?')) {
        return;
    }
    const proxy_id = document.getElementById('acct-form-proxy').value || null;
    setGuildStatus('Loading channels…', '');
    try {
        const res = await fetch('/api/accounts/guild-channels', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token, guild_id, proxy_id }),
        });
        const data = await res.json();
        if (!data.success) {
            setGuildStatus(data.error || 'Failed to load channels', 'err');
            return;
        }
        channelsInput.value = (data.channels || []).map(c => c.id).join(' ');
        setGuildStatus(`Loaded ${(data.channels || []).length} text channels — trim if needed.`, 'ok');
    } catch (e) {
        setGuildStatus('Failed to load channels.', 'err');
    }
};

function populateGuildSelect(index) {
    const sel = document.getElementById('acct-form-guild');
    if (!sel) return;
    const acc = (index >= 0 && accountConfigList[index]) ? accountConfigList[index] : null;
    const boundId = acc && acc.guild_id ? String(acc.guild_id) : '';
    if (accountGuilds.length) {
        const inList = boundId && accountGuilds.some(g => String(g.id) === boundId);
        sel.innerHTML = '<option value="">All servers (no binding)</option>' +
            accountGuilds.map(g =>
                `<option value="${accountEsc(g.id)}">${accountEsc(g.name)} (${accountEsc(g.id)})</option>`
            ).join('') +
            // Never lose the current binding to a stale cache: if the account's
            // guild isn't in the last scan, keep it as a selected option.
            (boundId && !inList
                ? `<option value="${accountEsc(boundId)}" selected>${accountEsc(acc.guild_name || boundId)}</option>`
                : '');
        sel.value = boundId;
        if (boundId) {
            setGuildStatus(`Bound to server: ${accountEsc(acc.guild_name || boundId)}`, 'ok');
        } else {
            setGuildStatus('Not bound — click Scan Servers to choose a server.', '');
        }
    } else if (boundId) {
        sel.innerHTML =
            `<option value="${accountEsc(boundId)}" selected>${accountEsc(acc.guild_name || boundId)}</option>` +
            '<option value="">All servers (no binding)</option>';
        setGuildStatus(`Bound to server: ${accountEsc(acc.guild_name || boundId)} — click Scan Servers to change.`, 'ok');
    } else {
        sel.innerHTML = '<option value="">All servers (no binding)</option>';
        setGuildStatus('Not bound — click Scan Servers to pick a server.', '');
    }
}

window.fetchAccounts = async function() {
    // Back off after repeated failures (server down) instead of hammering it every 5s
    if (_accountsFailStreak >= 3 && (Date.now() - _lastAccountsFailAt) < 15000) return;
    try {
        const res = await fetch('/api/accounts/list');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const ct = res.headers.get('content-type') || '';
        if (!ct.includes('application/json')) throw new Error('Non-JSON response');
        const data = await res.json();
        _accountsFailStreak = 0;
        accountsList = data;
        if (data.length > 0) {
            if (!currentAccountId || !data.find(a => a.id === currentAccountId)) {
                currentAccountId = data[0].id;
            }
        }
        renderAccountGrid();
        updateGlobalAccountName(); 
    } catch (e) {
        _accountsFailStreak++;
        _lastAccountsFailAt = Date.now();
        if (_accountsFailStreak === 1 || _accountsFailStreak === 3) {
            console.warn('Failed to fetch accounts (server unreachable?) — keeping last list:', e.message || e);
        }
        // Only show an error when there's nothing on screen already; don't wipe
        // a previously-loaded grid because of a transient outage.
        const grid = document.getElementById('accounts-grid');
        if (grid && (!accountsList || accountsList.length === 0)) {
            grid.innerHTML = `<div class="no-data error">Server unreachable — retrying automatically…</div>`;
        }
    }
};

function updateGlobalAccountName() {
    const nameEl = document.getElementById('currentAccountName');
    if (!nameEl) return;
    if (currentAccountId) {
        const acc = accountsList.find(a => a.id === currentAccountId);
        if (acc) {
            nameEl.innerText = `ACCOUNT: ${acc.username}`;
            return;
        }
    }
    nameEl.innerText = 'Loading account...';
}

window.selectAccount = function(id) {
    console.log(`Selecting account: ${id}`);
    currentAccountId = id;
    renderAccountGrid();
    const acc = accountsList.find(a => a.id === id);
    if (acc) {
        showToast(`Switched to account: ${acc.username}`, 'success');
        updateGlobalAccountName(); 
    }
    if (lineChart) lineChart.data.datasets[0].data = Array(30).fill(0);
    const configView = document.getElementById('config');
    if (configView && configView.classList.contains('active-view')) loadConfig();
    update();
    const dashNav = document.querySelector('.nav-item[onclick*="dash"]');
    if (dashNav) nav('dash', dashNav);
};

function renderAccountGrid() {
    const grid = document.getElementById('accounts-grid');
    if (!grid) return;
    if (!accountsList || !accountsList.length) {
        grid.innerHTML = '<div class="no-data">No accounts online. Start the bot to see connected accounts here.</div>';
        return;
    }
    grid.innerHTML = accountsList.map(acc => {
        const isSelected = acc.id === currentAccountId;
        const statusClass = acc.offline ? 'offline' : (acc.paused ? 'paused' : 'running');
        const statusLabel = acc.offline ? 'Offline' : (acc.paused ? 'Paused' : 'Running');
        const avatar = acc.avatar
            ? `<img src="${acc.avatar}" class="account-avatar-lg" alt="">`
            : `<span class="icon-svg account-avatar-lg account-avatar-fallback" style="--icon: url('/static/assets/limey_icons/discord.svg');"></span>`;
        return `
            <div class="account-picker-card ${isSelected ? 'selected' : ''}" onclick="selectAccount('${acc.id}')" role="button" tabindex="0">
                <div class="account-card-top">
                    ${avatar}
                    <div class="account-card-meta">
                        <div class="account-card-name">${acc.username}</div>
                        <div class="account-card-id">User ID · ${acc.id}</div>
                        <div class="account-card-status ${statusClass}">${statusLabel}</div>
                    </div>
                    ${isSelected ? '<span class="account-selected-badge">Selected</span>' : ''}
                </div>
                <div class="account-card-stats">
                    <div class="account-stat">
                        <span class="icon-svg" style="--icon: url('/static/assets/limey_icons/coins.svg');"></span>
                        <div class="account-stat-val">${(acc.cash || 0).toLocaleString()}</div>
                        <div class="account-stat-lbl">Balance</div>
                    </div>
                    <div class="account-stat">
                        <span class="icon-svg" style="--icon: url('/static/assets/limey_icons/terminal.svg');"></span>
                        <div class="account-stat-val">${acc.session_total || 0}</div>
                        <div class="account-stat-lbl">Session Cmds</div>
                    </div>
                    <div class="account-stat">
                        <span class="icon-svg" style="--icon: url('/static/assets/limey_icons/bolt.svg');"></span>
                        <div class="account-stat-val">${acc.gems_used || 0}</div>
                        <div class="account-stat-lbl">Gems Used</div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

window.fetchAccountConfig = async function() {
    try {
        const res = await fetch('/api/accounts/config');
        const data = await res.json();
        accountConfigList = data.accounts || [];
        renderAccountConfigList();
    } catch (e) {
        console.error('Failed to fetch account config', e);
    }
};

function renderAccountConfigList() {
    const el = document.getElementById('account-config-list');
    if (!el) return;
    if (!accountConfigList.length) {
        el.innerHTML = '<div class="no-data">No accounts configured. Click Add Account.</div>';
        return;
    }
    el.innerHTML = accountConfigList.map((acc, i) => {
        const token = acc.token_masked || '••••••';
        const proxy = acc.proxy_id ? `Proxy: ${acc.proxy_id}` : 'Direct';
        const status = acc.enabled !== false ? 'Enabled' : 'Disabled';
        const server = acc.guild_name ? `Server: ${accountEsc(acc.guild_name)}` : 'All servers';
        const worker = acc.worker_id ? `Worker: ${accountEsc(acc.worker_id)}` : 'Main server';
        return `
            <div class="account-config-card">
                <div class="account-config-info">
                    <strong>${acc.name || 'Unnamed'}</strong>
                    <span class="mono">${token}</span>
                    <span class="dim">${proxy} · ${status} · ${server} · ${worker}</span>
                </div>
                <div class="account-config-actions">
                    <button class="btn-proxy-sm" onclick="editAccountConfig(${i})">Edit</button>
                    <button class="btn-proxy-sm danger" onclick="deleteAccountConfig(${i})">Del</button>
                </div>
            </div>
        `;
    }).join('');
}

window.showAccountForm = function(index = -1) {
    const modal = document.getElementById('account-form-modal');
    const title = document.getElementById('acct-form-title');
    document.getElementById('acct-form-index').value = index;
    if (index >= 0 && accountConfigList[index]) {
        const acc = accountConfigList[index];
        title.textContent = 'Edit Account';
        document.getElementById('acct-form-name').value = acc.name || '';
        document.getElementById('acct-form-token').value = '';
        document.getElementById('acct-form-token').placeholder = acc.token_masked ? 'Leave blank to keep current token' : 'Discord user token';
        document.getElementById('acct-form-channels').value = (acc.channels || []).join(' ');
        document.getElementById('acct-form-enabled').checked = acc.enabled !== false;
        if (typeof populateAccountProxyDropdown === 'function') populateAccountProxyDropdown();
        document.getElementById('acct-form-proxy').value = acc.proxy_id || '';
        populateAccountWorkerDropdown(acc.worker_id || '');
        populateGuildSelect(index);
    } else {
        title.textContent = 'Add Account';
        document.getElementById('acct-form-name').value = '';
        document.getElementById('acct-form-token').value = '';
        document.getElementById('acct-form-token').placeholder = 'Discord user token';
        document.getElementById('acct-form-channels').value = '';
        document.getElementById('acct-form-enabled').checked = true;
        if (typeof populateAccountProxyDropdown === 'function') populateAccountProxyDropdown();
        document.getElementById('acct-form-proxy').value = '';
        populateAccountWorkerDropdown('');
        populateGuildSelect(-1);
    }
    if (modal) modal.classList.add('visible');
};

window.hideAccountForm = function() {
    const modal = document.getElementById('account-form-modal');
    if (modal) modal.classList.remove('visible');
};

window.editAccountConfig = function(index) {
    showAccountForm(index);
};

window.deleteAccountConfig = async function(index) {
    if (!confirm('Remove this account from config?')) return;
    accountConfigList.splice(index, 1);
    await saveAccountConfigList();
};

window.saveAccountForm = async function() {
    const index = parseInt(document.getElementById('acct-form-index').value, 10);
    const name = document.getElementById('acct-form-name').value.trim();
    const token = document.getElementById('acct-form-token').value.trim();
    const channels = document.getElementById('acct-form-channels').value.trim().split(/\s+/).filter(Boolean);
    const proxy_id = document.getElementById('acct-form-proxy').value || null;
    const enabled = document.getElementById('acct-form-enabled').checked;
    const workerSel = document.getElementById('acct-form-worker');
    const worker_id = workerSel ? workerSel.value : '';
    const guildSel = document.getElementById('acct-form-guild');
    const guild_id = guildSel ? guildSel.value : '';
    let guild_name = '';
    if (guildSel && guild_id) {
        const opt = guildSel.options[guildSel.selectedIndex];
        guild_name = opt ? opt.text.replace(/\s*\(\d+\)$/, '') : '';
    }
    if (!name) {
        showToast('Account name is required', 'error');
        return;
    }
    const entry = { name, channels, enabled, proxy_id, worker_id, guild_id, guild_name };
    if (index >= 0 && accountConfigList[index]) {
        entry.token = accountConfigList[index].token;
        if (token) entry.token = token;
        if (!entry.token) {
            showToast('Token is required for new accounts', 'error');
            return;
        }
        // Merge over the existing entry so fields we don't edit (id, user_id,
        // presence/stop-state, …) aren't silently dropped on save.
        accountConfigList[index] = Object.assign({}, accountConfigList[index], entry);
    } else {
        if (!token) {
            showToast('Token is required', 'error');
            return;
        }
        entry.token = token;
        accountConfigList.push(entry);
    }
    await saveAccountConfigList();
    hideAccountForm();
};

async function saveAccountConfigList() {
    try {
        const res = await fetch('/api/accounts/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accounts: accountConfigList }),
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast('Accounts saved — restart bot to apply', 'success');
            await fetchAccountConfig();
        } else {
            showToast(data.message || 'Save failed', 'error');
        }
    } catch (e) {
        showToast('Save failed', 'error');
    }
}