"""
Verified Classification Cache - Stores engineer-approved classifications.

Phase 2 Optimization: Build classification cache organically as engineers
approve classifications. Every cached entry came from LLM + IRS document
search first, so citations are preserved for IRS defensibility.

Expected improvement:
- Study 1: 0% cache hit (cold start)
- Study 5: ~40% cache hit
- Study 20: ~70% cache hit
- Study 50+: ~85% cache hit
"""

from datetime import datetime
from typing import Optional
import logging

from google.cloud import firestore

logger = logging.getLogger(__name__)

CACHE_COLLECTION = "verified_classifications"


def _normalize_component(component_name: str) -> str:
    """
    Normalize component name for cache key.

    Examples:
        "Kitchen Cabinet" -> "kitchen_cabinet"
        "HVAC Unit" -> "hvac_unit"
        "wall-mounted light" -> "wall_mounted_light"
    """
    if not component_name:
        return ""
    return component_name.lower().strip().replace(" ", "_").replace("-", "_")


def _generate_cache_key(component_name: str, property_type: str = "residential") -> str:
    """
    Generate cache key: {normalized_component}_{property_type}

    We include property_type because IRS classification can differ:
    - Residential: 27.5-year for building components
    - Commercial: 39-year for building components
    """
    normalized = _normalize_component(component_name)
    return f"{normalized}_{property_type}"


def get_cached_classification(
    db: firestore.Client,
    component_name: str,
    property_type: str = "residential",
) -> Optional[dict]:
    """
    Look up a verified classification from cache.

    Only returns cache entries that:
    1. Have citations (IRS defensibility)
    2. Have at least 1 engineer approval

    Args:
        db: Firestore client
        component_name: Component name to look up (e.g., "carpet", "HVAC unit")
        property_type: "residential" or "commercial"

    Returns:
        Cached classification dict with keys:
        - classification: IRS classification data
        - citations: IRS document citations
        - confidence: Always 0.95 for verified cache hits
        - from_cache: True
        - cache_key: The cache key used
        - approval_count: Number of engineer approvals

        Or None if not found or not verified.
    """
    cache_key = _generate_cache_key(component_name, property_type)

    try:
        doc_ref = db.collection(CACHE_COLLECTION).document(cache_key)
        doc = doc_ref.get()

        if not doc.exists:
            return None

        data = doc.to_dict()

        # Require citations and at least 1 approval for safety
        if not data.get("citations") or data.get("approval_count", 0) < 1:
            logger.debug(f"Cache entry {cache_key} exists but not verified (approvals: {data.get('approval_count', 0)})")
            return None

        logger.info(f"Cache HIT for '{component_name}' ({property_type}) - key: {cache_key}")
        return {
            "classification": data.get("classification"),
            "citations": data.get("citations", []),
            "confidence": 0.95,  # High confidence for verified entries
            "from_cache": True,
            "cache_key": cache_key,
            "approval_count": data.get("approval_count", 0),
        }

    except Exception as e:
        logger.error(f"Cache lookup error for {cache_key}: {e}")
        return None


def save_to_cache(
    db: firestore.Client,
    component_name: str,
    classification: dict,
    citations: list[dict],
    property_type: str = "residential",
    approved_by: str = None,
    study_id: str = None,
) -> bool:
    """
    Save an engineer-approved classification to cache.

    This is called when an engineer approves a classification on the
    engineering_takeoff page. Only classifications with citations are
    cached to maintain IRS defensibility.

    Args:
        db: Firestore client
        component_name: Component name (e.g., "carpet", "HVAC unit")
        classification: IRS classification dict with section, bucket, etc.
        citations: List of IRS citation dicts
        property_type: "residential" or "commercial"
        approved_by: User ID who approved
        study_id: Study ID for audit trail

    Returns:
        True if saved successfully, False otherwise.
    """
    if not citations:
        logger.warning(f"Not caching '{component_name}' - no citations (IRS defensibility required)")
        return False

    if not component_name:
        logger.warning("Not caching - empty component name")
        return False

    cache_key = _generate_cache_key(component_name, property_type)

    try:
        doc_ref = db.collection(CACHE_COLLECTION).document(cache_key)
        existing = doc_ref.get()

        if existing.exists:
            # Increment approval count and update timestamps
            doc_ref.update({
                "approval_count": firestore.Increment(1),
                "last_approved_at": datetime.utcnow(),
                "last_approved_by": approved_by,
                "study_ids": firestore.ArrayUnion([study_id] if study_id else []),
            })
            logger.info(f"Updated cache entry '{cache_key}' (approval_count++)")
        else:
            # Create new entry
            doc_ref.set({
                "component_name": component_name,
                "normalized_name": _normalize_component(component_name),
                "property_type": property_type,
                "classification": classification,
                "citations": citations,
                "approval_count": 1,
                "created_at": datetime.utcnow(),
                "created_by": approved_by,
                "last_approved_at": datetime.utcnow(),
                "last_approved_by": approved_by,
                "study_ids": [study_id] if study_id else [],
            })
            logger.info(f"Created cache entry '{cache_key}' for '{component_name}'")

        return True

    except Exception as e:
        logger.error(f"Cache save error for {cache_key}: {e}")
        return False


def get_cache_stats(db: firestore.Client) -> dict:
    """
    Get cache statistics for monitoring.

    Returns:
        Dict with:
        - total_entries: Number of cached classifications
        - total_approvals: Sum of all approval counts
    """
    try:
        docs = list(db.collection(CACHE_COLLECTION).stream())
        total_approvals = sum(doc.to_dict().get("approval_count", 0) for doc in docs)
        return {
            "total_entries": len(docs),
            "total_approvals": total_approvals,
        }
    except Exception as e:
        logger.error(f"Cache stats error: {e}")
        return {"error": str(e)}


def invalidate_cache_entry(
    db: firestore.Client,
    component_name: str,
    property_type: str = "residential",
    reason: str = None,
) -> bool:
    """
    Invalidate (delete) a cache entry.

    Use this if IRS guidance changes or an entry is found to be incorrect.

    Args:
        db: Firestore client
        component_name: Component name
        property_type: "residential" or "commercial"
        reason: Reason for invalidation (logged for audit)

    Returns:
        True if deleted, False otherwise.
    """
    cache_key = _generate_cache_key(component_name, property_type)

    try:
        doc_ref = db.collection(CACHE_COLLECTION).document(cache_key)
        doc = doc_ref.get()

        if doc.exists:
            # Log before deleting for audit trail
            data = doc.to_dict()
            logger.warning(
                f"Invalidating cache entry '{cache_key}' "
                f"(approvals: {data.get('approval_count', 0)}, reason: {reason})"
            )
            doc_ref.delete()
            return True
        else:
            logger.debug(f"Cache entry '{cache_key}' not found for invalidation")
            return False

    except Exception as e:
        logger.error(f"Cache invalidation error for {cache_key}: {e}")
        return False
