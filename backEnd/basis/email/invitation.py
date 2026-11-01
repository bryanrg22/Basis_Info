"""The invitation email: plain text, rendered from an :class:`InvitationView`.

Plain text on purpose: it renders everywhere, carries no tracking, and the only action is
"sign in with this address". There is no secret link: the invitation is bound to the email
(``basis.db.repositories.invitation``), so the message cannot be used by anyone else, and
the app URL in it is the public one (``APP_URL``).
"""

from __future__ import annotations

import uuid
from typing import Final

from basis.db.enums import OrgRole
from basis.db.repositories.invitation import InvitationView
from basis.email.sender import EmailMessage

ROLE_PHRASE: Final[dict[OrgRole, str]] = {
    OrgRole.OWNER: "an owner (the firm's admin: members, invitations and every study)",
    OrgRole.ENGINEER: "an engineer (creates and reviews studies)",
    OrgRole.VIEWER: "a viewer (reads studies)",
}
KEY_PREFIX: Final = "invitation-"


def idempotency_key(invitation_id: uuid.UUID) -> str:
    """``invitation-{id}``: one key per invitation row, so one send per invitation."""
    return f"{KEY_PREFIX}{invitation_id}"


def inviter_label(view: InvitationView) -> str | None:
    """``Name (email)``, the name, the email, or None for the operator CLI's invitations."""
    name = (view.invited_by_name or "").strip()
    email = (view.invited_by_email or "").strip()
    if name and email:
        return f"{name} ({email})"
    return name or email or None


def render_invitation(view: InvitationView, app_url: str | None) -> EmailMessage:
    invitation = view.invitation
    firm = view.org_name.strip() or "a firm"
    who = inviter_label(view)
    role = ROLE_PHRASE[OrgRole(invitation.role)]
    subject = (
        f"{who.split(' (')[0]} invited you to {firm} on Basis"
        if who
        else (f"You are invited to {firm} on Basis")
    )
    opening = (
        f"{who} invited you to join {firm} on Basis as {role}."
        if who
        else f"Basis set up {firm} and invited you to join it as {role}."
    )
    where = f"sign in to Basis at {app_url}" if app_url else "sign in to Basis"
    expires = invitation.expires_at.strftime("%d %B %Y")
    text = (
        "Hello,\n\n"
        f"{opening}\n\n"
        f"To accept, {where} with this email address ({invitation.email}), using a sign-in "
        "that confirms it (Google sign-in does). The invitation is shown after you sign in; "
        "accept or decline it there.\n\n"
        f"This invitation expires on {expires} (UTC). If you were not expecting it, ignore "
        "this message: nothing happens until you accept.\n\n"
        "Basis\n"
    )
    return EmailMessage(
        to=invitation.email,
        subject=subject,
        text=text,
        idempotency_key=idempotency_key(invitation.id),
    )
