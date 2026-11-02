"""The service list of ``GET /v1/ops/health`` (docs/api/OPS_HEALTH.md), composed from
:mod:`basis.ops.evidence` and the worker's heartbeat report (:mod:`basis.ops.runtime`).

Rules (the contract):

* A status other than ``unknown`` always has the time of the evidence behind it
  (``checked_at``) and how long that evidence stands (``expires_at``); evidence past
  ``expires_at`` is reported ``unknown``, never with its old status.
* ``configuration`` evidence (a setting) is never ``healthy``.
* ``disabled`` is an optional dependency the effective configuration does not use.
* A capability (``appraisal``, ``vision`` ...) aggregates only the dependencies it uses
  under the worker's effective settings; its evidence is the oldest of theirs.
* Missing licensed data (the RSMeans table), a missing reference corpus and stale cost
  indices are ``degraded`` with a detail that says so, never a provider outage.
* Models and adapters come from the worker's report (what it runs), not from defaults.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from basis.errors.codes import E
from basis.ops.evidence import POLLER_RECENT, Heartbeat, Ledger, Outcome, Pollers, State

Status = Literal["healthy", "degraded", "down", "unknown", "disabled"]
CheckKind = Literal["probe", "heartbeat", "model_run", "configuration"]

SERVICE_IDS: Final = (
    "api",
    "db",
    "temporal",
    "worker",
    "relay",
    "storage",
    "firebase",
    "openai",
    "azure_openai",
    "anthropic",
    "replicate",
    "azure_di",
    "appraisal",
    "vision",
    "sam3",
    "mask2former",
    "room_inventory",
    "embeddings",
    "retrieval",
    "classification",
    "takeoff",
    "structure",
    "rsmeans",
    "cost_indices",
    "exports",
)
# How long a check this request ran stands as evidence.
PROBE_VALID: Final = timedelta(seconds=60)
# A provider probe stands for two probe intervals, at least two minutes.
MIN_PROBE_VALID: Final = timedelta(seconds=120)
# A worker still starting after this long is down, not cold-starting.
COLD_START_LIMIT: Final = timedelta(minutes=15)
# The relay's backlog: a due message older than this, or any dead one, is degraded.
BACKLOG_DEGRADED_S: Final = 60.0
REFRESH_CLI: Final = "python -m basis.costs.refresh"
_BAD: Final[dict[str, int]] = {"healthy": 0, "disabled": 0, "degraded": 1, "unknown": 2, "down": 3}
PROVIDER_LEDGER_NAME: Final[dict[str, str]] = {
    "openai": "openai",
    "azure_openai": "azure",
    "anthropic": "anthropic",
}
PROVIDER_ADAPTER: Final[dict[str, str]] = {
    "openai": "OpenAI API",
    "azure_openai": "Azure OpenAI",
    "anthropic": "Anthropic API",
}
# Where a provider's requests are served: the provider itself (a hosting label, section 2).
PROVIDER_HOSTING: Final[dict[str, str]] = {
    "openai": "OpenAI",
    "azure_openai": "Microsoft Azure",
    "anthropic": "Anthropic",
}
NO_FALLBACK: Final[tuple[str, ...]] = ()  # the code has no fallback for this dependency


@dataclass(frozen=True, slots=True)
class LastInference:
    at: datetime
    model: str
    kind: str


@dataclass(frozen=True, slots=True)
class RouteHop:
    """One provider a model is routed to: the pool's provider name, its service id and the
    model or deployment the request is sent to."""

    provider: str
    service: str
    target: str

    @property
    def label(self) -> str:
        return f"{PROVIDER_ADAPTER.get(self.service, self.provider)} · {self.target}"


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """A model the pipeline calls, by the setting that names it, with its route: the primary
    provider first, then the fallbacks in order (from the worker's heartbeat report)."""

    setting: str
    model: str
    primary: RouteHop | None
    fallbacks: tuple[RouteHop, ...]

    @property
    def fallback_labels(self) -> tuple[str, ...]:
        return tuple(f"{self.model} → {hop.label}" for hop in self.fallbacks)


@dataclass(frozen=True, slots=True)
class Service:
    id: str
    status: Status
    check_kind: CheckKind
    detail: str
    required: bool
    checked_at: datetime | None = None
    expires_at: datetime | None = None
    latency_ms: float | None = None
    adapter: str | None = None
    model: str | None = None
    last_inference: LastInference | None = None
    # Section 2: where it runs (None: not established), the ordered backups (None: not known;
    # empty: the code has none), and the models it calls with their routes.
    hosting: str | None = None
    fallbacks: tuple[str, ...] | None = None
    models: tuple[ModelRoute, ...] = ()


@dataclass(frozen=True, slots=True)
class Inputs:
    """Everything the composition reads; built by :mod:`basis.api.routes.ops`."""

    now: datetime
    api_build: str
    api_env: str
    db: Outcome[bool] | None  # None: no database configured in the API
    temporal: Outcome[bool] | None  # None: Temporal not configured in the API
    pollers: Outcome[Pollers] | None
    state: Outcome[State] | None
    ledger: Outcome[Ledger] | None
    storage: Outcome[str]
    storage_adapter: str
    firebase_adapter: str
    firebase_project: str | None
    token_verified_at: datetime
    task_queue: str
    extra: dict[str, Any] = field(default_factory=dict)
    # Hosting labels the API establishes from its own runtime (basis.ops.hosting); None
    # when it cannot. The worker's and relay's come from their heartbeat reports.
    api_hosting: str | None = None
    db_hosting: str | None = None
    temporal_hosting: str | None = None
    storage_hosting: str | None = None
    firebase_hosting: str | None = None


def _ago(now: datetime, then: datetime) -> str:
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 120:
        return f"{seconds} s ago"
    if seconds < 7200:
        return f"{seconds // 60} min ago"
    if seconds < 172800:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} days ago"


def _sentence(parts: Sequence[str]) -> str:
    """Parts joined by semicolons, the first letter upper-cased (codes keep their case)."""
    text = "; ".join(parts)
    return text[:1].upper() + text[1:]


def _stamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def expire(service: Service, now: datetime) -> Service:
    """Evidence past ``expires_at`` is unknown (its time stays), and so is configuration
    evidence claiming health."""
    if service.status in ("unknown", "disabled"):
        return service
    if service.check_kind == "configuration" and service.status == "healthy":
        return replace(service, status="unknown")
    if service.checked_at is None:
        return replace(service, status="unknown", detail=service.detail + " (no evidence time)")
    if service.expires_at is not None and now > service.expires_at:
        return replace(
            service,
            status="unknown",
            detail=f"Evidence from {_ago(now, service.checked_at)} expired: {service.detail}",
        )
    return service


# ------------------------------------------------------------------------ infrastructure


def _probe(
    id: str,
    outcome: Outcome[Any] | None,
    *,
    ok_detail: str,
    missing: str,
    required: bool = True,
    adapter: str | None = None,
    hosting: str | None = None,
) -> Service:
    if outcome is None:
        return Service(
            id, "down" if required else "disabled", "configuration", missing, required,
            hosting=hosting, fallbacks=NO_FALLBACK,
        )  # fmt: skip
    if outcome.ok and outcome.value is not False:
        status: Status = "healthy"
        detail = ok_detail
    else:
        status, detail = "down", f"Check failed: {outcome.error or 'not ok'}."
    return Service(
        id,
        status,
        "probe",
        detail,
        required,
        checked_at=outcome.checked_at,
        expires_at=outcome.checked_at + PROBE_VALID,
        latency_ms=outcome.latency_ms,
        adapter=adapter,
        hosting=hosting,
        fallbacks=NO_FALLBACK,
    )


@dataclass(frozen=True, slots=True)
class Workers:
    fresh: list[Heartbeat]
    newest: Heartbeat | None  # the freshest row, polling first: its report is used
    report: dict[str, Any] | None  # None: no fresh worker report


def select_workers(inputs: Inputs) -> Workers:
    state = inputs.state.value if inputs.state is not None else None
    rows = [h for h in (state.heartbeats if state else []) if h.service == "worker"]
    fresh = [
        h
        for h in rows
        if inputs.now - h.heartbeat_at <= h.valid_for
        and h.state in ("starting", "warming", "polling")
    ]
    ordered = sorted(fresh, key=lambda h: (h.state == "polling", h.heartbeat_at), reverse=True)
    newest = ordered[0] if ordered else None
    report = newest.report if newest is not None and newest.report.get("build") else None
    return Workers(fresh, newest, report)


def _reported_hosting(section: dict[str, Any] | None) -> str | None:
    """The hosting label a process reported for itself (a string, else None)."""
    value = (section or {}).get("hosting")
    return value if isinstance(value, str) and value else None


def _worker_hosting(report: dict[str, Any] | None) -> str | None:
    """The hosting label the worker reported for itself (``build.hosting``)."""
    return _reported_hosting(_section(report, "build"))


def _in_worker(report: dict[str, Any] | None) -> str:
    """Hosting of a step that runs inside the worker process."""
    where = _worker_hosting(report)
    return f"Study worker ({where})" if where else "Study worker"


def worker_service(inputs: Inputs, workers: Workers) -> Service:
    now = inputs.now
    if inputs.state is None or not inputs.state.ok:
        return Service(
            "worker",
            "unknown",
            "heartbeat",
            "Worker heartbeats could not be read (the database check failed).",
            True,
        )
    polling = [h for h in workers.fresh if h.state == "polling"]
    starting = [h for h in workers.fresh if h.state in ("starting", "warming")]
    pollers = inputs.pollers.value if inputs.pollers is not None and inputs.pollers.ok else None
    poll_note = (
        "Temporal poller check unavailable"
        if pollers is None
        else f"Temporal sees {pollers.workflow} workflow and {pollers.activity} activity "
        f"pollers on {inputs.task_queue}"
    )
    builds = sorted({h.build_sha[:12] for h in workers.fresh}) or ["none"]
    if polling:
        newest = max(polling, key=lambda h: h.heartbeat_at)
        detail = (
            f"{len(polling)} worker(s) polling (build {', '.join(builds)}); last heartbeat "
            f"{_ago(now, newest.heartbeat_at)}. {poll_note}."
        )
        status: Status = "healthy"
        if pollers is None:
            status = "degraded"
        elif pollers.activity == 0 or pollers.workflow == 0:
            status = "degraded"
            detail += " A worker reports polling but Temporal lists no recent poller."
        report = newest.report
        warm = _section(report, "embedder")
        if warm.get("status") == "ready":
            detail += f" Start-up warm-up done ({warm.get('source', 'unknown')})."
        return Service(
            "worker",
            status,
            "heartbeat",
            detail,
            True,
            checked_at=newest.heartbeat_at,
            expires_at=newest.heartbeat_at + newest.valid_for,
            adapter="temporal worker",
            model=f"build {newest.build_sha[:12]}",
            hosting=_worker_hosting(report),
            fallbacks=NO_FALLBACK,
        )
    if starting:
        newest = max(starting, key=lambda h: h.heartbeat_at)
        since = now - newest.started_at
        cold = since <= COLD_START_LIMIT
        return Service(
            "worker",
            "degraded" if cold else "down",
            "heartbeat",
            f"Cold start: {newest.state} for {int(since.total_seconds())} s (loading the "
            f"embedding model before polling); not taking studies yet. {poll_note}."
            + ("" if cold else " Start-up has taken longer than 15 min."),
            True,
            checked_at=newest.heartbeat_at,
            expires_at=newest.heartbeat_at + newest.valid_for,
            adapter="temporal worker",
            model=f"build {newest.build_sha[:12]}",
            hosting=_worker_hosting(newest.report),
            fallbacks=NO_FALLBACK,
        )
    checked_at = inputs.state.checked_at
    rows = inputs.state.value.heartbeats if inputs.state.value is not None else []
    ended = [
        h
        for h in rows
        if h.service == "worker"
        and h.state in ("stopped", "failed")
        and now - h.heartbeat_at <= POLLER_RECENT
    ]
    if ended:
        # Temporal lists a worker's last polls for minutes after it exits: the worker's own
        # final heartbeat explains them.
        last = max(ended, key=lambda h: h.heartbeat_at)
        return Service(
            "worker",
            "down",
            "heartbeat",
            f"The worker {last.state} {_ago(now, last.heartbeat_at)} (build "
            f"{last.build_sha[:12]}) and no worker is polling. {poll_note} (its last polls).",
            True,
            checked_at=last.heartbeat_at,
            expires_at=last.heartbeat_at + POLLER_RECENT,
        )
    if pollers is not None and (pollers.activity or pollers.workflow):
        return Service(
            "worker",
            "degraded",
            "probe",
            f"{poll_note}, but no worker heartbeat is current: a worker without heartbeats "
            f"(an older build, or its writes fail: {E.INF_HEARTBEAT_WRITE}).",
            True,
            checked_at=checked_at,
            expires_at=checked_at + PROBE_VALID,
        )
    return Service(
        "worker",
        "down",
        "heartbeat",
        f"No worker heartbeat is current and no poller is seen. {poll_note}.",
        True,
        checked_at=checked_at,
        expires_at=checked_at + PROBE_VALID,
    )


def relay_service(inputs: Inputs) -> Service:
    if inputs.state is None or not inputs.state.ok or inputs.state.value is None:
        return Service(
            "relay",
            "unknown",
            "heartbeat",
            "Relay heartbeat and outbox backlog could not be read (the database check failed).",
            True,
        )
    state = inputs.state.value
    now = inputs.now
    backlog = state.backlog
    rows = [
        h
        for h in state.heartbeats
        if h.service == "relay" and h.state == "polling" and now - h.heartbeat_at <= h.valid_for
    ]
    oldest = f", oldest due {backlog.oldest_due_s:g} s" if backlog.oldest_due_s is not None else ""
    queue = f"Outbox: {backlog.pending} pending, {backlog.due} due{oldest}, {backlog.dead} dead."
    if not rows:
        return Service(
            "relay",
            "down",
            "heartbeat",
            f"No relay heartbeat is current: saved decisions are not delivered. {queue}",
            True,
            checked_at=inputs.state.checked_at,
            expires_at=inputs.state.checked_at + PROBE_VALID,
        )
    newest = max(rows, key=lambda h: h.heartbeat_at)
    status: Status = "healthy"
    notes = []
    if backlog.dead:
        status = "degraded"
        notes.append(f"{backlog.dead} dead message(s) need an operator ({E.INF_RELAY_DEAD})")
    if backlog.oldest_due_s is not None and backlog.oldest_due_s > BACKLOG_DEGRADED_S:
        status = "degraded"
        notes.append("delivery is behind")
    if newest.report.get("healthy") is False:
        status = "degraded"
        notes.append("the relay reports it has not reached the database recently")
    return Service(
        "relay",
        status,
        "heartbeat",
        f"Relay heartbeat {_ago(now, newest.heartbeat_at)} (build {newest.build_sha[:12]}). "
        f"{queue}" + (f" {_sentence(notes)}." if notes else ""),
        True,
        checked_at=min(newest.heartbeat_at, inputs.state.checked_at),
        expires_at=newest.heartbeat_at + newest.valid_for,
        adapter="outbox relay",
        hosting=_reported_hosting(newest.report),  # the relay reports it at the top level
        fallbacks=NO_FALLBACK,
    )


# ------------------------------------------------------------------------ providers


def _section(report: dict[str, Any] | None, key: str) -> dict[str, Any]:
    value = (report or {}).get(key)
    return value if isinstance(value, dict) else {}


def _routes(report: dict[str, Any]) -> list[dict[str, Any]]:
    routes = report.get("routes")
    return [r for r in routes if isinstance(r, dict)] if isinstance(routes, list) else []


def _probe_entry(report: dict[str, Any], target: str) -> dict[str, Any] | None:
    probes = report.get("probes")
    entry = probes.get(target) if isinstance(probes, dict) else None
    return entry if isinstance(entry, dict) else None


def _probe_valid(report: dict[str, Any]) -> timedelta:
    interval = report.get("probe_interval_s")
    seconds = float(interval) if isinstance(interval, int | float) else 0.0
    return max(MIN_PROBE_VALID, timedelta(seconds=2 * seconds))


def _from_probe(
    id: str,
    report: dict[str, Any],
    target: str,
    *,
    required: bool,
    context: str,
    adapter: str | None,
    model: str | None,
    last: LastInference | None,
    hosting: str | None = None,
    fallbacks: tuple[str, ...] | None = NO_FALLBACK,
    models: tuple[ModelRoute, ...] = (),
) -> Service:
    entry = _probe_entry(report, target)
    if entry is None:
        off = not report.get("probe_interval_s")
        why = (
            "Provider probes are off (OPS_PROVIDER_PROBE_SECONDS=0)."
            if off
            else "Not probed yet (the worker probes shortly after it starts)."
        )
        return Service(
            id, "unknown", "probe", f"{why} {context}".strip(), required,
            adapter=adapter, model=model, last_inference=last,
            hosting=hosting, fallbacks=fallbacks, models=models,
        )  # fmt: skip
    checked = _stamp(entry.get("checked_at"))
    status = entry.get("status") if entry.get("status") in ("healthy", "degraded", "down") else None
    latency = entry.get("latency_ms")
    detail = f"Metadata probe: {entry.get('detail', '')}. {entry.get('establishes', '')} {context}"
    return Service(
        id,
        status or "unknown",
        "probe",
        " ".join(detail.split()),
        required,
        checked_at=checked,
        expires_at=None if checked is None else checked + _probe_valid(report),
        latency_ms=float(latency) if isinstance(latency, int | float) else None,
        adapter=adapter,
        model=model,
        last_inference=last,
        hosting=hosting,
        fallbacks=fallbacks,
        models=models,
    )


def _last(
    ledger: Ledger | None,
    *,
    providers: tuple[str, ...] | None = None,
    kinds: tuple[str, ...] | None = None,
    activities: tuple[str, ...] | None = None,
) -> LastInference | None:
    if ledger is None:
        return None
    call = ledger.last(providers=providers, kinds=kinds, activities=activities)
    return None if call is None else LastInference(call.at, call.model, call.kind)


def _last_note(now: datetime, last: LastInference | None) -> str:
    if last is None:
        return "No successful paid call is recorded."
    return f"Last successful {last.kind} call: {last.model}, {_ago(now, last.at)}."


# Without a worker report these are not known to be used: not required.
OPTIONAL_UNLESS_CONFIGURED: Final = frozenset({"azure_di", "sam3", "mask2former"})
NO_WORKER: Final = "No current worker heartbeat: the worker's effective configuration is unknown."


def provider_service(
    id: str, inputs: Inputs, report: dict[str, Any] | None, ledger: Ledger | None
) -> Service:
    ledger_name = PROVIDER_LEDGER_NAME[id]
    last = _last(ledger, providers=(ledger_name,), kinds=("chat",))
    hosting = PROVIDER_HOSTING[id]
    if report is None:
        return Service(
            id, "unknown", "heartbeat", NO_WORKER, False, last_inference=last, hosting=hosting
        )
    configured = report.get("providers_configured") or []
    routes = _routes(report)
    routed = [
        (r["model"], p.get("target"), i == 0)
        for r in routes
        for i, p in enumerate(r.get("route") or [])
        if isinstance(p, dict) and p.get("service") == id
    ]
    if id not in configured:
        return Service(
            id,
            "disabled",
            "configuration",
            "Not configured in the worker; no pipeline model is routed to it.",
            False,
            last_inference=last,
            hosting=hosting,
            fallbacks=NO_FALLBACK,
        )
    primary = sorted({m for m, _, first in routed if first})
    models = ", ".join(
        dict.fromkeys(f"{m} → {t}" if t and t != m else str(m) for m, t, _ in routed)
    )
    role = (
        f"Primary for {', '.join(primary)}."
        if primary
        else "Fallback only (no pipeline model has it first)."
    )
    # The routes this provider serves first, and what each falls back to (section 2).
    served = tuple(
        route
        for route in _model_routes(report)
        if route.primary is not None and route.primary.service == id
    )
    fallbacks = tuple(dict.fromkeys(label for route in served for label in route.fallback_labels))
    return _from_probe(
        id,
        report,
        id,
        required=bool(primary),
        context=f"{role} {_last_note(inputs.now, last)}",
        adapter=PROVIDER_ADAPTER[id],
        model=models or None,
        last=last,
        hosting=hosting,
        fallbacks=fallbacks,
        models=served,
    )


def replicate_service(
    inputs: Inputs, report: dict[str, Any] | None, ledger: Ledger | None
) -> Service:
    last = _last(ledger, providers=("replicate",))
    if report is None:
        return Service(
            "replicate", "unknown", "heartbeat", NO_WORKER, False,
            last_inference=last, hosting="Replicate",
        )  # fmt: skip
    uses = [
        name
        for name, key in (("SAM 3", "localizer"), ("Mask2Former", "segmenter"))
        if isinstance(report.get(key), dict) and report[key].get("adapter") == "replicate"
    ]
    if not uses:
        return Service(
            "replicate",
            "disabled",
            "configuration",
            "Not used: neither SAM 3 nor Mask2Former runs on Replicate in this worker.",
            False,
            last_inference=last,
            hosting="Replicate",
            fallbacks=NO_FALLBACK,
        )
    return _from_probe(
        "replicate",
        report,
        "replicate",
        required=False,
        context=f"Hosts {' and '.join(uses)}. {_last_note(inputs.now, last)}",
        adapter="Replicate API",
        model=None,
        last=last,
        hosting="Replicate",
    )


# ------------------------------------------------------------------------ capabilities


def aggregate(
    id: str,
    deps: Sequence[tuple[Service, bool]],
    *,
    required: bool,
    detail: str,
    adapter: str | None = None,
    model: str | None = None,
    last: LastInference | None = None,
    hosting: str | None = None,
    fallbacks: tuple[str, ...] | None = None,
    models: tuple[ModelRoute, ...] = (),
) -> Service:
    """A capability from the services it uses: ``(service, required_for_it)``. Its backups
    are its models' fallback routes (``None``: not known; the routes' union otherwise)."""
    status: Status = "healthy"
    problems = []
    for dep, needed in deps:
        if dep.status in ("healthy",):
            continue
        if dep.status == "disabled" and not needed:
            continue
        problems.append(f"{dep.id} {dep.status}")
        if needed:
            effect: Status = {"down": "down", "degraded": "degraded"}.get(dep.status, "unknown")  # type: ignore[assignment]
        else:
            effect = "degraded"
        if _BAD[effect] > _BAD[status]:
            status = effect
    times = [d.checked_at for d, _ in deps if d.checked_at is not None]
    expiries = [d.expires_at for d, _ in deps if d.expires_at is not None]
    kinds = {d.check_kind for d, _ in deps}
    text = detail + (f" Needs attention: {', '.join(problems)}." if problems else "")
    return Service(
        id,
        status,
        "probe" if "probe" in kinds else "heartbeat",
        text,
        required,
        checked_at=min(times) if times else None,
        expires_at=min(expiries) if expiries else None,
        adapter=adapter,
        model=model,
        last_inference=last,
        hosting=hosting,
        fallbacks=(
            tuple(dict.fromkeys(label for r in models for label in r.fallback_labels))
            if fallbacks is None and models
            else fallbacks
        ),
        models=models,
    )


def _route_for(report: dict[str, Any], setting: str) -> dict[str, Any] | None:
    for route in _routes(report):
        if setting in (route.get("settings") or []):
            return route
    return None


def _hops(route: dict[str, Any]) -> list[RouteHop]:
    return [
        RouteHop(str(p.get("provider")), str(p.get("service")), str(p.get("target")))
        for p in route.get("route") or []
        if isinstance(p, dict)
    ]


def _model_routes(report: dict[str, Any]) -> list[ModelRoute]:
    """Every model the worker reported, once per setting that names it, in pipeline order."""
    routes: list[ModelRoute] = []
    for route in _routes(report):
        hops = _hops(route)
        for setting in route.get("settings") or []:
            routes.append(
                ModelRoute(
                    str(setting),
                    str(route.get("model")),
                    hops[0] if hops else None,
                    tuple(hops[1:]),
                )
            )
    return routes


def _model_dep(
    report: dict[str, Any], setting: str, by_id: dict[str, Service]
) -> tuple[Service, str, ModelRoute] | None:
    """The primary provider service of the model ``setting`` names, the route text, and the
    structured route."""
    route = _route_for(report, setting)
    if route is None:
        return None
    hops = _hops(route)
    model = str(route.get("model"))
    structured = ModelRoute(setting, model, hops[0] if hops else None, tuple(hops[1:]))
    if not hops:
        return (
            Service(
                f"{setting.lower()}", "down", "configuration", f"no provider serves {model}", True
            ),
            f"{model} (no provider)",
            structured,
        )
    first = hops[0]
    service = by_id.get(first.service)
    text = f"{model} via {first.provider}" + (
        f" ({first.target})" if first.target and first.target != model else ""
    )
    if service is None:
        return None
    return service, text, structured


def _uses(
    report: dict[str, Any], settings: Iterable[str], by_id: dict[str, Service]
) -> tuple[list[tuple[Service, bool]], list[str], tuple[ModelRoute, ...]]:
    deps: list[tuple[Service, bool]] = []
    texts: list[str] = []
    routes: list[ModelRoute] = []
    for setting in settings:
        found = _model_dep(report, setting, by_id)
        if found is None:
            continue
        service, text, route = found
        if all(service is not d for d, _ in deps):
            deps.append((service, True))
        texts.append(text)
        routes.append(route)
    return deps, texts, tuple(routes)


def compose(inputs: Inputs) -> list[Service]:
    now = inputs.now
    by_id: dict[str, Service] = {}

    workers = select_workers(inputs)
    # A known status needs a time: "disabled" is read from the worker's report (or, for
    # the API's own settings, now), so it carries that report's time and validity.
    if workers.newest is not None:
        read_at, read_until = (
            workers.newest.heartbeat_at,
            workers.newest.heartbeat_at + workers.newest.valid_for,
        )
    else:
        read_at, read_until = now, now + PROBE_VALID

    def add(service: Service) -> Service:
        if service.status == "disabled" and service.checked_at is None:
            service = replace(service, checked_at=read_at, expires_at=read_until)
        service = expire(service, now)
        by_id[service.id] = service
        return service

    add(
        Service(
            "api",
            "healthy",
            "probe",
            f"This API process answered (build {inputs.api_build[:12]}, {inputs.api_env}).",
            True,
            checked_at=now,
            expires_at=now + PROBE_VALID,
            adapter="FastAPI",
            hosting=inputs.api_hosting,
            fallbacks=NO_FALLBACK,
        )
    )
    add(
        _probe(
            "db",
            inputs.db,
            ok_detail="SELECT 1 on a pooled connection answered.",
            missing="No database configured in the API.",
            adapter="PostgreSQL",
            hosting=inputs.db_hosting,
        )
    )
    add(
        _probe(
            "temporal",
            inputs.temporal,
            ok_detail="DescribeNamespace answered. This does not verify workers or the relay.",
            missing="Temporal is not configured in the API: studies cannot start.",
            adapter="Temporal",
            hosting=inputs.temporal_hosting,
        )
    )
    report = workers.report
    ledger = inputs.ledger.value if inputs.ledger is not None and inputs.ledger.ok else None
    worker = add(worker_service(inputs, workers))
    add(relay_service(inputs))
    storage = add(
        _probe(
            "storage",
            inputs.storage,
            ok_detail="Metadata lookup of a reserved non-client object name "
            f"{inputs.storage.value or ''}. No study's files were read.",
            missing="",
            adapter=inputs.storage_adapter,
            hosting=inputs.storage_hosting,
        )
    )
    add(
        Service(
            "firebase",
            "healthy",
            "probe",
            "This request's ID token was verified "
            + (f"for project {inputs.firebase_project}" if inputs.firebase_project else "")
            + ". No user identity is reported.",
            True,
            checked_at=inputs.token_verified_at,
            expires_at=inputs.token_verified_at + PROBE_VALID,
            adapter=inputs.firebase_adapter,
            hosting=inputs.firebase_hosting,
            fallbacks=NO_FALLBACK,
        )
    )
    for id in ("openai", "azure_openai", "anthropic"):
        add(provider_service(id, inputs, report, ledger))
    add(replicate_service(inputs, report, ledger))

    if report is None:
        for id in SERVICE_IDS:
            if id not in by_id and id not in ("retrieval", "cost_indices"):
                add(
                    Service(
                        id,
                        "unknown",
                        "heartbeat",
                        NO_WORKER,
                        id not in OPTIONAL_UNLESS_CONFIGURED,
                    )
                )
        _data_services(inputs, report, by_id, add)
        return [by_id[id] for id in SERVICE_IDS]

    steps = _section(report, "steps")
    alive = (worker, True)

    # Azure Document Intelligence (optional: the text layer reads the appraisal without it).
    di_last = _last(ledger, kinds=("document_analysis",))
    if report.get("azure_di"):
        add(
            _from_probe(
                "azure_di",
                report,
                "azure_di",
                required=False,
                context=_last_note(now, di_last),
                adapter="prebuilt-layout",
                model=None,
                last=di_last,
                hosting="Microsoft Azure",
            )
        )
    else:
        add(
            Service(
                "azure_di",
                "disabled",
                "configuration",
                "Not configured in the worker: appraisals are read from the PDF text layer "
                "and the grounded reading cannot run (it needs page geometry).",
                False,
                last_inference=di_last,
                hosting="Microsoft Azure",
                fallbacks=NO_FALLBACK,
            )
        )

    # Appraisal extraction.
    grounded = steps.get("extraction_method") == "grounded"
    settings = (["EXTRACTION_GROUNDED_MODEL"] if grounded else []) + ["TEXT_MODEL"]
    deps, texts, routes = _uses(report, settings, by_id)
    if grounded and deps and report.get("routes"):
        # The text model is the fallback of a failed grounded reading: optional then.
        deps = [(d, i == 0) for i, (d, _) in enumerate(deps)]
    add(
        aggregate(
            "appraisal",
            [alive, *deps, (by_id["azure_di"], False)],
            required=True,
            detail="PDF / UAD 3.6 reading: "
            + ("grounded call, the pipeline when it cannot run. " if grounded else "pipeline. ")
            + _last_note(
                now,
                _last(ledger, kinds=("chat",), activities=("ingest_appraisal", "extract_fields")),
            ),
            adapter=str(steps.get("extraction_method")),
            model=" · ".join(texts) or None,
            last=_last(ledger, kinds=("chat",), activities=("ingest_appraisal", "extract_fields")),
            hosting=_in_worker(report),
            models=routes,
        )
    )

    # Photo analysis.
    vision_settings = ["VISION_MODEL", "VISION_SURFACES_MODEL"]
    if steps.get("count_fusion_active"):
        vision_settings.append("VISION_FUSION_MODEL")
    deps, texts, routes = _uses(report, vision_settings, by_id)
    vision_last = _last(ledger, kinds=("chat",), activities=("analyze_photo",))
    add(
        aggregate(
            "vision",
            [alive, *deps],
            required=True,
            detail=f"Per-photo analysis ({steps.get('vision_photo_mode')}"
            + (", per-class count fusion" if steps.get("count_fusion_active") else "")
            + f"). {_last_note(now, vision_last)}",
            adapter=str(steps.get("vision_photo_mode"))
            + (" + count fusion" if steps.get("count_fusion_active") else ""),
            model=" · ".join(texts) or None,
            last=vision_last,
            hosting=_in_worker(report),
            models=routes,
        )
    )

    # SAM 3.
    localizer = _section(report, "localizer")
    sam_last = _last(ledger, kinds=("localization",))
    sam_required = bool(steps.get("rooms_gate_waits_for_localization"))
    adapter = localizer.get("adapter")
    if adapter is None:
        add(
            Service(
                "sam3",
                "disabled",
                "configuration",
                "Neither LOCALIZER_URL nor REPLICATE_API_TOKEN is set in the worker: object "
                "outlines are skipped.",
                False,
                last_inference=sam_last,
                fallbacks=NO_FALLBACK,
            )
        )
    elif adapter == "self-hosted":
        context = (
            "Self-hosted Cog server. "
            + ("" if localizer.get("probed") else "Not probed: a request would start a GPU "
               "instance billed per second (set OPS_PROBE_LOCALIZER=true to probe). ")
            + _last_note(now, sam_last)
        )  # fmt: skip
        own = _from_probe(
            "sam3", report, "sam3", required=sam_required, context=context,
            adapter="self-hosted", model="SAM 3", last=sam_last,
            hosting=localizer.get("hosting") or "Self-hosted server",
        )  # fmt: skip
        if not localizer.get("probed"):
            own = replace(own, detail=" ".join(context.split()))
        add(aggregate("sam3", [alive, (own, True)], required=sam_required, detail=own.detail,
                      adapter="self-hosted", model="SAM 3", last=sam_last,
                      hosting=own.hosting, fallbacks=NO_FALLBACK))  # fmt: skip
    else:
        version = _from_probe(
            "sam3", report, "sam3", required=sam_required,
            context=f"On Replicate (vufinder/sam3@{localizer.get('version')}). "
            + _last_note(now, sam_last),
            adapter="replicate", model=f"SAM 3 (vufinder/sam3@{localizer.get('version')})",
            last=sam_last, hosting="Replicate",
        )  # fmt: skip
        add(
            aggregate(
                "sam3",
                [alive, (by_id["replicate"], True), (version, True)],
                required=sam_required,
                detail=version.detail,
                adapter="replicate",
                model=version.model,
                last=sam_last,
                hosting="Replicate",
                fallbacks=NO_FALLBACK,
            )
        )

    # Mask2Former (optional surface crops).
    segmenter = _section(report, "segmenter")
    seg_last = _last(ledger, kinds=("segmentation",))
    seg_adapter = segmenter.get("adapter")
    if seg_adapter is None:
        add(
            Service(
                "mask2former",
                "disabled",
                "configuration",
                "Not used: MATERIALS_SECOND_LOOK is not surface_crops (or the room step is off).",
                False,
                last_inference=seg_last,
                fallbacks=NO_FALLBACK,
            )
        )
    elif seg_adapter == "local":
        newest = workers.newest
        loaded = segmenter.get("loaded") is True
        add(
            Service(
                "mask2former",
                "healthy" if loaded else "unknown",
                "heartbeat",
                "Local checkpoint in the worker: "
                + ("loaded in memory." if loaded else "loads on first use; not loaded yet.")
                + f" {_last_note(now, seg_last)}",
                False,
                checked_at=newest.heartbeat_at if newest else None,
                expires_at=newest.heartbeat_at + newest.valid_for if newest else None,
                adapter="local",
                model="Mask2Former Swin-L ADE20K",
                last_inference=seg_last,
                hosting=_in_worker(report),
                fallbacks=NO_FALLBACK,
            )
        )
    else:
        version = _from_probe(
            "mask2former", report, "mask2former", required=False,
            context=_last_note(now, seg_last), adapter="replicate",
            model="Mask2Former (oitoito/mask2former)", last=seg_last, hosting="Replicate",
        )  # fmt: skip
        add(aggregate("mask2former", [alive, (by_id["replicate"], True), (version, True)],
                      required=False, detail=version.detail, adapter="replicate",
                      model=version.model, last=seg_last, hosting="Replicate",
                      fallbacks=NO_FALLBACK))  # fmt: skip

    # Embeddings.
    embedder = _section(report, "embedder")
    newest = workers.newest
    beat = (newest.heartbeat_at, newest.heartbeat_at + newest.valid_for) if newest else (None, None)
    name = str(embedder.get("model") or "")
    revision = embedder.get("revision")
    model_text = name + (f"@{str(revision)[:8]}" if revision else "")
    emb_status = embedder.get("status")
    if emb_status == "ready":
        source = embedder.get("source")
        how = (
            "needs no warm-up (test embedder)"
            if source == "none_needed"
            else f"loaded from the {source} in {embedder.get('seconds')} s"
        )
        add(
            Service(
                "embeddings", "healthy", "heartbeat",
                f"Warm in the worker: {how}; the worker heartbeat says it is still running.",
                True, checked_at=beat[0], expires_at=beat[1],
                adapter="hash" if name.startswith("hash") else "sentence-transformers (CPU)",
                model=model_text or None, hosting=_in_worker(report), fallbacks=NO_FALLBACK,
            )
        )  # fmt: skip
    elif emb_status == "warming":
        add(Service("embeddings", "degraded", "heartbeat", "Warming: the worker is loading the "
                    "model and is not polling yet.", True, checked_at=beat[0], expires_at=beat[1],
                    model=model_text or None, hosting=_in_worker(report),
                    fallbacks=NO_FALLBACK))  # fmt: skip
    elif emb_status == "failed":
        add(Service("embeddings", "down", "heartbeat", "The model failed to load; the worker "
                    "will not start.", True, checked_at=beat[0], expires_at=beat[1],
                    model=model_text or None, hosting=_in_worker(report),
                    fallbacks=NO_FALLBACK))  # fmt: skip
    else:
        add(Service("embeddings", "unknown", "heartbeat", "Warm-up not reported yet.", True,
                    model=model_text or None))  # fmt: skip

    _data_services(inputs, report, by_id, add)

    # Room inventory.
    if not steps.get("room_reconcile"):
        add(Service("room_inventory", "disabled", "configuration", "ROOM_RECONCILE is off: assets "
                    "come from the per-photo components.", False,
                    hosting=_in_worker(report), fallbacks=NO_FALLBACK))  # fmt: skip
    else:
        deps, texts, routes = _uses(
            report,
            ["ROOM_MODEL", "ROOM_MATERIALS_MODEL", "ROOM_CALL_MODEL", "SURFACE_CROP_MODEL"],
            by_id,
        )
        extra: list[tuple[Service, bool]] = []
        if steps.get("materials_second_look") == "surface_crops":
            extra.append((by_id["mask2former"], False))
        room_last = _last(ledger, kinds=("chat",), activities=("reconcile_rooms",))
        add(
            aggregate(
                "room_inventory",
                [alive, *deps, *extra],
                required=True,
                detail=f"Room step {steps.get('room_step_mode')}, second look "
                f"{steps.get('room_materials')}, materials {steps.get('materials_second_look')}. "
                + _last_note(now, room_last),
                adapter=str(steps.get("room_step_mode")),
                model=" · ".join(texts) or None,
                last=room_last,
                hosting=_in_worker(report),
                models=routes,
            )
        )

    # Classification.
    deps, texts, routes = _uses(report, ["CLASSIFY_MODEL"], by_id)
    class_last = _last(ledger, kinds=("chat",), activities=("process_assets",))
    add(
        aggregate(
            "classification",
            [alive, *deps, (by_id["retrieval"], True), (by_id["embeddings"], True)],
            required=True,
            detail="IRS rules and firm conventions first, the model for the rest, citations "
            f"from the reference corpus. {_last_note(now, class_last)}",
            adapter=f"rules + model ({steps.get('retrieval_search')} search)",
            model=" · ".join(texts) or None,
            last=class_last,
            hosting=_in_worker(report),
            models=routes,
        )
    )
    add(
        aggregate(
            "takeoff",
            [alive],
            required=True,
            detail=f"Deterministic step in the worker (TAKEOFF_AREAS={steps.get('takeoff_areas')});"
            " no external dependency, so the evidence is the worker heartbeat.",
            adapter=str(steps.get("takeoff_areas")),
            hosting=_in_worker(report),
            fallbacks=NO_FALLBACK,
        )
    )
    if steps.get("structure_step") == "off":
        add(Service("structure", "disabled", "configuration", "STRUCTURE_STEP is off: the "
                    "schedule comes from the photographs only.", False,
                    hosting=_in_worker(report), fallbacks=NO_FALLBACK))  # fmt: skip
    else:
        add(
            aggregate(
                "structure",
                [alive, (by_id["cost_indices"], False)],
                required=True,
                detail="Concealed building systems from the RSMeans square-foot model or the "
                "appraisal's cost approach, in the worker.",
                adapter="square-foot model",
                hosting=_in_worker(report),
                fallbacks=NO_FALLBACK,
            )
        )
    add(
        aggregate(
            "exports",
            [alive, (storage, True), (by_id["db"], True)],
            required=True,
            detail="The approved revision to JSON and CSV in storage, written by the worker.",
            adapter="JSON / CSV",
            hosting=_in_worker(report),
            fallbacks=NO_FALLBACK,
        )
    )
    return [by_id[id] for id in SERVICE_IDS]


def _store_hosting(inputs: Inputs) -> str:
    """A store in the database: PostgreSQL, where the API established it runs."""
    return f"PostgreSQL ({inputs.db_hosting})" if inputs.db_hosting else "PostgreSQL"


def _data_services(
    inputs: Inputs, report: dict[str, Any] | None, by_id: dict[str, Service], add: Any
) -> None:
    """``retrieval``, ``rsmeans`` and ``cost_indices``: reference data, not providers."""
    now = inputs.now
    state_ok = inputs.state is not None and inputs.state.ok and inputs.state.value is not None
    checked = inputs.state.checked_at if inputs.state is not None else None
    expires = checked + PROBE_VALID if checked is not None else None
    if not state_ok or inputs.state is None or inputs.state.value is None:
        for id in ("retrieval", "cost_indices"):
            add(Service(id, "unknown", "probe", "The store could not be read (database).", True))
    else:
        state = inputs.state.value
        reference = state.reference
        warmed = (report or {}).get("reference_index") or {}
        embedder = ((report or {}).get("embedder") or {}).get("model")
        if reference is None:
            add(
                Service(
                    "retrieval", "degraded", "probe",
                    "No published reference corpus: rule citations cannot be resolved and the "
                    "classifier's search finds nothing. Missing reference data, not a provider "
                    "outage: ingest and publish the reference corpus.",
                    True, checked_at=checked, expires_at=expires,
                    hosting=_store_hosting(inputs), fallbacks=NO_FALLBACK,
                )
            )  # fmt: skip
        else:
            notes = []
            status: Status = "healthy"
            if embedder and embedder != reference.embedding_model:
                status = "degraded"
                notes.append(
                    f"the corpus was embedded with {reference.embedding_model}, the worker "
                    f"runs {embedder}"
                )
            if warmed.get("status") == "failed":
                status = "degraded"
                notes.append("the worker could not warm its BM25 index (built on first use)")
            add(
                Service(
                    "retrieval", status, "probe",
                    f"Reference corpus version {reference.version} published "
                    f"{_ago(now, reference.published_at)}."
                    + (f" {_sentence(notes)}." if notes else ""),
                    True, checked_at=checked, expires_at=expires,
                    adapter=str(_section(report, "steps").get("retrieval_search") or "bm25"),
                    model=reference.embedding_model, hosting=_store_hosting(inputs),
                    fallbacks=NO_FALLBACK,
                )
            )  # fmt: skip
        cost = state.cost_indices
        failed = [r for r in cost.latest_runs if r.get("status") == "failed"]
        year = now.year
        if cost.values == 0 or cost.latest_year is None:
            add(
                Service(
                    "cost_indices", "down", "probe",
                    f"The cost index store is empty: run `{REFRESH_CLI} seed`. Missing data, "
                    "not a provider outage.", True, checked_at=checked, expires_at=expires,
                    hosting=_store_hosting(inputs), fallbacks=NO_FALLBACK,
                )
            )  # fmt: skip
        else:
            status = "healthy"
            notes = [f"Latest historical index {cost.latest_year} ({cost.values} values)"]
            if cost.latest_year < year - 1:
                status = "degraded"
                notes.append(
                    f"stale: studies placed in service in {cost.latest_year + 1}-{year} are "
                    f"priced at the {cost.latest_year} index and flagged; run `{REFRESH_CLI}` "
                    "or enter the published value"
                )
            if failed:
                status = "degraded"
                notes.append(
                    "last run failed for " + ", ".join(str(r.get("source")) for r in failed)
                )
            add(
                Service(
                    "cost_indices", status, "probe", "; ".join(notes) + ".", True,
                    checked_at=checked, expires_at=expires, adapter="cost index store",
                    model="RSMeans Historical Cost Index", hosting=_store_hosting(inputs),
                    fallbacks=NO_FALLBACK,
                )
            )  # fmt: skip
    if report is None:
        return
    table = report.get("cost_table")
    newest = select_workers(inputs).newest
    beat_at = newest.heartbeat_at if newest else None
    beat_until = newest.heartbeat_at + newest.valid_for if newest else None
    if isinstance(table, dict):
        add(
            Service(
                "rsmeans", "healthy", "heartbeat",
                f"Licensed unit-cost table loaded in the worker: {table.get('name')}, "
                f"{table.get('rows')} rows, base year {table.get('base_year')}.",
                True, checked_at=beat_at, expires_at=beat_until, adapter="cost table",
                model=str(table.get("name")), hosting=_in_worker(report),
                fallbacks=NO_FALLBACK,
            )
        )  # fmt: skip
    else:
        add(
            Service(
                "rsmeans", "degraded", "heartbeat",
                "No licensed RSMeans unit-cost table in the worker (COST_TABLE_PATH unset): "
                "only the six legacy rows price assets. Missing licensed data, not a provider "
                "outage.", True, checked_at=beat_at, expires_at=beat_until,
                hosting=_in_worker(report), fallbacks=NO_FALLBACK,
            )
        )  # fmt: skip


def overall_failed(services: Sequence[Service]) -> bool:
    """503: a required service is down."""
    return any(s.required and s.status == "down" for s in services)
