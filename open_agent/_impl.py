#!/usr/bin/env python3
"""
Open-Agent — local AI agent for consumer hardware.
One file. No cloud. Yours.

UI redesign inspired by Claude Code's clean terminal aesthetic.

PATCH NOTES (2025-05):
  - Fix 1: Rendering — single-pass Rich Markdown render, no triple-render drift
  - Fix 2: History — raise max_pairs to 20, smarter token-aware compression,
            summary injected as system prefix (not fake user/assistant exchange),
            verbatim last-turn echo in augment so small models never lose thread
  - Fix 3: _stream_lines_written tracking replaced with cursor-save/restore
            so erase math is always exact regardless of wrap width
"""

# ══════════════════════════════════════════════════════════════════════════
#  IMPORTS
# ══════════════════════════════════════════════════════════════════════════

import argparse
import asyncio
import feedparser
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import termios
import threading
import time
import tty
import uuid
import sqlite3
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Set, Dict, Callable

# ── Optional parallel loop ─────────────────────────────────────────────
try:
    from open_agent.agent_loop import (
        AgentLoop as CustomAgentLoop,
        TOOL_SCHEMAS,
        interrupt_agent,
        clear_interrupt,
    )
    _HAS_AGENT_LOOP = True
except ImportError:
    _HAS_AGENT_LOOP = False
    TOOL_SCHEMAS = []
    interrupt_agent = clear_interrupt = lambda: None


# ── Ctrl+C interrupt ────────────────────────────────────────────────────
_pending_interrupt = False

def _sigint_handler(signum, frame):
    global _pending_interrupt
    _pending_interrupt = True
    interrupt_agent()

if sys.platform != "win32":
    _old_sigint = signal.signal(signal.SIGINT, _sigint_handler)

# ══════════════════════════════════════════════════════════════════════════
#  TOOLSET SYSTEM
# ══════════════════════════════════════════════════════════════════════════

TOOLSETS = {
    "web": {
        "description": "Web search and content extraction",
        "tools": ["web_search", "cached_web_search", "smart_research", "fetch_page"],
        "keywords": ["search", "find", "google", "latest", "news", "web", "url", "download", "fetch", "scrape"],
    },
    "file": {
        "description": "File read, write, and search operations",
        "tools": ["read_file", "write_file"],
        "keywords": ["file", "read", "write", "save", "create", "edit", "open", "view"],
    },
    "terminal": {
        "description": "Shell command execution",
        "tools": ["run_terminal", "run_python"],
        "keywords": ["run", "execute", "command", "shell", "bash", "python", "install", "git", "npm"],
    },
    "memory": {
        "description": "Persistent memory and session search",
        "tools": ["update_memory", "read_memory", "search_sessions", "update_user_profile"],
        "keywords": ["remember", "memory", "history", "session", "past", "preference", "profile"],
    },
    "obsidian": {
        "description": "Obsidian vault notes",
        "tools": ["search_obsidian", "read_obsidian_note", "write_obsidian_note"],
        "keywords": ["obsidian", "note", "vault", "markdown"],
    },
    "office": {
        "description": "Office document creation",
        "tools": ["create_pptx"],
        "keywords": ["ppt", "presentation", "slides", "deck", "powerpoint"],
    },
    "soul": {
        "description": "Load extended instructions",
        "tools": ["load_soul"],
        "keywords": ["soul", "instructions", "extended", "behavior"],
    },
}


def _detect_toolset(user_input: str) -> Set[str]:
    user_lower = user_input.lower()
    detected = set()
    for toolset_name, config in TOOLSETS.items():
        for keyword in config.get("keywords", []):
            if keyword in user_lower:
                detected.add(toolset_name)
                break
    if not detected:
        detected = {"web", "terminal"}
    return detected


def get_toolset_tools(toolset_names: Set[str]) -> List[str]:
    tools = []
    for name in toolset_names:
        if name in TOOLSETS:
            tools.extend(TOOLSETS[name]["tools"])
    return tools


import httpx
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich.align import Align
from rich.live import Live
from rich.rule import Rule
from rich.columns import Columns
from rich.spinner import Spinner
from rich.padding import Padding
from rich.style import Style


# ══════════════════════════════════════════════════════════════════════════
#  DESIGN SYSTEM
# ══════════════════════════════════════════════════════════════════════════

ESC        = "\x1b"
RESET      = f"{ESC}[0m"
BOLD       = f"{ESC}[1m"
DIM        = f"{ESC}[2m"
ITALIC     = f"{ESC}[3m"
C_CYAN     = f"{ESC}[38;5;117m"
C_GREEN    = f"{ESC}[38;5;114m"
C_AMBER    = f"{ESC}[38;5;214m"
C_PURPLE   = f"{ESC}[38;5;183m"
C_TEAL     = f"{ESC}[38;5;115m"
C_RED      = f"{ESC}[38;5;203m"
C_SLATE    = f"{ESC}[38;5;60m"
C_WHITE    = f"{ESC}[38;5;252m"
C_BLUE     = f"{ESC}[38;5;75m"
C_GOLD     = f"{ESC}[38;5;221m"
CLEAR_LINE = f"\r{ESC}[2K"

SYM_AGENT    = ""
SYM_TOOL_RUN = "⠋"
SYM_TOOL_OK  = "✓"
SYM_TOOL_ERR = "✗"
SYM_THINK    = "…"
SYM_USER     = "❯"
SYM_INFO     = "·"
SYM_ARROW    = "⎿"

_ANSI_RE   = re.compile(r"\x1b\[[0-9;]*[mK]")
_wrap_col  = 0

def _w(s: str) -> None:
    """Write to stdout — no soft-wrap; let the terminal handle it."""
    global _wrap_col
    sys.stdout.write(s)
    sys.stdout.flush()
    # Track newlines for callers that still check _wrap_col
    for ch in s:
        if ch == "\n":
            _wrap_col = 0
        else:
            _wrap_col += 1

def _ln(s: str = "") -> None:
    global _wrap_col
    _wrap_col = 0
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


def _normalize_output(text: str) -> str:
    """Strip <think> tags, normalize whitespace."""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate_result(content: str, max_chars: Optional[int] = None) -> str:
    max_chars = max_chars or CFG.get("max_tool_result_chars", 6_000)
    if len(content) <= max_chars:
        return content
    truncated = content[:max_chars]
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars // 2:
        truncated = truncated[:last_nl + 1]
    return truncated + f"\n\n[Truncated: {len(content):,} → {max_chars:,} chars]"


# ══════════════════════════════════════════════════════════════════════════
#  RICH CONSOLE
# ══════════════════════════════════════════════════════════════════════════

THEME = Theme({
    "agent":       "bold #7DCFFF",
    "user":        "bold #9ECE6A",
    "tool.name":   "#BB9AF7",
    "tool.ok":     "#9ECE6A",
    "tool.err":    "#F7768E",
    "tool.info":   "#73DACA",
    "tool.run":    "#FF9E64",
    "dim":         "#565F89",
    "header":      "bold #7AA2F7",
    "accent":      "#FF9E64",
    "border":      "#3B4261",
    "response":    "#C0CAF5",
    "meta":        "#565F89",
    "sep":         "#1A1B26",
    "warn":        "#E0AF68",
})
console = Console(theme=THEME, highlight=False, soft_wrap=False, width=None)
err_console = Console(stderr=True, theme=THEME, highlight=False)


def _terminal_width() -> int:
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


# ══════════════════════════════════════════════════════════════════════════
#  CONFIG & PATHS
# ══════════════════════════════════════════════════════════════════════════

CONFIG_DIR   = Path.home() / ".config" / "open-agent"
SESSIONS_DIR = CONFIG_DIR / "sessions"
CONFIG_FILE  = CONFIG_DIR / "config.json"
SOUL_FILE    = Path(__file__).parent.parent / "SOUL.md"
MEMORY_FILE  = CONFIG_DIR / "MEMORY.md"
USER_FILE    = CONFIG_DIR / "USER.md"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG: Dict = {
    "base_url":              "http://localhost:8083/v1",
    "model_name":            "llama.cpp",
    "searxng_url":           "http://localhost:8081/search",
    "obsidian_path":         "",
    # ── PATCH: raise default history depth significantly ──────────────
    "max_pairs":            10,         # was 6 — keep 20 full turns verbatim
    "ctx_limit":             18_000,
    "ctx_warn":              16_000,     # warn earlier
    "ctx_compress":          30_000,     # compress later (was 28k)
    # ─────────────────────────────────────────────────────────────────
    "setup_done":            False,
    "memory_nudge_interval": 10,
    "max_summary_tokens":    600,       # was 800 — richer summaries
    "max_tool_result_chars": 6_000,
    "use_parallel_loop":     True,
    "agent_temperature":     0.6,
}

def load_config() -> Dict:
    if CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg: Dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))

CFG = load_config()

SOUL_TRIGGERS = {
    "research", "plan", "how should i", "step by step", "steps to",
    "compare", "analyze", "analyse", "strategy", "deep dive",
    "explain in detail", "walk me through", "pros and cons",
    "investigate", "outline", "draft", "write a", "help me build",
}
GROUNDING_TRIGGERS = {
    "today", "now", "current", "latest", "recent", "who is", "what is",
    "price", "weather", "news", "2024", "2025", "this year",
    "this week", "yesterday", "released", "announced",
}


# ══════════════════════════════════════════════════════════════════════════
#  PATH SAFETY
# ══════════════════════════════════════════════════════════════════════════

def _safe_expand_path(path: str) -> Path:
    p = Path(path).expanduser().resolve()
    home = Path.home().resolve()
    cfg_dir = CONFIG_DIR.resolve()
    if str(p).startswith(str(home)) or str(p).startswith(str(cfg_dir)):
        return p
    raise PermissionError(
        f"⛔ Path '{path}' is outside your home directory.\n"
        "File tools are restricted to ~/... only."
    )


BLOCKED_PATTERNS = [
    r"\brm\s+-rf\s+/",
    r"\brm\s+--no-preserve-root",
    r"\bmkfs\b",
    r"\bdd\b.*of=/dev/(sd|nvme|hd|vd)",
    r">\s*/dev/sd",
    r":\(\)\s*\{.*\}\s*;",
    r"\bsudo\s+su\b",
    r"\bsudo\s+bash\b",
    r"\bsudo\s+sh\b",
    r"\bchmod\s+777\s+/",
    r"\bchown\b.*\s+/etc/",
    r"(curl|wget)\s+.*\|\s*(ba)?sh",
    r"\bnc\b.*-e\s+/bin/(ba)?sh",
    r"\bpython\b.*-c.*socket.*connect",
    r"exec\s+\d+<>/dev/tcp",
    r"cat\s+/etc/(passwd|shadow|sudoers)",
    r"/etc/ssh/.*id_rsa",
    r"\brm\s+-r[rf]?\s+~",
    r"\brm\s+-r[rf]?\s+\$HOME",
    r"\brm\s+-r[rf]?\s+/home",
    r"\bshred\b",
    r"\bwipefs\b",
    r"\b:(){:|:&};:",
    r"\bbash\s+-i\s+>&\s+/dev/tcp",
    r"\bnc\s+-e\s+/bin/(ba)?sh",
]
_BLOCKED_RE = [re.compile(p, re.IGNORECASE) for p in BLOCKED_PATTERNS]

def _is_dangerous(cmd: str) -> Tuple[bool, str]:
    for pattern in _BLOCKED_RE:
        if pattern.search(cmd):
            return True, pattern.pattern
    return False, ""


ERROR_FIXES = {
    "NameError":          "Variable not defined. Define it first: x = 'value'",
    "SyntaxError":        "Check syntax — missing parentheses/colons.",
    "IndentationError":   "Fix indentation — use 4 spaces, not tabs.",
    "ImportError":        "Module not installed: pip install <module>",
    "ModuleNotFoundError":"Install missing package: pip install <packagename>",
    "FileNotFoundError":  "Check file path — case-sensitive on Linux.",
    "PermissionError":    "File not writable — check permissions.",
    "TimeoutExpired":     "Command took too long — optimize or break it up.",
    "JSONDecodeError":    "Invalid JSON — check syntax.",
    "ConnectionError":    "Network issue — check URL or retry later.",
    "HTTP":               "HTTP error — check URL or status code.",
}

def _analyze_error(error_msg: str) -> str:
    for err, fix in ERROR_FIXES.items():
        if err.lower() in error_msg.lower():
            return f"Hint: {fix}"
    return ""


# ══════════════════════════════════════════════════════════════════════════
#  AGENT SETUP
# ══════════════════════════════════════════════════════════════════════════

provider = OpenAIProvider(base_url=CFG["base_url"], api_key="lm-studio")
model    = OpenAIChatModel(model_name=CFG["model_name"], provider=provider)

SYSTEM_PROMPT = """You are Open-Agent, an interactive CLI agent running on the user's local machine with full tool access. You run on 9B–26B parameter models — be concise, tool-first, zero fluff.

# STRICT TOOL-FIRST PROTOCOL

## 1. NO NARRATION EVER
FORBIDDEN outputs (say these = failure):
- "I will now..." / "Let me..." / "I'm going to..."
- "Step 1:" / "First, I'll..." / "Running X..."
- "Certainly!" / "Great!" / "Of course!"
If you catch yourself writing any of the above — DELETE it and call the tool instead.

## 2. TOOL-ACTION MAPPING
- Every intended action = an immediate tool call. No exceptions.
- Independent actions → multiple tool calls in one response.
- Dependent actions → one tool, then next tool after result.
- Failed tool → read error → corrective tool call. No apology.

## 3. SESSION INIT (every new session)
IMMEDIATELY call both in parallel:
- read_memory() → load persistent facts
- update_user_profile() if user introduces themselves

---

# FILE OPERATIONS — CONTEXT BUDGET IS CRITICAL
You have a small context window. Violating this order wastes it:

1. `outline_file(path)` — ALWAYS first on any code file. Gets symbols + line numbers only.
2. `grep_file(path, pattern)` — pinpoint the exact lines you need.
3. `read_file_section(path, start, end)` — read only those lines.
4. `patch_file(path, old_str, new_str)` — surgical edits. Never rewrite whole files.
5. `read_file(path)` — ONLY for files <100 lines or plain text/config.
6. `read_file(path!!)` — force full read. Only when user explicitly asks.

NEVER call `read_file` on a code file without `outline_file` first.
NEVER use `write_file` to edit — use `patch_file` for any existing file.
`patch_file` old_str MUST be unique — include 2–3 lines of surrounding context.

## File Search Protocol (READ-ONLY exploration)
When exploring an unknown codebase, run these in parallel:
- `run_terminal("find . -name '*.py' | head -40")` — structure
- `run_terminal("ls -la")` — top-level layout
- `outline_file` on any file that looks relevant
Then grep → section-read. Never bulk-read. Never create temp files during exploration.

---

## SELF-IMPROVEMENT PROTOCOL
When you make a mistake, get retried, or a tool fails:
1. call log_failure(task, what_failed, attempted_fix, outcome)
2. If same failure appears 3+ times → call analyze_failures() and propose a fix

When asked to improve yourself:
1. introspect("all") → understand current state  
2. analyze_failures(20) → find patterns
3. outline_file + grep_file on agent.py → read relevant section
4. propose_patch(problem, location) → draft the fix
5. write findings to ~/open-agent-improvements.md
6. NEVER patch yourself without user confirmation first

You can read your own source code. You cannot auto-apply patches.

---

# PERSISTENT MEMORY SYSTEM

## Session Start — Orient (run automatically)
1. `read_memory()` — load MEMORY.md
2. `run_terminal("ls ~/.config/open-agent/")` — check what exists

## During Session — Capture Signal
Save to memory when you learn:
- User's name, preferences, recurring workflows
- Project paths, stack details, environment quirks
- Errors solved (error → fix pattern)
- Decisions made ("user prefers X over Y")

DO NOT save:
- One-off facts with no future value
- Raw tool output / logs
- Anything the user can look up in 5 seconds

## Memory Write Format
[Topic] — [YYYY-MM-DD]

Fact one (concrete, no relative dates like "yesterday")
Fact two
Source: [session title or tool that revealed this]

## Session End — Consolidate
Before the user exits, if anything noteworthy happened:
- `update_memory(content)` — merge new facts into existing topics, don't duplicate
- Convert relative dates to absolute
- Delete contradicted facts from old entries

---

# TOOL REFERENCE

## Search & Web
- `web_search(query)` → current info, news, docs. Use for anything time-sensitive.
- `cached_web_search(query)` → same + 1hr cache. Use for repeated queries in a session.
- `smart_research(queries: list)` → up to 4 parallel searches. Use for research tasks.
- `fetch_page(url)` → full text from a URL. Use after web_search finds a promising link.

## File Operations — USE IN THIS ORDER
- `outline_file(path)` → AST summary: classes, functions, line numbers. ALWAYS first on any code file.
- `grep_file(path, pattern, context_lines=3)` → regex search with surrounding context. Use second to pinpoint lines.
- `read_file_section(path, start, end)` → exact line range only. Use third after grep reveals line numbers.
- `view_file(path, start?, end?)` → line-numbered read. Use immediately before any patch_file call.
- `patch_file(path, old_str, new_str)` → surgical in-place replace. ALWAYS prefer over write_file for existing files. old_str must be unique — include 2–3 lines of surrounding context.
- `read_file(path)` → full file up to 8000 chars. ONLY for files <100 lines or plain text/config.
- `read_file(path!!)` → force full read. Only when user explicitly requests it.
- `write_file(path, content)` → full overwrite. Only for NEW files that do not exist yet.

## Shell & Code
- `run_terminal(command)` → any shell task: files, git, installs, processes, system info. Timeout: 30s.
- `run_python(code)` → calculations, data processing, scripting without shell overhead.

## Memory & Knowledge
- `read_memory()` → read MEMORY.md. Call at every session start.
- `update_memory(content)` → persist facts to MEMORY.md. Append by default. Use memory write format.
- `update_user_profile(content)` → persist user info to USER.md. Name, prefs, context.
- `search_sessions(query)` → full-text search of past conversations via FTS5.
- `load_soul()` → extended behavioral instructions. Call for complex or ambiguous tasks.

## Self-Improvement & Diagnostics
- `introspect(aspect)` → aspect = "memory" | "session" | "config" | "tools" | "all". Call when something feels off or to understand current state.
- `log_failure(task, what_failed, attempted_fix, outcome)` → log a failure to failures.jsonl. outcome = "resolved" | "unresolved" | "partial". Call after any retry or tool failure.
- `analyze_failures(last_n=20)` → read failure log, return patterns and top failure types. Call when same error repeats.
- `propose_patch(problem, location)` → scaffold for proposing a code fix to agent.py. Returns outline_file → grep → section-read workflow. Never auto-applies — user confirms.

## Obsidian
- `search_obsidian(query)` → search vault for notes containing text.
- `read_obsidian_note(name)` → read specific note by name.
- `write_obsidian_note(name, content, append=True)` → create or append to note.

## RSS & Feeds
- `read_rss_by_name(name)` → fetch feed by name or category.
  Names: hn, hn_show, verge, ars, techcrunch, arxiv_ai, arxiv_cl, huggingface, schneier, krebs
  Categories: tech, ai, security, engineering, startups, news

## Office
- `create_pptx(path, slides)` → PowerPoint. slides=[{"title":"X","content":"Y"}]

---

# GROUNDING — TIME-SENSITIVE QUERIES
For: news, prices, "latest", "current", "today", "who is", "released", "announced":
1. `run_terminal("date")` — anchor current time first
2. `web_search(query + current year)` — get fresh data
3. Confirm across ≥2 results. Cite every claim with a direct URL.

---

# ERROR RECOVERY
| Error | Immediate Fix |
|---|---|
| command not found | `run_terminal("which X")` → install if missing |
| Permission denied | retry with sudo prefix |
| FileNotFoundError | `run_terminal("find ~ -name 'filename' 2>/dev/null")` → use correct path |
| Timeout (30s) | break into smaller sequential commands |
| ImportError | `run_terminal("pip install X")` → retry original code |
| Context too large | STOP reading. Switch to outline_file → grep_file → read_file_section |
| exceed_context_size_error | STOP. Never retry with same large read. Use outline workflow only. |
| patch_file fails | old_str not unique — add more surrounding context lines and retry |
| Same error 3x | call analyze_failures() → log_failure() → propose_patch() |
---

# OUTPUT FORMAT
- Markdown. Headers and bullets for structure. Code blocks with language tag always.
- Show `$ command` then output for terminal results.
- Include URLs when citing any web source.
- Concise. No filler. No preamble.
- Long output → summarize first, offer full detail on request.
- Never truncate code you are writing — write it complete or not at all."""

agent = Agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    deps_type=None,
    model_settings={
        "temperature":       0.6,
        "max_tokens":        3200,
        "top_p":             0.85,
        "frequency_penalty": 0.10,
        "presence_penalty":  0.05,
    },
    retries=2,
)
# ══════════════════════════════════════════════════════════════════════════
#  TOOL BADGE
# ══════════════════════════════════════════════════════════════════════════

_badge_lock = threading.Lock()
_FENCE_RE   = re.compile(r"```(?P<lang>\w+)?(?:\s+(?P<file>[^\n]+))?\n(?P<body>.*?)```", re.DOTALL)

class ToolBadge:
    def __init__(self) -> None:
        self._timers: Dict[str, float] = {}
        self._active: Set[str] = set()

    def start(self, name: str, detail: str = "") -> None:
        dedup_key = f"{name}:{detail[:50]}"
        with _badge_lock:
            if dedup_key in self._active:
                return
            self._active.add(dedup_key)
            self._timers[dedup_key] = time.monotonic()
            detail_str = (detail[:100] if detail else "")
            sys.stderr.write(
                f"  {C_AMBER}⠿{RESET}  {C_PURPLE}{name}{RESET}  {DIM}{detail_str}{RESET}\n"
            )
            sys.stderr.flush()

    def done(self, name: str, summary: str = "", ok: bool = True, chars: int = 0) -> None:
        dedup_key = f"{name}:"  # prefix match
        # find matching key
        found_key = next((k for k in list(self._active) if k.startswith(f"{name}:")), None)
        with _badge_lock:
            if found_key:
                elapsed = time.monotonic() - self._timers.pop(found_key, time.monotonic())
                self._active.discard(found_key)
            else:
                elapsed = 0.0
            icon = f"{C_GREEN}✓{RESET}" if ok else f"{C_RED}✗{RESET}"
            parts = [f"  {icon}  {DIM}{elapsed:.1f}s{RESET}"]
            if summary:
                parts.append(f"  {DIM}{summary[:100]}{RESET}")
            if chars:
                parts.append(f"  {DIM}{chars:,}c{RESET}")
            sys.stderr.write("  " + "".join(parts).strip() + "\n")
            sys.stderr.flush()

    def info(self, label: str) -> None:
        with _badge_lock:
            sys.stderr.write(f"  {DIM}· {label}{RESET}\n")
            sys.stderr.flush()

    def warn(self, label: str) -> None:
        with _badge_lock:
            sys.stderr.write(f"  {C_GOLD}⚠ {label}{RESET}\n")
            sys.stderr.flush()

    def blocked(self, name: str, reason: str = "") -> None:
        with _badge_lock:
            sys.stderr.write(
                f"\n  {C_RED}⊘{RESET} {C_PURPLE}{name}{RESET}  "
                f"{C_RED}BLOCKED{RESET}  {DIM}{reason[:80]}{RESET}\n\n"
            )
            sys.stderr.flush()


td = ToolBadge()

# ══════════════════════════════════════════════════════════════════════════
#  RESPONSE RENDERER  (FIX 1: single-pass, no erase-math drift)
#
#  Strategy:
#  - During streaming: write raw tokens directly to stdout (fast, no Rich)
#  - On stream_end: use ANSI cursor-save/restore (ESC[s / ESC[u) to rewind
#    to the exact position where streaming started, then re-render the full
#    text with Rich Markdown in one clean pass.
#  - No _stream_lines_written counter, no width-simulation — cursor save/
#    restore is exact regardless of terminal width or wrap behaviour.
# ══════════════════════════════════════════════════════════════════════════

class ResponseRenderer:
    def __init__(self) -> None:
        self._live: Optional[Live] = None
        self._streamed_text: str = ""
        self._was_streamed: bool = False
        self._spinner_stop: Optional[threading.Event] = None   # ← ADD THIS
        self._spinner_thread: Optional[threading.Thread] = None  # ← AND THIS

    def reset(self) -> None:
        """Safe reset — stops any running spinner before clearing state."""
        self._stop_spinner()          # ← always kill spinner first
        self._streamed_text = ""
        self._was_streamed = False
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None

    def _stop_spinner(self) -> None:
        """Idempotent spinner teardown — safe to call even if never started."""
        if self._spinner_stop is not None:
            self._spinner_stop.set()
            self._spinner_stop = None
        if self._spinner_thread is not None:
            try:
                self._spinner_thread.join(timeout=0.5)
            except Exception:
                pass
            self._spinner_thread = None

    def stream_start(self) -> None:
        self._stop_spinner()          
        _w(f"\n{C_CYAN}{BOLD}{SYM_AGENT} Open-Agent{RESET}\n\n")
        self._streamed_text = ""
        self._was_streamed = False

    def stream_end(self) -> None:
        self._stop_spinner()          # ← always clean up before rendering
        if not self._was_streamed or not self._streamed_text:
            return

        text = _normalize_output(self._streamed_text)
        if text:
            from rich.console import Console
            Console().print("\n")
            self._render_markdown(text)  # single clean render

        self._was_streamed = False

    def render_static(self, text: str) -> None:
        text = _normalize_output(text)
        if not text:
            return
        _w("\n")
        self._render_markdown(text)
        self._was_streamed = False

    def _render_markdown(self, text: str) -> None:
        width = min(_terminal_width() - 2, 160)

        if not _FENCE_RE.search(text):
            console.print(Markdown(text, justify="left"), width=width)
        else:
            last_end = 0
            for m in _FENCE_RE.finditer(text):
                prose = text[last_end:m.start()].strip()
                if prose:
                    console.print(Markdown(prose, justify="left"), width=width)

                lang  = m.group("lang") or "text"
                fname = m.group("file") or ""
                body  = m.group("body").rstrip()

                label = Text()
                label.append(f" {lang} ", style="tool.name")
                if fname:
                    label.append(f"  {fname}", style="tool.info")
                console.print(label)
                console.print(
                    Syntax(
                        body, lang,
                        theme="monokai",
                        line_numbers=len(body.splitlines()) > 8,
                        word_wrap=True,
                        background_color="default",
                    )
                )
                last_end = m.end()

            tail = text[last_end:].strip()
            if tail:
                console.print(Markdown(tail, justify="left"), width=width)

        _w("\n")

    def reset(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None
        self._streamed_text = ""
        self._was_streamed  = False


rr = ResponseRenderer()
# ══════════════════════════════════════════════════════════════════════════
#  TOOLS  (unchanged from original)
# ══════════════════════════════════════════════════════════════════════════

def _get_http_client() -> httpx.AsyncClient:
    """Returns a fresh httpx.AsyncClient for every call.

    Previously cached via threading.local(), but that caused the client to
    hold a stale reference to a closed event loop after _run_sync() in
    agent_loop.py closed its per-thread loop.  Each call now gets a fresh
    client — the overhead is negligible for the request volume here.
    """
    return httpx.AsyncClient(
        timeout=10,
        follow_redirects=True,
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )

async def _extract_text_from_html(html: str, _url: str = "") -> str:
    if not html or len(html) < 100:
        return ""
    from bs4 import BeautifulSoup
    for parser in ("lxml", "html.parser"):
        try:
            soup = BeautifulSoup(html, parser)
            break
        except Exception:
            continue
    else:
        return ""
    try:
        noise_tags = ["script", "style", "noscript", "iframe", "svg", "form",
                      "input", "button", "select", "textarea", "map", "area"]
        for tag in soup.find_all(noise_tags):
            tag.decompose()
        noise_patterns = ["cookie", "banner", "sidebar", "nav", "header", "footer",
                          "social", "share", "related", "ad-", "advert", "popup",
                          "modal", "overlay", "newsletter", "subscribe", "menu"]
        for tag in soup.find_all(True):
            cls_id = (str(tag.get("class") or []) + " " + str(tag.get("id") or "")).lower()
            if any(p in cls_id for p in noise_patterns):
                tag.decompose()
        content = (
            soup.find("article") or soup.find("main") or
            soup.find(attrs={"role": "main"}) or
            soup.find("div", class_=lambda x: x and any(c in " ".join(x) for c in ["content", "post", "article", "entry", "story"])) or
            soup.find("div", class_=lambda x: x and "body" in " ".join(x)) or
            soup.body
        )
        if content is None:
            content = soup
        text_parts = []
        for el in content.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6",
                                     "li", "blockquote", "pre", "td", "th"]):
            try:
                t = el.get_text(separator=" ", strip=True)
                if len(t) > 30:
                    text_parts.append(t)
            except Exception:
                pass
        if len(text_parts) < 3:
            for el in content.find_all(["div", "section"]):
                try:
                    t = el.get_text(separator=" ", strip=True)
                    if 80 < len(t) < 1000:
                        text_parts.append(t)
                except Exception:
                    pass
        text = "\n\n".join(text_parts)
        text = re.sub(r"\n{4,}", "\n\n\n", text)
        text = re.sub(r" {3,}", "  ", text)
        return text.strip()
    except Exception:
        return ""


async def _extract_url(url: str) -> str:
    try:
        c = _get_http_client()
        r = await c.get(url, timeout=8)
        if r.status_code != 200:
            return ""
        return await _extract_text_from_html(r.text, url)
    except Exception:
        return ""

@agent.tool
async def web_search(ctx: RunContext, query: str) -> str:
    """Search the web via SearxNG."""
    td.start("web_search", query)
    try:
        c = _get_http_client()
        r = await c.get(
            CFG["searxng_url"],
            params={"q": query, "format": "json", "categories": "general", "language": "en"},
            timeout=12,
        )
        r.raise_for_status()
        results = r.json().get("results", [])[:7]
        if not results:
            td.done("web_search", "no results", ok=False)
            return "No results."
        fetch_tasks = []
        task_urls = []
        for x in results[:5]:
            u = x.get("url", "")
            if u and len(x.get("content", "").strip()) < 50:
                fetch_tasks.append(_extract_url(u))
                task_urls.append(u)
        if fetch_tasks:
            try:
                enriched = await asyncio.wait_for(
                    asyncio.gather(*fetch_tasks, return_exceptions=True),
                    timeout=8.0,
                )
            except asyncio.TimeoutError:
                enriched = []
            for u, e in zip(task_urls, enriched):
                if isinstance(e, str) and e:
                    for x in results:
                        if x.get("url") == u and len(x.get("content", "").strip()) < 50:
                            x["content"] = e[:300]
                            break
        out = [
            f"[{i}] {x.get('title', '')}\nURL: {x.get('url', '')}\n{x.get('content', '')[:200]}"
            for i, x in enumerate(results, 1)
        ]
        body = "\n\n".join(out)
        td.done("web_search", f"{len(results)} results", chars=len(body))
        return body
    except Exception as e:
        td.done("web_search", str(e)[:60], ok=False)
        return f"Search error: {e}"


@agent.tool
async def cached_web_search(ctx: RunContext, query: str) -> str:
    """Search web with local caching (1hr TTL)."""
    try:
        from open_agent.tool_cache import get_cached_result, cache_tool_result
        cached = get_cached_result("web_search", query)
        if cached:
            return f"[CACHED]\n{cached}"
    except ImportError:
        pass
    td.start("cached_web_search", query[:50])
    try:
        c = _get_http_client()
        r = await c.get(
            CFG["searxng_url"],
            params={"q": query, "format": "json", "categories": "general"},
            timeout=12,
        )
        r.raise_for_status()
        results = r.json().get("results", [])[:7]
        if not results:
            td.done("cached_web_search", "no results", ok=False)
            return "No results."
        out = [
            f"[{i}] {x.get('title', '')}\n{x.get('url', '')}\n{x.get('content', '')[:300]}"
            for i, x in enumerate(results, 1)
        ]
        body = "\n\n".join(out)
        try:
            from open_agent.tool_cache import cache_tool_result
            cache_tool_result("web_search", query, body)
        except ImportError:
            pass
        td.done("cached_web_search", f"{len(results)} results", chars=len(body))
        return body
    except Exception as e:
        td.done("cached_web_search", str(e)[:60], ok=False)
        return f"Search error: {e}"


@agent.tool
async def smart_research(ctx: RunContext, queries: list) -> str:
    """Run up to 4 parallel web queries and merge results."""
    queries = queries[:4]
    td.start("smart_research", " | ".join(queries))
    async def _one(q: str) -> str:
        try:
            c = _get_http_client()
            r = await c.get(
                CFG["searxng_url"],
                params={"q": q, "format": "json", "categories": "general", "language": "en"},
                timeout=12,
            )
            r.raise_for_status()
            res = r.json().get("results", [])[:4]
            return "\n".join(
                [f"### {q}"] +
                [f"**{i.get('title', '')}**\n{i.get('url', '')}\n{i.get('content', '')[:350]}" for i in res]
            ) if res else f"### {q}\nNo results."
        except Exception as e:
            return f"### {q}\nError: {e}"
    parts  = await asyncio.gather(*[_one(q) for q in queries])
    merged = "\n\n---\n\n".join(parts)
    td.done("smart_research", f"{len(queries)} queries merged", chars=len(merged))
    return merged


@agent.tool
async def fetch_page(ctx: RunContext, url: str) -> str:
    """Fetch readable text from a URL."""
    td.start("fetch_page", url[:70])
    try:
        c = _get_http_client()
        r = await c.get(url, timeout=15)
        if r.status_code == 403:
            td.done("fetch_page", "403 blocked", ok=False)
            return f"[Blocked 403] {url}"
        r.raise_for_status()
        text = await _extract_text_from_html(r.text, url)
        if not text:
            text = re.sub(r'<[^>]+>', ' ', r.text)
            text = re.sub(r'\s+', ' ', text).strip()[:3000]
        if len(text) > 6000:
            cutoff = text[:6000].rfind(" ")
            text = text[:cutoff] + "\n\n[... truncated ...]"
        td.done("fetch_page", url[:50], chars=len(text))
        return text
    except httpx.HTTPStatusError as e:
        td.done("fetch_page", f"HTTP {e.response.status_code}", ok=False)
        return f"[HTTP {e.response.status_code}] {e.response.reason_phrase}"
    except Exception as e:
        td.done("fetch_page", str(e)[:60], ok=False)
        return f"Fetch error: {e}"


@agent.tool
async def run_terminal(ctx: RunContext, command: str) -> str:
    """Run a safe shell command (30s timeout)."""
    dangerous, pattern = _is_dangerous(command)
    if dangerous:
        td.blocked("run_terminal", f"pattern: {pattern[:60]}")
        return (
            f"⛔ Command blocked for safety.\n"
            f"Pattern matched: {pattern}\n"
            "If this is legitimate, run it manually in your terminal."
        )
    td.start("run_terminal", command[:70])
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        ok  = proc.returncode == 0
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        if not ok:
            error_output = err or f"Exit code {proc.returncode}"
            hint = _analyze_error(error_output)
            td.done("run_terminal", (out or err or "error")[:60], ok=False)
            result = f"STDOUT:\n{proc.stdout}\nSTDERR:\n{error_output}\nExit: {proc.returncode}"
            if hint:
                result += f"\n\n💡 {hint}"
            return result
        td.done("run_terminal", (out or err or "done")[:60], ok=True)
        return f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}\nExit: {proc.returncode}"
    except subprocess.TimeoutExpired:
        td.done("run_terminal", "timed out (30s)", ok=False)
        return "TIMEOUT: Command exceeded 30s."
    except Exception as e:
        hint = _analyze_error(str(e))
        td.done("run_terminal", str(e)[:60], ok=False)
        return f"Error: {e}" + (f"\n\n💡 {hint}" if hint else "")


@agent.tool
async def run_python(ctx: RunContext, code: str, timeout: int = 15) -> str:
    """Execute Python code in a sandboxed subprocess."""
    td.start("run_python", f"{len(code)} chars")
    try:
        timeout = min(max(timeout, 1), 60)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        result = out[:5000]
        if err:
            result += f"\n\nSTDERR:\n{err[:500]}"
        if not result:
            result = "(no output)"
        td.done("run_python", f"{len(result)} chars", chars=len(result))
        return result
    except subprocess.TimeoutExpired:
        td.done("run_python", "timeout", ok=False)
        return f"Error: timeout ({timeout}s)."
    except Exception as e:
        td.done("run_python", str(e)[:40], ok=False)
        return f"Error: {e}"


@agent.tool
async def read_file(ctx: RunContext, path: str) -> str:
    """
    Read a local file. For files >300 lines or >6KB, returns an outline
    + first 60 lines instead of the full content — use outline_file then
    read_file_section to get specific parts cheaply.
    Pass path ending with '!!' to force full read: read_file('file.py!!')
    """
    force_full = path.endswith("!!")
    clean_path = path.rstrip("!").rstrip()

    td.start("read_file", clean_path)
    try:
        p       = _safe_expand_path(clean_path)
        content = p.read_text(encoding="utf-8", errors="ignore")
        lines   = content.splitlines()

        # ── Auto-guard: large file → outline + preview ───────────────
        if not force_full and (len(lines) > 300 or len(content) > 6_000):
            # Get outline cheaply
            outline = await outline_file(ctx, clean_path)
            preview = "\n".join(lines[:40])
            result  = (
                f"⚠ Large file ({len(lines)} lines, {len(content):,} chars).\n"
                f"Showing outline + first 40 lines.\n"
                f"Use read_file_section(path, start, end) for specific sections.\n"
                f"Use read_file('{clean_path}!!') to force full read.\n\n"
                f"{outline}\n\n"
                f"── First 40 lines ──\n{preview}"
            )
            td.done("read_file", f"auto-outline ({len(lines)} lines)", chars=len(result))
            return result

        # ── Normal read ──────────────────────────────────────────────
        content = content[:8_000]
        td.done("read_file", p.name, chars=len(content))
        return content

    except PermissionError:
        td.done("read_file", "permission denied", ok=False)
        return f"Permission denied: {clean_path}"
    except FileNotFoundError:
        td.done("read_file", "not found", ok=False)
        return f"File not found: {clean_path}"
    except Exception as e:
        td.done("read_file", str(e)[:50], ok=False)
        return f"Error: {e}"


@agent.tool
async def write_file(ctx: RunContext, path: str, content: str, expand_user: bool = True) -> str:
    """Write content to a local file. Restricted to ~/."""
    td.start("write_file", f"{path} ({len(content):,} chars)")
    try:
        p = _safe_expand_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        td.done("write_file", f"→ {p.name}", chars=len(content))
        return f"✓ Written {len(content):,} chars to {p}"
    except PermissionError as e:
        td.done("write_file", "permission denied", ok=False)
        return f"Permission denied: {e}"
    except Exception as e:
        td.done("write_file", str(e)[:50], ok=False)
        hint = _analyze_error(str(e))
        return f"Write error: {e}" + (f"\n\n💡 {hint}" if hint else "")

@agent.tool
async def grep_file(ctx: RunContext, path: str, pattern: str,
                    context_lines: int = 3) -> str:
    """
    Search for pattern in file, return only matching lines + N lines context.
    Use instead of read_file when you need to find/edit a specific function.
    pattern is a Python regex.
    """
    td.start("grep_file", f"{pattern!r} in {path}")
    try:
        p   = _safe_expand_path(path)
        src = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        rx  = re.compile(pattern, re.IGNORECASE)

        hits: List[str] = []
        matched_lines: Set[int] = set()

        for i, line in enumerate(src):
            if rx.search(line):
                lo = max(0, i - context_lines)
                hi = min(len(src), i + context_lines + 1)
                matched_lines.update(range(lo, hi))

        if not matched_lines:
            td.done("grep_file", "no matches", ok=False)
            return f"No matches for {pattern!r} in {p.name}"

        prev = -2
        for i in sorted(matched_lines):
            if i > prev + 1:
                hits.append("  ···")
            marker = "▶ " if rx.search(src[i]) else "  "
            hits.append(f"{marker}{i+1:4d} │ {src[i]}")
            prev = i

        result = f"// {p.name} — matches for {pattern!r}\n" + "\n".join(hits)
        td.done("grep_file", f"{len(matched_lines)} lines shown", chars=len(result))
        return result

    except re.error as e:
        td.done("grep_file", f"bad regex: {e}", ok=False)
        return f"Invalid regex: {e}"
    except Exception as e:
        td.done("grep_file", str(e)[:50], ok=False)
        return f"Error: {e}"

@agent.tool
async def outline_file(ctx: RunContext, path: str) -> str:
    """
    Returns a structural outline of a Python/JS/TS file — class names,
    function names, line numbers, first-line docstrings.
    Use this BEFORE read_file on any code file. Costs ~10x fewer tokens.
    """
    td.start("outline_file", path)
    try:
        p = _safe_expand_path(path)
        src = p.read_text(encoding="utf-8", errors="ignore")
        ext = p.suffix.lower()

        # ── Python: use AST for precise outline ──────────────────────
        if ext == ".py":
            import ast as _ast
            try:
                tree = _ast.parse(src)
            except SyntaxError as e:
                td.done("outline_file", "syntax error", ok=False)
                return f"SyntaxError — cannot outline: {e}"

            lines: List[str] = [f"File: {p.name}  ({len(src):,} chars, {src.count(chr(10))+1} lines)\n"]
            for node in _ast.walk(tree):
                if isinstance(node, (_ast.ClassDef, _ast.FunctionDef, _ast.AsyncFunctionDef)):
                    indent = "  " if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) else ""
                    kind   = "class" if isinstance(node, _ast.ClassDef) else (
                             "async def" if isinstance(node, _ast.AsyncFunctionDef) else "def"
                    )
                    doc = ""
                    if (node.body and isinstance(node.body[0], _ast.Expr)
                            and isinstance(node.body[0].value, _ast.Constant)):
                        doc = f"  # {str(node.body[0].value.value)[:60].splitlines()[0]}"
                    lines.append(f"  {indent}{kind} {node.name}  (L{node.lineno}){doc}")

            result = "\n".join(lines)
            td.done("outline_file", f"{len(lines)-1} symbols", chars=len(result))
            return result

        # ── Generic: first 80 chars of each line, skip blanks/comments ──
        out_lines = [f"File: {p.name}  ({len(src):,} chars)\n"]
        for i, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", "//", "*", "/*")):
                out_lines.append(f"  L{i:4d}  {line[:80]}")
            if len(out_lines) > 120:
                out_lines.append("  ... (truncated — use read_file_section for more)")
                break

        result = "\n".join(out_lines)
        td.done("outline_file", f"{p.name}", chars=len(result))
        return result

    except PermissionError:
        td.done("outline_file", "permission denied", ok=False)
        return f"Permission denied: {path}"
    except FileNotFoundError:
        td.done("outline_file", "not found", ok=False)
        return f"File not found: {path}"
    except Exception as e:
        td.done("outline_file", str(e)[:50], ok=False)
        return f"Error: {e}"

@agent.tool
async def patch_file(ctx: RunContext, path: str, old_str: str, new_str: str) -> str:
    """Replace an exact unique string in a file with new content.
    Use for surgical edits — fixing bugs, updating functions, changing config blocks.
    old_str must match the file content EXACTLY including whitespace and indentation.
    old_str must appear exactly ONCE in the file — be specific enough to be unique.
    """
    td.start("patch_file", path)
    try:
        p = _safe_expand_path(path)
        if not p.exists():
            td.done("patch_file", "not found", ok=False)
            return f"File not found: {path}"

        content = p.read_text(encoding="utf-8")

        # Count occurrences — must be exactly 1
        count = content.count(old_str)
        if count == 0:
            td.done("patch_file", "string not found", ok=False)
            # Show context to help agent fix the mismatch
            return (
                f"old_str not found in {path}.\n"
                f"The string must match exactly including whitespace.\n"
                f"First 200 chars of file:\n{content[:200]}"
            )
        if count > 1:
            td.done("patch_file", f"ambiguous: {count} matches", ok=False)
            return (
                f"old_str appears {count} times in {path} — must be unique.\n"
                f"Add more surrounding context to old_str to make it unique."
            )

        new_content = content.replace(old_str, new_str, 1)
        p.write_text(new_content, encoding="utf-8")

        # Show a diff summary
        old_lines = len(old_str.splitlines())
        new_lines = len(new_str.splitlines())
        td.done("patch_file", f"{old_lines} lines → {new_lines} lines", ok=True)
        return (
            f"✓ Patched {p.name}\n"
            f"  Replaced {old_lines} lines with {new_lines} lines\n"
            f"  File size: {len(new_content):,} chars"
        )

    except PermissionError as e:
        td.done("patch_file", "permission denied", ok=False)
        return f"Permission denied: {e}"
    except Exception as e:
        td.done("patch_file", str(e)[:50], ok=False)
        return f"Patch error: {e}"

@agent.tool
async def view_file(ctx: RunContext, path: str, start_line: int = 1, end_line: int = None) -> str:
    """Read a file with line numbers. Use before patch_file to see exact content.
    Optionally specify start_line and end_line to view a specific range.
    """
    td.start("view_file", path)
    try:
        p = _safe_expand_path(path)
        if not p.exists():
            td.done("view_file", "not found", ok=False)
            return f"File not found: {path}"

        lines = p.read_text(encoding="utf-8").splitlines()
        total = len(lines)

        # Apply range
        s = max(1, start_line) - 1
        e = min(total, end_line) if end_line else total
        slice_ = lines[s:e]

        # Format with line numbers like Claude Code
        numbered = "\n".join(
            f"{s + i + 1:4d}\t{line}"
            for i, line in enumerate(slice_)
        )

        td.done("view_file", f"lines {s+1}-{e} of {total}", chars=len(numbered))
        return f"{path} ({total} lines total, showing {s+1}-{e}):\n\n{numbered}"

    except Exception as e:
        td.done("view_file", str(e)[:50], ok=False)
        return f"View error: {e}"

@agent.tool
async def load_soul(ctx: RunContext) -> str:
    """Load extended behavioral instructions from SOUL.md."""
    td.start("load_soul", "SOUL.md")
    try:
        content = SOUL_FILE.read_text(encoding="utf-8")
        td.done("load_soul", "loaded", chars=len(content))
        return content
    except FileNotFoundError:
        td.done("load_soul", "not found", ok=False)
        return "SOUL.md not found."
    except Exception as e:
        td.done("load_soul", str(e)[:50], ok=False)
        return f"Error: {e}"


def _vault() -> Optional[Path]:
    p = CFG.get("obsidian_path", "")
    return Path(p) if p else None


@agent.tool
async def search_obsidian(ctx: RunContext, query: str) -> str:
    """Search your Obsidian vault."""
    vault = _vault()
    if not vault or not vault.exists():
        return "Obsidian vault not configured. Run /setup."
    td.start("search_obsidian", query)
    try:
        results = []
        for p in vault.rglob("*.md"):
            if any(part.startswith(".") for part in p.parts):
                continue
            if query.lower() in p.read_text(encoding="utf-8", errors="ignore").lower():
                results.append(f"- {p.relative_to(vault)}")
        if not results:
            td.done("search_obsidian", "no matches", ok=False)
            return "No matching notes."
        td.done("search_obsidian", f"{len(results)} matches")
        return "Matching notes:\n" + "\n".join(results[:25])
    except Exception as e:
        td.done("search_obsidian", str(e)[:50], ok=False)
        return f"Error: {e}"

@agent.tool
async def wiki_query(ctx: RunContext, query: str) -> str:
    """
    Search the personal LLM Wiki knowledge base for a topic or question.
    Use this when the user says 'use llm-wiki', 'check my wiki', 'look in my knowledge base',
    or when answering from personal notes would be better than a web search.
    Requires the llm_wiki desktop app to be running locally.
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "http://localhost:19827/query",
                json={"query": query}
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("answer") or data.get("content") or str(data)
    except httpx.ConnectError:
        return "llm_wiki is not running. Please open the llm_wiki desktop app first."
    except Exception as e:
        return f"wiki_query error: {e}"

@agent.tool
async def wiki_ingest(ctx: RunContext, content: str, title: str = "") -> str:
    """Ingest new content into the personal LLM Wiki knowledge base. Use when the user says
    'add this to my wiki', 'save to my knowledge base', or 'ingest this into llm-wiki'."""
    import httpx
    payload = {"content": content, "title": title, "source": "open-agent"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post("http://localhost:19827/clip", json=payload)
        return "Ingested successfully." if resp.status_code == 200 else f"Error: {resp.status_code}"

@agent.tool
async def read_obsidian_note(ctx: RunContext, note_name: str) -> str:
    """Read a specific note from your Obsidian vault."""
    vault = _vault()
    if not vault or not vault.exists():
        return "Obsidian vault not configured. Run /setup."
    if not note_name.endswith(".md"):
        note_name += ".md"
    td.start("read_obsidian_note", note_name)
    try:
        matches = [m for m in vault.rglob(note_name) if not any(p.startswith(".") for p in m.parts)]
        if not matches:
            td.done("read_obsidian_note", "not found", ok=False)
            return f"Note '{note_name}' not found."
        content = matches[0].read_text(encoding="utf-8")[:10_000]
        td.done("read_obsidian_note", note_name, chars=len(content))
        return content
    except Exception as e:
        td.done("read_obsidian_note", str(e)[:50], ok=False)
        return f"Error: {e}"


@agent.tool
async def self_debug(ctx: RunContext, issue: str, last_output: str) -> str:
    """
    Call this when you notice you're stuck, looping, or produced no output.
    Returns diagnostic info + corrective instruction.
    """
    td.start("self_debug", issue[:60])
    
    diagnostics = []
    
    # Check token pressure
    tok_est = len(last_output) // 3
    if tok_est > 1800:
        diagnostics.append(f"⚠ Token pressure: ~{tok_est} tokens used in last output. Switch to bullet-point mode.")
    
    # Detect planning-only output (no tool calls attempted)
    planning_phrases = ["i will", "i'll", "let me", "i am going to", "first i will", "next i will"]
    if any(p in last_output.lower() for p in planning_phrases) and not has_execution:
        diagnostics.append("✗ Planning loop: intent detected, no execution found.")
        diagnostics.append("→ FIX: Call the first required tool immediately. No preamble.")
    
    # Check for empty/truncated output
    if len(last_output.strip()) < 20:
        diagnostics.append("✗ Output appears truncated or empty.")
        diagnostics.append("→ FIX: Reduce scope. Answer one thing at a time.")
    
    report = "\n".join(diagnostics) if diagnostics else "No issues detected in output structure."
    
    correction = (
        "\n\nCORRECTION PROTOCOL:\n"
        "1. Do NOT repeat what you were going to do.\n"
        "2. Execute the FIRST action immediately.\n"
        "3. Use tools directly — no narration before the call.\n"
        "4. If you ran out of tokens: answer the core question in ≤3 sentences first."
    )
    
    result = f"[self_debug]\n{report}{correction}"
    td.done("self_debug", f"{len(diagnostics)} issues found", ok=len(diagnostics)==0)
    return result

@agent.tool
async def write_obsidian_note(ctx: RunContext, note_name: str, content: str, append: bool = True) -> str:
    """Create or append to an Obsidian note."""
    vault = _vault()
    if not vault or not vault.exists():
        return "Obsidian vault not configured. Run /setup."
    if not note_name.endswith(".md"):
        note_name += ".md"
    td.start("write_obsidian_note", f"{note_name}")
    try:
        matches = [m for m in vault.rglob(note_name) if not any(p.startswith(".") for p in m.parts)]
        target = matches[0] if matches else vault / note_name
        mode   = "a" if append and target.exists() else "w"
        with open(target, mode, encoding="utf-8") as f:
            if mode == "a":
                f.write("\n\n")
            f.write(content)
        action = "Appended to" if mode == "a" else "Created"
        td.done("write_obsidian_note", f"{action} {note_name}")
        return f"✓ {action} {target.relative_to(vault)}"
    except Exception as e:
        td.done("write_obsidian_note", str(e)[:50], ok=False)
        return f"Error: {e}"


@agent.tool
async def update_memory(ctx: RunContext, content: str, append: bool = True) -> str:
    """Update persistent MEMORY.md."""
    td.start("update_memory", f"{len(content):,} chars")
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        if append and MEMORY_FILE.exists():
            existing = MEMORY_FILE.read_text(encoding="utf-8")
            MEMORY_FILE.write_text(existing + "\n\n" + content, encoding="utf-8")
        else:
            MEMORY_FILE.write_text(content, encoding="utf-8")
        td.done("update_memory", f"{'appended' if append else 'written'}")
        return f"✓ MEMORY.md {'appended' if append else 'written'}"
    except Exception as e:
        td.done("update_memory", str(e)[:50], ok=False)
        return f"Error: {e}"


@agent.tool
async def read_memory(ctx: RunContext) -> str:
    """Read persistent MEMORY.md."""
    td.start("read_memory", "MEMORY.md")
    try:
        if not MEMORY_FILE.exists():
            td.done("read_memory", "not found", ok=False)
            return "MEMORY.md not found. Use update_memory to create it."
        content = MEMORY_FILE.read_text(encoding="utf-8", errors="ignore")[:1500]
        td.done("read_memory", f"{len(content):,} chars")
        return content
    except Exception as e:
        td.done("read_memory", str(e)[:50], ok=False)
        return f"Error: {e}"


@agent.tool
async def introspect(ctx: RunContext, aspect: str = "all") -> str:
    """
    Self-diagnostic tool. aspect = "memory" | "session" | "config" | "tools" | "all"
    Call this when something feels off or to understand current state.
    """
    td.start("introspect", aspect)
    report: Dict[str, any] = {}

    if aspect in ("config", "all"):
        report["config"] = {
            "model":        CFG["model_name"],
            "base_url":     CFG["base_url"],
            "max_pairs":    CFG["max_pairs"],
            "ctx_limit":    CFG["ctx_limit"],
            "parallel":     CFG.get("use_parallel_loop"),
            "temperature":  0.6,
        }

    if aspect in ("session", "all"):
        # Import here to avoid circular — session is available via ctx
        report["session"] = {
            "pairs_in_memory": "available via session object",
            "memory_file_exists": MEMORY_FILE.exists(),
            "memory_size_chars": len(MEMORY_FILE.read_text()) if MEMORY_FILE.exists() else 0,
            "user_file_exists":  USER_FILE.exists(),
        }

    if aspect in ("tools", "all"):
        report["registered_tools"] = [
            t.name for t in agent._function_tools.values()
        ] if hasattr(agent, '_function_tools') else ["inspect agent object for tools"]

    if aspect in ("memory", "all"):
        if MEMORY_FILE.exists():
            mem = MEMORY_FILE.read_text()[:500]
            report["memory_preview"] = mem
        else:
            report["memory_preview"] = "MEMORY.md does not exist yet"

    result = json.dumps(report, indent=2)
    td.done("introspect", aspect, chars=len(result))
    return result


@agent.tool
async def log_failure(ctx: RunContext, task: str, what_failed: str,
                       attempted_fix: str, outcome: str) -> str:
    """
    Log a failure + fix attempt to ~/.config/open-agent/failures.jsonl
    Call this whenever a tool fails, a retry was needed, or a plan didn't work.
    This builds the self-improvement dataset over time.
    """
    td.start("log_failure", what_failed[:50])
    entry = {
        "ts":            datetime.now().isoformat(),
        "task":          task[:200],
        "what_failed":   what_failed[:300],
        "attempted_fix": attempted_fix[:300],
        "outcome":       outcome,   # "resolved" | "unresolved" | "partial"
        "model":         CFG["model_name"],
    }
    log_path = CONFIG_DIR / "failures.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    td.done("log_failure", outcome)
    return f"✓ Logged failure → {log_path}"


@agent.tool
async def analyze_failures(ctx: RunContext, last_n: int = 20) -> str:
    """
    Read the last N failure logs and return patterns.
    Use this to understand what's repeatedly going wrong.
    """
    td.start("analyze_failures", f"last {last_n}")
    log_path = CONFIG_DIR / "failures.jsonl"
    if not log_path.exists():
        td.done("analyze_failures", "no logs yet", ok=False)
        return "No failure logs yet. Failures get logged via log_failure tool."

    lines = log_path.read_text().strip().splitlines()[-last_n:]
    entries = [json.loads(l) for l in lines if l.strip()]

    # Pattern detection
    from collections import Counter
    outcomes   = Counter(e["outcome"] for e in entries)
    fail_types = Counter(e["what_failed"][:40] for e in entries)

    result = (
        f"Last {len(entries)} failures:\n"
        f"Outcomes: {dict(outcomes)}\n"
        f"Top failure types:\n"
        + "\n".join(f"  {count}x  {ftype}" for ftype, count in fail_types.most_common(5))
        + "\n\nRaw entries (newest first):\n"
        + "\n---\n".join(
            f"[{e['ts'][:16]}] {e['what_failed']}\nFix: {e['attempted_fix']}\nOutcome: {e['outcome']}"
            for e in reversed(entries[-5:])
        )
    )
    td.done("analyze_failures", f"{len(entries)} entries", chars=len(result))
    return result


@agent.tool  
async def propose_patch(ctx: RunContext, problem: str, location: str) -> str:
    """
    Given a described problem and file location, read the relevant section
    and return a concrete patch suggestion in old_str/new_str format.
    Use when you've identified something in your own code to improve.
    """
    td.start("propose_patch", problem[:60])
    result = (
        f"Problem: {problem}\n"
        f"Location: {location}\n\n"
        f"To generate a patch:\n"
        f"1. outline_file('{location}') → find the function\n"
        f"2. grep_file('{location}', '<relevant pattern>') → get exact lines\n"
        f"3. read_file_section('{location}', start, end) → read it\n"
        f"4. Return patch as:\n"
        f"   OLD: <exact current code>\n"
        f"   NEW: <improved code>\n"
        f"   REASON: <one line why>\n"
    )
    td.done("propose_patch", "scaffold ready")
    return result

@agent.tool
async def update_user_profile(ctx: RunContext, content: str, append: bool = False) -> str:
    """Update USER.md with user info."""
    td.start("update_user_profile", f"{len(content):,} chars")
    try:
        USER_FILE.parent.mkdir(parents=True, exist_ok=True)
        if append and USER_FILE.exists():
            existing = USER_FILE.read_text(encoding="utf-8")
            USER_FILE.write_text(existing + "\n\n" + content, encoding="utf-8")
        else:
            USER_FILE.write_text(content, encoding="utf-8")
        td.done("update_user_profile", "updated")
        return "✓ USER.md updated"
    except Exception as e:
        td.done("update_user_profile", str(e)[:50], ok=False)
        return f"Error: {e}"


@agent.tool
async def search_sessions(ctx: RunContext, query: str, limit: int = 10) -> str:
    """Search all past sessions via FTS5."""
    td.start("search_sessions", query[:30])
    try:
        results = _db.search(query, limit=limit)
        if not results:
            td.done("search_sessions", "no matches", ok=False)
            return f"No matches for: {query}"
        out = [f"**Search: {query}** ({len(results)} matches)\n"]
        for r in results[:limit]:
            sid    = r.get("session_id", "?")[:12]
            title  = r.get("title", "")[:40]
            user   = r.get("user", r.get("user_msg", ""))[:80].replace("\n", " ")
            assist = r.get("assist", r.get("assist_msg", ""))[:120].replace("\n", " ")
            out += ["---", f"Session: {sid} · {title}", f"User: {user}", f"Assistant: {assist}"]
        result = "\n".join(out)
        td.done("search_sessions", f"{len(results)} matches", chars=len(result))
        return result
    except Exception as e:
        td.done("search_sessions", str(e)[:50], ok=False)
        return f"Error: {e}"


@agent.tool
async def create_pptx(ctx: RunContext, path: str, slides: list) -> str:
    """Create PowerPoint presentation."""
    td.start("create_pptx", path)
    try:
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()
        prs.slide_width  = Inches(10)
        prs.slide_height = Inches(6)
        for slide_data in slides:
            sp    = prs.slides.add_slide(prs.slide_layouts[1])
            title = sp.shapes.title
            title.text = slide_data.get("title", "")
            tf = sp.placeholders[1].text_frame
            tf.clear()
            if "content" in slide_data:
                tf.text = slide_data["content"]
            elif "bullets" in slide_data:
                for i, bullet in enumerate(slide_data["bullets"]):
                    if i == 0:
                        tf.paragraphs[0].text = bullet
                    else:
                        p = tf.add_paragraph()
                        p.text  = bullet
                        p.level = 0
        prs.save(path)
        td.done("create_pptx", f"{len(slides)} slides")
        return f"✓ Created {path} with {len(slides)} slides"
    except ImportError:
        td.done("create_pptx", "python-pptx not installed", ok=False)
        return "Error: pip install python-pptx"
    except Exception as e:
        td.done("create_pptx", str(e)[:50], ok=False)
        return f"Error: {e}"


# ── RSS ───────────────────────────────────────────────────────────────────

RSS_SOURCES: Dict[str, str] = {
    "eff":        "https://www.eff.org/rss/updates.xml",
    "schneier":   "https://www.schneier.com/feed/atom/",
    "krebs":      "https://krebsonsecurity.com/feed/",
    "bleeping":   "https://www.bleepingcomputer.com/feed/",
    "techcrunch": "https://techcrunch.com/feed/",
    "verge":      "http://www.theverge.com/rss/full.xml",
    "engadget":   "http://www.engadget.com/rss-full.xml",
    "venturebeat":"http://venturebeat.com/feed/",
    "ars":        "https://arstechnica.com/feed/",
    "hn":         "https://news.ycombinator.com/rss",
    "hn_show":    "http://hnrss.org/show",
    "hn_launches":"https://hnrss.org/launches",
    "pragmatic":  "https://blog.pragmaticengineer.com/rss/",
    "cloudflare": "https://blog.cloudflare.com/rss/",
    "stripe":     "https://stripe.com/blog/feed.rss",
    "meta_eng":   "https://engineering.fb.com/feed/",
    "julia_evans":"https://jvns.ca/atom.xml",
    "danluu":     "https://danluu.com/atom.xml",
    "arxiv_ai":   "https://rss.arxiv.org/rss/cs.AI",
    "arxiv_cl":   "https://rss.arxiv.org/rss/cs.CL",
    "huggingface":"https://huggingface.co/blog/feed.xml",
    "farnam":     "https://fs.blog/feed/",
    "producthunt":"http://www.producthunt.com/feed",
}
BROAD_CATS: Dict[str, List[str]] = {
    "tech":        ["techcrunch", "verge", "ars", "venturebeat", "hn"],
    "technology":  ["techcrunch", "verge", "ars", "venturebeat", "hn"],
    "news":        ["techcrunch", "verge", "ars", "hn"],
    "startups":    ["techcrunch", "hn_launches", "producthunt"],
    "engineering": ["pragmatic", "cloudflare", "stripe", "meta_eng"],
    "ai":          ["arxiv_ai", "arxiv_cl", "huggingface"],
    "security":    ["eff", "schneier", "krebs", "bleeping"],
}

async def _fetch_rss(url: str, limit: int = 8) -> str:
    feed = feedparser.parse(url)
    if feed.bozo:
        return f"RSS error: {feed.bozo_exception}"
    if not feed.entries:
        return f"No entries: {url}"
    out = [f"**{feed.feed.get('title', 'Feed')}**\n"]
    for i, e in enumerate(feed.entries[:min(limit, 15)], 1):
        out.append(
            f"{i}. **{e.get('title', 'No title')}**\n"
            f"   {e.get('published', e.get('updated', ''))}\n"
            f"   {e.get('link', '')}\n"
            f"   {e.get('summary', e.get('description', ''))[:260].strip()}\n"
        )
    return "\n".join(out)

@agent.tool
async def read_rss_by_name(ctx: RunContext, name: str, limit: int = 8) -> str:
    """Fetch RSS from curated sources."""
    td.start("read_rss_by_name", name)
    n = name.lower().strip()
    if n in BROAD_CATS:
        results = []
        for fn in BROAD_CATS[n]:
            if fn in RSS_SOURCES:
                try:
                    results.append(f"### {fn.upper()}\n{await _fetch_rss(RSS_SOURCES[fn], 4)}")
                except Exception:
                    pass
        merged = "\n\n---\n\n".join(results)
        td.done("read_rss_by_name", f"{len(results)} feeds", chars=len(merged))
        return merged or f"No content for '{name}'."
    url = RSS_SOURCES.get(n) or next(
        (v for k, v in RSS_SOURCES.items() if n in k or k in n), None
    )
    if url:
        c = await _fetch_rss(url, limit)
        td.done("read_rss_by_name", name, chars=len(c))
        return c
    td.done("read_rss_by_name", f"unknown: '{n}'", ok=False)
    return f"Unknown: '{n}'.\nCategories: {', '.join(BROAD_CATS)}\nSources: {', '.join(sorted(RSS_SOURCES))[:200]}…"


# ══════════════════════════════════════════════════════════════════════════
#  SQLITE SESSION DATABASE  (unchanged)
# ══════════════════════════════════════════════════════════════════════════

class SessionDB:
    _instance: Optional["SessionDB"] = None
    _lock = threading.Lock()

    def __init__(self, db_path: Path) -> None:
        self.path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._init_db()

    @classmethod
    def get(cls, db_path: Optional[Path] = None) -> "SessionDB":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db_path or (CONFIG_DIR / "sessions.db"))
        return cls._instance

    def _init_db(self) -> None:
        db = self._get_conn()
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA cache_size=-32000")
        db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                created_at TEXT NOT NULL,
                last_active TEXT NOT NULL,
                summary TEXT DEFAULT '',
                memory_md TEXT DEFAULT '',
                user_md TEXT DEFAULT '',
                turn_count INTEGER DEFAULT 0
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS pairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_msg TEXT NOT NULL,
                assist_msg TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS pairs_fts USING fts5(
                session_id, user_msg, assist_msg,
                content='pairs', content_rowid='id'
            )
        """)
        db.execute("""
            CREATE TRIGGER IF NOT EXISTS pairs_ai AFTER INSERT ON pairs BEGIN
                INSERT INTO pairs_fts(rowid, session_id, user_msg, assist_msg)
                VALUES (new.id, new.session_id, new.user_msg, new.assist_msg);
            END
        """)
        db.execute("""
            CREATE TRIGGER IF NOT EXISTS pairs_ad AFTER DELETE ON pairs BEGIN
                INSERT INTO pairs_fts(pairs_fts, rowid, session_id, user_msg, assist_msg)
                VALUES ('delete', old.id, old.session_id, old.user_msg, old.assist_msg);
            END
        """)
        db.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
        return self.conn

    def create_session(self, sid: str) -> Dict:
        now = datetime.now().isoformat()
        db  = self._get_conn()
        db.execute(
            "INSERT OR IGNORE INTO sessions (id, title, created_at, last_active) VALUES (?, ?, ?, ?)",
            (sid, "", now, now),
        )
        db.commit()
        return {"id": sid, "title": "", "created_at": now, "last_active": now}

    def get_session(self, sid: str) -> Optional[Dict]:
        db  = self._get_conn()
        row = db.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        return dict(row) if row else None

    def list_sessions(self, limit: int = 50) -> List[Dict]:
        db   = self._get_conn()
        rows = db.execute(
            "SELECT id, title, created_at, last_active, turn_count, summary "
            "FROM sessions ORDER BY last_active DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["turns"] = d.get("turn_count", 0)
            result.append(d)
        return result

    def add_pair(self, sid: str, user_msg: str, assist_msg: str) -> None:
        now = datetime.now().isoformat()
        db  = self._get_conn()
        db.execute(
            "INSERT INTO pairs (session_id, user_msg, assist_msg, created_at) VALUES (?, ?, ?, ?)",
            (sid, user_msg, assist_msg, now),
        )
        db.execute(
            "UPDATE sessions SET last_active = ?, turn_count = turn_count + 1 WHERE id = ?",
            (now, sid),
        )
        db.commit()

    def get_pairs(self, sid: str) -> List[Tuple[str, str]]:
        db   = self._get_conn()
        rows = db.execute(
            "SELECT user_msg, assist_msg FROM pairs WHERE session_id = ? ORDER BY id",
            (sid,),
        ).fetchall()
        return [(r["user_msg"], r["assist_msg"]) for r in rows]

    def get_pair_count(self, sid: str) -> int:
        db  = self._get_conn()
        row = db.execute(
            "SELECT COUNT(*) as cnt FROM pairs WHERE session_id = ?", (sid,)
        ).fetchone()
        return row["cnt"] if row else 0

    def update_summary(self, sid: str, summary: str) -> None:
        db = self._get_conn()
        db.execute("UPDATE sessions SET summary = ? WHERE id = ?", (summary, sid))
        db.commit()

    def update_memory(self, sid: str, memory_md: str = None, user_md: str = None) -> None:
        db = self._get_conn()
        if memory_md is not None:
            db.execute("UPDATE sessions SET memory_md = ? WHERE id = ?", (memory_md, sid))
        if user_md is not None:
            db.execute("UPDATE sessions SET user_md = ? WHERE id = ?", (user_md, sid))
        db.commit()

    def get_memory(self, sid: str) -> Tuple[str, str]:
        db  = self._get_conn()
        row = db.execute(
            "SELECT memory_md, user_md FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
        if row:
            return row["memory_md"] or "", row["user_md"] or ""
        return "", ""

    def search(self, query: str, limit: int = 20) -> List[Dict]:
        db = self._get_conn()
        try:
            rows = db.execute("""
                SELECT p.session_id, s.title, p.user_msg, p.assist_msg,
                       snippet(pairs_fts, 1, '[', ']', '...', 32) as user_snippet,
                       snippet(pairs_fts, 2, '[', ']', '...', 32) as assist_snippet
                FROM pairs_fts p
                JOIN sessions s ON p.session_id = s.id
                WHERE pairs_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, limit)).fetchall()
            if rows:
                return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            pass
        like_q = f"%{query}%"
        rows = db.execute("""
            SELECT p.session_id, s.title, p.user_msg, p.assist_msg
            FROM pairs p JOIN sessions s ON p.session_id = s.id
            WHERE p.user_msg LIKE ? OR p.assist_msg LIKE ?
            ORDER BY p.id DESC LIMIT ?
        """, (like_q, like_q, limit)).fetchall()
        return [
            {"session_id": r["session_id"], "title": r["title"],
             "user": r["user_msg"][:200], "assist": r["assist_msg"][:200]}
            for r in rows
        ]

    def update_title(self, sid: str, title: str) -> None:
        db = self._get_conn()
        db.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, sid))
        db.commit()

    def delete_session(self, sid: str) -> None:
        db = self._get_conn()
        db.execute("DELETE FROM pairs WHERE session_id = ?", (sid,))
        db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        db.commit()

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None


_db = SessionDB.get()
_msg_db = None  # hermes_state is optional; graceful fallback


# ══════════════════════════════════════════════════════════════════════════
#  SESSION  (FIX 2: token-aware compression, rich summary, correct history)
# ══════════════════════════════════════════════════════════════════════════

def _count_tokens(text: str) -> int:
    """Fast character-based token estimate (÷3 is closer for Gemma/Qwen)."""
    return max(1, len(text) // 3)


class Session:
    def __init__(self, sid: Optional[str] = None) -> None:
        self.id          = sid or uuid.uuid4().hex[:12]
        self.path        = SESSIONS_DIR / f"{self.id}.json"
        self.pairs:      List[Tuple[str, str]] = []
        self.summary:    str = ""
        self.title:      str = ""
        self.memory_md:  str = ""
        self.user_md:    str = ""
        self.created_at  = datetime.now().isoformat()
        self.last_active = self.created_at

        row = _db.get_session(self.id)
        if row:
            self.title       = row.get("title", "")
            self.summary     = row.get("summary", "")
            self.memory_md   = row.get("memory_md", "")
            self.user_md     = row.get("user_md", "")
            self.created_at  = row.get("created_at", self.created_at)
            self.last_active = row.get("last_active", self.last_active)
            self.pairs       = _db.get_pairs(self.id)
        elif sid and self.path.exists():
            self._load_json()

    def _load_json(self) -> None:
        try:
            d = json.loads(self.path.read_text())
            self.pairs       = [(p["u"], p["a"]) for p in d.get("pairs", []) if "u" in p]
            self.summary     = d.get("summary", "")
            self.title       = d.get("title", "")
            self.created_at  = d.get("created_at", self.created_at)
            self.last_active = d.get("last_active", self.last_active)
        except Exception:
            pass

    def save(self) -> None:
        self.last_active = datetime.now().isoformat()
        if not self.title and self.pairs:
            self.title = self.pairs[0][0][:60].replace("\n", " ").strip()
        if _db.get_session(self.id):
            _db.update_summary(self.id, self.summary)
            _db.update_memory(self.id, self.memory_md, self.user_md)
            if self.title:
                _db.update_title(self.id, self.title)
        else:
            _db.create_session(self.id)
            _db.update_summary(self.id, self.summary)
            _db.update_memory(self.id, self.memory_md, self.user_md)
        try:
            self.path.write_text(json.dumps({
                "id":          self.id,
                "title":       self.title,
                "created_at":  self.created_at,
                "last_active": self.last_active,
                "summary":     self.summary,
                "pairs":       [{"u": u, "a": a} for u, a in self.pairs],
            }, indent=2, ensure_ascii=False))
        except Exception:
            pass

    def delete(self) -> None:
        _db.delete_session(self.id)
        self.path.unlink(missing_ok=True)

    # ── FIX 2a: token-aware compression ──────────────────────────────────
    def compress(self) -> str:
        max_pairs = CFG["max_pairs"]
        if len(self.pairs) <= max_pairs:
            return "nothing to compress"
    
        old_pairs  = self.pairs[:-max_pairs]
        self.pairs = self.pairs[-max_pairs:]
    
        lines = []
        for i, (u, a) in enumerate(old_pairs):
            a_clean = re.sub(r'<think>.*?</think>', '', a, flags=re.DOTALL).strip()
            u_short = u[:300].replace("\n", " ").strip()
            a_short = a_clean[:600].replace("\n", " ").strip()
            lines.append(f"Turn {i+1}:\n  User: {u_short}\n  Assistant: {a_short}")
    
        new_block = "\n\n".join(lines)
        max_chars = CFG.get("max_summary_tokens", 1200) * 4
    
        if self.summary:
            combined = self.summary + "\n\n---\n\n" + new_block
        else:
            combined = new_block
    
        if len(combined) > max_chars:
            combined = combined[-max_chars:]
            nl = combined.find("\n")
            if nl > 0:
                combined = combined[nl + 1:]
    
        self.summary = combined
        _db.update_summary(self.id, self.summary)
        self.save()
        return f"compressed {len(old_pairs)} turns → {len(self.summary):,} char summary"

    # ── FIX 2b: to_messages — verbatim pairs + summary as system prefix ──
    def to_messages(self) -> List[ModelMessage]:
        """
        Build message history for the model.

        Layout:
          1. [system-style prefix] persistent MEMORY.md content
          2. [system-style prefix] USER.md profile
          3. [system-style prefix] rolling conversation summary
             (injected as a single assistant message so it reads as prior
             context, NOT as a fake Q&A that wastes tokens on fake questions)
          4. Verbatim last N pairs (user + assistant alternating)

        This ensures the model always gets:
          - Full recent context (no truncation of recent turns)
          - Compressed older context (summary, not nothing)
          - Zero "fake" user messages that confuse instruction-tuned models
        """
        from pydantic_ai.messages import (
            ModelRequest, ModelResponse, UserPromptPart, TextPart, SystemPromptPart
        )
        msgs: List[ModelMessage] = []

        # ── Persistent memory (global across sessions) ─────────────────
        if MEMORY_FILE.exists():
            try:
                mem = MEMORY_FILE.read_text(encoding="utf-8", errors="ignore").strip()
                if mem:
                    # Inject as a system message prefix understood by the model
                    msgs.append(ModelRequest(parts=[UserPromptPart(
                        content=f"[PERSISTENT MEMORY — facts you should remember across all sessions]\n{mem[:3000]}"
                    )]))
                    msgs.append(ModelResponse(parts=[TextPart(
                        content="Understood. I have loaded your persistent memory."
                    )]))
            except Exception:
                pass

        # ── User profile ───────────────────────────────────────────────
        if USER_FILE.exists():
            try:
                usr = USER_FILE.read_text(encoding="utf-8", errors="ignore").strip()
                if usr:
                    msgs.append(ModelRequest(parts=[UserPromptPart(
                        content=f"[USER PROFILE]\n{usr[:1500]}"
                    )]))
                    msgs.append(ModelResponse(parts=[TextPart(
                        content="Understood. I have noted your profile."
                    )]))
            except Exception:
                pass

        # ── Compressed older context (summary) ────────────────────────
        #  FIX: inject as assistant monologue, not fake Q&A.
        #  The model reads this as "what I remember from earlier" which is
        #  exactly what it is — no wasted prompt budget on fake questions.
        if self.summary.strip():
            msgs.append(ModelRequest(parts=[UserPromptPart(
                content="[Earlier in our conversation, before your current memory window:]"
            )]))
            msgs.append(ModelResponse(parts=[TextPart(
                content=f"I recall the following from earlier:\n\n{self.summary}"
            )]))

        # ── Verbatim recent pairs (the critical part) ──────────────────
        #  FIX: use ALL pairs up to max_pairs, not just the last few.
        #  The compress() method already trimmed self.pairs to max_pairs,
        #  so len(self.pairs) is always ≤ max_pairs here.
        for u, a in self.pairs:
            a_clean = re.sub(r'<think>.*?</think>', '', a, flags=re.DOTALL).strip()
            msgs.append(ModelRequest(parts=[UserPromptPart(content=u)]))
            msgs.append(ModelResponse(parts=[TextPart(content=a_clean)]))

        return msgs

    def add(self, user: str, assistant: str, tool_calls: List[Dict] = None) -> None:
        self.pairs.append((user, assistant))
        _db.add_pair(self.id, user, assistant)
        # Compress only when we actually exceed the limit (token-aware)
        if len(self.pairs) > CFG["max_pairs"] + 2:
            self.compress()
        self.save()

    @property
    def token_est(self) -> int:
        """Estimate tokens using ÷3 (closer for Gemma/Qwen verbose output)."""
        total  = _count_tokens(self.summary)
        total += _count_tokens(self.memory_md)
        total += _count_tokens(self.user_md)
        total += sum(_count_tokens(u) + _count_tokens(a) for u, a in self.pairs)
        try:
            if MEMORY_FILE.exists():
                total += min(_count_tokens(MEMORY_FILE.read_text(errors="ignore")), 1000)
            if USER_FILE.exists():
                total += min(_count_tokens(USER_FILE.read_text(errors="ignore")), 500)
        except Exception:
            pass
        return total

    @staticmethod
    def list_all() -> List[Dict]:
        return _db.list_sessions()


# ══════════════════════════════════════════════════════════════════════════
#  MULTILINE INPUT  (unchanged)
# ══════════════════════════════════════════════════════════════════════════

PASTE_START = b"\x1b[200~"
PASTE_END   = b"\x1b[201~"
BP_ON       = "\x1b[?2004h"
BP_OFF      = "\x1b[?2004l"


class InputBuffer:
    def _visible_len(self, s: str) -> int:
        return len(_ANSI_RE.sub("", s))

    def _redraw(self, prompt: str) -> None:
        if self._line > 0:
            sys.stdout.write(f"\x1b[{self._line}A")
        sys.stdout.write("\r\x1b[J")
        sys.stdout.flush()
        sys.stdout.write(prompt + "".join(self._buf))
        sys.stdout.flush()

    def readline(self, prompt_prefix: str = "") -> Optional[str]:
        if not sys.stdin.isatty():
            try:
                return input().strip() or None
            except EOFError:
                return None

        fd    = sys.stdin.fileno()
        old   = termios.tcgetattr(fd)
        prompt = f"{C_GREEN}{BOLD}{SYM_USER}{RESET} "
        self._buf: List[str] = []
        self._col  = 2
        self._line = 0
        self._width = _terminal_width()
        in_paste = False
        accum    = bytearray()

        sys.stdout.write(prompt + BP_ON)
        sys.stdout.flush()

        try:
            tty.setraw(fd)
            while True:
                if not select.select([sys.stdin], [], [], 0.05)[0]:
                    if _pending_interrupt:
                        raise KeyboardInterrupt
                    continue
                chunk = os.read(fd, 1024)
                if not chunk:
                    continue
                accum.extend(chunk)

                while accum:
                    if not in_paste and accum.startswith(PASTE_START):
                        del accum[:len(PASTE_START)]
                        in_paste = True
                        continue

                    if in_paste:
                        end_idx = accum.find(PASTE_END)
                        if end_idx == -1:
                            paste = bytes(accum)
                            accum.clear()
                            text = paste.decode("utf-8", errors="ignore")
                        else:
                            paste = bytes(accum[:end_idx])
                            del accum[:end_idx + len(PASTE_END)]
                            in_paste = False
                            text = paste.decode("utf-8", errors="ignore")
                        text = re.sub(r"\s+", " ", text).strip()
                        if text:
                            self._buf.extend(text)
                            sys.stdout.write(text)
                            sys.stdout.flush()
                            for _ch in text:
                                self._col += 1
                                if self._col >= self._width:
                                    self._col -= self._width
                                    self._line += 1
                        continue

                    b = accum[0]
                    del accum[0]

                    if b in (13, 10):
                        termios.tcsetattr(fd, termios.TCSADRAIN, old)
                        sys.stdout.write(BP_OFF + "\r\n")
                        sys.stdout.flush()
                        return "".join(self._buf).strip()
                    if b == 4:
                        termios.tcsetattr(fd, termios.TCSADRAIN, old)
                        sys.stdout.write(BP_OFF + "\r\n")
                        sys.stdout.flush()
                        return None
                    if b == 3:
                        raise KeyboardInterrupt
                    if b in (127, 8):
                        if self._buf:
                            self._buf.pop()
                            self._col -= 1
                            if self._col < 0:
                                if self._line > 0:
                                    self._line -= 1
                                    self._col += self._width
                                else:
                                    self._col = 0
                            self._redraw(prompt)
                        continue
                    if b == 27:
                        if (len(accum) >= 4 and accum[0] == 91 and accum[1] in (13, 49, 0x0D)
                                and accum[2] == 59 and accum[3] == 50):
                            del accum[:5]
                            self._buf.append("\n")
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                            self._col = 0
                            self._line += 1
                            continue
                        if len(accum) >= 2 and accum[0] == 91:
                            del accum[:2]
                        elif accum:
                            del accum[:1]
                        continue
                    if 32 <= b < 127:
                        ch = chr(b)
                        self._buf.append(ch)
                        sys.stdout.write(ch)
                        sys.stdout.flush()
                        self._col += 1
                        if self._col >= self._width:
                            self._col -= self._width
                            self._line += 1

        except KeyboardInterrupt:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            sys.stdout.write(BP_OFF + "\r\n")
            sys.stdout.flush()
            raise
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass


_ibuf = InputBuffer()


# ══════════════════════════════════════════════════════════════════════════
#  STREAMING ENGINE
# ══════════════════════════════════════════════════════════════════════════

async def _stream_pydantic(query: str, history: List[ModelMessage]) -> Tuple[str, bool]:
    full = ""
    try:
        with Live("", console=console, refresh_per_second=8, transient=False) as live:
            async with agent.run_stream(query, message_history=history) as result:
                async for chunk in result.stream_text():
                    full = chunk
                    live.update(Markdown(full))
        return full, True
    except Exception as e:
        _w(f"\n{C_RED}Error: {e}{RESET}\n")
        return "", False


def _stream_parallel(
    query: str,
    history: List[ModelMessage],
    system_prompt: str,
) -> Tuple[str, bool]:
    if not _HAS_AGENT_LOOP:
        raise RuntimeError("open_agent.agent_loop not available")
    from open_agent.agent_loop import AgentLoop, TOOL_SCHEMAS
    streamed_parts: List[str] = []

    def _on_chunk(chunk: str) -> None:
        if not chunk:
            return
        clean = re.sub(r'\[\[TOOL_CALLS:[^\]]*\]\]', '', chunk)
        if clean:
            _w(clean)
            streamed_parts.append(clean)
        rr._streamed_text += clean
        rr._was_streamed = True

    loop = AgentLoop(
        base_url=CFG["base_url"],
        api_key="lm-studio",
        model=CFG["model_name"],
        system_prompt=system_prompt,
        tools=TOOL_SCHEMAS,
        max_tokens=2400,
        temperature=CFG.get("agent_temperature", 0.6),
        text_callback=_on_chunk,
    )
    result, all_tool_calls = loop.run(query, history)
    return result or "".join(streamed_parts), all_tool_calls


# ══════════════════════════════════════════════════════════════════════════
#  AGENT SESSION  (FIX 3: smarter augment with last-turn echo)
# ══════════════════════════════════════════════════════════════════════════

# ── Planning stall detector ───────────────────────────────────────────────
_PLAN_PHRASES = (
    "i will ", "i'll ", "let me ", "i am going to",
    "first, i", "first i will", "i will now", "i will start",
    "to answer this", "i'll start by", "i need to first",
    "i'll search", "i will search", "i will fetch", "i will look",
)

def _is_planning_stall(text: str) -> bool:
    if not text or len(text.strip()) < 10:
        return False
    lowered = text.lower().strip()
    has_plan = any(p in lowered for p in _PLAN_PHRASES)
    if not has_plan:
        return False
    has_execution = (
        "```"   in text
        or "http" in text
        or "✓"   in text
        or "\n-" in text
        or "\n1." in text
        or len(text) > 600
    )
    return not has_execution
# ─────────────────────────────────────────────────────────────────────────

class AgentSession:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._turns_since_memory_nudge = 0

    

    def _augment(self, query: str, recent_pairs=None) -> str:
        q = query.lower()
        hints: List[str] = []
    
        # Always echo the last exchange verbatim — small models lose
        # context beyond ~4K tokens even with full history passed in
        pairs = recent_pairs or self.session.pairs
        if pairs:
            last_u, last_a = pairs[-1]
            last_a_clean = re.sub(r'<think>.*?</think>', '', last_a, flags=re.DOTALL).strip()
            if len(last_u) + len(last_a_clean) < 1000:
                    hints.append(
                        f"[Previous exchange]\n"
                        f"User: {last_u[:200].strip()}\n"
                        f"You: {last_a_clean[:400].strip()}"
                    )


        if any(t in q for t in GROUNDING_TRIGGERS):
            hints.append("[Grounding required: run date via terminal first, then web_search.]")

        if any(t in q for t in SOUL_TRIGGERS):
            hints.append("[Complex task: call load_soul before proceeding.]")

        self._turns_since_memory_nudge += 1
        nudge_interval = CFG.get("memory_nudge_interval", 10)
        if self._turns_since_memory_nudge >= nudge_interval:
            hints.append("[Memory nudge: update MEMORY.md with any new facts from this conversation.]")
            self._turns_since_memory_nudge = 0

        return query + ("\n\n" + "\n".join(hints) if hints else "")

    async def run(self, query: str) -> Optional[str]:
        sess = self.session
        tok  = sess.token_est + _count_tokens(query)

        if tok > CFG["ctx_compress"]:
            msg = sess.compress()
            td.info(f"context compressed: {msg}")
        elif tok > CFG["ctx_warn"]:
            td.warn(f"context ~{tok:,} tokens — approaching limit")

        # Show token budget bar
        pct  = min(100, int(tok / CFG["ctx_limit"] * 100))
        bar  = "█" * (pct // 10) + "░" * (10 - pct // 10)
        _w(
            f"\n  {DIM}[{bar}] {tok:,}/{CFG['ctx_limit']:,} tokens  "
            f"session {C_PURPLE}{sess.id}{RESET}\n"
        )

        augmented = self._augment(query)
        history   = sess.to_messages()

# ── Main run loop (up to 3 retries) ────────────────────────────
        use_parallel = CFG.get("use_parallel_loop", False) and _HAS_AGENT_LOOP

        for attempt in range(3):
            try:
                rr.reset()
                if use_parallel:
                    rr.stream_start()
                    ev_loop = asyncio.get_event_loop()
                    full, all_tool_calls = await ev_loop.run_in_executor(
                        None,
                        lambda: _stream_parallel(augmented, history, SYSTEM_PROMPT),
                    )
                    ok = bool(full)
                    if ok:
                        rr.stream_end()
                else:
                    full, ok = await _stream_pydantic(augmented, history)
                    all_tool_calls = []

                if ok and full:
                    full = _normalize_output(full)

                    # ── Planning stall interceptor (attempt 0 only) ─────
                    if _is_planning_stall(full) and attempt == 0:
                        td.warn("planning stall — injecting execution prompt")
                        from pydantic_ai.messages import (
                            ModelRequest, ModelResponse,
                            UserPromptPart, TextPart,
                        )
                        forced_history = list(history) + [
                            ModelResponse(parts=[TextPart(content=full)]),
                            ModelRequest(parts=[UserPromptPart(content=(
                                "EXECUTE NOW. Do not restate your plan. "
                                "Call the first required tool immediately, "
                                "or answer directly with no preamble."
                            ))]),
                        ]
                        rr.reset()
                        rr.stream_start()
                        if use_parallel:
                            ev_loop2 = asyncio.get_event_loop()
                            full, all_tool_calls = await ev_loop2.run_in_executor(
                                None,
                                lambda: _stream_parallel(augmented, forced_history, SYSTEM_PROMPT),
                            )
                            ok = bool(full)
                            if ok:
                                rr.stream_end()
                        else:
                            full, ok = await _stream_pydantic(augmented, forced_history)
                            all_tool_calls = []
                        full = _normalize_output(full)
                    # ── End interceptor ─────────────────────────────────

                    if ok and full:
                        sess.add(query, full, tool_calls=all_tool_calls)
                        _w(f"\n{DIM}{'─' * min(60, _terminal_width())}{RESET}\n")
                        return full

            except Exception as e:
                _w(f"\n{C_RED}Error: {e}{RESET}\n")

            if attempt < 2:
                _w(f"\n{DIM}Retry {attempt + 1}/3…{RESET}\n")
                await asyncio.sleep(1.5)

        _w(f"\n{C_RED}{SYM_TOOL_ERR} Failed after 3 attempts.{RESET}\n")
        return None

# ══════════════════════════════════════════════════════════════════════════
#  FIRST-RUN SETUP WIZARD  (unchanged)
# ══════════════════════════════════════════════════════════════════════════

def _ask(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    _w(f"  {C_CYAN}{prompt}{hint}{RESET}: ")
    try:
        val = input().strip()
        return val or default
    except (EOFError, KeyboardInterrupt):
        return default

def _yn(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    _w(f"  {C_CYAN}{prompt}{RESET} [{hint}]: ")
    try:
        val = input().strip().lower()
        if not val:
            return default
        return val in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return default

def run_setup_wizard(cfg: Dict, first_run: bool = True) -> Dict:
    _ln()
    if first_run:
        console.print(Panel(
            f"[header]Welcome to Open-Agent[/header]\n\n"
            f"[dim]Let's configure your setup in about 60 seconds.\n"
            f"Press Enter to accept defaults. Run [/dim][tool.name]/setup[/tool.name][dim] at any time to reconfigure.[/dim]",
            box=box.ROUNDED, border_style="border", padding=(1, 3),
        ))
    else:
        console.print(Panel(
            "[header]Open-Agent Setup[/header]\n[dim]Reconfigure your settings.[/dim]",
            box=box.ROUNDED, border_style="border", padding=(1, 2),
        ))
    _ln()

    _w(f"\n{C_AMBER}{BOLD}[1/3] Inference Server{RESET}\n")
    _w(f"  {DIM}Default: llama.cpp on localhost:8083{RESET}\n\n")
    base_url = _ask("Server URL", cfg.get("base_url", DEFAULT_CONFIG["base_url"]))

    _w(f"\n  {DIM}Checking connection…{RESET}")
    try:
        import urllib.request
        urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=3)
        _w(f"  {C_GREEN}✓ Connected{RESET}\n")
    except Exception:
        _w(f"  {C_GOLD}⚠ Could not reach server{RESET}\n")

    _w(f"\n{C_AMBER}{BOLD}[2/3] Web Search (SearxNG){RESET}\n")
    searxng_url = _ask("SearxNG URL", cfg.get("searxng_url", DEFAULT_CONFIG["searxng_url"]))

    _w(f"\n{C_AMBER}{BOLD}[3/3] Obsidian Vault (optional){RESET}\n")
    use_obsidian  = _yn("Connect Obsidian vault?", default=bool(cfg.get("obsidian_path")))
    obsidian_path = ""
    if use_obsidian:
        obsidian_path = _ask("Vault path", cfg.get("obsidian_path", str(Path.home() / "Documents" / "Obsidian")))
        if obsidian_path and not Path(obsidian_path).exists():
            _w(f"  {C_GOLD}⚠ Path doesn't exist yet{RESET}\n")
        elif obsidian_path:
            _w(f"  {C_GREEN}✓ Vault found{RESET}\n")

    cfg.update({
        "base_url":     base_url,
        "searxng_url":  searxng_url,
        "obsidian_path":obsidian_path,
        "setup_done":   True,
    })
    save_config(cfg)

    _ln()
    console.print(Panel(
        f"[tool.ok]✓ Setup complete![/tool.ok]\n\n"
        f"[dim]Config saved to[/dim] [accent]{CONFIG_FILE}[/accent]\n"
        f"[dim]Type[/dim] [tool.name]/help[/tool.name] [dim]to see all commands.[/dim]",
        box=box.ROUNDED, border_style="border", padding=(1, 3),
    ))
    _ln()
    return cfg


# ══════════════════════════════════════════════════════════════════════════
#  BANNER
# ══════════════════════════════════════════════════════════════════════════

LOGO_WIDE = r"""
   ██████╗ ██████╗ ███████╗███╗   ██╗      █████╗  ██████╗ ███████╗███╗   ██╗████████╗
  ██╔═══██╗██╔══██╗██╔════╝████╗  ██║     ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
  ██║   ██║██████╔╝█████╗  ██╔██╗ ██║████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║
  ██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║╚═══╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║
  ╚██████╔╝██║     ███████╗██║ ╚████║     ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║
   ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝     ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝
"""

LOGO_NARROW = """
  ░█████╗░██████╗░███████╗███╗░░██╗
  ██╔══██╗██╔══██╗██╔════╝████╗░██║
  ██║░░██║██████╔╝█████╗░░██╔██╗██║
  ██║░░██║██╔═══╝░██╔══╝░░██║╚████║
  ╚█████╔╝██║░░░░░███████╗██║░╚███║
  ░╚════╝░╚═╝░░░░░╚══════╝╚═╝░░╚══╝

  ░█████╗░░██████╗░███████╗███╗░░██╗████████╗
  ██╔══██╗██╔════╝░██╔════╝████╗░██║╚══██╔══╝
  ███████║██║░░██╗░█████╗░░██╔██╗██║░░░██║░░░
  ██╔══██║██║░░╚██╗██╔══╝░░██║╚████║░░░██║░░░
  ██║░░██║╚██████╔╝███████╗██║░╚███║░░░██║░░░
  ╚═╝░░╚═╝░╚═════╝░╚══════╝╚═╝░░╚══╝░░░╚═╝░░░

"""

VERSION = "1.1"

def print_banner(session_id: str) -> None:
    width = _terminal_width()
    _ln()
    if width >= 100:
        for line in LOGO_WIDE.splitlines():
            pad = max(0, (width - len(line)) // 2)
            _w(f"{C_CYAN}{' ' * pad}{line}{RESET}\n")
    else:
        lines = LOGO_NARROW.splitlines()
        mid = len(lines) // 2
        for i, line in enumerate(lines):
            pad = max(0, (width - len(line)) // 2)
            color = C_CYAN if i <= mid else C_PURPLE
            _w(f"{color}{' ' * pad}{line}{RESET}\n")

    tagline = "Run capable AI on any laptop — private, free, yours."
    pad = max(0, (width - len(tagline)) // 2)
    _w(f"\n{' ' * pad}{DIM}{tagline}{RESET}\n\n")

    tbl = Table(box=None, show_header=False, padding=(0, 1))
    tbl.add_column(style="dim")
    tbl.add_column(style="tool.name")
    tbl.add_row("session", session_id)
    tbl.add_row("version", VERSION)
    console.print(Align.center(Panel(tbl, box=box.ROUNDED, border_style="border", padding=(0, 3))))

    _w(f"\n  {DIM}Type[/] ")
    _w(f"{C_PURPLE}/help{RESET}")
    _w(f"  {DIM}·{RESET}  ")
    _w(f"{C_PURPLE}/sessions{RESET}")
    _w(f"  {DIM}·{RESET}  ")
    _w(f"{C_PURPLE}/exit{RESET}")
    _w(f"  {DIM}to quit{RESET}\n\n")


# ══════════════════════════════════════════════════════════════════════════
#  SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════

def _help_table() -> Table:
    tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 2), header_style="header")
    tbl.add_column("Command",     style="tool.name", no_wrap=True)
    tbl.add_column("Description", style="dim")
    tbl.add_column("Alias",       style="dim", no_wrap=True)
    rows = [
        ("/help",     "Show this help",                     "/h"),
        ("/sessions", "Browse and resume past sessions",    "/s"),
        ("/new",      "Start a fresh session",              ""),
        ("/history",  "Token count + session info",         "/hi"),
        ("/compress", "Force-compress conversation history",""),
        ("/clear",    "Wipe current session",               "/cl"),
        ("/soul",     "Display SOUL.md",                    ""),
        ("/setup",    "Reconfigure settings",               ""),
        ("/sources",  "List RSS sources",                   ""),
        ("/parallel", "Toggle parallel tool execution",     "/pll"),
        ("/save",     "Force-save current session",         ""),
        ("/exit",     "Save and quit",                      "/q"),
    ]
    for cmd, desc, alias in rows:
        tbl.add_row(cmd, desc, alias)
    return tbl


def _sessions_panel(runner: "AgentRunner") -> bool:
    sessions = Session.list_all()
    if not sessions:
        console.print("[dim]No sessions found.[/dim]")
        return True

    tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 2), header_style="header")
    tbl.add_column("#",           style="dim",       no_wrap=True)
    tbl.add_column("Session ID",  style="tool.name", no_wrap=True)
    tbl.add_column("Title",       style="dim")
    tbl.add_column("Turns",       style="dim",       no_wrap=True)
    tbl.add_column("Last active", style="dim",       no_wrap=True)

    for i, s in enumerate(sessions[:15], 1):
        try:
            la = datetime.fromisoformat(s["last_active"]).strftime("%b %d %H:%M")
        except Exception:
            la = s.get("last_active", "")[:16]
        current = " ← current" if s["id"] == runner.session.id else ""
        turns   = str(s.get("turns", s.get("turn_count", 0)))
        tbl.add_row(str(i), s["id"] + current, (s.get("title") or "(untitled)")[:45], turns, la)

    console.print(Panel(tbl, title="[header]Sessions[/header]", border_style="border", box=box.ROUNDED))
    _w(f"\n  {DIM}Enter number to resume, or press Enter to stay:{RESET} ")
    try:
        val = input().strip()
        if val.isdigit():
            idx = int(val) - 1
            if 0 <= idx < len(sessions):
                sid = sessions[idx]["id"]
                if sid != runner.session.id:
                    runner.session.save()
                    runner.session = Session(sid)
                    runner._refresh_agent()
                    _w(f"  {C_GREEN}✓ Resumed session {sid}{RESET}\n")
                    td.info(f"loaded {len(runner.session.pairs)} turns")
                else:
                    _w(f"  {DIM}Already in that session.{RESET}\n")
    except (EOFError, KeyboardInterrupt):
        pass
    return True


async def handle_slash(cmd: str, runner: "AgentRunner") -> bool:
    global CFG
    c = cmd.strip().split()[0].lower()

    if c in ("/exit", "/quit", "/q"):
        runner.session.save()
        console.print(Panel("[dim]Session saved. Goodbye.[/dim]", border_style="border"))
        sys.exit(0)

    if c in ("/help", "/h"):
        console.print(Panel(
            _help_table(),
            title=f"[header]Open-Agent {VERSION}[/header]",
            border_style="border", box=box.ROUNDED,
        ))
        return True

    if c in ("/sessions", "/s"):
        return _sessions_panel(runner)

    if c == "/new":
        runner.session.save()
        runner.session = Session()
        runner._refresh_agent()
        _w(f"  {C_GREEN}✓ New session: {runner.session.id}{RESET}\n")
        return True

    if c in ("/clear", "/cl"):
        runner.session.pairs.clear()
        runner.session.summary = ""
        runner.session.save()
        _w(f"  {C_GREEN}✓ Session cleared.{RESET}\n")
        return True

    if c == "/compress":
        msg = runner.session.compress()
        _w(f"  {C_GREEN}✓ {msg}{RESET}\n")
        return True

    if c in ("/history", "/hi"):
        s   = runner.session
        tok = s.token_est
        pct = min(100, int(tok / CFG["ctx_limit"] * 100))
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        tbl.add_column(style="tool.name")
        tbl.add_column(style="dim")
        tbl.add_row("Session ID",     s.id)
        tbl.add_row("Title",          s.title or "(none)")
        tbl.add_row("Verbatim turns", str(len(s.pairs)))
        tbl.add_row("Has summary",    "yes" if s.summary else "no")
        tbl.add_row("Summary chars",  f"{len(s.summary):,}")
        tbl.add_row("Token estimate", f"~{tok:,}")
        tbl.add_row("Context budget", f"[{bar}] {pct}%  of  {CFG['ctx_limit']:,}")
        tbl.add_row("max_pairs",      str(CFG["max_pairs"]))
        console.print(Panel(tbl, title="[header]Session Info[/header]", border_style="border", box=box.ROUNDED))
        return True

    if c == "/soul":
        if SOUL_FILE.exists():
            console.print(Panel(
                Markdown(SOUL_FILE.read_text()),
                title="[header]SOUL.md[/header]",
                border_style="border", box=box.ROUNDED, padding=(1, 2),
            ))
        else:
            _w(f"  {C_RED}SOUL.md not found at {SOUL_FILE}{RESET}\n")
        return True

    if c == "/setup":
        CFG = run_setup_wizard(CFG, first_run=False)
        return True

    if c == "/sources":
        names  = sorted(RSS_SOURCES.keys())
        cols   = 4
        chunks = [names[i:i+cols] for i in range(0, len(names), cols)]
        tbl    = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        for _ in range(cols):
            tbl.add_column(style="tool.name")
        for chunk in chunks:
            tbl.add_row(*chunk + [""] * (cols - len(chunk)))
        cats = " · ".join(sorted(BROAD_CATS))
        console.print(Panel(
            tbl, title="[header]RSS Sources[/header]",
            subtitle=f"[dim]categories: {cats}[/dim]",
            border_style="border", box=box.ROUNDED,
        ))
        return True

    if c == "/save":
        runner.session.save()
        _w(f"  {C_GREEN}✓ Saved {runner.session.id}{RESET}\n")
        return True

    if c in ("/parallel", "/pll"):
        enabled = not CFG.get("use_parallel_loop", False)
        CFG["use_parallel_loop"] = enabled
        save_config(CFG)
        status = f"{C_GREEN}ENABLED{RESET}" if enabled else f"{C_SLATE}disabled{RESET}"
        avail  = "" if _HAS_AGENT_LOOP else f"  {C_GOLD}(agent_loop not installed){RESET}"
        _w(f"  Parallel mode: {status}{avail}\n")
        return True

    return False


# ══════════════════════════════════════════════════════════════════════════
#  AGENT RUNNER
# ══════════════════════════════════════════════════════════════════════════

class AgentRunner:
    def __init__(self, session: Session) -> None:
        self.session    = session
        self._agent_obj = AgentSession(session)

    def _refresh_agent(self) -> None:
        self._agent_obj = AgentSession(self.session)

    @property
    def _agent(self) -> AgentSession:
        if self._agent_obj.session is not self.session:
            self._refresh_agent()
        return self._agent_obj


# ══════════════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════

async def _main_loop(runner: AgentRunner) -> None:
    while True:
        try:
            global _pending_interrupt
            _pending_interrupt = False
            clear_interrupt()

            _ln()
            user_input = _ibuf.readline()

            if user_input is None:
                runner.session.save()
                break
            if not user_input.strip():
                continue
            if user_input.startswith("/"):
                if await handle_slash(user_input, runner):
                    continue

            await runner._agent.run(user_input)

        except KeyboardInterrupt:
            if _pending_interrupt:
                _pending_interrupt = False
                clear_interrupt()
                # If streaming was active, clean up the display
                if rr._was_streamed:
                    if rr._saved_cursor:
                        sys.stdout.write("\x1b[u\x1b[J")
                        sys.stdout.flush()
                    rr.reset()
                else:
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                _w(f"  {C_AMBER}⚡ interrupted{RESET} — type a new message or /exit\n")
                continue
            else:
                _w(f"\n{DIM}Ctrl-C — saving…{RESET}\n")
                runner.session.save()
                _w(f"{DIM}Goodbye.{RESET}\n")
                break
        except EOFError:
            runner.session.save()
            break
        except Exception as e:
            _w(f"{C_RED}Unhandled error: {e}{RESET}\n")
            import traceback
            traceback.print_exc()
        finally:
                _db.close()


# ══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════

def main() -> None:
    global CFG

    parser = argparse.ArgumentParser(
        prog="open-agent",
        description=f"Open-Agent {VERSION} — local AI agent for consumer hardware",
    )
    parser.add_argument("--setup",   action="store_true", help="Run setup wizard")
    parser.add_argument("--version", action="store_true", help="Print version")
    parser.add_argument("--new",     action="store_true", help="Start a new session")
    parser.add_argument("--resume",  metavar="SESSION_ID", help="Resume a specific session")
    parser.add_argument("--tui",     action="store_true", help="Launch TUI mode")
    args = parser.parse_args()

    if args.version:
        print(f"open-agent {VERSION}")
        sys.exit(0)

    if args.tui:
        try:
            from open_agent.tui import run_tui
            run_tui()
        except ImportError:
            _w(f"{C_RED}TUI module not available.{RESET}\n")
        sys.exit(0)

    if not CFG.get("setup_done") or args.setup:
        CFG = run_setup_wizard(CFG, first_run=not CFG.get("setup_done"))

    if args.resume:
        session = Session(args.resume)
        if not session.path.exists() and not _db.get_session(args.resume):
            _w(f"{C_RED}Session '{args.resume}' not found.{RESET}\n")
            sys.exit(1)
    elif args.new:
        session = Session()
    else:
        all_sessions = Session.list_all()
        session = Session(all_sessions[0]["id"]) if all_sessions else Session()

    runner = AgentRunner(session)
    print_banner(session.id)

    if session.pairs:
        td.info(f"resumed: {len(session.pairs)} turns loaded")

    asyncio.run(_main_loop(runner))


def main_sync() -> None:
    main()


if __name__ == "__main__":
    main()
