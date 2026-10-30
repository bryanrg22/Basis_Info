"""``/v1/cost-indices``: the cost index store (COST_INDICES.md sections 9-12).

* ``GET /v1/cost-indices``: the store's values, newest first (``series``, ``year``,
  ``source`` filters; ``all_versions`` lists superseded rows too). Any firm member.
* ``POST /v1/cost-indices``: enter one value by hand with its citation (source ``manual``).
  A firm owner (the admin role) of any firm: the store is shared by every firm on the
  deployment and the value is a published fact, cited. Appends a row (the next version of
  its key when the key exists) and the ledger row that says who entered it and when; the
  same value and citation twice is 409 ``duplicate-value`` and nothing is written.
* ``GET /v1/cost-indices/runs``: the ledger, newest first (manual entries, imports, refresh
  runs with what they asked for and what arrived).

No route fetches anything: network fetches happen only in ``python -m basis.costs.refresh``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Final

from fastapi import Query, status

from basis.api.errors import Conflict, Forbidden, Unprocessable
from basis.api.routes.common import problems, read_router, write_router
from basis.api.schemas.cost_indices import (
    CostIndexCreate,
    CostIndexCreatedOut,
    CostIndexOut,
    CostIndexPageOut,
    CostIndexRunOut,
    CostIndexRunsOut,
)
from basis.api.users import CurrentUserDep, require_firm
from basis.costs.refresh import record_manual
from basis.db.enums import OrgRole
from basis.db.repositories.cost_index import MAX_PAGE, CostIndexRepository, IndexEntry
from basis.db.session import SessionDep
from basis.errors.codes import E

DEFAULT_PAGE: Final = 200
MAX_CURSOR_LENGTH: Final = 128
MAX_SOURCE_LENGTH: Final = 64
MANUAL: Final = "manual"
DETAIL_OWNER_REQUIRED: Final = (
    "entering a cost index needs the owner role of a firm: it prices every study on the deployment"
)

reads = read_router(tags=["cost indices"])
writes = write_router(tags=["cost indices"])

Limit = Annotated[int, Query(ge=1, le=MAX_PAGE, description="Rows per page.")]
Cursor = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=MAX_CURSOR_LENGTH,
        description="Opaque `next_cursor` of the previous page.",
    ),
]
SeriesFilter = Annotated[
    str | None, Query(pattern="^(historical|city)$", description="Only this series.")
]
YearFilter = Annotated[int | None, Query(ge=1900, le=2200, description="Only this year.")]
SourceFilter = Annotated[
    str | None,
    Query(min_length=1, max_length=MAX_SOURCE_LENGTH, pattern="^[a-z0-9_]+$"),
]


def _cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if cursor is None:
        return None
    when, _, row_id = cursor.partition("|")
    try:
        return datetime.fromisoformat(when), uuid.UUID(row_id)
    except ValueError as exc:
        raise Unprocessable(
            "invalid pagination cursor", type_suffix="invalid-cursor", code=E.API_INVALID_CURSOR
        ) from exc


@reads.get(
    "/cost-indices",
    summary="The cost index store: every value obtained, with its citation",
    responses=problems(403),
)
async def list_cost_indices(
    user: CurrentUserDep,
    session: SessionDep,
    limit: Limit = DEFAULT_PAGE,
    cursor: Cursor = None,
    series: SeriesFilter = None,
    year: YearFilter = None,
    source: SourceFilter = None,
    all_versions: Annotated[
        bool, Query(description="Also the superseded versions (default: current rows only).")
    ] = False,
) -> CostIndexPageOut:
    await require_firm(session, user)
    page = await CostIndexRepository(session).page(
        limit=limit,
        cursor=_cursor(cursor),
        series=series,  # type: ignore[arg-type]
        year=year,
        source=source,
        current_only=not all_versions,
    )
    return CostIndexPageOut(
        items=[CostIndexOut.of(row) for row in page.items], next_cursor=page.next_cursor
    )


@reads.get(
    "/cost-indices/runs",
    summary="The ledger: every attempt to obtain values, newest first",
    responses=problems(403),
)
async def list_cost_index_runs(
    user: CurrentUserDep,
    session: SessionDep,
    limit: Limit = 100,
    source: SourceFilter = None,
) -> CostIndexRunsOut:
    await require_firm(session, user)
    rows = await CostIndexRepository(session).runs(limit=limit, source=source)
    return CostIndexRunsOut(items=[CostIndexRunOut.of(row) for row in rows])


@writes.post(
    "/cost-indices",
    status_code=status.HTTP_201_CREATED,
    summary="Enter a cost index value by hand, with its citation",
    description="A firm owner enters a published value the store lacks (the year's RSMeans "
    "Historical Cost Index, a location factor). The row is appended with the entering user "
    "and the time; a value for a key the store holds becomes its next version and the old "
    "row stays. The same value and citation as the current version is 409 `duplicate-value`.",
    responses=problems(403, 409),
)
async def create_cost_index(
    body: CostIndexCreate, user: CurrentUserDep, session: SessionDep
) -> CostIndexCreatedOut:
    memberships = await require_firm(session, user)
    if OrgRole.OWNER not in memberships.values():
        raise Forbidden(DETAIL_OWNER_REQUIRED, code=E.API_FORBIDDEN)
    entry = IndexEntry(
        source=MANUAL,
        series=body.series,
        year=body.year,
        region=body.region,
        value=body.decimal,
        as_of=body.as_of,
        citation=body.citation,
        url=body.url,
        derived=body.derived,
        detail=dict(body.detail) if body.detail else None,
    )
    principal = user.principal
    name = user.user.email or user.user.display_name or principal.uid
    # Nothing is written for a repeat, not even the ledger row: the 409 says so.
    if not (await CostIndexRepository(session).plan([entry])).new:
        raise Conflict(
            f"the store's current {body.series} {body.year} {body.region} value from manual "
            "entry already carries this value and citation; nothing written",
            type_suffix="duplicate-value",
            code=E.COST_INDEX_DUPLICATE,
        )
    rows, run, _ = await record_manual(
        session,
        [entry],
        actor_id=user.id,
        actor_name=name,
        trigger="manual",
        detail={"series": body.series, "year": body.year, "region": body.region},
    )
    return CostIndexCreatedOut(value=CostIndexOut.of(rows[0]), run=CostIndexRunOut.of(run))


__all__ = ["reads", "writes"]
