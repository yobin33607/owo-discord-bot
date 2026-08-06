#!/usr/bin/env python3
"""Package the Limey Discord Theme for store upload.

Creates `limey-discord-theme-<version>.zip` with `manifest.json` at the root
of the archive, containing only the files the Chrome Web Store / Edge Add-ons
need (dev files like README and the icon generator are left out).

Usage:  python3 pack.py
"""
import json
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
EXCLUDE_NAMES = {"README.md", "pack.py", "generate_icons.py", "__pycache__"}


def main():
    manifest_path = os.path.join(HERE, "manifest.json")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    # Basic manifest sanity checks before packaging
    if manifest.get("manifest_version") != 3:
        raise SystemExit("manifest_version must be 3")
    name = manifest.get("name")
    version = manifest.get("version")
    if not name or not version:
        raise SystemExit("manifest.json must define name and version")

    slug = name.lower().replace(" ", "-")
    out = os.path.join(HERE, f"{slug}-{version}.zip")

    files = []
    for root, dirs, names in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_NAMES and not d.startswith(".")]
        for n in names:
            if n in EXCLUDE_NAMES or n.endswith(".zip"):
                continue
            full = os.path.join(root, n)
            rel = os.path.relpath(full, HERE).replace(os.sep, "/")
            files.append((full, rel))

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in sorted(files, key=lambda t: t[1]):
            z.write(full, rel)

    print(f"wrote {out}")
    for _, rel in sorted(files, key=lambda t: t[1]):
        print(f"  {rel}")
    print(f"total: {len(files)} files, {os.path.getsize(out):,} bytes")
    print("manifest.json is at the archive root — ready to upload.")


if __name__ == "__main__":
    main()
