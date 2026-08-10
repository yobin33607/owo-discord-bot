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
import io
import json
import os
import time
import logging
import random

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = ImageFont = None

_PIL_AVAILABLE = Image is not None and ImageDraw is not None and ImageFont is not None

_log = logging.getLogger("verification_bot")

# ── Config Helpers ─────────────────────────────────────

from utils.github_data_store import ghd

# ── Staff gate for admin commands ─────────────────────

from modules.staff_gate import staff_required, slash_staff_required


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


VALID_CONFIG_SETTINGS = (
    "verified_role_id",
    "unverified_role_id",
    "welcome_channel_id",
    "log_channel_id",
    "min_account_age_days",
    "captcha_enabled",
    "captcha_difficulty",
    "welcome_message",
    "guild_id",
)


def _apply_config_setting(cfg, setting, value, guild_id):
    """Apply a single verification config change in place.

    Returns (changed: bool, error: str | None).
    Shared by the prefix and slash commands.
    """
    setting = setting.strip().lower()

    if setting == "verified_role_id":
        try:
            role_id = int(str(value).strip("<@&>"))
        except ValueError:
            return False, "❌ Invalid role ID. Use a Discord role ID number."
        cfg["verified_role_id"] = str(role_id)
        cfg["guild_id"] = str(guild_id)
        return True, None

    elif setting == "unverified_role_id":
        try:
            role_id = int(str(value).strip("<@&>"))
        except ValueError:
            return False, "❌ Invalid role ID."
        cfg["unverified_role_id"] = str(role_id)
        return True, None

    elif setting in ("welcome_channel_id", "log_channel_id"):
        try:
            channel_id = int(str(value).strip("<#>"))
        except ValueError:
            return False, "❌ Invalid channel ID."
        cfg[setting] = str(channel_id)
        return True, None

    elif setting == "min_account_age_days":
        try:
            days = int(value)
            if days < 0:
                raise ValueError
        except ValueError:
            return False, "❌ Invalid number. Use a positive integer (0 to disable)."
        cfg[setting] = days
        return True, None

    elif setting == "captcha_enabled":
        val = str(value).strip().lower()
        if val in ("on", "enable", "true", "1"):
            cfg[setting] = True
            return True, None
        if val in ("off", "disable", "false", "0"):
            cfg[setting] = False
            return True, None
        return False, "❌ Use `on` or `off`."

    elif setting == "captcha_difficulty":
        val = str(value).strip().lower()
        if val in ("easy", "medium", "hard"):
            cfg[setting] = val
            return True, None
        return False, "❌ Difficulty must be `easy`, `medium`, or `hard`."

    elif setting == "welcome_message":
        if not value:
            return False, "❌ Please provide a welcome message."
        cfg[setting] = str(value)
        return True, None

    elif setting == "guild_id":
        try:
            gid = int(str(value).strip())
        except ValueError:
            return False, "❌ Invalid guild ID."
        cfg[setting] = str(gid)
        return True, None

    return False, (
        f"❌ Unknown setting: `{setting}`\n"
        f"Valid settings: {', '.join(VALID_CONFIG_SETTINGS)}"
    )


# ── Captcha Generator ─────────────────────────────────


class CaptchaGenerator:
    """Generates real image captchas (distorted text) for human verification.

    Falls back to a simple math challenge if Pillow isn't installed.
    """

    # Unambiguous characters (no 0/O, 1/l/I) so users aren't confused
    CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"

    # Operators and their display symbols (fallback math captcha)
    OPERATORS = [
        ("+", lambda a, b: a + b),
        ("-", lambda a, b: a - b),
        ("×", lambda a, b: a * b),
    ]

    _FONT_CANDIDATES = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/Library/Fonts/Arial.ttf",
    ]

    @classmethod
    def _load_font(cls, size):
        """Load a TTF font for rendering, falling back to Pillow's default."""
        for path in cls._FONT_CANDIDATES:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    @classmethod
    def _random_text(cls, length):
        """Generate a random captcha code avoiding ambiguous characters."""
        return "".join(random.choice(cls.CHARSET) for _ in range(length))

    @classmethod
    def generate_image(cls, difficulty="medium"):
        """Generate a captcha image.

        Returns (answer_text, png_bytes). If Pillow is unavailable,
        png_bytes is None and the caller should fall back to math.
        """
        if not _PIL_AVAILABLE:
            return None, None

        if difficulty == "easy":
            length, rot_range, noise = 4, 12, 0.35
        elif difficulty == "hard":
            length, rot_range, noise = 6, 28, 0.75
        else:
            length, rot_range, noise = 5, 20, 0.5

        text = cls._random_text(length)
        font = cls._load_font(46)
        font_size = 46

        pad = 22
        step = font_size - 2
        w = pad * 2 + length * step + 30
        h = font_size * 2 + 24
        img = Image.new("RGB", (w, h))
        draw = ImageDraw.Draw(img)

        # Light background with a soft vertical gradient
        bg_base = random.choice(
            [(238, 240, 244), (246, 246, 250), (234, 240, 248), (250, 244, 238)]
        )
        for y in range(h):
            shade = int(16 * (y / h))
            draw.line([(0, y), (w, y)], fill=tuple(max(0, c - shade) for c in bg_base))

        # Noise specks
        speck_colors = [(90, 90, 100), (120, 120, 130), (150, 150, 160)]
        for _ in range(int(w * h * (0.008 + noise * 0.02))):
            draw.point(
                (random.randint(0, w - 1), random.randint(0, h - 1)),
                fill=random.choice(speck_colors),
            )

        # Wavy lines across the image
        n_lines = 2 if noise < 0.45 else 3 if noise < 0.6 else 4
        for _ in range(n_lines):
            pts = []
            x = 0
            while x <= w:
                pts.append((x, random.randint(int(h * 0.25), int(h * 0.75))))
                x += random.randint(30, 55)
            draw.line(
                pts,
                fill=random.choice([(110, 110, 120), (140, 140, 150)]),
                width=1,
            )

        # Per-character drawing with random rotation / vertical jitter
        text_colors = [
            (25, 25, 30), (35, 70, 150), (150, 55, 35),
            (25, 110, 80), (110, 35, 120), (95, 60, 25),
        ]
        x = pad
        for ch in text:
            layer = Image.new("RGBA", (font_size + 30, font_size + 44), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            ld.text((15, 22), ch, font=font, fill=(*random.choice(text_colors), 255))
            layer = layer.rotate(
                random.uniform(-rot_range, rot_range),
                expand=True,
                resample=Image.BICUBIC,
            )
            y_off = random.randint(-12, 12)
            img.paste(layer, (x, (h - layer.height) // 2 + y_off), layer)
            x += step + random.randint(-2, 8)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return text, buf.getvalue()

    @classmethod
    def generate(cls, difficulty="medium"):
        """Fallback: generate a simple math challenge.
        Returns (question_text, correct_answer).
        """
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


# ── Captcha Answer View ───────────────────────────────


class CaptchaAnswerView(discord.ui.View):
    """View shown next to the captcha image; opens the answer modal."""

    def __init__(self, cog, user_id):
        super().__init__(timeout=300)
        self.cog = cog
        self._user_id = user_id

    @discord.ui.button(
        label="Enter Answer",
        style=discord.ButtonStyle.primary,
        custom_id="captcha_answer_btn",
        emoji="🔑",
    )
    async def answer_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open the modal where the user types the code from the image."""
        if interaction.user.id != self._user_id:
            await interaction.response.send_message(
                "❌ This captcha is not for you.", ephemeral=True
            )
            return

        captcha_data = self.cog._active_captchas.get(self._user_id)
        if not captcha_data or (time.time() - captcha_data.get("timestamp", 0)) > 300:
            await interaction.response.send_message(
                "❌ This captcha has expired. Click **Verify** to get a new one.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(CaptchaModal(self.cog, None, self._user_id))


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

            # Generate captcha and store the answer
            difficulty = config.get("captcha_difficulty", "medium")
            answer, image_bytes = CaptchaGenerator.generate_image(difficulty)

            if image_bytes:
                # Real image captcha: send the picture + an answer button
                self.cog._active_captchas[interaction.user.id] = {
                    "answer": answer.lower(),
                    "timestamp": time.time(),
                }

                file = discord.File(io.BytesIO(image_bytes), filename="captcha.png")
                embed = discord.Embed(
                    title="🔐 Human Verification",
                    description=(
                        "Please **read the code** in the image below, then click "
                        "**Enter Answer** to type it in.\n\n"
                        f"Difficulty: `{difficulty}`"
                    ),
                    color=0x00FF88,
                )
                embed.set_image(url="attachment://captcha.png")

                view = CaptchaAnswerView(self.cog, interaction.user.id)
                await interaction.response.send_message(
                    embed=embed, file=file, view=view, ephemeral=True
                )
            else:
                # Pillow unavailable – fall back to a math question
                question, answer = CaptchaGenerator.generate(difficulty)

                self.cog._active_captchas[interaction.user.id] = {
                    "answer": str(answer).lower(),
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
    """Modal that expects the captcha answer typed by the user."""

    def __init__(self, cog, question, user_id):
        self.cog = cog
        self._user_id = user_id
        super().__init__(title="🔐 Human Verification")

        if question:
            # Fallback math captcha – question is shown in the input label
            label = f"Solve: {question}"
            placeholder = "Type your answer..."
        else:
            # Image captcha – the code was shown in a picture
            label = "Enter the code shown in the image"
            placeholder = "e.g. K7xQ2"

        self.captcha_input = discord.ui.TextInput(
            label=label,
            placeholder=placeholder,
            style=discord.TextStyle.short,
            required=True,
            max_length=12,
        )
        self.add_item(self.captcha_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Check the captcha answer (case-insensitive)."""
        captcha_data = self.cog._active_captchas.pop(self._user_id, None)
        if not captcha_data:
            await interaction.response.send_message(
                "❌ Captcha expired. Please click the Verify button again.",
                ephemeral=True
            )
            return

        user_answer = self.captcha_input.value.strip().lower()
        if not user_answer:
            await interaction.response.send_message(
                "❌ Please enter the code shown in the image.",
                ephemeral=True
            )
            return

        correct_answer = str(captcha_data.get("answer", "")).lower()

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
                "❌ **Incorrect code.** Please click **Verify** to get a new captcha.",
                ephemeral=True
            )


# ── Verification Cog ───────────────────────────────────


class Verification(commands.Cog):
    """Complete verification system: account age check, captcha, button verification."""

    def __init__(self, bot):
        self.bot = bot
        self._active_captchas = {}  # user_id -> {"answer": str, "timestamp": float}
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
    @commands.check(staff_required)
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
    @commands.check(staff_required)
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
        cfg = _load_verification_config()
        changed, error = _apply_config_setting(cfg, setting, value, ctx.guild.id)

        if error:
            await ctx.send(error)
            return

        if changed:
            _save_verification_config(cfg)
            await ctx.send(f"✅ `{setting.strip().lower()}` has been updated.")
            _log.info(f"Verification config updated: {setting} = {value}")
        else:
            await ctx.send("⚠️ No changes were made.")

    # ── Slash Commands ────────────────────────────────

    @app_commands.command(name="verifypanel", description="Post the verification panel with the Verify button")
    @app_commands.check(slash_staff_required)
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
    @app_commands.describe(
        setting="What to configure — leave as 'View config' to just show current settings",
        role="Role to use (for verified / unverified role)",
        channel="Channel to use (for welcome / log channel)",
        enabled="Turn the captcha on or off",
        difficulty="Captcha difficulty level",
        days="Minimum account age in days (0 to disable)",
        message="Custom welcome message",
    )
    @app_commands.choices(setting=[
        app_commands.Choice(name="View current config", value="view"),
        app_commands.Choice(name="Verified role", value="verified_role_id"),
        app_commands.Choice(name="Unverified role", value="unverified_role_id"),
        app_commands.Choice(name="Welcome channel", value="welcome_channel_id"),
        app_commands.Choice(name="Log channel", value="log_channel_id"),
        app_commands.Choice(name="Minimum account age (days)", value="min_account_age_days"),
        app_commands.Choice(name="Enable/disable captcha", value="captcha_enabled"),
        app_commands.Choice(name="Captcha difficulty", value="captcha_difficulty"),
        app_commands.Choice(name="Welcome message", value="welcome_message"),
    ])
    @app_commands.choices(enabled=[
        app_commands.Choice(name="On", value="on"),
        app_commands.Choice(name="Off", value="off"),
    ])
    @app_commands.choices(difficulty=[
        app_commands.Choice(name="Easy", value="easy"),
        app_commands.Choice(name="Medium", value="medium"),
        app_commands.Choice(name="Hard", value="hard"),
    ])
    @app_commands.check(slash_staff_required)
    async def slash_verifyconfig(self, interaction: discord.Interaction, setting: str = "view", role: discord.Role = None, channel: discord.TextChannel = None, enabled: str = None, difficulty: str = None, days: int = None, message: str = None):
        """View or update verification config — pick settings from dropdowns, no typing needed.

        Examples:
          /verifyconfig setting:Verified role role:<pick a role>
          /verifyconfig setting:Enable/disable captcha enabled:Off
          /verifyconfig setting:Captcha difficulty difficulty:Hard
        """
        cfg = _load_verification_config()

        if setting == "view":
            # Show the current config
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

            welcome_msg = cfg.get('welcome_message', '')
            if welcome_msg:
                embed.add_field(name="Welcome Message", value=f"```{welcome_msg[:100]}```", inline=False)

            embed.set_footer(text="Use /verifyconfig setting:<dropdown> to change")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Resolve the typed value for the selected setting
        if setting == "verified_role_id":
            value = str(role.id) if role else None
        elif setting == "unverified_role_id":
            value = str(role.id) if role else None
        elif setting in ("welcome_channel_id", "log_channel_id"):
            value = str(channel.id) if channel else None
        elif setting == "min_account_age_days":
            value = str(days) if days is not None else None
        elif setting == "captcha_enabled":
            value = enabled
        elif setting == "captcha_difficulty":
            value = difficulty
        elif setting == "welcome_message":
            value = message
        else:
            value = None

        if not value or not str(value).strip():
            await interaction.response.send_message(
                f"❌ Missing the value for **{setting}** — fill in the matching option for this setting.",
                ephemeral=True,
            )
            return

        # Defer first: _save_verification_config does a GitHub write that can
        # exceed Discord's 3-second interaction window.
        await interaction.response.defer(ephemeral=True)
        changed, error = _apply_config_setting(cfg, setting, value, interaction.guild_id)

        if error:
            await interaction.followup.send(error, ephemeral=True)
            return

        if changed:
            _save_verification_config(cfg)
            _log.info(f"Verification config updated via slash: {setting} = {value}")
            await interaction.followup.send(
                f"✅ `{setting}` has been updated.", ephemeral=True
            )
        else:
            await interaction.followup.send("⚠️ No changes were made.", ephemeral=True)


# ── Setup ──────────────────────────────────────────────


async def setup(bot):
    """Add the Verification cog to the bot and register the persistent view."""
    cog = Verification(bot)
    await bot.add_cog(cog)
    # Register the persistent verification button view so it survives bot restarts
    bot.add_view(VerificationView(cog))
