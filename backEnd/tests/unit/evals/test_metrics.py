"""Eval metrics against values computed by hand (shown in each test)."""

from __future__ import annotations

import math

import pytest

from evals.metrics import (
    FieldScore,
    citation_support_rate,
    classification_metrics,
    clustered_standard_error,
    field_accuracy,
    field_matches,
    ir_metrics,
    ndcg_at_k,
    pass_hat_k,
    recall_at_k,
    reciprocal_rank,
    summarize_proportion,
    wilson_interval,
)

# --------------------------------------------------------------------------- fields


@pytest.mark.parametrize(
    ("predicted", "expected", "kind", "result"),
    [
        ("1290 Example Way", "1290 Example Way", "text", (True, True)),
        ("1290  example way.", "1290 Example Way", "text", (False, True)),
        ("$4,321", 4321, "currency", (False, True)),
        (4321, 4321, "currency", (True, True)),
        (427000, 425000, "currency", (False, True)),  # 2000 / 425000 = 0.47% <= 0.5%
        (428000, 425000, "currency", (False, False)),  # 0.71% > 0.5%
        ("2027", 2026, "int", (False, True)),  # +/-1
        ("2028", 2026, "int", (False, False)),
        ("09/01/2026", "2026-09-01", "date", (False, True)),
        ("2026-09-02", "2026-09-01", "date", (False, False)),
        ("No", False, "bool", (False, True)),
        ("80%", 80, "percent", (False, True)),
        ("2.1", 2.1, "float", (True, True)),
        (None, "x", "text", (False, False)),
        ("n/a", 100, "currency", (False, False)),
    ],
)
def test_field_matches(
    predicted: object, expected: object, kind: str, result: tuple[bool, bool]
) -> None:
    assert field_matches(predicted, expected, kind) == result  # type: ignore[arg-type]


def test_tolerance_overrides() -> None:
    assert field_matches(105, 100, "currency", relative=0.05) == (False, True)
    assert field_matches(2030, 2026, "int", absolute=5) == (False, True)
    assert field_matches(2027, 2026, "int", absolute=0) == (False, False)


def test_field_accuracy_aggregates_per_field() -> None:
    scores = [
        FieldScore("a", True, True),
        FieldScore("a", False, True),
        FieldScore("b", False, False),
        FieldScore("b", False, True),
    ]
    result = field_accuracy(scores)
    assert (result.n, result.exact, result.normalized) == (4, 0.25, 0.75)
    assert result.per_field == {"a": (2, 1, 2), "b": (2, 0, 1)}
    assert math.isnan(field_accuracy([]).exact)


# --------------------------------------------------------------------------- classes


def test_wilson_interval_8_of_10() -> None:
    # p=0.8, z=1.96: center (0.8 + 0.19208)/1.38416 = 0.71674;
    # margin 1.96 x sqrt(0.016 + 0.009604) / 1.38416 = 0.22658
    low, high = wilson_interval(8, 10)
    assert low == pytest.approx(0.4902, abs=1e-4)
    assert high == pytest.approx(0.9433, abs=1e-4)


def test_wilson_interval_edges() -> None:
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and high == pytest.approx(0.2775, abs=1e-4)  # 3.8416/13.8416
    low, high = wilson_interval(10, 10)
    assert high == pytest.approx(1.0) and low == pytest.approx(0.7225, abs=1e-4)
    assert all(math.isnan(x) for x in wilson_interval(0, 0))
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_classification_metrics_by_hand() -> None:
    # a: tp1 fp1 fn1 -> P .5 R .5 F1 .5; b: tp2 fp1 fn0 -> P 2/3 R 1 F1 .8;
    # c: tp0 fp0 fn1 -> 0; macro F1 = 1.3 / 3; accuracy 3/5
    metrics = classification_metrics(["a", "a", "b", "b", "c"], ["a", "b", "b", "b", "a"])
    assert metrics.accuracy == 0.6
    assert metrics.per_class["a"].f1 == 0.5
    assert metrics.per_class["b"].precision == pytest.approx(2 / 3)
    assert metrics.per_class["b"].f1 == pytest.approx(0.8)
    assert (metrics.per_class["c"].precision, metrics.per_class["c"].support) == (0.0, 1)
    assert metrics.macro_f1 == pytest.approx(1.3 / 3)
    assert metrics.confusion["a"] == {"a": 1, "b": 1}
    assert metrics.accuracy_interval == wilson_interval(3, 5)


def test_abstention_is_scored_as_abstention() -> None:
    truth = ["x", "x", "y", "ambiguous"]
    predicted = ["x", "ambiguous", "y", "ambiguous"]
    metrics = classification_metrics(truth, predicted, abstain="ambiguous")
    assert metrics.accuracy == 0.75  # an abstention on a real class is not correct
    assert (metrics.abstentions, metrics.coverage) == (2, 0.5)
    assert metrics.selective_accuracy == 1.0
    assert set(metrics.per_class) == {"x", "y", "ambiguous"}  # true class kept
    only_real = classification_metrics(["x", "y"], ["x", "abstain"], abstain="abstain")
    assert set(only_real.per_class) == {"x", "y"}  # abstain is not a class here


def test_length_mismatch_is_refused() -> None:
    with pytest.raises(ValueError):
        classification_metrics(["a"], ["a", "b"])


# --------------------------------------------------------------------------- IR


def test_ndcg_by_hand() -> None:
    # qrels a:2 b:1; ranking b, x, a: DCG = 1/log2(2) + 0 + 2/log2(4) = 2
    # IDCG = 2/log2(2) + 1/log2(3) = 2.6309298; nDCG = 2 / 2.6309298 = 0.7601875
    qrels = {"a": 2, "b": 1}
    assert ndcg_at_k(["b", "x", "a"], qrels, 10) == pytest.approx(0.7601875, abs=1e-7)
    assert ndcg_at_k(["a", "b"], qrels, 10) == pytest.approx(1.0)
    assert ndcg_at_k(["b", "x", "a"], qrels, 2) == pytest.approx(1 / 2.6309298, abs=1e-7)
    assert ndcg_at_k(["x"], {"a": 0}, 10) == 0.0


def test_recall_and_reciprocal_rank() -> None:
    qrels = {"a": 2, "b": 1, "z": 0}
    assert recall_at_k(["b", "x", "a"], qrels, 2) == 0.5
    assert recall_at_k(["b", "x", "a"], qrels, 3) == 1.0
    assert reciprocal_rank(["x", "y", "a"], qrels) == pytest.approx(1 / 3)
    assert reciprocal_rank(["z", "x"], qrels) == 0.0  # grade 0 is not relevant


def test_ir_metrics_means_and_missing_queries() -> None:
    qrels = {"q1": {"a": 1}, "q2": {"b": 1}}
    metrics = ir_metrics({"q1": ["a"], "q3": ["b"]}, qrels)
    assert metrics.n == 2
    assert (metrics.mrr, metrics.ndcg_10, metrics.recall_5) == (0.5, 0.5, 0.5)
    assert metrics.per_query["q2"]["mrr"] == 0.0


# --------------------------------------------------------------------------- citations


def test_citation_support_rate() -> None:
    assert citation_support_rate([(2, 2), (3, 1), (0, 0)]) == (0.6, 5)
    rate, claimed = citation_support_rate([])
    assert math.isnan(rate) and claimed == 0
    with pytest.raises(ValueError):
        citation_support_rate([(1, 2)])


# --------------------------------------------------------------------------- variance


def test_clustered_standard_error_by_hand() -> None:
    # mean 0.75; cluster sums of residuals: A = 0.25 - 0.75 = -0.5, B = 0.25 + 0.25 = 0.5
    # var = 2/1 x (0.25 + 0.25) / 16 = 0.0625 -> SE 0.25
    assert clustered_standard_error([1, 0, 1, 1], ["A", "A", "B", "B"]) == pytest.approx(0.25)


def test_clustered_se_with_singleton_clusters_is_the_iid_se() -> None:
    values = [1.0, 0.0, 1.0, 1.0, 0.0]
    n = len(values)
    m = sum(values) / n
    sample_sd = math.sqrt(sum((x - m) ** 2 for x in values) / (n - 1))
    assert clustered_standard_error(values, range(n)) == pytest.approx(sample_sd / math.sqrt(n))


def test_clustered_se_needs_two_clusters() -> None:
    assert math.isnan(clustered_standard_error([1, 0], ["A", "A"]))
    with pytest.raises(ValueError):
        clustered_standard_error([1], ["A", "B"])


def test_pass_hat_k_by_hand() -> None:
    # item 1: 2 of 3 succeed -> C(2,2)/C(3,2) = 1/3; item 2: 3 of 3 -> 1; mean 2/3
    outcomes = [[True, True, False], [True, True, True]]
    assert pass_hat_k(outcomes, 2) == pytest.approx(2 / 3)
    assert pass_hat_k(outcomes, 1) == pytest.approx((2 / 3 + 1) / 2)
    assert pass_hat_k(outcomes, 3) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        pass_hat_k(outcomes, 4)
    with pytest.raises(ValueError):
        pass_hat_k(outcomes, 0)


def test_summarize_proportion() -> None:
    summary = summarize_proportion([True, False, True, True], ["A", "A", "B", "B"])
    assert summary["value"] == 0.75 and summary["n"] == 4
    assert summary["stderr"] == pytest.approx(0.25)
    assert summary["interval"] == list(wilson_interval(3, 4))
