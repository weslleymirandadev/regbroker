#!/bin/bash
# Note: On Linux/Unix systems, make this script executable with: chmod +x build.sh
set -e

echo "=== RegBroker Build ==="
echo

# ── 1. Create virtual environment ────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "[1/5] Creating virtual environment..."
    python3 -m venv .venv
else
    echo "[1/5] Virtual environment already exists."
fi

# ── 2. Activate virtual environment ───────────────────────────────────────
echo
echo "[2/5] Activating virtual environment..."
source .venv/bin/activate

# ── 3. Install Python package ────────────────────────────────────────────────
echo
echo "[3/5] Installing Python package..."
pip install -e .
if [ $? -ne 0 ]; then
    echo "ERROR: pip install failed."
    exit 1
fi

# ── 4. Install PyInstaller ───────────────────────────────────────────────────
echo
echo "[4/5] Installing PyInstaller..."
pip install pyinstaller
if [ $? -ne 0 ]; then
    echo "ERROR: PyInstaller install failed."
    exit 1
fi

# ── 5. Build executable ─────────────────────────────────────────────────────
echo
echo "[5/5] Building executable..."
if [ ! -d build ]; then
    mkdir build
fi

# Get version from pyproject.toml
VERSION=$(grep '^version = ' pyproject.toml | cut -d'"' -f2)
OS_NAME="linux"
EXECUTABLE_NAME="regbroker-${VERSION}-${OS_NAME}"

# Export variables for spec file
export REGBROKER_VERSION="$VERSION"
export REGBROKER_OS="$OS_NAME"

# Build with version and OS in name
pyinstaller --distpath build regbroker.spec
if [ $? -ne 0 ]; then
    echo "ERROR: PyInstaller build failed."
    exit 1
fi

# Clean up PyInstaller artifacts
rm -rf dist __pycache__

echo
echo "=== Build complete! ==="
echo
echo "Executable created: build/${EXECUTABLE_NAME}"
echo
echo "Usage:"
echo "  ./${EXECUTABLE_NAME}                          -- interactive REPL with AI-powered analysis"
echo "  ./${EXECUTABLE_NAME} NTUSER.DAT              -- open hive file directly"
echo "  ./${EXECUTABLE_NAME} --version                 -- show version"
echo
echo "First run setup:"
echo "  ./${EXECUTABLE_NAME}"
echo "  > config set api_key YOUR_OPENROUTER_KEY"
echo "  > config set model   anthropic/claude-3.5-haiku"
echo "  > open /path/to/NTUSER.DAT"
echo
echo "AI-powered registry forensics tool."
echo
echo "Virtual environment created at: .venv/"
echo "To activate manually: source .venv/bin/activate"
