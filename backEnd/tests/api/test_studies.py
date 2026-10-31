"""``/v1/studies``: create, list, get, archive (S1_SPEC section 5, BUILD_PLAN 6.4).

Against the real application, a real PostgreSQL container and LocalStorage; Firebase is
replaced by ``FakeVerifier``.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from apitest_support import (
    BASE_URL,
    Api,
    FakeVerifier,
    Seed,
    api_settings,
    bearer,
    build_api_app,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.errors import PROBLEM_JSON
from basis.db.enums import OrgRole, Stage
from basis.db.models import ExtractionField, Study, StudyEvent
from basis.errors.codes import E
from basis.storage import LocalStorage

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
        "intake",  # null until PUT .../intake (docs/api/STUDY_RESULT.md)
        "intake_version",  # 0 until the first intake write (migration 0018)
        "created_at",
        "updated_at",
    }
    assert body["failure_reason"] is None
    assert body["components_gate"] is None
    assert body["room_aliases"] == {}
    assert body["intake_version"] == 0


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


# --------------------------------------------------------------------------- intake

INTAKE: dict[str, Any] = {
    "purchase_price": "650000.00",
    "acquisition_costs": [
        {"category": "title_insurance_owner", "amount": "1250.00"},
        {"category": "recording_fees", "amount": 180},
    ],
    "land_method": "appraisal_site_value",
    "appraisal_site_value": "90000.00",
    "appraisal_effective_date": "2025-03-14",
    "property_use": "residential_rental",
    "dwelling_unit_income_share": "1",
    "placed_in_service_date": "2025-05-01",
    "acquisition_date": "2025-04-15",
    "state": "NY",
    "zip": "12345",
}


def stored(intake: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """The intake as the API returns it: every field, decimals as strings."""
    document = {
        "acquisition_costs": [],
        "appraisal_site_value": None,
        "appraisal_effective_date": None,
        "land_appraisal_value": None,
        "land_appraisal_date": None,
        "assessor_land_value": None,
        "assessor_improvement_value": None,
        "flat_land_percentage": None,
        "flat_land_source": None,
        "dwelling_unit_income_share": None,
        "transient_lodging": False,
        "acquisition_date": None,
        "state": None,
        "zip": None,
        "location_source": None,
        **intake,
        **overrides,
    }
    document["acquisition_costs"] = [
        {"description": None, **c, "amount": str(Decimal(str(c["amount"])))}
        for c in document["acquisition_costs"]
    ]
    return document


async def test_create_with_intake_stores_it(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    response = await api.post("alice", "/v1/studies", {"property_name": "Harbor", "intake": INTAKE})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["intake"] == stored(INTAKE, location_source="intake")
    async with sessionmaker() as session:
        study = await session.get(Study, uuid.UUID(body["id"]))
    assert study is not None and study.intake is not None
    assert study.intake.purchase_price == Decimal("650000.00")
    assert study.intake.placed_in_service_date.isoformat() == "2025-05-01"
    assert (study.intake.state, study.intake.zip) == ("NY", "12345")
    detail = await api.get("alice", f"/v1/studies/{body['id']}")
    assert detail.json()["intake"] == body["intake"]


async def test_put_intake_sets_and_replaces(api: Api, seed: Seed) -> None:
    study = await api.create_study("alice")
    assert study["intake"] is None
    response = await api.client.put(
        f"/v1/studies/{study['id']}/intake", json=INTAKE, headers=bearer("alice")
    )
    assert response.status_code == 200, response.text
    assert response.json()["intake"] == stored(INTAKE, location_source="intake")
    replaced = {**INTAKE, "purchase_price": "700000.00", "acquisition_costs": []}
    response = await api.client.put(
        f"/v1/studies/{study['id']}/intake", json=replaced, headers=bearer("alice")
    )
    assert response.status_code == 200
    assert response.json()["intake"]["purchase_price"] == "700000.00"
    assert response.json()["intake"]["acquisition_costs"] == []
    # Idempotent by itself: the same PUT twice, no Idempotency-Key.
    again = await api.client.put(
        f"/v1/studies/{study['id']}/intake", json=replaced, headers=bearer("alice")
    )
    assert again.status_code == 200 and again.json()["intake"] == response.json()["intake"]


async def test_put_intake_fills_state_and_zip_from_the_appraisal(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    targets = await seed.outputs(study["id"])
    async with sessionmaker() as session:
        field = await session.get(ExtractionField, uuid.UUID(targets["extraction_field"]))
        assert field is not None
        session.add_all(
            [
                ExtractionField(
                    extraction_id=field.extraction_id,
                    study_id=field.study_id,
                    section="subject",
                    field="state",
                    value_text=" ca ",
                    source="stub",
                ),
                ExtractionField(
                    extraction_id=field.extraction_id,
                    study_id=field.study_id,
                    section="subject",
                    field="zip",
                    value_text="90001-1234",
                    source="stub",
                ),
            ]
        )
        await session.commit()
    body = {k: v for k, v in INTAKE.items() if k not in ("state", "zip")}
    response = await api.client.put(
        f"/v1/studies/{study['id']}/intake", json=body, headers=bearer("alice")
    )
    assert response.status_code == 200, response.text
    intake = response.json()["intake"]
    assert (intake["state"], intake["zip"], intake["location_source"]) == (
        "CA",
        "90001-1234",
        "appraisal",
    )
    # Given values win over the appraisal's.
    response = await api.client.put(
        f"/v1/studies/{study['id']}/intake", json={**body, "zip": "10001"}, headers=bearer("alice")
    )
    intake = response.json()["intake"]
    assert (intake["state"], intake["zip"], intake["location_source"]) == ("CA", "10001", "intake")


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        (
            {"acquisition_costs": [{"category": "points", "amount": "2000"}]},
            "points is not a capitalized",
        ),
        (
            {"acquisition_costs": [{"category": "appraisal_lender", "amount": "500"}]},
            "required by a lender",
        ),
        ({"appraisal_site_value": None}, "needs appraisal_site_value"),
        (
            {"land_method": "flat_percentage", "flat_land_percentage": "12"},
            "needs flat_land_source",
        ),
        ({"dwelling_unit_income_share": "0.5"}, "fails the 80 % test"),
        ({"property_use": "residential_rental", "transient_lodging": True}, "transient"),
        ({"purchase_price": 650000.123}, "decimal"),
        ({"state": "New York"}, "pattern"),
        ({"extra": 1}, "Extra inputs"),
    ],
)
async def test_put_intake_rejects_with_the_rule_in_errors(
    api: Api, overrides: dict[str, Any], fragment: str
) -> None:
    study = await api.create_study("alice")
    response = await api.client.put(
        f"/v1/studies/{study['id']}/intake", json={**INTAKE, **overrides}, headers=bearer("alice")
    )
    assert response.status_code == 422, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    problem = response.json()
    assert problem["type"].endswith("/validation")
    assert any(fragment in e["detail"] for e in problem["errors"]), problem["errors"]
    # Nothing was stored.
    assert (await api.get("alice", f"/v1/studies/{study['id']}")).json()["intake"] is None


async def test_put_intake_needs_the_engineer_role_and_a_visible_live_study(
    api: Api, seed: Seed
) -> None:
    study = await api.create_study("alice")
    await seed.member(uuid.UUID(study["org_id"]), "vera", OrgRole.VIEWER)
    forbidden = await api.client.put(
        f"/v1/studies/{study['id']}/intake", json=INTAKE, headers=bearer("vera")
    )
    assert forbidden.status_code == 403
    invisible = await api.client.put(
        f"/v1/studies/{study['id']}/intake", json=INTAKE, headers=bearer("bob")
    )
    assert invisible.status_code == 404
    missing = await api.client.put(
        f"/v1/studies/{uuid.uuid4()}/intake", json=INTAKE, headers=bearer("alice")
    )
    assert missing.status_code == 404
    await api.delete("alice", f"/v1/studies/{study['id']}")
    archived = await api.client.put(
        f"/v1/studies/{study['id']}/intake", json=INTAKE, headers=bearer("alice")
    )
    assert archived.status_code == 409
    assert archived.json()["type"].endswith("/stale-state")


# --------------------------------------------------------------------------- intake version
# Optimistic concurrency (RFC 9110 section 13.1.1): GET .../intake answers the version as a
# strong ETag; PUT .../intake with If-Match writes only while it is current.


async def put(
    api: Api, sid: str, body: dict[str, Any], *, uid: str = "alice", if_match: str | None = None
) -> httpx.Response:
    headers = bearer(uid)
    if if_match is not None:
        headers["If-Match"] = if_match
    return await api.client.put(f"/v1/studies/{sid}/intake", json=body, headers=headers)


async def intake_events(sm: async_sessionmaker[AsyncSession], sid: str) -> list[StudyEvent]:
    async with sm() as session:
        rows = await session.scalars(
            select(StudyEvent)
            .where(
                StudyEvent.study_id == uuid.UUID(sid), StudyEvent.event_type == "study.intake_set"
            )
            .order_by(StudyEvent.seq)
        )
        return list(rows.all())


async def test_get_intake_answers_the_version_as_a_strong_etag(api: Api, seed: Seed) -> None:
    study = await api.create_study("alice")
    sid = study["id"]
    empty = await api.get("alice", f"/v1/studies/{sid}/intake")
    assert empty.status_code == 200, empty.text
    assert empty.headers["etag"] == '"0"'
    assert empty.json() == {"study_id": sid, "intake": None, "intake_version": 0}
    written = await put(api, sid, INTAKE)
    assert written.status_code == 200, written.text
    assert written.headers["etag"] == '"1"'
    assert written.json()["intake_version"] == 1
    read = await api.get("alice", f"/v1/studies/{sid}/intake")
    assert read.headers["etag"] == '"1"'
    assert read.json()["intake"] == stored(INTAKE, location_source="intake")
    assert read.json()["intake_version"] == 1
    assert (await api.get("alice", f"/v1/studies/{sid}")).json()["intake_version"] == 1
    # A viewer reads it; a non-member does not see the study.
    await seed.member(uuid.UUID(study["org_id"]), "vera", OrgRole.VIEWER)
    assert (await api.get("vera", f"/v1/studies/{sid}/intake")).status_code == 200
    assert (await api.get("bob", f"/v1/studies/{sid}/intake")).status_code == 404
    assert (await api.get("alice", f"/v1/studies/{uuid.uuid4()}/intake")).status_code == 404


async def test_a_study_created_with_an_intake_starts_at_version_1(api: Api) -> None:
    response = await api.post("alice", "/v1/studies", {"property_name": "Harbor", "intake": INTAKE})
    assert response.status_code == 201, response.text
    assert response.json()["intake_version"] == 1
    read = await api.get("alice", f"/v1/studies/{response.json()['id']}/intake")
    assert read.headers["etag"] == '"1"'


async def test_put_with_the_current_etag_writes_and_answers_the_next_one(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid = (await api.create_study("alice"))["id"]
    first = await put(api, sid, INTAKE, if_match='"0"')  # the first intake, conditionally
    assert first.status_code == 200, first.text
    assert first.headers["etag"] == '"1"'
    replaced = {**INTAKE, "purchase_price": "700000.00"}
    second = await put(api, sid, replaced, if_match=first.headers["etag"])
    assert second.status_code == 200, second.text
    assert second.headers["etag"] == '"2"'
    assert second.json()["intake"]["purchase_price"] == "700000.00"
    unconditional = await put(api, sid, replaced)  # accepted without If-Match by default
    assert unconditional.status_code == 200 and unconditional.headers["etag"] == '"3"'
    events = await intake_events(sessionmaker, sid)
    assert [e.detail for e in events] == [
        {"version_before": 0, "version_after": 1, "if_match": True},
        {"version_before": 1, "version_after": 2, "if_match": True},
        {"version_before": 2, "version_after": 3, "if_match": False},
    ]
    assert events[0].before is None
    assert events[1].before is not None and events[1].before["purchase_price"] == "650000.00"
    assert events[1].after["purchase_price"] == "700000.00"


@pytest.mark.parametrize("stale", ['"0"', '"2"', 'W/"1"', "1", '"one"'])
async def test_a_stale_if_match_is_412_and_writes_nothing(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession], stale: str
) -> None:
    sid = (await api.create_study("alice"))["id"]
    assert (await put(api, sid, INTAKE)).status_code == 200  # version 1
    before = await api.get("alice", f"/v1/studies/{sid}/intake")
    response = await put(api, sid, {**INTAKE, "purchase_price": "900000.00"}, if_match=stale)
    assert response.status_code == 412, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.headers["etag"] == '"1"'  # the current tag, to re-read against
    problem = response.json()
    assert problem["type"].endswith("/precondition-failed")
    assert problem["code"] == E.STUDY_INTAKE_PRECONDITION_FAILED
    assert "Read it again" in problem["detail"] and '"1"' in problem["detail"]
    after = await api.get("alice", f"/v1/studies/{sid}/intake")
    assert after.json() == before.json()  # intake and version unchanged
    assert len(await intake_events(sessionmaker, sid)) == 1  # no event for the refusal


async def test_the_second_of_two_puts_with_the_same_if_match_is_412(api: Api) -> None:
    sid = (await api.create_study("alice"))["id"]
    tag = (await put(api, sid, INTAKE)).headers["etag"]
    mine = await put(api, sid, {**INTAKE, "purchase_price": "700000.00"}, if_match=tag)
    theirs = await put(api, sid, {**INTAKE, "purchase_price": "800000.00"}, if_match=tag)
    assert (mine.status_code, theirs.status_code) == (200, 412)
    read = (await api.get("alice", f"/v1/studies/{sid}/intake")).json()
    assert read["intake"]["purchase_price"] == "700000.00" and read["intake_version"] == 2


async def test_concurrent_puts_with_the_same_if_match_cannot_both_succeed(
    api: Api, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The compare-and-set is under the study's row lock: the second write waits for the
    first to commit, then sees the new version."""
    sid = (await api.create_study("alice"))["id"]
    tag = (await put(api, sid, INTAKE)).headers["etag"]
    prices = [f"{700000 + i}.00" for i in range(3)]  # within the pool of 5
    answers = await asyncio.gather(
        *(put(api, sid, {**INTAKE, "purchase_price": p}, if_match=tag) for p in prices)
    )
    assert sorted(r.status_code for r in answers) == [200, 412, 412]
    winner = next(p for p, r in zip(prices, answers, strict=True) if r.status_code == 200)
    read = (await api.get("alice", f"/v1/studies/{sid}/intake")).json()
    assert read["intake"]["purchase_price"] == winner and read["intake_version"] == 2
    assert len(await intake_events(sessionmaker, sid)) == 2


async def test_if_match_star_needs_an_existing_intake(api: Api) -> None:
    sid = (await api.create_study("alice"))["id"]
    refused = await put(api, sid, INTAKE, if_match="*")
    assert refused.status_code == 412, refused.text
    assert refused.headers["etag"] == '"0"'
    assert "If-Match: *" in refused.json()["detail"]
    assert (await api.get("alice", f"/v1/studies/{sid}/intake")).json()["intake"] is None
    assert (await put(api, sid, INTAKE)).status_code == 200
    replaced = await put(api, sid, {**INTAKE, "purchase_price": "700000.00"}, if_match="*")
    assert replaced.status_code == 200 and replaced.headers["etag"] == '"2"'


async def test_an_archived_study_is_still_409_with_or_without_if_match(api: Api) -> None:
    sid = (await api.create_study("alice"))["id"]
    tag = (await put(api, sid, INTAKE)).headers["etag"]
    await api.delete("alice", f"/v1/studies/{sid}")
    for if_match in (None, tag, '"0"'):
        archived = await put(api, sid, INTAKE, if_match=if_match)
        assert archived.status_code == 409, (if_match, archived.text)
        assert archived.json()["type"].endswith("/stale-state")


async def test_a_viewer_still_cannot_put_with_a_valid_if_match(api: Api, seed: Seed) -> None:
    study = await api.create_study("alice")
    await seed.member(uuid.UUID(study["org_id"]), "vera", OrgRole.VIEWER)
    assert (await put(api, study["id"], INTAKE, uid="vera", if_match='"0"')).status_code == 403
    assert (await put(api, study["id"], INTAKE, uid="bob", if_match='"0"')).status_code == 404


async def test_the_deployment_can_require_if_match(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> None:
    app = build_api_app(
        api_settings(database_url, intake_require_if_match=True), sessionmaker, storage, verifier
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        api = Api(client)
        sid = (await api.create_study("alice"))["id"]
        missing = await put(api, sid, INTAKE)
        assert missing.status_code == 428, missing.text
        assert missing.json()["type"].endswith("/precondition-required")
        assert missing.json()["code"] == E.STUDY_INTAKE_PRECONDITION_REQUIRED
        assert (await api.get("alice", f"/v1/studies/{sid}/intake")).json()["intake"] is None
        tag = (await api.get("alice", f"/v1/studies/{sid}/intake")).headers["etag"]
        accepted = await put(api, sid, INTAKE, if_match=tag)
        assert accepted.status_code == 200, accepted.text
        assert (await put(api, sid, INTAKE, if_match=tag)).status_code == 412
