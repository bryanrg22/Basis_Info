"""BM25 index and the per-process registry (BUILD_PLAN 6.1 ``test_bm25.py``)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence

import pytest

from basis.retrieval.bm25 import Bm25Doc, Bm25Index, Bm25Registry

TEXTS = {
    "a": "Section 168(e)(3) groups property into recovery classes by class life.",
    "b": "Asset class 57.0 covers distributive trades and services.",
    "c": "Asset class 00.11 covers office furniture, fixtures and equipment.",
    "d": "Section 1245 property generally means depreciable personal property.",
    "e": "Residential rental property is recovered over 27.5 years.",
}
IDS = {key: uuid.uuid5(uuid.NAMESPACE_URL, key) for key in TEXTS}
NAMES = {v: k for k, v in IDS.items()}


def index(texts: dict[str, str] = TEXTS) -> Bm25Index:
    return Bm25Index(
        Bm25Doc(IDS.get(k, uuid.uuid5(uuid.NAMESPACE_URL, k)), t) for k, t in texts.items()
    )


def top(idx: Bm25Index, query: str, k: int = 5) -> list[str]:
    return [NAMES[cid] for cid, _ in idx.search(query, k)]


@pytest.mark.parametrize(
    ("query", "expected"),
    [("168(e)(3)", "a"), ("57.0", "b"), ("00.11 office", "c"), ("§1245", "d"), ("27.5", "e")],
)
def test_exact_code_query_ranks_its_chunk_first(query: str, expected: str) -> None:
    assert top(index(), query)[0] == expected


def test_scores_are_positive_and_descending() -> None:
    results = index().search("asset class property", 5)
    scores = [s for _, s in results]
    assert scores and all(s > 0 for s in scores) and scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("query", ["", "   ", "!!", "zzzz unknownword"])
def test_empty_or_unmatched_query_returns_nothing(query: str) -> None:
    assert index().search(query, 5) == []


def test_k_limits_and_zero_k() -> None:
    assert len(index().search("property", 2)) == 2
    assert index().search("property", 0) == []


def test_empty_index_and_tokenless_docs() -> None:
    assert len(Bm25Index([])) == 0 and Bm25Index([]).search("anything", 3) == []
    idx = index({"x": "-- . --", "a": TEXTS["a"]})
    assert len(idx) == 1  # the chunk without tokens is not indexed
    assert top(idx, "recovery") == ["a"]


def test_ties_keep_the_given_order() -> None:
    twins = {f"t{i}": "identical text about carpet" for i in range(4)}
    ids = [uuid.uuid4() for _ in twins]
    idx = Bm25Index(
        [Bm25Doc(i, t) for i, t in zip(ids, twins.values(), strict=True)]
        + [Bm25Doc(uuid.uuid4(), "other words entirely")] * 4
    )
    assert [cid for cid, _ in idx.search("carpet", 4)] == ids


# --------------------------------------------------------------------------- registry


class CountingLoader:
    def __init__(self, delay: float = 0.0, fail: bool = False) -> None:
        self.calls: list[uuid.UUID] = []
        self.delay = delay
        self.fail = fail

    async def __call__(self, version_id: uuid.UUID) -> Sequence[Bm25Doc]:
        self.calls.append(version_id)
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("database down")
        return [Bm25Doc(IDS[k], t) for k, t in TEXTS.items()]


async def test_registry_builds_once_then_hits() -> None:
    registry, loader, version = Bm25Registry(), CountingLoader(), uuid.uuid4()
    first = await registry.get(version, loader)
    second = await registry.get(version, loader)
    assert first is second and loader.calls == [version]
    assert (registry.stats.misses, registry.stats.hits, registry.stats.builds) == (1, 1, 1)
    assert registry.stats.build_seconds[version] >= 0


async def test_concurrent_misses_build_once() -> None:
    registry, loader, version = Bm25Registry(), CountingLoader(delay=0.05), uuid.uuid4()
    results = await asyncio.gather(*(registry.get(version, loader) for _ in range(5)))
    assert len({id(r) for r in results}) == 1
    assert loader.calls == [version] and registry.stats.builds == 1


async def test_warm_and_invalidate() -> None:
    registry, loader = Bm25Registry(), CountingLoader()
    v1, v2 = uuid.uuid4(), uuid.uuid4()
    await registry.warm([v1, v2], loader)
    assert v1 in registry and v2 in registry and registry.stats.builds == 2
    registry.invalidate(v1)
    assert v1 not in registry and v2 in registry
    await registry.get(v1, loader)
    assert registry.stats.builds == 3
    registry.invalidate()
    assert len(registry) == 0


async def test_lru_eviction() -> None:
    registry, loader = Bm25Registry(max_entries=2), CountingLoader()
    v1, v2, v3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await registry.get(v1, loader)
    await registry.get(v2, loader)
    await registry.get(v1, loader)  # v1 is now most recent
    await registry.get(v3, loader)  # evicts v2
    assert v1 in registry and v3 in registry and v2 not in registry
    assert registry.stats.evictions == 1
    with pytest.raises(ValueError):
        Bm25Registry(max_entries=0)


async def test_failed_build_is_not_cached(caplog: pytest.LogCaptureFixture) -> None:
    registry, version = Bm25Registry(), uuid.uuid4()
    with pytest.raises(RuntimeError):
        await registry.get(version, CountingLoader(fail=True))
    assert version not in registry and registry.stats.builds == 0
    caplog.set_level(logging.INFO, logger="basis.retrieval.bm25")
    await registry.get(version, CountingLoader())
    assert registry.stats.builds == 1
    [record] = [r for r in caplog.records if r.getMessage() == "bm25 index built"]
    assert record.corpus_version_id == str(version)  # type: ignore[attr-defined]
    assert record.chunks == len(TEXTS)  # type: ignore[attr-defined]


@pytest.mark.regression
def test_common_terms_in_small_corpora_still_score() -> None:
    """BM25Okapi's IDF is <= 0 for a term in half the chunks or more, so a one-chunk
    study corpus returned nothing and a term in 2 of 4 chunks scored 0 (legacy bug)."""
    single = Bm25Index([Bm25Doc(IDS["a"], TEXTS["a"])])
    assert [cid for cid, _ in single.search("recovery classes", 3)] == [IDS["a"]]
    # "property" is in a and d: 2 of 4 chunks.
    half = Bm25Index([Bm25Doc(IDS[k], t) for k, t in TEXTS.items() if k in "abcd"])
    assert {NAMES[cid] for cid, _ in half.search("property", 4)} == {"a", "d"}
