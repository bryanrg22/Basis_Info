"""The two tables of EVAL_REPORT section 17 (text steps on current models), from saved runs.

Run from ``backEnd/`` (it imports the harness)::

    uv run python ../docs/review_2026-10-15/repro/text_step_table.py fields \\
        ~/basis-eval-data/predictions/text_fields_gpt-4o-mini ...
    uv run python ../docs/review_2026-10-15/repro/text_step_table.py assets \\
        ~/basis-eval-data/predictions/text_assets_gpt-4o-mini ...

Every number comes from the harness scorers (:func:`evals.run.score_fields`,
:func:`evals.run.score_assets`) on the run's prediction file and the labels in
``BASIS_EVAL_DATA_DIR``, or from the runner's ``run.json``:

* accuracy columns: repeat 1 (``normalized_accuracy``, ``critical_accuracy``,
  ``bucket_accuracy``, ``bucket_macro_f1``) and the mean over the three repeats
  (``pass^1``); stability is ``pass^3`` (right on all three repeats);
* unguarded (fields): the mean over repeats of the same score on
  ``appraisal_fields.unguarded.json``, the answers before the production verbatim guard;
* cost per study: ``run.json`` ``spent_usd`` / repeats (one study = the 75 fields of the
  appraisal, or the 46 rows of the engineer's schedule), priced by ``basis.llm.cost``;
* seconds: per call (fields: one call per study) or per row (assets: the whole loop);
* answered loops (assets): bucket accuracy over the (row, repeat) records of
  ``assets_raw.jsonl`` whose loop returned a classification (a failed loop is the
  pipeline's ``Unclassified`` fallback), i.e. the model's own answers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from evals import datasets as ds
from evals.run import score_assets, score_fields


def rows(result: object) -> dict[str, float]:
    return {r.metric: r.value for r in result.rows}  # type: ignore[attr-defined]


def fields_table(dirs: list[Path]) -> None:
    data = ds.load("appraisal_fields")
    assert isinstance(data, ds.AppraisalFields)
    print(
        "| Model | effort | Fields right, repeat 1 | mean of 3 | critical (12), mean of 3 "
        "| pass^3 | critical pass^3 | unguarded, mean of 3 | dropped by the guard per call "
        "| cost per study | seconds per call | models reported |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for d in dirs:
        run = json.loads((d / "run.json").read_text())
        k = run["repeats"]
        guarded = rows(
            score_fields(data, "dev", k, json.loads((d / "appraisal_fields.json").read_text()))
        )
        unguarded = rows(
            score_fields(
                data, "dev", k, json.loads((d / "appraisal_fields.unguarded.json").read_text())
            )
        )
        raw = [json.loads(line) for line in (d / "fields_raw.jsonl").read_text().splitlines()]
        dropped = sum(
            1 for r in raw for reason in r["dropped"].values() if reason == "not_in_text"
        ) / max(len(raw), 1)
        print(
            f"| {run['model_requested']} | {run['reasoning_effort'] or '-'} "
            f"| {guarded['normalized_accuracy']:.3f} | {guarded['pass^1']:.3f} "
            f"| {guarded['critical_pass^1']:.3f} | {guarded['pass^3']:.3f} "
            f"| {guarded['critical_pass^3']:.3f} | {unguarded['pass^1']:.3f} | {dropped:.1f} "
            f"| ${run['spent_usd'] / k:.4f} | {run['seconds_per_call']:.1f} "
            f"| {', '.join(run['models_reported'])} ({len(run['errors'])} errors) |"
        )


def assets_table(dirs: list[Path]) -> None:
    data = ds.load("assets")
    assert isinstance(data, ds.Assets)
    print(
        "| Model | effort | Bucket right, repeat 1 | mean of 3 | pass^3 | macro-F1, repeat 1 "
        "| bucket right, answered loops only | citations verified "
        "| rows with a verified citation | loop failures (of 138) "
        "| cost per study | seconds per row | models reported |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    truth = {a.id: a.bucket for a in data.assets}
    for d in dirs:
        run = json.loads((d / "run.json").read_text())
        k = run["repeats"]
        raw = [json.loads(line) for line in (d / "assets_raw.jsonl").read_text().splitlines()]
        answered = [r for r in raw if not r["category"].startswith("Unclassified")]
        own = sum(r["prediction"]["bucket"] == truth[r["id"]] for r in answered) / len(answered)
        m = rows(score_assets(data, "dev", k, json.loads((d / "assets.json").read_text())))
        print(
            f"| {run['model_requested']} | {run['reasoning_effort'] or '-'} "
            f"| {m['bucket_accuracy']:.3f} | {m['bucket_pass^1']:.3f} | {m['bucket_pass^3']:.3f} "
            f"| {m['bucket_macro_f1']:.3f} | {own:.3f} | {m['citation_validity']:.3f} "
            f"| {m['rows_with_verified_citation']:.3f} | {len(run['loop_failures'])} "
            f"| ${run['spent_usd'] / k:.4f} | {run['seconds_per_row']:.1f} "
            f"| {', '.join(run['models_reported']) or '-'} |"
        )


if __name__ == "__main__":
    step, *paths = sys.argv[1:]
    {"fields": fields_table, "assets": assets_table}[step]([Path(p) for p in paths])
