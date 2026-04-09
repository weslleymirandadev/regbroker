@echo off
setlocal

echo === RegBroker Build ===
echo.

REM ── 1. Create virtual environment ────────────────────────────────────────
if not exist .venv (
    echo [1/5] Creating virtual environment...
    python -m venv .venv
) else (
    echo [1/5] Virtual environment already exists.
)

REM ── 2. Activate virtual environment ───────────────────────────────────────
echo.
echo [2/5] Activating virtual environment...
call .venv\Scripts\activate.bat

REM Upgrade pip in virtual environment
python -m pip install --upgrade pip --quiet

REM ── 3. Install Python package ────────────────────────────────────────────────
echo.
echo [3/5] Installing Python package...
pip install -e . --quiet
if errorlevel 1 (
    echo ERROR: pip install failed.
    exit /b 1
)

REM ── 4. Install PyInstaller ───────────────────────────────────────────────────
echo.
echo [4/5] Installing PyInstaller...
pip install pyinstaller --quiet
if errorlevel 1 (
    echo ERROR: PyInstaller install failed.
    exit /b 1
)

REM ── 5. Build executable ─────────────────────────────────────────────────────
echo.
echo [5/5] Building executable...
if not exist build mkdir build

REM Get version from pyproject.toml
for /f "tokens=2 delims=" %%i in ('findstr "version = " pyproject.toml') do set VERSION=%%i
set OS_NAME=windows
set EXECUTABLE_NAME=regbroker-%VERSION%-%OS_NAME%

REM Set environment variables for spec file
set REGBROKER_VERSION=%VERSION%
set REGBROKER_OS=%OS_NAME%

REM Build with version and OS in name
pyinstaller --distpath build regbroker.spec
if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    exit /b 1
)

REM Clean up PyInstaller artifacts
if exist dist rmdir /s /q dist
if exist build rmdir /s /q __pycache__

echo.
echo === Build complete! ===
echo.
echo Executable created: build\%EXECUTABLE_NAME%.exe
echo.
echo Usage:
echo   %EXECUTABLE_NAME%.exe                          -- interactive REPL with AI-powered analysis
echo   %EXECUTABLE_NAME%.exe NTUSER.DAT              -- open hive file directly
echo   %EXECUTABLE_NAME%.exe --version                 -- show version
echo.
echo First run setup:
echo   %EXECUTABLE_NAME%.exe
echo   ^> config set api_key YOUR_OPENROUTER_KEY
echo   ^> config set model   anthropic/claude-3.5-haiku
echo   ^> open /path/to/NTUSER.DAT
echo.
echo AI-powered registry forensics tool.
echo.
echo Virtual environment created at: .venv\
echo To activate manually: .venv\Scripts\activate.bat
endlocal
