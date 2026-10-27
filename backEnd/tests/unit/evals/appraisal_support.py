"""Fabricated truth documents and predictions for the appraisal eval v2 tests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from evals.appraisal.truth import CATALOGUE_KEYS, DocumentTruth, TruthSet


def truth_doc(
    fields: Mapping[str, Mapping[str, Any]],
    *,
    document: str = "doc_a",
    pages: int = 3,
    pdf: str | None = None,
    sha256: str = "0" * 64,
    expected_issues: Iterable[Mapping[str, Any]] = (),
) -> DocumentTruth:
    """Every catalogue key ``absent`` except those given (``{key: {status, type, ...}}``)."""
    entries: dict[str, dict[str, Any]] = {k: {"key": k, "status": "absent"} for k in CATALOGUE_KEYS}
    for key, entry in fields.items():
        entries[key] = {"key": key, **entry}
    return DocumentTruth.model_validate(
        {
            "document": document,
            "source": "synthetic",
            "form": "urar_1004_2005",
            "pdf": pdf or f"synthetic_1004/{document}/appraisal.pdf",
            "sha256": sha256,
            "pages": pages,
            "fields": list(entries.values()),
            "expected_issues": list(expected_issues),
        }
    )


def truth_set(*documents: DocumentTruth, name: str = "synthetic_1004") -> TruthSet:
    return TruthSet.model_validate(
        {"schema_version": 2, "set": name, "documents": [d.model_dump() for d in documents]}
    )


def field(
    value_text: str | None = None,
    value_num: str | None = None,
    value_date: str | None = None,
    *,
    confidence: float = 0.95,
    source: str = "azure",
    page: int | None = 1,
) -> dict[str, Any]:
    return {
        "value_text": value_text,
        "value_num": value_num,
        "value_date": value_date,
        "confidence": confidence,
        "source": source,
        "page": page,
    }


def repeat(
    fields: Mapping[str, Mapping[str, Any]],
    *,
    number: int = 1,
    issues: Iterable[Mapping[str, Any]] = (),
    reasons: Iterable[str] = (),
    needs_review: bool = True,
) -> dict[str, Any]:
    return {
        "repeat": number,
        "status": "ok",
        "needs_review": needs_review,
        "review_reasons": list(reasons),
        "issues": list(issues),
        "fields": dict(fields),
    }


def predictions(*documents: dict[str, Any]) -> dict[str, Any]:
    return {"run": "test", "mode": "replay", "documents": list(documents)}


def prediction_doc(
    document: str, *repeats: dict[str, Any], set_name: str = "synthetic_1004", status: str = "ok"
) -> dict[str, Any]:
    return {"set": set_name, "document": document, "status": status, "repeats": list(repeats)}
