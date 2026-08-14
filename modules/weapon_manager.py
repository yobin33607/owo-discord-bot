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
Limey - https://github.com/limeyself/owo-discord-bot
"""


"""
Mass Dismantle / Weapon Manager
───────────────────────────────
Per-account manager for the dashboard's "Mass Dismantle" section.

  * `owo weapons`                → fetch the weapon list (IDs + names)
  * `owo sell <id>`              → sell one weapon
  * `owo dismantle <id>`         → dismantle one weapon (destroys it)
  * `owo sell all` / `owo dismantle all` → bulk actions

Attached to a `LimeyBot` instance as `bot.weapon_manager` and loaded as a cog
so it can listen for the OwO bot's response messages. Controlled from the
dashboard via the /api/weapons/* endpoints.
"""

import asyncio
import re
import time
from collections import deque

from discord.ext import commands

# Matches "ID: 123456789012345678" (optional parentheses/bold around it)
_ID_PATTERN = re.compile(r'\(?\bID:?\s*(\d{15,25})\)?', re.IGNORECASE)
# Matches a numbered list-item start: **1.** / 1. / 1) / 1:
_LINE_NUMBER = re.compile(r'^\s*(?:\*\*)?\d+(?:\.|\)|:)(?:\*\*)?\s*', re.IGNORECASE)
_MARKDOWN = re.compile(r'[*_~`]{1,2}')
_EMPTY_MARKERS = (
    "don't have any weapons",
    "you have no weapons",
    "no weapons",
    "you don't own any weapons",
    "weapon inventory is empty",
    "weapons list is empty",
    "no weapons found",
    "have no weapons",
)
# OwO rate-limit / cooldown replies — the busy bot can get these instead of the list.
_RATE_LIMIT_MARKERS = (
    "slow down",
    "too fast",
    "please wait",
    "try again in",
    "on cooldown",
    "rate limit",
)


class WeaponManager(commands.Cog):
    """Per-account weapon list fetcher + sell/dismantle actions (Mass Dismantle)."""

    def __init__(self, bot):
        self.bot = bot
        self.weapons = []        # [{"id": str, "name": str}]
        self.status = "idle"     # idle | fetching | done | empty | error
        self.last_error = ""
        self.last_fetch = 0.0
        self.paginated = False
        self.logs = deque(maxlen=100)
        self._waiting = False
        self._rate_limited = False
        self._event = asyncio.Event()
        self._lock = asyncio.Lock()

    # ── public control (called from the dashboard via run_coroutine_threadsafe) ──

    async def fetch_weapons(self, timeout=60, max_retries=2):
        """Send `owo weapons` and wait for the parsed response. Returns None on success.

        Uses priority 1 (highest) so the command jumps the command queue instead of
        waiting behind the bot's normal grinding commands. If OwO replies with a
        rate-limit message (common on busy accounts), it automatically re-sends.
        """
        if self._waiting:
            # Guard BEFORE the lock so a second call returns immediately
            # instead of queueing behind an in-progress fetch.
            return "Already fetching weapons..."
        async with self._lock:
            if self._waiting:
                return "Already fetching weapons..."
            if not getattr(self.bot, "is_ready", False):
                self.status = "error"
                self.last_error = "Bot is not ready yet"
                return self.last_error
            if getattr(self.bot, "paused", False):
                self.status = "error"
                self.last_error = "Bot is paused — resume it first (START on the dashboard)"
                return self.last_error
            if getattr(self.bot, "throttle_until", 0) == float("inf"):
                self.status = "error"
                self.last_error = "Bot is on a safety pause (captcha/cooldown) — resolve it in the Security tab first"
                return self.last_error

            self._waiting = True
            self.status = "fetching"
            self.weapons = []
            self.paginated = False
            self.last_error = ""
            deadline = time.time() + timeout
            attempts = 0
            try:
                while time.time() < deadline:
                    attempts += 1
                    self._event.clear()
                    self._rate_limited = False
                    self._log("INFO", f"Requesting owo weapons (attempt {attempts})...")
                    await self.bot.limey_enqueue("owo weapons", priority=1)
                    remaining = max(8, deadline - time.time())
                    try:
                        await asyncio.wait_for(self._event.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    if self.status in ("done", "empty"):
                        return None
                    if self._rate_limited and attempts <= max_retries:
                        self._log("WARN", "OwO rate-limited the request — retrying in a moment...")
                        await asyncio.sleep(4)
                        continue
                    break
            finally:
                self._waiting = False

            self.status = "error"
            if not self.last_error:
                self.last_error = (
                    "Timed out waiting for the weapons response. Check the terminal for "
                    "'Sent: owo weapons' and any '[Mass Dismantle]' logs around it."
                )
            self._log("ERROR", self.last_error)
            return self.last_error

    async def sell_weapon(self, weapon_id):
        return await self._send_action("sell", weapon_id)

    async def dismantle_weapon(self, weapon_id):
        return await self._send_action("dismantle", weapon_id)

    async def sell_all(self):
        return await self._send_action("sell", "all")

    async def dismantle_all(self):
        return await self._send_action("dismantle", "all")

    # ── dashboard-facing snapshot ─────────────────────────

    def status_dict(self):
        return {
            "weapons": list(self.weapons),
            "status": self.status,
            "last_error": self.last_error,
            "last_fetch": self.last_fetch,
            "paginated": self.paginated,
            "waiting": self._waiting,
            "account_ready": bool(getattr(self.bot, "is_ready", False)),
            "account_name": getattr(self.bot, "username", "Unknown"),
            "logs": list(self.logs)[:100],
        }

    # ── message listener ──────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message):
        if not self._waiting:
            return
        try:
            if message.author is None:
                return
            core_cfg = self.bot.config.get("core", {})
            monitor_id = str(core_cfg.get("monitor_bot_id", "408785106942164992"))
            if str(message.author.id) != monitor_id:
                return
            if str(message.channel.id) not in [str(c) for c in self.bot.channels]:
                return
            text = self._message_text(message)
            low = text.lower()
            if _ID_PATTERN.search(text) or any(m in low for m in _EMPTY_MARKERS) or \
                    any(m in low for m in _RATE_LIMIT_MARKERS):
                await self._parse_response(text, low)
            elif "weapon" in low or "id:" in low:
                # Log likely responses we don't understand so timeouts are debuggable.
                self._log("INFO", f"Possible response (unmatched): {self._snippet(text)}")
        except Exception as e:
            self.bot.log("ERROR", f"WeaponManager parse error: {e}")

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        # OwO sometimes edits its responses into place — catch that too.
        await self.on_message(after)

    # ── parsing ───────────────────────────────────────────

    async def _parse_response(self, text, low=None):
        low = low or text.lower()

        if any(marker in low for marker in _EMPTY_MARKERS):
            self.status = "empty"
            self.last_error = ""
            self.weapons = []
            self.last_fetch = time.time()
            self._log("INFO", "No weapons in inventory.")
            self._event.set()
            return

        if _ID_PATTERN.search(text):
            page_match = re.search(r'page\s*\d+\s*(?:/|of)\s*(\d+)', low)
            self.paginated = bool(page_match and int(page_match.group(1)) > 1)

            weapons = []
            seen = set()
            for m in _ID_PATTERN.finditer(text):
                wid = m.group(1)
                if wid in seen:
                    continue
                seen.add(wid)
                name = self._extract_name(text, m.start()) or f"Weapon {len(weapons) + 1}"
                weapons.append({"id": wid, "name": name})

            self.weapons = weapons
            if not weapons:
                self.status = "error"
                self.last_error = "No weapon IDs found in the response"
                self._log("ERROR", self.last_error)
            else:
                self.status = "done"
                self.last_fetch = time.time()
                self._log("INFO", f"Fetched {len(weapons)} weapon(s)")
                if self.paginated:
                    self._log("WARN", "Weapons list is paginated — re-fetch for more pages")
            self._event.set()
            return

        if any(marker in low for marker in _RATE_LIMIT_MARKERS):
            # Busy accounts get "slow down~" replies — trigger an automatic retry.
            self._rate_limited = True
            self._log("WARN", f"OwO rate-limited: {self._snippet(text)}")
            self._event.set()
            return

        # Some other message mentioning weapons/IDs — log it but keep waiting.
        self._log("INFO", f"Possible response (unmatched): {self._snippet(text)}")

    @classmethod
    def _extract_name(cls, text, id_pos):
        """Best-effort weapon name: text between the list marker and the ID."""
        line_start = text.rfind("\n", 0, id_pos) + 1
        line = text[line_start:id_pos]
        line_num = _LINE_NUMBER.match(line)
        if line_num:
            line = line[line_num.end():]
        else:
            line = re.sub(r'^\s*[-•*]\s*', '', line)
        line = re.split(r'\s*[—–]\s*', line)[0]  # cut "— lvl 3" suffixes
        name = _MARKDOWN.sub('', line)
        name = re.sub(r'^\W+', '', name)          # leading emoji / symbols
        name = re.sub(r'\s+', ' ', name).strip(' :\t')
        return name

    @staticmethod
    def _message_text(message):
        """Message content + embed text, case preserved (for display names)."""
        parts = [message.content or ""]
        if message.embeds:
            for em in message.embeds:
                chunks = [em.title or "", em.description or ""]
                if em.author and em.author.name:
                    chunks.append(em.author.name)
                for f in em.fields:
                    chunks.append(f"{f.name}: {f.value}")
                parts.append("\n".join(c for c in chunks if c))
        return "\n".join(parts)

    @staticmethod
    def _snippet(text, limit=120):
        """First meaningful line of a response, for logs."""
        flat = re.sub(r'\s+', ' ', text or '').strip()
        return flat[:limit] + ('...' if len(flat) > limit else '')

    # ── actions ───────────────────────────────────────────

    async def _send_action(self, action, weapon_id):
        if not getattr(self.bot, "is_ready", False):
            return "Bot is not ready"
        if getattr(self.bot, "paused", False):
            return "Bot is paused — resume it first (START on the dashboard)"
        if getattr(self.bot, "throttle_until", 0) == float("inf"):
            return "Bot is on a safety pause (captcha/cooldown) — resolve it in the Security tab first"
        content = f"owo {action} {weapon_id}"
        await self.bot.limey_enqueue(content, priority=1)
        self._log("INFO", f"Sent: {content}")
        # Note: the queue worker also logs the actual "Sent: ..." CMD entry
        # when the command goes out via _send_safe.
        # Optimistic cache update so the UI reflects the sent command.
        if weapon_id == "all":
            self.weapons = []
        else:
            self.weapons = [w for w in self.weapons if w["id"] != str(weapon_id)]
        return None

    # ── helpers ───────────────────────────────────────────

    def _log(self, level, message):
        entry = {
            "time": time.strftime("%I:%M:%S %p"),
            "timestamp": time.time(),
            "level": level,
            "message": message,
        }
        self.logs.appendleft(entry)
        try:
            self.bot.log(level, f"[Mass Dismantle] {message}")
        except Exception:
            pass


async def setup(bot):
    """Optional extension entry point.
    Not used by the main flow — core.bot.setup_hook attaches this cog directly
    via `add_cog` so `bot.weapon_manager` is always available.
    """
    await bot.add_cog(WeaponManager(bot))
