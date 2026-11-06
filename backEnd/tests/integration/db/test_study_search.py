"""``study_search``: the library's search document, kept by the database's triggers
(migration ``0033_study_search``, docs/api/STUDY_LIBRARY.md, EVAL_REPORT 92).

* every source writes through: the property name, the intake's ZIP and state, the report
  answers' taxpayer, the creator's name and email (a rename rewrites the documents of every
  study they created, and only theirs; a login that changes nothing rewrites none), the
  appraisal's identifying subject fields (insert, a correction's overwrite, delete), a
  signature's engineer; other extraction fields are not searched;
* the document is folded (lower case, accents removed) by ``study_search_fold``, one value
  per line, and a study's document goes with the study;
* a document change never moves ``studies.updated_at`` (the library's default order), and a
  stage move does not rewrite the document;
* lock order: a field write locks the study row before the document, so a field write and a
  concurrent study update queue instead of deadlocking (made real with two transactions).
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from dbtest_support import Factory, backends, waiting_on_a_lock
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import OrgRole
from basis.db.models import Extraction, Study, User
from basis.db.repositories import StudyRepository, UserRepository
from basis.db.repositories.extraction import ExtractionRepository
from basis.db.repositories.study import StudyFilters

pytestmark = pytest.mark.integration


async def document(session: AsyncSession, study_id: uuid.UUID) -> str | None:
    value = await session.scalar(
        text("SELECT document FROM study_search WHERE study_id = :id"), {"id": study_id}
    )
    return None if value is None else str(value)


async def appraisal(session: AsyncSession, study_id: uuid.UUID, revision: int = 1) -> Extraction:
    """An appraisal document and its extraction (synthetic rows), as the extraction writes."""
    document_id = await session.scalar(
        text(
            "INSERT INTO documents (study_id, kind, filename, content_type, bytes, sha256, "
            "gcs_uri) VALUES (:s, 'appraisal', 'a.pdf', 'application/pdf', 1, :h, :u) "
            "RETURNING id"
        ),
        {"s": study_id, "h": uuid.uuid4().hex * 2, "u": f"gs://b/{uuid.uuid4()}"},
    )
    return await ExtractionRepository(session).create(
        study_id=study_id,
        document_id=document_id,
        revision=revision,
        method="pipeline",
        overall_confidence=0.9,
        needs_review=False,
        raw={},
    )


async def set_field(
    session: AsyncSession, extraction: Extraction, key: str, value: str | None
) -> None:
    section, name = key.split(".", 1)
    await ExtractionRepository(session).set_field(
        extraction=extraction,
        section=section,
        field=name,
        value_text=value,
        confidence=0.9,
        source="test",
    )


async def sign(session: AsyncSession, study: Study, signer: User, name: str, email: str) -> None:
    await session.execute(
        text(
            "INSERT INTO report_signatures (study_id, revision, signed_by, signed_at, engineer, "
            "statement_version, export_sha256, reviewed_sha256, pdf_uri, pdf_sha256) VALUES "
            "(:s, 1, :u, now(), CAST(:e AS jsonb), 1, :h, :h, 'gs://b/r.pdf', :h)"
        ),
        {
            "s": study.id,
            "u": signer.id,
            "e": json.dumps({"name": name, "credentials": "PE", "email": email}),
            "h": "0" * 64,
        },
    )


async def test_every_source_writes_through(session: AsyncSession, make: Factory) -> None:
    owner = await make.user("ss-owner")  # email ss-owner@example.test, no name
    study = await make.study(owner, property_name="Café Lofts")
    # Folded on insert; the creator's email is the last study-row value.
    assert await document(session, study.id) == "cafe lofts\nss-owner@example.test"

    await session.execute(
        text(
            "UPDATE studies SET intake = CAST(:i AS jsonb), report_answers = CAST(:a AS jsonb) "
            "WHERE id = :id"
        ),
        {
            "id": study.id,
            "i": json.dumps({"zip": "93035", "state": "CA"}),
            "a": json.dumps(
                {
                    "taxpayer_name": "Okonkwo Holdings LLC",
                    "photographs": "engineer_site_visit",
                    "photographed_by": "Site Engineer",
                    "photographed_on": "2026-01-15",
                }
            ),
        },
    )
    assert await document(session, study.id) == (
        "cafe lofts\n93035\nca\nokonkwo holdings llc\nss-owner@example.test"
    )

    extraction = await appraisal(session, study.id)
    await set_field(session, extraction, "subject.property_address", "4417 Quillon Ave")
    await set_field(session, extraction, "subject.owner_of_public_record", "José Núñez")
    await set_field(session, extraction, "improvements.year_built", "1987")  # not searched
    await set_field(session, extraction, "subject.lender_client", "Big Bank")  # not searched
    doc = await document(session, study.id)
    assert doc is not None
    assert doc.endswith("jose nunez\n4417 quillon ave")  # ordered by field name
    assert "1987" not in doc and "big bank" not in doc

    # A correction overwrites the row (ON CONFLICT DO UPDATE): the old value is gone.
    await set_field(session, extraction, "subject.property_address", "4471 Quillon Ave")
    doc = await document(session, study.id)
    assert doc is not None and "4471 quillon ave" in doc and "4417" not in doc

    await sign(session, study, owner, "Bartholomew Quayle", "BQ@firm.example")
    doc = await document(session, study.id)
    assert doc is not None and doc.endswith("bartholomew quayle\nbq@firm.example")

    await session.execute(
        text("DELETE FROM extraction_fields WHERE study_id = :id AND field = 'property_address'"),
        {"id": study.id},
    )
    doc = await document(session, study.id)
    assert doc is not None and "quillon" not in doc

    await session.execute(
        text("UPDATE studies SET property_name = 'Harbor Point' WHERE id = :id"),
        {"id": study.id},
    )
    doc = await document(session, study.id)
    assert doc is not None and doc.startswith("harbor point\n")


async def test_a_studys_document_goes_with_it(session: AsyncSession, make: Factory) -> None:
    study = await make.study(property_name="Gone Soon")
    await session.execute(text("DELETE FROM studies WHERE id = :id"), {"id": study.id})
    assert await document(session, study.id) is None


async def test_a_rename_rewrites_the_creators_documents_only(
    session: AsyncSession, make: Factory
) -> None:
    alice, bob = await make.user("ss-alice"), await make.user("ss-bob")
    hers = [await make.study(alice, property_name=f"Hers {n}") for n in range(3)]
    his = await make.study(bob, property_name="His")
    users = UserRepository(session)
    await users.upsert_by_firebase_uid("ss-alice", None, "Zoë Strand")
    for study in hers:
        assert await document(session, study.id) == (
            f"hers {hers.index(study)}\nzoe strand\nss-alice@example.test"
        )
    assert await document(session, his.id) == "his\nss-bob@example.test"
    before = await session.scalar(
        text("SELECT max(xmin::text::bigint) FROM study_search WHERE study_id = :id"),
        {"id": hers[0].id},
    )
    await users.upsert_by_firebase_uid("ss-alice", None, "Zoë Strand")  # a login, no change
    after = await session.scalar(
        text("SELECT max(xmin::text::bigint) FROM study_search WHERE study_id = :id"),
        {"id": hers[0].id},
    )
    assert after == before  # last_seen_at alone does not fire the trigger


async def test_the_search_matches_through_the_repository(
    session: AsyncSession, make: Factory
) -> None:
    """The repository's word clauses against the triggers' document: every source, the
    creator (and a rename seen at once), case and accents, LIKE wildcards literal, every
    word required, the user's own firms only."""
    owner = await make.user("search-owner")
    await UserRepository(session).upsert_by_firebase_uid(
        "search-owner", "w.strand@firm.example", "Wilhelmina Strand"
    )
    other = await make.user("search-other")
    study = await make.study(owner, property_name="Harbor Point Lofts")
    extraction = await appraisal(session, study.id)
    await set_field(session, extraction, "subject.city", "Oxnard")
    await set_field(session, extraction, "subject.borrower", "Inés Varga")
    elsewhere = await make.study(other, property_name="Harbor Point Lofts")  # another firm
    plain = await make.study(owner, property_name="100% Occupied_Plaza")
    repo = StudyRepository(session)

    async def found(q: str) -> set[uuid.UUID]:
        page = await repo.list_for_user(owner.id, 100, filters=StudyFilters(q=q))
        assert page.total == len(page.items)
        return {s.id for s in page.items}

    assert await found("harbor oxnard") == {study.id}  # name + city, never the other firm
    assert await found("OXNARD  harbor") == {study.id}  # any order, case
    assert await found("ines") == {study.id}  # the query without the accent
    assert await found("INÉS") == {study.id}  # or with it
    assert await found("strand") == {study.id, plain.id}  # the creator
    assert await found("w.strand@") == {study.id, plain.id}
    assert await found("harbor fresno") == set()  # every word must match
    assert await found("100%") == {plain.id}
    assert await found("_") == {plain.id}  # literal, not "any character"
    assert await found("%") == {plain.id}
    assert await found("") == {study.id, plain.id}
    assert elsewhere.id not in await found("lofts")
    await UserRepository(session).upsert_by_firebase_uid("search-owner", None, "Mina Okafor")
    assert await found("okafor") == {study.id, plain.id}  # a rename is found at once
    assert await found("wilhelmina") == set()  # and the old name is gone
    assert await found("strand") == {study.id, plain.id}  # the email still says strand


async def test_a_member_of_two_firms_finds_in_both(session: AsyncSession, make: Factory) -> None:
    alice, bob = await make.user(), await make.user()
    mine = await make.study(alice, property_name="Quillon Commons")
    theirs = await make.study(bob, property_name="Quillon Annex")
    await make.member(await make.personal_org_id(bob), alice, OrgRole.VIEWER)
    page = await StudyRepository(session).list_for_user(
        alice.id, 10, filters=StudyFilters(q="quillon")
    )
    assert {s.id for s in page.items} == {mine.id, theirs.id}


# --------------------------------------------------------------- committed transactions


async def test_a_document_change_leaves_updated_at_and_stage_moves_leave_the_document(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    async with committed() as session, session.begin():
        study = await Factory(session).study(property_name="Order Keeper")
        study_id = study.id
    async with committed() as session:
        before = await session.scalar(
            text("SELECT updated_at FROM studies WHERE id = :id"), {"id": study_id}
        )
        version = await session.scalar(
            text("SELECT xmin::text FROM study_search WHERE study_id = :id"), {"id": study_id}
        )
    await asyncio.sleep(0.01)  # a later transaction start (now()) than the study's
    async with committed() as session, session.begin():
        extraction = await appraisal(session, study_id)
        await set_field(session, extraction, "subject.city", "Tampa")
    async with committed() as session:
        after = await session.scalar(
            text("SELECT updated_at FROM studies WHERE id = :id"), {"id": study_id}
        )
        assert after == before  # the document changed, the study did not
        moved = await session.scalar(
            text("SELECT xmin::text FROM study_search WHERE study_id = :id"), {"id": study_id}
        )
        assert moved != version  # (the document was rewritten)
    async with committed() as session, session.begin():
        await session.execute(
            text(
                "UPDATE studies SET status = 'processing', current_stage = 'extracting' "
                "WHERE id = :id"
            ),
            {"id": study_id},
        )
    async with committed() as session:
        assert (
            await session.scalar(
                text("SELECT xmin::text FROM study_search WHERE study_id = :id"),
                {"id": study_id},
            )
            == moved
        )  # a stage move does not touch the document


async def test_a_field_write_and_a_study_update_queue_instead_of_deadlocking(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    """The extraction writes a subject field and then moves the stage in one transaction;
    meanwhile the owner renames the study. Without the study row lock in
    ``study_search_refresh`` the rename would hold the study row and wait for the document
    row the field write holds, while the stage move waits for the study row: a deadlock.
    With it, the rename waits on the study row the field write took, and both commit."""
    async with committed() as session, session.begin():
        study = await Factory(session).study(property_name="Before")
        study_id = study.id
    async with committed() as worker, committed() as owner, committed() as watcher:
        await worker.begin()
        extraction = await appraisal(worker, study_id)
        await set_field(worker, extraction, "subject.city", "Austin")  # takes the study row

        async def rename() -> None:
            async with owner.begin():
                await owner.execute(
                    text("UPDATE studies SET property_name = 'After' WHERE id = :id"),
                    {"id": study_id},
                )

        renaming = asyncio.create_task(rename())
        deadline = asyncio.get_running_loop().time() + 10
        while waiting_on_a_lock(await backends(watcher)) < 1:
            assert asyncio.get_running_loop().time() < deadline, "the rename never waited"
            await asyncio.sleep(0.02)
        await worker.execute(
            text("UPDATE studies SET current_stage = 'appraisal_review' WHERE id = :id"),
            {"id": study_id},
        )
        await worker.commit()
        await asyncio.wait_for(renaming, 10)  # no DeadlockDetectedError
    async with committed() as session:
        doc = await document(session, study_id)
        assert doc is not None and doc.startswith("after\n") and doc.endswith("\naustin")


async def test_signatures_record_when_signed(session: AsyncSession, make: Factory) -> None:
    owner = await make.user("ss-signer")
    study = await make.study(owner, property_name="Signed")
    await sign(session, study, owner, "Zoë Ångström", "zoe@firm.example")
    assert await document(session, study.id) == (
        "signed\nss-signer@example.test\nzoe angstrom\nzoe@firm.example"
    )
