# Firms: creating one, renaming it, inviting by email

How a cost segregation firm gets onto Basis without an operator at a terminal, and what the
API tells the firm's owners about each invitation. Routes live in `basis/api/routes/orgs.py`;
the data rules in `basis/db/repositories/org.py` and `invitation.py`; the email in
`basis/email/` and `basis/relay/delivery.py`.

## 1. How a new customer starts

Basis sets each firm up (Bryan, 2026-11-06; `FIRM_SELF_SERVE` off, the default):

1. An operator runs `python -m basis.admin create-firm --name "Harbor Cost Seg" --owner-email
   admin@harbor.example`, which creates the firm and invites its owner (the invitation is
   emailed, section 3).
2. The owner signs in with that address (a sign-in that confirms the email) and accepts on the
   join screen; they are the firm's **owner** (shown as admin in the app).
3. The owner invites the rest of the firm (`POST /v1/orgs/{org_id}/invitations`, section 3).
   Each invitee receives an email, signs in with that address and accepts on the join screen.

Anyone who signs in before being invited sees "You're not in a firm yet": the app offers no
way to create a firm, and `POST /v1/orgs` answers 403 `firm-self-serve-off`
(`BASIS-FIRM-011`), so the rule holds at the API, not only on the screen.

With `FIRM_SELF_SERVE=true`, self-serve comes back: `POST /v1/orgs` with
`{"name": "Harbor Cost Seg"}` creates the firm and makes a caller with a verified email its
owner. 201 with `{id, name, created_at, role: "owner"}` and a `Location` header. The rules
below describe that route.

Rules, and why:

| Rule | Why |
|---|---|
| Creating a firm needs a verified email (403 `email-unverified`, `BASIS-FIRM-003`). | The owner is who Basis and the firm's members reach about the firm; an unconfirmed address is nobody. The same rule already governs answering an invitation. |
| With `FIRM_SELF_SERVE` on, any signed-in user may create a firm; there is no sign-up queue. Off (the default), only the operator does. | Off: Basis onboards each firm by hand for now, so a firm is never created by an engineer who meant to join one. On: the pitch is "here is the link"; abuse is bounded by the per-user mutation rate limit; a firm with no studies costs nothing. |
| A user may belong to, and own, several firms. | The code allowed it before this change and a consultant does work for two firms. `POST /v1/studies` already requires `org_id` from a member of several (422 `org-required`). This is the product rule; a cap would need a reason. |
| Firm names are not unique. | Two "Smith Engineering" exist in the world. Refusing a name another firm took would also tell a stranger that firm exists. The operator CLI keeps refusing duplicates by default because an operator cannot tell them apart in `list-firms`. |
| `PATCH /v1/orgs/{org_id}` (owner only) renames; nothing else about a firm is editable here. | The firm's report profile (address, logo, limiting conditions) is `PUT /v1/orgs/{org_id}/profile`, docs/api/REPORT.md; each engineer's name, credentials and license are their own (`PUT /v1/me/signer`, docs/api/SIGNERS.md). |
| The operator CLI `python -m basis.admin create-firm` is how firms are made. | It invites the owner instead of adding them, so the owner joins exactly like every other member; the owner invitation is emailed too. |

Both are on the firm's history (`org_events`, `GET /v1/orgs/{org_id}/activity`,
docs/api/TIMELINE_AND_AUDIT.md "Firm activity"): `org.created` by the creator (by the
system when the operator CLI created the firm) and `org.renamed` with the name before and
after; renaming to the current name records nothing. Both are also logged (`firm created`,
`firm renamed`, with `org_id`, `user_id` and `firm_name`).

## 2. The routes

| Route | Role | Body / answer |
|---|---|---|
| `POST /v1/orgs` | any user, verified email | `{name}` (one line, 1 to 200 characters, trimmed). 201 `FirmOut`; 403 `email-unverified`; 422 on the name. `Idempotency-Key` required. |
| `PATCH /v1/orgs/{org_id}` | owner | `{name}`. 200 `FirmOut` with `role`; 403 below owner; 404 non-member (firms are not enumerable); 422 on the name. Renaming to the current name is a no-op 200. No `Idempotency-Key` (the write is idempotent by nature, like `PUT .../intake`). |
| `GET /v1/me` | any user | Unchanged: `memberships` lists the firms by name, so a rename shows there. |

`FirmOut`: `id`, `name`, `created_at`, `role` (the caller's).

## 3. Invitation emails

An invitation is still bound to an email, not to a secret link (the GitHub organization
pattern): the invitee must sign in with that address, verified, and accept in the app.
What changed is that they are now told.

### 3.1 The path

```
POST /orgs/{id}/invitations ─┐  one transaction
  INSERT invitations          │  email_queued_at = now()
  INSERT outbox               │  kind notification, payload {type: invitation, invitation_id, org_id, email}
  COMMIT ───────────────────┘
relay (python -m basis.relay), every RELAY_POLL_SECONDS:
  claim the row ── load the invitation ── still pending and not yet emailed?
     ── render (basis.email.invitation) ── EmailSender.send ── record ── commit
```

* **Same transaction.** The outbox row is written by `InvitationRepository.create`, which
  the API and the CLI both call, so an invitation that commits is emailed and one that rolls
  back (or is refused: 409 `already-member`, `invitation-pending`) is not.
* **The relay sends; the API never does.** The existing outbox relay delivers
  `notification` rows; the invitation is one with `type: invitation`. It inherits the
  relay's retries (`min(2**attempts, 300)` s, dead after 50), its `FOR UPDATE SKIP LOCKED`
  claims (several relays never send the same row at once) and its 30 s delivery timeout.
* **Once per invitation.** Before sending, the relay checks `emailed_at`; a redelivered row
  (the relay died between the send and the commit) is marked delivered without sending. The
  window that check cannot close, a crash after the provider accepted and before the
  commit, is closed by the message's idempotency key `invitation-{id}`: an HTTP provider
  that honours one (Resend's `Idempotency-Key`) answers the retry without sending; SMTP
  gets it as the `Message-ID` (`<invitation-{id}@from-domain>`), which receiving servers
  use to collapse duplicates.
* **Not sent when it no longer matters.** An invitation revoked or answered before the
  relay reached it is not emailed; the row is `delivered` with `last_error` saying so, and
  the invitation's `email_error` says so too.
* **Re-inviting is a new email.** Each invitation row is emailed once. To send again, revoke
  and invite again (a new row, a new key); re-inviting after expiry does the same by itself.

### 3.2 What the owner sees

`GET /v1/orgs/{org_id}/invitations` (and the create's answer) carries, per invitation:

| Field | Meaning |
|---|---|
| `delivery` | `queued` (waiting for the relay), `sent` (handed to the sender; with `EMAIL_PROVIDER=log` it was only logged), `failed` (the last attempt failed; the relay retries until it succeeds or gives up), `not_sent` (created before invitation emails existed; nothing was queued). |
| `email_queued_at` | When the message was enqueued (the invitation's creation). |
| `emailed_at` | When the relay handed it to the sender. Set once. |
| `email_error` | The last send error while `delivery` is `failed`, or why no email was sent (revoked first). Cleared when a later attempt succeeds. |

The invitee's view (`GET /v1/me`, the accept and decline answers) does not carry these: the
invitation works whether or not the email arrived.

### 3.3 The message

Plain text, no tracking, no link other than `APP_URL`:

```
Subject: Olivia Owner invited you to Harbor Cost Seg on Basis

Hello,

Olivia Owner (olivia@example.com) invited you to join Harbor Cost Seg on Basis as an
engineer (creates and reviews studies).

To accept, sign in to Basis at https://cost-seg-basis.web.app with this email address
(drew@example.com), using a sign-in that confirms it (Google sign-in does). The
invitation is shown after you sign in; accept or decline it there.

This invitation expires on 15 November 2026 (UTC). If you were not expecting it, ignore
this message: nothing happens until you accept.

Basis
```

An invitation the operator CLI issued (no inviter) reads "Basis set up Harbor Cost Seg and
invited you to join it". Without `APP_URL` the text says "sign in to Basis".

## 4. The sender boundary

`basis/email/sender.py`: `EmailMessage(to, subject, text, idempotency_key)` in,
`EmailSender.send(message) -> SendResult(provider, message_id)` out. Rendering and transport
are separate, as Django's `EmailMessage`/`EMAIL_BACKEND` and Rails' `ActionMailer`/
`delivery_method` separate them: the template is tested without a server, and the transport
is chosen by configuration, never by the caller.

| `EMAIL_PROVIDER` | What it does | When |
|---|---|---|
| `log` (default) | Logs the rendered message at INFO (`email (not sent: EMAIL_PROVIDER=log)` with `email_to`, `email_subject`, `email_text`) and sends nothing. `delivery` still reads `sent`: the sender accepted it. | Development, tests, a deployment with no mail provider yet. An operator can forward the logged text by hand. |
| `smtp` | The standard library's `smtplib` in a worker thread: STARTTLS on any port but 465, implicit TLS on 465, `SMTP_USERNAME`/`SMTP_PASSWORD` when set, `From: EMAIL_FROM`, `Auto-Submitted: auto-generated`, deterministic `Message-ID`. A 5xx on the recipient is final (the outbox row is `dead` at once, `email_error` says `recipient refused`); a 4xx, a connection failure or a timeout is retried. | Any provider's SMTP relay (Postmark, Amazon SES, Resend, SendGrid, Google Workspace): nothing is signed up for in the code. |
| a provider's HTTP API | **The slot.** Implement `EmailSender` (one class, `send` only), map `idempotency_key` to the provider's idempotency header, raise `EmailRejectedError` for a final refusal and `EmailSendError` for anything retryable, add the name to `build_email_sender` and to the `Literal` in `basis/config.py`. | When a firm needs the provider's bounce and complaint webhooks or templates. |

Errors: `BASIS-INF-005` (`invitation email failed`) on every failed attempt, in the relay's
log, with the attempt count; the catalogue entry is in `docs/ERROR_CODES.md`.

## 5. Names on studies and signatures

`GET /v1/studies` and `GET /v1/studies/{id}` carry `created_by_name` and `created_by_email`
next to `created_by` (from `users`: the display name and email the creator's sign-in last
carried; null when unknown). The report signature (`POST .../report/sign`,
`GET .../report/state`) carries `signed_by_name` and `signed_by_email` next to `signed_by`;
the name the engineer attested to stays in `engineer`. One `users` read per response.

`GET /v1/orgs/{org_id}/members` (owners), and the answers of the role change and the removal,
carry each member's `signer` (the name, credentials and license they sign with; null
when they saved none). Read-only: only the member writes their own (`PUT /v1/me/signer`,
docs/api/SIGNERS.md). One signer-details read per response.

## 6. Settings

| Variable | Default | Read by | Meaning |
|---|---|---|---|
| `APP_URL` | unset | relay | The app's public URL in the invitation text (`https://cost-seg-basis.web.app`). `scheme://host[:port][/path]`, no trailing slash; outside dev and test https or `http://localhost`. Unset: "sign in to Basis". |
| `EMAIL_PROVIDER` | `log` | relay | `log` or `smtp` (section 4). |
| `EMAIL_FROM` | unset | relay | `Name <address>` or an address. Required with `smtp`; with `log` it only names the From in the log. |
| `SMTP_HOST`, `SMTP_PORT` | unset, `587` | relay | The relay's SMTP server. Required with `smtp`. 465 means implicit TLS. |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | unset | relay | Credentials when the server wants them. The password is a secret (never in `repr`, never logged). |
| `SMTP_STARTTLS` | `true` | relay | Off only for a local test server. |
| `FIRM_SELF_SERVE` | `false` | API | Whether a signed-in user may create a firm with `POST /v1/orgs` (section 1). Off: Basis sets firms up with `python -m basis.admin create-firm`. |
| `INVITATION_TTL_DAYS` | `14` | API, CLI | Unchanged: how long an invitation can be accepted (the email states the date). |
| `AUTH_LINK_VERIFIED_EMAIL` | `false` | API | A first sign-in whose verified email a firm member already has joins that member's user (section 6.1). On in `docker-compose.web.yml` for local stacks. |

The settings are read by every process (one `Settings`), but only the relay sends. A
misconfigured `smtp` (no host, no from) refuses to start every process, so it is found at
deploy, not at the first invitation.

### 6.1 One person, one user

A Basis user is keyed by the Firebase uid they first signed in with, and that uid never
changes on the user (`users.firebase_uid`). When the same person comes back with a **new**
uid (their Firebase account was deleted and created again, or a local Auth emulator lost its
accounts on restart), Basis used to create a second user in no firm. A firm that re-invited
them then listed the same email twice on the members page, once without a name if the new
sign-in was email-and-password (EVAL_REPORT section 93).

A new uid can now be linked to the existing user (`user_linked_accounts`, migration 0031):
it then signs in as that user, with their firms, studies and history, and the members page
keeps one row. Two ways:

* **The operator**, in production:
  `python -m basis.admin link-account --email <address> --uid <new uid> [--user-id <id>]`.
  Check in the Firebase console that the uid belongs to that address and that the person
  asked. A uid that is already a user of its own, or already linked, is refused; several
  users with the address need `--user-id`.
* **At sign-in**, with `AUTH_LINK_VERIFIED_EMAIL=true` (local stacks): a first sign-in of a
  new uid whose email is verified and equal (trimmed, case-insensitive) to the stored email
  of a user who belongs to a firm is linked to that user (the most recently seen one, if
  there are several). A user in no firm is never a target, an unverified email never links,
  and a uid that already has its own user keeps it. Two first sign-ins of the same new uid
  at once take the uid's advisory lock, so they link once.

Why automatic linking is off in production: a verified email shows who held the address
when it was verified, not that this is the person who held it before. Auth0's guidance is
not to link accounts automatically on email for that reason, and the account pre-hijacking
study (Sudhodanan and Paverd, USENIX Security 2022) shows the attacks that follow from
merging on an identifier. A reassigned work address (a departed engineer's mailbox given to
a new hire) would otherwise hand the old member's firms to the new holder. Firebase's
default "one account per email address" means a second uid for an address only appears
after the first account was deleted, which is rare and worth an operator's look.

## 7. Visibility of studies within a firm (not changed)

Every member of a firm sees every study of the firm, whatever their role; `viewer` reads,
`engineer` creates and reviews, `owner` administers. The frontend design's split between
"My studies" and "Workspace studies" (an engineer sees only their own) is **not** enforced
and no `visibility` field was added here. Why not now: the product rule is not settled
(a reviewer must see a colleague's study to take it over; an owner must see all; whether a
creator may hide one is a decision for the firms being pitched), and a stored field that
nothing enforces would mislead the client. When the rule is decided it is one nullable
column on `studies`, a filter in `StudyRepository.list_for_user` and a `visibility` member
on `StudyOut`; `created_by_name` (section 5) already lets the client offer the split as a
client-side filter.

## 8. Tests

`tests/unit/email/test_invitation_email.py` (rendering, the log sender, the SMTP sender
against a local SMTP server, the factory), `tests/unit/config/test_email_settings.py`,
`tests/unit/relay/test_relay_outcomes.py` (the email outcome record),
`tests/integration/relay/test_invitation_emails.py` (same transaction, once, retries,
rejected, revoked first, log sender), `tests/api/test_firms.py` (create, rename,
authorization, delivery fields, creator names), `tests/api/test_report_signing.py`
(signer names), `tests/integration/db/test_admin_cli.py` (the CLI's invitation is queued),
`tests/integration/db/test_migrations.py` (0025). Rows in `tests/LEDGER.md`.
