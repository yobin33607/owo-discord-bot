 #!/usr/bin/env bash
# =============================================================================
# obfuscate_limey.sh — Build a zipped, obfuscated copy of the Limey project.
#
# WHAT IT DOES
#   * Works entirely on a COPY of the source — the original code is never
#     modified (only the final .zip is written into this directory).
#   * Uses PyArmor (https://pyarmor.readthedocs.io/) to obfuscate every Limey
#     Python script with the highest protection your license allows.
#   * Packages everything (obfuscated scripts + PyArmor runtime + vendored
#     discord library + dashboard assets + models) into a single .zip.
#
# OBFUSCATION LEVELS (auto-detected from your PyArmor license)
#   trial   --enable jit                              (restricted: files over the
#                                                      size limit are SKIPPED
#                                                      and shipped un-obfuscated;
#                                                      no mix-str / obf-code 2)
#   basic   --obf-code 2 --mix-str --enable jit       (free non-commercial
#                                                      license — recommended)
#   pro     --obf-code 2 --mix-str --enable jit,rft,bcc   (strongest possible;
#                                                      opt-in via --pro)
#
# TRIAL LICENSE
#   With the default trial license the build still completes: files larger than
#   the trial limit (default 32KB, see --max-file-size) are skipped and shipped
#   as plain Python instead of failing the whole build.
#
# GETTING THE FREE BASIC LICENSE (for FULL obfuscation)
#   Register the free non-commercial (non-profits) license:
#     1. Get an activation code  ->  https://pyarmor.dashingsoft.com/register/
#        (send it to yourself; it arrives as pyarmor-regcode-xxxx.txt)
#     2. ./obfuscate_limey.sh --regcode pyarmor-regcode-xxxx.txt
#   After that, just run the script normally on this machine.
#
# USAGE
#   ./obfuscate_limey.sh [options]
#
# OPTIONS
#   --regcode <file>      Register a PyArmor license first (activation .txt or
#                         registration .zip) then build. Needs internet the
#                         first time.
#   --product <name>      Product name for registration (default: non-profits)
#   --pro                 Enable RFT+BCC modes (requires a Pro license).
#                         WARNING: RFT renames names; this project dispatches
#                         to cogs by class-name strings (e.g. get_cog("Grinding")),
#                         so --pro builds may need manual fixes to run.
#   --max-file-size <b>   Skip files larger than this many bytes instead of
#                         failing. Default 32768 (32KB). NOTE: the trial's real
#                         per-file cutoff is ~35KB — raising this above that
#                         will fail on oversized files.
#   --force-trial         With the trial license, obfuscate EVERYTHING and let
#                         oversized files fail (no skipping).
#   --project <dir>       Build from this project copy instead of the script dir.
#   --python <path>       Python interpreter to use (default: first 3.10+ found).
#   --out <file>          Output .zip path (default: limey-obfuscated-<ts>.zip
#                         in this directory).
#   --verify              Run a smoke test on the obfuscated output before
#                         zipping (imports limey_ascii through the runtime).
#   --keep-temp           Keep the temporary build/staging directories.
#   -h, --help            Show this help.
#
# PLATFORM SUPPORT
#   Linux  : ./obfuscate_limey.sh      (native bash — default platform)
#   macOS  : ./obfuscate_limey.sh      (native bash; macOS ships with bash)
#   Windows: obfuscate_limey.bat       (delegates to obfuscate_limey.py)
#
# NOTE ON COMPATIBILITY
#   Obfuscated code is tied to the Python version + platform used to build it.
#   Build the .zip ON the OS you intend to distribute to:
#   Linux -> Linux builds, macOS -> macOS builds, Windows -> Windows builds.
# =============================================================================

set -Eeuo pipefail

APP_NAME="Limey"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/limey-obfuscate"
VENV_DIR="$CACHE_DIR/venv"
WORK_DIR="$CACHE_DIR/work"          # neutral cwd for pyarmor -> keeps .pyarmor/ out of the project
TMPBASE="${TMPDIR:-/tmp}"           # portable temp root (macOS sets TMPDIR; Linux defaults to /tmp)

ZIP_OUT=""
KEEP_TEMP=0
FORCE_TRIAL=0
PRO_FLAG=0
VERIFY=0
PY_CMD=""
REGCODE=""
PRODUCT_NAME="non-profits"
MAX_FILE_SIZE=""
LICENSE_TYPE=""

# Every top-level script/package that belongs to Limey and gets obfuscated.
OBFUSCATE_TARGETS=(
    "limey.py"
    "limey_setup.py"
    "run_manager_bot.py"
    "proxy_server.py"
    "regen_github_token.py"
    "core"
    "modules"
    "cogs"
    "utils"
    "dashboard"
    "limey_engines"
    "component_v2_limey"
    "limey_ascii"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
RED="\033[1;31m"; GREEN="\033[1;32m"; CYAN="\033[1;36m"; YELLOW="\033[1;33m"; RESET="\033[0m"

fail()    { echo -e "${RED}[X] $*${RESET}" >&2; exit 1; }
ok()      { echo -e "${GREEN}[OK] $*${RESET}"; }
info()    { echo -e "${CYAN}[*] $*${RESET}"; }
warn()    { echo -e "${YELLOW}[!] $*${RESET}"; }

usage() {
    sed -n '2,90p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

find_python() {
    local candidates=(python3.12 python3.11 python3.10 python3)
    local cmd
    if [ -n "$PY_CMD" ] && [ -x "$PY_CMD" ]; then
        echo "$PY_CMD"; return 0
    fi
    # Prefer the project's own venv so the obfuscated runtime matches the
    # exact Python version Limey actually runs under.
    if [ -x "$PROJECT_DIR/.venv/bin/python" ] && "$PROJECT_DIR/.venv/bin/python" - <<'PY' 2>/dev/null
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
    then
        echo "$PROJECT_DIR/.venv/bin/python"; return 0
    fi
    for cmd in "${candidates[@]}"; do
        if command -v "$cmd" >/dev/null 2>&1 && "$cmd" - <<'PY' 2>/dev/null
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
        then
            echo "$cmd"; return 0
        fi
    done
    return 1
}

# Portable replacement for GNU `find -maxdepth 1` (macOS's BSD find lacks it).
# Prints the pyarmor runtime dir directly inside $OUT, if present.
find_runtime_dir() {
    local d
    for d in "$OUT"/pyarmor_runtime_*; do
        [ -d "$d" ] && { echo "$d"; return 0; }
    done
    return 1
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --regcode)         REGCODE="${2:?--regcode needs a file}"; shift 2 ;;
        --product)         PRODUCT_NAME="${2:?--product needs a name}"; shift 2 ;;
        --project)         PROJECT_DIR="${2:?--project needs a dir}"; shift 2 ;;
        --python)          PY_CMD="${2:?--python needs a path}"; shift 2 ;;
        --out)             ZIP_OUT="${2:?--out needs a path}"; shift 2 ;;
        --pro)             PRO_FLAG=1; shift ;;
        --max-file-size)   MAX_FILE_SIZE="${2:?--max-file-size needs a byte count}"; shift 2 ;;
        --force-trial)     FORCE_TRIAL=1; shift ;;
        --verify)          VERIFY=1; shift ;;
        --keep-temp)       KEEP_TEMP=1; shift ;;
        -h|--help)         usage ;;
        *)                 fail "Unknown option: $1 (see --help)" ;;
    esac
done

[ -d "$PROJECT_DIR" ] || fail "Project directory not found: $PROJECT_DIR"
[ -z "$ZIP_OUT" ] && ZIP_OUT="$SCRIPT_DIR/limey-obfuscated-$TIMESTAMP.zip"
[ -f "$PROJECT_DIR/limey.py" ] || fail "limey.py not found in $PROJECT_DIR — is this the Limey project?"

# --max-file-size must be a positive integer
if [ -n "$MAX_FILE_SIZE" ] && ! [[ "$MAX_FILE_SIZE" =~ ^[0-9]+$ ]]; then
    fail "--max-file-size must be a positive integer (bytes), got: $MAX_FILE_SIZE"
fi

echo -e "${CYAN}=== ${APP_NAME} Obfuscation Builder ===${RESET}"

# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------
OS_NAME="$(uname -s)"
case "$OS_NAME" in
    Linux|Darwin) : ;;
    *) fail "This script supports Linux and macOS only (got: $OS_NAME). On Windows use obfuscate_limey.bat instead." ;;
esac


PY_CMD="$(find_python)" || fail "Python 3.10+ not found. Install it or pass --python /path/to/python."
ok "Python detected: $("$PY_CMD" --version)"

if command -v zip >/dev/null 2>&1; then
    ZIP_TOOL="zip"
else
    ZIP_TOOL="python"
    info "The 'zip' command is missing — will zip with Python's zipfile module instead."
fi

# ---------------------------------------------------------------------------
# Obfuscation venv (cached outside the project — never touches the source)
# ---------------------------------------------------------------------------
setup_venv() {
    mkdir -p "$WORK_DIR"
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        info "Creating obfuscation virtual environment (first run)..."
        "$PY_CMD" -m venv "$VENV_DIR"
        "$VENV_DIR/bin/pip" install -q --upgrade pip
    fi
    info "Ensuring PyArmor is installed..."
    "$VENV_DIR/bin/pip" install -q --upgrade pyarmor
    PYARMOR_BIN="$VENV_DIR/bin/pyarmor"
    if [ ! -x "$PYARMOR_BIN" ]; then
        fail "PyArmor install failed. Check your internet connection and try again."
    fi
    ok "PyArmor ready: $("$PYARMOR_BIN" --version 2>&1 | head -1)"
}

# ---------------------------------------------------------------------------
# License handling
# ---------------------------------------------------------------------------
license_info() {
    # Prints: "<license-type>|<product>" e.g. "pyarmor-trial|non-profits"
    (cd "$WORK_DIR" && "$PYARMOR_BIN" -v 2>&1 || true) | awk '
        /License Type/ { type=$NF }
        /License Product/ { product=$NF }
        /License To/ { to=$NF }
        END { print (type=="" ? "unknown" : type) "|" (product=="" ? to : product) }
    '
}

register_license() {
    local file="$1"
    [ -f "$file" ] || fail "License file not found: $file"
    info "Registering PyArmor license from $file ..."
    case "$file" in
        *.zip)
            # Registration file (already activated on another machine)
            (cd "$WORK_DIR" && "$PYARMOR_BIN" reg "$file") || fail "License registration failed."
            ;;
        *)
            # Activation code file (first-time registration; needs internet)
            (cd "$WORK_DIR" && "$PYARMOR_BIN" reg -p "$PRODUCT_NAME" "$file") || \
                fail "License registration failed. Is the code valid and is there internet access?"
            ;;
    esac
    ok "License registered."
}

choose_obfuscation_options() {
    local license="$1"
    local -a opts=()

    case "$license" in
        *pro*|*group*)
            opts+=(--obf-code 2 --mix-str)
            if [ "$PRO_FLAG" = 1 ]; then
                local vout rft= bcc= enable="jit"
                vout="$(cd "$WORK_DIR" && "$PYARMOR_BIN" -v 2>&1 || true)"
                echo "$vout" | grep -qi "RFT Mode.*Yes" && { rft=1; enable="jit,rft"; }
                echo "$vout" | grep -qi "BCC Mode.*Yes" && { bcc=1; enable="$enable,bcc"; }
                if [ -n "$rft$bcc" ]; then
                    warn "Enabling RFT/BCC modes (max security). String-based cog dispatch may break — test the build!"
                    opts+=(--enable "$enable")
                else
                    warn "--pro requested but this license does not enable RFT/BCC. Using basic+JIT options."
                    opts+=(--enable jit)
                fi
            else
                info "Pro license detected — pass --pro to also enable RFT/BCC (highest security; may affect dynamic cog dispatch)."
                opts+=(--enable jit)
            fi
            ;;
        *basic*)
            [ "$PRO_FLAG" = 1 ] && warn "--pro requires a Pro license — using the best options this license allows."
            opts+=(--obf-code 2 --mix-str --enable jit)
            ;;
        *ci*)
            fail "PyArmor CI licenses only work in CI pipelines — register a Basic/Pro license on this machine."
            ;;
        *trial*)
            if [ "$FORCE_TRIAL" = 1 ]; then
                warn "Attempting FULL obfuscation with the trial license — oversized files will fail."
            else
                warn "Trial license: files over ${MAX_FILE_SIZE:-32768} bytes will be SKIPPED and shipped un-obfuscated."
                warn "Register the free non-commercial license for full obfuscation (see --help)."
            fi
            opts+=(--enable jit)
            ;;
        *)
            fail "Could not determine PyArmor license type ('$license'). Run '$PYARMOR_BIN -v' to inspect."
            ;;
    esac
    OBFUSCATION_OPTS=("${opts[@]}")
}

# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------
stage_copy() {
    STAGE="$(mktemp -d "$TMPBASE/limey-stage-XXXXXX")"
    info "Copying project to staging area (originals untouched): $STAGE"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete \
            --exclude '/.venv/' --exclude '/.git/' --exclude '/__pycache__/' \
            --exclude '*.pyc' --exclude '/config/' --exclude '/.env' \
            --exclude '/.env.*' --exclude '/tokens.txt' --exclude '/data/' \
            --exclude '/prebuilt/' --exclude '/*.zip' --exclude '/dist/' \
            --exclude '/build/' --exclude '/.obfuscate*/' --exclude '/.github/' \
            "$PROJECT_DIR/" "$STAGE/"
    else
        tar --exclude='./.venv' --exclude='./.git' --exclude='./__pycache__' \
            --exclude='./config' --exclude='./.env' --exclude='./.env.*' \
            --exclude='./tokens.txt' --exclude='./data' --exclude='./prebuilt' \
            --exclude='./*.zip' --exclude='./dist' --exclude='./build' \
            --exclude='./.obfuscate*' --exclude='./.github' --exclude='*.pyc' \
            -C "$PROJECT_DIR" -cf - . | tar -C "$STAGE" -xf -
    fi
    info "Staged $(find "$STAGE" -name '*.py' | wc -l) Python files."
}

run_pyarmor() {
    OUT="$(mktemp -d "$TMPBASE/limey-out-XXXXXX")"

    local max_size="${MAX_FILE_SIZE:-32768}"
    local skip_large=0
    if [ "$LICENSE_TYPE" = "pyarmor-trial" ] && [ "$FORCE_TRIAL" != 1 ]; then
        skip_large=1
    fi

    info "Obfuscating with options: ${OBFUSCATION_OPTS[*]:-defaults}"
    local -a inputs=() excludes=()
    SKIPPED_FILES=()
    local t f
    for t in "${OBFUSCATE_TARGETS[@]}"; do
        [ -e "$STAGE/$t" ] || continue
        if [ -d "$STAGE/$t" ]; then
            # Package: exclude oversized modules so the rest of the package
            # still gets obfuscated (they are copied in as plain files later).
            # Pattern is path-specific (*/rel/path) to avoid excluding any
            # same-named file in another package.
            if [ "$skip_large" = 1 ]; then
                while IFS= read -r f; do
                    [ -n "$f" ] || continue
                    SKIPPED_FILES+=("$f")
                    excludes+=(--exclude "*/${f#$STAGE/}")
                done < <(find "$STAGE/$t" -name '*.py' -size +"$max_size"c)
            fi
            inputs+=("$STAGE/$t")
        elif [ -f "$STAGE/$t" ]; then
            if [ "$skip_large" = 1 ] && [ "$(wc -c < "$STAGE/$t")" -gt "$max_size" ]; then
                SKIPPED_FILES+=("$STAGE/$t")
            else
                inputs+=("$STAGE/$t")
            fi
        fi
    done
    [ ${#inputs[@]} -gt 0 ] || fail "No obfuscation targets found in staging dir."

    if [ ${#SKIPPED_FILES[@]} -gt 0 ]; then
        warn "Skipping ${#SKIPPED_FILES[@]} file(s) over the ${max_size}-byte limit (will be shipped un-obfuscated):"
        for f in "${SKIPPED_FILES[@]}"; do
            echo "    ${f#$STAGE/}"
        done
    fi

    local log
    # stdin from /dev/null so an interactive pyarmor prompt can never hang a build
    log="$(cd "$WORK_DIR" && "$PYARMOR_BIN" gen -r -O "$OUT" "${OBFUSCATION_OPTS[@]}" "${excludes[@]}" "${inputs[@]}" </dev/null 2>&1)" || {
        if echo "$log" | grep -qi "out of license"; then
            {
                echo
                echo -e "${RED}[X] PyArmor refused to obfuscate part of the project (out of license).${RESET}"
                echo "    The current license cannot handle the full Limey codebase."
                echo
                echo "    Register the FREE non-commercial license:"
                echo "      1. Get an activation code at https://pyarmor.dashingsoft.com/register/"
                echo "      2. Run:  $0 --regcode /path/to/pyarmor-regcode-xxxx.txt"
                echo
            } >&2
        else
            echo "$log" >&2
            fail "PyArmor obfuscation failed. See output above."
        fi
        exit 1
    }
    echo "$log" | tail -n 8
    ok "Obfuscation complete."

    # Ship oversized files as plain copies so the build stays complete/runnable
    if [ ${#SKIPPED_FILES[@]} -gt 0 ]; then
        local rel
        for f in "${SKIPPED_FILES[@]}"; do
            rel="${f#$STAGE/}"
            mkdir -p "$(dirname "$OUT/$rel")"
            cp -a "$f" "$OUT/$rel"
        done
        ok "Copied ${#SKIPPED_FILES[@]} un-obfuscated file(s) into the build."
    fi
}

overlay_assets() {
    info "Adding runtime assets (not obfuscated)..."
    # Oversized files that were skipped by the trial limit were already copied
    # back in at the end of run_pyarmor().
    # Vendored discord.py used by the manager bot (kept plain on purpose)
    if [ -d "$STAGE/manager_bot_discord" ]; then
        cp -a "$STAGE/manager_bot_discord" "$OUT/"
    fi
    # Dashboard web assets (skip if pyarmor already carried them over)
    if [ -d "$STAGE/dashboard/templates" ] && [ ! -e "$OUT/dashboard/templates" ]; then
        mkdir -p "$OUT/dashboard"; cp -a "$STAGE/dashboard/templates" "$OUT/dashboard/"
    fi
    if [ -d "$STAGE/dashboard/static" ] && [ ! -e "$OUT/dashboard/static" ]; then
        mkdir -p "$OUT/dashboard"; cp -a "$STAGE/dashboard/static" "$OUT/dashboard/"
    fi
    # Data / resources
    [ -f "$STAGE/utils/emojis.json" ] && [ ! -e "$OUT/utils/emojis.json" ] && cp -a "$STAGE/utils/emojis.json" "$OUT/utils/"
    [ -d "$STAGE/beeps" ]             && cp -a "$STAGE/beeps" "$OUT/"
    [ -d "$STAGE/models" ]            && cp -a "$STAGE/models" "$OUT/"
    # Docs / dependency manifest
    for f in requirements.txt README.md LICENSE; do
        [ -f "$STAGE/$f" ] && cp -a "$STAGE/$f" "$OUT/"
    done
    ok "Assets added."
}

write_readme() {
    local runtime_dir runtime_name
    runtime_dir="$(find_runtime_dir || true)"
    runtime_name="$(basename "${runtime_dir:-pyarmor_runtime_*}")"
    cat > "$OUT/README-OBFUSCATED.txt" <<EOF
Limey — obfuscated build
========================
This is an obfuscated copy of the Limey source produced by obfuscate_limey.sh.

  Built on : $(uname -srm)
  Built for: Python $("$PY_CMD" -c 'import platform; print(platform.python_version())')
  Runtime  : $runtime_name  (must be distributed together with the scripts)

How to run
  1. Extract this archive.
  2. Create a virtual environment with the SAME Python version listed above
     and install requirements.txt into it.
  3. First run:  <venv>/bin/python limey_setup.py
  4. Start:      <venv>/bin/python limey.py

Notes
  * The obfuscated code only runs on the Python version / OS it was built for.
  * config/, data/, .env and other runtime state are NOT included — provide
    your own (keys, accounts, settings) as usual.
  * Do NOT run obfuscation on this extracted copy. Keep the original repo as
    the source of truth.
EOF
    if [ ${#SKIPPED_FILES[@]} -gt 0 ]; then
        {
            echo
            echo "Un-obfuscated files (exceeded the build's size limit):"
            for f in "${SKIPPED_FILES[@]}"; do
                echo "  - ${f#$STAGE/}"
            done
        } >> "$OUT/README-OBFUSCATED.txt"
    fi
    ok "Wrote README-OBFUSCATED.txt"
}

verify_build() {
    info "Verifying obfuscated output..."
    local src_count=0 out_count=0 t
    for t in "${OBFUSCATE_TARGETS[@]}"; do
        [ -e "$STAGE/$t" ] && src_count=$((src_count + $(find "$STAGE/$t" -name '*.py' 2>/dev/null | wc -l)))
    done
    out_count="$(find "$OUT" \( -path "$OUT/manager_bot_discord" -o -path "$OUT/pyarmor_runtime_*" \) -prune -o -name '*.py' -print | wc -l)"
    if [ "$src_count" -eq "$out_count" ]; then
        ok "All $src_count scripts have an obfuscated counterpart."
    else
        warn "Script count mismatch: $src_count source vs $out_count obfuscated."
    fi

    local runtime_dir
    runtime_dir="$(find_runtime_dir || true)"
    if [ -z "$runtime_dir" ]; then
        fail "PyArmor runtime package missing from the output!"
    fi
    ok "PyArmor runtime present: $(basename "$runtime_dir")"

    if [ "$VERIFY" = 1 ]; then
        info "Smoke test: importing obfuscated limey_ascii through the runtime..."
        "$VENV_DIR/bin/pip" install -q rich
        (cd "$OUT" && "$VENV_DIR/bin/python" -c "
import limey_ascii.limey_ascii as m
assert m.AUTHOR
print('  smoke test OK — obfuscated module imports and runs')
") || fail "Smoke test failed — the obfuscated output is not importable. Inspect with --keep-temp."
    fi
}

make_zip() {
    info "Creating archive: $ZIP_OUT"
    mkdir -p "$(dirname "$ZIP_OUT")"
    # Keep bytecode caches out of the archive
    find "$OUT" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
    if [ "$ZIP_TOOL" = "zip" ]; then
        (cd "$OUT" && zip -qr "$ZIP_OUT" . -x '*__pycache__*' '*.pyc')
    else
        "$VENV_DIR/bin/python" - "$ZIP_OUT" "$OUT" <<'PY'
import sys, zipfile, os
zip_path, out_dir = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(out_dir):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            if f.endswith('.pyc'):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, out_dir)
            z.write(full, rel)
PY
    fi
    ok "Archive created: $ZIP_OUT ($(du -h "$ZIP_OUT" | cut -f1))"
}

cleanup() {
    if [ "${KEEP_TEMP:-0}" = 1 ]; then
        info "Keeping temp dirs: STAGE=${STAGE:-unset} OUT=${OUT:-unset}"
    else
        [ -n "${STAGE:-}" ] && rm -rf "$STAGE"
        [ -n "${OUT:-}" ]   && rm -rf "$OUT"
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    setup_venv

    # License registration / detection
    if [ -n "$REGCODE" ]; then
        # Resolve to an absolute path (registration runs from another cwd).
        # readlink -f is not available on macOS, so resolve via the detected Python.
        REGCODE="$("$PY_CMD" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$REGCODE")"
        register_license "$REGCODE"
    fi
    LICENSE_INFO="$(license_info)"
    LICENSE_TYPE="${LICENSE_INFO%%|*}"
    info "PyArmor license: $LICENSE_TYPE"

    choose_obfuscation_options "$LICENSE_TYPE"

    stage_copy
    run_pyarmor
    overlay_assets
    write_readme
    verify_build
    make_zip

    echo
    ok "Done. Original code was never modified — only $ZIP_OUT was created."
    echo -e "${CYAN}To distribute: extract the zip and run 'python limey.py' with the same Python version.${RESET}"
}

main
