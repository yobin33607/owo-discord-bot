"""
Memory budget manager for multi-account operation.

Every enabled account runs a full discord.py-self client inside the same
process (gateway websocket, aiohttp sessions, caches, schedulers…). On
memory-constrained hosts — Render's free tier is 512 MB — starting too many
accounts OOM-kills the whole process. Render then restarts it, it tries to
start every account again, and the instance crash-loops forever.

This module fixes that by:

1. Working out a safe account budget from a configurable memory limit.
2. Letting startup check the *projected* RSS before each account is started,
   so the bot simply stops starting accounts once the budget is consumed
   (the dashboard stays up instead of the process being killed).
3. Running a runtime watchdog that periodically checks RSS and gracefully
   disconnects the least-active account(s) if usage climbs past a critical
   mark (slow leaks, server churn, etc.).
4. Persisting a "degraded" marker after a constrained boot so a crash-loop
   boot starts with fewer accounts instead of repeating the same OOM.

All knobs live in the `resource_limits` section of settings.json
(Dashboard → Settings), and can be overridden per-deployment with
LIMEY_MEMORY_LIMIT_MB.
"""

import asyncio
import json
import os
import time

try:
    import psutil  # optional — used when available
except ImportError:
    psutil = None

import core.state as state

_DEGRADED_FILE = os.path.join(state.DATA_DIR, "memory_state.json")
_DEGRADED_TTL = 1800  # seconds — a degraded marker older than this is ignored

DEFAULT_LIMITS = {
    "enabled": True,
    "max_accounts": 0,        # 0 = auto (computed from the memory budget)
    "memory_limit_mb": 0,     # 0 = auto-detect (512 on Render, else system total)
    "reserve_mb": 120,        # base: Python runtime + dashboard + manager-bot subprocess
    "per_account_mb": 55,     # avg memory of one discord.py-self client
    "watchdog_interval": 30,  # seconds between watchdog memory checks
    "critical_ratio": 0.80,   # fraction of the limit at which the watchdog disconnects accounts
}


def get_memory_limit_mb():
    """Memory ceiling for this deployment, in MB.

    Priority: LIMEY_MEMORY_LIMIT_MB env var → 512 on Render → system total.
    """
    try:
        env_val = os.environ.get("LIMEY_MEMORY_LIMIT_MB", "").strip()
        if env_val:
            return max(128, int(env_val))
    except (TypeError, ValueError):
        pass
    if os.environ.get("RENDER"):
        # Render's instance limit is 512 MB regardless of what the host reports.
        return 512
    if psutil is not None:
        try:
            return max(256, int(psutil.virtual_memory().total / 1024 / 1024))
        except Exception:
            pass
    return 512


def get_rss_mb():
    """Current process RSS in MB. Returns 0.0 when it can't be measured."""
    if psutil is not None:
        try:
            return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
        except Exception:
            pass
    try:
        with open("/proc/self/statm", "r", encoding="utf-8") as f:
            fields = f.read().split()
        return int(fields[1]) * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024
    except Exception:
        pass
    return 0.0


def load_memory_limits():
    """Load the `resource_limits` section from settings.json over defaults."""
    limits = dict(DEFAULT_LIMITS)
    try:
        from utils.github_data_store import ghd
        cfg = ghd.read_json("config/settings.json", default={}) or {}
        section = cfg.get("resource_limits") or {}
        for key in limits:
            if key in section:
                if key == "enabled":
                    continue  # parsed below — bool("false") is True, not False
                try:
                    limits[key] = type(limits[key])(section[key])
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    # Sanitize + resolve dynamic values (enabled accepts real booleans only —
    # a JSON string "false" must not be treated as enabled)
    _enabled = section.get("enabled", limits.get("enabled", True))
    limits["enabled"] = _enabled if isinstance(_enabled, bool) else str(_enabled).lower() in ("1", "true", "yes", "on")
    limits["max_accounts"] = max(0, int(limits.get("max_accounts") or 0))
    limits["memory_limit_mb"] = int(limits.get("memory_limit_mb") or 0) or get_memory_limit_mb()
    limits["reserve_mb"] = max(0, int(limits.get("reserve_mb") or 0))
    limits["per_account_mb"] = max(10, int(limits.get("per_account_mb") or 0))
    limits["watchdog_interval"] = max(10, int(limits.get("watchdog_interval") or 0))
    try:
        limits["critical_ratio"] = min(0.99, max(0.5, float(limits.get("critical_ratio") or 0.8)))
    except (TypeError, ValueError):
        limits["critical_ratio"] = 0.8
    return limits


def account_budget(limits):
    """How many accounts fit inside the memory budget (honors max_accounts)."""
    budget = limits["memory_limit_mb"] - limits["reserve_mb"]
    if budget <= 0:
        return 0
    auto = max(0, int(budget // limits["per_account_mb"]))
    if limits["max_accounts"]:
        return min(limits["max_accounts"], auto)
    return auto


def can_start_account(limits, rss_mb, started):
    """Return (ok, reason) — call before starting the next account.

    Combines a hard `max_accounts` cap with a projection of the current RSS
    (when measurable) or a conservative estimate (when not).
    """
    if not limits.get("enabled", True):
        return True, None
    max_accounts = limits.get("max_accounts") or 0
    if max_accounts and started >= max_accounts:
        return False, f"max_accounts ({max_accounts}) reached"
    budget = limits["memory_limit_mb"] - limits["reserve_mb"]
    if budget <= 0:
        return False, "memory budget exhausted (reserve >= limit)"
    if rss_mb > 0:
        projected = rss_mb + limits["per_account_mb"]
        if projected > budget:
            return False, (
                f"projected RSS {projected:.0f} MB would exceed the "
                f"{budget:.0f} MB account budget"
            )
    else:
        estimated = limits["reserve_mb"] + limits["per_account_mb"] * (started + 1)
        if estimated > limits["memory_limit_mb"]:
            return False, (
                f"estimated usage {estimated:.0f} MB would exceed the "
                f"{limits['memory_limit_mb']:.0f} MB limit"
            )
    return True, None


# ── Degraded-mode marker ───────────────────────────────
# Stored locally (data/memory_state.json). Render keeps the same disk across
# restarts of an instance, so a crash-loop boot can read it and start with a
# reduced account cap. It expires after _DEGRADED_TTL so it self-heals.

def persist_degraded_state(degraded, started=0):
    """Record whether the last boot ran memory-constrained."""
    try:
        os.makedirs(state.DATA_DIR, exist_ok=True)
        with open(_DEGRADED_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "degraded": bool(degraded),
                "started": int(started),
                "ts": time.time(),
            }, f)
    except Exception:
        pass


def read_degraded_state():
    """Fresh degraded marker dict, or None."""
    try:
        with open(_DEGRADED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("degraded"):
            return None
        if time.time() - data.get("ts", 0) > _DEGRADED_TTL:
            return None
        return data
    except Exception:
        return None


def ensure_resource_limits_defaults():
    """Write `resource_limits` defaults into settings.json once, if missing.

    Makes the section visible/editable in the Dashboard → Settings editor.
    Fails silently — the code always operates on defaults either way.
    """
    try:
        from utils.github_data_store import ghd
        cfg = ghd.read_json("config/settings.json", default={}) or {}
        if isinstance(cfg, dict) and "resource_limits" not in cfg:
            cfg["resource_limits"] = {
                "enabled": True,
                "max_accounts": 0,
                "memory_limit_mb": 0,
                "reserve_mb": DEFAULT_LIMITS["reserve_mb"],
                "per_account_mb": DEFAULT_LIMITS["per_account_mb"],
                "watchdog_interval": DEFAULT_LIMITS["watchdog_interval"],
                "critical_ratio": DEFAULT_LIMITS["critical_ratio"],
            }
            ghd.write_json("config/settings.json", cfg, message="Add resource_limits defaults")
    except Exception:
        pass


# ── Runtime watchdog ───────────────────────────────────

async def disconnect_bot(bot):
    """Gracefully shut down one bot instance and release its resources.

    Returns the bot's display name (for logging).
    """
    name = getattr(bot, "username", "Unknown")
    bot.active = False
    bot.is_ready = False
    try:
        await bot.close()
    except Exception:
        pass
    session = getattr(bot, "session", None)
    if session is not None:
        try:
            await session.close()
        except Exception:
            pass
    try:
        if bot in state.bot_instances:
            state.bot_instances.remove(bot)
    except Exception:
        pass
    return name


def _activity_key(bot):
    """Least-active first: lowest grind time, then fewest commands sent."""
    grind = getattr(bot, "grind_active_time", 0) or 0
    try:
        cmds = (bot.stats or {}).get("total_cmd_count", 0) or 0
    except Exception:
        cmds = 0
    return (grind, cmds)


async def run_memory_watchdog(limits=None, interval=None):
    """Periodically check RSS; disconnect idle accounts above the critical mark.

    This is the last line of defense against a slow OOM: instead of the whole
    process being killed (and crash-looping), the least-active account gets
    cleanly disconnected until usage drops back below the critical mark.
    """
    if limits is None:
        limits = load_memory_limits()
    if not limits.get("enabled", True):
        return
    if interval is None:
        interval = limits.get("watchdog_interval", 30)

    while True:
        await asyncio.sleep(interval)
        try:
            rss = get_rss_mb()
            if rss <= 0:
                continue
            limit = limits["memory_limit_mb"]
            critical = limit * limits["critical_ratio"]
            if rss < critical:
                continue

            active = [b for b in state.bot_instances if getattr(b, "active", False)]
            if not active:
                continue
            bot = min(active, key=_activity_key)
            name = await disconnect_bot(bot)
            msg = (
                f"Memory watchdog: RSS {rss:.0f} MB ≥ critical {critical:.0f} MB — "
                f"disconnected least-active account '{name}' to prevent an OOM restart."
            )
            print(msg)
            try:
                state.log_command("SYS", msg, "warning")
            except Exception:
                pass
            persist_degraded_state(True, len(state.bot_instances))
        except asyncio.CancelledError:
            raise
        except Exception:
            continue


def memory_status():
    """Summary dict for the dashboard system-status endpoint."""
    limits = load_memory_limits()
    return {
        "limit_mb": limits["memory_limit_mb"],
        "reserve_mb": limits["reserve_mb"],
        "per_account_mb": limits["per_account_mb"],
        "max_accounts": limits["max_accounts"],
        "budget_mb": max(0, limits["memory_limit_mb"] - limits["reserve_mb"]),
        "critical_mb": round(limits["memory_limit_mb"] * limits["critical_ratio"], 1),
        "watchdog_enabled": bool(limits.get("enabled", True)),
        "degraded": bool((read_degraded_state() or {}).get("degraded")),
    }
