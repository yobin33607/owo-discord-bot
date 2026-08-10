"""
Limey Moderation Bot
====================
A complete moderation system for the manager bot.
Provides kick, ban, timeout, purge, warn, mute, slowmode, lockdown,
auto-moderation, and mod logging.

Loaded as a cog by the ManagerBot class.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import time
import logging
import re

_log = logging.getLogger("moderation_bot")

# ── GitHub Data Store ────────────────────────────────

from utils.github_data_store import ghd

# ── Staff gate for admin commands ─────────────────────

from modules.staff_gate import staff_required, slash_staff_required, StaffRoleRequired


# ── Data Helpers ───────────────────────────────────────

def _load_mod_data():
    """Load moderation data from GitHub data repo."""
    data = ghd.read_json("config/moderation.json", default=None)
    if data is not None:
        return data
    return {"warnings": {}, "mutes": {}, "mod_log": {}, "violations": {}, "next_warn_id": 1, "next_violation_id": 1}


def _save_mod_data(data):
    """Save moderation data to GitHub data repo."""
    ghd.write_json("config/moderation.json", data, message="Update moderation data")


def _load_appeals_data():
    """Load appeals data from GitHub data repo."""
    data = ghd.read_json("config/appeals.json", default=None)
    if data is not None:
        return data
    return {"appeals": [], "next_id": 1}


def _get_mod_config():
    """Get moderation config from settings.json manager_bot section via GitHub."""
    cfg = ghd.read_json("config/settings.json", default={})
    return cfg.get("manager_bot", {}).get("moderation", {})


def _save_mod_config(new_mod_cfg):
    """Save full moderation config back to settings.json via GitHub.
    Merges into the existing manager_bot section, preserving other keys.
    Returns True on success."""
    full = ghd.read_json("config/settings.json", default={})
    if full is None:
        full = {}

    if "manager_bot" not in full:
        full["manager_bot"] = {}
    full["manager_bot"]["moderation"] = new_mod_cfg

    return ghd.write_json("config/settings.json", full, message="Update moderation config")


def _update_auto_mod(auto_mod_updates: dict):
    """Update only specific keys in the auto_mod config and save."""
    cfg = _get_mod_config()
    existing_auto = cfg.get("auto_mod", {})
    existing_auto.update(auto_mod_updates)
    cfg["auto_mod"] = existing_auto
    return _save_mod_config(cfg)


def _get_mod_log_channel_id(guild_id):
    """Get the mod log channel ID for a guild."""
    cfg = _get_mod_config()
    return cfg.get("mod_log_channel_id", "")


def _get_muted_role_id():
    """Get the muted role ID from config."""
    cfg = _get_mod_config()
    return cfg.get("muted_role_id", None)


def _get_quarantine_role_id():
    """Get the quarantine role ID from config."""
    cfg = _get_mod_config()
    return cfg.get("quarantine_role_id", None)


def _ensure_quarantine_data():
    """Load mod data, ensuring the quarantines key exists."""
    data = _load_mod_data()
    if "quarantines" not in data:
        data["quarantines"] = {}
    return data


def _get_auto_mod_config():
    """Get auto-mod config."""
    cfg = _get_mod_config()
    return cfg.get("auto_mod", {})


def _get_warn_thresholds():
    """Get warn thresholds config."""
    cfg = _get_mod_config()
    return cfg.get("warn_thresholds", {})


def clear_user_violations(guild_id, user_id):
    """Clear all warnings, violations, and active mutes for a user in a guild.
    Returns (removed_count: int, had_timeout: bool).
    """
    import time as _time
    data = _load_mod_data()
    guild_key = str(guild_id)
    user_key = str(user_id)
    removed = 0

    warns = data.get("warnings", {}).get(guild_key, {}).get(user_key, [])
    if warns:
        removed += len(warns)
        data["warnings"].setdefault(guild_key, {})[user_key] = []

    violations = data.get("violations", {}).get(guild_key, {}).get(user_key, [])
    if violations:
        removed += len(violations)
        data["violations"].setdefault(guild_key, {})[user_key] = []

    # Clear mute record
    mutes = data.get("mutes", {}).get(guild_key, {})
    if user_key in mutes:
        del mutes[user_key]
        removed += 1

    if removed:
        _save_mod_data(data)
    return removed


def _get_auto_slowmode_config():
    """Get auto slowmode config with sensible defaults."""
    cfg = _get_mod_config()
    asm = cfg.get("auto_slowmode", {}) or {}
    thresholds = asm.get("thresholds", {}) or {}
    # Ensure thresholds keys are ints and sorted descending
    clean_thresholds = {}
    for k, v in thresholds.items():
        try:
            clean_thresholds[int(k)] = int(v)
        except (ValueError, TypeError):
            pass
    return {
        "enabled": bool(asm.get("enabled", False)),
        "check_interval": max(10, int(asm.get("check_interval", 30))),
        "thresholds": clean_thresholds,
        "cooldown": max(0, int(asm.get("cooldown", 300))),
        "min_slowmode": max(0, int(asm.get("min_slowmode", 0))),
        "max_slowmode": min(21600, max(0, int(asm.get("max_slowmode", 21600)))),
    }


def _update_auto_slowmode(updates: dict):
    """Update auto slowmode config keys and save."""
    cfg = _get_mod_config()
    existing = cfg.get("auto_slowmode", {}) or {}
    existing.update(updates)
    cfg["auto_slowmode"] = existing
    return _save_mod_config(cfg)


def _format_duration(seconds):
    """Format seconds into human-readable duration string."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    elif seconds < 86400:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        return f"{hours}h {mins}m"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days}d {hours}h"


def _parse_duration(text):
    """Parse a duration string like '1h', '30m', '7d' into seconds."""
    text = text.strip().lower()
    total = 0
    import re
    parts = re.findall(r'(\d+)\s*(s|sec|seconds?|m|min|minutes?|h|hr|hours?|d|days?)', text)
    for num, unit in parts:
        num = int(num)
        if unit.startswith('d'):
            total += num * 86400
        elif unit.startswith('h'):
            total += num * 3600
        elif unit.startswith('m'):
            total += num * 60
        elif unit.startswith('s'):
            total += num
    return total if total > 0 else None


async def _send_mod_log(guild, action_type, target, moderator, reason, duration=None):
    """Send a moderation log embed to the configured mod log channel."""
    channel_id = _get_mod_log_channel_id(str(guild.id))
    if not channel_id:
        return

    try:
        channel = guild.get_channel(int(channel_id)) or await guild.fetch_channel(int(channel_id))
        if not channel:
            return
    except Exception:
        return

    colors = {
        "kick": 0xFFAA00,
        "ban": 0xFF4444,
        "unban": 0x44FF88,
        "timeout": 0xFF8800,
        "untimeout": 0x44FF88,
        "warn": 0xFFAA44,
        "clearwarns": 0x44AAFF,
        "clearviolations": 0x44AAFF,
        "mute": 0xFF8800,
        "unmute": 0x44FF88,
        "purge": 0x4488FF,
        "slowmode": 0xAA88FF,
        "lock": 0xFF4444,
        "unlock": 0x44FF88,
        "automod": 0xFF44AA,
        "quarantine": 0xFF6600,
        "unquarantine": 0x44FF88,
    }

    emojis = {
        "kick": "👢",
        "ban": "🔨",
        "unban": "🔓",
        "timeout": "🔇",
        "untimeout": "🔊",
        "warn": "⚠️",
        "clearwarns": "🧹",
        "clearviolations": "🧹",
        "mute": "🔇",
        "unmute": "🔊",
        "purge": "🗑️",
        "slowmode": "🐢",
        "lock": "🔒",
        "unlock": "🔓",
        "automod": "🤖",
        "quarantine": "🔒",
        "unquarantine": "🔓",
    }

    embed = discord.Embed(
        title=f"{emojis.get(action_type, '🛡️')} {action_type.upper()}",
        color=colors.get(action_type, 0xFF4444),
        timestamp=discord.utils.utcnow(),
    )

    target_text = f"{target} ({target.id})" if isinstance(target, (discord.User, discord.Member)) else str(target)
    mod_text = f"{moderator} ({moderator.id})" if isinstance(moderator, (discord.User, discord.Member)) else str(moderator)

    embed.add_field(name="Target", value=target_text, inline=True)
    embed.add_field(name="Moderator", value=mod_text, inline=True)

    if duration:
        embed.add_field(name="Duration", value=duration, inline=True)

    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)

    embed.set_footer(text=f"Action • {guild.name}")

    try:
        await channel.send(embed=embed)
    except Exception:
        pass


async def _check_warn_thresholds(guild, member, total_warns):
    """Check if a user has reached warn thresholds and auto-punish."""
    thresholds = _get_warn_thresholds()
    if not thresholds:
        return

    # Sort thresholds descending so we trigger the highest matched
    sorted_thresholds = sorted(thresholds.items(), key=lambda x: int(x[0]), reverse=True)

    for str_count, action in sorted_thresholds:
        count = int(str_count)
        if total_warns >= count:
            action = action.strip().lower()

            if action.startswith("mute"):
                parts = action.split()
                if len(parts) >= 2:
                    duration_str = parts[1]
                    duration_secs = _parse_duration(duration_str)
                    if duration_secs:
                        try:
                            await member.timeout(discord.utils.utcnow() + discord.timedelta(seconds=duration_secs),
                                                  reason=f"Auto-mute: reached {total_warns} warns")
                            await _send_mod_log(guild, "mute", member, guild.me,
                                                f"Auto-mute: reached {total_warns} warns (threshold: {count})",
                                                duration=duration_str)
                        except Exception:
                            pass
                    else:
                        # Try role-based mute
                        muted_role_id = _get_muted_role_id()
                        if muted_role_id:
                            muted_role = guild.get_role(int(muted_role_id))
                            if muted_role:
                                try:
                                    await member.add_roles(muted_role, reason=f"Auto-mute: reached {total_warns} warns")
                                    await _send_mod_log(guild, "mute", member, guild.me,
                                                        f"Auto-mute: reached {total_warns} warns (threshold: {count})")
                                except Exception:
                                    pass

            elif action == "kick":
                try:
                    await member.kick(reason=f"Auto-kick: reached {total_warns} warns (threshold: {count})")
                    await _send_mod_log(guild, "kick", member, guild.me,
                                        f"Auto-kick: reached {total_warns} warns (threshold: {count})")
                except Exception:
                    pass

            elif action.startswith("ban"):
                try:
                    await member.ban(reason=f"Auto-ban: reached {total_warns} warns (threshold: {count})")
                    await _send_mod_log(guild, "ban", member, guild.me,
                                        f"Auto-ban: reached {total_warns} warns (threshold: {count})")
                except Exception:
                    pass
            break  # Only trigger the highest threshold


# ── Moderation Cog ─────────────────────────────────────

class Moderation(commands.Cog):
    """Complete moderation system: kick, ban, timeout, purge, warn, mute, slowmode, lockdown, Discord AutoMod DM."""

    def __init__(self, bot):
        self.bot = bot
        self._locked_channels = set()  # Track locked channels per guild
        self._auto_unmute_loop.start()  # Start the expired mute checker
        self._auto_slowmode_data = {}  # guild_id -> {channel_id: [timestamps]}
        self._auto_slowmode_current = {}  # guild_id -> {channel_id: current_slowmode}
        self._auto_slowmode_loop.start()  # Start the auto slowmode adjuster

    # ── Permission Helper ─────────────────────────────

    async def _check_permissions(self, ctx, permission="moderate_members"):
        """Check if the user has the required permission."""
        if not isinstance(ctx.author, discord.Member):
            return False
        return getattr(ctx.author.guild_permissions, permission, False)

    async def _check_slash_permissions(self, interaction: discord.Interaction, permission="moderate_members"):
        """Check if the user has the required permission for slash commands."""
        if not isinstance(interaction.user, discord.Member):
            return False
        return getattr(interaction.user.guild_permissions, permission, False)

    async def _resolve_member(self, guild, user_input):
        """Resolve a user input string into a Member object."""
        # Try to parse as ID
        if user_input.isdigit():
            try:
                member = guild.get_member(int(user_input))
                if member:
                    return member
                # Try fetching
                user = await self.bot.fetch_user(int(user_input))
                return user
            except Exception:
                pass

        # Try by name or nickname
        members = guild.get_member_named(user_input)
        if members:
            return members[0]

        # Try fetching by ID just in case
        try:
            user = await self.bot.fetch_user(int(user_input))
            return user
        except Exception:
            pass

        return None

    # ── Violations System ─────────────────────────────

    async def _store_violation(self, guild_id, user_id, vtype, reason, moderator, duration=None):
        """Store a violation (warn/kick/ban/timeout/mute) for a user."""
        data = _load_mod_data()
        if "violations" not in data:
            data["violations"] = {}
        guild_key = str(guild_id)
        user_key = str(user_id)

        if guild_key not in data["violations"]:
            data["violations"][guild_key] = {}
        if user_key not in data["violations"][guild_key]:
            data["violations"][guild_key][user_key] = []

        vid = data.get("next_violation_id", 1)
        data["next_violation_id"] = vid + 1

        mod_str = str(moderator)
        if isinstance(moderator, (discord.User, discord.Member)):
            mod_str = f"{moderator} ({moderator.id})"

        data["violations"][guild_key][user_key].append({
            "id": vid,
            "type": vtype,
            "reason": reason or "No reason provided",
            "moderator": mod_str,
            "duration": duration,
            "timestamp": time.time(),
        })

        # Keep last 50 violations per user
        if len(data["violations"][guild_key][user_key]) > 50:
            data["violations"][guild_key][user_key] = data["violations"][guild_key][user_key][-50:]

        _save_mod_data(data)

    async def _get_user_violations(self, guild_id, user_id):
        """Get all violations for a user in a guild."""
        data = _load_mod_data()
        guild_key = str(guild_id)
        user_key = str(user_id)
        return data.get("violations", {}).get(guild_key, {}).get(user_key, [])

    # ── Mod Log Viewing ───────────────────────────────

    @commands.command(name="modlog")
    @commands.check(staff_required)
    async def cmd_modlog(self, ctx, count: int = 10):
        """View recent moderation actions. Usage: !modlog [count]"""
        data = _load_mod_data()
        guild_logs = data.get("mod_log", {}).get(str(ctx.guild.id), [])

        if not guild_logs:
            await ctx.send("```📋 No moderation actions logged yet.```")
            return

        count = max(1, min(count, 30))
        recent = list(reversed(guild_logs))[:count]

        lines = [f"📋  MOD LOG — Last {count} actions"]
        lines.append("─" * 60)
        for entry in recent:
            ts = time.strftime("%m/%d %H:%M", time.localtime(entry.get("timestamp", 0)))
            action = entry.get("type", "?").upper()
            target = entry.get("target", "?")
            mod = entry.get("moderator", "?")
            reason = (entry.get("reason") or "")[:30]
            lines.append(f"  [{ts}] {action:10s} {target[:20]:20s} → {mod[:20]:20s}")
            if reason:
                lines.append(f"  {'':14s}Reason: {reason}")
        lines.append("─" * 60)

        await ctx.send(f"```{chr(10).join(lines)}```")

    @app_commands.command(name="modlog", description="View recent moderation actions")
    @app_commands.describe(count="Number of entries to show (max 30)")
    @app_commands.check(slash_staff_required)
    async def slash_modlog(self, interaction: discord.Interaction, count: int = 10):
        """View recent moderation actions."""
        data = _load_mod_data()
        guild_logs = data.get("mod_log", {}).get(str(interaction.guild_id), [])

        if not guild_logs:
            await interaction.response.send_message("```📋 No moderation actions logged yet.```", ephemeral=True)
            return

        count = max(1, min(count, 30))
        recent = list(reversed(guild_logs))[:count]

        lines = [f"📋  MOD LOG — Last {count} actions"]
        lines.append("─" * 60)
        for entry in recent:
            ts = time.strftime("%m/%d %H:%M", time.localtime(entry.get("timestamp", 0)))
            action = entry.get("type", "?").upper()
            target = entry.get("target", "?")
            mod = entry.get("moderator", "?")
            reason = (entry.get("reason") or "")[:30]
            lines.append(f"  [{ts}] {action:10s} {target[:20]:20s} → {mod[:20]:20s}")
            if reason:
                lines.append(f"  {'':14s}Reason: {reason}")
        lines.append("─" * 60)

        await interaction.response.send_message(f"```{chr(10).join(lines)}```")

    # ── Internal Logging ──────────────────────────────

    async def _store_mod_action(self, guild_id, action_type, target, moderator, reason=None):
        """Store a mod action in the local JSON log."""
        data = _load_mod_data()
        guild_key = str(guild_id)
        if guild_key not in data["mod_log"]:
            data["mod_log"][guild_key] = []

        target_str = str(target)
        if isinstance(target, (discord.User, discord.Member)):
            target_str = f"{target} ({target.id})"
        mod_str = str(moderator)
        if isinstance(moderator, (discord.User, discord.Member)):
            mod_str = f"{moderator} ({moderator.id})"

        data["mod_log"][guild_key].append({
            "type": action_type,
            "target": target_str,
            "moderator": mod_str,
            "reason": reason or "No reason provided",
            "timestamp": time.time(),
        })

        # Keep only last 500 entries per guild
        if len(data["mod_log"][guild_key]) > 500:
            data["mod_log"][guild_key] = data["mod_log"][guild_key][-500:]

        _save_mod_data(data)

    # ── Mod Settings ────────────────────────────────

    @commands.command(name="modsettings")
    @commands.check(staff_required)
    async def cmd_modsettings(self, ctx, setting: str = "", *, value: str = ""):
        """Toggle Discord AutoMod DM warnings.

        Usage:
          !modsettings                   — Show current settings
          !modsettings discordwarn on    — Enable Discord AutoMod DM warnings
          !modsettings discordwarn off   — Disable Discord AutoMod DM warnings
        """
        if not setting:
            # Show current settings
            cfg = _get_mod_config()
            auto = cfg.get("auto_mod", {})
            discord_automod_warn = auto.get("discord_automod_warn", True)

            embed = discord.Embed(
                title="🤖 Discord AutoMod Settings",
                color=0xFF44AA,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="DM Warnings",
                             value="🟢 **On**" if discord_automod_warn else "🔴 **Off**", inline=False)
            embed.set_footer(text=f"Use !modsettings discordwarn on/off • {ctx.guild.name}")
            await ctx.send(embed=embed)
            return

        # Handle setting changes
        setting = setting.lower()

        if setting == "discordwarn":
            val = value.strip().lower()
            if val in ("on", "enable", "true", "1"):
                _update_auto_mod({"discord_automod_warn": True})
                await ctx.send("🤖 Discord AutoMod DM warnings have been **enabled**.")
                await self._store_mod_action(ctx.guild.id, "modsettings", "discord automod dm enabled", ctx.author)
            elif val in ("off", "disable", "false", "0"):
                _update_auto_mod({"discord_automod_warn": False})
                await ctx.send("🤖 Discord AutoMod DM warnings have been **disabled**.")
                await self._store_mod_action(ctx.guild.id, "modsettings", "discord automod dm disabled", ctx.author)
            else:
                await ctx.send("❌ Usage: `!modsettings discordwarn on` or `!modsettings discordwarn off`")
        else:
            await ctx.send("❌ Unknown setting. Use `!modsettings` to see settings.")

    @app_commands.command(name="modsettings", description="Toggle Discord AutoMod DM warnings")
    @app_commands.describe(
        setting="Setting: discordwarn",
        value="Value: on or off",
    )
    @app_commands.check(slash_staff_required)
    async def slash_modsettings(self, interaction: discord.Interaction, setting: str = "", value: str = ""):
        """Toggle Discord AutoMod DM warnings."""
        if not setting:
            # Show current settings
            cfg = _get_mod_config()
            auto = cfg.get("auto_mod", {})
            discord_automod_warn = auto.get("discord_automod_warn", True)

            embed = discord.Embed(title="🤖 Discord AutoMod Settings", color=0xFF44AA, timestamp=discord.utils.utcnow())
            embed.add_field(name="DM Warnings",
                             value="🟢 **On**" if discord_automod_warn else "🔴 **Off**", inline=False)
            embed.set_footer(text=f"Use /modsettings discordwarn on/off • {interaction.guild.name}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        setting = setting.lower()

        if setting == "discordwarn":
            val = value.strip().lower()
            if val in ("on", "enable", "true", "1"):
                _update_auto_mod({"discord_automod_warn": True})
                await interaction.response.send_message("🤖 Discord AutoMod DM warnings have been **enabled**.")
                await self._store_mod_action(interaction.guild_id, "modsettings", "discord automod dm enabled", interaction.user)
            elif val in ("off", "disable", "false", "0"):
                _update_auto_mod({"discord_automod_warn": False})
                await interaction.response.send_message("🤖 Discord AutoMod DM warnings have been **disabled**.")
                await self._store_mod_action(interaction.guild_id, "modsettings", "discord automod dm disabled", interaction.user)
            else:
                await interaction.response.send_message("❌ Usage: `discordwarn on` or `discordwarn off`", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Unknown setting. Use `/modsettings` to see settings.", ephemeral=True)

    # ── Warn System ───────────────────────────────────

    @commands.command(name="warn")
    @commands.has_permissions(moderate_members=True)
    async def cmd_warn(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        """Warn a member. Usage: !warn <member> [reason]"""
        if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            await ctx.send("❌ You cannot warn someone with a higher or equal role.")
            return

        data = _load_mod_data()
        guild_key = str(ctx.guild.id)
        user_key = str(member.id)

        if guild_key not in data["warnings"]:
            data["warnings"][guild_key] = {}

        if user_key not in data["warnings"][guild_key]:
            data["warnings"][guild_key][user_key] = []

        warn_id = data["next_warn_id"]
        data["next_warn_id"] += 1

        data["warnings"][guild_key][user_key].append({
            "id": warn_id,
            "moderator": f"{ctx.author} ({ctx.author.id})",
            "reason": reason,
            "timestamp": time.time(),
        })

        _save_mod_data(data)

        total_warns = len(data["warnings"][guild_key][user_key])
        warning_msg = f"⚠️ {member.mention} has been warned by {ctx.author.mention}"
        warning_msg += f"\n**Reason:** {reason}"
        warning_msg += f"\n**Total warns:** {total_warns}"

        await ctx.send(warning_msg)

        # Check thresholds
        await _check_warn_thresholds(ctx.guild, member, total_warns)

        # Log
        await _send_mod_log(ctx.guild, "warn", member, ctx.author, reason)
        await self._store_mod_action(ctx.guild.id, "warn", member, ctx.author, reason)
        await self._store_violation(ctx.guild.id, member.id, "warn", reason, ctx.author)

        # Try to DM the user
        try:
            embed = discord.Embed(
                title="⚠️ You've Been Warned",
                description=f"**Server:** {ctx.guild.name}\n**Reason:** {reason}\n**Total warns:** {total_warns}",
                color=0xFFAA44,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text=f"Warning #{warn_id}")
            await member.send(embed=embed)
        except Exception:
            pass

    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.describe(member="The member to warn", reason="The reason for the warning")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def slash_warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        """Warn a member."""
        if member.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
            await interaction.response.send_message("❌ You cannot warn someone with a higher or equal role.", ephemeral=True)
            return

        data = _load_mod_data()
        guild_key = str(interaction.guild_id)
        user_key = str(member.id)

        if guild_key not in data["warnings"]:
            data["warnings"][guild_key] = {}
        if user_key not in data["warnings"][guild_key]:
            data["warnings"][guild_key][user_key] = []

        warn_id = data["next_warn_id"]
        data["next_warn_id"] += 1

        data["warnings"][guild_key][user_key].append({
            "id": warn_id,
            "moderator": f"{interaction.user} ({interaction.user.id})",
            "reason": reason,
            "timestamp": time.time(),
        })

        _save_mod_data(data)

        total_warns = len(data["warnings"][guild_key][user_key])

        await interaction.response.send_message(
            f"⚠️ {member.mention} has been warned.\n**Reason:** {reason}\n**Total warns:** {total_warns}"
        )

        await _check_warn_thresholds(interaction.guild, member, total_warns)
        await _send_mod_log(interaction.guild, "warn", member, interaction.user, reason)
        await self._store_mod_action(interaction.guild_id, "warn", member, interaction.user, reason)
        await self._store_violation(interaction.guild_id, member.id, "warn", reason, interaction.user)

        try:
            embed = discord.Embed(
                title="⚠️ You've Been Warned",
                description=f"**Server:** {interaction.guild.name}\n**Reason:** {reason}\n**Total warns:** {total_warns}",
                color=0xFFAA44,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text=f"Warning #{warn_id}")
            await member.send(embed=embed)
        except Exception:
            pass

    @commands.command(name="warnings")
    @commands.has_permissions(moderate_members=True)
    async def cmd_warnings(self, ctx, member: discord.Member):
        """View warnings for a member. Usage: !warnings <member>"""
        data = _load_mod_data()
        guild_key = str(ctx.guild.id)
        user_key = str(member.id)

        warns = data.get("warnings", {}).get(guild_key, {}).get(user_key, [])

        if not warns:
            await ctx.send(f"✅ {member.mention} has **no warnings**.")
            return

        lines = [f"⚠️  WARNINGS — {member} ({member.id})"]
        lines.append("─" * 50)
        for w in warns:
            ts = time.strftime("%m/%d %H:%M", time.localtime(w.get("timestamp", 0)))
            mod = w.get("moderator", "?")[:25]
            reason = (w.get("reason") or "")[:40]
            lines.append(f"  #{w.get('id', '?')} [{ts}]")
            lines.append(f"     Mod: {mod}")
            lines.append(f"     Reason: {reason}")
        lines.append("─" * 50)
        lines.append(f"  Total: {len(warns)} warning(s)")

        await ctx.send(f"```{chr(10).join(lines)}```")

    @app_commands.command(name="warnings", description="View warnings for a member")
    @app_commands.describe(member="The member to check warnings for")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def slash_warnings(self, interaction: discord.Interaction, member: discord.Member):
        """View warnings for a member."""
        data = _load_mod_data()
        guild_key = str(interaction.guild_id)
        user_key = str(member.id)

        warns = data.get("warnings", {}).get(guild_key, {}).get(user_key, [])

        if not warns:
            await interaction.response.send_message(f"✅ {member.mention} has **no warnings**.", ephemeral=True)
            return

        lines = [f"⚠️  WARNINGS — {member} ({member.id})"]
        lines.append("─" * 50)
        for w in warns:
            ts = time.strftime("%m/%d %H:%M", time.localtime(w.get("timestamp", 0)))
            mod = w.get("moderator", "?")[:25]
            reason = (w.get("reason") or "")[:40]
            lines.append(f"  #{w.get('id', '?')} [{ts}]")
            lines.append(f"     Mod: {mod}")
            lines.append(f"     Reason: {reason}")
        lines.append("─" * 50)
        lines.append(f"  Total: {len(warns)} warning(s)")

        await interaction.response.send_message(f"```{chr(10).join(lines)}```", ephemeral=True)

    # ── Violations Command ───────────────────────────

    @commands.command(name="violations")
    @commands.has_permissions(moderate_members=True)
    async def cmd_violations(self, ctx, member: discord.Member):
        """View all violations (warns/kicks/bans/timeouts/mutes) for a member. Usage: !violations <member>"""
        violations = await self._get_user_violations(ctx.guild.id, member.id)

        if not violations:
            await ctx.send(f"✅ {member.mention} has **no violations** on record.")
            return

        lines = [f"📋  VIOLATIONS — {member} ({member.id})"]
        lines.append("─" * 55)
        for v in violations:
            vid = v.get("id", "?")
            ts = time.strftime("%m/%d %H:%M", time.localtime(v.get("timestamp", 0)))
            vtype = v.get("type", "?").upper()
            mod = (v.get("moderator") or "?")[:25]
            reason = (v.get("reason") or "")[:45]
            duration = v.get("duration")
            dur_text = f" [{duration}]" if duration else ""
            lines.append(f"  #{vid} [{ts}] {vtype:8s}{dur_text}")
            lines.append(f"     Mod: {mod}")
            lines.append(f"     Reason: {reason}")
        lines.append("─" * 55)
        lines.append(f"  Total: {len(violations)} violation(s)")

        await ctx.send(f"```{chr(10).join(lines)}```")

    @app_commands.command(name="violations", description="View all violations for a member (warns/kicks/bans/timeouts/mutes)")
    @app_commands.describe(member="The member to check violations for")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def slash_violations(self, interaction: discord.Interaction, member: discord.Member):
        """View all violations for a member."""
        violations = await self._get_user_violations(interaction.guild_id, member.id)

        if not violations:
            await interaction.response.send_message(f"✅ {member.mention} has **no violations** on record.", ephemeral=True)
            return

        lines = [f"📋  VIOLATIONS — {member} ({member.id})"]
        lines.append("─" * 55)
        for v in violations:
            vid = v.get("id", "?")
            ts = time.strftime("%m/%d %H:%M", time.localtime(v.get("timestamp", 0)))
            vtype = v.get("type", "?").upper()
            mod = (v.get("moderator") or "?")[:25]
            reason = (v.get("reason") or "")[:45]
            duration = v.get("duration")
            dur_text = f" [{duration}]" if duration else ""
            lines.append(f"  #{vid} [{ts}] {vtype:8s}{dur_text}")
            lines.append(f"     Mod: {mod}")
            lines.append(f"     Reason: {reason}")
        lines.append("─" * 55)
        lines.append(f"  Total: {len(violations)} violation(s)")

        await interaction.response.send_message(f"```{chr(10).join(lines)}```", ephemeral=True)

    # ── Clear Violations Command ─────────────────────

    @commands.command(name="clearviolations")
    @commands.check(staff_required)
    async def cmd_clearviolations(self, ctx, member: discord.Member, violation_id: str = "all"):
        """Clear violations for a member. Usage: !clearviolations <member> [violation_id|all]"""
        data = _load_mod_data()
        guild_key = str(ctx.guild.id)
        user_key = str(member.id)

        violations = data.get("violations", {}).get(guild_key, {}).get(user_key, [])

        if not violations:
            await ctx.send(f"✅ {member.mention} has no violations to clear.")
            return

        if violation_id == "all":
            count = len(violations)
            data["violations"][guild_key][user_key] = []
            _save_mod_data(data)
            await ctx.send(f"🧹 Cleared all {count} violation(s) for {member.mention}.")
            await _send_mod_log(ctx.guild, "clearviolations", member, ctx.author, f"Cleared all {count} violations")
            await self._store_mod_action(ctx.guild.id, "clearviolations", member, ctx.author, f"Cleared all {count} violations")
        else:
            try:
                vid = int(violation_id)
            except ValueError:
                await ctx.send("❌ Invalid violation ID. Use a number or 'all'.")
                return

            before = len(violations)
            data["violations"][guild_key][user_key] = [v for v in violations if v.get("id") != vid]
            removed = before - len(data["violations"][guild_key][user_key])
            _save_mod_data(data)

            if removed:
                await ctx.send(f"🧹 Removed violation #{vid} from {member.mention}.")
                await _send_mod_log(ctx.guild, "clearviolations", member, ctx.author, f"Removed violation #{vid}")
                await self._store_mod_action(ctx.guild.id, "clearviolations", member, ctx.author, f"Removed violation #{vid}")
            else:
                await ctx.send(f"❌ Violation #{vid} not found for {member.mention}.")

    @app_commands.command(name="clearviolations", description="Clear violations for a member")
    @app_commands.describe(
        member="The member to clear violations for",
        violation_id="Violation ID to remove, or 'all' for all violations",
    )
    @app_commands.check(slash_staff_required)
    async def slash_clearviolations(self, interaction: discord.Interaction, member: discord.Member, violation_id: str = "all"):
        """Clear violations for a member."""
        data = _load_mod_data()
        guild_key = str(interaction.guild_id)
        user_key = str(member.id)

        violations = data.get("violations", {}).get(guild_key, {}).get(user_key, [])

        if not violations:
            await interaction.response.send_message(f"✅ {member.mention} has no violations to clear.", ephemeral=True)
            return

        if violation_id == "all":
            count = len(violations)
            data["violations"][guild_key][user_key] = []
            _save_mod_data(data)
            await interaction.response.send_message(f"🧹 Cleared all {count} violation(s) for {member.mention}.")
            await _send_mod_log(interaction.guild, "clearviolations", member, interaction.user, f"Cleared all {count} violations")
            await self._store_mod_action(interaction.guild_id, "clearviolations", member, interaction.user, f"Cleared all {count} violations")
        else:
            vid = violation_id if violation_id.isdigit() else None
            if not vid:
                await interaction.response.send_message("❌ Invalid violation ID. Use a number or 'all'.", ephemeral=True)
                return
            vid = int(vid)
            before = len(violations)
            data["violations"][guild_key][user_key] = [v for v in violations if v.get("id") != vid]
            removed = before - len(data["violations"][guild_key][user_key])
            _save_mod_data(data)

            if removed:
                await interaction.response.send_message(f"🧹 Removed violation #{vid} from {member.mention}.")
                await _send_mod_log(interaction.guild, "clearviolations", member, interaction.user, f"Removed violation #{vid}")
                await self._store_mod_action(interaction.guild_id, "clearviolations", member, interaction.user, f"Removed violation #{vid}")
            else:
                await interaction.response.send_message(f"❌ Violation #{vid} not found for {member.mention}.", ephemeral=True)

    @commands.command(name="clearwarns")
    @commands.check(staff_required)
    async def cmd_clearwarns(self, ctx, member: discord.Member, warn_id: str = "all"):
        """Clear warnings for a member. Usage: !clearwarns <member> [warn_id|all]"""
        data = _load_mod_data()
        guild_key = str(ctx.guild.id)
        user_key = str(member.id)

        warns = data.get("warnings", {}).get(guild_key, {}).get(user_key, [])

        if not warns:
            await ctx.send(f"✅ {member.mention} has no warnings to clear.")
            return

        if warn_id == "all":
            data["warnings"][guild_key][user_key] = []
            _save_mod_data(data)
            await ctx.send(f"🧹 Cleared all {len(warns)} warning(s) for {member.mention}.")
            await _send_mod_log(ctx.guild, "clearwarns", member, ctx.author, f"Cleared all {len(warns)} warnings")
            await self._store_mod_action(ctx.guild.id, "clearwarns", member, ctx.author, f"Cleared all {len(warns)} warnings")
        else:
            try:
                wid = int(warn_id)
            except ValueError:
                await ctx.send("❌ Invalid warn ID. Use a number or 'all'.")
                return

            before = len(warns)
            data["warnings"][guild_key][user_key] = [w for w in warns if w.get("id") != wid]
            removed = before - len(data["warnings"][guild_key][user_key])
            _save_mod_data(data)

            if removed:
                await ctx.send(f"🧹 Removed warning #{wid} from {member.mention}.")
                await _send_mod_log(ctx.guild, "clearwarns", member, ctx.author, f"Removed warning #{wid}")
                await self._store_mod_action(ctx.guild.id, "clearwarns", member, ctx.author, f"Removed warning #{wid}")
            else:
                await ctx.send(f"❌ Warning #{wid} not found for {member.mention}.")

    @app_commands.command(name="clearwarns", description="Clear warnings for a member")
    @app_commands.describe(
        member="The member to clear warnings for",
        warn_id="Warning ID to remove, or 'all' for all warnings",
    )
    @app_commands.check(slash_staff_required)
    async def slash_clearwarns(self, interaction: discord.Interaction, member: discord.Member, warn_id: str = "all"):
        """Clear warnings for a member."""
        data = _load_mod_data()
        guild_key = str(interaction.guild_id)
        user_key = str(member.id)

        warns = data.get("warnings", {}).get(guild_key, {}).get(user_key, [])

        if not warns:
            await interaction.response.send_message(f"✅ {member.mention} has no warnings to clear.", ephemeral=True)
            return

        if warn_id == "all":
            data["warnings"][guild_key][user_key] = []
            _save_mod_data(data)
            await interaction.response.send_message(f"🧹 Cleared all {len(warns)} warning(s) for {member.mention}.")
            await _send_mod_log(interaction.guild, "clearwarns", member, interaction.user,
                                 f"Cleared all {len(warns)} warnings")
            await self._store_mod_action(interaction.guild_id, "clearwarns", member, interaction.user,
                                          f"Cleared all {len(warns)} warnings")
        else:
            wid = warn_id if warn_id.isdigit() else None
            if not wid:
                await interaction.response.send_message("❌ Invalid warn ID. Use a number or 'all'.", ephemeral=True)
                return
            wid = int(wid)
            before = len(warns)
            data["warnings"][guild_key][user_key] = [w for w in warns if w.get("id") != wid]
            removed = before - len(data["warnings"][guild_key][user_key])
            _save_mod_data(data)

            if removed:
                await interaction.response.send_message(f"🧹 Removed warning #{wid} from {member.mention}.")
                await _send_mod_log(interaction.guild, "clearwarns", member, interaction.user,
                                     f"Removed warning #{wid}")
                await self._store_mod_action(interaction.guild_id, "clearwarns", member, interaction.user,
                                              f"Removed warning #{wid}")
            else:
                await interaction.response.send_message(f"❌ Warning #{wid} not found for {member.mention}.", ephemeral=True)

    # ── Kick / Ban / Timeout ──────────────────────────

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def cmd_kick(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        """Kick a member. Usage: !kick <member> [reason]"""
        if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            await ctx.send("❌ You cannot kick someone with a higher or equal role.")
            return

        try:
            await member.kick(reason=f"{reason} | Kicked by {ctx.author}")
            await ctx.send(f"👢 {member.mention} has been kicked.\n**Reason:** {reason}")
            await _send_mod_log(ctx.guild, "kick", member, ctx.author, reason)
            await self._store_mod_action(ctx.guild.id, "kick", member, ctx.author, reason)
            await self._store_violation(ctx.guild.id, member.id, "kick", reason, ctx.author)
        except Exception as e:
            await ctx.send(f"❌ Failed to kick {member.mention}: {e}")

    @app_commands.command(name="kick", description="Kick a member from the server")
    @app_commands.describe(member="The member to kick", reason="The reason for the kick")
    @app_commands.checks.has_permissions(kick_members=True)
    async def slash_kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        """Kick a member."""
        if member.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
            await interaction.response.send_message("❌ You cannot kick someone with a higher or equal role.", ephemeral=True)
            return

        try:
            await member.kick(reason=f"{reason} | Kicked by {interaction.user}")
            await interaction.response.send_message(f"👢 {member.mention} has been kicked.\n**Reason:** {reason}")
            await _send_mod_log(interaction.guild, "kick", member, interaction.user, reason)
            await self._store_mod_action(interaction.guild_id, "kick", member, interaction.user, reason)
            await self._store_violation(interaction.guild_id, member.id, "kick", reason, interaction.user)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to kick {member.mention}: {e}", ephemeral=True)

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def cmd_ban(self, ctx, user: str, days: int = 0, *, reason: str = "No reason provided"):
        """Ban a user. Usage: !ban <user_id|@mention> [delete_days] [reason]"""
        # Resolve the user
        member = await self._resolve_member(ctx.guild, user)
        if not member:
            await ctx.send("❌ User not found. Try using their ID.")
            return

        if isinstance(member, discord.Member):
            if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
                await ctx.send("❌ You cannot ban someone with a higher or equal role.")
                return

        try:
            await ctx.guild.ban(member, reason=f"{reason} | Banned by {ctx.author}", delete_message_days=days)
            await ctx.send(f"🔨 {member} has been banned.\n**Reason:** {reason}")
            await _send_mod_log(ctx.guild, "ban", member, ctx.author, reason)
            await self._store_mod_action(ctx.guild.id, "ban", member, ctx.author, reason)
            await self._store_violation(ctx.guild.id, member.id, "ban", reason, ctx.author)
        except Exception as e:
            await ctx.send(f"❌ Failed to ban {member}: {e}")

    @app_commands.command(name="ban", description="Ban a user from the server")
    @app_commands.describe(
        user="The user ID or mention to ban",
        delete_days="Delete messages from this many days (0-7)",
        reason="The reason for the ban",
    )
    @app_commands.checks.has_permissions(ban_members=True)
    async def slash_ban(self, interaction: discord.Interaction, user: str, delete_days: int = 0, reason: str = "No reason provided"):
        """Ban a user."""
        member = await self._resolve_member(interaction.guild, user)
        if not member:
            await interaction.response.send_message("❌ User not found. Try using their ID.", ephemeral=True)
            return

        if isinstance(member, discord.Member):
            if member.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
                await interaction.response.send_message("❌ You cannot ban someone with a higher or equal role.", ephemeral=True)
                return

        try:
            delete_days = max(0, min(delete_days, 7))
            await interaction.guild.ban(member, reason=f"{reason} | Banned by {interaction.user}",
                                         delete_message_days=delete_days)
            await interaction.response.send_message(f"🔨 {member} has been banned.\n**Reason:** {reason}")
            await _send_mod_log(interaction.guild, "ban", member, interaction.user, reason)
            await self._store_mod_action(interaction.guild_id, "ban", member, interaction.user, reason)
            await self._store_violation(interaction.guild_id, member.id, "ban", reason, interaction.user)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to ban {member}: {e}", ephemeral=True)

    @commands.command(name="timeout")
    @commands.has_permissions(moderate_members=True)
    async def cmd_timeout(self, ctx, member: discord.Member, duration: str = "10m", *, reason: str = "No reason provided"):
        """Timeout a member. Usage: !timeout <member> <duration> [reason] (e.g. 10m, 1h, 7d)"""
        if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            await ctx.send("❌ You cannot timeout someone with a higher or equal role.")
            return

        seconds = _parse_duration(duration)
        if not seconds or seconds < 10:
            await ctx.send("❌ Invalid duration. Use e.g. `10m`, `1h`, `30m`, `7d` (minimum 10 seconds).")
            return

        if seconds > 2419200:  # 28 days max
            await ctx.send("❌ Duration cannot exceed 28 days.")
            return

        try:
            until = discord.utils.utcnow() + discord.timedelta(seconds=seconds)
            await member.timeout(until, reason=f"{reason} | Timed out by {ctx.author} | Duration: {duration}")
            dur_str = _format_duration(seconds)
            await ctx.send(f"🔇 {member.mention} has been timed out for **{dur_str}**.\n**Reason:** {reason}")
            await _send_mod_log(ctx.guild, "timeout", member, ctx.author, reason, duration=dur_str)
            await self._store_mod_action(ctx.guild.id, "timeout", member, ctx.author, f"{reason} ({dur_str})")
            await self._store_violation(ctx.guild.id, member.id, "timeout", reason, ctx.author, duration=dur_str)
        except Exception as e:
            await ctx.send(f"❌ Failed to timeout {member.mention}: {e}")

    @app_commands.command(name="timeout", description="Timeout a member")
    @app_commands.describe(
        member="The member to timeout",
        duration="Duration (e.g. 10m, 1h, 7d)",
        reason="The reason for the timeout",
    )
    @app_commands.checks.has_permissions(moderate_members=True)
    async def slash_timeout(self, interaction: discord.Interaction, member: discord.Member,
                            duration: str = "10m", reason: str = "No reason provided"):
        """Timeout a member."""
        if member.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
            await interaction.response.send_message("❌ You cannot timeout someone with a higher or equal role.", ephemeral=True)
            return

        seconds = _parse_duration(duration)
        if not seconds or seconds < 10:
            await interaction.response.send_message("❌ Invalid duration. Use e.g. `10m`, `1h`, `30m`, `7d`.", ephemeral=True)
            return

        if seconds > 2419200:
            await interaction.response.send_message("❌ Duration cannot exceed 28 days.", ephemeral=True)
            return

        try:
            until = discord.utils.utcnow() + discord.timedelta(seconds=seconds)
            await member.timeout(until, reason=f"{reason} | Timed out by {interaction.user} | Duration: {duration}")
            dur_str = _format_duration(seconds)
            await interaction.response.send_message(f"🔇 {member.mention} has been timed out for **{dur_str}**.\n**Reason:** {reason}")
            await _send_mod_log(interaction.guild, "timeout", member, interaction.user, reason, duration=dur_str)
            await self._store_mod_action(interaction.guild_id, "timeout", member, interaction.user, f"{reason} ({dur_str})")
            await self._store_violation(interaction.guild_id, member.id, "timeout", reason, interaction.user, duration=dur_str)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to timeout {member.mention}: {e}", ephemeral=True)

    # ── Mute / Unmute ─────────────────────────────────

    @commands.command(name="mute")
    @commands.has_permissions(moderate_members=True)
    async def cmd_mute(self, ctx, member: discord.Member, duration: str = "1h", *, reason: str = "No reason provided"):
        """Mute a member. Usage: !mute <member> <duration> [reason]"""
        if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            await ctx.send("❌ You cannot mute someone with a higher or equal role.")
            return

        seconds = _parse_duration(duration)
        if not seconds:
            await ctx.send("❌ Invalid duration. Use e.g. `10m`, `1h`, `30m`, `7d`.")
            return

        # Try timeout-based mute first (Discord's built-in timeout)
        try:
            until = discord.utils.utcnow() + discord.timedelta(seconds=seconds) if seconds else None
            if until and seconds <= 2419200:
                await member.timeout(until, reason=f"Mute: {reason} | By {ctx.author}")
                dur_str = _format_duration(seconds)
                await ctx.send(f"🔇 {member.mention} has been muted for **{dur_str}**.\n**Reason:** {reason}")
                await _send_mod_log(ctx.guild, "mute", member, ctx.author, reason, duration=dur_str)
                await self._store_mod_action(ctx.guild.id, "mute", member, ctx.author, f"{reason} ({dur_str})")
                await self._store_violation(ctx.guild.id, member.id, "mute", reason, ctx.author, duration=dur_str)

                # Store mute record
                data = _load_mod_data()
                guild_key = str(ctx.guild.id)
                user_key = str(member.id)
                if "mutes" not in data:
                    data["mutes"] = {}
                if guild_key not in data["mutes"]:
                    data["mutes"][guild_key] = {}
                data["mutes"][guild_key][user_key] = {
                    "until": until.timestamp(),
                    "moderator": f"{ctx.author} ({ctx.author.id})",
                    "reason": reason,
                }
                _save_mod_data(data)
                return
        except Exception:
            pass

        # Fallback: role-based mute
        muted_role_id = _get_muted_role_id()
        if not muted_role_id:
            await ctx.send("❌ No muted role configured. Add `muted_role_id` to `manager_bot.moderation` in settings.")
            return

        muted_role = ctx.guild.get_role(int(muted_role_id))
        if not muted_role:
            await ctx.send("❌ Muted role not found on this server. Check the role ID in config.")
            return

        try:
            await member.add_roles(muted_role, reason=f"Mute: {reason} | By {ctx.author}")
            dur_str = _format_duration(seconds) if seconds else "indefinite"
            await ctx.send(f"🔇 {member.mention} has been muted for **{dur_str}** (role-based).\n**Reason:** {reason}")
            await _send_mod_log(ctx.guild, "mute", member, ctx.author, reason, duration=dur_str)
            await self._store_mod_action(ctx.guild.id, "mute", member, ctx.author, f"{reason} ({dur_str})")
            await self._store_violation(ctx.guild.id, member.id, "mute", reason, ctx.author, duration=dur_str)

            # Store mute record
            data = _load_mod_data()
            guild_key = str(ctx.guild.id)
            user_key = str(member.id)
            if "mutes" not in data:
                data["mutes"] = {}
            if guild_key not in data["mutes"]:
                data["mutes"][guild_key] = {}
            data["mutes"][guild_key][user_key] = {
                "role": muted_role_id,
                "until": (time.time() + seconds) if seconds else None,
                "moderator": f"{ctx.author} ({ctx.author.id})",
                "reason": reason,
            }
            _save_mod_data(data)
        except Exception as e:
            await ctx.send(f"❌ Failed to mute {member.mention}: {e}")

    @app_commands.command(name="mute", description="Mute a member")
    @app_commands.describe(
        member="The member to mute",
        duration="Duration (e.g. 10m, 1h, 7d, or 'perm' for permanent)",
        reason="The reason for the mute",
    )
    @app_commands.checks.has_permissions(moderate_members=True)
    async def slash_mute(self, interaction: discord.Interaction, member: discord.Member,
                         duration: str = "1h", reason: str = "No reason provided"):
        """Mute a member."""
        if member.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
            await interaction.response.send_message("❌ You cannot mute someone with a higher or equal role.", ephemeral=True)
            return

        seconds = _parse_duration(duration)
        if not seconds:
            await interaction.response.send_message("❌ Invalid duration. Use e.g. `10m`, `1h`, `30m`, `7d`.", ephemeral=True)
            return

        try:
            until = discord.utils.utcnow() + discord.timedelta(seconds=seconds) if seconds else None
            if until and seconds <= 2419200:
                await member.timeout(until, reason=f"Mute: {reason} | By {interaction.user}")
                dur_str = _format_duration(seconds)
                await interaction.response.send_message(f"🔇 {member.mention} has been muted for **{dur_str}**.\n**Reason:** {reason}")
                await _send_mod_log(interaction.guild, "mute", member, interaction.user, reason, duration=dur_str)
                await self._store_mod_action(interaction.guild_id, "mute", member, interaction.user, f"{reason} ({dur_str})")
                await self._store_violation(interaction.guild_id, member.id, "mute", reason, interaction.user, duration=dur_str)

                data = _load_mod_data()
                guild_key = str(interaction.guild_id)
                user_key = str(member.id)
                if "mutes" not in data:
                    data["mutes"] = {}
                if guild_key not in data["mutes"]:
                    data["mutes"][guild_key] = {}
                data["mutes"][guild_key][user_key] = {
                    "until": until.timestamp(),
                    "moderator": f"{interaction.user} ({interaction.user.id})",
                    "reason": reason,
                }
                _save_mod_data(data)
                return
        except Exception:
            pass

        muted_role_id = _get_muted_role_id()
        if not muted_role_id:
            await interaction.response.send_message(
                "❌ No muted role configured. Add `muted_role_id` to manager_bot.moderation in settings.",
                ephemeral=True)
            return

        muted_role = interaction.guild.get_role(int(muted_role_id))
        if not muted_role:
            await interaction.response.send_message("❌ Muted role not found on this server.", ephemeral=True)
            return

        try:
            await member.add_roles(muted_role, reason=f"Mute: {reason} | By {interaction.user}")
            dur_str = _format_duration(seconds)
            await interaction.response.send_message(f"🔇 {member.mention} has been muted for **{dur_str}** (role-based).\n**Reason:** {reason}")
            await _send_mod_log(interaction.guild, "mute", member, interaction.user, reason, duration=dur_str)
            await self._store_mod_action(interaction.guild_id, "mute", member, interaction.user, f"{reason} ({dur_str})")
            await self._store_violation(interaction.guild_id, member.id, "mute", reason, interaction.user, duration=dur_str)

            data = _load_mod_data()
            guild_key = str(interaction.guild_id)
            user_key = str(member.id)
            if "mutes" not in data:
                data["mutes"] = {}
            if guild_key not in data["mutes"]:
                data["mutes"][guild_key] = {}
            data["mutes"][guild_key][user_key] = {
                "role": muted_role_id,
                "until": time.time() + seconds if seconds else None,
                "moderator": f"{interaction.user} ({interaction.user.id})",
                "reason": reason,
            }
            _save_mod_data(data)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to mute {member.mention}: {e}", ephemeral=True)

    @commands.command(name="unmute")
    @commands.has_permissions(moderate_members=True)
    async def cmd_unmute(self, ctx, member: discord.Member, *, reason: str = "Mute lifted"):
        """Unmute a member. Usage: !unmute <member> [reason]"""
        try:
            await member.timeout(None, reason=f"Unmute: {reason} | By {ctx.author}")
            await ctx.send(f"🔊 {member.mention} has been unmuted.\n**Reason:** {reason}")
            await _send_mod_log(ctx.guild, "unmute", member, ctx.author, reason)
            await self._store_mod_action(ctx.guild.id, "unmute", member, ctx.author, reason)

            # Clean up mute record
            data = _load_mod_data()
            guild_key = str(ctx.guild.id)
            user_key = str(member.id)
            if data.get("mutes", {}).get(guild_key, {}).get(user_key):
                del data["mutes"][guild_key][user_key]
                _save_mod_data(data)
            return
        except Exception:
            pass

        # Role-based unmute
        muted_role_id = _get_muted_role_id()
        if muted_role_id:
            muted_role = ctx.guild.get_role(int(muted_role_id))
            if muted_role and muted_role in member.roles:
                try:
                    await member.remove_roles(muted_role, reason=f"Unmute: {reason} | By {ctx.author}")
                    await ctx.send(f"🔊 {member.mention} has been unmuted (role removed).\n**Reason:** {reason}")
                    await _send_mod_log(ctx.guild, "unmute", member, ctx.author, reason)
                    await self._store_mod_action(ctx.guild.id, "unmute", member, ctx.author, reason)

                    data = _load_mod_data()
                    guild_key = str(ctx.guild.id)
                    user_key = str(member.id)
                    if data.get("mutes", {}).get(guild_key, {}).get(user_key):
                        del data["mutes"][guild_key][user_key]
                        _save_mod_data(data)
                    return
                except Exception as e:
                    await ctx.send(f"❌ Failed to unmute {member.mention}: {e}")
                    return

        await ctx.send(f"❌ {member.mention} is not currently muted.")

    @app_commands.command(name="unmute", description="Unmute a member")
    @app_commands.describe(member="The member to unmute", reason="The reason for the unmute")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def slash_unmute(self, interaction: discord.Interaction, member: discord.Member,
                           reason: str = "Mute lifted"):
        """Unmute a member."""
        try:
            await member.timeout(None, reason=f"Unmute: {reason} | By {interaction.user}")
            await interaction.response.send_message(f"🔊 {member.mention} has been unmuted.\n**Reason:** {reason}")
            await _send_mod_log(interaction.guild, "unmute", member, interaction.user, reason)
            await self._store_mod_action(interaction.guild_id, "unmute", member, interaction.user, reason)

            data = _load_mod_data()
            guild_key = str(interaction.guild_id)
            user_key = str(member.id)
            if data.get("mutes", {}).get(guild_key, {}).get(user_key):
                del data["mutes"][guild_key][user_key]
                _save_mod_data(data)
            return
        except Exception:
            pass

        muted_role_id = _get_muted_role_id()
        if muted_role_id:
            muted_role = interaction.guild.get_role(int(muted_role_id))
            if muted_role and muted_role in member.roles:
                try:
                    await member.remove_roles(muted_role, reason=f"Unmute: {reason} | By {interaction.user}")
                    await interaction.response.send_message(f"🔊 {member.mention} has been unmuted (role removed).\n**Reason:** {reason}")
                    await _send_mod_log(interaction.guild, "unmute", member, interaction.user, reason)
                    await self._store_mod_action(interaction.guild_id, "unmute", member, interaction.user, reason)

                    data = _load_mod_data()
                    guild_key = str(interaction.guild_id)
                    user_key = str(member.id)
                    if data.get("mutes", {}).get(guild_key, {}).get(user_key):
                        del data["mutes"][guild_key][user_key]
                        _save_mod_data(data)
                    return
                except Exception as e:
                    await interaction.response.send_message(f"❌ Failed to unmute {member.mention}: {e}", ephemeral=True)
                    return

        await interaction.response.send_message(f"❌ {member.mention} is not currently muted.", ephemeral=True)

    # ── Purge ─────────────────────────────────────────

    @commands.command(name="purge")
    @commands.has_permissions(manage_messages=True)
    async def cmd_purge(self, ctx, count: int = 10, target_member: discord.Member = None):
        """Purge messages. Usage: !purge <count> [@member]"""
        if count < 1 or count > 100:
            await ctx.send("❌ Count must be between 1 and 100.")
            return

        try:
            if target_member:
                def check(m):
                    return m.author.id == target_member.id

                deleted = await ctx.channel.purge(limit=count + 1, check=check, bulk=True)
                await _send_mod_log(ctx.guild, "purge", f"{len(deleted)} messages", ctx.author,
                                     f"Purged {len(deleted)} messages from {target_member} in #{ctx.channel.name}")
            else:
                deleted = await ctx.channel.purge(limit=count + 1, bulk=True)
                await _send_mod_log(ctx.guild, "purge", f"{len(deleted)} messages", ctx.author,
                                     f"Purged {len(deleted)} messages in #{ctx.channel.name}")

            await self._store_mod_action(ctx.guild.id, "purge", f"{len(deleted)} msgs in #{ctx.channel.name}",
                                          ctx.author)
            msg = await ctx.send(f"🗑️ Purged **{len(deleted)}** message(s).", delete_after=3)
        except Exception as e:
            await ctx.send(f"❌ Failed to purge messages: {e}")

    @app_commands.command(name="purge", description="Purge messages in this channel")
    @app_commands.describe(
        count="Number of messages to delete (1-100)",
        member="Optional: only delete messages from this member",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_purge(self, interaction: discord.Interaction, count: int = 10,
                          member: discord.Member = None):
        """Purge messages in this channel."""
        if count < 1 or count > 100:
            await interaction.response.send_message("❌ Count must be between 1 and 100.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            if member:
                def check(m):
                    return m.author.id == member.id

                deleted = await interaction.channel.purge(limit=count, check=check, bulk=True)
                await _send_mod_log(interaction.guild, "purge", f"{len(deleted)} messages", interaction.user,
                                     f"Purged {len(deleted)} messages from {member} in #{interaction.channel.name}")
            else:
                deleted = await interaction.channel.purge(limit=count, bulk=True)
                await _send_mod_log(interaction.guild, "purge", f"{len(deleted)} messages", interaction.user,
                                     f"Purged {len(deleted)} messages in #{interaction.channel.name}")

            await self._store_mod_action(interaction.guild_id, "purge",
                                          f"{len(deleted)} msgs in #{interaction.channel.name}", interaction.user)
            await interaction.followup.send(f"🗑️ Purged **{len(deleted)}** message(s).", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to purge messages: {e}", ephemeral=True)

    # ── Slowmode ──────────────────────────────────────

    @commands.command(name="slowmode")
    @commands.has_permissions(manage_channels=True)
    async def cmd_slowmode(self, ctx, seconds: str = "0"):
        """Set slowmode for the channel. Usage: !slowmode <seconds> (0 to disable)"""
        if seconds.lower() in ("off", "disable", "0"):
            secs = 0
        else:
            try:
                secs = int(seconds)
            except ValueError:
                parsed = _parse_duration(seconds)
                secs = parsed if parsed else 0

        if secs < 0 or secs > 21600:
            await ctx.send("❌ Slowmode must be between 0 and 21600 seconds (6 hours).")
            return

        try:
            await ctx.channel.edit(slowmode_delay=secs)
            if secs == 0:
                await ctx.send(f"🐢 Slowmode has been **disabled** in this channel.")
            else:
                await ctx.send(f"🐢 Slowmode set to **{secs} seconds** in this channel.")

            await _send_mod_log(ctx.guild, "slowmode", f"#{ctx.channel.name}", ctx.author,
                                 f"Set slowmode to {secs}s")
            await self._store_mod_action(ctx.guild.id, "slowmode", f"#{ctx.channel.name} → {secs}s", ctx.author)
        except Exception as e:
            await ctx.send(f"❌ Failed to set slowmode: {e}")

    @app_commands.command(name="slowmode", description="Set slowmode for this channel")
    @app_commands.describe(seconds="Slowmode in seconds (0 to disable, max 21600)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slash_slowmode(self, interaction: discord.Interaction, seconds: int = 0):
        """Set slowmode for this channel."""
        if seconds < 0 or seconds > 21600:
            await interaction.response.send_message("❌ Slowmode must be between 0 and 21600 seconds (6 hours).",
                                                     ephemeral=True)
            return

        try:
            await interaction.channel.edit(slowmode_delay=seconds)
            if seconds == 0:
                await interaction.response.send_message("🐢 Slowmode has been **disabled** in this channel.")
            else:
                await interaction.response.send_message(f"🐢 Slowmode set to **{seconds} seconds** in this channel.")

            await _send_mod_log(interaction.guild, "slowmode", f"#{interaction.channel.name}", interaction.user,
                                 f"Set slowmode to {seconds}s")
            await self._store_mod_action(interaction.guild_id, "slowmode", f"#{interaction.channel.name} → {seconds}s",
                                          interaction.user)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to set slowmode: {e}", ephemeral=True)

    # ── Lockdown ──────────────────────────────────────

    @commands.command(name="lock")
    @commands.has_permissions(manage_channels=True)
    async def cmd_lock(self, ctx):
        """Lock this channel (deny send messages for @everyone). Usage: !lock"""
        try:
            overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = False
            await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
            self._locked_channels.add(ctx.channel.id)

            await ctx.send(f"🔒 Channel has been **locked**. Only members with manage channels can speak.")

            await _send_mod_log(ctx.guild, "lock", f"#{ctx.channel.name}", ctx.author,
                                 "Channel locked (send messages denied for @everyone)")
            await self._store_mod_action(ctx.guild.id, "lock", f"#{ctx.channel.name}", ctx.author)
        except Exception as e:
            await ctx.send(f"❌ Failed to lock channel: {e}")

    @app_commands.command(name="lock", description="Lock this channel (deny send messages for @everyone)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slash_lock(self, interaction: discord.Interaction):
        """Lock this channel."""
        try:
            overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
            overwrite.send_messages = False
            await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
            self._locked_channels.add(interaction.channel.id)

            await interaction.response.send_message(f"🔒 Channel has been **locked**.")

            await _send_mod_log(interaction.guild, "lock", f"#{interaction.channel.name}", interaction.user,
                                 "Channel locked")
            await self._store_mod_action(interaction.guild_id, "lock", f"#{interaction.channel.name}", interaction.user)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to lock channel: {e}", ephemeral=True)

    @commands.command(name="unlock")
    @commands.has_permissions(manage_channels=True)
    async def cmd_unlock(self, ctx):
        """Unlock this channel. Usage: !unlock"""
        try:
            overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = None
            await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
            self._locked_channels.discard(ctx.channel.id)

            await ctx.send(f"🔓 Channel has been **unlocked**.")

            await _send_mod_log(ctx.guild, "unlock", f"#{ctx.channel.name}", ctx.author,
                                 "Channel unlocked (send messages restored for @everyone)")
            await self._store_mod_action(ctx.guild.id, "unlock", f"#{ctx.channel.name}", ctx.author)
        except Exception as e:
            await ctx.send(f"❌ Failed to unlock channel: {e}")

    @app_commands.command(name="unlock", description="Unlock this channel")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slash_unlock(self, interaction: discord.Interaction):
        """Unlock this channel."""
        try:
            overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
            overwrite.send_messages = None
            await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
            self._locked_channels.discard(interaction.channel.id)

            await interaction.response.send_message(f"🔓 Channel has been **unlocked**.")

            await _send_mod_log(interaction.guild, "unlock", f"#{interaction.channel.name}", interaction.user,
                                 "Channel unlocked")
            await self._store_mod_action(interaction.guild_id, "unlock", f"#{interaction.channel.name}",
                                          interaction.user)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to unlock channel: {e}", ephemeral=True)

    # ── Quarantine ───────────────────────────────────

    @commands.command(name="quarantine")
    @commands.has_permissions(moderate_members=True)
    async def cmd_quarantine(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        """Quarantine a member: strip all roles, assign quarantine role.
        Usage: !quarantine <member> [reason]
        """
        if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            await ctx.send("❌ You cannot quarantine someone with a higher or equal role.")
            return

        q_role_id = _get_quarantine_role_id()
        if not q_role_id:
            await ctx.send("❌ No quarantine role configured. Add `quarantine_role_id` to `manager_bot.moderation` in settings.")
            return

        q_role = ctx.guild.get_role(int(q_role_id))
        if not q_role:
            await ctx.send("❌ Quarantine role not found on this server. Check the role ID in config.")
            return

        if q_role in member.roles:
            await ctx.send(f"⚠️ {member.mention} is already quarantined.")
            return

        # Save current roles (exclude @everyone and managed roles)
        saved_roles = []
        for role in member.roles:
            if role.is_default():
                continue
            if role.managed:
                continue
            saved_roles.append(role.id)

        # Remove all roles and assign quarantine role
        try:
            roles_to_remove = [r for r in member.roles if not r.is_default() and not r.managed]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason=f"Quarantine: {reason} | By {ctx.author}")
            await member.add_roles(q_role, reason=f"Quarantine: {reason} | By {ctx.author}")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to manage this member's roles.")
            return
        except Exception as e:
            await ctx.send(f"❌ Failed to quarantine {member.mention}: {e}")
            return

        # Persist saved roles
        data = _ensure_quarantine_data()
        guild_key = str(ctx.guild.id)
        user_key = str(member.id)
        if guild_key not in data["quarantines"]:
            data["quarantines"][guild_key] = {}
        data["quarantines"][guild_key][user_key] = {
            "roles": saved_roles,
            "moderator": f"{ctx.author} ({ctx.author.id})",
            "reason": reason,
            "timestamp": time.time(),
        }
        _save_mod_data(data)

        await ctx.send(f"🔒 {member.mention} has been **quarantined**.\n**Reason:** {reason}\n"
                       f"**Roles saved:** {len(saved_roles)}")
        await _send_mod_log(ctx.guild, "quarantine", member, ctx.author, reason)
        await self._store_mod_action(ctx.guild.id, "quarantine", member, ctx.author, reason)
        await self._store_violation(ctx.guild.id, member.id, "quarantine", reason, ctx.author)

    @app_commands.command(name="quarantine", description="Quarantine a member — strip all roles and assign quarantine role")
    @app_commands.describe(member="The member to quarantine", reason="The reason for the quarantine")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def slash_quarantine(self, interaction: discord.Interaction, member: discord.Member,
                               reason: str = "No reason provided"):
        """Quarantine a member."""
        if member.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
            await interaction.response.send_message(
                "❌ You cannot quarantine someone with a higher or equal role.", ephemeral=True)
            return

        q_role_id = _get_quarantine_role_id()
        if not q_role_id:
            await interaction.response.send_message(
                "❌ No quarantine role configured. Add `quarantine_role_id` to manager_bot.moderation in settings.",
                ephemeral=True)
            return

        q_role = interaction.guild.get_role(int(q_role_id))
        if not q_role:
            await interaction.response.send_message(
                "❌ Quarantine role not found on this server.", ephemeral=True)
            return

        if q_role in member.roles:
            await interaction.response.send_message(f"⚠️ {member.mention} is already quarantined.", ephemeral=True)
            return

        saved_roles = []
        for role in member.roles:
            if role.is_default():
                continue
            if role.managed:
                continue
            saved_roles.append(role.id)

        try:
            roles_to_remove = [r for r in member.roles if not r.is_default() and not r.managed]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason=f"Quarantine: {reason} | By {interaction.user}")
            await member.add_roles(q_role, reason=f"Quarantine: {reason} | By {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to manage this member's roles.", ephemeral=True)
            return
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to quarantine {member.mention}: {e}", ephemeral=True)
            return

        data = _ensure_quarantine_data()
        guild_key = str(interaction.guild_id)
        user_key = str(member.id)
        if guild_key not in data["quarantines"]:
            data["quarantines"][guild_key] = {}
        data["quarantines"][guild_key][user_key] = {
            "roles": saved_roles,
            "moderator": f"{interaction.user} ({interaction.user.id})",
            "reason": reason,
            "timestamp": time.time(),
        }
        _save_mod_data(data)

        await interaction.response.send_message(
            f"🔒 {member.mention} has been **quarantined**.\n**Reason:** {reason}\n**Roles saved:** {len(saved_roles)}")
        await _send_mod_log(interaction.guild, "quarantine", member, interaction.user, reason)
        await self._store_mod_action(interaction.guild_id, "quarantine", member, interaction.user, reason)
        await self._store_violation(interaction.guild_id, member.id, "quarantine", reason, interaction.user)

    @commands.command(name="unquarantine")
    @commands.has_permissions(moderate_members=True)
    async def cmd_unquarantine(self, ctx, member: discord.Member, *, reason: str = "Quarantine lifted"):
        """Release a member from quarantine: remove quarantine role, restore saved roles.
        Usage: !unquarantine <member> [reason]
        """
        q_role_id = _get_quarantine_role_id()
        if not q_role_id:
            await ctx.send("❌ No quarantine role configured.")
            return

        q_role = ctx.guild.get_role(int(q_role_id))
        if not q_role:
            await ctx.send("❌ Quarantine role not found on this server.")
            return

        if q_role not in member.roles:
            await ctx.send(f"⚠️ {member.mention} is not currently quarantined.")
            return

        # Load saved roles
        data = _ensure_quarantine_data()
        guild_key = str(ctx.guild.id)
        user_key = str(member.id)
        record = data.get("quarantines", {}).get(guild_key, {}).get(user_key)
        saved_role_ids = record.get("roles", []) if record else []

        roles_to_restore = []
        for rid in saved_role_ids:
            role = ctx.guild.get_role(rid)
            if role and not role.managed and role != q_role:
                roles_to_restore.append(role)

        try:
            await member.remove_roles(q_role, reason=f"Unquarantine: {reason} | By {ctx.author}")
            if roles_to_restore:
                await member.add_roles(*roles_to_restore, reason=f"Unquarantine: {reason} | By {ctx.author}")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to manage this member's roles.")
            return
        except Exception as e:
            await ctx.send(f"❌ Failed to unquarantine {member.mention}: {e}")
            return

        # Clean up record
        if guild_key in data.get("quarantines", {}):
            data["quarantines"][guild_key].pop(user_key, None)
            if not data["quarantines"][guild_key]:
                del data["quarantines"][guild_key]
        _save_mod_data(data)

        await ctx.send(f"🔓 {member.mention} has been **unquarantined**.\n**Reason:** {reason}\n"
                       f"**Roles restored:** {len(roles_to_restore)}")
        await _send_mod_log(ctx.guild, "unquarantine", member, ctx.author, reason)
        await self._store_mod_action(ctx.guild.id, "unquarantine", member, ctx.author, reason)

    @app_commands.command(name="unquarantine", description="Release a member from quarantine and restore their roles")
    @app_commands.describe(member="The member to release", reason="The reason for the release")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def slash_unquarantine(self, interaction: discord.Interaction, member: discord.Member,
                                 reason: str = "Quarantine lifted"):
        """Release a member from quarantine."""
        q_role_id = _get_quarantine_role_id()
        if not q_role_id:
            await interaction.response.send_message("❌ No quarantine role configured.", ephemeral=True)
            return

        q_role = interaction.guild.get_role(int(q_role_id))
        if not q_role:
            await interaction.response.send_message("❌ Quarantine role not found on this server.", ephemeral=True)
            return

        if q_role not in member.roles:
            await interaction.response.send_message(f"⚠️ {member.mention} is not currently quarantined.", ephemeral=True)
            return

        data = _ensure_quarantine_data()
        guild_key = str(interaction.guild_id)
        user_key = str(member.id)
        record = data.get("quarantines", {}).get(guild_key, {}).get(user_key)
        saved_role_ids = record.get("roles", []) if record else []

        roles_to_restore = []
        for rid in saved_role_ids:
            role = interaction.guild.get_role(rid)
            if role and not role.managed and role != q_role:
                roles_to_restore.append(role)

        try:
            await member.remove_roles(q_role, reason=f"Unquarantine: {reason} | By {interaction.user}")
            if roles_to_restore:
                await member.add_roles(*roles_to_restore, reason=f"Unquarantine: {reason} | By {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to manage this member's roles.", ephemeral=True)
            return
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to unquarantine {member.mention}: {e}", ephemeral=True)
            return

        if guild_key in data.get("quarantines", {}):
            data["quarantines"][guild_key].pop(user_key, None)
            if not data["quarantines"][guild_key]:
                del data["quarantines"][guild_key]
        _save_mod_data(data)

        await interaction.response.send_message(
            f"🔓 {member.mention} has been **unquarantined**.\n**Reason:** {reason}\n**Roles restored:** {len(roles_to_restore)}")
        await _send_mod_log(interaction.guild, "unquarantine", member, interaction.user, reason)
        await self._store_mod_action(interaction.guild_id, "unquarantine", member, interaction.user, reason)

    # ── Auto-Unmute Background Task ──────────────────

    @tasks.loop(seconds=30)
    async def _auto_unmute_loop(self):
        """Periodically checks for expired mutes and auto-unmutes.
        Runs every 30 seconds.
        """
        data = _load_mod_data()
        mutes = data.get("mutes", {})
        now = time.time()
        unmuted_any = False

        for guild_key in list(mutes.keys()):
            guild = self.bot.get_guild(int(guild_key))
            if not guild:
                # Guild not cached — skip, it will be retried
                continue

            for user_key in list(mutes.get(guild_key, {}).keys()):
                mute_record = mutes[guild_key][user_key]
                until = mute_record.get("until")

                # Skip indefinite mutes (None until) or mutes that haven't expired
                if until is None or until > now:
                    continue

                # This mute has expired — try to unmute
                member = guild.get_member(int(user_key))
                if not member:
                    # Member left the server — just clean up the record
                    del mutes[guild_key][user_key]
                    unmuted_any = True
                    _log.info(f"Auto-unmute: {user_key} left guild {guild_key}, cleared mute record")
                    continue

                try:
                    # Try timeout-based unmute first
                    if member.is_timed_out():
                        await member.timeout(None, reason="Auto-unmute: mute duration expired")
                    else:
                        # Try role-based unmute
                        mute_role_id = mute_record.get("role")
                        if mute_role_id:
                            mute_role = guild.get_role(int(mute_role_id))
                            if mute_role and mute_role in member.roles:
                                await member.remove_roles(mute_role, reason="Auto-unmute: mute duration expired")

                    # Log the auto-unmute
                    reason = mute_record.get("reason", "No reason recorded")
                    await _send_mod_log(guild, "unmute", member, guild.me,
                                        f"Auto-unmute: mute duration expired (original reason: {reason})")
                    await self._store_mod_action(guild.id, "unmute", member, guild.me,
                                                  f"Auto-unmute: expired mute ({reason})")

                    _log.info(f"Auto-unmuted {member} ({user_key}) in {guild.name}")

                except Exception as e:
                    _log.warning(f"Auto-unmute failed for {user_key} in guild {guild_key}: {e}")
                    # Don't remove the record if unmute failed — retry next cycle
                    continue

                # Remove the mute record
                del mutes[guild_key][user_key]
                unmuted_any = True

        if unmuted_any:
            data["mutes"] = mutes
            _save_mod_data(data)

    @_auto_unmute_loop.before_loop
    async def _before_auto_unmute(self):
        """Wait for the bot to be ready before starting the loop."""
        await self.bot.wait_until_ready()

    # ── Cog Lifecycle ────────────────────────────────

    async def cog_load(self):
        """Called when the cog is loaded. Start background loops if not already running."""
        if not self._auto_unmute_loop.is_running():
            self._auto_unmute_loop.start()
        if not self._auto_slowmode_loop.is_running():
            self._auto_slowmode_loop.start()

    async def cog_unload(self):
        """Called when the cog is unloaded. Cancel background loops."""
        self._auto_unmute_loop.cancel()
        self._auto_slowmode_loop.cancel()

    # ── Discord AutoMod Action Listener ─────────────────

    @commands.Cog.listener()
    async def on_automod_action(self, execution):
        """
        Listen for Discord's native AutoMod rule executions.
        Sends a DM warning to the user when their message triggers an AutoMod rule.
        """
        if not execution.guild:
            return

        # Ignore actions attributed to Discord's AutoMod system user
        if execution.user_id == 1533732730401980588:
            return

        guild = execution.guild
        user_id = execution.user_id
        rule_id = execution.rule_id
        trigger_type = execution.rule_trigger_type
        matched_keyword = execution.matched_keyword or execution.matched_content or ""

        # Try to get the member
        member = execution.member
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                pass

        if not member:
            return

        # Check if Discord AutoMod DM warnings are enabled
        auto_config = _get_auto_mod_config()
        discord_warn_enabled = auto_config.get("discord_automod_warn", True)

        # Build a human-readable reason
        trigger_names = {
            "keyword": "Blocked Keyword",
            "keyword_preset": "Profanity / Hate Speech",
            "spam": "Spam Detection",
            "mention_spam": "Mention Spam",
            "member_profile": "Flagged Profile Content",
        }
        # Use .name on the enum to safely get the member name (e.g. "keyword")
        trigger_type_name = trigger_type.name.lower() if hasattr(trigger_type, 'name') else str(trigger_type).rsplit('.', 1)[-1]
        reason_label = trigger_names.get(trigger_type_name, "AutoMod Rule")

        # Try to fetch the rule name from Discord
        rule_name = None
        try:
            rule = await execution.fetch_rule()
            rule_name = rule.name
        except Exception:
            pass

        if discord_warn_enabled:
            try:
                embed = discord.Embed(
                    title="🤖 Discord AutoMod — Warning",
                    description=(
                        f"**Server:** {guild.name}\n"
                        f"**Reason:** {reason_label}\n"
                    ),
                    color=0xFF44AA,
                    timestamp=discord.utils.utcnow(),
                )
                if rule_name:
                    embed.add_field(name="Rule", value=rule_name, inline=False)
                if matched_keyword:
                    embed.add_field(name="Matched Content", value=f"`{matched_keyword[:100]}`", inline=False)
                footer_parts = []
                if rule_name:
                    footer_parts.append(rule_name)
                footer_parts.append(f"Rule #{rule_id}")
                embed.set_footer(text=f"AutoMod • {' • '.join(footer_parts)}")

                await member.send(embed=embed)
            except Exception:
                pass

        # Log to mod log channel
        await _send_mod_log(
            guild, "automod", member, guild.me,
            f"Discord AutoMod triggered: {reason_label}"
            + (f" (matched: {matched_keyword[:50]})" if matched_keyword else "")
        )

        # Log internally
        await self._store_mod_action(
            guild.id, "automod", member, guild.me,
            f"Discord AutoMod: {reason_label}"
            + (f" — {matched_keyword[:80]}" if matched_keyword else "")
        )

        # Store a violation
        await self._store_violation(
            guild.id, user_id, "automod",
            f"Discord AutoMod: {reason_label}"
            + (f" — {matched_keyword[:80]}" if matched_keyword else ""),
            guild.me
        )

    # ── Auto Slowmode: Message Tracker ────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Track message timestamps per channel for auto slowmode."""
        if not message.guild:
            return
        if message.author.bot:
            return

        cfg = _get_auto_slowmode_config()
        if not cfg.get("enabled", False):
            return

        guild_key = str(message.guild.id)
        channel_key = str(message.channel.id)

        if guild_key not in self._auto_slowmode_data:
            self._auto_slowmode_data[guild_key] = {}
        if channel_key not in self._auto_slowmode_data[guild_key]:
            self._auto_slowmode_data[guild_key][channel_key] = []

        self._auto_slowmode_data[guild_key][channel_key].append(time.time())

        # Keep only timestamps from the last check window
        window = cfg.get("check_interval", 30) * 2
        cutoff = time.time() - window
        self._auto_slowmode_data[guild_key][channel_key] = [
            t for t in self._auto_slowmode_data[guild_key][channel_key]
            if t >= cutoff
        ]

    # ── Auto Slowmode: Background Adjuster ────────────

    @tasks.loop(seconds=30)
    async def _auto_slowmode_loop(self):
        """Periodically check message rates and adjust slowmode."""
        cfg = _get_auto_slowmode_config()
        if not cfg.get("enabled", False):
            return

        now = time.time()
        interval = cfg.get("check_interval", 30)
        thresholds = cfg.get("thresholds", {})
        cooldown = cfg.get("cooldown", 300)
        min_sm = cfg.get("min_slowmode", 0)
        max_sm = cfg.get("max_slowmode", 21600)

        if not thresholds:
            return

        # Sort thresholds descending: highest msg/min first
        sorted_thresholds = sorted(thresholds.items(), key=lambda x: x[0], reverse=True)

        for guild_key, channels in list(self._auto_slowmode_data.items()):
            guild = self.bot.get_guild(int(guild_key))
            if not guild:
                continue

            for channel_key, timestamps in list(channels.items()):
                channel = guild.get_channel(int(channel_key))
                if not channel or not hasattr(channel, "edit"):
                    continue

                # Count messages in the last check_interval seconds
                cutoff = now - interval
                recent = [t for t in timestamps if t >= cutoff]
                msg_count = len(recent)
                msg_per_min = msg_count * (60 / interval) if interval > 0 else 0

                # Determine target slowmode based on thresholds
                target_slowmode = min_sm  # Default: no slowmode
                for threshold_msgs, threshold_slowmode in sorted_thresholds:
                    if msg_per_min >= threshold_msgs:
                        target_slowmode = threshold_slowmode
                        break

                # Clamp
                target_slowmode = max(min_sm, min(target_slowmode, max_sm))

                # Get the current channel slowmode
                current_slowmode = channel.slowmode_delay

                # Track what we last set (dict: guild -> channel -> (slowmode, timestamp))
                if guild_key not in self._auto_slowmode_current:
                    self._auto_slowmode_current[guild_key] = {}
                last_entry = self._auto_slowmode_current[guild_key].get(channel_key)
                last_set = last_entry[0] if last_entry else None
                last_set_time = last_entry[1] if last_entry else 0

                # If slowmode was changed manually (not by us), skip — don't override
                if last_set is not None and current_slowmode != last_set:
                    continue

                # Only adjust if target differs from current
                if target_slowmode == current_slowmode:
                    continue

                # If we're reducing slowmode, check cooldown
                if target_slowmode < current_slowmode and last_set_time > 0:
                    elapsed = now - last_set_time
                    if elapsed < cooldown:
                        continue  # Still in cooldown, don't relax yet

                try:
                    await channel.edit(slowmode_delay=target_slowmode)
                    self._auto_slowmode_current[guild_key][channel_key] = (target_slowmode, now)
                    _log.info(
                        f"AutoSlowmode: Set #{channel.name} in {guild.name} "
                        f"to {target_slowmode}s (rate: {msg_per_min:.0f} msg/min)"
                    )
                except Exception as e:
                    _log.warning(f"AutoSlowmode: Failed to set slowmode on #{channel}: {e}")

            # Clean up channels with no recent messages (prevent memory leak)
            empty_channels = [
                ck for ck, ts in channels.items()
                if not [t for t in ts if t >= now - interval]
            ]
            for ck in empty_channels:
                del channels[ck]
                self._auto_slowmode_current.get(guild_key, {}).pop(ck, None)
            if not channels:
                del self._auto_slowmode_data[guild_key]
                self._auto_slowmode_current.pop(guild_key, None)

    @_auto_slowmode_loop.before_loop
    async def _before_auto_slowmode(self):
        """Wait for the bot to be ready before starting the loop, then sync interval from config."""
        await self.bot.wait_until_ready()
        cfg = _get_auto_slowmode_config()
        self._auto_slowmode_loop.change_interval(seconds=cfg.get("check_interval", 30))

    # ── Auto Slowmode: Commands ──────────────────────

    @commands.command(name="autoslowmode")
    @commands.has_permissions(manage_channels=True)
    async def cmd_autoslowmode(self, ctx, action: str = "", *, value: str = ""):
        """Configure auto slowmode. Usage:
          !autoslowmode                    — Show current config
          !autoslowmode on/off             — Enable/disable
          !autoslowmode interval <sec>     — Set check interval (10-300s)
          !autoslowmode cooldown <sec>     — Set cooldown before relaxing (0-3600s)
          !autoslowmode threshold <msgs> <slowmode_sec>
                                           — Add/update a threshold
          !autoslowmode delthreshold <msgs>— Remove a threshold
          !autoslowmode min <sec>          — Minimum slowmode
          !autoslowmode max <sec>          — Maximum slowmode
        """
        if not action:
            cfg = _get_auto_slowmode_config()
            embed = discord.Embed(
                title="🐢 Auto Slowmode Configuration",
                color=0xAA88FF,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Status", value="🟢 Enabled" if cfg["enabled"] else "🔴 Disabled", inline=True)
            embed.add_field(name="Check Interval", value=f"{cfg['check_interval']}s", inline=True)
            embed.add_field(name="Cooldown", value=f"{cfg['cooldown']}s", inline=True)
            embed.add_field(name="Min Slowmode", value=f"{cfg['min_slowmode']}s", inline=True)
            embed.add_field(name="Max Slowmode", value=f"{cfg['max_slowmode']}s", inline=True)
            if cfg["thresholds"]:
                lines = []
                for msgs, sm in sorted(cfg["thresholds"].items(), reverse=True):
                    lines.append(f"  ≥{msgs} msg/min → **{sm}s** slowmode")
                embed.add_field(name="Thresholds", value="\n".join(lines), inline=False)
            else:
                embed.add_field(name="Thresholds", value="*No thresholds configured.*", inline=False)
            embed.set_footer(text=f"Use !autoslowmode <action> to configure • {ctx.guild.name}")
            await ctx.send(embed=embed)
            return

        action = action.strip().lower()

        if action in ("on", "enable", "true", "1"):
            _update_auto_slowmode({"enabled": True})
            await ctx.send("🐢 Auto slowmode has been **enabled**.")
            await self._store_mod_action(ctx.guild.id, "autoslowmode", "enabled", ctx.author)
        elif action in ("off", "disable", "false", "0"):
            _update_auto_slowmode({"enabled": False})
            # Clear tracking data so stale data doesn't sit forever
            self._auto_slowmode_data.clear()
            self._auto_slowmode_current.clear()
            await ctx.send("🐢 Auto slowmode has been **disabled**.")
            await self._store_mod_action(ctx.guild.id, "autoslowmode", "disabled", ctx.author)
        elif action == "interval":
            try:
                secs = int(value)
                if secs < 10 or secs > 300:
                    await ctx.send("❌ Interval must be between 10 and 300 seconds.")
                    return
                _update_auto_slowmode({"check_interval": secs})
                # Update loop interval
                self._auto_slowmode_loop.change_interval(seconds=secs)
                await ctx.send(f"🐢 Check interval set to **{secs}s**.")
                await self._store_mod_action(ctx.guild.id, "autoslowmode", f"interval={secs}s", ctx.author)
            except ValueError:
                await ctx.send("❌ Invalid number. Use e.g. `!autoslowmode interval 30`")
        elif action == "cooldown":
            try:
                secs = int(value)
                if secs < 0 or secs > 3600:
                    await ctx.send("❌ Cooldown must be between 0 and 3600 seconds.")
                    return
                _update_auto_slowmode({"cooldown": secs})
                await ctx.send(f"🐢 Cooldown set to **{secs}s**.")
                await self._store_mod_action(ctx.guild.id, "autoslowmode", f"cooldown={secs}s", ctx.author)
            except ValueError:
                await ctx.send("❌ Invalid number.")
        elif action == "min":
            try:
                secs = int(value)
                if secs < 0 or secs > 21600:
                    await ctx.send("❌ Min slowmode must be between 0 and 21600.")
                    return
                _update_auto_slowmode({"min_slowmode": secs})
                await ctx.send(f"🐢 Min slowmode set to **{secs}s**.")
                await self._store_mod_action(ctx.guild.id, "autoslowmode", f"min={secs}s", ctx.author)
            except ValueError:
                await ctx.send("❌ Invalid number.")
        elif action == "max":
            try:
                secs = int(value)
                if secs < 0 or secs > 21600:
                    await ctx.send("❌ Max slowmode must be between 0 and 21600.")
                    return
                _update_auto_slowmode({"max_slowmode": secs})
                await ctx.send(f"🐢 Max slowmode set to **{secs}s**.")
                await self._store_mod_action(ctx.guild.id, "autoslowmode", f"max={secs}s", ctx.author)
            except ValueError:
                await ctx.send("❌ Invalid number.")
        elif action == "threshold":
            parts = value.split()
            if len(parts) < 2:
                await ctx.send("❌ Usage: `!autoslowmode threshold <msg_per_min> <slowmode_seconds>`")
                return
            try:
                msgs = int(parts[0])
                sm = int(parts[1])
                if msgs < 1:
                    await ctx.send("❌ Message threshold must be at least 1.")
                    return
                if sm < 0 or sm > 21600:
                    await ctx.send("❌ Slowmode must be between 0 and 21600s.")
                    return
                cfg = _get_auto_slowmode_config()
                thresholds = cfg["thresholds"]
                thresholds[msgs] = sm
                _update_auto_slowmode({"thresholds": {str(k): v for k, v in thresholds.items()}})
                await ctx.send(f"🐢 Threshold added: **≥{msgs} msg/min → {sm}s slowmode**.")
                await self._store_mod_action(ctx.guild.id, "autoslowmode", f"threshold {msgs}msgs→{sm}s", ctx.author)
            except ValueError:
                await ctx.send("❌ Invalid numbers.")
        elif action in ("delthreshold", "removethreshold", "rmthreshold"):
            try:
                msgs = int(value)
                cfg = _get_auto_slowmode_config()
                thresholds = cfg["thresholds"]
                if msgs in thresholds:
                    del thresholds[msgs]
                    _update_auto_slowmode({"thresholds": {str(k): v for k, v in thresholds.items()}})
                    await ctx.send(f"🐢 Threshold at **{msgs} msg/min** removed.")
                    await self._store_mod_action(ctx.guild.id, "autoslowmode", f"removed threshold {msgs}msgs", ctx.author)
                else:
                    await ctx.send(f"❌ No threshold found at **{msgs} msg/min**.")
            except ValueError:
                await ctx.send("❌ Invalid number.")
        else:
            await ctx.send(f"❌ Unknown action: `{action}`. Use `!autoslowmode` to see options.")

    @app_commands.command(name="autoslowmode", description="Configure auto slowmode — adjusts slowmode based on message rate")
    @app_commands.describe(
        action="What to do — leave as 'View config' to just show current settings",
        seconds="Seconds — used by interval / cooldown / min / max",
        msgs="Messages per minute — used by threshold / delthreshold",
        slowmode="Slowmode seconds for the threshold",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="View current config", value="view"),
        app_commands.Choice(name="Enable auto slowmode", value="on"),
        app_commands.Choice(name="Disable auto slowmode", value="off"),
        app_commands.Choice(name="Set check interval (10-300s)", value="interval"),
        app_commands.Choice(name="Set cooldown before relaxing (0-3600s)", value="cooldown"),
        app_commands.Choice(name="Set minimum slowmode (0-21600s)", value="min"),
        app_commands.Choice(name="Set maximum slowmode (0-21600s)", value="max"),
        app_commands.Choice(name="Add or update a threshold", value="threshold"),
        app_commands.Choice(name="Remove a threshold", value="delthreshold"),
    ])
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slash_autoslowmode(self, interaction: discord.Interaction, action: str = "view", seconds: int = 0, msgs: int = 0, slowmode: int = 0):
        """Configure auto slowmode — pick an action from the dropdown, no typing needed."""
        if action == "view":
            cfg = _get_auto_slowmode_config()
            embed = discord.Embed(
                title="🐢 Auto Slowmode Configuration",
                color=0xAA88FF,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Status", value="🟢 Enabled" if cfg["enabled"] else "🔴 Disabled", inline=True)
            embed.add_field(name="Check Interval", value=f"{cfg['check_interval']}s", inline=True)
            embed.add_field(name="Cooldown", value=f"{cfg['cooldown']}s", inline=True)
            embed.add_field(name="Min Slowmode", value=f"{cfg['min_slowmode']}s", inline=True)
            embed.add_field(name="Max Slowmode", value=f"{cfg['max_slowmode']}s", inline=True)
            if cfg["thresholds"]:
                lines = []
                for msgs_k, sm in sorted(cfg["thresholds"].items(), reverse=True):
                    lines.append(f"  ≥{msgs_k} msg/min → **{sm}s** slowmode")
                embed.add_field(name="Thresholds", value="\n".join(lines), inline=False)
            else:
                embed.add_field(name="Thresholds", value="*No thresholds configured.*", inline=False)
            embed.set_footer(text=f"Use /autoslowmode <action> to configure • {interaction.guild.name}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if action in ("on", "enable", "true", "1"):
            _update_auto_slowmode({"enabled": True})
            await interaction.response.send_message("🐢 Auto slowmode has been **enabled**.")
            await self._store_mod_action(interaction.guild_id, "autoslowmode", "enabled", interaction.user)
        elif action in ("off", "disable", "false", "0"):
            _update_auto_slowmode({"enabled": False})
            self._auto_slowmode_data.clear()
            self._auto_slowmode_current.clear()
            await interaction.response.send_message("🐢 Auto slowmode has been **disabled**.")
            await self._store_mod_action(interaction.guild_id, "autoslowmode", "disabled", interaction.user)
        elif action == "interval":
            secs = seconds
            if secs < 10 or secs > 300:
                await interaction.response.send_message("❌ Interval must be between 10 and 300 seconds.", ephemeral=True)
                return
            _update_auto_slowmode({"check_interval": secs})
            self._auto_slowmode_loop.change_interval(seconds=secs)
            await interaction.response.send_message(f"🐢 Check interval set to **{secs}s**.")
            await self._store_mod_action(interaction.guild_id, "autoslowmode", f"interval={secs}s", interaction.user)
        elif action == "cooldown":
            secs = seconds
            if secs < 0 or secs > 3600:
                await interaction.response.send_message("❌ Cooldown must be between 0 and 3600 seconds.", ephemeral=True)
                return
            _update_auto_slowmode({"cooldown": secs})
            await interaction.response.send_message(f"🐢 Cooldown set to **{secs}s**.")
            await self._store_mod_action(interaction.guild_id, "autoslowmode", f"cooldown={secs}s", interaction.user)
        elif action == "min":
            secs = seconds
            if secs < 0 or secs > 21600:
                await interaction.response.send_message("❌ Min slowmode must be between 0 and 21600.", ephemeral=True)
                return
            _update_auto_slowmode({"min_slowmode": secs})
            await interaction.response.send_message(f"🐢 Min slowmode set to **{secs}s**.")
            await self._store_mod_action(interaction.guild_id, "autoslowmode", f"min={secs}s", interaction.user)
        elif action == "max":
            secs = seconds
            if secs < 0 or secs > 21600:
                await interaction.response.send_message("❌ Max slowmode must be between 0 and 21600.", ephemeral=True)
                return
            _update_auto_slowmode({"max_slowmode": secs})
            await interaction.response.send_message(f"🐢 Max slowmode set to **{secs}s**.")
            await self._store_mod_action(interaction.guild_id, "autoslowmode", f"max={secs}s", interaction.user)
        elif action == "threshold":
            if msgs < 1:
                await interaction.response.send_message("❌ Message threshold must be at least 1.", ephemeral=True)
                return
            if slowmode < 0 or slowmode > 21600:
                await interaction.response.send_message("❌ Slowmode must be between 0 and 21600s.", ephemeral=True)
                return
            cfg = _get_auto_slowmode_config()
            thresholds = cfg["thresholds"]
            thresholds[msgs] = slowmode
            _update_auto_slowmode({"thresholds": {str(k): v for k, v in thresholds.items()}})
            await interaction.response.send_message(f"🐢 Threshold added: **≥{msgs} msg/min → {slowmode}s slowmode**.")
            await self._store_mod_action(interaction.guild_id, "autoslowmode", f"threshold {msgs}msgs→{slowmode}s", interaction.user)
        elif action in ("delthreshold", "removethreshold", "rmthreshold"):
            if msgs < 1:
                await interaction.response.send_message("❌ Please provide the messages-per-minute value (msgs) for the threshold to remove.", ephemeral=True)
                return
            cfg = _get_auto_slowmode_config()
            thresholds = cfg["thresholds"]
            if msgs in thresholds:
                del thresholds[msgs]
                _update_auto_slowmode({"thresholds": {str(k): v for k, v in thresholds.items()}})
                await interaction.response.send_message(f"🐢 Threshold at **{msgs} msg/min** removed.")
                await self._store_mod_action(interaction.guild_id, "autoslowmode", f"removed threshold {msgs}msgs", interaction.user)
            else:
                await interaction.response.send_message(f"❌ No threshold found at **{msgs} msg/min**.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Unknown action: `{action}`. Use `/autoslowmode` to see options.", ephemeral=True)

    # ── Interaction error handling ─────────────────────

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle slash command errors gracefully."""
        from discord.errors import InteractionResponded

        if isinstance(error, app_commands.CommandOnCooldown):
            try:
                await interaction.response.send_message(
                    f"⏳ Command on cooldown. Try again in {error.retry_after:.0f}s", ephemeral=True
                )
            except InteractionResponded:
                await interaction.followup.send(
                    f"⏳ Command on cooldown. Try again in {error.retry_after:.0f}s", ephemeral=True
                )
        elif isinstance(error, StaffRoleRequired):
            try:
                await interaction.response.send_message(str(error), ephemeral=True)
            except InteractionResponded:
                await interaction.followup.send(str(error), ephemeral=True)
        elif isinstance(error, app_commands.CommandNotFound):
            pass
        elif isinstance(error, app_commands.MissingPermissions):
            try:
                await interaction.response.send_message(
                    "❌ You don't have permission to use this command.", ephemeral=True
                )
            except InteractionResponded:
                await interaction.followup.send(
                    "❌ You don't have permission to use this command.", ephemeral=True
                )
        elif isinstance(error, app_commands.BotMissingPermissions):
            try:
                await interaction.response.send_message(
                    "❌ I don't have the required permissions to execute this command.", ephemeral=True
                )
            except InteractionResponded:
                await interaction.followup.send(
                    "❌ I don't have the required permissions to execute this command.", ephemeral=True
                )
        else:
            _log.warning(f"Mod slash command error: {error}")
            try:
                await interaction.response.send_message(
                    f"❌ An error occurred: {error}", ephemeral=True
                )
            except InteractionResponded:
                await interaction.followup.send(
                    f"❌ An error occurred: {error}", ephemeral=True
                )


# ── Setup function ─────────────────────────────────────

async def setup(bot):
    """Load the Moderation cog."""
    await bot.add_cog(Moderation(bot))
