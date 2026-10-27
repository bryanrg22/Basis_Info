"""Shapes shared by the extraction modules: candidates, fields, issues, the result."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from basis.extraction.cleaners import CleanValue, clean_field
from basis.extraction.field_mappings import FIELD_BY_KEY

Source = Literal["azure", "regex", "table", "llm", "manual"]
Severity = Literal["error", "warning"]


class Region(BaseModel):
    """Where a value is printed: a page and a box as page fractions ``(x0, y0, x1, y1)``,
    origin top-left (what the review page highlights; :mod:`basis.extraction.regions`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float]


class Candidate(BaseModel):
    """One raw value for a field as one extractor saw it, before cleaning."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    raw_text: str
    page: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source: Source
    # The value's own box when the extractor knows it (an Azure DI key-value pair's value).
    regions: tuple[Region, ...] = ()


class ExtractedField(BaseModel):
    """A cleaned field: what one ``extraction_fields`` row stores."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    raw_text: str | None
    value_text: str | None
    value_num: Decimal | None = None
    value_date: date | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source: Source
    page: int | None = None
    parse_error: str | None = None
    corroborated_by: tuple[str, ...] = ()  # other sources that read the same value
    regions: tuple[Region, ...] = ()  # where the value is printed (empty: not located)

    @property
    def section(self) -> str:
        return self.key.split(".", 1)[0]

    @property
    def field(self) -> str:
        return self.key.split(".", 1)[1]

    @property
    def is_empty(self) -> bool:
        return self.value_text is None and self.value_num is None and self.value_date is None

    @property
    def typed(self) -> str | Decimal | date | None:
        if self.value_date is not None:
            return self.value_date
        if self.value_num is not None:
            return self.value_num
        return self.value_text

    @property
    def critical(self) -> bool:
        spec = FIELD_BY_KEY.get(self.key)
        return spec is not None and spec.critical

    @classmethod
    def from_candidate(cls, candidate: Candidate) -> ExtractedField:
        value: CleanValue = clean_field(candidate.key, candidate.raw_text)
        return cls(
            key=candidate.key,
            raw_text=candidate.raw_text,
            value_text=value.text,
            value_num=value.num,
            value_date=value.day,
            confidence=candidate.confidence,
            source=candidate.source,
            page=candidate.page,
            parse_error=value.error,
            regions=candidate.regions,
        )


class ValidationIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: str
    message: str
    keys: tuple[str, ...] = ()
    severity: Severity = "error"


class ExtractionResult(BaseModel):
    """Everything ``extract_appraisal`` produced for one document."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    method: str  # "azure_di+regex" | "regex" (Azure DI not configured) | ...
    page_count: int
    fields: dict[str, ExtractedField] = Field(default_factory=dict)
    issues: list[ValidationIssue] = Field(default_factory=list)
    overall_confidence: float = 0.0
    needs_review: bool = True
    review_reasons: list[str] = Field(default_factory=list)
    comparables: list[dict[str, Any]] = Field(default_factory=list)
    fallback: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    def sections(self) -> dict[str, dict[str, Any]]:
        """The URAR sections shape: ``{section: {field: typed value}}`` (JSON-friendly)."""
        shaped: dict[str, dict[str, Any]] = {}
        for key in sorted(self.fields):
            item = self.fields[key]
            value: Any = item.typed
            if isinstance(value, Decimal):
                value = int(value) if value == value.to_integral_value() else float(value)
            elif isinstance(value, date):
                value = value.isoformat()
            shaped.setdefault(item.section, {})[item.field] = value
        if self.comparables:
            shaped.setdefault("sales_comparison", {})["comparables"] = self.comparables
        return shaped
