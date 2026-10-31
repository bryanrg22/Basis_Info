"""A study with a complete intake runs start -> gates -> export, and the export carries what
the intake decided (EVAL_REPORT section 37).

The happy path (``test_happy_path.py``) seeds no intake, as every study did before the intake
was required. Here the study has one: a nonresidential use (a house let by the night), placed
in service in 2024, in New York. The workflow is the real one on the time-skipping server;
the assertions are on the stored export: ``summary.basis`` has the worksheet, the
structure model's rows and reconciliation and the allocation on RCNLD (no ``missing``;
STRUCTURE_MODEL.md, EVAL_REPORT 44), the schedule's building rows are 39-year, and
``summary.takeoff`` names the intake as the source of the cost year, the location factor and
the property class. The purchase price is set so the synthetic appraisal's cost new less
its depreciation lands within the reconciliation's tolerance of the price less land.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from lifecycle_support import Harness
from lifecycletest_support import intake_document, set_intake

from basis.costs.refresh import manual_entry, record_manual
from basis.db.enums import Stage, StudyStatus
from basis.db.session import session_scope

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

NEW_YORK = (
    "zip 10001 (intake): 100-102 New York 1.01, "
    "RSMeans Residential 2020 Location Factors - Residential p.720"
)


async def test_a_study_with_a_complete_intake_runs_to_its_export(
    harness: Harness, tmp_path: Path
) -> None:
    # The 2024 cost year is after the last index the 2020 books publish; the firm's remedy
    # (COST_INDICES.md section 10) is a value in the store, so one is entered here by the
    # manual path with a synthetic 2024 value (no published value is in the repository) and
    # the rows carry no assumption. tests/unit/activities/test_process_assets.py covers the
    # flag without one.
    async with session_scope(harness.sessionmaker) as session:
        rows, run, _ = await record_manual(
            session,
            [
                manual_entry(
                    series="historical",
                    year=2024,
                    value=Decimal("326.6"),
                    as_of=date(2024, 1, 1),
                    citation="synthetic fixture (test_intake_takeoff.py), not published",
                )
            ],
            actor_id=None,
            actor_name="test_intake_takeoff.py",
        )
        assert len(rows) == 1 and rows[0].version == 1 and run.rows_added == 1
    try:
        study = await harness.seed()
        await set_intake(
            harness.sessionmaker,
            study.study_id,
            intake_document(
                # the fabricated house reconciles at this price since STRUCTURE_MODEL.md
                # section 9 (its 2.1 baths and central air priced as RSMeans modifications)
                purchase_price="450000.00",
                property_use="nonresidential",
                dwelling_unit_income_share=None,
                transient_lodging=True,
                placed_in_service_date="2024-03-01",
                acquisition_date="2024-02-15",
                state="NY",
                zip="10001",
            ),
        )
        async with harness.worker():
            started = await harness.start(study)
            handle = started.handle
            await harness.wait_awaiting(handle, "appraisal_review", 1)
            await harness.approve_all(handle)
            result = await harness.result(handle)

        assert result.outcome == "completed"
        assert (result.stage, result.revision) == ("completed", 1)
        sequence = await harness.activity_sequence(handle)
        assert Counter(sequence)["process_assets"] == 1 and sequence[-3] == "export_report"
        row = await harness.study_row(started.study_id)
        assert (row.current_stage, row.status) == (Stage.COMPLETED, StudyStatus.COMPLETED)
        assert row.intake is not None and row.intake.state == "NY"

        exported = json.loads(await harness.deps.storage.read(result.export_uri or ""))
        assert exported["revision"] == 1
        itemized = [a for a in exported["assets"] if a["component_id"] is not None]
        concealed = {a["label"]: a for a in exported["assets"] if a["component_id"] is None}
        assert len(itemized) == 6 and len(concealed) == 14

        # The structure model (STRUCTURE_MODEL.md): the synthetic appraisal's cost approach gives
        # the building's RCN (Average 2 Story: Q4, two stories), one concealed row per system,
        # the appraiser's physical depreciation on every row, and a schedule that reconciles
        # to the price less land within tolerance, so the basis is allocated pro rata on RCNLD.
        basis = exported["summary"]["basis"]
        assert basis["intake"] is True and basis["missing"] == [], basis["reconciliation"]
        structure = basis["structure"]
        assert structure["building_rcn"]["source"] == "square_foot_model"  # the sweep's pick
        assert structure["building_rcn"]["model"]["name"] == "Average 2 Story"
        assert sorted(c["label"] for c in structure["concealed"]) == sorted(concealed)
        foundation = concealed["foundation"]
        assert foundation["bucket"] == "y39" and foundation["method"] == "rule"
        assert foundation["takeoff_inputs"]["concealed"] is True
        assert foundation["takeoff_inputs"]["system"] == "Miscellaneous Building Components"
        assert foundation["unit"] == "SF" and foundation["total"] is not None
        # The building RCN the concealed rows are carved from takes the same resolved year
        # factor as the itemized rows (the store's), not the book's alone.
        assert "x 1.3660 for 2024 (HCI 326.6 (Jan 2024)" in structure["building_rcn"]["formula"]
        assert foundation["takeoff_inputs"]["building_rcn"] == structure["building_rcn"]["amount"]
        assert "ATG ch. 4 C.11" in foundation["takeoff_inputs"]["overhead_spread"]
        assert concealed["data_cabling"]["bucket"] == "y5"
        assert concealed["access_control"]["needs_review"] is True  # contested row
        assert basis["depreciation"]["method"] == "appraisal_physical_depreciation"
        reconciliation = basis["reconciliation"]
        assert reconciliation["status"] == "reconciled", reconciliation["checks"]
        assert abs(float(reconciliation["gap_share"])) <= 0.10
        assert [c["code"] for c in reconciliation["causes"]][:2] == ["land_fmv", "assets_missing"]
        allocation = basis["allocation"]
        assert allocation["depreciated"] is True and allocation["total"] == "360180.00"
        assert {c["code"]: c["severity"] for c in allocation["checks"]} == {"rcnld_factor": "ok"}
        assert allocation["by_system"] is not None
        # The harness configures no cost table, so the four small systems (priced from the
        # table's row for their label) stay unpriced and are listed as not produced; the nine
        # building rows are priced from the appraisal's cost new.
        assert [n["system"] for n in basis["not_produced"]] == [
            "Fire Protection & Alarm",
            "Communication / Data",
            "Access Control",
            "Site Utilities",
        ]
        assert all(
            concealed[label]["total"] is None for label in ("fire_alarm_system", "data_cabling")
        )
        worksheet = basis["worksheet"]
        assert (worksheet["property_use"], worksheet["recovery_period"]) == ("nonresidential", "39")
        assert (worksheet["placed_in_service_date"], worksheet["state"], worksheet["zip"]) == (
            "2024-03-01",
            "NY",
            "10001",
        )
        assert worksheet["location_source"] == "intake"
        assert (
            worksheet["building_basis"] == "360000.00" and worksheet["total_basis"] == "360180.00"
        )

        # The use decided the building period (39 years on the worksheet): the fixture's
        # bathtub and toilet are building rows since RULES_VERSION 2026-10-29.1 (ATG p.315), so
        # they take 39 years for this nonresidential use; the cabinets and countertops are
        # section 1245 rule hits, 5 years.
        buckets = {a["label"]: a["bucket"] for a in exported["assets"]}
        assert {buckets["bathtub"], buckets["toilet"]} == {"y39"}
        assert {a["bucket"] for a in exported["assets"]} == {"y5", "y39"}
        assert all(a["recovery_period"] != "27.5" for a in exported["assets"])

        # The takeoff inputs, read back from the rows: the intake's year and state applied to
        # every cost row, and the class came from the intake's use.
        takeoff = exported["summary"]["takeoff"]
        # The intake's ZIP names the city in the book (EVAL_REPORT 45): 100-102 New York 1.01;
        # the store's 2024 index prices the year (326.6 / 239.1 = 1.3660), no assumption.
        assert (takeoff["cost_year"], takeoff["location_factor"]) == (2024, "1.010")
        assert takeoff["cost_year_source"] == "placed in service 2024-03-01 (intake)"
        assert takeoff["location_source"] == NEW_YORK
        assert takeoff["cost_year_assumption"] is None
        assert (takeoff["property_class"], takeoff["property_class_source"]) == (
            "nonresidential",
            "intake property_use",
        )
        assert takeoff["area_sources"]["structure_model"] == 14
        assert sum(takeoff["area_sources"].values()) == 20
        for asset in itemized:
            assert (asset["cost_year"], asset["location_factor"]) == (2024, "1.010")
            assert asset["takeoff_inputs"]["property_class_source"] == "intake property_use"
            assert "cost_year_assumption" not in asset["takeoff_inputs"]
            if asset["cost_source"] and not asset["cost_source"].startswith("missing"):
                assert (
                    "x1.3660 for 2024 = HCI 326.6 (Jan 2024) [synthetic fixture"
                    in (asset["cost_source"])
                )
                assert asset["cost_source"].endswith(
                    f", from placed in service 2024-03-01 (intake); x1.01 location from {NEW_YORK})"
                )
                assert "cost:YEAR_INDEX_MISSING" not in (asset["review_reason"] or "")
        # A concealed building row priced from the square-foot model (national dollars) carries
        # the study's location factor, like the itemized rows (with STRUCTURE_RCN_SOURCE set to
        # the appraisal's local figure it would carry none); a small system is priced from the
        # cost table like any label, so it carries one.
        assert concealed["foundation"]["location_factor"] == "1.010"
        assert concealed["access_control"]["location_factor"] in ("1.010", None)
        assert all(a["cost_year"] == 2024 for a in concealed.values())
    finally:
        await harness.cleanup()
