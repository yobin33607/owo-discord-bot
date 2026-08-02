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

// ─── Discord Quest Orb Grinder Page ──────────────────────
let _questPollInterval = null;
let _lastQuestJson = '';

const QUEST_STATUS_META = {
    available:    { label: 'AVAILABLE',  color: '#3b82f6' },
    enrolling:    { label: 'ENROLLING',  color: '#ffcc00' },
    progressing:  { label: 'GRINDING',   color: '#ff3e3e' },
    claiming:     { label: 'CLAIMING',   color: '#ffcc00' },
    claimable:    { label: 'READY TO CLAIM', color: '#00ff88' },
    claimed:      { label: 'CLAIMED',    color: '#64748b' },
    expired:      { label: 'EXPIRED',    color: '#ff4d4d' },
    unsupported:  { label: 'MANUAL ONLY', color: '#64748b' },
    error:        { label: 'ERROR',      color: '#ff4d4d' },
    needs_captcha:{ label: 'CAPTCHA NEEDED', color: '#ffcc00' }
};

function questFormatDuration(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    if (seconds < 60) return `${seconds}s`;
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    if (m < 60) return `${m}m ${s}s`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
}

function questTimeAgo(ts) {
    if (!ts) return 'never';
    const diff = Math.floor(Date.now() / 1000 - ts);
    if (diff < 10) return 'just now';
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    return `${Math.floor(diff / 3600)}h ago`;
}

function questExpiresIn(iso) {
    if (!iso) return '';
    const exp = new Date(iso).getTime();
    if (isNaN(exp)) return '';
    const diff = exp - Date.now();
    if (diff <= 0) return 'Expired';
    if (diff < 3600000) return `expires in ${Math.ceil(diff / 60000)}m`;
    return `expires in ${Math.ceil(diff / 3600000)}h`;
}

window.loadQuestGrinder = function() {
    window.stopQuestPolling();
    fetchQuestStatus(true);
    _questPollInterval = setInterval(() => fetchQuestStatus(false), 3000);
};

window.stopQuestPolling = function() {
    if (_questPollInterval) {
        clearInterval(_questPollInterval);
        _questPollInterval = null;
    }
};

function fetchQuestStatus(showLoading) {
    const q = currentAccountId ? `?id=${currentAccountId}` : '';
    fetch(`/api/quests/status${q}`)
        .then(r => r.json())
        .then(data => {
            if (!data.success) {
                renderQuestError(data.error || 'Failed to load quest data');
                return;
            }
            renderQuestGrinder(data.status);
        })
        .catch(() => {
            if (showLoading) renderQuestError('Could not reach the quest API.');
        });
}

function renderQuestError(message) {
    const list = document.getElementById('quest-grinder-list');
    if (list) list.innerHTML = `<div class="no-data">${escapeHtml(message)}</div>`;
    const kpi = document.getElementById('quest-grinder-kpi');
    if (kpi) kpi.innerHTML = '';
}

function renderQuestGrinder(s) {
    const nameEl = document.getElementById('quest-grinder-account');
    if (nameEl) nameEl.textContent = s.account_name || 'Unknown';
    const readyEl = document.getElementById('quest-grinder-ready');
    if (readyEl) {
        readyEl.textContent = s.account_ready ? 'READY' : 'CONNECTING…';
        readyEl.className = 'quest-ready-badge ' + (s.account_ready ? 'on' : 'off');
    }

    // KPI cards
    const kpi = document.getElementById('quest-grinder-kpi');
    if (kpi) {
        const available = (s.quests || []).filter(q => q.status === 'available').length;
        const grinding = (s.quests || []).filter(q => ['enrolling', 'progressing', 'claiming'].includes(q.status)).length;
        const claimable = (s.quests || []).filter(q => q.status === 'claimable').length;
        kpi.innerHTML = `
            <div class="kpi-card quest-kpi">
                <div class="quest-kpi-icon orb">🟠</div>
                <div class="quest-kpi-data"><h3>Orbs Earned</h3><p>${s.orbs_earned || 0}</p><span>rewards: ${s.rewards_earned || 0}</span></div>
            </div>
            <div class="kpi-card quest-kpi">
                <div class="quest-kpi-icon">⚙️</div>
                <div class="quest-kpi-data"><h3>Grinding</h3><p>${grinding}</p><span>${s.running || 0} active task(s)</span></div>
            </div>
            <div class="kpi-card quest-kpi">
                <div class="quest-kpi-icon claim">✅</div>
                <div class="quest-kpi-data"><h3>Ready to Claim</h3><p>${claimable}</p><span>${available} available to start</span></div>
            </div>
            <div class="kpi-card quest-kpi">
                <div class="quest-kpi-icon ${s.auto_enabled ? 'auto-on' : ''}">🤖</div>
                <div class="quest-kpi-data"><h3>Auto Mode</h3><p>${s.auto_enabled ? 'ON' : 'OFF'}</p><span>last fetch: ${questTimeAgo(s.last_fetch)}</span></div>
            </div>
        `;
    }

    // Blocked banner
    const banner = document.getElementById('quest-grinder-blocked');
    if (banner) {
        if (s.enrollment_blocked_until) {
            banner.style.display = 'flex';
            banner.querySelector('#quest-blocked-text').textContent =
                `Quest enrollment is blocked until ${s.enrollment_blocked_until} — new quests can still be tracked but not auto-started.`;
        } else {
            banner.style.display = 'none';
        }
    }

    // Controls
    const startBtn = document.getElementById('quest-auto-start');
    const stopBtn = document.getElementById('quest-auto-stop');
    if (startBtn) startBtn.style.display = s.auto_enabled ? 'none' : '';
    if (stopBtn) stopBtn.style.display = s.auto_enabled ? '' : 'none';
    const statusChip = document.getElementById('quest-auto-status');
    if (statusChip) {
        statusChip.textContent = s.auto_enabled ? 'AUTO GRINDING' : 'MANUAL MODE';
        statusChip.className = 'quest-auto-chip ' + (s.auto_enabled ? 'on' : 'off');
    }

    // Quest cards
    const list = document.getElementById('quest-grinder-list');
    if (list) {
        const quests = s.quests || [];
        if (quests.length === 0) {
            list.innerHTML = `<div class="no-data">No quests available for this account. Hit <strong>Refresh</strong> to pull the latest quest list.</div>`;
        } else {
            list.innerHTML = quests.map(q => renderQuestCard(q)).join('');
        }
    }

    // Logs
    const logsEl = document.getElementById('quest-grinder-logs');
    if (logsEl) {
        const logs = s.logs || [];
        const json = JSON.stringify(logs.slice(0, 40));
        if (json !== _lastQuestJson) {
            _lastQuestJson = json;
            logsEl.innerHTML = logs.length === 0
                ? '<div class="no-data">No grinder activity yet.</div>'
                : logs.map(l => `
                    <div class="quest-log-line">
                        <span class="quest-log-time">[${escapeHtml(l.time)}]</span>
                        <span class="quest-log-level ${(l.level || 'INFO').toLowerCase()}">${escapeHtml(l.level || 'INFO')}</span>
                        <span class="quest-log-msg">${escapeHtml(l.message)}</span>
                    </div>`).join('');
        }
    }
}

function renderQuestCard(q) {
    const meta = QUEST_STATUS_META[q.status] || { label: q.status.toUpperCase(), color: '#888' };
    const percent = Math.min(100, Math.max(0, q.progress_percent || 0));
    const isVideo = q.task_type === 'WATCH_VIDEO' || q.task_type === 'WATCH_VIDEO_ON_MOBILE';
    const isPlay = q.task_type && q.task_type.startsWith('PLAY_ON');

    let progressLabel = '';
    if (q.target && (isVideo || isPlay || q.task_type === 'PLAY_ACTIVITY')) {
        progressLabel = `${questFormatDuration(q.current)} / ${questFormatDuration(q.target)}`;
    } else if (q.target) {
        progressLabel = `${q.current} / ${q.target}`;
    }

    let actions = '';
    if (q.status === 'claimable') {
        actions += `<button class="btn-control quest-btn claim" onclick="questClaim('${q.id}')">🎁 CLAIM REWARD</button>`;
    } else if (q.status === 'available') {
        actions += `<button class="btn-control quest-btn ${q.enrolled ? 'retry' : 'start'}" onclick="questRetry('${q.id}')">${q.enrolled ? '↻ RESUME' : '▶ START'}</button>`;
    } else if (['error', 'needs_captcha'].includes(q.status)) {
        actions += `<button class="btn-control quest-btn retry" onclick="questRetry('${q.id}')">↻ RETRY</button>`;
    }

    const orbBadge = (q.orb_quantity || 0) > 0
        ? `<span class="quest-orb-badge">🟠 +${q.orb_quantity} Orbs</span>`
        : (q.reward_name ? `<span class="quest-orb-badge reward">🎁 ${escapeHtml(q.reward_name)}</span>` : '');

    const taskLabel = {
        'WATCH_VIDEO': 'Watch a video', 'WATCH_VIDEO_ON_MOBILE': 'Watch on mobile',
        'PLAY_ON_DESKTOP': 'Play on desktop', 'PLAY_ON_XBOX': 'Play on Xbox',
        'PLAY_ON_PLAYSTATION': 'Play on PlayStation', 'PLAY_ACTIVITY': 'Play an activity',
        'STREAM_ON_DESKTOP': 'Stream on desktop', 'ACHIEVEMENT_IN_ACTIVITY': 'In-activity achievement',
        'UNKNOWN': 'Unknown task'
    }[q.task_type] || q.task_type;

    return `
        <div class="quest-card" data-status="${q.status}">
            <div class="quest-card-head">
                <div class="quest-card-title">
                    <span class="quest-game">${escapeHtml(q.game)}</span>
                    <span class="quest-name">${escapeHtml(q.name)}</span>
                </div>
                <span class="quest-status-chip" style="--chip:${meta.color};">${meta.label}</span>
            </div>
            ${orbBadge ? `<div class="quest-reward-row">${orbBadge}</div>` : ''}
            <div class="quest-task-row">
                <span>${taskLabel}</span>
                <span class="quest-expiry">${questExpiresIn(q.expires_at)}</span>
            </div>
            <div class="quest-progress">
                <div class="quest-progress-bar"><div class="quest-progress-fill" style="width:${percent}%; background:${meta.color}; box-shadow:0 0 10px ${meta.color}55;"></div></div>
                <div class="quest-progress-meta">
                    <span style="color:${meta.color}; font-weight:600;">${progressLabel || (q.completed ? 'Completed' : '—')}</span>
                    <span class="quest-percent">${Math.round(percent)}%</span>
                </div>
            </div>
            ${q.status_detail ? `<div class="quest-detail">${escapeHtml(q.status_detail)}</div>` : ''}
            ${actions ? `<div class="quest-card-actions">${actions}</div>` : ''}
        </div>
    `;
}

// ─── Actions ────────────────────────────────────────────

window.questToggleAuto = function(enabled, el) {
    if (enabled && !confirm('🤖 Start auto grinding?\n\nThis will auto-enroll and auto-progress every available quest for this account.\n\nRewards are still claimed manually (Claim button) to avoid captcha issues.\n\nContinue?')) return;
    fetch('/api/quests/auto', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: currentAccountId, enabled: !!enabled })
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast(data.message, enabled ? 'success' : 'info');
                fetchQuestStatus(true);
            } else {
                showToast(data.error || 'Failed to toggle auto mode', 'error');
            }
        })
        .catch(() => showToast('Error toggling auto mode', 'error'));
};

window.questRefresh = function(el) {
    if (el) { el.disabled = true; }
    fetch('/api/quests/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: currentAccountId })
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast('Quest list refreshed');
            } else {
                showToast(data.error || 'Refresh failed', 'error');
            }
            if (el) { el.disabled = false; }
            setTimeout(() => fetchQuestStatus(true), 1500);
        })
        .catch(() => {
            showToast('Error refreshing quests', 'error');
            if (el) { el.disabled = false; }
        });
};

function questClaim(questId) {
    if (!confirm('🎁 Claim this reward?\n\nDiscord may sometimes require a captcha — if it does, the quest will be flagged and you can retry later.')) return;
    fetch('/api/quests/claim', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: currentAccountId, quest_id: questId })
    })
        .then(r => r.json())
        .then(data => {
            showToast(data.message || (data.error || 'Claim attempted'), data.success ? 'success' : 'error');
            setTimeout(() => fetchQuestStatus(true), 1500);
        })
        .catch(() => showToast('Error claiming reward', 'error'));
}

function questRetry(questId) {
    fetch('/api/quests/retry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: currentAccountId, quest_id: questId })
    })
        .then(r => r.json())
        .then(data => {
            showToast(data.message || (data.error || 'Retry started'), data.success ? 'success' : 'error');
            setTimeout(() => fetchQuestStatus(true), 1000);
        })
        .catch(() => showToast('Error retrying quest', 'error'));
}
