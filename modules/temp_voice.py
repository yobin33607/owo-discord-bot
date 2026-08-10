"""
Limey Temp Voice
================
Join-to-create temporary voice channels for the manager bot.

When a member joins the configured **hub channel**, the bot spawns a personal
voice channel for them (named from a template, optionally private) and moves
them into it. The channel is deleted as soon as it becomes empty.

Configuration (stored in manager_bot.temp_voice in settings.json):
  enabled            — Master switch (True/False)
  hub_channel_id     — The "join to create" voice channel
  category_id        — Optional category that temp channels are created in
  naming             — Channel name template; {name} becomes the member's name
  private_default    — Whether new channels start locked to their owner
  user_limit         — Default member cap per channel (0 = unlimited)
  guild_id           — The guild the system is set up in

Commands:
  /tempvoice-setup [channel] — (staff) set the hub; creates one if omitted
  /tempvoice-config          — (staff) show current configuration
  /tempvoice-set <setting>   — (staff) change a configuration value
  /tempvoice-reset           — (staff) delete all temp channels + clear config
  /tempvoice-name <name>     — rename your own temp channel
  /tempvoice-lock            — lock your own temp channel (private)
  /tempvoice-unlock          — unlock your own temp channel (public)
  /tempvoice-limit <n>       — set the member cap on your own temp channel
"""

import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from utils.github_data_store import ghd
from modules.staff_gate import slash_staff_required

_log = logging.getLogger("temp_voice")

DEFAULTS = {
    "enabled": True,
    "hub_channel_id": "",
    "category_id": "",
    "naming": "{name}'s Channel",
    "private_default": False,
    "user_limit": 0,
    "guild_id": "",
}

# Characters Discord does not allow in channel names.
_INVALID_NAME_CHARS = re.compile(r'[@#:?/\\*%<>|"`\u0000-\u001f]')
_WHITESPACE = re.compile(r"\s+")

VALID_SETTINGS = ("enabled", "naming", "private_default", "user_limit", "category_id")


def _load_config():
    cfg = ghd.read_json("config/settings.json", default={})
    if not isinstance(cfg, dict):
        return {}
    mb = cfg.get("manager_bot") or {}
    return mb.get("temp_voice") or {}


def _save_config(new_cfg):
    full = ghd.read_json("config/settings.json", default={}) or {}
    if not isinstance(full, dict):
        full = {}
    full.setdefault("manager_bot", {})["temp_voice"] = new_cfg
    return ghd.write_json("config/settings.json", full, message="Update temp voice config")


def _cfg_bool(value, default=False):
    """Normalize config booleans (JSON bool, 'on'/'off', '1'/'0', empty)."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _sanitize_name(name, fallback="Voice"):
    """Clean a channel name for Discord (max 100 chars, no invalid chars)."""
    name = _INVALID_NAME_CHARS.sub("", str(name or ""))
    name = _WHITESPACE.sub(" ", name).strip()
    name = name[:90].strip()
    return name or fallback


def _parse_user_limit(value):
    try:
        return max(0, min(int(value), 99))
    except (TypeError, ValueError):
        return 0


class TempVoice(commands.Cog):
    """Join-to-create temporary voice channels."""

    def __init__(self, bot):
        self.bot = bot
        # channel_id (int) -> owner_id (int) for live temp channels
        self.active = {}
        self._lock = asyncio.Lock()

    def _cfg(self):
        cfg = dict(DEFAULTS)
        cfg.update(_load_config())
        return cfg

    def _hub_channel(self, guild):
        cfg = self._cfg()
        cid = cfg.get("hub_channel_id") or ""
        if not cid:
            return None
        try:
            channel = guild.get_channel(int(cid))
        except (TypeError, ValueError):
            return None
        return channel if isinstance(channel, discord.VoiceChannel) else None

    # ── Events ───────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        # Restore tracking after a restart so existing temp channels keep
        # being auto-deleted when empty and owner commands keep working.
        await self._restore_tracking()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        try:
            await self._handle_voice(member, before, after)
        except Exception:
            _log.exception("temp voice handler failed for %s in %s", member, getattr(member, "guild", None))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if getattr(channel, "id", None) in self.active:
            self.active.pop(channel.id, None)

    async def _handle_voice(self, member, before, after):
        if not isinstance(member, discord.Member):
            return
        guild = member.guild
        before_chan = before.channel
        after_chan = after.channel

        async with self._lock:
            # 1) Member left a temp channel (or moved) — delete it if empty.
            #    Runs even when the feature is disabled so leftovers are cleaned.
            if before_chan and isinstance(before_chan, discord.VoiceChannel) and before_chan.id in self.active:
                if not before_chan.members:
                    self.active.pop(before_chan.id, None)
                    try:
                        await before_chan.delete(reason="Temp voice channel is empty")
                    except discord.NotFound:
                        pass
                    except discord.Forbidden:
                        _log.warning("Missing permissions to delete temp channel %s", before_chan.id)

            # 2) Joined the hub — spawn (or reuse) a personal channel.
            cfg = self._cfg()
            # Scope the feature to the configured guild (single-hub setup).
            if cfg.get("guild_id") and str(guild.id) != str(cfg["guild_id"]):
                return
            if not _cfg_bool(cfg.get("enabled"), True):
                return
            hub = self._hub_channel(guild)
            if not (hub and after_chan and after_chan.id == hub.id):
                return
            if before_chan and before_chan.id == hub.id:
                return  # already sitting in the hub — no re-trigger
            await self._create_for_member(member, guild, cfg, hub)

    async def _create_for_member(self, member, guild, cfg, hub):
        # Reuse an existing empty channel this member owns, if any.
        for cid, owner in list(self.active.items()):
            if owner != member.id:
                continue
            existing = guild.get_channel(cid)
            if isinstance(existing, discord.VoiceChannel) and not existing.members:
                try:
                    await member.move_to(existing)
                except (discord.Forbidden, discord.HTTPException):
                    _log.warning("Could not move %s into reused temp channel %s", member, cid)
                return

        name = _sanitize_name((cfg.get("naming") or DEFAULTS["naming"]).replace("{name}", member.display_name))

        category = None
        cat_id = cfg.get("category_id")
        if cat_id:
            try:
                category = guild.get_channel(int(cat_id))
            except (TypeError, ValueError):
                category = None
            if not isinstance(category, discord.CategoryChannel):
                category = None
        if category is None and hub.category is not None:
            category = hub.category

        private = _cfg_bool(cfg.get("private_default"), False)
        # The owner gets manage_channels so they can rename natively; the
        # lock/unlock/limit commands run as the bot, so manage_permissions is
        # deliberately NOT granted to regular members.
        overwrites = {
            member: discord.PermissionOverwrite(
                manage_channels=True,
                connect=True,
                view_channel=True,
            ),
        }
        if private:
            overwrites[guild.default_role] = discord.PermissionOverwrite(connect=False, view_channel=False)

        user_limit = _parse_user_limit(cfg.get("user_limit"))

        try:
            channel = await guild.create_voice_channel(
                name,
                category=category,
                overwrites=overwrites,
                user_limit=user_limit or None,
                reason=f"Temp voice channel for {member}",
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            _log.warning("Could not create temp voice channel for %s: %s", member, e)
            return

        self.active[channel.id] = member.id
        try:
            await member.move_to(channel)
        except (discord.Forbidden, discord.HTTPException):
            # The move failed — remove the fresh channel so it doesn't linger.
            self.active.pop(channel.id, None)
            try:
                await channel.delete(reason="Failed to move owner into temp channel")
            except (discord.NotFound, discord.Forbidden):
                pass

    # ── Owner controls (anyone in their own temp channel) ──

    async def _restore_tracking(self):
        """Re-discover live temp channels after a restart.

        Channels are identified by their owner overwrite (a member with
        manage_channels), scoped to the hub's category when one is configured.
        """
        try:
            for guild in self.bot.guilds:
                cfg = self._cfg(guild.id)
                if cfg.get("guild_id") and str(guild.id) != str(cfg["guild_id"]):
                    continue
                if not _cfg_bool(cfg.get("enabled"), True):
                    continue
                hub = self._hub_channel(guild)
                if not hub:
                    continue
                category = None
                cat_id = cfg.get("category_id")
                if cat_id:
                    try:
                        category = guild.get_channel(int(cat_id))
                    except (TypeError, ValueError):
                        category = None
                    if not isinstance(category, discord.CategoryChannel):
                        category = None
                for ch in guild.voice_channels:
                    if ch.id == hub.id or ch.id in self.active:
                        continue
                    if category is not None and (ch.category is None or ch.category.id != category.id):
                        continue
                    for target, ov in (ch.overwrites or {}).items():
                        if (
                            isinstance(target, discord.Member)
                            and ov
                            and getattr(ov, "manage_channels", False)
                        ):
                            self.active[ch.id] = target.id
                            break
        except Exception:
            _log.exception("temp voice tracking restore failed")

    async def _own_channel(self, interaction) -> discord.VoiceChannel | None:
        voice = getattr(interaction.user, "voice", None)
        channel = voice.channel if voice else None
        if not channel or not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "❌ You're not in one of your temp voice channels.", ephemeral=True
            )
            return None
        if channel.id not in self.active or self.active[channel.id] != interaction.user.id:
            await interaction.response.send_message(
                "❌ You can only manage your own temp voice channel.", ephemeral=True
            )
            return None
        return channel

    @app_commands.command(name="tempvoice-name", description="Rename your temporary voice channel")
    @app_commands.describe(name="The new channel name")
    async def tempvoice_name(self, interaction: discord.Interaction, name: str):
        channel = await self._own_channel(interaction)
        if not channel:
            return
        new_name = _sanitize_name(name)
        if not new_name:
            await interaction.response.send_message("❌ That name isn't allowed.", ephemeral=True)
            return
        try:
            await channel.edit(name=new_name)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("❌ Could not rename the channel.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Channel renamed to **{new_name}**.", ephemeral=True)

    @app_commands.command(name="tempvoice-lock", description="Lock your temporary voice channel (private)")
    async def tempvoice_lock(self, interaction: discord.Interaction):
        channel = await self._own_channel(interaction)
        if not channel:
            return
        try:
            await channel.set_permissions(
                interaction.guild.default_role, connect=False, view_channel=False
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("❌ Could not lock the channel.", ephemeral=True)
            return
        await interaction.response.send_message("🔒 Your channel is now **private**.", ephemeral=True)

    @app_commands.command(name="tempvoice-unlock", description="Unlock your temporary voice channel (public)")
    async def tempvoice_unlock(self, interaction: discord.Interaction):
        channel = await self._own_channel(interaction)
        if not channel:
            return
        try:
            await channel.set_permissions(interaction.guild.default_role, overwrite=None)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("❌ Could not unlock the channel.", ephemeral=True)
            return
        await interaction.response.send_message("🔓 Your channel is now **public**.", ephemeral=True)

    @app_commands.command(name="tempvoice-limit", description="Set the member limit on your temporary voice channel")
    @app_commands.describe(limit="Max members (0 = unlimited)")
    async def tempvoice_limit(self, interaction: discord.Interaction, limit: int):
        channel = await self._own_channel(interaction)
        if not channel:
            return
        limit = _parse_user_limit(limit)
        try:
            await channel.edit(user_limit=limit or None)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("❌ Could not update the limit.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ Member limit set to **{'unlimited' if not limit else limit}**.", ephemeral=True
        )

    # ── Staff administration ─────────────────────────────

    @app_commands.command(name="tempvoice-setup", description="Set up temp voice channels (join-to-create)")
    @app_commands.describe(channel="Optional: use an existing voice channel as the hub")
    @app_commands.check(slash_staff_required)
    async def tempvoice_setup(self, interaction: discord.Interaction, channel: discord.VoiceChannel = None):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        cfg = _load_config()
        if not isinstance(cfg, dict):
            cfg = {}

        if channel is None:
            try:
                category = await guild.create_category("Temp Channels")
                hub = await guild.create_voice_channel("➕ Join to Create", category=category)
                cfg["category_id"] = str(category.id)
            except (discord.Forbidden, discord.HTTPException) as e:
                await interaction.followup.send(
                    f"❌ I need **Manage Channels** permission to create the hub: {e}", ephemeral=True
                )
                return
        else:
            hub = channel

        # Clean up channels from a previous setup so nothing is orphaned.
        for cid in list(self.active.keys()):
            old = guild.get_channel(cid)
            if isinstance(old, discord.VoiceChannel):
                try:
                    await old.delete(reason="Temp voice re-setup")
                except (discord.NotFound, discord.Forbidden):
                    pass
        self.active.clear()

        cfg["hub_channel_id"] = str(hub.id)
        cfg["guild_id"] = str(guild.id)
        cfg.setdefault("enabled", True)
        cfg.setdefault("naming", DEFAULTS["naming"])
        cfg.setdefault("private_default", False)
        cfg.setdefault("user_limit", 0)
        _save_config(cfg)
        await interaction.followup.send(
            f"✅ Temp voice is live! Join **{hub.mention}** to spawn your own channel.", ephemeral=True
        )

    @app_commands.command(name="tempvoice-config", description="Show the temp voice configuration")
    @app_commands.check(slash_staff_required)
    async def tempvoice_config(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        cfg = self._cfg()
        hub = self._hub_channel(interaction.guild)
        lines = [
            f"**Enabled:** {'✅ On' if _cfg_bool(cfg.get('enabled'), True) else '❌ Off'}",
            f"**Hub channel:** {hub.mention if hub else '`not set`'}",
            f"**Naming:** `{cfg.get('naming') or DEFAULTS['naming']}`",
            f"**Private by default:** {'Yes' if _cfg_bool(cfg.get('private_default'), False) else 'No'}",
            f"**User limit:** {cfg.get('user_limit') or 'Unlimited'}",
            f"**Active channels:** {len(self.active)}",
        ]
        embed = discord.Embed(title="🎙️ Temp Voice Configuration", color=0xFF1F1F, description="\n".join(lines))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="tempvoice-set", description="Change a temp voice setting")
    @app_commands.describe(setting="Which setting to change", value="New value (on/off, name, number, or channel ID)")
    @app_commands.choices(setting=[
        app_commands.Choice(name="Enable / disable", value="enabled"),
        app_commands.Choice(name="Channel name template", value="naming"),
        app_commands.Choice(name="Private by default", value="private_default"),
        app_commands.Choice(name="Default user limit", value="user_limit"),
        app_commands.Choice(name="Category for temp channels", value="category_id"),
    ])
    @app_commands.check(slash_staff_required)
    async def tempvoice_set(self, interaction: discord.Interaction, setting: str, value: str):
        await interaction.response.defer(ephemeral=True)
        setting = setting.strip().lower()
        cfg = _load_config()
        if not isinstance(cfg, dict):
            cfg = {}
        value = (value or "").strip()

        if setting == "enabled":
            cfg["enabled"] = str(value).lower() in ("1", "true", "yes", "on")
        elif setting == "naming":
            name = _sanitize_name(value)
            if "{name}" not in name:
                await interaction.followup.send(
                    "❌ The template must contain `{name}` (e.g. `{name}'s Channel`).", ephemeral=True
                )
                return
            cfg["naming"] = name
        elif setting == "private_default":
            cfg["private_default"] = str(value).lower() in ("1", "true", "yes", "on")
        elif setting == "user_limit":
            cfg["user_limit"] = _parse_user_limit(value)
        elif setting == "category_id":
            try:
                cid = int(str(value).strip("<#>"))
            except ValueError:
                await interaction.followup.send("❌ Invalid channel ID.", ephemeral=True)
                return
            cfg["category_id"] = str(cid)
        else:
            await interaction.followup.send("❌ Unknown setting.", ephemeral=True)
            return

        _save_config(cfg)
        _log.info("Temp voice config updated via slash: %s = %s", setting, cfg.get(setting))
        await interaction.followup.send(f"✅ `{setting}` updated to **{cfg.get(setting)}**.", ephemeral=True)

    @app_commands.command(name="tempvoice-reset", description="Delete all temp voice channels and clear the config")
    @app_commands.check(slash_staff_required)
    async def tempvoice_reset(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        deleted = 0
        for cid in list(self.active.keys()):
            channel = interaction.guild.get_channel(cid)
            if isinstance(channel, discord.VoiceChannel):
                try:
                    await channel.delete(reason="Temp voice reset")
                    deleted += 1
                except (discord.NotFound, discord.Forbidden):
                    pass
        self.active.clear()
        _save_config({})
        await interaction.followup.send(
            f"✅ Deleted **{deleted}** temp channel(s) and cleared the configuration.", ephemeral=True
        )


async def setup(bot):
    """Add the TempVoice cog to the manager bot."""
    cog = TempVoice(bot)
    await bot.add_cog(cog)
