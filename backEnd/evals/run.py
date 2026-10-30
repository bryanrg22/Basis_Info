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
  ``<data dir>/corpus/``, or the synthetic corpus on a dry run);
* ``rule_citations``: ``page_text`` (every rules-table citation checked against the page
  text of ``<data dir>/corpus/<doc_id>.pdf``: page in range and
  :func:`basis.classify.verify.excerpt_supported`, as
  :func:`basis.classify.router.resolve_rule_citations` checks a chunk). Derived from the
  code, so it has no dataset file and is scored whole on every split. On a dry run the
  synthetic corpus holds none of the cited documents, so nothing resolves (0 of n).
  ``--corpus-text parser`` reads the corpus through the production parse + chunk path
  instead of pymupdf for both (systems ``bm25_pages_parser``, ``page_text_parser``).
  ``--retrieval-systems`` adds the chunk-level systems of :mod:`evals.retrieval`:
  ``bm25_chunks``, ``vector_chunks`` (MiniLM cosine) and ``hybrid`` (the production
  fusion), with a paired bootstrap comparison between every two systems scored;
  ``--fusion-sweep`` adds the weighted-fusion weight sweep (EVAL_REPORT section 22).
* ``room_inventory``: ``per_photo_max`` (the labelled per-photo components of
  ``photo_components`` merged per room by label, count = the largest per-photo count:
  what the inventory is with no cross-photo reconciliation, the floor the per-room call
  has to beat);
* ``detection_boxes``: none. It is scored only from saved predictions
  (``python -m evals.predict_boxes``): per-class AP@0.5, mAP, and counts, precision and
  recall at ``--detection-threshold``.
* ``report_rows``: ``gold_inventory`` (the human room inventory of ``room_inventory``
  read as if the pipeline had produced it: the ceiling the vocabulary and the labelling
  conventions allow). Saved predictions are the room inventory of the predictions
  directory (``--inventory-file``, default ``room_inventory.json``, the same file
  ``room_inventory`` scores), with materials from ``room_raw.jsonl`` (or ``rooms.json``
  for the heuristic merge) when present. Per report row: evidenced, missed, vocabulary
  gap or not photo-derivable; coverage over photo-derivable rows, by cost share and per
  bucket.

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
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

from basis.classify.store_protocol import InMemoryChunkStore, StoredChunk
from basis.domain.rules import lookup, normalize_label
from basis.retrieval.embed import Embedder
from evals import datasets as ds
from evals import record
from evals.metrics import (
    DetectionImage,
    FieldScore,
    GoldBox,
    PredictedBox,
    classification_metrics,
    clustered_standard_error,
    count_pass_hat_k,
    detection_metrics,
    field_accuracy,
    field_matches,
    inventory_metrics,
    ir_metrics,
    mean,
    page_citation_status,
    paired_bootstrap,
    pass_hat_k,
    room_inventory_score,
    summarize_proportion,
    wilson_interval,
)

ABSTAIN: Final = "abstain"
IR_METRICS: Final = ("ndcg_10", "recall_5", "recall_10", "mrr")
RETRIEVAL_SYSTEMS: Final = ("bm25_pages", "bm25_chunks", "vector_chunks", "hybrid")
CORPUS_VERSION: Final = uuid.UUID(int=1)
_WORD: Final = re.compile(r"[a-z]+")
# Loose furniture (the FF&E row): listed so the engineer can price it (LABEL_GUIDE section 4,
# EVAL_REPORT section 19) but not a building component, so, like ``other``, it is kept out
# of the recognition and count scores (photo_components, room_inventory). report_rows reads
# it: it is the FF&E row's evidence.
UNSCORED_LABELS: Final = frozenset({"other", "furniture", "chair", "desk", "filing_cabinet"})


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
    # Extra Markdown tables for the report: (title, headers, rows).
    tables: list[tuple[str, list[str], list[list[str]]]] = field(default_factory=list)


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


def _counts_from_raw(directory: Path | None) -> dict[str, Any] | None:
    """``photo_counts`` predictions rebuilt from ``photo_raw.jsonl`` (runs made before the
    runners wrote ``photo_counts.json``): ``{file: {"repeats": [{label: count}, ...]}}``."""
    if directory is None or not (directory / "photo_raw.jsonl").exists():
        return None
    per_file: dict[str, list[tuple[int, dict[str, int]]]] = {}
    for line in (directory / "photo_raw.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        analysis = record.get("analysis")
        if analysis is None:
            continue
        counts: dict[str, int] = {}
        for component in analysis["components"]:
            label = str(component["label"])
            counts[label] = counts.get(label, 0) + int(component.get("count", 1))
        per_file.setdefault(record["file"], []).append((int(record.get("repeat", 1)), counts))
    return {
        file: {"repeats": [counts for _, counts in sorted(runs, key=lambda r: r[0])]}
        for file, runs in per_file.items()
    }


def predicted_count(counts: Mapping[str, Any], label: str) -> int:
    """The predicted count of ``label``; ``door`` is interior + exterior (+ a literal door)."""
    normalized = {normalize_label(str(k)): int(v) for k, v in counts.items()}
    if label == "door":
        return sum(normalized.get(k, 0) for k in ("door", "interior_door", "exterior_door"))
    return normalized.get(label, 0)


def score_photo_counts(
    data: ds.PhotoCounts, split: str, repeats: int, saved: dict[str, Any] | None
) -> TaskResult:
    result = TaskResult("photo_counts", "predictions" if saved is not None else "-")
    photos = [p for p in data.photos if p.split == split]
    if not photos:
        result.skipped = True
        result.notes.append(f"no {split} items")
        return result
    if saved is None:
        result.skipped = True
        result.notes.append(
            "no saved predictions (python -m evals.predict_photos); there is no baseline"
        )
        return result
    pairs: list[tuple[int, int]] = []
    clusters: list[str] = []
    per_class: dict[str, list[tuple[int, int]]] = {}
    missing = 0
    for photo in photos:
        for counts in _repeats(saved.get(photo.file), repeats):
            if counts is None:
                missing += 1
                continue
            for label, true in photo.counts.items():
                predicted = predicted_count(counts, label)
                pairs.append((predicted, true))
                clusters.append(photo.document)
                per_class.setdefault(label, []).append((predicted, true))

    def rows(name: str, values: list[tuple[int, int]], suffix: str = "") -> list[MetricRow]:
        errors = [float(abs(p - t)) for p, t in values]
        signed = [float(p - t) for p, t in values]
        tp = sum(min(p, t) for p, t in values)
        fp = sum(max(0, p - t) for p, t in values)
        fn = sum(max(0, t - p) for p, t in values)
        n = len(values)
        out = [
            MetricRow(f"count_mae{suffix}", mean(errors), n),
            MetricRow(f"exact_count_rate{suffix}", sum(1 for e in errors if e == 0) / n, n),
            MetricRow(f"count_signed_bias{suffix}", mean(signed), n),
            MetricRow(f"count_precision{suffix}", tp / (tp + fp) if tp + fp else 0.0, tp + fp),
            MetricRow(f"count_recall{suffix}", tp / (tp + fn) if tp + fn else 0.0, tp + fn),
        ]
        return out

    if not pairs:
        result.skipped = True
        result.notes.append("no photo of the dataset has a saved prediction")
        return result
    overall = rows("overall", pairs)
    overall[0] = MetricRow(
        "count_mae",
        overall[0].value,
        overall[0].n,
        clustered_standard_error([float(abs(p - t)) for p, t in pairs], clusters),
    )
    result.rows += overall
    for label in sorted(per_class):
        result.rows += rows(label, per_class[label], f":{label}")
    result.notes.append(
        "per (photo, class, repeat) over the verified classes of each photo (missing = 0); "
        "count precision/recall pool min/over/under-counts over pairs; 'door' is scored "
        "against interior_door + exterior_door; SE clustered by document"
    )
    if missing:
        result.notes.append(f"{missing} (photo, repeat) pairs have no saved prediction")
    return result


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


def pdf_page_texts(path: Path) -> list[str]:
    """The text of every page of a PDF (index 0 is page 1), as pymupdf extracts it."""
    import pymupdf  # dev/eval dependency; imported only when a corpus is scored

    with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
        return [page.get_text() for page in document]


def parser_page_texts(path: Path) -> list[str]:
    """The text of every page as the ingestion path stores it (index 0 is page 1).

    :func:`basis.retrieval.parse.parse_pdf` then :func:`basis.retrieval.chunk.chunk_document`,
    the chunks of a page (text windows, then table surrogates) joined by newlines: what
    retrieval indexes and what a citation is checked against, at page level. A page with
    no chunks is the empty string.
    """
    from basis.retrieval.chunk import chunk_document
    from basis.retrieval.parse import REFERENCE_MAX_PAGES, parse_pdf

    document = parse_pdf(path.read_bytes(), path.stem, max_pages=REFERENCE_MAX_PAGES)
    by_page: dict[int, list[str]] = defaultdict(list)
    for draft in chunk_document(document):
        by_page[draft.page].append(draft.text)
    return ["\n".join(by_page.get(n, [])) for n in range(1, document.page_count + 1)]


# ``--corpus-text``: how the corpus PDFs become page text for retrieval_qrels and
# rule_citations. ``pymupdf`` is the parser-independent reading (the historical
# baseline); ``parser`` is the production ingestion path, so it measures the parser.
CORPUS_TEXT: Final[dict[str, Callable[[Path], list[str]]]] = {
    "pymupdf": pdf_page_texts,
    "parser": parser_page_texts,
}


def pdf_page_store(
    pdfs: dict[str, Path], page_texts: Callable[[Path], list[str]] = pdf_page_texts
) -> InMemoryChunkStore:
    store = InMemoryChunkStore()
    for doc_id, path in pdfs.items():
        for number, text in enumerate(page_texts(path), start=1):
            store.add(
                StoredChunk(
                    id=uuid.uuid5(CORPUS_VERSION, f"{doc_id}:{number}"),
                    corpus_version_id=CORPUS_VERSION,
                    doc_id=doc_id,
                    page_start=number,
                    page_end=number,
                    chunk_key=f"{doc_id}:{number:04d}",
                    text=text,
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
    critical: list[bool] = []  # per scored field, in the order of every repeat's list
    for doc in (d for d in data.documents if d.split == split):
        doc_predictions = (saved or {}).get(doc.document, {})
        for label in doc.fields:
            critical.append(label.critical)
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
    if any(critical):
        # The fields marked critical in the labels (the ones a study cannot proceed without).
        crit = [s for s, c in zip(first, critical, strict=True) if c]
        summary = summarize_proportion([s.normalized for s in crit], [s.cluster for s in crit])
        result.rows.append(
            MetricRow(
                "critical_accuracy",
                summary["value"],
                summary["n"],
                summary["stderr"],
                tuple(summary["interval"]),
                {"missed": sorted(s.field for s in crit if not s.normalized)},
            )
        )
    _add_pass_k(result, [[s.normalized for s in rep] for rep in per_repeat], repeats)
    if any(critical):
        _add_pass_k(
            result,
            [[s.normalized for s, c in zip(rep, critical, strict=True) if c] for rep in per_repeat],
            repeats,
            prefix="critical_",
        )
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
        truth = {c.label for c in photo.components if c.label not in UNSCORED_LABELS}
        if saved is not None:
            raw = _repeats(saved.get(photo.file), repeats)[0] or []
            predicted = {normalize_label(str(x)) for x in raw} - UNSCORED_LABELS
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
    result.notes.append(
        "'other' and loose furniture (furniture, chair, desk, filing_cabinet) are excluded; "
        "sets of labels per photo are compared"
    )
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
    bucket_runs: list[list[bool]] = []  # per bucket-labelled asset, one outcome per repeat
    claimed = verified = 0
    cited_rows: list[bool] = []
    for asset in items:
        if saved is not None:
            runs = [r or {} for r in _repeats(saved.get(asset.id), repeats)]
            guess = runs[0]
            section = guess.get("section") or ABSTAIN
            bucket = guess.get("bucket") or ABSTAIN
            citations = guess.get("citations")
            if isinstance(citations, dict):
                claimed += int(citations.get("claimed", 0))
                verified += int(citations.get("verified", 0))
                cited_rows.append(int(citations.get("verified", 0)) > 0)
        else:
            hit = lookup(asset.component, asset.material, asset.attachment, asset.property_class)
            section = hit.section if hit else ABSTAIN
            bucket = hit.bucket.value if hit else ABSTAIN
            runs = [{"bucket": bucket}] * repeats
        if asset.section is not None:
            sections_true.append(asset.section)
            sections_pred.append(str(section))
            clusters.append(asset.document)
        if asset.bucket is not None:
            buckets_true.append(asset.bucket)
            buckets_pred.append(str(bucket))
            bucket_runs.append([r.get("bucket") == asset.bucket for r in runs])
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
    _add_pass_k(result, _transpose(bucket_runs), repeats, prefix="bucket_")
    if cited_rows:
        # Repeat 1, from the predictions' citation counts (evals.predict_assets): the share
        # of claimed citations that passed the production check (resolved, retrieved in the
        # loop, excerpt on the chunk), and the share of rows with at least one that did.
        low, high = wilson_interval(verified, claimed) if claimed else (math.nan, math.nan)
        result.rows += [
            MetricRow(
                "citation_validity",
                verified / claimed if claimed else math.nan,
                claimed,
                interval=(low, high),
                details={"claimed": claimed, "verified": verified},
            ),
            MetricRow(
                "rows_with_verified_citation",
                sum(cited_rows) / len(cited_rows),
                len(cited_rows),
                interval=wilson_interval(sum(cited_rows), len(cited_rows)),
            ),
        ]
    return result


def _corpus_system(name: str, corpus_text: str) -> str:
    """The system name, suffixed for a corpus text other than the pymupdf baseline."""
    return name if corpus_text == "pymupdf" else f"{name}_{corpus_text}"


def _ir_rows(
    run: Mapping[str, Sequence[str]], qrels: Mapping[str, Mapping[str, int]]
) -> list[MetricRow]:
    """ndcg_10, recall_5, recall_10, mrr (each with its per-query values) and the hole rate."""
    metrics = ir_metrics(run, qrels)
    rows: list[MetricRow] = []
    for name in IR_METRICS:
        values = [q[name] for q in metrics.per_query.values()]
        rows.append(
            MetricRow(
                name,
                getattr(metrics, name),
                metrics.n,
                clustered_standard_error(values, list(metrics.per_query)),
                details={"per_query": {q: v[name] for q, v in metrics.per_query.items()}},
            )
        )
    # A hole is a retrieved page with no judgment for that query (Buckley and Voorhees'
    # sense). Until EVAL_REPORT section 22 a page judged for any query counted as judged,
    # which understated the holes (bm25_pages_parser: 0.66 that way, 0.83 per query).
    retrieved = [(qid, doc) for qid, docs in run.items() if qid in qrels for doc in docs[:10]]
    holes = sum(1 for qid, doc in retrieved if doc not in qrels[qid])
    rows.append(
        MetricRow(
            "hole_rate_at_10", holes / len(retrieved) if retrieved else math.nan, len(retrieved)
        )
    )
    return rows


def score_retrieval(
    data: ds.RetrievalQrels,
    split: str,
    repeats: int,
    saved: dict[str, Any] | None,
    corpus_dir: Path | None,
    corpus_text: str = "pymupdf",
) -> TaskResult:
    system = "predictions" if saved is not None else _corpus_system("bm25_pages", corpus_text)
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
        store = pdf_page_store(pdfs, CORPUS_TEXT[corpus_text])
        queries = {q.id: q.query for q in data.queries if q.id in qrels}
        run = {
            qid: [f"{h.doc_id}:{h.page_start}" for h in store.bm25(CORPUS_VERSION, text, 10)]
            for qid, text in queries.items()
        }
        result.notes.append(f"{len(store.chunks)} page chunks from {len(pdfs)} PDFs")
    result.rows = _ir_rows(run, qrels)
    return result


def _per_query(result: TaskResult, metric: str) -> dict[str, float]:
    row = next(r for r in result.rows if r.metric == metric)
    return dict(row.details["per_query"])


def paired_comparison(results: Sequence[TaskResult], iterations: int = 10_000) -> TaskResult:
    """Every pair of scored retrieval systems: mean per-query difference (later system
    minus earlier), 95% bootstrap interval, randomization p, wins / losses / ties."""
    result = TaskResult("retrieval_qrels", "paired_bootstrap")
    scored = [r for r in results if not r.skipped]
    body: list[list[str]] = []
    for i, base in enumerate(scored):
        for other in scored[i + 1 :]:
            for metric in IR_METRICS:
                a, b = _per_query(other, metric), _per_query(base, metric)
                queries = sorted(a)
                diff = paired_bootstrap(
                    [a[q] for q in queries], [b[q] for q in queries], iterations=iterations
                )
                name = f"{metric}:{other.system}-{base.system}"
                result.rows.append(
                    MetricRow(
                        name,
                        diff.mean,
                        diff.n,
                        interval=(diff.low, diff.high),
                        details={
                            "p_value": diff.p_value,
                            "wins": diff.wins,
                            "losses": diff.losses,
                            "ties": diff.ties,
                        },
                    )
                )
                body.append(
                    [
                        f"{other.system} - {base.system}",
                        metric,
                        f"{diff.mean:+.4f}",
                        f"[{diff.low:+.4f}, {diff.high:+.4f}]",
                        f"{diff.p_value:.4f}",
                        f"{diff.wins}/{diff.losses}/{diff.ties}",
                    ]
                )
    if not body:
        result.skipped = True
        result.notes.append("fewer than two scored retrieval systems")
        return result
    result.notes.append(
        f"paired over the same queries; percentile bootstrap ({iterations} resamples, seed 0) "
        "and two-sided sign-flip randomization p; wins/losses/ties per query"
    )
    result.tables.append(
        (
            "Paired differences",
            ["systems", "metric", "mean difference", "95% bootstrap interval", "p", "w/l/t"],
            body,
        )
    )
    return result


def score_chunk_systems(
    data: ds.RetrievalQrels,
    split: str,
    corpus_dir: Path | None,
    systems: Sequence[str],
    embedder: Embedder | None = None,
    sweep: bool = False,
) -> list[TaskResult]:
    """``bm25_chunks``, ``vector_chunks`` and ``hybrid`` over the production chunks
    (:mod:`evals.retrieval`), one result per system, plus the fusion sweep on request."""
    from evals import retrieval as rt

    qrels = data.qrels(split)
    pdfs = ds.corpus_pdfs(corpus_dir) if corpus_dir is not None and corpus_dir.is_dir() else {}
    if not qrels or not pdfs:
        reason = f"no {split} queries" if not qrels else "no corpus PDFs to search"
        return [
            TaskResult("retrieval_qrels", name, notes=[reason], skipped=True) for name in systems
        ]
    from basis.config import get_settings

    settings = get_settings()
    if embedder is None:
        from basis.retrieval.embed import embedder_for

        embedder = embedder_for(settings)
    window = (settings.retrieval_chunk_tokens, settings.retrieval_chunk_overlap)
    started = time.perf_counter()
    chunks = rt.parser_chunks(pdfs, chunk_tokens=window[0], overlap_tokens=window[1])
    parsed = time.perf_counter()
    index = rt.ChunkIndex(chunks, embedder.embed([c.text for c in chunks]))
    embedded = time.perf_counter()
    queries = {q.id: q.query for q in data.queries if q.id in qrels}
    runs = rt.run_chunk_systems(index, embedder, queries, rt.production_fusion())
    setup = (
        f"{len(chunks)} production chunks ({window[0]}/{window[1]} tokens) from {len(pdfs)} "
        f"PDFs (parse + chunk {parsed - started:.0f} s, embed with {embedder.name} "
        f"{embedded - parsed:.0f} s); top {rt.CHUNK_DEPTH} chunks per query read as pages"
    )
    results: list[TaskResult] = []
    for name in systems:
        run = runs.page_run(name)
        result = TaskResult("retrieval_qrels", name, rows=_ir_rows(run, qrels), notes=[setup])
        short = sum(1 for docs in run.values() if len(docs) < rt.PAGE_DEPTH)
        if short:
            result.notes.append(f"{short} queries ranked fewer than {rt.PAGE_DEPTH} pages")
        if name == "hybrid":
            result.notes.append(
                f"fusion {runs.fusion.method}"
                + (
                    f", BM25 weight {runs.fusion.weight}"
                    if runs.fusion.method == "weighted"
                    else ""
                )
                + " (Settings.retrieval_fusion / retrieval_bm25_weight)"
            )
        results.append(result)
    if sweep:
        results[-1].tables.append(_fusion_sweep_table(runs, qrels))
    if "hybrid" in systems:
        results += _rule_resolution_results(rt.resolve_rules(index, embedder, runs.fusion), setup)
    return results


def _rule_resolution_results(resolutions: Sequence[Any], setup: str) -> list[TaskResult]:
    """``rule_citations`` through ``resolve_rule_citations`` with each classifier search
    (``resolve_bm25``, ``resolve_hybrid``) over the chunk systems' index."""
    results: list[TaskResult] = []
    for r in resolutions:
        result = TaskResult("rule_citations", f"resolve_{r.search}", notes=[setup])
        result.rows.append(
            MetricRow(
                "resolved_fraction",
                r.resolved / r.claimed if r.claimed else math.nan,
                r.claimed,
                details={"unresolved": r.unresolved},
            )
        )
        result.notes.append("unresolved: " + (", ".join(r.unresolved) or "none"))
        results.append(result)
    return results


def _fusion_sweep_table(
    runs: Any, qrels: Mapping[str, Mapping[str, int]]
) -> tuple[str, list[str], list[list[str]]]:
    from evals import retrieval as rt

    def ndcg(run: Mapping[str, Sequence[str]]) -> dict[str, float]:
        return {q: v["ndcg_10"] for q, v in ir_metrics(run, qrels).per_query.items()}

    rows, loo, chosen = rt.fusion_sweep(runs, ndcg)
    body = [[row.label, f"{row.mean:.4f}"] for row in rows]
    picks = ", ".join(f"{label} x{chosen.count(label)}" for label in dict.fromkeys(chosen))
    body.append(
        [
            "leave-one-out choice",
            f"{sum(loo.values()) / len(loo):.4f} (chosen: {picks})",
        ]
    )
    return ("Fusion sweep (ndcg_10, same candidates)", ["fusion", "ndcg_10"], body)


def score_rule_citations(
    data: ds.RuleCitations,
    corpus_dir: Path | None,
    page_texts: Callable[[Path], list[str]] = pdf_page_texts,
    system: str = "page_text",
) -> TaskResult:
    """Resolve every rules-table citation against the corpus page text.

    ``resolved_fraction`` is over citations (SE clustered by rule, since one rule's
    citations are not independent); ``rules_resolved`` is the share of rules whose every
    citation resolves (a rule with any unresolved citation is flagged
    ``citation_unverified`` in production).
    """
    result = TaskResult("rule_citations", system)
    if not data.citations:
        result.skipped = True
        result.notes.append("the rules table has no citations")
        return result
    pdfs = ds.corpus_pdfs(corpus_dir) if corpus_dir is not None and corpus_dir.is_dir() else {}
    if not pdfs:
        result.skipped = True
        result.notes.append("no corpus PDFs to check the citations against")
        return result
    cited = {c.doc_id for c in data.citations}
    pages = {doc_id: page_texts(path) for doc_id, path in pdfs.items() if doc_id in cited}
    statuses = [
        page_citation_status(c.excerpt, c.page, pages.get(c.doc_id)) for c in data.citations
    ]
    hits = [s == "resolved" for s in statuses]
    summary = summarize_proportion(hits, [c.rule for c in data.citations])
    unresolved = [
        {"rule": c.rule, "doc_id": c.doc_id, "page": c.page, "status": s}
        for c, s in zip(data.citations, statuses, strict=True)
        if s != "resolved"
    ]
    by_status: dict[str, int] = {}
    for status in statuses:
        by_status[status] = by_status.get(status, 0) + 1
    result.rows.append(
        MetricRow(
            "resolved_fraction",
            summary["value"],
            summary["n"],
            summary["stderr"],
            tuple(summary["interval"]),
            {"by_status": by_status, "unresolved": unresolved},
        )
    )
    rules = list(dict.fromkeys(c.rule for c in data.citations))
    failed = {u["rule"] for u in unresolved}
    per_rule = summarize_proportion([r not in failed for r in rules])
    result.rows.append(
        MetricRow(
            "rules_resolved",
            per_rule["value"],
            per_rule["n"],
            interval=tuple(per_rule["interval"]),
            details={"unresolved_rules": [r for r in rules if r in failed]},
        )
    )
    contested = sorted({c.rule for c in data.citations if c.contested})
    result.notes.append(
        f"rules version {data.rules_version}; {len(pages)} of {len(cited)} cited documents in "
        f"the corpus; status counts: " + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items()))
    )
    result.notes.append(
        "unresolved rules: " + (", ".join(r for r in rules if r in failed) or "none")
    )
    result.notes.append(
        f"{len(contested)} contested rules (a hit goes to review whatever its citations "
        "say): " + (", ".join(contested) or "none")
    )
    return result


def max_merge(per_photo: Sequence[Mapping[str, int]]) -> dict[str, int]:
    """Per-photo ``{label: count}`` -> one room inventory: count = the largest per-photo
    count (the :func:`basis.vision.reconcile.heuristic_merge` rule on plain labels)."""
    merged: dict[str, int] = {}
    for counts in per_photo:
        for label, count in counts.items():
            merged[label] = max(merged.get(label, 0), count)
    return merged


def _photo_index(components: ds.PhotoComponentsSet) -> dict[str, ds.PhotoComponents]:
    """Labelled photos by file, and by base name when that is unambiguous (a room may cite
    ``01_kitchen.jpg`` where the photo labels say ``photos/01_kitchen.jpg``)."""
    index = {p.file: p for p in components.photos}
    names: dict[str, list[ds.PhotoComponents]] = {}
    for photo in components.photos:
        names.setdefault(Path(photo.file).name, []).append(photo)
    for name, photos in names.items():
        if len(photos) == 1:
            index.setdefault(name, photos[0])
    return index


def per_photo_max(
    room: ds.RoomInventoryLabel, index: Mapping[str, ds.PhotoComponents]
) -> tuple[dict[str, int], list[str]]:
    """The baseline inventory of a room and the room's photos that have no labels."""
    per_photo: list[dict[str, int]] = []
    missing: list[str] = []
    for file in room.photos:
        photo = index.get(file) or index.get(Path(file).name)
        if photo is None:
            missing.append(file)
            continue
        counts: dict[str, int] = {}
        for c in photo.components:
            counts[c.label] = counts.get(c.label, 0) + c.count
        per_photo.append(counts)
    return max_merge(per_photo), missing


def _inventory(value: Any) -> dict[str, int]:
    """A predicted room inventory ``{label: count}`` with labels normalized, ``other``
    dropped and repeated labels (after normalization) added up."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"a room prediction must be {{label: count}}, got {value!r}")
    counts: dict[str, int] = {}
    for label, count in value.items():
        key = normalize_label(str(label))
        if key and key != "other":
            counts[key] = counts.get(key, 0) + int(count)
    return counts


def _scored(counts: Mapping[str, int]) -> dict[str, int]:
    """``counts`` without the labels the count scores leave out (:data:`UNSCORED_LABELS`)."""
    return {label: n for label, n in counts.items() if label not in UNSCORED_LABELS}


def score_room_inventory(
    data: ds.RoomInventorySet,
    split: str,
    repeats: int,
    saved: dict[str, Any] | None,
    components: ds.PhotoComponentsSet | None,
) -> TaskResult:
    system = "predictions" if saved is not None else "per_photo_max"
    result = TaskResult("room_inventory", system)
    rooms = [r for r in data.rooms if r.split == split]
    if not rooms:
        result.skipped = True
        result.notes.append(f"no {split} items")
        return result
    if saved is None and components is None:
        result.skipped = True
        result.notes.append("no saved predictions and no photo_components labels for the baseline")
        return result
    gold = {r.room_id: _scored(r.counts()) for r in rooms}
    runs: list[dict[str, dict[str, int]]] = [{} for _ in range(repeats)]
    if saved is not None:
        for room in rooms:
            for r, value in enumerate(_repeats(saved.get(room.room_id), repeats)):
                runs[r][room.room_id] = _scored(_inventory(value))
        extra = sorted(set(saved) - set(gold))
        if extra:
            result.notes.append(f"predicted rooms not in the {split} labels (ignored): {extra}")
    else:
        index = _photo_index(components) if components is not None else {}
        missing: list[str] = []
        for room in rooms:
            merged, absent = per_photo_max(room, index)
            missing += absent
            for r in range(repeats):
                runs[r][room.room_id] = _scored(_inventory(merged))
        if missing:
            result.notes.append(f"{len(missing)} room photos have no photo_components labels")
    metrics = inventory_metrics(gold, runs[0])
    documents = {r.room_id: r.document for r in rooms}
    room_ids = list(metrics.per_room)
    clusters = [documents[room] for room in room_ids]
    scores = [metrics.per_room[room] for room in room_ids]
    exact = summarize_proportion([s.exact for s in scores], clusters)
    by_label: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for score in scores:
        for label, truth, guess in score.pairs:
            by_label[label][0] += 1
            by_label[label][1] += guess - truth
    result.rows += [
        MetricRow("count_mae", metrics.count_mae, metrics.pairs, metrics.count_mae_se),
        MetricRow(
            "exact_count_rate", metrics.exact_count_rate, metrics.pairs, metrics.exact_count_se
        ),
        MetricRow(
            "signed_bias",
            metrics.signed_bias,
            metrics.pairs,
            metrics.signed_bias_se,
            details={"by_label": {k: v for k, v in sorted(by_label.items())}},
        ),
        MetricRow(
            "room_exact_inventory",
            exact["value"],
            exact["n"],
            exact["stderr"],
            tuple(exact["interval"]),
            {
                "per_room": {
                    room: {
                        "gold": gold[room],
                        "predicted": runs[0].get(room, {}),
                        "exact": metrics.per_room[room].exact,
                    }
                    for room in room_ids
                }
            },
        ),
    ]
    for name, values in (
        ("label_precision", [s.precision for s in scores]),
        ("label_recall", [s.recall for s in scores]),
        ("label_f1", [s.f1 for s in scores]),
    ):
        result.rows.append(
            MetricRow(name, mean(values), len(values), clustered_standard_error(values, clusters))
        )
    _add_pass_k(
        result,
        [
            [room_inventory_score(gold[room], run.get(room, {})).exact for room in room_ids]
            for run in runs
        ],
        repeats,
    )
    result.notes.append(
        "'other' and loose furniture are excluded; count metrics are over (room, label) "
        "pairs in the union of gold and predicted labels (missing = 0), SE clustered by room; room "
        "metrics are over rooms, SE clustered by document; signed bias > 0 is over-counting"
    )
    return result


def _predicted_boxes(value: Any) -> list[PredictedBox]:
    """A saved detection prediction: ``[{"label", "score", "x1", "y1", "x2", "y2"}]``."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"a detection prediction must be a list of boxes, got {value!r}")
    return [
        PredictedBox(
            label=normalize_label(str(box["label"])),
            score=float(box["score"]),
            x1=float(box["x1"]),
            y1=float(box["y1"]),
            x2=float(box["x2"]),
            y2=float(box["y2"]),
        )
        for box in value
    ]


def score_detection_boxes(
    data: ds.DetectionBoxes,
    split: str,
    repeats: int,
    saved: dict[str, Any] | None,
    threshold: float = 0.5,
) -> TaskResult:
    result = TaskResult("detection_boxes", "predictions" if saved is not None else "-")
    images = [i for i in data.images if i.split == split]
    if not images:
        result.skipped = True
        result.notes.append(f"no {split} items")
        return result
    if saved is None:
        result.skipped = True
        result.notes.append(
            "no saved predictions (python -m evals.predict_boxes); there is no baseline"
        )
        return result
    gold = [
        DetectionImage(
            file=i.file,
            document=i.document,
            boxes=tuple(GoldBox(b.label, b.x1, b.y1, b.x2, b.y2, b.group_of) for b in i.boxes),
            verified=frozenset(i.verified) if i.verified is not None else None,
        )
        for i in images
    ]
    runs: list[dict[str, list[PredictedBox]]] = [{} for _ in range(repeats)]
    for image in images:
        for r, value in enumerate(_repeats(saved.get(image.file), repeats)):
            runs[r][image.file] = _predicted_boxes(value)
    missing = sorted(i.file for i in images if i.file not in saved)
    if missing:
        result.notes.append(f"{len(missing)} images have no predictions (scored as empty)")
    metrics = detection_metrics(gold, runs[0], threshold=threshold)
    scored_classes = {c: v for c, v in metrics.classes.items() if v.positives > 0}
    result.rows.append(
        MetricRow(
            "map_50",
            metrics.map,
            len(scored_classes),
            details={
                c: {"ap": v.ap, "positives": v.positives, "predictions": v.predictions}
                for c, v in metrics.classes.items()
            },
        )
    )
    for label, value in metrics.classes.items():
        result.rows.append(MetricRow(f"ap_50:{label}", value.ap, value.positives))
    by_class = {
        c: {
            "pairs": v.count_pairs,
            "count_mae": v.count_mae,
            "count_bias": v.count_bias,
            "precision": v.precision,
            "recall": v.recall,
        }
        for c, v in metrics.classes.items()
    }
    result.rows += [
        MetricRow("count_mae", metrics.count_mae, metrics.count_pairs, metrics.count_mae_se),
        MetricRow(
            "count_signed_bias",
            metrics.count_bias,
            metrics.count_pairs,
            metrics.count_bias_se,
            details={"by_class": by_class},
        ),
        MetricRow("precision_at_threshold", metrics.precision, metrics.predictions_at_threshold),
        MetricRow("recall_at_threshold", metrics.recall, metrics.positives_at_threshold),
    ]
    for label, value in metrics.classes.items():
        result.rows.append(MetricRow(f"count_mae:{label}", value.count_mae, value.count_pairs))
        result.rows.append(
            MetricRow(f"count_signed_bias:{label}", value.count_bias, value.count_pairs)
        )
    if repeats >= 2:
        for k in (repeats, 1):
            value_k, n = count_pass_hat_k(gold, runs, threshold, k)
            result.rows.append(MetricRow(f"count_pass^{k}", value_k, n))
    result.notes.append(
        f"AP at IoU 0.5 (VOC all-point; Open Images group-of and verified-label rules); "
        f"counts, precision and recall at score >= {threshold:g}; count pairs are (image, "
        "class) over the union of gold and predicted classes, skipping classes with a "
        "group box, SE clustered by document; 'door' is scored against interior_door + "
        "exterior_door predictions"
    )
    return result


# --------------------------------------------------------------------------- report rows

INVENTORY_FILE: Final = "room_inventory.json"
HEURISTIC_INVENTORY_FILE: Final = "room_inventory.heuristic.json"
NO_MATERIAL: Final = frozenset({"", "unknown", "none", "null"})
Outcome = str  # evidenced | missed | vocabulary_gap | not_photo_derivable
RoomMaterials = Mapping[str, Mapping[str, Sequence[str]]]  # room_id -> label -> materials
# One map for every repeat, or one map per repeat (cycled like a saved prediction's repeats).
MaterialsInput = RoomMaterials | Sequence[RoomMaterials | None] | None


@dataclass(frozen=True)
class Evidence:
    room: str
    label: str
    count: int
    materials: tuple[str, ...]  # as reported; () when no material source


@dataclass
class RowOutcome:
    row: ds.ReportRow
    outcome: Outcome
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def evidenced(self) -> bool:
        return self.outcome == "evidenced"

    @property
    def count_present(self) -> bool:
        return any(e.count >= 1 for e in self.evidence)

    @property
    def material_present(self) -> bool:
        return any(m for e in self.evidence for m in e.materials if m not in NO_MATERIAL)


def load_room_materials(predictions: Path | None, inventory_file: str) -> MaterialsInput:
    """Materials per (room, label) for the inventory being scored, from the files
    :mod:`evals.predict_rooms` writes next to it: for ``room_inventory.json`` one map per
    repeat of the reconciliation call from ``room_raw.jsonl`` (a list, in repeat order);
    for ``room_inventory.heuristic.json`` one map from the heuristic objects of
    ``rooms.json``; ``None`` when neither applies."""
    if predictions is None:
        return None

    def add(target: dict[str, dict[str, list[str]]], room: str, objects: Any) -> None:
        for obj in objects or []:
            label = normalize_label(str(obj.get("label", "")))
            material = obj.get("material")
            if label and label != "other":
                target.setdefault(room, {}).setdefault(label, []).append(
                    str(material).lower() if material else "unknown"
                )

    if inventory_file == HEURISTIC_INVENTORY_FILE and (predictions / "rooms.json").is_file():
        found: dict[str, dict[str, list[str]]] = {}
        for room in json.loads((predictions / "rooms.json").read_text(encoding="utf-8")):
            add(found, str(room["room_id"]), room.get("heuristic_objects"))
        return found
    if inventory_file == INVENTORY_FILE and (predictions / "room_raw.jsonl").is_file():
        by_repeat: dict[int, dict[str, dict[str, list[str]]]] = {}
        for line in (predictions / "room_raw.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                repeat = int(record.get("repeat", 1))
                add(by_repeat.setdefault(repeat, {}), str(record["room_id"]), record.get("objects"))
        return [by_repeat[r] for r in sorted(by_repeat)]
    return None


def materials_for_repeats(materials: MaterialsInput, repeats: int) -> list[RoomMaterials | None]:
    """One material map per repeat: a single map is shared, a list is cycled."""
    if materials is None:
        return [None] * repeats
    if isinstance(materials, Mapping):
        return [materials] * repeats
    maps = list(materials)
    return (maps * repeats)[:repeats] if maps else [None] * repeats


def gold_materials(
    inventory: ds.RoomInventorySet, components: ds.PhotoComponentsSet | None
) -> RoomMaterials | None:
    """Materials for the ``gold_inventory`` baseline: the labelled per-photo components
    (``photo_components``) of each room's photos, by label."""
    if components is None:
        return None
    index = _photo_index(components)
    found: dict[str, dict[str, list[str]]] = {}
    for room in inventory.rooms:
        for file in room.photos:
            photo = index.get(file) or index.get(Path(file).name)
            for c in photo.components if photo is not None else []:
                if c.label != "other":
                    found.setdefault(room.room_id, {}).setdefault(c.label, []).append(
                        c.material or "unknown"
                    )
    return found


def row_outcome(
    row: ds.ReportRow,
    inventory: Mapping[str, Mapping[str, int]],
    room_types: Mapping[str, str] | None,
    materials: RoomMaterials | None,
) -> RowOutcome:
    """Apply one row's evidence rule to one room inventory ``{room_id: {label: count}}``."""
    if not row.photo_derivable:
        return RowOutcome(row, "not_photo_derivable")
    if row.vocabulary_gap:
        return RowOutcome(row, "vocabulary_gap")
    evidence: list[Evidence] = []
    for room_id, counts in sorted(inventory.items()):
        room_type = room_types.get(room_id) if room_types is not None else None
        if not row.room_allowed(room_type):
            continue
        for label in row.labels:
            count = counts.get(label, 0)
            if count < 1:
                continue
            reported = tuple((materials or {}).get(room_id, {}).get(label, ()))
            if not row.material_allowed(label, reported):
                continue
            evidence.append(Evidence(room_id, label, count, reported))
    return RowOutcome(row, "evidenced" if evidence else "missed", evidence)


def _share(outcomes: Sequence[RowOutcome]) -> float:
    return sum(o.row.cost_share for o in outcomes)


def score_report_rows(
    data: ds.ReportRows,
    split: str,
    repeats: int,
    saved: dict[str, Any] | None,
    inventory: ds.RoomInventorySet | None,
    materials: MaterialsInput = None,
) -> TaskResult:
    """Did the pipeline produce the evidence each report row needs?

    ``saved`` is a room inventory prediction (``{room_id: {label: count} | {"repeats":
    [...]}}``); without one the human inventory of the ``room_inventory`` dataset is
    scored as the ``gold_inventory`` ceiling. Room types for the rows' room filters come
    from the ``room_inventory`` labels when given; ``materials`` is one map for every
    repeat or one per repeat (:func:`load_room_materials`). Coverage is over the photo-derivable
    rows (kinds A and B, vocabulary gaps included: the product number); the
    in-vocabulary variant leaves the gaps out (the model number). Kind C rows are never
    a miss. With repeats, pass^k over the photo-derivable rows is "stable coverage".
    """
    system = "predictions" if saved is not None else "gold_inventory"
    result = TaskResult("report_rows", system)
    rows = [r for r in data.rows if r.split == split]
    if not rows:
        result.skipped = True
        result.notes.append(f"no {split} items")
        return result
    if saved is None and inventory is None:
        result.skipped = True
        result.notes.append("no saved room inventory and no room_inventory labels for the baseline")
        return result
    room_types: dict[str, str] | None = None
    if inventory is not None:
        room_types = {r.room_id: r.room_type for r in inventory.rooms}
    runs: list[dict[str, dict[str, int]]] = []
    if saved is not None:
        for r in range(repeats):
            runs.append(
                {room: _inventory(_repeats(value, repeats)[r]) for room, value in saved.items()}
            )
    else:
        assert inventory is not None
        gold = {r.room_id: r.counts() for r in inventory.rooms if r.split == split}
        runs = [gold for _ in range(repeats)]
    per_repeat = [
        [row_outcome(row, run, room_types, repeat_materials) for row in rows]
        for run, repeat_materials in zip(
            runs, materials_for_repeats(materials, repeats), strict=True
        )
    ]
    outcomes = per_repeat[0]
    # how many repeats evidence each row (repeat 1 decides the outcome; the rest, stability)
    stable = [sum(rep[i].evidenced for rep in per_repeat) for i in range(len(rows))]
    derivable = [o for o in outcomes if o.row.photo_derivable]
    in_vocabulary = [o for o in derivable if not o.row.vocabulary_gap]
    evidenced = [o for o in derivable if o.evidenced]
    missed = [o for o in derivable if o.outcome == "missed"]
    gaps = [o for o in derivable if o.outcome == "vocabulary_gap"]
    not_derivable = [o for o in outcomes if not o.row.photo_derivable]

    def proportion(metric: str, hits: Sequence[bool], items: Sequence[RowOutcome]) -> MetricRow:
        summary = summarize_proportion(list(hits), [o.row.document for o in items])
        return MetricRow(
            metric, summary["value"], summary["n"], summary["stderr"], tuple(summary["interval"])
        )

    def ratio(numerator: float, denominator: float) -> float:
        return numerator / denominator if denominator else math.nan

    total_share = _share(outcomes)
    coverage = proportion("row_coverage", [o.evidenced for o in derivable], derivable)
    coverage.details = {
        "rows": [
            {
                "id": o.row.id,
                "system": o.row.system,
                "bucket": o.row.bucket,
                "kind": o.row.kind,
                "outcome": o.outcome,
                "cost_share": o.row.cost_share,
                "count_present": o.count_present if o.row.count_required else None,
                "material_present": o.material_present if o.row.material_required else None,
                "missing_label": o.row.missing_label,
                "repeats_evidenced": stable[i] if o.row.labels else None,
                "evidence": [
                    {
                        "room": e.room,
                        "label": e.label,
                        "count": e.count,
                        "materials": list(e.materials),
                    }
                    for e in o.evidence
                ],
            }
            for i, o in enumerate(outcomes)
        ]
    }
    result.rows.append(coverage)
    result.rows.append(
        proportion(
            "row_coverage_in_vocabulary", [o.evidenced for o in in_vocabulary], in_vocabulary
        )
    )
    result.rows.append(
        MetricRow(
            "cost_coverage",
            ratio(_share(evidenced), _share(derivable)),
            len(derivable),
            details={
                "evidenced_share": _share(evidenced),
                "missed_share": _share(missed),
                "vocabulary_gap_share": _share(gaps),
                "not_photo_derivable_share": _share(not_derivable),
                "photo_derivable_share": _share(derivable),
            },
        )
    )
    result.rows.append(
        MetricRow("cost_coverage_of_study", ratio(_share(evidenced), total_share), len(outcomes))
    )
    for bucket in ("y5", "y7", "y15", "y27_5", "y39"):
        in_bucket = [o for o in derivable if o.row.bucket == bucket]
        if not in_bucket:
            continue
        hit = [o for o in in_bucket if o.evidenced]
        row = proportion(f"coverage:{bucket}", [o.evidenced for o in in_bucket], in_bucket)
        row.details = {
            "cost_coverage": ratio(_share(hit), _share(in_bucket)),
            "rows_not_photo_derivable": sum(1 for o in not_derivable if o.row.bucket == bucket),
        }
        result.rows.append(row)
        result.rows.append(
            MetricRow(
                f"cost_coverage:{bucket}", ratio(_share(hit), _share(in_bucket)), len(in_bucket)
            )
        )
    need_count = [o for o in derivable if o.row.count_required]
    need_material = [o for o in derivable if o.row.material_required]
    result.rows.append(
        proportion(
            "count_present_rate", [o.evidenced and o.count_present for o in need_count], need_count
        )
    )
    result.rows.append(
        proportion(
            "material_present_rate",
            [o.evidenced and o.material_present for o in need_material],
            need_material,
        )
    )
    result.rows.append(
        MetricRow("vocabulary_gap_rate", ratio(len(gaps), len(derivable)), len(derivable))
    )
    result.rows.append(
        MetricRow(
            "not_photo_derivable_rate", ratio(len(not_derivable), len(outcomes)), len(outcomes)
        )
    )
    _add_pass_k(
        result,
        [[o.evidenced for o in rep if o.row.photo_derivable] for rep in per_repeat],
        repeats,
    )

    def names(items: Sequence[RowOutcome]) -> str:
        return ", ".join(o.row.system for o in items) or "none"

    result.notes.append(
        f"{len(outcomes)} rows: {len(evidenced)} evidenced, {len(missed)} missed, "
        f"{len(gaps)} vocabulary gaps, {len(not_derivable)} not photo-derivable"
    )
    result.notes.append(f"missed rows: {names(missed)}")
    result.notes.append(
        "vocabulary gaps (label needed): "
        + (", ".join(f"{o.row.system} ({o.row.missing_label})" for o in gaps) or "none")
    )
    result.notes.append(f"not photo-derivable: {names(not_derivable)}")
    if repeats >= 2:
        unstable = [
            f"{o.row.system} ({stable[i]}/{repeats})"
            for i, o in enumerate(outcomes)
            if o.row.photo_derivable and 0 < stable[i] < repeats
        ]
        result.notes.append(f"rows evidenced in some repeats only: {', '.join(unstable) or 'none'}")
    if room_types is None:
        filtered = sum(
            1 for o in derivable if o.row.room_types is not None or o.row.exclude_room_types
        )
        result.notes.append(
            f"no room types available: {filtered} rows with a room-type filter cannot be evidenced"
        )
    result.notes.append(
        "materials from "
        + ("the per-room records" if materials is not None else "nowhere: material_present is 0")
        + "; coverage is over photo-derivable rows (kinds A and B, vocabulary gaps included), "
        "SE clustered by document; cost coverage weights rows by their share of the study's "
        "cost; pass^k over repeats is coverage stable across repeats"
    )
    result.tables.append(
        (
            "Rows",
            [
                "row",
                "bucket",
                "kind",
                "outcome",
                "count",
                "material",
                "repeats",
                "evidence (room: label x n)",
            ],
            [
                [
                    o.row.system,
                    o.row.bucket,
                    o.row.kind,
                    o.outcome
                    + (f" (needs `{o.row.missing_label}`)" if o.row.vocabulary_gap else ""),
                    ("yes" if o.count_present else "no") if o.row.count_required else "",
                    ("yes" if o.material_present else "no") if o.row.material_required else "",
                    f"{stable[i]}/{repeats}" if o.row.labels and repeats >= 2 else "",
                    "; ".join(f"{e.room}: {e.label} x{e.count}" for e in o.evidence[:6])
                    + (f"; +{len(o.evidence) - 6} more" if len(o.evidence) > 6 else ""),
                ]
                for i, o in enumerate(outcomes)
            ],
        )
    )
    return result


def _transpose(rows: list[list[bool]]) -> list[list[bool]]:
    return [list(col) for col in zip(*rows, strict=True)] if rows else []


def _add_pass_k(
    result: TaskResult, per_repeat: list[list[bool]], repeats: int, prefix: str = ""
) -> None:
    """``pass^k`` (every repeat right) and ``pass^1`` (the mean over repeats) per item."""
    if repeats < 2 or not per_repeat or not per_repeat[0]:
        return
    per_item = [list(col) for col in zip(*per_repeat, strict=True)]
    result.rows.append(
        MetricRow(f"{prefix}pass^{repeats}", pass_hat_k(per_item, repeats), len(per_item))
    )
    result.rows.append(MetricRow(f"{prefix}pass^1", pass_hat_k(per_item, 1), len(per_item)))


# --------------------------------------------------------------------------- report


def _fmt(value: float) -> str:
    return (
        "n/a"
        if value is None or (isinstance(value, float) and math.isnan(value))
        else f"{value:.4f}"
    )


def render_report(
    results: Sequence[TaskResult],
    *,
    split: str,
    repeats: int,
    source: str,
    git_sha: str,
    git: Mapping[str, Any] | None = None,
) -> str:
    """The Markdown report. ``git`` is :func:`evals.record.git_state`: its ``release`` (the
    nearest release tag, ``none`` when no tag is reachable) and ``dirty`` flag are printed
    under the sha; without it both read ``unknown``."""
    state = git or {}
    release = state.get("release")
    dirty = state.get("dirty")
    lines = [
        "# Basis evaluation report",
        "",
        f"- split: `{split}`",
        f"- repeats: {repeats}",
        f"- data: {source}",
        f"- git sha: `{git_sha}`",
        f"- release: `{release}`" if release else f"- release: {'none' if git else 'unknown'}",
        f"- dirty: {'unknown' if dirty is None else 'yes' if dirty else 'no'}",
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
        for title, headers, body in task.tables:
            lines += ["", f"### {title}", "", "| " + " | ".join(headers) + " |"]
            lines.append("|" + "---|" * len(headers))
            lines += ["| " + " | ".join(cell.replace("|", "/") for cell in r) + " |" for r in body]
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
    manifest: Mapping[str, Any] | None = None,
    run_dir: str | Path | None = None,
) -> list[uuid.UUID]:
    """One ``eval_runs`` row per scored dataset, one ``eval_results`` row per metric.

    ``manifest`` is the ``scores.jsonl`` entry (:func:`evals.record.score_entry`): its git
    ``dirty`` flag and ``release``, and the dataset's label file sha256 and version from
    ``data.labels``, fill the row's columns, and the whole entry is stored in ``manifest``.
    ``run_dir`` is the predictions directory scored (``--predictions``), NULL for a baseline.
    Without a manifest those columns stay NULL."""
    from basis.db.models import EvalResult, EvalRun
    from basis.db.session import session_scope

    git = (manifest or {}).get("git") or {}
    labels = ((manifest or {}).get("data") or {}).get("labels") or {}
    run_ids: list[uuid.UUID] = []
    async with session_scope(sessionmaker) as session:
        for task in results:
            if task.skipped:
                continue
            label = labels.get(task.dataset) or {}
            run = EvalRun(
                dataset=task.dataset,
                split=split,
                git_sha=git_sha,
                model=model or task.system,
                prompt_version=prompt_version,
                git_dirty=git.get("dirty"),
                release=git.get("release"),
                run_dir=None if run_dir is None else str(run_dir),
                label_sha256=label.get("sha256"),
                label_version=label.get("version"),
                manifest=None if manifest is None else dict(manifest),
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


async def _write_to_configured_db(
    results: Sequence[TaskResult],
    split: str,
    git_sha: str,
    *,
    manifest: Mapping[str, Any] | None = None,
    run_dir: Path | None = None,
) -> int:
    from basis.config import load_settings
    from basis.db.engine import create_engine
    from basis.db.session import create_sessionmaker

    engine = create_engine(load_settings())
    try:
        ids = await write_results(
            create_sessionmaker(engine),
            results,
            split=split,
            git_sha=git_sha,
            manifest=manifest,
            run_dir=run_dir,
        )
    finally:
        await engine.dispose()
    return len(ids)


# --------------------------------------------------------------------------- main


def git_sha() -> str:
    """The short sha the report and the ``eval_runs.git_sha`` column name (``GIT_SHA`` from the
    environment where there is no checkout). :func:`evals.record.git_state` gives the full
    state (dirty flag, release tag) for the manifest."""
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
    detection_threshold: float = 0.5,
    inventory_file: str = INVENTORY_FILE,
    corpus_text: str = "pymupdf",
    retrieval_systems: Sequence[str] = ("bm25_pages",),
    embedder: Embedder | None = None,
    fusion_sweep: bool = False,
) -> list[TaskResult]:
    """Score every dataset in ``data``.

    ``retrieval_systems`` (``--retrieval-systems``) chooses the retrieval_qrels systems;
    with more than one, a paired comparison follows them. ``embedder`` is the chunk
    systems' embedder (default: the production one from settings).
    """
    unknown = set(retrieval_systems) - set(RETRIEVAL_SYSTEMS)
    if unknown or not retrieval_systems:
        raise ValueError(f"unknown retrieval systems {sorted(unknown)}; known {RETRIEVAL_SYSTEMS}")
    results: list[TaskResult] = []
    rooms = data.get("room_inventory")
    room_labels = rooms if isinstance(rooms, ds.RoomInventorySet) else None
    for name in ds.DATASETS:  # the dataset files in order, then the derived rule_citations
        dataset = data.get(name)
        if dataset is None:
            results.append(TaskResult(name, "-", notes=["dataset file not found"], skipped=True))
            continue
        if name in ("room_inventory", "report_rows"):
            saved = _load_predictions(predictions, Path(inventory_file).stem)
        else:
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
            if saved is not None or tuple(retrieval_systems) == ("bm25_pages",):
                results.append(
                    score_retrieval(dataset, split, repeats, saved, corpus_dir, corpus_text)
                )
                continue
            scored: list[TaskResult] = []
            if "bm25_pages" in retrieval_systems:
                scored.append(
                    score_retrieval(dataset, split, repeats, None, corpus_dir, corpus_text)
                )
            chunk_systems = [s for s in retrieval_systems if s != "bm25_pages"]
            if chunk_systems:
                scored += score_chunk_systems(
                    dataset, split, corpus_dir, chunk_systems, embedder, fusion_sweep
                )
            results += scored
            ranked = [r for r in scored if r.dataset == "retrieval_qrels"]
            if sum(1 for r in ranked if not r.skipped) > 1:
                results.append(paired_comparison(ranked))
        elif isinstance(dataset, ds.RuleCitations):
            results.append(
                score_rule_citations(
                    dataset,
                    corpus_dir,
                    CORPUS_TEXT[corpus_text],
                    _corpus_system("page_text", corpus_text),
                )
            )
        elif isinstance(dataset, ds.RoomInventorySet):
            labels = data.get("photo_components")
            results.append(
                score_room_inventory(
                    dataset,
                    split,
                    repeats,
                    saved,
                    labels if isinstance(labels, ds.PhotoComponentsSet) else None,
                )
            )
        elif isinstance(dataset, ds.DetectionBoxes):
            results.append(
                score_detection_boxes(dataset, split, repeats, saved, detection_threshold)
            )
        elif isinstance(dataset, ds.ReportRows):
            if saved is not None:
                materials = load_room_materials(predictions, inventory_file)
            elif room_labels is not None:
                labels = data.get("photo_components")
                materials = gold_materials(
                    room_labels, labels if isinstance(labels, ds.PhotoComponentsSet) else None
                )
            else:
                materials = None
            results.append(
                score_report_rows(dataset, split, repeats, saved, room_labels, materials)
            )
        elif isinstance(dataset, ds.PhotoCounts):
            counts = saved if saved is not None else _counts_from_raw(predictions)
            results.append(score_photo_counts(dataset, split, repeats, counts))
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
    parser.add_argument(
        "--detection-threshold",
        type=float,
        default=0.5,
        help="score at or above which a detection counts (counts, precision, recall)",
    )
    parser.add_argument(
        "--inventory-file",
        default=INVENTORY_FILE,
        help="the room inventory file in --predictions to score (room_inventory and "
        f"report_rows); {HEURISTIC_INVENTORY_FILE} is the heuristic merge",
    )
    parser.add_argument(
        "--corpus-text",
        default="pymupdf",
        choices=tuple(CORPUS_TEXT),
        help="page text for retrieval_qrels and rule_citations: pymupdf (parser-independent "
        "baseline) or parser (the production parse + chunk path)",
    )
    parser.add_argument(
        "--retrieval-systems",
        default="bm25_pages",
        help="comma-separated retrieval_qrels systems: "
        + ", ".join(RETRIEVAL_SYSTEMS)
        + " (or 'all'); the chunk systems use the production parser, chunker, embedder "
        "(EMBEDDING_MODEL) and fusion (RETRIEVAL_FUSION, RETRIEVAL_BM25_WEIGHT); two or "
        "more add a paired bootstrap comparison",
    )
    parser.add_argument(
        "--fusion-sweep",
        action="store_true",
        help="with hybrid: also score weighted fusion at BM25 weights 0.0-1.0 and RRF, "
        "with a leave-one-out estimate of choosing the weight",
    )
    args = parser.parse_args(argv)
    systems = (
        list(RETRIEVAL_SYSTEMS)
        if args.retrieval_systems == "all"
        else [s.strip() for s in args.retrieval_systems.split(",") if s.strip()]
    )
    unknown = [s for s in systems if s not in RETRIEVAL_SYSTEMS]
    if unknown or not systems or len(set(systems)) != len(systems):
        parser.error(
            f"--retrieval-systems takes distinct names from {', '.join(RETRIEVAL_SYSTEMS)} or 'all'"
        )
    args.retrieval_systems = systems
    if args.fusion_sweep and "hybrid" not in systems:
        parser.error("--fusion-sweep needs hybrid in --retrieval-systems")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if Path(args.inventory_file).name != args.inventory_file or not args.inventory_file.endswith(
        ".json"
    ):
        parser.error("--inventory-file must be a .json file name, not a path")
    if not 0.0 <= args.detection_threshold <= 1.0:
        parser.error("--detection-threshold must be in [0, 1]")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        data = ds.synthetic_smoke()
        corpus_dir: Path | None = ds.SYNTHETIC_DIR / "corpus"
        source = "synthetic smoke dataset (tests/fixtures/synthetic)"
        report_dir = Path(tempfile.gettempdir())
        data_dir = ds.SYNTHETIC_DIR
    else:
        root = ds.data_dir(args.data_dir)
        data = ds.load_available(root)
        corpus_dir = root / "corpus"
        files = sum(1 for name in data if name in ds.DATASET_FILES)
        source = f"{ds.ENV_VAR} ({files} dataset files; derived: {', '.join(ds.DERIVED_DATASETS)})"
        report_dir = root / "reports"
        data_dir = root
    sha = git_sha()
    state = record.git_state()
    results = evaluate(
        data,
        split=args.split,
        repeats=args.repeats,
        predictions=args.predictions,
        corpus_dir=corpus_dir,
        detection_threshold=args.detection_threshold,
        inventory_file=args.inventory_file,
        corpus_text=args.corpus_text,
        retrieval_systems=args.retrieval_systems,
        fusion_sweep=args.fusion_sweep,
    )
    # The run record (evals.record): which label files, at which version and content, these
    # scores refer to, on which code. Appended to the predictions directory's scores.jsonl
    # when one was scored; a baseline has no run directory, so the entry only reaches the
    # database row.
    entry = record.score_entry(
        results,
        harness=__name__,
        data_dir=data_dir,
        label_files=ds.DATASET_FILES,
        split=args.split,
        repeats=args.repeats,
        options={k: v for k, v in vars(args).items() if not k.startswith("_")},
    )
    entry["git"] = state  # one git state per run: the report and the rows agree
    report = render_report(
        results, split=args.split, repeats=args.repeats, source=source, git_sha=sha, git=state
    )
    path = args.report or report_dir / f"basis-eval-{args.split}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    print(f"report written to {path}")
    if args.predictions is not None:
        scores = record.append_scores(args.predictions, entry)
        print(f"scores appended to {scores}")
    if os.environ.get("DATABASE_URL") and not args.no_db:
        written = asyncio.run(
            _write_to_configured_db(
                results, args.split, sha, manifest=entry, run_dir=args.predictions
            )
        )
        print(f"{written} eval_runs rows written")
    for task in results:
        headline = ", ".join(f"{r.metric}={_fmt(r.value)}" for r in task.rows[:3])
        print(f"{task.dataset} [{task.system}]: {'skipped' if task.skipped else headline}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["evaluate", "main", "mean", "render_report", "write_results"]
