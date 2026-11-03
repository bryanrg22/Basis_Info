"""Missing inputs recorded for the operators (EVAL_REPORT 78; Bryan, 2026-11-02).

A study whose dollars are held by a missing input writes one ``client_errors`` record (kind
``study_input``, ``BASIS-STUDY-037``) per blocker per revision, with the firm, the person whose
decision started the run, the study and every file it has; the records group into one issue
per firm and blocker; the operators read them on ``GET /v1/ops/client-errors``; a rerun records
nothing twice; the browser cannot post the kind; and a failure to record never fails the
caller's transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, build_api_app
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.models import ClientError, ClientErrorIssue, Document
from basis.db.repositories.client_error import group_fingerprint
from basis.db.session import session_scope
from basis.errors.codes import E
from basis.evidence import input_records
from basis.evidence.input_records import KIND, event_id, record_input_blockers
from basis.storage import LocalStorage

OPERATOR = "ops-admin"


@pytest.fixture
def ops_app(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> FastAPI:
    return build_api_app(
        api_settings(database_url, ops_operator_uids=(OPERATOR,), git_sha="server-sha"),
        sessionmaker,
        storage,
        verifier,
    )


@pytest.fixture
async def ops(ops_app: FastAPI) -> AsyncIterator[Api]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ops_app), base_url=BASE_URL
    ) as client:
        yield Api(client)


async def held_study(api: Api, seed: Seed) -> tuple[uuid.UUID, dict[str, str]]:
    """A study of alice's firm whose revision 1 has an unpriced asset and no appraisal fields
    the building model can use (synthetic rows: :meth:`Seed.outputs`)."""
    study = await api.create_study("alice", "Harbor House")
    sid = uuid.UUID(study["id"])
    return sid, await seed.outputs(sid, revision=1)


async def records(sessionmaker: async_sessionmaker[AsyncSession]) -> list[ClientError]:
    async with sessionmaker() as session:
        stmt = select(ClientError).where(ClientError.kind == KIND).order_by(ClientError.seq)
        return list((await session.scalars(stmt)).all())


async def record(sessionmaker: async_sessionmaker[AsyncSession], sid: uuid.UUID, rev: int) -> int:
    async with session_scope(sessionmaker) as session:
        return await record_input_blockers(session, sid, rev, api_settings("unused"))


async def test_each_blocker_is_recorded_with_the_firm_the_person_the_study_and_its_files(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, targets = await held_study(api, seed)
    written = await record(sessionmaker, sid, 1)
    stored = await records(sessionmaker)
    assert written == len(stored) >= 2
    codes = [r.error_type for r in stored]
    # The seeded revision: one unpriced asset; one extraction field (no living area, no cost
    # approach), so no building cost or depreciation; no location; no index for the year.
    assert "unpriced" in codes and "building_rcn" in codes and "depreciation" in codes
    alice = await seed.user("alice")
    async with sessionmaker() as session:
        documents = (await session.scalars(select(Document).where(Document.study_id == sid))).all()
    for row in stored:
        assert (row.kind, row.code, row.study_id, row.revision) == (
            KIND,
            E.STUDY_INPUT_MISSING,
            sid,
            1,
        )
        assert row.user_id == alice.id and row.org_id is not None
        assert row.event_id == event_id(sid, 1, row.error_type or "")
        assert row.message.startswith("Harbor House: ")
        event: dict[str, Any] = row.event
        assert event["blocker"]["code"] == row.error_type and event["blocker"]["fix"]
        assert event["study"]["name"] == "Harbor House" and event["study"]["revision"] == 1
        assert event["person"]["user_id"] == str(alice.id)
        assert event["person"]["role"] == "created the study"  # no review yet
        assert event["firm"]["org_id"] == str(row.org_id)
        assert {d["document_id"] for d in event["documents"]} == {str(d.id) for d in documents}
        assert all(d["storage_uri"] and d["filename"] and d["sha256"] for d in event["documents"])
    [unpriced] = [r for r in stored if r.error_type == "unpriced"]
    assert unpriced.event["asset_ids"] == [targets["asset"]] and unpriced.event["assets_held"] == 1
    assert unpriced.event["fix_stage"] == "assets_review"


async def test_a_rerun_records_nothing_twice_and_a_later_revision_joins_the_same_issue(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, _ = await held_study(api, seed)
    first = await record(sessionmaker, sid, 1)
    assert await record(sessionmaker, sid, 1) == 0  # a retried activity
    assert len(await records(sessionmaker)) == first
    await seed.outputs(sid, revision=2)
    assert await record(sessionmaker, sid, 2) == first
    stored = await records(sessionmaker)
    org_id = stored[0].org_id
    fp = group_fingerprint(KIND, f"{org_id}:unpriced")
    async with sessionmaker() as session:
        issue = await session.get(ClientErrorIssue, fp)
    assert issue is not None and issue.occurrences == 2  # one issue per firm and blocker
    assert {r.fingerprint for r in stored if r.error_type == "unpriced"} == {fp}


async def test_a_revision_with_no_blocker_records_nothing(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid, _ = await held_study(api, seed)
    real = input_records.compute_result

    def resolved(*args: Any, **kwargs: Any) -> Any:
        return real(*args, **kwargs).model_copy(update={"blockers": []})

    monkeypatch.setattr(input_records, "compute_result", resolved)
    assert await record(sessionmaker, sid, 1) == 0
    assert await records(sessionmaker) == []


async def test_a_failure_to_record_never_fails_the_callers_transaction(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid, _ = await held_study(api, seed)

    async def broken(*_: Any, **__: Any) -> int:
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(input_records, "_record", broken)
    async with session_scope(sessionmaker) as session:
        assert await record_input_blockers(session, sid, 1, api_settings("unused")) == 0
        # The session is still usable: the savepoint rolled back, not the transaction.
        assert (
            await session.scalar(select(Document.id).where(Document.study_id == sid))
        ) is not None
    assert await records(sessionmaker) == []


async def test_operators_list_the_records_by_kind_and_the_browser_cannot_post_one(
    api: Api, ops: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, _ = await held_study(api, seed)
    await record(sessionmaker, sid, 1)
    listed = await ops.get(OPERATOR, "/v1/ops/client-errors", params={"kind": KIND})
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert items and {i["kind"] for i in items} == {KIND}
    assert {i["study_id"] for i in items} == {str(sid)}
    one = await ops.get(OPERATOR, f"/v1/ops/client-errors/{items[0]['id']}")
    assert one.status_code == 200 and one.json()["event"]["documents"]
    refused = await api.post(
        "alice",
        "/v1/client-errors",
        {
            "events": [
                {
                    "event_id": str(uuid.uuid4()),
                    "occurred_at": "2026-11-02T12:00:00+00:00",
                    "kind": KIND,
                    "message": "a forged record",
                }
            ]
        },
    )
    assert refused.status_code == 422
    assert all(r.message != "a forged record" for r in await records(sessionmaker))
