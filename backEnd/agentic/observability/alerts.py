"""
Workflow failure alerts for production monitoring.

Phase 6: Provides multi-channel alerting for workflow failures
with throttling to prevent alert fatigue.
"""

import logging
import hashlib
import httpx
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ..config.settings import get_settings

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
                    "text": f"*Error:*\n```{self.error_message[:1000]}```",
                },
            },
        ]

        if self.context:
            context_str = "\n".join(f"• {k}: {v}" for k, v in self.context.items())
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Context:*\n{context_str}",
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
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload.to_slack_message(),
                    timeout=10.0,
                )
                if response.status_code == 200:
                    logger.info(f"Slack alert sent for {payload.study_id}")
                    return True
                else:
                    logger.error(f"Slack webhook failed: {response.status_code}")
                    return False
        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}")
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
            channels.append(SlackAlertChannel(slack_webhook))

        # Add generic webhook if configured
        webhook_url = getattr(settings, "alert_webhook_url", None)
        if webhook_url:
            channels.append(WebhookAlertChannel(webhook_url))

        # Always add log channel as fallback
        channels.append(LogAlertChannel())

        throttle_seconds = getattr(settings, "alert_throttle_seconds", 300)

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
