"""The cost index store's bodies: ``POST`` / ``GET /v1/cost-indices`` and ``/runs``."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Final, Literal

from pydantic import Field, model_validator

from basis.api.schemas.common import LINE_PATTERN, NO_NUL_PATTERN, RequestModel, ResponseModel
from basis.db.models import CostIndexRun, CostIndexValue

MAX_CITATION: Final = 1000
MAX_URL: Final = 2000
MAX_REGION: Final = 64
MAX_DETAIL_KEYS: Final = 16
MAX_DETAIL_VALUE: Final = 200
# A positive decimal with at most ten integer digits and four decimals (NUMERIC(14, 4)).
VALUE_PATTERN: Final = r"^[0-9]{1,10}(\.[0-9]{1,4})?$"
NATIONAL: Final = "US"
# A state code, a state mean, or a city row keyed by its ZIP prefixes as the book prints
# them: ``US``, ``CA``, ``CA:900-902``, ``NY:100-102,104``.
REGION_PATTERN: Final = r"^(US|[A-Z]{2}|[A-Z]{2}:[0-9]{3}(-[0-9]{3})?(,[0-9]{3}(-[0-9]{3})?)*)$"

Series = Literal["historical", "city"]
Citation = Annotated[str, Field(min_length=1, max_length=MAX_CITATION, pattern=LINE_PATTERN)]
Region = Annotated[str, Field(min_length=2, max_length=MAX_REGION, pattern=REGION_PATTERN)]
Url = Annotated[str, Field(min_length=1, max_length=MAX_URL, pattern=NO_NUL_PATTERN)]
DetailValue = Annotated[str, Field(max_length=MAX_DETAIL_VALUE, pattern=NO_NUL_PATTERN)]


class CostIndexCreate(RequestModel):
    """One value entered by hand, with its citation (source `manual`)."""

    series: Series = Field(
        description="`historical`: the RSMeans Historical Cost Index (Jan. 1, 1993 = 100), "
        "one national value per year; `city`: a location factor (the City Cost Index's "
        "weighted average over 100) of one city row or state."
    )
    year: int = Field(ge=1900, le=2200, description="The year the value is for.")
    region: Region = Field(
        default=NATIONAL,
        description="`US` for a historical value; for a city row `<state>:<ZIP prefixes>` as "
        "the book prints them (`CA:900-902`), or a state code for a state value.",
    )
    value: Annotated[str, Field(pattern=VALUE_PATTERN)] = Field(
        description="The index as printed, as a decimal string (`294.8`, `0.96`): up to ten "
        "digits and four decimals, above zero (the store's column scale; the pattern is the "
        "whole rule, so a client can check it)."
    )
    as_of: date = Field(description="The date the value is for (`2024-07-01` for a July index).")
    citation: Citation = Field(
        description="Publication, table and page (or URL) as printed: what the IRS guide asks "
        "of a cost source (ATG ch. 3 C.2(2))."
    )
    url: Url | None = None
    derived: Citation | None = Field(
        default=None,
        description="Only for a value that is not a published RSMeans index but one you "
        "converted from another series: the conversion and its error band. Every row "
        "priced with it is flagged `cost:YEAR_INDEX_CONVERTED`; a published value for the "
        "same year outranks it.",
    )
    detail: dict[str, DetailValue | int | bool] | None = Field(
        default=None,
        description="For a city row: `city` (required), `state`, `zip_prefixes`, `printed_page`; "
        "`estimated` for a published estimate.",
    )

    @property
    def decimal(self) -> Decimal:
        return Decimal(self.value)

    @model_validator(mode="after")
    def _consistent(self) -> CostIndexCreate:
        if self.series == "historical" and self.region != NATIONAL:
            raise ValueError("a historical value is national: region must be US")
        if self.series == "city":
            if self.region == NATIONAL:
                raise ValueError("a city value needs a region: <state>:<ZIP prefixes> or a state")
            if ":" in self.region and not (self.detail or {}).get("city"):
                raise ValueError("a city row needs detail.city (the city's name as printed)")
        if Decimal(self.value) <= 0:
            raise ValueError("value must be above zero")
        if self.as_of.year != self.year:
            raise ValueError("as_of must fall in the value's year")
        if self.detail is not None and len(self.detail) > MAX_DETAIL_KEYS:
            raise ValueError(f"detail has more than {MAX_DETAIL_KEYS} keys")
        return self


class CostIndexOut(ResponseModel):
    id: uuid.UUID
    source: str = Field(
        description="`rsmeans_2020_book`, `rsmeans_public`, `manual` or `rsmeans_api`."
    )
    series: Series
    year: int
    region: str
    value: Decimal
    as_of: date
    citation: str
    url: str | None
    derived: str | None = Field(
        description="How a non-RSMeans value was converted to the RSMeans base, with its error "
        "band; null for a published value."
    )
    version: int = Field(description="1 for the first value of this key; a revision is the next.")
    obtained_at: datetime
    obtained_by: uuid.UUID | None = Field(description="The user of a manual entry.")
    obtained_by_name: str = Field(description="Their name or email, or the fetcher's name.")
    document_sha256: str | None
    run_id: uuid.UUID | None
    detail: dict[str, Any] | None

    @classmethod
    def of(cls, row: CostIndexValue) -> CostIndexOut:
        return cls.model_validate(row)


class CostIndexRunOut(ResponseModel):
    id: uuid.UUID
    source: str
    trigger: str = Field(description="`seed`, `refresh`, `manual` or `import`.")
    actor_type: str
    actor_id: uuid.UUID | None
    actor_name: str | None
    status: str = Field(description="`ok`, `skipped` (with `detail.reason`) or `failed`.")
    started_at: datetime
    finished_at: datetime
    rows_added: int
    years_wanted: list[int] | None
    years_added: list[int] | None
    failures: list[str] | None
    document_sha256: str | None
    detail: dict[str, Any] | None

    @classmethod
    def of(cls, row: CostIndexRun) -> CostIndexRunOut:
        return cls.model_validate(row)


class CostIndexCreatedOut(ResponseModel):
    value: CostIndexOut
    run: CostIndexRunOut = Field(description="The ledger row of this entry (who, when).")


class CostIndexPageOut(ResponseModel):
    items: list[CostIndexOut]
    next_cursor: str | None


class CostIndexRunsOut(ResponseModel):
    items: list[CostIndexRunOut]


__all__ = [
    "REGION_PATTERN",
    "VALUE_PATTERN",
    "CostIndexCreate",
    "CostIndexCreatedOut",
    "CostIndexOut",
    "CostIndexPageOut",
    "CostIndexRunOut",
    "CostIndexRunsOut",
]
