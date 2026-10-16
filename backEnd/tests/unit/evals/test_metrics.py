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
    inventory_metrics,
    ir_metrics,
    ndcg_at_k,
    page_citation_status,
    paired_bootstrap,
    pass_hat_k,
    recall_at_k,
    reciprocal_rank,
    room_inventory_score,
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
    with pytest.raises(ValueError):  # no relevant item: undefined, the caller excludes it
        ndcg_at_k(["x"], {"a": 0}, 10)


def test_recall_and_reciprocal_rank() -> None:
    qrels = {"a": 2, "b": 1, "z": 0}
    assert recall_at_k(["b", "x", "a"], qrels, 2) == 0.5
    assert recall_at_k(["b", "x", "a"], qrels, 3) == 1.0
    assert reciprocal_rank(["x", "y", "a"], qrels) == pytest.approx(1 / 3)
    assert reciprocal_rank(["z", "x"], qrels) == 0.0  # grade 0 is not relevant


def test_ir_metrics_means_and_missing_queries() -> None:
    qrels = {"q1": {"a": 1}, "q2": {"b": 1}, "q4": {"c": 0}}
    metrics = ir_metrics({"q1": ["a"], "q3": ["b"], "q4": ["c"]}, qrels)
    assert metrics.n == 2  # q2 is missing from the run (scores 0); q4 has nothing relevant
    assert metrics.excluded == ("q4",)
    assert (metrics.mrr, metrics.ndcg_10, metrics.recall_5) == (0.5, 0.5, 0.5)
    assert metrics.per_query["q2"]["mrr"] == 0.0
    assert "q4" not in metrics.per_query


# --------------------------------------------------------------------------- citations


def test_citation_support_rate() -> None:
    assert citation_support_rate([(2, 2), (3, 1), (0, 0)]) == (0.6, 5)
    rate, claimed = citation_support_rate([])
    assert math.isnan(rate) and claimed == 0
    with pytest.raises(ValueError):
        citation_support_rate([(1, 2)])


PAGES = ["Cover page.", "Carpeting will be treated as not permanently attached.", "Third."]


@pytest.mark.parametrize(
    ("excerpt", "page", "pages", "status"),
    [
        ("carpeting will be treated as not permanently attached", 2, PAGES, "resolved"),
        # typographic dash, case and line break fold away (basis.retrieval.verify)
        ("CARPETING  will be\ntreated", 2, PAGES, "resolved"),
        ("Carpeting will be treated", 1, PAGES, "excerpt_not_on_page"),  # wrong page
        ("Carpeting will be treated", 3, PAGES, "excerpt_not_on_page"),
        ("Carpeting will be treated", 4, PAGES, "page_out_of_range"),  # 3-page document
        ("Carpeting will be treated", 0, PAGES, "page_out_of_range"),
        ("Carpeting will be treated", 415, PAGES, "page_out_of_range"),  # legacy ATG pages
        ("Carpeting will be treated", 2, None, "document_missing"),
        ("Carpeting", 2, PAGES, "excerpt_too_short"),  # 9 < MIN_EXCERPT_CHARS (12)
        ("Carpeting - 5-year recovery", 2, PAGES, "excerpt_not_on_page"),  # a paraphrase
    ],
)
def test_page_citation_status(
    excerpt: str, page: int, pages: list[str] | None, status: str
) -> None:
    assert page_citation_status(excerpt, page, pages) == status


def test_page_citation_status_folds_typographic_punctuation() -> None:
    pages = ["Asset Class 57.0 \u2013 Distributive Trades applies to \u201cmost\u201d property."]
    assert page_citation_status('Class 57.0 - Distributive Trades applies to "most"', 1, pages) == (
        "resolved"
    )


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


# --------------------------------------------------------------------------- inventories


def test_inventory_metrics_by_hand() -> None:
    gold = {"r1": {"sink": 2, "countertop": 1}, "r2": {"toilet": 1}}
    predicted = {"r1": {"sink": 3, "countertop": 1, "stove": 1}, "r2": {}, "r9": {"sink": 1}}
    m = inventory_metrics(gold, predicted)
    # pairs (label, gold, predicted): r1 countertop (1, 1), sink (2, 3), stove (0, 1);
    # r2 toilet (1, 0). r9 is not a gold room and is ignored.
    assert m.per_room["r1"].pairs == (("countertop", 1, 1), ("sink", 2, 3), ("stove", 0, 1))
    assert (m.rooms, m.pairs) == (2, 4)
    assert m.count_mae == pytest.approx(3 / 4)  # |0| + |1| + |1| + |-1|
    assert m.exact_count_rate == pytest.approx(1 / 4)
    assert m.signed_bias == pytest.approx(1 / 4)  # (0 + 1 + 1 - 1) / 4: over-counting
    # Clustered by room. MAE values [0, 1, 1 | 1], mean 0.75: residual sums r1 -0.25,
    # r2 0.25; var = 2/1 x (0.0625 + 0.0625) / 16 = 0.015625 -> SE 0.125.
    assert m.count_mae_se == pytest.approx(0.125)
    # exact [1, 0, 0 | 0], mean 0.25: sums r1 0.25, r2 -0.25 -> same SE 0.125.
    assert m.exact_count_se == pytest.approx(0.125)
    # signed [0, 1, 1 | -1], mean 0.25: sums r1 1.25, r2 -1.25;
    # var = 2 x (1.5625 x 2) / 16 = 0.390625 -> SE 0.625.
    assert m.signed_bias_se == pytest.approx(0.625)
    assert m.room_exact_rate == 0.0
    # r1: predicted {sink, countertop, stove} vs gold {sink, countertop}: P 2/3, R 1,
    # F1 4/5; r2: nothing predicted: P 0, R 0, F1 0. Macro over rooms.
    assert (m.per_room["r1"].precision, m.per_room["r1"].recall) == (pytest.approx(2 / 3), 1.0)
    assert m.per_room["r1"].f1 == pytest.approx(0.8)
    assert m.label_precision == pytest.approx(1 / 3)
    assert m.label_recall == pytest.approx(1 / 2)
    assert m.label_f1 == pytest.approx(0.4)


def test_room_inventory_score_edges() -> None:
    empty = room_inventory_score({}, {})
    assert (empty.pairs, empty.exact, empty.precision, empty.recall, empty.f1) == (
        (),
        True,
        1.0,
        1.0,
        1.0,
    )
    # A zero count is absence; an exact inventory scores 1 everywhere.
    same = room_inventory_score({"sink": 1}, {"sink": 1, "stove": 0})
    assert (same.pairs, same.exact, same.f1) == ((("sink", 1, 1),), True, 1.0)
    # Right labels, wrong count: label sets agree, the inventory does not.
    count = room_inventory_score({"sink": 1}, {"sink": 2})
    assert (count.exact, count.precision, count.recall) == (False, 1.0, 1.0)
    # Something predicted in a room with no gold objects.
    spurious = room_inventory_score({}, {"sink": 1})
    assert (spurious.exact, spurious.precision, spurious.recall, spurious.f1) == (
        False,
        0.0,
        0.0,
        0.0,
    )
    with pytest.raises(ValueError, match="negative"):
        room_inventory_score({"sink": 1}, {"sink": -1})


def test_inventory_metrics_with_no_pairs() -> None:
    m = inventory_metrics({"r1": {}}, {})
    assert m.pairs == 0 and math.isnan(m.count_mae) and math.isnan(m.count_mae_se)
    assert m.room_exact_rate == 1.0


# --------------------------------------------------------------------------- paired bootstrap


def test_paired_bootstrap_identical_systems_have_no_difference() -> None:
    values = [0.1, 0.5, 0.9, 0.3]
    diff = paired_bootstrap(values, values, iterations=1000)
    assert (diff.mean, diff.low, diff.high) == (0.0, 0.0, 0.0)
    assert diff.p_value == 1.0 and (diff.wins, diff.losses, diff.ties) == (0, 0, 4)


def test_paired_bootstrap_constant_shift_is_exact_and_significant() -> None:
    a = [x + 0.25 for x in range(30)]
    diff = paired_bootstrap(a, [float(x) for x in range(30)], iterations=2000)
    assert diff.mean == pytest.approx(0.25)
    assert diff.low == pytest.approx(0.25) and diff.high == pytest.approx(0.25)
    # Only the all-positive sign flip reaches |mean| = 0.25 (chance 2^-30): p = 1/(N+1).
    assert diff.p_value < 0.01 and diff.wins == 30


def test_paired_bootstrap_interval_matches_the_normal_approximation() -> None:
    import random

    rng = random.Random(7)
    b = [rng.random() for _ in range(200)]
    a = [x + rng.gauss(0.05, 0.2) for x in b]
    diff = paired_bootstrap(a, b, iterations=20_000)
    d = [x - y for x, y in zip(a, b, strict=True)]
    mean = sum(d) / len(d)
    se = math.sqrt(sum((x - mean) ** 2 for x in d) / (len(d) - 1) / len(d))
    assert diff.mean == pytest.approx(mean)
    assert diff.low == pytest.approx(mean - 1.96 * se, abs=0.25 * se)
    assert diff.high == pytest.approx(mean + 1.96 * se, abs=0.25 * se)
    assert diff.p_value < 0.05  # the shift is about 3.5 standard errors


def test_paired_bootstrap_is_seeded_and_swapping_systems_flips_the_sign() -> None:
    a, b = [0.2, 0.9, 0.4, 0.4, 1.0], [0.1, 0.3, 0.6, 0.4, 0.5]
    first, again = paired_bootstrap(a, b, iterations=500), paired_bootstrap(a, b, iterations=500)
    assert first == again
    flipped = paired_bootstrap(b, a, iterations=500)
    assert flipped.mean == pytest.approx(-first.mean)
    assert (flipped.wins, flipped.losses, flipped.ties) == (first.losses, first.wins, 1)


def test_paired_bootstrap_refuses_bad_input() -> None:
    with pytest.raises(ValueError):
        paired_bootstrap([1.0], [1.0, 2.0])
    with pytest.raises(ValueError):
        paired_bootstrap([], [])
    with pytest.raises(ValueError):
        paired_bootstrap([1.0], [0.0], iterations=0)
