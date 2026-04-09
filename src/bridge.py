"""Bridge to the regbroker-core C++ binary — all output is JSON."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class BridgeError(Exception):
    pass


def _find_core() -> str:
    here = Path(__file__).resolve().parent.parent
    candidates = [
        here / "bin" / "regbroker-core.exe",
        here / "bin" / "regbroker-core",
        here / "core" / "build" / "Release" / "regbroker-core.exe",
        here / "core" / "build" / "regbroker-core.exe",
        here / "core" / "build" / "regbroker-core",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    found = shutil.which("regbroker-core")
    if found:
        return found
    raise BridgeError(
        "regbroker-core not found.\n"
        "Build it first:\n"
        "  cd core && cmake -B build && cmake --build build --config Release"
    )


_CORE_BIN: str | None = None


def _core() -> str:
    global _CORE_BIN
    if _CORE_BIN is None:
        _CORE_BIN = _find_core()
    return _CORE_BIN


def _run(*args: str) -> Any:
    cmd = [_core(), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    except FileNotFoundError:
        raise BridgeError(f"Cannot execute: {cmd[0]}")
    except subprocess.TimeoutExpired:
        raise BridgeError("regbroker-core timed out")

    out = r.stdout.strip()
    if not out:
        raise BridgeError(r.stderr.strip() or "No output from core")

    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise BridgeError(f"Bad JSON: {e}\n{out[:200]}")

    if isinstance(data, dict) and "error" in data:
        raise BridgeError(data["error"])
    return data


def hive_info(hive_path: str) -> dict:
    return _run("info", hive_path)

def ls(hive_path: str, path: str = "\\") -> dict:
    return _run("ls", hive_path, path)

def cat(hive_path: str, path: str, value_name: str) -> dict:
    return _run("cat", hive_path, path, value_name)

def tree(hive_path: str, path: str = "\\", depth: int = 3) -> list:
    return _run("tree", hive_path, path, str(depth))

def find(hive_path: str, path: str, pattern: str) -> list:
    return _run("find", hive_path, path, pattern)

def search(hive_path: str, path: str, pattern: str) -> list:
    return _run("search", hive_path, path, pattern)

def recover(hive_path: str) -> dict:
    return _run("recover", hive_path)
