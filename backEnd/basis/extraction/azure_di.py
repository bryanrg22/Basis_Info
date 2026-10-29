"""Azure AI Document Intelligence (``prebuilt-layout`` with key-value pairs).

:class:`AzureDiResult` is our own JSON-serialisable copy of what the pipeline uses from
the SDK's ``AnalyzeResult`` (page lines, key-value pairs with their confidence and page,
tables as cells). It is what ``extractions.raw`` stores and what the test fixtures under
``tests/fixtures/synthetic/azure_di/`` contain, so tests never need the SDK or a network
and a stored extraction can be re-mapped later without paying for a second analysis.

The client is the async SDK client with ``AnalyzeDocumentRequest(bytes_source=...)``
(the legacy extractor ran the sync client in a thread over a file path) and the
``keyValuePairs`` add-on feature, which ``prebuilt-layout`` needs to return form
key-value pairs. Errors are mapped to :mod:`basis.llm.errors` like the model providers:
429 -> :class:`ProviderRateLimited`, 5xx/timeout/connection -> :class:`ProviderUnavailable`
(retried by the activity), 401/403 -> :class:`ProviderAccountError` (a wrong or revoked
key, or a quota such as the F0 tier's call volume: not retried, it stops the stage), other
4xx -> :class:`ProviderBadResponse` (a document the service cannot read). The overall
analysis is bounded by ``settings.azure_di_timeout_seconds`` because the SDK poller
otherwise waits indefinitely.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field

from basis.config import Settings
from basis.llm.errors import (
    ProviderAccountError,
    ProviderBadResponse,
    ProviderError,
    ProviderRateLimited,
    ProviderUnavailable,
)

logger = logging.getLogger("basis.extraction.azure_di")

MODEL_ID: Final = "prebuilt-layout"
PROVIDER: Final = "azure_di"
# The longest wait between two polls of an analysis. Azure DI can answer a poll with a
# Retry-After far beyond the analysis time (measured: 1,587 s on a 32-page form it finished in
# about 60 s), and the SDK's poller honours it, so the client timeout fires first.
MAX_POLL_DELAY_S: Final = 5.0


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# A box as page fractions ``(x0, y0, x1, y1)``, origin top-left (polygon x / page width,
# y / page height): what the review page scales to the rendered page to highlight a value.
BBox = tuple[float, float, float, float]


class AzureDiWord(_Frozen):
    text: str
    bbox: BBox
    offset: int = -1  # into ``AzureDiResult.content``; -1 when the analysis has no span


class AzureDiMark(_Frozen):
    """A selection mark (checkbox): ``:selected:`` / ``:unselected:`` in ``content``."""

    selected: bool
    bbox: BBox
    offset: int = -1


class AzureDiRegion(_Frozen):
    page: int = Field(ge=1)
    bbox: BBox


class AzureDiPage(_Frozen):
    """A page's lines (no selection marks: what the pipeline maps) and, for highlighting
    and checkbox reading, its geometry. Results stored before the geometry existed validate
    with the geometry empty."""

    page_number: int = Field(ge=1)
    lines: tuple[str, ...] = ()
    width: float = 0.0
    height: float = 0.0
    unit: str | None = None
    words: tuple[AzureDiWord, ...] = ()
    marks: tuple[AzureDiMark, ...] = ()
    # The page's slices of ``content`` as ``(offset, length)``: its text with the marks.
    spans: tuple[tuple[int, int], ...] = ()
    figure_fraction: float = Field(default=0.0, ge=0.0, le=1.0)  # page area inside figures


class AzureDiKeyValue(_Frozen):
    key: str
    value: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    page: int | None = Field(default=None, ge=1)
    key_region: AzureDiRegion | None = None
    value_region: AzureDiRegion | None = None


class AzureDiCell(_Frozen):
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    content: str = ""
    kind: str | None = None  # "columnHeader", "rowHeader", "content", ...


class AzureDiTable(_Frozen):
    page: int | None = Field(default=None, ge=1)
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    cells: tuple[AzureDiCell, ...] = ()

    def rows(self) -> list[list[str]]:
        """The table as a dense grid of cell texts (missing cells are ``""``)."""
        grid = [["" for _ in range(self.column_count)] for _ in range(self.row_count)]
        for cell in self.cells:
            if cell.row_index < self.row_count and cell.column_index < self.column_count:
                grid[cell.row_index][cell.column_index] = cell.content
        return grid


class AzureDiResult(_Frozen):
    """What the extraction pipeline needs from one ``prebuilt-layout`` analysis."""

    model_id: str = MODEL_ID
    api_version: str | None = None
    content: str = ""
    pages: tuple[AzureDiPage, ...] = ()
    key_value_pairs: tuple[AzureDiKeyValue, ...] = ()
    tables: tuple[AzureDiTable, ...] = ()
    synthetic: bool = False  # True for hand-built fixtures, never for a real analysis

    def page_texts(self) -> list[str]:
        """One string per page (lines joined by newlines), in page order."""
        return ["\n".join(page.lines) for page in sorted(self.pages, key=lambda p: p.page_number)]

    def page_text_with_marks(self, page: AzureDiPage) -> str:
        """``page``'s slices of ``content``: its text with the ``:selected:`` /
        ``:unselected:`` marks in reading order (the lines when no spans were kept)."""
        if self.content and page.spans:
            return "".join(self.content[o : o + n] for o, n in page.spans)
        return "\n".join(page.lines)

    @property
    def mean_confidence(self) -> float | None:
        values = [kv.confidence for kv in self.key_value_pairs]
        return sum(values) / len(values) if values else None


def _page_of(element: Any) -> int | None:
    regions = getattr(element, "bounding_regions", None) or ()
    for region in regions:
        number = getattr(region, "page_number", None)
        if number:
            return int(number)
    return None


def _kind(cell: Any) -> str | None:
    kind = getattr(cell, "kind", None)
    if kind is None:
        return None
    return str(getattr(kind, "value", kind))


def bbox_of(polygon: Any, width: float, height: float) -> BBox | None:
    """The bounding box of an Azure polygon ``[x1, y1, ..., x4, y4]`` as page fractions."""
    if not polygon or len(polygon) < 4 or width <= 0 or height <= 0:
        return None
    xs = [float(v) for v in polygon[0::2]]
    ys = [float(v) for v in polygon[1::2]]

    def clamp(value: float) -> float:
        return min(1.0, max(0.0, value))

    return (
        clamp(min(xs) / width),
        clamp(min(ys) / height),
        clamp(max(xs) / width),
        clamp(max(ys) / height),
    )


def _offset(element: Any) -> int:
    span = getattr(element, "span", None)
    offset = getattr(span, "offset", None)
    return int(offset) if offset is not None else -1


def _area(box: BBox) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _region_of(element: Any, sizes: dict[int, tuple[float, float]]) -> AzureDiRegion | None:
    for bounding in getattr(element, "bounding_regions", None) or ():
        number = int(getattr(bounding, "page_number", 0) or 0)
        width, height = sizes.get(number, (0.0, 0.0))
        box = bbox_of(getattr(bounding, "polygon", None), width, height)
        if box is not None:
            return AzureDiRegion(page=number, bbox=box)
    return None


def _page_of_sdk(page: Any, figures: dict[int, list[BBox]]) -> AzureDiPage:
    number = int(page.page_number)
    width = float(getattr(page, "width", None) or 0.0)
    height = float(getattr(page, "height", None) or 0.0)
    words = tuple(
        AzureDiWord(text=str(word.content or ""), bbox=box, offset=_offset(word))
        for word in (getattr(page, "words", None) or ())
        if (box := bbox_of(getattr(word, "polygon", None), width, height)) is not None
    )
    marks = tuple(
        AzureDiMark(
            selected=str(getattr(mark.state, "value", mark.state) or "").lower() == "selected",
            bbox=box,
            offset=_offset(mark),
        )
        for mark in (getattr(page, "selection_marks", None) or ())
        if (box := bbox_of(getattr(mark, "polygon", None), width, height)) is not None
    )
    spans = tuple(
        (int(span.offset), int(span.length)) for span in (getattr(page, "spans", None) or ())
    )
    return AzureDiPage(
        page_number=number,
        lines=tuple(line.content for line in (page.lines or ())),
        width=width,
        height=height,
        unit=str(getattr(page.unit, "value", page.unit)) if getattr(page, "unit", None) else None,
        words=words,
        marks=marks,
        spans=spans,
        figure_fraction=min(1.0, sum(_area(b) for b in figures.get(number, ()))),
    )


def from_sdk(result: Any) -> AzureDiResult:
    """Copy the parts of an SDK ``AnalyzeResult`` the pipeline uses, with the geometry the
    review page highlights from (word, mark and key-value boxes as page fractions)."""
    sizes = {
        int(page.page_number): (
            float(getattr(page, "width", None) or 0.0),
            float(getattr(page, "height", None) or 0.0),
        )
        for page in (result.pages or ())
    }
    figures: dict[int, list[BBox]] = {}
    for figure in getattr(result, "figures", None) or ():
        for bounding in getattr(figure, "bounding_regions", None) or ():
            number = int(getattr(bounding, "page_number", 0) or 0)
            width, height = sizes.get(number, (0.0, 0.0))
            box = bbox_of(getattr(bounding, "polygon", None), width, height)
            if box is not None:
                figures.setdefault(number, []).append(box)
    pages = tuple(_page_of_sdk(page, figures) for page in (result.pages or ()))
    pairs = []
    for pair in result.key_value_pairs or ():
        if pair.key is None or not (pair.key.content or "").strip():
            continue
        value = pair.value.content if pair.value is not None else None
        page = _page_of(pair.key) or (_page_of(pair.value) if pair.value is not None else None)
        pairs.append(
            AzureDiKeyValue(
                key=pair.key.content,
                value=value,
                confidence=float(pair.confidence or 0.0),
                page=page,
                key_region=_region_of(pair.key, sizes),
                value_region=_region_of(pair.value, sizes) if pair.value is not None else None,
            )
        )
    tables = tuple(
        AzureDiTable(
            page=_page_of(table),
            row_count=int(table.row_count),
            column_count=int(table.column_count),
            cells=tuple(
                AzureDiCell(
                    row_index=int(cell.row_index),
                    column_index=int(cell.column_index),
                    content=cell.content or "",
                    kind=_kind(cell),
                )
                for cell in (table.cells or ())
            ),
        )
        for table in (result.tables or ())
    )
    return AzureDiResult(
        model_id=str(getattr(result, "model_id", None) or MODEL_ID),
        api_version=getattr(result, "api_version", None),
        content=result.content or "",
        pages=pages,
        key_value_pairs=tuple(pairs),
        tables=tables,
    )


class AzureDiClient(Protocol):
    async def analyze(self, pdf_bytes: bytes) -> AzureDiResult: ...


def map_azure_error(exc: Exception) -> ProviderError:
    from azure.core.exceptions import HttpResponseError, ServiceRequestError

    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, asyncio.TimeoutError):
        return ProviderUnavailable(f"analysis timed out: {exc}", provider=PROVIDER)
    if isinstance(exc, HttpResponseError):
        status = exc.status_code
        if status in (401, 403):
            error = getattr(exc, "error", None)
            return ProviderAccountError(
                str(getattr(exc, "message", None) or exc),
                provider=PROVIDER,
                status=status,
                code=getattr(error, "code", None),
            )
        if status == 429:
            return ProviderRateLimited(str(exc), provider=PROVIDER, status=429)
        if status is not None and status >= 500:
            return ProviderUnavailable(str(exc), provider=PROVIDER, status=status)
        return ProviderBadResponse(str(exc), provider=PROVIDER, status=status)
    if isinstance(exc, ServiceRequestError):
        return ProviderUnavailable(f"connection failed: {exc}", provider=PROVIDER)
    return ProviderUnavailable(f"{type(exc).__name__}: {exc}", provider=PROVIDER)


def capped_polling(endpoint: str) -> Any:
    """The SDK's own poller (``AsyncLROBasePolling``, as ``begin_analyze_document`` builds it)
    with the server's Retry-After capped at :data:`MAX_POLL_DELAY_S`."""
    from azure.core.polling.async_base_polling import AsyncLROBasePolling

    class CappedPolling(AsyncLROBasePolling[Any, Any, Any]):
        def _extract_delay(self) -> float:
            return float(min(super()._extract_delay(), MAX_POLL_DELAY_S))

    return CappedPolling(1, path_format_arguments={"endpoint": endpoint.rstrip("/")})


class AzureDocumentIntelligence:
    """The real client (``azure.ai.documentintelligence.aio``)."""

    name = PROVIDER

    def __init__(self, endpoint: str, key: str, *, timeout_s: float) -> None:
        self._endpoint = endpoint
        self._key = key
        self._timeout_s = timeout_s

    async def analyze(self, pdf_bytes: bytes) -> AzureDiResult:
        return from_sdk(await self.analyze_raw(pdf_bytes))

    async def analyze_raw(self, pdf_bytes: bytes) -> Any:
        """The SDK ``AnalyzeResult`` itself, before :func:`from_sdk` (the appraisal eval
        harness records it with ``result.as_dict()``). Same request, timeout and error
        mapping as :meth:`analyze`."""
        from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
        from azure.ai.documentintelligence.models import (
            AnalyzeDocumentRequest,
            DocumentAnalysisFeature,
        )
        from azure.core.credentials import AzureKeyCredential

        async def run() -> Any:
            async with DocumentIntelligenceClient(
                endpoint=self._endpoint, credential=AzureKeyCredential(self._key)
            ) as client:
                poller = await client.begin_analyze_document(
                    MODEL_ID,
                    AnalyzeDocumentRequest(bytes_source=pdf_bytes),
                    features=[DocumentAnalysisFeature.KEY_VALUE_PAIRS],
                    polling=capped_polling(self._endpoint),
                )
                return await poller.result()

        try:
            result = await asyncio.wait_for(run(), timeout=self._timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise map_azure_error(exc) from exc
        return result

    @classmethod
    def from_settings(cls, settings: Settings) -> AzureDocumentIntelligence | None:
        key = settings.azure_document_intelligence_key
        endpoint = settings.azure_document_intelligence_endpoint
        if not endpoint or key is None or not key.get_secret_value():
            return None
        return cls(endpoint, key.get_secret_value(), timeout_s=settings.azure_di_timeout_seconds)
