/* Distributed worker dashboard controls. */

let _workerList = [];

function workerEsc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

function workerDate(timestamp) {
    return timestamp ? new Date(timestamp * 1000).toLocaleString() : 'Never';
}

window.populateWorkerSelectors = async function() {
    const select = document.getElementById('proxy-test-worker');
    if (!select) return;
    try {
        const response = await fetch('/api/workers');
        const data = await response.json();
        if (!response.ok || !data.success) return;
        const online = (data.workers || []).filter(w => w.online && !w.revoked);
        select.innerHTML = '<option value="">Test on main server</option>' + online.map(w =>
            `<option value="${workerEsc(w.id)}">${workerEsc(w.name)}</option>`
        ).join('');
    } catch (error) {
        // Keep the main-server option if workers are unavailable.
    }
};

window.loadWorkers = async function() {
    const list = document.getElementById('workers-list');
    if (!list) return;
    try {
        const response = await fetch('/api/workers');
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.error || 'Failed to load workers');
        _workerList = data.workers || [];
        renderWorkers();
        populateWorkerSelectors();
    } catch (error) {
        list.innerHTML = '<div class="no-data error">Failed to load workers.</div>';
    }
};

function renderWorkers() {
    const list = document.getElementById('workers-list');
    if (!list) return;
    if (!_workerList.length) {
        list.innerHTML = '<div class="no-data">No workers linked yet. Generate an enrollment token above.</div>';
        return;
    }
    list.innerHTML = _workerList.map(worker => {
        const resources = worker.resources || {};
        const status = worker.revoked ? 'REVOKED' : (worker.online ? 'ONLINE' : 'OFFLINE');
        const statusColor = worker.revoked ? '#ff4444' : (worker.online ? '#00ff88' : '#888');
        const accounts = (worker.active_accounts || []).length;
        const capabilities = (worker.capabilities || []).join(', ') || 'none reported';
        return `<div class="account-config-card" style="${worker.revoked ? 'opacity:0.55;' : ''}">
            <div class="account-config-info">
                <strong>${workerEsc(worker.name)}</strong>
                <span class="mono">${workerEsc(worker.id)}</span>
                <span class="dim" style="color:${statusColor};">${status} · ${accounts} account(s) · Last seen: ${workerDate(worker.last_seen)}</span>
                <span class="dim">CPU: ${workerEsc(resources.cpu_count || '?')} · RAM: ${workerEsc(resources.memory_used_mb || '?')}/${workerEsc(resources.memory_total_mb || '?')} MB · ${workerEsc(capabilities)}</span>
                ${worker.last_error ? `<span class="dim" style="color:#ffb454;">${workerEsc(worker.last_error)}</span>` : ''}
            </div>
            <div class="account-config-actions">
                ${!worker.revoked ? `<button class="btn-proxy-sm danger" onclick="revokeWorker('${workerEsc(worker.id)}')">Revoke</button>` : ''}
            </div>
        </div>`;
    }).join('');
}

window.createWorkerEnrollment = async function() {
    const labelInput = document.getElementById('worker-link-label');
    const result = document.getElementById('worker-enrollment-result');
    const label = (labelInput ? labelInput.value : '').trim() || 'Worker';
    try {
        const response = await fetch('/api/workers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label })
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            showToast(data.error || 'Could not create enrollment token', 'error');
            return;
        }
        const token = data.enrollment.token;
        const serverUrl = window.location.origin;
        result.style.display = '';
        result.innerHTML = `<div style="padding:12px;background:#0a0a0c;border:1px solid #333;border-radius:8px;">
            <div style="color:#ffb454;font-weight:600;margin-bottom:8px;">Copy this token now — it expires in 15 minutes and is shown only once.</div>
            <code id="new-worker-token" style="display:block;word-break:break-all;color:#fff;font-size:0.78rem;margin-bottom:10px;">${workerEsc(token)}</code>
            <textarea readonly rows="3" style="width:100%;background:#111;color:#ddd;border:1px solid #333;border-radius:5px;padding:8px;font-family:var(--font-mono);font-size:0.72rem;">LIMEY_SERVER_URL=${workerEsc(serverUrl)}
LIMEY_WORKER_NAME=${workerEsc(label)}
LIMEY_WORKER_ENROLLMENT_TOKEN=${workerEsc(token)}</textarea>
            <div style="display:flex;gap:8px;margin-top:10px;">
                <button class="btn-control green" onclick="copyWorkerEnrollment()">Copy Token</button>
                <button class="btn-control" onclick="document.getElementById('worker-enrollment-result').style.display='none'">Hide</button>
            </div>
        </div>`;
        if (labelInput) labelInput.value = '';
    } catch (error) {
        showToast('Could not create enrollment token', 'error');
    }
};

window.copyWorkerEnrollment = async function() {
    const token = document.getElementById('new-worker-token');
    if (!token) return;
    try {
        await navigator.clipboard.writeText(token.textContent || '');
        showToast('Enrollment token copied', 'success');
    } catch (error) {
        showToast('Copy failed — select the token manually', 'error');
    }
};

window.revokeWorker = async function(workerId) {
    if (!confirm('Revoke this worker? It will stop receiving assignments on its next poll.')) return;
    try {
        const response = await fetch('/api/workers/' + encodeURIComponent(workerId), { method: 'DELETE' });
        const data = await response.json();
        if (!response.ok || !data.success) {
            showToast(data.error || 'Failed to revoke worker', 'error');
            return;
        }
        showToast('Worker revoked', 'success');
        loadWorkers();
    } catch (error) {
        showToast('Failed to revoke worker', 'error');
    }
};

document.addEventListener('DOMContentLoaded', () => {
    setInterval(() => {
        const view = document.getElementById('workers');
        if (view && view.classList.contains('active-view')) loadWorkers();
    }, 15000);
});
