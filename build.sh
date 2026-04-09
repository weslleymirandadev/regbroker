#!/bin/bash
# Note: On Linux/Unix systems, make this script executable with: chmod +x build.sh

echo "=== RegBroker Build ==="
echo

# ── 1. Build C++ core ────────────────────────────────────────────────────────
echo "[1/3] Building C++ core (regbroker-core)..."
cd core

if [ ! -d build ]; then
    mkdir build
fi

cmake -B build -DCMAKE_BUILD_TYPE=Release 2>/dev/null
if [ $? -ne 0 ]; then
    echo "ERROR: CMake configuration failed."
    echo "Make sure CMake and a C++20 compiler are installed."
    exit 1
fi

cmake --build build --config Release
if [ $? -ne 0 ]; then
    echo "ERROR: C++ build failed."
    exit 1
fi

cd ..

# ── 2. Copy binary to bin/ ───────────────────────────────────────────────────
echo
echo "[2/3] Installing core binary..."
if [ ! -d bin ]; then
    mkdir bin
fi

CORE_BIN="core/build/regbroker-core"
if [ ! -f "$CORE_BIN" ]; then
    CORE_BIN="core/build/Release/regbroker-core"
fi

if [ -f "$CORE_BIN" ]; then
    cp -f "$CORE_BIN" bin/regbroker-core
    echo "  Copied to bin/regbroker-core"
else
    echo "WARNING: Could not find built binary. Check core/build/"
fi

# ── 3. Install Python package ────────────────────────────────────────────────
echo
echo "[3/3] Installing Python package..."
pip install -e . --quiet
if [ $? -ne 0 ]; then
    echo "ERROR: pip install failed."
    exit 1
fi

echo
echo "=== Build complete! ==="
echo
echo "Usage:"
echo "  regbroker                          -- interactive REPL"
echo "  regbroker NTUSER.DAT               -- open hive directly"
echo "  regbroker -k YOUR_API_KEY          -- start with API key"
echo "  regbroker NTUSER.DAT -k YOUR_KEY   -- both"
echo
echo "First run setup:"
echo "  regbroker"
echo "  > config set api_key YOUR_OPENROUTER_KEY"
echo "  > config set model   anthropic/claude-3.5-haiku"
echo "  > open /path/to/NTUSER.DAT"
