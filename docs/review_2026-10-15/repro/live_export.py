"""Turn a live study's stored outputs into prediction files the harness scores (EVAL_REPORT 19).

Run from ``backEnd/`` (it imports the harness and the room-name rule)::

    uv run python ../docs/review_2026-10-15/repro/live_export.py SNAPSHOT_DIR DOCUMENTS_JSON OUT_DIR
    uv run python -m evals.run --predictions OUT_DIR --no-db --report OUT_DIR/eval.md

``SNAPSHOT_DIR`` holds one JSON array per table of the study, read from the database after
the study completed (``extraction_fields``, ``rooms``, ``photo_rooms``, ``components``,
``assets``, ``citations``, ``cost_estimates``); ``DOCUMENTS_JSON`` maps the study's document
ids to their file names (what the client uploaded). Nothing is called: every value is what
the pipeline wrote. Files written to ``OUT_DIR``:

* ``appraisal_fields.json``: ``{<appraisal document>: {"<section>.<field>": value}}``, the
  stored value of each extracted field (number, then date, then text), under the label
  file's key where the catalogue names the same box differently (``CATALOGUE_TO_LABEL``).
* ``photo_counts.json`` / ``photo_components.json`` / ``photo_rooms.json``: per photo, from
  the per-photo ``components`` rows (one row per label, ``raw.count``) and the room type of
  the room the photo was assigned to.
* ``room_inventory.json`` and ``room_raw.jsonl`` (materials): the labelled rooms are keyed by
  the file-name rule and the engineer's aliases, the pipeline's rooms by the model's room
  identity, so the two are joined through the photos. Each room inventory row
  (``raw.source = "room_inventory"``) is attributed to the labelled room of the photos it
  was seen in (the most frequent one; ties go to the first photo in file order); a pipeline
  room with no inventory rows (the Unassigned room) contributes its per-photo rows,
  attributed to each photo's labelled room, merged by the largest count per label. A
  pipeline room that spans several labelled rooms therefore splits by evidence instead of
  being charged whole to each.
* ``assets.json``: the engineer's schedule rows (``assets_v1``) answered from the
  pipeline's assets: a row is answered by the assets whose component label is one of the
  row's ``report_rows`` labels, in a room of an allowed type (the room type the pipeline
  gave the component's room), and takes their majority bucket (ties: the larger estimated
  cost); rows with no such asset abstain. Citation counts are the matched assets' claimed
  and verified citations.
* ``grouping.json``: how the pipeline's rooms map onto the labelled rooms (for the report).
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from basis.vision.rooms import name_hint, resolve_aliases
from evals import datasets as ds
from evals.predict_photos import label_counts

BUCKETS = {5: "y5", 7: "y7", 15: "y15", 27.5: "y27_5", 39: "y39"}
# The same URAR box under the production field catalogue's key and the label file's key
# (basis.extraction.field_mappings against appraisal_fields_v1); nothing else is renamed.
CATALOGUE_TO_LABEL = {
    "subject.assessors_parcel": "subject.assessor_parcel",
    "subject.real_estate_taxes": "subject.re_taxes",
    "cost_approach.depreciated_cost_improvements": "cost_approach.depreciated_cost_of_improvements",
    "cost_approach.indicated_value": "cost_approach.indicated_value_by_cost_approach",
    "cost_approach.site_improvements_value": "cost_approach.site_improvements_as_is",
}


def load(snapshot: Path, name: str) -> list[dict[str, Any]]:
    return json.loads((snapshot / f"{name}.json").read_text())


def field_value(row: dict[str, Any]) -> Any:
    if row.get("value_num") is not None:
        value = float(row["value_num"])
        return int(value) if value.is_integer() else value
    if row.get("value_date") is not None:
        return str(row["value_date"])
    return row.get("value_text")


def bucket_of(asset: dict[str, Any]) -> str | None:
    bucket = asset.get("bucket")
    if bucket:
        return str(bucket)
    period = asset.get("recovery_period")
    return BUCKETS.get(float(period)) if period is not None else None  # type: ignore[call-overload]


def main(snapshot: Path, documents_json: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data_dir = ds.data_dir()
    documents = {d["id"]: d for d in json.loads(documents_json.read_text())}
    files = {doc_id: d["file"] for doc_id, d in documents.items()}
    order = {doc_id: i for i, doc_id in enumerate(sorted(files, key=lambda k: files[k]))}
    aliases = resolve_aliases(
        json.loads((data_dir / "labels" / "room_aliases_v1.json").read_text())
    )

    def label_room(doc_id: str) -> str:
        room_id = name_hint(files[doc_id]).room_id
        return aliases.get(room_id, room_id)

    # ------------------------------------------------------------- appraisal fields
    gold_fields = json.loads((data_dir / "appraisal_fields_v1.json").read_text())
    gold_document = gold_fields["documents"][0]["document"]
    fields = {}
    for row in load(snapshot, "extraction_fields"):
        key = f"{row['section']}.{row['field']}"
        fields[CATALOGUE_TO_LABEL.get(key, key)] = field_value(row)
    (out / "appraisal_fields.json").write_text(json.dumps({gold_document: fields}, indent=1))

    # ------------------------------------------------------------- per photo
    rooms = {r["id"]: r for r in load(snapshot, "rooms")}
    photo_room = {r["document_id"]: r["room_id"] for r in load(snapshot, "photo_rooms")}
    components = load(snapshot, "components")
    per_photo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in components:
        if row["document_id"] is not None:
            per_photo[row["document_id"]].append(
                {"label": row["label"], "count": int((row["raw"] or {}).get("count", 1))}
            )
    photos = [d for d in documents.values() if d["kind"] == "photo"]
    counts = {d["file"]: label_counts(per_photo.get(d["id"], [])) for d in photos}
    labels = {d["file"]: sorted({c["label"] for c in per_photo.get(d["id"], [])}) for d in photos}
    room_types = {
        d["file"]: rooms[photo_room[d["id"]]]["room_type"] for d in photos if d["id"] in photo_room
    }
    (out / "photo_counts.json").write_text(json.dumps(counts, indent=1))
    (out / "photo_components.json").write_text(json.dumps(labels, indent=1))
    (out / "photo_rooms.json").write_text(json.dumps(room_types, indent=1))

    # ------------------------------------------------------------- rooms
    inventory: dict[str, Counter[str]] = defaultdict(Counter)
    materials: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with_inventory = set()
    for row in components:
        raw = row["raw"] or {}
        if row["document_id"] is None and raw.get("source") == "room_inventory":
            with_inventory.add(row["room_id"])
            seen = [p for p in (raw.get("seen_in") or raw.get("photos") or []) if p in files]
            if not seen:
                continue
            votes = Counter(label_room(p) for p in seen)
            best = max(votes.values())
            first = sorted(seen, key=order.__getitem__)
            target = next(label_room(p) for p in first if votes[label_room(p)] == best)
            inventory[target][row["label"]] += int(raw.get("count", 1))
            materials[target].append({"label": row["label"], "material": row["material"]})
    fallback: dict[tuple[str, str], int] = {}
    for row in components:
        if row["document_id"] is not None and row["room_id"] not in with_inventory:
            key = (label_room(row["document_id"]), row["label"])
            fallback[key] = max(fallback.get(key, 0), int((row["raw"] or {}).get("count", 1)))
            materials[key[0]].append({"label": row["label"], "material": row["material"]})
    for (room, label), count in fallback.items():
        inventory[room][label] += count
    (out / "room_inventory.json").write_text(
        json.dumps({room: dict(c) for room, c in sorted(inventory.items())}, indent=1)
    )
    with (out / "room_raw.jsonl").open("w") as raw_file:
        for room, objects in sorted(materials.items()):
            raw_file.write(json.dumps({"room_id": room, "repeat": 1, "objects": objects}) + "\n")

    grouping: dict[str, Any] = {}
    for room_id, room in rooms.items():
        docs = [d for d, r in photo_room.items() if r == room_id]
        grouping[room_id] = {
            "name": room["name"],
            "room_type": room["room_type"],
            "floor": room["floor"],
            "photos": len(docs),
            "labelled_rooms": sorted({label_room(d) for d in docs}),
            "has_inventory": room_id in with_inventory,
        }
    (out / "grouping.json").write_text(json.dumps(grouping, indent=1))

    # ------------------------------------------------------------- assets
    by_component = {c["id"]: c for c in components}
    costs = {c["asset_id"]: float(c["total"] or 0) for c in load(snapshot, "cost_estimates")}
    citations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in load(snapshot, "citations"):
        citations[c["asset_id"]].append(c)
    assets = load(snapshot, "assets")
    rows = json.loads((data_dir / "report_rows_v1.json").read_text())["rows"]
    answers: dict[str, Any] = {}
    for row in rows:
        allowed = set(row.get("labels") or [])
        if not allowed:
            continue  # kind C or a vocabulary gap: photos cannot answer it
        matched = []
        for asset in assets:
            component = by_component.get(asset["component_id"])
            if component is None or component["label"] not in allowed:
                continue
            room_type = rooms.get(component["room_id"], {}).get("room_type")
            if row.get("room_types") and room_type not in row["room_types"]:
                continue
            if room_type in (row.get("exclude_room_types") or []):
                continue
            matched.append(asset)
        if not matched:
            continue
        tally: Counter[str] = Counter()
        spend: Counter[str] = Counter()
        for asset in matched:
            bucket = bucket_of(asset)
            if bucket is not None:
                tally[bucket] += 1
                spend[bucket] += costs.get(asset["id"], 0.0)
        if not tally:
            continue
        bucket = max(tally, key=lambda b: (tally[b], spend[b]))
        claimed = sum(len(citations[a["id"]]) for a in matched)
        verified = sum(
            1 for a in matched for c in citations[a["id"]] if c.get("verified_at") is not None
        )
        answers[row["id"]] = {
            "section": None,
            "bucket": bucket,
            "citations": {"claimed": claimed, "verified": verified},
            "matched_assets": len(matched),
        }
    (out / "assets.json").write_text(json.dumps(answers, indent=1))
    print(
        json.dumps(
            {
                "fields": len(fields),
                "photos": len(counts),
                "label_rooms_predicted": len(inventory),
                "pipeline_rooms": len(rooms),
                "asset_rows_answered": len(answers),
            }
        )
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
