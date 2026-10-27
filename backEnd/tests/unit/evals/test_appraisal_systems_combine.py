"""Validators (passing and failing fabricated numbers, the rounding tolerance), the
``validate_reask`` wrapper (only implicated keys re-asked, with their page images; a
surviving issue stays, a resolved one goes; no issue, no call) and ``vote`` (agreement,
disagreement, one-sided values)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from basis.extraction.results import Candidate, ExtractedField
from basis.llm import ScriptedProvider, ScriptedTurn
from evals.appraisal.systems.base import ModelChoice, SystemEnv, SystemOutput, finish
from evals.appraisal.systems.combine import ValidateReask, Vote, implicated, vote_fields
from evals.appraisal.systems.grounded import GroundedAnswer, GroundedValue
from evals.appraisal.systems.validators import VALIDATOR_RULES, close, gla_mentions, validate

from .appraisal_systems_support import RawClient, make_env, pdf_bytes, raw_layout


def f(
    key: str, raw: str, page: int | None = 1, confidence: float = 0.95, source: str = "azure"
) -> ExtractedField:
    return ExtractedField.from_candidate(
        Candidate(key=key, raw_text=raw, page=page, confidence=confidence, source=source)  # type: ignore[arg-type]
    )


def fields(**values: str) -> dict[str, ExtractedField]:
    out = {}
    for name, raw in values.items():
        key = name.replace("__", ".")
        out[key] = f(key, raw)
    return out


def rules(issues: Any) -> list[str]:
    return sorted(i.rule for i in issues)


# Client-like cost approach, internally consistent.
COST = dict(
    cost_approach__dwelling_sqft="1,740",
    cost_approach__dwelling_cost_per_sqft="150.00",
    cost_approach__dwelling_cost="261,000",
    cost_approach__garage_sqft="480",
    cost_approach__garage_cost_per_sqft="40.00",
    cost_approach__garage_cost="19,200",
    cost_approach__total_cost_new="280,200",
    cost_approach__depreciation="(56,040)",
    cost_approach__depreciated_cost_improvements="224,160",
    cost_approach__site_improvements_value="25,000",
    cost_approach__site_value="85,000",
    cost_approach__indicated_value="334,160",
    reconciliation__indicated_value_cost_approach="334,160",
    reconciliation__final_market_value="412,000",
    appraiser__appraised_value="412,000",
    reconciliation__effective_date_of_appraisal="03/15/2026",
    appraiser__effective_date="03/15/2026",
    improvements__gla_sqft="1,740",
)


def test_close_tolerance() -> None:
    assert close(Decimal("261000.99"), Decimal(261000))  # under $1
    assert close(Decimal(262_300), Decimal(261_000))  # 0.5% of 261,000 is 1,305
    assert not close(Decimal(262_400), Decimal(261_000))
    assert not close(Decimal(102), Decimal(100))  # $2 on $100 exceeds both bounds


def test_consistent_document_raises_nothing() -> None:
    assert validate(fields(**COST), ["Gross Living Area 1,740 sq.ft. 1,650 sq.ft."]) == []


@pytest.mark.parametrize(
    ("change", "rule", "named"),
    [
        (
            {"cost_approach__dwelling_cost_per_sqft": "155.00"},
            "cost_row_arithmetic",
            "cost_approach.dwelling_cost_per_sqft",
        ),
        (
            {"cost_approach__mechanicals_misc": "10,000"},
            "cost_new_sum",
            "cost_approach.mechanicals_misc",
        ),
        (
            {"cost_approach__depreciation": "(66,040)"},
            "cost_depreciated_arithmetic",
            "cost_approach.depreciation",
        ),
        (
            {
                "cost_approach__indicated_value": "344,160",
                "reconciliation__indicated_value_cost_approach": "344,160",
            },
            "cost_approach_total",
            "cost_approach.site_value",
        ),
        (
            {"reconciliation__indicated_value_cost_approach": "324,160"},
            "reconciliation_cost_mismatch",
            "cost_approach.indicated_value",
        ),
        (
            {"appraiser__appraised_value": "421,000"},
            "final_value_mismatch",
            "reconciliation.final_market_value",
        ),
        (
            {"appraiser__effective_date": "03/16/2026"},
            "effective_date_mismatch",
            "appraiser.effective_date",
        ),
        ({"subject__occupant": "Owner, Tenant"}, "checkbox_multiple_selected", "subject.occupant"),
    ],
)
def test_each_rule_fails_on_its_inconsistency(
    change: dict[str, str], rule: str, named: str
) -> None:
    issues = validate(fields(**{**COST, **change}), [])
    assert rules(issues) == [rule]
    assert named in issues[0].keys


def test_rules_skip_missing_figures_and_text_values() -> None:
    partial = {k: v for k, v in COST.items() if k not in ("cost_approach__site_value",)}
    assert validate(fields(**partial), []) == []  # cost_approach_total needs the site value
    assert (
        validate(
            fields(cost_approach__indicated_value="N/A", cost_approach__site_value="85,000"), []
        )
        == []
    )
    # Depreciation from its parts when the total box is empty; parentheses are a subtraction.
    parts = {k: v for k, v in COST.items() if k != "cost_approach__depreciation"}
    parts |= {
        "cost_approach__depreciation_physical": "(50,000)",
        "cost_approach__depreciation_external": "6,040",
    }
    assert validate(fields(**parts), []) == []
    # A multi checkbox may hold several options.
    assert validate(fields(improvements__foundation_type="Crawl Space, Full Basement"), []) == []


def test_client_style_planted_error() -> None:
    # SPEC example: 729,071 - 156,240 + 25,000 + 85,000 = 682,831, printed 692,831.
    planted = fields(
        cost_approach__total_cost_new="729,071",
        cost_approach__depreciation="=$( 156,240)",
        cost_approach__site_improvements_value="25,000",
        cost_approach__site_value="85,000",
        cost_approach__indicated_value="692,831",
        reconciliation__indicated_value_cost_approach="682,831",
    )
    issues = validate(planted, [])
    assert rules(issues) == ["cost_approach_total", "reconciliation_cost_mismatch"]
    total = next(i for i in issues if i.rule == "cost_approach_total")
    assert "cost_approach.indicated_value" in total.keys  # matches the truth's expected issue


def test_gla_mentions_and_rule() -> None:
    pages = [
        "Gross Living Area 1,740 sq.ft. 1,650 sq.ft.\n1,740 Square Feet of Gross Living Area",
        "GLA1 First Floor 1,200.00\nTotal Living Area (Rounded) 1,758\nGLA 12",
    ]
    assert gla_mentions(pages) == [(1, Decimal(1740)), (2, Decimal(1758))]
    [issue] = validate(fields(improvements__gla_sqft="1,740"), pages)
    assert issue.rule == "gla_vs_sketch" and issue.keys == ("improvements.gla_sqft",)
    assert validate(fields(improvements__gla_sqft="1,740"), ["Living Area 1,740.6"]) == []


# --------------------------------------------------------------------------- re-ask

PAGE_1 = [
    "Cost Approach",
    "Indicated Value by Cost Approach = $ 334,160",
    "Opinion of Site Value = $ 85,000",
]
PAGE_2 = [
    "Reconciliation",
    "Indicated Value by Cost Approach $ 324,160",
    "Final Opinion of Market Value $ 412,000",
]
PAGES = [PAGE_1, PAGE_2]


class Fixed:
    """A base system answering fixed fields (with regions)."""

    name = "fixed"

    def __init__(self, values: dict[str, ExtractedField]) -> None:
        self.values = values

    async def run(self, env: SystemEnv) -> SystemOutput:
        result = finish(
            doc_id=env.doc_id,
            method="fixed",
            page_count=2,
            fields=self.values,
            review_threshold=0.9,
        )
        return SystemOutput(result=result, regions={k: [] for k in self.values})


def reread(key: str, value: str, page: int, quote: str) -> GroundedValue:
    return GroundedValue(key=key, status="present", value=value, page=page, quote=quote)


def base_fields(recon: str) -> dict[str, ExtractedField]:
    return {
        "cost_approach.indicated_value": f("cost_approach.indicated_value", "334,160", 1),
        "cost_approach.site_value": f("cost_approach.site_value", "85,000", 1),
        "reconciliation.indicated_value_cost_approach": f(
            "reconciliation.indicated_value_cost_approach", recon, 2
        ),
        "reconciliation.final_market_value": f("reconciliation.final_market_value", "412,000", 2),
    }


def reask_env(provider: ScriptedProvider) -> SystemEnv:
    return make_env(layout=RawClient(raw_layout(PAGES)), provider=provider, pdf=pdf_bytes(PAGES))


def test_reask_touches_only_implicated_keys_and_a_misread_resolves() -> None:
    # The base misread the reconciliation figure as 344,160; the page prints 334,160, equal
    # to the cost section, so after the re-read the issue resolves.
    page_2 = ["Reconciliation", "Indicated Value by Cost Approach $ 334,160"]
    pages = [PAGE_1, page_2]
    provider = ScriptedProvider(
        [
            ScriptedTurn(
                parsed=GroundedAnswer(
                    fields=[
                        reread("cost_approach.indicated_value", "$ 334,160", 1, PAGE_1[1]),
                        reread(
                            "reconciliation.indicated_value_cost_approach",
                            "$ 334,160",
                            2,
                            page_2[1],
                        ),
                    ]
                )
            )
        ],
        model="claude-sonnet-5-5",
    )
    env = make_env(layout=RawClient(raw_layout(pages)), provider=provider, pdf=pdf_bytes(pages))
    system = ValidateReask(
        Fixed(base_fields("344,160")), ModelChoice("claude-sonnet-5-5", "low"), dpi=50
    )
    output = asyncio.run(system.run(env))
    [request] = provider.requests
    content = request.messages[0].content
    assert request.prompt_version == "appraisal_fields_reask/v1"
    assert "- cost_approach.indicated_value:" in content
    assert "- reconciliation.indicated_value_cost_approach:" in content
    assert "site_value" not in content and "final_market_value" not in content  # not implicated
    assert "Checks that failed: reconciliation_cost_mismatch." in content
    assert len(request.messages[0].images) == 2  # the pages the keys were read on
    reask = output.result.fallback["reask"]
    assert reask["changed"] == ["reconciliation.indicated_value_cost_approach"]  # 334,160 kept
    assert output.result.fields["reconciliation.indicated_value_cost_approach"].value_num == 334160
    assert output.origins["reconciliation.indicated_value_cost_approach"] == "llm_reask"
    assert output.origins["cost_approach.indicated_value"] == "azure"  # the same value: kept
    assert reask["issues_after"] == []
    assert all(i.rule != "reconciliation_cost_mismatch" for i in output.result.issues)


def test_reask_issue_that_survives_stays() -> None:
    provider = ScriptedProvider(
        [
            ScriptedTurn(
                parsed=GroundedAnswer(
                    fields=[
                        reread("cost_approach.indicated_value", "$ 334,160", 1, PAGE_1[1]),
                        reread(
                            "reconciliation.indicated_value_cost_approach",
                            "$ 324,160",
                            2,
                            PAGE_2[1],
                        ),
                    ]
                )
            )
        ],
        model="claude-sonnet-5-5",
    )
    system = ValidateReask(
        Fixed(base_fields("324,160")), ModelChoice("claude-sonnet-5-5", "low"), dpi=50
    )
    output = asyncio.run(system.run(reask_env(provider)))
    assert output.result.fallback["reask"]["changed"] == []
    found = [i.rule for i in output.result.issues if i.rule in VALIDATOR_RULES]
    assert found == ["reconciliation_cost_mismatch"]
    assert output.result.needs_review
    assert (
        "reconciliation_cost_mismatch:reconciliation.indicated_value_cost_approach,cost_approach.indicated_value"
        in output.result.review_reasons
    )


def test_reask_no_issue_no_call() -> None:
    provider = ScriptedProvider([], model="claude-sonnet-5-5")
    system = ValidateReask(
        Fixed(base_fields("334,160")), ModelChoice("claude-sonnet-5-5", "low"), dpi=50
    )
    output = asyncio.run(system.run(reask_env(provider)))
    assert provider.requests == []
    assert output.result.fallback["reask"]["asked"] == []


def test_implicated_keys_come_from_validator_issues_only() -> None:
    from basis.extraction.results import ValidationIssue

    issues = [
        ValidationIssue(rule="cost_approach_total", message="", keys=("cost_approach.site_value",)),
        ValidationIssue(rule="sources_disagree", message="", keys=("subject.zip",)),
        ValidationIssue(rule="range", message="", keys=("improvements.year_built",)),
    ]
    assert implicated(issues) == ["cost_approach.site_value"]


# --------------------------------------------------------------------------- vote


def output_of(values: dict[str, ExtractedField]) -> SystemOutput:
    result = finish(doc_id="d", method="m", page_count=1, fields=values, review_threshold=0.9)
    return SystemOutput(
        result=result, regions={k: [{"page": 1, "bbox": [0, 0, 1, 1]}] for k in values}
    )


def test_vote_rules() -> None:
    first = {
        "subject.occupant": f("subject.occupant", "Owner", source="llm", confidence=0.6),
        "subject.zip": f("subject.zip", "12345", source="llm", confidence=0.6),
        "subject.city": f("subject.city", "Springfield", source="llm", confidence=0.6),
    }
    second = {
        "subject.occupant": f("subject.occupant", "owner", source="llm", confidence=0.6),
        "subject.zip": f("subject.zip", "12346", source="llm", confidence=0.6),
        "subject.state": f("subject.state", "IL", source="llm", confidence=0.6),
    }
    merged, chosen, disagree, origins = vote_fields([output_of(first), output_of(second)])
    assert merged["subject.occupant"].confidence == 0.95  # agreement (case folded)
    assert merged["subject.zip"].value_text == "12345"  # the first voter's value
    assert merged["subject.zip"].confidence == 0.5
    assert merged["subject.city"].confidence == 0.5 and merged["subject.state"].value_text == "IL"
    assert disagree == ["subject.city", "subject.state", "subject.zip"]
    assert chosen["subject.state"] == 1 and origins["subject.state"] == "vote_disagree_2"
    assert origins["subject.occupant"] == "vote_agree"


def test_vote_system_reasons_and_parallel_voters() -> None:
    a = Fixed({"subject.zip": f("subject.zip", "12345")})
    b = Fixed({"subject.zip": f("subject.zip", "54321")})
    output = asyncio.run(Vote([a, b], ["a", "b"]).run(make_env()))
    assert "voters_disagree:subject.zip" in output.result.review_reasons
    assert output.result.needs_review
    assert output.result.method == "vote:a,b"
    with pytest.raises(ValueError, match="two voters"):
        Vote([a], ["a"])
