"""One person, one user: a new sign-in account (Firebase uid) for an existing firm member
(``user_linked_accounts``, migration 0031; EVAL_REPORT section 93; docs/api/FIRMS.md).

The duplicate the Team page showed came from a second uid for the same email getting its
own ``users`` row. These tests pin when a new uid joins the existing user (the setting on,
a verified email a firm member already has, or the operator CLI) and when it does not (the
setting off, an unverified email, no firm member with that email), including two first
sign-ins of the same new uid at once.
"""

from __future__ import annotations

import uuid

import pytest
from dbtest_support import Factory, race
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.admin import AdminError, link_account
from basis.db.enums import OrgRole
from basis.db.models import User, UserLinkedAccount
from basis.db.repositories import UserRepository
from basis.db.session import session_scope


async def _member(session: AsyncSession, uid: str, email: str, name: str | None = None) -> User:
    make = Factory(session)
    user = await UserRepository(session).upsert_by_firebase_uid(uid, email, name)
    org = await make.org("Acme Engineering")
    await make.member(org.id, user, OrgRole.OWNER)
    return user


async def _users(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(User)) or 0)


async def test_setting_on_links_a_new_verified_uid_to_the_member(session: AsyncSession) -> None:
    repo = UserRepository(session)
    member = await _member(session, "uid-old", "Ada@Example.test", "Ada")
    again = await repo.upsert_by_firebase_uid(
        "uid-new", " ada@example.TEST ", None, email_verified=True, link_verified_email=True
    )
    assert again.id == member.id
    assert again.display_name == "Ada"  # a missing claim never erases
    assert await _users(session) == 1
    assert await repo.memberships(again.id) == await repo.memberships(member.id)
    link = await session.get(UserLinkedAccount, "uid-new")
    assert link is not None and link.user_id == member.id
    assert link.email == "ada@example.test" and link.linked_by == "sign_in"
    # The new uid resolves on later requests, and the original uid still does.
    assert (await repo.get_by_firebase_uid("uid-new")).id == member.id  # type: ignore[union-attr]
    assert (await repo.get_by_firebase_uid("uid-old")).id == member.id  # type: ignore[union-attr]
    # users.firebase_uid did not change (it never may).
    assert (await repo.get(member.id)).firebase_uid == "uid-old"  # type: ignore[union-attr]


async def test_a_linked_uid_refreshes_the_member_on_later_sign_ins(session: AsyncSession) -> None:
    repo = UserRepository(session)
    member = await _member(session, "uid-old", "ada@example.test", "Ada")
    await repo.upsert_by_firebase_uid(
        "uid-new", "ada@example.test", None, email_verified=True, link_verified_email=True
    )
    backdated = await session.scalar(
        update(User)
        .where(User.id == member.id)
        .values(last_seen_at=text("now() - interval '1 hour'"))
        .returning(User.last_seen_at)
    )
    # Later sign-ins of the linked uid need neither the setting nor a verified claim.
    later = await repo.upsert_by_firebase_uid("uid-new", None, "Ada Lovelace")
    assert later.id == member.id and later.display_name == "Ada Lovelace"
    assert backdated is not None and later.last_seen_at is not None
    assert later.last_seen_at > backdated
    assert await _users(session) == 1


@pytest.mark.parametrize(
    ("verified", "setting"),
    [(False, True), (True, False), (False, False)],
    ids=["unverified-email", "setting-off", "both"],
)
async def test_without_the_setting_or_a_verified_email_a_new_uid_is_a_new_user(
    session: AsyncSession, verified: bool, setting: bool
) -> None:
    """The regression check: with the setting off (the default) sign-in behaves as before."""
    repo = UserRepository(session)
    member = await _member(session, "uid-old", "ada@example.test")
    other = await repo.upsert_by_firebase_uid(
        "uid-new", "ada@example.test", None, email_verified=verified, link_verified_email=setting
    )
    assert other.id != member.id
    assert other.firebase_uid == "uid-new"
    assert await repo.memberships(other.id) == {}
    assert await session.scalar(select(func.count()).select_from(UserLinkedAccount)) == 0


async def test_a_user_in_no_firm_is_never_a_link_target(session: AsyncSession) -> None:
    """Only a firm member's address was proven by that person (membership is reached through
    a verified email), so a firmless user with the address is not linked to."""
    repo = UserRepository(session)
    shell = await repo.upsert_by_firebase_uid("uid-shell", "ada@example.test", None)
    fresh = await repo.upsert_by_firebase_uid(
        "uid-new", "ada@example.test", None, email_verified=True, link_verified_email=True
    )
    assert fresh.id != shell.id
    assert await session.scalar(select(func.count()).select_from(UserLinkedAccount)) == 0


async def test_another_email_is_not_linked(session: AsyncSession) -> None:
    repo = UserRepository(session)
    await _member(session, "uid-old", "ada@example.test")
    fresh = await repo.upsert_by_firebase_uid(
        "uid-new", "bob@example.test", None, email_verified=True, link_verified_email=True
    )
    assert fresh.firebase_uid == "uid-new"
    assert await repo.memberships(fresh.id) == {}


async def test_an_existing_uid_is_never_relinked(session: AsyncSession) -> None:
    """A uid that already has its own user keeps it, even when a member has its email."""
    repo = UserRepository(session)
    own = await repo.upsert_by_firebase_uid("uid-own", "ada@example.test", None)
    await _member(session, "uid-old", "ada@example.test")
    again = await repo.upsert_by_firebase_uid(
        "uid-own", "ada@example.test", None, email_verified=True, link_verified_email=True
    )
    assert again.id == own.id
    assert await session.scalar(select(func.count()).select_from(UserLinkedAccount)) == 0


async def test_several_members_with_the_email_link_to_the_one_seen_last(
    session: AsyncSession,
) -> None:
    """An earlier duplicate (two member rows with one email): the most recently seen wins,
    so the person lands where they last worked."""
    repo = UserRepository(session)
    older = await _member(session, "uid-a", "ada@example.test")
    newer = await _member(session, "uid-b", "ada@example.test")
    await session.execute(
        update(User)
        .where(User.id == older.id)
        .values(last_seen_at=text("now() - interval '2 days'"))
    )
    linked = await repo.upsert_by_firebase_uid(
        "uid-new", "ada@example.test", None, email_verified=True, link_verified_email=True
    )
    assert linked.id == newer.id


async def test_two_first_sign_ins_of_one_new_uid_at_once_link_once(
    committed: async_sessionmaker[AsyncSession],
) -> None:
    """Both requests take the uid's advisory lock; the second waits, then finds the link."""
    async with session_scope(committed) as session:
        member = await _member(session, "uid-old", "ada@example.test")

    async def sign_in(session: AsyncSession) -> User:
        return await UserRepository(session).upsert_by_firebase_uid(
            "uid-new", "ada@example.test", None, email_verified=True, link_verified_email=True
        )

    results = await race(committed, sign_in, sign_in, sign_in)
    errors = [r for r in results if isinstance(r, BaseException)]
    assert errors == []
    assert {r.id for r in results if isinstance(r, User)} == {member.id}
    async with committed() as session:
        assert await _users(session) == 1
        assert await session.scalar(select(func.count()).select_from(UserLinkedAccount)) == 1


# --------------------------------------------------------------------- the operator CLI


async def test_operator_links_a_uid_to_the_user_with_the_email(session: AsyncSession) -> None:
    member = await _member(session, "uid-old", "ada@example.test")
    out = await link_account(session, " ADA@example.test ", " uid-new ", None)
    assert out == {
        "action": "linked",
        "uid": "uid-new",
        "user_id": str(member.id),
        "email": "ada@example.test",
    }
    link = await session.get(UserLinkedAccount, "uid-new")
    assert link is not None and link.linked_by == "operator"
    # Then sign-in resolves the uid with the setting off.
    again = await UserRepository(session).upsert_by_firebase_uid("uid-new", None, None)
    assert again.id == member.id


async def test_operator_link_refusals(session: AsyncSession) -> None:
    repo = UserRepository(session)
    first = await _member(session, "uid-a", "ada@example.test")
    await _member(session, "uid-b", "ada@example.test")
    await repo.upsert_by_firebase_uid("uid-taken", "someone@example.test", None)
    with pytest.raises(AdminError, match="no user with email"):
        await link_account(session, "nobody@example.test", "uid-new", None)
    with pytest.raises(AdminError, match="several users"):
        await link_account(session, "ada@example.test", "uid-new", None)
    with pytest.raises(AdminError, match="no user with email .* and id"):
        await link_account(session, "ada@example.test", "uid-new", uuid.uuid4())
    with pytest.raises(AdminError, match="already a user of its own"):
        await link_account(session, "ada@example.test", "uid-taken", first.id)
    with pytest.raises(AdminError, match="must be an email address"):
        await link_account(session, "not-an-email", "uid-new", None)
    with pytest.raises(AdminError, match="must be an email address"):
        await link_account(session, "ada@example.test", "  ", None)
    assert (await link_account(session, "ada@example.test", "uid-new", first.id))["action"] == (
        "linked"
    )
    with pytest.raises(AdminError, match="already linked"):
        await link_account(session, "ada@example.test", "uid-new", first.id)
