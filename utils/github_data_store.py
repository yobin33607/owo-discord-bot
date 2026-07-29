"""
Limey GitHub Data Store
=======================
Centralized module that stores all bot configuration and data
in the GitHub repo yobin33607/data instead of local files.

The GitHub access token is encrypted at rest and decrypted at
runtime using a Fernet key. The key must be provided via the
environment variable LIMEY_GITHUB_ENCRYPTION_KEY (or loaded from
a .env file in the project root).

Usage:
    from utils.github_data_store import ghd

    # Read a JSON file from the repo
    data = ghd.read_json("config/settings.json")

    # Write a JSON file to the repo
    ghd.write_json("config/settings.json", {"key": "value"})

    # Delete a file from the repo
    ghd.delete_file("config/settings.json")

    # Check if a file exists in the repo
    if ghd.exists("config/accounts.json"):
        ...
"""

import json
import os
import time
import base64
import logging

_log = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]


# ── Encrypted token ────────────────────────────────────
# The GitHub token is stored encrypted using Fernet (symmetric encryption).
# It is decrypted at runtime using the key from the LIMEY_GITHUB_ENCRYPTION_KEY
# environment variable. If that variable is not set, falls back to the
# LIMEY_GITHUB_TOKEN environment variable (plaintext), which is also used
# if the decryption key cannot be loaded (legacy support).

_ENCRYPTED_TOKEN = "gAAAAABqao1N8uCz3BvHsQzG463QEN1uvVJR17MT4OmEqvLtxmzq-fjMKmbbJqXylysKzh-7ei5pZOc_X3WHDvOYiPm3uo3fSu8Rbo_tWrmCXxMujzffx1XqWfvvU4Csw81atg5yZKMmMxDA5YCa0sbgDcXPTOCWRU2RGK5oeMO4I_DwLLf4xPnxjp55Qb3Ki3AoGOQl_gIy"


def _load_env():
    """Load .env file from project root if it exists."""
    try:
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip().strip("'\"")
                        os.environ.setdefault(key, value)
    except Exception:
        pass


def _resolve_token() -> str:
    """Decrypt the GitHub token or fall back to plaintext env var.

    Resolution order:
    1. LIMEY_GITHUB_ENCRYPTION_KEY env var -> decrypt _ENCRYPTED_TOKEN
    2. LIMEY_GITHUB_TOKEN env var (plaintext, legacy)
    3. Prompt the user interactively
    """
    # Try decryption first
    encryption_key = os.environ.get("LIMEY_GITHUB_ENCRYPTION_KEY")
    if encryption_key:
        try:
            from cryptography.fernet import Fernet
            cipher = Fernet(encryption_key.encode())
            decrypted = cipher.decrypt(_ENCRYPTED_TOKEN.encode())
            return decrypted.decode()
        except Exception as e:
            _log.warning(f"Failed to decrypt GitHub token: {e}")

    # Fall back to plaintext env var (legacy)
    token = os.environ.get("LIMEY_GITHUB_TOKEN")
    if token:
        return token

    # Prompt user as last resort
    try:
        token = input("[Limey] Enter your GitHub token (or set LIMEY_GITHUB_ENCRYPTION_KEY env var): ").strip()
        if token:
            return token
    except (EOFError, KeyboardInterrupt):
        pass

    _log.error("No GitHub token available! Set LIMEY_GITHUB_ENCRYPTION_KEY or LIMEY_GITHUB_TOKEN.")
    return ""


# Load .env file before resolving the token
_load_env()


GITHUB_TOKEN = _resolve_token()
GITHUB_REPO = "yobin33607/data"
GITHUB_BRANCH = "main"
API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}/contents"

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

# ── Cache ──────────────────────────────────────────────

_cache: dict[str, dict] = {}  # path -> {data, sha, fetched_at}
_cache_ttl = 5.0  # seconds before re-fetching

# Track recently written paths to avoid stale cache
_written_paths: set[str] = set()

# ── Internal helpers ──────────────────────────────────


def _get_sha(path: str) -> str | None:
    """Get the SHA of an existing file in the repo (for updates)."""
    if path in _cache and _cache[path].get("sha"):
        return _cache[path]["sha"]
    try:
        r = requests.get(f"{API_BASE}/{path}", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json().get("sha")
    except Exception as e:
        _log.warning(f"Failed to get SHA for {path}: {e}")
    return None


def _read_raw(path: str) -> tuple[dict | None, str | None, bool]:
    """Read a file from GitHub. Returns (parsed_data, sha, exists)."""
    try:
        r = requests.get(f"{API_BASE}/{path}", headers=HEADERS, timeout=10)
        if r.status_code == 404:
            return None, None, False
        if r.status_code != 200:
            _log.warning(f"GitHub read failed for {path}: HTTP {r.status_code}")
            return None, None, False

        data = r.json()
        sha = data.get("sha")
        content_b64 = data.get("content", "")
        encoding = data.get("encoding", "")

        if encoding == "base64":
            try:
                decoded = base64.b64decode(content_b64).decode("utf-8")
                parsed = json.loads(decoded)
                return parsed, sha, True
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                _log.warning(f"Failed to decode {path}: {e}")
                return None, sha, True

        return None, sha, True
    except Exception as e:
        _log.warning(f"GitHub read error for {path}: {e}")
        return None, None, False


def _write_raw(path: str, data: dict | list, message: str = "") -> bool:
    """Write a JSON-serializable object to a file in GitHub."""
    try:
        content_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        content_b64 = base64.b64encode(content_bytes).decode("utf-8")

        sha = _get_sha(path)
        commit_message = message or f"Update {path} via Limey GitHub Data Store"

        body: dict = {
            "message": commit_message,
            "content": content_b64,
            "branch": GITHUB_BRANCH,
        }
        if sha:
            body["sha"] = sha

        method = "PUT"
        r = requests.request(method, f"{API_BASE}/{path}", headers=HEADERS, json=body, timeout=15)

        if r.status_code in (200, 201):
            # Update cache
            new_sha = r.json().get("content", {}).get("sha", sha)
            _cache[path] = {
                "data": data,
                "sha": new_sha,
                "fetched_at": time.time(),
            }
            _written_paths.add(path)
            return True
        else:
            _log.warning(f"GitHub write failed for {path}: HTTP {r.status_code} - {r.text[:200]}")
            return False
    except Exception as e:
        _log.warning(f"GitHub write error for {path}: {e}")
        return False


# ── Public API ─────────────────────────────────────────


class GitHubDataStore:
    """GitHub-backed data store for Limey configuration and data files."""

    def __init__(self):
        self.token = GITHUB_TOKEN
        self.repo = GITHUB_REPO
        self.branch = GITHUB_BRANCH
        self.api_base = API_BASE
        self.headers = HEADERS
        self._cache = _cache
        self._cache_ttl = _cache_ttl
        self._written_paths = _written_paths

    # ── JSON operations ────────────────────────────────

    def read_json(self, path: str, default: dict | None = None) -> dict | list | None:
        """Read and parse a JSON file from the data repo.

        Args:
            path: File path within the repo (e.g. 'config/settings.json')
            default: Value returned if the file doesn't exist or can't be read

        Returns:
            Parsed JSON data, or default if not found
        """
        # Check cache first
        now = time.time()
        if path in self._cache and path not in self._written_paths:
            cached = self._cache[path]
            if now - cached.get("fetched_at", 0) < self._cache_ttl:
                return cached.get("data")

        parsed, sha, exists = _read_raw(path)
        if not exists:
            return default

        if parsed is not None:
            self._cache[path] = {
                "data": parsed,
                "sha": sha,
                "fetched_at": now,
            }
        return parsed

    def write_json(self, path: str, data: dict | list, message: str = "") -> bool:
        """Write a JSON-serializable object to the data repo.

        Args:
            path: File path within the repo (e.g. 'config/settings.json')
            data: JSON-serializable dict or list
            message: Optional commit message

        Returns:
            True if the write succeeded
        """
        return _write_raw(path, data, message=message)

    def delete_file(self, path: str, message: str = "") -> bool:
        """Delete a file from the data repo.

        Args:
            path: File path within the repo
            message: Optional commit message

        Returns:
            True if the deletion succeeded
        """
        try:
            sha = _get_sha(path)
            if not sha:
                return False

            commit_message = message or f"Delete {path} via Limey GitHub Data Store"
            r = requests.delete(
                f"{API_BASE}/{path}",
                headers=HEADERS,
                json={"message": commit_message, "sha": sha, "branch": GITHUB_BRANCH},
                timeout=15,
            )
            if r.status_code in (200, 204):
                self._cache.pop(path, None)
                self._written_paths.discard(path)
                return True
            return False
        except Exception as e:
            _log.warning(f"GitHub delete error for {path}: {e}")
            return False

    def exists(self, path: str) -> bool:
        """Check if a file exists in the data repo."""
        # Check cache
        if path in self._cache:
            return True
        sha = _get_sha(path)
        return sha is not None

    def list_files(self, prefix: str = "") -> list[str]:
        """List files in a directory in the data repo.

        Args:
            prefix: Directory path prefix (e.g. 'config/')

        Returns:
            List of file paths
        """
        try:
            url = f"{API_BASE}/{prefix}" if prefix else API_BASE
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                return []
            items = r.json()
            if not isinstance(items, list):
                return []
            return [item["path"] for item in items if item.get("type") == "file"]
        except Exception as e:
            _log.warning(f"GitHub list error for {prefix}: {e}")
            return []

    # ── Utility ────────────────────────────────────────

    def migrate_local_file(self, local_path: str, repo_path: str) -> bool:
        """Upload a local file to the data repo.

        Args:
            local_path: Path to the local file
            repo_path: Destination path in the repo

        Returns:
            True if migration succeeded
        """
        if not os.path.exists(local_path):
            _log.warning(f"Local file not found: {local_path}")
            return False

        try:
            with open(local_path, "r") as f:
                data = json.load(f)
            return self.write_json(repo_path, data, message=f"Migrate {repo_path} from local storage")
        except Exception as e:
            _log.warning(f"Failed to migrate {local_path}: {e}")
            return False

    def clear_cache(self, path: str | None = None):
        """Clear the memory cache for a specific path or all paths."""
        if path:
            self._cache.pop(path, None)
            self._written_paths.discard(path)
        else:
            self._cache.clear()
            self._written_paths.clear()

    @property
    def cache_size(self) -> int:
        return len(self._cache)


# ── Singleton instance ────────────────────────────────

ghd = GitHubDataStore()
