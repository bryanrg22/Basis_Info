"""The service list of GET /v1/ops/health (basis.ops.services), composed from fabricated
evidence: every contract case (missing credentials, disabled adapters, expired evidence,
partial outages, relay backlog, worker cold start, malformed answers, missing data)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

from basis.ops.evidence import (
    Backlog,
    CostIndexState,
    Heartbeat,
    LastCall,
    Ledger,
    Outcome,
    Pollers,
    Reference,
    State,
)
from basis.ops.services import (
    SERVICE_IDS,
    Inputs,
    Service,
    compose,
    expire,
    overall_failed,
)

NOW = datetime(2026, 10, 31, 15, 0, tzinfo=UTC)
EMBEDDER = "sentence-transformers/all-MiniLM-L6-v2"


def ok(value: Any = True, at: datetime = NOW) -> Outcome[Any]:
    return Outcome(value, None, at, 1.5)


def failed(error: str = "timed out after 2 s") -> Outcome[Any]:
    return Outcome(None, error, NOW, 2000.0)


def probe(status: str = "healthy", age_s: float = 10, detail: str = "answered") -> dict[str, Any]:
    return {
        "status": status,
        "checked_at": (NOW - timedelta(seconds=age_s)).isoformat(),
        "latency_ms": 120.0,
        "detail": f"model: {detail}",
        "establishes": "The key is accepted. Does not prove quota or inference.",
        "parts": [],
    }


def route(*hops: tuple[str, str, str]) -> list[dict[str, str]]:
    return [{"provider": p, "service": s, "target": t} for p, s, t in hops]


AZURE = ("azure", "azure_openai")
OPENAI = ("openai", "openai")
CLAUDE = ("anthropic", "anthropic")


def worker_report() -> dict[str, Any]:
    """A production worker: Azure first for GPT models, Claude for fusion, materials and the
    classifier, SAM 3 on Replicate, no surface crops."""
    return {
        "build": {
            "git_sha": "9c430aa0f00d",
            "build_id": "9c430aa0f00d",
            "task_queue": "basis-studies",
        },
        "routes": [
            {
                "model": "gpt-6.1-sol",
                "settings": ["VISION_MODEL", "EXTRACTION_GROUNDED_MODEL"],
                "route": route((*AZURE, "basis-vision"), (*OPENAI, "gpt-6.1-sol")),
            },
            {
                "model": "gpt-6-luna",
                "settings": ["VISION_SURFACES_MODEL", "ROOM_MODEL", "TEXT_MODEL"],
                "route": route((*AZURE, "basis-default"), (*OPENAI, "gpt-6-luna")),
            },
            {
                "model": "claude-opus-5-5",
                "settings": ["VISION_FUSION_MODEL"],
                "route": route((*CLAUDE, "claude-opus-5-5")),
            },
            {
                "model": "claude-sonnet-5-5",
                "settings": ["ROOM_MATERIALS_MODEL", "CLASSIFY_MODEL"],
                "route": route((*CLAUDE, "claude-sonnet-5-5")),
            },
        ],
        "providers_configured": ["anthropic", "azure_openai", "openai"],
        "steps": {
            "extraction_method": "grounded",
            "vision_photo_mode": "decomposed",
            "count_fusion_active": True,
            "room_reconcile": True,
            "room_step_mode": "reconcile",
            "room_materials": "sensitive",
            "materials_second_look": "per_photo",
            "rooms_gate_waits_for_localization": False,
            "structure_step": "on",
            "takeoff_areas": "appraisal",
            "retrieval_search": "bm25",
        },
        "azure_di": True,
        "localizer": {"adapter": "replicate", "version": "1bf97763d5df", "probed": False},
        "segmenter": {"adapter": None, "loaded": None},
        "cost_table": {"name": "RSMeans 2026", "rows": 812, "base_year": 2026},
        "embedder": {
            "status": "ready",
            "model": EMBEDDER,
            "revision": "c9745ed1",
            "source": "cache",
            "seconds": 4.1,
        },
        "reference_index": {"status": "ready"},
        "probes": {
            name: probe()
            for name in ("openai", "azure_openai", "anthropic", "replicate", "sam3", "azure_di")
        },
        "probe_interval_s": 60,
        "heartbeat_interval_s": 15,
    }


def beat(
    service: str = "worker",
    state: str = "polling",
    age_s: float = 5,
    started_s: float = 3600,
    report: dict[str, Any] | None = None,
) -> Heartbeat:
    if report is None:
        report = (
            worker_report()
            if service == "worker"
            else {"heartbeat_interval_s": 15, "healthy": True}
        )
    return Heartbeat(
        service=service,
        instance_id="00000000-0000-0000-0000-000000000001",
        build_sha="9c430aa0f00d1234",
        state=state,
        started_at=NOW - timedelta(seconds=started_s),
        heartbeat_at=NOW - timedelta(seconds=age_s),
        report=report,
    )


def world(
    *,
    heartbeats: list[Heartbeat] | None = None,
    backlog: Backlog | None = None,
    reference: Reference | None | str = "default",
    cost: CostIndexState | None = None,
) -> State:
    return State(
        heartbeats=[beat(), beat("relay")] if heartbeats is None else heartbeats,
        backlog=backlog or Backlog(pending=0, due=0, oldest_due_s=None, dead=0),
        reference=Reference(3, EMBEDDER, NOW - timedelta(days=2))
        if reference == "default"
        else reference,  # type: ignore[arg-type]
        cost_indices=cost or CostIndexState(values=600, latest_year=2025, latest_runs=[]),
    )


def inputs(**overrides: Any) -> Inputs:
    values: dict[str, Any] = {
        "now": NOW,
        "api_build": "9c430aa0f00d1234",
        "api_env": "dev",
        "db": ok(),
        "temporal": ok(),
        "pollers": ok(Pollers(workflow=1, activity=1)),
        "state": ok(world()),
        "ledger": ok(Ledger()),
        "storage": ok("answered: the reserved probe object is absent, as expected"),
        "storage_adapter": "local",
        "firebase_adapter": "emulator",
        "firebase_project": "demo-basis",
        "token_verified_at": NOW,
        "task_queue": "basis-studies",
        **overrides,
    }
    return Inputs(**values)


def with_report(change: Any) -> Inputs:
    report = worker_report()
    change(report)
    return inputs(state=ok(world(heartbeats=[beat(report=report), beat("relay")])))


def by_id(services: list[Service]) -> dict[str, Service]:
    return {s.id: s for s in services}


def statuses(services: list[Service]) -> dict[str, str]:
    return {s.id: s.status for s in services}


# ------------------------------------------------------------------------ baseline


def test_every_stable_id_once_in_order_and_all_green() -> None:
    services = compose(inputs())
    assert [s.id for s in services] == list(SERVICE_IDS)
    assert len(SERVICE_IDS) == 25
    got = statuses(services)
    assert got.pop("mask2former") == "disabled"
    assert set(got.values()) == {"healthy"}, got
    assert not overall_failed(services)
    for s in services:
        # Every known status, "disabled" included, has the time of its evidence.
        assert s.checked_at is not None and s.expires_at is not None, s.id
        if s.status != "disabled":
            assert s.check_kind != "configuration", s.id
    assert by_id(services)["mask2former"].checked_at == NOW - timedelta(seconds=5)


def test_runtime_models_and_routes_come_from_the_worker_report() -> None:
    s = by_id(compose(inputs()))
    assert "gpt-6.1-sol via azure (basis-vision)" in (s["vision"].model or "")
    assert "claude-opus-5-5 via anthropic" in (s["vision"].model or "")
    assert s["azure_openai"].model == "gpt-6.1-sol → basis-vision, gpt-6-luna → basis-default"
    assert s["azure_openai"].required and s["anthropic"].required
    # OpenAI is only the fallback of the Azure models: not required.
    assert not s["openai"].required and "Fallback only" in s["openai"].detail
    assert s["sam3"].adapter == "replicate" and "vufinder/sam3@1bf97763d5df" in (
        s["sam3"].model or ""
    )
    assert s["worker"].model == "build 9c430aa0f00d"
    assert s["embeddings"].model == f"{EMBEDDER}@c9745ed1"
    assert s["appraisal"].adapter == "grounded"
    assert s["vision"].adapter == "decomposed + count fusion"


def test_a_probe_says_what_it_does_not_establish() -> None:
    s = by_id(compose(inputs()))
    assert "Does not prove quota or inference" in s["anthropic"].detail
    assert s["anthropic"].check_kind == "probe"


# ------------------------------------------------------------------------ credentials


def test_missing_credentials_disable_providers_and_fail_what_needs_them() -> None:
    def strip(report: dict[str, Any]) -> None:
        report["providers_configured"] = []
        for r in report["routes"]:
            r["route"] = []
        report["probes"] = {}

    services = compose(with_report(strip))
    s = by_id(services)
    for id in ("openai", "azure_openai", "anthropic"):
        assert s[id].status == "disabled" and not s[id].required
    assert s["vision"].status == "down"
    assert "no provider serves gpt-6.1-sol" in s["vision"].detail.replace(" down", "") or (
        "vision_model down" in s["vision"].detail
    )
    assert s["classification"].status == "down"
    assert overall_failed(services)


def test_credentials_alone_never_make_a_provider_healthy() -> None:
    def unprobed(report: dict[str, Any]) -> None:
        report["probes"] = {}

    s = by_id(compose(with_report(unprobed)))
    assert s["openai"].status == "unknown" and "Not probed yet" in s["openai"].detail
    assert s["vision"].status == "unknown"


def test_probes_turned_off_are_unknown_with_the_reason() -> None:
    def off(report: dict[str, Any]) -> None:
        report["probes"] = {}
        report["probe_interval_s"] = 0

    s = by_id(compose(with_report(off)))
    assert "OPS_PROVIDER_PROBE_SECONDS=0" in s["anthropic"].detail


# ------------------------------------------------------------------------ disabled adapters


def test_disabled_optional_adapters_do_not_degrade_anything() -> None:
    def minimal(report: dict[str, Any]) -> None:
        report["localizer"] = {"adapter": None}
        report["segmenter"] = {"adapter": None}
        report["azure_di"] = False
        report["steps"]["room_reconcile"] = False
        report["steps"]["structure_step"] = "off"
        del report["probes"]["replicate"], report["probes"]["sam3"], report["probes"]["azure_di"]

    services = compose(with_report(minimal))
    s = by_id(services)
    for id in ("sam3", "mask2former", "azure_di", "replicate", "room_inventory", "structure"):
        assert s[id].status == "disabled", id
        assert s[id].checked_at == NOW - timedelta(seconds=5), id  # the report's heartbeat
        assert not s[id].required, id
        assert s[id].check_kind == "configuration"
    assert s["appraisal"].status == "healthy"
    assert s["vision"].status == "healthy"
    assert not overall_failed(services)


def test_sam3_is_required_only_when_the_rooms_gate_waits_for_it() -> None:
    def wait(report: dict[str, Any]) -> None:
        report["steps"]["rooms_gate_waits_for_localization"] = True
        report["probes"]["sam3"] = probe(
            "down", detail="not found for these credentials (HTTP 404)"
        )

    services = compose(with_report(wait))
    assert by_id(services)["sam3"].required and by_id(services)["sam3"].status == "down"
    assert overall_failed(services)
    loose = compose(with_report(lambda r: r["probes"].update(sam3=probe("down"))))
    assert by_id(loose)["sam3"].status == "down" and not overall_failed(loose)


def test_self_hosted_sam3_is_not_probed_by_default_and_says_why() -> None:
    def hosted(report: dict[str, Any]) -> None:
        report["localizer"] = {"adapter": "self-hosted", "version": "1bf97763d5df", "probed": False}
        del report["probes"]["sam3"]

    s = by_id(compose(with_report(hosted)))
    assert s["sam3"].status == "unknown" and "billed per second" in s["sam3"].detail
    assert s["sam3"].adapter == "self-hosted"

    def probed(report: dict[str, Any]) -> None:
        hosted(report)
        report["localizer"]["probed"] = True
        report["probes"]["sam3"] = probe(detail="ready: setup succeeded, so the model is loaded")

    s = by_id(compose(with_report(probed)))
    assert s["sam3"].status == "healthy" and "model is loaded" in s["sam3"].detail


def test_local_mask2former_is_unknown_until_loaded() -> None:
    def local(loaded: bool) -> Any:
        def change(report: dict[str, Any]) -> None:
            report["segmenter"] = {"adapter": "local", "loaded": loaded}
            report["steps"]["materials_second_look"] = "surface_crops"

        return change

    cold = by_id(compose(with_report(local(False))))
    assert cold["mask2former"].status == "unknown" and "first use" in cold["mask2former"].detail
    # Optional for the room step: an unknown segmenter does not fail it.
    assert cold["room_inventory"].status == "degraded"
    warm = by_id(compose(with_report(local(True))))
    assert warm["mask2former"].status == "healthy"
    assert warm["room_inventory"].status == "healthy"


# ------------------------------------------------------------------------ evidence age


def test_expired_evidence_is_unknown_and_keeps_its_time() -> None:
    def old(report: dict[str, Any]) -> None:
        for name in report["probes"]:
            report["probes"][name] = probe(age_s=600)

    s = by_id(compose(with_report(old)))
    assert s["anthropic"].status == "unknown"
    assert s["anthropic"].checked_at == NOW - timedelta(seconds=600)
    assert "expired" in s["anthropic"].detail
    assert s["vision"].status == "unknown"


def test_a_stale_worker_heartbeat_is_not_current() -> None:
    stale = world(heartbeats=[beat(age_s=600), beat("relay")])
    services = compose(inputs(pollers=ok(Pollers(0, 0)), state=ok(stale)))
    s = by_id(services)
    assert s["worker"].status == "down"
    assert s["vision"].status == "unknown" and "No current worker heartbeat" in s["vision"].detail


def test_configuration_alone_is_never_healthy() -> None:
    claim = Service("api", "healthy", "configuration", "a setting", True, checked_at=NOW)
    assert expire(claim, NOW).status == "unknown"
    timeless = Service("api", "healthy", "probe", "no time", True)
    assert expire(timeless, NOW).status == "unknown"


# ------------------------------------------------------------------------ the worker


def test_worker_cold_start_is_degraded_then_down_after_the_limit() -> None:
    report = worker_report()
    report["embedder"] = {"status": "warming", "model": EMBEDDER, "since": NOW.isoformat()}
    report["probes"] = {}
    cold = inputs(
        pollers=ok(Pollers(0, 0)),
        state=ok(
            world(heartbeats=[beat(state="warming", started_s=40, report=report), beat("relay")])
        ),
    )
    services = compose(cold)
    s = by_id(services)
    assert s["worker"].status == "degraded" and "Cold start: warming for 40 s" in s["worker"].detail
    assert s["embeddings"].status == "degraded"
    assert s["takeoff"].status == "degraded"
    assert not overall_failed(services)
    stuck = inputs(
        pollers=ok(Pollers(0, 0)),
        state=ok(
            world(heartbeats=[beat(state="warming", started_s=1200, report=report), beat("relay")])
        ),
    )
    assert by_id(compose(stuck))["worker"].status == "down"
    assert overall_failed(compose(stuck))


def test_a_failed_embedder_is_down() -> None:
    def broken(report: dict[str, Any]) -> None:
        report["embedder"] = {"status": "failed", "model": EMBEDDER}

    report = worker_report()
    broken(report)
    s = by_id(
        compose(
            inputs(
                state=ok(
                    world(
                        heartbeats=[
                            beat(state="warming", started_s=30, report=report),
                            beat("relay"),
                        ]
                    )
                )
            )
        )
    )
    assert s["embeddings"].status == "down"


def test_worker_polling_but_temporal_sees_no_poller_is_degraded() -> None:
    s = by_id(compose(inputs(pollers=ok(Pollers(workflow=1, activity=0)))))
    assert s["worker"].status == "degraded" and "lists no recent poller" in s["worker"].detail


def test_poller_check_failure_degrades_the_worker_and_says_so() -> None:
    s = by_id(compose(inputs(pollers=failed())))
    assert s["worker"].status == "degraded" and "poller check unavailable" in s["worker"].detail


def test_pollers_without_a_heartbeat_are_degraded() -> None:
    services = compose(inputs(state=ok(world(heartbeats=[beat("relay")]))))
    s = by_id(services)
    assert s["worker"].status == "degraded" and "BASIS-INF-004" in s["worker"].detail


def test_a_stopped_worker_is_down_although_temporal_lists_its_last_polls() -> None:
    stopped = beat(state="stopped", age_s=20)
    services = compose(inputs(state=ok(world(heartbeats=[stopped, beat("relay")]))))
    worker = by_id(services)["worker"]
    assert worker.status == "down" and "The worker stopped 20 s ago" in worker.detail
    assert worker.checked_at == NOW - timedelta(seconds=20)
    assert overall_failed(services)
    # Long after the stop, lingering pollers are not explained by it.
    old = beat(state="stopped", age_s=600)
    later = by_id(compose(inputs(state=ok(world(heartbeats=[old, beat("relay")])))))["worker"]
    assert later.status == "degraded"
    failed_start = beat(state="failed", age_s=5)
    crashed = compose(inputs(state=ok(world(heartbeats=[failed_start, beat("relay")]))))
    assert "The worker failed" in by_id(crashed)["worker"].detail


def test_no_worker_at_all_is_down() -> None:
    services = compose(
        inputs(pollers=ok(Pollers(0, 0)), state=ok(world(heartbeats=[beat("relay")])))
    )
    s = by_id(services)
    assert s["worker"].status == "down"
    assert s["vision"].status == "unknown"
    assert overall_failed(services)


# ------------------------------------------------------------------------ the relay


def test_relay_backlog() -> None:
    healthy = by_id(compose(inputs(state=ok(world(backlog=Backlog(2, 1, 1.5, 0))))))["relay"]
    assert healthy.status == "healthy" and "2 pending, 1 due" in healthy.detail
    behind = by_id(compose(inputs(state=ok(world(backlog=Backlog(40, 40, 300.0, 0))))))["relay"]
    assert behind.status == "degraded" and "behind" in behind.detail
    dead = by_id(compose(inputs(state=ok(world(backlog=Backlog(0, 0, None, 3))))))["relay"]
    assert dead.status == "degraded" and "3 dead message(s)" in dead.detail
    assert "BASIS-INF-003" in dead.detail


def test_no_relay_heartbeat_is_down() -> None:
    services = compose(inputs(state=ok(world(heartbeats=[beat()]))))
    assert by_id(services)["relay"].status == "down"
    assert overall_failed(services)


# ------------------------------------------------------------------------ partial outages


def test_a_database_outage_is_a_partial_answer() -> None:
    services = compose(inputs(db=failed(), state=failed(), ledger=failed()))
    s = by_id(services)
    assert s["db"].status == "down" and "timed out" in s["db"].detail
    assert s["worker"].status == "unknown" and s["relay"].status == "unknown"
    assert s["retrieval"].status == "unknown" and s["cost_indices"].status == "unknown"
    for id in ("api", "temporal", "storage", "firebase"):
        assert s[id].status == "healthy", id
    assert overall_failed(services)


def test_temporal_down_and_storage_down() -> None:
    services = compose(inputs(temporal=failed(), storage=failed("failed (Forbidden)")))
    s = by_id(services)
    assert s["temporal"].status == "down" and s["storage"].status == "down"
    assert s["exports"].status == "down"
    assert "Forbidden" in s["storage"].detail
    assert overall_failed(services)


def test_a_provider_outage_fails_only_what_uses_it() -> None:
    def claude_down(report: dict[str, Any]) -> None:
        report["probes"]["anthropic"] = probe("down", detail="credentials rejected (HTTP 401)")

    services = compose(with_report(claude_down))
    s = by_id(services)
    assert s["anthropic"].status == "down"
    assert s["classification"].status == "down"
    assert s["vision"].status == "down"  # fusion is active: it calls Claude per photo
    assert s["appraisal"].status == "healthy"
    assert overall_failed(services)

    def no_fusion(report: dict[str, Any]) -> None:
        claude_down(report)
        report["steps"]["count_fusion_active"] = False
        report["routes"] = [
            r for r in report["routes"] if "VISION_FUSION_MODEL" not in r["settings"]
        ]

    assert by_id(compose(with_report(no_fusion)))["vision"].status == "healthy"


def test_a_down_fallback_provider_is_not_required() -> None:
    services = compose(with_report(lambda r: r["probes"].update(openai=probe("down"))))
    s = by_id(services)
    assert s["openai"].status == "down" and not s["openai"].required
    assert s["vision"].status == "healthy"
    assert not overall_failed(services)


def test_a_malformed_provider_answer_degrades() -> None:
    def malformed(report: dict[str, Any]) -> None:
        report["probes"]["azure_openai"] = probe(
            "degraded", detail="malformed response: HTTP 200 without the documented body"
        )

    s = by_id(compose(with_report(malformed)))
    assert s["azure_openai"].status == "degraded" and "malformed" in s["azure_openai"].detail
    assert s["vision"].status == "degraded" and s["appraisal"].status == "degraded"


def test_an_unrecognized_probe_status_is_unknown() -> None:
    s = by_id(compose(with_report(lambda r: r["probes"].update(azure_di={"status": "green"}))))
    assert s["azure_di"].status == "unknown"


# ------------------------------------------------------------------------ data, not outages


def test_missing_data_is_degraded_and_never_a_provider_outage() -> None:
    def no_table(report: dict[str, Any]) -> None:
        report["cost_table"] = None

    report = worker_report()
    no_table(report)
    services = compose(
        inputs(
            state=ok(
                world(
                    heartbeats=[beat(report=report), beat("relay")],
                    reference=None,
                    cost=CostIndexState(values=600, latest_year=2022, latest_runs=[]),
                )
            )
        )
    )
    s = by_id(services)
    assert s["rsmeans"].status == "degraded" and "Missing licensed data" in s["rsmeans"].detail
    assert s["retrieval"].status == "degraded" and "not a provider" in s["retrieval"].detail
    assert s["cost_indices"].status == "degraded" and "stale" in s["cost_indices"].detail
    assert "python -m basis.costs.refresh" in s["cost_indices"].detail
    assert s["classification"].status == "degraded"
    for id in ("openai", "azure_openai", "anthropic"):
        assert s[id].status == "healthy", id
    assert not overall_failed(services)


def test_an_empty_cost_index_store_and_a_failed_refresh() -> None:
    empty = by_id(compose(inputs(state=ok(world(cost=CostIndexState(0, None, []))))))
    assert empty["cost_indices"].status == "down" and "seed" in empty["cost_indices"].detail
    runs = [
        {"source": "rsmeans_public", "status": "failed", "trigger": "refresh", "finished_at": NOW}
    ]
    failed_run = by_id(compose(inputs(state=ok(world(cost=CostIndexState(600, 2025, runs))))))
    assert failed_run["cost_indices"].status == "degraded"
    assert "rsmeans_public" in failed_run["cost_indices"].detail


def test_an_embedding_model_mismatch_degrades_retrieval() -> None:
    other = Reference(4, "intfloat/e5-small-v2", NOW - timedelta(days=1))
    s = by_id(compose(inputs(state=ok(world(reference=other)))))
    assert s["retrieval"].status == "degraded" and "e5-small-v2" in s["retrieval"].detail


# ------------------------------------------------------------------------ inference evidence


def test_last_inference_is_reported_apart_from_the_probe() -> None:
    ledger = Ledger(
        [
            LastCall("azure", "chat", "analyze_photo", "gpt-6.1-sol", NOW - timedelta(days=3)),
            LastCall(
                "anthropic", "chat", "process_assets", "claude-sonnet-5-5", NOW - timedelta(hours=2)
            ),
            LastCall(
                "replicate",
                "localization",
                "localize_photo",
                "vufinder/sam3",
                NOW - timedelta(days=1),
            ),
            LastCall(
                "azure_di",
                "document_analysis",
                "ingest_appraisal",
                "prebuilt-layout",
                NOW - timedelta(days=5),
            ),
        ]
    )
    s = by_id(compose(inputs(ledger=ok(ledger))))
    azure = s["azure_openai"]
    assert azure.status == "healthy" and azure.check_kind == "probe"
    assert azure.checked_at == NOW - timedelta(seconds=10)  # the probe, not the 3-day-old call
    assert azure.last_inference is not None and azure.last_inference.model == "gpt-6.1-sol"
    assert s["vision"].last_inference is not None
    assert s["classification"].last_inference.model == "claude-sonnet-5-5"  # type: ignore[union-attr]
    assert s["sam3"].last_inference.kind == "localization"  # type: ignore[union-attr]
    assert s["azure_di"].last_inference.kind == "document_analysis"  # type: ignore[union-attr]
    assert s["openai"].last_inference is None
    assert "No successful paid call is recorded" in s["openai"].detail


def test_the_ledger_failing_only_drops_the_inference_evidence() -> None:
    s = by_id(compose(inputs(ledger=failed())))
    assert s["vision"].status == "healthy" and s["vision"].last_inference is None


def test_compose_does_not_mutate_its_inputs() -> None:
    given = inputs()
    before = copy.deepcopy(given.state.value.heartbeats[0].report)  # type: ignore[union-attr]
    compose(given)
    assert given.state.value.heartbeats[0].report == before  # type: ignore[union-attr]
