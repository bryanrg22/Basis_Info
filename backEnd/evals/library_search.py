"""``python -m evals.library_search``: which way should ``GET /v1/studies?q=`` search the
library? (EVAL_REPORT 92.)

The library search matched the property name only. Engineers look a study up by what they
remember: the street, the city, the ZIP, the client (taxpayer, borrower, owner of record),
the parcel number, who created it, who signed it. Those values live in four places: the
study row (name, the intake's ZIP and state, the report answers' taxpayer), the appraisal's
extracted subject fields (``extraction_fields``), the creator (``users``) and the signature
(``report_signatures.engineer``). This harness seeds a synthetic library (several firms,
synthetic names and addresses only, never a client file) into a disposable PostgreSQL built
by the real migrations, then measures each candidate on the same data:

``current``
    ``property_name ILIKE '%q%'`` (what main does).
``joined_ilike``
    Every word of ``q`` must match one of the fields above, ``ILIKE`` at query time
    (``EXISTS`` into the joined tables); no index, no migration.
``joined_trgm``
    The same predicate written so the planner can use pg_trgm GIN indexes (on the subject
    fields, a partial index; on the creator and the signature), plus those indexes.
``doc_seq``
    One search document per study (``study_search``, kept by triggers on the four sources),
    ``ILIKE`` per word, no index on it.
``doc_trgm``
    The same document with a pg_trgm GIN index.
``doc_trgm_fold``
    The same, lower-cased and accent-folded (``unaccent``) when it is written, so ``cafe``
    finds ``Café``.
``doc_fts``
    Full-text search over the same document (``to_tsvector('simple')``, GIN), each word a
    prefix query (``word:*``).

For each size (``--sizes``, default 2k, 20k and 200k studies over eight firms, the
searching firm a quarter of them), every candidate gets: correctness (every labelled query
and every sampled query compared with a Python oracle of the intended semantics, the
searching user's visible studies only, so firm isolation is part of every comparison),
latency (the count and the first page, as the repository runs them, p50/p95 over all
queries and passes), the plans of three representative queries (``EXPLAIN ANALYZE``), the
size of what it adds (indexes, the document table), how long that takes to build, and what
it costs a write (an extraction's subject fields, a user rename).

Nothing is paid; nothing leaves the container. The run files under
``verification/library_search/<harness version>/``.

Usage (from ``backEnd/``; Docker for the disposable PostgreSQL unless ``--database-url``)::

    BASIS_EVAL_DATA_DIR=~/basis-eval-data uv run python -m evals.library_search \\
        --out "$BASIS_EVAL_DATA_DIR/predictions/library_search_<date>"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import statistics
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import asyncpg

from basis.domain.intake import StudyIntake, StudyIntakeIn
from basis.extraction.field_mappings import FIELDS
from basis.report.inputs import StudyAnswers
from evals import record

VERSION: Final = "v1"
PG_IMAGE: Final = "pgvector/pgvector:pg16"  # the image the stack and CI use
# The schema the candidates are compared on: main before the search change, so a later
# migration never changes what a candidate measures. ``implemented`` then upgrades to head.
SCHEMA_REVISION: Final = "0030"
IMPLEMENTED: Final = "implemented"
BACKEND_ROOT: Final = Path(__file__).resolve().parents[1]
SIZES: Final = {"2k": 2_000, "20k": 20_000, "200k": 200_000}
FIRMS: Final = 8
SEARCHER_SHARE: Final = 0.25
PAGE: Final = 50
SAMPLED_QUERIES: Final = 120
TIMED_PASSES: Final = 3
# Per candidate and size, for the correctness pass and again for the timing passes: a
# candidate slower than this is cut short (the labelled queries run first) and says so.
CANDIDATE_BUDGET_S: Final = 300.0
STATEMENT_TIMEOUT: Final = "60s"
WRITE_PROBES: Final = 15

# The appraisal's subject fields searched: the address, the client (borrower, owner of
# record) and the parcel number. The lender is the appraisal's client, not the firm's, and
# names the same few banks across a library, so it is left out.
SUBJECT_FIELDS: Final = (
    "property_address",
    "city",
    "state",
    "zip",
    "county",
    "borrower",
    "owner_of_public_record",
    "assessors_parcel",
)
STATUSES: Final = (
    ("completed", "completed", 0.70),
    ("waiting_review", "assets_review", 0.08),
    ("processing", "components_processing", 0.05),
    ("draft", "uploading", 0.10),
    ("failed", "extracting", 0.02),
    ("archived", "completed", 0.05),
)

FIRST: Final = (
    "Priya Marcus Elena Tomás Grace Daniel Aisha Kenji Laura Victor Nadia Owen Sofia Rajesh "
    "Hannah Diego Mei Samuel Chloe Andre Ingrid Felix Yara Colin Beatriz Hugo Leah Dmitri "
    "Rosa Theo"
).split()
LAST: Final = (
    "Raman Okafor Lindqvist Brennan Castillo Nakamura Whitfield Duarte Kowalski Abernathy "
    "Fitzgerald Moreau Haddad Delgado Sorensen Pemberton Vasquez Hollis Albright Quintero "
    "Ashworth Bergstrom Calloway Dunmore Everly Fairbanks Garrity Halvorsen Iverson Jessup "
    "Kincaid Lockhart Merriweather Northcott Oyelaran Prescott Rutherford Stanhope Thorne Upton"
).split()
STREETS: Final = (
    "Harbor Maple Oak Cedar Willow Pine Lakeview Sunset Ridge Mission Juniper Sycamore "
    "Magnolia Aspen Cypress Hawthorne Birch Chestnut Elm Laurel Bayview Canyon Meadow Orchard "
    "Prospect Quarry Redwood Summit Valley Westbrook Fairview Highland Kingsley Linden Mulberry "
    "Northgate Parkside Riverside Shoreline Timber"
).split()
SUFFIXES: Final = ("St", "Ave", "Blvd", "Dr", "Ln", "Rd", "Ct", "Way", "Pl")
KINDS: Final = (
    "Apartments",
    "Court Duplex",
    "Plaza",
    "Townhomes",
    "Fourplex",
    "Commons",
    "Lofts",
    "Office Park",
    "Retail Center",
    "Cottages",
)
CITIES: Final = (
    ("Oxnard", "Ventura", "CA", "930"),
    ("Ventura", "Ventura", "CA", "930"),
    ("Fresno", "Fresno", "CA", "937"),
    ("Austin", "Travis", "TX", "787"),
    ("Round Rock", "Williamson", "TX", "786"),
    ("Denver", "Denver", "CO", "802"),
    ("Boulder", "Boulder", "CO", "803"),
    ("Phoenix", "Maricopa", "AZ", "850"),
    ("Tempe", "Maricopa", "AZ", "852"),
    ("Nashville", "Davidson", "TN", "372"),
    ("Charlotte", "Mecklenburg", "NC", "282"),
    ("Raleigh", "Wake", "NC", "276"),
    ("Tampa", "Hillsborough", "FL", "336"),
    ("Orlando", "Orange", "FL", "328"),
    ("Columbus", "Franklin", "OH", "432"),
    ("Albany", "Albany", "NY", "122"),
    ("Buffalo", "Erie", "NY", "142"),
    ("Spokane", "Spokane", "WA", "992"),
    ("Boise", "Ada", "ID", "837"),
    ("Savannah", "Chatham", "GA", "314"),
)
TAXPAYER_FORMS: Final = (
    "{last} Holdings LLC",
    "{last} Family Trust",
    "{first} {last}",
    "{street} Partners LP",
)
FILLER_WORDS: Final = (
    "Average Good Gable Hip Asphalt Shingle Vinyl Brick Stucco Concrete Slab Crawl Space "
    "Forced Air Central None Public Typical Suburban Stable Fee Simple Owner Tenant"
).split()

# The intake every seeded study carries, varied by state and ZIP (a valid StudyIntake, so
# the repository reads the rows back as production does).
_INTAKE_EXAMPLE: Final[dict[str, Any]] = dict(
    StudyIntakeIn.model_config.get("json_schema_extra", {})["examples"][0]  # type: ignore[arg-type,index]
)


# --------------------------------------------------------------------------- the library


@dataclass
class Firm:
    id: uuid.UUID
    name: str
    domain: str
    users: list[User] = field(default_factory=list)


@dataclass
class User:
    id: uuid.UUID
    firm: int
    display_name: str | None
    email: str | None


@dataclass
class StudyRow:
    id: uuid.UUID
    firm: int
    created_by: uuid.UUID
    property_name: str
    status: str
    stage: str
    created_at: datetime
    updated_at: datetime
    intake_state: str | None = None
    intake_zip: str | None = None
    taxpayer: str | None = None
    subject: dict[str, str] = field(default_factory=dict)
    signer_name: str | None = None
    signer_email: str | None = None

    def searchable(self, users: dict[uuid.UUID, User]) -> list[str]:
        """Every value the intended search matches (the oracle's view)."""
        creator = users[self.created_by]
        values = [
            self.property_name,
            self.intake_zip,
            self.intake_state,
            self.taxpayer,
            creator.display_name,
            creator.email,
            *self.subject.values(),
            self.signer_name,
            self.signer_email,
        ]
        return [v for v in values if v]


@dataclass
class Library:
    firms: list[Firm]
    users: dict[uuid.UUID, User]
    studies: list[StudyRow]
    searcher: User
    needles: dict[str, uuid.UUID]


def _pick_status(rng: random.Random) -> tuple[str, str]:
    roll = rng.random()
    acc = 0.0
    for status, stage, share in STATUSES:
        acc += share
        if roll < acc:
            return status, stage
    return STATUSES[0][0], STATUSES[0][1]


def _street(rng: random.Random) -> str:
    return f"{rng.randint(10, 9899)} {rng.choice(STREETS)} {rng.choice(SUFFIXES)}"


def _apn(rng: random.Random) -> str:
    return f"{rng.randint(100, 999)}-{rng.randint(100, 999)}-{rng.randint(10, 99)}"


def _person(rng: random.Random) -> tuple[str, str]:
    return rng.choice(FIRST), rng.choice(LAST)


def _study(rng: random.Random, firm: Firm, firm_index: int, now: datetime) -> StudyRow:
    status, stage = _pick_status(rng)
    creator = rng.choice(firm.users)
    created = now - timedelta(days=rng.uniform(0, 3650))
    updated = created + timedelta(days=rng.uniform(0, 60))
    city, county, state, zip3 = rng.choice(CITIES)
    zip_code = f"{zip3}{rng.randint(0, 99):02d}"
    address = _street(rng)
    style = rng.random()
    if style < 0.4:
        name = f"{address}, {city} {state}"
    elif style < 0.8:
        place = rng.choice(("View", "Ridge", "Park", "Point", "Grove"))
        name = f"{rng.choice(STREETS)} {place} {rng.choice(KINDS)}"
    else:
        name = f"{rng.choice(LAST)} {rng.choice(KINDS)}"
    study = StudyRow(
        id=uuid.UUID(int=rng.getrandbits(128), version=4),
        firm=firm_index,
        created_by=creator.id,
        property_name=name,
        status=status,
        stage=stage,
        created_at=created,
        updated_at=min(updated, now),
    )
    if status != "draft" or rng.random() < 0.5:
        study.intake_state, study.intake_zip = state, zip_code
    if status != "draft":
        first, last = _person(rng)
        owner_first, owner_last = _person(rng)
        study.subject = {
            "property_address": address,
            "city": city,
            "state": state,
            "zip": zip_code,
            "county": county,
            "borrower": f"{first} {last}",
            "owner_of_public_record": rng.choice(
                (f"{owner_first} {owner_last}", f"{owner_last} Holdings LLC")
            ),
            "assessors_parcel": _apn(rng),
        }
    if status in ("completed", "archived"):
        first, last = _person(rng)
        study.taxpayer = rng.choice(TAXPAYER_FORMS).format(
            first=first, last=last, street=rng.choice(STREETS)
        )
        signer = rng.choice(firm.users)
        study.signer_name = signer.display_name or "Unnamed Engineer"
        study.signer_email = signer.email
    return study


def build_library(total: int, seed: int = 92, extra_users: int = 0) -> Library:
    """A deterministic synthetic library of ``total`` studies over :data:`FIRMS` firms; the
    searching firm (index 0) holds :data:`SEARCHER_SHARE` of them, plus the planted needles
    the labelled queries name (some planted in firm 1 too, for isolation)."""
    rng = random.Random(seed)
    now = datetime(2026, 11, 1, tzinfo=UTC)
    firms: list[Firm] = []
    users: dict[uuid.UUID, User] = {}
    for f in range(FIRMS):
        firm = Firm(
            id=uuid.UUID(int=rng.getrandbits(128), version=4),
            name=f"Firm {f}",
            domain=f"firm{f}.example",
        )
        for _ in range(12 if f == 0 else rng.randint(3, 10)):
            first, last = _person(rng)
            named = rng.random() > 0.1  # a few accounts have no display name
            user = User(
                id=uuid.UUID(int=rng.getrandbits(128), version=4),
                firm=f,
                display_name=f"{first} {last}" if named else None,
                email=f"{_ascii(first).lower()}.{_ascii(last).lower()}@{firm.domain}",
            )
            firm.users.append(user)
            users[user.id] = user
        firms.append(firm)
    searcher = firms[0].users[0]
    studies: list[StudyRow] = []
    own = int(total * SEARCHER_SHARE)
    for i in range(total):
        f = 0 if i < own else 1 + (i - own) % (FIRMS - 1)
        studies.append(_study(rng, firms[f], f, now))
    needles = _plant(rng, firms, users, studies, now)
    rng.shuffle(studies)
    # Members who created nothing (the platform's other users), from a generator of their
    # own so the studies are the same with or without them: the creator clause scans users.
    extra = random.Random(seed + 1)
    for n in range(extra_users):
        first, last = _person(extra)
        f = 1 + n % (FIRMS - 1)
        user = User(
            id=uuid.UUID(int=extra.getrandbits(128), version=4),
            firm=f,
            display_name=f"{first} {last}",
            email=f"{_ascii(first).lower()}.{_ascii(last).lower()}.{n}@{firms[f].domain}",
        )
        firms[f].users.append(user)
        users[user.id] = user
    return Library(firms=firms, users=users, studies=studies, searcher=searcher, needles=needles)


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _plant(
    rng: random.Random,
    firms: list[Firm],
    users: dict[uuid.UUID, User],
    studies: list[StudyRow],
    now: datetime,
) -> dict[str, uuid.UUID]:
    """The studies the labelled queries are about, with values found nowhere else."""
    # Two named engineers in the searching firm: one creates, one signs.
    creator = User(
        uuid.UUID(int=rng.getrandbits(128), version=4),
        0,
        "Wilhelmina Strand",
        "w.strand@firm0.example",
    )
    signer = User(
        uuid.UUID(int=rng.getrandbits(128), version=4), 0, "Bartholomew Quayle", "bq@firm0.example"
    )
    nameless = User(
        uuid.UUID(int=rng.getrandbits(128), version=4), 0, None, "intake.desk@firm0.example"
    )
    other = User(
        uuid.UUID(int=rng.getrandbits(128), version=4), 1, "Wilhelmina Strand", "ws@firm1.example"
    )
    for user in (creator, signer, nameless, other):
        firms[user.firm].users.append(user)
        users[user.id] = user
    base = dict(
        status="completed",
        stage="completed",
        created_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=2),
    )

    def new(firm: int, by: User, name: str, **kw: Any) -> StudyRow:
        row = StudyRow(
            id=uuid.UUID(int=rng.getrandbits(128), version=4),
            firm=firm,
            created_by=by.id,
            property_name=name,
            **{**base, **kw},
        )
        studies.append(row)
        return row

    harbor = new(0, creator, "Harbor Point Lofts")
    harbor.intake_state, harbor.intake_zip = "CA", "93035"
    harbor.subject = {
        "property_address": "4417 Quillon Ave",
        "city": "Oxnard",
        "state": "CA",
        "zip": "93035",
        "county": "Ventura",
        "borrower": "Ines Varga",
        "owner_of_public_record": "Okonkwo Holdings LLC",
        "assessors_parcel": "205-118-34",
    }
    harbor.taxpayer = "Okonkwo Holdings LLC"
    harbor.signer_name, harbor.signer_email = signer.display_name, signer.email
    archived = new(0, creator, "Zephyrine Gardens", status="archived")
    percent = new(0, creator, "100% Occupied Plaza", status="waiting_review", stage="assets_review")
    underscore = new(0, creator, "unit_7 warehouse", status="draft", stage="uploading")
    new(0, creator, "unit 7 warehouse", status="draft", stage="uploading")
    cafe = new(0, creator, "Café Lofts")
    cafe.subject = {"owner_of_public_record": "José Núñez", "city": "Tampa", "state": "FL"}
    intake_only = new(0, nameless, "Draft without an appraisal", status="draft", stage="uploading")
    intake_only.intake_state, intake_only.intake_zip = "MT", "59901"
    filler = new(0, creator, "Asphalt Shingle Test")  # a name that is also a filler value
    elsewhere = new(1, other, "Brightwater Mews")
    elsewhere.taxpayer = "Thistlewood Capital LLC"
    shared = new(1, other, "Okonkwo Family Duplex")  # the same client in another firm
    shared.taxpayer = "Okonkwo Holdings LLC"
    return {
        "harbor": harbor.id,
        "archived": archived.id,
        "percent": percent.id,
        "underscore": underscore.id,
        "cafe": cafe.id,
        "intake_only": intake_only.id,
        "filler_name": filler.id,
        "elsewhere": elsewhere.id,
        "shared_elsewhere": shared.id,
    }


# --------------------------------------------------------------------------- semantics


def fold(text: str, accents: bool) -> str:
    """Case folding as ``lower()``/``ILIKE`` does on this data; with ``accents``, the
    combining marks removed as ``unaccent`` does for Latin letters."""
    out = text.lower()
    return _ascii_keep(out) if accents else out


def _ascii_keep(text: str) -> str:
    """Strip combining marks, keep every other character (``%``, ``_``, digits)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def words(q: str) -> list[str]:
    return q.split()


def oracle(
    library: Library, q: str, *, accents: bool, include_archived: bool = False
) -> set[uuid.UUID]:
    """The intended answer: the searcher's firm's studies (archived hidden) where every
    word of ``q`` is a substring of one searchable value, case ignored (and accents, when
    ``accents``). Blank ``q`` matches every visible study."""
    terms = [fold(w, accents) for w in words(q)]
    out: set[uuid.UUID] = set()
    for study in library.studies:
        if study.firm != library.searcher.firm:
            continue
        if study.status == "archived" and not include_archived:
            continue
        values = [fold(v, accents) for v in study.searchable(library.users)]
        if all(any(t in v for v in values) for t in terms):
            out.add(study.id)
    return out


@dataclass(frozen=True)
class Labelled:
    id: str
    category: str
    q: str
    must: tuple[str, ...] = ()  # needle names that must be returned
    must_not: tuple[str, ...] = ()  # needle names that must not


LABELLED: Final[tuple[Labelled, ...]] = (
    Labelled("name_mid_word", "property name (backwards compatible)", "arbor point", ("harbor",)),
    Labelled(
        "name_case", "property name (backwards compatible)", "HARBOR POINT LOFTS", ("harbor",)
    ),
    Labelled("address_street", "address", "quillon", ("harbor",)),
    Labelled("address_number_street", "address", "4417 quillon ave", ("harbor",)),
    Labelled("name_and_city", "several fields", "harbor oxnard", ("harbor",)),
    Labelled("zip_exact", "ZIP", "93035", ("harbor",)),
    Labelled("zip_prefix", "ZIP", "9303", ("harbor",)),
    Labelled("intake_zip_only", "intake", "59901", ("intake_only",)),
    Labelled("parcel_fragment", "parcel number", "118-34", ("harbor",)),
    Labelled("taxpayer", "client", "okonkwo", ("harbor",), ("shared_elsewhere",)),
    Labelled("borrower", "client", "ines varga", ("harbor",)),
    Labelled("creator_name", "engineer", "wilhelmina", ("harbor",)),
    Labelled("creator_email", "engineer", "w.strand@", ("harbor",)),
    Labelled("creator_without_name", "engineer", "intake.desk", ("intake_only",)),
    Labelled("signer_name", "engineer", "quayle", ("harbor",)),
    Labelled("accent_query_plain", "accents", "cafe", ("cafe",)),
    Labelled("accent_owner_plain", "accents", "jose nunez", ("cafe",)),
    Labelled("accent_query_accented", "accents", "Café", ("cafe",)),
    Labelled("wildcard_percent", "literal wildcards", "100%", ("percent",)),
    Labelled("wildcard_underscore", "literal wildcards", "unit_7", ("underscore",)),
    Labelled("isolation_name", "firm isolation", "brightwater", (), ("elsewhere",)),
    Labelled("isolation_client", "firm isolation", "thistlewood", (), ("elsewhere",)),
    Labelled("archived_hidden", "archived", "zephyrine", (), ("archived",)),
    Labelled("filler_not_searched", "fields not searched", "asphalt", ("filler_name",)),
    Labelled("no_match", "no match", "qqzzx"),
    Labelled("one_char", "short", "q"),
    Labelled("two_chars", "short", "z9"),
)


def sampled_queries(library: Library, n: int, seed: int = 7) -> list[str]:
    """Queries a person would type, drawn from the searching firm's own values."""
    rng = random.Random(seed)
    own = [s for s in library.studies if s.firm == library.searcher.firm and s.subject]
    kinds: list[Callable[[], str]] = [
        lambda: rng.choice(own).subject["property_address"].split()[1],  # a street name
        lambda: rng.choice(own).subject["property_address"],  # a whole address
        lambda: rng.choice(own).subject["city"],
        lambda: rng.choice(own).subject["zip"],
        lambda: rng.choice(own).subject["zip"][:3],
        lambda: rng.choice(own).subject["assessors_parcel"][-6:],
        lambda: rng.choice(own).subject["borrower"].split()[1],
        lambda: rng.choice(own).property_name.split()[0],
        lambda: rng.choice(own).property_name[1:6],  # a mid-word fragment
        lambda: (
            rng.choice(own).subject["property_address"].split()[1]
            + " "
            + rng.choice(own).subject["city"]
        ),
        lambda: rng.choice(FIRST)[:2],  # two letters
        lambda: rng.choice("abcdefghijklmnopqrstuvwxyz"),  # one letter
        lambda: "".join(rng.choice("qxzjv") for _ in range(5)),  # nothing
    ]
    return [rng.choice(kinds)() for _ in range(n)]


# --------------------------------------------------------------------------- candidates


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def subject_sql(prefix: str = "") -> str:
    """``section = 'subject' AND field IN (...)`` over the row ``prefix`` names."""
    names = ", ".join(f"'{f}'" for f in SUBJECT_FIELDS)
    return f"{prefix}section = 'subject' AND {prefix}field IN ({names})"


def document_sql(creator: bool) -> str:
    """Every searched value of one study (``sid``), one per line, so a word never spans two;
    with ``creator``, the creator's name and email too."""
    who = "u.display_name, u.email," if creator else ""
    return f"""
SELECT concat_ws(E'\\n',
    s.property_name, s.intake->>'zip', s.intake->>'state', s.report_answers->>'taxpayer_name',
    {who}
    (SELECT string_agg(f.value_text, E'\\n' ORDER BY e.revision, f.field)
       FROM extractions e JOIN extraction_fields f ON f.extraction_id = e.id
       WHERE e.study_id = s.id AND {subject_sql("f.")}),
    (SELECT string_agg(concat_ws(E'\\n', r.engineer->>'name', r.engineer->>'email'), E'\\n')
       FROM report_signatures r WHERE r.study_id = s.id))
FROM studies s LEFT JOIN users u ON u.id = s.created_by WHERE s.id = sid
"""


def _doc_setup(folded: bool, index: str | None, creator: bool = True) -> list[str]:
    """The ``study_search`` table, its triggers and its backfill (the denormalized document),
    optionally lower-cased and accent-folded, with the index ``index`` names; without
    ``creator`` the creator is matched at query time and ``users`` has no trigger."""
    normal = "lower(public.unaccent('public.unaccent', t.d))" if folded else "t.d"
    statements = [
        "CREATE TABLE study_search (study_id uuid PRIMARY KEY REFERENCES studies(id) "
        "ON DELETE CASCADE, document text NOT NULL)",
        "CREATE FUNCTION study_search_document(sid uuid) RETURNS text LANGUAGE sql STABLE AS "
        f"$$ SELECT {normal} FROM ({document_sql(creator)}) AS t(d) $$",
        """CREATE FUNCTION study_search_refresh(sid uuid) RETURNS void LANGUAGE sql AS $$
            INSERT INTO study_search (study_id, document)
            SELECT s.id, study_search_document(s.id) FROM studies s WHERE s.id = sid
            ON CONFLICT (study_id) DO UPDATE SET document = EXCLUDED.document
            WHERE study_search.document IS DISTINCT FROM EXCLUDED.document $$""",
        """CREATE FUNCTION study_search_from_study() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN PERFORM study_search_refresh(NEW.id); RETURN NULL; END $$""",
        """CREATE FUNCTION study_search_from_child() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF TG_OP IN ('UPDATE', 'DELETE') THEN PERFORM study_search_refresh(OLD.study_id);
              END IF;
              IF TG_OP IN ('INSERT', 'UPDATE') THEN PERFORM study_search_refresh(NEW.study_id);
              END IF;
              RETURN NULL;
            END $$""",
        """CREATE FUNCTION study_search_from_user() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              PERFORM study_search_refresh(s.id) FROM studies s WHERE s.created_by = NEW.id;
              RETURN NULL;
            END $$""",
        "CREATE TRIGGER study_search_study AFTER INSERT OR UPDATE OF property_name, intake, "
        "report_answers, created_by ON studies FOR EACH ROW "
        "EXECUTE FUNCTION study_search_from_study()",
        "CREATE TRIGGER study_search_field_insert AFTER INSERT ON extraction_fields FOR EACH ROW "
        f"WHEN ({subject_sql('NEW.')}) EXECUTE FUNCTION study_search_from_child()",
        "CREATE TRIGGER study_search_field_update AFTER UPDATE ON extraction_fields FOR EACH ROW "
        f"WHEN (({subject_sql('NEW.')}) OR ({subject_sql('OLD.')})) "
        "EXECUTE FUNCTION study_search_from_child()",
        "CREATE TRIGGER study_search_field_delete AFTER DELETE ON extraction_fields FOR EACH ROW "
        f"WHEN ({subject_sql('OLD.')}) EXECUTE FUNCTION study_search_from_child()",
        "CREATE TRIGGER study_search_signature AFTER INSERT ON report_signatures "
        "FOR EACH ROW EXECUTE FUNCTION study_search_from_child()",
    ]
    if creator:
        statements.append(
            "CREATE TRIGGER study_search_user AFTER UPDATE OF display_name, email ON users "
            "FOR EACH ROW WHEN (OLD.display_name IS DISTINCT FROM NEW.display_name "
            "OR OLD.email IS DISTINCT FROM NEW.email) EXECUTE FUNCTION study_search_from_user()"
        )
    statements.append(
        "INSERT INTO study_search (study_id, document) "
        "SELECT id, study_search_document(id) FROM studies"
    )
    if index == "trgm":
        statements.append(
            "CREATE INDEX ix_study_search_trgm ON study_search USING gin (document gin_trgm_ops)"
        )
    elif index == "fts":
        statements.append(
            "CREATE INDEX ix_study_search_fts ON study_search "
            "USING gin (to_tsvector('simple', document))"
        )
    statements.append("ANALYZE study_search")
    return statements


_DOC_TEARDOWN: Final = (
    "DROP TRIGGER IF EXISTS study_search_study ON studies",
    "DROP TRIGGER IF EXISTS study_search_field_insert ON extraction_fields",
    "DROP TRIGGER IF EXISTS study_search_field_update ON extraction_fields",
    "DROP TRIGGER IF EXISTS study_search_field_delete ON extraction_fields",
    "DROP TRIGGER IF EXISTS study_search_signature ON report_signatures",
    "DROP TRIGGER IF EXISTS study_search_user ON users",
    "DROP TABLE IF EXISTS study_search",
    "DROP FUNCTION IF EXISTS study_search_from_study()",
    "DROP FUNCTION IF EXISTS study_search_from_child()",
    "DROP FUNCTION IF EXISTS study_search_from_user()",
    "DROP FUNCTION IF EXISTS study_search_refresh(uuid)",
    "DROP FUNCTION IF EXISTS study_search_document(uuid)",
)


@dataclass(frozen=True)
class Candidate:
    name: str
    description: str
    setup: tuple[str, ...]
    teardown: tuple[str, ...]
    created: tuple[str, ...]  # relations whose size is what the candidate adds
    predicate: Callable[[str, list[Any]], str]  # (q, params) -> SQL; appends to params
    accents: bool = False  # whether the intended semantics fold accents for it


def _param(params: list[Any], value: Any) -> str:
    params.append(value)
    return f"${len(params)}"


def _current(q: str, params: list[Any]) -> str:
    p = _param(params, f"%{escape_like(q.strip())}%")
    return f"s.property_name ILIKE {p} ESCAPE '\\'"


def _joined(correlated: bool) -> Callable[[str, list[Any]], str]:
    def predicate(q: str, params: list[Any]) -> str:
        clauses = []
        for word in words(q):
            p = _param(params, f"%{escape_like(word)}%")
            own = " OR ".join(
                f"{expr} ILIKE {p} ESCAPE '\\'"
                for expr in (
                    "s.property_name",
                    "(s.intake->>'zip')",
                    "(s.intake->>'state')",
                    "(s.report_answers->>'taxpayer_name')",
                )
            )
            if correlated:
                joined = (
                    # Through extractions: (study_id, id) and (extraction_id, section,
                    # field) are indexed; extraction_fields.study_id alone is not.
                    "EXISTS (SELECT 1 FROM extractions e JOIN extraction_fields f "
                    f"ON f.extraction_id = e.id WHERE e.study_id = s.id AND "
                    f"{subject_sql('f.')} AND f.value_text ILIKE {p} ESCAPE '\\') "
                    f"OR EXISTS (SELECT 1 FROM users u WHERE u.id = s.created_by AND "
                    f"(u.display_name ILIKE {p} ESCAPE '\\' OR u.email ILIKE {p} ESCAPE '\\')) "
                    f"OR EXISTS (SELECT 1 FROM report_signatures r WHERE r.study_id = s.id AND "
                    f"((r.engineer->>'name') ILIKE {p} ESCAPE '\\' "
                    f"OR (r.engineer->>'email') ILIKE {p} ESCAPE '\\'))"
                )
            else:
                joined = (
                    f"s.id IN (SELECT f.study_id FROM extraction_fields f WHERE "
                    f"{subject_sql('f.')} AND f.value_text ILIKE {p} ESCAPE '\\') "
                    f"OR s.created_by IN (SELECT u.id FROM users u WHERE "
                    f"u.display_name ILIKE {p} ESCAPE '\\' OR u.email ILIKE {p} ESCAPE '\\') "
                    f"OR s.id IN (SELECT r.study_id FROM report_signatures r WHERE "
                    f"(r.engineer->>'name') ILIKE {p} ESCAPE '\\' "
                    f"OR (r.engineer->>'email') ILIKE {p} ESCAPE '\\')"
                )
            clauses.append(f"({own} OR {joined})")
        return " AND ".join(clauses) or "TRUE"

    return predicate


def _doc(folded: bool, live_creator: bool = False) -> Callable[[str, list[Any]], str]:
    def predicate(q: str, params: list[Any]) -> str:
        clauses = []
        if live_creator:
            # One clause per word: in the document, or in the creator's name or email
            # (users is a small table: every firm's members), both folded the same way.
            for word in words(q):
                p = _param(params, escape_like(word))
                pattern = f"('%' || lower(public.unaccent('public.unaccent', {p}::text)) || '%')"
                clauses.append(
                    "(EXISTS (SELECT 1 FROM study_search d WHERE d.study_id = s.id AND "
                    f"d.document LIKE {pattern} ESCAPE '\\') "
                    "OR s.created_by IN (SELECT u.id FROM users u WHERE "
                    "lower(public.unaccent('public.unaccent', "
                    f"concat_ws(E'\\n', u.display_name, u.email))) LIKE {pattern} ESCAPE '\\'))"
                )
            return " AND ".join(clauses) or "TRUE"
        for word in words(q):
            if folded:
                p = _param(params, escape_like(word))
                clauses.append(
                    "d.document LIKE ('%' || lower(public.unaccent('public.unaccent', "
                    f"{p}::text)) || '%') ESCAPE '\\'"
                )
            else:
                p = _param(params, f"%{escape_like(word)}%")
                clauses.append(f"d.document ILIKE {p} ESCAPE '\\'")
        body = " AND ".join(clauses) or "TRUE"
        return (
            f"EXISTS (SELECT 1 FROM study_search d WHERE d.study_id = s.id AND {body})"
            if clauses
            else "TRUE"
        )

    return predicate


def tsquery(q: str) -> str:
    """Each word's alphanumeric parts as prefix terms, all required."""
    parts = [p for w in words(q) for p in re.findall(r"[^\W_]+", w.lower())]
    return " & ".join(f"'{p}':*" for p in parts)


def _fts(q: str, params: list[Any]) -> str:
    text = tsquery(q)
    if not text:
        return "TRUE" if not words(q) else "FALSE"
    p = _param(params, text)
    return (
        "EXISTS (SELECT 1 FROM study_search d WHERE d.study_id = s.id AND "
        f"to_tsvector('simple', d.document) @@ to_tsquery('simple', {p}))"
    )


_TRGM_INDEXES: Final = (
    "CREATE INDEX ix_ef_subject_trgm ON extraction_fields USING gin (value_text gin_trgm_ops) "
    f"WHERE {subject_sql()}",
    "CREATE INDEX ix_users_name_trgm ON users USING gin "
    "(display_name gin_trgm_ops, email gin_trgm_ops)",
    "CREATE INDEX ix_sig_engineer_trgm ON report_signatures USING gin "
    "((engineer->>'name') gin_trgm_ops, (engineer->>'email') gin_trgm_ops)",
    "ANALYZE extraction_fields",
)

CANDIDATES: Final[tuple[Candidate, ...]] = (
    Candidate("current", "property_name ILIKE '%q%' (main)", (), (), (), _current),
    Candidate(
        "joined_ilike",
        "every word ILIKE across the joined fields (EXISTS), no index",
        (),
        (),
        (),
        _joined(True),
    ),
    Candidate(
        "joined_trgm",
        "every word ILIKE across the joined fields (IN subqueries) + pg_trgm GIN indexes on them",
        _TRGM_INDEXES,
        (
            "DROP INDEX IF EXISTS ix_ef_subject_trgm",
            "DROP INDEX IF EXISTS ix_users_name_trgm",
            "DROP INDEX IF EXISTS ix_sig_engineer_trgm",
        ),
        ("ix_ef_subject_trgm", "ix_users_name_trgm", "ix_sig_engineer_trgm"),
        _joined(False),
    ),
    Candidate(
        "doc_seq",
        "study_search document (triggers), ILIKE per word, no index",
        tuple(_doc_setup(False, None)),
        _DOC_TEARDOWN,
        ("study_search",),
        _doc(False),
    ),
    Candidate(
        "doc_trgm",
        "study_search document + pg_trgm GIN",
        tuple(_doc_setup(False, "trgm")),
        _DOC_TEARDOWN,
        ("study_search",),
        _doc(False),
    ),
    Candidate(
        "doc_trgm_fold",
        "study_search document lower-cased and unaccented + pg_trgm GIN",
        tuple(_doc_setup(True, "trgm")),
        _DOC_TEARDOWN,
        ("study_search",),
        _doc(True),
        accents=True,
    ),
    Candidate(
        "doc_trgm_fold_live_creator",
        "doc_trgm_fold without the creator in the document: the creator matched on users at "
        "query time, so a rename rewrites nothing and users carries no trigger",
        tuple(_doc_setup(True, "trgm", creator=False)),
        _DOC_TEARDOWN,
        ("study_search",),
        _doc(True, live_creator=True),
        accents=True,
    ),
    Candidate(
        "doc_fts",
        "study_search document, full-text ('simple', word:* prefixes) + GIN",
        tuple(_doc_setup(False, "fts")),
        _DOC_TEARDOWN,
        ("study_search",),
        _fts,
    ),
)


def listing_sql(
    candidate: Candidate, q: str, user_id: uuid.UUID
) -> tuple[str, str, str, list[Any]]:
    """The count, the first page (``updated_desc``, as the repository runs them) and every
    matching id, for the searcher, archived hidden."""
    params: list[Any] = [user_id]
    where = candidate.predicate(q, params) if q.strip() else "TRUE"
    base = (
        "FROM studies s JOIN org_members m ON m.org_id = s.org_id AND m.user_id = $1 "
        f"WHERE s.status <> 'archived' AND {where}"
    )
    return (
        f"SELECT count(*) {base}",
        f"SELECT s.* {base} ORDER BY s.updated_at DESC, s.id DESC LIMIT {PAGE + 1}",
        f"SELECT s.id {base}",
        params,
    )


# --------------------------------------------------------------------------- the database


def _alembic(dsn: str) -> Any:
    from alembic.config import Config
    from sqlalchemy.engine import make_url

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = (
        make_url(dsn).set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    )
    config.attributes["configure_logger"] = False
    return config


async def create_schema(admin_dsn: str, name: str) -> str:
    """A fresh database ``name`` at :data:`SCHEMA_REVISION`; its DSN."""
    from alembic import command
    from sqlalchemy.engine import make_url

    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    dsn = make_url(admin_dsn).set(database=name).render_as_string(hide_password=False)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    finally:
        await conn.close()
    await asyncio.to_thread(command.upgrade, _alembic(dsn), SCHEMA_REVISION)
    return dsn


def _intake(state: str, zip_code: str) -> str:
    intake = StudyIntake.model_validate({**_INTAKE_EXAMPLE, "state": state, "zip": zip_code})
    return json.dumps(intake.model_dump(mode="json"))


def _answers(taxpayer: str) -> str:
    answers = StudyAnswers(
        taxpayer_name=taxpayer,
        photographs="engineer_site_visit",
        photographed_by="Site Engineer",
        photographed_on=date(2026, 1, 15),
    )
    return json.dumps(answers.model_dump(mode="json"))


def _chunks(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def seed(dsn: str, library: Library, filler_fields: int) -> dict[str, Any]:
    """COPY the library in; the filler extraction fields are generated server-side.

    Loaded as a restore loads (``pg_restore`` adds keys after the data): the foreign-key
    triggers off (``session_replication_role = replica``) for rows the generator made
    consistent, and the two indexes of ``extraction_fields`` dropped and rebuilt with the same
    names and definitions after the rows are in; inserting 24 million random-UUID keys one
    by one into indexes larger than the server's cache took over 30 minutes and was not done.
    Nothing is measured during the load; the schema is identical for everything after it."""
    t0 = time.monotonic()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SET session_replication_role = replica")
        await conn.execute(
            "ALTER TABLE extraction_fields DROP CONSTRAINT pk_extraction_fields, "
            "DROP CONSTRAINT uq_extraction_fields_extraction_id_section_field"
        )
        await conn.copy_records_to_table(
            "orgs", records=[(f.id, f.name) for f in library.firms], columns=["id", "name"]
        )
        await conn.copy_records_to_table(
            "users",
            records=[
                (u.id, f"uid-{u.id}", u.email, u.display_name) for u in library.users.values()
            ],
            columns=["id", "firebase_uid", "email", "display_name"],
        )
        await conn.copy_records_to_table(
            "org_members",
            records=[(library.firms[u.firm].id, u.id, "engineer") for u in library.users.values()],
            columns=["org_id", "user_id", "role"],
        )
        for chunk in _chunks(library.studies, 5_000):
            studies, docs, extractions, fields, sigs = [], [], [], [], []
            for s in chunk:
                studies.append(
                    (
                        s.id,
                        library.firms[s.firm].id,
                        s.created_by,
                        s.property_name,
                        s.status,
                        s.stage,
                        _intake(s.intake_state, s.intake_zip)
                        if s.intake_zip and s.intake_state
                        else None,
                        1 if s.intake_zip else 0,
                        _answers(s.taxpayer) if s.taxpayer else None,
                        1 if s.taxpayer else 0,
                        s.created_at,
                        s.updated_at,
                    )
                )
                if s.subject:
                    doc_id, ext_id = uuid.uuid4(), uuid.uuid4()
                    docs.append(
                        (
                            doc_id,
                            s.id,
                            "appraisal",
                            "appraisal.pdf",
                            "application/pdf",
                            1_000_000,
                            uuid.uuid4().hex + uuid.uuid4().hex,
                            f"gs://seed/{doc_id}",
                        )
                    )
                    extractions.append((ext_id, s.id, doc_id, 1, "pipeline", "{}"))
                    for name, value in s.subject.items():
                        fields.append(
                            (uuid.uuid4(), ext_id, s.id, "subject", name, value, 0.9, "seed")
                        )
                if s.signer_name:
                    sigs.append(
                        (
                            uuid.uuid4(),
                            s.id,
                            1,
                            s.created_by,
                            s.updated_at,
                            json.dumps(
                                {
                                    "name": s.signer_name,
                                    "credentials": "PE",
                                    "email": s.signer_email,
                                }
                            ),
                            1,
                            "0" * 64,
                            "0" * 64,
                            f"gs://seed/{s.id}.pdf",
                            "0" * 64,
                        )
                    )
            await conn.copy_records_to_table(
                "studies",
                records=studies,
                columns=[
                    "id",
                    "org_id",
                    "created_by",
                    "property_name",
                    "status",
                    "current_stage",
                    "intake",
                    "intake_version",
                    "report_answers",
                    "report_answers_version",
                    "created_at",
                    "updated_at",
                ],
            )
            await conn.copy_records_to_table(
                "documents",
                records=docs,
                columns=[
                    "id",
                    "study_id",
                    "kind",
                    "filename",
                    "content_type",
                    "bytes",
                    "sha256",
                    "gcs_uri",
                ],
            )
            await conn.copy_records_to_table(
                "extractions",
                records=extractions,
                columns=["id", "study_id", "document_id", "revision", "method", "raw"],
            )
            await conn.copy_records_to_table(
                "extraction_fields",
                records=fields,
                columns=[
                    "id",
                    "extraction_id",
                    "study_id",
                    "section",
                    "field",
                    "value_text",
                    "confidence",
                    "source",
                ],
            )
            await conn.copy_records_to_table(
                "report_signatures",
                records=sigs,
                columns=[
                    "id",
                    "study_id",
                    "revision",
                    "signed_by",
                    "signed_at",
                    "engineer",
                    "statement_version",
                    "export_sha256",
                    "reviewed_sha256",
                    "pdf_uri",
                    "pdf_sha256",
                ],
            )
        # The rest of a real extraction: the catalogue's other keys, filler values.
        others = [
            spec.key.split(".", 1)
            for spec in FIELDS
            if not (spec.key.startswith("subject.") and spec.key.split(".", 1)[1] in SUBJECT_FIELDS)
        ]
        others = others[:filler_fields]
        await conn.execute(
            """INSERT INTO extraction_fields
                 (extraction_id, study_id, section, field, value_text, confidence, source)
               SELECT e.id, e.study_id, k.section, k.field,
                      CASE WHEN (hashtext(e.id::text || k.field) & 1) = 0
                           THEN ($3::text[])[1 + (abs(hashtext(k.field || e.id::text))
                                                  % array_length($3::text[], 1))]
                           ELSE (1900 + abs(hashtext(e.id::text || k.section)) % 125)::text END,
                      0.9, 'seed'
               FROM extractions e CROSS JOIN unnest($1::text[], $2::text[]) AS k(section, field)""",
            [o[0] for o in others],
            [o[1] for o in others],
            list(FILLER_WORDS),
        )
        await conn.execute(
            "ALTER TABLE extraction_fields ADD CONSTRAINT pk_extraction_fields PRIMARY KEY (id), "
            "ADD CONSTRAINT uq_extraction_fields_extraction_id_section_field "
            "UNIQUE (extraction_id, section, field)"
        )
        await conn.execute("RESET session_replication_role")
        await conn.execute("VACUUM ANALYZE")
        counts = {
            table: await conn.fetchval(f"SELECT count(*) FROM {table}")
            for table in (
                "orgs",
                "users",
                "studies",
                "extractions",
                "extraction_fields",
                "report_signatures",
            )
        }
    finally:
        await conn.close()
    return {
        "rows": counts,
        "filler_fields": len(others),
        "seconds": round(time.monotonic() - t0, 1),
    }


async def relation_bytes(conn: Any, names: Sequence[str]) -> int:
    total = 0
    for name in names:
        size = await conn.fetchval("SELECT pg_total_relation_size(to_regclass($1))", name)
        total += int(size or 0)
    return total


async def write_probe(conn: Any, library: Library) -> dict[str, float]:
    """What a write costs with the candidate's objects in place: one extraction's fields
    written as ``persist_extraction`` does (one INSERT per field) and a creator's rename,
    each in a transaction rolled back; medians in milliseconds."""
    study = next(
        s for s in library.studies if s.firm == 0 and s.subject and s.status == "completed"
    )
    keys = [spec.key.split(".", 1) for spec in FIELDS]
    extraction_ms: list[float] = []
    rename_ms: list[float] = []
    for _ in range(WRITE_PROBES):
        tx = conn.transaction()
        await tx.start()
        doc_id, ext_id = uuid.uuid4(), uuid.uuid4()
        await conn.execute(
            "INSERT INTO documents (id, study_id, kind, filename, content_type, bytes, sha256, "
            "gcs_uri) "
            "VALUES ($1, $2, 'appraisal', 'a.pdf', 'application/pdf', 1, $3, $4)",
            doc_id,
            study.id,
            uuid.uuid4().hex + uuid.uuid4().hex,
            f"gs://probe/{doc_id}",
        )
        await conn.execute(
            "INSERT INTO extractions (id, study_id, document_id, revision, method, raw) "
            "VALUES ($1, $2, $3, 2, 'probe', '{}')",
            ext_id,
            study.id,
            doc_id,
        )
        t0 = time.perf_counter()
        for section, name in keys:
            await conn.execute(
                "INSERT INTO extraction_fields (extraction_id, study_id, section, field, "
                "value_text, confidence, source) "
                "VALUES ($1, $2, $3, $4, $5, 0.9, 'probe')",
                ext_id,
                study.id,
                section,
                name,
                f"{name} value",
            )
        extraction_ms.append((time.perf_counter() - t0) * 1000)
        await tx.rollback()
        tx = conn.transaction()
        await tx.start()
        t0 = time.perf_counter()
        await conn.execute(
            "UPDATE users SET display_name = display_name || ' Jr' WHERE id = $1",
            library.searcher.id,
        )
        rename_ms.append((time.perf_counter() - t0) * 1000)
        await tx.rollback()
    return {
        "extraction_fields": len(keys),
        "extraction_ms": round(statistics.median(extraction_ms), 2),
        "creator_rename_ms": round(statistics.median(rename_ms), 2),
        "creator_studies": sum(1 for s in library.studies if s.created_by == library.searcher.id),
    }


def percentile(samples: Sequence[float], p: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        return float("nan")
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


async def measure_candidate(
    conn: Any,
    candidate: Candidate,
    library: Library,
    queries: list[tuple[str, str]],
    plans_dir: Path,
    run: record.EvalRun,
    size: str,
) -> dict[str, Any]:
    t0 = time.monotonic()
    for statement in candidate.setup:
        await conn.execute(statement)
    build_s = time.monotonic() - t0
    added = await relation_bytes(conn, candidate.created)
    probe = await write_probe(conn, library)
    try:
        # Correctness: the full answer of every query against the oracle.
        exact = 0
        evaluated = 0
        wrong: list[dict[str, Any]] = []
        deadline = time.monotonic() + CANDIDATE_BUDGET_S
        for qid, q in queries:
            if time.monotonic() > deadline:
                break
            evaluated += 1
            count_sql, _, ids_sql, params = listing_sql(candidate, q, library.searcher.id)
            got = {r["id"] for r in await conn.fetch(ids_sql, *params)}
            count = await conn.fetchval(count_sql, *params)
            expected = oracle(library, q, accents=candidate.accents or _accent_label(qid))
            ok = got == expected and count == len(got)
            exact += ok
            labelled = next((lab for lab in LABELLED if lab.id == qid), None)
            if labelled is not None:
                needles_ok = all(library.needles[n] in got for n in labelled.must) and not any(
                    library.needles[n] in got for n in labelled.must_not
                )
                run.case(
                    f"{size}:{candidate.name}:{qid}",
                    "passed" if ok and needles_ok else "failed",
                    kind="labelled",
                    detail={
                        "q": q,
                        "category": labelled.category,
                        "returned": len(got),
                        "expected": len(expected),
                        "needles_ok": needles_ok,
                    },
                )
            if not ok:
                wrong.append(
                    {
                        "query": qid,
                        "q": q,
                        "returned": len(got),
                        "expected": len(expected),
                        "missing": len(expected - got),
                        "extra": len(got - expected),
                    }
                )
        # Latency: count + first page, one warm-up pass then timed passes, within a budget
        # (the correctness pass has already read every query's rows once).
        samples: list[float] = []
        deadline = time.monotonic() + CANDIDATE_BUDGET_S
        cut = evaluated < len(queries)
        for _, q in queries[:evaluated]:
            if time.monotonic() > deadline:
                break
            count_sql, page_sql, _, params = listing_sql(candidate, q, library.searcher.id)
            await conn.fetch(page_sql, *params)
        for _ in range(TIMED_PASSES):
            for _, q in queries:
                if time.monotonic() > deadline:
                    cut = True
                    break
                count_sql, page_sql, _, params = listing_sql(candidate, q, library.searcher.id)
                t = time.perf_counter()
                await conn.fetchval(count_sql, *params)
                await conn.fetch(page_sql, *params)
                samples.append((time.perf_counter() - t) * 1000)
            if cut:
                break
        # Plans of three representative queries.
        out = plans_dir / candidate.name
        out.mkdir(parents=True, exist_ok=True)
        for label, q in (("rare", "quillon"), ("common", "austin"), ("one_char", "a")):
            count_sql, page_sql, _, params = listing_sql(candidate, q, library.searcher.id)
            lines = [f"-- q={q!r}\n-- count"]
            lines += [
                r[0] for r in await conn.fetch(f"EXPLAIN (ANALYZE, BUFFERS) {count_sql}", *params)
            ]
            lines += ["-- first page"]
            lines += [
                r[0] for r in await conn.fetch(f"EXPLAIN (ANALYZE, BUFFERS) {page_sql}", *params)
            ]
            (out / f"{label}.txt").write_text("\n".join(lines) + "\n")
    finally:
        for statement in candidate.teardown:
            await conn.execute(statement)
    return {
        "candidate": candidate.name,
        "description": candidate.description,
        "queries": len(queries),
        "evaluated": evaluated,
        "exact": exact,
        "wrong": wrong,
        "latency_ms": {
            "samples": len(samples),
            "p50": round(percentile(samples, 0.5), 2),
            "p95": round(percentile(samples, 0.95), 2),
            "max": round(max(samples), 2) if samples else None,
            "cut_short": cut,
        },
        "added_bytes": added,
        "build_seconds": round(build_s, 2),
        "writes": probe,
    }


async def measure_implemented(
    conn: Any,
    dsn: str,
    library: Library,
    queries: list[tuple[str, str]],
    plans_dir: Path,
    run: record.EvalRun,
    size: str,
) -> dict[str, Any]:
    """The shipped change, end to end: ``alembic upgrade head`` on the seeded library (the
    migration's backfill and index build are the build time), then the repository itself:
    correctness from its own filter clauses (every matching id), latency from
    :meth:`StudyRepository.list_for_user` as the route calls it (count, first page, rows
    loaded as ORM objects), plans from the statement it runs."""
    from alembic import command
    from sqlalchemy import func, select
    from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from basis.db.models import OrgMember, Study
    from basis.db.repositories.study import StudyFilters, StudyRepository, _filter_clauses

    t0 = time.monotonic()
    await asyncio.to_thread(command.upgrade, _alembic(dsn), "head")
    build_s = time.monotonic() - t0
    added = await relation_bytes(conn, ("study_search",))
    probe = await write_probe(conn, library)
    engine = create_async_engine(
        make_url(dsn).set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user = library.searcher.id
    member = (OrgMember.org_id == Study.org_id) & (OrgMember.user_id == user)

    def matching(q: str) -> Any:
        clauses = _filter_clauses(StudyFilters(q=q))
        return select(Study.id).join(OrgMember, member).where(*clauses)

    exact = 0
    evaluated = 0
    wrong: list[dict[str, Any]] = []
    samples: list[float] = []
    cut = False
    try:
        async with sessions() as session:
            deadline = time.monotonic() + CANDIDATE_BUDGET_S
            for qid, q in queries:
                if time.monotonic() > deadline:
                    break
                evaluated += 1
                got = set((await session.scalars(matching(q))).all())
                page = await StudyRepository(session).list_for_user(
                    user, PAGE, filters=StudyFilters(q=q)
                )
                expected = oracle(library, q, accents=True)
                ok = got == expected and page.total == len(got)
                exact += ok
                labelled = next((lab for lab in LABELLED if lab.id == qid), None)
                if labelled is not None:
                    needles_ok = all(library.needles[n] in got for n in labelled.must) and not any(
                        library.needles[n] in got for n in labelled.must_not
                    )
                    run.case(
                        f"{size}:{IMPLEMENTED}:{qid}",
                        "passed" if ok and needles_ok else "failed",
                        kind="labelled",
                        detail={
                            "q": q,
                            "category": labelled.category,
                            "returned": len(got),
                            "expected": len(expected),
                            "needles_ok": needles_ok,
                        },
                    )
                if not ok:
                    wrong.append(
                        {
                            "query": qid,
                            "q": q,
                            "returned": len(got),
                            "total": page.total,
                            "expected": len(expected),
                            "missing": len(expected - got),
                            "extra": len(got - expected),
                        }
                    )
                await session.rollback()  # each read in a fresh transaction, as a request
            deadline = time.monotonic() + CANDIDATE_BUDGET_S
            cut = evaluated < len(queries)
            for _ in range(TIMED_PASSES):
                for _, q in queries:
                    if time.monotonic() > deadline:
                        cut = True
                        break
                    t = time.perf_counter()
                    await StudyRepository(session).list_for_user(
                        user, PAGE, filters=StudyFilters(q=q)
                    )
                    samples.append((time.perf_counter() - t) * 1000)
                    await session.rollback()
                if cut:
                    break
            out = plans_dir / IMPLEMENTED
            out.mkdir(parents=True, exist_ok=True)
            for label, q in (("rare", "quillon"), ("common", "austin"), ("one_char", "a")):
                stmt = (
                    select(func.count())
                    .select_from(Study)
                    .join(OrgMember, member)
                    .where(*_filter_clauses(StudyFilters(q=q)))
                )
                sql = str(
                    stmt.compile(
                        dialect=PGDialect_asyncpg(),  # type: ignore[no-untyped-call]
                        compile_kwargs={"literal_binds": True},
                    )
                )
                lines = [f"-- q={q!r}\n-- count, as list_for_user runs it\n{sql}"]
                lines += [r[0] for r in await conn.fetch(f"EXPLAIN (ANALYZE, BUFFERS) {sql}")]
                (out / f"{label}.txt").write_text("\n".join(lines) + "\n")
    finally:
        await engine.dispose()
    return {
        "candidate": IMPLEMENTED,
        "description": "the shipped change: migration 0033 (study_search, triggers, pg_trgm "
        "GIN) and StudyRepository.list_for_user, measured through the repository",
        "queries": len(queries),
        "evaluated": evaluated,
        "exact": exact,
        "wrong": wrong,
        "latency_ms": {
            "samples": len(samples),
            "p50": round(percentile(samples, 0.5), 2),
            "p95": round(percentile(samples, 0.95), 2),
            "max": round(max(samples), 2) if samples else None,
            "cut_short": cut,
        },
        "added_bytes": added,
        "build_seconds": round(build_s, 2),
        "writes": probe,
    }


def _accent_label(qid: str) -> bool:
    """The accent labels are scored against the folded semantics for every candidate: that
    is the intended behaviour, and a candidate that does not fold fails them."""
    return qid.startswith("accent_")


async def run_size(
    admin_dsn: str, size: str, total: int, args: argparse.Namespace, run: record.EvalRun
) -> dict[str, Any]:
    library = build_library(total, extra_users=args.extra_users)
    dsn = await create_schema(admin_dsn, f"library_{size}")
    seeded = await seed(dsn, library, args.filler_fields)
    print(f"[{size}] seeded {seeded}", flush=True)
    queries = [(lab.id, lab.q) for lab in LABELLED] + [
        (f"sampled_{i:03d}", q) for i, q in enumerate(sampled_queries(library, args.sampled))
    ]
    searcher_visible = len(oracle(library, "", accents=False))
    results = []
    conn = await asyncpg.connect(dsn)
    try:
        for candidate in CANDIDATES:
            if args.candidates and candidate.name not in args.candidates:
                continue
            result = await measure_candidate(
                conn, candidate, library, queries, args.out / "plans" / size, run, size
            )
            print(
                f"[{size}] {candidate.name:14s} exact {result['exact']}/{result['queries']} "
                f"p50 {result['latency_ms']['p50']} ms p95 {result['latency_ms']['p95']} ms "
                f"added {result['added_bytes'] / 1e6:.1f} MB build {result['build_seconds']} s "
                f"writes {result['writes']}",
                flush=True,
            )
            results.append(result)
        if not args.candidates or IMPLEMENTED in args.candidates:
            result = await measure_implemented(
                conn, dsn, library, queries, args.out / "plans" / size, run, size
            )
            print(
                f"[{size}] {IMPLEMENTED:14s} exact {result['exact']}/{result['queries']} "
                f"p50 {result['latency_ms']['p50']} ms p95 {result['latency_ms']['p95']} ms "
                f"added {result['added_bytes'] / 1e6:.1f} MB build {result['build_seconds']} s "
                f"writes {result['writes']}",
                flush=True,
            )
            results.append(result)
    finally:
        await conn.close()
        if not args.keep:
            admin = await asyncpg.connect(admin_dsn)
            try:
                await admin.execute(f'DROP DATABASE IF EXISTS "library_{size}" WITH (FORCE)')
            finally:
                await admin.close()
    return {
        "size": size,
        "studies": total,
        "searcher_visible": searcher_visible,
        "seeded": seeded,
        "candidates": results,
    }


async def amain(args: argparse.Namespace, run: record.EvalRun) -> list[dict[str, Any]]:
    container = None
    admin_dsn = args.database_url
    if admin_dsn is None:
        from testcontainers.community.postgres import PostgresContainer

        # Docker's default 64 MB /dev/shm is too small for parallel VACUUM and index builds
        # at 200k studies (a managed PostgreSQL has no such limit); the server settings are
        # the image's defaults, recorded in the manifest.
        container = PostgresContainer(PG_IMAGE, driver=None).with_kwargs(shm_size="1g")
        container.start()
        admin_dsn = container.get_connection_url()
    try:
        server = await asyncpg.connect(admin_dsn)
        try:
            run.note(
                postgres=await server.fetchval("SELECT version()"),
                shared_buffers=await server.fetchval("SHOW shared_buffers"),
                work_mem=await server.fetchval("SHOW work_mem"),
            )
        finally:
            await server.close()
        out = []
        for size in args.sizes:
            out.append(await run_size(admin_dsn, size, SIZES[size], args, run))
            # Each size as it finishes, so a later size that fails loses nothing measured.
            (args.out / "comparison.json").write_text(json.dumps(out, indent=2, default=str))
        return out
    finally:
        if container is not None:
            container.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="the run directory")
    parser.add_argument("--sizes", nargs="+", choices=list(SIZES), default=list(SIZES))
    parser.add_argument("--candidates", nargs="*", help="only these candidates")
    parser.add_argument(
        "--sampled", type=int, default=SAMPLED_QUERIES, help="sampled queries per size"
    )
    parser.add_argument(
        "--filler-fields", type=int, default=len(FIELDS), help="other extraction fields per study"
    )
    parser.add_argument(
        "--database-url", help="an admin DSN (postgresql://...) instead of a container"
    )
    parser.add_argument("--keep", action="store_true", help="keep the seeded databases")
    parser.add_argument(
        "--extra-users", type=int, default=0, help="members of other firms who created nothing"
    )
    args = parser.parse_args(argv)
    run = record.EvalRun.begin(
        args.out, harness=__name__, args=args, component="library_search", version=VERSION
    )
    run.note(
        sizes={s: SIZES[s] for s in args.sizes},
        firms=FIRMS,
        searcher_share=SEARCHER_SHARE,
        page=PAGE,
        timed_passes=TIMED_PASSES,
        subject_fields=list(SUBJECT_FIELDS),
        labelled=[lab.__dict__ for lab in LABELLED],
    )
    try:
        sizes = asyncio.run(amain(args, run))
    except BaseException as exc:
        run.finish(None, status="error", exit_code=1, error=repr(exc))
        raise
    (args.out / "comparison.json").write_text(json.dumps(sizes, indent=2, default=str) + "\n")
    summary = record.summarize_cases(run.cases)
    run.finish({"version": VERSION, "sizes": sizes, **summary})
    print(record.summary_line(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
