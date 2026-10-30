"""The fetcher protocol: one way to obtain cost index values from one source.

A fetcher is asked, by the refresh job (:mod:`basis.costs.refresh`), only for what the store
lacks: :meth:`Fetcher.wanted` turns the years the store already holds from this source into
the years to ask for, and :meth:`Fetcher.fetch` returns :class:`IndexEntry` rows with their
citation and the fetched document's sha256. A fetcher never touches the database and never
decides what a study uses: the store keeps every value it returns (append-only, versioned),
and the selection (:mod:`basis.costs.indices.select`) picks among sources by the configured
priority. A fetcher that needs the network says so (``network``), so the job skips it while
``COST_INDEX_FETCH`` is off; one that needs credentials says why it is unavailable
(:meth:`Fetcher.unavailable`), so the run is recorded as skipped with the reason.

Fetchers: :mod:`basis.costs.indices.book` (the released 2020-book tables, offline, always
available), :mod:`basis.costs.indices.rsmeans_public` (Gordian's free quarterly pages of
the same index, the public source), :mod:`basis.costs.indices.rsmeans_api` (a stub: RSMeans
Data Online is a subscription).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, runtime_checkable

from basis.config import Settings
from basis.db.repositories.cost_index import IndexEntry, Series

Wanted = Mapping[Series, Sequence[int]]


@dataclass(frozen=True, slots=True)
class Fetched:
    """What one fetch returned: the values (possibly none), the document they were read
    from and the years that could not be read (``failures``, one line each)."""

    entries: tuple[IndexEntry, ...] = ()
    document_sha256: str | None = None
    url: str | None = None
    failures: tuple[str, ...] = ()


@runtime_checkable
class Fetcher(Protocol):
    @property
    def name(self) -> str:
        """The ``source`` written on every row this fetcher returns."""
        ...

    @property
    def network(self) -> bool:
        """True when fetching reaches outside the process (skipped while fetching is off)."""
        ...

    def unavailable(self, settings: Settings) -> str | None:
        """Why this source cannot be asked under ``settings`` (missing credentials), or None."""
        ...

    def wanted(
        self, have: Mapping[Series, Mapping[int, Any]], today: date
    ) -> dict[Series, list[int]]:
        """The years to ask for, per series, given the rows the store holds from this
        source by year (the national row of the historical series; any row of a city
        year); empty when there is nothing new to ask for."""
        ...

    async def fetch(self, wanted: Wanted, *, timeout: float) -> Fetched:
        """Obtain the wanted values. Raises on a failure that voids the whole fetch; a
        year that could not be read is a line in ``failures``."""
        ...


__all__ = ["Fetched", "Fetcher", "Wanted"]
