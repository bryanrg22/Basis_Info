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
* :class:`HashEmbedder`: deterministic feature hashing (signed, 384 buckets, seeded by a
  blake2b hash of each token), L2-normalized. For tests and local runs without the model
  download. It is lexical, not semantic: neighbours share words. ``Settings`` refuses
  ``EMBEDDING_MODEL=hash`` outside ``dev`` and ``test``.

The model name an embedder reports is stored on ``corpus_versions.embedding_model``; a
query must be embedded by the same embedder as the corpus it searches.
"""

from __future__ import annotations

import hashlib
import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from basis.retrieval.tokenizer import irs_tokenize

if TYPE_CHECKING:
    from basis.config import Settings

EMBEDDING_DIM: Final = 384
DEFAULT_MODEL: Final = "sentence-transformers/all-MiniLM-L6-v2"
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


# The models measured in EVAL_REPORT section 25. All are 384-d, the chunks.embedding
# column's dimension; a model of another dimension needs a migration first.
MODEL_SPECS: Final[dict[str, ModelSpec]] = {
    DEFAULT_MODEL: ModelSpec(dim=384, window=256),
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


@lru_cache(maxsize=4)
def _load_model(model_name: str) -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, device="cpu")


def load_model(model_name: str) -> Any:
    """The process-wide model instance (loaded on first use, then cached)."""
    with _MODEL_LOCK:
        return _load_model(model_name)


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


def model_is_cached(model_name: str = DEFAULT_MODEL) -> bool:
    """Whether the model's files are in the local Hugging Face cache (no network)."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:  # pragma: no cover - sentence-transformers depends on it
        return False
    found = try_to_load_from_cache(model_name, "config.json")
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
