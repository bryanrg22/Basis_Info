"""The retention purge on PostgreSQL with ``LocalStorage`` (docs/api/RETENTION_AND_ALERTS.md
section 1).

* Selection: a completed or archived study of a firm with a rule, whose row last changed
  more than ``retention_days`` ago and that is not purged yet; never a study still in
  progress, of a firm without a rule, or too young; oldest change first; ``--org-id`` and
  ``--limit`` narrow it.
* A dry run lists what would go and changes nothing: every object and row is untouched,
  no event, ``purged_at`` null.
* The purge deletes exactly the client files (documents, photos, rendered pages, analyses,
  room reconciliations, model payloads) and keeps every object under ``exports/`` (the
  export JSON, the assets CSV, the calls transcript, the report PDF, the signed report),
  every database row (documents, extractions, events), sets ``purged_at`` and appends
  ``study.purged`` with the counts; another study's objects are untouched.
* A second run is a no-op: the study is no longer selected and nothing changes.
* A document whose object was already gone is reported, not an error; a storage failure
  leaves the study unpurged (no mark, no event) and the CLI exits 1 for it while purging
  the others.
* The CLI (``run_purge``) prints one JSON line per study and a summary.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dbtest_support import Factory
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import DocumentKind, Stage, StudyStatus
from basis.db.models import Document, DocumentPage, Org, Study, StudyEvent
from basis.db.repositories import DocumentRepository
from basis.db.session import session_scope
from basis.retention import purge_study, select_candidates, study_prefix
from basis.retention.__main__ import run_purge
from basis.retention.purge import PURGED_EVENT
from basis.storage import LocalStorage, ObjectNotFound, StorageError

pytestmark = pytest.mark.integration

BUCKET = "basis-local"
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
# The set_updated_at() trigger stamps now() on every UPDATE, so a study cannot be backdated;
# the tests move the rule's clock forward instead (``now``).
LATER = NOW + timedelta(days=31)


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "objects")


async def set_rule(session: AsyncSession, org_id: uuid.UUID, days: int | None) -> None:
    await session.execute(update(Org).where(Org.id == org_id).values(retention_days=days))


async def set_status(session: AsyncSession, study: Study, status: StudyStatus) -> None:
    stage = Stage.COMPLETED if status is StudyStatus.COMPLETED else study.current_stage
    await session.execute(
        update(Study).where(Study.id == study.id).values(status=status, current_stage=stage)
    )
    await session.refresh(study)


async def finished_study(
    make: Factory, session: AsyncSession, *, status: StudyStatus = StudyStatus.COMPLETED
) -> Study:
    study = await make.study(stage=Stage.COMPLETED)
    await set_status(session, study, status)
    return study


async def populate(
    session: AsyncSession, storage: LocalStorage, study: Study
) -> dict[str, list[str]]:
    """A study's objects in storage with their rows: two documents (with a page), analyses,
    rooms, model payloads, and the exports. Returns ``{"client": [...], "exports": [...]}``."""
    prefix = study_prefix(BUCKET, study.id)
    client: list[str] = []
    documents = DocumentRepository(session)
    for kind, name in ((DocumentKind.APPRAISAL, "appraisal.pdf"), (DocumentKind.PHOTO, "k.jpg")):
        document_id = uuid.uuid4()
        uri = f"{prefix}{document_id}/{name}"
        await documents.create(
            study_id=study.id,
            kind=kind,
            filename=name,
            content_type="application/octet-stream",
            size_bytes=3,
            sha256=uuid.uuid4().hex * 2,
            gcs_uri=uri,
            uploaded_by=None,
            document_id=document_id,
        )
        storage.write(uri, b"doc")
        client.append(uri)
        if kind is DocumentKind.APPRAISAL:
            page = f"{prefix}{document_id}/pages/1.png"
            session.add(DocumentPage(document_id=document_id, page_no=1, gcs_uri=page))
            storage.write(page, b"png")
            client.append(page)
    for name in (
        "analyses/x/r1.json",
        "rooms/y/r1.json",
        "llm/z.request.json",
        "llm/z.response.json",
    ):
        storage.write(prefix + name, b"{}")
        client.append(prefix + name)
    exports = [
        prefix + name
        for name in (
            "exports/rev-1.json",
            "exports/rev-1.assets.csv",
            "exports/rev-1.calls.jsonl",
            "exports/rev-1.report.pdf",
            "exports/rev-1.schedule.csv",
            f"exports/rev-1.report.signed.{uuid.uuid4()}.pdf",
        )
    ]
    for uri in exports:
        storage.write(uri, b"keep")
    await session.flush()
    return {"client": sorted(client), "exports": sorted(exports)}


async def events(session: AsyncSession, study_id: uuid.UUID, kind: str) -> list[StudyEvent]:
    stmt = select(StudyEvent).where(StudyEvent.study_id == study_id, StudyEvent.event_type == kind)
    return list((await session.scalars(stmt)).all())


# --------------------------------------------------------------------------- selection


async def test_the_rule_selects_finished_old_studies_of_firms_with_a_rule(
    session: AsyncSession, make: Factory
) -> None:
    owner = await make.user()
    org = await make.personal_org_id(owner)
    await set_rule(session, org, 30)
    completed = await make.study(owner, stage=Stage.COMPLETED)
    await set_status(session, completed, StudyStatus.COMPLETED)
    archived = await make.study(owner, stage=Stage.ROOMS_REVIEW)
    await set_status(session, archived, StudyStatus.ARCHIVED)
    in_progress = await make.study(owner, stage=Stage.ROOMS_REVIEW)  # waiting_review
    other_owner = await make.user()  # a firm without a rule
    no_rule = await make.study(other_owner, stage=Stage.COMPLETED)
    await set_status(session, no_rule, StudyStatus.COMPLETED)

    selected = await select_candidates(session, now=LATER)
    assert sorted(c.study.id for c in selected) == sorted([completed.id, archived.id])
    assert {c.retention_days for c in selected} == {30}
    for candidate in selected:
        assert candidate.eligible_since == candidate.study.updated_at + timedelta(days=30)
    assert in_progress.id not in {c.study.id for c in selected}

    # Too young: the clock has not passed updated_at + 30 days.
    assert await select_candidates(session, now=NOW + timedelta(days=29)) == []
    # Narrowed to a firm and limited.
    one = await select_candidates(session, now=LATER, org_id=org, limit=1)
    assert len(one) == 1 and one[0].org_id == org
    assert (
        await select_candidates(session, now=LATER, org_id=await make.personal_org_id(other_owner))
        == []
    )
    with pytest.raises(ValueError):
        await select_candidates(session, now=LATER, limit=0)


async def test_a_null_rule_selects_nothing(session: AsyncSession, make: Factory) -> None:
    study = await finished_study(make, session)
    await set_rule(session, study.org_id, None)
    assert await select_candidates(session, now=LATER + timedelta(days=36500)) == []


# --------------------------------------------------------------------------- dry run


async def test_a_dry_run_changes_nothing(
    session: AsyncSession, make: Factory, storage: LocalStorage
) -> None:
    study = await finished_study(make, session)
    await set_rule(session, study.org_id, 30)
    objects = await populate(session, storage, study)
    (candidate,) = await select_candidates(session, now=LATER)

    report = await purge_study(session, storage, BUCKET, candidate, dry_run=True, now=LATER)

    assert report.status == "dry_run"
    assert report.deleted == objects["client"] and report.kept == objects["exports"]
    assert (report.documents, report.pages, report.missing) == (2, 1, [])
    for uri in objects["client"] + objects["exports"]:
        assert await storage.read(uri)  # still there
    await session.refresh(study)
    assert study.purged_at is None
    assert await events(session, study.id, PURGED_EVENT) == []
    # Still selected.
    assert [c.study.id for c in await select_candidates(session, now=LATER)] == [study.id]


# --------------------------------------------------------------------------- the purge


async def test_the_purge_deletes_the_client_files_and_keeps_the_deliverables(
    session: AsyncSession, make: Factory, storage: LocalStorage
) -> None:
    study = await finished_study(make, session)
    await set_rule(session, study.org_id, 30)
    objects = await populate(session, storage, study)
    other = await finished_study(make, session)
    other_objects = await populate(session, storage, other)
    rows_before = await session.scalar(select(func.count()).select_from(Document))
    (candidate,) = [
        c for c in await select_candidates(session, now=LATER) if c.study.id == study.id
    ]

    report = await purge_study(session, storage, BUCKET, candidate, dry_run=False, now=LATER)

    assert report.status == "purged"
    assert report.deleted == objects["client"] and report.kept == objects["exports"]
    for uri in objects["client"]:
        with pytest.raises(ObjectNotFound):
            await storage.read(uri)
    for uri in objects["exports"]:
        assert await storage.read(uri) == b"keep"
    for uri in other_objects["client"] + other_objects["exports"]:
        assert await storage.read(uri)  # the other study is untouched
    assert await storage.list(study_prefix(BUCKET, study.id)) == objects["exports"]
    # Every row survives: documents, pages, the study.
    assert await session.scalar(select(func.count()).select_from(Document)) == rows_before
    assert await session.scalar(select(func.count()).select_from(DocumentPage)) == 2
    await session.refresh(study)
    assert study.purged_at == LATER and study.status is StudyStatus.COMPLETED
    (event,) = await events(session, study.id, PURGED_EVENT)
    assert event.actor_type == "system" and event.stage is Stage.COMPLETED
    assert event.detail["retention_days"] == 30
    assert event.detail["documents"] == 2 and event.detail["pages"] == 1
    assert event.detail["objects_deleted"] == len(objects["client"])
    assert event.detail["objects_kept"] == objects["exports"]
    assert event.detail["missing"] == []
    assert event.detail["rule_from"] == study.updated_at.isoformat()


async def test_a_second_run_is_a_no_op(
    session: AsyncSession, make: Factory, storage: LocalStorage
) -> None:
    study = await finished_study(make, session)
    await set_rule(session, study.org_id, 30)
    objects = await populate(session, storage, study)
    (candidate,) = await select_candidates(session, now=LATER)
    await purge_study(session, storage, BUCKET, candidate, dry_run=False, now=LATER)
    await session.refresh(study)
    first_mark = study.purged_at

    assert await select_candidates(session, now=LATER + timedelta(days=400)) == []
    # Even forced through, the purge finds nothing to delete and does not re-mark.
    report = await purge_study(
        session, storage, BUCKET, candidate, dry_run=False, now=LATER + timedelta(days=1)
    )
    assert report.deleted == [] and report.kept == objects["exports"]
    await session.refresh(study)
    assert study.purged_at == first_mark
    assert len(await events(session, study.id, PURGED_EVENT)) == 2  # the record of both runs


async def test_an_object_already_gone_is_reported_not_fatal(
    session: AsyncSession, make: Factory, storage: LocalStorage
) -> None:
    study = await finished_study(make, session)
    await set_rule(session, study.org_id, 30)
    objects = await populate(session, storage, study)
    gone = objects["client"][0]
    await storage.delete(gone)
    (candidate,) = await select_candidates(session, now=LATER)

    report = await purge_study(session, storage, BUCKET, candidate, dry_run=False, now=LATER)

    assert report.missing == [gone] and gone not in report.deleted
    (event,) = await events(session, study.id, PURGED_EVENT)
    assert event.detail["missing"] == [gone]


class FailingStorage(LocalStorage):
    def __init__(self, root: Path, fail_on: str) -> None:
        super().__init__(root)
        self.fail_on = fail_on

    async def delete(self, uri: str) -> None:
        if uri.endswith(self.fail_on):
            raise StorageError("permission denied")
        await super().delete(uri)


async def test_a_storage_failure_leaves_the_study_unpurged(
    session: AsyncSession, make: Factory, tmp_path: Path
) -> None:
    storage = FailingStorage(tmp_path / "objects", fail_on="k.jpg")
    study = await finished_study(make, session)
    await set_rule(session, study.org_id, 30)
    await populate(session, storage, study)
    (candidate,) = await select_candidates(session, now=LATER)

    with pytest.raises(StorageError):
        await purge_study(session, storage, BUCKET, candidate, dry_run=False, now=LATER)

    await session.refresh(study)
    assert study.purged_at is None
    assert await events(session, study.id, PURGED_EVENT) == []
    assert [c.study.id for c in await select_candidates(session, now=LATER)] == [study.id]


# --------------------------------------------------------------------------- the CLI


async def test_run_purge_prints_a_line_per_study_and_a_summary(
    committed: async_sessionmaker[AsyncSession], storage: LocalStorage, tmp_path: Path
) -> None:
    failing = FailingStorage(tmp_path / "objects", fail_on="k.jpg")
    async with session_scope(committed) as session:
        make = Factory(session)
        good = await finished_study(make, session)
        await set_rule(session, good.org_id, 30)
        good_objects = await populate(session, storage, good)
        bad = await finished_study(make, session)
        await set_rule(session, bad.org_id, 30)
        await populate(session, storage, bad)
        await session.execute(text("SELECT pg_sleep(0.01)"))
    out = io.StringIO()

    dry = await run_purge(
        committed, storage, BUCKET, dry_run=True, org_id=None, limit=100, out=out, now=LATER
    )
    assert dry == 0
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line["status"] for line in lines[:-1]] == ["dry_run", "dry_run"]
    assert lines[-1] == {
        "summary": True,
        "dry_run": True,
        "candidates": 2,
        "would_purge": 2,
        "failed": 0,
    }
    for uri in good_objects["client"]:
        assert await storage.read(uri)

    out = io.StringIO()
    status = await run_purge(
        committed, failing, BUCKET, dry_run=False, org_id=None, limit=100, out=out, now=LATER
    )
    assert status == 1  # one study failed
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    by_study = {line["study_id"]: line for line in lines[:-1]}
    assert by_study[str(good.id)]["status"] == "failed"  # both have a k.jpg; both fail on it
    assert by_study[str(bad.id)]["status"] == "failed"
    assert lines[-1]["failed"] == 2 and lines[-1]["purged"] == 0

    out = io.StringIO()
    status = await run_purge(
        committed, storage, BUCKET, dry_run=False, org_id=good.org_id, limit=100, out=out, now=LATER
    )
    assert status == 0
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line["study_id"] for line in lines[:-1]] == [str(good.id)]
    assert lines[0]["status"] == "purged" and lines[0]["objects_kept"] == 6
    assert lines[-1] == {
        "summary": True,
        "dry_run": False,
        "candidates": 1,
        "purged": 1,
        "failed": 0,
    }
    async with committed() as session:
        purged_at = await session.scalar(select(Study.purged_at).where(Study.id == good.id))
        assert purged_at == LATER
        assert await session.scalar(select(Study.purged_at).where(Study.id == bad.id)) is None


def test_the_cli_parses_its_arguments() -> None:
    from basis.retention.__main__ import build_parser

    args = build_parser().parse_args(["purge", "--dry-run", "--limit", "5"])
    assert (args.command, args.dry_run, args.limit, args.org_id) == ("purge", True, 5, None)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["purge", "--org-id", "not-a-uuid"])
