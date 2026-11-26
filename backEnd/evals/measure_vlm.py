"""M5 of AI_MEASUREMENT_PLAN.md section 5: a vision model asked for each exterior quantity
directly, the floor every other measuring method must beat (EVAL_REPORT 108).

One call per house per setup: the property's pictures as the ground-truth work captured them
(``$BASIS_EVAL_DATA_DIR/measure_truth/captures/<id>/``, EVAL_REPORT 103.5) and what an appraisal
gives (the GLA and the storeys, as M0 gets them), one structured answer
(:class:`ExteriorAnswer`, :class:`PixelAnswer` for the pixel prompt) with the quantities and
five judgments. A setup is one model (at a production call's exact settings, :data:`MODELS`)
times one prompt (:data:`PROMPTS`):

* ``plain`` (``measure_exterior_plain/v1``): the pictures and the definitions, "estimate";
* ``pixels`` (``measure_exterior_pixels/v1``): plus the straight-down picture's ground size of a
  pixel and the instruction to list the roof's corners in pixels and compute from them;
* ``reference`` (``measure_exterior_reference/v1``): plus objects of known size as rulers (an
  80 in door, a 9 to 10 ft storey), the SpatialPrompt-style reference prompting.

The pixel prompt's corners are also measured by code (:func:`corner_quantities`): the same
answer scored twice, so an error can be put on the model's corner placement or on its
arithmetic.

**The pictures** (:func:`load_pictures`): the straight-down view that holds the house
(``aerial-close``, 0.084 m a pixel, else ``aerial-wide``), cropped to 768 x 768 px around the
house at its native pixels (so neither provider rescales it: Claude below its 1.15 MP limit,
OpenAI's high detail at its 768 px short side), with a red cross on the house's centre (the
truth file's located point, as a confirmed pin would give) and a scale bar; the four obliques
and the street picture as the production image preparation makes any photo
(:func:`basis.vision.prep.prepare_image`, 1024 px). The capture's point can be off the house
(Google's pin, or a capture shared between neighbours: mo1223 is in mo1222's views,
EVAL_REPORT 103.5); the message then says where the house is from that point.

**Which houses** (:func:`skip_reason`): a house without captures is not run (``no pictures``);
nor one changed since it was drawn (``standing.status == "changed"``: EVAL_REPORT 103.4, an
imagery method measures today's building) nor one whose building was never located (a
location the truth file marks ``APPROXIMATE``).

**The money**: every call goes through :func:`basis.llm.call.call_model` with the run's ledger
(:class:`evals.record.FileLedgerRecorder`: model, prompt version and sha256, tokens, cost,
latency), under one :class:`~basis.llm.provider.Budget`; a house's dollars and tokens are the
ledger rows of its calls (the call context's entity id), retries and unusable answers included.
Azure is left out of the pool: its deployment is a different model, and a ``gpt-*`` call that
fell back to it would be scored as the model it named.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field, field_validator

from basis.config import Settings
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.cost import estimate_usd, is_anthropic_model
from basis.llm.provider import Budget, ChatProvider, ChatRequest, ImagePart, Message
from basis.llm.recorder import CallRecord
from basis.vision.prep import prepare_image
from evals import measure_truth as mt

M_TO_FT: Final = 1 / 0.3048
EARTH_RADIUS_M: Final = 6_371_008.8  # mean radius: the local offsets are tens of metres
CROP_PX: Final = 768
# The house's centre must stand this far inside a straight-down view (on the ground) for the
# view to hold the whole house: the largest house here is 96 ft (29 m) long, so 20 m covers
# half its diagonal with its roof. ga0157 sits 17 m from the wide view's edge and is cut off.
VIEW_MARGIN_M: Final = 20.0
STRAIGHT_DOWN: Final = ("aerial-close", "aerial-wide")
OBLIQUES: Final[dict[str, str]] = {
    "side-south": "from the south, looking north",
    "side-west": "from the west, looking east",
    "side-north": "from the north, looking south",
    "side-east": "from the east, looking west",
}
STREET: Final = "street-front"
SCALE_BARS_FT: Final = (30, 50, 100, 200)
MIN_BAR_PX: Final = 80
# Houses captured in another house's views (EVAL_REPORT 103.5: "mo1222 (and mo1223 in the
# same views)").
SHARED_CAPTURES: Final[dict[str, str]] = {"mo1223": "mo1222"}
NOTE_MAX: Final = 600
MAX_CORNERS: Final = 60

PromptKey = Literal["plain", "pixels", "reference"]
PROMPTS: Final[dict[str, str]] = {
    "plain": "measure_exterior_plain",
    "pixels": "measure_exterior_pixels",
    "reference": "measure_exterior_reference",
}
PROMPT_VERSION: Final = "v1"
PROMPT_NAMES: Final[dict[str, str]] = {
    "plain": "plain ask",
    "pixels": "pixel size, count pixels",
    "reference": "objects of known size",
}

# The quantities the answer gives directly, and the one computed from them on the truth's own
# definition (wall face = perimeter x eave height, EVAL_REPORT 103.1).
ANSWER_QUANTITIES: Final = (
    "footprint_sf",
    "perimeter_ft",
    "long_side_ft",
    "eave_height_ft",
    "ridge_height_ft",
    "roof_plan_sf",
    "roof_area_sf",
    "gutter_ft",
)
JUDGMENTS: Final = (
    "outline_is_rectangle",
    "eaves_all_round",
    "rectangle_understates_perimeter",
    "footprint_exceeds_gla_per_storey",
)


# ------------------------------------------------------------------ the answer


NonNegative = Annotated[float, Field(ge=0)]


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > NOTE_MAX:
        return value[: NOTE_MAX - 1] + "…"
    return value


class ExteriorAnswer(BaseModel):
    """What the model fills (``measure_exterior_plain`` and ``measure_exterior_reference``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_found: bool
    footprint_sf: NonNegative | None
    perimeter_ft: NonNegative | None
    long_side_ft: NonNegative | None
    eave_height_ft: NonNegative | None
    ridge_height_ft: NonNegative | None
    overhang_ft: NonNegative | None
    roof_plan_sf: NonNegative | None
    roof_area_sf: NonNegative | None
    gutter_ft: NonNegative | None
    outline_shape: Literal["rectangle", "L", "T", "U", "other"]
    eaves_all_round: bool
    rectangle_understates_perimeter: bool
    footprint_exceeds_gla_per_storey: bool
    confidence: Literal["low", "medium", "high"]
    notes: str = Field(max_length=NOTE_MAX)

    @field_validator("notes", mode="before")
    @classmethod
    def _clip_notes(cls, value: Any) -> Any:
        return _clip(value)


class Corner(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    x: float
    y: float


class PixelAnswer(ExteriorAnswer):
    """``measure_exterior_pixels``: the answer plus the roof edge's corners in pixels."""

    roof_outline_px: list[Corner] = Field(max_length=MAX_CORNERS)


ANSWER_MODELS: Final[dict[str, type[ExteriorAnswer]]] = {
    "plain": ExteriorAnswer,
    "pixels": PixelAnswer,
    "reference": ExteriorAnswer,
}


# ------------------------------------------------------------------ the setups


@dataclass(frozen=True, slots=True)
class ModelSetup:
    key: str
    model: str
    effort: str
    max_tokens: int
    production_call: str  # which production call runs this model at these settings


def models_from_settings(settings: Settings) -> dict[str, ModelSetup]:
    """The models at the exact settings of a production call each (``basis.config``)."""
    return {
        "sol": ModelSetup(
            "sol",
            settings.vision_model,
            settings.vision_reasoning_effort,
            settings.vision_output_budget,
            "per-photo vision (VISION_MODEL, VISION_REASONING_EFFORT, its output budget)",
        ),
        "luna": ModelSetup(
            "luna",
            settings.vision_surfaces_model,
            settings.vision_reasoning_effort,
            settings.vision_surfaces_output_budget,
            "per-photo surfaces (VISION_SURFACES_MODEL, VISION_REASONING_EFFORT)",
        ),
        "opus": ModelSetup(
            "opus",
            settings.vision_fusion_model,
            settings.vision_fusion_reasoning_effort,
            settings.vision_fusion_output_budget,
            "count fusion (VISION_FUSION_MODEL, VISION_FUSION_REASONING_EFFORT)",
        ),
        "sonnet": ModelSetup(
            "sonnet",
            settings.surface_crop_model,
            settings.surface_crop_reasoning_effort,
            1200,
            "surface crops (SURFACE_CROP_MODEL, SURFACE_CROP_REASONING_EFFORT, 1200 answer)",
        ),
    }


@dataclass(frozen=True, slots=True)
class Setup:
    model: ModelSetup
    prompt: str  # a key of PROMPTS

    @property
    def id(self) -> str:
        return f"M5.{self.model.key}.{self.prompt}"

    @property
    def name(self) -> str:
        return f"Vision model asked ({self.model.model}, {PROMPT_NAMES[self.prompt]})"

    def settings(self) -> dict[str, Any]:
        prompt = prompts.load(PROMPTS[self.prompt], PROMPT_VERSION)
        return {
            "model": self.model.model,
            "reasoning_effort": self.model.effort,
            "answer_budget_tokens": self.model.max_tokens,
            "temperature": "not sent (reasoning models refuse it)",
            "prompt": prompt.version_id,
            "prompt_sha256": prompt.sha256,
            "production_call": self.model.production_call,
            "inputs": "straight-down crop 768 px native + red cross + scale bar; obliques and "
            "street at 1024 px; GLA and storeys",
        }


def parse_setups(text: str, models: Mapping[str, ModelSetup]) -> list[Setup]:
    """``"opus:plain,sol:*"`` or ``"*"``: model keys times prompt keys."""
    out: list[Setup] = []
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        model_key, _, prompt_key = part.partition(":")
        model_keys = list(models) if model_key in ("*", "") else [model_key]
        prompt_keys = list(PROMPTS) if prompt_key in ("*", "") else [prompt_key]
        for mk in model_keys:
            if mk not in models:
                raise ValueError(f"unknown M5 model {mk!r}; known {list(models)}")
            for pk in prompt_keys:
                if pk not in PROMPTS:
                    raise ValueError(f"unknown M5 prompt {pk!r}; known {list(PROMPTS)}")
                setup = Setup(models[mk], pk)
                if setup not in out:
                    out.append(setup)
    if not out:
        raise ValueError("no M5 setup named")
    return out


# ------------------------------------------------------------------ the pictures


@dataclass(frozen=True, slots=True)
class View:
    label: str
    path: Path
    width: int
    height: int
    pixel_size_m: float | None
    lat: float
    lon: float
    imagery_on: str | None


@dataclass(frozen=True, slots=True)
class Pictures:
    straight_down: bytes  # PNG, CROP_PX square (or smaller), cross and scale bar drawn
    view: str
    size: tuple[int, int]
    pixel_size_m: float
    subject_px: tuple[int, int]
    bar_ft: int
    obliques: tuple[tuple[str, bytes], ...]  # (label, JPEG)
    street: bytes | None
    offset_m: tuple[float, float]  # the house from the capture's point: east, north
    imagery_on: str | None

    @property
    def count(self) -> int:
        return 1 + len(self.obliques) + (1 if self.street is not None else 0)


def capture_dir(captures: Path, house_id: str) -> Path:
    return captures / SHARED_CAPTURES.get(house_id, house_id)


def read_views(directory: Path) -> list[View]:
    """The capture's views (``result.json``), each with its PNG under ``objects/``."""
    result_path = directory / "result.json"
    if not result_path.is_file():
        return []
    result = json.loads(result_path.read_text(encoding="utf-8"))
    views = []
    for v in result.get("views") or []:
        uri = str(v.get("uri") or "")
        if not uri.startswith("gs://"):
            continue
        path = directory / "objects" / uri.removeprefix("gs://")
        if not path.is_file():
            continue
        views.append(
            View(
                label=str(v["label"]),
                path=path,
                width=int(v["width"]),
                height=int(v["height"]),
                pixel_size_m=None if v.get("pixel_size_m") is None else float(v["pixel_size_m"]),
                lat=float(v["lat"]),
                lon=float(v["lon"]),
                imagery_on=v.get("imagery_on"),
            )
        )
    return views


def offset_m(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    """East and north metres from ``(lat0, lon0)`` to ``(lat, lon)`` (local, tens of metres)."""
    east = EARTH_RADIUS_M * math.cos(math.radians(lat0)) * math.radians(lon - lon0)
    north = EARTH_RADIUS_M * math.radians(lat - lat0)
    return east, north


def subject_pixel(view: View, lat: float, lon: float) -> tuple[float, float]:
    """Where ``(lat, lon)`` falls in a straight-down view (north up, centred on its point)."""
    if view.pixel_size_m is None or view.pixel_size_m <= 0:
        raise ValueError(f"{view.label} has no ground pixel size")
    east, north = offset_m(view.lat, view.lon, lat, lon)
    return view.width / 2 + east / view.pixel_size_m, view.height / 2 - north / view.pixel_size_m


def choose_view(views: Sequence[View], lat: float, lon: float) -> tuple[View, tuple[float, float]]:
    """The finest straight-down view that holds the house with a margin."""
    for label in STRAIGHT_DOWN:
        for view in views:
            if view.label != label or view.pixel_size_m is None:
                continue
            x, y = subject_pixel(view, lat, lon)
            margin = VIEW_MARGIN_M / view.pixel_size_m
            if margin <= x <= view.width - margin and margin <= y <= view.height - margin:
                return view, (x, y)
    raise LookupError("the house is not whole in any straight-down view")


def scale_bar_ft(pixel_size_m: float) -> int:
    """The shortest round bar at least :data:`MIN_BAR_PX` long."""
    for ft in SCALE_BARS_FT:
        if ft * 0.3048 / pixel_size_m >= MIN_BAR_PX:
            return ft
    return SCALE_BARS_FT[-1]


def crop_box(
    width: int, height: int, x: float, y: float, size: int = CROP_PX
) -> tuple[int, int, int, int]:
    """A ``size`` square around ``(x, y)``, moved inside the image (smaller if it is)."""
    w, h = min(size, width), min(size, height)
    left = int(round(min(max(x - w / 2, 0), width - w)))
    top = int(round(min(max(y - h / 2, 0), height - h)))
    return left, top, left + w, top + h


def draw_straight_down(
    image: Image.Image, x: float, y: float, pixel_size_m: float
) -> tuple[bytes, tuple[int, int], int]:
    """The crop around the house with the red cross and the scale bar, as PNG bytes; the
    house's pixel in the crop; the bar's length in feet."""
    box = crop_box(image.width, image.height, x, y)
    crop = image.convert("RGB").crop(box)
    cx, cy = int(round(x - box[0])), int(round(y - box[1]))
    draw = ImageDraw.Draw(crop)
    arm, gap = 9, 3
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        draw.line(
            [(cx + dx * gap, cy + dy * gap), (cx + dx * arm, cy + dy * arm)],
            fill=(255, 0, 0),
            width=2,
        )
    bar_ft = scale_bar_ft(pixel_size_m)
    bar_px = int(round(bar_ft * 0.3048 / pixel_size_m))
    x0, y0 = 12, crop.height - 16
    font = ImageFont.load_default()
    label = f"{bar_ft} ft"
    draw.rectangle([x0 - 6, y0 - 22, x0 + bar_px + 6, y0 + 8], fill=(255, 255, 255))
    draw.line([(x0, y0), (x0 + bar_px, y0)], fill=(0, 0, 0), width=3)
    for end in (x0, x0 + bar_px):
        draw.line([(end, y0 - 6), (end, y0 + 4)], fill=(0, 0, 0), width=2)
    draw.text((x0, y0 - 20), label, fill=(0, 0, 0), font=font)
    out = io.BytesIO()
    crop.save(out, format="PNG", optimize=False)
    return out.getvalue(), (cx, cy), bar_ft


def skip_reason(house: mt.House, captures: Path) -> str | None:
    """Why M5 is not run on ``house``, or ``None``."""
    if house.standing.status == "changed":
        return "changed since drawn (EVAL_REPORT 103.4): pictures show another building"
    if house.location.how.strip().upper().startswith("APPROXIMATE"):
        return "the building is not located (EVAL_REPORT 103.4)"
    if not read_views(capture_dir(captures, house.id)):
        return "no pictures"
    return None


def load_pictures(house: mt.House, captures: Path) -> Pictures:
    """The house's pictures, prepared (CPU work: call it on a thread from async code)."""
    views = read_views(capture_dir(captures, house.id))
    if not views:
        raise LookupError("no pictures")
    lat, lon = house.location.lat, house.location.lon
    view, (x, y) = choose_view(views, lat, lon)
    assert view.pixel_size_m is not None
    with Image.open(view.path) as image:
        png, subject, bar_ft = draw_straight_down(image, x, y, view.pixel_size_m)
        size = (min(CROP_PX, image.width), min(CROP_PX, image.height))
    obliques = tuple(
        (v.label, prepare_image(v.path.read_bytes()).jpeg)
        for label in OBLIQUES
        for v in views
        if v.label == label
    )
    street = next(
        (prepare_image(v.path.read_bytes()).jpeg for v in views if v.label == STREET), None
    )
    centre = next((v for v in views if v.label in OBLIQUES), view)
    return Pictures(
        straight_down=png,
        view=view.label,
        size=size,
        pixel_size_m=view.pixel_size_m,
        subject_px=subject,
        bar_ft=bar_ft,
        obliques=obliques,
        street=street,
        offset_m=offset_m(centre.lat, centre.lon, lat, lon),
        imagery_on=view.imagery_on,
    )


# ------------------------------------------------------------------ the request


def appraisal_gla(house: mt.House) -> float:
    """The GLA an appraisal would print (whole square feet; M0 gets the same value)."""
    return round(mt.gla_sf(house))


def _direction(east: float, north: float) -> str:
    names = (
        "north",
        "north-east",
        "east",
        "south-east",
        "south",
        "south-west",
        "west",
        "north-west",
    )
    angle = math.degrees(math.atan2(east, north)) % 360
    return names[int((angle + 22.5) // 45) % 8]


def message_text(prompt: str, house: mt.House, pictures: Pictures) -> str:
    """The user message: the appraisal's two facts and what each picture is."""
    w, h = pictures.size
    lines = [
        f"From the appraisal: GLA {appraisal_gla(house):,.0f} SF; {house.stories:g} "
        f"{'storey' if house.stories == 1 else 'storeys'}.",
        "",
        f"Picture 1, straight down: {w} x {h} pixels, north at the top. The red cross is at "
        f"pixel ({pictures.subject_px[0]}, {pictures.subject_px[1]}); the scale bar is "
        f"{pictures.bar_ft} ft.",
    ]
    if prompt == "pixels":
        ft = pictures.pixel_size_m * M_TO_FT
        lines.append(
            f"One pixel of picture 1 is {pictures.pixel_size_m:.4f} m ({ft:.4f} ft) on the "
            f"ground, so {1 / ft:.2f} pixels make a foot."
        )
    n = 2
    east, north = pictures.offset_m
    distance = math.hypot(east, north)
    centred = (
        "centred on the house"
        if distance < 5
        else f"centred on a point about {distance:.0f} m {_direction(-east, -north)} of the house"
    )
    for label, _ in pictures.obliques:
        lines.append(f"Picture {n}, oblique {OBLIQUES[label]}, {centred}.")
        n += 1
    if pictures.street is not None:
        lines.append(f"Picture {n}, street picture from the road in front.")
    lines += ["", "Measure the house marked by the red cross."]
    return "\n".join(lines)


def build_request(
    setup: Setup, house: mt.House, pictures: Pictures, *, study_id: str, activity: str
) -> ChatRequest:
    prompt = prompts.load(PROMPTS[setup.prompt], PROMPT_VERSION)
    images = [
        ImagePart(
            media_type="image/png",
            data_base64=base64.b64encode(pictures.straight_down).decode("ascii"),
            detail="high",
            ref=f"{house.id}:{pictures.view}:crop",
        )
    ]
    for label, jpeg in pictures.obliques:
        images.append(
            ImagePart(
                data_base64=base64.b64encode(jpeg).decode("ascii"),
                detail="high",
                ref=f"{house.id}:{label}",
            )
        )
    if pictures.street is not None:
        images.append(
            ImagePart(
                data_base64=base64.b64encode(pictures.street).decode("ascii"),
                detail="high",
                ref=f"{house.id}:{STREET}",
            )
        )
    return ChatRequest(
        model=setup.model.model,
        system=prompt.text,
        messages=(
            Message(
                role="user",
                content=message_text(setup.prompt, house, pictures),
                images=tuple(images),
            ),
        ),
        response_model=ANSWER_MODELS[setup.prompt],
        max_tokens=setup.model.max_tokens,
        reasoning_effort=setup.model.effort,
        prompt_version=prompt.version_id,
        study_id=study_id,
        activity=activity,
    )


def estimate_job_usd(setup: Setup, house: mt.House, pictures: Pictures) -> float:
    """The pre-call estimate :func:`basis.llm.call.call_model` reserves (worst case)."""
    request = build_request(setup, house, pictures, study_id="estimate", activity="estimate")
    chars = len(request.system) + sum(len(m.content) for m in request.messages)
    return estimate_usd(request.model, request.max_tokens, chars, pictures.count)


# ------------------------------------------------------------------ from the answer


def answer_quantities(answer: ExteriorAnswer) -> dict[str, float]:
    """The quantities the answer gives, and the wall face on the truth's definition."""
    q = {k: float(v) for k in ANSWER_QUANTITIES if (v := getattr(answer, k)) is not None}
    if "perimeter_ft" in q and "eave_height_ft" in q:
        q["wall_area_gross_sf"] = q["perimeter_ft"] * q["eave_height_ft"]
    return q


def shoelace(points: Sequence[tuple[float, float]]) -> float:
    """The polygon's area (any orientation; zero under three points)."""
    n = len(points)
    if n < 3:
        return 0.0
    twice = 0.0
    for k in range(n):
        (x1, y1), (x2, y2) = points[k], points[(k + 1) % n]
        twice += x1 * y2 - x2 * y1
    return abs(twice) / 2.0


def ring_length(points: Sequence[tuple[float, float]]) -> float:
    n = len(points)
    return sum(math.dist(points[i], points[(i + 1) % n]) for i in range(n)) if n >= 2 else 0.0


def long_extent(points: Sequence[tuple[float, float]]) -> float:
    """The longer side of the bounding rectangle aligned with the polygon's longest edge."""
    n = len(points)
    if n < 2:
        return 0.0
    i = max(range(n), key=lambda k: math.dist(points[k], points[(k + 1) % n]))
    (x1, y1), (x2, y2) = points[i], points[(i + 1) % n]
    angle = math.atan2(y2 - y1, x2 - x1)
    c, s = math.cos(-angle), math.sin(-angle)
    us = [x * c - y * s for x, y in points]
    vs = [x * s + y * c for x, y in points]
    return max(max(us) - min(us), max(vs) - min(vs))


def corner_quantities(answer: PixelAnswer, pixel_size_m: float) -> dict[str, float]:
    """The plan quantities measured by code on the model's roof corners: the roof plan from
    the polygon; the walls by taking the model's overhang off an outline whose corners turn
    four more times convex than concave (``A - P d + 4 d^2``, ``P - 8 d``), the long side
    less twice the overhang. Empty under three corners."""
    pts = [(c.x, c.y) for c in answer.roof_outline_px]
    if len(pts) < 3:
        return {}
    ft = pixel_size_m * M_TO_FT
    plan = shoelace(pts) * ft * ft
    edge = ring_length(pts) * ft
    d = answer.overhang_ft or 0.0
    out = {
        "roof_plan_sf": plan,
        "footprint_sf": max(plan - edge * d + 4 * d * d, 0.0),
        "perimeter_ft": max(edge - 8 * d, 0.0),
        "long_side_ft": max(long_extent(pts) * ft - 2 * d, 0.0),
    }
    if answer.eave_height_ft is not None:
        out["wall_area_gross_sf"] = out["perimeter_ft"] * answer.eave_height_ft
    return out


# ------------------------------------------------------------------ the judgments' truth


def judgment_truths(house: mt.House, m0_perimeter_ft: float) -> dict[str, bool]:
    """What each judgment should say for ``house``: the outline is a rectangle (four edges);
    eaves on every side (the truth's ``roof.eaves == "all"``; absent when not recorded);
    today's rectangle (M0) reads the perimeter more than 10 % low; the ground floor is more
    than 5 % over GLA / storeys."""
    t = mt.truths(house)
    perimeter = t["perimeter_ft"].value
    out = {
        "outline_is_rectangle": len(house.plan.chain) == 4,
        "rectangle_understates_perimeter": (m0_perimeter_ft - perimeter) / perimeter < -0.10,
        "footprint_exceeds_gla_per_storey": t["footprint_sf"].value
        > 1.05 * mt.gla_sf(house) / house.stories,
    }
    if house.roof.eaves is not None:
        out["eaves_all_round"] = house.roof.eaves == "all"
    return out


def answer_judgments(answer: ExteriorAnswer) -> dict[str, bool]:
    return {
        "outline_is_rectangle": answer.outline_shape == "rectangle",
        "eaves_all_round": answer.eaves_all_round,
        "rectangle_understates_perimeter": answer.rectangle_understates_perimeter,
        "footprint_exceeds_gla_per_storey": answer.footprint_exceeds_gla_per_storey,
    }


# ------------------------------------------------------------------ running


@dataclass(slots=True)
class Outcome:
    """One setup on one house, one repeat."""

    setup: str
    house: str
    repeat: int
    answer: dict[str, Any] | None = None
    quantities: dict[str, float] = field(default_factory=dict)
    corner_quantities: dict[str, float] = field(default_factory=dict)
    judgments: dict[str, bool] = field(default_factory=dict)
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    calls: int = 0
    seconds: float = 0.0
    models: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: str | None = None


def ledger_totals(records: Sequence[CallRecord], entity: uuid.UUID) -> dict[str, Any]:
    """Dollars, tokens and answering models of the ledger rows about ``entity``."""
    mine = [r for r in records if r.entry.entity_id == entity]
    return {
        "cost_usd": float(sum((r.cost_usd for r in mine), Decimal(0))),
        "input_tokens": sum(r.entry.usage.input_tokens for r in mine),
        "output_tokens": sum(r.entry.usage.output_tokens for r in mine),
        "reasoning_tokens": sum(r.entry.usage.reasoning_output_tokens for r in mine),
        "calls": len(mine),
        "models": sorted({r.entry.model for r in mine}),
    }


async def run_one(
    provider: ChatProvider,
    ctx: CallContext,
    setup: Setup,
    house: mt.House,
    pictures: Pictures,
    repeat: int,
    records: Sequence[CallRecord],
) -> Outcome:
    """One call (two attempts on an unusable answer, transient errors retried) through the
    production call path; the ledger rows of this job make its cost."""
    from evals.predict_multiview import call_with_retries

    entity = uuid.uuid4()
    job_ctx = ctx.about("measure_house", entity)
    request = build_request(
        setup, house, pictures, study_id=str(ctx.study_id), activity=ctx.activity
    )
    started = time.monotonic()
    parsed, attempts = await call_with_retries(provider, request, job_ctx)
    out = Outcome(setup.id, house.id, repeat, seconds=time.monotonic() - started)
    out.errors = list(attempts.get("errors") or [])
    for key, value in ledger_totals(records, entity).items():
        setattr(out, key, value)
    if isinstance(parsed, ExteriorAnswer):
        out.answer = parsed.model_dump(mode="json")
        out.quantities = answer_quantities(parsed)
        out.judgments = answer_judgments(parsed)
        if isinstance(parsed, PixelAnswer):
            out.corner_quantities = corner_quantities(parsed, pictures.pixel_size_m)
    return out


async def run_jobs(
    provider: ChatProvider,
    ctx: CallContext,
    records: Sequence[CallRecord],
    jobs: Sequence[tuple[Setup, mt.House, Pictures, int]],
    *,
    concurrency: int = 4,
) -> list[Outcome]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(job: tuple[Setup, mt.House, Pictures, int]) -> Outcome:
        setup, house, pictures, repeat = job
        async with semaphore:
            return await run_one(provider, ctx, setup, house, pictures, repeat, records)

    return list(await asyncio.gather(*(one(j) for j in jobs)))


def new_context(budget: Budget, recorder: Any, activity: str = "evals.measure_vlm") -> CallContext:
    return CallContext(
        study_id=uuid.uuid4(), run_id=None, activity=activity, budget=budget, recorder=recorder
    )


def is_claude(setup: Setup) -> bool:
    return is_anthropic_model(setup.model.model)


__all__ = [
    "ANSWER_MODELS",
    "ExteriorAnswer",
    "ModelSetup",
    "Outcome",
    "PROMPTS",
    "PixelAnswer",
    "Pictures",
    "Setup",
    "answer_judgments",
    "answer_quantities",
    "build_request",
    "choose_view",
    "corner_quantities",
    "estimate_job_usd",
    "judgment_truths",
    "load_pictures",
    "message_text",
    "models_from_settings",
    "parse_setups",
    "run_jobs",
    "skip_reason",
    "subject_pixel",
]
