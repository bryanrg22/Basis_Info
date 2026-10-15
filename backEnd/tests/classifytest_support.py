"""Helpers for the classifier tests and behavioral evals: a tiny corpus, scripted turns.

Imported as a top-level module (``tests/`` is on ``sys.path``, see ``dbtest_support``).
The corpus is synthetic text written for these tests; three corpus versions exist: the
reference corpus, this study's corpus, and another study's corpus (for scope tests).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Final

from basis.classify.router import ClassifyContext
from basis.classify.schemas import CitationClaim, Classification, ComponentView
from basis.classify.store_protocol import InMemoryChunkStore, StoredChunk, StoredTable
from basis.llm.provider import Budget, ScriptedProvider, ScriptedTurn, ToolCall

REF_VERSION: Final = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
STUDY_VERSION: Final = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
OTHER_STUDY_VERSION: Final = uuid.UUID("00000000-0000-4000-8000-0000000000c1")

CARPET_CHUNK: Final = uuid.UUID("00000000-0000-4000-8000-000000000001")
ASSET_CLASS_CHUNK: Final = uuid.UUID("00000000-0000-4000-8000-000000000002")
STRUCTURAL_CHUNK: Final = uuid.UUID("00000000-0000-4000-8000-000000000003")
STUDY_CHUNK: Final = uuid.UUID("00000000-0000-4000-8000-000000000010")
OTHER_STUDY_CHUNK: Final = uuid.UUID("00000000-0000-4000-8000-000000000020")
REF_TABLE: Final = uuid.UUID("00000000-0000-4000-8000-0000000000f1")
OTHER_TABLE: Final = uuid.UUID("00000000-0000-4000-8000-0000000000f2")

STRUCTURAL_TEXT: Final = (
    "Structural components include walls, partitions, floors and ceilings, windows and "
    "doors, central air conditioning and heating systems, plumbing and plumbing fixtures. "
    "A garage door is a door of the building and is a structural component."
)
GARAGE_EXCERPT: Final = "A garage door is a door of the building"


def reference_chunks() -> list[StoredChunk]:
    return [
        StoredChunk(
            id=CARPET_CHUNK,
            corpus_version_id=REF_VERSION,
            doc_id="IRS_IRS_COST_SEG_ATG__2024",
            page_start=415,
            page_end=415,
            chunk_key="IRS_IRS_COST_SEG_ATG__2024:415:0",
            text="Floor coverings. Carpeting - 5-year recovery when it is tacked down "
            "and removable without damage.",
        ),
        StoredChunk(
            id=ASSET_CLASS_CHUNK,
            corpus_version_id=REF_VERSION,
            doc_id="IRS_REV_PROC_87_56",
            page_start=12,
            page_end=12,
            chunk_key="IRS_REV_PROC_87_56:12:0",
            text="Distributive trades and services. Asset class 57.0 includes carpeting "
            "and other furnishings used in the business.",
        ),
        StoredChunk(
            id=STRUCTURAL_CHUNK,
            corpus_version_id=REF_VERSION,
            doc_id="TREAS_REG_1_48_1",
            page_start=3,
            page_end=3,
            chunk_key="TREAS_REG_1_48_1:3:0",
            text=STRUCTURAL_TEXT,
        ),
    ]


def study_chunks() -> list[StoredChunk]:
    return [
        StoredChunk(
            id=STUDY_CHUNK,
            corpus_version_id=STUDY_VERSION,
            doc_id="appraisal",
            page_start=2,
            page_end=2,
            chunk_key="appraisal:2:0",
            text="The subject has an attached two-car garage with an overhead garage door.",
        ),
        StoredChunk(
            id=OTHER_STUDY_CHUNK,
            corpus_version_id=OTHER_STUDY_VERSION,
            doc_id="appraisal",
            page_start=1,
            page_end=1,
            chunk_key="other:1:0",
            text="Confidential: another client's appraisal says the owner is selling.",
        ),
    ]


def make_store(*, extra: Sequence[StoredChunk] = ()) -> InMemoryChunkStore:
    store = InMemoryChunkStore.of(
        [*reference_chunks(), *study_chunks(), *extra],
        [
            StoredTable(REF_TABLE, REF_VERSION, "IRS_REV_PROC_87_56", 12, ["class"], [["57.0"]]),
            StoredTable(OTHER_TABLE, OTHER_STUDY_VERSION, "appraisal", 1, ["x"], [["y"]]),
        ],
    )
    return store


def component(label: str = "garage_door", **changes: Any) -> ComponentView:
    values: dict[str, Any] = {
        "component_id": "11111111-1111-4111-8111-111111111111",
        "label": label,
        "room_type": "garage",
        "room_name": "Garage",
    }
    values.update(changes)
    return ComponentView(**values)


def classification(
    *,
    citations: Sequence[tuple[uuid.UUID | str, str]] = ((STRUCTURAL_CHUNK, GARAGE_EXCERPT),),
    section: str = "1250",
    bucket: str = "y27_5",
    confidence: float = 0.8,
    **changes: Any,
) -> Classification:
    values: dict[str, Any] = {
        "category": "Garage door",
        "section": section,
        "bucket": bucket,
        "rationale": "A door of the building is a structural component.",
        "citations": [CitationClaim(chunk_id=str(c), excerpt=e) for c, e in citations],
        "confidence": confidence,
    }
    values.update(changes)
    return Classification(**values)


def call(name: str, arguments: Any, call_id: str = "call-1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def tools_turn(*calls: ToolCall) -> ScriptedTurn:
    return ScriptedTurn(tool_calls=tuple(calls))


def answer_turn(answer: Classification | None = None) -> ScriptedTurn:
    return ScriptedTurn(parsed=answer if answer is not None else classification())


def happy_turns() -> list[ScriptedTurn]:
    """Search the reference corpus, then answer citing the passage it returned."""
    return [
        tools_turn(call("search_reference", {"query": "garage door structural component", "k": 3})),
        answer_turn(),
    ]


def make_ctx(
    provider: ScriptedProvider | None = None,
    *,
    store: InMemoryChunkStore | None = None,
    budget_usd: float = 1.0,
    **changes: Any,
) -> ClassifyContext:
    values: dict[str, Any] = {
        "study_id": "study-1",
        "property_class": "residential_rental",
        "budget": Budget(remaining_usd=budget_usd),
        "store": store if store is not None else make_store(),
        "reference_version_id": REF_VERSION,
        "study_version_id": STUDY_VERSION,
        "provider": provider,
    }
    values.update(changes)
    return ClassifyContext(**values)
