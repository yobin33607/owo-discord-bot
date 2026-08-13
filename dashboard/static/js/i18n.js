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
 * Client-side i18n runtime.
 *
 * Loads the translation catalog from /api/i18n/catalog, exposes a `t()`
 * helper, applies translations to elements tagged with `data-i18n`
 * (text), `data-i18n-ph` (placeholder) and `data-i18n-title` (title attr),
 * and manages any `.limey-lang-select` dropdowns. The default language is
 * English. The chosen language is persisted in localStorage.
 */
(function () {
    'use strict';

    const LS_KEY = 'limey_lang';
    const state = {
        lang: null,
        languages: [],
        keys: {},
        staleSet: new Set(),
        ready: false,
    };

    function safeGet() {
        try { return localStorage.getItem(LS_KEY); } catch (e) { return null; }
    }
    function safeSet(v) {
        try { localStorage.setItem(LS_KEY, v); } catch (e) { /* ignore */ }
    }

    // Translate one key. Stale strings (English changed since the last
    // translation) fall back to English until a human re-translates them.
    function t(key, fallback) {
        if (key == null) return fallback != null ? fallback : '';
        const entry = state.keys[key];
        if (entry && typeof entry === 'object') {
            const stale = state.staleSet.has(key + '::' + state.lang);
            const val = entry[state.lang];
            if (val && !stale) return val;
            if (entry.en) return entry.en;
        }
        return fallback != null ? fallback : key;
    }

    // Replace only direct text nodes, preserving child elements (icons, etc.).
    function setTextPreservingChildren(el, text) {
        const nodes = Array.from(el.childNodes);
        let replaced = false;
        for (const n of nodes) {
            if (n.nodeType === Node.TEXT_NODE) {
                if (!replaced) {
                    n.nodeValue = text;
                    replaced = true;
                } else {
                    n.nodeValue = '';
                }
            }
        }
        if (!replaced) {
            el.appendChild(document.createTextNode(text));
        }
    }

    function apply(root) {
        if (!state.ready) return;
        root = root || document;
        root.querySelectorAll('[data-i18n]').forEach(el => {
            setTextPreservingChildren(el, t(el.getAttribute('data-i18n')));
        });
        root.querySelectorAll('[data-i18n-ph]').forEach(el => {
            el.setAttribute('placeholder', t(el.getAttribute('data-i18n-ph')));
        });
        root.querySelectorAll('[data-i18n-title]').forEach(el => {
            el.setAttribute('title', t(el.getAttribute('data-i18n-title')));
        });
        document.documentElement.setAttribute('lang', state.lang || 'en');
        const info = (state.languages || []).find(l => l.code === state.lang);
        document.documentElement.setAttribute('dir', (info && info.rtl) ? 'rtl' : 'ltr');
    }

    function populateSelects() {
        const selects = document.querySelectorAll('.limey-lang-select');
        selects.forEach(sel => {
            if (sel.dataset.populated) return;
            sel.dataset.populated = '1';
            sel.innerHTML = '';
            (state.languages || []).forEach(l => {
                const opt = document.createElement('option');
                opt.value = l.code;
                opt.textContent = (l.flag || '') + ' ' + (l.native || l.name);
                if (l.code === state.lang) opt.selected = true;
                sel.appendChild(opt);
            });
            sel.addEventListener('change', () => setLang(sel.value));
        });
    }

    function setLang(code) {
        if (!code) return;
        state.lang = code;
        safeSet(code);
        apply(document);
        populateSelects();
        document.dispatchEvent(new CustomEvent('limey-langchange', { detail: { lang: code } }));
    }

    function init(catalog) {
        state.languages = (catalog && catalog.languages) || [];
        state.keys = (catalog && catalog.keys) || {};
        state.staleSet = new Set((catalog && catalog.stale) || []);
        const defaultLang = (catalog && catalog.default_language) || 'en';
        state.lang = safeGet() || defaultLang;
        if (!state.languages.some(l => l.code === state.lang)) state.lang = defaultLang;
        state.ready = true;
        populateSelects();
        apply(document);
    }

    const I18N = {
        get lang() { return state.lang; },
        get languages() { return state.languages; },
        get keys() { return state.keys; },
        get stale() { return Array.from(state.staleSet); },
        get ready() { return state.ready; },
        t: t,
        apply: apply,
        setLang: setLang,
    };

    window.I18N = I18N;
    window.t = t;
    window.applyI18n = apply;

    function load() {
        fetch('/api/i18n/catalog')
            .then(r => (r.ok ? r.json() : null))
            .then(catalog => { if (catalog) init(catalog); })
            .catch(() => { /* keep English on failure */ });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', load);
    } else {
        load();
    }
})();
