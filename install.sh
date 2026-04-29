#!/usr/bin/env bash
set -e

# ── Colours ───────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}  ✓${RESET} $1"; }
warn() { echo -e "${YELLOW}  !${RESET} $1"; }
die()  { echo -e "${RED}  ✗${RESET} $1"; exit 1; }
step() { echo -e "\n${BOLD}$1${RESET}"; }

SL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SL_DIR/.venv"

echo -e "${BOLD}"
echo "  ┌─────────────────────────────────┐"
echo "  │   SL Language — Installer       │"
echo "  └─────────────────────────────────┘"
echo -e "${RESET}"

# ── 1. Python ─────────────────────────────────────────────────────────
step "1/5  Checking Python"

if ! command -v python3 &>/dev/null; then
    die "Python 3 not found. Install from https://python.org"
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    die "Python 3.10+ required (found $PY_VER)"
fi
ok "Python $PY_VER"

# ── 2. Virtual environment ────────────────────────────────────────────
step "2/5  Setting up virtual environment"

if [ -d "$VENV" ]; then
    ok "Virtual environment already exists at .venv"
else
    python3 -m venv "$VENV"
    ok "Created .venv"
fi

# Activate for the rest of this script
source "$VENV/bin/activate"

# ── 3. llvmlite ───────────────────────────────────────────────────────
step "3/5  Installing dependencies into venv"

if python -c "import llvmlite" &>/dev/null; then
    LLVM_VER=$(python -c "import llvmlite; print(llvmlite.__version__)")
    ok "llvmlite $LLVM_VER already installed"
else
    warn "Installing llvmlite (this may take a moment)..."
    pip install --quiet llvmlite || die "Failed to install llvmlite"
    LLVM_VER=$(python -c "import llvmlite; print(llvmlite.__version__)")
    ok "llvmlite $LLVM_VER installed"
fi

# ── 4. ARC runtime ────────────────────────────────────────────────────
step "4/5  Building ARC runtime"

if ! command -v gcc &>/dev/null; then
    die "gcc not found. Install build tools:
  Ubuntu/Debian:  sudo apt install build-essential
  macOS:          xcode-select --install
  Fedora:         sudo dnf install gcc"
fi

if ! command -v make &>/dev/null; then
    die "make not found. Install build tools:
  Ubuntu/Debian:  sudo apt install build-essential
  macOS:          xcode-select --install
  Fedora:         sudo dnf install make"
fi

cd "$SL_DIR/arc"
make shared --quiet
ok "libarc.so built"
cd "$SL_DIR"

# ── 5. Install 'sl' command ───────────────────────────────────────────
step "5/5  Installing 'sl' command"

SL_BIN="$HOME/.local/bin/sl"
mkdir -p "$HOME/.local/bin"

# Wrapper uses the venv Python — no activation needed by the user
cat > "$SL_BIN" << SLEOF
#!/usr/bin/env bash
export PYTHONDONTWRITEBYTECODE=1
exec "$VENV/bin/python" "$SL_DIR/codegen.py" "\$@"
SLEOF

chmod +x "$SL_BIN"
ok "Installed to $SL_BIN"
ok "Using venv at $VENV"

# Check PATH
if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
    warn "$HOME/.local/bin is not in your PATH"
    echo ""
    echo "  Add this to your shell config (~/.bashrc, ~/.zshrc, etc.):"
    echo -e "  ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}"
    echo ""
    echo "  Then reload: source ~/.bashrc  (or open a new terminal)"
    echo ""
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── Smoke test ────────────────────────────────────────────────────────
echo ""
if "$VENV/bin/python" "$SL_DIR/codegen.py" "$SL_DIR/example.sl" --run &>/dev/null; then
    ok "Smoke test passed"
else
    warn "Smoke test had issues — run:  sl example.sl --run"
fi

echo -e "\n${GREEN}${BOLD}  SL installed successfully!${RESET}\n"
echo "  Try it:"
echo -e "  ${BOLD}sl example.sl --run${RESET}"
echo -e "  ${BOLD}sl advanced.sl --run${RESET}"
echo ""
echo "  To update dependencies later:"
echo -e "  ${BOLD}source $VENV/bin/activate && pip install --upgrade llvmlite${RESET}"
echo ""
