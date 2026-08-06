/* Limey Captcha Alert Theme — content script
 *
 * Styles captcha-alert messages as dark red auth-screen alert boxes (styles.css).
 *
 * Matching rules (designed to avoid false positives):
 *   1. Author ID — messages from the Limey bot id (LIMEY_AUTHOR_IDS).
 *   2. Author name — the display name, matched case-insensitively, but ONLY
 *      for authors that carry a bot/webhook tag (BOT / APP / WEBHOOK). A human
 *      user named "Limey" has no tag and is never matched.
 *   3. Keyword fallback — messages whose author shows the "WEBHOOK" tag AND
 *      whose text contains a strong captcha keyword (captcha, hcaptcha,
 *      recaptcha, autohunt, slow-down). The official Discord bot (tagged BOT)
 *      and human users are never flagged.
 *   4. Webhook lock (popup) — "Lock to the captcha webhook" pins the alert to
 *      a single webhook's author id; after locking, only that webhook (and the
 *      configured bot id) can trigger the ⚠ label.
 *
 * No data is read beyond what is already on screen, and nothing is sent
 * anywhere — 100% client-side CSS.
 */
(() => {
  'use strict';

  const STYLE_ID = 'limey-theme-style';
  const DEBUG = true; // set false to silence per-message logs

  // ── Targeting (edit these to match your setup) ─────────────────────────────
  const LIMEY_AUTHOR_IDS = ['1514929209158402078'];
  const LIMEY_AUTHOR_NAMES = ['limey']; // name match requires a bot/webhook tag
  const FALLBACK_KEYWORD_ALERTS = true; // WEBHOOK-tagged authors + strong keywords

  // Keywords that trigger the "CAPTCHA ALERT" label on CONFIRMED Limey messages
  // (matched by id, name+tag, or the locked webhook).
  const KEYWORDS = [
    'captcha',
    'hcaptcha',
    'recaptcha',
    'autohunt',
    'slow-down',
    'rate limited',
    'verify',
    'verification',
    '⚠️',
  ];

  // STRONG keywords also trigger the alert on unconfirmed WEBHOOK messages.
  const STRONG_KEYWORDS = ['captcha', 'hcaptcha', 'recaptcha', 'autohunt', 'slow-down'];

  // Message rows in Discord are <li> with hashed class names, e.g.
  // "messageListItem-..." — match by attribute prefix instead of exact name.
  const MESSAGE_SELECTOR = '[class*="messageListItem"], li[data-list-item-id]';
  // Author id attributes (Discord has historically used data-author-id; some
  // layouts use data-user-id) — we try several strategies below.
  const USER_ID_ATTRS = ['data-author-id', 'data-user-id'];
  // Avatar images are served from cdn.discordapp.com/avatars/<id>/<hash>.png
  // (and sometimes media.discordapp.net) for BOTH normal users and webhooks —
  // a stable fallback if the id attributes ever move (as the 'no
  // data-author-id' warning suggested). Attachment URLs never contain
  // 'avatars/', so a bare `img[src*="avatars/"]` search is safe.
  const AVATAR_SRC_RE = /avatars\/(\d{15,20})\//;
  const NAME_SELECTOR = '[class*="username"], [class*="authorName"], [class*="headerText"]';
  // The small pill next to the author name: text is "BOT", "APP" or "WEBHOOK".
  // Discord may rename the class, so tagTextOf() also scans pill/badge/tag
  // elements by their exact text.
  const TAG_SELECTOR = '[class*="botTag"]';
  const TAG_PILL_SELECTOR = '[class*="botTag"], [class*="tag"], [class*="badge"], [class*="pill"]';
  const TAG_WORDS = ['bot', 'app', 'webhook'];

  let styleEl = null;
  let state = { enabled: true, captchaHighlight: true, webhookAuthorId: '' };
  const stats = { tagged: 0, alerts: 0, noAuthorAttr: 0 };
  let warnedNoAuthor = false;
  let warnedNoTag = false;
  let lastLog = '';

  function log(msg, level) {
    if (!DEBUG) return;
    (console[level] || console.log)('[Limey Theme] ' + msg);
  }

  async function loadCss() {
    try {
      const res = await fetch(chrome.runtime.getURL('styles.css'));
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
      loadCss().then((css) => {
        if (css && el.isConnected) {
          el.textContent = css;
        } else if (el.isConnected) {
          el.remove();
          if (styleEl === el) styleEl = null;
        }
      });
      (document.head || document.documentElement).appendChild(el);
      styleEl = el;
    } else if (!state.enabled && styleEl) {
      styleEl.remove();
      styleEl = null;
    }
  }

  function clearAll() {
    document.querySelectorAll('.limey-captcha').forEach((el) => {
      el.classList.remove('limey-captcha', 'is-alert');
    });
    document.querySelectorAll('.limey-alert-label').forEach((el) => el.remove());
  }

  function messageRow(node) {
    if (!node) return null;
    if (node.closest) {
      const row = node.closest(MESSAGE_SELECTOR);
      if (row) return row;
    }
    return (node.matches && node.matches(MESSAGE_SELECTOR)) ? node : null;
  }

  function authorIdOf(node) {
    // 1) Author/user id attributes (self, descendants, ancestors).
    for (const attr of USER_ID_ATTRS) {
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
        const m = (img.getAttribute('src') || '').match(AVATAR_SRC_RE);
        if (m) return m[1];
      }
    }
    return '';
  }

  function authorNameOf(li) {
    if (!li.querySelector) return '';
    const el = li.querySelector(NAME_SELECTOR);
    return el ? (el.textContent || '').trim().toLowerCase() : '';
  }

  // Discord's author pill: "bot", "app" or "webhook". Humans have no tag.
  function tagTextOf(li) {
    if (!li.querySelector) return '';
    // 1) The known tag-pill class.
    let el = li.querySelector(TAG_SELECTOR);
    // 2) Fallback: any pill/badge/tag element whose exact text is one of
    //    Discord's author tags (survives class renames).
    if (!el && li.querySelectorAll) {
      const pills = li.querySelectorAll(TAG_PILL_SELECTOR);
      for (const p of pills) {
        const t = (p.textContent || '').trim().toLowerCase();
        if (TAG_WORDS.indexOf(t) !== -1) { el = p; break; }
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
      }
    }

    const text = (node.textContent || '').toLowerCase();
    const kwHit = KEYWORDS.some((k) => text.includes(k));
    const strongHit = STRONG_KEYWORDS.some((k) => text.includes(k));

    const idMatch = author !== '' && LIMEY_AUTHOR_IDS.includes(author);
    let nameMatch = false;
    let name = '';
    // Name matching requires a bot/webhook/app tag so a human named "Limey"
    // (no tag) is never styled.
    if (!idMatch && LIMEY_AUTHOR_NAMES.length && isTagged(li)) {
      name = authorNameOf(li);
      nameMatch = name !== '' && LIMEY_AUTHOR_NAMES.some((n) => nameMatches(name, n));
    }
    const isLimey = idMatch || nameMatch;

    const locked = state.webhookAuthorId !== '';
    const lockedMatch = locked && author === state.webhookAuthorId;

    // The ⚠ label:
    //  - confirmed Limey / locked webhook: any keyword triggers it
    //  - otherwise: ONLY messages tagged "WEBHOOK" with a strong keyword —
    //    humans and bots (like the official Discord account) never qualify.
    const fallbackEligible = FALLBACK_KEYWORD_ALERTS && !locked &&
      isWebhook(li) && strongHit;
    const applyAlert = (isLimey || lockedMatch)
      ? (kwHit && state.captchaHighlight)
      : (fallbackEligible && state.captchaHighlight);

    if (!isLimey && !lockedMatch && !applyAlert) {
      // Diagnostic: a captcha-like message was skipped. If its author has no
      // tag at all, Discord's tag markup may have changed and TAG_SELECTOR is
      // stale — otherwise this is a normal exclusion (human or non-webhook bot).
      if (strongHit && tagTextOf(li) === '' && !warnedNoTag) {
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
      root.querySelectorAll(MESSAGE_SELECTOR).forEach(tagMessage);
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
        if (node.matches && node.matches(MESSAGE_SELECTOR)) {
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
      (state.webhookAuthorId ? ` [locked to webhook ${state.webhookAuthorId}]` : '');
    if (msg !== lastLog) {
      lastLog = msg;
      log(msg);
    }
  }

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
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'sync') return;
    if (changes.enabled) state.enabled = !!changes.enabled.newValue;
    if (changes.captchaHighlight) state.captchaHighlight = !!changes.captchaHighlight.newValue;
    if (changes.webhookAuthorId) state.webhookAuthorId = changes.webhookAuthorId.newValue || '';
    applyTheme();
    refresh();
  });

  // Used by the popup for live status, manual re-scan, and the webhook lock.
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || typeof msg.type !== 'string') return false;
    if (msg.type === 'LIMEY_STATUS') {
      sendResponse({
        active: true,
        host: location.hostname,
        tagged: stats.tagged,
        alerts: stats.alerts,
        noAuthorAttr: stats.noAuthorAttr,
        fallback: FALLBACK_KEYWORD_ALERTS,
        ids: LIMEY_AUTHOR_IDS,
        names: LIMEY_AUTHOR_NAMES,
        webhookAuthorId: state.webhookAuthorId,
      });
    } else if (msg.type === 'LIMEY_RESCAN') {
      refresh();
      sendResponse({ active: true, tagged: stats.tagged, alerts: stats.alerts });
    } else if (msg.type === 'LIMEY_LOCK_WEBHOOK') {
      // Report the author id of a webhook message (tag "WEBHOOK") so the popup
      // can lock the alert to exactly that webhook. Prefer messages that look
      // like captcha alerts, but accept ANY webhook message as a fallback so
      // the lock works even when no alert is currently on screen. `preferred`
      // tells the popup whether the match was a captcha-looking message or a
      // fallback (so it can warn the user to verify the right webhook).
      const rows = document.querySelectorAll(MESSAGE_SELECTOR);
      let anyWebhook = null;
      for (const row of rows) {
        if (!row.querySelector || !isWebhook(row)) continue;
        const id = authorIdOf(row);
        if (!id) continue;
        const nm = authorNameOf(row);
        if (!anyWebhook) anyWebhook = { id: id, name: nm };
        const text = (row.textContent || '').toLowerCase();
        if (STRONG_KEYWORDS.some((k) => text.includes(k))) {
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
})();
