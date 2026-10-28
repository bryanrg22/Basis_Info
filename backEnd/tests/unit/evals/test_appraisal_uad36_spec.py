"""The UAD 3.6 mapping generator (``evals/appraisal/uad36_spec.py``) on fabricated spec rows."""

from __future__ import annotations

import pytest

from basis.extraction.field_mappings import FIELDS
from evals.appraisal.uad36_spec import (
    NONE,
    SELECTORS,
    G,
    SpecError,
    SpecRow,
    Specs,
    captions_of,
    resolve,
)

PATH = "MESSAGE/X/PROPERTIES/PROPERTY/SITE/SITE_COST/"


def specs(*extra: SpecRow) -> Specs:
    a1 = SpecRow(
        uid="9000.0001",
        xpath=PATH,
        point="SiteEstimatedValueAmount",
        attribute="",
        locations=("4.105: Site - Site Valuation Methodology - Opinion of Site Value",),
        fmt="Amount",
        subject=True,
    )
    g1 = SpecRow(
        uid="9000.0001",
        xpath=PATH,
        point="SiteEstimatedValueAmount",
        attribute="",
        locations=a1.locations,
        subject=True,
        legacy_ids=("3-14",),
        legacy_label="Opinion of Site Value",
    )
    comparable = SpecRow(
        uid="9000.0002",
        xpath=PATH,
        point="SiteEstimatedValueAmount",
        attribute="",
        locations=("22.03.01: Sales Comparison Approach - Site - Site Value",),
        comparable=True,
        legacy_ids=("3-14",),
        legacy_label="Opinion of Site Value",
    )
    return Specs([a1, *[r for r in extra if not r.legacy_ids]], [g1, comparable, *extra], {})


def test_every_catalogue_key_has_one_selector() -> None:
    assert set(SELECTORS) == {spec.key for spec in FIELDS}


def test_a_g1_selector_resolves_to_the_subject_row_with_its_caption_and_legacy_field() -> None:
    entry = resolve(specs(), "cost_approach.site_value", G("3-14", "SiteEstimatedValueAmount"))
    assert entry["status"] == "mapped"
    assert [s["uid"] for s in entry["sources"]] == ["9000.0001"]  # never the comparable row
    assert entry["sources"][0]["path"] == PATH + "SiteEstimatedValueAmount"
    assert entry["captions"] == ("Opinion of Site Value",)
    assert entry["legacy"] == (("3-14", "Opinion of Site Value"),)
    assert entry["label_match"] is True


def test_an_ambiguous_or_unknown_selector_is_refused() -> None:
    twin = SpecRow(
        uid="9000.0003",
        xpath=PATH,
        point="SiteEstimatedValueAmount",
        attribute="",
        locations=("25.004: Cost Approach - Site Value",),
        subject=True,
        legacy_ids=("3-14",),
    )
    with pytest.raises(SpecError, match="2 rows"):
        resolve(specs(twin), "cost_approach.site_value", G("3-14", "SiteEstimatedValueAmount"))
    with pytest.raises(SpecError, match="0 rows"):
        resolve(specs(), "cost_approach.site_value", G("3-99", "SiteEstimatedValueAmount"))


def test_a_key_without_an_equivalent_keeps_its_reason() -> None:
    entry = resolve(specs(), "subject.tax_year", NONE("not on the redesigned URAR"))
    assert entry["status"] == "no_equivalent" and entry["reason"] == "not on the redesigned URAR"


def test_captions_skip_grids_and_unlabelled_boxes_use_their_subsection() -> None:
    lines = [
        "1.001: Summary - {No Subsection} - Opinion of Market Value",
        "26.007: Reconciliation - Appraisal Summary - Opinion of Market Value",
        "22.15.15: Sales Comparison Approach - Summary - Indicated Value",
        "3.031: Subject Property - Legal Description - {No Label}",
    ]
    assert captions_of(lines) == ["Opinion of Market Value", "Legal Description"]
    assert "Indicated Value" in captions_of(lines, grid=True)
