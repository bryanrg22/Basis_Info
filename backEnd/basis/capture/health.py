"""The ``property_capture`` member of ``GET /v1/ops/health`` (docs/api/OPS_HEALTH.md section 7):
whether captures are on, the capture worker's last sign of life, the breaker (closed; open
until when and why; half-open: lapsed, waiting for the trial capture), the navigations used
in their windows against the caps, the last success and the lease. Pure: built from a
:class:`~basis.capture.store.Snapshot` and the settings."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Final, Literal

from basis.capture import governor as gov
from basis.capture.governor import GovernorConfig
from basis.capture.store import Snapshot

# The capture worker writes ``worker_seen_at`` every WORKER_BEAT; three missed beats is down.
WORKER_BEAT: Final = timedelta(seconds=30)
WORKER_STALE: Final = timedelta(seconds=90)
BreakerState = Literal["closed", "open", "half_open"]


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def breaker_state(snapshot: Snapshot, now: datetime) -> BreakerState:
    state = snapshot.state
    if state.open_at(now):
        return "open"
    if state.trips > 0:
        return "half_open"
    return "closed"


def capture_health(
    snapshot: Snapshot, *, enabled: bool, config: GovernorConfig, now: datetime
) -> dict[str, Any]:
    state, usage = snapshot.state, snapshot.usage
    seen = snapshot.worker_seen_at
    worker = "never" if seen is None else ("up" if now - seen <= WORKER_STALE else "down")
    held = snapshot.lease_expires_at is not None and snapshot.lease_expires_at > now
    breaker = breaker_state(snapshot, now)
    return {
        "enabled": enabled,
        "worker": {"status": worker, "seen_at": _iso(seen)},
        "breaker": {
            "state": breaker,
            "open_until": _iso(state.breaker_open_until) if breaker != "closed" else None,
            "opened_at": _iso(state.breaker_opened_at) if breaker != "closed" else None,
            "code": state.breaker_code if breaker != "closed" else None,
            "reason": state.breaker_reason if breaker != "closed" else None,
            "trips": state.trips,
        },
        "used": {
            "loads_hour": len(usage.loads_since(now - gov.HOUR)),
            "loads_hour_cap": config.loads_per_hour,
            "loads_day": len(usage.loads_since(now - gov.DAY)),
            "loads_day_cap": config.loads_per_day,
            "moves_hour": len(usage.moves_since(now - gov.HOUR)),
            "moves_hour_cap": config.moves_per_hour,
        },
        "last_success_at": _iso(state.last_success_at),
        "last_navigation_at": _iso(state.last_navigation_at),
        "capturing": held,
    }
