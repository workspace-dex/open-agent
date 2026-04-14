#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  OPEN-AGENT                                                      ║
║  Engineered for consumer hardware · PydanticAI · SearxNG         ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import feedparser
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

import httpx
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from rich import box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


# ─────────────────────────── THEME & CONSOLE ──────────────────────────────
THEME = Theme({
    "agent":     "bold #7DCFFF",
    "user":      "bold #9ECE6A",
    "tool":      "bold #E0AF68",
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


# ──────────────────────────── CONFIG ──────────────────────────────────────
BASE_URL      = "http://localhost:8083/v1"
MODEL_NAME    = "Gemma 4 (recommended)"
OBSIDIAN_VAULT_PATH = Path("/home/dex/Work/dex")
SEARXNG_URL   = "http://localhost:8081/search"
HISTORY_FILE  = Path("agent_history.json")
SOUL_FILE     = Path("SOUL.md")
MAX_PAIRS     = 3          # Full turns kept verbatim in history
CTX_WARN      = 40_000     # Warn threshold (tokens)
CTX_COMPRESS  = 48_000     # Hard compress threshold

# Queries that suggest the agent needs its extended SOUL instructions
SOUL_TRIGGERS = {
    "research", "plan", "how should i", "step by step", "steps to",
    "compare", "analyze", "analyse", "strategy", "deep dive",
    "explain in detail", "walk me through", "pros and cons",
    "investigate", "outline", "draft", "write a", "help me build",
}

# Queries that suggest real-world grounding is needed
GROUNDING_TRIGGERS = {
    "today", "now", "current", "latest", "recent", "who is", "what is",
    "price", "weather", "news", "just", "2024", "2025", "this year",
    "this week", "yesterday", "released", "announced",
}

provider = OpenAIProvider(base_url=BASE_URL, api_key="lm-studio")
model    = OpenAIChatModel(model_name=MODEL_NAME, provider=provider)


# ──────────────────────────── SYSTEM PROMPT ───────────────────────────────
# Intentionally minimal — SOUL.md carries the deep behavioral layer.
SYSTEM_PROMPT = """You are a fast, grounded local AI agent.

RITUALS — before any real-world or time-sensitive query:
1. run_terminal("date && echo '---' && uname -r") — anchor current time/system
2. web_search if your knowledge might be stale (default: assume it is for anything < 2 years old)
3. Confirm important facts across ≥2 search results before asserting

TOOLS: web_search · smart_research · fetch_page · read_rss_by_name · run_terminal · read_file · write_file · load_soul · read_obsidian_note · write_obsidian_note

When asked about personal notes, projects, or ideas, aggressively use the Obsidian tools to search and read before answering.
Be direct. Prefer Markdown. No filler phrases."""


agent = Agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    deps_type=None,
    model_settings={
        "temperature":        0.5,
        "max_tokens":         2400,
        "top_p":              0.9,
        "frequency_penalty":  0.35,
        "presence_penalty":   0.15,
    },
    retries=2,
)


# ─────────────────────────── UI HELPERS ───────────────────────────────────
def _tool_panel(name: str, detail: str, status: str = "running") -> Panel:
    colour = {"running": "tool", "ok": "tool.ok", "err": "tool.err"}.get(status, "tool")
    icon   = {"running": "⟳", "ok": "✓", "err": "✗"}.get(status, "·")
    title  = Text(f"{icon} {name}", style="tool.name")
    return Panel(
        Text(detail[:120], style="tool.info"),
        title=title, title_align="left",
        border_style="border", box=box.ROUNDED, padding=(0, 1),
    )

def _print_tool_call(name: str, detail: str):
    console.print(_tool_panel(name, detail, "running"))

def _print_tool_result(name: str, summary: str, ok: bool = True):
    console.print(_tool_panel(name, summary, "ok" if ok else "err"))

def _est_tokens(text: str) -> int:
    return max(1, len(text) // 4)

def _safe_text(text: str) -> str:
    return text.encode("utf-8")[-6000:].decode("utf-8", errors="ignore")


# ─────────────────────────── TOOLS ────────────────────────────────────────

@agent.tool
async def web_search(ctx: RunContext, query: str) -> str:
    """Search the web via SearxNG. Use for current facts, news, or anything potentially stale."""
    _print_tool_call("web_search", query)
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(
                SEARXNG_URL,
                params={"q": query, "format": "json", "categories": "general", "language": "en"},
            )
            r.raise_for_status()
        results = r.json().get("results", [])[:7]
        if not results:
            _print_tool_result("web_search", "no results", ok=False)
            return "No results found."
        out = [
            f"[{i}] {res.get('title','')}\nURL: {res.get('url','')}\n{res.get('content','')[:400]}"
            for i, res in enumerate(results, 1)
        ]
        _print_tool_result("web_search", f"{len(results)} results for: {query}")
        return "\n\n".join(out)
    except Exception as e:
        _print_tool_result("web_search", str(e), ok=False)
        return f"Search error: {e}"


@agent.tool
async def smart_research(ctx: RunContext, queries: list[str]) -> str:
    """Run up to 4 search queries in parallel and merge results. Use for multi-angle research."""
    queries = queries[:4]
    _print_tool_call("smart_research", f"{len(queries)} queries: {' | '.join(queries)}")

    async def _single(q: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                r = await client.get(
                    SEARXNG_URL,
                    params={"q": q, "format": "json", "categories": "general", "language": "en"},
                )
                r.raise_for_status()
            res = r.json().get("results", [])[:4]
            if not res:
                return f"### {q}\nNo results.\n"
            lines = [f"### {q}"] + [
                f"**{item.get('title','')}**\n{item.get('url','')}\n{item.get('content','')[:350]}"
                for item in res
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"### {q}\nError: {e}\n"

    results = await asyncio.gather(*[_single(q) for q in queries])
    merged  = "\n\n---\n\n".join(results)
    _print_tool_result("smart_research", f"Merged {len(queries)} result sets")
    return merged


@agent.tool
async def fetch_page(ctx: RunContext, url: str) -> str:
    """Fetch and extract text from a URL. Use when you need full page content."""
    _print_tool_call("fetch_page", url)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s{3,}", "\n\n", text).strip()[:4000]
        _print_tool_result("fetch_page", f"{len(text)} chars from {url[:60]}")
        return text
    except Exception as e:
        _print_tool_result("fetch_page", str(e), ok=False)
        return f"Fetch error: {e}"


@agent.tool
async def run_terminal(ctx: RunContext, command: str) -> str:
    """Run a safe shell command. Use for date, file ops, system info, etc."""
    _print_tool_call("run_terminal", command)
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        out     = proc.stdout.strip()
        err     = proc.stderr.strip()
        summary = (out or err or "no output")[:80]
        ok      = proc.returncode == 0
        _print_tool_result("run_terminal", summary, ok=ok)
        return f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}\nCode: {proc.returncode}"
    except Exception as e:
        _print_tool_result("run_terminal", str(e), ok=False)
        return f"Terminal error: {e}"


@agent.tool
async def read_file(ctx: RunContext, path: str) -> str:
    """Read a local file. Returns up to 8000 chars."""
    _print_tool_call("read_file", path)
    try:
        content = Path(path).read_text(encoding="utf-8")[:8000]
        _print_tool_result("read_file", f"{len(content)} chars")
        return content
    except Exception as e:
        _print_tool_result("read_file", str(e), ok=False)
        return f"Read error: {e}"


@agent.tool
async def write_file(ctx: RunContext, path: str, content: str) -> str:
    """Write content to a local file."""
    _print_tool_call("write_file", f"{path} ({len(content)} chars)")
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _print_tool_result("write_file", f"Written → {path}")
        return f"✓ Written to {path}"
    except Exception as e:
        _print_tool_result("write_file", str(e), ok=False)
        return f"Write error: {e}"


@agent.tool
async def load_soul(ctx: RunContext) -> str:
    """Load extended behavioral instructions from SOUL.md. Call for complex, multi-step, or research tasks."""
    _print_tool_call("load_soul", "Loading SOUL.md")
    try:
        content = SOUL_FILE.read_text(encoding="utf-8")
        _print_tool_result("load_soul", f"{len(content)} chars loaded")
        return content
    except FileNotFoundError:
        _print_tool_result("load_soul", "SOUL.md not found", ok=False)
        return "SOUL.md not found. Place it alongside agent.py."
    except Exception as e:
        _print_tool_result("load_soul", str(e), ok=False)
        return f"SOUL load error: {e}"

@agent.tool
async def search_obsidian(ctx: RunContext, query: str) -> str:
    """Search your Obsidian vault for notes containing specific text."""
    _print_tool_call("search_obsidian", query)
    try:
        if not OBSIDIAN_VAULT_PATH.exists():
            return f"Vault path {OBSIDIAN_VAULT_PATH} does not exist."

        results = []
        # Case-insensitive search across all markdown files
        for p in OBSIDIAN_VAULT_PATH.rglob("*.md"):
            if any(part.startswith('.') for part in p.parts): # Skip .obsidian, .trash, etc.
                continue
            
            content = p.read_text(encoding="utf-8", errors="ignore")
            if query.lower() in content.lower():
                # Provide the relative path so the LLM knows the exact note name
                results.append(f"- {p.relative_to(OBSIDIAN_VAULT_PATH)}")

        if not results:
            _print_tool_result("search_obsidian", "No matches found", ok=False)
            return "No matching notes found."

        summary = f"Found {len(results)} matches"
        _print_tool_result("search_obsidian", summary)
        
        # Limit to prevent context blowout
        return f"Matching notes:\n" + "\n".join(results[:25]) 
    except Exception as e:
        _print_tool_result("search_obsidian", str(e), ok=False)
        return f"Search error: {e}"


@agent.tool
async def read_obsidian_note(ctx: RunContext, note_name: str) -> str:
    """Read a specific note from your Obsidian vault. Provide the name with or without .md."""
    if not note_name.endswith(".md"):
        note_name += ".md"
    
    _print_tool_call("read_obsidian_note", note_name)
    try:
        # Recursively find the note to handle sub-folders gracefully
        matches = list(OBSIDIAN_VAULT_PATH.rglob(note_name))
        
        # Filter out hidden folders
        matches = [m for m in matches if not any(part.startswith('.') for part in m.parts)]

        if not matches:
            _print_tool_result("read_obsidian_note", "Not found", ok=False)
            return f"Note '{note_name}' not found in vault."

        target = matches[0]
        content = target.read_text(encoding="utf-8")[:10000] # Safe cap
        _print_tool_result("read_obsidian_note", f"{len(content)} chars read")
        return content
    except Exception as e:
        _print_tool_result("read_obsidian_note", str(e), ok=False)
        return f"Read error: {e}"


@agent.tool
async def write_obsidian_note(ctx: RunContext, note_name: str, content: str, append: bool = True) -> str:
    """Create or append to an Obsidian note. Useful for logging ideas or drafting."""
    if not note_name.endswith(".md"):
        note_name += ".md"
        
    _print_tool_call("write_obsidian_note", f"{note_name} (append={append})")
    try:
        # Check if it exists anywhere in the vault to append
        matches = list(OBSIDIAN_VAULT_PATH.rglob(note_name))
        matches = [m for m in matches if not any(part.startswith('.') for part in m.parts)]
        
        # If it exists, use that path. If not, create it at the vault root.
        target = matches[0] if matches else OBSIDIAN_VAULT_PATH / note_name

        mode = "a" if append and target.exists() else "w"
        prefix = "\n\n" if mode == "a" else ""

        with open(target, mode, encoding="utf-8") as f:
            f.write(prefix + content)

        action = "Appended to" if mode == "a" else "Created"
        _print_tool_result("write_obsidian_note", f"{action} {note_name}")
        return f"✓ {action} {target.relative_to(OBSIDIAN_VAULT_PATH)}"
    except Exception as e:
        _print_tool_result("write_obsidian_note", str(e), ok=False)
        return f"Write error: {e}"

# ─────────────────── RSS SOURCES & TOOL ───────────────────────────────────
RSS_SOURCES: dict[str, str] = {
    # Privacy & Security
    "eff":        "https://www.eff.org/rss/updates.xml",
    "schneier":   "https://www.schneier.com/feed/atom/",
    "krebs":      "https://krebsonsecurity.com/feed/",
    "bleeping":   "https://www.bleepingcomputer.com/feed/",
    # Tech News
    "techcrunch": "https://techcrunch.com/feed/",
    "verge":      "http://www.theverge.com/rss/full.xml",
    "engadget":   "http://www.engadget.com/rss-full.xml",
    "venturebeat":"http://venturebeat.com/feed/",
    "ars":        "https://arstechnica.com/feed/",
    # Hacker News
    "hn":         "https://news.ycombinator.com/rss",
    "hn_show":    "http://hnrss.org/show",
    "hn_launches":"https://hnrss.org/launches",
    # Engineering
    "pragmatic":  "https://blog.pragmaticengineer.com/rss/",
    "cloudflare": "https://blog.cloudflare.com/rss/",
    "stripe":     "https://stripe.com/blog/feed.rss",
    "meta_eng":   "https://engineering.fb.com/feed/",
    "julia_evans":"https://jvns.ca/atom.xml",
    "danluu":     "https://danluu.com/atom.xml",
    # AI / Research
    "arxiv_ai":   "https://rss.arxiv.org/rss/cs.AI",
    "arxiv_cl":   "https://rss.arxiv.org/rss/cs.CL",
    "huggingface":"https://huggingface.co/blog/feed.xml",
    # Ideas
    "farnam":     "https://fs.blog/feed/",
    "producthunt":"http://www.producthunt.com/feed",
}

BROAD_CATEGORIES: dict[str, list[str]] = {
    "tech":        ["techcrunch", "verge", "ars", "venturebeat", "hn", "engadget"],
    "technology":  ["techcrunch", "verge", "ars", "venturebeat", "hn", "engadget"],
    "news":        ["techcrunch", "verge", "ars", "venturebeat", "hn"],
    "startups":    ["techcrunch", "hn_launches", "producthunt", "venturebeat"],
    "engineering": ["pragmatic", "cloudflare", "stripe", "meta_eng", "julia_evans"],
    "ai":          ["arxiv_ai", "arxiv_cl", "huggingface"],
    "security":    ["eff", "schneier", "krebs", "bleeping"],
}


async def _fetch_rss(url: str, limit: int = 10) -> str:
    limit = min(max(limit, 1), 20)
    feed  = feedparser.parse(url)
    if feed.bozo:
        return f"RSS parse error: {feed.bozo_exception}"
    if not feed.entries:
        return f"No entries in feed: {url}"
    out = [
        f"**Feed:** {feed.feed.get('title', 'Untitled')}\n"
        f"**Link:** {feed.feed.get('link', url)}\n"
    ]
    for i, entry in enumerate(feed.entries[:limit], 1):
        out.append(
            f"{i}. **{entry.get('title', 'No title')}**\n"
            f"   {entry.get('published', entry.get('updated', 'No date'))}\n"
            f"   {entry.get('link', '')}\n"
            f"   {entry.get('summary', entry.get('description', ''))[:300].strip()}\n"
        )
    return "\n".join(out)


@agent.tool
async def read_rss_by_name(ctx: RunContext, name: str, limit: int = 8) -> str:
    """
    Fetch RSS from curated sources. Accepts short names (hn, verge, arxiv_ai)
    or broad categories (tech, ai, engineering, security, startups, news).
    """
    _print_tool_call("read_rss_by_name", f"{name} (limit: {limit})")
    name_lower = name.lower().strip()
    limit      = min(max(limit, 1), 15)

    if name_lower in BROAD_CATEGORIES:
        feeds   = BROAD_CATEGORIES[name_lower]
        results = []
        for fn in feeds:
            if fn in RSS_SOURCES:
                try:
                    content = await _fetch_rss(RSS_SOURCES[fn], limit=5)
                    results.append(f"### {fn.upper()}\n{content}")
                except Exception:
                    pass
        merged = "\n\n---\n\n".join(results)
        _print_tool_result("read_rss_by_name", f"Merged {len(results)} feeds for '{name}'")
        return merged or f"No content from '{name}' feeds."

    url = RSS_SOURCES.get(name_lower)
    if not url:
        for key, val in RSS_SOURCES.items():
            if name_lower in key or key in name_lower:
                url = val
                break
    if url:
        content = await _fetch_rss(url, limit)
        _print_tool_result("read_rss_by_name", f"Fetched {name}")
        return content

    available = ", ".join(sorted(RSS_SOURCES.keys()))
    _print_tool_result("read_rss_by_name", f"Unknown source '{name}'", ok=False)
    return (
        f"Unknown source '{name}'.\n"
        f"Categories: {', '.join(BROAD_CATEGORIES)}\n"
        f"Sources: {available[:300]}..."
    )


# ─────────────────────────── HISTORY ──────────────────────────────────────
class HistoryManager:
    """
    Two-tier history:
      Tier 1 — last MAX_PAIRS full user+assistant exchanges (verbatim, in context)
      Tier 2 — older turns condensed into a rolling summary string
    Storage: single JSON file, minimal schema.
    """

    def __init__(self):
        self.pairs:   list[tuple[str, str]] = []  # (user, assistant) tuples
        self.summary: str = ""                     # Rolling summary of older pairs
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────

    def _load(self):
        if not HISTORY_FILE.exists():
            return
        try:
            data          = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            self.summary  = data.get("summary", "")
            raw_pairs     = data.get("pairs", [])
            self.pairs    = [(p["u"], p["a"]) for p in raw_pairs if "u" in p and "a" in p]
            console.print(
                f"[dim]↑ Loaded {len(self.pairs)} turns"
                + (f" + summary ({len(self.summary)} chars)" if self.summary else "")
                + "[/dim]"
            )
        except Exception:
            pass  # Corrupt file — start fresh silently

    def save(self):
        try:
            data = {
                "summary": self.summary,
                "pairs":   [{"u": u, "a": a} for u, a in self.pairs],
            }
            HISTORY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def clear(self):
        self.pairs   = []
        self.summary = ""
        self.save()

    # ── Rolling compression ────────────────────────────────────────────────

    def compress(self) -> str:
        """Condense pairs beyond MAX_PAIRS into a rolling summary."""
        if len(self.pairs) <= MAX_PAIRS:
            return "Nothing to compress."

        old_pairs      = self.pairs[:-MAX_PAIRS]
        self.pairs     = self.pairs[-MAX_PAIRS:]
        old_text       = "\n".join(
            f"User: {u[:300]}\nAssistant: {a[:500]}" for u, a in old_pairs
        )

        # Build a tight summary — key facts + decisions only
        if self.summary:
            combined = f"Previous summary:\n{self.summary}\n\nNew turns:\n{old_text}"
        else:
            combined = old_text

        # Heuristic summariser (no LLM call — keeps it fast & cheap)
        lines       = [ln.strip() for ln in combined.splitlines() if ln.strip()]
        bullet_lines = [ln for ln in lines if len(ln) > 30][:18]
        self.summary = "Session context (summarised):\n" + "\n".join(f"- {ln}" for ln in bullet_lines)

        self.save()
        return f"Compressed {len(old_pairs)} old turns into summary."

    # ── PydanticAI message list ────────────────────────────────────────────

    def to_messages(self) -> list[ModelMessage]:
        """Convert stored history to PydanticAI message format."""
        from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart

        messages: list[ModelMessage] = []

        # Prepend summary as a synthetic user→assistant exchange
        if self.summary:
            messages.append(ModelRequest(parts=[UserPromptPart(content="[Session context]")]))
            messages.append(ModelResponse(parts=[TextPart(content=self.summary)]))

        for user_text, assistant_text in self.pairs:
            messages.append(ModelRequest(parts=[UserPromptPart(content=user_text)]))
            messages.append(ModelResponse(parts=[TextPart(content=assistant_text)]))

        return messages

    def add(self, user_text: str, assistant_text: str):
        self.pairs.append((user_text, assistant_text))
        if len(self.pairs) > MAX_PAIRS + 2:
            self.compress()
        self.save()

    @property
    def token_estimate(self) -> int:
        total = len(self.summary)
        for u, a in self.pairs:
            total += len(u) + len(a)
        return total // 4

    @property
    def turn_count(self) -> int:
        return len(self.pairs)


# ─────────────────────────── SESSION ──────────────────────────────────────
class AgentSession:

    def __init__(self):
        self.history = HistoryManager()

    def _needs_soul(self, query: str) -> bool:
        q = query.lower()
        return any(trigger in q for trigger in SOUL_TRIGGERS)

    def _needs_grounding(self, query: str) -> bool:
        q = query.lower()
        return any(trigger in q for trigger in GROUNDING_TRIGGERS)

    def _augment_query(self, query: str) -> str:
        hints = []
        if self._needs_grounding(query):
            hints.append(
                "[Grounding required: run date via terminal first, then search before answering.]"
            )
        if self._needs_soul(query):
            hints.append(
                "[Complex task detected: call load_soul before proceeding.]"
            )
        return query + ("\n\n" + "\n".join(hints) if hints else "")

    async def run(self, query: str) -> Optional[str]:
        console.print()
        console.rule(style="border")

        augmented   = self._augment_query(query)
        msg_history = self.history.to_messages()
        tok_est     = self.history.token_estimate + _est_tokens(augmented)

        if tok_est > CTX_COMPRESS:
            msg = self.history.compress()
            console.print(f"[dim]Context guard: {msg}[/dim]")
        elif tok_est > CTX_WARN:
            console.print(f"[dim]Context at {tok_est:,} tokens — approaching limit.[/dim]")

        for attempt in range(3):
            try:
                console.print(Text(" Agent ", style="agent"), end=" ")
                full_response = ""

                with Live(
                    Panel(Text("Thinking…", style="dim"), border_style="border", box=box.ROUNDED, padding=(1, 2)),
                    console=console, transient=True, refresh_per_second=12,
                ) as live:
                    async with agent.run_stream(augmented, message_history=msg_history) as result:
                        async for chunk in result.stream_text():
                            full_response = chunk if chunk.startswith(full_response) else full_response + chunk
                            ctx_display   = f"ctx: ~{tok_est:,}/55,000"
                            live.update(Panel(
                                Text(_safe_text(full_response) or "Thinking…", style="dim"),
                                title=Text(f" thinking | {ctx_display} ", style="dim"),
                                border_style="border", box=box.ROUNDED, padding=(1, 2),
                            ))

                    # Extract clean assistant text from new messages
                    assistant_text = full_response

                self.history.add(query, assistant_text)

                console.print()
                console.print(Panel(
                    Markdown(full_response),
                    border_style="border", box=box.ROUNDED, padding=(1, 2),
                ))
                return full_response

            except Exception as e:
                if attempt == 2:
                    console.print(f"\n[tool.err]✗ Failed after 3 attempts: {e}[/tool.err]")
                    return None
                console.print(f"\n[dim]Connection error (attempt {attempt + 1}/3), retrying…[/dim]")
                await asyncio.sleep(1.5)

        return None


# ─────────────────────────── SLASH COMMANDS ───────────────────────────────

def _make_help_table() -> Table:
    tbl = Table(box=box.SIMPLE, show_header=True, padding=(0, 2), header_style="header")
    tbl.add_column("Command",     style="tool.name", no_wrap=True)
    tbl.add_column("Description", style="dim")
    rows = [
        ("/help",        "Show this help"),
        ("/clear",       "Wipe conversation history"),
        ("/compress",    "Force-compress history now"),
        ("/history",     "Show turn count + token estimate"),
        ("/soul",        "Print SOUL.md contents"),
        ("/model",       "Show model + server config"),
        ("/sources",     "List available RSS sources"),
        ("/exit",        "Save history and exit"),
    ]
    for cmd, desc in rows:
        tbl.add_row(cmd, desc)
    return tbl


async def handle_slash(cmd: str, session: AgentSession) -> bool:
    parts = cmd.strip().split(None, 1)
    c     = parts[0].lower()

    if c in ("/exit", "/quit", "/q"):
        session.history.save()
        console.print(Panel("[dim]History saved. Goodbye.[/dim]", border_style="border"))
        sys.exit(0)

    if c == "/help":
        console.print(Panel(
            _make_help_table(),
            title="[header]Open-Agent Commands[/header]",
            border_style="border", box=box.ROUNDED,
        ))
        return True

    if c == "/clear":
        session.history.clear()
        console.print("[tool.ok]✓ History cleared.[/tool.ok]")
        return True

    if c == "/compress":
        msg = session.history.compress()
        console.print(f"[tool.ok]✓ {msg}[/tool.ok]")
        return True

    if c == "/history":
        h   = session.history
        tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        tbl.add_column(style="tool.name")
        tbl.add_column(style="dim")
        tbl.add_row("Turns in context", str(h.turn_count))
        tbl.add_row("Summary present",  "yes" if h.summary else "no")
        tbl.add_row("Token estimate",   f"~{h.token_estimate:,}")
        tbl.add_row("Context limit",    "55,000")
        console.print(Panel(tbl, title="[header]History[/header]", border_style="border", box=box.ROUNDED))
        return True

    if c == "/soul":
        if SOUL_FILE.exists():
            console.print(Panel(
                Markdown(SOUL_FILE.read_text(encoding="utf-8")),
                title="[header]SOUL.md[/header]",
                border_style="border", box=box.ROUNDED, padding=(1, 2),
            ))
        else:
            console.print("[tool.err]SOUL.md not found alongside agent.py[/tool.err]")
        return True

    if c == "/model":
        tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        tbl.add_column(style="tool.name")
        tbl.add_column(style="accent")
        tbl.add_row("Model",       MODEL_NAME)
        tbl.add_row("Server",      BASE_URL)
        tbl.add_row("Search",      SEARXNG_URL)
        tbl.add_row("Context",     "55,000 tokens")
        tbl.add_row("KV cache",    "Q4_0 (K + V)")
        tbl.add_row("Flash attn",  "on")
        console.print(Panel(tbl, title="[header]Config[/header]", border_style="border", box=box.ROUNDED))
        return True

    if c == "/sources":
        cols   = 3
        names  = sorted(RSS_SOURCES.keys())
        chunks = [names[i:i+cols] for i in range(0, len(names), cols)]
        tbl    = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        for _ in range(cols):
            tbl.add_column(style="tool.name")
        for chunk in chunks:
            tbl.add_row(*chunk + [""] * (cols - len(chunk)))
        cats = ", ".join(sorted(BROAD_CATEGORIES.keys()))
        console.print(Panel(
            tbl,
            title="[header]RSS Sources[/header]",
            subtitle=f"[dim]Categories: {cats}[/dim]",
            border_style="border", box=box.ROUNDED,
        ))
        return True

    return False


# ─────────────────────────── BANNER ───────────────────────────────────────

def print_banner():
    console.print()
    console.print(Panel(
        f"[header]OPEN-AGENT[/header]  [dim]v2.0[/dim]\n"
        f"[dim]Model :[/dim] [accent]{MODEL_NAME}[/accent]  [dim]@[/dim]  [accent]port 8083[/accent]\n"
        f"[dim]Search:[/dim] [accent]SearxNG[/accent]       [dim]@[/dim]  [accent]localhost:8081[/accent]\n"
        f"[dim]Context: 55,000 tokens · KV Q4_0 · Flash Attention[/dim]\n"
        f"[dim]Type [/dim][tool.name]/help[/tool.name][dim] for commands · [/dim][tool.name]/exit[/tool.name][dim] to quit[/dim]",
        box=box.DOUBLE_EDGE, border_style="header", padding=(1, 3),
    ))
    console.print()


# ─────────────────────────── MAIN ─────────────────────────────────────────

async def main():
    print_banner()
    session = AgentSession()
    while True:
        try:
            console.print()
            user_input = console.input(Text("You ", style="user") + Text("> ", style="dim")).strip()
            if not user_input:
                continue
            if user_input.startswith("/"):
                if await handle_slash(user_input, session):
                    continue
            await session.run(user_input)
        except KeyboardInterrupt:
            console.print("\n[dim]Ctrl-C — saving history…[/dim]")
            session.history.save()
            console.print("[dim]Goodbye.[/dim]")
            break
        except EOFError:
            session.history.save()
            break
        except Exception as e:
            console.print(f"[tool.err]Unexpected error: {e}[/tool.err]")


if __name__ == "__main__":
    asyncio.run(main())


