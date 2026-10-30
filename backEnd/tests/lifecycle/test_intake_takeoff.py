"""A study with a complete intake runs start -> gates -> export, and the export carries what
the intake decided (EVAL_REPORT section 37).

The happy path (``test_happy_path.py``) seeds no intake, as every study did before the intake
was required. Here the study has one: a nonresidential use (a house let by the night), placed
in service in 2024, in New York. The workflow is the real one on the time-skipping server;
the assertions are on the stored export: ``summary.basis`` has the worksheet and the
allocation (no ``missing``), the schedule's building rows are 39-year, and
``summary.takeoff`` names the intake as the source of the cost year, the location factor and
the property class.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest
from lifecycle_support import Harness
from lifecycletest_support import intake_document, set_intake

from basis.db.enums import Stage, StudyStatus

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_a_study_with_a_complete_intake_runs_to_its_export(harness: Harness) -> None:
    study = await harness.seed()
    await set_intake(
        harness.sessionmaker,
        study.study_id,
        intake_document(
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
    assert exported["revision"] == 1 and len(exported["assets"]) == 6

    # The basis: the worksheet from the intake. The fixture's six assets price on the six
    # legacy cost rows; when none of them does, the result says the allocation is missing
    # rather than inventing one (docs/api/STUDY_RESULT.md, ``missing``).
    basis = exported["summary"]["basis"]
    assert basis["intake"] is True
    if basis["schedule"]["priced"] == 0:
        assert basis["missing"] == ["allocation"] and basis["allocation"] is None
    else:
        assert basis["missing"] == [] and basis["allocation"]["total"] == "560180.00"
    worksheet = basis["worksheet"]
    assert (worksheet["property_use"], worksheet["recovery_period"]) == ("nonresidential", "39")
    assert (worksheet["placed_in_service_date"], worksheet["state"], worksheet["zip"]) == (
        "2024-03-01",
        "NY",
        "10001",
    )
    assert worksheet["location_source"] == "intake"
    assert worksheet["building_basis"] == "560000.00" and worksheet["total_basis"] == "560180.00"

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
    assert (takeoff["cost_year"], takeoff["location_factor"]) == (2024, "1.200")
    assert takeoff["cost_year_source"] == "placed in service 2024-03-01 (intake)"
    assert takeoff["location_source"] == "state NY (intake)"
    assert (takeoff["property_class"], takeoff["property_class_source"]) == (
        "nonresidential",
        "intake property_use",
    )
    assert sum(takeoff["area_sources"].values()) == 6
    for asset in exported["assets"]:
        assert (asset["cost_year"], asset["location_factor"]) == (2024, "1.200")
        assert asset["takeoff_inputs"]["property_class_source"] == "intake property_use"
        if asset["cost_source"] and not asset["cost_source"].startswith("missing"):
            assert asset["cost_source"].endswith(
                "from placed in service 2024-03-01 (intake), x1.20 location from state NY (intake))"
            )
