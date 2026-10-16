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
from basis.classify.verify import excerpt_supported
from basis.domain.rules import RULES
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
            page_start=307,
            page_end=307,
            chunk_key="IRS_IRS_COST_SEG_ATG__2024:307:0",
            text="Floor coverings. Carpeting - 5-year recovery when it is tacked down "
            "and removable without damage. All vinyl composition tile (VCT), sheet vinyl, "
            "and carpeting will be treated as not permanently attached and not intended to "
            "be permanent.",
        ),
        StoredChunk(
            id=ASSET_CLASS_CHUNK,
            corpus_version_id=REF_VERSION,
            doc_id="IRS_IRS_COST_SEG_ATG__2024",
            page_start=297,
            page_end=297,
            chunk_key="IRS_IRS_COST_SEG_ATG__2024:297:0",
            text="Distributive trades and services. Asset Class 57.0 - Distributive Trades "
            "and Services applies to most of the § 1245 property used with RRP, which is "
            "recovered over 5 years.",
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


RULES_CHUNK_NS: Final = uuid.UUID("00000000-0000-4000-8000-00000000c0de")


def rules_reference_chunks() -> list[StoredChunk]:
    """One reference chunk per ``(doc_id, page)`` the rules table cites, holding every
    excerpt cited on that page, so each rule citation resolves in tests the way it must
    against the real corpus. Excerpts the hand-written chunks above already support are
    left to them (a page with such a chunk gets a second chunk only for the rest)."""
    hand = {(c.doc_id, c.page_start): c.text for c in reference_chunks()}
    excerpts: dict[tuple[str, int], list[str]] = {}
    for rule in RULES.values():
        for claim in rule.citations:
            key = (claim.doc_id, claim.page)
            if key in hand and excerpt_supported(claim.excerpt, hand[key]):
                continue
            page = excerpts.setdefault(key, [])
            if claim.excerpt not in page:
                page.append(claim.excerpt)
    chunks: list[StoredChunk] = []
    for (doc_id, page_no), texts in sorted(excerpts.items()):
        chunk_key = f"{doc_id}:{page_no}:{1 if (doc_id, page_no) in hand else 0}"
        chunks.append(
            StoredChunk(
                id=uuid.uuid5(RULES_CHUNK_NS, chunk_key),
                corpus_version_id=REF_VERSION,
                doc_id=doc_id,
                page_start=page_no,
                page_end=page_no,
                chunk_key=chunk_key,
                text=". ".join(texts) + ".",
            )
        )
    return chunks


def lifecycle_reference_chunks() -> list[StoredChunk]:
    """The hand-written chunks plus one per rules-table citation page (lifecycle tests)."""
    return [*reference_chunks(), *rules_reference_chunks()]


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
