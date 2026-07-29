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

import sys
import os

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

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from limey_engines.core_engines.setup_engine import LimeySetupEngine
from core.bot import LimeyBot
from dashboard.app import app as flask_app
import core.state as state
from utils import proxy_manager

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
    flask_app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False)


def _start_manager_bot_subprocess():
    """Launch the manager bot as a subprocess (uses standard discord.py)."""
    global _manager_bot_proc

    config_path = os.path.join(state.CONFIG_DIR, 'settings.json')
    try:
        with open(config_path, 'r') as f:
            cfg = json.load(f)
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

async def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    while True:
        show_banner()
        is_termux = detect_platform()
        state.load_account_stats()
        console.print(f"[cyan]Config Directory:[/cyan] {state.CONFIG_DIR}")
        console.print(f"[cyan]Accounts File:[/cyan] {os.path.join(state.CONFIG_DIR, 'accounts.json')}\n")
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
        try:
            acc_path = os.path.join(state.CONFIG_DIR, 'accounts.json')
            with open(acc_path, 'r') as f:
                acc_data = json.load(f)
                accounts = [a for a in acc_data.get('accounts', []) if a.get('enabled', True)]
        except:
            accounts = []
        if not accounts:
            console.print("[bold red]No active accounts? Add some in the Account Manager (Option 2).[/bold red]")
            time.sleep(2)
            continue
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
        tasks = []
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
                bot = LimeyBot(
                    token=token,
                    channels=valid_channels,
                    proxy_url=proxy_url,
                    proxy_auth=proxy_auth,
                    proxy_label=proxy_label,
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