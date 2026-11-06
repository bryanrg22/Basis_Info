"""Each engineer's own signer details and how signing uses them (EVAL_REPORT 91;
docs/api/SIGNERS.md).

* ``GET``/``PUT /v1/me/signer``: yours only, versioned (``If-Match``, 412 BASIS-REPORT-016),
  validated (a license number only with its state, the state a licensing jurisdiction, values
  trimmed, one line); any signed-in user, a firm or not; no route writes another user's.
* The preview names the reader who may sign with their saved details; a viewer's never.
* Signing prints the signer's saved details (``engineer`` may be left out; a different one is
  409 BASIS-REPORT-015), records their version and the letterhead; without saved details the
  ``engineer`` sent is printed as before, unless the deployment requires saved details (409
  BASIS-REPORT-014); with neither, 409 BASIS-REPORT-014. Never the firm profile's engineer.
* Two engineers of one firm sign different revisions, each with their own details.
* ``changed_since_signing`` lists ``signer`` while the signer's saved details differ from the
  signature's, not after an edit that is undone, and not for another engineer's edit.
* An edit between the preview and the signing is the preview no longer being the report (412).
* The members list shows each member's details, read-only.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, build_api_app
from dbtest_support import queued_behind_a_lock
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import OrgRole
from basis.db.models import User
from basis.errors.codes import E
from basis.storage import LocalStorage
from tests.api.test_report_signing import (
    ANSWERS,
    PROFILE,
    SIGNER,
    exported,
    org_of,
    preview_tag,
    put,
    sign_body,
    signatures,
    text_of,
)

pytestmark = pytest.mark.integration

ME = "/v1/me/signer"
ALICE: dict[str, Any] = {
    "name": "Alice Licensee",
    "credentials": "PE",
    "license_number": "PE-012345",
    "license_state": "NY",
}
ERIN: dict[str, Any] = {
    "name": "Erin Engineer",
    "credentials": "PE, CCSP",
    "license_number": "98765",
    "license_state": "NJ",
}


def bare_sign(tag: str, revision: int = 1) -> dict[str, Any]:
    """A signing that names no engineer: the signer's saved details are printed."""
    body = sign_body(tag, revision)
    del body["engineer"]
    return body


async def save(api: Api, uid: str, details: dict[str, Any], **headers: str) -> httpx.Response:
    return await put(api, uid, ME, details, **headers)


async def ready(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> tuple[str, uuid.UUID]:
    """A study of alice's firm whose revision 1 is exported with the firm profile (its
    deprecated engineer named "A. Engineer") and the answers saved."""
    sid = (await api.create_study("alice"))["id"]
    org = await org_of(seed, "alice")
    await put(api, "alice", f"/v1/orgs/{org}/profile", PROFILE)
    await put(api, "alice", f"/v1/studies/{sid}/report-answers", ANSWERS)
    await exported(sessionmaker, storage, sid)
    return sid, org


async def state(api: Api, uid: str, sid: str) -> dict[str, Any]:
    response = await api.get(uid, f"/v1/studies/{sid}/report/state")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# --------------------------------------------------------------------- the route


async def test_your_details_are_versioned_and_yours(api: Api, seed: Seed) -> None:
    none_yet = await api.get("alice", ME)
    assert none_yet.status_code == 200 and none_yet.headers["etag"] == '"0"'
    assert none_yet.json()["signer"] is None and none_yet.json()["version"] == 0
    assert none_yet.json()["updated_at"] is None
    # "*" means "over saved details": nothing is saved yet.
    star = await save(api, "alice", ALICE, **{"If-Match": "*"})
    assert star.status_code == 412 and star.json()["code"] == E.REPORT_SIGNER_PRECONDITION_FAILED
    first = await save(api, "alice", ALICE, **{"If-Match": '"0"'})
    assert first.status_code == 200, first.text
    assert first.headers["etag"] == '"1"' and first.json()["signer"] == ALICE
    assert first.json()["user_id"] == str((await seed.user("alice")).id)
    stale = await save(api, "alice", {**ALICE, "credentials": "PE, CCSP"}, **{"If-Match": '"0"'})
    assert stale.status_code == 412 and stale.headers["etag"] == '"1"'
    assert stale.json()["code"] == E.REPORT_SIGNER_PRECONDITION_FAILED
    second = await save(api, "alice", {**ALICE, "credentials": "PE, CCSP"}, **{"If-Match": '"1"'})
    assert second.status_code == 200 and second.json()["version"] == 2
    unconditional = await save(api, "alice", ALICE)
    assert unconditional.json()["version"] == 3
    # Another user, in the same firm or not, reads and writes only their own.
    org = await org_of(seed, "alice")
    await seed.member(org, "owen", OrgRole.OWNER)
    assert (await api.get("owen", ME)).json()["signer"] is None
    assert (await save(api, "owen", ERIN)).json()["version"] == 1
    assert (await api.get("alice", ME)).json()["signer"] == ALICE
    # No route takes another user's id: /v1/me/signer is the caller's, whatever the role.
    assert (await api.client.get(f"{ME}/{uuid.uuid4()}")).status_code in (401, 404, 405)


async def test_a_user_with_no_firm_may_save_details(invite_only_api: Api) -> None:
    saved = await save(invite_only_api, "newcomer", ALICE)
    assert saved.status_code == 200 and saved.json()["version"] == 1


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"license_state": "ny"}, "lower case"),
        ({"license_state": "XX"}, "not a jurisdiction"),
        ({"license_state": "AS"}, "American Samoa has no NCEES board"),
        ({"license_state": "NYC"}, "three letters"),
        ({"license_state": None}, "a number without its state"),
        ({"license_number": None}, "a state without its number"),
        ({"license_number": "   "}, "blank number"),
        ({"license_number": "1" * 51}, "number over 50"),
        ({"name": ""}, "empty name"),
        ({"name": "   "}, "blank name"),
        ({"name": "A\u0000B"}, "NUL"),
        ({"name": "two\nlines"}, "two lines"),
        ({"name": "x" * 501}, "name over 500"),
        ({"credentials": None}, "no credentials"),
        ({"seal": "x"}, "an unknown field"),
    ],
)
async def test_invalid_details_are_refused_and_nothing_is_written(
    api: Api, change: dict[str, Any], why: str
) -> None:
    refused = await save(api, "alice", {**ALICE, **change})
    assert refused.status_code == 422, (why, refused.text)
    assert (await api.get("alice", ME)).json()["version"] == 0, why


@pytest.mark.parametrize("state_code", ["DC", "PR", "GU", "VI", "MP", "CA", "WY"])
async def test_every_kind_of_licensing_jurisdiction_is_accepted(api: Api, state_code: str) -> None:
    saved = await save(api, "alice", {**ALICE, "license_state": state_code})
    assert saved.status_code == 200 and saved.json()["signer"]["license_state"] == (state_code)


async def test_values_are_trimmed_and_the_license_is_optional(api: Api) -> None:
    padded = {"name": "  Alice Licensee ", "credentials": " CCSP ", "license_number": None}
    saved = await save(api, "alice", padded)
    assert saved.status_code == 200, saved.text
    assert saved.json()["signer"] == {
        "name": "Alice Licensee",
        "credentials": "CCSP",
        "license_number": None,
        "license_state": None,
    }


# --------------------------------------------------------------------- the preview


async def test_the_preview_names_the_reader_who_may_sign(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, org = await ready(api, seed, storage, sessionmaker)
    preview = f"/v1/studies/{sid}/report/preview"
    # No saved details: the firm's deprecated engineer, as before (the regression path).
    before = (await api.get("alice", preview)).content
    assert "A. Engineer, PE" in text_of(before)
    await save(api, "alice", ALICE)
    mine = text_of((await api.get("alice", preview)).content)
    assert "Alice Licensee, PE" in mine and "A. Engineer" not in mine
    # A viewer cannot sign: their saved details are never the preparer.
    await seed.member(org, "vic", OrgRole.VIEWER)
    await save(api, "vic", {**ERIN, "name": "Vic Viewer"})
    viewer = (await api.get("vic", preview)).content
    assert "Vic Viewer" not in text_of(viewer) and viewer == before
    # Rendering stays deterministic per reader.
    assert await preview_tag(api, "alice", sid) == await preview_tag(api, "alice", sid)


# --------------------------------------------------------------------- signing


async def test_signing_prints_the_saved_details_and_records_them(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, _org = await ready(api, seed, storage, sessionmaker)
    await save(api, "alice", ALICE)
    tag = await preview_tag(api, "alice", sid)
    signed = await api.post("alice", f"/v1/studies/{sid}/report/sign", bare_sign(tag))
    assert signed.status_code == 201, signed.text
    signature = signed.json()
    assert signature["engineer"] == {**ALICE, "email": None}
    assert signature["signer_details_version"] == 1
    assert signature["letterhead"] == {
        "name": PROFILE["name"],
        "address": PROFILE["address"],
        "phone": None,
        "logo_sha256": None,
    }
    final = text_of((await api.get("alice", f"/v1/studies/{sid}/report")).content)
    assert "Alice Licensee, PE" in final and "License PE-012345 (NY)" in final
    assert "A. Engineer" not in final  # the firm's deprecated engineer is never signed
    event = (
        await api.get(
            "alice", f"/v1/studies/{sid}/timeline", params={"event_type": "report.signed"}
        )
    ).json()["items"][0]
    assert event["detail"]["signer_details"] == "saved"
    assert event["detail"]["signer_details_version"] == 1
    assert event["detail"]["letterhead"]["name"] == PROFILE["name"]
    current = await state(api, "alice", sid)
    assert current["signer"] == ALICE and current["changed_since_signing"] == []


async def test_a_sent_engineer_must_be_the_saved_details(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, _org = await ready(api, seed, storage, sessionmaker)
    await save(api, "alice", ALICE)
    tag = await preview_tag(api, "alice", sid)
    sign = f"/v1/studies/{sid}/report/sign"
    for other in (
        SIGNER,  # what the client typed: another name
        {**ALICE, "license_number": "999"},
        {**ALICE, "license_state": "NJ"},
        {**ALICE, "credentials": "PE, CCSP"},
        {k: v for k, v in ALICE.items() if not k.startswith("license")},  # license left out
    ):
        refused = await api.post("alice", sign, {**sign_body(tag), "engineer": other})
        assert refused.status_code == 409, (other, refused.text)
        assert refused.json()["code"] == E.REPORT_SIGNER_DETAILS_MISMATCH
    assert await signatures(sessionmaker, sid) == []
    # The same details, padded as a form might send them, sign.
    same = {**{k: f" {v} " for k, v in ALICE.items()}, "license_state": "NY"}
    signed = await api.post("alice", sign, {**sign_body(tag), "engineer": same})
    assert signed.status_code == 201, signed.text
    assert signed.json()["engineer"] == {**ALICE, "email": None}


async def test_without_saved_details_the_sent_engineer_is_signed_as_before(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, _org = await ready(api, seed, storage, sessionmaker)
    tag = await preview_tag(api, "alice", sid)
    sign = f"/v1/studies/{sid}/report/sign"
    # Nothing saved and nothing sent: there is nothing to sign with.
    nothing = await api.post("alice", sign, bare_sign(tag))
    assert nothing.status_code == 409 and nothing.json()["code"] == E.REPORT_SIGNER_DETAILS_MISSING
    assert await signatures(sessionmaker, sid) == []
    signed = await api.post("alice", sign, sign_body(tag))
    assert signed.status_code == 201, signed.text
    assert signed.json()["signer_details_version"] is None
    assert signed.json()["engineer"]["name"] == SIGNER["name"]
    event = (
        await api.get(
            "alice", f"/v1/studies/{sid}/timeline", params={"event_type": "report.signed"}
        )
    ).json()["items"][0]
    assert event["detail"]["signer_details"] == "entered"
    # Saving afterwards the very details typed at signing is not a change; others are.
    current = await state(api, "alice", sid)
    assert current["signer"] is None and current["changed_since_signing"] == []
    await save(api, "alice", {**SIGNER, "license_number": None})
    assert (await state(api, "alice", sid))["changed_since_signing"] == []
    await save(api, "alice", ALICE)
    assert (await state(api, "alice", sid))["changed_since_signing"] == ["signer"]


async def test_the_deployment_can_require_saved_details(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    seed: Seed,
) -> None:
    app = build_api_app(
        api_settings(database_url, report_signer_details_required=True),
        sessionmaker,
        storage,
        verifier,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        api = Api(client)
        sid, _org = await ready(api, seed, storage, sessionmaker)
        tag = await preview_tag(api, "alice", sid)
        sign = f"/v1/studies/{sid}/report/sign"
        typed = await api.post("alice", sign, sign_body(tag))
        assert typed.status_code == 409 and typed.json()["code"] == (
            E.REPORT_SIGNER_DETAILS_MISSING
        )
        assert "signer details" in typed.json()["detail"]
        assert await signatures(sessionmaker, sid) == []
        await save(api, "alice", ALICE)
        # The preview now names alice: the tag reviewed before saving is not the report.
        stale = await api.post("alice", sign, bare_sign(tag))
        assert stale.status_code == 412 and stale.json()["code"] == E.REPORT_CHANGED
        signed = await api.post("alice", sign, bare_sign(await preview_tag(api, "alice", sid)))
        assert signed.status_code == 201, signed.text
        assert signed.json()["signer_details_version"] == 1


async def test_an_edit_between_the_preview_and_the_signing_is_a_changed_report(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, _org = await ready(api, seed, storage, sessionmaker)
    await save(api, "alice", ALICE)
    tag = await preview_tag(api, "alice", sid)
    await save(api, "alice", {**ALICE, "credentials": "PE, CCSP"})  # another tab
    changed = await api.post("alice", f"/v1/studies/{sid}/report/sign", bare_sign(tag))
    assert changed.status_code == 412 and changed.json()["code"] == E.REPORT_CHANGED
    assert await signatures(sessionmaker, sid) == []


async def test_two_engineers_sign_different_revisions_each_with_their_own_details(
    api: Api, seed: Seed, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Alice signs revision 1, Erin revision 2 (placed as the reopen leaves it, as
    ``test_a_new_revision_is_signed_on_its_own...`` does). Each signature and signed PDF
    carries its signer; Erin's later edit marks revision 2 changed and nothing of Alice's."""
    sid, org = await ready(api, seed, storage, sessionmaker)
    await seed.member(org, "erin", OrgRole.ENGINEER)
    await save(api, "alice", ALICE)
    await save(api, "erin", ERIN)
    first = await api.post(
        "alice", f"/v1/studies/{sid}/report/sign", bare_sign(await preview_tag(api, "alice", sid))
    )
    assert first.status_code == 201, first.text
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text(
                "UPDATE studies SET revision = 2, current_stage = 'completed', "
                "status = 'completed' WHERE id = :id"
            ),
            {"id": uuid.UUID(sid)},
        )
    await exported(sessionmaker, storage, sid, revision=2)
    erin_tag = await preview_tag(api, "erin", sid)
    assert erin_tag != await preview_tag(api, "alice", sid)  # each preview names its reader
    # Erin cannot sign with alice's preview: it is not the report she would sign.
    wrong = await api.post(
        "erin", f"/v1/studies/{sid}/report/sign", bare_sign(await preview_tag(api, "alice", sid), 2)
    )
    assert wrong.status_code == 412 and wrong.json()["code"] == E.REPORT_CHANGED
    second = await api.post("erin", f"/v1/studies/{sid}/report/sign", bare_sign(erin_tag, 2))
    assert second.status_code == 201, second.text
    assert first.json()["engineer"]["name"] == "Alice Licensee"
    assert second.json()["engineer"]["name"] == "Erin Engineer"
    rev1 = text_of(
        (await api.get("alice", f"/v1/studies/{sid}/report", params={"revision": 1})).content
    )
    rev2 = text_of((await api.get("alice", f"/v1/studies/{sid}/report")).content)
    assert "Alice Licensee" in rev1 and "Erin Engineer" not in rev1
    assert "Erin Engineer, PE, CCSP" in rev2 and "License 98765 (NJ)" in rev2
    assert "Alice Licensee" not in rev2
    current = await state(api, "alice", sid)
    assert current["signature"]["signed_by_name"] == "Erin"
    assert current["signer"] == ERIN and current["changed_since_signing"] == []
    # Alice edits her details: revision 2 (Erin's) is not affected.
    await save(api, "alice", {**ALICE, "license_number": "PE-000001"})
    assert (await state(api, "alice", sid))["changed_since_signing"] == []
    # Erin renews her license: revision 2 now differs from what she signed.
    await save(api, "erin", {**ERIN, "license_number": "11111"})
    changed = await state(api, "alice", sid)
    assert changed["changed_since_signing"] == ["signer"]
    assert changed["signature"]["engineer"]["license_number"] == "98765"  # what was signed
    assert changed["signer"]["license_number"] == "11111"  # what she has now
    # Undone, it is no longer a change: the values are compared, not the versions.
    await save(api, "erin", ERIN)
    assert (await state(api, "alice", sid))["changed_since_signing"] == []


async def test_the_members_list_shows_each_members_details_read_only(api: Api, seed: Seed) -> None:
    org = await org_of(seed, "alice")
    await seed.member(org, "erin", OrgRole.ENGINEER)
    await save(api, "erin", ERIN)
    members = (await api.get("alice", f"/v1/orgs/{org}/members")).json()["items"]
    by_email = {m["email"]: m for m in members}
    assert by_email["erin@example.test"]["signer"] == ERIN
    assert by_email["alice@example.test"]["signer"] is None
    erin = (await seed.user("erin")).id
    role = await api.post(
        "alice", f"/v1/orgs/{org}/members/{erin}/role", {"role": OrgRole.VIEWER.value}
    )
    assert role.status_code == 200 and role.json()["signer"] == ERIN
    # An owner writing "their" details writes their own, never the member's.
    await save(api, "alice", {**ERIN, "name": "Not Erin"})
    assert (await api.get("erin", ME)).json()["signer"] == ERIN


async def test_two_first_saves_with_one_etag_at_once_write_one(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Two tabs save the user's first details with ``If-Match: "0"`` at once. The write takes
    the user's row lock before it reads the version, so the second waits, reads version 1 and
    is refused (412 with the current tag); without the lock both would insert and the second
    would die on the primary key (a 500)."""
    user = await seed.user("alice")
    lock = select(User).where(User.id == user.id).with_for_update()
    mine, theirs = await queued_behind_a_lock(
        sessionmaker,
        lock,
        lambda: save(api, "alice", ALICE, **{"If-Match": '"0"'}),
        lambda: save(api, "alice", ERIN, **{"If-Match": '"0"'}),
    )
    assert (mine.status_code, theirs.status_code) == (200, 412), (mine.text, theirs.text)
    assert theirs.headers["etag"] == '"1"'
    assert theirs.json()["code"] == E.REPORT_SIGNER_PRECONDITION_FAILED
    assert (await api.get("alice", ME)).json()["signer"] == ALICE
