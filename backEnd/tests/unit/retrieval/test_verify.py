"""Citation checks (BUILD_PLAN 6.1 ``test_citation_verify``): normalized substring, right chunk."""

from __future__ import annotations

import uuid

import pytest

from basis.retrieval.types import Chunk
from basis.retrieval.verify import (
    MIN_EXCERPT_CHARS,
    check_citation,
    normalize_text,
    resolve_citation,
    verify_excerpt,
)

CHUNK_TEXT = (
    "Sec. 4. Asset class 57.0, distributive trades and services\n"
    "Asset class 57.0 covers assets used in wholesale and retail trade — such as "
    "display fixtures and point-of-sale equipment. The “class life” is 9 years."
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  Two\n\tlines  ", "two lines"),
        ("“quoted” ‘single’", "\"quoted\" 'single'"),
        ("a – b — c ‒ d − e", "a - b - c - d - e"),
        ("ﬁxtures", "fixtures"),  # ligature (NFKC)
        ("non breaking", "non breaking"),
        ("soft­hyphen zero​width", "softhyphen zerowidth"),
        ("STRASSE Straße", "strasse strasse"),  # casefold
    ],
)
def test_normalize_text(text: str, expected: str) -> None:
    assert normalize_text(text) == expected


@pytest.mark.parametrize(
    "excerpt",
    [
        "Asset class 57.0 covers assets used in wholesale and retail trade",
        "ASSET CLASS 57.0 COVERS ASSETS",
        "retail trade - such as display fixtures",  # dash differs
        'The "class life" is 9 years.',  # quotes differ
        "distributive trades and services Asset class 57.0",  # line break became a space
        "  point-of-sale   equipment ",
    ],
)
def test_excerpt_variants_that_keep_meaning_verify(excerpt: str) -> None:
    assert verify_excerpt(CHUNK_TEXT, excerpt)


@pytest.mark.parametrize(
    "excerpt",
    [
        "Asset class 57.1 covers assets",  # one digit changed
        "class life is 9 years",  # quotation marks dropped: no longer a substring
        "covers assets used in retail trade",  # words dropped
        "Asset class 00.11 covers office furniture",  # text from another chunk
        "",
        "57.0",  # too short to prove anything
    ],
)
def test_excerpts_that_change_meaning_or_prove_nothing_fail(excerpt: str) -> None:
    assert not verify_excerpt(CHUNK_TEXT, excerpt)


def test_minimum_length_is_configurable() -> None:
    assert len(normalize_text("class 57.0 c")) == MIN_EXCERPT_CHARS
    assert verify_excerpt(CHUNK_TEXT, "class 57.0 c")
    assert not verify_excerpt(CHUNK_TEXT, "57.0 cover")  # 10 characters: too short
    assert verify_excerpt(CHUNK_TEXT, "57.0", min_chars=1)


class FakeStore:
    def __init__(self, *chunks: Chunk) -> None:
        self.chunks = {c.id: c for c in chunks}
        self.calls = 0

    async def get_chunk(self, chunk_id: uuid.UUID) -> Chunk | None:
        self.calls += 1
        return self.chunks.get(chunk_id)


V1, V2 = uuid.uuid4(), uuid.uuid4()
CHUNK = Chunk(uuid.uuid4(), V1, "SYN_PUB_A", "SYN_PUB_A:2:0", 2, 2, None, CHUNK_TEXT, None)


async def test_resolve_citation_checks_the_version() -> None:
    store = FakeStore(CHUNK)
    assert await resolve_citation(store, CHUNK.id, V1) == CHUNK
    assert await resolve_citation(store, str(CHUNK.id), V1) == CHUNK
    assert await resolve_citation(store, CHUNK.id, V2) is None  # right id, wrong version
    assert await resolve_citation(store, uuid.uuid4(), V1) is None


@pytest.mark.parametrize("bad", ["", "chunk-7", "SYN_PUB_A:2:0", "not a uuid at all"])
async def test_malformed_ids_from_a_model_resolve_to_none(bad: str) -> None:
    store = FakeStore(CHUNK)
    assert await resolve_citation(store, bad, V1) is None
    assert store.calls == 0


async def test_check_citation_reports_why() -> None:
    store = FakeStore(CHUNK)
    ok = await check_citation(store, CHUNK.id, V1, "Asset class 57.0 covers assets")
    assert ok.ok and ok.status == "verified" and ok.chunk == CHUNK
    wrong_text = await check_citation(store, CHUNK.id, V1, "Asset class 00.11 covers furniture")
    assert (wrong_text.ok, wrong_text.status) == (False, "excerpt_not_in_chunk")
    wrong_version = await check_citation(store, CHUNK.id, V2, "Asset class 57.0 covers assets")
    assert (wrong_version.status, wrong_version.chunk) == ("chunk_not_found", None)
