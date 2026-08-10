/*
 * Limey extension bridge (page world)
 * ====================================
 * Lets dashboard pages talk to the Limey browser extension's content script.
 * The content script runs in an isolated world but shares the DOM, so we
 * exchange messages through CustomEvents:
 *
 *   page  --(limeyExtRequest)-->  content script  --(chrome.storage)-->  reply
 *   page  <--(limeyExtResponse)-- content script
 *
 * The content script marks the page with <html data-limey-ext="1"> so its
 * presence can be detected without any messaging.
 *
 * Requests: { type: 'hello' } | { type: 'get' } | { type: 'store', credential } | { type: 'clear' }
 */
(function () {
    'use strict';

    var REQ_EVENT = 'limeyExtRequest';
    var RESP_EVENT = 'limeyExtResponse';

    function request(type, payload, timeoutMs) {
        payload = payload || {};
        return new Promise(function (resolve) {
            var requestId = Math.random().toString(36).slice(2) + Date.now().toString(36);
            var settled = false;
            function finish(res) {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                document.removeEventListener(RESP_EVENT, onResponse);
                resolve(res);
            }
            var timer = setTimeout(function () {
                finish({ ok: false, error: 'timeout' });
            }, timeoutMs || 2500);

            function onResponse(e) {
                var d = (e && e.detail) || {};
                if (d.requestId !== requestId) return;
                finish(d);
            }

            // Both sides talk on `document` (the content script dispatches the
            // response there); using the same node avoids any event-path doubt.
            document.addEventListener(RESP_EVENT, onResponse);
            var detail = { requestId: requestId, type: type };
            Object.keys(payload).forEach(function (k) { detail[k] = payload[k]; });
            document.dispatchEvent(new CustomEvent(REQ_EVENT, { detail: detail }));
        });
    }

    function isInstalled() {
        return document.documentElement.getAttribute('data-limey-ext') === '1';
    }

    function waitForInstalled(timeoutMs) {
        timeoutMs = timeoutMs || 3000;
        return new Promise(function (resolve) {
            var start = Date.now();
            (function poll() {
                if (isInstalled()) return resolve(true);
                if (Date.now() - start > timeoutMs) return resolve(false);
                setTimeout(poll, 150);
            })();
        });
    }

    window.limeyExtBridge = {
        request: request,
        isInstalled: isInstalled,
        waitForInstalled: waitForInstalled
    };
})();
