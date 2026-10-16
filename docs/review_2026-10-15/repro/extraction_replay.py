"""Replay the production extraction on a stored Azure DI result and score it (EVAL_REPORT 25).

Run from ``backEnd/`` with ``BASIS_ENV=dev`` and the provider keys in the environment::

    uv run python ../docs/review_2026-10-15/repro/extraction_replay.py APPRAISAL_PDF \\
        AZURE_DI_JSON LABELS_JSON OUT_DIR [--scope missing|critical] [--repeats N]

``AZURE_DI_JSON`` is the ``azure_di`` object a live study stored in ``extractions.raw``
(so Azure DI is not called again; the pipeline reuses it exactly as it reuses an earlier
revision's analysis). The pipeline is :func:`basis.extraction.pipeline.extract_appraisal`
with the settings in effect (``TEXT_MODEL``, ``EXTRACTION_*``) and ``--scope`` for the
fallback; nothing else differs from the ``extract_fields`` activity. Each stored value is
mapped to the label file's key as the live exporter maps it (``CATALOGUE_TO_LABEL`` in
``live_export.py``) and scored with the harness's ``field_matches``. Writes::

    OUT_DIR/appraisal_fields.json   {<document>: {<label key>: value | {"repeats": [...]}}}
    OUT_DIR/replay.json             per repeat: right / wrong / not extracted per field,
                                    the fallback's status, asked and dropped keys, cost

Nothing about the document is printed; the private labels stay where they are.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_export import CATALOGUE_TO_LABEL  # noqa: E402

from basis.config import load_settings  # noqa: E402
from basis.extraction.azure_di import AzureDiResult  # noqa: E402
from basis.extraction.fallback import PROMPT_NAME  # noqa: E402
from basis.extraction.pipeline import ExtractionContext, extract_appraisal  # noqa: E402
from basis.extraction.results import ExtractedField  # noqa: E402
from basis.llm import prompts  # noqa: E402
from basis.llm.call import CallContext  # noqa: E402
from basis.llm.pool import ProviderPool  # noqa: E402
from basis.llm.provider import Budget  # noqa: E402
from basis.llm.recorder import MemoryRecorder  # noqa: E402
from evals.metrics import field_matches  # noqa: E402


def stored_value(item: ExtractedField) -> Any:
    """What ``extraction_fields`` stores, read back as the exporter reads it."""
    if item.value_num is not None:
        number = float(item.value_num)
        return int(number) if number.is_integer() else number
    if item.value_date is not None:
        return item.value_date.isoformat()
    return item.value_text


async def replay(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings()
    pool = ProviderPool.from_settings(settings)
    azure = AzureDiResult.model_validate(json.loads(args.azure.read_text()))
    labels = json.loads(args.labels.read_text())["documents"][0]["fields"]
    pdf = args.pdf.read_bytes()
    runs: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    for repeat in range(args.repeats):
        budget = Budget(remaining_usd=2.0)
        ctx = ExtractionContext(
            call=CallContext(
                study_id=uuid.uuid4(),
                run_id=None,
                activity="extract_fields",
                budget=budget,
                recorder=MemoryRecorder(),
            ),
            provider=pool,
            azure=None,
            prompt=prompts.latest(PROMPT_NAME),
            text_model=settings.text_model,
            review_threshold=settings.extraction_review_threshold,
            fallback_max_tokens=settings.extraction_output_budget,
            fallback_reasoning_effort=settings.extraction_reasoning_effort,
            previous_azure=azure,
            fallback_scope=args.scope,
            fallback_context_chars=settings.extraction_fallback_context_chars,
        )
        result = await extract_appraisal(pdf, "appraisal", ctx)
        values = {CATALOGUE_TO_LABEL.get(k, k): stored_value(f) for k, f in result.fields.items()}
        predictions.append(values)
        right, wrong, missing = [], [], []
        for label in labels:
            predicted = values.get(label["field"])
            if predicted is None:
                missing.append(label["field"])
                continue
            _, ok = field_matches(
                predicted,
                label["expected"],
                label.get("type", "text"),
                absolute=label.get("absolute"),
                relative=label.get("relative"),
            )
            (right if ok else wrong).append(label["field"])
        critical = [lab["field"] for lab in labels if str(lab.get("critical")) == "True"]
        runs.append(
            {
                "repeat": repeat + 1,
                "fields_stored": len(result.fields),
                "right": right,
                "wrong": wrong,
                "not_extracted": missing,
                "critical_right": sorted(set(critical) & set(right)),
                "critical_total": len(critical),
                "fallback": {k: v for k, v in result.fallback.items() if k != "raw"},
                "sources": {
                    CATALOGUE_TO_LABEL.get(k, k): f.source for k, f in result.fields.items()
                },
                "needs_review": result.needs_review,
                "cost_usd": round(budget.spent_usd, 6),
                "model": settings.text_model,
                "scope": args.scope,
            }
        )
        print(
            f"repeat {repeat + 1}: {len(right)} right, {len(wrong)} wrong, "
            f"{len(missing)} not extracted of {len(labels)}; "
            f"critical {len(runs[-1]['critical_right'])}/{len(critical)}; "
            f"${budget.spent_usd:.4f}"
        )
    document = json.loads(args.labels.read_text())["documents"][0]["document"]
    keys = sorted({k for p in predictions for k in p})
    merged = {
        k: (
            predictions[0].get(k)
            if len(predictions) == 1
            else {"repeats": [p.get(k) for p in predictions]}
        )
        for k in keys
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "appraisal_fields.json").write_text(
        json.dumps({document: merged}, indent=1, default=_json)
    )
    (args.out / "replay.json").write_text(json.dumps(runs, indent=1, default=_json))
    return {"runs": runs}


def _json(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("azure", type=Path)
    parser.add_argument("labels", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--scope", choices=("missing", "critical"), default="missing")
    parser.add_argument("--repeats", type=int, default=1)
    asyncio.run(replay(parser.parse_args()))


if __name__ == "__main__":
    main()
