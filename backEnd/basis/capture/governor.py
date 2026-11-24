"""The breaker and the caps, as pure functions of the shared state (EVAL_REPORT 98).

The rules, which every capture worker applies to the one ``capture_state`` row and the
``capture_navigations`` log (so restarting a worker resets nothing):

* **Breaker.** A block signal (:data:`basis.capture.signals.BLOCK_CODES`) opens it for
  ``CAPTURE_BREAKER_MINUTES``, doubled for each consecutive trip (60 min, 2 h, 4 h ...) up to
  ``CAPTURE_BREAKER_MAX_HOURS``: Hystrix's and Resilience4j's open state with an exponential
  wait. While it is open every capture waits. When it lapses the next capture is the trial
  (half-open): a success closes it and resets the count; another signal trips it again,
  for twice as long. A signal while it is already open is not a new trip (no new alert, no
  doubling): it is the same episode.
* **Caps.** Page loads per hour and per day, camera moves per hour, counted in sliding
  windows over the log. A capture that would exceed one waits until enough of the window has
  aged out.
* **Spacing.** A random wait of ``CAPTURE_SPACING_MIN_S`` to ``MAX_S`` seconds since the last
  navigation of any worker, so the rhythm is neither fast nor regular.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final, Literal

from basis.config import Settings
from basis.errors.codes import E

NavigationKind = Literal["load", "move"]
HOUR: Final = timedelta(hours=1)
DAY: Final = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class GovernorConfig:
    loads_per_hour: int = 6
    loads_per_day: int = 30
    moves_per_hour: int = 120
    spacing_min_s: float = 8.0
    spacing_max_s: float = 20.0
    breaker_base: timedelta = timedelta(minutes=60)
    breaker_max: timedelta = timedelta(hours=24)

    @classmethod
    def of(cls, settings: Settings) -> GovernorConfig:
        return cls(
            loads_per_hour=settings.capture_loads_per_hour,
            loads_per_day=settings.capture_loads_per_day,
            moves_per_hour=settings.capture_moves_per_hour,
            spacing_min_s=settings.capture_spacing_min_s,
            spacing_max_s=settings.capture_spacing_max_s,
            breaker_base=timedelta(minutes=settings.capture_breaker_minutes),
            breaker_max=timedelta(hours=settings.capture_breaker_max_hours),
        )


@dataclass(frozen=True, slots=True)
class GovernorState:
    """The breaker's part of ``capture_state``."""

    breaker_open_until: datetime | None = None
    breaker_opened_at: datetime | None = None
    breaker_code: str | None = None
    breaker_reason: str | None = None
    trips: int = 0
    last_navigation_at: datetime | None = None
    last_success_at: datetime | None = None

    def open_at(self, now: datetime) -> bool:
        return self.breaker_open_until is not None and self.breaker_open_until > now


@dataclass(frozen=True, slots=True)
class Usage:
    """The navigations in the windows: page loads of the last day, moves of the last hour
    (their times, any order)."""

    loads: Sequence[datetime] = ()
    moves: Sequence[datetime] = ()

    def loads_since(self, since: datetime) -> list[datetime]:
        return sorted(t for t in self.loads if t > since)

    def moves_since(self, since: datetime) -> list[datetime]:
        return sorted(t for t in self.moves if t > since)


@dataclass(frozen=True, slots=True)
class Go:
    pass


@dataclass(frozen=True, slots=True)
class Wait:
    code: str
    until: datetime
    why: str


def breaker_wait(trips: int, config: GovernorConfig) -> timedelta:
    """How long the breaker stays open on trip number ``trips`` (1 is the first)."""
    if trips < 1:
        raise ValueError("trips start at 1")
    wait = config.breaker_base * (1 << min(trips - 1, 30))
    return min(wait, config.breaker_max)


def _window_free_at(times: list[datetime], cap: int, window: timedelta) -> datetime | None:
    """None while one more fits in the window; else when the oldest that must age out does."""
    if len(times) < cap:
        return None
    return times[len(times) - cap] + window


def admit(
    state: GovernorState,
    usage: Usage,
    config: GovernorConfig,
    now: datetime,
    kind: NavigationKind,
) -> Go | Wait:
    """Whether one more navigation of ``kind`` may happen now."""
    if state.open_at(now):
        assert state.breaker_open_until is not None
        return Wait(E.CAPTURE_BREAKER_OPEN, state.breaker_open_until, "the breaker is open")
    checks: tuple[tuple[list[datetime], int, timedelta, str], ...]
    if kind == "load":
        checks = (
            (usage.loads_since(now - HOUR), config.loads_per_hour, HOUR, "page loads this hour"),
            (usage.loads_since(now - DAY), config.loads_per_day, DAY, "page loads today"),
        )
    else:
        checks = (
            (usage.moves_since(now - HOUR), config.moves_per_hour, HOUR, "camera moves this hour"),
        )
    for times, cap, window, what in checks:
        free_at = _window_free_at(times, cap, window)
        if free_at is not None:
            return Wait(E.CAPTURE_CAPS_SPENT, free_at, f"the cap of {cap} {what} is spent")
    return Go()


def spacing_s(
    last_navigation_at: datetime | None,
    now: datetime,
    config: GovernorConfig,
    rng: random.Random,
) -> float:
    """Seconds to wait before the next navigation: a random gap in the configured range,
    less the time already passed since the last one (any worker's)."""
    gap = rng.uniform(config.spacing_min_s, config.spacing_max_s)
    if last_navigation_at is None:
        return 0.0
    passed = (now - last_navigation_at).total_seconds()
    return max(0.0, gap - passed)


@dataclass(frozen=True, slots=True)
class Trip:
    """The breaker after a block signal: ``new`` when this signal opened it (a new trip,
    which raises one alert), False when it was already open."""

    state: GovernorState
    number: int
    new: bool


def trip(
    state: GovernorState, code: str, reason: str, config: GovernorConfig, now: datetime
) -> Trip:
    if state.open_at(now):
        return Trip(state=state, number=state.trips, new=False)
    number = state.trips + 1
    return Trip(
        state=replace(
            state,
            breaker_open_until=now + breaker_wait(number, config),
            breaker_opened_at=now,
            breaker_code=code,
            breaker_reason=reason,
            trips=number,
        ),
        number=number,
        new=True,
    )


def succeeded(state: GovernorState, now: datetime) -> GovernorState:
    """A capture succeeded: the breaker closes and its count starts over."""
    return replace(
        state,
        breaker_open_until=None,
        breaker_opened_at=None,
        breaker_code=None,
        breaker_reason=None,
        trips=0,
        last_success_at=now,
    )
