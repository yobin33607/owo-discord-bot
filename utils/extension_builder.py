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
Builds the Limey browser extension zip.

The zip is (re)built every time Limey boots (see limey.py) and is served by
the dashboard so it can be downloaded straight from Dashboard → Extension
(no need to clone the repo to install it).

The actual packaging logic lives in `limey_discord_theme/pack.py`; this
module just locates it, runs it, and exposes helpers for the dashboard.
"""

import json
import os
import subprocess
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTENSION_DIR = os.path.join(_PROJECT_DIR, "limey_discord_theme")
PACK_SCRIPT = os.path.join(EXTENSION_DIR, "pack.py")


def _load_manifest():
    """Load the extension manifest.json (best effort)."""
    try:
        with open(os.path.join(EXTENSION_DIR, "manifest.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def extension_zip_name():
    """The zip filename pack.py produces, e.g. limey-captcha-alert-theme-1.3.0.zip."""
    manifest = _load_manifest()
    name = manifest.get("name", "Limey Captcha Alert Theme")
    version = manifest.get("version", "0.0.0")
    slug = name.lower().replace(" ", "-")
    return f"{slug}-{version}.zip"


def extension_zip_path():
    """Absolute path of the built zip (may not exist yet)."""
    return os.path.join(EXTENSION_DIR, extension_zip_name())


def extension_info():
    """Non-building status info for the dashboard UI."""
    manifest = _load_manifest()
    zip_path = extension_zip_path()
    return {
        "name": manifest.get("name", "Limey Captcha Alert Theme"),
        "version": manifest.get("version", "0.0.0"),
        "built": os.path.exists(zip_path),
        "zip_name": extension_zip_name(),
        "size_bytes": os.path.getsize(zip_path) if os.path.exists(zip_path) else 0,
    }


def build_extension_zip():
    """Run pack.py to (re)build the extension zip.

    Returns (zip_path, error_message); error_message is None on success.
    """
    if not os.path.exists(PACK_SCRIPT):
        return None, "extension pack script not found"
    try:
        proc = subprocess.run(
            [sys.executable, PACK_SCRIPT],
            cwd=EXTENSION_DIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "pack failed").strip()
            return None, detail[-500:]
        zip_path = extension_zip_path()
        if os.path.exists(zip_path):
            return zip_path, None
        return None, "zip file was not produced"
    except Exception as e:  # noqa: BLE001 - surface any error to the dashboard
        return None, str(e)


def ensure_extension_zip():
    """Return the built zip, building it on demand if missing.

    Returns (zip_path, error_message); error_message is None on success.
    """
    zip_path = extension_zip_path()
    if os.path.exists(zip_path):
        return zip_path, None
    return build_extension_zip()
