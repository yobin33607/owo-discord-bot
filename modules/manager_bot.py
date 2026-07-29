"""
Limey Manager Bot
=================
An official Discord bot that controls your self-bots via Discord commands.
Uses a regular bot token (not a self-bot token).

Communicates with the main Limey process via the dashboard API (localhost:8000).

Commands available:
  !status          — Show all self-bots and their status
  !control start/stop <account> — Resume or pause a self-bot
  !cash [account]  — Check cash balances
  !logs [count] [account] — View recent command logs
  !settings [section] — View current configuration
  !accounts        — List all configured accounts
  !help            — Show this help
"""

import discord
from discord.ext import commands
import json
import os
import time
import sys
import logging
import requests

_log = logging.getLogger("manager_bot")

# ── Config & API helpers ───────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
DASHBOARD_URL = "http://localhost:8000"
INTERNAL_KEY = os.environ.get("LIMEY_INTERNAL_KEY", "")

_HEADERS = {"Content-Type": "application/json"}
if INTERNAL_KEY:
    _HEADERS["X-Internal-Key"] = INTERNAL_KEY


def _api_get(path, params=None, timeout=5):
    """Helper: GET request to the dashboard API."""
    try:
        resp = requests.get(
            f"{DASHBOARD_URL}{path}",
            params=params,
            headers=_HEADERS,
            timeout=timeout,
        )
        if resp.status_code == 200:
            return resp.json()
    except requests.ConnectionError:
        pass
    except Exception:
        pass
    return None


def _api_post(path, data=None, timeout=5):
    """Helper: POST request to the dashboard API."""
    try:
        resp = requests.post(
            f"{DASHBOARD_URL}{path}",
            json=data,
            headers=_HEADERS,
            timeout=timeout,
        )
        if resp.status_code == 200:
            return resp.json()
    except requests.ConnectionError:
        pass
    except Exception:
        pass
    return None


def load_manager_config():
    """Load manager bot config from settings.json"""
    try:
        with open(SETTINGS_PATH, "r") as f:
            cfg = json.load(f)
        return cfg.get("manager_bot", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ── Bot Class ──────────────────────────────────────────


class ManagerBot(commands.Bot):
    def __init__(self):
        cfg = load_manager_config()
        token = cfg.get("token", "")
        prefix = cfg.get("prefix", "!")

        self.manager_token = token

        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(command_prefix=prefix, intents=intents, help_command=None)

    async def setup_hook(self):
        await self.add_cog(ManagerCommands(self))

    async def on_ready(self):
        _log.info(f"Logged in as {self.user} (ID: {self.user.id})")
        _log.info(f"Prefix: {self.command_prefix}")
        print(f"\n[Manager Bot] ✅ Logged in as {self.user} (ID: {self.user.id})")
        print(f"[Manager Bot]   Prefix: {self.command_prefix}")
        print(f"[Manager Bot]   Ready! Responds in any channel.\n")

    async def on_message(self, message):
        if message.author.bot:
            return
        await self.process_commands(message)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            await ctx.send(f"❌ Invalid argument: {error}\nUse `!help` to see command usage.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing required argument: {error}\nUse `!help` to see command usage.")
        elif isinstance(error, commands.CommandNotFound):
            pass
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Command on cooldown. Try again in {error.retry_after:.0f}s")
        else:
            _log.warning(f"Command error: {error}")
            await ctx.send(f"❌ An error occurred: {error}")


# ── Commands ───────────────────────────────────────────


class ManagerCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _fetch_accounts(self):
        """Fetch all running self-bots from the dashboard API."""
        return _api_get("/api/accounts/list") or []

    def _fetch_stats(self, account_id=None):
        """Fetch stats for a specific account (or first account if None)."""
        params = {"id": account_id} if account_id else {}
        return _api_get("/api/stats", params=params)

    def _fetch_logs(self):
        """Fetch command logs from the dashboard API."""
        return _api_get("/api/history") or []

    def _find_bot(self, query):
        """Find a self-bot by name, username, or ID via API."""
        accounts = self._fetch_accounts()
        if not query:
            return accounts
        q = query.lower()
        return [
            a for a in accounts
            if q in a.get("username", "").lower() or q in a.get("id", "")
        ]

    # ── Commands ──────────────────────────────────────

    @commands.command(name="status")
    async def cmd_status(self, ctx):
        """Show all self-bots and their current status"""
        accounts = self._fetch_accounts()
        if not accounts:
            await ctx.send("```❌ No self-bots are running.```")
            return

        lines = ["╔══════════════════════════════════════════════╗"]
        lines.append("║           🌐 LIMEY BOT STATUS              ║")
        lines.append("╚══════════════════════════════════════════════╝")
        lines.append("")

        for i, acc in enumerate(accounts, 1):
            name = acc.get("username", "Unknown")
            uid = acc.get("id", "N/A")
            cash = acc.get("cash", 0)
            paused = acc.get("paused", True)
            status = "🔴 PAUSED" if paused else "🟢 RUNNING"
            # Get uptime from stats endpoint
            stats = self._fetch_stats(uid)
            uptime = "N/A"
            if stats and isinstance(stats, dict):
                uptime = stats.get("uptime", "N/A") or "N/A"

            lines.append(f"  [{i}] {name}")
            lines.append(f"      ID: {uid}")
            lines.append(f"      Status: {status}")
            lines.append(f"      Cash: {cash:,}")
            lines.append(f"      Uptime: {uptime}")
            lines.append("")

        await ctx.send(f"```ansi\n{chr(10).join(lines)}```")

    @commands.command(name="control")
    async def cmd_control(self, ctx, action: str = "", *, query: str = ""):
        """Start or stop a self-bot. Usage: !control start <name> or !control stop <name>"""
        if action not in ("start", "stop", "pause", "resume"):
            await ctx.send("```Usage: !control <start|stop> <account name or ID>```")
            return

        accounts = self._fetch_accounts()
        if not accounts:
            await ctx.send("```❌ No self-bots running.```")
            return

        if query:
            targets = self._find_bot(query)
        else:
            targets = [accounts[0]] if accounts else []

        if not targets:
            await ctx.send(f"```❌ No bots found matching '{query or 'any'}'.```")
            return

        api_action = {"start": "start", "resume": "start", "stop": "stop", "pause": "stop"}.get(action, action)

        results = []
        for target in targets:
            name = target.get("username", "Unknown")
            aid = target.get("id", "")
            result = _api_post("/api/control", data={"action": api_action, "id": aid})
            if result and result.get("success"):
                status_label = "🟢 RESUMED" if api_action == "start" else "🔴 PAUSED"
                results.append(f"  {status_label} {name}")
            else:
                results.append(f"  ⚠️  {name} — API request failed")

        await ctx.send(
            f"```{'Control results:' if len(results) > 1 else 'Control result:'}\n"
            + "\n".join(results)
            + "```"
        )

    @commands.command(name="cash")
    async def cmd_cash(self, ctx, *, query: str = ""):
        """Check cash balances for self-bots"""
        if query:
            accounts = self._find_bot(query)
        else:
            accounts = self._fetch_accounts()

        if not accounts:
            await ctx.send("```❌ No bots found.```")
            return

        lines = ["💰  CASH REPORT"]
        lines.append("─" * 30)
        for acc in accounts:
            name = acc.get("username", "Unknown")
            cash = acc.get("cash", 0)
            lines.append(f"  {name:20s} : {cash:>12,}")
        lines.append("─" * 30)

        await ctx.send(f"```{chr(10).join(lines)}```")

    @commands.command(name="logs")
    async def cmd_logs(self, ctx, count_or_query: str = "10", *, query: str = ""):
        """View recent command logs. Usage: !logs [count] [account]"""
        try:
            count = int(count_or_query)
        except ValueError:
            count = 10
            query = f"{count_or_query} {query}".strip()

        all_logs = self._fetch_logs()

        if query:
            targets = self._find_bot(query)
            target_ids = {a.get("id") for a in targets}
            all_logs = [l for l in all_logs if str(l.get("bot_id", "")) in target_ids]

        if not all_logs:
            await ctx.send("```No logs found.```")
            return

        count = max(1, min(count, 30))
        recent = all_logs[-count:]

        lines = [f"📋  RECENT LOGS (last {count})"]
        lines.append("─" * 40)
        for log_entry in recent:
            ts = time.strftime(
                "%H:%M:%S",
                time.localtime(log_entry.get("timestamp", time.time())),
            )
            level = log_entry.get("level", "info").upper()[:4]
            msg = log_entry.get("message", "")
            bot_name = log_entry.get("bot_name", "")
            lines.append(f"  [{ts}][{level}] {bot_name or '':12s} {msg[:60]}")

        await ctx.send(f"```{chr(10).join(lines)}```")

    @commands.command(name="settings")
    async def cmd_settings(self, ctx, *, section: str = ""):
        """View current configuration. Usage: !settings [section]"""
        cfg = _api_get("/api/settings")
        if not cfg:
            await ctx.send("```❌ Failed to fetch settings. Is the dashboard running?```")
            return

        if section:
            parts = section.split(".")
            val = cfg
            for p in parts:
                if isinstance(val, dict):
                    val = val.get(p, "NOT FOUND")
                else:
                    val = "NOT FOUND"
                    break
            await ctx.send(f"```json\n{json.dumps(val, indent=2)[:1800]}```")
        else:
            lines = ["⚙️  SETTINGS OVERVIEW"]
            lines.append("─" * 40)
            for key, val in cfg.items():
                if isinstance(val, dict):
                    sub = ", ".join(list(val.keys())[:6])
                    if len(val) > 6:
                        sub += "..."
                    lines.append(f"  {key:20s} : {sub}")
                else:
                    lines.append(f"  {key:20s} : {str(val)[:40]}")
            await ctx.send(f"```{chr(10).join(lines)}```")

    @commands.command(name="accounts")
    async def cmd_accounts(self, ctx):
        """List all configured accounts"""
        accounts = self._fetch_accounts()
        if not accounts:
            await ctx.send("```📋  CONFIGURED ACCOUNTS\n─" + "─" * 40 + "\n  No accounts online.```")
            return

        lines = ["📋  CONFIGURED ACCOUNTS"]
        lines.append("─" * 40)
        for acc in accounts:
            uid = acc.get("id", "N/A")
            name = acc.get("username", "Unknown")
            paused = acc.get("paused", True)
            status = "🟢" if not paused else "🔴"
            lines.append(f"  {status} {name:20s} {uid}")
        lines.append("─" * 40)

        await ctx.send(f"```{chr(10).join(lines)}```")

    @commands.command(name="help")
    async def cmd_help(self, ctx):
        """Show available commands"""
        embed = discord.Embed(
            title="🤖 Limey Manager Bot",
            description="Control your self-bots via Discord commands",
            color=0xFF4444,
        )
        embed.add_field(
            name="Commands",
            value=(
                "`!status` — Show all self-bots and their status\n"
                "`!control start <name>` — Resume a self-bot\n"
                "`!control stop <name>` — Pause a self-bot\n"
                "`!cash [name]` — Check cash balance(s)\n"
                "`!logs [count] [name]` — View recent logs\n"
                "`!settings [section]` — View configuration\n"
                "`!accounts` — List all accounts\n"
                "`!help` — This message"
            ),
            inline=False,
        )
        embed.set_footer(text="Limey Manager Bot")
        await ctx.send(embed=embed)


# ── Entry Point ────────────────────────────────────────


def create_manager_bot():
    """Create and return a ManagerBot instance, or None if not configured."""
    cfg = load_manager_config()
    token = cfg.get("token", "")

    if not token:
        _log.info("Manager Bot not configured — skipping")
        return None

    return ManagerBot()


async def run_manager_bot():
    """Start the manager bot if configured. Call this as a background task."""
    bot = create_manager_bot()
    if bot is None:
        return

    try:
        await bot.start(bot.manager_token)
    except discord.LoginFailure:
        print("[Manager Bot] ❌ Login failed — check your bot token")
    except Exception as e:
        print(f"[Manager Bot] ❌ Error: {e}")
    finally:
        if not bot.is_closed():
            await bot.close()
