"""``appraisal_fields`` critical accuracy and ``assets`` pass^k / citation validity.

Values are computed by hand in the comments; the text-step benchmark (EVAL_REPORT section
17) reads these metrics.
"""

from __future__ import annotations

import pytest

from evals import datasets as ds
from evals.run import score_assets, score_fields


def fields() -> ds.AppraisalFields:
    return ds.AppraisalFields.model_validate(
        {
            "documents": [
                {
                    "document": "doc.pdf",
                    "fields": [
                        {"field": "a.price", "expected": 100, "type": "currency", "critical": True},
                        {"field": "a.year", "expected": 5, "type": "int", "critical": True},
                        {"field": "a.name", "expected": "x", "type": "text"},
                        {"field": "a.rooms", "expected": 7, "type": "int"},
                    ],
                }
            ]
        }
    )


def test_critical_accuracy_and_pass_k_over_repeats() -> None:
    saved = {
        "doc.pdf": {
            "a.price": {"repeats": ["$100", "$100", "$100"]},  # right 3 of 3
            "a.year": {"repeats": ["5", "9", "5"]},  # right 2 of 3 (9 is outside +/-1)
            "a.name": {"repeats": ["X", None, "x"]},  # right 2 of 3
            # a.rooms never predicted: wrong 3 of 3
        }
    }
    result = score_fields(fields(), "dev", 3, saved)
    rows = {r.metric: r for r in result.rows}
    assert rows["normalized_accuracy"].value == pytest.approx(3 / 4)  # repeat 1
    assert rows["critical_accuracy"].value == pytest.approx(1.0)
    assert rows["critical_accuracy"].n == 2
    assert rows["critical_accuracy"].details == {"missed": []}
    assert rows["pass^3"].value == pytest.approx(1 / 4)  # only a.price is right every time
    assert rows["pass^1"].value == pytest.approx((1 + 2 / 3 + 2 / 3 + 0) / 4)
    assert rows["critical_pass^3"].value == pytest.approx(1 / 2)
    assert rows["critical_pass^1"].value == pytest.approx((1 + 2 / 3) / 2)


def test_critical_misses_are_named_and_no_critical_rows_without_critical_labels() -> None:
    saved = {"doc.pdf": {"a.price": "$999", "a.year": "5"}}
    rows = {r.metric: r for r in score_fields(fields(), "dev", 1, saved).rows}
    assert rows["critical_accuracy"].value == pytest.approx(1 / 2)
    assert rows["critical_accuracy"].details == {"missed": ["a.price"]}
    assert "critical_pass^1" not in rows  # one repeat: no pass^k

    plain = ds.AppraisalFields.model_validate(
        {"documents": [{"document": "d", "fields": [{"field": "a.b", "expected": "x"}]}]}
    )
    rows = {r.metric: r for r in score_fields(plain, "dev", 3, {"d": {"a.b": "x"}}).rows}
    assert "critical_accuracy" not in rows and "critical_pass^3" not in rows
    assert rows["pass^3"].value == 1.0


def assets() -> ds.Assets:
    return ds.Assets.model_validate(
        {
            "assets": [
                {"id": "r1", "component": "Carpet", "bucket": "y5"},
                {"id": "r2", "component": "Roofing", "bucket": "y39"},
                {"id": "r3", "component": "Painting", "bucket": "y39"},
                {"id": "r4", "component": "Unlabelled", "bucket": None},
            ]
        }
    )


def test_assets_bucket_pass_k_and_citation_validity() -> None:
    cited = {"claimed": 2, "verified": 1}
    saved = {
        "r1": {
            "repeats": [
                {"section": "1245", "bucket": "y5", "citations": cited},
                {"section": "1245", "bucket": "y5", "citations": cited},
                {"section": "1245", "bucket": "y7", "citations": cited},
            ]
        },
        "r2": {"section": "1250", "bucket": "y39", "citations": {"claimed": 1, "verified": 1}},
        "r3": {"section": "1245", "bucket": "y5", "citations": {"claimed": 0, "verified": 0}},
        "r4": {"section": "1250", "bucket": "y39", "citations": {"claimed": 1, "verified": 0}},
    }
    rows = {r.metric: r for r in score_assets(assets(), "dev", 3, saved).rows}
    assert rows["bucket_accuracy"].value == pytest.approx(2 / 3)  # r1, r2 right on repeat 1
    assert rows["bucket_pass^3"].value == pytest.approx(1 / 3)  # r2 only
    assert rows["bucket_pass^1"].value == pytest.approx((2 / 3 + 1 + 0) / 3)
    assert rows["bucket_pass^3"].n == 3  # the unlabelled row is not a bucket item
    # Citations on repeat 1 of every row, labelled or not: 2 of 4 claimed passed.
    assert rows["citation_validity"].value == pytest.approx(2 / 4)
    assert rows["citation_validity"].details == {"claimed": 4, "verified": 2}
    low, high = rows["citation_validity"].interval or (0.0, 0.0)
    assert low < 0.5 < high
    assert rows["rows_with_verified_citation"].value == pytest.approx(2 / 4)


def test_assets_without_citation_counts_report_no_citation_rows() -> None:
    saved = {"r1": {"section": "1245", "bucket": "y5"}}
    rows = {r.metric: r for r in score_assets(assets(), "dev", 1, saved).rows}
    assert "citation_validity" not in rows and "rows_with_verified_citation" not in rows
    assert "bucket_pass^1" not in rows  # one repeat
    baseline = {r.metric for r in score_assets(assets(), "dev", 3, None).rows}
    assert "citation_validity" not in baseline and "bucket_pass^3" in baseline


def test_no_claimed_citations_is_not_a_division_by_zero() -> None:
    saved = {"r1": {"bucket": "y5", "citations": {"claimed": 0, "verified": 0}}}
    rows = {r.metric: r for r in score_assets(assets(), "dev", 1, saved).rows}
    assert rows["citation_validity"].n == 0
    assert rows["citation_validity"].value != rows["citation_validity"].value  # NaN
    assert rows["rows_with_verified_citation"].value == 0.0
