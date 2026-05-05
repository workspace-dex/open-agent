#!/usr/bin/env python3
"""
Open-Agent CLI Runner — Simple entry point for token-efficient local AI.

Usage:
    python agent.py                    # Interactive mode
    python agent.py --new              # New session
    python agent.py --resume <id>      # Resume session
    python agent.py --version           # Show version
"""
import asyncio
import sys
from pathlib import Path

# Token-efficient: only import what's needed
from open_agent import CFG, load_config, AgentSession, AgentRunner, console


async def main():
    """Main CLI entry point."""
    load_config()
    
    # Parse args
    args = sys.argv[1:]
    
    if "--version" in args:
        print("Open-Agent 5.1.0")
        print("Token-efficient local AI agent")
        return
    
    # Check config first time
    if not CFG.get("setup_done"):
        console.print("[yellow]First run detected. Run: python agent.py --setup[/yellow]")
        return
    
    # Determine mode
    session_id = None
    if "--resume" in args:
        idx = args.index("--resume")
        if idx + 1 < len(args):
            session_id = args[idx + 1]
    
    # Create runner
    runner = AgentRunner()
    
    if "--new" in args:
        runner.session = None  # Force new
    
    # Run interactive
    await runner.interact(session_id)


if __name__ == "__main__":
    asyncio.run(main())