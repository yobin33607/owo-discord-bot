"""
Limey Staff Gate
================
Shared permission gate for the manager bot's admin-level commands.

Admin commands (previously gated with ``has_permissions(administrator=True)``)
now require the configured **staff role**. The server owner is always allowed
so the system can never lock itself out.

The staff role is configured in settings.json under
``manager_bot.moderation.staff_role_id`` (editable from the dashboard's
Configuration tab, just like quarantine_role_id).

Usage:
    from modules.staff_gate import staff_required, slash_staff_required

    @commands.command(...)
    @commands.check(staff_required)          # prefix commands
    async def cmd(self, ctx): ...

    @app_commands.command(...)
    @app_commands.check(slash_staff_required)  # slash commands
    async def slash(self, interaction): ...
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.github_data_store import ghd


class StaffRoleRequired(app_commands.CheckFailure, commands.CheckFailure):
    """Raised when someone without the staff role tries an admin command.

    Inherits from both app_commands and ext.commands CheckFailure so the same
    exception passes through discord.py's slash AND prefix error pipelines
    without being wrapped in CommandInvokeError.
    """


def get_staff_role_id():
    """Get the staff role ID from settings.json -> manager_bot.moderation."""
    try:
        cfg = ghd.read_json("config/settings.json", default={})
        if not isinstance(cfg, dict):
            return None
        return ((cfg.get("manager_bot") or {}).get("moderation") or {}).get("staff_role_id") or None
    except Exception:
        return None


def is_staff(member) -> bool:
    """Return True if the member has the staff role or is the guild owner."""
    if not isinstance(member, discord.Member):
        return False
    guild = member.guild
    if guild is None:
        return False
    # The owner is always allowed so admin commands can't be locked out.
    if getattr(guild, "owner_id", None) == member.id:
        return True
    role_id = get_staff_role_id()
    if not role_id:
        return False
    try:
        role = guild.get_role(int(role_id))
    except (TypeError, ValueError):
        return False
    return bool(role and role in member.roles)


def _deny_reason() -> str:
    """Build the denial message, hinting when no staff role is configured."""
    if not get_staff_role_id():
        return (
            "❌ No staff role configured. Add `staff_role_id` to "
            "`manager_bot.moderation` in settings."
        )
    return "❌ You must have the **Staff role** to use this command."


async def staff_required(ctx) -> bool:
    """commands.check predicate for prefix commands (staff role or owner)."""
    if is_staff(ctx.author):
        return True
    raise StaffRoleRequired(_deny_reason())


async def slash_staff_required(interaction: discord.Interaction) -> bool:
    """app_commands.check predicate for slash commands (staff role or owner)."""
    if is_staff(interaction.user):
        return True
    raise StaffRoleRequired(_deny_reason())
