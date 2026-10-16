"""Localized objects of a photo: response bodies and the engineer's manual selection.

Geometry is normalized to the photo as the model saw it (the prepared image: EXIF
orientation applied, resized), origin top left, so it is independent of the pixel size the
client displays. A mask is uncompressed COCO-style run lengths over the row-major
flattening of a ``height x width`` binary mask, starting with a run of zeros (COCO's own
uncompressed RLE is column-major; row-major reads in drawing order).

A manual selection is either a tap (``kind="point"``) or a drawn box (``kind="box"``).
Every value the schema allows is accepted: box corners are sorted, so ``x1 > x2`` is the
same box as ``x2 < x1``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from basis.api.schemas.common import JSON_NUMBER, RequestModel, ResponseModel
from basis.vision.schemas import ComponentLabel

Unit = Annotated[float, Field(ge=0.0, le=1.0), JSON_NUMBER]
LocalizationStatusName = Literal["done", "failed", "skipped"]


class BoxOut(ResponseModel):
    x1: float = Field(description="Left edge, 0 to 1 of the image width.")
    y1: float = Field(description="Top edge, 0 to 1 of the image height.")
    x2: float = Field(description="Right edge (x1 <= x2).")
    y2: float = Field(description="Bottom edge (y1 <= y2).")


class MaskOut(ResponseModel):
    height: int
    width: int
    counts: list[int] = Field(
        description="Run lengths over the row-major flattened mask, zeros first."
    )


class PhotoObjectOut(ResponseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    revision: int
    label: str
    phrase: str | None = Field(description="The phrase SAM 3 was prompted with (model rows).")
    score: float | None = Field(description="SAM 3's score; null for a manual box.")
    box: BoxOut
    mask: MaskOut | None
    area: float = Field(description="Share of the image the instance covers.")
    source: Literal["model", "manual"]
    counted: bool = Field(
        description="Counted in `counts`: a model instance whose score clears its class's "
        "threshold, or any manual instance. Uncounted model instances are candidates."
    )
    derived_from: uuid.UUID | None = Field(
        description="For a manual instance: the candidate the tap or box resolved to."
    )
    created_by: uuid.UUID | None = Field(description="Who added a manual instance.")
    created_at: datetime
    deleted_at: datetime | None = None
    deleted_by: uuid.UUID | None = None


class LocalizationOut(ResponseModel):
    status: LocalizationStatusName
    reason: str | None
    instances: int
    model_version: str | None
    carried_from_revision: int | None
    created_at: datetime


class PhotoObjectsOut(ResponseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: uuid.UUID
    revision: int
    localization: LocalizationOut | None = Field(
        description="The localization outcome; null when the photo has not been localized "
        "at this revision (not analyzed yet, or analyzed before localization existed)."
    )
    counts: dict[str, int] = Field(description="Counted instances per label.")
    items: list[PhotoObjectOut] = Field(
        description="Active instances by label, highest score first, manual boxes last."
    )


class PointIn(RequestModel):
    x: Unit
    y: Unit


class BoxIn(RequestModel):
    x1: Unit
    y1: Unit
    x2: Unit
    y2: Unit


class PointSelection(RequestModel):
    kind: Literal["point"]
    label: ComponentLabel
    point: PointIn


class BoxSelection(RequestModel):
    kind: Literal["box"]
    label: ComponentLabel
    box: BoxIn


ManualSelectionIn = Annotated[PointSelection | BoxSelection, Field(discriminator="kind")]


class ManualSelectionOut(ResponseModel):
    object: PhotoObjectOut
    created: bool = Field(
        description="False when the selection resolved to an instance already counted "
        "(nothing was added)."
    )
