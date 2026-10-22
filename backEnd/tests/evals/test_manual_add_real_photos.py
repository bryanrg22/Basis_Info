"""The manual-add gates on the real photos (private data only): ``evals.manual_add`` replayed.

Runs only where ``BASIS_EVAL_DATA_DIR`` holds the verified boxes, the shipped v3 SAM 3 run
and the saved SAM 3 answers to the boxes (``predictions/manual_add_v1/prompted.json``,
written once by ``python -m evals.manual_add --live``); skipped with the reason otherwise.
Marked ``eval_data`` so CI deselects it. Nothing from the photos is printed: rates only.

Gates (``evals.manual_add.gates``): on the 43 objects the pipeline misses, the exact box
outlines the right object at least 0.95 of the time and a wrong one never (sloppy boxes
included); on the 148 it counts, at most 2% of box selections create a duplicate. Also
pinned: every selection that reaches SAM 3 has a saved answer, and what was measured when
the answers were saved (2026-10-22), so a change to the selection rules shows here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final

import pytest

from evals import manual_add

ENV_VAR: Final = manual_add.ENV_VAR


def _data() -> Path | None:
    value = os.environ.get(ENV_VAR)
    return Path(value).expanduser() if value else None


def _skip_reason() -> str | None:
    data = _data()
    if data is None:
        return f"{ENV_VAR} is not set (private eval data; this test never runs in CI)"
    needed = [manual_add.LABELS_FILE, manual_add.PROMPTS_FILE] + [
        f"{run}/instances_raw.jsonl" for run in manual_add.SAM3_RUNS
    ]
    missing = [name for name in needed if not (data / name).is_file()]
    return f"{ENV_VAR} lacks {', '.join(missing)}" if missing else None


SKIP_REASON: Final = _skip_reason()

pytestmark = [
    pytest.mark.eval_data,
    pytest.mark.skipif(SKIP_REASON is not None, reason=SKIP_REASON or ""),
]


@pytest.fixture(scope="module")
def measured() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = _data()
    assert data is not None
    return manual_add.run(data, data / manual_add.PROMPTS_FILE)


def test_the_gates_hold(measured: tuple[list[dict[str, Any]], dict[str, Any]]) -> None:
    records, summary = measured
    assert manual_add.gates(summary) == []
    assert not [r for r in records if "unprompted" in (r["outcome"], r["after_add_anyway"])]


def test_the_measured_rates(measured: tuple[list[dict[str, Any]], dict[str, Any]]) -> None:
    _, summary = measured
    assert summary["objects"] == {"misses": 43, "counted": 148}
    for variant in manual_add.VARIANTS:
        entry = summary["misses"][f"box_{variant}"]
        # 42 outlined at once; the 43rd is a light a counted mask covers with its
        # neighbour (the guard asks), outlined after "add anyway".
        assert (entry["correct"]["k"], entry["wrong"]["k"]) == (42, 0), variant
        assert entry["correct_after_add_anyway"]["k"] == 43, variant
    tap = summary["misses"]["tap"]
    assert (tap["correct"]["k"], tap["wrong"]["k"], tap["outcomes"].get("nothing")) == (27, 0, 13)
    assert summary["counted"]["boxes_all"]["duplicates_created"]["k"] <= 1
