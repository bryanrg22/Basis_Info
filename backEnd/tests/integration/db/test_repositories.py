"""Repository behavior against real PostgreSQL (S1_SPEC §3, §4, §5).

Visibility by org membership, keyset pagination, document duplicates, user upsert with
a personal org, idempotency key lifecycle, and the outbox relay operations.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from dbtest_support import Factory
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import Decision, DocumentKind, OrgRole, Stage, StudyStatus
from basis.db.errors import (
    DuplicateDocumentError,
    InvalidCursorError,
    NotVisibleError,
    StaleStateError,
)
from basis.db.models import IdempotencyKey, Org, OrgMember, OutboxMessage, Study, User
from basis.db.repositories import (
    DocumentRepository,
    IdempotencyRepository,
    OutboxRepository,
    ReviewRepository,
    StudyRepository,
    UserRepository,
)

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------- studies


async def test_create_returns_server_defaults(session: AsyncSession, make: Factory) -> None:
    owner = await make.user()
    org_id = await make.personal_org_id(owner)
    study = await StudyRepository(session).create(org_id, owner.id, "1 Elm St")
    assert isinstance(study.id, uuid.UUID) and study.id.version == 4
    assert study.status is StudyStatus.DRAFT
    assert study.current_stage is Stage.UPLOADING
    assert study.revision == 1
    assert study.created_at.tzinfo is not None
    assert study.workflow_id is None


async def test_study_is_visible_only_to_org_members(session: AsyncSession, make: Factory) -> None:
    owner, viewer, outsider = await make.user(), await make.user(), await make.user()
    study = await make.study(owner)
    await make.member(study.org_id, viewer, OrgRole.VIEWER)
    repo = StudyRepository(session)

    assert (await repo.get_for_user(study.id, owner.id)) is not None
    visible = await repo.get_visible(study.id, viewer.id)
    assert visible is not None and visible.role is OrgRole.VIEWER
    assert visible.role.rank < OrgRole.ENGINEER.rank
    assert await repo.get_for_user(study.id, outsider.id) is None
    assert await repo.get_for_user(uuid.uuid4(), owner.id) is None


async def test_list_for_user_paginates_newest_first_without_gaps(
    session: AsyncSession, make: Factory
) -> None:
    owner, outsider = await make.user(), await make.user()
    team = await make.org()
    await make.member(team.id, owner, OrgRole.ENGINEER)
    org_ids = [await make.personal_org_id(owner), team.id]
    created: list[Study] = []
    for n in range(7):
        created.append(await make.study(owner, org_id=org_ids[n % 2], property_name=f"P{n}"))
    await make.study(outsider)  # never listed for owner
    # Same created_at for several rows (one transaction): the id tiebreak must hold.
    await session.execute(
        update(Study)
        .where(Study.id.in_([s.id for s in created[:4]]))
        .values(created_at=text("'2026-01-01T00:00:00Z'"))
    )
    repo = StudyRepository(session)
    expected = list(
        (
            await session.scalars(
                select(Study.id)
                .where(Study.id.in_([s.id for s in created]))
                .order_by(Study.created_at.desc(), Study.id.desc())
            )
        ).all()
    )

    seen: list[uuid.UUID] = []
    cursor = None
    pages = 0
    while True:
        page = await repo.list_for_user(owner.id, limit=3, cursor=cursor)
        seen.extend(study.id for study in page.items)
        pages += 1
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert seen == expected
    assert pages == 3


async def test_list_for_user_hides_archived_by_default(
    session: AsyncSession, make: Factory
) -> None:
    owner = await make.user()
    kept, archived = await make.study(owner), await make.study(owner)
    await StudyRepository(session).archive(archived.id)
    repo = StudyRepository(session)
    assert [s.id for s in (await repo.list_for_user(owner.id)).items] == [kept.id]
    everything = await repo.list_for_user(owner.id, include_archived=True)
    assert {s.id for s in everything.items} == {kept.id, archived.id}


@pytest.mark.parametrize("limit", [0, 101])
async def test_list_for_user_rejects_bad_limit(session: AsyncSession, limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        await StudyRepository(session).list_for_user(uuid.uuid4(), limit=limit)


async def test_list_for_user_rejects_bad_cursor(session: AsyncSession) -> None:
    with pytest.raises(InvalidCursorError):
        await StudyRepository(session).list_for_user(uuid.uuid4(), cursor="not-a-cursor")


async def test_transition_errors(session: AsyncSession, make: Factory) -> None:
    study = await make.study(stage=Stage.ROOMS_REVIEW, revision=3)
    repo = StudyRepository(session)
    with pytest.raises(StaleStateError, match="rooms_review revision 3"):
        await repo.transition(study.id, Stage.APPRAISAL_REVIEW, Stage.ROOMS_REVIEW, 3)
    with pytest.raises(StaleStateError):
        await repo.transition(study.id, Stage.ROOMS_REVIEW, Stage.ASSETS_PROCESSING, 2)
    with pytest.raises(NotVisibleError):
        await repo.transition(uuid.uuid4(), Stage.ROOMS_REVIEW, Stage.ASSETS_PROCESSING, 3)
    with pytest.raises(ValueError, match="change the stage or the revision"):
        await repo.transition(study.id, Stage.ROOMS_REVIEW, Stage.ROOMS_REVIEW, 3)
    with pytest.raises(ValueError, match="lower"):
        await repo.transition(study.id, Stage.ROOMS_REVIEW, Stage.EXTRACTING, 3, new_revision=2)
    moved = await repo.transition(study.id, Stage.ROOMS_REVIEW, Stage.ASSETS_PROCESSING, 3)
    assert (moved.current_stage, moved.status, moved.revision) == (
        Stage.ASSETS_PROCESSING,
        StudyStatus.PROCESSING,
        3,
    )


async def test_archive_is_idempotent(session: AsyncSession, make: Factory) -> None:
    study = await make.study()
    repo = StudyRepository(session)
    first = await repo.archive(study.id)
    second = await repo.archive(study.id)
    assert first.status is second.status is StudyStatus.ARCHIVED
    with pytest.raises(NotVisibleError):
        await repo.archive(uuid.uuid4())


# --------------------------------------------------------------------- reviews


async def test_decision_on_non_review_stage_is_stale(session: AsyncSession, make: Factory) -> None:
    study = await make.study(stage=Stage.EXTRACTING)
    with pytest.raises(StaleStateError, match="not a review stage"):
        await ReviewRepository(session).record_decision(
            study_id=study.id,
            stage=Stage.EXTRACTING,
            revision=1,
            decision=Decision.APPROVE,
            reviewer_id=study.created_by,
            idempotency_key="k",
        )


async def test_latest_review_per_stage(session: AsyncSession, make: Factory) -> None:
    study = await make.study(stage=Stage.APPRAISAL_REVIEW)
    repo = ReviewRepository(session)
    reviewer = study.created_by
    await repo.record_decision(
        study_id=study.id,
        stage=Stage.APPRAISAL_REVIEW,
        revision=1,
        decision=Decision.REJECT,
        reviewer_id=reviewer,
        idempotency_key="r1",
    )
    await StudyRepository(session).transition(study.id, Stage.EXTRACTING, Stage.APPRAISAL_REVIEW, 2)
    await repo.record_decision(
        study_id=study.id,
        stage=Stage.APPRAISAL_REVIEW,
        revision=2,
        decision=Decision.APPROVE,
        reviewer_id=reviewer,
        idempotency_key="r2",
    )
    latest = await repo.latest_by_stage(study.id)
    assert set(latest) == {Stage.APPRAISAL_REVIEW}
    assert (latest[Stage.APPRAISAL_REVIEW].revision, latest[Stage.APPRAISAL_REVIEW].decision) == (
        2,
        Decision.APPROVE,
    )
    assert len(await repo.list_for_study(study.id)) == 2
    found = await repo.get_by_idempotency_key(reviewer, "r1")
    assert found is not None and found.decision is Decision.REJECT
    assert await repo.get_by_idempotency_key(uuid.uuid4(), "r1") is None


# --------------------------------------------------------------------- documents


async def register(
    repo: DocumentRepository, study: Study, digest: str, uri: str, document_id: uuid.UUID | None
) -> object:
    return await repo.create(
        study_id=study.id,
        kind=DocumentKind.APPRAISAL,
        filename="a.pdf",
        content_type="application/pdf",
        size_bytes=1234,
        sha256=digest,
        gcs_uri=uri,
        uploaded_by=study.created_by,
        document_id=document_id,
    )


async def test_document_duplicate_is_reported_and_transaction_stays_usable(
    session: AsyncSession, make: Factory
) -> None:
    study = await make.study()
    repo = DocumentRepository(session)
    chosen = uuid.uuid4()
    first = await repo.create(
        study_id=study.id,
        kind=DocumentKind.APPRAISAL,
        filename="a.pdf",
        content_type="application/pdf",
        size_bytes=1234,
        sha256="AB" * 32,  # normalized to lowercase
        gcs_uri=f"studies/{study.id}/{chosen}/a.pdf",
        uploaded_by=study.created_by,
        document_id=chosen,
    )
    assert first.id == chosen and first.sha256 == "ab" * 32 and first.uploaded_at is None
    with pytest.raises(DuplicateDocumentError):
        await register(repo, study, "ab" * 32, "studies/other", None)
    with pytest.raises(DuplicateDocumentError):
        await register(repo, study, "cd" * 32, first.gcs_uri, None)
    # The SAVEPOINT kept the outer transaction usable.
    assert await repo.find_by_sha256(study.id, "AB" * 32) is not None
    assert [d.id for d in await repo.list_for_study(study.id)] == [chosen]


async def test_document_rejects_malformed_sha256(session: AsyncSession, make: Factory) -> None:
    study = await make.study()
    with pytest.raises(ValueError, match="sha256"):
        await register(DocumentRepository(session), study, "xyz", "u", None)


async def test_mark_uploaded_is_scoped_and_idempotent(session: AsyncSession, make: Factory) -> None:
    study, other = await make.study(), await make.study()
    repo = DocumentRepository(session)
    document = await repo.create(
        study_id=study.id,
        kind=DocumentKind.PHOTO,
        filename="p.jpg",
        content_type="image/jpeg",
        size_bytes=5,
        sha256="0" * 64,
        gcs_uri="studies/p.jpg",
        uploaded_by=None,
    )
    uploaded = await repo.mark_uploaded(study.id, document.id)
    assert uploaded.uploaded_at is not None
    again = await repo.mark_uploaded(study.id, document.id)
    assert again.uploaded_at == uploaded.uploaded_at
    with pytest.raises(NotVisibleError):
        await repo.mark_uploaded(other.id, document.id)
    assert await repo.get(other.id, document.id) is None


# --------------------------------------------------------------------- users


async def test_first_login_creates_the_user_and_no_org(
    session: AsyncSession,
) -> None:
    """Firms are invite-only: first sight creates the user only (before, it also created a
    personal org with an owner membership)."""
    repo = UserRepository(session)
    user = await repo.upsert_by_firebase_uid("uid-ada", "ada@example.test", "Ada")
    assert user.last_seen_at is not None
    assert await repo.memberships(user.id) == {}
    assert await session.scalar(select(func.count()).select_from(Org)) == 0


async def test_later_login_updates_without_new_org_and_keeps_missing_claims(
    session: AsyncSession,
) -> None:
    repo = UserRepository(session)
    first = await repo.upsert_by_firebase_uid("uid-bob", "bob@example.test", "Bob")
    # now() is fixed inside the test transaction, so backdate last_seen_at to see it move.
    backdated = await session.scalar(
        update(User)
        .where(User.id == first.id)
        .values(last_seen_at=text("now() - interval '1 hour'"))
        .returning(User.last_seen_at)
    )
    again = await repo.upsert_by_firebase_uid("uid-bob", None, "Robert")
    assert again.id == first.id
    assert again.email == "bob@example.test"  # a missing claim does not erase
    assert again.display_name == "Robert"
    assert backdated is not None and again.last_seen_at is not None
    assert again.last_seen_at > backdated
    assert await session.scalar(select(func.count()).select_from(OrgMember)) == 0
    assert await repo.get_by_firebase_uid("uid-bob") is not None
    assert await repo.get(first.id) is not None


async def test_add_membership_inserts_then_changes_role(
    session: AsyncSession, make: Factory
) -> None:
    user = await make.user()
    org = await make.org()
    repo = UserRepository(session)
    await repo.add_membership(org.id, user.id, OrgRole.VIEWER)
    await repo.add_membership(org.id, user.id, OrgRole.ENGINEER)
    assert (await repo.memberships(user.id))[org.id] is OrgRole.ENGINEER


async def test_grant_membership_never_lowers_a_role(session: AsyncSession, make: Factory) -> None:
    """Accepting an invitation: insert, raise a lower role, keep a higher one."""
    user, org = await make.user(), await make.org()
    repo = UserRepository(session)
    assert await repo.grant_membership(org.id, user.id, OrgRole.ENGINEER) is OrgRole.ENGINEER
    assert await repo.grant_membership(org.id, user.id, OrgRole.VIEWER) is OrgRole.ENGINEER
    assert await repo.grant_membership(org.id, user.id, OrgRole.OWNER) is OrgRole.OWNER
    assert await repo.grant_membership(org.id, user.id, OrgRole.ENGINEER) is OrgRole.OWNER
    assert (await repo.memberships(user.id))[org.id] is OrgRole.OWNER


# --------------------------------------------------------------------- idempotency


async def test_idempotency_key_lifecycle(session: AsyncSession, make: Factory) -> None:
    user, other = await make.user(), await make.user()
    repo = IdempotencyRepository(session)
    claim = await repo.claim(user.id, "k1", "POST /v1/studies", "a" * 64)
    assert claim.created and claim.record.status_code is None
    assert claim.record.expires_at - claim.record.created_at == timedelta(hours=24)

    replay = await repo.claim(user.id, "k1", "POST /v1/studies", "b" * 64)
    assert not replay.created and replay.record.request_hash == "a" * 64

    # The same client key from another user is a different key.
    assert (await repo.claim(other.id, "k1", "POST /v1/studies", "c" * 64)).created

    assert await repo.complete(user.id, "k1", 201, {"id": "s1"})
    assert not await repo.complete(user.id, "k1", 500, {"late": True})  # already completed
    stored = await repo.get(user.id, "k1")
    assert stored is not None and (stored.status_code, stored.response) == (201, {"id": "s1"})
    assert not await repo.release(user.id, "k1")  # completed keys are kept


async def test_released_and_expired_keys_can_be_claimed_again(
    session: AsyncSession, make: Factory
) -> None:
    user = await make.user()
    repo = IdempotencyRepository(session)
    await repo.claim(user.id, "k2", "POST /x", "a" * 64)
    assert await repo.release(user.id, "k2")
    assert (await repo.claim(user.id, "k2", "POST /x", "b" * 64)).created

    await session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.user_id == user.id)
        .values(expires_at=text("now() - interval '1 second'"), status_code=200)
    )
    assert await repo.get(user.id, "k2") is None
    fresh = await repo.claim(user.id, "k2", "POST /x", "c" * 64)
    assert fresh.created
    assert (fresh.record.request_hash, fresh.record.status_code, fresh.record.response) == (
        "c" * 64,
        None,
        None,
    )
    await session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.user_id == user.id)
        .values(expires_at=text("now() - interval '1 second'"))
    )
    assert await repo.purge_expired() == 1


async def test_idempotency_key_validation(session: AsyncSession) -> None:
    repo = IdempotencyRepository(session)
    with pytest.raises(ValueError):
        await repo.claim(None, "x" * 129, "POST /x", "a" * 64)
    with pytest.raises(ValueError):
        await repo.claim(None, "k", "POST /x", "a" * 64, ttl=timedelta(0))


# --------------------------------------------------------------------- outbox


async def test_outbox_claim_deliver_and_retry(session: AsyncSession) -> None:
    repo = OutboxRepository(session)
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    first = await repo.enqueue("workflow_signal", {"study_id": str(s1)})
    second = await repo.enqueue("workflow_signal", {"study_id": str(s2)})
    assert {m.id for m in await repo.claim_due(limit=10)} == {first.id, second.id}

    await repo.mark_delivered(first.id)
    await repo.mark_failed(second.id, "x" * 5000, retry_in=timedelta(minutes=5))
    assert await repo.claim_due() == []  # delivered, and the other is not due yet

    failed = await session.get(OutboxMessage, second.id, populate_existing=True)
    assert failed is not None
    assert failed.attempts == 1 and failed.last_error is not None
    assert len(failed.last_error) == 2000
    assert [m.id for m in await repo.list_for_study(s2)] == [second.id]


async def test_outbox_rejects_bad_arguments(session: AsyncSession) -> None:
    repo = OutboxRepository(session)
    with pytest.raises(ValueError):
        await repo.claim_due(limit=0)
    with pytest.raises(ValueError):
        await repo.mark_failed(uuid.uuid4(), "e", retry_in=timedelta(seconds=-1))
