"""``azure_1004`` (Azure's ``prebuilt-mortgage.us.1004``) and ``production_plus_1004``.

**azure_1004.** The prebuilt model trained on the legacy URAR 1004 layout (Document
Intelligence v4.0, 2024-11-30 GA). Its field schema, fetched from the configured resource
(``GET .../documentModels/prebuilt-mortgage.us.1004?api-version=2024-11-30``, free, 2026-10-26),
covers about 50 catalogue keys (:data:`FIELD_MAP`): subject, contract, the neighborhood and
site checkboxes, FEMA, the improvements header (units, type, year built, effective age,
foundation, basement), the indicated values and the reconciliation, the appraiser block. It
has no GLA, site area or site value, no cost-approach lines, no rooms, no condition/quality
(research 3.1): those keys are not predicted. Values:

* a ``selectionGroup`` field is the selected options' labels as Azure returns them, joined by
  ", " (an "Other" option with its write-in field: ``"Other: <text>"``); an address field
  gives the street (``streetAddress``, else house number + road, else the content) and the
  city, state and zip boxes from its parts; any other field is its ``content`` as printed
  (the production cleaners parse it), else its typed value;
* confidence: Azure's own field confidence (the production rule for Azure DI values);
* region: the field's bounding polygon; for a selection group, narrowed to the selected
  option's label inside it (the group's box covers every option).

Recorded as ``recordings/<sha>/azure_1004.json`` (the raw ``as_dict()``) and replayed.

**production_plus_1004** merges the production result with the 1004 values, key by key:

* only one reader has a value: keep it (a 1004-only value keeps its Azure confidence);
* both agree (:func:`~evals.appraisal.systems.base.same_value`): keep the production field,
  confidence ``max(production, 1004)`` and ``corroborated_by += azure_1004``. Agreement of
  two independent readers never lowers a confidence and lets the trained form model vouch
  for a capped model value; it does not invent a number above what either reader claimed;
* they disagree on a **checkbox** key (:data:`CHECKBOX_KEYS`): the 1004 value wins. The
  measured dominant defect is production reading the text beside a box (the layout lines
  carry no marks); the 1004 model reads the selection group itself;
* they disagree elsewhere: the production value stays (Azure layout key-value pairs and the
  verbatim guard, measured 111/122 on the client), since the 1004 model has no measurement
  yet on typed boxes;
* every disagreement: confidence ``min(production, 1004, 0.5)`` and a ``sources_disagree``
  issue (warning) naming the key, so the reviewer sees it (the scorer counts the key as
  flagged).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from basis.config import Settings
from basis.extraction.azure_di import map_azure_error
from basis.extraction.field_mappings import FIELD_BY_KEY
from basis.extraction.results import Candidate, ExtractedField, ValidationIssue
from evals.appraisal.systems.base import (
    CHECKBOX_KEYS,
    MORTGAGE_1004_MODEL_ID,
    SystemEnv,
    SystemOutput,
    finish,
    own_issues,
    same_value,
)
from evals.appraisal.systems.layout import Box, Layout, box_of, from_raw, locate, region
from evals.appraisal.systems.production import Production, production_regions

DISAGREE_CONFIDENCE: Final = 0.5

# (path in the 1004 result, catalogue key, how the value is read)
FIELD_MAP: Final[tuple[tuple[str, str, str], ...]] = (
    ("Subject.PropertyAddress", "subject.property_address", "street"),
    ("Subject.PropertyAddress", "subject.city", "city"),
    ("Subject.PropertyAddress", "subject.state", "state"),
    ("Subject.PropertyAddress", "subject.zip", "postal"),
    ("Subject.BorrowerName", "subject.borrower", "value"),
    ("Subject.PublicRecordOwner", "subject.owner_of_public_record", "value"),
    ("Subject.LegalDescription", "subject.legal_description", "value"),
    ("Subject.AssessorParcelNumber", "subject.assessors_parcel", "value"),
    ("Subject.TaxYear", "subject.tax_year", "value"),
    ("Subject.RealEstateTaxes", "subject.real_estate_taxes", "value"),
    ("Subject.OccupantType", "subject.occupant", "selection"),
    ("Subject.HoaAmount", "subject.hoa_dues", "value"),
    (
        "Subject.PropertyRightsAppraisedType",
        "subject.property_rights_appraised",
        "selection:Subject.OtherPropertyRightsAppraised",
    ),
    ("Subject.AssignmentType", "subject.assignment_type", "selection:Subject.OtherAssignment"),
    ("Subject.LenderOrClientName", "subject.lender_client", "value"),
    ("Contract.ContractPrice", "listing_and_contract.contract_price", "value"),
    ("Contract.ContractDate", "listing_and_contract.contract_date", "value"),
    ("Neighborhood.LocationType", "neighborhood.location", "selection"),
    ("Neighborhood.BuiltUpType", "neighborhood.built_up", "selection"),
    ("Neighborhood.GrowthType", "neighborhood.growth", "selection"),
    ("Neighborhood.PropertyValuesTrend", "neighborhood.property_values", "selection"),
    ("Neighborhood.DemandAndSupplyTrend", "neighborhood.demand_supply", "selection"),
    ("Neighborhood.MarketingTimeTrend", "neighborhood.marketing_time", "selection"),
    (
        "Site.Utilities.ElectricityType",
        "site.utilities_electric",
        "selection:Site.Utilities.OtherElectricity",
    ),
    ("Site.Utilities.GasType", "site.utilities_gas", "selection:Site.Utilities.OtherGas"),
    ("Site.Utilities.WaterType", "site.utilities_water", "selection:Site.Utilities.OtherWater"),
    (
        "Site.Utilities.SanitarySewerType",
        "site.utilities_sewer",
        "selection:Site.Utilities.OtherSanitarySewer",
    ),
    ("Site.IsFemaSpecialFloodArea", "site.special_flood_hazard", "selection"),
    ("Site.FemaFloodZone", "site.flood_zone", "value"),
    ("Site.FemaMapNumber", "site.fema_map_number", "value"),
    ("Site.FemaMapDate", "site.fema_map_date", "value"),
    ("Improvements.UnitsType", "improvements.units", "selection"),
    ("Improvements.Type", "improvements.type", "selection"),
    ("Improvements.DesignStyle", "improvements.design_style", "value"),
    ("Improvements.YearBuilt", "improvements.year_built", "value"),
    ("Improvements.EffectiveAgeInYears", "improvements.effective_age_years", "value"),
    ("Improvements.FoundationType", "improvements.foundation_type", "selection"),
    ("Improvements.BasementArea", "improvements.basement_area_sqft", "value"),
    ("Improvements.BasementFinish", "improvements.basement_finish_percent", "value"),
    ("SalesComparisonApproach.IndicatedValue", "sales_comparison.indicated_value", "value"),
    (
        "Reconciliation.IndicatedValueBySalesComparisonApproach",
        "reconciliation.indicated_value_sales_comparison",
        "value",
    ),
    (
        "Reconciliation.IndicatedValueByCostApproach",
        "reconciliation.indicated_value_cost_approach",
        "value",
    ),
    (
        "Reconciliation.IndicatedValueByIncomeApproach",
        "reconciliation.indicated_value_income_approach",
        "value",
    ),
    ("Reconciliation.AppraisalType", "reconciliation.appraisal_made", "selection"),
    ("Reconciliation.AppraisedMarketValue", "reconciliation.final_market_value", "value"),
    (
        "Reconciliation.AppraisalEffectiveDate",
        "reconciliation.effective_date_of_appraisal",
        "value",
    ),
    ("Appraiser.AppraiserName", "appraiser.name", "value"),
    ("Appraiser.CompanyName", "appraiser.company", "value"),
    ("Appraiser.SignatureAndReportDate", "appraiser.signature_date", "value"),
    ("Appraiser.EffectiveDate", "appraiser.effective_date", "value"),
    ("Appraiser.AppraisedValueOfSubjectProperty", "appraiser.appraised_value", "value"),
)

_ADDRESS_PART: Final = {"city": "city", "state": "state", "postal": "postalCode"}
_TYPED: Final = (
    "valueString",
    "valueNumber",
    "valueInteger",
    "valueDate",
    "valueCurrency",
    "valuePhoneNumber",
)


class AzureModelClient:
    """A live Document Intelligence client for another prebuilt model (the raw SDK result),
    with production's request, timeout and error mapping (:mod:`basis.extraction.azure_di`)."""

    def __init__(self, endpoint: str, key: str, *, timeout_s: float, model_id: str) -> None:
        self._endpoint = endpoint
        self._key = key
        self._timeout_s = timeout_s
        self.model_id = model_id

    async def analyze_raw(self, pdf_bytes: bytes) -> Any:
        import asyncio

        from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
        from azure.core.credentials import AzureKeyCredential

        async def run() -> Any:
            async with DocumentIntelligenceClient(
                endpoint=self._endpoint, credential=AzureKeyCredential(self._key)
            ) as client:
                poller = await client.begin_analyze_document(
                    self.model_id, AnalyzeDocumentRequest(bytes_source=pdf_bytes)
                )
                return await poller.result()

        try:
            return await asyncio.wait_for(run(), timeout=self._timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise map_azure_error(exc) from exc

    @classmethod
    def from_settings(
        cls, settings: Settings, model_id: str = MORTGAGE_1004_MODEL_ID
    ) -> AzureModelClient | None:
        key = settings.azure_document_intelligence_key
        endpoint = settings.azure_document_intelligence_endpoint
        if not endpoint or key is None or not key.get_secret_value():
            return None
        return cls(
            endpoint,
            key.get_secret_value(),
            timeout_s=settings.azure_di_timeout_seconds,
            model_id=model_id,
        )


# --------------------------------------------------------------------------- mapping


def document_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The first analyzed document's fields (``{}`` when the model found no 1004)."""
    for document in raw.get("documents") or ():
        fields = document.get("fields")
        if isinstance(fields, Mapping):
            return dict(fields)
    return {}


def field_at(fields: Mapping[str, Any], path: str) -> Mapping[str, Any] | None:
    head, *rest = path.split(".")
    node = fields.get(head)
    for name in rest:
        if not isinstance(node, Mapping):
            return None
        node = (node.get("valueObject") or {}).get(name)
    return node if isinstance(node, Mapping) else None


def _text(node: Mapping[str, Any] | None) -> str | None:
    if node is None:
        return None
    content = str(node.get("content") or "").strip()
    if content:
        return content
    for name in _TYPED:
        value = node.get(name)
        if isinstance(value, Mapping):  # currency {amount, currencySymbol}
            value = value.get("amount")
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _street(address: Mapping[str, Any]) -> str | None:
    street = address.get("streetAddress")
    if street:
        return str(street)
    parts = [str(address[k]) for k in ("houseNumber", "road") if address.get(k)]
    return " ".join(parts) or None


def _selection(node: Mapping[str, Any], fields: Mapping[str, Any], other: str | None) -> str | None:
    selected = [
        str(s).strip().strip(",").strip('"').strip() for s in node.get("valueSelectionGroup") or ()
    ]
    selected = [s for s in selected if s]
    if not selected:
        return None
    write_in = _text(field_at(fields, other)) if other else None
    if write_in:
        selected = [f"Other: {write_in}" if s.casefold() == "other" else s for s in selected]
    return ", ".join(selected)


def read_value(node: Mapping[str, Any], fields: Mapping[str, Any], how: str) -> str | None:
    if how.startswith("selection"):
        _, _, other = how.partition(":")
        return _selection(node, fields, other or None)
    if how in ("street", *_ADDRESS_PART):
        address = node.get("valueAddress")
        if not isinstance(address, Mapping):
            return _text(node) if how == "street" else None
        if how == "street":
            return _street(address) or _text(node)
        part = address.get(_ADDRESS_PART[how])
        return str(part).strip() if part else None
    return _text(node)


def _bounding(node: Mapping[str, Any], geometry: Layout) -> tuple[int, Box] | None:
    for bounding in node.get("boundingRegions") or ():
        number = int(bounding.get("pageNumber", 0))
        page = geometry.page(number)
        if page is None:
            continue
        box = box_of(bounding.get("polygon"), page.width, page.height)
        if box is not None:
            return number, box
    return None


def field_region(
    node: Mapping[str, Any], value: str, how: str, geometry: Layout
) -> list[dict[str, Any]]:
    """The field's box; a selection group's narrowed to its first selected label; an
    address part's narrowed to that part's words."""
    found = _bounding(node, geometry)
    if found is None:
        return []
    page, box = found
    if how.startswith("selection") or how in _ADDRESS_PART:
        label = value.split(",")[0].removeprefix("Other: ").strip()
        narrowed = locate(geometry.page(page), label, within=box)
        if narrowed is not None:
            box = narrowed
    return [region(page, box)]


def map_1004(
    raw: Mapping[str, Any],
) -> tuple[dict[str, ExtractedField], dict[str, list[dict[str, Any]]], dict[str, str]]:
    """Catalogue fields, regions and the unparseable values (key -> reason)."""
    fields = document_fields(raw)
    geometry = from_raw(raw)
    out: dict[str, ExtractedField] = {}
    regions: dict[str, list[dict[str, Any]]] = {}
    dropped: dict[str, str] = {}
    for path, key, how in FIELD_MAP:
        node = field_at(fields, path)
        if node is None:
            continue
        value = read_value(node, fields, how)
        if not value:
            continue
        found = _bounding(node, geometry)
        extracted = ExtractedField.from_candidate(
            Candidate(
                key=key,
                raw_text=value,
                page=found[0] if found is not None else None,
                confidence=min(1.0, max(0.0, float(node.get("confidence") or 0.0))),
                source="azure",
            )
        )
        if extracted.is_empty or extracted.parse_error:
            dropped[key] = extracted.parse_error or "empty"
            continue
        out[key] = extracted
        regions[key] = field_region(node, value, how, geometry)
    return out, regions, dropped


class Azure1004:
    name = "azure_1004"

    async def run(self, env: SystemEnv) -> SystemOutput:
        raw = await env.sources.mortgage(env.pdf)
        fields, regions, dropped = map_1004(raw)
        result = finish(
            doc_id=env.doc_id,
            method="azure_1004",
            page_count=len(raw.get("pages") or ()),
            fields=fields,
            review_threshold=env.extraction.review_threshold,
            fallback={"status": "none", "asked": [], "filled": [], "dropped": dropped},
            notes=[] if document_fields(raw) else ["the 1004 model found no document"],
        )
        return SystemOutput(
            result=result, regions=regions, origins={k: "azure_1004" for k in fields}
        )


# --------------------------------------------------------------------------- merge


def merge_1004(
    production: Mapping[str, ExtractedField], mortgage: Mapping[str, ExtractedField]
) -> tuple[dict[str, ExtractedField], dict[str, str], list[ValidationIssue]]:
    """The merged fields, each key's origin, and a ``sources_disagree`` issue per
    disagreement (module doc)."""
    merged: dict[str, ExtractedField] = {}
    origins: dict[str, str] = {}
    issues: list[ValidationIssue] = []
    for key in [*production, *(k for k in mortgage if k not in production)]:
        mine, theirs = production.get(key), mortgage.get(key)
        if theirs is None or theirs.is_empty:
            assert mine is not None
            merged[key], origins[key] = mine, mine.source
            continue
        if mine is None or mine.is_empty:
            merged[key], origins[key] = theirs, "azure_1004"
            continue
        if same_value(mine, theirs):
            merged[key] = mine.model_copy(
                update={
                    "confidence": max(mine.confidence, theirs.confidence),
                    "corroborated_by": (*mine.corroborated_by, "azure_1004"),
                }
            )
            origins[key] = f"{mine.source}+azure_1004"
            continue
        low = min(mine.confidence, theirs.confidence, DISAGREE_CONFIDENCE)
        if key in CHECKBOX_KEYS:
            merged[key], origins[key] = theirs.model_copy(update={"confidence": low}), "azure_1004"
        else:
            merged[key], origins[key] = mine.model_copy(update={"confidence": low}), mine.source
        spec = FIELD_BY_KEY.get(key)
        issues.append(
            ValidationIssue(
                rule="sources_disagree",
                message=(
                    f"{key}: production read {mine.value_text!r}, "
                    f"prebuilt-mortgage.us.1004 read {theirs.value_text!r}"
                ),
                keys=(key,),
                severity="error" if spec is not None and spec.critical else "warning",
            )
        )
    return merged, origins, issues


class ProductionPlus1004:
    name = "production_plus_1004"

    async def run(self, env: SystemEnv) -> SystemOutput:
        base = await Production().run(env)
        raw = await env.sources.mortgage(env.pdf)
        mortgage, mortgage_regions, dropped = map_1004(raw)
        merged, origins, disagreements = merge_1004(base.result.fields, mortgage)
        result = finish(
            doc_id=env.doc_id,
            method=f"{base.result.method}+azure_1004",
            page_count=base.result.page_count,
            fields=merged,
            review_threshold=env.extraction.review_threshold,
            extra_issues=[*own_issues(base.result), *disagreements],
            fallback={**base.result.fallback, "azure_1004_dropped": dropped},
            notes=base.result.notes,
            raw={**base.result.raw, "merge_origins": origins},
        )
        result.comparables = base.result.comparables
        geometry = env.sources.geometry_if_loaded()
        regions: dict[str, list[dict[str, Any]]] = (
            production_regions(base.result, geometry, env.sources.layout_raw())
            if geometry is not None
            else {}
        )
        for key, origin in origins.items():
            if origin == "azure_1004":
                regions[key] = mortgage_regions.get(key, [])
            else:
                regions.setdefault(key, [])
        return SystemOutput(result=result, regions=regions, origins=origins)
