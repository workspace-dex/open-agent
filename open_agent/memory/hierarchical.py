#!/usr/bin/env python3
"""
3-tier hierarchical memory system for open-agent.
- Working Memory: Current turn only (very fast)
- Session Memory: Current session with compression (medium)
- Persistent Memory: Cross-session facts (slow, high-value)
"""
import json
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from open_agent._impl import CONFIG_DIR, MEMORY_FILE, USER_FILE


@dataclass
class Fact:
    """A single fact stored in memory."""
    key: str
    value: str
    importance: float = 0.5  # 0.0 to 1.0
    created_at: str = ""
    source: str = "session"  # session, user, obsidian
    tags: list = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


@dataclass
class MemoryResult:
    """Result from a memory query."""
    facts: list[Fact]
    relevance: float  # 0.0 to 1.0
    source: str


class WorkingMemory:
    """Tier 1: Ultra-fast, current turn only."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._context: dict[str, str] = {}

    def set(self, key: str, value: str):
        """Store a value for current turn."""
        self._store[key] = value

    def get(self, key: str, default: str = "") -> str:
        """Retrieve current turn value."""
        return self._store.get(key, default)

    def update_context(self, key: str, value: str):
        """Update context (survives across turns within session)."""
        self._context[key] = value

    def get_context(self, key: str, default: str = "") -> str:
        """Get context value."""
        return self._context.get(key, default)

    def clear(self):
        """Clear working memory (between turns)."""
        self._store.clear()
        # Context persists


class SessionMemory:
    """
    Tier 2: Session-scoped memory with SQLite storage.
    Compresses old turns into structured summaries.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (CONFIG_DIR / "memory.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """Initialize SQLite for session memory."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                created_at TEXT NOT NULL,
                source TEXT DEFAULT 'session',
                tags TEXT DEFAULT '[]'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS context (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_facts_key ON facts(key)
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                key, value, tags, content='facts', content_rowid='rowid'
            )
        """)
        conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def add_fact(self, key: str, value: str, importance: float = 0.5,
                 source: str = "session", tags: list = None) -> str:
        """Add a fact to session memory."""
        fact_id = uuid.uuid4().hex[:12]
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO facts (id, key, value, importance, created_at, source, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (fact_id, key, value, importance, datetime.now().isoformat(),
             source, json.dumps(tags or []))
        )
        conn.commit()
        return fact_id

    def search(self, query: str, limit: int = 10) -> list[MemoryResult]:
        """Search session memory for relevant facts."""
        conn = self._get_conn()
        results = []

        # Try FTS first
        try:
            rows = conn.execute("""
                SELECT f.*, snippet(facts_fts, 1, '[', ']', '...', 32) as snippet
                FROM facts_fts f
                WHERE facts_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, limit)).fetchall()
        except sqlite3.OperationalError:
            # Fallback to LIKE
            like_q = f"%{query}%"
            rows = conn.execute("""
                SELECT * FROM facts
                WHERE key LIKE ? OR value LIKE ?
                ORDER BY importance DESC
                LIMIT ?
            """, (like_q, like_q, limit)).fetchall()

        for row in rows:
            fact = Fact(
                key=row["key"],
                value=row["value"],
                importance=row["importance"],
                created_at=row["created_at"],
                source=row["source"],
                tags=json.loads(row.get("tags", "[]")),
            )
            # Calculate relevance based on importance
            relevance = fact.importance
            results.append(MemoryResult(facts=[fact], relevance=relevance, source=fact.source))

        return results

    def get_recent(self, limit: int = 20) -> list[Fact]:
        """Get most recent facts."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT * FROM facts
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

        return [
            Fact(
                key=r["key"],
                value=r["value"],
                importance=r["importance"],
                created_at=r["created_at"],
                source=r["source"],
                tags=json.loads(r.get("tags", "[]")),
            )
            for r in rows
        ]

    def update_context(self, key: str, value: str):
        """Update session context."""
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO context (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, value, datetime.now().isoformat()))
        conn.commit()

    def get_context(self, key: str) -> Optional[str]:
        """Get session context value."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT value FROM context WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def clear_session(self):
        """Clear all session facts (keep persistent)."""
        conn = self._get_conn()
        conn.execute("DELETE FROM facts WHERE source = 'session'")
        conn.execute("DELETE FROM context")
        conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


class PersistentMemory:
    """
    Tier 3: Cross-session persistent memory.
    Reads/writes MEMORY.md and USER.md files.
    """

    def __init__(self, memory_file: Optional[Path] = None,
                 user_file: Optional[Path] = None):
        self.memory_file = memory_file or MEMORY_FILE
        self.user_file = user_file or USER_FILE

    def read(self, max_chars: int = 3000) -> str:
        """Read persistent memory."""
        if not self.memory_file.exists():
            return ""
        try:
            return self.memory_file.read_text(encoding="utf-8")[:max_chars]
        except Exception:
            return ""

    def write(self, content: str, append: bool = False):
        """Write to persistent memory."""
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        if append and self.memory_file.exists():
            existing = self.memory_file.read_text(encoding="utf-8", errors="ignore")
            self.memory_file.write_text(existing + "\n\n" + content, encoding="utf-8")
        else:
            self.memory_file.write_text(content, encoding="utf-8")

    def read_user(self, max_chars: int = 1500) -> str:
        """Read user profile."""
        if not self.user_file.exists():
            return ""
        try:
            return self.user_file.read_text(encoding="utf-8")[:max_chars]
        except Exception:
            return ""

    def write_user(self, content: str, append: bool = False):
        """Write user profile."""
        self.user_file.parent.mkdir(parents=True, exist_ok=True)
        if append and self.user_file.exists():
            existing = self.user_file.read_text(encoding="utf-8", errors="ignore")
            self.user_file.write_text(existing + "\n\n" + content, encoding="utf-8")
        else:
            self.user_file.write_text(content, encoding="utf-8")

    def search(self, query: str, max_results: int = 5) -> list[str]:
        """Search persistent memory for mentions."""
        if not self.memory_file.exists():
            return []

        try:
            content = self.memory_file.read_text(encoding="utf-8", errors="ignore")
            lines = content.split("\n")
            matches = []
            query_lower = query.lower()

            for i, line in enumerate(lines):
                if query_lower in line.lower():
                    # Get context around match
                    start = max(0, i - 1)
                    end = min(len(lines), i + 3)
                    snippet = "\n".join(lines[start:end])
                    matches.append(snippet)

                    if len(matches) >= max_results:
                        break

            return matches
        except Exception:
            return []

    def extract_facts(self, text: str) -> list[Fact]:
        """Extract structured facts from text."""
        facts = []

        # Pattern: "Key: Value" or "Key - Value"
        for pattern in [r"([A-Za-z\s]+):\s*(.+)", r"([A-Za-z\s]+)\s*-\s*(.+)"]:
            for match in re.finditer(pattern, text):
                key = match.group(1).strip()
                value = match.group(2).strip()
                if len(key) > 2 and len(value) > 2:
                    facts.append(Fact(key=key, value=value, importance=0.7, source="extracted"))

        return facts

    def add_fact(self, key: str, value: str, importance: float = 0.7):
        """Add a fact to persistent memory."""
        fact_line = f"- **{key}**: {value}"
        self.write(fact_line, append=True)

    def get_fact(self, key: str) -> Optional[str]:
        """Get a specific fact."""
        if not self.memory_file.exists():
            return None

        try:
            content = self.memory_file.read_text(encoding="utf-8", errors="ignore")
            pattern = rf"{re.escape(key)}[:\s]+(.+)"
            match = re.search(pattern, content, re.IGNORECASE)
            return match.group(1).strip() if match else None
        except Exception:
            return None


class HierarchicalMemory:
    """
    Complete 3-tier memory system.
    Query across all tiers with automatic relevance scoring.
    """

    def __init__(self):
        self.working = WorkingMemory()
        self.session = SessionMemory()
        self.persistent = PersistentMemory()

    def remember(self, key: str, value: str, importance: float = 0.7,
                 persist: bool = True, session: bool = True):
        """Store a fact in multiple memory tiers."""
        self.working.set(key, value)

        if session:
            self.session.add_fact(key, value, importance, source="session")

        if persist and importance >= 0.6:
            self.persistent.add_fact(key, value, importance)

    def recall(self, query: str, limit: int = 10) -> list[MemoryResult]:
        """Recall facts across all memory tiers."""
        results = []

        # Working memory (highest priority)
        working_value = self.working.get(query)
        if working_value:
            results.append(MemoryResult(
                facts=[Fact(key=query, value=working_value, importance=1.0)],
                relevance=1.0,
                source="working"
            ))

        # Session memory
        session_results = self.session.search(query, limit=limit)
        results.extend(session_results)

        # Persistent memory
        persistent_matches = self.persistent.search(query, limit=limit)
        for match in persistent_matches:
            facts = self.persistent.extract_facts(match)
            if facts:
                results.append(MemoryResult(facts=facts, relevance=0.6, source="persistent"))

        return results[:limit]

    def get_context(self, key: str) -> str:
        """Get context from any tier."""
        # Priority: working > session > persistent
        val = self.working.get_context(key)
        if val:
            return val

        val = self.session.get_context(key)
        if val:
            return val

        return ""

    def update_context(self, key: str, value: str):
        """Update context across tiers."""
        self.working.update_context(key, value)
        self.session.update_context(key, value)

    def inject_for_prompt(self, max_chars: int = 2000) -> str:
        """Generate memory injection text for LLM prompt."""
        parts = []

        # Persistent memory
        persistent = self.persistent.read(max_chars=max_chars // 2)
        if persistent:
            parts.append("[Persistent Memory]\n" + persistent)

        # Session context
        recent = self.session.get_recent(limit=5)
        if recent:
            ctx_lines = [f"- {f.key}: {f.value}" for f in recent]
            parts.append("[Recent Session Facts]\n" + "\n".join(ctx_lines))

        # Working context
        if self.working._context:
            ctx_lines = [f"- {k}: {v}" for k, v in self.working._context.items()]
            parts.append("[Current Context]\n" + "\n".join(ctx_lines))

        result = "\n\n".join(parts)
        return result[:max_chars]

    def clear_session(self):
        """Clear session memory (preserve persistent)."""
        self.session.clear_session()
        self.working.clear()

    def close(self):
        """Cleanup connections."""
        self.session.close()
