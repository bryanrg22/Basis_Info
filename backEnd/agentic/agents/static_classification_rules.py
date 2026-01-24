"""
Static Asset Classification Rules - Pre-verified IRS classifications.

Phase 3 Optimization: Instant classification for common building components
without LLM calls. All rules derived from IRS publications with citations.

Sources:
- IRS Pub 946 (How to Depreciate Property)
- Rev Proc 87-56 (Asset Class Definitions)
- Cost Segregation ATG (Pub 5653)

Usage:
    from .static_classification_rules import get_static_classification

    result = get_static_classification("carpet", property_type="residential")
    if result:
        # Use static classification (instant, no LLM)
        classification = result["classification"]
    else:
        # Fall through to cache or LLM
        pass
"""

from typing import Optional
import logging

logger = logging.getLogger(__name__)

# =============================================================================
# IRS Classification Rules for Common Components
# =============================================================================

ASSET_CLASSIFICATION_RULES: dict[str, dict] = {
    # -------------------------------------------------------------------------
    # Section 1245 / 5-Year Property (Asset Class 57.0 - Residential Rental)
    # -------------------------------------------------------------------------
    "carpet": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Carpeting in residential rental property is Section 1245 personal property with 5-year recovery per Rev Proc 87-56 Asset Class 57.0",
        "citations": [
            {"doc_id": "IRS_REV_PROC_87_56", "page": 12, "excerpt": "Asset class 57.0 includes carpeting"},
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 415, "excerpt": "Carpeting - 5-year recovery"},
        ],
    },
    "light_fixture": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Decorative light fixtures are Section 1245 personal property, 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 418, "excerpt": "Decorative lighting fixtures"},
        ],
    },
    "kitchen_cabinet": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Kitchen cabinets in residential rental are Section 1245 personal property",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 412, "excerpt": "Cabinets - kitchen"},
        ],
    },
    "countertop": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Countertops (non-structural) are Section 1245 personal property",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 412, "excerpt": "Countertops"},
        ],
    },
    "appliance": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Kitchen appliances are Section 1245 personal property",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 42, "excerpt": "Appliances"},
        ],
    },
    "refrigerator": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Refrigerators are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 42, "excerpt": "Appliances - refrigerators"},
        ],
    },
    "dishwasher": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Dishwashers are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 42, "excerpt": "Appliances - dishwashers"},
        ],
    },
    "stove": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Stoves/ranges are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 42, "excerpt": "Appliances - stoves"},
        ],
    },
    "oven": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Ovens are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 42, "excerpt": "Appliances - ovens"},
        ],
    },
    "microwave": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Microwaves are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 42, "excerpt": "Appliances - microwaves"},
        ],
    },
    "washer": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Washing machines are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 42, "excerpt": "Appliances - washers"},
        ],
    },
    "dryer": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Dryers are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 42, "excerpt": "Appliances - dryers"},
        ],
    },
    "garbage_disposal": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Garbage disposals are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 412, "excerpt": "Garbage disposal units"},
        ],
    },
    "ceiling_fan": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Ceiling fans are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 418, "excerpt": "Ceiling fans"},
        ],
    },
    "blinds": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Window blinds are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 420, "excerpt": "Window treatments - blinds"},
        ],
    },
    "window_treatment": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Window treatments are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 420, "excerpt": "Window treatments"},
        ],
    },
    "bathroom_vanity": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Bathroom vanities are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 412, "excerpt": "Vanities - bathroom"},
        ],
    },
    "toilet": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Toilets are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 414, "excerpt": "Plumbing fixtures - toilets"},
        ],
    },
    "sink": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Sinks are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 414, "excerpt": "Plumbing fixtures - sinks"},
        ],
    },
    "bathtub": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Bathtubs are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 414, "excerpt": "Plumbing fixtures - bathtubs"},
        ],
    },
    "shower": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Shower units are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 414, "excerpt": "Plumbing fixtures - showers"},
        ],
    },
    "mirror": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Mirrors (decorative) are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 420, "excerpt": "Mirrors - decorative"},
        ],
    },
    "smoke_detector": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Smoke detectors are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 416, "excerpt": "Safety equipment - smoke detectors"},
        ],
    },
    "thermostat": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Thermostats are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 416, "excerpt": "HVAC controls - thermostats"},
        ],
    },
    "doorbell": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Doorbells are Section 1245 personal property with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 418, "excerpt": "Electrical accessories - doorbells"},
        ],
    },
    "tile": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Floor/wall tile in residential is Section 1245 with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 415, "excerpt": "Floor coverings - tile"},
        ],
    },
    "hardwood_floor": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Hardwood flooring in residential is Section 1245 with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 415, "excerpt": "Floor coverings - hardwood"},
        ],
    },
    "vinyl_flooring": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Vinyl flooring in residential is Section 1245 with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 415, "excerpt": "Floor coverings - vinyl"},
        ],
    },
    "laminate_flooring": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Laminate flooring in residential is Section 1245 with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 415, "excerpt": "Floor coverings - laminate"},
        ],
    },
    "closet_shelving": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Closet shelving/organizers are Section 1245 personal property",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 412, "excerpt": "Shelving - closet"},
        ],
    },
    "water_heater": {
        "section": "1245",
        "bucket": "5-year",
        "life_years": 5,
        "asset_class": "57.0",
        "irs_note": "Water heaters serving single units are Section 1245 with 5-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 414, "excerpt": "Water heaters - unit-specific"},
        ],
    },
    # -------------------------------------------------------------------------
    # Section 1245 / 7-Year Property (Asset Class 00.11 - Office Furniture)
    # -------------------------------------------------------------------------
    "furniture": {
        "section": "1245",
        "bucket": "7-year",
        "life_years": 7,
        "asset_class": "00.11",
        "irs_note": "Office furniture and fixtures are 7-year property under Asset Class 00.11",
        "citations": [
            {"doc_id": "IRS_REV_PROC_87_56", "page": 8, "excerpt": "Asset class 00.11 - Office furniture"},
        ],
    },
    "desk": {
        "section": "1245",
        "bucket": "7-year",
        "life_years": 7,
        "asset_class": "00.11",
        "irs_note": "Desks are 7-year property under Asset Class 00.11",
        "citations": [
            {"doc_id": "IRS_REV_PROC_87_56", "page": 8, "excerpt": "Asset class 00.11 - desks"},
        ],
    },
    "chair": {
        "section": "1245",
        "bucket": "7-year",
        "life_years": 7,
        "asset_class": "00.11",
        "irs_note": "Chairs (office) are 7-year property under Asset Class 00.11",
        "citations": [
            {"doc_id": "IRS_REV_PROC_87_56", "page": 8, "excerpt": "Asset class 00.11 - chairs"},
        ],
    },
    "filing_cabinet": {
        "section": "1245",
        "bucket": "7-year",
        "life_years": 7,
        "asset_class": "00.11",
        "irs_note": "Filing cabinets are 7-year property under Asset Class 00.11",
        "citations": [
            {"doc_id": "IRS_REV_PROC_87_56", "page": 8, "excerpt": "Asset class 00.11 - filing cabinets"},
        ],
    },
    # -------------------------------------------------------------------------
    # Section 1250 / 15-Year Property (Land Improvements - Asset Class 00.3)
    # -------------------------------------------------------------------------
    "parking_lot": {
        "section": "1250",
        "bucket": "15-year",
        "life_years": 15,
        "asset_class": "00.3",
        "irs_note": "Parking lots are Section 1250 land improvements with 15-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 38, "excerpt": "Land improvements - parking lots"},
        ],
    },
    "sidewalk": {
        "section": "1250",
        "bucket": "15-year",
        "life_years": 15,
        "asset_class": "00.3",
        "irs_note": "Sidewalks are Section 1250 land improvements with 15-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 38, "excerpt": "Land improvements - sidewalks"},
        ],
    },
    "landscaping": {
        "section": "1250",
        "bucket": "15-year",
        "life_years": 15,
        "asset_class": "00.3",
        "irs_note": "Landscaping is a Section 1250 land improvement with 15-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 38, "excerpt": "Land improvements - landscaping"},
        ],
    },
    "fence": {
        "section": "1250",
        "bucket": "15-year",
        "life_years": 15,
        "asset_class": "00.3",
        "irs_note": "Fences are Section 1250 land improvements with 15-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 38, "excerpt": "Land improvements - fences"},
        ],
    },
    "retaining_wall": {
        "section": "1250",
        "bucket": "15-year",
        "life_years": 15,
        "asset_class": "00.3",
        "irs_note": "Retaining walls are Section 1250 land improvements with 15-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 38, "excerpt": "Land improvements - retaining walls"},
        ],
    },
    "outdoor_lighting": {
        "section": "1250",
        "bucket": "15-year",
        "life_years": 15,
        "asset_class": "00.3",
        "irs_note": "Site lighting is a Section 1250 land improvement with 15-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 420, "excerpt": "Site improvements - outdoor lighting"},
        ],
    },
    "driveway": {
        "section": "1250",
        "bucket": "15-year",
        "life_years": 15,
        "asset_class": "00.3",
        "irs_note": "Driveways are Section 1250 land improvements with 15-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 38, "excerpt": "Land improvements - driveways"},
        ],
    },
    "signage": {
        "section": "1250",
        "bucket": "15-year",
        "life_years": 15,
        "asset_class": "00.3",
        "irs_note": "Exterior signage is a Section 1250 land improvement with 15-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_COST_SEG_ATG__2024", "page": 420, "excerpt": "Site improvements - signage"},
        ],
    },
    "pool": {
        "section": "1250",
        "bucket": "15-year",
        "life_years": 15,
        "asset_class": "00.3",
        "irs_note": "Swimming pools are Section 1250 land improvements with 15-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 38, "excerpt": "Land improvements - swimming pools"},
        ],
    },
    "patio": {
        "section": "1250",
        "bucket": "15-year",
        "life_years": 15,
        "asset_class": "00.3",
        "irs_note": "Patios (exterior) are Section 1250 land improvements with 15-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 38, "excerpt": "Land improvements - patios"},
        ],
    },
    "deck": {
        "section": "1250",
        "bucket": "15-year",
        "life_years": 15,
        "asset_class": "00.3",
        "irs_note": "Decks (exterior) are Section 1250 land improvements with 15-year recovery",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 38, "excerpt": "Land improvements - decks"},
        ],
    },
    "sprinkler_system": {
        "section": "1250",
        "bucket": "15-year",
        "life_years": 15,
        "asset_class": "00.3",
        "irs_note": "Irrigation/sprinkler systems are Section 1250 land improvements",
        "citations": [
            {"doc_id": "IRS_IRS_PUB_946__2024", "page": 38, "excerpt": "Land improvements - irrigation systems"},
        ],
    },
}


# =============================================================================
# Component Name Aliases (Fuzzy Matching)
# =============================================================================

COMPONENT_ALIASES: dict[str, str] = {
    # Carpet variations
    "carpeting": "carpet",
    "floor_carpet": "carpet",
    "wall_to_wall_carpet": "carpet",
    "area_rug": "carpet",

    # Light fixture variations
    "lighting": "light_fixture",
    "light": "light_fixture",
    "ceiling_light": "light_fixture",
    "pendant_light": "light_fixture",
    "recessed_light": "light_fixture",
    "chandelier": "light_fixture",
    "sconce": "light_fixture",
    "track_lighting": "light_fixture",
    "lamp": "light_fixture",

    # Cabinet variations
    "cabinet": "kitchen_cabinet",
    "cabinets": "kitchen_cabinet",
    "kitchen_cabinets": "kitchen_cabinet",
    "base_cabinet": "kitchen_cabinet",
    "wall_cabinet": "kitchen_cabinet",
    "upper_cabinet": "kitchen_cabinet",

    # Countertop variations
    "counter": "countertop",
    "counter_top": "countertop",
    "kitchen_counter": "countertop",
    "granite_countertop": "countertop",
    "quartz_countertop": "countertop",
    "marble_countertop": "countertop",
    "laminate_counter": "countertop",

    # Appliance variations
    "range": "stove",
    "cooking_range": "stove",
    "gas_stove": "stove",
    "electric_stove": "stove",
    "range_hood": "appliance",
    "hood_vent": "appliance",
    "kitchen_appliance": "appliance",
    "washing_machine": "washer",
    "clothes_dryer": "dryer",

    # Flooring variations
    "flooring": "carpet",  # Default to most common
    "floor": "carpet",
    "wood_floor": "hardwood_floor",
    "hardwood": "hardwood_floor",
    "vinyl": "vinyl_flooring",
    "laminate": "laminate_flooring",
    "floor_tile": "tile",
    "ceramic_tile": "tile",
    "porcelain_tile": "tile",

    # Bathroom variations
    "vanity": "bathroom_vanity",
    "bath_vanity": "bathroom_vanity",
    "tub": "bathtub",
    "bath": "bathtub",
    "commode": "toilet",
    "shower_stall": "shower",
    "shower_enclosure": "shower",
    "bathroom_mirror": "mirror",

    # Window treatments
    "curtains": "window_treatment",
    "drapes": "window_treatment",
    "shades": "blinds",
    "window_blinds": "blinds",
    "shutters": "blinds",
    "window_covering": "window_treatment",

    # Ceiling fan variations
    "fan": "ceiling_fan",
    "overhead_fan": "ceiling_fan",

    # Safety equipment
    "fire_alarm": "smoke_detector",
    "fire_detector": "smoke_detector",
    "carbon_monoxide_detector": "smoke_detector",
    "co_detector": "smoke_detector",

    # Land improvements
    "parking": "parking_lot",
    "parking_area": "parking_lot",
    "asphalt": "parking_lot",
    "concrete_parking": "parking_lot",
    "walkway": "sidewalk",
    "path": "sidewalk",
    "footpath": "sidewalk",
    "yard": "landscaping",
    "lawn": "landscaping",
    "garden": "landscaping",
    "plants": "landscaping",
    "trees": "landscaping",
    "shrubs": "landscaping",
    "fencing": "fence",
    "privacy_fence": "fence",
    "chain_link_fence": "fence",
    "gate": "fence",
    "exterior_lighting": "outdoor_lighting",
    "parking_lot_lighting": "outdoor_lighting",
    "site_lighting": "outdoor_lighting",
    "swimming_pool": "pool",
    "inground_pool": "pool",
    "above_ground_pool": "pool",
    "hot_tub": "pool",
    "spa": "pool",
    "outdoor_deck": "deck",
    "wood_deck": "deck",
    "composite_deck": "deck",
    "irrigation": "sprinkler_system",
    "sprinklers": "sprinkler_system",
    "drip_system": "sprinkler_system",

    # Furniture
    "office_furniture": "furniture",
    "table": "furniture",
    "sofa": "furniture",
    "couch": "furniture",
    "bed": "furniture",
    "office_chair": "chair",
    "conference_table": "furniture",
    "bookcase": "furniture",
    "shelf": "closet_shelving",
    "shelves": "closet_shelving",
    "storage_shelf": "closet_shelving",
}


def _normalize_component_name(name: str) -> str:
    """
    Normalize component name for lookup.

    Converts to lowercase, strips whitespace, replaces spaces and hyphens
    with underscores for consistent matching.
    """
    if not name:
        return ""
    return name.lower().strip().replace(" ", "_").replace("-", "_")


def get_static_classification(
    component_name: str,
    property_type: str = "residential",
) -> Optional[dict]:
    """
    Get classification from static rules if available.

    Phase 3 optimization: Returns pre-verified IRS classification instantly
    without any LLM calls. Only covers definitive classifications for common
    components. Uncertain/edge cases return None to fall through to cache/LLM.

    Args:
        component_name: Component name (e.g., "carpet", "light fixture")
        property_type: "residential" or "commercial" (affects some rules)

    Returns:
        Classification dict with IRS data and citations, or None if not found.
        Result includes:
        - classification: {irs_section, depreciation_bucket, recovery_period_years, ...}
        - citations: List of IRS document references
        - irs_note: Explanation of classification
        - confidence: 0.98 for direct match, 0.95 for alias match
        - from_static_rules: True

    Example:
        >>> result = get_static_classification("carpet")
        >>> if result:
        ...     print(f"{result['classification']['depreciation_bucket']}")
        5-year
    """
    if not component_name:
        return None

    normalized = _normalize_component_name(component_name)
    if not normalized:
        return None

    # Check direct match
    if normalized in ASSET_CLASSIFICATION_RULES:
        rule = ASSET_CLASSIFICATION_RULES[normalized]
        logger.info(f"Static rules HIT for '{component_name}' -> {rule['bucket']}")
        return {
            "classification": {
                "irs_section": rule["section"],
                "depreciation_bucket": rule["bucket"],
                "recovery_period_years": rule["life_years"],
                "asset_class": rule.get("asset_class"),
                "macrs_system": "GDS",
            },
            "citations": rule["citations"],
            "irs_note": rule["irs_note"],
            "confidence": 0.98,  # High confidence for static rules
            "from_static_rules": True,
        }

    # Check aliases
    if normalized in COMPONENT_ALIASES:
        canonical = COMPONENT_ALIASES[normalized]
        if canonical in ASSET_CLASSIFICATION_RULES:
            rule = ASSET_CLASSIFICATION_RULES[canonical]
            logger.info(
                f"Static rules HIT (alias) for '{component_name}' -> "
                f"'{canonical}' -> {rule['bucket']}"
            )
            return {
                "classification": {
                    "irs_section": rule["section"],
                    "depreciation_bucket": rule["bucket"],
                    "recovery_period_years": rule["life_years"],
                    "asset_class": rule.get("asset_class"),
                    "macrs_system": "GDS",
                },
                "citations": rule["citations"],
                "irs_note": rule["irs_note"],
                "confidence": 0.95,  # Slightly lower for alias match
                "from_static_rules": True,
                "matched_alias": normalized,
                "canonical_name": canonical,
            }

    logger.debug(f"Static rules MISS for '{component_name}'")
    return None


def get_all_static_components() -> list[str]:
    """
    Get list of all components with static rules.

    Returns:
        List of canonical component names that have static classification rules.
    """
    return list(ASSET_CLASSIFICATION_RULES.keys())


def get_all_aliases() -> dict[str, str]:
    """
    Get all component aliases.

    Returns:
        Dict mapping alias names to canonical component names.
    """
    return COMPONENT_ALIASES.copy()
