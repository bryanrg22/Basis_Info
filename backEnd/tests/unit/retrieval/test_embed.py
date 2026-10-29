"""Embedders: the hash embedder's determinism and shape; model selection from settings."""

from __future__ import annotations

import logging
import math
import re
import subprocess
import sys
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from dbtest_support import BACKEND_ROOT

from basis.config import ConfigError, Settings
from basis.retrieval.embed import (
    DEFAULT_MODEL,
    DEFAULT_MODEL_REVISION,
    EMBEDDING_DIM,
    HASH_EMBEDDER_NAME,
    MODEL_SPECS,
    Embedder,
    HashEmbedder,
    ModelLoad,
    SentenceTransformerEmbedder,
    embedder_for,
    loaded_models,
    model_is_cached,
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


# ----------------------------------------------------------------- prefixes (section 25)


class FakeModel:
    """Records what ``encode`` was given; returns unit vectors of the right size."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.seen: list[list[str]] = []
        self.dim = dim

    def encode(self, texts: list[str], **_: object) -> list[list[float]]:
        self.seen.append(list(texts))
        return [[1.0] + [0.0] * (self.dim - 1) for _ in texts]


@pytest.mark.parametrize(
    ("model", "query", "passage"),
    [
        (DEFAULT_MODEL, "", ""),
        ("BAAI/bge-small-en-v1.5", "Represent this sentence for searching relevant passages: ", ""),
        ("intfloat/e5-small-v2", "query: ", "passage: "),
        ("some/unlisted-model", "", ""),
    ],
)
def test_queries_and_passages_get_the_models_prefixes(
    monkeypatch: pytest.MonkeyPatch, model: str, query: str, passage: str
) -> None:
    from basis.retrieval import embed as embed_module

    fake = FakeModel()
    monkeypatch.setattr(embed_module, "load_model", lambda name: fake)
    embedder = SentenceTransformerEmbedder(model)
    assert isinstance(embedder, Embedder)
    embedder.embed(["Section 1245 property"])
    embedder.embed_query(["what is 1245 property"])
    assert fake.seen == [[passage + "Section 1245 property"], [query + "what is 1245 property"]]
    assert embedder.embed_query([]) == [] and len(fake.seen) == 2


def test_every_listed_model_fits_the_vector_column() -> None:
    from basis.db.models import EMBEDDING_DIM as COLUMN_DIM
    from basis.retrieval.embed import MODEL_SPECS

    assert DEFAULT_MODEL in MODEL_SPECS
    assert {spec.dim for spec in MODEL_SPECS.values()} == {COLUMN_DIM}
    assert MODEL_SPECS[DEFAULT_MODEL].window == 256


def test_wrong_dimension_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from basis.retrieval import embed as embed_module
    from basis.retrieval.embed import EmbedderConfigError

    monkeypatch.setattr(embed_module, "load_model", lambda name: FakeModel(dim=768))
    with pytest.raises(EmbedderConfigError):
        SentenceTransformerEmbedder("BAAI/bge-base-en-v1.5").embed_query(["x"])


def test_hash_embedder_embeds_queries_like_passages() -> None:
    embedder = HashEmbedder()
    assert embedder.embed_query(["asset class 57.0"]) == embedder.embed(["asset class 57.0"])


# ------------------------------------------------- loading and warming (EVAL_REPORT 38)


class FakeSentenceTransformers:
    """A stand-in ``sentence_transformers`` module: records the constructor's keyword
    arguments and raises transformers' offline ``OSError`` for a revision that is not in
    ``cached`` when ``local_files_only`` is set."""

    def __init__(self, cached: set[str | None]) -> None:
        self.cached = cached
        self.calls: list[dict[str, Any]] = []
        self.encoded: list[list[str]] = []
        outer = self

        class SentenceTransformer:
            def __init__(self, name: str, **kwargs: Any) -> None:
                outer.calls.append({"name": name, **kwargs})
                if kwargs.get("local_files_only") and kwargs.get("revision") not in outer.cached:
                    raise OSError(
                        "We couldn't connect to 'https://huggingface.co' to load the files, "
                        "and couldn't find them in the cached files."
                    )

            def encode(self, texts: list[str], **_: Any) -> list[list[float]]:
                outer.encoded.append(list(texts))
                return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]

        self.SentenceTransformer = SentenceTransformer


@pytest.fixture
def fake_st(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[set[str | None]], FakeSentenceTransformers]:
    """Install the stand-in module (no torch import); each test loads a model name of its
    own, so the process-wide load cache never crosses tests."""

    def install(cached: set[str | None]) -> FakeSentenceTransformers:
        fake = FakeSentenceTransformers(cached)
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
        return fake

    return install


def test_the_default_model_is_pinned_and_the_pin_is_what_is_loaded(
    fake_st: Callable[[set[str | None]], FakeSentenceTransformers],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from basis.retrieval import embed as embed_module

    assert MODEL_SPECS[DEFAULT_MODEL].revision == DEFAULT_MODEL_REVISION
    assert re.fullmatch(r"[0-9a-f]{40}", DEFAULT_MODEL_REVISION)  # a commit, not a branch
    name = "test/pinned-" + uuid.uuid4().hex
    monkeypatch.setitem(
        embed_module.MODEL_SPECS, name, embed_module.ModelSpec(dim=384, window=256, revision="abc")
    )
    fake = fake_st({"abc"})
    load = SentenceTransformerEmbedder(name).warm()
    assert fake.calls == [
        {"name": name, "device": "cpu", "revision": "abc", "local_files_only": True}
    ]
    assert isinstance(load, ModelLoad)
    assert (load.model, load.revision, load.source) == (name, "abc", "cache")
    assert load.seconds >= 0.0
    assert fake.encoded == [["warm-up"]]  # the first-call setup is paid at the warm-up
    SentenceTransformerEmbedder(name).embed(["x"])
    assert len(fake.calls) == 1  # loaded once per process


def test_an_uncached_revision_is_downloaded_with_a_warning(
    fake_st: Callable[[set[str | None]], FakeSentenceTransformers],
    caplog: pytest.LogCaptureFixture,
) -> None:
    name = "test/uncached-" + uuid.uuid4().hex  # unlisted: no pin, the repository's main
    fake = fake_st(set())
    with caplog.at_level(logging.WARNING, logger="basis.retrieval.embed"):
        load = SentenceTransformerEmbedder(name).warm()
    assert [c.get("local_files_only") for c in fake.calls] == [True, None]
    assert fake.calls[1] == {"name": name, "device": "cpu", "revision": None}
    assert (load.source, load.revision) == ("download", None)
    assert any("not in the local Hugging Face cache" in r.message for r in caplog.records)
    assert any("Dockerfile" in r.message for r in caplog.records)
    assert SentenceTransformerEmbedder(name).warm().source == "download"  # the same load


def test_hash_embedder_has_nothing_to_warm() -> None:
    assert HashEmbedder().warm() is None


def test_model_is_cached_asks_for_the_pinned_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    import huggingface_hub

    asked: list[tuple[str, str, str | None]] = []

    def fake_try(repo_id: str, filename: str, revision: str | None = None, **_: Any) -> object:
        asked.append((repo_id, filename, revision))
        return "/cache/config.json" if revision == DEFAULT_MODEL_REVISION else None

    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", fake_try)
    assert model_is_cached() is True
    assert model_is_cached(DEFAULT_MODEL, "0" * 40) is False
    assert model_is_cached("some/unlisted-model") is False
    assert asked == [
        (DEFAULT_MODEL, "config.json", DEFAULT_MODEL_REVISION),
        (DEFAULT_MODEL, "config.json", "0" * 40),
        ("some/unlisted-model", "config.json", None),
    ]


def test_the_dockerfile_bakes_the_pinned_model() -> None:
    """The image downloads the model at build time (EVAL_REPORT 38); its two ARGs must be
    the model and revision the code loads, or the worker downloads at run time after all."""
    dockerfile = (BACKEND_ROOT / "Dockerfile").read_text()
    args = dict(re.findall(r"^ARG (EMBEDDING_MODEL(?:_REVISION)?)=(\S+)$", dockerfile, re.M))
    assert args == {
        "EMBEDDING_MODEL": DEFAULT_MODEL,
        "EMBEDDING_MODEL_REVISION": DEFAULT_MODEL_REVISION,
    }
    assert re.search(r"^ENV HF_HOME=/\S+$", dockerfile, re.M)  # where the worker reads it
    bake = re.search(
        r"SentenceTransformer\('\$EMBEDDING_MODEL', revision='\$EMBEDDING_MODEL_REVISION'",
        dockerfile,
    )
    assert bake is not None
    assert dockerfile.index("ARG EMBEDDING_MODEL=") < dockerfile.index("COPY basis ./basis")
