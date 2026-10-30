"""Gordian's free quarterly pages: the parser against a recorded page, the year's value
rule (July, else the latest earlier quarter marked provisional), and the fetcher over a
mock transport (no network)."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fixtures import fixtures_dir

from basis.config import Settings
from basis.costs.indices import rsmeans_public as public
from basis.costs.indices.rsmeans_public import (
    PageError,
    RsmeansPublicFetcher,
    entry_for,
    page_url,
    parse_page,
)

# A recorded public page (not a generated synthetic fixture): tests/fixtures/recorded.
PAGE: Path = fixtures_dir().parent / "recorded" / "rsmeans_2024_cost_index_page.html"


def page_text() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_the_recorded_page_parses_to_its_four_quarters_ignoring_comments_and_scripts() -> None:
    # The fixture carries decoys the live 2026 page has: the previous year's lines inside an
    # HTML comment, and here a script; neither is read.
    quarters = parse_page(page_text(), 2024)
    assert {m: str(v) for m, (v, _) in quarters.items()} == {
        "January": "295.6",
        "April": "295.5",
        "July": "294.8",
        "October": "296.3",
    }
    assert quarters["July"][1] == "The actual July 2024 Historical Index = 294.8"
    # Another year's lines on the same page are not this year's.
    with pytest.raises(PageError, match="no 'The actual <month> 2023 Historical Index"):
        parse_page(page_text(), 2023)


def test_a_month_printed_twice_with_different_values_fails_the_year() -> None:
    html = page_text().replace(
        "</body>", "<p>The actual July 2024 Historical Index = 301.0</p></body>"
    )
    with pytest.raises(PageError, match="July 2024 is printed twice"):
        parse_page(html, 2024)
    # The same value twice is not a conflict.
    same = page_text().replace(
        "</body>", "<p>The actual July 2024 Historical Index = 294.8</p></body>"
    )
    assert str(parse_page(same, 2024)["July"][0]) == "294.8"


def test_the_years_value_is_july_else_the_latest_earlier_quarter_marked_provisional() -> None:
    url = page_url(2024)
    quarters = parse_page(page_text(), 2024)
    entry = entry_for(2024, quarters, url=url, sha256="a" * 64)
    assert (entry.source, entry.series, entry.year, entry.region) == (
        "rsmeans_public",
        "historical",
        2024,
        "US",
    )
    assert entry.value == Decimal("294.8") and entry.as_of == date(2024, 7, 1)
    assert entry.url == url and entry.document_sha256 == "a" * 64
    assert "The actual July 2024 Historical Index = 294.8" in entry.citation
    assert url in entry.citation and "provisional" not in entry.citation
    assert entry.detail is not None and entry.detail["provisional"] is False
    assert entry.detail["quarters"] == {
        "January": "295.6",
        "April": "295.5",
        "July": "294.8",
        "October": "296.3",
    }
    early = {m: q for m, q in quarters.items() if m in ("January", "April")}
    provisional = entry_for(2024, early, url=url, sha256="b" * 64)
    assert provisional.value == Decimal("295.5") and provisional.as_of == date(2024, 4, 1)
    assert provisional.detail is not None and provisional.detail["provisional"] is True
    assert "[provisional" in provisional.citation
    with pytest.raises(PageError, match="no July value and no earlier quarter"):
        entry_for(2024, {"October": quarters["October"]}, url=url, sha256="c" * 64)


class Row:
    def __init__(self, provisional: bool) -> None:
        self.detail = {"provisional": provisional}


def test_wanted_is_every_page_year_the_store_lacks_plus_provisional_ones() -> None:
    fetcher = RsmeansPublicFetcher()
    assert fetcher.name == "rsmeans_public" and fetcher.network is True
    assert fetcher.unavailable(Settings(basis_env="test")) is None
    today = date(2026, 10, 30)
    assert fetcher.wanted({"historical": {}, "city": {}}, today) == {
        "historical": [2020, 2021, 2022, 2023, 2024, 2025, 2026],
        "city": [],
    }
    held = {y: Row(provisional=False) for y in range(2020, 2027)}
    held[2026] = Row(provisional=True)
    assert fetcher.wanted({"historical": held, "city": {}}, today) == {
        "historical": [2026],
        "city": [],
    }


@pytest.mark.asyncio
async def test_fetch_reads_the_pages_records_the_digest_and_lists_each_failure() -> None:
    body = PAGE.read_bytes()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["User-Agent"].startswith("basis-cost-index-refresh")
        if str(request.url) == page_url(2024):
            return httpx.Response(200, content=body, headers={"content-type": "text/html"})
        if str(request.url) == page_url(2023):
            return httpx.Response(404, content=b"not here")
        return httpx.Response(200, content=b"<html>nothing of the kind</html>")

    fetcher = RsmeansPublicFetcher(transport=httpx.MockTransport(handler))
    fetched = await fetcher.fetch({"historical": [2023, 2024, 2025], "city": []}, timeout=5)
    assert calls == [page_url(2023), page_url(2024), page_url(2025)]
    assert [e.year for e in fetched.entries] == [2024]
    entry = fetched.entries[0]
    assert entry.value == Decimal("294.8")
    assert entry.document_sha256 == hashlib.sha256(body).hexdigest()
    assert len(fetched.failures) == 2
    assert fetched.failures[0].startswith("2023: HTTPStatusError")
    assert fetched.failures[1].startswith("2025: PageError: no 'The actual")
    assert fetched.document_sha256 is None  # several years: each row carries its own


@pytest.mark.asyncio
async def test_a_single_page_fetch_names_its_url_and_digest_on_the_result() -> None:
    body = PAGE.read_bytes()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    fetched = await RsmeansPublicFetcher(transport=transport).fetch(
        {"historical": [2024]}, timeout=5
    )
    assert fetched.url == page_url(2024)
    assert fetched.document_sha256 == hashlib.sha256(body).hexdigest()
    assert fetched.failures == ()


def test_the_page_url_and_constants_are_the_published_ones() -> None:
    assert page_url(2024) == "https://www.rsmeans.com/landing-pages/2024-rsmeans-cost-index"
    assert public.FIRST_YEAR == 2020 and public.ANNUAL_MONTH == "July"
