"""Run a regular Limey Manager Bot gateway shard slice on a worker.

This is deliberately a separate process from worker_agent.py because the
worker agent uses discord.py-self while the Manager Bot uses the vendored
standard discord.py build in manager_bot_discord/.
"""

from __future__ import annotations

import asyncio
import os
import sys


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DISCORD_PATH = os.path.join(PROJECT_ROOT, "manager_bot_discord")
if os.path.isdir(DISCORD_PATH):
    sys.path.insert(0, DISCORD_PATH)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.manager_bot import run_manager_bot


if __name__ == "__main__":
    asyncio.run(run_manager_bot())
