"""Run an appraisal extraction system over a truth set, live or replayed, and score it.

``python -m evals.appraisal.run --set client|gse_d1|synthetic_1004|all --mode live|replay``
(``SPEC.md`` §7). Writes ``BASIS_EVAL_DATA_DIR/appraisal/runs/<id>/`` (or ``--out``):
``predictions.json``, ``score.json``, ``report.md``, ``run.json``.

* **live**: the system with the configured clients. For ``production``,
  :func:`basis.extraction.pipeline.extract_appraisal` with the context the ``extract_fields``
  activity builds (``TEXT_MODEL``, ``EXTRACTION_REVIEW_THRESHOLD``,
  ``EXTRACTION_OUTPUT_BUDGET``/``_REASONING_EFFORT``/``_FALLBACK_SCOPE``/
  ``_FALLBACK_CONTEXT_CHARS``, the latest ``appraisal_field_fallback`` prompt), the
  configured Azure DI client and the provider pool. Differences from the activity: the
  budget is ``--budget-usd`` per document (default $1) instead of the study's, calls are
  recorded in memory (:class:`~basis.llm.recorder.MemoryRecorder`) instead of
  ``llm_calls``, and there is no earlier revision to reuse. The Azure DI analyses and
  every model call are recorded under ``recordings/<pdf sha256>/``; with ``--repeats N``
  the document is analyzed by Azure DI once and the model is called N times. Prints the
  model cost.
* **replay**: no network. Azure DI and the model answers come from the recordings; the
  production-based systems use the extraction settings the recording was made with
  (``context.json``); the review threshold is the current setting. A missing recording fails
  that document with ``missing_recording`` and the run continues.

Systems (``--system``, default ``production``; designs in :mod:`evals.appraisal.systems`):
``production``, ``azure_1004``, ``production_plus_1004``, ``checkbox_context``, ``llm_all``,
``llm_pages``, ``llm_sections`` (``--with-images``), ``validate_reask`` (``--base``,
``--base-model``), ``vote`` (``--voters system@model[@effort],system@model[@effort]``),
``mismo_xml`` (the UAD 3.6 XML beside the PDF; no model).
``--model``/``--effort``/``--max-tokens`` choose the model of the model systems (for
``checkbox_context`` they override ``TEXT_MODEL``); ``--dpi``/``--min-page-words`` the page
images. A document's analyses (Azure DI layout, the 1004 model) run once per run, whatever
the system and its voters ask. Ablations of ``production``: ``--no-azure`` (text layer and
the model only), ``--azure-only`` (no model fallback). ``--accept-baseline`` writes
``appraisal/baseline.json`` from this run. A set whose truth file does not exist yet is
skipped with a message. Exit status 1 when a document failed or a baseline field regressed.
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
from basis.extraction.azure_di import AzureDiClient, AzureDocumentIntelligence
from basis.extraction.fallback import PROMPT_NAME
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.families import CLAUDE_EFFORTS, is_claude_model, refuses_no_effort
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget, ChatProvider
from basis.llm.recorder import CallRecord, MemoryRecorder
from evals.appraisal import baseline, recordings
from evals.appraisal.normalize import Columns, review_display
from evals.appraisal.score import PageWords, render_report, score_run
from evals.appraisal.systems import (
    NO_MODEL_SYSTEMS,
    SYSTEMS,
    ModelChoice,
    SystemEnv,
    SystemOutput,
    SystemSpec,
    VoterSpec,
    build_system,
    needs_mortgage,
    uses_production_settings,
)
from evals.appraisal.systems.base import (
    DEFAULT_DPI,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MIN_PAGE_WORDS,
    DocumentSources,
    ExtractionSettings,
    MissingInput,
    RawAnalyzer,
)
from evals.appraisal.systems.mortgage import AzureModelClient
from evals.appraisal.truth import SETS, DocumentTruth, TruthSet, appraisal_dir, load_truth

logger = logging.getLogger("evals.appraisal.run")

ACTIVITY: Final = "extract_fields"
DEFAULT_BUDGET_USD: Final = 1.0

__all__ = ["ExtractionSettings", "LiveDeps", "Options", "main", "run_eval"]


@dataclass(frozen=True)
class Options:
    sets: tuple[str, ...]
    mode: str  # live | replay
    repeats: int = 1
    documents: tuple[str, ...] = ()
    no_azure: bool = False
    fresh_azure: bool = False  # live: analyze again even when a recording exists
    azure_only: bool = False
    accept_baseline: bool = False
    out: Path | None = None
    budget_usd: float = DEFAULT_BUDGET_USD
    data_dir: Path | None = None
    system: SystemSpec = field(default_factory=SystemSpec)


@dataclass
class LiveDeps:
    """What a live run calls: the raw Azure DI client, the model provider and, for the 1004
    systems, the ``prebuilt-mortgage.us.1004`` client."""

    azure: recordings.RawAzureClient | None
    provider: ChatProvider | None
    mortgage: recordings.RawModelClient | None = None


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


def _needs_model(options: Options) -> bool:
    return options.system.name not in NO_MODEL_SYSTEMS and not options.azure_only


def build_live_deps(settings: Settings, options: Options) -> LiveDeps:
    """The production clients from settings; refuses a live run that would silently drop
    a system (Azure DI, the model, the 1004 model) the options ask for."""
    azure = None if options.no_azure else AzureDocumentIntelligence.from_settings(settings)
    if azure is None and not options.no_azure:
        raise SystemExit(
            "Azure DI is not configured (AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT/_KEY); "
            "pass --no-azure to run without it"
        )
    provider = ProviderPool.from_settings(settings) if _needs_model(options) else None
    if provider is None and _needs_model(options):
        raise SystemExit("no model provider is configured; pass --azure-only to run without one")
    mortgage = None
    if needs_mortgage(options.system):
        mortgage = AzureModelClient.from_settings(settings)
        if mortgage is None:
            raise SystemExit("the 1004 systems need AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT/_KEY")
    return LiveDeps(azure=azure, provider=provider, mortgage=mortgage)


def _field_json(output: SystemOutput) -> dict[str, Any]:
    """Every stored column, plus the system's regions (``None``: the system has none), the
    reader the value came from (``origin``) and the grounded quote."""
    result = output.result
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
            "origin": output.origin(key),
            "page": item.page,
            "critical": item.critical,
            "display": review_display(columns),
            "regions": output.regions.get(key, []) if output.regions is not None else None,
            "quote": output.quotes.get(key),
        }
    return fields


def _usage(records: Sequence[CallRecord]) -> dict[str, Any]:
    """Tokens, calls and the models' own latency of one repeat's model calls."""
    return {
        "llm_calls": len(records),
        "tokens_input": sum(r.usage.input_tokens for r in records),
        "tokens_output": sum(r.usage.output_tokens for r in records),
        "model_latency_ms": sum(int(r.latency_ms or 0) for r in records),
        "models": sorted({r.model for r in records}),
    }


def _repeat_json(
    output: SystemOutput, repeat: int, cost: float, seconds: float, usage: dict[str, Any]
) -> dict[str, Any]:
    result = output.result
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
            **{
                k: fallback[k]
                for k in ("statuses", "calls", "reask", "disagree", "error")
                if k in fallback
            },
        },
        "fields": _field_json(output),
        "cost_usd": cost,
        "seconds": round(seconds, 3),
        **usage,
    }


def _failed(repeat: int, status: str, error: str | None) -> dict[str, Any]:
    return {"repeat": repeat, "status": status, "error": error}


def _layout_client(
    options: Options, live: LiveDeps | None, directory: Path
) -> AzureDiClient | None:
    if options.no_azure:
        return None
    # A live run reuses a recorded analysis of the same PDF bytes (Azure DI is deterministic
    # for a document and billed per page; the sweep would otherwise pay again per system).
    if options.mode == "replay" or (
        not options.fresh_azure and (directory / recordings.AZURE_RAW).exists()
    ):
        return recordings.ReplayAzureClient(directory)
    assert live is not None and live.azure is not None
    return recordings.RecordingAzureClient(live.azure, directory)


def _mortgage_client(
    options: Options, live: LiveDeps | None, directory: Path
) -> RawAnalyzer | None:
    if not needs_mortgage(options.system):
        return None
    if options.mode == "replay" or (
        not options.fresh_azure and (directory / recordings.AZURE_1004_RAW).exists()
    ):
        return recordings.ReplayRawModel(directory)
    assert live is not None and live.mortgage is not None
    return recordings.RecordingRawModel(live.mortgage, directory)


def _provider(
    options: Options, live: LiveDeps | None, state: RunState, directory: Path
) -> ChatProvider | None:
    if not _needs_model(options):
        return None
    if options.mode == "replay":
        return recordings.ReplayProvider(directory / recordings.LLM_DIR)
    assert live is not None and live.provider is not None
    if directory not in state.providers:
        state.providers[directory] = recordings.RecordingProvider(
            live.provider, directory / recordings.LLM_DIR
        )
    return state.providers[directory]


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
    production_settings = uses_production_settings(options.system)
    if options.mode == "live":
        extraction = current
        if production_settings:  # what a production replay rebuilds its context from
            recordings.write_context(directory, asdict(current))
    else:
        recorded = recordings.read_context(directory) if production_settings else None
        extraction = (
            ExtractionSettings.from_json(recorded, current.review_threshold)
            if recorded is not None
            else current
        )
    entry["context"] = asdict(extraction)
    system = build_system(options.system)
    # The document's analyses, shared by every repeat: Azure DI runs once per document.
    layout = _layout_client(options, live, directory)
    mortgage = _mortgage_client(options, live, directory)
    sources = DocumentSources(layout, mortgage)
    started_doc = time.monotonic()
    for repeat in range(1, options.repeats + 1):
        started = time.monotonic()
        budget = Budget(remaining_usd=options.budget_usd)
        env = SystemEnv(
            pdf=pdf,
            doc_id=doc.document,
            call=CallContext(
                study_id=uuid.uuid4(),
                run_id=None,
                activity=ACTIVITY,
                budget=budget,
                recorder=state.recorder,
            ),
            provider=_provider(options, live, state, directory),
            sources=sources,
            extraction=extraction,
            pdf_path=pdf_path,
        )
        before = len(state.recorder.records)
        try:
            output = await system.run(env)
        except recordings.MissingRecording as exc:
            logger.warning("%s/%s: %s", set_name, doc.document, exc)
            entry["repeats"].append(_failed(repeat, "missing_recording", str(exc)))
            entry["status"] = "missing_recording"
            break
        except MissingInput as exc:
            logger.warning("%s/%s: %s", set_name, doc.document, exc)
            entry["repeats"].append(_failed(repeat, "missing_input", str(exc)))
            entry["status"] = "missing_input"
            break
        except Exception as exc:  # one document's failure must not stop the run
            logger.exception("%s/%s repeat %d failed", set_name, doc.document, repeat)
            status = f"error:{type(exc).__name__}"
            entry["repeats"].append(_failed(repeat, status, str(exc)[:500]))
            if entry["status"] == "ok":
                entry["status"] = status
            continue
        usage = _usage(state.recorder.records[before:])
        entry["repeats"].append(
            _repeat_json(output, repeat, budget.spent_usd, time.monotonic() - started, usage)
        )
    for client in (layout, mortgage):
        if isinstance(client, recordings.RecordingAzureClient | recordings.RecordingRawModel):
            state.azure_calls += client.calls
            state.azure_pages += client.pages
    entry["azure_pages"] = dict(sorted(sources.pages_by_model.items()))
    state.timings[f"{set_name}/{doc.document}"] = round(time.monotonic() - started_doc, 3)
    return entry


@lru_cache(maxsize=64)
def _pdf_pages(path: str) -> tuple[str, ...]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return tuple((page.extract_text() or "") for page in pdf.pages)


@lru_cache(maxsize=64)
def _pdf_words(path: str) -> tuple[PageWords, ...]:
    import pdfplumber

    pages: list[PageWords] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = tuple(
                (
                    str(w["text"]),
                    float(w["x0"]),
                    float(w["top"]),
                    float(w["x1"]),
                    float(w["bottom"]),
                )
                for w in page.extract_words()
            )
            pages.append(PageWords(float(page.width), float(page.height), words))
    return tuple(pages)


def page_texts_for(appraisal: Path) -> Any:
    def texts(doc: DocumentTruth) -> Sequence[str] | None:
        path = appraisal / doc.pdf
        return _pdf_pages(str(path)) if path.is_file() else None

    return texts


def page_words_for(appraisal: Path) -> Any:
    """pdfplumber words per page (the highlight metric's text layer)."""

    def words(doc: DocumentTruth) -> Sequence[PageWords] | None:
        path = appraisal / doc.pdf
        return _pdf_words(str(path)) if path.is_file() else None

    return words


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


def _system_json(spec: SystemSpec) -> dict[str, Any]:
    data = asdict(spec)
    data["label"] = spec.label()
    return data


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
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{options.mode}-{options.system.label()}"
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
    predictions = {
        "run": run_id,
        "mode": options.mode,
        "system": _system_json(options.system),
        "documents": documents,
    }
    score = score_run(
        truths,
        predictions,
        page_texts=page_texts_for(appraisal),
        page_words=page_words_for(appraisal),
    )
    sha = git_sha()
    cost = float(sum(r.cost_usd for r in state.recorder.records))
    run: dict[str, Any] = {
        "run": run_id,
        "mode": options.mode,
        "system": _system_json(options.system),
        "sets": sorted(truths),
        "documents": sorted(f"{s}/{d}" for s, d in attempted),
        "repeats": options.repeats,
        "systems": {
            "azure_di": not options.no_azure,
            "llm": _needs_model(options),
            "azure_1004": needs_mortgage(options.system),
        },
        "git_sha": sha,
        "settings": asdict(ExtractionSettings.from_settings(settings)),
        "prompt_version": prompts.latest(PROMPT_NAME).version_id,
        "prompt_versions": sorted({r.prompt_version or "" for r in state.recorder.records} - {""}),
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


# --------------------------------------------------------------------------- CLI


def _check_effort(parser: argparse.ArgumentParser, model: str | None, effort: str | None) -> None:
    if model is None or effort is None:
        return
    if is_claude_model(model) and effort not in CLAUDE_EFFORTS:
        parser.error(f"{model} takes effort {', '.join(CLAUDE_EFFORTS)}, not {effort!r}")
    if refuses_no_effort(model) and effort == "none":
        parser.error(f"{model} refuses reasoning effort 'none'")


def parse_voters(
    parser: argparse.ArgumentParser, text: str, max_tokens: int
) -> tuple[VoterSpec, ...]:
    """``system@model[@effort],system@model[@effort]`` (``system`` alone: no model)."""
    voters = []
    for part in (p.strip() for p in text.split(",") if p.strip()):
        name, _, rest = part.partition("@")
        model, _, effort = rest.partition("@")
        if name not in SYSTEMS or name in ("vote", "validate_reask"):
            parser.error(f"--voters: {name!r} is not a system that can vote")
        _check_effort(parser, model or None, effort or None)
        voters.append(VoterSpec(name, ModelChoice(model or None, effort or None, max_tokens)))
    return tuple(voters)


def parse_args(argv: Sequence[str] | None) -> Options:
    parser = argparse.ArgumentParser(
        prog="python -m evals.appraisal.run", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--set", required=True, choices=(*SETS, "all"))
    parser.add_argument("--mode", required=True, choices=("live", "replay"))
    parser.add_argument("--system", default="production", choices=SYSTEMS)
    parser.add_argument("--model", default=None, help="the model systems' model")
    parser.add_argument("--effort", default=None, help="its reasoning effort")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--base", default=None, choices=SYSTEMS, help="validate_reask's base")
    parser.add_argument("--base-model", default=None, help="the base's model (default --model)")
    parser.add_argument("--base-effort", default=None)
    parser.add_argument("--voters", default="", help="vote: system@model[@effort],...")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--min-page-words", type=int, default=DEFAULT_MIN_PAGE_WORDS)
    parser.add_argument("--with-images", action="store_true", help="llm_sections: page images")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--documents", default="", help="comma-separated document names")
    parser.add_argument("--no-azure", action="store_true", help="text layer and model only")
    parser.add_argument(
        "--fresh-azure",
        action="store_true",
        help="live: call Azure DI again even when the document's analysis is recorded",
    )
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
    if (args.no_azure or args.azure_only) and args.system != "production":
        parser.error("--no-azure / --azure-only are ablations of --system production")
    if args.budget_usd <= 0:
        parser.error("--budget-usd must be positive")
    if args.max_tokens < 1 or args.dpi < 36 or args.min_page_words < 0:
        parser.error("--max-tokens >= 1, --dpi >= 36 and --min-page-words >= 0")
    _check_effort(parser, args.model, args.effort)
    model = ModelChoice(args.model, args.effort, args.max_tokens)
    if args.system in ("llm_all", "llm_pages", "llm_sections", "validate_reask") and not args.model:
        parser.error(f"--system {args.system} needs --model")
    if (args.system == "validate_reask") != bool(args.base):
        parser.error("--base goes with --system validate_reask (and it needs one)")
    if args.base in ("validate_reask", "vote"):
        parser.error("--base must be a single system")
    base_model = None
    if args.base_model or args.base_effort:
        _check_effort(parser, args.base_model or args.model, args.base_effort)
        base_model = ModelChoice(
            args.base_model or args.model, args.base_effort or args.effort, args.max_tokens
        )
    if args.base in ("llm_all", "llm_pages", "llm_sections") and not (base_model or model).model:
        parser.error("the base model system needs --base-model or --model")
    voters: tuple[VoterSpec, ...] = ()
    if args.system == "vote":
        voters = parse_voters(parser, args.voters, args.max_tokens)
        if len(voters) != 2:
            parser.error("--system vote needs --voters with two system@model entries")
        for voter in voters:
            if voter.system in ("llm_all", "llm_pages", "llm_sections") and not voter.model.model:
                parser.error(f"--voters: {voter.system} needs a model (system@model)")
    elif args.voters:
        parser.error("--voters goes with --system vote")
    spec = SystemSpec(
        name=args.system,
        model=model,
        base=args.base,
        base_model=base_model,
        voters=voters,
        dpi=args.dpi,
        with_images=args.with_images,
        min_page_words=args.min_page_words,
    )
    return Options(
        sets=SETS if args.set == "all" else (args.set,),
        mode=args.mode,
        repeats=args.repeats,
        documents=tuple(d.strip() for d in args.documents.split(",") if d.strip()),
        no_azure=args.no_azure,
        fresh_azure=args.fresh_azure,
        azure_only=args.azure_only,
        accept_baseline=args.accept_baseline,
        out=args.out,
        budget_usd=args.budget_usd,
        data_dir=args.data_dir,
        system=spec,
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
    usage = output.score["usage"]
    print(
        f"wrote {output.directory}: {output.score['documents']['scored']} documents scored, "
        f"{len(failed)} failed; headline accuracy {overall['accuracy']:.3f} "
        f"({overall['correct']}/{overall['present']}), spurious {overall['spurious']}"
    )
    print(
        f"per document: model ${usage['model_usd_per_document']:.4f}, Azure DI "
        f"${usage['azure_usd_per_document']:.4f} (estimated), "
        f"{usage['tokens_input_per_document']:.0f} in / "
        f"{usage['tokens_output_per_document']:.0f} out tokens"
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
