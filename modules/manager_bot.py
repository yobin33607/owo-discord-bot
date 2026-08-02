"""
Limey Manager Bot
=================
An official Discord bot that controls your self-bots via Discord commands.
Uses a regular bot token (not a self-bot token).

Communicates with the main Limey process via the dashboard API (localhost:8000).

Commands available:
  !status            — Show all self-bots and their status
  !control <a> <n>   — Resume or pause a self-bot
  !cash [name]       — Check cash balances
  !logs [cnt] [n]    — View recent command logs
  !settings [sec]    — View configuration
  !accounts          — List all configured accounts
  !sync [guild_id]   — Sync slash commands (omit for global)
  !help              — Show this help

Slash commands:
  /status            — Show all self-bots and their status
  /control           — Resume or pause a self-bot
  /cash              — Check cash balances
  /logs              — View recent command logs
  /settings          — View configuration
  /accounts          — List all configured accounts
  /sync              — Sync slash commands to this server or globally
  /help              — Show available commands
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import time
import sys
import logging
import requests

_log = logging.getLogger("manager_bot")

# ── Config & API helpers ───────────────────────────────

from utils.github_data_store import ghd

DASHBOARD_URL = "http://localhost:8000"
INTERNAL_KEY = os.environ.get("LIMEY_INTERNAL_KEY", "")

# Default target guild for appeals (can be overridden in settings)
DEFAULT_APPEAL_GUILD_ID = 1514802189606977736

_HEADERS = {"Content-Type": "application/json"}
if INTERNAL_KEY:
    _HEADERS["X-Internal-Key"] = INTERNAL_KEY


def _load_appeals():
    """Load appeals from the GitHub data repo."""
    data = ghd.read_json("config/appeals.json", default=None)
    if data is not None:
        return data
    return {"appeals": [], "next_id": 1}


def _save_appeals(data):
    """Save appeals to the GitHub data repo."""
    ghd.write_json("config/appeals.json", data, message="Update appeals data")


def _add_appeal(username, user_id, punishment_type, reason, explanation, evidence):
    """Add a new appeal and return its ID."""
    data = _load_appeals()
    appeal_id = data["next_id"]
    data["next_id"] += 1
    data["appeals"].append({
        "id": appeal_id,
        "username": username,
        "user_id": user_id,
        "punishment_type": punishment_type,
        "reason": reason,
        "explanation": explanation,
        "evidence": evidence,
        "status": "pending",
        "created_at": time.time(),
        "reviewed_by": None,
        "review_notes": None,
    })
    _save_appeals(data)
    return appeal_id


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


def _load_mod_data():
    """Load moderation data from GitHub data repo."""
    data = ghd.read_json("config/moderation.json", default=None)
    if data is not None:
        return data
    return {"violations": {}, "next_violation_id": 1}


def _get_user_violations(guild_id, user_id):
    """Get all violations for a user in a guild from moderation.json via GitHub."""
    data = _load_mod_data()
    guild_key = str(guild_id)
    user_key = str(user_id)
    return data.get("violations", {}).get(guild_key, {}).get(user_key, [])


def load_manager_config():
    """Load manager bot config from settings.json via GitHub."""
    cfg = ghd.read_json("config/settings.json", default={})
    return cfg.get("manager_bot", {})


# ── Bot Class ──────────────────────────────────────────


class ManagerBot(commands.Bot):
    def __init__(self):
        cfg = load_manager_config()
        token = cfg.get("token", "")
        prefix = cfg.get("prefix", "!")
        auto_sync = cfg.get("auto_sync", True)
        sync_guilds = cfg.get("sync_guilds", [])

        self.manager_token = token
        self.auto_sync = auto_sync
        self.sync_guilds = sync_guilds

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        # moderation intent may not exist in older discord.py builds
        if hasattr(intents, 'moderation'):
            intents.moderation = True

        super().__init__(command_prefix=prefix, intents=intents, help_command=None)

    async def setup_hook(self):
        await self.add_cog(ManagerCommands(self))
        try:
            from modules.moderation import setup as setup_mod
            await setup_mod(self)
            _log.info("Moderation cog loaded")
            print("[Manager Bot] ✅ Moderation cog loaded")
        except Exception as e:
            _log.warning(f"Failed to load Moderation cog: {e}")
            print(f"[Manager Bot] ⚠️  Moderation cog failed to load: {e}")
        try:
            from modules.tickets import setup as setup_tickets
            await setup_tickets(self)
            _log.info("Tickets cog loaded")
            print("[Manager Bot] ✅ Tickets cog loaded")
        except Exception as e:
            _log.warning(f"Failed to load Tickets cog: {e}")
            print(f"[Manager Bot] ⚠️  Tickets cog failed to load: {e}")
        try:
            await self.add_cog(RoleManager(self))
            _log.info("RoleManager cog loaded")
            print("[Manager Bot] ✅ RoleManager cog loaded")
        except Exception as e:
            _log.warning(f"Failed to load RoleManager cog: {e}")
            print(f"[Manager Bot] ⚠️  RoleManager cog failed to load: {e}")
        try:
            from modules.verification import setup as setup_verification
            await setup_verification(self)
            _log.info("Verification cog loaded")
            print("[Manager Bot] ✅ Verification cog loaded")
        except Exception as e:
            _log.warning(f"Failed to load Verification cog: {e}")
            print(f"[Manager Bot] ⚠️  Verification cog failed to load: {e}")

    async def on_ready(self):
        _log.info(f"Logged in as {self.user} (ID: {self.user.id})")
        _log.info(f"Prefix: {self.command_prefix}")
        print(f"\n[Manager Bot] ✅ Logged in as {self.user} (ID: {self.user.id})")
        print(f"[Manager Bot]   Prefix: {self.command_prefix}")

        # ── Auto-sync slash commands on startup ────────
        if self.auto_sync:
            await self._sync_all_commands()

        print(f"[Manager Bot]   Ready! Responds in any channel.\n")

        # ── Start Role Manager ─────────────────────────
        role_mgr = self.get_cog("RoleManager")
        if role_mgr:
            role_mgr.check_roles.start()
            _log.info("RoleManager background loop started")

    async def _get_cog_app_commands(self):
        """Get ALL app commands from ALL loaded cogs.
        This ensures commands from both ManagerCommands and Moderation
        cogs are included in the sync.
        """
        all_cmds = []
        for cog in self.cogs.values():
            try:
                all_cmds.extend(cog.get_app_commands())
            except Exception:
                pass
        return all_cmds

    async def _remove_entry_point_commands(self, guild=None):
        """Remove any Entry Point (type 4) commands before bulk syncing.

        Discord added Entry Point commands (type 4) which cannot be removed
        via bulk upsert (PUT). They must be deleted individually first.

        Note: try_enum() returns a namedtuple-like proxy for unknown values,
        not a raw int, so we check .value directly.
        """
        try:
            existing = await self.tree.fetch_commands(guild=guild)
            for cmd in existing:
                # type 4 = entry point (not in AppCommandType which only has 1, 2, 3)
                if cmd.type.value == 4:
                    _log.info(f"Removing entry point command '{cmd.name}' (ID: {cmd.id})")
                    print(f"[Manager Bot]   🗑️  Removing entry point command '{cmd.name}'")
                    await cmd.delete()
        except Exception as e:
            _log.warning(f"Failed to check/remove entry point commands: {e}")

    async def _rebuild_and_sync(self, guild=None):
        """Rebuild the tree from current cog state and sync.

        This clears the tree's internal state for the given scope,
        re-adds only what the cog currently defines, then does a
        bulk PUT sync. This ensures old/stale commands are removed
        from Discord.
        """
        try:
            # 1. Get current commands from the cog (live state)
            current_cmds = await self._get_cog_app_commands()
            current_names = {cmd.name for cmd in current_cmds}

            # 2. Delete any entry point commands from Discord
            await self._remove_entry_point_commands(guild=guild)

            # 3. Fetch what Discord currently has
            discord_cmds = await self.tree.fetch_commands(guild=guild)

            # 4. Delete any Discord commands NOT in our current cog defs
            removed = 0
            for cmd in discord_cmds:
                # Type 4 handled above, skip regular commands we still want
                if cmd.type.value == 4:
                    continue
                if cmd.name not in current_names:
                    _log.info(f"Removing stale command '{cmd.name}' (ID: {cmd.id})")
                    print(f"[Manager Bot]   🗑️  Removing stale command '{cmd.name}'")
                    await cmd.delete()
                    removed += 1
            if removed:
                print(f"[Manager Bot]   🗑️  Removed {removed} stale slash command(s)")

            # 5. Clear the tree's local state for this scope
            self.tree.clear_commands(guild=guild)

            # 6. Re-add only current commands from the cog into the tree
            for cmd in current_cmds:
                self.tree.add_command(cmd, override=True, guild=guild)

            # 7. Bulk sync (PUT) — now the tree only has current commands
            result = await self.tree.sync(guild=guild)
            return result

        except Exception as e:
            _log.warning(f"Rebuild & sync failed: {e}")
            raise

    async def _sync_all_commands(self):
        """Sync all slash commands globally and to configured guilds.

        Performs a full rebuild-and-sync to ensure the local tree
        matches the current code, removing any stale/orphaned commands.
        """
        try:
            _log.info("Syncing global commands...")
            global_cmds = await self._rebuild_and_sync()
            _log.info(f"   Synced {len(global_cmds)} global command(s)")
            print(f"[Manager Bot]   🌐 Synced {len(global_cmds)} global slash command(s)")

            for guild_id in self.sync_guilds:
                try:
                    guild_obj = discord.Object(id=int(guild_id))
                    guild_cmds = await self._rebuild_and_sync(guild=guild_obj)
                    _log.info(f"   Synced {len(guild_cmds)} command(s) to guild {guild_id}")
                    print(f"[Manager Bot]   🏠 Synced {len(guild_cmds)} slash command(s) to guild {guild_id}")
                except Exception as e:
                    _log.warning(f"   Failed to sync to guild {guild_id}: {e}")
                    print(f"[Manager Bot]   ⚠️  Failed to sync to guild {guild_id}: {e}")

        except Exception as e:
            _log.warning(f"Failed to sync commands: {e}")
            print(f"[Manager Bot]   ⚠️  Slash command sync failed: {e}")

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


# ── Commands & Slash Commands ─────────────────────────


class ManagerCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Shared helpers ────────────────────────────────

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

    def _build_status_embed(self, accounts):
        """Build a status embed/list from accounts data."""
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

        return f"```ansi\n{chr(10).join(lines)}```"

    def _build_cash_text(self, accounts):
        """Build a cash report text block from accounts."""
        lines = ["💰  CASH REPORT"]
        lines.append("─" * 30)
        for acc in accounts:
            name = acc.get("username", "Unknown")
            cash = acc.get("cash", 0)
            lines.append(f"  {name:20s} : {cash:>12,}")
        lines.append("─" * 30)
        return f"```{chr(10).join(lines)}```"

    def _build_logs_text(self, logs, count):
        """Build a log text block from log entries."""
        lines = [f"📋  RECENT LOGS (last {count})"]
        lines.append("─" * 40)
        for log_entry in logs:
            ts = time.strftime(
                "%H:%M:%S",
                time.localtime(log_entry.get("timestamp", time.time())),
            )
            level = log_entry.get("level", "info").upper()[:4]
            msg = log_entry.get("message", "")
            bot_name = log_entry.get("bot_name", "")
            lines.append(f"  [{ts}][{level}] {bot_name or '':12s} {msg[:60]}")
        return f"```{chr(10).join(lines)}```"

    # ── Prefix Commands ───────────────────────────────

    @commands.command(name="status")
    async def cmd_status(self, ctx):
        """Show all self-bots and their current status"""
        accounts = self._fetch_accounts()
        if not accounts:
            await ctx.send("```❌ No self-bots are running.```")
            return
        await ctx.send(self._build_status_embed(accounts))

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

        await ctx.send(self._build_cash_text(accounts))

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

        await ctx.send(self._build_logs_text(recent, count))

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

    @commands.command(name="rolestatus")
    async def cmd_rolestatus(self, ctx):
        """Check role tier status for all self-bots. Shows cash, current role, and next tier target."""
        accounts = self._fetch_accounts()
        if not accounts:
            await ctx.send("```❌ No self-bots are running.```")
            return

        role_mgr = self.bot.get_cog("RoleManager")
        if not role_mgr:
            await ctx.send("```❌ RoleManager cog not loaded.```")
            return

        guild = role_mgr._find_role_guild()
        if not guild:
            await ctx.send("```❌ Could not find guild with tier roles. Is the bot in the right server?```")
            return

        lines = ["🎖️  ROLE STATUS REPORT"]
        lines.append("─" * 60)

        for acc in accounts:
            uid = acc.get("id", "")
            username = acc.get("username", "Unknown")
            cash = acc.get("cash", 0) or 0

            member = guild.get_member(int(uid)) if uid.isdigit() else None
            in_guild = "✅" if member else "❌"

            # Find current highest tier role
            current_role_name = "None"
            current_role_id = None
            if member:
                for _min_cash, rid in role_mgr.ROLE_TIERS:
                    role = guild.get_role(rid)
                    if role and role in member.roles:
                        current_role_name = role.name
                        current_role_id = rid
                        break

            # Determine target tier
            target_role_id = role_mgr._get_tier_for_cash(cash)
            target_name = "None"
            next_target = "—"
            if target_role_id:
                target_role = guild.get_role(target_role_id)
                target_name = target_role.name if target_role else f"ID {target_role_id}"
                # Find immediate next higher tier (iterate lowest-to-highest)
                for min_cash, rid in reversed(role_mgr.ROLE_TIERS):
                    if cash < min_cash:
                        next_role = guild.get_role(rid)
                        next_target = f"{next_role.name if next_role else f'ID {rid}'} ({min_cash:,} cash)"
                        break
            else:
                # Find the lowest tier as next target
                if role_mgr.ROLE_TIERS:
                    lowest_min, lowest_rid = role_mgr.ROLE_TIERS[-1]
                    lowest_role = guild.get_role(lowest_rid)
                    next_target = f"{lowest_role.name if lowest_role else f'ID {lowest_rid}'} ({lowest_min:,} cash)"

            role_status = "✅" if current_role_id == target_role_id else ("⚠️ Needs update" if target_role_id else "—")

            lines.append(f"  {in_guild} {username:20s}")
            lines.append(f"      Cash:        {cash:>12,}")
            lines.append(f"      Current:     {current_role_name}")
            lines.append(f"      Target:      {target_name} {role_status}")
            lines.append(f"      Next Tier:   {next_target}")
            lines.append("")

        lines.append("─" * 60)
        lines.append(f"  Guild: {guild.name} ({guild.id})")
        lines.append(f"  Accounts visible: {len(accounts)}")

        await ctx.send(f"```{chr(10).join(lines)}```")

    @commands.command(name="accounts")
    async def cmd_accounts(self, ctx):
        """List all configured accounts"""
        accounts = self._fetch_accounts()
        if not accounts:
            empty = "📋  CONFIGURED ACCOUNTS\n" + "─" * 40 + "\n  No accounts online."
            await ctx.send(f"```{empty}```")
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

    @commands.command(name="sync")
    async def cmd_sync(self, ctx, guild_id: str = ""):
        """Sync slash commands. Usage: !sync [guild_id] (omit for global)"""
        msg = await ctx.send("🔄 Syncing slash commands...")

        try:
            if guild_id:
                try:
                    gid = int(guild_id)
                except ValueError:
                    await msg.edit(content=f"❌ Invalid guild ID: `{guild_id}`")
                    return
                guild_obj = discord.Object(id=gid)
                cmds = await self.bot._rebuild_and_sync(guild=guild_obj)
                await msg.edit(content=f"✅ Synced {len(cmds)} slash command(s) to guild `{guild_id}`")
            else:
                cmds = await self.bot._rebuild_and_sync()
                await msg.edit(content=f"✅ Synced {len(cmds)} global slash command(s) 🌐")

            _log.info(f"Manual sync: {len(cmds)} commands synced (guild={guild_id or 'global'})")

        except Exception as e:
            _log.warning(f"Manual sync failed: {e}")
            await msg.edit(content=f"❌ Sync failed: {e}")

    @commands.command(name="help")
    async def cmd_help(self, ctx):
        """Show available commands"""
        embed = discord.Embed(
            title="🤖 Limey Manager Bot",
            description="Control your self-bots via Discord commands & slash commands",
            color=0xFF4444,
        )
        embed.add_field(
            name="Prefix Commands — Manager",
            value=(
                "`!status` — Show all self-bots and their status\n"
                "`!control start/stop <name>` — Resume/pause a self-bot\n"
                "`!cash [name]` — Check cash balance(s)\n"
                "`!logs [count] [name]` — View recent logs\n"
                "`!settings [section]` — View configuration\n"
                "`!accounts` — List all accounts\n"
                "`!sync [guild_id]` — Sync slash commands\n"
                "`!appeals [status]` — List appeals\n"
                "`!appeal <id> approve/reject [notes]` — Review an appeal\n"
                "`!modlog [count]` — View moderation log\n"
                "`!rolestatus` — Check role tier status for all self-bots\n"
                "`!help` — This message"
            ),
            inline=False,
        )
        embed.add_field(
            name="Prefix Commands — Moderation",
            value=(
                "`!warn <user> [reason]` — Warn a member\n"
                "`!warnings <user>` — View member warnings\n"
                "`!clearwarns <user> [id]` — Clear warnings\n"
                "`!kick <user> [reason]` — Kick a member\n"
                "`!ban <user> [days] [reason]` — Ban a user\n"
                "`!timeout <user> <dur> [reason]` — Timeout a member\n"
                "`!mute <user> <dur> [reason]` — Mute a member\n"
                "`!unmute <user> [reason]` — Unmute a member\n"
                "`!purge <count> [@user]` — Purge messages\n"
                "`!slowmode <sec>` — Set channel slowmode\n"
                "`!lock` / `!unlock` — Lock/unlock channel\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Prefix Commands — Tickets",
            value=(
                "`!ticketsetup` — Interactive ticket system setup\n"
                "`!ticketpanel` — Post the ticket creation panel\n"
                "`!ticketconfig` — Show ticket configuration\n"
                "`!close` — Close current ticket channel\n"
                "`!add <member>` — Add user to ticket (staff only)\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Prefix Commands — Verification",
            value=(
                "`!verifypanel` — Post the verification button panel\n"
                "`!verifyconfig` — View/set verification settings\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Slash Commands — Manager",
            value=(
                "`/status` — Show all self-bots and their status\n"
                "`/control` — Resume or pause a self-bot\n"
                "`/cash` — Check cash balance(s)\n"
                "`/logs` — View recent command logs\n"
                "`/settings` — View configuration\n"
                "`/accounts` — List all accounts\n"
                "`/sync` — Sync slash commands\n"
                "`/appeal` — Submit an appeal (mute/ban review)\n"
                "`/help` — Show this message"
            ),
            inline=False,
        )
        embed.add_field(
            name="Slash Commands — Moderation",
            value=(
                "`/warn <user> [reason]` — Warn a member\n"
                "`/warnings <user>` — View member warnings\n"
                "`/clearwarns <user> [id]` — Clear warnings\n"
                "`/kick <user> [reason]` — Kick a member\n"
                "`/ban <user> [days] [reason]` — Ban a user\n"
                "`/timeout <user> <dur> [reason]` — Timeout a member\n"
                "`/mute <user> <dur> [reason]` — Mute a member\n"
                "`/unmute <user> [reason]` — Unmute a member\n"
                "`/purge <count> [@user]` — Purge messages\n"
                "`/slowmode <sec>` — Set channel slowmode\n"
                "`/lock` / `/unlock` — Lock/unlock channel\n"
                "`/modlog [count]` — View moderation log\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Slash Commands — Tickets",
            value=(
                "`/ticket-setup` — Interactive ticket system setup\n"
                "`/ticket-panel` — Post the ticket creation panel\n"
                "`/ticket-config` — Show ticket configuration\n"
                "`/close` — Close current ticket channel\n"
                "`/ticket-add <member>` — Add user to ticket (staff only)\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Slash Commands — Verification",
            value=(
                "`/verifypanel` — Post the verification button panel\n"
                "`/verifyconfig` — View/update verification configuration\n"
            ),
            inline=False,
        )
        embed.set_footer(text="Limey Manager Bot")
        await ctx.send(embed=embed)

    # ── Slash Commands ────────────────────────────────

    @app_commands.command(name="status", description="Show all self-bots and their current status")
    async def slash_status(self, interaction: discord.Interaction):
        """Show all self-bots and their status"""
        accounts = self._fetch_accounts()
        if not accounts:
            await interaction.response.send_message("```❌ No self-bots are running.```", ephemeral=True)
            return
        await interaction.response.send_message(self._build_status_embed(accounts))

    @app_commands.command(name="control", description="Resume or pause a self-bot")
    @app_commands.describe(
        action="Action: resume or pause",
        account="Account name or ID to control",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Resume (start)", value="start"),
        app_commands.Choice(name="Pause (stop)", value="stop"),
    ])
    async def slash_control(self, interaction: discord.Interaction, action: str, account: str = ""):
        """Resume or pause a self-bot"""
        accounts = self._fetch_accounts()
        if not accounts:
            await interaction.response.send_message("```❌ No self-bots running.```", ephemeral=True)
            return

        if account:
            targets = self._find_bot(account)
        else:
            targets = [accounts[0]] if accounts else []

        if not targets:
            await interaction.response.send_message(
                f"```❌ No bots found matching '{account or 'any'}'.```", ephemeral=True
            )
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

        await interaction.response.send_message(
            f"```{'Control results:' if len(results) > 1 else 'Control result:'}\n"
            + "\n".join(results)
            + "```"
        )

    @app_commands.command(name="cash", description="Check cash balances for self-bots")
    @app_commands.describe(account="Optional: filter by account name or ID")
    async def slash_cash(self, interaction: discord.Interaction, account: str = ""):
        """Check cash balances for self-bots"""
        if account:
            accounts = self._find_bot(account)
        else:
            accounts = self._fetch_accounts()

        if not accounts:
            await interaction.response.send_message("```❌ No bots found.```", ephemeral=True)
            return

        await interaction.response.send_message(self._build_cash_text(accounts))

    @app_commands.command(name="logs", description="View recent command logs")
    @app_commands.describe(
        count="Number of log entries to show (max 30, default 10)",
        account="Optional: filter by account name or ID",
    )
    async def slash_logs(self, interaction: discord.Interaction, count: int = 10, account: str = ""):
        """View recent command logs"""
        all_logs = self._fetch_logs()

        if account:
            targets = self._find_bot(account)
            target_ids = {a.get("id") for a in targets}
            all_logs = [l for l in all_logs if str(l.get("bot_id", "")) in target_ids]

        if not all_logs:
            await interaction.response.send_message("```No logs found.```", ephemeral=True)
            return

        count = max(1, min(count, 30))
        recent = all_logs[-count:]

        await interaction.response.send_message(self._build_logs_text(recent, count))

    @app_commands.command(name="settings", description="View current configuration")
    @app_commands.describe(section="Optional: specific setting section to view (e.g. 'commands.hunt')")
    async def slash_settings(self, interaction: discord.Interaction, section: str = ""):
        """View current configuration"""
        cfg = _api_get("/api/settings")
        if not cfg:
            await interaction.response.send_message(
                "```❌ Failed to fetch settings. Is the dashboard running?```", ephemeral=True
            )
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
            await interaction.response.send_message(f"```json\n{json.dumps(val, indent=2)[:1800]}```")
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
            await interaction.response.send_message(f"```{chr(10).join(lines)}```")

    @app_commands.command(name="accounts", description="List all configured accounts")
    async def slash_accounts(self, interaction: discord.Interaction):
        """List all configured accounts"""
        accounts = self._fetch_accounts()
        if not accounts:
            empty = "📋  CONFIGURED ACCOUNTS\n" + "─" * 40 + "\n  No accounts online."
            await interaction.response.send_message(f"```{empty}```")
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

        await interaction.response.send_message(f"```{chr(10).join(lines)}```")

    @app_commands.command(name="sync", description="Sync slash commands (in a server = guild sync, in DMs = global)")
    async def slash_sync(self, interaction: discord.Interaction):
        """Sync slash commands to this guild or globally"""
        await interaction.response.defer(ephemeral=True)

        if interaction.guild_id:
            guild_obj = discord.Object(id=interaction.guild_id)
            try:
                cmds = await self.bot._rebuild_and_sync(guild=guild_obj)
                await interaction.followup.send(
                    f"✅ Synced {len(cmds)} slash command(s) to this server 🏠", ephemeral=True
                )
                _log.info(f"Guild sync ({interaction.guild_id}): {len(cmds)} commands")
            except Exception as e:
                await interaction.followup.send(f"❌ Sync failed: {e}", ephemeral=True)
                _log.warning(f"Guild sync failed ({interaction.guild_id}): {e}")
        else:
            try:
                cmds = await self.bot._rebuild_and_sync()
                await interaction.followup.send(
                    f"✅ Synced {len(cmds)} global slash command(s) 🌐", ephemeral=True
                )
                _log.info(f"Global sync: {len(cmds)} commands")
            except Exception as e:
                await interaction.followup.send(f"❌ Global sync failed: {e}", ephemeral=True)
                _log.warning(f"Global sync failed: {e}")

    @app_commands.command(name="help", description="Show available commands")
    async def slash_help(self, interaction: discord.Interaction):
        """Show available commands"""
        embed = discord.Embed(
            title="🤖 Limey Manager Bot",
            description="Control your self-bots & moderate your server",
            color=0xFF4444,
        )
        embed.add_field(
            name="Prefix Commands — Manager",
            value=(
                "`!status` — Show all self-bots and their status\n"
                "`!control start/stop <name>` — Resume/pause a self-bot\n"
                "`!cash [name]` — Check cash balance(s)\n"
                "`!logs [count] [name]` — View recent logs\n"
                "`!settings [section]` — View configuration\n"
                "`!accounts` — List all accounts\n"
                "`!sync [guild_id]` — Sync slash commands\n"
                "`!appeals [status]` — List appeals\n"
                "`!appeal <id> approve/reject [notes]` — Review an appeal\n"
                "`!modlog [count]` — View moderation log\n"
                "`!rolestatus` — Check role tier status for all self-bots\n"
                "`!help` — This message"
            ),
            inline=False,
        )
        embed.add_field(
            name="Prefix Commands — Moderation",
            value=(
                "`!warn <user> [reason]` — Warn a member\n"
                "`!warnings <user>` — View member warnings\n"
                "`!clearwarns <user> [id]` — Clear warnings\n"
                "`!kick <user> [reason]` — Kick a member\n"
                "`!ban <user> [days] [reason]` — Ban a user\n"
                "`!timeout <user> <dur> [reason]` — Timeout a member\n"
                "`!mute <user> <dur> [reason]` — Mute a member\n"
                "`!unmute <user> [reason]` — Unmute a member\n"
                "`!purge <count> [@user]` — Purge messages\n"
                "`!slowmode <sec>` — Set channel slowmode\n"
                "`!lock` / `!unlock` — Lock/unlock channel\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Prefix Commands — Tickets",
            value=(
                "`!ticketsetup` — Interactive ticket system setup\n"
                "`!ticketpanel` — Post the ticket creation panel\n"
                "`!ticketconfig` — Show ticket configuration\n"
                "`!close` — Close current ticket channel\n"
                "`!add <member>` — Add user to ticket (staff only)\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Prefix Commands — Verification",
            value=(
                "`!verifypanel` — Post the verification button panel\n"
                "`!verifyconfig` — View/set verification settings\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Slash Commands — Manager",
            value=(
                "`/status` — Show all self-bots and their status\n"
                "`/control` — Resume or pause a self-bot\n"
                "`/cash` — Check cash balance(s)\n"
                "`/logs` — View recent command logs\n"
                "`/settings` — View configuration\n"
                "`/accounts` — List all accounts\n"
                "`/sync` — Sync slash commands\n"
                "`/appeal` — Submit an appeal (mute/ban review)\n"
                "`/help` — Show this message"
            ),
            inline=False,
        )
        embed.add_field(
            name="Slash Commands — Moderation",
            value=(
                "`/warn <user> [reason]` — Warn a member\n"
                "`/warnings <user>` — View member warnings\n"
                "`/clearwarns <user> [id]` — Clear warnings\n"
                "`/kick <user> [reason]` — Kick a member\n"
                "`/ban <user> [days] [reason]` — Ban a user\n"
                "`/timeout <user> <dur> [reason]` — Timeout a member\n"
                "`/mute <user> <dur> [reason]` — Mute a member\n"
                "`/unmute <user> [reason]` — Unmute a member\n"
                "`/purge <count> [@user]` — Purge messages\n"
                "`/slowmode <sec>` — Set channel slowmode\n"
                "`/lock` / `/unlock` — Lock/unlock channel\n"
                "`/modlog [count]` — View moderation log\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Slash Commands — Tickets",
            value=(
                "`/ticket-setup` — Interactive ticket system setup\n"
                "`/ticket-panel` — Post the ticket creation panel\n"
                "`/ticket-config` — Show ticket configuration\n"
                "`/close` — Close current ticket channel\n"
                "`/ticket-add <member>` — Add user to ticket (staff only)\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Slash Commands — Verification",
            value=(
                "`/verifypanel` — Post the verification button panel\n"
                "`/verifyconfig` — View/update verification configuration\n"
            ),
            inline=False,
        )
        embed.set_footer(text="Limey Manager Bot")
        await interaction.response.send_message(embed=embed)

    # ── Appeal System ────────────────────────────────

    def _get_appeal_config(self):
        """Get appeal config from bot settings."""
        cfg = load_manager_config()
        return cfg.get("appeal", {})

    def _get_appeal_guild_id(self):
        """Get the target guild ID for appeals."""
        cfg = self._get_appeal_config()
        return int(cfg.get("guild_id", DEFAULT_APPEAL_GUILD_ID))

    def _get_appeal_channel_id(self):
        """Get the channel ID where appeal notifications are sent."""
        cfg = self._get_appeal_config()
        channel_id = cfg.get("channel_id", "")
        return int(channel_id) if channel_id else None

    async def _notify_appeal(self, appeal_id, username, punishment_type):
        """Send a notification embed to the appeals channel."""
        channel_id = self._get_appeal_channel_id()
        if not channel_id:
            return

        guild_id = self._get_appeal_guild_id()
        guild = self.bot.get_guild(guild_id)

        try:
            channel = await self.bot.fetch_channel(channel_id)
            if not channel:
                return

            embed = discord.Embed(
                title="🆕 New Appeal Submitted",
                color=0xFF4444,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Appeal ID", value=f"`#{appeal_id}`", inline=True)
            embed.add_field(name="User", value=username, inline=True)
            embed.add_field(name="Punishment", value=punishment_type, inline=True)
            embed.add_field(
                name="Review",
                value=f"Use `!appeal {appeal_id} approve/reject` to review",
                inline=False,
            )
            embed.set_footer(text="Appeal System", icon_url=guild.icon.url if guild and guild.icon else None)

            await channel.send(embed=embed)
        except Exception as e:
            _log.warning(f"Failed to send appeal notification: {e}")

    # ── Violation Select View ──────────────────────────

    class ViolationSelectView(discord.ui.View):
        """A view that shows the user's violations with a dropdown and continues to the appeal modal."""

        def __init__(self, cog, violations):
            super().__init__(timeout=120)
            self.cog = cog
            self.violations = violations

            # Build dropdown options from violations or static list
            options = []
            seen_types = set()
            for v in violations:
                vtype = v.get("type", "Unknown").capitalize()
                if vtype not in seen_types:
                    seen_types.add(vtype)
                    ts = time.strftime("%m/%d", time.localtime(v.get("timestamp", 0)))
                    reason = (v.get("reason") or "")[:60]
                    options.append(
                        discord.SelectOption(
                            label=vtype,
                            description=f"{ts} — {reason[:50]}",
                            value=vtype.lower(),
                            emoji={"warn": "⚠️", "kick": "👢", "ban": "🔨", "timeout": "🔇", "mute": "🔇"}.get(vtype.lower(), "📋"),
                        )
                    )

            # Add a "Other" option in case their specific punishment isn't listed
            if not any(o.value == "other" for o in options):
                options.append(
                    discord.SelectOption(label="Other", description="Something not listed above", value="other", emoji="📝")
                )

            self.punishment_select = discord.ui.Select(
                placeholder="📋 Select the punishment you're appealing...",
                options=options[:25],  # Discord max 25 options
                min_values=1,
                max_values=1,
            )
            self.punishment_select.callback = self._on_select
            self.add_item(self.punishment_select)

            self.selected_violation = None

        async def _on_select(self, interaction: discord.Interaction):
            selected_val = self.punishment_select.values[0]
            # Find the matching violation to get its reason
            for v in self.violations:
                if v.get("type", "").lower() == selected_val:
                    self.selected_violation = v
                    break
            if self.selected_violation is None and selected_val == "other":
                self.selected_violation = {"type": "Other", "reason": "Not specified"}
            # Enable the continue button
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = False
            await interaction.response.edit_message(view=self)

        @discord.ui.button(label="✏️  Continue to Appeal", style=discord.ButtonStyle.primary, disabled=True, row=1)
        async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            v = self.selected_violation or {}
            ptype = v.get("type", "other")
            reason = v.get("reason", "Not specified")
            modal = ManagerCommands.AppealModal(self.cog, punishment_type=ptype, mod_reason=reason)
            await interaction.response.send_modal(modal)

        async def on_timeout(self):
            # Disable all components on timeout
            for child in self.children:
                child.disabled = True
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    # ── Appeal Modal (updated) ────────────────────────

    class AppealModal(discord.ui.Modal):
        def __init__(self, cog, punishment_type="", mod_reason=""):
            self.cog = cog
            self._punishment_type = punishment_type
            self._mod_reason = mod_reason
            title = "📋 Submit an Appeal"
            if punishment_type and punishment_type.lower() != "other":
                title = f"📋 Appeal — {punishment_type.upper()}"
            super().__init__(title=title)

            # Only explanation and evidence — punishment type & reason
            # are auto-filled from violation records and cannot be edited
            self.explanation = discord.ui.TextInput(
                label="Why Should This Be Lifted?",
                placeholder="Explain your side of the story and why the punishment should be removed...",
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=1000,
            )
            self.add_item(self.explanation)

            self.evidence = discord.ui.TextInput(
                label="Evidence (optional)",
                placeholder="Links, screenshots, or any evidence supporting your appeal",
                style=discord.TextStyle.paragraph,
                required=False,
                max_length=1000,
            )
            self.add_item(self.evidence)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                username = interaction.user.name
                user_id = str(interaction.user.id)
                appeal_id = _add_appeal(
                    username=username,
                    user_id=user_id,
                    punishment_type=self._punishment_type,
                    reason=self._mod_reason,
                    explanation=self.explanation.value,
                    evidence=self.evidence.value or "None provided",
                )

                # Notify the appeals channel
                await self.cog._notify_appeal(
                    appeal_id=appeal_id,
                    username=username,
                    punishment_type=self._punishment_type,
                )

                embed = discord.Embed(
                    title="✅ Appeal Submitted",
                    description=f"Your appeal has been submitted and assigned ID `#{appeal_id}`.",
                    color=0x00FF88,
                )
                embed.add_field(
                    name="What happens next?",
                    value=(
                        "A moderator will review your appeal. "
                        "You will be notified here when a decision is made.\n\n"
                        "Please be patient — review times vary."
                    ),
                    inline=False,
                )
                embed.set_footer(text=f"Appeal #{appeal_id}")

                await interaction.response.send_message(embed=embed, ephemeral=True)
                _log.info(f"Appeal #{appeal_id} submitted by {interaction.user} ({interaction.user.id})")

            except Exception as e:
                _log.warning(f"Appeal submission error: {e}")
                await interaction.response.send_message(
                    f"❌ Failed to submit appeal: {e}", ephemeral=True
                )

        async def on_error(self, interaction: discord.Interaction, error):
            _log.warning(f"Appeal modal error: {error}")
            await interaction.response.send_message(
                "❌ An error occurred while submitting your appeal. Please try again later.",
                ephemeral=True
            )

    # ── Appeal Slash Command (updated) ───────────────

    @app_commands.command(
        name="appeal",
        description="Submit an appeal if you've been muted or banned in the server",
    )
    async def slash_appeal(self, interaction: discord.Interaction):
        """Open an appeal form to request a punishment review.
        First checks for violations, then shows a dropdown to select punishment type.
        """
        await interaction.response.defer(ephemeral=True)

        try:
            # Fetch violations from moderation.json
            violations = _get_user_violations(interaction.guild_id, interaction.user.id)

            if violations:
                # Build a nice embed showing violations
                embed = discord.Embed(
                    title="📋 Your Violations",
                    description=f"You have **{len(violations)}** violation(s) on record in this server.",
                    color=0xFF4444,
                    timestamp=discord.utils.utcnow(),
                )

                # Show most recent first
                violations_sorted = sorted(violations, key=lambda v: v.get("timestamp", 0), reverse=True)[:8]

                for v in violations_sorted:
                    vtype = v.get("type", "Unknown").upper()
                    ts = time.strftime("%m/%d %H:%M", time.localtime(v.get("timestamp", 0)))
                    reason = (v.get("reason") or "No reason")[:80]
                    emoji = {"warn": "⚠️", "kick": "👢", "ban": "🔨", "timeout": "🔇", "mute": "🔇"}.get(v.get("type", ""), "📋")
                    embed.add_field(
                        name=f"{emoji} {vtype} — {ts}",
                        value=f"Reason: {reason}",
                        inline=False,
                    )

                embed.set_footer(text="Select a punishment type below to start your appeal")

                view = ManagerCommands.ViolationSelectView(self, violations)
                msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                view.message = msg

            else:
                # No violations - let them appeal directly
                embed = discord.Embed(
                    title="✅ No Violations Found",
                    description=(
                        "You have **no violations** on record in this server.\n\n"
                        "If you believe you've been wrongly punished or still want to submit an "
                        "appeal, you can continue below."
                    ),
                    color=0x00FF88,
                )

                view = discord.ui.View(timeout=120)
                async def open_modal_cb(btn_interaction: discord.Interaction):
                    modal = ManagerCommands.AppealModal(self)
                    await btn_interaction.response.send_modal(modal)
                open_modal_cb.__name__ = "open_modal_callback"

                button = discord.ui.Button(label="✏️  Submit Appeal Anyway", style=discord.ButtonStyle.primary)
                button.callback = open_modal_cb
                view.add_item(button)

                await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            _log.warning(f"Appeal initiation error: {e}")
            # Fallback: send a message with a button to open the modal
            embed = discord.Embed(
                title="❌ Could not fetch violations",
                description=f"Error: {e}\n\nBut you can still submit your appeal using the button below.",
                color=0xFF4444,
            )
            view = discord.ui.View(timeout=120)
            async def fallback_cb(btn_interaction: discord.Interaction):
                modal = ManagerCommands.AppealModal(self)
                await btn_interaction.response.send_modal(modal)
            fallback_cb.__name__ = "fallback_callback"
            fallback_btn = discord.ui.Button(label="✏️  Submit Appeal", style=discord.ButtonStyle.primary)
            fallback_btn.callback = fallback_cb
            view.add_item(fallback_btn)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ── Appeal Prefix Commands ────────────────────────

    @commands.command(name="appeals")
    async def cmd_appeals(self, ctx, status: str = "pending"):
        """List appeals. Usage: !appeals [pending|approved|rejected|all]"""
        allowed_statuses = {"pending", "approved", "rejected", "all"}
        if status not in allowed_statuses:
            await ctx.send(f"```Usage: !appeals <pending|approved|rejected|all>```")
            return

        data = _load_appeals()
        appeals = data.get("appeals", [])

        if status != "all":
            appeals = [a for a in appeals if a.get("status") == status]

        if not appeals:
            await ctx.send(f"```📋 No {status} appeals found.```")
            return

        # Show most recent first
        appeals.sort(key=lambda a: a.get("created_at", 0), reverse=True)

        # Limit to first 15 for Discord message length
        shown = appeals[:15]

        lines = [f"📋  APPEALS ({status.upper()}) — {len(appeals)} total"]
        lines.append("─" * 50)
        for a in shown:
            aid = a.get("id", "?")
            user = a.get("username", "Unknown")
            ptype = a.get("punishment_type", "?").upper()
            created = time.strftime("%m/%d %H:%M", time.localtime(a.get("created_at", 0)))
            status_icon = {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(a.get("status", ""), "❓")
            lines.append(f"  {status_icon} #{aid} | {ptype:6s} | {created} | {user[:25]:25s}")

        if len(appeals) > 15:
            lines.append(f"  ... and {len(appeals) - 15} more")

        await ctx.send(f"```{chr(10).join(lines)}```")

    @commands.command(name="appeal")
    async def cmd_appeal(self, ctx, appeal_id: str, action: str = "", *, notes: str = ""):
        """Review an appeal. Usage: !appeal <id> approve|reject [notes]"""
        try:
            aid = int(appeal_id)
        except ValueError:
            await ctx.send(f"```❌ Invalid appeal ID: {appeal_id}```")
            return

        if action not in ("approve", "reject"):
            await ctx.send("```Usage: !appeal <id> approve|reject [notes]```")
            return

        data = _load_appeals()
        appeals = data.get("appeals", [])

        target = None
        for a in appeals:
            if a.get("id") == aid:
                target = a
                break

        if not target:
            await ctx.send(f"```❌ Appeal #{aid} not found.```")
            return

        if target.get("status") != "pending":
            await ctx.send(f"```⚠️ Appeal #{aid} is already {target['status']}.```")
            return

        new_status = "approved" if action == "approve" else "rejected"
        target["status"] = new_status
        target["reviewed_by"] = str(ctx.author)
        target["review_notes"] = notes or f"{action.capitalize()} by {ctx.author}"
        target["reviewed_at"] = time.time()

        _save_appeals(data)

        emoji = "✅" if new_status == "approved" else "❌"
        await ctx.send(f"```{emoji} Appeal #{aid} has been {new_status}.\nNotes: {target['review_notes']}```")

        # Try to DM the user about the result
        try:
            user_id = target.get("user_id", "")
            if user_id and user_id.isdigit():
                user = await self.bot.fetch_user(int(user_id))
                if user:
                    embed = discord.Embed(
                        title=f"{emoji} Appeal #{aid} — {new_status.upper()}",
                        description=f"Your appeal has been reviewed.",
                        color=0x00FF88 if new_status == "approved" else 0xFF4444,
                    )
                    embed.add_field(name="Decision", value=new_status.upper(), inline=True)
                    embed.add_field(name="Notes", value=target["review_notes"], inline=False)
                    embed.set_footer(text=f"Appeal #{aid} • Reviewed by {ctx.author}")
                    await user.send(embed=embed)
        except Exception as e:
            _log.warning(f"Failed to DM user about appeal #{aid}: {e}")

    # ── Interaction error handling ─────────────────────

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle slash command errors gracefully."""
        from discord.errors import InteractionResponded

        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"⏳ Command on cooldown. Try again in {error.retry_after:.0f}s", ephemeral=True
            )
        elif isinstance(error, app_commands.CommandNotFound):
            pass
        else:
            _log.warning(f"Slash command error: {error}")
            try:
                await interaction.response.send_message(
                    f"❌ An error occurred: {error}", ephemeral=True
                )
            except InteractionResponded:
                await interaction.followup.send(
                    f"❌ An error occurred: {error}", ephemeral=True
                )


# ── Role Manager ──────────────────────────────────────


# ── Role Manager ──────────────────────────────────────


class RoleManager(commands.Cog):
    """
    Background cog that assigns role tiers to self-bots based on their cash balance.
    Runs every 60 seconds, checking each self-bot's cash and assigning the highest
    matching tier role. Removes lower-tier roles when a higher tier is achieved.
    """

    # Role tiers: (min_cash, role_id) sorted highest to lowest
    ROLE_TIERS = [
        (500000, 1530189334643343420),  # 500k+
        (400000, 1532291694773534730),  # 400k+
        (300000, 1532291746829172846),  # 300k+
        (200000, 1532291817012465664),  # 200k+
        (100000, 1532291804869820537),  # 100k+
    ]
    ALL_ROLE_IDS = {rid for _, rid in ROLE_TIERS}

    def __init__(self, bot):
        self.bot = bot
        self._guild = None  # Cached guild where roles exist
        self._last_assignments = {}  # user_id -> role_id (to avoid redundant API calls)

    def _find_role_guild(self):
        """Find the guild that contains our role IDs by checking all guilds the bot is in."""
        for guild in self.bot.guilds:
            for role_id in self.ALL_ROLE_IDS:
                if guild.get_role(role_id):
                    _log.info(f"RoleManager: Found target guild {guild.id} ({guild.name})")
                    return guild
        return None

    async def _ensure_guild(self):
        """Ensure we have the guild cached."""
        if self._guild is None:
            self._guild = self._find_role_guild()
        return self._guild

    def _get_tier_for_cash(self, cash):
        """Return the role ID for the highest tier this cash qualifies for, or None."""
        for min_cash, role_id in self.ROLE_TIERS:
            if cash >= min_cash:
                return role_id
        return None

    def _get_lower_tier_role_ids(self, target_role_id):
        """Get all role IDs below the given tier that should be removed."""
        lower_ids = set()
        found = False
        for _min_cash, role_id in self.ROLE_TIERS:
            if role_id == target_role_id:
                found = True
            elif found:
                lower_ids.add(role_id)
        return lower_ids

    @tasks.loop(seconds=60)
    async def check_roles(self):
        """Periodically check cash balance of all self-bots and assign tier roles."""
        guild = await self._ensure_guild()
        if not guild:
            _log.warning("RoleManager: No guild found containing role IDs — retrying next cycle")
            return

        accounts = _api_get("/api/accounts/list")
        if not accounts:
            _log.debug("RoleManager: No accounts data from API")
            return

        for acc in accounts:
            try:
                user_id = str(acc.get("id", ""))
                cash = acc.get("cash", 0) or 0
                username = acc.get("username", "Unknown")

                if not user_id or not user_id.isdigit():
                    continue

                target_role_id = self._get_tier_for_cash(cash)

                member = guild.get_member(int(user_id))
                if not member:
                    try:
                        member = await guild.fetch_member(int(user_id))
                    except (discord.NotFound, discord.HTTPException):
                        continue

                if not member:
                    continue

                current_assignment = self._last_assignments.get(user_id)
                if current_assignment == target_role_id:
                    continue

                if target_role_id:
                    target_role = guild.get_role(target_role_id)
                    if not target_role:
                        _log.warning(f"RoleManager: Role {target_role_id} not found in guild")
                        continue

                    if target_role not in member.roles:
                        try:
                            await member.add_roles(target_role, reason=f"Cash tier: {cash:,}")
                            _log.info(f"RoleManager: Assigned {target_role.name} to {username} [cash: {cash:,}]")
                        except discord.Forbidden:
                            _log.warning(f"RoleManager: No permission to assign roles to {username}")
                            continue
                        except Exception as e:
                            _log.warning(f"RoleManager: Failed to assign role to {username}: {e}")
                            continue

                    lower_ids = self._get_lower_tier_role_ids(target_role_id)
                    roles_to_remove = []
                    for rid in lower_ids:
                        role = guild.get_role(rid)
                        if role and role in member.roles:
                            roles_to_remove.append(role)
                    if roles_to_remove:
                        try:
                            await member.remove_roles(*roles_to_remove, reason=f"Superseded by higher cash tier ({cash:,})")
                            _log.info(f"RoleManager: Removed {len(roles_to_remove)} lower tier role(s) from {username}")
                        except Exception as e:
                            _log.warning(f"RoleManager: Failed to remove roles from {username}: {e}")

                else:
                    roles_to_remove = []
                    for rid in self.ALL_ROLE_IDS:
                        role = guild.get_role(rid)
                        if role and role in member.roles:
                            roles_to_remove.append(role)
                    if roles_to_remove:
                        try:
                            await member.remove_roles(*roles_to_remove, reason=f"Cash below 100k ({cash:,})")
                            _log.info(f"RoleManager: Removed all tier roles from {username} (cash: {cash:,})")
                        except Exception as e:
                            _log.warning(f"RoleManager: Failed to remove roles from {username}: {e}")

                self._last_assignments[user_id] = target_role_id

            except Exception as e:
                _log.warning(f"RoleManager: Error processing account {acc.get('id', '?')}: {e}")

    @check_roles.before_loop
    async def before_check_roles(self):
        """Wait for the bot to be ready before starting the loop."""
        await self.bot.wait_until_ready()


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
