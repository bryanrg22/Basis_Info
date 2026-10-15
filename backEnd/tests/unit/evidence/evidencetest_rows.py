"""In-memory ORM rows for the pure evidence pack tests (no database)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from basis.db.enums import Bucket, ClassificationMethod
from basis.db.models import Asset, Citation, Component, CostEstimate, Room, Takeoff
from basis.evidence.pack import ChunkInfo

STUDY = uuid.UUID("00000000-0000-4000-8000-00000000aaaa")
KITCHEN = uuid.UUID("00000000-0000-4000-8000-0000000000e1")
BATH = uuid.UUID("00000000-0000-4000-8000-0000000000e2")
CHUNK_A = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
CHUNK_B = uuid.UUID("00000000-0000-4000-8000-0000000000c2")


def _id(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def rows() -> dict[str, Any]:
    rooms = {
        KITCHEN: Room(id=KITCHEN, study_id=STUDY, revision=1, name="Kitchen", room_type="kitchen"),
        BATH: Room(id=BATH, study_id=STUDY, revision=1, name="Bathroom", room_type="bathroom"),
    }
    components = {
        _id(11): Component(
            id=_id(11), study_id=STUDY, revision=1, room_id=KITCHEN, label="carpet", material=None
        ),
        _id(12): Component(
            id=_id(12), study_id=STUDY, revision=1, room_id=KITCHEN, label="kitchen_cabinet"
        ),
        _id(13): Component(id=_id(13), study_id=STUDY, revision=1, room_id=BATH, label="toilet"),
    }

    def asset(n: int, component: int, bucket: Bucket, period: str, review: bool) -> Asset:
        return Asset(
            id=_id(n),
            study_id=STUDY,
            revision=1,
            component_id=_id(component),
            category=f"category {component}",
            bucket=bucket,
            recovery_period=Decimal(period),
            irs_section="1245" if bucket is Bucket.Y5 else "1250",
            method=ClassificationMethod.RULE,
            confidence=0.98,
            needs_review=review,
            review_reason="citation_unverified" if review else None,
        )

    assets = [
        asset(21, 11, Bucket.Y5, "5.0", False),
        asset(22, 12, Bucket.Y5, "5.0", False),
        asset(23, 13, Bucket.Y27_5, "27.5", True),
    ]
    takeoffs = {
        _id(21): Takeoff(asset_id=_id(21), quantity=Decimal("18.33"), unit="SY", formula="f"),
        _id(22): Takeoff(asset_id=_id(22), quantity=Decimal("48"), unit="LF", formula="g"),
        _id(23): Takeoff(asset_id=_id(23), quantity=Decimal("1"), unit="EA", formula="h"),
    }
    costs = {
        _id(21): CostEstimate(
            asset_id=_id(21), unit_cost=Decimal("74.66"), total=Decimal("1368.52"), cost_year=2026
        ),
        _id(22): CostEstimate(
            asset_id=_id(22), unit_cost=Decimal("314.76"), total=Decimal("15108.48")
        ),
        _id(23): CostEstimate(asset_id=_id(23), unit_cost=None, total=None, source_row="missing"),
    }
    citations = [
        Citation(asset_id=_id(21), chunk_id=CHUNK_B, excerpt="Carpeting - 5-year recovery"),
        Citation(asset_id=_id(21), chunk_id=CHUNK_A, excerpt="Asset class 57.0 includes carpeting"),
        # duplicate of the first after normalization
        Citation(asset_id=_id(21), chunk_id=CHUNK_B, excerpt="carpeting  -  5-year RECOVERY"),
        Citation(asset_id=_id(22), chunk_id=CHUNK_B, excerpt="Cabinets - kitchen fixtures"),
    ]
    chunks = {
        CHUNK_A: ChunkInfo("IRS_REV_PROC_87_56", 12, 12, "reference"),
        CHUNK_B: ChunkInfo("IRS_IRS_COST_SEG_ATG__2024", 412, 415, "reference"),
    }
    return {
        "study_id": STUDY,
        "revision": 1,
        "assets": assets,
        "components": components,
        "rooms": rooms,
        "takeoffs": takeoffs,
        "costs": costs,
        "citations": citations,
        "chunks": chunks,
    }
