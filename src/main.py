"""RegBroker entry point."""
from __future__ import annotations

import argparse
import sys

try:
    from regbroker import config as cfg_mod
    from regbroker import repl
except ImportError:
    # Development mode - try relative imports
    from . import config as cfg_mod
    from . import repl as repl_module
    Repl = repl_module.Repl


def main() -> None:
    p = argparse.ArgumentParser(
        prog="regbroker",
        description="Windows Registry Hive Forensics — AI-powered",
    )
    p.add_argument("hivefile", nargs="?", help="Hive file to open on start")
    p.add_argument("--version", "-v", action="version", version="RegBroker 1.0.0")
    args = p.parse_args()

    config = cfg_mod.load()
    Repl(config).run(initial_hive=args.hivefile or "")


if __name__ == "__main__":
    sys.exit(main())
