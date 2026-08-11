#!/usr/bin/env bash

set -Eeuo pipefail

APP_NAME="Limey"
REPO_URL="https://github.com/yobin33607/owo-discord-bot.git"

RED="\033[1;31m"
GREEN="\033[1;32m"
CYAN="\033[1;36m"
YELLOW="\033[1;33m"
RESET="\033[0m"

fail() {
    echo -e "${RED}[X] $1${RESET}"
    exit 1
}

ok() {
    echo -e "${GREEN}[OK] $1${RESET}"
}

info() {
    echo -e "${CYAN}[*] $1${RESET}"
}

warn() {
    echo -e "${YELLOW}[!] $1${RESET}"
}


if [ -d "/data/data/com.termux/files/usr" ]; then
    PLATFORM="termux"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    PLATFORM="macos"
else
    PLATFORM="linux"
fi


case "$PLATFORM" in
    termux)
        if [ ! -d "$HOME/storage" ]; then
            warn "Termux storage permission required"
            termux-setup-storage || true
            echo -e "${YELLOW}Please tap 'ALLOW' on the popup on your screen.${RESET}"
            read -p "Press ENTER after you have allowed storage permission..."
        fi
        if [ -d "$HOME/storage/downloads" ]; then
            INSTALL_DIR="$HOME/storage/downloads/limey"
        else
            INSTALL_DIR="$HOME/limey"
        fi
        ;;
    macos)
        if command -v xdg-user-dir >/dev/null 2>&1; then
            INSTALL_DIR="$(xdg-user-dir DOWNLOAD)/limey"
        else
            INSTALL_DIR="$HOME/Downloads/limey"
        fi
        ;;
    linux)
        if command -v xdg-user-dir >/dev/null 2>&1; then
            BASE_DIR="$(xdg-user-dir DOWNLOAD)"
        elif [ -d "$HOME/Downloads" ]; then
            BASE_DIR="$HOME/Downloads"
        else
            BASE_DIR="$HOME"
        fi
        INSTALL_DIR="$BASE_DIR/limey"
        ;;
esac


find_python() {
    for cmd in python3.10 python3 python python3.11 python3.12; do
        if command -v "$cmd" >/dev/null 2>&1; then
            if "$cmd" - <<'PY'
import sys
exit(0 if sys.version_info >= (3,10) else 1)
PY
            then
                echo "$cmd"
                return
            fi
        fi
    done
}

PY_CMD="$(find_python || true)"

install_python() {
    info "Attempting to auto-install Python 3..."
    case "$PLATFORM" in
        termux)
            pkg update -y && pkg install python -y
            ;;
        macos)
            if command -v brew >/dev/null 2>&1; then
                brew install python
            else
                fail "Homebrew missing. Install Python manually from python.org."
            fi
            ;;
        linux)
            if command -v apt >/dev/null 2>&1; then
                sudo apt update
                sudo apt install -y python3 python3-pip python3-venv
            elif command -v dnf >/dev/null 2>&1; then
                sudo dnf install -y python3 python3-pip
            elif command -v yum >/dev/null 2>&1; then
                sudo yum install -y python3 python3-pip
            elif command -v pacman >/dev/null 2>&1; then
                sudo pacman -Sy --noconfirm python python-pip
            elif command -v apk >/dev/null 2>&1; then
                sudo apk add python3 py3-pip
            else
                fail "No supported package manager found. Please install Python 3.10+ manually."
            fi
            ;;
    esac
}

if [ -z "$PY_CMD" ]; then
    warn "Python 3.10+ not found."
    install_python
    PY_CMD="$(find_python || true)"
    if [ -z "$PY_CMD" ]; then
        fail "Failed to install Python 3.10+. Please install it manually."
    fi
fi

ok "Python detected: $($PY_CMD --version)"


install_git() {
    case "$PLATFORM" in
        termux)
            pkg update -y && pkg install git -y
            ;;
        macos)
            if command -v brew >/dev/null 2>&1; then
                brew install git
            else
                fail "Git missing. Install Homebrew or Git manually (brew install git)."
            fi
            ;;
        linux)
            if command -v apt >/dev/null 2>&1; then
                sudo apt update
                sudo apt install -y git
            elif command -v dnf >/dev/null 2>&1; then
                sudo dnf install -y git
            elif command -v yum >/dev/null 2>&1; then
                sudo yum install -y git
            elif command -v pacman >/dev/null 2>&1; then
                sudo pacman -Sy --noconfirm git
            elif command -v apk >/dev/null 2>&1; then
                sudo apk add git
            else
                fail "No supported package manager found. Please install Git manually."
            fi
            ;;
    esac
}

if ! command -v git >/dev/null 2>&1; then
    warn "Git not found"
    install_git
fi

command -v git >/dev/null 2>&1 || fail "Git installation failed"

ok "Git detected"

mkdir -p "$(dirname "$INSTALL_DIR")"

if [ -d "$INSTALL_DIR" ]; then
    if [ -d "$INSTALL_DIR/.git" ]; then
        info "Updating existing installation"
        cd "$INSTALL_DIR"
        git pull || fail "Update failed"
    else
        fail "Installation directory exists but is not a Limey repository"
    fi
else
    info "Downloading Limey"
    git clone "$REPO_URL" "$INSTALL_DIR" || fail "Clone failed"
fi

cd "$INSTALL_DIR"

if [ ! -f "limey_setup.py" ]; then
    fail "limey_setup.py not found"
fi

# ────────────────────────────────────────────────────
#  Virtual environment setup
# ────────────────────────────────────────────────────
VENV_DIR="$INSTALL_DIR/.venv"

REPO_PATH="${REPO_URL%.git}"
REPO_PATH="${REPO_PATH#https://github.com/}"
PREBUILT_BASE="https://raw.githubusercontent.com/$REPO_PATH/main/prebuilt"

venv_is_usable() {
    if [ -x "$VENV_DIR/bin/python" ]; then
        "$VENV_DIR/bin/python" --version >/dev/null 2>&1
    elif [ -x "$VENV_DIR/Scripts/python" ]; then
        "$VENV_DIR/Scripts/python" --version >/dev/null 2>&1
    else
        return 1
    fi
}

try_download_prebuilt() {
    local archive=""
    case "$PLATFORM" in
        termux)
            return 1  # no pre-built venv for termux (different libc/arch)
            ;;
        macos)
            archive="venv-macos.tar.gz"
            ;;
        linux)
            archive="venv-linux.tar.gz"
            ;;
        *)
            return 1
            ;;
    esac

    local url="$PREBUILT_BASE/$archive"
    local tmp
    tmp="$(mktemp -d)" || return 1

    info "Downloading pre-built venv ($archive)..."
    if ! curl -fsSL -o "$tmp/$archive" "$url"; then
        warn "Pre-built venv unavailable — building locally"
        rm -rf "$tmp"
        return 1
    fi

    # Replace any existing .venv with the fresh pre-built one
    if [ -d "$VENV_DIR" ]; then
        info "Replacing existing virtual environment"
        rm -rf "$VENV_DIR"
    fi

    info "Extracting pre-built venv..."
    if ! tar -xzf "$tmp/$archive" -C "$INSTALL_DIR"; then
        warn "Failed to extract pre-built venv — building locally"
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"

    if venv_is_usable; then
        ok "Pre-built virtual environment ready"
        VENV_PY="$VENV_DIR/bin/python"
        return 0
    fi

    warn "Pre-built venv incompatible with this system — building locally"
    rm -rf "$VENV_DIR"
    return 1
}

create_venv() {
    if [ -d "$VENV_DIR" ]; then
        info "Virtual environment already exists"
    else
        info "Creating virtual environment..."
        "$PY_CMD" -m venv "$VENV_DIR" || fail "Failed to create virtual environment"
        ok "Virtual environment created at $VENV_DIR"
    fi

    # Determine the venv Python path
    if [ -f "$VENV_DIR/bin/python" ]; then
        VENV_PY="$VENV_DIR/bin/python"
    elif [ -f "$VENV_DIR/Scripts/python" ]; then
        VENV_PY="$VENV_DIR/Scripts/python"
    else
        fail "Could not find Python inside virtual environment"
    fi

    ok "Using venv Python: $VENV_PY"

    # Upgrade pip inside venv
    info "Upgrading pip..."
    "$VENV_PY" -m pip install --upgrade pip --quiet || warn "pip upgrade skipped"

    # Install requirements into venv
    if [ -f "requirements.txt" ]; then
        info "Installing dependencies into virtual environment..."
        "$VENV_PY" -m pip install -r requirements.txt --no-cache-dir || warn "Some packages may have failed"
        ok "Dependencies installed"
    fi

    PY_CMD="$VENV_PY"
}

# Use the pre-built venv from the repo if possible; otherwise build locally
if [ -d "$VENV_DIR" ] && venv_is_usable; then
    info "Virtual environment already exists"
    create_venv
elif try_download_prebuilt; then
    info "Using pre-built virtual environment"
    PY_CMD="$VENV_PY"
else
    # Only reached when no usable venv exists — start from a clean slate
    rm -rf "$VENV_DIR"
    create_venv
fi

info "Starting setup (--quick)"
"$PY_CMD" limey_setup.py --quick || fail "Setup failed"

echo
ok "Limey installed successfully"
echo -e "${CYAN}Location:${RESET} $INSTALL_DIR"
echo -e "${CYAN}Run:${RESET} cd \"$INSTALL_DIR\" && $PY_CMD limey.py"
echo -e "${CYAN}Setup:${RESET} cd \"$INSTALL_DIR\" && $PY_CMD limey_setup.py"
echo -e "${CYAN}Activate venv:${RESET} source \"$INSTALL_DIR/.venv/bin/activate\""