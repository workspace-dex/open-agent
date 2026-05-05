#!/usr/bin/env python3
"""
Tool Result Cache - Cache duplicate tool calls to save API tokens.

For small models (gemma-4-4B), avoiding redundant tool calls 
improves token efficiency and response time.

Usage:
    from open_agent.tool_cache import ToolCache, get_cached_result
    
    # Check cache before executing tool
    cache = ToolCache()
    cached = cache.get("web_search", "weather in delhi")
    if cached:
        return cached
    
    # After tool execution, cache result
    cache.set("web_search", "weather in delhi", result)
"""

import hashlib
import json
import time
from typing import Optional, Dict, Any, List
from pathlib import Path


class ToolCache:
    """LRU cache for tool results keyed by (tool_name, args_hash)."""
    
    def __init__(self, max_entries: int = 100, ttl_seconds: float = 3600):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._cache_dir = Path.home() / ".config" / "open-agent"
        self._cache_file = self._cache_dir / "tool_cache.json"
        self._load()
    
    def _make_key(self, tool_name: str, args: str) -> str:
        """Create cache key from tool name + args hash."""
        args_str = json.dumps(args, sort_keys=True)
        hash_val = hashlib.sha256(f"{tool_name}:{args_str}".encode()).hexdigest()[:24]
        return f"{tool_name}:{hash_val}"
    
    def _load(self):
        """Load cache from disk on init."""
        if not self._cache_file.exists():
            return
        try:
            data = json.loads(self._cache_file.read_text())
            # Filter expired entries
            now = time.time()
            self._cache = {
                k: v for k, v in data.items()
                if now - v.get("_ts", 0) < self._ttl
            }
        except Exception:
            pass
    
    def _save(self):
        """Persist cache to disk."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file.write_text(json.dumps(self._cache, default=str))
    
    def get(self, tool_name: str, args: str) -> Optional[str]:
        """Get cached result if exists and not expired."""
        key = self._make_key(tool_name, str(args))
        entry = self._cache.get(key)
        if not entry:
            return None
        # Check TTL
        if time.time() - entry.get("_ts", 0) > self._ttl:
            del self._cache[key]
            return None
        return entry.get("result")
    
    def set(self, tool_name: str, args: str, result: str):
        """Cache a tool result."""
        key = self._make_key(tool_name, str(args))
        # Evict if full
        if len(self._cache) >= self._max_entries:
            # Remove oldest entry
            oldest = min(self._cache.items(), key=lambda x: x[1].get("_ts", 0))
            del self._cache[oldest[0]]
        
        self._cache[key] = {
            "result": result,
            "_ts": time.time(),
            "_tool": tool_name,
            "_args": str(args)[:100],  # Keep short for debug
        }
        self._save()
    
    def clear(self):
        """Clear all cached results."""
        self._cache.clear()
        self._save()
    
    def stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        return {
            "entries": len(self._cache),
            "max": self._max_entries,
            "ttl_seconds": self._ttl,
        }


# Global instance
_cache: Optional[ToolCache] = None


def get_tool_cache() -> ToolCache:
    """Get global cache instance."""
    global _cache
    if _cache is None:
        _cache = ToolCache()
    return _cache


def get_cached_result(tool_name: str, args: str) -> Optional[str]:
    """Convenience function to check cache."""
    return get_tool_cache().get(tool_name, args)


def cache_tool_result(tool_name: str, args: str, result: str):
    """Convenience function to cache result."""
    get_tool_cache().set(tool_name, args, result)
