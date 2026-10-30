"""The fetchers by name, and which ones ``COST_INDEX_SOURCES`` enables.

Adding a source: one module ``basis/costs/indices/<source>.py`` with a class that satisfies
:class:`~basis.costs.indices.protocol.Fetcher`, registered here under its ``name``. The
refresh job asks the enabled fetchers in the configured order; the selection ranks their
values in the same order (:func:`basis.costs.indices.select.source_priority`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Final

from basis.config import ConfigError, Settings
from basis.costs.indices.book import BookFetcher
from basis.costs.indices.protocol import Fetcher
from basis.costs.indices.rsmeans_api import RsmeansApiFetcher
from basis.costs.indices.rsmeans_public import RsmeansPublicFetcher

FETCHERS: Final[dict[str, Callable[[], Fetcher]]] = {
    BookFetcher.name: BookFetcher,
    RsmeansPublicFetcher.name: RsmeansPublicFetcher,
    RsmeansApiFetcher.name: RsmeansApiFetcher,
}


def source_names(settings: Settings) -> list[str]:
    names = [s.strip() for s in settings.cost_index_sources.split(",") if s.strip()]
    unknown = [n for n in names if n not in FETCHERS]
    if unknown:
        raise ConfigError(
            f"COST_INDEX_SOURCES names unknown sources {unknown}; known: {sorted(FETCHERS)}"
        )
    return names


def fetchers_for(settings: Settings, only: Sequence[str] | None = None) -> list[Fetcher]:
    """The enabled fetchers in ``COST_INDEX_SOURCES`` order (``only`` narrows to some of
    them by name; a name not enabled raises ``ConfigError``)."""
    names = source_names(settings)
    if only is not None:
        missing = [n for n in only if n not in names]
        if missing:
            raise ConfigError(f"sources {missing} are not enabled in COST_INDEX_SOURCES {names}")
        names = [n for n in names if n in only]
    return [FETCHERS[name]() for name in names]


__all__ = ["FETCHERS", "fetchers_for", "source_names"]
