#!/usr/bin/env python3
"""
Open-Agent — CLI entry point.
Uses the full open_agent package (parallel tools, thinking logger, tool cache, etc.)
"""
import sys
from pathlib import Path

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent))

from open_agent._impl import main as _main, main_sync

if __name__ == "__main__":
    _main()
