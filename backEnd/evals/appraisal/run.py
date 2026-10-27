"""Run the production appraisal extraction over a truth set, live or replayed, and score it.

``python -m evals.appraisal.run --set client|gse_d1|synthetic_1004|all --mode live|replay``
(``SPEC.md`` §7). Writes ``BASIS_EVAL_DATA_DIR/appraisal/runs/<id>/`` (or ``--out``):
``predictions.json``, ``score.json``, ``report.md``, ``run.json``.

* **live**: :func:`basis.extraction.pipeline.extract_appraisal` with the context the
  ``extract_fields`` activity builds (``TEXT_MODEL``, ``EXTRACTION_REVIEW_THRESHOLD``,
  ``EXTRACTION_OUTPUT_BUDGET``/``_REASONING_EFFORT``/``_FALLBACK_SCOPE``/
  ``_FALLBACK_CONTEXT_CHARS``, the latest ``appraisal_field_fallback`` prompt), the
  configured Azure DI client and the provider pool. Differences from the activity: the
  budget is ``--budget-usd`` per document (default $1) instead of the study's, calls are
  recorded in memory (:class:`~basis.llm.recorder.MemoryRecorder`) instead of
  ``llm_calls``, and there is no earlier revision to reuse. The Azure DI analysis and
  every model call are recorded under ``recordings/<pdf sha256>/``; with ``--repeats N``
  the document is analyzed by Azure DI once and the model is called N times. Prints the
  model cost.
* **replay**: no network. Azure DI and the model answers come from the recordings, with
  the extraction settings the recording was made with (``context.json``); the review
  threshold is the current setting. A missing recording fails that document with
  ``missing_recording`` and the run continues.

Systems for ablations: ``--no-azure`` (text layer and the model only), ``--azure-only`` (no
model fallback). ``--accept-baseline`` writes ``appraisal/baseline.json`` from this run.
A set whose truth file does not exist yet is skipped with a message. Exit status 1 when a
document failed or a baseline field regressed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import subprocess
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from basis.config import Settings, load_settings
from basis.extraction.azure_di import AzureDiClient, AzureDiResult, AzureDocumentIntelligence
from basis.extraction.fallback import PROMPT_NAME
from basis.extraction.pipeline import ExtractionContext, extract_appraisal
from basis.extraction.results import ExtractionResult
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget, ChatProvider
from basis.llm.recorder import MemoryRecorder
from evals.appraisal import baseline, recordings
from evals.appraisal.normalize import Columns, review_display
from evals.appraisal.score import render_report, score_run
from evals.appraisal.truth import SETS, DocumentTruth, TruthSet, appraisal_dir, load_truth

logger = logging.getLogger("evals.appraisal.run")

ACTIVITY: Final = "extract_fields"
DEFAULT_BUDGET_USD: Final = 1.0


@dataclass(frozen=True)
class Options:
    sets: tuple[str, ...]
    mode: str  # live | replay
    repeats: int = 1
    documents: tuple[str, ...] = ()
    no_azure: bool = False
    azure_only: bool = False
    accept_baseline: bool = False
    out: Path | None = None
    budget_usd: float = DEFAULT_BUDGET_USD
    data_dir: Path | None = None


@dataclass(frozen=True)
class ExtractionSettings:
    """The settings that shape the extraction (what ``context.json`` records)."""

    text_model: str
    reasoning_effort: str | None
    output_budget: int
    fallback_scope: str
    context_chars: int
    review_threshold: float

    @classmethod
    def from_settings(cls, settings: Settings) -> ExtractionSettings:
        return cls(
            text_model=settings.text_model,
            reasoning_effort=settings.extraction_reasoning_effort,
            output_budget=settings.extraction_output_budget,
            fallback_scope=settings.extraction_fallback_scope,
            context_chars=settings.extraction_fallback_context_chars,
            review_threshold=settings.extraction_review_threshold,
        )

    @classmethod
    def from_json(cls, data: dict[str, Any], review_threshold: float) -> ExtractionSettings:
        return cls(
            text_model=str(data["text_model"]),
            reasoning_effort=data.get("reasoning_effort"),
            output_budget=int(data["output_budget"]),
            fallback_scope=str(data["fallback_scope"]),
            context_chars=int(data["context_chars"]),
            review_threshold=review_threshold,
        )


@dataclass
class LiveDeps:
    """What a live run calls: the raw Azure DI client and the model provider."""

    azure: recordings.RawAzureClient | None
    provider: ChatProvider | None


@dataclass
class RunState:
    recorder: MemoryRecorder = field(default_factory=MemoryRecorder)
    # One recording provider per document, so each request hash is written once per run.
    providers: dict[Path, recordings.RecordingProvider] = field(default_factory=dict)
    azure_calls: int = 0
    azure_pages: int = 0
    timings: dict[str, float] = field(default_factory=dict)


def git_sha() -> str:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return done.stdout.strip()


def build_live_deps(settings: Settings, options: Options) -> LiveDeps:
    """The production clients from settings; refuses a live run that would silently drop
    a system (Azure DI or the model) the options ask for."""
    azure = None if options.no_azure else AzureDocumentIntelligence.from_settings(settings)
    if azure is None and not options.no_azure:
        raise SystemExit(
            "Azure DI is not configured (AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT/_KEY); "
            "pass --no-azure to run without it"
        )
    provider = None if options.azure_only else ProviderPool.from_settings(settings)
    if provider is None and not options.azure_only:
        raise SystemExit("no model provider is configured; pass --azure-only to run without one")
    return LiveDeps(azure=azure, provider=provider)


def _field_json(result: ExtractionResult) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in sorted(result.fields):
        item = result.fields[key]
        columns = Columns(item.value_text, item.value_num, item.value_date)
        fields[key] = {
            "value_text": item.value_text,
            "value_num": str(item.value_num) if item.value_num is not None else None,
            "value_date": item.value_date.isoformat() if item.value_date else None,
            "confidence": item.confidence,
            "source": item.source,
            "page": item.page,
            "critical": item.critical,
            "display": review_display(columns),
        }
    return fields


def _repeat_json(
    result: ExtractionResult, repeat: int, cost: float, seconds: float
) -> dict[str, Any]:
    fallback = result.fallback
    return {
        "repeat": repeat,
        "status": "ok",
        "method": result.method,
        "needs_review": result.needs_review,
        "review_reasons": list(result.review_reasons),
        "issues": [
            {"rule": i.rule, "keys": list(i.keys), "severity": i.severity} for i in result.issues
        ],
        "fallback": {
            "status": fallback.get("status"),
            "asked": fallback.get("asked", []),
            "filled": fallback.get("filled", []),
            "dropped": fallback.get("dropped", {}),
        },
        "fields": _field_json(result),
        "cost_usd": cost,
        "seconds": round(seconds, 3),
    }


def _failed(repeat: int, status: str, error: str | None) -> dict[str, Any]:
    return {"repeat": repeat, "status": status, "error": error}


async def predict_document(
    set_name: str,
    doc: DocumentTruth,
    *,
    appraisal: Path,
    options: Options,
    settings: Settings,
    live: LiveDeps | None,
    state: RunState,
) -> dict[str, Any]:
    """Every repeat of one document; failures are recorded, never raised."""
    entry: dict[str, Any] = {
        "set": set_name,
        "document": doc.document,
        "sha256": doc.sha256,
        "status": "ok",
        "repeats": [],
    }
    pdf_path = appraisal / doc.pdf
    if not pdf_path.is_file():
        entry["status"] = "missing_pdf"
        entry["repeats"] = [_failed(1, "missing_pdf", None)]
        return entry
    pdf = pdf_path.read_bytes()
    if hashlib.sha256(pdf).hexdigest() != doc.sha256:
        entry["status"] = "sha256_mismatch"
        entry["repeats"] = [_failed(1, "sha256_mismatch", None)]
        return entry
    directory = recordings.recording_dir(appraisal, doc.sha256)
    current = ExtractionSettings.from_settings(settings)
    if options.mode == "live":
        extraction = current
        recordings.write_context(directory, asdict(current))
    else:
        recorded = recordings.read_context(directory)
        extraction = (
            ExtractionSettings.from_json(recorded, current.review_threshold)
            if recorded is not None
            else current
        )
    entry["context"] = asdict(extraction)
    azure_result: AzureDiResult | None = None
    started_doc = time.monotonic()
    for repeat in range(1, options.repeats + 1):
        started = time.monotonic()
        azure: AzureDiClient | None = None
        provider: ChatProvider | None = None
        try:
            if not options.no_azure:
                if azure_result is not None:
                    azure = recordings.StaticAzureClient(azure_result)
                elif options.mode == "replay":
                    azure = recordings.ReplayAzureClient(directory)
                else:
                    assert live is not None and live.azure is not None
                    azure = recordings.RecordingAzureClient(live.azure, directory)
            if not options.azure_only:
                if options.mode == "replay":
                    provider = recordings.ReplayProvider(directory / recordings.LLM_DIR)
                else:
                    assert live is not None and live.provider is not None
                    if directory not in state.providers:
                        state.providers[directory] = recordings.RecordingProvider(
                            live.provider, directory / recordings.LLM_DIR
                        )
                    provider = state.providers[directory]
            budget = Budget(remaining_usd=options.budget_usd)
            ctx = ExtractionContext(
                call=CallContext(
                    study_id=uuid.uuid4(),
                    run_id=None,
                    activity=ACTIVITY,
                    budget=budget,
                    recorder=state.recorder,
                ),
                provider=provider,
                azure=azure,
                prompt=prompts.latest(PROMPT_NAME),
                text_model=extraction.text_model,
                review_threshold=extraction.review_threshold,
                fallback_max_tokens=extraction.output_budget,
                fallback_reasoning_effort=extraction.reasoning_effort,
                fallback_scope="missing" if extraction.fallback_scope == "missing" else "critical",
                fallback_context_chars=extraction.context_chars,
            )
            result = await extract_appraisal(pdf, doc.document, ctx)
        except recordings.MissingRecording as exc:
            logger.warning("%s/%s: %s", set_name, doc.document, exc)
            entry["repeats"].append(_failed(repeat, "missing_recording", str(exc)))
            entry["status"] = "missing_recording"
            break
        except Exception as exc:  # one document's failure must not stop the run
            logger.exception("%s/%s repeat %d failed", set_name, doc.document, repeat)
            status = f"error:{type(exc).__name__}"
            entry["repeats"].append(_failed(repeat, status, str(exc)[:500]))
            if entry["status"] == "ok":
                entry["status"] = status
            continue
        if isinstance(azure, recordings.RecordingAzureClient):
            state.azure_calls += azure.calls
            state.azure_pages += azure.pages
        if isinstance(azure, recordings.RecordingAzureClient | recordings.ReplayAzureClient):
            azure_result = azure.result  # analyzed once; later repeats reuse it
        entry["repeats"].append(
            _repeat_json(result, repeat, budget.spent_usd, time.monotonic() - started)
        )
    state.timings[f"{set_name}/{doc.document}"] = round(time.monotonic() - started_doc, 3)
    return entry


@lru_cache(maxsize=64)
def _pdf_pages(path: str) -> tuple[str, ...]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return tuple((page.extract_text() or "") for page in pdf.pages)


def page_texts_for(appraisal: Path) -> Any:
    def texts(doc: DocumentTruth) -> Sequence[str] | None:
        path = appraisal / doc.pdf
        return _pdf_pages(str(path)) if path.is_file() else None

    return texts


def load_sets(options: Options, appraisal: Path) -> dict[str, TruthSet]:
    truths: dict[str, TruthSet] = {}
    for name in options.sets:
        if not (appraisal / "truth" / f"{name}.json").is_file():
            print(f"skipping set {name}: no truth file truth/{name}.json yet")
            continue
        truths[name] = load_truth(name, appraisal.parent)
    return truths


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=_json_default) + "\n")


@dataclass
class RunOutput:
    directory: Path
    score: dict[str, Any]
    run: dict[str, Any]
    regressions: list[dict[str, str]]


async def run_eval(
    options: Options, *, settings: Settings | None = None, live: LiveDeps | None = None
) -> RunOutput | None:
    """Predict, score and write one run; ``None`` when no set has a truth file."""
    appraisal = appraisal_dir(options.data_dir)
    truths = load_sets(options, appraisal)
    if not truths:
        print("no truth file for the requested sets: nothing to run")
        return None
    settings = settings or load_settings()
    if options.mode == "live" and live is None:
        live = build_live_deps(settings, options)
    started = datetime.now(UTC)
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{options.mode}"
    directory = options.out or appraisal / "runs" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    state = RunState()
    documents: list[dict[str, Any]] = []
    attempted: set[tuple[str, str]] = set()
    for set_name, truth in truths.items():
        for doc in truth.documents:
            if options.documents and doc.document not in options.documents:
                continue
            attempted.add((set_name, doc.document))
            print(f"{set_name}/{doc.document} ...", flush=True)
            entry = await predict_document(
                set_name,
                doc,
                appraisal=appraisal,
                options=options,
                settings=settings,
                live=live,
                state=state,
            )
            print(f"{set_name}/{doc.document}: {entry['status']}", flush=True)
            documents.append(entry)
    predictions = {"run": run_id, "mode": options.mode, "documents": documents}
    score = score_run(truths, predictions, page_texts=page_texts_for(appraisal))
    sha = git_sha()
    cost = float(sum(r.cost_usd for r in state.recorder.records))
    run: dict[str, Any] = {
        "run": run_id,
        "mode": options.mode,
        "sets": sorted(truths),
        "documents": sorted(f"{s}/{d}" for s, d in attempted),
        "repeats": options.repeats,
        "systems": {"azure_di": not options.no_azure, "llm": not options.azure_only},
        "git_sha": sha,
        "settings": asdict(ExtractionSettings.from_settings(settings)),
        "prompt_version": prompts.latest(PROMPT_NAME).version_id,
        "budget_usd_per_document": options.budget_usd,
        "models": sorted({r.model for r in state.recorder.records}),
        "llm_calls": len(state.recorder.records),
        "cost_usd": cost if options.mode == "live" else 0.0,
        "replayed_cost_usd": cost if options.mode == "replay" else None,
        "azure_di_calls": state.azure_calls,
        "azure_di_pages": state.azure_pages,
        "timings_seconds": state.timings,
        "started": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _write(directory / "predictions.json", predictions)
    _write(directory / "score.json", score)
    _write(directory / "run.json", run)
    (directory / "report.md").write_text(render_report(score, run))
    baseline_path = appraisal / baseline.FILE
    previous = baseline.load(baseline_path)
    found = baseline.regressions(previous, score, attempted) if previous is not None else []
    if options.accept_baseline:
        baseline.write(baseline_path, score, attempted, run=run_id, git_sha=sha)
        print(f"baseline written: {baseline_path}")
    return RunOutput(directory=directory, score=score, run=run, regressions=found)


def parse_args(argv: Sequence[str] | None) -> Options:
    parser = argparse.ArgumentParser(
        prog="python -m evals.appraisal.run", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--set", required=True, choices=(*SETS, "all"))
    parser.add_argument("--mode", required=True, choices=("live", "replay"))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--documents", default="", help="comma-separated document names")
    parser.add_argument("--no-azure", action="store_true", help="text layer and model only")
    parser.add_argument("--azure-only", action="store_true", help="no model fallback")
    parser.add_argument("--accept-baseline", action="store_true")
    parser.add_argument("--out", type=Path, default=None, help="run directory (default runs/<id>)")
    parser.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    parser.add_argument("--data-dir", type=Path, default=None, help="default BASIS_EVAL_DATA_DIR")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.mode == "replay" and args.repeats != 1:
        parser.error("replay is deterministic: --repeats applies to live runs only")
    if args.no_azure and args.azure_only:
        parser.error("--no-azure and --azure-only together leave nothing to run")
    if args.budget_usd <= 0:
        parser.error("--budget-usd must be positive")
    return Options(
        sets=SETS if args.set == "all" else (args.set,),
        mode=args.mode,
        repeats=args.repeats,
        documents=tuple(d.strip() for d in args.documents.split(",") if d.strip()),
        no_azure=args.no_azure,
        azure_only=args.azure_only,
        accept_baseline=args.accept_baseline,
        out=args.out,
        budget_usd=args.budget_usd,
        data_dir=args.data_dir,
    )


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    options = parse_args(argv)
    try:
        output = asyncio.run(run_eval(options))
    except ValueError as exc:  # an invalid truth file, or a baseline refused for failures
        print(f"error: {exc}")
        return 1
    if output is None:
        return 0
    overall = output.score["overall"]
    failed = output.score["documents"]["failed"]
    print(
        f"wrote {output.directory}: {output.score['documents']['scored']} documents scored, "
        f"{len(failed)} failed; headline accuracy {overall['accuracy']:.3f} "
        f"({overall['correct']}/{overall['present']}), spurious {overall['spurious']}"
    )
    if options.mode == "live":
        print(
            f"model cost ${output.run['cost_usd']:.4f} over {output.run['llm_calls']} calls; "
            f"Azure DI {output.run['azure_di_pages']} pages in {output.run['azure_di_calls']} "
            "analyses (billed by Azure, not included)"
        )
    if output.regressions:
        print(f"{len(output.regressions)} baseline fields regressed:")
        for item in output.regressions:
            print(f"  {item['field']}: {item['baseline']} -> {item['now']}")
    return 1 if failed or output.regressions else 0


if __name__ == "__main__":
    sys.exit(main())
