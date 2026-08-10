/* 
 * Moderation Panel - Dashboard
 * Displays violations, mod log, and config for the moderation system.
 */

let _modUsersData = [];
let _modCurrentTab = 'users';

// ─── Load Summary ─────────────────────────────────────

function loadModerationSummary() {
    fetch('/api/moderation/summary')
        .then(r => r.json())
        .then(data => {
            if (data.success && data.summary) {
                const s = data.summary;
                document.getElementById('mod-stat-violations').textContent = s.total_violations.toLocaleString();
                document.getElementById('mod-stat-users').textContent = s.users_with_violations;
                document.getElementById('mod-stat-actions').textContent = s.total_mod_actions.toLocaleString();
                document.getElementById('mod-stat-mutes').textContent = s.active_mutes;
            }
        })
        .catch(() => {});
}

// ─── Tab Switching ─────────────────────────────────────

function switchModTab(tab, el) {
    _modCurrentTab = tab;
    document.querySelectorAll('.mod-filter').forEach(b => b.classList.remove('active'));
    if (el) el.classList.add('active');
    
    document.querySelectorAll('.mod-section').forEach(s => s.style.display = 'none');
    
    const sections = {
        'users': 'mod-users-section',
        'modlog': 'mod-modlog-section',
        'config': 'mod-config-section'
    };
    
    const target = document.getElementById(sections[tab]);
    if (target) {
        target.style.display = '';
        target.style.animation = 'none';
        setTimeout(() => target.style.animation = '', 10);
    }
    
    if (tab === 'users') loadModUsers();
    else if (tab === 'modlog') fetchModLog();
    else if (tab === 'config') loadModConfig();
}

// ─── Users List ────────────────────────────────────────

function loadModUsers() {
    const list = document.getElementById('mod-users-list');
    list.innerHTML = '<div class="no-data">Loading users...</div>';
    
    fetch('/api/moderation/users')
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                _modUsersData = data.users || [];
                renderModUsers(_modUsersData);
                loadModerationSummary();
            } else {
                list.innerHTML = '<div class="no-data">❌ Failed to load moderation data</div>';
            }
        })
        .catch(() => {
            list.innerHTML = '<div class="no-data">❌ Failed to load moderation data</div>';
        });
}

function renderModUsers(users) {
    const list = document.getElementById('mod-users-list');
    
    if (!users || users.length === 0) {
        list.innerHTML = '<div class="no-data">✅ No users with violations found. Clean server!</div>';
        return;
    }
    
    let html = '';
    users.forEach(u => {
        const uid = u.user_id || '?';
        const totalV = u.total_violations || 0;
        const guildCount = Object.keys(u.guilds || {}).length;
        const lastType = u.last_type || 'unknown';
        const lastReason = u.last_reason || '';
        const lastTime = u.last_violation ? new Date(u.last_violation * 1000).toLocaleDateString() : 'Unknown';
        
        const typeColors = {
            'warn': 'type-warn', 'kick': 'type-kick', 'ban': 'type-ban',
            'timeout': 'type-timeout', 'mute': 'type-mute'
        };
        const typeClass = typeColors[lastType] || '';
        const typeEmoji = { 'warn': '⚠️', 'kick': '👢', 'ban': '🔨', 'timeout': '🔇', 'mute': '🔇' };
        const emoji = typeEmoji[lastType] || '📋';
        
        html += '<div class="mod-user-card" onclick="openModUserDetail(\'' + uid + '\')">' +
            '<div class="mod-user-card-header">' +
                '<div class="mod-user-info">' +
                    '<div class="mod-user-avatar">' +
                        '<span>' + emoji + '</span>' +
                    '</div>' +
                    '<div style="min-width:0;">' +
                        '<div class="mod-user-name">' + escapeHtml(uid) + '</div>' +
                        '<div class="mod-user-id">ID: ' + escapeHtml(uid) + ' · ' + guildCount + ' guild(s)</div>' +
                    '</div>' +
                '</div>' +
                '<div class="mod-user-stats">' +
                    '<div class="mod-stat-badge violations">' +
                        '<span class="stat-num">' + totalV + '</span>' +
                        '<span class="stat-label">Violations</span>' +
                    '</div>' +
                    '<div class="mod-stat-badge last-action">' +
                        '<span class="stat-num">' + lastTime + '</span>' +
                        '<span class="stat-label">Last Action</span>' +
                    '</div>' +
                    '<span class="mod-user-type ' + typeClass + '">' + emoji + ' ' + lastType.toUpperCase() + '</span>' +
                '</div>' +
            '</div>' +
        '</div>';
    });
    
    list.innerHTML = html;
}

function filterModUsers(query) {
    if (!query.trim()) {
        renderModUsers(_modUsersData);
        return;
    }
    const q = query.toLowerCase().trim();
    const filtered = _modUsersData.filter(u => 
        (u.user_id && u.user_id.toLowerCase().includes(q)) ||
        (u.last_type && u.last_type.toLowerCase().includes(q)) ||
        (u.last_reason && u.last_reason.toLowerCase().includes(q))
    );
    renderModUsers(filtered);
}

// ─── User Detail Modal ─────────────────────────────────

function openModUserDetail(userId) {
    const content = document.getElementById('mod-user-detail-content');
    const modal = document.getElementById('mod-user-detail-modal');
    
    content.innerHTML = '<div class="no-data">Loading user details...</div>';
    modal.style.display = 'flex';
    
    // Fetch violations
    fetch('/api/moderation/violations/' + userId)
        .then(r => r.json())
        .then((violationsData) => {
        const violations = violationsData.success ? violationsData.violations : [];
        
        const totalV = violations.length;
        
        // Find user summary data
        const userSum = _modUsersData.find(u => u.user_id === userId) || {};
        const guildCount = Object.keys(userSum.guilds || {}).length;
        
        let html = '';
        
        // Header
        html += '<div class="mod-detail-header">' +
            '<div class="mod-detail-avatar"><span>👤</span></div>' +
            '<div class="mod-detail-info">' +
                '<h3>User Details</h3>' +
                '<div class="user-id-small">ID: ' + escapeHtml(userId) + ' · ' + guildCount + ' guild(s)</div>' +
            '</div>' +
        '</div>';
        
        // Stats
        html += '<div class="mod-stats-bar" style="margin-bottom:12px;">' +
            '<div class="proxy-stat-card"><span>' + totalV + '</span><label>Violations</label></div>' +
        '</div>';
        
        // Violations content
        html += '<div id="mod-detail-violations" class="mod-detail-section">';
        if (violations.length === 0) {
            html += '<div class="mod-empty">No violations on record.</div>';
        } else {
            violations.forEach(v => {
                const ts = v.timestamp ? new Date(v.timestamp * 1000).toLocaleString() : 'Unknown';
                const vtype = (v.type || 'unknown').toUpperCase();
                const reason = v.reason || 'No reason provided';
                const mod = v.moderator || 'Unknown';
                const dur = v.duration ? ' · Duration: ' + v.duration : '';
                const guildId = v.guild_id || '?';
                const vid = v.id || '?';
                
                const typeEmoji = { 'WARN': '⚠️', 'KICK': '👢', 'BAN': '🔨', 'TIMEOUT': '🔇', 'MUTE': '🔇' };
                const emoji = typeEmoji[vtype] || '📋';
                
                html += '<div class="mod-violation-item">' +
                    '<span class="mod-violation-icon">' + emoji + '</span>' +
                    '<div class="mod-violation-body">' +
                        '<div class="mod-violation-type">#' + vid + ' ' + vtype + '</div>' +
                        '<div class="mod-violation-reason">' + escapeHtml(reason) + '</div>' +
                        '<div class="mod-violation-meta">' + ts + ' · Mod: ' + escapeHtml(mod) + ' · Guild: ' + escapeHtml(guildId) + dur + '</div>' +
                    '</div>' +
                    '<div class="mod-violation-actions">' +
                        '<button class="btn-control red" onclick="event.stopPropagation(); clearSingleViolation(\'' + escapeHtml(userId) + '\', \'' + escapeHtml(guildId) + '\', ' + vid + ')" style="padding:3px 8px;font-size:0.65rem;">🗑️</button>' +
                    '</div>' +
                '</div>';
            });
        }
        html += '</div>';
        
        // Clear all button
        html += '<div class="mod-bulk-clear">' +
            '<button class="btn-control red" onclick="clearAllViolations(\'' + escapeHtml(userId) + '\')">🗑️ Clear All Violations</button>' +
            '<span style="font-size:0.72rem;color:#666;">This clears violations across all guilds.</span>' +
        '</div>';
        
        // ── Mod Action Buttons ────────────────────────
        html += '<div class="mod-action-bar">' +
            '<div class="mod-action-bar-title">🛡️ Perform Moderation Action</div>' +
            '<div class="mod-action-buttons">' +
                '<button class="btn-control gold" onclick="showModActionForm(\'' + escapeHtml(userId) + '\', \'warn\')">⚠️ Warn</button>' +
                '<button class="btn-control" style="background:#ff8800;border-color:#ff8800;color:#fff;" onclick="showModActionForm(\'' + escapeHtml(userId) + '\', \'kick\')">👢 Kick</button>' +
                '<button class="btn-control red" onclick="showModActionForm(\'' + escapeHtml(userId) + '\', \'ban\')">🔨 Ban</button>' +
                '<button class="btn-control" style="background:#ff8800;border-color:#ff8800;color:#fff;" onclick="showModActionForm(\'' + escapeHtml(userId) + '\', \'timeout\')">🔇 Timeout</button>' +
                '<button class="btn-control" style="background:#ff8800;border-color:#ff8800;color:#fff;" onclick="showModActionForm(\'' + escapeHtml(userId) + '\', \'mute\')">🔇 Mute</button>' +
                '<button class="btn-control green" onclick="showModActionForm(\'' + escapeHtml(userId) + '\', \'unmute\')">🔊 Unmute</button>' +
                '<button class="btn-control green" onclick="showModActionForm(\'' + escapeHtml(userId) + '\', \'unban\')">🔓 Unban</button>' +
            '</div>' +
        '</div>';
        
        // ── Action Form (hidden) ──────────────────────
        html += '<div id="mod-action-form-container" class="mod-action-form-container" style="display:none;">' +
            '<div class="mod-action-form">' +
                '<div class="mod-action-form-header">' +
                    '<span id="mod-action-form-title">⚠️ Warn User</span>' +
                    '<button class="btn-control" onclick="hideModActionForm()" style="padding:2px 8px;font-size:0.7rem;">✕</button>' +
                '</div>' +
                '<div class="mod-action-form-body">' +
                    '<div style="display:flex;gap:10px;flex-wrap:wrap;">' +
                        '<div style="flex:1;min-width:200px;">' +
                            '<label class="form-label">Guild ID</label>' +
                            '<select id="mod-action-guild" class="cfg-input" style="font-size:0.75rem;">' +
                                getGuildOptions(userId) +
                            '</select>' +
                        '</div>' +
                        '<div id="mod-action-duration-group" style="flex:0 0 140px;display:none;">' +
                            '<label class="form-label">Duration</label>' +
                            '<input type="text" id="mod-action-duration" class="cfg-input" placeholder="e.g. 10m, 1h, 7d" style="font-size:0.75rem;">' +
                        '</div>' +
                        '<div id="mod-action-days-group" style="flex:0 0 140px;display:none;">' +
                            '<label class="form-label">Delete Days</label>' +
                            '<input type="number" id="mod-action-days" class="cfg-input" value="0" min="0" max="7" style="font-size:0.75rem;">' +
                        '</div>' +
                    '</div>' +
                    '<div style="margin-top:10px;">' +
                        '<label class="form-label">Reason</label>' +
                        '<input type="text" id="mod-action-reason" class="cfg-input" placeholder="Reason for action..." style="font-size:0.75rem;">' +
                    '</div>' +
                '</div>' +
                '<div class="mod-action-form-footer">' +
                    '<button class="btn-control" onclick="hideModActionForm()">Cancel</button>' +
                    '<button class="btn-control green" id="mod-action-submit" onclick="submitModAction(\'' + escapeHtml(userId) + '\')">✅ Confirm Action</button>' +
                '</div>' +
            '</div>' +
        '</div>';
        
        content.innerHTML = html;
    })
    .catch(() => {
        content.innerHTML = '<div class="no-data">❌ Failed to load user details</div>';
    });
}

function closeModUserDetail() {
    document.getElementById('mod-user-detail-modal').style.display = 'none';
}

// Close modal on overlay click
document.addEventListener('click', function(e) {
    const modal = document.getElementById('mod-user-detail-modal');
    if (e.target === modal) {
        closeModUserDetail();
    }
});

// ─── Clear Violations ──────────────────────────────────

function clearSingleViolation(userId, guildId, violationId) {
    if (!confirm('Remove violation #' + violationId + ' for user ' + userId + '?')) return;
    
    fetch('/api/moderation/clear-violations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, guild_id: guildId, violation_id: violationId })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            showToast(data.message || 'Violation cleared', 'success');
            openModUserDetail(userId);
            loadModUsers();
        } else {
            showToast(data.error || 'Failed to clear violation', 'error');
        }
    })
    .catch(() => showToast('Error clearing violation', 'error'));
}

function clearAllViolations(userId) {
    if (!confirm('Clear ALL violations for user ' + userId + '? This cannot be undone.')) return;
    if (!confirm('Are you sure? This will remove ALL violations across all guilds for this user.')) return;
    
    fetch('/api/moderation/clear-violations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, violation_id: 'all' })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            showToast(data.message || 'All violations cleared', 'success');
            closeModUserDetail();
            loadModUsers();
            loadModerationSummary();
        } else {
            showToast(data.error || 'Failed to clear violations', 'error');
        }
    })
    .catch(() => showToast('Error clearing violations', 'error'));
}

// ─── Mod Log ───────────────────────────────────────────

function fetchModLog() {
    const list = document.getElementById('mod-modlog-list');
    list.innerHTML = '<div class="no-data">Loading mod log...</div>';
    
    const actionFilter = document.getElementById('mod-action-filter').value;
    let url = '/api/moderation/modlog?limit=200';
    if (actionFilter) url += '&action=' + actionFilter;
    
    fetch(url)
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                renderModLog(data.entries || []);
            } else {
                list.innerHTML = '<div class="no-data">❌ Failed to load mod log</div>';
            }
        })
        .catch(() => {
            list.innerHTML = '<div class="no-data">❌ Failed to load mod log</div>';
        });
}

function renderModLog(entries) {
    const list = document.getElementById('mod-modlog-list');
    
    if (!entries || entries.length === 0) {
        list.innerHTML = '<div class="no-data">No mod log entries found.</div>';
        return;
    }
    
    const actionEmojis = {
        'warn': '⚠️', 'kick': '👢', 'ban': '🔨', 'unban': '🔓',
        'timeout': '🔇', 'untimeout': '🔊',
        'mute': '🔇', 'unmute': '🔊',
        'purge': '🗑️', 'slowmode': '🐢',
        'lock': '🔒', 'unlock': '🔓',
        'clearviolations': '🧹',
        'automod': '🤖', 'modsettings': '⚙️'
    };
    
    const actionColors = {
        'warn': '#ffaa44', 'kick': '#ff8800', 'ban': '#ff4444', 'unban': '#00ff88',
        'timeout': '#ff8800', 'untimeout': '#00ff88',
        'mute': '#ff8800', 'unmute': '#00ff88',
        'purge': '#4488ff', 'slowmode': '#aa88ff',
        'lock': '#ff4444', 'unlock': '#00ff88',
        'clearviolations': '#44aaff',
        'automod': '#ff44aa', 'modsettings': '#aa88ff'
    };
    
    let html = '';
    const count = Math.min(entries.length, 200);
    
    for (let i = 0; i < count; i++) {
        const e = entries[i];
        const ts = e.timestamp ? new Date(e.timestamp * 1000).toLocaleString() : 'Unknown';
        const action = (e.type || 'unknown').toLowerCase();
        const emoji = actionEmojis[action] || '📋';
        const color = actionColors[action] || '#888';
        const target = e.target || 'Unknown';
        const moderator = e.moderator || 'Unknown';
        const reason = e.reason || '';
        const guildId = e.guild_id || '?';
        
        html += '<div class="mod-log-entry">' +
            '<span class="mod-log-icon">' + emoji + '</span>' +
            '<div class="mod-log-body">' +
                '<span class="mod-log-action" style="color:' + color + ';">' + action.toUpperCase() + '</span> ' +
                '<span class="mod-log-target">→ ' + escapeHtml(target.length > 40 ? target.substring(0, 40) + '...' : target) + '</span> ' +
                '<span class="mod-log-moderator">by ' + escapeHtml(moderator.length > 30 ? moderator.substring(0, 30) + '...' : moderator) + '</span>' +
                (reason ? '<div class="mod-log-reason">' + escapeHtml(reason.length > 80 ? reason.substring(0, 80) + '...' : reason) + '</div>' : '') +
            '</div>' +
            '<div class="mod-log-time" title="Guild: ' + escapeHtml(guildId) + '">' + ts + '</div>' +
        '</div>';
    }
    
    if (entries.length > 200) {
        html += '<div class="mod-empty">... and ' + (entries.length - 200) + ' more entries. Use action filter to narrow down.</div>';
    }
    
    list.innerHTML = html;
}

// ─── Config ────────────────────────────────────────────

function loadModConfig() {
    const content = document.getElementById('mod-config-content');
    content.innerHTML = '<div class="no-data">Loading config...</div>';
    
    fetch('/api/moderation/summary')
        .then(r => r.json())
        .then(data => {
            if (data.success && data.summary) {
                renderModConfig(data.summary);
            } else {
                content.innerHTML = '<div class="no-data">❌ Failed to load moderation config</div>';
            }
        })
        .catch(() => {
            content.innerHTML = '<div class="no-data">❌ Failed to load moderation config</div>';
        });
}

function renderModConfig(summary) {
    const content = document.getElementById('mod-config-content');
    const cfg = summary.config || {};
    const typeBreakdown = summary.type_breakdown || {};
    const thresholds = cfg.warn_thresholds || {};
    
    let html = '';
    
    // Settings section
    html += '<div class="mod-config-section">' +
        '<h3>⚙️ Moderation Settings</h3>' +
        '<div class="mod-config-grid">' +
            '<div class="mod-config-item">' +
                '<span class="mod-config-label">AutoMod DM Warnings</span>' +
                '<span class="mod-config-value ' + (cfg.auto_mod_enabled ? 'enabled' : 'disabled') + '">' + 
                    (cfg.auto_mod_enabled ? '✅ Enabled' : '❌ Disabled') + 
                '</span>' +
            '</div>' +
            '<div class="mod-config-item">' +
                '<span class="mod-config-label">Muted Role ID</span>' +
                '<span class="mod-config-value">' + 
                    (cfg.muted_role_id ? escapeHtml(cfg.muted_role_id) : 'Not configured') + 
                '</span>' +
            '</div>' +
            '<div class="mod-config-item">' +
                '<span class="mod-config-label">Quarantine Role ID</span>' +
                '<span class="mod-config-value">' + 
                    (cfg.quarantine_role_id ? escapeHtml(cfg.quarantine_role_id) : 'Not configured') + 
                '</span>' +
            '</div>' +
            '<div class="mod-config-item">' +
                '<span class="mod-config-label">Staff Role ID</span>' +
                '<span class="mod-config-value">' + 
                    (cfg.staff_role_id ? escapeHtml(cfg.staff_role_id) : 'Not configured') + 
                '</span>' +
            '</div>' +
            '<div class="mod-config-item">' +
                '<span class="mod-config-label">Mod Log Channel</span>' +
                '<span class="mod-config-value">' + 
                    (cfg.mod_log_channel ? escapeHtml(cfg.mod_log_channel) : 'Not configured') + 
                '</span>' +
            '</div>' +
        '</div>' +
    '</div>';
    
    // Warn Thresholds
    html += '<div class="mod-config-section">' +
        '<h3>⚠️ Warn Thresholds</h3>';
    
    const thresholdKeys = Object.keys(thresholds);
    if (thresholdKeys.length === 0) {
        html += '<div class="mod-empty">No warn thresholds configured. Use !modsettings to set them up.</div>';
    } else {
        html += '<div class="mod-config-thresholds">';
        // Sort by count ascending
        thresholdKeys.sort((a, b) => parseInt(a) - parseInt(b));
        thresholdKeys.forEach(k => {
            const action = thresholds[k];
            const actionEmoji = action.includes('mute') ? '🔇' : action.includes('kick') ? '👢' : action.includes('ban') ? '🔨' : '⚠️';
            html += '<div class="mod-threshold-item">' +
                '<span class="mod-threshold-count">' + k + ' warns</span>' +
                '<span class="mod-threshold-action">→ ' + actionEmoji + ' ' + escapeHtml(action) + '</span>' +
            '</div>';
        });
        html += '</div>';
    }
    html += '</div>';
    
    // Type Breakdown
    html += '<div class="mod-config-section">' +
        '<h3>📊 Violation Breakdown</h3>' +
        '<div class="mod-config-thresholds">';
    
    const typeEmojis = { 'warn': '⚠️', 'kick': '👢', 'ban': '🔨', 'timeout': '🔇', 'mute': '🔇' };
    const typeKeys = Object.keys(typeBreakdown);
    if (typeKeys.length === 0) {
        html += '<div class="mod-empty">No violations recorded yet.</div>';
    } else {
        typeKeys.forEach(t => {
            const emoji = typeEmojis[t.toLowerCase()] || '📋';
            html += '<div class="mod-threshold-item">' +
                '<span class="mod-threshold-count" style="color:#888;">' + emoji + ' ' + escapeHtml(t.toUpperCase()) + '</span>' +
                '<span class="mod-threshold-action">' + typeBreakdown[t] + ' total</span>' +
            '</div>';
        });
    }
    html += '</div></div>';
    
    // Note that config is managed from Configuration page
    html += '<div style="margin-top:16px;padding:12px;background:rgba(255,255,255,0.03);border-radius:8px;border:1px solid rgba(255,255,255,0.06);">' +
        '<p style="color:#888;font-size:0.8rem;margin:0;">' +
        '💡 Moderation settings (warn thresholds, roles, mod log channel) can be managed via the <strong>Configuration</strong> tab under <code>manager_bot → moderation</code>.</p>' +
    '</div>';
    
    content.innerHTML = html;
}

// ─── Mod Action Form ──────────────────────────────────

let _modActionCurrentAction = null;

function getFirstGuildId(userId) {
    const userSum = _modUsersData.find(u => u.user_id === userId);
    if (userSum && userSum.guilds) {
        const guildKeys = Object.keys(userSum.guilds);
        if (guildKeys.length > 0) return guildKeys[0];
    }
    return '';
}

function getGuildOptions(userId) {
    const userSum = _modUsersData.find(u => u.user_id === userId);
    let options = '';
    if (userSum && userSum.guilds) {
        const guildKeys = Object.keys(userSum.guilds);
        guildKeys.forEach((gk, i) => {
            const count = userSum.guilds[gk];
            const selected = i === 0 ? 'selected' : '';
            options += '<option value="' + escapeHtml(gk) + '" ' + selected + '>Guild ' + escapeHtml(gk) + ' (' + count + ' violations)</option>';
        });
    }
    if (!options) {
        options = '<option value="">No guild data available</option>';
    }
    return options;
}

function showModActionForm(userId, action) {
    _modActionCurrentAction = action;
    const container = document.getElementById('mod-action-form-container');
    const title = document.getElementById('mod-action-form-title');
    const durationGroup = document.getElementById('mod-action-duration-group');
    const daysGroup = document.getElementById('mod-action-days-group');
    const reasonInput = document.getElementById('mod-action-reason');
    const submitBtn = document.getElementById('mod-action-submit');
    
    const actionNames = {
        'warn': '⚠️ Warn User',
        'kick': '👢 Kick Member',
        'ban': '🔨 Ban User',
        'unban': '🔓 Unban User',
        'timeout': '🔇 Timeout Member',
        'mute': '🔇 Mute Member',
        'unmute': '🔊 Unmute Member'
    };
    
    title.textContent = actionNames[action] || 'Action: ' + action;
    
    // Show/hide duration field
    if (action === 'timeout' || action === 'mute') {
        durationGroup.style.display = '';
        document.getElementById('mod-action-duration').value = action === 'mute' ? '1h' : '10m';
    } else {
        durationGroup.style.display = 'none';
    }
    
    // Show/hide delete days field
    if (action === 'ban') {
        daysGroup.style.display = '';
        document.getElementById('mod-action-days').value = '0';
    } else {
        daysGroup.style.display = 'none';
    }
    
    // Set submit button color based on action
    const actionColors = {
        'warn': 'gold',
        'kick': '#ff8800',
        'ban': 'red',
        'timeout': '#ff8800',
        'mute': '#ff8800',
        'unmute': 'green',
        'unban': 'green'
    };
    const color = actionColors[action] || 'green';
    submitBtn.style.cssText = color.startsWith('#') ? 
        'background:' + color + ';border-color:' + color + ';color:#fff;padding:8px 16px;border-radius:6px;border:none;cursor:pointer;font-weight:600;' :
        '';
    submitBtn.className = color.startsWith('#') ? 'btn-control' : 'btn-control ' + color;
    
    // Set reason placeholder
    const reasonPhrases = {
        'warn': 'Reason for warning...',
        'kick': 'Reason for kick...',
        'ban': 'Reason for ban...',
        'timeout': 'Reason for timeout...',
        'mute': 'Reason for mute...',
        'unmute': 'Reason for unmute...',
        'unban': 'Reason for unban...'
    };
    reasonInput.placeholder = reasonPhrases[action] || 'Reason...';
    
    container.style.display = '';
    container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function hideModActionForm() {
    document.getElementById('mod-action-form-container').style.display = 'none';
    _modActionCurrentAction = null;
}

function submitModAction(userId) {
    const action = _modActionCurrentAction;
    if (!action) {
        showToast('No action selected', 'error');
        return;
    }
    
    const guildSelect = document.getElementById('mod-action-guild');
    const guildId = guildSelect ? guildSelect.value : '';
    const reason = document.getElementById('mod-action-reason').value.trim() || '';
    const duration = document.getElementById('mod-action-duration') ? document.getElementById('mod-action-duration').value.trim() : '';
    const daysInput = document.getElementById('mod-action-days');
    const deleteDays = daysInput ? parseInt(daysInput.value) || 0 : 0;
    
    if (!guildId) {
        showToast('Please select a guild', 'error');
        return;
    }
    
    // Confirmation dialog
    let confirmMsg = '⚠️ ' + action.toUpperCase() + ' user ' + userId;
    confirmMsg += '\nGuild: ' + guildId;
    if (reason) confirmMsg += '\nReason: ' + reason;
    if (duration && (action === 'timeout' || action === 'mute')) confirmMsg += '\nDuration: ' + duration;
    if (action === 'ban' && deleteDays > 0) confirmMsg += '\nDelete Days: ' + deleteDays;
    confirmMsg += '\n\nProceed with this action?';
    
    if (!confirm(confirmMsg)) return;
    
    const btn = document.querySelector('#mod-action-submit');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ Processing...';
    }
    
    const payload = {
        action: action,
        user_id: userId,
        guild_id: guildId,
        reason: reason || 'No reason provided'
    };
    
    if (duration && (action === 'timeout' || action === 'mute')) {
        payload.duration = duration;
    }
    if (action === 'ban' && deleteDays > 0) {
        payload.delete_days = deleteDays;
    }
    
    fetch('/api/moderation/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(r => r.json())
    .then(data => {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '✅ Confirm Action';
        }
        if (data.success) {
            showToast(data.message || action + ' completed successfully', 'success');
            hideModActionForm();
            // Refresh the user detail to show updated violations
            openModUserDetail(userId);
            loadModUsers();
            loadModerationSummary();
        } else {
            showToast(data.error || 'Action failed', 'error');
        }
    })
    .catch(() => {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '✅ Confirm Action';
        }
        showToast('Error performing action', 'error');
    });
}

// ─── Init ──────────────────────────────────────────────

// Hook into the navigation system - load moderation data when tab is shown
const _origNav = window.nav;
if (typeof _origNav === 'function') {
    const _patchedNav = function(id, el) {
        _origNav.call(window, id, el);
        if (id === 'moderation') {
            loadModUsers();
        }
    };
    window.nav = _patchedNav;
}

// Also handle security tab's existing captcha polling for mod data refresh
document.addEventListener('DOMContentLoaded', function() {
    // Moderation data refresh every 30 seconds when visible
    setInterval(function() {
        const modView = document.getElementById('moderation');
        if (modView && modView.classList.contains('active-view')) {
            if (_modCurrentTab === 'users') {
                loadModUsers();
            } else if (_modCurrentTab === 'modlog') {
                fetchModLog();
            }
        }
    }, 30000);
});
