"""Evidence pack assembly: dedupe, grouping, ordering, resolvable entries, summary.

BUILD_PLAN 6.2 ``node/test_build_evidence_pack.py`` (dedupe, per-stage grouping, every
entry resolvable) on in-memory rows; ``tests/node/test_export_report.py`` covers the
same through Postgres.
"""

from __future__ import annotations

import random
import uuid

from basis.evidence.pack import EVIDENCE_STAGE, assemble_pack, entry_id

from .evidencetest_rows import CHUNK_A, CHUNK_B, rows


def test_assets_are_ordered_by_room_label_category() -> None:
    pack = assemble_pack(**rows())
    assert [(a.room_name, a.label) for a in pack.assets] == [
        ("Bathroom", "toilet"),
        ("Kitchen", "carpet"),
        ("Kitchen", "kitchen_cabinet"),
    ]


def test_duplicate_citations_collapse_and_entries_resolve() -> None:
    pack = assemble_pack(**rows())
    carpet = next(a for a in pack.assets if a.label == "carpet")
    assert len(carpet.citations) == 2  # three rows, one a normalized duplicate
    entries = [pack.entry(eid) for eid in carpet.citations]
    # ordered by document, then page
    assert [e.doc_id for e in entries] == ["IRS_IRS_COST_SEG_ATG__2024", "IRS_REV_PROC_87_56"]
    assert entries[0].reference() == "IRS_IRS_COST_SEG_ATG__2024, pp.412-415"
    assert entries[1].reference() == "IRS_REV_PROC_87_56, p.12"
    assert {e.chunk_id for e in pack.entries} == {str(CHUNK_A), str(CHUNK_B)}
    assert len(pack.entries) == 3


def test_groupings_by_stage_component_document() -> None:
    pack = assemble_pack(**rows())
    ids = [e.id for e in pack.entries]
    assert pack.by_stage == {EVIDENCE_STAGE: ids}
    assert sum(len(v) for v in pack.by_component.values()) == 3
    assert set(pack.by_document) == {"IRS_IRS_COST_SEG_ATG__2024", "IRS_REV_PROC_87_56"}
    assert len(pack.by_document["IRS_IRS_COST_SEG_ATG__2024"]) == 2


def test_entry_ids_are_stable() -> None:
    first = entry_id("a", "c", "Carpeting - 5-year")
    assert first == entry_id("a", "c", "  carpeting -  5-YEAR ")
    assert first != entry_id("b", "c", "Carpeting - 5-year")


def test_summary_totals_by_bucket() -> None:
    summary = assemble_pack(**rows()).summary
    assert summary["assets"] == 3 and summary["needs_review"] == 1
    assert summary["total_cost"] == "16477.00"
    assert summary["by_bucket"]["y5"] == {
        "assets": 2,
        "total": "16477.00",
        "percent_of_total": "100.00",
    }
    assert summary["by_bucket"]["y27_5"]["total"] == "0.00"
    toilet = str(uuid.UUID(int=23))
    assert summary["assets_without_cost"] == [toilet]
    assert summary["assets_without_evidence"] == [toilet]
    assert (summary["total_citations"], summary["unique_documents"]) == (3, 2)
    assert summary["by_method"] == {"rule": 3}


def test_input_order_does_not_change_the_pack() -> None:
    reference = assemble_pack(**rows())
    for seed in range(5):
        shuffled = rows()
        random.Random(seed).shuffle(shuffled["assets"])
        random.Random(seed).shuffle(shuffled["citations"])
        assert assemble_pack(**shuffled) == reference


def test_empty_revision() -> None:
    pack = assemble_pack(
        study_id="s",
        revision=2,
        assets=[],
        components={},
        rooms={},
        takeoffs={},
        costs={},
        citations=[],
        chunks={},
    )
    assert pack.assets == () and pack.entries == ()
    assert pack.summary["total_cost"] == "0" and pack.summary["by_bucket"] == {}


def test_summary_carries_the_basis_result() -> None:
    from datetime import date

    from basis.domain.intake import StudyIntakeIn, with_location

    without = assemble_pack(**rows()).summary["basis"]
    assert (without["intake"], without["missing"], without["allocation"]) == (
        False,
        ["intake"],
        None,
    )
    assert without["schedule"]["rcn_total"] == "16477.00"
    intake = with_location(
        StudyIntakeIn.model_validate(
            {
                "purchase_price": "100000.00",
                "land_method": "appraisal_site_value",
                "appraisal_site_value": "20000.00",
                "property_use": "nonresidential",
                "transient_lodging": True,
                "placed_in_service_date": date(2025, 5, 1),
            }
        ),
        appraisal_state=None,
        appraisal_zip=None,
    )
    with_intake = assemble_pack(**rows(), intake=intake).summary["basis"]
    assert with_intake["worksheet"]["total_basis"] == "80000.00"
    assert with_intake["allocation"]["rows_sum"] == "80000.00"
    assert with_intake["allocation"]["by_bucket"][0]["allocated"] == "80000.00"
    assert with_intake["revision"] == 1
