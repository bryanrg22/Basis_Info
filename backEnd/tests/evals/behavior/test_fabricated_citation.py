"""Fabricated citations are rejected; real ones beside them survive.

BUILD_PLAN 6.6 ``test_fabricated_citation``: a nonexistent chunk id, a real chunk with an
invented excerpt, and a real chunk the model never retrieved are all dropped, and any drop
sends the asset to review (``citation_unverified``).
"""

from __future__ import annotations

import uuid

from classifytest_support import (
    CARPET_CHUNK,
    GARAGE_EXCERPT,
    STRUCTURAL_CHUNK,
    answer_turn,
    call,
    classification,
    component,
    make_ctx,
    tools_turn,
)

from basis.classify.router import classify
from basis.llm.provider import ScriptedProvider

SEARCH = call("search_reference", {"query": "garage door structural component", "k": 1})


async def test_nonexistent_chunk_id_is_rejected() -> None:
    fake = uuid.uuid4()
    provider = ScriptedProvider(
        [tools_turn(SEARCH), answer_turn(classification(citations=((fake, GARAGE_EXCERPT),)))]
    )
    result = await classify(component(), make_ctx(provider))
    assert result.citations == ()
    assert [(d.chunk_id, d.reason) for d in result.dropped] == [(str(fake), "not_found")]
    assert result.needs_review and "citation_unverified" in result.review_reasons


async def test_real_chunk_with_invented_excerpt_is_rejected() -> None:
    invented = "Garage doors are always 5-year personal property"
    provider = ScriptedProvider(
        [tools_turn(SEARCH), answer_turn(classification(citations=((STRUCTURAL_CHUNK, invented),)))]
    )
    result = await classify(component(), make_ctx(provider))
    assert [d.reason for d in result.dropped] == ["excerpt_mismatch"]
    assert result.needs_review


async def test_real_chunk_not_retrieved_in_this_loop_is_rejected() -> None:
    """The carpet chunk exists and contains the text, but the search returned only one hit."""
    provider = ScriptedProvider(
        [
            tools_turn(SEARCH),
            answer_turn(
                classification(
                    citations=(
                        (STRUCTURAL_CHUNK, GARAGE_EXCERPT),
                        (CARPET_CHUNK, "Carpeting - 5-year recovery"),
                    )
                )
            ),
        ]
    )
    result = await classify(component(), make_ctx(provider))
    assert [c.chunk_id for c in result.citations] == [STRUCTURAL_CHUNK]
    assert [(d.chunk_id, d.reason) for d in result.dropped] == [
        (str(CARPET_CHUNK), "not_retrieved")
    ]
    assert result.needs_review and result.review_reasons == ("citation_unverified",)


async def test_malformed_and_tiny_excerpts_are_rejected() -> None:
    provider = ScriptedProvider(
        [
            tools_turn(SEARCH),
            answer_turn(
                classification(citations=(("chunk-7", GARAGE_EXCERPT), (STRUCTURAL_CHUNK, "door")))
            ),
        ]
    )
    result = await classify(component(), make_ctx(provider))
    assert [d.reason for d in result.dropped] == ["malformed_id", "excerpt_too_short"]
    assert result.citations == () and result.confidence <= 0.3
