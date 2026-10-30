"""``python -m evals.predict_fields``: run the field-extraction model on the labelled fields.

For every document of ``appraisal_fields_v1.json`` (its ``document`` is a PDF path under the
data directory), the text of every page is read with the extraction pipeline's own reader
(:func:`basis.extraction.pdf_text.read_pdf`) and the model is asked for *every* labelled
field in one call with the released ``appraisal_field_fallback`` prompt, the production
request format (:func:`basis.extraction.fallback.request_content`, pages in
``<page number=N>`` tags) and the production structured answer
(:class:`~basis.extraction.fallback.FallbackAnswer`). In production the same call is asked
only for the critical fields Azure DI and the regex pass missed; asking for all of them
measures the model on the whole form, with the whole document as context
(``--max-context-chars``, default large enough for the client appraisal's 28k characters).

A field's caption is the catalogue label (:data:`basis.extraction.field_mappings.FIELD_BY_KEY`)
when the key is catalogued, otherwise its section and name in words; its type is the
label's. Values pass the production verbatim guard
(:func:`basis.extraction.fallback.appears_in`): a value that is not in the page text is
dropped, as production drops it, and scores as a miss. Writes::

    <out>/appraisal_fields.json             {"<document>": {"<field>": value | {"repeats": [...]}}}
    <out>/appraisal_fields.unguarded.json   the same before the guard (for the drop analysis)
    <out>/fields_raw.jsonl                  one line per (document, repeat): the answer, the
                                            drops, tokens, cost, latency
    <out>/run.json                          models reported, effort, spend, latency, errors
    <out>/llm_calls.jsonl, llm/, run.log, manifest.json         the run record (evals.record)

Usage (from ``backEnd/``, ``BASIS_ENV=dev``, provider keys in the environment)::

    python -m evals.predict_fields --out out/fields_x --model gpt-6.1-sol \
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
from pathlib import Path
from typing import Any

from basis.config import load_settings
from basis.extraction.fallback import (
    PROMPT_NAME,
    FallbackAnswer,
    appears_in,
    page_context,
    request_content,
)
from basis.extraction.field_mappings import FIELD_BY_KEY
from basis.extraction.pdf_text import read_pdf
from basis.llm import prompts
from basis.llm.call import CallContext, call_model
from basis.llm.cost import is_anthropic_model
from basis.llm.errors import ProviderAccountError, ProviderError
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget, ChatRequest, Message
from evals import datasets as ds
from evals.account_stop import guard, stops_on_account_error, track
from evals.record import EvalRun

DEFAULT_MAX_CONTEXT_CHARS = 60_000
DEFAULT_MAX_TOKENS = 8_000


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.predict_fields", description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None, help="default BASIS_EVAL_DATA_DIR")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--model", required=True, help="model name, e.g. gpt-6.1-sol")
    parser.add_argument("--reasoning-effort", default=None, help="reasoning models: low, ...")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--max-context-chars", type=int, default=DEFAULT_MAX_CONTEXT_CHARS)
    parser.add_argument("--prompt", default=None, help="released prompt version (default latest)")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--budget-usd", type=float, default=2.0, help="stop when exceeded")
    parser.add_argument("--split", default="dev")
    return parser.parse_args(argv)


def caption(key: str) -> str:
    """The printed caption of a catalogued field, else its section and name in words."""
    spec = FIELD_BY_KEY.get(key)
    if spec is not None:
        return spec.label
    section, _, name = key.partition(".")
    return f"{section.replace('_', ' ')}: {name.replace('_', ' ')}"


def build_request(
    labels: Sequence[ds.FieldLabel],
    context: str,
    *,
    prompt: prompts.Prompt,
    model: str,
    max_tokens: int,
    reasoning_effort: str | None,
) -> ChatRequest:
    return ChatRequest(
        model=model,
        system=prompt.text,
        messages=(
            Message(
                role="user",
                content=request_content(
                    [(label.field, caption(label.field), label.type) for label in labels], context
                ),
            ),
        ),
        response_model=FallbackAnswer,
        max_tokens=max_tokens,
        temperature=0.0,
        reasoning_effort=reasoning_effort,
        prompt_version=prompt.version_id,
        study_id="evals.predict_fields",
        activity="evals.predict_fields",
    )


def accept(
    answer: FallbackAnswer, asked: Sequence[str], context: str
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """``(accepted, unguarded, dropped)``: the production guards on one answer.

    A key not asked or repeated, a null or empty value, or a value not in the page text
    is dropped with its reason; ``unguarded`` keeps every non-empty value of an asked key.
    """
    accepted: dict[str, str] = {}
    unguarded: dict[str, str] = {}
    dropped: dict[str, str] = {}
    wanted = set(asked)
    for item in answer.fields:
        if item.key not in wanted:
            dropped[item.key] = "not_asked"
            continue
        if item.key in unguarded or item.key in dropped:
            dropped[item.key] = "duplicate"
            continue
        if item.value is None or not item.value.strip():
            dropped[item.key] = "not_found"
            continue
        value = item.value.strip()
        unguarded[item.key] = value
        if not appears_in(value, context):
            dropped[item.key] = "not_in_text"
            continue
        accepted[item.key] = value
    return accepted, unguarded, dropped


def fold(values: list[Any]) -> Any:
    return values[0] if len(values) == 1 else {"repeats": values}


@stops_on_account_error
async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    pool = guard(ProviderPool.from_settings(settings))
    if pool is None:
        print("no model provider configured", file=sys.stderr)
        return 2
    data_dir = args.data_dir or Path(os.environ.get("BASIS_EVAL_DATA_DIR", ""))
    data = ds.AppraisalFields.model_validate_json(
        (data_dir / "appraisal_fields_v1.json").read_text("utf-8")
    )
    documents = [d for d in data.documents if d.split == args.split]
    if not documents:
        print(f"no {args.split} documents", file=sys.stderr)
        return 2
    prompt = prompts.load(PROMPT_NAME, args.prompt or prompts.versions(PROMPT_NAME)[-1])
    eval_run = EvalRun.begin(args.out, harness=__name__, args=args, settings=settings)
    eval_run.dataset(data_dir / "appraisal_fields_v1.json")
    eval_run.prompt(prompt)
    budget = Budget(remaining_usd=args.budget_usd)
    track(args, budget)
    recorder = eval_run.recorder
    ctx = CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="evals.predict_fields",
        budget=budget,
        recorder=recorder,
    )
    guarded: dict[str, dict[str, list[Any]]] = {}
    raw_values: dict[str, dict[str, list[Any]]] = {}
    errors: list[dict[str, Any]] = []
    seconds: list[float] = []
    with (args.out / "fields_raw.jsonl").open("w", encoding="utf-8") as raw:
        for doc in documents:
            eval_run.input(doc.document, data_dir / doc.document)
            pdf = read_pdf((data_dir / doc.document).read_bytes())
            context, used = page_context(
                pdf.pages, range(1, len(pdf.pages) + 1), args.max_context_chars
            )
            keys = [label.field for label in doc.fields]
            request = build_request(
                doc.fields,
                context,
                prompt=prompt,
                model=args.model,
                max_tokens=args.max_tokens,
                reasoning_effort=args.reasoning_effort,
            )
            guarded[doc.document] = {k: [] for k in keys}
            raw_values[doc.document] = {k: [] for k in keys}
            for repeat in range(1, args.repeats + 1):
                started = time.monotonic()
                calls_before = len(recorder.records)
                try:
                    response = await call_model(pool, request, ctx)
                except ProviderAccountError:
                    raise  # stops the run (evals.account_stop)
                except ProviderError as exc:
                    errors.append({"document": doc.document, "repeat": repeat, "error": str(exc)})
                    print(f"repeat {repeat}: {type(exc).__name__}: {exc}", file=sys.stderr)
                    for k in keys:
                        guarded[doc.document][k].append(None)
                        raw_values[doc.document][k].append(None)
                    continue
                elapsed = time.monotonic() - started
                seconds.append(elapsed)
                answer = response.parsed
                assert isinstance(answer, FallbackAnswer)
                accepted, unguarded, dropped = accept(answer, keys, context)
                for k in keys:
                    guarded[doc.document][k].append(accepted.get(k))
                    raw_values[doc.document][k].append(unguarded.get(k))
                calls = recorder.records[calls_before:]
                raw.write(
                    json.dumps(
                        {
                            "document": doc.document,
                            "repeat": repeat,
                            "pages_sent": used,
                            "answer": answer.model_dump(mode="json"),
                            "dropped": dropped,
                            "model": response.model,
                            "provider": response.provider,
                            "input_tokens": response.usage.input_tokens,
                            "output_tokens": response.usage.output_tokens,
                            "cost_usd": float(sum(c.cost_usd for c in calls)),
                            "seconds": round(elapsed, 2),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                print(
                    f"{doc.document} repeat {repeat}/{args.repeats}: {len(accepted)} accepted, "
                    f"{len(dropped)} dropped, spent ${budget.spent_usd:.4f}",
                    file=sys.stderr,
                )
    for name, values in (
        ("appraisal_fields.json", guarded),
        ("appraisal_fields.unguarded.json", raw_values),
    ):
        (args.out / name).write_text(
            json.dumps(
                {d: {k: fold(v) for k, v in fields.items()} for d, fields in values.items()},
                indent=1,
                sort_keys=True,
            )
        )
    records = recorder.records
    report = {
        "documents": len(documents),
        "fields": sum(len(d.fields) for d in documents),
        "repeats": args.repeats,
        "model_requested": args.model,
        "reasoning_effort": args.reasoning_effort
        or (settings.anthropic_effort if is_anthropic_model(args.model) else None),
        "max_tokens": args.max_tokens,
        "prompt": prompt.version_id,
        "models_reported": sorted({r.model for r in records}),
        "providers_reported": sorted({r.provider for r in records}),
        "model_calls": len(records),
        "input_tokens": sum(r.usage.input_tokens for r in records),
        "output_tokens": sum(r.usage.output_tokens for r in records),
        "spent_usd": round(float(sum(r.cost_usd for r in records)), 6),
        "seconds_per_call": round(sum(seconds) / len(seconds), 2) if seconds else None,
        "errors": errors,
    }
    print(f"wrote {args.out} (spent ${budget.spent_usd:.4f})", file=sys.stderr)
    eval_run.finish(report)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
