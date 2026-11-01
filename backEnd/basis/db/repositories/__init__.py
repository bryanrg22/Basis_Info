"""Repositories: explicit, typed queries per aggregate (no generic CRUD).

Each repository wraps one ``AsyncSession`` and never commits; the caller owns the
transaction (``basis.db.session.get_session`` commits once per request).
"""

from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.idempotency import IdempotencyClaim, IdempotencyRepository, scoped_key
from basis.db.repositories.invitation import (
    Answer,
    EmailDelivery,
    InvitationRepository,
    InvitationStatus,
    InvitationView,
)
from basis.db.repositories.org import (
    MAX_FIRM_NAME_LENGTH,
    FirmSummary,
    Member,
    Membership,
    OrgRepository,
)
from basis.db.repositories.outbox import (
    INVITATION_NOTIFICATION,
    NOTIFICATION,
    WORKFLOW_SIGNAL,
    OutboxRepository,
)
from basis.db.repositories.review import (
    DECISION_TRANSITIONS,
    SINGLE_ROOMS_GATE_TRANSITIONS,
    CorrectionInput,
    DecisionRecord,
    ReviewRepository,
    decision_target,
)
from basis.db.repositories.review_draft import DraftWrite, OtherDraft, ReviewDraftRepository
from basis.db.repositories.study import RestartRecord, StudyPage, StudyRepository, VisibleStudy
from basis.db.repositories.user import UserIdentity, UserRepository

__all__ = [
    "DECISION_TRANSITIONS",
    "INVITATION_NOTIFICATION",
    "MAX_FIRM_NAME_LENGTH",
    "NOTIFICATION",
    "SINGLE_ROOMS_GATE_TRANSITIONS",
    "WORKFLOW_SIGNAL",
    "Answer",
    "CorrectionInput",
    "DecisionRecord",
    "DocumentRepository",
    "DraftWrite",
    "EmailDelivery",
    "FirmSummary",
    "IdempotencyClaim",
    "IdempotencyRepository",
    "InvitationRepository",
    "InvitationStatus",
    "InvitationView",
    "Member",
    "Membership",
    "OrgRepository",
    "OtherDraft",
    "OutboxRepository",
    "RestartRecord",
    "ReviewDraftRepository",
    "ReviewRepository",
    "StudyPage",
    "StudyRepository",
    "UserIdentity",
    "UserRepository",
    "VisibleStudy",
    "decision_target",
    "scoped_key",
]
