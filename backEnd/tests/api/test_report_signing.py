"""The report's inputs and the engineer's signature (STUDY_REPORT_SPEC 11.13).

* The firm profile: owners write it whole with ``If-Match`` on its version; members read
  it; another firm 404; no profile 404 BASIS-REPORT-013; a logo that is not a PNG 422.
* The study's answers: engineers write, viewers read, ``If-Match`` 412 with the current
  ``ETag``; the write is a timeline event.
* The export step's draft carries the stored inputs.
* Signing: refused without an export (409 BASIS-REPORT-007), without the inputs (409
  BASIS-REPORT-008), with a preview hash that is not the current one (412 BASIS-REPORT-010,
  e.g. after an answer changed), for a revision that is not current, by a viewer; signed
  once: the signed PDF (the signature id, name and credentials, no DRAFT) is what ``GET
  .../report`` serves, the draft stays available, a second signature is 409 BASIS-REPORT-009,
  ``report/state`` and the timeline record it, and the row cannot be updated.
"""

from __future__ import annotations

import base64
import io
import json
import uuid
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from apitest_support import Api, Seed, bearer, sha256_hex
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.activities.export_report import build_report_files
from basis.db.enums import OrgRole, Stage
from basis.db.repositories.study_event import StudyEventRepository
from basis.db.session import session_scope
from basis.storage import LocalStorage
from tests.unit.report.support import export

pytestmark = pytest.mark.integration

PROFILE: dict[str, Any] = {
    "name": "Example Engineering",
    "address": "1 Example Way, Albany NY",
    "engineer": {
        "name": "A. Engineer",
        "credentials": "PE",
        "license_number": "123",
        "license_state": "NY",
    },
}
ANSWERS: dict[str, Any] = {
    "taxpayer_name": "Example Holdings LLC",
    "photographs": "taxpayer_supplied",
    "photographed_by": "the owner",
    "photographed_on": "2025-02-20",
}
SIGNER: dict[str, Any] = {"name": "A. Engineer", "credentials": "PE, CCSP"}


def text_of(pdf: bytes) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


async def org_of(seed: Seed, uid: str) -> uuid.UUID:
    return await seed.personal_org(uid)


async def put(api: Api, uid: str, path: str, body: Any, **headers: str) -> Any:
    return await api.client.put(path, json=body, headers={**bearer(uid), **headers})


async def exported(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    study_id: str,
    revision: int = 1,
) -> None:
    """The synthetic export stored as revision ``revision`` with the draft the export step
    writes from it (the stored inputs), and its ``export.written`` event."""
    doc = export()
    doc["study_id"] = study_id
    doc["revision"] = revision
    data = json.dumps(doc, sort_keys=True).encode()
    base = f"gs://basis/studies/{study_id}/exports/rev-{revision}"
    await storage.put(f"{base}.json", data, "application/json")
    async with session_scope(sessionmaker) as session:
        files = await build_report_files(session, storage, data, uuid.UUID(study_id), revision)
        written = {}
        for suffix, payload in files.files.items():
            await storage.put(f"{base}.{suffix}", payload, "application/octet-stream")
            written[suffix] = {"uri": f"{base}.{suffix}", "sha256": sha256_hex(payload)}
        await StudyEventRepository(session).record(
            uuid.UUID(study_id),
            "export.written",
            actor="system",
            stage=Stage.EXPORTING,
            revision=revision,
            detail={
                "uri": f"{base}.json",
                "sha256": sha256_hex(data),
                "report": {"files": written, "draft": files.draft},
            },
        )


async def preview_tag(api: Api, uid: str, study_id: str) -> str:
    response = await api.get(uid, f"/v1/studies/{study_id}/report/preview")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    tag: str = response.headers["etag"].strip('"')
    assert tag == sha256_hex(response.content)
    return tag


def sign_body(tag: str, revision: int = 1, **extra: Any) -> dict[str, Any]:
    return {
        "revision": revision,
        "reviewed_sha256": tag,
        "engineer": SIGNER,
        "attest": True,
        "statement_version": 1,
        **extra,
    }


# --------------------------------------------------------------------- the inputs


async def test_the_firm_profile_is_owned_versioned_and_private(api: Api, seed: Seed) -> None:
    org = await org_of(seed, "alice")
    path = f"/v1/orgs/{org}/profile"
    none_yet = await api.get("alice", path)
    assert none_yet.status_code == 404 and none_yet.json()["code"] == "BASIS-REPORT-013"
    first = await put(api, "alice", path, PROFILE)
    assert first.status_code == 200, first.text
    assert first.headers["etag"] == '"1"' and first.json()["version"] == 1
    read = await api.get("alice", path)
    assert read.json()["profile"]["engineer"]["license_state"] == "NY"
    stale = await put(api, "alice", path, {**PROFILE, "phone": "555"}, **{"If-Match": '"0"'})
    assert stale.status_code == 412 and stale.json()["code"] == "BASIS-REPORT-011"
    assert stale.headers["etag"] == '"1"'
    second = await put(api, "alice", path, {**PROFILE, "phone": "555"}, **{"If-Match": '"1"'})
    assert second.status_code == 200 and second.json()["version"] == 2
    await seed.member(org, "erin", OrgRole.ENGINEER)
    assert (await api.get("erin", path)).status_code == 200
    assert (await put(api, "erin", path, PROFILE)).status_code == 403
    assert (await api.get("mallory", path)).status_code == 404
    png = io.BytesIO()
    Image.new("RGB", (8, 8)).save(png, "PNG")
    logo = base64.b64encode(png.getvalue()).decode()
    with_logo = await put(api, "alice", path, {**PROFILE, "logo_png": logo})
    assert with_logo.status_code == 200 and with_logo.json()["profile"]["logo_png"] == logo
    gif = base64.b64encode(b"GIF89a....").decode()
    assert (await put(api, "alice", path, {**PROFILE, "logo_png": gif})).status_code == 422


async def test_the_study_answers_are_versioned_and_a_timeline_event(api: Api, seed: Seed) -> None:
    study = await api.create_study("alice")
    path = f"/v1/studies/{study['id']}/report-answers"
    empty = await api.get("alice", path)
    assert empty.json()["report_answers"] is None and empty.headers["etag"] == '"0"'
    first = await put(api, "alice", path, ANSWERS, **{"If-Match": '"0"'})
    assert first.status_code == 200 and first.json()["report_answers_version"] == 1
    stale = await put(api, "alice", path, ANSWERS, **{"If-Match": '"0"'})
    assert stale.status_code == 412 and stale.json()["code"] == "BASIS-REPORT-012"
    bad = await put(api, "alice", path, {**ANSWERS, "photographs": "drone"})
    assert bad.status_code == 422
    # NUL cannot be stored in JSONB and a control character never prints: 422, not 500
    # (found by the contract test).
    for value in ("Holdings\u0000LLC", "Holdings\u0082", "two\nlines", "   "):
        refused = await put(api, "alice", path, {**ANSWERS, "taxpayer_name": value})
        assert refused.status_code == 422, value
    org = await org_of(seed, "alice")
    await seed.member(org, "vic", OrgRole.VIEWER)
    assert (await api.get("vic", path)).json()["report_answers"]["taxpayer_name"]
    assert (await put(api, "vic", path, ANSWERS)).status_code == 403
    timeline = await api.get(
        "alice",
        f"/v1/studies/{study['id']}/timeline",
        params={"event_type": "study.report_answers_set"},
    )
    [event] = timeline.json()["items"]
    assert event["detail"]["version_after"] == 1 and event["after"]["photographs"]


# --------------------------------------------------------------------- signing


async def test_signing_needs_an_export_the_inputs_and_the_reviewed_preview(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    sid = study["id"]
    sign = f"/v1/studies/{sid}/report/sign"
    zero = "0" * 64
    not_exported = await api.post("alice", sign, sign_body(zero))
    assert not_exported.status_code == 409 and not_exported.json()["code"] == "BASIS-REPORT-007"
    assert (await api.get("alice", f"/v1/studies/{sid}/report/preview")).status_code == 409
    await exported(sessionmaker, storage, sid)
    state = (await api.get("alice", f"/v1/studies/{sid}/report/state")).json()
    assert state["exported"] and state["missing_for_final"] == ["firm", "study", "signature"]
    no_inputs = await api.post("alice", sign, sign_body(await preview_tag(api, "alice", sid)))
    assert no_inputs.status_code == 409 and no_inputs.json()["code"] == "BASIS-REPORT-008"
    assert "firm and study" in no_inputs.json()["detail"]
    org = await org_of(seed, "alice")
    await put(api, "alice", f"/v1/orgs/{org}/profile", PROFILE)
    await put(api, "alice", f"/v1/studies/{sid}/report-answers", ANSWERS)
    reviewed = await preview_tag(api, "alice", sid)
    # An answer changes after the review: the preview the engineer saw is not the report.
    await put(api, "alice", f"/v1/studies/{sid}/report-answers", {**ANSWERS, "taxpayer_name": "B"})
    changed = await api.post("alice", sign, sign_body(reviewed))
    assert changed.status_code == 412 and changed.json()["code"] == "BASIS-REPORT-010"
    current = changed.headers["etag"].strip('"')
    assert current == await preview_tag(api, "alice", sid) and current != reviewed
    wrong_revision = await api.post("alice", sign, sign_body(current, revision=2))
    assert wrong_revision.json()["code"] == "BASIS-REPORT-007"
    assert (await api.post("alice", sign, {**sign_body(current), "attest": False})).status_code == (
        422
    )
    assert (
        await api.post("alice", sign, {**sign_body(current), "statement_version": 2})
    ).status_code == 422
    await seed.member(org, "vic", OrgRole.VIEWER)
    assert (await api.post("vic", sign, sign_body(current))).status_code == 403
    state = (await api.get("alice", f"/v1/studies/{sid}/report/state")).json()
    assert state["signature"] is None and state["missing_for_final"] == ["signature"]


async def test_a_signed_report_is_served_recorded_and_never_replaced(
    api: Api,
    seed: Seed,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    study = await api.create_study("alice")
    sid = study["id"]
    org = await org_of(seed, "alice")
    await put(api, "alice", f"/v1/orgs/{org}/profile", PROFILE)
    await put(api, "alice", f"/v1/studies/{sid}/report-answers", ANSWERS)
    await exported(sessionmaker, storage, sid)
    # The export step's draft carries the stored inputs (and no signature).
    draft = await api.get("alice", f"/v1/studies/{sid}/report")
    draft_text = text_of(draft.content)
    assert "Example Holdings LLC" in draft_text and "Not yet signed" in draft_text
    reviewed = await preview_tag(api, "alice", sid)
    signed = await api.post("alice", f"/v1/studies/{sid}/report/sign", sign_body(reviewed))
    assert signed.status_code == 201, signed.text
    signature = signed.json()
    assert signature["revision"] == 1 and signature["reviewed_sha256"] == reviewed
    assert signature["engineer"] == {
        **SIGNER,
        "license_number": None,
        "license_state": None,
        "email": None,
    }
    final = await api.get("alice", f"/v1/studies/{sid}/report")
    assert final.status_code == 200
    assert final.headers["etag"] == f'"{signature["pdf_sha256"]}"'
    assert sha256_hex(final.content) == signature["pdf_sha256"]
    assert final.headers["content-disposition"].endswith('-report-signed.pdf"')
    final_text = text_of(final.content)
    assert f"signature {signature['id']}" in final_text
    assert "A. Engineer, PE, CCSP" in final_text and "Signed in Basis" in final_text
    assert "DRAFT" not in final_text and "Not yet signed" not in final_text
    still_draft = await api.get("alice", f"/v1/studies/{sid}/report", params={"variant": "draft"})
    assert still_draft.content == draft.content
    again = await api.post("alice", f"/v1/studies/{sid}/report/sign", sign_body(reviewed))
    assert again.status_code == 409 and again.json()["code"] == "BASIS-REPORT-009"
    # Refused before anything is rendered or stored: one signed PDF in storage.
    assert len(list((tmp_path / "objects").rglob("rev-1.report.signed.*.pdf"))) == 1
    state = (await api.get("alice", f"/v1/studies/{sid}/report/state")).json()
    assert state["missing_for_final"] == [] and state["signature"]["id"] == signature["id"]
    timeline = await api.get(
        "alice", f"/v1/studies/{sid}/timeline", params={"event_type": "report.signed"}
    )
    [event] = timeline.json()["items"]
    assert event["entity_id"] == signature["id"]
    assert event["detail"]["pdf_sha256"] == signature["pdf_sha256"]
    assert event["detail"]["signer_email"] == "alice@example.test"
    # The firm edits its profile later: the signed PDF stays what was signed.
    await put(api, "alice", f"/v1/orgs/{org}/profile", {**PROFILE, "name": "Renamed"})
    assert (await api.get("alice", f"/v1/studies/{sid}/report")).content == final.content
    async with sessionmaker() as session:
        with pytest.raises(DBAPIError, match="23001|append-only|not allowed"):
            await session.execute(text("UPDATE report_signatures SET pdf_uri = 'x'"))
