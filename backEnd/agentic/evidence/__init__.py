"""
Evidence aggregation module for unified citation tracking.

Phase 6: Provides organized evidence packs with deduplication and filtering.
"""

from .models import (
    EvidenceEntry,
    EvidencePack,
    EvidenceSummary,
)
from .aggregator import EvidenceAggregator

__all__ = [
    "EvidenceEntry",
    "EvidencePack",
    "EvidenceSummary",
    "EvidenceAggregator",
]
