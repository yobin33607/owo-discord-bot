# Limey Captcha Alert Theme

A tiny browser extension that restyles **only the captcha-alert messages** the
Limey bot/webhook (ID `1514929209158402078`) posts into your channel, so they
stand out in Discord exactly like the Limey dashboard's **auth/login screen**
(dark red panel, corner brackets, glowing mono label).

Pure client-side CSS. The extension **does not read your token, does not touch
your session, and sends nothing anywhere** — it only injects a stylesheet into
`discord.com` and adds a CSS class to messages whose author id matches the
Limey bot. Exactly like any Discord theme/userstyle.

## What it looks like

Messages from the Limey bot become **alert boxes** styled like the dashboard's
login screen:

- Dark red panel with a scanline texture (`rgba(10,0,0,0.94)` over pure red
  `#ff0000` accents)
- **Corner brackets** in the corners of the card, like the auth card's terminal
  frame
- The bot's username rendered as a glowing, letter-spaced **red mono title**
- Message text in the auth screen's warm light (`#ffe0e0`)
- When the message text looks like a captcha alert, a **"⚠ CAPTCHA ALERT /
  SOLVE TO CONTINUE"** label appears on top with a **pulsing red glow** —
  impossible to miss while you're in the channel

Everything else in Discord is left untouched — it's **captcha alerts only**.

## Author detection is resilient

Discord hashes and renames its class names frequently, and has moved the
message author id attribute around. `content.js` therefore never relies on a
single selector:

- **Author id** — tries `data-author-id`, then `data-user-id`, then parses the
  author **avatar image URL** (`cdn.discordapp.com/avatars/<id>/…`, which is
  served for both users and webhooks).
- **BOT / APP / WEBHOOK tag** — tries the `botTag` class, then any pill/badge
  element whose exact text is one of the tag words.

If you ever see the console warning *"Could not determine the message
author…"* or *"captcha-like message whose author has no WEBHOOK/BOT tag"*, the
captcha alert message itself is being found but Discord changed its markup in a
way even the fallbacks can't read. Open F12 → Console on the channel with an
alert and run:

```js
const e = [...document.querySelectorAll('[class*="messageListItem"], li[data-list-item-id]')]
  .filter(e => /captcha|slow-down|autohunt/i.test(e.textContent)).pop();
e && copy(e.outerHTML.slice(0, 3000));
```

Paste the output here and the exact selectors will be updated.

Note: if the tag is missing entirely, the **on-screen lock** button needs a
WEBHOOK-tagged message to work — in that situation paste the webhook id from
Dashboard → Extension instead (that path never needs the tag).

## Captcha webhook id — from the server

The extension no longer needs a captcha alert on screen to know which webhook
posts them. The dashboard reads it straight from your bot's config:

- The captcha alerts in the channel are sent by the webhook configured at
  `security.webhook.url` in your settings (Configuration → Security).
- A Discord webhook URL embeds its id (`…/api/webhooks/<id>/<token>`), so the
  dashboard can show you that id without any Discord API call.
- **Dashboard → Extension** displays it with a **Copy ID** button; paste it
  into the extension popup's **Webhook ID** field.

If no security webhook is configured yet, the Extension tab will say so — set
one in Configuration → Security first.

## Auto-updates from your server

Chrome **cannot silently update a "Load unpacked" extension** (auto-update only
exists for Chrome Web Store / enterprise-managed installs) — so this extension
uses a **remote-config updater** instead, which covers everything that actually
breaks: Discord markup changes, new captcha wording, and styling tweaks.

**How it works:**

- Every installed copy checks `https://limeyself.onrender.com/ext/updates.json`
  on page load (and via the popup's **Check for updates** button). That
  endpoint is served by this dashboard app and is composed live from the same
  `manifest.json` + `config.json` + `styles.css` in this folder.
- When the server version is newer, the extension **hot-applies** the new
  targeting config (author ids/names, keywords, selectors) and the new CSS to
  the already-open Discord page — no reload, no reinstall.
- Full-code releases are rarer; the popup then shows the new version with a
  direct zip download link from the server.

**To ship a fix:** edit `config.json` (or `content.js`/`styles.css` for code/CSS
changes) → commit → the deploy workflow redeploys to Render → every installed
extension picks it up. No user action needed for config/CSS fixes.

**Security note:** this makes the extension trust `limeyself.onrender.com` —
that server can push new config and CSS (never arbitrary code, and nothing is
ever sent to it). That's fine for a personal extension; if you ever publish to
the Chrome Web Store, remote-config behavior must be disclosed in the privacy
listing and the update URL pointed at a store-approved host.

## Install from the Limey dashboard

If you run Limey with its dashboard, you don't need to clone anything — the
zip is **rebuilt automatically every time Limey boots** and is served by the
dashboard:

1. Open the dashboard → **Extension** tab.
2. Click **Download Extension (.zip)** (or open
   `http://localhost:8000/api/extension/download` directly).
3. Extract the zip anywhere, then follow the steps below to load it.

## Install

### Chrome / Edge / Brave

1. Download (or clone) this folder — it must keep its name `limey_discord_theme`.
2. Open `chrome://extensions` (Edge: `edge://extensions`).
3. Toggle **Developer mode** on (top-right).
4. Click **Load unpacked** and select the `limey_discord_theme` folder.
5. Open Discord — the alert boxes apply automatically.

### Firefox

1. Go to `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on…** and pick `limey_discord_theme/manifest.json`.
3. Open Discord. (Temporary add-ons reset when Firefox restarts — repackage as a
   signed .xpi if you want it permanent.)

## Usage

Click the extension icon → toggle:
- **Style Limey bot messages** — the auth-screen panel on messages from bot
  `1514929209158402078` on/off
- **CAPTCHA ALERT label** — the ⚠ label + pulsing glow (only when the message
  text looks like a captcha alert) on/off

Changes apply live; if Discord was already open, a refresh (Ctrl+R) forces them.

## Targeting: who gets the ⚠ label

The extension is deliberately conservative to avoid false positives
(`content.js`):

1. **Author id** — `LIMEY_AUTHOR_IDS` (default: `['1514929209158402078']`).
   Note: messages posted by a **webhook** carry the *webhook's* id, not the
   bot's client id — so if your alerts come from a webhook, id matching alone
   won't hit. Find the real id: F12 → inspect a message row → read
   `data-author-id`.
2. **Author name + tag** — `LIMEY_AUTHOR_NAMES` (default: `['limey']`, matched
   against the name shown above the message) but **only for authors that carry
   Discord's bot/webhook tag** (BOT / APP / WEBHOOK). A human user named
   "Limey" has no tag and is never styled.
3. **Alert phrasing, not bare mentions** — the ⚠ label is only given to
   messages that actually **demand** a captcha/verification:
   - direct signals: `slow-down`, `rate limited`, `autohunt`, `hcaptcha`,
     `recaptcha`, `⚠`
   - demand phrasing: "captcha/verify …" combined with `complete`, `solve`,
     `detected`, `required`, `please`, `continue`, `click`, … (either order)

   A message that merely **mentions** captcha — like the bot's stats
   announcement (`✅ 12 captchas solved`) — is never flagged. Webhook-keyword
   fallback uses the same rule, so the official Discord bot (tagged BOT) and
   human users never qualify.
4. **Webhook lock (recommended)** — two ways, no on-screen alert needed:
   - **From the server (best):** Dashboard → **Extension** tab shows the
     captcha alert webhook id read from your bot's settings
     (`security.webhook.url` — the webhook your bot posts its alerts
     through). Click **Copy ID**, then paste it into the popup's **Webhook ID
     field** and press **Use** — a full webhook URL pasted there works too
     (the id is extracted automatically).
   - **From the screen:** the popup's **"Lock to the captcha webhook on
     screen"** button picks the webhook from any WEBHOOK-tagged message in
     the current view (captcha-looking ones first, any webhook as fallback).

   Either way the ⚠ label becomes exclusive to that one webhook (saved in
   `chrome.storage.sync`). Click again / clear the field to unlock.

## Publishing to the Chrome Web Store / Edge Add-ons

First package the extension (this validates the manifest and zips it with
`manifest.json` at the archive root, which both stores require):

```bash
cd limey_discord_theme
python3 pack.py          # -> limey-captcha-alert-theme-1.5.1.zip
```

### Chrome Web Store (public)

1. Register at the [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole)
   — one-time **$5 fee** (use an email you'll keep; it can't be changed later).
2. Click **Add new item** → upload `limey-captcha-alert-theme-1.5.1.zip`.
3. Fill in the listing tabs:
   - **Store listing**: title, description, category, icon, **at least 1
     screenshot (1280×800 or 640×400)**, and the mandatory **small promo
     tile (440×280)**.
   - **Privacy**: declare the single purpose (restyling captcha-alert messages),
     justify the `storage` permission, and add a privacy policy URL if the form
     asks. (This extension collects no personal data — you can state that.)
   - **Distribution**: free / target regions.
4. **Submit for review** — typically a few days to a few weeks. Extensions with
   broad host permissions take longer; this one only asks for `storage`.

### Edge Add-ons (public)

1. Sign up in [Microsoft Partner Center](https://partner.microsoft.com/dashboard/microsoftedge) —
   **no fee**, verify a personal Microsoft account (individual accounts verify fast).
2. **Create new extension** → upload the same `.zip`.
3. Set visibility/markets, properties, and the privacy disclosures (single
   purpose, permission justification, remote-code = no).
4. Add a store listing per language — description minimum is 250 characters —
   plus a **300×300 logo** (or at least 128×128) and optional screenshots.
5. Submit for certification.

### Not ready for the stores yet?

For your own use (or to send the folder to a friend), you don't need any store:

- **Load unpacked**: `chrome://extensions` → Developer mode → **Load unpacked**
  (or Edge `edge://extensions`). No account, no fee, works instantly.
- **Pack a .crx**: same page → **Pack extension** → pick the folder → Chrome
  produces a `.crx` (installable) + `.pem` (keep the .pem — it's the signing key).

Notes:
- The store listing name mentions Discord only as the product it restyles;
  don't imply it's an official Discord/Google/Microsoft product.
- The CWS listing icon likes the artwork padded to ~96×96 inside 128×128 with
  transparency; the current full-bleed icon works, but padding it is nicer.

## Files

| File | Purpose |
|------|---------|
| `manifest.json` | Manifest V3 (Chrome/Edge/Firefox 109+) |
| `content.js` | Tags messages from the Limey bot (by `data-author-id`) + injects the stylesheet |
| `styles.css` | The auth-screen alert styling (attribute-contains selectors, like all Discord userstyles) |
| `popup.html` / `popup.js` | On/off toggles, persisted in `chrome.storage.sync` |
| `icons/` | Extension icons (`generate_icons.py` regenerates them, stdlib only) |

## Notes / troubleshooting

**"I loaded it but nothing happens" — check in this order:**

1. **Are you using Discord in the browser?** The extension only runs on
   `discord.com` in Chrome/Edge. The **Discord desktop app (Electron) can't
   run extensions** — you'll see nothing there. Use the browser version.
2. **Reload Discord** (Ctrl+R) after loading the extension — tabs that were
   already open before install don't get the content script until refreshed.
3. **Check the popup status panel** — the extension icon now shows a live
   report: whether the content script is running, how many messages were
   styled, and how many alerts were found. Use **Re-scan messages now** to
   force a re-scan.
4. **Check the browser console** (F12 → Console, filter `Limey Theme`): you
   should see `active on discord.com — N message(s) styled…`. If you see the
   "No data-author-id found" warning, the fallback is doing the work.
5. **Webhook vs bot id** — if alerts come from a webhook, its author id
   differs from the bot id. Easiest: use the popup's **"Lock to the captcha
   webhook on screen"** button — it pins the ⚠ label to exactly that webhook.
   You can also add the webhook's id to `LIMEY_AUTHOR_IDS` or its display name
   to `LIMEY_AUTHOR_NAMES` in `content.js`.
6. **A human named "Limey" or another bot is getting flagged?** — that should
   be impossible in 1.5.1: name matching requires a bot/webhook tag, and the
   keyword fallback only applies to messages tagged "WEBHOOK". If you still
   see it, Discord likely changed its tag markup — tell me and we'll update
   `TAG_SELECTOR` in `content.js`.

Other notes:

- Discord hashes its class names between releases, so the stylesheet uses
  `[class*="…"]` matches (the standard userstyle technique). If a Discord
  update breaks a rule, tweak the selector in `styles.css` and reload the
  extension.
- The box appears for **every** matched Limey message; the **"⚠ CAPTCHA
  ALERT"** label only shows when the text contains a captcha keyword
  (`captcha`, `autohunt`, `slow-down`, `verify`, `⚠️`, …). If your captcha
  webhook uses different wording, add the keyword to `KEYWORDS` in
  `content.js`.
- The only permission requested is `storage` (for the toggles) — no network
  access is granted. The content script merely runs on `discord.com` to
  inject CSS, which is why Chrome shows the usual "read and change your data
  on discord.com" notice when installing.
