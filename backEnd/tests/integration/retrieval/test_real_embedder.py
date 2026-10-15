"""The real sentence-transformers model, only when it is already in the local cache.

The unit suite never downloads a model. These tests run where ``~/.cache/huggingface``
holds ``sentence-transformers/all-MiniLM-L6-v2`` (CI must cache that directory for them
to run there; see tests/LEDGER.md) and are skipped otherwise.

The model runs in a child interpreter: while the legacy packages are still collected,
``faiss`` is imported into the pytest process, and faiss and torch each load their own
OpenMP runtime on macOS, which crashed the whole test process with a segmentation fault
(reproduced: ``import faiss`` then ``SentenceTransformer(...).encode`` dies). The worker
never imports faiss; the child process is what production looks like.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from typing import Any

import pytest
from dbtest_support import BACKEND_ROOT
from fixtures import load_fixture_json

from basis.retrieval.embed import DEFAULT_MODEL, EMBEDDING_DIM, model_is_cached

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        not model_is_cached(DEFAULT_MODEL),
        reason=f"{DEFAULT_MODEL} is not in the local Hugging Face cache (no download in tests)",
    ),
]

CHILD = """
import json, sys
from basis.retrieval.embed import SentenceTransformerEmbedder, loaded_models
texts = json.loads(sys.stdin.read())
before = loaded_models()
first = SentenceTransformerEmbedder().embed(texts)
after_first = loaded_models()
SentenceTransformerEmbedder().embed(["another instance"])
print(json.dumps({"vectors": first, "loaded": [before, after_first, loaded_models()]}))
"""


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.fixture(scope="module")
def run() -> dict[str, Any]:
    pairs = load_fixture_json("corpus", "manifest.json")["paraphrase_pairs"]
    texts = (
        ["Section 1245 property", "a garage door", ""]
        + [p["exact"]["text"] for p in pairs]
        + [p["paraphrase"]["text"] for p in pairs]
    )
    result = subprocess.run(
        [sys.executable, "-c", CHILD],
        input=json.dumps(texts),
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env={**os.environ, "HF_HUB_OFFLINE": "1"},  # never download from a test
    )
    assert result.returncode == 0, result.stderr[-2000:]
    out: dict[str, Any] = json.loads(result.stdout.strip().splitlines()[-1])
    out["pairs"] = pairs
    return out


def test_vectors_are_384_d_and_unit_length(run: dict[str, Any]) -> None:
    vectors = run["vectors"]
    assert all(len(v) == EMBEDDING_DIM for v in vectors)
    for vector in vectors[:2]:
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-4)


def test_paraphrases_are_closer_than_unrelated_text(run: dict[str, Any]) -> None:
    n = len(run["pairs"])
    exact, paraphrase = run["vectors"][3 : 3 + n], run["vectors"][3 + n :]
    for i in range(n):
        own = cosine(exact[i], paraphrase[i])
        others = [cosine(exact[i], paraphrase[j]) for j in range(n) if j != i]
        assert own > min(others), run["pairs"][i]["id"]


def test_model_is_loaded_once_per_process(run: dict[str, Any]) -> None:
    assert run["loaded"] == [0, 1, 1]
