#!/usr/bin/env python3
"""
Tool selector with reasoning for open-agent.
Analyzes query and selects optimal tools with explainable decisions.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from open_agent.enhanced.schemas import TOOL_SCHEMAS, is_parallel_safe


@dataclass
class ToolDecision:
    """A decision about which tool to use."""
    tool: str
    reasoning: str
    confidence: float  # 0.0 to 1.0
    args: dict = field(default_factory=dict)
    alternatives: list = field(default_factory=list)
    parallel_candidates: list = field(default_factory=list)


# Keyword → tool mapping with confidence scores
TOOL_KEYWORDS = {
    "web_search": {
        "keywords": [
            "search", "search for", "search the web", "find", "find information",
            "find out", "google", "bing", "look up", "look for",
            "what is", "who is", "where is", "when did", "why does",
            "how to", "how do", "can you find", "latest", "news",
            "current", "recent", "price", "weather", "stock", "score",
            "result", "information about", "tutorial", "guide", "review",
            "compare", "vs ", "released", "announced", "updated",
            "2024", "2025", "2026",
        ],
        "weight": 1.2,
    },
    "read_rss_by_name": {
        "keywords": [
            "rss", "feed", "hacker news", "subscribe",
            "latest articles", "blog posts",
        ],
        "weight": 1.0,
    },
    "cached_web_search": {
        "keywords": [
            "again", "repeat", "same thing", "already searched",
            "what about", "also check", "more about",
        ],
        "weight": 0.8,
    },
    "fetch_page": {
        "keywords": [
            "fetch", "download", "scrape", "get content", "read url",
            "extract from", "visit", "open url", "http", "https://",
        ],
        "weight": 1.0,
    },
    "run_terminal": {
        "keywords": [
            "run", "execute", "command", "shell", "bash", "python",
            "pip", "git", "npm", "install", "build", "compile",
            "script", "docker", "curl", "wget", "ssh", "ls", "cd",
            "mkdir", "rm", "cp", "mv", "cat", "grep", "awk",
            "cargo", "make", "cmake", "pytest", "node",
        ],
        "weight": 1.0,
    },
    "read_file": {
        "keywords": [
            "read", "view", "show", "cat", "open", "display",
            "file", "contents of", "look at", "check file", "inspect",
            "source", "code", "script", "config", "json", "yaml",
            "txt", "md", "py", "js", "sh", "html", "css",
        ],
        "weight": 0.9,
    },
    "write_file": {
        "keywords": [
            "write", "create", "save", "make", "generate", "new file",
            "save as", "output to", "put in", "store in",
            "build file", "generate file",
        ],
        "weight": 1.0,
    },
    "search_obsidian": {
        "keywords": [
            "search obsidian", "vault notes", "obsidian search",
            "my notes", "knowledge base", "search notes",
        ],
        "weight": 1.0,
    },
    "update_memory": {
        "keywords": [
            "remember", "note to self", "save fact", "don't forget",
            "keep in mind", "persist", "store for later",
        ],
        "weight": 0.9,
    },
    "read_memory": {
        "keywords": [
            "what do you remember", "recall", "memory", "what's saved",
            "remind me", "what have we discussed",
        ],
        "weight": 1.0,
    },
    "search_sessions": {
        "keywords": [
            "search history", "past conversations", "previous sessions",
            "what did we do", "before this", "earlier",
        ],
        "weight": 1.0,
    },
    "create_pptx": {
        "keywords": [
            "presentation", "slides", "powerpoint", "ppt", "deck",
            "slide deck", "presentation for",
        ],
        "weight": 1.0,
    },
    "read_rss_by_name": {
        "keywords": [
            "rss", "feed", "hacker news", "subscribe",
            "latest articles", "blog posts",
        ],
        "weight": 1.0,
    },
    "load_soul": {
        "keywords": [
            "complex task", "deep research", "detailed analysis",
            "full research", "comprehensive", "extended mode",
        ],
        "weight": 0.7,
    },
}


# Compound patterns for multi-tool tasks
COMPOUND_PATTERNS = [
    # Research patterns
    {
        "pattern": r"(search|research|find).+and (download|fetch|get)",
        "tools": ["web_search", "fetch_page"],
        "reasoning": "Research task: search then fetch details",
    },
    {
        "pattern": r"(search|research|find).+and (read|look at|view)",
        "tools": ["web_search", "read_file"],
        "reasoning": "Research task: search then read file",
    },
    # File + run patterns
    {
        "pattern": r"(create|write|make).+and (run|execute|test)",
        "tools": ["write_file", "run_terminal"],
        "reasoning": "Create file and run it",
    },
    {
        "pattern": r"(read|view|show).+and (run|execute)",
        "tools": ["read_file", "run_terminal"],
        "reasoning": "Read file and run it",
    },
    # Multiple search patterns
    {
        "pattern": r"(search|find).+,? (.+),? and (.+)",
        "tools": ["web_search", "web_search"],
        "reasoning": "Multiple searches in parallel",
    },
]


class ToolSelector:
    """
    Analyzes user query and selects optimal tools with reasoning.
    Does NOT call tools — just decides which to recommend.
    """

    def __init__(self):
        self._keyword_cache = {}
        self._compile_patterns()

    def _compile_patterns(self):
        """Compile regex patterns for efficiency."""
        self._compound_matcher = []
        for cp in COMPOUND_PATTERNS:
            try:
                self._compound_matcher.append((
                    re.compile(cp["pattern"], re.IGNORECASE),
                    cp["tools"],
                    cp["reasoning"],
                ))
            except re.error:
                pass

    def select(self, query: str) -> ToolDecision:
        """
        Analyze query and return tool decision with reasoning.
        
        Returns ToolDecision with:
        - tool: primary tool name
        - reasoning: why this tool was chosen
        - confidence: 0.0-1.0 confidence score
        - args: suggested arguments
        - alternatives: backup tools to try
        - parallel_candidates: tools that could run in parallel
        """
        query_lower = query.lower()
        scores = {}

        # Score each tool based on keyword matches
        for tool_name, config in TOOL_KEYWORDS.items():
            score = 0.0
            matches = 0

            for keyword in config["keywords"]:
                if keyword.lower() in query_lower:
                    score += config["weight"]
                    matches += 1

            if matches > 0:
                # Normalize by number of keywords (avoid favoring tools with many keywords)
                normalized_score = score / max(1, len(config["keywords"]) ** 0.5)
                scores[tool_name] = normalized_score

        # Check for compound patterns
        for pattern_re, tools, reasoning in self._compound_matcher:
            if pattern_re.search(query):
                if len(tools) >= 2:
                    primary = tools[0]
                    secondary = tools[1] if len(tools) > 1 else None

                    return ToolDecision(
                        tool=primary,
                        reasoning=reasoning,
                        confidence=0.95,
                        alternatives=[t for t in tools[1:] if t in scores],
                        parallel_candidates=[t for t in tools if is_parallel_safe(t)],
                    )

        # Check for URL patterns (fetch_page)
        if re.search(r"https?://", query):
            url_match = re.search(r"https?://\S+", query)
            if url_match:
                return ToolDecision(
                    tool="fetch_page",
                    reasoning="URL detected in query",
                    confidence=0.98,
                    args={"url": url_match.group(0)},
                )

        # Check for file paths
        path_match = re.search(r"/[\w/\-\.]+", query)
        if path_match and any(ext in path_match.group(0) for ext in [".py", ".md", ".txt", ".json", ".yaml", ".sh"]):
            return ToolDecision(
                tool="read_file",
                reasoning="File path detected",
                confidence=0.95,
                args={"path": path_match.group(0)},
            )

        # Check for shell commands
        shell_indicators = ["pip install", "git ", "python ", "npm ", "curl ", "docker "]
        for indicator in shell_indicators:
            if indicator in query_lower:
                return ToolDecision(
                    tool="run_terminal",
                    reasoning=f"Shell command detected ('{indicator.strip()}')",
                    confidence=0.98,
                    args={"command": query},
                )

        # Select best single tool
        if scores:
            sorted_tools = sorted(scores.items(), key=lambda x: -x[1])
            best_tool, best_score = sorted_tools[0]
            alternatives = [t for t, _ in sorted_tools[1:4] if t != best_tool]

            # Calculate confidence
            max_possible = sum(1.0 / (len(config["keywords"]) ** 0.5)
                              for config in TOOL_KEYWORDS.values())
            confidence = min(1.0, best_score / max_possible * 2)

            # Build reasoning
            schema = TOOL_SCHEMAS.get(best_tool, {})
            desc = schema.get("description", "")[:80]

            return ToolDecision(
                tool=best_tool,
                reasoning=f"Best match: {best_tool} (score: {best_score:.2f})",
                confidence=confidence,
                alternatives=alternatives,
                parallel_candidates=[t for t in alternatives if is_parallel_safe(t)][:2],
            )

        # No clear match
        return ToolDecision(
            tool="web_search",
            reasoning="No specific tool matched — using web_search as fallback",
            confidence=0.3,
            alternatives=["read_memory", "search_sessions"],
        )

    def select_multiple(self, query: str) -> list[ToolDecision]:
        """Select multiple tools for complex queries."""
        # Try compound patterns first
        for pattern_re, tools, reasoning in self._compound_matcher:
            if pattern_re.search(query):
                decisions = []
                for tool in tools:
                    decisions.append(ToolDecision(
                        tool=tool,
                        reasoning=reasoning,
                        confidence=0.9,
                    ))
                return decisions

        # Single tool
        return [self.select(query)]

    def suggest_next(self, current_tool: str, result: str) -> Optional[ToolDecision]:
        """Suggest next tool based on current result."""
        result_lower = result.lower()

        # After web search, suggest fetching URLs
        if current_tool == "web_search":
            if "url:" in result_lower or "http" in result_lower:
                return ToolDecision(
                    tool="fetch_page",
                    reasoning="Web search found URLs — fetch for details",
                    confidence=0.85,
                )

        # After search obsidian, suggest reading notes
        if current_tool == "search_obsidian":
            if "match" in result_lower or ".md" in result_lower:
                return ToolDecision(
                    tool="read_obsidian_note",
                    reasoning="Obsidian search found matches",
                    confidence=0.8,
                )

        # After read_file, suggest running
        if current_tool == "read_file":
            if "error" not in result_lower and "not found" not in result_lower:
                return ToolDecision(
                    tool="run_terminal",
                    reasoning="File looks good — consider running it",
                    confidence=0.6,
                )

        return None


# Global selector instance
selector = ToolSelector()


def select_tool(query: str) -> ToolDecision:
    """Convenience function for tool selection."""
    return selector.select(query)


def select_next(current_tool: str, result: str) -> Optional[ToolDecision]:
    """Suggest next tool based on result."""
    return selector.suggest_next(current_tool, result)
