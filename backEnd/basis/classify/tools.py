"""Tools the residual classifier may call, bound to one study's corpora at construction.

``build_tools(study_id, reference_version_id, study_version_id, store)`` returns
``search_reference``, ``search_study``, ``get_chunk`` and ``get_table``. The study and the
corpus versions are captured in each tool's closure; the JSON schema the model sees
exposes only ``query``/``k`` or ``chunk_id``/``table_id`` (``additionalProperties`` is
false), so the model can never name a study or a corpus. ``get_chunk`` and ``get_table``
refuse an id from any other corpus version without revealing its text, which is how a
request for another study's evidence fails (BUILD_PLAN 6.6 ``test_tool_scope``).

A tool never raises into the loop: malformed arguments, unknown ids and scope violations
come back as a JSON ``{"error": ...}`` payload with ``refused`` set, and the loop records
them in its trace. Results are JSON objects (sorted keys) the loop sends as the content
of a ``Message(role="tool", name=<tool>)``: the legacy W5 defect (no tool name, Python
``repr`` content) cannot recur.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from basis.classify.store_protocol import ChunkRecord, ChunkStore, HitRecord
from basis.llm.provider import ToolSpec

SEARCH_REFERENCE: Final = "search_reference"
SEARCH_STUDY: Final = "search_study"
GET_CHUNK: Final = "get_chunk"
GET_TABLE: Final = "get_table"
TOOL_NAMES: Final = (SEARCH_REFERENCE, SEARCH_STUDY, GET_CHUNK, GET_TABLE)
MAX_K: Final = 10
DEFAULT_K: Final = 5
MAX_TEXT_CHARS: Final = 1500

Handler = Callable[[BaseModel], Awaitable["ToolOutcome"]]
SearchFn = Callable[[uuid.UUID, str, int], Awaitable[Sequence[HitRecord]]]


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=500, description="What to look for")
    k: int = Field(default=DEFAULT_K, ge=1, le=MAX_K, description="How many passages")


class ChunkArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1, max_length=64, description="A chunk_id from a search")


class TableArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table_id: str = Field(min_length=1, max_length=64, description="A table_id from a search")


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    payload: dict[str, Any]
    refused: bool = False
    chunk_ids: tuple[uuid.UUID, ...] = ()  # chunks this call put in front of the model


@dataclass(frozen=True, slots=True)
class BoundTool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[ToolOutcome]] = field(repr=False)

    @property
    def parameters(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        schema["additionalProperties"] = False
        return schema

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)

    async def call(self, arguments: Any) -> ToolOutcome:
        """Validate ``arguments`` and run the tool; bad input is a refusal, not an error."""
        if not isinstance(arguments, dict):
            return refusal(f"{self.name} expects a JSON object of arguments")
        try:
            args = self.args_model.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
                for e in exc.errors()
            )
            return refusal(f"invalid arguments for {self.name}: {problems}")
        return await self.handler(args)


def refusal(message: str) -> ToolOutcome:
    return ToolOutcome(payload={"error": message}, refused=True)


def _hit_json(hit: HitRecord) -> dict[str, Any]:
    return {
        "chunk_id": str(hit.chunk_id),
        "doc_id": hit.doc_id,
        "page_start": hit.page_start,
        "page_end": hit.page_end,
        "score": round(float(hit.score), 4),
        "table_id": None if hit.table_id is None else str(hit.table_id),
        "text": hit.text[:MAX_TEXT_CHARS],
    }


def _chunk_json(chunk: ChunkRecord) -> dict[str, Any]:
    return {
        "chunk_id": str(chunk.id),
        "doc_id": chunk.doc_id,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "table_id": None if chunk.table_id is None else str(chunk.table_id),
        "text": chunk.text[:MAX_TEXT_CHARS],
    }


def _parse_id(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def build_tools(
    study_id: str,
    reference_version_id: uuid.UUID | None,
    study_version_id: uuid.UUID | None,
    store: ChunkStore,
    *,
    search: SearchFn | None = None,
) -> list[BoundTool]:
    """The four scoped tools for one study. ``search`` defaults to the store's BM25.

    ``study_id`` is bound for traceability only; it is never a tool parameter and no
    tool accepts a corpus or study identifier.
    """
    run_search: SearchFn = search if search is not None else store.search_bm25
    allowed = frozenset(v for v in (reference_version_id, study_version_id) if v is not None)

    def searcher(version: uuid.UUID | None, corpus: str) -> Handler:
        async def handler(args: BaseModel) -> ToolOutcome:
            assert isinstance(args, SearchArgs)
            if version is None:
                return refusal(f"no {corpus} corpus is available for this study")
            hits = list(await run_search(version, args.query, args.k))[: args.k]
            return ToolOutcome(
                payload={"corpus": corpus, "hits": [_hit_json(h) for h in hits]},
                chunk_ids=tuple(h.chunk_id for h in hits),
            )

        return handler

    async def get_chunk(args: BaseModel) -> ToolOutcome:
        assert isinstance(args, ChunkArgs)
        chunk_id = _parse_id(args.chunk_id)
        if chunk_id is None:
            return refusal(f"chunk_id {args.chunk_id!r} is not a valid id")
        chunk = await store.get_chunk(chunk_id)
        if chunk is None:
            return refusal(f"chunk {chunk_id} not found")
        if chunk.corpus_version_id not in allowed:
            return refusal(f"chunk {chunk_id} is outside this study's corpora")
        return ToolOutcome(payload={"chunk": _chunk_json(chunk)}, chunk_ids=(chunk.id,))

    async def get_table(args: BaseModel) -> ToolOutcome:
        assert isinstance(args, TableArgs)
        table_id = _parse_id(args.table_id)
        if table_id is None:
            return refusal(f"table_id {args.table_id!r} is not a valid id")
        table = await store.get_table(table_id)
        if table is None:
            return refusal(f"table {table_id} not found")
        if table.corpus_version_id not in allowed:
            return refusal(f"table {table_id} is outside this study's corpora")
        return ToolOutcome(
            payload={
                "table": {
                    "table_id": str(table.id),
                    "doc_id": table.doc_id,
                    "page": table.page,
                    "headers": table.headers,
                    "rows": table.rows,
                    "markdown": table.markdown,
                }
            }
        )

    return [
        BoundTool(
            SEARCH_REFERENCE,
            "Search the IRS reference corpus (publications, revenue procedures, the cost "
            "segregation audit techniques guide). Returns passages with chunk ids.",
            SearchArgs,
            searcher(reference_version_id, "reference"),
        ),
        BoundTool(
            SEARCH_STUDY,
            "Search this study's own documents (the appraisal). Returns passages with chunk ids.",
            SearchArgs,
            searcher(study_version_id, "study"),
        ),
        BoundTool(
            GET_CHUNK, "Fetch the full text of one passage by chunk_id.", ChunkArgs, get_chunk
        ),
        BoundTool(
            GET_TABLE, "Fetch a whole table (headers and rows) by table_id.", TableArgs, get_table
        ),
    ]
