"""Appraisal eval v2 scorer (``evals/appraisal/score.py``, SPEC §5 and §8) on fabricated data.

One fabricated document carries every outcome kind; the counts, rates, page and grounding
checks, display, review gate (recall and load at 0.6/0.7/0.9, the risk-coverage curve, the
document-level check), issue matching and pass^k are compared with values computed by hand
in the comments. A second test covers the document-level Wilson interval and the clustered
standard error; another checks that neither the report nor score.json carries a value.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from evals.appraisal.score import (
    named_keys,
    production_critical,
    render_report,
    score_run,
    tier_for,
)
from evals.appraisal.truth import CATALOGUE_KEYS, DocumentTruth
from evals.metrics import wilson_interval

from .appraisal_support import field, prediction_doc, predictions, repeat, truth_doc, truth_set

N = len(CATALOGUE_KEYS)

TRUTH_A = {
    # critical tier; also printed on page 1 (page lenient there)
    "cost_approach.site_value": {
        "status": "present",
        "type": "currency",
        "value": 85000,
        "page": 3,
        "also_on": [1],
    },
    "listing_and_contract.contract_price": {
        "status": "present",
        "type": "currency",
        "value": 300000,
        "page": 1,
    },
    "improvements.gla_sqft": {"status": "present", "type": "int", "value": 1740, "page": 2},
    "subject.hoa_dues": {"status": "present", "type": "currency", "value": 0, "page": 1},
    "reconciliation.indicated_value_income_approach": {
        "status": "not_developed",
        "printed": "0",
        "page": 3,
    },
    "subject.county": {
        "status": "present",
        "type": "text",
        "value": "Fabricated County",
        "page": 1,
    },
    "improvements.heating_type": {
        "status": "present",
        "type": "enum_multi",
        "value": ["fwa", {"code": "other", "text": "Infloor"}],
        "page": 2,
    },
    "appraiser.name": {"status": "unscorable", "note": "illegible"},
    "subject.zip": {"status": "present", "type": "text", "value": "ZipFab", "verified": False},
    "improvements.garage_type": {
        "status": "present",
        "type": "enum",
        "value": "attached",
        "extra": True,
        "page": 2,
    },
}

FIELDS_A = {
    "cost_approach.site_value": field("$85,000", "85000", page=1),  # correct, lenient
    "listing_and_contract.contract_price": field("310,000", "310000", confidence=0.5),  # wrong
    # improvements.gla_sqft: not predicted (missed, a production critical field)
    "subject.hoa_dues": field("$0", "0"),  # correct: $0 is a value
    "reconciliation.indicated_value_income_approach": field(
        "0", "0", confidence=0.65, source="llm", page=3
    ),  # spurious
    "subject.county": field("Fabricated Cnty"),  # wrong (ANLS 1 - 2/17)
    "improvements.heating_type": field("FWA Other: Infloor", confidence=0.92, page=2),  # correct
    "appraiser.name": field("Somebody"),  # unscorable: excluded
    "subject.zip": field("ZipFab"),  # unverified, correct
    "improvements.garage_type": field("Attached", page=2),  # extra, correct
}
ISSUES_A = [
    {"rule": "cost_approach_total", "keys": ["cost_approach.indicated_value"]},
    {"rule": "sources_disagree", "keys": ["listing_and_contract.contract_price"]},
]
REASONS_A = [
    "missing:improvements.gla_sqft",
    "low_confidence:listing_and_contract.contract_price=0.50<0.90",
    "sources_disagree:listing_and_contract.contract_price",
]
EXPECTED_ISSUES_A = [
    {"rule": "cost_approach_total", "keys": ["cost_approach.indicated_value"], "note": "x"},
    {"rule": "site_value_ratio", "keys": ["cost_approach.site_value"]},
]
PAGES_A = [
    "Contract Price $ 300,000 HOA $0 Opinion of Site Value $ 85,000 County Fabricated County",
    "Heating FWA Other: Infloor Garage Attached",
    "",  # no text layer on page 3
]


def doc_a() -> DocumentTruth:
    return truth_doc(TRUTH_A, expected_issues=EXPECTED_ISSUES_A)


def scored(extra_repeat: bool = False) -> dict[str, Any]:
    reps = [repeat(FIELDS_A, issues=ISSUES_A, reasons=REASONS_A, needs_review=True)]
    if extra_repeat:
        second = dict(FIELDS_A)
        second["cost_approach.site_value"] = field("$95,000", "95000", page=1)
        reps.append(repeat(second, number=2, issues=ISSUES_A, reasons=REASONS_A))
    failed = prediction_doc(
        "doc_c",
        {"repeat": 1, "status": "missing_recording", "error": "x"},
        status="missing_recording",
    )
    truth = truth_set(doc_a(), truth_doc({}, document="doc_c"))
    return score_run(
        {"synthetic_1004": truth},
        predictions(prediction_doc("doc_a", *reps), failed),
        page_texts=lambda doc: PAGES_A if doc.document == "doc_a" else None,
    )


def test_every_outcome_and_the_four_rates() -> None:
    s = scored()
    overall = s["overall"]
    # Present (verified catalogue): site, contract, gla, hoa, county, heating.
    assert (overall["correct"], overall["wrong"], overall["missed"]) == (3, 2, 1)
    assert overall["spurious"] == 1
    # Everything else absent, minus unscorable appraiser.name and the unverified zip.
    assert overall["correct_absent"] == N - 9
    assert overall["accuracy"] == pytest.approx(3 / 6)
    assert overall["wrong_value_rate"] == pytest.approx(3 / 6)  # (2 wrong + 1 spurious) / 6
    assert overall["coverage"] == pytest.approx(5 / 6)
    assert overall["spurious_rate"] == pytest.approx(1 / (N - 8))
    assert math.isnan(overall["accuracy_se"])  # one document: not estimable
    assert s["documents"]["unscorable_fields"] == 1
    assert s["documents"]["scored"] == 1
    assert s["documents"]["failed"] == [
        {"set": "synthetic_1004", "document": "doc_c", "status": "missing_recording"}
    ]
    # Unverified and extra keys are scored apart, not in the headline.
    assert s["unverified"]["correct"] == 1 and s["unverified"]["present"] == 1
    assert s["extra"]["correct"] == 1 and s["extra"]["present"] == 1
    assert s["all_fields"]["present"] == 8


def test_breakdowns() -> None:
    s = scored()
    tier = s["by"]["tier"]
    assert (tier["critical"]["correct"], tier["critical"]["wrong"]) == (1, 1)  # site, contract
    assert tier["high"]["missed"] == 1  # gla
    assert s["by"]["source"]["llm"]["spurious"] == 1
    assert s["by"]["source"]["none"]["missed"] == 1
    assert s["by"]["source"]["azure_di"]["correct"] == 3
    assert s["by"]["critical_flag"]["true"]["missed"] == 1  # gla is production critical
    assert s["by"]["type"]["enum_multi"]["correct"] == 1
    assert s["by"]["document"]["synthetic_1004/doc_a"]["present"] == 6
    assert s["by"]["section"]["cost_approach"]["correct"] == 1
    assert s["keys"]["subject.county"]["wrong"] == 1
    assert s["keys"]["subject.county"]["accuracy_interval"] is None  # one document


def test_pages_grounding_display_anls() -> None:
    s = scored()
    page = s["page"]
    # Correct with a page: site (page 1, in also_on -> lenient), hoa (strict), heating (strict).
    assert (page["page_cited"], page["page_strict"], page["page_lenient"]) == (3, 2, 3)
    # Grounding over predictions citing a page with text: site, contract, hoa, county,
    # heating (income cites page 3, which has no text layer). On the page: site ("$85,000"
    # matches "$ 85,000"), hoa, heating.
    assert (page["grounding_checked"], page["grounded"]) == (5, 3)
    assert page["grounding_unchecked"] == 1
    # Display over correct values: "$85,000" and "$0" match; the heating text does not
    # equal "FWA, Infloor".
    display = s["display"]
    assert (display["checked"], display["matches"]) == (3, 2)
    assert display["by_type"]["enum_multi"] == {"matches": 0, "checked": 1}
    assert s["text_anls"] == {"fields": 1, "mean_anls": pytest.approx(1 - 2 / 17)}


def test_review_gate_recall_load_and_documents() -> None:
    gate = scored()["review_gate"]
    # Errors: contract (wrong, 0.50, named), county (wrong, 0.95), income (spurious, 0.65),
    # gla (missed critical, named by "missing:"). Predicted: 6 values.
    at = gate["thresholds"]
    assert (at["0.6"]["errors"], at["0.6"]["errors_flagged"], at["0.6"]["flagged"]) == (4, 2, 1)
    assert at["0.6"]["error_recall"] == pytest.approx(0.5)
    assert at["0.6"]["review_load"] == pytest.approx(1 / 6)
    assert (at["0.7"]["errors_flagged"], at["0.7"]["flagged"]) == (3, 2)
    assert (at["0.9"]["errors_flagged"], at["0.9"]["flagged"]) == (3, 2)  # 0.92 is above
    curve = {round(p["threshold"], 6): p for p in gate["risk_coverage"]}
    assert set(curve) == {0.0, 0.5, 0.65, 0.92, 0.95, 1.000001}
    # t = 0: everything but the named contract price is accepted; county and income are bad.
    assert curve[0.0]["coverage"] == pytest.approx(5 / 6)
    assert curve[0.0]["risk"] == pytest.approx(2 / 5)
    assert curve[0.95]["accepted"] == 3 and curve[0.95]["risk"] == pytest.approx(1 / 3)
    assert curve[1.000001]["coverage"] == 0 and math.isnan(curve[1.000001]["risk"])
    documents = gate["documents"]
    assert documents["documents_with_critical_error"] == 1
    assert documents["needs_review_on_critical_error"] == 1
    assert documents["missed_documents"] == []


def test_document_gate_reports_a_silent_critical_error() -> None:
    truth = truth_set(doc_a())
    fields = dict(FIELDS_A)
    run = predictions(prediction_doc("doc_a", repeat(fields, needs_review=False)))
    documents = score_run({"synthetic_1004": truth}, run)["review_gate"]["documents"]
    assert documents["missed_documents"] == ["synthetic_1004/doc_a"]
    assert documents["rate"] == 0.0


def test_issue_matching() -> None:
    issues = scored()["issues"]
    # cost_approach_total matches (rule and key overlap); site_value_ratio was not raised;
    # sources_disagree matches no expected issue (a false alarm).
    assert (issues["expected"], issues["found"], issues["raised"], issues["false_alarms"]) == (
        2,
        1,
        2,
        1,
    )
    assert issues["by_rule"]["site_value_ratio"] == {
        "expected": 1,
        "found": 0,
        "raised": 0,
        "false_alarms": 0,
    }
    assert issues["by_rule"]["sources_disagree"]["false_alarms"] == 1


def test_issue_with_the_rule_on_other_keys_is_not_a_match() -> None:
    truth = truth_set(
        truth_doc({}, expected_issues=[{"rule": "r", "keys": ["a.b"]}, {"rule": "s", "keys": []}])
    )
    raised = [{"rule": "r", "keys": ["c.d"]}, {"rule": "s", "keys": ["x.y"]}]
    run = predictions(prediction_doc("doc_a", repeat({}, issues=raised)))
    issues = score_run({"synthetic_1004": truth}, run)["issues"]
    # "r" on other keys: not found, and a false alarm; "s" expects no keys: any match.
    assert (issues["found"], issues["false_alarms"]) == (1, 1)


def test_pass_hat_k_across_repeats() -> None:
    reps = scored(extra_repeat=True)["repeats"]
    fields = N - 2  # headline fields: every catalogue key minus unscorable and unverified
    assert reps["fields"] == fields
    # Site value is right in repeat 1 and wrong in repeat 2; the other successes hold.
    successes_both = (N - 9) + 2  # correct_absent + hoa + heating
    assert reps["pass_hat_k"]["2"] == pytest.approx(successes_both / fields)
    assert reps["pass_hat_k"]["1"] == pytest.approx((successes_both + 0.5) / fields)
    assert reps["accuracy_by_repeat"] == {"1": pytest.approx(0.5), "2": pytest.approx(2 / 6)}


def test_wilson_per_key_and_clustered_se_over_documents() -> None:
    site = {"status": "present", "type": "currency", "value": 85000, "page": 1}
    docs = [truth_doc({"cost_approach.site_value": site}, document=f"d{i}") for i in range(3)]
    run = predictions(
        prediction_doc("d0", repeat({"cost_approach.site_value": field("85,000", "85000")})),
        prediction_doc("d1", repeat({"cost_approach.site_value": field("85,000", "85000")})),
        prediction_doc("d2", repeat({"cost_approach.site_value": field("58,000", "58000")})),
    )
    s = score_run({"synthetic_1004": truth_set(*docs)}, run)
    key = s["keys"]["cost_approach.site_value"]
    assert (key["documents"], key["correct"], key["present"]) == (3, 2, 3)
    assert key["accuracy_interval"] == pytest.approx(list(wilson_interval(2, 3)))
    # Three single-field clusters: CR1 SE of [1, 1, 0] = sqrt(3/2 * (1/9+1/9+4/9) / 9).
    expected = math.sqrt(3 / 2 * (1 / 9 + 1 / 9 + 4 / 9) / 9)
    assert key["accuracy_se"] == pytest.approx(expected)


def test_unparseable_value_is_wrong_and_absent_zero_is_spurious() -> None:
    truth = truth_set(
        truth_doc(
            {
                "cost_approach.site_value": {"status": "present", "type": "currency", "value": 1},
                "subject.hoa_dues": {"status": "not_developed", "printed": "0"},
            }
        )
    )
    fields = {
        "cost_approach.site_value": field("see addendum"),
        "subject.hoa_dues": field("0", "0"),
    }
    s = score_run({"synthetic_1004": truth}, predictions(prediction_doc("doc_a", repeat(fields))))
    assert s["keys"]["cost_approach.site_value"]["wrong"] == 1
    assert s["keys"]["subject.hoa_dues"]["spurious"] == 1


def test_tiers_and_the_production_flag() -> None:
    assert tier_for("cost_approach.site_value") == "critical"
    assert tier_for("subject.property_address") == "critical"
    assert tier_for("cost_approach.depreciation_physical") == "high"
    assert tier_for("cost_approach.remaining_economic_life") == "high"
    assert tier_for("cost_approach.cost_data_source") == "low"
    assert tier_for("improvements.gla_sqft") == "high"
    assert tier_for("improvements.garage_type") == "high"  # an extra garage key
    assert tier_for("site.zoning_compliance") == "medium"
    assert tier_for("site.flood_zone") == "medium"
    assert tier_for("improvements.bedrooms") == "low"
    assert tier_for("improvements.bedrooms", "critical") == "critical"  # truth override
    assert production_critical("improvements.bedrooms")
    assert not production_critical("cost_approach.site_value")


def test_named_keys() -> None:
    keys = named_keys(
        [{"rule": "r", "keys": ["a.b"]}],
        [
            "missing:c.d",
            "low_confidence:e.f=0.50<0.90",  # the production threshold: left to the sweep
            "rule:g.h,i.j",
            "azure_di_rejected",
        ],
    )
    assert keys == {"a.b", "c.d", "g.h", "i.j"}


def test_report_and_score_carry_no_values() -> None:
    s = scored(extra_repeat=True)
    report = render_report(s, {"run": "test", "mode": "replay"})
    dumped = json.dumps(s)
    for value in ("85,000", "85000", "310", "Fabricated", "Infloor", "Somebody", "ZipFab"):
        assert value not in report, value
        assert value not in dumped, value
    assert "cost_approach.site_value" in report
    assert "## Review gate" in report and "## Per key" in report
    assert "synthetic_1004/doc_c: missing_recording" in report
