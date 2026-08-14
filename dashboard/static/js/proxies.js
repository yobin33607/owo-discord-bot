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

let proxyList = [];

window.fetchProxies = async function() {
    try {
        const res = await fetch('/api/proxies');
        const data = await res.json();
        proxyList = data.proxies || [];
        renderProxyStats();
        renderProxyTable();
        populateAccountProxyDropdown();
        if (typeof window.populateWorkerSelectors === 'function') window.populateWorkerSelectors();
    } catch (e) {
        console.error('Failed to fetch proxies', e);
        _proxyRowCache.clear();
        _proxyRenderInFlight = false;
        const el = document.getElementById('proxy-table-body');
        if (el) el.innerHTML = `<tr><td colspan="8" class="no-data error">Failed to load proxies</td></tr>`;
    }
};

function renderProxyStats() {
    let total = 0, enabled = 0, healthy = 0, assigned = 0, free = 0;
    for (const p of proxyList) {
        total++;
        const isEnabled = p.enabled !== false;
        if (isEnabled) enabled++;
        if (p.status === 'ok') healthy++;
        if (p.assigned_to) assigned++;
        if (isEnabled && !p.assigned_to) free++;
    }

    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('proxy-stat-total', total);
    set('proxy-stat-enabled', enabled);
    set('proxy-stat-healthy', healthy);
    set('proxy-stat-assigned', assigned);
    set('proxy-stat-free', free);
}

function statusBadge(status) {
    const cls = status === 'ok' ? 'proxy-status-ok' : (status === 'fail' ? 'proxy-status-fail' : 'proxy-status-unknown');
    const label = status === 'ok' ? 'OK' : (status === 'fail' ? 'FAIL' : 'UNKNOWN');
    return `<span class="proxy-status-badge ${cls}">${label}</span>`;
}

// Render the attempt results from the last test, e.g. "3/5"
function attemptsBadge(p) {
    const a = p.last_attempts;
    if (!a || a.total == null) return '<span class="proxy-attempts none">—</span>';
    const all = a.ok >= a.total;
    const partial = a.ok > 0;
    const cls = all ? 'all' : (partial ? 'partial' : 'none');
    const pct = a.total > 0 ? Math.round((a.ok / a.total) * 100) : 0;
    return `<span class="proxy-attempts ${cls}" title="${a.ok} of ${a.total} attempts succeeded (${pct}%)">${a.ok}/${a.total}</span>`;
}

// Row elements cached by proxy id, so a single test result updates just its
// own row instead of rebuilding the entire table (which made the page lag
// with large pools and high-concurrency test runs).
const _proxyRowCache = new Map();
let _proxyRenderGen = 0;
let _proxyRenderInFlight = false;
const _PROXY_RENDER_CHUNK = 300; // rows appended per frame

function escapeHtml(v) {
    return String(v).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

function proxyRowHtml(p, i) {
    return `
        <tr data-id="${escapeHtml(p.id)}">
            <td>${i + 1}</td>
            <td>${escapeHtml(p.label || '-')}</td>
            <td>${escapeHtml(p.type || 'socks5')}</td>
            <td class="mono">${escapeHtml(p.host)}:${escapeHtml(p.port)}</td>
            <td class="js-proxy-status">${statusBadge(p.status || 'unknown')}</td>
            <td class="js-proxy-attempts">${attemptsBadge(p)}</td>
            <td>${escapeHtml(p.assigned_to || '-')}</td>
            <td class="proxy-actions">
                <button class="btn-proxy-sm" data-proxy-action="test">Test</button>
                <button class="btn-proxy-sm danger" data-proxy-action="delete">Del</button>
            </td>
        </tr>
    `;
}

function renderProxyTable() {
    const body = document.getElementById('proxy-table-body');
    if (!body) return;

    _proxyRowCache.clear();
    const statusEl = document.getElementById('proxy-render-status');
    if (!proxyList.length) {
        body.innerHTML = '<tr><td colspan="8" class="no-data">No proxies yet. Use bulk import or add one.</td></tr>';
        if (statusEl) statusEl.textContent = '';
        _proxyRenderInFlight = false;
        return;
    }

    // Rebuild in chunks (yielding between frames) so a huge proxy list never
    // freezes the page with one giant synchronous innerHTML parse.
    const gen = ++_proxyRenderGen;
    _proxyRenderInFlight = true;
    const total = proxyList.length;
    body.innerHTML = '';
    let idx = 0;

    const step = () => {
        if (gen !== _proxyRenderGen) return; // superseded — the new render owns the flag
        const end = Math.min(idx + _PROXY_RENDER_CHUNK, total);
        const frag = document.createDocumentFragment();
        for (; idx < end; idx++) {
            const p = proxyList[idx];
            const tr = document.createElement('tr');
            tr.innerHTML = proxyRowHtml(p, idx);
            _proxyRowCache.set(p.id, {
                tr,
                statusTd: tr.querySelector('.js-proxy-status'),
                attemptsTd: tr.querySelector('.js-proxy-attempts'),
            });
            frag.appendChild(tr);
        }
        body.appendChild(frag);
        if (idx < total) {
            if (statusEl) statusEl.textContent = `Rendering ${idx}/${total} proxies…`;
            requestAnimationFrame(step);
        } else {
            if (statusEl) statusEl.textContent = '';
            _proxyRenderInFlight = false;
        }
    };
    requestAnimationFrame(step);
}

// Update only the affected row's cells — the key fix for test-run lag.
function updateProxyRow(p) {
    if (!p || p.id == null) return;
    const row = _proxyRowCache.get(p.id);
    if (!row) {
        // A render in flight re-reads the live proxyList, so the fresh status
        // will be picked up anyway — don't cancel and restart it.
        if (!_proxyRenderInFlight) scheduleProxyTableRender();
        return;
    }
    if (row.statusTd) row.statusTd.innerHTML = statusBadge(p.status || 'unknown');
    if (row.attemptsTd) row.attemptsTd.innerHTML = attemptsBadge(p);
}

// Row actions (Test / Del) via event delegation — avoids building inline
// onclick strings from data, and HTML-unescapes ids safely through data-id.
document.addEventListener('click', function(e) {
    const btn = e.target.closest('[data-proxy-action]');
    if (!btn) return;
    const tr = btn.closest('tr[data-id]');
    if (!tr) return;
    const id = tr.dataset.id;
    if (btn.dataset.proxyAction === 'test') window.testProxy(id);
    else if (btn.dataset.proxyAction === 'delete') window.deleteProxy(id);
});

// Coalesce bursts of updates into one render per animation frame.
let _proxyRenderScheduled = false;
function scheduleProxyTableRender() {
    if (_proxyRenderScheduled) return;
    _proxyRenderScheduled = true;
    requestAnimationFrame(() => {
        _proxyRenderScheduled = false;
        renderProxyTable();
    });
}

window.bulkImportProxies = async function() {
    const textarea = document.getElementById('proxy-bulk-text');
    const resultEl = document.getElementById('proxy-bulk-result');
    if (!textarea) return;

    const text = textarea.value.trim();
    if (!text) {
        showToast('Paste at least one proxy line', 'info');
        return;
    }

    try {
        const res = await fetch('/api/proxies/bulk', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
        });
        const data = await res.json();
        if (resultEl) {
            let msg = `Added ${data.added} proxies.`;
            if (data.errors && data.errors.length) {
                msg += ` ${data.errors.length} line(s) skipped.`;
                const errLines = data.errors.slice(0, 5).map(e => `Line ${e.line}: ${e.error}`).join('<br>');
                resultEl.innerHTML = `<div class="proxy-bulk-ok">${msg}</div><div class="proxy-bulk-err">${errLines}</div>`;
            } else {
                resultEl.innerHTML = `<div class="proxy-bulk-ok">${msg}</div>`;
            }
        }
        textarea.value = '';
        proxyList = data.proxies || [];
        renderProxyStats();
        renderProxyTable();
        populateAccountProxyDropdown();
        showToast(`Imported ${data.added} proxies`, 'success');
    } catch (e) {
        showToast('Bulk import failed', 'error');
    }
};

let proxyTesting = false;
let proxyTestStopRequested = false;
const PROXY_TEST_CONCURRENCY = 8; // max proxies tested at the same time

function applyProxyTestResult(data) {
    if (!data || data.id == null) return;
    const idx = proxyList.findIndex(x => x.id === data.id);
    if (idx === -1) return;
    if (data.proxy) {
        proxyList[idx] = data.proxy;
    } else {
        if (data.status) proxyList[idx].status = data.status;
        if (data.last_check) proxyList[idx].last_check = data.last_check;
        if (data.last_attempts) proxyList[idx].last_attempts = data.last_attempts;
    }
}

// Marks a proxy failed after a REQUEST-level error (no backend verdict).
// last_attempts is left untouched so the row shows "—" instead of a fake 0/5.
function markProxyFail(p) {
    const idx = proxyList.findIndex(x => x.id === p.id);
    if (idx !== -1) {
        proxyList[idx].status = 'fail';
        proxyList[idx].last_check = new Date().toISOString();
    }
}

// Test a single proxy via the API (used by the pool and the row button)
async function testProxyRequest(p, persist) {
    const workerSelect = document.getElementById('proxy-test-worker');
    const worker_id = workerSelect ? workerSelect.value : '';
    const res = await fetch('/api/proxies/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: p.id, persist: persist, worker_id }),
    });
    const data = await res.json();
    if (res.ok && data.ok) return { ok: true, queued: !!data.queued, data };
    return { ok: false, queued: false, data };
}

// Test many proxies in PARALLEL, up to PROXY_TEST_CONCURRENCY at a time.
// Each proxy's 5 attempts also run in parallel on the backend, so a whole
// pool can be verified in a single round-trip's worth of time.
// `limit` (optional): only test the first N matching proxies (0/undefined = all).
async function testProxiesParallel(filter, limit) {
    if (proxyTesting) {
        showToast('A proxy test is already running', 'info');
        return;
    }

    const matches = proxyList.filter(p =>
        p.enabled !== false && (filter === 'all' || (p.status || 'unknown') !== 'ok')
    );
    if (!matches.length) {
        showToast(filter === 'all' ? 'No enabled proxies to test' : 'No unverified proxies to test', 'info');
        return;
    }
    const targets = (limit && limit > 0 && limit < matches.length) ? matches.slice(0, limit) : matches;

    proxyTesting = true;
    const progressWrap = document.getElementById('proxy-test-progress');
    const progressFill = document.getElementById('proxy-test-progress-fill');
    const progressText = document.getElementById('proxy-test-progress-text');
    const showProgress = (show) => { if (progressWrap) progressWrap.style.display = show ? 'flex' : 'none'; };
    const setProgress = (text, fraction) => {
        if (progressFill) progressFill.style.width = `${Math.max(0, Math.min(100, Math.round((fraction || 0) * 100)))}%`;
        if (progressText) progressText.textContent = text;
    };
    const toggleBtns = (busy) => {
        ['testAllProxiesBtn', 'testUnverifiedProxiesBtn', 'deleteFailedProxiesBtn'].forEach(id => {
            const b = document.getElementById(id);
            if (b) b.disabled = busy;
        });
        const stopBtn = document.getElementById('stopProxyTestsBtn');
        if (stopBtn) stopBtn.disabled = !busy;
    };
    toggleBtns(true);

    proxyTestStopRequested = false;
    let okCount = 0;
    let queuedCount = 0;
    let completedCount = 0;
    let stopped = false;
    showProgress(true);
    setProgress(`Testing 0/${targets.length}...`, 0);

    const tick = (label, done, p) => {
        setProgress(label, done / targets.length);
        renderProxyStats();
        if (p) updateProxyRow(p);
    };

    try {
        // Run the test in a small worker pool: each worker pulls the next
        // proxy and tests it. Concurrency stays bounded by the pool size.
        const workers = [];
        let nextIdx = 0;
        const runWorker = async () => {
            while (true) {
                if (proxyTestStopRequested) {
                    stopped = true;
                    return;
                }
                const i = nextIdx++;
                if (i >= targets.length) return;
                const p = targets[i];
                setProgress(`Testing ${p.host}:${p.port}...`, completedCount / targets.length);
                try {
                    const { ok, queued, data } = await testProxyRequest(p, false);
                    if (ok && queued) queuedCount++;
                    else if (ok) okCount++;
                    if (!queued) {
                        applyProxyTestResult(data);
                        if (!ok) markProxyFail(p);
                    }
                } catch (e) {
                    markProxyFail(p);
                }
                completedCount++;
                // Refresh just this row in place — no full-table rebuild per
                // completed proxy (that was the source of the page lag).
                const updated = proxyList.find(x => x.id === p.id);
                tick(`Tested ${completedCount}/${targets.length}: ${p.host}:${p.port}`, completedCount, updated);
            }
        };
        const poolSize = Math.min(PROXY_TEST_CONCURRENCY, targets.length);
        for (let w = 0; w < poolSize; w++) workers.push(runWorker());
        await Promise.all(workers);

        // Persist the results collected so far in a single write
        // (avoids one GitHub commit per proxy; skipped if nothing was tested)
        if (completedCount > 0 && queuedCount === 0) {
            try {
                const persistRes = await fetch('/api/proxies', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ proxies: proxyList }),
                });
                if (!persistRes.ok) {
                    console.error('Failed to persist proxy test results:', persistRes.status);
                    showToast('Warning: results could not be saved', 'error');
                }
            } catch (e) {
                console.error('Failed to persist proxy test results', e);
                showToast('Warning: results could not be saved', 'error');
            }
        }
    } finally {
        showProgress(false);
        toggleBtns(false);
        proxyTesting = false;
        proxyTestStopRequested = false;
    }
    if (stopped) {
        showToast(completedCount > 0 ? `Test stopped: ${okCount}/${completedCount} OK so far` : 'Test stopped — no proxies tested yet', 'info');
    } else {
        const scope = (limit && limit > 0 && targets.length < matches.length)
            ? ` (first ${targets.length} of ${matches.length})`
            : '';
        const queuedText = queuedCount ? `, ${queuedCount} queued on worker` : '';
        showToast(`Test complete: ${okCount}/${targets.length} OK${queuedText}${scope}`, queuedCount || okCount === targets.length ? 'success' : 'info');
    }
}

window.stopProxyTests = function() {
    if (!proxyTesting) return;
    proxyTestStopRequested = true;
    const stopBtn = document.getElementById('stopProxyTestsBtn');
    if (stopBtn) stopBtn.disabled = true;
    showToast('Stopping proxy test...', 'info');
};

window.testAllProxies = function() { closeTestUnverifiedOptions(); testProxiesParallel('all'); };
window.testUnverifiedProxies = function() {
    // Read the current count from the popover input; default to a small batch
    // (10) so "Test Unverified" stays quick unless the user asks for more.
    const input = document.getElementById('testUnverifiedCount');
    const raw = input ? parseInt(input.value, 10) : NaN;
    const limit = (raw && raw > 0) ? raw : 10;
    closeTestUnverifiedOptions();
    testProxiesParallel('unverified', limit);
};

// Quick preset / custom-count entry from the options popover.
window.quickTestUnverified = function(limit) {
    if (limit === undefined) {
        const input = document.getElementById('testUnverifiedCount');
        const raw = input ? parseInt(input.value, 10) : NaN;
        limit = (raw && raw > 0) ? raw : 10;
    }
    closeTestUnverifiedOptions();
    testProxiesParallel('unverified', limit);
};

window.toggleTestUnverifiedOptions = function(e) {
    if (e && e.stopPropagation) e.stopPropagation();
    const pop = document.getElementById('testUnverifiedOptions');
    if (!pop) return;
    const open = pop.classList.toggle('open');
    if (open) {
        // Show how many unverified proxies are available to test
        const hint = document.getElementById('testUnverifiedCountHint');
        if (hint) {
            const count = proxyList.filter(p => p.enabled !== false && (p.status || 'unknown') !== 'ok').length;
            hint.textContent = count === 0 ? 'No unverified proxies' : `${count} unverified ready`;
        }
    }
    // Mark the active preset chip (only when the custom input is empty)
    const countInput = document.getElementById('testUnverifiedCount');
    const noCustom = !countInput || !countInput.value;
    pop.querySelectorAll('.proxy-test-option-chip').forEach(chip => {
        chip.classList.toggle('active', noCustom && chip.dataset.limit === '10');
    });
};

function closeTestUnverifiedOptions() {
    const pop = document.getElementById('testUnverifiedOptions');
    if (pop) pop.classList.remove('open');
}

// Close the popover when clicking anywhere outside it.
document.addEventListener('click', function(e) {
    const wrap = document.querySelector('.proxy-test-unverified-wrap');
    if (wrap && !wrap.contains(e.target)) closeTestUnverifiedOptions();
});

window.testProxy = async function(id) {
    try {
        const p = proxyList.find(x => x.id === id);
        if (!p) return;
        const { ok, queued, data } = await testProxyRequest(p, true);
        if (queued) {
            showToast('Proxy test queued on worker', 'info');
            return;
        }
        applyProxyTestResult(data);
        renderProxyStats();
        updateProxyRow(proxyList.find(x => x.id === id));
        const a = data.last_attempts || (data.proxy && data.proxy.last_attempts);
        const suffix = a ? ` (${a.ok}/${a.total} attempts)` : '';
        showToast(ok ? `Proxy OK${suffix}` : 'Proxy failed', ok ? 'success' : 'error');
    } catch (e) {
        showToast('Test failed', 'error');
    }
};

window.autoAssignProxies = async function() {
    try {
        const res = await fetch('/api/proxies/assign', { method: 'POST' });
        const data = await res.json();
        proxyList = data.proxies || proxyList;
        renderProxyStats();
        renderProxyTable();
        populateAccountProxyDropdown();
        await fetchAccountConfig();
        showToast(`Assigned ${(data.assigned || []).length} proxies`, 'success');
    } catch (e) {
        showToast('Auto-assign failed', 'error');
    }
};

window.deleteProxy = async function(id) {
    if (!confirm('Remove this proxy? Accounts using it will switch to direct.')) return;
    try {
        const res = await fetch(`/api/proxies/${id}`, { method: 'DELETE' });
        const data = await res.json();
        proxyList = data.proxies || [];
        renderProxyStats();
        renderProxyTable();
        populateAccountProxyDropdown();
        await fetchAccountConfig();
        showToast('Proxy removed', 'info');
    } catch (e) {
        showToast('Delete failed', 'error');
    }
};

window.deleteAllProxies = async function() {
    if (!confirm('⚠️ Are you sure you want to delete ALL proxies? This cannot be undone.')) return;
    try {
        const res = await fetch('/api/proxies/all', { method: 'DELETE' });
        const data = await res.json();
        if (data.status === 'success') {
            proxyList = [];
            renderProxyStats();
            renderProxyTable();
            populateAccountProxyDropdown();
            await fetchAccountConfig();
            showToast('All proxies deleted', 'info');
        } else {
            showToast('Failed to delete proxies', 'error');
        }
    } catch (e) {
        showToast('Error deleting proxies', 'error');
    }
};

window.deleteFailedProxies = async function() {
    if (proxyTesting) {
        // Test results are held in memory until the run finishes, so deleting from
        // disk now would remove proxies the test has already marked OK.
        showToast('Wait for the proxy test to finish, then delete failed proxies', 'info');
        return;
    }
    if (!confirm('Delete all proxies with status "fail"?')) return;
    try {
        const res = await fetch('/api/proxies/failed', { method: 'DELETE' });
        const data = await res.json();
        if (data.status === 'success') {
            proxyList = data.proxies || [];
            renderProxyStats();
            renderProxyTable();
            populateAccountProxyDropdown();
            await fetchAccountConfig();
            showToast(`Deleted ${data.count || 0} failed proxies`, 'info');
        } else {
            showToast('Failed to delete failed proxies', 'error');
        }
    } catch (e) {
        showToast('Error deleting failed proxies', 'error');
    }
};

window.showAddProxyModal = function() {
    const modal = document.getElementById('proxy-add-modal');
    if (modal) modal.classList.add('visible');
};

window.hideAddProxyModal = function() {
    const modal = document.getElementById('proxy-add-modal');
    if (modal) modal.classList.remove('visible');
    const input = document.getElementById('proxy-add-line');
    if (input) input.value = '';
};

window.addSingleProxy = async function() {
    const input = document.getElementById('proxy-add-line');
    if (!input || !input.value.trim()) return;

    const res = await fetch('/api/proxies/bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: input.value.trim() }),
    });
    const data = await res.json();
    proxyList = data.proxies || proxyList;
    renderProxyStats();
    renderProxyTable();
    populateAccountProxyDropdown();
    hideAddProxyModal();
    showToast('Proxy added', 'success');
};

let _proxyDropdownSig = '';
function populateAccountProxyDropdown() {
    const sel = document.getElementById('acct-form-proxy');
    if (!sel) return;
    // Rebuilding the <select> resets the user's open form state — only rebuild
    // when the actual option list changed (ids/labels/types), not on every
    // fetch (status changes don't affect the dropdown).
    const sig = proxyList
        .filter(p => p.enabled !== false)
        .map(p => p.id + '\u0001' + (p.label || p.host + ':' + p.port) + '\u0001' + (p.type || 'socks5'))
        .join('\u0002');
    if (sig === _proxyDropdownSig) return;
    _proxyDropdownSig = sig;
    const current = sel.value;
    sel.innerHTML = '<option value="">None (direct)</option>' +
        proxyList.filter(p => p.enabled !== false).map(p =>
            `<option value="${p.id}">${p.label || p.host + ':' + p.port} [${p.type || 'socks5'}]</option>`
        ).join('');
    if (current) sel.value = current;
}