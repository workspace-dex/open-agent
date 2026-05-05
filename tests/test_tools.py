"""Tests for tool registration."""
import pytest

def test_truncate_result():
    """truncate_result() works."""
    from open_agent import truncate_result
    result = truncate_result("x" * 20000)
    assert len(result) < 20000
    assert "[Truncated:" in result

def test_truncate_small():
    """truncate_result() leaves small strings alone."""
    from open_agent import truncate_result
    result = truncate_result("hello world")
    assert result == "hello world"

def test_cfg_defaults():
    """CFG has the right defaults."""
    from open_agent import CFG
    assert CFG.get("memory_nudge_interval") == 10
    assert CFG.get("max_tool_result_chars") == 12000
    assert CFG.get("max_summary_tokens") == 800
