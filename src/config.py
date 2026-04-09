from __future__ import annotations
import json, os
from pathlib import Path

# Brazilian + UTC + common international timezones
TIMEZONES = [
    "UTC",
    "America/Sao_Paulo",
    "America/Manaus",
    "America/Fortaleza",
    "America/Belem",
    "America/Cuiaba",
    "America/Rio_Branco",
    "America/Noronha",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Lisbon",
    "Europe/Berlin",
]

TS_FORMATS = ["ISO", "BR", "US", "UNIX", "custom"]

DEFAULTS = {
    # ── Identidade do perito ─────────────────────────────────────────────────
    "perito_name":   "",
    "perito_org":    "",
    "perito_reg":    "",
    # ── Inteligência artificial ──────────────────────────────────────────────
    "api_key":       "",
    "model":         "anthropic/claude-3.5-haiku",
    "max_tokens":    4096,
    "temperature":   0.7,
    # ── Timestamps ───────────────────────────────────────────────────────────
    "ts_format":     "ISO",            # ISO | BR | US | UNIX | custom
    "ts_custom_fmt": "%d/%m/%Y %H:%M:%S",
    "timezone":      "UTC",
    # ── Exportação ───────────────────────────────────────────────────────────
    "note_file":     "data.md",
}

def config_dir() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home())) / "regbroker"
    base.mkdir(parents=True, exist_ok=True)
    return base

def config_path() -> Path:
    return config_dir() / "config.json"

def load() -> dict:
    cfg = dict(DEFAULTS)
    p   = config_path()
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text("utf-8")))
        except Exception:
            pass
    if os.environ.get("OPENROUTER_API_KEY"):
        cfg["api_key"] = os.environ["OPENROUTER_API_KEY"]
    if os.environ.get("REGBROKER_MODEL"):
        cfg["model"] = os.environ["REGBROKER_MODEL"]
    return cfg

def save(cfg: dict) -> None:
    try:
        config_path().write_text(
            json.dumps({k: v for k, v in cfg.items() if v}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
