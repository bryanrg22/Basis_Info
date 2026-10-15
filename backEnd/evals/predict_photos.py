"""``python -m evals.predict_photos``: run the photo model on a directory and save predictions.

Runs :func:`basis.vision.analyze.analyze_photo_detailed` (the activity's own pipeline:
image preparation, the released ``photo_analysis`` prompt, structured output, one retry
on bad output) on every photo in ``--photos`` with the providers configured in the
environment, and writes what ``evals.run --predictions`` scores::

    <out>/photo_rooms.json        {"<file>": "<room_type>" | {"repeats": [...]}}
    <out>/photo_components.json   {"<file>": ["label", ...] | {"repeats": [...]}}
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
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from basis.config import load_settings
from basis.llm import prompts
from basis.llm.call import CallContext
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


async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    pool = ProviderPool.from_settings(settings)
    if pool is None:
        print("no model provider configured (OPENAI_API_KEY or AZURE_OPENAI_*)", file=sys.stderr)
        return 2
    files = photo_files(args.photos)
    if not files:
        print(f"no photos in {args.photos}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    budget = Budget(remaining_usd=args.budget_usd)
    recorder = MemoryRecorder()
    ctx = VisionContext(
        call=CallContext(
            study_id=uuid.uuid4(),
            run_id=None,
            activity="evals.predict_photos",
            budget=budget,
            recorder=recorder,
        ),
        provider=pool,
        prompt=(prompts.load("photo_analysis", args.prompt) if args.prompt else released_prompt()),
        model=args.model or settings.vision_model,
    )
    semaphore = asyncio.Semaphore(args.concurrency)
    raw_path = args.out / "photo_raw.jsonl"
    rooms: dict[str, list[str]] = {f.name: [] for f in files}
    components: dict[str, list[list[str]]] = {f.name: [] for f in files}

    async def one(file: Path, repeat: int) -> dict[str, Any]:
        async with semaphore:
            started = time.monotonic()
            try:
                result = await analyze_photo_detailed(file.read_bytes(), ctx)
            except ImageRejected as exc:
                return {"file": file.name, "repeat": repeat, "rejected": str(exc)}
            return _record(file.name, repeat, result, time.monotonic() - started)

    with raw_path.open("w", encoding="utf-8") as raw:
        for repeat in range(1, args.repeats + 1):
            records = await asyncio.gather(*(one(f, repeat) for f in files))
            for record in records:
                raw.write(json.dumps(record, sort_keys=True) + "\n")
                analysis = record.get("analysis")
                if analysis is None:
                    continue
                rooms[record["file"]].append(analysis["room_type"])
                components[record["file"]].append(
                    sorted({c["label"] for c in analysis["components"]})
                )
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
    models = sorted({str(getattr(r, "model", "")) for r in recorder.records} - {""})
    (args.out / "run.json").write_text(
        json.dumps(
            {
                "photos": len(files),
                "repeats": args.repeats,
                "vision_model_setting": settings.vision_model,
                "model_requested": args.model or settings.vision_model,
                "models_reported": models,
                "spent_usd": round(budget.spent_usd, 6),
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
