"""Smoke tests for the agent."""
import pytest

def test_agent_import():
    """Agent can be imported."""
    from open_agent import agent
    assert agent is not None

def test_agent_type():
    """Agent is the right type."""
    from open_agent import agent
    from pydantic_ai import Agent
    assert isinstance(agent, Agent)
