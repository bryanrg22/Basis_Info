"""Names under which ``StudyWorkflow`` is addressed from outside the worker (S2_SPEC 1, 3).

The API and the outbox relay refer to the workflow by these strings instead of importing
the workflow class, so they do not load workflow code (and its sandbox restrictions)
and can be built and tested without it. The worker registers the workflow, its update,
query and signal under exactly these names.
"""

from __future__ import annotations

import uuid
from typing import Final

STUDY_WORKFLOW: Final = "StudyWorkflow"
DECIDE_UPDATE: Final = "decide"
STATUS_QUERY: Final = "status"
CANCEL_SIGNAL: Final = "cancel_study"
WORKFLOW_ID_PREFIX: Final = "study-"


def study_workflow_id(study_id: uuid.UUID | str) -> str:
    """``study-{study_id}``: one workflow id per study, so a second start is a no-op."""
    return f"{WORKFLOW_ID_PREFIX}{study_id}"


def decision_update_id(review_id: str) -> str:
    """Temporal update id for a decision: a redelivered review is deduplicated by the server."""
    return f"decision-{review_id}"
