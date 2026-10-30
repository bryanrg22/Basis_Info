"""RSMeans Data Online (``source`` ``rsmeans_api``): a stub.

Gordian sells the current Historical Cost Index and City Cost Index through RSMeans Data
Online, a subscription with an API for enterprise plans; the request and response contract
is behind the subscription, so there is nothing public to implement against
(COST_INDICES.md section 11). This fetcher reads the credentials from the settings
(``RSMEANS_API_URL``, ``RSMEANS_API_KEY``) and reports itself unavailable without them, so a
refresh run records why no RSMeans value arrived; with them it records that the client is
not implemented, rather than scraping. It never reaches the network.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Final

from basis.config import Settings
from basis.costs.indices.protocol import Fetched, Wanted
from basis.db.repositories.cost_index import Series
from basis.domain.cost_indices import indices

SOURCE: Final = "rsmeans_api"
NOT_IMPLEMENTED: Final = (
    "the RSMeans Data Online client is not implemented: the API contract is part of the "
    "subscription (COST_INDICES.md section 11); enter the year's Historical Cost Index "
    "manually (POST /v1/cost-indices) until it is"
)


class RsmeansApiFetcher:
    name = SOURCE
    network = True

    def unavailable(self, settings: Settings) -> str | None:
        if settings.rsmeans_api_url is None or settings.rsmeans_api_key is None:
            return (
                "RSMEANS_API_URL and RSMEANS_API_KEY are not configured (RSMeans Data Online "
                "is a subscription; COST_INDICES.md section 11)"
            )
        return None

    def wanted(
        self, have: Mapping[Series, Mapping[int, Any]], today: date
    ) -> dict[Series, list[int]]:
        first = indices().last_year + 1
        years = [
            y for y in range(first, today.year + 1) if y not in set(have.get("historical", {}))
        ]
        return {"historical": years, "city": []}

    async def fetch(self, wanted: Wanted, *, timeout: float) -> Fetched:
        return Fetched(failures=(NOT_IMPLEMENTED,))


__all__ = ["NOT_IMPLEMENTED", "SOURCE", "RsmeansApiFetcher"]
