"""
Enhanced tools package.
All improvements can be toggled on/off independently.
"""
from open_agent.enhanced.schemas import (
    TOOL_SCHEMAS,
    get_tool_schema,
    is_parallel_safe,
    get_error_recovery,
    suggest_followup_tools,
    build_tool_hint,
)

from open_agent.enhanced.auto_retry import (
    ErrorAnalyzer,
    analyze_error,
    format_error_with_hint,
    is_retryable_error,
    RetryableTool,
    with_retry,
    retry_with_fix,
    ErrorSummary,
    extract_install_commands,
    auto_install_missing_packages,
)

from open_agent.enhanced.parallel_runner import (
    ToolCall,
    ToolResult,
    ParallelRunner,
    parallel_web_search,
    parallel_file_read,
    parallel_fetch_page,
    ChainRunner,
)

from open_agent.enhanced.tool_selector import (
    ToolDecision,
    ToolSelector,
    select_tool,
    select_next,
    selector,
)

# Feature flags — toggle enhancements on/off
ENABLED = {
    "thinking_log": True,        # Hermes-style thinking logs
    "auto_retry": True,          # Smart error recovery
    "parallel_tools": True,      # Parallel tool execution
    "tool_selector": True,       # Smart tool selection
    "enhanced_schemas": True,    # Rich tool schemas
}

def is_enabled(feature: str) -> bool:
    """Check if a feature is enabled."""
    return ENABLED.get(feature, False)

def enable(feature: str):
    """Enable a feature."""
    ENABLED[feature] = True

def disable(feature: str):
    """Disable a feature."""
    ENABLED[feature] = False
