"""``process_assets`` with renovations (EVAL_REPORT 102, ``basis.activities.line_annotations``):
a row's age and its removal are written on its takeoff and carried into every later revision
(a component row by its lineage, a concealed row by its label) until cleared, without making
the asset a manual classification; a renovations document's after photographs put their
components in the project, record the version, and leave the structure model's carve; a study
without renovations writes no new key. Synthetic rows, scripted classifier."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fixtures import fixture_path
from lifecycletest_support import SeededStudy, make_deps
from sqlalchemy import select, update

from basis.activities import Deps
from basis.activities.apply_corrections import ApplyCorrections
from basis.activities.process_assets import ProcessAssets
from basis.db.enums import ClassificationMethod
from basis.db.models import Asset, Component, CostEstimate, Study, Takeoff
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.domain.renovations import StudyRenovations
from basis.domain.structure import mapping_for
from basis.workflow.contracts import ApplyCorrectionsInput, CorrectionInput

from .conftest import Env
from .test_process_assets import (  # noqa: F401 - the fixture is used by name
    STRUCTURE_FIELDS,
    analyzed,
    labelled,
    process,
    seed_extraction,
)

pytestmark = pytest.mark.integration

AGE = {
    "effective_age": 2,
    "economic_life": 20,
    "age_source": "engineer_inspection",
    "age_source_detail": "new cabinets on the 2023 receipt",
}
NEW_KEYS = ("component_age", "removal", "renovation_project", "renovations_version")


async def next_revision(activity_env: Env, deps: Deps, seeded: SeededStudy, to: int) -> None:
    """An assets-review rejection from ``to - 1`` to ``to``. Each call is its own activity
    (its own ``activity_id``): ``apply_corrections`` runs once per key, so a second call with
    the harness's default id would return the first call's stored output and copy nothing."""
    activity_env.with_info(activity_id=f"apply-corrections-{to}")
    await activity_env.run(
        ApplyCorrections(deps).run,
        ApplyCorrectionsInput(
            study_id=str(seeded.study_id),
            stage="assets_review",
            from_revision=to - 1,
            to_revision=to,
            corrections=(),
        ),
    )


async def takeoffs(deps: Deps, sid: uuid.UUID, revision: int) -> list[tuple[Asset, Takeoff]]:
    async with session_scope(deps.sessionmaker) as session:
        return [
            (a, t)
            for a, t in (
                await session.execute(
                    select(Asset, Takeoff)
                    .join(Takeoff, Takeoff.asset_id == Asset.id)
                    .where(Asset.study_id == sid, Asset.revision == revision)
                )
            ).all()
        ]


async def by_label(deps: Deps, sid: uuid.UUID, revision: int) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    async with session_scope(deps.sessionmaker) as session:
        rows = (
            await session.execute(
                select(Component.label, Takeoff.inputs)
                .join(Asset, Asset.component_id == Component.id)
                .join(Takeoff, Takeoff.asset_id == Asset.id)
                .where(Asset.study_id == sid, Asset.revision == revision)
            )
        ).all()
    for label, inputs in rows:
        out.setdefault(label, inputs)
    return out


async def test_without_renovations_no_new_key_is_written(
    deps: Deps,
    analyzed: SeededStudy,  # noqa: F811 - the fixture imported above
    activity_env: Env,
) -> None:
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    for _, takeoff in await takeoffs(deps, analyzed.study_id, 1):
        assert not set(NEW_KEYS) & set(takeoff.inputs)


async def test_an_age_is_carried_until_cleared_and_is_not_a_classification(
    deps: Deps,
    analyzed: SeededStudy,  # noqa: F811 - the fixture imported above
    activity_env: Env,
) -> None:
    sid = analyzed.study_id
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    target = next(a for label, a in await labelled(deps, sid, 1) if label == "cabinetry")
    await next_revision(activity_env, deps, analyzed, 2)
    aged = CorrectionInput(
        entity_type="asset", entity_id=str(target.id), after=AGE, reason="receipt"
    )
    await activity_env.run(ProcessAssets(deps).run, process(analyzed, 2, corrections=(aged,)))
    rows = {a.component_id: (a, t) for a, t in await takeoffs(deps, sid, 2)}
    async with session_scope(deps.sessionmaker) as session:
        component = (
            await session.scalars(
                select(Component).where(
                    Component.study_id == sid,
                    Component.revision == 2,
                    Component.raw["carried_from"].astext == str(target.component_id),
                )
            )
        ).one()
    asset, takeoff = rows[component.id]
    assert takeoff.inputs["component_age"]["economic_life"] == "20"
    assert takeoff.inputs["component_age"]["from_asset_id"] == str(target.id)
    assert asset.method is not ClassificationMethod.MANUAL  # an age is not a classification
    # Only the corrected row carries it.
    assert sum("component_age" in t.inputs for _, t in rows.values()) == 1
    # An ordinary rejection later keeps it (the lineage of the carried components).
    await next_revision(activity_env, deps, analyzed, 3)
    await activity_env.run(ProcessAssets(deps).run, process(analyzed, 3))
    carried = [t for _, t in await takeoffs(deps, sid, 3) if "component_age" in t.inputs]
    assert len(carried) == 1 and carried[0].inputs["component_age"]["effective_age"] == "2"
    old = next(a for a, t in await takeoffs(deps, sid, 3) if "component_age" in t.inputs)
    await next_revision(activity_env, deps, analyzed, 4)
    clear = CorrectionInput(
        entity_type="asset",
        entity_id=str(old.id),
        after={"effective_age": None},
        reason="replaced after all",
    )
    await activity_env.run(ProcessAssets(deps).run, process(analyzed, 4, corrections=(clear,)))
    assert not [t for _, t in await takeoffs(deps, sid, 4) if "component_age" in t.inputs]


async def test_a_bad_age_is_refused_before_anything_is_written(
    deps: Deps,
    analyzed: SeededStudy,  # noqa: F811 - the fixture imported above
    activity_env: Env,
) -> None:
    sid = analyzed.study_id
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    target = next(a for _, a in await labelled(deps, sid, 1))
    await next_revision(activity_env, deps, analyzed, 2)
    bad = CorrectionInput(
        entity_type="asset",
        entity_id=str(target.id),
        after={**AGE, "effective_age": 30},
        reason="r",
    )
    with pytest.raises(Exception, match="BASIS-CORRECT-047"):
        await activity_env.run(ProcessAssets(deps).run, process(analyzed, 2, corrections=(bad,)))
    assert await takeoffs(deps, sid, 2) == []


async def test_a_concealed_rows_age_and_removal_are_carried_by_label(
    deps: Deps,
    analyzed: SeededStudy,  # noqa: F811 - the fixture imported above
    activity_env: Env,
) -> None:
    sid = analyzed.study_id
    await seed_extraction(deps, analyzed, STRUCTURE_FIELDS)
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    roof = next(a for a, t in await takeoffs(deps, sid, 1) if t.inputs.get("label") == "painting")
    await next_revision(activity_env, deps, analyzed, 2)
    removal = {"removed_by_project": "paint-2025", "disposition_date": "2025-03-03"}
    corrections = (
        CorrectionInput(entity_type="asset", entity_id=str(roof.id), after=AGE, reason="r"),
        CorrectionInput(entity_type="asset", entity_id=str(roof.id), after=removal, reason="r"),
    )
    await activity_env.run(ProcessAssets(deps).run, process(analyzed, 2, corrections=corrections))
    concealed = [t for a, t in await takeoffs(deps, sid, 2) if a.component_id is None]
    marked = [t for t in concealed if "component_age" in t.inputs]
    assert len(marked) == 1 and marked[0].inputs["removal"]["project"] == "paint-2025"
    await next_revision(activity_env, deps, analyzed, 3)
    await activity_env.run(ProcessAssets(deps).run, process(analyzed, 3))
    concealed = [t for a, t in await takeoffs(deps, sid, 3) if a.component_id is None]
    assert [t.inputs["label"] for t in concealed if "removal" in t.inputs] == [
        marked[0].inputs["label"]
    ]


async def test_after_photos_move_their_components_to_the_project(
    deps: Deps,
    analyzed: SeededStudy,  # noqa: F811 - the fixture imported above
    activity_env: Env,
) -> None:
    """A kitchen photograph tagged after a project: its components are the project's, every
    row records the version, and the structure model no longer credits them against the
    building: the Specialties residual grows by exactly the priced cabinet the photograph
    holds (the stub's own labels are unpriced, so a priced ``kitchen_cabinet`` is added to
    the photograph)."""
    sid = analyzed.study_id
    await seed_extraction(deps, analyzed, STRUCTURE_FIELDS)
    async with session_scope(deps.sessionmaker) as session:
        photo, room = (
            await session.execute(
                select(Component.document_id, Component.room_id).where(
                    Component.study_id == sid,
                    Component.revision == 1,
                    Component.label == "cabinetry",
                )
            )
        ).first()  # type: ignore[misc]
        await RoomRepository(session).add_component(
            study_id=sid,
            revision=1,
            document_id=photo,
            room_id=room,
            label="kitchen_cabinet",
            material=None,
            attachment=None,
            confidence=0.9,
            raw={"source": "vision", "count": 1},
        )
    priced = make_deps(
        deps.sessionmaker,
        deps.storage,
        deps.settings.model_copy(update={"cost_table_path": str(fixture_path("cost_table.json"))}),
    )
    await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    async with session_scope(deps.sessionmaker) as session:
        doc = StudyRenovations.model_validate(
            {
                "projects": [
                    {
                        "key": "kitchen-2025",
                        "kind": "after_service",
                        "scope": "Kitchen remodel",
                        "work_start_date": "2025-03-03",
                        "placed_in_service_date": "2025-05-30",
                        "invoice_total": "1000.00",
                        "after_photos": [str(photo)],
                    }
                ]
            }
        )
        await session.execute(
            update(Study).where(Study.id == sid).values(renovations=doc, renovations_version=3)
        )
    await next_revision(activity_env, deps, analyzed, 2)
    await activity_env.run(ProcessAssets(priced).run, process(analyzed, 2))
    after = await takeoffs(deps, sid, 2)
    assert all(t.inputs["renovations_version"] == 3 for _, t in after)
    async with session_scope(deps.sessionmaker) as session:
        photo_of = {
            c.id: c.document_id
            for c in (
                await session.scalars(
                    select(Component).where(Component.study_id == sid, Component.revision == 2)
                )
            ).all()
        }
        costs = {c.asset_id: c.total for c in (await session.scalars(select(CostEstimate))).all()}
    in_project = [(a, t) for a, t in after if t.inputs.get("renovation_project") == "kitchen-2025"]
    assert in_project and all(photo_of[a.component_id] == photo for a, _ in in_project)
    assert not [
        t
        for a, t in after
        if a.component_id is not None
        and photo_of[a.component_id] != photo
        and "renovation_project" in t.inputs
    ]
    # The photograph's rows the Specialties component credited: the stub's ``cabinetry``
    # (an alias of ``kitchen_cabinet``) and the added cabinet, each 538.50 on the fixture table.
    credited = [
        costs[a.id]
        for a, t in in_project
        if mapping_for(str(t.inputs.get("component"))).component == "specialties"
    ]
    assert len(credited) == 2 and all(c == Decimal("538.50") for c in credited)

    def residual(rows: list[tuple[Asset, Takeoff]]) -> Decimal:
        found = next(a for a, t in rows if t.inputs.get("label") == "specialties_residual")
        total = costs[found.id]
        assert total is not None
        return total

    before = await takeoffs(deps, sid, 1)
    assert residual(after) - residual(before) == sum(credited, Decimal(0))
