"""Embedders: the hash embedder's determinism and shape; model selection from settings."""

from __future__ import annotations

import math
import subprocess
import sys

import pytest

from basis.config import ConfigError, Settings
from basis.retrieval.embed import (
    DEFAULT_MODEL,
    EMBEDDING_DIM,
    HASH_EMBEDDER_NAME,
    Embedder,
    HashEmbedder,
    SentenceTransformerEmbedder,
    embedder_for,
    loaded_models,
)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_shape_name_and_unit_length() -> None:
    embedder = HashEmbedder()
    assert isinstance(embedder, Embedder)
    assert (embedder.name, embedder.dim) == (HASH_EMBEDDER_NAME, EMBEDDING_DIM)
    vectors = embedder.embed(["Section 1245 property", "asset class 57.0", ""])
    assert len(vectors) == 3
    for vector in vectors:
        assert len(vector) == EMBEDDING_DIM
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


def test_deterministic_across_instances_and_processes() -> None:
    text = "Tack-down carpet is removable personal property (Section 1245)."
    first = HashEmbedder().embed([text])[0]
    assert HashEmbedder().embed([text])[0] == first
    # blake2b, not Python's salted hash(): a fresh interpreter gives the same vector.
    code = (
        "from basis.retrieval.embed import HashEmbedder;"
        f"print(repr(HashEmbedder().embed([{text!r}])[0]))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    assert eval(out) == first  # noqa: S307 - our own repr of a list of floats


def test_texts_without_tokens_get_distinct_nonzero_vectors() -> None:
    a, b = HashEmbedder().embed(["", "---"])
    assert any(a) and any(b) and a != b


def test_lexical_neighbours() -> None:
    embedder = HashEmbedder()
    query, near, far = embedder.embed(
        [
            "office furniture asset class 00.11",
            "Asset class 00.11 covers office furniture, fixtures and equipment.",
            "Residential rental property is recovered over 27.5 years.",
        ]
    )
    assert cosine(query, near) > cosine(query, far)
    assert cosine(near, near) == pytest.approx(1.0)


def test_other_dimensions() -> None:
    embedder = HashEmbedder(dim=16)
    assert embedder.name == "hash-v1-16" and len(embedder.embed(["x y"])[0]) == 16


def test_sentence_transformer_embedder_loads_lazily() -> None:
    before = loaded_models()
    embedder = SentenceTransformerEmbedder()
    assert (embedder.name, embedder.dim) == (DEFAULT_MODEL, EMBEDDING_DIM)
    assert embedder.embed([]) == []
    assert loaded_models() == before  # nothing was downloaded or loaded


def settings(**values: object) -> Settings:
    return Settings.model_validate({"BASIS_ENV": "test", **values})


def test_embedder_for_settings() -> None:
    assert isinstance(embedder_for(settings(embedding_model="hash")), HashEmbedder)
    real = embedder_for(settings())
    assert isinstance(real, SentenceTransformerEmbedder) and real.name == DEFAULT_MODEL
    assert embedder_for(settings()) is real  # one embedder per model per process


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_hash_embedder_is_refused_outside_dev_and_test(env: str) -> None:
    with pytest.raises(ConfigError, match="EMBEDDING_MODEL=hash"):
        Settings.model_validate(
            {
                "BASIS_ENV": env,
                "database_url": "postgresql+asyncpg://u@h/db",
                "gcs_bucket": "bucket",
                "firebase_project_id": "project",
                "embedding_model": "hash",
            }
        )
