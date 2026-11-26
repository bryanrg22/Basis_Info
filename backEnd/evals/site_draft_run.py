"""``python -m evals.site_draft_run``: SAM 3 and the vision models on every site-truth view
(EVAL_REPORT 106). Paid: SAM 3 on Replicate (billed predict seconds) and the vision models;
every call is a row of the run's ledger (``llm_calls.jsonl``), and the run stops before
``--budget-usd``.

For each parcel of :mod:`evals.site_truth` and each view (``county``: Fairfax's 2023 3-inch
ortho; ``naip``: the federal 0.6 m view; ``google``: the straight-down capture, cut to the same
ground as the county view through :mod:`evals.site_register`), it writes
``<out>/<parcel>/<view>/``:

* ``m2.json``: one SAM 3 prediction with, per truth item, a **box** prompt alone (the item's box
  plus :data:`BOX_MARGIN_M`: an engineer's drag), a **point** alone (the item's innermost point:
  an engineer's click), the **phrase and the box** together (how production prompts an
  engineer's box on a photo today), and per family a **phrase** alone (no click at all); every
  instance SAM 3 returned, as returned (masks as run lengths); the call (seconds, dollars);
* ``m2v_<model>.json`` per ``--model``: one ``site_items/v1`` call on the same picture (the
  model's boxes), then one SAM 3 prediction with each proposed box alone;
* ``view.json``: the picture's size, the size SAM 3 saw it at, pixel size, georeference, and
  the truth outlines and the parcel line in its pixels.

Scoring is separate and free (``python -m evals.measure_eval --site <out>``), so the drafting
rules (tolerance, refusals) can be re-scored without paying again.

Usage (from ``backEnd/``)::

    BASIS_ENV=dev uv run python -m evals.site_draft_run --out "$BASIS_EVAL_DATA_DIR/..." \\
        --views county,naip,google --model gpt-4o-mini --model gpt-5-nano --budget-usd 3.5
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np

from basis.config import load_settings
from basis.geo.geometry import lonlat_to_pixel
from basis.llm import prompts
from basis.llm.call import CallContext, call_model, step_entry
from basis.llm.errors import ProviderError
from basis.llm.pool import ProviderPool
from basis.llm.provider import Budget, BudgetExceededError, ChatRequest, ImagePart, Message
from basis.llm.recorder import KIND_LOCALIZATION
from basis.vision import site_items as si
from basis.vision.localize import (
    L40S_USD_PER_SECOND,
    Box,
    LocalizeCall,
    LocalizerError,
    Point,
    VisualPrompt,
    build_replicate_localizer,
)
from basis.vision.prep import prepare_image
from evals import record
from evals import site_register as sr
from evals import site_truth as st

BOX_MARGIN_M: Final = 0.5
# Registration template side and the largest square cut from Google's close view (900 px at
# 0.0841 m is 75.7 m tall).
TEMPLATE_M: Final = 55.0
MAX_GOOGLE_M: Final = 74.0
PROMPT: Final = "site_items"
DEFAULT_VERSION: Final = (
    "1bf97763d5dfd3a1584adca913a8ef4b43c684fca97e04e39e4c50a3a5e09650"  # pragma: allowlist secret  # noqa: E501
)
# The phrase each truth family is asked by (the phrase-only and phrase-plus-box prompts).
PHRASES: Final[dict[str, str]] = {f: si.FAMILIES[f].phrase for f in st.FAMILIES.values()}
# The vision models' settings as production runs its per-photo vision step (EVAL 12.1: low
# reasoning effort for a reasoning model; temperature 0 for the others).
MODEL_SETTINGS: Final[dict[str, dict[str, Any]]] = {
    "gpt-4o-mini": {"temperature": 0.0, "reasoning_effort": None, "max_tokens": 2000},
    "gpt-5-nano": {"temperature": 0.0, "reasoning_effort": "low", "max_tokens": 6000},
}


# ------------------------------------------------------------------ the pictures


def _grey(data: bytes) -> np.ndarray:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im.convert("L"), dtype=np.float64)


def _mask_buildings(meta: Mapping[str, Any], parcel: st.Parcel, dilate_m: float = 1.5) -> Any:
    from PIL import Image, ImageDraw
    from scipy import ndimage

    w, h = meta["width"], meta["height"]
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    for ring in parcel.buildings:
        draw.polygon([lonlat_to_pixel(meta["bbox"], w, h, lo, la) for lo, la in ring], fill=1)
    built = np.asarray(img, dtype=bool)
    built = ndimage.binary_dilation(
        built, iterations=max(1, round(dilate_m / meta["pixel_size_m"]))
    )
    return ~built


def google_view(
    parcel: st.Parcel, views: Path, captures: Path
) -> tuple[si.TopDownView, dict[str, Any]] | None:
    """Google's straight-down capture of the parcel, registered on the county view and cut to
    the county view's ground; None when the parcel was not captured."""
    result = captures / parcel.id / "result.json"
    if not result.is_file():
        return None
    got = json.loads(result.read_text())
    close = next((v for v in got["views"] if v["label"] == "aerial-close"), None)
    if close is None:
        return None
    png = next((captures / parcel.id).rglob("aerial-close.png"))
    data = png.read_bytes()
    county = json.loads((views / parcel.id / "county.json").read_text())
    county_img = _grey((views / parcel.id / county["file"]).read_bytes())
    # The template is the county view's middle TEMPLATE_M square: Google's close view is only
    # 76 m tall, and the template must fit inside it at every scale tried.
    half = min(county["width"], round(TEMPLATE_M / county["pixel_size_m"])) // 2
    ox, oy = county["width"] // 2 - half, county["height"] // 2 - half
    window = (slice(oy, oy + 2 * half), slice(ox, ox + 2 * half))
    reg = sr.register(
        county_img[window],
        _grey(data),
        county_px=county["pixel_size_m"],
        google_px=close["pixel_size_m"],
        mask=_mask_buildings(county, parcel)[window],
    )

    def county_to_google(x: float, y: float) -> tuple[float, float]:
        return reg.county_to_google(x - ox, y - oy)

    cx, cy = county_to_google(county["width"] / 2, county["height"] / 2)
    side = min(county["extent_m"], MAX_GOOGLE_M) / close["pixel_size_m"]
    box = (
        round(cx - side / 2),
        round(cy - side / 2),
        round(cx + side / 2),
        round(cy + side / 2),
    )
    full = si.TopDownView(
        data=data,
        width=close["width"],
        height=close["height"],
        pixel_size_m=close["pixel_size_m"],
        georef=si.CentredGeoref(
            close["lat"], close["lon"], close["width"], close["height"], close["pixel_size_m"]
        ),
        kind="capture",
        source="Google Earth straight-down view",
        label="aerial-close",
    )
    view = si.crop(full, box)
    offset = view.georef
    assert isinstance(offset, si.Offset)
    left, top = offset.x0, offset.y0

    def to_px(lon: float, lat: float) -> tuple[float, float]:
        x, y = lonlat_to_pixel(county["bbox"], county["width"], county["height"], lon, lat)
        gx, gy = county_to_google(x, y)
        return gx - left, gy - top

    extra = {
        "registration": {
            "k": reg.k,
            "score": reg.score,
            "f": reg.f,
            "x0": reg.x0,
            "y0": reg.y0,
            "template_origin": [ox, oy],
            "scores": reg.scores,
        },
        "imagery_date": close.get("imagery_date"),
        "capture_centre": [close["lon"], close["lat"]],
        "crop": [left, top, view.width, view.height],
        "to_px": to_px,
    }
    return view, extra


def public_view(parcel: st.Parcel, views: Path, name: str) -> tuple[si.TopDownView, dict[str, Any]]:
    meta = json.loads((views / parcel.id / f"{name}.json").read_text())
    data = (views / parcel.id / meta["file"]).read_bytes()
    view = si.TopDownView(
        data=data,
        width=meta["width"],
        height=meta["height"],
        pixel_size_m=meta["pixel_size_m"],
        georef=si.MercatorGeoref(tuple(meta["bbox"]), meta["width"], meta["height"]),
        kind="public",
        source=meta["source_id"],
        label=name,
    )

    def to_px(lon: float, lat: float) -> tuple[float, float]:
        return lonlat_to_pixel(meta["bbox"], meta["width"], meta["height"], lon, lat)

    return view, {"to_px": to_px, "year": meta.get("year")}


def georef_record(view: si.TopDownView) -> dict[str, Any]:
    g = view.georef
    if isinstance(g, si.MercatorGeoref):
        return {"type": "mercator", "bbox": list(g.bbox), "width": g.width, "height": g.height}
    assert isinstance(g, si.Offset) and isinstance(g.inner, si.CentredGeoref)
    c = g.inner
    return {
        "type": "centred",
        "lat": c.lat,
        "lon": c.lon,
        "width": c.width,
        "height": c.height,
        "pixel_size_m": c.pixel_size_m,
        "x0": g.x0,
        "y0": g.y0,
    }


# ------------------------------------------------------------------ prompts from the truth


def _raster(ring: Sequence[tuple[float, float]], w: int, h: int) -> np.ndarray:
    from PIL import Image, ImageDraw

    img = Image.new("L", (w, h), 0)
    ImageDraw.Draw(img).polygon([tuple(p) for p in ring], fill=1)
    return np.asarray(img, dtype=bool)


def truth_prompts(
    ring_px: Sequence[tuple[float, float]], w: int, h: int, pixel_m: float
) -> tuple[Box, Point] | None:
    """The engineer's box (the item's box plus the margin) and click (the item's innermost
    pixel), normalized; None when the item is not in the picture."""
    from scipy import ndimage

    xs, ys = [p[0] for p in ring_px], [p[1] for p in ring_px]
    if max(xs) <= 0 or max(ys) <= 0 or min(xs) >= w or min(ys) >= h:
        return None
    m = BOX_MARGIN_M / pixel_m
    box = Box.from_pixels(min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m, w, h)
    mask = _raster(ring_px, w, h)
    if not mask.any():
        return None
    dist = ndimage.distance_transform_edt(mask)
    y, x = np.unravel_index(int(np.argmax(dist)), dist.shape)
    return box, Point(x=(x + 0.5) / w, y=(y + 0.5) / h)


# ------------------------------------------------------------------ the calls


async def ledger_sam(ctx: CallContext, call: LocalizeCall, version: str, what: str) -> None:
    await ctx.recorder.record(
        step_entry(
            ctx,
            kind=KIND_LOCALIZATION,
            provider="replicate",
            model=f"vufinder/sam3@{version}",
            quantity=call.predict_seconds,
            unit="second",
            unit_price_usd=L40S_USD_PER_SECOND,
            cost_usd=call.cost_usd,
            response_id=call.prediction_id,
            params={"prompts": list(call.prompts), "what": what},
            response={"status": call.status, "predict_seconds": call.predict_seconds},
        )
    )


def _instances(found: Sequence[Sequence[Any]]) -> list[list[dict[str, Any]]]:
    return [[i.model_dump(mode="json") for i in group] for group in found]


async def run_m2(
    view: si.TopDownView,
    prepared: Any,
    items: Sequence[tuple[st.Item, Box, Point]],
    localizer: Any,
) -> dict[str, Any]:
    prompts_: list[VisualPrompt] = []
    meta: list[dict[str, Any]] = []
    for item, box, point in items:
        label = si.FAMILIES[item.family].label
        prompts_.append(VisualPrompt(label=label, boxes=(box,)))
        meta.append({"mode": "box", "item": item.id, "family": item.family})
        prompts_.append(VisualPrompt(label=label, points=(point,)))
        meta.append({"mode": "point", "item": item.id, "family": item.family})
        prompts_.append(VisualPrompt(label=label, text=PHRASES[item.family], boxes=(box,)))
        meta.append({"mode": "text_box", "item": item.id, "family": item.family})
    for family, phrase in PHRASES.items():
        prompts_.append(VisualPrompt(label=si.FAMILIES[family].label, text=phrase))
        meta.append({"mode": "text", "item": None, "family": family})
    started = time.monotonic()
    found, call = await localizer.run_prompts(prepared, prompts_, threshold=si.THRESHOLD)
    return {
        "prompts": [
            {**m, "prompt": p.model_dump(mode="json")} for m, p in zip(meta, prompts_, strict=True)
        ],
        "instances": _instances(found),
        "call": call.model_dump(mode="json"),
        "seconds": round(time.monotonic() - started, 2),
    }


async def propose(
    pool: Any, ctx: CallContext, prompt: prompts.Prompt, model: str, prepared: Any
) -> tuple[si.Proposal | None, dict[str, Any]]:
    settings = MODEL_SETTINGS[model]
    request = ChatRequest(
        model=model,
        system=prompt.text,
        messages=(
            Message(
                role="user",
                content=si.USER_TEXT,
                images=(
                    ImagePart(
                        media_type="image/jpeg",
                        data_base64=prepared.base64,
                        detail="high",
                        ref="site view",
                    ),
                ),
            ),
        ),
        response_model=si.Proposal,
        max_tokens=settings["max_tokens"],
        temperature=settings["temperature"],
        reasoning_effort=settings["reasoning_effort"],
        prompt_version=prompt.version_id,
        study_id=str(ctx.study_id),
        activity=ctx.activity,
    )
    started = time.monotonic()
    try:
        response = await call_model(pool, request, ctx)
    except ProviderError as exc:
        return None, {"error": repr(exc)[:500], "seconds": round(time.monotonic() - started, 2)}
    parsed = response.parsed if isinstance(response.parsed, si.Proposal) else None
    return parsed, {
        "model_reported": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "latency_ms": response.latency_ms,
        "seconds": round(time.monotonic() - started, 2),
        "answer": parsed.model_dump(mode="json") if parsed is not None else None,
        "text": None if parsed is not None else response.text[:2000],
    }


async def run_m2v(
    proposal: si.Proposal, prepared: Any, localizer: Any, model: str
) -> dict[str, Any]:
    prompts_ = si.proposal_prompts(proposal, by=f"model:{model}")
    drafted = [p for p in prompts_ if p.family in si.DRAFTED]
    out: dict[str, Any] = {
        "proposed": [
            {"family": p.family, "box": p.box.model_dump() if p.box else None} for p in prompts_
        ],
        "instances": [],
        "call": None,
    }
    if not drafted:
        return out
    found, call = await localizer.run_prompts(
        prepared, [si.sam_prompt(p) for p in drafted], threshold=si.THRESHOLD
    )
    out["sam_prompts"] = [
        {"family": p.family, "box": p.box.model_dump() if p.box else None} for p in drafted
    ]
    out["instances"] = _instances(found)
    out["call"] = call.model_dump(mode="json")
    return out


# ------------------------------------------------------------------ the run


async def run(args: argparse.Namespace) -> int:
    rec = record.EvalRun.begin(
        args.out, harness=__name__, args=args, component="site_drafts", version="v1"
    )
    os.environ.setdefault("BASIS_ENV", "dev")
    # Each model name is sent to the Azure deployment of the same name: the provider maps the
    # vision model and the text model to the vision and text deployments, so the two are named
    # after the deployments here (the backEnd/.env deployments: gpt-4o-mini and gpt-5-nano).
    settings = load_settings(args.env_file).model_copy(
        update={"vision_model": "gpt-4o-mini", "text_model": "gpt-5-nano"}
    )
    token = settings.replicate_api_token.get_secret_value() if settings.replicate_api_token else ""
    if not token:
        print("REPLICATE_API_TOKEN is not set", file=sys.stderr)
        return 2
    budget = Budget(remaining_usd=args.budget_usd)
    localizer = build_replicate_localizer(token, args.version, concurrency=2, budget=budget)
    pool = ProviderPool.from_settings(settings) if args.model else None
    prompt = prompts.load(PROMPT, "v1")
    rec.prompt(prompt)
    ctx = CallContext(
        study_id=uuid.uuid4(),
        run_id=None,
        activity="evals.site_draft_run",
        budget=budget,
        recorder=rec.recorder,
    )
    parcels = st.load_all(args.truth)
    if args.only:
        wanted = set(args.only.split(","))
        parcels = [p for p in parcels if p.id in wanted]
    rec.dataset(args.truth, name="site_truth")
    views_wanted = args.views.split(",")
    stopped = None
    try:
        for parcel in parcels:
            for name in views_wanted:
                folder = args.out / parcel.id / name
                if (folder / "view.json").is_file() and not args.again:
                    continue
                if name == "google":
                    got = google_view(parcel, args.views_dir, args.captures)
                    if got is None:
                        continue
                    view, extra = got
                else:
                    view, extra = public_view(parcel, args.views_dir, name)
                to_px = extra.pop("to_px")
                prepared = await asyncio.to_thread(prepare_image, view.data)
                truth_px = {i.id: [list(to_px(lo, la)) for lo, la in i.ring] for i in parcel.items}
                pixel_m = float(view.pixel_size_m or 1.0)
                items = []
                for item in parcel.items:
                    scaled = [
                        (x * prepared.width / view.width, y * prepared.height / view.height)
                        for x, y in truth_px[item.id]
                    ]
                    tp = truth_prompts(
                        scaled,
                        prepared.width,
                        prepared.height,
                        pixel_m * view.width / prepared.width,
                    )
                    if tp is not None:
                        items.append((item, *tp))
                folder.mkdir(parents=True, exist_ok=True)
                meta = {
                    "parcel": parcel.id,
                    "view": name,
                    "width": view.width,
                    "height": view.height,
                    "prepared": [prepared.width, prepared.height],
                    "pixel_size_m": view.pixel_size_m,
                    "source": view.source,
                    "georef": georef_record(view),
                    "truth_px": truth_px,
                    "parcel_px": [list(to_px(lo, la)) for lo, la in parcel.parcel],
                    **extra,
                }
                (folder / "prepared.jpg").write_bytes(prepared.jpeg)
                try:
                    m2 = await run_m2(view, prepared, items, localizer)
                    await ledger_sam(
                        ctx, LocalizeCall(**m2["call"]), args.version, f"{parcel.id}/{name}/m2"
                    )
                    (folder / "m2.json").write_text(json.dumps(m2) + "\n")
                    for model in args.model or ():
                        proposal, info = await propose(pool, ctx, prompt, model, prepared)
                        doc: dict[str, Any] = {
                            "model": model,
                            "settings": MODEL_SETTINGS[model],
                            **info,
                        }
                        if proposal is not None:
                            doc |= await run_m2v(proposal, prepared, localizer, model)
                            if doc.get("call"):
                                await ledger_sam(
                                    ctx,
                                    LocalizeCall(**doc["call"]),
                                    args.version,
                                    f"{parcel.id}/{name}/m2v/{model}",
                                )
                        (folder / f"m2v_{model}.json").write_text(json.dumps(doc) + "\n")
                except LocalizerError as exc:
                    rec.case(f"{parcel.id}/{name}", "error", detail={"error": str(exc)})
                    print(
                        json.dumps({"parcel": parcel.id, "view": name, "error": str(exc)}),
                        flush=True,
                    )
                    continue
                (folder / "view.json").write_text(json.dumps(meta, indent=1) + "\n")
                rec.case(f"{parcel.id}/{name}", "passed", detail={"items": len(items)})
                print(
                    json.dumps(
                        {
                            "parcel": parcel.id,
                            "view": name,
                            "items": len(items),
                            "spent_usd": round(budget.spent_usd, 4),
                        }
                    ),
                    flush=True,
                )
    except BudgetExceededError as exc:
        stopped = f"budget: {exc}"
        print(json.dumps({"stopped": stopped}), flush=True)
    finally:
        await localizer.client.aclose()
    rec.finish(
        {
            "spent_usd": round(budget.spent_usd, 6),
            "stopped": stopped,
            "views": views_wanted,
            "models": args.model or [],
        }
    )
    return 0 if stopped is None else 3


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.site_draft_run", description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--truth", type=Path, default=st.FIXTURES)
    parser.add_argument(
        "--views-dir", type=Path, default=Path.home() / "basis-eval-data/site_truth/views"
    )
    parser.add_argument(
        "--captures", type=Path, default=Path.home() / "basis-eval-data/site_truth/captures"
    )
    parser.add_argument("--views", default="county,naip,google")
    parser.add_argument("--model", action="append", choices=sorted(MODEL_SETTINGS))
    parser.add_argument("--only")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--budget-usd", type=float, default=3.5)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--again", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
