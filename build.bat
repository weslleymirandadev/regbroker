@echo off
setlocal

echo === RegBroker Build ===
echo.

REM ── 1. Build C++ core ────────────────────────────────────────────────────────
echo [1/3] Building C++ core (regbroker-core)...
cd core

if not exist build mkdir build
cmake -B build -DCMAKE_BUILD_TYPE=Release -G "Visual Studio 17 2022" -A x64 2>nul
if errorlevel 1 (
    cmake -B build -DCMAKE_BUILD_TYPE=Release 2>nul
)
if errorlevel 1 (
    echo ERROR: CMake configuration failed.
    echo Make sure CMake and a C++20 compiler are installed.
    exit /b 1
)

cmake --build build --config Release
if errorlevel 1 (
    echo ERROR: C++ build failed.
    exit /b 1
)

cd ..

REM ── 2. Copy binary to bin/ ───────────────────────────────────────────────────
echo.
echo [2/3] Installing core binary...
if not exist bin mkdir bin

set CORE_BIN=core\build\Release\regbroker-core.exe
if not exist %CORE_BIN% set CORE_BIN=core\build\regbroker-core.exe
if not exist %CORE_BIN% set CORE_BIN=core\build\Release\regbroker-core

if exist %CORE_BIN% (
    copy /Y %CORE_BIN% bin\regbroker-core.exe >nul
    echo   Copied to bin\regbroker-core.exe
) else (
    echo WARNING: Could not find built binary. Check core\build\
)

REM ── 3. Install Python package ────────────────────────────────────────────────
echo.
echo [3/3] Installing Python package...
pip install -e . --quiet
if errorlevel 1 (
    echo ERROR: pip install failed.
    exit /b 1
)

echo.
echo === Build complete! ===
echo.
echo Usage:
echo   regbroker                          -- interactive REPL
echo   regbroker NTUSER.DAT               -- open hive directly
echo   regbroker -k YOUR_API_KEY          -- start with API key
echo   regbroker NTUSER.DAT -k YOUR_KEY   -- both
echo.
echo First run setup:
echo   regbroker
echo   ^> config set api_key YOUR_OPENROUTER_KEY
echo   ^> config set model   anthropic/claude-3.5-haiku
echo   ^> open C:\path\to\NTUSER.DAT
endlocal
