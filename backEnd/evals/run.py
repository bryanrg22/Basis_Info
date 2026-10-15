"""``python -m evals.run``: score systems on the datasets, write a report and DB rows.

Usage (from ``backEnd/``)::

    python -m evals.run --split dev --repeats 5                 # BASIS_EVAL_DATA_DIR
    python -m evals.run --dry-run --report /tmp/eval.md         # synthetic fixtures
    python -m evals.run --predictions out/ --split dev          # score saved outputs

Each dataset is scored by one *system*. Saved predictions (``--predictions DIR``, one
``<dataset>.json`` per dataset, formats in ``evals/README.md``) win; otherwise a built-in
deterministic baseline runs where one exists:

* ``appraisal_fields``: ``printed_text`` (the value exactly as printed, from the labels'
  ``printed`` column) -- shows the gap between exact and normalized matching;
* ``photo_rooms`` / ``photo_components``: ``filename`` (words of the file name matched
  against the label vocabulary) -- a floor, not a model;
* ``assets``: ``rules_table`` (:func:`basis.domain.rules.lookup`; a miss abstains);
* ``retrieval_qrels``: ``bm25_pages`` (BM25 over one chunk per PDF page of the corpus in
  ``<data dir>/corpus/``, or the synthetic corpus on a dry run).

Model-backed systems (extraction, vision, the residual classifier) are run by the
orchestrator and scored through ``--predictions``. Every metric is reported with ``n``, a
Wilson interval for proportions, and a standard error clustered by document; with
``--repeats N`` > 1, pass^k over the N repeats is reported too.

When ``DATABASE_URL`` is set (and ``--no-db`` is not given) one ``eval_runs`` row per
dataset and one ``eval_results`` row per metric are written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

from basis.classify.store_protocol import InMemoryChunkStore, StoredChunk
from basis.domain.rules import lookup, normalize_label
from evals import datasets as ds
from evals.metrics import (
    FieldScore,
    classification_metrics,
    clustered_standard_error,
    field_accuracy,
    field_matches,
    ir_metrics,
    mean,
    pass_hat_k,
    summarize_proportion,
)

ABSTAIN: Final = "abstain"
CORPUS_VERSION: Final = uuid.UUID(int=1)
_WORD: Final = re.compile(r"[a-z]+")


@dataclass
class MetricRow:
    metric: str
    value: float
    n: int
    stderr: float = math.nan
    interval: tuple[float, float] | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    dataset: str
    system: str
    rows: list[MetricRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    skipped: bool = False


# --------------------------------------------------------------------------- systems


def _repeats(value: Any, repeats: int) -> list[Any]:
    """A saved prediction is one value or a list with one value per repeat."""
    if isinstance(value, dict) and "repeats" in value:
        runs = list(value["repeats"])
        return (runs * repeats)[:repeats] if runs else [None] * repeats
    return [value] * repeats


def _load_predictions(directory: Path | None, name: str) -> dict[str, Any] | None:
    if directory is None:
        return None
    path = directory / f"{name}.json"
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _filename_words(file: str) -> set[str]:
    return set(_WORD.findall(Path(file).stem.lower()))


def filename_room(file: str, vocabulary: Sequence[str]) -> str:
    words = _filename_words(file)
    for room in vocabulary:
        if set(room.split("_")) <= words:
            return room
    return "ambiguous"


def filename_components(file: str, vocabulary: Sequence[str]) -> set[str]:
    words = _filename_words(file)
    return {label for label in vocabulary if label != "other" and set(label.split("_")) <= words}


def pdf_page_store(pdfs: dict[str, Path]) -> InMemoryChunkStore:
    import pymupdf  # dev/eval dependency; imported only when a corpus is scored

    store = InMemoryChunkStore()
    for doc_id, path in pdfs.items():
        with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
            for number, page in enumerate(document, start=1):
                store.add(
                    StoredChunk(
                        id=uuid.uuid5(CORPUS_VERSION, f"{doc_id}:{number}"),
                        corpus_version_id=CORPUS_VERSION,
                        doc_id=doc_id,
                        page_start=number,
                        page_end=number,
                        chunk_key=f"{doc_id}:{number:04d}",
                        text=page.get_text(),
                    )
                )
    return store


# --------------------------------------------------------------------------- tasks


def score_fields(
    data: ds.AppraisalFields, split: str, repeats: int, saved: dict[str, Any] | None
) -> TaskResult:
    system = "predictions" if saved is not None else "printed_text"
    result = TaskResult("appraisal_fields", system)
    per_repeat: list[list[FieldScore]] = [[] for _ in range(repeats)]
    for doc in (d for d in data.documents if d.split == split):
        doc_predictions = (saved or {}).get(doc.document, {})
        for label in doc.fields:
            values = (
                _repeats(doc_predictions.get(label.field), repeats)
                if saved is not None
                else [label.printed] * repeats
            )
            for r, predicted in enumerate(values):
                exact, normalized = field_matches(
                    predicted,
                    label.expected,
                    label.type,
                    absolute=label.absolute,
                    relative=label.relative,
                )
                per_repeat[r].append(FieldScore(label.field, exact, normalized, doc.document))
    first = per_repeat[0]
    if not first:
        result.skipped = True
        result.notes.append(f"no {split} items")
        return result
    accuracy = field_accuracy(first)
    clusters = [s.cluster for s in first]
    for metric, hits in (
        ("exact_accuracy", [s.exact for s in first]),
        ("normalized_accuracy", [s.normalized for s in first]),
    ):
        summary = summarize_proportion(hits, clusters)
        result.rows.append(
            MetricRow(
                metric,
                summary["value"],
                summary["n"],
                summary["stderr"],
                tuple(summary["interval"]),
                {"per_field": {k: list(v) for k, v in accuracy.per_field.items()}}
                if metric == "normalized_accuracy"
                else {},
            )
        )
    _add_pass_k(result, [[s.normalized for s in rep] for rep in per_repeat], repeats)
    return result


def score_photo_rooms(
    data: ds.PhotoRooms, split: str, repeats: int, saved: dict[str, Any] | None
) -> TaskResult:
    system = "predictions" if saved is not None else "filename"
    result = TaskResult("photo_rooms", system)
    photos = [p for p in data.photos if p.split == split]
    if not photos:
        result.skipped = True
        result.notes.append(f"no {split} items")
        return result
    vocabulary = sorted({p.room_type for p in data.photos}, key=lambda r: (-len(r), r))
    runs = [
        [
            "ambiguous" if v is None else str(v)
            for v in (
                _repeats(saved.get(p.file), repeats)
                if saved is not None
                else [filename_room(p.file, vocabulary)] * repeats
            )
        ]
        for p in photos
    ]
    truth = [p.room_type for p in photos]
    predicted = [r[0] for r in runs]
    metrics = classification_metrics(truth, predicted, abstain="ambiguous")
    hits = [t == p for t, p in zip(truth, predicted, strict=True)]
    summary = summarize_proportion(hits, [p.document for p in photos])
    result.rows += [
        MetricRow(
            "accuracy",
            metrics.accuracy,
            metrics.n,
            summary["stderr"],
            metrics.accuracy_interval,
            {"confusion": metrics.confusion},
        ),
        MetricRow(
            "macro_f1",
            metrics.macro_f1,
            metrics.n,
            details={c: vars(s) for c, s in metrics.per_class.items()},
        ),
        MetricRow("coverage", metrics.coverage, metrics.n),
    ]
    _add_pass_k(
        result,
        _transpose([[v == t for v in r] for r, t in zip(runs, truth, strict=True)]),
        repeats,
    )
    return result


def score_photo_components(
    data: ds.PhotoComponentsSet, split: str, repeats: int, saved: dict[str, Any] | None
) -> TaskResult:
    system = "predictions" if saved is not None else "filename"
    result = TaskResult("photo_components", system)
    photos = [p for p in data.photos if p.split == split]
    if not photos:
        result.skipped = True
        result.notes.append(f"no {split} items")
        return result
    vocabulary = sorted({c.label for p in data.photos for c in p.components})
    tp = fp = fn = 0
    per_photo: list[bool] = []
    for photo in photos:
        truth = {c.label for c in photo.components if c.label != "other"}
        if saved is not None:
            raw = _repeats(saved.get(photo.file), repeats)[0] or []
            predicted = {normalize_label(str(x)) for x in raw}
        else:
            predicted = filename_components(photo.file, vocabulary)
        tp += len(truth & predicted)
        fp += len(predicted - truth)
        fn += len(truth - predicted)
        per_photo.append(truth == predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 0.0
    exact = summarize_proportion(per_photo, [p.document for p in photos])
    result.rows += [
        MetricRow("precision", precision, tp + fp),
        MetricRow("recall", recall, tp + fn),
        MetricRow("f1", f1, tp + fp + fn),
        MetricRow(
            "photo_exact_set", exact["value"], exact["n"], exact["stderr"], tuple(exact["interval"])
        ),
    ]
    result.notes.append("'other' labels are excluded; sets of labels per photo are compared")
    return result


def score_assets(
    data: ds.Assets, split: str, repeats: int, saved: dict[str, Any] | None
) -> TaskResult:
    system = "predictions" if saved is not None else "rules_table"
    result = TaskResult("assets", system)
    items = [a for a in data.assets if a.split == split]
    if not items:
        result.skipped = True
        result.notes.append(f"no {split} items")
        return result
    sections_true: list[str] = []
    sections_pred: list[str] = []
    buckets_true: list[str] = []
    buckets_pred: list[str] = []
    clusters: list[str] = []
    for asset in items:
        if saved is not None:
            guess = _repeats(saved.get(asset.id), repeats)[0] or {}
            section = guess.get("section") or ABSTAIN
            bucket = guess.get("bucket") or ABSTAIN
        else:
            hit = lookup(asset.component, asset.material, asset.attachment, asset.property_class)
            section = hit.section if hit else ABSTAIN
            bucket = hit.bucket.value if hit else ABSTAIN
        if asset.section is not None:
            sections_true.append(asset.section)
            sections_pred.append(str(section))
            clusters.append(asset.document)
        if asset.bucket is not None:
            buckets_true.append(asset.bucket)
            buckets_pred.append(str(bucket))
    for name, truth, predicted in (
        ("section", sections_true, sections_pred),
        ("bucket", buckets_true, buckets_pred),
    ):
        if not truth:
            result.notes.append(f"no {name} labels")
            continue
        metrics = classification_metrics(truth, predicted, abstain=ABSTAIN)
        hits = [t == p for t, p in zip(truth, predicted, strict=True)]
        stderr = (
            clustered_standard_error([float(h) for h in hits], clusters)
            if name == "section"
            else math.nan
        )
        result.rows += [
            MetricRow(
                f"{name}_accuracy",
                metrics.accuracy,
                metrics.n,
                stderr,
                metrics.accuracy_interval,
                {"confusion": metrics.confusion},
            ),
            MetricRow(f"{name}_macro_f1", metrics.macro_f1, metrics.n),
            MetricRow(
                f"{name}_coverage",
                metrics.coverage,
                metrics.n,
                details={"selective_accuracy": metrics.selective_accuracy},
            ),
        ]
    return result


def score_retrieval(
    data: ds.RetrievalQrels,
    split: str,
    repeats: int,
    saved: dict[str, Any] | None,
    corpus_dir: Path | None,
) -> TaskResult:
    system = "predictions" if saved is not None else "bm25_pages"
    result = TaskResult("retrieval_qrels", system)
    qrels = data.qrels(split)
    if not qrels:
        result.skipped = True
        result.notes.append(f"no {split} queries")
        return result
    if saved is not None:
        run = {qid: [str(x) for x in _repeats(saved.get(qid), repeats)[0] or []] for qid in qrels}
    else:
        pdfs = ds.corpus_pdfs(corpus_dir) if corpus_dir is not None and corpus_dir.is_dir() else {}
        if not pdfs:
            result.skipped = True
            result.notes.append("no corpus PDFs to search and no saved predictions")
            return result
        store = pdf_page_store(pdfs)
        queries = {q.id: q.query for q in data.queries if q.id in qrels}
        run = {
            qid: [f"{h.doc_id}:{h.page_start}" for h in store.bm25(CORPUS_VERSION, text, 10)]
            for qid, text in queries.items()
        }
        result.notes.append(f"{len(store.chunks)} page chunks from {len(pdfs)} PDFs")
    metrics = ir_metrics(run, qrels)
    for name in ("ndcg_10", "recall_5", "recall_10", "mrr"):
        values = [q[name] for q in metrics.per_query.values()]
        result.rows.append(
            MetricRow(
                name,
                getattr(metrics, name),
                metrics.n,
                clustered_standard_error(values, list(metrics.per_query)),
                details={"per_query": {q: v[name] for q, v in metrics.per_query.items()}}
                if name == "ndcg_10"
                else {},
            )
        )
    judged = {doc for q in qrels.values() for doc in q}
    retrieved = [doc for docs in run.values() for doc in docs[:10]]
    holes = sum(1 for doc in retrieved if doc not in judged)
    result.rows.append(
        MetricRow(
            "hole_rate_at_10", holes / len(retrieved) if retrieved else math.nan, len(retrieved)
        )
    )
    return result


def _transpose(rows: list[list[bool]]) -> list[list[bool]]:
    return [list(col) for col in zip(*rows, strict=True)] if rows else []


def _add_pass_k(result: TaskResult, per_repeat: list[list[bool]], repeats: int) -> None:
    if repeats < 2 or not per_repeat or not per_repeat[0]:
        return
    per_item = [list(col) for col in zip(*per_repeat, strict=True)]
    result.rows.append(MetricRow(f"pass^{repeats}", pass_hat_k(per_item, repeats), len(per_item)))
    result.rows.append(MetricRow("pass^1", pass_hat_k(per_item, 1), len(per_item)))


# --------------------------------------------------------------------------- report


def _fmt(value: float) -> str:
    return (
        "n/a"
        if value is None or (isinstance(value, float) and math.isnan(value))
        else f"{value:.4f}"
    )


def render_report(
    results: Sequence[TaskResult], *, split: str, repeats: int, source: str, git_sha: str
) -> str:
    lines = [
        "# Basis evaluation report",
        "",
        f"- split: `{split}`",
        f"- repeats: {repeats}",
        f"- data: {source}",
        f"- git sha: `{git_sha}`",
        f"- generated: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
        "Intervals are 95% Wilson intervals for proportions; SE is the standard error of the "
        "mean clustered by document (n/a with fewer than two clusters).",
    ]
    for task in results:
        lines += ["", f"## {task.dataset} (system: `{task.system}`)", ""]
        if task.skipped:
            lines.append(f"Skipped: {'; '.join(task.notes) or 'no data'}.")
            continue
        lines += ["| metric | value | n | 95% interval | SE |", "|---|---|---|---|---|"]
        for row in task.rows:
            interval = f"[{_fmt(row.interval[0])}, {_fmt(row.interval[1])}]" if row.interval else ""
            lines.append(
                f"| {row.metric} | {_fmt(row.value)} | {row.n} | {interval} | {_fmt(row.stderr)} |"
            )
        for note in task.notes:
            lines.append(f"\nNote: {note}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- database


def _finite(value: float) -> float | None:
    return None if value is None or math.isnan(value) or math.isinf(value) else float(value)


async def write_results(
    sessionmaker: Any,
    results: Sequence[TaskResult],
    *,
    split: str,
    git_sha: str,
    model: str | None = None,
    prompt_version: str | None = None,
) -> list[uuid.UUID]:
    """One ``eval_runs`` row per scored dataset, one ``eval_results`` row per metric."""
    from basis.db.models import EvalResult, EvalRun
    from basis.db.session import session_scope

    run_ids: list[uuid.UUID] = []
    async with session_scope(sessionmaker) as session:
        for task in results:
            if task.skipped:
                continue
            run = EvalRun(
                dataset=task.dataset,
                split=split,
                git_sha=git_sha,
                model=model or task.system,
                prompt_version=prompt_version,
            )
            session.add(run)
            await session.flush()
            run_ids.append(run.id)
            for row in task.rows:
                details = dict(row.details)
                if row.interval is not None:
                    details["interval"] = [_finite(row.interval[0]), _finite(row.interval[1])]
                details["system"] = task.system
                session.add(
                    EvalResult(
                        eval_run_id=run.id,
                        metric=row.metric,
                        value=_finite(row.value),
                        n=row.n,
                        stderr=_finite(row.stderr),
                        details=json.loads(json.dumps(details, default=str)),
                    )
                )
    return run_ids


async def _write_to_configured_db(results: Sequence[TaskResult], split: str, git_sha: str) -> int:
    from basis.config import load_settings
    from basis.db.engine import create_engine
    from basis.db.session import create_sessionmaker

    engine = create_engine(load_settings())
    try:
        ids = await write_results(
            create_sessionmaker(engine), results, split=split, git_sha=git_sha
        )
    finally:
        await engine.dispose()
    return len(ids)


# --------------------------------------------------------------------------- main


def git_sha() -> str:
    if os.environ.get("GIT_SHA"):
        return os.environ["GIT_SHA"]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


Scorer = Callable[[BaseModel, str, int, dict[str, Any] | None], TaskResult]


def evaluate(
    data: dict[str, BaseModel],
    *,
    split: str,
    repeats: int,
    predictions: Path | None,
    corpus_dir: Path | None,
) -> list[TaskResult]:
    results: list[TaskResult] = []
    order = ("appraisal_fields", "photo_rooms", "photo_components", "assets", "retrieval_qrels")
    for name in order:
        dataset = data.get(name)
        if dataset is None:
            results.append(TaskResult(name, "-", notes=["dataset file not found"], skipped=True))
            continue
        saved = _load_predictions(predictions, name)
        if isinstance(dataset, ds.AppraisalFields):
            results.append(score_fields(dataset, split, repeats, saved))
        elif isinstance(dataset, ds.PhotoRooms):
            results.append(score_photo_rooms(dataset, split, repeats, saved))
        elif isinstance(dataset, ds.PhotoComponentsSet):
            results.append(score_photo_components(dataset, split, repeats, saved))
        elif isinstance(dataset, ds.Assets):
            results.append(score_assets(dataset, split, repeats, saved))
        elif isinstance(dataset, ds.RetrievalQrels):
            results.append(score_retrieval(dataset, split, repeats, saved, corpus_dir))
    return results


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__.split("\n")[0])
    parser.add_argument("--split", default="dev", choices=("dev", "regression", "held_out"))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true", help="use the synthetic fixtures")
    parser.add_argument("--data-dir", type=Path, default=None, help=f"default: ${ds.ENV_VAR}")
    parser.add_argument("--predictions", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--no-db", action="store_true", help="do not write eval_runs rows")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        data = ds.synthetic_smoke()
        corpus_dir: Path | None = ds.SYNTHETIC_DIR / "corpus"
        source = "synthetic smoke dataset (tests/fixtures/synthetic)"
        report_dir = Path(tempfile.gettempdir())
    else:
        root = ds.data_dir(args.data_dir)
        data = ds.load_available(root)
        corpus_dir = root / "corpus"
        source = f"{ds.ENV_VAR} ({len(data)} dataset files)"
        report_dir = root / "reports"
    sha = git_sha()
    results = evaluate(
        data,
        split=args.split,
        repeats=args.repeats,
        predictions=args.predictions,
        corpus_dir=corpus_dir,
    )
    report = render_report(
        results, split=args.split, repeats=args.repeats, source=source, git_sha=sha
    )
    path = args.report or report_dir / f"basis-eval-{args.split}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    print(f"report written to {path}")
    if os.environ.get("DATABASE_URL") and not args.no_db:
        written = asyncio.run(_write_to_configured_db(results, args.split, sha))
        print(f"{written} eval_runs rows written")
    for task in results:
        headline = ", ".join(f"{r.metric}={_fmt(r.value)}" for r in task.rows[:3])
        print(f"{task.dataset} [{task.system}]: {'skipped' if task.skipped else headline}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["evaluate", "main", "mean", "render_report", "write_results"]
