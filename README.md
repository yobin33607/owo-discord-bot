<div align="center">

# LIMEY

**Discord Bot Ecosystem** • Moderation • Automation • Dashboard

[![Version](https://img.shields.io/badge/version-2.5.0-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)]()
[![License](https://img.shields.io/badge/license-GPLv3-red.svg)]()

</div>

---

> [!IMPORTANT]
> This project is for **educational purposes only**. Using self-bots violates Discord's Terms of Service. Use at your own risk in private servers only.

## What is Limey?

**Limey** is a full-featured Discord bot ecosystem combining a **web dashboard**, a **moderation bot**, and an **automation engine**. It offers real-time monitoring, moderation tools like warn/kick/ban/timeout, an appeals system, Discord AutoMod integration, and a self-bot automation platform — all controllable from a beautifully designed web interface.

---

## Features

### 🤖 Manager Bot
A full Discord bot (using `discord.py`) with:
- **Moderation** — `/warn`, `/kick`, `/ban`, `/timeout`, `/mute`, `/unmute`, `/purge`, `/lockdown`, `/slowmode`
- **Warn thresholds** — Auto-mute/kick/ban when warns reach configurable limits
- **AutoMod integration** — Listens to Discord's native AutoMod and DMs users when they trigger rules
- **Violations tracking** — All moderation actions stored with full history (`/violations`, `/clearviolations`)
- **Mod log channel** — All actions logged to a dedicated channel with rich embeds
- **Auto-unmute** — Automatically removes timed mutes when they expire
- **Appeal system** — Users can appeal bans/mutes directly via Discord or the web dashboard

### 🖥️ Web Dashboard
A premium web interface at `http://localhost:8000` featuring:
- **Live stats** — Real-time CPH, command counters, session analytics, cash history charts
- **Account management** — Add/remove/edit bot accounts with proxy assignment
- **Settings editor** — Full configuration UI with live save
- **Captcha solving** — Manual captcha solver panel with balance checks
- **Proxy manager** — Bulk import, auto-testing, health monitoring, auto-assignment
- **Appeals management** — Review, approve, or reject user appeals with violation history displayed
- **API key system** — Role-based API keys for external integrations
- **Discord OAuth** — Sign in or link your Discord account
- **Multi-user** — Role-based access (View, Manage, Admin)

### 🛡️ Moderation System
- **Warn** — Track and threshold users with auto-punishment
- **Kick / Ban** — With reason logging and violation storage
- **Timeout / Mute** — Duration-based with auto-unmute
- **Purge** — Bulk delete messages
- **Lockdown** — Lock/unlock channels
- **Slowmode** — Set channel slowmode
- **Mod log** — All actions logged with colored embeds
- **Violations** — Centralized violation history across all punishment types

### 📋 Appeals System
- **Submit appeals** — Via Discord with punishment type dropdown and evidence
- **Violation lookup** — Fetches the user's violations automatically; punishment type and reason are locked
- **Dashboard management** — Review, approve/reject with notes and violation history shown
- **DM notifications** — Users get DMed when their appeal is approved or rejected

### 🤖 Discord AutoMod Integration
- **Rule execution listener** — Detects when Discord's native AutoMod triggers
- **DM warnings** — Optionally sends users a DM explaining which rule they triggered
- **Rule name display** — Fetches and shows the AutoMod rule name from Discord
- **Toggle** — `/modsettings discordwarn on/off`

### 🧠 Automation Engine
- **Auto Hunt & Battle** — Smart scheduling with cooldown management
- **Quest Automation** — Auto-track, solve, and claim rewards
- **Gem Management** — Tier-based selection and dynamic gem sets
- **Gambling** — Martingale and flat betting strategies with stop-loss

### 🛡️ Security
- **Captcha solvers** — YesCaptcha, AntiCaptcha, NopeCHA, Captcha.ly, ONNX AI
- **Stealth mode** — Human-like typing, random delays, configurable presets
- **Webhook alerts** — Real-time security notifications
- **Auto-pause** — Pause on detection triggers

---

## Installation

### Windows

```bash
curl -o "%TEMP%\install_limey.bat" https://raw.githubusercontent.com/cubiced0/owo-discord-bot/main/install_limey.bat && "%TEMP%\install_limey.bat"
```

### Termux / Linux / MacOS

```bash
bash <(curl -s https://raw.githubusercontent.com/cubiced0/owo-discord-bot/main/install_limey.sh)
```

---

## Quick Start

1. **Install** using the commands above
2. **Run** `python limey_setup.py` to configure accounts and settings
3. **Start** with `python limey.py`
4. **Dashboard** opens at **http://localhost:8000**

### Default Login
- **Username:** `admin`
- **Password:** `12345678910`

---

## Manager Bot Setup

The Manager Bot is a **regular Discord bot** (not a self-bot) that provides moderation and appeals functionality.

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application and a bot user
3. Copy the bot token
4. Add it to your `config/settings.json`:

```json
{
  "manager_bot": {
    "token": "your-bot-token-here",
    "prefix": "!"
  }
}
```

### Manager Bot Commands

| Command | Description |
|---------|-------------|
| `!status` | Show all self-bots and their status |
| `!control start/stop <name>` | Resume/pause a self-bot |
| `!cash [name]` | Check cash balance(s) |
| `!logs [count] [name]` | View recent command logs |
| `!appeal` | Submit a moderation appeal |
| `!help` | Show help |

### Moderation Commands (Prefix + Slash)

| Command | Permission | Description |
|---------|-----------|-------------|
| `!warn` / `/warn` | moderate_members | Warn a member |
| `!kick` / `/kick` | kick_members | Kick a member |
| `!ban` / `/ban` | ban_members | Ban a user |
| `!timeout` / `/timeout` | moderate_members | Timeout a member |
| `!mute` / `/mute` | moderate_members | Mute a member |
| `!unmute` / `/unmute` | moderate_members | Unmute a member |
| `!purge` / `/purge` | manage_messages | Bulk delete messages |
| `!lockdown` / `/lockdown` | administrator | Lock/unlock a channel |
| `!slowmode` / `/slowmode` | manage_channels | Set channel slowmode |
| `!warnings` / `/warnings` | moderate_members | View warnings |
| `!clearwarns` / `/clearwarns` | administrator | Clear warnings |
| `!violations` / `/violations` | moderate_members | View violations |
| `!clearviolations` / `/clearviolations` | administrator | Clear violations |
| `!modlog` / `/modlog` | administrator | View moderation log |
| `!modsettings` / `/modsettings` | administrator | Toggle Discord AutoMod DM |

---

## Dashboard

Access at **http://localhost:8000**

Features:
- **Live bot stats** — Uptime, cash, command rates, status
- **Account controls** — Start/stop/pause individual bots
- **Settings editor** — Full configuration with live save
- **Proxy manager** — Import, test, assign proxies
- **Captcha solver** — Manual captcha submission
- **Appeals management** — Review and handle user appeals
- **Security alerts** — View and respond to captcha challenges
- **API key management** — Create and revoke API keys
- **User management** — Add/remove dashboard users with roles

---

## Screenshots

### Login Page
![Login](dashboard/static/assets/limey-auth.jpg)

### Dashboard Desktop
![Dashboard](dashboard/static/assets/limey-desktop-dash.jpg)

### Dashboard Mobile
![Dashboard](dashboard/static/assets/limey-mob.jpg)

---

## Disclaimer

This tool is for **educational purposes only**. Using self-bots violates Discord's Terms of Service. Use at your own risk in private servers only.

<div align="center">

### Limey

**Discord Bot Ecosystem** • Built by **LIMEY** • Made with ❤️

**⭐ Star this project if you find it useful!**

</div>
