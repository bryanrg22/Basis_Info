"""Shared helpers for the behavioral evals (BUILD_PLAN 6.6): real loop, scripted model.

These are PR-gate tests. The model is a :class:`ScriptedProvider`, so each test fixes what
the "model" does (including misbehaving) and asserts what the pipeline does with it: what
reaches the model, which tools run, what is refused, and what is written.

The appraisal fixtures are read page by page with PyMuPDF into a study corpus whose chunk
ids depend only on the page number, so a clean and an injected document produce the same
ids and the same scripted tool calls apply to both.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Final

import pymupdf
from classifytest_support import STUDY_VERSION, reference_chunks

from basis.classify.store_protocol import InMemoryChunkStore, StoredChunk
from basis.llm.provider import ChatRequest

FIXTURES: Final = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic"
CLEAN_APPRAISAL: Final = FIXTURES / "urar_sample.pdf"
INJECTED_APPRAISAL: Final = FIXTURES / "adversarial" / "urar_injected.pdf"
_PAGE_NS: Final = uuid.UUID("6f1d2f2e-8a8e-4a43-9d0d-2b7c0c1a0e01")


def adversarial_cases() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "adversarial" / "cases.json").read_text())
    return data


def injected_strings() -> list[str]:
    return [i["text"] for case in adversarial_cases()["cases"] for i in case["injections"]]


def page_chunk_id(page: int) -> uuid.UUID:
    return uuid.uuid5(_PAGE_NS, f"page-{page}")


def pdf_pages(path: Path) -> list[str]:
    with pymupdf.open(path) as document:
        return [page.get_text() for page in document]


def appraisal_store(path: Path) -> InMemoryChunkStore:
    """The reference corpus plus the appraisal as one study chunk per page."""
    pages = [
        StoredChunk(
            id=page_chunk_id(n),
            corpus_version_id=STUDY_VERSION,
            doc_id="appraisal",
            page_start=n,
            page_end=n,
            chunk_key=f"appraisal:{n}:0",
            text=text,
        )
        for n, text in enumerate(pdf_pages(path), start=1)
    ]
    return InMemoryChunkStore.of([*reference_chunks(), *pages])


def request_texts(request: ChatRequest) -> dict[str, list[str]]:
    """Every text the model received, by role (``system`` is the system prompt)."""
    texts: dict[str, list[str]] = {"system": [request.system]}
    for message in request.messages:
        texts.setdefault(message.role, []).append(message.content)
    return texts
