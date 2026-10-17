"""Wall, floor and ceiling masks for the surface-crop material look (EVAL_REPORT 26.3).

``MATERIALS_SECOND_LOOK=surface_crops`` (:mod:`basis.vision.surface_crops`) needs, per
photo, the pixels a trained segmenter calls ``wall``, ``floor`` and ``ceiling``
(:func:`basis.vision.ade20k.surface`). Two backends, chosen by ``SURFACE_SEGMENTER``:

* :class:`LocalMask2Former` (``local``, the default): the checkpoint section 26.3 measured,
  ``facebook/mask2former-swin-large-ade-panoptic``, through ``transformers`` in the worker
  process, on the CPU (about 0.5 s per 800 px photo after a 20 s load; the weights, about
  0.9 GB, are fetched once into the Hugging Face cache). Exactly ``evals.predict_detector``'s
  segmenter: panoptic post-processing at score 0.05 with no class fused, then the union of
  each surface class's segments. The model is loaded on first use and inference runs in a
  thread, one photo at a time (torch already uses every core).
* :class:`ReplicateMask2Former` (``replicate``): the community deployment
  ``oitoito/mask2former`` by version (``SURFACE_SEGMENTER_REPLICATE_VERSION``), which runs
  ``facebook/mask2former-swin-small-ade-semantic`` on a T4 and returns a colour-coded PNG
  (inline, as a data URI) with each class's colour. **Not measured** on the eval set: a
  smaller checkpoint and semantic, not panoptic, segmentation. On the kitchen photo its
  masks overlap the local model's at IoU 0.79 (wall), 0.98 (ceiling) and 0.96 (floor), for
  $0.0005, after a 99 s cold start (2026-10-16). Requests go through the SAM 3 client's
  HTTP handling (:class:`basis.vision.localize.ReplicateSam3Localizer`: bounded retries,
  the token only to the API host); predict seconds are charged to the budget at the T4
  rate.

Any failure raises :class:`SegmenterError` (the caller falls back to the per-photo
material and flags the room); nothing is retried here beyond the HTTP client's retries.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import threading
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import numpy as np
import numpy.typing as npt
from PIL import Image

from basis.llm.provider import Budget
from basis.vision import ade20k
from basis.vision.localize import (
    RESERVE_SECONDS,
    LocalizerError,
    ReplicateSam3Localizer,
    build_replicate_localizer,
)
from basis.vision.prep import ImageRejected, prepare_image
from basis.vision.surface_crops import SURFACES, Surface

Mask = npt.NDArray[np.bool_]
SurfaceMasks = dict[Surface, Mask]

LOCAL_CHECKPOINT: Final = "facebook/mask2former-swin-large-ade-panoptic"
# evals.predict_detector.CANDIDATE_SCORE: every segment the study's masks were built from.
SCORE_FLOOR: Final = 0.05
# Replicate T4 (the hardware of oitoito/mask2former), per predict second.
T4_USD_PER_SECOND: Final = 0.000225


class SegmenterError(RuntimeError):
    """The segmenter could not give masks for a photo."""


class SurfaceSegmenter(Protocol):
    """``image`` (RGB) -> a mask per surface it found, each the image's size."""

    @property
    def name(self) -> str: ...

    async def segment(self, image: Image.Image, *, budget: Budget | None = None) -> SurfaceMasks:
        """Raise :class:`SegmenterError` on any failure."""
        ...


def union_by_surface(segments: list[tuple[str, Mask]]) -> SurfaceMasks:
    """``(ADE20K class name, mask)`` pairs -> the union of each surface class's masks."""
    merged: SurfaceMasks = {}
    for name, mask in segments:
        kind = ade20k.surface(name)
        if kind is None or kind not in SURFACES:
            continue
        surface: Surface = kind
        merged[surface] = merged[surface] | mask if surface in merged else mask.copy()
    return merged


def _transformers() -> Any:
    import importlib

    return importlib.import_module("transformers")


@dataclass
class LocalMask2Former:
    """The measured checkpoint in this process (module doc)."""

    checkpoint: str = LOCAL_CHECKPOINT
    device: str = "cpu"
    _model: Any = field(default=None, init=False, repr=False)
    _processor: Any = field(default=None, init=False, repr=False)
    _names: dict[int, str] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def name(self) -> str:
        return f"local:{self.checkpoint}"

    def _load(self) -> None:
        if self._model is not None:
            return
        hf = _transformers()
        self._processor = hf.AutoImageProcessor.from_pretrained(self.checkpoint)
        model = hf.AutoModelForUniversalSegmentation.from_pretrained(
            self.checkpoint, use_safetensors=True
        )
        model.to(self.device).eval()
        self._names = {int(k): str(v) for k, v in model.config.id2label.items()}
        self._model = model

    def segment_sync(self, image: Image.Image) -> SurfaceMasks:
        import torch

        with self._lock:
            self._load()
            inputs = self._processor(images=image, return_tensors="pt")
            inputs = {k: v.to(self.device) if hasattr(v, "to") else v for k, v in inputs.items()}
            with torch.no_grad():
                outputs = self._model(**inputs)
            result = self._processor.post_process_panoptic_segmentation(
                outputs,
                threshold=SCORE_FLOOR,
                label_ids_to_fuse=set(),
                target_sizes=[(image.height, image.width)],
            )[0]
        segmentation = result["segmentation"].cpu().numpy()
        return union_by_surface(
            [
                (self._names[int(s["label_id"])], segmentation == s["id"])
                for s in result["segments_info"]
            ]
        )

    async def segment(self, image: Image.Image, *, budget: Budget | None = None) -> SurfaceMasks:
        try:
            return await asyncio.to_thread(self.segment_sync, image)
        except Exception as exc:  # the model, torch or the weights download
            raise SegmenterError(f"{self.name}: {type(exc).__name__}: {exc}"[:500]) from exc


def masks_from_colours(
    png: bytes, objects: list[dict[str, Any]], size: tuple[int, int]
) -> SurfaceMasks:
    """A colour-coded segmentation PNG and its ``[{label, color}]`` legend -> surface masks
    at ``size`` (width, height; nearest-neighbour when the PNG differs)."""
    picture = Image.open(io.BytesIO(png)).convert("RGB")
    if picture.size != size:
        picture = picture.resize(size, Image.Resampling.NEAREST)
    pixels = np.asarray(picture)
    segments: list[tuple[str, Mask]] = []
    for obj in objects:
        colour = obj.get("color")
        label = obj.get("label")
        if not isinstance(label, str) or not isinstance(colour, list) or len(colour) != 3:
            continue
        segments.append((label, np.all(pixels == np.asarray(colour, dtype=pixels.dtype), axis=-1)))
    return union_by_surface(segments)


async def _segment_bytes(client: ReplicateSam3Localizer, segment: str) -> bytes:
    """The segmentation PNG: inline (``data:image/png;base64,...``, what the deployment
    returns) or a file URL (fetched without the token)."""
    if segment.startswith("data:"):
        header, _, data = segment.partition(",")
        if not header.endswith(";base64"):
            raise SegmenterError("the segmentation is an unencoded data URI")
        try:
            return base64.b64decode(data, validate=True)
        except binascii.Error as exc:
            raise SegmenterError(f"the segmentation is not base64: {exc}") from exc
    if not segment.startswith("https://"):
        raise SegmenterError("the segmentation is neither a data URI nor an https URL")
    return (await client._request("GET", segment)).content


@dataclass
class ReplicateMask2Former:
    """``oitoito/mask2former`` on Replicate (module doc; not measured)."""

    client: ReplicateSam3Localizer
    version: str

    @property
    def name(self) -> str:
        return f"replicate:oitoito/mask2former@{self.version[:12]}"

    async def segment(self, image: Image.Image, *, budget: Budget | None = None) -> SurfaceMasks:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90)
        try:
            prepared = prepare_image(buffer.getvalue())
        except ImageRejected as exc:
            raise SegmenterError(f"{self.name}: {exc}") from exc
        input_ = {"image": f"data:image/jpeg;base64,{prepared.base64}"}
        client = self.client  # shared, built without a budget: the caller's is charged here
        if budget is not None:
            budget.reserve(RESERVE_SECONDS * client.usd_per_second)
        try:
            # The SAM 3 client's create-poll cycle and retries (localize module doc).
            prediction, call = await client._predict(input_, ("mask2former",))
            if budget is not None and call.cost_usd > 0:
                budget.charge(call.cost_usd)
            output = prediction.get("output")
            if not isinstance(output, dict) or not isinstance(output.get("segment"), str):
                raise SegmenterError(f"{self.name}: no segment in the prediction's output")
            objects = output.get("objects")
            png = await _segment_bytes(client, output["segment"])
        except LocalizerError as exc:
            raise SegmenterError(f"{self.name}: {exc}") from exc
        try:
            return await asyncio.to_thread(
                masks_from_colours, png, objects if isinstance(objects, list) else [], image.size
            )
        except Exception as exc:
            raise SegmenterError(f"{self.name}: unreadable segmentation: {exc}") from exc


def build_segmenter(
    kind: str, *, token: str | None = None, version: str = "", concurrency: int = 4
) -> SurfaceSegmenter:
    """The ``SURFACE_SEGMENTER`` backend (the settings refuse ``replicate`` without a
    token)."""
    if kind == "local":
        return LocalMask2Former()
    if kind == "replicate":
        if not token:
            raise ValueError("SURFACE_SEGMENTER=replicate needs REPLICATE_API_TOKEN")
        client = build_replicate_localizer(token, version, concurrency=concurrency)
        client.usd_per_second = T4_USD_PER_SECOND
        return ReplicateMask2Former(client=client, version=version)
    raise ValueError(f"unknown surface segmenter {kind!r}")


__all__ = [
    "LOCAL_CHECKPOINT",
    "LocalMask2Former",
    "ReplicateMask2Former",
    "SegmenterError",
    "SurfaceMasks",
    "SurfaceSegmenter",
    "build_segmenter",
    "masks_from_colours",
    "union_by_surface",
]
