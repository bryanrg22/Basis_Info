"""Pure repository logic and error mapping (no database).

Decision routing, status derivation, cursor encoding, key scoping, sha256 validation,
the domain-error hierarchy, and extraction of the violated constraint name from a
driver error.
"""

from __future__ import annotations

import base64
import json
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
from basis.db.models import Study
from basis.db.repositories import (
    DECISION_TRANSITIONS,
    SINGLE_ROOMS_GATE_TRANSITIONS,
    decision_target,
    scoped_key,
)
from basis.db.repositories.document import normalize_sha256
from basis.db.repositories.study import SORTS, StudySort, decode_cursor, encode_cursor


@pytest.mark.parametrize(
    ("stage", "decision", "expected"),
    [
        (Stage.APPRAISAL_REVIEW, Decision.APPROVE, (Stage.ROOMS_REVIEW, 3)),
        (Stage.APPRAISAL_REVIEW, Decision.REJECT, (Stage.EXTRACTING, 4)),
        (Stage.ROOMS_REVIEW, Decision.APPROVE, (Stage.COMPONENTS_PROCESSING, 3)),
        (Stage.ROOMS_REVIEW, Decision.REJECT, (Stage.EXTRACTING, 4)),
        (Stage.COMPONENTS_REVIEW, Decision.APPROVE, (Stage.ASSETS_PROCESSING, 3)),
        (Stage.COMPONENTS_REVIEW, Decision.REJECT, (Stage.COMPONENTS_PROCESSING, 4)),
        (Stage.ASSETS_REVIEW, Decision.APPROVE, (Stage.EXPORTING, 3)),
        (Stage.ASSETS_REVIEW, Decision.REJECT, (Stage.ASSETS_PROCESSING, 4)),
    ],
)
def test_decision_target(stage: Stage, decision: Decision, expected: tuple[Stage, int]) -> None:
    assert decision_target(stage, decision, 3) == expected
    assert decision_target(stage, decision, 3, components_gate=True) == expected


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
def test_decision_target_single_rooms_gate(
    stage: Stage, decision: Decision, expected: tuple[Stage, int]
) -> None:
    """A study whose workflow has the single rooms review (before components-gate-v1)."""
    assert decision_target(stage, decision, 3, components_gate=False) == expected


def test_decision_table_covers_exactly_review_stages_and_both_decisions() -> None:
    every = {(s, d) for s in REVIEW_STAGES for d in Decision}
    assert set(DECISION_TRANSITIONS) == every
    assert set(SINGLE_ROOMS_GATE_TRANSITIONS) == every
    # A decision always leaves the review stage, so the same revision cannot be decided twice.
    for table in (DECISION_TRANSITIONS, SINGLE_ROOMS_GATE_TRANSITIONS):
        for (stage, _), target in table.items():
            assert target is not stage
    # The single gate never reaches the components stages from the rooms review.
    differ = {k for k in every if DECISION_TRANSITIONS[k] != SINGLE_ROOMS_GATE_TRANSITIONS[k]}
    assert differ == {(Stage.ROOMS_REVIEW, Decision.APPROVE)}


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
        (Stage.COMPONENTS_PROCESSING, StudyStatus.PROCESSING),
        (Stage.COMPONENTS_REVIEW, StudyStatus.WAITING_REVIEW),
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


def _study(name: str = "Harbor") -> Study:
    return Study(
        id=uuid.uuid4(),
        property_name=name,
        created_at=datetime(2026, 10, 15, 12, 30, 45, 123456, tzinfo=UTC),
        updated_at=datetime(2026, 10, 16, 8, 0, 0, 7, tzinfo=UTC),
    )


@pytest.mark.parametrize("sort", SORTS)
def test_cursor_round_trip_under_each_sort(sort: StudySort) -> None:
    study = _study("Harbor Blvd")
    cursor = encode_cursor(sort, study)
    assert "=" not in cursor and "/" not in cursor and "+" not in cursor
    key, study_id = decode_cursor(cursor, sort)
    assert study_id == study.id
    expected = {
        "updated_desc": study.updated_at,
        "created_desc": study.created_at,
        "name_asc": "harbor blvd",  # the name's lower-cased form, the sort's key
    }
    assert key == expected[sort]


def test_a_cursor_issued_under_another_sort_is_invalid() -> None:
    cursor = encode_cursor("updated_desc", _study())
    with pytest.raises(InvalidCursorError, match="issued for sort=updated_desc"):
        decode_cursor(cursor, "name_asc")
    with pytest.raises(InvalidCursorError, match="issued for sort=updated_desc"):
        decode_cursor(cursor, "created_desc")


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "!!!",
        "bm90LWpzb24",  # "not-json"
        encode_cursor("created_desc", _study())[:-4],
        # the cursor format before sorting existed ({"t", "id"}): no sort, so not comparable
        "eyJ0IjoiMjAyNi0wMS0wMVQwMDowMDowMCswMDowMCIsImlkIjoiMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwIn0",
        # naive timestamp
        "eyJzIjoiY3JlYXRlZF9kZXNjIiwiayI6IjIwMjYtMDEtMDFUMDA6MDA6MDAiLCJpZCI6IjAwMDAwMDAwLTAwMDAtMDAwMC0wMDAwLTAwMDAwMDAwMDAwMCJ9",
        # bad uuid
        "eyJzIjoiY3JlYXRlZF9kZXNjIiwiayI6IjIwMjYtMDEtMDFUMDA6MDA6MDArMDA6MDAiLCJpZCI6Im5vcGUifQ",
    ],
)
def test_invalid_cursor(cursor: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor, "created_desc")


def test_a_name_cursor_needs_a_string_key() -> None:
    raw = base64.urlsafe_b64encode(
        json.dumps({"s": "name_asc", "k": 3, "id": str(uuid.uuid4())}).encode()
    ).decode()
    with pytest.raises(InvalidCursorError):
        decode_cursor(raw, "name_asc")


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
