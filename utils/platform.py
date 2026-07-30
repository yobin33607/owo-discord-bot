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


import os
import sys
import platform

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_termux():
    if os.environ.get("TERMUX_VERSION"):
        return True
    if "com.termux" in os.environ.get("PREFIX", ""):
        return True
    return os.path.exists("/data/data/com.termux")


def get_platform():
    if is_termux():
        return "termux"
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


def get_python_cmd():
    return sys.executable


def is_venv_python() -> bool:
    """Check if the current process is running from the project's .venv."""
    if hasattr(sys, "real_prefix"):
        return True
    if sys.prefix == sys.base_prefix:
        return False
    venv_dir = os.path.join(_PROJECT_DIR, ".venv")
    if not os.path.exists(venv_dir):
        return False
    expected = os.path.join(venv_dir, "bin", "python")
    if os.name == "nt":
        expected = os.path.join(venv_dir, "Scripts", "python.exe")
    if not os.path.exists(expected):
        return False
    try:
        if os.path.samefile(sys.executable, expected):
            return True
    except (OSError, AttributeError):
        pass
    return os.path.realpath(sys.executable) == os.path.realpath(expected)


def find_venv_python() -> str | None:
    """Return the path to the .venv Python binary, or None if .venv doesn't exist."""
    venv_dir = os.path.join(_PROJECT_DIR, ".venv")
    if not os.path.exists(venv_dir):
        return None
    py = os.path.join(venv_dir, "bin", "python")
    if os.name == "nt":
        py = os.path.join(venv_dir, "Scripts", "python.exe")
    return py if os.path.exists(py) else None


def _venv_is_valid(venv_py: str) -> bool:
    """
    Check if the venv Python binary is actually runnable.
    This catches dead symlinks from a committed venv on a different system.
    """
    import subprocess

    # Binary must exist AND be executable (fails for dead symlinks)
    if not os.path.exists(venv_py) or not os.access(venv_py, os.X_OK):
        return False

    # Must actually run and return a valid version
    try:
        result = subprocess.run(
            [venv_py, "--version"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _setup_venv_pip(venv_py: str) -> None:
    """Upgrade pip and install requirements into the venv."""
    import subprocess

    subprocess.check_call([venv_py, "-m", "pip", "install", "--upgrade", "pip", "--quiet"])

    req_file = os.path.join(_PROJECT_DIR, "requirements.txt")
    if os.path.exists(req_file):
        print("[...] Installing dependencies (this may take a while)...", file=sys.stderr)
        subprocess.check_call([venv_py, "-m", "pip", "install", "-r", req_file, "--no-cache-dir"])


def _rebuild_venv(venv_dir: str, venv_py: str) -> None:
    """Remove an incompatible .venv and create a fresh one."""
    import shutil
    import subprocess

    print("[...] Existing venv is incompatible with this system — rebuilding...", file=sys.stderr)

    if os.path.exists(venv_dir):
        shutil.rmtree(venv_dir, ignore_errors=True)

    subprocess.check_call([sys.executable, "-m", "venv", venv_dir])
    _setup_venv_pip(venv_py)

    print("[OK] Virtual environment ready!", file=sys.stderr)


def _download_prebuilt_venv(venv_dir: str, venv_py: str) -> bool:
    """
    Try to download a pre-built .venv from the latest GitHub Release
    for the current platform. Returns True on success.
    """
    import subprocess
    import urllib.request
    import json
    import shutil

    # Map platform to release asset name
    system = platform.system().lower()
    if system == "windows":
        asset_glob = "venv-windows.zip"
        extract_cmd = ["7z", "x", "-aoa", "-o.venv"]
    elif system == "darwin":
        asset_glob = "venv-macos.tar.gz"
        extract_cmd = ["tar", "xzf"]
    else:
        asset_glob = "venv-linux.tar.gz"
        extract_cmd = ["tar", "xzf"]

    repo = "cubiced0/owo-discord-bot"
    api_url = f"https://api.github.com/repos/{repo}/releases?per_page=5"

    try:
        # Fetch recent releases
        req = urllib.request.Request(api_url, headers={"User-Agent": "Limey/1.0", "Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            releases = json.loads(resp.read().decode())

        if not releases:
            return False

        # Find a release with our asset
        for release in releases:
            for asset in release.get("assets", []):
                name = asset["name"]
                if name.startswith("venv-") and any(p in name for p in ("linux", "macos", "windows")):
                    if name.endswith(".tar.gz") or name.endswith(".zip"):
                        download_url = asset["browser_download_url"]
                        print(f"[...] Downloading pre-built venv ({name})...", file=sys.stderr)

                        # Download
                        archive_path = os.path.join(_PROJECT_DIR, f"_{name}")
                        urllib.request.urlretrieve(download_url, archive_path)

                        # Remove broken venv dir if exists
                        if os.path.exists(venv_dir):
                            shutil.rmtree(venv_dir, ignore_errors=True)

                        # Extract
                        if name.endswith(".zip"):
                            subprocess.check_call(extract_cmd + [archive_path], cwd=_PROJECT_DIR)
                        else:
                            subprocess.check_call(extract_cmd + [archive_path], cwd=_PROJECT_DIR)

                        # Clean up archive
                        os.remove(archive_path)

                        # Verify the downloaded venv works
                        if os.path.exists(venv_py) and _venv_is_valid(venv_py):
                            print("[OK] Pre-built venv downloaded and ready!", file=sys.stderr)
                            return True

                        # Downloaded venv didn't work — will fall through to local build
                        print("[!] Downloaded venv incompatible — building locally", file=sys.stderr)
                        if os.path.exists(venv_dir):
                            shutil.rmtree(venv_dir, ignore_errors=True)
                        return False
    except Exception as e:
        print(f"[!] Could not download pre-built venv: {e}", file=sys.stderr)
        # Clean up partial downloads
        if os.path.exists(venv_dir) and not _venv_is_valid(venv_py):
            shutil.rmtree(venv_dir, ignore_errors=True)
        return False

    return False


def _ensure_venv() -> str:
    """
    Ensure the project's .venv exists and is compatible with the current system.
    If missing or incompatible, tries the following in order:
      1. Use existing venv if valid
      2. Download pre-built venv from GitHub Releases
      3. Create venv from scratch (pip install)
    Returns the venv Python path.
    """
    import subprocess

    venv_dir = os.path.join(_PROJECT_DIR, ".venv")
    venv_py = os.path.join(venv_dir, "bin", "python")
    if os.name == "nt":
        venv_py = os.path.join(venv_dir, "Scripts", "python.exe")

    # 1. Existing venv is valid — use it
    if os.path.exists(venv_dir) and _venv_is_valid(venv_py):
        return venv_py

    # 2. Directory exists but broken — try downloading a pre-built one first
    if os.path.exists(venv_dir):
        if _download_prebuilt_venv(venv_dir, venv_py):
            return venv_py
        # Download failed — rebuild from scratch
        print("[...] Building venv locally...", file=sys.stderr)
        shutil.rmtree(venv_dir, ignore_errors=True)

    # 3. Try downloading a pre-built venv before building from scratch
    if _download_prebuilt_venv(venv_dir, venv_py):
        return venv_py

    # 4. Create from scratch
    print("[...] Setting up virtual environment from scratch...", file=sys.stderr)
    subprocess.check_call([sys.executable, "-m", "venv", venv_dir])
    _setup_venv_pip(venv_py)

    print("[OK] Virtual environment ready!", file=sys.stderr)
    return venv_py


def exec_venv_or_continue():
    """
    Ensure the project's .venv exists (auto-create if missing), then
    re-execute this script using the venv Python if not already in it.
    """
    if is_venv_python():
        return  # Already in the venv

    # Auto-create the venv if missing, then get its Python path
    venv_py = _ensure_venv()

    # Re-exec using the venv Python
    try:
        os.execv(venv_py, [venv_py] + sys.argv)
    except OSError:
        # os.execv may not fully work on Windows; fall back to subprocess
        import subprocess
        sys.exit(subprocess.call([venv_py] + sys.argv))
