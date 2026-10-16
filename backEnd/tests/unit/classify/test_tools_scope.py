"""Tools are bound to one study's corpora; the model can never name a study or a corpus."""

from __future__ import annotations

import json
import uuid

import pytest
from classifytest_support import (
    CARPET_CHUNK,
    OTHER_STUDY_CHUNK,
    OTHER_STUDY_VERSION,
    OTHER_TABLE,
    REF_TABLE,
    REF_VERSION,
    STUDY_CHUNK,
    STUDY_VERSION,
    make_store,
)

from basis.classify.store_protocol import InMemoryChunkStore
from basis.classify.tools import TOOL_NAMES, BoundTool, build_tools


def tools(
    store: InMemoryChunkStore | None = None, **versions: uuid.UUID | None
) -> dict[str, BoundTool]:
    built = build_tools(
        "study-1",
        versions.get("reference", REF_VERSION),
        versions.get("study", STUDY_VERSION),
        store if store is not None else make_store(),
    )
    return {t.name: t for t in built}


def test_four_tools_with_minimal_schemas() -> None:
    built = tools()
    assert tuple(built) == TOOL_NAMES
    expected = {
        "search_reference": {"query", "k"},
        "search_study": {"query", "k"},
        "get_chunk": {"chunk_id"},
        "get_table": {"table_id"},
    }
    for name, tool in built.items():
        schema = tool.spec.parameters
        assert set(schema["properties"]) == expected[name]
        assert schema["additionalProperties"] is False
        text = json.dumps(schema).lower()
        assert "study" not in text and "corpus" not in text and "version" not in text


async def test_search_reference_only_searches_the_bound_reference_version() -> None:
    store = make_store()
    outcome = await tools(store)["search_reference"].call({"query": "carpeting"})
    assert not outcome.refused
    assert store.calls == [("search_bm25", str(REF_VERSION))]
    returned = {h["chunk_id"] for h in outcome.payload["hits"]}
    assert str(CARPET_CHUNK) in returned
    assert returned <= {
        str(c.id) for c in store.chunks.values() if c.corpus_version_id == REF_VERSION
    }
    assert all(uuid.UUID(h["chunk_id"]) in outcome.chunk_ids for h in outcome.payload["hits"])


async def test_search_study_only_searches_the_bound_study_version() -> None:
    store = make_store()
    outcome = await tools(store)["search_study"].call({"query": "garage"})
    assert store.calls == [("search_bm25", str(STUDY_VERSION))]
    assert [h["chunk_id"] for h in outcome.payload["hits"]] == [str(STUDY_CHUNK)]


async def test_another_studys_chunk_is_refused_without_its_text() -> None:
    outcome = await tools()["get_chunk"].call({"chunk_id": str(OTHER_STUDY_CHUNK)})
    assert outcome.refused and outcome.chunk_ids == ()
    assert "outside this study" in outcome.payload["error"]
    assert "Confidential" not in json.dumps(outcome.payload)


async def test_in_scope_chunk_is_returned() -> None:
    outcome = await tools()["get_chunk"].call({"chunk_id": str(CARPET_CHUNK)})
    assert not outcome.refused and outcome.chunk_ids == (CARPET_CHUNK,)
    assert outcome.payload["chunk"]["page_start"] == 307


async def test_tables_are_scoped_too() -> None:
    built = tools()
    assert not (await built["get_table"].call({"table_id": str(REF_TABLE)})).refused
    other = await built["get_table"].call({"table_id": str(OTHER_TABLE)})
    assert other.refused and "outside" in other.payload["error"]
    missing = await built["get_table"].call({"table_id": str(uuid.uuid4())})
    assert missing.refused and "not found" in missing.payload["error"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "carpet", "study_id": "other-study"},
        {"query": "carpet", "corpus_version_id": str(OTHER_STUDY_VERSION)},
        {"query": "carpet", "k": 11},
        {"query": ""},
        {},
        ["carpet"],
    ],
)
async def test_scope_cannot_be_passed_as_an_argument(arguments: object) -> None:
    store = make_store()
    outcome = await tools(store)["search_reference"].call(arguments)
    assert outcome.refused and store.calls == []


async def test_missing_corpus_is_a_refusal() -> None:
    built = tools(reference=None, study=None)
    for name in ("search_reference", "search_study"):
        outcome = await built[name].call({"query": "carpet"})
        assert outcome.refused and "no " in outcome.payload["error"]
    chunk = await built["get_chunk"].call({"chunk_id": str(CARPET_CHUNK)})
    assert chunk.refused  # nothing is in scope without a bound version


async def test_k_limits_results() -> None:
    outcome = await tools()["search_reference"].call({"query": "carpeting structural", "k": 1})
    assert len(outcome.payload["hits"]) == 1
