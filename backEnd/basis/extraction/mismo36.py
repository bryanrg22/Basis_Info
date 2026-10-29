"""Read a UAD 3.6 (MISMO 3.6) appraisal XML into catalogue fields, and find them on its PDF.

UAD 3.6, the redesigned URAR, is delivered as MISMO 3.6 XML (root ``MESSAGE``, namespace
:data:`MISMO_NS`), mandatory for GSE submissions from 2026-11-02. The XML holds every value the
PDF prints, typed and unambiguous, so for such an appraisal the fields come from the XML and the
PDF is used only to show where each value is printed.

**Mapping.** :data:`basis.extraction.uad36_map.MAPPING` (generated from the GSE A-1 and G-1
specifications, ``evals/appraisal/uad36_spec.py``) names, per catalogue key, the data points
(full xPath from ``MESSAGE``), a rule (:data:`RULES`), qualifiers on repeating containers and the
Scope of Work indicators that gate an approach.

**Subject, not comparables.** Every ``PROPERTY`` under ``VALUATION_ANALYSIS/PROPERTIES`` is
filtered to the subject: ``@ValuationUseType="SubjectProperty"`` (A-1 Subject Property rows); a
document that marks no property so is read only when exactly one property carries no use type
(A-1 notes the subject instance may omit it). Comparables (``SalesComparable``, ...) are never
read. Containers that repeat per party, improvement or cost line are narrowed by the mapping's
``where`` (``PartyRoleType = Appraiser``; ``ImprovementType = Dwelling``) or through the xlink
relationships A-1 documents (``scope``): the cost lines of the dwelling
(``IMPROVEMENT_COST_AND_DEPRECIATED_VALUE_IsAssociatedWith_IMPROVEMENT``), the cost approach's
data source (``DATA_SOURCE_IsDataSourceFor_COST_APPROACH``), the appraiser's signature
(``SIGNATORY_IsAssociatedWith_ROLE``).

**Units.** UAD 3.6 reports bedrooms, bathrooms, occupancy and finished area per living unit.
The catalogue keys are single-unit (1004). For one living unit (plus an accessory unit at most)
they are that unit's values; for a 2-4 unit property the per-unit keys (occupant, bedrooms,
bathrooms) are left empty with a note, ``improvements.gla_sqft`` is the sum over the living units
(accessory units excluded). ``improvements.units`` comes from the subject's own counts (A-1
Units Excluding ADUs, Accessory Dwelling Units; :func:`units_value`): One, One with Accessory
Unit, or the count of a 2-4 unit property.

**Values.** Each value is written the way the PDF prints it (``$489,000``, ``09/20/2019``, an
enumeration as its A-1 *Output Report Display Text*, an ``Other`` as its ``...OtherDescription``)
and cleaned by the production cleaners (:func:`basis.extraction.cleaners.clean_field`), so the
typed columns match what the PDF path produces; source ``mismo_xml``, confidence 1.0. A gated
approach not developed (``CostApproachIndicator`` / ``IncomeApproachIndicator`` false) leaves its
keys empty.

**Regions and the cross-check** (:func:`locate_fields`). Each value is looked for on the PDF's pages
(Azure DI layout geometry) as the whole words it prints as, the occurrence nearest one of the
key's UAD 3.6 captions (A-1 Report Location labels in the mapping). A value that the PDF does not
print on any page where its caption is printed (or anywhere, when no caption is found) gets no
region and an ``xml_pdf_mismatch`` warning: the XML and the PDF of one delivery disagree, and
the reviewer is told rather than the XML being silently preferred. Derived values (a percentage,
a count, ``Yes``/``No``) are located through the printed values they come from and never raise
the issue. Known limit: a one- or two-digit value found on its caption's page passes the check
even when it is printed elsewhere on that page.

**Safety** (:func:`parse_mismo`): at most :data:`MAX_XML_BYTES`; a document with a DOCTYPE, an
entity declaration or an external entity reference is refused before it is parsed (no entity
expansion, no external fetches); the root must be a MISMO ``MESSAGE``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final
from xml.etree import ElementTree
from xml.parsers import expat

from basis.extraction.confidence import review_gate
from basis.extraction.derived import derive_fields
from basis.extraction.field_mappings import FIELD_BY_KEY
from basis.extraction.geometry import Box, Layout, Page, find_all
from basis.extraction.geometry import locate as locate_text
from basis.extraction.results import (
    Candidate,
    ExtractedField,
    ExtractionResult,
    Region,
    ValidationIssue,
)
from basis.extraction.uad36_map import MAPPING
from basis.extraction.validation import validate_fields

MISMO_NS: Final = "http://www.mismo.org/residential/2009/schemas"
XLINK_NS: Final = "http://www.w3.org/1999/xlink"
MAX_XML_BYTES: Final = 25 * 1024 * 1024
SOURCE: Final = "mismo_xml"
CONFIDENCE: Final = 1.0
METHOD: Final = "mismo_xml"
MISMATCH_RULE: Final = "xml_pdf_mismatch"

Element = ElementTree.Element


class InvalidMismo(ValueError):
    """The bytes are not a readable, safe MISMO 3.6 appraisal XML."""


# --------------------------------------------------------------------------- parsing


def _refuse(*_: object) -> None:
    raise InvalidMismo("DOCTYPE, entity declarations and external entities are not accepted")


def _refuse_external(*_: object) -> int:
    raise InvalidMismo("external entities are not accepted")


def parse_mismo(data: bytes) -> Element:
    """The ``MESSAGE`` root of a MISMO 3.6 document (module doc: safety)."""
    if len(data) > MAX_XML_BYTES:
        raise InvalidMismo(f"the XML is larger than {MAX_XML_BYTES} bytes")
    guard = expat.ParserCreate()
    guard.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    guard.StartDoctypeDeclHandler = _refuse
    guard.EntityDeclHandler = _refuse
    guard.UnparsedEntityDeclHandler = _refuse
    guard.ExternalEntityRefHandler = _refuse_external
    try:
        guard.Parse(data, True)
    except expat.ExpatError as exc:
        raise InvalidMismo(f"not well-formed XML: {exc}") from None
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:  # pragma: no cover - the guard parsed it already
        raise InvalidMismo(f"not well-formed XML: {exc}") from None
    if root.tag != f"{{{MISMO_NS}}}MESSAGE":
        raise InvalidMismo(f"the root is {root.tag!r}, not a MISMO MESSAGE")
    return root


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xlink(element: Element, name: str) -> str | None:
    return element.get(f"{{{XLINK_NS}}}{name}")


def _text(element: Element) -> str:
    return (element.text or "").strip()


# --------------------------------------------------------------------------- the document


class Mismo36Document:
    """A parsed UAD 3.6 appraisal: the subject property, the relationships, parent links."""

    def __init__(self, root: Element) -> None:
        self.root = root
        self.parent: dict[Element, Element] = {
            child: node for node in root.iter() for child in node
        }
        self.by_label: dict[str, Element] = {}
        self.arcs: list[tuple[str, str, str]] = []  # (arcrole, from, to)
        for node in root.iter():
            label = _xlink(node, "label")
            if label:
                self.by_label.setdefault(label, node)
            if local(node.tag) == "RELATIONSHIP":
                role = (_xlink(node, "arcrole") or "").rsplit("/", 1)[-1]
                self.arcs.append((role, _xlink(node, "from") or "", _xlink(node, "to") or ""))
        self.subjects = self._subjects()
        self.notes: list[str] = []

    def _subjects(self) -> set[Element]:
        candidates = [
            node
            for node in self.root.iter(f"{{{MISMO_NS}}}PROPERTY")
            if local(self.parent.get(node, node).tag) == "PROPERTIES"
            and local(self.parent.get(self.parent[node], node).tag) == "VALUATION_ANALYSIS"
        ]
        marked = {p for p in candidates if p.get("ValuationUseType") == "SubjectProperty"}
        if marked:
            return marked
        unmarked = [p for p in candidates if p.get("ValuationUseType") is None]
        if len(unmarked) == 1:
            return set(unmarked)
        raise InvalidMismo("no subject PROPERTY (ValuationUseType=SubjectProperty)")

    def indicator(self, name: str) -> bool | None:
        """A Scope of Work indicator (``CostApproachIndicator``): True, False or absent."""
        for node in self.root.iter(f"{{{MISMO_NS}}}{name}"):
            if local(self.parent.get(node, node).tag) == "SCOPE_OF_WORK_DETAIL":
                text = _text(node).lower()
                if text in ("true", "false"):
                    return text == "true"
        return None

    def ancestor(self, element: Element, tag: str) -> Element | None:
        node: Element | None = element
        while node is not None:
            if local(node.tag) == tag:
                return node
            node = self.parent.get(node)
        return None

    def linked(
        self, arcrole: str, *, to: Iterable[str] = (), from_: Iterable[str] = ()
    ) -> set[str]:
        """The ``from`` labels of the arcs of ``arcrole`` to any of ``to`` (or the ``to``
        labels from any of ``from_``)."""
        targets, sources = set(to), set(from_)
        out: set[str] = set()
        for role, start, end in self.arcs:
            if role != arcrole:
                continue
            if end in targets:
                out.add(start)
            if start in sources:
                out.add(end)
        return out

    # ------------------------------------------------------------------- living units

    def living_units(self) -> tuple[list[Element], list[Element]]:
        """(living units of the subject's dwellings, accessory units anywhere on the subject:
        an ADU may be its own outbuilding)."""
        living: list[Element] = []
        accessory: list[Element] = []
        for subject in self.subjects:
            for improvement in subject.iter(f"{{{MISMO_NS}}}IMPROVEMENT"):
                dwelling = _has_leaf(improvement, "ImprovementType", ("Dwelling",))
                for unit in improvement.iter(f"{{{MISMO_NS}}}PROPERTY_UNIT"):
                    if _has_leaf(unit, "AccessoryDwellingUnitIndicator", ("true",)):
                        accessory.append(unit)
                    elif dwelling:
                        living.append(unit)
        return living, accessory


def _has_leaf(element: Element, point: str, values: Sequence[str]) -> bool:
    return any(_text(node) in values for node in element.iter(f"{{{MISMO_NS}}}{point}"))


# --------------------------------------------------------------------------- xPath walk


def _scope_filter(doc: Mismo36Document, scope: str | None, tag: str) -> Callable[[Element], bool]:
    """Relationship scopes (module doc) applied at the container they name."""
    if scope in ("dwelling_cost", "all_costs") and tag == "IMPROVEMENT_COST_AND_DEPRECIATED_VALUE":
        if scope == "all_costs":
            return lambda _: True
        dwellings = {
            label
            for label, node in doc.by_label.items()
            if local(node.tag) == "IMPROVEMENT"
            and _has_leaf(node, "ImprovementType", ("Dwelling",))
        }
        linked = doc.linked(
            "IMPROVEMENT_COST_AND_DEPRECIATED_VALUE_IsAssociatedWith_IMPROVEMENT", to=dwellings
        )
        costs = list(doc.root.iter(f"{{{MISMO_NS}}}{tag}"))
        if not linked and len(costs) == 1:
            return lambda _: True  # one costed improvement and no link: it is the dwelling
        return lambda node: (_xlink(node, "label") or "") in linked
    if scope == "living_units" and tag == "PROPERTY_UNIT":
        return lambda node: not _has_leaf(node, "AccessoryDwellingUnitIndicator", ("true",))
    if scope == "garage_cost" and tag == "REPLACEMENT_REPRODUCTION_COST":
        return _is_garage_cost
    if scope == "current_listing" and tag == "LISTING_INFORMATION":
        return lambda node: node is _current_listing(doc, node)
    if scope == "cost_data_source" and tag == "DATA_SOURCE":
        approaches = {
            label for label, node in doc.by_label.items() if local(node.tag) == "COST_APPROACH"
        }
        sources = doc.linked("DATA_SOURCE_IsDataSourceFor_COST_APPROACH", to=approaches)
        return lambda node: (_xlink(node, "label") or "") in sources
    if scope == "appraiser_signatory" and tag == "SIGNATORY":
        roles = {
            label
            for label, node in doc.by_label.items()
            if local(node.tag) == "ROLE" and _has_leaf(node, "PartyRoleType", ("Appraiser",))
        }
        signatories = doc.linked("SIGNATORY_IsAssociatedWith_ROLE", to=roles)
        if not signatories:
            return lambda _: True  # no link: every signatory (the first is used)
        return lambda node: (_xlink(node, "label") or "") in signatories
    return lambda _: True


GARAGE_COST_TYPES: Final = ("AttachedGarage", "BuiltInGarage", "Carport")


def _is_garage_cost(node: Element) -> bool:
    """A cost row of a garage: an A-1 garage area type, or Other described as a garage."""
    kind = node.find(f"{{{MISMO_NS}}}ReplacementOrReproductionCostAreaType")
    if kind is None:
        return False
    if _text(kind) in GARAGE_COST_TYPES:
        return True
    other = node.find(f"{{{MISMO_NS}}}ReplacementOrReproductionCostAreaTypeOtherDescription")
    return _text(kind) == "Other" and other is not None and "garage" in _text(other).lower()


def _current_listing(doc: Mismo36Document, node: Element) -> Element:
    """The listing with the latest start date among ``node``'s siblings (the last of equals):
    the subject's current or most recent listing."""
    parent = doc.parent.get(node)
    listings = [c for c in parent if local(c.tag) == local(node.tag)] if parent is not None else []

    def start(listing: Element) -> str:
        found = listing.find(f".//{{{MISMO_NS}}}ListingStartDate")
        return _text(found) if found is not None else ""

    best = node
    for listing in listings:
        if start(listing) >= start(best):
            best = listing
    return best


def select(
    doc: Mismo36Document,
    path: str,
    where: Mapping[str, Mapping[str, Sequence[str]]] = {},  # noqa: B006 - read only
    scope: str | None = None,
) -> list[Element]:
    """The elements at ``path`` (A-1 xPath from ``MESSAGE``), in document order: the subject
    property only, each ``where`` container holding one of its values, the ``scope``
    containers only."""
    steps = [s for s in path.split("/") if s]
    if not steps or steps[0] != local(doc.root.tag):
        return []
    nodes = [doc.root]
    for step in steps[1:]:
        keep = _scope_filter(doc, scope, step)
        tests = where.get(step, {})
        found: list[Element] = []
        for node in nodes:
            for child in node:
                if local(child.tag) != step:
                    continue
                if (
                    step == "PROPERTY"
                    and child not in doc.subjects
                    and local(node.tag) == "PROPERTIES"
                    and _under_analysis(doc, node)
                ):
                    continue  # a comparable (or another use type): never read
                if tests and not all(_has_leaf(child, p, v) for p, v in tests.items()):
                    continue
                if keep(child):
                    found.append(child)
        nodes = found
    return nodes


def _under_analysis(doc: Mismo36Document, properties: Element) -> bool:
    parent = doc.parent.get(properties)
    return parent is not None and local(parent.tag) == "VALUATION_ANALYSIS"


# --------------------------------------------------------------------------- formatting


def _decimal(text: str) -> Decimal | None:
    try:
        value = Decimal(text.replace(",", "").strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _grouped(value: Decimal) -> str:
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def money(value: Decimal) -> str:
    return f"-${_grouped(-value)}" if value < 0 else f"${_grouped(value)}"


def us_date(text: str) -> str:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text.strip())
    return f"{match.group(2)}/{match.group(3)}/{match.group(1)}" if match else text.strip()


def us_month(text: str) -> str:
    match = re.fullmatch(r"(\d{4})-(\d{2})", text.strip())
    return f"{match.group(2)}/{match.group(1)}" if match else us_date(text)


def decamel(code: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", code)


@dataclass
class Reading:
    """What a rule read: the text as printed (cleaned into the field), the printed fragments
    it comes from (located on the PDF), whether a fragment the PDF lacks is a mismatch, and a
    number to store when the text is a word (``No`` concessions = 0)."""

    raw: str
    parts: list[str] = field(default_factory=list)
    verify: bool = True
    num: Decimal | None = None


@dataclass
class Values:
    """The leaf elements of one mapping entry, per source data point."""

    doc: Mismo36Document
    entry: Mapping[str, Any]
    by_point: dict[str, list[Element]]

    def leaves(self, index: int = 0) -> list[Element]:
        sources = self.entry["sources"]
        if index >= len(sources):
            return []
        return self.by_point.get(sources[index]["path"].rsplit("/", 1)[-1], [])

    def display(self, element: Element, index: int = 0) -> str:
        """An enumeration as its A-1 display text (``Other``: its description)."""
        code = _text(element)
        enums: Mapping[str, str] = self.entry["sources"][index]["enums"]
        if not enums:
            return code
        shown = enums.get(code)
        if shown is None or shown == "<<Does Not Display>>":
            parent = self.doc.parent.get(element)
            other = None
            if parent is not None:
                other = parent.find(f"{{{MISMO_NS}}}{local(element.tag)}OtherDescription")
            if other is not None and _text(other):
                return _text(other)
            if shown is None:
                self.doc.notes.append(f"{local(element.tag)} {code!r} is not an A-1 value")
                return decamel(code)
            return ""
        return shown


def _first(values: Values, index: int = 0) -> Element | None:
    for node in values.leaves(index):
        if _text(node):
            return node
    return None


def rule_text(values: Values) -> Reading | None:
    node = _first(values)
    if node is None:
        return None
    text = values.display(node)
    return Reading(text, [text]) if text else None


def rule_join(values: Values) -> Reading | None:
    shown: list[str] = []
    for node in values.leaves():
        text = values.display(node) if _text(node) else ""
        if text and text not in shown:
            shown.append(text)
    return Reading(", ".join(shown), shown) if shown else None


def rule_amount(values: Values) -> Reading | None:
    node = _first(values)
    amount = _decimal(_text(node)) if node is not None else None
    if amount is None:
        return None
    return Reading(money(amount), [money(amount)])


def rule_number(values: Values) -> Reading | None:
    node = _first(values)
    if node is None or _decimal(_text(node)) is None:
        return None
    return Reading(_text(node), [_text(node)])


def rule_date(values: Values) -> Reading | None:
    node = _first(values)
    if node is None:
        return None
    text = us_date(_text(node))
    return Reading(text, [text])


def rule_month(values: Values) -> Reading | None:
    node = _first(values)
    if node is None:
        return None
    text = us_month(_text(node))
    return Reading(text, [text])


def rule_bool(values: Values) -> Reading | None:
    node = _first(values)
    if node is None or _text(node).lower() not in ("true", "false"):
        return None
    word = "Yes" if _text(node).lower() == "true" else "No"
    return Reading(word, [word], verify=False)


def rule_area(values: Values) -> Reading | None:
    node = _first(values)
    amount = _decimal(_text(node)) if node is not None else None
    if node is None or amount is None:
        return None
    attribute = values.entry["sources"][0].get("attribute") or ""
    unit = node.get(attribute, "") if attribute else ""
    if unit.lower() == "acres":
        return Reading(f"{_text(node)} ac", [_text(node)])
    return Reading(f"{_grouped(amount)} sq ft", [_grouped(amount)])


def _sum(values: Values, *, money_format: bool) -> tuple[Decimal, list[str]] | None:
    total = Decimal(0)
    parts: list[str] = []
    found = False
    for index in range(len(values.entry["sources"])):
        for node in values.leaves(index):
            amount = _decimal(_text(node))
            if amount is None:
                continue
            found = True
            total += amount
            if amount:
                parts.append(money(amount) if money_format else _grouped(amount))
    return (total, parts) if found else None


def _is_money(values: Values) -> bool:
    return any(s.get("format") == "Amount" for s in values.entry["sources"])


def rule_sum(values: Values) -> Reading | None:
    is_money = _is_money(values)
    summed = _sum(values, money_format=is_money)
    if summed is None:
        return None
    total, parts = summed
    raw = money(total) if is_money else _grouped(total)
    return Reading(raw, parts or [raw], verify=bool(parts))


def rule_below_grade_area(values: Values) -> Reading | None:
    """The 1004's Basement Area: finished plus unfinished area of the below-grade levels
    (sources 0 and 1, G-1 1-144). A unit with no below-grade level reports none, so the value
    falls back to the Unit Interior's below-grade areas (sources 2-4: Finished Below Grade,
    Finished Below Grade (Nonstandard), Unfinished Below Grade; A-1 0700.0143, 1800.0398,
    0700.0144, "Deliver even if the value is zero"): ``0`` when the report prints 0 Sq. Ft.,
    the legacy 1004's 0 for no basement (UAD Appendix D)."""
    for indices in ((0, 1), (2, 3, 4)):
        amounts = [
            a
            for i in indices
            for a in (_decimal(_text(n)) for n in values.leaves(i))
            if a is not None
        ]
        if amounts:
            total = sum(amounts, Decimal(0))
            parts = [_grouped(a) for a in amounts if a]
            raw = _grouped(total)
            return Reading(raw, parts or [raw], verify=bool(parts))
    return None


def rule_single(values: Values) -> Reading | None:
    nodes = [n for n in values.leaves() if _decimal(_text(n)) is not None]
    if len(nodes) != 1:
        return None
    amount = _decimal(_text(nodes[0]))
    assert amount is not None
    text = money(amount) if _is_money(values) else _text(nodes[0])
    return Reading(text, [text])


def rule_thousands(values: Values) -> Reading | None:
    node = _first(values)
    amount = _decimal(_text(node)) if node is not None else None
    if amount is None:
        return None
    thousands = (amount / 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return Reading(str(thousands), [money(amount)])


def rule_finished_percent(values: Values) -> Reading | None:
    finished = _sum_index(values, 0)
    unfinished = _sum_index(values, 1)
    if finished is None or unfinished is None or finished + unfinished == 0:
        return None
    share = (finished * 100 / (finished + unfinished)).quantize(Decimal(1), ROUND_HALF_UP)
    parts = [_grouped(v) for v in (finished, unfinished) if v]
    return Reading(str(share), parts, verify=True)


def _sum_index(values: Values, index: int) -> Decimal | None:
    amounts = [_decimal(_text(n)) for n in values.leaves(index)]
    found = [a for a in amounts if a is not None]
    return sum(found, Decimal(0)) if found else None


def rule_effective_age(values: Values) -> Reading | None:
    node = _first(values, 0)
    if node is not None:
        return Reading(_text(node), [_text(node)])
    low, high = _first(values, 1), _first(values, 2)
    if low is None or high is None:
        return None
    text = f"{_text(low)}-{_text(high)}"
    return Reading(text, [_text(low), _text(high)])


def rule_name(values: Values) -> Reading | None:
    """One name per party (first middle last suffix, or the legal entity's full name), the
    parties joined by ``; ``."""
    parts_by_party: dict[Element, dict[str, str]] = {}
    for source in values.entry["sources"]:
        point = source["path"].rsplit("/", 1)[-1]
        for node in values.by_point.get(point, []):
            party = values.doc.ancestor(node, "PARTY")
            if party is not None and _text(node):
                parts_by_party.setdefault(party, {})[point] = _text(node)
    names: list[str] = []
    for parts in parts_by_party.values():
        name = parts.get("FullName") or " ".join(
            parts[p] for p in ("FirstName", "MiddleName", "LastName", "SuffixName") if p in parts
        )
        if name and name not in names:
            names.append(name)
    return Reading("; ".join(names), names) if names else None


# The order a client party is chosen in when several hold client roles (A-1 2.011).
CLIENT_ROLES: Final = ("Lender", "Investor", "Attorney", "Other", "ManagementCompany")


def rule_client(values: Values) -> Reading | None:
    """The client party's name: the first role of :data:`CLIENT_ROLES` that a party holds."""
    for role in CLIENT_ROLES:
        for node in values.leaves():
            party = values.doc.ancestor(node, "PARTY")
            if party is not None and _text(node) and _has_leaf(party, "PartyRoleType", (role,)):
                return Reading(_text(node), [_text(node)])
    return None


def rule_enum(values: Values) -> Reading | None:
    return rule_text(values)


def rule_indicator_no(values: Values) -> Reading | None:
    node = _first(values)
    if node is None:
        return None
    if _text(node).lower() == "false":
        return Reading("No", ["No"], verify=False, num=Decimal(0))
    if _text(node).lower() == "true":
        values.doc.notes.append(f"{values.entry['label']}: Yes, with no amount in UAD 3.6")
    return None


def rule_amount_or_no(values: Values) -> Reading | None:
    indicator = _first(values, 1)
    if indicator is not None and _text(indicator).lower() == "false":
        return Reading("No", ["No"], verify=False, num=Decimal(0))
    return rule_amount(values)


def rule_flood(values: Values) -> Reading | None:
    codes = [_text(n) for n in values.leaves() if _text(n)]
    if not codes:
        return None
    shown = [values.display(n) for n in values.leaves() if _text(n)]
    word = "Yes" if "FEMASpecialFloodHazardArea" in codes else "No"
    return Reading(word, [s for s in shown if s], verify=False)


def _single_unit(values: Values) -> Element | None:
    living, _ = values.doc.living_units()
    if len(living) == 1:
        return living[0]
    if len(living) > 1:
        values.doc.notes.append(
            f"{len(living)} living units: the per-unit keys (occupant, bedrooms, bathrooms) "
            "are left empty"
        )
    return None


def _in_unit(values: Values, unit: Element, index: int = 0) -> list[Element]:
    return [n for n in values.leaves(index) if values.doc.ancestor(n, "PROPERTY_UNIT") is unit]


def rule_per_unit(values: Values) -> Reading | None:
    unit = _single_unit(values)
    if unit is None:
        return None
    nodes = [n for n in _in_unit(values, unit) if _text(n)]
    if not nodes:
        return None
    text = values.display(nodes[0])
    return Reading(text, [text]) if text else None


def rule_per_unit_agree(values: Values) -> Reading | None:
    """A per-unit value every living unit shares (all units owner occupied); empty when the
    units differ."""
    living, _ = values.doc.living_units()
    shown = {values.display(n) for unit in living for n in _in_unit(values, unit) if _text(n)} - {
        ""
    }
    if len(shown) != 1:
        if len(shown) > 1:
            values.doc.notes.append(f"{values.entry['label']}: the living units differ")
        return None
    text = shown.pop()
    return Reading(text, [text])


def rule_address(values: Values) -> Reading | None:
    """The street line, then the unit as the report prints it (``201 Underhill Dr, Unit
    202``)."""
    line = _first(values, 0)
    if line is None:
        return None
    designator, unit = _first(values, 1), _first(values, 2)
    text = _text(line)
    parts = [text]
    if unit is not None:
        label = values.display(designator, 1) if designator is not None else ""
        suffix = f"{label} {_text(unit)}".strip()
        text = f"{text}, {suffix}"
        parts.append(_text(unit))
    return Reading(text, parts)


def rule_utility(values: Values) -> Reading | None:
    """Public, or the 1004's Other (describe) for a non-public utility, described by its
    A-1 service type when it has one (``Other: Well``, ``Other: Septic``), else by the
    ownership as printed (``Other: Private``)."""
    node = _first(values, 0)
    if node is None:
        return None
    shown = values.display(node)
    if _text(node) == "Public":
        return Reading(shown, [shown])
    service = _first(values, 1)
    described = values.display(service, 1) if service is not None else ""
    if not described:
        return Reading(f"Other: {shown}", [shown])
    return Reading(f"Other: {described}", [shown, described])


def rule_baths(values: Values) -> Reading | None:
    unit = _single_unit(values)
    if unit is None:
        return None
    full = [_text(n) for n in _in_unit(values, unit, 0) if _text(n)]
    half = [_text(n) for n in _in_unit(values, unit, 1) if _text(n)]
    if not full and not half:
        return None
    f, h = (full or ["0"])[0], (half or ["0"])[0]
    return Reading(f"{f}.{h}", [p for p in (f, h) if p], verify=False)


def rule_gla(values: Values) -> Reading | None:
    living, _ = values.doc.living_units()
    building = _first(values, 1)
    if len(living) > 1 and building is not None and _decimal(_text(building)) is not None:
        total = _decimal(_text(building))
        assert total is not None
        return Reading(_grouped(total), [_grouped(total)])
    amounts = [
        a
        for unit in living
        for a in (_decimal(_text(n)) for n in _in_unit(values, unit))
        if a is not None
    ]
    if not amounts:
        return None
    total = sum(amounts, Decimal(0))
    return Reading(_grouped(total), [_grouped(a) for a in amounts], verify=len(amounts) == 1)


def units_value(count: int, adus: int | None) -> str | None:
    """The 1004 Units box from a property's unit counts (A-1 0100.0022 Units Excluding ADUs,
    0100.0019 Accessory Dwelling Units): one unit is "One", or "One with Accessory Unit" with
    an ADU; a 2-4 unit property (no legacy 1004 code) is its count. ``None`` when the counts
    cannot say (no unit, or one unit and the ADU count unknown)."""
    if count < 1:
        return None
    if count > 1:
        return str(count)
    if adus is None:
        return None
    return "One with Accessory Unit" if adus > 0 else "One"


def _count(node: Element | None) -> int | None:
    number = _decimal(_text(node)) if node is not None else None
    if number is None or number < 0 or number != number.to_integral_value():
        return None
    return int(number)


def rule_units(values: Values) -> Reading | None:
    """:func:`units_value` of the subject's counts (``sources`` 0 and 1); a delivery without
    the living-unit count falls back to counting its PROPERTY_UNIT containers (living units
    of the dwellings, accessory units anywhere)."""
    count_node, adu_node = _first(values, 0), _first(values, 1)
    count, adus = _count(count_node), _count(adu_node)
    if count is not None:
        if adus is None and count == 1:
            adus = len(values.doc.living_units()[1])
        text = units_value(count, adus)
        parts = [_text(n) for n in (count_node, adu_node) if n is not None and _text(n)]
        return Reading(text, parts, verify=False) if text is not None else None
    living, accessory = values.doc.living_units()
    if not living:
        return None
    if len(living) > 1:
        return Reading(str(len(living)), [str(len(living))], verify=False)
    return Reading("One with Accessory Unit" if accessory else "One", [], verify=False)


def rule_car_storage(values: Values) -> Reading | None:
    shown: list[str] = []
    for index in (0, 1):
        for node in values.leaves(index):
            text = values.display(node, index) if _text(node) else ""
            if text and text not in shown:
                shown.append(text)
    return Reading(", ".join(shown), shown) if shown else None


RULES: Final[dict[str, Callable[[Values], Reading | None]]] = {
    "text": rule_text,
    "enum": rule_enum,
    "join": rule_join,
    "amount": rule_amount,
    "number": rule_number,
    "date": rule_date,
    "month": rule_month,
    "bool": rule_bool,
    "area": rule_area,
    "sum": rule_sum,
    "below_grade_area": rule_below_grade_area,
    "single": rule_single,
    "thousands": rule_thousands,
    "finished_percent": rule_finished_percent,
    "effective_age": rule_effective_age,
    "name": rule_name,
    "client": rule_client,
    "indicator_no": rule_indicator_no,
    "amount_or_no": rule_amount_or_no,
    "flood": rule_flood,
    "per_unit": rule_per_unit,
    "per_unit_agree": rule_per_unit_agree,
    "address": rule_address,
    "utility": rule_utility,
    "baths": rule_baths,
    "gla": rule_gla,
    "units": rule_units,
    "car_storage": rule_car_storage,
}


# --------------------------------------------------------------------------- reading


@dataclass
class XmlField:
    """One key read from the XML, before it is located on the PDF."""

    key: str
    reading: Reading
    captions: tuple[str, ...]


def gated_off(doc: Mismo36Document, gates: Sequence[str]) -> bool:
    """True when every gate indicator says the approach was not developed."""
    return bool(gates) and all(doc.indicator(g) is False for g in gates)


def read_entry(
    doc: Mismo36Document, key: str, entry: Mapping[str, Any] | None = None
) -> Reading | None:
    entry = entry if entry is not None else MAPPING[key]
    if entry["status"] != "mapped" or gated_off(doc, entry.get("gate") or ()):
        return None
    by_point: dict[str, list[Element]] = {}
    for source in entry["sources"]:
        point = source["path"].rsplit("/", 1)[-1]
        by_point[point] = select(doc, source["path"], entry.get("where") or {}, entry.get("scope"))
    return RULES[entry["rule"]](Values(doc, entry, by_point))


def read_document(
    doc: Mismo36Document, mapping: Mapping[str, Mapping[str, Any]] = MAPPING
) -> dict[str, XmlField]:
    out: dict[str, XmlField] = {}
    for key, entry in mapping.items():
        reading = read_entry(doc, key, entry)
        if reading is not None and reading.raw.strip():
            out[key] = XmlField(key, reading, tuple(entry.get("captions") or ()))
    return out


# --------------------------------------------------------------------------- the PDF


def variants(part: str) -> list[str]:
    """The ways a value may be printed: as read, as a grouped number, with or without ``$``,
    a date with or without leading zeros."""
    out = [part]
    plain = part.lstrip("$").replace(",", "")
    amount = _decimal(plain) if re.fullmatch(r"-?\d+(\.\d+)?", plain) else None
    if amount is not None:
        grouped = _grouped(amount)
        out += [grouped, plain, f"${grouped}"]
        if amount == amount.to_integral_value():
            out.append(f"{int(amount):,}.00")
    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", part)
    if match:
        out.append(f"{int(match.group(1))}/{int(match.group(2))}/{match.group(3)}")
    return list(dict.fromkeys(v for v in out if v))


def _distance(a: Box, b: Box) -> float:
    """The gap between two boxes (0 when they touch), squared: a value printed right after its
    caption on the caption's line is nearer than one on the next line below the caption's
    middle (centre distance would prefer the latter for a long caption)."""
    dx = max(0.0, b[0] - a[2], a[0] - b[2])
    dy = max(0.0, b[1] - a[3], a[1] - b[3])
    return float(dx * dx + dy * dy)


def _matches(page: Page, part: str) -> list[Box]:
    for text in variants(part):
        found = find_all(page, text, whole_words=True)
        if found:
            return found
    if len(part) >= 4 and not re.fullmatch(r"[\d,.$/-]+", part):
        return find_all(page, part)
    return []


@dataclass
class Located:
    page: int | None
    regions: tuple[Region, ...]
    missing: list[str]


def locate_parts(parts: Sequence[str], captions: Sequence[str], layout: Layout) -> Located:
    """Each part's occurrence nearest a caption, on a page where a caption is printed (any
    page when no caption is printed anywhere); the parts not found so."""
    caption_boxes: dict[int, list[Box]] = {}
    for page in layout.pages:
        boxes = [b for c in captions if (b := locate_text(page, c, whole_words=True)) is not None]
        if boxes:
            caption_boxes[page.number] = boxes
    regions: list[Region] = []
    missing: list[str] = []
    first_page: int | None = None
    for part in parts:
        best: tuple[float, int, Box] | None = None
        for page in layout.pages:
            near = caption_boxes.get(page.number)
            if caption_boxes and not near:
                continue
            for box in _matches(page, part):
                score = min(_distance(box, c) for c in near) if near else 0.0
                if best is None or (score, page.number) < (best[0], best[1]):
                    best = (score, page.number, box)
                if not near:
                    break
            if best is not None and not near:
                break
        if best is None:
            missing.append(part)
            continue
        region = Region(page=best[1], bbox=best[2])
        if region not in regions:
            regions.append(region)
        first_page = first_page if first_page is not None else best[1]
    return Located(first_page, tuple(regions), missing)


def locate_fields(
    fields: Mapping[str, XmlField], layout: Layout | None
) -> tuple[dict[str, Located], list[ValidationIssue]]:
    """Each field's page and regions, and an ``xml_pdf_mismatch`` warning per field the PDF
    does not print (module doc)."""
    if layout is None or not layout.pages:
        return {}, []
    located: dict[str, Located] = {}
    issues: list[ValidationIssue] = []
    for key, item in fields.items():
        found = locate_parts(item.reading.parts, item.captions, layout)
        located[key] = found
        if item.reading.verify and found.missing:
            shown = ", ".join(found.missing)[:200]
            issues.append(
                ValidationIssue(
                    rule=MISMATCH_RULE,
                    message=f"the XML value {shown!r} is not printed on the PDF next to "
                    f"{' / '.join(item.captions[:2]) or 'its caption'}",
                    keys=(key,),
                    severity="warning",
                )
            )
    return located, issues


# --------------------------------------------------------------------------- the result


# Catalogue types whose value is the text itself: kept as the XML spells it (the cleaners'
# OCR word-split repair would join "Low E thermal" into "Low Ethermal").
_TEXT_TYPES: Final = frozenset({"text"})


def to_field(item: XmlField, located: Located | None) -> ExtractedField:
    reading = item.reading
    field_ = ExtractedField.from_candidate(
        Candidate(
            key=item.key,
            raw_text=reading.raw,
            page=located.page if located else None,
            confidence=CONFIDENCE,
            source=SOURCE,
            regions=located.regions if located else (),
        )
    )
    if reading.num is not None and field_.value_num is None:
        field_ = field_.model_copy(update={"value_num": reading.num, "parse_error": None})
    spec = FIELD_BY_KEY.get(item.key)
    exact = re.sub(r"\s+", " ", reading.raw).strip()
    if spec is not None and spec.type in _TEXT_TYPES and field_.value_text != exact:
        field_ = field_.model_copy(update={"value_text": exact})
    return field_


def extract_mismo(
    xml: bytes,
    doc_id: str,
    *,
    layout: Layout | None,
    page_count: int = 0,
    review_threshold: float = 0.90,
    today: date | None = None,
    notes: Sequence[str] = (),
) -> ExtractionResult:
    """The catalogue fields of a UAD 3.6 XML, located on its PDF's ``layout`` (module doc)."""
    doc = Mismo36Document(parse_mismo(xml))
    read = read_document(doc)
    located, mismatches = locate_fields(read, layout)
    fields = {key: to_field(item, located.get(key)) for key, item in read.items()}
    derived = derive_fields(fields)
    area = fields.get("site.area_sqft")
    if area is not None:  # the derived site-area keys are the same printed box
        derived = {
            key: item.model_copy(update={"page": area.page, "regions": area.regions})
            for key, item in derived.items()
        }
    fields = {**fields, **derived}
    issues = [*mismatches, *validate_fields(fields, today=today)]
    gate = review_gate(fields, issues, threshold=review_threshold)
    all_notes = [*notes, *dict.fromkeys(doc.notes)]
    if layout is None:
        all_notes.append("no PDF layout: the XML values have no highlight regions")
    method = METHOD if layout is None else f"{METHOD}+azure_di"
    return ExtractionResult(
        doc_id=doc_id,
        method=method,
        page_count=page_count,
        fields=fields,
        issues=issues,
        overall_confidence=gate.overall_confidence,
        needs_review=gate.needs_review,
        review_reasons=list(gate.reasons),
        notes=all_notes,
        raw={
            "method": method,
            "doc_id": doc_id,
            "page_count": page_count,
            "notes": all_notes,
            "issues": [i.model_dump(mode="json") for i in issues],
            "review_reasons": list(gate.reasons),
            "review_threshold": review_threshold,
            "mismo": {"keys_read": sorted(read)},
        },
    )
