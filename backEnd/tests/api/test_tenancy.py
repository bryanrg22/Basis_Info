"""Tenant isolation across every scoped route (OWASP API Security Top 10, API1: broken
object-level authorization).

Two firms, each with an owner, a study and its outputs. ``mallory`` owns a firm of her own
and asks for ``alice``'s resources:

* every operation of the OpenAPI document whose path names a study, a firm or an
  invitation (``{study_id}``, ``{org_id}``, ``{invitation_id}``) answers 404 to a member of
  another firm, with a body identical to the one for an id that does not exist, so ids
  cannot be probed. The walk reads the document, and :data:`BODIES` must name every such
  operation: a new scoped route fails this test until it is listed (with a valid body, so a
  422 cannot stand in for the missing check);
* a member's own study or firm in the path does not reach another firm's rows named by
  the second id (a document, a photo, an object, a member, an invitation): 404, and a
  filter by another study's asset returns nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Final

import httpx
import pytest
from apitest_support import (
    BASE_URL,
    COMPLETE_INTAKE,
    Api,
    FakeVerifier,
    Seed,
    api_settings,
    bearer,
    build_api_app,
    fill_path,
    new_key,
    operations,
)
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from workflowtest_support import FakeWorkflowService

from basis.api.idempotency import IDEMPOTENCY_HEADER
from basis.db.enums import Stage
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

UPLOAD: Final = {
    "filename": "a.pdf",
    "content_type": "application/pdf",
    "bytes": 3,
    "sha256": "a" * 64,
    "kind": "appraisal",
}
# Every scoped operation and a body that passes validation (None: no body).
BODIES: Final[dict[str, Any]] = {
    "GET /v1/studies/{study_id}": None,
    "DELETE /v1/studies/{study_id}": None,
    "POST /v1/studies/{study_id}/unarchive": None,
    "POST /v1/studies/{study_id}/documents": UPLOAD,
    "POST /v1/studies/{study_id}/documents/{document_id}/complete": None,
    "GET /v1/studies/{study_id}/documents/{document_id}/content": None,
    "GET /v1/studies/{study_id}/documents/{document_id}/pdf": None,
    "GET /v1/studies/{study_id}/extraction": None,
    "POST /v1/studies/{study_id}/decisions": {
        "stage": "appraisal_review",
        "revision": 1,
        "decision": "approve",
    },
    "GET /v1/studies/{study_id}/evidence": None,
    "GET /v1/studies/{study_id}/photos/{document_id}/objects": None,
    "POST /v1/studies/{study_id}/photos/{document_id}/objects": {
        "kind": "point",
        "label": "window",
        "point": {"x": 0.5, "y": 0.5},
    },
    "DELETE /v1/studies/{study_id}/photos/{document_id}/objects/{object_id}": None,
    "POST /v1/studies/{study_id}/start": None,
    "GET /v1/studies/{study_id}/workflow": None,
    "POST /v1/studies/{study_id}/cancel": None,
    "POST /v1/studies/{study_id}/retry": None,
    "GET /v1/studies/{study_id}/fields": None,
    "GET /v1/studies/{study_id}/rooms": None,
    "GET /v1/studies/{study_id}/assets": None,
    "PUT /v1/studies/{study_id}/intake": COMPLETE_INTAKE,
    "GET /v1/studies/{study_id}/intake": None,
    "GET /v1/studies/{study_id}/result": None,
    "GET /v1/studies/{study_id}/report": None,
    "GET /v1/studies/{study_id}/report/state": None,
    "GET /v1/studies/{study_id}/report/preview": None,
    "POST /v1/studies/{study_id}/report/sign": {
        "revision": 1,
        "reviewed_sha256": "0" * 64,
        "engineer": {"name": "A. Engineer", "credentials": "PE"},
        "attest": True,
    },
    "GET /v1/studies/{study_id}/report-answers": None,
    "PUT /v1/studies/{study_id}/report-answers": {
        "taxpayer_name": "T. Payer",
        "photographs": "engineer_site_visit",
        "photographed_by": "A. Engineer",
        "photographed_on": "2025-05-01",
    },
    "GET /v1/studies/{study_id}/spend": None,
    "GET /v1/studies/{study_id}/calls": None,
    "GET /v1/studies/{study_id}/timeline": None,
    "GET /v1/studies/{study_id}/timeline/steps": None,
    "GET /v1/studies/{study_id}/audit": None,
    "GET /v1/studies/{study_id}/assets/reprice": None,
    "POST /v1/studies/{study_id}/assets/reprice": {
        "revision": 1,
        "fingerprint": "0" * 64,
        "reason": "cross-firm probe",
    },
    "GET /v1/orgs/{org_id}/profile": None,
    "PUT /v1/orgs/{org_id}/profile": {"name": "Other Firm"},
    "GET /v1/orgs/{org_id}/members": None,
    "GET /v1/orgs/{org_id}/activity": None,
    "POST /v1/orgs/{org_id}/members/{user_id}/role": {"role": "engineer"},
    "DELETE /v1/orgs/{org_id}/members/{user_id}": None,
    "GET /v1/orgs/{org_id}/invitations": None,
    "POST /v1/orgs/{org_id}/invitations": {"email": "probe@example.test", "role": "engineer"},
    "DELETE /v1/orgs/{org_id}/invitations/{invitation_id}": None,
    "POST /v1/invitations/{invitation_id}/accept": None,
    "POST /v1/invitations/{invitation_id}/decline": None,
}
SCOPE_PARAMS: Final = ("{study_id}", "{org_id}", "{invitation_id}")


@dataclass(frozen=True, slots=True)
class Firm:
    """One firm's owner and the ids of what they own."""

    uid: str
    ids: dict[str, str]  # path parameter name -> id


def scoped_operations(app: FastAPI) -> list[tuple[str, str]]:
    return [
        (method, template)
        for method, template in operations(app)
        if any(param in template for param in SCOPE_PARAMS)
    ]


async def firm_of(api: Api, seed: Seed, uid: str) -> Firm:
    study = await api.create_study(uid, f"{uid}'s property")
    await seed.stage(study["id"], Stage.APPRAISAL_REVIEW)
    outputs = await seed.outputs(study["id"])
    org_id = study["org_id"]
    invitation = await api.post(
        uid,
        f"/v1/orgs/{org_id}/invitations",
        {"email": f"{uid}-guest@example.test", "role": "viewer"},
    )
    assert invitation.status_code == 201, invitation.text
    me = await api.get(uid, "/v1/me")
    return Firm(
        uid,
        {
            "study_id": study["id"],
            "org_id": org_id,
            "document_id": outputs["photo"],
            "object_id": str(uuid.uuid4()),
            "user_id": me.json()["user"]["id"],
            "invitation_id": invitation.json()["id"],
            "asset_id": outputs["asset"],
        },
    )


@pytest.fixture
def api_app(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> FastAPI:
    """The conftest app plus a workflow service, so the workflow routes reach their access
    check instead of answering 503 ``temporal-not-configured`` first."""
    return build_api_app(
        api_settings(database_url), sessionmaker, storage, verifier, FakeWorkflowService()
    )


@pytest.fixture
async def http(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client


@pytest.fixture
async def alice(api: Api, seed: Seed) -> Firm:
    return await firm_of(api, seed, "alice")


@pytest.fixture
async def mallory(api: Api, seed: Seed) -> Firm:
    return await firm_of(api, seed, "mallory")


async def send(
    http: httpx.AsyncClient, uid: str, method: str, path: str, body: Any
) -> httpx.Response:
    headers = {**bearer(uid), IDEMPOTENCY_HEADER: new_key()}
    return await http.request(method, path, json=body, headers=headers)


def comparable(response: httpx.Response) -> dict[str, Any]:
    problem = response.json()
    problem.pop("request_id", None)
    problem.pop("instance", None)
    return dict(problem)


async def test_every_scoped_operation_is_listed_with_a_body(api_app: FastAPI) -> None:
    documented = {f"{m} {t}" for m, t in scoped_operations(api_app)}
    assert documented == set(BODIES), {
        "undocumented in BODIES": sorted(documented - set(BODIES)),
        "not in the OpenAPI document": sorted(set(BODIES) - documented),
    }


async def test_another_firms_ids_are_404_exactly_like_missing_ones(
    api_app: FastAPI, http: httpx.AsyncClient, alice: Firm, mallory: Firm
) -> None:
    for method, template in scoped_operations(api_app):
        body = BODIES[f"{method} {template}"]
        hidden = await send(http, mallory.uid, method, fill_path(template, alice.ids), body)
        missing = await send(http, mallory.uid, method, fill_path(template), body)
        assert hidden.status_code == 404, (method, template, hidden.text)
        assert missing.status_code == 404, (method, template, missing.text)
        assert comparable(hidden) == comparable(missing), (method, template)


async def test_own_scope_does_not_reach_another_firms_rows(
    http: httpx.AsyncClient, alice: Firm, mallory: Firm
) -> None:
    """Mallory's own study or firm in the path, Alice's ids after it."""
    mine = {**alice.ids, "study_id": mallory.ids["study_id"], "org_id": mallory.ids["org_id"]}
    for method, template in (
        ("POST", "/v1/studies/{study_id}/documents/{document_id}/complete"),
        ("GET", "/v1/studies/{study_id}/documents/{document_id}/content"),
        ("GET", "/v1/studies/{study_id}/documents/{document_id}/pdf"),
        ("GET", "/v1/studies/{study_id}/photos/{document_id}/objects"),
        ("POST", "/v1/studies/{study_id}/photos/{document_id}/objects"),
        ("DELETE", "/v1/studies/{study_id}/photos/{document_id}/objects/{object_id}"),
        ("POST", "/v1/orgs/{org_id}/members/{user_id}/role"),
        ("DELETE", "/v1/orgs/{org_id}/members/{user_id}"),
        ("DELETE", "/v1/orgs/{org_id}/invitations/{invitation_id}"),
    ):
        body = BODIES[f"{method} {template}"]
        response = await send(http, mallory.uid, method, fill_path(template, mine), body)
        assert response.status_code == 404, (method, template, response.text)
    # A filter by another study's asset: nothing, not that asset.
    evidence = await http.get(
        f"/v1/studies/{mallory.ids['study_id']}/evidence",
        params={"asset_id": alice.ids["asset_id"]},
        headers=bearer(mallory.uid),
    )
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["items"] == []
    # And Alice still sees her own study and firm.
    assert (
        await http.get(f"/v1/studies/{alice.ids['study_id']}", headers=bearer("alice"))
    ).status_code == 200
    members = await http.get(f"/v1/orgs/{alice.ids['org_id']}/members", headers=bearer("alice"))
    assert [m["user_id"] for m in members.json()["items"]] == [alice.ids["user_id"]]
