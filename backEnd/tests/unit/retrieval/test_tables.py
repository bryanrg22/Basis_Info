"""Whole-table extraction against the synthetic golden files.

``urar_sample.tables.json`` and ``corpus/manifest.json`` were written by the fixture
generator from the values it drew, so they are ground truth independent of any parser.
"""

from __future__ import annotations

from typing import Any

import pytest
from fixtures import fixture_path, load_fixture_json

from basis.retrieval.parse import parse_pdf
from basis.retrieval.tables import (
    ExtractedTable,
    clean_cell,
    normalize_headers,
    table_from_rows,
)


def as_lists(table: ExtractedTable) -> tuple[list[str], list[list[str]]]:
    return list(table.headers), [list(row) for row in table.rows]


def test_urar_tables_match_the_golden_file() -> None:
    golden = load_fixture_json("urar_sample.tables.json")
    document = parse_pdf(fixture_path("urar_sample.pdf").read_bytes(), golden["doc_id"])
    assert len(document.tables) == len(golden["tables"]) == 2
    for table, expected in zip(document.tables, golden["tables"], strict=True):
        assert table.page == expected["page"]
        assert as_lists(table) == (expected["headers"], expected["rows"])
        assert (len(table.rows), len(table.headers)) == (expected["n_rows"], expected["n_cols"])


@pytest.mark.parametrize("doc", load_fixture_json("corpus", "manifest.json")["documents"])
def test_corpus_tables_match_the_manifest(doc: dict[str, Any]) -> None:
    document = parse_pdf(fixture_path(*doc["file"].split("/")).read_bytes(), doc["doc_id"])
    assert len(document.tables) == len(doc["tables"])
    for table, expected in zip(document.tables, doc["tables"], strict=True):
        assert table.page == expected["page"]
        assert as_lists(table) == (expected["headers"], expected["rows"])
        assert table.caption == expected["title"]
        assert table.key == f"{doc['doc_id']}:{expected['page']}:t0"


def test_markdown_renders_every_cell() -> None:
    table = table_from_rows(
        [["Item", "Amount"], ["A | B", "$1"], ["Short"]], doc_id="d", page=1, index=0
    )
    assert table is not None
    assert table.markdown() == ("| Item | Amount |\n| --- | --- |\n| A \\| B | $1 |\n| Short |  |")


def test_cells_are_cleaned_and_blank_headers_named() -> None:
    assert clean_cell(None) == ""
    assert clean_cell("  two\nlines \t here ") == "two lines here"
    assert normalize_headers(["Asset", None, " "]) == ("Asset", "Column_2", "Column_3")


@pytest.mark.parametrize(
    "raw",
    [
        [],
        [["only", "a header"]],
        [[None, ""], ["a", "b"]],  # no header text at all
        [["h1", "h2"], [None, ""], ["", " "]],  # no data
    ],
    ids=["empty", "header-only", "blank-header", "blank-rows"],
)
def test_unusable_tables_are_skipped(raw: list[list[Any]]) -> None:
    assert table_from_rows(raw, doc_id="d", page=1, index=0) is None


def test_empty_rows_are_dropped_and_values_kept_verbatim() -> None:
    table = table_from_rows(
        [["Item", "Amount"], ["", ""], ['"As-is" Value', "$12,000"]], doc_id="d", page=3, index=2
    )
    assert table is not None
    assert table.rows == (('"As-is" Value', "$12,000"),)
    assert table.key == "d:3:t2"
