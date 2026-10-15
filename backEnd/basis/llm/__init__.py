"""The single interface every model call goes through (S3_SPEC section 0 and 2).

Providers live in ``basis.llm.providers``; ``basis.llm.pool`` chooses between them,
``basis.llm.cost`` prices calls, ``basis.llm.recorder`` writes ``llm_calls`` rows and
``basis.llm.prompts`` loads the versioned prompt files. ``basis.llm.provider`` holds the
protocol, the request/response types, the budget, and the scripted provider used by
tests and behavioral evals.
"""

from basis.llm.provider import (
    Budget,
    BudgetExceededError,
    ChatProvider,
    ChatRequest,
    ChatResponse,
    ImagePart,
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
    "ImagePart",
    "Message",
    "ScriptedProvider",
    "ScriptedTurn",
    "ToolCall",
    "ToolSpec",
    "Usage",
]
