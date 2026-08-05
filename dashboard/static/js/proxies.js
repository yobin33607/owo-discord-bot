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
    } catch (e) {
        console.error('Failed to fetch proxies', e);
        const el = document.getElementById('proxy-table-body');
        if (el) el.innerHTML = `<tr><td colspan="8" class="no-data error">Failed to load proxies</td></tr>`;
    }
};

function renderProxyStats() {
    const total = proxyList.length;
    const enabled = proxyList.filter(p => p.enabled !== false).length;
    const healthy = proxyList.filter(p => p.status === 'ok').length;
    const assigned = proxyList.filter(p => p.assigned_to).length;
    const free = proxyList.filter(p => p.enabled !== false && !p.assigned_to).length;

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

function renderProxyTable() {
    const body = document.getElementById('proxy-table-body');
    if (!body) return;

    if (!proxyList.length) {
        body.innerHTML = '<tr><td colspan="8" class="no-data">No proxies yet. Use bulk import or add one.</td></tr>';
        return;
    }

    body.innerHTML = proxyList.map((p, i) => `
        <tr>
            <td>${i + 1}</td>
            <td>${p.label || '-'}</td>
            <td>${p.type || 'socks5'}</td>
            <td class="mono">${p.host}:${p.port}</td>
            <td>${statusBadge(p.status || 'unknown')}</td>
            <td>${attemptsBadge(p)}</td>
            <td>${p.assigned_to || '-'}</td>
            <td class="proxy-actions">
                <button class="btn-proxy-sm" onclick="testProxy('${p.id}')">Test</button>
                <button class="btn-proxy-sm danger" onclick="deleteProxy('${p.id}')">Del</button>
            </td>
        </tr>
    `).join('');
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
    const res = await fetch('/api/proxies/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: p.id, persist: persist }),
    });
    const data = await res.json();
    if (res.ok && data.ok) return { ok: true, data };
    return { ok: false, data };
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
    let completedCount = 0;
    let stopped = false;
    showProgress(true);
    setProgress(`Testing 0/${targets.length}...`, 0);

    const tick = (label, done) => {
        setProgress(label, done / targets.length);
        renderProxyStats();
        renderProxyTable();
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
                    const { ok, data } = await testProxyRequest(p, false);
                    if (ok) okCount++;
                    applyProxyTestResult(data);
                    if (!ok) markProxyFail(p);
                } catch (e) {
                    markProxyFail(p);
                }
                completedCount++;
                tick(`Tested ${completedCount}/${targets.length}: ${p.host}:${p.port}`, completedCount);
            }
        };
        const poolSize = Math.min(PROXY_TEST_CONCURRENCY, targets.length);
        for (let w = 0; w < poolSize; w++) workers.push(runWorker());
        await Promise.all(workers);

        // Persist the results collected so far in a single write
        // (avoids one GitHub commit per proxy; skipped if nothing was tested)
        if (completedCount > 0) {
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
        showToast(`Test complete: ${okCount}/${targets.length} OK${scope}`, okCount === targets.length ? 'success' : 'info');
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
        const { ok, data } = await testProxyRequest(p, true);
        applyProxyTestResult(data);
        renderProxyStats();
        renderProxyTable();
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

function populateAccountProxyDropdown() {
    const sel = document.getElementById('acct-form-proxy');
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = '<option value="">None (direct)</option>' +
        proxyList.filter(p => p.enabled !== false).map(p =>
            `<option value="${p.id}">${p.label || p.host + ':' + p.port} [${p.type || 'socks5'}]</option>`
        ).join('');
    if (current) sel.value = current;
}