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
import urllib.parse
import requests
import re

# ── Appeals System ───────────────────────────────────────

from utils.github_data_store import ghd


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
            'throttled': (time.time() < bot.throttle_until) if is_active else False,
            'cooldown_remaining': 999999 if (is_active and bot.throttle_until == float('inf')) else (max(0, int(bot.throttle_until - time.time())) if is_active else 0),
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
        'cmd_states': {k: {**v, 'content': '[Dynamic function]' if callable(v.get('content')) else v.get('content')} for k, v in bot.cmd_states.items()} if bot else {},
        'gambling_stats': st.get('gambling_stats', {})
    }
    
    return jsonify(response_data)

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
                    asyncio.run_coroutine_threadsafe(bot.sync_settings(new_config), bot.loop)
                
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
                        asyncio.run_coroutine_threadsafe(bot.sync_settings(new_config), bot.loop)
                
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
                    'prefix': '!'
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
            proxy = proxy_manager.get_proxy_by_id(proxy_id)
            if not proxy:
                return {"ok": False, "error": "not found"}
            ok = await proxy_manager.test_proxy(proxy)
            proxies = proxy_manager.load_proxies()
            for p in proxies:
                if p.get('id') == proxy_id:
                    p['status'] = proxy['status']
                    p['last_check'] = proxy['last_check']
            proxy_manager.save_proxies(proxies)
            return {"ok": ok, "id": proxy_id, "status": proxy['status']}
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
        asyncio.run_coroutine_threadsafe(sec.play_beep(), bot.loop)
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
        asyncio.run_coroutine_threadsafe(
            bot.send_message(f"{bot.prefix}cash", skip_typing=True, priority=True),
            bot.loop
        )
        state.log_command("CMD", "Manual Cash Check Sent", "info", bot_name=bot.username)
        
    return jsonify({'success': True})

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
    
    asyncio.run_coroutine_threadsafe(
        bot.send_message(full_command, skip_typing=True, priority=True), 
        bot.loop
    )
    
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

    try:
        future = asyncio.run_coroutine_threadsafe(temp_solver.get_balance(), bot.loop)
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
    
    asyncio.run_coroutine_threadsafe(
        bot.send_message(command, skip_typing=True, priority=True), 
        bot.loop
    )
    state.log_command("CMD", f"Manual command sent: {command}", bot_name=bot.username)
    return jsonify({'success': True, 'message': f'Command sent: {command}'})

_pending_captchas = {}

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
