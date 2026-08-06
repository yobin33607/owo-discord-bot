/* Limey Extension tab — shows the built browser extension status, the captcha
 * alert webhook id read from the server settings, and wires the download +
 * copy buttons. The zip is rebuilt on every Limey boot. */
(() => {
  'use strict';

  window.loadExtensionInfo = function () {
    const verEl = document.getElementById('ext-version');
    const subEl = document.getElementById('ext-sub');
    const btn = document.getElementById('ext-download-btn');
    if (!verEl) return;

    fetch('/api/extension/info')
      .then((r) => r.json())
      .then((d) => {
        if (!d || !d.success) throw new Error('bad response');
        verEl.textContent = d.name + ' — v' + d.version;
        if (d.built && d.size_bytes > 0) {
          subEl.textContent = 'Ready · ' + (d.size_bytes / 1024).toFixed(1) +
            ' KB · rebuilt whenever Limey boots';
          btn.classList.remove('disabled');
        } else {
          subEl.textContent = 'Not built yet — restart Limey (it rebuilds the zip at boot).';
          btn.classList.add('disabled');
        }
        renderWebhook(d.webhook_id);
      })
      .catch(() => {
        verEl.textContent = 'Extension info unavailable';
        subEl.textContent = 'The dashboard build step may have been skipped.';
      });
  };

  function renderWebhook(webhookId) {
    const title = document.getElementById('ext-webhook-title');
    const sub = document.getElementById('ext-webhook-sub');
    const idEl = document.getElementById('ext-webhook-id');
    const copyBtn = document.getElementById('ext-webhook-copy');
    if (!idEl) return;
    if (webhookId) {
      title.textContent = 'Captcha alert webhook';
      sub.textContent = 'Paste this id into the extension popup (Webhook ID field), or use the on-screen lock as a fallback. Read from your security webhook in settings (global first).';
      idEl.textContent = webhookId;
      idEl.dataset.id = webhookId;
      copyBtn.disabled = false;
    } else {
      title.textContent = 'Captcha alert webhook';
      sub.textContent = 'No security webhook found in settings. Set one in Configuration → Security (webhook url) — the alerts your bot posts into the channel come from that webhook.';
      idEl.textContent = '—';
      idEl.dataset.id = '';
      copyBtn.disabled = true;
    }
  }

  window.copyCaptchaWebhookId = function () {
    const idEl = document.getElementById('ext-webhook-id');
    const raw = (idEl && idEl.dataset.id) || '';
    if (!raw) return;

    const fallback = () => {
      if (typeof showToast === 'function') {
        showToast('Copy manually — select the id above', 'error');
      } else {
        idEl.textContent = 'Select the id above to copy it manually';
        setTimeout(() => { idEl.textContent = raw; }, 2500);
      }
    };

    // navigator.clipboard only exists in secure contexts — the dashboard can
    // be opened over LAN (http://<ip>:8000) where it is unavailable.
    if (!navigator.clipboard) {
      fallback();
      return;
    }
    navigator.clipboard.writeText(raw).then(() => {
      if (typeof showToast === 'function') {
        showToast('Webhook id copied — paste it into the extension popup', 'success');
      } else {
        idEl.textContent = '✓ Copied — paste into the extension popup';
        setTimeout(() => { idEl.textContent = raw; }, 2000);
      }
    }).catch(fallback);
  };

  document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('extension')) window.loadExtensionInfo();
  });
})();
