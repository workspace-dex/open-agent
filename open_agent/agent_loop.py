#!/usr/bin/env python3
"""
Open-Agent custom loop with parallel tool calling.
Replaces pydantic-ai's run_stream() for full control over tool execution.

Uses the raw OpenAI SDK + custom agent loop:
- Structured response.tool_calls (not text-stream-only like pydantic-ai)
- Parallel execution via ThreadPoolExecutor for independent tools
- Hermes-style safety: path-overlap detection for file tools
"""

import asyncio
import concurrent.futures
import json
import re
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from openai import OpenAI


# ══════════════════════════════════════════════════════════════════════════
#  INTERRUPT SIGNAL  (mirrors hermes-agent tools/interrupt.py)
# ══════════════════════════════════════════════════════════════════════════
# Thread-safe interrupt flag. Set by Ctrl+C handler; checked between
# tool calls and API batches.  Unlike hermes-agent's per-thread set,
# open-agent uses a single Event since it runs one agent at a time.

_interrupted = threading.Event()


def interrupt_agent() -> None:
    """Signal the agent to abort at the next safe point."""
    _interrupted.set()


def is_interrupted() -> bool:
    """True if Ctrl+C has been pressed since the last reset."""
    return _interrupted.is_set()


def clear_interrupt() -> None:
    """Reset the interrupt flag before starting a new agent run."""
    _interrupted.clear()


# ══════════════════════════════════════════════════════════════════════════
#  ANSI COLOR CONSTANTS
# ══════════════════════════════════════════════════════════════════════════

RESET  = "\033[0m"
DIM    = "\033[2m"
C_RED  = "\033[91m"
C_GREEN = "\033[92m"
C_AMBER = "\033[93m"
C_BLUE  = "\033[94m"
C_PURPLE = "\033[95m"
C_CYAN  = "\033[96m"
C_GRAY  = "\033[90m"
C_WHITE = "\033[97m"
C_BOLD  = "\033[1m"
C_SLATE = "\033[90m"

# ══════════════════════════════════════════════════════════════════════════
#  PARALLEL EXECUTION CONSTANTS
#  (mirrors hermes-agent run_agent.py)
# ══════════════════════════════════════════════════════════════════════════

_PARALLEL_SAFE_TOOLS = frozenset({
    "web_search", "cached_web_search", "fetch_page",
    "smart_research",
    "read_file", "search_obsidian", "read_obsidian_note",
    "read_memory", "read_rss_by_name", "search_sessions",
    "run_terminal",  # shell commands — safe to parallelize with each other
    "write_file",     # already path-scoped checked below
    "run_python",     # stateless per call, safe to parallelize
})

# File tools: safe in parallel only when paths DON'T overlap
_PATH_SCOPED_TOOLS = frozenset({"read_file", "write_file", "patch"})

# Never parallelize — must run sequentially
_NEVER_PARALLEL_TOOLS = frozenset({
    "web_search",
    "cached_web_search",
    "fetch_page",
    "smart_research",
})

_MAX_TOOL_WORKERS = 4  # conservative for local Ollama


# ══════════════════════════════════════════════════════════════════════════
#  TOOL SCHEMAS  (OpenAI format — mirrors what pydantic_ai generates)
# ══════════════════════════════════════════════════════════════════════════

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web via SearxNG. Use for current facts, news, or anything potentially stale.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cached_web_search",
            "description": "Web search with local caching (1hr TTL). Use for queries that may repeat within the same session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "smart_research",
            "description": "Run up to 4 parallel web queries and merge. For multi-angle research.",
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Up to 4 search queries",
                    },
                },
                "required": ["queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": "Extract readable text from a URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal",
            "description": "Execute a shell command safely. Dangerous patterns are blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run (max 30s)"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a local file (up to 8,000 chars). RESTRICTED TO ~/ FOR SAFETY.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (must be in home directory)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a local file. RESTRICTED TO ~/ FOR SAFETY. Auto-creates parent dirs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (must be in home directory)"},
                    "content": {"type": "string", "description": "Content to write"},
                    "expand_user": {
                        "type": "boolean",
                        "description": "Expand ~ in path",
                        "default": True,
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_soul",
            "description": "Load extended behavioral instructions from SOUL.md.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_obsidian",
            "description": "Search your Obsidian vault for notes containing specific text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_obsidian_note",
            "description": "Read a specific note from your Obsidian vault.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note_name": {"type": "string", "description": "Note name (with or without .md)"},
                },
                "required": ["note_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_obsidian_note",
            "description": "Create or append to an Obsidian note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note_name": {"type": "string", "description": "Note name"},
                    "content": {"type": "string", "description": "Content to write"},
                    "append": {
                        "type": "boolean",
                        "description": "Append to existing note",
                        "default": True,
                    },
                },
                "required": ["note_name", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_memory",
            "description": "Update persistent MEMORY.md.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Content to append or write"},
                    "append": {
                        "type": "boolean",
                        "description": "Append (default True)",
                        "default": True,
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_memory",
            "description": "Read persistent MEMORY.md.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_profile",
            "description": "Update USER.md with user info.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Content to write"},
                    "append": {
                        "type": "boolean",
                        "description": "Append (default False)",
                        "default": False,
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_sessions",
            "description": "Full-text search across all past conversation sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {
                        "type": "integer",
                        "description": "Max results",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_pptx",
            "description": "Create PowerPoint presentation from slide data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Output .pptx file path"},
                    "slides": {
                        "type": "array",
                        "description": "List of slide dicts with 'title' and optional 'content'/'bullets'",
                        "items": {"type": "object"},
                    },
                },
                "required": ["path", "slides"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_rss_by_name",
            "description": "Fetch RSS from curated sources. Names: hn, verge, arxiv_ai... Categories: tech, ai, security, engineering, startups.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Feed name or category"},
                    "limit": {
                        "type": "integer",
                        "description": "Max entries per feed",
                        "default": 8,
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "Execute Python code in a sandboxed subprocess. Use for calculations, data processing, file parsing, regex testing, API calls, or quick experiments. Stdout is returned (capped at 5000 chars). Stderr is appended.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute. Print results to stdout.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max execution time in seconds (default 15, max 60).",
                        "default": 15,
                    },
                },
                "required": ["code"],
            },
        },
    },
]


# ══════════════════════════════════════════════════════════════════════════
#  PATH OVERLAP DETECTION  (from hermes-agent)
# ══════════════════════════════════════════════════════════════════════════

def _extract_path(tool_name: str, args: dict) -> Optional[Path]:
    raw = args.get("path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    expanded = Path(raw).expanduser()
    if expanded.is_absolute():
        return Path(str(expanded).rstrip("/"))
    return Path(str(Path.cwd() / expanded)).absolute()


def _paths_overlap(a: Path, b: Path) -> bool:
    a_parts = a.parts
    b_parts = b.parts
    if not a_parts or not b_parts:
        return bool(a_parts) == bool(b_parts)
    return a_parts[:min(len(a_parts), len(b_parts))] == b_parts[:min(len(a_parts), len(b_parts))]


# ══════════════════════════════════════════════════════════════════════════
#  PARALLEL DECISION
# ══════════════════════════════════════════════════════════════════════════

def _should_parallelize(tool_calls: list) -> bool:
    """Return True when a tool-call batch is safe to run concurrently.

    Mirrors hermes-agent's _should_parallelize_tool_batch().
    """
    if len(tool_calls) <= 1:
        return False

    tool_names = [tc["function"]["name"] for tc in tool_calls]

    if any(n in _NEVER_PARALLEL_TOOLS for n in tool_names):
        return False

    reserved_paths: list[Path] = []
    for tc in tool_calls:
        name = tc["function"]["name"]
        try:
            raw_args = tc["function"].get("arguments", "{}")
            if isinstance(raw_args, str):
                args = json.loads(raw_args)
            else:
                args = raw_args or {}
        except Exception:
            return False

        if not isinstance(args, dict):
            return False

        if name in _PATH_SCOPED_TOOLS:
            path = _extract_path(name, args)
            if path is None:
                return False
            if any(_paths_overlap(path, existing) for existing in reserved_paths):
                return False
            reserved_paths.append(path)
            continue

        if name not in _PARALLEL_SAFE_TOOLS:
            return False

    return True


# ══════════════════════════════════════════════════════════════════════════
#  SAFETY HELPERS  (from _impl.py — copied to avoid import cycles)
# ══════════════════════════════════════════════════════════════════════════

_BLOCKED_PATTERNS = [
    r"\brm\s+-rf\s+/", r"\brm\s+--no-preserve-root", r"\bmkfs\b",
    r"sudo\s+su\b", r"sudo\s+bash\b", r"curl\s+.*\|\s*(ba)?sh",
    r"\bnc\b.*-e\s+/bin/(ba)?sh",
]
_BLOCKED_RE = [re.compile(p, re.IGNORECASE) for p in _BLOCKED_PATTERNS]


def _is_dangerous(cmd: str) -> tuple[bool, str]:
    for p in _BLOCKED_RE:
        if p.search(cmd):
            return True, p.pattern
    return False, ""


# ══════════════════════════════════════════════════════════════════════════
#  TOOL HANDLERS  — called by the loop
# ══════════════════════════════════════════════════════════════════════════
#  These are sync wrappers around the async @agent.tool functions.
#  The async tools are imported lazily to avoid import cycles.

_tool_handler_cache: dict = {}


def _load_handlers():
    """Lazily import tool handlers from _impl to avoid circular imports."""
    global _tool_handler_cache
    if _tool_handler_cache:
        return _tool_handler_cache
    from open_agent._impl import (
        web_search, cached_web_search, smart_research, fetch_page,
        run_terminal, read_file, write_file, load_soul,
        search_obsidian, read_obsidian_note, write_obsidian_note,
        update_memory, read_memory, update_user_profile,
        search_sessions, create_pptx, read_rss_by_name,
    )
    _tool_handler_cache = {
        "web_search": web_search,
        "cached_web_search": cached_web_search,
        "smart_research": smart_research,
        "fetch_page": fetch_page,
        "run_terminal": run_terminal,
        "read_file": read_file,
        "write_file": write_file,
        "load_soul": load_soul,
        "search_obsidian": search_obsidian,
        "read_obsidian_note": read_obsidian_note,
        "write_obsidian_note": write_obsidian_note,
        "update_memory": update_memory,
        "read_memory": read_memory,
        "update_user_profile": update_user_profile,
        "search_sessions": search_sessions,
        "create_pptx": create_pptx,
        "read_rss_by_name": read_rss_by_name,
    }
    return _tool_handler_cache


def _run_sync(tool_name: str, args: dict) -> str:
    """Run a tool synchronously from the thread pool.

    Each call creates its own event loop, registers it as the thread-local
    default (so asyncio.gather() etc. inside tools find a valid loop),
    runs the coroutine, cancels any stray tasks, then closes the loop.

    If the interrupt flag is set (Ctrl+C), abort immediately rather than
    spending time on a potentially long tool call.
    """
    # Fast exit if already interrupted (checks before acquiring any lock)
    if is_interrupted():
        return "[interrupted]"

    handlers = _load_handlers()
    if tool_name not in handlers:
        return f"Error: unknown tool '{tool_name}'"

    handler = handlers[tool_name]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # Second check inside the event loop (covers race with signal delivery)
        if is_interrupted():
            return "[interrupted]"
        return loop.run_until_complete(handler(None, **args))
    except Exception as e:
        return f"Error in {tool_name}: {e}"
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        finally:
            loop.close()
            asyncio.set_event_loop(None)


# ══════════════════════════════════════════════════════════════════════════
#  AGENT LOOP
# ══════════════════════════════════════════════════════════════════════════

class AgentLoop:
    """
    Custom OpenAI-agent loop with parallel tool execution.

    Unlike pydantic-ai's run_stream() which hides tool_calls internally
    and executes them sequentially, this loop:
    1. Gets structured response.tool_calls from the API
    2. Decides: parallel or sequential?
    3. Executes with ThreadPoolExecutor
    4. Feeds results back into the conversation
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8083/v1",
        api_key: str = "lm-studio",
        model: str = "llama.cpp",
        system_prompt: str = "",
        tools: list = None,
        max_tokens: int = 2400,
        temperature: float = 0.6,
        top_p: float = 0.95,
        text_callback: callable = None,  # called with each text chunk for real-time output
    ):
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.system_prompt = system_prompt
        self.tools = tools or TOOL_SCHEMAS
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.text_callback = text_callback

    def _to_messages(self, history: list, query: str) -> list[dict]:
        """Convert pydantic-ai ModelMessage history to OpenAI message format."""
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})

        for msg in history:
            # pydantic_ai message types
            if hasattr(msg, "parts"):
                content = ""
                for part in msg.parts:
                    if hasattr(part, "content"):
                        content += str(part.content)
                if hasattr(msg, "role"):
                    role = str(msg.role).replace("user", "user").replace("model", "assistant")
                    if role in ("user", "assistant", "system", "tool"):
                        messages.append({"role": role, "content": content})
            elif hasattr(msg, "content") and hasattr(msg, "role"):
                role = str(msg.role).lower()
                if role in ("user", "assistant", "system", "tool"):
                    messages.append({"role": role, "content": msg.content})

        messages.append({"role": "user", "content": query})
        return messages

    def _execute_single(self, tool_call: dict) -> str:
        """Execute one tool call synchronously."""
        name = tool_call["function"]["name"]
        raw_args = tool_call["function"].get("arguments", "{}")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            args = {"raw": str(raw_args)}

        if name == "run_terminal":
            cmd = args.get("command", "")
            dangerous, pattern = _is_dangerous(cmd)
            if dangerous:
                return (f"⛔ Command blocked for safety.\n"
                        f"Pattern matched: {pattern}\n"
                        f"If this is legitimate, run it manually.")

        return _run_sync(name, args)

    def _execute_parallel(self, tool_calls: list) -> list[str]:
        """Execute multiple tools concurrently. Returns results in original order."""
        # Tool progress is handled by td.start/done in each tool function
        # Just execute and collect results
        start_time = time.time()
        results = {}

        with ThreadPoolExecutor(max_workers=_MAX_TOOL_WORKERS) as pool:
            future_to_tc = {}
            for tc in tool_calls:
                fut = pool.submit(self._execute_single, tc)
                future_to_tc[fut] = tc["function"]["name"]

            for fut in concurrent.futures.as_completed(future_to_tc):
                # After each completed tool, give the interrupt signal a chance
                # to abort remaining tools without waiting for them to finish.
                # (as_completed returns immediately when any future is done,
                #  so this is a natural polling point.)
                if is_interrupted():
                    # Cancel all pending futures — their _run_sync calls will
                    # see is_interrupted() and return "[interrupted]" promptly
                    for pending in future_to_tc:
                        pending.cancel()
                    break

                name = future_to_tc[fut]
                elapsed = time.time() - start_time
                try:
                    results[name] = fut.result()
                except Exception as e:
                    results[name] = f"Error: {e}"

        return [results.get(tc["function"]["name"], "") for tc in tool_calls]

    def _execute_sequential(self, tool_calls: list) -> list[str]:
        """Execute tools one at a time."""
        # Tool progress is handled by td.start/done in each tool function
        results = []
        for tc in tool_calls:
            # Check interrupt between each tool — avoids spending 30s on
            # a long-running tool after the user already pressed Ctrl+C
            if is_interrupted():
                results.append("[interrupted]")
                break
            result = self._execute_single(tc)
            results.append(result)
        return results

    def _run_stream(self, messages: list[dict], tool_use_callback=None
                    ) -> tuple[str, list[dict]]:
        """
        Run the model with streaming. Accumulates tool call text from content
        (via [[TOOL_CALLS: ...]] markers) and API-level tool_calls simultaneously.

        During streaming, if [[TOOL_CALLS: ...]] is detected, it immediately
        executes those calls in parallel, feeds results back, and continues.
        This means the model's _text_ can trigger parallel execution.

        Returns:
            (final_text_without_markers, list_of_detected_tool_calls)
        """
        accumulated = ""
        tool_calls_detected: list[dict] = []
        pending_tool_text = ""  # accumulates between [[TOOL_CALLS: and ]]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self.tools,
            stream=True,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            parallel_tool_calls=True,  # allow model to call multiple tools in one response
        )

        for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                accumulated += delta.content
                # Stream text in real-time if callback provided
                if self.text_callback:
                    self.text_callback(delta.content)

                # Intercept [[TOOL_CALLS: ...]] markers from text
                for ch in delta.content:
                    pending_tool_text += ch
                    if pending_tool_text.endswith("]]"):
                        start = pending_tool_text.find("[[TOOL_CALLS:")
                        if start >= 0:
                            json_str = pending_tool_text[start + 13:pending_tool_text.rfind("]]")]
                            pending_tool_text = ""
                            try:
                                calls = json.loads(json_str)
                                for c in calls:
                                    # Generate a fake tool_call_id
                                    fake_id = f"text_{len(tool_calls_detected)}_{random_id(8)}"
                                    tool_calls_detected.append({
                                        "id": fake_id,
                                        "function": {
                                            "name": c.get("name", c.get("tool", "")),
                                            "arguments": json.dumps(c.get("args", c.get("arguments", {}))),
                                        },
                                    })
                            except Exception:
                                pending_tool_text = ""
                        else:
                            pending_tool_text = ""
                    elif pending_tool_text.endswith("[[") or (len(pending_tool_text) > 20 and "[[TOOL_CALLS:" not in pending_tool_text):
                        pending_tool_text = ""

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    while len(tool_calls_detected) <= idx:
                        tool_calls_detected.append({"function": {"name": "", "arguments": ""}, "id": ""})
                    if tc_delta.id:
                        tool_calls_detected[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        fn = tc_delta.function
                        if fn.name:
                            tool_calls_detected[idx]["function"]["name"] = fn.name
                        if fn.arguments:
                            tool_calls_detected[idx]["function"]["arguments"] = (
                                tool_calls_detected[idx]["function"].get("arguments", "") + fn.arguments
                            )

        # Strip [[TOOL_CALLS: ...]] from final text
        import re
        clean_text = re.sub(r'\[\[TOOL_CALLS:[^\]]*\]\]', '', accumulated).strip()

        return clean_text, tool_calls_detected

    def _debug_print_calls(self, tool_calls: list):
        """Print tool call details for debugging."""
        for i, tc in enumerate(tool_calls):
            print(f"  [DEBUG] tool_call[{i}]: id={tc.get('id','')!r}, name={tc['function']['name']!r}, args={tc['function'].get('arguments','')[:80]!r}", flush=True)

    def _collect_all_tool_calls(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """
        Run the model and collect tool calls, executing them with parallel threading.
        With parallel_tool_calls=True, the model makes all needed calls in one response.
        """
        all_tool_calls: list[dict] = []

        for batch_num in range(20):  # max 20 batches (prevents infinite loops)
            # Check interrupt before starting another round-trip to the model.
            # If the user pressed Ctrl+C while tools were running, bail now
            # instead of kicking off another potentially long API call.
            if is_interrupted():
                return "(interrupted)", all_tool_calls

            text, tool_calls = self._run_stream(messages)

            if not tool_calls:
                # No more tool calls — return final answer
                return text, all_tool_calls

            # Execute: parallel if multiple AND safe, else sequential
            if len(tool_calls) > 1 and _should_parallelize(tool_calls):
                # Don't print [PARALLEL] header - just show tool activity lines
                results = self._execute_parallel(tool_calls)
            else:
                results = self._execute_sequential(tool_calls)

            # Record all tool calls with their results
            for tc, result in zip(tool_calls, results):
                tc_with_result = dict(tc)
                tc_with_result["_result"] = result[:8000]
                all_tool_calls.append(tc_with_result)

            # Append assistant message with tool calls
            messages.append({
                "role": "assistant",
                "content": text,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }
                    for tc in tool_calls
                ],
            })

            # Append tool results
            for tc, result in zip(tool_calls, results):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["function"]["name"],
                    "content": result[:8000],
                })

        return "(max tool iterations reached)", all_tool_calls

    def run(self, query: str, history: list = None) -> tuple[str, list[dict]]:
        """
        Main agent loop — handles multi-tool parallel execution.
        Returns (final_text, all_tool_calls) where tool_calls includes both
        call metadata and results for session persistence.
        """
        messages = self._to_messages(history or [], query)
        text, all_tool_calls = self._collect_all_tool_calls(messages)
        return text, all_tool_calls
