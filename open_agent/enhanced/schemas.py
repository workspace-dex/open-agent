#!/usr/bin/env python3
"""
Enhanced tool schemas with examples, retry policies, and metadata.
Extends _impl.py's TOOLSETS with richer tool descriptions.
"""
from typing import Optional


# Enhanced tool schemas with examples and metadata
TOOL_SCHEMAS = {
    # ── Web Tools ─────────────────────────────────────────────────────────────
    "web_search": {
        "description": "Search the web via SearxNG (private, no tracking). "
                       "Use for current facts, news, prices, weather, or anything that may be stale.",
        "examples": [
            {
                "query": "latest AI models released 2026",
                "reasoning": "Recent information needed — use web_search for up-to-date facts",
            },
            {
                "query": "Python requests library documentation",
                "reasoning": "Documentation lookup — fetch_page may follow for full docs",
            },
            {
                "query": "weather in Bangalore today",
                "reasoning": "Real-time data needed — web_search for current conditions",
            },
        ],
        "parallel_safe": True,
        "cache_ttl": 3600,
        "retry_on_empty": True,
        "max_retries": 2,
        "truncate_chars": 3000,
        "keywords": ["search", "find", "google", "latest", "news", "current", "price", "weather"],
    },

    "cached_web_search": {
        "description": "Web search with 1-hour local caching. "
                       "Use for queries that may repeat within the same session.",
        "examples": [
            {
                "query": "ollama available models",
                "reasoning": "Stable info — cached to avoid repeat API calls",
            },
            {
                "query": "python library version",
                "reasoning": "Version info doesn't change frequently",
            },
        ],
        "parallel_safe": True,
        "cache_ttl": 3600,
        "retry_on_empty": True,
        "truncate_chars": 2500,
        "keywords": ["cached", "repeat", "again", "same", "already searched"],
    },

    "smart_research": {
        "description": "Run up to 4 web searches in parallel and merge results. "
                       "Use for multi-angle research on complex topics.",
        "examples": [
            {
                "queries": ["GPT-5 capabilities", "Claude 4 features", "Gemini 2.0 comparison"],
                "reasoning": "Multi-angle comparison — parallel searches faster than sequential",
            },
            {
                "queries": ["local LLM setup arch linux", "ollama vs llama.cpp", "GGUF quantization guide"],
                "reasoning": "Researching a topic from multiple angles simultaneously",
            },
        ],
        "parallel_safe": True,
        "max_queries": 4,
        "truncate_per_result": 400,
        "keywords": ["research", "compare", "multi", "several", "different angles"],
    },

    "fetch_page": {
        "description": "Extract readable text from a URL. "
                       "Use after web_search to get full content from promising links.",
        "examples": [
            {
                "url": "https://github.com/ollama/ollama",
                "reasoning": "Get full README details after initial search",
            },
            {
                "url": "https://python.readthedocs.io/en/latest/",
                "reasoning": "Fetch documentation that web_search only showed snippets of",
            },
        ],
        "parallel_safe": True,
        "truncate_chars": 6000,
        "timeout": 15,
        "retry_on_error": True,
        "keywords": ["fetch", "download", "scrape", "read", "extract", "content"],
    },

    # ── File Tools ────────────────────────────────────────────────────────────
    "read_file": {
        "description": "Read a local file (up to 8,000 chars). "
                       "RESTRICTED TO ~/ FOR SAFETY. Returns truncated content if too large.",
        "examples": [
            {
                "path": "/home/dex/project/main.py",
                "reasoning": "Read source code to understand existing implementation",
            },
            {
                "path": "/home/dex/notes/todo.md",
                "reasoning": "Read user notes to recall previous context",
            },
        ],
        "max_chars": 8000,
        "allowed_dirs": ["~"],  # Security: only home dir
        "retry_on_error": True,
        "keywords": ["read", "view", "show", "cat", "open", "file"],
    },

    "write_file": {
        "description": "Write content to a local file (creates parent dirs if needed). "
                       "RESTRICTED TO ~/ FOR SAFETY.",
        "examples": [
            {
                "path": "/home/dex/project/test.py",
                "content": "print('hello')",
                "reasoning": "Create or overwrite a Python script",
            },
            {
                "path": "/home/dex/notes/idea.md",
                "content": "# My idea\n\nSome content",
                "reasoning": "Save notes or markdown files",
            },
        ],
        "allowed_dirs": ["~"],
        "auto_mkdir": True,
        "verify_write": True,
        "retry_on_permission_error": True,
        "keywords": ["write", "create", "save", "make", "new file", "generate"],
    },

    # ── Terminal Tools ────────────────────────────────────────────────────────
    "run_terminal": {
        "description": "Execute a shell command safely. "
                       "DANGEROUS PATTERNS ARE BLOCKED (rm -rf /, sudo su, curl|wget|sh, etc.).",
        "examples": [
            {
                "command": "pip install requests",
                "reasoning": "Install a Python package",
            },
            {
                "command": "git status && git log --oneline -5",
                "reasoning": "Check git repository state",
            },
            {
                "command": "python script.py --arg value",
                "reasoning": "Run a Python script with arguments",
            },
            {
                "command": "find ~/project -name '*.py' | head -20",
                "reasoning": "Find files matching a pattern",
            },
        ],
        "timeout": 30,
        "dangerous_patterns_blocked": True,
        "error_hints": True,
        "auto_fix_on_retry": True,
        "keywords": ["run", "execute", "command", "shell", "bash", "python", "pip", "git", "npm"],
    },

    # ── Memory Tools ──────────────────────────────────────────────────────────
    "update_memory": {
        "description": "Append or replace persistent MEMORY.md. "
                       "Use to remember important facts across sessions.",
        "examples": [
            {
                "content": "User prefers dark mode terminal",
                "reasoning": "Remember user preference for future sessions",
            },
            {
                "content": "Project uses FastAPI, PostgreSQL, Redis",
                "reasoning": "Remember project stack for context in future sessions",
            },
        ],
        "allowed_dirs": ["~/.config/open-agent"],
        "append_by_default": True,
        "max_chars": 10000,
        "keywords": ["remember", "note", "save fact", "store", "persist"],
    },

    "read_memory": {
        "description": "Read the persistent MEMORY.md file.",
        "examples": [
            {
                "reasoning": "Recall user preferences at session start",
            },
        ],
        "max_chars": 3000,
        "keywords": ["what do you remember", "recall", "memory", "remembered"],
    },

    "search_sessions": {
        "description": "Full-text search across all past conversation sessions. "
                       "Returns matching conversation pairs with context.",
        "examples": [
            {
                "query": "python asyncio patterns",
                "reasoning": "Find previous conversations about async programming",
            },
        ],
        "max_results": 10,
        "keywords": ["search history", "past conversations", "what did we do", "previous session"],
    },

    "update_user_profile": {
        "description": "Update USER.md with user info (name, preferences, context).",
        "examples": [
            {
                "content": "Name: Dex\nTimezone: IST\nFocus: Local AI, coding",
                "reasoning": "Remember user identity and preferences",
            },
        ],
        "append": False,
        "keywords": ["profile", "preferences", "about me", "user info"],
    },

    # ── Obsidian Tools ───────────────────────────────────────────────────────
    "search_obsidian": {
        "description": "Search Obsidian vault notes for matching content.",
        "examples": [
            {
                "query": "machine learning notes",
                "reasoning": "Find relevant notes in the user's knowledge base",
            },
        ],
        "max_results": 25,
        "keywords": ["obsidian", "vault", "notes", "search notes"],
    },

    "read_obsidian_note": {
        "description": "Read a specific note from Obsidian vault.",
        "examples": [
            {
                "note_name": "AI Research",
                "reasoning": "Read a specific known note",
            },
        ],
        "max_chars": 10000,
        "keywords": ["read note", "obsidian note"],
    },

    "write_obsidian_note": {
        "description": "Create or append to an Obsidian note.",
        "examples": [
            {
                "note_name": "Daily Notes/2026-04-30",
                "content": "Worked on open-agent improvements today",
                "reasoning": "Log daily progress to Obsidian",
            },
        ],
        "append_by_default": True,
        "keywords": ["write obsidian", "save to obsidian", "obsidian note"],
    },

    # ── Office Tools ─────────────────────────────────────────────────────────
    "create_pptx": {
        "description": "Create a PowerPoint presentation from slide data.",
        "examples": [
            {
                "path": "/home/dex/presentation.pptx",
                "slides": [
                    {"title": "Introduction", "content": "Welcome"},
                    {"title": "Agenda", "bullets": ["Point 1", "Point 2"]},
                ],
                "reasoning": "Generate a simple presentation deck",
            },
        ],
        "keywords": ["presentation", "slides", "powerpoint", "ppt", "deck"],
    },

    # ── RSS Tools ────────────────────────────────────────────────────────────
    "read_rss_by_name": {
        "description": "Fetch RSS feeds from curated sources. "
                       "Use for staying updated on tech news, AI research, security, etc.",
        "examples": [
            {
                "name": "hn",
                "reasoning": "Get Hacker News top stories",
            },
            {
                "name": "ai",
                "reasoning": "Get latest AI research from arXiv",
            },
            {
                "name": "security",
                "reasoning": "Get security news from multiple sources",
            },
        ],
        "sources": ["hn", "verge", "ars", "arxiv_ai", "arxiv_cl", "security"],
        "categories": ["tech", "ai", "security", "engineering", "startups"],
        "keywords": ["rss", "feed", "news", "hacker news", "subscribe"],
    },

    # ── Soul Tools ──────────────────────────────────────────────────────────
    "load_soul": {
        "description": "Load extended behavioral instructions from SOUL.md. "
                       "Call for complex research or planning tasks.",
        "examples": [
            {
                "reasoning": "Complex multi-step task needs extended behavioral guidance",
            },
        ],
        "trigger_keywords": ["research", "plan", "step by step", "deep dive", "strategy"],
        "keywords": ["soul", "extended", "complex", "research mode"],
    },
}


# Tool dependency graph — what tools feed into others
TOOL_DEPENDENCIES = {
    "smart_research": ["web_search"],
    "fetch_page": ["web_search"],  # Usually called after web_search finds URLs
    "read_obsidian_note": ["search_obsidian"],  # Usually follows search
    "write_obsidian_note": ["read_obsidian_note"],  # May follow reading
}


# Tools that can run in parallel (independent results)
PARALLEL_SAFE_TOOLS = {
    "web_search", "cached_web_search", "fetch_page",
    "read_file", "write_file",  # Different files
    "search_obsidian", "read_memory", "read_rss_by_name",
}


# Error recovery strategies per tool
ERROR_RECOVERY = {
    "web_search": {
        "retry_on": ["timeout", "connection_error"],
        "fallback": "cached_web_search",
        "max_retries": 2,
    },
    "fetch_page": {
        "retry_on": ["timeout", "connection_error", "HTTP 500", "HTTP 502"],
        "fallback": None,
        "max_retries": 1,
    },
    "run_terminal": {
        "retry_on": ["timeout", "exit_code_nonzero"],
        "auto_fix": True,  # Analyze error and fix
        "max_retries": 2,
    },
    "read_file": {
        "retry_on": ["permission_error"],
        "fallback": None,
        "max_retries": 1,
    },
    "write_file": {
        "retry_on": ["permission_error", "FileNotFoundError"],
        "auto_mkdir": True,
        "max_retries": 2,
    },
}


def get_tool_schema(tool_name: str) -> Optional[dict]:
    """Get enhanced schema for a tool."""
    return TOOL_SCHEMAS.get(tool_name)


def is_parallel_safe(tool_name: str) -> bool:
    """Check if tool can run in parallel with others."""
    return tool_name in PARALLEL_SAFE_TOOLS


def get_error_recovery(tool_name: str) -> Optional[dict]:
    """Get error recovery strategy for a tool."""
    return ERROR_RECOVERY.get(tool_name)


def get_tool_dependencies(tool_name: str) -> list[str]:
    """Get tools that should typically run before this one."""
    return TOOL_DEPENDENCIES.get(tool_name, [])


def suggest_followup_tools(tool_name: str, result: str) -> list[str]:
    """Suggest next tools based on tool result."""
    suggestions = []

    if tool_name == "web_search" and result:
        suggestions.append("fetch_page")  # URLs to fetch

    if tool_name == "search_obsidian" and "matches" in result.lower():
        suggestions.append("read_obsidian_note")  # Notes to read

    if tool_name == "read_file" and "error" not in result.lower():
        suggestions.append("run_terminal")  # File looks good, maybe run it

    return suggestions


def build_tool_hint(tool_name: str, context: str = "") -> str:
    """Build a contextual hint for when to use a tool."""
    schema = get_tool_schema(tool_name)
    if not schema:
        return ""

    hints = [schema.get("description", "")[:100]]

    if "examples" in schema and schema["examples"]:
        ex = schema["examples"][0]
        if "query" in ex:
            hints.append(f"Try: {ex['query']}")
        elif "command" in ex:
            hints.append(f"Try: {ex['command']}")
        elif "path" in ex:
            hints.append(f"Try: {ex['path']}")

    return " | ".join(hints)
