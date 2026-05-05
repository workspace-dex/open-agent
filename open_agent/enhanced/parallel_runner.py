#!/usr/bin/env python3
"""
Parallel tool execution for open-agent.
Run independent tools simultaneously for maximum efficiency.
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Union

from open_agent.enhanced.schemas import is_parallel_safe


@dataclass
class ToolCall:
    """Represents a single tool call."""
    name: str
    func: Callable[..., Awaitable[str]]
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    priority: int = 0  # Higher = run first
    timeout: float = 30.0


@dataclass
class ToolResult:
    """Result from a tool execution."""
    name: str
    result: Union[str, Exception]
    success: bool
    duration: float
    error_type: Optional[str] = None
    error_hint: Optional[str] = None


class ParallelRunner:
    """
    Execute multiple tools in parallel when they are independent.
    
    Usage:
        runner = ParallelRunner()
        runner.add("web_search", web_search_tool, query="AI news")
        runner.add("web_search", web_search_tool, query="tech news")
        results = await runner.run_all()
    """

    def __init__(self, max_concurrent: int = 4):
        self.max_concurrent = max_concurrent
        self.calls: list[ToolCall] = []

    def add(
        self,
        name: str,
        func: Callable[..., Awaitable[str]],
        *args,
        priority: int = 0,
        timeout: float = 30.0,
        **kwargs
    ):
        """Add a tool call to the execution queue."""
        self.calls.append(ToolCall(
            name=name,
            func=func,
            args=args,
            kwargs=kwargs,
            priority=priority,
            timeout=timeout,
        ))

    def add_if_parallel(
        self,
        name: str,
        func: Callable[..., Awaitable[str]],
        *args,
        **kwargs
    ):
        """Add tool only if it's safe to run in parallel."""
        if is_parallel_safe(name):
            self.add(name, func, *args, **kwargs)
        else:
            raise ValueError(f"Tool '{name}' is not safe for parallel execution")

    async def _run_one(self, call: ToolCall) -> ToolResult:
        """Execute a single tool call with timing."""
        start = time.monotonic()
        error_type = None
        error_hint = None

        try:
            result = await asyncio.wait_for(
                call.func(*call.args, **call.kwargs),
                timeout=call.timeout
            )
            success = not self._looks_like_error(result)
            if not success:
                from open_agent.enhanced.auto_retry import analyze_error
                analysis = analyze_error(result)
                error_type = analysis["type"]
                error_hint = analysis["fix"]
            return ToolResult(
                name=call.name,
                result=result,
                success=success,
                duration=time.monotonic() - start,
                error_type=error_type,
                error_hint=error_hint,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                name=call.name,
                result=f"Timeout after {call.timeout}s",
                success=False,
                duration=time.monotonic() - start,
                error_type="Timeout",
                error_hint="Command took too long. Optimize or increase timeout.",
            )
        except Exception as e:
            return ToolResult(
                name=call.name,
                result=str(e),
                success=False,
                duration=time.monotonic() - start,
                error_type=type(e).__name__,
                error_hint=str(e)[:200],
            )

    def _looks_like_error(self, result: str) -> bool:
        """Check if result looks like an error."""
        if not isinstance(result, str):
            return False
        error_indicators = ["error", "failed", "exception", "traceback",
                           "✗", "not found", "denied", "no such"]
        return any(indicator in result.lower() for indicator in error_indicators)

    async def run_all(self) -> list[ToolResult]:
        """Execute all queued tools in parallel (up to max_concurrent)."""
        if not self.calls:
            return []

        # Sort by priority (higher first)
        sorted_calls = sorted(self.calls, key=lambda c: -c.priority)

        # Execute with semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def run_with_sem(call: ToolCall) -> ToolResult:
            async with semaphore:
                return await self._run_one(call)

        tasks = [run_with_sem(call) for call in sorted_calls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to ToolResult
        processed = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed.append(ToolResult(
                    name=sorted_calls[i].name,
                    result=str(result),
                    success=False,
                    duration=0,
                    error_type=type(result).__name__,
                    error_hint=str(result)[:200],
                ))
            else:
                processed.append(result)

        self.calls.clear()  # Clear for next batch
        return processed

    async def run_until_first_success(self) -> Optional[ToolResult]:
        """Run until one succeeds, cancel the rest."""
        if not self.calls:
            return None

        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def run_with_sem(call: ToolCall) -> ToolResult:
            async with semaphore:
                return await self._run_one(call)

        tasks = [asyncio.create_task(run_with_sem(call)) for call in self.calls]

        try:
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED
            )

            # Cancel remaining
            for task in pending:
                task.cancel()

            # Get the result
            completed = done.pop()
            result = completed.result()

            self.calls.clear()
            return result

        except Exception as e:
            for task in tasks:
                task.cancel()
            raise


# ── Convenience functions ─────────────────────────────────────────────────────

async def parallel_web_search(
    queries: list[str],
    search_func: Callable[..., Awaitable[str]],
    max_concurrent: int = 4
) -> list[tuple[str, str]]:
    """
    Run multiple web searches in parallel.
    
    Returns: [(query, result), ...]
    """
    runner = ParallelRunner(max_concurrent=max_concurrent)
    for q in queries:
        runner.add("web_search", search_func, query=q)

    results = await runner.run_all()
    return [(q, r.result) for q, r in zip(queries, results)]


async def parallel_file_read(
    paths: list[str],
    read_func: Callable[..., Awaitable[str]],
    max_concurrent: int = 4
) -> list[tuple[str, str]]:
    """Read multiple files in parallel."""
    runner = ParallelRunner(max_concurrent=max_concurrent)
    for path in paths:
        runner.add("read_file", read_func, path=path)

    results = await runner.run_all()
    return [(p, r.result) for p, r in zip(paths, results)]


async def parallel_fetch_page(
    urls: list[str],
    fetch_func: Callable[..., Awaitable[str]],
    max_concurrent: int = 4
) -> list[tuple[str, str]]:
    """Fetch multiple URLs in parallel."""
    runner = ParallelRunner(max_concurrent=max_concurrent)
    for url in urls:
        runner.add("fetch_page", fetch_func, url=url)

    results = await runner.run_all()
    return [(u, r.result) for u, r in zip(urls, results)]


# ── Chaining with dependencies ─────────────────────────────────────────────────

class ChainRunner:
    """
    Run tools in a dependency chain.
    Tools in the same "layer" run in parallel.
    """

    def __init__(self):
        self.layers: list[list[ToolCall]] = [[]]

    def add(self, name: str, func: Callable[..., Awaitable[str]],
            *args, layer: int = -1, **kwargs):
        """Add tool to a layer (-1 = last layer)."""
        if layer == -1:
            layer = len(self.layers) - 1

        # Expand layers if needed
        while len(self.layers) <= layer:
            self.layers.append([])

        self.layers[layer].append(ToolCall(
            name=name,
            func=func,
            args=args,
            kwargs=kwargs,
        ))

    async def run(self) -> list[list[ToolResult]]:
        """Execute all layers sequentially, tools within layers in parallel."""
        all_results = []

        for layer in self.layers:
            if not layer:
                continue

            runner = ParallelRunner(max_concurrent=len(layer))
            for call in layer:
                runner.calls.append(call)

            layer_results = await runner.run_all()
            all_results.append(layer_results)

        return all_results
