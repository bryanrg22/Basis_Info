"""``python -m evals.predict_photos``: run the photo model on a directory and save predictions.

Runs :func:`basis.vision.analyze.analyze_photo_detailed` (the activity's own pipeline:
image preparation, the released ``photo_analysis`` prompt, structured output, one retry
on bad output) on every photo in ``--photos`` with the providers configured in the
environment, and writes what ``evals.run --predictions`` scores::

    <out>/photo_rooms.json        {"<file>": "<room_type>" | {"repeats": [...]}}
    <out>/photo_components.json   {"<file>": ["label", ...] | {"repeats": [...]}}
    <out>/photo_counts.json       {"<file>": {"label": count} | {"repeats": [...]}}
    <out>/photo_raw.jsonl         one line per (file, repeat): the full analysis, model,
                                  provider, attempts, errors, estimated cost

Usage (from ``backEnd/``, with ``BASIS_ENV=dev`` and the provider credentials in the
environment)::

    python -m evals.predict_photos --photos ~/basis-eval-data/inputs/photos --out out/vision
    python -m evals.predict_photos --photos ... --out ... --repeats 3 --concurrency 4

``--repeats N`` analyzes every photo N times (temperature is 0, but providers are not
deterministic) so ``evals.run`` can report pass^k. Nothing here touches the database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from basis.config import load_settings
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.cost import is_anthropic_model
from basis.llm.errors import ProviderRateLimited, ProviderUnavailable
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget
from basis.llm.recorder import MemoryRecorder
from basis.vision.analyze import (
    PhotoResult,
    VisionContext,
    analyze_photo_detailed,
    released_prompt,
)
from basis.vision.prep import ImageRejected

PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def photo_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in PHOTO_SUFFIXES)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.predict_photos", description=__doc__)
    parser.add_argument("--photos", type=Path, required=True, help="directory of photos")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--budget-usd", type=float, default=10.0, help="stop when exceeded")
    parser.add_argument(
        "--prompt", default=None, help="released photo_analysis version, e.g. v2 (default: latest)"
    )
    parser.add_argument("--model", default=None, help="model name (default: VISION_MODEL)")
    parser.add_argument(
        "--reasoning-effort",
        default=None,
        help="reasoning models only: none/minimal/low/medium/high (default: the model's)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="output budget per call (default: the pipeline's; raise it for reasoning models)",
    )
    parser.add_argument(
        "--prompt-name",
        default="photo_analysis",
        help="prompt family: photo_analysis (default), photo_countables or photo_surfaces",
    )
    return parser.parse_args(argv)


def _record(file: str, repeat: int, result: PhotoResult, seconds: float) -> dict[str, Any]:
    return {
        "file": file,
        "repeat": repeat,
        "analysis": result.analysis.model_dump(mode="json"),
        "provider": result.provider,
        "model": result.model,
        "attempts": result.attempts,
        "errors": result.errors,
        "seconds": round(seconds, 2),
        "image": {
            "width": result.image.width,
            "height": result.image.height,
            "original_width": result.image.original_width,
            "original_height": result.image.original_height,
        },
    }


RETRY_DELAYS_S: tuple[float, ...] = (2.0, 6.0, 15.0)


def label_counts(components: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """``{label: count}`` with same-label entries added up (``photo_counts`` predictions)."""
    counts: dict[str, int] = {}
    for component in components:
        label = str(component["label"])
        counts[label] = counts.get(label, 0) + int(component.get("count", 1))
    return counts


async def analyze_with_retries(
    image_bytes: bytes, ctx: VisionContext, delays: Sequence[float] | None = None
) -> PhotoResult:
    """``analyze_photo_detailed`` with a bounded retry on rate limits and outages.

    The activity relies on Temporal's retry policy for these; the runner has none, so a
    single provider timeout would otherwise abort a whole evaluation run.
    """
    for delay in RETRY_DELAYS_S if delays is None else delays:
        try:
            return await analyze_photo_detailed(image_bytes, ctx)
        except (ProviderRateLimited, ProviderUnavailable) as exc:
            print(f"transient provider error, retrying in {delay:g}s: {exc}", file=sys.stderr)
            await asyncio.sleep(delay)
    return await analyze_photo_detailed(image_bytes, ctx)


async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    pool = ProviderPool.from_settings(settings)
    if pool is None:
        print(
            "no model provider configured (OPENAI_API_KEY, ANTHROPIC_API_KEY or AZURE_OPENAI_*)",
            file=sys.stderr,
        )
        return 2
    files = photo_files(args.photos)
    if not files:
        print(f"no photos in {args.photos}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    budget = Budget(remaining_usd=args.budget_usd)
    recorder = MemoryRecorder()
    model = args.model or settings.vision_model
    ctx = VisionContext(
        call=CallContext(
            study_id=uuid.uuid4(),
            run_id=None,
            activity="evals.predict_photos",
            budget=budget,
            recorder=recorder,
        ),
        provider=pool,
        prompt=(
            prompts.load(args.prompt_name, args.prompt or prompts.versions(args.prompt_name)[-1])
            if args.prompt or args.prompt_name != "photo_analysis"
            else released_prompt()
        ),
        model=model,
        reasoning_effort=args.reasoning_effort,
        **({"max_tokens": args.max_tokens} if args.max_tokens else {}),
    )
    semaphore = asyncio.Semaphore(args.concurrency)
    raw_path = args.out / "photo_raw.jsonl"
    errors_seen: list[dict[str, Any]] = []
    rooms: dict[str, list[str]] = {f.name: [] for f in files}
    components: dict[str, list[list[str]]] = {f.name: [] for f in files}
    counts: dict[str, list[dict[str, int]]] = {f.name: [] for f in files}

    async def one(file: Path, repeat: int) -> dict[str, Any]:
        async with semaphore:
            started = time.monotonic()
            try:
                result = await analyze_with_retries(file.read_bytes(), ctx)
            except ImageRejected as exc:
                return {"file": file.name, "repeat": repeat, "rejected": str(exc)}
            except (ProviderRateLimited, ProviderUnavailable) as exc:
                # Transient after every retry: recorded, not fatal (Temporal retries the
                # activity in production; here the photo is simply missing from the run).
                return {"file": file.name, "repeat": repeat, "error": str(exc)}
            return _record(file.name, repeat, result, time.monotonic() - started)

    with raw_path.open("w", encoding="utf-8") as raw:
        for repeat in range(1, args.repeats + 1):
            records = await asyncio.gather(*(one(f, repeat) for f in files))
            for record in records:
                raw.write(json.dumps(record, sort_keys=True) + "\n")
                if "error" in record:
                    errors_seen.append(
                        {"file": record["file"], "repeat": repeat, "error": record["error"]}
                    )
                analysis = record.get("analysis")
                if analysis is None:
                    continue
                rooms[record["file"]].append(analysis["room_type"])
                components[record["file"]].append(
                    sorted({c["label"] for c in analysis["components"]})
                )
                counts[record["file"]].append(label_counts(analysis["components"]))
            print(
                f"repeat {repeat}/{args.repeats}: {len(records)} photos, "
                f"spent ${budget.spent_usd:.4f}",
                file=sys.stderr,
            )

    def fold(values: list[Any]) -> Any:
        return values[0] if len(values) == 1 else {"repeats": values}

    (args.out / "photo_rooms.json").write_text(
        json.dumps({f: fold(v) for f, v in rooms.items() if v}, indent=1, sort_keys=True)
    )
    (args.out / "photo_components.json").write_text(
        json.dumps({f: fold(v) for f, v in components.items() if v}, indent=1, sort_keys=True)
    )
    (args.out / "photo_counts.json").write_text(
        json.dumps({f: fold(v) for f, v in counts.items() if v}, indent=1, sort_keys=True)
    )
    models = sorted({str(getattr(r, "model", "")) for r in recorder.records} - {""})
    (args.out / "run.json").write_text(
        json.dumps(
            {
                "photos": len(files),
                "repeats": args.repeats,
                "vision_model_setting": settings.vision_model,
                "model_requested": model,
                # The effort in effect: the flag, else the Anthropic default for a Claude
                # model (ANTHROPIC_EFFORT), else the model's own default (null).
                "reasoning_effort": args.reasoning_effort
                or (settings.anthropic_effort if is_anthropic_model(model) else None),
                "max_tokens": ctx.max_tokens,
                "models_reported": models,
                "spent_usd": round(budget.spent_usd, 6),
                "errors": errors_seen,
                "prompt": ctx.prompt.version_id,
            },
            indent=1,
        )
    )
    print(f"wrote {args.out} (spent ${budget.spent_usd:.4f})", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
