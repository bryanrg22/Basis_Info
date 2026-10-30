"""The refresh job's pure parts: the CLI parser, the export document (what the offline
replay reads), the import of a supplement file, the run report."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from basis.config import ConfigError, Settings
from basis.costs.indices.registry import FETCHERS, fetchers_for, source_names
from basis.costs.refresh import (
    RunReport,
    build_parser,
    entries_from_supplement,
    export_document,
    manual_entry,
)
from basis.domain.cost_indices import YearIndex, parse_supplement


def settings(**overrides: object) -> Settings:
    return Settings(basis_env="test", **overrides)  # type: ignore[arg-type]


def test_the_registry_knows_three_sources_and_refuses_an_unknown_name() -> None:
    assert sorted(FETCHERS) == ["rsmeans_2020_book", "rsmeans_api", "rsmeans_public"]
    assert source_names(settings()) == ["rsmeans_2020_book", "rsmeans_public"]
    assert [f.name for f in fetchers_for(settings())] == ["rsmeans_2020_book", "rsmeans_public"]
    assert [f.name for f in fetchers_for(settings(), ["rsmeans_public"])] == ["rsmeans_public"]
    with pytest.raises(ConfigError, match="unknown sources \\['turner'\\]"):
        fetchers_for(settings(cost_index_sources="rsmeans_2020_book,turner"))
    with pytest.raises(ConfigError, match="not enabled"):
        fetchers_for(settings(cost_index_sources="rsmeans_2020_book"), ["rsmeans_public"])


def test_fetching_is_off_by_default_and_the_sources_are_settings() -> None:
    assert settings().cost_index_fetch == "off"
    assert settings(cost_index_fetch="on").cost_index_fetch == "on"
    assert settings().cost_index_fetch_timeout_s == 30.0
    assert settings().rsmeans_api_url is None and settings().rsmeans_api_key is None


def test_the_parser_has_every_command_and_defaults_to_refresh() -> None:
    parser = build_parser()
    assert parser.parse_args([]).command is None
    args = parser.parse_args(["refresh", "--source", "rsmeans_public", "--trigger", "seed"])
    assert (args.command, args.source, args.trigger) == ("refresh", ["rsmeans_public"], "seed")
    assert parser.parse_args(["seed"]).command == "seed"
    entered = parser.parse_args(
        [
            "enter",
            "--series",
            "historical",
            "--year",
            "2024",
            "--value",
            "294.8",
            "--as-of",
            "2024-07-01",
            "--citation",
            "the page",
            "--by",
            "ann@firm.example",
        ]
    )
    assert (entered.year, entered.value, entered.region, entered.by) == (
        2024,
        "294.8",
        "US",
        "ann@firm.example",
    )
    assert parser.parse_args(["import", "x.json", "--by", "ann"]).file.name == "x.json"
    assert parser.parse_args(["export", "--out", "o.json"]).out.name == "o.json"
    listed = parser.parse_args(["list", "--series", "city", "--year", "2020", "--all-versions"])
    assert (listed.series, listed.year, listed.all_versions) == ("city", 2020, True)
    assert parser.parse_args(["runs", "--limit", "5"]).limit == 5


def test_the_export_round_trips_through_the_supplement_parser() -> None:
    years = {
        2024: YearIndex(2024, Decimal("294.8"), date(2024, 7, 1), "Gordian 2024 page"),
        2020: YearIndex(2020, Decimal("239.1"), date(2020, 1, 1), "the 2020 book", True),
        2025: YearIndex(
            2025, Decimal("320"), date(2025, 7, 1), "converted", derived="Turner x 239.1/1171"
        ),
    }
    document = export_document(years)
    assert [e["year"] for e in document["historical_cost_index"]] == [2020, 2024, 2025]
    assert document["historical_cost_index"][0] == {
        "year": 2020,
        "as_of": "2020-01-01",
        "index": "239.1",
        "source": "the 2020 book",
        "estimated": True,
    }
    assert document["historical_cost_index"][2]["derived"] == "Turner x 239.1/1171"
    parsed = parse_supplement(document)
    assert parsed[2024].index == Decimal("294.8") and parsed[2020].estimated is True
    entries = entries_from_supplement(parsed)
    assert [(e.year, e.source, e.region) for e in entries] == [
        (2020, "manual", "US"),
        (2024, "manual", "US"),
        (2025, "manual", "US"),
    ]
    assert entries[0].detail == {"estimated": True} and entries[1].detail is None


def test_a_manual_entry_and_a_run_report_serialize_without_nulls() -> None:
    entry = manual_entry(
        series="historical",
        year=2024,
        value=Decimal("294.8"),
        as_of=date(2024, 7, 1),
        citation="the page",
    )
    assert entry.key == ("manual", "historical", 2024, "US")
    report = RunReport(
        source="rsmeans_public",
        trigger="refresh",
        status="skipped",
        started_at="t0",
        finished_at="t1",
        reason="COST_INDEX_FETCH is off",
    )
    assert report.as_json() == {
        "source": "rsmeans_public",
        "trigger": "refresh",
        "status": "skipped",
        "started_at": "t0",
        "finished_at": "t1",
        "rows_added": 0,
        "unchanged": 0,
        "reason": "COST_INDEX_FETCH is off",
    }
