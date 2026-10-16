"""``python -m evals.predict_assets``: run the residual classifier on the ``assets`` rows.

Every labelled row of ``assets_v1.json`` goes through the production model path of
:func:`basis.classify.router.classify` (the bounded tool loop of
:mod:`basis.classify.residual` with the released ``classify`` prompt, then citation
verification and the review policy), with the rules table and the cache bypassed: the
question is how well a model classifies, and the rules table is scored on its own
(``evals.run``'s ``rules_table`` system). The reference corpus is the IRS PDFs of
``<data dir>/corpus`` (``--corpus-glob``, default ``IRS_*.pdf``), read page by page with
pymupdf and cut into the ingest pipeline's 400-token windows with 80 tokens of overlap
(:func:`basis.retrieval.chunk.split_text`), searched by BM25
(:class:`basis.classify.store_protocol.InMemoryChunkStore`); production adds a vector
leg to the search, which this runner does not have. Writes what ``evals.run
--predictions`` scores::

    <out>/assets.json        {"<id>": {"section", "bucket", "citations": {"claimed",
                              "verified"}} | {"repeats": [...]}}
    <out>/assets_raw.jsonl   one line per (id, repeat): the result, review reasons, tool
                              calls, dropped citations, model calls with tokens and cost
    <out>/run.json           models reported, effort, spend, latency, errors

A row whose loop failed gets the pipeline's fallback (section 1250, the building bucket,
``needs_review``), exactly what production would write, and is scored as such.

Usage (from ``backEnd/``, ``BASIS_ENV=dev``, provider keys in the environment)::

    python -m evals.predict_assets --out out/assets_x --model gpt-6.1-sol \
        --reasoning-effort low --repeats 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from basis.classify.residual import DEFAULT_LIMITS
from basis.classify.router import ClassificationResult, ClassifyContext, classify_with_model
from basis.classify.schemas import ComponentView
from basis.classify.store_protocol import InMemoryChunkStore, StoredChunk
from basis.config import load_settings
from basis.llm.cost import cost_usd, is_anthropic_model
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget, ChatProvider, ChatRequest, ChatResponse
from basis.retrieval.chunk import split_text
from evals import datasets as ds

REFERENCE_VERSION = uuid.UUID(int=2)


class ErrorLog:
    """The pool, with every provider error's text kept: the loop reports only its type."""

    def __init__(self, provider: ChatProvider) -> None:
        self._provider = provider
        self.name = provider.name
        self.errors: list[dict[str, str]] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        try:
            return await self._provider.complete(request)
        except Exception as exc:
            self.errors.append(
                {"study": request.study_id, "error": f"{type(exc).__name__}: {exc}"[:500]}
            )
            raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.predict_assets", description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None, help="default BASIS_EVAL_DATA_DIR")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--model", required=True, help="model name, e.g. gpt-6.1-sol")
    parser.add_argument("--reasoning-effort", default=None, help="reasoning models: low, ...")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_LIMITS.max_tokens,
        help="output budget per model call (raise it for reasoning models)",
    )
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_LIMITS.timeout_s)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--budget-usd", type=float, default=5.0, help="stop when exceeded")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--limit", type=int, default=None, help="first N rows only (smoke)")
    parser.add_argument("--corpus-glob", default="IRS_*.pdf")
    return parser.parse_args(argv)


def corpus_store(corpus_dir: Path, pattern: str) -> InMemoryChunkStore:
    """The reference corpus: 400-token windows of every page of the matching PDFs."""
    import pymupdf  # dev/eval dependency

    store = InMemoryChunkStore()
    for path in sorted(corpus_dir.glob(pattern)):
        doc_id = path.stem
        with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
            pages = [page.get_text() for page in document]
        for number, text in enumerate(pages, start=1):
            for ordinal, (_, chunk, _) in enumerate(split_text(text)):
                key = f"{doc_id}:{number}:{ordinal}"
                store.add(
                    StoredChunk(
                        id=uuid.uuid5(REFERENCE_VERSION, key),
                        corpus_version_id=REFERENCE_VERSION,
                        doc_id=doc_id,
                        page_start=number,
                        page_end=number,
                        chunk_key=key,
                        text=chunk,
                    )
                )
    return store


def component_view(asset: ds.AssetLabel) -> ComponentView:
    return ComponentView(
        component_id=asset.id,
        label=asset.component,
        material=asset.material,
        attachment=asset.attachment,
        property_class=asset.property_class,
    )


def prediction(result: ClassificationResult) -> dict[str, Any]:
    """What ``evals.run`` scores for one row: section, bucket and the citation counts."""
    return {
        "section": result.section,
        "bucket": result.bucket.value,
        "citations": {
            "claimed": len(result.citations) + len(result.dropped),
            "verified": len(result.citations),
        },
    }


def record(
    asset_id: str, repeat: int, result: ClassificationResult, seconds: float
) -> dict[str, Any]:
    return {
        "id": asset_id,
        "repeat": repeat,
        "prediction": prediction(result),
        "category": result.category,
        "asset_class": result.asset_class,
        "confidence": result.confidence,
        "needs_review": result.needs_review,
        "review_reasons": list(result.review_reasons),
        "rationale": result.rationale,
        "citations": [
            {"doc_id": c.doc_id, "page": c.page_start, "excerpt": c.excerpt}
            for c in result.citations
        ],
        "dropped": [{"citation": d.chunk_id, "reason": d.reason} for d in result.dropped],
        "tool_calls": [
            {"name": t.name, "arguments": t.arguments, "refused": t.refused}
            for t in result.tool_calls
        ],
        "calls": [
            {
                "provider": c.provider,
                "model": c.model,
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
                "cost_usd": c.cost_usd,
                "latency_ms": c.latency_ms,
            }
            for c in result.calls
        ],
        "cost_usd": round(result.cost_usd, 6),
        "seconds": round(seconds, 2),
    }


def fold(values: list[Any]) -> Any:
    return values[0] if len(values) == 1 else {"repeats": values}


async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    configured = ProviderPool.from_settings(settings)
    if configured is None:
        print("no model provider configured", file=sys.stderr)
        return 2
    pool = ErrorLog(configured)
    data_dir = args.data_dir or Path(os.environ.get("BASIS_EVAL_DATA_DIR", ""))
    data = ds.Assets.model_validate_json((data_dir / "assets_v1.json").read_text("utf-8"))
    assets = [a for a in data.assets if a.split == args.split][: args.limit]
    store = corpus_store(data_dir / "corpus", args.corpus_glob)
    if not store.chunks:
        print(f"no corpus PDFs match {args.corpus_glob} in {data_dir / 'corpus'}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    budget = Budget(remaining_usd=args.budget_usd)
    limits = replace(DEFAULT_LIMITS, max_tokens=args.max_tokens, timeout_s=args.timeout_s)

    def context(asset: ds.AssetLabel) -> ClassifyContext:
        return ClassifyContext(
            study_id=f"eval-{asset.id}",
            property_class=asset.property_class,
            budget=budget,
            store=store,
            reference_version_id=REFERENCE_VERSION,
            provider=pool,
            model=args.model,
            limits=limits,
            price=cost_usd,
            reasoning_effort=args.reasoning_effort,
        )

    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(asset: ds.AssetLabel, repeat: int) -> dict[str, Any]:
        async with semaphore:
            started = time.monotonic()
            result = await classify_with_model(component_view(asset), context(asset))
            return record(asset.id, repeat, result, time.monotonic() - started)

    predictions: dict[str, list[dict[str, Any]]] = {a.id: [] for a in assets}
    records: list[dict[str, Any]] = []
    with (args.out / "assets_raw.jsonl").open("w", encoding="utf-8") as raw:
        for repeat in range(1, args.repeats + 1):
            batch = await asyncio.gather(*(one(a, repeat) for a in assets))
            for item in batch:
                raw.write(json.dumps(item, sort_keys=True, default=str) + "\n")
                predictions[item["id"]].append(item["prediction"])
            records += batch
            print(
                f"repeat {repeat}/{args.repeats}: {len(batch)} rows, spent ${budget.spent_usd:.4f}",
                file=sys.stderr,
            )
    (args.out / "assets.json").write_text(
        json.dumps({k: fold(v) for k, v in predictions.items()}, indent=1, sort_keys=True)
    )
    (args.out / "run.json").write_text(
        json.dumps({**summary(args, settings, records), "provider_errors": pool.errors}, indent=1)
    )
    print(f"wrote {args.out} (spent ${budget.spent_usd:.4f})", file=sys.stderr)
    return 0


def summary(
    args: argparse.Namespace, settings: Any, records: list[dict[str, Any]]
) -> dict[str, Any]:
    calls = [c for r in records for c in r["calls"]]
    failures = [
        {"id": r["id"], "repeat": r["repeat"], "reasons": r["review_reasons"]}
        for r in records
        if r["confidence"] == 0.0 and r["category"].startswith("Unclassified")
    ]
    return {
        "rows": len({r["id"] for r in records}),
        "repeats": args.repeats,
        "model_requested": args.model,
        "reasoning_effort": args.reasoning_effort
        or (settings.anthropic_effort if is_anthropic_model(args.model) else None),
        "max_tokens": args.max_tokens,
        "corpus_glob": args.corpus_glob,
        "models_reported": sorted({c["model"] for c in calls}),
        "providers_reported": sorted({c["provider"] for c in calls}),
        "model_calls": len(calls),
        "input_tokens": sum(c["input_tokens"] for c in calls),
        "output_tokens": sum(c["output_tokens"] for c in calls),
        "spent_usd": round(sum(r["cost_usd"] for r in records), 6),
        "seconds_per_row": round(sum(r["seconds"] for r in records) / max(len(records), 1), 2),
        "loop_failures": failures,
    }


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
