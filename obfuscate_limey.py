#!/usr/bin/env python3
"""
obfuscate_limey.py — Build a zipped, obfuscated copy of the Limey project.

This is the CROSS-PLATFORM engine used on Windows (via obfuscate_limey.bat).
On Linux/macOS the equivalent native tool is obfuscate_limey.sh, which
implements exactly the same steps. Keep both in sync when changing behavior.

WHAT IT DOES
  * Works entirely on a COPY of the source — the original code is never
    modified (only the final .zip is written into this directory).
  * Uses PyArmor (https://pyarmor.readthedocs.io/) to obfuscate every Limey
    Python script with the highest protection your license allows.
  * Packages everything (obfuscated scripts + PyArmor runtime + vendored
    discord library + dashboard assets + models) into a single .zip.

OBFUSCATION LEVELS (auto-detected from your PyArmor license)
  trial   --enable jit                              (restricted: files over the
                                                    size limit are SKIPPED and
                                                    shipped un-obfuscated; no
                                                    mix-str / obf-code 2)
  basic   --obf-code 2 --mix-str --enable jit       (free non-commercial
                                                    license — recommended)
  pro     --obf-code 2 --mix-str --enable jit,rft,bcc   (strongest possible;
                                                    opt-in via --pro)

TRIAL LICENSE
  With the default trial license the build still completes: files larger than
  the trial limit (default 32KB, see --max-file-size) are skipped and shipped
  as plain Python instead of failing the whole build.

GETTING THE FREE BASIC LICENSE (for FULL obfuscation)
  Register the free non-commercial (non-profits) license:
    1. Get an activation code  ->  https://pyarmor.dashingsoft.com/register/
       (send it to yourself; it arrives as pyarmor-regcode-xxxx.txt)
    2. python obfuscate_limey.py --regcode pyarmor-regcode-xxxx.txt
  After that, just run the script normally on this machine.

PLATFORM SUPPORT
  Linux  : obfuscate_limey.sh    (native bash)
  macOS  : obfuscate_limey.sh    (native bash)
  Windows: obfuscate_limey.bat   (this engine)

USAGE
  python obfuscate_limey.py [options]

OPTIONS (identical to obfuscate_limey.sh)
  --regcode <file>      Register a PyArmor license first (activation .txt or
                        registration .zip) then build.
  --product <name>      Product name for registration (default: non-profits)
  --pro                 Enable RFT+BCC modes (requires a Pro license).
  --max-file-size <b>   Skip files larger than this many bytes instead of
                        failing. Default 32768 (32KB).
  --force-trial         With the trial license, obfuscate EVERYTHING and let
                        oversized files fail (no skipping).
  --project <dir>       Build from this project copy instead of the script dir.
  --python <path>       Python interpreter to use (default: first 3.10+ found).
  --out <file>          Output .zip path (default: limey-obfuscated-<ts>.zip).
  --verify              Run a smoke test on the obfuscated output before zipping.
  --keep-temp           Keep the temporary build/staging directories.
  -h, --help            Show this help.

NOTE ON COMPATIBILITY
  Obfuscated code is tied to the Python version + platform used to build it.
  Build the .zip ON the OS you intend to distribute to.
"""

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

APP_NAME = "Limey"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IS_WINDOWS = os.name == "nt"

# ANSI colors (disabled on Windows — plain cmd.exe can't render them reliably)
RED = "\033[1;31m"; GREEN = "\033[1;32m"; CYAN = "\033[1;36m"; YELLOW = "\033[1;33m"; RESET = "\033[0m"
if IS_WINDOWS:
    RED = GREEN = CYAN = YELLOW = RESET = ""

# Every top-level script/package that belongs to Limey and gets obfuscated.
OBFUSCATE_TARGETS = [
    "limey.py",
    "limey_setup.py",
    "run_manager_bot.py",
    "proxy_server.py",
    "regen_github_token.py",
    "core",
    "modules",
    "cogs",
    "utils",
    "dashboard",
    "limey_engines",
    "component_v2_limey",
    "limey_ascii",
]

# Mirrors the ANCHORED rsync/tar exclude list in obfuscate_limey.sh: these
# patterns only apply to entries directly under the project root (see
# _ignore_stage), keeping runtime state / secrets out of the build.
STAGE_EXCLUDES = {
    ".venv", ".git", "__pycache__", "config", "prebuilt", "dist", "build",
    ".github", "data",
}


def fail(msg):
    print(f"{RED}[X] {msg}{RESET}", file=sys.stderr)
    sys.exit(1)


def ok(msg):
    print(f"{GREEN}[OK] {msg}{RESET}")


def info(msg):
    print(f"{CYAN}[*] {msg}{RESET}")


def warn(msg):
    print(f"{YELLOW}[!] {msg}{RESET}")


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024.0
    return f"{n:.1f}GB"


def run_cmd(cmd, cwd=None, timeout=None):
    """Run a command, capture combined output (never hangs on prompts)."""
    try:
        p = subprocess.run(
            cmd, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout,
        )
    except OSError as e:
        fail(f"Failed to run {' '.join(cmd)}: {e}")
    except subprocess.TimeoutExpired:
        fail(f"Command timed out: {' '.join(cmd)}")
    return p.returncode, p.stdout or ""


# ---------------------------------------------------------------------------
# Python detection
# ---------------------------------------------------------------------------
def python_version_ok(argv):
    try:
        p = subprocess.run(argv + ["--version"], capture_output=True, text=True, timeout=30)
        text = p.stdout + p.stderr
        m = re.search(r"Python (\d+)\.(\d+)", text)
        return bool(m) and (int(m.group(1)), int(m.group(2))) >= (3, 10)
    except Exception:
        return False


def find_python(explicit):
    if explicit:
        if not os.path.isfile(explicit):
            fail(f"--python path not found: {explicit}")
        if not python_version_ok([explicit]):
            fail(f"--python is not a Python 3.10+ interpreter: {explicit}")
        return [explicit]

    # Prefer the project's own venv so the obfuscated runtime matches the
    # exact Python version Limey actually runs under.
    for py in ("bin/python", "Scripts/python.exe"):
        cand = os.path.join(PROJECT_DIR, ".venv", py)
        if os.path.isfile(cand) and python_version_ok([cand]):
            return [cand]

    for cmd in ("python3.12", "python3.11", "python3.10", "python3", "python"):
        if shutil.which(cmd) and python_version_ok([cmd]):
            return [cmd]

    if IS_WINDOWS and shutil.which("py"):
        # The Windows py launcher: `py -3` always selects a Python 3.x.
        if python_version_ok(["py", "-3"]):
            return ["py", "-3"]

    fail("Python 3.10+ not found. Install it or pass --python /path/to/python.")


# ---------------------------------------------------------------------------
# Path helpers (Unix venvs use bin/, Windows venvs use Scripts/)
# ---------------------------------------------------------------------------
def venv_python(venv_dir):
    return os.path.join(venv_dir, "Scripts", "python.exe") if IS_WINDOWS \
        else os.path.join(venv_dir, "bin", "python")


def venv_pyarmor(venv_dir):
    return os.path.join(venv_dir, "Scripts", "pyarmor.exe") if IS_WINDOWS \
        else os.path.join(venv_dir, "bin", "pyarmor")


def cache_dir():
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "limey-obfuscate")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "limey-obfuscate")


# ---------------------------------------------------------------------------
# Obfuscation venv (cached outside the project — never touches the source)
# ---------------------------------------------------------------------------
def setup_venv(py_argv):
    global PYARMOR_CMD
    os.makedirs(WORK_DIR, exist_ok=True)
    vpy = venv_python(VENV_DIR)
    if not os.path.isfile(vpy):
        info("Creating obfuscation virtual environment (first run)...")
        rc, out = run_cmd(py_argv + ["-m", "venv", VENV_DIR])
        if rc != 0:
            print(out, file=sys.stderr)
            fail("Failed to create the obfuscation virtual environment.")
    info("Ensuring PyArmor is installed...")
    rc, out = run_cmd([vpy, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    if rc != 0:
        print(out, file=sys.stderr)
        fail("Failed to upgrade pip inside the obfuscation virtual environment.")
    rc, out = run_cmd([vpy, "-m", "pip", "install", "-q", "--upgrade", "pyarmor"])
    if rc != 0:
        print(out, file=sys.stderr)
        fail("PyArmor install failed. Check your internet connection and try again.")
    exe = venv_pyarmor(VENV_DIR)
    if os.path.isfile(exe):
        PYARMOR_CMD = [exe]
    else:
        # Fallback: invoke through the module when no console script was created.
        PYARMOR_CMD = [vpy, "-m", "pyarmor.cli"]
    rc, out = run_cmd(PYARMOR_CMD + ["--version"])
    first = (out.strip().splitlines() or ["version unknown"])[0]
    ok(f"PyArmor ready: {first}")


# ---------------------------------------------------------------------------
# License handling
# ---------------------------------------------------------------------------
def license_info():
    """Returns (license_type, product) e.g. ('pyarmor-trial', 'non-profits')."""
    _, out = run_cmd(PYARMOR_CMD + ["-v"], cwd=WORK_DIR)
    ltype = product = to = ""
    for line in out.splitlines():
        if "License Type" in line:
            ltype = line.split()[-1]
        elif "License Product" in line:
            product = line.split()[-1]
        elif "License To" in line:
            to = line.split()[-1]
    return (ltype or "unknown"), (product or to)


def register_license(regcode, product):
    regcode = os.path.realpath(regcode)
    if not os.path.isfile(regcode):
        fail(f"License file not found: {regcode}")
    info(f"Registering PyArmor license from {regcode} ...")
    if regcode.lower().endswith(".zip"):
        # Registration file (already activated on another machine)
        cmd = PYARMOR_CMD + ["reg", regcode]
    else:
        # Activation code file (first-time registration; needs internet)
        cmd = PYARMOR_CMD + ["reg", "-p", product, regcode]
    rc, out = run_cmd(cmd, cwd=WORK_DIR)
    if rc != 0:
        print(out, file=sys.stderr)
        fail("License registration failed. Is the code valid and is there internet access?")
    ok("License registered.")


def choose_obfuscation_options(license_type):
    opts = []
    if "pro" in license_type or "group" in license_type:
        opts += ["--obf-code", "2", "--mix-str"]
        if ARGS.pro:
            _, vout = run_cmd(PYARMOR_CMD + ["-v"], cwd=WORK_DIR)
            rft = bool(re.search(r"RFT Mode.*Yes", vout, re.I))
            bcc = bool(re.search(r"BCC Mode.*Yes", vout, re.I))
            enable = "jit"
            if rft:
                enable += ",rft"
            if bcc:
                enable += ",bcc"
            if rft or bcc:
                warn("Enabling RFT/BCC modes (max security). String-based cog dispatch may break — test the build!")
                opts += ["--enable", enable]
            else:
                warn("--pro requested but this license does not enable RFT/BCC. Using basic+JIT options.")
                opts += ["--enable", "jit"]
        else:
            info("Pro license detected — pass --pro to also enable RFT/BCC (highest security; may affect dynamic cog dispatch).")
            opts += ["--enable", "jit"]
    elif "basic" in license_type:
        if ARGS.pro:
            warn("--pro requires a Pro license — using the best options this license allows.")
        opts += ["--obf-code", "2", "--mix-str", "--enable", "jit"]
    elif "ci" in license_type:
        fail("PyArmor CI licenses only work in CI pipelines — register a Basic/Pro license on this machine.")
    elif "trial" in license_type:
        if ARGS.force_trial:
            warn("Attempting FULL obfuscation with the trial license — oversized files will fail.")
        else:
            warn(f"Trial license: files over {ARGS.max_file_size or 32768} bytes will be SKIPPED and shipped un-obfuscated.")
            warn("Register the free non-commercial license for full obfuscation (see --help).")
        opts += ["--enable", "jit"]
    else:
        fail(f"Could not determine PyArmor license type ('{license_type}'). Run '{' '.join(PYARMOR_CMD)} -v' to inspect.")
    return opts


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------
def _ignore_stage(src, names):
    ignored = set()
    top_level = os.path.abspath(src) == os.path.abspath(PROJECT_DIR)
    for n in names:
        full = os.path.join(src, n)
        is_file = os.path.isfile(full)
        if top_level:
            # Anchored rsync patterns ('--exclude /<name>') apply only to
            # entries directly under the project root — mirror that exactly.
            if n in STAGE_EXCLUDES or n in (".env", "tokens.txt") or n.startswith(".env."):
                ignored.add(n)
            elif n.startswith(".obfuscate") and not is_file:
                ignored.add(n)
            elif is_file and (n.endswith(".pyc") or n.endswith(".zip")):
                ignored.add(n)
        elif is_file and n.endswith(".pyc"):
            # Unanchored pattern: '*.pyc' is excluded at any depth in bash.
            ignored.add(n)
    return ignored


def stage_copy():
    global STAGE
    STAGE = tempfile.mkdtemp(prefix="limey-stage-", dir=TMPDIR)
    info(f"Copying project to staging area (originals untouched): {STAGE}")
    # mkdtemp already created STAGE — copytree must merge into it.
    shutil.copytree(PROJECT_DIR, STAGE, ignore=_ignore_stage, symlinks=True, dirs_exist_ok=True)
    n = sum(1 for _ in Path(STAGE).rglob("*.py"))
    info(f"Staged {n} Python files.")


def run_pyarmor(obfuscation_opts):
    global OUT, SKIPPED_FILES
    OUT = tempfile.mkdtemp(prefix="limey-out-", dir=TMPDIR)

    max_size = ARGS.max_file_size or 32768
    skip_large = (LICENSE_TYPE == "pyarmor-trial") and not ARGS.force_trial

    info(f"Obfuscating with options: {' '.join(obfuscation_opts) or 'defaults'}")
    inputs, excludes = [], []
    SKIPPED_FILES = []
    for t in OBFUSCATE_TARGETS:
        p = os.path.join(STAGE, t)
        if not os.path.exists(p):
            continue
        if os.path.isdir(p):
            # Package: exclude oversized modules so the rest of the package
            # still gets obfuscated (they are copied in as plain files later).
            if skip_large:
                for f in Path(p).rglob("*.py"):
                    if f.stat().st_size > max_size:
                        # PyArmor matches forward-slash paths — on Windows
                        # os.path.relpath returns backslashes that would break
                        # the glob, so normalize separators here.
                        rel = os.path.relpath(f, STAGE).replace(os.sep, "/")
                        SKIPPED_FILES.append(str(f))
                        excludes += ["--exclude", f"*/{rel}"]
            inputs.append(p)
        elif os.path.isfile(p):
            if skip_large and os.path.getsize(p) > max_size:
                SKIPPED_FILES.append(p)
            else:
                inputs.append(p)
    if not inputs:
        fail("No obfuscation targets found in staging dir.")

    if SKIPPED_FILES:
        warn(f"Skipping {len(SKIPPED_FILES)} file(s) over the {max_size}-byte limit (will be shipped un-obfuscated):")
        for f in SKIPPED_FILES:
            print(f"    {os.path.relpath(f, STAGE)}")

    cmd = PYARMOR_CMD + ["gen", "-r", "-O", OUT] + obfuscation_opts + excludes + inputs
    rc, log = run_cmd(cmd, cwd=WORK_DIR)
    if rc != 0:
        if re.search(r"out of license", log, re.I):
            print()
            print(f"{RED}[X] PyArmor refused to obfuscate part of the project (out of license).{RESET}")
            print("    The current license cannot handle the full Limey codebase.")
            print()
            print("    Register the FREE non-commercial license:")
            print("      1. Get an activation code at https://pyarmor.dashingsoft.com/register/")
            print("      2. Run:  obfuscate_limey.sh / obfuscate_limey.bat --regcode /path/to/pyarmor-regcode-xxxx.txt")
            print()
        else:
            print(log, file=sys.stderr)
            fail("PyArmor obfuscation failed. See output above.")
        sys.exit(1)
    print("\n".join(log.splitlines()[-8:]))
    ok("Obfuscation complete.")

    # Ship oversized files as plain copies so the build stays complete/runnable
    for f in SKIPPED_FILES:
        rel = os.path.relpath(f, STAGE).replace(os.sep, "/")
        dest = os.path.join(OUT, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(f, dest)
    if SKIPPED_FILES:
        ok(f"Copied {len(SKIPPED_FILES)} un-obfuscated file(s) into the build.")


def overlay_assets():
    info("Adding runtime assets (not obfuscated)...")
    # Vendored discord.py used by the manager bot (kept plain on purpose)
    for name in ("manager_bot_discord", "beeps", "models"):
        s = os.path.join(STAGE, name)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(OUT, name), dirs_exist_ok=True)
    # Dashboard web assets (skip if pyarmor already carried them over)
    for sub in ("templates", "static"):
        s = os.path.join(STAGE, "dashboard", sub)
        if os.path.isdir(s) and not os.path.exists(os.path.join(OUT, "dashboard", sub)):
            os.makedirs(os.path.join(OUT, "dashboard"), exist_ok=True)
            shutil.copytree(s, os.path.join(OUT, "dashboard", sub))
    # Data / resources / docs / dependency manifest
    for rel in ("utils/emojis.json", "requirements.txt", "README.md", "LICENSE"):
        s = os.path.join(STAGE, rel)
        if os.path.isfile(s) and not os.path.exists(os.path.join(OUT, rel)):
            dest = os.path.join(OUT, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(s, dest)
    ok("Assets added.")


def find_runtime_dir():
    for name in os.listdir(OUT):
        if name.startswith("pyarmor_runtime_") and os.path.isdir(os.path.join(OUT, name)):
            return os.path.join(OUT, name)
    return None


def write_readme(py_argv):
    runtime_dir = find_runtime_dir()
    runtime_name = os.path.basename(runtime_dir) if runtime_dir else "pyarmor_runtime_*"
    _, pyver = run_cmd(py_argv + ["-c", "import platform; print(platform.python_version())"], cwd=WORK_DIR)
    venv_hint = "<venv>\\Scripts\\python.exe" if IS_WINDOWS else "<venv>/bin/python"
    lines = [
        "Limey — obfuscated build",
        "========================",
        "This is an obfuscated copy of the Limey source produced by",
        "obfuscate_limey.sh (Linux/macOS) or obfuscate_limey.bat (Windows).",
        "",
        f"  Built on : {platform.system()} {platform.release()} {platform.machine()}",
        f"  Built for: Python {pyver.strip()}",
        f"  Runtime  : {runtime_name}  (must be distributed together with the scripts)",
        "",
        "How to run",
        "  1. Extract this archive.",
        "  2. Create a virtual environment with the SAME Python version listed above",
        "     and install requirements.txt into it.",
        f"  3. First run:  {venv_hint} limey_setup.py",
        f"  4. Start:      {venv_hint} limey.py",
        "",
        "Notes",
        "  * The obfuscated code only runs on the Python version / OS it was built for.",
        "  * config/, data/, .env and other runtime state are NOT included — provide",
        "    your own (keys, accounts, settings) as usual.",
        "  * Do NOT run obfuscation on this extracted copy. Keep the original repo as",
        "    the source of truth.",
    ]
    if SKIPPED_FILES:
        lines += ["", "Un-obfuscated files (exceeded the build's size limit):"]
        lines += [f"  - {os.path.relpath(f, STAGE)}" for f in SKIPPED_FILES]
    with open(os.path.join(OUT, "README-OBFUSCATED.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    ok("Wrote README-OBFUSCATED.txt")


def _py_count(p):
    """Count .py files under a path — works for both files and directories.
    (Path.rglob on a plain file yields nothing, unlike `find <file> -name '*.py'`.)"""
    if os.path.isdir(p):
        return sum(1 for _ in Path(p).rglob("*.py"))
    return 1 if os.path.isfile(p) and p.endswith(".py") else 0


def verify_build(py_argv):
    info("Verifying obfuscated output...")
    src_count = 0
    for t in OBFUSCATE_TARGETS:
        p = os.path.join(STAGE, t)
        if os.path.exists(p):
            src_count += _py_count(p)
    out_count = 0
    for root, dirs, files in os.walk(OUT):
        base = os.path.basename(root)
        if base == "manager_bot_discord" or base.startswith("pyarmor_runtime_"):
            dirs[:] = []
            continue
        out_count += sum(1 for f in files if f.endswith(".py"))
    if src_count == out_count:
        ok(f"All {src_count} scripts have an obfuscated counterpart.")
    else:
        warn(f"Script count mismatch: {src_count} source vs {out_count} obfuscated.")

    runtime_dir = find_runtime_dir()
    if not runtime_dir:
        fail("PyArmor runtime package missing from the output!")
    ok(f"PyArmor runtime present: {os.path.basename(runtime_dir)}")

    if ARGS.verify:
        info("Smoke test: importing obfuscated limey_ascii through the runtime...")
        rc, out = run_cmd([venv_python(VENV_DIR), "-m", "pip", "install", "-q", "rich"])
        if rc != 0:
            print(out, file=sys.stderr)
            fail("Failed to install rich for the smoke test.")
        code = (
            "import limey_ascii.limey_ascii as m\n"
            "assert m.AUTHOR\n"
            "print('  smoke test OK — obfuscated module imports and runs')"
        )
        rc, out = run_cmd([venv_python(VENV_DIR), "-c", code], cwd=OUT)
        if rc != 0:
            print(out, file=sys.stderr)
            fail("Smoke test failed — the obfuscated output is not importable. Inspect with --keep-temp.")
        print(out, end="")


def make_zip():
    info(f"Creating archive: {ZIP_OUT}")
    os.makedirs(os.path.dirname(os.path.abspath(ZIP_OUT)), exist_ok=True)
    # Keep bytecode caches out of the archive
    for root, dirs, files in os.walk(OUT, topdown=False):
        if os.path.basename(root) == "__pycache__":
            shutil.rmtree(root, ignore_errors=True)
    with zipfile.ZipFile(ZIP_OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(OUT):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f.endswith(".pyc"):
                    continue
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, OUT))
    ok(f"Archive created: {ZIP_OUT} ({human_size(os.path.getsize(ZIP_OUT))})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    global ARGS, PROJECT_DIR, ZIP_OUT, TMPDIR, VENV_DIR, WORK_DIR, PY_CMD, \
        STAGE, OUT, SKIPPED_FILES, LICENSE_TYPE

    parser = argparse.ArgumentParser(description="Build a zipped, obfuscated copy of the Limey project (cross-platform).", add_help=False)
    parser.add_argument("--regcode")
    parser.add_argument("--product", default="non-profits")
    parser.add_argument("--pro", action="store_true")
    parser.add_argument("--max-file-size", type=int)
    parser.add_argument("--force-trial", action="store_true")
    parser.add_argument("--project")
    parser.add_argument("--python")
    parser.add_argument("--out")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    ARGS = parser.parse_args()

    if ARGS.help:
        print(__doc__)
        sys.exit(0)

    if ARGS.max_file_size is not None and ARGS.max_file_size < 0:
        fail("--max-file-size must be a positive integer (bytes).")

    PROJECT_DIR = os.path.abspath(ARGS.project or SCRIPT_DIR)
    if not os.path.isdir(PROJECT_DIR):
        fail(f"Project directory not found: {PROJECT_DIR}")
    if not os.path.isfile(os.path.join(PROJECT_DIR, "limey.py")):
        fail(f"limey.py not found in {PROJECT_DIR} — is this the Limey project?")

    ZIP_OUT = ARGS.out or os.path.join(
        SCRIPT_DIR, f"limey-obfuscated-{time.strftime('%Y%m%d-%H%M%S')}.zip")

    print(f"{CYAN}=== {APP_NAME} Obfuscation Builder ==={RESET}")

    TMPDIR = os.environ.get("TMPDIR") or tempfile.gettempdir()
    CACHE_DIR = cache_dir()
    VENV_DIR = os.path.join(CACHE_DIR, "venv")
    WORK_DIR = os.path.join(CACHE_DIR, "work")

    PY_CMD = find_python(ARGS.python)
    rc, v = run_cmd(PY_CMD + ["--version"])
    ok(f"Python detected: {v.strip()}")

    try:
        setup_venv(PY_CMD)

        # License registration / detection
        if ARGS.regcode:
            register_license(ARGS.regcode, ARGS.product)
        license_type, _ = license_info()
        LICENSE_TYPE = license_type
        info(f"PyArmor license: {license_type}")

        opts = choose_obfuscation_options(license_type)
        stage_copy()
        run_pyarmor(opts)
        overlay_assets()
        write_readme(PY_CMD)
        verify_build(PY_CMD)
        make_zip()
    finally:
        if ARGS.keep_temp:
            for d in ("STAGE", "OUT"):
                if d in globals():
                    info(f"Keeping temp dirs: {d}={globals()[d]}")
        else:
            for d in ("STAGE", "OUT"):
                if d in globals():
                    shutil.rmtree(globals()[d], ignore_errors=True)

    print()
    ok(f"Done. Original code was never modified — only {ZIP_OUT} was created.")
    print(f"{CYAN}To distribute: extract the zip and run 'python limey.py' with the same Python version.{RESET}")


if __name__ == "__main__":
    main()
