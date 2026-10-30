"""The RSMeans cost indices (EVAL_REPORT section 45, COST_INDICES.md): the released file
matches its manifest, every state and year resolves, the factors are the transcribed
ratios, the ZIP lookup finds the city, the extrapolation rule and the supplement.

The data is public reference data (published index numbers, no client data); the spot
checks below were read from the page images of the two 2020 books in the corpus.
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from basis.domain import cost_indices as ci

D = Decimal
BOOK = ci.indices()
RAW = json.loads((ci.DATA_DIR / ci.RELEASED_FILE).read_text(encoding="utf-8"))

STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY",
}  # fmt: skip

# (state, city, factor, printed page) as read from the page images.
SPOT_CHECKS = [
    ("AL", "Birmingham", "0.96", 718),
    ("AK", "Ketchikan", "1.30", 718),
    ("CA", "Los Angeles", "0.99", 718),
    ("CA", "San Francisco", "1.03", 718),
    ("CA", "San Mateo", "1.03", 718),
    ("DC", "Washington", "0.99", 718),
    ("HI", "Honolulu", "1.19", 718),
    ("IA", "Davenport", "0.99", 719),
    ("ID", "Coeur d'Alene", "1.11", 719),
    ("MA", "Boston", "1.02", 719),
    ("MT", "Butte", "0.97", 720),
    ("NY", "New York", "1.01", 720),
    ("NY", "Long Island City", "1.02", 720),
    ("PA", "Norristown", "0.90", 721),
    ("SD", "Watertown", "0.96", 721),
    ("TX", "Houston", "0.99", 721),
    ("TX", "Huntsville", "0.96", 721),
    ("VA", "Fredericksburg", "0.95", 722),
    ("WI", "Green Bay", "1.05", 722),
    ("WY", "Rock Springs", "1.06", 722),
]

# (year, index Jan. 1, 1993 = 100) as read from Building Construction Costs 2020 p.778.
HCI_SPOT_CHECKS = [
    (2020, "239.1"), (2019, "232.2"), (2018, "222.9"), (2015, "206.2"), (2010, "183.5"),
    (2009, "180.1"), (2008, "180.4"), (2005, "151.6"), (2000, "120.9"), (1993, "101.7"),
    (1985, "82.6"), (1978, "53.5"), (1970, "28.7"),
]  # fmt: skip


# ------------------------------------------------------------------ the released file


def test_the_released_file_matches_its_manifest_and_a_tampered_copy_is_refused(
    tmp_path: Path,
) -> None:
    manifest = json.loads((ci.DATA_DIR / "manifest.json").read_text())
    assert manifest["files"][ci.RELEASED_FILE]["sha256"] == BOOK.sha256
    copy = tmp_path / ci.RELEASED_FILE
    copy.write_text((ci.DATA_DIR / ci.RELEASED_FILE).read_text().replace('"0.96"', '"0.97"', 1))
    with pytest.raises(ci.CostIndexError, match="changed after release"):
        ci.load_indices(copy, ci.DATA_DIR / "manifest.json")
    # Without a manifest the copy loads (a test fixture or a future release candidate).
    assert ci.load_indices(copy, None).name == "rsmeans_2020"


def test_the_tables_cover_every_state_and_the_index_years() -> None:
    assert set(BOOK.by_state) == STATES and len(STATES) == 51
    assert (BOOK.first_year, BOOK.last_year) == (1970, 2020)
    assert set(BOOK.years) == set(range(1970, 2021))
    assert len(BOOK.cities) == 651 and len(BOOK.by_prefix) == 908
    # One value per year: July 1, and Jan. 1 for the book's own year.
    for year, entry in BOOK.years.items():
        assert entry.as_of.isoformat() == (f"{year}-01-01" if year == 2020 else f"{year}-07-01")
    assert BOOK.base.year == 2020 and BOOK.base.index == D("239.1") and BOOK.base.estimated


def test_the_printed_2020_base_column_agrees_with_the_1993_base_column() -> None:
    """Two columns were transcribed per year; the book computes the second from the
    first, so they check each other (within the book's own rounding)."""
    for row in RAW["historical_cost_index"]["annual"]:
        computed = D(row["index"]) / D("239.1") * 100
        assert abs(computed - D(row["index_2020_base"])) <= D("0.1"), row


@pytest.mark.parametrize(("state", "city", "factor", "page"), SPOT_CHECKS)
def test_location_rows_as_read_from_the_page_images(
    state: str, city: str, factor: str, page: int
) -> None:
    row = next(r for r in BOOK.by_state[state] if r.city == city)
    assert (row.factor, row.printed_page) == (D(factor), page)


@pytest.mark.parametrize(("year", "index"), HCI_SPOT_CHECKS)
def test_index_values_as_read_from_the_page_image(year: int, index: str) -> None:
    assert BOOK.years[year].index == D(index)


def test_no_client_data_in_the_file() -> None:
    """Every value is a published index number or a place name from the book: no field
    beyond the schema's, no five-digit number (a ZIP, a parcel or a dollar amount)."""
    values = json.dumps(RAW["location_factors"]["cities"]) + json.dumps(
        RAW["historical_cost_index"]["annual"]
    )
    assert re.search(r"\d{5,}", values) is None
    for row in RAW["location_factors"]["cities"]:
        assert set(row) == {"state", "zip_prefixes", "city", "factor", "printed_page"}
    for row in RAW["historical_cost_index"]["annual"]:
        assert set(row) <= {"year", "as_of", "index", "index_2020_base", "estimated"}


# ------------------------------------------------------------------ the year factor


@pytest.mark.parametrize(
    ("year", "factor"),
    [(2020, "1.0000"), (2019, "0.9711"), (2018, "0.9322"), (2010, "0.7675"), (1970, "0.1200")],
)
def test_year_factor_is_the_index_over_the_2020_basis(year: int, factor: str) -> None:
    got = ci.year_factor_for(year)
    assert got.factor == D(factor) and not got.extrapolated
    assert got.factor == (BOOK.years[year].index / D("239.1")).quantize(
        D("0.0001"), rounding=ROUND_HALF_UP
    )
    assert "RSMeans Building Construction Costs 2020 p.778" in got.citation()


def test_every_index_year_resolves_and_only_years_before_the_table_fail() -> None:
    for year in range(1970, 2021):
        assert ci.year_factor_for(year).extrapolated is False
    for year in (1969, 1900):
        with pytest.raises(ci.UnknownCostYear, match="1970-2020"):
            ci.year_factor_for(year)


@pytest.mark.parametrize("year", [2021, 2024, 2026, 2040])
def test_a_year_after_the_latest_index_takes_it_with_the_assumption(year: int) -> None:
    got = ci.year_factor_for(year)
    assert got.factor == D("1.0000") and got.extrapolated
    assert got.index is BOOK.years[2020]
    assert got.assumption is not None
    assert f"no published cost index for {year} is loaded" in got.assumption
    assert "COST_INDEX_SUPPLEMENT_PATH" in got.assumption


def test_year_ratio_moves_a_cost_between_two_published_years() -> None:
    factor, formula = ci.year_ratio(2018, 2015)
    assert factor == (D("222.9") / D("206.2")).quantize(D("0.0001"))
    assert formula == "HCI 222.9 (July 2018) / HCI 206.2 (July 2015)"
    with pytest.raises(KeyError):
        ci.year_ratio(2024, 2020)  # 2024 has no published index loaded
    with pytest.raises(ci.UnknownCostYear):
        ci.year_ratio(2020, 1950)


def test_citations_name_the_index_values_and_the_page() -> None:
    assert ci.year_factor_for(2018).citation() == (
        "HCI 222.9 (July 2018) / 239.1 (Jan 2020, est.), "
        "RSMeans Building Construction Costs 2020 p.778"
    )


# ------------------------------------------------------------------ the location factor


def test_every_state_mean_is_the_mean_of_its_listed_cities() -> None:
    for state, rows in BOOK.by_state.items():
        got = ci.location_factor_for_state_code(state)
        assert got is not None and got.is_state_mean and got.cities == len(rows)
        mean = sum((r.factor for r in rows), D(0)) / len(rows)
        assert got.factor == mean.quantize(D("0.001"), rounding=ROUND_HALF_UP)
        assert got.low == min(r.factor for r in rows) and got.high == max(r.factor for r in rows)
        assert "pp.718-722" in got.citation()


@pytest.mark.parametrize(
    ("state", "factor", "cities"),
    [
        ("CA", "1.001", 37),
        ("NY", "0.976", 32),
        ("IL", "0.959", 19),
        ("HI", "1.165", 2),
        ("DC", "0.990", 1),
    ],
)
def test_state_means(state: str, factor: str, cities: int) -> None:
    got = ci.location_factor_for_state_code(state.lower())
    assert got is not None and (got.factor, got.cities) == (D(factor), cities)


def test_unknown_state_or_zip_is_none() -> None:
    assert ci.location_factor_for_state_code("ZZ") is None
    assert ci.location_factor_for_state_code("PR") is None  # in the book, not transcribed
    assert ci.location_factor_for_zip("00501") is None  # 005 is not a listed prefix
    assert ci.location_factor_for_zip("12") is None


def test_zip_lookup_finds_the_city_by_its_first_three_digits() -> None:
    la = ci.location_factor_for_zip("90210")
    assert la is not None and la.city is not None
    assert (la.city.city, la.factor, la.state, la.cities) == ("Los Angeles", D("0.99"), "CA", 1)
    assert la.citation() == (
        "900-902 Los Angeles 0.99, RSMeans Residential 2020 Location Factors - Residential p.718"
    )
    # A nine-digit ZIP, a range member and a comma list member.
    assert ci.location_factor_for_zip("10001-1234").city.city == "New York"  # type: ignore[union-attr]
    assert ci.location_factor_for_zip("95712").city.city == "Sacramento"  # type: ignore[union-attr]
    assert ci.location_factor_for_zip("11801").city.city == "Hicksville"  # type: ignore[union-attr]
    assert ci.location_factor_for_zip("88510").city.city == "El Paso"  # type: ignore[union-attr]


def test_every_zip_prefix_names_one_city() -> None:
    seen: dict[str, str] = {}
    for city in BOOK.cities:
        for prefix in city.prefixes():
            assert prefix not in seen, (prefix, city.city, seen[prefix])
            seen[prefix] = city.city
            assert ci.location_factor_for_zip(prefix + "00").city is city  # type: ignore[union-attr]


# ------------------------------------------------------------------ the supplement


def supplement(**entries: object) -> dict[str, object]:
    return {"schema_version": 1, "historical_cost_index": list(entries.values())}


def test_a_supplement_adds_later_years_each_with_its_source(tmp_path: Path) -> None:
    doc = supplement(
        a={"year": 2024, "as_of": "2024-01-01", "index": "300.0", "source": "Book 2024 p.1"},
    )
    path = tmp_path / "supplement.json"
    path.write_text(json.dumps(doc))
    loaded = ci.load_supplement(str(path))
    assert loaded[2024].source == "Book 2024 p.1"
    got = ci.year_factor_for(2024, loaded)
    assert (got.factor, got.extrapolated) == (D("1.2547"), False)
    assert got.citation() == (
        "HCI 300.0 (Jan 2024) [Book 2024 p.1] / 239.1 (Jan 2020, est.) "
        "[RSMeans Building Construction Costs 2020 p.778]"
    )
    # A later year still extrapolates, from the supplement's latest value now.
    later = ci.year_factor_for(2026, loaded)
    assert later.extrapolated and later.index is loaded[2024] and later.factor == D("1.2547")
    assert ci.year_ratio(2024, 2020, loaded)[0] == D("1.2547")
    assert ci.supplement_for(None) is None


@pytest.mark.parametrize(
    ("doc", "match"),
    [
        ({"schema_version": 2, "historical_cost_index": []}, "schema_version"),
        ({"schema_version": 1}, "must be a list"),
        (
            supplement(a={"year": 2024, "as_of": "2024-01-01", "index": "300.0"}),
            "its own source",
        ),
        (
            supplement(a={"year": 2020, "as_of": "2020-07-01", "index": "240.0", "source": "x"}),
            "covers through 2020",
        ),
        (
            supplement(a={"year": 2024, "as_of": "2023-12-01", "index": "300.0", "source": "x"}),
            "dated",
        ),
        (
            supplement(a={"year": 2024, "as_of": "2024-01-01", "index": "-1", "source": "x"}),
            "positive",
        ),
    ],
)
def test_a_bad_supplement_is_refused_with_the_defect(doc: dict[str, object], match: str) -> None:
    with pytest.raises(ci.CostIndexError, match=match):
        ci.parse_supplement(doc)


def test_an_empty_supplement_adds_nothing() -> None:
    assert ci.parse_supplement({"schema_version": 1, "historical_cost_index": []}) == {}


# ------------------------------------------------------------------ the parser


def test_the_parser_names_the_first_defect() -> None:
    doc = json.loads(json.dumps(RAW))
    doc["location_factors"]["cities"].append(
        {"state": "CA", "zip_prefixes": "900", "city": "Twice", "factor": "1.00", "printed_page": 1}
    )
    with pytest.raises(ci.CostIndexError, match="ZIP prefix 900 is listed for"):
        ci.parse_indices(doc)
    doc = json.loads(json.dumps(RAW))
    doc["historical_cost_index"]["cost_basis"]["index"] = "240.0"
    with pytest.raises(ci.CostIndexError, match="cost basis must be one of the annual"):
        ci.parse_indices(doc)
    with pytest.raises(ci.CostIndexError, match="schema_version"):
        ci.parse_indices({"schema_version": 0})
    with pytest.raises(ci.CostIndexError, match="bad ZIP prefix range"):
        ci.parse_indices(
            {
                **RAW,
                "location_factors": {
                    **RAW["location_factors"],
                    "cities": [
                        {
                            "state": "CA",
                            "zip_prefixes": "909-900",
                            "city": "x",
                            "factor": "1",
                            "printed_page": 1,
                        }
                    ],
                },
            }
        )
