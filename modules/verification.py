"""
Limey Verification Bot
======================
A complete verification system for the manager bot.
Provides account age checking, captcha verification, and role assignment.

Loaded as a cog by the ManagerBot class.

Configuration (stored in manager_bot.verification in settings.json):
  guild_id              — Guild where verification happens
  verified_role_id      — Role to assign on successful verification
  unverified_role_id    — Optional role for unverified members
  log_channel_id        — Channel for verification logs
  min_account_age_days  — Minimum account age in days (0 to disable)
  captcha_enabled       — Whether to require captcha (True/False)
  welcome_channel_id    — Channel for welcome messages
"""

import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import time
import logging
import random

_log = logging.getLogger("verification_bot")

# ── Config Helpers ─────────────────────────────────────

from utils.github_data_store import ghd


def _load_verification_config():
    """Load verification config from manager_bot settings."""
    cfg = ghd.read_json("config/settings.json", default={})
    return cfg.get("manager_bot", {}).get("verification", {})


def _save_verification_config(new_cfg):
    """Save verification config back to settings.json."""
    full = ghd.read_json("config/settings.json", default={})
    if full is None:
        full = {}
    if "manager_bot" not in full:
        full["manager_bot"] = {}
    full["manager_bot"]["verification"] = new_cfg
    return ghd.write_json("config/settings.json", full, message="Update verification config")


def _log_verification(guild_id, user_id, username, action, details):
    """Log a verification event."""
    _log.info(f"VERIFY [{action}] {username} ({user_id}) in guild {guild_id}: {details}")


# ── Captcha Generator ─────────────────────────────────


class CaptchaGenerator:
    """Generates simple math captcha challenges."""

    # Operators and their display symbols
    OPERATORS = [
        ("+", lambda a, b: a + b),
        ("-", lambda a, b: a - b),
        ("×", lambda a, b: a * b),
    ]

    @classmethod
    def generate(cls, difficulty="medium"):
        """Generate a captcha challenge. Returns (question_text, correct_answer)."""
        if difficulty == "easy":
            a = random.randint(1, 10)
            b = random.randint(1, 10)
            op_symbol, op_func = random.choice(cls.OPERATORS[:2])  # + or - only
        elif difficulty == "medium":
            a = random.randint(5, 50)
            b = random.randint(5, 50)
            op_symbol, op_func = random.choice(cls.OPERATORS)
            # Ensure no negative results for medium difficulty
            if op_symbol == "-" and a < b:
                a, b = b, a
        else:  # hard
            a = random.randint(10, 99)
            b = random.randint(2, 20)
            op_symbol, op_func = random.choice(cls.OPERATORS)
            if op_symbol == "-" and a < b:
                a, b = b, a
            # For multiplication, keep numbers smaller
            if op_symbol == "×":
                a = random.randint(2, 25)
                b = random.randint(2, 12)

        answer = op_func(a, b)
        question = f"**{a} {op_symbol} {b} = ?**"
        return question, answer


# ── Verification Views ─────────────────────────────────


class VerificationView(discord.ui.View):
    """View with a single Verify button. Posted in the verification channel."""

    def __init__(self, cog):
        super().__init__(timeout=None)  # Persistent view
        self.cog = cog

    @discord.ui.button(label="✅ Verify", style=discord.ButtonStyle.success, custom_id="verify_btn", emoji="🛡️")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle the verify button click."""
        # Check if already verified
        config = _load_verification_config()
        verified_role_id = config.get("verified_role_id", "")
        guild = interaction.guild

        if verified_role_id and guild:
            role = guild.get_role(int(verified_role_id))
            if role and role in interaction.user.roles:
                await interaction.response.send_message(
                    "✅ You are already verified!", ephemeral=True
                )
                return

        captcha_enabled = config.get("captcha_enabled", True)

        if captcha_enabled:
            # Check cooldown to prevent spam
            now = time.time()
            last_captcha = self.cog._captcha_cooldowns.get(interaction.user.id, 0)
            if now - last_captcha < 30:
                remaining = int(30 - (now - last_captcha))
                await interaction.response.send_message(
                    f"⏳ Please wait **{remaining}s** before requesting a new captcha.",
                    ephemeral=True
                )
                return

            # Generate captcha and open modal
            difficulty = config.get("captcha_difficulty", "medium")
            question, answer = CaptchaGenerator.generate(difficulty)

            # Store the answer
            self.cog._active_captchas[interaction.user.id] = {
                "answer": answer,
                "timestamp": time.time(),
            }

            modal = CaptchaModal(self.cog, question, interaction.user.id)
            await interaction.response.send_modal(modal)
        else:
            # No captcha required, verify immediately
            await self.cog._assign_verified_role(interaction.user, guild)
            await interaction.response.send_message(
                "✅ You have been verified! Welcome to the server! 🎉", ephemeral=True
            )


class CaptchaModal(discord.ui.Modal):
    """Modal that shows a captcha question and expects the answer."""

    def __init__(self, cog, question, user_id):
        self.cog = cog
        self._user_id = user_id
        super().__init__(title="🔐 Human Verification")

        self.captcha_input = discord.ui.TextInput(
            label=f"Solve: {question}",
            placeholder="Type your answer...",
            style=discord.TextStyle.short,
            required=True,
            max_length=10,
        )
        self.add_item(self.captcha_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Check the captcha answer."""
        captcha_data = self.cog._active_captchas.pop(self._user_id, None)
        if not captcha_data:
            await interaction.response.send_message(
                "❌ Captcha expired. Please click the Verify button again.",
                ephemeral=True
            )
            return

        try:
            user_answer = int(self.captcha_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid answer. Please enter a number.\nClick the Verify button to try again.",
                ephemeral=True
            )
            return

        correct_answer = captcha_data["answer"]

        if user_answer == correct_answer:
            # Success!
            self.cog._captcha_cooldowns[interaction.user.id] = time.time()
            await self.cog._assign_verified_role(interaction.user, interaction.guild)
            await interaction.response.send_message(
                "✅ **Verification successful!** Welcome to the server! 🎉\nYou now have full access to all channels.",
                ephemeral=True
            )
            _log_verification(
                interaction.guild_id, interaction.user.id, str(interaction.user),
                "VERIFIED", "Passed captcha"
            )
        else:
            await interaction.response.send_message(
                f"❌ **Incorrect answer.** The correct answer was **{correct_answer}**.\n"
                "Click the Verify button to try again with a new challenge.",
                ephemeral=True
            )


# ── Verification Cog ───────────────────────────────────


class Verification(commands.Cog):
    """Complete verification system: account age check, captcha, button verification."""

    def __init__(self, bot):
        self.bot = bot
        self._active_captchas = {}  # user_id -> {"answer": int, "timestamp": float}
        self._captcha_cooldowns = {}  # user_id -> timestamp

    # ── Events ────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle new members: check account age, assign unverified role, send welcome."""
        config = _load_verification_config()
        guild_id = config.get("guild_id", "")

        if str(member.guild.id) != str(guild_id):
            return  # Not our verification guild

        # ── Account Age Check ──────────────────────────
        min_age_days = config.get("min_account_age_days", 0)
        if min_age_days > 0:
            account_age_days = (discord.utils.utcnow() - member.created_at).days
            if account_age_days < min_age_days:
                try:
                    await member.send(
                        f"❌ Your Discord account is **{account_age_days} day(s)** old, "
                        f"which is below the minimum requirement of **{min_age_days} day(s)**.\n"
                        f"Please come back when your account is older. Goodbye!"
                    )
                except Exception:
                    pass
                try:
                    await member.kick(reason=f"Account age: {account_age_days}d (min: {min_age_days}d)")
                    _log_verification(
                        str(member.guild.id), member.id, str(member),
                        "KICKED", f"Account too young: {account_age_days}d < {min_age_days}d"
                    )
                except Exception as e:
                    _log.warning(f"Failed to kick {member}: {e}")
                return

        # ── Assign Unverified Role ─────────────────────
        unverified_role_id = config.get("unverified_role_id", "")
        if unverified_role_id:
            try:
                role = member.guild.get_role(int(unverified_role_id))
                if role:
                    await member.add_roles(role, reason="New member - unverified")
            except Exception as e:
                _log.warning(f"Failed to assign unverified role to {member}: {e}")

        # ── Welcome Message ────────────────────────────
        welcome_channel_id = config.get("welcome_channel_id", "")
        welcome_message = config.get(
            "welcome_message",
            "Welcome {mention}! 🎉\nPlease click the **Verify** button below to gain access to the server."
        )

        welcome_channel = None
        if welcome_channel_id:
            welcome_channel = member.guild.get_channel(int(welcome_channel_id))

        if welcome_channel:
            embed = discord.Embed(
                title="🛡️ Welcome to the Server!",
                description=welcome_message.replace("{mention}", member.mention)
                         .replace("{username}", member.name)
                         .replace("{server}", member.guild.name),
                color=0x00FF88,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_thumbnail(url=member.display_avatar.url if member.display_avatar else None)
            embed.set_footer(text=f"Account created {discord.utils.format_dt(member.created_at, 'R')}")

            # Post the verification view
            view = VerificationView(self)
            await welcome_channel.send(embed=embed, view=view)

        _log_verification(
            str(member.guild.id), member.id, str(member),
            "JOINED", f"Age: {(discord.utils.utcnow() - member.created_at).days}d"
        )

    # ── Helper ─────────────────────────────────────────

    async def _assign_verified_role(self, user: discord.Member, guild: discord.Guild):
        """Assign the verified role and remove unverified role."""
        config = _load_verification_config()
        verified_role_id = config.get("verified_role_id", "")
        unverified_role_id = config.get("unverified_role_id", "")

        roles_to_add = []
        roles_to_remove = []

        if verified_role_id:
            role = guild.get_role(int(verified_role_id))
            if role and role not in user.roles:
                roles_to_add.append(role)

        if unverified_role_id:
            role = guild.get_role(int(unverified_role_id))
            if role and role in user.roles:
                roles_to_remove.append(role)

        try:
            if roles_to_add:
                await user.add_roles(*roles_to_add, reason="Verified via verification system")
            if roles_to_remove:
                await user.remove_roles(*roles_to_remove, reason="Verified - no longer unverified")
        except discord.Forbidden:
            _log.warning(f"No permission to manage roles for {user}")
        except Exception as e:
            _log.warning(f"Failed to update roles for {user}: {e}")

        # Log to verification channel
        log_channel_id = config.get("log_channel_id", "")
        if log_channel_id:
            log_channel = guild.get_channel(int(log_channel_id))
            if log_channel:
                embed = discord.Embed(
                    title="✅ User Verified",
                    description=f"{user.mention} **{user}** ({user.id})",
                    color=0x00FF88,
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Account Created", value=discord.utils.format_dt(user.created_at, 'R'), inline=True)
                embed.add_field(name="Joined Server", value=discord.utils.format_dt(user.joined_at, 'R') if user.joined_at else "Unknown", inline=True)
                embed.set_thumbnail(url=user.display_avatar.url if user.display_avatar else None)
                try:
                    await log_channel.send(embed=embed)
                except Exception:
                    pass

    # ── Prefix Commands ───────────────────────────────

    @commands.command(name="verifypanel")
    @commands.has_permissions(administrator=True)
    async def cmd_verifypanel(self, ctx):
        """Post the verification panel with the Verify button."""
        config = _load_verification_config()
        guild_id = config.get("guild_id", "")

        if str(ctx.guild.id) != str(guild_id):
            await ctx.send("❌ Verification is not configured for this guild.")
            return

        verified_role_id = config.get("verified_role_id", "")
        if not verified_role_id:
            await ctx.send("❌ No verified role configured. Use `!verifyconfig` to set one up.")
            return

        embed = discord.Embed(
            title="🛡️ Server Verification",
            description=(
                "Welcome! To gain access to all channels, please complete the verification below.\n\n"
                "Click the **Verify** button to start. You may need to solve a simple challenge "
                "to prove you're human."
            ),
            color=0x00FF88,
        )
        embed.add_field(
            name="What happens next?",
            value=(
                "1️⃣ Click **Verify** below\n"
                "2️⃣ Solve the captcha challenge (if enabled)\n"
                "3️⃣ Get verified and access all channels! 🎉"
            ),
            inline=False,
        )
        embed.set_footer(text="Verification System")

        view = VerificationView(self)
        await ctx.send(embed=embed, view=view)
        await ctx.message.delete()

    @commands.command(name="verifyconfig")
    @commands.has_permissions(administrator=True)
    async def cmd_verifyconfig(self, ctx, setting: str = "", *, value: str = ""):
        """View or set verification configuration.
        
        Usage:
          !verifyconfig                          — Show current config
          !verifyconfig verified_role_id <id>    — Set the verified role
          !verifyconfig unverified_role_id <id>  — Set the unverified role
          !verifyconfig welcome_channel_id <id>  — Set the welcome channel
          !verifyconfig log_channel_id <id>      — Set the log channel
          !verifyconfig min_account_age_days <n> — Minimum account age in days
          !verifyconfig captcha_enabled on/off   — Enable/disable captcha
          !verifyconfig captcha_difficulty <lvl> — easy/medium/hard
          !verifyconfig welcome_message <msg>    — Custom welcome message
        """
        if not setting:
            # Show current config
            cfg = _load_verification_config()
            embed = discord.Embed(
                title="⚙️ Verification Configuration",
                color=0x4488FF,
                timestamp=discord.utils.utcnow(),
            )

            fields = [
                ("Verified Role", f"<@&{cfg.get('verified_role_id', 'Not set')}>" if cfg.get('verified_role_id') else "❌ Not set"),
                ("Unverified Role", f"<@&{cfg.get('unverified_role_id', 'Not set')}>" if cfg.get('unverified_role_id') else "Not configured"),
                ("Welcome Channel", f"<#{cfg.get('welcome_channel_id', 'Not set')}>" if cfg.get('welcome_channel_id') else "Not configured"),
                ("Log Channel", f"<#{cfg.get('log_channel_id', 'Not set')}>" if cfg.get('log_channel_id') else "Not configured"),
                ("Min Account Age", f"{cfg.get('min_account_age_days', 0)} day(s)"),
                ("Captcha Enabled", "✅ Yes" if cfg.get('captcha_enabled', True) else "❌ No"),
                ("Captcha Difficulty", cfg.get('captcha_difficulty', 'medium').capitalize()),
                ("Guild ID", cfg.get('guild_id', str(ctx.guild.id))),
            ]

            for name, val in fields:
                embed.add_field(name=name, value=val, inline=True)

            welcome_msg = cfg.get('welcome_message', '')
            if welcome_msg:
                embed.add_field(name="Welcome Message", value=f"```{welcome_msg[:100]}```", inline=False)

            embed.set_footer(text=f"Use !verifyconfig <setting> <value> to change")
            await ctx.send(embed=embed)
            return

        # Handle setting changes
        setting = setting.lower()
        cfg = _load_verification_config()
        changed = False

        if setting == "verified_role_id":
            try:
                role_id = int(value.strip("<@&>"))
                cfg["verified_role_id"] = str(role_id)
                cfg["guild_id"] = str(ctx.guild.id)
                changed = True
            except ValueError:
                await ctx.send("❌ Invalid role ID. Use a Discord role ID number.")
                return

        elif setting == "unverified_role_id":
            try:
                role_id = int(value.strip("<@&>"))
                cfg["unverified_role_id"] = str(role_id)
                changed = True
            except ValueError:
                await ctx.send("❌ Invalid role ID.")
                return

        elif setting == "welcome_channel_id":
            try:
                channel_id = int(value.strip("<#>"))
                cfg["welcome_channel_id"] = str(channel_id)
                changed = True
            except ValueError:
                await ctx.send("❌ Invalid channel ID.")
                return

        elif setting == "log_channel_id":
            try:
                channel_id = int(value.strip("<#>"))
                cfg["log_channel_id"] = str(channel_id)
                changed = True
            except ValueError:
                await ctx.send("❌ Invalid channel ID.")
                return

        elif setting == "min_account_age_days":
            try:
                days = int(value)
                if days < 0:
                    raise ValueError
                cfg["min_account_age_days"] = days
                changed = True
            except ValueError:
                await ctx.send("❌ Invalid number. Use a positive integer (0 to disable).")
                return

        elif setting == "captcha_enabled":
            val = value.strip().lower()
            if val in ("on", "enable", "true", "1"):
                cfg["captcha_enabled"] = True
                changed = True
            elif val in ("off", "disable", "false", "0"):
                cfg["captcha_enabled"] = False
                changed = True
            else:
                await ctx.send("❌ Use `on` or `off`.")
                return

        elif setting == "captcha_difficulty":
            val = value.strip().lower()
            if val in ("easy", "medium", "hard"):
                cfg["captcha_difficulty"] = val
                changed = True
            else:
                await ctx.send("❌ Difficulty must be `easy`, `medium`, or `hard`.")
                return

        elif setting == "welcome_message":
            if value:
                cfg["welcome_message"] = value
                changed = True
            else:
                await ctx.send("❌ Please provide a welcome message.")
                return

        else:
            await ctx.send(
                f"❌ Unknown setting: `{setting}`\n"
                f"Valid settings: verified_role_id, unverified_role_id, welcome_channel_id, "
                f"log_channel_id, min_account_age_days, captcha_enabled, captcha_difficulty, welcome_message"
            )
            return

        if changed:
            _save_verification_config(cfg)
            await ctx.send(f"✅ `{setting}` has been updated.")
            _log.info(f"Verification config updated: {setting} = {value}")
        else:
            await ctx.send("⚠️ No changes were made.")

    # ── Slash Commands ────────────────────────────────

    @app_commands.command(name="verifypanel", description="Post the verification panel with the Verify button")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_verifypanel(self, interaction: discord.Interaction):
        """Post the verification panel."""
        config = _load_verification_config()
        guild_id = config.get("guild_id", "")

        if str(interaction.guild_id) != str(guild_id):
            await interaction.response.send_message("❌ Verification is not configured for this guild.", ephemeral=True)
            return

        verified_role_id = config.get("verified_role_id", "")
        if not verified_role_id:
            await interaction.response.send_message(
                "❌ No verified role configured. Use `/verifyconfig` to set one up.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🛡️ Server Verification",
            description=(
                "Welcome! To gain access to all channels, please complete the verification below.\n\n"
                "Click the **Verify** button to start."
            ),
            color=0x00FF88,
        )
        embed.add_field(
            name="What happens next?",
            value="1️⃣ Click **Verify** below\n2️⃣ Solve the captcha challenge (if enabled)\n3️⃣ Get verified! 🎉",
            inline=False,
        )
        embed.set_footer(text="Verification System")

        view = VerificationView(self)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="verifyconfig", description="View or update verification configuration")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_verifyconfig(self, interaction: discord.Interaction):
        """Show verification config."""
        cfg = _load_verification_config()
        embed = discord.Embed(
            title="⚙️ Verification Configuration",
            color=0x4488FF,
            timestamp=discord.utils.utcnow(),
        )

        fields = [
            ("Verified Role", f"<@&{cfg.get('verified_role_id', 'Not set')}>" if cfg.get('verified_role_id') else "❌ Not set"),
            ("Unverified Role", f"<@&{cfg.get('unverified_role_id', 'Not set')}>" if cfg.get('unverified_role_id') else "Not configured"),
            ("Welcome Channel", f"<#{cfg.get('welcome_channel_id', 'Not set')}>" if cfg.get('welcome_channel_id') else "Not configured"),
            ("Log Channel", f"<#{cfg.get('log_channel_id', 'Not set')}>" if cfg.get('log_channel_id') else "Not configured"),
            ("Min Account Age", f"{cfg.get('min_account_age_days', 0)} day(s)"),
            ("Captcha Enabled", "✅ Yes" if cfg.get('captcha_enabled', True) else "❌ No"),
            ("Captcha Difficulty", cfg.get('captcha_difficulty', 'medium').capitalize()),
            ("Guild ID", cfg.get('guild_id', 'Auto-detected')),
        ]

        for name, val in fields:
            embed.add_field(name=name, value=val, inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Setup ──────────────────────────────────────────────


async def setup(bot):
    """Add the Verification cog to the bot and register the persistent view."""
    cog = Verification(bot)
    await bot.add_cog(cog)
    # Register the persistent verification button view so it survives bot restarts
    bot.add_view(VerificationView(cog))
