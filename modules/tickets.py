"""
Limey Ticket Bot
================
A complete ticket system for the manager bot.
Provides ticket panels, channel creation, transcript archiving, and staff controls.

Loaded as a cog by the ManagerBot class.

Features:
  - Interactive ticket panel with button
  - Multi-type tickets: Support, Report, Appeal, Question, Other
  - Per-type categories for organization
  - Configurable staff role for access
  - Transcript generation to log channel on close
  - Ticket claiming by staff
  - Close with confirmation + transcript archive

Configuration (stored in config/tickets.json in the GitHub data repo):
  staff_role_id          — Role allowed to view/manage all tickets
  log_channel_id         — Channel where transcripts/logs are sent on close
  categories             — Per-ticket-type category channels
"""

import discord
from discord import app_commands
from discord.ext import commands
import json
import time
import logging
import io
import textwrap
import asyncio
import requests

_log = logging.getLogger("ticket_bot")

# ── GitHub Data Store ────────────────────────────────

from utils.github_data_store import ghd

# ── Staff gate for admin commands ─────────────────────

from modules.staff_gate import staff_required, slash_staff_required


# ── Ticket Data Helpers ───────────────────────────────

TICKET_TYPES = {
    "support": {"emoji": "❓", "label": "Support", "color": 0x44AAFF, "description": "Get help with using the bot or server"},
    "report": {"emoji": "🚩", "label": "Report", "color": 0xFF4444, "description": "Report a user or issue"},
    "appeal": {"emoji": "⚖️", "label": "Appeal", "color": 0xFF8800, "description": "Appeal a mute, ban, or warning"},
    "question": {"emoji": "💡", "label": "Question", "color": 0x44FF88, "description": "Ask a general question"},
    "gambling": {"emoji": "🎰", "label": "Turn on Gambling", "color": 0xFFD700, "description": "Request access to gambling features"},
    "selfbot": {"emoji": "🤖", "label": "Manage Selfbot", "color": 0x8888FF, "description": "Manage your selfbot account"},
    "other": {"emoji": "📝", "label": "Other", "color": 0xAA88FF, "description": "Something else"},
}

GAMBLING_ROLE_ID = 1531214791354482729


def _check_selfbot_account(user_id: str):
    """Check if a user has a selfbot account via the dashboard API.
    Returns (has_account: bool, account_data: dict or None).
    """
    try:
        accounts = requests.get(
            "http://localhost:8000/api/accounts/list",
            headers={"Content-Type": "application/json"},
            timeout=5,
        ).json()
        if isinstance(accounts, list):
            for acc in accounts:
                if str(acc.get("id", "")) == str(user_id):
                    return True, acc
        return False, None
    except Exception:
        return False, None


def _load_ticket_data():
    """Load ticket data from GitHub data repo."""
    data = ghd.read_json("config/tickets.json", default=None)
    if data is not None:
        # Migrate old format if needed
        if "config" not in data:
            data["config"] = {}
        if "tickets" not in data:
            data["tickets"] = {}
        if "next_ticket_num" not in data:
            data["next_ticket_num"] = 1
        return data
    return {
        "config": {},
        "tickets": {},
        "next_ticket_num": 1,
    }


def _save_ticket_data(data):
    """Save ticket data to GitHub data repo. Returns True on success."""
    ok = bool(ghd.write_json("config/tickets.json", data, message="Update ticket data"))
    if not ok:
        _log.warning("Ticket data: failed to write config/tickets.json to GitHub")
    return ok


def _get_ticket_config():
    """Get the ticket system config from config/tickets.json.

    The ticket config used to be stored under manager_bot.tickets in
    settings.json, but dashboard settings saves rewrite that file from the
    config form and dropped keys it didn't know about. config/tickets.json is
    the single dedicated source of truth. Falls back to the legacy settings.json
    location for configs saved by older versions.
    """
    data = _load_ticket_data()
    if data.get("config"):
        return data["config"]
    legacy = ghd.read_json("config/settings.json", default={})
    if isinstance(legacy, dict):
        return (legacy.get("manager_bot") or {}).get("tickets", {})
    return {}


def _save_ticket_config(new_ticket_cfg):
    """Save the ticket system config into config/tickets.json.

    Returns True on success. Writes into the existing ticket data file so
    ticket records are preserved, and never touches settings.json (which the
    dashboard rewrites from its config form and would drop this config).
    """
    data = _load_ticket_data()
    data["config"] = new_ticket_cfg
    return _save_ticket_data(data)


def _build_ticket_channel_name(ticket_num, ticket_type, username):
    """Build a clean channel name for a ticket."""
    # Clean the username for Discord channel naming
    clean_name = "".join(c for c in username.lower() if c.isalnum() or c in "-_")
    clean_name = clean_name[:20]
    type_short = ticket_type[:4]
    return f"ticket-{type_short}-{ticket_num}-{clean_name}"


async def _send_ticket_log(guild, action, ticket_num, ticket_type, user, staff=None, reason=None):
    """Send a ticket event to the configured log channel."""
    data = _load_ticket_data()
    guild_key = str(guild.id)
    log_channel_id = data.get("config", {}).get("log_channel_id")
    if not log_channel_id:
        return

    try:
        channel = guild.get_channel(int(log_channel_id)) or await guild.fetch_channel(int(log_channel_id))
        if not channel:
            return
    except Exception:
        return

    colors = {
        "created": 0x44AAFF,
        "closed": 0xFF4444,
        "claimed": 0x44FF88,
    }
    emojis = {
        "created": "🎫",
        "closed": "🔒",
        "claimed": "👋",
    }

    ttype_info = TICKET_TYPES.get(ticket_type, {})
    embed = discord.Embed(
        title=f"{emojis.get(action, '🎫')} Ticket #{ticket_num} — {action.upper()}",
        color=colors.get(action, 0x44AAFF),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Type", value=f"{ttype_info.get('emoji', '📋')} {ttype_info.get('label', ticket_type)}", inline=True)
    embed.add_field(name="User", value=f"{user} ({user.id})", inline=True)
    if staff:
        embed.add_field(name="Staff", value=f"{staff} ({staff.id})", inline=True)
    if reason:
        embed.add_field(name="Reason/Note", value=reason, inline=False)
    embed.set_footer(text=f"Ticket #{ticket_num} • {guild.name}")

    try:
        await channel.send(embed=embed)
    except Exception:
        pass


# ── Interactive Setup View ────────────────────────────

class TicketSetupView(discord.ui.View):
    """Interactive setup for the ticket system."""

    def __init__(self, cog, guild):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.selected_staff_role = None
        self.selected_log_channel = None
        self.selected_categories = {}

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="🎯 Select the staff role for ticket access...",
        min_values=0,
        max_values=1,
        row=0,
    )
    async def staff_role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        if select.values:
            self.selected_staff_role = select.values[0]
            await interaction.response.send_message(
                f"✅ Staff role set to: {self.selected_staff_role.mention}",
                ephemeral=True,
            )
        else:
            self.selected_staff_role = None
            await interaction.response.send_message("❌ No role selected.", ephemeral=True)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="📁 Select the log channel for ticket transcripts...",
        channel_types=[discord.ChannelType.text],
        min_values=0,
        max_values=1,
        row=1,
    )
    async def log_channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        if select.values:
            self.selected_log_channel = select.values[0]
            await interaction.response.send_message(
                f"✅ Log channel set to: {self.selected_log_channel.mention}",
                ephemeral=True,
            )
        else:
            self.selected_log_channel = None
            await interaction.response.send_message("❌ No channel selected.", ephemeral=True)

    @discord.ui.button(label="✅ Save Configuration", style=discord.ButtonStyle.success, row=2)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_staff_role:
            await interaction.response.send_message(
                "❌ Please select a staff role first using the dropdown above.",
                ephemeral=True,
            )
            return

        data = _load_ticket_data()
        if "config" not in data:
            data["config"] = {}

        guild_key = str(self.guild.id)
        data["config"]["staff_role_id"] = str(self.selected_staff_role.id)
        data["config"]["log_channel_id"] = str(self.selected_log_channel.id) if self.selected_log_channel else ""

        # Ensure categories exist
        if "categories" not in data["config"]:
            data["config"]["categories"] = {}

        _save_ticket_data(data)

        embed = discord.Embed(
            title="✅ Ticket System Configured",
            description="The ticket system is now set up! Use `/ticket-panel` or `!ticketpanel` to post the ticket creation panel.",
            color=0x00FF88,
        )
        embed.add_field(name="Staff Role", value=self.selected_staff_role.mention, inline=True)
        embed.add_field(name="Log Channel", value=self.selected_log_channel.mention if self.selected_log_channel else "Not set", inline=True)
        embed.set_footer(text="You can re-run setup anytime to change these settings")

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


# ── Ticket Panel View ─────────────────────────────────

class TicketPanelView(discord.ui.View):
    """The main ticket creation panel with a Create Ticket button."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Create Ticket",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="ticket_create_btn",
    )
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle the Create Ticket button click."""
        # Check if user already has an open ticket
        data = _load_ticket_data()
        guild_key = str(interaction.guild_id)
        user_key = str(interaction.user.id)

        guild_tickets = data.get("tickets", {}).get(guild_key, {})
        for tnum, tdata in guild_tickets.items():
            if tdata.get("user_id") == user_key and tdata.get("status") == "open":
                # User already has an open ticket
                channel_id = tdata.get("channel_id")
                channel = interaction.guild.get_channel(int(channel_id)) if channel_id else None
                if channel:
                    embed = discord.Embed(
                        title="❌ You Already Have an Open Ticket",
                        description=f"You already have an open ticket: {channel.mention}\n\nPlease close that ticket first before creating a new one.",
                        color=0xFF4444,
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return
                else:
                    # Channel no longer exists, clean up
                    tdata["status"] = "orphaned"

        # Show the type selection view
        view = TicketTypeSelectView(self.cog, interaction.user)
        embed = discord.Embed(
            title="🎫 Create a Ticket",
            description="What type of ticket would you like to create?",
            color=0x44AAFF,
        )
        for tkey, tinfo in TICKET_TYPES.items():
            embed.add_field(
                name=f"{tinfo['emoji']} {tinfo['label']}",
                value=tinfo['description'],
                inline=True,
            )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class TicketTypeSelectView(discord.ui.View):
    """Dropdown to select ticket type, then opens a modal."""

    def __init__(self, cog, user):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user

    @discord.ui.select(
        placeholder="📋 Select ticket type...",
        options=[
            discord.SelectOption(
                label=tinfo["label"],
                description=tinfo["description"],
                value=tkey,
                emoji=tinfo["emoji"],
            )
            for tkey, tinfo in TICKET_TYPES.items()
        ],
        min_values=1,
        max_values=1,
    )
    async def type_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ This is not your selection.", ephemeral=True)
            return

        ticket_type = select.values[0]

        # If appeal type, first show violation selection view
        if ticket_type == "appeal":
            # Defer so we can load violations
            await interaction.response.defer(ephemeral=True)

            # Load violations from the moderation system (lazy import to avoid circular issues)
            try:
                from modules.manager_bot import _get_user_violations
                violations = _get_user_violations(interaction.guild_id, interaction.user.id)
            except Exception:
                violations = []

            if violations:
                embed = discord.Embed(
                    title="⚖️ Appeal — Select Violation",
                    description=f"You have **{len(violations)}** violation(s) on record. Select the one you want to appeal:",
                    color=0xFF8800,
                    timestamp=discord.utils.utcnow(),
                )

                # Show most recent violations
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

                embed.set_footer(text="Select a violation below to continue your appeal")

                view = AppealTicketViolationSelectView(self.cog, interaction.user, interaction.guild, violations)
                msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                view.message = msg
            else:
                # No violations — let them appeal directly with an appeal modal
                embed = discord.Embed(
                    title="✅ No Violations Found",
                    description=(
                        "You have **no violations** on record in this server.\n\n"
                        "If you still want to submit an appeal ticket, continue below."
                    ),
                    color=0x00FF88,
                )

                view = discord.ui.View(timeout=120)
                async def open_appeal_modal(btn_interaction: discord.Interaction):
                    if btn_interaction.user.id != self.user.id:
                        await btn_interaction.response.send_message("❌ This is not your interaction.", ephemeral=True)
                        return
                    appeal_info = {
                        "punishment_type": "other",
                        "violation_reason": "Not specified",
                    }
                    modal = TicketCreateModal(self.cog, "appeal", appeal_info=appeal_info)
                    await btn_interaction.response.send_modal(modal)
                open_appeal_modal.__name__ = "open_appeal_modal_callback"

                button = discord.ui.Button(label="✏️  Submit Appeal Ticket", style=discord.ButtonStyle.primary)
                button.callback = open_appeal_modal
                view.add_item(button)

                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            modal = TicketCreateModal(self.cog, ticket_type)
            await interaction.response.send_modal(modal)


class AppealTicketViolationSelectView(discord.ui.View):
    """View showing the user's violations with a dropdown to select which one to appeal,
    then continues to the appeal ticket creation modal."""

    def __init__(self, cog, user, guild, violations):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user
        self.guild = guild
        self.violations = violations
        self.selected_violation = None

        # Build dropdown options from violations
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

        # Add "Other" option
        if not any(o.value == "other" for o in options):
            options.append(
                discord.SelectOption(label="Other", description="Something not listed above", value="other", emoji="📝")
            )

        self.violation_select = discord.ui.Select(
            placeholder="📋 Select the violation you're appealing...",
            options=options[:25],
            min_values=1,
            max_values=1,
        )
        self.violation_select.callback = self._on_select
        self.add_item(self.violation_select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ This is not your selection.", ephemeral=True)
            return

        selected_val = self.violation_select.values[0]
        # Find the matching violation from stored violations
        for v in self.violations:
            if v.get("type", "").lower() == selected_val:
                self.selected_violation = v
                break
        if self.selected_violation is None:
            self.selected_violation = {"type": selected_val, "reason": "Not specified"}

        # Enable the continue button
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = False
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="✏️  Continue to Appeal Ticket", style=discord.ButtonStyle.primary, disabled=True, row=1)
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ This is not your interaction.", ephemeral=True)
            return

        v = self.selected_violation or {}
        ptype = v.get("type", "other")
        reason = v.get("reason", "Not specified")

        appeal_info = {
            "punishment_type": ptype,
            "violation_reason": reason,
        }

        modal = TicketCreateModal(self.cog, "appeal", appeal_info=appeal_info)
        await interaction.response.send_modal(modal)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass


class TicketCreateModal(discord.ui.Modal):
    """Modal for user to describe their ticket issue.
    If `appeal_info` is provided, shows appeal-specific fields instead.
    """

    def __init__(self, cog, ticket_type, appeal_info=None):
        self.cog = cog
        self.ticket_type = ticket_type
        self.appeal_info = appeal_info
        tinfo = TICKET_TYPES.get(ticket_type, {})

        if appeal_info:
            # Appeal-specific modal
            punishment_type = appeal_info.get('punishment_type', '')
            title = "⚖️ Appeal Ticket"
            if punishment_type and punishment_type.lower() != "other":
                title = f"⚖️ Appeal — {punishment_type.upper()}"
            super().__init__(title=title)

            self.explanation = discord.ui.TextInput(
                label="Why Should This Be Lifted?",
                placeholder="Explain your side of the story and why the punishment should be removed...",
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=1500,
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
        else:
            # Regular ticket modal
            title_text = f"🎫 {tinfo.get('label', 'Ticket')} - Describe Your Issue"
            super().__init__(title=title_text)

            self.subject = discord.ui.TextInput(
                label="Subject",
                placeholder="Brief summary of your issue...",
                style=discord.TextStyle.short,
                required=True,
                max_length=100,
            )
            self.add_item(self.subject)

            self.description = discord.ui.TextInput(
                label="Description",
                placeholder="Please describe your issue in detail...",
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=1500,
            )
            self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction):
        """Create the ticket channel after modal submission."""
        await interaction.response.defer(ephemeral=True)

        try:
            guild = interaction.guild
            user = interaction.user
            ticket_type = self.ticket_type
            tinfo = TICKET_TYPES.get(ticket_type, {})

            # Load data
            data = _load_ticket_data()
            guild_key = str(guild.id)

            if "config" not in data:
                data["config"] = {}
            if "tickets" not in data:
                data["tickets"] = {}
            if guild_key not in data["tickets"]:
                data["tickets"][guild_key] = {}

            staff_role_id = data.get("config", {}).get("staff_role_id")
            if not staff_role_id:
                await interaction.followup.send(
                    "❌ Ticket system is not fully configured yet. Please ask an admin to run `/ticket-setup`.",
                    ephemeral=True,
                )
                return

            ticket_num = data.get("next_ticket_num", 1)
            data["next_ticket_num"] = ticket_num + 1

            # Find or create the category for this ticket type
            category_config = data.get("config", {}).get("categories", {}).get(ticket_type, {})
            category_id = category_config.get("id")

            category = None
            if category_id:
                category = guild.get_channel(int(category_id))

            if not category:
                # Create the category
                category_name = f"🎫 {tinfo.get('emoji', '📋')} {tinfo.get('label', ticket_type)} Tickets"
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, manage_messages=True),
                }
                if staff_role_id:
                    staff_role = guild.get_role(int(staff_role_id))
                    if staff_role:
                        overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

                try:
                    category = await guild.create_category(name=category_name, overwrites=overwrites)
                    # Update data
                    if "categories" not in data["config"]:
                        data["config"]["categories"] = {}
                    if ticket_type not in data["config"]["categories"]:
                        data["config"]["categories"][ticket_type] = {}
                    data["config"]["categories"][ticket_type]["id"] = str(category.id)
                    _save_ticket_data(data)
                except Exception as e:
                    await interaction.followup.send(
                        f"❌ Failed to create ticket category: {e}\n\nMake sure the bot has the `Manage Channels` permission.",
                        ephemeral=True,
                    )
                    return

            # Create the ticket channel
            channel_name = _build_ticket_channel_name(ticket_num, ticket_type, user.display_name)

            # Permission overwrites for the ticket channel
            staff_role = guild.get_role(int(staff_role_id)) if staff_role_id else None
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, manage_messages=True),
            }
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            try:
                channel = await guild.create_text_channel(
                    name=channel_name,
                    category=category,
                    overwrites=overwrites,
                    topic=f"Ticket #{ticket_num} — {tinfo.get('label', ticket_type)} — {user} ({user.id})",
                )
            except Exception as e:
                await interaction.followup.send(
                    f"❌ Failed to create ticket channel: {e}\n\nMake sure the bot has the `Manage Channels` permission.",
                    ephemeral=True,
                )
                return

            # Build ticket record with appeal info if applicable
            ticket_record = {
                "num": ticket_num,
                "channel_id": str(channel.id),
                "user_id": str(user.id),
                "username": str(user),
                "type": ticket_type,
                "status": "open",
                "claimed_by": None,
                "created_at": time.time(),
                "closed_at": None,
                "transcript": [],
            }

            if self.appeal_info:
                # Appeal ticket — store appeal details
                ticket_record["subject"] = f"Appeal: {self.appeal_info.get('punishment_type', 'other').upper()}"
                ticket_record["description"] = self.explanation.value
                ticket_record["appeal_violation"] = {
                    "punishment_type": self.appeal_info.get('punishment_type', 'other'),
                    "violation_reason": self.appeal_info.get('violation_reason', 'Not specified'),
                }
                ticket_record["appeal_evidence"] = self.evidence.value or "None provided"
            else:
                # Regular ticket
                ticket_record["subject"] = self.subject.value
                ticket_record["description"] = self.description.value

            data["tickets"][guild_key][str(ticket_num)] = ticket_record
            _save_ticket_data(data)

            # Send welcome message in the ticket channel
            welcome_embed = discord.Embed(
                title=f"🎫 Ticket #{ticket_num} — {tinfo.get('emoji', '')} {tinfo.get('label', ticket_type)}",
                description=f"Thank you for creating a ticket, {user.mention}! A staff member will be with you shortly.",
                color=tinfo.get("color", 0x44AAFF),
                timestamp=discord.utils.utcnow(),
            )

            if self.appeal_info:
                # Appeal-specific welcome embed
                ptype = self.appeal_info.get('punishment_type', 'other').upper()
                preason = self.appeal_info.get('violation_reason', 'Not specified')
                welcome_embed.add_field(
                    name="⚖️ Appeal Details",
                    value=f"**Appealing:** {ptype}\n**Reason:** {preason}",
                    inline=False,
                )
                welcome_embed.add_field(
                    name="📝 Explanation",
                    value=self.explanation.value,
                    inline=False,
                )
                if self.evidence.value:
                    welcome_embed.add_field(
                        name="🔗 Evidence",
                        value=self.evidence.value,
                        inline=False,
                    )
            else:
                # Regular welcome embed
                welcome_embed.add_field(name="Subject", value=self.subject.value, inline=False)
                welcome_embed.add_field(name="Description", value=self.description.value, inline=False)

            if staff_role:
                welcome_embed.add_field(name="Staff", value=staff_role.mention, inline=True)
            welcome_embed.set_footer(text=f"Ticket #{ticket_num}")

            # Add ticket control buttons
            ticket_view = TicketControlView(self.cog, ticket_num)

            await channel.send(content=staff_role.mention if staff_role else "", embed=welcome_embed, view=ticket_view)

            # ── Selfbot ticket: post account panel after welcome ─────
            if ticket_type == "selfbot":
                has_account, account_data = _check_selfbot_account(str(user.id))
                if has_account and account_data:
                    cash = account_data.get("cash", 0) or 0
                    paused = account_data.get("paused", True)
                    username = account_data.get("username", "Unknown")
                    status_str = "🔴 PAUSED" if paused else "🟢 RUNNING"
                    panel = discord.Embed(
                        title="🤖 Your Selfbot Account",
                        description=f"Here's what we found for your account:",
                        color=0x44FF88,
                    )
                    panel.add_field(name="Username", value=username, inline=True)
                    panel.add_field(name="Status", value=status_str, inline=True)
                    panel.add_field(name="Cash", value=f"{cash:,}", inline=True)
                    panel.add_field(name="Account ID", value=f"`{user.id}`", inline=False)
                    panel.set_footer(text="A staff member will assist you with account management")
                    await channel.send(embed=panel)
                else:
                    no_account = discord.Embed(
                        title="⚠️ No Selfbot Account Found",
                        description=(
                            "We couldn't find a selfbot account linked to your Discord ID.\n\n"
                            "**This won't work** unless you have a selfbot connected. "
                            "Please make sure your selfbot is running and linked to this account.\n\n"
                            "A staff member will assist you with setup if needed."
                        ),
                        color=0xFF4444,
                    )
                    no_account.set_footer(text="No selfbot account found for this user")
                    await channel.send(embed=no_account)

            # Confirm to the user
            confirm_embed = discord.Embed(
                title="✅ Ticket Created",
                description=f"Your ticket has been created! Check {channel.mention} to continue.",
                color=0x00FF88,
            )
            confirm_embed.add_field(name="Ticket #", value=f"#{ticket_num}", inline=True)
            confirm_embed.add_field(name="Type", value=f"{tinfo.get('emoji', '📋')} {tinfo.get('label', ticket_type)}", inline=True)

            if self.appeal_info:
                confirm_embed.add_field(
                    name="Appealing",
                    value=f"{self.appeal_info.get('punishment_type', 'other').upper()}",
                    inline=False,
                )
            else:
                confirm_embed.add_field(name="Subject", value=self.subject.value, inline=False)

            await interaction.followup.send(embed=confirm_embed, ephemeral=True)

            # Log to log channel
            await _send_ticket_log(guild, "created", ticket_num, ticket_type, user)

        except Exception as e:
            _log.warning(f"Ticket creation error: {e}")
            try:
                await interaction.followup.send(f"❌ Failed to create ticket: {e}", ephemeral=True)
            except Exception:
                pass


# ── Ticket Control View ───────────────────────────────

class TicketControlView(discord.ui.View):
    """Buttons inside an active ticket channel: Close, Claim."""

    def __init__(self, cog, ticket_num):
        super().__init__(timeout=None)
        self.cog = cog
        self.ticket_num = ticket_num

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Close the ticket with confirmation."""
        # Check if user is ticket creator or staff
        data = _load_ticket_data()
        guild_key = str(interaction.guild_id)
        ticket = data.get("tickets", {}).get(guild_key, {}).get(str(self.ticket_num))

        if not ticket:
            await interaction.response.send_message("❌ Ticket data not found.", ephemeral=True)
            return

        user_id = ticket.get("user_id")
        staff_role_id = data.get("config", {}).get("staff_role_id")
        is_staff = False
        if staff_role_id and isinstance(interaction.user, discord.Member):
            staff_role = interaction.guild.get_role(int(staff_role_id))
            if staff_role and staff_role in interaction.user.roles:
                is_staff = True

        if str(interaction.user.id) != user_id and not is_staff and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Only the ticket creator or staff can close this ticket.", ephemeral=True)
            return

        # Send confirmation
        confirm_view = TicketCloseConfirmView(self.cog, self.ticket_num)
        embed = discord.Embed(
            title="🔒 Close Ticket?",
            description="Are you sure you want to close this ticket? A transcript will be saved to the log channel.",
            color=0xFF4444,
        )
        await interaction.response.send_message(embed=embed, view=confirm_view)

    @discord.ui.button(label="👋 Claim Ticket", style=discord.ButtonStyle.secondary, custom_id="ticket_claim_btn")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Claim the ticket as a staff member."""
        data = _load_ticket_data()
        guild_key = str(interaction.guild_id)
        ticket = data.get("tickets", {}).get(guild_key, {}).get(str(self.ticket_num))

        if not ticket:
            await interaction.response.send_message("❌ Ticket data not found.", ephemeral=True)
            return

        if ticket.get("claimed_by"):
            await interaction.response.send_message(
                f"❌ This ticket is already claimed by <@{ticket['claimed_by']}>.",
                ephemeral=True,
            )
            return

        staff_role_id = data.get("config", {}).get("staff_role_id")
        is_staff = False
        if staff_role_id and isinstance(interaction.user, discord.Member):
            staff_role = interaction.guild.get_role(int(staff_role_id))
            if staff_role and staff_role in interaction.user.roles:
                is_staff = True

        if not is_staff and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Only staff can claim tickets.", ephemeral=True)
            return

        ticket["claimed_by"] = str(interaction.user.id)
        _save_ticket_data(data)

        embed = discord.Embed(
            title="👋 Ticket Claimed",
            description=f"{interaction.user.mention} has claimed this ticket and will be handling it.",
            color=0x44FF88,
        )
        await interaction.response.send_message(embed=embed)

        # Rename channel to show claimed status
        try:
            current_name = interaction.channel.name
            if not current_name.startswith("claimed-"):
                await interaction.channel.edit(name=f"claimed-{current_name}")
        except Exception:
            pass

        await _send_ticket_log(interaction.guild, "claimed", self.ticket_num, ticket.get("type", "unknown"), interaction.user)


class TicketCloseConfirmView(discord.ui.View):
    """Confirmation dialog for closing a ticket."""

    def __init__(self, cog, ticket_num):
        super().__init__(timeout=60)
        self.cog = cog
        self.ticket_num = ticket_num

    @discord.ui.button(label="✅ Yes, Close Ticket", style=discord.ButtonStyle.danger)
    async def confirm_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirm and close the ticket."""
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await self.cog._close_ticket(interaction, self.ticket_num)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel closing the ticket."""
        for child in self.children:
            child.disabled = True
        embed = discord.Embed(
            title="✅ Close Cancelled",
            description="Ticket close has been cancelled.",
            color=0x00FF88,
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass


# ── Ticket Cog ────────────────────────────────────────

class Tickets(commands.Cog):
    """Complete ticket system: panel, creation, claiming, closing, transcripts."""

    def __init__(self, bot):
        self.bot = bot

    # ── Setup Command ─────────────────────────────────

    @commands.command(name="ticketsetup")
    @commands.check(staff_required)
    async def cmd_ticketsetup(self, ctx):
        """Set up the ticket system interactively. Usage: !ticketsetup"""
        embed = discord.Embed(
            title="🎫 Ticket System Setup",
            description="Use the dropdowns below to configure the ticket system:\n\n"
                        "1. **Staff Role** — Members with this role can view and manage all tickets\n"
                        "2. **Log Channel** — Where ticket transcripts and logs are sent\n\n"
                        "Then click **Save Configuration** to apply.",
            color=0x44AAFF,
        )

        # Show current config if exists
        data = _load_ticket_data()
        cfg = data.get("config", {})
        if cfg.get("staff_role_id"):
            role = ctx.guild.get_role(int(cfg["staff_role_id"]))
            embed.add_field(name="Current Staff Role", value=role.mention if role else "Unknown", inline=False)
        if cfg.get("log_channel_id"):
            ch = ctx.guild.get_channel(int(cfg["log_channel_id"]))
            embed.add_field(name="Current Log Channel", value=ch.mention if ch else "Unknown", inline=False)

        view = TicketSetupView(self, ctx.guild)
        await ctx.send(embed=embed, view=view)

    @app_commands.command(name="ticket-setup", description="Set up the ticket system interactively")
    @app_commands.check(slash_staff_required)
    async def slash_ticketsetup(self, interaction: discord.Interaction):
        """Set up the ticket system interactively."""
        embed = discord.Embed(
            title="🎫 Ticket System Setup",
            description="Use the dropdowns below to configure the ticket system.",
            color=0x44AAFF,
        )
        data = _load_ticket_data()
        cfg = data.get("config", {})
        if cfg.get("staff_role_id"):
            role = interaction.guild.get_role(int(cfg["staff_role_id"]))
            embed.add_field(name="Current Staff Role", value=role.mention if role else "Unknown", inline=False)
        if cfg.get("log_channel_id"):
            ch = interaction.guild.get_channel(int(cfg["log_channel_id"]))
            embed.add_field(name="Current Log Channel", value=ch.mention if ch else "Unknown", inline=False)

        view = TicketSetupView(self, interaction.guild)
        await interaction.response.send_message(embed=embed, view=view)

    # ── Panel Command ─────────────────────────────────

    @commands.command(name="ticketpanel")
    @commands.check(staff_required)
    async def cmd_ticketpanel(self, ctx):
        """Post the ticket creation panel in this channel. Usage: !ticketpanel"""
        embed = discord.Embed(
            title="🎫 Create a Support Ticket",
            description="Need help? Click the button below to create a ticket and a staff member will assist you!",
            color=0x44AAFF,
        )
        embed.add_field(
            name="Available Types",
            value="\n".join(f"{tinfo['emoji']} **{tinfo['label']}** — {tinfo['description']}" for tinfo in TICKET_TYPES.values()),
            inline=False,
        )
        embed.set_footer(text="Your ticket channel is only visible to you and staff")

        view = TicketPanelView(self)
        await ctx.send(embed=embed, view=view)

    @app_commands.command(name="ticket-panel", description="Post the ticket creation panel in this channel")
    @app_commands.check(slash_staff_required)
    async def slash_ticketpanel(self, interaction: discord.Interaction):
        """Post the ticket creation panel in this channel."""
        embed = discord.Embed(
            title="🎫 Create a Support Ticket",
            description="Need help? Click the button below to create a ticket and a staff member will assist you!",
            color=0x44AAFF,
        )
        embed.add_field(
            name="Available Types",
            value="\n".join(f"{tinfo['emoji']} **{tinfo['label']}** — {tinfo['description']}" for tinfo in TICKET_TYPES.values()),
            inline=False,
        )
        embed.set_footer(text="Your ticket channel is only visible to you and staff")

        view = TicketPanelView(self)
        await interaction.response.send_message(embed=embed, view=view)

    # ── Ticket Config Command ─────────────────────────

    @commands.command(name="ticketconfig")
    @commands.check(staff_required)
    async def cmd_ticketconfig(self, ctx):
        """Show the current ticket system configuration. Usage: !ticketconfig"""
        data = _load_ticket_data()
        cfg = data.get("config", {})

        embed = discord.Embed(title="🎫 Ticket System Configuration", color=0x44AAFF)

        staff_role_id = cfg.get("staff_role_id")
        if staff_role_id:
            role = ctx.guild.get_role(int(staff_role_id))
            embed.add_field(name="👮 Staff Role", value=role.mention if role else f"`{staff_role_id}` (not found)", inline=False)
        else:
            embed.add_field(name="👮 Staff Role", value="❌ Not set", inline=False)

        log_channel_id = cfg.get("log_channel_id")
        if log_channel_id:
            ch = ctx.guild.get_channel(int(log_channel_id))
            embed.add_field(name="📁 Log Channel", value=ch.mention if ch else f"`{log_channel_id}` (not found)", inline=False)
        else:
            embed.add_field(name="📁 Log Channel", value="❌ Not set", inline=False)

        # Categories
        categories = cfg.get("categories", {})
        cat_list = []
        for tkey, tinfo in TICKET_TYPES.items():
            cat_data = categories.get(tkey, {})
            cat_id = cat_data.get("id")
            if cat_id:
                cat = ctx.guild.get_channel(int(cat_id))
                cat_list.append(f"{tinfo['emoji']} {tinfo['label']}: {cat.mention if cat else '`'+cat_id+'`'}")
            else:
                cat_list.append(f"{tinfo['emoji']} {tinfo['label']}: ⏳ Not yet created")
        embed.add_field(name="📂 Categories", value="\n".join(cat_list) if cat_list else "None", inline=False)

        # Ticket stats
        guild_key = str(ctx.guild.id)
        tickets = data.get("tickets", {}).get(guild_key, {})
        open_count = sum(1 for t in tickets.values() if t.get("status") == "open")
        total_count = len(tickets)
        embed.add_field(name="📊 Ticket Stats", value=f"**Open:** {open_count} | **Total:** {total_count}", inline=False)

        embed.set_footer(text="Use !ticketsetup to change settings • !ticketpanel to post the panel")

        await ctx.send(embed=embed)

    @app_commands.command(name="ticket-config", description="Show current ticket system configuration")
    @app_commands.check(slash_staff_required)
    async def slash_ticketconfig(self, interaction: discord.Interaction):
        """Show current ticket system configuration."""
        data = _load_ticket_data()
        cfg = data.get("config", {})

        embed = discord.Embed(title="🎫 Ticket System Configuration", color=0x44AAFF)

        staff_role_id = cfg.get("staff_role_id")
        if staff_role_id:
            role = interaction.guild.get_role(int(staff_role_id))
            embed.add_field(name="👮 Staff Role", value=role.mention if role else f"`{staff_role_id}` (not found)", inline=False)
        else:
            embed.add_field(name="👮 Staff Role", value="❌ Not set", inline=False)

        log_channel_id = cfg.get("log_channel_id")
        if log_channel_id:
            ch = interaction.guild.get_channel(int(log_channel_id))
            embed.add_field(name="📁 Log Channel", value=ch.mention if ch else f"`{log_channel_id}` (not found)", inline=False)
        else:
            embed.add_field(name="📁 Log Channel", value="❌ Not set", inline=False)

        categories = cfg.get("categories", {})
        cat_list = []
        for tkey, tinfo in TICKET_TYPES.items():
            cat_data = categories.get(tkey, {})
            cat_id = cat_data.get("id")
            if cat_id:
                cat = interaction.guild.get_channel(int(cat_id))
                cat_list.append(f"{tinfo['emoji']} {tinfo['label']}: {cat.mention if cat else '`'+cat_id+'`'}")
            else:
                cat_list.append(f"{tinfo['emoji']} {tinfo['label']}: ⏳ Not yet created")
        embed.add_field(name="📂 Categories", value="\n".join(cat_list) if cat_list else "None", inline=False)

        guild_key = str(interaction.guild_id)
        tickets = data.get("tickets", {}).get(guild_key, {})
        open_count = sum(1 for t in tickets.values() if t.get("status") == "open")
        total_count = len(tickets)
        embed.add_field(name="📊 Ticket Stats", value=f"**Open:** {open_count} | **Total:** {total_count}", inline=False)

        embed.set_footer(text="Use /ticket-setup to change settings • /ticket-panel to post the panel")

        await interaction.response.send_message(embed=embed)

    # ── Close Command ─────────────────────────────────

    @commands.command(name="close")
    async def cmd_close(self, ctx):
        """Close the current ticket channel. Usage: !close (only works in ticket channels)"""
        ticket_num = await self._get_ticket_num_from_channel(ctx.channel)
        if not ticket_num:
            await ctx.send("❌ This command can only be used in a ticket channel.", delete_after=5)
            return

        data = _load_ticket_data()
        guild_key = str(ctx.guild.id)
        ticket = data.get("tickets", {}).get(guild_key, {}).get(str(ticket_num))

        if not ticket:
            await ctx.send("❌ Ticket data not found.", delete_after=5)
            return

        # Check permissions
        user_id = ticket.get("user_id")
        staff_role_id = data.get("config", {}).get("staff_role_id")
        is_staff = False
        if staff_role_id and isinstance(ctx.author, discord.Member):
            staff_role = ctx.guild.get_role(int(staff_role_id))
            if staff_role and staff_role in ctx.author.roles:
                is_staff = True

        if str(ctx.author.id) != user_id and not is_staff and not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Only the ticket creator or staff can close this ticket.", delete_after=5)
            return

        confirm_view = TicketCloseConfirmView(self, ticket_num)
        embed = discord.Embed(
            title="🔒 Close Ticket?",
            description="Are you sure you want to close this ticket? A transcript will be saved.",
            color=0xFF4444,
        )
        await ctx.send(embed=embed, view=confirm_view)

    @app_commands.command(name="close", description="Close the current ticket channel")
    async def slash_close(self, interaction: discord.Interaction):
        """Close the current ticket channel."""
        ticket_num = await self._get_ticket_num_from_channel(interaction.channel)
        if not ticket_num:
            await interaction.response.send_message("❌ This command can only be used in a ticket channel.", ephemeral=True)
            return

        data = _load_ticket_data()
        guild_key = str(interaction.guild_id)
        ticket = data.get("tickets", {}).get(guild_key, {}).get(str(ticket_num))

        if not ticket:
            await interaction.response.send_message("❌ Ticket data not found.", ephemeral=True)
            return

        user_id = ticket.get("user_id")
        staff_role_id = data.get("config", {}).get("staff_role_id")
        is_staff = False
        if staff_role_id and isinstance(interaction.user, discord.Member):
            staff_role = interaction.guild.get_role(int(staff_role_id))
            if staff_role and staff_role in interaction.user.roles:
                is_staff = True

        if str(interaction.user.id) != user_id and not is_staff and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Only the ticket creator or staff can close this ticket.", ephemeral=True)
            return

        confirm_view = TicketCloseConfirmView(self, ticket_num)
        embed = discord.Embed(
            title="🔒 Close Ticket?",
            description="Are you sure you want to close this ticket? A transcript will be saved.",
            color=0xFF4444,
        )
        await interaction.response.send_message(embed=embed, view=confirm_view)

    # ── Add Command to Ticket ─────────────────────────

    @commands.command(name="add")
    async def cmd_add(self, ctx, *, member: discord.Member):
        """Add a user to the current ticket channel. Usage: !add <member> (staff only)"""
        ticket_num = await self._get_ticket_num_from_channel(ctx.channel)
        if not ticket_num:
            await ctx.send("❌ This command can only be used in a ticket channel.", delete_after=5)
            return

        data = _load_ticket_data()
        staff_role_id = data.get("config", {}).get("staff_role_id")
        is_staff = False
        if staff_role_id and isinstance(ctx.author, discord.Member):
            staff_role = ctx.guild.get_role(int(staff_role_id))
            if staff_role and staff_role in ctx.author.roles:
                is_staff = True

        if not is_staff and not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Only staff members can add users to tickets.", delete_after=5)
            return

        try:
            await ctx.channel.set_permissions(member, read_messages=True, send_messages=True)
            await ctx.send(f"✅ {member.mention} has been added to this ticket.")
        except Exception as e:
            await ctx.send(f"❌ Failed to add {member.mention}: {e}")

    @app_commands.command(name="ticket-add", description="Add a user to the current ticket channel (staff only)")
    @app_commands.describe(member="The member to add to this ticket")
    async def slash_add(self, interaction: discord.Interaction, member: discord.Member):
        """Add a user to the current ticket channel."""
        ticket_num = await self._get_ticket_num_from_channel(interaction.channel)
        if not ticket_num:
            await interaction.response.send_message("❌ This command can only be used in a ticket channel.", ephemeral=True)
            return

        data = _load_ticket_data()
        staff_role_id = data.get("config", {}).get("staff_role_id")
        is_staff = False
        if staff_role_id and isinstance(interaction.user, discord.Member):
            staff_role = interaction.guild.get_role(int(staff_role_id))
            if staff_role and staff_role in interaction.user.roles:
                is_staff = True

        if not is_staff and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Only staff members can add users to tickets.", ephemeral=True)
            return

        try:
            await interaction.channel.set_permissions(member, read_messages=True, send_messages=True)
            await interaction.response.send_message(f"✅ {member.mention} has been added to this ticket.")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to add {member.mention}: {e}", ephemeral=True)

    # ── Internal Helpers ──────────────────────────────

    async def _get_ticket_num_from_channel(self, channel):
        """Find the ticket number associated with a channel."""
        data = _load_ticket_data()
        guild_key = str(channel.guild.id)
        channel_id = str(channel.id)
        guild_tickets = data.get("tickets", {}).get(guild_key, {})

        for tnum, tdata in guild_tickets.items():
            if tdata.get("channel_id") == channel_id:
                return int(tnum)
        return None

    async def _close_ticket(self, interaction_or_ctx, ticket_num):
        """Internal method to close a ticket, generate transcript, and delete the channel."""
        guild = interaction_or_ctx.guild
        channel = interaction_or_ctx.channel
        user = interaction_or_ctx.user

        data = _load_ticket_data()
        guild_key = str(guild.id)
        ticket = data.get("tickets", {}).get(guild_key, {}).get(str(ticket_num))

        if not ticket:
            try:
                await interaction_or_ctx.send("❌ Ticket data not found.")
            except Exception:
                pass
            return

        # Generate transcript
        transcript_lines = []
        transcript_lines.append(f"{'='*60}")
        transcript_lines.append(f"TICKET TRANSCRIPT — #{ticket_num}")
        transcript_lines.append(f"{'='*60}")
        transcript_lines.append(f"")
        transcript_lines.append(f"Type:       {ticket.get('type', 'unknown')}")
        transcript_lines.append(f"Created by: {ticket.get('username', 'unknown')} ({ticket.get('user_id', '?')})")
        transcript_lines.append(f"Subject:    {ticket.get('subject', 'N/A')}")
        transcript_lines.append(f"Created at: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(ticket.get('created_at', 0)))}")
        transcript_lines.append(f"Closed by:  {user} ({user.id})")
        transcript_lines.append(f"")
        transcript_lines.append(f"{'─'*60}")
        transcript_lines.append(f"MESSAGE LOG")
        transcript_lines.append(f"{'─'*60}")
        transcript_lines.append(f"")

        # Collect messages from the channel
        try:
            messages = []
            async for msg in channel.history(limit=500, oldest_first=True):
                messages.append(msg)

            for msg in messages:
                ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
                author = f"{msg.author} ({msg.author.id})"
                content = msg.clean_content.replace("\n", "\\n")
                if content:
                    transcript_lines.append(f"[{ts}] {author}: {content}")
                for attachment in msg.attachments:
                    transcript_lines.append(f"[{ts}] {author}: [ATTACHMENT] {attachment.url}")
        except Exception as e:
            transcript_lines.append(f"[ERROR fetching messages: {e}]")

        transcript_lines.append(f"")
        transcript_lines.append(f"{'─'*60}")
        transcript_lines.append(f"END OF TRANSCRIPT — Ticket #{ticket_num}")
        transcript_lines.append(f"{'='*60}")

        transcript_text = "\n".join(transcript_lines)

        # Update ticket record
        ticket["status"] = "closed"
        ticket["closed_at"] = time.time()
        ticket["closed_by"] = str(user.id)
        ticket["transcript"] = transcript_text
        _save_ticket_data(data)

        # ── Gambling ticket: add gambling role to user ─────────
        if ticket.get("type") == "gambling":
            ticket_user_id = ticket.get("user_id", "")
            if ticket_user_id:
                try:
                    member = guild.get_member(int(ticket_user_id))
                    if member:
                        gambling_role = guild.get_role(GAMBLING_ROLE_ID)
                        if gambling_role:
                            await member.add_roles(gambling_role, reason=f"Gambling ticket #{ticket_num} approved by {user}")
                            _log.info(f"Tickets: Added gambling role to {member} via ticket #{ticket_num}")
                            try:
                                await channel.send(f"🎰 Gambling role has been assigned to <@{ticket_user_id}>.")
                            except Exception:
                                pass
                        else:
                            _log.warning(f"Tickets: Gambling role {GAMBLING_ROLE_ID} not found in guild")
                            try:
                                await channel.send(
                                    f"⚠️ Could not assign gambling role — role ID `{GAMBLING_ROLE_ID}` "
                                    f"not found on this server. Please create it or update the config."
                                )
                            except Exception:
                                pass
                except Exception as e:
                    _log.warning(f"Tickets: Failed to assign gambling role via ticket #{ticket_num}: {e}")

        # ── Appeal ticket: remove warnings/violations for user ─
        if ticket.get("type") == "appeal":
            ticket_user_id = ticket.get("user_id", "")
            if ticket_user_id:
                try:
                    from modules.moderation import clear_user_violations
                    removed = clear_user_violations(guild.id, ticket_user_id)
                    if removed:
                        _log.info(
                            f"Tickets: Cleared {removed} warning(s)/violation(s) "
                            f"for {ticket_user_id} via appeal ticket #{ticket_num}"
                        )
                        try:
                            await channel.send(
                                f"⚖️ Appeal accepted — cleared {removed} warning(s)/violation(s) "
                                f"for <@{ticket_user_id}>."
                            )
                        except Exception:
                            pass

                    # Also clear any active timeouts for the user
                    member = guild.get_member(int(ticket_user_id))
                    if member and member.is_timed_out():
                        try:
                            await member.timeout(None, reason=f"Appeal ticket #{ticket_num} accepted")
                            _log.info(f"Tickets: Removed timeout for {member} via appeal ticket #{ticket_num}")
                        except Exception as e:
                            _log.warning(f"Tickets: Failed to remove timeout for {member}: {e}")

                except Exception as e:
                    _log.warning(f"Tickets: Failed to clear violations via appeal ticket #{ticket_num}: {e}")

        # Send transcript to log channel
        log_channel_id = data.get("config", {}).get("log_channel_id")
        if log_channel_id:
            try:
                log_channel = guild.get_channel(int(log_channel_id)) or await guild.fetch_channel(int(log_channel_id))
                if log_channel:
                    tinfo = TICKET_TYPES.get(ticket.get("type", ""), {})
                    embed = discord.Embed(
                        title=f"🔒 Ticket #{ticket_num} Closed",
                        color=0xFF4444,
                        timestamp=discord.utils.utcnow(),
                    )
                    embed.add_field(name="Type", value=f"{tinfo.get('emoji', '📋')} {tinfo.get('label', ticket.get('type', 'unknown'))}", inline=True)
                    embed.add_field(name="User", value=ticket.get("username", "unknown"), inline=True)
                    embed.add_field(name="Subject", value=ticket.get("subject", "N/A"), inline=False)
                    embed.add_field(name="Closed by", value=str(user), inline=True)

                    # Send transcript as a file
                    transcript_file = discord.File(
                        io.StringIO(transcript_text),
                        filename=f"ticket-{ticket_num}-transcript.txt",
                    )
                    await log_channel.send(embed=embed, file=transcript_file)
            except Exception as e:
                _log.warning(f"Failed to send ticket transcript to log channel: {e}")

        # Log the closure
        await _send_ticket_log(guild, "closed", ticket_num, ticket.get("type", "unknown"), user, reason=f"Closed by {user}")

        # Notify the channel and then delete it
        close_embed = discord.Embed(
            title="🔒 Ticket Closed",
            description="This ticket is now being closed. The channel will be deleted shortly.\n\nA transcript has been saved to the log channel.",
            color=0xFF4444,
        )
        try:
            await channel.send(embed=close_embed)
        except Exception:
            pass

        # Small delay so users can see the message
        await asyncio.sleep(3)

        # Delete the channel
        try:
            await channel.delete(reason=f"Ticket #{ticket_num} closed by {user}")
        except Exception as e:
            _log.warning(f"Failed to delete ticket channel: {e}")


# ── Cog Setup ──────────────────────────────────────────

async def setup(bot):
    """Load the Tickets cog into the bot."""
    await bot.add_cog(Tickets(bot))
    _log.info("Tickets cog loaded")
