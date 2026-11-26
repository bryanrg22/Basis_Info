"""The app's draft screens read what the API serves (EVAL_REPORT 110; docs/api/MEASURE_DRAFTS.md).

``web/tests/fixtures/measure-drafts-api.json`` is the real ``GET .../assets`` items and
``GET .../result`` blockers for drafted rows, serialized by the API's own functions
(``asset_out`` with its provenance, ``compute_result``) after ``process_assets`` ran on
Postgres with both draft settings on: the measured house's roof, siding and gutter drafted from a
stored shell, the kitchen's countertop, wall and flooring from a room draft; then the same study
priced again with its pin unconfirmed, whose exterior rows say why they were not drafted. Ids
and timestamps are replaced by placeholders so the file is stable; the figures are the
server's. ``web/tests/measure-drafts-display.test.cjs`` checks the sample API's drafted rows
and the app's texts against it, so a change to the API's draft shapes that the app or the sample
does not follow fails there; this test fails here when the API changes and the fixture was not
rewritten (``BASIS_WRITE_FIXTURES=1``)."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from lifecycletest_support import SeededStudy
from sqlalchemy import delete, update

from basis.activities import Deps
from basis.activities.process_assets import ProcessAssets
from basis.api.routes.outputs import asset_out
from basis.db.models import Asset, Citation, CostEstimate, PropertyPin, Takeoff
from basis.db.repositories.outputs import OutputRepository
from basis.db.repositories.provenance import ProvenanceResolver
from basis.db.session import session_scope
from basis.domain import measure_drafts as md
from basis.evidence.result import compute_result, load_schedule

from .conftest import Env
from .test_measure_drafts_pipeline import (
    add_exterior,
    add_kitchen_rows,
    kitchen_draft,
    seed_shell,
    settings_for,
)
from .test_process_assets import MEASURED_FIELDS, process, seed_extraction
from .test_process_assets import analyzed as analyzed  # noqa: F401 - the fixture

pytestmark = pytest.mark.integration

FIXTURE = (
    Path(__file__).resolve().parents[4] / "web" / "tests" / "fixtures" / "measure-drafts-api.json"
)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?")


def _stable(value: Any) -> Any:
    """Ids and timestamps as placeholders (new on every run); every other value as served."""
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    if isinstance(value, str):
        return _AT.sub("<at>", _UUID.sub("<id>", value))
    return value


async def _served(deps: Deps, study_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope(deps.sessionmaker) as session:
        page = await OutputRepository(session).assets(study_id, 1, limit=200, cursor=None)
        provenance = await ProvenanceResolver(session, study_id).assets(
            [row.asset for row in page.items],
            {row.component.id: row.component for row in page.items if row.component is not None},
        )
        items = [
            asset_out(row, provenance.get(row.asset.id)).model_dump(mode="json")
            for row in page.items
        ]
        schedule = await load_schedule(session, study_id, 1)
    result = compute_result(schedule, None, 1).model_dump(mode="json")
    return {"items": items, "blockers": result["blockers"]}


def _keep(item: dict[str, Any]) -> bool:
    inputs = (item.get("takeoff") or {}).get("inputs") or {}
    return md.DRAFT in inputs or "draft_not_used" in inputs


def _order(item: dict[str, Any]) -> tuple[str, str]:
    takeoff = item.get("takeoff") or {}
    return (str(item.get("label") or item.get("category")), str(takeoff.get("quantity")))


async def test_the_web_reference_is_what_the_api_serves(
    deps: Deps,
    analyzed: SeededStudy,
    activity_env: Env,  # noqa: F811
) -> None:
    await seed_extraction(deps, analyzed, MEASURED_FIELDS)
    await add_exterior(deps, analyzed)
    kitchen = await add_kitchen_rows(deps, analyzed)
    await seed_shell(deps, analyzed)
    on = settings_for(
        deps,
        measure_drafts_exterior=True,
        measure_drafts_interior=True,
        measure_draft_verify_usd=1.0,
    )
    given = process(analyzed).model_copy(
        update={"room_drafts": (kitchen_draft(kitchen, analyzed),)}
    )
    await activity_env.run(ProcessAssets(on).run, given)
    drafted = await _served(deps, analyzed.study_id)
    items = sorted((i for i in drafted["items"] if _keep(i)), key=_order)
    # The same study priced again with the pin unconfirmed: the roof keeps today's quantity
    # and says why it was not drafted (``draft_not_used``).
    async with session_scope(deps.sessionmaker) as session:
        for table in (Citation, CostEstimate, Takeoff, Asset):
            await session.execute(delete(table))
        await session.execute(update(PropertyPin).values(confirmed=False))
    exterior = settings_for(deps, measure_drafts_exterior=True)
    await activity_env.run(ProcessAssets(exterior).run, process(analyzed, key="k-unconfirmed"))
    undrafted = await _served(deps, analyzed.study_id)
    not_used = sorted(
        (i for i in undrafted["items"] if "draft_not_used" in i["takeoff"]["inputs"]), key=_order
    )
    assert not_used and all(md.DRAFT not in i["takeoff"]["inputs"] for i in not_used)
    reference = _stable(
        {
            "drafted": {"items": items, "blockers": drafted["blockers"]},
            "not_drafted": {"items": not_used},
        }
    )
    # The shapes the app reads: at least one held row, one under the threshold or unpriced, an
    # exterior draft with a measurement and an interior one without, and the blocker.
    drafts = [i["takeoff"]["inputs"][md.DRAFT] for i in items if md.DRAFT in i["takeoff"]["inputs"]]
    assert any(d["blocks"] for d in drafts) and any(not d["blocks"] for d in drafts)
    assert any("measurement" in i["takeoff"]["inputs"] for i in items)
    assert any(
        md.DRAFT in i["takeoff"]["inputs"] and "measurement" not in i["takeoff"]["inputs"]
        for i in items
    )
    assert [b["code"] for b in drafted["blockers"]].count(md.BLOCKER) == 1

    expected = json.dumps(reference, indent=1, sort_keys=True) + "\n"
    if os.environ.get("BASIS_WRITE_FIXTURES") == "1":
        FIXTURE.write_text(expected, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == expected, (
        "the API's drafted rows changed: rewrite with BASIS_WRITE_FIXTURES=1 and run "
        "node --test web/tests/measure-drafts-display.test.cjs"
    )
