"""Fusion (BUILD_PLAN 6.1): weight 1.0 is the BM25 order, 0.0 the vector order, RRF values."""

from __future__ import annotations

import math
import uuid

import pytest

from basis.retrieval.hybrid import fuse
from basis.retrieval.types import Hit

ID = {name: uuid.uuid5(uuid.NAMESPACE_DNS, name) for name in "abcdefg"}
NAME = {v: k for k, v in ID.items()}


def hits(*pairs: tuple[str, float]) -> list[Hit]:
    return [Hit(ID[n], "DOC", 1, 1, score, f"text {n}") for n, score in pairs]


def names(result: list[Hit]) -> list[str]:
    return [NAME[h.chunk_id] for h in result]


BM25 = hits(("a", 9.0), ("b", 6.0), ("c", 6.0), ("d", 1.5))
VEC = hits(("e", 0.9), ("c", 0.8), ("a", 0.7), ("f", 0.7))


def test_weight_one_reproduces_bm25_order_including_ties() -> None:
    result = fuse(BM25, VEC, "weighted", weight=1.0)
    assert names(result)[: len(BM25)] == ["a", "b", "c", "d"]
    assert names(result)[len(BM25) :] == ["e", "f"]  # vector-only hits, in vector order
    assert [h.score for h in result[len(BM25) :]] == [0.0, 0.0]


def test_weight_zero_reproduces_vector_order_including_ties() -> None:
    result = fuse(BM25, VEC, "weighted", weight=0.0)
    assert names(result)[: len(VEC)] == ["e", "c", "a", "f"]
    assert names(result)[len(VEC) :] == ["b", "d"]


def test_weighted_values_are_max_normalized_sums() -> None:
    result = {NAME[h.chunk_id]: h.score for h in fuse(BM25, VEC, "weighted", weight=0.5)}
    assert result["a"] == pytest.approx(0.5 * 9 / 9 + 0.5 * 0.7 / 0.9)
    assert result["c"] == pytest.approx(0.5 * 6 / 9 + 0.5 * 0.8 / 0.9)
    assert result["e"] == pytest.approx(0.5 * 0.9 / 0.9)
    assert result["d"] == pytest.approx(0.5 * 1.5 / 9)
    ordered = fuse(BM25, VEC, "weighted", weight=0.5)
    assert [h.score for h in ordered] == sorted((h.score for h in ordered), reverse=True)


def test_weighted_with_non_positive_maximum_is_zero() -> None:
    result = fuse(hits(("a", 0.0)), hits(("b", -0.2), ("c", -0.5)), "weighted", weight=0.5)
    assert [h.score for h in result] == [0.0, 0.0, 0.0]
    assert names(result) == ["a", "b", "c"]  # ties: BM25 rank, then vector rank


def test_rrf_reference_values() -> None:
    # Cormack et al. 2009: score(d) = sum over lists of 1 / (60 + rank), rank from 1.
    bm25 = hits(("a", 3.0), ("b", 2.0), ("c", 1.0))
    vec = hits(("c", 0.9), ("a", 0.8), ("d", 0.7))
    result = fuse(bm25, vec, "rrf")
    expected = {
        "a": 1 / 61 + 1 / 62,
        "b": 1 / 62,
        "c": 1 / 63 + 1 / 61,
        "d": 1 / 63,
    }
    assert names(result) == ["a", "c", "b", "d"]
    for hit in result:
        assert math.isclose(hit.score, expected[NAME[hit.chunk_id]], rel_tol=1e-12)


def test_rrf_k_parameter() -> None:
    [only] = fuse(hits(("a", 1.0)), [], "rrf", k=1)
    assert only.score == pytest.approx(1 / 2)


def test_rrf_ties_break_by_the_favoured_list() -> None:
    bm25 = hits(("a", 2.0), ("b", 1.0))
    vec = hits(("b", 0.9), ("a", 0.8))
    tied = fuse(bm25, vec, "rrf")
    assert tied[0].score == tied[1].score
    assert names(tied) == ["a", "b"]  # weight 0.5: BM25 rank decides
    assert names(fuse(bm25, vec, "rrf", weight=0.25)) == ["b", "a"]  # vector rank decides


def test_duplicates_keep_their_first_position_and_text_comes_from_a_list() -> None:
    bm25 = hits(("a", 5.0), ("b", 4.0), ("a", 3.0))
    result = fuse(bm25, hits(("c", 0.5)), "rrf")
    # a: 1/61 (first position only), c: 1/61 (tie, a wins on BM25 rank), b: 1/62.
    assert names(result) == ["a", "c", "b"]
    assert result[0].score == pytest.approx(1 / 61)
    assert all(h.text == f"text {NAME[h.chunk_id]}" for h in result)


def test_limit_and_empty_inputs() -> None:
    assert len(fuse(BM25, VEC, "rrf", limit=3)) == 3
    assert fuse([], [], "rrf") == [] and fuse([], [], "weighted") == []
    assert names(fuse([], VEC, "weighted", weight=0.5)) == ["e", "c", "a", "f"]


def test_deterministic_across_calls() -> None:
    for method in ("rrf", "weighted"):
        runs = {tuple(names(fuse(BM25, VEC, method))) for _ in range(5)}  # type: ignore[arg-type]
        assert len(runs) == 1


@pytest.mark.parametrize("weight", [-0.1, 1.1, float("nan")])
def test_invalid_weight(weight: float) -> None:
    with pytest.raises(ValueError):
        fuse(BM25, VEC, "weighted", weight=weight)


def test_invalid_method_and_k() -> None:
    with pytest.raises(ValueError):
        fuse(BM25, VEC, "max")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        fuse(BM25, VEC, "rrf", k=0)
