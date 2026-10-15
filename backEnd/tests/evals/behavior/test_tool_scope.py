"""The model asks for another study's evidence: the tool refuses, the trace shows it, review.

BUILD_PLAN 6.6 ``test_tool_scope``.
"""

from __future__ import annotations

import json

from classifytest_support import (
    OTHER_STUDY_CHUNK,
    OTHER_STUDY_VERSION,
    OTHER_TABLE,
    answer_turn,
    call,
    classification,
    component,
    make_ctx,
    tools_turn,
)

from basis.classify.router import classify
from basis.llm.provider import ScriptedProvider


async def test_another_studys_chunk_is_refused_and_cannot_be_cited() -> None:
    provider = ScriptedProvider(
        [
            tools_turn(
                call("get_chunk", {"chunk_id": str(OTHER_STUDY_CHUNK)}, "c1"),
                call("get_table", {"table_id": str(OTHER_TABLE)}, "c2"),
                call(
                    "search_reference",
                    {"query": "garage", "corpus_version_id": str(OTHER_STUDY_VERSION)},
                    "c3",
                ),
            ),
            answer_turn(
                classification(
                    citations=((OTHER_STUDY_CHUNK, "another client's appraisal says the owner"),)
                )
            ),
        ]
    )
    result = await classify(component(), make_ctx(provider))

    # The trace shows three refusals and nothing retrieved.
    assert [(t.name, t.refused) for t in result.tool_calls] == [
        ("get_chunk", True),
        ("get_table", True),
        ("search_reference", True),
    ]
    assert "outside this study" in (result.tool_calls[0].error or "")
    assert "invalid arguments" in (result.tool_calls[2].error or "")
    assert all(t.chunk_ids == () for t in result.tool_calls)
    # Nothing of the other study reached the model.
    tool_messages = [m for m in provider.requests[1].messages if m.role == "tool"]
    assert len(tool_messages) == 3
    for message in tool_messages:
        assert "error" in json.loads(message.content)
        assert "Confidential" not in message.content
    # Citing it anyway gets the citation dropped and the asset sent to review.
    assert result.citations == ()
    assert [d.reason for d in result.dropped] == ["out_of_scope"]
    assert result.needs_review
    assert result.review_reasons[:2] == ("missing_evidence", "citation_unverified")


async def test_no_tool_takes_a_study_or_corpus_parameter() -> None:
    provider = ScriptedProvider([answer_turn()])
    await classify(component(), make_ctx(provider))
    for spec in provider.requests[0].tools:
        params = set(spec.parameters["properties"])
        assert params <= {"query", "k", "chunk_id", "table_id"}, spec.name
        assert spec.parameters["additionalProperties"] is False
