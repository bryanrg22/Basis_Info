"""Evidence export: JSON and CSV are byte-identical for the same revision's rows."""

from __future__ import annotations

import csv
import io
import json
import random
from decimal import Decimal

from basis.evidence.export import (
    CSV_COLUMNS,
    PACK_FORMAT,
    canonical_json,
    pack_document,
    pack_to_csv,
    pack_to_json,
    sha256_hex,
)
from basis.evidence.pack import assemble_pack

from .evidencetest_rows import rows


def test_json_is_canonical_and_carries_citations_inline() -> None:
    data = pack_to_json(assemble_pack(**rows()))
    document = json.loads(data)
    assert canonical_json(document) == data
    assert document["format"] == PACK_FORMAT and document["revision"] == 1
    carpet = next(a for a in document["assets"] if a["label"] == "carpet")
    assert (carpet["bucket"], carpet["quantity"], carpet["unit"], carpet["total"]) == (
        "y5",
        "18.33",
        "SY",
        "1368.52",
    )
    [first, second] = carpet["citations"]
    assert first["reference"] == "IRS_IRS_COST_SEG_ATG__2024, pp.412-415"
    assert first["excerpt"] == "Carpeting - 5-year recovery"
    assert second["page_start"] == 12


def test_csv_has_one_row_per_asset_in_pack_order() -> None:
    pack = assemble_pack(**rows())
    data = pack_to_csv(pack)
    assert b"\r\n" not in data
    parsed = list(csv.reader(io.StringIO(data.decode())))
    assert tuple(parsed[0]) == CSV_COLUMNS
    assert [r[0] for r in parsed[1:]] == [a.id for a in pack.assets]
    carpet = next(r for r in parsed[1:] if r[4] == "carpet")
    row = dict(zip(CSV_COLUMNS, carpet, strict=True))
    assert row["citation_count"] == "2" and row["needs_review"] == "false"
    assert row["citations"].startswith("IRS_IRS_COST_SEG_ATG__2024, pp.412-415: Carpeting")
    toilet = dict(zip(CSV_COLUMNS, next(r for r in parsed[1:] if r[4] == "toilet"), strict=True))
    assert (toilet["total"], toilet["needs_review"], toilet["citations"]) == ("", "true", "")


def test_the_json_carries_each_cost_trail_as_stored_and_the_csv_has_no_trail_column() -> None:
    """Cost trail (migration 0018): the export's asset entry has the cost row's trail as
    stored (null for a row priced before it existed); the CSV's columns are unchanged."""
    data = rows()
    trail = {"version": 1, "kind": "unit_price", "unit_cost": "74.66", "year": {"factor": "1.2330"}}
    carpet_cost = next(c for c in data["costs"].values() if c.unit_cost == Decimal("74.66"))
    carpet_cost.trail = trail
    pack = assemble_pack(**data)
    document = json.loads(pack_to_json(pack))
    by_label = {a["label"]: a for a in document["assets"]}
    assert by_label["carpet"]["cost_trail"] == trail
    assert by_label["carpet"]["cost_trail"]["year"]["factor"] == "1.2330"
    assert by_label["toilet"]["cost_trail"] is None
    assert "cost_trail" not in CSV_COLUMNS
    header = next(csv.reader(io.StringIO(pack_to_csv(pack).decode())))
    assert tuple(header) == CSV_COLUMNS


def test_same_rows_give_byte_identical_files() -> None:
    reference = assemble_pack(**rows())
    json_bytes, csv_bytes = pack_to_json(reference), pack_to_csv(reference)
    for seed in range(5):
        shuffled = rows()
        random.Random(seed).shuffle(shuffled["assets"])
        random.Random(seed).shuffle(shuffled["citations"])
        pack = assemble_pack(**shuffled)
        assert pack_to_json(pack) == json_bytes
        assert pack_to_csv(pack) == csv_bytes
    assert sha256_hex(json_bytes) == sha256_hex(pack_to_json(assemble_pack(**rows())))


def test_document_groupings_reference_existing_entries() -> None:
    pack = assemble_pack(**rows())
    document = pack_document(pack)
    ids = {c["id"] for a in document["assets"] for c in a["citations"]}
    for group in ("by_stage", "by_component", "by_document"):
        assert {i for v in document[group].values() for i in v} == ids
