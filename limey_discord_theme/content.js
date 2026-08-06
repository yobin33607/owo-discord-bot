/* Limey Captcha Alert Theme — content script
 *
 * Styles captcha-alert messages as dark red auth-screen alert boxes (styles.css).
 *
 * Everything that decides WHAT gets styled lives in config.json (and the
 * defaults below), so Discord markup changes or new captcha wording can be
 * fixed WITHOUT reinstalling the extension: the script checks the update
 * server (UPDATE_URL — your dashboard deployed to Render) on load, and when a
 * newer version is published it hot-applies the new targeting config and CSS
 * to the already-open page. Nothing is ever sent to the server — the updater
 * only downloads the extension's own config/CSS files.
 *
 * Matching rules (designed to avoid false positives):
 *   1. Author ID — messages from the Limey bot id (config.authorIds).
 *   2. Author name — the display name, matched case-insensitively, but ONLY
 *      for authors that carry a bot/webhook tag (BOT / APP / WEBHOOK). A human
 *      user named "Limey" has no tag and is never matched.
 *   3. Keyword fallback — messages whose author shows the "WEBHOOK" tag AND
 *      whose text contains a strong captcha keyword. The official Discord bot
 *      (tagged BOT) and human users are never flagged.
 *   4. Webhook lock (popup) — pins the alert to a single webhook's author id.
 *
 * Author detection is resilient to Discord renames: data-author-id →
 * data-user-id → avatar image URL (served for users and webhooks).
 */
(() => {
  'use strict';

  const STYLE_ID = 'limey-theme-style';
  const DEBUG = true; // set false to silence per-message logs

  // ── Update server ──────────────────────────────────────────────────────────
  // The server (your dashboard app on Render) serves /ext/updates.json built
  // from this same folder, so any fix you push and redeploy is picked up by
  // every installed copy of the extension automatically.
  const UPDATE_URL = 'https://limeyself.onrender.com/ext/updates.json';

  // ── Targeting defaults (overridden by config.json, then by the server) ─────
  const DEFAULT_CONFIG = {
    authorIds: ['1514929209158402078'],
    authorNames: ['limey'], // name match requires a bot/webhook tag
    fallbackKeywordAlerts: true, // WEBHOOK-tagged authors + alert phrasing
    // Direct demand signals — any of these on a confirmed author triggers the
    // ⚠ label on its own.
    alertKeywords: ['slow-down', 'rate limited', 'autohunt', 'hcaptcha', 'recaptcha', '⚠'],
    // "captcha/verify … <action>" demand phrasing (either order). Report
    // phrasing like "captchas solved: 5" intentionally does NOT match, so a
    // stats announcement is never flagged as a captcha alert.
    alertPatterns: [
      '(captcha|hcaptcha|recaptcha|verification|verify)\\b[^.!?\\n]{0,40}\\b(complete|solve|solving|detected|required|confirm|continue|click|banned|ban|timeout|must|need|please)\\b',
      '\\b(complete|solve|solving|detected|required|confirm|continue|click|banned|ban|timeout|must|need|please)[^.!?\\n]{0,40}(captcha|hcaptcha|recaptcha|verification|verify)\\b',
    ],
    messageSelector: '[class*="messageListItem"], li[data-list-item-id]',
    userIdAttrs: ['data-author-id', 'data-user-id'],
    avatarSrcRe: 'avatars/(\\d{15,20})/',
    nameSelector: '[class*="username"], [class*="authorName"], [class*="headerText"]',
    tagSelector: '[class*="botTag"]',
    tagPillSelector: '[class*="botTag"], [class*="tag"], [class*="badge"], [class*="pill"]',
    tagWords: ['bot', 'app', 'webhook'],
  };

  let CONFIG = Object.assign({}, DEFAULT_CONFIG);
  let configVersion = ''; // version of the config currently applied ('' = packaged)
  let avatarRe = null;

  let styleEl = null;
  let styleSource = 'local'; // 'local' | 'remote' — remote css wins once applied
  let remoteCssUrl = ''; // set when a server update is applied ('' = packaged css)
  let state = { enabled: true, captchaHighlight: true, webhookAuthorId: '' };
  const stats = { tagged: 0, alerts: 0, noAuthorAttr: 0 };
  let warnedNoAuthor = false;
  let warnedNoTag = false;
  let lastLog = '';

  function log(msg, level) {
    if (!DEBUG) return;
    (console[level] || console.log)('[Limey Theme] ' + msg);
  }

  function getAvatarRe() {
    if (!avatarRe) avatarRe = new RegExp(CONFIG.avatarSrcRe);
    return avatarRe;
  }

  let alertRes = null;
  function getAlertRes() {
    if (!alertRes) {
      alertRes = (CONFIG.alertPatterns || []).map((p) => {
        try { return new RegExp(p, 'i'); } catch (e) { return null; }
      }).filter(Boolean);
    }
    return alertRes;
  }

  // A message is alert-worthy only when it DEMANDS a captcha/verification (or
  // is a direct slow-down / rate-limit / autohunt signal). Merely mentioning
  // captcha — e.g. a stats line like "captchas solved: 5" — is not, so stats
  // announcements are never given the ⚠ CAPTCHA ALERT label.
  function isAlertWorthy(text) {
    if ((CONFIG.alertKeywords || []).some((k) => text.includes(k))) return true;
    for (const re of getAlertRes()) {
      if (re.test(text)) return true;
    }
    return false;
  }

  // ── Config loading / merging ────────────────────────────────────────────────
  // Only known keys are accepted, and only with the same type as the default,
  // so a malformed remote payload can never break the script.
  function applyConfig(cfg) {
    if (!cfg || typeof cfg !== 'object') return false;
    let changed = false;
    for (const key of Object.keys(DEFAULT_CONFIG)) {
      if (!(key in cfg) || cfg[key] === null || cfg[key] === undefined) continue;
      const wantArr = Array.isArray(DEFAULT_CONFIG[key]);
      if (wantArr && !Array.isArray(cfg[key])) continue;
      if (!wantArr && typeof cfg[key] !== typeof DEFAULT_CONFIG[key]) continue;
      CONFIG[key] = cfg[key];
      changed = true;
    }
    if (changed) { avatarRe = null; alertRes = null; }
    return changed;
  }

  async function fetchWithTimeout(url, ms) {
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), ms || 8000) : null;
    try {
      return await fetch(url, { cache: 'no-store', signal: ctrl ? ctrl.signal : undefined });
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  function resolveUrl(base, u) {
    try { return new URL(u, base).href; } catch (e) { return u; }
  }

  function semverGt(a, b) {
    const pa = String(a || '0').split('.').map(Number);
    const pb = String(b || '0').split('.').map(Number);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
      const x = pa[i] || 0;
      const y = pb[i] || 0;
      if (x !== y) return x > y;
    }
    return false;
  }

  async function checkForUpdates(manual) {
    try {
      const res = await fetchWithTimeout(UPDATE_URL, 8000);
      if (!res.ok) return { ok: false, error: 'HTTP ' + res.status };
      const data = await res.json();
      const local = chrome.runtime.getManifest().version || '0';
      const remote = String(data.version || '0');
      if (!semverGt(remote, local)) {
        return { ok: true, updated: false, local: local, remote: remote };
      }
      const cfgChanged = applyConfig(data.config);
      remoteCssUrl = data.css_url || '/ext/styles.css';
      let cssChanged = false;
      if (styleEl && styleSource === 'local') {
        const css = await fetchWithTimeout(resolveUrl(UPDATE_URL, remoteCssUrl), 8000).then((r) => (r.ok ? r.text() : '')).catch(() => '');
        if (css) {
          styleEl.textContent = css;
          styleSource = 'remote';
          cssChanged = true;
        }
      }
      configVersion = remote;
      refresh();
      log('updated to v' + remote + ' from server' + (cfgChanged ? ' (new targeting config)' : '') + (cssChanged ? ' (new styles)' : ''));
      return { ok: true, updated: true, local: local, remote: remote, config: cfgChanged, css: cssChanged };
    } catch (e) {
      if (manual) log('update check failed: ' + (e && e.message ? e.message : e), 'warn');
      return { ok: false, error: String((e && e.message) || e) };
    }
  }

  // ── Stylesheet injection ────────────────────────────────────────────────────
  async function loadCss(url) {
    try {
      const res = await fetchWithTimeout(url, 8000);
      return await res.text();
    } catch (e) {
      console.warn('[Limey Theme] Could not load styles.css:', e);
      return '';
    }
  }

  function applyTheme() {
    if (state.enabled && !styleEl) {
      const el = document.createElement('style');
      el.id = STYLE_ID;
      if (remoteCssUrl) {
        // A server update is active — load the remote stylesheet for the
        // fresh element so the update survives a toggle off/on.
        loadCss(resolveUrl(UPDATE_URL, remoteCssUrl)).then((css) => {
          if (css && el.isConnected) {
            el.textContent = css;
            styleSource = 'remote';
          } else if (el.isConnected) {
            el.remove();
            if (styleEl === el) styleEl = null;
          }
        });
      } else {
        styleSource = 'local';
        loadCss(chrome.runtime.getURL('styles.css')).then((css) => {
          if (css && el.isConnected && styleSource === 'local') {
            el.textContent = css;
          } else if (el.isConnected && !css && styleSource === 'local') {
            el.remove();
            if (styleEl === el) styleEl = null;
          }
        });
      }
      (document.head || document.documentElement).appendChild(el);
      styleEl = el;
    } else if (!state.enabled && styleEl) {
      styleEl.remove();
      styleEl = null;
    }
  }

  // One-time DOM probe printed when author detection fails — shows what the
  // page actually contains so the selectors can be pinned to the real client
  // (mods like Vencord change Discord's markup). Best-effort, never throws.
  function domProbe() {
    try {
      if (!document.querySelectorAll) return;
      const primary = document.querySelectorAll(CONFIG.messageSelector).length;
      const loose = document.querySelectorAll('[class*="message"]').length;
      const authorIds = document.querySelectorAll('[data-author-id]').length;
      const tags = document.querySelectorAll(CONFIG.tagSelector).length;
      const classes = [];
      const els = document.querySelectorAll('[class]');
      for (let i = 0; i < els.length && classes.length < 16; i++) {
        const list = els[i].classList;
        if (!list || typeof list.forEach !== 'function') continue;
        list.forEach((c) => { if (/message|author|avatar|chat/i.test(c)) classes.push(c); });
      }
      const sample = [...new Set(classes)].slice(0, 12).join(', ') || '(no message/author/avatar classes found)';
      log('DOM probe: primaryRows=' + primary + ' [class*="message"]=' + loose +
        ' [data-author-id]=' + authorIds + ' tags=' + tags + ' | classes: ' + sample, 'warn');
    } catch (e) { /* probe is best-effort */ }
  }

  function clearAll() {
    document.querySelectorAll('.limey-captcha').forEach((el) => {
      el.classList.remove('limey-captcha', 'is-alert');
    });
    document.querySelectorAll('.limey-alert-label').forEach((el) => el.remove());
  }

  // ── Author / tag detection ──────────────────────────────────────────────────
  function messageRow(node) {
    if (!node) return null;
    if (node.closest) {
      const row = node.closest(CONFIG.messageSelector);
      if (row) return row;
    }
    return (node.matches && node.matches(CONFIG.messageSelector)) ? node : null;
  }

  function authorIdOf(node) {
    // 1) Author/user id attributes (self, descendants, ancestors).
    for (const attr of CONFIG.userIdAttrs) {
      let host = null;
      if (node.querySelector) host = node.querySelector('[' + attr + ']');
      if (!host && node.closest) host = node.closest('[' + attr + ']');
      if (host) {
        const v = host.getAttribute(attr);
        if (v) return v;
      }
    }
    // 2) Avatar image src — users AND webhooks are served avatar URLs that
    //    survive markup renames. Skip avatars inside embeds: an embed may
    //    cite an arbitrary other user, so its avatar is not the author.
    if (node.querySelectorAll) {
      const imgs = node.querySelectorAll('img[src*="avatars/"]');
      for (const img of imgs) {
        if (img.closest && img.closest('[class*="embed"]')) continue;
        const m = (img.getAttribute('src') || '').match(getAvatarRe());
        if (m) return m[1];
      }
      // Some clients (e.g. Vencord) render avatars as CSS background images
      // instead of <img> tags — check inline styles too.
      const styled = node.querySelectorAll('[style*="avatars/"]');
      for (const el of styled) {
        if (el.closest && el.closest('[class*="embed"]')) continue;
        const m = (el.getAttribute('style') || '').match(getAvatarRe());
        if (m) return m[1];
      }
    }
    return '';
  }

  function authorNameOf(li) {
    if (!li.querySelector) return '';
    const el = li.querySelector(CONFIG.nameSelector);
    return el ? (el.textContent || '').trim().toLowerCase() : '';
  }

  // Discord's author pill: "bot", "app" or "webhook". Humans have no tag.
  function tagTextOf(li) {
    if (!li.querySelector) return '';
    // 1) The known tag-pill class.
    let el = li.querySelector(CONFIG.tagSelector);
    // 2) Fallback: any pill/badge/tag element whose exact text is one of
    //    Discord's author tags (survives class renames).
    if (!el && li.querySelectorAll) {
      const pills = li.querySelectorAll(CONFIG.tagPillSelector);
      for (const p of pills) {
        const t = (p.textContent || '').trim().toLowerCase();
        if (CONFIG.tagWords.indexOf(t) !== -1) { el = p; break; }
      }
    }
    return el ? (el.textContent || '').trim().toLowerCase() : '';
  }

  function isWebhook(li) {
    return tagTextOf(li) === 'webhook';
  }

  function isTagged(li) {
    const t = tagTextOf(li);
    return t === 'webhook' || t === 'bot' || t === 'app';
  }

  // Matches are normalized: exact name or "name …" (covers "Limey Alerts")
  // but NOT variants like "limey2" or "x_limey".
  function nameMatches(name, n) {
    return name === n || name.startsWith(n + ' ');
  }

  function tagMessage(node) {
    if (!node || node.nodeType !== 1) return;
    const li = messageRow(node);
    if (!li || !li.classList) return;

    const author = authorIdOf(node);
    if (!author) {
      stats.noAuthorAttr++;
      if (!warnedNoAuthor) {
        warnedNoAuthor = true;
        log('Could not determine the message author (no id attribute or avatar found) — Discord may have changed its DOM. Falling back to name/tag/keyword matching.', 'warn');
        domProbe();
      }
    }

    const text = (node.textContent || '').toLowerCase();
    const alertHit = isAlertWorthy(text);

    const idMatch = author !== '' && CONFIG.authorIds.indexOf(author) !== -1;
    let nameMatch = false;
    let name = '';
    // Name matching requires a bot/webhook/app tag so a human named "Limey"
    // (no tag) is never styled.
    if (!idMatch && CONFIG.authorNames.length && isTagged(li)) {
      name = authorNameOf(li);
      nameMatch = name !== '' && CONFIG.authorNames.some((n) => nameMatches(name, n));
    }
    const isLimey = idMatch || nameMatch;

    const locked = state.webhookAuthorId !== '';
    const lockedMatch = locked && author === state.webhookAuthorId;

    // The ⚠ label:
    //  - confirmed Limey: only when the text DEMANDS a captcha (a stats
    //    announcement that merely says "captchas solved" never qualifies)
    //  - locked: ONLY the locked webhook, same demand phrasing
    //  - fallback: ONLY messages tagged "WEBHOOK" with demand phrasing —
    //    humans and bots (like the official Discord account) never qualify.
    const fallbackEligible = CONFIG.fallbackKeywordAlerts && !locked &&
      isWebhook(li) && alertHit;
    let applyAlert;
    if (locked) {
      applyAlert = lockedMatch && alertHit && state.captchaHighlight;
    } else {
      applyAlert = (isLimey && alertHit && state.captchaHighlight) ||
        (fallbackEligible && state.captchaHighlight);
    }

    if (!isLimey && !lockedMatch && !applyAlert) {
      // Diagnostic: a captcha-demand message was skipped. If its author has no
      // tag at all, Discord's tag markup may have changed.
      if (alertHit && tagTextOf(li) === '' && !warnedNoTag) {
        warnedNoTag = true;
        log('Found a captcha-like message whose author has no WEBHOOK/BOT tag. If you expected an alert here, Discord\'s tag markup may have changed — use the dashboard webhook id (Dashboard → Extension → lock) or run the F12 snippet from the README to dump the message markup.', 'warn');
      }
      return;
    }

    if (state.enabled) {
      li.classList.add('limey-captcha');
      stats.tagged++;
      if (applyAlert) {
        li.classList.add('is-alert');
        ensureLabel(li);
        stats.alerts++;
      }
      const via = idMatch ? 'id ' + author : nameMatch ? 'name "' + name + '"' : lockedMatch ? 'locked webhook' : 'webhook keyword';
      log('styled message from ' + via + (applyAlert ? ' [alert]' : ''));
    }
  }

  // The ⚠ label is a real DOM element (not a CSS pseudo-element) so it renders
  // regardless of Discord's internal wrappers — webhook captcha alerts are
  // often embed-only posts with an empty/hidden message-content div.
  function ensureLabel(li) {
    if (li.querySelector('.limey-alert-label')) return;
    const label = document.createElement('div');
    label.className = 'limey-alert-label';
    label.setAttribute('aria-hidden', 'true'); // decorative; the alert text itself is in the message
    const title = document.createElement('div');
    title.className = 'limey-alert-title';
    title.textContent = '⚠ CAPTCHA ALERT';
    const sub = document.createElement('div');
    sub.className = 'limey-alert-sub';
    sub.textContent = 'SOLVE TO CONTINUE';
    label.appendChild(title);
    label.appendChild(sub);
    li.insertBefore(label, li.firstChild);
  }

  function scan(root) {
    if (!state.enabled || !root) return;
    if (root.querySelectorAll) {
      root.querySelectorAll(CONFIG.messageSelector).forEach(tagMessage);
    }
  }

  // Batch subtree scans through requestIdleCallback so loading a big channel
  // history (huge added subtrees) doesn't jank the page.
  let pendingRoots = [];
  let scanScheduled = false;
  function scanLater(root) {
    if (!state.enabled) return;
    pendingRoots.push(root);
    if (scanScheduled) return;
    scanScheduled = true;
    const run = () => {
      scanScheduled = false;
      const roots = pendingRoots;
      pendingRoots = [];
      for (const r of roots) scan(r);
    };
    if (typeof window !== 'undefined' && window.requestIdleCallback) {
      window.requestIdleCallback(run, { timeout: 250 });
    } else {
      setTimeout(run, 60);
    }
  }

  const observer = new MutationObserver((mutations) => {
    if (!state.enabled) return;
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.matches && node.matches(CONFIG.messageSelector)) {
          tagMessage(node);
        }
        scanLater(node);
      }
    }
  });

  function refresh() {
    clearAll();
    stats.tagged = 0;
    stats.alerts = 0;
    if (state.enabled) scan(document);
    const msg = `active on ${location.hostname} — ${stats.tagged} message(s) styled, ${stats.alerts} captcha alert(s)` +
      (state.webhookAuthorId ? ` [locked to webhook ${state.webhookAuthorId}]` : '') +
      (configVersion ? ` [updated to v${configVersion} from server]` : '');
    if (msg !== lastLog) {
      lastLog = msg;
      log(msg);
    }
  }

  // Registered at top level (not inside the async init) so storage changes
  // are handled immediately, before the packaged config finishes loading.
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'sync') return;
    if (changes.enabled) state.enabled = !!changes.enabled.newValue;
    if (changes.captchaHighlight) state.captchaHighlight = !!changes.captchaHighlight.newValue;
    if (changes.webhookAuthorId) state.webhookAuthorId = changes.webhookAuthorId.newValue || '';
    applyTheme();
    refresh();
  });

  async function init() {
    // Load the packaged config.json (optional override of the defaults).
    try {
      const res = await fetch(chrome.runtime.getURL('config.json'));
      if (res.ok) {
        const cfg = await res.json();
        if (applyConfig(cfg)) log('loaded packaged config.json');
      }
    } catch (e) { /* packaged config is optional */ }

    chrome.storage.sync.get({ enabled: true, captchaHighlight: true, webhookAuthorId: '' }, (values) => {
      state.enabled = values.enabled !== false;
      state.captchaHighlight = values.captchaHighlight !== false;
      state.webhookAuthorId = values.webhookAuthorId || '';
      applyTheme();
      refresh();
      observer.observe(document.documentElement, { childList: true, subtree: true });
      // Discord's message list may load after document_idle — re-scan a few
      // times, but only if nothing was found yet (avoids churning a big channel).
      [1500, 4000, 9000].forEach((ms) => setTimeout(() => {
        if (stats.tagged === 0) refresh();
      }, ms));
      // Silent auto-update check (applies remote config/CSS when newer),
      // repeated every 6h for tabs that stay open.
      checkForUpdates(false);
      if (typeof setInterval === 'function') {
        setInterval(() => { checkForUpdates(false); }, 6 * 3600 * 1000);
      }
    });
  }

  // Used by the popup for live status, manual re-scan, updates, and the lock.
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || typeof msg.type !== 'string') return false;
    if (msg.type === 'LIMEY_STATUS') {
      sendResponse({
        active: true,
        host: location.hostname,
        tagged: stats.tagged,
        alerts: stats.alerts,
        noAuthorAttr: stats.noAuthorAttr,
        fallback: CONFIG.fallbackKeywordAlerts,
        ids: CONFIG.authorIds,
        names: CONFIG.authorNames,
        webhookAuthorId: state.webhookAuthorId,
        localVersion: chrome.runtime.getManifest().version || '0',
        configVersion: configVersion,
      });
    } else if (msg.type === 'LIMEY_RESCAN') {
      refresh();
      sendResponse({ active: true, tagged: stats.tagged, alerts: stats.alerts });
    } else if (msg.type === 'LIMEY_CHECK_UPDATES') {
      checkForUpdates(true).then(sendResponse);
      return true; // async response
    } else if (msg.type === 'LIMEY_LOCK_WEBHOOK') {
      // Report the author id of a webhook message (tag "WEBHOOK") so the popup
      // can lock the alert to exactly that webhook. Prefer messages that look
      // like captcha alerts, but accept ANY webhook message as a fallback so
      // the lock works even when no alert is currently on screen. `preferred`
      // tells the popup whether the match was a captcha-looking message or a
      // fallback (so it can warn the user to verify the right webhook).
      const rows = document.querySelectorAll(CONFIG.messageSelector);
      let anyWebhook = null;
      for (const row of rows) {
        if (!row.querySelector || !isWebhook(row)) continue;
        const id = authorIdOf(row);
        if (!id) continue;
        const nm = authorNameOf(row);
        if (!anyWebhook) anyWebhook = { id: id, name: nm };
        const text = (row.textContent || '').toLowerCase();
        if (isAlertWorthy(text)) {
          sendResponse({ ok: true, preferred: true, id: id, name: nm });
          return false;
        }
      }
      if (anyWebhook) {
        sendResponse({ ok: true, preferred: false, id: anyWebhook.id, name: anyWebhook.name });
        return false;
      }
      sendResponse({ ok: false });
    }
    return false;
  });

  init();
})();
