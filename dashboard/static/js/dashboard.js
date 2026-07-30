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

function initDashCharts() {
    try {
        const c2 = document.getElementById('lineChart').getContext('2d');
        lineChart = new Chart(c2, {
            type: 'line',
            data: { labels: Array(30).fill(''), datasets: [{ data: Array(30).fill(0), borderColor: '#ff1f1f', backgroundColor: 'rgba(255,31,31,0.05)', fill: true, pointRadius: 2, pointHoverRadius: 5, tension: 0.3 }] },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: { x: { display: false }, y: { min: 0, suggestedMax: 10, grid: { color: '#222' }, ticks: { color: '#555', font: { size: 10 } } } },
                plugins: { legend: { display: false } }
            }
        });
    } catch (e) { console.warn("Dashboard charts blocked"); }
}

// ─── Auto Cash Check ──────────────────────────────────
let _lastCashCheckSent = 0;
let _cashCheckInterval = null;

function _sendCashCheck() {
    _lastCashCheckSent = Date.now() / 1000;
    const lastCheckEl = document.getElementById('cashLastCheck');
    if (lastCheckEl) lastCheckEl.innerHTML = 'Checking…';
    window.action('cash');
}

window.startAutoCashCheck = function() {
    if (_cashCheckInterval) clearInterval(_cashCheckInterval);
    // First check after 10s to let dashboard settle
    setTimeout(_sendCashCheck, 10000);
    _cashCheckInterval = setInterval(_sendCashCheck, 120000);
};

window.stopAutoCashCheck = function() {
    if (_cashCheckInterval) {
        clearInterval(_cashCheckInterval);
        _cashCheckInterval = null;
    }
};

function _updateCashCheckDisplay(lastCashUpdate) {
    const lastCheckEl = document.getElementById('cashLastCheck');
    if (!lastCheckEl) return;

    // If we just sent a check, show "Checking…" for up to 15s
    const elapsedSinceSend = (Date.now() / 1000) - _lastCashCheckSent;
    if (_lastCashCheckSent > 0 && elapsedSinceSend < 15) {
        lastCheckEl.innerHTML = 'Checking…';
        return;
    }

    if (!lastCashUpdate || lastCashUpdate === 0) {
        lastCheckEl.innerHTML = 'Not checked yet';
        return;
    }

    const now = Date.now() / 1000;
    const diff = now - lastCashUpdate;

    let display = '';
    if (diff < 5) {
        display = 'just now';
    } else if (diff < 60) {
        display = `${Math.round(diff)}s ago`;
    } else if (diff < 3600) {
        const mins = Math.floor(diff / 60);
        const secs = Math.round(diff % 60);
        display = `${mins}m ${secs}s ago`;
    } else {
        const hours = Math.floor(diff / 3600);
        display = `${hours}h ${Math.round((diff % 3600) / 60)}m ago`;
    }
    lastCheckEl.innerHTML = `Last checked: ${display}`;
}

    
function update() {
    const q = currentAccountId ? `?id=${currentAccountId}` : '';
    fetch(`/api/stats${q}`).then(r => r.json()).then(d => {
        console.log('Logs received:', d.logs);
        if (!d || Object.keys(d).length === 0) return;
        if (d.bot) {
            console.log(`[Stats Update] ${d.bot.username} (#${d.bot.user_id}): ${Object.keys(d.cmd_states || {}).length} commands in scheduler.`);
            const nameEl = document.getElementById('currentAccountName');
            if (nameEl) nameEl.innerText = `ACCOUNT: ${d.bot.username}`;
        }
        if (d.cash) document.getElementById('cash').innerText = d.cash.toLocaleString();
        if (d.uptime) document.getElementById('uptimeDisplay').innerText = d.uptime;
        if (d.logs) renderLogs(d.logs);
        const dot = document.getElementById('statusDot'), lbl = document.getElementById('botStatus');
        lbl.innerText = d.status; dot.className = "ping-dot " + (d.status === "PAUSED" ? "paused" : "");
        if (d.status === "PAUSED" && d.security && d.security.last_message) {
            document.getElementById('securityAlert').style.display = 'flex';
            document.getElementById('captchaMsg').innerText = d.security.last_message;

            const section = document.getElementById('captcha-solver-section');
            if (section && section.style.display !== 'block') {
                const acc = accountsList.find(a => a.id === currentAccountId);
                if (acc) openEmbeddedCaptcha(currentAccountId, acc.username);
            }
        } else {
            document.getElementById('securityAlert').style.display = 'none';
        }
        if (d.chart_data) {
            document.getElementById('huntsToday').innerHTML = `${d.chart_data.hunt} <span style="font-size:0.5em; color:var(--success);" id="huntsSession">(${d.chart_data.session_hunt} this session)</span>`;
            document.getElementById('battlesToday').innerHTML = `${d.chart_data.battle} <span style="font-size:0.5em; color:#3b82f6;" id="battlesSession">(${d.chart_data.session_battle} this session)</span>`;
            document.getElementById('cpm').innerText = d.chart_data.perf_bpm;
            if (document.getElementById('totalOwO')) document.getElementById('totalOwO').innerHTML = `${d.chart_data.owo} <span style="font-size:0.5em; color:#a855f7;" id="owoSession">(${d.chart_data.session_owo} this session)</span>`;
        }
        if (d.security) {
            const sc = document.getElementById('sec-captchas'); if (sc) sc.innerText = d.security.captchas;
            const sb = document.getElementById('sec-bans'); if (sb) sb.innerText = d.security.bans;
            const sw = document.getElementById('sec-warns'); if (sw) sw.innerText = d.security.warnings;
        }
        // Update last cash check display
        if (d.system && d.system.last_cash_update !== undefined) {
            _updateCashCheckDisplay(d.system.last_cash_update);
        }
        if (lineChart && d.chart_data) {
            lineChart.data.datasets[0].data.push(d.chart_data.perf_bpm);
            lineChart.data.datasets[0].data.shift();
            lineChart.update('none');
        }
        try { renderQuests(d.quest_data, d.next_quest_timer); } catch(e) { console.error("Quest Render Error:", e); }
        try { if (d.cmd_states) renderScheduler(d.cmd_states); } catch(e) { console.error("Scheduler Render Error in update():\n", e); }
        try { fetchSecuritySummary(); } catch(e) { console.error("Security Summary Error:\n", e); }
    });
}


function renderScheduler(states) {
    const list = document.getElementById('schedulerList');
    if (!list) return;
    try {
        const now = Date.now() / 1000;
        const items = Object.entries(states || {}).map(([id, s]) => {
            try {
                const lastRan = s.last_ran || 0;
                const delay = s.delay || 1;
                const nextRun = lastRan + delay;
                const remaining = Math.max(0, nextRun - now);
                return { id, priority: s.priority || 3, delay: delay, in_queue: !!s.in_queue, remaining };
            } catch(e) {
                return null;
            }
        }).filter(item => item !== null);
        items.sort((a, b) => (a.remaining || 0) - (b.remaining || 0));
        if (items.length === 0) {
            list.innerHTML = '<div style="color:#666; font-style:italic; font-size:0.9rem; text-align:center; padding-top:20px;">No scheduled actions</div>';
            return;
        }
        list.innerHTML = items.map(item => {
            const name = item.id.toUpperCase();
            let statusHtml = '';
            let progress = 0;
            if (item.in_queue) {
                statusHtml = `<span style="color:var(--success); font-size:0.8rem; font-weight:bold;"><span class="icon-svg" style="--icon: url('/static/assets/limey_icons/sync.svg'); animation: spin 2s linear infinite;"></span> QUEUED</span>`;
                progress = 100;
            } else {
                const displayTime = Math.ceil(item.remaining);
                const timeStr = displayTime > 60 ? `${Math.floor(displayTime / 60)}m ${displayTime % 60}s` : `${displayTime}s`;
                statusHtml = `<span style="color:#aaa; font-family:var(--font-mono); font-size:0.8rem;">in ${timeStr}</span>`;
                progress = Math.min(100, Math.max(0, 100 - (item.remaining / item.delay) * 100));
            }
            const pColor = item.priority <= 2 ? 'var(--primary)' : '#888';
            return `
                <div style="background:rgba(0,0,0,0.2); border:1px solid rgba(255,255,255,0.05); border-radius:6px; padding:8px 12px; display:flex; flex-direction:column; gap:6px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div style="display:flex; align-items:center; gap:8px;">
                            <span style="width:8px; height:8px; border-radius:50%; background:${pColor}; display:inline-block; box-shadow:0 0 5px ${pColor};"></span>
                            <span style="color:#ddd; font-weight:600; font-size:0.85rem;">${name}</span>
                        </div>
                        ${statusHtml}
                    </div>
                    <div style="height:3px; background:rgba(255,255,255,0.05); border-radius:2px; overflow:hidden;">
                        <div style="height:100%; width:${progress}%; background:${item.in_queue ? 'var(--success)' : '#444'}; transition:width 1s linear;"></div>
                    </div>
                </div>
            `;
        }).join('');
    } catch (e) {
        console.error("Scheduler Render Error:\n", e);
        list.innerHTML = '<div style="color:#666; font-style:italic; font-size:0.9rem; text-align:center; padding-top:20px;">Render Error (Check Console)</div>';
    }
}

function renderQuests(quests, timer) {
    const list = document.getElementById('questList');
    const timerEl = document.getElementById('nextQuestTimer');
    if (!list || !timerEl) return;
    if (timer) {
        timerEl.innerHTML = `<span class="icon-svg" style="--icon: url('/static/assets/limey_icons/clock.svg'); width: 14px; height: 14px;"></span> Next quest in: ${timer}`;
        timerEl.style.display = 'block';
    } else {
        timerEl.style.display = 'none';
    }
    if (!quests || quests.length === 0) {
        list.innerHTML = '<div style="color:#666; font-style:italic; text-align:center; padding: 20px;">No active quests tracked.<br><span style="font-size:0.8rem; opacity:0.7;">Run "o quest" to sync with OwO.</span></div>';
        return;
    }
    list.innerHTML = quests.map(q => {
        const percent = Math.min(100, Math.round((q.current / q.total) * 100));
        const isCompleted = q.completed;
        const color = isCompleted ? 'var(--success)' : 'var(--primary)';
        let status = "Auto-solving";
        if (isCompleted) status = "Completed";
        else {
            const desc = q.description.toLowerCase();
            const socialQuests = ["friend", "pray to you", "curse you", "cookie from", "action command on you", "emote command on you"];
            if (socialQuests.some(s => desc.includes(s))) {
                status = "Alt Coordinated";
            } else if (desc.includes("hunt 3 animals")) {
                status = "Gem Optimized";
            } else if (desc.includes("gamble")) {
                status = "Auto Gambling";
            }
        }
        return `
            <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.05); padding:15px; border-radius:8px;">
                <div style="display:flex; justify-content:space-between; margin-bottom:5px; font-size:0.9rem;">
                    <span style="color:#eee; font-weight:500;">${q.description}</span>
                    <span style="color:${color}; font-weight:bold;">${q.current}/${q.total}</span>
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                    <span style="font-size:0.65rem; color:${isCompleted ? 'var(--success)' : '#888'}; text-transform:uppercase; letter-spacing:0.8px; font-family:var(--font-mono);">${status}</span>
                </div>
                <div style="height:6px; background:rgba(255,255,255,0.05); border-radius:3px; overflow:hidden;">
                    <div style="width:${percent}%; height:100%; background:${color}; box-shadow: 0 0 10px ${color}44; transition: width 0.5s ease;"></div>
                </div>
            </div>
        `;
    }).join('');
}

function renderLogs(logs) {
    const t = document.getElementById('term');
    if (!t) return;
    const currentHash = logs.slice(0, 5).map(l => l.timestamp).join('|');
    if (currentHash === lastLogsHash) return;
    lastLogsHash = currentHash;
    t.innerHTML = logs.map(l => {
        const tagClass = l.type ? `tag-${l.type.toLowerCase()}` : '';
        const localTime = l.timestamp ? timeFormatter.format(new Date(l.timestamp * 1000)) : l.time;
        const botTag = l.bot_name ? `<span style="color:magenta; margin-right:5px;">[${l.bot_name}]</span>` : '';
        return `<div class="history-item ${l.type ? l.type.toLowerCase() : ''}">${botTag}<span class="history-time">[${localTime}]</span> <span class="history-tag ${tagClass}">${l.type}</span> <span class="history-msg">${l.message}</span></div>`;
    }).join('');
}


window.resumeBot = function() { 
    console.log("Resuming bot...");
    fetch('/api/security', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'resume', id: currentAccountId })
    }).then(() => {
        document.getElementById('securityAlert').style.display = 'none';
        update();
    });
};

window.action = function(a, el) {
    console.log(`Executing action: ${a}`);
    fetch('/api/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: a, id: currentAccountId })
    }).then(() => update());
};

window.startAllBots = function() {
    if (!confirm('▶️ START ALL BOTS\n\nThis will resume all paused bots.\n\nContinue?')) return;
    
    const btn = document.querySelector('[onclick*="startAllBots"]');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="icon-svg" style="--icon: url(\'/static/assets/limey_icons/sync.svg\'); animation: spin 1s linear infinite;"></span> <span class="btn-text">STARTING ALL...</span>'; }
    
    showToast('▶️ Starting all bots...', 'info');
    
    fetch('/api/control/all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'start' })
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast(`✅ Started ${data.success_count}/${data.total} bots`, 'success');
                if (btn) { btn.disabled = false; btn.innerHTML = '<span class="icon-svg" style="--icon: url(\'/static/assets/limey_icons/play-all.svg\');"></span> <span class="btn-text">START ALL</span>'; }
                update();
                if (typeof fetchAccounts === 'function') fetchAccounts();
            } else {
                showToast('❌ ' + (data.error || 'Failed to start bots'), 'error');
                if (btn) { btn.disabled = false; btn.innerHTML = '<span class="icon-svg" style="--icon: url(\'/static/assets/limey_icons/play-all.svg\');"></span> <span class="btn-text">START ALL</span>'; }
            }
        })
        .catch(() => {
            showToast('❌ Error starting bots', 'error');
            if (btn) { btn.disabled = false; btn.innerHTML = '<span class="icon-svg" style="--icon: url(\'/static/assets/limey_icons/play-all.svg\');"></span> <span class="btn-text">START ALL</span>'; }
        });
};

window.stopAllBots = function() {
    if (!confirm('⏹️ STOP ALL BOTS\n\nThis will pause all running bots.\n\nContinue?')) return;
    
    const btn = document.querySelector('[onclick*="stopAllBots"]');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="icon-svg" style="--icon: url(\'/static/assets/limey_icons/sync.svg\'); animation: spin 1s linear infinite;"></span> <span class="btn-text">STOPPING ALL...</span>'; }
    
    showToast('⏹️ Stopping all bots...', 'warning');
    
    fetch('/api/control/all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'stop' })
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast(`⏹️ Stopped ${data.success_count}/${data.total} bots`, 'warning');
                if (btn) { btn.disabled = false; btn.innerHTML = '<span class="icon-svg" style="--icon: url(\'/static/assets/limey_icons/stop-all.svg\');"></span> <span class="btn-text">STOP ALL</span>'; }
                update();
                if (typeof fetchAccounts === 'function') fetchAccounts();
            } else {
                showToast('❌ ' + (data.error || 'Failed to stop bots'), 'error');
                if (btn) { btn.disabled = false; btn.innerHTML = '<span class="icon-svg" style="--icon: url(\'/static/assets/limey_icons/stop-all.svg\');"></span> <span class="btn-text">STOP ALL</span>'; }
            }
        })
        .catch(() => {
            showToast('❌ Error stopping bots', 'error');
            if (btn) { btn.disabled = false; btn.innerHTML = '<span class="icon-svg" style="--icon: url(\'/static/assets/limey_icons/stop-all.svg\');"></span> <span class="btn-text">STOP ALL</span>'; }
        });
};

// ─── Console / Terminal Controls ────────────────────

function fetchSystemStatus() {
    fetch('/api/system/status')
        .then(r => r.json())
        .then(data => {
            if (data.success && data.system) {
                const s = data.system;
                document.getElementById('sys-uptime').textContent = s.uptime || '--';
                document.getElementById('sys-bots').textContent = s.bot_count || 0;
                document.getElementById('sys-active').textContent = s.active_count || 0;
                document.getElementById('sys-python').textContent = s.python_version || '--';
                document.getElementById('sys-pid').textContent = s.pid || '--';
                document.getElementById('sys-platform').textContent = (s.platform || '--').replace('darwin', 'macOS').replace('win32', 'Windows').replace('linux', 'Linux');
                
                if (s.memory && s.memory.available) {
                    document.getElementById('sys-memory').textContent = (s.memory.rss_mb || '--') + ' MB';
                    document.getElementById('sys-cpu').textContent = (s.memory.cpu || '--') + '%';
                } else {
                    document.getElementById('sys-memory').textContent = 'N/A';
                    document.getElementById('sys-cpu').textContent = 'N/A';
                }
            }
        })
        .catch(() => {});
}

function clearTerminalLogs() {
    if (!confirm('Clear all log entries from the terminal display?')) return;
    
    fetch('/api/system/logs/clear', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                lastLogsHash = '';
                document.getElementById('term').innerHTML = '<div class="history-item sys" style="color:#888; font-style:italic;">[SYSTEM] Logs cleared.</div>';
                showToast('Logs cleared');
            } else {
                showToast(data.error || 'Failed to clear logs', 'error');
            }
        })
        .catch(() => showToast('Error clearing logs', 'error'));
}

function systemRestart() {
    if (!confirm('⚠️ RESTART SYSTEM\n\nThis will stop all bots, save state, and restart the entire process.\n\nContinue?')) return;
    
    const btn = document.querySelector('.btn-control.green[onclick*="systemRestart"]');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="icon-svg" style="--icon: url(\'/static/assets/limey_icons/sync.svg\'); animation: spin 1s linear infinite;"></span> <span class="btn-text">RESTARTING...</span>'; }
    
    showToast('🔄 System restarting...', 'info');
    
    fetch('/api/system/restart', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast('✅ ' + data.message, 'success');
                // The page will reload when the system comes back up
                setTimeout(() => {
                    const checkInterval = setInterval(() => {
                        fetch('/api/system/status')
                            .then(() => {
                                clearInterval(checkInterval);
                                window.location.reload();
                            })
                            .catch(() => {});
                    }, 3000);
                }, 5000);
            } else {
                showToast(data.error || 'Restart failed', 'error');
                if (btn) { btn.disabled = false; btn.innerHTML = '<span class="icon-svg" style="--icon: url(\'/static/assets/limey_icons/sync.svg\');"></span> <span class="btn-text">RESTART</span>'; }
            }
        })
        .catch(() => {
            showToast('Restart command sent. Waiting for system...', 'info');
            setTimeout(() => {
                const checkInterval = setInterval(() => {
                    fetch('/api/system/status')
                        .then(() => {
                            clearInterval(checkInterval);
                            window.location.reload();
                        })
                        .catch(() => {});
                }, 3000);
            }, 5000);
        });
}

function systemShutdown() {
    if (!confirm('⚠️ SHUTDOWN SYSTEM\n\nThis will stop all bots, save state, and shut down the entire process.\n\nThe dashboard will become unavailable until manually restarted.\n\nContinue?')) return;
    
    const btn = document.querySelector('.btn-control.red[onclick*="systemShutdown"]');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="icon-svg" style="--icon: url(\'/static/assets/limey_icons/stop.svg\');"></span> <span class="btn-text">SHUTTING DOWN...</span>'; }
    
    showToast('🛑 System shutting down...', 'warning');
    
    fetch('/api/system/shutdown', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast('✅ ' + data.message, 'success');
                document.getElementById('term').innerHTML = '<div class="history-item" style="color:#ff4444; font-weight:bold; text-align:center; padding:40px 20px;">' +
                    '<div style="font-size:2rem; margin-bottom:12px;">🛑</div>' +
                    '<div>System shut down successfully.</div>' +
                    '<div style="color:#888; font-size:0.8rem; margin-top:8px;">The dashboard is no longer available.</div>' +
                    '<div style="color:#888; font-size:0.8rem;">Restart the bot manually to bring it back.</div>' +
                '</div>';
            } else {
                showToast(data.error || 'Shutdown failed', 'error');
                if (btn) { btn.disabled = false; btn.innerHTML = '<span class="icon-svg" style="--icon: url(\'/static/assets/limey_icons/stop.svg\');"></span> <span class="btn-text">SHUTDOWN</span>'; }
            }
        })
        .catch(() => showToast('Shutdown command sent', 'info'));
}

// ─── Ticket System Dashboard ────────────────────────

let _currentTicketStatusFilter = 'open';
let _ticketConfigOriginal = null;

function switchTicketTab(tab, el) {
    document.querySelectorAll('#tickets .mod-filter[data-tab]').forEach(b => b.classList.remove('active'));
    if (el) el.classList.add('active');
    
    document.getElementById('ticket-tickets-section').style.display = tab === 'tickets' ? '' : 'none';
    document.getElementById('ticket-config-section').style.display = tab === 'config' ? '' : 'none';
    
    if (tab === 'config') {
        loadTicketConfig();
    } else if (tab === 'tickets') {
        fetchTickets();
    }
}

function loadTicketConfig() {
    fetch('/api/tickets/config')
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                const cfg = data.config || {};
                document.getElementById('ticket-cfg-role').value = cfg.staff_role_id || '';
                document.getElementById('ticket-cfg-log').value = cfg.log_channel_id || '';
                _ticketConfigOriginal = JSON.stringify({ staff_role_id: cfg.staff_role_id || '', log_channel_id: cfg.log_channel_id || '' });
                document.getElementById('ticket-cfg-status').textContent = 'Configuration loaded';
                document.getElementById('ticket-cfg-status').style.color = '#888';
                
                // Show ticket types info
                renderTicketTypeInfo(cfg);
            }
        })
        .catch(() => {
            document.getElementById('ticket-cfg-status').textContent = 'Failed to load config';
            document.getElementById('ticket-cfg-status').style.color = '#ff4444';
        });
}

function renderTicketTypeInfo(cfg) {
    const container = document.getElementById('ticket-cfg-categories');
    const typeEmojis = { 'support': '❓', 'report': '🚩', 'appeal': '⚖️', 'question': '💡', 'other': '📝' };
    const categories = cfg.categories || {};
    
    let html = '';
    Object.entries(typeEmojis).forEach(([type, emoji]) => {
        const catData = categories[type] || {};
        const catId = catData.id;
        html += '<div class="account-row" style="padding: 10px 16px;">' +
            '<div style="display:flex;align-items:center;gap:12px;flex:1;">' +
            '<span style="font-size:1.2rem;">' + emoji + '</span>' +
            '<div>' +
            '<div style="font-weight:600;">' + type.charAt(0).toUpperCase() + type.slice(1) + '</div>' +
            '<div style="font-size:0.75rem;color:#666;">' +
            (catId ? 'Category ID: <code style="color:#888;">' + catId + '</code>' : '⏳ Not yet created (auto-created on first ticket)') +
            '</div>' +
            '</div>' +
            '</div>' +
            '</div>';
    });
    container.innerHTML = html;
}

function markTicketConfigDirty() {
    const current = JSON.stringify({
        staff_role_id: document.getElementById('ticket-cfg-role').value.trim(),
        log_channel_id: document.getElementById('ticket-cfg-log').value.trim(),
    });
    const statusEl = document.getElementById('ticket-cfg-status');
    if (current !== _ticketConfigOriginal) {
        statusEl.textContent = '⚠️ Unsaved changes';
        statusEl.style.color = '#ffaa00';
    } else {
        statusEl.textContent = 'No changes';
        statusEl.style.color = '#888';
    }
}

function saveTicketConfig() {
    const staffRoleId = document.getElementById('ticket-cfg-role').value.trim();
    const logChannelId = document.getElementById('ticket-cfg-log').value.trim();
    
    const statusEl = document.getElementById('ticket-cfg-status');
    statusEl.textContent = 'Saving...';
    statusEl.style.color = '#888';
    
    fetch('/api/tickets/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            config: {
                staff_role_id: staffRoleId,
                log_channel_id: logChannelId,
            }
        })
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                _ticketConfigOriginal = JSON.stringify({ staff_role_id: staffRoleId, log_channel_id: logChannelId });
                statusEl.textContent = '✅ Configuration saved!';
                statusEl.style.color = '#00ff88';
                showToast('Ticket configuration saved');
                
                // Update categories display
                renderTicketTypeInfo(data.config || {});
            } else {
                statusEl.textContent = '❌ ' + (data.error || 'Save failed');
                statusEl.style.color = '#ff4444';
                showToast(data.error || 'Failed to save', 'error');
            }
        })
        .catch(() => {
            statusEl.textContent = '❌ Failed to save';
            statusEl.style.color = '#ff4444';
            showToast('Error saving config', 'error');
        });
}

function filterTickets(status, el) {
    _currentTicketStatusFilter = status;
    document.querySelectorAll('#tickets .mod-filter').forEach(b => b.classList.remove('active'));
    if (el) el.classList.add('active');
    fetchTickets();
}

function fetchTickets() {
    const status = _currentTicketStatusFilter || 'open';
    fetch('/api/tickets/list?status=' + status + '&limit=100')
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                renderTicketList(data.tickets);
            }
        })
        .catch(() => {});

    // Also fetch stats
    fetch('/api/tickets/stats')
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                document.getElementById('ticket-stat-open').textContent = data.stats.total_open || 0;
                document.getElementById('ticket-stat-closed').textContent = data.stats.total_closed || 0;
                document.getElementById('ticket-stat-total').textContent = data.stats.total_all || 0;
                document.getElementById('ticket-stat-types').textContent = Object.keys(data.stats.type_breakdown || {}).length;
            }
        })
        .catch(() => {});
}

function renderTicketList(tickets) {
    const list = document.getElementById('tickets-list');
    if (!tickets || tickets.length === 0) {
        list.innerHTML = '<div class="no-data">No tickets found.</div>';
        return;
    }

    const typeEmojis = {
        'support': '❓',
        'report': '🚩',
        'appeal': '⚖️',
        'question': '💡',
        'other': '📝',
    };

    const statusColors = {
        'open': '#44ff88',
        'closed': '#ff4444',
        'orphaned': '#888',
    };

    let html = '';
    tickets.forEach(t => {
        const typeEmoji = typeEmojis[t.type] || '🎫';
        const statusColor = statusColors[t.status] || '#888';
        const createdDate = t.created_at ? new Date(t.created_at * 1000).toLocaleString() : 'Unknown';
        const closedDate = t.closed_at ? new Date(t.closed_at * 1000).toLocaleString() : '—';

        html += '<div class="account-row">' +
            '<div style="display:flex;flex-direction:column;gap:4px;flex:1;min-width:0;">' +
            '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">' +
            '<span style="font-weight:600;">' + typeEmoji + ' #' + t.ticket_num + '</span>' +
            '<span style="font-size:0.75rem;padding:2px 8px;border-radius:4px;background:' + statusColor + '22;color:' + statusColor + ';border:1px solid ' + statusColor + '44;">' + (t.status || 'unknown').toUpperCase() + '</span>' +
            '<span style="font-size:0.75rem;color:#888;">' + escapeHtml(t.type || 'unknown') + '</span>' +
            '</div>' +
            '<div style="font-size:0.85rem;color:#ccc;">' + escapeHtml(t.subject || 'No subject') + '</div>' +
            '<div style="font-size:0.75rem;color:#666;">' +
            'By: ' + escapeHtml(t.username || 'Unknown') + ' &middot; ' +
            'Created: ' + createdDate + ' &middot; ' +
            'Channel: <code style="color:#888;font-size:0.7rem;">' + (t.channel_id ? t.channel_id.substring(0, 8) + '...' : 'N/A') + '</code>' +
            '</div>' +
            '</div>' +
            '</div>';
    });

    list.innerHTML = html;
}

// ─── Override nav for Terminal tab to fetch system status ──
const _origNavForTerminal = window.nav;
if (typeof _origNavForTerminal === 'function') {
    const _patchedNavForTerminal = function(id, el) {
        _origNavForTerminal.call(window, id, el);
        if (id === 'logs') {
            fetchSystemStatus();
        }
    };
    window.nav = _patchedNavForTerminal;
}