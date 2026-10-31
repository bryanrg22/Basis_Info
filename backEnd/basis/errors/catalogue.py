"""The error-code catalogue: what every code means, where it occurs, and what to do.

One :class:`Entry` per code of :class:`basis.errors.codes.E`. The document
``docs/ERROR_CODES.md`` is rendered from this module (``python -m basis.errors.catalogue
--write``; a test fails while it is stale, like ``docs/api/openapi.json``) so the tables and
the code can never disagree.

``kind``:

* ``refusal``: the API refused a request (a 4xx problem response carries the code as its
  ``code`` member next to RFC 9457's ``type``);
* ``error``: a step of the workflow failed (the code is inside the activity's error message,
  ``... [BASIS-...]``, so it is in ``activity_results.error``, in the study's ``failure`` /
  ``failure_reason`` and ``failure_code``, in the ``study_events`` row and in the logs);
* ``flag``: a check that did not fail anything but put a row in front of the engineer (the
  ``review_reasons`` of an asset or extraction, a photo's or room's ``needs_review``);
  ``signal`` is the string stored on the row, and :func:`code_for_reason` maps it back;
* ``outcome``: a recorded, non-error state worth knowing (a skipped localization).

``retry``: ``no`` (the input or configuration must change first), ``automatic`` (Temporal
retries it; nothing to do unless it keeps failing), ``after fix`` (``POST
/v1/studies/{id}/retry`` once the cause is fixed), ``yes`` (repeat the request).

Numbering follows SQLSTATE's rule (a class, then a member; numbers are never reused) and the
format of Stripe's error-code table (one row per identifier with meaning and next step) and
Google's AIP-193 ``ErrorInfo`` (a stable identifier beside the human text). Standard library
only, like :mod:`basis.errors.codes`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from basis.errors.codes import CODE_PATTERN, E, all_codes

Kind = Literal["refusal", "error", "flag", "outcome"]
Retry = Literal["no", "automatic", "after fix", "yes"]

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
DOCUMENT_PATH: Final = REPO_ROOT / "docs" / "ERROR_CODES.md"

AREA_TITLES: Final[dict[str, str]] = {
    "API": "API: request plumbing (authentication, idempotency, limits, dependencies)",
    "FIRM": "Firms and membership",
    "UPLOAD": "Uploads and documents",
    "STUDY": "Study lifecycle (start, decisions, retry, outputs)",
    "OBJECT": "Photo objects (an engineer's selections)",
    "WF": "Workflow and Temporal",
    "PROVIDER": "Model providers, Azure Document Intelligence, SAM 3",
    "BUDGET": "The study's spend budget",
    "INGEST": "ingest_appraisal",
    "EXTRACT": "extract_fields",
    "PHOTO": "analyze_photo",
    "LOCALIZE": "localize_photo",
    "ROOMS": "reconcile_rooms",
    "CORRECT": "Corrections (apply_corrections and the shared checks)",
    "ASSETS": "process_assets",
    "EXPORT": "export_report",
    "CHECK": "Checks that flag a row for review",
    "COST": "The cost index store: the missing-year rule, conversions, acquisition",
    "INF": "Infrastructure: worker start-up and the outbox relay",
}


@dataclass(frozen=True, slots=True)
class Entry:
    code: str
    kind: Kind
    step: str
    component: str
    what: str
    seen: str
    fix: str
    retry: Retry
    problem: str | None = None  # the RFC 9457 type suffix (refusals)
    temporal_type: str | None = None  # the ApplicationError / TimeoutError type (errors)
    signal: str | None = None  # the review-reason string or prefix on the row (flags)

    @property
    def area(self) -> str:
        return self.code.split("-")[1]

    @property
    def name(self) -> str:
        return _NAMES[self.code]


_NAMES: Final = {code: name for name, code in all_codes().items()}


def _e(
    code: str,
    kind: Kind,
    step: str,
    component: str,
    what: str,
    seen: str,
    fix: str,
    retry: Retry,
    *,
    problem: str | None = None,
    temporal_type: str | None = None,
    signal: str | None = None,
) -> Entry:
    return Entry(
        code, kind, step, component, what, seen, fix, retry, problem, temporal_type, signal
    )


RETRY_ROUTE: Final = "`POST /v1/studies/{id}/retry`"
API_ERRORS: Final = "basis.api.errors"
PROBLEM: Final = "a problem response"

ENTRIES: Final[tuple[Entry, ...]] = (
    # ------------------------------------------------------------------ API
    _e(
        E.API_AUTH_MISSING,
        "refusal",
        "any request",
        "basis.api.auth",
        "No `Authorization: Bearer` header.",
        "401 `unauthorized`, `WWW-Authenticate: Bearer`.",
        "Send a Firebase ID token.",
        "yes",
        problem="unauthorized",
    ),
    _e(
        E.API_AUTH_MALFORMED,
        "refusal",
        "any request",
        "basis.api.auth",
        "The header is not `Bearer <token>`.",
        "401 `unauthorized`.",
        "Fix the header format.",
        "yes",
        problem="unauthorized",
    ),
    _e(
        E.API_AUTH_EXPIRED,
        "refusal",
        "any request",
        "basis.auth.firebase",
        "The ID token has expired.",
        '401 `unauthorized`, `error="invalid_token"`.',
        "Refresh the token and repeat.",
        "yes",
        problem="unauthorized",
    ),
    _e(
        E.API_AUTH_REVOKED,
        "refusal",
        "any request",
        "basis.auth.firebase",
        "The ID token was revoked (production checks revocation).",
        "401 `unauthorized`.",
        "Sign in again.",
        "yes",
        problem="unauthorized",
    ),
    _e(
        E.API_AUTH_DISABLED,
        "refusal",
        "any request",
        "basis.auth.firebase",
        "The Firebase user is disabled.",
        "401 `unauthorized`.",
        "An administrator must re-enable the account.",
        "no",
        problem="unauthorized",
    ),
    _e(
        E.API_AUTH_DELETED,
        "refusal",
        "any request",
        "basis.auth.firebase",
        "The Firebase user was deleted; the token is still signed.",
        "401 `unauthorized`.",
        "Create or restore the account.",
        "no",
        problem="unauthorized",
    ),
    _e(
        E.API_AUTH_INVALID,
        "refusal",
        "any request",
        "basis.auth.firebase",
        "The token does not verify (wrong project, tampered, not an ID token).",
        "401 `unauthorized`.",
        "Send a token for this project.",
        "yes",
        problem="unauthorized",
    ),
    _e(
        E.API_AUTH_NO_UID,
        "refusal",
        "any request",
        "basis.auth.firebase",
        "The token verified but carries no `uid`/`sub` claim.",
        "401 `unauthorized`.",
        "Report to the operator: the identity provider's claims changed.",
        "no",
        problem="unauthorized",
    ),
    _e(
        E.API_AUTH_CERTIFICATES_UNAVAILABLE,
        "refusal",
        "any request",
        "basis.auth.firebase",
        "Google's public signing keys could not be fetched.",
        "503 `service-unavailable`.",
        "Retry; check the server's egress to Google if it persists.",
        "yes",
        problem="service-unavailable",
    ),
    _e(
        E.API_FORBIDDEN,
        "refusal",
        "a route with a role requirement",
        "basis.api.users",
        "The caller's role in the study's or firm's organization is too low (viewer < engineer "
        "< owner).",
        "403 `forbidden` naming the role needed.",
        "Ask an owner to raise the role, or have someone with the role act.",
        "no",
        problem="forbidden",
    ),
    _e(
        E.API_NOT_FOUND,
        "refusal",
        "any route with an id",
        API_ERRORS,
        "The row does not exist or is not visible to the caller (the two are "
        "indistinguishable on purpose).",
        "404 `not-found`.",
        "Check the id; check the caller is a member of the study's firm.",
        "no",
        problem="not-found",
    ),
    _e(
        E.API_CONFLICT,
        "refusal",
        "a write",
        API_ERRORS,
        "A repository conflict with no more specific type.",
        "409 `conflict`.",
        "Read the current state and repeat with it.",
        "yes",
        problem="conflict",
    ),
    _e(
        E.API_VALIDATION,
        "refusal",
        "any request with a body or query",
        API_ERRORS,
        "The request did not validate: a field is missing, out of range, the wrong type, or "
        "(intake) violates a rule such as a non-capitalizable cost category.",
        "422 `validation` with one `errors[]` entry per field (`loc`, `detail`, `type`).",
        "Fix the fields named in `errors`.",
        "yes",
        problem="validation",
    ),
    _e(
        E.API_UNPROCESSABLE,
        "refusal",
        "a write",
        API_ERRORS,
        "Unprocessable with no more specific type.",
        "422 `unprocessable`.",
        "Read `detail`.",
        "no",
        problem="unprocessable",
    ),
    _e(
        E.API_BAD_REQUEST,
        "refusal",
        "a write",
        API_ERRORS,
        "Bad request with no more specific type.",
        "400 `bad-request`.",
        "Read `detail`.",
        "no",
        problem="bad-request",
    ),
    _e(
        E.API_PAYLOAD_TOO_LARGE,
        "refusal",
        "any `/v1` request",
        "basis.api.bodylimit",
        "The JSON body is larger than 1 MiB.",
        "413 `payload-too-large`.",
        "Files never go to the API: request a signed URL and upload there.",
        "no",
        problem="payload-too-large",
    ),
    _e(
        E.API_RATE_LIMITED,
        "refusal",
        "any request",
        "basis.api.ratelimit",
        "The caller exceeded the per-user limit (240 reads or 60 mutations per minute by default).",
        "429 `rate-limited` with `Retry-After`.",
        "Wait `Retry-After` seconds.",
        "yes",
        problem="rate-limited",
    ),
    _e(
        E.API_DATABASE_BUSY,
        "refusal",
        "any request",
        API_ERRORS,
        "Every pooled database connection was busy for `pool_timeout`.",
        "503 `service-unavailable`, `Retry-After: 2`.",
        "Retry; if it persists the API is under-provisioned (docs/perf/LOAD_TESTING.md).",
        "yes",
        problem="service-unavailable",
    ),
    _e(
        E.API_DATABASE_UNAVAILABLE,
        "refusal",
        "any request",
        API_ERRORS,
        "A new database connection could not be opened (server down, network, "
        '"cannot accept connections now").',
        "503 `service-unavailable`, `Retry-After: 5`.",
        "Retry; check Cloud SQL and the connector.",
        "yes",
        problem="service-unavailable",
    ),
    _e(
        E.API_NO_DATABASE,
        "refusal",
        "any `/v1` request",
        "basis.api.users",
        "The API started with neither `DATABASE_URL` nor `CLOUD_SQL_INSTANCE`.",
        "503 `service-unavailable`.",
        "Configure the database and restart the API.",
        "no",
        problem="service-unavailable",
    ),
    _e(
        E.API_TEMPORAL_NOT_CONFIGURED,
        "refusal",
        "start, workflow, cancel, retry",
        "basis.api.routes.workflow",
        "`TEMPORAL_ADDRESS` is empty: the API has no workflow service.",
        "503 `service-unavailable`.",
        "Configure Temporal and restart the API.",
        "no",
        problem="service-unavailable",
    ),
    _e(
        E.API_TEMPORAL_UNAVAILABLE,
        "refusal",
        "start, workflow, cancel, retry",
        "basis.api.routes.workflow",
        "Temporal did not answer within the call timeout or refused the connection.",
        "503 `service-unavailable`, `Retry-After: 5`.",
        "Retry; a start that got this far already moved the study to `extracting`, and the "
        "repeat converges.",
        "yes",
        problem="service-unavailable",
    ),
    _e(
        E.API_SERVICE_UNAVAILABLE,
        "refusal",
        "any request",
        API_ERRORS,
        "A dependency is unavailable with no more specific code.",
        "503 `service-unavailable`.",
        "Retry after `Retry-After`.",
        "yes",
        problem="service-unavailable",
    ),
    _e(
        E.API_INTERNAL_ERROR,
        "refusal",
        "any request",
        API_ERRORS,
        "An unexpected exception escaped a route; the stack trace is in the API log under the "
        "request id.",
        "500 (`about:blank`) with `request_id`.",
        "Search the API logs for the `request_id`; file the defect.",
        "yes",
    ),
    _e(
        E.API_HTTP_ERROR,
        "refusal",
        "any request",
        API_ERRORS,
        "A plain HTTP error: an unknown path (404) or a method the path does not serve (405, "
        "with `Allow`).",
        "4xx `about:blank`.",
        "Check the path and method against docs/api/openapi.json.",
        "no",
    ),
    _e(
        E.API_IDEMPOTENCY_KEY_MISSING,
        "refusal",
        "POST, DELETE",
        "basis.api.idempotency",
        "No `Idempotency-Key` header.",
        "400 `idempotency-key-missing`.",
        "Send a fresh key (a UUID) per request.",
        "yes",
        problem="idempotency-key-missing",
    ),
    _e(
        E.API_IDEMPOTENCY_KEY_INVALID,
        "refusal",
        "POST, DELETE",
        "basis.api.idempotency",
        "The key has a character outside the documented class or is too long.",
        "400 `idempotency-key-invalid`.",
        "Use visible ASCII (a UUID).",
        "yes",
        problem="idempotency-key-invalid",
    ),
    _e(
        E.API_IDEMPOTENCY_KEY_REUSED,
        "refusal",
        "POST, DELETE",
        "basis.api.idempotency",
        "The key was used within 24 hours for a different route or body.",
        "422 `idempotency-key-reused`.",
        "Use a new key for a new request.",
        "yes",
        problem="idempotency-key-reused",
    ),
    _e(
        E.API_IDEMPOTENCY_KEY_IN_FLIGHT,
        "refusal",
        "POST, DELETE",
        "basis.api.idempotency",
        "The first request with this key is still running.",
        "409 `idempotency-key-in-flight`.",
        "Wait for it, then repeat with the same key to get its stored answer.",
        "yes",
        problem="idempotency-key-in-flight",
    ),
    _e(
        E.API_INVALID_CURSOR,
        "refusal",
        "paginated reads",
        API_ERRORS,
        "The `cursor` does not decode (it is an opaque token the server issued).",
        "422 `invalid-cursor`.",
        "Pass a `next_cursor` from the previous page unchanged.",
        "yes",
        problem="invalid-cursor",
    ),
    _e(
        E.API_ORG_NOT_FOUND,
        "refusal",
        "firm routes, `POST /studies` with `org_id`",
        "basis.api.users",
        "The caller is not a member of that firm (firms are not enumerable).",
        "404 `not-found`.",
        "Check `GET /v1/me` for the caller's firms.",
        "no",
        problem="not-found",
    ),
    _e(
        E.API_REPRICE_INPUTS_CHANGED,
        "refusal",
        "`POST /studies/{id}/assets/reprice`",
        "basis.costs.reprice",
        "The `fingerprint` sent is not the current one: the intake, the cost index store, "
        "the square-foot model or a pricing setting changed since the preview.",
        "409 `reprice-inputs-changed`.",
        "Preview again (`GET .../assets/reprice`), review the new impact, send its fingerprint.",
        "yes",
        problem="reprice-inputs-changed",
    ),
    _e(
        E.API_REPRICE_RECLASSIFY,
        "refusal",
        "`GET`/`POST /studies/{id}/assets/reprice`",
        "basis.costs.reprice",
        "The intake's property use changed since the assets were classified (27.5- or "
        "39-year building, the rules table's class): carrying the classifications would be "
        "wrong.",
        "409 `reprice-reclassify`; the preview's `blocked_code`.",
        "Reject the assets review so the assets are classified again.",
        "no",
        problem="reprice-reclassify",
    ),
    _e(
        E.API_REPRICE_NOT_AT_REVIEW,
        "refusal",
        "`GET`/`POST /studies/{id}/assets/reprice`",
        "basis.costs.reprice",
        "The study is not waiting at the assets review of that revision.",
        "409 `gate-not-open`; the preview's `blocked_code`.",
        "Wait for the assets review to open, or use the current revision.",
        "yes",
        problem="gate-not-open",
    ),
    # ------------------------------------------------------------------ FIRM
    _e(
        E.FIRM_NO_FIRM,
        "refusal",
        "`GET /studies`, `POST /studies`",
        "basis.api.users",
        "The caller belongs to no firm: firms are invite-only.",
        "403 `no-firm`.",
        "Accept an invitation (`GET /v1/me` lists the ones waiting for the verified email).",
        "no",
        problem="no-firm",
    ),
    _e(
        E.FIRM_ORG_REQUIRED,
        "refusal",
        "`POST /studies`",
        "basis.api.routes.studies",
        "The caller may create studies in several firms and gave no `org_id`.",
        "422 `org-required`.",
        "Set `org_id`.",
        "yes",
        problem="org-required",
    ),
    _e(
        E.FIRM_EMAIL_UNVERIFIED,
        "refusal",
        "accept or decline an invitation",
        "basis.db.repositories.invitation",
        "The token does not assert a verified email, which an invitation is bound to.",
        "403 `email-unverified`.",
        "Verify the email with the identity provider, sign in again.",
        "no",
        problem="email-unverified",
    ),
    _e(
        E.FIRM_LAST_OWNER,
        "refusal",
        "change a role, remove a member",
        "basis.db.repositories.org",
        "The change would leave the firm without an owner.",
        "409 `last-owner`.",
        "Make another member owner first.",
        "no",
        problem="last-owner",
    ),
    _e(
        E.FIRM_ALREADY_MEMBER,
        "refusal",
        "invite",
        "basis.db.repositories.invitation",
        "The email already belongs to a member of the firm.",
        "409 `already-member`.",
        "Nothing to do; change their role instead.",
        "no",
        problem="already-member",
    ),
    _e(
        E.FIRM_INVITATION_PENDING,
        "refusal",
        "invite",
        "basis.db.repositories.invitation",
        "The firm already has an open invitation for this email.",
        "409 `invitation-pending`.",
        "Revoke it first, or wait for the answer.",
        "no",
        problem="invitation-pending",
    ),
    _e(
        E.FIRM_INVITATION_ACCEPTED,
        "refusal",
        "answer or revoke an invitation",
        "basis.db.repositories.invitation",
        "The invitation was already accepted.",
        "409 `invitation-accepted`.",
        "Nothing to do.",
        "no",
        problem="invitation-accepted",
    ),
    _e(
        E.FIRM_INVITATION_DECLINED,
        "refusal",
        "answer or revoke an invitation",
        "basis.db.repositories.invitation",
        "The invitation was declined.",
        "409 `invitation-declined`.",
        "Invite again.",
        "no",
        problem="invitation-declined",
    ),
    _e(
        E.FIRM_INVITATION_REVOKED,
        "refusal",
        "answer an invitation",
        "basis.db.repositories.invitation",
        "The invitation was revoked.",
        "409 `invitation-revoked`.",
        "Ask for a new invitation.",
        "no",
        problem="invitation-revoked",
    ),
    _e(
        E.FIRM_INVITATION_EXPIRED,
        "refusal",
        "answer an invitation",
        "basis.db.repositories.invitation",
        "The invitation expired.",
        "409 `invitation-expired`.",
        "Ask for a new invitation.",
        "no",
        problem="invitation-expired",
    ),
    # ------------------------------------------------------------------ UPLOAD
    _e(
        E.UPLOAD_DUPLICATE_DOCUMENT,
        "refusal",
        "register a document",
        "basis.api.routes.documents",
        "The study already has a document with this sha256.",
        "409 `duplicate-document` naming the existing document.",
        "Use the existing document.",
        "no",
        problem="duplicate-document",
    ),
    _e(
        E.UPLOAD_MISSING,
        "refusal",
        "confirm, read or render a document",
        "basis.api.routes.documents",
        "No object was uploaded to the signed URL yet (confirm), "
        "or the upload was never confirmed (read).",
        "409 `upload-missing`.",
        "Upload the bytes to the signed URL, then `POST .../complete`.",
        "yes",
        problem="upload-missing",
    ),
    _e(
        E.UPLOAD_MISMATCH,
        "refusal",
        "confirm an upload",
        "basis.api.routes.documents",
        "The stored object's size, content type or sha256 differs from what was registered. "
        "The object was deleted.",
        "422 `upload-mismatch` saying which differed.",
        "Upload the registered file again.",
        "yes",
        problem="upload-mismatch",
    ),
    _e(
        E.UPLOAD_INVALID_APPRAISAL,
        "refusal",
        "confirm an appraisal upload",
        "basis.extraction.package",
        "The appraisal is not a PDF, a MISMO 3.6 XML or a safe UAD "
        "3.6 ZIP of its declared type. The object was deleted.",
        "422 `invalid-appraisal` with the reason.",
        "Upload the report PDF, the XML or the delivery ZIP (docs/api/UAD36_INTAKE.md).",
        "no",
        problem="invalid-appraisal",
    ),
    _e(
        E.UPLOAD_NO_REPORT_PDF,
        "refusal",
        "render a document's PDF",
        "basis.api.routes.documents",
        "The document has no report PDF to render: a bare XML with no companion, or a package "
        "that is not usable.",
        "409 `no-report-pdf`.",
        "Upload the report PDF as another appraisal document.",
        "no",
        problem="no-report-pdf",
    ),
    _e(
        E.UPLOAD_DOCUMENT_NOT_FOUND,
        "refusal",
        "document routes",
        "basis.api.routes.documents",
        "No document with that id in the study.",
        "404 `not-found`.",
        "Check the id.",
        "no",
        problem="not-found",
    ),
    _e(
        E.UPLOAD_BYTES_MISSING,
        "refusal",
        "read a document",
        "basis.api.routes.documents",
        "The row says uploaded but the object is gone from storage.",
        "404 `not-found`.",
        "Operator: check the bucket's lifecycle rules; re-upload the file.",
        "no",
        problem="not-found",
    ),
    _e(
        E.UPLOAD_LOCAL_DISABLED,
        "refusal",
        "dev upload route",
        "basis.api.local_uploads",
        "The dev upload route was called on a deployment that uses GCS.",
        "403 `forbidden`.",
        "Upload to the signed GCS URL.",
        "no",
        problem="forbidden",
    ),
    _e(
        E.UPLOAD_LOCAL_TOO_LARGE,
        "refusal",
        "dev upload route",
        "basis.api.local_uploads",
        "The upload is larger than the maximum.",
        "413 `payload-too-large`.",
        "Upload a smaller file.",
        "no",
        problem="payload-too-large",
    ),
    _e(
        E.UPLOAD_LOCAL_REJECTED,
        "refusal",
        "dev upload route",
        "basis.api.local_uploads",
        "The signed token is unknown, expired, or the headers differ from the ticket.",
        "403 `forbidden`.",
        "Request a new upload ticket.",
        "yes",
        problem="forbidden",
    ),
    # ------------------------------------------------------------------ STUDY
    _e(
        E.STUDY_INTAKE_REQUIRED,
        "refusal",
        "`POST /studies/{id}/start`",
        "basis.api.routes.workflow (`basis.domain.intake.missing_for_start`)",
        "The study's intake lacks a field the output cannot be produced without (price, "
        "acquisition costs, land source and value, use, placed-in-service date, state).",
        "422 `intake-required` with one `errors[]` entry per missing field "
        '(`loc` `["intake", <field>]`).',
        "`PUT /v1/studies/{id}/intake` with the fields "
        "named (docs/api/STUDY_RESULT.md), then start again.",
        "yes",
        problem="intake-required",
    ),
    _e(
        E.STUDY_APPRAISAL_REQUIRED,
        "refusal",
        "`POST /studies/{id}/start`",
        "basis.api.routes.workflow",
        "No appraisal document has a confirmed upload.",
        "409 `appraisal-required`.",
        "Upload and confirm the appraisal, then start again.",
        "yes",
        problem="appraisal-required",
    ),
    _e(
        E.STUDY_ARCHIVED,
        "refusal",
        "any write to a study",
        "basis.api.routes.*",
        "The study is archived (soft deleted).",
        "409 `study-archived`.",
        "Nothing: archived studies are read-only.",
        "no",
        problem="study-archived",
    ),
    _e(
        E.STUDY_STALE_STATE,
        "refusal",
        "decisions, intake, transitions",
        "basis.db.repositories.study",
        "A compare-and-set lost: the study is not at the stage "
        "and revision the request assumed (another decision landed first, or the decision "
        "names an old revision).",
        "409 `stale-state` with the current stage and revision.",
        "`GET /v1/studies/{id}` and decide on the current stage and revision.",
        "yes",
        problem="stale-state",
    ),
    _e(
        E.STUDY_GATE_NOT_OPEN,
        "refusal",
        "`POST .../decisions`",
        "basis.db.repositories.review",
        "The study is at the review stage but the workflow has not opened the gate: the work "
        "it reviews is still running (`processing`) or a stage failed (`failed`).",
        "409 `gate-not-open` with the status.",
        "Wait for `waiting_review` (poll `GET .../workflow`), or retry the failed stage first.",
        "yes",
        problem="gate-not-open",
    ),
    _e(
        E.STUDY_WORKFLOW_MISSING,
        "refusal",
        "`POST .../start` on a started study",
        "basis.api.routes.workflow",
        "Temporal has no record of `study-{id}` although the study "
        "progressed past its first stage (namespace retention, a different namespace).",
        "409 `workflow-missing`.",
        "Operator: the execution is gone; the study cannot be "
        "resumed automatically. Start a new study, or restore the namespace.",
        "no",
        problem="workflow-missing",
    ),
    _e(
        E.STUDY_WORKFLOW_CLOSED,
        "refusal",
        "cancel, retry, start",
        "basis.api.routes.workflow",
        "The execution has closed (completed, failed, cancelled, terminated, timed out).",
        "409 `workflow-closed` naming the status.",
        "A failed or terminated execution cannot "
        "be resumed; its run is kept for investigation (`GET .../workflow`, "
        "`GET .../timeline`).",
        "no",
        problem="workflow-closed",
    ),
    _e(
        E.STUDY_NOT_FAILED,
        "refusal",
        "`POST .../retry`",
        "basis.api.routes.workflow",
        "No stage has failed, or the study was cancelled: the workflow's `retry` validator "
        "refused.",
        "409 `study-not-failed` with the workflow's reason.",
        "Nothing to retry; check `GET .../workflow` `failed_stage`.",
        "no",
        problem="study-not-failed",
    ),
    _e(
        E.STUDY_NO_WORKFLOW,
        "refusal",
        "workflow, cancel, retry",
        "basis.api.routes.workflow",
        "The study has no workflow yet.",
        "404 `not-found`.",
        "Start the study first.",
        "yes",
        problem="not-found",
    ),
    _e(
        E.STUDY_NO_SUCH_REVISION,
        "refusal",
        "outputs with `?revision=`",
        "basis.api.routes.outputs",
        "The revision asked for is above the study's current one.",
        "404 `not-found`.",
        "Ask for a revision up to `revision` of `GET /v1/studies/{id}`.",
        "no",
        problem="not-found",
    ),
    _e(
        E.STUDY_NO_EXTRACTION,
        "refusal",
        "`GET .../extraction`",
        "basis.api.routes.outputs",
        "The revision has no appraisal extraction (only the first revision and appraisal "
        "rejections extract).",
        "404 `not-found`.",
        "Read the extraction of the revision that "
        "extracted (the fields of this revision were carried from it).",
        "no",
        problem="not-found",
    ),
    _e(
        E.STUDY_DECISION_KEY_REUSED,
        "refusal",
        "`POST .../decisions`",
        "basis.db.repositories.review",
        "The `Idempotency-Key` was already used for another "
        "decision (the review row's second guard).",
        "409 `duplicate-key`.",
        "Use a fresh key.",
        "yes",
        problem="duplicate-key",
    ),
    _e(
        E.STUDY_CORRECTION_TYPE,
        "refusal",
        "`POST .../decisions`",
        "basis.api.routes.decisions",
        "A correction's `entity_type` is not one the decided stage accepts.",
        '422 `validation`, `errors[].type` `correction_type` at `["body", "corrections", '
        '<i>, "entity_type"]`.',
        "Use the types the operation description lists for the stage.",
        "yes",
        problem="validation",
    ),
    _e(
        E.STUDY_CORRECTION_TARGET,
        "refusal",
        "`POST .../decisions`",
        "basis.api.routes.decisions",
        "A correction's `entity_id` (or a `photo_room` "
        "`after.room_id`) is not in the decided revision of this study.",
        "422 `validation`, `errors[].type` `correction_target`.",
        "Use the ids the output routes return for that revision.",
        "yes",
        problem="validation",
    ),
    _e(
        E.STUDY_CORRECTION_VALUE,
        "refusal",
        "`POST .../decisions`",
        "basis.api.routes.decisions",
        "A `component` `count`, a `room_component`, a "
        "`photo_room`, a `component_remove` or an `extraction_field_add` value the workflow "
        "would refuse (not a whole number from 1, an unknown label, not exactly a `room_id`, "
        "a removal with fields, an unknown field key, a value of the wrong type, a page past "
        "the appraisal PDF, blank evidence).",
        "422 `validation`, `errors[].type` `correction_value` at `after`.",
        "Fix the value.",
        "yes",
        problem="validation",
    ),
    _e(
        E.STUDY_APPROVAL_WITH_CORRECTIONS,
        "refusal",
        "`POST .../decisions`",
        "basis.api.routes.decisions",
        "An approval carried corrections (the workflow would "
        "refuse the decision after the API recorded it).",
        '422 `validation` at `["body", "corrections"]`.',
        "Reject with the corrections instead.",
        "yes",
        problem="validation",
    ),
    _e(
        E.STUDY_NOT_FOUND,
        "refusal",
        "any study route",
        "basis.api.users",
        "No study with that id is visible to the caller.",
        "404 `not-found`.",
        "Check the id and the caller's firm.",
        "no",
        problem="not-found",
    ),
    _e(
        E.STUDY_INTAKE_PRECONDITION_FAILED,
        "refusal",
        "`PUT .../intake`",
        "basis.api.routes.studies",
        "The `If-Match` entity tag is not the intake's current one (someone saved the intake "
        "since the client read it), or `If-Match: *` on a study with no intake.",
        "412 `precondition-failed`; the response's `ETag` is the current tag. Nothing was written.",
        "Read the intake again (`GET .../intake`), reapply the change, and send the new "
        "`ETag` as `If-Match`.",
        "yes",
        problem="precondition-failed",
    ),
    _e(
        E.STUDY_INTAKE_PRECONDITION_REQUIRED,
        "refusal",
        "`PUT .../intake`",
        "basis.api.routes.studies",
        "The deployment requires `If-Match` on intake writes (`INTAKE_REQUIRE_IF_MATCH=true`) "
        "and the request had none.",
        "428 `precondition-required`.",
        "Read the intake (`GET .../intake`) and send its `ETag` as `If-Match`.",
        "yes",
        problem="precondition-required",
    ),
    _e(
        E.STUDY_CORRECTION_REASON,
        "refusal",
        "`POST .../decisions`",
        "basis.api.routes.decisions",
        "A `component_remove` or `extraction_field_add` correction has no reason (or a blank "
        "one): removing or adding a row is engineer evidence and must say why.",
        '422 `validation`, `errors[].type` `correction_reason` at `["body", "corrections", '
        '<i>, "reason"]`.',
        "Give the reason.",
        "yes",
        problem="validation",
    ),
    _e(
        E.STUDY_CORRECTION_DUPLICATE,
        "refusal",
        "`POST .../decisions`",
        "basis.api.routes.decisions",
        "One decision removes the same component twice, removes a component and also corrects "
        "it, or adds the same appraisal field twice.",
        "422 `validation`, `errors[].type` `correction_duplicate` at the later correction.",
        "Send one correction per row (or field).",
        "yes",
        problem="validation",
    ),
    _e(
        E.STUDY_CORRECTION_REBUILT,
        "refusal",
        "`POST .../decisions`",
        "basis.api.routes.decisions",
        "A `component_remove` names a row the rejection rebuilds anyway: an inventory row of a "
        "room with a photo flagged for re-analysis (or a merged room), or a row of a flagged "
        "photo; corrections of rebuilt rows are not carried.",
        "422 `validation`, `errors[].type` `correction_rebuilt` at the removal's `entity_id`.",
        "Drop the removal (or the re-analysis) and remove the row from the next revision.",
        "yes",
        problem="validation",
    ),
    _e(
        E.STUDY_CORRECTION_LAST_INVENTORY,
        "refusal",
        "`POST .../decisions`",
        "basis.api.routes.decisions",
        "The decision removes every inventory row of a reconciled room: the room would fall "
        "back to its per-photo rows and the room step would rebuild the inventory.",
        "422 `validation`, `errors[].type` `correction_last_inventory` at each removal of that "
        "room.",
        "Keep at least one inventory row (correct it), or flag the room's photos for re-analysis.",
        "yes",
        problem="validation",
    ),
    _e(
        E.STUDY_CORRECTION_FIELD_EXISTS,
        "refusal",
        "`POST .../decisions`",
        "basis.api.routes.decisions",
        "An `extraction_field_add` names a key the extraction already has.",
        "422 `validation`, `errors[].type` `correction_field_exists` at `after`.",
        "Correct the existing field with an `extraction_field` correction.",
        "yes",
        problem="validation",
    ),
    # ------------------------------------------------------------------ OBJECT
    _e(
        E.OBJECT_LOCALIZATION_PENDING,
        "refusal",
        "`POST .../objects`",
        "basis.api.routes.objects",
        "The photo's SAM 3 localization is not recorded yet (it runs behind the review).",
        "409 `localization-pending`.",
        "Wait for `GET .../objects` to show a `localization`, then add.",
        "yes",
        problem="localization-pending",
    ),
    _e(
        E.OBJECT_NOTHING_AT_POINT,
        "refusal",
        "`POST .../objects` (a tap)",
        "basis.api.routes.objects",
        "No stored instance of the label is under the point.",
        "422 `nothing-at-point`.",
        "Draw a box around the object instead.",
        "no",
        problem="nothing-at-point",
    ),
    _e(
        E.OBJECT_NOT_MANUAL,
        "refusal",
        "`DELETE .../objects/{id}`",
        "basis.api.routes.objects",
        "The instance is the model's: evidence of what it found, never deleted.",
        "409 `not-manual`.",
        "Only manual instances can be deleted.",
        "no",
        problem="not-manual",
    ),
    _e(
        E.OBJECT_NOT_FOUND,
        "refusal",
        "objects routes",
        "basis.api.routes.objects",
        "No active instance with that id on this photo at the current revision.",
        "404 `not-found`.",
        "Read `GET .../objects` for the current ids.",
        "no",
        problem="not-found",
    ),
    _e(
        E.OBJECT_PHOTO_NOT_FOUND,
        "refusal",
        "objects routes",
        "basis.api.routes.objects",
        "The document is not a photo of the study.",
        "404 `not-found`.",
        "Check the id.",
        "no",
        problem="not-found",
    ),
    _e(
        E.OBJECT_REVISION_MOVED,
        "refusal",
        "`POST .../objects`",
        "basis.api.routes.objects",
        "A decision moved the study to another revision while SAM 3 outlined the box.",
        "409 `stale-state`.",
        "Repeat on the current revision.",
        "yes",
        problem="stale-state",
    ),
    _e(
        E.OBJECT_WRONG_STAGE,
        "refusal",
        "objects writes",
        "basis.api.routes.objects",
        "Objects are edited only while the study waits at its edit stage "
        "(`components_review`, or `rooms_review` without the components gate).",
        "409 `stale-state` naming both stages.",
        "Edit at the components review.",
        "no",
        problem="stale-state",
    ),
    _e(
        E.OBJECT_GATE_NOT_OPEN,
        "refusal",
        "objects writes",
        "basis.api.routes.objects",
        "The study is at the edit stage but the photos are still being prepared for it "
        "(or a stage failed).",
        "409 `gate-not-open`.",
        "Wait for `waiting_review`.",
        "yes",
        problem="gate-not-open",
    ),
    _e(
        E.OBJECT_PROMPT_FAILED,
        "outcome",
        "`POST .../objects` (a box)",
        "basis.api.routes.objects",
        "SAM 3 could not outline the engineer's box (no localizer, file missing, the call "
        "failed or timed out, nothing overlapped). The box was stored without an outline.",
        '201 with `mask_source: "none"` and `mask_reason`; an `object_prompts` row with '
        "`status: failed`.",
        "Nothing required; delete the box and draw it again to try another outline.",
        "yes",
    ),
    # ------------------------------------------------------------------ WF
    _e(
        E.WF_HEARTBEAT_TIMEOUT,
        "error",
        "any heartbeating activity",
        "Temporal",
        "The worker running the activity stopped heartbeating for `heartbeat_timeout` (30 s): "
        "the worker died, was killed, or a long step did not heartbeat (EVAL_REPORT 38: "
        "`ingest_appraisal` embedding on a cold model).",
        "`failure` "
        "`<activity>: activity Heartbeat timeout [BASIS-WF-001]`; study `failed` once the "
        "retries are exhausted; `study_events` `activity.failed`.",
        "Check the worker is alive (Cloud Run logs around the time); retry the stage. If one "
        "step always times out, it needs heartbeats (`basis.activities._support.heartbeat`).",
        "after fix",
        temporal_type="HEARTBEAT",
    ),
    _e(
        E.WF_START_TO_CLOSE_TIMEOUT,
        "error",
        "any activity",
        "Temporal",
        "One attempt ran longer than its `start_to_close_timeout` "
        "(`basis.workflow.activity_options`).",
        "`activity StartToClose timeout [BASIS-WF-002]` in `failure`.",
        "Retry; if a step is always this slow, raise its timeout with a measurement.",
        "automatic",
        temporal_type="START_TO_CLOSE",
    ),
    _e(
        E.WF_SCHEDULE_TO_CLOSE_TIMEOUT,
        "error",
        "any activity",
        "Temporal",
        "The activity's attempts together exceeded `schedule_to_close_timeout`: it kept "
        "failing or waiting.",
        "`activity ScheduleToClose timeout [BASIS-WF-003]`.",
        "Look at the earlier attempts' `activity.failed` events for the real cause; then "
        + RETRY_ROUTE
        + ".",
        "after fix",
        temporal_type="SCHEDULE_TO_CLOSE",
    ),
    _e(
        E.WF_SCHEDULE_TO_START_TIMEOUT,
        "error",
        "any activity",
        "Temporal",
        "No worker picked the activity up in time (not set by this workflow; listed for "
        "completeness).",
        "`activity ScheduleToStart timeout [BASIS-WF-004]`.",
        "Check that a worker polls the task queue.",
        "after fix",
        temporal_type="SCHEDULE_TO_START",
    ),
    _e(
        E.WF_ACTIVITY_CANCELLED,
        "error",
        "any activity",
        "Temporal",
        "The activity was cancelled: the study was cancelled or the worker drained on shutdown.",
        "`[BASIS-WF-005]` on the `activity.cancelled` event.",
        "Nothing for a cancelled study; a drained activity is re-run by the next worker.",
        "automatic",
    ),
    _e(
        E.WF_ACTIVITY_FAILED,
        "error",
        "any activity",
        "basis.workflow.study_workflow",
        "An activity failed with an error type that carries no code of its own "
        "(a test fake, a type from before the catalogue).",
        "`<activity>: <type>: <message> [BASIS-WF-006]`.",
        "Read the message; the type says which class it is.",
        "after fix",
    ),
    _e(
        E.WF_UNEXPECTED_ERROR,
        "error",
        "any activity",
        "basis.activities._support",
        "An exception that is not an `ApplicationError` escaped an activity (a database "
        "error on an established connection, a bug).",
        "`activity_results.error` "
        "`<ExceptionType>: <message> [BASIS-WF-007]`; Temporal retries it up to the "
        "activity's attempts, then the stage fails.",
        "Read the worker log for the stack trace; fix; " + RETRY_ROUTE + ".",
        "automatic",
    ),
    _e(
        E.WF_INVALID_DECISION,
        "error",
        "the `decide` update",
        "basis.workflow.study_workflow",
        "The workflow refused a decision: not waiting, wrong stage, stale revision, already "
        "decided, unknown decision, a correction type the gate does not take, a bad "
        "`entity_id`, an approval with corrections. The API checks the same rules first, so "
        "this reaches only the outbox relay (which logs it as final).",
        "Relay log `outbox message refused by its consumer` with the reason; the review row "
        "stays recorded but is never applied.",
        "Decide again on the current stage and revision.",
        "no",
        temporal_type="InvalidDecision",
    ),
    _e(
        E.WF_INVALID_RETRY,
        "error",
        "the `retry` update",
        "basis.workflow.study_workflow",
        "No stage has failed, or the study was cancelled.",
        "409 `study-not-failed` (" + E.STUDY_NOT_FAILED + ").",
        "Nothing to retry.",
        "no",
        temporal_type="InvalidRetry",
    ),
    _e(
        E.WF_STUDY_CANCELLED,
        "outcome",
        "the study",
        "basis.workflow.study_workflow",
        "The owner cancelled the study (`POST .../cancel`): every activity stopped, the run "
        "ended `cancelled`.",
        "`runs.outcome = cancelled`; `study.cancel_requested` and `run.ended` events.",
        "Start a new study.",
        "no",
    ),
    _e(
        E.WF_REVIEW_STALLED,
        "outcome",
        "a review gate",
        "basis.workflow.study_workflow",
        "No decision for `review_deadline_days`; the study is `stalled` and the reminder "
        "keeps going.",
        "`status: stalled`; `study.stalled` event.",
        "Decide; the study resumes.",
        "yes",
    ),
    _e(
        E.WF_NO_RUN_ROW,
        "error",
        "any activity",
        "basis.activities._support",
        "The `runs` row of this execution does not exist: `record_run_start` did not run first.",
        "`ValidationError: no runs row ... [BASIS-WF-012]`.",
        "A workflow ordering defect; report it.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.WF_NOT_IN_WORKFLOW,
        "error",
        "any activity",
        "basis.activities._support",
        "An activity was invoked outside a workflow (no workflow id on the activity).",
        "`ValidationError: ... [BASIS-WF-013]`.",
        "Run the activities through the StudyWorkflow.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.WF_BAD_UUID,
        "error",
        "any activity",
        "basis.activities._support",
        "An input id is missing or not a UUID.",
        "`ValidationError: <what> ... is not a UUID [BASIS-WF-014]`.",
        "A contract defect between the workflow and the activity; report it.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.WF_STUDY_GONE,
        "error",
        "any activity",
        "basis.activities._support",
        "The study (or a row it needs) is gone from the database while the workflow runs.",
        "`ValidationError: <repository message> [BASIS-WF-015]`.",
        "Operator: the study was deleted outside the API; the execution cannot continue.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.WF_STALE_TRANSITION,
        "error",
        "`transition`",
        "basis.db.repositories.workflow_state",
        "The study row is not at the stage the workflow expects and not at the target either "
        "(moved by hand, or archived).",
        "`ValidationError: study ... is at ...; expected ... [BASIS-WF-016]`.",
        "Operator: put the row back at the expected stage, then "
        + RETRY_ROUTE
        + "; never edit study rows by hand while a workflow runs.",
        "after fix",
        temporal_type="ValidationError",
    ),
    _e(
        E.WF_SIMULATED_FAILURE,
        "error",
        "`analyze_photo` (dev and test only)",
        "basis.activities.analyze_photo",
        "A `SimulateFault` asked for the failure.",
        "`SimulatedFailure: simulated ... [BASIS-WF-017]`.",
        "Test only.",
        "no",
        temporal_type="SimulatedFailure",
    ),
    # ------------------------------------------------------------------ PROVIDER
    _e(
        E.PROVIDER_ACCOUNT,
        "error",
        "analyze_photo, extract_fields, reconcile_rooms, process_assets",
        "basis.llm.errors (`ProviderAccountError`)",
        "The provider refused the account: out of credit or quota, a missing, invalid or "
        "revoked key, no permission (OpenAI `insufficient_quota`, Anthropic "
        "`billing_error`, Azure 401/403). Every later call fails the same way, so the stage "
        "stops at once and nothing is retried.",
        "Study `failed` at its stage with "
        "`<activity>: ProviderAccountError: <provider> account error "
        "(HTTP <n>, <code>): <provider's message>; fix the account, then retry the study [BASIS-PROVIDER-001]`.",
        "Top up or fix the account or key; then " + RETRY_ROUTE + " (the photos the error "
        "stopped run again, the rest is kept).",
        "after fix",
        temporal_type="ProviderAccountError",
    ),
    _e(
        E.PROVIDER_CONFIG,
        "error",
        "analyze_photo, extract_fields, reconcile_rooms, process_assets",
        "basis.llm.errors (`ProviderConfigError`)",
        "The provider refused the request as configured: unknown model or deployment, a "
        "parameter or value the model does not take, a schema it rejects. The same template "
        "fails for every input, so it stops the stage like an account error.",
        "`ProviderAccountError: <provider> rejected the request (HTTP "
        "<n>, <code>): ...; check the model and deployment settings, then retry the study [BASIS-PROVIDER-002]`.",
        "Fix the model or deployment setting named; " + RETRY_ROUTE + ".",
        "after fix",
        temporal_type="ProviderAccountError",
    ),
    _e(
        E.PROVIDER_NOT_CONFIGURED_VISION,
        "error",
        "analyze_photo",
        "basis.activities.analyze_photo",
        "No vision provider is configured at all "
        "(dev and test only: the worker refuses to start that way elsewhere).",
        "`ProviderNotConfigured: ... [BASIS-PROVIDER-003]`; the photo is flagged.",
        "Set `OPENAI_API_KEY` or the Azure OpenAI variables.",
        "no",
        temporal_type="ProviderNotConfigured",
    ),
    _e(
        E.PROVIDER_NOT_CONFIGURED_ROOMS,
        "error",
        "reconcile_rooms",
        "basis.activities.reconcile_rooms",
        "No model provider for the room step.",
        "`ProviderNotConfigured: ... [BASIS-PROVIDER-004]`; the rooms review is flagged and "
        "the per-photo rows stand.",
        "Configure a provider.",
        "no",
        temporal_type="ProviderNotConfigured",
    ),
    _e(
        E.PROVIDER_REQUEST_REJECTED,
        "error",
        "analyze_photo, extract_fields, reconcile_rooms",
        "basis.llm.errors (`ProviderRequestRejected`)",
        "The provider refused this request's "
        "content: an image it cannot process, a request too large, a content-policy block. "
        "Another input may work, so only this item fails.",
        "`ValidationError: "
        "<what>: <provider> rejected the input (...) [BASIS-PROVIDER-005]`; a photo is "
        "flagged, an extraction stops the stage.",
        "Replace the file (a photo: re-encode or "
        "crop it; the appraisal: a clean PDF); for an extraction " + RETRY_ROUTE + ".",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.PROVIDER_BAD_RESPONSE,
        "error",
        "analyze_photo, extract_fields, reconcile_rooms",
        "basis.llm.errors (`ProviderBadResponse`)",
        "The call completed but its output cannot "
        "be used (invalid JSON for the schema after the retry, a refusal, truncation; Azure "
        "DI 4xx). Charged, not retried elsewhere.",
        "`ValidationError: <what>: <provider> rejected the input: ... [BASIS-PROVIDER-006]`.",
        "Retry the stage; if it repeats, the prompt or the model setting needs a change.",
        "after fix",
        temporal_type="ValidationError",
    ),
    _e(
        E.PROVIDER_UNAVAILABLE,
        "error",
        "analyze_photo, extract_fields, reconcile_rooms, process_assets",
        "basis.llm.errors (`ProviderRateLimited`, `ProviderUnavailable`)",
        "429, 5xx, 408/409, a timeout or a connection failure after the provider's own retry "
        "and the pool's secondary provider.",
        "`ProviderUnavailable: "
        "... [BASIS-PROVIDER-007]`; Temporal retries the activity (3 attempts), then the stage fails.",
        "Usually transient. If the stage failed, " + RETRY_ROUTE + " once the provider is healthy.",
        "automatic",
        temporal_type="ProviderUnavailable",
    ),
    _e(
        E.PROVIDER_LOCALIZER_UNAVAILABLE,
        "error",
        "localize_photo",
        "basis.vision.localize (`LocalizerUnavailable`)",
        "SAM 3 (Replicate or the Cog "
        "server) answered 429/5xx or timed out after the localizer's own backoff.",
        "`LocalizerUnavailable: ... [BASIS-PROVIDER-008]` while attempts remain; on the last "
        "attempt the photo's localization is recorded `failed` and nothing else fails.",
        "Nothing: the engineer can still draw boxes; a failed localization never fails the study.",
        "automatic",
        temporal_type="LocalizerUnavailable",
    ),
    _e(
        E.PROVIDER_LOCALIZER_FAILED,
        "outcome",
        "localize_photo",
        "basis.activities.localize_photo",
        "The localizer returned an error a retry cannot fix (a rejected request, a failed "
        "prediction, an unreadable result).",
        "`photo_localizations.status = failed` with `reason` `... [BASIS-PROVIDER-009]`.",
        "Check the localizer deployment "
        "(docs/review_2026-10-15/SAM3_HOSTING.md); boxes can still be drawn by hand.",
        "no",
    ),
    _e(
        E.PROVIDER_NO_LOCALIZER,
        "outcome",
        "localize_photo",
        "basis.activities.localize_photo",
        "Neither `LOCALIZER_URL` nor `REPLICATE_API_TOKEN` is set.",
        "`photo_localizations.status = skipped`, `reason` `no localizer "
        "configured ... [BASIS-PROVIDER-010]`.",
        "Configure a localizer for outlines and counts.",
        "no",
    ),
    # ------------------------------------------------------------------ BUDGET
    _e(
        E.BUDGET_PHOTO,
        "error",
        "analyze_photo",
        "basis.activities.analyze_photo",
        "The study's remaining budget cannot cover the photo's model call(s).",
        "`BudgetExceededError: photo ... [BASIS-BUDGET-001]`; the photo is flagged and the "
        "components review marked `needs_review`.",
        "Raise `STUDY_BUDGET_USD` (or the photo "
        "step's cost) for later studies; this photo stays flagged.",
        "no",
        temporal_type="BudgetExceededError",
    ),
    _e(
        E.BUDGET_ROOMS,
        "error",
        "reconcile_rooms",
        "basis.activities.reconcile_rooms",
        "The room step's calls would exceed the remaining budget.",
        "`BudgetExceededError: "
        "room step ... [BASIS-BUDGET-002]`; the rooms review is flagged, the per-photo rows "
        "stand.",
        "Raise the budget; the review shows per-photo rows.",
        "no",
        temporal_type="BudgetExceededError",
    ),
    _e(
        E.BUDGET_CLASSIFY,
        "error",
        "process_assets",
        "basis.activities.process_assets",
        "The worst case of the residual classifier's calls does not fit the remaining budget "
        "(checked before any call).",
        "`BudgetExceededError: N components "
        "need the model (up to $x); $y remains [BASIS-BUDGET-003]`; the assets review is flagged and no asset "
        "is written until the engineer decides.",
        "Raise the budget and retry, or approve the review as is.",
        "no",
        temporal_type="BudgetExceededError",
    ),
    _e(
        E.BUDGET_LOCALIZE_SKIPPED,
        "outcome",
        "localize_photo",
        "basis.activities.localize_photo",
        "No budget left when the photo reached SAM 3.",
        "`photo_localizations.status = "
        "skipped`, `reason` `the study budget is exhausted [BASIS-BUDGET-004]`.",
        "Raise the budget for later studies.",
        "no",
    ),
    _e(
        E.BUDGET_PHOTO_SKIPPED,
        "outcome",
        "analyze_photo (scheduling)",
        "basis.workflow.study_workflow",
        "The budget reached zero before the photo's analysis was scheduled; it was not analyzed.",
        "The photo is in `flagged_photos` and the components review is `needs_review`.",
        "Raise the budget; the photo has no rows.",
        "no",
    ),
    # ------------------------------------------------------------------ INGEST
    _e(
        E.INGEST_STUDY_NOT_FOUND,
        "error",
        "ingest_appraisal",
        "basis.activities.ingest_appraisal",
        "The study row is gone.",
        "`ValidationError: study ... not found [BASIS-INGEST-001]`.",
        "Operator: the study was deleted outside the API.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.INGEST_FILE_MISSING,
        "error",
        "ingest_appraisal",
        "basis.activities.ingest_appraisal",
        "The appraisal row says uploaded but the object is gone from storage.",
        "`ValidationError: appraisal ... is missing from storage [BASIS-INGEST-002]`.",
        "Re-upload the appraisal as a new document; " + RETRY_ROUTE + ".",
        "after fix",
        temporal_type="ValidationError",
    ),
    _e(
        E.INGEST_INVALID_PACKAGE,
        "error",
        "ingest_appraisal",
        "basis.extraction.package",
        "The UAD 3.6 ZIP or XML cannot be read (size limits, not a safe ZIP, no XML, bad XML).",
        "`ValidationError: appraisal ...: <reason> [BASIS-INGEST-003]`.",
        "Upload a valid delivery (docs/api/UAD36_INTAKE.md); " + RETRY_ROUTE + ".",
        "after fix",
        temporal_type="ValidationError",
    ),
    _e(
        E.INGEST_COMPANION_NOT_PDF,
        "error",
        "ingest_appraisal",
        "basis.activities.ingest_appraisal",
        "The companion document chosen as a bare XML's report PDF is not a PDF.",
        "`ValidationError: ... [BASIS-INGEST-004]`.",
        "Upload the real report PDF; " + RETRY_ROUTE + ".",
        "after fix",
        temporal_type="ValidationError",
    ),
    _e(
        E.INGEST_PDF_UNREADABLE,
        "error",
        "ingest_appraisal",
        "basis.retrieval.parse",
        "The report PDF cannot be parsed (corrupt, encrypted, not a PDF).",
        "`ValidationError: appraisal ...: <PdfError> [BASIS-INGEST-005]`.",
        "Export the appraisal to a clean PDF and upload it; " + RETRY_ROUTE + ".",
        "after fix",
        temporal_type="ValidationError",
    ),
    _e(
        E.INGEST_CORPUS_DISCARD_FAILED,
        "error",
        "ingest_appraisal",
        "basis.retrieval.store",
        "An unpublished study corpus version from an earlier attempt could not be discarded.",
        "`ValidationError: ... [BASIS-INGEST-006]`.",
        "Operator: inspect `corpus_versions` for the study; " + RETRY_ROUTE + ".",
        "after fix",
        temporal_type="ValidationError",
    ),
    # ------------------------------------------------------------------ EXTRACT
    _e(
        E.EXTRACT_APPRAISAL_NOT_FOUND,
        "error",
        "extract_fields",
        "basis.activities.extract_fields",
        "The document the workflow named is not an appraisal of the study.",
        "`ValidationError: ... [BASIS-EXTRACT-001]`.",
        "Operator: a workflow contract defect; report it.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.EXTRACT_FILE_MISSING,
        "error",
        "extract_fields",
        "basis.activities.extract_fields",
        "The appraisal's object is gone from storage.",
        "`ValidationError: appraisal file ... is missing from storage [BASIS-EXTRACT-002]`.",
        "Re-upload; " + RETRY_ROUTE + ".",
        "after fix",
        temporal_type="ValidationError",
    ),
    _e(
        E.EXTRACT_REPORT_PDF_MISSING,
        "error",
        "extract_fields",
        "basis.activities.extract_fields",
        "A bare XML's report PDF is gone from storage.",
        "`ValidationError: ... [BASIS-EXTRACT-003]`.",
        "Re-upload the report PDF; " + RETRY_ROUTE + ".",
        "after fix",
        temporal_type="ValidationError",
    ),
    _e(
        E.EXTRACT_REPORT_PDF_NOT_PDF,
        "error",
        "extract_fields",
        "basis.activities.extract_fields",
        "The report PDF of a bare XML is not a PDF.",
        "`ValidationError: ... [BASIS-EXTRACT-004]`.",
        "Upload the real report PDF; " + RETRY_ROUTE + ".",
        "after fix",
        temporal_type="ValidationError",
    ),
    _e(
        E.EXTRACT_REPORT_PDF_NOT_CONFIRMED,
        "error",
        "extract_fields",
        "basis.activities.extract_fields",
        "The report PDF `ingest_appraisal` chose is not a "
        "confirmed appraisal of the study any more.",
        "`ValidationError: ... [BASIS-EXTRACT-005]`.",
        "Operator: check the document rows; " + RETRY_ROUTE + ".",
        "after fix",
        temporal_type="ValidationError",
    ),
    _e(
        E.EXTRACT_INVALID_DOCUMENT,
        "error",
        "extract_fields",
        "basis.extraction.pipeline",
        "The appraisal cannot be read as a document (not a PDF/XML/ZIP, empty, corrupt).",
        "`ValidationError: appraisal ...: <reason> [BASIS-EXTRACT-006]`.",
        "Upload a readable appraisal; " + RETRY_ROUTE + ".",
        "after fix",
        temporal_type="ValidationError",
    ),
    _e(
        E.EXTRACT_CORRECTION_TYPE,
        "error",
        "extract_fields",
        "basis.activities.extract_fields",
        "A correction of another type than `extraction_field` reached the extraction.",
        "`ValidationError: ... [BASIS-EXTRACT-007]`.",
        "A workflow defect; the API refuses such a decision first. Report it.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.EXTRACT_FIELD_NOT_FOUND,
        "error",
        "extract_fields",
        "basis.activities.extract_fields",
        "A corrected field id does not exist in the study.",
        "`ValidationError: extraction field ... not found [BASIS-EXTRACT-008]`.",
        "Decide again with the ids `GET .../fields` returns.",
        "no",
        temporal_type="ValidationError",
    ),
    # ------------------------------------------------------------------ PHOTO
    _e(
        E.PHOTO_NOT_FOUND,
        "error",
        "analyze_photo",
        "basis.activities.analyze_photo",
        "The document is not a photo of the study.",
        "`ValidationError: ... [BASIS-PHOTO-001]`; the photo is flagged.",
        "Operator: a contract defect; report it.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.PHOTO_FILE_MISSING,
        "error",
        "analyze_photo",
        "basis.activities.analyze_photo",
        "The photo's object is gone from storage.",
        "`ValidationError: photo "
        "file ... is missing from storage [BASIS-PHOTO-002]`; the photo is flagged.",
        "Re-upload the photo (a new document) and flag the old one as re-analyze, or ignore it.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.PHOTO_IMAGE_REJECTED,
        "error",
        "analyze_photo",
        "basis.vision.prep",
        "The image cannot be analyzed: empty, not an image, too small, too many pixels.",
        "`ValidationError: photo ... cannot be analyzed: <reason> [BASIS-PHOTO-003]`; the "
        "photo is flagged.",
        "Replace the photo with a readable JPEG or PNG.",
        "no",
        temporal_type="ValidationError",
    ),
    # ------------------------------------------------------------------ LOCALIZE
    _e(
        E.LOCALIZE_PHOTO_NOT_FOUND,
        "error",
        "localize_photo",
        "basis.activities.localize_photo",
        "The document is not a photo of the study.",
        "`ValidationError: ... [BASIS-LOCALIZE-001]`; logged, the photo is not localized.",
        "Operator: a contract defect.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.LOCALIZE_FILE_MISSING,
        "outcome",
        "localize_photo",
        "basis.activities.localize_photo",
        "The photo's object is gone from storage.",
        "`photo_localizations.status = failed`, `reason` `... [BASIS-LOCALIZE-002]`.",
        "Re-upload the photo.",
        "no",
    ),
    _e(
        E.LOCALIZE_IMAGE_REJECTED,
        "outcome",
        "localize_photo",
        "basis.activities.localize_photo",
        "The photo cannot be prepared for SAM 3.",
        "`status = failed`, `reason` `the photo cannot be prepared: ... [BASIS-LOCALIZE-003]`.",
        "Replace the photo.",
        "no",
    ),
    # ------------------------------------------------------------------ ROOMS
    _e(
        E.ROOMS_FILE_MISSING,
        "error",
        "reconcile_rooms",
        "basis.activities.reconcile_rooms",
        "A photo of the room is gone from storage.",
        "`ValidationError: photo "
        "file ... is missing [BASIS-ROOMS-001]`; the rooms/components review is flagged, per-photo rows stand.",
        "Re-upload the photo.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.ROOMS_IMAGE_REJECTED,
        "error",
        "reconcile_rooms",
        "basis.activities.reconcile_rooms",
        "A photo of the room cannot be prepared (it was analyzed before, so this is rare).",
        "`ValidationError: ... [BASIS-ROOMS-002]`; the review is flagged.",
        "Replace the photo.",
        "no",
        temporal_type="ValidationError",
    ),
    # ------------------------------------------------------------------ CORRECT
    _e(
        E.CORRECT_UNKNOWN_FIELDS,
        "error",
        "apply_corrections, extract_fields, process_assets",
        "basis.activities.corrections",
        "A correction names a field the entity type does not "
        "have (room: name, room_type, floor, merge_into; component: label, material, "
        "attachment, count; asset: category, bucket, recovery_period, irs_section; "
        "extraction_field: value_text, value_num, value_date).",
        "`ValidationError: "
        "unknown <kind> fields: ... [BASIS-CORRECT-001]`; the revision starts without any of "
        "the rejection's corrections and the gate asks again (`needs_review_stages`).",
        "Reject again with the field names listed.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_NOT_A_NUMBER,
        "error",
        "corrections",
        "basis.activities.corrections",
        "A `value_num` or `recovery_period` is not a finite number.",
        "`ValidationError: ... [BASIS-CORRECT-002]`.",
        "Reject again with a number.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_NOT_A_DATE,
        "error",
        "corrections",
        "basis.activities.corrections",
        "A `value_date` is not an ISO date.",
        "`ValidationError: ... [BASIS-CORRECT-003]`.",
        "Use `YYYY-MM-DD`.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_UNKNOWN_BUCKET,
        "error",
        "corrections",
        "basis.activities.corrections",
        "An asset `bucket` is not y5, y7, y15, y27_5, y39 or land.",
        "`ValidationError: unknown bucket ... [BASIS-CORRECT-004]`.",
        "Use a known bucket.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_RECOVERY_PERIOD,
        "error",
        "corrections",
        "basis.activities.corrections",
        "A `recovery_period` is not a positive number of years below 1000.",
        "`ValidationError: ... [BASIS-CORRECT-005]`.",
        "Use the bucket's years (5, 7, 15, 27.5, 39).",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_COUNT,
        "error",
        "corrections",
        "basis.domain.counts",
        "A component `count` is not a whole number from 1.",
        "`ValidationError: ... [BASIS-CORRECT-006]`.",
        "Use a whole number from 1.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_PHOTO_ROOM_SHAPE,
        "error",
        "corrections",
        "basis.activities.corrections",
        'A `photo_room` correction is not exactly `{"room_id": <uuid>}`.',
        "`ValidationError: ... [BASIS-CORRECT-007]`.",
        "Send only `room_id`.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_ROOM_COMPONENT,
        "error",
        "corrections",
        "basis.domain.counts",
        "A `room_component` is not a taxonomy label with a count (and known material and "
        "attachment).",
        "`ValidationError: <problems> [BASIS-CORRECT-008]`.",
        "Use a label from the component taxonomy with `count`.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_IRS_SECTION,
        "error",
        "corrections",
        "basis.activities.corrections",
        "An `irs_section` is not 1245 or 1250.",
        "`ValidationError: ... [BASIS-CORRECT-009]`.",
        "Use 1245 or 1250.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_BUCKET_PERIOD_MISMATCH,
        "error",
        "corrections",
        "basis.domain.validators",
        "A bucket and a recovery period given together disagree (y5 is 5 years).",
        "`ValidationError: bucket ... recovers over ... years, not ... [BASIS-CORRECT-010]`.",
        "Give the bucket's years or leave the period out.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_SECTION_BUCKET_MISMATCH,
        "error",
        "corrections",
        "basis.domain.validators",
        "A section given with a bucket does not allow it (1245: 5/7/15; 1250: 15/27.5/39).",
        "`ValidationError: section ... cannot have bucket ... [BASIS-CORRECT-011]`.",
        "Fix the section or the bucket.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_REVISION_STEP,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "`to_revision` is not `from_revision + 1`.",
        "`ValidationError: ... [BASIS-CORRECT-012]`.",
        "A workflow defect; report it.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_STAGE_TYPE,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "The rejected stage does not accept a correction type in the batch.",
        "`ValidationError: <stage> does not accept ... corrections [BASIS-CORRECT-013]`.",
        "Reject with the types the stage accepts (the API lists them).",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_FIELD_NOT_IN_REVISION,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "An `extraction_field` target is not in the rejected revision.",
        "`ValidationError: ... [BASIS-CORRECT-014]`.",
        "Use the ids of the decided revision.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_ASSET_NOT_IN_REVISION,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "An `asset` target is not in the rejected revision.",
        "`ValidationError: ... [BASIS-CORRECT-015]`.",
        "Use the ids of the decided revision.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_NOT_A_PHOTO,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "A `photo` or `photo_room` target is not a photo document of the study.",
        "`ValidationError: ... [BASIS-CORRECT-016]`.",
        "Use a photo document id.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_PHOTO_HAS_NO_ROOM,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "A moved photo has no room in the rejected revision (its analysis failed).",
        "`ValidationError: ... [BASIS-CORRECT-017]`.",
        "Flag the photo for re-analysis instead of moving it.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_MOVE_TARGET_UNKNOWN,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "A `photo_room` `room_id` is not a room of the rejected revision.",
        "`ValidationError: ... [BASIS-CORRECT-018]`.",
        "Use a room id of that revision.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_ROOM_NOT_IN_REVISION,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "A `room_component` names a room that is not in the rejected revision.",
        "`ValidationError: ... [BASIS-CORRECT-019]`.",
        "Use a room id of that revision.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_MERGE_WITH_OTHER_FIELDS,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "A `merge_into` correction also carries other fields.",
        "`ValidationError: ... [BASIS-CORRECT-020]`.",
        "Send `merge_into` alone.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_MERGE_TARGET_UNKNOWN,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "The `merge_into` target is not a room of the rejected revision.",
        "`ValidationError: ... [BASIS-CORRECT-021]`.",
        "Use a room id of that revision.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_MERGE_INTO_SELF,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "A room is merged into itself.",
        "`ValidationError: ... [BASIS-CORRECT-022]`.",
        "Name another room.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_MERGE_LOOP,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "The merges form a loop (a into b, b into a).",
        "`ValidationError: room merges loop: ... [BASIS-CORRECT-023]`.",
        "Merge every room into one target.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_STUDY_NOT_FOUND,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "The study is gone while merges are recorded.",
        "`ValidationError: ... [BASIS-CORRECT-024]`.",
        "Operator: the study was deleted.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_TARGET_NOT_IN_REVISION,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "A `room` or `component` target is not in the rejected revision.",
        "`ValidationError: <kind> ... is not in the rejected revision [BASIS-CORRECT-025]`.",
        "Use the ids of the decided revision.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_REFUSED,
        "outcome",
        "a rejection",
        "basis.workflow.study_workflow",
        "`apply_corrections` refused the batch (one of the CORRECT errors above): the "
        "revision started without any correction and the gate asks again.",
        "`needs_review_stages` names the gate; the `corrections.refused` event carries the "
        "code of the refusal.",
        "Decide again with the corrections fixed.",
        "yes",
    ),
    _e(
        E.CORRECT_ROOM_FIELD,
        "error",
        "corrections",
        "basis.domain.rooms",
        "A `room` correction's `name` or `room_type` is not non-empty text, or its `floor` "
        "is neither text, a whole number nor null.",
        "`ValidationError: <problems> [BASIS-CORRECT-027]`.",
        'Send text (`"Guest Bedroom"`, `"bedroom"`), a floor as `"2"`, `2` or null.',
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_REMOVE_SHAPE,
        "error",
        "corrections",
        "basis.activities.corrections",
        "A `component_remove` correction carries fields (its `after` must be `{}`).",
        "`ValidationError: ... [BASIS-CORRECT-028]`.",
        "Send `after: {}`.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_REASON_REQUIRED,
        "error",
        "corrections",
        "basis.activities.corrections",
        "A `component_remove` or `extraction_field_add` correction has no reason.",
        "`ValidationError: ... [BASIS-CORRECT-029]`.",
        "Give the reason.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_REMOVE_CONFLICT,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "A component is removed twice, or removed and corrected, in one rejection.",
        "`ValidationError: ... [BASIS-CORRECT-030]`.",
        "Send one correction per row.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_REMOVE_REBUILT,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "A removed row is one the rejection rebuilds anyway (an inventory row of a room with a "
        "flagged photo or a merge, a row of a flagged photo).",
        "`ValidationError: ... [BASIS-CORRECT-031]`.",
        "Remove the row from the next revision instead.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_REMOVE_LAST_INVENTORY,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "The rejection removes every inventory row of a reconciled room.",
        "`ValidationError: ... [BASIS-CORRECT-032]`.",
        "Keep one inventory row, or flag the room's photos for re-analysis.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_FIELD_ADD_SHAPE,
        "error",
        "corrections",
        "basis.extraction.added_fields",
        "An `extraction_field_add` `after` is not a key, exactly one value, a page from 1 and "
        "non-blank evidence of at most 1000 characters (and nothing else).",
        "`ValidationError: ... [BASIS-CORRECT-033]`.",
        "Fix the correction's `after`.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_FIELD_ADD_UNKNOWN_KEY,
        "error",
        "corrections",
        "basis.extraction.added_fields",
        "An `extraction_field_add` key is not in the URAR field catalogue.",
        "`ValidationError: ... [BASIS-CORRECT-034]`.",
        "Use a `<section>.<field>` key of the catalogue (`basis.extraction.field_mappings`).",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_FIELD_ADD_TYPE,
        "error",
        "corrections",
        "basis.extraction.added_fields",
        "An added value does not match the field's catalogue type (a date in `value_date`, a "
        "number in `value_num`, a whole number for an integer field, Yes/No for a check box).",
        "`ValidationError: ... [BASIS-CORRECT-035]`.",
        "Send the value in its type's member.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_FIELD_ADD_EXISTS,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "An `extraction_field_add` names a key the extraction already has.",
        "`ValidationError: ... [BASIS-CORRECT-036]`.",
        "Correct the existing field (`extraction_field`).",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_FIELD_ADD_PAGE,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "An added field's page is past the appraisal PDF's page count.",
        "`ValidationError: ... [BASIS-CORRECT-037]`.",
        "Give the page the value is printed on.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_FIELD_ADD_DUPLICATE,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "The same field is added twice to one extraction in one rejection.",
        "`ValidationError: ... [BASIS-CORRECT-038]`.",
        "Add each field once.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_EXTRACTION_NOT_IN_REVISION,
        "error",
        "apply_corrections",
        "basis.activities.apply_corrections",
        "An `extraction_field_add` target is not an extraction of the rejected revision.",
        "`ValidationError: ... [BASIS-CORRECT-039]`.",
        "Use the extraction id of the decided revision.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_REPRICE_SHAPE,
        "error",
        "apply_corrections, process_assets",
        "basis.activities.reprice_corrections",
        'A `reprice` correction is not exactly `{"fingerprint": <64 hex>}`.',
        "`ValidationError: ... [BASIS-CORRECT-040]`.",
        "Request the rebuild with `POST /studies/{id}/assets/reprice`, which writes it.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_STRUCTURE_MODEL,
        "error",
        "apply_corrections, process_assets",
        "basis.activities.reprice_corrections",
        "A `structure_model` correction has an unknown member, no member, a class or type "
        "outside the RSMeans vocabularies, or a living area outside (0, 100,000] SF.",
        "`ValidationError: ... [BASIS-CORRECT-041]`.",
        "Send `model_class` (economy, average, custom, luxury), `building_type` (1 story ... "
        "3 story) and/or `living_area_sf`.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.CORRECT_STUDY_TARGET,
        "error",
        "apply_corrections, process_assets",
        "basis.activities.reprice_corrections",
        "A study-level correction (`reprice`, `structure_model`) names another entity than "
        "its own study.",
        "`ValidationError: ... [BASIS-CORRECT-042]`.",
        "Its `entity_id` is the study id.",
        "no",
        temporal_type="ValidationError",
    ),
    # ------------------------------------------------------------------ ASSETS
    _e(
        E.ASSETS_CORRECTION_TYPE,
        "error",
        "process_assets",
        "basis.activities.process_assets",
        "A correction of another type than `asset` reached the assets step.",
        "`ValidationError: ... [BASIS-ASSETS-001]`.",
        "A workflow defect; report it.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.ASSETS_ASSET_NOT_FOUND,
        "error",
        "process_assets",
        "basis.activities.process_assets",
        "A corrected asset id is not in the study (or has no component).",
        "`ValidationError: ... [BASIS-ASSETS-002]`.",
        "Decide again with the ids `GET .../assets` returns.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.ASSETS_NO_COMPONENT,
        "error",
        "process_assets",
        "basis.activities.process_assets",
        "The corrected asset's component was not carried into this revision.",
        "`ValidationError: ... [BASIS-ASSETS-003]`.",
        "A workflow defect; report it.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.ASSETS_BUCKET_NEEDS_PERIOD,
        "error",
        "process_assets",
        "basis.activities.process_assets",
        "An asset correction changes the bucket to one with no standard recovery period "
        "(land) without giving `recovery_period`.",
        "`ValidationError: ... [BASIS-ASSETS-004]`.",
        "Give `recovery_period` with the bucket.",
        "no",
        temporal_type="ValidationError",
    ),
    _e(
        E.ASSETS_RULE_CONTESTED,
        "flag",
        "process_assets",
        "basis.classify.router",
        "The rules-table row that classified the component is marked contested (the "
        "authorities disagree).",
        "`review_reasons` `rule_contested:<key>`.",
        "The engineer confirms or corrects the bucket at the assets review.",
        "no",
        signal="rule_contested:",
    ),
    _e(
        E.ASSETS_CLASSIFIER_UNAVAILABLE,
        "flag",
        "process_assets",
        "basis.classify.router",
        "No chat provider is wired for the residual classifier; the component got a "
        "fallback classification.",
        "`review_reasons` `classifier_unavailable`.",
        "Configure the classifier provider; the engineer classifies by hand meanwhile.",
        "no",
        signal="classifier_unavailable",
    ),
    _e(
        E.ASSETS_CITATION_UNVERIFIED,
        "flag",
        "process_assets",
        "basis.classify.router",
        "A citation could not be verified against the reference text, or the model path "
        "produced none.",
        "`review_reasons` `citation_unverified`.",
        "The engineer checks the authority by hand.",
        "no",
        signal="citation_unverified",
    ),
    _e(
        E.ASSETS_MISSING_EVIDENCE,
        "flag",
        "process_assets",
        "basis.classify.router",
        "The model answered without any search result to cite.",
        "`review_reasons` `missing_evidence`.",
        "The engineer supplies the authority.",
        "no",
        signal="missing_evidence",
    ),
    _e(
        E.ASSETS_MODEL_REQUESTED_REVIEW,
        "flag",
        "process_assets",
        "basis.classify.router",
        "The model itself asked for review and said why.",
        "`review_reasons` `model:<reason>`.",
        "Read the reason.",
        "no",
        signal="model:",
    ),
    _e(
        E.ASSETS_TAKEOFF_UPPER_BOUND,
        "flag",
        "process_assets",
        "basis.activities.process_assets",
        "The quantity is an upper bound (landscaping: the whole unbuilt lot).",
        "`review_reasons` `takeoff:upper_bound`.",
        "Measure or confirm the quantity.",
        "no",
        signal="takeoff:upper_bound",
    ),
    _e(
        E.ASSETS_BUDGET_FLAGGED,
        "outcome",
        "process_assets",
        "basis.workflow.study_workflow",
        "The assets step did not run for want of budget (" + E.BUDGET_CLASSIFY + ").",
        "`needs_review_stages` has `assets_review` and the revision has no assets.",
        "Raise the budget and retry, or approve as is.",
        "no",
    ),
    _e(
        E.ASSETS_CONVENTION_PROMOTION_FAILED,
        "outcome",
        "promote_conventions",
        "basis.workflow.study_workflow",
        "The approved assets could not be promoted to the firm's conventions; the study "
        "continued to the export (its result does not depend on the conventions).",
        "A worker-log warning with this code and the `promote_conventions` "
        "`activity.failed` events; no `convention.promoted` events for the revision.",
        "Fix the cause (the activity's error code). This study's rulings stay unpromoted; "
        "a later approved study with the same components seeds them.",
        "no",
    ),
    _e(
        E.ASSETS_CONVENTION_REPLACED,
        "outcome",
        "promote_conventions",
        "basis.activities.promote_conventions",
        "An approved answer for a context key differed from the firm's convention for it, "
        "so the convention was replaced (approvals back to 1).",
        "A `convention.replaced` timeline event with this code: `before` the replaced "
        "convention, `after` the new one.",
        "None if the new ruling is intended; otherwise correct the asset in a later study.",
        "no",
    ),
    _e(
        E.ASSETS_CONVENTION_CONFLICT,
        "flag",
        "process_assets",
        "basis.classify.router",
        "The firm's rulings for the component's label disagree with equal weight at the "
        "tier that would have served it (CLASSIFICATION_CONVENTIONS.md section 9.3), so "
        "the rules table or the model classified it instead.",
        "`review_reasons` `convention_conflict:<tier>` (`label_room` or `label`).",
        "The engineer rules on the asset at the assets review; the approved ruling becomes "
        "the exact convention, which wins next time.",
        "no",
        signal="convention_conflict:",
    ),
    _e(
        E.ASSETS_REPRICE_CLASS_CHANGED,
        "outcome",
        "process_assets",
        "basis.activities.process_assets",
        "A reprice found the property class changed since the revision was classified (the "
        "intake changed between the request and the rebuild): nothing was carried and every "
        "asset was classified again.",
        "Timeline event `assets.reprice_reclassified` with the old and new class.",
        "Review the reclassified assets at the assets review.",
        "no",
    ),
    # ------------------------------------------------------------------ EXPORT
    _e(
        E.EXPORT_STUDY_NOT_FOUND,
        "error",
        "export_report",
        "basis.activities.export_report",
        "The study is gone at export time.",
        "`ValidationError: ... [BASIS-EXPORT-001]`.",
        "Operator: the study was deleted.",
        "no",
        temporal_type="ValidationError",
    ),
    # ------------------------------------------------------------------ CHECK (validators)
    _e(
        E.CHECK_SECTION_BUCKET_MISMATCH,
        "flag",
        "process_assets, asset corrections",
        "basis.domain.validators",
        "The IRS section does not allow the bucket (1245: 5/7/15; 1250: 15/27.5/39).",
        "`review_reasons` `validation:SECTION_BUCKET_MISMATCH`.",
        "Correct the section or the bucket.",
        "no",
        signal="validation:SECTION_BUCKET_MISMATCH",
    ),
    _e(
        E.CHECK_LAND_NOT_DEPRECIABLE,
        "flag",
        "process_assets",
        "basis.domain.validators",
        "An asset is in the `land` bucket.",
        "`validation:LAND_NOT_DEPRECIABLE`.",
        "Land is not depreciable; move the cost out of the schedule.",
        "no",
        signal="validation:LAND_NOT_DEPRECIABLE",
    ),
    _e(
        E.CHECK_SECTION_MISSING,
        "flag",
        "process_assets",
        "basis.domain.validators",
        "No IRS section recorded.",
        "`validation:SECTION_MISSING`.",
        "Set 1245 or 1250.",
        "no",
        signal="validation:SECTION_MISSING",
    ),
    _e(
        E.CHECK_SECTION_UNKNOWN,
        "flag",
        "process_assets",
        "basis.domain.validators",
        "The IRS section is neither 1245 nor 1250.",
        "`validation:SECTION_UNKNOWN`.",
        "Set 1245 or 1250.",
        "no",
        signal="validation:SECTION_UNKNOWN",
    ),
    _e(
        E.CHECK_RECOVERY_PERIOD_MISMATCH,
        "flag",
        "process_assets",
        "basis.domain.validators",
        "The recovery period is not the bucket's.",
        "`validation:RECOVERY_PERIOD_MISMATCH`.",
        "Correct the period.",
        "no",
        signal="validation:RECOVERY_PERIOD_MISMATCH",
    ),
    _e(
        E.CHECK_BUILDING_BUCKET_PROPERTY_CLASS,
        "flag",
        "process_assets",
        "basis.domain.validators",
        "A building bucket does not match the property class "
        "(27.5 residential rental, 39 nonresidential).",
        "`validation:BUILDING_BUCKET_PROPERTY_CLASS`.",
        "Correct the bucket or the intake's `property_use`.",
        "no",
        signal="validation:BUILDING_BUCKET_PROPERTY_CLASS",
    ),
    _e(
        E.CHECK_STRUCTURAL_AS_PERSONAL,
        "flag",
        "process_assets",
        "basis.domain.validators",
        "A structural-looking label is classified section 1245.",
        "`validation:STRUCTURAL_AS_PERSONAL`.",
        "Confirm it is really personal property.",
        "no",
        signal="validation:STRUCTURAL_AS_PERSONAL",
    ),
    _e(
        E.CHECK_PERSONAL_AS_BUILDING,
        "flag",
        "process_assets",
        "basis.domain.validators",
        "A personal-property-looking label is in a building bucket.",
        "`validation:PERSONAL_AS_BUILDING`.",
        "Confirm the classification.",
        "no",
        signal="validation:PERSONAL_AS_BUILDING",
    ),
    _e(
        E.CHECK_ROOM_CONTEXT,
        "flag",
        "process_assets",
        "basis.domain.validators",
        "A room-context note (mechanical room items are often 1245). Informational: it never "
        "flags by itself.",
        "Issue code `ROOM_CONTEXT` (info).",
        "None.",
        "no",
        signal="ROOM_CONTEXT",
    ),
    _e(
        E.CHECK_CROSS_SECTION_1245_SF_LARGE,
        "flag",
        "process_assets",
        "basis.domain.cross_validation",
        "A section 1245 asset measured in square feet is implausibly large.",
        "`cross:SECTION_1245_SF_LARGE`.",
        "Check the takeoff.",
        "no",
        signal="cross:SECTION_1245_SF_LARGE",
    ),
    _e(
        E.CHECK_CROSS_COST_NEEDS_QUANTITY,
        "flag",
        "process_assets",
        "basis.domain.cross_validation",
        "The cost row needs a quantity the takeoff could not produce.",
        "`cross:COST_NEEDS_QUANTITY`.",
        "Measure the item.",
        "no",
        signal="cross:COST_NEEDS_QUANTITY",
    ),
    _e(
        E.CHECK_CROSS_COST_ROW_MISSING,
        "flag",
        "process_assets",
        "basis.domain.cross_validation",
        "No cost row prices the component's label.",
        "`cross:COST_ROW_MISSING`; the cost estimate has no amounts.",
        "Price it by hand, or add the row to the cost table.",
        "no",
        signal="cross:COST_ROW_MISSING",
    ),
    _e(
        E.CHECK_CROSS_COST_BELOW_RANGE,
        "flag",
        "process_assets",
        "basis.domain.cross_validation",
        "The unit cost is below the row's plausible range.",
        "`cross:COST_BELOW_RANGE`.",
        "Check the row and the quantity.",
        "no",
        signal="cross:COST_BELOW_RANGE",
    ),
    _e(
        E.CHECK_CROSS_COST_ABOVE_RANGE,
        "flag",
        "process_assets",
        "basis.domain.cross_validation",
        "The unit cost is above the row's plausible range.",
        "`cross:COST_ABOVE_RANGE`.",
        "Check the row and the quantity.",
        "no",
        signal="cross:COST_ABOVE_RANGE",
    ),
    _e(
        E.CHECK_CROSS_COST_TOTAL_MISMATCH,
        "flag",
        "process_assets",
        "basis.domain.cross_validation",
        "The total is not quantity times unit cost.",
        "`cross:COST_TOTAL_MISMATCH`.",
        "Report it: a pricing defect.",
        "no",
        signal="cross:COST_TOTAL_MISMATCH",
    ),
    _e(
        E.CHECK_EXTRACTION_MISSING,
        "flag",
        "extract_fields",
        "basis.extraction.confidence",
        "A critical field is empty.",
        "`review_reasons` `missing:<key>`.",
        "Fill it at the appraisal review (an `extraction_field` correction).",
        "no",
        signal="missing:",
    ),
    _e(
        E.CHECK_EXTRACTION_LOW_CONFIDENCE,
        "flag",
        "extract_fields",
        "basis.extraction.confidence",
        "A critical field is below the review threshold.",
        "`low_confidence:<key>=<c><<t>`.",
        "Confirm or correct the value.",
        "no",
        signal="low_confidence:",
    ),
    _e(
        E.CHECK_EXTRACTION_MISSING_CRITICAL,
        "flag",
        "extract_fields",
        "basis.extraction.validation",
        "The validator found a critical field missing.",
        "issue `missing_critical`.",
        "Fill it.",
        "no",
        signal="missing_critical",
    ),
    _e(
        E.CHECK_EXTRACTION_TYPE,
        "flag",
        "extract_fields",
        "basis.extraction.validation",
        "A value does not parse as its type (a number, a date).",
        "issue `type:<key>`.",
        "Correct the value.",
        "no",
        signal="type",
    ),
    _e(
        E.CHECK_EXTRACTION_RANGE,
        "flag",
        "extract_fields",
        "basis.extraction.validation",
        "A value is implausible (a date out of range).",
        "issue `range:<key>`.",
        "Correct the value.",
        "no",
        signal="range",
    ),
    _e(
        E.CHECK_EXTRACTION_BASEMENT_VS_GLA,
        "flag",
        "extract_fields",
        "basis.extraction.validation",
        "The basement area exceeds the gross living area.",
        "issue `basement_vs_gla`.",
        "Check both fields.",
        "no",
        signal="basement_vs_gla",
    ),
    _e(
        E.CHECK_EXTRACTION_VALUE_VS_CONTRACT,
        "flag",
        "extract_fields",
        "basis.extraction.validation",
        "The appraised value is far from the contract price.",
        "issue `value_vs_contract`.",
        "Check both fields.",
        "no",
        signal="value_vs_contract",
    ),
    _e(
        E.CHECK_EXTRACTION_SOURCES_DISAGREE,
        "flag",
        "extract_fields",
        "basis.extraction.sections",
        "Two readers (Azure DI, the text layer, the model) disagree on a field.",
        "issue `sources_disagree:<keys>`.",
        "Pick the right value.",
        "no",
        signal="sources_disagree",
    ),
    _e(
        E.CHECK_EXTRACTION_AZURE_DI_REJECTED,
        "flag",
        "extract_fields",
        "basis.extraction.pipeline",
        "Azure Document Intelligence refused the PDF; only the text layer was read.",
        "issue `azure_di_rejected`.",
        "Check the values; a clean PDF may read better.",
        "no",
        signal="azure_di_rejected",
    ),
    _e(
        E.CHECK_EXTRACTION_XML_PDF_MISMATCH,
        "flag",
        "extract_fields",
        "basis.extraction.mismo36",
        "A UAD 3.6 XML value is not printed where its caption is on the report PDF.",
        "issue `xml_pdf_mismatch:<keys>` (warning).",
        "Check the XML against the PDF.",
        "no",
        signal="xml_pdf_mismatch",
    ),
    _e(
        E.CHECK_EXTRACTION_NO_REPORT_PDF,
        "flag",
        "extract_fields",
        "basis.activities.appraisal_sources",
        "A bare XML with no report PDF: no regions, no study corpus.",
        "issue `no_report_pdf` (warning).",
        "Upload the report PDF as another appraisal document.",
        "no",
        signal="no_report_pdf",
    ),
    _e(
        E.CHECK_PHOTO_AMBIGUOUS,
        "flag",
        "analyze_photo",
        "basis.vision.analyze",
        "The model could not tell the room or found nothing usable.",
        "photo `review_reason` `ambiguous`; the photo is in `flagged_photos`.",
        "Assign the room and add components by hand.",
        "no",
        signal="ambiguous",
    ),
    _e(
        E.CHECK_PHOTO_LOW_ROOM_CONFIDENCE,
        "flag",
        "analyze_photo",
        "basis.vision.analyze",
        "The room identity is below the confidence floor.",
        "`low_room_confidence:<c>`.",
        "Confirm the room.",
        "no",
        signal="low_room_confidence:",
    ),
    _e(
        E.CHECK_PHOTO_INVALID_MODEL_OUTPUT,
        "flag",
        "analyze_photo",
        "basis.vision.analyze",
        "The model's answer stayed invalid after one retry; the photo got an `ambiguous` analysis.",
        "`invalid_model_output[:calls]`.",
        "Re-analyze the photo (a `photo` correction) or add by hand.",
        "no",
        signal="invalid_model_output",
    ),
    _e(
        E.CHECK_PHOTO_COUNT_FUSION_FALLBACK,
        "flag",
        "analyze_photo",
        "basis.vision.analyze",
        "The second counting model failed; the base counts stand.",
        "`count_fusion_fallback:<why>`.",
        "Check the counts.",
        "no",
        signal="count_fusion_fallback:",
    ),
    _e(
        E.CHECK_ROOM_DROPPED_OBJECTS,
        "flag",
        "reconcile_rooms",
        "basis.vision.room_step",
        "The room step dropped objects it could not reconcile.",
        "room `review_reason` `dropped_objects:<n>`.",
        "Check the room's inventory against its photos.",
        "no",
        signal="dropped_objects:",
    ),
    _e(
        E.CHECK_ROOM_SINGLE_PHOTO,
        "flag",
        "reconcile_rooms",
        "basis.vision.room_step",
        "A one-photo room's inventory is the per-photo analysis (configured so).",
        "`single_photo`.",
        "None unless the counts look wrong.",
        "no",
        signal="single_photo",
    ),
    _e(
        E.CHECK_ROOM_INVALID_MODEL_OUTPUT,
        "flag",
        "reconcile_rooms",
        "basis.vision.reconcile",
        "The reconciliation model's answer was unusable; the heuristic merge was used.",
        "`invalid_model_output`.",
        "Check the inventory.",
        "no",
        signal="invalid_model_output",
    ),
    _e(
        E.CHECK_ROOM_TOO_MANY_PHOTOS,
        "flag",
        "reconcile_rooms",
        "basis.vision.room_step",
        "The room has more photos than one call can take.",
        "`too_many_photos`.",
        "Split the room or check the inventory.",
        "no",
        signal="too_many_photos",
    ),
    _e(
        E.CHECK_CLASSIFY_TOOL_CAP,
        "flag",
        "process_assets",
        "basis.classify.residual",
        "The classifier hit its tool-call cap without an answer.",
        "`tool_cap_exceeded`.",
        "Classify by hand.",
        "no",
        signal="tool_cap_exceeded",
    ),
    _e(
        E.CHECK_CLASSIFY_ITERATION_CAP,
        "flag",
        "process_assets",
        "basis.classify.residual",
        "The classifier hit its iteration cap.",
        "`iteration_cap`.",
        "Classify by hand.",
        "no",
        signal="iteration_cap",
    ),
    _e(
        E.CHECK_CLASSIFY_INVALID_OUTPUT,
        "flag",
        "process_assets",
        "basis.classify.residual",
        "The classifier's final answer did not parse.",
        "`invalid_output`.",
        "Classify by hand.",
        "no",
        signal="invalid_output",
    ),
    _e(
        E.CHECK_CLASSIFY_TIMEOUT,
        "flag",
        "process_assets",
        "basis.classify.residual",
        "The classifier timed out.",
        "`timeout`.",
        "Classify by hand or retry.",
        "no",
        signal="timeout",
    ),
    _e(
        E.CHECK_CLASSIFY_BUDGET,
        "flag",
        "process_assets",
        "basis.classify.residual",
        "The classifier's reservation ran out mid-component.",
        "`budget_exceeded`.",
        "Raise the budget.",
        "no",
        signal="budget_exceeded",
    ),
    _e(
        E.CHECK_CLASSIFY_PROVIDER_ERROR,
        "flag",
        "process_assets",
        "basis.classify.residual",
        "A provider error on the classifier's call (not an account error, which stops the stage).",
        "`provider_error:<Type>`.",
        "Classify by hand or retry.",
        "no",
        signal="provider_error:",
    ),
    _e(
        E.CHECK_CLASSIFY_NO_ANSWER,
        "flag",
        "process_assets",
        "basis.classify.router",
        "The classifier produced no classification and no reason.",
        "`no_answer`.",
        "Classify by hand.",
        "no",
        signal="no_answer",
    ),
    # ------------------------------------------------------------------ COST
    _e(
        E.COST_YEAR_INDEX_MISSING,
        "flag",
        "process_assets",
        "basis.domain.cost_indices",
        "No cost index is loaded for the study's cost year (the store holds none and no "
        "fetcher supplied one); every priced row takes the latest loaded index with the "
        "assumption recorded (COST_INDICES.md section 4), and `summary.basis.cost_index` "
        "names the value to enter.",
        "`cost:YEAR_INDEX_MISSING` on every priced row; the takeoff inputs' "
        "`cost_year_assumption`; `summary.basis.cost_index.status` `missing`.",
        "Enter the year's RSMeans Historical Cost Index (`POST /v1/cost-indices`, or "
        "`python -m basis.costs.refresh enter`) or run the refresh job, then rerun the "
        "assets step.",
        "no",
        signal="cost:YEAR_INDEX_MISSING",
    ),
    _e(
        E.COST_YEAR_INDEX_CONVERTED,
        "flag",
        "process_assets",
        "basis.costs.indices",
        "The cost year's index is not a published RSMeans value but one converted from a "
        "public series (the BLS Producer Price Index) with the error band recorded "
        "(COST_INDICES.md section 11).",
        "`cost:YEAR_INDEX_CONVERTED` on every priced row; the takeoff inputs' "
        "`cost_year_conversion`; `summary.basis.cost_index.status` `converted`.",
        "Enter the year's published RSMeans Historical Cost Index; a manual entry outranks "
        "the converted value.",
        "no",
        signal="cost:YEAR_INDEX_CONVERTED",
    ),
    _e(
        E.COST_INDEX_DUPLICATE,
        "refusal",
        "POST /v1/cost-indices",
        "basis.api.routes.cost_indices",
        "The store's latest version of this source, series, year and region already "
        "carries the same value and citation; nothing is written twice.",
        PROBLEM,
        "Nothing to do; to revise a value, send the new value or citation (it becomes the "
        "next version).",
        "no",
        problem="duplicate-value",
    ),
    _e(
        E.COST_INDEX_SOURCE_UNAVAILABLE,
        "outcome",
        "python -m basis.costs.refresh",
        "basis.costs.refresh",
        "A configured source was not asked: fetching is off (`COST_INDEX_FETCH`) or the "
        "source lacks its credentials (the RSMeans API without a subscription).",
        "A `cost_index_runs` row with status `skipped` and the reason in `detail.reason`.",
        "Turn fetching on or configure the source; or enter the value manually.",
        "no",
    ),
    _e(
        E.COST_INDEX_FETCH_FAILED,
        "outcome",
        "python -m basis.costs.refresh",
        "basis.costs.refresh",
        "A fetcher raised or returned no value for a wanted year (network, timeout, an "
        "unexpected document); the store is unchanged for that source.",
        "A `cost_index_runs` row with status `failed` and the errors in `failures`; the "
        "job's exit status 1.",
        "Retry (the next scheduled run does); the failures name the year and the cause.",
        "yes",
    ),
    # ------------------------------------------------------------------ INF
    _e(
        E.INF_WORKER_CONFIG,
        "error",
        "worker start-up",
        "basis.worker.main",
        "A model the configured pipeline calls has no configured provider, or the embedding "
        "model cannot load: the worker refuses to start.",
        "The worker exits; its last log "
        "line names the models and the settings (`[BASIS-INF-001]`).",
        "Configure the provider or the model setting named, redeploy.",
        "no",
    ),
    _e(
        E.INF_RELAY_REFUSED,
        "error",
        "the outbox relay",
        "basis.relay.delivery",
        "The workflow's validator refused a decision the API had recorded ("
        + E.WF_INVALID_DECISION
        + "); the refusal is final.",
        "Relay log `outbox message refused by its consumer` "
        "with `error_code`; the outbox row is `delivered` with `last_error`.",
        "Decide again on the current stage and revision.",
        "no",
    ),
    _e(
        E.INF_RELAY_DEAD,
        "error",
        "the outbox relay",
        "basis.relay.delivery",
        "A message failed `RELAY_MAX_ATTEMPTS` deliveries (Temporal unreachable for long).",
        "Outbox row `status = dead` with `last_error`; relay log with `error_code`.",
        "Operator: re-arm the row (`python -m basis.admin`) once Temporal is back.",
        "after fix",
    ),
)

BY_CODE: Final[dict[str, Entry]] = {entry.code: entry for entry in ENTRIES}

# The code a problem type suffix means when the raise site passes none. A suffix several
# entries share (``not-found``, ``service-unavailable``, ``stale-state`` ...) names its generic
# here; the specific sites pass their code explicitly. :func:`check_consistent` refuses a
# shared suffix that has no generic.
GENERIC_FOR_PROBLEM: Final[dict[str, str]] = {
    "unauthorized": E.API_AUTH_INVALID,
    "forbidden": E.API_FORBIDDEN,
    "not-found": E.API_NOT_FOUND,
    "conflict": E.API_CONFLICT,
    "validation": E.API_VALIDATION,
    "unprocessable": E.API_UNPROCESSABLE,
    "bad-request": E.API_BAD_REQUEST,
    "payload-too-large": E.API_PAYLOAD_TOO_LARGE,
    "rate-limited": E.API_RATE_LIMITED,
    "service-unavailable": E.API_SERVICE_UNAVAILABLE,
    "stale-state": E.STUDY_STALE_STATE,
    "gate-not-open": E.STUDY_GATE_NOT_OPEN,
}


def _by_problem() -> dict[str, str]:
    counts: dict[str, list[str]] = {}
    for entry in ENTRIES:
        if entry.problem is not None:
            counts.setdefault(entry.problem, []).append(entry.code)
    table = dict(GENERIC_FOR_PROBLEM)
    for suffix, codes in counts.items():
        if suffix not in table and len(codes) == 1:
            table[suffix] = codes[0]
    return table


BY_PROBLEM: Final[dict[str, str]] = _by_problem()

# The fallback code for an ApplicationError type that carries no code of its own.
BY_TEMPORAL_TYPE: Final[dict[str, str]] = {
    "ProviderAccountError": E.PROVIDER_ACCOUNT,
    "ProviderUnavailable": E.PROVIDER_UNAVAILABLE,
    "ProviderNotConfigured": E.PROVIDER_NOT_CONFIGURED_VISION,
    "LocalizerUnavailable": E.PROVIDER_LOCALIZER_UNAVAILABLE,
    "BudgetExceededError": E.BUDGET_PHOTO,
    "SimulatedFailure": E.WF_SIMULATED_FAILURE,
    "InvalidDecision": E.WF_INVALID_DECISION,
    "InvalidRetry": E.WF_INVALID_RETRY,
}
TIMEOUT_CODES: Final[dict[str, str]] = {
    "HEARTBEAT": E.WF_HEARTBEAT_TIMEOUT,
    "START_TO_CLOSE": E.WF_START_TO_CLOSE_TIMEOUT,
    "SCHEDULE_TO_CLOSE": E.WF_SCHEDULE_TO_CLOSE_TIMEOUT,
    "SCHEDULE_TO_START": E.WF_SCHEDULE_TO_START_TIMEOUT,
}

# Review-reason strings, longest prefix first, so ``validation:X`` wins over a bare word.
_SIGNALS: Final[tuple[tuple[str, str], ...]] = tuple(
    sorted(
        ((entry.signal, entry.code) for entry in ENTRIES if entry.signal is not None),
        key=lambda item: -len(item[0]),
    )
)
_ROOM_SIGNALS: Final = frozenset(
    {E.CHECK_ROOM_INVALID_MODEL_OUTPUT, E.CHECK_ROOM_DROPPED_OBJECTS, E.CHECK_ROOM_SINGLE_PHOTO}
)


def code_for_problem(type_suffix: str | None) -> str | None:
    """The generic code of an RFC 9457 type suffix (``None`` for an unknown one)."""
    return None if type_suffix is None else BY_PROBLEM.get(type_suffix)


def code_for_reason(reason: str, *, room: bool = False) -> str | None:
    """The code behind a stored review reason (``validation:SECTION_MISSING``,
    ``rule_contested:kitchen_cabinet``, ``missing:subject.gla``, ``cross:COST_ROW_MISSING``,
    ``ambiguous`` ...), or ``None`` for text the catalogue does not know. ``room`` picks the
    room step's reading of a word both steps use (``invalid_model_output``)."""
    text = reason.strip()
    for signal, code in _SIGNALS:
        if signal.endswith(":"):
            matched = text.startswith(signal)
        else:
            matched = text == signal or text.startswith(signal + ":")
        if not matched:
            continue
        if text.startswith("invalid_model_output"):
            return E.CHECK_ROOM_INVALID_MODEL_OUTPUT if room else E.CHECK_PHOTO_INVALID_MODEL_OUTPUT
        return code
    return None


def codes_for_reasons(reasons: Iterable[str], *, room: bool = False) -> list[str]:
    """The distinct codes behind ``reasons``, in order, unknown reasons skipped."""
    found: list[str] = []
    for reason in reasons:
        code = code_for_reason(reason, room=room)
        if code is not None and code not in found:
            found.append(code)
    return found


# --------------------------------------------------------------------------- the document

_HEADER: Final = """\
# Error codes

Every failure, refusal or review flag a study can hit has a stable code `BASIS-<area>-<nnn>`.
Look the code up here from wherever you saw it:

| Where you saw it | How it got there |
|---|---|
| a problem response (`application/problem+json`) | the `code` member, next to RFC 9457's `type` |
| `GET /v1/studies/{id}/workflow` → `status.failure` / `failure_code`, `GET /v1/studies/{id}` → `failure_reason` | the workflow's failure reason `<activity>: <type>: <message> [BASIS-...]` and the code alone |
| `GET /v1/studies/{id}/timeline` → an `activity.failed`, `stage.failed` or `corrections.refused` event | its `code` member (docs/api/TIMELINE_AND_AUDIT.md) |
| `activity_results.error`, the worker log, Temporal's failure message | `[BASIS-...]` at the end of the message |
| `review_reasons` on an asset or extraction, `review_codes` beside it | `review_codes` (the codes behind each reason) |
| the API log | `error_code` on the problem's log line |

`kind`: **refusal** (the API refused the request: a 4xx), **error** (a step failed),
**flag** (a check put a row in front of the engineer; nothing failed), **outcome** (a recorded
state worth knowing). `retry`: **no** (change the input or configuration first),
**automatic** (Temporal retries; act only if it keeps failing), **after fix** (`POST
/v1/studies/{id}/retry` once the cause is fixed), **yes** (repeat the request).

Numbers are never reused. Source of truth: `backEnd/basis/errors/catalogue.py` (this file is
rendered from it: `uv run python -m basis.errors.catalogue --write`; a test fails while it is
stale).

"""


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(entries: Iterable[Entry] = ENTRIES) -> str:
    """The Markdown document, one table per area, in area order."""
    by_area: dict[str, list[Entry]] = {}
    for entry in entries:
        by_area.setdefault(entry.area, []).append(entry)
    parts = [_HEADER]
    total = sum(len(rows) for rows in by_area.values())
    parts.append(f"{total} codes in {len(by_area)} areas.\n\n")
    for area, title in AREA_TITLES.items():
        rows = by_area.get(area)
        if not rows:
            continue
        parts.append(f"## {area}: {title}\n\n")
        parts.append(
            "| Code | Kind | Where (step; component) | What happened | What you see | "
            "How to fix | Retry |\n|---|---|---|---|---|---|---|\n"
        )
        for row in sorted(rows, key=lambda r: r.code):
            where = f"{row.step}; `{row.component}`"
            identifiers = []
            if row.problem:
                identifiers.append(f"type `{row.problem}`")
            if row.temporal_type:
                identifiers.append(f"Temporal `{row.temporal_type}`")
            if row.signal:
                identifiers.append(f"reason `{row.signal}`")
            seen = row.seen + (f" ({'; '.join(identifiers)})" if identifiers else "")
            parts.append(
                f"| `{row.code}` | {row.kind} | {_cell(where)} | {_cell(row.what)} | "
                f"{_cell(seen)} | {_cell(row.fix)} | {row.retry} |\n"
            )
        parts.append("\n")
    return "".join(parts)


def check_consistent() -> list[str]:
    """Problems with the catalogue itself: a constant without an entry, an entry without a
    constant, a duplicate code, a code outside the pattern."""
    problems: list[str] = []
    codes = all_codes()
    seen: set[str] = set()
    for entry in ENTRIES:
        if entry.code in seen:
            problems.append(f"duplicate entry {entry.code}")
        seen.add(entry.code)
        if not CODE_PATTERN.fullmatch(entry.code):
            problems.append(f"malformed code {entry.code}")
        if entry.area not in AREA_TITLES:
            problems.append(f"unknown area in {entry.code}")
    for name, code in codes.items():
        if code not in seen:
            problems.append(f"E.{name} = {code} has no catalogue entry")
    constants = set(codes.values())
    for code in seen - constants:
        problems.append(f"entry {code} has no constant on E")
    if len(constants) != len(codes):
        problems.append("two constants share a code")
    suffixes: dict[str, int] = {}
    for entry in ENTRIES:
        if entry.problem is not None:
            suffixes[entry.problem] = suffixes.get(entry.problem, 0) + 1
    for suffix, count in suffixes.items():
        if count > 1 and suffix not in GENERIC_FOR_PROBLEM:
            problems.append(f"problem type {suffix!r} is shared by {count} entries, no generic")
    for suffix, code in GENERIC_FOR_PROBLEM.items():
        if code not in seen or suffix not in suffixes:
            problems.append(f"generic {suffix!r} -> {code} does not match an entry")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render or check docs/ERROR_CODES.md.")
    parser.add_argument("--write", action="store_true", help="write the document")
    parser.add_argument("--check", action="store_true", help="exit 1 when the document is stale")
    args = parser.parse_args(argv)
    problems = check_consistent()
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2
    document = render()
    if args.write:
        DOCUMENT_PATH.write_text(document)
        print(f"wrote {DOCUMENT_PATH} ({len(ENTRIES)} codes)")
        return 0
    current = DOCUMENT_PATH.read_text() if DOCUMENT_PATH.exists() else ""
    if args.check:
        if current != document:
            print(f"{DOCUMENT_PATH} is stale; run --write", file=sys.stderr)
            return 1
        print("up to date")
        return 0
    sys.stdout.write(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
