#!/usr/bin/env python3
"""
Standalone runner for the Limey Manager Bot.
Uses standard discord.py (not discord.py-self) so it can use a regular bot token.

Run this as a subprocess from limey.py.
"""

import sys
import os

# ── Point to standard discord.py first ─────────────────
# The manager_bot_discord/ directory has standard discord.py installed.
# It must come before site-packages so it shadows discord.py-self.
_DISCORD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "manager_bot_discord"
)
if os.path.isdir(_DISCORD_PATH):
    sys.path.insert(0, _DISCORD_PATH)

# ── Make the project root importable ───────────────────
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import asyncio
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="[Manager Bot] %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%I:%M:%S %p",
)


def wait_for_dashboard(max_retries=15, delay=2):
    """Wait until the dashboard API is reachable."""
    import urllib.request

    for attempt in range(1, max_retries + 1):
        try:
            urllib.request.urlopen("http://localhost:8000/api/debug_status", timeout=3)
            return True
        except Exception:
            if attempt < max_retries:
                time.sleep(delay)
    return False


def main():
    if not wait_for_dashboard():
        print("[Manager Bot] ❌ Dashboard not reachable at localhost:8000 — exiting")
        sys.exit(1)

    from modules.manager_bot import run_manager_bot

    asyncio.run(run_manager_bot())


if __name__ == "__main__":
    main()
