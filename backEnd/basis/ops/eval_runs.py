"""Evaluation run records for platform operators (``GET /v1/ops/eval-runs``,
docs/api/EVAL_RUNS.md, EVAL_REPORT section 74).

The registry (:mod:`evals.registry`) answers, from the run directories alone, which runs
exercised which component on which model against which labels, and what they scored, cost
and when. This module is the API's read of it, nothing more: the registry scans and reads
(``run_directories``, ``tree_directories``, ``read_run``), this module

* says plainly when there is nothing to read (:func:`data_root`: no ``BASIS_EVAL_DATA_DIR``
  on this host, a directory that does not exist, or the evaluation tooling not installed in
  this image), so an empty host is never shown as "no runs";
* reads every run directory once per :data:`CACHE_TTL_S` per process, off the event loop
  (:class:`RunCatalogue`), and keeps a directory the registry cannot read as a record with
  its error instead of failing the whole list;
* projects a :class:`evals.registry.RunEntry` into a :class:`RunRecord`, the shape the API
  returns: every unknown stays ``None`` (a backfilled manifest's ``"unknown"`` included), a
  figure says where it came from (a cost summed from the recorded calls or copied from the
  runner's summary; a duration the runner clocked or the backfill estimated from file times;
  a manifest written at run time or backfilled afterwards), and a run's standing against the
  pipeline this API process is configured for is computed, never typed (:func:`selection`);
* names a run by an opaque, reversible id (:func:`run_id`: the directory's path relative to
  the data directory, base64url), so the API never takes a file system path;
* serves the four record files (:data:`FILES`: the manifest, the report, the ledger and the
  console log) redacted and bounded (:func:`read_file`): API-key-shaped strings and
  credential-named fields masked, the data directory and any home directory replaced in
  every path, the host name masked, at most :data:`MAX_FILE_BYTES`. The payload files
  (``llm/*.json``: prompts and model answers) are never served.

Everything here is read-only and free: no model call, no database.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import math
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from basis.activities.analyze_photo import fusion_config
from basis.classify import prompt as classify_prompt
from basis.config import Settings
from basis.extraction import fallback as extraction_fallback
from basis.extraction import grounded as extraction_grounded
from basis.extraction import pipeline as extraction_pipeline
from basis.llm import prompts
from basis.llm.errors import redact_api_keys
from basis.obs.logging import REDACTED, SENSITIVE_NAME
from basis.vision import analyze, multiview, reconcile, room_step, surface_crops, surface_material

try:
    from evals import registry
except ImportError:  # the production image ships basis/ alone (backEnd/Dockerfile)
    _TOOLING = False
else:
    _TOOLING = True
TOOLING_INSTALLED: Final = _TOOLING

__all__ = [
    "CACHE_TTL_S",
    "FILES",
    "MAX_FILE_BYTES",
    "FileContent",
    "FileName",
    "Filters",
    "PromptUse",
    "RunCatalogue",
    "RunRecord",
    "Selection",
    "Unavailable",
    "data_root",
    "decode_cursor",
    "decode_run_id",
    "encode_cursor",
    "matches",
    "page",
    "pipeline_prompts",
    "read_file",
    "redact_text",
    "redact_value",
    "run_id",
    "scan",
    "selection",
]

logger = logging.getLogger(__name__)

CACHE_TTL_S: Final = 30.0
MAX_FILE_BYTES: Final = 8 * 1024 * 1024
MAX_RUN_ID_LENGTH: Final = 512
MAX_CURSOR_LENGTH: Final = 1024
UNKNOWN: Final = "unknown"
DATA_DIR_PLACEHOLDER: Final = "<data dir>"
HOME_PLACEHOLDER: Final = "~"
# Fields a record file never gives out even to an operator: the machine's name.
_PRIVATE_KEYS: Final = frozenset({"hostname"})
_HOME: Final = re.compile(r"(?<![\w/])/(?:Users|home)/[^/\s\"'`,;:)\]}>]+")

FileName = Literal["manifest", "report", "ledger", "console"]
SelectionStatus = Literal["current", "alternative", "not_in_pipeline", "unknown"]
# ``name -> (manifest ``files`` key, default file name, media type, the download's suffix)``.
FILES: Final[dict[str, tuple[str | None, str, str, str]]] = {
    "manifest": (None, "manifest.json", "application/json", "manifest.json"),
    "report": ("report", "report.md", "text/markdown; charset=utf-8", "report.md"),
    "ledger": ("ledger", "llm_calls.jsonl", "application/x-ndjson", "llm_calls.jsonl"),
    "console": ("log", "console.log", "text/plain; charset=utf-8", "console.log"),
}


# --------------------------------------------------------------------------- availability


@dataclass(frozen=True)
class Unavailable:
    """Why this host has no run records to list; the problem detail."""

    detail: str


def data_root(settings: Settings) -> Path | Unavailable:
    """The evaluation data directory, or why there is none here."""
    if not TOOLING_INSTALLED:
        return Unavailable(
            "The evaluation tooling (the evals package) is not installed in this image; run "
            "records are read on the machine that holds the evaluation data."
        )
    root = settings.eval_data_dir
    if root is None:
        return Unavailable(
            "No evaluation data on this host: BASIS_EVAL_DATA_DIR is not set. Run records "
            "live with the private evaluation data, never in a deployment."
        )
    if not root.is_dir():
        return Unavailable(
            "No evaluation data on this host: BASIS_EVAL_DATA_DIR names a directory that does "
            "not exist."
        )
    return root


# --------------------------------------------------------------------------- ids


def run_id(path: str) -> str:
    """The opaque id of a run: its directory's path relative to the data directory, as
    base64url without padding (reversible, never a file system path on the wire)."""
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")


def decode_run_id(value: str) -> str | None:
    """The relative path a run id names, or ``None`` when the id is not one this module
    issued: not base64url, not UTF-8, absolute, empty, or stepping out of the directory. A
    decoded path is still only ever looked up in the catalogue, never opened on its own."""
    if not value or len(value) > MAX_RUN_ID_LENGTH:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        path = raw.decode("utf-8")
    except (binascii.Error, ValueError):
        return None
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or any(part in ("", ".", "..") for part in parts)
    ):
        return None
    return path


# --------------------------------------------------------------------------- the pipeline


@dataclass(frozen=True)
class PromptUse:
    """One prompt the configured pipeline runs: its version and the setting-named model."""

    name: str
    version: str
    setting: str
    model: str


def pipeline_prompts(settings: Settings) -> list[PromptUse]:
    """Every prompt the pipeline runs under ``settings``, with its version and the model the
    setting names: what the activities load (``basis.vision.analyze.prompts_for_mode``,
    ``room_step.load_config``, the surface crops, the grounded reading and the fallback,
    the classifier). The same switches as :func:`basis.worker.main.pipeline_models`, which
    lists the models; ``tests/unit/ops/test_eval_runs.py`` holds the two to the same
    settings."""
    uses: list[PromptUse] = []
    if settings.vision_photo_mode == "decomposed":
        uses.append(
            PromptUse(
                analyze.COUNTABLES_PROMPT_NAME,
                analyze.COUNTABLES_PROMPT_VERSION,
                "VISION_MODEL",
                settings.vision_model,
            )
        )
        uses.append(
            PromptUse(
                analyze.SURFACES_PROMPT_NAME,
                analyze.SURFACES_PROMPT_VERSION,
                "VISION_SURFACES_MODEL",
                settings.vision_surfaces_model,
            )
        )
    else:
        uses.append(
            PromptUse(
                analyze.PROMPT_NAME, analyze.PROMPT_VERSION, "VISION_MODEL", settings.vision_model
            )
        )
    fusion = fusion_config(settings)
    if fusion is not None:
        # The fusion call is a second countables call on its own model (EVAL_REPORT 15).
        uses.append(
            PromptUse(
                analyze.COUNTABLES_PROMPT_NAME,
                analyze.COUNTABLES_PROMPT_VERSION,
                "VISION_FUSION_MODEL",
                fusion.model,
            )
        )
    if settings.room_reconcile:
        uses.append(
            PromptUse(
                reconcile.PROMPT_NAME,
                settings.room_prompt_version,
                "ROOM_MODEL",
                settings.room_model,
            )
        )
        if settings.room_materials != "none":
            look = (
                ("ROOM_MATERIALS_MODEL", settings.room_materials_model)
                if settings.room_materials_model is not None
                else ("ROOM_MODEL", settings.room_model)
            )
            uses.append(
                PromptUse(surface_material.PROMPT_NAME, room_step.MATERIALS_PROMPT_VERSION, *look)
            )
        if settings.room_step_mode == "room_call":
            uses.append(
                PromptUse(
                    multiview.PROMPTS["room"],
                    room_step.ROOM_CALL_PROMPT_VERSION,
                    "ROOM_CALL_MODEL",
                    settings.room_call_model,
                )
            )
        if settings.materials_second_look == "surface_crops":
            uses.append(
                PromptUse(
                    surface_crops.PROMPT_NAME,
                    surface_crops.PROMPT_VERSION,
                    "SURFACE_CROP_MODEL",
                    settings.surface_crop_model,
                )
            )
    if settings.extraction_method == "grounded":
        uses.append(
            PromptUse(
                extraction_grounded.PROMPT,
                prompts.latest(extraction_grounded.PROMPT).version,
                "EXTRACTION_GROUNDED_MODEL",
                settings.extraction_grounded_model,
            )
        )
    for name in (extraction_fallback.PROMPT_NAME, extraction_pipeline.UAD36_PROMPT_NAME):
        uses.append(
            PromptUse(name, prompts.latest(name).version, "TEXT_MODEL", settings.text_model)
        )
    uses.append(
        PromptUse(
            classify_prompt.PROMPT_NAME,
            classify_prompt.PROMPT_VERSION_ID,
            "CLASSIFY_MODEL",
            settings.classify_model,
        )
    )
    return uses


@dataclass(frozen=True)
class Selection:
    """A run's standing against the configured pipeline: ``current`` when every prompt it
    names is the version the pipeline runs, on a model a pipeline setting names for that
    prompt; ``alternative`` when a version, a model or a prompt differs; ``not_in_pipeline``
    when none of its prompts is run by the pipeline; ``unknown`` when the run names no
    prompt (a detector, an export, a backfilled run whose files did not say). Computed from
    this API process's settings, which are the deployment's; the worker's effective routes
    are on ``GET /v1/ops/health``."""

    status: SelectionStatus
    reasons: list[str]


def selection(
    prompts_used: Sequence[Mapping[str, Any]],
    models_requested: Sequence[str],
    pipeline: Sequence[PromptUse],
) -> Selection:
    named = [
        (str(p["name"]), None if p.get("version") in (None, UNKNOWN) else str(p["version"]))
        for p in prompts_used
        if p.get("name") not in (None, UNKNOWN)
    ]
    if not named:
        return Selection("unknown", ["the run names no prompt"])
    by_name: dict[str, list[PromptUse]] = {}
    for use in pipeline:
        by_name.setdefault(use.name, []).append(use)
    in_pipeline = [(name, version) for name, version in named if name in by_name]
    outside = sorted({name for name, _ in named if name not in by_name})
    if not in_pipeline:
        return Selection(
            "not_in_pipeline", [f"{name}: the pipeline runs no such prompt" for name in outside]
        )
    reasons: list[str] = []
    status: SelectionStatus = "current"
    for name in outside:
        status = "alternative"
        reasons.append(f"{name}: the pipeline runs no such prompt")
    requested = set(models_requested)
    for name, version in in_pipeline:
        uses = by_name[name]
        versions = sorted({u.version for u in uses})
        if version is None:
            if status == "current":
                status = "unknown"
            reasons.append(f"{name}: the run's version is not recorded")
        elif version not in versions:
            status = "alternative"
            reasons.append(
                f"{name}: the run used {version}, the pipeline runs {', '.join(versions)}"
            )
        settings_of = {u.model: u.setting for u in uses}
        if not requested:
            if status == "current":
                status = "unknown"
            reasons.append(f"{name}: the run's model is not recorded")
        elif not (requested & set(settings_of)):
            status = "alternative"
            named_models = ", ".join(f"{m} ({s})" for m, s in sorted(settings_of.items()))
            reasons.append(
                f"{name}: the run asked {', '.join(sorted(requested))}, the pipeline runs "
                f"{named_models}"
            )
        elif version in versions:
            model = sorted(requested & set(settings_of))[0]
            reasons.append(f"{name} {version} on {model} is the pipeline's ({settings_of[model]})")
    return Selection(status, reasons)


# --------------------------------------------------------------------------- redaction


def _path_rules(root: Path) -> list[tuple[str, str]]:
    rules = [(str(root), DATA_DIR_PLACEHOLDER)]
    try:
        resolved = str(root.resolve())
    except OSError:
        resolved = str(root)
    if resolved != str(root):
        rules.append((resolved, DATA_DIR_PLACEHOLDER))
    return sorted(rules, key=lambda r: -len(r[0]))


def redact_text(text: str, root: Path) -> str:
    """``text`` with API keys and credential headers masked, the data directory (as given
    and resolved) replaced by ``<data dir>`` and any ``/Users/<name>`` or ``/home/<name>``
    prefix by ``~``."""
    out = redact_api_keys(text)
    for prefix, placeholder in _path_rules(root):
        out = out.replace(prefix, placeholder)
    return _HOME.sub(HOME_PLACEHOLDER, out)


def redact_value(value: Any, root: Path) -> Any:
    """A copy of a JSON value with credential-named keys masked (the logging rule:
    key, token, secret, password, authorization), the host name masked, and every string
    passed through :func:`redact_text`. Non-finite numbers become ``None`` (JSON has none)."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, inner in value.items():
            name = str(key)
            if name in _PRIVATE_KEYS or SENSITIVE_NAME.search(name):
                out[name] = REDACTED
            else:
                out[name] = redact_value(inner, root)
        return out
    if isinstance(value, list | tuple):
        return [redact_value(v, root) for v in value]
    if isinstance(value, str):
        return redact_text(value, root)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


# --------------------------------------------------------------------------- the record


@dataclass(frozen=True)
class RunRecord:
    """One run as the API returns it. ``entry`` is the registry's reading (None for a
    directory the registry could not read); ``directory`` never leaves the process."""

    run_id: str
    name: str
    path: str
    kind: str
    directory: Path = field(repr=False, compare=False)
    entry: Any = field(repr=False, compare=False)  # registry.RunEntry | None
    component: str | None = None
    version: str | None = None
    harness: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    status: str | None = None
    exit_code: int | None = None
    results: dict[str, int] | None = None
    git_sha: str | None = None
    git_dirty: bool | None = None
    release: str | None = None
    prompts: list[dict[str, Any]] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    models_requested: list[str] = field(default_factory=list)
    models_answered: list[str] = field(default_factory=list)
    reasoning_effort: list[str] = field(default_factory=list)
    labels: dict[str, dict[str, Any]] = field(default_factory=dict)
    split: str | None = None
    repeats: int | None = None
    scores: dict[str, dict[str, float | None]] = field(default_factory=dict)
    headline: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float | None = None
    cost_source: str | None = None
    calls: int | None = None
    tokens: dict[str, int] | None = None
    duration_seconds: float | None = None
    duration_source: str | None = None
    manifest: bool = False
    backfilled: bool = False
    format: int | None = None
    record_error: str | None = None
    selection: Selection = field(default_factory=lambda: Selection("unknown", []))
    report_sections: list[int] = field(default_factory=list)
    files: dict[str, bool] = field(default_factory=dict)
    # Detail only.
    settings: dict[str, Any] | None = None
    environment: dict[str, Any] | None = None
    options: dict[str, Any] | None = None
    sources: dict[str, Any] | None = None
    notes: dict[str, Any] | None = None
    error: str | None = None

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.started_at or "", self.name, self.path)


def _known(value: Any) -> Any:
    return None if value == UNKNOWN else value


def _known_deep(value: Any) -> Any:
    """``value`` with every ``"unknown"`` a backfilled manifest wrote read as ``None``."""
    if isinstance(value, Mapping):
        return {str(k): _known_deep(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_known_deep(v) for v in value]
    return _known(value)


def _label(value: Mapping[str, Any], root: Path) -> dict[str, Any]:
    """A label entry without its path (a backfilled manifest records the absolute one)."""
    out = {
        "file": None if _known(value.get("file")) is None else str(value["file"]),
        "version": None if _known(value.get("version")) is None else str(value["version"]),
        "sha256": None if _known(value.get("sha256")) is None else str(value["sha256"]),
        "scored_at": None if _known(value.get("scored_at")) is None else str(value["scored_at"]),
    }
    if out["file"] is not None:
        out["file"] = Path(redact_text(out["file"], root)).name
    return out


def _file_present(directory: Path, root: Path, entry: Any, name: str) -> Path | None:
    """The file ``name`` stands for in this run directory: the manifest's own ``files``
    entry when it names a file in the directory, else the default name, else (for the
    report) the older ``eval.md``; a plain file inside the data directory, never a link out
    of it."""
    key, default, _, _ = FILES[name]
    candidates: list[str] = []
    recorded = None if entry is None or key is None else entry.files.get(key)
    if isinstance(recorded, str) and recorded and "/" not in recorded and "\\" not in recorded:
        candidates.append(recorded)
    candidates.append(default)
    if name == "report":
        candidates.append("eval.md")
    for candidate in candidates:
        path = directory / candidate
        if not path.is_file():
            continue
        try:
            if not path.resolve().is_relative_to(root.resolve()):
                continue
        except OSError:
            continue
        return path
    return None


def _headline(scores: Mapping[str, Mapping[str, float | None]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for dataset, metrics in sorted(scores.items()):
        if not metrics:
            continue
        metric = registry.HEADLINE_METRICS.get(dataset)
        if metric is None or metric not in metrics:
            metric = next(iter(metrics))
        out.append({"dataset": dataset, "metric": metric, "value": metrics[metric]})
    return out


def project(entry: Any, root: Path, pipeline: Sequence[PromptUse]) -> RunRecord:
    """The API's record of a registry entry."""
    directory = root / entry.path
    scores = {
        dataset: {metric: _finite(value) for metric, value in metrics.items()}
        for dataset, metrics in entry.scores.items()
    }
    labels = {str(dataset): _label(value, root) for dataset, value in entry.labels.items()}
    git_dirty = entry.git_dirty if isinstance(entry.git_dirty, bool) else None
    prompts_used = [
        {
            "name": str(p.get("name")),
            "version": None if _known(p.get("version")) is None else str(p["version"]),
            "sha256": None if _known(p.get("sha256")) is None else str(p["sha256"]),
            "calls": p["calls"]
            if isinstance(p.get("calls"), int) and not isinstance(p.get("calls"), bool)
            else None,
        }
        for p in entry.prompts
        if p.get("name") not in (None, UNKNOWN)
    ]
    settings_value = _known_deep(redact_value(entry.settings, root)) if entry.settings else None
    record_error = None
    if not entry.manifest and (directory / FILES["manifest"][1]).is_file():
        # The registry reads an unparsable manifest as none; the operator should know.
        record_error = "manifest.json is not valid JSON; the run is read from its other files"
    environment = (
        {
            k: _known_deep(redact_value(v, root))
            for k, v in entry.environment.items()
            if k not in _PRIVATE_KEYS
        }
        if entry.environment
        else None
    )
    return RunRecord(
        run_id=run_id(entry.path),
        name=entry.name,
        path=entry.path,
        kind=entry.kind,
        directory=directory,
        entry=entry,
        component=_known(entry.component),
        version=_known(entry.version),
        harness=_known(entry.harness),
        started_at=_known(entry.started_at),
        finished_at=_known(entry.finished_at),
        status=_known(entry.status),
        exit_code=entry.exit_code,
        results=dict(entry.results) if entry.results else None,
        git_sha=_known(entry.git_sha),
        git_dirty=git_dirty,
        release=_known(entry.release),
        prompts=prompts_used,
        components=list(entry.components),
        models_requested=[m for m in entry.models_requested if m != UNKNOWN],
        models_answered=[m for m in entry.models_answered if m != UNKNOWN],
        reasoning_effort=[e for e in entry.reasoning_effort if e != UNKNOWN],
        labels=labels,
        split=entry.split,
        repeats=entry.repeats,
        scores=scores,
        headline=_headline(scores),
        cost_usd=_finite(entry.cost_usd),
        cost_source=entry.cost_source if _finite(entry.cost_usd) is not None else None,
        calls=entry.calls
        if isinstance(entry.calls, int) and not isinstance(entry.calls, bool)
        else None,
        tokens=dict(entry.tokens) if entry.tokens else None,
        duration_seconds=_finite(entry.wall_seconds),
        duration_source=entry.duration_source if _finite(entry.wall_seconds) is not None else None,
        manifest=entry.manifest,
        backfilled=entry.backfilled,
        format=entry.format,
        record_error=record_error,
        selection=selection(
            prompts_used, [m for m in entry.models_requested if m != UNKNOWN], pipeline
        ),
        report_sections=[s for s in entry.report_sections if isinstance(s, int)],
        files={name: _file_present(directory, root, entry, name) is not None for name in FILES},
        settings=settings_value,
        environment=environment,
        options=_known_deep(redact_value(entry.options, root)) if entry.options else None,
        sources=redact_value(entry.sources, root) if entry.sources else None,
        notes=_known_deep(redact_value(entry.notes, root)) if entry.notes else None,
        error=None if entry.error is None else redact_text(str(entry.error), root)[:4000],
    )


def broken(directory: Path, root: Path, exc: BaseException) -> RunRecord:
    """A run directory the registry could not read: listed, with the error, so a malformed
    manifest is a fact on the page rather than a missing row."""
    path = str(directory.relative_to(root))
    kind = "appraisal" if "appraisal" in directory.relative_to(root).parts else "predictions"
    return RunRecord(
        run_id=run_id(path),
        name=directory.name,
        path=path,
        kind=kind,
        directory=directory,
        entry=None,
        manifest=(directory / "manifest.json").is_file(),
        record_error=redact_text(f"{type(exc).__name__}: {exc}", root)[:500],
        selection=Selection("unknown", ["the record could not be read"]),
        files={name: _file_present(directory, root, None, name) is not None for name in FILES},
    )


def scan(root: Path, pipeline: Sequence[PromptUse]) -> list[RunRecord]:
    """Every run directory under ``root`` as the API returns it, newest first; a directory
    the registry cannot read is a :func:`broken` record. Synchronous: call it off the loop."""
    records: list[RunRecord] = []
    for directory in registry.run_directories(root) + registry.tree_directories(root):
        try:
            records.append(project(registry.read_run(directory, root), root, pipeline))
        except Exception as exc:  # noqa: BLE001 - one bad directory must not hide the rest
            logger.warning(
                "eval run directory could not be read",
                extra={"run_dir": str(directory.relative_to(root)), "error": type(exc).__name__},
            )
            records.append(broken(directory, root, exc))
    records.sort(key=lambda r: r.sort_key, reverse=True)
    return records


class RunCatalogue:
    """The scanned records for :data:`CACHE_TTL_S`; one scan at a time, off the event loop."""

    def __init__(
        self, ttl_s: float = CACHE_TTL_S, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = asyncio.Lock()
        self._value: tuple[float, Path, list[RunRecord]] | None = None
        self.scans = 0

    async def records(self, root: Path, pipeline: Sequence[PromptUse]) -> list[RunRecord]:
        async with self._lock:
            if (
                self._value is not None
                and self._value[1] == root
                and self._clock() - self._value[0] < self._ttl_s
            ):
                return self._value[2]
            records = await asyncio.to_thread(scan, root, pipeline)
            self.scans += 1
            self._value = (self._clock(), root, records)
            return records

    async def find(self, root: Path, pipeline: Sequence[PromptUse], path: str) -> RunRecord | None:
        for item in await self.records(root, pipeline):
            if item.path == path:
                return item
        return None


# --------------------------------------------------------------------------- filters, pages


@dataclass(frozen=True)
class Filters:
    """The list's filters, all AND; ``components`` is OR within itself (a step lists several
    components). Each matches as ``python -m evals.registry`` does."""

    components: tuple[str, ...] = ()
    model: str | None = None
    labels: str | None = None
    dataset: str | None = None
    split: str | None = None
    status: str | None = None
    release: str | None = None

    @property
    def empty(self) -> bool:
        return self == Filters()


def matches(item: RunRecord, filters: Filters) -> bool:
    if filters.empty:
        return True
    if item.entry is None:
        return False  # a broken record names no component, model or label
    if filters.components and not any(
        registry.filter_runs([item.entry], component=c) for c in filters.components
    ):
        return False
    if filters.split and item.split != filters.split:
        return False
    return bool(
        registry.filter_runs(
            [item.entry],
            model=filters.model,
            labels=filters.labels,
            dataset=filters.dataset,
            release=filters.release,
            status=filters.status,
        )
    )


def encode_cursor(item: RunRecord) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps(list(item.sort_key)).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )


def decode_cursor(value: str) -> tuple[str, str, str] | None:
    """The sort key a cursor names, or ``None`` when it is not one this module issued."""
    if not value or len(value) > MAX_CURSOR_LENGTH:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        key = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError):
        return None
    if not (isinstance(key, list) and len(key) == 3 and all(isinstance(k, str) for k in key)):
        return None
    return (key[0], key[1], key[2])


def page(
    items: Iterable[RunRecord], *, after: tuple[str, str, str] | None, limit: int
) -> tuple[list[RunRecord], str | None]:
    """The next ``limit`` items after the cursor's key, newest first, and the cursor of the
    following page (None on the last)."""
    ordered = [i for i in items if after is None or i.sort_key < after]
    chosen = ordered[:limit]
    return chosen, encode_cursor(chosen[-1]) if len(ordered) > limit and chosen else None


# --------------------------------------------------------------------------- files


@dataclass(frozen=True)
class FileContent:
    body: bytes
    media_type: str
    filename: str


class FileTooLarge(Exception):
    def __init__(self, size: int) -> None:
        super().__init__(f"{size} bytes")
        self.size = size


def read_file(item: RunRecord, root: Path, name: str) -> FileContent | None:
    """The record file ``name`` of a run, redacted; ``None`` when the run has none;
    :class:`FileTooLarge` over :data:`MAX_FILE_BYTES`."""
    path = _file_present(item.directory, root, item.entry, name)
    if path is None:
        return None
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise FileTooLarge(size)
    text = path.read_bytes().decode("utf-8", errors="replace")
    _, _, media_type, suffix = FILES[name]
    if name == "manifest":
        try:
            document = json.loads(text)
        except ValueError:
            body = redact_text(text, root)  # a malformed manifest is served as it is, redacted
        else:
            body = json.dumps(redact_value(document, root), indent=1, ensure_ascii=False) + "\n"
    elif name == "ledger":
        lines: list[str] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                lines.append(redact_text(line, root))
            else:
                lines.append(json.dumps(redact_value(row, root), ensure_ascii=False))
        body = "\n".join(lines) + ("\n" if lines else "")
    else:
        body = redact_text(text, root)
    return FileContent(body.encode("utf-8"), media_type, f"{item.name}.{suffix}")
