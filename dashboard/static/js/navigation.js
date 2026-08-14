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

const DASHBOARD_VIEW_PATHS = {
    'accounts': 'overview/accounts',
    'dash': 'overview/dashboard',
    'history': 'overview/history',
    'orb-grinder': 'automation/orb-grinder',
    'mass-dismantle': 'automation/mass-dismantle',
    'proxies': 'automation/proxies',
    'config': 'tools/configuration',
    'extension': 'tools/extension',
    'archives': 'tools/archives',
    'logs': 'tools/logs',
    'security': 'security',
    'admin-users': 'admin/login-users',
    'api-keys': 'admin/api-keys',
    'appeals': 'admin/appeals',
    'moderation': 'admin/moderation',
    'tickets': 'admin/tickets',
    'translate': 'admin/translate',
    'my-account': 'account/my-account'
};

function dashboardViewPath(id) {
    return DASHBOARD_VIEW_PATHS[id] || DASHBOARD_VIEW_PATHS.accounts;
}

window.dashboardPathForView = function(id) {
    return '/dashboard/' + dashboardViewPath(id);
};

window.dashboardViewFromPath = function(pathname) {
    const path = (pathname || '').replace(/^\/+|\/+$/g, '');
    if (!path || path === 'dashboard') return 'accounts';
    if (!path.startsWith('dashboard/')) return null;
    const viewPath = path.slice('dashboard/'.length);
    return Object.keys(DASHBOARD_VIEW_PATHS).find(id => DASHBOARD_VIEW_PATHS[id] === viewPath) || null;
};

window.updateDashboardUrl = function(id, replace = false) {
    const nextPath = window.dashboardPathForView(id);
    if (window.location.pathname === nextPath) return;
    window.history[replace ? 'replaceState' : 'pushState']({ dashboardView: id }, '', nextPath);
};

window.nav = function(id, el, options = {}) {
    console.log(`Navigating to: ${id}`);
    if (!options.skipUrl) window.updateDashboardUrl(id, !!options.replaceUrl);
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active-view'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const target = document.getElementById(id);
    if (!target) {
        console.error(`Navigation target not found: ${id}`);
        return;
    }
    target.classList.add('active-view');
    if (el) el.classList.add('active');
    checkDirty();

    const mobileControls = document.getElementById('mobileControls');
    if (mobileControls) {
        if (id === 'dash' && window.innerWidth <= 768) {
            mobileControls.style.display = 'flex';
        } else {
            mobileControls.style.display = 'none';
        }
    }

    const mobileTopControls = document.querySelector('.mobile-top-controls');
    if (mobileTopControls) {
        mobileTopControls.style.display = (id === 'dash') ? 'flex' : 'none';
    }
    if (id === 'dash') {
        document.body.classList.add('active-dash-header');
    } else {
        document.body.classList.remove('active-dash-header');
    }
    if (window.innerWidth <= 768) {
        const sidebar = document.querySelector('.sidebar');
        if (sidebar && sidebar.classList.contains('active')) {
            toggleMobileMenu();
        }
    }
    if (id === 'accounts') {
        renderAccountGrid();
        fetchAccountConfig();
        if (typeof fetchProxies === 'function') fetchProxies();
    }
    if (id === 'proxies' && typeof fetchProxies === 'function') fetchProxies();
    if (id === 'config') loadConfig();
    if (id === 'history') loadHistory();
    if (id === 'security') {
        fetchSecuritySummary();
        pollForCaptchas();
        if (typeof window.maybeRenderEmbeddedCaptcha === 'function') window.maybeRenderEmbeddedCaptcha();
    }
    if (id === 'admin-users') {
        if (typeof fetchUsers === 'function') fetchUsers();
    }
    if (id === 'extension' && typeof window.loadExtensionInfo === 'function') {
        window.loadExtensionInfo();
    }
    // Orb Grinder page: stop any polling when leaving, start it when entering
    if (typeof window.stopQuestPolling === 'function') window.stopQuestPolling();
    if (id === 'orb-grinder' && typeof window.loadQuestGrinder === 'function') {
        window.loadQuestGrinder();
    }
    // Mass Dismantle page: stop any polling when leaving, start it when entering
    if (typeof window.stopWeaponsPolling === 'function') window.stopWeaponsPolling();
    if (id === 'mass-dismantle' && typeof window.loadWeapons === 'function') {
        window.loadWeapons();
    }
    if (id === 'archives' && typeof window.loadArchivePage === 'function') {
        window.loadArchivePage();
    }
    if (id === 'my-account' && typeof window.loadSecurityStatus === 'function') {
        window.loadSecurityStatus();
    }
};

function initDashboardLinks() {
    document.querySelectorAll('a.nav-item').forEach(item => {
        const onclick = item.getAttribute('onclick') || '';
        const match = onclick.match(/nav\('([^']+)'/);
        if (match && DASHBOARD_VIEW_PATHS[match[1]]) {
            item.setAttribute('href', window.dashboardPathForView(match[1]));
        }
    });
}

document.addEventListener('click', event => {
    const link = event.target.closest('a.nav-item[href^="/dashboard/"]');
    if (link) event.preventDefault();
});

function dashboardNavElement(id) {
    return Array.from(document.querySelectorAll('.nav-item')).find(item => {
        const onclick = item.getAttribute('onclick') || '';
        return onclick.includes(`nav('${id}'`);
    });
}

function applyDashboardPath(pathname, replaceUrl = false) {
    const id = window.dashboardViewFromPath(pathname) || 'accounts';
    const current = document.getElementById(id);
    if (!current) return;
    window.nav(id, dashboardNavElement(id), { skipUrl: true });
    if (replaceUrl) window.updateDashboardUrl(id, true);
}

window.addEventListener('popstate', () => applyDashboardPath(window.location.pathname));

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        initDashboardLinks();
        applyDashboardPath(window.location.pathname, window.location.pathname === '/dashboard');
    });
} else {
    initDashboardLinks();
    applyDashboardPath(window.location.pathname, window.location.pathname === '/dashboard');
}

window.toggleMobileMenu = function() {
    const s = document.querySelector('.sidebar'), o = document.querySelector('.sidebar-overlay'), t = document.querySelector('.mobile-menu-toggle');
    if (!s || !o || !t) return;
    s.classList.toggle('active'); o.classList.toggle('active'); t.classList.toggle('active');
    document.body.style.overflow = s.classList.contains('active') ? 'hidden' : '';
};

// ── Collapsible nav groups ─────────────────────────────

window.toggleNavGroup = function(name, btn) {
    const group = btn ? btn.closest('.nav-group') : document.getElementById('navGroup-' + name);
    if (!group) return;
    const open = group.classList.toggle('open');
    const toggle = group.querySelector('.nav-group-toggle');
    if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    try {
        const saved = JSON.parse(localStorage.getItem('limeyNavGroups') || '{}');
        saved[name] = open;
        localStorage.setItem('limeyNavGroups', JSON.stringify(saved));
    } catch (e) { /* private mode / disabled storage — collapse still works */ }
};

function refreshNavGroups() {
    document.querySelectorAll('.nav-group').forEach(group => {
        let visible = 0;
        group.querySelectorAll('.nav-item').forEach(item => {
            if (item.style.display !== 'none') visible++;
        });
        group.style.display = visible ? '' : 'none';
    });
}
window.refreshNavGroups = refreshNavGroups;

function initNavGroups() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem('limeyNavGroups') || '{}'); } catch (e) {}
    document.querySelectorAll('.nav-group').forEach(group => {
        const name = (group.id || '').replace(/^navGroup-/, '');
        let open = false;
        if (group.querySelector('.nav-item.active')) {
            // The group holding the current page is always shown expanded
            open = true;
        } else if (typeof saved[name] === 'boolean') {
            open = saved[name];
        }
        group.classList.toggle('open', open);
        const toggle = group.querySelector('.nav-group-toggle');
        if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    refreshNavGroups();
    // Auto-expand (and remember) the group of whichever nav item becomes active
    document.addEventListener('click', e => {
        const item = e.target.closest('.nav-item');
        const group = item ? item.closest('.nav-group') : null;
        if (!group) return;
        group.classList.add('open');
        const toggle = group.querySelector('.nav-group-toggle');
        if (toggle) toggle.setAttribute('aria-expanded', 'true');
        const name = (group.id || '').replace(/^navGroup-/, '');
        if (!name) return;
        try {
            const saved = JSON.parse(localStorage.getItem('limeyNavGroups') || '{}');
            if (!saved[name]) {
                saved[name] = true;
                localStorage.setItem('limeyNavGroups', JSON.stringify(saved));
            }
        } catch (e) { /* private mode / disabled storage */ }
    });
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initNavGroups);
} else {
    initNavGroups();
}