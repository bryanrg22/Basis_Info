"""Gordian's free quarterly Historical Cost Index pages (``source`` ``rsmeans_public``).

Gordian publishes the actual RSMeans Historical Cost Index, free and without a login, on
one landing page per cost-book year, ``https://www.rsmeans.com/landing-pages/<year>-rsmeans-
cost-index``, as four lines of the form ``The actual July 2024 Historical Index = 294.8``
(January, April, July, October; verified for every year 2020-2026 on 2026-10-30,
COST_INDICES.md section 11). It is the same series the books print (30-city national
average, base Jan. 1, 1993 = 100; Gordian's Quarterly Construction Cost Insights reports
state the base), so no conversion is needed: the stored value is RSMeans' own number with
the page as its citation and the page's sha256 on the row.

The year's value is the **July** figure, the convention of the books' Historical Cost Index
table (one value per past year, July 1; the 2020 book's own 2019 line is July 2019 =
232.2). Until a year's July value is published, the latest earlier quarter stands in,
marked provisional (``detail.provisional``, ``as_of`` the quarter's date); the next refresh
asks for that year again and the July value becomes the next version when it appears. The
2020 book's cost basis stays Jan. 1, 2020 = 239.1 (the book's estimate for its own costs):
the actual July 2020 value (234.6) is a different point in time and is stored as 2020's
July value like any other year's.

Parsing is deliberately strict: HTML comments and scripts are removed first (the 2026 page
carries the previous year's lines inside a comment), a month found twice with different
values fails the year rather than guessing, and a page without any line of the pattern
fails the year with the reason. A failure is a ledger row, never a stored value.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import httpx

from basis.config import Settings
from basis.costs.indices.protocol import Fetched, Wanted
from basis.db.repositories.cost_index import NATIONAL, IndexEntry, Series
from basis.domain.cost_indices import indices

SOURCE: Final = "rsmeans_public"
URL_TEMPLATE: Final = "https://www.rsmeans.com/landing-pages/{year}-rsmeans-cost-index"
FIRST_YEAR: Final = 2020  # the earliest landing page
USER_AGENT: Final = "basis-cost-index-refresh/1 (+https://github.com/bryanrg22/Basis_Info)"
PUBLISHER: Final = "RSMeans data from Gordian"
BASE: Final = "Jan. 1, 1993 = 100 (30-city national average)"
MONTHS: Final[dict[str, int]] = {"January": 1, "April": 4, "July": 7, "October": 10}
ANNUAL_MONTH: Final = "July"
LINE: Final = re.compile(
    r"The actual (January|April|July|October) (\d{4}) Historical Index\s*=\s*([0-9]+(?:\.[0-9]+)?)"
)
COMMENT: Final = re.compile(r"<!--.*?-->", re.S)
SCRIPT: Final = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)


class PageError(ValueError):
    """The page does not carry the year's values in the published form."""


def page_url(year: int) -> str:
    return URL_TEMPLATE.format(year=year)


def parse_page(html: str, year: int) -> dict[str, tuple[Decimal, str]]:
    """``{month: (value, the line as printed)}`` of ``year`` from the page's HTML."""
    text = SCRIPT.sub(" ", COMMENT.sub(" ", html))
    found: dict[str, tuple[Decimal, str]] = {}
    for match in LINE.finditer(text):
        month, printed_year, number = match.group(1), int(match.group(2)), match.group(3)
        if printed_year != year:
            continue
        try:
            value = Decimal(number)
        except InvalidOperation as exc:  # pragma: no cover - the pattern admits numbers only
            raise PageError(f"{month} {year}: not a number: {number!r}") from exc
        line = re.sub(r"\s+", " ", match.group(0))
        if month in found and found[month][0] != value:
            raise PageError(
                f"{month} {year} is printed twice with different values "
                f"({found[month][0]} and {value}); the page is not read"
            )
        found[month] = (value, line)
    if not found:
        raise PageError(f"no 'The actual <month> {year} Historical Index = <value>' line")
    return found


def entry_for(
    year: int, quarters: Mapping[str, tuple[Decimal, str]], *, url: str, sha256: str
) -> IndexEntry:
    """The year's entry: July when published, else the latest earlier quarter (provisional)."""
    if ANNUAL_MONTH in quarters:
        month = ANNUAL_MONTH
    else:
        earlier = [m for m in MONTHS if m in quarters and MONTHS[m] < MONTHS[ANNUAL_MONTH]]
        if not earlier:
            raise PageError(f"{year}: no July value and no earlier quarter")
        month = earlier[-1]
    value, line = quarters[month]
    provisional = month != ANNUAL_MONTH
    citation = f'{PUBLISHER}, {year} cost index page: "{line}" ({BASE}), {url}' + (
        " [provisional: the July value is not published yet]" if provisional else ""
    )
    detail: dict[str, Any] = {
        "month": month,
        "quarters": {m: str(v) for m, (v, _) in quarters.items()},
        "wording": line,
        "base": BASE,
        "provisional": provisional,
        "rule": "the year's value is the July figure, as the books' Historical Cost Index "
        "table lists past years",
    }
    return IndexEntry(
        source=SOURCE,
        series="historical",
        year=year,
        region=NATIONAL,
        value=value,
        as_of=date(year, MONTHS[month], 1),
        citation=citation,
        url=url,
        document_sha256=sha256,
        detail=detail,
    )


class RsmeansPublicFetcher:
    name = SOURCE
    network = True

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport  # tests pass a MockTransport with recorded pages

    def unavailable(self, settings: Settings) -> str | None:
        return None

    def wanted(
        self, have: Mapping[Series, Mapping[int, Any]], today: date
    ) -> dict[Series, list[int]]:
        """Every page year the store lacks, plus a year whose stored value is provisional
        (the July figure may have appeared since)."""
        held = have.get("historical", {})
        first = max(FIRST_YEAR, indices().first_year)
        years = []
        for year in range(first, today.year + 1):
            row = held.get(year)
            if row is None:
                years.append(year)
                continue
            detail = getattr(row, "detail", None)
            if isinstance(detail, dict) and detail.get("provisional"):
                years.append(year)
        return {"historical": years, "city": []}

    async def fetch(self, wanted: Wanted, *, timeout: float) -> Fetched:
        years = sorted(wanted.get("historical", ()))
        entries: list[IndexEntry] = []
        failures: list[str] = []
        digests: list[str] = []
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            for year in years:
                url = page_url(year)
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    body = response.text
                    sha256 = hashlib.sha256(response.content).hexdigest()
                    quarters = parse_page(body, year)
                    entries.append(entry_for(year, quarters, url=url, sha256=sha256))
                    digests.append(sha256)
                except (httpx.HTTPError, PageError) as exc:
                    failures.append(f"{year}: {type(exc).__name__}: {exc} ({url})")
        return Fetched(
            entries=tuple(entries),
            document_sha256=digests[0] if len(years) == 1 and digests else None,
            url=page_url(years[0]) if len(years) == 1 else None,
            failures=tuple(failures),
        )


__all__ = [
    "ANNUAL_MONTH",
    "FIRST_YEAR",
    "SOURCE",
    "URL_TEMPLATE",
    "PageError",
    "RsmeansPublicFetcher",
    "entry_for",
    "page_url",
    "parse_page",
]
