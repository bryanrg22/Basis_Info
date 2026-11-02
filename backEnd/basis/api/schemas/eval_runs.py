"""``GET /v1/ops/eval-runs``: evaluation run records for platform operators
(docs/api/EVAL_RUNS.md). Every unknown is ``null``; a figure carries its source."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from basis.api.schemas.common import ResponseModel
from basis.ops.eval_runs import RunRecord

RunStatus = Literal["ok", "stopped", "error"]


class PromptUseOut(ResponseModel):
    """A prompt the run called, as its manifest records it."""

    name: str
    version: str | None = Field(description="Null when the files do not say.")
    sha256: str | None
    calls: int | None = Field(description="Calls on this version; null for a backfilled run.")


class LabelOut(ResponseModel):
    """The label file a dataset was scored against (the version is in the file's name)."""

    file: str | None = Field(description="The file's name only, never its path.")
    version: str | None
    sha256: str | None = Field(description="At scoring time; null when it was not recorded.")
    scored_at: str | None


class HeadlineOut(ResponseModel):
    """The one metric per scored dataset the registry's table shows."""

    dataset: str
    metric: str
    value: float | None


class TokensOut(ResponseModel):
    input: int | None = None
    output: int | None = None
    cache_read: int | None = None
    cache_creation: int | None = None
    reasoning: int | None = None


class SpendOut(ResponseModel):
    """What the run cost, with the source of each figure."""

    cost_usd: float | None
    cost_source: Literal["ledger", "summary"] | None = Field(
        description="`ledger`: summed from the run's recorded, priced calls (measured by the "
        "price table of the day); `summary`: the runner's own run.json figure, what a "
        "backfilled manifest copies; null with no cost."
    )
    calls: int | None
    tokens: TokensOut | None = Field(
        description="From the recorded calls (chat kind); null when the record has no totals."
    )
    duration_seconds: float | None
    duration_source: Literal["recorded", "file_times"] | None = Field(
        description="`recorded`: the runner's clock at finish; `file_times`: the backfill's "
        "estimate from the directory's file birth and modification times, which can span days."
    )


class GitOut(ResponseModel):
    sha: str | None = Field(description="The tested commit; null for an old run (not recoverable).")
    dirty: bool | None = Field(description="Tracked files modified at run time; null unknown.")
    release: str | None = Field(description="The nearest release tag reachable at run time.")


class RecordOut(ResponseModel):
    """How the record came to be."""

    manifest: bool = Field(description="A manifest.json exists.")
    backfilled: bool = Field(
        description="The manifest was reconstructed from the directory's files after the run "
        "(`evals.backfill_manifests`): its fields are what the files said, `unknown` where "
        "they did not."
    )
    format: int | None
    error: str | None = Field(
        description="Why the registry could not read the directory (a malformed manifest); "
        "the other fields are then null."
    )


class SelectionOut(ResponseModel):
    """The run's standing against the pipeline this API process is configured for."""

    status: Literal["current", "alternative", "not_in_pipeline", "unknown"]
    reasons: list[str]


class FilesOut(ResponseModel):
    """Download paths (relative to the API origin), served with the operator check; null when
    the run has no such file."""

    manifest: str | None
    report: str | None
    ledger: str | None
    console: str | None


class EvalRunOut(ResponseModel):
    run_id: str = Field(description="Opaque; the path parameter of the detail and file routes.")
    name: str
    path: str = Field(description="The run directory, relative to the evaluation data directory.")
    kind: Literal["predictions", "appraisal"]
    component: str | None = Field(
        description="Where the run files in the verification tree (`photo_countables`, "
        "`detection`, ...)."
    )
    version: str | None
    harness: str | None = Field(description="The runner module, e.g. `evals.predict_photos`.")
    started_at: str | None = Field(description="As recorded (ISO 8601 with offset).")
    finished_at: str | None
    status: RunStatus | None
    results: dict[str, int] | None = Field(
        description="`passed`, `failed`, `test_error` case counts from results.json; null "
        "when the run recorded no cases."
    )
    git: GitOut
    prompts: list[PromptUseOut]
    components: list[str] = Field(
        description="Every component id the run exercised: prompt version ids, `detector:x`, "
        "`embedder:x`."
    )
    models_requested: list[str]
    models_answered: list[str]
    reasoning_effort: list[str]
    labels: dict[str, LabelOut]
    split: str | None
    repeats: int | None
    scores: dict[str, dict[str, float | None]] = Field(
        description="Measured metrics per dataset (the latest scoring of the run's own "
        "predictions). Runs scored against different labels or splits are not comparable."
    )
    headline: list[HeadlineOut]
    spend: SpendOut
    record: RecordOut
    selection: SelectionOut
    report_sections: list[int] = Field(description="EVAL_REPORT sections that cite the run.")
    files: FilesOut

    @classmethod
    def of(cls, item: RunRecord, files_path: str) -> EvalRunOut:
        return cls.model_validate(_fields(item, files_path))


class EvalRunDetailOut(EvalRunOut):
    settings: dict[str, object] | None = Field(
        description="The evaluated system's settings as the manifest recorded them (model, "
        "effort, prompt, threshold and version fields; never a credential or an address)."
    )
    environment: dict[str, object] | None = Field(
        description="Basis version, price table, rules table, embedder, cost table; the host "
        "name is withheld."
    )
    options: dict[str, object] | None = Field(description="The harness's command-line options.")
    sources: dict[str, object] | None = Field(
        description="A backfilled manifest's provenance: where each field came from."
    )
    notes: dict[str, object] | None
    error: str | None = Field(description="The run's error text, redacted, when it failed.")

    @classmethod
    def of(cls, item: RunRecord, files_path: str) -> EvalRunDetailOut:
        return cls.model_validate(
            {
                **_fields(item, files_path),
                "settings": item.settings,
                "environment": item.environment,
                "options": item.options,
                "sources": item.sources,
                "notes": item.notes,
                "error": item.error,
            }
        )


class EvalRunPageOut(ResponseModel):
    items: list[EvalRunOut]
    next_cursor: str | None = Field(
        description="Pass as `cursor` for the next (older) page; null on the last page."
    )
    total: int = Field(description="Runs matching the filters, across every page.")


def _fields(item: RunRecord, files_path: str) -> dict[str, object]:
    return {
        "run_id": item.run_id,
        "name": item.name,
        "path": item.path,
        "kind": item.kind,
        "component": item.component,
        "version": item.version,
        "harness": item.harness,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
        "status": item.status if item.status in ("ok", "stopped", "error") else None,
        "results": item.results,
        "git": {"sha": item.git_sha, "dirty": item.git_dirty, "release": item.release},
        "prompts": item.prompts,
        "components": item.components,
        "models_requested": item.models_requested,
        "models_answered": item.models_answered,
        "reasoning_effort": item.reasoning_effort,
        "labels": item.labels,
        "split": item.split,
        "repeats": item.repeats,
        "scores": item.scores,
        "headline": item.headline,
        "spend": {
            "cost_usd": item.cost_usd,
            "cost_source": item.cost_source,
            "calls": item.calls,
            "tokens": item.tokens,
            "duration_seconds": item.duration_seconds,
            "duration_source": item.duration_source,
        },
        "record": {
            "manifest": item.manifest,
            "backfilled": item.backfilled,
            "format": item.format,
            "error": item.record_error,
        },
        "selection": {"status": item.selection.status, "reasons": item.selection.reasons},
        "report_sections": item.report_sections,
        "files": {
            name: f"{files_path}/{item.run_id}/files/{name}" if present else None
            for name, present in item.files.items()
        },
    }
