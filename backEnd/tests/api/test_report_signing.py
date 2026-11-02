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

import asyncio
import base64
import io
import json
import uuid
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from apitest_support import Api, Seed, bearer, sha256_hex
from dbtest_support import backends, queued_behind_a_lock, waiting_on_a_lock
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.activities.export_report import build_report_files
from basis.db.enums import OrgRole, Stage
from basis.db.models import Org, ReportSignature, Study
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
    # The signer as their sign-in names them, next to the id and the attested name.
    assert (signature["signed_by_name"], signature["signed_by_email"]) == (
        "Alice",
        "alice@example.test",
    )
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


# --------------------------------------------------------------------- races (EVAL_REPORT 62)


async def signable(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> tuple[str, uuid.UUID, str]:
    """A study whose current revision can be signed: ``(study id, org id, preview tag)``."""
    sid = (await api.create_study("alice"))["id"]
    org = await org_of(seed, "alice")
    await put(api, "alice", f"/v1/orgs/{org}/profile", PROFILE)
    await put(api, "alice", f"/v1/studies/{sid}/report-answers", ANSWERS)
    await exported(sessionmaker, storage, sid)
    return sid, org, await preview_tag(api, "alice", sid)


def study_lock(sid: str) -> Any:
    return select(Study).where(Study.id == uuid.UUID(sid)).with_for_update()


async def signatures(sm: async_sessionmaker[AsyncSession], sid: str) -> list[ReportSignature]:
    async with sm() as session:
        rows = await session.scalars(
            select(ReportSignature).where(ReportSignature.study_id == uuid.UUID(sid))
        )
        return list(rows.all())


async def test_two_signings_at_once_record_one_signature_and_one_pdf(
    api: Api,
    seed: Seed,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Two engineers sign the same preview at the same moment. ``sign_report`` takes the
    study's row lock before any check, so the second waits, re-reads under the lock, finds
    the signature and is refused before it renders or stores anything: one 201, one 409
    BASIS-REPORT-009, one row, one signed PDF in storage, one ``report.signed`` event.
    Without the lock (verified by removing ``FOR UPDATE`` from ``lock_study``) both render,
    both store a PDF and the second insert dies on the unique key: a 500 and an orphan."""
    sid, org, tag = await signable(api, seed, storage, sessionmaker)
    await seed.member(org, "erin", OrgRole.ENGINEER)
    sign = f"/v1/studies/{sid}/report/sign"
    first, second = await queued_behind_a_lock(
        sessionmaker,
        study_lock(sid),
        lambda: api.post("alice", sign, sign_body(tag)),
        lambda: api.post("erin", sign, sign_body(tag)),
    )
    assert (first.status_code, second.status_code) == (201, 409), (first.text, second.text)
    assert second.json()["code"] == "BASIS-REPORT-009"
    [row] = await signatures(sessionmaker, sid)
    assert str(row.id) == first.json()["id"] and str(row.signed_by) == str(
        (await seed.user("alice")).id
    )
    assert len(list((tmp_path / "objects").rglob("rev-1.report.signed.*.pdf"))) == 1
    timeline = await api.get(
        "alice", f"/v1/studies/{sid}/timeline", params={"event_type": "report.signed"}
    )
    assert len(timeline.json()["items"]) == 1
    state = (await api.get("alice", f"/v1/studies/{sid}/report/state")).json()
    assert state["signature"]["id"] == first.json()["id"]


async def test_two_profile_writes_with_one_etag_at_once_write_one(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The firm profile's compare-and-set is under the firm's row lock: two owners who read
    version 1 and both send ``If-Match: "1"`` at once get one 200 (version 2) and one 412
    carrying the current tag; the loser's profile is not written."""
    org = await org_of(seed, "alice")
    await seed.member(org, "owen", OrgRole.OWNER)
    path = f"/v1/orgs/{org}/profile"
    tag = (await put(api, "alice", path, PROFILE)).headers["etag"]
    assert tag == '"1"'
    lock = select(Org).where(Org.id == org).with_for_update()
    mine, theirs = await queued_behind_a_lock(
        sessionmaker,
        lock,
        lambda: put(api, "alice", path, {**PROFILE, "phone": "111"}, **{"If-Match": tag}),
        lambda: put(api, "owen", path, {**PROFILE, "phone": "222"}, **{"If-Match": tag}),
    )
    assert (mine.status_code, theirs.status_code) == (200, 412), (mine.text, theirs.text)
    assert mine.headers["etag"] == '"2"' and theirs.headers["etag"] == '"2"'
    assert theirs.json()["code"] == "BASIS-REPORT-011"
    read = (await api.get("alice", path)).json()
    assert (read["version"], read["profile"]["phone"]) == (2, "111")


async def test_two_answers_writes_with_one_etag_at_once_write_one(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The same for the study's report answers, under the study's row lock: one 200, one
    412 BASIS-REPORT-012 with the current tag, one ``study.report_answers_set`` event."""
    sid = (await api.create_study("alice"))["id"]
    org = await org_of(seed, "alice")
    await seed.member(org, "erin", OrgRole.ENGINEER)
    path = f"/v1/studies/{sid}/report-answers"
    tag = (await put(api, "alice", path, ANSWERS)).headers["etag"]
    mine, theirs = await queued_behind_a_lock(
        sessionmaker,
        study_lock(sid),
        lambda: put(api, "alice", path, {**ANSWERS, "taxpayer_name": "A"}, **{"If-Match": tag}),
        lambda: put(api, "erin", path, {**ANSWERS, "taxpayer_name": "B"}, **{"If-Match": tag}),
    )
    assert (mine.status_code, theirs.status_code) == (200, 412), (mine.text, theirs.text)
    assert theirs.json()["code"] == "BASIS-REPORT-012" and theirs.headers["etag"] == '"2"'
    read = (await api.get("alice", path)).json()
    assert (read["report_answers_version"], read["report_answers"]["taxpayer_name"]) == (2, "A")
    timeline = await api.get(
        "alice", f"/v1/studies/{sid}/timeline", params={"event_type": "study.report_answers_set"}
    )
    assert [e["detail"]["version_after"] for e in timeline.json()["items"]] == [1, 2]


async def test_an_answers_write_queued_before_the_signing_makes_it_412(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """An engineer saves an answer while another signs, and the save takes the study's row
    lock first: the signing then renders from the new answers under the same lock, the
    preview the signer reviewed is no longer the report, and nothing is signed (412
    BASIS-REPORT-010 with the current tag). The answer is kept."""
    sid, org, tag = await signable(api, seed, storage, sessionmaker)
    await seed.member(org, "erin", OrgRole.ENGINEER)
    answers = f"/v1/studies/{sid}/report-answers"
    saved, signed = await queued_behind_a_lock(
        sessionmaker,
        study_lock(sid),
        lambda: put(api, "erin", answers, {**ANSWERS, "taxpayer_name": "Changed LLC"}),
        lambda: api.post("alice", f"/v1/studies/{sid}/report/sign", sign_body(tag)),
    )
    assert (saved.status_code, signed.status_code) == (200, 412), (saved.text, signed.text)
    assert signed.json()["code"] == "BASIS-REPORT-010"
    assert signed.headers["etag"].strip('"') == await preview_tag(api, "alice", sid) != tag
    assert await signatures(sessionmaker, sid) == []
    assert (await api.get("alice", answers)).json()["report_answers"]["taxpayer_name"] == (
        "Changed LLC"
    )


async def test_inputs_written_after_signing_are_reported_not_rendered(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """After signing, the answers and the firm profile stay writable (the profile serves
    every study), the signed PDF is what was signed, and ``report/state`` says which inputs
    changed since: the signature keeps the versions it was rendered with and the state
    compares them with the current ones (docs/api/REPORT.md, "After signing")."""
    sid, org, tag = await signable(api, seed, storage, sessionmaker)
    signed = await api.post("alice", f"/v1/studies/{sid}/report/sign", sign_body(tag))
    assert signed.status_code == 201, signed.text
    signature = signed.json()
    assert (signature["firm_profile_version"], signature["report_answers_version"]) == (1, 1)
    state_path = f"/v1/studies/{sid}/report/state"
    assert (await api.get("alice", state_path)).json()["changed_since_signing"] == []
    final = (await api.get("alice", f"/v1/studies/{sid}/report")).content

    answers = await put(
        api, "alice", f"/v1/studies/{sid}/report-answers", {**ANSWERS, "taxpayer_name": "Later"}
    )
    assert answers.status_code == 200
    state = (await api.get("alice", state_path)).json()
    assert state["changed_since_signing"] == ["study"]
    assert (state["report_answers_version"], state["firm_profile_version"]) == (2, 1)
    profile = await put(api, "alice", f"/v1/orgs/{org}/profile", {**PROFILE, "name": "Renamed"})
    assert profile.status_code == 200
    state = (await api.get("alice", state_path)).json()
    assert state["changed_since_signing"] == ["firm", "study"]
    assert state["signature"]["id"] == signature["id"] and state["missing_for_final"] == []
    # The signed PDF and its hash are untouched; the preview (a draft) follows the inputs.
    assert (await api.get("alice", f"/v1/studies/{sid}/report")).content == final
    assert await preview_tag(api, "alice", sid) != tag
    event = (
        await api.get(
            "alice", f"/v1/studies/{sid}/timeline", params={"event_type": "report.signed"}
        )
    ).json()["items"][0]
    assert (event["detail"]["firm_profile_version"], event["detail"]["report_answers_version"]) == (
        1,
        1,
    )
    # A signature recorded before the versions were kept: the state says "unknown".
    async with sessionmaker() as session, session.begin():
        await session.execute(text("ALTER TABLE report_signatures DISABLE TRIGGER USER"))
        await session.execute(
            text(
                "UPDATE report_signatures SET firm_profile_version = NULL, "
                "report_answers_version = NULL"
            )
        )
        await session.execute(text("ALTER TABLE report_signatures ENABLE TRIGGER USER"))
    state = (await api.get("alice", state_path)).json()
    assert state["changed_since_signing"] is None and state["signature"]["id"] == signature["id"]


# ------------------------------------------------- the step verified end to end (EVAL_REPORT 72)


async def signed(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> tuple[str, uuid.UUID, dict[str, Any]]:
    """A study whose revision 1 is signed: ``(study id, org id, the signature)``."""
    sid, org, tag = await signable(api, seed, storage, sessionmaker)
    response = await api.post("alice", f"/v1/studies/{sid}/report/sign", sign_body(tag))
    assert response.status_code == 201, response.text
    signature: dict[str, Any] = response.json()
    return sid, org, signature


async def test_a_retried_or_double_submitted_signing_records_one_signature(
    api: Api,
    seed: Seed,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """The web client sends one ``Idempotency-Key`` per signing and keeps it on a retry. The
    same key while the first request is still inside the handler (held here on the study's
    row lock) is refused at once (409 ``idempotency-key-in-flight``) without running the
    handler; after the first completes, the same key replays its 201 (``Idempotent-Replayed``)
    and a different body under the key is 422. One row, one PDF, one event throughout."""
    sid, _org, tag = await signable(api, seed, storage, sessionmaker)
    sign, key = f"/v1/studies/{sid}/report/sign", "sign-once"
    # Hold the study's row lock: the first request reserves its key, then waits inside the
    # handler; the double submit under the same key is answered without waiting on anything.
    async with sessionmaker() as locker, locker.begin():
        await locker.execute(study_lock(sid))
        pending = asyncio.create_task(api.post("alice", sign, sign_body(tag), key=key))
        deadline = asyncio.get_running_loop().time() + 10
        while waiting_on_a_lock(await backends(locker)) < 1:
            assert asyncio.get_running_loop().time() < deadline, "the signing never took the lock"
            await asyncio.sleep(0.02)
        during = await api.post("alice", sign, sign_body(tag), key=key)
    first = await pending
    assert (first.status_code, during.status_code) == (201, 409), (first.text, during.text)
    assert during.json()["type"].endswith("/idempotency-key-in-flight")
    replay = await api.post("alice", sign, sign_body(tag), key=key)
    assert replay.status_code == 201 and replay.headers.get("idempotent-replayed") == "true"
    assert replay.json()["id"] == first.json()["id"]
    other = await api.post(
        "alice", sign, {**sign_body(tag), "engineer": {**SIGNER, "name": "B"}}, key=key
    )
    assert other.status_code == 422, other.text
    # A fresh key after the signature: the ordinary refusal, nothing written.
    again = await api.post("alice", sign, sign_body(tag))
    assert again.status_code == 409 and again.json()["code"] == "BASIS-REPORT-009"
    [row] = await signatures(sessionmaker, sid)
    assert str(row.id) == first.json()["id"]
    assert len(list((tmp_path / "objects").rglob("rev-1.report.signed.*.pdf"))) == 1
    timeline = await api.get(
        "alice", f"/v1/studies/{sid}/timeline", params={"event_type": "report.signed"}
    )
    assert len(timeline.json()["items"]) == 1


async def test_another_firm_cannot_see_preview_sign_or_fetch_the_report(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Firm isolation on every report route: a member of another firm gets 404 on the state,
    the preview, the files and the signing, nothing is written; a viewer of the firm reads
    the state, the preview and the signed file but may not sign (403); an engineer of the
    firm may."""
    sid, org, signature = await signed(api, seed, storage, sessionmaker)
    base = f"/v1/studies/{sid}/report"
    for path in (f"{base}/state", f"{base}/preview", base):
        response = await api.get("mallory", path)
        assert response.status_code == 404, (path, response.text)
    refused = await api.post("mallory", f"{base}/sign", sign_body("0" * 64))
    assert refused.status_code == 404
    await seed.member(org, "vic", OrgRole.VIEWER)
    state = await api.get("vic", f"{base}/state")
    assert state.status_code == 200 and state.json()["signature"]["id"] == signature["id"]
    assert (await api.get("vic", f"{base}/preview")).status_code == 200
    final = await api.get("vic", base)
    assert final.status_code == 200 and sha256_hex(final.content) == signature["pdf_sha256"]
    assert (await api.post("vic", f"{base}/sign", sign_body("0" * 64))).status_code == 403
    assert len(await signatures(sessionmaker, sid)) == 1


async def test_an_archived_study_keeps_its_signed_report_and_cannot_be_signed(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Archiving neither hides nor changes a signed report: the state and the signed PDF still
    read. An archived unsigned study cannot be previewed or signed (409 BASIS-REPORT-007),
    and its answers cannot change (409 ``stale-state``); nothing is written."""
    sid, _org, signature = await signed(api, seed, storage, sessionmaker)
    assert (await api.delete("alice", f"/v1/studies/{sid}")).status_code == 200
    state = (await api.get("alice", f"/v1/studies/{sid}/report/state")).json()
    assert state["signature"]["id"] == signature["id"] and state["changed_since_signing"] == []
    final = await api.get("alice", f"/v1/studies/{sid}/report")
    assert final.status_code == 200 and sha256_hex(final.content) == signature["pdf_sha256"]

    other, _org2, tag = await signable(api, seed, storage, sessionmaker)
    assert (await api.delete("alice", f"/v1/studies/{other}")).status_code == 200
    preview = await api.get("alice", f"/v1/studies/{other}/report/preview")
    assert preview.status_code == 409 and preview.json()["code"] == "BASIS-REPORT-007"
    refused = await api.post("alice", f"/v1/studies/{other}/report/sign", sign_body(tag))
    assert refused.status_code == 409 and refused.json()["code"] == "BASIS-REPORT-007"
    answers = await put(api, "alice", f"/v1/studies/{other}/report-answers", ANSWERS)
    assert answers.status_code == 409 and answers.json()["code"] == "BASIS-STUDY-004"
    assert await signatures(sessionmaker, other) == []
    state = (await api.get("alice", f"/v1/studies/{other}/report/state")).json()
    assert state["signature"] is None and state["exported"]


async def test_a_new_revision_is_signed_on_its_own_and_the_earlier_signed_pdf_stays(
    api: Api,
    seed: Seed,
    storage: LocalStorage,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """The recovery path after an input changed following the signature: the study moves to
    revision 2 (a reopen at the assets review; ``tests/lifecycle/test_reopen.py`` runs the
    workflow, this test places the rows the way it leaves them) and revision 2 is exported.
    Then: the state is revision 2's (unsigned, nothing missing but the signature, no
    ``changed_since_signing``), signing revision 1 is refused as not current (409
    BASIS-REPORT-007), ``GET .../report`` serves revision 2's draft by default and revision
    1's signed PDF by number, unchanged; revision 2 is signed with its own preview, and both
    signed files then serve, each with its own sha256; the revision history lists both."""
    sid, _org, first = await signed(api, seed, storage, sessionmaker)
    rev1_pdf = (await api.get("alice", f"/v1/studies/{sid}/report")).content
    assert sha256_hex(rev1_pdf) == first["pdf_sha256"]
    await put(api, "alice", f"/v1/studies/{sid}/report-answers", {**ANSWERS, "taxpayer_name": "B"})
    assert (await api.get("alice", f"/v1/studies/{sid}/report/state")).json()[
        "changed_since_signing"
    ] == ["study"]
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text(
                "UPDATE studies SET revision = 2, current_stage = 'completed', "
                "status = 'completed' WHERE id = :id"
            ),
            {"id": uuid.UUID(sid)},
        )
    await exported(sessionmaker, storage, sid, revision=2)

    state = (await api.get("alice", f"/v1/studies/{sid}/report/state")).json()
    assert (state["revision"], state["signature"]) == (2, None)
    assert state["changed_since_signing"] is None
    assert state["missing_for_final"] == ["signature"] and state["exported"]
    stale = await api.post("alice", f"/v1/studies/{sid}/report/sign", sign_body("0" * 64, 1))
    assert stale.status_code == 409 and stale.json()["code"] == "BASIS-REPORT-007"
    latest = await api.get("alice", f"/v1/studies/{sid}/report")
    assert latest.headers["content-disposition"].endswith('rev-2-report.pdf"')
    assert "DRAFT" in text_of(latest.content) and "B" in text_of(latest.content)
    earlier = await api.get("alice", f"/v1/studies/{sid}/report", params={"revision": 1})
    assert earlier.headers["content-disposition"].endswith('rev-1-report-signed.pdf"')
    assert earlier.content == rev1_pdf

    tag = await preview_tag(api, "alice", sid)
    second = await api.post("alice", f"/v1/studies/{sid}/report/sign", sign_body(tag, 2))
    assert second.status_code == 201, second.text
    assert second.json()["revision"] == 2 and second.json()["id"] != first["id"]
    assert second.json()["pdf_sha256"] != first["pdf_sha256"]
    state = (await api.get("alice", f"/v1/studies/{sid}/report/state")).json()
    assert state["signature"]["id"] == second.json()["id"]
    assert state["changed_since_signing"] == [] and state["missing_for_final"] == []
    now_latest = await api.get("alice", f"/v1/studies/{sid}/report")
    assert now_latest.headers["content-disposition"].endswith('rev-2-report-signed.pdf"')
    assert sha256_hex(now_latest.content) == second.json()["pdf_sha256"]
    assert (
        await api.get("alice", f"/v1/studies/{sid}/report", params={"revision": 1})
    ).content == rev1_pdf
    assert len(list((tmp_path / "objects").rglob("rev-1.report.signed.*.pdf"))) == 1
    assert len(list((tmp_path / "objects").rglob("rev-2.report.signed.*.pdf"))) == 1
    history = (await api.get("alice", f"/v1/studies/{sid}/revisions")).json()
    by_revision = {item["revision"]: item for item in history["items"]}
    assert by_revision[1]["signature"]["id"] == first["id"]
    assert by_revision[2]["signature"]["id"] == second.json()["id"]
    assert [r["revision"] for r in history["items"] if r["current"]] == [2]
