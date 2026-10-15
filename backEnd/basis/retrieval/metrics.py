"""Information-retrieval metrics from their definitions (BUILD_PLAN 13.2).

Small, owned functions so the eval harness (S3-C) and the retrieval tests share one
implementation. Inputs: a ranked list of item ids (best first) and graded judgments
``qrels: {item id: grade}`` with grade 0 meaning "judged not relevant". Items are opaque
strings; the retrieval tests use ``"{doc_id}:{page}"`` (:func:`page_keys`).

* nDCG@k, Järvelin and Kekäläinen (ACM TOIS 20(4), 2002), with linear gain (the grade).
  ``variant="trec_eval"`` (default) discounts rank ``i`` (1-based) by ``log2(i + 1)``,
  as trec_eval / pytrec_eval ``ndcg_cut`` do, so these numbers match the harness tool.
  ``variant="jk2002"`` is the paper's original form with log base b = 2: ranks
  ``i < b`` are not discounted and rank ``i >= b`` is divided by ``log2(i)`` (ranks 1
  and 2 both count fully). The ideal DCG sorts **all** judged grades, retrieved or not.
* recall@k: relevant items in the top ``k`` over all relevant items.
* reciprocal rank: ``1 / rank`` of the first relevant item (0 when none is retrieved);
  MRR is its mean over queries.

A ranked list is de-duplicated (first occurrence kept) before scoring, as trec_eval
assumes unique documents. A query with no relevant item has no defined nDCG, recall or
reciprocal rank; these functions raise ``ValueError`` so the caller excludes it
explicitly instead of averaging in a silent 0 (trec_eval also skips such queries).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from basis.retrieval.types import Hit

NdcgVariant = Literal["trec_eval", "jk2002"]


def dedupe(ranked: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in ranked:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def page_keys(hits: Iterable[Hit]) -> list[str]:
    """``"{doc_id}:{page}"`` for every page each hit covers, in rank order, de-duplicated."""
    return dedupe(f"{hit.doc_id}:{page}" for hit in hits for page in hit.pages())


def _relevant(qrels: Mapping[str, int], min_grade: int) -> set[str]:
    relevant = {item for item, grade in qrels.items() if grade >= min_grade}
    if not relevant:
        raise ValueError("the query has no relevant items; exclude it from the average")
    return relevant


def _discount(rank: int, variant: NdcgVariant) -> float:
    if variant == "trec_eval":
        return math.log2(rank + 1)
    if variant == "jk2002":
        return 1.0 if rank < 2 else math.log2(rank)
    raise ValueError(f"unknown nDCG variant {variant!r}")


def dcg(gains: Sequence[float], k: int, variant: NdcgVariant = "trec_eval") -> float:
    return sum(g / _discount(i, variant) for i, g in enumerate(gains[:k], start=1))


def ndcg_at_k(
    ranked: Sequence[str],
    qrels: Mapping[str, int],
    k: int,
    *,
    variant: NdcgVariant = "trec_eval",
) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    _relevant(qrels, 1)
    gains = [float(max(qrels.get(item, 0), 0)) for item in dedupe(ranked)]
    ideal = sorted((float(g) for g in qrels.values() if g > 0), reverse=True)
    return dcg(gains, k, variant) / dcg(ideal, k, variant)


def recall_at_k(
    ranked: Sequence[str], qrels: Mapping[str, int], k: int, *, min_grade: int = 1
) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    relevant = _relevant(qrels, min_grade)
    return len(relevant.intersection(dedupe(ranked)[:k])) / len(relevant)


def reciprocal_rank(
    ranked: Sequence[str], qrels: Mapping[str, int], *, min_grade: int = 1
) -> float:
    relevant = _relevant(qrels, min_grade)
    for rank, item in enumerate(dedupe(ranked), start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def mrr(runs: Iterable[tuple[Sequence[str], Mapping[str, int]]], *, min_grade: int = 1) -> float:
    """Mean reciprocal rank over ``(ranked, qrels)`` pairs."""
    values = [reciprocal_rank(ranked, qrels, min_grade=min_grade) for ranked, qrels in runs]
    if not values:
        raise ValueError("no queries")
    return sum(values) / len(values)
