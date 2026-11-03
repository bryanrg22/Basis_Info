"""``basis.report.model.build_report``: every figure from the export, every total tied to the
cent, every asset on exactly one line (STUDY_REPORT_SPEC 11.3, 11.4, 11.8)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from basis.db.enums import Bucket
from basis.errors.codes import E
from basis.report.inputs import (
    Engineer,
    FirmProfile,
    PriorDepreciation,
    ReportInputs,
    Signature,
    StudyAnswers,
    missing_for_final,
)
from basis.report.model import ReportError, build_report, category_name, masterformat_of, money

from .support import export

ENGINEER = Engineer(name="A. Engineer", credentials="PE", license_number="123", license_state="NY")


def full_inputs(**study: object) -> ReportInputs:
    return ReportInputs(
        firm=FirmProfile(name="Example Engineering", engineer=ENGINEER),
        study=StudyAnswers(
            taxpayer_name="Example Holdings LLC",
            photographs="taxpayer_supplied",
            photographed_by="the owner",
            photographed_on=date(2025, 2, 20),
            **study,  # type: ignore[arg-type]
        ),
        signature=Signature(engineer=ENGINEER, signed_at=datetime(2026, 10, 31, 15, tzinfo=UTC)),
    )


def test_the_totals_tie_and_every_asset_is_on_one_line() -> None:
    r = build_report(export())
    assert r.depreciable_basis == Decimal("61000.00") and r.land == Decimal("9500.00")
    assert [(c.bucket, c.allocated) for c in r.classes] == [
        (Bucket.Y5, Decimal("1800.00")),
        (Bucket.Y15, Decimal("3000.00")),
        (Bucket.Y27_5, Decimal("56200.00")),
    ]
    assert sum(c.share for c in r.classes) == Decimal("1.0000")
    assert r.accelerated == Decimal("4800.00")
    assert sum(i.allocated for i in r.items) == r.depreciable_basis
    assert sum(c.allocated for c in r.categories) == r.depreciable_basis
    placed = sorted([a for i in r.items for a in i.asset_ids] + [w.asset_id for w in r.awaiting])
    assert placed == [f"a{n}" for n in range(1, 9)]
    assert [i.line for i in r.items] == [1, 2, 3, 4, 5]


def test_a_site_wide_quantity_is_one_line_seen_in_every_photograph() -> None:
    [site] = [i for i in build_report(export()).items if i.category_key == "landscaping"]
    assert site.asset_ids == ("a3", "a4", "a5")  # a zero-valued copy and an unpriced one fold
    assert (site.location, site.also_in) == ("Whole property", ("Front", "Rear", "Side"))
    assert site.allocated == Decimal("3000.00")


def test_items_use_one_name_per_rules_row_and_mark_estimates_and_contested() -> None:
    r = build_report(export())
    by = {i.asset_ids[0]: i for i in r.items}
    assert by["a1"].description == "Carpet" and by["a1"].masterformat == ("09 68 10.10 0100",)
    assert by["a2"].masterformat == ()  # priced from the typical unit costs, not a line
    assert by["a7"].description.endswith("(vinyl)")
    assert by["a6"].estimated and by["a6"].contested and by["a6"].location == "Building (concealed)"
    assert (by["a1"].asset_class, by["a6"].asset_class) == ("57.0", None)
    assert [w.asset_id for w in r.awaiting] == ["a8"] and r.contested == 1


def test_the_schedule_carries_dates_and_the_bonus_default_by_class() -> None:
    r = build_report(export())
    rates = {s.terms.bucket: (s.bonus.rate if s.bonus else None) for s in r.schedule}
    assert rates == {Bucket.Y5: Decimal("0.40"), Bucket.Y15: Decimal("0.40"), Bucket.Y27_5: None}
    assert {(s.acquired, s.placed_in_service) for s in r.schedule} == {
        (date(2024, 6, 18), date(2025, 3, 1))
    }


def test_only_open_checks_become_flags() -> None:
    assert [f.code for f in build_report(export()).flags] == ["land_value_date"]


def test_a_report_without_inputs_is_a_draft_naming_what_is_missing() -> None:
    r = build_report(export())
    assert r.draft and r.missing_for_final == ("firm", "study", "signature")
    final = build_report(export(), full_inputs(), report_date=date(2026, 10, 31))
    assert not final.draft and final.missing_for_final == () and not final.look_back
    assert missing_for_final(ReportInputs(firm=full_inputs().firm)) == ("study", "signature")
    look_back = full_inputs(
        prior_depreciation=PriorDepreciation(
            tax_years="2022-2024", depreciation_claimed=Decimal("4000"), method_used="27.5 SL"
        )
    )
    assert build_report(export(), look_back).look_back


def test_the_same_export_gives_the_same_report() -> None:
    assert build_report(export(), report_date=date(2026, 1, 2)) == build_report(
        export(), report_date=date(2026, 1, 2)
    )


# ------------------------------------------------------------------ refusals


def test_not_an_export_and_no_allocation_are_refused() -> None:
    with pytest.raises(ReportError) as e:
        build_report(export(format="other"))
    assert e.value.code == E.REPORT_NOT_AN_EXPORT
    doc = export()
    doc["summary"]["basis"]["allocation"] = None
    doc["summary"]["basis"]["missing"] = ["intake", "repricing"]
    with pytest.raises(ReportError, match=r"missing: intake, repricing") as e:
        build_report(doc)
    assert e.value.code == E.REPORT_NOT_PRODUCED
    doc = export()
    del doc["summary"]["basis"]["allocation"]
    with pytest.raises(ReportError, match="no allocation in the export") as e:
        build_report(doc)
    assert e.value.code == E.REPORT_NOT_PRODUCED


def test_a_withheld_allocation_names_every_blocker_and_its_fix() -> None:
    """EVAL_REPORT 78: the report refused for missing inputs says which, and where to fix."""
    doc = export()
    basis = doc["summary"]["basis"]
    basis["allocation"] = None
    basis["missing"] = ["blockers", "allocation"]
    basis["blockers"] = [
        {
            "code": "input:gla",
            "message": "the gross living area (GLA) is not on the appraisal",
            "assets": 4,
            "fix": "add or correct the field at the appraisal review (improvements.gla_sqft)",
        },
        {
            "code": "cost_index",
            "message": "no RSMeans Historical Cost Index is loaded for 2026",
            "assets": 0,
            "fix": "POST /v1/cost-indices",
        },
    ]
    with pytest.raises(ReportError) as e:
        build_report(doc)
    assert e.value.code == E.REPORT_NOT_PRODUCED
    text = str(e.value)
    assert "missing: blockers, allocation" in text
    assert "the gross living area (GLA) is not on the appraisal (4 assets): add or correct" in text
    assert "no RSMeans Historical Cost Index is loaded for 2026: POST /v1/cost-indices" in text


def test_systems_without_a_priced_line_are_disclosed_not_refused() -> None:
    doc = export()
    doc["summary"]["basis"]["not_produced"] = [
        {"system": "Fire Protection & Alarm", "reason": "no cost row"}
    ]
    r = build_report(doc)
    assert [(u.system, u.reason) for u in r.unpriced_systems] == [
        ("Fire Protection & Alarm", "no cost row")
    ]


@pytest.mark.parametrize(
    "tamper",
    [
        lambda b: b["allocation"]["assets"][0].update(allocated="1000.01"),
        lambda b: b["allocation"].update(total="61000.01"),
        lambda b: b["worksheet"].update(land="9400.00"),
        lambda b: b["worksheet"].update(purchase_price="70000.01"),
    ],
)
def test_a_total_that_does_not_tie_stops_the_report(tamper: object) -> None:
    doc = export()
    tamper(doc["summary"]["basis"])  # type: ignore[operator]
    with pytest.raises(ReportError) as e:
        build_report(doc)
    assert e.value.code == E.REPORT_TOTALS_DO_NOT_TIE


def test_an_asset_the_allocation_knows_but_the_export_does_not_stops_the_report() -> None:
    doc = export()
    doc["summary"]["basis"]["allocation"]["assets"].append(
        {"asset_id": "ghost", "bucket": "y5", "rcn": "0", "allocated": "0.00"}
    )
    with pytest.raises(ReportError) as e:
        build_report(doc)
    assert e.value.code == E.REPORT_ASSET_UNACCOUNTED


# ------------------------------------------------------------------ helpers


def test_masterformat_lines_are_read_in_order_once() -> None:
    assert masterformat_of(
        "RSMeans Residential 2020 08 71 20.40 0400 and 08 71 20.41 1420, p. 474; 08 71 20.40 0400"
    ) == ("08 71 20.40 0400", "08 71 20.41 1420")
    assert masterformat_of("RSMeans 2020 32 92 23.10 0200") == ("32 92 23.10 0200",)
    assert masterformat_of("TYPICAL_UNIT_COSTS[carpet]") == () and masterformat_of(None) == ()


def test_category_names_come_from_the_rules_table_else_the_label() -> None:
    assert category_name("Carpet")[0] == "carpet"
    assert category_name("flooring", "vinyl")[0] == "vinyl_flooring"  # resolved by material
    assert category_name("mystery_widget") == ("mystery_widget", "Mystery widget")
    assert category_name(None) == ("other", "Other")


def test_money_is_cents() -> None:
    assert money("1.005") == Decimal("1.01") and money(None) == Decimal(0) and money("") == 0


def test_a_logo_must_be_a_png_and_travels_as_base64() -> None:
    import base64

    from pydantic import ValidationError

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    firm = FirmProfile.model_validate(
        {"name": "Example Engineering", "logo_png": base64.b64encode(png).decode()}
    )
    assert firm.logo_png == png
    assert firm.model_dump(mode="json")["logo_png"] == base64.b64encode(png).decode()
    assert FirmProfile.model_validate(firm.model_dump(mode="json")) == firm
    with pytest.raises(ValidationError, match="must be a PNG"):
        FirmProfile.model_validate({"name": "x", "logo_png": base64.b64encode(b"GIF89a").decode()})
    with pytest.raises(ValidationError, match="at most 524288"):
        FirmProfile.model_validate(
            {"name": "x", "logo_png": base64.b64encode(png + bytes(524_288)).decode()}
        )
