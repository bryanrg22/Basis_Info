"""The breaker, the caps and the spacing (:mod:`basis.capture.governor`), and the
``property_capture`` member of ``GET /v1/ops/health`` (:mod:`basis.capture.health`).

* The breaker's wait doubles per consecutive trip from the base to the cap; a signal while it
  is open is the same trip; a success closes it and starts the count over.
* Each cap admits up to its count in its sliding window and names when the window frees.
* The spacing is random within its range, less the time already passed.
* The health member reports on/off, the worker (up, down, never), the breaker (closed, open
  with until/why/code, half-open after it lapsed), the navigations against the caps, the last
  success and whether a capture runs, as the store holds them after a run.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

from capturetest_support import Clock, FakeBrowser, MemoryStorage, factory, failed_page

from basis.capture import governor as gov
from basis.capture.governor import GovernorConfig, GovernorState, Usage
from basis.capture.health import capture_health
from basis.capture.runner import CaptureRunner
from basis.capture.store import MemoryCaptureStore, PinRow
from basis.config import Settings
from basis.errors.codes import E

NOW = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)
CONFIG = GovernorConfig()


def test_the_breaker_wait_doubles_to_its_cap() -> None:
    waits = [gov.breaker_wait(n, CONFIG) for n in range(1, 8)]
    assert waits[:5] == [timedelta(hours=h) for h in (1, 2, 4, 8, 16)]
    assert waits[5:] == [timedelta(hours=24)] * 2
    assert gov.breaker_wait(100, CONFIG) == timedelta(hours=24)


def test_a_trip_while_open_is_the_same_episode_and_success_resets() -> None:
    first = gov.trip(GovernorState(), E.CAPTURE_RECAPTCHA, "captcha", CONFIG, NOW)
    assert first.new and first.number == 1
    again = gov.trip(first.state, E.CAPTURE_WALL, "wall", CONFIG, NOW + timedelta(minutes=5))
    assert not again.new and again.state == first.state
    second = gov.trip(first.state, E.CAPTURE_WALL, "wall", CONFIG, NOW + timedelta(hours=1))
    assert second.new and second.number == 2
    assert second.state.breaker_open_until == NOW + timedelta(hours=3)
    closed = gov.succeeded(second.state, NOW + timedelta(hours=4))
    assert closed.trips == 0 and closed.breaker_open_until is None and closed.breaker_code is None
    assert gov.trip(closed, E.CAPTURE_WALL, "w", CONFIG, NOW + timedelta(hours=5)).number == 1


def test_each_cap_admits_up_to_its_count_and_names_when_it_frees() -> None:
    state = GovernorState()
    five = [NOW - timedelta(minutes=m) for m in (50, 40, 30, 20, 10)]
    assert gov.admit(state, Usage(loads=five), CONFIG, NOW, "load") == gov.Go()
    six = [*five, NOW - timedelta(minutes=5)]
    wait = gov.admit(state, Usage(loads=six), CONFIG, NOW, "load")
    assert wait == gov.Wait(
        E.CAPTURE_CAPS_SPENT,
        NOW + timedelta(minutes=10),
        "the cap of 6 page loads this hour is spent",
    )
    # Loads older than the hour count only against the day.
    day = [NOW - timedelta(hours=2, minutes=m) for m in range(30)]
    wait = gov.admit(state, Usage(loads=day), CONFIG, NOW, "load")
    assert isinstance(wait, gov.Wait) and "today" in wait.why
    assert wait.until == min(day) + timedelta(days=1)
    moves = [NOW - timedelta(seconds=s) for s in range(120)]
    assert isinstance(gov.admit(state, Usage(moves=moves), CONFIG, NOW, "move"), gov.Wait)
    assert gov.admit(state, Usage(moves=moves[:119]), CONFIG, NOW, "move") == gov.Go()
    # An open breaker outranks the caps.
    open_state = GovernorState(breaker_open_until=NOW + timedelta(minutes=1))
    assert gov.admit(open_state, Usage(), CONFIG, NOW, "move") == gov.Wait(
        E.CAPTURE_BREAKER_OPEN, NOW + timedelta(minutes=1), "the breaker is open"
    )


def test_the_spacing_is_random_within_its_range_less_the_time_passed() -> None:
    rng = random.Random(3)
    assert gov.spacing_s(None, NOW, CONFIG, rng) == 0
    gaps = [gov.spacing_s(NOW, NOW, CONFIG, rng) for _ in range(200)]
    assert min(gaps) >= 8 and max(gaps) <= 20 and max(gaps) - min(gaps) > 8
    assert gov.spacing_s(NOW - timedelta(seconds=25), NOW, CONFIG, rng) == 0


def test_the_config_comes_from_the_settings() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        capture_loads_per_hour=3,
        capture_loads_per_day=9,
        capture_breaker_minutes=30,
    )
    config = GovernorConfig.of(settings)
    assert (config.loads_per_hour, config.loads_per_day) == (3, 9)
    assert config.breaker_base == timedelta(minutes=30)


async def test_the_health_member_reflects_the_store_after_a_trip_and_a_success() -> None:
    store = MemoryCaptureStore()
    clock = Clock()
    study = uuid.uuid4()
    pin = PinRow(study_id=study, address="1 Main", lat=None, lon=None, source="engineer", version=1)
    capture = store.add_capture(study, pin)
    blocked = FakeBrowser(on_load={0: failed_page("https://www.google.com/sorry/x", 429)})
    runner = CaptureRunner(
        store,
        MemoryStorage(),
        "b",
        factory(blocked),  # type: ignore[arg-type]
        config=CONFIG,
        holder="w",
        settle_s=0,
        clock=clock,
        sleep=clock.sleep,
        rng=random.Random(1),
    )
    empty = capture_health(
        await store.snapshot(clock.now), enabled=False, config=CONFIG, now=clock.now
    )
    assert empty["enabled"] is False and empty["worker"] == {"status": "never", "seen_at": None}
    assert empty["breaker"]["state"] == "closed" and empty["last_success_at"] is None
    await store.worker_seen(clock.now)
    await runner.run(capture.id)
    health = capture_health(
        await store.snapshot(clock.now), enabled=True, config=CONFIG, now=clock.now
    )
    assert health["worker"]["status"] == "up"
    assert health["breaker"] == {
        "state": "open",
        "open_until": (clock.now + timedelta(hours=1)).isoformat(),
        "opened_at": clock.now.isoformat(),
        "code": E.CAPTURE_UNUSUAL_TRAFFIC,
        "reason": "the unusual-traffic page",
        "trips": 1,
    }
    assert health["used"] == {
        "loads_hour": 1,
        "loads_hour_cap": 6,
        "loads_day": 1,
        "loads_day_cap": 30,
        "moves_hour": 0,
        "moves_hour_cap": 120,
    }
    assert health["capturing"] is False
    # Lapsed: half-open until the trial capture succeeds; the worker went quiet.
    clock.advance(minutes=61)
    lapsed = capture_health(
        await store.snapshot(clock.now), enabled=True, config=CONFIG, now=clock.now
    )
    assert lapsed["breaker"]["state"] == "half_open" and lapsed["worker"]["status"] == "down"
    ok = CaptureRunner(
        store,
        MemoryStorage(),
        "b",
        factory(FakeBrowser()),  # type: ignore[arg-type]
        config=CONFIG,
        holder="w",
        settle_s=0,
        clock=clock,
        sleep=clock.sleep,
        rng=random.Random(1),
    )
    await ok.run(capture.id)
    closed = capture_health(
        await store.snapshot(clock.now), enabled=True, config=CONFIG, now=clock.now
    )
    assert closed["breaker"]["state"] == "closed" and closed["breaker"]["trips"] == 0
    assert closed["last_success_at"] == clock.now.isoformat()
    assert closed["used"]["loads_hour"] == 1 and closed["used"]["moves_hour"] == 6
    assert closed["used"]["loads_day"] == 2
