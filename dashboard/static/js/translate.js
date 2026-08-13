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

/*
 * Admin translation editor ("Translate" section).
 *
 * Lists every string key with its English source, and lets an admin write a
 * human translation for the selected language. Strings whose English source
 * changed since the last translation are flagged "Needs redo". Translations
 * are saved to the data repo via /api/i18n/translate.
 */
(function () {
    'use strict';

    let adminCatalog = null;
    let adminLang = null;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }

    function statusOf(key, lang, val, staleSet) {
        if (!val) return { cls: 'untranslated', label: 'Untranslated' };
        if (staleSet.has(key + '::' + lang)) return { cls: 'stale', label: 'Needs redo' };
        return { cls: 'translated', label: 'Translated' };
    }

    function filteredKeys() {
        const keys = adminCatalog.keys || {};
        const staleSet = new Set(adminCatalog.stale || []);
        const q = (document.getElementById('translate-search').value || '').trim().toLowerCase();
        const filter = document.getElementById('translate-filter').value;

        return Object.keys(keys).filter(key => {
            const entry = keys[key] || {};
            const en = entry.en || key;
            const val = entry[adminLang] || '';
            const st = statusOf(key, adminLang, val, staleSet);

            if (filter === 'untranslated' && st.cls !== 'untranslated') return false;
            if (filter === 'stale' && st.cls !== 'stale') return false;
            if (filter === 'translated' && st.cls !== 'translated') return false;

            if (q && !(key.toLowerCase().includes(q) || en.toLowerCase().includes(q))) return false;
            return true;
        }).sort((a, b) => {
            // Sort so "Needs redo" floats to the top, then untranslated.
            const stA = statusOf(a, adminLang, (keys[a] || {})[adminLang] || '', new Set(adminCatalog.stale || []));
            const stB = statusOf(b, adminLang, (keys[b] || {})[adminLang] || '', new Set(adminCatalog.stale || []));
            const rank = { stale: 0, untranslated: 1, translated: 2 };
            return (rank[stA.cls] - rank[stB.cls]) || a.localeCompare(b);
        });
    }

    function renderRow(key, entry) {
        const staleSet = new Set(adminCatalog.stale || []);
        const en = entry.en || key;
        const val = entry[adminLang] || '';
        const st = statusOf(key, adminLang, val, staleSet);

        const row = document.createElement('div');
        row.className = 'translate-row';

        const meta = document.createElement('div');
        meta.className = 'translate-row-meta';
        const keyEl = document.createElement('div');
        keyEl.className = 'translate-key';
        keyEl.textContent = key;
        const enEl = document.createElement('div');
        enEl.className = 'translate-en';
        enEl.textContent = en;
        meta.appendChild(keyEl);
        meta.appendChild(enEl);

        const badge = document.createElement('span');
        badge.className = 'translate-badge ' + st.cls;
        badge.textContent = st.label;

        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'cfg-input translate-input';
        input.value = val;
        input.placeholder = en;
        input.setAttribute('dir', 'auto');

        const save = document.createElement('button');
        save.className = 'btn-control green translate-save';
        save.textContent = 'Save';
        save.onclick = () => saveTranslation(key, input, save, badge);

        row.appendChild(meta);
        row.appendChild(badge);
        row.appendChild(input);
        row.appendChild(save);
        return row;
    }

    function renderList() {
        const list = document.getElementById('translate-list');
        const keys = adminCatalog.keys || {};
        const filtered = filteredKeys();
        const total = Object.keys(keys).length;

        document.getElementById('translate-count').textContent =
            `${filtered.length} of ${total} strings`;

        list.innerHTML = '';
        if (!filtered.length) {
            list.innerHTML = '<div class="no-data">No strings match.</div>';
            return;
        }
        filtered.forEach(key => list.appendChild(renderRow(key, keys[key] || {})));
    }

    function saveTranslation(key, input, saveBtn, badge) {
        const value = input.value;
        saveBtn.disabled = true;
        fetch('/api/i18n/translate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: key, language: adminLang, value: value }),
        })
            .then(r => r.json())
            .then(data => {
                if (data && data.success) {
                    adminCatalog.keys[key][adminLang] = value.trim();
                    const staleSet = new Set(adminCatalog.stale || []);
                    staleSet.delete(key + '::' + adminLang);
                    adminCatalog.stale = Array.from(staleSet);
                    const st = statusOf(key, adminLang, value.trim(), staleSet);
                    badge.className = 'translate-badge ' + st.cls;
                    badge.textContent = st.label;
                    if (window.showToast) showToast('Translation saved', 'success');
                } else {
                    if (window.showToast) showToast((data && data.error) || 'Save failed', 'error');
                }
            })
            .catch(() => { if (window.showToast) showToast('Save failed', 'error'); })
            .finally(() => { saveBtn.disabled = false; });
    }

    function populateLanguageSelect() {
        const sel = document.getElementById('translate-lang');
        sel.innerHTML = '';
        (adminCatalog.languages || []).forEach(l => {
            if (l.code === 'en') return; // English is the source, not editable
            const opt = document.createElement('option');
            opt.value = l.code;
            opt.textContent = (l.flag || '') + ' ' + (l.native || l.name);
            if (l.code === adminLang) opt.selected = true;
            sel.appendChild(opt);
        });
    }

    function loadCatalog() {
        fetch('/api/i18n/catalog')
            .then(r => r.json())
            .then(catalog => {
                adminCatalog = catalog || { languages: [], keys: {}, stale: [] };
                adminLang = adminLang || (adminCatalog.default_language || 'en');
                if (adminLang === 'en') {
                    const first = (adminCatalog.languages || []).find(l => l.code !== 'en');
                    adminLang = first ? first.code : 'en';
                }
                populateLanguageSelect();
                renderList();
            })
            .catch(() => {
                document.getElementById('translate-list').innerHTML =
                    '<div class="no-data">Failed to load translations.</div>';
            });
    }

    window.initTranslateView = function () {
        loadCatalog();
    };

    window.syncI18nNow = function () {
        const btn = document.getElementById('translate-sync-btn');
        if (btn) btn.disabled = true;
        fetch('/api/i18n/sync', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data && data.success) {
                    if (window.showToast) showToast('Catalog re-synced', 'success');
                    adminLang = null;
                    loadCatalog();
                } else if (window.showToast) {
                    showToast((data && data.error) || 'Sync failed', 'error');
                }
            })
            .catch(() => { if (window.showToast) showToast('Sync failed', 'error'); })
            .finally(() => { if (btn) btn.disabled = false; });
    };

    document.addEventListener('DOMContentLoaded', function () {
        const langSel = document.getElementById('translate-lang');
        if (langSel) langSel.addEventListener('change', () => { adminLang = langSel.value; renderList(); });
        const search = document.getElementById('translate-search');
        if (search) search.addEventListener('input', renderList);
        const filter = document.getElementById('translate-filter');
        if (filter) filter.addEventListener('change', renderList);
    });
})();
