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

Discord server (guild) scanner.

Lets Limey list the Discord servers an account token can access, so the user
can pick which server to operate in before logging on — no browser extension
needed. The token used here is the same one the user already configures in
Limey (config/accounts.json / the dashboard); every request is made from THIS
machine, never sent anywhere else.

Used by:
  * dashboard/app.py  -> POST /api/accounts/scan-guilds and guild-channels
  * limey.py          -> server picker shown before login
"""

import json

import aiohttp

DISCORD_API = "https://discord.com/api/v10"


class TokenError(Exception):
    """Raised when Discord rejects the token or the request fails."""


async def _fetch_json(url, token, proxy_url=None, proxy_auth=None, timeout=15):
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    headers = {"Authorization": token, "User-Agent": "LimeySelf/2.5"}
    try:
        if proxy_url and proxy_url.startswith(("socks4://", "socks5://")):
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url(proxy_url, rdns=True)
            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout_cfg, headers=headers
            ) as session:
                async with session.get(url) as resp:
                    return resp.status, await resp.text()
        async with aiohttp.ClientSession(
            timeout=timeout_cfg, headers=headers
        ) as session:
            async with session.get(url, proxy=proxy_url, proxy_auth=proxy_auth) as resp:
                return resp.status, await resp.text()
    # Catch broadly: aiohttp_socks' SocksError does not subclass aiohttp.ClientError
    except Exception as e:
        raise TokenError(f"Network error while contacting Discord: {e}")


def _parse(status, body):
    try:
        data = json.loads(body) if body else None
    except json.JSONDecodeError:
        data = None
    if status == 200:
        return data
    if status == 401:
        raise TokenError("Invalid token — Discord rejected it.")
    if status == 403:
        raise TokenError("Discord denied access (403) — check the token and server permissions.")
    if status == 429:
        raise TokenError("Rate limited by Discord — wait a minute and retry.")
    msg = data.get("message") if isinstance(data, dict) else None
    raise TokenError(f"Discord API error {status}: {msg or 'unknown'}")


async def scan_guilds(token, proxy_url=None, proxy_auth=None):
    """Return the Discord servers an account token can access.

    Returns a list of dicts: [{"id", "name", "icon"}, ...]
    """
    status, body = await _fetch_json(
        f"{DISCORD_API}/users/@me/guilds", token, proxy_url, proxy_auth
    )
    data = _parse(status, body) or []
    return [
        {
            "id": str(g.get("id")),
            "name": g.get("name", "Unknown"),
            "icon": g.get("icon"),
        }
        for g in data
    ]


async def scan_guild_channels(token, guild_id, proxy_url=None, proxy_auth=None):
    """Return the text-like channels of a guild.

    Returns a list of dicts: [{"id", "name", "type"}, ...]
    (only text and announcement channels — the ones commands are sent to)
    """
    status, body = await _fetch_json(
        f"{DISCORD_API}/guilds/{guild_id}/channels", token, proxy_url, proxy_auth
    )
    data = _parse(status, body) or []
    return [
        {
            "id": str(c.get("id")),
            "name": c.get("name", ""),
            "type": c.get("type", 0),
        }
        for c in data
        if c.get("type") in (0, 5)  # 0 = text, 5 = announcement/news
    ]
