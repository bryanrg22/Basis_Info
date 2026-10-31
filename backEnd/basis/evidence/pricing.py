"""What a revision's dollars rest on, and whether that is still what the study's inputs give
(docs/api/STUDY_RESULT.md "Pricing"; docs/api/REPRICE.md).

A priced revision records its pricing on every cost row (``cost_estimates.trail``,
:mod:`basis.domain.cost_trail`): the cost year and its year factor with both index values
and their store rows, the location factor with its row, the intake version and the property
class, and (on the concealed rows' takeoff inputs) the engineer's square-foot model when one
was set. :func:`recorded_pricing` reads that record back; :func:`structure_inputs_as_priced`
gives the result the structure inputs of the revision *as it was priced*, so the top-down
building RCN, the carve-out and the reconciliation are computed with the same index as the
rows they are compared with. A newer index or an intake changed after the pricing never
reaches those figures: :func:`pricing_status` reports it as ``stale`` with the reasons, and a
reprice applies it in a new revision.

A revision priced before the trail existed has no record: its result keeps computing the
structure from the current inputs (as before) and says ``not_recorded``; nothing is
reconstructed from rounded amounts.

Pure, except :func:`load_recorded` which reads the rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from basis.config import Settings
from basis.db.repositories.asset import AssetRepository
from basis.domain.cost_indices import StoreRow, YearFactor, YearIndex
from basis.domain.structure import ModelOverride, StructureInputs, select_model

STRUCTURE_MODEL_INPUT = "structure_model"
Reason = Literal[
    "not_recorded",
    "intake_changed",
    "cost_year_changed",
    "index_updated",
    "location_changed",
    "property_class_changed",
    "method_changed",
]
METHOD_KEYS = (
    "cost_basis",
    "overhead_allocation",
    "modifications",
    "specialties_residual",
    "rcn_source",
)


def pricing_method(settings: Settings) -> dict[str, str | bool]:
    """The pricing method of a revision priced now (recorded on its trails)."""
    return {
        "cost_basis": settings.cost_basis,
        "overhead_allocation": settings.structure_overhead_allocation,
        "modifications": settings.structure_modifications == "on",
        "specialties_residual": settings.structure_specialties_residual,
        "rcn_source": settings.structure_rcn_source,
    }


class PricedWithOut(BaseModel):
    """What a revision's dollars rest on: recorded on its cost rows when it was priced
    (`priced_with`), or what a rebuild would use now (`current`)."""

    recorded: bool = Field(
        description="False for a revision priced before the cost trail was recorded (its "
        "factors are not known and are not reconstructed). Always true for `current`."
    )
    cost_year: int | None
    year_factor: Decimal | None
    year_citation: str | None
    year_store_version: int | None = Field(
        description="The store version of the cost year's index value (null: the released book)."
    )
    location_factor: Decimal | None
    location_citation: str | None
    intake_version: int | None
    property_class: str | None
    model_class: str | None = Field(description="The structure model's class of construction.")
    building_type: str | None = Field(description="The structure model's building type.")
    living_area_sf: Decimal | None = Field(
        description="The living area the square-foot model is sized by, when an engineer set it."
    )
    method: dict[str, str | bool] | None = Field(
        default=None,
        description="The pricing method (STRUCTURE_MODEL.md section 9): `cost_basis` (bare or "
        "installed), `overhead_allocation` (pro_rata or none), `modifications`, "
        "`specialties_residual`, `rcn_source`. Null for a revision priced before it was "
        "recorded.",
    )


class PricingStatusOut(BaseModel):
    """Whether the revision's recorded pricing is still what the study's inputs give."""

    priced_with: PricedWithOut
    current: PricedWithOut
    stale: bool = Field(
        description="The recorded pricing differs from what the current intake and index "
        "store give. A new index or intake never changes stored dollars: a reprice applies "
        "them in a new revision. A stale revision's allocation is withheld."
    )
    reasons: list[Reason] = Field(
        description="`not_recorded` (priced before the trail; not stale by itself), "
        "`intake_changed` (the intake version moved since the pricing), `cost_year_changed`, "
        "`index_updated` (the cost year's index value or its store version differs), "
        "`location_changed`, `property_class_changed`, `method_changed` (a pricing setting "
        "of STRUCTURE_MODEL.md section 9 changed since)."
    )
    remedy: Literal["reprice", "reject_assets"] | None = Field(
        description="`reprice`: POST /v1/studies/{id}/assets/reprice (classifications carried, "
        "no model call); `reject_assets`: the property class changed, so the assets must be "
        "classified again (reject the assets review); null when nothing is stale."
    )


@dataclass(frozen=True, slots=True)
class Pricing:
    """One side of the comparison (recorded, or current)."""

    recorded: bool
    cost_year: int | None
    year: YearFactor | None
    year_source: str | None
    location_factor: Decimal | None
    location_source: str | None
    location_citation: str | None
    intake_version: int | None
    property_class: str | None
    model_override: ModelOverride | None = None
    method: dict[str, str | bool] | None = None


def _index(block: Mapping[str, Any]) -> YearIndex:
    store = block.get("store")
    return YearIndex(
        year=int(block["year"]),
        index=Decimal(str(block["value"])),
        as_of=date.fromisoformat(str(block["as_of"])),
        source=str(block["citation"]),
        estimated=bool(block.get("estimated")),
        derived=block.get("derived"),
        store=None
        if not isinstance(store, dict)
        else StoreRow(
            id=None if store.get("id") is None else uuid.UUID(str(store["id"])),
            source=str(store.get("source")),
            version=int(store.get("version") or 0),
        ),
    )


def year_from_trail(block: Mapping[str, Any]) -> YearFactor:
    """The time factor exactly as recorded (the factor's own four decimals, not divided
    again)."""
    return YearFactor(
        cost_year=int(block["cost_year"]),
        factor=Decimal(str(block["factor"])),
        index=_index(block["index"]),
        base=_index(block["base"]),
        assumption=block.get("assumption"),
    )


def recorded_pricing(trails: Iterable[Any], concealed_inputs: Iterable[Any] = ()) -> Pricing | None:
    """The pricing recorded on a revision's cost rows; None when no row has a trail. The
    first itemized (``unit_price``/``unpriced``) trail names the year and location every
    row was priced with (one ``TakeoffPlan`` per revision); a revision with only
    structure rows takes a residual trail's."""
    chosen: Mapping[str, Any] | None = None
    fallback: Mapping[str, Any] | None = None
    any_trail: Mapping[str, Any] | None = None
    for trail in trails:
        if not isinstance(trail, dict):
            continue
        any_trail = any_trail or trail
        if trail.get("year") is None:
            continue
        if trail.get("kind") in ("unit_price", "unpriced") and chosen is None:
            chosen = trail
        fallback = fallback or trail
    picked = chosen or fallback
    if picked is None:
        if any_trail is None:
            return None
        picked = any_trail
    override = None
    for inputs in concealed_inputs:
        if isinstance(inputs, dict) and inputs.get(STRUCTURE_MODEL_INPUT) is not None:
            override = ModelOverride.from_record(inputs[STRUCTURE_MODEL_INPUT])
            break
    year_block = picked.get("year")
    found = picked.get("location")
    location: Mapping[str, Any] = found if isinstance(found, dict) else {}
    factor = picked.get("location_factor")
    return Pricing(
        recorded=True,
        cost_year=None if picked.get("cost_year") is None else int(picked["cost_year"]),
        year=None if not isinstance(year_block, dict) else year_from_trail(year_block),
        year_source=None if not isinstance(year_block, dict) else year_block.get("from"),
        location_factor=None if factor is None else Decimal(str(factor)),
        location_source=location.get("from"),
        location_citation=location.get("citation"),
        intake_version=picked.get("intake_version"),
        property_class=picked.get("property_class"),
        model_override=override,
        method=picked.get("method") if isinstance(picked.get("method"), dict) else None,
    )


def current_pricing(
    structure: StructureInputs | None,
    *,
    intake_version: int | None,
    property_class: str,
    location_citation: str | None,
    method: dict[str, str | bool] | None = None,
) -> Pricing:
    return Pricing(
        recorded=True,
        cost_year=None if structure is None else structure.cost_year,
        year=None if structure is None else structure.year,
        year_source=None if structure is None else structure.year_source,
        location_factor=None if structure is None else structure.location_factor,
        location_source=None if structure is None else structure.location_source,
        location_citation=location_citation,
        intake_version=intake_version,
        property_class=property_class,
        model_override=None if structure is None else structure.model_override,
        method=method,
    )


def structure_inputs_as_priced(
    live: StructureInputs | None, recorded: Pricing | None
) -> StructureInputs | None:
    """The revision's structure inputs with the year, location and square-foot model it was
    priced with (the appraisal readings are the revision's own, so they are the same); the
    live inputs unchanged when nothing was recorded."""
    if live is None or recorded is None or recorded.year is None:
        return live
    return replace(
        live,
        cost_year=recorded.year.cost_year,
        year=recorded.year,
        year_source=recorded.year_source,
        location_factor=recorded.location_factor
        if recorded.location_factor is not None
        else live.location_factor,
        location_source=recorded.location_source,
        model_override=recorded.model_override,
        **_method_inputs(recorded.method),
    )


def _method_inputs(method: dict[str, str | bool] | None) -> dict[str, Any]:
    """The recorded pricing method as ``StructureInputs`` members (none when not recorded:
    a revision priced before section 9 keeps the live settings, as before)."""
    if not method:
        return {}
    found: dict[str, Any] = {}
    if isinstance(method.get("cost_basis"), str):
        found["cost_basis"] = method["cost_basis"]
    if isinstance(method.get("overhead_allocation"), str):
        found["overhead_allocation"] = method["overhead_allocation"]
    if isinstance(method.get("modifications"), bool):
        found["modifications"] = method["modifications"]
    if isinstance(method.get("specialties_residual"), str):
        found["specialties_residual"] = method["specialties_residual"]
    if isinstance(method.get("rcn_source"), str):
        found["rcn_source"] = method["rcn_source"]
    return found


def _store_version(year: YearFactor | None) -> int | None:
    store = None if year is None else year.index.store
    return None if store is None else store.version


def priced_with_out(pricing: Pricing | None, structure: StructureInputs | None) -> PricedWithOut:
    if pricing is None:
        return PricedWithOut(
            recorded=False,
            cost_year=None,
            year_factor=None,
            year_citation=None,
            year_store_version=None,
            location_factor=None,
            location_citation=None,
            intake_version=None,
            property_class=None,
            model_class=None,
            building_type=None,
            living_area_sf=None,
        )
    model_class = building_type = None
    if structure is not None:
        measurements = structure.measurements
        if pricing.model_override is not None:
            measurements = pricing.model_override.apply(measurements)
        model = select_model(measurements, structure.cost_approach, pricing.model_override).model
        model_class, building_type = model.cls, model.type
    return PricedWithOut(
        recorded=pricing.recorded,
        cost_year=pricing.cost_year,
        year_factor=None if pricing.year is None else pricing.year.factor,
        year_citation=None if pricing.year is None else pricing.year.citation(),
        year_store_version=_store_version(pricing.year),
        location_factor=pricing.location_factor,
        location_citation=pricing.location_citation,
        intake_version=pricing.intake_version,
        property_class=pricing.property_class,
        model_class=model_class,
        building_type=building_type,
        living_area_sf=None
        if pricing.model_override is None
        else pricing.model_override.living_area_sf,
        method=pricing.method,
    )


def pricing_status(
    recorded: Pricing | None, current: Pricing, structure: StructureInputs | None
) -> PricingStatusOut:
    """The comparison: every difference that would change the dollars, and the remedy."""
    reasons: list[Reason] = []
    if recorded is None:
        reasons.append("not_recorded")
    else:
        if (
            recorded.intake_version is not None or current.intake_version is not None
        ) and recorded.intake_version != current.intake_version:
            reasons.append("intake_changed")
        if recorded.cost_year != current.cost_year:
            reasons.append("cost_year_changed")
        elif (
            recorded.year is not None
            and current.year is not None
            and (
                recorded.year.factor != current.year.factor
                or recorded.year.index.index != current.year.index.index
                or _store_version(recorded.year) != _store_version(current.year)
            )
        ):
            reasons.append("index_updated")
        if (
            recorded.location_factor is not None
            and current.location_factor is not None
            and recorded.location_factor != current.location_factor
        ):
            reasons.append("location_changed")
        if (
            recorded.property_class is not None
            and recorded.property_class != current.property_class
        ):
            reasons.append("property_class_changed")
        if (
            recorded.method is not None
            and current.method is not None
            and recorded.method != current.method
        ):
            reasons.append("method_changed")
    stale = any(r != "not_recorded" for r in reasons)
    remedy: Literal["reprice", "reject_assets"] | None = None
    if "property_class_changed" in reasons:
        remedy = "reject_assets"
    elif stale:
        remedy = "reprice"
    as_priced = structure_inputs_as_priced(structure, recorded)
    return PricingStatusOut(
        priced_with=priced_with_out(recorded, as_priced),
        current=priced_with_out(current, structure),
        stale=stale,
        reasons=reasons,
        remedy=remedy,
    )


async def load_recorded(
    session: AsyncSession, study_id: uuid.UUID, revision: int
) -> Pricing | None:
    """The recorded pricing of a revision's rows (:func:`recorded_pricing`)."""
    repo = AssetRepository(session)
    assets = await repo.list_for_revision(study_id, revision)
    ids = [a.id for a in assets]
    costs = await repo.cost_estimates(ids)
    takeoffs = await repo.takeoffs(ids)
    trails = [costs[i].trail for i in ids if i in costs]
    concealed = [
        takeoffs[a.id].inputs for a in assets if a.component_id is None and a.id in takeoffs
    ]
    return recorded_pricing(trails, concealed)


__all__ = [
    "PricedWithOut",
    "Pricing",
    "PricingStatusOut",
    "current_pricing",
    "load_recorded",
    "pricing_method",
    "pricing_status",
    "recorded_pricing",
    "structure_inputs_as_priced",
    "year_from_trail",
]
