"""
Workflow failure alerts for production monitoring.

Phase 6: Provides multi-channel alerting for workflow failures
with throttling to prevent alert fatigue.

Includes @alert_on_failure decorator for automatic alerting on any component failure.
"""

import functools
import logging
import hashlib
import httpx
import traceback
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Literal, Optional, TypeVar, ParamSpec

from pydantic import BaseModel, Field

from ..config.settings import get_settings

# Type variables for decorator
P = ParamSpec("P")
T = TypeVar("T")

logger = logging.getLogger(__name__)


# =============================================================================
# Models
# =============================================================================


class AlertPayload(BaseModel):
    """
    Alert payload for workflow failures.

    Contains all relevant context for debugging the failure.
    """

    study_id: str = Field(
        ...,
        description="Study ID where failure occurred",
    )
    workflow_stage: str = Field(
        ...,
        description="Workflow stage where failure occurred",
    )
    error_type: str = Field(
        ...,
        description="Type of error (e.g., 'LLM_TIMEOUT', 'PARSING_ERROR')",
    )
    error_message: str = Field(
        ...,
        description="Error message",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the error occurred",
    )
    severity: Literal["warning", "error", "critical"] = Field(
        default="error",
        description="Alert severity level",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context (e.g., component_id, user_id)",
    )
    trace_summary: Optional[str] = Field(
        default=None,
        description="LangSmith trace summary for debugging",
    )
    langsmith_url: Optional[str] = Field(
        default=None,
        description="Link to LangSmith trace",
    )
    flagged_fields: list[str] = Field(
        default_factory=list,
        description="Fields flagged for review",
    )
    workflow_stats: Optional[dict[str, Any]] = Field(
        default=None,
        description="Workflow statistics (tokens, cost, duration)",
    )

    def to_slack_message(self) -> dict[str, Any]:
        """Format as Slack message payload."""
        emoji = {
            "warning": ":warning:",
            "error": ":x:",
            "critical": ":rotating_light:",
        }.get(self.severity, ":x:")

        color = {
            "warning": "#FFA500",
            "error": "#FF0000",
            "critical": "#8B0000",
        }.get(self.severity, "#FF0000")

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} Workflow {self.severity.upper()}: {self.error_type}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Study ID:*\n{self.study_id}"},
                    {"type": "mrkdwn", "text": f"*Stage:*\n{self.workflow_stage}"},
                    {"type": "mrkdwn", "text": f"*Time:*\n{self.timestamp.isoformat()}"},
                    {"type": "mrkdwn", "text": f"*Severity:*\n{self.severity}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Error:*\n```{self.error_message[:500]}```",
                },
            },
        ]

        # Add trace summary if available
        if self.trace_summary:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*📊 Trace Summary:*\n```{self.trace_summary[:1500]}```",
                },
            })

        # Add flagged fields if available
        if self.flagged_fields:
            flagged_list = "\n".join([f"• {f}" for f in self.flagged_fields[:10]])
            if len(self.flagged_fields) > 10:
                flagged_list += f"\n• ... and {len(self.flagged_fields) - 10} more"
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*❌ {len(self.flagged_fields)} Flagged Fields:*\n{flagged_list}",
                },
            })

        # Add workflow stats if available
        if self.workflow_stats:
            stats = self.workflow_stats
            stats_text = (
                f"*💰 Cost:* ${stats.get('cost', 0):.4f} | "
                f"*⏱️ Duration:* {stats.get('duration', 0):.0f}s | "
                f"*🔢 Tokens:* {stats.get('tokens', 0):,}"
            )
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": stats_text,
                },
            })

        # Add context if available
        if self.context:
            context_str = "\n".join(f"• {k}: {v}" for k, v in self.context.items())
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Context:*\n{context_str}",
                },
            })

        # Add LangSmith link if available
        if self.langsmith_url:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🔗 LangSmith:* <{self.langsmith_url}|View Trace>",
                },
            })

        return {
            "attachments": [
                {
                    "color": color,
                    "blocks": blocks,
                }
            ],
        }

    def to_email_body(self) -> str:
        """Format as email body."""
        context_lines = "\n".join(
            f"  - {k}: {v}" for k, v in self.context.items()
        ) if self.context else "  None"

        return f"""
Workflow Alert: {self.severity.upper()}

Study ID: {self.study_id}
Stage: {self.workflow_stage}
Error Type: {self.error_type}
Time: {self.timestamp.isoformat()}
Severity: {self.severity}

Error Message:
{self.error_message}

Context:
{context_lines}
"""

    def throttle_key(self) -> str:
        """Generate a key for throttling similar alerts."""
        key_string = f"{self.study_id}:{self.workflow_stage}:{self.error_type}"
        return hashlib.md5(key_string.encode()).hexdigest()


# =============================================================================
# Alert Channels
# =============================================================================


class AlertChannel(ABC):
    """Abstract base class for alert channels."""

    @abstractmethod
    async def send(self, payload: AlertPayload) -> bool:
        """
        Send an alert through this channel.

        Args:
            payload: Alert payload to send

        Returns:
            True if sent successfully, False otherwise
        """
        pass


class SlackAlertChannel(AlertChannel):
    """Send alerts to Slack via webhook."""

    def __init__(self, webhook_url: str):
        """
        Initialize Slack channel.

        Args:
            webhook_url: Slack webhook URL
        """
        self.webhook_url = webhook_url

    async def send(self, payload: AlertPayload) -> bool:
        """Send alert to Slack."""
        try:
            logger.info(f"Attempting to send Slack alert to: {self.webhook_url[:50]}...")
            message = payload.to_slack_message()
            logger.debug(f"Slack message payload: {message}")

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json=message,
                    timeout=10.0,
                )
                if response.status_code == 200:
                    logger.info(f"Slack alert sent successfully for {payload.study_id}")
                    return True
                else:
                    logger.error(f"Slack webhook failed: {response.status_code} - {response.text}")
                    return False
        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}", exc_info=True)
            return False


class WebhookAlertChannel(AlertChannel):
    """Send alerts to a generic webhook."""

    def __init__(self, webhook_url: str):
        """
        Initialize webhook channel.

        Args:
            webhook_url: Webhook URL
        """
        self.webhook_url = webhook_url

    async def send(self, payload: AlertPayload) -> bool:
        """Send alert to webhook."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload.model_dump(),
                    timeout=10.0,
                )
                if response.status_code in (200, 201, 202):
                    logger.info(f"Webhook alert sent for {payload.study_id}")
                    return True
                else:
                    logger.error(f"Webhook failed: {response.status_code}")
                    return False
        except Exception as e:
            logger.error(f"Failed to send webhook alert: {e}")
            return False


class LogAlertChannel(AlertChannel):
    """Log alerts (useful for development/testing)."""

    def __init__(self, log_level: int = logging.ERROR):
        """
        Initialize log channel.

        Args:
            log_level: Logging level to use
        """
        self.log_level = log_level

    async def send(self, payload: AlertPayload) -> bool:
        """Log the alert."""
        logger.log(
            self.log_level,
            f"ALERT [{payload.severity.upper()}] {payload.error_type} "
            f"in study {payload.study_id} at {payload.workflow_stage}: "
            f"{payload.error_message[:200]}"
        )
        return True


# =============================================================================
# Alert Manager
# =============================================================================


class AlertManager:
    """
    Manages alert delivery across multiple channels.

    Provides throttling to prevent alert fatigue for repeated errors.

    Usage:
        manager = AlertManager()
        manager.add_channel(SlackAlertChannel(webhook_url))

        await manager.alert(AlertPayload(
            study_id="study_123",
            workflow_stage="classification",
            error_type="LLM_TIMEOUT",
            error_message="LLM call timed out after 60s",
            severity="error",
        ))
    """

    def __init__(
        self,
        channels: Optional[list[AlertChannel]] = None,
        throttle_seconds: int = 300,
    ):
        """
        Initialize alert manager.

        Args:
            channels: List of alert channels to use
            throttle_seconds: Minimum seconds between similar alerts
        """
        self.channels = channels or []
        self.throttle_seconds = throttle_seconds
        self._last_alert_times: dict[str, datetime] = {}

    def add_channel(self, channel: AlertChannel) -> None:
        """Add an alert channel."""
        self.channels.append(channel)

    def _is_throttled(self, throttle_key: str) -> bool:
        """Check if an alert should be throttled."""
        if throttle_key not in self._last_alert_times:
            return False

        last_time = self._last_alert_times[throttle_key]
        elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
        return elapsed < self.throttle_seconds

    def _record_alert_time(self, throttle_key: str) -> None:
        """Record when an alert was sent."""
        self._last_alert_times[throttle_key] = datetime.now(timezone.utc)

        # Clean up old entries (older than 1 hour)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        self._last_alert_times = {
            k: v for k, v in self._last_alert_times.items()
            if v > cutoff
        }

    async def alert(
        self,
        payload: AlertPayload,
        throttle_key: Optional[str] = None,
    ) -> bool:
        """
        Send an alert through all configured channels.

        Applies throttling to prevent alert fatigue.

        Args:
            payload: Alert payload
            throttle_key: Optional custom throttle key

        Returns:
            True if at least one channel succeeded
        """
        # Use default throttle key if not provided
        key = throttle_key or payload.throttle_key()

        # Check throttling
        if self._is_throttled(key):
            logger.debug(f"Alert throttled: {key}")
            return False

        if not self.channels:
            logger.warning("No alert channels configured")
            return False

        # Send to all channels
        success = False
        for channel in self.channels:
            try:
                result = await channel.send(payload)
                if result:
                    success = True
            except Exception as e:
                logger.error(f"Alert channel failed: {e}")

        # Record alert time if any channel succeeded
        if success:
            self._record_alert_time(key)

        return success

    async def alert_error(
        self,
        study_id: str,
        stage: str,
        error: Exception,
        severity: Literal["warning", "error", "critical"] = "error",
        context: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Convenience method to alert on an exception.

        Args:
            study_id: Study ID
            stage: Workflow stage
            error: The exception
            severity: Alert severity
            context: Additional context

        Returns:
            True if alert was sent
        """
        payload = AlertPayload(
            study_id=study_id,
            workflow_stage=stage,
            error_type=type(error).__name__,
            error_message=str(error),
            severity=severity,
            context=context or {},
        )
        return await self.alert(payload)


# =============================================================================
# Global Alert Manager
# =============================================================================


_alert_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    """
    Get the global alert manager instance.

    Configures channels from settings on first call.
    """
    global _alert_manager

    if _alert_manager is None:
        settings = get_settings()

        channels: list[AlertChannel] = []

        # Add Slack channel if configured
        slack_webhook = getattr(settings, "alert_slack_webhook", None)
        if slack_webhook:
            logger.info(f"Configuring Slack alert channel: {slack_webhook[:50]}...")
            channels.append(SlackAlertChannel(slack_webhook))
        else:
            logger.warning("No ALERT_SLACK_WEBHOOK configured - Slack alerts disabled")

        # Add generic webhook if configured
        webhook_url = getattr(settings, "alert_webhook_url", None)
        if webhook_url:
            channels.append(WebhookAlertChannel(webhook_url))

        # Always add log channel as fallback
        channels.append(LogAlertChannel())

        throttle_seconds = getattr(settings, "alert_throttle_seconds", 300)

        logger.info(f"Alert manager initialized with {len(channels)} channels, throttle={throttle_seconds}s")

        _alert_manager = AlertManager(
            channels=channels,
            throttle_seconds=throttle_seconds,
        )

    return _alert_manager


async def send_alert(
    study_id: str,
    stage: str,
    error_type: str,
    error_message: str,
    severity: Literal["warning", "error", "critical"] = "error",
    context: Optional[dict[str, Any]] = None,
) -> bool:
    """
    Convenience function to send an alert.

    Args:
        study_id: Study ID
        stage: Workflow stage
        error_type: Type of error
        error_message: Error message
        severity: Alert severity
        context: Additional context

    Returns:
        True if alert was sent
    """
    manager = get_alert_manager()
    payload = AlertPayload(
        study_id=study_id,
        workflow_stage=stage,
        error_type=error_type,
        error_message=error_message,
        severity=severity,
        context=context or {},
    )
    return await manager.alert(payload)


# =============================================================================
# @alert_on_failure Decorator
# =============================================================================


def alert_on_failure(
    component: str,
    severity: Literal["warning", "error", "critical"] = "error",
    study_id_param: str = "study_id",
    reraise: bool = True,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator that sends a Slack alert when a function fails.

    Automatically extracts study_id from function parameters or kwargs.
    Works with both sync and async functions.

    Args:
        component: Name of the component (e.g., "room_agent", "firestore_client")
        severity: Alert severity level
        study_id_param: Name of the parameter containing study_id
        reraise: Whether to re-raise the exception after alerting

    Usage:
        @alert_on_failure("room_agent")
        async def analyze_rooms(study_id: str, images: list[str]) -> dict:
            ...

        @alert_on_failure("firestore_client", study_id_param="doc_id")
        async def update_document(doc_id: str, data: dict) -> None:
            ...

    Returns:
        Decorated function that alerts on failure
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            # Try to extract study_id from args/kwargs
            study_id = _extract_study_id(func, args, kwargs, study_id_param)

            try:
                return await func(*args, **kwargs)
            except Exception as e:
                # Build error context
                error_type = type(e).__name__
                error_message = str(e)
                tb = traceback.format_exc()

                # Categorize the error
                categorized_type = _categorize_error(error_type, error_message)

                # Send alert
                try:
                    await send_alert(
                        study_id=study_id or "unknown",
                        stage=component,
                        error_type=categorized_type,
                        error_message=f"{error_message}\n\nTraceback:\n{tb[-1000:]}",
                        severity=severity,
                        context={
                            "function": func.__name__,
                            "exception_type": error_type,
                            "component": component,
                        },
                    )
                except Exception as alert_error:
                    logger.error(f"Failed to send failure alert: {alert_error}")

                if reraise:
                    raise

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            # For sync functions, we can't send async alerts easily
            # Log the error and re-raise
            study_id = _extract_study_id(func, args, kwargs, study_id_param)

            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_type = type(e).__name__
                error_message = str(e)

                # Log the failure (can't send async alert from sync context)
                logger.error(
                    f"ALERT [{severity.upper()}] {component}.{func.__name__} failed: "
                    f"study_id={study_id}, error={error_type}: {error_message}"
                )

                if reraise:
                    raise

        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        else:
            return sync_wrapper  # type: ignore

    return decorator


def _extract_study_id(
    func: Callable,
    args: tuple,
    kwargs: dict,
    param_name: str,
) -> Optional[str]:
    """Extract study_id from function arguments."""
    # Check kwargs first
    if param_name in kwargs:
        return str(kwargs[param_name])

    # Try to find in positional args using function signature
    try:
        import inspect
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        if param_name in params:
            idx = params.index(param_name)
            if idx < len(args):
                return str(args[idx])
    except Exception:
        pass

    # Check if first arg looks like a study_id (contains underscore or is alphanumeric)
    if args and isinstance(args[0], str) and len(args[0]) > 5:
        return args[0]

    # Check kwargs for common study_id variations
    for key in ["study_id", "studyId", "doc_id", "document_id", "id"]:
        if key in kwargs:
            return str(kwargs[key])

    return None


def _categorize_error(error_type: str, error_message: str) -> str:
    """Categorize error for better alert grouping."""
    message_lower = error_message.lower()
    type_lower = error_type.lower()

    # Rate limiting
    if "429" in error_message or "rate" in message_lower:
        return "RATE_LIMITED"

    # Authentication/Authorization
    if "401" in error_message or "403" in error_message:
        return "AUTH_ERROR"
    if "auth" in message_lower or "credential" in message_lower:
        return "AUTH_ERROR"

    # Timeout
    if "timeout" in message_lower or "timed out" in message_lower:
        return "TIMEOUT"

    # Connection issues
    if "connection" in message_lower or "network" in message_lower:
        return "CONNECTION_ERROR"

    # Validation
    if "validation" in type_lower or "pydantic" in type_lower:
        return "VALIDATION_ERROR"

    # Not found
    if "not found" in message_lower or "404" in error_message:
        return "NOT_FOUND"

    # Package/Import
    if "import" in type_lower or "module" in message_lower or "package" in message_lower:
        return "IMPORT_ERROR"

    # File operations
    if "file" in message_lower or "permission" in message_lower:
        return "FILE_ERROR"

    # Default to exception type
    return error_type.upper()


# =============================================================================
# Alert with Trace Summary
# =============================================================================


async def send_alert_with_trace(
    study_id: str,
    stage: str,
    error_type: str,
    error_message: str,
    severity: Literal["warning", "error", "critical"] = "error",
    context: Optional[dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> bool:
    """
    Send an alert with LangSmith trace summary for debugging.

    Automatically fetches trace information from LangSmith if available.

    Args:
        study_id: Study ID
        stage: Workflow stage
        error_type: Type of error
        error_message: Error message
        severity: Alert severity
        context: Additional context
        trace_id: Optional specific trace ID (uses latest if not provided)

    Returns:
        True if alert was sent
    """
    from .trace_analyzer import get_trace_analyzer

    manager = get_alert_manager()

    # Try to get trace summary
    trace_summary = None
    langsmith_url = None
    flagged_fields = []
    workflow_stats = None

    try:
        analyzer = get_trace_analyzer()
        if trace_id:
            summary = analyzer.get_trace_by_id(trace_id)
        else:
            summary = analyzer.get_latest_trace(study_id)

        if summary:
            # Build trace summary string
            agent_lines = []
            for agent in summary.agents:
                status_icon = "✅" if agent.status == "success" else "❌"
                line = f"{status_icon} {agent.name}: {agent.duration_seconds:.0f}s"
                if agent.flagged_count > 0:
                    line += f" → flagged {agent.flagged_count}"
                if agent.error:
                    line += " ❌"
                agent_lines.append(line)

            trace_summary = "\n".join(agent_lines)
            langsmith_url = summary.langsmith_url
            flagged_fields = summary.flagged_fields
            workflow_stats = {
                "cost": summary.total_cost_usd,
                "duration": summary.duration_seconds,
                "tokens": summary.total_tokens,
            }

    except Exception as e:
        logger.warning(f"Failed to fetch trace summary: {e}")

    payload = AlertPayload(
        study_id=study_id,
        workflow_stage=stage,
        error_type=error_type,
        error_message=error_message,
        severity=severity,
        context=context or {},
        trace_summary=trace_summary,
        langsmith_url=langsmith_url,
        flagged_fields=flagged_fields,
        workflow_stats=workflow_stats,
    )

    return await manager.alert(payload)


async def send_workflow_completion_alert(
    study_id: str,
    trace_id: Optional[str] = None,
) -> bool:
    """
    Send an alert when workflow completes with issues (needs review).

    Only sends if there are flagged fields or errors.

    Args:
        study_id: Study ID
        trace_id: Optional specific trace ID

    Returns:
        True if alert was sent (or no alert needed)
    """
    from .trace_analyzer import get_trace_analyzer

    try:
        analyzer = get_trace_analyzer()
        if trace_id:
            summary = analyzer.get_trace_by_id(trace_id)
        else:
            summary = analyzer.get_latest_trace(study_id)

        if not summary:
            logger.warning(f"Could not fetch trace for study {study_id}")
            return False

        # Only alert if there are issues
        if summary.final_state == "completed" and not summary.flagged_fields:
            logger.info(f"Workflow completed successfully for {study_id}, no alert needed")
            return True

        # Determine severity based on state
        if summary.errors:
            severity = "error"
            error_type = "WORKFLOW_ERROR"
        elif summary.flagged_fields:
            severity = "warning"
            error_type = "NEEDS_REVIEW"
        else:
            return True  # No issues

        # Build error message
        if summary.errors:
            error_message = f"Workflow completed with errors:\n" + "\n".join(summary.errors[:5])
        else:
            error_message = f"Workflow completed but {len(summary.flagged_fields)} fields need review"

        # Build trace summary string
        agent_lines = []
        for agent in summary.agents:
            status_icon = "✅" if agent.status == "success" else "❌"
            line = f"{status_icon} {agent.name}: {agent.duration_seconds:.0f}s"
            if agent.flagged_count > 0:
                line += f" → flagged {agent.flagged_count}"
            agent_lines.append(line)

        manager = get_alert_manager()
        payload = AlertPayload(
            study_id=study_id,
            workflow_stage="workflow_completion",
            error_type=error_type,
            error_message=error_message,
            severity=severity,
            trace_summary="\n".join(agent_lines),
            langsmith_url=summary.langsmith_url,
            flagged_fields=summary.flagged_fields,
            workflow_stats={
                "cost": summary.total_cost_usd,
                "duration": summary.duration_seconds,
                "tokens": summary.total_tokens,
            },
        )

        return await manager.alert(payload)

    except Exception as e:
        logger.error(f"Failed to send workflow completion alert: {e}")
        return False
