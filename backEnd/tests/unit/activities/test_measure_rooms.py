"""``measure_rooms``: each measured room drafted from its photos (EVAL_REPORT 110).

On the seeded study (a Kitchen of two photos, a Bathroom of one) with a scripted model: one call
per room at the production settings section 107 chose (claude-sonnet-5-5, low effort, 1,200
tokens, ``measure_interior_reference/v1``, the photos at high detail), each paid call in
``llm_calls`` about its room; an unusable answer asked once more then ``failed``; refused photos
``refused`` with no retry; a transient provider error ``failed`` (never a number); the budget
checked before the first call; an account error stops the step; the setting off, no room of a
measured type, and a completed key make no call.
"""

from __future__ import annotations

from typing import Any

import pytest
from lifecycletest_support import SeededStudy, make_deps
from sqlalchemy import func, select
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.measure_rooms import MeasureRooms
from basis.db.models import LlmCall, Room
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.llm import ScriptedTurn, Usage
from basis.llm.errors import (
    ProviderAccountError,
    ProviderBadResponse,
    ProviderRequestRejected,
    ProviderUnavailable,
)
from basis.llm.provider import ScriptedProvider
from basis.vision.measure_interior import InteriorAnswer
from basis.workflow.contracts import MeasureRoomsInput

from .conftest import Env
from .test_process_assets import analyzed as analyzed  # noqa: F401 - the fixture

pytestmark = pytest.mark.integration

USAGE = Usage(input_tokens=3200, output_tokens=220)


def answer(**over: Any) -> InteriorAnswer:
    values: dict[str, Any] = {
        "length_ft": 15.0,
        "width_ft": 12.0,
        "floor_area_sf": 180.0,
        "ceiling_height_ft": 8.0,
        "perimeter_ft": 54.0,
        "wall_area_sf": 432.0,
        "countertop_sf": 28.0,
        "cabinet_lf": 16.0,
        "room_fully_seen": False,
        "outline_shape": "rectangle",
        "confidence": "medium",
        "notes": "measured against the 80 in door",
    }
    return InteriorAnswer(**{**values, **over})


def deps_with(
    deps: Deps, turns: list[ScriptedTurn], **settings: Any
) -> tuple[Deps, ScriptedProvider]:
    provider = ScriptedProvider(turns, model="claude-sonnet-5-5")
    changed = deps.settings.model_copy(update={"measure_drafts_interior": True, **settings})
    return make_deps(deps.sessionmaker, deps.storage, changed, llm=provider), provider


def measure(seeded: SeededStudy, budget: float = 1.0, key: str = "k-measure") -> MeasureRoomsInput:
    return MeasureRoomsInput(
        study_id=str(seeded.study_id), revision=1, idempotency_key=key, budget_remaining_usd=budget
    )


async def room_ids(deps: Deps, seeded: SeededStudy) -> dict[str, str]:
    async with session_scope(deps.sessionmaker) as session:
        rows = (await session.scalars(select(Room).where(Room.study_id == seeded.study_id))).all()
    return {r.name: str(r.id) for r in rows}


async def calls(deps: Deps) -> list[LlmCall]:
    async with session_scope(deps.sessionmaker) as session:
        return list((await session.scalars(select(LlmCall))).all())


async def test_each_measured_room_is_one_call_at_the_production_settings(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    turns = [ScriptedTurn(parsed=answer(), usage=USAGE), ScriptedTurn(parsed=answer(), usage=USAGE)]
    measured, provider = deps_with(deps, turns)
    out = await activity_env.run(MeasureRooms(measured).run, measure(analyzed))
    assert (out.rooms, out.drafted, out.not_drafted) == (2, 2, 0)
    ids = await room_ids(deps, analyzed)
    by_room = {d.room_id: d for d in out.drafts}
    kitchen = by_room[ids["Kitchen"]]
    assert kitchen.status == "drafted" and kitchen.room_type == "kitchen"
    assert kitchen.photos == tuple(str(p) for p in analyzed.photo_ids[:2])
    assert kitchen.quantities["countertop_sf"] == 28.0 and kitchen.model == "claude-sonnet-5-5"
    assert kitchen.prompt_version == "measure_interior_reference/v1"
    assert by_room[ids["Bathroom"]].photos == (str(analyzed.photo_ids[2]),)
    for request in provider.requests:
        assert request.model == "claude-sonnet-5-5" and request.reasoning_effort == "low"
        assert request.max_tokens == 1200 and request.response_model is InteriorAnswer
        assert request.prompt_version == "measure_interior_reference/v1"
        assert all(i.detail == "high" for i in request.messages[0].images)
    texts = sorted(r.messages[0].content for r in provider.requests)
    assert texts == [
        "The room is a bathroom. 1 photos of it follow, taken from different spots in the "
        "room. Measure the room.",
        "The room is a kitchen. 2 photos of it follow, taken from different spots in the room. "
        "Measure the room.",
    ]
    ledger = await calls(deps)
    assert len(ledger) == 2 and {c.activity for c in ledger} == {"measure_rooms"}
    assert {str(c.entity_id) for c in ledger} == {ids["Kitchen"], ids["Bathroom"]}
    assert out.cost_usd == pytest.approx(sum(float(c.cost_usd) for c in ledger), abs=1e-6)
    assert out.cost_usd > 0
    # Each room's own calls, though the rooms share one budget in flight.
    assert sum(d.cost_usd for d in out.drafts) == pytest.approx(out.cost_usd, abs=1e-6)
    assert by_room[ids["Kitchen"]].cost_usd == pytest.approx(out.cost_usd / 2, abs=1e-6)


async def test_an_unusable_answer_is_asked_once_more_then_the_room_keeps_todays_rows(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    bad = ProviderBadResponse("no JSON", provider="scripted")
    # Rooms run concurrently; the scripted turns are taken in call order, so both rooms see
    # the same sequence: every call unusable.
    turns = [ScriptedTurn(raise_error=bad) for _ in range(4)]
    measured, provider = deps_with(deps, turns)
    out = await activity_env.run(MeasureRooms(measured).run, measure(analyzed))
    assert len(provider.requests) == 4  # two attempts a room
    assert out.drafted == 0 and out.not_drafted == 2
    assert {d.status for d in out.drafts} == {"failed"}
    assert {d.code for d in out.drafts} == {E.MEASURE_ROOM_NOT_DRAFTED}
    assert all(d.quantities == {} for d in out.drafts)


async def test_refused_photos_are_not_asked_again_and_a_transient_error_is_no_number(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    turns = [
        ScriptedTurn(
            raise_error=ProviderRequestRejected("content filter", provider="scripted", status=400)
        ),
        ScriptedTurn(raise_error=ProviderUnavailable("503", provider="scripted", status=503)),
    ]
    measured, provider = deps_with(deps, turns, photo_concurrency=1)
    out = await activity_env.run(MeasureRooms(measured).run, measure(analyzed))
    assert len(provider.requests) == 2
    assert sorted(d.status for d in out.drafts) == ["failed", "refused"]
    assert all(d.quantities == {} and d.reason for d in out.drafts)


async def test_the_budget_is_checked_before_the_first_call(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    measured, provider = deps_with(deps, [])
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(MeasureRooms(measured).run, measure(analyzed, budget=0.001))
    assert raised.value.type == "BudgetExceededError" and raised.value.non_retryable
    assert E.MEASURE_INTERIOR_BUDGET in str(raised.value)
    assert provider.requests == []


async def test_an_account_error_stops_the_step(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    turns = [
        ScriptedTurn(raise_error=ProviderAccountError("no credit", provider="scripted", status=402))
    ] * 2
    measured, _ = deps_with(deps, turns)
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(MeasureRooms(measured).run, measure(analyzed))
    assert raised.value.type == "ProviderAccountError" and raised.value.non_retryable


async def test_off_no_room_or_a_completed_key_makes_no_call(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    off, provider = deps_with(deps, [], measure_drafts_interior=False)
    out = await activity_env.run(MeasureRooms(off).run, measure(analyzed, key="k-off"))
    assert out.drafts == () and provider.requests == []
    turns = [ScriptedTurn(parsed=answer(), usage=USAGE), ScriptedTurn(parsed=answer(), usage=USAGE)]
    on, provider = deps_with(deps, turns)
    first = await activity_env.run(MeasureRooms(on).run, measure(analyzed))
    again = await activity_env.run(MeasureRooms(on).run, measure(analyzed))
    assert again == first and len(provider.requests) == 2
    async with session_scope(deps.sessionmaker) as session:
        await session.execute(
            Room.__table__.update()  # type: ignore[attr-defined]
            .where(Room.study_id == analyzed.study_id)
            .values(room_type="garage")
        )
    out = await activity_env.run(MeasureRooms(on).run, measure(analyzed, key="k-garage"))
    assert out.rooms == 0 and len(provider.requests) == 2
    async with session_scope(deps.sessionmaker) as session:
        assert await session.scalar(select(func.count()).select_from(LlmCall)) == 2


async def test_without_a_provider_the_step_fails_without_retry(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    changed = deps.settings.model_copy(update={"measure_drafts_interior": True})
    no_llm = make_deps(deps.sessionmaker, deps.storage, changed, llm=None)
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(MeasureRooms(no_llm).run, measure(analyzed))
    assert raised.value.non_retryable
