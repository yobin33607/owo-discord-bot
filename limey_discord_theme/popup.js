/* Limey Captcha Alert Theme — popup
 * Toggles, live status from the content script, manual re-scan, and locking
 * the ⚠ label to exactly one captcha webhook.
 */
(() => {
  'use strict';

  const enabled = document.getElementById('toggle-enabled');
  const captcha = document.getElementById('toggle-captcha');
  const statusEl = document.getElementById('status');
  const statusText = document.getElementById('status-text');
  const rescanBtn = document.getElementById('btn-rescan');
  const lockBtn = document.getElementById('btn-lock');
  const lockSub = document.getElementById('lock-sub');
  const whInput = document.getElementById('webhook-id-input');
  const applyBtn = document.getElementById('btn-apply-webhook');
  const updateText = document.getElementById('update-text');
  const updateBtn = document.getElementById('btn-update');

  // Where updates are fetched from — must match content.js UPDATE_URL.
  const UPDATE_URL = 'https://limeyself.onrender.com/ext/updates.json';
  const LOCAL_VERSION = chrome.runtime.getManifest().version || '0';

  let lockState = { id: '', name: '' };

  chrome.storage.sync.get({ enabled: true, captchaHighlight: true, webhookAuthorId: '', webhookAuthorName: '' }, (values) => {
    enabled.checked = values.enabled !== false;
    captcha.checked = values.captchaHighlight !== false;
    lockState.id = values.webhookAuthorId || '';
    lockState.name = values.webhookAuthorName || '';
    whInput.value = lockState.id;
    refreshLockUi();
  });

  function save() {
    chrome.storage.sync.set({
      enabled: enabled.checked,
      captchaHighlight: captcha.checked,
    });
  }

  enabled.addEventListener('change', save);
  captcha.addEventListener('change', save);

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function setStatus(html, cls) {
    statusText.innerHTML = html;
    statusEl.className = 'status ' + (cls || 'info');
  }

  function refreshLockUi() {
    if (lockState.id) {
      lockSub.textContent = 'Locked to webhook ' + lockState.id + (lockState.name ? ' (' + lockState.name + ')' : '') + ' — only it gets the ⚠ label';
      lockBtn.textContent = 'Unlock';
    } else {
      lockSub.textContent = 'Not locked — any webhook that posts a captcha alert is flagged';
      lockBtn.textContent = 'Lock to the captcha webhook on screen';
    }
  }

  function activeTab(cb) {
    chrome.tabs.query({ active: true, lastFocusedWindow: true }, (tabs) => {
      const tab = tabs && tabs[0];
      if (!tab || !tab.id) {
        setStatus('No active tab found.', 'error');
        return;
      }
      cb(tab);
    });
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

  function setUpdateStatus(html, cls) {
    updateText.innerHTML = html;
    updateBtn.textContent = cls === 'updating' ? 'Checking…' : 'Check for updates';
  }

  function fetchJson(url) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    return fetch(url, { cache: 'no-store', signal: ctrl.signal })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .finally(() => clearTimeout(timer));
  }

  function renderUpdateInfo(local, remote, applied) {
    if (remote && semverGt(remote, local)) {
      setUpdateStatus(
        'v' + escapeHtml(local) + ' installed · <b>v' + escapeHtml(remote) + ' available</b>' +
        (applied ? ' — applied to this page ✓' : ' — open Discord and press Check to apply.') +
        '<br><small>Full-code releases need the new zip: <a href="https://limeyself.onrender.com/ext/limey-captcha-alert-theme-' + escapeHtml(remote) + '.zip" target="_blank" rel="noopener">download v' + escapeHtml(remote) + '</a></small>',
        'warn'
      );
    } else if (remote && remote === local) {
      setUpdateStatus('v' + escapeHtml(local) + ' · <b>up to date</b>' + (applied ? ' (server config applied)' : ''), 'ok');
    } else {
      setUpdateStatus('v' + escapeHtml(local) + ' · server unreachable — auto-update will retry', 'error');
    }
  }

  function checkUpdatesFromPopup() {
    setUpdateStatus('Checking ' + UPDATE_URL + '…', 'updating');
    activeTab((tab) => {
      chrome.tabs.sendMessage(tab.id, { type: 'LIMEY_CHECK_UPDATES' }, (resp) => {
        if (chrome.runtime.lastError || !resp) {
          // No Discord tab / content script — still show the version info.
          fetchJson(UPDATE_URL).then((d) => renderUpdateInfo(LOCAL_VERSION, d ? String(d.version || '') : '', false));
          return;
        }
        if (resp.ok && resp.remote) {
          renderUpdateInfo(resp.local || LOCAL_VERSION, resp.remote, !!resp.updated);
        } else {
          renderUpdateInfo(LOCAL_VERSION, '', false);
        }
      });
    });
  }

  updateBtn.addEventListener('click', checkUpdatesFromPopup);

  function updateStatus() {
    setStatus('Checking Discord tab…', 'info');
    activeTab((tab) => {
      chrome.tabs.sendMessage(tab.id, { type: 'LIMEY_STATUS' }, (resp) => {
        if (chrome.runtime.lastError || !resp || !resp.active) {
          setStatus(
            'Not connected. The <b>active tab</b> must be Discord in this browser ' +
            '(not the desktop app) — switch to your Discord tab, press Ctrl+R, and ' +
            'reopen this popup.',
            'error'
          );
          return;
        }
        const host = escapeHtml(resp.host);
        const part = resp.tagged > 0
          ? resp.tagged + ' message(s) styled, ' + resp.alerts + ' alert(s)'
          : 'no captcha messages found yet';
        let extra = '';
        if (resp.webhookAuthorId) {
          extra = '<br><small>Locked to webhook ' + escapeHtml(resp.webhookAuthorId) + '.</small>';
        } else if (resp.tagged === 0 && resp.noAuthorAttr > 0) {
          extra = '<br><small>Author-id detection is off (Discord changed its DOM) — name/tag/keyword matching is being used.</small>';
        }
        setStatus('Running on <b>' + host + '</b> — ' + part + '.' + extra, resp.tagged > 0 ? 'ok' : 'warn');
      });
    });
  }

  rescanBtn.addEventListener('click', () => {
    activeTab((tab) => {
      chrome.tabs.sendMessage(tab.id, { type: 'LIMEY_RESCAN' }, (resp) => {
        if (chrome.runtime.lastError || !resp) {
          setStatus('Not connected to Discord.', 'error');
          return;
        }
        setStatus('Re-scanned — ' + resp.tagged + ' message(s) styled, ' + resp.alerts + ' alert(s).', 'ok');
      });
    });
  });

  function applyLock(id, name) {
    lockState.id = id;
    lockState.name = name || '';
    whInput.value = id;
    chrome.storage.sync.set({ webhookAuthorId: id, webhookAuthorName: lockState.name });
    refreshLockUi();
    // Saving storage triggers the content script's onChanged -> refresh.
    setStatus(
      'Locked to webhook ' + id + (lockState.name ? ' (' + escapeHtml(lockState.name) + ')' : '') +
      '. Only that webhook gets the ⚠ label now.',
      'ok'
    );
  }

  // Accept either a bare id or a pasted webhook URL (https://discord.com/
  // api/webhooks/<id>/<token>) — extract the id automatically.
  function normalizeWebhookId(raw) {
    let s = (raw || '').trim();
    const m = s.match(/\/api\/webhooks\/(\d+)/);
    if (m) s = m[1];
    return s;
  }

  applyBtn.addEventListener('click', () => {
    const id = normalizeWebhookId(whInput.value);
    if (!/^\d{15,20}$/.test(id)) {
      setStatus('Enter a valid webhook id or paste the full webhook URL — copy it from Dashboard → Extension.', 'error');
      return;
    }
    applyLock(id, '');
  });
  whInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') applyBtn.click();
  });

  lockBtn.addEventListener('click', () => {
    if (lockState.id) {
      // Clearing storage triggers the content script's onChanged -> refresh,
      // so the re-scan happens automatically.
      lockState.id = '';
      lockState.name = '';
      whInput.value = '';
      chrome.storage.sync.set({ webhookAuthorId: '', webhookAuthorName: '' });
      refreshLockUi();
      setStatus('Unlocked — any captcha webhook is flagged again.', 'info');
      return;
    }
    activeTab((tab) => {
      chrome.tabs.sendMessage(tab.id, { type: 'LIMEY_LOCK_WEBHOOK' }, (resp) => {
        if (chrome.runtime.lastError || !resp) {
          setStatus('Not connected to Discord.', 'error');
          return;
        }
        if (!resp.ok || !resp.id) {
          setStatus(
            'No webhook message on screen. Paste the webhook id from Dashboard → Extension into the field above instead.',
            'error'
          );
          return;
        }
        applyLock(resp.id, resp.name || '');
        if (resp.preferred === false) {
          setStatus(
            'Locked to webhook ' + resp.id + (resp.name ? ' (' + escapeHtml(resp.name) + ')' : '') +
            ' — but no captcha message was on screen, so it may not be the captcha webhook. ' +
            'Verify it against Dashboard → Extension.',
            'warn'
          );
        }
      });
    });
  });

  updateStatus();

  // Show the installed version immediately; fetch the remote version lazily.
  setUpdateStatus('v' + escapeHtml(LOCAL_VERSION) + ' · checking for updates…', 'info');
  fetchJson(UPDATE_URL).then((d) => {
    const remote = d ? String(d.version || '') : '';
    setUpdateStatus(
      'v' + escapeHtml(LOCAL_VERSION) + (remote && !semverGt(remote, LOCAL_VERSION)
        ? ' · <b>up to date</b>'
        : remote && semverGt(remote, LOCAL_VERSION)
          ? ' · <b>v' + escapeHtml(remote) + ' available</b> — press Check to apply'
          : ' · server unreachable'),
      remote && semverGt(remote, LOCAL_VERSION) ? 'warn' : 'ok'
    );
  });
})();
