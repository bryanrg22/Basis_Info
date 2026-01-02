"""
LangSmith integration for observability, tracing, and debugging.

Provides:
- Automatic trace configuration from environment
- Custom tracer for Basis-specific events
- Decorator for tracing functions
"""

import os
from contextlib import contextmanager
from functools import lru_cache, wraps
from typing import Any, Callable, Optional, TypeVar

from langsmith import Client
from langsmith.run_trees import RunTree

from ..config.settings import get_settings


F = TypeVar("F", bound=Callable[..., Any])


def configure_langsmith() -> Optional[Client]:
    """
    Configure LangSmith from environment variables.

    Required env vars:
    - LANGCHAIN_API_KEY: LangSmith API key
    - LANGCHAIN_PROJECT: Project name (default: "basis-agentic")
    - LANGCHAIN_TRACING_V2: Enable tracing (default: true)

    Returns:
        LangSmith client if configured, None otherwise
    """
    settings = get_settings()

    if not settings.is_langsmith_configured():
        return None

    # Set environment variables for LangChain
    os.environ["LANGCHAIN_TRACING_V2"] = str(settings.langchain_tracing_v2).lower()
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
    os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key

    return Client()


class BasisTracer:
    """
    Custom tracer for Basis workflow observability.

    Provides structured logging for:
    - Evidence searches
    - Asset classifications
    - Workflow stage transitions
    - Error tracking
    """

    def __init__(self):
        self._client: Optional[Client] = None
        self._project: str = get_settings().langchain_project

    @property
    def client(self) -> Optional[Client]:
        """Lazy-load LangSmith client."""
        if self._client is None:
            self._client = configure_langsmith()
        return self._client

    @property
    def is_enabled(self) -> bool:
        """Check if tracing is enabled."""
        return self.client is not None

    @contextmanager
    def span(self, name: str, run_type: str = "chain", **metadata):
        """
        Create a trace span with metadata.

        Args:
            name: Span name
            run_type: LangSmith run type (chain, tool, llm, etc.)
            **metadata: Additional metadata to attach

        Yields:
            RunTree object for the span
        """
        if not self.is_enabled:
            yield None
            return

        run = RunTree(
            name=name,
            run_type=run_type,
            extra=metadata,
            project_name=self._project,
        )

        try:
            yield run
            run.end()
            run.post()
        except Exception as e:
            run.end(error=str(e))
            run.post()
            raise

    def log_evidence_search(
        self,
        query: str,
        tool_name: str,
        doc_id: str,
        num_results: int,
        study_id: Optional[str] = None,
    ) -> None:
        """
        Log an evidence search for debugging.

        Args:
            query: Search query
            tool_name: Tool used (bm25_search, vector_search, etc.)
            doc_id: Document searched
            num_results: Number of results returned
            study_id: Study ID if applicable
        """
        if not self.is_enabled:
            return

        self.client.create_run(
            name=f"evidence_search_{tool_name}",
            run_type="tool",
            project_name=self._project,
            inputs={
                "query": query,
                "doc_id": doc_id,
                "study_id": study_id,
            },
            outputs={
                "num_results": num_results,
            },
        )

    def log_classification(
        self,
        component: str,
        classification: dict[str, Any],
        num_citations: int,
        confidence: float,
        needs_review: bool,
        study_id: str,
    ) -> None:
        """
        Log an asset classification for eval tracking.

        Args:
            component: Component being classified
            classification: Classification result
            num_citations: Number of citations found
            confidence: Confidence score
            needs_review: Whether review is needed
            study_id: Study ID
        """
        if not self.is_enabled:
            return

        self.client.create_run(
            name="asset_classification",
            run_type="llm",
            project_name=self._project,
            inputs={
                "component": component,
                "study_id": study_id,
            },
            outputs={
                "classification": classification,
                "num_citations": num_citations,
                "confidence": confidence,
                "needs_review": needs_review,
            },
        )

    def log_workflow_transition(
        self,
        study_id: str,
        from_status: str,
        to_status: str,
        stage_summary: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Log a workflow status transition.

        Args:
            study_id: Study ID
            from_status: Previous workflow status
            to_status: New workflow status
            stage_summary: Optional stage completion summary
        """
        if not self.is_enabled:
            return

        self.client.create_run(
            name="workflow_transition",
            run_type="chain",
            project_name=self._project,
            inputs={
                "study_id": study_id,
                "from_status": from_status,
            },
            outputs={
                "to_status": to_status,
                "stage_summary": stage_summary,
            },
        )

    def log_error(
        self,
        error: Exception,
        context: dict[str, Any],
    ) -> None:
        """
        Log an error for debugging.

        Args:
            error: The exception that occurred
            context: Additional context about the error
        """
        if not self.is_enabled:
            return

        self.client.create_run(
            name="error",
            run_type="chain",
            project_name=self._project,
            inputs=context,
            outputs={
                "error_type": type(error).__name__,
                "error_message": str(error),
            },
            error=str(error),
        )


@lru_cache()
def get_tracer() -> BasisTracer:
    """Get singleton tracer instance."""
    return BasisTracer()


def traced(name: Optional[str] = None, run_type: str = "chain"):
    """
    Decorator for tracing functions.

    Args:
        name: Span name (defaults to function name)
        run_type: LangSmith run type

    Example:
        @traced("classify_asset")
        async def classify(component: str) -> dict:
            ...
    """
    def decorator(func: F) -> F:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.span(name or func.__name__, run_type=run_type):
                return await func(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.span(name or func.__name__, run_type=run_type):
                return func(*args, **kwargs)

        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore

    return decorator
