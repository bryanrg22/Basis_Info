"""``analyze_photo``: real prep, vision call (offline providers), rooms and components.

``FixtureVisionProvider`` recognises the synthetic photos the ``seeded`` fixture wrote to
storage (``kitchen_1.jpg`` and ``kitchen_2.jpg`` are the kitchen scene, ``bathroom_1.jpg``
the bathroom scene) and answers with their labels; ``ScriptedProvider`` drives the
invalid-output paths. Rows are real (Postgres container). The ``decomposed`` tests run the
same activity with ``VISION_PHOTO_MODE=decomposed``: two calls per photo, both recorded,
their cost summed, one merged analysis.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fixtures import fixture_path
from lifecycletest_support import SeededStudy, count_rows, make_deps, seed_run, seed_study
from llm_fakes import PHOTO_COST_USD, FixtureVisionProvider, PromptKeyedProvider
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.analyze_photo import SIMULATED_FAILURE, AnalyzePhoto, analysis_uri
from basis.db.models import ActivityResult, Component, Document, LlmCall, PhotoRoom, Room, Study
from basis.db.repositories.activity_result import COMPLETED
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.llm import ScriptedProvider, ScriptedTurn, Usage
from basis.llm.errors import ProviderAccountError, ProviderBadResponse, ProviderRateLimited
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


def by_identity(deps: Deps) -> Deps:
    """The same deps with ``ROOM_GROUPING=model``: rooms by the model's room identity (the
    fallback for a file name that names no room), whatever the seeded file names say."""
    settings = deps.settings.model_copy(update={"room_grouping": "model"})
    return make_deps(deps.sessionmaker, deps.storage, settings, llm=deps.llm)


def decomposed(deps: Deps, llm: Any = None) -> Deps:
    """The same deps with ``VISION_PHOTO_MODE=decomposed`` (the fixture provider unless given)."""
    settings = deps.settings.model_copy(update={"vision_photo_mode": "decomposed"})
    if llm is None:
        return make_deps(deps.sessionmaker, deps.storage, settings)
    return make_deps(deps.sessionmaker, deps.storage, settings, llm=llm)


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
    assert request.max_tokens == deps.settings.vision_output_budget
    assert request.reasoning_effort == deps.settings.vision_reasoning_effort


async def test_the_production_vision_settings_reach_every_call(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    """EVAL_REPORT 12.1 defaults: sol, decomposed, effort low, budget 6000 on both calls."""
    settings = deps.settings.model_copy(
        update={
            "vision_model": "gpt-6.1-sol",
            "vision_photo_mode": "decomposed",
            "vision_reasoning_effort": "low",
            "vision_max_tokens": None,
        }
    )
    provider = FixtureVisionProvider()
    chosen = make_deps(deps.sessionmaker, deps.storage, settings, llm=provider)
    await activity_env.run(AnalyzePhoto(chosen).run, analyze(seeded, 0, budget=5.0))
    assert len(provider.requests) == 2
    for request in provider.requests:
        assert (request.model, request.max_tokens, request.reasoning_effort) == (
            "gpt-6.1-sol",
            6000,
            "low",
        )


async def test_two_photos_of_one_room_share_it_even_concurrently(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    from temporalio.testing import ActivityEnvironment

    activity = AnalyzePhoto(deps)
    # "Kitchen photo 1.jpg" and "Kitchen photo 2.jpg", analyzed at once by two "workers"
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
    output = await activity_env.run(
        AnalyzePhoto(by_identity(deps)).run, analyze(seeded, 0, revision=2)
    )
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
    output = await activity_env.run(
        AnalyzePhoto(with_llm(by_identity(deps), provider)).run, analyze(seeded, 0)
    )
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
    output = await activity_env.run(AnalyzePhoto(by_identity(deps)).run, analyze(seeded, 0))
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


async def test_an_account_error_is_non_retryable_and_names_the_provider(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    """Not a bad photo (EVAL_REPORT 26.12): the activity fails with the non-retryable
    ``ProviderAccountError`` and writes nothing, so the workflow fails the stage with the
    provider's reason instead of flagging this photo and every one after it."""
    error = ProviderAccountError(
        "Your credit balance is too low to access the Anthropic API.",
        provider="anthropic",
        status=400,
        code="invalid_request_error",
    )
    provider = ScriptedProvider([ScriptedTurn(raise_error=error)])
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(AnalyzePhoto(with_llm(deps, provider)).run, analyze(seeded, 0))
    assert caught.value.type == "ProviderAccountError" and caught.value.non_retryable
    assert "anthropic account error (HTTP 400" in str(caught.value)
    assert "credit balance is too low" in str(caught.value)
    assert len(provider.requests) == 1  # not retried as an unusable answer
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id) == 0
    assert await count_rows(deps.sessionmaker, PhotoRoom, study_id=seeded.study_id) == 0


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


# --------------------------------------------------------------------------- decomposed mode

DECOMPOSED_PROMPTS = ["photo_countables/v3", "photo_surfaces/v2"]


async def test_the_default_mode_is_single(deps: Deps) -> None:
    assert deps.settings.vision_photo_mode == "single"


async def test_decomposed_makes_two_recorded_calls_and_writes_one_merged_analysis(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    # The bedroom scene: carpet comes from the surfaces call, the unlisted item from the
    # countables call; the photo's components are the union.
    await put_photo(
        deps, seeded.photo_ids[0], fixture_path("photos", "03_bedroom.jpg").read_bytes()
    )
    provider = FixtureVisionProvider()
    output = await activity_env.run(
        AnalyzePhoto(decomposed(by_identity(deps), provider)).run, analyze(seeded, 0)
    )
    assert (output.components, output.needs_review) == (2, False)
    assert output.cost_usd == pytest.approx(2 * PHOTO_COST_USD)  # both calls charged
    assert sorted(r.prompt_version for r in provider.requests) == DECOMPOSED_PROMPTS
    [room] = await rows(deps, Room, study_id=seeded.study_id)
    assert (room.name, room.room_type, room.floor) == ("Bedroom", "bedroom", "2")
    components = {c.label: c for c in await rows(deps, Component, study_id=seeded.study_id)}
    assert set(components) == {"carpet", "other"}
    assert {c.raw["prompt_version"] for c in components.values()} == {
        "photo_countables/v3+photo_surfaces/v2"
    }
    calls = await rows(deps, LlmCall, study_id=seeded.study_id)
    assert sorted(c.prompt_version for c in calls) == DECOMPOSED_PROMPTS
    assert {(c.activity, c.model, c.cost_usd) for c in calls} == {
        ("analyze_photo", "gpt-4.1-nano", Decimal("0.010000"))
    }
    stored = json.loads(
        await deps.storage.read(analysis_uri(deps.bucket, seeded.study_id, seeded.photo_ids[0], 1))
    )
    assert (stored["mode"], stored["prompt_versions"]) == ("decomposed", DECOMPOSED_PROMPTS)
    assert [(c["prompt_version"], c["usable"], c["attempts"]) for c in stored["calls"]] == [
        ("photo_countables/v3", True, 1),
        ("photo_surfaces/v2", True, 1),
    ]
    assert len(stored["responses"]) == 2 and stored["attempts"] == 2
    assert {c["label"] for c in stored["analysis"]["components"]} == {"carpet", "other"}


async def test_decomposed_writes_the_same_rows_as_single_for_the_kitchen(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    single = await activity_env.run(AnalyzePhoto(deps).run, analyze(seeded, 0))
    before = {
        (c.label, c.raw["count"], c.material, c.attachment)
        for c in await rows(deps, Component, study_id=seeded.study_id)
    }
    # kitchen_2.jpg is the same scene; analyzed decomposed it joins the same room with the
    # same component kinds and counts, at twice the cost.
    output = await activity_env.run(AnalyzePhoto(decomposed(deps)).run, analyze(seeded, 1))
    assert output.room_id == single.room_id and output.components == single.components == 2
    assert output.cost_usd == pytest.approx(2 * single.cost_usd)
    after = {
        (c.label, c.raw["count"], c.material, c.attachment)
        for c in await rows(
            deps, Component, study_id=seeded.study_id, document_id=seeded.photo_ids[1]
        )
    }
    assert (
        after
        == before
        == {("kitchen_cabinet", 6, None, "permanent"), ("countertop", 1, None, "permanent")}
    )
    assert await count_rows(deps.sessionmaker, LlmCall, study_id=seeded.study_id) == 3


async def test_decomposed_one_failing_call_fails_the_photo(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    from llm_fakes import _analysis_by_image

    kitchen = next(a for a in _analysis_by_image().values() if a.room_type == "kitchen")
    provider = PromptKeyedProvider(
        {
            "photo_countables": [
                ScriptedTurn(parsed=kitchen, usage=Usage(input_tokens=1000, output_tokens=200))
            ],
            "photo_surfaces": [
                ScriptedTurn(raise_error=ProviderRateLimited("429", provider="azure", status=429))
            ],
        }
    )
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(AnalyzePhoto(decomposed(deps, provider)).run, analyze(seeded, 0))
    assert caught.value.type == "ProviderUnavailable" and not caught.value.non_retryable
    # No half result: no room, no assignment, no component from the call that succeeded.
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id) == 0
    assert await count_rows(deps.sessionmaker, PhotoRoom, study_id=seeded.study_id) == 0
    assert await count_rows(deps.sessionmaker, Component, study_id=seeded.study_id) == 0


async def test_decomposed_one_call_unusable_twice_is_ambiguous_with_the_call_named(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    from llm_fakes import _analysis_by_image

    kitchen = next(a for a in _analysis_by_image().values() if a.room_type == "kitchen")
    provider = PromptKeyedProvider(
        {
            "photo_countables": [
                ScriptedTurn(parsed=kitchen, usage=Usage(input_tokens=1000, output_tokens=200))
            ],
            "photo_surfaces": [
                invalid_turn(),
                ScriptedTurn(text="tile floor", usage=Usage(input_tokens=1000, output_tokens=200)),
            ],
        }
    )
    output = await activity_env.run(
        AnalyzePhoto(decomposed(by_identity(deps), provider)).run, analyze(seeded, 0)
    )
    assert output.needs_review and output.components == 0
    [room] = await rows(deps, Room, study_id=seeded.study_id)
    assert (room.name, room.room_type) == ("Unassigned", "ambiguous")
    stored = json.loads(
        await deps.storage.read(analysis_uri(deps.bucket, seeded.study_id, seeded.photo_ids[0], 1))
    )
    assert stored["analysis"]["review_reason"] == "invalid_model_output:photo_surfaces/v2"
    assert [c["usable"] for c in stored["calls"]] == [True, False]
    assert await count_rows(deps.sessionmaker, LlmCall) == 3  # every attempt cost money
    assert output.cost_usd == pytest.approx(3 * (1000 * 0.15 + 200 * 0.60) / 1e6)


async def test_decomposed_budget_must_cover_both_calls(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    # 0.015 covers one fixture call (0.01) but not two. Each call's estimate fits, so both
    # are sent, recorded and charged; the second charge breaks the cap and the merged
    # result is discarded: non-retryable, no rows (as the single mode's overrun).
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(AnalyzePhoto(decomposed(deps)).run, analyze(seeded, 0, budget=0.015))
    assert caught.value.type == "BudgetExceededError" and caught.value.non_retryable
    assert await count_rows(deps.sessionmaker, Room, study_id=seeded.study_id) == 0
    assert await count_rows(deps.sessionmaker, Component, study_id=seeded.study_id) == 0
    calls = await rows(deps, LlmCall, study_id=seeded.study_id)
    assert sorted(c.prompt_version for c in calls) == DECOMPOSED_PROMPTS  # the spend is kept


async def test_decomposed_replay_makes_no_new_calls(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    provider = FixtureVisionProvider()
    activity = AnalyzePhoto(decomposed(deps, provider))
    first = await activity_env.run(activity.run, analyze(seeded, 0))
    assert await activity_env.run(activity.run, analyze(seeded, 0)) == first
    again = await activity_env.run(activity.run, analyze(seeded, 0, key="k-new"))
    assert again.room_id == first.room_id and again.cost_usd == 0
    assert len(provider.requests) == 2


# --------------------------------------------------------------------------- file-name rooms
# EVAL_REPORT section 27, item 1: ROOM_GROUPING=filename (the default) puts a photo in the
# room its file name names (basis.vision.rooms.group_photos, after the study's aliases).


async def seed_named(
    deps: Deps, storage: Any, names: tuple[str, ...], aliases: dict[str, str] | None = None
) -> SeededStudy:
    study = await seed_study(deps.sessionmaker, photos=names, storage=storage)
    await seed_run(deps.sessionmaker, study.study_id)
    if aliases:
        async with session_scope(deps.sessionmaker) as session:
            row = await session.get(Study, study.study_id)
            assert row is not None
            row.room_aliases = aliases
    return study


NAMED = (
    "15_1st Floor Breakfast Nook.JPG",
    "21_1st Floor Kitchen photo 1.JPG",
    "22_1st Floor Kitchen photo 2.JPG",
    "45_Basement Bath.JPG",
    "IMG_0001.JPG",
)


async def test_photos_are_grouped_by_file_name_and_aliases(
    deps: Deps, storage: Any, activity_env: Env
) -> None:
    study = await seed_named(deps, storage, NAMED, {"1_breakfast_nook": "1_kitchen"})
    activity = AnalyzePhoto(deps)
    outputs = [
        await activity_env.run(activity.run, analyze(study, index)) for index in range(len(NAMED))
    ]
    rooms = {r.id: r for r in await rows(deps, Room, study_id=study.study_id)}
    by_photo = {NAMED[i]: rooms[uuid.UUID(o.room_id)] for i, o in enumerate(outputs)}
    kitchen = by_photo["21_1st Floor Kitchen photo 1.JPG"]
    # The nook (aliased) and both kitchen views are one room, named and floored by the file.
    assert by_photo["15_1st Floor Breakfast Nook.JPG"] is kitchen
    assert by_photo["22_1st Floor Kitchen photo 2.JPG"] is kitchen
    assert (kitchen.group_key, kitchen.name, kitchen.floor) == (
        "1_kitchen",
        "1st Floor Kitchen",
        "1",
    )
    bath = by_photo["45_Basement Bath.JPG"]
    assert (bath.group_key, bath.name, bath.floor) == ("b_bath", "Basement Bath", "0")
    # A camera name says nothing: the model's room identity (the bedroom scene) is used.
    fallback = by_photo["IMG_0001.JPG"]
    assert (fallback.group_key, fallback.name, fallback.room_type) == (None, "Bedroom", "bedroom")
    assert len(rooms) == 3


async def test_without_aliases_each_named_view_is_its_own_room(
    deps: Deps, storage: Any, activity_env: Env
) -> None:
    study = await seed_named(deps, storage, NAMED[:2])
    activity = AnalyzePhoto(deps)
    for index in range(2):
        await activity_env.run(activity.run, analyze(study, index))
    keys = sorted(r.group_key for r in await rows(deps, Room, study_id=study.study_id))
    assert keys == ["1_breakfast_nook", "1_kitchen"]


async def test_an_ambiguous_photo_with_a_room_name_joins_its_named_room(
    deps: Deps, storage: Any, activity_env: Env
) -> None:
    study = await seed_named(deps, storage, ("Kitchen photo 1.jpg", "Kitchen photo 2.jpg"))
    await put_photo(
        deps, study.photo_ids[1], fixture_path("photos", "08_ambiguous.jpg").read_bytes()
    )
    activity = AnalyzePhoto(deps)
    first = await activity_env.run(activity.run, analyze(study, 0))
    second = await activity_env.run(activity.run, analyze(study, 1))
    assert second.needs_review  # still flagged for the reviewer
    assert first.room_id == second.room_id
    [room] = await rows(deps, Room, study_id=study.study_id)
    assert (room.group_key, room.room_type) == ("x_kitchen", "kitchen")


async def test_model_grouping_ignores_file_names(
    deps: Deps, storage: Any, activity_env: Env
) -> None:
    # Two file-name rooms, both the kitchen scene: one room by the model's identity.
    study = await seed_named(
        deps, storage, ("21_1st Floor Kitchen photo 1.JPG", "Kitchen Island.JPG")
    )
    activity = AnalyzePhoto(by_identity(deps))
    for index in range(2):
        await activity_env.run(activity.run, analyze(study, index))
    [room] = await rows(deps, Room, study_id=study.study_id)
    assert (room.group_key, room.name) == (None, "Kitchen")
