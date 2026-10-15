"""The single interface every model call goes through (S3_SPEC section 0 and 2).

Providers live in ``basis.llm.providers`` (Stage 3-B). ``basis.llm.provider`` holds the
protocol, the request/response types, the budget, and the scripted provider used by
tests and behavioral evals.
"""

from basis.llm.provider import (
    Budget,
    BudgetExceededError,
    ChatProvider,
    ChatRequest,
    ChatResponse,
    Message,
    ScriptedProvider,
    ScriptedTurn,
    ToolCall,
    ToolSpec,
    Usage,
)

__all__ = [
    "Budget",
    "BudgetExceededError",
    "ChatProvider",
    "ChatRequest",
    "ChatResponse",
    "Message",
    "ScriptedProvider",
    "ScriptedTurn",
    "ToolCall",
    "ToolSpec",
    "Usage",
]
