"""What the capture workflow and its activities pass each other (pydantic, like
:mod:`basis.workflow.contracts`, so the pydantic data converter carries them)."""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

CAPTURE_WORKFLOW: Final = "PropertyCaptureWorkflow"
CAPTURE_ACTIVITY: Final = "capture_property"
FAIL_ACTIVITY: Final = "fail_capture"
WORKFLOW_ID_PREFIX: Final = "property-capture-"


def capture_workflow_id(capture_id: object) -> str:
    """``property-capture-{capture id}``: one execution per capture."""
    return f"{WORKFLOW_ID_PREFIX}{capture_id}"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CaptureInput(_Frozen):
    capture_id: str
    max_wait_hours: float = 48.0


class CaptureStep(_Frozen):
    """One run of the capture activity: ended (``done``, ``failed``) or ``waiting`` until
    ``resume_at``."""

    status: Literal["done", "failed", "waiting"]
    resume_at: datetime | None = None
    code: str | None = None
    views: int = 0


class FailInput(_Frozen):
    capture_id: str
    code: str
    detail: str
