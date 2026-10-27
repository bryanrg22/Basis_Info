"""The accepted baseline (``BASIS_EVAL_DATA_DIR/appraisal/baseline.json``, ``SPEC.md`` §9).

Keys and outcomes only: ``{"<set>/<document>/<key>": outcome}`` for the first repeat of every
scored field. A regression is a field the baseline has ``correct``/``correct_absent`` that
now has any other outcome, or whose document now fails (``failed:<status>``). Only the
documents a run attempted are compared. ``--accept-baseline`` replaces the entries of the
documents the run scored and keeps the others; it is refused when a document failed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from evals.appraisal.score import SUCCESS

SCHEMA: Final = 1
FILE: Final = "baseline.json"


def field_id(set_name: str, document: str, key: str) -> str:
    return f"{set_name}/{document}/{key}"


def outcomes(score: Mapping[str, Any]) -> dict[str, str]:
    """``{field id: outcome}`` for the first repeat of every scored field."""
    return {
        field_id(row["set"], row["document"], row["key"]): row["outcome"]
        for row in score["fields"]
        if row["repeat"] == 1
    }


def load(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    return dict(data["outcomes"])


def write(
    path: Path,
    score: Mapping[str, Any],
    attempted: set[tuple[str, str]],
    *,
    run: str,
    git_sha: str,
) -> dict[str, str]:
    """Accept ``score`` for the ``attempted`` documents; other documents' entries stay."""
    if score["documents"]["failed"]:
        names = ", ".join(f"{f['set']}/{f['document']}" for f in score["documents"]["failed"])
        raise ValueError(f"not accepting a baseline with failed documents: {names}")
    previous = load(path) or {}
    kept = {
        k: v
        for k, v in previous.items()
        if tuple(k.split("/", 2)[:2]) not in attempted  # (set, document)
    }
    merged = dict(sorted({**kept, **outcomes(score)}.items()))
    path.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "accepted": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "run": run,
                "git_sha": git_sha,
                "outcomes": merged,
            },
            indent=1,
        )
        + "\n"
    )
    return merged


def regressions(
    baseline: Mapping[str, str], score: Mapping[str, Any], attempted: set[tuple[str, str]]
) -> list[dict[str, str]]:
    """Baseline successes of the attempted documents that are no longer successes."""
    now = outcomes(score)
    failed = {(f["set"], f["document"]): f["status"] for f in score["documents"]["failed"]}
    found: list[dict[str, str]] = []
    for fid, was in sorted(baseline.items()):
        set_name, document, key = fid.split("/", 2)
        if was not in SUCCESS or (set_name, document) not in attempted:
            continue
        if (set_name, document) in failed:
            current = f"failed:{failed[(set_name, document)]}"
        else:
            current = now.get(fid, "not_scored")
        if current not in SUCCESS:
            found.append({"field": fid, "baseline": was, "now": current})
    return found
