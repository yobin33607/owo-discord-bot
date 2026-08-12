# This file is part of Limey.
# Copyright (c) 2025-Present Limey
#
# Limey is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with Limey. If not, see <https://www.gnu.org/licenses/>.


"""
Author: Limey
Limey - https://github.com/yobin33607/owo-discord-bot
"""


import discord
from discord.ext import commands
import json
import os
import time
import random
import asyncio
import re
import requests
from modules.limey_human import LimeyHuman
from modules.limey_logs import limey_logger
from modules.identity import IdentityManager
from component_v2_limey import setup_interactions
from modules.captcha_solver import setup_solver
from modules.web_solver import setup_web_solver
import core.state as state
import aiohttp
import unicodedata
import copy
import logging
from rich.console import Console
from utils.github_data_store import ghd

_log = logging.getLogger(__name__)

class LimeyBot(commands.Bot):
    def __init__(self, token=None, channels=None, proxy_url=None, proxy_auth=None, proxy_label="direct", guild_id=None, guild_name=None):
        self.session = None
        self.base_dir = state.BASE_DIR
        self.config_file = os.path.join(state.CONFIG_DIR, 'settings.json')
        
        self.console = Console()
        self.aliases = {}
        self.config = {}
        self.accounts = []
        self.token = token
        self.channels = channels or []
        self.proxy_url = proxy_url
        self.proxy_auth = proxy_auth
        self.proxy_label = proxy_label or "direct"
        self.guild_id = str(guild_id) if guild_id else None
        self.guild_name = guild_name or ""
        self._load_config()
        
        if not self.token or not self.channels:
            if self.accounts:
                primary = self.accounts[0]
                self.token = self.token or primary.get('token')
                self.channels = self.channels or primary.get('channels', [])
        
        self.channel_id = int(self.channels[0]) if self.channels else None
        
        core_cfg = self.config.get('core', {})
        self.prefix = core_cfg.get('prefix', 'owo ')
        self.user_id = core_cfg.get('user_id')
        self.owo_bot_id = str(core_cfg.get('monitor_bot_id', '408785106942164992'))
        self.owo_user = None
        
        super().__init__(
            command_prefix=self.prefix,
            self_bot=True,
            enable_debug_events=True,
            # Limey only sends commands and reacts to events — it never reads
            # historical messages. Capping the internal message cache keeps
            # memory flat on long-running instances (discord.py-self defaults
            # to 1000 messages per bot, which multiplies across accounts).
            max_messages=100,
            proxy=proxy_url,
            proxy_auth=proxy_auth,
        )
        
        self.username = "Bot"
        self.display_name = "Bot"
        self.nickname = None
        self.identifiers = []
        self.identity = IdentityManager(self)
        self.modules = {}
        self.active = True
        self.paused = False
        # Discord presence requested for this account: 'online' or 'offline'.
        # 'offline' makes the account appear offline/invisible in Discord while
        # the connection stays alive — this is what "stop" now means.
        self.presence_status = "online"
        self.warmup_until = time.time() + 10
        self.throttle_until = 0.0
        self.last_sent_time = 0
        self.last_sent_command = ""
        self.command_lock = asyncio.Lock()
        self.min_command_interval = 2.2
        self.command_history = []
        self.is_ready = False
        self.cmd_cooldowns = {}
        self.cmd_states = {}
        self.limey_queue = asyncio.PriorityQueue()
        self.limey_scheduler_task = None
        # Background tasks started in setup_hook — cancelled together when the
        # bot is shut down (memory watchdog) so nothing keeps the bot's object
        # graph alive and its memory can actually be freed.
        self._bg_tasks = []
        self.is_busy = False
        self.quest_grinder = None
        self.weapon_manager = None
        self.grind_active_time = 0.0
        self.last_break_check = 0.0
        self.is_on_break = False
        self.break_lock = asyncio.Lock()

        
        self.is_mobile = "TERMUX_VERSION" in os.environ or "com.termux" in os.environ.get("PREFIX", "")
        platform = "Mobile (Termux)" if self.is_mobile else "Desktop"
        _log.info(f"Initialized bot on platform: {platform}")
        
        # Store event loop reference for thread-safe scheduling from dashboard
        self._loop_ref = None

    @property
    def loop_ref(self):
        """Get the event loop safely, falling back to discord.py's loop attribute.

        Returns None when the loop isn't usable yet (e.g. the bot is still
        connecting) instead of discord.py-self's loop sentinel, which raises
        AttributeError when accessed from non-async contexts.
        """
        if self._loop_ref is not None:
            return self._loop_ref
        try:
            loop = self.loop
            # discord.py-self sets self.loop to a sentinel until the client is
            # initialised inside the event loop; any attribute access on it raises.
            if loop is not None and hasattr(loop, 'create_task'):
                return loop
        except (AttributeError, RuntimeError):
            pass
        return None

    async def setup_hook(self):
        # Capture the event loop for cross-thread scheduling (dashboard API)
        try:
            self._loop_ref = asyncio.get_running_loop()
        except RuntimeError:
            self._loop_ref = None

        if self.proxy_url and self.proxy_url.startswith(("socks4://", "socks5://")):
            try:
                from aiohttp_socks import ProxyConnector
                connector = ProxyConnector.from_url(self.proxy_url, rdns=True)
                self.session = aiohttp.ClientSession(connector=connector)
            except Exception:
                self.session = aiohttp.ClientSession()
        else:
            self.session = aiohttp.ClientSession()
        self.interactions = setup_interactions(self)
        self.captcha_solver = setup_solver(self)
        self.web_solver = setup_web_solver(self)
        self.log("SYS", "Initializing systems...")
        
        # Discord Quests / Orb Grinder (port of Discord-Quest-Auto-Completion-Selfbot)
        try:
            from modules.quest_grinder import QuestGrinder
            self.quest_grinder = QuestGrinder(self)
            self._bg_tasks.append(asyncio.create_task(self.quest_grinder.run()))
            self.log("SYS", "Orb Grinder initialized")
        except Exception as e:
            self.log("ERROR", f"Failed to initialize Orb Grinder: {e}")

        # Mass Dismantle / Weapon manager (owo weapons, owo sell/dismantle)
        try:
            from modules.weapon_manager import WeaponManager
            self.weapon_manager = WeaponManager(self)
            await self.add_cog(self.weapon_manager)
            self.log("SYS", "Mass Dismantle initialized")
        except Exception as e:
            self.log("ERROR", f"Failed to initialize Mass Dismantle: {e}")
        
        try:
            history = state.ht.load_history()
            state.ht.start_session(history)
        except Exception as e:
            self.log("ERROR", f"Failed to start history session: {e}")

        self._bg_tasks.append(asyncio.create_task(self._process_pending_commands()))
        self._bg_tasks.append(asyncio.create_task(self.limey_queue_worker()))
        self._bg_tasks.append(asyncio.create_task(self._track_active_time()))
        self._bg_tasks.append(asyncio.create_task(self._balance_monitor_worker()))
        self.limey_scheduler_task = asyncio.create_task(self.limey_scheduler_worker())
        self._bg_tasks.append(self.limey_scheduler_task)
        await self._load_cogs()
    
    async def _track_active_time(self):
        await self.wait_until_ready()
        while self.active:
            if not self.paused:
                self.grind_active_time += 1.0
            await asyncio.sleep(1.0)

    async def _balance_monitor_worker(self):
        """Check the account balance periodically (default every 5 minutes) and pause
        the bot if it drops more than a configurable amount below its starting
        balance (default -100). Uses the existing cash parser to refresh stats.
        Config: settings.json -> balance_monitor -> {enabled, interval, drop_limit}
        """
        await self.wait_until_ready()
        # The custom is_ready flag is set at the end of on_ready, which runs after
        # discord's internal ready event – make sure sends will actually go through.
        while not self.is_ready and self.active:
            await asyncio.sleep(1)

        try:
            cfg = self.config.get('balance_monitor', {})
            if not cfg.get('enabled', True):
                return
            interval = max(30, int(cfg.get('interval', 300)))
            drop_limit = max(0, int(cfg.get('drop_limit', 100)))
        except Exception:
            interval, drop_limit = 300, 100
        if not self.active:
            return

        self.log("SYS", f"Balance Monitor started (every {interval}s, stops if balance drops ≥{drop_limit} from start).")
        # Send an initial cash check so the starting-balance baseline gets captured
        await self._send_cash_check_and_wait()

        while self.active:
            try:
                await asyncio.sleep(interval)
                if not self.active or self.paused:
                    continue

                await self._send_cash_check_and_wait()

                st = self.stats
                current = st.get('current_cash')
                start = st.get('start_cash')
                if current is None or not start:
                    continue  # baseline not synced yet – skip this cycle

                drop = start - current
                if drop >= drop_limit:
                    self.log("ALARM", f"Balance Monitor: Balance dropped {drop:,} from start ({start:,} → {current:,}). Stopping bot.")
                    self.paused = True
                    self.throttle_until = float('inf')
                    state.log_command("SYS", f"Balance Monitor stopped {self.username} (dropped {drop:,} from start)", "warning", bot_name=self.username)
                    break
            except Exception as e:
                self.log("ERROR", f"Balance Monitor error: {e}")

    async def _send_cash_check_and_wait(self, timeout=8):
        """Send a cash check and wait until the parsed balance is refreshed.
        Returns True if the balance was updated, False on timeout."""
        before = self.stats.get('last_cash_update', 0)
        await self.send_message(f"{self.prefix}cash", skip_typing=True, priority=True)
        for _ in range(timeout):
            await asyncio.sleep(1)
            if self.stats.get('last_cash_update', 0) > before:
                return True
        return False

    async def _process_pending_commands(self):
        await asyncio.sleep(5)
        # Must exit when the bot is shut down — a `while True` here would keep
        # this task (and the whole bot object graph) alive forever, so the
        # memory watchdog could never actually free a disconnected account.
        while self.active:
            if not self.is_ready:
                await asyncio.sleep(1)
                continue
            
            st = self.stats
            if 'pending_commands' in st and st['pending_commands']:
                pending = st['pending_commands'][:]
                for cmd_data in pending:
                    if time.time() - cmd_data['timestamp'] < 300:
                        success = await self.send_message(cmd_data['command'])
                        if success:
                            st['pending_commands'] = [
                                c for c in st['pending_commands'] 
                                if c['timestamp'] != cmd_data['timestamp']
                            ]
                    else:
                        st['pending_commands'] = [
                            c for c in st['pending_commands'] 
                            if c['timestamp'] != cmd_data['timestamp']
                        ]
            await asyncio.sleep(2)
    
    def get_startup_delay(self, offset=0):
        return random.uniform(5, 15) + offset

    async def set_presence(self, status="online"):
        """Set the account's Discord presence.

        'offline' shows the account as offline/invisible in Discord while
        keeping the gateway connection alive (so it never disappears from the
        dashboard and doesn't need to re-login to come back). The requested
        state is stored on the bot so it also survives reconnects.
        """
        if status == "offline":
            self.presence_status = "offline"
        else:
            self.presence_status = "online"
        if not self.is_ready:
            # Applied automatically in on_ready once the account connects.
            return
        try:
            await self.change_presence(
                status=discord.Status.offline
                if self.presence_status == "offline"
                else discord.Status.online
            )
        except Exception as e:
            self.log("ERROR", f"Failed to set presence to {self.presence_status}: {e}")

    async def on_ready(self):
        if getattr(self, '_already_ready', False):
            # Reconnect: is_ready is still True, so apply the stored presence
            # now — a fresh gateway IDENTIFY resets presence to online and a
            # stopped account must be told to go invisible again.
            if getattr(self, "presence_status", "online") == "offline":
                try:
                    await self.set_presence("offline")
                except Exception:
                    pass
            _log.info(f"Reconnected as {self.user.name}")
            return

        self.user_id = str(self.user.id)
        self.username = self.user.name
        self.display_name = self.user.display_name
        self.user_display_name = self.display_name
        
        self.identifiers = [
            self.username.lower(),
            self.display_name.lower(),
            f"<@{self.user_id}>",
            f"<@!{self.user_id}>"
        ]

        if self.user_id not in state.account_stats:
            state.account_stats[self.user_id] = state.get_empty_stats()
        
        st = state.account_stats[self.user_id]
        st['username'] = self.username
        
        self._load_config()

        from modules.web_solver import setup_web_solver
        self.web_solver = setup_web_solver(self)
        self.log("SYS", "WebSolver reinitialized with account-specific settings.")

        if not st.get('uptime_start'):
            st['uptime_start'] = time.time()
        
        for counter in ['hunt_count', 'battle_count', 'owo_count', 'total_cmd_count', 'other_count', 'captchas_solved', 'bans_detected', 'warnings_detected']:
            if counter not in st: st[counter] = 0
            
        if 'cowoncy_history' not in st: st['cowoncy_history'] = []
        
        self.log("SYS", f"Ready as {self.username} (Display: {self.display_name})")
        
        self.cmd_states.clear()
        
        for cog in self.cogs.values():
            if hasattr(cog, 'register_actions'):
                try:
                    await cog.register_actions()
                except Exception as e:
                    self.log("ERROR", f"Failed to register {type(cog).__name__} actions: {e}")

        active_cmds = [f"{k}({v['delay']}s)" for k, v in self.cmd_states.items()]
        self.log("DEBUG", f"Active Scheduler: {', '.join(active_cmds) if active_cmds else 'None'}")
        
        self.interactions = setup_interactions(self)
        self.captcha_solver = setup_solver(self)
        self.web_solver = setup_web_solver(self)
        
        # ── Server binding: operate inside the chosen server only ─────────
        if self.guild_id:
            guild = None
            if str(self.guild_id).isdigit():
                guild = self.get_guild(int(self.guild_id))
                if guild is None:
                    try:
                        guild = await self.fetch_guild(int(self.guild_id))
                    except Exception:
                        guild = None
            if guild:
                self.guild_name = guild.name
                self.log("SYS", f"Operating in server: {guild.name} ({guild.id})")
                channels = guild.channels
                if not channels:
                    try:
                        channels = await guild.fetch_channels()
                    except Exception:
                        channels = []
                guild_ch_ids = {str(c.id) for c in channels}
                orig_len = len(self.channels)
                kept = [c for c in self.channels if str(c) in guild_ch_ids]
                if kept and len(kept) != orig_len:
                    self.channels = kept
                    self.log("SYS", f"Scoped channels to '{guild.name}': {len(kept)}/{orig_len} inside server")
                elif not kept and orig_len:
                    self.log("WARN", f"No configured channels are inside '{guild.name}' — using them anyway.")
                if self.channels and str(self.channel_id) not in [str(c) for c in self.channels]:
                    self.channel_id = int(self.channels[0])
            else:
                self.log("WARN", f"Server {self.guild_id} not found for this account — ignoring server binding.")
        else:
            self.log("SYS", "No server binding — operating on all configured channels.")

        self.log("INFO", f"Channel: {self.channel_id}")
        
        self.is_ready = True
        self._already_ready = True

        # First connect: is_ready was False during the re-apply check at the top
        # of on_ready, so apply a stored "offline" presence now that the account
        # is actually ready (a stopped bot must come back invisible).
        if getattr(self, "presence_status", "online") == "offline":
            try:
                await self.set_presence("offline")
            except Exception:
                pass

        if self.session is None:
            self.session = aiohttp.ClientSession()
    
    async def _send_safe(self, content, skip_typing=False, target_channel_id=None, priority=False):
        if not content or not self.is_ready:
            return False
            
        content = self._fix_command(content)

        async with self.command_lock:
            current_time = time.time()
            if current_time < self.warmup_until:
                 await asyncio.sleep(max(0.1, self.warmup_until - current_time))

            if current_time < self.throttle_until:
                wait = self.throttle_until - current_time
                if wait == float('inf'):
                    self.log("INFO", "Safety Pause: Paused until manually resumed or captcha solved")
                    while self.paused or self.throttle_until == float('inf'):
                        await asyncio.sleep(1)
                else:
                    self.log("INFO", f"Safety Pause: Resuming in {round(wait, 1)}s (Waiting for OwO Slow-Down)")
                    await asyncio.sleep(wait + 0.1)

            stealth_cfg = self.config.get('stealth', {})
            typing_enabled = stealth_cfg.get('typing_enabled', False)
            wait_limit = 0.0 if not typing_enabled else (1.2 if priority else self.min_command_interval)
            
            now = time.time()
            elapsed = now - self.last_sent_time
            if elapsed < wait_limit:
                await asyncio.sleep(wait_limit - elapsed)

            c_id = target_channel_id or self.channel_id
            channel = self.get_channel(c_id)
            if not channel:
                try:
                    channel = await self.fetch_channel(c_id)
                except Exception as e:
                    self.log("ERROR", f"Failed to fetch channel {c_id}: {e}")
                    return False
            
            if not channel:
                return False
            
            try:
                if typing_enabled and not skip_typing:
                    sent_ok = await LimeyHuman.limey_send(self, channel, content)
                    if not sent_ok:
                        return False
                else:
                    await channel.send(content)
                    
                self.last_sent_time = time.time()
                short_cmd = content[:30] + "..." if len(content) > 30 else content
                typing_str = ""
                if getattr(self, 'last_typing_time', None):
                    typing_str = f" ({self.last_typing_time}s)"
                    self.last_typing_time = None
                self.log("CMD", f"Sent: {short_cmd}{typing_str}")
                return True
            except Exception as e:
                self.log("ERROR", f"Send failed: {str(e)}")
                return False
    
    def _fix_command(self, command):
        cmd = command.strip()
        if cmd.lower() == "owo": return "owo"
        if cmd.lower().startswith("owo owo"): cmd = cmd[4:]
        
        if self.shortforms:
            parts = cmd.split()
            if parts:
                base_cmd = parts[0].lower()
                prefix = self.prefix.lower()
                
                actual_cmd = base_cmd[len(prefix):] if base_cmd.startswith(prefix) else base_cmd
                
                if actual_cmd in self.shortforms:
                    if self.config.get('commands', {}).get(actual_cmd, {}).get('use_shortform', False):
                        new_base = self.shortforms[actual_cmd]
                        parts[0] = f"{self.prefix}{new_base}" if base_cmd.startswith(prefix) else new_base
                        cmd = " ".join(parts)

        known = ['hunt', 'battle', 'curse', 'huntbot', 'daily', 'cookie',
                'quest', 'checklist', 'cf', 'slots', 'bj', 'blackjack', 'autohunt', 'upgrade',
                'sacrifice', 'team', 'zoo', 'use', 'inv', 'sell', 'crate',
                'lootbox', 'run', 'pup', 'piku','pray']
        
        if self.shortforms:
            for sf in self.shortforms.values():
                if sf not in known:
                    known.append(sf)

        first = cmd.lower().split()[0] if cmd else ""
        if first in known and not cmd.lower().startswith(self.prefix.lower()):
            return f"{self.prefix}{cmd}"
        return cmd
    
    async def send_message(self, content, skip_typing=False, priority=False, target_channel_id=None):
        if not self.active:
            return False
        if self.paused and "autohunt" not in content.lower() and "check" not in content.lower():
            return False
        
        if state.checking_gems.get(self.user_id):
            cmd_clean = content.lower().strip()
            if "hunt" in cmd_clean or "battle" in cmd_clean:
                if "huntbot" not in cmd_clean and "autohunt" not in cmd_clean:
                    return False

        fixed_content = self._fix_command(content)
        self.last_sent_command = fixed_content
        
        success = await self._send_safe(fixed_content, skip_typing=skip_typing, target_channel_id=target_channel_id, priority=priority)
        return success
    
    @property
    def stats(self):
        if not hasattr(self, '_connection') or not self.user: return {}
        uid = str(self.user.id)
        if uid not in state.account_stats:
            state.account_stats[uid] = state.get_empty_stats()
            state.account_stats[uid]['username'] = self.username
        return state.account_stats[uid]

    def log(self, log_type, message):
        limey_logger.log(self, log_type, message)

    async def _load_cogs(self):
        cogs_dir = os.path.join(self.base_dir, 'cogs')
        for filename in os.listdir(cogs_dir):
            if filename.endswith('.py'):
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    self.log("SYS", f"Loaded {filename}")
                except Exception as e:
                    self.log("ERROR", f"Failed to load {filename}: {e}")
    
    def _collect_changed_paths(self, old, new, prefix=""):
        """Return dotted paths that differ between two config dicts."""
        changed = set()
        if not isinstance(old, dict) or not isinstance(new, dict):
            p = prefix.rstrip(".")
            if p and old != new:
                changed.add(p)
            return changed
        for key in set(old.keys()) | set(new.keys()):
            path = f"{prefix}{key}" if prefix else key
            ov, nv = old.get(key), new.get(key)
            if isinstance(ov, dict) and isinstance(nv, dict):
                sub = self._collect_changed_paths(ov, nv, f"{path}.")
                if sub:
                    changed.add(path)
                    changed.update(sub)
            elif ov != nv:
                changed.add(path)
        return changed

    def _cogs_for_config_changes(self, changed_paths):
        """Map changed config paths to cog class names that need register_actions."""
        cog_names = set()
        cmd_to_cog = {
            "owo": "Grinding", "hunt": "Grinding", "battle": "Grinding",
            "coinflip": "Gambling", "slots": "Gambling",
            "curse": "LimeyCursePray", "pray": "LimeyCursePray",
            "shop": "Shop", "huntbot": "HuntBot", "daily": "Daily",
            "quest": "Quest", "rpp": "RPP", "cookie": "Cookie",
            "level_grind": "LevelQuotes",
        }
        top_to_cog = {
            "reactionBot": "ReactionBot",
            "security": "Security",
            "boss": "Boss",
            "utilities": "ChannelSwitch",
            "level_grind": "LevelQuotes",
        }
        for path in changed_paths:
            if path == "commands" or path.startswith("commands."):
                parts = path.split(".")
                if len(parts) >= 2:
                    cog_names.add(cmd_to_cog.get(parts[1], "Grinding"))
                else:
                    cog_names.update(cmd_to_cog.values())
            elif path.split(".")[0] in top_to_cog:
                cog_names.add(top_to_cog[path.split(".")[0]])
        return cog_names

    def _prune_disabled_scheduler_cmds(self):
        """Remove scheduler entries for commands that are now disabled."""
        cmds = self.config.get("commands", {})

        def enabled(name):
            return bool(cmds.get(name, {}).get("enabled", False))

        rules = [
            ("owo", enabled("owo")),
            ("hunt", enabled("hunt")),
            ("battle", enabled("battle")),
            ("coinflip", enabled("coinflip")),
            ("slots", enabled("slots")),
            ("blackjack", enabled("blackjack")),
            ("cursepray", enabled("curse") or enabled("pray")),
            ("daily", enabled("daily")),
            ("quest", enabled("quest")),
            ("rpp", enabled("rpp")),
            ("cookie", enabled("cookie")),
            ("huntbot", enabled("huntbot")),
            ("shop_buy", enabled("shop")),
            ("shop_cash_sync", enabled("shop")),
            ("level_quotes", self.config.get("level_grind", {}).get("enabled", False)),
            ("channelswitch", self.config.get("utilities", {}).get("autochannel", {}).get("enabled", False)),
        ]
        for cmd_id, is_on in rules:
            if not is_on and cmd_id in self.cmd_states:
                del self.cmd_states[cmd_id]

    async def sync_settings(self, new_config):
        """Merge settings and only refresh scheduler modules that actually changed."""
        old_config = copy.deepcopy(self.config)
        self._load_config()
        self._deep_merge(self.config, new_config)

        core_cfg = self.config.get("core", {})
        self.prefix = core_cfg.get("prefix", "owo ")
        if hasattr(self, "_connection"):
            self.command_prefix = self.prefix

        changed = self._collect_changed_paths(old_config, self.config)
        if not changed:
            self.log("SYS", "Settings saved (no changes detected).")
            return

        cogs_to_refresh = self._cogs_for_config_changes(changed)
        scheduler_paths = {
            p for p in changed
            if p == "commands" or p.startswith("commands.")
            or p.startswith("utilities.") or p in ("reactionBot", "level_grind")
            or p.startswith("reactionBot.")
        }

        if scheduler_paths:
            self._prune_disabled_scheduler_cmds()
            for cog in self.cogs.values():
                name = type(cog).__name__
                if name in cogs_to_refresh and hasattr(cog, "register_actions"):
                    try:
                        await cog.register_actions()
                    except Exception as e:
                        self.log("ERROR", f"Failed to refresh {name}: {e}")
            self.log("SYS", f"Settings updated ({len(changed)} change(s)). Scheduler modules refreshed: {', '.join(sorted(cogs_to_refresh)) or 'none'}")
        else:
            for cog in self.cogs.values():
                name = type(cog).__name__
                if name in cogs_to_refresh and hasattr(cog, "register_actions"):
                    try:
                        await cog.register_actions()
                    except Exception as e:
                        self.log("ERROR", f"Failed to refresh {name}: {e}")
            self.log("SYS", f"Settings updated ({len(changed)} change(s), no scheduler restart).")

        active_cmds = [f"{k}({round(v['delay'], 1)}s)" for k, v in self.cmd_states.items()]
        self.log("DEBUG", f"Active Scheduler: {', '.join(active_cmds) if active_cmds else 'None'}")

    def _deep_merge(self, base, override):
        for key, value in override.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def _load_config(self):
        try:
            cfg_data = ghd.read_json("config/settings.json", default={})
            if cfg_data:
                self.config = cfg_data
            else:
                self.config = {}

            uid = getattr(self, 'user_id', None)
            if not uid and hasattr(self, '_connection') and self.user:
                uid = str(self.user.id)
            
            if uid:
                user_cfg_path = f'config/settings_{uid}.json'
                user_cfg = ghd.read_json(user_cfg_path, default=None)
                
                if user_cfg:
                    self._deep_merge(self.config, user_cfg)
                    self.log("SYS", f"Using account-specific settings: settings_{uid}.json")
                else:
                    try:
                        ghd.write_json(user_cfg_path, self.config, message=f"Create settings_{uid}.json")
                        self.log("SYS", f"Created personal settings file: settings_{uid}.json")
                    except Exception as e:
                        self.log("ERROR", f"Failed to create settings_{uid}.json: {e}")
            else:
                self.log("SYS", "Using global settings: settings.json")

            account_data = ghd.read_json("config/accounts.json", default={})
            if account_data:
                self.accounts = account_data.get('accounts', [])
            else:
                self.accounts = []

            if self.accounts:
                current_acc = None
                if uid:
                    current_acc = next((a for a in self.accounts if str(a.get('id', a.get('user_id', ''))) == uid), None)
                if not current_acc and self.token:
                    current_acc = next((a for a in self.accounts if a.get('token') == self.token), None)
                
                if current_acc:
                    # Sync the server binding from accounts.json (set in the
                    # dashboard's server picker) so edits apply live.
                    self.guild_id = str(current_acc['guild_id']) if current_acc.get('guild_id') else None
                    self.guild_name = current_acc.get('guild_name') or ""
                    if self.guild_id:
                        self.log("SYS", f"Server binding: {self.guild_name or self.guild_id}")
                    new_channels = current_acc.get('channels', [])
                    if new_channels != self.channels:
                        self.channels = new_channels
                        if self.channels:
                            if str(self.channel_id) not in [str(c) for c in self.channels]:
                                self.channel_id = int(self.channels[0])
                                self.log("SYS", f"Channel rotated to {self.channel_id} (Config Update)")
                        self.log("SYS", f"Channels updated from accounts.json: {len(self.channels)} available")
                
                elif not self.channels:
                    primary = self.accounts[0]
                    self.channels = primary.get('channels', [])
                    self.channel_id = int(self.channels[0]) if self.channels else None

            shortform_data = ghd.read_json("config/shortform.json", default={})
            if shortform_data:
                self.shortforms = shortform_data
            else:
                self.shortforms = {}

            core_cfg = self.config.get('core', {})
            self.prefix = core_cfg.get('prefix', 'owo ')
            if hasattr(self, '_connection'):
                self.command_prefix = self.prefix

        except Exception as e:
            print(f"Error loading config: {e}")
            self.config = {}


    async def run_bot(self):
        # Capture the event loop BEFORE start() so dashboard can use it immediately
        self._loop_ref = asyncio.get_running_loop()
        route = f"via {self.proxy_label}" if self.proxy_label != "direct" else "direct connection"
        self.log("SYS", f"Starting bot ({route})...")
        await self.start(self.token)

    def set_cooldown(self, cmd, seconds):
        self.cmd_cooldowns[cmd.lower()] = time.time() + seconds

    def get_cooldown(self, cmd):
        return max(0, self.cmd_cooldowns.get(cmd.lower(), 0) - time.time())

    def get_cmd_priority(self, cmd_id, default=3):
        """load priority from cmd_priorities.json via GitHub, fallback to default."""
        try:
            priorities = ghd.read_json("config/cmd_priorities.json", default={})
            if priorities:
                return priorities.get(cmd_id, default)
        except Exception:
            pass
        return default

    def get_command_id_from_content(self, content):
        if not content:
            return None
        cmd_clean = content.lower().strip()
        prefix = self.prefix.lower().strip()
        if cmd_clean.startswith(prefix):
            cmd_clean = cmd_clean[len(prefix):].strip()
        elif cmd_clean.startswith("owo "):
            cmd_clean = cmd_clean[4:].strip()
        elif cmd_clean.startswith("uwu "):
            cmd_clean = cmd_clean[4:].strip()
            
        parts = cmd_clean.split()
        if not parts:
            return "owo"
            
        base = parts[0]
        alias_map = {
            "h": "hunt",
            "hunt": "hunt",
            "b": "battle",
            "battle": "battle",
            "fight": "battle",
            "pray": "cursepray",
            "curse": "cursepray",
            "cookie": "cookie",
            "rep": "cookie",
            "cf": "coinflip",
            "coinflip": "coinflip",
            "slots": "slots",
            "slot": "slots",
            "s": "slots",
            "daily": "daily",
            "rpp": "rpp",
            "owo": "owo"
        }
        return alias_map.get(base, base)


    def get_full_content(self, message):
        if not message: return ""
        content = message.content or ""
        embed_texts = []
        if message.embeds:
            for em in message.embeds:
                parts = [
                    em.title or "",
                    em.author.name if em.author else "",
                    em.description or "",
                    "\n".join([f"{f.name}: {f.value}" for f in em.fields])
                ]
                embed_texts.append("\n".join([p for p in parts if p]))
        return (content + "\n" + "\n".join(embed_texts)).lower()


    def is_message_for_me(self, message, role="any", keyword=None):
        return self.identity.is_message_for_me(message, role, keyword)

    async def limey_enqueue(self, content, priority=3, skip_typing=None, _cmd_id=None, target_channel_id=None):
        options = {"skip_typing": skip_typing, "_cmd_id": _cmd_id, "target_channel_id": target_channel_id}
        item = (priority, time.time(), content, options)
        await self.limey_queue.put(item)

    async def limey_queue_worker(self):
        await self.wait_until_ready()
        self.log("SYS", "LimeyQueue Worker started.")
        while self.active:
            try:
                priority, ts, content, options = await self.limey_queue.get()
                cmd_id = options.get("_cmd_id")
                target_channel_id = options.get("target_channel_id")

                ran_successfully = False
                
                try:
                    if content == "":
                        if cmd_id == "channelswitch":
                            cog = self.get_cog("ChannelSwitch")
                            if cog: cog.trigger_switch()
                        
                        if cmd_id and cmd_id in self.cmd_states:
                           self.cmd_states[cmd_id]['last_ran'] = time.time()
                        
                        ran_successfully = True
                        continue
                    
                    if self.paused and "autohunt" not in content.lower() and "check" not in content.lower():
                        continue

                    gem_check_val = state.checking_gems.get(self.user_id)
                    if gem_check_val:
                        timestamp = gem_check_val.get("time") if isinstance(gem_check_val, dict) else (time.time() if isinstance(gem_check_val, bool) else gem_check_val)
                        
                        if timestamp and time.time() - timestamp > 20:
                            self.log("WARN", "LimeyGems check timed out. Resuming queue.")
                            state.checking_gems[self.user_id] = False
                            gem_check_val = False
                    
                    if gem_check_val:
                        cmd_clean = content.lower().strip()
                        if "hunt" in cmd_clean or "battle" in cmd_clean:
                             if "huntbot" not in cmd_clean and "autohunt" not in cmd_clean:
                                continue

                    skip_typing = options.get("skip_typing")
                    if skip_typing is None:
                        skip_typing = priority <= 1 or content.lower().strip() == "owo"

                    if not cmd_id and content:
                        cmd_id = self.get_command_id_from_content(content)

                    if cmd_id and cmd_id in self.cmd_states:
                        state_info = self.cmd_states[cmd_id]
                        elapsed = time.time() - state_info['last_ran']
                        if elapsed < state_info['delay']:
                            remaining = state_info['delay'] - elapsed
                            if priority >= 4 and remaining > 60:
                                self.log("WARN", f"Quest Engine: Skipping '{content}' because '{cmd_id}' has a long remaining cooldown of {round(remaining, 1)}s")
                                continue
                            elif remaining <= 60:
                                self.log("INFO", f"Quest Engine: Deferring '{content}' for {round(remaining, 1)}s (Waiting for '{cmd_id}' cooldown)")
                                await asyncio.sleep(remaining + 0.5)

                    self.last_sent_command = content
                    await self._send_safe(content, skip_typing=skip_typing, target_channel_id=target_channel_id, priority=(priority <= 1))
                    ran_successfully = True
                    
                    if cmd_id and cmd_id in self.cmd_states:
                        self.cmd_states[cmd_id]['last_ran'] = time.time()
                    
                    if cmd_id and cmd_id in self.cmd_states:
                        if cmd_id in ["rpp", "quest", "level_quotes", "huntbot", "daily", "cookie", "coinflip", "slots", "blackjack"]:
                            class_map = {
                                "rpp": "RPP", "quest": "Quest", "level_quotes": "LevelQuotes", 
                                "huntbot": "HuntBot", "daily": "Daily", "cookie": "Cookie",
                                "coinflip": "Gambling", "slots": "Gambling", "blackjack": "Gambling"
                            }
                            
                            cog = self.get_cog(class_map[cmd_id])
                            if cog:
                                if cmd_id == "coinflip": getattr(cog, "trigger_coinflip")()
                                elif cmd_id == "slots": getattr(cog, "trigger_slots")()
                                elif cmd_id == "blackjack": getattr(cog, "trigger_blackjack")()
                                else: getattr(cog, "trigger_action")()
                
                finally:
                    if cmd_id and cmd_id in self.cmd_states:
                        self.cmd_states[cmd_id]['in_queue'] = False
                    self.limey_queue.task_done()

            except Exception as e:
                self.log("ERROR", f"Queue worker error: {e}")
                await asyncio.sleep(1)

    async def limey_register_command(self, cmd_id, content, priority, delay, initial_offset=0):
        existing = self.cmd_states.get(cmd_id, {})
        now = time.time()

        if existing and "last_ran" in existing:
            last_ran = existing["last_ran"]
            in_queue = existing.get("in_queue", False)
            old_delay = existing.get("delay", delay)
            if old_delay > 0 and abs(delay - old_delay) > 0.01:
                elapsed = max(0, now - last_ran)
                remaining_ratio = min(1.0, max(0, 1.0 - (elapsed / old_delay)))
                last_ran = now - (delay * (1.0 - remaining_ratio))
        else:
            last_ran = now - delay + initial_offset
            in_queue = False

        self.cmd_states[cmd_id] = {
            "content": content,
            "priority": priority,
            "delay": delay,
            "last_ran": last_ran,
            "in_queue": in_queue
        }

    async def limey_scheduler_worker(self):
        await self.wait_until_ready()
        self.log("SYS", "LimeyScheduler started.")
        while self.active:
            try:
                if self.paused:
                    await asyncio.sleep(1)
                    continue

                now = time.time()
                for cmd_id, state in list(self.cmd_states.items()):
                    if state["in_queue"]: continue
                    
                    if now - state["last_ran"] >= state["delay"]:
                        state["in_queue"] = True
                        actual_content = state["content"]
                        if callable(actual_content):
                            if asyncio.iscoroutinefunction(actual_content):
                                actual_content = await actual_content()
                            else:
                                actual_content = actual_content()
                        
                        if actual_content is not None:
                            asyncio.create_task(self.limey_enqueue(actual_content, priority=state["priority"], _cmd_id=cmd_id))
                        else:
                            state["in_queue"] = False
                            state["last_ran"] = time.time()

                await asyncio.sleep(1)
            except Exception as e:
                self.log("ERROR", f"Scheduler error: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(1)
