#!/usr/bin/env python3
"""
Regenerate the GitHub data-store token + Fernet key.

Use this when the original LIMEY_GITHUB_ENCRYPTION_KEY was lost and you
need to re-encrypt a brand-new GitHub token.

What it does:
  1. Generates a fresh Fernet key.
  2. Encrypts your new GitHub token with it.
  3. Replaces _ENCRYPTED_TOKEN in utils/github_data_store.py (with a timestamped .bak backup).
  4. Writes/updates .env with LIMEY_GITHUB_ENCRYPTION_KEY=<new key>.
  5. Verifies the round-trip decrypt matches your token.
  6. (Optional) Verifies the runtime path by importing the updated module.

Usage:
  python regen_github_token.py                      # prompts for token
  python regen_github_token.py --token ghp_XXXX     # pass token on CLI
  LIMEY_GITHUB_TOKEN=ghp_XXXX python regen_github_token.py

Note: passing the token via --token leaves it in your shell history.
Prefer the LIMEY_GITHUB_TOKEN env var or the interactive prompt.

The new Fernet key is printed at the end — save it somewhere safe
(password manager), since the token can only be decrypted with it.
"""

import argparse
import datetime
import getpass
import os
import re
import subprocess
import sys

from cryptography.fernet import Fernet

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_STORE_PATH = os.path.join(PROJECT_DIR, "utils", "github_data_store.py")
ENV_PATH = os.path.join(PROJECT_DIR, ".env")

_TOKEN_LINE_RE = re.compile(r'^_ENCRYPTED_TOKEN\s*=\s*"[^"]*"', re.MULTILINE)


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-encrypt the GitHub data-store token with a new Fernet key.")
    parser.add_argument("--token", help="The new GitHub token (otherwise prompted / LIMEY_GITHUB_TOKEN env).")
    parser.add_argument("--file", default=DATA_STORE_PATH, help="Path to github_data_store.py (default: repo copy).")
    parser.add_argument("--env", default=ENV_PATH, help="Path to .env file (default: repo copy).")
    args = parser.parse_args()

    token = args.token or os.environ.get("LIMEY_GITHUB_TOKEN", "").strip()
    if not token:
        try:
            token = getpass.getpass("Paste your new GitHub token: ").strip()
        except (EOFError, KeyboardInterrupt):
            token = ""
    if not token:
        print("[X] No token provided.", file=sys.stderr)
        return 1

    # 1) Fresh Fernet key
    key = Fernet.generate_key().decode()
    cipher = Fernet(key.encode())
    new_encrypted = cipher.encrypt(token.encode()).decode()

    # 2) Verify the round-trip works before touching any file
    decrypted = cipher.decrypt(new_encrypted.encode()).decode()
    if decrypted != token:
        print("[X] Encrypt/decrypt round-trip failed — aborting.", file=sys.stderr)
        return 1

    # 3) Update utils/github_data_store.py (timestamped backup first)
    if not os.path.exists(args.file):
        print(f"[X] Data store file not found: {args.file}", file=sys.stderr)
        return 1
    with open(args.file, "r", encoding="utf-8") as f:
        src = f.read()
    new_src, n = _TOKEN_LINE_RE.subn(f'_ENCRYPTED_TOKEN = "{new_encrypted}"', src, count=1)
    if n != 1:
        print("[X] Could not find _ENCRYPTED_TOKEN in the data store file — aborting.", file=sys.stderr)
        return 1
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{args.file}.{stamp}.bak"
    with open(backup, "w", encoding="utf-8") as f:
        f.write(src)
    with open(args.file, "w", encoding="utf-8") as f:
        f.write(new_src)
    print(f"[OK] Updated {args.file} (backup saved to {backup})")

    # 4) Write/update .env
    env_lines = []
    if os.path.exists(args.env):
        with open(args.env, "r", encoding="utf-8") as f:
            env_lines = f.read().splitlines()
    key_line = f"LIMEY_GITHUB_ENCRYPTION_KEY={key}"
    found = False
    for i, line in enumerate(env_lines):
        stripped = line.strip()
        if stripped.startswith("LIMEY_GITHUB_ENCRYPTION_KEY=") or stripped == "LIMEY_GITHUB_ENCRYPTION_KEY":
            env_lines[i] = key_line
            found = True
            break
    if not found:
        env_lines.append(key_line)
    with open(args.env, "w", encoding="utf-8") as f:
        f.write("\n".join(env_lines) + "\n")
    print(f"[OK] Updated {args.env}")

    # 5) End-to-end verification: import the module and confirm it decrypts to the exact
    #    new token. Only safe when updating the real repo files (default paths) since the
    #    imported module must be the one we just edited. LIMEY_GITHUB_TOKEN is stripped so
    #    the plaintext fallback can't mask a broken encryption.
    if args.file == DATA_STORE_PATH and args.env == ENV_PATH:
        try:
            env = dict(os.environ)
            env.pop("LIMEY_GITHUB_TOKEN", None)
            env["LIMEY_GITHUB_ENCRYPTION_KEY"] = key
            code = (
                "from utils.github_data_store import GITHUB_TOKEN; "
                "import sys; "
                "sys.stdout.write(GITHUB_TOKEN)"
            )
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, timeout=30,
                env=env,
                input="",
                cwd=PROJECT_DIR,
            )
            out = proc.stdout.strip()
            if out == token:
                print("[OK] Runtime verification passed — module decrypts the new token.")
            else:
                print(f"[!] Runtime verification failed — module resolved to: {out!r}", file=sys.stderr)
                print(f"    stderr: {proc.stderr.strip()}", file=sys.stderr)
        except Exception as e:
            print(f"[!] Runtime verification skipped/failed: {e}", file=sys.stderr)

    # 6) Report the key so it can be saved externally
    print("\n[OK] Done. Save this key somewhere safe (password manager):")
    print(f"    LIMEY_GITHUB_ENCRYPTION_KEY={key}")
    print("\nYour new token is now encrypted in the code and will decrypt automatically at runtime.")
    print("Note: a timestamped .bak of the old data store file was left next to it —")
    print("you can delete it once you've confirmed everything works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
