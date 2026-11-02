"""``GET /v1/ops/eval-runs``, ``.../{run_id}`` and ``.../{run_id}/files/{name}``: the
evaluation run records for platform operators (docs/api/EVAL_RUNS.md).

* Access: a verified Firebase ID token whose uid is in ``OPS_OPERATOR_UIDS``
  (:func:`basis.api.operators.require_operator`); a firm role never grants it; 401 or 403
  ``not-operator`` without diagnostics. No database user is upserted (no database at all).
* Rate limits: the read limit by uid, then :data:`EVAL_RUNS_RATE_LIMIT_PER_MIN` per operator.
* Data: ``BASIS_EVAL_DATA_DIR`` through :func:`basis.ops.eval_runs.data_root`. Without it
  every route answers 404 ``eval-data-unavailable`` (``E.API_EVAL_DATA_UNAVAILABLE``) with
  the reason: an empty host is never an empty list.
* The directories are scanned at most once per :data:`basis.ops.eval_runs.CACHE_TTL_S` per
  process, off the event loop; one scan at a time.
* Read-only and free: no model call, no write.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi import Path as PathParam

from basis.api.errors import (
    ApiError,
    NotFound,
    PayloadTooLarge,
    ServiceUnavailable,
    TooManyRequests,
    Unprocessable,
)
from basis.api.operators import require_operator
from basis.api.ratelimit import RateLimiter, get_rate_limiter, rate_limited
from basis.api.routes.common import API_PREFIX, problems
from basis.api.schemas.eval_runs import EvalRunDetailOut, EvalRunOut, EvalRunPageOut
from basis.auth.firebase import Principal
from basis.config import Settings
from basis.errors.codes import E
from basis.ops import eval_runs
from basis.ops.eval_runs import FileName, Filters, PromptUse, RunCatalogue, RunRecord

EVAL_RUNS_RATE_LIMIT_PER_MIN: Final = 60
DEFAULT_PAGE: Final = 50
MAX_PAGE: Final = 200
MAX_FILTER_LENGTH: Final = 200
DETAIL_NOT_FOUND: Final = "Evaluation run not found."
DETAIL_FILE_NOT_FOUND: Final = "This run has no such record file."
ROUTE_PATH: Final = f"{API_PREFIX}/ops/eval-runs"
CATALOGUE_STATE_ATTR: Final = "eval_runs_catalogue"

logger = logging.getLogger(__name__)

router = APIRouter(prefix=API_PREFIX, tags=["ops"])

Filter = Annotated[str | None, Query(min_length=1, max_length=MAX_FILTER_LENGTH)]


async def eval_runs_rate_limit(
    operator: Annotated[Principal, Depends(require_operator)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> Principal:
    decision = await limiter.hit(
        f"ops-eval-runs:uid:{operator.uid}", EVAL_RUNS_RATE_LIMIT_PER_MIN, timedelta(minutes=1)
    )
    if not decision.allowed:
        raise TooManyRequests(
            f"Rate limit of {EVAL_RUNS_RATE_LIMIT_PER_MIN} run-record reads per minute exceeded.",
            retry_after=int(decision.retry_after) + 1,
        )
    return operator


def catalogue(request: Request) -> RunCatalogue:
    found: RunCatalogue | None = getattr(request.app.state, CATALOGUE_STATE_ATTR, None)
    if found is None:
        found = RunCatalogue()
        setattr(request.app.state, CATALOGUE_STATE_ATTR, found)
    return found


def _root(request: Request) -> Path:
    settings: Settings = request.app.state.settings
    root = eval_runs.data_root(settings)
    if isinstance(root, eval_runs.Unavailable):
        raise ApiError(
            404,
            detail=root.detail,
            type_suffix="eval-data-unavailable",
            code=E.API_EVAL_DATA_UNAVAILABLE,
        )
    return root


def _pipeline(request: Request) -> list[PromptUse]:
    cached: list[PromptUse] | None = getattr(request.app.state, "eval_runs_pipeline", None)
    if cached is None:
        settings: Settings = request.app.state.settings
        cached = eval_runs.pipeline_prompts(settings)
        request.app.state.eval_runs_pipeline = cached
    return cached


async def _records(request: Request) -> tuple[Path, list[RunRecord]]:
    root = _root(request)
    try:
        return root, await catalogue(request).records(root, _pipeline(request))
    except OSError as exc:
        logger.warning(
            "eval data directory could not be scanned", extra={"error": type(exc).__name__}
        )
        raise ServiceUnavailable("The evaluation data directory could not be read.") from None


async def _record(request: Request, run_id: str) -> tuple[Path, RunRecord]:
    path = eval_runs.decode_run_id(run_id)
    root, records = await _records(request)
    if path is None:
        raise NotFound(DETAIL_NOT_FOUND)
    for item in records:
        if item.path == path:
            return root, item
    raise NotFound(DETAIL_NOT_FOUND)


@router.get(
    "/ops/eval-runs",
    response_model=EvalRunPageOut,
    summary="Evaluation run records, newest first (platform operators)",
    description="Every evaluation run directory under the host's `BASIS_EVAL_DATA_DIR` as the "
    "registry (`python -m evals.registry`) reads it: component and version, prompts, models, "
    "labels and split, results, measured metrics, tokens, cost and duration with their "
    "sources, the tested commit, and the record files. Filters combine with AND "
    "(`component` repeats as OR); `next_cursor` continues with older runs. A host without "
    "evaluation data answers 404 `eval-data-unavailable`, never an empty list. Operators only "
    "(`OPS_OPERATOR_UIDS`). See docs/api/EVAL_RUNS.md.",
    responses=problems(401, 403, 404, 422, 429, 503),
    dependencies=[Depends(rate_limited("read"))],
)
async def list_eval_runs(
    request: Request,
    response: Response,
    operator: Annotated[Principal, Depends(eval_runs_rate_limit)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE, description="Runs per page.")] = DEFAULT_PAGE,
    cursor: Annotated[
        str | None, Query(min_length=1, max_length=eval_runs.MAX_CURSOR_LENGTH)
    ] = None,
    component: Annotated[
        list[Annotated[str, Query(min_length=1, max_length=MAX_FILTER_LENGTH)]] | None,
        Query(
            description="A component: a prompt name or `name/vN`, `detector:x`, or the tree "
            "component (`room_inventory`, `room_inventory/v3`). Repeat for several (OR)."
        ),
    ] = None,
    model: Filter = None,
    labels: Filter = None,
    dataset: Filter = None,
    split: Filter = None,
    status: Annotated[str | None, Query(pattern=r"^(ok|stopped|error|unknown)$")] = None,
    release: Filter = None,
) -> EvalRunPageOut:
    after = None
    if cursor is not None:
        after = eval_runs.decode_cursor(cursor)
        if after is None:
            raise Unprocessable(
                "invalid pagination cursor", type_suffix="invalid-cursor", code=E.API_INVALID_CURSOR
            )
    _, records = await _records(request)
    filters = Filters(
        components=tuple(component or ()),
        model=model,
        labels=labels,
        dataset=dataset,
        split=split,
        status=status,
        release=release,
    )
    matching = [item for item in records if eval_runs.matches(item, filters)]
    chosen, next_cursor = eval_runs.page(matching, after=after, limit=limit)
    response.headers["Cache-Control"] = "no-store"
    return EvalRunPageOut(
        items=[EvalRunOut.of(item, ROUTE_PATH) for item in chosen],
        next_cursor=next_cursor,
        total=len(matching),
    )


@router.get(
    "/ops/eval-runs/{run_id}",
    response_model=EvalRunDetailOut,
    summary="One evaluation run record (platform operators)",
    description="The list's fields plus the recorded settings, environment, harness options, "
    "a backfilled manifest's provenance and the run's error text. 404 for an id this host "
    "did not issue.",
    responses=problems(401, 403, 404, 422, 429, 503),
    dependencies=[Depends(rate_limited("read"))],
)
async def get_eval_run(
    request: Request,
    response: Response,
    operator: Annotated[Principal, Depends(eval_runs_rate_limit)],
    run_id: Annotated[str, PathParam(min_length=1, max_length=eval_runs.MAX_RUN_ID_LENGTH)],
) -> EvalRunDetailOut:
    _, item = await _record(request, run_id)
    response.headers["Cache-Control"] = "no-store"
    return EvalRunDetailOut.of(item, ROUTE_PATH)


@router.get(
    "/ops/eval-runs/{run_id}/files/{name}",
    summary="A run's record file: manifest, report, redacted ledger or console log (operators)",
    description="Served through the API with the operator check, never as a static file. "
    "Credential-named fields and API-key-shaped strings are masked, the data directory and "
    "any home directory are replaced in paths, the host name is withheld. The payload files "
    "(prompts and model answers) are never served. 404 when the run has no such file; 413 "
    "over 8 MiB.",
    responses={
        200: {
            "description": "The file as an attachment: `application/json` (manifest), "
            "`text/markdown` (report), `application/x-ndjson` (ledger), `text/plain` (console).",
            "content": {
                "application/json": {},
                "text/markdown": {},
                "application/x-ndjson": {},
                "text/plain": {},
            },
        },
        **problems(401, 403, 404, 413, 422, 429, 503),
    },
    dependencies=[Depends(rate_limited("read"))],
)
async def get_eval_run_file(
    request: Request,
    operator: Annotated[Principal, Depends(eval_runs_rate_limit)],
    run_id: Annotated[str, PathParam(min_length=1, max_length=eval_runs.MAX_RUN_ID_LENGTH)],
    name: Annotated[FileName, PathParam()],
) -> Response:
    root, item = await _record(request, run_id)
    try:
        content = eval_runs.read_file(item, root, name)
    except eval_runs.FileTooLarge as exc:
        raise PayloadTooLarge(
            f"The {name} file is {exc.size} bytes; the API serves at most "
            f"{eval_runs.MAX_FILE_BYTES}. Read it from the run directory on the host.",
            code=E.API_EVAL_RUN_FILE_TOO_LARGE,
        ) from None
    if content is None:
        raise NotFound(DETAIL_FILE_NOT_FOUND)
    return Response(
        content.body,
        media_type=content.media_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{content.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
