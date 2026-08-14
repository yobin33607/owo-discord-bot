"""Limey distributed worker agent.

Run this as a Render Background Worker with:
  LIMEY_SERVER_URL=https://limeyself.onrender.com
  LIMEY_WORKER_ENROLLMENT_TOKEN=<one-time token from the dashboard>
  LIMEY_WORKER_NAME=worker-oregon-1

After enrollment the agent stores its revocable worker credential locally.
The agent makes outbound HTTPS requests only; it does not expose a port.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import time
from pathlib import Path

import aiohttp
import requests

log = logging.getLogger("limey.worker")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


class WorkerAgent:
    def __init__(self):
        self.server_url = os.environ.get("LIMEY_SERVER_URL", "").rstrip("/")
        self.enrollment_token = os.environ.get("LIMEY_WORKER_ENROLLMENT_TOKEN", "").strip()
        self.worker_token = os.environ.get("LIMEY_WORKER_TOKEN", "").strip()
        self.worker_id = os.environ.get("LIMEY_WORKER_ID", "").strip() or None
        self.name = os.environ.get("LIMEY_WORKER_NAME", "limey-worker").strip()[:80]
        self.poll_interval = max(2, float(os.environ.get("LIMEY_WORKER_POLL_SECONDS", "5")))
        self.state_path = Path(os.environ.get("LIMEY_WORKER_STATE", "data/worker.json"))
        self.capabilities = ["selfbot", "proxy_test"]
        self.bots = {}
        self.stopping = False
        self.last_error = None
        self._load_state()

    def _load_state(self):
        if self.worker_token:
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.worker_token = data.get("worker_token", "")
            self.worker_id = data.get("worker_id") or self.worker_id
        except (OSError, ValueError):
            pass

    def _save_state(self):
        if not self.worker_token:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({
                "worker_id": self.worker_id,
                "worker_token": self.worker_token,
            }, indent=2), encoding="utf-8")
            try:
                os.chmod(self.state_path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            log.warning("Could not persist worker credential: %s", exc)

    def _request(self, method, path, **kwargs):
        response = requests.request(
            method,
            self.server_url + path,
            timeout=25,
            headers={"Authorization": f"Bearer {self.worker_token}"} if self.worker_token else {},
            **kwargs,
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", response.text)
            except ValueError:
                detail = response.text
            raise RuntimeError(f"HTTP {response.status_code}: {detail[:300]}")
        return response.json()

    async def enroll(self):
        if not self.server_url:
            raise RuntimeError("LIMEY_SERVER_URL is required")
        if self.worker_token:
            return
        if not self.enrollment_token:
            raise RuntimeError("Set LIMEY_WORKER_ENROLLMENT_TOKEN or LIMEY_WORKER_TOKEN")
        data = await asyncio.to_thread(
            self._request,
            "POST",
            "/api/worker/enroll",
            json={
                "enrollment_token": self.enrollment_token,
                "name": self.name,
                "capabilities": self.capabilities,
                "resources": self.resource_snapshot(),
            },
        )
        self.worker_token = data["worker_token"]
        self.worker_id = data["worker_id"]
        self.enrollment_token = ""
        self._save_state()
        log.info("Enrolled as %s (%s)", data.get("name"), self.worker_id)

    def resource_snapshot(self):
        try:
            import psutil
            process = psutil.Process()
            return {
                "cpu_count": psutil.cpu_count() or 1,
                "memory_total_mb": round(psutil.virtual_memory().total / 1048576),
                "memory_used_mb": round(psutil.virtual_memory().used / 1048576),
                "process_memory_mb": round(process.memory_info().rss / 1048576),
                "cpu_percent": process.cpu_percent(interval=None),
            }
        except Exception:
            return {"cpu_count": os.cpu_count() or 1}

    def account_snapshot(self):
        result = []
        for assignment_id, entry in self.bots.items():
            bot = entry["bot"]
            result.append({
                "id": assignment_id,
                "name": getattr(bot, "username", entry["assignment"].get("name", assignment_id)),
                "user_id": str(getattr(getattr(bot, "user", None), "id", "") or ""),
                "ready": bool(getattr(bot, "is_ready", False)),
                "paused": bool(getattr(bot, "paused", False)),
            })
        return result

    async def heartbeat(self):
        if not self.worker_token:
            return
        await asyncio.to_thread(
            self._request,
            "POST",
            "/api/worker/heartbeat",
            json={
                "capabilities": self.capabilities,
                "resources": self.resource_snapshot(),
                "active_accounts": self.account_snapshot(),
                "last_error": self.last_error,
            },
        )
        self.last_error = None

    @staticmethod
    def _fingerprint(assignment):
        material = {
            key: assignment.get(key)
            for key in ("id", "name", "token", "channels", "guild_id", "proxy")
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

    async def _stop_account(self, assignment_id):
        entry = self.bots.pop(assignment_id, None)
        if not entry:
            return
        bot = entry["bot"]
        bot.active = False
        try:
            await bot.close()
        except Exception:
            pass
        task = entry.get("task")
        if task and not task.done():
            task.cancel()
        log.info("Stopped assigned account %s", assignment_id)

    async def _start_account(self, assignment):
        from core.bot import LimeyBot

        assignment_id = str(assignment["id"])
        proxy = assignment.get("proxy") or {}
        proxy_url = proxy.get("url") or None
        proxy_auth = None
        if proxy.get("username"):
            proxy_auth = aiohttp.BasicAuth(proxy.get("username", ""), proxy.get("password", ""))
        bot = LimeyBot(
            token=assignment["token"],
            channels=assignment.get("channels") or [],
            proxy_url=proxy_url,
            proxy_auth=proxy_auth,
            proxy_label=proxy.get("label") or "direct",
            guild_id=assignment.get("guild_id"),
            guild_name=assignment.get("guild_name"),
        )
        bot.worker_assignment_id = assignment_id
        task = asyncio.create_task(bot.run_bot(), name=f"limey-account-{assignment_id}")
        self.bots[assignment_id] = {
            "bot": bot,
            "task": task,
            "assignment": assignment,
            "fingerprint": self._fingerprint(assignment),
        }
        log.info("Started assigned account %s", assignment.get("name", assignment_id))

    async def reconcile_assignments(self, assignments):
        desired = {str(a.get("id")): a for a in assignments if a.get("id") and a.get("token")}
        for assignment_id in list(self.bots):
            current = self.bots[assignment_id]
            wanted = desired.get(assignment_id)
            if not wanted or self._fingerprint(wanted) != current["fingerprint"]:
                await self._stop_account(assignment_id)
        for assignment_id, assignment in desired.items():
            if assignment_id not in self.bots:
                await self._start_account(assignment)

    async def handle_job(self, job):
        if not job:
            return
        success = False
        result = None
        error = ""
        try:
            kind = job.get("kind")
            payload = job.get("payload") or {}
            if kind == "proxy_test":
                from utils import proxy_manager
                proxy = dict(payload.get("proxy") or {})
                ok = await proxy_manager.test_proxy(proxy)
                result = {"ok": ok, "proxy": proxy}
                success = True
            else:
                error = f"Unsupported worker job: {kind}"
        except Exception as exc:
            error = str(exc)
        try:
            await asyncio.to_thread(
                self._request,
                "POST",
                f"/api/worker/jobs/{job['id']}/result",
                json={"success": success, "result": result, "error": error},
            )
        except Exception as exc:
            log.warning("Could not submit job result: %s", exc)

    async def poll(self):
        return await asyncio.to_thread(self._request, "GET", "/api/worker/poll")

    async def stop(self):
        self.stopping = True
        for assignment_id in list(self.bots):
            await self._stop_account(assignment_id)

    async def run(self):
        await self.enroll()
        while not self.stopping:
            try:
                data = await self.poll()
                await self.reconcile_assignments(data.get("assignments") or [])
                await self.handle_job(data.get("job"))
                await self.heartbeat()
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("Worker loop error: %s", exc)
                if "HTTP 401" in str(exc):
                    raise
            await asyncio.sleep(self.poll_interval)


async def main():
    agent = WorkerAgent()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: setattr(agent, "stopping", True))
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await agent.run()
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
