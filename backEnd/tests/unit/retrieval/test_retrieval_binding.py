"""The process binding of retrieval: the hybrid fusion comes from settings (EVAL_REPORT 22)."""

from __future__ import annotations

import asyncio
import inspect
import uuid

import pytest
from pydantic import ValidationError

from basis.config import Settings
from basis.retrieval import Retrieval, default_retrieval
from basis.retrieval.bm25 import Bm25Registry
from basis.retrieval.embed import HashEmbedder
from basis.retrieval.hybrid import CANDIDATE_FACTOR, fuse, hybrid_search
from basis.retrieval.types import Hit


def make_settings(**values: object) -> Settings:
    return Settings(BASIS_ENV="test", EMBEDDING_MODEL="hash", **values)  # type: ignore[arg-type]


def test_defaults_are_rrf_and_match_the_dataclass_and_hybrid_search() -> None:
    settings = make_settings()
    assert (settings.retrieval_fusion, settings.retrieval_bm25_weight) == ("rrf", 0.5)
    bare = Retrieval(embedder=HashEmbedder(), registry=Bm25Registry())
    assert (bare.fusion, bare.bm25_weight) == (
        settings.retrieval_fusion,
        settings.retrieval_bm25_weight,
    )
    defaults = inspect.signature(hybrid_search).parameters
    assert (defaults["method"].default, defaults["weight"].default) == (
        settings.retrieval_fusion,
        settings.retrieval_bm25_weight,
    )


def test_default_retrieval_takes_the_fusion_from_settings() -> None:
    bound = default_retrieval(make_settings(retrieval_fusion="rrf", retrieval_bm25_weight=0.8))
    assert (bound.fusion, bound.bm25_weight) == ("rrf", 0.8)


@pytest.mark.parametrize(
    "values",
    [{"retrieval_fusion": "max"}, {"retrieval_bm25_weight": 1.5}, {"retrieval_bm25_weight": -0.1}],
)
def test_bad_fusion_settings_are_refused(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_settings(**values)


def test_environment_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRIEVAL_FUSION", "rrf")
    monkeypatch.setenv("RETRIEVAL_BM25_WEIGHT", "0.6")
    settings = make_settings()
    assert (settings.retrieval_fusion, settings.retrieval_bm25_weight) == ("rrf", 0.6)


class RecordingStore:
    """BM25 and vector lists fixed per call; records the candidate depth asked for."""

    def __init__(self) -> None:
        self.ids = [uuid.uuid4() for _ in range(4)]
        self.asked: list[tuple[str, int]] = []

    def _hits(self, order: list[int], k: int) -> list[Hit]:
        return [
            Hit(self.ids[i], "D", i + 1, i + 1, 1.0 / (r + 1), "") for r, i in enumerate(order)
        ][:k]

    async def search_bm25(self, corpus_version_id: uuid.UUID, query: str, k: int) -> list[Hit]:
        self.asked.append(("bm25", k))
        return self._hits([0, 1, 2], k)

    async def search_vector(
        self, corpus_version_id: uuid.UUID, query_embedding: object, k: int
    ) -> list[Hit]:
        self.asked.append(("vector", k))
        return self._hits([3, 2, 1], k)

    async def get_chunk(self, chunk_id: uuid.UUID) -> None:
        return None

    async def get_table(self, table_id: uuid.UUID) -> None:
        return None


@pytest.mark.parametrize(("fusion", "weight"), [("weighted", 0.3), ("weighted", 0.9), ("rrf", 0.5)])
def test_hybrid_uses_the_bound_fusion(fusion: str, weight: float) -> None:
    store = RecordingStore()
    bound = Retrieval(HashEmbedder(), Bm25Registry(), fusion=fusion, bm25_weight=weight)  # type: ignore[arg-type]
    hits = asyncio.run(bound.hybrid(store, uuid.uuid4(), "query", 3))  # type: ignore[arg-type]
    assert store.asked == [("bm25", 3 * CANDIDATE_FACTOR), ("vector", 3 * CANDIDATE_FACTOR)]
    expected = fuse(store._hits([0, 1, 2], 6), store._hits([3, 2, 1], 6), fusion, weight, limit=3)  # type: ignore[arg-type]
    assert [h.chunk_id for h in hits] == [h.chunk_id for h in expected]


# ----------------------------------------------------------------- RETRIEVAL_SEARCH (section 25)


def test_search_defaults_to_bm25_everywhere() -> None:
    settings = make_settings()
    assert settings.retrieval_search == "bm25"
    assert Retrieval(HashEmbedder(), Bm25Registry()).search == "bm25"
    assert default_retrieval(settings).search == "bm25"


def test_default_retrieval_takes_the_search_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRIEVAL_SEARCH", "hybrid")
    assert default_retrieval(make_settings()).search == "hybrid"
    with pytest.raises(ValidationError):
        make_settings(retrieval_search="vector")


def test_bm25_search_leaves_the_classifier_on_the_stores_bm25() -> None:
    bound = Retrieval(HashEmbedder(), Bm25Registry(), search="bm25")
    assert bound.classifier_search(RecordingStore()) is None  # type: ignore[arg-type]


def test_hybrid_search_is_retrieval_hybrid_bound_to_the_store() -> None:
    store = RecordingStore()
    bound = Retrieval(HashEmbedder(), Bm25Registry(), fusion="rrf", search="hybrid")
    search = bound.classifier_search(store)  # type: ignore[arg-type]
    assert search is not None
    version = uuid.uuid4()
    got = asyncio.run(search(version, "query", 3))
    assert store.asked == [("bm25", 3 * CANDIDATE_FACTOR), ("vector", 3 * CANDIDATE_FACTOR)]
    direct = asyncio.run(bound.hybrid(store, version, "query", 3))  # type: ignore[arg-type]
    assert [(h.chunk_id, h.score) for h in got] == [(h.chunk_id, h.score) for h in direct]


@pytest.mark.parametrize(
    ("values", "ok"),
    [
        ({"retrieval_chunk_tokens": 200, "retrieval_chunk_overlap": 40}, True),
        ({"retrieval_chunk_tokens": 200, "retrieval_chunk_overlap": 199}, True),
        ({"retrieval_chunk_tokens": 200, "retrieval_chunk_overlap": 200}, False),
        ({"retrieval_chunk_tokens": 40}, False),  # overlap 80 >= 40 (and below the minimum)
        ({"retrieval_chunk_tokens": 10, "retrieval_chunk_overlap": 0}, False),
        ({"retrieval_chunk_overlap": -1}, False),
    ],
)
def test_chunk_window_settings(values: dict[str, object], ok: bool) -> None:
    from basis.config import ConfigError

    if ok:
        settings = make_settings(**values)
        assert settings.retrieval_chunk_tokens == values["retrieval_chunk_tokens"]
        return
    with pytest.raises((ValidationError, ConfigError)):
        make_settings(**values)


def test_chunk_window_defaults_are_the_chunkers(monkeypatch: pytest.MonkeyPatch) -> None:
    from basis.retrieval.chunk import CHUNK_TOKENS, OVERLAP_TOKENS

    settings = make_settings()
    assert (settings.retrieval_chunk_tokens, settings.retrieval_chunk_overlap) == (
        CHUNK_TOKENS,
        OVERLAP_TOKENS,
    )
    monkeypatch.setenv("RETRIEVAL_CHUNK_TOKENS", "200")
    monkeypatch.setenv("RETRIEVAL_CHUNK_OVERLAP", "40")
    settings = make_settings()
    assert (settings.retrieval_chunk_tokens, settings.retrieval_chunk_overlap) == (200, 40)
