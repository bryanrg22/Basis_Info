"""Renovation projects on a study (docs/review_2026-10-15/RENOVATIONS.md, docs/api/RENOVATIONS.md).

A renovation the taxpayer made after placing the building in service is a separate asset with
its own placed-in-service date: "If the taxpayer places in service an improvement or addition
to an asset after the taxpayer placed the asset in service, ... the improvement or addition
and, if applicable, its structural components are a separate asset" (Treas. Reg.
1.168(i)-8(c)(4)(ii)(D)); the cost segregation guide treats "Newly constructed property, which
includes remodels of existing properties and additions to existing properties" as its own
case, priced from actual costs (IRS Publication 5653, rev. 2-2025, p.26). A renovation made
before the building was placed in service is part of what the taxpayer placed in service: one
date, no dispositions (IRS Practice Unit DCE-P-002, 1/16/2025: "A partial disposition cannot
occur before the building is PIS").

This module is the document the engineer writes on the study (``studies.renovations``,
migration 0039; ``PUT /v1/studies/{id}/renovations``) and the rules a client can be told about
in a 422; the schedules and dispositions it gives are :mod:`basis.evidence.renovations`.

What is an input and never computed here (the people's judgement, RENOVATIONS.md section 6):
whether a cost is capitalized (Reg. 1.263(a)-3; the CPA's ``capitalized_amount``), whether a
component was truly removed (the engineer's ``removed_by_project`` correction, with evidence),
whether to make the partial disposition election (the CPA, on the return), the bonus actually
claimed on the acquisition (``acquisition_bonus_rate``, the CPA's; the statutory default when
not given).

A removed acquisition line is an ``asset`` correction at the assets review (:data:`REMOVAL_FIELDS`):
asset ids are per revision, and a correction is how every decision of the engineer on a line
is carried from one revision to the next (``basis.activities.process_assets``).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from basis.domain.intake import TEXT_PATTERN, Fraction, PositiveMoney, Text

MAX_PROJECTS: Final = 20
MAX_INVOICES: Final = 200
MAX_PHOTOS: Final = 2000
MAX_VIEWS: Final = 20
KEY_PATTERN: Final = r"^[a-z0-9][a-z0-9-]{0,39}$"
LongText = Annotated[str, Field(min_length=1, max_length=500, pattern=TEXT_PATTERN)]
ProjectKey = Annotated[
    str,
    Field(
        pattern=KEY_PATTERN,
        description="A short key the engineer chooses (lower-case letters, digits, '-'; at "
        "most 40), stable across revisions: removal corrections name it.",
    ),
]
Kind = Literal["after_service", "before_service"]

# Where a project's membership is written on a row's takeoff (``process_assets``).
PROJECT_INPUT: Final = "renovation_project"
# Where a removal is kept on a row's takeoff (``process_assets``).
REMOVAL_INPUT: Final = "removal"
# The renovations version the revision's assets were built with (every takeoff, only when the
# study has a renovations document): a later version makes the pricing stale.
VERSION_INPUT: Final = "renovations_version"


class Invoice(BaseModel):
    """One invoice, pay application or contract (ATG p.28: actual cost records), uploaded to
    the study as a document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: uuid.UUID = Field(description="The uploaded file (kind `other`).")
    amount: PositiveMoney
    invoice_date: date | None = None
    description: Text | None = None


class ViewRef(BaseModel):
    """A dated aerial or street view (corroboration only; RENOVATIONS.md section 5)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    date: date
    reference: Text = Field(
        description="Which view: the property view's id, or the source and its image id."
    )


class AerialBracket(BaseModel):
    """The last dated view without the work and the first with it: corroboration, never a
    date, a cost or a disposition by itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    last_without: ViewRef
    first_with: ViewRef

    @model_validator(mode="after")
    def _ordered(self) -> AerialBracket:
        if self.first_with.date <= self.last_without.date:
            raise ValueError("the first view with the work must be after the last view without it")
        return self


class ComponentBracket(BaseModel):
    """An exterior component's dated views (the roof, siding, a deck), by its label."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: Text = Field(description="The component's label (e.g. `roof`, `deck`).")
    bracket: AerialBracket


class RenovationProject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: ProjectKey
    kind: Kind = Field(
        description="`after_service`: made after the building was placed in service, a "
        "separate schedule with its own date and the dispositions of what it removed; "
        "`before_service`: bought, renovated, then placed in service: part of the acquisition "
        "(one date, no dispositions), its capitalized cost an intake line "
        "`improvements_before_service`."
    )
    scope: LongText = Field(description="What was done, in the engineer's words.")
    addition: bool = Field(
        default=False,
        description="The project enlarged the building (an addition): no qualified "
        "improvement property, and no dispositions unless `removals_stated`.",
    )
    removals_stated: bool = Field(
        default=False,
        description="For an addition: the engineer states that it replaced components (the "
        "Practice Unit's example: an HVAC system replaced to serve the addition).",
    )
    contract_date: date | None = Field(
        default=None,
        description="The written binding contract's date: the acquisition date for bonus "
        "depreciation (Notice 2026-11 section 3.03, Reg. 1.168(k)-2(b)(5)).",
    )
    work_start_date: date = Field(
        description="When physical work of a significant nature began (the self-constructed "
        "property's acquisition date when there is no binding contract; Reg. "
        "1.168(k)-2(b)(5)(iv)(B))."
    )
    placed_in_service_date: date | None = Field(
        default=None,
        description="When the work was ready and available for use. Required for "
        "`after_service`; absent for `before_service` (the building's date applies).",
    )
    invoice_total: PositiveMoney = Field(description="The invoices' total.")
    invoices: list[Invoice] = Field(
        default_factory=list,
        max_length=MAX_INVOICES,
        description="The invoices; when given they must sum to `invoice_total`.",
    )
    capitalized_amount: PositiveMoney | None = Field(
        default=None,
        description="The amount the CPA confirms is capitalized (Reg. 1.263(a)-3): the "
        "renovation schedule is reconciled to it. Null until confirmed: the result withholds "
        "the project's schedule (blocker `renovation_capitalized`).",
    )
    capitalized_confirmed_by: Text | None = Field(
        default=None, description="Who confirmed it (the CPA's name and firm)."
    )
    after_photos: list[uuid.UUID] = Field(
        default_factory=list,
        max_length=MAX_PHOTOS,
        description="Photographs of the work as finished (the engineer's tag). The acquisition "
        "is built from the other photographs; this project's schedule from these.",
    )
    qip: Literal["auto", "off"] = Field(
        default="auto",
        description="`auto`: interior 39-year rows of a nonresidential building become "
        "qualified improvement property (15-year straight line) unless excluded; `off`: never "
        "(the CPA's call).",
    )
    aerial: AerialBracket | None = Field(
        default=None,
        description="For exterior work: the last dated view without it and the first with it.",
    )

    @model_validator(mode="after")
    def _consistent(self) -> RenovationProject:
        pis = self.placed_in_service_date
        if self.kind == "after_service":
            if pis is None:
                raise ValueError(f"project {self.key}: after_service needs placed_in_service_date")
            if pis < self.work_start_date:
                raise ValueError(
                    f"project {self.key}: placed_in_service_date cannot be before work_start_date"
                )
            if self.contract_date is not None and pis < self.contract_date:
                raise ValueError(
                    f"project {self.key}: placed_in_service_date cannot be before contract_date"
                )
        else:
            if pis is not None:
                raise ValueError(
                    f"project {self.key}: a before_service project has no placed_in_service_date "
                    "of its own (the building's applies)"
                )
            if self.after_photos:
                raise ValueError(
                    f"project {self.key}: a before_service project is part of the acquisition; "
                    "its photographs are not tagged after"
                )
        if self.invoices:
            total = sum((i.amount for i in self.invoices), Decimal(0))
            if total != self.invoice_total:
                raise ValueError(
                    f"project {self.key}: the invoices sum to {total}, not invoice_total "
                    f"{self.invoice_total}"
                )
            ids = [i.document_id for i in self.invoices]
            if len(set(ids)) != len(ids):
                raise ValueError(f"project {self.key}: an invoice document is listed twice")
        if self.capitalized_amount is not None:
            if self.capitalized_amount > self.invoice_total:
                raise ValueError(
                    f"project {self.key}: capitalized_amount {self.capitalized_amount} is above "
                    f"invoice_total {self.invoice_total}"
                )
            if self.capitalized_confirmed_by is None:
                raise ValueError(
                    f"project {self.key}: capitalized_amount needs capitalized_confirmed_by "
                    "(the CPA confirms what is capitalized)"
                )
        if self.removals_stated and not self.addition:
            raise ValueError(f"project {self.key}: removals_stated is only for an addition")
        if len(set(self.after_photos)) != len(self.after_photos):
            raise ValueError(f"project {self.key}: a photograph is listed twice")
        return self

    @property
    def bonus_acquired(self) -> date:
        """The acquisition date bonus depreciation keys on: the binding contract's date, else
        the start of physical work (self-constructed property)."""
        return self.contract_date or self.work_start_date


class StudyRenovationsIn(BaseModel):
    """The renovations document as the client sends it."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "projects": [
                        {
                            "key": "kitchen-2025",
                            "kind": "after_service",
                            "scope": "Kitchen remodel: cabinets, countertops and flooring replaced",
                            "contract_date": "2025-02-10",
                            "work_start_date": "2025-03-03",
                            "placed_in_service_date": "2025-05-30",
                            "invoice_total": "38500.00",
                            "capitalized_amount": "38500.00",
                            "capitalized_confirmed_by": "A. Preparer, CPA",
                            "after_photos": [],
                        }
                    ]
                }
            ]
        },
    )

    projects: list[RenovationProject] = Field(max_length=MAX_PROJECTS)
    acquisition_bonus_rate: Fraction | None = Field(
        default=None,
        description="The bonus depreciation actually claimed on the acquisition's 5, 7 and "
        "15-year property (0..1; the CPA's, from the return). Null: the statutory default for "
        "the acquisition's dates. The dispositions' depreciation allowed or allowable includes "
        "the removed line's share of it.",
    )
    exterior_brackets: list[ComponentBracket] = Field(
        default_factory=list,
        max_length=MAX_VIEWS,
        description="Exterior components' dated views (a component's year installed is checked "
        "against them).",
    )

    @model_validator(mode="after")
    def _unique(self) -> StudyRenovationsIn:
        keys = [p.key for p in self.projects]
        if len(set(keys)) != len(keys):
            raise ValueError("project keys must be unique")
        seen: dict[uuid.UUID, str] = {}
        for project in self.projects:
            for photo in project.after_photos:
                if photo in seen:
                    raise ValueError(
                        f"photo {photo} is tagged after both {seen[photo]} and {project.key}"
                    )
                seen[photo] = project.key
        invoices: dict[uuid.UUID, str] = {}
        for project in self.projects:
            for invoice in project.invoices:
                if invoice.document_id in invoices:
                    raise ValueError(
                        f"invoice {invoice.document_id} is listed on "
                        f"{invoices[invoice.document_id]} and {project.key}"
                    )
                invoices[invoice.document_id] = project.key
        return self

    def project(self, key: str) -> RenovationProject | None:
        return next((p for p in self.projects if p.key == key), None)

    def photo_projects(self) -> dict[str, str]:
        """``{photo document id: project key}`` of every after-tagged photo."""
        return {str(photo): p.key for p in self.projects for photo in p.after_photos}

    def document_ids(self) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
        """``(invoice document ids, photo document ids)`` the document names."""
        return (
            {i.document_id for p in self.projects for i in p.invoices},
            {photo for p in self.projects for photo in p.after_photos},
        )


class StudyRenovations(StudyRenovationsIn):
    """The stored document: the request (no field is added on write)."""

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra={})


@dataclass(frozen=True, slots=True)
class DateProblem:
    field: str
    message: str


def date_problems(
    renovations: StudyRenovationsIn,
    *,
    building_placed_in_service: date | None,
    acquisition_date: date | None,
) -> list[DateProblem]:
    """The project dates against the intake's: no after-service project before the building
    was placed in service (the Practice Unit's rule), no before-service work before the
    acquisition or ending after the building's placed-in-service date."""
    out: list[DateProblem] = []
    for p in renovations.projects:
        where = f"projects[{p.key}]"
        if p.kind == "after_service" and building_placed_in_service is not None:
            pis = p.placed_in_service_date
            assert pis is not None
            if pis <= building_placed_in_service:
                out.append(
                    DateProblem(
                        f"{where}.placed_in_service_date",
                        f"project {p.key} is placed in service on {pis}, not after the building "
                        f"({building_placed_in_service}): a renovation before the building is in "
                        "service is kind before_service",
                    )
                )
            if p.work_start_date < building_placed_in_service:
                out.append(
                    DateProblem(
                        f"{where}.work_start_date",
                        f"project {p.key} started on {p.work_start_date}, before the building "
                        f"was placed in service ({building_placed_in_service}): split the work "
                        "before the date into a before_service project",
                    )
                )
        if p.kind == "before_service":
            if acquisition_date is not None and p.work_start_date < acquisition_date:
                out.append(
                    DateProblem(
                        f"{where}.work_start_date",
                        f"project {p.key} started on {p.work_start_date}, before the property "
                        f"was acquired ({acquisition_date}): the seller's work is part of the "
                        "price, not a project",
                    )
                )
            if building_placed_in_service is not None and (
                p.work_start_date > building_placed_in_service
            ):
                out.append(
                    DateProblem(
                        f"{where}.work_start_date",
                        f"project {p.key} started after the building was placed in service "
                        f"({building_placed_in_service}): it is kind after_service",
                    )
                )
    return out


# ------------------------------------------------------------------ a removed acquisition line

REMOVED_BY_PROJECT: Final = "removed_by_project"
DISPOSITION_DATE: Final = "disposition_date"
REMOVED_SHARE: Final = "removed_share"
REMOVAL_FIELDS: Final = frozenset({REMOVED_BY_PROJECT, DISPOSITION_DATE, REMOVED_SHARE})


def has_removal(after: Mapping[str, Any]) -> bool:
    return bool(REMOVAL_FIELDS & set(after))


def split_removal(after: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {k: v for k, v in after.items() if k not in REMOVAL_FIELDS},
        {k: v for k, v in after.items() if k in REMOVAL_FIELDS},
    )


def removal_problems(after: Mapping[str, Any], reason: str | None) -> list[str]:
    """Why the removal in an ``asset`` correction cannot be applied: ``removed_by_project``
    (a project key) with ``disposition_date`` (ISO date) and optionally ``removed_share``
    (above 0, at most 1; default 1, the whole line), or ``removed_by_project: null`` alone to
    clear; the correction's reason is required either way."""
    if not has_removal(after):
        return []
    found: list[str] = []
    if reason is None or not reason.strip():
        found.append("a removal needs the correction's reason (what shows it was removed)")
    key = after.get(REMOVED_BY_PROJECT, "")
    if key is None:
        if set(REMOVAL_FIELDS & set(after)) != {REMOVED_BY_PROJECT}:
            found.append("clearing a removal takes removed_by_project: null alone")
        return found
    if not isinstance(key, str) or re.fullmatch(KEY_PATTERN, key) is None:
        found.append("removed_by_project must be a project key of the study's renovations")
    when = after.get(DISPOSITION_DATE)
    try:
        if not isinstance(when, str):
            raise ValueError
        date.fromisoformat(when)
    except ValueError:
        found.append("disposition_date must be an ISO date (YYYY-MM-DD)")
    if REMOVED_SHARE in after:
        share = after[REMOVED_SHARE]
        if (
            isinstance(share, bool)
            or not isinstance(share, int | float)
            or not 0 < Decimal(str(share)) <= 1
        ):
            found.append("removed_share must be a number above 0 and at most 1")
    return found


def removal_record(
    after: Mapping[str, Any], reason: str, *, from_asset_id: str, revision: int
) -> dict[str, Any] | None:
    """The removal as stored on the row's takeoff, or None for a clearing correction."""
    if after.get(REMOVED_BY_PROJECT) is None:
        return None
    return {
        "project": str(after[REMOVED_BY_PROJECT]),
        "disposition_date": str(after[DISPOSITION_DATE]),
        "share": format(Decimal(str(after.get(REMOVED_SHARE, 1))), "f"),
        "reason": reason.strip(),
        "from_asset_id": from_asset_id,
        "revision": revision,
    }


@dataclass(frozen=True, slots=True)
class Removal:
    project: str
    disposition_date: date
    share: Decimal
    reason: str


def removal_from_inputs(inputs: Any) -> Removal | None:
    if not isinstance(inputs, Mapping):
        return None
    found = inputs.get(REMOVAL_INPUT)
    if not isinstance(found, Mapping):
        return None
    return Removal(
        project=str(found["project"]),
        disposition_date=date.fromisoformat(str(found["disposition_date"])),
        share=Decimal(str(found.get("share", "1"))),
        reason=str(found.get("reason") or ""),
    )


def project_from_inputs(inputs: Any) -> str | None:
    if not isinstance(inputs, Mapping):
        return None
    found = inputs.get(PROJECT_INPUT)
    return found if isinstance(found, str) else None


# ------------------------------------------------------------------ which rows a project holds


@dataclass(frozen=True, slots=True)
class Membership:
    """Which project each component of a revision belongs to (None: the acquisition), and the
    rooms that mix photographs of the acquisition and of a project (their inventory rows stay
    in the acquisition, flagged)."""

    projects: dict[uuid.UUID, str]
    mixed_rooms: frozenset[uuid.UUID]


def membership(
    components: Sequence[tuple[uuid.UUID, uuid.UUID | None, uuid.UUID | None]],
    room_photos: Mapping[uuid.UUID, Sequence[uuid.UUID]],
    photo_projects: Mapping[str, str],
) -> Membership:
    """``components``: ``(component id, its photo or None, its room or None)``. A per-photo row
    follows its photo's tag; a room-level row (no photo: a reconciled inventory row or an
    engineer's added row) follows its room when every photo of the room is tagged after the
    same project; a room whose photos mix tags (or projects) keeps its room-level rows in the
    acquisition and is reported."""
    out: dict[uuid.UUID, str] = {}
    mixed: set[uuid.UUID] = set()
    room_project: dict[uuid.UUID, str | None] = {}
    for room, photos in room_photos.items():
        tags = {photo_projects.get(str(p)) for p in photos}
        if len(tags) == 1:
            room_project[room] = next(iter(tags))
        else:
            room_project[room] = None
            if any(t is not None for t in tags):
                mixed.add(room)
    for component_id, photo, room_id in components:
        key: str | None
        if photo is not None:
            key = photo_projects.get(str(photo))
        elif room_id is not None:
            key = room_project.get(room_id)
        else:
            key = None
        if key is not None:
            out[component_id] = key
    return Membership(out, frozenset(mixed))


__all__ = [
    "DISPOSITION_DATE",
    "KEY_PATTERN",
    "MAX_PROJECTS",
    "PROJECT_INPUT",
    "REMOVAL_FIELDS",
    "REMOVAL_INPUT",
    "REMOVED_BY_PROJECT",
    "REMOVED_SHARE",
    "VERSION_INPUT",
    "AerialBracket",
    "ComponentBracket",
    "DateProblem",
    "Invoice",
    "Kind",
    "Membership",
    "Removal",
    "RenovationProject",
    "StudyRenovations",
    "StudyRenovationsIn",
    "ViewRef",
    "date_problems",
    "has_removal",
    "membership",
    "project_from_inputs",
    "removal_from_inputs",
    "removal_problems",
    "removal_record",
    "split_removal",
]
