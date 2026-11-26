"""Pricing rule (a) in the result (EVAL_REPORT 110): a row priced on an unverified draft whose
exposure was above the threshold is the blocker ``measure:draft_unverified`` and the allocation
is withheld; the same complete house with the draft verified (or under the threshold) allocates
exactly as before. ``annotations_of`` reads the row's draft record as the loaders do."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from basis.domain import measure_drafts as md
from basis.evidence.result import annotations_of, compute_result
from tests.unit.evidence.test_result import PRODUCED, clean, intake

D = Decimal


def rows(carpet_inputs: dict[str, object] | None) -> list:  # type: ignore[type-arg]
    priced = [r for r in PRODUCED if r.rcn is not None]
    if carpet_inputs is None:
        return priced
    return [
        replace(r, **annotations_of(carpet_inputs)) if r.label == "carpet" else r for r in priced
    ]


def record(**over: object) -> dict[str, object]:
    base = {
        "drawn_by": "model:claude-sonnet-5-5@measure_interior_reference-v1",
        "verified": False,
        "quantity": "60.00",
        "unit": "SY",
        "expected_error_pct": "22.5",
    }
    return {**base, **over}


def result_of(carpet_inputs: dict[str, object] | None):  # type: ignore[no-untyped-def]
    return compute_result(rows(carpet_inputs), intake(purchase_price="180000.00"), 1, clean())  # type: ignore[arg-type]


def test_an_unverified_draft_above_the_threshold_withholds_the_report() -> None:
    held = md.priced(record(), D("5000.00"), D("100"))  # exposure 1,125.00 > 100
    result = result_of({md.DRAFT: held})
    assert [(b.code, b.assets) for b in result.blockers] == [("measure:draft_unverified", 1)]
    blocker = result.blockers[0]
    assert blocker.fix == "verify or edit this measurement in the measure tool"
    assert "$100.00" in blocker.message and "1 measurement drafted by Basis" in blocker.message
    assert result.allocation is None and result.missing == ["blockers", "allocation"]


def test_under_the_threshold_verified_or_replaced_it_allocates_as_before() -> None:
    plain = result_of(None)
    assert plain.blockers == [] and plain.allocation is not None
    under = md.priced(record(), D("5000.00"), D("2000"))  # 1,125.00 is not above 2,000
    verified = md.resolved(
        md.priced(record(), D("5000.00"), D("1")), verified=True, by="engineer", quantity="60.00 SY"
    )
    replaced = md.resolved(
        md.priced(record(), D("5000.00"), D("1")),
        verified=False,
        by="engineer",
        quantity="55.00 SY",
    )
    for inputs in ({md.DRAFT: under}, {md.DRAFT: verified}, {md.DRAFT: replaced}, {}):
        result = result_of(inputs)
        assert result.blockers == [] and result.allocation == plain.allocation


def test_annotations_of_reads_only_a_holding_draft() -> None:
    assert "draft_blocks" not in annotations_of({md.DRAFT: record()})  # never priced
    assert "draft_blocks" not in annotations_of(None)
    held = md.priced(record(), D("5000.00"), D("100"))
    assert annotations_of({md.DRAFT: held})["draft_blocks"] == D("100")
