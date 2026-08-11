/* 
 * Chat Archives page — scan a selfbot's servers + DMs, then create a
 * downloadable JSON + HTML archive once the owner confirms.
 */

let _archivePollTimer = null;
let _archiveAccountId = null;

function loadArchivePage() {
    loadArchiveAccounts();
    loadArchiveList();
}

// ─── Accounts & controls ──────────────────────────────

async function loadArchiveAccounts() {
    const sel = document.getElementById('arch-account');
    const btn = document.getElementById('arch-scan-btn');
    try {
        const r = await fetch('/api/archive/accounts');
        const data = await r.json();
        const accounts = data.success ? data.accounts : [];
        if (accounts.length) {
            sel.innerHTML = accounts.map(a =>
                `<option value="${escapeHtml(a.id)}">${escapeHtml(a.username)}</option>`).join('');
            sel.disabled = false;
            btn.disabled = false;
            _archiveAccountId = accounts[0].id;
        } else {
            sel.innerHTML = '<option value="">No accounts online</option>';
            sel.disabled = true;
            btn.disabled = true;
        }
    } catch (e) {
        sel.innerHTML = '<option value="">Failed to load accounts</option>';
        sel.disabled = true;
        btn.disabled = true;
    }
}

function archiveScanOptions() {
    return {
        user_id: document.getElementById('arch-account').value,
        message_limit: document.getElementById('arch-limit').value,
        include_guilds: document.getElementById('arch-guilds').checked,
        include_dms: document.getElementById('arch-dms').checked
    };
}

// ─── Scan flow ────────────────────────────────────────

async function startArchiveScan() {
    const opts = archiveScanOptions();
    if (!opts.user_id) {
        showToast('No account selected', 'error');
        return;
    }
    const btn = document.getElementById('arch-scan-btn');
    btn.disabled = true;
    btn.textContent = 'Starting…';
    try {
        const r = await fetch('/api/archive/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(opts)
        });
        const data = await r.json();
        if (!data.success) {
            showToast(data.error || 'Failed to start scan', 'error');
            btn.disabled = false;
            btn.textContent = '▶ Start Scan';
            return;
        }
        _archiveAccountId = opts.user_id;
        document.getElementById('arch-result-panel').style.display = 'none';
        document.getElementById('arch-progress-panel').style.display = '';
        pollArchiveScan();
    } catch (e) {
        showToast('Failed to start scan', 'error');
        btn.disabled = false;
        btn.textContent = '▶ Start Scan';
    }
}

function pollArchiveScan() {
    clearTimeout(_archivePollTimer);
    if (!_archiveAccountId) return;
    fetch('/api/archive/status?user_id=' + encodeURIComponent(_archiveAccountId))
        .then(r => r.json())
        .then(data => {
            const scan = data.scan;
            if (!scan) {
                _archivePollTimer = setTimeout(pollArchiveScan, 2000);
                return;
            }
            renderArchiveProgress(scan);
            if (scan.status === 'scanning') {
                _archivePollTimer = setTimeout(pollArchiveScan, 1500);
            } else {
                renderArchiveResult(scan);
            }
        })
        .catch(() => {
            _archivePollTimer = setTimeout(pollArchiveScan, 2500);
        });
}

function renderArchiveProgress(scan) {
    const total = scan.channels_total || 0;
    const done = scan.channels_done || 0;
    const pct = total ? Math.round((done / total) * 100) : (scan.status === 'scanning' ? 5 : 100);
    document.getElementById('arch-progress-fill').style.width = pct + '%';
    document.getElementById('arch-progress-text').innerHTML =
        `Servers ${scan.guilds_done}/${scan.guilds_total} · DMs ${scan.dms_done}/${scan.dms_total} · ` +
        `Channels ${done}/${total} · Messages ${(scan.messages_total || 0).toLocaleString()}`;
}

function renderArchiveResult(scan) {
    document.getElementById('arch-progress-panel').style.display = 'none';
    const panel = document.getElementById('arch-result-panel');
    panel.style.display = '';
    const errEl = document.getElementById('arch-result-error');
    const statsEl = document.getElementById('arch-result-stats');
    if (scan.status === 'error') {
        errEl.style.display = '';
        errEl.textContent = '❌ ' + (scan.error || 'Scan failed.');
        statsEl.innerHTML = '';
        document.getElementById('arch-search-block').style.display = 'none';
        clearArchiveSearch();
        return;
    }
    errEl.style.display = 'none';
    document.getElementById('arch-search-block').style.display = '';
    clearArchiveSearch();
    statsEl.innerHTML =
        `<div class="proxy-stat-card"><span>${scan.guilds_total}</span><label>Servers</label></div>` +
        `<div class="proxy-stat-card"><span>${scan.dms_total}</span><label>DMs</label></div>` +
        `<div class="proxy-stat-card ok"><span>${(scan.messages_total || 0).toLocaleString()}</span><label>Messages</label></div>`;
    document.getElementById('arch-scan-btn').disabled = false;
    document.getElementById('arch-scan-btn').textContent = '▶ Start Scan';
}

function resetArchiveScan() {
    clearTimeout(_archivePollTimer);
    clearArchiveSearch();
    document.getElementById('arch-progress-panel').style.display = 'none';
    document.getElementById('arch-result-panel').style.display = 'none';
    const btn = document.getElementById('arch-scan-btn');
    btn.disabled = false;
    btn.textContent = '▶ Start Scan';
}

// ─── Create (owner confirms) ──────────────────────────

async function createArchiveNow() {
    if (!_archiveAccountId) return;
    if (!confirm('Create the archive now? All scanned messages will be pushed to the GitHub data repo (zip + index.json) and only you can download them.')) {
        return;
    }
    try {
        const r = await fetch('/api/archive/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: _archiveAccountId })
        });
        const data = await r.json();
        if (!data.success) {
            showToast(data.error || 'Failed to create archive', 'error');
            return;
        }
        if (data.archive && data.archive.push_error) {
            showToast('Archive created locally — GitHub push failed: ' + data.archive.push_error, 'error');
        } else {
            showToast('Archive created and pushed to GitHub!');
        }
        resetArchiveScan();
        loadArchiveList();
    } catch (e) {
        showToast('Failed to create archive', 'error');
    }
}

// ─── Archive list ─────────────────────────────────────

function formatBytes(bytes) {
    if (!bytes && bytes !== 0) return '?';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(2) + ' MB';
}

async function loadArchiveList() {
    const list = document.getElementById('arch-archive-list');
    try {
        const r = await fetch('/api/archive/list');
        const data = await r.json();
        const archives = data.success ? data.archives : [];
        renderArchiveStats(archives);
        const purgeBtn = document.getElementById('arch-purge-btn');
        if (purgeBtn) purgeBtn.style.display = archives.length ? '' : 'none';
        if (!archives.length) {
            closeArchiveBrowser();
            clearArchiveSearchAll();
            list.innerHTML = `<div class="arch-empty">
                <div class="arch-empty-icon">🗄️</div>
                <div>No archives yet — your chat history starts here.</div>
                <div class="arch-empty-steps">
                    <div class="arch-empty-step"><b>1 · SCAN</b><span>Pick an online account, choose a depth, and scan its servers + DMs. Read-only — nothing is sent or stored.</span></div>
                    <div class="arch-empty-step"><b>2 · REVIEW</b><span>Search the scanned messages right here to check what would be archived before committing.</span></div>
                    <div class="arch-empty-step"><b>3 · CREATE</b><span>Confirm to build a zip (JSON + readable HTML) and push it to the GitHub data repo.</span></div>
                </div>
            </div>`;
            return;
        }
        list.innerHTML = archives.map(a => {
            const storage = a.stored_in === 'github'
                ? '<span class="arch-badge github">☁️ GitHub</span>'
                : '<span class="arch-badge local">💾 Local fallback</span>';
            return `
            <div class="arch-item">
                <div style="min-width:0;flex:1;">
                    <div class="arch-item-name">📦 ${escapeHtml(a.username)} — ${escapeHtml(a.created_at || '')} ${storage}</div>
                    <div class="arch-item-meta">
                        ${a.guild_count} servers · ${a.dm_count} DMs · ${(a.message_count || 0).toLocaleString()} messages · ${formatBytes(a.size_bytes)}
                        ${a.push_error ? '<div style="color:#ff6b6b;font-size:0.75rem;margin-top:3px;">⚠️ ' + escapeHtml(a.push_error) + '</div>' : ''}
                        ${a.rename_warning ? '<div style="color:#ffb454;font-size:0.75rem;margin-top:3px;">⚠️ ' + escapeHtml(a.rename_warning) + '</div>' : ''}
                    </div>
                </div>
                <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end;">
                    <div class="arch-actions">
                        <button class="btn-control arch-browse" onclick="openArchiveBrowser('${escapeHtml(a.name)}')">📖 Browse</button>
                        <a class="btn-control green" href="${escapeHtml(a.download)}" style="text-decoration:none;">⬇ Download</a>
                        <button class="arch-act red" onclick="deleteArchiveItem('${escapeHtml(a.name)}')" title="Delete archive">🗑️</button>
                    </div>
                    <div class="arch-actions">
                        <a class="arch-act" href="/api/archive/download-json/${encodeURIComponent(a.name)}" title="Download index.json (all message data)">JSON</a>
                        <a class="arch-act" href="/api/archive/download-html/${encodeURIComponent(a.name)}" title="Download readable HTML index">HTML</a>
                        <button class="arch-act" onclick="renameArchiveItem('${escapeHtml(a.name)}')" title="Rename archive">✏️ Rename</button>
                        <button class="arch-act" onclick="showArchiveDetails('${escapeHtml(a.name)}')" title="View archive details">ℹ️ Details</button>
                    </div>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        list.innerHTML = '<div class="no-data">Failed to load archives.</div>';
    }
}

function renderArchiveStats(archives) {
    const bar = document.getElementById('arch-stat-bar');
    if (!bar) return;
    if (!archives.length) {
        bar.innerHTML = '';
        return;
    }
    const totalMsgs = archives.reduce((s, a) => s + (a.message_count || 0), 0);
    const totalBytes = archives.reduce((s, a) => s + (a.size_bytes || 0), 0);
    const githubCount = archives.filter(a => a.stored_in === 'github').length;
    const localCount = archives.length - githubCount;
    bar.innerHTML =
        `<div class="proxy-stat-card"><span>${archives.length}</span><label>Archives</label></div>` +
        `<div class="proxy-stat-card ok"><span>${totalMsgs.toLocaleString()}</span><label>Messages</label></div>` +
        `<div class="proxy-stat-card"><span>${formatBytes(totalBytes)}</span><label>Total size</label></div>` +
        `<div class="proxy-stat-card"><span>${githubCount}</span><label>☁️ GitHub</label></div>` +
        `<div class="proxy-stat-card"><span>${localCount}</span><label>💾 Local</label></div>`;
}

async function deleteArchiveItem(name) {
    if (!confirm('Delete this archive permanently?')) return;
    try {
        const r = await fetch('/api/archive/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        });
        const data = await r.json();
        if (data.success) {
            showToast('Archive deleted');
            if (_archiveBrowserName === name) closeArchiveBrowser();
            loadArchiveList();
        } else {
            showToast(data.error || 'Failed to delete', 'error');
        }
    } catch (e) {
        showToast('Failed to delete', 'error');
    }
}

// ─── Archive actions: rename / details / purge ────────

let _archiveRenameName = null;

function renameArchiveItem(name) {
    _archiveRenameName = name;
    const modal = document.getElementById('arch-rename-modal');
    const input = document.getElementById('arch-rename-input');
    if (!modal || !input) return;
    input.value = name;
    modal.style.display = 'flex';
    input.focus();
    input.select();
}

function closeRenameArchiveModal() {
    document.getElementById('arch-rename-modal').style.display = 'none';
    _archiveRenameName = null;
}

async function confirmRenameArchive() {
    if (!_archiveRenameName) return;
    const input = document.getElementById('arch-rename-input');
    const newName = (input.value || '').trim();
    if (!newName) {
        showToast('Enter a new name', 'error');
        return;
    }
    if (!/^[A-Za-z0-9._-]+$/.test(newName)) {
        showToast('Name may only contain letters, numbers, dots, dashes and underscores', 'error');
        return;
    }
    const oldName = _archiveRenameName;
    try {
        const r = await fetch('/api/archive/rename', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: oldName, new_name: newName })
        });
        const data = await r.json();
        if (!data.success) {
            showToast(data.error || 'Failed to rename archive', 'error');
            return;
        }
        closeRenameArchiveModal();
        if (_archiveBrowserName === oldName) closeArchiveBrowser();
        if (data.archive && data.archive.rename_warning) {
            showToast('Renamed — warning: ' + data.archive.rename_warning, 'error');
        } else {
            showToast('Archive renamed');
        }
        loadArchiveList();
    } catch (e) {
        showToast('Failed to rename archive', 'error');
    }
}

async function showArchiveDetails(name) {
    const modal = document.getElementById('arch-details-modal');
    const content = document.getElementById('arch-details-content');
    if (!modal || !content) return;
    modal.style.display = 'flex';
    content.innerHTML = '<div class="no-data">Loading…</div>';
    try {
        const r = await fetch('/api/archive/info/' + encodeURIComponent(name));
        const data = await r.json();
        if (!data.success) {
            content.innerHTML = '<div class="no-data">' + escapeHtml(data.error || 'Failed to load details') + '</div>';
            return;
        }
        const a = data.archive || {};
        const meta = a.meta || {};
        const row = (k, v) => `<div class="arch-det-row"><span class="arch-det-key">${k}</span><span class="arch-det-val">${v}</span></div>`;
        const limit = meta.message_limit != null
            ? (meta.message_limit === null ? 'All messages' : meta.message_limit + ' / channel')
            : '—';
        let html = '';
        html += row('Name', escapeHtml(a.name || '—'));
        html += row('Account', escapeHtml(a.username || '—'));
        html += row('Created', escapeHtml(meta.created_at || a.created_at || '—'));
        html += row('Scanned at', escapeHtml(meta.scanned_at || '—'));
        html += row('Message limit', escapeHtml(String(limit)));
        html += row('Servers', a.guild_count != null ? a.guild_count : '—');
        html += row('DMs', a.dm_count != null ? a.dm_count : '—');
        html += row('Messages', (a.message_count || 0).toLocaleString());
        html += row('Size', formatBytes(a.size_bytes));
        html += row('Storage', a.stored_in === 'github' ? '☁️ GitHub data repo' : '💾 Local');
        if (a.stored_in === 'github' && a.github_download) {
            html += row('GitHub zip', `<a href="${escapeHtml(a.github_download)}" target="_blank" rel="noopener">open ↗</a>`);
        }
        if (a.push_error) html += row('Push error', escapeHtml(a.push_error));
        if (a.rename_warning) html += row('Rename warning', escapeHtml(a.rename_warning));
        content.innerHTML = html;
    } catch (e) {
        content.innerHTML = '<div class="no-data">Failed to load details.</div>';
    }
}

function closeArchiveDetailsModal() {
    document.getElementById('arch-details-modal').style.display = 'none';
}

async function purgeAllArchives() {
    if (!confirm('Delete ALL archives permanently? This removes every archive from the GitHub data repo and local storage. This cannot be undone.')) return;
    try {
        const r = await fetch('/api/archive/purge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        const data = await r.json();
        if (!data.success) {
            showToast(data.error || 'Failed to purge archives', 'error');
            return;
        }
        showToast((data.deleted || 0) + ' archive(s) deleted');
        closeArchiveBrowser();
        clearArchiveSearchAll();
        loadArchiveList();
    } catch (e) {
        showToast('Failed to purge archives', 'error');
    }
}

// ─── Search the completed scan (before creating) ─────

let _archiveSearchTimer = null;
let _archiveSearchSeq = 0;

function debouncedArchiveSearch() {
    clearTimeout(_archiveSearchTimer);
    _archiveSearchTimer = setTimeout(archiveSearchNow, 350);
}

async function archiveSearchNow() {
    clearTimeout(_archiveSearchTimer);
    const input = document.getElementById('arch-search-input');
    const q = input ? input.value.trim() : '';
    if (!q || !_archiveAccountId) {
        clearArchiveSearch();
        return;
    }
    const reqId = ++_archiveSearchSeq;
    const status = document.getElementById('arch-search-status');
    const results = document.getElementById('arch-search-results');
    status.textContent = 'Searching…';
    try {
        const r = await fetch('/api/archive/search?user_id=' + encodeURIComponent(_archiveAccountId) +
            '&q=' + encodeURIComponent(q) + '&limit=200');
        if (reqId !== _archiveSearchSeq) return; // stale response — a newer search is in flight
        const data = await r.json();
        if (!data.success) {
            status.textContent = '❌ ' + (data.error || 'Search failed');
            results.innerHTML = '';
            return;
        }
        document.getElementById('arch-search-clear').style.display = '';
        if (!data.total) {
            status.textContent = 'No matches for “' + q + '”.';
            results.innerHTML = '';
            return;
        }
        status.textContent = data.total + ' match' + (data.total === 1 ? '' : 'es') + ' for “' +
            q + '”' + (data.truncated ? ' — showing first ' + data.results.length : '');
        results.innerHTML = data.results.map(m => {
            const loc = m.kind === 'dm' ? '💬' : '📁';
            const attachments = (m.attachments || []).map(u =>
                '<div class="arch-search-attach">📎 ' + escapeHtml(u) + '</div>').join('');
            return `
                <div class="arch-search-item">
                    <div class="arch-search-loc">${loc} ${escapeHtml(m.guild)} / ${escapeHtml(m.channel)}
                        <span class="arch-item-meta">· ${escapeHtml(m.author || 'Unknown')} · ${escapeHtml(m.timestamp || '')}</span></div>
                    <div class="arch-search-content">${highlightMatch(m.content, q)}</div>
                    ${attachments}
                </div>`;
        }).join('');
    } catch (e) {
        status.textContent = '❌ Search failed';
        results.innerHTML = '';
    }
}

function highlightMatch(text, q) {
    const escaped = escapeHtml(text || '');
    const escapedQuery = escapeHtml(q || '');
    if (!escapedQuery) return escaped;
    let re;
    try {
        re = new RegExp('(' + escapedQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
    } catch (e) {
        return escaped;
    }
    return escaped.replace(re, '<mark>$1</mark>');
}

function clearArchiveSearch() {
    clearTimeout(_archiveSearchTimer);
    const input = document.getElementById('arch-search-input');
    if (input) input.value = '';
    const status = document.getElementById('arch-search-status');
    if (status) status.textContent = '';
    const results = document.getElementById('arch-search-results');
    if (results) results.innerHTML = '';
    const clear = document.getElementById('arch-search-clear');
    if (clear) clear.style.display = 'none';
}

// ─── Browse an existing archive in the dashboard ─────

let _archiveBrowserName = null;
let _archiveBrowserDetail = null;
let _archiveBrowserLoc = null;

async function openArchiveBrowser(name) {
    const host = document.getElementById('arch-browser');
    host.style.display = '';
    host.innerHTML = '<div class="no-data">Loading archive…</div>';
    _archiveBrowserName = name;
    _archiveBrowserDetail = null;
    _archiveBrowserLoc = null;
    try {
        const r = await fetch('/api/archive/detail/' + encodeURIComponent(name));
        const data = await r.json();
        if (!data.success) {
            host.innerHTML = '<div class="no-data">' + escapeHtml(data.error || 'Failed to load archive') + '</div>';
            return;
        }
        _archiveBrowserDetail = data.archive;
        renderArchiveBrowser();
    } catch (e) {
        host.innerHTML = '<div class="no-data">Failed to load archive.</div>';
    }
}

function closeArchiveBrowser() {
    _archiveBrowserName = null;
    _archiveBrowserDetail = null;
    _archiveBrowserLoc = null;
    const host = document.getElementById('arch-browser');
    if (host) host.style.display = 'none';
}

function renderArchiveBrowser() {
    const host = document.getElementById('arch-browser');
    const d = _archiveBrowserDetail;
    if (!d) return;
    const meta = d.meta || {};

    const treeRows = [];
    (d.guilds || []).forEach(g => {
        treeRows.push(`<div class="tree-group">📁 ${escapeHtml(g.name)}</div>`);
        (g.channels || []).forEach(ch => {
            const loc = 'guild:' + g.id + ':' + ch.id;
            const active = _archiveBrowserLoc === loc ? ' active' : '';
            treeRows.push(
                `<div class="arch-tree-row${active}" onclick="archiveSelectChannel(this, '${loc}')">` +
                `<span>💬</span><span class="tree-name">${escapeHtml(ch.name)}</span>` +
                `<span class="tree-count">${(ch.message_count || 0).toLocaleString()}</span></div>`);
        });
    });
    if ((d.dms || []).length) {
        treeRows.push('<div class="tree-group">💬 Direct Messages</div>');
        (d.dms || []).forEach(ch => {
            const loc = 'dm:' + ch.id;
            const active = _archiveBrowserLoc === loc ? ' active' : '';
            treeRows.push(
                `<div class="arch-tree-row${active}" onclick="archiveSelectChannel(this, '${loc}')">` +
                `<span>💬</span><span class="tree-name">${escapeHtml(ch.name)}</span>` +
                `<span class="tree-count">${(ch.message_count || 0).toLocaleString()}</span></div>`);
        });
    }
    if (!treeRows.length) {
        treeRows.push('<div class="view-empty">No channels in this archive.</div>');
    }

    host.innerHTML = `
        <div class="arch-browser">
            <div class="arch-browser-head">
                <div class="arch-browser-title">📖 ${escapeHtml(meta.username || _archiveBrowserName)} <span style="color:#8892a0;font-size:0.75rem;">${escapeHtml(meta.created_at || '')} · ${(d.total_messages || 0).toLocaleString()} messages</span></div>
                <div class="arch-browser-actions">
                    <a class="btn-control" href="/api/archive/download/${encodeURIComponent(_archiveBrowserName)}" style="text-decoration:none;">⬇ Download zip</a>
                    <button class="btn-control red" onclick="closeArchiveBrowser()">✕ Close</button>
                </div>
            </div>
            <div class="arch-browser-grid">
                <div class="arch-browser-tree">${treeRows.join('')}</div>
                <div class="arch-browser-view" id="arch-browser-view">
                    <div class="view-empty">👈 Pick a channel to read its messages here.</div>
                </div>
            </div>
        </div>`;
    if (_archiveBrowserLoc) {
        archiveLoadChannelMessages(_archiveBrowserLoc);
    }
}

function archiveSelectChannel(el, loc) {
    _archiveBrowserLoc = loc;
    // Update the active highlight in the tree.
    const rows = document.querySelectorAll('.arch-browser-tree .arch-tree-row');
    rows.forEach(row => row.classList.remove('active'));
    if (el) el.classList.add('active');
    // Load the messages.
    const view = document.getElementById('arch-browser-view');
    if (view) view.innerHTML = '<div class="view-empty">Loading messages…</div>';
    archiveLoadChannelMessages(loc);
}

async function archiveLoadChannelMessages(loc) {
    const view = document.getElementById('arch-browser-view');
    if (!view) return;
    try {
        const r = await fetch('/api/archive/messages/' + encodeURIComponent(_archiveBrowserName) + '?loc=' + encodeURIComponent(loc));
        const data = await r.json();
        if (!data.success) {
            view.innerHTML = '<div class="view-empty">' + escapeHtml(data.error || 'Failed to load messages') + '</div>';
            return;
        }
        const msgs = data.messages || [];
        if (!msgs.length) {
            view.innerHTML = '<div class="view-empty">No messages in this channel.</div>';
            return;
        }
        view.innerHTML = msgs.map(m => {
            const ts = escapeHtml((m.timestamp || '').replace('T', ' ').slice(0, 16));
            const atts = (m.attachments || []).map(u =>
                '<div class="arch-msg-attach">📎 <a href="' + escapeHtml(u) + '" target="_blank" rel="noopener">' + escapeHtml(u) + '</a></div>').join('');
            return `<div class="arch-msg">
                <span class="arch-msg-author">${escapeHtml(m.author || 'Unknown')}</span><span class="arch-msg-time">${ts}</span>
                <div class="arch-msg-content">${escapeHtml(m.content || '')}</div>${atts}
            </div>`;
        }).join('');
    } catch (e) {
        view.innerHTML = '<div class="view-empty">Failed to load messages.</div>';
    }
}

// ─── Search across all created archives ───────────────

let _archiveSearchAllTimer = null;
let _archiveSearchAllSeq = 0;

function debouncedArchiveSearchAll() {
    clearTimeout(_archiveSearchAllTimer);
    _archiveSearchAllTimer = setTimeout(archiveSearchAllNow, 400);
}

async function archiveSearchAllNow() {
    clearTimeout(_archiveSearchAllTimer);
    const input = document.getElementById('arch-archive-search-input');
    const q = input ? input.value.trim() : '';
    const status = document.getElementById('arch-archive-search-status');
    const results = document.getElementById('arch-archive-search-results');
    if (!q) {
        clearArchiveSearchAll();
        return;
    }
    const reqId = ++_archiveSearchAllSeq;
    status.textContent = 'Searching archives…';
    try {
        const r = await fetch('/api/archive/search-archives?q=' + encodeURIComponent(q) + '&limit=200');
        if (reqId !== _archiveSearchAllSeq) return; // stale
        const data = await r.json();
        if (!data.success) {
            status.textContent = '❌ ' + (data.error || 'Search failed');
            results.innerHTML = '';
            return;
        }
        document.getElementById('arch-archive-search-clear').style.display = '';
        if (!data.total) {
            status.textContent = 'No matches for “' + q + '” in any archive.';
            results.innerHTML = '';
            return;
        }
        status.textContent = data.total + ' match' + (data.total === 1 ? '' : 'es') + ' across archives' +
            (data.truncated ? ' — showing first ' + data.results.length : '');
        results.innerHTML = data.results.map(m => {
            const loc = m.kind === 'dm' ? '💬' : '📁';
            const atts = (m.attachments || []).map(u =>
                '<div class="arch-search-attach">📎 ' + escapeHtml(u) + '</div>').join('');
            return `
                <div class="arch-search-item">
                    <div class="arch-search-loc">${loc} ${escapeHtml(m.archive)} / ${escapeHtml(m.guild)} / ${escapeHtml(m.channel)}
                        <span class="arch-item-meta">· ${escapeHtml(m.author || 'Unknown')} · ${escapeHtml(m.timestamp || '')}</span></div>
                    <div class="arch-search-content">${highlightMatch(m.content, q)}</div>
                    ${atts}
                </div>`;
        }).join('');
    } catch (e) {
        status.textContent = '❌ Search failed';
        results.innerHTML = '';
    }
}

function clearArchiveSearchAll() {
    clearTimeout(_archiveSearchAllTimer);
    const input = document.getElementById('arch-archive-search-input');
    if (input) input.value = '';
    const status = document.getElementById('arch-archive-search-status');
    if (status) status.textContent = '';
    const results = document.getElementById('arch-archive-search-results');
    if (results) results.innerHTML = '';
    const clear = document.getElementById('arch-archive-search-clear');
    if (clear) clear.style.display = 'none';
}
