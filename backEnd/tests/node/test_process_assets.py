"""``process_assets`` and ``promote_conventions`` end to end on Postgres.

BUILD_PLAN 6.2 (``node/test_classify_component.py``, ``test_compute_takeoffs.py``,
``test_estimate_costs.py``): a scripted model with search results produces an asset
with a verified citation; a fabricated citation is rejected; takeoffs and costs are
deterministic. The firm's conventions (docs/review_2026-10-15/CLASSIFICATION_CONVENTIONS.md
section 6): an approved revision promotes its engineer corrections and cited model answers
(never rule assets), idempotently; the next study at the firm is classified from them with
no model call and carries their provenance, a study at another firm is not; a repeat
approval counts, a different answer replaces the convention with an event.
The hierarchy (section 9): a component whose material the photo step read differently, or
whose room the first study never showed, is served by the ruling at a coarser tier
(``matched_tier`` on the asset and in the export), a generic floor keeps its material, and
the next approval counts on the row that served.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from classifytest_support import (
    ASSET_CLASS_CHUNK,
    CARPET_CHUNK,
    GARAGE_EXCERPT,
    STRUCTURAL_CHUNK,
    answer_turn,
    call,
    classification,
    tools_turn,
)
from dbtest_support import Factory
from lifecycletest_support import SeededStudy, seed_study
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.apply_corrections import ApplyCorrections
from basis.activities.process_assets import ProcessAssets
from basis.activities.promote_conventions import PromoteConventions
from basis.classify.cache import cache_key
from basis.classify.store_protocol import InMemoryChunkStore
from basis.config import Settings
from basis.db.enums import Bucket, ClassificationMethod, Decision, OrgRole, Stage
from basis.db.models import (
    Asset,
    Citation,
    ClassificationCacheEntry,
    Component,
    Correction,
    CostEstimate,
    LlmCall,
    Review,
    Room,
    StudyEvent,
    Takeoff,
)
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.domain.rules import RULES_VERSION
from basis.evidence.export import pack_document
from basis.evidence.pack import load_pack
from basis.llm.cost import cost_usd
from basis.llm.provider import Usage
from basis.storage import LocalStorage
from basis.vision.rooms import ROOM_MANUAL_SOURCE
from basis.workflow.contracts import (
    ApplyCorrectionsInput,
    CorrectionInput,
    ProcessAssetsInput,
    PromoteConventionsInput,
    PromoteConventionsOutput,
)

from .conftest import Env, add_room_with, node_deps

pytestmark = pytest.mark.integration

SEARCH = {"query": "garage door structural component", "k": 3}
FAKE_CHUNK = "00000000-0000-4000-8000-00000000beef"


def scripted_turns() -> list:  # type: ignore[type-arg]
    # Components are processed in label order: carpet (rule), flooring (model),
    # garage_door (model), refrigerator (rule).
    return [
        tools_turn(call("search_reference", {"query": "vinyl flooring"})),
        answer_turn(
            classification(
                category="Flooring",
                section="1245",
                bucket="y5",
                citations=((FAKE_CHUNK, "Floor coverings of every kind are 5-year"),),
            )
        ),
        tools_turn(call("search_reference", SEARCH)),
        answer_turn(),  # cites STRUCTURAL_CHUNK with an excerpt that is in it
    ]


async def seed_components(sm: async_sessionmaker[AsyncSession], study_id: uuid.UUID) -> None:
    await add_room_with(sm, study_id, "Bedroom 2", "bedroom", [("carpet", None)])
    await add_room_with(
        sm, study_id, "Kitchen", "kitchen", [("flooring", None), ("refrigerator", None)]
    )
    await add_room_with(sm, study_id, "Garage", "garage", [("garage_door", None)])


def process(study: SeededStudy, key: str = "k-assets") -> ProcessAssetsInput:
    return ProcessAssetsInput(
        study_id=str(study.study_id), revision=1, idempotency_key=key, budget_remaining_usd=1.0
    )


async def by_label(sm: async_sessionmaker[AsyncSession], study_id: uuid.UUID) -> dict[str, Asset]:
    async with session_scope(sm) as session:
        rows = await session.execute(
            select(Component.label, Asset)
            .join(Asset, Asset.component_id == Component.id)
            .where(Asset.study_id == study_id)
        )
        return {label: asset for label, asset in rows.all()}


async def citations_of(sm: async_sessionmaker[AsyncSession], asset_id: uuid.UUID) -> list[Citation]:
    async with session_scope(sm) as session:
        stmt = select(Citation).where(Citation.asset_id == asset_id).order_by(Citation.chunk_id)
        return list((await session.scalars(stmt)).all())


async def test_rule_model_and_fabricated_paths(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    db_test_settings: Settings,
    reference_store: InMemoryChunkStore,
    study: SeededStudy,
    activity_env: Env,
) -> None:
    deps = node_deps(sessionmaker, storage, db_test_settings, reference_store, scripted_turns())
    await seed_components(sessionmaker, study.study_id)
    output = await activity_env.run(ProcessAssets(deps).run, process(study))
    assert output.assets == 4
    assets = await by_label(sessionmaker, study.study_id)

    carpet = assets["carpet"]
    assert (carpet.method, carpet.bucket, carpet.needs_review) == (
        ClassificationMethod.RULE,
        Bucket.Y5,
        False,
    )
    cited = await citations_of(sessionmaker, carpet.id)
    assert {c.chunk_id for c in cited} == {CARPET_CHUNK, ASSET_CLASS_CHUNK}
    assert all(c.verified_at is not None and c.verification_method for c in cited)

    door = assets["garage_door"]
    assert (door.method, door.bucket, door.irs_section) == (
        ClassificationMethod.LLM,
        Bucket.Y27_5,
        "1250",
    )
    [door_citation] = await citations_of(sessionmaker, door.id)
    assert (door_citation.chunk_id, door_citation.excerpt) == (STRUCTURAL_CHUNK, GARAGE_EXCERPT)
    # The classification stands; only the missing cost row needs the engineer.
    assert door.review_reason == "cross:COST_ROW_MISSING"

    floor = assets["flooring"]
    assert floor.method is ClassificationMethod.LLM
    assert await citations_of(sessionmaker, floor.id) == []  # fabricated chunk id dropped
    assert floor.review_reason and floor.review_reason.startswith("citation_unverified")

    fridge = assets["refrigerator"]
    assert fridge.method is ClassificationMethod.RULE and fridge.needs_review
    assert fridge.review_reason and "citation_unverified" in fridge.review_reason

    async with session_scope(sessionmaker) as session:
        calls = (await session.scalars(select(LlmCall))).all()
        carpet_takeoff = (
            await session.scalars(select(Takeoff).where(Takeoff.asset_id == carpet.id))
        ).one()
        carpet_cost = (
            await session.scalars(select(CostEstimate).where(CostEstimate.asset_id == carpet.id))
        ).one()
    assert len(calls) == 4 and {c.prompt_version for c in calls} == {"classify/v1"}
    assert all(c.run_id is not None and c.activity == "process_assets" for c in calls)
    # Charged from the cost table for the model the provider reports it ran; the scripted
    # provider's name has no price row, so the conservative unknown-model row applies.
    assert output.cost_usd == pytest.approx(
        4 * cost_usd("scripted-model", Usage(input_tokens=100, output_tokens=50))
    )
    # Bedroom: 150 SF typical -> 150 / 9 x 1.10 = 18.33 SY; 61.20/SY x 1.22 = 74.66.
    assert (carpet_takeoff.quantity, carpet_takeoff.unit) == (Decimal("18.33"), "SY")
    assert carpet_takeoff.inputs["room_area_source"] == "typical_for_room_type"
    assert (carpet_cost.unit_cost, carpet_cost.total, carpet_cost.cost_year) == (
        Decimal("74.66"),
        Decimal("1368.52"),
        2026,
    )
    assert output.needs_review == 3


# --------------------------------------------------------------------------- conventions
# docs/review_2026-10-15/CLASSIFICATION_CONVENTIONS.md sections 5 and 6.

RC = "residential_rental"
GARAGE_KEY = cache_key("garage_door", None, None, RC, "garage")
CARPET_KEY = cache_key("carpet", None, None, RC, "bedroom")
FLOORING_KEY = cache_key("flooring", None, None, RC, "kitchen")
CARPET_REASON = "glued down over the slab: the firm treats it as building"
FLOORING_REASON = "sheet vinyl on strippable adhesive"


async def add_kept_room(
    sm: async_sessionmaker[AsyncSession], study_id: uuid.UUID, name: str, room_type: str, label: str
) -> None:
    """A room with one component that a later revision keeps: ``apply_corrections`` carries
    a room with photos or with an engineer-added component (``room_manual``), and these
    studies have no photos."""
    async with session_scope(sm) as session:
        rooms = RoomRepository(session)
        room = await rooms.create(
            study_id=study_id, revision=1, name=name, room_type=room_type, source="test"
        )
        await rooms.add_component(
            study_id=study_id,
            revision=1,
            document_id=None,
            room_id=room.id,
            label=label,
            material=None,
            attachment=None,
            confidence=0.9,
            raw={"source": ROOM_MANUAL_SOURCE},
        )


async def seed_kept_components(sm: async_sessionmaker[AsyncSession], study_id: uuid.UUID) -> None:
    """:func:`seed_components`' rooms and labels, kept across a revision."""
    for name, room_type, label in (
        ("Bedroom 2", "bedroom", "carpet"),
        ("Kitchen", "kitchen", "flooring"),
        ("Kitchen pantry", "kitchen", "refrigerator"),
        ("Garage", "garage", "garage_door"),
    ):
        await add_kept_room(sm, study_id, name, room_type, label)


def garage_turns() -> list:  # type: ignore[type-arg]
    """The model's cited garage-door answer (the components before it are rules)."""
    return [tools_turn(call("search_reference", SEARCH)), answer_turn()]


async def approve_assets(
    sm: async_sessionmaker[AsyncSession], study: SeededStudy, revision: int = 1
) -> None:
    async with session_scope(sm) as session:
        session.add(
            Review(
                study_id=study.study_id,
                stage=Stage.ASSETS_REVIEW,
                revision=revision,
                decision=Decision.APPROVE,
                reviewer_id=study.owner_id,
                idempotency_key=f"approve-{study.study_id}-{revision}",
            )
        )


async def study_at_firm(sm: async_sessionmaker[AsyncSession], firm: SeededStudy) -> SeededStudy:
    """A second study of the same firm (``seed_study`` gives every study a firm of its own)."""
    async with session_scope(sm) as session:
        make = Factory(session)
        owner = await make.user()
        await make.member(firm.org_id, owner, OrgRole.ENGINEER)
        study = await make.study(owner, org_id=firm.org_id)
        return SeededStudy(study.id, firm.org_id, owner.id, None)


def promote_input(study: SeededStudy, revision: int, key: str) -> PromoteConventionsInput:
    return PromoteConventionsInput(
        study_id=str(study.study_id), revision=revision, idempotency_key=key
    )


async def correct(
    sm: async_sessionmaker[AsyncSession],
    deps: Deps,
    env: Env,
    study: SeededStudy,
    fixes: dict[str, tuple[dict[str, str], str]],
    key: str,
) -> None:
    """Reject revision 1 with ``asset`` corrections (label -> (after, reason)) recorded as
    the API records them, carry the revision over and build revision 2's assets."""
    old = await by_label(sm, study.study_id)
    corrections = []
    async with session_scope(sm) as session:
        for label, (after, reason) in fixes.items():
            session.add(
                Correction(
                    study_id=study.study_id,
                    entity_type="asset",
                    entity_id=old[label].id,
                    revision=1,
                    before={"bucket": old[label].bucket.value},
                    after=after,
                    reason=reason,
                    reviewer_id=study.owner_id,
                )
            )
            corrections.append(
                CorrectionInput(entity_type="asset", entity_id=str(old[label].id), after=after)
            )
    await env.run(
        ApplyCorrections(deps).run,
        ApplyCorrectionsInput(
            study_id=str(study.study_id),
            stage="assets_review",
            from_revision=1,
            to_revision=2,
            corrections=tuple(corrections),
        ),
    )
    await env.run(
        ProcessAssets(deps).run,
        ProcessAssetsInput(
            study_id=str(study.study_id),
            revision=2,
            idempotency_key=key,
            budget_remaining_usd=1.0,
            corrections=tuple(corrections),
        ),
    )


async def conventions(
    sm: async_sessionmaker[AsyncSession], org_id: uuid.UUID
) -> dict[str, ClassificationCacheEntry]:
    async with session_scope(sm) as session:
        rows = await session.scalars(
            select(ClassificationCacheEntry).where(ClassificationCacheEntry.org_id == org_id)
        )
        return {row.key: row for row in rows.all()}


async def events(
    sm: async_sessionmaker[AsyncSession], study_id: uuid.UUID, event_type: str
) -> list[StudyEvent]:
    async with session_scope(sm) as session:
        rows = await session.scalars(
            select(StudyEvent)
            .where(StudyEvent.study_id == study_id, StudyEvent.event_type == event_type)
            .order_by(StudyEvent.seq)
        )
        return list(rows.all())


async def by_label_at(
    sm: async_sessionmaker[AsyncSession], study_id: uuid.UUID, revision: int
) -> dict[str, Asset]:
    async with session_scope(sm) as session:
        rows = await session.execute(
            select(Component.label, Asset)
            .join(Asset, Asset.component_id == Component.id)
            .where(Asset.study_id == study_id, Asset.revision == revision)
        )
        return {label: asset for label, asset in rows.all()}


async def seed_firm_conventions(
    sm: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    settings: Settings,
    store: InMemoryChunkStore,
    study: SeededStudy,
    env: Env,
) -> PromoteConventionsOutput:
    """Study A: rules (carpet, refrigerator), a cited model answer (garage door), an
    uncited one (flooring); the engineer corrects carpet (against its rule row) and flooring;
    revision 2 is approved and promoted."""
    deps = node_deps(sm, storage, settings, store, scripted_turns())
    await seed_kept_components(sm, study.study_id)
    await env.run(ProcessAssets(deps).run, process(study))
    fixes = {
        "carpet": (
            {"bucket": "y27_5", "irs_section": "1250", "category": "Glued carpet"},
            CARPET_REASON,
        ),
        "flooring": (
            {"bucket": "y5", "irs_section": "1245", "category": "Vinyl flooring"},
            FLOORING_REASON,
        ),
    }
    rev2 = node_deps(sm, storage, settings, store, garage_turns())
    await correct(sm, rev2, env, study, fixes, key="k-assets-r2")
    promote = PromoteConventions(rev2).run
    with pytest.raises(ApplicationError, match="not approved at assets_review"):
        await env.run(promote, promote_input(study, 2, "k-promote-early"))
    await approve_assets(sm, study, revision=2)
    return await env.run(promote, promote_input(study, 2, "k-promote"))  # type: ignore[no-any-return]


async def test_promotion_seeds_from_manual_and_cited_model_assets_only(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    db_test_settings: Settings,
    reference_store: InMemoryChunkStore,
    study: SeededStudy,
    activity_env: Env,
) -> None:
    sm = sessionmaker
    output = await seed_firm_conventions(
        sm, storage, db_test_settings, reference_store, study, activity_env
    )
    assert output == PromoteConventionsOutput(created=3)
    assets = await by_label_at(sm, study.study_id, 2)
    assert {label: a.method for label, a in assets.items()} == {
        "carpet": ClassificationMethod.MANUAL,
        "flooring": ClassificationMethod.MANUAL,
        "garage_door": ClassificationMethod.LLM,
        "refrigerator": ClassificationMethod.RULE,
    }
    rows = await conventions(sm, study.org_id)
    # Never the rule asset (refrigerator); the engineer's two rulings and the cited model
    # answer, each under its six-part context key.
    assert set(rows) == {CARPET_KEY, FLOORING_KEY, GARAGE_KEY}
    async with session_scope(sm) as session:
        review = (
            await session.scalars(select(Review).where(Review.study_id == study.study_id))
        ).one()
    for row in rows.values():
        assert (row.org_id, row.study_id, row.reviewer_id) == (
            study.org_id,
            study.study_id,
            study.owner_id,
        )
        assert row.approved_at == review.created_at
        assert (row.approvals, row.hits, row.rules_version) == (1, 0, RULES_VERSION)

    carpet = rows[CARPET_KEY]
    assert carpet.source == f"approved_asset:{assets['carpet'].id}"
    assert (carpet.rule_key, carpet.agrees_with_rules) == ("carpet", False)
    assert carpet.result["bucket"] == "y27_5" and carpet.result["rule_bucket"] == "y5"
    assert carpet.result["rationale"] == CARPET_REASON
    assert carpet.result["citations"] == []  # a ruling needs no citation
    flooring = rows[FLOORING_KEY]
    assert (flooring.rule_key, flooring.agrees_with_rules) == (None, None)  # no rule row
    assert flooring.result["rationale"] == FLOORING_REASON
    garage = rows[GARAGE_KEY]
    assert garage.result["citations"] == [
        {"chunk_id": str(STRUCTURAL_CHUNK), "excerpt": GARAGE_EXCERPT}
    ]
    assert garage.result["method"] == "llm" and garage.result["rationale"] == ""

    promoted = await events(sm, study.study_id, "convention.promoted")
    assert [(e.detail["key"], e.detail["outcome"]) for e in promoted] == sorted(
        (key, "created") for key in rows
    )
    by_key = {e.detail["key"]: e for e in promoted}
    assert by_key[CARPET_KEY].detail == {
        "key": CARPET_KEY,
        "bucket": "y27_5",
        "agrees_with_rules": False,
        "rule_key": "carpet",
        "outcome": "created",
        "approvals": 1,
        "method": "manual",
        "cited": False,
    }
    assert {(e.actor_type, e.actor_id, e.entity_id) for e in promoted} == {
        ("engineer", study.owner_id, assets[label].id)
        for label in ("carpet", "flooring", "garage_door")
    }

    # Idempotent: the same activity key returns its stored output; a re-run under a new
    # key (a workflow reset) skips every key this study already promoted.
    promote = PromoteConventions(node_deps(sm, storage, db_test_settings, reference_store, []))
    again = await activity_env.run(promote.run, promote_input(study, 2, "k-promote"))
    assert again == output
    reset = await activity_env.run(promote.run, promote_input(study, 2, "k-promote-reset"))
    assert reset == PromoteConventionsOutput(skipped=3)
    after = await conventions(sm, study.org_id)
    assert {k: r.approvals for k, r in after.items()} == {k: 1 for k in rows}
    assert len(await events(sm, study.study_id, "convention.promoted")) == 3


async def test_the_next_study_at_the_firm_is_classified_from_the_conventions(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    db_test_settings: Settings,
    reference_store: InMemoryChunkStore,
    study: SeededStudy,
    activity_env: Env,
) -> None:
    sm = sessionmaker
    await seed_firm_conventions(sm, storage, db_test_settings, reference_store, study, activity_env)
    second = await study_at_firm(sm, study)
    await seed_components(sm, second.study_id)
    # The same carpet in a bathroom has no convention: the rules table answers it.
    await add_room_with(sm, second.study_id, "Bath", "bathroom", [("carpet", None)])
    no_model = node_deps(sm, storage, db_test_settings, reference_store, [])
    output = await activity_env.run(ProcessAssets(no_model).run, process(second, key="k-second"))
    assert (output.assets, output.cost_usd) == (5, 0.0)  # no model call at all
    async with session_scope(sm) as session:
        rows = await session.execute(
            select(Component.label, Room.room_type, Asset)
            .join(Asset, Asset.component_id == Component.id)
            .join(Room, Room.id == Component.room_id)
            .where(Asset.study_id == second.study_id)
        )
        found = {(label, room): asset for label, room, asset in rows.all()}

    door = found[("garage_door", "garage")]
    assert (door.method, door.bucket, door.confidence) == (
        ClassificationMethod.CACHE,
        Bucket.Y27_5,
        pytest.approx(0.95),
    )
    [citation] = await citations_of(sm, door.id)
    assert citation.chunk_id == STRUCTURAL_CHUNK and citation.verified_at is not None
    seeded = await conventions(sm, study.org_id)
    assert door.convention == {
        "key": GARAGE_KEY,
        "matched_tier": "exact",
        "study_id": str(study.study_id),
        "reviewer_id": str(study.owner_id),
        "approved_at": seeded[GARAGE_KEY].approved_at.isoformat(),
        "approvals": 1,
        "rule_key": None,
        "agrees_with_rules": None,
        "citation": None,
    }

    # The engineer's ruling beats the 5-year rule row and is not sent back to review for it.
    carpet = found[("carpet", "bedroom")]
    assert (carpet.method, carpet.bucket, carpet.category) == (
        ClassificationMethod.CACHE,
        Bucket.Y27_5,
        "Glued carpet",
    )
    assert carpet.review_reason is None or "rule_contested" not in carpet.review_reason
    assert carpet.review_reason is None or "validation" not in carpet.review_reason
    assert carpet.convention is not None
    assert (carpet.convention["agrees_with_rules"], carpet.convention["citation"]) == (
        False,
        "firm convention, uncited",
    )
    flooring = found[("flooring", "kitchen")]
    assert (flooring.method, flooring.bucket) == (ClassificationMethod.CACHE, Bucket.Y5)
    assert "citation_unverified" not in (flooring.review_reason or "")
    assert await citations_of(sm, flooring.id) == []
    assert found[("refrigerator", "kitchen")].method is ClassificationMethod.RULE
    bath = found[("carpet", "bathroom")]
    assert (bath.method, bath.bucket, bath.convention) == (
        ClassificationMethod.RULE,
        Bucket.Y5,
        None,
    )

    # Each served convention counts its hit; the export carries the provenance.
    assert {k: r.hits for k, r in (await conventions(sm, study.org_id)).items()} == {
        CARPET_KEY: 1,
        FLOORING_KEY: 1,
        GARAGE_KEY: 1,
    }
    async with session_scope(sm) as session:
        pack = await load_pack(session, second.study_id, 1)
    exported = {a["id"]: a for a in pack_document(pack)["assets"]}
    assert exported[str(door.id)]["convention"] == door.convention
    assert exported[str(bath.id)]["convention"] is None

    # Approving the second study adds an approval to each convention it let stand; it
    # creates and replaces nothing, and the rows keep the ruling that seeded them.
    await approve_assets(sm, second)
    promoted = await activity_env.run(
        PromoteConventions(no_model).run, promote_input(second, 1, "k-promote-second")
    )
    assert promoted == PromoteConventionsOutput(confirmed=3)
    after = await conventions(sm, study.org_id)
    assert {k: (r.approvals, r.study_id) for k, r in after.items()} == {
        CARPET_KEY: (2, study.study_id),
        FLOORING_KEY: (2, study.study_id),
        GARAGE_KEY: (2, study.study_id),
    }
    confirmations = await events(sm, second.study_id, "convention.promoted")
    assert {
        (e.detail["method"], e.detail["outcome"], e.detail["approvals"]) for e in confirmations
    } == {("cache", "confirmed", 2)}

    # A study at another firm never sees them: the garage door goes to the model.
    other = await seed_study(sm, photos=())
    assert other.org_id != study.org_id
    await add_room_with(sm, other.study_id, "Garage", "garage", [("garage_door", None)])
    modelled = node_deps(sm, storage, db_test_settings, reference_store, garage_turns())
    result = await activity_env.run(ProcessAssets(modelled).run, process(other, key="k-other"))
    assert result.cost_usd > 0
    [other_door] = (await by_label(sm, other.study_id)).values()
    assert (other_door.method, other_door.convention) == (ClassificationMethod.LLM, None)


async def test_a_repeat_approval_counts_and_a_different_answer_replaces(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    db_test_settings: Settings,
    reference_store: InMemoryChunkStore,
    study: SeededStudy,
    activity_env: Env,
) -> None:
    sm = sessionmaker
    # Two studies of the firm classified before either is approved: the model answers the
    # garage door the same way in both.
    second = await study_at_firm(sm, study)
    for seeded, key in ((study, "k-a"), (second, "k-b")):
        await add_kept_room(sm, seeded.study_id, "Garage", "garage", "garage_door")
        deps = node_deps(sm, storage, db_test_settings, reference_store, garage_turns())
        await activity_env.run(ProcessAssets(deps).run, process(seeded, key=key))
    promote = PromoteConventions(node_deps(sm, storage, db_test_settings, reference_store, []))
    await approve_assets(sm, study)
    first = await activity_env.run(promote.run, promote_input(study, 1, "k-promote-a"))
    await approve_assets(sm, second)
    repeat = await activity_env.run(promote.run, promote_input(second, 1, "k-promote-b"))
    assert (first, repeat) == (
        PromoteConventionsOutput(created=1),
        PromoteConventionsOutput(confirmed=1),
    )
    row = (await conventions(sm, study.org_id))[GARAGE_KEY]
    # One more approval; the row keeps the ruling that seeded it.
    assert (row.approvals, row.study_id) == (2, study.study_id)
    [confirmed] = await events(sm, second.study_id, "convention.promoted")
    assert (confirmed.detail["outcome"], confirmed.detail["approvals"]) == ("confirmed", 2)

    # A third study is classified from it with both approvals in its provenance; its
    # engineer rules differently, and the approval replaces the convention.
    third = await study_at_firm(sm, study)
    await add_kept_room(sm, third.study_id, "Garage", "garage", "garage_door")
    no_model = node_deps(sm, storage, db_test_settings, reference_store, [])
    await activity_env.run(ProcessAssets(no_model).run, process(third, key="k-c"))
    [served] = (await by_label(sm, third.study_id)).values()
    assert served.method is ClassificationMethod.CACHE and served.convention is not None
    assert served.convention["approvals"] == 2
    fix = {"bucket": "y15", "irs_section": "1250", "category": "Detached garage door"}
    await correct(
        sm, no_model, activity_env, third, {"garage_door": (fix, "detached garage")}, "k-c-r2"
    )
    await approve_assets(sm, third, revision=2)
    replaced = await activity_env.run(promote.run, promote_input(third, 2, "k-promote-c"))
    assert replaced == PromoteConventionsOutput(replaced=1)
    row = (await conventions(sm, study.org_id))[GARAGE_KEY]
    assert (row.result["bucket"], row.approvals, row.hits, row.study_id) == (
        "y15",
        1,
        0,
        third.study_id,
    )
    assert row.result["rationale"] == "detached garage" and row.result["citations"] == []
    [event] = await events(sm, third.study_id, "convention.replaced")
    assert event.code == "BASIS-ASSETS-013" and event.detail == {"key": GARAGE_KEY}
    assert event.before["approvals"] == 2 and event.before["study_id"] == str(study.study_id)
    assert event.before["result"]["bucket"] == "y27_5"
    assert event.after["result"]["bucket"] == "y15"
    [promoted] = await events(sm, third.study_id, "convention.promoted")
    assert (promoted.detail["outcome"], promoted.detail["approvals"]) == ("replaced", 1)


async def test_a_coarser_tier_serves_the_next_study_and_counts_on_the_served_row(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    db_test_settings: Settings,
    reference_store: InMemoryChunkStore,
    study: SeededStudy,
    activity_env: Env,
) -> None:
    """CLASSIFICATION_CONVENTIONS.md section 9: the photo step reads the next study's carpet
    with a material (the ruling was keyed without one), its garage door is in a room the
    first study never showed, and its kitchen floor is a generic floor with a material the
    firm never ruled on."""
    sm = sessionmaker
    await seed_firm_conventions(sm, storage, db_test_settings, reference_store, study, activity_env)
    second = await study_at_firm(sm, study)
    await add_room_with(sm, second.study_id, "Bedroom 3", "bedroom", [("carpet", "wool")])
    await add_room_with(sm, second.study_id, "Cellar", "basement", [("garage_door", None)])
    await add_room_with(sm, second.study_id, "Kitchen", "kitchen", [("flooring", "vinyl")])
    no_model = node_deps(sm, storage, db_test_settings, reference_store, [])
    output = await activity_env.run(ProcessAssets(no_model).run, process(second, key="k-tiers"))
    assert (output.assets, output.cost_usd) == (3, 0.0)  # no model call
    async with session_scope(sm) as session:
        rows = await session.execute(
            select(Component.label, Asset)
            .join(Asset, Asset.component_id == Component.id)
            .where(Asset.study_id == second.study_id)
        )
        found = {label: asset for label, asset in rows.all()}

    # The same label and room, another material: the bedroom ruling serves (label_room).
    carpet = found["carpet"]
    assert (carpet.method, carpet.bucket, carpet.category) == (
        ClassificationMethod.CACHE,
        Bucket.Y27_5,
        "Glued carpet",
    )
    assert carpet.convention is not None
    assert (carpet.convention["key"], carpet.convention["matched_tier"]) == (
        CARPET_KEY,
        "label_room",
    )
    assert not carpet.needs_review
    # A room the firm never ruled on, no rule row: the label alone serves.
    door = found["garage_door"]
    assert (door.method, door.bucket) == (ClassificationMethod.CACHE, Bucket.Y27_5)
    assert door.convention is not None
    assert (door.convention["key"], door.convention["matched_tier"]) == (GARAGE_KEY, "label")
    # A generic floor keeps its material at every tier: the firm's ruling was on a kitchen
    # floor with no material, so a vinyl floor takes the vinyl rule row, not the ruling.
    floor = found["flooring"]
    assert (floor.method, floor.bucket, floor.category, floor.convention) == (
        ClassificationMethod.RULE,
        Bucket.Y5,
        "Vinyl flooring",
        None,
    )

    # Hits are counted on the rows that served; the export carries the tier.
    assert {k: r.hits for k, r in (await conventions(sm, study.org_id)).items()} == {
        CARPET_KEY: 1,
        FLOORING_KEY: 0,
        GARAGE_KEY: 1,
    }
    async with session_scope(sm) as session:
        pack = await load_pack(session, second.study_id, 1)
    exported = {a["id"]: a for a in pack_document(pack)["assets"]}
    assert exported[str(carpet.id)]["convention"]["matched_tier"] == "label_room"
    assert exported[str(door.id)]["convention"]["matched_tier"] == "label"
    assert exported[str(floor.id)]["convention"] is None

    # Approving the study adds an approval to the rows that served, not to rows for the
    # assets' own keys (there are none); the events name the served keys.
    await approve_assets(sm, second)
    promoted = await activity_env.run(
        PromoteConventions(no_model).run, promote_input(second, 1, "k-promote-tiers")
    )
    assert promoted == PromoteConventionsOutput(confirmed=2)
    after = await conventions(sm, study.org_id)
    assert {k: r.approvals for k, r in after.items()} == {
        CARPET_KEY: 2,
        FLOORING_KEY: 1,
        GARAGE_KEY: 2,
    }
    assert set(after) == {CARPET_KEY, FLOORING_KEY, GARAGE_KEY}  # no new row for wool or basement
    assert sorted(
        e.detail["key"] for e in await events(sm, second.study_id, "convention.promoted")
    ) == sorted([CARPET_KEY, GARAGE_KEY])
