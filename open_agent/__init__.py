"""Open-Agent — local AI agent for consumer hardware."""

# Core
from ._impl import (
    agent, Agent, RunContext, ModelMessage,
    CFG, DEFAULT_CONFIG, load_config, save_config,
    CONFIG_DIR, SESSIONS_DIR, CONFIG_FILE, MEMORY_FILE, USER_FILE,
    SessionDB, Session, AgentSession, AgentRunner,
    truncate_result, _normalize_output,
    _w, _ln, _is_dangerous, _analyze_error,
    run_setup_wizard, main as opagent_main, main_sync,
    console, ToolBadge, td,
    TOOLSETS, _detect_toolset, get_toolset_tools,
    web_search, cached_web_search, smart_research, fetch_page,
    run_terminal, read_file, write_file,
    update_memory, read_memory, search_sessions, update_user_profile,
    create_pptx, load_soul,
)

# Agent loop with parallel tool calling
from .agent_loop import AgentLoop, TOOL_SCHEMAS, _should_parallelize

# Tool cache
from .tool_cache import (
    ToolCache, get_cached_result, cache_tool_result,
    get_tool_cache,
)

# Enhanced tools (optional — adds hermes-agent-like features)
try:
    from .enhanced import (
        ENABLED, is_enabled, enable, disable,
        ErrorAnalyzer, analyze_error, format_error_with_hint, retry_with_fix,
        ParallelRunner, ToolCall, ToolResult,
        ToolSelector, ToolDecision, select_tool,
        TOOL_SCHEMAS, get_tool_schema, is_parallel_safe, build_tool_hint,
    )
    _ENHANCED_AVAILABLE = True
except ImportError:
    _ENHANCED_AVAILABLE = False
    ENABLED = {}

# Memory
try:
    from .memory.hierarchical import (
        HierarchicalMemory, WorkingMemory, SessionMemory, PersistentMemory,
        Fact, MemoryResult,
    )
except ImportError:
    pass

# Thinking logger
try:
    from .thinking_logger import (
        ThinkingChain, ThinkingLogger, run_with_thinking_log,
        detect_planning_text, format_error_hint,
    )
except ImportError:
    pass

__version__ = "1.0.0"
__all__ = [
    "agent", "CFG", "SessionDB", "Session", "AgentSession", "AgentRunner",
    "opagent_main", "main_sync", "console", "truncate_result",
    "ToolCache", "get_cached_result", "cache_tool_result",
    "TOOLSETS", "_detect_toolset", "get_toolset_tools",
]