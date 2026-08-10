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
        if (!archives.length) {
            list.innerHTML = '<div class="no-data">No archives yet. Run a scan and create one.</div>';
            return;
        }
        list.innerHTML = archives.map(a => {
            const storage = a.stored_in === 'github'
                ? '<span class="arch-badge github">☁️ GitHub</span>'
                : '<span class="arch-badge local">💾 Local fallback</span>';
            return `
            <div class="arch-item">
                <div>
                    <div class="arch-item-name">📦 ${escapeHtml(a.username)} — ${escapeHtml(a.created_at || '')} ${storage}</div>
                    <div class="arch-item-meta">
                        ${a.guild_count} servers · ${a.dm_count} DMs · ${(a.message_count || 0).toLocaleString()} messages · ${formatBytes(a.size_bytes)}
                    </div>
                </div>
                <div style="display:flex;gap:8px;align-items:center;">
                    <a class="btn-control green" href="${escapeHtml(a.download)}" style="text-decoration:none;">⬇ Download</a>
                    <button class="btn-control red" onclick="deleteArchiveItem('${escapeHtml(a.name)}')">🗑️</button>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        list.innerHTML = '<div class="no-data">Failed to load archives.</div>';
    }
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
            loadArchiveList();
        } else {
            showToast(data.error || 'Failed to delete', 'error');
        }
    } catch (e) {
        showToast('Failed to delete', 'error');
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
