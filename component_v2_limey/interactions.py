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




import aiohttp
import json
import base64
import uuid
import time
import re
import random
import asyncio
import hashlib
from datetime import datetime

# Path (in the GitHub data store) where per-account interaction state lives.
# Currently only the stable client installation id is stored here, so that
# Discord keeps seeing the *same* client installation across bot restarts
# instead of a brand-new random device identity on every boot.
INTERACTION_STATE_PATH = "data/interaction_state.json"

class InteractionManager:

    def __init__(self, bot):
        self.bot = bot
        self._build_number = 310000 
        self._last_fetch = 0
        self._installation_id = self._load_installation_id()

        # Wire gateway lifecycle hooks (once per bot, deduped via a flag on the
        # bot) so interaction clicks pause while the gateway is reconnecting
        # after a restart / network drop instead of firing with a dead session.
        self._register_lifecycle_hooks()
        
        self.chrome_version = f"{random.randint(124, 127)}.0.0.0"
        self.user_agent = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self.chrome_version} Safari/537.36"

    # ── stable installation id (persisted across restarts) ────────────────

    def _account_key(self):
        """Stable per-account key so multiple accounts don't share an id."""
        token = getattr(self.bot, "token", "") or ""
        if token:
            return hashlib.sha1(token.encode("utf-8")).hexdigest()[:16]
        return "default"

    def _load_installation_id(self):
        """Reuse the installation id from the previous run if one exists.

        Discord ties client behaviour to the ``X-Installation-Id`` header;
        regenerating it on every restart looks like a brand-new device and can
        get interactions rejected. Persisting it keeps clicks working across
        restarts.
        """
        install_id = None
        try:
            from utils.github_data_store import ghd
            data = ghd.read_json(INTERACTION_STATE_PATH, default={}) or {}
            install_id = data.get(self._account_key())
        except Exception:
            pass
        if not install_id or len(str(install_id)) < 8:
            install_id = str(uuid.uuid4()).replace('-', '')[:32]
            self._save_installation_id(install_id)
        return str(install_id)

    def _save_installation_id(self, install_id):
        # Fire-and-forget: ghd reads/writes are synchronous GitHub API calls, so
        # a slow/unreachable GitHub must never block bot startup on first run.
        def _do_save():
            try:
                from utils.github_data_store import ghd
                data = ghd.read_json(INTERACTION_STATE_PATH, default={}) or {}
                data[self._account_key()] = install_id
                ghd.write_json(INTERACTION_STATE_PATH, data, message="Update interaction installation id")
            except Exception:
                pass
        try:
            import threading
            threading.Thread(target=_do_save, daemon=True).start()
        except Exception:
            pass

    # ── gateway session tracking (survives restarts / reconnects) ─────────

    def _register_lifecycle_hooks(self):
        bot = self.bot
        # The InteractionManager is re-created on every on_ready, so only
        # register the listeners once per bot instance. The listeners mutate
        # state stored on the bot itself, which every manager instance reads.
        if getattr(bot, "_limey_interaction_hooks", False):
            return
        bot._limey_interaction_hooks = True
        if not hasattr(bot, "_limey_interaction_session_stale"):
            bot._limey_interaction_session_stale = False
        try:
            if hasattr(bot, "add_listener"):
                bot.add_listener(self._on_gateway_disconnect, "on_disconnect")
        except Exception:
            pass

    async def _on_gateway_disconnect(self):
        # discord.py-self nulls ws.session_id on disconnect; mark it stale so
        # pending clicks wait for the fresh session instead of failing fast.
        self.bot._limey_interaction_session_stale = True

    def _get_live_session_id(self):
        """Return the current gateway session id, or None while disconnected."""
        try:
            ws = getattr(self.bot, "ws", None)
            if ws is None:
                return None
            sid = getattr(ws, "session_id", None)
            if not sid:
                return None
            self.bot._limey_interaction_session_stale = False
            return sid
        except Exception:
            return None

    async def _wait_for_session(self, timeout=10.0):
        """Wait until the gateway has a live session id.

        After a bot restart (or reconnect) the gateway session id is briefly
        None or stale; clicking during that window gets rejected by Discord.
        This waits the window out so the click goes out with a valid session.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            sid = self._get_live_session_id()
            if sid:
                return sid
            await asyncio.sleep(0.4)
        return None

    async def _fetch_build_number(self):
        now = time.time()
        if now - self._last_fetch < 43200 and self._build_number > 310000:
            return self._build_number

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://discord.com/login", timeout=10) as resp:
                    text = await resp.text()

                match = re.search(r"assets/(sentry\.\w+)\.js", text)
                if not match:
                    match = re.search(r"assets/(\d+\.\w+)\.js", text)
                
                if match:
                    url = f"https://static.discord.com/assets/{match.group(1)}.js"
                    async with session.get(url, timeout=10) as resp:
                        js = await resp.text()
                    
                    b_match = re.search(r'buildNumber\D+(\d+)"', js)
                    if b_match:
                        self._build_number = int(b_match.group(1))
                        self._last_fetch = now
        except Exception as e:
            pass
        return self._build_number

    def _generate_super_properties(self, build_number):
        major_ver = self.chrome_version.split('.')[0]
        props = {
            "os": "Windows",
            "browser": "Chrome",
            "device": "",
            "system_locale": "en-US",
            "browser_user_agent": self.user_agent,
            "browser_version": self.chrome_version,
            "os_version": "10",
            "referrer": "",
            "referring_domain": "",
            "referrer_current": "",
            "referring_domain_current": "",
            "release_channel": "stable",
            "client_build_number": build_number,
            "client_event_source": None,
            "has_client_mods": False,
            "client_launch_id": str(uuid.uuid4()),
            "launch_signature": str(uuid.uuid4()),
            "client_app_state": "focused",
            "client_heartbeat_session_id": str(uuid.uuid4())
        }
        return base64.b64encode(json.dumps(props, separators=(',', ':')).encode()).decode()

    async def _get_headers(self, channel_id=None, guild_id=None):
        bn = await self._fetch_build_number()
        sp = self._generate_super_properties(bn)
        tz = datetime.now().astimezone().tzname() or "UTC"
        
        referer = "https://discord.com/channels/@me"
        if guild_id and channel_id:
            referer = f"https://discord.com/channels/{guild_id}/{channel_id}"
        elif channel_id:
            referer = f"https://discord.com/channels/@me/{channel_id}"

        major_ver = self.chrome_version.split('.')[0]
        return {
            "Authorization": self.bot.token,
            "Content-Type": "application/json",
            "X-Super-Properties": sp,
            "X-Discord-Locale": "en-US",
            "X-Discord-Timezone": tz,
            "User-Agent": self.user_agent,
            "Origin": "https://discord.com",
            "Referer": referer,
            "Sec-CH-UA": f'"Not/A)Brand";v="8", "Chromium";v="{major_ver}", "Google Chrome";v="{major_ver}"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "Sec-CH-UA-Platform-Version": '"15.0.0"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Debug-Options": "bugReporterEnabled",
            "X-Discord-Features": "quests",
            "X-Installation-Id": self._installation_id
        }

    async def click_button(self, custom_id, message, guild_id=None):
        if not custom_id or not message:
            return False
            
        return await self.click_button_raw(
            custom_id=custom_id,
            message_id=message.id,
            channel_id=message.channel.id,
            guild_id=guild_id or (message.guild.id if message.guild else None),
            author_id=message.author.id,
            application_id=getattr(message, "application_id", None),
            flags=message.flags.value
        )

    async def click_button_raw(self, custom_id, message_id, channel_id, author_id, guild_id=None, flags=0, application_id=None, max_attempts=3):
        """Click a message component button via the HTTP interactions endpoint.

        Restart-safe behaviour:
          * waits for a live gateway session id before sending (a restart or
            reconnect briefly leaves the session id None/stale and Discord
            rejects clicks sent during that window),
          * retries on rate limits (429) with ``Retry-After`` backoff,
          * retries once on session-related failures (400/401/403) after
            refreshing the session id,
          * uses the real ``application_id`` from the message when available
            (falls back to the author id for backwards compatibility).
        """
        if not custom_id:
            return False

        session_id = await self._wait_for_session(timeout=10.0)
        if not session_id:
            self.bot.log("WARN", "Interaction skipped: no live gateway session (bot reconnecting?).")
            return False

        app_id = str(application_id) if application_id else str(author_id)

        payload = {
            "type": 3,
            "application_id": app_id,
            "guild_id": str(guild_id) if guild_id else None,
            "channel_id": str(channel_id),
            "message_id": str(message_id),
            "session_id": session_id,
            "message_flags": flags,
            "data": {
                "component_type": 2,
                "custom_id": custom_id
            }
        }

        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            headers = await self._get_headers(channel_id=channel_id, guild_id=guild_id)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "https://discord.com/api/v9/interactions",
                        json=payload,
                        headers=headers
                    ) as resp:
                        if resp.status == 204:
                            return True

                        error_text = await resp.text()

                        if resp.status == 429:
                            retry_after = resp.headers.get("Retry-After")
                            try:
                                delay = min(float(retry_after), 15.0) if retry_after else 5.0
                            except (TypeError, ValueError):
                                delay = 5.0
                            self.bot.log("WARN", f"Interaction rate limited (429); retrying in {round(delay, 1)}s")
                            await asyncio.sleep(delay)
                            continue

                        if resp.status in (400, 401, 403) and attempt < max_attempts:
                            # Possibly a stale/unknown session right after a
                            # restart or reconnect. Only retry if the gateway
                            # hands us a *new* session id — retrying with the
                            # same id (e.g. a bad token 401) can never succeed.
                            self.bot.log("WARN", f"Interaction failed ({resp.status}); refreshing gateway session and retrying...")
                            self.bot._limey_interaction_session_stale = True
                            fresh_session = await self._wait_for_session(timeout=8.0)
                            if fresh_session and fresh_session != session_id:
                                session_id = fresh_session
                                payload["session_id"] = session_id
                                continue

                        self.bot.log("ERROR", f"Interaction failed ({resp.status}): {error_text}")
                        return False
            except Exception as e:
                self.bot.log("ERROR", f"Interaction error: {e}")
                if attempt < max_attempts:
                    await asyncio.sleep(2.0)
                    continue
                return False
        return False

def setup_interactions(bot):
    return InteractionManager(bot)
