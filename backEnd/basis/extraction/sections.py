"""Map Azure DI output to catalogue fields and merge it with the regex/table candidates.

Migrated from ``evidence_layer/src/map_appraisal_sections.py`` and the key/table mapping
in ``tiered_extraction/azure_di_extractor.py``. The result is one
:class:`~basis.extraction.results.ExtractedField` per catalogue key (the URAR sections
shape is ``ExtractionResult.sections()``).

Merge rule (BUILD_PLAN ``test_section_mapper``: "fallbacks only fill empty values"):
Azure DI is the primary source; a regex or text-table value is used only for a field
Azure DI did not produce (or produced empty or unparseable). When both read a field, the
field records which other sources agree (``corroborated_by``); when they disagree on a
critical field, :func:`merge` reports a ``sources_disagree`` issue so the extraction goes
to review instead of silently trusting either reader.

Azure key-value pairs carry a page, not a section. The section is found by locating the
key's caption on that page in Azure's own page lines (headings set sections the same way
as for the text layer); when the caption is not found, every section printed on that page
is tried in order.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from basis.extraction.azure_di import AzureDiResult
from basis.extraction.field_mappings import key_for_label, normalize_label
from basis.extraction.regex_fields import Line, section_lines, table_candidates
from basis.extraction.results import Candidate, ExtractedField, ValidationIssue


def _sections_on_page(lines: Sequence[Line], page: int | None) -> list[str]:
    seen: list[str] = []
    for line in lines:
        if line.page == page and line.section is not None and line.section not in seen:
            seen.append(line.section)
    return seen


def _section_of_caption(lines: Sequence[Line], page: int | None, caption: str) -> str | None:
    target = normalize_label(caption)
    if not target:
        return None
    for line in lines:
        if line.page == page and target in normalize_label(line.text):
            return line.section
    return None


def azure_candidates(result: AzureDiResult) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """Candidates from Azure DI key-value pairs and tables, plus sales-grid comparables."""
    lines = section_lines(result.page_texts())
    found: dict[str, Candidate] = {}
    for pair in result.key_value_pairs:
        if pair.value is None or not pair.value.strip():
            continue
        sections: list[str | None] = []
        located = _section_of_caption(lines, pair.page, pair.key)
        if located is not None:
            sections.append(located)
        sections.extend(s for s in _sections_on_page(lines, pair.page) if s != located)
        sections.append(None)  # a caption that is unambiguous anywhere
        key = next(
            (k for k in (key_for_label(pair.key, s) for s in sections) if k is not None), None
        )
        if key is None or key in found:
            continue
        found[key] = Candidate(
            key=key,
            raw_text=pair.value,
            page=pair.page,
            confidence=pair.confidence,
            source="azure",
        )
    table_fields, comparables = table_candidates(
        ((table.page, table.rows()) for table in result.tables), source="azure"
    )
    for candidate in table_fields:
        found.setdefault(candidate.key, candidate)
    return list(found.values()), comparables


def _same(a: ExtractedField, b: ExtractedField) -> bool:
    return a.typed == b.typed if a.typed is not None else a.value_text == b.value_text


def merge(
    primary: Iterable[Candidate], fallbacks: Iterable[Candidate]
) -> tuple[dict[str, ExtractedField], list[ValidationIssue]]:
    """Primary (Azure DI) fields, with fallback (regex/table) values only for gaps."""
    fields: dict[str, ExtractedField] = {}
    for candidate in primary:
        item = ExtractedField.from_candidate(candidate)
        if not item.is_empty:
            fields.setdefault(item.key, item)
    issues: list[ValidationIssue] = []
    for candidate in fallbacks:
        item = ExtractedField.from_candidate(candidate)
        if item.is_empty:
            continue
        current = fields.get(item.key)
        if current is None or (current.parse_error and not item.parse_error):
            fields[item.key] = item
            continue
        if _same(current, item):
            fields[item.key] = current.model_copy(
                update={"corroborated_by": (*current.corroborated_by, item.source)}
            )
        elif current.critical:
            issues.append(
                ValidationIssue(
                    rule="sources_disagree",
                    message=(
                        f"{item.key}: Azure DI read {current.value_text!r}, "
                        f"the text layer reads {item.value_text!r}"
                    ),
                    keys=(item.key,),
                    severity="error",
                )
            )
    return fields, issues
