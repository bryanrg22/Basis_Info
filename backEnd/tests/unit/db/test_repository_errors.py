"""Pure repository logic and error mapping (no database).

Decision routing, status derivation, cursor encoding, key scoping, sha256 validation,
the domain-error hierarchy, and extraction of the violated constraint name from a
driver error.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from basis.db.enums import REVIEW_STAGES, Decision, OrgRole, Stage, StudyStatus, status_for_stage
from basis.db.errors import (
    ConflictError,
    DuplicateDocumentError,
    DuplicateKeyError,
    InvalidCursorError,
    NotVisibleError,
    RepositoryError,
    StaleStateError,
    constraint_name,
)
from basis.db.repositories import DECISION_TRANSITIONS, decision_target, scoped_key
from basis.db.repositories.document import normalize_sha256
from basis.db.repositories.study import decode_cursor, encode_cursor
from basis.db.repositories.user import personal_org_name


@pytest.mark.parametrize(
    ("stage", "decision", "expected"),
    [
        (Stage.APPRAISAL_REVIEW, Decision.APPROVE, (Stage.ROOMS_REVIEW, 3)),
        (Stage.APPRAISAL_REVIEW, Decision.REJECT, (Stage.EXTRACTING, 4)),
        (Stage.ROOMS_REVIEW, Decision.APPROVE, (Stage.ASSETS_PROCESSING, 3)),
        (Stage.ROOMS_REVIEW, Decision.REJECT, (Stage.EXTRACTING, 4)),
        (Stage.ASSETS_REVIEW, Decision.APPROVE, (Stage.EXPORTING, 3)),
        (Stage.ASSETS_REVIEW, Decision.REJECT, (Stage.ASSETS_PROCESSING, 4)),
    ],
)
def test_decision_target(stage: Stage, decision: Decision, expected: tuple[Stage, int]) -> None:
    assert decision_target(stage, decision, 3) == expected


def test_decision_table_covers_exactly_review_stages_and_both_decisions() -> None:
    assert set(DECISION_TRANSITIONS) == {(s, d) for s in REVIEW_STAGES for d in Decision}
    # A decision always leaves the review stage, so the same revision cannot be decided twice.
    for (stage, _), target in DECISION_TRANSITIONS.items():
        assert target is not stage


@pytest.mark.parametrize("stage", sorted(set(Stage) - REVIEW_STAGES))
def test_decision_on_non_review_stage_is_stale(stage: Stage) -> None:
    with pytest.raises(StaleStateError, match="not a review stage"):
        decision_target(stage, Decision.APPROVE, 1)


@pytest.mark.parametrize(
    ("stage", "status"),
    [
        (Stage.UPLOADING, StudyStatus.DRAFT),
        (Stage.EXTRACTING, StudyStatus.PROCESSING),
        (Stage.APPRAISAL_REVIEW, StudyStatus.WAITING_REVIEW),
        (Stage.ROOMS_REVIEW, StudyStatus.WAITING_REVIEW),
        (Stage.ASSETS_PROCESSING, StudyStatus.PROCESSING),
        (Stage.ASSETS_REVIEW, StudyStatus.WAITING_REVIEW),
        (Stage.EXPORTING, StudyStatus.PROCESSING),
        (Stage.COMPLETED, StudyStatus.COMPLETED),
    ],
)
def test_status_for_stage(stage: Stage, status: StudyStatus) -> None:
    assert status_for_stage(stage) is status


def test_org_role_rank_order() -> None:
    assert OrgRole.VIEWER.rank < OrgRole.ENGINEER.rank < OrgRole.OWNER.rank


def test_cursor_round_trip() -> None:
    created_at = datetime(2026, 10, 15, 12, 30, 45, 123456, tzinfo=UTC)
    study_id = uuid.uuid4()
    cursor = encode_cursor(created_at, study_id)
    assert "=" not in cursor and "/" not in cursor and "+" not in cursor
    assert decode_cursor(cursor) == (created_at, study_id)


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "!!!",
        "bm90LWpzb24",  # "not-json"
        encode_cursor(datetime(2026, 1, 1, tzinfo=UTC), uuid.uuid4())[:-4],
        # naive timestamp
        "eyJ0IjoiMjAyNi0wMS0wMVQwMDowMDowMCIsImlkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIn0",
        # bad uuid
        "eyJ0IjoiMjAyNi0wMS0wMVQwMDowMDowMCswMDowMCIsImlkIjoibm9wZSJ9",
    ],
)
def test_invalid_cursor(cursor: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor)


def test_scoped_key() -> None:
    user = uuid.uuid4()
    assert scoped_key(user, "abc") == f"{user}:abc"
    assert scoped_key(None, "abc") == "anonymous:abc"
    assert scoped_key(user, "k") != scoped_key(uuid.uuid4(), "k")
    assert scoped_key(user, "x" * 128).endswith("x" * 128)
    for bad in ("", "x" * 129):
        with pytest.raises(ValueError):
            scoped_key(user, bad)


def test_normalize_sha256() -> None:
    assert normalize_sha256(" " + "AB" * 32 + "\n") == "ab" * 32
    for bad in ("", "ab" * 31, "zz" * 32, "ab" * 33):
        with pytest.raises(ValueError):
            normalize_sha256(bad)


def test_personal_org_name() -> None:
    assert personal_org_name("a@x.test", "Ada") == "Ada (personal)"
    assert personal_org_name("a@x.test", "  ") == "a@x.test (personal)"
    assert personal_org_name(None, None) == "Personal workspace (personal)"


def test_error_hierarchy() -> None:
    for conflict in (StaleStateError, DuplicateDocumentError, DuplicateKeyError):
        assert issubclass(conflict, ConflictError)
        assert issubclass(conflict, RepositoryError)
    assert issubclass(NotVisibleError, RepositoryError)
    assert not issubclass(NotVisibleError, ConflictError)
    assert issubclass(InvalidCursorError, ValueError)


class FakeAsyncpgError(Exception):
    def __init__(self, constraint: str | None) -> None:
        super().__init__("duplicate key")
        self.constraint_name = constraint


class FakeAdaptedError(Exception):
    """Mimics SQLAlchemy's asyncpg adapter: the asyncpg error is the ``__cause__``."""


def integrity_error(orig: BaseException) -> IntegrityError:
    return IntegrityError("INSERT ...", {}, orig)


def test_constraint_name_from_asyncpg_cause() -> None:
    adapted = FakeAdaptedError("wrapped")
    adapted.__cause__ = FakeAsyncpgError("uq_documents_study_id_sha256")
    assert constraint_name(integrity_error(adapted)) == "uq_documents_study_id_sha256"


def test_constraint_name_directly_on_orig() -> None:
    assert constraint_name(integrity_error(FakeAsyncpgError("pk_x"))) == "pk_x"


@pytest.mark.parametrize("orig", [FakeAdaptedError("no cause"), FakeAsyncpgError(None)])
def test_constraint_name_absent(orig: BaseException) -> None:
    assert constraint_name(integrity_error(orig)) is None
