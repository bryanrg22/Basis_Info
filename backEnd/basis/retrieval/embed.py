"""Sentence embeddings behind one small protocol.

* :class:`SentenceTransformerEmbedder`: ``sentence-transformers/all-MiniLM-L6-v2``
  (384-d, 256 word-piece window) by default, or another 384-d model of
  :data:`MODEL_SPECS`, L2-normalized so cosine distance in pgvector is ``1 - dot``.
  Some models are trained with a prefix on queries, passages or both (e5: ``query: `` /
  ``passage: ``; bge: an instruction on queries only); :meth:`embed` is for stored
  text and :meth:`embed_query` for search queries, and each adds its prefix. A model
  outside :data:`MODEL_SPECS` gets no prefixes. The model is
  loaded once per process (``_load_model`` is cached and guarded by a lock) and lazily,
  so importing this module never imports torch. ``encode`` runs under a lock: concurrent
  activities share one model, and serializing CPU inference costs nothing on a worker
  whose cores are already busy with it.

  The default model is pinned to a revision (``ModelSpec.revision``): the corpus
  embeddings must come from exactly the weights that embed the queries, and the Docker
  image bakes that revision into its Hugging Face cache (``backEnd/Dockerfile``,
  ``HF_HOME``). The load reads the cache first (``local_files_only``: no request to the
  Hub, so no network is needed and no HEAD call per file); when the revision is not
  cached it downloads it, with a warning, because a run-time download is what a built
  image should never need. ``warm`` (:meth:`SentenceTransformerEmbedder.warm`) loads the
  model and encodes one text, and reports where the weights came from and how long it
  took; the worker calls it before polling (EVAL_REPORT section 38: on a fresh container
  the first ``embed`` took 187 s, far past the activity's 30 s heartbeat timeout).
* :class:`HashEmbedder`: deterministic feature hashing (signed, 384 buckets, seeded by a
  blake2b hash of each token), L2-normalized. For tests and local runs without the model
  download. It is lexical, not semantic: neighbours share words. ``Settings`` refuses
  ``EMBEDDING_MODEL=hash`` outside ``dev`` and ``test``.

The model name an embedder reports is stored on ``corpus_versions.embedding_model``; a
query must be embedded by the same embedder as the corpus it searches.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, runtime_checkable

from basis.retrieval.tokenizer import irs_tokenize

if TYPE_CHECKING:
    from basis.config import Settings

logger = logging.getLogger("basis.retrieval.embed")

EMBEDDING_DIM: Final = 384
DEFAULT_MODEL: Final = "sentence-transformers/all-MiniLM-L6-v2"
# The Hub commit the default model is loaded at and the image bakes (backEnd/Dockerfile's
# EMBEDDING_MODEL_REVISION; tests/unit/retrieval/test_embed.py keeps the two equal).
DEFAULT_MODEL_REVISION: Final = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
HASH_MODEL: Final = "hash"
HASH_EMBEDDER_NAME: Final = "hash-v1-384"
ENCODE_BATCH_SIZE: Final = 32


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """What a model needs that its name does not say (from each model card)."""

    dim: int
    window: int  # input word pieces, special tokens included; longer text is truncated
    query_prefix: str = ""
    passage_prefix: str = ""
    revision: str | None = None  # the Hub commit to load; None: the repository's main


# The models measured in EVAL_REPORT section 25. All are 384-d, the chunks.embedding
# column's dimension; a model of another dimension needs a migration first.
MODEL_SPECS: Final[dict[str, ModelSpec]] = {
    DEFAULT_MODEL: ModelSpec(dim=384, window=256, revision=DEFAULT_MODEL_REVISION),
    "BAAI/bge-small-en-v1.5": ModelSpec(
        dim=384,
        window=512,
        query_prefix="Represent this sentence for searching relevant passages: ",
    ),
    "intfloat/e5-small-v2": ModelSpec(
        dim=384, window=512, query_prefix="query: ", passage_prefix="passage: "
    ),
    "thenlper/gte-small": ModelSpec(dim=384, window=512),  # no prefixes (model card)
}


class EmbedderConfigError(RuntimeError):
    pass


ModelSource = Literal["cache", "download"]


@dataclass(frozen=True, slots=True)
class ModelLoad:
    """What :meth:`Embedder.warm` reports: where the weights came from and how long the
    load (and one encode) took. ``source`` is where this process loaded the model from,
    also when an earlier call had loaded it (``seconds`` is then near zero)."""

    model: str
    revision: str | None
    source: ModelSource
    seconds: float


@runtime_checkable
class Embedder(Protocol):
    @property
    def name(self) -> str:
        """Recorded on ``corpus_versions.embedding_model``."""
        ...

    @property
    def dim(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One unit-length vector per stored text (synchronous; CPU-bound for real models)."""
        ...

    def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        """One unit-length vector per search query (the model's query prefix, if any)."""
        ...

    def warm(self) -> ModelLoad | None:
        """Load whatever ``embed`` would load on first use; None when there is nothing to
        load. Synchronous and possibly slow (a download); callers run it in a thread."""
        ...


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


class HashEmbedder:
    """Signed feature hashing of IRS-tokenized words into ``dim`` buckets."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    @property
    def name(self) -> str:
        return HASH_EMBEDDER_NAME if self._dim == EMBEDDING_DIM else f"hash-v1-{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        return self.embed(texts)

    def warm(self) -> None:
        return None

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        # A text with no tokens still gets a deterministic unit vector: a zero vector has
        # no cosine distance (pgvector returns NaN) and would sort unpredictably.
        tokens = irs_tokenize(text) or [f"\x00empty:{text}"]
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            bucket = value % self._dim
            sign = 1.0 if (value >> 63) & 1 else -1.0
            vector[bucket] += sign
        if not any(vector):  # every token cancelled out
            digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
            vector[int.from_bytes(digest, "big") % self._dim] = 1.0
        return _normalize(vector)


_MODEL_LOCK = threading.Lock()


def model_revision(model_name: str) -> str | None:
    """The pinned Hub commit of a listed model (None: unpinned, the repository's main)."""
    spec = MODEL_SPECS.get(model_name)
    return spec.revision if spec else None


@dataclass(frozen=True, slots=True)
class _Loaded:
    model: Any
    source: ModelSource


@lru_cache(maxsize=4)
def _load_model(model_name: str) -> _Loaded:
    """Load from the local Hugging Face cache without touching the network; when the
    pinned revision is not there, download it (warned: a built image has it baked in)."""
    from sentence_transformers import SentenceTransformer

    revision = model_revision(model_name)
    try:
        model = SentenceTransformer(
            model_name, device="cpu", revision=revision, local_files_only=True
        )
    except OSError:  # transformers' "couldn't find them in the cached files"
        logger.warning(
            "embedding model %s (revision %s) is not in the local Hugging Face cache; "
            "downloading it now. The Docker image bakes it (backEnd/Dockerfile): a worker "
            "that downloads at run time was not built from it or has another HF_HOME",
            model_name,
            revision or "main",
        )
        model = SentenceTransformer(model_name, device="cpu", revision=revision)
        return _Loaded(model=model, source="download")
    return _Loaded(model=model, source="cache")


def load_model(model_name: str) -> Any:
    """The process-wide model instance (loaded on first use, then cached)."""
    with _MODEL_LOCK:
        return _load_model(model_name).model


def warm_model(model_name: str) -> ModelLoad:
    """Load ``model_name`` (if this process has not yet) and encode one short text, so the
    first real ``embed`` pays neither the load nor the first-call setup; what came from
    where and how long it took."""
    started = time.perf_counter()
    with _MODEL_LOCK:
        loaded = _load_model(model_name)
    loaded.model.encode(
        ["warm-up"],
        batch_size=1,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return ModelLoad(
        model=model_name,
        revision=model_revision(model_name),
        source=loaded.source,
        seconds=time.perf_counter() - started,
    )


def loaded_models() -> int:
    """How many distinct models this process has loaded (for tests)."""
    return _load_model.cache_info().currsize


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, dim: int = EMBEDDING_DIM) -> None:
        self._model_name = model_name
        self._dim = dim
        spec = MODEL_SPECS.get(model_name)
        self._query_prefix = spec.query_prefix if spec else ""
        self._passage_prefix = spec.passage_prefix if spec else ""
        self._encode_lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode([self._passage_prefix + t for t in texts])

    def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode([self._query_prefix + t for t in texts])

    def warm(self) -> ModelLoad:
        with self._encode_lock:
            return warm_model(self._model_name)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = load_model(self._model_name)
        with self._encode_lock:
            matrix = model.encode(
                texts,
                batch_size=ENCODE_BATCH_SIZE,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        vectors: list[list[float]] = [[float(v) for v in row] for row in matrix]
        if vectors and len(vectors[0]) != self._dim:
            raise EmbedderConfigError(
                f"{self._model_name} produced {len(vectors[0])}-d vectors; expected {self._dim}"
            )
        return vectors


def model_is_cached(model_name: str = DEFAULT_MODEL, revision: str | None = None) -> bool:
    """Whether the model's files, at ``revision`` (default: the listed model's pin, else
    the repository's main), are in the local Hugging Face cache (no network)."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:  # pragma: no cover - sentence-transformers depends on it
        return False
    found = try_to_load_from_cache(
        model_name, "config.json", revision=revision or model_revision(model_name)
    )
    return isinstance(found, str)


@lru_cache(maxsize=8)
def _embedder(model: str) -> Embedder:
    if model == HASH_MODEL:
        return HashEmbedder()
    return SentenceTransformerEmbedder(model)


def embedder_for(settings: Settings) -> Embedder:
    """The process embedder for ``settings.embedding_model`` (one instance per model).

    ``Settings`` already refuses ``hash`` outside dev and test.
    """
    return _embedder(settings.embedding_model)
