"""Fabricated Azure DI analyses (raw ``as_dict()`` shape) for the sweep-system tests.

:func:`raw_layout` lays out pages of tokens on an 8.5 x 11 inch page: line ``i`` at
``y = 1 + 0.25 i`` inches, tokens left to right with 0.08 inch per character and 0.03 inch
between tokens (the word gap measured on the forms: 0.0029-0.0045 page widths). A token
``:selected:`` / ``:unselected:`` is a selection mark (in ``selectionMarks`` and in
``content``), anything else a word. ``content`` joins tokens with
spaces, lines with newlines and pages with a newline, and every word, mark and page carries
its span, as a real analysis does. :func:`token_box` gives the page-fraction box a token gets.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from basis.extraction.azure_di import AzureDiResult
from basis.llm.call import CallContext
from basis.llm.provider import Budget, ChatProvider
from basis.llm.recorder import MemoryRecorder
from evals.appraisal.systems.base import DocumentSources, ExtractionSettings, SystemEnv

WIDTH = 8.5
HEIGHT = 11.0
CHAR = 0.08
GAP = 0.03
LINE = 0.25
TOP = 1.0
TALL = 0.15
MARKS = (":selected:", ":unselected:")

Page = Sequence[str]  # lines; each line's tokens are split on spaces


def _x_positions(tokens: Sequence[str]) -> list[tuple[float, float]]:
    x = 0.5
    out = []
    for token in tokens:
        width = 0.12 if token in MARKS else CHAR * len(token)
        out.append((x, x + width))
        x += width + GAP
    return out


def _polygon(x0: float, y0: float, x1: float, y1: float) -> list[float]:
    return [x0, y0, x1, y0, x1, y1, x0, y1]


def token_box(line: int, tokens: Sequence[str], index: int) -> tuple[float, float, float, float]:
    """The page-fraction box :func:`raw_layout` gives token ``index`` of line ``line``."""
    x0, x1 = _x_positions(tokens)[index]
    y0 = TOP + LINE * line
    return (x0 / WIDTH, y0 / HEIGHT, x1 / WIDTH, (y0 + TALL) / HEIGHT)


def raw_layout(
    pages: Sequence[Page],
    *,
    key_value_pairs: Sequence[Mapping[str, Any]] = (),
    figures: Sequence[Mapping[str, Any]] = (),
    model_id: str = "prebuilt-layout",
    documents: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    content_parts: list[str] = []
    offset = 0
    raw_pages = []
    for number, lines in enumerate(pages, start=1):
        start = offset
        words: list[dict[str, Any]] = []
        marks: list[dict[str, Any]] = []
        raw_lines: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            tokens = line.split(" ") if line else []
            y0 = TOP + LINE * index
            line_start = offset
            for token, (x0, x1) in zip(tokens, _x_positions(tokens), strict=True):
                item = {
                    "polygon": _polygon(x0, y0, x1, y0 + TALL),
                    "span": {"offset": offset, "length": len(token)},
                    "confidence": 0.99,
                }
                if token in MARKS:
                    marks.append({"state": token.strip(":"), **item})
                else:
                    words.append({"content": token, **item})
                content_parts.append(token)
                offset += len(token)
                content_parts.append(" ")
                offset += 1
            if tokens:  # the trailing space becomes the line break
                content_parts[-1] = "\n"
            else:
                content_parts.append("\n")
                offset += 1
            text = " ".join(t for t in tokens if t not in MARKS)
            raw_lines.append(
                {
                    "content": text,
                    "polygon": _polygon(0.5, y0, 8.0, y0 + TALL),
                    "spans": [{"offset": line_start, "length": offset - line_start}],
                }
            )
        raw_pages.append(
            {
                "pageNumber": number,
                "width": WIDTH,
                "height": HEIGHT,
                "unit": "inch",
                "spans": [{"offset": start, "length": offset - start}],
                "words": words,
                "selectionMarks": marks,
                "lines": raw_lines,
            }
        )
    return {
        "apiVersion": "2024-11-30",
        "modelId": model_id,
        "content": "".join(content_parts),
        "pages": raw_pages,
        "keyValuePairs": list(key_value_pairs),
        "tables": [],
        "figures": list(figures),
        "documents": list(documents),
    }


def pdf_bytes(pages: Sequence[Page]) -> bytes:
    """A reportlab PDF printing each page's lines (marks left out), 18 pt apart from the top."""
    import io

    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=LETTER)
    for lines in pages:
        for i, line in enumerate(lines):
            text = " ".join(t for t in line.split(" ") if t not in MARKS)
            pdf.drawString(72, 720 - 18 * i, text)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def region_of(page: int, box: tuple[float, float, float, float]) -> dict[str, Any]:
    """A boundingRegions entry for a page-fraction box (inches on the fabricated page)."""
    x0, y0, x1, y1 = box
    return {
        "pageNumber": page,
        "polygon": _polygon(x0 * WIDTH, y0 * HEIGHT, x1 * WIDTH, y1 * HEIGHT),
    }


class RawClient:
    """A layout client with a ``raw`` analysis (what the recording and replay clients are)."""

    name = "azure_di"

    def __init__(self, raw: Mapping[str, Any], *, keep_raw: bool = True) -> None:
        from basis.extraction.azure_di import from_sdk
        from evals.appraisal.recordings import rebuild_sdk_result

        self._raw = dict(raw)
        self.raw: dict[str, Any] | None = None
        self._keep = keep_raw
        self.calls = 0
        self.result: AzureDiResult = from_sdk(rebuild_sdk_result(self._raw))

    async def analyze(self, pdf_bytes: bytes) -> AzureDiResult:
        self.calls += 1
        self.raw = self._raw if self._keep else None
        return self.result


class RawModel:
    """A 1004 client answering one raw analysis."""

    def __init__(self, raw: Mapping[str, Any]) -> None:
        self.raw = dict(raw)
        self.calls = 0

    async def analyze(self, pdf_bytes: bytes) -> dict[str, Any]:
        self.calls += 1
        return self.raw


SETTINGS = ExtractionSettings(
    text_model="gpt-6-luna",
    reasoning_effort="low",
    output_budget=8000,
    fallback_scope="missing",
    context_chars=60_000,
    review_threshold=0.9,
)


def make_env(
    *,
    layout: RawClient | None = None,
    mortgage: RawModel | None = None,
    provider: ChatProvider | None = None,
    pdf: bytes = b"%PDF-1.4 fabricated",
    budget_usd: float = 5.0,
) -> SystemEnv:
    import uuid

    return SystemEnv(
        pdf=pdf,
        doc_id="doc_a",
        call=CallContext(
            study_id=uuid.uuid4(),
            run_id=None,
            activity="extract_fields",
            budget=Budget(remaining_usd=budget_usd),
            recorder=MemoryRecorder(),
        ),
        provider=provider,
        sources=DocumentSources(layout, mortgage),
        extraction=SETTINGS,
    )
