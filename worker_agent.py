"""Limey distributed worker agent.

Run this as a Render Web Service with:
  LIMEY_SERVER_URL=https://limeyself.onrender.com
  LIMEY_WORKER_ENROLLMENT_TOKEN=<one-time token from the dashboard>
  LIMEY_WORKER_NAME=worker-oregon-1

After enrollment the agent stores its revocable worker credential locally.
The agent makes outbound HTTPS control-plane requests and exposes a small health server on Render's PORT at /health.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import aiohttp
import requests

log = logging.getLogger("limey.worker")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


class _WorkerHealthHandler(BaseHTTPRequestHandler):
    agent = None

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/", "/health"):
            self.send_error(404)
            return
        payload = self.agent.health_payload()
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200 if payload["status"] == "ok" else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        # Render probes / uptime monitors should not flood worker logs.
        return


class WorkerAgent:
    def __init__(self):
        self.server_url = os.environ.get("LIMEY_SERVER_URL", "").rstrip("/")
        self.enrollment_token = os.environ.get("LIMEY_WORKER_ENROLLMENT_TOKEN", "").strip()
        self.worker_token = os.environ.get("LIMEY_WORKER_TOKEN", "").strip()
        self.worker_id = os.environ.get("LIMEY_WORKER_ID", "").strip() or None
        self.name = os.environ.get("LIMEY_WORKER_NAME", "limey-worker").strip()[:80]
        self.poll_interval = max(2, float(os.environ.get("LIMEY_WORKER_POLL_SECONDS", "5")))
        self.state_path = Path(os.environ.get("LIMEY_WORKER_STATE", "data/worker.json"))
        self.capabilities = ["selfbot", "proxy_test", "manager_shards"]
        self.bots = {}
        self.manager_process = None
        self.manager_assignment_fingerprint = None
        self.wake_version = None
        self.health_server = None
        self.health_thread = None
        self.ready = False
        self.last_activity = time.time()
        self.last_poll = None
        self.stopping = False
        self.last_error = None
        self._load_state()

    def start_health_server(self):
        """Bind Render's PORT so this agent can run as a Web Service."""
        raw_port = os.environ.get("PORT", os.environ.get("LIMEY_HEALTH_PORT", "8000"))
        try:
            port = int(raw_port)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid health-server port: {raw_port!r}") from exc
        if not 1 <= port <= 65535:
            raise RuntimeError(f"Health-server port out of range: {port}")
        _WorkerHealthHandler.agent = self
        self.health_server = ThreadingHTTPServer(("0.0.0.0", port), _WorkerHealthHandler)
        self.health_thread = threading.Thread(
            target=self.health_server.serve_forever,
            name="limey-worker-health",
            daemon=True,
        )
        self.health_thread.start()
        log.info("Worker health server listening on 0.0.0.0:%s", port)

    def stop_health_server(self):
        server = self.health_server
        self.health_server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        self.health_thread = None

    def health_payload(self):
        age = time.time() - self.last_activity
        healthy = bool(self.ready and not self.stopping and age <= 90)
        return {
            "status": "ok" if healthy else "degraded",
            "worker_id": self.worker_id,
            "ready": self.ready,
            "last_poll": self.last_poll,
            "last_error": self.last_error,
        }

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
        headers = {"Authorization": f"Bearer {self.worker_token}"} if self.worker_token else {}
        headers.update(kwargs.pop("headers", {}) or {})
        response = requests.request(
            method,
            self.server_url + path,
            timeout=25,
            headers=headers,
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

    @staticmethod
    def _manager_fingerprint(assignment):
        material = {
            "shard_count": assignment.get("shard_count"),
            "shard_ids": assignment.get("shard_ids") or [],
            "token": assignment.get("token"),
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

    def _stop_manager_shards(self):
        process = self.manager_process
        self.manager_process = None
        self.manager_assignment_fingerprint = None
        if not process or process.poll() is not None:
            return
        log.info("Stopping Manager Bot shard process")
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _start_manager_shards(self, assignment):
        shard_ids = [str(value) for value in assignment.get("shard_ids") or []]
        if not shard_ids or not assignment.get("token"):
            return
        env = os.environ.copy()
        env.update({
            "LIMEY_SERVER_URL": self.server_url,
            "LIMEY_MANAGER_DASHBOARD_URL": self.server_url,
            "LIMEY_WORKER_TOKEN": self.worker_token,
            "LIMEY_WORKER_ID": self.worker_id or "",
            "LIMEY_MANAGER_BOT_TOKEN": assignment["token"],
            "LIMEY_MANAGER_SHARD_COUNT": str(assignment["shard_count"]),
            "LIMEY_MANAGER_SHARD_IDS": ",".join(shard_ids),
            "PYTHONUNBUFFERED": "1",
        })
        script = Path(__file__).with_name("worker_manager_shard.py")
        self.manager_process = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(Path(__file__).resolve().parent),
            env=env,
        )
        self.manager_assignment_fingerprint = self._manager_fingerprint(assignment)
        log.info(
            "Started Manager Bot shards %s/%s (PID %s)",
            ",".join(shard_ids), assignment.get("shard_count"), self.manager_process.pid,
        )

    def reconcile_manager_shards(self, assignment):
        if not assignment:
            self._stop_manager_shards()
            return
        if not self.server_url or not self.worker_token:
            self._stop_manager_shards()
            return
        fingerprint = self._manager_fingerprint(assignment)
        process_dead = self.manager_process is not None and self.manager_process.poll() is not None
        if (self.manager_process is None or process_dead
                or fingerprint != self.manager_assignment_fingerprint):
            self._stop_manager_shards()
            self._start_manager_shards(assignment)

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
        headers = {}
        if self.wake_version is not None:
            headers["X-Worker-Wake"] = str(self.wake_version)
        data = await asyncio.to_thread(
            self._request,
            "GET",
            "/api/worker/poll?wait=20",
            headers=headers,
        )
        if data.get("wake_version") is not None:
            self.wake_version = data["wake_version"]
        return data

    async def stop(self):
        self.stopping = True
        self._stop_manager_shards()
        for assignment_id in list(self.bots):
            await self._stop_account(assignment_id)

    async def run(self):
        await self.enroll()
        self.ready = True
        while not self.stopping:
            try:
                self.last_activity = time.time()
                data = await self.poll()
                self.last_activity = time.time()
                self.last_poll = time.time()
                self.reconcile_manager_shards(data.get("manager_shards"))
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
    agent.start_health_server()
    try:
        await agent.run()
    finally:
        await agent.stop()
        agent.stop_health_server()


if __name__ == "__main__":
    asyncio.run(main())
