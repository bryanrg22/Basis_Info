"""The parcel's area against the appraisal's site area (EVAL_REPORT 101).

The appraisal's site area is ``site.area_sqft`` (square feet, converted from acres when the
form printed acres; ``basis.extraction.derived``), from the study's newest extraction. The intake
has no site area of its own (``basis.domain.intake``), so the appraisal is the one figure to
check.

Each parcel offers up to two figures: the layer's own acres (the assessor's or deed's, what an
appraisal usually copies) and its polygon measured on the ellipsoid. The rule:

1. ``agree``: either figure of the parcel under the pin is within ``tolerance_pct`` of the
   appraisal;
2. ``adjacent_match`` (``E.GEO_SITE_AREA_ADJACENT``, a note, not a flag): the parcel under the
   pin together with some of the same owner's adjacent parcels sums to within the tolerance (the
   smallest such set, by the layer's acres where every parcel in it has them, else polygons);
3. ``disagree`` (``E.GEO_SITE_AREA_MISMATCH``, a review flag) otherwise;
4. ``no_appraisal`` / ``no_parcel`` when one side is missing (nothing to check).

The difference is ``(parcel - appraisal) / appraisal``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Final, Literal, Protocol

from basis.errors.codes import E
from basis.geo.geometry import M2_PER_ACRE, SQFT_PER_M2

MAX_SUBSET: Final = 8
Status = Literal["agree", "adjacent_match", "disagree", "no_appraisal", "no_parcel"]


class ParcelArea(Protocol):
    @property
    def parcel_id(self) -> str | None: ...
    @property
    def under_pin(self) -> bool: ...
    @property
    def area_m2(self) -> float: ...
    @property
    def layer_acres(self) -> float | None: ...


@dataclass(frozen=True, slots=True)
class SiteCheck:
    status: Status
    flag: bool
    code: str | None
    appraisal_sqft: float | None
    parcel_sqft: float | None  # the figure compared: layer acres or polygon, of the pin's parcel
    layer_sqft: float | None
    polygon_sqft: float | None
    difference_pct: float | None
    parcels: tuple[str | None, ...]  # the parcel ids in the matching set
    combined_sqft: float | None
    tolerance_pct: float
    message: str


def _pct(value: float, reference: float) -> float:
    return (value - reference) / reference * 100.0


def _sqft(p: ParcelArea, *, layer: bool) -> float | None:
    if layer:
        return None if p.layer_acres is None else p.layer_acres * M2_PER_ACRE * SQFT_PER_M2
    return p.area_m2 * SQFT_PER_M2


def check(
    parcels: Sequence[ParcelArea], appraisal_sqft: float | None, *, tolerance_pct: float
) -> SiteCheck:
    under = [p for p in parcels if p.under_pin]
    pin = under[0] if under else None
    layer = None if pin is None else _sqft(pin, layer=True)
    polygon = None if pin is None else _sqft(pin, layer=False)

    def result(
        status: Status,
        code: str | None,
        message: str,
        *,
        parcel: float | None,
        diff: float | None,
        ids: tuple[str | None, ...],
        combined: float | None,
    ) -> SiteCheck:
        return SiteCheck(
            status=status,
            flag=status == "disagree",
            code=code,
            appraisal_sqft=appraisal_sqft,
            parcel_sqft=parcel,
            layer_sqft=layer,
            polygon_sqft=polygon,
            difference_pct=diff,
            parcels=ids,
            combined_sqft=combined,
            tolerance_pct=tolerance_pct,
            message=message,
        )

    if pin is None:
        return result(
            "no_parcel",
            None,
            "No parcel was found under the pin.",
            parcel=None,
            diff=None,
            ids=(),
            combined=None,
        )
    if appraisal_sqft is None or appraisal_sqft <= 0:
        return result(
            "no_appraisal",
            None,
            "The appraisal's site area is not read yet.",
            parcel=layer if layer is not None else polygon,
            diff=None,
            ids=(pin.parcel_id,),
            combined=None,
        )
    figures = [f for f in (layer, polygon) if f is not None]
    best = min(figures, key=lambda f: abs(_pct(f, appraisal_sqft)))
    diff = _pct(best, appraisal_sqft)
    if abs(diff) <= tolerance_pct:
        return result(
            "agree",
            None,
            f"The parcel's area is within {abs(diff):.1f} % of the appraisal's site area.",
            parcel=best,
            diff=diff,
            ids=(pin.parcel_id,),
            combined=best,
        )
    others = [p for p in parcels if not p.under_pin][:MAX_SUBSET]
    for size in range(1, len(others) + 1):
        matches: list[tuple[float, float, tuple[ParcelArea, ...]]] = []
        for subset in combinations(others, size):
            group = (pin, *subset)
            use_layer = all(p.layer_acres is not None for p in group)
            total = sum(_sqft(p, layer=use_layer) or 0.0 for p in group)
            d = _pct(total, appraisal_sqft)
            if abs(d) <= tolerance_pct:
                matches.append((d, total, group))
        if matches:
            d, total, group = min(matches, key=lambda m: abs(m[0]))
            return result(
                "adjacent_match",
                E.GEO_SITE_AREA_ADJACENT,
                f"The parcel under the pin and {size} adjacent parcel"
                f"{'s' if size > 1 else ''} of the same owner together are within "
                f"{abs(d):.1f} % of the appraisal's site area.",
                parcel=best,
                diff=d,
                ids=tuple(p.parcel_id for p in group),
                combined=total,
            )
    return result(
        "disagree",
        E.GEO_SITE_AREA_MISMATCH,
        f"The parcel's area differs from the appraisal's site area by {diff:+.1f} % "
        f"(more than {tolerance_pct:g} %). Check the site area and the pin.",
        parcel=best,
        diff=diff,
        ids=(pin.parcel_id,),
        combined=best,
    )
