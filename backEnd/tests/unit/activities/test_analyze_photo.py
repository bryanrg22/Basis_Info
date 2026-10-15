"""``analyze_photo``: real prep, vision call (offline providers), rooms and components.

``FixtureVisionProvider`` recognises the synthetic photos the ``seeded`` fixture wrote to
storage (``kitchen_1.jpg`` and ``kitchen_2.jpg`` are the kitchen scene, ``bathroom_1.jpg``
the bathroom scene) and answers with their labels; ``ScriptedProvider`` drives the
invalid-output paths. Rows are real (Postgres container).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fixtures import fixture_path
from lifecycletest_support import SeededStudy, count_rows, make_deps
from llm_fakes import PHOTO_COST_USD, FixtureVisionProvider
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.analyze_photo import SIMULATED_FAILURE, AnalyzePhoto, analysis_uri
from basis.db.models import ActivityResult, Component, Document, LlmCall, PhotoRoom, Room
from basis.db.repositories.activity_result import COMPLETED
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.llm import ScriptedProvider, ScriptedTurn, Usage
from basis.llm.errors import ProviderBadResponse, ProviderRateLimited
from basis.workflow.contracts import AnalyzePhotoInput, SimulateFault

from .conftest import Env

pytestmark = pytest.mark.integration


def analyze(
    seeded: SeededStudy,
    index: int = 0,
    *,
    budget: float = 1.0,
    simulate: SimulateFault | None = None,
    key: str | None = None,
    document_id: str | None = None,
    revision: int = 1,
) -> AnalyzePhotoInput:
    doc = document_id or str(seeded.photo_ids[index])
    return AnalyzePhotoInput(
        study_id=str(seeded.study_id),
        document_id=doc,
        revision=revision,
        idempotency_key=key or f"k-photo-{doc}-r{revision}",
        budget_remaining_usd=budget,
        simulate=simulate,
    )


def with_llm(deps: Deps, llm: Any) -> Deps:
    return make_deps(deps.sessionmaker, deps.storage, deps.settings, llm=llm)


async def put_photo(deps: Deps, document_id: uuid.UUID, data: bytes) -> None:
    async with session_scope(deps.sessionmaker) as session:
        document = await session.get(Document, document_id)
        assert document is not None
        uri = document.gcs_uri
    await deps.storage.put(uri, data, "image/jpeg")


async def rows(deps: Deps, model: type[Any], **where: Any) -> list[Any]:
    async with session_scope(deps.sessionmaker) as session:
        stmt = select(model)
        for name, value in where.items():
            stmt = stmt.where(getattr(model, name) == value)
        return list((await session.scalars(stmt)).all())


def invalid_turn() -> ScriptedTurn:
    return ScriptedTurn(
        raise_error=ProviderBadResponse(
            "output does not match ModelPhotoAnalysis",
            provider="scripted",
            usage=Usage(input_tokens=1000, output_tokens=200),
            model="gpt-4o-mini",
            raw={"choices": [{"message": {"content": "{not json"}}]},
        )
    )


async def test_writes_room_assignment_and_components(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    output = await activity_env.run(AnalyzePhoto(deps).run, analyze(seeded, 0))
    assert output.document_id == str(seeded.photo_ids[0])
    assert (output.components, output.cost_usd, output.needs_review) == (2, PHOTO_COST_USD, False)
    [room] = await rows(deps, Room, study_id=seeded.study_id)
    assert (room.name, room.room_type, room.floor, room.source) == (
        "Kitchen",
        "kitchen",
        "1",
        "vision",
    )
    assert str(room.id) == output.room_id
    assert await count_rows(deps.sessionmaker, PhotoRoom, study_id=seeded.study_id) == 1
    components = {c.label: c for c in await rows(deps, Component, study_id=seeded.study_id)}
    assert set(components) == {"kitchen_cabinet", "countertop"}
    cabinet = components["kitchen_cabinet"]
    assert cabinet.raw["count"] == 6 and cabinet.attachment == "permanent"
    assert cabinet.material is None  # "unknown" is stored as no material
    assert cabinet.raw["prompt_version"] == "photo_analysis/v1"
    [call] = await rows(deps, LlmCall, study_id=seeded.study_id)
    assert (call.activity, call.model, call.prompt_version) == (
        "analyze_photo",
        "gpt-4.1-nano",
        "photo_analysis/v1",
    )
    assert call.cost_usd == Decimal("0.010000")
    stored = json.loads(
        await deps.storage.read(analysis_uri(deps.bucket, seeded.study_id, seeded.photo_ids[0], 1))
    )
    assert stored["analysis"]["room_type"] == "kitchen"
    assert stored["image"]["width"] <= 1024 and stored["responses"]
    assert activity_env.heartbeats


async def test_the_image_sent_is_prepared(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    provider = FixtureVisionProvider()
    await activity_env.run(AnalyzePhoto(with_llm(deps, provider)).run, analyze(seeded, 0))
    [request] = provider.requests
    [image] = request.messages[0].images
    assert image.media_type == "image/jpeg" and image.detail == "high"
    assert request.response_model is not None
    assert request.response_model.__name__ == "ModelPhotoAnalysis"
    assert request.model == deps.settings.vision_model


async def test_two_photos_of_one_room_share_it_even_concurrently(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    from temporalio.testing import ActivityEnvironment

    activity = AnalyzePhoto(deps)
    # kitchen_1.jpg and kitchen_2.jpg, analyzed at the same time by two "workers"
    await asyncio.gather(
        activity_env.run(activity.run, analyze(seeded, 0)),
        ActivityEnvironment().run(activity.run, analyze(seeded, 1)),
    )
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id, name="Kitchen") == 1
    assert await count_rows(deps.sessionmaker, PhotoRoom, study_id=seeded.study_id) == 2


async def test_a_renamed_room_is_reused_in_the_next_revision(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    # Revision 2 after a rooms rejection: the reviewer renamed the kitchen (source manual).
    async with session_scope(deps.sessionmaker) as session:
        renamed = await RoomRepository(session).create(
            study_id=seeded.study_id,
            revision=2,
            name="Main Kitchen",
            room_type="kitchen",
            floor="1",
            source="manual",
        )
    output = await activity_env.run(AnalyzePhoto(deps).run, analyze(seeded, 0, revision=2))
    assert output.room_id == str(renamed.id)
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id, revision=2) == 1


async def test_rooms_on_different_floors_or_types_are_distinct(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    activity = AnalyzePhoto(deps)
    await activity_env.run(activity.run, analyze(seeded, 0))  # kitchen, floor 1
    await activity_env.run(activity.run, analyze(seeded, 2))  # bathroom, floor 2
    found = {(r.name, r.floor) for r in await rows(deps, Room, study_id=seeded.study_id)}
    assert found == {("Kitchen", "1"), ("Bathroom", "2")}


async def test_replay_returns_the_stored_output(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    provider = FixtureVisionProvider()
    activity = AnalyzePhoto(with_llm(deps, provider))
    first = await activity_env.run(activity.run, analyze(seeded, 0))
    assert await activity_env.run(activity.run, analyze(seeded, 0)) == first
    # Same photo and revision under a new key (e.g. carried over already): no new rows,
    # no new model call, no cost.
    again = await activity_env.run(activity.run, analyze(seeded, 0, key="k-new"))
    assert again.room_id == first.room_id and again.cost_usd == 0
    assert len(provider.requests) == 1
    assert await count_rows(deps.sessionmaker, Component, study_id=seeded.study_id) == 2


async def test_budget_too_small_is_refused_before_calling(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    provider = FixtureVisionProvider()
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(
            AnalyzePhoto(with_llm(deps, provider)).run, analyze(seeded, 0, budget=0.0001)
        )
    assert caught.value.type == "BudgetExceededError" and caught.value.non_retryable
    assert provider.requests == []
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id) == 0
    assert await count_rows(deps.sessionmaker, LlmCall) == 0


async def test_a_call_that_overruns_the_budget_is_recorded_and_its_result_discarded(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    # The estimate fits in 0.005 but the call costs 0.01: the money is spent (recorded)
    # and the hard cap holds (no rows, non-retryable).
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(AnalyzePhoto(deps).run, analyze(seeded, 0, budget=0.005))
    assert caught.value.type == "BudgetExceededError" and caught.value.non_retryable
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id) == 0
    [call] = await rows(deps, LlmCall)
    assert call.cost_usd == Decimal("0.010000")


async def test_invalid_output_is_retried_once(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    from llm_fakes import _analysis_by_image

    kitchen = next(a for a in _analysis_by_image().values() if a.room_type == "kitchen")
    provider = ScriptedProvider(
        [
            invalid_turn(),
            ScriptedTurn(parsed=kitchen, usage=Usage(input_tokens=1000, output_tokens=200)),
        ],
        model="gpt-4o-mini",
    )
    output = await activity_env.run(AnalyzePhoto(with_llm(deps, provider)).run, analyze(seeded, 0))
    assert not output.needs_review and output.components == 2
    assert len(provider.requests) == 2
    assert await count_rows(deps.sessionmaker, LlmCall) == 2  # both attempts cost money
    assert output.cost_usd == pytest.approx(2 * (1000 * 0.15 + 200 * 0.60) / 1e6)


async def test_invalid_output_twice_is_ambiguous_and_needs_review(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    provider = ScriptedProvider([invalid_turn(), ScriptedTurn(text="I think it is a kitchen")])
    output = await activity_env.run(AnalyzePhoto(with_llm(deps, provider)).run, analyze(seeded, 0))
    assert output.needs_review and output.components == 0
    [room] = await rows(deps, Room, study_id=seeded.study_id)
    assert (room.name, room.room_type) == ("Unassigned", "ambiguous")
    stored = json.loads(
        await deps.storage.read(analysis_uri(deps.bucket, seeded.study_id, seeded.photo_ids[0], 1))
    )
    assert stored["analysis"]["review_reason"] == "invalid_model_output"
    assert len(stored["errors"]) == 2


async def test_an_ambiguous_photo_is_flagged(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await put_photo(
        deps, seeded.photo_ids[0], fixture_path("photos", "08_ambiguous.jpg").read_bytes()
    )
    output = await activity_env.run(AnalyzePhoto(deps).run, analyze(seeded, 0))
    assert output.needs_review
    [room] = await rows(deps, Room, study_id=seeded.study_id)
    assert room.name == "Unassigned"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"", "empty"),
        (b"not an image at all", "not a readable image"),
        (None, "at least 64 px"),
    ],
)
async def test_unusable_images_are_explicit_non_retryable_errors(
    deps: Deps, seeded: SeededStudy, activity_env: Env, data: bytes | None, reason: str
) -> None:
    payload = data if data is not None else fixture_path("malformed", "tiny.jpg").read_bytes()
    await put_photo(deps, seeded.photo_ids[0], payload)
    provider = FixtureVisionProvider()
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(AnalyzePhoto(with_llm(deps, provider)).run, analyze(seeded, 0))
    assert caught.value.type == "ValidationError" and caught.value.non_retryable
    assert reason in str(caught.value)
    assert provider.requests == []
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id) == 0


async def test_a_missing_photo_file_is_not_an_unknown_room(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    async with session_scope(deps.sessionmaker) as session:
        document = await session.get(Document, seeded.photo_ids[0])
        assert document is not None
        await deps.storage.delete(document.gcs_uri)
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(AnalyzePhoto(deps).run, analyze(seeded, 0))
    assert caught.value.non_retryable and "missing from storage" in str(caught.value)


async def test_no_provider_configured_is_non_retryable(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(AnalyzePhoto(with_llm(deps, None)).run, analyze(seeded, 0))
    assert caught.value.type == "ProviderNotConfigured" and caught.value.non_retryable


async def test_a_rate_limited_provider_is_retried_by_temporal(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    provider = ScriptedProvider(
        [ScriptedTurn(raise_error=ProviderRateLimited("429", provider="azure", status=429))]
    )
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(AnalyzePhoto(with_llm(deps, provider)).run, analyze(seeded, 0))
    assert caught.value.type == "ProviderUnavailable" and not caught.value.non_retryable
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id) == 0


async def test_fail_attempts_fails_early_attempts_then_succeeds(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    activity = AnalyzePhoto(deps)
    payload = analyze(seeded, 0, simulate=SimulateFault(fail_attempts=1))
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(activity.run, payload)
    assert caught.value.type == SIMULATED_FAILURE and not caught.value.non_retryable
    activity_env.with_info(attempt=2)
    output = await activity_env.run(activity.run, payload)
    assert output.components == 2


async def test_fail_permanently_is_non_retryable(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    payload = analyze(seeded, 0, simulate=SimulateFault(fail_permanently=True))
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(AnalyzePhoto(deps).run, payload)
    assert caught.value.non_retryable


async def test_faults_are_ignored_outside_dev_and_test(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    staging = deps.settings.model_copy(update={"env": "staging"})
    deps_staging = make_deps(deps.sessionmaker, deps.storage, staging)
    assert not deps_staging.faults_enabled
    payload = analyze(seeded, 0, simulate=SimulateFault(fail_permanently=True))
    output = await activity_env.run(AnalyzePhoto(deps_staging).run, payload)
    assert output.components == 2


async def test_cancellation_rolls_back_the_partial_rows(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    payload = analyze(seeded, 0, simulate=SimulateFault(sleep_seconds=10))
    task = asyncio.ensure_future(activity_env.run(AnalyzePhoto(deps).run, payload))
    for _ in range(200):  # wait until it sleeps inside the transaction (room inserted)
        if any(details == ("sleeping",) for details in activity_env.heartbeats):
            break
        await asyncio.sleep(0.05)
    activity_env.env.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id) == 0
    assert await count_rows(deps.sessionmaker, PhotoRoom, study_id=seeded.study_id) == 0
    rows_done = await count_rows(deps.sessionmaker, ActivityResult, status=COMPLETED)
    assert rows_done == 0


@pytest.mark.parametrize("which", ["appraisal", "foreign"])
async def test_not_a_photo_of_this_study_is_not_retried(
    deps: Deps, seeded: SeededStudy, activity_env: Env, which: str
) -> None:
    doc = str(seeded.appraisal_id) if which == "appraisal" else str(uuid.uuid4())
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(AnalyzePhoto(deps).run, analyze(seeded, document_id=doc))
    assert caught.value.type == "ValidationError" and caught.value.non_retryable
