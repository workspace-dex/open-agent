#!/usr/bin/env python3
"""
Smart error recovery for open-agent tools.
Analyzes errors and automatically retries with fixes.
"""
import asyncio
import re
from typing import Callable, Any, Optional, Awaitable, Union

# Error patterns and their fixes
ERROR_PATTERNS = {
    # Python errors
    r"NameError: name '(\w+)' is not defined": {
        "type": "NameError",
        "fix": lambda m: f"Define the variable first: {m.group(1)} = <value>",
        "auto_fix": False,
    },
    r'NameError: name "(\w+)" is not defined': {
        "type": "NameError",
        "fix": lambda m: f"Define the variable first: {m.group(1)} = <value>",
        "auto_fix": False,
    },
    r"NameError": {
        "type": "NameError",
        "fix": "Variable not defined. Define it first: variable_name = <value>",
        "auto_fix": False,
    },
    r"SyntaxError: invalid syntax": {
        "type": "SyntaxError",
        "fix": "Check parentheses, colons, brackets. Compare with working Python code.",
        "auto_fix": False,
    },
    r"IndentationError": {
        "type": "IndentationError",
        "fix": "Use consistent 4 spaces for indentation, not tabs.",
        "auto_fix": False,
    },
    r"IndentationError: unexpected indent": {
        "type": "IndentationError",
        "fix": "Remove extra indentation or fix nesting.",
        "auto_fix": False,
    },
    r'ImportError: No module named "(\w+)"|ImportError: No module named \'(\w+)\'': {
        "type": "ImportError",
        "fix": lambda m: f"pip install {m.group(1) or m.group(2)}",
        "auto_fix": True,
        "install_hint": lambda m: f"pip install {m.group(1) or m.group(2)}",
    },
    r"ModuleNotFoundError": {
        "type": "ModuleNotFoundError",
        "fix": "Module not installed. Run: pip install <module-name>",
        "auto_fix": False,
    },
    r"FileNotFoundError": {
        "type": "FileNotFoundError",
        "fix": "Path not found. Check the path is correct, or create parent directories first.",
        "auto_fix": False,
    },
    r"PermissionError": {
        "type": "PermissionError",
        "fix": "Permission denied. Use a path in ~/ or check file permissions.",
        "auto_fix": False,
    },
    r"IsADirectoryError": {
        "type": "IsADirectoryError",
        "fix": "Expected a file but got a directory. Check the path.",
        "auto_fix": False,
    },
    r"JSONDecodeError": {
        "type": "JSONDecodeError",
        "fix": "Invalid JSON. Check syntax — matching quotes, brackets, commas.",
        "auto_fix": False,
    },
    r"TimeoutExpired": {
        "type": "TimeoutExpired",
        "fix": "Command timed out (30s). Optimize or use a simpler approach.",
        "auto_fix": False,
    },
    r"ConnectionError": {
        "type": "ConnectionError",
        "fix": "Network error. Check URL or internet connection.",
        "auto_fix": False,
    },
    r"HTTPError: (\d+)": {
        "type": "HTTPError",
        "fix": lambda m: f"HTTP {m.group(1)} error. Check URL.",
        "auto_fix": False,
    },

    # Git errors
    r"fatal: (.+)": {
        "type": "GitError",
        "fix": lambda m: f"Git error: {m.group(1)}",
        "auto_fix": False,
    },

    # Shell errors
    r"command not found": {
        "type": "CommandNotFound",
        "fix": "Command not found. Check spelling or install the tool.",
        "auto_fix": False,
    },
    r"no such option": {
        "type": "InvalidOption",
        "fix": "Invalid flag/option. Check command syntax.",
        "auto_fix": False,
    },

    # Process errors
    r"Exit code (\d+)": {
        "type": "NonZeroExit",
        "fix": lambda m: f"Command exited with code {m.group(1)}. Check for errors.",
        "auto_fix": False,
    },

    # Python tracebacks
    r"Traceback \(most recent call last\):": {
        "type": "Traceback",
        "fix": "See traceback above for specific error type.",
        "auto_fix": False,
    },
}

# Retry backoff strategy
RETRY_BACKOFF = [0.5, 1.5, 3.0]  # seconds between retries


class ErrorAnalyzer:
    """Analyzes errors and provides actionable fixes."""

    def __init__(self):
        self._compiled = []
        for pattern, info in ERROR_PATTERNS.items():
            try:
                self._compiled.append((re.compile(pattern, re.IGNORECASE | re.MULTILINE), info))
            except re.error:
                pass

    def analyze(self, error_text: str) -> dict:
        """Analyze error text and return fix info."""
        error_text = error_text[:2000]  # Limit analysis

        for pattern_re, info in self._compiled:
            match = pattern_re.search(error_text)
            if match:
                fix_text = info["fix"]
                if callable(fix_text):
                    fix_text = fix_text(match)

                return {
                    "type": info["type"],
                    "fix": fix_text,
                    "auto_fix": info.get("auto_fix", False),
                    "install_hint": info.get("install_hint", lambda m: None)(match)
                                   if "install_hint" in info and match else None,
                    "match": match.group(0)[:200] if match else "",
                }

        # No specific match — provide generic advice
        if "error" in error_text.lower():
            return {
                "type": "Unknown",
                "fix": "An error occurred. Check the output above.",
                "auto_fix": False,
                "install_hint": None,
                "match": error_text[:200],
            }

        return {
            "type": "Unknown",
            "fix": "No clear error found. Verify the command or input.",
            "auto_fix": False,
            "install_hint": None,
            "match": "",
        }


# Global analyzer instance
_analyzer = ErrorAnalyzer()


def analyze_error(error_text: str) -> dict:
    """Convenience function for error analysis."""
    return _analyzer.analyze(error_text)


def format_error_with_hint(error_text: str) -> str:
    """Format error text with actionable hint."""
    analysis = analyze_error(error_text)
    hint = analysis["fix"]

    output = error_text[:500]
    if hint:
        output += f"\n\n💡 Hint: {hint}"

    return output


def extract_error_type(error_text: str) -> str:
    """Quick extract error type from text."""
    return analyze_error(error_text)["type"]


def is_retryable_error(error_text: str) -> bool:
    """Check if error might succeed on retry."""
    retryable = {"timeout", "connectionerror", "httperror"}
    analysis = analyze_error(error_text)
    return analysis["type"].lower() in retryable


class RetryableTool:
    """
    Wrapper that adds smart retry to any tool function.
    Usage:
        async def safe_web_search(...):
            return await with_retry(web_search, ctx, query)
    """

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries

    async def __call__(
        self,
        func: Callable[..., Awaitable[str]],
        *args,
        **kwargs
    ) -> str:
        """Call function with retry logic."""
        last_error = ""

        for attempt in range(self.max_retries):
            try:
                result = await func(*args, **kwargs)

                # Check if result looks like an error
                if isinstance(result, str):
                    error_indicators = ["error", "failed", "exception", "traceback", "✗"]
                    if any(indicator in result.lower() for indicator in error_indicators):
                        last_error = result
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                            continue

                return result

            except Exception as e:
                last_error = str(e)
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])

        # All retries failed
        return f"Failed after {self.max_retries} attempts.\n\n{format_error_with_hint(last_error)}"


# Default instance
with_retry = RetryableTool(max_retries=3)


async def retry_with_fix(
    func: Callable[..., Awaitable[str]],
    *args,
    **kwargs
) -> str:
    """
    Call a tool function with automatic retry + hint injection.
    Returns result with actionable error hints if all retries fail.
    """
    return await with_retry(func, *args, **kwargs)


# ── Auto-fix utilities ────────────────────────────────────────────────────────

def extract_install_commands(error_text: str) -> list[str]:
    """Extract pip install commands from error hints."""
    commands = []
    for match in re.finditer(r"pip install (\S+)", error_text):
        pkg = match.group(1).rstrip("'\",")
        commands.append(f"pip install {pkg}")
    return commands


async def auto_install_missing_packages(error_text: str) -> tuple[bool, str]:
    """Try to auto-install packages mentioned in error."""
    import subprocess

    commands = extract_install_commands(error_text)
    if not commands:
        return False, "No install commands found"

    results = []
    for cmd in commands[:3]:  # Limit to 3
        try:
            proc = subprocess.run(
                cmd.split(),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode == 0:
                results.append(f"✓ {cmd}")
            else:
                results.append(f"✗ {cmd}: {proc.stderr[:100]}")
        except Exception as e:
            results.append(f"✗ {cmd}: {e}")

    success = any(r.startswith("✓") for r in results)
    return success, "\n".join(results)


def suggest_file_path_fixes(error_text: str) -> str:
    """Suggest file path fixes based on error."""
    # Try to extract the problematic path
    path_match = re.search(r"['\"](.+?)['\"]", error_text)
    if not path_match:
        return ""

    path = path_match.group(1)
    if not path.startswith("/"):
        return ""

    suggestions = []

    # Suggest home-expanded version
    home = "/home/"
    if path.startswith("/home/"):
        user_part = path.split("/")[2] if len(path.split("/")) > 2 else ""
        suggestions.append(f"Try: ~/{path[len(home) + len(user_part) + 1:]}")

    if suggestions:
        return " | ".join(suggestions)
    return ""


# ── Error aggregation for multi-tool tasks ────────────────────────────────────

class ErrorSummary:
    """Collect and summarize errors across multiple tool calls."""

    def __init__(self):
        self.errors: list[dict] = []

    def add(self, tool: str, error: str, attempt: int = 0):
        analysis = analyze_error(error)
        self.errors.append({
            "tool": tool,
            "error": error[:200],
            "type": analysis["type"],
            "fix": analysis["fix"],
            "attempt": attempt,
        })

    def has_critical(self) -> bool:
        """Check for unrecoverable errors."""
        critical_types = {"permissionerror", "isadirectoryerror"}
        return any(e["type"].lower() in critical_types for e in self.errors)

    def summary(self) -> str:
        """Get human-readable summary."""
        if not self.errors:
            return "No errors."

        lines = [f"{len(self.errors)} error(s) occurred:"]
        for e in self.errors:
            lines.append(f"  • {e['tool']}: {e['type']} — {e['fix']}")

        return "\n".join(lines)

    def retry_plan(self) -> str:
        """Generate a plan for retrying failed operations."""
        retryable = [e for e in self.errors if is_retryable_error(e["error"])]
        if not retryable:
            return "No retryable errors found."

        tools = [e["tool"] for e in retryable]
        return f"Retry: {', '.join(set(tools))}"
