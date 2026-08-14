"""Worker control-plane primitives for distributed Limey processes."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
import uuid
from collections import deque

from utils.github_data_store import ghd


WORKER_CONFIG_PATH = "config/workers.json"
ENROLLMENT_TTL = 15 * 60
HEARTBEAT_TTL = 45
_JOB_REQUEUE_TTL = 120

_lock = threading.RLock()
_wake_condition = threading.Condition(_lock)
_wake_version = 0
_loaded = False
_data = {"workers": [], "enrollment_tokens": []}
_jobs = deque()
_job_history = deque(maxlen=200)


def notify_workers() -> int:
    """Wake long-polling workers after a server-side state change."""
    global _wake_version
    with _wake_condition:
        _wake_version += 1
        _wake_condition.notify_all()
        return _wake_version


def wake_version() -> int:
    with _lock:
        return _wake_version


def wait_for_wake(version: int, timeout: float) -> bool:
    """Wait until the worker state changes or the long-poll timeout expires."""
    timeout = max(0.0, min(float(timeout), 25.0))
    with _wake_condition:
        if _wake_version != version:
            return True
        _wake_condition.wait_for(lambda: _wake_version != version, timeout=timeout)
        return _wake_version != version


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_locked() -> None:
    global _loaded, _data
    if _loaded:
        return
    raw = ghd.read_json(WORKER_CONFIG_PATH, default={}) or {}
    _data = {
        "workers": list(raw.get("workers") or []),
        "enrollment_tokens": list(raw.get("enrollment_tokens") or []),
    }
    _loaded = True


def _save_locked() -> None:
    ghd.write_json(WORKER_CONFIG_PATH, _data, message="Update worker registry")


def _prune_enrollment_locked() -> None:
    now = time.time()
    _data["enrollment_tokens"] = [
        entry for entry in _data["enrollment_tokens"]
        if entry.get("expires_at", 0) > now and not entry.get("used")
    ]


def _public_worker(worker: dict) -> dict:
    now = time.time()
    last_seen = worker.get("last_seen")
    online = bool(last_seen and now - last_seen <= HEARTBEAT_TTL and not worker.get("revoked"))
    return {
        "id": worker.get("id"),
        "name": worker.get("name") or worker.get("id"),
        "created_at": worker.get("created_at"),
        "last_seen": last_seen,
        "online": online,
        "revoked": bool(worker.get("revoked")),
        "capabilities": worker.get("capabilities") or [],
        "resources": worker.get("resources") or {},
        "active_accounts": worker.get("active_accounts") or [],
        "last_error": worker.get("last_error"),
    }


def create_enrollment(label: str = "") -> dict:
    """Create a short-lived one-time enrollment token for the dashboard."""
    with _lock:
        _load_locked()
        _prune_enrollment_locked()
        raw = "lw_enroll_" + secrets.token_urlsafe(32)
        entry = {
            "id": uuid.uuid4().hex,
            "label": (label or "Worker").strip()[:80],
            "hash": _hash(raw),
            "created_at": time.time(),
            "expires_at": time.time() + ENROLLMENT_TTL,
        }
        _data["enrollment_tokens"].append(entry)
        _save_locked()
        notify_workers()
        return {
            "token": raw,
            "label": entry["label"],
            "expires_at": entry["expires_at"],
        }


def enroll(raw_token: str, name: str, capabilities=None, resources=None) -> dict | None:
    """Exchange a one-time enrollment token for a revocable worker credential."""
    with _lock:
        _load_locked()
        _prune_enrollment_locked()
        token_hash = _hash((raw_token or "").strip())
        entry = next((x for x in _data["enrollment_tokens"] if x.get("hash") == token_hash), None)
        if not entry:
            return None

        worker_id = "worker_" + secrets.token_urlsafe(9)
        worker_raw = "lw_worker_" + secrets.token_urlsafe(32)
        worker = {
            "id": worker_id,
            "name": (name or entry.get("label") or worker_id).strip()[:80],
            "token_hash": _hash(worker_raw),
            "created_at": time.time(),
            "last_seen": time.time(),
            "capabilities": list(capabilities or []),
            "resources": dict(resources or {}),
            "active_accounts": [],
            "last_error": None,
            "revoked": False,
        }
        _data["workers"].append(worker)
        _data["enrollment_tokens"] = [x for x in _data["enrollment_tokens"] if x is not entry]
        _save_locked()
        notify_workers()
        return {"worker_id": worker_id, "worker_token": worker_raw, "name": worker["name"]}


def authenticate(raw_token: str) -> dict | None:
    """Return the worker record for a valid worker credential."""
    with _lock:
        _load_locked()
        token_hash = _hash((raw_token or "").strip())
        return next(
            (worker for worker in _data["workers"]
             if worker.get("token_hash") == token_hash and not worker.get("revoked")),
            None,
        )


def heartbeat(raw_token: str, payload: dict) -> dict | None:
    with _lock:
        worker = authenticate(raw_token)
        if not worker:
            return None
        worker["last_seen"] = time.time()
        if isinstance(payload.get("capabilities"), list):
            worker["capabilities"] = payload["capabilities"][:50]
        if isinstance(payload.get("resources"), dict):
            worker["resources"] = payload["resources"]
        if isinstance(payload.get("active_accounts"), list):
            worker["active_accounts"] = payload["active_accounts"][:200]
        worker["last_error"] = str(payload.get("last_error") or "")[:500] or None
        return _public_worker(worker)


def list_workers() -> list[dict]:
    with _lock:
        _load_locked()
        return [_public_worker(worker) for worker in _data["workers"]]


def revoke(worker_id: str) -> bool:
    with _lock:
        _load_locked()
        worker = next((x for x in _data["workers"] if x.get("id") == worker_id), None)
        if not worker:
            return False
        worker["revoked"] = True
        _save_locked()
        notify_workers()
        return True


def enqueue(kind: str, payload: dict, target_worker: str | None = None) -> dict:
    with _lock:
        job = {
            "id": "job_" + uuid.uuid4().hex,
            "kind": (kind or "generic").strip()[:80],
            "payload": payload or {},
            "target_worker": target_worker,
            "status": "queued",
            "created_at": time.time(),
            "claimed_by": None,
            "claimed_at": None,
            "result": None,
        }
        _jobs.append(job)
        notify_workers()
        return {k: v for k, v in job.items() if k != "result"}


def _requeue_stale_locked() -> None:
    now = time.time()
    for job in _jobs:
        if job.get("status") == "claimed" and now - (job.get("claimed_at") or now) > _JOB_REQUEUE_TTL:
            job["status"] = "queued"
            job["claimed_by"] = None
            job["claimed_at"] = None


def claim(worker_id: str, capabilities=None) -> dict | None:
    with _lock:
        _requeue_stale_locked()
        caps = set(capabilities or [])
        for job in _jobs:
            if job.get("status") != "queued":
                continue
            target = job.get("target_worker")
            if target and target != worker_id:
                continue
            required = job.get("payload", {}).get("required_capability")
            if required and required not in caps:
                continue
            job["status"] = "claimed"
            job["claimed_by"] = worker_id
            job["claimed_at"] = time.time()
            return dict(job)
        return None


def complete(worker_id: str, job_id: str, success: bool, result=None, error: str = "") -> dict | None:
    with _lock:
        job = next((x for x in _jobs if x.get("id") == job_id), None)
        if not job or job.get("claimed_by") != worker_id:
            return None
        job["status"] = "completed" if success else "failed"
        job["result"] = result
        job["error"] = str(error or "")[:1000]
        job["completed_at"] = time.time()
        completed = dict(job)
        _job_history.append(completed)
        try:
            _jobs.remove(job)
        except ValueError:
            pass
        return completed


def recent_jobs() -> list[dict]:
    with _lock:
        return [dict(job) for job in reversed(_job_history)]
