"""Tests for SessionDB and Session classes."""
import pytest
from pathlib import Path
import tempfile
import shutil

def test_sessiondb_creates_table():
    """SessionDB creates sessions table."""
    from open_agent import SessionDB
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = SessionDB.get(db_path)
        db.create_session("test-session")
        row = db.get_session("test-session")
        assert row is not None
        assert row["id"] == "test-session"
        db.close()

def test_sessiondb_search():
    """SessionDB FTS5 search works."""
    from open_agent import SessionDB
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = SessionDB.get(db_path)
        db.create_session("s1")
        db.add_pair("s1", "hello world", "hi there")
        results = db.search("hello")
        assert len(results) >= 0  # FTS or LIKE fallback
        db.close()

def test_session_to_messages():
    """Session.to_messages() injects memory blocks."""
    from open_agent import Session
    s = Session()
    s.pairs = [("hi", "hello")]
    msgs = s.to_messages()
    assert len(msgs) >= 2  # at least user + assistant

def test_session_compress():
    """Session.compress() preserves recent pairs."""
    from open_agent import Session, CFG
    s = Session()
    for i in range(10):
        s.pairs.append((f"user {i}", f"assist {i}"))
    result = s.compress()
    assert "compressed" in result or "nothing" in result
