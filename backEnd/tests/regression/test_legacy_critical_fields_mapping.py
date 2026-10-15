"""A.8: every CRITICAL_FIELDS name must be a field the mapping tables produce.

Commit 277703c (the Azure DI work that was uncommitted at review time) renamed the
MISMO and Azure DI mapping targets to the cleaner's canonical names
(gross_living_area -> gla_sqft, effective_date -> effective_date_of_appraisal,
final_opinion_of_market_value -> final_market_value) but left CRITICAL_FIELDS on
the old names. confidence.py / extractor.py / validation.py look fields up by those
names, so GLA and effective date always read as missing, needs_review is always
true, and overall confidence is computed from fewer fields. ``appraised_value``
is not a target of either table at all.
"""

from __future__ import annotations

import pytest


def _produced_leaf_names() -> set[str]:
    from evidence_layer.src.tiered_extraction.field_mappings import (
        AZURE_DI_KEY_MAPPINGS,
        MISMO_FIELD_MAPPINGS,
    )

    targets = list(MISMO_FIELD_MAPPINGS.values()) + list(AZURE_DI_KEY_MAPPINGS.values())
    # Targets are "section.field"; CRITICAL_FIELDS holds bare field names.
    return {target.rsplit(".", 1)[-1] for target in targets}


@pytest.mark.regression
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "A.8: 277703c renamed mapping targets (gla_sqft, effective_date_of_appraisal, "
        "final_market_value) without updating CRITICAL_FIELDS"
    ),
)
def test_every_critical_field_is_a_mapping_target() -> None:
    from evidence_layer.src.tiered_extraction.field_mappings import CRITICAL_FIELDS

    produced = _produced_leaf_names()
    if not produced:
        pytest.fail("mapping tables are empty; update this test")
    orphaned = sorted(set(CRITICAL_FIELDS) - produced)
    assert not orphaned, f"CRITICAL_FIELDS not produced by any mapping: {orphaned}"
