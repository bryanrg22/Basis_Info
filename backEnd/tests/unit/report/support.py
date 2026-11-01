"""A synthetic approved export (``basis-export/2``) for the report tests: no client data.

Residential rental, price 70,000 + 500 closing costs, land 9,500: depreciable basis 61,000.00.
Assets: a carpet priced from an RSMeans line, a refrigerator from the typical unit costs,
site landscaping measured once with one zero-valued and one unpriced copy (both folded into
it), a concealed structural framing row, a window, and an unpriced item awaiting a measurement.
"""

from __future__ import annotations

import copy
from typing import Any

RSMEANS = (
    "rsmeans_2020[carpet] RSMeans Residential 2020 09 68 10.10 0100, p. 300 per SF; Total Incl O&P"
)
TYPICAL = "TYPICAL_UNIT_COSTS[refrigerator][standard] per EA; installed"


def _asset(
    aid: str,
    label: str,
    bucket: str,
    *,
    room: str | None,
    floor: str | None = "1",
    total: str = "0.00",
    source: str | None = None,
    host: str | None = None,
    component: bool = True,
    material: str | None = None,
    section: str = "1245",
    review: str | None = None,
    quantity: str | None = "1",
    unit: str | None = "EA",
) -> dict[str, Any]:
    return {
        "id": aid,
        "label": label,
        "material": material,
        "attachment": None,
        "room_name": room,
        "room_floor": floor,
        "room_type": None,
        "room_id": None,
        "bucket": bucket,
        "category": f"{label} (free text)",
        "component_id": f"c-{aid}" if component else None,
        "irs_section": section,
        "recovery_period": "5",
        "method": "rule",
        "confidence": 0.98,
        "convention": None,
        "needs_review": review is not None,
        "review_reason": review,
        "quantity": quantity,
        "unit": unit,
        "unit_cost": None,
        "total": total,
        "cost_source": source,
        "cost_trail": None,
        "takeoff_inputs": {"measured_with_asset": host} if host else {},
        "citations": [{"reference": "IRS_IRS_COST_SEG_ATG__2024, p.298", "doc_id": "ATG"}],
    }


BASE: dict[str, Any] = {
    "format": "basis-export/2",
    "study_id": "00000000-0000-4000-8000-0000000000aa",
    "revision": 2,
    "property_name": "12 Example Lane",
    "photos": [{"document_id": "p1"}, {"document_id": "p2"}, {"document_id": "p3"}],
    "extraction": [
        {
            "fields": [
                {"field": "property_address", "value_text": "12 Example Lane"},
                {"field": "year_built", "value_num": "1990"},
                {"field": "gla_sqft", "value_num": "1800"},
            ]
        }
    ],
    "assets": [
        _asset(
            "a1",
            "carpet",
            "y5",
            room="Bedroom",
            floor="2",
            total="1200.00",
            source=RSMEANS,
            quantity="120",
            unit="SF",
        ),
        _asset("a2", "refrigerator", "y5", room="Kitchen", total="900.00", source=TYPICAL),
        _asset(
            "a3",
            "landscaping",
            "y15",
            room="Front",
            floor=None,
            total="3500.00",
            section="1250",
            quantity="5000",
            unit="SF",
        ),
        _asset("a4", "landscaping", "y15", room="Rear", floor=None, host="a3", section="1250"),
        _asset("a5", "landscaping", "y15", room="Side", floor=None, host="a3", section="1250"),
        _asset(
            "a6",
            "structural_framing",
            "y27_5",
            room=None,
            floor=None,
            total="52000.00",
            component=False,
            section="1250",
            review="rule_contested:structural_framing",
        ),
        _asset(
            "a7",
            "window",
            "y27_5",
            room="Living",
            total="6500.00",
            material="vinyl",
            section="1250",
        ),
        _asset(
            "a8",
            "other",
            "y5",
            room="Garage",
            review="takeoff:needs_measurement",
            quantity=None,
            unit=None,
        ),
    ],
    "summary": {
        "takeoff": {
            "property_class": "residential_rental",
            "location_factor": "1.02",
            "location_source": "state NY",
            "area_sources": {"gla_share": 3},
        },
        "basis": {
            "not_produced": [],
            "worksheet": {
                "purchase_price": "70000.00",
                "acquisition_costs": [
                    {
                        "category": "title",
                        "amount": "500.00",
                        "description": "title and recording",
                        "authority": "Reg. 1.263(a)-2(f)(1)",
                    }
                ],
                "capitalized_costs": "500.00",
                "land": "9500.00",
                "land_method": "appraisal_site_value",
                "land_source": "appraisal site value",
                "building_basis": "60500.00",
                "total_basis": "70500.00",
                "property_use": "residential_rental",
                "recovery_period": "27.5",
                "acquisition_date": "2024-06-18",
                "placed_in_service_date": "2025-03-01",
                "checks": [
                    {"code": "land_share_of_price", "severity": "ok", "message": "fine"},
                    {
                        "code": "land_value_date",
                        "severity": "warning",
                        "message": "the site value is 14 months older than the contract",
                    },
                ],
            },
            "allocation": {
                "total": "61000.00",
                "assets": [
                    {
                        "asset_id": "a1",
                        "bucket": "y5",
                        "rcn": "1200.00",
                        "allocated": "1000.00",
                        "concealed": False,
                    },
                    {
                        "asset_id": "a2",
                        "bucket": "y5",
                        "rcn": "900.00",
                        "allocated": "800.00",
                        "concealed": False,
                    },
                    {
                        "asset_id": "a3",
                        "bucket": "y15",
                        "rcn": "3500.00",
                        "allocated": "3000.00",
                        "concealed": False,
                    },
                    {
                        "asset_id": "a4",
                        "bucket": "y15",
                        "rcn": "0.00",
                        "allocated": "0.00",
                        "concealed": False,
                    },
                    {
                        "asset_id": "a6",
                        "bucket": "y27_5",
                        "rcn": "52000.00",
                        "allocated": "50000.00",
                        "concealed": True,
                    },
                    {
                        "asset_id": "a7",
                        "bucket": "y27_5",
                        "rcn": "6500.00",
                        "allocated": "6200.00",
                        "concealed": False,
                    },
                ],
                "checks": [],
            },
            "reconciliation": {
                "status": "reconciled",
                "gap_share": "0.0150",
                "tolerance": "0.10",
                "checks": [],
                "by_component": [
                    {
                        "component": "framing",
                        "top_down": "52000.00",
                        "itemized": "0.00",
                        "concealed": "52000.00",
                    }
                ],
            },
            "structure": {
                "building_rcn": {
                    "source": "square_foot_model",
                    "model": {"name": "Average 1 Story"},
                }
            },
            "cost_index": {"cost_year": 2025, "factor": "1.2600", "applied": "HCI (test)"},
            "depreciation": {
                "method": "appraisal_physical_depreciation",
                "rate": "0.10",
                "source": "appraisal",
                "checks": [],
            },
        },
    },
}


def export(**changes: Any) -> dict[str, Any]:
    """A deep copy of the synthetic export with ``changes`` applied to its top level."""
    document = copy.deepcopy(BASE)
    document.update(changes)
    return document
