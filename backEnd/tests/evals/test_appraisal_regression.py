"""Appraisal extraction regression on the private sets (SPEC §9): replay, compare to baseline.

Runs only where ``BASIS_EVAL_DATA_DIR`` holds ``appraisal/baseline.json`` (written by
``python -m evals.appraisal.run ... --accept-baseline``); skipped with the reason otherwise.
Marked ``eval_data`` so CI deselects it. For every set the baseline names and that has a
truth file, the pipeline method (``--system pipeline``, the system the baseline was
accepted from) is replayed from ``appraisal/recordings/`` (no network) and no field the
baseline has ``correct``/``correct_absent`` may now be anything else; a document whose
recording is gone fails every such field. The failure message lists keys and outcomes only,
never values.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import pytest

from evals.appraisal import baseline
from evals.appraisal.run import Options, run_eval
from evals.appraisal.systems import SystemSpec
from evals.appraisal.truth import SETS
from evals.datasets import ENV_VAR


def _appraisal() -> Path | None:
    value = os.environ.get(ENV_VAR)
    return Path(value).expanduser() / "appraisal" if value else None


def _sets() -> tuple[str, ...]:
    appraisal = _appraisal()
    if appraisal is None:
        return ()
    accepted = baseline.load(appraisal / baseline.FILE) or {}
    named = {fid.split("/", 1)[0] for fid in accepted}
    return tuple(s for s in SETS if s in named and (appraisal / "truth" / f"{s}.json").is_file())


def _skip_reason() -> str | None:
    appraisal = _appraisal()
    if appraisal is None:
        return f"{ENV_VAR} is not set (private eval data; this test never runs in CI)"
    if not (appraisal / baseline.FILE).is_file():
        return "no appraisal/baseline.json (run evals.appraisal.run --accept-baseline)"
    if not _sets():
        return "no set in appraisal/baseline.json has a truth file"
    return None


SKIP_REASON: Final = _skip_reason()

pytestmark = [
    pytest.mark.eval_data,
    pytest.mark.skipif(SKIP_REASON is not None, reason=SKIP_REASON or ""),
]


@pytest.mark.parametrize("set_name", _sets() or ("none",))
async def test_no_baseline_field_regresses(set_name: str, tmp_path: Path) -> None:
    appraisal = _appraisal()
    assert appraisal is not None
    accepted = baseline.load(appraisal / baseline.FILE)
    assert accepted is not None
    output = await run_eval(
        Options(
            sets=(set_name,),
            mode="replay",
            out=tmp_path / "run",
            data_dir=appraisal.parent,
            system=SystemSpec(name="pipeline"),  # baseline.json is the pipeline's (SPEC §9)
        )
    )
    assert output is not None
    found = output.regressions
    assert not found, f"{len(found)} baseline fields regressed:\n" + "\n".join(
        f"{r['field']}: {r['baseline']} -> {r['now']}" for r in found
    )
