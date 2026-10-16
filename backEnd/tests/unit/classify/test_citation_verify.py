"""Citation verification: resolve, scope, retrieved, normalized excerpt containment."""

from __future__ import annotations

import uuid

import pytest
from classifytest_support import (
    CARPET_CHUNK,
    OTHER_STUDY_CHUNK,
    REF_VERSION,
    STRUCTURAL_CHUNK,
    STUDY_VERSION,
    make_store,
)

from basis.classify.schemas import CitationClaim
from basis.classify.verify import excerpt_supported, normalize_text, verify_citations

ALLOWED = frozenset({REF_VERSION, STUDY_VERSION})


def claim(chunk: uuid.UUID | str, excerpt: str) -> CitationClaim:
    return CitationClaim(chunk_id=str(chunk), excerpt=excerpt)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Carpeting   -\n5-year ", "carpeting - 5-year"),
        ("“Tacked down”", '"tacked down"'),
        ("it’s", "it's"),
        ("5–year — recovery", "5-year - recovery"),
        ("ﬁxtures", "fixtures"),  # NFKC folds the fi ligature
        ("soft­hyphen", "softhyphen"),
    ],
)
def test_normalize_text(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_excerpt_supported_ignores_typography_and_case() -> None:
    text = "Floor coverings. Carpeting - 5-year recovery when it is tacked down."
    assert excerpt_supported("CARPETING – 5‑year   recovery", text)
    assert not excerpt_supported("carpeting - 7-year recovery", text)
    assert not excerpt_supported("Carpeting", text)  # too short to mean anything


async def test_a_valid_citation_is_verified() -> None:
    verified, dropped = await verify_citations(
        [claim(CARPET_CHUNK, "Carpeting - 5-year recovery")],
        make_store(),
        allowed_versions=ALLOWED,
        retrieved=frozenset({CARPET_CHUNK}),
    )
    assert dropped == []
    [citation] = verified
    assert (citation.chunk_id, citation.doc_id, citation.page_start) == (
        CARPET_CHUNK,
        "IRS_IRS_COST_SEG_ATG__2024",
        307,
    )


@pytest.mark.parametrize(
    ("chunk", "excerpt", "reason"),
    [
        ("not-a-uuid", "Carpeting - 5-year recovery", "malformed_id"),
        (uuid.UUID(int=99), "Carpeting - 5-year recovery", "not_found"),
        (OTHER_STUDY_CHUNK, "another client's appraisal says", "out_of_scope"),
        (STRUCTURAL_CHUNK, "A garage door is a door of the building", "not_retrieved"),
        (CARPET_CHUNK, "Carpeting - 3-year recovery", "excerpt_mismatch"),
        (CARPET_CHUNK, "Carpeting", "excerpt_too_short"),
    ],
)
async def test_each_failure_drops_the_citation_with_a_reason(
    chunk: uuid.UUID | str, excerpt: str, reason: str
) -> None:
    verified, dropped = await verify_citations(
        [claim(chunk, excerpt)],
        make_store(),
        allowed_versions=ALLOWED,
        retrieved=frozenset({CARPET_CHUNK, OTHER_STUDY_CHUNK}),
    )
    assert verified == []
    assert [d.reason for d in dropped] == [reason]


async def test_duplicates_are_dropped_and_order_is_kept() -> None:
    claims = [
        claim(CARPET_CHUNK, "Carpeting - 5-year recovery"),
        claim(STRUCTURAL_CHUNK, "plumbing and plumbing fixtures"),
        claim(CARPET_CHUNK, "carpeting  -  5-YEAR recovery"),
    ]
    verified, dropped = await verify_citations(claims, make_store(), allowed_versions=ALLOWED)
    assert [v.chunk_id for v in verified] == [CARPET_CHUNK, STRUCTURAL_CHUNK]
    assert [d.reason for d in dropped] == ["duplicate"]


async def test_without_a_store_nothing_verifies() -> None:
    verified, dropped = await verify_citations(
        [claim(CARPET_CHUNK, "Carpeting - 5-year recovery")], None, allowed_versions=ALLOWED
    )
    assert verified == [] and dropped[0].reason == "not_found"


async def test_retrieved_none_skips_the_retrieval_check() -> None:
    """Rule and cache citations were never 'retrieved' by a model; scope still applies."""
    verified, _ = await verify_citations(
        [claim(STRUCTURAL_CHUNK, "plumbing and plumbing fixtures")],
        make_store(),
        allowed_versions=frozenset({REF_VERSION}),
    )
    assert len(verified) == 1
