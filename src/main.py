"""RegBroker entry point."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add src directory to Python path when running directly
if __name__ == "__main__":
    project_root = Path(__file__).parent.parent
    src_path = Path(__file__).parent
    
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

# Import modules
try:
    import config as cfg_mod
    import minimal_repl
    run_repl = minimal_repl.run_minimal_repl
except ImportError as e:
    print(f"Import error: {e}")
    print("Could not import required modules.")
    print("Make sure to install dependencies: pip install -r requirements.txt")
    print("Current sys.path:")
    for p in sys.path:
        print(f"  {p}")
    sys.exit(1)


def main() -> None:
    p = argparse.ArgumentParser(
        prog="regbroker",
        description="Windows Registry Hive Forensics — AI-powered",
    )
    p.add_argument("hivefile", nargs="?", help="Hive file to open on start")
    p.add_argument("--version", "-v", action="version", version="RegBroker 1.0.0")
    args = p.parse_args()

    config = cfg_mod.load()
    run_repl(config, initial_hive=args.hivefile or "")


if __name__ == "__main__":
    sys.exit(main())
