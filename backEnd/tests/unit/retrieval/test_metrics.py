"""IR metrics against hand-computed values (the arithmetic is written out in each test).

The ``trec_eval`` values were also cross-checked once against pytrec_eval
(``ndcg_cut_5``, ``recall_5``, ``recip_rank``) when the module was written.
"""

from __future__ import annotations

import math
import uuid

import pytest

from basis.retrieval.metrics import (
    dcg,
    dedupe,
    mrr,
    ndcg_at_k,
    page_keys,
    recall_at_k,
    reciprocal_rank,
)
from basis.retrieval.types import Hit

RANKED = ["d1", "d2", "d3", "d4", "d5"]
QRELS = {"d1": 2, "d2": 0, "d3": 1, "d6": 2}  # d6 is relevant but never retrieved


def test_ndcg_trec_eval_form() -> None:
    # gains [2, 0, 1, 0, 0]; DCG = 2/log2(2) + 1/log2(4) = 2.5
    # ideal [2, 2, 1]: IDCG = 2/log2(2) + 2/log2(3) + 1/log2(4)
    idcg = 2 + 2 / math.log2(3) + 0.5
    assert ndcg_at_k(RANKED, QRELS, 5) == pytest.approx(2.5 / idcg)
    assert ndcg_at_k(RANKED, QRELS, 5) == pytest.approx(0.6645649, abs=1e-6)
    assert ndcg_at_k(RANKED, QRELS, 1) == pytest.approx(1.0)  # 2/1 over 2/1
    # @2: DCG = 2; IDCG = 2 + 2/log2(3)
    assert ndcg_at_k(RANKED, QRELS, 2) == pytest.approx(2 / (2 + 2 / math.log2(3)))


def test_ndcg_jarvelin_kekalainen_2002_form() -> None:
    # b = 2: ranks 1 and 2 undiscounted, rank 3 divided by log2(3).
    dcg_value = 2 + 0 + 1 / math.log2(3)
    idcg = 2 + 2 + 1 / math.log2(3)
    assert ndcg_at_k(RANKED, QRELS, 5, variant="jk2002") == pytest.approx(dcg_value / idcg)
    assert dcg([3, 2, 3], 3, "jk2002") == pytest.approx(3 + 2 + 3 / math.log2(3))
    assert dcg([3, 2, 3], 3, "trec_eval") == pytest.approx(3 + 2 / math.log2(3) + 3 / 2)


def test_perfect_and_empty_rankings() -> None:
    assert ndcg_at_k(["d1", "d6", "d3"], QRELS, 10) == pytest.approx(1.0)
    assert ndcg_at_k([], QRELS, 10) == 0.0
    assert recall_at_k([], QRELS, 10) == 0.0
    assert reciprocal_rank([], QRELS) == 0.0


def test_recall() -> None:
    assert recall_at_k(RANKED, QRELS, 5) == pytest.approx(2 / 3)  # d1, d3 of d1, d3, d6
    assert recall_at_k(RANKED, QRELS, 1) == pytest.approx(1 / 3)
    assert recall_at_k(RANKED, QRELS, 5, min_grade=2) == pytest.approx(1 / 2)  # d1 of d1, d6


def test_reciprocal_rank_and_mrr() -> None:
    assert reciprocal_rank(RANKED, QRELS) == 1.0
    assert reciprocal_rank(["x", "d3"], QRELS) == 0.5
    assert reciprocal_rank(["x", "d3"], QRELS, min_grade=2) == 0.0
    assert mrr([(RANKED, QRELS), (["x", "d3"], QRELS), (["x"], QRELS)]) == pytest.approx(0.5)


def test_duplicates_count_once() -> None:
    assert dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]
    assert recall_at_k(["d1", "d1", "d1", "d3"], QRELS, 2) == pytest.approx(2 / 3)
    assert reciprocal_rank(["x", "x", "d1"], QRELS) == 0.5


def test_queries_without_relevant_items_are_refused() -> None:
    for call in (
        lambda: ndcg_at_k(RANKED, {"d1": 0}, 5),
        lambda: recall_at_k(RANKED, {}, 5),
        lambda: reciprocal_rank(RANKED, {"d2": 0}),
        lambda: mrr([]),
        lambda: ndcg_at_k(RANKED, QRELS, 0),
    ):
        with pytest.raises(ValueError):
            call()


def test_page_keys_expand_hits_in_rank_order() -> None:
    def hit(doc: str, start: int | None, end: int | None) -> Hit:
        return Hit(uuid.uuid4(), doc, start, end, 1.0, "t")

    hits = [hit("A", 2, 2), hit("B", 1, 2), hit("A", 2, 2), hit("C", None, None)]
    assert page_keys(hits) == ["A:2", "B:1", "B:2"]
