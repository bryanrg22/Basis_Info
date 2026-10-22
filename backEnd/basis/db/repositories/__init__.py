"""Repositories: explicit, typed queries per aggregate (no generic CRUD).

Each repository wraps one ``AsyncSession`` and never commits; the caller owns the
transaction (``basis.db.session.get_session`` commits once per request).
"""

from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.idempotency import IdempotencyClaim, IdempotencyRepository, scoped_key
from basis.db.repositories.invitation import (
    Answer,
    InvitationRepository,
    InvitationStatus,
    InvitationView,
)
from basis.db.repositories.org import FirmSummary, Member, Membership, OrgRepository
from basis.db.repositories.outbox import NOTIFICATION, WORKFLOW_SIGNAL, OutboxRepository
from basis.db.repositories.review import (
    DECISION_TRANSITIONS,
    SINGLE_ROOMS_GATE_TRANSITIONS,
    CorrectionInput,
    DecisionRecord,
    ReviewRepository,
    decision_target,
)
from basis.db.repositories.study import StudyPage, StudyRepository, VisibleStudy
from basis.db.repositories.user import UserRepository

__all__ = [
    "DECISION_TRANSITIONS",
    "NOTIFICATION",
    "SINGLE_ROOMS_GATE_TRANSITIONS",
    "WORKFLOW_SIGNAL",
    "Answer",
    "CorrectionInput",
    "DecisionRecord",
    "DocumentRepository",
    "FirmSummary",
    "IdempotencyClaim",
    "IdempotencyRepository",
    "InvitationRepository",
    "InvitationStatus",
    "InvitationView",
    "Member",
    "Membership",
    "OrgRepository",
    "OutboxRepository",
    "ReviewRepository",
    "StudyPage",
    "StudyRepository",
    "UserRepository",
    "VisibleStudy",
    "decision_target",
    "scoped_key",
]
