"""The client report shows a line the engineer priced (EVAL_REPORT 90): Appendix 1 marks it
with the price and its source, section 7 says what the mark means, the methodology counts
the lines; the dollars are the allocation's, so the schedule carries them unchanged. A
report with no engineer's price has none of it."""

from __future__ import annotations

from datetime import date
from typing import Any

from basis.report.model import build_report, engineer_price
from basis.report.render import render_pdf, view
from basis.report.schedule import rows

from .support import export
from .test_model import full_inputs
from .test_render import text_of

SOURCE = "Acme Flooring bid B-2291"


def engineer_trail() -> dict[str, Any]:
    return {
        "kind": "engineer_price",
        "quantity": {"value": "120", "unit": "SF"},
        "override": {"unit_cost": "10", "source": SOURCE, "reason": "bid on file"},
    }


def with_engineer_carpet() -> dict[str, Any]:
    document = export()
    for asset in document["assets"]:
        if asset["id"] == "a1":
            asset["cost_trail"] = engineer_trail()
    return document


def test_the_line_carries_the_price_and_its_source() -> None:
    report = build_report(with_engineer_carpet(), full_inputs(), report_date=date(2026, 11, 5))
    [carpet] = [i for i in report.items if "a1" in i.asset_ids]
    assert carpet.engineer_price == f"$10.00 per SF: {SOURCE}"
    assert all(i.engineer_price is None for i in report.items if i is not carpet)
    [fact] = [m for m in report.methodology if m.label == "Engineer's prices"]
    assert fact.value.startswith("1 item priced at the unit price the engineer set")
    data = view(report, ())
    assert data["engineer_prices"] == "1"


def test_the_pdf_marks_the_line_and_explains_the_mark() -> None:
    report = build_report(with_engineer_carpet(), full_inputs(), report_date=date(2026, 11, 5))
    # Typst sets the apostrophe as a typographic one.
    text = " ".join(text_of(render_pdf(report)).split()).replace("\u2019", "'")
    assert f"engineer's price: $10.00 per SF: {SOURCE}" in text
    assert "Items marked engineer's price (1) are priced at the unit price the engineer set" in text


def test_a_report_without_one_says_nothing_of_it() -> None:
    report = build_report(export(), full_inputs(), report_date=date(2026, 11, 5))
    assert all(i.engineer_price is None for i in report.items)
    assert not any(m.label == "Engineer's prices" for m in report.methodology)
    text = text_of(render_pdf(report)).replace("\u2019", "'")
    assert "engineer's price" not in text.lower()


def test_the_schedule_rows_are_the_allocations_either_way() -> None:
    plain = rows(build_report(export(), full_inputs()))
    priced = rows(build_report(with_engineer_carpet(), full_inputs()))
    assert [r.values for r in plain] == [r.values for r in priced]


def test_only_an_engineer_price_trail_names_one() -> None:
    assert engineer_price({"cost_trail": None}) is None
    assert engineer_price({"cost_trail": {"kind": "unit_price"}}) is None
    assert engineer_price({"cost_trail": {"kind": "engineer_price"}}) is None  # no override
    assert engineer_price({"unit": "EA", "cost_trail": engineer_trail()}) == (
        f"$10.00 per EA: {SOURCE}"
    )
