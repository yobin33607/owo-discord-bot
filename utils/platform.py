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


def _ensure_venv() -> str:
    """
    Ensure the project's .venv exists and is compatible with the current system.
    If missing or incompatible, create/recreate it, install requirements,
    and return the venv Python path.
    """
    import subprocess

    venv_dir = os.path.join(_PROJECT_DIR, ".venv")
    venv_py = os.path.join(venv_dir, "bin", "python")
    if os.name == "nt":
        venv_py = os.path.join(venv_dir, "Scripts", "python.exe")

    # Check if existing venv is valid for this platform
    if os.path.exists(venv_dir) and _venv_is_valid(venv_py):
        return venv_py

    # Rebuild if the directory exists but is broken/incompatible
    if os.path.exists(venv_dir):
        _rebuild_venv(venv_dir, venv_py)
        return venv_py

    # Create from scratch
    print("[...] Setting up virtual environment...", file=sys.stderr)
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
