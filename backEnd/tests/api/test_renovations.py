"""``GET`` and ``PUT /v1/studies/{id}/renovations`` and the assets review's age and removal
corrections (EVAL_REPORT 102, docs/api/RENOVATIONS.md): versions and ``If-Match``, each role,
the files the document names, the dates against the intake, the lock once exported, the
archived study, the timeline event, the result reading the document, and the decision route's
422s for an age or a removal it cannot apply. Synthetic rows."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from apitest_support import Api, Seed, bearer
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_decisions import assert_nothing_written, decision, study_at

from basis.db.enums import DocumentKind, OrgRole, Stage, StudyStatus
from basis.db.models import Study
from basis.db.repositories.document import DocumentRepository
from basis.db.session import session_scope

pytestmark = pytest.mark.integration

INTAKE: dict[str, Any] = {
    "purchase_price": "205000.00",
    "acquisition_costs": [],
    "land_method": "appraisal_site_value",
    "appraisal_site_value": "20000.00",
    "property_use": "residential_rental",
    "dwelling_unit_income_share": "1",
    "placed_in_service_date": "2023-04-01",
    "acquisition_date": "2023-03-10",
    "state": "NY",
}


async def put(api: Api, uid: str, path: str, body: Any, **headers: str) -> Any:
    return await api.client.put(path, json=body, headers={**bearer(uid), **headers})


async def document(seed: Seed, sid: str, kind: DocumentKind) -> str:
    async with session_scope(seed.sessionmaker) as session:
        did = uuid.uuid4()
        await DocumentRepository(session).create(
            study_id=uuid.UUID(sid),
            kind=kind,
            filename=f"{kind.value}-{did.hex[:6]}.bin",
            content_type="application/octet-stream",
            size_bytes=10,
            sha256=uuid.uuid4().hex * 2,
            gcs_uri=f"gs://basis-local/studies/{sid}/{did}/file",
            uploaded_by=None,
            document_id=did,
        )
        return str(did)


def body(invoice: str, photo: str, **over: Any) -> dict[str, Any]:
    project = {
        "key": "kitchen-2025",
        "kind": "after_service",
        "scope": "Kitchen remodel",
        "contract_date": "2025-02-10",
        "work_start_date": "2025-03-03",
        "placed_in_service_date": "2025-05-30",
        "invoice_total": "38500.00",
        "invoices": [{"document_id": invoice, "amount": "38500.00"}],
        "capitalized_amount": "38500.00",
        "capitalized_confirmed_by": "A. Preparer, CPA",
        "after_photos": [photo],
        **over,
    }
    return {"projects": [project]}


async def ready(api: Api, seed: Seed) -> tuple[str, str, str]:
    study = await api.create_study("alice", intake=INTAKE)
    sid = study["id"]
    return (
        sid,
        await document(seed, sid, DocumentKind.OTHER),
        await document(seed, sid, DocumentKind.PHOTO),
    )


async def test_the_renovations_are_versioned_and_a_timeline_event(api: Api, seed: Seed) -> None:
    sid, invoice, photo = await ready(api, seed)
    path = f"/v1/studies/{sid}/renovations"
    empty = await api.get("alice", path)
    assert empty.status_code == 200 and empty.headers["etag"] == '"0"'
    assert empty.json()["renovations"] is None
    first = await put(api, "alice", path, body(invoice, photo), **{"If-Match": '"0"'})
    assert first.status_code == 200, first.text
    assert first.json()["renovations_version"] == 1 and first.headers["etag"] == '"1"'
    stale = await put(api, "alice", path, body(invoice, photo), **{"If-Match": '"0"'})
    assert stale.status_code == 412 and stale.json()["code"] == "BASIS-STUDY-039"
    assert stale.headers["etag"] == '"1"'
    again = await put(api, "alice", path, body(invoice, photo), **{"If-Match": "*"})
    assert again.status_code == 200 and again.json()["renovations_version"] == 2
    org = await seed.personal_org("alice")
    await seed.member(org, "vic", OrgRole.VIEWER)
    seen = await api.get("vic", path)
    assert seen.json()["renovations"]["projects"][0]["key"] == "kitchen-2025"
    assert (await put(api, "vic", path, body(invoice, photo))).status_code == 403
    assert (await api.get("mallory", path)).status_code == 404
    timeline = await api.get(
        "alice", f"/v1/studies/{sid}/timeline", params={"event_type": "study.renovations_set"}
    )
    events = timeline.json()["items"]
    assert len(events) == 2 and {e["detail"]["version_after"] for e in events} == {1, 2}


async def test_files_that_are_not_the_studys_are_422(api: Api, seed: Seed) -> None:
    sid, invoice, photo = await ready(api, seed)
    path = f"/v1/studies/{sid}/renovations"
    stranger = str(uuid.uuid4())
    for bad in (body(stranger, photo), body(invoice, stranger), body(photo, invoice)):
        response = await put(api, "alice", path, bad)
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "BASIS-STUDY-040"
    assert (await api.get("alice", path)).json()["renovations_version"] == 0


async def test_dates_the_intake_contradicts_are_422(api: Api, seed: Seed) -> None:
    sid, invoice, photo = await ready(api, seed)
    early = body(
        invoice,
        photo,
        contract_date=None,
        work_start_date="2023-01-01",
        placed_in_service_date="2023-03-01",
    )
    response = await put(api, "alice", f"/v1/studies/{sid}/renovations", early)
    assert response.status_code == 422 and response.json()["code"] == "BASIS-STUDY-040"
    fields = {e["loc"][-1] for e in response.json()["errors"]}
    assert fields == {
        "projects[kitchen-2025].placed_in_service_date",
        "projects[kitchen-2025].work_start_date",
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"projects": [{"key": "x"}]},
        {"projects": [], "unknown": 1},
        {"projects": [], "acquisition_bonus_rate": "1.5"},
    ],
)
async def test_a_document_its_own_rules_refuse_is_422(api: Api, seed: Seed, bad: Any) -> None:
    sid, _, _ = await ready(api, seed)
    response = await put(api, "alice", f"/v1/studies/{sid}/renovations", bad)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("stage", "status"),
    [
        (Stage.EXPORTING, 409),
        (Stage.COMPLETED, 409),
        (Stage.ASSETS_REVIEW, 200),
        (Stage.UPLOADING, 200),
    ],
)
async def test_locked_once_exported(api: Api, seed: Seed, stage: Stage, status: int) -> None:
    sid, invoice, photo = await ready(api, seed)
    await seed.stage(sid, stage)
    response = await put(api, "alice", f"/v1/studies/{sid}/renovations", body(invoice, photo))
    assert response.status_code == status, response.text
    if status == 409:
        assert response.json()["code"] == "BASIS-STUDY-038"
        assert response.json()["type"].endswith("renovations-locked")


async def test_an_archived_study_is_stale_state(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, invoice, photo = await ready(api, seed)
    async with session_scope(sessionmaker) as session:
        await session.execute(
            update(Study).where(Study.id == uuid.UUID(sid)).values(status=StudyStatus.ARCHIVED)
        )
    response = await put(api, "alice", f"/v1/studies/{sid}/renovations", body(invoice, photo))
    assert response.status_code == 409 and response.json()["type"].endswith("stale-state")


async def test_the_result_reads_the_renovations(api: Api, seed: Seed) -> None:
    sid, invoice, photo = await ready(api, seed)
    before = (await api.get("alice", f"/v1/studies/{sid}/result")).json()
    assert "renovations" not in before
    await put(api, "alice", f"/v1/studies/{sid}/renovations", body(invoice, photo))
    after = (await api.get("alice", f"/v1/studies/{sid}/result")).json()
    assert after["renovations"]["version"] == 1
    # No asset was built with these renovations yet: the result names the reprice.
    assert "renovations_changed" in {b["code"] for b in after["blockers"]}


# --------------------------------------------------------------------------- corrections

AGE = {
    "effective_age": 2,
    "economic_life": 20,
    "age_source": "engineer_inspection",
    "age_source_detail": "site visit: new cabinets, 2023 receipt",
}


def corrected(
    target: str, after: dict[str, Any], reason: str | None = "photographs"
) -> dict[str, Any]:
    return {"entity_type": "asset", "entity_id": target, "after": after, "reason": reason}


async def test_an_age_and_a_removal_are_accepted(api: Api, seed: Seed) -> None:
    sid = await study_at(api, seed, Stage.ASSETS_REVIEW, revision=2)
    invoice = await document(seed, sid, DocumentKind.OTHER)
    photo = await document(seed, sid, DocumentKind.PHOTO)
    await put(api, "alice", f"/v1/studies/{sid}/renovations", body(invoice, photo))
    targets = await seed.outputs(sid, revision=2)
    removal = {"removed_by_project": "kitchen-2025", "disposition_date": "2025-03-03"}
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "assets_review",
            2,
            "reject",
            corrections=[corrected(targets["asset"], AGE), corrected(targets["asset"], removal)],
        ),
    )
    assert response.status_code == 202, response.text


@pytest.mark.parametrize(
    ("after", "reason", "member"),
    [
        ({**AGE, "effective_age": 25}, "r", "after"),
        ({**AGE, "age_source": "hunch"}, "r", "after"),
        (AGE, None, "reason"),
        ({"removed_by_project": "kitchen-2025"}, "r", "after"),
        ({"removed_by_project": "kitchen-2025", "disposition_date": "2025-03-03"}, " ", "reason"),
        ({"removed_by_project": "no-such", "disposition_date": "2025-03-03"}, "r", "after"),
    ],
)
async def test_an_age_or_a_removal_it_cannot_apply_is_422(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    after: dict[str, Any],
    reason: str | None,
    member: str,
) -> None:
    sid = await study_at(api, seed, Stage.ASSETS_REVIEW, revision=2)
    targets = await seed.outputs(sid, revision=2)
    response = await api.post(
        "alice",
        f"/v1/studies/{sid}/decisions",
        decision(
            "assets_review", 2, "reject", corrections=[corrected(targets["asset"], after, reason)]
        ),
    )
    assert response.status_code == 422, response.text
    assert member in {e["loc"][-1] for e in response.json()["errors"]}
    await assert_nothing_written(sessionmaker, sid, Stage.ASSETS_REVIEW, 2)
