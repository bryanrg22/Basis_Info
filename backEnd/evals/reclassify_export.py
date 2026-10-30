"""``python -m evals.reclassify_export``: re-run the rules table over a stored study export.

What it measures (CLASSIFICATION_CONVENTIONS.md section 7, EVAL_REPORT section 42): the
effect of a rules-table change on a study that already ran, without a paid run. For every
asset of the export it calls :func:`basis.domain.rules.lookup` with the asset's label,
material, attachment, room type and the export's property class. Where a rule hits, the
asset takes the rule's classification (the router takes a rule before the model, so this
is what the pipeline would do today); where no rule hits, the asset keeps its stored
classification (model, cache or manual). Quantities, costs and everything else are left
alone, so :mod:`evals.e2e_report` scores the new export against the engineer's rows with
the same dollars.

Usage (from ``backEnd/``)::

    python -m evals.reclassify_export export/rev-4.json --out export/rev-4.reclassified.json
    python -m evals.e2e_report --export export/rev-4.reclassified.json ...

``--property-class`` overrides the export's ``summary.takeoff.property_class``.
The change list goes to stdout (and ``--changes`` as JSON): per label, the stored and the
new bucket with counts, and the rule context that fired.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from basis.domain.rules import PROPERTY_CLASSES, RECOVERY_YEARS, RULES_VERSION, lookup

EXPORT_FORMATS = frozenset({"basis-export/2", "basis-evidence/1"})


class ReclassifyError(ValueError):
    pass


def property_class_of(document: Mapping[str, Any], override: str | None) -> str:
    if override is not None:
        if override not in PROPERTY_CLASSES:
            raise ReclassifyError(f"unknown property class {override!r}; one of {PROPERTY_CLASSES}")
        return override
    summary = document.get("summary")
    takeoff = summary.get("takeoff") if isinstance(summary, dict) else None
    value = takeoff.get("property_class") if isinstance(takeoff, dict) else None
    if value not in PROPERTY_CLASSES:
        raise ReclassifyError(
            "the export has no summary.takeoff.property_class; pass --property-class"
        )
    return str(value)


def reclassify_asset(asset: dict[str, Any], property_class: str) -> dict[str, Any] | None:
    """The asset with the rule's classification, or ``None`` when no rule hits.

    The returned dict is a copy; ``asset`` is not changed.
    """
    hit = lookup(
        asset.get("label"),
        asset.get("material"),
        asset.get("attachment"),
        property_class,
        room_type=asset.get("room_type"),
    )
    if hit is None:
        return None
    reasons = [f"rule_contested:{hit.key}"] if hit.contested else []
    new = dict(asset)
    new.update(
        {
            "category": hit.category,
            "bucket": hit.bucket.value,
            "recovery_period": str(RECOVERY_YEARS[hit.bucket]),
            "irs_section": hit.section,
            "method": "rule",
            "confidence": hit.confidence,
            "needs_review": bool(reasons),
            "review_reason": "; ".join(reasons) or None,
            "reclassified": {
                "rules_version": RULES_VERSION,
                "rule_key": hit.key,
                "context": hit.context,
                "stored_bucket": asset.get("bucket"),
                "stored_method": asset.get("method"),
            },
        }
    )
    return new


def reclassify(
    document: Mapping[str, Any], property_class: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``(new export, changes)``: ``changes`` has one row per asset whose bucket moved."""
    out = dict(document)
    assets: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for asset in document["assets"]:
        new = reclassify_asset(asset, property_class)
        if new is None:
            assets.append(dict(asset))
            continue
        assets.append(new)
        if new["bucket"] != asset.get("bucket"):
            changes.append(
                {
                    "asset_id": asset.get("id"),
                    "label": asset.get("label"),
                    "room_type": asset.get("room_type"),
                    "attachment": asset.get("attachment"),
                    "from": asset.get("bucket"),
                    "to": new["bucket"],
                    "stored_method": asset.get("method"),
                    "rule_key": new["reclassified"]["rule_key"],
                    "context": new["reclassified"]["context"],
                }
            )
    out["assets"] = assets
    out["reclassified"] = {
        "rules_version": RULES_VERSION,
        "property_class": property_class,
        "assets": len(assets),
        "rule_hits": sum(1 for a in assets if "reclassified" in a),
        "buckets_moved": len(changes),
    }
    return out, changes


def change_table(changes: Sequence[Mapping[str, Any]]) -> str:
    counts: Counter[tuple[str, str, str, str]] = Counter()
    for c in changes:
        counts[(str(c["label"]), str(c["from"]), str(c["to"]), str(c["context"]))] += 1
    lines = ["| label | from | to | context | assets |", "|---|---|---|---|---|"]
    for (label, src, dst, context), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {label} | {src} | {dst} | {context} | {n} |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("export", type=Path, help="rev-<n>.json (basis-export/2)")
    parser.add_argument("--out", type=Path, required=True, help="where to write the new export")
    parser.add_argument("--changes", type=Path, default=None, help="the change list as JSON")
    parser.add_argument("--property-class", default=None, choices=list(PROPERTY_CLASSES))
    args = parser.parse_args(argv)
    document = json.loads(args.export.read_text(encoding="utf-8"))
    if document.get("format") not in EXPORT_FORMATS:
        raise ReclassifyError(
            f"{args.export}: not a study export (format {document.get('format')!r})"
        )
    property_class = property_class_of(document, args.property_class)
    out, changes = reclassify(document, property_class)
    args.out.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.changes is not None:
        args.changes.write_text(json.dumps(changes, indent=1), encoding="utf-8")
    meta = out["reclassified"]
    print(
        f"rules {meta['rules_version']} on {meta['assets']} assets ({property_class}): "
        f"{meta['rule_hits']} rule hits, {meta['buckets_moved']} buckets moved"
    )
    if changes:
        print(change_table(changes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
