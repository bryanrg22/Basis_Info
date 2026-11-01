"""``GET /v1/studies`` as the library (docs/api/STUDY_LIBRARY.md): search, filters, sort,
keyset pages under each sort, ``total``; ``POST /v1/studies/{id}/unarchive``.

Against the real application and a real PostgreSQL container; Firebase is replaced by
``FakeVerifier``. The creation, get and archive routes are in ``test_studies.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from apitest_support import COMPLETE_INTAKE, Api, Seed, bearer
from dbtest_support import queued_behind_a_lock
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import OrgRole, Stage, StudyStatus
from basis.db.models import Study, StudyEvent
from basis.db.repositories.study import SORTS, StudySort
from basis.db.session import session_scope
from basis.errors.codes import E

pytestmark = pytest.mark.integration


def ids(items: list[dict[str, Any]]) -> list[str]:
    return [item["id"] for item in items]


async def listing(api: Api, uid: str = "alice", **params: Any) -> dict[str, Any]:
    response = await api.get(uid, "/v1/studies", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def walk(api: Api, limit: int, **params: Any) -> tuple[list[str], int, int]:
    """Every page under ``params``: the ids in order, the page count, the total."""
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    total = -1
    while True:
        page = await listing(api, limit=limit, **({"cursor": cursor} if cursor else {}), **params)
        assert len(page["items"]) <= limit
        seen += ids(page["items"])
        pages += 1
        total = page["total"]
        cursor = page["next_cursor"]
        if cursor is None:
            return seen, pages, total


async def set_status(
    sm: async_sessionmaker[AsyncSession], study_id: str, status: StudyStatus, **values: Any
) -> None:
    async with session_scope(sm) as session:
        await session.execute(
            update(Study)
            .where(Study.id == uuid.UUID(study_id))
            .values(status=status, **values)
            .execution_options(synchronize_session=False)
        )


async def touch(sm: async_sessionmaker[AsyncSession], study_id: str, when: datetime) -> None:
    """Set ``updated_at`` directly (the trigger sets it on every real write; the tests need
    a known order)."""
    async with session_scope(sm) as session:
        await session.execute(
            update(Study)
            .where(Study.id == uuid.UUID(study_id))
            .values(updated_at=when)
            .execution_options(synchronize_session=False)
        )


# --------------------------------------------------------------------------- search


async def test_q_is_a_case_insensitive_substring_of_the_property_name(api: Api) -> None:
    harbor = await api.create_study("alice", "1200 Harbor Blvd, Oxnard CA")
    main = await api.create_study("alice", "12 Main St")
    await api.create_study("alice", "Elm House")

    assert ids((await listing(api, q="harbor"))["items"]) == [harbor["id"]]
    assert ids((await listing(api, q="HARBOR BLVD"))["items"]) == [harbor["id"]]
    assert ids((await listing(api, q="  main  "))["items"]) == [main["id"]]  # trimmed
    assert (await listing(api, q="12"))["total"] == 2  # a substring anywhere
    assert (await listing(api, q="nothing like it"))["items"] == []
    assert (await listing(api, q=""))["total"] == 3  # blank matches everything
    assert (await listing(api, q="   "))["total"] == 3


async def test_q_treats_like_wildcards_as_literal_characters(api: Api) -> None:
    percent = await api.create_study("alice", "100% Occupied Plaza")
    underscore = await api.create_study("alice", "unit_7 warehouse")
    backslash = await api.create_study("alice", r"C:\plans archive")
    await api.create_study("alice", "unit 7 warehouse")  # a space, not an underscore

    assert ids((await listing(api, q="100%"))["items"]) == [percent["id"]]
    assert ids((await listing(api, q="unit_7"))["items"]) == [underscore["id"]]
    assert ids((await listing(api, q="\\plans"))["items"]) == [backslash["id"]]
    assert (await listing(api, q="%"))["total"] == 1  # not "everything"
    assert (await listing(api, q="_"))["total"] == 1


@pytest.mark.parametrize("params", [{"q": "x" * 201}, {"q": "a\x00b"}])
async def test_q_limits(api: Api, params: dict[str, Any]) -> None:
    assert (await api.get("alice", "/v1/studies", params=params)).status_code == 422


# --------------------------------------------------------------------------- filters


async def test_status_filter_is_repeatable_and_hides_archived_unless_named(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    draft = await api.create_study("alice", "Draft")
    waiting = await api.create_study("alice", "Waiting")
    await seed.stage(waiting["id"], Stage.ROOMS_REVIEW)
    failed = await api.create_study("alice", "Failed")
    await set_status(sessionmaker, failed["id"], StudyStatus.FAILED, failure_reason="boom")
    archived = await api.create_study("alice", "Archived")
    assert (await api.delete("alice", f"/v1/studies/{archived['id']}")).status_code == 200

    assert ids((await listing(api, status="draft"))["items"]) == [draft["id"]]
    both = await listing(api, status=["waiting_review", "failed"], sort="name_asc")
    assert ids(both["items"]) == [failed["id"], waiting["id"]] and both["total"] == 2
    # Archived studies are hidden by default, shown by the switch, and shown when named.
    assert archived["id"] not in ids((await listing(api))["items"])
    assert (await listing(api))["total"] == 3
    with_archived = await listing(api, include_archived="true", sort="name_asc")
    assert ids(with_archived["items"])[0] == archived["id"] and with_archived["total"] == 4
    assert ids((await listing(api, status="archived"))["items"]) == [archived["id"]]
    named = await listing(api, status=["archived", "draft"], sort="name_asc")
    assert ids(named["items"]) == [archived["id"], draft["id"]]
    assert (await api.get("alice", "/v1/studies", params={"status": "open"})).status_code == 422


async def test_created_by_filter(api: Api, seed: Seed) -> None:
    org = await seed.org()
    await seed.member(org, "alice", OrgRole.OWNER)
    await seed.member(org, "bob", OrgRole.ENGINEER)
    mine = await api.create_study("alice", "Mine", org_id=str(org))
    bobs = await api.create_study("bob", "Bob's", org_id=str(org))

    assert ids((await listing(api, created_by=mine["created_by"]))["items"]) == [mine["id"]]
    assert ids((await listing(api, created_by=bobs["created_by"]))["items"]) == [bobs["id"]]
    assert (await listing(api, created_by=str(uuid.uuid4())))["total"] == 0
    assert (await api.get("alice", "/v1/studies", params={"created_by": "me"})).status_code == 422


async def test_filters_combine_and_total_counts_every_page(api: Api, seed: Seed) -> None:
    for i in range(7):
        study = await api.create_study("alice", f"Harbor {i}")
        if i % 2:
            await seed.stage(study["id"], Stage.ROOMS_REVIEW)
    await api.create_study("alice", "Elm")

    page = await listing(api, q="harbor", status="waiting_review", limit=2)
    assert len(page["items"]) == 2 and page["total"] == 3 and page["next_cursor"]
    seen, pages, total = await walk(api, 2, q="harbor", status="waiting_review")
    assert len(seen) == 3 and pages == 2 and total == 3
    assert (await listing(api, q="harbor"))["total"] == 7
    assert (await listing(api))["total"] == 8


async def test_a_member_of_no_firm_still_gets_no_firm(seed: Seed, invite_only_api: Api) -> None:
    response = await invite_only_api.get("nina", "/v1/studies", params={"q": "x"})
    assert response.status_code == 403 and response.json()["type"].endswith("/no-firm")


# --------------------------------------------------------------------------- sort


async def test_default_sort_is_most_recently_updated_first(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    first = await api.create_study("alice", "First")
    second = await api.create_study("alice", "Second")
    third = await api.create_study("alice", "Third")
    # A write to the oldest study moves it to the top (the trigger sets updated_at).
    response = await api.client.put(
        f"/v1/studies/{first['id']}/intake",
        json=COMPLETE_INTAKE,
        headers=bearer("alice"),
    )
    assert response.status_code == 200, response.text

    page = await listing(api)
    assert ids(page["items"]) == [first["id"], third["id"], second["id"]]
    assert ids((await listing(api, sort="updated_desc"))["items"]) == ids(page["items"])
    assert ids((await listing(api, sort="created_desc"))["items"]) == [
        third["id"],
        second["id"],
        first["id"],
    ]
    assert ids((await listing(api, sort="name_asc"))["items"]) == [
        first["id"],
        second["id"],
        third["id"],
    ]


async def test_name_sort_ignores_case_and_breaks_ties_by_id(api: Api) -> None:
    names = ["beta", "Alpha", "alpha", "ALPHA", "Gamma", "alpha"]
    created = {name: [] for name in names}
    for name in names:
        created[name].append((await api.create_study("alice", name))["id"])

    page = await listing(api, sort="name_asc")
    listed = [item["property_name"].lower() for item in page["items"]]
    assert listed == ["alpha"] * 4 + ["beta", "gamma"]
    alphas = ids(page["items"])[:4]
    assert alphas == sorted(alphas)  # ties by id, so the order is stable across pages


@pytest.mark.parametrize("sort", SORTS)
async def test_pages_under_each_sort_have_no_gaps_or_repeats(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession], sort: StudySort
) -> None:
    # Names that collide (case, duplicates) and update times out of creation order.
    names = ["Delta", "alpha", "Alpha", "charlie", "alpha", "Bravo", "echo"]
    created = [(await api.create_study("alice", name))["id"] for name in names]
    base = datetime(2026, 10, 1, tzinfo=UTC)
    for offset, study_id in zip((3, 0, 5, 1, 4, 2, 6), created, strict=True):
        await touch(sessionmaker, study_id, base + timedelta(hours=offset))

    seen, pages, total = await walk(api, 3, sort=sort)

    assert pages == 3 and total == 7
    assert sorted(seen) == sorted(created) and len(seen) == len(set(seen))
    everything = ids((await listing(api, sort=sort, limit=100))["items"])
    assert seen == everything  # the pages are the one listing, cut


async def test_a_row_inserted_between_pages_is_neither_skipped_nor_repeated(
    api: Api,
) -> None:
    created = [(await api.create_study("alice", f"S{i}"))["id"] for i in range(4)]
    first = await listing(api, limit=2, sort="created_desc")
    assert ids(first["items"]) == [created[3], created[2]]
    newest = await api.create_study("alice", "S4")  # arrives after page one was read

    rest, pages, total = await walk(api, 2, sort="created_desc", cursor=first["next_cursor"])

    assert rest == [created[1], created[0]] and newest["id"] not in rest
    assert total == 5  # the total is as of each request


async def test_a_cursor_from_another_sort_is_422_invalid_cursor(api: Api) -> None:
    for i in range(3):
        await api.create_study("alice", f"S{i}")
    page = await listing(api, limit=1, sort="updated_desc")

    response = await api.get(
        "alice", "/v1/studies", params={"cursor": page["next_cursor"], "sort": "name_asc"}
    )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/invalid-cursor")
    assert response.json()["code"] == E.API_INVALID_CURSOR
    # The same cursor under its own sort is fine, and the default sort is updated_desc.
    assert (
        await api.get("alice", "/v1/studies", params={"cursor": page["next_cursor"]})
    ).status_code == 200


async def test_unknown_sort_is_422(api: Api) -> None:
    assert (await api.get("alice", "/v1/studies", params={"sort": "name"})).status_code == 422


# --------------------------------------------------------------------------- unarchive


async def events_of(
    sm: async_sessionmaker[AsyncSession], study_id: str, event_type: str
) -> list[StudyEvent]:
    async with sm() as session:
        rows = await session.scalars(
            select(StudyEvent)
            .where(StudyEvent.study_id == uuid.UUID(study_id), StudyEvent.event_type == event_type)
            .order_by(StudyEvent.seq)
        )
        return list(rows.all())


async def unarchived_events(
    sm: async_sessionmaker[AsyncSession], study_id: str
) -> list[StudyEvent]:
    return await events_of(sm, study_id, "study.unarchived")


async def test_unarchive_restores_the_status_the_stage_implies(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    cases = {
        "draft": (Stage.UPLOADING, None),
        "processing": (Stage.EXTRACTING, None),
        "waiting_review": (Stage.ROOMS_REVIEW, None),
        "completed": (Stage.COMPLETED, None),
        "failed": (Stage.EXTRACTING, "the appraisal could not be read"),
    }
    for expected, (stage, failure) in cases.items():
        study = await api.create_study("alice", expected)
        await seed.stage(study["id"], stage)
        if failure is not None:
            await set_status(sessionmaker, study["id"], StudyStatus.FAILED, failure_reason=failure)
        archived = await api.delete("alice", f"/v1/studies/{study['id']}")
        assert archived.json()["status"] == "archived"

        response = await api.post("alice", f"/v1/studies/{study['id']}/unarchive")

        assert response.status_code == 200, response.text
        assert response.json()["status"] == expected, expected
        assert response.json()["current_stage"] == stage.value
        [event] = await unarchived_events(sessionmaker, study["id"])
        assert event.actor_id is not None and event.after == {"status": expected}
        assert study["id"] in ids((await listing(api))["items"])


async def test_unarchive_is_idempotent_and_records_only_the_change(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    untouched = await api.post("alice", f"/v1/studies/{study['id']}/unarchive")
    assert untouched.status_code == 200 and untouched.json()["status"] == "draft"
    assert await unarchived_events(sessionmaker, study["id"]) == []
    await api.delete("alice", f"/v1/studies/{study['id']}")
    first = await api.post("alice", f"/v1/studies/{study['id']}/unarchive")
    second = await api.post("alice", f"/v1/studies/{study['id']}/unarchive")
    assert (first.status_code, second.status_code) == (200, 200)
    assert len(await unarchived_events(sessionmaker, study["id"])) == 1


def row_lock(study_id: str) -> Any:
    return select(Study).where(Study.id == uuid.UUID(study_id)).with_for_update()


async def test_concurrent_unarchives_record_one_event(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Three unarchives of one study at once: every one answers 200, exactly one event.

    The study's row is held locked by this test while the three requests are sent, so all
    three read the study as ``archived`` before any of them can restore it (the interleaving
    that CI hit by chance, 2 of 20 rounds here without the lock); the lock is released once
    all three wait on it (``queued_behind_a_lock``). The guard then has to be the
    repository's, taken under the lock: one taken from the route's earlier read recorded
    three events (verified by reverting it).
    """
    study = await api.create_study("alice")
    await api.delete("alice", f"/v1/studies/{study['id']}")
    unarchive = f"/v1/studies/{study['id']}/unarchive"
    responses = await queued_behind_a_lock(
        sessionmaker, row_lock(study["id"]), *[lambda: api.post("alice", unarchive)] * 3
    )
    assert {r.status_code for r in responses} == {200}
    assert {r.json()["status"] for r in responses} == {"draft"}
    assert len(await unarchived_events(sessionmaker, study["id"])) == 1


async def test_concurrent_archives_record_one_event(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The mirror of the unarchive race (EVAL_REPORT 62): two archives of one draft at once
    both answer 200 and the timeline says ``study.archived`` once. Before the repository
    decided under the row lock, the route recorded an event per request."""
    study = await api.create_study("alice")
    archive = f"/v1/studies/{study['id']}"
    responses = await queued_behind_a_lock(
        sessionmaker, row_lock(study["id"]), *[lambda: api.delete("alice", archive)] * 2
    )
    assert [r.status_code for r in responses] == [200, 200]
    assert {r.json()["status"] for r in responses} == {"archived"}
    assert len(await events_of(sessionmaker, study["id"], "study.archived")) == 1
    # A repeat later records nothing more either.
    assert (await api.delete("alice", archive)).status_code == 200
    assert len(await events_of(sessionmaker, study["id"], "study.archived")) == 1


@pytest.mark.parametrize("role", [OrgRole.ENGINEER, OrgRole.VIEWER])
async def test_unarchive_is_owner_only(api: Api, seed: Seed, role: OrgRole) -> None:
    org = await seed.org()
    await seed.member(org, "carol", OrgRole.OWNER)
    await seed.member(org, "alice", role)
    study = await api.create_study("carol", org_id=str(org))
    await api.delete("carol", f"/v1/studies/{study['id']}")

    response = await api.post("alice", f"/v1/studies/{study['id']}/unarchive")

    assert response.status_code == 403
    assert (await api.get("carol", f"/v1/studies/{study['id']}")).json()["status"] == "archived"


async def test_unarchive_by_a_non_member_or_of_an_unknown_study_is_404(api: Api) -> None:
    study = await api.create_study("alice")
    assert (await api.post("bob", f"/v1/studies/{study['id']}/unarchive")).status_code == 404
    assert (await api.post("alice", f"/v1/studies/{uuid.uuid4()}/unarchive")).status_code == 404
