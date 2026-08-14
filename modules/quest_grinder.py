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
Limey - https://github.com/limeyself/owo-discord-bot
"""


"""
Discord Orb Grinder
───────────────────
Python port of `Discord-Quest-Auto-Completion-Selfbot` (TypeScript) merged into
the Limey codebase. Automates Discord's official Quests API to earn Discord Orbs:

  * GET    /quests/@me                 → list current quests
  * POST   /quests/{id}/enroll         → enroll in a quest
  * POST   /quests/{id}/video-progress → spoof video watch time (video quests)
  * POST   /quests/{id}/heartbeat      → spoof game play time (play quests)
  * POST   /quests/{id}/claim-reward   → claim the reward (Discord Orbs)

Behavior:
  * Auto mode enrolls + auto-progresses every available quest.
  * Rewards are claimed manually from the dashboard (claiming often trips a
    hCaptcha that third-party solvers cannot handle reliably).
"""

import asyncio
import base64
import json
import random
import time
import uuid
from collections import deque
from datetime import datetime

import aiohttp

API_BASE = "https://discord.com/api/v9"

# Task types in priority order (same as the TypeScript original).
TASK_ORDER = [
    "WATCH_VIDEO",
    "PLAY_ON_DESKTOP",
    "PLAY_ON_XBOX",
    "PLAY_ON_PLAYSTATION",
    "STREAM_ON_DESKTOP",
    "PLAY_ACTIVITY",
    "WATCH_VIDEO_ON_MOBILE",
    "ACHIEVEMENT_IN_ACTIVITY",
]

# Quest types we cannot automate headlessly.
UNSUPPORTED_TASKS = ("STREAM_ON_DESKTOP", "ACHIEVEMENT_IN_ACTIVITY")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) discord/1.0.9236 Chrome/138.0.7204.251 "
    "Electron/37.6.0 Safari/537.36"
)
ANDROID_USER_AGENT = "Discord-Android/316011;RNA"


def _build_properties(android=False):
    """Client properties injected into x-super-properties (mimics desktop/mobile client)."""
    if android:
        return {
            "os": "Android",
            "browser": "Discord Android",
            "device": "b0q",
            "system_locale": "en-US",
            "has_client_mods": False,
            "client_version": "316.11 - rn",
            "release_channel": "googleRelease",
            "device_vendor_id": str(uuid.uuid4()),
            "design_id": 2,
            "browser_user_agent": "",
            "browser_version": "",
            "os_version": "28",
            "client_build_number": 5169,
            "client_event_source": None,
            "client_launch_id": str(uuid.uuid4()),
            "launch_signature": "1771754995045142953",
            "client_app_state": "active",
            "client_heartbeat_session_id": str(uuid.uuid4()),
        }
    return {
        "os": "Windows",
        "browser": "Discord Client",
        "release_channel": "stable",
        "client_version": "1.0.9236",
        "os_version": "10.0.19045",
        "os_arch": "x64",
        "app_arch": "x64",
        "system_locale": "en-US",
        "has_client_mods": False,
        "client_launch_id": str(uuid.uuid4()),
        "browser_user_agent": USER_AGENT,
        "browser_version": "37.6.0",
        "os_sdk_version": "19045",
        "client_build_number": 539951,
        "native_build_number": 81687,
        "client_event_source": None,
        "launch_signature": str(uuid.uuid4()),
        "client_heartbeat_session_id": str(uuid.uuid4()),
        "client_app_state": "focused",
    }


def _super_properties(android=False):
    return base64.b64encode(
        json.dumps(_build_properties(android), separators=(",", ":")).encode()
    ).decode()


def _make_headers(token, android=False):
    return {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": ANDROID_USER_AGENT if android else USER_AGENT,
        "X-Super-Properties": _super_properties(android),
        "accept-language": "en-US",
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/channels/@me",
        "X-Discord-Locale": "en-US",
        "X-Discord-Timezone": "UTC",
        "X-Debug-Options": "bugReporterEnabled",
        "X-Discord-Features": "quests",
    }


class QuestGrinder:
    """Per-account Discord Quests / Orb grinder.

    Attached to a `LimeyBot` instance as `bot.quest_grinder`. Runs its own
    background loop that keeps quest data fresh and (in auto mode) enrolls and
    progresses quests until completion. Rewards are claimed via `claim_quest`.
    """

    def __init__(self, bot):
        self.bot = bot
        self.quests = []                 # normalized quest dicts (dashboard-facing)
        self.auto_enabled = False
        self.last_fetch = 0.0
        self.orbs_earned = 0
        self.rewards_earned = 0
        self.enrollment_blocked_until = None
        self.logs = deque(maxlen=150)
        self._loop_task = None
        self._quest_tasks = {}           # quest_id -> asyncio.Task
        self._fetching = False

    # ── public control (called from the dashboard via run_coroutine_threadsafe) ──

    async def run(self):
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._worker())

    async def shutdown(self):
        """Cancel the background worker and in-flight quest tasks so the bot's
        object graph can actually be freed when it's disconnected (memory
        watchdog) instead of lingering up to 15s on the next active-check."""
        tasks = []
        if self._loop_task and not self._loop_task.done():
            tasks.append(self._loop_task)
        for t in list(self._quest_tasks.values() or []):
            if t and not t.done():
                tasks.append(t)
        for t in tasks:
            t.cancel()
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=10
                )
            except Exception:
                pass
        # Second pass: _worker may have been mid-`_spawn_quest_tasks` when
        # cancelled and created a quest task after our snapshot — cancel any
        # stragglers that ended up in the dict so nothing pins the bot.
        for t in list(self._quest_tasks.values() or []):
            if t and not t.done():
                t.cancel()
        self._loop_task = None
        self._quest_tasks.clear()

    async def set_auto(self, enabled):
        if bool(enabled) == self.auto_enabled:
            await self.refresh()
            return
        self.auto_enabled = bool(enabled)
        if self.auto_enabled:
            self._log("INFO", "Auto grinding ENABLED")
            await self.refresh()
        else:
            self._log("INFO", "Auto grinding DISABLED")
            for task in list(self._quest_tasks.values()):
                task.cancel()
            self._quest_tasks.clear()
            # In-flight progress persists server-side, so just mark statuses back.
            for q in self.quests:
                if q["status"] in ("enrolling", "progressing"):
                    q["status"] = "available"
                    q["status_detail"] = ""

    async def refresh(self):
        await self.fetch_quests(force=True)
        if self.auto_enabled:
            self._spawn_quest_tasks()

    async def retry_quest(self, quest_id):
        quest = self._get_quest(quest_id)
        if not quest:
            return "Quest not found"
        if quest["status"] in ("claimable", "claimed", "progressing", "enrolling"):
            return "Quest is already being handled"
        quest["status"] = "available"
        quest["status_detail"] = ""
        self._log("INFO", f"Retrying quest \"{quest['name']}\"")
        await self._start_quest_task(quest_id)
        return None

    async def claim_quest(self, quest_id):
        quest = self._get_quest(quest_id)
        if not quest:
            return "Quest not found"
        if quest["status"] == "claimed":
            return "Reward already claimed"
        if not quest["completed"]:
            return "Quest is not completed yet"
        quest["status"] = "claiming"
        quest["status_detail"] = ""
        body = {
            "platform": quest.get("platform", 11),
            "location": 11,  # QUEST_HOME_DESKTOP
            "is_targeted": False,
            "metadata_raw": None,
            "metadata_sealed": None,
            "traffic_metadata_raw": quest.get("traffic_metadata_raw"),
            "traffic_metadata_sealed": quest.get("traffic_metadata_sealed"),
        }
        try:
            status, data = await self._request(
                "POST", f"/quests/{quest_id}/claim-reward", json_body=body
            )
        except Exception as e:
            quest["status"] = "error"
            quest["status_detail"] = f"Claim error: {e}"
            self._log("ERROR", f"Failed to claim \"{quest['name']}\": {e}")
            return None

        if status in (200, 201, 204):
            quest["claimed"] = True
            quest["status"] = "claimed"
            self.rewards_earned += 1
            orb_qty = quest.get("orb_quantity", 0) or 0
            self.orbs_earned += orb_qty
            self._log(
                "SUCCESS",
                f"Claimed reward for \"{quest['name']}\""
                + (f" (+{orb_qty} orbs)" if orb_qty else ""),
            )
            await self._refresh_quest_status(quest_id)
            return None

        if isinstance(data, dict) and data.get("captcha_key"):
            quest["status"] = "needs_captcha"
            quest["status_detail"] = "Discord requires a captcha to claim this reward"
            self._log(
                "WARN",
                f"\"{quest['name']}\": captcha required to claim — retry later",
            )
            return None

        detail = data.get("message") if isinstance(data, dict) else ""
        quest["status"] = "error"
        quest["status_detail"] = f"HTTP {status}: {detail or 'claim failed'}"
        self._log("ERROR", f"Failed to claim \"{quest['name']}\": {quest['status_detail']}")
        return None

    # ── dashboard-facing snapshot ────────────────────────────────────────────

    def status_dict(self):
        snap = list(self.quests)
        quests_out = []
        for q in snap:
            item = dict(q)
            item["progress_percent"] = (
                round(item["current"] / item["target"] * 100, 1)
                if item.get("target")
                else 0
            )
            quests_out.append(item)
        return {
            "quests": quests_out,
            "auto_enabled": self.auto_enabled,
            "orbs_earned": self.orbs_earned,
            "rewards_earned": self.rewards_earned,
            "last_fetch": self.last_fetch,
            "enrollment_blocked_until": self.enrollment_blocked_until,
            "running": sum(1 for t in self._quest_tasks.values() if not t.done()),
            "logs": list(self.logs)[:100],
            "account_ready": bool(getattr(self.bot, "is_ready", False)),
            "account_name": getattr(self.bot, "username", "Unknown"),
        }

    # ── background worker ────────────────────────────────────────────────────

    async def _worker(self):
        try:
            await self.bot.wait_until_ready()
        except Exception:
            pass
        while not getattr(self.bot, "is_ready", False) and getattr(
            self.bot, "active", True
        ):
            await asyncio.sleep(1)
        self._log("INFO", "Orb Grinder ready")
        while getattr(self.bot, "active", True):
            try:
                if self.auto_enabled:
                    await self.fetch_quests()
                    self._spawn_quest_tasks()
            except Exception as e:
                self._log("ERROR", f"Grinder loop error: {e}")
            await asyncio.sleep(15)

    # ── fetching / normalizing ───────────────────────────────────────────────

    async def fetch_quests(self, force=False):
        now = time.time()
        if not force and now - self.last_fetch < 300:
            return
        if self._fetching:
            return
        self._fetching = True
        try:
            status, data = await self._request("GET", "/quests/@me")
            if status == 200:
                self.enrollment_blocked_until = data.get(
                    "quest_enrollment_blocked_until"
                )
                if self.enrollment_blocked_until:
                    self._log(
                        "WARN",
                        f"Quest enrollment blocked until {self.enrollment_blocked_until}",
                    )
                self.last_fetch = now
                raw = data.get("quests", [])
                normalized = [self._normalize_quest(q) for q in raw if q.get("id")]
                old = {q["id"]: q for q in self.quests}
                for nq in normalized:
                    prev = old.get(nq["id"])
                    if prev and prev["status"] in ("enrolling", "progressing", "claiming"):
                        nq["status"] = prev["status"]
                        nq["status_detail"] = prev["status_detail"]
                    elif prev and prev["status"] == "needs_captcha":
                        nq["status"] = prev["status"]
                        nq["status_detail"] = prev["status_detail"]
                self.quests = normalized
                self._log("INFO", f"Fetched {len(normalized)} quest(s)")
            elif status == 401:
                self._log("ERROR", "Quest API auth failed (401) — invalid token?")
            else:
                self._log("ERROR", f"Failed to fetch quests: HTTP {status}")
        except Exception as e:
            self._log("ERROR", f"Fetch quests error: {e}")
        finally:
            self._fetching = False

    def _normalize_quest(self, q):
        cfg = q.get("config", {}) or {}
        msgs = cfg.get("messages", {}) or {}
        tasks = (cfg.get("task_config_v2", {}) or {}).get("tasks", {}) or {}
        task_type = next((t for t in TASK_ORDER if tasks.get(t) is not None), None)
        target = tasks.get(task_type, {}).get("target", 0) if task_type else 0

        us = q.get("user_status") or {}
        progress = us.get("progress", {}) or {}
        current = 0
        if task_type and progress.get(task_type):
            try:
                current = int(progress[task_type].get("value", 0) or 0)
            except (TypeError, ValueError):
                current = 0

        rewards_cfg = cfg.get("rewards_config", {}) or {}
        rewards = rewards_cfg.get("rewards", []) or []
        orb_qty = 0
        reward_name = ""
        for r in rewards:
            rq = r.get("orb_quantity", 0) or 0
            if rq > orb_qty:
                orb_qty = rq
            rn = (r.get("messages", {}) or {}).get("name", "")
            if rn:
                reward_name = rn

        enrolled = bool(us.get("enrolled_at"))
        completed = bool(us.get("completed_at"))
        claimed = bool(us.get("claimed_at"))
        expires_at = cfg.get("expires_at")

        if claimed:
            status = "claimed"
        elif completed:
            status = "claimable"
        elif self._is_expired(expires_at):
            status = "expired"
        elif task_type in UNSUPPORTED_TASKS or task_type is None:
            status = "unsupported"
        else:
            status = "available"

        return {
            "id": q.get("id"),
            "name": msgs.get("quest_name", "Unknown Quest"),
            "game": msgs.get(
                "game_title", (cfg.get("application", {}) or {}).get("name", "Unknown Game")
            ),
            "publisher": msgs.get("game_publisher", ""),
            "application_id": (cfg.get("application", {}) or {}).get("id", ""),
            "task_type": task_type or "UNKNOWN",
            "target": target,
            "current": min(current, target) if target else current,
            "enrolled": enrolled,
            "completed": completed,
            "claimed": claimed,
            "enrolled_at": us.get("enrolled_at"),
            "expires_at": expires_at,
            "reward_name": reward_name,
            "orb_quantity": orb_qty,
            "platform": (rewards_cfg.get("platforms") or [11])[0],
            "traffic_metadata_raw": q.get("traffic_metadata_raw"),
            "traffic_metadata_sealed": q.get("traffic_metadata_sealed"),
            "is_android": bool(tasks.get("WATCH_VIDEO_ON_MOBILE"))
            and not bool(tasks.get("WATCH_VIDEO")),
            "status": status,
            "status_detail": "",
        }

    @staticmethod
    def _is_expired(expires_at):
        if not expires_at:
            return False
        try:
            exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            return exp.timestamp() < time.time()
        except (ValueError, TypeError):
            return False

    # ── quest automation ─────────────────────────────────────────────────────

    def _spawn_quest_tasks(self):
        if not self.auto_enabled:
            return
        for quest in self.quests:
            if quest["status"] == "available":
                self._start_quest_task(quest["id"])

    def _start_quest_task(self, quest_id):
        existing = self._quest_tasks.get(quest_id)
        if existing and not existing.done():
            return
        task = asyncio.create_task(self._do_quest(quest_id))
        self._quest_tasks[quest_id] = task
        task.add_done_callback(lambda t, _id=quest_id: self._quest_tasks.pop(_id, None))

    async def _do_quest(self, quest_id):
        quest = self._get_quest(quest_id)
        if not quest:
            return
        if quest["status"] in ("claimable", "claimed", "expired", "unsupported"):
            return
        quest["status"] = "enrolling"
        quest["status_detail"] = ""
        try:
            if not quest["enrolled"]:
                ok, detail = await self._enroll(quest)
                if not ok:
                    quest["status"] = "error"
                    quest["status_detail"] = detail
                    self._log("WARN", f"{quest['name']}: {detail}")
                    return
                quest["enrolled"] = True
                quest["status"] = "progressing"

            task_type = quest["task_type"]
            if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
                await self._watch_video_loop(quest)
            elif task_type in ("PLAY_ON_DESKTOP", "PLAY_ON_XBOX", "PLAY_ON_PLAYSTATION"):
                await self._play_loop(quest)
            elif task_type == "PLAY_ACTIVITY":
                await self._play_activity_loop(quest)
            else:
                quest["status"] = "unsupported"
                quest["status_detail"] = f"Cannot automate {task_type} quests"

            await self._refresh_quest_status(quest_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            quest["status"] = "error"
            quest["status_detail"] = str(e)[:140]
            self._log("ERROR", f"Quest \"{quest['name']}\" failed: {e}")

    async def _enroll(self, quest):
        is_android = quest.get("is_android", False)
        body = {
            "location": 12 if is_android else 11,  # QUEST_HOME_MOBILE : QUEST_HOME_DESKTOP
            "is_targeted": False,
            "metadata_sealed": None,
            "traffic_metadata_raw": quest.get("traffic_metadata_raw"),
            "traffic_metadata_sealed": quest.get("traffic_metadata_sealed"),
        }
        try:
            status, data = await self._request(
                "POST", f"/quests/{quest['id']}/enroll", json_body=body, android=is_android
            )
        except Exception as e:
            return False, f"Enroll error: {e}"
        if status in (200, 201, 204):
            self._log("INFO", f"Enrolled in \"{quest['name']}\"")
            return True, ""
        if status == 429:
            return False, "Enrollment rate limited (45 min cooldown) — try again later"
        if isinstance(data, dict) and data.get("message"):
            return False, f"HTTP {status}: {data['message']}"
        return False, f"Enroll failed (HTTP {status})"

    async def _watch_video_loop(self, quest):
        max_future = 10
        speed = 7
        interval = 7
        target = quest["target"]
        done = quest["current"]
        enrolled_ms = self._parse_enrolled_ms(quest)
        completed = False
        quest["status"] = "progressing"
        while True:
            max_allowed = (int(time.time() * 1000) - enrolled_ms) // 1000 + max_future
            diff = max_allowed - done
            timestamp = done + speed
            if diff >= speed:
                status, res = await self._request(
                    "POST",
                    f"/quests/{quest['id']}/video-progress",
                    json_body={"timestamp": min(target, timestamp + random.random())},
                )
                if status in (200, 201):
                    completed = (res or {}).get("completed_at") is not None
                    done = min(target, timestamp)
                    quest["current"] = done
                else:
                    self._log("WARN", f"{quest['name']}: video-progress HTTP {status}")
            if timestamp >= target:
                break
            await asyncio.sleep(interval)
        if not completed:
            await self._request(
                "POST",
                f"/quests/{quest['id']}/video-progress",
                json_body={"timestamp": target},
            )
        quest["status"] = "claimable"
        quest["status_detail"] = ""
        self._log("SUCCESS", f"Quest \"{quest['name']}\" completed!")

    async def _play_loop(self, quest):
        interval = 20
        target = quest["target"]
        application_id = quest["application_id"]
        deadline = time.time() + max(target, 600) + 300
        quest["status"] = "progressing"
        while not quest["completed"] and time.time() < deadline:
            status, res = await self._request(
                "POST",
                f"/quests/{quest['id']}/heartbeat",
                json_body={"application_id": application_id, "terminal": False},
            )
            if status in (200, 201):
                self._apply_user_status(quest, res)
            else:
                self._log("WARN", f"{quest['name']}: heartbeat HTTP {status}")
            await asyncio.sleep(interval)
        await self._request(
            "POST",
            f"/quests/{quest['id']}/heartbeat",
            json_body={"application_id": application_id, "terminal": True},
        )
        if quest["completed"]:
            quest["status"] = "claimable"
            quest["status_detail"] = ""
            self._log("SUCCESS", f"Quest \"{quest['name']}\" completed!")
        else:
            quest["status"] = "error"
            quest["status_detail"] = "Timed out waiting for play progress"

    async def _play_activity_loop(self, quest):
        interval = 20
        target = quest["target"]
        stream_key = "call:1:1"
        deadline = time.time() + max(target, 600) + 300
        quest["status"] = "progressing"
        while not quest["completed"] and time.time() < deadline:
            status, res = await self._request(
                "POST",
                f"/quests/{quest['id']}/heartbeat",
                json_body={"stream_key": stream_key, "terminal": False},
            )
            if status in (200, 201):
                self._apply_user_status(quest, res)
            else:
                self._log("WARN", f"{quest['name']}: heartbeat HTTP {status}")
            await asyncio.sleep(interval)
        await self._request(
            "POST",
            f"/quests/{quest['id']}/heartbeat",
            json_body={"stream_key": stream_key, "terminal": True},
        )
        if quest["completed"]:
            quest["status"] = "claimable"
            quest["status_detail"] = ""
            self._log("SUCCESS", f"Quest \"{quest['name']}\" completed!")
        else:
            quest["status"] = "error"
            quest["status_detail"] = "Timed out waiting for activity progress"

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _refresh_quest_status(self, quest_id):
        try:
            status, data = await self._request("GET", f"/quests/{quest_id}")
            if status == 200:
                nq = self._normalize_quest(data)
                idx = self._find_quest_index(quest_id)
                if idx is not None:
                    prev = self.quests[idx]
                    # Keep transient statuses so a completed quest does not flip
                    # back to "available" if the server hasn't propagated yet.
                    if prev["status"] in ("claiming",) or (
                        prev["status"] == "claimable" and nq["status"] == "available"
                    ):
                        nq["status"] = prev["status"]
                        nq["status_detail"] = prev["status_detail"]
                    self.quests[idx] = nq
        except Exception:
            pass

    def _apply_user_status(self, quest, res):
        if not isinstance(res, dict):
            return
        quest["completed"] = bool(res.get("completed_at"))
        task_type = quest["task_type"]
        progress = res.get("progress", {}) or {}
        task_prog = progress.get(task_type) or {}
        try:
            quest["current"] = min(
                int(task_prog.get("value", 0) or 0), quest["target"] or 0
            )
        except (TypeError, ValueError):
            pass

    def _parse_enrolled_ms(self, quest):
        enrolled_at = quest.get("enrolled_at")
        if not enrolled_at:
            return int(time.time() * 1000)
        try:
            dt = datetime.fromisoformat(str(enrolled_at).replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            return int(time.time() * 1000)

    def _get_quest(self, quest_id):
        for q in self.quests:
            if q["id"] == quest_id:
                return q
        return None

    def _find_quest_index(self, quest_id):
        for i, q in enumerate(self.quests):
            if q["id"] == quest_id:
                return i
        return None

    def _log(self, level, message):
        entry = {
            "time": time.strftime("%I:%M:%S %p"),
            "timestamp": time.time(),
            "level": level,
            "message": message,
        }
        self.logs.appendleft(entry)
        try:
            self.bot.log(level, f"[Orb Grinder] {message}")
        except Exception:
            pass

    async def _request(self, method, path, json_body=None, android=False):
        if self.bot.session is None:
            self.bot.session = aiohttp.ClientSession()
        headers = _make_headers(self.bot.token, android=android)
        url = f"{API_BASE}{path}"
        timeout = aiohttp.ClientTimeout(total=20)
        async with self.bot.session.request(
            method, url, json=json_body, headers=headers, timeout=timeout
        ) as resp:
            text = await resp.text()
            try:
                data = json.loads(text) if text else None
            except (json.JSONDecodeError, ValueError):
                data = {"raw": text[:500]}
            return resp.status, data
