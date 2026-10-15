"""Missing evidence -> ``needs_review`` with confidence at most 0.3.

BUILD_PLAN 6.6 ``test_missing_evidence``: a model that answers without searching, or
whose searches find nothing it can cite, does not produce a confident classification.
"""

from __future__ import annotations

from classifytest_support import answer_turn, call, classification, component, make_ctx, tools_turn

from basis.classify.router import classify
from basis.classify.store_protocol import InMemoryChunkStore
from basis.llm.provider import ScriptedProvider


async def test_no_search_is_needs_review_with_low_confidence() -> None:
    provider = ScriptedProvider([answer_turn(classification(confidence=0.99))])
    result = await classify(component(), make_ctx(provider))
    assert result.needs_review
    assert result.review_reasons[0] == "missing_evidence"
    assert result.confidence <= 0.3


async def test_search_that_finds_nothing_is_needs_review_with_low_confidence() -> None:
    empty = InMemoryChunkStore()
    provider = ScriptedProvider(
        [
            tools_turn(call("search_reference", {"query": "garage door"})),
            answer_turn(classification(citations=(), confidence=0.95)),
        ]
    )
    result = await classify(component(), make_ctx(provider, store=empty))
    [trace] = result.tool_calls
    assert not trace.refused and trace.chunk_ids == ()  # the search ran and found nothing
    assert result.review_reasons == ("citation_unverified",)
    assert result.confidence <= 0.3


async def test_no_reference_corpus_is_needs_review_with_low_confidence() -> None:
    provider = ScriptedProvider(
        [
            tools_turn(call("search_reference", {"query": "garage door"})),
            answer_turn(classification(confidence=0.9)),
        ]
    )
    result = await classify(component(), make_ctx(provider, reference_version_id=None))
    assert result.tool_calls[0].refused
    assert result.review_reasons[:2] == ("missing_evidence", "citation_unverified")
    assert result.confidence <= 0.3


async def test_model_that_gives_up_is_needs_review_with_zero_confidence() -> None:
    provider = ScriptedProvider([answer_turn(classification(confidence=0.0, citations=()))])
    result = await classify(component(), make_ctx(provider))
    assert result.needs_review and result.confidence == 0.0
