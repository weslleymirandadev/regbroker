"""RegBroker entry point."""
from __future__ import annotations

import argparse
import sys

from . import config as cfg_mod
from .repl import Repl


def main() -> None:
    p = argparse.ArgumentParser(
        prog="regbroker",
        description="Windows Registry Hive Forensics — AI-powered",
    )
    p.add_argument("hivefile",  nargs="?", help="Hive file to open on start")
    p.add_argument("--api-key", "-k", metavar="KEY",   help="OpenRouter API key")
    p.add_argument("--model",   "-m", metavar="MODEL", help="AI model ID")
    p.add_argument("--version", "-v", action="version", version="RegBroker 1.0.0")
    args = p.parse_args()

    config = cfg_mod.load()
    if args.api_key:
        config["api_key"] = args.api_key
    if args.model:
        config["model"] = args.model

    Repl(config).run(initial_hive=args.hivefile or "")


if __name__ == "__main__":
    main()
