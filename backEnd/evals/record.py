"""The record every evaluation run leaves: the study's ledger, plus a run manifest.

A study's paid calls are written to ``llm_calls`` with their prompt, parameters, tokens and
cost, and their request and response archived (EVAL_REPORT section 40). An evaluation run
used to keep only a summary (``run.json``); the per-call records of its
:class:`~basis.llm.recorder.MemoryRecorder` were discarded, the terminal output was kept
only when someone redirected it, and nothing said which release, prompts, label files or
settings a run exercised. This module gives a run directory the same record a study has
(EVAL_REPORT section 42):

``llm_calls.jsonl``
    One line per paid call with every column of the ``llm_calls`` table
    (:func:`basis.llm.recorder.ledger_columns`, the dict the production repository writes,
    plus ``created_at``), so a row of the file and a row of the table are the same record.
``llm/<call id>.request.json`` / ``.response.json``
    The request as sent (messages, images as ref + sha256, tools, schema) and the raw
    response, built and redacted by the same functions as the production archive.
``run.log``
    Everything the run printed to stdout and stderr and logged, API-key-shaped strings
    redacted (:func:`basis.llm.errors.redact_api_keys`).
``manifest.json``
    The run manifest: what ran (harness module, its file's sha256, the command line and
    options), on which code (git sha, dirty flag, branch, nearest release tag), with which
    components (every prompt name + version + sha256, the price table version, the rules
    table version, the embedder and its pinned revision, the cost table if one is configured,
    the models requested and the models that answered, the request parameters in effect,
    the model settings), on which data (the input files and dataset files with their
    sha256 and label version, the split, the repeats), what it cost (calls, tokens of every
    kind, dollars) and how it ended (status, exit code, error), plus the ``run.json`` summary.
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

Usage in a runner (``run(args)``)::

    run = record.EvalRun.begin(args.out, harness=__name__, args=args, settings=settings)
    recorder = run.recorder                  # instead of MemoryRecorder()
    run.input("photos", args.photos)          # what was read: a file or a directory
    run.dataset(data_dir / "assets_v1.json")  # a label/dataset file (version from its name)
    ...
    run.finish({...})                         # writes run.json and manifest.json

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
    "EvalRun",
    "FileLedgerRecorder",
    "append_scores",
    "environment",
    "finish_pending",
    "git_state",
    "label_version",
    "ledger_row",
    "read_ledger",
    "score_entry",
    "sha256_file",
    "sha256_tree",
]

# The record format: bump when a file's shape changes (the registry reads every version).
FORMAT: Final = 1
MANIFEST_FILE: Final = "manifest.json"
LEDGER_FILE: Final = "llm_calls.jsonl"
PAYLOAD_DIR: Final = "llm"
LOG_FILE: Final = "run.log"
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
    ) -> EvalRun:
        """Start recording into ``out`` (created): the ledger recorder is ready, stdout,
        stderr and logging are copied to ``run.log``. ``harness`` is the runner's module
        name (``__name__``); ``args`` its options, to which the run attaches itself."""
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
                "log": LOG_FILE
                if self._log is not None or (self.out / LOG_FILE).exists()
                else None,
                "summary": SUMMARY_FILE if summary is not None else None,
                "ledger": LEDGER_FILE if self.recorder.records else None,
            },
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
        """Write ``run.json`` (when a summary is given; an existing one is read otherwise)
        and ``manifest.json``, stop the log, and return the manifest."""
        if summary is not None:
            (self.out / SUMMARY_FILE).write_text(_dumps(summary, indent=1) + "\n", encoding="utf-8")
        elif (self.out / SUMMARY_FILE).is_file():
            try:
                summary = json.loads((self.out / SUMMARY_FILE).read_text(encoding="utf-8"))
            except ValueError:
                summary = None
        manifest = self.manifest(summary, status=status, exit_code=exit_code, error=error)
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
