"""``evals.predict_assets``: the store and the classifier search it gives the model
(EVAL_REPORT section 25). The model loop itself is the router's and is tested there."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from basis.config import Settings
from basis.retrieval.embed import HashEmbedder
from evals import datasets as ds
from evals import predict_assets as pa
from evals import retrieval as rt

CORPUS = ds.SYNTHETIC_DIR / "corpus"


def settings(**values: object) -> Settings:
    return Settings(BASIS_ENV="test", EMBEDDING_MODEL="hash", **values)  # type: ignore[arg-type]


def args(*extra: str) -> list[str]:
    return ["--out", "/tmp/unused", "--model", "claude-sonnet-5-5", *extra]


def test_defaults_are_the_historical_store_and_bm25() -> None:
    parsed = pa.parse_args(args())
    assert (parsed.store, parsed.search) == ("pymupdf", "bm25")


def test_hybrid_needs_the_parser_store(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        pa.parse_args(args("--search", "hybrid"))
    assert "--search hybrid needs --store parser" in capsys.readouterr().err
    parsed = pa.parse_args(args("--store", "parser", "--search", "hybrid"))
    assert (parsed.store, parsed.search) == ("parser", "hybrid")


def test_the_reference_version_is_the_indexs() -> None:
    assert pa.REFERENCE_VERSION == rt.CHUNK_VERSION


def test_parser_store_is_the_production_chunks_at_the_configured_window() -> None:
    default = pa.parser_store(CORPUS, "*.pdf", settings())
    small = pa.parser_store(
        CORPUS, "*.pdf", settings(retrieval_chunk_tokens=60, retrieval_chunk_overlap=10)
    )
    expected = rt.parser_chunks(ds.corpus_pdfs(CORPUS))
    assert [c.chunk_key for c in default.chunks] == [c.chunk_key for c in expected]
    assert len(small.chunks) > len(default.chunks)
    assert pa.parser_store(CORPUS, "NOTHING_*.pdf", settings()).chunks == []


def test_classifier_search_follows_the_flag() -> None:
    index = pa.parser_store(CORPUS, "*.pdf", settings())
    assert pa.classifier_search(settings(), "bm25", index) is None
    search = pa.classifier_search(settings(), "hybrid", index)
    assert search is not None
    query = "asset class 57.0"
    got = asyncio.run(search(rt.CHUNK_VERSION, query, 5))
    from basis.retrieval.hybrid import hybrid_search

    direct = asyncio.run(hybrid_search(index, HashEmbedder(), rt.CHUNK_VERSION, query, 5))
    assert [h.chunk_id for h in got] == [h.chunk_id for h in direct]
    assert got and all(h.doc_id for h in got)


def test_the_historical_store_is_unchanged(tmp_path: Path) -> None:
    store = pa.corpus_store(CORPUS, "*.pdf")
    assert store.chunks
    assert all(c.corpus_version_id == pa.REFERENCE_VERSION for c in store.chunks.values())
