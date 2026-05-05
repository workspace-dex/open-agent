#!/usr/bin/env python3
"""Open-Agent CLI entry point — wraps the main() from the package."""
import sys
from pathlib import Path

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from open_agent._impl import main as _main


def run_cli():
    _main()


if __name__ == "__main__":
    run_cli()