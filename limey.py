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
"""

import sys
import os

# ── Auto-detect project virtual environment ───────────────────
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# Check if we should re-exec using the project's .venv Python
from utils.platform import exec_venv_or_continue  # type: ignore[import-untyped]
exec_venv_or_continue()

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
import subprocess
import asyncio
import random
import json
import threading
import time
import secrets
from rich.console import Console
from rich.align import Align

sys.path.append(_PROJECT_DIR)

from limey_engines.core_engines.setup_engine import LimeySetupEngine
from core.bot import LimeyBot
from dashboard.app import app as flask_app
import core.state as state
from utils import proxy_manager
from utils import guild_scanner
from utils.github_data_store import ghd

# ── Manager Bot (runs as subprocess with standard discord.py) ────
_manager_bot_proc = None
_manager_bot_available = os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_manager_bot.py"))

console = Console()
engine = LimeySetupEngine()

if not engine.environment_healthy():
    console.print("[yellow]Environment not healthy – running setup...[/yellow]")
    if not engine.run_full_setup(force_bootstrap=True):
        console.print("[red]Setup failed. Please run 'python limey_setup.py' manually.[/red]")
        sys.exit(1)
    console.print("[green]Setup complete. Restarting...[/green]")
    os.execv(sys.executable, [sys.executable] + sys.argv)

def show_banner():
    from limey_ascii import limey_ascii
    limey_ascii.show_banner('main')

def detect_platform():
    if "TERMUX_VERSION" in os.environ or "com.termux" in os.environ.get("PREFIX", ""):
        platform = "Mobile (Termux)"
        is_termux = True
    elif sys.platform.startswith("linux"):
        platform = "Linux (Server/Desktop)"
        is_termux = False
    elif sys.platform == "darwin":
        platform = "MacOS"
        is_termux = False
    elif os.name == "nt":
        platform = "PC (Windows)"
        is_termux = False
    else:
        platform = f"Unknown ({sys.platform})"
        is_termux = False
    console.print(f"[bold green]Detected Platform: {platform}[/bold green]")
    return is_termux

def run_dashboard():
    # Render and similar hosts provide the public listener through PORT.
    try:
        port = int(os.environ.get("PORT", "8000"))
    except (TypeError, ValueError):
        console.print("[yellow]Invalid PORT value; using 8000.[/yellow]")
        port = 8000
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


def _start_manager_bot_subprocess():
    """Launch the manager bot as a subprocess (uses standard discord.py)."""
    global _manager_bot_proc

    try:
        cfg = ghd.read_json("config/settings.json", default={})
        if not cfg:
            console.print("[dim]  Manager Bot config not found — skipping[/dim]")
            return
        mgr_cfg = cfg.get('manager_bot', {})
        token = mgr_cfg.get('token', '')
        if not token:
            console.print("[dim]  Manager Bot not configured — skipping[/dim]")
            return
    except Exception:
        console.print("[dim]  Manager Bot config not found — skipping[/dim]")
        return

    # Generate internal API key for manager bot to authenticate with dashboard
    internal_key = secrets.token_hex(32)
    os.environ["LIMEY_INTERNAL_KEY"] = internal_key

    runner_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_manager_bot.py")
    env = os.environ.copy()
    env["LIMEY_INTERNAL_KEY"] = internal_key
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.Popen(
            [sys.executable, runner_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
        )
        _manager_bot_proc = proc
        console.print(f"[dim]  Manager Bot subprocess started (PID: {proc.pid})[/dim]")

        # Read output in a daemon thread to show manager bot logs
        def _read_output():
            for line in iter(proc.stdout.readline, ''):
                if line:
                    console.print(f"[dim]{line.rstrip()}[/dim]")
            proc.stdout.close()

        threading.Thread(target=_read_output, daemon=True).start()
    except Exception as e:
        console.print(f"[dim]  Failed to start Manager Bot: {e}[/dim]")

async def _pick_and_bind_server(acc):
    """Scan the account's servers and let the user pick one to bind."""
    from rich.prompt import Prompt
    try:
        proxy_url, proxy_auth, _ = proxy_manager.resolve_account_proxy(acc)
        guilds = await guild_scanner.scan_guilds(
            acc.get("token", ""), proxy_url=proxy_url, proxy_auth=proxy_auth
        )
    except guild_scanner.TokenError as e:
        console.print(f"[red]Could not scan servers for '{acc.get('name', 'Unknown')}': {e}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]Server scan failed for '{acc.get('name', 'Unknown')}': {e}[/red]")
        return False
    if not guilds:
        console.print(f"[yellow]No servers found for '{acc.get('name', 'Unknown')}'.[/yellow]")
        return False
    console.print(f"\n[bold cyan]Servers for '{acc.get('name', 'Unknown')}':[/bold cyan]")
    for i, g in enumerate(guilds, 1):
        console.print(f"  [{i}] {g['name']} ({g['id']})")
    choice = Prompt.ask("server no. (0 = all servers)", default="0")
    try:
        idx = int(choice) - 1
    except ValueError:
        return False
    if idx < 0 or idx >= len(guilds):
        acc.pop("guild_id", None)
        acc.pop("guild_name", None)
        console.print("[dim]Server binding removed — will use all servers.[/dim]")
        return True
    acc["guild_id"] = guilds[idx]["id"]
    acc["guild_name"] = guilds[idx]["name"]
    console.print(f"[green]Bound '{acc.get('name')}' to server: {guilds[idx]['name']}[/green]")
    return True


async def maybe_switch_servers():
    """Before login: make sure every account has a server chosen (or switch it).

    Accounts without a binding are asked to pick one (persisted); bound
    accounts can optionally switch. Picking/editing is also available in the
    dashboard (Accounts → Edit → Scan Servers).
    """
    from rich.prompt import Prompt
    try:
        acc_data = ghd.read_json("config/accounts.json", default={"accounts": []})
    except Exception:
        return
    accounts = acc_data.get("accounts", []) if acc_data else []
    if not accounts:
        return
    enabled = [a for a in accounts if a.get("enabled", True)]
    if not enabled:
        return
    unbound = [a for a in enabled if not a.get("guild_id")]
    changed = False
    if unbound:
        console.print(f"[yellow]{len(unbound)} account(s) have no server bound yet.[/yellow]")
        for acc in unbound:
            pick = Prompt.ask(
                f"Choose a server for '{acc.get('name', 'Unknown')}' before login?",
                choices=["y", "n"], default="y"
            )
            if pick == "y" and await _pick_and_bind_server(acc):
                changed = True
    else:
        console.print("[dim]All accounts have a server binding (set in Dashboard → Accounts).[/dim]")
        pick = Prompt.ask("Switch any account's server now?", choices=["y", "n"], default="n")
        if pick == "y":
            for acc in enabled:
                if await _pick_and_bind_server(acc):
                    changed = True
    if changed:
        ghd.write_json("config/accounts.json", {"accounts": accounts}, message="Update server bindings")
        console.print("[green]Server bindings saved.[/green]")


async def start_limey(switch_servers=False):
    """Start all enabled accounts (menu option 1, or `limey.py -1`).

    With switch_servers=True the user is asked to choose/switch each account's
    Discord server before logging on. Returns True if accounts were started,
    False if there was nothing to start.
    """
    try:
        acc_data = ghd.read_json("config/accounts.json", default={"accounts": []})
        if acc_data:
            accounts = [a for a in acc_data.get('accounts', []) if a.get('enabled', True)]
        else:
            accounts = []
    except Exception:
        accounts = []
    if not accounts:
        console.print("[bold red]No active accounts? Add some in the Account Manager (Option 2).[/bold red]")
        return False

    if switch_servers:
        await maybe_switch_servers()
        # Reload — bindings may have changed above
        try:
            acc_data = ghd.read_json("config/accounts.json", default={"accounts": []})
            accounts = [a for a in acc_data.get('accounts', []) if a.get('enabled', True)]
        except Exception:
            accounts = [a for a in accounts if a.get('enabled', True)]
        if not accounts:
            console.print("[bold red]No active accounts after server selection.[/bold red]")
            return False

    import utils.history_tracker as ht
    ht.start_session()
    dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
    dashboard_thread.start()

    # Start the manager bot as a separate subprocess (uses standard discord.py)
    if _manager_bot_available:
        console.print("[dim]Starting Manager Bot subprocess...[/dim]")
        _start_manager_bot_subprocess()

    console.print(f"[bold yellow]Initializing {len(accounts)} accounts...[/bold yellow]")
    bots = []
    for i, acc in enumerate(accounts):
        token = acc.get('token')
        channels = acc.get('channels')
        if not token or "YOUR_TOKEN_HERE" in token or "PLACEHOLDER" in token:
            continue
        valid_channels = []
        if channels:
            for ch in channels:
                if ch and "YOUR_CHANNEL_ID_HERE" not in str(ch) and "PLACEHOLDER" not in str(ch):
                    valid_channels.append(ch)
        try:
            proxy_url, proxy_auth, proxy_label = proxy_manager.resolve_account_proxy(acc)
            guild_id = acc.get('guild_id')
            guild_name = acc.get('guild_name')
            console.print(f"[dim]  Server: {guild_name or 'all servers'}[/dim]")
            bot = LimeyBot(
                token=token,
                channels=valid_channels,
                proxy_url=proxy_url,
                proxy_auth=proxy_auth,
                proxy_label=proxy_label,
                guild_id=guild_id,
                guild_name=guild_name,
            )
            state.bot_instances.append(bot)
            bots.append(bot)
            if i > 0:
                delay = random.uniform(2.5, 4.5)
                console.print(f"[dim]Waiting {delay:.1f}s for next account...[/dim]")
                time.sleep(delay)
            asyncio.create_task(bot.run_bot())
            console.print(f"[green]Starting Account {i+1}/{len(accounts)} ({acc.get('name', 'Unknown')})[/green]")
        except Exception as e:
            console.print(f"[bold red]Failed to initialize Account {i+1}: {e}[/bold red]")
            continue
    console.print("[bold green]All accounts are now connecting in background...[/bold green]")
    return True

async def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    show_banner()
    # Rebuild the browser-extension zip so Dashboard → Extension can serve it.
    try:
        from utils import extension_builder
        _zip, _err = extension_builder.build_extension_zip()
        if _zip:
            console.print(f"[green]Extension zip built: {os.path.basename(_zip)}[/green]")
        else:
            console.print(f"[yellow]Extension zip build skipped ({_err}) — available in Dashboard → Extension[/yellow]")
    except Exception as e:
        console.print(f"[dim]Extension zip build skipped: {e}[/dim]")
    is_termux = detect_platform()
    state.load_account_stats()
    console.print(f"[cyan]Config Directory:[/cyan] {state.CONFIG_DIR}")
    console.print(f"[cyan]Accounts File:[/cyan] {os.path.join(state.CONFIG_DIR, 'accounts.json')}\n")

    # `limey.py -1` → skip the menu and start the bot directly
    if "-1" in sys.argv[1:]:
        console.print("[bold cyan]Direct start mode (-1): launching Limey without the menu...[/bold cyan]")
        if not await start_limey():
            # Keep hosted deployments alive when the remote data store is
            # temporarily unavailable or has no configured accounts. The
            # dashboard remains available so the issue can be repaired instead
            # of causing an endless deploy crash loop.
            if os.environ.get("RENDER") or os.environ.get("LIMEY_KEEP_DASHBOARD_ON_EMPTY") == "1":
                console.print("[yellow]No accounts started; keeping the dashboard online for configuration.[/yellow]")
                dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
                dashboard_thread.start()
            else:
                sys.exit(1)
        while True:
            await asyncio.sleep(60)
        return

    while True:
        console.print("\n[bold cyan]1.[/bold cyan] Start Limey")
        console.print("[bold cyan]2.[/bold cyan] Manage Accounts")
        console.print("[bold cyan]3.[/bold cyan] Exit")
        from rich.prompt import Prompt
        choice = Prompt.ask("\nSelect option", choices=["1", "2", "3"], default="1")
        if choice == "2":
            import limey_setup
            await limey_setup.account_manager()
            continue
        elif choice == "3":
            console.print("\n[yellow]Shutting down. See you next time![/yellow]")
            sys.exit(0)
        if not await start_limey(switch_servers=True):
            time.sleep(2)
            continue
        while True:
            await asyncio.sleep(60)
        break

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            import utils.history_tracker as ht
            ht.end_session()
            state.save_account_stats()
            console.print("\n[bold yellow][!] Systems shut down. History saved.[/bold yellow]")
        except Exception:
            pass