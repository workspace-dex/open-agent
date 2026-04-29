#!/usr/bin/env python3
"""
Open-Agent — local AI agent for consumer hardware.
One file. No cloud. Yours.
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
import subprocess
import sys
import termios
import tty
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

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
from rich.columns import Columns
from rich.live import Live


# ══════════════════════════════════════════════════════════════════════════
#  ANSI HELPERS  (direct stdout — no Rich buffering during streaming)
# ══════════════════════════════════════════════════════════════════════════

ESC        = "\x1b"
RESET      = f"{ESC}[0m"
BOLD       = f"{ESC}[1m"
DIM        = f"{ESC}[2m"
C_CYAN     = f"{ESC}[38;5;117m"
C_GREEN    = f"{ESC}[38;5;114m"
C_AMBER    = f"{ESC}[38;5;214m"
C_PURPLE   = f"{ESC}[38;5;183m"
C_TEAL     = f"{ESC}[38;5;115m"
C_RED      = f"{ESC}[38;5;203m"
C_SLATE    = f"{ESC}[38;5;60m"
C_WHITE    = f"{ESC}[38;5;252m"
C_BLUE     = f"{ESC}[38;5;75m"
CLEAR_LINE = f"\r{ESC}[2K"

def _w(s: str):
    sys.stdout.write(s); sys.stdout.flush()

def _ln(s: str = ""):
    _w(s + "\n")


def _normalize_output(text: str) -> str:
    """Tighten assistant output before rendering and saving."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_like_planning(text: str) -> bool:
    lowered = text.lower()
    planning_markers = (
        "i will", "i'll", "let me", "i am going to", "i’m going to",
        "i will now", "first i will", "i’ll now", "i will fetch",
        "let me get", "let me search", "i will search"
    )
    return any(marker in lowered for marker in planning_markers)


# ══════════════════════════════════════════════════════════════════════════
#  RICH CONSOLE  (panels, tables, non-streaming output)
# ══════════════════════════════════════════════════════════════════════════

THEME = Theme({
    "agent":     "bold #7DCFFF",
    "user":      "bold #9ECE6A",
    "tool.name": "#BB9AF7",
    "tool.ok":   "#9ECE6A",
    "tool.err":  "#F7768E",
    "tool.info": "#73DACA",
    "dim":       "#565F89",
    "header":    "bold #7AA2F7",
    "accent":    "#FF9E64",
    "border":    "#3B4261",
})
console = Console(theme=THEME, highlight=False)


# ══════════════════════════════════════════════════════════════════════════
#  CONFIG & PATHS
# ══════════════════════════════════════════════════════════════════════════

CONFIG_DIR = Path.home() / ".config" / "open-agent"
SESSIONS_DIR = CONFIG_DIR / "sessions"
CONFIG_FILE = CONFIG_DIR / "config.json"
SOUL_FILE = Path(__file__).parent / "SOUL.md"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "base_url": "http://localhost:8083/v1",
    "model_name": "llama.cpp",
    "searxng_url": "http://localhost:8081/search",
    "obsidian_path": "",
    "max_pairs": 3,
    "ctx_limit": 55_000,
    "ctx_warn": 40_000,
    "ctx_compress": 48_000,
    "setup_done": False,
}

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg: dict):
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

# ── Terminal safety: block dangerous shell patterns ───────────────────────
def _safe_expand_path(path: str) -> Path:
    """Resolve path and HARD restrict to user's home directory only.
    This is the single biggest safety improvement."""
    p = Path(path).expanduser().resolve()
    home = Path.home().resolve()
    if str(p).startswith(str(home)) or str(p).startswith(str(CONFIG_DIR.resolve())):
        return p
    raise PermissionError(
        f"⛔ Path '{path}' is outside your home directory for safety.\n"
        f"File tools are restricted to ~/... only."
    )

# ── Terminal safety: block dangerous shell patterns ───────────────────────
BLOCKED_PATTERNS = [
    # Original strong set
    r"\brm\s+-rf\s+/",
    r"\brm\s+--no-preserve-root",
    r"\bmkfs\b",
    r"\bdd\b.*of=/dev/(sd|nvme|hd|vd)",
    r">\s*/dev/sd",
    r":\(\)\s*\{.*\}\s*;", 
    # Privilege escalation
    r"\bsudo\s+su\b",
    r"\bsudo\s+bash\b",
    r"\bsudo\s+sh\b",
    r"\bchmod\s+777\s+/",
    r"\bchown\b.*\s+/etc/",
    # Exfiltration / reverse shells
    r"(curl|wget)\s+.*\|\s*(ba)?sh",
    r"\bnc\b.*-e\s+/bin/(ba)?sh",
    r"\bpython\b.*-c.*socket.*connect",
    r"exec\s+\d+<>/dev/tcp",
    # Credential theft
    r"cat\s+/etc/(passwd|shadow|sudoers)",
    r"/etc/ssh/.*id_rsa",
    # Package manager abuse
    r"\bpip\s+install\b.*--break-system",
    # ADDITIONAL HARDENING (new)
    r"\brm\s+-r[rf]?\s+~",
    r"\brm\s+-r[rf]?\s+\$HOME",
    r"\brm\s+-r[rf]?\s+/home",
    r"\bshred\b",
    r"\bwipefs\b",
    r"\b:(){:|:&};:",                     # fork bomb variant
    r"\bbash\s+-i\s+>&\s+/dev/tcp",       # common reverse shell
    r"\bnc\s+-e\s+/bin/(ba)?sh",
]

_BLOCKED_RE = [re.compile(p, re.IGNORECASE) for p in BLOCKED_PATTERNS]

def _is_dangerous(cmd: str) -> tuple[bool, str]:
    for pattern in _BLOCKED_RE:
        if pattern.search(cmd):
            return True, pattern.pattern
    return False, ""


# ══════════════════════════════════════════════════════════════════════════
#  AGENT SETUP
# ══════════════════════════════════════════════════════════════════════════

provider = OpenAIProvider(base_url=CFG["base_url"], api_key="lm-studio")
model    = OpenAIChatModel(model_name=CFG["model_name"], provider=provider)

SYSTEM_PROMPT = """You are a fast, grounded local AI agent.

RITUALS — before any real-world or time-sensitive query:
1. run_terminal("date && uname -r") — anchor current time/system
2. web_search if knowledge might be stale (default: assume it is for recent topics)
3. Confirm facts across ≥2 results before asserting

EXECUTION RULES:
- If a tool is relevant, call it immediately.
- Do not narrate plans when execution is needed.
- Prefer action over explanation.
- Use the supplied tools directly instead of describing what you will do.

TOOLS: web_search · smart_research · fetch_page · read_rss_by_name · run_terminal · read_file · write_file · load_soul · search_obsidian · read_obsidian_note · write_obsidian_note

When writing code/files: use write_file. Show key code in response.
Be direct. Use Markdown. No filler."""

agent = Agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    deps_type=None,
    model_settings={
        "temperature":       0.5,
        "max_tokens":        2400,
        "top_p":             0.9,
        "frequency_penalty": 0.35,
        "presence_penalty":  0.15,
    },
    retries=2,
)


# ══════════════════════════════════════════════════════════════════════════
#  TOOL BADGE  (in-place ANSI overwrite — no Rich.Live)
# ══════════════════════════════════════════════════════════════════════════

class ToolBadge:
    def start(self, name: str, detail: str):
        _w(f"  {C_AMBER}⟳{RESET} {C_PURPLE}{name}{RESET} {DIM}{detail[:120]}{RESET}\n")

    def done(self, name: str, summary: str, ok: bool = True, chars: int = 0):
        icon = f"{C_GREEN}✓{RESET}" if ok else f"{C_RED}✗{RESET}"
        ch = f"  {DIM}{chars:,}c{RESET}" if chars else ""
        _w(f"  {icon} {C_PURPLE}{name}{RESET} {DIM}{summary[:120]}{ch}{RESET}\n")

    def info(self, label: str):
        _w(f"  {DIM}· {label}{RESET}\n")

td = ToolBadge()


# ══════════════════════════════════════════════════════════════════════════
#  TOOLS
# ══════════════════════════════════════════════════════════════════════════

@agent.tool
async def web_search(ctx: RunContext, query: str) -> str:
    """Search the web via SearxNG. Use for current facts, news, or anything potentially stale."""
    td.start("web_search", query)
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.get(CFG["searxng_url"],
                params={"q": query, "format": "json", "categories": "general", "language": "en"})
            r.raise_for_status()
        results = r.json().get("results", [])[:7]
        if not results:
            td.done("web_search", "no results", ok=False); return "No results."
        out = [f"[{i}] {x.get('title','')}\nURL: {x.get('url','')}\n{x.get('content','')[:400]}"
               for i, x in enumerate(results, 1)]
        body = "\n\n".join(out)
        td.done("web_search", f"{len(results)} results", chars=len(body))
        return body
    except Exception as e:
        td.done("web_search", str(e), ok=False); return f"Search error: {e}"


@agent.tool
async def smart_research(ctx: RunContext, queries: list[str]) -> str:
    """Run up to 4 parallel web queries and merge. For multi-angle research."""
    queries = queries[:4]
    td.start("smart_research", " | ".join(queries))
    async def _one(q: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=12) as c:
                r = await c.get(CFG["searxng_url"],
                    params={"q": q, "format": "json", "categories": "general", "language": "en"})
                r.raise_for_status()
            res = r.json().get("results", [])[:4]
            return "\n".join([f"### {q}"] +
                [f"**{i.get('title','')}**\n{i.get('url','')}\n{i.get('content','')[:350]}" for i in res]
            ) if res else f"### {q}\nNo results."
        except Exception as e: return f"### {q}\nError: {e}"
    parts  = await asyncio.gather(*[_one(q) for q in queries])
    merged = "\n\n---\n\n".join(parts)
    td.done("smart_research", f"merged {len(queries)} queries", chars=len(merged))
    return merged


@agent.tool
async def fetch_page(ctx: RunContext, url: str) -> str:
    """Fetch readable text from a URL."""
    td.start("fetch_page", url[:70])
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s{3,}", "\n\n", text).strip()[:6000]
        td.done("fetch_page", url[:50], chars=len(text)); return text
    except Exception as e:
        td.done("fetch_page", str(e), ok=False); return f"Fetch error: {e}"


@agent.tool
async def run_terminal(ctx: RunContext, command: str) -> str:
    """Run a safe shell command. Dangerous patterns are blocked for your safety."""
    dangerous, pattern = _is_dangerous(command)
    if dangerous:
        td.done("run_terminal", f"BLOCKED — dangerous pattern", ok=False)
        return (
            f"⛔ Command blocked for safety.\n"
            f"Pattern matched: {pattern}\n"
            f"If this is a legitimate command, run it manually in your terminal."
        )
    td.start("run_terminal", command[:70])
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        ok  = proc.returncode == 0
        out = proc.stdout.strip(); err = proc.stderr.strip()
        td.done("run_terminal", (out or err or "done")[:60], ok=ok)
        return f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}\nExit: {proc.returncode}"
    except subprocess.TimeoutExpired:
        td.done("run_terminal", "timed out (30s)", ok=False); return "Command timed out."
    except Exception as e:
        td.done("run_terminal", str(e), ok=False); return f"Error: {e}"


@agent.tool
async def read_file(ctx: RunContext, path: str) -> str:
    """Read a local file (up to 8,000 chars). RESTRICTED TO ~/ FOR SAFETY."""
    td.start("read_file", path)
    try:
        p = _safe_expand_path(path)
        content = p.read_text(encoding="utf-8")[:8000]
        td.done("read_file", str(p), chars=len(content))
        return content
    except PermissionError as e:
        td.done("read_file", str(e), ok=False)
        return str(e)
    except Exception as e:
        td.done("read_file", str(e), ok=False)
        return f"Read error: {e}"

@agent.tool
async def write_file(ctx: RunContext, path: str, content: str) -> str:
    """Write content to a local file. RESTRICTED TO ~/ FOR SAFETY."""
    td.start("write_file", f"{path} ({len(content):,} chars)")
    try:
        p = _safe_expand_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        td.done("write_file", f"→ {p}", chars=len(content))
        return f"✓ Written {len(content):,} chars to {p}"
    except PermissionError as e:
        td.done("write_file", str(e), ok=False)
        return str(e)
    except Exception as e:
        td.done("write_file", str(e), ok=False)
        return f"Write error: {e}"

@agent.tool
async def load_soul(ctx: RunContext) -> str:
    """Load extended behavioral instructions from SOUL.md. Call for complex/research tasks."""
    td.start("load_soul", "SOUL.md")
    try:
        content = SOUL_FILE.read_text(encoding="utf-8")
        td.done("load_soul", "behavioral core loaded", chars=len(content)); return content
    except FileNotFoundError:
        td.done("load_soul", "SOUL.md not found", ok=False)
        return "SOUL.md not found alongside agent.py."
    except Exception as e:
        td.done("load_soul", str(e), ok=False); return f"Error: {e}"


# ── Obsidian ──────────────────────────────────────────────────────────────

def _vault() -> Optional[Path]:
    p = CFG.get("obsidian_path", "")
    return Path(p) if p else None


@agent.tool
async def search_obsidian(ctx: RunContext, query: str) -> str:
    """Search your Obsidian vault for notes containing specific text."""
    vault = _vault()
    if not vault or not vault.exists():
        return "Obsidian vault not configured. Run /setup to configure."
    td.start("search_obsidian", query)
    try:
        results = []
        for p in vault.rglob("*.md"):
            if any(part.startswith('.') for part in p.parts): continue
            if query.lower() in p.read_text(encoding="utf-8", errors="ignore").lower():
                results.append(f"- {p.relative_to(vault)}")
        if not results:
            td.done("search_obsidian", "no matches", ok=False); return "No matching notes."
        td.done("search_obsidian", f"{len(results)} matches")
        return "Matching notes:\n" + "\n".join(results[:25])
    except Exception as e:
        td.done("search_obsidian", str(e), ok=False); return f"Error: {e}"


@agent.tool
async def read_obsidian_note(ctx: RunContext, note_name: str) -> str:
    """Read a specific note from your Obsidian vault."""
    vault = _vault()
    if not vault or not vault.exists():
        return "Obsidian vault not configured. Run /setup."
    if not note_name.endswith(".md"): note_name += ".md"
    td.start("read_obsidian_note", note_name)
    try:
        matches = [m for m in vault.rglob(note_name)
                   if not any(p.startswith('.') for p in m.parts)]
        if not matches:
            td.done("read_obsidian_note", "not found", ok=False)
            return f"Note '{note_name}' not found."
        content = matches[0].read_text(encoding="utf-8")[:10000]
        td.done("read_obsidian_note", note_name, chars=len(content)); return content
    except Exception as e:
        td.done("read_obsidian_note", str(e), ok=False); return f"Error: {e}"


@agent.tool
async def write_obsidian_note(ctx: RunContext, note_name: str, content: str, append: bool = True) -> str:
    """Create or append to an Obsidian note."""
    vault = _vault()
    if not vault or not vault.exists():
        return "Obsidian vault not configured. Run /setup."
    if not note_name.endswith(".md"): note_name += ".md"
    td.start("write_obsidian_note", f"{note_name} append={append}")
    try:
        matches = [m for m in vault.rglob(note_name)
                   if not any(p.startswith('.') for p in m.parts)]
        target = matches[0] if matches else vault / note_name
        mode   = "a" if append and target.exists() else "w"
        with open(target, mode, encoding="utf-8") as f:
            f.write(("\n\n" if mode == "a" else "") + content)
        action = "Appended to" if mode == "a" else "Created"
        td.done("write_obsidian_note", f"{action} {note_name}")
        return f"✓ {action} {target.relative_to(vault)}"
    except Exception as e:
        td.done("write_obsidian_note", str(e), ok=False); return f"Error: {e}"


# ── RSS ───────────────────────────────────────────────────────────────────

RSS_SOURCES: dict[str, str] = {
    "eff": "https://www.eff.org/rss/updates.xml",
    "schneier": "https://www.schneier.com/feed/atom/",
    "krebs": "https://krebsonsecurity.com/feed/",
    "bleeping": "https://www.bleepingcomputer.com/feed/",
    "techcrunch": "https://techcrunch.com/feed/",
    "verge": "http://www.theverge.com/rss/full.xml",
    "engadget": "http://www.engadget.com/rss-full.xml",
    "venturebeat": "http://venturebeat.com/feed/",
    "ars": "https://arstechnica.com/feed/",
    "hn": "https://news.ycombinator.com/rss",
    "hn_show": "http://hnrss.org/show",
    "hn_launches": "https://hnrss.org/launches",
    "pragmatic": "https://blog.pragmaticengineer.com/rss/",
    "cloudflare": "https://blog.cloudflare.com/rss/",
    "stripe": "https://stripe.com/blog/feed.rss",
    "meta_eng": "https://engineering.fb.com/feed/",
    "julia_evans": "https://jvns.ca/atom.xml",
    "danluu": "https://danluu.com/atom.xml",
    "arxiv_ai": "https://rss.arxiv.org/rss/cs.AI",
    "arxiv_cl": "https://rss.arxiv.org/rss/cs.CL",
    "huggingface": "https://huggingface.co/blog/feed.xml",
    "farnam": "https://fs.blog/feed/",
    "producthunt": "http://www.producthunt.com/feed",
}
BROAD_CATS: dict[str, list[str]] = {
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
    if feed.bozo: return f"RSS error: {feed.bozo_exception}"
    if not feed.entries: return f"No entries: {url}"
    out = [f"**{feed.feed.get('title','Feed')}**\n"]
    for i, e in enumerate(feed.entries[:min(limit, 15)], 1):
        out.append(f"{i}. **{e.get('title','No title')}**\n"
                   f"   {e.get('published', e.get('updated',''))}\n"
                   f"   {e.get('link','')}\n"
                   f"   {e.get('summary', e.get('description',''))[:260].strip()}\n")
    return "\n".join(out)

@agent.tool
async def read_rss_by_name(ctx: RunContext, name: str, limit: int = 8) -> str:
    """Fetch RSS from curated sources. Names: hn, verge, arxiv_ai... Categories: tech, ai, security, engineering, startups."""
    td.start("read_rss_by_name", name)
    n = name.lower().strip()
    if n in BROAD_CATS:
        results = []
        for fn in BROAD_CATS[n]:
            if fn in RSS_SOURCES:
                try: results.append(f"### {fn.upper()}\n{await _fetch_rss(RSS_SOURCES[fn], 4)}")
                except Exception: pass
        merged = "\n\n---\n\n".join(results)
        td.done("read_rss_by_name", f"merged {len(results)} feeds", chars=len(merged))
        return merged or f"No content for '{name}'."
    url = RSS_SOURCES.get(n) or next((v for k, v in RSS_SOURCES.items() if n in k or k in n), None)
    if url:
        c = await _fetch_rss(url, limit)
        td.done("read_rss_by_name", name, chars=len(c)); return c
    td.done("read_rss_by_name", f"unknown: '{name}'", ok=False)
    return f"Unknown: '{name}'. Categories: {', '.join(BROAD_CATS)}. Sources: {', '.join(sorted(RSS_SOURCES))[:200]}..."


# ══════════════════════════════════════════════════════════════════════════
#  SESSION MANAGER
# ══════════════════════════════════════════════════════════════════════════

class Session:
    """
    A single conversation session.
    Stored as ~/.config/open-agent/sessions/<id>.json
    Contains: id, title, created_at, last_active, summary, pairs
    """
    def __init__(self, sid: Optional[str] = None):
        self.id          = sid or uuid.uuid4().hex[:12]
        self.path        = SESSIONS_DIR / f"{self.id}.json"
        self.pairs:      list[tuple[str, str]] = []
        self.summary:    str = ""
        self.title:      str = ""
        self.created_at: str = datetime.now().isoformat()
        self.last_active:str = self.created_at
        if sid and self.path.exists():
            self._load()

    def _load(self):
        try:
            d             = json.loads(self.path.read_text())
            self.pairs    = [(p["u"], p["a"]) for p in d.get("pairs", []) if "u" in p]
            self.summary  = d.get("summary", "")
            self.title    = d.get("title", "")
            self.created_at  = d.get("created_at", self.created_at)
            self.last_active = d.get("last_active", self.last_active)
        except Exception:
            pass

    def save(self):
        self.last_active = datetime.now().isoformat()
        if not self.title and self.pairs:
            # Auto-title from first user message (first 60 chars)
            self.title = self.pairs[0][0][:60].replace("\n", " ").strip()
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

    def delete(self):
        self.path.unlink(missing_ok=True)

    def compress(self) -> str:
        max_pairs = CFG["max_pairs"]
        if len(self.pairs) <= max_pairs: return "nothing to compress"
        old, self.pairs = self.pairs[:-max_pairs], self.pairs[-max_pairs:]
        block    = "\n".join(f"User: {u[:300]}\nAssistant: {a[:500]}" for u, a in old)
        combined = f"{self.summary}\n\n{block}" if self.summary else block
        lines    = [ln.strip() for ln in combined.splitlines() if len(ln.strip()) > 25][:20]
        self.summary = "Prior context:\n" + "\n".join(f"• {ln}" for ln in lines)
        self.save()
        return f"compressed {len(old)} old turns"

    def to_messages(self) -> list[ModelMessage]:
        from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart
        msgs: list[ModelMessage] = []
        if self.summary:
            msgs.append(ModelRequest(parts=[UserPromptPart(content="[Prior context]")]))
            msgs.append(ModelResponse(parts=[TextPart(content=self.summary)]))
        for u, a in self.pairs:
            msgs.append(ModelRequest(parts=[UserPromptPart(content=u)]))
            msgs.append(ModelResponse(parts=[TextPart(content=a)]))
        return msgs

    def add(self, user: str, assistant: str):
        self.pairs.append((user, assistant))
        if len(self.pairs) > CFG["max_pairs"] + 2:
            self.compress()
        self.save()

    @property
    def token_est(self) -> int:
        return (len(self.summary) + sum(len(u)+len(a) for u, a in self.pairs)) // 4

    @staticmethod
    def list_all() -> list[dict]:
        """Return all sessions sorted by last_active descending."""
        sessions = []
        for f in SESSIONS_DIR.glob("*.json"):
            try:
                d = json.loads(f.read_text())
                sessions.append({
                    "id":          d.get("id", f.stem),
                    "title":       d.get("title", "Untitled"),
                    "created_at":  d.get("created_at", ""),
                    "last_active": d.get("last_active", ""),
                    "turns":       len(d.get("pairs", [])),
                })
            except Exception:
                pass
        return sorted(sessions, key=lambda x: x["last_active"], reverse=True)


# ══════════════════════════════════════════════════════════════════════════
#  MULTILINE INPUT  (raw mode + bracketed paste)
# ══════════════════════════════════════════════════════════════════════════

PASTE_START = b"\x1b[200~"
PASTE_END   = b"\x1b[201~"
BP_ON       = "\x1b[?2004h"
BP_OFF      = "\x1b[?2004l"


class InputBuffer:
    """
    Raw-mode terminal input with bracketed paste support.
    Pasted text is cleaned before it reaches the prompt so it stays editable.
    """

    def _redraw(self, prompt: str, buf: list[str]):
        _w(CLEAR_LINE)
        _w(prompt + "".join(buf))

    def readline(self, prompt_prefix: str = "") -> Optional[str]:
        if not sys.stdin.isatty():
            try:
                return input().strip() or None
            except EOFError:
                return None

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        prompt = f"{C_GREEN}{BOLD}You {RESET}{DIM}> {RESET}"

        buf: list[str] = []
        in_paste = False
        accum = bytearray()

        _w(prompt)
        _w(BP_ON)
        sys.stdout.flush()

        try:
            tty.setraw(fd)
            while True:
                if not select.select([sys.stdin], [], [], 0.05)[0]:
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

                        # Keep pasted content clean and editable as a single line.
                        text = re.sub(r"\s+", " ", text).strip()
                        if text:
                            buf.extend(text)
                            _w(text)
                        continue

                    b = accum[0]
                    del accum[0]

                    if b in (13, 10):
                        termios.tcsetattr(fd, termios.TCSADRAIN, old)
                        _w(BP_OFF + "\r\n")
                        return "".join(buf).strip()

                    if b == 4:
                        termios.tcsetattr(fd, termios.TCSADRAIN, old)
                        _w(BP_OFF + "\r\n")
                        return None

                    if b == 3:
                        raise KeyboardInterrupt

                    if b in (127, 8):
                        if buf:
                            buf.pop()
                            self._redraw(prompt, buf)
                        continue

                    if b == 27:
                        # Swallow ANSI escape sequences from arrows/navigation.
                        # Common arrow keys arrive as ESC [ A/B/C/D.
                        if len(accum) >= 2 and accum[0] == 91:
                            del accum[:2]
                        elif accum:
                            del accum[:1]
                        continue

                    if 32 <= b < 127:
                        ch = chr(b)
                        buf.append(ch)
                        _w(ch)

        except KeyboardInterrupt:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            _w(BP_OFF + "\r\n")
            raise
        except Exception:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            _w(BP_OFF + "\r\n")
            raise
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass

_ibuf = InputBuffer()


# ══════════════════════════════════════════════════════════════════════════
#  STREAMING OUTPUT
# ══════════════════════════════════════════════════════════════════════════

_FENCE_RE = re.compile(r"```(?P<lang>\w+)?(?:\s+(?P<file>[^\n]+))?\n(?P<body>.*?)```", re.DOTALL)


def _render_response(text: str):
    """Render the full assistant response in a clean, Claude-style final box."""
    text = _normalize_output(text)
    if not text:
        return

    console.print()
    console.print(Panel(
        Markdown(text),
        title=None,
        border_style="border",
        box=box.ROUNDED,
        padding=(1, 2),
    ))

    blocks = list(_FENCE_RE.finditer(text))
    if not blocks:
        return

    _ln()
    _ln(f"{DIM}── highlighted ──{RESET}")
    for m in blocks:
        lang = m.group("lang") or "text"
        file = m.group("file") or ""
        body = m.group("body").rstrip()
        hdr = Text()
        hdr.append(f" {lang} ", style="tool.name")
        if file:
            hdr.append(f"  {file}", style="tool.info")
        console.print(hdr)
        console.print(Syntax(
            body,
            lang,
            theme="monokai",
            line_numbers=len(body.splitlines()) > 5,
            word_wrap=True,
            background_color="default",
        ))
        _ln()


async def _stream(query: str, history: list[ModelMessage]) -> tuple[str, bool]:
    _w(f"\n{C_CYAN}{BOLD}Agent{RESET}\n")

    full = ""

    try:
        with Live(
            Panel(
                Text("Thinking…", style="dim"),
                border_style="border",
                box=box.ROUNDED,
                padding=(1, 2),
            ),
            console=console,
            transient=True,
            refresh_per_second=18,
        ) as live:
            async with agent.run_stream(query, message_history=history) as result:
                async for chunk in result.stream_text():
                    full = chunk if chunk.startswith(full) else full + chunk
                    live.update(Panel(
                        Text(_normalize_output(full) or "Thinking…", style="dim"),
                        title=Text(" thinking ", style="dim"),
                        border_style="border",
                        box=box.ROUNDED,
                        padding=(1, 2),
                    ))

        return full, True

    except Exception as e:
        _w(f"{C_RED}Stream error: {e}{RESET}\n")
        return full, False


# ══════════════════════════════════════════════════════════════════════════
#  AGENT SESSION RUNNER
# ══════════════════════════════════════════════════════════════════════════

class AgentSession:
    def __init__(self, session: Session):
        self.session = session

    def _augment(self, query: str) -> str:
        q = query.lower()
        hints = []
        if any(t in q for t in GROUNDING_TRIGGERS):
            hints.append("[Grounding required: run date via terminal first, then search.]")
            hints.append("[Execution mode: use grounded facts directly. Do not narrate plans.]")
        if any(t in q for t in SOUL_TRIGGERS):
            hints.append("[Complex task: call load_soul before proceeding.]")
            hints.append("[Execution mode: tool-first, concise, action-oriented.]")
        return query + ("\n\n" + "\n".join(hints) if hints else "")

    async def run(self, query: str) -> Optional[str]:
        _ln(); _w(f"{C_SLATE}{'─' * 60}{RESET}\n")
        sess = self.session
        tok = sess.token_est + len(query) // 4

        if tok > CFG["ctx_compress"]:
            td.info(f"context guard: {sess.compress()}")
        elif tok > CFG["ctx_warn"]:
            td.info(f"context ~{tok:,} tokens — approaching limit")

        ctx_col = C_RED if tok > CFG["ctx_compress"] else (C_AMBER if tok > CFG["ctx_warn"] else C_SLATE)
        _w(f"  {DIM}session {C_PURPLE}{sess.id}{RESET}  "
           f"{ctx_col}~{tok:,}/{CFG['ctx_limit']:,}{RESET} {DIM}tokens{RESET}\n")

        augmented = self._augment(query)
        history = sess.to_messages()

        # Proactive grounding for time-sensitive/current queries.
        grounding_blob = ""
        if any(t in query.lower() for t in GROUNDING_TRIGGERS):
            td.info("execution mode: grounding tools")
            try:
                date_output = await run_terminal(None, "date && uname -r")
                search_output = await web_search(None, query)
                grounding_blob = (
                    "[Grounded snapshot]\n"
                    f"{date_output}\n\n"
                    "[Search snapshot]\n"
                    f"{search_output}"
                )
                augmented = (
                    f"{augmented}\n\n"
                    f"{grounding_blob}\n\n"
                    "Use the grounded snapshot above directly. Do not narrate what you will do."
                )
            except Exception as e:
                td.info(f"grounding bootstrap failed: {e}")

        # Complex tasks can load the extended behavioral layer before the model thinks.
        if any(t in query.lower() for t in SOUL_TRIGGERS):
            try:
                td.info("loading extended instructions")
                soul_text = await load_soul(None)
                augmented = (
                    f"{augmented}\n\n"
                    "[Extended instructions]\n"
                    f"{soul_text}\n\n"
                    "Follow the execution rules above and answer directly."
                )
            except Exception as e:
                td.info(f"soul bootstrap failed: {e}")

        for attempt in range(3):
            full, ok = await _stream(augmented, history)

            if ok and full:
                full = _normalize_output(full)

                # Fallback: if the model starts planning, strengthen the prompt and rerun once.
                if _looks_like_planning(full):
                    td.info("model is planning — forcing execution")
                    try:
                        forced_augmented = (
                            "EXECUTION MODE ACTIVE. "
                            "Do not explain what you will do. Answer directly using the grounded information and tools."
                        )
                        if grounding_blob:
                            forced_augmented += f"\n\n{grounding_blob}"
                        forced_augmented += f"\n\nUser request: {query}"
                        full, ok = await _stream(forced_augmented, history)
                        full = _normalize_output(full)
                    except Exception as e:
                        td.info(f"forced execution failed: {e}")

                if ok and full:
                    sess.add(query, full)
                    _render_response(full)
                    return full

            if not ok and attempt < 2:
                _w(f"\n{DIM}Retry {attempt+1}/3…{RESET}\n")
                await asyncio.sleep(1.5)

        _w(f"{C_RED}✗ Failed after 3 attempts.{RESET}")
        return None


# ══════════════════════════════════════════════════════════════════════════
#  FIRST-RUN ONBOARDING WIZARD
# ══════════════════════════════════════════════════════════════════════════

def _ask(prompt: str, default: str = "") -> str:
    """Simple interactive prompt with a default value."""
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
        if not val: return default
        return val in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return default

def run_setup_wizard(cfg: dict, first_run: bool = True) -> dict:
    """Interactive first-run configuration wizard."""
    _ln()
    if first_run:
        console.print(Panel(
            f"[header]Welcome to Open-Agent![/header]\n\n"
            f"[dim]Let's get you set up in about 60 seconds.\n"
            f"Press Enter to keep defaults. You can change anything later with [/dim][tool.name]/setup[/tool.name]",
            box=box.ROUNDED, border_style="border", padding=(1, 3),
        ))
    else:
        console.print(Panel(
            "[header]Open-Agent Setup[/header]\n[dim]Reconfigure your settings.[/dim]",
            box=box.ROUNDED, border_style="border", padding=(1, 2),
        ))

    _ln()

    # ── Step 1: Inference server ──────────────────────────────────────────
    _w(f"\n{C_AMBER}{BOLD}[1/3] Inference Server{RESET}\n")
    _w(f"  {DIM}Open-Agent connects to any OpenAI-compatible API.\n")
    _w(f"  Default: llama.cpp on localhost:8083{RESET}\n\n")

    base_url = _ask("Server URL", cfg.get("base_url", DEFAULT_CONFIG["base_url"]))
    model_name = _ask("Model name", cfg.get("model_name", DEFAULT_CONFIG["model_name"]))

    # Quick connectivity check
    _w(f"\n  {DIM}Checking connection…{RESET}")
    try:
        import urllib.request
        urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=3)
        _w(f"  {C_GREEN}✓ Connected{RESET}\n")
    except Exception:
        _w(f"  {C_AMBER}⚠ Could not reach server (make sure llama.cpp is running){RESET}\n")

    # ── Step 2: Web search ────────────────────────────────────────────────
    _w(f"\n{C_AMBER}{BOLD}[2/3] Web Search (SearxNG){RESET}\n")
    _w(f"  {DIM}SearxNG is a private, self-hosted search engine.\n")
    _w(f"  Run it with: docker run -d -p 8081:8080 searxng/searxng{RESET}\n\n")

    searxng_url = _ask("SearxNG URL", cfg.get("searxng_url", DEFAULT_CONFIG["searxng_url"]))

    # ── Step 3: Obsidian (optional) ───────────────────────────────────────
    _w(f"\n{C_AMBER}{BOLD}[3/3] Obsidian Vault (optional){RESET}\n")
    _w(f"  {DIM}Connect your Obsidian vault so the agent can read and write your notes.{RESET}\n\n")

    use_obsidian = _yn("Connect Obsidian vault?", default=bool(cfg.get("obsidian_path")))
    obsidian_path = ""
    if use_obsidian:
        obsidian_path = _ask("Vault path", cfg.get("obsidian_path", str(Path.home() / "Documents" / "Obsidian")))
        if obsidian_path and not Path(obsidian_path).exists():
            _w(f"  {C_AMBER}⚠ Path doesn't exist yet — you can create it later{RESET}\n")
        elif obsidian_path:
            _w(f"  {C_GREEN}✓ Vault found{RESET}\n")

    # ── Save ──────────────────────────────────────────────────────────────
    cfg.update({
        "base_url":     base_url,
        "model_name":   model_name,
        "searxng_url":  searxng_url,
        "obsidian_path":obsidian_path,
        "setup_done":   True,
    })
    save_config(cfg)

    _ln()
    console.print(Panel(
        f"[tool.ok]✓ Setup complete![/tool.ok]\n\n"
        f"[dim]Config saved to[/dim] [accent]{CONFIG_FILE}[/accent]\n"
        f"[dim]Run [/dim][tool.name]/setup[/tool.name][dim] at any time to change settings.\n"
        f"[dim]Type[/dim] [tool.name]/help[/tool.name] [dim]to see all commands.[/dim]",
        box=box.ROUNDED, border_style="border", padding=(1, 3),
    ))
    _ln()
    return cfg


# ══════════════════════════════════════════════════════════════════════════
#  BANNER  (centered, animated-style)
# ══════════════════════════════════════════════════════════════════════════

LOGO_FRAMES = [
    # Frame 1 (static — terminals that don't support animation see this)
    r"""
   ██████╗ ██████╗ ███████╗███╗   ██╗       █████╗  ██████╗ ███████╗███╗   ██╗████████╗
  ██╔═══██╗██╔══██╗██╔════╝████╗  ██║      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
  ██║   ██║██████╔╝█████╗  ██╔██╗ ██║█████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║
  ██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║
  ╚██████╔╝██║     ███████╗██║ ╚████║      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║
   ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝
""",
]

# Compact logo for narrow terminals
LOGO_COMPACT = """
  ┌─────────────────────────────────────┐
  │         O P E N - A G E N T         │
  │   local AI · private · consumer hw  │
  └─────────────────────────────────────┘
"""

def _terminal_width() -> int:
    try: return shutil.get_terminal_size().columns
    except Exception: return 80

def print_banner(session_id: str):
    width = _terminal_width()
    ts    = datetime.now().strftime("%a %b %d  %H:%M")

    _ln()
    if width >= 100:
        # Full ASCII art logo, centred
        for line in LOGO_FRAMES[0].splitlines():
            padding = max(0, (width - len(line)) // 2)
            _w(f"{C_CYAN}{' ' * padding}{line}{RESET}\n")
    else:
        # Compact logo
        for line in LOGO_COMPACT.splitlines():
            padding = max(0, (width - len(line)) // 2)
            _w(f"{C_CYAN}{' ' * padding}{line}{RESET}\n")

    # Tagline — centred
    tagline = "Run capable AI on any laptop. Private. Free. Yours."
    pad = max(0, (width - len(tagline)) // 2)
    _w(f"\n{' ' * pad}{DIM}{tagline}{RESET}\n\n")

    # Info bar — centred panel via Rich
    info = (
        f"[dim][/dim] [tool.name]{session_id}[/tool.name]\n\n"
        f"[dim][/dim][tool.name]/help[/tool.name] [dim]for commands · [/dim]"
        f"[tool.name]/sessions[/tool.name] [dim]for history · [/dim][tool.name]/exit[/tool.name] [dim]to quit[/dim]"
    )
    console.print(Align.center(Panel(info, box=box.ROUNDED, border_style="border", padding=(0, 3))))
    _ln()


# ══════════════════════════════════════════════════════════════════════════
#  SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════

def _help_table() -> Table:
    tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 2), header_style="header")
    tbl.add_column("Command",     style="tool.name", no_wrap=True)
    tbl.add_column("Description", style="dim")
    tbl.add_column("Alias",       style="dim")
    for cmd, desc, alias in [
        ("/help",     "Show this help",              "/h"),
        ("/sessions", "Browse and resume old chats", "/s"),
        ("/new",      "Start a fresh session",       ""),
        ("/history",  "Token count + session info",  "/hi"),
        ("/compress", "Force compress history",       ""),
        ("/clear",    "Wipe current session",         "/cl"),
        ("/soul",     "Show SOUL.md",                 ""),
        ("/setup",    "Reconfigure settings",         ""),
        ("/model",    "Show model + server config",   "/m"),
        ("/sources",  "List RSS sources",             ""),
        ("/save",     "Force save current session",   ""),
        ("/exit",     "Save and quit",                "/q"),
    ]:
        tbl.add_row(cmd, desc, alias)
    return tbl


def _sessions_panel(runner: "AgentRunner") -> bool:
    """Show session browser and allow resuming an old session."""
    sessions = Session.list_all()
    if not sessions:
        console.print("[dim]No sessions found.[/dim]")
        return True

    tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 2), header_style="header")
    tbl.add_column("#",           style="dim", no_wrap=True)
    tbl.add_column("Session ID",  style="tool.name", no_wrap=True)
    tbl.add_column("Title",       style="dim")
    tbl.add_column("Turns",       style="dim", no_wrap=True)
    tbl.add_column("Last active", style="dim", no_wrap=True)

    for i, s in enumerate(sessions[:15], 1):
        try:
            la = datetime.fromisoformat(s["last_active"]).strftime("%b %d %H:%M")
        except Exception:
            la = s["last_active"][:16]
        current = " ← current" if s["id"] == runner.session.id else ""
        tbl.add_row(
            str(i),
            s["id"] + current,
            s["title"][:45] or "(no title)",
            str(s["turns"]),
            la,
        )

    console.print(Panel(tbl, title="[header]Sessions[/header]",
                        border_style="border", box=box.ROUNDED))
    _w(f"\n  {DIM}Enter number to resume, or press Enter to stay in current session:{RESET} ")
    try:
        val = input().strip()
        if val.isdigit():
            idx = int(val) - 1
            if 0 <= idx < len(sessions):
                sid = sessions[idx]["id"]
                if sid != runner.session.id:
                    runner.session.save()
                    runner.session = Session(sid)
                    _w(f"  {C_GREEN}✓ Resumed session {sid}{RESET}\n")
                    td.info(f"loaded {len(runner.session.pairs)} turns")
                else:
                    _w(f"  {DIM}Already in that session.{RESET}\n")
    except (EOFError, KeyboardInterrupt):
        pass
    return True


async def handle_slash(cmd: str, runner: "AgentRunner") -> bool:
    """Handle slash commands (/help, /setup, /exit, etc.)."""
    global CFG   # Needed because we reassign CFG in /setup

    parts = cmd.strip().split()
    c = parts[0].lower()

    if c in ("/exit", "/quit", "/q"):
        runner.session.save()
        console.print(Panel("[dim]Session saved. Goodbye.[/dim]", border_style="border"))
        sys.exit(0)

    if c in ("/help", "/h"):
        console.print(Panel(_help_table(), title="[header]Open-Agent[/header]",
                            border_style="border", box=box.ROUNDED))
        return True

    if c in ("/sessions", "/s"):
        return _sessions_panel(runner)

    if c == "/new":
        runner.session.save()
        runner.session = Session()
        _w(f" {C_GREEN}✓ New session started: {runner.session.id}{RESET}\n")
        return True

    if c in ("/clear", "/cl"):
        runner.session.pairs.clear()
        runner.session.summary = ""
        runner.session.save()
        _w(f" {C_GREEN}✓ Session cleared.{RESET}\n")
        return True

    if c == "/compress":
        msg = runner.session.compress()
        _w(f" {C_GREEN}✓ {msg}{RESET}\n")
        return True

    if c in ("/history", "/hi"):
        s = runner.session
        tok = s.token_est
        tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        tbl.add_column(style="tool.name")
        tbl.add_column(style="dim")
        tbl.add_row("Session ID", s.id)
        tbl.add_row("Title", s.title or "(none yet)")
        tbl.add_row("Verbatim turns", str(len(s.pairs)))
        tbl.add_row("Summary", "yes" if s.summary else "no")
        tbl.add_row("Token estimate", f"~{tok:,}")
        tbl.add_row("Summary size", f"{len(s.summary):,} chars")
        tbl.add_row("Context used", f"{tok / CFG['ctx_limit'] * 100:.1f}%")
        tbl.add_row("Context limit", f"{CFG['ctx_limit']:,}")
        console.print(Panel(tbl, title="[header]History[/header]",
                            border_style="border", box=box.ROUNDED))
        return True

    if c == "/soul":
        if SOUL_FILE.exists():
            console.print(Panel(Markdown(SOUL_FILE.read_text()),
                                title="[header]SOUL.md[/header]",
                                border_style="border", box=box.ROUNDED, padding=(1, 2)))
        else:
            _w(f" {C_RED}SOUL.md not found{RESET}\n")
        return True

    if c == "/setup":
        CFG = run_setup_wizard(CFG, first_run=False)
        return True

    if c in ("/model", "/m"):
        tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        tbl.add_column(style="tool.name")
        tbl.add_column(style="accent")
        for k, v in [("Model", CFG["model_name"]), ("Server", CFG["base_url"]),
                     ("Search", CFG["searxng_url"]), ("Context", f"{CFG['ctx_limit']:,} tokens"),
                     ("KV cache", "Q4_0 (K+V)"), ("Flash attn", "on")]:
            tbl.add_row(k, v)
        console.print(Panel(tbl, title="[header]Config[/header]",
                            border_style="border", box=box.ROUNDED))
        return True

    if c == "/sources":
        names = sorted(RSS_SOURCES.keys())
        cols = 4
        chunks = [names[i:i+cols] for i in range(0, len(names), cols)]
        tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        for _ in range(cols):
            tbl.add_column(style="tool.name")
        for chunk in chunks:
            tbl.add_row(*chunk + [""] * (cols - len(chunk)))
        cats = " · ".join(sorted(BROAD_CATS))
        console.print(Panel(tbl, title="[header]RSS Sources[/header]",
                            subtitle=f"[dim]categories: {cats}[/dim]",
                            border_style="border", box=box.ROUNDED))
        return True

    if c == "/save":
        runner.session.save()
        _w(f" {C_GREEN}✓ Saved session {runner.session.id}{RESET}\n")
        return True

    return False

# ══════════════════════════════════════════════════════════════════════════
#  RUNNER  (ties it all together)
# ══════════════════════════════════════════════════════════════════════════

class AgentRunner:
    def __init__(self, session: Session):
        self.session    = session
        self._agent_run = AgentSession(session)

    @property
    def _agent(self) -> AgentSession:
        # Always returns an AgentSession bound to the current session
        if self._agent_run.session is not self.session:
            self._agent_run = AgentSession(self.session)
        return self._agent_run


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

async def _main_loop(runner: AgentRunner):
    while True:
        try:
            _ln()
            user_input = _ibuf.readline()
            if user_input is None:
                runner.session.save(); break
            if not user_input.strip(): continue
            if user_input.startswith("/"):
                if await handle_slash(user_input, runner): continue
            await runner._agent.run(user_input)
        except KeyboardInterrupt:
            _w(f"\n{DIM}Ctrl-C — saving…{RESET}\n")
            runner.session.save()
            _w(f"{DIM}Goodbye.{RESET}\n"); break
        except EOFError:
            runner.session.save(); break
        except Exception as e:
            _w(f"{C_RED}Error: {e}{RESET}\n")


def main():
    global CFG
    parser = argparse.ArgumentParser(
        prog="opagent",
        description="Open-Agent — local AI agent for consumer hardware",
    )
    parser.add_argument("--setup",   action="store_true", help="Run setup wizard")
    parser.add_argument("--version", action="store_true", help="Print version")
    parser.add_argument("--new",     action="store_true", help="Start a new session")
    parser.add_argument("--resume",  metavar="SESSION_ID", help="Resume a specific session")
    args = parser.parse_args()

    if args.version:
        print("open-agent 5.0.0"); sys.exit(0)

    # First-run onboarding
    if not CFG.get("setup_done") or args.setup:
        CFG = run_setup_wizard(CFG, first_run=not CFG.get("setup_done"))

    # Session selection
    if args.resume:
        session = Session(args.resume)
        if not session.path.exists():
            _w(f"{C_RED}Session '{args.resume}' not found.{RESET}\n"); sys.exit(1)
    elif args.new:
        session = Session()
    else:
        # Resume most recent session if it exists, else create new
        all_sessions = Session.list_all()
        if all_sessions and not args.new:
            session = Session(all_sessions[0]["id"])
        else:
            session = Session()

    runner = AgentRunner(session)
    print_banner(session.id)
    if session.pairs:
        td.info(f"resumed session {session.id} — {len(session.pairs)} turns")

    asyncio.run(_main_loop(runner))


def main_sync():
    """Entry point for console_scripts."""
    main()


if __name__ == "__main__":
    main()
