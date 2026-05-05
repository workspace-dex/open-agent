#!/usr/bin/env python3
"""
Hermes-style thinking logger for open-agent.
Shows structured reasoning, tool calls, and results — WITHOUT modifying _impl.py.
Wraps the agent session with observable thinking chains.
"""
import asyncio
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from open_agent._impl import AgentSession, td as TDBadge

# ANSI Colors (match _impl.py)
ESC = "\x1b"
RESET = f"{ESC}[0m"
BOLD = f"{ESC}[1m"
DIM = f"{ESC}[2m"
C_CYAN = f"{ESC}[38;5;117m"
C_GREEN = f"{ESC}[38;5;114m"
C_AMBER = f"{ESC}[38;5;214m"
C_PURPLE = f"{ESC}[38;5;183m"
C_TEAL = f"{ESC}[38;5;115m"
C_RED = f"{ESC}[38;5;203m"
C_SLATE = f"{ESC}[38;5;60m"
C_BLUE = f"{ESC}[38;5;75m"
C_WHITE = f"{ESC}[38;5;252m"

# Thinking step icons
STEP_ICONS = {
    "analyze": "🔍",
    "plan": "📋",
    "tool": "⚡",
    "result": "✓",
    "error": "✗",
    "retry": "↻",
    "done": "🏁",
    "context": "📚",
    "memory": "🧠",
    "search": "🌐",
    "execute": "⚙️",
    "verify": "✅",
    "fix": "🔧",
}


class ThinkingChain:
    """Structured thinking chain — shows reasoning step by step."""

    def __init__(self, verbose: bool = True, log_file: Optional[Path] = None):
        self.verbose = verbose
        self.log_file = log_file or Path.home() / ".config" / "open-agent" / "thinking.log"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.steps: list[dict] = []
        self._indent = 0
        self._timing_enabled = True

    def _log(self, msg: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] [{level}] {msg}"
        print(f"  {msg}")
        if self.log_file:
            try:
                with open(self.log_file, "a") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    def think(self, step: str, detail: str = "", icon: str = "⚡"):
        """Record and display a thinking step."""
        self._indent = min(self._indent, 3)
        indent = "  " * self._indent
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        step_str = f"{C_CYAN}{icon}{RESET} {C_PURPLE}{step}{RESET}"
        if detail:
            detail_clean = detail.replace("\n", " ")[:120]
            step_str += f" {DIM}{detail_clean}{RESET}"

        self._log(f"{indent}{step_str}")
        self.steps.append({
            "step": step,
            "detail": detail[:200],
            "timestamp": timestamp,
            "type": "thought",
        })

    def tool_call(self, tool: str, args: dict, icon: str = "⚡"):
        """Log a tool call with arguments."""
        args_short = {k: (str(v)[:60] + "..." if len(str(v)) > 60 else str(v))
                      for k, v in args.items()}
        args_str = ", ".join(f"{k}={v!r}" for k, v in args_short.items())

        self._log(f"{C_AMBER}{icon}{RESET} {C_GREEN}{tool}{RESET}({args_str})")
        self.steps.append({
            "step": f"TOOL_CALL:{tool}",
            "detail": args_str[:200],
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "type": "tool_call",
        })

    def tool_result(self, tool: str, success: bool, chars: int = 0,
                    summary: str = "", icon: str = "⚡"):
        """Log tool result."""
        status_icon = f"{C_GREEN}✓{RESET}" if success else f"{C_RED}✗{RESET}"
        char_str = f" {DIM}{chars:,}c{RESET}" if chars else ""

        display_summary = summary.replace("\n", " ")[:80] if summary else ""
        if display_summary:
            display_summary = f" {DIM}{display_summary}{RESET}"

        self._log(f"  {status_icon} {C_TEAL}{tool}{RESET}{display_summary}{char_str}")
        self.steps.append({
            "step": f"TOOL_RESULT:{tool}",
            "detail": summary[:200],
            "success": success,
            "chars": chars,
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "type": "tool_result",
        })

    def error(self, msg: str, icon: str = "✗"):
        """Log an error."""
        self._log(f"{C_RED}{icon}{RESET} {C_RED}{msg}{RESET}", "ERROR")
        self.steps.append({
            "step": "ERROR",
            "detail": msg[:200],
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "type": "error",
        })

    def retry(self, attempt: int, reason: str = "", icon: str = "↻"):
        """Log a retry attempt."""
        self._log(f"{C_AMBER}{icon}{RESET} {C_AMBER}Retry {attempt}{RESET}"
                  + (f" {DIM}({reason}){RESET}" if reason else ""))
        self.steps.append({
            "step": f"RETRY:{attempt}",
            "detail": reason[:200],
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "type": "retry",
        })

    def context_info(self, msg: str, icon: str = "📚"):
        """Log context/memory info."""
        self._log(f"{C_BLUE}{icon}{RESET} {DIM}{msg}{RESET}")
        self.steps.append({
            "step": "CONTEXT",
            "detail": msg[:200],
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "type": "context",
        })

    def memory_info(self, msg: str, icon: str = "🧠"):
        """Log memory operations."""
        self._log(f"{C_TEAL}{icon}{RESET} {DIM}{msg}{RESET}")
        self.steps.append({
            "step": "MEMORY",
            "detail": msg[:200],
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "type": "memory",
        })

    def sub_think(self, detail: str = ""):
        """Increase indent for sub-thoughts."""
        self._indent += 1
        if detail:
            self.think("sub", detail, icon="↳")

    def end_sub(self):
        """Decrease indent."""
        self._indent = max(0, self._indent - 1)

    def summary(self) -> dict:
        """Get summary of thinking chain."""
        tool_calls = [s for s in self.steps if s["type"] == "tool_call"]
        tool_results = [s for s in self.steps if s["type"] == "tool_result"]
        errors = [s for s in self.steps if s["type"] == "error"]
        return {
            "total_steps": len(self.steps),
            "tool_calls": len(tool_calls),
            "tool_results": len(tool_results),
            "errors": len(errors),
            "success": len(errors) == 0,
        }


class ToolBadgeLogger:
    """
    Drop-in replacement for _impl.td (ToolBadge).
    Wraps the original ToolBadge to add thinking logger output.
    Does NOT modify the original — just intercepts calls.
    """

    def __init__(self, original_badge, thinking_chain: ThinkingChain):
        self._original = original_badge
        self._chain = thinking_chain
        self._call_depth = 0

    def start(self, name: str, detail: str = ""):
        self._chain.tool_call(name, {"detail": detail[:120]})
        self._call_depth += 1
        self._original.start(name, detail)

    def done(self, name: str, summary: str = "", ok: bool = True, chars: int = 0):
        self._call_depth = max(0, self._call_depth - 1)
        # Truncate summary for display
        summary_clean = summary.replace("\n", " ")[:80] if summary else ""
        self._chain.tool_result(name, ok, chars, summary_clean)
        self._original.done(name, summary, ok, chars)

    def info(self, label: str = ""):
        self._chain.think("info", label)
        self._original.info(label)


def patch_tool_badge(chain: ThinkingChain):
    """Patch the global td (ToolBadge) with logging wrapper."""
    from open_agent import _impl
    if hasattr(_impl, 'td'):
        _impl.td = ToolBadgeLogger(_impl.td, chain)


def unpatch_tool_badge(original_td):
    """Restore original ToolBadge."""
    from open_agent import _impl
    _impl.td = original_td


class ThinkingLogger:
    """
    High-level wrapper for AgentSession that adds hermes-agent-like thinking logs.
    Does NOT modify _impl.py — wraps at runtime.
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.chain = ThinkingChain(verbose=verbose)
        self._original_td = None

    def __enter__(self):
        if self.verbose:
            self._original_td = self._get_original_td()
            if self._original_td:
                patch_tool_badge(self.chain)
        return self

    def __exit__(self, *args):
        if self._original_td:
            unpatch_tool_badge(self._original_td)

    def _get_original_td(self):
        """Get the original ToolBadge from _impl."""
        try:
            from open_agent import _impl
            return getattr(_impl, 'td', None)
        except Exception:
            return None

    async def run(self, session_run_fn, *args, **kwargs) -> Any:
        """Run a session method with thinking logs."""
        self.chain.think("analyze", "Starting agent session", icon=STEP_ICONS["analyze"])
        try:
            result = await session_run_fn(*args, **kwargs)
            self.chain.think("done", "Session complete", icon=STEP_ICONS["done"])
            return result
        except Exception as e:
            self.chain.error(f"Session failed: {e}", icon=STEP_ICONS["error"])
            raise


def detect_planning_text(text: str) -> tuple[bool, str]:
    """Detect if model output looks like planning/narration."""
    text_lower = text.lower()
    planning_markers = [
        "i will", "i'll", "let me", "i am going to", "i'm going to",
        "i will now", "first i will", "i'll now", "i will fetch",
        "let me get", "let me search", "i will search", "step 1", "step 2",
        "first,", "second,", "third,", "then i", "after that",
        "to do this, i need to", "here's what i will do",
    ]
    for marker in planning_markers:
        if marker in text_lower:
            return True, marker
    return False, ""


def extract_tool_calls_from_text(text: str) -> list[dict]:
    """Extract potential tool calls from model output text."""
    # Match patterns like tool_name(arg="value") or tool_name(arg=value)
    tool_pattern = re.compile(
        r'(web_search|cached_web_search|smart_research|fetch_page|'
        r'run_terminal|read_file|write_file|search_obsidian|'
        r'read_obsidian_note|write_obsidian_note|update_memory|'
        r'read_memory|search_sessions|create_pptx|read_rss_by_name|'
        r'load_soul)\s*\(',
        re.IGNORECASE
    )
    matches = tool_pattern.findall(text)
    return [{"tool": m.lower(), "position": match.start()}
            for m, match in zip(matches, tool_pattern.finditer(text))]


def format_error_hint(error_msg: str) -> str:
    """Format an error with actionable hint."""
    error_lower = error_msg.lower()

    hints = {
        "nameerror": ("Variable not defined", "Define the variable first: x = 'value'"),
        "syntaxerror": ("Syntax error", "Check parentheses, colons, indentation"),
        "indentationerror": ("Indentation error", "Use consistent spaces (4), not tabs"),
        "importerror": ("Import error", "Install missing package: pip install <name>"),
        "modulenotfounderror": ("Module not found", "Install: pip install <name>"),
        "filenotfounderror": ("File not found", "Check path or create parent dirs"),
        "permissionerror": ("Permission denied", "Use a different path in ~/"),
        "timeoutexpired": ("Timeout", "Optimize command or increase timeout"),
        "jsondecodeerror": ("JSON error", "Check JSON syntax"),
        "connectionerror": ("Connection error", "Check network/URL"),
        "httperror": ("HTTP error", "Check URL and status code"),
    }

    for key, (label, hint) in hints.items():
        if key in error_lower:
            return f"{label}: {error_msg[:100]}\n💡 Hint: {hint}"

    return error_msg[:200]


async def run_with_thinking_log(session, query: str, verbose: bool = True) -> str:
    """
    Run agent query with full thinking log visibility.
    This is the main entry point — wraps AgentSession.run() with logging.
    """
    chain = ThinkingChain(verbose=verbose)

    chain.think("analyze", query[:80], icon=STEP_ICONS["analyze"])

    # Get context info
    tok = session.token_est + len(query) // 4
    chain.context_info(
        f"context ~{tok:,} tokens | session {session.id}",
        icon=STEP_ICONS["context"]
    )

    # Check memory
    if session.memory_md:
        chain.memory_info(f"session memory active ({len(session.memory_md):,} chars)")
    if session.summary:
        chain.memory_info(f"summary active ({len(session.summary):,} chars)")

    # Run the session
    from open_agent import _impl

    # Patch td for tool logging
    original_td = _impl.td
    badge_logger = ToolBadgeLogger(original_td, chain)
    _impl.td = badge_logger

    try:
        result = await session.run(query)

        # Check if result looks like planning
        if result:
            is_planning, marker = detect_planning_text(result)
            if is_planning:
                chain.think(
                    "warning",
                    f"Model doing narration instead of execution: '{marker}'",
                    icon="⚠️"
                )

            # Extract and log detected tool calls
            tool_calls = extract_tool_calls_from_text(result)
            if tool_calls:
                chain.think(
                    "verify",
                    f"Detected {len(tool_calls)} tool call(s) in response",
                    icon=STEP_ICONS["verify"]
                )

        chain.think("done", "Complete", icon=STEP_ICONS["done"])
        summary = chain.summary()
        if summary["errors"] > 0:
            chain.error(f"Completed with {summary['errors']} error(s)")

        return result

    except Exception as e:
        chain.error(f"Exception: {e}", icon=STEP_ICONS["error"])
        raise

    finally:
        _impl.td = original_td
