"""``python -m basis.admin``: operator bootstrap for invite-only firms (no HTTP route).

Firms are invite-only, so the first owner of a firm cannot invite themselves: an operator
with database access creates the firm and its first owner's invitation here. The owner
accepts it in the app after signing in with that email (verified), exactly like any other
invitation; an existing user is not added directly, so there is one path. From then on
the firm's owners manage it in the app (``/v1/orgs/{org_id}``).

::

    python -m basis.admin create-firm --name "Basis" --owner-email owner@example.com \\
        [--allow-duplicate-name]
    python -m basis.admin invite --org-id UUID --email someone@example.com --role engineer
    python -m basis.admin list-firms

* ``create-firm`` refuses a name another firm already has unless
  ``--allow-duplicate-name``. Re-running it reports the existing state: the firm and
  whether the email is an owner or has a pending owner invitation; when its earlier owner
  invitation expired, was revoked or declined, a new one is issued.
* ``invite`` issues an invitation (``created_by`` NULL: from Basis, not a member). Re-running
  it with the same role reports the pending invitation.
* ``list-firms`` prints every firm with its owners, member count and pending invitations.

Like the API, it takes the firm's row lock before touching its invitations (the lock
order every writer of a firm uses). Output is one JSON object per line on stdout; errors
go to stderr with exit status 2. It reads ``BASIS_ENV``, ``DATABASE_URL`` (or
``CLOUD_SQL_INSTANCE``) and ``INVITATION_TTL_DAYS`` like every process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.db.enums import OrgRole
from basis.db.errors import AlreadyMemberError, InvitationPendingError
from basis.db.models import Invitation, Org
from basis.db.repositories import (
    InvitationRepository,
    InvitationStatus,
    InvitationView,
    OrgRepository,
)
from basis.db.repositories.invitation import invitation_status, valid_email

PROG: Final = "python -m basis.admin"
MAX_NAME_LENGTH: Final = 200


class AdminError(Exception):
    """A refusal to report to the operator (exit status 2)."""


def _firm(org: Org) -> dict[str, Any]:
    return {"org_id": str(org.id), "name": org.name, "created_at": org.created_at.isoformat()}


def _invitation(view: InvitationView) -> dict[str, Any]:
    invitation = view.invitation
    return {
        "invitation_id": str(invitation.id),
        "email": invitation.email,
        "role": invitation.role,
        "status": view.status.value,
        "expires_at": invitation.expires_at.isoformat(),
        "delivery": "not_sent",
    }


async def _existing_owner_state(
    session: AsyncSession, org: Org, email: str, ttl: timedelta
) -> dict[str, Any] | None:
    """What ``email`` already is in ``org``: owner, invited as owner, re-invited, or None."""
    firms, invitations = OrgRepository(session), InvitationRepository(session)
    member_id = await firms.member_with_email(org.id, email)
    if member_id is not None:
        role = await firms.role_of(org.id, member_id)
        if role is not OrgRole.OWNER:
            raise AdminError(
                f"{email} is already a {role} of {org.name!r} ({org.id}); an owner of the "
                "firm can change their role in the app"
            )
        return {"owner": {"email": email, "status": "member", "user_id": str(member_id)}}
    rows = (
        await session.scalars(
            select(Invitation)
            .where(Invitation.org_id == org.id, Invitation.email == email)
            .order_by(Invitation.created_at.desc())
        )
    ).all()
    if not rows:
        return None
    now = await invitations.now()
    for row in rows:
        if invitation_status(row, now) is InvitationStatus.PENDING:
            if row.role != OrgRole.OWNER.value:
                raise AdminError(
                    f"{email} has a pending {row.role} invitation to {org.name!r}; revoke it "
                    "in the app first"
                )
            return {"owner": _invitation(await invitations.view(row.id))}
    view = await invitations.create(org.id, email, OrgRole.OWNER, None, ttl)
    return {"owner": _invitation(view), "reinvited": True}


async def create_firm(
    session: AsyncSession,
    name: str,
    owner_email: str,
    *,
    ttl: timedelta,
    allow_duplicate_name: bool = False,
) -> dict[str, Any]:
    """Create the firm and a pending owner invitation; idempotent for the same arguments."""
    name = name.strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        raise AdminError(f"the firm name must be 1 to {MAX_NAME_LENGTH} characters")
    try:
        email = valid_email(owner_email)
    except ValueError as exc:
        raise AdminError(str(exc)) from None
    firms = OrgRepository(session)
    existing = await firms.by_name(name)
    if existing and not allow_duplicate_name:
        if len(existing) > 1:
            raise AdminError(
                f"{len(existing)} firms are named {name!r}: "
                + ", ".join(str(org.id) for org in existing)
                + "; use invite --org-id"
            )
        org = existing[0]
        await firms.lock(org.id)  # lock order: the firm, then its invitations
        state = await _existing_owner_state(session, org, email, ttl)
        if state is None:
            raise AdminError(
                f"a firm named {name!r} already exists ({org.id}) and {email} has never been "
                "invited to it; pass --allow-duplicate-name to create another, or use invite"
            )
        return {"action": "exists", "firm": _firm(org), **state}
    org = await firms.create(name)
    view = await InvitationRepository(session).create(org.id, email, OrgRole.OWNER, None, ttl)
    return {"action": "created", "firm": _firm(org), "owner": _invitation(view)}


async def invite(
    session: AsyncSession, org_id: uuid.UUID, email: str, role: OrgRole, *, ttl: timedelta
) -> dict[str, Any]:
    """Invite ``email`` to the firm as ``role``; a pending one with that role is reported."""
    firms = OrgRepository(session)
    org = await firms.get(org_id)
    if org is None:
        raise AdminError(f"no firm with id {org_id}")
    await firms.lock(org_id)  # lock order: the firm, then its invitations
    invitations = InvitationRepository(session)
    try:
        view = await invitations.create(org_id, email, role, None, ttl)
    except ValueError as exc:
        raise AdminError(str(exc)) from None
    except AlreadyMemberError as exc:
        raise AdminError(f"{email}: {exc}") from None
    except InvitationPendingError:
        pending = [
            v for v in await invitations.pending_for_email(email) if v.invitation.org_id == org_id
        ]
        if pending and pending[0].invitation.role == role.value:
            return {"action": "exists", "firm": _firm(org), "invitation": _invitation(pending[0])}
        raise AdminError(
            f"{email} has a pending invitation to {org.name!r} with another role; revoke it "
            "in the app first"
        ) from None
    return {"action": "invited", "firm": _firm(org), "invitation": _invitation(view)}


async def list_firms(session: AsyncSession) -> list[dict[str, Any]]:
    return [
        {
            **_firm(summary.org),
            "owners": summary.owners,
            "members": summary.members,
            "pending_invitations": summary.open_invitations,
        }
        for summary in await OrgRepository(session).summaries()
    ]


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG, description="Create invite-only firms and their first owners."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-firm", help="create a firm and invite its owner")
    create.add_argument("--name", required=True)
    create.add_argument("--owner-email", required=True)
    create.add_argument("--allow-duplicate-name", action="store_true")
    add = commands.add_parser("invite", help="invite an email to a firm")
    add.add_argument("--org-id", required=True, type=uuid.UUID)
    add.add_argument("--email", required=True)
    add.add_argument("--role", required=True, choices=[role.value for role in OrgRole])
    commands.add_parser("list-firms", help="list every firm")
    return parser


async def _run(args: argparse.Namespace) -> list[dict[str, Any]]:
    from basis.config import load_settings
    from basis.db.engine import create_engine
    from basis.db.session import create_sessionmaker, session_scope

    settings = load_settings()
    ttl = timedelta(days=settings.invitation_ttl_days)
    engine = create_engine(settings)
    try:
        async with session_scope(create_sessionmaker(engine)) as session:
            if args.command == "create-firm":
                return [
                    await create_firm(
                        session,
                        args.name,
                        args.owner_email,
                        ttl=ttl,
                        allow_duplicate_name=args.allow_duplicate_name,
                    )
                ]
            if args.command == "invite":
                return [await invite(session, args.org_id, args.email, OrgRole(args.role), ttl=ttl)]
            return await list_firms(session)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        lines = asyncio.run(_run(args))
    except AdminError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(json.dumps(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
