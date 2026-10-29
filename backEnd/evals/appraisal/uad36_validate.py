"""Validate MISMO 3.6 UAD (URAR) appraisal XML against the GSE specifications.

``python -m evals.appraisal.uad36_validate <xml>... [--json out.json] [--rules-report out.md]
[--today YYYY-MM-DD] [--data-dir DIR]``

The specifications are read at run time (nothing is copied into the repository) from
``BASIS_EVAL_DATA_DIR/appraisal/forms/`` (public Fannie Mae / Freddie Mac downloads):

* **A-1** ``appendix-a-1-urar-delivery-specification.xlsx`` (URAR Delivery Specification v1.4):
  sheets ``UAD Delivery Spec 1.4`` (one row per data point instance), ``UAD Enumerations`` and
  ``UAD ARC Roles``;
* **H-1** ``appendix-h-1-uad-compliance-rules-urar.xlsx`` (UAD Compliance Rules v1.5): sheets
  ``UAD Compliance Rules v1.5`` and ``Adjustments Cardinality``.

**The A-1 structure.** The delivery sheet is a pre-order walk of the XML: a container *open*
row (no data point, no attribute, a Cardinality such as ``0:1``), the container's attribute
and data point rows, nested containers, and a *close* row (no data point, no Cardinality).
:func:`build_spec` turns it into *blocks*, one per container instance A-1 describes (the
subject's PROPERTY and each comparable PROPERTY are blocks of one path, so are the
appraiser's and the client's PARTY), and refuses a sheet whose rows do not nest. A row's
Valuation Use Types are its marked columns (``Subject Property`` -> ``SubjectProperty``: the
header without spaces); a row with no mark, inside a PROPERTY or not, is valid for every use.

**Instance resolution.** Each container element is matched to the blocks it is *consistent*
with: same path, under a block its parent matched, every closed-enumeration data point and
attribute in its subtree holding an allowed value, no attribute or child that A-1 lists only
for other blocks of the path. A PROPERTY with ``ValuationUseType="SalesComparable"`` matches
only the sales comparable block, a PARTY whose role is ``Appraiser`` the appraiser block, a
SALES_HISTORY with an ``xlink:label`` the prior-sales block. When no block is consistent
(the element is invalid in another way, reported by another check) every block of the path
is used and the checks that need a known block skip the element.

**Checks** (each finding: severity, rule id, indexed xPath, value, message):

* ``A1.path``: an element whose path is no A-1 xPath (container or data point), or outside
  the MISMO namespace (A-1 defines no extension); its subtree is not checked further.
* ``A1.usetype``: inside a PROPERTY of Valuation Use Type *u*, a data point no A-1 row for
  its path marks for *u* (or leaves unmarked), a container no block for *u* has.
* ``A1.attribute``: an attribute A-1 does not list for the element. Also allowed:
  ``xsi:schemaLocation`` / ``xsi:noNamespaceSchemaLocation`` on MESSAGE (schema-location
  hints, not data). Namespace declarations are not attributes in the XML infoset.
* ``A1.enum`` / ``A1.version``: a value outside the Supported Enumerations of the rows for its
  path that apply to the property type, joined with the ``UAD Enumerations`` sheet rows of the
  same Unique IDs (``A1.version`` for MESSAGE/@MISMOReferenceModelIdentifier and the form and
  content version identifiers). Hand decisions: the sheets' use marks agree for every
  enumeration row, but two Boolean rows list only ``true`` in A-1 and both values in the
  enumerations sheet: their union is accepted. An Enumerated or Boolean row is a closed list;
  a String row with values is closed unless its Implementation Note says other values may be
  delivered ("Additional values may be provided", "may contain more than those listed",
  "free form text"). ``xlink:label`` / ``from`` / ``to`` values (``PROPERTY_n``) are a
  naming pattern, not checked.
* ``A1.format``: ``UAD Data Point Format Type`` / ``Format Details``: Amount and Numeric
  (``+9.2``: at most 9 integer and 2 decimal digits; ``+``: no minus sign, zero allowed;
  ``±``: optional minus; ``-``: a minus; digits only, no commas; a leading ``.75`` is an XML
  Schema decimal and is accepted), Boolean (``true``/``false``; a case error is a format
  finding, not also an enumeration one), Date (``YYYY-MM-DD``, ``YYYY-MM``, ``YYYY``, a real
  calendar date), Datetime (``YYYY-MM-DDThh:mm:ssZ``), String (at most N characters),
  Enumerated (the enumeration check); an empty data point. A value valid under any applicable
  row passes. A type or detail outside this list is one ``A1.format-unchecked`` info finding.
* ``A1.cardinality``: per resolved container and child name, more children than the child
  blocks allow in total, more children resolved to one block than its maximum, or fewer that
  can be that block than its minimum (minimum only when the parent resolved to one block).
* ``A1.repeat``: a data point (an element A-1 names as a data point, not as a container) that
  occurs more than once in one container; MISMO data points are single-valued (A-1 lists no
  cardinality for data points because they cannot repeat). Two roles of one party, for
  example, are two ``ROLE`` containers, not two ``PartyRoleType`` siblings.
* ``A1.arcrole``: each RELATIONSHIP has an ``xlink:arcrole`` that is an ``UAD ARC Roles``
  role (the URN A-1 gives the role's Unique ID, which must end in ``From_Link_To``),
  ``xlink:from`` / ``xlink:to`` naming an ``xlink:label`` in the message on an element named
  as the role's From / To; labels are unique.
* ``A1.required``: an ``R`` data point or attribute missing from an element whose every
  consistent block requires it; a ``CR`` one whose condition holds in every consistent
  block. Conditions use the H-1 grammar below plus ``Is Present`` / ``Is Not Present`` /
  ``X Exists``, in three-valued logic: a phrase that is not mechanical (a bare ``Exists``,
  "when the appraiser has the data"; ``Included In Sales Comparison Approach``; ``Photo Of
  Amenity Exists``) is unknown, a condition the row's Implementation Note narrows in words
  ("Deliver only when ...", "only if ...") is unknown when it holds, and an unknown
  requirement is counted as not checked, never as passed.
* **H-1** (rule id = the Message ID; Fatal -> error, Warning -> warning): every rule whose
  Rule Logic parses (:class:`_Parser`) and names only A-1 data; :func:`rules_registry` lists
  every rule with its status and why one is not implemented (``--rules-report``). UAD1758
  ("Please refer to the Adjustments Cardinality tab") is evaluated from that sheet: an
  adjustment type marked "cannot repeat" twice on a comparable.

**H-1 semantics** (hand decisions; the sheet is English):

* Scope: ``Subject`` / ``X Comparable[ #n]`` evaluates the rule on each PROPERTY of that
  Valuation Use Type; ``N/A``, and a rule that counts PROPERTY instances, on the message. A
  rule about the subject and a comparable together is a cross-property comparison: not
  implemented.
* Names: a data point resolves to the A-1 path(s) sharing the longest prefix with the rule's
  xPath (among rows marked for the rule's use), read from the nearest element enclosing that
  path; all instances found are the name's values. An ``N/A`` rule whose xPath is outside
  PROPERTY reads property data from the subject.
* Anchor: a trailing ``in a given instance of C [with ...]``, ``for the instance of C with``,
  ``for X = "v"`` (C = the repeating container holding X and the rule's xPath; none: the
  condition reaches the data through an arcrole, not implemented), ``in C container with``
  or ``For each C`` evaluates the rule on each such instance. Otherwise on each instance of
  the deepest repeating container that holds every data point the rule *compares* (presence
  tests do not anchor: a missing data point has no instance; a data point with one value per
  scope, such as PROPERTY_DETAIL/AttachmentType, does not move the anchor), else on the scope:
  "UtilityType = Electricity and SiteUtilityOwnershipType = NonPublic and ..." reads both from
  one SITE_UTILITY. The same phrases inside a sub-clause are existential over the instances.
* Counting (``there is no instance of C``, ``the number of instances of C``, ``not unique
  across``) counts inside the anchor when C is below it, else in the scope; ``with A = "v"
  and B = "w"`` keeps the ``and`` comparison in the ``with`` when A-1 places B inside C.
* Comparisons: ``X = "a" or "b"`` is a value list; ``X = v`` holds when some value is v, ``X
  <> v`` when values exist and none is v; numbers compare as decimals, ISO dates as text; a
  missing value satisfies no comparison. ``and`` / ``or`` apply left to right without
  precedence, as the rules read ("If A, or (B) and C" is "(A or B) and C").
* Value tests read as tests of a provided value: ``is not provided in YYYY-MM format``,
  ``is not provided between -90 and 90 with at least 5 digits of precision`` (absence is the
  sibling "is not provided" rule's). ``is in the future`` / ``more than N days before the
  current date`` use ``--today`` (default: today). The postal code phrase "5 digits, or 5
  digits, a hyphen, and 4 digits" is the one hand-coded format (:data:`_FORMAT_PHRASES`).
* A finding is reported once per anchor element.

Stdlib only (``zipfile`` + ``xml.etree``, through :func:`evals.appraisal.uad36_spec.read_sheet`
and the size / DOCTYPE / entity guard of :func:`basis.extraction.mismo36.parse_mismo`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import operator
import re
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Final
from xml.etree import ElementTree

from basis.extraction.mismo36 import InvalidMismo, parse_mismo
from evals.appraisal.uad36_spec import A1_ENUM_SHEET, A1_FILE, A1_SHEET, read_sheet
from evals.datasets import data_dir

A1_ARC_SHEET: Final = "UAD ARC Roles"
H1_FILE: Final = "appendix-h-1-uad-compliance-rules-urar.xlsx"
H1_SHEET: Final = "UAD Compliance Rules v1.5"
H1_ADJ_SHEET: Final = "Adjustments Cardinality"

MISMO_NS: Final = "http://www.mismo.org/residential/2009/schemas"
XLINK_NS: Final = "http://www.w3.org/1999/xlink"
XSI_NS: Final = "http://www.w3.org/2001/XMLSchema-instance"
_NS_PREFIX: Final = {XLINK_NS: "xlink", XSI_NS: "xsi"}
# Schema-location hints accepted on MESSAGE (module doc).
_ROOT_EXTRA_ATTRS: Final = frozenset({"xsi:schemaLocation", "xsi:noNamespaceSchemaLocation"})
# The A-1 Valuation Use Type columns (header text without spaces is the enumeration).
USE_COLUMNS: Final = (
    "Subject Property",
    "Land Comparable",
    "Property Analyzed Not Used",
    "Rental Comparable",
    "Gross Rent Multiplier Comparable",
    "Sales Comparable",
)
SUBJECT: Final = "SubjectProperty"
_PROPERTY_PATH: Final = ("VALUATION_ANALYSIS", "PROPERTIES", "PROPERTY")
# Attributes whose A-1 "enumeration" is a naming pattern (``PROPERTY_n``), not a value list.
_PATTERN_ATTRS: Final = frozenset({"xlink:label", "xlink:from", "xlink:to"})
# A-1 Implementation Notes that open a String enumeration (module doc: A1.enum).
_OPEN_ENUM: Final = re.compile(
    r"additional values may be provided|may contain more than those listed|"
    r"free form text may be delivered",
    re.IGNORECASE,
)
# An Implementation Note that narrows a CR condition in words ("Deliver only when providing
# data sources for cost approach"): a true condition is then unknown, not a requirement.
_NOTE_LIMITS: Final = re.compile(r"\bonly (?:when|if)\b", re.IGNORECASE)
_VERSION_NAMES: Final = frozenset(
    {
        "MISMOReferenceModelIdentifier",
        "DocumentFormIssuingEntityVersionIdentifier",
        "ValuationReportContentIdentifier",
    }
)


class SpecError(ValueError):
    pass


# =========================================================================== spec


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if line.strip()]


def _header(row: Sequence[str]) -> dict[str, int]:
    return {cell.split("\n")[0].strip(): i for i, cell in enumerate(row) if cell.strip()}


def _get(row: Sequence[str], index: Mapping[str, int], name: str) -> str:
    i = index.get(name)
    return row[i].strip() if i is not None and i < len(row) else ""


def _path(xpath: str) -> tuple[str, ...]:
    return tuple(seg for seg in xpath.strip().split("/") if seg)


def _split_values(text: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in text.split("|") if v.strip())


@dataclass(frozen=True)
class Cardinality:
    low: int
    high: int | None  # None: unbounded (A-1 "∞")

    @staticmethod
    def parse(text: str) -> Cardinality:
        low, _, high = text.strip().partition(":")
        return Cardinality(int(low), None if high.strip() in ("∞", "n", "N") else int(high))

    def __str__(self) -> str:
        return f"{self.low}:{'∞' if self.high is None else self.high}"


@dataclass(frozen=True)
class A1Row:
    """One A-1 data point or attribute row (container open/close rows become blocks)."""

    line: int  # sheet row number (1-based, header = 1)
    uid: str
    path: tuple[str, ...]  # the container path
    point: str  # "" for an attribute of the container
    attrs: tuple[str, ...]  # attribute names without "@" ("ValuationUseType", "xlink:label")
    enums: tuple[str, ...]
    attr_enums: Mapping[str, tuple[str, ...]]
    fmt: str
    details: str
    uses: frozenset[str]
    cond: str  # "R", "CR" or ""
    cond_details: str
    notes: str
    block: int

    @property
    def element_path(self) -> tuple[str, ...]:
        return (*self.path, self.point) if self.point else self.path

    @property
    def closed_enum(self) -> bool:
        """The data point's enumeration is the complete list of allowed values."""
        if not self.enums:
            return False
        if self.fmt in ("Enumerated", "Boolean"):
            return True
        return not _OPEN_ENUM.search(self.notes)


@dataclass
class Block:
    id: int
    path: tuple[str, ...]
    card: Cardinality
    parent: int | None
    line: int
    children: dict[str, list[int]] = field(default_factory=dict)  # child container -> blocks
    points: dict[str, list[A1Row]] = field(default_factory=dict)  # data point -> rows
    attrs: dict[str, list[A1Row]] = field(default_factory=dict)  # container attribute -> rows
    uses: frozenset[str] = frozenset()  # Valuation Use Types marked in the subtree

    @property
    def name(self) -> str:
        return self.path[-1]

    def relevant(self, use: str | None) -> bool:
        return use is None or not self.uses or use in self.uses


@dataclass(frozen=True)
class ArcRole:
    uid: str
    source: str  # From container (or data point)
    link: str
    target: str
    urn: str


@dataclass(frozen=True)
class H1Rule:
    uid: str
    element: str
    message_id: str
    text: str
    logic: str
    severity: str  # Fatal / Warning
    affected: str
    point: str
    xpath: str
    date_format: str
    number_format: str
    low: str
    high: str


@dataclass
class Spec:
    rows: list[A1Row]
    blocks: list[Block]
    enum_sheet: dict[str, list[tuple[str, str, frozenset[str]]]]
    arcroles: list[ArcRole]
    rules: list[H1Rule]
    adjustments: dict[str, tuple[str, str]]  # ComparableAdjustmentType -> (repeatability, use)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.blocks_by_path: dict[tuple[str, ...], list[Block]] = {}
        for block in self.blocks:
            self.blocks_by_path.setdefault(block.path, []).append(block)
        self.point_rows: dict[tuple[str, ...], list[A1Row]] = {}
        self.attr_rows: dict[tuple[tuple[str, ...], str], list[A1Row]] = {}
        for row in self.rows:
            if row.point:
                self.point_rows.setdefault(row.element_path, []).append(row)
            for attr in row.attrs:
                self.attr_rows.setdefault((row.element_path, attr), []).append(row)
        self.attrs_by_path: dict[tuple[str, ...], set[str]] = {}
        for path, attr in self.attr_rows:
            self.attrs_by_path.setdefault(path, set()).add(attr)
        self.containers: set[str] = {path[-1] for path in self.blocks_by_path}
        self.point_names: set[str] = {path[-1] for path in self.point_rows}
        self.attr_names: set[str] = {attr for _, attr in self.attr_rows}
        self.arc_by_urn: dict[str, ArcRole] = {arc.urn: arc for arc in self.arcroles}
        self.use_types: tuple[str, ...] = tuple(c.replace(" ", "") for c in USE_COLUMNS)

    # ----------------------------------------------------------------- name -> paths

    def name_paths(self, name: str) -> list[tuple[tuple[str, ...], list[A1Row]]]:
        """``[(path, rows)]`` for a data point (path ends with the point), an ``@attribute``
        (the path of the element that carries it) or a container (rows empty). A qualified
        name ``A/B`` keeps the paths ending with ``A/B``."""
        *qualifier, last = name.split("/")
        out: list[tuple[tuple[str, ...], list[A1Row]]] = []
        if last.startswith("@"):
            attr = last[1:]
            for (path, a), rows in self.attr_rows.items():
                if a == attr:
                    out.append((path, rows))
        elif last in self.point_names:
            out.extend((p, rows) for p, rows in self.point_rows.items() if p[-1] == last)
        elif last in self.containers:
            out.extend((p, []) for p in self.blocks_by_path if p[-1] == last)
        if qualifier:
            tail = tuple(qualifier)
            out = [(p, r) for p, r in out if p[-1 - len(tail) : -1] == tail]
        return out

    def knows(self, name: str) -> bool:
        return bool(self.name_paths(name))


def _uses(row: Sequence[str], index: Mapping[str, int]) -> frozenset[str]:
    return frozenset(
        column.replace(" ", "") for column in USE_COLUMNS if _get(row, index, column).lower() == "x"
    )


def _attr_enums(attrs: Sequence[str], text: str) -> dict[str, tuple[str, ...]]:
    if not attrs:
        return {}
    if len(attrs) == 1:
        return {attrs[0]: _split_values(text)}
    out: dict[str, tuple[str, ...]] = {}
    for line in _lines(text):  # "@ValuationUseType - SalesComparable"
        name, sep, value = line.partition(" - ")
        if sep:
            out[name.strip().lstrip("@")] = _split_values(value)
    return out


def build_spec(sheets: Mapping[str, Sequence[Sequence[str]]]) -> Spec:
    """The validator's spec from the sheets' rows (:func:`read_sheet` output, header first).
    Required: :data:`A1_SHEET`; optional: the enumeration, ARC role, H-1 and adjustment
    sheets (tests build small fabricated sheets through this seam)."""
    rows_in = sheets[A1_SHEET]
    index = _header(rows_in[0])
    rows: list[A1Row] = []
    blocks: list[Block] = []
    stack: list[Block] = []
    for number, raw in enumerate(rows_in[1:], start=2):
        path = _path(_get(raw, index, "MISMO xPath"))
        if not path:
            continue
        point = _get(raw, index, "MISMO Data Point Name")
        attrs = tuple(a.lstrip("@") for a in _lines(_get(raw, index, "MISMO Attribute Name")))
        card = _get(raw, index, "Cardinality")
        if not point and not attrs:
            if card:  # open
                if stack and stack[-1].path != path[:-1]:
                    raise SpecError(f"A-1 row {number}: {'/'.join(path)} opens outside its parent")
                block = Block(
                    id=len(blocks),
                    path=path,
                    card=Cardinality.parse(card),
                    parent=stack[-1].id if stack else None,
                    line=number,
                )
                if stack:
                    stack[-1].children.setdefault(path[-1], []).append(block.id)
                elif path != ("MESSAGE",):
                    raise SpecError(f"A-1 row {number}: the first container is not MESSAGE")
                blocks.append(block)
                stack.append(block)
            else:  # close
                if not stack or stack[-1].path != path:
                    raise SpecError(f"A-1 row {number}: {'/'.join(path)} closes out of order")
                stack.pop()
            continue
        if not stack or stack[-1].path != path:
            raise SpecError(f"A-1 row {number}: {'/'.join(path)} is outside its container")
        current = stack[-1]
        row = A1Row(
            line=number,
            uid=_get(raw, index, "Unique ID"),
            path=path,
            point=point,
            attrs=attrs,
            enums=_split_values(_get(raw, index, "Supported Data Point Enumerations")),
            attr_enums=_attr_enums(attrs, _get(raw, index, "Supported Attribute Enumerations")),
            fmt=_get(raw, index, "UAD Data Point Format Type"),
            details=_get(raw, index, "UAD Data Point Format Details"),
            uses=_uses(raw, index),
            cond=_get(raw, index, "Conditionality"),
            cond_details=" ".join(_lines(_get(raw, index, "Conditionality Details"))),
            notes=" ".join(_lines(_get(raw, index, "Implementation Notes"))),
            block=current.id,
        )
        rows.append(row)
        if point:
            current.points.setdefault(point, []).append(row)
        else:
            for attr in attrs:
                current.attrs.setdefault(attr, []).append(row)
    if stack:
        raise SpecError(f"A-1: {'/'.join(stack[-1].path)} is never closed")
    # a block's uses: the marks in its subtree (children were appended after their parent)
    for block in reversed(blocks):
        marks: set[str] = set()
        for group in (*block.points.values(), *block.attrs.values()):
            for row in group:
                marks |= row.uses
        for ids in block.children.values():
            for child in ids:
                marks |= blocks[child].uses
        block.uses = frozenset(marks)
    spec = Spec(
        rows=rows,
        blocks=blocks,
        enum_sheet=_load_enum_sheet(sheets.get(A1_ENUM_SHEET)),
        arcroles=_load_arcroles(sheets.get(A1_ARC_SHEET), rows),
        rules=_load_rules(sheets.get(H1_SHEET)),
        adjustments=_load_adjustments(sheets.get(H1_ADJ_SHEET)),
    )
    vut = {v for (_, a), rs in spec.attr_rows.items() if a == "ValuationUseType" for r in rs
           for v in r.attr_enums.get(a, ())}  # fmt: skip
    missing = [u for u in spec.use_types if vut and u not in vut]
    if missing:
        spec.notes.append(f"A-1 use columns with no ValuationUseType enumeration: {missing}")
    return spec


def _load_enum_sheet(
    rows: Sequence[Sequence[str]] | None,
) -> dict[str, list[tuple[str, str, frozenset[str]]]]:
    out: dict[str, list[tuple[str, str, frozenset[str]]]] = {}
    if not rows:
        return out
    index = _header(rows[0])
    for raw in rows[1:]:
        uid = _get(raw, index, "Unique ID")
        value = _get(raw, index, "Supported Enumerations")
        if uid and value:
            name = _get(raw, index, "MISMO Data Point / Attribute Name").lstrip("@")
            out.setdefault(uid, []).append((name, value, _uses(raw, index)))
    return out


def _load_arcroles(rows: Sequence[Sequence[str]] | None, a1: Sequence[A1Row]) -> list[ArcRole]:
    if not rows:
        return []
    start = next((i for i, r in enumerate(rows) if r and r[0].strip() == "Unique ID"), None)
    if start is None:
        raise SpecError(f"{A1_ARC_SHEET}: no header row")
    index = _header(rows[start])
    urns = {
        row.uid: row.attr_enums.get("xlink:arcrole", ())
        for row in a1
        if "xlink:arcrole" in row.attrs
    }
    out: list[ArcRole] = []
    for raw in rows[start + 1 :]:
        uid = _get(raw, index, "Unique ID")
        if not uid:
            continue
        source, link, target = (_get(raw, index, n) for n in ("From", "Link", "To"))
        urn = next(iter(urns.get(uid, ())), "")
        if not urn.endswith(f"/{source}_{link}_{target}"):
            raise SpecError(f"{A1_ARC_SHEET} {uid}: A-1 arcrole {urn!r} is not {source} {link}")
        out.append(ArcRole(uid, source, link, target, urn))
    return out


def _load_rules(rows: Sequence[Sequence[str]] | None) -> list[H1Rule]:
    if not rows:
        return []
    index = _header(rows[0])
    out: list[H1Rule] = []
    for raw in rows[1:]:
        message_id = _get(raw, index, "Message ID")
        if not message_id:
            continue
        out.append(
            H1Rule(
                uid=_get(raw, index, "Unique ID"),
                element=_get(raw, index, "Primary Data Element"),
                message_id=message_id,
                text=" ".join(_lines(_get(raw, index, "Message Text"))),
                logic=_get(raw, index, "Rule Logic"),
                severity=_get(raw, index, "Severity"),
                affected=_get(raw, index, "Property Affected"),
                point=_get(raw, index, "Data Point Name / Value"),
                xpath=_get(raw, index, "xPath"),
                date_format=_get(raw, index, "Date Format"),
                number_format=_get(raw, index, "Number Format"),
                low=_get(raw, index, "Min Value"),
                high=_get(raw, index, "Max Value"),
            )
        )
    return out


def _load_adjustments(rows: Sequence[Sequence[str]] | None) -> dict[str, tuple[str, str]]:
    if not rows:
        return {}
    index = _header(rows[0])
    out: dict[str, tuple[str, str]] = {}
    for raw in rows[1:]:
        kind = _get(raw, index, "ComparableAdjustmentType")
        if kind:
            out[kind] = (
                _get(raw, index, "Adjustment Repeatability"),
                _get(raw, index, "ValuationUseType"),
            )
    return out


def load_spec(directory: str | Path | None = None) -> Spec:
    forms = data_dir(directory) / "appraisal" / "forms"
    a1, h1 = forms / A1_FILE, forms / H1_FILE
    sheets = {
        A1_SHEET: read_sheet(a1, A1_SHEET),
        A1_ENUM_SHEET: read_sheet(a1, A1_ENUM_SHEET),
        A1_ARC_SHEET: read_sheet(a1, A1_ARC_SHEET),
        H1_SHEET: read_sheet(h1, H1_SHEET),
        H1_ADJ_SHEET: read_sheet(h1, H1_ADJ_SHEET),
    }
    return build_spec(sheets)


# =========================================================================== document


class XNode:
    """An element of the message with its MISMO path and indexed xPath."""

    __slots__ = ("name", "parent", "children", "text", "attrs", "path", "xpath", "depth", "mismo")

    def __init__(self, elem: ElementTree.Element, parent: XNode | None, xpath: str) -> None:
        tag = elem.tag
        ns, _, local = tag[1:].partition("}") if tag.startswith("{") else ("", "", tag)
        self.mismo = ns == MISMO_NS
        self.name = local if self.mismo else (f"{{{ns}}}{local}" if ns else local)
        self.parent = parent
        self.children: list[XNode] = []
        self.text = (elem.text or "").strip()
        self.attrs: dict[str, str] = {}
        for key, value in elem.attrib.items():
            if key.startswith("{"):
                kns, _, klocal = key[1:].partition("}")
                prefix = _NS_PREFIX.get(kns)
                self.attrs[f"{prefix}:{klocal}" if prefix else key] = value
            else:
                self.attrs[key] = value
        self.path: tuple[str, ...] = (*parent.path, self.name) if parent else (self.name,)
        self.depth = len(self.path)
        self.xpath = xpath

    def ancestor(self, depth: int) -> XNode:
        node: XNode = self
        while node.depth > depth and node.parent is not None:
            node = node.parent
        return node

    def find(self, name: str) -> list[XNode]:
        return [c for c in self.children if c.name == name]

    def iter(self) -> Iterator[XNode]:
        yield self
        for child in self.children:
            yield from child.iter()


class Document:
    def __init__(self, root: ElementTree.Element) -> None:
        self.root = self._build(root, None, root.tag.rsplit("}", 1)[-1])
        self.nodes = list(self.root.iter())
        self.labels: dict[str, list[XNode]] = {}
        for node in self.nodes:
            label = node.attrs.get("xlink:label")
            if label is not None:
                self.labels.setdefault(label, []).append(node)

    def _build(self, elem: ElementTree.Element, parent: XNode | None, xpath: str) -> XNode:
        node = XNode(elem, parent, xpath)
        counts: dict[str, int] = {}
        for child in elem:
            if isinstance(child.tag, str):
                counts[child.tag] = counts.get(child.tag, 0) + 1
        seen: dict[str, int] = {}
        for child in elem:
            if not isinstance(child.tag, str):  # comments, processing instructions
                continue
            seen[child.tag] = seen.get(child.tag, 0) + 1
            local = child.tag.rsplit("}", 1)[-1]
            suffix = f"[{seen[child.tag]}]" if counts[child.tag] > 1 else ""
            node.children.append(self._build(child, node, f"{xpath}/{local}{suffix}"))
        return node

    @staticmethod
    def parse(data: bytes) -> Document:
        return Document(parse_mismo(data))


def use_of(node: XNode) -> str | None:
    """The Valuation Use Type of the PROPERTY the node is in (None outside one)."""
    current: XNode | None = node
    while current is not None:
        if current.name == "PROPERTY" and "ValuationUseType" in current.attrs:
            return current.attrs["ValuationUseType"]
        current = current.parent
    return None


def _property_of(node: XNode) -> XNode | None:
    current: XNode | None = node
    while current is not None:
        if current.path[-3:] == _PROPERTY_PATH:
            return current
        current = current.parent
    return None


# =========================================================================== findings


@dataclass(frozen=True)
class Finding:
    severity: str  # error / warning / info
    rule: str
    xpath: str
    value: str
    message: str
    h1_severity: str = ""

    def as_dict(self) -> dict[str, str]:
        out = {
            "severity": self.severity,
            "rule": self.rule,
            "xpath": self.xpath,
            "value": self.value,
            "message": self.message,
        }
        if self.h1_severity:
            out["h1_severity"] = self.h1_severity
        return out


@dataclass
class Result:
    path: str
    findings: list[Finding]
    not_checked: dict[str, int]  # requirement / rule kind -> count

    @property
    def errors(self) -> int:
        return sum(f.severity == "error" for f in self.findings)

    @property
    def warnings(self) -> int:
        return sum(f.severity == "warning" for f in self.findings)


# =========================================================================== formats

_DATE_FORMATS: Final = {
    "YYYY-MM-DD": (re.compile(r"\d{4}-\d{2}-\d{2}"), "%Y-%m-%d"),
    "YYYY-MM": (re.compile(r"\d{4}-\d{2}"), "%Y-%m"),
    "YYYY": (re.compile(r"\d{4}"), "%Y"),
}
_DATETIME: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_PRECISION: Final = re.compile(r"([+±-])?(\d+)\.(\d+)")


def format_supported(fmt: str, details: str) -> bool:
    if fmt in ("Enumerated", "Boolean", "") or (fmt == "Datetime"):
        return True
    if fmt == "Date":
        return details in _DATE_FORMATS
    if fmt in ("Amount", "Numeric"):
        return _PRECISION.fullmatch(details) is not None
    if fmt == "String":
        return details.isdigit()
    return False


def format_error(value: str, fmt: str, details: str) -> str | None:
    """Why ``value`` does not have the A-1 format (None when it does or is not checked)."""
    if fmt == "Boolean":
        return None if value in ("true", "false") else "not true/false (lower case)"
    if fmt == "Datetime":
        if not _DATETIME.fullmatch(value):
            return "not YYYY-MM-DDThh:mm:ssZ"
        try:
            dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return "not a real date/time"
        return None
    if fmt == "Date":
        spec = _DATE_FORMATS.get(details)
        if spec is None:
            return None
        pattern, parse = spec
        if not pattern.fullmatch(value):
            return f"not {details}"
        try:
            dt.datetime.strptime(value, parse)
        except ValueError:
            return f"not a real date ({details})"
        return None
    if fmt in ("Amount", "Numeric"):
        match = _PRECISION.fullmatch(details)
        if match is None:
            return None
        sign, whole, frac = match.group(1) or "+", int(match.group(2)), int(match.group(3))
        number = re.fullmatch(r"(-?)(\d*)(?:\.(\d+))?", value)
        if number is None or not (number.group(2) or number.group(3)):
            return f"not a number of format {details} (digits, optional '.', no commas)"
        if number.group(1) and sign == "+":
            return f"negative, format {details} requires a positive value"
        if not number.group(1) and sign == "-" and Decimal(value) != 0:
            return f"positive, format {details} requires a negative value"
        if len(number.group(2).lstrip("0") or "0") > whole:
            return f"more than {whole} integer digits (format {details})"
        if len(number.group(3) or "") > frac:
            return f"more than {frac} decimal places (format {details})"
        return None
    if fmt == "String" and details.isdigit():
        if len(value) > int(details):
            return f"{len(value)} characters, more than {details}"
        return None
    return None


# =========================================================================== A-1 checks


class _Checker:
    def __init__(self, spec: Spec, doc: Document, today: dt.date) -> None:
        self.spec = spec
        self.doc = doc
        self.today = today
        self.findings: list[Finding] = []
        self.not_checked: dict[str, int] = {}
        self.candidates: dict[int, list[Block]] = {}
        self.resolved: dict[int, bool] = {}
        self._consistent: dict[tuple[int, int], bool] = {}

    def add(self, severity: str, rule: str, node: XNode, value: str, message: str) -> None:
        self.findings.append(Finding(severity, rule, node.xpath, value, message))

    # ----------------------------------------------------------------- resolution

    def consistent(self, node: XNode, block: Block) -> bool:
        key = (id(node), block.id)
        cached = self._consistent.get(key)
        if cached is not None:
            return cached
        self._consistent[key] = True  # recursion guard (trees have no cycles)
        result = self._consistent_uncached(node, block)
        self._consistent[key] = result
        return result

    def _consistent_uncached(self, node: XNode, block: Block) -> bool:
        if node.name != block.name:
            return False
        path_blocks = self.spec.blocks_by_path.get(block.path, [])
        for attr, value in node.attrs.items():
            rows = block.attrs.get(attr)
            if not rows and any(attr in other.attrs for other in path_blocks):
                return False
            if rows and attr not in _PATTERN_ATTRS:
                allowed = {v for r in rows for v in r.attr_enums.get(attr, ())}
                if allowed and value not in allowed:
                    return False
        for child in node.children:
            child_path = (*block.path, child.name)
            if child.name in block.points:
                rows = block.points[child.name]
                if rows and all(r.closed_enum for r in rows):
                    allowed = {v for r in rows for v in r.enums}
                    if child.text not in allowed:
                        return False
                continue
            if child.name in block.children:
                if not any(
                    self.consistent(child, self.spec.blocks[b]) for b in block.children[child.name]
                ):
                    return False
                continue
            # a name some other block of this path has: this element is not this block
            if (
                any(
                    child.name in other.points or child.name in other.children
                    for other in path_blocks
                )
                or child_path in self.spec.point_rows
            ):
                return False
        return True

    def resolve(self, node: XNode, parents: Sequence[Block] | None) -> None:
        if parents is None:
            options = self.spec.blocks_by_path.get(node.path, [])
        else:
            options = [self.spec.blocks[b] for p in parents for b in p.children.get(node.name, [])]
            if not options:
                options = self.spec.blocks_by_path.get(node.path, [])
        matched = [b for b in options if self.consistent(node, b)]
        self.resolved[id(node)] = bool(matched)
        chosen = matched or list(self.spec.blocks_by_path.get(node.path, []))
        self.candidates[id(node)] = chosen
        for child in node.children:
            if (*node.path, child.name) in self.spec.blocks_by_path:
                self.resolve(child, chosen)

    # ----------------------------------------------------------------- checks

    def run(self) -> None:
        root = self.doc.root
        if not root.mismo:
            self.add("error", "A1.path", root, "", "root is not a MISMO MESSAGE")
            return
        self.resolve(root, None)
        unsupported: set[tuple[str, str]] = set()
        for row in self.spec.rows:
            if row.point and not format_supported(row.fmt, row.details):
                unsupported.add((row.fmt, row.details))
        for fmt, details in sorted(unsupported):
            self.findings.append(
                Finding(
                    "info",
                    "A1.format-unchecked",
                    "",
                    f"{fmt} {details}".strip(),
                    "A-1 format type/details the validator cannot check",
                )
            )
        self._walk(root)
        self._check_links()

    def _walk(self, node: XNode) -> None:
        spec = self.spec
        is_container = node.path in spec.blocks_by_path
        is_point = node.path in spec.point_rows
        if not node.mismo or not (is_container or is_point):
            self.add(
                "error",
                "A1.path",
                node,
                node.text if not node.children else "",
                f"{'/'.join(node.path)} is not an A-1 xPath",
            )
            return
        use = use_of(node)
        self._check_use(node, use, is_container)
        self._check_attrs(node, use)
        if is_point and not node.children:
            self._check_value(node, use)
        if is_container:
            self._check_cardinality(node)
            self._check_required(node, use)
        for child in node.children:
            self._walk(child)

    def _check_use(self, node: XNode, use: str | None, is_container: bool) -> None:
        if use is None or _property_of(node) is None:
            return
        if is_container:
            if any(b.relevant(use) for b in self.spec.blocks_by_path[node.path]):
                return
        else:
            rows = self.spec.point_rows[node.path]
            if any(not r.uses or use in r.uses for r in rows):
                return
        self.add(
            "error",
            "A1.usetype",
            node,
            node.text if not node.children else "",
            f"{node.name} is not an A-1 data point of a PROPERTY with ValuationUseType {use}",
        )

    def _check_attrs(self, node: XNode, use: str | None) -> None:
        allowed = self.spec.attrs_by_path.get(node.path, set())
        for attr, value in node.attrs.items():
            if attr in _ROOT_EXTRA_ATTRS and node.parent is None:
                continue
            if attr not in allowed:
                self.add(
                    "error",
                    "A1.attribute",
                    node,
                    value,
                    f"attribute {attr} is not an A-1 attribute of {node.name}",
                )
                continue
            if attr in _PATTERN_ATTRS:
                continue
            rows = [
                r
                for r in self.spec.attr_rows[(node.path, attr)]
                if use is None or not r.uses or use in r.uses
            ] or self.spec.attr_rows[(node.path, attr)]
            values = {v for r in rows for v in r.attr_enums.get(attr, ())}
            values |= self._sheet_values(rows, attr)
            if values and value not in values:
                rule = "A1.version" if attr in _VERSION_NAMES else "A1.enum"
                if attr == "xlink:arcrole":
                    rule = "A1.arcrole"
                self.add(
                    "error",
                    rule,
                    node,
                    value,
                    f"@{attr} is not one of the A-1 values {sorted(values)[:8]}",
                )

    def _sheet_values(self, rows: Iterable[A1Row], name: str) -> set[str]:
        out: set[str] = set()
        for row in rows:
            for point, value, _ in self.spec.enum_sheet.get(row.uid, ()):
                if point == name:
                    out.add(value)
        return out

    def _check_value(self, node: XNode, use: str | None) -> None:
        all_rows = self.spec.point_rows[node.path]
        rows = [r for r in all_rows if use is None or not r.uses or use in r.uses] or all_rows
        value = node.text
        if value == "":
            self.add("error", "A1.format", node, "", f"{node.name} is empty")
            return
        closed = [r for r in rows if r.closed_enum]
        boolean_case = all(r.fmt == "Boolean" for r in rows) and value not in ("true", "false")
        if closed and len(closed) == len(rows) and not boolean_case:
            allowed = {v for r in closed for v in r.enums} | self._sheet_values(closed, node.name)
            if value not in allowed:
                rule = "A1.version" if node.name in _VERSION_NAMES else "A1.enum"
                shown = sorted(allowed)
                self.add(
                    "error",
                    rule,
                    node,
                    value,
                    f"{node.name} is not an A-1 enumeration"
                    f" ({len(shown)} allowed: {', '.join(shown[:10])}"
                    f"{', ...' if len(shown) > 10 else ''})",
                )
        formats = {(r.fmt, r.details) for r in rows if r.fmt not in ("Enumerated", "")}
        if not formats:
            return
        problems = [format_error(value, fmt, details) for fmt, details in sorted(formats)]
        if all(p is not None for p in problems):
            expected = "; ".join(f"{f} {d}".strip() for f, d in sorted(formats))
            self.add(
                "error",
                "A1.format",
                node,
                value,
                f"{node.name}: {problems[0]} (A-1 format: {expected})",
            )

    def _check_cardinality(self, node: XNode) -> None:
        # A data point (an element A-1 lists as a data point, not a container) occurs at most once
        # in its container: MISMO data points are single-valued; repeats belong in repeated
        # containers (two ROLEs, two AMENITYs), never as two siblings in one.
        seen: dict[str, int] = {}
        for child in node.children:
            path = (*node.path, child.name)
            if (
                not child.children
                and path not in self.spec.blocks_by_path
                and self.spec.knows(child.name)
            ):
                seen[child.name] = seen.get(child.name, 0) + 1
        for name, count in sorted(seen.items()):
            if count > 1:
                self.add(
                    "error",
                    "A1.repeat",
                    node,
                    str(count),
                    f"data point {name} occurs {count} times in one {node.name};"
                    " a data point is single-valued",
                )
        if not self.resolved.get(id(node)):
            return
        parents = self.candidates[id(node)]
        groups: dict[str, list[XNode]] = {}
        for child in node.children:
            if (*node.path, child.name) in self.spec.blocks_by_path:
                groups.setdefault(child.name, []).append(child)
        names = set(groups)
        if len(parents) == 1:
            names |= set(parents[0].children)
        for name in sorted(names):
            children = groups.get(name, [])
            if len(parents) == 1:
                kids = [self.spec.blocks[b] for b in parents[0].children.get(name, [])]
                if not kids:
                    continue
                total_max = (
                    None
                    if any(k.card.high is None for k in kids)
                    else sum(k.card.high or 0 for k in kids)
                )
                if total_max is not None and len(children) > total_max:
                    self.add(
                        "error",
                        "A1.cardinality",
                        node,
                        str(len(children)),
                        f"{len(children)} {name} in {node.name}; A-1 allows at most {total_max}"
                        f" ({', '.join(str(k.card) for k in kids)})",
                    )
                    continue
                for kid in kids:
                    sure = [c for c in children if self._only(c, kid)]
                    possible = [c for c in children if self._maybe(c, kid)]
                    if kid.card.high is not None and len(sure) > kid.card.high:
                        self.add(
                            "error",
                            "A1.cardinality",
                            node,
                            str(len(sure)),
                            f"{len(sure)} {name} of the A-1 instance at row {kid.line}"
                            f" ({kid.card}) in {node.name}",
                        )
                    if kid.card.low > len(possible):
                        self.add(
                            "error",
                            "A1.cardinality",
                            node,
                            str(len(possible)),
                            f"{name} (A-1 row {kid.line}, cardinality {kid.card}) has"
                            f" {len(possible)} instance(s) in {node.name}",
                        )
            else:
                caps: list[int | None] = []
                for parent in parents:
                    kids = [self.spec.blocks[b] for b in parent.children.get(name, [])]
                    if any(k.card.high is None for k in kids):
                        caps.append(None)
                    else:
                        caps.append(sum(k.card.high or 0 for k in kids))
                if caps and None not in caps:
                    cap = max(c for c in caps if c is not None)
                    if len(children) > cap:
                        self.add(
                            "error",
                            "A1.cardinality",
                            node,
                            str(len(children)),
                            f"{len(children)} {name} in {node.name}; A-1 allows at most {cap}",
                        )

    def _only(self, node: XNode, block: Block) -> bool:
        cands = self.candidates.get(id(node), [])
        return self.resolved.get(id(node), False) and [b.id for b in cands] == [block.id]

    def _maybe(self, node: XNode, block: Block) -> bool:
        if not self.resolved.get(id(node), False):
            return True
        return any(b.id == block.id for b in self.candidates.get(id(node), []))

    # ----------------------------------------------------------------- required

    def _check_required(self, node: XNode, use: str | None) -> None:
        blocks = self.candidates.get(id(node), [])
        if not blocks:
            return
        names: set[tuple[str, bool]] = set()  # (name, is attribute)
        for block in blocks:
            names |= {(p, False) for p in block.points}
            names |= {(a, True) for a in block.attrs}
        for name, is_attr in sorted(names):
            present = (
                name in node.attrs
                if is_attr
                else any(c.name == name and (c.text or c.children) for c in node.children)
            )
            if present:
                continue
            verdicts: list[bool | None] = []
            rows_seen: list[A1Row] = []
            for block in blocks:
                rows = (block.attrs if is_attr else block.points).get(name, [])
                rows = [r for r in rows if use is None or not r.uses or use in r.uses]
                verdict: bool | None = False
                for row in rows:
                    rows_seen.append(row)
                    v = self._requires(node, row, use)
                    if v is True:
                        verdict = True
                        break
                    if v is None:
                        verdict = None
                verdicts.append(verdict)
            if verdicts and all(v is True for v in verdicts):
                row = rows_seen[0]
                why = "required (R)" if row.cond == "R" else f"required: {row.cond_details}"
                self.add(
                    "error",
                    "A1.required",
                    node,
                    "",
                    f"{'@' if is_attr else ''}{name} missing in {node.name}: {why}"
                    f" (A-1 {row.uid or f'row {row.line}'})",
                )
            elif verdicts and all(v is not False for v in verdicts):
                self.not_checked["A1.required (CR condition not mechanical)"] = (
                    self.not_checked.get("A1.required (CR condition not mechanical)", 0) + 1
                )

    def _requires(self, node: XNode, row: A1Row, use: str | None) -> bool | None:
        if row.cond == "R":
            return True
        if row.cond != "CR" or not row.cond_details:
            return False
        parsed = parse_condition(row.cond_details, self.spec)
        if parsed is None:
            return None
        scope = _property_of(node) or self.doc.root
        ctx = _Ctx(self, home=row.path, use=use, scope=scope, today=self.today)
        verdict = _eval(parsed, ctx, node)
        if verdict is True and _NOTE_LIMITS.search(row.notes):
            return None  # the Implementation Note narrows the condition in words
        return verdict

    # ----------------------------------------------------------------- xlink

    def _check_links(self) -> None:
        for label, nodes in self.doc.labels.items():
            if len(nodes) > 1:
                for node in nodes[1:]:
                    self.add("error", "A1.arcrole", node, label, "xlink:label is not unique")
        for node in self.doc.nodes:
            if node.name != "RELATIONSHIP":
                continue
            urn = node.attrs.get("xlink:arcrole", "")
            if not urn:
                self.add("error", "A1.arcrole", node, "", "RELATIONSHIP without xlink:arcrole")
            role = self.spec.arc_by_urn.get(urn)
            if role is None and urn and self.spec.arcroles:
                # A1.attribute/A1.arcrole already reported the value when A-1 lists roles
                if not any(f.xpath == node.xpath and f.rule == "A1.arcrole" for f in self.findings):
                    self.add("error", "A1.arcrole", node, urn, "not a UAD ARC Roles arcrole")
            for end, expected in (
                ("xlink:from", role.source if role else None),
                ("xlink:to", role.target if role else None),
            ):
                ref = node.attrs.get(end)
                if ref is None:
                    self.add("error", "A1.arcrole", node, "", f"RELATIONSHIP without {end}")
                    continue
                targets = self.doc.labels.get(ref, [])
                if not targets:
                    self.add(
                        "error",
                        "A1.arcrole",
                        node,
                        ref,
                        f"{end} names no xlink:label in the message",
                    )
                    continue
                if expected and targets[0].name != expected:
                    self.add(
                        "error",
                        "A1.arcrole",
                        node,
                        ref,
                        f"{end} labels a {targets[0].name}; the arcrole links {expected}",
                    )


# =========================================================================== expressions


@dataclass(frozen=True)
class Cond:
    """``name op values`` (values: quoted strings / numbers) or ``name op other``."""

    name: str
    op: str  # = <> < > <= >=
    values: tuple[str, ...] = ()
    other: str = ""
    also: tuple[Cond, ...] = ()  # further comparisons that must hold too (``with A and B``)

    def names(self) -> Iterator[str]:
        yield self.name
        if self.other:
            yield self.other
        for cond in self.also:
            yield from cond.names()


@dataclass(frozen=True)
class Scope:
    kind: str  # instance / for / service / one_or_more / each
    container: str  # "" when computed from the condition (``for PartyRoleType = ...``)
    cond: Cond | None = None


@dataclass(frozen=True)
class Node:
    """An expression node. ``kind`` and the fields it uses:

    and/or: items; provided/missing: name; cmp: cond; count: container, cond, op, number;
    count_ref: name, op, container, cond; sum_ref: name, op, other (summed), container;
    unique: name, container, cond; date_format: name; future: name; days_before: name,
    number; length: name, number; pattern: name, regex; link: urn, source, target, cond;
    scoped: items[0], scope; unknown: text (A-1 conditions only).
    """

    kind: str
    items: tuple[Node, ...] = ()
    name: str = ""
    cond: Cond | None = None
    container: str = ""
    op: str = ""
    number: Decimal = Decimal(0)
    other: str = ""
    urn: str = ""
    source: str = ""
    target: str = ""
    regex: str = ""
    text: str = ""
    scope: Scope | None = None

    def names(self) -> Iterator[str]:
        for item in self.items:
            yield from item.names()
        for name in (self.name, self.other, self.container, self.source, self.target):
            if name:
                yield name
        for cond in (self.cond, self.scope.cond if self.scope else None):
            if cond is not None:
                yield from cond.names()
        if self.scope and self.scope.container:
            yield self.scope.container


class ParseError(ValueError):
    pass


# Phrase -> regular expression for the H-1 "required format" wording (hand table).
_FORMAT_PHRASES: Final = {
    "5 digits, or 5 digits, a hyphen, and 4 digits": r"\d{5}(-\d{4})?",
}
_NAME: Final = r"@?[A-Za-z][A-Za-z0-9_:]*(?:/[A-Za-z][A-Za-z0-9_:]*)*"
_CONTAINER: Final = r"[A-Z][A-Z0-9_]*(?:/[A-Z][A-Z0-9_]*)*"
_NUMBER: Final = r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?"


def _normalize(text: str) -> str:
    s = text.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    s = re.sub(r"\s+", " ", s).strip().rstrip(".").strip()
    s = re.sub(r"'([A-Za-z]+)'", r'"\1"', s)
    s = re.sub(r"\b(AND|And)\b", "and", s)
    s = re.sub(r"\b(OR|Or)\b", "or", s)
    s = re.sub(r"^(IF|If|if)\s+", "", s)
    s = re.sub(r"\b(and|or),?\s+If\s+", r"\1 ", s)  # "..., and If (...)": a stray If
    s = re.sub(r"\bin in\b", "in", s)
    s = re.sub(r"\bin a given instance (?!of )([A-Z])", r"in a given instance of \1", s)
    s = re.sub(r'\bis "', '= "', s)
    s = re.sub(r'\bis not = "', '<> "', s)
    s = re.sub(r'\bis not "', '<> "', s)
    s = re.sub(r"\bdoes not equal\b", "<>", s)
    s = re.sub(r'([A-Za-z])(=|<>)"', r'\1 \2 "', s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r"\s+,", ",", s)
    return s


_PAREN_PHRASES: Final = (
    rf"\(({_NAME}) and ({_NAME})\) is not provided|\(either (or both of )?({_NAME}) or"
    rf"|\(any of ({_NAME}),"
)


class _Parser:
    """Recursive descent over the normalized Rule Logic / Conditionality text (module doc).
    ``lenient`` (A-1 conditions): a phrase that is not a known form is an ``unknown`` atom
    instead of a parse error."""

    def __init__(self, text: str, spec: Spec, *, lenient: bool = False) -> None:
        self.s = _normalize(text)
        self.i = 0
        self.spec = spec
        self.lenient = lenient

    # ---------------------------------------------------------------- helpers

    def _take(self, pattern: str) -> re.Match[str] | None:
        m = re.compile(pattern).match(self.s, self.i)
        if m:
            self.i = m.end()
        return m

    def _peek(self, pattern: str) -> bool:
        return re.compile(pattern).match(self.s, self.i) is not None

    def _ws(self) -> None:
        self._take(r"\s*")

    def fail(self, why: str) -> ParseError:
        return ParseError(f"{why} at: {self.s[self.i : self.i + 80]!r}")

    # ---------------------------------------------------------------- grammar

    def parse(self) -> tuple[Node, Scope | None]:
        each = self._take(rf"For each ({_CONTAINER}),\s*if\s+")
        expr = self.expr()
        self._ws()
        if self.i != len(self.s):
            raise self.fail("unparsed text")
        scope: Scope | None = Scope("each", each.group(1)) if each else None
        # hoist a scope on the last top-level term (module doc: anchors)
        items = list(expr.items) if expr.kind == "and" else [expr]
        last = items[-1]
        if scope is None and last.kind == "scoped" and last.scope is not None:
            scope = last.scope
            items[-1] = last.items[0]
            expr = _fold_and(items) if expr.kind == "and" else items[0]
        return expr, scope

    def expr(self) -> Node:
        acc = self.term()
        while True:
            m = self._take(r"\s*,?\s*(and/or|and|or|when)\s+(?!\")")
            if not m:
                return acc
            op = m.group(1)
            right = self.term()
            if op == "when":
                acc = Node("and", (right, acc))
            elif op == "and":
                acc = Node("and", (*(acc.items if acc.kind == "and" else (acc,)), right))
            else:
                acc = Node("or", (acc, right))

    def term(self) -> Node:
        self._ws()
        # "(X and Y) is not provided", "(either X or Y is ...)": phrases, not groupings
        if not self._peek(_PAREN_PHRASES) and self._take(r"\("):
            node = self.expr()
            self._ws()
            if not self._take(r"\)"):
                raise self.fail("missing ')'")
        else:
            node = self.atom()
        return self._suffixes(node)

    def _suffixes(self, node: Node) -> Node:
        while True:
            scope = self._scope()
            if scope is None:
                return node
            if scope.kind != "noise":
                node = Node("scoped", (node,), scope=scope)

    def _scope(self) -> Scope | None:
        start = self.i
        m = self._take(rf"\s+(?:in|for) (?:a|the) (?:given )?instance of ({_CONTAINER})")
        if m:
            return Scope("instance", m.group(1), self._with(m.group(1)))
        m = self._take(rf"\s+in ({_CONTAINER}) container")
        if m:
            return Scope("service", m.group(1), self._with(m.group(1)))
        m = self._take(rf"\s+for one or more (?:instances of )?({_CONTAINER})")
        if m:
            return Scope("one_or_more", m.group(1))
        m = self._take(rf"\s+for ({_NAME}) (=|<>) ")
        if m and self.spec.knows(m.group(1)):
            return Scope("for", "", Cond(m.group(1), m.group(2), self._values()))
        self.i = start
        # wording that restates where the data point is: no effect on the evaluation
        m = self._take(
            rf"\s+(?:in|across all instances of|in any instance of) ({_CONTAINER})(?=$|[\s,)])"
        )
        if m and m.group(1).split("/")[-1] in self.spec.containers:
            return Scope("noise", m.group(1))
        self.i = start
        if self._take(r"\s+for a given comp\b"):
            return Scope("noise", "")
        return None

    def _with(self, container: str) -> Cond | None:
        """``with X = "v"[ and Y = "w"...]``: a following ``and`` comparison belongs to the
        ``with`` when A-1 places its data point inside ``container``."""
        if not self._take(r"\s*(?:with|where) "):
            return None
        first = self.cond()
        more: list[Cond] = []
        while True:
            start = self.i
            m = self._take(rf"\s*,?\s+and\s+(?=({_NAME})\s*(?:<>|<=|>=|=|<|>))")
            if not m or not self._inside(m.group(1), container):
                self.i = start
                break
            more.append(self.cond())
        return Cond(first.name, first.op, first.values, first.other, tuple(more))

    def _inside(self, name: str, container: str) -> bool:
        last = container.split("/")[-1]
        return any(last in path[:-1] for path, _ in self.spec.name_paths(name))

    def cond(self) -> Cond:
        self._ws()
        m = self._take(rf"({_NAME})\s*(<>|<=|>=|=|<|>)\s*")
        if not m:
            raise self.fail("expected a comparison")
        name, op = m.group(1), m.group(2)
        if self._peek(r'"|\("'):
            return Cond(name, op, self._values())
        n = self._take(_NUMBER)
        if n:
            return Cond(name, op, (n.group(0).replace(",", ""),))
        o = self._take(_NAME)
        if o:
            return Cond(name, op, (), o.group(0))
        raise self.fail("expected a value")

    def _values(self) -> tuple[str, ...]:
        self._ws()
        paren = self._take(r"\(")
        values: list[str] = []
        m = self._take(r'"([^"]*)"')
        if not m:
            raise self.fail("expected a quoted value")
        values.append(m.group(1).lstrip("'"))
        while True:
            m = self._take(r'\s*,?\s*(?:or|and/or)\s+"([^"]*)"(?!\s*(?:=|<>))')
            if not m:
                break
            values.append(m.group(1))
        if paren and not self._take(r"\s*\)"):
            raise self.fail("missing ')' after values")
        return tuple(values)

    def atom(self) -> Node:  # noqa: C901 - one branch per H-1 phrase
        self._ws()
        c = _CONTAINER
        m = self._take(rf"there is no instance or more than one instance of ({c})")
        if m:
            return Node(
                "count",
                container=m.group(1),
                cond=self._with(m.group(1)),
                op="!=",
                number=Decimal(1),
            )
        m = self._take(
            rf"there (?:is|are) no instances? of RELATIONSHIP with @xlink:arcrole = \"([^\"]+)\""
            rf" (?:that links|linking) ({_NAME}) to (each )?({_NAME})"
        )
        if m:
            where = None
            if self._take(r"\s+where "):
                where = self.cond()
            return Node(
                "link",
                urn=m.group(1),
                source=m.group(2),
                target=m.group(4),
                cond=where,
                op="each" if m.group(3) else "any",
            )
        m = self._take(rf"(?:there (?:is|are) )?no instances? of ({c})")
        if m:
            cond = self._with(m.group(1))
            self._take(r"\s+(?:are|is) provided")
            return Node("count", container=m.group(1), cond=cond, op="=", number=Decimal(0))
        m = self._take(rf"(?:an|at least one) instance of ({c})")
        if m:
            cond = self._with(m.group(1))
            if not self._take(r"\s+is not provided"):
                raise self.fail("expected 'is not provided'")
            cond = cond or self._with(m.group(1))
            return Node("count", container=m.group(1), cond=cond, op="=", number=Decimal(0))
        m = self._take(
            rf"there is more than one instance of ({c}) with the same ({_NAME}) enumeration"
        )
        if m:
            return Node("unique", name=m.group(2), container=m.group(1))
        m = self._take(rf"(?:there (?:is|are) )?more than one instance of ({c})")
        if m:
            cond = self._with(m.group(1))
            if cond is None and self._take(r"\s+has "):
                cond = self.cond()
            return Node("count", container=m.group(1), cond=cond, op=">", number=Decimal(1))
        m = self._take(rf"there are one or more additional instances of ({c})")
        if m:
            return Node("count", container=m.group(1), op=">", number=Decimal(1))
        m = self._take(rf"there (?:is|are) one or more instances? of ({c})")
        if m:
            cond = self._with(m.group(1))
            return Node("count", container=m.group(1), cond=cond, op=">=", number=Decimal(1))
        m = self._take(
            r"the 'xlink:arcrole', 'xlink:from', and 'xlink:to' labels are not unique across all"
            r" instances of RELATIONSHIP"
        )
        if m:
            return Node("rel_unique")
        m = self._take(
            r"the associated container(?: or data element)? does not have the (xlink:from|xlink:to)"
            r" label specified in a given instance of RELATIONSHIP"
        )
        if m:
            return Node("rel_end", text=m.group(1))
        m = self._take(rf"\(({_NAME}) and ({_NAME})\) is not provided")
        if m:
            return Node("and", (Node("missing", name=m.group(1)), Node("missing", name=m.group(2))))
        m = self._take(rf"({_NAME}) and ({_NAME}) are (not )?provided")
        if m:
            kind = "missing" if m.group(3) else "provided"
            return Node("and", (Node(kind, name=m.group(1)), Node(kind, name=m.group(2))))
        m = self._take(rf"\(?either or both of ({_NAME}) or ({_NAME}) is provided\)?")
        if m:
            return Node(
                "or", (Node("provided", name=m.group(1)), Node("provided", name=m.group(2)))
            )
        m = self._take(rf"\(?either ({_NAME}) or ({_NAME}) is not provided\)?")
        if m:
            return Node("or", (Node("missing", name=m.group(1)), Node("missing", name=m.group(2))))
        m = self._take(rf"neither ({_NAME}) nor ({_NAME}) is provided")
        if m:
            return Node("and", (Node("missing", name=m.group(1)), Node("missing", name=m.group(2))))
        m = self._take(rf"\(?any of ((?:{_NAME}, )+(?:and )?{_NAME}) are (?:also )?provided\)?")
        if m:
            names = re.split(r",\s*(?:and\s+)?|\s+and\s+", m.group(1))
            return _fold_or([Node("provided", name=n) for n in names if n])
        m = self._take(rf"({c}) is not provided")
        if m and m.group(1) in self.spec.containers:
            return Node("count", container=m.group(1), op="=", number=Decimal(0))
        if m:
            self.i = m.start()
        m = self._take(rf"({_NAME})")
        if not m:
            return self._unknown("expected a data point")
        name = m.group(1)
        if name in ("Exists", "Included", "Photo", "Defect") and self.lenient:
            self.i = m.start()
            return self._unknown("")
        return self._after_name(name, m.start())

    def _after_name(self, name: str, start: int) -> Node:  # noqa: C901
        c = _CONTAINER
        m = self._take(
            r"\s+is not provided between (-?\d+) and (-?\d+) with at least (\d+) digits of"
            r" precision to the right of the decimal point"
        )
        if m:
            low, high, digits = m.groups()
            return Node("range", name=name, regex=f"{low} {high} {digits}")
        m = self._take(r"\s+is not provided in (YYYY-MM-DD|YYYY-MM|YYYY) format")
        if m:
            return Node("date_format", name=name, regex=m.group(1))
        m = self._take(r"\s+(?:is not provided|is not present)( or <> )?")
        if m:
            if m.group(1):
                values = self._values()
                either = (Node("missing", name=name), Node("cmp", cond=Cond(name, "<>", values)))
                return Node("or", either)
            return Node("missing", name=name)
        if self._take(r"\s+is either not provided, or not = "):
            values = self._values()
            return Node(
                "or", (Node("missing", name=name), Node("cmp", cond=Cond(name, "<>", values)))
            )
        if self._take(r"\s+(?:is provided|is present|exists)"):
            return Node("provided", name=name)
        m = self._take(rf"\s+is not unique across all instances of ({c})")
        if m:
            cond = self._with(m.group(1))
            return Node("unique", name=name, container=m.group(1), cond=cond)
        if self._take(
            r"\s+does not include year, month and day"
            r"(?: in YYYY-MM-DD format| \(YYYY-MM-DD format\)| YYYY-MM-DD format)?"
        ):
            return Node("date_format", name=name, regex="YYYY-MM-DD")
        if self._take(r"\s+does not include year and month(?: \(YYYY-MM format\))?"):
            return Node("date_format", name=name, regex="YYYY-MM")
        if self._take(r"\s+is in the future"):
            return Node("future", name=name)
        m = self._take(r"\s+is more than (\d+) days before the current date")
        if m:
            return Node("days_before", name=name, number=Decimal(m.group(1)))
        m = self._take(r"\s+length exceeds (\d+) characters")
        if m:
            return Node("length", name=name, number=Decimal(m.group(1)))
        m = self._take(r"\s+is not in the required format \(([^)]*)\)")
        if m:
            regex = _FORMAT_PHRASES.get(m.group(1))
            if regex is None:
                raise self.fail("unknown format phrase")
            return Node("pattern", name=name, regex=regex)
        m = self._take(rf"\s*(<>|<=|>=|=|<|>) the number of instances of ({c})")
        if m:
            return Node(
                "count_ref",
                name=name,
                op=m.group(1),
                container=m.group(2),
                cond=self._with(m.group(2)),
            )
        m = self._take(rf"\s*(<>|=) the sum of ({_NAME}) across all instances of ({c})")
        if m:
            return Node("sum_ref", name=name, op=m.group(1), other=m.group(2), container=m.group(3))
        m = self._take(r"\s*(<>|<=|>=|=|<|>)\s*")
        if m:
            op = m.group(1)
            if self._peek(r'"|\("'):
                values = self._values()
                if self._take(r"\s+is not provided"):
                    return Node("value_missing", cond=Cond(name, "=", values))
                return Node("cmp", cond=Cond(name, op, values))
            n = self._take(_NUMBER)
            if n:
                return Node("cmp", cond=Cond(name, op, (n.group(0).replace(",", ""),)))
            o = self._take(_NAME)
            if o and not o.group(0).startswith(("year", "the")):
                return Node("cmp", cond=Cond(name, op, (), o.group(0)))
            raise self.fail("unsupported comparison")
        self.i = start
        return self._unknown("unsupported phrase")

    def _unknown(self, why: str) -> Node:
        if not self.lenient:
            raise self.fail(why or "unsupported phrase")
        m = self._take(r"((?:(?!\s+(?:and|or)\s|\)).)+)")
        if not m:
            raise self.fail("empty phrase")
        return Node("unknown", text=m.group(1))


def _walk_nodes(node: Node, scope: Scope | None = None) -> Iterator[Scope]:
    """Every scope in an expression (and the hoisted one)."""
    if scope is not None:
        yield scope
    if node.scope is not None:
        yield node.scope
    for item in node.items:
        yield from _walk_nodes(item)


def _fold_and(items: Sequence[Node]) -> Node:
    return items[0] if len(items) == 1 else Node("and", tuple(items))


def _fold_or(items: Sequence[Node]) -> Node:
    return items[0] if len(items) == 1 else Node("or", tuple(items))


_CONDITIONS: dict[tuple[int, str], Node | None] = {}


def parse_condition(text: str, spec: Spec) -> Node | None:
    """An A-1 Conditionality Details text (None: does not parse or names unknown data)."""
    key = (id(spec), text)
    if key not in _CONDITIONS:
        try:
            node, scope = _Parser(text, spec, lenient=True).parse()
            if scope is not None:
                node = Node("scoped", (node,), scope=scope)
            unknown = [n for n in node.names() if not spec.knows(n)]
            _CONDITIONS[key] = None if unknown else node
        except ParseError:
            _CONDITIONS[key] = None
    return _CONDITIONS[key]


# =========================================================================== evaluation


def _lcp(a: Sequence[str], b: Sequence[str]) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


@dataclass
class _Ctx:
    checker: _Checker
    home: tuple[str, ...]
    use: str | None
    scope: XNode  # the property or the message
    today: dt.date
    property_filter: frozenset[str] | None = None

    @property
    def spec(self) -> Spec:
        return self.checker.spec


def _best_paths(ctx: _Ctx, name: str, anchor: XNode) -> list[tuple[str, ...]]:
    found = ctx.spec.name_paths(name)
    if ctx.use is not None:
        marked = [
            (p, rows)
            for p, rows in found
            if not rows or any(not r.uses or ctx.use in r.uses for r in rows)
        ]
        found = marked or found
    paths = sorted({p for p, _ in found})
    if not paths:
        return []
    ref = ctx.home if _lcp(ctx.home, anchor.path) == anchor.depth else anchor.path
    best = max(_lcp(p, ref) for p in paths)
    return [p for p in paths if _lcp(p, ref) == best]


def _descend(ctx: _Ctx, base: XNode, path: tuple[str, ...]) -> list[XNode]:
    nodes = [base]
    for depth in range(base.depth, len(path)):
        nxt: list[XNode] = []
        for node in nodes:
            for child in node.find(path[depth]):
                if (
                    child.name == "PROPERTY"
                    and ctx.property_filter is not None
                    and child.path[-3:] == _PROPERTY_PATH
                    and child.attrs.get("ValuationUseType") not in ctx.property_filter
                ):
                    continue
                nxt.append(child)
        nodes = nxt
    return nodes


def _elements(ctx: _Ctx, name: str, anchor: XNode, *, siblings: bool = False) -> list[XNode]:
    """The elements ``name`` (data point or container) resolves to from ``anchor`` (module doc:
    names). ``siblings``: a container is counted from its parent, the anchor included."""
    out: list[XNode] = []
    target = name.split("/")[-1]
    for path in _best_paths(ctx, target if target.startswith("@") else name, anchor):
        k = _lcp(path, anchor.path)
        if siblings:
            k = min(k, len(path) - 1)
        base = anchor.ancestor(k)
        if base.path != path[: base.depth]:
            continue
        out.extend(_descend(ctx, base, path))
    return list({id(n): n for n in out}.values())


def _values(ctx: _Ctx, name: str, anchor: XNode) -> list[str]:
    last = name.split("/")[-1]
    if last.startswith("@"):
        attr = last[1:]
        holders = _elements(ctx, name, anchor)
        return [n.attrs[attr] for n in holders if attr in n.attrs]
    return [n.text for n in _elements(ctx, name, anchor) if n.text]


_DECIMAL: Final = re.compile(r"-?(\d+\.?\d*|\.\d+)")
_ISO: Final = re.compile(r"\d{4}(-\d{2}(-\d{2})?)?$")
_ORDER: Final[dict[str, Callable[[Any, Any], bool]]] = {
    "=": operator.eq,
    "<>": operator.ne,
    "<": operator.lt,
    ">": operator.gt,
    "<=": operator.le,
    ">=": operator.ge,
}


def _number(text: str) -> Decimal | None:
    """A plain decimal (no exponent, NaN or infinity, no thousands separator)."""
    return Decimal(text) if _DECIMAL.fullmatch(text) else None


def _compare(a: str, op: str, b: str) -> bool:
    """``a op b``: numerically when both are numbers, as ISO dates (text order) when both are
    dates, else only ``=`` / ``<>`` as text."""
    x, y = _number(a), _number(b)
    dates = bool(_ISO.match(a) and _ISO.match(b) and "-" in a + b)
    if x is not None and y is not None and not dates:
        return _ORDER[op](x, y)
    if dates or op in ("=", "<>"):
        return _ORDER[op](a, b)
    return False


def _cond_true(ctx: _Ctx, cond: Cond, anchor: XNode) -> bool:
    return _one_cond(ctx, cond, anchor) and all(_cond_true(ctx, c, anchor) for c in cond.also)


def _one_cond(ctx: _Ctx, cond: Cond, anchor: XNode) -> bool:
    values = _values(ctx, cond.name, anchor)
    if cond.other:
        others = _values(ctx, cond.other, anchor)
        return any(_compare(v, cond.op, o) for v in values for o in others)
    if cond.op == "=":
        return any(
            v in cond.values or any(_compare(v, "=", w) for w in cond.values) for v in values
        )
    if cond.op == "<>":  # "X is not v": X provided and no X is v (module doc)
        return bool(values) and not any(
            v in cond.values or any(_compare(v, "=", w) for w in cond.values) for v in values
        )
    return any(_compare(v, cond.op, w) for v in values for w in cond.values)


def _scope_instances(ctx: _Ctx, scope: Scope, anchor: XNode) -> list[XNode]:
    if scope.kind == "for" and scope.cond is not None:
        container = for_container(ctx.spec, ctx.home, scope.cond.name)
        if container is None:
            return []
    else:
        container = scope.container
    nodes = _elements(ctx, container, anchor, siblings=True)
    if scope.cond is not None:
        nodes = [n for n in nodes if _cond_true(ctx, scope.cond, n)]
    return nodes


def for_container(spec: Spec, home: tuple[str, ...], name: str) -> str | None:
    """``X for PartyRoleType = "v"``: the deepest container holding both the condition's data
    point and the rule's xPath (ROLE), which must be able to occur more than once (it or a
    container above it has an A-1 maximum above 1): the phrase picks among instances. None
    when it does not (the condition reaches the data through an arcrole, such as the
    appraiser's INSPECTION, which containment cannot express)."""
    paths = [p for p, _ in spec.name_paths(name)]
    if not paths:
        return None
    best = max(paths, key=lambda p: _lcp(p, home))
    k = len(best) if name.startswith("@") else _lcp(best, home)
    if not k or not any(_repeats(spec, best[:j]) for j in range(1, k + 1)):
        return None
    return best[k - 1]


def _eval(node: Node, ctx: _Ctx, anchor: XNode) -> bool | None:  # noqa: C901
    kind = node.kind
    if kind == "and":
        result: bool | None = True
        for item in node.items:
            value = _eval(item, ctx, anchor)
            if value is False:
                return False
            if value is None:
                result = None
        return result
    if kind == "or":
        result = False
        for item in node.items:
            value = _eval(item, ctx, anchor)
            if value is True:
                return True
            if value is None:
                result = None
        return result
    if kind == "unknown":
        return None
    if kind == "missing":
        return not _values(ctx, node.name, anchor)
    if kind == "provided":
        return bool(_values(ctx, node.name, anchor))
    if kind == "cmp":
        assert node.cond is not None
        return _cond_true(ctx, node.cond, anchor)
    if kind == "value_missing":
        assert node.cond is not None
        return not _cond_true(ctx, node.cond, anchor)
    if kind == "scoped":
        assert node.scope is not None
        instances = _scope_instances(ctx, node.scope, anchor)
        verdicts = [_eval(node.items[0], ctx, inst) for inst in instances]
        if any(v is True for v in verdicts):
            return True
        return None if any(v is None for v in verdicts) else False
    if kind == "count":
        nodes = _elements(
            ctx, node.container, _count_base(ctx, node.container, anchor), siblings=True
        )
        if node.cond is not None:
            nodes = [n for n in nodes if _cond_true(ctx, node.cond, n)]
        return _compare(str(len(nodes)), node.op if node.op != "!=" else "<>", str(node.number))
    if kind == "count_ref":
        nodes = _elements(
            ctx, node.container, _count_base(ctx, node.container, anchor), siblings=True
        )
        if node.cond is not None:
            nodes = [n for n in nodes if _cond_true(ctx, node.cond, n)]
        values = _values(ctx, node.name, anchor)
        return any(_compare(v, node.op, str(len(nodes))) for v in values)
    if kind == "sum_ref":
        values = _values(ctx, node.name, anchor)
        base = _count_base(ctx, node.container, anchor)
        parts = [
            _number(v)
            for inst in _elements(ctx, node.container, base, siblings=True)
            for v in _values(ctx, node.other, inst)
        ]
        if not values or any(p is None for p in parts):
            return False
        total = sum((p for p in parts if p is not None), Decimal(0))
        return any(_compare(v, node.op, str(total)) for v in values)
    if kind == "unique":
        return _duplicates(ctx, node, anchor)
    if kind == "date_format":
        return any(
            format_error(v, "Date", node.regex) is not None for v in _values(ctx, node.name, anchor)
        )
    if kind == "range":
        low, high, digits = (Decimal(x) for x in node.regex.split())
        for v in _values(ctx, node.name, anchor):
            number = _number(v)
            places = len(v.partition(".")[2]) if "." in v else 0
            if number is None or not low <= number <= high or places < digits:
                return True
        return False
    if kind == "rel_unique":
        triples = [
            (n.attrs.get("xlink:arcrole"), n.attrs.get("xlink:from"), n.attrs.get("xlink:to"))
            for n in ctx.checker.doc.nodes
            if n.name == "RELATIONSHIP"
        ]
        return len(triples) != len(set(triples))
    if kind == "rel_end":
        return any(
            n.name == "RELATIONSHIP" and n.attrs.get(node.text, "") not in ctx.checker.doc.labels
            for n in ctx.checker.doc.nodes
        )
    if kind == "future":
        return any(
            v > ctx.today.isoformat() for v in _values(ctx, node.name, anchor) if _ISO.match(v)
        )
    if kind == "days_before":
        limit = (ctx.today - dt.timedelta(days=int(node.number))).isoformat()
        return any(v < limit for v in _values(ctx, node.name, anchor) if _ISO.match(v))
    if kind == "length":
        return any(len(v) > node.number for v in _values(ctx, node.name, anchor))
    if kind == "pattern":
        return any(not re.fullmatch(node.regex, v) for v in _values(ctx, node.name, anchor))
    if kind == "link":
        return not _linked(ctx, node, anchor)
    raise ValueError(f"unknown node kind {kind}")


def _count_base(ctx: _Ctx, container: str, anchor: XNode) -> XNode:
    """Where ``instances of C`` are counted: inside the anchor when C is below it, else in the
    rule's scope (the property or the message)."""
    for path in _best_paths(ctx, container, anchor):
        if len(path) > anchor.depth and path[: anchor.depth] == anchor.path:
            return anchor
    return ctx.scope


def _duplicates(ctx: _Ctx, node: Node, anchor: XNode) -> bool:
    if node.container == "PROPERTY":
        root = ctx.checker.doc.root
        saved, ctx.property_filter = ctx.property_filter, None
        props = _elements(ctx, "PROPERTY", root)
        ctx.property_filter = saved
        if node.cond is not None:
            props = [p for p in props if _cond_true(ctx, node.cond, p)]
        values = [v for p in props for v in _values(ctx, node.name, p)]
    else:
        base = _count_base(ctx, node.container, anchor)
        instances = _elements(ctx, node.container, base, siblings=True)
        values = [v for inst in instances for v in _values(ctx, node.name, inst)]
    return len(values) != len(set(values))


def _linked(ctx: _Ctx, node: Node, anchor: XNode) -> bool:
    """``that links A to each B``: every B in scope is the xlink:to of a RELATIONSHIP with the
    arcrole whose xlink:from labels an A (true without any B); ``that links A to B``: at least
    one B in scope is (the rule fires when none is)."""
    targets = _elements(ctx, node.target, anchor)
    if node.cond is not None:
        targets = [t for t in targets if _cond_true(ctx, node.cond, t)]
    doc = ctx.checker.doc
    links = [
        n
        for n in doc.nodes
        if n.name == "RELATIONSHIP" and n.attrs.get("xlink:arcrole") == node.urn
    ]

    def linked(target: XNode) -> bool:
        label = target.attrs.get("xlink:label")
        for rel in links:
            if label is None or rel.attrs.get("xlink:to") != label:
                continue
            sources = doc.labels.get(rel.attrs.get("xlink:from", ""), [])
            if any(s.name == node.source.split("/")[-1] for s in sources):
                return True
        return False

    if node.op == "each":
        return all(linked(t) for t in targets)
    return any(linked(t) for t in targets)


# =========================================================================== H-1


@dataclass(frozen=True)
class RuleStatus:
    rule: H1Rule
    implemented: bool
    reason: str
    expr: Node | None = None
    scope: Scope | None = None


_USE_OF_AFFECTED: Final = {
    "Subject": SUBJECT,
    "Sales Comparable": "SalesComparable",
    "Land Comparable": "LandComparable",
    "Rental Comparable": "RentalComparable",
    "Gross Rent Multiplier Comparable": "GrossRentMultiplierComparable",
    "Property Analyzed Not Used": "PropertyAnalyzedNotUsed",
}
_NOT_MECHANICAL: Final = (
    (re.compile(r"\bPDF\b|\bpdf\b"), "requires the PDF report"),
    (re.compile(r"valid 2-character US State"), "requires an external code list (US states)"),
    (re.compile(r"For each combination", re.I), "cross-property comparison (subject and comps)"),
    (re.compile(r"year of|\+ \d+ years?|within 1 year"), "date arithmetic not in the grammar"),
    (re.compile(r"related to cost approach|that has arc role"), "relationship-scoped wording"),
    (re.compile(r"in at least one instance of"), "ambiguous quantifier ('in at least one')"),
)


def _home(spec: Spec, xpath: str) -> tuple[str, ...] | None:
    """The A-1 container path of an H-1 xPath (``../X/Y/`` is the shortest A-1 path ending
    with ``X/Y``; several xPaths joined by ``;`` share their common prefix)."""
    homes: list[tuple[str, ...]] = []
    for part in xpath.split(";"):
        segs = _path(part)
        if segs and segs[0] == "..":
            segs = segs[1:]
        candidates = [p for p in spec.blocks_by_path if segs and p[-len(segs) :] == segs]
        if not candidates:
            return None
        homes.append(min(candidates, key=len))
    if not homes:
        return None
    common = homes[0]
    for home in homes[1:]:
        common = common[: _lcp(common, home)]
    return common or None


def _affected_use(text: str) -> tuple[list[str], bool]:
    """``([use types], per property)`` of a Property Affected cell."""
    parts = [p.strip().removesuffix("#n").strip() for p in text.split(";") if p.strip()]
    uses = [_USE_OF_AFFECTED[p] for p in parts if p in _USE_OF_AFFECTED]
    return uses, bool(uses) and len(uses) == len(parts)


def classify(spec: Spec, rule: H1Rule) -> RuleStatus:
    logic = rule.logic
    for pattern, reason in _NOT_MECHANICAL:
        if pattern.search(logic):
            return RuleStatus(rule, False, reason)
    uses, per_property = _affected_use(rule.affected)
    if len(uses) > 1 or (
        "SubjectProperty" in logic and re.search(r'"(Sales|Rental|Land|GrossRent)', logic)
    ):
        return RuleStatus(rule, False, "cross-property comparison (subject and comps)")
    if rule.affected not in ("N/A",) and not per_property:
        return RuleStatus(rule, False, f"Property Affected {rule.affected!r} not understood")
    if _home(spec, rule.xpath) is None:
        return RuleStatus(rule, False, f"xPath {rule.xpath!r} is not an A-1 container path")
    try:
        expr, scope = _Parser(logic, spec).parse()
    except ParseError as exc:
        return RuleStatus(rule, False, f"logic not in the supported grammar ({exc})")
    names = set(expr.names())
    if scope is not None:
        names |= {scope.container} if scope.container else set()
        names |= set(scope.cond.names()) if scope.cond is not None else set()
    unknown = sorted(n for n in names if not spec.knows(n))
    if unknown:
        return RuleStatus(rule, False, f"names data A-1 does not define: {', '.join(unknown)}")
    home = _home(spec, rule.xpath)
    assert home is not None
    for item in _walk_nodes(expr, scope):
        if item.kind == "for" and item.cond is not None:
            if for_container(spec, home, item.cond.name) is None:
                return RuleStatus(
                    rule, False, "condition reaches the data through an arcrole (not containment)"
                )
    return RuleStatus(rule, True, "", expr, scope)


_ADJ_RULE: Final = "UAD1758"


def rules_registry(spec: Spec) -> list[RuleStatus]:
    out: list[RuleStatus] = []
    for rule in spec.rules:
        if rule.message_id == _ADJ_RULE and spec.adjustments:
            out.append(RuleStatus(rule, True, "", Node("adjustments")))
        else:
            out.append(classify(spec, rule))
    return out


def _run_rule(checker: _Checker, status: RuleStatus) -> list[Finding]:
    rule = status.rule
    spec = checker.spec
    doc = checker.doc
    home = _home(spec, rule.xpath)
    assert home is not None
    uses, per_property = _affected_use(rule.affected)
    use = uses[0] if uses else None
    counts_properties = re.search(r"instances? of PROPERTY\b(?!_)", rule.logic) is not None
    scopes: list[XNode]
    property_filter: frozenset[str] | None = None
    if per_property and not counts_properties:
        scopes = [
            n
            for n in doc.nodes
            if n.name == "PROPERTY"
            and n.path[-3:] == _PROPERTY_PATH
            and n.attrs.get("ValuationUseType") == use
        ]
    else:
        scopes = [doc.root]
        if not per_property and not counts_properties and "PROPERTY" not in home:
            property_filter = frozenset({SUBJECT})
    findings: list[Finding] = []
    seen: set[int] = set()  # anchors already reported (a comparable rule can reach one
    # element outside the comparable, such as a DATA_SOURCE, from every comparable)
    severity = "error" if rule.severity.lower() == "fatal" else "warning"
    for scope_root in scopes:
        ctx = _Ctx(
            checker,
            home=home,
            use=use,
            scope=scope_root,
            today=checker.today,
            property_filter=property_filter,
        )
        if status.expr is not None and status.expr.kind == "adjustments":
            findings.extend(_adjustments(ctx, rule, scope_root, severity))
            continue
        assert status.expr is not None
        if status.scope is not None:
            anchors = _scope_instances(ctx, status.scope, scope_root)
        else:
            anchors = _anchors(ctx, status.expr, scope_root)
        for anchor in anchors:
            if id(anchor) in seen or _eval(status.expr, ctx, anchor) is not True:
                continue
            seen.add(id(anchor))
            point = rule.point.split(" ")[0].lstrip("@")
            shown = _values(ctx, rule.point.split(" ")[0], anchor) if point else []
            findings.append(
                Finding(
                    severity,
                    rule.message_id,
                    anchor.xpath,
                    ", ".join(shown[:3]),
                    rule.text,
                    rule.severity,
                )
            )
    return findings


def _direct_names(node: Node) -> Iterator[str]:
    """The data points whose values an expression tests at its anchor: comparisons and value
    tests, not presence tests (a missing data point has no instance to anchor on), not
    scoped sub-clauses, counting phrases or relationship tests."""
    if node.kind in ("and", "or"):
        for item in node.items:
            yield from _direct_names(item)
    elif node.kind == "cmp" and node.cond is not None:
        yield from node.cond.names()
    elif node.kind in (
        "range",
        "count_ref",
        "sum_ref",
        "date_format",
        "future",
        "days_before",
        "length",
        "pattern",
    ):
        yield node.name


def _repeats(spec: Spec, path: tuple[str, ...]) -> bool:
    return any(b.card.high is None or b.card.high > 1 for b in spec.blocks_by_path.get(path, []))


def _anchors(ctx: _Ctx, expr: Node, scope_root: XNode) -> list[XNode]:
    """Where a rule without an explicit instance phrase is evaluated (module doc: anchors):
    each instance of the deepest repeating container that holds every data point the rule
    reads (a data point with no repeating container between the scope and itself, such as
    PROPERTY_DETAIL/AttachmentType, holds one value for the whole scope and does not count),
    else the scope itself."""
    spec = ctx.spec
    depth = scope_root.depth
    if _lcp(ctx.home, scope_root.path) != depth:
        return [scope_root]
    prefix = ctx.home
    local: list[tuple[str, ...]] = []
    for name in set(_direct_names(expr)):
        paths = _best_paths(ctx, name, scope_root)
        if not paths:
            continue
        full = paths[0]
        path = full if name.split("/")[-1].startswith("@") else full[:-1]
        if _lcp(path, scope_root.path) < depth:
            continue  # outside the scope (report-level data read by a property rule)
        if not any(_repeats(spec, path[:k]) for k in range(depth + 1, len(path) + 1)):
            continue  # one value per scope
        local.append(full)
        prefix = prefix[: _lcp(prefix, path)]
    level = next((k for k in range(len(prefix), depth, -1) if _repeats(spec, prefix[:k])), None)
    if level is None or not local:
        return [scope_root]
    return _descend(ctx, scope_root, prefix[:level])


def _adjustments(ctx: _Ctx, rule: H1Rule, prop: XNode, severity: str) -> list[Finding]:
    """UAD1758 from the ``Adjustments Cardinality`` sheet: an adjustment type that cannot
    repeat appears more than once for a comparable."""
    if prop.attrs.get("ValuationUseType") != "SalesComparable":
        return []
    counts: dict[str, int] = {}
    for adj in _elements(ctx, "COMPARABLE_ADJUSTMENT", prop):
        for kind in _values(ctx, "ComparableAdjustmentType", adj):
            counts[kind] = counts.get(kind, 0) + 1
    out: list[Finding] = []
    for kind, n in sorted(counts.items()):
        repeat, use = ctx.spec.adjustments.get(kind, ("", ""))
        if n > 1 and repeat.lower().startswith("adjustment cannot repeat"):
            out.append(
                Finding(severity, rule.message_id, prop.xpath, kind, rule.text, rule.severity)
            )
    return out


# =========================================================================== entry points


def validate(
    data: bytes,
    spec: Spec,
    *,
    path: str = "",
    today: dt.date | None = None,
    registry: Sequence[RuleStatus] | None = None,
) -> Result:
    """Every A-1 and implemented H-1 check on one message."""
    try:
        doc = Document.parse(data)
    except InvalidMismo as exc:
        return Result(path, [Finding("error", "XML", "", "", str(exc))], {})
    checker = _Checker(spec, doc, today or dt.date.today())
    checker.run()
    findings = list(checker.findings)
    statuses = registry if registry is not None else rules_registry(spec)
    for status in statuses:
        if status.implemented:
            findings.extend(_run_rule(checker, status))
    not_checked = dict(checker.not_checked)
    skipped = sum(not s.implemented for s in statuses)
    if skipped:
        not_checked["H-1 rules not implemented"] = skipped
    return Result(path, findings, not_checked)


def conditions_summary(spec: Spec) -> dict[str, int]:
    """How many A-1 Conditionality rows the A1.required check evaluates: ``R``; ``CR``
    mechanical (every phrase parsed), partly mechanical (a phrase such as a bare ``Exists`` is
    unknown: checked only when the rest of the condition is false), not parsed."""

    def unknown(node: Node) -> bool:
        return node.kind == "unknown" or any(unknown(item) for item in node.items)

    out = {"R": 0, "CR mechanical": 0, "CR partly mechanical": 0, "CR not parsed": 0}
    for row in spec.rows:
        if row.cond == "R":
            out["R"] += 1
        elif row.cond == "CR":
            parsed = parse_condition(row.cond_details, spec)
            key = (
                "not parsed"
                if parsed is None
                else "partly mechanical"
                if unknown(parsed)
                else "mechanical"
            )
            out[f"CR {key}"] += 1
    return out


def render_rules(statuses: Sequence[RuleStatus], spec: Spec | None = None) -> str:
    done = sum(s.implemented for s in statuses)
    reasons: dict[str, int] = {}
    for s in statuses:
        if not s.implemented:
            key = re.sub(r" \(.*", "", s.reason).split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
    lines = [
        "# H-1 UAD compliance rules: validator registry",
        "",
        f"{len(statuses)} rules: {done} implemented, {len(statuses) - done} not implemented.",
        "",
        "| reason (not implemented) | rules |",
        "|---|---|",
        *(f"| {r} | {n} |" for r, n in sorted(reasons.items(), key=lambda x: -x[1])),
        "",
    ]
    if spec is not None:
        lines += [
            "A-1 Conditionality rows (the A1.required check):",
            "",
            "| rows | count |",
            "|---|---|",
            *(f"| {k} | {n} |" for k, n in conditions_summary(spec).items()),
            "",
        ]
    lines += [
        "| Message ID | Unique ID | severity | property | status | reason |",
        "|---|---|---|---|---|---|",
    ]
    for s in statuses:
        reason = s.reason.replace("|", "\\|")
        lines.append(
            f"| {s.rule.message_id} | {s.rule.uid} | {s.rule.severity} | {s.rule.affected} |"
            f" {'implemented' if s.implemented else 'not implemented'} | {reason} |"
        )
    return "\n".join(lines) + "\n"


def render_result(result: Result) -> str:
    lines = [f"{result.path}: {result.errors} errors, {result.warnings} warnings"]
    for f in result.findings:
        if f.severity == "info":
            continue
        lines.append(f"  {f.severity:7} {f.rule:16} {f.xpath} [{f.value}] {f.message}")
    for kind, n in sorted(result.not_checked.items()):
        lines.append(f"  not checked: {kind}: {n}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("xml", nargs="*", type=Path)
    parser.add_argument("--json", type=Path, default=None, help="write the findings as JSON")
    parser.add_argument("--rules-report", type=Path, default=None, help="write the H-1 registry")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--today", type=dt.date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    spec = load_spec(args.data_dir)
    registry = rules_registry(spec)
    for note in spec.notes:
        print(f"spec note: {note}")
    if args.rules_report:
        args.rules_report.write_text(render_rules(registry, spec))
        print(f"wrote {args.rules_report}")
    results = [
        validate(p.read_bytes(), spec, path=str(p), today=args.today, registry=registry)
        for p in args.xml
    ]
    for result in results:
        print(render_result(result))
    if args.json:
        payload = [
            {
                "path": r.path,
                "errors": r.errors,
                "warnings": r.warnings,
                "findings": [f.as_dict() for f in r.findings],
                "not_checked": r.not_checked,
            }
            for r in results
        ]
        args.json.write_text(json.dumps(payload, indent=2))
        print(f"wrote {args.json}")
    return 1 if any(r.errors for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
