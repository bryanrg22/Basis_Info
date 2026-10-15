"""Python mirrors of the PostgreSQL enums and TEXT-with-CHECK vocabularies (S1_SPEC §1, §2).

The ``.value`` of every member is exactly the label stored in the database. The
PostgreSQL enum types are created by migration ``0001_initial``; the unit test
``tests/unit/db/test_models_metadata.py`` pins the labels to the spec.
"""

from __future__ import annotations

from enum import StrEnum


class StudyStatus(StrEnum):
    DRAFT = "draft"
    PROCESSING = "processing"
    WAITING_REVIEW = "waiting_review"
    STALLED = "stalled"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class Stage(StrEnum):
    UPLOADING = "uploading"
    EXTRACTING = "extracting"
    APPRAISAL_REVIEW = "appraisal_review"
    ROOMS_REVIEW = "rooms_review"
    ASSETS_PROCESSING = "assets_processing"
    ASSETS_REVIEW = "assets_review"
    EXPORTING = "exporting"
    COMPLETED = "completed"


class DocumentKind(StrEnum):
    APPRAISAL = "appraisal"
    PHOTO = "photo"
    OTHER = "other"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ClassificationMethod(StrEnum):
    RULE = "rule"
    CACHE = "cache"
    LLM = "llm"
    MANUAL = "manual"


class Bucket(StrEnum):
    Y5 = "y5"
    Y7 = "y7"
    Y15 = "y15"
    Y27_5 = "y27_5"
    Y39 = "y39"
    LAND = "land"


class OrgRole(StrEnum):
    """``org_members.role`` (TEXT with a CHECK, not a PostgreSQL enum)."""

    VIEWER = "viewer"
    ENGINEER = "engineer"
    OWNER = "owner"

    @property
    def rank(self) -> int:
        """Ordering for ``role >= required`` checks: viewer < engineer < owner."""
        return list(OrgRole).index(self)


class Corpus(StrEnum):
    """``corpus_versions.corpus`` (TEXT with a CHECK)."""

    REFERENCE = "reference"
    STUDY = "study"


REVIEW_STAGES: frozenset[Stage] = frozenset(
    {Stage.APPRAISAL_REVIEW, Stage.ROOMS_REVIEW, Stage.ASSETS_REVIEW}
)


def status_for_stage(stage: Stage) -> StudyStatus:
    """The ``study_status`` a study has while it sits at ``stage``.

    ``stalled`` and ``archived`` are never derived from a stage; they are set explicitly.
    """
    if stage is Stage.UPLOADING:
        return StudyStatus.DRAFT
    if stage is Stage.COMPLETED:
        return StudyStatus.COMPLETED
    if stage in REVIEW_STAGES:
        return StudyStatus.WAITING_REVIEW
    return StudyStatus.PROCESSING
