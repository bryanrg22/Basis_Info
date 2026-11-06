"""Your signer details: ``GET``/``PUT /v1/me/signer`` (EVAL_REPORT 91; docs/api/SIGNERS.md).

The name, credentials and license an engineer signs reports with are the engineer's own: a
seal belongs to the individual licensee, never to the firm (NCEES Model Law 140.10 D, Model
Rules 240.20), so each user saves theirs once and only they can change them. There is no route
that writes another user's details, whatever the caller's role: an owner reads a member's in
the members list and cannot set them. The details are written whole with ``If-Match`` on their
version, as the firm profile is; ``POST .../report/sign`` prints them.

Any signed-in user may save details, including one with no firm yet (an engineer can fill
them in before accepting an invitation); they only matter when that user signs.
"""

from __future__ import annotations

import logging

from fastapi import Response

from basis.api.errors import PreconditionFailed
from basis.api.preconditions import ETAG_HEADER, intake_etag, parse_if_match
from basis.api.routes.common import problems, read_router, write_router
from basis.api.routes.report import IfMatchHeader
from basis.api.schemas.signer import SignerDetailsOut
from basis.api.users import CurrentUserDep
from basis.db.errors import PreconditionFailedError
from basis.db.models import SignerDetailsRow
from basis.db.repositories.signer import SignerRepository
from basis.db.session import SessionDep
from basis.errors.codes import E
from basis.report.inputs import SignerDetails

logger = logging.getLogger(__name__)

reads = read_router(tags=["me"])
writes = write_router(tags=["me"])


def _out(user_id: object, row: SignerDetailsRow | None) -> SignerDetailsOut:
    return SignerDetailsOut.model_validate(
        {
            "user_id": user_id,
            "signer": row.details() if row else None,
            "version": row.version if row else 0,
            "updated_at": row.updated_at if row else None,
        }
    )


@reads.get(
    "/me/signer",
    summary="Your signer details: the name, credentials and license you sign reports with",
    description='`signer` is null until you save them; the `ETag` is `"0"` then. '
    "Send the `ETag` back as `If-Match` on the `PUT`.",
)
async def get_signer(
    user: CurrentUserDep, session: SessionDep, response: Response
) -> SignerDetailsOut:
    row = await SignerRepository(session).get(user.id)
    response.headers[ETAG_HEADER] = intake_etag(row.version if row else 0)
    return _out(user.id, row)


@writes.put(
    "/me/signer",
    summary="Set or replace your signer details",
    description=(
        "The whole document: `name`, `credentials`, and the license as `license_number` with "
        "`license_state` (both or neither; the state a US state, DC, GU, MP, PR or VI). Yours "
        "only: no role writes another user's details. `If-Match` with the `ETag` of the "
        f"`GET` writes only while it is current (412 {E.REPORT_SIGNER_PRECONDITION_FAILED} "
        "with the current `ETag` otherwise); `*` only over saved details. Every report you "
        "sign afterwards prints them; a report you already signed keeps what it printed, and "
        "its `GET .../report/state` lists `signer` in `changed_since_signing` while your "
        "details differ from it."
    ),
    responses=problems(412),
)
async def put_signer(
    body: SignerDetails,
    user: CurrentUserDep,
    session: SessionDep,
    response: Response,
    if_match: IfMatchHeader = None,
) -> SignerDetailsOut:
    condition = parse_if_match(if_match)
    try:
        written = await SignerRepository(session).write(
            user.id, body, precondition=None if condition is None else condition.allows
        )
    except PreconditionFailedError as exc:
        current = intake_etag(exc.version)
        raise PreconditionFailed(
            f"Your signer details changed since you read them (their ETag is now {current}); "
            "nothing was written. Read them again, reapply your change, and send the new ETag.",
            headers={ETAG_HEADER: current},
            code=E.REPORT_SIGNER_PRECONDITION_FAILED,
        ) from exc
    logger.info(
        "signer details saved",
        extra={
            "user_id": str(user.id),
            "version_before": written.version_before,
            "version_after": written.row.version,
            "changed": written.changed,
        },
    )
    response.headers[ETAG_HEADER] = intake_etag(written.row.version)
    return _out(user.id, written.row)


__all__ = ["reads", "writes"]
