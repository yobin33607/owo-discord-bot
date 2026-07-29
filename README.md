<div align="center">

# Limey

**Advanced OwO Bot Automation** • Built by **LIMEY**

[![Version](https://img.shields.io/badge/version-2.4.3-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)]()
[![License](https://img.shields.io/badge/license-GPLv3-red.svg)]()

</div>

---

> [!IMPORTANT]
> WE ARE NOT responsible if you get banned using our selfbots. Selfbots are against Discord ToS and break OwO bot rules. Use only in private servers and do not openly share that you are using automation.

## What is Limey?

**Limey** is a powerful, fully-featured OWO-BOT automation tool offering a premium web dashboard. It allows you to monitor all your data in real-time through an easy-to-manage, beautifully designed interface.

---

## Features

- **🖥️ Full Web Dashboard**
  Real-time stats, charts, live configuration editor, captcha solving, proxy management, and account controls — all from an elegant web interface at `http://localhost:8000`.

- **🤖 Manager Bot**
  A built-in Discord bot (using standard `discord.py`) that lets you control your self-bots directly from Discord. Commands include `!status`, `!control start/stop`, `!cash`, `!logs`, `!settings`, and `!accounts`.

- **📱 Mobile Support (Termux)**
  Fully functional on Android devices with toast notifications and vibration support.

- **🧩 Smart Captcha Solvers**
  Multiple captcha service integrations (YesCaptcha, AntiCaptcha, NopeCHA, Capchaly), ONNX model for letterword captchas, and manual fallback.

- **👥 Multi-Account Manager**
  Run unlimited accounts simultaneously with independent settings, proxy assignments, and channel configurations.

- **🛡️ Advanced Stealth & Security**
  Realistic typing simulation, auto-pause on detection, configurable speed presets, webhook alerts, and multi-layer security to keep you safe.

- **🎲 Advanced Gambling**
  Smart betting strategies (Martingale, Flat) with configurable stop-loss, take-profit, max balance limits, and streak tracking.

- **🧠 Dynamic Quest Intelligence**
  Automatically completes quest checklists and tracks progression with smart timing.

- **💎 Advanced AutoGems**
  Automatically detect and equip the best gems based on your settings.

- **⚡ Smart Command Scheduling**
  Priority-based queue system with per-command cooldowns, auto-retry, and lazy loading.

- **🌐 Proxy Support**
  HTTP/HTTPS/SOCKS4/SOCKS5 proxies with per-account assignment, auto-testing, and bulk import.

- **📊 Live Analytics**
  Real-time CPH (cash per hour), command counters, session stats, and cash history charts.

- **🔧 Easy Setup**
  One-liner install scripts and an interactive setup wizard for quick configuration.

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

#### For Termux

Make sure to install the **Termux** and **Termux:API** apps from F-Droid or GitHub (grant the API app notifications permission). After the installation script finishes, follow the setup steps prompted by `limey_setup.py`. If you face issues with the basic installation, try the manual installation method.

---

## Quick Start

1. **Install** using the commands above
2. **Run** `python limey_setup.py` to configure accounts and settings
3. **Start** with `python limey.py` and select option `1`
4. **Dashboard** opens at **http://localhost:8000**

---

## Manager Bot

The Manager Bot is a **regular Discord bot** (not a self-bot) that lets you control your Limey self-bots via Discord commands. It runs as a separate subprocess using standard `discord.py`.

### Setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application and a bot user
3. Copy the bot token
4. Add it to your `config/settings.json` under `manager_bot.token`:

```json
{
  "manager_bot": {
    "token": "your-bot-token-here",
    "prefix": "!"
  }
}
```

### Commands

| Command | Description |
|---------|-------------|
| `!status` | Show all self-bots and their current status |
| `!control start <name>` | Resume a paused self-bot |
| `!control stop <name>` | Pause a running self-bot |
| `!cash [name]` | Check cash balance(s) |
| `!logs [count] [name]` | View recent command logs |
| `!settings [section]` | View current configuration |
| `!accounts` | List all accounts |
| `!help` | Show this help message |

---

## Dashboard

Once the bot is running, access the dashboard at:

**http://localhost:8000**

Default credentials (change immediately):
- **Username:** `admin`
- **Password:** `12345678910`

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

**Advanced OwO Bot Grinder** • Built by **LIMEY** • Made with ❤️

**⭐ Star this project if you find it useful!**

</div>
