"""The Postgres capture store (:class:`basis.capture.pg_store.PgCaptureStore`, migration 0036)
held to the in-memory store's rules (EVAL_REPORT 98), on a real database:

* the lease is one conditional UPDATE: two workers racing for it get it once; a lapsed lease
  is taken over; only its holder renews or releases it;
* two block signals racing trip the breaker once and raise exactly one ``capture_blocked``
  alert (the row lock serializes them; ``raise_once``'s unique key is the second guard); the
  next trip after it lapses raises a second with a doubled wait;
* an incident row counts every occurrence of its code with first and last seen and the newest
  occurrence's study, capture, page and evidence; the timeline gets ``capture.paused`` /
  ``capture.failed`` with the code;
* the navigation log feeds the caps and prunes rows older than two days;
* the search puts the found point on a pin without one (unconfirmed, version + 1) and never
  moves an engineer's point;
* a success closes the breaker and records the last success; the snapshot reports it all;
* EVAL_REPORT 99 (migration 0037): a history view's date, leaf label and green share and a
  street view's camera point and panorama round-trip; a part that did not work writes its
  note once per code, its incident and a ``capture.part_failed`` event; the reference date
  is the intake's placed-in-service date, else the appraisal's effective date from the
  current revision's extraction fields, else none.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from dbtest_support import Factory
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.capture.governor import GovernorConfig
from basis.capture.pg_store import PgCaptureStore
from basis.capture.store import Incident, NewView, Note
from basis.db.models import (
    Alert,
    CaptureIncident,
    CaptureNavigation,
    CaptureView,
    Document,
    Extraction,
    ExtractionField,
    PropertyCapture,
    PropertyPin,
    Study,
    StudyEvent,
)
from basis.db.session import session_scope
from basis.errors.codes import E

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)
CONFIG = GovernorConfig()


async def seeded(
    sm: async_sessionmaker[AsyncSession], *, lat: float | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_scope(sm) as session:
        study = await Factory(session).study()
        session.add(
            PropertyPin(
                study_id=study.id,
                address="1 Main St",
                lat=lat,
                lon=None if lat is None else -93.60,
                source="engineer",
                version=1,
            )
        )
        capture = PropertyCapture(study_id=study.id, status="queued", pin_version=1)
        session.add(capture)
        await session.flush()
        return study.id, capture.id


def incident(code: str, study_id: uuid.UUID, capture_id: uuid.UUID, at: datetime) -> Incident:
    return Incident(
        code=code,
        at=at,
        study_id=study_id,
        capture_id=capture_id,
        url="https://www.google.com/sorry/index",
        http_status=429,
        detail="the unusual-traffic page",
        evidence=f"gs://b/studies/{study_id}/captures/{capture_id}/evidence/x/",
    )


async def test_the_lease_is_taken_once_and_lapses(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    store = PgCaptureStore(committed)
    ttl = timedelta(minutes=10)
    got = await asyncio.gather(
        store.acquire_lease("w1", ttl, NOW), store.acquire_lease("w2", ttl, NOW)
    )
    assert sorted(got) == [False, True]
    holder = "w1" if got[0] else "w2"
    other = "w2" if holder == "w1" else "w1"
    assert not await store.renew_lease(other, ttl, NOW)
    await store.release_lease(other)  # not its lease: nothing happens
    assert not await store.acquire_lease(other, ttl, NOW + timedelta(minutes=5))
    assert await store.acquire_lease(other, ttl, NOW + timedelta(minutes=11))  # lapsed


async def test_racing_signals_trip_once_and_raise_one_alert(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    store = PgCaptureStore(committed)
    first = await seeded(committed)
    second = await seeded(committed)
    trips = await asyncio.gather(
        *(
            store.pause(
                capture_id,
                reason="paused",
                resume_at=NOW,
                incident=incident(E.CAPTURE_UNUSUAL_TRAFFIC, study_id, capture_id, NOW),
                config=CONFIG,
                block=True,
            )
            for study_id, capture_id in (first, second)
        )
    )
    assert sorted(t.new for t in trips if t is not None) == [False, True]
    async with session_scope(committed) as session:
        alerts = (await session.scalars(select(Alert))).all()
        assert [a.kind for a in alerts] == ["capture_blocked"]
        assert alerts[0].payload["trip"] == 1
        row = await session.get(CaptureIncident, E.CAPTURE_UNUSUAL_TRAFFIC)
        assert row is not None and row.occurrences == 2
        captures = (await session.scalars(select(PropertyCapture))).all()
        assert {(c.status, c.resume_at) for c in captures} == {
            ("waiting", NOW + timedelta(hours=1))
        }
        events = (
            await session.scalars(
                select(StudyEvent).where(StudyEvent.event_type == "capture.paused")
            )
        ).all()
        assert len(events) == 2 and {e.code for e in events} == {E.CAPTURE_UNUSUAL_TRAFFIC}
    # After it lapses, the next signal is trip 2, twice as long, with its own alert.
    later = NOW + timedelta(hours=1, minutes=1)
    trip = await store.pause(
        first[1],
        reason="paused",
        resume_at=later,
        incident=incident(E.CAPTURE_WALL, first[0], first[1], later),
        config=CONFIG,
        block=True,
    )
    assert trip is not None and trip.new and trip.number == 2
    assert trip.state.breaker_open_until == later + timedelta(hours=2)
    async with session_scope(committed) as session:
        assert await session.scalar(select(func.count()).select_from(Alert)) == 2


async def test_fail_complete_and_the_snapshot(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    store = PgCaptureStore(committed)
    study_id, capture_id = await seeded(committed)
    await store.start(capture_id, NOW)
    for minutes in range(3):
        await store.navigated("load", capture_id, NOW + timedelta(minutes=minutes))
    await store.navigated("move", capture_id, NOW + timedelta(minutes=4))
    async with session_scope(committed) as session:
        session.add(CaptureNavigation(kind="load", created_at=NOW - timedelta(days=3)))
    await store.navigated("move", capture_id, NOW + timedelta(minutes=5))  # prunes the old row
    state, usage = await store.governor(NOW + timedelta(minutes=6))
    assert len(usage.loads) == 3 and len(usage.moves) == 2
    assert state.last_navigation_at == NOW + timedelta(minutes=5)
    await store.add_view(
        NewView(
            study_id=study_id,
            capture_id=capture_id,
            pin_version=1,
            kind="aerial",
            label="aerial-close",
            lat=41.60,
            lon=-93.60,
            ground_alt_m=250,
            range_m=120,
            fov_deg=35,
            heading_deg=0,
            tilt_deg=0,
            pixel_size_m=0.084,
            imagery_date="8/4/2026",
            width=1440,
            height=900,
            uri="gs://b/x.png",
            sha256="0" * 64,
            bytes=10,
            source="Aerial imagery",
            captured_at=NOW,
        )
    )
    assert await store.labels(capture_id) == {"aerial-close"}
    await store.complete(capture_id, views=1, now=NOW + timedelta(minutes=7))
    snapshot = await store.snapshot(NOW + timedelta(minutes=8))
    assert snapshot.state.last_success_at == NOW + timedelta(minutes=7)
    assert snapshot.state.trips == 0
    failing = await seeded(committed)
    await store.fail(
        failing[1],
        reason="blank",
        incident=incident(E.CAPTURE_BLANK_RENDER, failing[0], failing[1], NOW),
    )
    async with session_scope(committed) as session:
        row = await session.get(PropertyCapture, failing[1])
        assert row is not None and (row.status, row.code) == ("failed", E.CAPTURE_BLANK_RENDER)
        done = await session.get(PropertyCapture, capture_id)
        assert done is not None and (done.status, done.views) == ("succeeded", 1)


async def test_the_search_places_a_point_only_on_a_pin_without_one(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    store = PgCaptureStore(committed)
    study_id, capture_id = await seeded(committed)
    assert await store.place_pin(capture_id, 41.60, -93.60, "1 Main St, IA", NOW) == 2
    pin = await store.pin(study_id)
    assert pin is not None and (pin.source, pin.confirmed, pin.version) == ("search", False, 2)
    capture = await store.capture(capture_id)
    assert capture is not None and capture.pin_version == 2
    kept_study, kept_capture = await seeded(committed, lat=40.0)
    assert await store.place_pin(kept_capture, 41.60, -93.60, None, NOW) == 1
    kept = await store.pin(kept_study)
    assert kept is not None and kept.lat == 40.0


def _history_view(
    study_id: uuid.UUID, capture_id: uuid.UUID, label: str, **extra: object
) -> NewView:
    values: dict[str, object] = {
        "study_id": study_id,
        "capture_id": capture_id,
        "pin_version": 1,
        "kind": "history",
        "label": label,
        "lat": 43.85,
        "lon": -103.20,
        "ground_alt_m": 250,
        "range_m": 165.33,
        "fov_deg": 35,
        "heading_deg": 0,
        "tilt_deg": 0,
        "pixel_size_m": 0.0841,
        "imagery_date": "3/12/2021",
        "width": 1440,
        "height": 900,
        "uri": f"gs://b/{label}.png",
        "sha256": "0" * 64,
        "bytes": 10,
        "source": "Aerial imagery",
        "captured_at": NOW,
        "imagery_on": date(2021, 3, 12),
        "imagery_date_note": "status-bar, matches the address",
        "leaf": "leaf-off",
        "green_share": 0.171,
    }
    values.update(extra)
    return NewView(**values)  # type: ignore[arg-type]


async def test_history_and_street_views_notes_and_the_reference_date(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    store = PgCaptureStore(committed)
    study_id, capture_id = await seeded(committed)
    await store.start(capture_id, NOW)
    await store.add_view(_history_view(study_id, capture_id, "history-2021-03-12"))
    await store.add_view(
        _history_view(
            study_id,
            capture_id,
            "street-front",
            kind="street",
            tilt_deg=90,
            pixel_size_m=None,
            leaf="unknown",
            green_share=None,
            imagery_on=None,
            imagery_date=None,
            imagery_date_note="not read",
            camera_lat=43.8505,
            camera_lon=-103.2000,
            pano_id="AbCdEfGhIjKl",
        )
    )
    async with session_scope(committed) as session:
        rows = {
            v.label: v
            for v in await session.scalars(
                select(CaptureView).where(CaptureView.capture_id == capture_id)
            )
        }
    history = rows["history-2021-03-12"]
    assert (history.imagery_on, history.leaf, history.green_share) == (
        date(2021, 3, 12),
        "leaf-off",
        pytest.approx(0.171),
    )
    front = rows["street-front"]
    assert (front.camera_lat, front.camera_lon, front.pano_id, front.tilt_deg) == (
        43.8505,
        -103.2000,
        "AbCdEfGhIjKl",
        90,
    )
    note = Note("street", E.CAPTURE_STREET_REFUSED, "Street-level pictures are not available yet.")
    for _ in range(2):
        await store.part_failed(
            capture_id, note, incident(E.CAPTURE_STREET_REFUSED, study_id, capture_id, NOW)
        )
    row = await store.capture(capture_id)
    assert row is not None and row.notes == (note.as_json(),)  # once per code
    async with session_scope(committed) as session:
        counted = await session.get(CaptureIncident, E.CAPTURE_STREET_REFUSED)
        assert counted is not None and counted.occurrences == 2
        events = (
            await session.scalars(
                select(StudyEvent).where(
                    StudyEvent.study_id == study_id, StudyEvent.event_type == "capture.part_failed"
                )
            )
        ).all()
        assert len(events) == 2 and events[0].code == E.CAPTURE_STREET_REFUSED
        assert events[0].detail["part"] == "street"
    # The reference date: nothing yet, then the appraisal's effective date, then the intake.
    assert await store.reference_date(study_id) is None
    async with session_scope(committed) as session:
        document = Document(
            study_id=study_id,
            kind="appraisal",
            filename="appraisal.pdf",
            content_type="application/pdf",
            bytes=100,
            sha256="1" * 64,
            gcs_uri=f"gs://bucket/{uuid.uuid4()}",
        )
        session.add(document)
        await session.flush()
        extraction = Extraction(
            study_id=study_id, document_id=document.id, revision=1, method="m", raw={}
        )
        session.add(extraction)
        await session.flush()
        session.add(
            ExtractionField(
                study_id=study_id,
                extraction_id=extraction.id,
                section="reconciliation",
                field="effective_date_of_appraisal",
                value_date=date(2024, 9, 1),
                source="azure",
            )
        )
    assert await store.reference_date(study_id) == (
        date(2024, 9, 1),
        "appraisal effective date",
    )
    from basis.domain.intake import StudyIntake

    example = {
        "purchase_price": "650000.00",
        "acquisition_costs": [],
        "land_method": "appraisal_site_value",
        "appraisal_site_value": "90000.00",
        "appraisal_effective_date": "2025-03-14",
        "property_use": "residential_rental",
        "dwelling_unit_income_share": "1",
        "transient_lodging": False,
        "placed_in_service_date": "2025-05-01",
        "acquisition_date": "2025-04-15",
        "state": "NY",
        "zip": "12345",
    }
    async with session_scope(committed) as session:
        study = await session.get(Study, study_id)
        assert study is not None
        study.intake = StudyIntake.model_validate(example)
    assert await store.reference_date(study_id) == (
        date(2025, 5, 1),
        "placed in service (intake)",
    )
    assert await store.reference_date(uuid.uuid4()) is None
