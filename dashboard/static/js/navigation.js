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
window.nav = function(id, el) {
    console.log(`Navigating to: ${id}`);
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
};

window.toggleMobileMenu = function() {
    const s = document.querySelector('.sidebar'), o = document.querySelector('.sidebar-overlay'), t = document.querySelector('.mobile-menu-toggle');
    if (!s || !o || !t) return;
    s.classList.toggle('active'); o.classList.toggle('active'); t.classList.toggle('active');
    document.body.style.overflow = s.classList.contains('active') ? 'hidden' : '';
};