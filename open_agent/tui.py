#!/usr/bin/env python3
"""
Open-Agent Terminal TUI - A modern terminal UI with Rich-based rendering.
Provides real-time streaming with proper markdown formatting like Claude Code.
"""

import asyncio
import io
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from pathlib import Path

import httpx
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text
from rich.tree import Tree


# Create console that outputs to stdout
console = Console(file=sys.stdout, force_terminal=True, markup=True)


@dataclass
class MessageItem:
    """A message in the conversation."""
    role: str  # "user" or "assistant"
    content: str
    tool_calls: list = field(default_factory=list)


@dataclass
class ToolCall:
    """A tool call."""
    name: str
    detail: str = ""
    result: str = ""
    status: str = "pending"  # pending, running, done, error
    duration: float = 0
    chars: int = 0


class TerminalUI:
    """Terminal UI with Rich-based rendering."""
    
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url
        self.model = model
        self.messages = []
        self.console = Console(file=sys.stdout, force_terminal=True)
        
    def header(self):
        """Print the header."""
        self.console.print()
        self.console.rule("[bold cyan]Open-Agent TUI[/bold cyan]", style="cyan")
        self.console.print(f"[dim]Model:[/dim] {self.model}")
        self.console.print(f"[dim]Base:[/dim] {self.base_url}")
        self.console.rule(style="dim")
        self.console.print()
    
    def user_input(self, query: str):
        """Show user input."""
        self.console.print(f"\n[bold cyan]❯[/bold cyan] {query}")
        self.console.print("[dim]" + "─" * 50 + "[/dim]")
    
    def thinking(self):
        """Show thinking indicator."""
        self.console.print("[dim]Thinking...[/dim]", end=" ")
        sys.stdout.flush()
    
    def thinking_done(self):
        """Finish thinking."""
        self.console.print("[green]✓[/green]")
    
    def tool_start(self, name: str, detail: str = ""):
        """Show tool started."""
        detail_short = detail[:50] + "..." if len(detail) > 50 else detail
        self.console.print(f"  [amber]⟳[/amber] [purple]{name}[/purple] [dim]{detail_short}[/dim]")
        sys.stdout.flush()
    
    def tool_done(self, name: str, summary: str = "", chars: int = 0, duration: float = 0):
        """Show tool completed."""
        self.console.print(f"  [green]✓[/green] [purple]{name}[/purple] [dim]{summary[:50]} ({chars}c, {duration:.1f}s)[/dim]")
        sys.stdout.flush()
    
    def tool_error(self, name: str, error: str):
        """Show tool error."""
        self.console.print(f"  [red]✗[/red] [purple]{name}[/purple] [red]{error}[/red]")
        sys.stdout.flush()
    
    def response(self, content: str):
        """Show the response with markdown rendering."""
        # Render markdown
        md = Markdown(content)
        self.console.print()
        for line in md.generate():
            self.console.print(line, end="")
    
    def separator(self):
        """Print separator."""
        self.console.print("[dim]" + "─" * 50 + "[/dim]")
    
    def error(self, msg: str):
        """Show error."""
        self.console.print(f"[red]Error:[/red] {msg}")
    
    def clear(self):
        """Clear screen."""
        self.console.clear()
        self.header()


class AgentRunner:
    """Runs the agent and handles streaming."""
    
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url
        self.model = model
        self.ui = TerminalUI(base_url, model)
        self.messages = []
        
    def run_query(self, query: str):
        """Run a query with the agent."""
        # Show user input
        self.ui.user_input(query)
        self.ui.thinking()
        
        # Add to history
        self.messages.append({"role": "user", "content": query})
        
        # Run agent
        try:
            from open_agent.agent_loop import AgentLoop, TOOL_SCHEMAS
            from open_agent._impl import SYSTEM_PROMPT
            
            # Text buffer for accumulation
            text_buffer = []
            
            # Callbacks
            def on_text(chunk: str):
                text_buffer.append(chunk)
                # Write to stdout for immediate feedback
                sys.stdout.write(chunk)
                sys.stdout.flush()
            
            def on_tool_start(name: str, detail: str = ""):
                self.ui.tool_start(name, detail)
            
            def on_tool_done(name: str, summary: str = "", chars: int = 0, duration: float = 0):
                self.ui.tool_done(name, summary, chars, duration)
            
            # Create agent loop
            loop = AgentLoop(
                base_url=self.base_url,
                api_key="lm-studio",
                model=self.model,
                system_prompt=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                max_tokens=2400,
                temperature=0.6,
                text_callback=on_text,
            )
            
            # Run
            result = loop.run(query, self.messages)
            
            # Add to history
            self.messages.append({"role": "assistant", "content": result})
            
            # Done thinking
            self.ui.thinking_done()
            
            # Final separator
            self.ui.separator()
            
        except Exception as e:
            import traceback
            self.ui.thinking_done()
            self.ui.error(str(e))
            print(traceback.format_exc())


def run_interactive():
    """Run interactive mode."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Open-Agent Terminal TUI")
    parser.add_argument("--url", default="http://localhost:8083/v1", help="API base URL")
    parser.add_argument("--model", default="Qwen3.5-9B-Q3_K_M.gguf", help="Model name")
    args = parser.parse_args()
    
    # Create UI and agent
    runner = AgentRunner(base_url=args.url, model=args.model)
    
    # Show header
    runner.ui.header()
    
    print("Type your queries. Press Ctrl+C to quit.\n")
    
    # Interactive loop
    while True:
        try:
            query = input("❯ ").strip()
            if not query:
                continue
            if query.lower() in ["exit", "quit", "q"]:
                break
                
            runner.run_query(query)
            
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break


def run_tui():
    """Run the TUI."""
    run_interactive()


if __name__ == "__main__":
    run_tui()
