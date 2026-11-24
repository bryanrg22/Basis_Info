"""The renovations document (EVAL_REPORT 102, ``basis.domain.renovations``): each validation
of a project and of the document, the dates against the intake, the removal correction's
fields, and which project each component belongs to. Fabricated values."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from basis.domain import renovations as rv

PHOTO_A = uuid.UUID(int=101)
PHOTO_B = uuid.UUID(int=102)
INVOICE = uuid.UUID(int=201)


def project(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "key": "kitchen-2025",
        "kind": "after_service",
        "scope": "Kitchen remodel",
        "contract_date": "2025-02-10",
        "work_start_date": "2025-03-03",
        "placed_in_service_date": "2025-05-30",
        "invoice_total": "38500.00",
        "invoices": [{"document_id": str(INVOICE), "amount": "38500.00"}],
        "capitalized_amount": "38500.00",
        "capitalized_confirmed_by": "A. Preparer, CPA",
        "after_photos": [str(PHOTO_A)],
    }
    base.update(over)
    return base


def doc(*projects: dict[str, Any], **over: Any) -> rv.StudyRenovationsIn:
    return rv.StudyRenovationsIn.model_validate({"projects": list(projects), **over})


def test_a_complete_project_is_accepted() -> None:
    d = doc(project())
    p = d.projects[0]
    assert p.bonus_acquired == date(2025, 2, 10)
    assert d.photo_projects() == {str(PHOTO_A): "kitchen-2025"}
    assert d.document_ids() == ({INVOICE}, {PHOTO_A})


def test_without_a_contract_bonus_keys_on_the_work_start() -> None:
    assert doc(project(contract_date=None)).projects[0].bonus_acquired == date(2025, 3, 3)


@pytest.mark.parametrize(
    ("over", "fragment"),
    [
        ({"placed_in_service_date": None}, "needs placed_in_service_date"),
        ({"placed_in_service_date": "2025-03-01"}, "before work_start_date"),
        ({"contract_date": "2025-06-01"}, "before contract_date"),
        ({"invoice_total": "0"}, "greater than 0"),
        ({"invoice_total": "-5"}, "greater than 0"),
        ({"invoice_total": "38000.00"}, "the invoices sum to"),
        ({"capitalized_amount": "40000.00"}, "is above invoice_total"),
        ({"capitalized_confirmed_by": None}, "needs capitalized_confirmed_by"),
        ({"removals_stated": True}, "only for an addition"),
        ({"after_photos": [str(PHOTO_A), str(PHOTO_A)]}, "listed twice"),
        ({"key": "Kitchen 2025"}, "String should match pattern"),
        (
            {"invoices": [{"document_id": str(INVOICE), "amount": "19250.00"}] * 2},
            "listed twice",
        ),
    ],
)
def test_each_project_rule_refuses(over: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        doc(project(**over))


def test_a_before_service_project_has_no_date_or_photos_of_its_own() -> None:
    ok = project(kind="before_service", placed_in_service_date=None, after_photos=[])
    assert doc(ok).projects[0].kind == "before_service"
    with pytest.raises(ValidationError, match="no placed_in_service_date"):
        doc(project(kind="before_service"))
    with pytest.raises(ValidationError, match="not tagged after"):
        doc(project(kind="before_service", placed_in_service_date=None))


def test_the_document_refuses_duplicates_across_projects() -> None:
    other = project(key="bath-2026", invoices=[], after_photos=[str(PHOTO_B)])
    assert len(doc(project(), other).projects) == 2
    with pytest.raises(ValidationError, match="unique"):
        doc(project(), project(after_photos=[]))
    with pytest.raises(ValidationError, match="tagged after both"):
        doc(project(), project(key="bath-2026", invoices=[]))
    with pytest.raises(ValidationError, match="is listed on"):
        doc(project(), project(key="bath-2026", after_photos=[]))


def test_the_aerial_bracket_must_be_in_order() -> None:
    bracket = {
        "last_without": {"date": "2024-06-01", "reference": "view 1"},
        "first_with": {"date": "2024-06-01", "reference": "view 2"},
    }
    with pytest.raises(ValidationError, match="after the last view"):
        doc(project(aerial=bracket))


def test_dates_against_the_intake() -> None:
    building = date(2023, 4, 1)
    assert (
        rv.date_problems(doc(project()), building_placed_in_service=building, acquisition_date=None)
        == []
    )
    early = doc(
        project(
            work_start_date="2023-03-01", contract_date=None, placed_in_service_date="2023-04-01"
        )
    )
    found = rv.date_problems(early, building_placed_in_service=building, acquisition_date=None)
    assert {p.field for p in found} == {
        "projects[kitchen-2025].placed_in_service_date",
        "projects[kitchen-2025].work_start_date",
    }
    pre = doc(
        project(
            kind="before_service",
            placed_in_service_date=None,
            after_photos=[],
            work_start_date="2023-01-01",
            contract_date=None,
        )
    )
    found = rv.date_problems(
        pre, building_placed_in_service=building, acquisition_date=date(2023, 2, 1)
    )
    assert [p.field for p in found] == ["projects[kitchen-2025].work_start_date"]
    late = doc(
        project(
            kind="before_service",
            placed_in_service_date=None,
            after_photos=[],
            work_start_date="2023-05-01",
            contract_date=None,
        )
    )
    assert (
        "kind after_service"
        in rv.date_problems(late, building_placed_in_service=building, acquisition_date=None)[
            0
        ].message
    )
    # No intake: nothing to compare with.
    assert rv.date_problems(early, building_placed_in_service=None, acquisition_date=None) == []


@pytest.mark.parametrize(
    ("after", "reason", "fragment"),
    [
        ({"removed_by_project": "kitchen-2025", "disposition_date": "2025-03-03"}, "photo", None),
        ({"removed_by_project": "kitchen-2025", "disposition_date": "2025-03-03"}, None, "reason"),
        ({"removed_by_project": "kitchen-2025"}, "r", "ISO date"),
        ({"removed_by_project": "kitchen-2025", "disposition_date": "03/03/2025"}, "r", "ISO date"),
        ({"removed_by_project": "Kitchen!", "disposition_date": "2025-03-03"}, "r", "project key"),
        ({"removed_by_project": 7, "disposition_date": "2025-03-03"}, "r", "project key"),
        (
            {"removed_by_project": "k", "disposition_date": "2025-03-03", "removed_share": 0},
            "r",
            "removed_share",
        ),
        (
            {"removed_by_project": "k", "disposition_date": "2025-03-03", "removed_share": 1.5},
            "r",
            "removed_share",
        ),
        (
            {"removed_by_project": "k", "disposition_date": "2025-03-03", "removed_share": True},
            "r",
            "removed_share",
        ),
        ({"removed_by_project": None}, "r", None),
        ({"removed_by_project": None, "disposition_date": "2025-03-03"}, "r", "alone"),
        ({"disposition_date": "2025-03-03"}, "r", "project key"),
    ],
)
def test_the_removal_fields(
    after: dict[str, Any], reason: str | None, fragment: str | None
) -> None:
    found = rv.removal_problems(after, reason)
    if fragment is None:
        assert found == []
    else:
        assert any(fragment in p for p in found), found


def test_a_removal_is_recorded_and_read_back() -> None:
    after = {
        "removed_by_project": "kitchen-2025",
        "disposition_date": "2025-03-03",
        "removed_share": 0.5,
    }
    rec = rv.removal_record(after, " demolition photo ", from_asset_id="a1", revision=2)
    assert rec is not None and rec["share"] == "0.5" and rec["reason"] == "demolition photo"
    got = rv.removal_from_inputs({rv.REMOVAL_INPUT: rec})
    assert got == rv.Removal("kitchen-2025", date(2025, 3, 3), Decimal("0.5"), "demolition photo")
    assert (
        rv.removal_record({"removed_by_project": None}, "r", from_asset_id="a", revision=2) is None
    )
    assert rv.removal_from_inputs({}) is None and rv.removal_from_inputs(None) is None


def test_membership_follows_the_photo_then_the_room() -> None:
    room_after, room_before, room_mixed = uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=3)
    c = [uuid.UUID(int=10 + i) for i in range(6)]
    tags = {str(PHOTO_A): "kitchen-2025", str(PHOTO_B): "kitchen-2025"}
    rooms = {
        room_after: [PHOTO_A, PHOTO_B],
        room_before: [uuid.UUID(int=103)],
        room_mixed: [PHOTO_A, uuid.UUID(int=104)],
    }
    m = rv.membership(
        [
            (c[0], PHOTO_A, room_mixed),  # a per-photo row follows its photo
            (c[1], uuid.UUID(int=104), room_mixed),
            (c[2], None, room_after),  # an inventory row of a room all tagged
            (c[3], None, room_before),
            (c[4], None, room_mixed),  # mixed: stays with the acquisition
            (c[5], None, None),
        ],
        rooms,
        tags,
    )
    assert m.projects == {c[0]: "kitchen-2025", c[2]: "kitchen-2025"}
    assert m.mixed_rooms == frozenset({room_mixed})
    # Two projects in one room is mixed too.
    two = rv.membership([(c[2], None, room_after)], rooms, {str(PHOTO_A): "a", str(PHOTO_B): "b"})
    assert two.projects == {} and room_after in two.mixed_rooms
