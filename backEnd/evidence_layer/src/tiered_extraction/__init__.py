"""
Tiered Appraisal Extraction System

A production-grade extraction system using multiple tiers:
- Tier 1: MISMO XML Parser (confidence: 1.0)
- Tier 2: Azure Document Intelligence (confidence: 0.7-0.95)
- Tier 3: GPT-4o Vision Fallback (confidence: 0.6-0.9)
- Tier 4: Regex Fallback (confidence: 0.5-0.8)
- Tier 5: Validation & Confidence Aggregation
"""

from .extractor import TieredExtractor
from .confidence import FieldResult, ExtractionResult
from .field_mappings import CRITICAL_FIELDS, URAR_SECTIONS, CONFIDENCE_THRESHOLDS

__all__ = [
    "TieredExtractor",
    "FieldResult",
    "ExtractionResult",
    "CRITICAL_FIELDS",
    "URAR_SECTIONS",
    "CONFIDENCE_THRESHOLDS",
]
