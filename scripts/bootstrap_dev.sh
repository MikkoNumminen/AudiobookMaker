#!/usr/bin/env bash
# One-shot dev bootstrap for AudiobookMaker (Linux / macOS / Git Bash).
#
# Idempotent: re-running it does not duplicate a PATH entry or rebuild the
# venv from scratch. It only does the work that is still missing.
#
# What it does, in order:
#   1. Create .venv in the repo root if it isn't there yet.
#   2. Using that venv's python/pip, install runtime deps (requirements.txt
#      if present) and the package itself in editable mode (pip install -e .).
#   3. Find the venv's bin dir; if it isn't on PATH, append one export line to
#      ~/.bashrc (only if not already there) and tell you to open a fresh shell.
#   4. Run scripts/check_cli_install.py to print shim / PATH / GUI-shadow status,
#      then `audiobookmaker-cli --version` if the command resolves.
#   5. Print a success + next-steps message.
#
# Usage:
#   bash scripts/bootstrap_dev.sh
#
# NOTE: step 2's editable install pulls the runtime dependency tree, which on a
# GPU box includes PyTorch — that download is large. This is expected dev setup.

set -euo pipefail

# ── Locate the repo root (this script lives in <root>/scripts) ──────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

VENV_DIR="$ROOT/.venv"

# ── 0. Fail loudly if there is no Python at all ─────────────────────────────
# Prefer python3, fall back to python. We only need it to create the venv;
# after that we use the venv's own interpreter directly.
find_python() {
    if command -v python3 >/dev/null 2>&1; then
        echo "python3"; return 0
    fi
    if command -v python >/dev/null 2>&1; then
        echo "python"; return 0
    fi
    return 1
}

if ! BOOTSTRAP_PY="$(find_python)"; then
    echo "ERROR: no Python interpreter found on PATH (looked for python3, python)." >&2
    echo "Install Python 3.11+ and re-run: bash scripts/bootstrap_dev.sh" >&2
    exit 1
fi

echo "Using '$BOOTSTRAP_PY' to bootstrap (Python: $("$BOOTSTRAP_PY" --version 2>&1))."

# ── 1. Create .venv if missing ──────────────────────────────────────────────
if [ -d "$VENV_DIR" ]; then
    echo "Found existing virtualenv at '$VENV_DIR' — reusing it."
else
    echo "Creating virtualenv at '$VENV_DIR'..."
    "$BOOTSTRAP_PY" -m venv "$VENV_DIR"
fi

# The venv's bin dir + interpreter. On Git Bash for Windows the layout is
# Scripts/ with a python.exe; everywhere else it's bin/ with python.
if [ -x "$VENV_DIR/bin/python" ]; then
    VENV_BIN="$VENV_DIR/bin"
    VENV_PY="$VENV_DIR/bin/python"
elif [ -x "$VENV_DIR/Scripts/python.exe" ]; then
    VENV_BIN="$VENV_DIR/Scripts"
    VENV_PY="$VENV_DIR/Scripts/python.exe"
else
    echo "ERROR: could not find the venv interpreter under '$VENV_DIR'." >&2
    echo "Delete the directory and re-run to recreate it." >&2
    exit 1
fi

# ── 2. Install deps into the venv ───────────────────────────────────────────
echo "Upgrading pip in the venv..."
"$VENV_PY" -m pip install --upgrade pip

if [ -f "$ROOT/requirements.txt" ]; then
    echo "Installing runtime deps from requirements.txt..."
    "$VENV_PY" -m pip install -r "$ROOT/requirements.txt"
else
    echo "No requirements.txt found — skipping runtime deps."
fi

echo "Installing the package in editable mode (pip install -e .)..."
"$VENV_PY" -m pip install -e "$ROOT"

# ── 3. Ensure the venv bin dir is on PATH (persist to ~/.bashrc if not) ──────
# Idempotent: we only append the export line if an identical one isn't already
# present in ~/.bashrc. We match on the resolved bin dir so re-runs are no-ops.
PATH_PERSISTED=0
case ":$PATH:" in
    *":$VENV_BIN:"*)
        echo "The venv bin dir is already on PATH for this shell."
        ;;
    *)
        BASHRC="$HOME/.bashrc"
        EXPORT_LINE="export PATH=\"$VENV_BIN:\$PATH\""
        if [ -f "$BASHRC" ] && grep -qF "$EXPORT_LINE" "$BASHRC"; then
            echo "PATH export for the venv bin dir already present in $BASHRC."
            PATH_PERSISTED=1
        else
            {
                echo ""
                echo "# Added by AudiobookMaker scripts/bootstrap_dev.sh — venv CLI shims on PATH"
                echo "$EXPORT_LINE"
            } >> "$BASHRC"
            echo "Appended the venv bin dir to PATH in $BASHRC."
            PATH_PERSISTED=1
        fi
        ;;
esac

# ── 4. Print shim / PATH / GUI-shadow status, then the CLI version ──────────
echo ""
echo "Running the CLI install check (shim / PATH / GUI-shadow status)..."
# Run through the venv interpreter so the diagnostics reflect the env we just
# set up. A non-zero exit here just means the shim isn't on PATH yet (expected
# before you open a fresh shell), so don't let it abort the bootstrap.
"$VENV_PY" "$ROOT/scripts/check_cli_install.py" || true

echo ""
if command -v audiobookmaker-cli >/dev/null 2>&1; then
    echo "audiobookmaker-cli resolves on PATH:"
    audiobookmaker-cli --version || true
else
    echo "audiobookmaker-cli is not on PATH in THIS shell yet."
    echo "It was installed into '$VENV_BIN'."
fi

# ── 5. Success + next steps ─────────────────────────────────────────────────
echo ""
echo "Bootstrap complete."
echo ""
echo "Next steps:"
if [ "$PATH_PERSISTED" -eq 1 ]; then
    echo "  - Open a FRESH shell (or run: source ~/.bashrc) so the updated PATH"
    echo "    takes effect, then run: audiobookmaker-cli --version"
else
    echo "  - Run: audiobookmaker-cli --version"
fi
echo "  - The Chatterbox GPU engine installs separately. See"
echo "    docs/QUICKSTART_DEV.md for the .venv-chatterbox setup."
echo "  - Day-to-day, activate the env with: source '$VENV_BIN/activate'"
