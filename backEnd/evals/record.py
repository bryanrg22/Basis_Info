"""The record every evaluation run leaves: the study's ledger, plus a run manifest.

A study's paid calls are written to ``llm_calls`` with their prompt, parameters, tokens and
cost, and their request and response archived (EVAL_REPORT section 40). An evaluation run
used to keep only a summary (``run.json``); the per-call records of its
:class:`~basis.llm.recorder.MemoryRecorder` were discarded, the terminal output was kept
only when someone redirected it, and nothing said which release, prompts, label files or
settings a run exercised. This module gives a run directory the same record a study has
(EVAL_REPORT section 43):

``llm_calls.jsonl``
    One line per paid call with every column of the ``llm_calls`` table
    (:func:`basis.llm.recorder.ledger_columns`, the dict the production repository writes,
    plus ``created_at``), so a row of the file and a row of the table are the same record.
``llm/<call id>.request.json`` / ``.response.json``
    The request as sent (messages, images as ref + sha256, tools, schema) and the raw
    response, built and redacted by the same functions as the production archive.
``console.log``
    Everything the run printed to stdout and stderr and logged, API-key-shaped strings
    redacted (:func:`basis.llm.errors.redact_api_keys`): one tee, so the terminal and the
    file never disagree.
``results.json`` / ``report.md``
    Every case the run recorded (:meth:`EvalRun.case`) with an outcome of exactly one of
    ``passed``, ``failed`` or ``test_error`` (the harness or an API failed, which says
    nothing about the system under test); the summary shows all three counts. An
    expected-error case records the ``BASIS-<AREA>-<nnn>`` code it set out to provoke
    (docs/ERROR_CODES.md), the one that occurred and whether they matched. The scorer
    (``evals.run --predictions``) adds its per-item outcomes to the same files.
``manifest.json``
    The run manifest: what ran (harness module, its file's sha256, the command line and
    options), on which code (git sha, dirty flag, branch, nearest release tag), with which
    components (every prompt name + version + sha256, the price table version, the rules
    table version, the embedder and its pinned revision, the cost table if one is configured,
    the models requested and the models that answered, the request parameters in effect,
    the model settings), on which data (the input files and dataset files with their
    sha256 and label version, the split, the repeats), what it cost (calls, tokens of every
    kind, dollars), how it ended (status, exit code, error), where it is filed (``tree``:
    the component and version under ``<data dir>/verification/``), plus the ``run.json``
    summary.
``run.json``
    The script's own summary, unchanged in shape (the registry and the repro scripts read it).
``scores.jsonl``
    Appended by ``evals.run --predictions DIR`` (:func:`score_entry`): the label files
    scored against, with version and sha256, and every metric, so a run directory says
    which labels its scores refer to.

The shape follows what the experiment trackers record for a run (MLflow's
``mlflow.source.git.commit`` tag and params, W&B's ``wandb-metadata.json`` with the git
commit, program, host and arguments) and the ledger's OpenTelemetry GenAI columns; a
dirty flag and the component versions are added because the question this answers is
"was this component, at this version, tested against these labels on this model, on which
release", from files alone (:mod:`evals.registry`).

The verification tree (``<data dir>/verification/<component>/<version>/<run id>/``, one
shape for every component, browsable without a tool) holds a relative symbolic link to each
run directory, filed under the component the run exercised (the prompt with the most calls
by default, or what the runner declares) and that component's version; its README explains
the layout (:data:`TREE_README_TEXT`).

Usage in a runner (``run(args)``)::

    run = record.EvalRun.begin(args.out, harness=__name__, args=args, settings=settings)
    recorder = run.recorder                  # instead of MemoryRecorder()
    run.input("photos", args.photos)          # what was read: a file or a directory
    run.dataset(data_dir / "assets_v1.json")  # a label/dataset file (version from its name)
    run.case("21_kitchen.jpg#1", "passed", kind="photo")   # one case per unit of work
    ...
    run.finish({...})                         # run.json, results.json, report.md, manifest.json

:func:`evals.account_stop.stops_on_account_error` finishes a run its body left unfinished
(an account stop or any other exception) so the manifest and the log are written whatever
happened. ``args`` is the argparse namespace the runner was given; the run attaches itself
to it for that purpose.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import socket
import subprocess
import sys
import time
import traceback
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, TextIO

from basis import __version__ as BASIS_VERSION
from basis.domain.rules import RULES_VERSION
from basis.errors.codes import E, code_in
from basis.llm import prompts
from basis.llm.cost import PRICING_VERSION
from basis.llm.errors import redact_api_keys
from basis.llm.recorder import (
    KIND_CHAT,
    STATUS_ERROR,
    CallRecord,
    LedgerEntry,
    PayloadRefs,
    ledger_columns,
    payload_bytes,
    price_entry,
    priced_payload,
    sha256_hex,
    with_refs,
)

__all__ = [
    "FORMAT",
    "LEDGER_FILE",
    "LOG_FILE",
    "MANIFEST_FILE",
    "PAYLOAD_DIR",
    "SCORES_FILE",
    "SUMMARY_FILE",
    "OUTCOMES",
    "REPORT_FILE",
    "RESULTS_FILE",
    "TREE_DIR",
    "UNVERSIONED",
    "EvalRun",
    "FileLedgerRecorder",
    "add_cases",
    "append_scores",
    "data_root",
    "environment",
    "error_code",
    "finish_pending",
    "git_state",
    "label_version",
    "ledger_row",
    "link_into_tree",
    "read_ledger",
    "read_results",
    "render_report",
    "score_entry",
    "sha256_file",
    "sha256_tree",
    "summarize_cases",
    "tree_location",
    "tree_root",
]

# The record format: bump when a file's shape changes (the registry reads every version).
FORMAT: Final = 1
MANIFEST_FILE: Final = "manifest.json"
LEDGER_FILE: Final = "llm_calls.jsonl"
PAYLOAD_DIR: Final = "llm"
LOG_FILE: Final = "console.log"
RESULTS_FILE: Final = "results.json"
REPORT_FILE: Final = "report.md"
TREE_DIR: Final = "verification"
TREE_README: Final = "README.md"
UNVERSIONED: Final = "unversioned"
PASSED: Final = "passed"
FAILED: Final = "failed"
TEST_ERROR: Final = "test_error"
OUTCOMES: Final = (PASSED, FAILED, TEST_ERROR)
SUMMARY_FILE: Final = "run.json"
SCORES_FILE: Final = "scores.jsonl"
STATUS_OK: Final = "ok"
STATUS_STOPPED: Final = "stopped"
STATUS_FAILED: Final = "error"
UNKNOWN: Final = "unknown"
_RECORD_ATTR: Final = "_eval_record"
_LABEL_VERSION: Final = re.compile(r"_(v[1-9]\d*)(?:\.[a-z]+)+$")
# Settings that describe the evaluated system (never a credential or an address).
_SETTING_FIELDS: Final = re.compile(
    r"(_model$|^model_|_effort$|_prompt|^retrieval_|^room_|^vision_|^extraction_|^surface_"
    r"|^materials_|^sam3_|^localizer_|^photo_|^embedding_|^text_|^takeoff_|^classif|_mode$"
    r"|_threshold$|_tokens$|_version$|_table_path$|_concurrency$)"
)
_SECRET_FIELDS: Final = re.compile(r"(key|token|secret|password|url|dsn|credential)", re.I)

logger = logging.getLogger("evals.record")


# --------------------------------------------------------------------------- hashing


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(path: Path) -> dict[str, Any]:
    """``{"files": n, "sha256": ...}`` of a file, or of every regular file under a
    directory (sorted relative paths and their sha256, hashed together), so two
    directories with the same contents give the same digest."""
    if path.is_file():
        return {"files": 1, "sha256": sha256_file(path)}
    files = sorted(p for p in path.rglob("*") if p.is_file() and not p.name.startswith("."))
    digest = hashlib.sha256()
    for file in files:
        digest.update(f"{file.relative_to(path).as_posix()}\0{sha256_file(file)}\n".encode())
    return {"files": len(files), "sha256": digest.hexdigest()}


def label_version(filename: str) -> str | None:
    """``v<n>`` from a versioned file name (``photo_components_v1.json`` -> ``v1``)."""
    match = _LABEL_VERSION.search(filename)
    return match.group(1) if match else None


# --------------------------------------------------------------------------- git


def _git(args: Sequence[str], cwd: Path | None) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=30
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip()


# The checkout the harness runs from (git is asked about this tree, not the process's cwd).
REPO_DIR: Final = Path(__file__).resolve().parent


def git_state(cwd: Path | None = None) -> dict[str, Any]:
    """Where the code came from: ``sha`` (full), ``short``, ``dirty`` (tracked files
    modified or staged), ``untracked`` (count), ``branch``, ``release`` (the nearest tag
    reachable from HEAD: the Basis release the run belongs to), ``commits_since_release``
    and ``describe`` (``git describe --tags --long --dirty``). ``GIT_SHA`` in the environment
    (the CI and the worker image carry it) stands in for the sha where there is no
    checkout; anything git cannot say is ``None``. ``cwd`` defaults to the harness's own
    checkout (:data:`REPO_DIR`), whatever directory the process was started from."""
    cwd = REPO_DIR if cwd is None else cwd
    sha = _git(["rev-parse", "HEAD"], cwd) or os.environ.get("GIT_SHA") or None
    status = _git(["status", "--porcelain", "--untracked-files=normal"], cwd)
    dirty: bool | None = None
    untracked: int | None = None
    if status is not None:
        lines = [line for line in status.splitlines() if line.strip()]
        untracked = sum(1 for line in lines if line.startswith("??"))
        dirty = any(not line.startswith("??") for line in lines)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    release = _git(["describe", "--tags", "--abbrev=0"], cwd) or None
    since: int | None = None
    if release:
        count = _git(["rev-list", "--count", f"{release}..HEAD"], cwd)
        since = int(count) if count and count.isdigit() else None
    return {
        "sha": sha,
        "short": sha[:7] if sha else None,
        "dirty": dirty,
        "untracked": untracked,
        "branch": None if branch in (None, "HEAD") else branch,
        "release": release,
        "commits_since_release": since,
        "describe": _git(["describe", "--tags", "--long", "--dirty", "--always"], cwd),
    }


# --------------------------------------------------------------------------- environment


def _cost_table_identity(path: str | None) -> dict[str, Any] | None:
    """The configured unit-cost table (``basis.domain.cost_table``): its name, base year and
    file sha256; ``None`` when none is configured, an error note when it cannot be read."""
    if not path:
        return None
    file = Path(path)
    if not file.is_file():
        return {"path": path, "error": "file not found"}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"path": path, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "path": path,
        "name": data.get("name") if isinstance(data, dict) else None,
        "base_year": data.get("base_year") if isinstance(data, dict) else None,
        "schema_version": data.get("schema_version") if isinstance(data, dict) else None,
        "sha256": sha256_file(file),
    }


def settings_snapshot(settings: Any) -> dict[str, Any]:
    """The settings that describe the evaluated system (model names, efforts, prompts,
    thresholds, modes), never a credential or an address."""
    if settings is None:
        return {}
    try:
        values = settings.model_dump()
    except AttributeError:
        values = dict(vars(settings))
    out: dict[str, Any] = {}
    for name in sorted(values):
        if _SECRET_FIELDS.search(name) and name != "cost_table_path":
            continue
        if not _SETTING_FIELDS.search(name):
            continue
        value = values[name]
        if hasattr(value, "get_secret_value"):
            continue
        out[name] = json.loads(json.dumps(value, default=str))
    return out


def environment(settings: Any = None) -> dict[str, Any]:
    """The component versions in effect that are not per call: Basis version, price table,
    rules table, embedder (model and pinned revision), cost table, and the host."""
    from basis.retrieval.embed import model_revision

    embedding_model = getattr(settings, "embedding_model", None)
    return {
        "basis_version": BASIS_VERSION,
        "pricing_version": PRICING_VERSION,
        "rules_version": RULES_VERSION,
        "embedder": None
        if embedding_model is None
        else {"model": embedding_model, "revision": model_revision(embedding_model)},
        "cost_table": _cost_table_identity(getattr(settings, "cost_table_path", None)),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "hostname": socket.gethostname(),
        "settings": settings_snapshot(settings),
    }


# --------------------------------------------------------------------------- ledger


def _json_default(value: object) -> Any:
    if isinstance(value, Decimal | uuid.UUID | Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, set | frozenset):
        return sorted(value)
    return str(value)


def _dumps(value: Any, **kw: Any) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False, **kw)


def _now() -> datetime:
    return datetime.now(UTC)


def ledger_row(
    record: CallRecord, *, build_sha: str | None, created_at: datetime
) -> dict[str, Any]:
    """One ``llm_calls.jsonl`` line: the table's columns and ``created_at``."""
    row = ledger_columns(record, build_sha=build_sha)
    row["created_at"] = created_at
    return row


def read_ledger(directory: Path) -> list[dict[str, Any]]:
    """The rows of a run's ``llm_calls.jsonl`` (empty when there is none)."""
    path = directory / LEDGER_FILE
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


class FileLedgerRecorder:
    """A :class:`~basis.llm.recorder.Recorder` that writes the study ledger's record into a
    run directory: one JSONL line per call (:func:`ledger_row`) and the two payload files,
    priced and redacted as production does. Keeps every record in ``records`` as
    :class:`~basis.llm.recorder.MemoryRecorder` does, so the runners read it the same way."""

    def __init__(self, out: Path, *, build_sha: str | None = None) -> None:
        self.out = out
        self.records: list[CallRecord] = []
        self._build_sha = build_sha
        self._payloads = out / PAYLOAD_DIR
        self._ledger = out / LEDGER_FILE

    def _archive(self, record: CallRecord) -> PayloadRefs:
        entry = record.entry
        refs: dict[str, str] = {}
        parts = (("request", entry.request_payload), ("response", entry.response_payload))
        for part, payload in parts:
            if payload is None:
                continue
            data = payload_bytes(priced_payload(record, part, payload))
            name = f"{record.id}.{part}.json"
            try:
                self._payloads.mkdir(parents=True, exist_ok=True)
                (self._payloads / name).write_bytes(data)
            except OSError:
                logger.error(
                    "eval ledger payload %s not stored; the row is written without it", name
                )
                continue
            refs[f"{part}_uri"] = f"{PAYLOAD_DIR}/{name}"
            refs[f"{part}_sha256"] = sha256_hex(data)
        return PayloadRefs(**refs)

    async def record(self, entry: LedgerEntry) -> CallRecord:
        cost, price_row, pricing_version = price_entry(entry)
        record = CallRecord(
            id=uuid.uuid4(),
            entry=entry,
            cost_usd=cost,
            price_row=price_row,
            pricing_version=pricing_version,
        )
        record = with_refs(record, self._archive(record))
        line = _dumps(
            ledger_row(record, build_sha=self._build_sha, created_at=_now()), sort_keys=True
        )
        with self._ledger.open("a", encoding="utf-8") as handle:
            handle.write(redact_api_keys(line) + "\n")
        self.records.append(record)
        return record

    def spent(self) -> Decimal:
        return sum((r.cost_usd for r in self.records), Decimal(0))


# --------------------------------------------------------------------------- the log


class _RedactingLog:
    """The run log: every chunk written is redacted first."""

    def __init__(self, path: Path) -> None:
        self._handle = path.open("a", encoding="utf-8", buffering=1)
        self.closed = False

    def write(self, text: str) -> int:
        if not self.closed:
            self._handle.write(redact_api_keys(text))
        return len(text)

    def flush(self) -> None:
        if not self.closed:
            self._handle.flush()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._handle.close()


class _Tee:
    """A stream that writes to the original stream and to the run log."""

    def __init__(self, stream: TextIO, log: _RedactingLog) -> None:
        self._stream = stream
        self._log = log

    def write(self, text: str) -> int:
        self._stream.write(text)
        self._log.write(text)
        return len(text)

    def flush(self) -> None:
        self._stream.flush()
        self._log.flush()

    def __getattr__(self, name: str) -> Any:  # encoding, isatty, fileno, ...
        return getattr(self._stream, name)


# --------------------------------------------------------------------------- error codes

# The catalogue code an exception stands for (docs/ERROR_CODES.md), by class name along the
# MRO so a subclass is read before its parent: what an expected-error case records as
# ``observed_error``. A message that already carries a ``[BASIS-...]`` tag wins.
_CODE_BY_EXCEPTION: Final[tuple[tuple[str, str], ...]] = (
    ("ProviderNotConfigured", E.PROVIDER_NOT_CONFIGURED_VISION),
    ("ProviderConfigError", E.PROVIDER_CONFIG),
    ("ProviderAccountError", E.PROVIDER_ACCOUNT),
    ("ProviderRequestRejected", E.PROVIDER_REQUEST_REJECTED),
    ("ProviderBadResponse", E.PROVIDER_BAD_RESPONSE),
    ("ProviderRateLimited", E.PROVIDER_UNAVAILABLE),
    ("ProviderUnavailable", E.PROVIDER_UNAVAILABLE),
    ("BudgetExceededError", E.BUDGET_PHOTO),
    ("LocalizerUnavailable", E.PROVIDER_LOCALIZER_UNAVAILABLE),
    ("LocalizerError", E.PROVIDER_LOCALIZER_FAILED),
)


def error_code(error: BaseException | str | None) -> str | None:
    """The ``BASIS-<AREA>-<nnn>`` code of an exception (or of a message), ``None`` when the
    catalogue has none for it."""
    if error is None:
        return None
    if isinstance(error, str):
        return code_in(error)
    tagged = code_in(str(error))
    if tagged:
        return tagged
    names = [cls.__name__ for cls in type(error).__mro__]
    for name in names:
        for known, code in _CODE_BY_EXCEPTION:
            if name == known:
                return code
    return None


# --------------------------------------------------------------------------- results


def _case(
    case_id: str,
    outcome: str,
    *,
    kind: str,
    detail: Any = None,
    expected_error: str | None = None,
    observed_error: BaseException | str | None = None,
    seconds: float | None = None,
) -> dict[str, Any]:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome {outcome!r} is not one of {OUTCOMES}")
    case: dict[str, Any] = {"id": case_id, "kind": kind, "outcome": outcome}
    if seconds is not None:
        case["seconds"] = round(seconds, 3)
    if detail is not None:
        case["detail"] = json.loads(_dumps(detail))
    if expected_error is not None or observed_error is not None:
        observed = error_code(observed_error)
        case["expected_error"] = expected_error
        case["observed_error"] = observed
        case["observed_message"] = (
            None if observed_error is None else redact_api_keys(str(observed_error))[:1000]
        )
        case["matched"] = expected_error is not None and observed == expected_error
    return case


def summarize_cases(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The three counts, never folded into each other, plus the expected-error tally and
    the counts per case kind."""
    counts = Counter(str(c.get("outcome")) for c in cases)
    expected = [c for c in cases if c.get("expected_error") is not None]
    by_kind: dict[str, dict[str, int]] = {}
    for case in cases:
        kind = by_kind.setdefault(str(case.get("kind", "case")), dict.fromkeys(OUTCOMES, 0))
        kind[str(case.get("outcome"))] = kind.get(str(case.get("outcome")), 0) + 1
    return {
        "total": len(cases),
        PASSED: counts.get(PASSED, 0),
        FAILED: counts.get(FAILED, 0),
        TEST_ERROR: counts.get(TEST_ERROR, 0),
        "expected_errors": {
            "cases": len(expected),
            "matched": sum(1 for c in expected if c.get("matched")),
            # another catalogued error occurred / no error (or an uncatalogued one) occurred
            "mismatched": sum(
                1 for c in expected if not c.get("matched") and c.get("observed_error")
            ),
            "not_observed": sum(
                1 for c in expected if not c.get("matched") and not c.get("observed_error")
            ),
        },
        "by_kind": dict(sorted(by_kind.items())),
    }


def summary_line(summary: Mapping[str, Any]) -> str:
    line = (
        f"results: {summary[PASSED]} passed, {summary[FAILED]} failed, "
        f"{summary[TEST_ERROR]} test errors ({summary['total']} cases)"
    )
    expected = summary.get("expected_errors") or {}
    if expected.get("cases"):
        line += (
            f"; expected errors: {expected['matched']} matched, "
            f"{expected['mismatched']} mismatched, {expected['not_observed']} not observed"
        )
    return line


def render_report(manifest: Mapping[str, Any], results: Mapping[str, Any]) -> str:
    """``report.md``: the run's identity, the summary line and one row per case."""
    summary = results.get("summary") or summarize_cases(results.get("cases") or [])
    tree = manifest.get("tree") or {}
    git = manifest.get("git") or {}
    calls = manifest.get("calls") or {}
    lines = [
        f"# {manifest.get('name', '?')}",
        "",
        f"- component: `{tree.get('component', '?')}` version `{tree.get('version', '?')}`",
        f"- harness: `{(manifest.get('harness') or {}).get('module', '?')}`",
        f"- started: {manifest.get('started_at', '?')}; status: {manifest.get('status', '?')}",
        f"- code: `{git.get('short') or '?'}`"
        + (" (dirty)" if git.get("dirty") is True else "")
        + f", release `{git.get('release') or '?'}`",
        f"- calls: {calls.get('count', '?')}, cost ${calls.get('cost_usd', 0) or 0:.4f}",
        "",
        f"**{summary_line(summary)}**",
        "",
    ]
    cases = list(results.get("cases") or [])
    if not cases:
        lines.append("No cases recorded.")
        return "\n".join(lines) + "\n"
    expected = any(c.get("expected_error") is not None for c in cases)
    header = ["case", "kind", "outcome"]
    if expected:
        header += ["expected error", "observed error", "matched"]
    header += ["detail"]
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for case in cases:
        row = [f"`{case.get('id', '?')}`", str(case.get("kind", "")), str(case.get("outcome", ""))]
        if expected:
            row += [
                str(case.get("expected_error") or "-"),
                str(case.get("observed_error") or "-"),
                "-"
                if case.get("expected_error") is None
                else ("yes" if case.get("matched") else "no"),
            ]
        detail = case.get("detail")
        if detail is None and case.get("observed_message"):
            detail = case["observed_message"]
        text = "" if detail is None else (detail if isinstance(detail, str) else _dumps(detail))
        row.append(text.replace("|", "/").replace("\n", " ")[:200])
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def read_results(directory: Path) -> dict[str, Any]:
    path = Path(directory) / RESULTS_FILE
    if not path.is_file():
        return {"summary": summarize_cases([]), "cases": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {"summary": summarize_cases([]), "cases": []}
    cases = list(data.get("cases") or [])
    return {"summary": summarize_cases(cases), "cases": cases}


def write_results(
    directory: Path, manifest: Mapping[str, Any], cases: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """``results.json`` and its ``report.md`` for ``cases``; returns the results document."""
    results = {
        "format": FORMAT,
        "kind": "results",
        "summary": summarize_cases(cases),
        "cases": list(cases),
    }
    directory = Path(directory)
    (directory / RESULTS_FILE).write_text(_dumps(results, indent=1) + "\n", encoding="utf-8")
    (directory / REPORT_FILE).write_text(render_report(manifest, results), encoding="utf-8")
    return results


def add_cases(directory: Path, cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Merge ``cases`` into a run directory's results (a later case with the same ``id``
    replaces the earlier one) and rewrite ``results.json`` and ``report.md``; the scorer
    adds its per-item outcomes to a prediction run this way."""
    directory = Path(directory)
    existing = read_results(directory)["cases"]
    merged: dict[str, dict[str, Any]] = {str(c["id"]): dict(c) for c in existing}
    for case in cases:
        merged[str(case["id"])] = dict(case)
    manifest_path = directory / MANIFEST_FILE
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError:
            manifest = {}
    manifest.setdefault("name", directory.name)
    results = write_results(directory, manifest, list(merged.values()))
    if manifest_path.is_file():
        manifest["results"] = results["summary"]
        manifest_path.write_text(_dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return results


# --------------------------------------------------------------------------- the tree


def data_root() -> Path | None:
    value = os.environ.get("BASIS_EVAL_DATA_DIR")
    return Path(value) if value else None


def tree_root(root: Path | None = None) -> Path | None:
    """``<data dir>/verification``: the tree a person browses, component / version / run."""
    root = root or data_root()
    return None if root is None else root / TREE_DIR


# The component a runner's output stands for when its calls name no prompt.
HARNESS_COMPONENTS: Final[dict[str, str]] = {
    "evals.predict_photos": "photo_analysis",
    "evals.predict_rooms": "room_inventory",
    "evals.replay_rooms": "room_inventory",
    "evals.predict_fields": "appraisal_field_fallback",
    "evals.predict_assets": "classify",
    "evals.predict_marks": "photo_marks",
    "evals.predict_points": "photo_points",
    "evals.provoke_errors": "provider_errors",
    "evals.predict_boxes": "detection",
    "evals.predict_boxes (probe)": "detection",
    "evals.predict_detector": "detection",
    "evals.sam3_select": "detection",
    "evals.manual_add": "detection",
    "evals.verify_boxes": "detection",
    "evals.merge_photo_runs": "photo_analysis",
    "evals.aggregate_runs": "photo_analysis",
    "evals.fuse_counts": "photo_counts",
    "evals.combine_rooms": "room_inventory",
    "evals.predict_surface_crops": "surface_crop_material",
    "evals.appraisal.run": "appraisal_fields",
    "docs/review_2026-10-15/repro/extraction_replay.py": "appraisal_fields",
    "live study export (repro/live_export.py)": "study_export",
    "evals.run": "scoring",
}


def tree_location(
    manifest: Mapping[str, Any], *, component: str | None = None, version: str | None = None
) -> tuple[str, str]:
    """``(component, version)`` a run files under: what the runner declared, else the prompt
    with the most calls (its name and version: the system's own names), else what the
    harness stands for (a detector, a localizer, a merge) with the version its manifest
    names, else ``unknown`` / ``unversioned``."""
    components = manifest.get("components") or {}
    prompts = [p for p in components.get("prompts") or [] if p.get("name")]
    if component is None and prompts:
        best = max(
            prompts, key=lambda p: int(p.get("calls") or 0 if p.get("calls") != UNKNOWN else 0)
        )
        component = str(best["name"])
        if version is None:
            version = str(best.get("version") or UNVERSIONED)
    if component is None:
        harness = str((manifest.get("harness") or {}).get("module") or "")
        component = HARNESS_COMPONENTS.get(harness, UNKNOWN)
        if version is None:
            detector = components.get("detector")
            localizer = components.get("localizer")
            version = str(detector or localizer or UNVERSIONED).replace("/", "_")
    if version is None:
        wanted = [p for p in prompts if p.get("name") == component]
        version = str(wanted[0].get("version") or UNVERSIONED) if wanted else UNVERSIONED
    return component, version


def ensure_tree_readme(root: Path) -> Path:
    """The verification tree's own README (written once; a person browsing the tree with
    Finder reads it first)."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / TREE_README
    if not path.exists():
        path.write_text(TREE_README_TEXT, encoding="utf-8")
    return path


TREE_README_TEXT: Final = """# Verification

Every evaluation run, filed by the component it exercised, then the version of that
component, then the run:

    verification/<component>/<version>/<run id>/
        manifest.json      what ran (harness, command, options), on which code (git sha,
                           dirty flag, release tag), with which components (every prompt
                           name/version/sha256, price and rules table versions, embedder,
                           models, request parameters, settings), on which data (inputs
                           and label files with sha256 and version), what it cost, how it
                           ended, where it is filed (tree)
        results.json       every case with an outcome of exactly one of passed, failed or
                           test_error (the harness or an API failed, not the system under
                           test); an expected-error case names the BASIS-<AREA>-<nnn> code it
                           expected, the one that occurred and whether they matched
        report.md          the same results as a page: the summary line (all three counts)
                           and one row per case
        llm_calls.jsonl    one line per paid call: every column of the llm_calls table
        llm/               <call id>.request.json (the prompt and messages as sent, images
                           as ref + sha256) and <call id>.response.json (the raw answer)
        console.log        everything the run printed and logged (API keys redacted)
        run.json           the runner's own summary
        scores.jsonl       appended by `evals.run --predictions`: the label files (version,
                           sha256) and every metric of each scoring
        ...                the runner's prediction files (photo_counts.json, room_inventory.json,
                           fields_raw.jsonl, ...)

Components are the system's own names: a released prompt (photo_countables, photo_surfaces,
room_inventory, surface_material, house_inventory, classify, appraisal_fields_grounded, ...),
`detection` (SAM 3 and the detectors), `photo_analysis` (merged per-photo runs),
`photo_counts` (count fusion), `appraisal_fields` (the appraisal harness as configured),
`provider_errors` (the expected-error runs), `scoring`. A version is the prompt, rule or
table version the run exercised (`v3`); a component without one files under `unversioned`,
a detector under its name.

A run directory here is a symbolic link to where the runner wrote (`predictions/<run id>` or
`appraisal/runs/<run id>`) unless it was written here directly; the link's target is in its
manifest (`tree.target`). Runs made before manifests existed carry a backfilled manifest
(`backfilled: true`, each field with its source, `unknown` where the files do not say) and
no results.json. `python -m evals.registry` lists and filters the tree; `backEnd/evals/README.md`
("Run records and the registry") and EVAL_REPORT section 43 describe the format.
"""


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@+-]+", "_", text) or UNKNOWN


def link_into_tree(
    out: Path, component: str, version: str, *, root: Path, run_id: str | None = None
) -> dict[str, Any]:
    """File ``out`` under ``root/<component>/<version>/<run id>`` as a relative symbolic link
    (``run_id`` defaults to the directory's name), unless ``out`` is already inside the
    tree. A stale link of the same name to the same directory is replaced; a link of the
    same name to another directory is left alone and this run takes the name plus a short
    hash of its path, so two runs never share a slot. Returns the manifest's ``tree``."""
    out = Path(out).resolve()
    root = Path(root).resolve()
    ensure_tree_readme(root)
    slot = root / _safe_name(component) / _safe_name(version)
    name = _safe_name(run_id or out.name)
    section: dict[str, Any] = {
        "component": component,
        "version": version,
        "root": str(root),
        "path": str((slot / name).relative_to(root)),
    }
    try:
        out.relative_to(root)
        section["link"] = None  # written inside the tree already
        section["path"] = str(out.relative_to(root))
        return section
    except ValueError:
        pass
    slot.mkdir(parents=True, exist_ok=True)
    link = slot / name
    if link.is_symlink():
        if link.resolve() == out:
            link.unlink()
        else:
            name = f"{name}@{hashlib.sha256(str(out).encode()).hexdigest()[:8]}"
            link = slot / name
            section["path"] = str(link.relative_to(root))
            section["renamed"] = "another run of the same name is filed in this slot"
            if link.is_symlink():
                link.unlink()
    if link.exists():
        section["link"] = None
        section["error"] = f"{link} exists and is not a link"
        return section
    link.symlink_to(os.path.relpath(out, slot), target_is_directory=True)
    section["link"] = str(link)
    section["target"] = os.path.relpath(out, slot)
    return section


# --------------------------------------------------------------------------- the run


def _options(args: Any) -> dict[str, Any]:
    """The runner's options as given (an argparse namespace or a mapping), without the
    attributes other helpers attach to it."""
    if args is None:
        return {}
    values = args if isinstance(args, Mapping) else vars(args)
    return {
        k: json.loads(_dumps(v)) for k, v in sorted(values.items()) if not str(k).startswith("_")
    }


def _module_name(module: str) -> str:
    """``__name__`` of a script run as ``python -m evals.x`` is ``__main__``; the manifest
    records the module's import name (``evals.x``)."""
    if module != "__main__":
        return module
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    name = getattr(spec, "name", None)
    return str(name) if name else module


def _module_file_sha256(module: str) -> str | None:
    found = sys.modules.get(module)
    file = getattr(found, "__file__", None)
    if not file:
        return None
    try:
        return sha256_file(Path(file))
    except OSError:
        return None


_active: EvalRun | None = None


@dataclass
class EvalRun:
    """One evaluation run being recorded (:meth:`begin` ... :meth:`finish`)."""

    out: Path
    harness: str
    recorder: FileLedgerRecorder
    started: datetime
    git: dict[str, Any]
    env: dict[str, Any]
    options: dict[str, Any]
    command: list[str]
    _t0: float
    inputs: list[dict[str, Any]] = field(default_factory=list)
    datasets: list[dict[str, Any]] = field(default_factory=list)
    declared_prompts: dict[str, dict[str, Any]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    cases: list[dict[str, Any]] = field(default_factory=list)
    component: str | None = None
    version: str | None = None
    tree: Path | None = None
    finished: bool = False
    _log: _RedactingLog | None = None
    _streams: tuple[TextIO, TextIO] | None = None
    _handler: logging.Handler | None = None

    @classmethod
    def begin(
        cls,
        out: Path,
        *,
        harness: str,
        args: Any = None,
        settings: Any = None,
        log: bool = True,
        cwd: Path | None = None,
        component: str | None = None,
        version: str | None = None,
        tree: Path | None = None,
    ) -> EvalRun:
        """Start recording into ``out`` (created): the ledger recorder is ready, stdout,
        stderr and logging are copied to ``console.log``. ``harness`` is the runner's module
        name (``__name__``); ``args`` its options, to which the run attaches itself.
        ``component`` and ``version`` say where the run files in the verification tree
        (:func:`tree_location` derives them from the prompts called when not given);
        ``tree`` is the tree's root (default ``$BASIS_EVAL_DATA_DIR/verification``; no link
        is made when neither is known)."""
        global _active
        if _active is not None and not _active.finished:
            _active._release()
        out = Path(out)
        harness = _module_name(harness)
        out.mkdir(parents=True, exist_ok=True)
        git = git_state(cwd)
        run = cls(
            out=out,
            harness=harness,
            recorder=FileLedgerRecorder(out, build_sha=git.get("sha")),
            started=_now(),
            git=git,
            env=environment(settings),
            options=_options(args),
            command=list(sys.argv),
            _t0=time.monotonic(),
            component=component,
            version=version,
            tree=tree if tree is not None else tree_root(),
        )
        if log:
            run._capture()
        if args is not None and not isinstance(args, Mapping):
            try:
                setattr(args, _RECORD_ATTR, run)
            except AttributeError:
                pass
        previous = out / MANIFEST_FILE
        if previous.is_file():
            try:
                before = json.loads(previous.read_text(encoding="utf-8"))
            except ValueError:
                before = {}
            run.extra["previous_manifest"] = {
                k: before.get(k) for k in ("id", "harness", "finished_at", "status")
            }
        _active = run
        return run

    # --- what the run read

    def input(self, name: str, path: Path | str) -> dict[str, Any]:
        """Record an input (a file or a directory of files) by name, with its digest."""
        path = Path(path)
        entry: dict[str, Any] = {"name": name, "path": str(path)}
        if path.exists():
            entry.update(sha256_tree(path))
        else:
            entry["error"] = "not found"
        self.inputs.append(entry)
        return entry

    def dataset(self, path: Path | str, *, name: str | None = None) -> dict[str, Any]:
        """Record a dataset or label file: its name, label version (from the file name),
        path and sha256."""
        path = Path(path)
        entry: dict[str, Any] = {
            "name": name or _LABEL_VERSION.sub("", path.name),
            "file": path.name,
            "version": label_version(path.name),
            "path": str(path),
            "sha256": sha256_file(path) if path.is_file() else None,
        }
        self.datasets.append(entry)
        return entry

    def prompt(self, prompt: prompts.Prompt | None) -> None:
        """Declare a released prompt the run is configured with (the calls record the ones
        used; this keeps a prompt that made no call, such as a step that was skipped)."""
        if prompt is not None:
            self.declared_prompts[prompt.version_id] = {
                "name": prompt.name,
                "version": prompt.version,
                "sha256": prompt.sha256,
            }

    def note(self, **fields: Any) -> None:
        """Extra manifest fields a runner wants kept (a scope, a mode, a source run)."""
        self.extra.update(fields)

    # --- the cases

    def case(
        self,
        case_id: str,
        outcome: str,
        *,
        kind: str = "case",
        detail: Any = None,
        expected_error: str | None = None,
        observed_error: BaseException | str | None = None,
        seconds: float | None = None,
    ) -> dict[str, Any]:
        """Record one case: ``outcome`` is exactly one of ``passed`` (the system under test
        did what the case asked), ``failed`` (it did not) or ``test_error`` (the harness or
        an API failed, so the case says nothing about the system). An expected-error case
        gives the ``BASIS-<AREA>-<nnn>`` code it set out to provoke and the exception (or
        message) that occurred; the record keeps both codes and whether they matched."""
        case = _case(
            case_id,
            outcome,
            kind=kind,
            detail=detail,
            expected_error=expected_error,
            observed_error=observed_error,
            seconds=seconds,
        )
        self.cases.append(case)
        return case

    # --- the log

    def _capture(self) -> None:
        self._log = _RedactingLog(self.out / LOG_FILE)
        self._log.write(f"# {self.harness} started {self.started.isoformat()}\n")
        self._log.write(f"# command: {_dumps(self.command)}\n")
        self._streams = (sys.stdout, sys.stderr)
        sys.stdout = _Tee(sys.stdout, self._log)
        sys.stderr = _Tee(sys.stderr, self._log)
        handler = logging.StreamHandler(self._log)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        self._handler = handler

    def _release(self) -> None:
        """Put the streams back and close the log (idempotent)."""
        global _active
        if self._handler is not None:
            logging.getLogger().removeHandler(self._handler)
            self._handler = None
        if self._streams is not None:
            out, err = self._streams
            if isinstance(sys.stdout, _Tee):
                sys.stdout = out
            if isinstance(sys.stderr, _Tee):
                sys.stderr = err
            self._streams = None
        if self._log is not None:
            self._log.close()
        if _active is self:
            _active = None

    # --- the manifest

    def _rows(self) -> list[dict[str, Any]]:
        """The ledger as it stands on disk: the rows this run wrote and any an earlier run
        into the same directory left (``predict_marks --from-raw`` redoes a step over its own
        output). The manifest describes the file; ``this_run`` says what this process added."""
        return read_ledger(self.out)

    def _calls(self) -> dict[str, Any]:
        rows = self._rows()
        chat = [r for r in rows if r.get("kind") == KIND_CHAT]

        def total(key: str) -> int:
            return sum(int(r.get(key) or 0) for r in chat)

        own = self.recorder.records
        calls: dict[str, Any] = {
            "count": len(rows),
            "by_kind": dict(sorted(Counter(str(r.get("kind")) for r in rows).items())),
            "errors": sum(1 for r in rows if r.get("status") == STATUS_ERROR),
            "cost_usd": round(float(sum(Decimal(str(r.get("cost_usd") or 0)) for r in rows)), 6),
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "cache_read_input_tokens": total("cache_read_input_tokens"),
            "cache_creation_input_tokens": total("cache_creation_input_tokens"),
            "reasoning_output_tokens": total("reasoning_output_tokens"),
            "latency_ms": sum(int(r.get("latency_ms") or 0) for r in rows),
            "ledger": LEDGER_FILE if rows else None,
            "payloads": PAYLOAD_DIR if rows else None,
        }
        if len(own) != len(rows):
            calls["this_run"] = {
                "count": len(own),
                "cost_usd": round(float(self.recorder.spent()), 6),
                "earlier_rows": len(rows) - len(own),
            }
        return calls

    def _components(self) -> dict[str, Any]:
        rows = self._rows()
        found: dict[str, dict[str, Any]] = {
            key: {**value, "calls": 0} for key, value in self.declared_prompts.items()
        }
        for r in rows:
            version_id = r.get("prompt_version")
            if not version_id:
                continue
            if version_id not in found:
                name, _, version = str(version_id).partition("/")
                found[version_id] = {
                    "name": r.get("prompt_name") or name,
                    "version": version or None,
                    "sha256": r.get("prompt_sha256"),
                    "calls": 0,
                }
            found[version_id]["calls"] += 1
            if found[version_id].get("sha256") is None:
                found[version_id]["sha256"] = r.get("prompt_sha256")
        params: dict[str, set[Any]] = {
            "reasoning_effort": set(),
            "max_output_tokens": set(),
            "temperature": set(),
            "response_format": set(),
        }
        for r in rows:
            for key, values in params.items():
                values.add(r.get(key))
        return {
            "prompts": [found[k] for k in sorted(found)],
            "models": {
                "requested": sorted(
                    {str(r.get("requested_model") or r.get("model")) for r in rows}
                ),
                "answered": sorted({str(r.get("model")) for r in rows}),
                "providers": sorted({str(r.get("provider")) for r in rows}),
            },
            "request_params": {
                k: sorted("null" if v is None else str(v) for v in vs) for k, vs in params.items()
            },
            "pricing_version": self.env.get("pricing_version"),
            "rules_version": self.env.get("rules_version"),
            "embedder": self.env.get("embedder"),
            "cost_table": self.env.get("cost_table"),
        }

    def manifest(
        self,
        summary: Mapping[str, Any] | None,
        *,
        status: str = STATUS_OK,
        exit_code: int | None = 0,
        error: str | None = None,
        finished: datetime | None = None,
    ) -> dict[str, Any]:
        finished = finished or _now()
        return {
            "format": FORMAT,
            "kind": "eval_run",
            "id": str(uuid.uuid4()),
            "name": self.out.name,
            "harness": {
                "module": self.harness,
                "sha256": _module_file_sha256(self.harness),
                "command": self.command,
                "options": self.options,
            },
            "started_at": self.started.isoformat(),
            "finished_at": finished.isoformat(),
            "wall_seconds": round(time.monotonic() - self._t0, 1),
            "status": status,
            "exit_code": exit_code,
            "error": None if error is None else redact_api_keys(error)[:4000],
            "git": self.git,
            "environment": {
                k: v for k, v in self.env.items() if k not in ("settings", "embedder", "cost_table")
            },
            "settings": self.env.get("settings", {}),
            "components": self._components(),
            "data": {
                "data_dir": os.environ.get("BASIS_EVAL_DATA_DIR"),
                "inputs": self.inputs,
                "datasets": self.datasets,
                "split": self.options.get("split"),
                "repeats": self.options.get("repeats"),
            },
            "calls": self._calls(),
            "files": {
                "log": LOG_FILE if (self.out / LOG_FILE).exists() else None,
                "summary": SUMMARY_FILE if summary is not None else None,
                "ledger": LEDGER_FILE if self.recorder.records else None,
                "results": RESULTS_FILE,
                "report": REPORT_FILE,
            },
            "results": summarize_cases(self.cases),
            **({"notes": self.extra} if self.extra else {}),
            "summary": None if summary is None else json.loads(_dumps(summary)),
        }

    def finish(
        self,
        summary: Mapping[str, Any] | None = None,
        *,
        status: str = STATUS_OK,
        exit_code: int | None = 0,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Write ``run.json`` (when a summary is given; an existing one is read otherwise),
        ``results.json`` and ``report.md`` from the cases, ``manifest.json``, file the run in
        the verification tree, stop the log, and return the manifest."""
        if summary is not None:
            (self.out / SUMMARY_FILE).write_text(_dumps(summary, indent=1) + "\n", encoding="utf-8")
        elif (self.out / SUMMARY_FILE).is_file():
            try:
                summary = json.loads((self.out / SUMMARY_FILE).read_text(encoding="utf-8"))
            except ValueError:
                summary = None
        manifest = self.manifest(summary, status=status, exit_code=exit_code, error=error)
        component, version = tree_location(manifest, component=self.component, version=self.version)
        manifest["tree"] = {"component": component, "version": version, "link": None}
        if self.tree is not None:
            try:
                manifest["tree"] = link_into_tree(self.out, component, version, root=self.tree)
            except OSError as exc:
                manifest["tree"]["error"] = f"{type(exc).__name__}: {exc}"
        results = write_results(self.out, manifest, self.cases)
        # The one summary line, on the console and in console.log alike (the tee).
        print(summary_line(results["summary"]), file=sys.stderr)
        if self._log is not None:
            self._log.write(f"# finished {manifest['finished_at']} status={status}\n")
        (self.out / MANIFEST_FILE).write_text(_dumps(manifest, indent=1) + "\n", encoding="utf-8")
        self.finished = True
        self._release()
        return manifest


def finish_pending(args: Any, *, exit_code: int | None, error: BaseException | str | None) -> None:
    """Finish the run attached to ``args`` if its body did not: status ``stopped`` for an
    account stop, ``error`` for any other exception, ``ok`` otherwise."""
    run = getattr(args, _RECORD_ATTR, None)
    if run is None or run.finished:
        return
    if error is None:
        status = STATUS_OK
        text = None
    elif isinstance(error, str):
        status = STATUS_STOPPED
        text = error
    else:
        status = STATUS_FAILED
        text = "".join(traceback.format_exception(error))
    run.finish(None, status=status, exit_code=exit_code, error=text)


# --------------------------------------------------------------------------- scores


def score_entry(
    results: Iterable[Any],
    *,
    harness: str,
    data_dir: Path | None,
    label_files: Mapping[str, str],
    split: str,
    repeats: int,
    options: Mapping[str, Any] | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """One ``scores.jsonl`` line for a scoring of a predictions directory: the label file
    of every scored dataset with its version and sha256, the git state of the scorer, the
    options, and every metric row (``results`` are ``evals.run.TaskResult``)."""
    labels: dict[str, Any] = {}
    scored: dict[str, Any] = {}
    for task in results:
        filename = label_files.get(task.dataset)
        path = None if data_dir is None or filename is None else data_dir / filename
        labels[task.dataset] = {
            "file": filename,
            "version": label_version(filename) if filename else None,
            "sha256": sha256_file(path) if path is not None and path.is_file() else None,
        }
        scored[task.dataset] = {
            "system": task.system,
            "skipped": task.skipped,
            "metrics": {
                row.metric: {
                    "value": _finite(row.value),
                    "n": row.n,
                    "stderr": _finite(row.stderr),
                    "interval": None
                    if row.interval is None
                    else [_finite(row.interval[0]), _finite(row.interval[1])],
                }
                for row in task.rows
            },
            "notes": list(task.notes),
        }
    return {
        "format": FORMAT,
        "kind": "scores",
        "scored_at": _now().isoformat(),
        "harness": {"module": harness, "command": list(sys.argv)},
        "git": git_state(cwd),
        "options": json.loads(_dumps(dict(options or {}))),
        "data": {
            "data_dir": None if data_dir is None else str(data_dir),
            "labels": labels,
            "split": split,
            "repeats": repeats,
        },
        "results": scored,
    }


def _finite(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def append_scores(directory: Path, entry: Mapping[str, Any]) -> Path:
    """Append one scoring to ``<directory>/scores.jsonl``."""
    path = Path(directory) / SCORES_FILE
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_dumps(entry, sort_keys=True) + "\n")
    return path


def read_scores(directory: Path) -> list[dict[str, Any]]:
    path = Path(directory) / SCORES_FILE
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
