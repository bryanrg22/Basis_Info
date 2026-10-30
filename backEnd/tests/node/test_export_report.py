"""``export_report`` on Postgres: the evidence pack of a processed revision, byte-stable.

BUILD_PLAN 6.2 ``node/test_export_report.py`` (same revision -> byte-identical output) and
``node/test_build_evidence_pack.py`` (every entry resolvable to its chunk, grouped).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json

import pytest
from classifytest_support import CARPET_CHUNK, STRUCTURAL_CHUNK
from lifecycletest_support import SeededStudy
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.activities.export_report import ExportReport
from basis.activities.process_assets import ProcessAssets
from basis.classify.store_protocol import InMemoryChunkStore
from basis.config import Settings
from basis.db.session import session_scope
from basis.evidence.export import CSV_COLUMNS, pack_to_json
from basis.evidence.pack import load_pack
from basis.storage import LocalStorage
from basis.workflow.contracts import ExportReportInput, ProcessAssetsInput

from .conftest import Env, node_deps
from .test_process_assets import scripted_turns, seed_components

pytestmark = pytest.mark.integration


async def test_same_revision_exports_byte_identical_files_with_resolved_evidence(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    db_test_settings: Settings,
    reference_store: InMemoryChunkStore,
    study: SeededStudy,
    activity_env: Env,
) -> None:
    deps = node_deps(sessionmaker, storage, db_test_settings, reference_store, scripted_turns())
    await seed_components(sessionmaker, study.study_id)
    sid = str(study.study_id)
    await activity_env.run(
        ProcessAssets(deps).run,
        ProcessAssetsInput(study_id=sid, revision=1, idempotency_key="k-a", budget_remaining_usd=1),
    )
    export = ExportReport(deps)
    first = await activity_env.run(
        export.run, ExportReportInput(study_id=sid, revision=1, idempotency_key="k-e1")
    )
    json_bytes = await storage.read(first.export_uri)
    csv_uri = first.export_uri.replace(".json", ".assets.csv")
    csv_bytes = await storage.read(csv_uri)
    second = await activity_env.run(
        export.run, ExportReportInput(study_id=sid, revision=1, idempotency_key="k-e2")
    )
    assert second.sha256 == first.sha256 == hashlib.sha256(json_bytes).hexdigest()
    assert await storage.read(second.export_uri) == json_bytes
    assert await storage.read(csv_uri) == csv_bytes

    document = json.loads(json_bytes)
    assets = {a["label"]: a for a in document["assets"]}
    assert set(assets) == {"carpet", "flooring", "garage_door", "refrigerator"}
    carpet = assets["carpet"]
    assert (carpet["bucket"], carpet["quantity"], carpet["unit"], carpet["total"]) == (
        "y5",
        "18.3300",
        "SY",
        "1121.80",  # 61.20/SY x 1.0000: 2026 is after the last published index (EVAL_REPORT 45)
    )
    references = sorted(c["reference"] for c in carpet["citations"])
    assert references == [
        "IRS_IRS_COST_SEG_ATG__2024, p.297",
        "IRS_IRS_COST_SEG_ATG__2024, p.307",
    ]
    assert {c["corpus"] for c in carpet["citations"]} == {"reference"}
    door = assets["garage_door"]
    assert [c["chunk_id"] for c in door["citations"]] == [str(STRUCTURAL_CHUNK)]
    assert assets["flooring"]["citations"] == []

    summary = document["summary"]
    # carpet's two citations, the garage door's one, and the refrigerator's 57.0 citation
    # (ATG p.297, shared with carpet); its kitchen-appliance citation has no test chunk. Every
    # priced row is flagged at the settings' 2026 cost year (no published index; EVAL_REPORT 45).
    assert (summary["assets"], summary["needs_review"], summary["total_citations"]) == (4, 4, 4)
    assert summary["by_method"] == {"llm": 2, "rule": 2}
    assert document["files"]["assets_csv"]["sha256"] == hashlib.sha256(csv_bytes).hexdigest()
    every_entry = {c["id"] for a in document["assets"] for c in a["citations"]}
    assert set(document["evidence"]["by_stage"]["assets_review"]) == every_entry
    assert str(CARPET_CHUNK) in {c["chunk_id"] for c in carpet["citations"]}

    rows = list(csv.reader(io.StringIO(csv_bytes.decode())))
    assert tuple(rows[0]) == CSV_COLUMNS and len(rows) == 5

    # The pack loaded directly equals the exported section.
    async with session_scope(sessionmaker) as session:
        pack = await load_pack(session, study.study_id, 1)
    # The export adds each asset's ``provenance`` (docs/api/TIMELINE_AND_AUDIT.md).
    without_provenance = [
        {key: value for key, value in asset.items() if key != "provenance"}
        for asset in document["assets"]
    ]
    assert json.loads(pack_to_json(pack))["assets"] == without_provenance
    assert {a["provenance"]["origin"] for a in document["assets"]} == {"model"}


async def test_export_of_an_empty_revision(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    db_test_settings: Settings,
    reference_store: InMemoryChunkStore,
    study: SeededStudy,
    activity_env: Env,
) -> None:
    deps = node_deps(sessionmaker, storage, db_test_settings, reference_store, [])
    output = await activity_env.run(
        ExportReport(deps).run,
        ExportReportInput(study_id=str(study.study_id), revision=1, idempotency_key="k-e"),
    )
    document = json.loads(await storage.read(output.export_uri))
    assert document["assets"] == [] and document["summary"]["assets"] == 0
