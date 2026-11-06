"""The engineer's unit price through the activities (EVAL_REPORT 90, docs/api/OVERRIDES.md).

``apply_corrections`` stores a ``component`` correction's price (the components review) and
an ``asset`` correction's (the assets review) on the copied component row; ``process_assets``
prices every later revision with it: the engineer's price x the quantity, the cost table's
own pricing kept in the trail (``replaced``). The study is the Stage 2 stub's with the
synthetic cost table (kitchen cabinets 480.00 per EA Total Incl O&P; a patio priced per SF,
which the count does not measure; a driveway with no row) and no classifier provider (the
residual components are flagged ``classifier_unavailable``; pricing does not depend on it).

Paths: a component's price on a priced, a per-other-unit and an unpriced row; the asset's
price wins over its component's and stays a model classification; both carried through
later rejections, a reprice and a reopen; cleared back to the table; a missing quantity is
never priced by a price; the refusals (a bad value, a concealed row); the regression check
that every row without a price is priced exactly as before.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fixtures import fixture_path
from lifecycletest_support import SeededStudy, make_deps
from sqlalchemy import select, update
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.apply_corrections import ApplyCorrections
from basis.activities.carry_revision import CarryRevision
from basis.activities.process_assets import ProcessAssets
from basis.db.enums import Bucket, ClassificationMethod
from basis.db.models import (
    Asset,
    Component,
    Correction,
    CostEstimate,
    StudyEvent,
    Takeoff,
    User,
)
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.domain import price_overrides as po
from basis.domain.cost_trail import check
from basis.workflow.contracts import (
    ApplyCorrectionsInput,
    CarryRevisionInput,
    CorrectionInput,
    ReviewStage,
)

from .conftest import Env
from .test_process_assets import (
    MEASURED_FIELDS,
    analyzed,
    assert_trail_matches,
    process,
    reprice,
    seed_extraction,
)

__all__ = ["analyzed"]  # the fixture, imported for this module

pytestmark = pytest.mark.integration

D = Decimal
QUOTE = {"unit_cost": 512.4, "unit_cost_source": "Acme Cabinets quote 2024-118"}
INVOICE = {"unit_cost": 600, "unit_cost_source": "Invoice 77, installed"}


def table_deps(deps: Deps) -> Deps:
    settings = deps.settings.model_copy(
        update={"cost_table_path": str(fixture_path("cost_table.json")), "cost_year": 2025}
    )
    return make_deps(deps.sessionmaker, deps.storage, settings, chat_provider=None)


async def add_priced_components(deps: Deps, seeded: SeededStudy) -> None:
    """Kitchen cabinets (a table row per EA), a patio (a row per SF: the count does not
    price it), a driveway (no row), as the cost table test adds them."""
    async with session_scope(deps.sessionmaker) as session:
        repo = RoomRepository(session)
        for label, count in (("kitchen_cabinet", 18), ("patio", 1), ("driveway", 1)):
            await repo.add_component(
                study_id=seeded.study_id,
                revision=1,
                document_id=None,
                room_id=None,
                label=label,
                material=None,
                attachment=None,
                confidence=0.9,
                raw={"source": "vision", "count": count},
            )
        await session.execute(
            update(User).where(User.id == seeded.owner_id).values(display_name="Erin Engineer")
        )


async def rows(deps: Deps, sid: uuid.UUID, revision: int) -> dict[str, tuple[Asset, Any, Any]]:
    """``{label: (asset, takeoff, cost)}`` of the revision's itemized assets (labels are
    unique among the rows these tests read)."""
    async with session_scope(deps.sessionmaker) as session:
        found = (
            await session.execute(
                select(Component.label, Asset, Takeoff, CostEstimate)
                .join(Component, Component.id == Asset.component_id)
                .join(Takeoff, Takeoff.asset_id == Asset.id)
                .join(CostEstimate, CostEstimate.asset_id == Asset.id)
                .where(Asset.study_id == sid, Asset.revision == revision)
            )
        ).all()
    out: dict[str, tuple[Asset, Any, Any]] = {}
    for label, asset, takeoff, cost in found:
        out.setdefault(label, (asset, takeoff, cost))
    return out


async def all_rows(deps: Deps, sid: uuid.UUID, revision: int) -> list[tuple[str, Asset, Any, Any]]:
    """Every ``(label, asset, takeoff, cost)`` of the revision's itemized assets."""
    async with session_scope(deps.sessionmaker) as session:
        found = (
            await session.execute(
                select(Component.label, Asset, Takeoff, CostEstimate)
                .join(Component, Component.id == Asset.component_id)
                .join(Takeoff, Takeoff.asset_id == Asset.id)
                .join(CostEstimate, CostEstimate.asset_id == Asset.id)
                .where(Asset.study_id == sid, Asset.revision == revision)
            )
        ).all()
    return [(label, asset, takeoff, cost) for label, asset, takeoff, cost in found]


async def component_of(deps: Deps, asset: Asset) -> Component:
    async with session_scope(deps.sessionmaker) as session:
        found = await session.get(Component, asset.component_id)
    assert found is not None
    return found


async def reject(
    deps: Deps,
    seeded: SeededStudy,
    env: Env,
    stage: ReviewStage,
    from_revision: int,
    *corrections: tuple[str, uuid.UUID, dict[str, Any], str | None],
) -> None:
    """A rejection as the API and the workflow make it: the correction rows (the API's),
    then ``apply_corrections`` and ``process_assets`` for the next revision."""
    inputs = []
    async with session_scope(deps.sessionmaker) as session:
        for entity_type, entity_id, after, reason in corrections:
            session.add(
                Correction(
                    study_id=seeded.study_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    revision=from_revision,
                    after=after,
                    reason=reason,
                    reviewer_id=seeded.owner_id,
                )
            )
            inputs.append(
                CorrectionInput(
                    entity_type=entity_type, entity_id=str(entity_id), after=after, reason=reason
                )
            )
    env.with_info(activity_id=f"apply-{from_revision}")
    await env.run(
        ApplyCorrections(deps).run,
        ApplyCorrectionsInput(
            study_id=str(seeded.study_id),
            stage=stage,
            from_revision=from_revision,
            to_revision=from_revision + 1,
            corrections=tuple(inputs),
        ),
    )
    assets_corrections = tuple(c for c in inputs if stage == "assets_review")
    await env.run(
        ProcessAssets(deps).run,
        process(seeded, revision=from_revision + 1, corrections=assets_corrections),
    )


def comparable(cost: CostEstimate) -> tuple[Any, ...]:
    return (cost.unit_cost, cost.total, cost.source_row, cost.location_factor, cost.cost_year)


# --------------------------------------------------------------------------- component price


async def test_a_components_price_prices_its_asset_and_keeps_the_tables(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    priced = table_deps(deps)
    sid = analyzed.study_id
    await add_priced_components(deps, analyzed)
    await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    before = await rows(deps, sid, 1)
    cabinet, patio, driveway = (before[k][0] for k in ("kitchen_cabinet", "patio", "driveway"))
    assert before["kitchen_cabinet"][2].total == D("8640.00")  # 480.00 x 18, the table's
    assert before["patio"][2].total is None and before["driveway"][2].total is None
    await reject(
        priced,
        analyzed,
        activity_env,
        "components_review",
        1,
        ("component", cabinet.component_id, QUOTE, "Contractor's quote on file"),
        ("component", patio.component_id, {"unit_cost": 950, "unit_cost_source": "Bid B-4"}, "Bid"),
        ("component", driveway.component_id, {"unit_cost": 12.5, "unit_cost_source": "Guide"}, "x"),
    )
    after = await rows(deps, sid, 2)
    asset, takeoff, cost = after["kitchen_cabinet"]
    assert (cost.unit_cost, cost.total) == (D("512.40"), D("9223.20"))
    trail = assert_trail_matches(cost, takeoff)
    assert trail["kind"] == "engineer_price" and check(trail) == []
    override = trail["override"]
    assert (override["unit_cost"], override["source"], override["level"]) == (
        "512.4",
        "Acme Cabinets quote 2024-118",
        "component",
    )
    assert (override["by"], override["by_name"]) == (str(analyzed.owner_id), "Erin Engineer")
    assert override["reason"] == "Contractor's quote on file" and override["revision"] == 2
    # The table's price is kept: 480.00 x 18.
    assert (trail["replaced"]["kind"], trail["replaced"]["total"]) == ("unit_price", "8640.00")
    assert cost.source_row.startswith("engineer's unit price 512.4 per EA")
    assert cost.source_row.endswith(before["kitchen_cabinet"][2].source_row)
    # A price is not a classification: the asset is classified as before, by the rules.
    assert asset.method is before["kitchen_cabinet"][0].method
    assert "cost:" not in (asset.review_reason or "")
    # The patio's row prices per SF (the count could not price it) and the driveway has no
    # row: the engineer's price per takeoff unit prices both, and their table reasons go.
    for label, total in (("patio", D("950.00")), ("driveway", D("12.50"))):
        asset, takeoff, cost = after[label]
        trail = assert_trail_matches(cost, takeoff)
        assert cost.total == total and trail["replaced"]["kind"] == "unpriced"
        assert "COST_NEEDS_QUANTITY" not in (asset.review_reason or "")
        assert "COST_ROW_MISSING" not in (asset.review_reason or "")
    # The component's identification is untouched: same label, count and confidence.
    component = await component_of(deps, after["kitchen_cabinet"][0])
    assert (component.label, component.raw["count"]) == ("kitchen_cabinet", 18)
    assert component.confidence == pytest.approx(0.9)  # not 1.0: no identification changed
    assert "corrected" not in component.raw
    # Regression: every row without a price is priced exactly as before.
    for label, (_, _, old) in before.items():
        if label not in ("kitchen_cabinet", "patio", "driveway"):
            assert comparable(after[label][2]) == comparable(old), label
            assert after[label][2].trail == old.trail, label


async def test_the_correction_is_on_the_timeline_with_its_level(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    priced = table_deps(deps)
    await add_priced_components(deps, analyzed)
    await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    cabinet = (await rows(deps, analyzed.study_id, 1))["kitchen_cabinet"][0]
    await reject(
        priced,
        analyzed,
        activity_env,
        "components_review",
        1,
        ("component", cabinet.component_id, QUOTE, "quote"),
    )
    async with session_scope(deps.sessionmaker) as session:
        events = (
            await session.scalars(
                select(StudyEvent).where(
                    StudyEvent.study_id == analyzed.study_id,
                    StudyEvent.event_type == "entity.corrected",
                )
            )
        ).all()
    [event] = events  # a price alone: one event, no identification change
    assert event.actor_id == analyzed.owner_id and event.revision == 2
    assert event.before == {po.COMPONENT_KEY: None}
    assert event.after[po.COMPONENT_KEY]["unit_cost"] == "512.4"
    assert event.detail["level"] == "component" and event.detail["reasons"] == ["quote"]


# --------------------------------------------------------------------------- asset price


async def test_the_assets_price_wins_carries_and_clears_back_to_the_component_then_table(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    priced = table_deps(deps)
    sid = analyzed.study_id
    await add_priced_components(deps, analyzed)
    await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    first = (await rows(deps, sid, 1))["kitchen_cabinet"][0]
    await reject(
        priced, analyzed, activity_env, "components_review", 1,
        ("component", first.component_id, QUOTE, "quote"),
    )  # fmt: skip
    # Revision 2 -> 3: the asset's own price (an assets rejection) wins over the component's.
    two = (await rows(deps, sid, 2))["kitchen_cabinet"][0]
    await reject(
        priced, analyzed, activity_env, "assets_review", 2,
        ("asset", two.id, INVOICE, "Installed price from the invoice"),
    )  # fmt: skip
    asset, _, cost = (await rows(deps, sid, 3))["kitchen_cabinet"]
    assert (cost.unit_cost, cost.total) == (D("600.00"), D("10800.00"))
    assert cost.trail["override"]["level"] == "asset"
    assert cost.trail["replaced"]["total"] == "8640.00"
    # A price-only asset correction is not a manual classification.
    assert asset.method is not ClassificationMethod.MANUAL
    raw = (await component_of(deps, asset)).raw
    assert raw[po.ASSET_KEY]["unit_cost"] == "600" and raw[po.COMPONENT_KEY]["unit_cost"] == "512.4"
    # Revision 3 -> 4: an unrelated assets rejection carries the asset's price.
    patio = (await rows(deps, sid, 3))["patio"][0]
    await reject(
        priced, analyzed, activity_env, "assets_review", 3,
        ("asset", patio.id, {"bucket": "y15", "recovery_period": "15"}, "land improvement"),
    )  # fmt: skip
    four = await rows(deps, sid, 4)
    assert four["kitchen_cabinet"][2].total == D("10800.00")
    assert four["patio"][0].bucket is Bucket.Y15
    assert four["patio"][0].method is ClassificationMethod.MANUAL
    # Revision 4 -> 5: clearing the asset's price falls back to the component's.
    await reject(
        priced, analyzed, activity_env, "assets_review", 4,
        ("asset", four["kitchen_cabinet"][0].id, {"unit_cost": None}, "use the quote"),
    )  # fmt: skip
    five = (await rows(deps, sid, 5))["kitchen_cabinet"]
    assert five[2].total == D("9223.20") and five[2].trail["override"]["level"] == "component"
    assert po.ASSET_KEY not in (await component_of(deps, five[0])).raw
    # Revision 5 -> 6: clearing the component's price is the table's price again, exactly.
    await reject(
        priced, analyzed, activity_env, "components_review", 5,
        ("component", five[0].component_id, {"unit_cost": None}, "table price is right"),
    )  # fmt: skip
    six = (await rows(deps, sid, 6))["kitchen_cabinet"][2]
    original = (await rows(deps, sid, 1))["kitchen_cabinet"][2]
    assert six.trail["kind"] == "unit_price" and "override" not in six.trail
    assert comparable(six) == comparable(original)


async def test_a_reprice_and_a_reopen_carry_the_price(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    priced = table_deps(deps)
    sid = analyzed.study_id
    await add_priced_components(deps, analyzed)
    await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    cabinet = (await rows(deps, sid, 1))["kitchen_cabinet"][0]
    await reject(
        priced, analyzed, activity_env, "components_review", 1,
        ("component", cabinet.component_id, QUOTE, "quote"),
    )  # fmt: skip
    # A reprice (REPRICE.md): the classifications are carried, the price still applies.
    activity_env.with_info(activity_id="apply-reprice")
    await activity_env.run(
        ApplyCorrections(priced).run,
        ApplyCorrectionsInput(
            study_id=str(sid),
            stage="assets_review",
            from_revision=2,
            to_revision=3,
            corrections=(reprice(sid),),
        ),
    )
    await activity_env.run(
        ProcessAssets(priced).run, process(analyzed, revision=3, corrections=(reprice(sid),))
    )
    assert (await rows(deps, sid, 3))["kitchen_cabinet"][2].total == D("9223.20")
    # A reopen at the components review (CarryRevision): the component keeps its price, and
    # the assets rebuilt on it are priced with it.
    await activity_env.run(
        CarryRevision(priced).run,
        CarryRevisionInput(
            study_id=str(sid),
            from_revision=3,
            to_revision=4,
            through="components_review",
            reason="reopen",
            idempotency_key="carry-4",
        ),
    )
    await activity_env.run(ProcessAssets(priced).run, process(analyzed, revision=4))
    asset, _, cost = (await rows(deps, sid, 4))["kitchen_cabinet"]
    assert cost.total == D("9223.20") and cost.trail["override"]["revision"] == 2
    assert (await component_of(deps, asset)).raw[po.COMPONENT_KEY]["source"] == QUOTE[
        "unit_cost_source"
    ]


async def test_the_index_flags_are_the_tables_not_the_engineers(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """EVAL_REPORT 89: a ZIP in another state flags every row the index priced
    (``cost:LOCATION_STATE_MISMATCH``). The engineer's price applies no location factor,
    so its row carries no such flag while the table's rows keep theirs."""
    priced = table_deps(deps)
    sid = analyzed.study_id
    await seed_extraction(
        deps, analyzed, {**MEASURED_FIELDS, "subject.state": "NY", "subject.zip": "60601"}
    )
    await add_priced_components(deps, analyzed)
    await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    before = await rows(deps, sid, 1)
    assert "cost:LOCATION_STATE_MISMATCH" in (before["kitchen_cabinet"][0].review_reason or "")
    await reject(
        priced, analyzed, activity_env, "components_review", 1,
        ("component", before["kitchen_cabinet"][0].component_id, QUOTE, "quote"),
    )  # fmt: skip
    after = await rows(deps, sid, 2)
    assert after["kitchen_cabinet"][2].trail["kind"] == "engineer_price"
    assert "cost:" not in (after["kitchen_cabinet"][0].review_reason or "")
    flagged = [
        a
        for label, (a, _, c) in after.items()
        if label != "kitchen_cabinet" and c.total is not None
    ]
    assert flagged and all(
        "cost:LOCATION_STATE_MISMATCH" in (a.review_reason or "") for a in flagged
    )


# --------------------------------------------------------------------------- not priced


async def test_a_price_does_not_price_a_missing_quantity(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """Fail-safe money (EVAL_REPORT 78): a row whose quantity is a missing input (here a
    floor with no GLA) stays unpriced with its blocker; the price waits on the trail."""
    priced = table_deps(deps)
    sid = analyzed.study_id
    await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    asset = next(
        a
        for _, a, t, _ in await all_rows(deps, sid, 1)
        if isinstance(t.inputs, dict) and t.inputs.get("missing_input")
    )
    assert asset.component_id is not None
    await reject(
        priced, analyzed, activity_env, "components_review", 1,
        ("component", asset.component_id, {"unit_cost": 4.25, "unit_cost_source": "Bid"}, "bid"),
    )  # fmt: skip
    # The row is found by the price it carries, not by its label: a label (a floor in two
    # rooms) can name several rows, and which one a label lookup returns follows random ids.
    carrying = [
        (a, c) for _, a, _, c in await all_rows(deps, sid, 2) if (c.trail or {}).get("override")
    ]
    assert len(carrying) == 1, carrying
    asset, cost = carrying[0]
    assert cost.unit_cost is None and cost.total is None
    assert cost.trail["kind"] == "unpriced" and cost.trail["override"]["unit_cost"] == "4.25"
    assert "input:gla" in (asset.review_reason or "").split("; ")


# --------------------------------------------------------------------------- refused


@pytest.mark.parametrize(
    ("after", "reason"),
    [
        ({"unit_cost": 0, "unit_cost_source": "x"}, "r"),
        ({"unit_cost": 12.345, "unit_cost_source": "x"}, "r"),
        ({"unit_cost": 12, "unit_cost_source": " "}, "r"),
        ({"unit_cost": 12, "unit_cost_source": "x"}, None),
    ],
)
async def test_a_bad_price_refuses_the_rejection_before_anything_is_written(
    deps: Deps, analyzed: SeededStudy, activity_env: Env, after: dict[str, Any], reason: str | None
) -> None:
    priced = table_deps(deps)
    await add_priced_components(deps, analyzed)
    await activity_env.run(ProcessAssets(priced).run, process(analyzed))
    cabinet = (await rows(deps, analyzed.study_id, 1))["kitchen_cabinet"][0]
    for stage, entity, target in (
        ("components_review", "component", cabinet.component_id),
        ("assets_review", "asset", cabinet.id),
    ):
        with pytest.raises(ApplicationError) as raised:
            await activity_env.run(
                ApplyCorrections(priced).run,
                ApplyCorrectionsInput(
                    study_id=str(analyzed.study_id),
                    stage=stage,  # type: ignore[arg-type]
                    from_revision=1,
                    to_revision=2,
                    corrections=(
                        CorrectionInput(
                            entity_type=entity, entity_id=str(target), after=after, reason=reason
                        ),
                    ),
                ),
            )
        assert raised.value.non_retryable and "BASIS-CORRECT-044" in str(raised.value)
    async with session_scope(deps.sessionmaker) as session:
        written = await session.scalar(select(Component.id).where(Component.revision == 2))
    assert written is None


async def test_a_concealed_row_has_no_unit_price_to_override(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    async with session_scope(deps.sessionmaker) as session:
        concealed = Asset(
            study_id=analyzed.study_id,
            revision=1,
            component_id=None,
            category="Foundations",
            bucket=Bucket.Y27_5,
            recovery_period=D("27.5"),
            method=ClassificationMethod.RULE,
        )
        session.add(concealed)
        await session.flush()
        concealed_id = concealed.id
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(
            ApplyCorrections(deps).run,
            ApplyCorrectionsInput(
                study_id=str(analyzed.study_id),
                stage="assets_review",
                from_revision=1,
                to_revision=2,
                corrections=(
                    CorrectionInput(
                        entity_type="asset",
                        entity_id=str(concealed_id),
                        after=QUOTE,
                        reason="quote",
                    ),
                ),
            ),
        )
    assert raised.value.non_retryable and "BASIS-CORRECT-045" in str(raised.value)
