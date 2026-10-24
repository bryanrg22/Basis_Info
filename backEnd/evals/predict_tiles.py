"""``python -m evals.predict_tiles``: count objects on enlarged parts of each photo.

Vision-language models miss small objects (Zhang et al., "MLLMs Know Where to Look", ICLR
2025: accuracy falls with the object's size, and an enlarged crop of the region restores
it). This runner gives the countables prompt enlarged parts of the photo, two ways
(``--mode``):

* ``separate``: the photo is cut into a ``--grid`` x ``--grid`` grid and each part,
  enlarged ``--upscale`` times, is one call to ``photo_countables_tile/v1`` (the countables
  prompt plus a rule for the cuts). The photo's count is the sum over its parts. Summing
  grid parts double-counts objects the cuts go through (the failure LVLM-Count, Qharabagh
  et al., TMLR 2026, removes with object-aware cuts); here each part is told which of its
  edges are cuts and counts a cut object only when its center lies inside the part, the
  grid-cell ownership rule of YOLO (Redmon et al., CVPR 2016), so a cut object is counted
  by exactly one part.
* ``joint``: one call to ``photo_countables_joint/v1`` with the whole photo followed by its
  ``--grid`` x ``--grid`` enlarged parts, asking for one count per object over the whole
  photo (the global-plus-crop input of ViCrop).

Each image goes through :func:`basis.vision.prep.prepare_image` (orientation, metadata
stripped, at most 1024 px) and parts are cut from the prepared photo. Output in ``--out``:
``photo_counts.json`` and ``photo_components.json`` (per photo, per repeat; ``separate``:
summed counts and the union of the labels), ``photo_rooms.json`` (``joint`` only),
``tile_counts.json`` (``separate``: each part's counts, for other merge rules),
``photo_raw.jsonl`` (one line per photo and repeat with every call) and ``run.json``
(models reported, spent). An unusable answer is retried once; a part still unusable after
that makes the photo missing for that repeat (not zero).

Usage (from ``backEnd/``)::

    python -m evals.predict_tiles --photos DIR --out OUT --mode separate --model gpt-6.1-sol \\
        --reasoning-effort low --max-tokens 6000 --repeats 3
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from PIL import Image
from pydantic import BaseModel

from basis.config import load_settings
from basis.llm import prompts
from basis.llm.call import CallContext, call_model
from basis.llm.errors import (
    ProviderBadResponse,
    ProviderRateLimited,
    ProviderRequestRejected,
    ProviderUnavailable,
)
from basis.llm.pool import ProviderPool
from basis.llm.prompts import Prompt
from basis.llm.provider import Budget, ChatProvider, ChatRequest, ImagePart, Message
from basis.llm.recorder import MemoryRecorder
from basis.vision.prep import JPEG_QUALITY, PreparedImage, prepare_image
from basis.vision.schemas import ModelPhotoAnalysis
from evals.account_stop import guard, stops_on_account_error, track
from evals.predict_photos import RETRY_DELAYS_S, label_counts, photo_files

MODES: Final = ("separate", "joint")
PROMPTS: Final = {"separate": "photo_countables_tile", "joint": "photo_countables_joint"}
MAX_ATTEMPTS: Final = 2


@dataclass(frozen=True, slots=True)
class Tile:
    row: int
    col: int
    grid: int
    image: PreparedImage

    @property
    def position(self) -> str:
        if self.grid == 2:
            return f"{('top', 'bottom')[self.row]}-{('left', 'right')[self.col]} quarter"
        return f"part in row {self.row + 1} of {self.grid}, column {self.col + 1} of {self.grid}"

    @property
    def cut_edges(self) -> list[str]:
        """The edges of this part that are cuts through the photo (not its border)."""
        edges = []
        if self.row > 0:
            edges.append("top")
        if self.row < self.grid - 1:
            edges.append("bottom")
        if self.col > 0:
            edges.append("left")
        if self.col < self.grid - 1:
            edges.append("right")
        return edges


def tiles(photo: PreparedImage, grid: int, upscale: float) -> list[Tile]:
    """The ``grid`` x ``grid`` parts of the prepared photo, row by row, each enlarged
    ``upscale`` times (Lanczos) and prepared like a photo (so at most 1024 px a side).
    Integer cut positions; the last row and column take the remainder."""
    if grid < 1:
        raise ValueError("grid must be at least 1")
    if upscale < 1:
        raise ValueError("upscale must be at least 1")
    with Image.open(io.BytesIO(photo.jpeg)) as image:
        image.load()
        width, height = image.size
        xs = [round(width * i / grid) for i in range(grid + 1)]
        ys = [round(height * i / grid) for i in range(grid + 1)]
        out = []
        for r in range(grid):
            for c in range(grid):
                part = image.crop((xs[c], ys[r], xs[c + 1], ys[r + 1]))
                if upscale != 1:
                    size = (round(part.width * upscale), round(part.height * upscale))
                    part = part.resize(size, Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                part.save(buffer, format="JPEG", quality=JPEG_QUALITY)
                out.append(Tile(r, c, grid, prepare_image(buffer.getvalue())))
    return out


def _image(prepared: PreparedImage) -> ImagePart:
    return ImagePart(media_type="image/jpeg", data_base64=prepared.base64, detail="high")


@dataclass
class Settings:
    provider: ChatProvider
    call: CallContext
    prompt: Prompt
    model: str
    max_tokens: int
    reasoning_effort: str | None


def _request(s: Settings, text: str, images: Sequence[PreparedImage]) -> ChatRequest:
    return ChatRequest(
        model=s.model,
        system=s.prompt.text,
        messages=(Message(role="user", content=text, images=tuple(_image(i) for i in images)),),
        response_model=ModelPhotoAnalysis,
        max_tokens=s.max_tokens,
        temperature=0.0,
        reasoning_effort=s.reasoning_effort,
        prompt_version=s.prompt.version_id,
        study_id=str(s.call.study_id),
        activity=s.call.activity,
    )


def tile_text(tile: Tile, upscale: float) -> str:
    cuts = tile.cut_edges
    return (
        f"This image is the {tile.position} of the photo, enlarged {upscale:g} times. "
        + (
            f"Its {', '.join(cuts)} edge{'s are cuts' if len(cuts) > 1 else ' is a cut'} "
            "through the photo; its other edges are the photo's border. "
            if cuts
            else "It is the whole photo. "
        )
        + "Count the countable objects this part shows."
    )


JOINT_TEXT: Final = (
    "The first image is the whole photo; the next are its parts, enlarged {upscale:g} times, "
    "in reading order ({positions}). Count the countable objects of the whole photo, each "
    "object once."
)


async def ask_structured(
    provider: ChatProvider, call: CallContext, request: ChatRequest
) -> tuple[BaseModel | None, int, list[str], str | None]:
    """``(parsed, attempts, errors, model)``: up to :data:`MAX_ATTEMPTS` calls until the
    response parses as ``request.response_model``; transient provider errors are retried
    after :data:`RETRY_DELAYS_S` and then propagate."""
    errors: list[str] = []
    model = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = None
        for delay in (*RETRY_DELAYS_S, None):
            try:
                response = await call_model(provider, request, call)
                break
            except (ProviderRateLimited, ProviderUnavailable) as exc:
                if delay is None:
                    raise
                print(f"transient provider error, retrying in {delay:g}s: {exc}", file=sys.stderr)
                await asyncio.sleep(delay)
            except ProviderBadResponse as exc:
                errors.append(f"attempt {attempt}: {exc}")
                break
            except ProviderRequestRejected as exc:
                # The input refused: no answer and no second attempt; configuration and
                # account errors propagate and stop the run.
                errors.append(f"attempt {attempt}: {exc}")
                return None, attempt, errors, model
        if response is None:
            continue
        model = response.model
        expected = request.response_model
        if expected is not None and isinstance(response.parsed, expected):
            return response.parsed, attempt, errors, model
        errors.append(f"attempt {attempt}: no structured answer in the response")
    return None, MAX_ATTEMPTS, errors, model


async def ask(s: Settings, request: ChatRequest) -> dict[str, Any]:
    """One photo analysis request (:func:`ask_structured`) as a raw-record dict."""
    parsed, attempts, errors, model = await ask_structured(s.provider, s.call, request)
    analysis = (
        parsed.to_analysis().model_dump(mode="json")
        if isinstance(parsed, ModelPhotoAnalysis)
        else None
    )
    return {"analysis": analysis, "attempts": attempts, "errors": errors, "model": model}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.predict_tiles", description=__doc__)
    parser.add_argument("--photos", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--grid", type=int, default=2)
    parser.add_argument("--upscale", type=float, default=2.0)
    parser.add_argument("--prompt", default="v1", help="released version of the mode's prompt")
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--budget-usd", type=float, default=8.0)
    return parser.parse_args(argv)


@stops_on_account_error
async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    pool = guard(ProviderPool.from_settings(settings))
    if pool is None:
        print("no model provider configured", file=sys.stderr)
        return 2
    files = photo_files(args.photos)
    if not files:
        print(f"no photos in {args.photos}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    budget = Budget(remaining_usd=args.budget_usd)
    track(args, budget)
    recorder = MemoryRecorder()
    s = Settings(
        provider=pool,
        call=CallContext(
            study_id=uuid.uuid4(),
            run_id=None,
            activity="evals.predict_tiles",
            budget=budget,
            recorder=recorder,
        ),
        prompt=prompts.load(PROMPTS[args.mode], args.prompt),
        model=args.model or settings.vision_model,
        max_tokens=args.max_tokens,
        reasoning_effort=args.reasoning_effort,
    )
    semaphore = asyncio.Semaphore(args.concurrency)
    prepared = {f.name: prepare_image(f.read_bytes()) for f in files}
    parts = {name: tiles(image, args.grid, args.upscale) for name, image in prepared.items()}

    async def call(request: ChatRequest) -> dict[str, Any]:
        async with semaphore:
            started = time.monotonic()
            answer = await ask(s, request)
            # time in the call itself, not in the queue for the semaphore
            answer["seconds"] = round(time.monotonic() - started, 2)
            return answer

    async def one(name: str, repeat: int) -> dict[str, Any]:
        if args.mode == "separate":
            calls = await asyncio.gather(
                *(call(_request(s, tile_text(t, args.upscale), [t.image])) for t in parts[name])
            )
        else:
            positions = ", ".join(t.position for t in parts[name])
            text = JOINT_TEXT.format(upscale=args.upscale, positions=positions)
            images = [prepared[name], *(t.image for t in parts[name])]
            calls = [await call(_request(s, text, images))]
        return {
            "file": name,
            "repeat": repeat,
            "mode": args.mode,
            "calls": calls,
            # call seconds added up (``separate``: the parts run in parallel in production,
            # so the photo's latency is the slowest part, ``max(c["seconds"])``)
            "seconds": round(sum(c["seconds"] for c in calls), 2),
        }

    counts: dict[str, list[dict[str, int]]] = {f.name: [] for f in files}
    labels: dict[str, list[list[str]]] = {f.name: [] for f in files}
    rooms: dict[str, list[str]] = {f.name: [] for f in files}
    tile_counts: dict[str, list[list[dict[str, int]]]] = {f.name: [] for f in files}
    missing: list[str] = []
    with (args.out / "photo_raw.jsonl").open("w", encoding="utf-8") as raw:
        for repeat in range(1, args.repeats + 1):
            records = await asyncio.gather(*(one(f.name, repeat) for f in files))
            for record in records:
                raw.write(json.dumps(record, sort_keys=True) + "\n")
                analyses = [c["analysis"] for c in record["calls"]]
                if any(a is None for a in analyses):
                    missing.append(f"{record['file']}#{repeat}")
                    continue
                per_call = [label_counts(a["components"]) for a in analyses]
                total: dict[str, int] = {}
                for part_counts in per_call:
                    for label, n in part_counts.items():
                        total[label] = total.get(label, 0) + n
                counts[record["file"]].append(total)
                labels[record["file"]].append(
                    sorted({c["label"] for a in analyses for c in a["components"]})
                )
                if args.mode == "separate":
                    tile_counts[record["file"]].append(per_call)
                else:
                    rooms[record["file"]].append(analyses[0]["room_type"])
            print(
                f"repeat {repeat}/{args.repeats}: {len(records)} photos, "
                f"spent ${budget.spent_usd:.4f}",
                file=sys.stderr,
            )

    def fold(values: list[Any]) -> Any:
        return values[0] if len(values) == 1 else {"repeats": values}

    outputs: dict[str, dict[str, Any]] = {"photo_counts": counts, "photo_components": labels}
    if args.mode == "separate":
        outputs["tile_counts"] = tile_counts
    else:
        outputs["photo_rooms"] = rooms
    for name, data in outputs.items():
        (args.out / f"{name}.json").write_text(
            json.dumps({f: fold(v) for f, v in data.items() if v}, indent=1, sort_keys=True)
        )
    (args.out / "run.json").write_text(
        json.dumps(
            {
                "photos": len(files),
                "repeats": args.repeats,
                "mode": args.mode,
                "grid": args.grid,
                "upscale": args.upscale,
                "prompt": s.prompt.version_id,
                "model_requested": s.model,
                "models_reported": sorted(
                    {str(getattr(r, "model", "")) for r in recorder.records} - {""}
                ),
                "reasoning_effort": args.reasoning_effort,
                "max_tokens": args.max_tokens,
                "calls": len(recorder.records),
                "spent_usd": round(budget.spent_usd, 6),
                "missing": missing,
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
