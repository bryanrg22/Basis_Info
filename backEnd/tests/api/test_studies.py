"""``/v1/studies``: create, list, get, archive (S1_SPEC section 5, BUILD_PLAN 6.4).

Against the real application, a real PostgreSQL container and LocalStorage; Firebase is
replaced by ``FakeVerifier``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from apitest_support import Api, Seed
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.errors import PROBLEM_JSON
from basis.db.enums import OrgRole, Stage
from basis.db.models import Study

pytestmark = pytest.mark.integration


def ids(items: list[dict[str, Any]]) -> list[str]:
    return [item["id"] for item in items]


# --------------------------------------------------------------------------- create


async def test_create_returns_201_with_the_study(api: Api, seed: Seed) -> None:
    response = await api.post("alice", "/v1/studies", {"property_name": "1200 Harbor Blvd"})

    assert response.status_code == 201
    body = response.json()
    assert response.headers["location"] == f"/v1/studies/{body['id']}"
    alice = await seed.user("alice")
    assert body["org_id"] == str(await seed.personal_org("alice"))
    assert body["created_by"] == str(alice.id)
    assert body["property_name"] == "1200 Harbor Blvd"
    assert (body["status"], body["current_stage"], body["revision"]) == ("draft", "uploading", 1)
    assert set(body) == {
        "id",
        "org_id",
        "created_by",
        "property_name",
        "status",
        "current_stage",
        "revision",
        "room_aliases",
        "failure_reason",
        "components_gate",  # v0.12: null until the workflow starts (record_run_start)
        "created_at",
        "updated_at",
    }
    assert body["failure_reason"] is None
    assert body["components_gate"] is None
    assert body["room_aliases"] == {}


async def test_create_persists_the_row(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    body = await api.create_study("alice", "  Oxnard warehouse  ")
    async with sessionmaker() as session:
        study = await session.get(Study, uuid.UUID(body["id"]))
    assert study is not None
    assert study.property_name == "Oxnard warehouse"  # trimmed


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"property_name": ""},
        {"property_name": "   "},
        {"property_name": "a\x00b"},
        {"property_name": "line\nbreak"},
        {"property_name": "x" * 201},
        {"property_name": 12},
        {"property_name": "ok", "unexpected": True},
        {"property_name": "ok", "org_id": "not-a-uuid"},
        # Room aliases: file-name room ids only, no loop, no self-alias.
        {"property_name": "ok", "room_aliases": {"Kitchen": "1_kitchen"}},
        {"property_name": "ok", "room_aliases": {"1_nook": "kitchen"}},
        {"property_name": "ok", "room_aliases": {"1_nook": 3}},
        {"property_name": "ok", "room_aliases": {"1_a": "1_b", "1_b": "1_a"}},
        {"property_name": "ok", "room_aliases": {"1_a": "1_a"}},
        {"property_name": "ok", "room_aliases": ["1_a"]},
        {"property_name": "ok", "room_aliases": {f"x_r{i}": "x_k" for i in range(501)}},
    ],
)
async def test_create_rejects_invalid_bodies(api: Api, payload: dict[str, Any]) -> None:
    response = await api.post("alice", "/v1/studies", payload)
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["errors"]


async def test_create_stores_room_aliases(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    aliases = {"1_breakfast_nook": "1_kitchen", "2_bed_3_shower": "2_bed_3_bath", "b_x": "x_y"}
    response = await api.post(
        "alice", "/v1/studies", {"property_name": "Harbor", "room_aliases": aliases}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["room_aliases"] == aliases
    async with sessionmaker() as session:
        study = await session.get(Study, uuid.UUID(body["id"]))
    assert study is not None and study.room_aliases == aliases
    detail = await api.get("alice", f"/v1/studies/{body['id']}")
    assert detail.json()["room_aliases"] == aliases


async def test_create_in_another_org_as_engineer(api: Api, seed: Seed) -> None:
    org = await seed.org()
    await seed.member(org, "alice", OrgRole.ENGINEER)

    response = await api.post(
        "alice", "/v1/studies", {"property_name": "Shared", "org_id": str(org)}
    )

    assert response.status_code == 201
    assert response.json()["org_id"] == str(org)


async def test_create_needs_engineer_role_in_the_org(api: Api, seed: Seed) -> None:
    org = await seed.org()
    await seed.member(org, "alice", OrgRole.VIEWER)

    response = await api.post("alice", "/v1/studies", {"property_name": "X", "org_id": str(org)})

    assert response.status_code == 403


async def test_create_in_an_org_you_do_not_belong_to_is_404(api: Api, seed: Seed) -> None:
    org = await seed.org()
    response = await api.post("alice", "/v1/studies", {"property_name": "X", "org_id": str(org)})
    assert response.status_code == 404


async def test_create_without_org_id_when_several_orgs_qualify_is_422(api: Api, seed: Seed) -> None:
    org = await seed.org()
    await seed.member(org, "alice", OrgRole.ENGINEER)  # plus the personal org

    response = await api.post("alice", "/v1/studies", {"property_name": "X"})

    assert response.status_code == 422
    assert response.json()["type"].endswith("/org-required")


async def test_create_without_org_id_ignores_orgs_where_you_are_a_viewer(
    api: Api, seed: Seed
) -> None:
    org = await seed.org()
    await seed.member(org, "alice", OrgRole.VIEWER)
    body = await api.create_study("alice")
    assert body["org_id"] == str(await seed.personal_org("alice"))


# --------------------------------------------------------------------------- list


async def test_list_shows_only_studies_of_your_orgs(api: Api, seed: Seed) -> None:
    mine = await api.create_study("alice", "Mine")
    await api.create_study("bob", "Bob's")
    shared_org = await seed.org()
    await seed.member(shared_org, "alice", OrgRole.VIEWER)
    await seed.member(shared_org, "carol", OrgRole.OWNER)
    shared = await api.create_study("carol", "Shared", org_id=str(shared_org))

    response = await api.get("alice", "/v1/studies")

    assert response.status_code == 200
    assert ids(response.json()["items"]) == [shared["id"], mine["id"]]
    assert response.json()["next_cursor"] is None


async def test_list_paginates_newest_first_without_gaps_or_repeats(api: Api) -> None:
    created = [(await api.create_study("alice", f"S{i}"))["id"] for i in range(5)]

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        response = await api.get("alice", "/v1/studies", params=params)
        assert response.status_code == 200
        page = response.json()
        assert len(page["items"]) <= 2
        seen += ids(page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert seen == list(reversed(created))


async def test_list_default_page_size_is_50(api: Api, seed: Seed) -> None:
    for i in range(51):
        await api.create_study("alice", f"S{i}")
    page = (await api.get("alice", "/v1/studies")).json()
    assert len(page["items"]) == 50
    assert page["next_cursor"] is not None


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"limit": "x"}, {"cursor": ""}])
async def test_list_rejects_bad_paging_parameters(api: Api, params: dict[str, Any]) -> None:
    response = await api.get("alice", "/v1/studies", params=params)
    assert response.status_code == 422


async def test_list_rejects_an_undecodable_cursor(api: Api) -> None:
    response = await api.get("alice", "/v1/studies", params={"cursor": "not-a-cursor"})
    assert response.status_code == 422
    assert response.json()["type"].endswith("/invalid-cursor")


async def test_list_hides_archived_studies(api: Api) -> None:
    kept = await api.create_study("alice", "Kept")
    gone = await api.create_study("alice", "Gone")
    assert (await api.delete("alice", f"/v1/studies/{gone['id']}")).status_code == 200

    page = (await api.get("alice", "/v1/studies")).json()

    assert ids(page["items"]) == [kept["id"]]


# --------------------------------------------------------------------------- get


async def test_get_includes_documents_and_latest_review_per_stage(api: Api, seed: Seed) -> None:
    study = await api.create_study("alice")
    sid = study["id"]
    upload = await api.request_upload("alice", sid, b"%PDF-1.7 appraisal")
    assert upload.status_code == 201
    await seed.stage(sid, Stage.APPRAISAL_REVIEW, 1)
    decide = f"/v1/studies/{sid}/decisions"
    approved = await api.post(
        "alice", decide, {"stage": "appraisal_review", "revision": 1, "decision": "approve"}
    )
    assert approved.status_code == 202
    await seed.stage(sid, Stage.ROOMS_REVIEW, 1)  # the workflow opens the rooms gate
    rejected = await api.post(
        "alice", decide, {"stage": "rooms_review", "revision": 1, "decision": "reject"}
    )
    assert rejected.status_code == 202
    # The rejection sent the study back to extracting at revision 2; approve both again.
    await seed.stage(sid, Stage.APPRAISAL_REVIEW, 2)
    again = await api.post(
        "alice", decide, {"stage": "appraisal_review", "revision": 2, "decision": "approve"}
    )
    assert again.status_code == 202

    response = await api.get("alice", f"/v1/studies/{sid}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == sid
    assert body["current_stage"] == "rooms_review"
    assert body["revision"] == 2
    assert [d["id"] for d in body["documents"]] == [upload.json()["document"]["id"]]
    assert body["documents"][0]["uploaded_at"] is None
    latest = [(r["stage"], r["revision"], r["decision"]) for r in body["latest_reviews"]]
    assert latest == [("appraisal_review", 2, "approve"), ("rooms_review", 1, "reject")]


async def test_get_a_new_study_has_no_documents_or_reviews(api: Api) -> None:
    study = await api.create_study("alice")
    body = (await api.get("alice", f"/v1/studies/{study['id']}")).json()
    assert body["documents"] == []
    assert body["latest_reviews"] == []


async def test_get_unknown_study_is_404(api: Api) -> None:
    response = await api.get("alice", f"/v1/studies/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON


async def test_get_with_a_malformed_id_is_422(api: Api) -> None:
    assert (await api.get("alice", "/v1/studies/not-a-uuid")).status_code == 422


# --------------------------------------------------------------------------- archive


async def test_owner_archives_a_study(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")

    response = await api.delete("alice", f"/v1/studies/{study['id']}")

    assert response.status_code == 200
    assert response.json()["status"] == "archived"
    async with sessionmaker() as session:
        count = await session.scalar(select(func.count()).select_from(Study))
        row = await session.get(Study, uuid.UUID(study["id"]))
    assert count == 1  # soft delete: the row stays
    assert row is not None and row.status.value == "archived"
    # Still readable by id.
    assert (await api.get("alice", f"/v1/studies/{study['id']}")).json()["status"] == "archived"


async def test_archive_is_idempotent_across_keys(api: Api) -> None:
    study = await api.create_study("alice")
    first = await api.delete("alice", f"/v1/studies/{study['id']}")
    second = await api.delete("alice", f"/v1/studies/{study['id']}")
    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["status"] == "archived"


@pytest.mark.parametrize("role", [OrgRole.ENGINEER, OrgRole.VIEWER])
async def test_archive_is_owner_only(api: Api, seed: Seed, role: OrgRole) -> None:
    org = await seed.org()
    await seed.member(org, "carol", OrgRole.OWNER)
    await seed.member(org, "alice", role)
    study = await api.create_study("carol", org_id=str(org))

    response = await api.delete("alice", f"/v1/studies/{study['id']}")

    assert response.status_code == 403
    assert (await api.get("carol", f"/v1/studies/{study['id']}")).json()["status"] == "draft"


async def test_archive_by_a_non_member_is_404(api: Api) -> None:
    study = await api.create_study("alice")
    assert (await api.delete("bob", f"/v1/studies/{study['id']}")).status_code == 404
