"""``classification_cache`` keys end in the rule fingerprint, not the rules version.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-31

CLASSIFICATION_CONVENTIONS.md section 10. A convention's key ended in ``RULES_VERSION`` and
the reader served only the running version, so a release that only added rows hid every
firm ruling (live run 4: 157 rulings, 27 of 35 rows against 34-35). The key now ends in
:func:`basis.domain.rules.rule_fingerprint`, the digest of the row the label resolves to.

Data only, no schema change. Each stored row is re-keyed to the fingerprint of the rule it
was made under: the released table of its version (``FROZEN``, generated from git by
``evals.freeze_rule_fingerprints``; the live table is never imported, it moves on after this
migration). Serving then compares with the running table's fingerprint, so a row made under
a rule that has changed since stays stored and is not served.

Rows that land on the same key (one context, several releases) are settled as one: the
answer of the latest approval holds the key; every row with that answer merges into it
(approvals and hits add up, the earliest of them stays the seed: result, study, reviewer,
time, rule agreement, ``rules_version``); a row with another answer keeps its old key,
stored and never served. A row whose key is malformed or does not end in a frozen version
(an unknown version, or a fingerprint already) is left as it is.

Downgrade puts the version back from ``rules_version``; merged rows stay merged. Where two
rows would share a key the latest approval keeps it and the other is deleted.

Plain SQL, so ``alembic upgrade --sql`` renders it and online and offline run the same
statements: the frozen tables are loaded into temporary tables, a key's fingerprint is a
lookup there (the stored key parts are normalized already: ``cache_key`` wrote them), and the
settling is done with window functions. Two answers are the same as
``basis.classify.cache.answer_of`` decides: bucket, recovery period (the bucket's standard one
when absent, compared as a number written in plain notation, as the cache writes it; anything
else is null) and section.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NONE: Final = "r1-none"
FINGERPRINT_RE: Final = "^r[0-9]+-([0-9a-f]{16}|none)$"

# BEGIN FROZEN (evals.freeze_rule_fingerprints)
FROZEN: Final[dict[str, dict[str, Any]]] = {
    "2026-10-29.1": {
        "aliases": {
            "above_ground_pool": "pool",
            "area_rug": "carpet",
            "asphalt": "parking_lot",
            "base_cabinet": "kitchen_cabinet",
            "bath": "bathtub",
            "bath_accessory": "bathroom_accessory",
            "bath_vanity": "bathroom_vanity",
            "bathroom_mirror": "mirror",
            "bed": "furniture",
            "bookcase": "furniture",
            "cabinet": "kitchen_cabinet",
            "cabinetry": "kitchen_cabinet",
            "carbon_monoxide_detector": "smoke_detector",
            "carpeting": "carpet",
            "ceiling_light": "light_fixture",
            "ceramic_tile": "tile",
            "chain_link_fence": "fence",
            "chandelier": "light_fixture",
            "clothes_dryer": "dryer",
            "co_detector": "smoke_detector",
            "commode": "toilet",
            "composite_deck": "deck",
            "concrete_parking": "parking_lot",
            "condenser_pad": "concrete_pad",
            "conference_table": "furniture",
            "cooking_range": "stove",
            "couch": "furniture",
            "counter": "countertop",
            "counter_top": "countertop",
            "curtains": "window_treatment",
            "decorative_lighting": "accent_lighting",
            "door_guard": "window_guard",
            "drapes": "window_treatment",
            "drip_system": "sprinkler_system",
            "electric_stove": "stove",
            "equipment_pad": "concrete_pad",
            "exterior_lighting": "outdoor_lighting",
            "fan": "ceiling_fan",
            "fencing": "fence",
            "fire_alarm": "smoke_detector",
            "fire_detector": "smoke_detector",
            "floor_carpet": "carpet",
            "floor_tile": "tile",
            "footpath": "sidewalk",
            "garage_floor": "concrete_floor",
            "garden": "landscaping",
            "garden_shed": "shed",
            "gas_stove": "stove",
            "gate": "fence",
            "grab_bar": "bathroom_accessory",
            "granite_countertop": "countertop",
            "hardwood": "hardwood_floor",
            "hood_vent": "appliance",
            "hot_tub": "pool",
            "inground_pool": "pool",
            "irrigation": "sprinkler_system",
            "kitchen_appliance": "appliance",
            "kitchen_cabinets": "kitchen_cabinet",
            "kitchen_counter": "countertop",
            "knee_wall": "half_wall",
            "laminate": "laminate_flooring",
            "laminate_counter": "countertop",
            "lamp": "furniture",
            "landscape_lighting": "accent_lighting",
            "lawn": "landscaping",
            "light": "light_fixture",
            "lighting": "light_fixture",
            "marble_countertop": "countertop",
            "neon_lighting": "accent_lighting",
            "office_chair": "chair",
            "office_furniture": "furniture",
            "outdoor_deck": "deck",
            "overhead_fan": "ceiling_fan",
            "parking": "parking_lot",
            "parking_area": "parking_lot",
            "parking_lot_lighting": "outdoor_lighting",
            "path": "sidewalk",
            "pendant_light": "light_fixture",
            "plants": "landscaping",
            "pony_wall": "half_wall",
            "porcelain_tile": "tile",
            "privacy_fence": "fence",
            "quartz_countertop": "countertop",
            "range": "stove",
            "range_hood": "appliance",
            "recessed_light": "light_fixture",
            "restroom_accessory": "bathroom_accessory",
            "sconce": "light_fixture",
            "sealed_concrete_floor": "concrete_floor",
            "security_bars": "window_guard",
            "security_grille": "window_guard",
            "shades": "blinds",
            "shelf": "closet_shelving",
            "shelves": "closet_shelving",
            "shower_curtain_rod": "bathroom_accessory",
            "shower_enclosure": "shower",
            "shower_stall": "shower",
            "shrubs": "landscaping",
            "shutters": "blinds",
            "site_lighting": "outdoor_lighting",
            "sofa": "furniture",
            "spa": "pool",
            "sprinklers": "sprinkler_system",
            "storage_shed": "shed",
            "storage_shelf": "closet_shelving",
            "swimming_pool": "pool",
            "table": "furniture",
            "tile_flooring": "tile",
            "toilet_paper_holder": "bathroom_accessory",
            "towel_bar": "bathroom_accessory",
            "towel_ring": "bathroom_accessory",
            "track_lighting": "accent_lighting",
            "trees": "landscaping",
            "tub": "bathtub",
            "upper_cabinet": "kitchen_cabinet",
            "vanity": "bathroom_vanity",
            "vinyl": "vinyl_flooring",
            "walkway": "sidewalk",
            "wall_cabinet": "kitchen_cabinet",
            "wall_to_wall_carpet": "carpet",
            "washing_machine": "washer",
            "window_bars": "window_guard",
            "window_blinds": "blinds",
            "window_covering": "window_treatment",
            "wood_deck": "deck",
            "wood_floor": "hardwood_floor",
            "yard": "landscaping",
        },
        "generic_floors": ["floor", "floor_covering", "flooring", "floors"],
        "material_rules": {
            "carpet": "carpet",
            "carpeting": "carpet",
            "ceramic": "tile",
            "ceramic_tile": "tile",
            "concrete": "concrete_floor",
            "hardwood": "hardwood_floor",
            "laminate": "laminate_flooring",
            "luxury_vinyl": "vinyl_flooring",
            "lvp": "vinyl_flooring",
            "porcelain": "tile",
            "sealed_concrete": "concrete_floor",
            "tile": "tile",
            "vinyl": "vinyl_flooring",
            "wood": "hardwood_floor",
        },
        "row_digests": {
            "accent_lighting": "r1-d2b24e1a7c6b5a9b",
            "appliance": "r1-b7f74d63652e8a3f",
            "bathroom_accessory": "r1-bdce26ea5f4c6db1",
            "bathroom_vanity": "r1-427668926474714d",
            "bathtub": "r1-ac352ceb3b2ccc9e",
            "blinds": "r1-c3cc9953e6fe6cda",
            "carpet": "r1-44340d380d7d3c78",
            "ceiling_fan": "r1-957057f4f0367bfb",
            "chair": "r1-3cd8f1265eb7d68f",
            "closet_shelving": "r1-c5629aeaeba937ae",
            "concrete_floor": "r1-fc7c2d7f87a2ab5a",
            "concrete_pad": "r1-41847ac88bd056e5",
            "countertop": "r1-bcb3b9181bf54c3a",
            "deck": "r1-d495aed568e3f45a",
            "desk": "r1-ba97b25c98e9165d",
            "dishwasher": "r1-a2e07306a4742081",
            "doorbell": "r1-c7a9732b3f37f3c2",
            "driveway": "r1-d45e4526902d6f5c",
            "dryer": "r1-6bccce6d80ef4b98",
            "fence": "r1-b61f3dbea22f415f",
            "filing_cabinet": "r1-e1c4af268c49aa7a",
            "furniture": "r1-743d49dd5028deab",
            "garbage_disposal": "r1-6c68ae2565d03886",
            "half_wall": "r1-265ae09156dde676",
            "hardwood_floor": "r1-a07d2c1e1697f691",
            "kitchen_cabinet": "r1-8899d56bd5f71845",
            "laminate_flooring": "r1-36f0d8008713b834",
            "landscaping": "r1-5f02acdbc55067a5",
            "light_fixture": "r1-3ac290216fc19d43",
            "microwave": "r1-ba0fccc1fef66721",
            "mirror": "r1-82403923e801668d",
            "outdoor_lighting": "r1-12f21c42eabc5abf",
            "oven": "r1-3b9dcd161d7d9c3b",
            "parking_lot": "r1-fc39a75f3a80bea0",
            "patio": "r1-e7a966ed3e09d1fa",
            "pool": "r1-66db0e48eea5925b",
            "refrigerator": "r1-f41de490a8e60e65",
            "retaining_wall": "r1-43d4a77d0d589297",
            "shed": "r1-2e61c9a884891734",
            "shower": "r1-3f38787b180ec900",
            "sidewalk": "r1-c955be33f0826bb1",
            "signage": "r1-9dc9e38d56fdd057",
            "sink": "r1-d82b36953753cda3",
            "smoke_detector": "r1-a9056204adc1bcc1",
            "sprinkler_system": "r1-736b20602d4d4853",
            "stove": "r1-1b37a6f138d54f08",
            "thermostat": "r1-1404cea3089fb992",
            "tile": "r1-62049a6781523cf6",
            "toilet": "r1-a110cbb07f514cf8",
            "vinyl_flooring": "r1-d8cb9da569d61f23",
            "washer": "r1-8d67187a3f602e79",
            "water_heater": "r1-63124af625c830ef",
            "window_guard": "r1-d802345482f7eccd",
            "window_treatment": "r1-7983a363bccebeef",
        },
    },
    "2026-10-30.1": {
        "aliases": {
            "above_ground_pool": "pool",
            "area_rug": "carpet",
            "asphalt": "parking_lot",
            "base_cabinet": "kitchen_cabinet",
            "bath": "bathtub",
            "bath_accessory": "bathroom_accessory",
            "bath_vanity": "bathroom_vanity",
            "bathroom_mirror": "mirror",
            "bed": "furniture",
            "bookcase": "furniture",
            "cabinet": "kitchen_cabinet",
            "cabinetry": "kitchen_cabinet",
            "carbon_monoxide_detector": "smoke_detector",
            "carpeting": "carpet",
            "ceiling_light": "light_fixture",
            "ceramic_tile": "tile",
            "chain_link_fence": "fence",
            "chandelier": "light_fixture",
            "clothes_dryer": "dryer",
            "co_detector": "smoke_detector",
            "commode": "toilet",
            "composite_deck": "deck",
            "concrete_parking": "parking_lot",
            "condenser_pad": "concrete_pad",
            "conference_table": "furniture",
            "cooking_range": "stove",
            "couch": "furniture",
            "counter": "countertop",
            "counter_top": "countertop",
            "curtains": "window_treatment",
            "decorative_lighting": "accent_lighting",
            "door_guard": "window_guard",
            "drapes": "window_treatment",
            "drip_system": "sprinkler_system",
            "electric_stove": "stove",
            "equipment_pad": "concrete_pad",
            "exterior_lighting": "outdoor_lighting",
            "fan": "ceiling_fan",
            "fencing": "fence",
            "fire_alarm": "smoke_detector",
            "fire_detector": "smoke_detector",
            "floor_carpet": "carpet",
            "floor_tile": "tile",
            "footpath": "sidewalk",
            "garage_floor": "concrete_floor",
            "garden": "landscaping",
            "garden_shed": "shed",
            "gas_stove": "stove",
            "gate": "fence",
            "grab_bar": "bathroom_accessory",
            "granite_countertop": "countertop",
            "hardwood": "hardwood_floor",
            "hood_vent": "appliance",
            "hot_tub": "pool",
            "inground_pool": "pool",
            "irrigation": "sprinkler_system",
            "kitchen_appliance": "appliance",
            "kitchen_cabinets": "kitchen_cabinet",
            "kitchen_counter": "countertop",
            "knee_wall": "half_wall",
            "laminate": "laminate_flooring",
            "laminate_counter": "countertop",
            "lamp": "furniture",
            "landscape_lighting": "accent_lighting",
            "lawn": "landscaping",
            "light": "light_fixture",
            "lighting": "light_fixture",
            "marble_countertop": "countertop",
            "neon_lighting": "accent_lighting",
            "office_chair": "chair",
            "office_furniture": "furniture",
            "outdoor_deck": "deck",
            "overhead_fan": "ceiling_fan",
            "parking": "parking_lot",
            "parking_area": "parking_lot",
            "parking_lot_lighting": "outdoor_lighting",
            "path": "sidewalk",
            "pendant_light": "light_fixture",
            "plants": "landscaping",
            "pony_wall": "half_wall",
            "porcelain_tile": "tile",
            "privacy_fence": "fence",
            "quartz_countertop": "countertop",
            "range": "stove",
            "range_hood": "appliance",
            "recessed_light": "light_fixture",
            "restroom_accessory": "bathroom_accessory",
            "sconce": "light_fixture",
            "sealed_concrete_floor": "concrete_floor",
            "security_bars": "window_guard",
            "security_grille": "window_guard",
            "shades": "blinds",
            "shelf": "closet_shelving",
            "shelves": "closet_shelving",
            "shower_curtain_rod": "bathroom_accessory",
            "shower_enclosure": "shower",
            "shower_stall": "shower",
            "shrubs": "landscaping",
            "shutters": "blinds",
            "site_lighting": "outdoor_lighting",
            "sofa": "furniture",
            "spa": "pool",
            "sprinklers": "sprinkler_system",
            "storage_shed": "shed",
            "storage_shelf": "closet_shelving",
            "swimming_pool": "pool",
            "table": "furniture",
            "tile_flooring": "tile",
            "toilet_paper_holder": "bathroom_accessory",
            "towel_bar": "bathroom_accessory",
            "towel_ring": "bathroom_accessory",
            "track_lighting": "accent_lighting",
            "trees": "landscaping",
            "tub": "bathtub",
            "upper_cabinet": "kitchen_cabinet",
            "vanity": "bathroom_vanity",
            "vinyl": "vinyl_flooring",
            "walkway": "sidewalk",
            "wall_cabinet": "kitchen_cabinet",
            "wall_to_wall_carpet": "carpet",
            "washing_machine": "washer",
            "window_bars": "window_guard",
            "window_blinds": "blinds",
            "window_covering": "window_treatment",
            "wood_deck": "deck",
            "wood_floor": "hardwood_floor",
            "yard": "landscaping",
        },
        "generic_floors": ["floor", "floor_covering", "flooring", "floors"],
        "material_rules": {
            "carpet": "carpet",
            "carpeting": "carpet",
            "ceramic": "tile",
            "ceramic_tile": "tile",
            "concrete": "concrete_floor",
            "hardwood": "hardwood_floor",
            "laminate": "laminate_flooring",
            "luxury_vinyl": "vinyl_flooring",
            "lvp": "vinyl_flooring",
            "porcelain": "tile",
            "sealed_concrete": "concrete_floor",
            "tile": "tile",
            "vinyl": "vinyl_flooring",
            "wood": "hardwood_floor",
        },
        "row_digests": {
            "accent_lighting": "r1-d2b24e1a7c6b5a9b",
            "access_control": "r1-6be4848d08d5fb9e",
            "appliance": "r1-b7f74d63652e8a3f",
            "bathroom_accessory": "r1-bdce26ea5f4c6db1",
            "bathroom_vanity": "r1-427668926474714d",
            "bathtub": "r1-ac352ceb3b2ccc9e",
            "blinds": "r1-c3cc9953e6fe6cda",
            "carpet": "r1-44340d380d7d3c78",
            "ceiling_fan": "r1-957057f4f0367bfb",
            "chair": "r1-3cd8f1265eb7d68f",
            "closet_shelving": "r1-c5629aeaeba937ae",
            "concrete_floor": "r1-fc7c2d7f87a2ab5a",
            "concrete_pad": "r1-41847ac88bd056e5",
            "countertop": "r1-bcb3b9181bf54c3a",
            "data_cabling": "r1-8c7c5e21de523385",
            "deck": "r1-d495aed568e3f45a",
            "desk": "r1-ba97b25c98e9165d",
            "dishwasher": "r1-a2e07306a4742081",
            "doorbell": "r1-c7a9732b3f37f3c2",
            "driveway": "r1-d45e4526902d6f5c",
            "dryer": "r1-6bccce6d80ef4b98",
            "electrical_wiring": "r1-e2d26d1fa695f2b0",
            "exterior_wall_assembly": "r1-306ddf4e6f346d7e",
            "fence": "r1-b61f3dbea22f415f",
            "filing_cabinet": "r1-e1c4af268c49aa7a",
            "fire_alarm_system": "r1-4ae58d7bc2bb76a6",
            "foundation": "r1-70b7e8f3a71eba3e",
            "furniture": "r1-743d49dd5028deab",
            "garbage_disposal": "r1-6c68ae2565d03886",
            "half_wall": "r1-265ae09156dde676",
            "hardwood_floor": "r1-a07d2c1e1697f691",
            "hvac_distribution": "r1-33025d8e3cf7b5ff",
            "interior_partition": "r1-efcfe9d60cc21b6b",
            "kitchen_cabinet": "r1-8899d56bd5f71845",
            "laminate_flooring": "r1-36f0d8008713b834",
            "landscaping": "r1-5f02acdbc55067a5",
            "light_fixture": "r1-3ac290216fc19d43",
            "microwave": "r1-ba0fccc1fef66721",
            "mirror": "r1-82403923e801668d",
            "outdoor_lighting": "r1-12f21c42eabc5abf",
            "oven": "r1-3b9dcd161d7d9c3b",
            "painting": "r1-3bdfb6c670f0a598",
            "parking_lot": "r1-fc39a75f3a80bea0",
            "patio": "r1-e7a966ed3e09d1fa",
            "plumbing_rough_in": "r1-fe085b1c4677c81b",
            "pool": "r1-66db0e48eea5925b",
            "refrigerator": "r1-f41de490a8e60e65",
            "retaining_wall": "r1-43d4a77d0d589297",
            "roofing_assembly": "r1-780706b231b0d0f4",
            "shed": "r1-2e61c9a884891734",
            "shower": "r1-3f38787b180ec900",
            "sidewalk": "r1-c955be33f0826bb1",
            "signage": "r1-9dc9e38d56fdd057",
            "sink": "r1-d82b36953753cda3",
            "site_utilities": "r1-cc930fb195a1dab6",
            "smoke_detector": "r1-a9056204adc1bcc1",
            "sprinkler_system": "r1-736b20602d4d4853",
            "stove": "r1-1b37a6f138d54f08",
            "structural_framing": "r1-1f4c10d4c707f2e9",
            "thermostat": "r1-1404cea3089fb992",
            "tile": "r1-62049a6781523cf6",
            "toilet": "r1-a110cbb07f514cf8",
            "vinyl_flooring": "r1-d8cb9da569d61f23",
            "washer": "r1-8d67187a3f602e79",
            "water_heater": "r1-63124af625c830ef",
            "window_guard": "r1-d802345482f7eccd",
            "window_treatment": "r1-7983a363bccebeef",
        },
    },
    "2026-10-31.1": {
        "aliases": {
            "above_ground_pool": "pool",
            "area_rug": "carpet",
            "asphalt": "parking_lot",
            "base_cabinet": "kitchen_cabinet",
            "bath": "bathtub",
            "bath_accessory": "bathroom_accessory",
            "bath_vanity": "bathroom_vanity",
            "bathroom_mirror": "mirror",
            "bed": "furniture",
            "bookcase": "furniture",
            "cabinet": "kitchen_cabinet",
            "cabinetry": "kitchen_cabinet",
            "carbon_monoxide_detector": "smoke_detector",
            "carpeting": "carpet",
            "ceiling_light": "light_fixture",
            "ceramic_tile": "tile",
            "chain_link_fence": "fence",
            "chandelier": "light_fixture",
            "clothes_dryer": "dryer",
            "co_detector": "smoke_detector",
            "commode": "toilet",
            "composite_deck": "deck",
            "concrete_parking": "parking_lot",
            "condenser_pad": "concrete_pad",
            "conference_table": "furniture",
            "cooking_range": "stove",
            "couch": "furniture",
            "counter": "countertop",
            "counter_top": "countertop",
            "curtains": "window_treatment",
            "decorative_lighting": "accent_lighting",
            "door_guard": "window_guard",
            "drapes": "window_treatment",
            "drip_system": "sprinkler_system",
            "electric_stove": "stove",
            "equipment_pad": "concrete_pad",
            "exterior_lighting": "outdoor_lighting",
            "fan": "ceiling_fan",
            "fencing": "fence",
            "fire_alarm": "smoke_detector",
            "fire_detector": "smoke_detector",
            "floor_carpet": "carpet",
            "floor_tile": "tile",
            "footpath": "sidewalk",
            "garage_floor": "concrete_floor",
            "garden": "landscaping",
            "garden_shed": "shed",
            "gas_stove": "stove",
            "gate": "fence",
            "grab_bar": "bathroom_accessory",
            "granite_countertop": "countertop",
            "hardwood": "hardwood_floor",
            "hood_vent": "appliance",
            "hot_tub": "pool",
            "inground_pool": "pool",
            "irrigation": "sprinkler_system",
            "kitchen_appliance": "appliance",
            "kitchen_cabinets": "kitchen_cabinet",
            "kitchen_counter": "countertop",
            "knee_wall": "half_wall",
            "laminate": "laminate_flooring",
            "laminate_counter": "countertop",
            "lamp": "furniture",
            "landscape_lighting": "accent_lighting",
            "lawn": "landscaping",
            "light": "light_fixture",
            "lighting": "light_fixture",
            "marble_countertop": "countertop",
            "neon_lighting": "accent_lighting",
            "office_chair": "chair",
            "office_furniture": "furniture",
            "outdoor_deck": "deck",
            "overhead_fan": "ceiling_fan",
            "parking": "parking_lot",
            "parking_area": "parking_lot",
            "parking_lot_lighting": "outdoor_lighting",
            "path": "sidewalk",
            "pendant_light": "light_fixture",
            "plants": "landscaping",
            "pony_wall": "half_wall",
            "porcelain_tile": "tile",
            "privacy_fence": "fence",
            "quartz_countertop": "countertop",
            "range": "stove",
            "range_hood": "appliance",
            "recessed_light": "light_fixture",
            "restroom_accessory": "bathroom_accessory",
            "sconce": "light_fixture",
            "sealed_concrete_floor": "concrete_floor",
            "security_bars": "window_guard",
            "security_grille": "window_guard",
            "shades": "blinds",
            "shelf": "closet_shelving",
            "shelves": "closet_shelving",
            "shower_curtain_rod": "bathroom_accessory",
            "shower_enclosure": "shower",
            "shower_stall": "shower",
            "shrubs": "landscaping",
            "shutters": "blinds",
            "site_lighting": "outdoor_lighting",
            "sofa": "furniture",
            "spa": "pool",
            "sprinklers": "sprinkler_system",
            "storage_shed": "shed",
            "storage_shelf": "closet_shelving",
            "swimming_pool": "pool",
            "table": "furniture",
            "tile_flooring": "tile",
            "toilet_paper_holder": "bathroom_accessory",
            "towel_bar": "bathroom_accessory",
            "towel_ring": "bathroom_accessory",
            "track_lighting": "accent_lighting",
            "trees": "landscaping",
            "tub": "bathtub",
            "upper_cabinet": "kitchen_cabinet",
            "vanity": "bathroom_vanity",
            "vinyl": "vinyl_flooring",
            "walkway": "sidewalk",
            "wall_cabinet": "kitchen_cabinet",
            "wall_to_wall_carpet": "carpet",
            "washing_machine": "washer",
            "window_bars": "window_guard",
            "window_blinds": "blinds",
            "window_covering": "window_treatment",
            "wood_deck": "deck",
            "wood_floor": "hardwood_floor",
            "yard": "landscaping",
        },
        "generic_floors": ["floor", "floor_covering", "flooring", "floors"],
        "material_rules": {
            "carpet": "carpet",
            "carpeting": "carpet",
            "ceramic": "tile",
            "ceramic_tile": "tile",
            "concrete": "concrete_floor",
            "hardwood": "hardwood_floor",
            "laminate": "laminate_flooring",
            "luxury_vinyl": "vinyl_flooring",
            "lvp": "vinyl_flooring",
            "porcelain": "tile",
            "sealed_concrete": "concrete_floor",
            "tile": "tile",
            "vinyl": "vinyl_flooring",
            "wood": "hardwood_floor",
        },
        "row_digests": {
            "accent_lighting": "r1-d2b24e1a7c6b5a9b",
            "access_control": "r1-6be4848d08d5fb9e",
            "appliance": "r1-b7f74d63652e8a3f",
            "bathroom_accessory": "r1-bdce26ea5f4c6db1",
            "bathroom_vanity": "r1-427668926474714d",
            "bathtub": "r1-ac352ceb3b2ccc9e",
            "blinds": "r1-c3cc9953e6fe6cda",
            "carpet": "r1-44340d380d7d3c78",
            "ceiling_fan": "r1-957057f4f0367bfb",
            "chair": "r1-3cd8f1265eb7d68f",
            "closet_shelving": "r1-c5629aeaeba937ae",
            "concrete_floor": "r1-fc7c2d7f87a2ab5a",
            "concrete_pad": "r1-41847ac88bd056e5",
            "countertop": "r1-bcb3b9181bf54c3a",
            "data_cabling": "r1-8c7c5e21de523385",
            "deck": "r1-d495aed568e3f45a",
            "desk": "r1-ba97b25c98e9165d",
            "dishwasher": "r1-a2e07306a4742081",
            "doorbell": "r1-c7a9732b3f37f3c2",
            "driveway": "r1-d45e4526902d6f5c",
            "dryer": "r1-6bccce6d80ef4b98",
            "electrical_wiring": "r1-e2d26d1fa695f2b0",
            "exterior_wall_assembly": "r1-306ddf4e6f346d7e",
            "fence": "r1-b61f3dbea22f415f",
            "filing_cabinet": "r1-e1c4af268c49aa7a",
            "fire_alarm_system": "r1-4ae58d7bc2bb76a6",
            "foundation": "r1-70b7e8f3a71eba3e",
            "furniture": "r1-743d49dd5028deab",
            "garbage_disposal": "r1-6c68ae2565d03886",
            "half_wall": "r1-265ae09156dde676",
            "hardwood_floor": "r1-a07d2c1e1697f691",
            "hvac_distribution": "r1-33025d8e3cf7b5ff",
            "interior_partition": "r1-efcfe9d60cc21b6b",
            "kitchen_cabinet": "r1-8899d56bd5f71845",
            "laminate_flooring": "r1-36f0d8008713b834",
            "landscaping": "r1-5f02acdbc55067a5",
            "light_fixture": "r1-3ac290216fc19d43",
            "microwave": "r1-ba0fccc1fef66721",
            "mirror": "r1-82403923e801668d",
            "outdoor_lighting": "r1-12f21c42eabc5abf",
            "oven": "r1-3b9dcd161d7d9c3b",
            "painting": "r1-3bdfb6c670f0a598",
            "parking_lot": "r1-fc39a75f3a80bea0",
            "patio": "r1-e7a966ed3e09d1fa",
            "plumbing_rough_in": "r1-fe085b1c4677c81b",
            "pool": "r1-66db0e48eea5925b",
            "refrigerator": "r1-f41de490a8e60e65",
            "retaining_wall": "r1-43d4a77d0d589297",
            "roofing_assembly": "r1-780706b231b0d0f4",
            "shed": "r1-2e61c9a884891734",
            "shower": "r1-3f38787b180ec900",
            "sidewalk": "r1-c955be33f0826bb1",
            "signage": "r1-9dc9e38d56fdd057",
            "sink": "r1-d82b36953753cda3",
            "site_utilities": "r1-cc930fb195a1dab6",
            "smoke_detector": "r1-a9056204adc1bcc1",
            "specialties_residual": "r1-0dd89bfc8e27b046",
            "sprinkler_system": "r1-736b20602d4d4853",
            "stove": "r1-1b37a6f138d54f08",
            "structural_framing": "r1-1f4c10d4c707f2e9",
            "thermostat": "r1-1404cea3089fb992",
            "tile": "r1-62049a6781523cf6",
            "toilet": "r1-a110cbb07f514cf8",
            "vinyl_flooring": "r1-d8cb9da569d61f23",
            "washer": "r1-8d67187a3f602e79",
            "water_heater": "r1-63124af625c830ef",
            "window_guard": "r1-d802345482f7eccd",
            "window_treatment": "r1-7983a363bccebeef",
        },
    },
}
# END FROZEN


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _values(rows: Sequence[tuple[str, ...]]) -> str:
    return ",\n".join("(" + ", ".join(_lit(v) for v in row) + ")" for row in rows)


def _name_rows() -> list[tuple[str, str, str]]:
    """(version, name, fingerprint): every row key, then every alias not shadowed by a row
    key (``canonical_key`` tries the row first)."""
    rows: list[tuple[str, str, str]] = []
    for version, table in sorted(FROZEN.items()):
        digests: dict[str, str] = table["row_digests"]
        rows += [(version, name, fp) for name, fp in sorted(digests.items())]
        rows += [
            (version, alias, digests[target])
            for alias, target in sorted(table["aliases"].items())
            if alias not in digests
        ]
    return rows


def _floor_rows() -> list[tuple[str, str, str]]:
    return [
        (version, material, table["row_digests"][key])
        for version, table in sorted(FROZEN.items())
        for material, key in sorted(table["material_rules"].items())
    ]


def _generic_rows() -> list[tuple[str, str]]:
    return [
        (version, name)
        for version, table in sorted(FROZEN.items())
        for name in table["generic_floors"]
    ]


# The fingerprint of a parsed row (label, material, version) over the frozen tables.
RESOLVE: Final = f"""
CASE
    WHEN EXISTS (SELECT 1 FROM _fp_generic g WHERE g.version = p.version AND g.name = p.label)
    THEN coalesce(
        (SELECT f.fp FROM _fp_floors f WHERE f.version = p.version AND f.material = p.material),
        '{NONE}')
    ELSE coalesce(
        (SELECT n.fp FROM _fp_names n
          WHERE n.version = p.version AND p.label <> '' AND n.name = p.label),
        (SELECT n.fp FROM _fp_names n
          WHERE n.version = p.version AND p.label LIKE '%s' AND length(p.label) > 1
            AND n.name = left(p.label, -1)),
        '{NONE}')
END"""

# ``answer_of``'s recovery period: the stored one as a number, else the bucket's standard.
RECOVERY: Final = """
CASE
    WHEN c.result ->> 'recovery_period' IS NULL
    THEN CASE c.result ->> 'bucket'
        WHEN 'y5' THEN 5 WHEN 'y7' THEN 7 WHEN 'y15' THEN 15
        WHEN 'y27_5' THEN 27.5 WHEN 'y39' THEN 39 END
    WHEN btrim(c.result ->> 'recovery_period') ~ '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$'
    THEN btrim(c.result ->> 'recovery_period')::numeric
END"""


def upgrade() -> None:
    op.execute(
        "CREATE TEMPORARY TABLE _fp_names (version text, name text, fp text, "
        "PRIMARY KEY (version, name))"
    )
    op.execute(
        "CREATE TEMPORARY TABLE _fp_floors (version text, material text, fp text, "
        "PRIMARY KEY (version, material))"
    )
    op.execute(
        "CREATE TEMPORARY TABLE _fp_generic (version text, name text, PRIMARY KEY (version, name))"
    )
    op.execute(f"INSERT INTO _fp_names (version, name, fp) VALUES\n{_values(_name_rows())}")
    op.execute(f"INSERT INTO _fp_floors (version, material, fp) VALUES\n{_values(_floor_rows())}")
    op.execute(f"INSERT INTO _fp_generic (version, name) VALUES\n{_values(_generic_rows())}")
    op.execute(
        f"""
CREATE TEMPORARY TABLE _fp_rekey AS
WITH parsed AS (
    SELECT c.org_id, c.key, c.approved_at, c.approvals, c.hits,
           split_part(c.key, '|', 1) AS label, split_part(c.key, '|', 2) AS material,
           split_part(c.key, '|', 6) AS version,
           c.result ->> 'bucket' AS bucket, {RECOVERY} AS recovery,
           c.result ->> 'section' AS section
      FROM classification_cache c
     WHERE array_length(string_to_array(c.key, '|'), 1) = 6
       AND split_part(c.key, '|', 6) IN (SELECT version FROM _fp_names)
),
resolved AS (
    SELECT p.*, regexp_replace(p.key, '[^|]*$', '') || {RESOLVE} AS new_key FROM parsed p
),
latest AS (
    SELECT DISTINCT ON (org_id, new_key) org_id, new_key, bucket, recovery, section
      FROM resolved ORDER BY org_id, new_key, approved_at DESC, key DESC
),
same AS (
    SELECT r.org_id, r.key, r.new_key, r.approved_at, r.approvals, r.hits
      FROM resolved r JOIN latest l USING (org_id, new_key)
     WHERE r.bucket IS NOT DISTINCT FROM l.bucket
       AND r.recovery IS NOT DISTINCT FROM l.recovery
       AND r.section IS NOT DISTINCT FROM l.section
)
SELECT org_id, key, new_key,
       row_number() OVER (PARTITION BY org_id, new_key ORDER BY approved_at, key) = 1 AS seed,
       sum(approvals) OVER (PARTITION BY org_id, new_key) AS approvals,
       sum(hits) OVER (PARTITION BY org_id, new_key) AS hits
  FROM same
"""
    )
    op.execute(
        "DELETE FROM classification_cache c USING _fp_rekey r "
        "WHERE c.org_id = r.org_id AND c.key = r.key AND NOT r.seed"
    )
    op.execute(
        "UPDATE classification_cache c SET key = r.new_key, approvals = r.approvals, "
        "hits = r.hits FROM _fp_rekey r "
        "WHERE c.org_id = r.org_id AND c.key = r.key AND r.seed"
    )
    op.execute("DROP TABLE _fp_rekey, _fp_names, _fp_floors, _fp_generic")


def downgrade() -> None:
    op.execute(
        f"""
CREATE TEMPORARY TABLE _fp_restore AS
WITH claims AS (
    SELECT org_id, key, approved_at,
           CASE WHEN array_length(string_to_array(key, '|'), 1) = 6
                 AND split_part(key, '|', 6) ~ '{FINGERPRINT_RE}'
                THEN regexp_replace(key, '[^|]*$', '') || rules_version
                ELSE key END AS target
      FROM classification_cache
)
SELECT org_id, key, target,
       row_number() OVER (PARTITION BY org_id, target ORDER BY approved_at DESC, key DESC) = 1
           AS keeper
  FROM claims
"""
    )
    op.execute(
        "DELETE FROM classification_cache c USING _fp_restore r "
        "WHERE c.org_id = r.org_id AND c.key = r.key AND NOT r.keeper"
    )
    op.execute(
        "UPDATE classification_cache c SET key = r.target FROM _fp_restore r "
        "WHERE c.org_id = r.org_id AND c.key = r.key AND r.keeper AND r.key <> r.target"
    )
    op.execute("DROP TABLE _fp_restore")
