"""``evals.schedule_vs_engineer``: Basis's preparer schedule against an engineer's, on the
synthetic report export and a synthetic engineer schedule (no client data)."""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from basis.report.model import build_report
from basis.report.render import render_pdf
from basis.report.schedule import rows, to_csv, to_xlsx
from basis.vision.schemas import COMPONENT_LABELS
from evals import schedule_vs_engineer as sve
from evals.e2e_report import E2ERows, EngineerReport
from tests.unit.report.support import export

D = Decimal


def e2e_rows(items: list[dict[str, Any]] | None = None) -> E2ERows:
    """Five engineer rows over the synthetic export's labels; every other vocabulary label
    unassigned. The Windows row is the engineer's 5-year call against Basis's 27.5."""
    items = items or [
        {"id": "r1", "system": "Carpet", "kind": "A", "assign": [{"label": "carpet"}]},
        {"id": "r2", "system": "Appliances", "kind": "A", "assign": [{"label": "refrigerator"}]},
        {"id": "r3", "system": "Landscaping", "kind": "B", "assign": [{"label": "landscaping"}]},
        {"id": "r4", "system": "Windows", "kind": "A", "assign": [{"label": "window"}]},
        {
            "id": "r5",
            "system": "Framing",
            "kind": "C",
            "assign": [{"label": "structural_framing"}],
        },
        {"id": "r6", "system": "Signage", "kind": "A", "assign": []},
    ]
    used = {a["label"] for r in items for a in r["assign"]}
    unassigned = [
        {"label": label, "reason": "not in this test"}
        for label in COMPONENT_LABELS
        if label not in used
    ]
    return E2ERows.model_validate({"rows": items, "unassigned": unassigned})


def engineer() -> EngineerReport:
    """Residential rental; rows sum to 61,000 = 60,500 + 500, as the synthetic export."""
    return EngineerReport.model_validate(
        {
            "property_class": "residential_rental",
            "building_basis": "60500.00",
            "closing_costs": "500.00",
            "rows": [
                {"id": "r1", "system": "Carpet", "bucket": "y5", "cost": "1000.00"},
                {"id": "r2", "system": "Appliances", "bucket": "y5", "cost": "800.00"},
                {"id": "r3", "system": "Landscaping", "bucket": "y15", "cost": "3000.00"},
                {"id": "r4", "system": "Windows", "bucket": "y5", "cost": "6000.00"},
                {"id": "r5", "system": "Framing", "bucket": "y27_5", "cost": "50000.00"},
                {"id": "r6", "system": "Signage", "bucket": "y15", "cost": "200.00"},
            ],
        }
    )


# ---------------------------------------------------------------- the IRS arithmetic


@pytest.mark.parametrize(
    ("period", "placed", "convention", "rate"),
    [
        ("5", date(2025, 3, 1), "HY", "0.2000"),  # Table A-1
        ("7", date(2025, 3, 1), "HY", "0.1429"),
        ("15", date(2025, 3, 1), "HY", "0.0500"),
        ("5", date(2025, 3, 1), "MQ", "0.3500"),  # Table A-2, first quarter
        ("5", date(2025, 12, 3), "MQ", "0.0500"),  # Table A-5, fourth quarter
        ("7", date(2025, 12, 3), "MQ", "0.0357"),
        ("15", date(2025, 12, 3), "MQ", "0.0125"),
        ("27.5", date(2025, 3, 1), "MM", "0.02879"),  # Table A-6, month 3
        ("39", date(2024, 12, 3), "MM", "0.00107"),  # Table A-7a, month 12
        ("39", date(2024, 1, 15), "MM", "0.02461"),  # Table A-7a, month 1
    ],
)
def test_year1_rates_follow_pub_946_appendix_a(
    period: str, placed: date, convention: str, rate: str
) -> None:
    assert sve.year1_rate(period, placed, convention) == D(rate)  # type: ignore[arg-type]


def test_quarters_are_calendar_quarters() -> None:
    assert [sve.quarter(date(2025, m, 15)) for m in range(1, 13)] == [1] * 3 + [2] * 3 + [3] * 3 + [
        4
    ] * 3


def test_first_year_share_takes_the_allowance_then_the_table_rate() -> None:
    # 40 % bonus, then 20 % of the remaining 60 %: 52 % (Pub 946 chapter 3, chapter 4).
    assert sve.first_year_share("5", date(2025, 3, 1), D("0.40"), "HY") == D("0.52")
    # Elected out: the table rate alone.
    assert sve.first_year_share("5", date(2025, 3, 1), None, "HY") == D("0.20")
    # Real property never takes the allowance, whatever is passed.
    assert sve.first_year_share("39", date(2024, 12, 3), D("0.60"), "MM") == D("0.00107")
    with pytest.raises(ValueError):
        sve.year1_rate("5", date(2025, 3, 1), "MM")


def test_mid_quarter_test_counts_personal_property_in_the_last_quarter() -> None:
    march = [("5", date(2025, 3, 1), D(100)), ("39", date(2025, 3, 1), D(900))]
    assert sve.mid_quarter_test(march) == sve.MidQuarterTest(D(0), D(100), D(0), False)
    december = [("5", date(2024, 12, 3), D(60)), ("15", date(2024, 2, 1), D(40))]
    result = sve.mid_quarter_test(december)
    assert (result.share, result.applies) == (D("0.6000"), True)
    exactly_forty = [("5", date(2024, 12, 3), D(40)), ("15", date(2024, 2, 1), D(60))]
    assert sve.mid_quarter_test(exactly_forty).applies is False  # "more than 40 %"
    real_only = [("39", date(2024, 12, 3), D(100))]
    assert sve.mid_quarter_test(real_only).share is None


# ---------------------------------------------------------------- Basis's schedule


def test_basis_schedule_is_the_production_schedule() -> None:
    document = export()
    report, schedule = sve.basis_schedule(document)
    production = rows(build_report(document))
    assert [s.basis for s in schedule] == [r.basis for r in production]
    assert [s.line for s in schedule] == list(range(1, len(production) + 1))
    assert sum(s.basis for s in schedule) == report.depreciable_basis == D("61000.00")
    landscaping = next(s for s in schedule if "andscaping" in s.description)
    assert landscaping.asset_ids == ("a3", "a4", "a5")  # the folded copies follow their host
    assert (landscaping.period, landscaping.method, landscaping.convention) == ("15", "150DB", "HY")
    assert landscaping.bonus_rate == D("0.40")  # acquired 2024-06-18, placed 2025-03-01
    framing = next(s for s in schedule if s.period == "27.5")
    assert (framing.method, framing.convention, framing.bonus_rate) == ("SL", "MM", None)


def test_regression_against_a_stored_schedule(tmp_path: Path) -> None:
    document = export()
    schedule = rows(build_report(document))
    stored = tmp_path / "rev-2.schedule.csv"
    stored.write_bytes(to_csv(schedule))
    stored.with_suffix(".xlsx").write_bytes(to_xlsx(schedule))
    assert sve.regression(document, stored, stored.with_suffix(".xlsx")) == {
        "csv_equal": True,
        "xlsx_equal": True,
        "rows": len(schedule),
    }
    stored.write_bytes(to_csv(schedule).replace(b"HY", b"MQ", 1))
    assert sve.regression(document, stored, None)["csv_equal"] is False


# ---------------------------------------------------------------- the comparison


def test_compare_matches_rows_and_explains_every_difference() -> None:
    c = sve.compare(export(), e2e_rows(), engineer())
    by_id = {r.row_id: r for r in c.rows}
    # Charged rows in the engineer's period.
    for row_id in ("r1", "r2", "r3", "r5"):
        assert by_id[row_id].matched and by_id[row_id].period_match, row_id
    assert by_id["r3"].lines == [3] and by_id["r3"].tally_count == {"15": 1}
    # The engineer's 5-year windows against Basis's 27.5-year: a bucket difference.
    r4 = by_id["r4"]
    assert r4.matched and r4.period_match is False
    assert (r4.basis_period, r4.engineer_period) == ("27.5", "5")
    assert (r4.basis_method, r4.expected_method) == ("SL", "200DB")
    assert (r4.basis_convention, r4.expected_convention) == ("MM", "HY")
    assert (r4.basis_bonus, r4.expected_bonus) == (None, D("0.40"))
    assert "classification difference" in r4.reason
    # The vocabulary-gap row has no Basis line and says why.
    assert not by_id["r6"].matched and "vocabulary gap" in by_id["r6"].reason
    # Every Basis line is charged somewhere (the synthetic labels cover them all).
    assert c.unmatched_lines == [] and c.unmatched_basis == 0
    # Class shares of each side's own total.
    assert c.class_share_basis["5-year property"] == D("0.0295")  # 1800 / 61000
    assert c.class_share_engineer["5-year property"] == D("0.1279")  # 7800 / 61000
    assert c.class_share_basis["Residential rental property"] == D("0.9213")
    # First-year shares per row: 40 % bonus then Table A-1 for 5-year (52 %); A-6 month 3.
    assert by_id["r1"].first_year_basis["statutory"] == D("0.5200")
    assert by_id["r1"].first_year_engineer["statutory"] == D("0.5200")
    assert by_id["r1"].first_year_basis["no_bonus"] == D("0.2000")
    assert by_id["r1"].first_year_basis["mid_quarter"] == D("0.6100")  # 0.4 + 0.6 x 0.35
    assert by_id["r4"].first_year_basis["statutory"] == D("0.0288")
    assert by_id["r4"].first_year_engineer["statutory"] == D("0.5200")
    # Totals: Basis = (1800 x 0.52 + 3000 x 0.43 + 56200 x 0.02879) / 61000.
    expected = (D(1800) * D("0.52") + D(3000) * D("0.43") + D(56200) * D("0.02879")) / D(61000)
    assert c.first_year_total_basis["statutory"] == expected.quantize(D("0.0001"))
    assert c.first_year_total_engineer["statutory"] > c.first_year_total_basis["statutory"]
    # Placed in March: the 40 % test is not met on either side; Basis prints HY.
    assert (c.mid_quarter_basis.share, c.mid_quarter_basis.applies) == (D(0), False)
    assert c.mid_quarter_engineer.applies is False
    assert c.printed_convention == "HY"


def test_markdown_carries_shares_only_and_json_round_trips() -> None:
    c = sve.compare(export(), e2e_rows(), engineer())
    text = sve.render_markdown(c)
    assert "| r4 | Windows | A | 5 | 27.5 (27.5-yr 1) | 1 |" in text
    assert "vocabulary gap" in text
    for figure in ("61000", "60500", "50000", "6000.00", "1000.00"):
        assert figure not in text, figure
    data = json.loads(json.dumps(sve.to_json(c)))
    assert data["depreciable_basis"] == "61000.00"
    assert data["rows"][3]["first_year_basis"]["statutory"] == "0.0288"
    assert data["mid_quarter_basis"]["applies"] is False


def test_main_writes_the_run_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASIS_EVAL_DATA_DIR", str(tmp_path / "data"))
    document = export()
    export_path = tmp_path / "rev-2.json"
    export_path.write_text(json.dumps(document), encoding="utf-8")
    schedule = rows(build_report(document))
    (tmp_path / "rev-2.schedule.csv").write_bytes(to_csv(schedule))
    rows_path = tmp_path / "e2e_rows_v1.json"
    rows_path.write_text(e2e_rows().model_dump_json(), encoding="utf-8")
    report_path = tmp_path / "engineer_report_v1.json"
    report_path.write_text(engineer().model_dump_json(), encoding="utf-8")
    out = tmp_path / "run"
    code = sve.main(
        [
            "--export",
            str(export_path),
            "--out",
            str(out),
            "--rows",
            str(rows_path),
            "--report",
            str(report_path),
            "--tree",
            str(tmp_path / "tree"),
        ]
    )
    assert code == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tree"]["component"] == "preparer_schedule"
    assert manifest["tree"]["version"] == "template-1"
    assert manifest["calls"]["calls"] == 0 if "calls" in manifest["calls"] else True
    assert [d["name"] for d in manifest["data"]["datasets"]] == ["e2e_rows", "engineer_report"]
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    by_id: dict[str, dict[str, Any]] = {case["id"]: case for case in results["cases"]}
    assert by_id["r1"]["outcome"] == "passed" and by_id["r1"]["kind"] == "row"
    assert by_id["r4"]["outcome"] == "failed"
    assert by_id["r6"]["outcome"] == "failed" and "vocabulary gap" in by_id["r6"]["detail"]
    assert by_id["regression:stored_schedule"]["outcome"] == "passed"
    assert by_id["mid_quarter_test"]["outcome"] == "passed"
    assert by_id["class_share:5-year property"]["outcome"] == "failed"  # 2.95 vs 12.79
    assert by_id["first_year_total:statutory"]["kind"] == "first_year_total"
    summary = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert (summary["rows"], summary["matched"], summary["period_right"]) == (6, 5, 4)
    assert summary["spent_usd"] == 0.0 and summary["regression"]["csv_equal"] is True
    assert (out / "comparison.md").is_file() and (out / "comparison.json").is_file()
    assert not re.search(r"\b61000\b", (out / "comparison.md").read_text(encoding="utf-8"))
    assert (tmp_path / "tree" / "preparer_schedule" / "template-1").is_dir()


def test_a_row_with_lines_in_another_class_says_so() -> None:
    # The engineer's landscaping row also carries the carpet line: 15-year by dollars, with
    # one 5-year line named in the note.
    items: list[dict[str, Any]] = [
        {
            "id": "r3",
            "system": "Landscaping",
            "kind": "B",
            "assign": [{"label": "landscaping"}, {"label": "carpet"}],
        },
        *(
            {"id": f"r{n}", "system": f"Row {n}", "kind": "A", "assign": []}
            for n in (1, 2, 4, 5, 6)
        ),
    ]
    c = sve.compare(export(), e2e_rows(items), engineer())
    r3 = next(r for r in c.rows if r.row_id == "r3")
    assert r3.period_match and r3.basis_period == "15"
    assert r3.tally_count == {"15": 1, "5": 1}
    assert "1 line(s) in 5-year" in r3.reason and "classification difference" in r3.reason
    # Rows without an assignment are not charged.
    assert not next(r for r in c.rows if r.row_id == "r1").matched


def _set_dates(node: Any, placed: str) -> None:
    if isinstance(node, dict):
        if "placed_in_service_date" in node:
            node["placed_in_service_date"] = placed
        for value in node.values():
            _set_dates(value, placed)
    elif isinstance(node, list):
        for value in node:
            _set_dates(value, placed)


def test_report_pdf_regression_reads_the_class_table(tmp_path: Path) -> None:
    document = export()
    pdf = tmp_path / "rev-2.report.pdf"
    pdf.write_bytes(render_pdf(build_report(document)))
    result = sve.report_pdf_regression(document, pdf)
    assert result["classes"] == result["found"] >= 3 and result["missing"] == []
    # A report built with another in-service date prints other class rows: all missing.
    moved = export()
    _set_dates(moved, "2025-12-03")
    assert sve.report_pdf_regression(moved, pdf)["found"] == 0


def test_engineer_choices_need_an_election_term() -> None:
    assert sve.engineer_choices(None)["identifiable"] is None
    periods_only = {"terms_found": {"5 year": [1], "depreciable": [3]}, "terms_missing": []}
    choices = sve.engineer_choices(periods_only)
    assert choices["identifiable"] is False and "no method" in choices["reason"]
    with_bonus = {"terms_found": {"bonus": [2], "5 year": [1]}, "terms_missing": []}
    assert sve.engineer_choices(with_bonus) == {
        "identifiable": True,
        "printed": ["bonus"],
        "reason": "the report prints bonus",
    }
