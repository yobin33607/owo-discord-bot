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
Limey - https://github.com/cubiced0/owo-discord-bot
"""




from flask import Flask, render_template, jsonify, request, session, redirect, url_for, g
from werkzeug.exceptions import HTTPException
from functools import wraps
import threading
import time
import json
import logging
import os
import secrets
import core.state as state
import utils.utils as utils
import asyncio
from datetime import datetime, timedelta


import socket
import sys
import urllib.parse
import requests
import re

# ── Appeals System ───────────────────────────────────────

from utils.github_data_store import ghd

import discord


def _load_appeals_data():
    """Load appeals from GitHub data repo."""
    data = ghd.read_json("config/appeals.json", default=None)
    if data is not None:
        return data
    return {"appeals": [], "next_id": 1}


def _save_appeals_data(data):
    """Save appeals to GitHub data repo."""
    ghd.write_json("config/appeals.json", data)

_original_getaddrinfo = socket.getaddrinfo

def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host == 'owobot.com':
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('104.21.35.189', port))]
    return _original_getaddrinfo(host, port, family, type, proto, flags)

socket.getaddrinfo = patched_getaddrinfo

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
try:
    app.json.sort_keys = False
except AttributeError:
    pass

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


@app.route('/health')
def health():
    """Health check endpoint — used by uptime monitors / platform probes.

    No auth required on purpose: it only reports process-level liveness and a
    count of ready bots, never sensitive data.
    """
    ready = sum(1 for bot in state.bot_instances if getattr(bot, 'is_ready', False))
    return jsonify({
        'status': 'ok',
        'uptime_seconds': int(time.time() - state.active_session_start),
        'bots_total': len(state.bot_instances),
        'bots_ready': ready,
        'timestamp': time.time(),
    })


LOGIN_ATTEMPTS = {}
BLOCK_DURATION = 300  
MAX_ATTEMPTS = 5

ROLE_HIERARCHY = {'view': 10, 'manage': 20, 'admin': 30}

# ── API Key Authentication ────────────────────────────
from utils import api_keys as api_key_manager

def _get_api_key_from_request():
    """Extract an API key from request headers/params."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        return api_key
    return request.args.get("api_key", "")


def load_auth_config():
    try:
        cfg = ghd.read_json("config/auth.json", default=None)
        if cfg is None:
            return None
        
        # Migrate old single-user schema to new multi-user schema
        changed = False
        if 'users' not in cfg:
            old_user = cfg.get('username', 'admin')
            old_pass = cfg.get('password', '')
            cfg['users'] = [{'username': old_user, 'password': old_pass, 'role': 'admin'}]
            cfg.pop('username', None)
            cfg.pop('password', None)
            changed = True
        
        if cfg.get('secret_key', '').startswith("generate_a_random_long_secret_key_here_please") or not cfg.get('secret_key'):
            cfg['secret_key'] = secrets.token_hex(32)
            changed = True
        
        if changed:
            ghd.write_json("config/auth.json", cfg)
            
        return cfg
    except:
        pass
    return None

auth_cfg = load_auth_config()
if auth_cfg:
    app.secret_key = auth_cfg.get('secret_key', 'limey_fallback_secret')
else:
    app.secret_key = 'temporary_secret_key'

def _authenticate_request():
    """Try to authenticate using an API key from the request.
    Uses Flask's request-local `g` object instead of session
    to avoid leaking persistent cookies on API key requests.
    Returns True if authenticated via API key, False otherwise."""
    api_key = _get_api_key_from_request()
    if not api_key:
        # Also check for internal key (used by manager bot subprocess)
        internal_key = os.environ.get("LIMEY_INTERNAL_KEY")
        if internal_key:
            header_key = request.headers.get("X-Internal-Key", "")
            if header_key == internal_key:
                g.api_key_auth = True
                g.api_key_role = "admin"
                g.api_key_user = "manager_bot"
                return True
        return False
    key_info = api_key_manager.validate_key(api_key)
    if key_info:
        g.api_key_auth = True
        g.api_key_role = key_info['role']
        g.api_key_user = key_info['label']
        return True
    return False

def _get_effective_role():
    """Get the effective user role, checking API key auth first."""
    if getattr(g, 'api_key_auth', False):
        return getattr(g, 'api_key_role', 'view')
    return session.get('role', 'view')

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check API key first (no session cookie leak)
        if _authenticate_request():
            return f(*args, **kwargs)
        # Fall back to session
        if 'logged_in' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def require_permission(min_role='manage'):
    """Decorator requiring the user to have at least the specified role.
    Hierarchy: view < manage < admin
    Supports both session-based auth and API key auth."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Check API key first (no session cookie leak)
            if _authenticate_request():
                user_role = _get_effective_role()
                if ROLE_HIERARCHY.get(user_role, 0) < ROLE_HIERARCHY.get(min_role, 20):
                    return jsonify({'success': False, 'error': 'Insufficient permissions', 'role': user_role, 'required': min_role}), 403
                return f(*args, **kwargs)
            # Fall back to session
            if 'logged_in' not in session:
                if request.is_json or request.path.startswith('/api/'):
                    return jsonify({'success': False, 'error': 'Authentication required'}), 401
                return redirect(url_for('login'))
            user_role = session.get('role', 'view')
            if ROLE_HIERARCHY.get(user_role, 0) < ROLE_HIERARCHY.get(min_role, 20):
                return jsonify({'success': False, 'error': 'Insufficient permissions', 'role': user_role, 'required': min_role}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def check_rate_limit(ip):
    now = time.time()
    if ip in LOGIN_ATTEMPTS:
        attempts, block_time = LOGIN_ATTEMPTS[ip]
        if block_time > now:
            return False, int(block_time - now)
        if now - block_time > BLOCK_DURATION: 
             LOGIN_ATTEMPTS[ip] = [0, 0]
    return True, 0

def fail_login(ip):
    now = time.time()
    if ip not in LOGIN_ATTEMPTS:
        LOGIN_ATTEMPTS[ip] = [1, 0]
    else:
        attempts, block_time = LOGIN_ATTEMPTS[ip]
        attempts += 1
        if attempts >= MAX_ATTEMPTS:
            block_time = now + BLOCK_DURATION
        LOGIN_ATTEMPTS[ip] = [attempts, block_time]

def protect_large_ints(obj):
    if isinstance(obj, dict):
        return {k: protect_large_ints(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [protect_large_ints(v) for v in obj]
    elif isinstance(obj, int) and (obj > 9007199254740991 or obj < -9007199254740991):
        return str(obj)
    return obj


# ── Discord Account Linking ────────────────────────────


def load_discord_links():
    """Load Discord account links from GitHub data repo."""
    data = ghd.read_json("config/discord_links.json", default=None)
    if data is not None:
        return data
    return {"links": []}


def save_discord_links(data):
    """Save Discord account links to GitHub data repo."""
    ghd.write_json("config/discord_links.json", data)


def get_discord_oauth_config():
    """Load Discord OAuth config from settings.json via GitHub."""
    try:
        cfg = ghd.read_json("config/settings.json", default={})
        if cfg:
            return cfg.get('discord_oauth', {})
        return {}
    except:
        return {}


@app.route('/api/auth/discord/login')
def discord_login():
    """Initiate Discord OAuth login flow."""
    oauth_cfg = get_discord_oauth_config()
    client_id = oauth_cfg.get('client_id', '')
    if not client_id:
        return jsonify({'success': False, 'error': 'Discord OAuth not configured'}), 400

    redirect_uri = oauth_cfg.get('redirect_uri', 'http://localhost:8000/api/auth/discord/callback')
    state_token = secrets.token_urlsafe(32)
    session['discord_oauth_state'] = state_token
    session['discord_oauth_action'] = 'login'

    authorize_url = (
        "https://discord.com/api/v10/oauth2/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=identify"
        f"&state={state_token}"
    )
    return redirect(authorize_url)


@app.route('/api/auth/discord/callback')
def discord_callback():
    """Handle Discord OAuth callback."""
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')

    def _redirect_with_msg(msg_type, msg):
        return redirect(f"/login?{msg_type}={urllib.parse.quote(msg)}")

    if error:
        return _redirect_with_msg('oauth_error', f'Discord login cancelled or failed: {error}')

    if not code or not state:
        return _redirect_with_msg('oauth_error', 'Invalid OAuth response')

    # Verify state token
    saved_state = session.pop('discord_oauth_state', None)
    if not saved_state or saved_state != state:
        return _redirect_with_msg('oauth_error', 'State mismatch — try again')

    action = session.pop('discord_oauth_action', 'login')

    oauth_cfg = get_discord_oauth_config()
    client_id = oauth_cfg.get('client_id', '')
    client_secret = oauth_cfg.get('client_secret', '')
    redirect_uri = oauth_cfg.get('redirect_uri', 'http://localhost:8000/api/auth/discord/callback')

    if not client_id or not client_secret:
        return _redirect_with_msg('oauth_error', 'Discord OAuth not configured')

    # Exchange code for token
    token_data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
    }
    token_headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    try:
        token_resp = requests.post(
            'https://discord.com/api/v10/oauth2/token',
            data=token_data,
            headers=token_headers,
            timeout=10
        )
        if token_resp.status_code != 200:
            return _redirect_with_msg('oauth_error', 'Failed to get Discord token')

        token_json = token_resp.json()
        access_token = token_json.get('access_token')

        # Get user info
        user_resp = requests.get(
            'https://discord.com/api/v10/users/@me',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )
        if user_resp.status_code != 200:
            return _redirect_with_msg('oauth_error', 'Failed to get Discord user info')

        discord_user = user_resp.json()
        discord_id = discord_user['id']
        discord_username = discord_user.get('username', 'Unknown')
        discord_avatar_hash = discord_user.get('avatar', '')
        discord_avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{discord_avatar_hash}.png" if discord_avatar_hash else None

        links = load_discord_links()

        if action == 'login':
            # Find the dashboard user linked to this Discord account
            for link in links.get('links', []):
                if link['discord_id'] == discord_id:
                    dashboard_user = link['dashboard_username']
                    auth_cfg = load_auth_config()
                    if auth_cfg:
                        for user in auth_cfg.get('users', []):
                            if user['username'] == dashboard_user:
                                session['logged_in'] = True
                                session['username'] = dashboard_user
                                session['role'] = user.get('role', 'view')
                                session.permanent = True
                                return redirect(url_for('dashboard'))

            return _redirect_with_msg('oauth_error', f'Discord account {discord_username} is not linked to any dashboard user. Link it in My Account settings first.')

        elif action == 'link':
            # Linking flow — user is already logged into dashboard
            if 'logged_in' not in session:
                return redirect(url_for('login'))

            dashboard_username = session.get('username')

            # Check if this Discord ID is already linked to another user
            for link in links.get('links', []):
                if link['discord_id'] == discord_id and link['dashboard_username'] != dashboard_username:
                    return _redirect_with_msg('oauth_error', f'Discord account {discord_username} is already linked to another user. Unlink it first.')

            # Remove existing link for this dashboard user (if any)
            links['links'] = [l for l in links.get('links', []) if l['dashboard_username'] != dashboard_username]

            # Add new link
            links['links'].append({
                'discord_id': discord_id,
                'discord_username': discord_username,
                'discord_avatar': discord_avatar_url,
                'dashboard_username': dashboard_username,
                'linked_at': time.time()
            })
            save_discord_links(links)

            return redirect(url_for('dashboard'))

    except Exception:
        return _redirect_with_msg('oauth_error', 'OAuth error encountered. Please try again.')

    return _redirect_with_msg('oauth_error', 'Unknown error')


@app.route('/api/auth/discord/link')
@login_required
def discord_link():
    """Initiate Discord OAuth linking flow."""
    oauth_cfg = get_discord_oauth_config()
    client_id = oauth_cfg.get('client_id', '')
    if not client_id:
        return jsonify({'success': False, 'error': 'Discord OAuth not configured'}), 400

    redirect_uri = oauth_cfg.get('redirect_uri', 'http://localhost:8000/api/auth/discord/callback')
    state_token = secrets.token_urlsafe(32)
    session['discord_oauth_state'] = state_token
    session['discord_oauth_action'] = 'link'

    authorize_url = (
        "https://discord.com/api/v10/oauth2/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=identify"
        f"&state={state_token}"
    )
    return redirect(authorize_url)


@app.route('/api/auth/discord/status')
def discord_status():
    """Get linked Discord account info for the current user."""
    if 'logged_in' not in session:
        return jsonify({'linked': False})

    dashboard_username = session.get('username')
    links = load_discord_links()

    for link in links.get('links', []):
        if link['dashboard_username'] == dashboard_username:
            return jsonify({
                'linked': True,
                'discord_username': link.get('discord_username'),
                'discord_avatar': link.get('discord_avatar'),
                'discord_id': link.get('discord_id'),
                'linked_at': link.get('linked_at')
            })

    return jsonify({'linked': False})


@app.route('/api/auth/discord/unlink', methods=['POST'])
@login_required
def discord_unlink():
    """Unlink Discord account from current user."""
    dashboard_username = session.get('username')
    links = load_discord_links()
    links['links'] = [l for l in links.get('links', []) if l['dashboard_username'] != dashboard_username]
    save_discord_links(links)
    return jsonify({'success': True, 'message': 'Discord account unlinked'})


@app.route('/api/auth/change-password', methods=['POST'])
@login_required
def change_password():
    """Change password for the current user."""
    data = request.json or {}
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')

    if not current_password or not new_password:
        return jsonify({'success': False, 'error': 'Current and new password required'}), 400

    if len(new_password) < 4:
        return jsonify({'success': False, 'error': 'New password must be at least 4 characters'}), 400

    username = session.get('username')
    auth_cfg = load_auth_config()
    if not auth_cfg:
        return jsonify({'success': False, 'error': 'Auth config missing'}), 500

    users = auth_cfg.get('users', [])
    found = False
    for user in users:
        if user['username'] == username:
            if user['password'] != current_password:
                return jsonify({'success': False, 'error': 'Current password is incorrect'}), 403
            user['password'] = new_password
            found = True
            break

    if not found:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    auth_cfg['users'] = users
    ghd.write_json("config/auth.json", auth_cfg)

    return jsonify({'success': True, 'message': 'Password changed successfully'})


# ── API Key Management ──────────────────────────────────

@app.route('/api/api-keys', methods=['GET', 'POST'])
@require_permission('admin')
def manage_api_keys():
    """List or create API keys."""
    if request.method == 'POST':
        data = request.json or {}
        label = data.get('label', '').strip()
        role = data.get('role', 'view').strip()

        if role not in ('view', 'manage', 'admin'):
            return jsonify({'success': False, 'error': 'Invalid role. Must be view, manage, or admin'}), 400

        username = session.get('username', 'admin')
        new_key = api_key_manager.generate_key(label=label, role=role, created_by=username)

        return jsonify({
            'success': True,
            'key': new_key,  # Full key only shown once on creation
            'message': f'API key "{new_key["label"]}" created. Save it now — it won\'t be shown again.'
        })

    # GET: list all keys (without exposing full keys)
    include_revoked = request.args.get('include_revoked', '').lower() == 'true'
    keys = api_key_manager.list_keys(include_revoked=include_revoked)
    return jsonify({'success': True, 'keys': keys})


@app.route('/api/api-keys/<key_id>/revoke', methods=['POST'])
@require_permission('admin')
def revoke_api_key(key_id):
    """Revoke an API key."""
    if api_key_manager.revoke_key(key_id):
        return jsonify({'success': True, 'message': 'API key revoked'})
    return jsonify({'success': False, 'error': 'API key not found'}), 404


@app.route('/api/api-keys/<key_id>', methods=['DELETE'])
@require_permission('admin')
def delete_api_key(key_id):
    """Permanently delete an API key."""
    if api_key_manager.delete_key(key_id):
        return jsonify({'success': True, 'message': 'API key deleted'})
    return jsonify({'success': False, 'error': 'API key not found'}), 404


@app.route('/api/api-keys/verify', methods=['GET', 'POST'])
def verify_api_key():
    """Verify an API key and return its role.
    This endpoint is publicly accessible (no login required)
    because it's used for key validation.
    """
    api_key = _get_api_key_from_request()
    if not api_key:
        # Also allow passing key in body for POST
        if request.method == 'POST':
            api_key = (request.json or {}).get('api_key', '')

    if not api_key:
        return jsonify({'success': False, 'error': 'No API key provided'}), 400

    key_info = api_key_manager.validate_key(api_key)
    if key_info:
        return jsonify({
            'success': True,
            'valid': True,
            'label': key_info['label'],
            'role': key_info['role'],
        })
    return jsonify({'success': False, 'valid': False, 'error': 'Invalid or revoked API key'}), 401

# ── Moderation Data Helpers ──────────────────────────

def _load_mod_data():
    """Load moderation data from GitHub data repo."""
    data = ghd.read_json("config/moderation.json", default=None)
    if data is not None:
        return data
    return {"violations": {}, "warnings": {}, "mod_log": {}, "mutes": {}, "next_violation_id": 1, "next_warn_id": 1}


def _save_mod_data(data):
    """Save moderation data to GitHub data repo."""
    ghd.write_json("config/moderation.json", data, message="Update moderation data")


def _get_mod_config():
    """Get moderation config from settings.json."""
    cfg = ghd.read_json("config/settings.json", default={})
    return cfg.get("manager_bot", {}).get("moderation", {})


# ── Standalone Data Helpers for Dashboard Actions ─────

def _store_violation_data(user_id, guild_id, vtype, reason, moderator, duration=None):
    """Store a violation record (standalone, no cog needed)."""
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

    data["violations"][guild_key][user_key].append({
        "id": vid,
        "type": vtype,
        "reason": reason or "No reason provided",
        "moderator": str(moderator),
        "duration": duration,
        "timestamp": time.time(),
    })

    if len(data["violations"][guild_key][user_key]) > 50:
        data["violations"][guild_key][user_key] = data["violations"][guild_key][user_key][-50:]

    _save_mod_data(data)
    return vid


def _store_mod_action_data(guild_id, action_type, target, moderator, reason=None):
    """Store a mod action log entry (standalone, no cog needed)."""
    data = _load_mod_data()
    guild_key = str(guild_id)
    if "mod_log" not in data:
        data["mod_log"] = {}
    if guild_key not in data["mod_log"]:
        data["mod_log"][guild_key] = []

    data["mod_log"][guild_key].append({
        "type": action_type,
        "target": str(target),
        "moderator": str(moderator),
        "reason": reason or "No reason provided",
        "timestamp": time.time(),
    })

    if len(data["mod_log"][guild_key]) > 500:
        data["mod_log"][guild_key] = data["mod_log"][guild_key][-500:]

    _save_mod_data(data)


import re as _re

def _parse_duration_seconds(text):
    """Parse a duration string like '1h', '30m', '7d' into seconds."""
    text = text.strip().lower()
    total = 0
    parts = _re.findall(r'(\d+)\s*(s|sec|seconds?|m|min|minutes?|h|hr|hours?|d|days?)', text)
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


def _format_duration_seconds(seconds):
    """Format seconds into a human-readable duration string."""
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


async def _discord_mod_action(bot, action, guild_id, user_id, reason, duration_seconds=None, delete_days=0):
    """Perform only the Discord API part of a moderation action on the bot's event loop.
    Returns nothing on success, raises on failure."""
    guild = bot.get_guild(int(guild_id))
    if not guild:
        try:
            guild = await bot.fetch_guild(int(guild_id))
        except Exception as e:
            raise ValueError(f"Guild not found: {e}")

    if action not in ('ban', 'unban'):
        member = guild.get_member(int(user_id))
        if not member:
            try:
                member = await guild.fetch_member(int(user_id))
            except Exception as e:
                raise ValueError(f"Member not found in guild: {e}")

    if action == 'kick':
        await member.kick(reason=reason)
    elif action == 'ban':
        await guild.ban(
            discord.Object(id=int(user_id)),
            reason=reason,
            delete_message_days=max(0, min(delete_days, 7))
        )
    elif action == 'unban':
        async for ban_entry in guild.bans():
            if ban_entry.user.id == int(user_id):
                await guild.unban(ban_entry.user, reason=reason)
                return
        raise ValueError("User is not banned in this guild")
    elif action == 'timeout':
        seconds = duration_seconds or 600
        if seconds < 10:
            raise ValueError("Duration too short (minimum 10 seconds)")
        if seconds > 2419200:
            raise ValueError("Duration cannot exceed 28 days")
        until = discord.utils.utcnow() + discord.timedelta(seconds=seconds)
        await member.timeout(until, reason=reason)
    elif action == 'mute':
        seconds = duration_seconds or 3600
        until = discord.utils.utcnow() + discord.timedelta(seconds=seconds)
        await member.timeout(until, reason=reason)
    elif action == 'unmute':
        await member.timeout(None, reason=reason)


# ── Violations API ──────────────────────────────────────


def _load_violations_for_user(user_id):
    """Load all violations for a user across all guilds from moderation.json."""
    data = ghd.read_json("config/moderation.json", default={})

    user_key = str(user_id)
    violations = data.get('violations', {})
    results = []
    for guild_key, guild_violations in violations.items():
        if user_key in guild_violations:
            for v in guild_violations[user_key]:
                v['guild_id'] = guild_key
                results.append(v)
    results.sort(key=lambda v: v.get('timestamp', 0), reverse=True)
    return results[:50]


@app.route('/api/violations/<user_id>')
@require_permission('manage')
def api_violations(user_id):
    """Get violations for a specific Discord user ID."""
    violations = _load_violations_for_user(user_id)
    return jsonify({'success': True, 'violations': violations, 'total': len(violations)})


# ── Appeals API Routes ──────────────────────────────────

@app.route('/api/appeals')
@require_permission('manage')
def api_appeals_list():
    """List appeals. Optional query params: status (pending|approved|rejected|all)"""
    status_filter = request.args.get('status', 'pending')
    limit = request.args.get('limit', 50, type=int)

    data = _load_appeals_data()
    appeals = data.get('appeals', [])

    if status_filter != 'all':
        appeals = [a for a in appeals if a.get('status') == status_filter]

    appeals.sort(key=lambda a: a.get('created_at', 0), reverse=True)
    appeals = appeals[:limit]

    return jsonify({'success': True, 'appeals': appeals, 'total': len(appeals)})


@app.route('/api/appeals/<int:appeal_id>')
@require_permission('manage')
def api_appeals_detail(appeal_id):
    """Get a single appeal's full details."""
    data = _load_appeals_data()
    for a in data.get('appeals', []):
        if a.get('id') == appeal_id:
            return jsonify({'success': True, 'appeal': a})
    return jsonify({'success': False, 'error': 'Appeal not found'}), 404


@app.route('/api/appeals/<int:appeal_id>/review', methods=['POST'])
@require_permission('manage')
def api_appeals_review(appeal_id):
    """Review (approve/reject) an appeal."""
    payload = request.json or {}
    action = payload.get('action', '').strip().lower()
    notes = payload.get('notes', '').strip()

    if action not in ('approve', 'reject'):
        return jsonify({'success': False, 'error': 'Action must be "approve" or "reject"'}), 400

    data = _load_appeals_data()
    target = None
    for a in data.get('appeals', []):
        if a.get('id') == appeal_id:
            target = a
            break

    if not target:
        return jsonify({'success': False, 'error': f'Appeal #{appeal_id} not found'}), 404

    if target.get('status') != 'pending':
        return jsonify({'success': False, 'error': f'Appeal #{appeal_id} is already {target["status"]}'}), 400

    new_status = 'approved' if action == 'approve' else 'rejected'
    reviewer = session.get('username', 'Unknown')
    if getattr(g, 'api_key_auth', False):
        reviewer = getattr(g, 'api_key_user', 'API')

    target['status'] = new_status
    target['reviewed_by'] = reviewer
    target['review_notes'] = notes or f'{action.capitalize()} by {reviewer}'
    target['reviewed_at'] = time.time()

    _save_appeals_data(data)

    return jsonify({
        'success': True,
        'message': f'Appeal #{appeal_id} has been {new_status}.',
        'appeal': target
    })


@app.errorhandler(404)
def not_found(e):
    """Render the custom 404 error page."""
    path = request.path.strip('/')
    first_segment = path.split('/')[0] if path else 'unknown'
    return render_template('404.html', first_segment=first_segment), 404


@app.errorhandler(Exception)
def handle_exception(e):
    """Global handler: API routes must always return JSON, never an HTML error page.
    The dashboard JS parses every /api response as JSON — an HTML 500/502/503 page
    from here caused the 'Unexpected token <' / 'Unexpected end of JSON input'
    crashes in the console. For /api/ paths we return a JSON error; other paths
    get a plain 500 (same as Flask's default). HTTPExceptions (405, 403, etc.)
    pass through unchanged so their real status codes are preserved.
    """
    if isinstance(e, HTTPException):
        return e
    app.logger.exception("Unhandled exception on %s: %s", request.path, e)
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'Internal server error'}), 500
    return "Internal Server Error", 500


@app.route('/')
def home():
    if 'logged_in' in session:
        return redirect(url_for('dashboard'))
    return render_template('homepage.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        ip = request.remote_addr
        allowed, wait_time = check_rate_limit(ip)
        
        if not allowed:
             return jsonify({'success': False, 'error': f'Too many failed attempts. Try again in {wait_time}s'})

        data = request.json
        cfg = load_auth_config()
        
        if not cfg:
             return jsonify({'success': False, 'error': 'Auth config missing'})
             
        username = data.get('username', '')
        password = data.get('password', '')
        
        for user in cfg.get('users', []):
            if user.get('username') == username and user.get('password') == password:
                session['logged_in'] = True
                session['username'] = username
                session['role'] = user.get('role', 'view')
                session.permanent = True
                if ip in LOGIN_ATTEMPTS: del LOGIN_ATTEMPTS[ip]
                return jsonify({'success': True, 'role': user.get('role', 'view'), 'username': username})
        
        fail_login(ip)
        return jsonify({'success': False, 'error': 'Invalid Credentials'})
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    session.pop('role', None)
    return redirect(url_for('home'))

@app.route('/api/auth/me')
@login_required
def auth_me():
    return jsonify({
        'logged_in': True,
        'username': session.get('username', 'Unknown'),
        'role': session.get('role', 'view')
    })

@app.route('/api/auth/users', methods=['GET', 'POST'])
@login_required
def auth_users():
    cfg = load_auth_config()
    if not cfg:
        return jsonify({'success': False, 'error': 'Auth config missing'}), 500
    
    if request.method == 'POST':
        # Only admins can add/edit users
        user_role = session.get('role', 'view')
        if ROLE_HIERARCHY.get(user_role, 0) < ROLE_HIERARCHY.get('admin', 30):
            return jsonify({'success': False, 'error': 'Admin permission required'}), 403
        
        data = request.json or {}
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        role = data.get('role', 'view').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'error': 'Username and password required'}), 400
        if role not in ROLE_HIERARCHY:
            return jsonify({'success': False, 'error': 'Invalid role. Must be view, manage, or admin'}), 400
        
        users = cfg.get('users', [])
        for user in users:
            if user['username'] == username:
                user['password'] = password
                user['role'] = role
                break
        else:
            users.append({'username': username, 'password': password, 'role': role})
        
        cfg['users'] = users
        ghd.write_json("config/auth.json", cfg)
        
        return jsonify({'success': True, 'message': f'User {username} saved'})
    
    users = []
    for user in cfg.get('users', []):
        users.append({
            'username': user.get('username', ''),
            'role': user.get('role', 'view'),
            'has_password': bool(user.get('password', ''))
        })
    return jsonify({'users': users})

@app.route('/api/auth/users/<username>', methods=['DELETE'])
@login_required
def auth_users_delete(username):
    # Only admins can delete users
    user_role = session.get('role', 'view')
    if ROLE_HIERARCHY.get(user_role, 0) < ROLE_HIERARCHY.get('admin', 30):
        return jsonify({'success': False, 'error': 'Admin permission required'}), 403
    
    cfg = load_auth_config()
    if not cfg:
        return jsonify({'success': False, 'error': 'Auth config missing'}), 500
    
    users = cfg.get('users', [])
    # Don't allow deleting the last admin
    admin_count = sum(1 for u in users if u.get('role') == 'admin')
    user_to_delete = next((u for u in users if u['username'] == username), None)
    if user_to_delete and user_to_delete.get('role') == 'admin' and admin_count <= 1:
        return jsonify({'success': False, 'error': 'Cannot delete the last admin user'}), 400
    
    cfg['users'] = [u for u in users if u['username'] != username]
    ghd.write_json("config/auth.json", cfg)
    
    return jsonify({'success': True, 'message': f'User {username} deleted'})

@app.route('/api/accounts/list')
@login_required
def account_list():
    accounts = []
    for bot in state.bot_instances:
        if not bot.user or not bot.is_ready: continue
        uid = str(bot.user.id)
        st = state.account_stats.get(uid, {})
        session_total = st.get('session_hunt_count', 0) + st.get('session_battle_count', 0) + st.get('session_owo_count', 0) + st.get('session_other_count', 0)
        
        accounts.append({
            'id': uid,
            'username': bot.username,
            'avatar': str(bot.user.display_avatar.url) if bot.user.display_avatar else None,
            'paused': bot.paused,
            'cash': st.get('current_cash', 0),
            'session_total': session_total,
            'gems_used': st.get('gems_used', 0)
        })
    return jsonify(accounts)

def get_bot(account_id):
    if not account_id:
        return state.bot_instances[0] if state.bot_instances else None
    for bot in state.bot_instances:
        if bot.user and str(bot.user.id) == str(account_id):
            return bot
    return state.bot_instances[0] if state.bot_instances else None


def _run_on_bot_loop(bot, coro):
    """Schedule a coroutine on the bot's event loop from the dashboard thread.
    Returns an error message string, or None on success.
    The coroutine is closed when it can't be scheduled, so it doesn't trigger
    a 'coroutine was never awaited' RuntimeWarning."""
    loop = bot.loop_ref
    if loop is None:
        coro.close()
        return "Bot is still connecting – try again in a moment."
    try:
        asyncio.run_coroutine_threadsafe(coro, loop)
        return None
    except Exception as e:
        coro.close()
        return f"Could not reach the bot's event loop: {e}"


@app.route('/api/stats')
@login_required
def stats():
    account_id = request.args.get('id')
    bot = get_bot(account_id)
    uid = str(account_id) if account_id else (str(bot.user.id) if bot and bot.user else None)

    if not uid:
        return jsonify({})
        
    st = state.account_stats.get(uid)
    if not st:
        if bot and bot.user:
             st = state.get_empty_stats()
             st['username'] = bot.username
             state.account_stats[uid] = st
        else:
             return jsonify({})
    
    uptime_start = st.get('uptime_start', time.time())
    elapsed = time.time() - uptime_start
    session_cmds = (
        st.get('session_hunt_count', 0) + 
        st.get('session_battle_count', 0) + 
        st.get('session_owo_count', 0) + 
        st.get('session_other_count', 0)
    )
    mins = elapsed / 60
    cpm = round(session_cmds / mins, 1) if mins > 0.1 else 0
    
    cph = 0
    history = st.get('cowoncy_history', [])
    if len(history) > 1:
        first = history[0]
        last = history[-1]
        time_diff_hrs = (last[0] - first[0]) / 3600
        cash_diff = last[1] - first[1]
        if time_diff_hrs > 0.01:
            cph = round(cash_diff / time_diff_hrs)

    is_active = bot and str(bot.user.id) == uid if bot and bot.user else False
    current_status = ("PAUSED" if bot.paused else "ONLINE") if is_active else "OFFLINE"

    # ── Defensive scheduling / throttle data ───────────────────
    # cmd_states values are normally dicts, but a bad cog could store
    # anything — never let that crash the stats endpoint.
    cmd_states = {}
    if bot:
        try:
            for k, v in bot.cmd_states.items():
                if not isinstance(v, dict):
                    continue
                item = dict(v)
                content = item.get('content')
                item['content'] = '[Dynamic function]' if callable(content) else content
                cmd_states[k] = item
        except Exception:
            cmd_states = {}

    throttle_until = getattr(bot, 'throttle_until', 0) if is_active else 0
    if is_active and throttle_until == float('inf'):
        cooldown_remaining = 999999
    elif is_active:
        try:
            cooldown_remaining = max(0, int(throttle_until - time.time()))
        except Exception:
            cooldown_remaining = 0
    else:
        cooldown_remaining = 0

    response_data = {
        'uptime': utils.format_seconds(elapsed),
        'cash': st.get('current_cash', 0),
        'logs': [l for l in state.command_logs if str(l.get('bot_id')) == uid][:200],
        'status': current_status,
        'security': {
             'captchas': st.get('captchas_solved', 0),
             'bans': st.get('bans_detected', 0),
             'warnings': st.get('warnings_detected', 0),
             'last_message': st.get('last_captcha_msg', '')
        },
        'analytics': {
            'cph': cph,
            'gems_used': st.get('gems_used', 0)
        },
        'bot': {
            'user_id': uid,
            'username': st.get('username', 'Unknown'),
            'channel_id': bot.channel_id if is_active else None,
            'paused': bot.paused if is_active else True,
            'throttled': is_active and throttle_until != float('inf') and time.time() < throttle_until,
            'cooldown_remaining': cooldown_remaining,
            'cooldown_command': bot.last_sent_command if is_active else None
        },
        'chart_data': {
            'hunt': st.get('hunt_count', 0),
            'battle': st.get('battle_count', 0),
            'session_hunt': st.get('session_hunt_count', 0),
            'session_battle': st.get('session_battle_count', 0),
            'session_owo': st.get('session_owo_count', 0),
            'other': st.get('other_count', 0),
            'owo': st.get('owo_count', 0),
            'total': st.get('total_cmd_count', 0),
            'perf_bpm': cpm
        },
        'system': {
            'last_cash_update': st.get('last_cash_update', 0),
            'pending_commands': len(st.get('pending_commands', []))
        },
        'quest_data': st.get('quest_data', []),
        'next_quest_timer': st.get('next_quest_timer'),
        'cmd_states': cmd_states,
        'gambling_stats': st.get('gambling_stats', {})
    }
    
    try:
        return jsonify(protect_large_ints(response_data))
    except Exception as e:
        log.warning(f"Stats serialization failed: {e}")
        return jsonify({'status': current_status}), 200

@app.route('/api/debug')
@login_required
def debug():
    return jsonify({
        'account_stats': state.account_stats,
        'bot_instances': len(state.bot_instances),
        'command_logs_count': len(state.command_logs),
        'full_history_count': len(state.full_session_history)
    })

@app.route('/api/debug_status')
def debug_status():
    res = []
    for bot in state.bot_instances:
        res.append({
            'username': bot.username,
            'id': str(bot.user.id) if bot.user else None,
            'ready': bot.is_ready,
            'cmd_count': len(bot.cmd_states),
            'cmds': list(bot.cmd_states.keys())
        })
    return jsonify(res)

@app.route('/api/history')
@login_required
def get_history():
    return jsonify(list(reversed(state.full_session_history)))

@app.route('/api/history/analytics')
@login_required
def get_analytics():
    try:
        from utils import history_tracker
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        dat = history_tracker.get_analytics_data(start_date=start_date, end_date=end_date)
        dat['recent_logs'] = list(state.full_session_history)[-500:]
        return jsonify(dat)
    except Exception:
        return jsonify({"error": "Failed to load analytics data"}), 500

@app.route('/api/settings', methods=['GET', 'POST'])
@login_required
def settings():
    account_id = request.args.get('id')
    
    if request.method == 'POST':
        # Write operations require manage permissions
        user_role = session.get('role', 'view')
        if ROLE_HIERARCHY.get(user_role, 0) < ROLE_HIERARCHY.get('manage', 20):
            return jsonify({'success': False, 'error': 'Insufficient permissions'}), 403
        
        new_config = request.json
        try:
            save_to_all = request.args.get('all_accounts') == 'true' or request.args.get('all') == 'true'
            
            if save_to_all:
                ghd.write_json("config/settings.json", new_config, message="Update global settings from dashboard")
                
                # Update all per-user settings files as well
                settings_files = ghd.list_files("config")
                for fpath in settings_files:
                    if fpath.startswith("config/settings_") and fpath.endswith(".json"):
                        ghd.write_json(fpath, new_config, message=f"Update {fpath} from dashboard (all accounts)")
                
                for bot in state.bot_instances:
                    _run_on_bot_loop(bot, bot.sync_settings(new_config))
                
                state.log_command("SYS", "Settings updated for ALL accounts", "success")
            else:
                if account_id:
                    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '', account_id) if account_id else ''
                    config_path = f'config/settings_{safe_id}.json'
                else:
                    config_path = 'config/settings.json'
                
                ghd.write_json(config_path, new_config, message=f"Update settings from dashboard")
                
                for bot in state.bot_instances:
                    if (not account_id) or (bot.user and str(bot.user.id) == str(account_id)):
                        _run_on_bot_loop(bot, bot.sync_settings(new_config))
                
                state.log_command("SYS", f"Settings updated for {'Account ' + account_id if account_id else 'Global'}", "success")
            
            return jsonify({"status": "success"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to save settings: {e}"}), 500
    else:
        if account_id:
            safe_id = re.sub(r'[^a-zA-Z0-9_-]', '', account_id)
            config_path = f'config/settings_{safe_id}.json'
        else:
            config_path = 'config/settings.json'
        
        try:
            data = ghd.read_json(config_path, default=None)
            if data is None and account_id:
                data = ghd.read_json("config/settings.json", default={})
            elif data is None:
                data = {}
            
            # Ensure manager_bot and discord_oauth sections appear in the config UI
            if 'manager_bot' not in data:
                data['manager_bot'] = {
                    'token': '',
                    'prefix': '!'
                }
            mb = data.get('manager_bot', {})
            if not isinstance(mb, dict):
                mb = data['manager_bot'] = {}
            if 'announcements' not in mb:
                mb['announcements'] = {
                    'channel_id': '',
                    'auto_post': False,
                    'post_time': '09:00'
                }
            if 'discord_oauth' not in data:
                data['discord_oauth'] = {
                    'client_id': '',
                    'client_secret': '',
                    'redirect_uri': 'http://localhost:8000/api/auth/discord/callback'
                }
            
            return jsonify(protect_large_ints(data))
        except:
            return jsonify({
                'manager_bot': {
                    'token': '',
                    'prefix': '!',
                    'announcements': {
                        'channel_id': '',
                        'auto_post': False,
                        'post_time': '09:00'
                    }
                },
                'discord_oauth': {
                    'client_id': '',
                    'client_secret': '',
                    'redirect_uri': 'http://localhost:8000/api/auth/discord/callback'
                }
            })

@app.route('/api/accounts/config', methods=['GET', 'POST'])
@login_required
def accounts_config_api():
    if request.method == 'POST':
        user_role = session.get('role', 'view')
        if ROLE_HIERARCHY.get(user_role, 0) < ROLE_HIERARCHY.get('manage', 20):
            return jsonify({'success': False, 'error': 'Insufficient permissions'}), 403
        payload = request.json or {}
        accounts = payload.get('accounts', payload if isinstance(payload, list) else [])
        try:
            ghd.write_json("config/accounts.json", {"accounts": accounts}, message="Update accounts from dashboard")
            from utils import proxy_manager
            proxy_manager.sync_proxy_assignments()
            for bot in state.bot_instances:
                bot.accounts = accounts
            state.log_command("SYS", "Accounts config updated. Restart recommended.", "success")
            return jsonify({"status": "success"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to save accounts config: {e}"}), 500

    try:
        from utils import proxy_manager
        account_data = ghd.read_json("config/accounts.json", default={"accounts": []})
        accounts = account_data.get('accounts', [])
        for acc in accounts:
            if acc.get('token'):
                acc['token_masked'] = proxy_manager.mask_token(acc['token'])
        return jsonify({'accounts': accounts})
    except Exception:
        return jsonify({'accounts': []})


@app.route('/api/accounts', methods=['GET', 'POST'])
@login_required
def accounts_api():
    if request.method == 'POST':
        user_role = session.get('role', 'view')
        if ROLE_HIERARCHY.get(user_role, 0) < ROLE_HIERARCHY.get('manage', 20):
            return jsonify({'success': False, 'error': 'Insufficient permissions'}), 403
        new_accounts = request.json
        try:
            ghd.write_json("config/accounts.json", new_accounts, message="Update accounts from dashboard")

            for bot in state.bot_instances:
                bot.accounts = new_accounts.get('accounts', new_accounts) if isinstance(new_accounts, dict) else new_accounts

            state.log_command("SYS", "Accounts updated successfully. Restart recommended.", "success")
            return jsonify({"status": "success"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to save accounts config: {e}"}), 500
    else:
        try:
            account_data = ghd.read_json("config/accounts.json", default={"accounts": []})
            return jsonify(account_data)
        except Exception:
            return jsonify([])


@app.route('/api/proxies', methods=['GET', 'POST'])
@login_required
def proxies_api():
    from utils import proxy_manager
    if request.method == 'POST':
        user_role = session.get('role', 'view')
        if ROLE_HIERARCHY.get(user_role, 0) < ROLE_HIERARCHY.get('manage', 20):
            return jsonify({'success': False, 'error': 'Insufficient permissions'}), 403
        payload = request.json or {}
        proxies = payload.get('proxies', [])
        proxy_manager.save_proxies(proxies)
        proxy_manager.sync_proxy_assignments()
        state.log_command("SYS", "Proxy pool saved", "success")
        return jsonify({"status": "success", "proxies": proxy_manager.load_proxies()})
    return jsonify({"proxies": proxy_manager.load_proxies()})


@app.route('/api/proxies/bulk', methods=['POST'])
@require_permission('manage')
def proxies_bulk():
    from utils import proxy_manager
    text = (request.json or {}).get('text', '')
    result = proxy_manager.bulk_import(text)
    state.log_command("SYS", f"Bulk imported {len(result['added'])} proxies", "success")
    return jsonify({
        "status": "success",
        "added": len(result['added']),
        "errors": result['errors'],
        "proxies": proxy_manager.load_proxies(),
    })


@app.route('/api/proxies/test', methods=['POST'])
@require_permission('manage')
def proxies_test():
    from utils import proxy_manager
    payload = request.json or {}
    proxy_id = payload.get('id')

    async def _run():
        if proxy_id:
            persist = payload.get('persist', True)
            proxy = proxy_manager.get_proxy_by_id(proxy_id)
            if not proxy:
                return {"ok": False, "error": "not found"}
            ok = await proxy_manager.test_proxy(proxy)
            updated = proxy
            if persist:
                proxies = proxy_manager.load_proxies()
                for p in proxies:
                    if p.get('id') == proxy_id:
                        p['status'] = proxy['status']
                        p['last_check'] = proxy['last_check']
                        p['last_attempts'] = proxy.get('last_attempts')
                        updated = p
                proxy_manager.save_proxies(proxies)
            return {"ok": ok, "id": proxy_id, "status": proxy['status'], "last_check": proxy['last_check'], "last_attempts": proxy.get('last_attempts'), "proxy": updated}
        results = await proxy_manager.test_all_proxies()
        return {"results": results, "proxies": proxy_manager.load_proxies()}

    result = asyncio.run(_run())
    return jsonify({"status": "success", **result})


@app.route('/api/proxies/assign', methods=['POST'])
@require_permission('manage')
def proxies_assign():
    from utils import proxy_manager
    assigned = proxy_manager.auto_assign()
    state.log_command("SYS", f"Auto-assigned {len(assigned)} proxies to accounts", "success")
    return jsonify({"status": "success", "assigned": assigned, "proxies": proxy_manager.load_proxies()})


@app.route('/api/proxies/<proxy_id>', methods=['DELETE'])
@require_permission('manage')
def proxies_delete(proxy_id):
    from utils import proxy_manager
    proxy_manager.remove_proxy(proxy_id)
    state.log_command("SYS", f"Removed proxy {proxy_id}", "info")
    return jsonify({"status": "success", "proxies": proxy_manager.load_proxies()})


@app.route('/api/proxies/all', methods=['DELETE'])
@require_permission('manage')
def proxies_delete_all():
    from utils import proxy_manager
    proxy_manager.remove_all_proxies()
    state.log_command("SYS", "Deleted ALL proxies", "info")
    return jsonify({"status": "success", "proxies": []})


@app.route('/api/proxies/failed', methods=['DELETE'])
@require_permission('manage')
def proxies_delete_failed():
    from utils import proxy_manager
    count = proxy_manager.remove_failed_proxies()
    state.log_command("SYS", f"Deleted {count} failed proxies", "info")
    return jsonify({"status": "success", "count": count, "proxies": proxy_manager.load_proxies()})


@app.route('/api/security/test', methods=['POST'])
@require_permission('manage')
def test_security():
    account_id = request.args.get('id')
    bot = get_bot(account_id)
    if not bot:
        return jsonify({'status': 'error', 'message': 'Bot not found'}), 404
        
    sec = bot.get_cog('Security')
    if sec:
        err = _run_on_bot_loop(bot, sec.play_beep())
        if err:
            return jsonify({'status': 'error', 'message': err}), 503
        sec._show_desktop_notification("Test: Limey Security Alert working!")
        sec._send_webhook("SYSTEM TEST", "This is a test of your security notification system. All systems are operational.")
        return jsonify({'status': 'success', 'message': 'Test signals sent'})
    
    return jsonify({'status': 'error', 'message': 'Security module not loaded'}), 500

@app.route('/api/control', methods=['POST'])
@require_permission('manage')
def control():
    data = request.json
    action = data.get('action')
    account_id = data.get('id')
    bot = get_bot(account_id)
    
    if not bot: return jsonify({'success': False, 'error': 'Bot not found'})
    
    if action == 'stop':
        bot.paused = True
        bot.log("SYS", "Bot STOPPED via Dashboard")
            
    elif action == 'start':
        bot.paused = False
        bot.throttle_until = 0
        bot.log("SYS", "Bot RESUMED via Dashboard")
            
    elif action == 'cash':
        err = _run_on_bot_loop(bot, bot.send_message(f"{bot.prefix}cash", skip_typing=True, priority=True))
        if err:
            return jsonify({'success': False, 'error': err}), 503
        state.log_command("CMD", "Manual Cash Check Sent", "info", bot_name=bot.username)
        
    return jsonify({'success': True})


@app.route('/api/control/all', methods=['POST'])
@require_permission('manage')
def control_all():
    """Start or stop all bots at once."""
    data = request.json
    action = data.get('action', '').strip().lower()
    
    if action not in ('start', 'stop'):
        return jsonify({'success': False, 'error': 'Action must be "start" or "stop"'}), 400
    
    success_count = 0
    fail_count = 0
    
    for bot in state.bot_instances:
        try:
            if action == 'stop':
                bot.paused = True
                bot.log("SYS", f"Bot STOPPED via Dashboard (bulk {action})")
            elif action == 'start':
                bot.paused = False
                bot.throttle_until = 0
                bot.log("SYS", f"Bot RESUMED via Dashboard (bulk {action})")
            success_count += 1
        except Exception:
            fail_count += 1
    
    if action == 'stop':
        state.log_command("SYS", f"Bulk STOP: {success_count} bots stopped, {fail_count} failed", "warning")
    else:
        state.log_command("SYS", f"Bulk START: {success_count} bots resumed, {fail_count} failed", "success")
    
    return jsonify({
        'success': True,
        'action': action,
        'success_count': success_count,
        'fail_count': fail_count,
        'total': success_count + fail_count
    })

@app.route('/api/security', methods=['POST'])
@require_permission('manage')
def security():
    data = request.json
    action = data.get('action')
    account_id = data.get('id')
    bot = get_bot(account_id)
    
    if not bot: return jsonify({'success': False, 'error': 'Bot not found'})

    if action == 'resume':
        bot.paused = False
        bot.throttle_until = 0
        state.log_command("SEC", f"User Resumed {bot.username} from Security Alert", "success")
            
    return jsonify({'success': True})

@app.route('/api/captcha/current')
@login_required
def captcha_current():
    account_id = request.args.get('id')
    bot = get_bot(account_id)
    if not bot: return jsonify({'success': False})
    
    st = bot.stats
    captcha_data = st.get('current_captcha')
    
    if captcha_data and captcha_data.get('image_url'):
        timestamp = captcha_data.get('timestamp', 0)
        if time.time() - timestamp < 600:
            return jsonify({
                'success': True,
                'url': captcha_data['image_url'],
                'cash': captcha_data.get('cash', 16000),
                'command': captcha_data.get('command_template', 'owo autohunt {cash} {password}'),
                'age_seconds': int(time.time() - timestamp)
            })
        else:
            if 'current_captcha' in st:
                del st['current_captcha']
    
    return jsonify({'success': False, 'message': 'No active captcha'})

@app.route('/api/captcha/submit', methods=['POST'])
@require_permission('manage')
def captcha_submit():
    data = request.json
    code = data.get('code', '').strip()
    account_id = data.get('id')
    bot = get_bot(account_id)
    
    if not bot: return jsonify({'success': False, 'error': 'Bot not found'})
    
    if not code:
        return jsonify({'success': False, 'error': 'No password provided'})
    
    st = bot.stats
    captcha_data = st.get('current_captcha')
    if not captcha_data:
        return jsonify({'success': False, 'error': 'No active captcha'})
    
    cash = captcha_data.get('cash', 16000)
    command_template = captcha_data.get('command_template', f"owo autohunt {cash} {{password}}")
    full_command = command_template.replace('{password}', code)
    
    err = _run_on_bot_loop(bot, bot.send_message(full_command, skip_typing=True, priority=True))
    if err:
        return jsonify({'success': False, 'error': err}), 503
    
    if 'current_captcha' in st:
        del st['current_captcha']
    
    st['captchas_solved_today'] = st.get('captchas_solved_today', 0) + 1
    st['captcha_success_count'] = st.get('captcha_success_count', 0) + 1
    state.log_command("CMD", f"Captcha solution sent: {full_command}", bot_name=bot.username)
    
    return jsonify({'success': True, 'message': f'Captcha solution sent: {full_command}'})

@app.route('/api/captcha/balance', methods=['GET', 'POST'])
@login_required
def captcha_balance():
    account_id = request.args.get('id')
    bot = get_bot(account_id)
    if not bot:
        return jsonify({'balance': None, 'service': 'unknown', 'error': 'Bot not found'})
    
    cfg = bot.config.get('security', {}).get('captcha_solver', {})
    service = cfg.get('service', 'yescaptcha')
    api_key = ''
    
    if request.method == 'POST':
        data = request.json or {}
        if 'service' in data:
            service = data['service']
        if 'api_key' in data:
            api_key = data['api_key']
            
    if not api_key:
        if service == 'nopecha':
            api_key = cfg.get('nopecha_api_key', cfg.get('api_key', ''))
        elif service == 'anticaptcha':
            api_key = cfg.get('anticaptcha_api_key', cfg.get('api_key', ''))
        elif service == 'captchaly':
            api_key = cfg.get('captchaly_api_key', cfg.get('api_key', ''))
        else:
            api_key = cfg.get('yescaptcha_api_key', cfg.get('api_key', ''))


    temp_solver = None
    if service == 'nopecha':
        from modules.services.nopecha import NopeCaptchaService
        temp_solver = NopeCaptchaService(bot, api_key, "")
    elif service == 'anticaptcha':
        from modules.services.anticaptcha import AntiCaptchaService
        temp_solver = AntiCaptchaService(bot, api_key, "")
    elif service == 'captchaly':
        from modules.services.captchaly import CaptchalyService
        temp_solver = CaptchalyService(bot, api_key, "")
    else:
        from modules.services.yescaptcha import YesCaptchaService
        temp_solver = YesCaptchaService(bot, api_key, "")

    loop = bot.loop_ref
    if loop is None:
        return jsonify({'balance': None, 'service': service, 'error': 'Bot is still connecting – try again in a moment.'}), 503

    try:
        future = asyncio.run_coroutine_threadsafe(temp_solver.get_balance(), loop)
        balance = future.result(timeout=10)
        return jsonify({'balance': balance, 'service': service, 'enabled': cfg.get('enabled', False)})
    except Exception:
        return jsonify({'balance': None, 'service': service, 'error': 'Failed to get balance'})

@app.route('/api/captcha/stats')
@login_required
def captcha_stats():
    account_id = request.args.get('id')
    bot = get_bot(account_id)
    st = bot.stats if bot else {}
    
    solved = st.get('captchas_solved_today', 0)
    success = st.get('captcha_success_count', 0)
    success_rate = 100 if solved == 0 else round((success / max(solved, 1)) * 100)
    
    return jsonify({
        'solved': solved,
        'success_rate': success_rate
    })

@app.route('/api/bot/command', methods=['POST'])
@require_permission('manage')
def bot_command():
    data = request.json
    command = data.get('command', '').strip()
    account_id = data.get('id')
    bot = get_bot(account_id)
    
    if not bot: return jsonify({'success': False, 'error': 'Bot not found'})
    
    if not command:
        return jsonify({'success': False, 'error': 'No command provided'})
    
    err = _run_on_bot_loop(bot, bot.send_message(command, skip_typing=True, priority=True))
    if err:
        return jsonify({'success': False, 'error': err}), 503
    state.log_command("CMD", f"Manual command sent: {command}", bot_name=bot.username)
    return jsonify({'success': True, 'message': f'Command sent: {command}'})

# ── Quest Orb Grinder API ────────────────────────────

def _get_grinder(account_id):
    """Get the QuestGrinder for an account. Returns (grinder, error)."""
    bot = get_bot(account_id)
    if not bot:
        return None, "Bot not found"
    grinder = getattr(bot, 'quest_grinder', None)
    if not grinder:
        return None, "Orb Grinder not initialized for this account"
    return grinder, None


@app.route('/api/quests/status')
@login_required
def quests_status():
    """Live status of the Discord quest orb grinder for an account."""
    grinder, err = _get_grinder(request.args.get('id'))
    if not grinder:
        return jsonify({'success': False, 'error': err}), 404
    try:
        return jsonify({'success': True, 'status': grinder.status_dict()})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to read grinder status: {e}'}), 500


@app.route('/api/quests/refresh', methods=['POST'])
@require_permission('manage')
def quests_refresh():
    """Force-refresh the quest list for an account."""
    data = request.json or {}
    grinder, err = _get_grinder(data.get('id'))
    if not grinder:
        return jsonify({'success': False, 'error': err}), 404
    e = _run_on_bot_loop(grinder.bot, grinder.refresh())
    if e:
        return jsonify({'success': False, 'error': e}), 503
    return jsonify({'success': True, 'message': 'Quest list refreshed'})


@app.route('/api/quests/auto', methods=['POST'])
@require_permission('manage')
def quests_auto():
    """Enable/disable auto grinding (enroll + auto-progress quests)."""
    data = request.json or {}
    grinder, err = _get_grinder(data.get('id'))
    if not grinder:
        return jsonify({'success': False, 'error': err}), 404
    enabled = bool(data.get('enabled'))
    e = _run_on_bot_loop(grinder.bot, grinder.set_auto(enabled))
    if e:
        return jsonify({'success': False, 'error': e}), 503
    return jsonify({
        'success': True,
        'message': 'Auto grinding enabled' if enabled else 'Auto grinding disabled',
        'auto_enabled': enabled,
    })


@app.route('/api/quests/claim', methods=['POST'])
@require_permission('manage')
def quests_claim():
    """Manually claim a completed quest's reward (Discord Orbs)."""
    data = request.json or {}
    grinder, err = _get_grinder(data.get('id'))
    if not grinder:
        return jsonify({'success': False, 'error': err}), 404
    quest_id = data.get('quest_id')
    if not quest_id:
        return jsonify({'success': False, 'error': 'quest_id required'}), 400
    e = _run_on_bot_loop(grinder.bot, grinder.claim_quest(quest_id))
    if e:
        return jsonify({'success': False, 'error': e}), 503
    return jsonify({'success': True, 'message': 'Claim attempted'})


@app.route('/api/quests/retry', methods=['POST'])
@require_permission('manage')
def quests_retry():
    """Manually retry an errored/available quest (re-enroll + progress)."""
    data = request.json or {}
    grinder, err = _get_grinder(data.get('id'))
    if not grinder:
        return jsonify({'success': False, 'error': err}), 404
    quest_id = data.get('quest_id')
    if not quest_id:
        return jsonify({'success': False, 'error': 'quest_id required'}), 400
    e = _run_on_bot_loop(grinder.bot, grinder.retry_quest(quest_id))
    if e:
        return jsonify({'success': False, 'error': e}), 503
    return jsonify({'success': True, 'message': 'Quest retry started'})


# ── Mass Dismantle (Weapons) API ───────────────────────

def _get_weapon_manager(account_id):
    """Get the WeaponManager for an account. Returns (manager, error)."""
    bot = get_bot(account_id)
    if not bot:
        return None, "Bot not found"
    wm = getattr(bot, 'weapon_manager', None)
    if not wm:
        return None, "Mass Dismantle not initialized for this account"
    return wm, None


@app.route('/api/weapons/status')
@login_required
def weapons_status():
    """Live weapon list + manager status for an account."""
    wm, err = _get_weapon_manager(request.args.get('id'))
    if not wm:
        return jsonify({'success': False, 'error': err}), 404
    return jsonify({'success': True, **wm.status_dict()})


@app.route('/api/weapons/fetch', methods=['POST'])
@require_permission('manage')
def weapons_fetch():
    """Ask the selfbot to type `owo weapons` and parse the weapon list."""
    data = request.json or {}
    wm, err = _get_weapon_manager(data.get('id'))
    if not wm:
        return jsonify({'success': False, 'error': err}), 404
    e = _run_on_bot_loop(wm.bot, wm.fetch_weapons())
    if e:
        return jsonify({'success': False, 'error': e}), 503
    return jsonify({'success': True, 'message': 'Fetching weapons...'})


@app.route('/api/weapons/action', methods=['POST'])
@require_permission('manage')
def weapons_action():
    """Sell or dismantle one weapon: owo sell <id> / owo dismantle <id>."""
    data = request.json or {}
    wm, err = _get_weapon_manager(data.get('id'))
    if not wm:
        return jsonify({'success': False, 'error': err}), 404
    action = data.get('action', '')
    weapon_id = str(data.get('weapon_id', '')).strip()
    if action not in ('sell', 'dismantle'):
        return jsonify({'success': False, 'error': 'Action must be "sell" or "dismantle"'}), 400
    if not weapon_id:
        return jsonify({'success': False, 'error': 'Missing weapon_id'}), 400
    coro = wm.sell_weapon(weapon_id) if action == 'sell' else wm.dismantle_weapon(weapon_id)
    e = _run_on_bot_loop(wm.bot, coro)
    if e:
        return jsonify({'success': False, 'error': e}), 503
    return jsonify({'success': True, 'message': f'owo {action} {weapon_id} sent'})


@app.route('/api/weapons/bulk', methods=['POST'])
@require_permission('manage')
def weapons_bulk():
    """Bulk action: sell/dismantle all weapons (owo sell all / owo dismantle all)."""
    data = request.json or {}
    wm, err = _get_weapon_manager(data.get('id'))
    if not wm:
        return jsonify({'success': False, 'error': err}), 404
    action = data.get('action', '')
    if action not in ('sell', 'dismantle'):
        return jsonify({'success': False, 'error': 'Action must be "sell" or "dismantle"'}), 400
    coro = wm.sell_all() if action == 'sell' else wm.dismantle_all()
    e = _run_on_bot_loop(wm.bot, coro)
    if e:
        return jsonify({'success': False, 'error': e}), 503
    return jsonify({'success': True, 'message': f'owo {action} all sent'})


_pending_captchas = {}

# ── System Control API ────────────────────────────────

@app.route('/api/system/status')
@login_required
def system_status():
    """Get system status information."""
    uptime = time.time() - state.active_session_start if state.active_session_start else 0
    bot_count = len(state.bot_instances)
    active_count = sum(1 for b in state.bot_instances if not b.paused)
    
    # Get memory info (cross-platform)
    memory_info = {}
    try:
        import psutil
        process = psutil.Process(os.getpid())
        memory_info = {
            'rss': process.memory_info().rss,
            'vms': process.memory_info().vms,
            'percent': process.memory_percent(),
            'cpu_percent': process.cpu_percent(interval=0.1),
        }
        memory_info['rss_mb'] = round(memory_info['rss'] / 1024 / 1024, 1)
        memory_info['vms_mb'] = round(memory_info['vms'] / 1024 / 1024, 1)
        memory_info['cpu'] = round(memory_info['cpu_percent'], 1)
        memory_info['available'] = psutil is not None
    except ImportError:
        memory_info = {'available': False}
    except Exception:
        memory_info = {'available': False}
    
    return jsonify({
        'success': True,
        'system': {
            'uptime': utils.format_seconds(uptime),
            'uptime_seconds': uptime,
            'bot_count': bot_count,
            'active_count': active_count,
            'platform': sys.platform,
            'python_version': sys.version.split()[0],
            'memory': memory_info,
            'pid': os.getpid(),
        }
    })


@app.route('/api/system/restart', methods=['POST'])
@require_permission('manage')
def system_restart():
    """Restart the entire bot system."""
    try:
        state.log_command("SYS", "🔄 System restart initiated from dashboard...", "warning")
        
        # Save state
        state.save_account_stats()
        try:
            import utils.history_tracker as ht
            ht.end_session()
        except Exception:
            pass
        
        # Stop all bots gracefully
        for bot in state.bot_instances:
            try:
                bot.active = False
                loop = bot.loop_ref
                if loop is not None:
                    asyncio.run_coroutine_threadsafe(bot.close(), loop)
            except Exception:
                pass
        
        state.log_command("SYS", "✅ State saved, all bots stopped. Restarting...", "success")
        
        # Use a thread for restart to avoid blocking the response
        def _do_restart():
            time.sleep(1)  # Give time for the response to be sent
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception:
                os._exit(0)
        
        threading.Thread(target=_do_restart, daemon=True).start()
        
        return jsonify({'success': True, 'message': 'System restarting...'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Restart failed: {e}'}), 500


@app.route('/api/system/shutdown', methods=['POST'])
@require_permission('manage')
def system_shutdown():
    """Shutdown the entire bot system."""
    try:
        state.log_command("SYS", "🛑 System shutdown initiated from dashboard...", "warning")
        
        # Save state
        state.save_account_stats()
        try:
            import utils.history_tracker as ht
            ht.end_session()
        except Exception:
            pass
        
        # Stop all bots
        for bot in state.bot_instances:
            try:
                bot.active = False
                loop = bot.loop_ref
                if loop is not None:
                    asyncio.run_coroutine_threadsafe(bot.close(), loop)
            except Exception:
                pass
        
        state.log_command("SYS", "✅ State saved. System shutting down...", "success")
        
        def _do_shutdown():
            time.sleep(1)
            os._exit(0)
        
        threading.Thread(target=_do_shutdown, daemon=True).start()
        
        return jsonify({'success': True, 'message': 'System shutting down...'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Shutdown failed: {e}'}), 500


@app.route('/api/system/logs/clear', methods=['POST'])
@require_permission('manage')
def system_clear_logs():
    """Clear the in-memory command logs."""
    try:
        state.command_logs.clear()
        state.log_command("SYS", "🗑️ Command logs cleared from dashboard", "info")
        return jsonify({'success': True, 'message': 'Logs cleared'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to clear logs: {e}'}), 500


@app.route('/api/captcha_challenge', methods=['GET'])
@login_required
def get_captcha_challenge():
    """Get pending captcha challenges for dashboard display."""
    account_id = request.args.get('account_id', type=str)
    if account_id and account_id in _pending_captchas:
        challenge = _pending_captchas[account_id]
        return jsonify({'success': True, 'challenge': challenge})
    
    if _pending_captchas:
        for acc_id, challenge in _pending_captchas.items():
            return jsonify({'success': True, 'challenge': challenge, 'account_id': acc_id})
    return jsonify({'success': False, 'message': 'No captcha pending'})

@app.route('/api/captcha_solve', methods=['POST'])
@require_permission('manage')
def submit_captcha_solution():
    """Submit hCaptcha solution from dashboard."""
    import socket
    import requests
    
    data = request.get_json()
    account_id = data.get('account_id', '')
    token = data.get('token', '')
    
    if not account_id or not token:
        return jsonify({'success': False, 'error': 'Missing account_id or token'})
    
    bot = get_bot(account_id)
    if not bot:
        return jsonify({'success': False, 'error': 'Bot not found'})
    
    _original_getaddrinfo = socket.getaddrinfo
    
    def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host == 'owobot.com':

            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('104.21.35.189', port))]
        return _original_getaddrinfo(host, port, family, type, proto, flags)
    
    socket.getaddrinfo = patched_getaddrinfo
    
    headers = {
        "Authorization": bot.token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    payload = {"token": token}
    
    try:
        verify_url = "https://owobot.com/api/captcha/verify"
        response = requests.post(verify_url, json=payload, headers=headers, timeout=10)
        
        socket.getaddrinfo = _original_getaddrinfo
        
        if response.status_code == 200:
            from modules.web_solver import WebSolver
            WebSolver.mark_verification_done(account_id)
            clear_captcha_challenge(account_id)
            # Resume the bot immediately instead of waiting for the DM confirmation
            bot.paused = False
            bot.throttle_until = 0.0
            bot.last_sent_time = 0
            bot.warmup_until = 0
            state.log_command("SEC", f"Captcha verified for account {account_id}", "success")
            return jsonify({'success': True, 'message': 'Captcha verified successfully'})
        else:
            state.log_command("SEC", f"Captcha verification failed: {response.text}", "error")
            return jsonify({'success': False, 'error': 'Invalid captcha token'})
    except Exception:
        socket.getaddrinfo = _original_getaddrinfo
        state.log_command("SEC", "Verification error", "error")
        return jsonify({'success': False, 'error': 'Captcha verification failed'})
    
    
@app.route('/api/captcha/oauth_url', methods=['POST'])
@login_required
def captcha_oauth_url():
    data = request.get_json()
    account_id = data.get('account_id')
    if not account_id:
        return jsonify({'success': False, 'error': 'Missing account_id'})
    
    bot = get_bot(account_id)
    if not bot:
        return jsonify({'success': False, 'error': 'Bot not found'})
    
    import aiohttp
    import asyncio
    
    auth_url = "https://discord.com/api/v9/oauth2/authorize?client_id=408785106942164992&response_type=code&redirect_uri=https://owobot.com/api/auth/discord/redirect&scope=identify guilds"
    
    async def get_redirect_url():
        headers = {
            "Authorization": bot.token,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            auth_payload = {
                "authorize": True,
                "permissions": "0",
                "integration_type": 0,
                "location_context": {"guild_id": "10000", "channel_id": "10000", "channel_type": 10000}
            }
            async with session.post(auth_url, json=auth_payload) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("location")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    redirect_url = loop.run_until_complete(get_redirect_url())
    loop.close()
    
    if not redirect_url:
        return jsonify({'success': False, 'error': 'Failed to get OAuth URL'})
    
    return jsonify({'success': True, 'url': redirect_url})

# ── Ticket API ────────────────────────────────────────


def _load_ticket_data():
    """Load ticket data from GitHub data repo."""
    data = ghd.read_json("config/tickets.json", default=None)
    if data is not None:
        return data
    return {"config": {}, "tickets": {}, "next_ticket_num": 1}


@app.route('/api/tickets/config', methods=['GET', 'POST'])
@require_permission('manage')
def api_tickets_config():
    """Get or update the ticket system configuration."""
    if request.method == 'POST':
        payload = request.json or {}
        config_update = payload.get('config', {})
        
        data = _load_ticket_data()
        if 'config' not in data:
            data['config'] = {}
        
        # Merge the update into existing config
        for key in ('staff_role_id', 'log_channel_id'):
            if key in config_update:
                data['config'][key] = str(config_update[key]) if config_update[key] else ''
        
        _save_ticket_data(data)
        
        state.log_command("SYS", "Ticket configuration updated from dashboard", "success")
        return jsonify({'success': True, 'message': 'Ticket configuration saved', 'config': data['config']})
    
    # GET: Return current config
    data = _load_ticket_data()
    return jsonify({'success': True, 'config': data.get('config', {})})


@app.route('/api/tickets/stats')
@require_permission('manage')
def api_tickets_stats():
    """Get ticket statistics across all guilds."""
    data = _load_ticket_data()
    tickets = data.get('tickets', {})
    
    total_open = 0
    total_closed = 0
    total_orphaned = 0
    type_breakdown = {}
    guild_totals = {}
    
    for guild_key, guild_tickets in tickets.items():
        guild_totals[guild_key] = len(guild_tickets)
        for tnum, tdata in guild_tickets.items():
            status = tdata.get('status', 'unknown')
            if status == 'open':
                total_open += 1
            elif status == 'closed':
                total_closed += 1
            else:
                total_orphaned += 1
            
            ttype = tdata.get('type', 'unknown')
            type_breakdown[ttype] = type_breakdown.get(ttype, 0) + 1
    
    return jsonify({
        'success': True,
        'stats': {
            'total_open': total_open,
            'total_closed': total_closed,
            'total_orphaned': total_orphaned,
            'total_all': total_open + total_closed + total_orphaned,
            'type_breakdown': type_breakdown,
            'guild_count': len(guild_totals),
        }
    })


@app.route('/api/tickets/list')
@require_permission('manage')
def api_tickets_list():
    """List all tickets across all guilds."""
    data = _load_ticket_data()
    tickets = data.get('tickets', {})
    
    status_filter = request.args.get('status', 'all')
    limit = request.args.get('limit', 50, type=int)
    
    results = []
    for guild_key, guild_tickets in tickets.items():
        for tnum, tdata in guild_tickets.items():
            if status_filter != 'all' and tdata.get('status') != status_filter:
                continue
            results.append({
                'ticket_num': tnum,
                'guild_id': guild_key,
                'channel_id': tdata.get('channel_id'),
                'user_id': tdata.get('user_id'),
                'username': tdata.get('username'),
                'type': tdata.get('type'),
                'subject': tdata.get('subject'),
                'status': tdata.get('status'),
                'claimed_by': tdata.get('claimed_by'),
                'created_at': tdata.get('created_at'),
                'closed_at': tdata.get('closed_at'),
            })
    
    results.sort(key=lambda t: t.get('created_at', 0), reverse=True)
    results = results[:limit]
    
    return jsonify({'success': True, 'tickets': results, 'total': len(results)})


# ── Moderation API ────────────────────────────────────


def _load_mod_data():
    """Load moderation data from GitHub data repo."""
    data = ghd.read_json("config/moderation.json", default=None)
    if data is not None:
        return data
    return {"warnings": {}, "mutes": {}, "mod_log": {}, "violations": {}, "next_warn_id": 1, "next_violation_id": 1}


def _get_mod_config():
    """Get moderation config from settings.json via GitHub."""
    cfg = ghd.read_json("config/settings.json", default={})
    return cfg.get("manager_bot", {}).get("moderation", {})


@app.route('/api/moderation/users')
@require_permission('manage')
def api_moderation_users():
    """Get a summary of all users with violations across all guilds."""
    data = _load_mod_data()
    violations = data.get('violations', {})
    
    users_map = {}
    for guild_key, guild_violations in violations.items():
        for user_key, user_violations in guild_violations.items():
            if user_key not in users_map:
                users_map[user_key] = {
                    'user_id': user_key,
                    'guilds': {},
                    'total_violations': 0,
                    'last_violation': 0
                }
            
            if guild_key not in users_map[user_key]['guilds']:
                users_map[user_key]['guilds'][guild_key] = 0
            
            users_map[user_key]['guilds'][guild_key] += len(user_violations)
            users_map[user_key]['total_violations'] += len(user_violations)
            
            for v in user_violations:
                ts = v.get('timestamp', 0)
                if ts > users_map[user_key]['last_violation']:
                    users_map[user_key]['last_violation'] = ts
                    users_map[user_key]['last_type'] = v.get('type', 'unknown')
                    users_map[user_key]['last_reason'] = v.get('reason', '')
    
    users_list = list(users_map.values())
    users_list.sort(key=lambda u: u['last_violation'], reverse=True)
    
    return jsonify({'success': True, 'users': users_list, 'total': len(users_list)})


@app.route('/api/moderation/violations/<user_id>')
@require_permission('manage')
def api_moderation_violations(user_id):
    """Get all violations for a user with full details."""
    data = _load_mod_data()
    violations = data.get('violations', {})
    
    results = []
    for guild_key, guild_violations in violations.items():
        if str(user_id) in guild_violations:
            for v in guild_violations[str(user_id)]:
                v['guild_id'] = guild_key
                results.append(v)
    
    results.sort(key=lambda v: v.get('timestamp', 0), reverse=True)
    return jsonify({'success': True, 'violations': results, 'total': len(results)})


@app.route('/api/moderation/warnings/<user_id>')
@require_permission('manage')
def api_moderation_warnings(user_id):
    """Get all warnings for a user across all guilds."""
    data = _load_mod_data()
    warnings = data.get('warnings', {})
    
    results = []
    for guild_key, guild_warnings in warnings.items():
        if str(user_id) in guild_warnings:
            for w in guild_warnings[str(user_id)]:
                w['guild_id'] = guild_key
                results.append(w)
    
    results.sort(key=lambda w: w.get('timestamp', 0), reverse=True)
    return jsonify({'success': True, 'warnings': results, 'total': len(results)})


@app.route('/api/moderation/modlog')
@require_permission('manage')
def api_moderation_modlog():
    """Get mod log entries across all guilds."""
    data = _load_mod_data()
    mod_log = data.get('mod_log', {})
    
    guild_filter = request.args.get('guild_id', '')
    action_filter = request.args.get('action', '')
    limit = request.args.get('limit', 100, type=int)
    
    results = []
    for guild_key, entries in mod_log.items():
        if guild_filter and guild_key != guild_filter:
            continue
        for entry in entries:
            if action_filter and entry.get('type', '') != action_filter:
                continue
            entry['guild_id'] = guild_key
            results.append(entry)
    
    results.sort(key=lambda e: e.get('timestamp', 0), reverse=True)
    results = results[:limit]
    
    return jsonify({'success': True, 'entries': results, 'total': len(results)})


@app.route('/api/moderation/summary')
@require_permission('manage')
def api_moderation_summary():
    """Get a summary of moderation activity."""
    data = _load_mod_data()
    cfg = _get_mod_config()
    
    violations = data.get('violations', {})
    mod_log = data.get('mod_log', {})
    mutes = data.get('mutes', {})
    
    total_violations = sum(
        len(user_v)
        for guild_v in violations.values()
        for user_v in guild_v.values()
    )
    
    # Count users with violations
    users_with_violations = set()
    for guild_v in violations.values():
        for uid in guild_v.keys():
            users_with_violations.add(uid)
    
    total_mod_actions = sum(len(entries) for entries in mod_log.values())
    active_mutes = 0
    now = time.time()
    for guild_m in mutes.values():
        for uid, mute_info in guild_m.items():
            until = mute_info.get('until', 0)
            if until is None or until > now:
                active_mutes += 1
    
    # Count by type
    type_counts = {}
    for guild_v in violations.values():
        for user_v in guild_v.values():
            items = user_v if isinstance(user_v, list) else [user_v]
            for v in items:
                vtype = v.get('type', 'unknown')
                type_counts[vtype] = type_counts.get(vtype, 0) + 1
    
    warn_thresholds = cfg.get('warn_thresholds', {})
    auto_mod_enabled = cfg.get('auto_mod', {}).get('discord_automod_warn', True)
    muted_role_id = cfg.get('muted_role_id', None)
    mod_log_channel = cfg.get('mod_log_channel_id', '')
    
    return jsonify({
        'success': True,
        'summary': {
            'total_violations': total_violations,
            'users_with_violations': len(users_with_violations),
            'total_mod_actions': total_mod_actions,
            'active_mutes': active_mutes,
            'type_breakdown': type_counts,
            'config': {
                'warn_thresholds': warn_thresholds,
                'auto_mod_enabled': auto_mod_enabled,
                'muted_role_id': muted_role_id,
                'mod_log_channel': mod_log_channel,
            }
        }
    })


@app.route('/api/moderation/clear-violations', methods=['POST'])
@require_permission('manage')
def api_moderation_clear_violations():
    """Clear violations for a user in a guild."""
    payload = request.json or {}
    user_id = str(payload.get('user_id', ''))
    guild_id = str(payload.get('guild_id', ''))
    violation_id = payload.get('violation_id', 'all')
    
    if not user_id:
        return jsonify({'success': False, 'error': 'User ID required'}), 400
    
    data = _load_mod_data()
    
    if guild_id:
        guild_keys = [guild_id]
    else:
        guild_keys = list(data.get('violations', {}).keys())
    
    cleared_count = 0
    for gk in guild_keys:
        guild_v = data.get('violations', {}).get(gk, {})
        if user_id not in guild_v:
            continue
        
        if violation_id == 'all':
            cleared_count += len(guild_v[user_id])
            guild_v[user_id] = []
        else:
            try:
                vid = int(violation_id)
                before = len(guild_v[user_id])
                guild_v[user_id] = [v for v in guild_v[user_id] if v.get('id') != vid]
                cleared_count += before - len(guild_v[user_id])
            except (ValueError, TypeError):
                return jsonify({'success': False, 'error': 'Invalid violation ID'}), 400
    
    _save_mod_data(data)
    state.log_command("MOD", f"Dashboard cleared {cleared_count} violations for user {user_id}", "warning")
    return jsonify({'success': True, 'message': f'Cleared {cleared_count} violation(s)', 'cleared': cleared_count})


@app.route('/api/moderation/action', methods=['POST'])
@require_permission('manage')
def api_moderation_action():
    """Perform a moderation action (warn, kick, ban, unban, timeout, mute, unmute) via a Discord bot."""
    payload = request.json or {}
    action = payload.get('action', '').strip().lower()
    user_id = str(payload.get('user_id', ''))
    guild_id = str(payload.get('guild_id', ''))
    reason = payload.get('reason', '').strip() or 'No reason provided'
    duration = payload.get('duration', '').strip()
    delete_days = payload.get('delete_days', 0)

    if not action or not user_id or not guild_id:
        return jsonify({'success': False, 'error': 'Missing required fields: action, user_id, guild_id'}), 400

    try:
        int(user_id)
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid user ID format'}), 400

    valid_actions = ['warn', 'kick', 'ban', 'unban', 'timeout', 'mute', 'unmute', 'clearviolations', 'clearwarns']
    if action not in valid_actions:
        return jsonify({'success': False, 'error': f'Invalid action. Must be one of: {", ".join(valid_actions)}'}), 400

    # Parse duration
    duration_seconds = None
    if duration and action in ('timeout', 'mute'):
        duration_seconds = _parse_duration_seconds(duration)
        if not duration_seconds:
            return jsonify({'success': False, 'error': 'Invalid duration format. Use e.g. 10m, 1h, 7d'}), 400

    # Find a bot in the target guild
    bot = None
    try:
        guild_id_int = int(guild_id)
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid guild ID'}), 400

    for b in state.bot_instances:
        if b.is_ready and b.get_guild(guild_id_int):
            bot = b
            break

    if not bot:
        return jsonify({'success': False, 'error': 'No bot is connected to the target guild'}), 400

    # Get moderator name
    moderator_name = session.get('username', 'Dashboard')
    if getattr(g, 'api_key_auth', False):
        moderator_name = getattr(g, 'api_key_user', 'API')
    moderator_str = f"{moderator_name} (Dashboard)"

    # For local-only actions (warn, clearviolations, clearwarns), skip Discord API call
    if action in ('warn', 'clearviolations', 'clearwarns'):
        if action == 'warn':
            _store_violation_data(user_id, guild_id, 'warn', reason, moderator_str)
            _store_mod_action_data(guild_id, 'warn', user_id, moderator_str, reason)
            dur_text = ''
        elif action == 'clearviolations':
            data = _load_mod_data()
            gk = str(guild_id)
            uk = str(user_id)
            count = len(data.get('violations', {}).get(gk, {}).get(uk, []))
            if gk in data.get('violations', {}) and uk in data['violations'][gk]:
                data['violations'][gk][uk] = []
                _save_mod_data(data)
            _store_mod_action_data(guild_id, 'clearviolations', user_id, moderator_str, f'Cleared all {count} violations')
            dur_text = f' ({count} cleared)'
        elif action == 'clearwarns':
            data = _load_mod_data()
            gk = str(guild_id)
            uk = str(user_id)
            count = len(data.get('warnings', {}).get(gk, {}).get(uk, []))
            if gk in data.get('warnings', {}) and uk in data['warnings'][gk]:
                data['warnings'][gk][uk] = []
                _save_mod_data(data)
            _store_mod_action_data(guild_id, 'clearwarns', user_id, moderator_str, f'Cleared all {count} warnings')
            dur_text = f' ({count} cleared)'

        state.log_command("MOD", f"Dashboard {action} on user {user_id} in guild {guild_id}: {reason}", "warning")
        return jsonify({'success': True, 'message': f'{action.capitalize()} completed successfully.{dur_text}'})

    # For Discord API actions, run on the bot's event loop
    loop = bot.loop_ref
    if loop is None:
        return jsonify({'success': False, 'error': 'Bot is still connecting – try again in a moment.'}), 503
    future = asyncio.run_coroutine_threadsafe(
        _discord_mod_action(bot, action, guild_id, user_id, reason, duration_seconds, delete_days),
        loop
    )

    try:
        future.result(timeout=30)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except discord.Forbidden:
        return jsonify({'success': False, 'error': 'Bot lacks permissions to perform this action'}), 403
    except Exception as e:
        return jsonify({'success': False, 'error': f'Action failed: {str(e)}'}), 500

    # Store violation for punitive actions
    dur_str = None
    if duration_seconds and action in ('timeout', 'mute'):
        dur_str = _format_duration_seconds(duration_seconds)

    if action in ('kick', 'ban', 'timeout', 'mute'):
        _store_violation_data(user_id, guild_id, action, reason, moderator_str, duration=dur_str)

    # Store mod action
    _store_mod_action_data(guild_id, action, user_id, moderator_str, reason)

    state.log_command("MOD", f"Dashboard {action} on user {user_id} in guild {guild_id}: {reason}", "warning")
    return jsonify({'success': True, 'message': f'{action.capitalize()} completed successfully.'})


def _save_mod_data(data):
    """Save moderation data to GitHub data repo."""
    ghd.write_json("config/moderation.json", data, message="Update moderation data from dashboard")


@app.route('/api/captcha/pending', methods=['GET'])
@login_required
def pending_captchas():
    pending = []
    for acc_id, challenge in _pending_captchas.items():
        pending.append({
            'account_id': acc_id,
            'account_name': challenge.get('account_name', acc_id),
            'created_at': challenge.get('created_at', time.time())
        })
    return jsonify({'pending': pending})

def register_captcha_challenge(account_id, challenge_data):
    _pending_captchas[account_id] = {
        'account_id': account_id,
        'created_at': time.time(),
        **challenge_data
    }
    state.log_command("SEC", f"Captcha challenge registered for account {account_id}", "info")

def clear_captcha_challenge(account_id):
    if account_id in _pending_captchas:
        _pending_captchas.pop(account_id, None)
        state.log_command("SEC", f"Captcha challenge cleared for account {account_id}", "info")
