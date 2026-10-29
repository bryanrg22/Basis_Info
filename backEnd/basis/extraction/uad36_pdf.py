"""Read a UAD 3.6 URAR PDF by its own captions: the spec-derived counterpart of the 1004 mapping.

The catalogue (:data:`basis.extraction.field_mappings.FIELDS`) is shaped by the legacy 1004:
its labels are 1004 captions and :func:`~basis.extraction.field_mappings.key_for_label` maps
1004 captions in 1004 sections. On the redesigned URAR those captions are wrong or ambiguous
("Location" is a defect's location, "Area" a level's area, "State" the appraiser's license
state). When :mod:`basis.extraction.forms` recognizes a UAD 3.6 report, the pipeline maps
captions with this module instead, and every rule here comes from the GSE specification
through the generated :mod:`basis.extraction.uad36_map` (Appendix A-1 v1.4 / G-1 v1.4), never
from the sample reports:

* **Sections.** A-1 numbers the report's sections in print order (``SECTIONS``: 1 Summary,
  2 Assignment Information ... 29 Certifications). A page line that is a section name (any
  case, optionally followed by " (continued)" or by " - " and a qualifier such as
  "Unit Interior - Building 1 - Unit 1") opens that section. Within a page, sections only move
  forward: the reconciliation's "Approaches to Value" table repeats "Sales Comparison
  Approach", "Income Approach" and "Cost Approach" as row labels after the Reconciliation
  heading, and those rows must not reopen the earlier sections. The first heading of a page
  may go anywhere (a cover page before the report, the Summary after it).
* **Captions.** Each mapped key's A-1 Report Locations (``locations``: section, subsection,
  label) say where the value is printed. A caption maps to a key when the key has a location
  with that label in the section the caption is printed in. When several keys share the
  label in that section, the subsection decides: the key whose subsection name is the
  nearest one printed above the caption in that section ("Company Name" under "Client/Lender"
  vs. under "Appraiser"); otherwise the caption is ambiguous and maps to nothing (the model
  fallback is asked instead). A caption in a section where no key prints it maps to nothing:
  "Year Built" of an outbuilding is not the dwelling's.
* **Which keys.** Only keys A-1 carries (``status == "mapped"``) are read or asked on a UAD
  3.6 report (:func:`carried_keys`). ``no_equivalent`` and ``unresolved`` keys (no A-1 data
  point carries the 1004 box: tax year, the neighborhood checkboxes, total rooms, ...) are
  neither mapped nor asked. Captions map only for keys whose value is one printed box
  (:data:`SINGLE_BOX_RULES`, the reader's rule for the key in ``uad36_map``); a key read by
  combining several printed values (a sum, the full/half bath counts, the table rows of a
  material) is left to the model fallback, which is told the captions.
* **Units.** ``improvements.units`` (the 1004's One / One with Accessory Unit) combines two
  printed counts, A-1's Units Excluding ADUs and Accessory Dwelling Units (the ``units`` rule's
  two sources, each found by its own captions in the sections A-1 prints it in, the subject's
  sections before the Summary; the value follows the caption on its line, or is the next line
  when the caption line has none, as Azure DI's page text prints it): :func:`unit_count_candidate`,
  from the text layer and from Azure DI's page text, by
  :func:`basis.extraction.mismo36.units_value` like the XML reader.
* **Text-layer pass.** Without Azure DI key-value pairs, a printed line holds several
  "Caption value" pairs and no colon; a value runs from its caption to the next A-1 caption
  on the line (``LABELS``, longest first, as printed). The extent of a free-text value cannot
  be told that way ("Borrower Name James Borrower Yes No"), so the pass keeps only typed
  values (amounts, numbers, dates, areas) that parse as the key's type.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from basis.extraction.azure_di import AzureDiResult
from basis.extraction.cleaners import clean_field
from basis.extraction.field_mappings import (
    FIELD_BY_KEY,
    FIELDS,
    REGEX_CONFIDENCE,
    normalize_label,
)
from basis.extraction.mismo36 import units_value
from basis.extraction.results import Candidate, Region, Source
from basis.extraction.uad36_map import LABELS, MAPPING, PLACES, SECTIONS

# The reader rules (uad36_map ``rule``) whose value is one printed box: one data point, or a
# party's name parts printed together. Every other rule combines several printed values.
SINGLE_BOX_RULES: Final = frozenset(
    {
        "text",
        "name",
        "amount",
        "amount_or_no",
        "enum",
        "date",
        "month",
        "number",
        "single",
        "area",
        "bool",
        "per_unit",
        "gla",
        "client",
    }
)
# Qualified containers whose choice the report prints as a subsection or section heading.
_PRINTED_QUALIFIERS: Final = frozenset({"PARTY", "IMPROVEMENT"})
# A-1's comparable grids (sales comparison, rental, income): a caption there heads a column of
# the subject and the comparables, and the subject's cell is often empty ("—").
GRID_SECTIONS: Final = frozenset({22, 23, 24})
# Catalogue types the text-layer pass reads (module doc: a typed value must parse).
_TEXT_PASS_TYPES: Final = frozenset({"int", "float", "currency", "percent", "date"})
_CONTINUED = re.compile(r"\s*\(continued\)$")
_PLACEHOLDER = re.compile(r"\[[^\]]*\]|\{[^}]*\}")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})
_MARK = re.compile(r":(?:un)?selected:")


def carried_keys() -> frozenset[str]:
    """Catalogue keys the UAD 3.6 URAR carries (A-1 maps them)."""
    return frozenset(key for key, entry in MAPPING.items() if entry["status"] == "mapped")


CARRIED: Final = carried_keys()
NOT_CARRIED: Final = frozenset(spec.key for spec in FIELDS) - CARRIED

_SECTION_NUMBER: Final[dict[str, int]] = {normalize_label(name): n for n, name in SECTIONS}
_SECTION_NAME: Final[dict[int, str]] = dict(SECTIONS)


def heading_of(text: str) -> int | None:
    """The A-1 section number ``text`` opens when the line is a section heading."""
    norm = _CONTINUED.sub("", normalize_label(text.translate(_QUOTES)))
    number = _SECTION_NUMBER.get(norm)
    if number is not None:
        return number
    head, sep, _ = norm.partition(" - ")
    return _SECTION_NUMBER.get(head) if sep else None


@dataclass(frozen=True, slots=True)
class Line:
    page: int
    section: str | None
    text: str


def section_lines(pages: Sequence[str]) -> list[Line]:
    """Every non-empty line with its page and A-1 section (module doc: sections only move
    forward within a page; the first heading of a page may go anywhere)."""
    lines: list[Line] = []
    current: int | None = None
    for number, page in enumerate(pages, start=1):
        first = True
        for raw in page.splitlines():
            text = raw.translate(_QUOTES).strip()
            if not text:
                continue
            heading = heading_of(text)
            if heading is not None and (first or current is None or heading >= current):
                current = heading
                first = False
            name = _SECTION_NAME.get(current) if current is not None else None
            lines.append(Line(number, name, text))
    return lines


@dataclass(frozen=True, slots=True)
class _Place:
    key: str | None  # None: a box no catalogue key reads
    subsection: tuple[tuple[str, bool], ...]  # (printed name, a prefix: it had a placeholder)


def _subsection_names(subsection: str) -> tuple[tuple[str, bool], ...]:
    names = []
    for part in subsection.split(" - "):
        name = normalize_label(_PLACEHOLDER.sub("", part).strip())
        if len(name) >= 3:
            names.append((name, _PLACEHOLDER.search(part) is not None or name.endswith("/")))
    return tuple(names)


def _readable(entry: Mapping[str, Any]) -> bool:
    """A key a caption may fill: carried, one printed box (module doc), and not a row of a
    table chosen by a sibling value (a utility, a cost line, a site feature): only a party
    (chosen by its printed subsection) or the dwelling (by its printed section) qualifies."""
    return (
        entry["status"] == "mapped"
        and entry["rule"] in SINGLE_BOX_RULES
        and set(entry["where"]) <= _PRINTED_QUALIFIERS
    )


def _section_number(fid: str) -> int:
    head = fid.split(".")[0]
    return int(head) if head.isdigit() else 0


def _index() -> dict[tuple[str, str], list[_Place]]:
    index: dict[tuple[str, str], list[_Place]] = {}
    keyed: set[tuple[str, str, str]] = set()

    def add(section: str, label: str, place: _Place) -> None:
        places = index.setdefault((section, normalize_label(label)), [])
        if place not in places:
            places.append(place)

    for key, entry in MAPPING.items():
        if entry["status"] != "mapped":
            continue
        for fid, section, subsection, label in entry.get("locations", ()):
            if _section_number(fid) in GRID_SECTIONS:
                continue
            add(section, label, _Place(key, _subsection_names(subsection)))
            keyed.add((section, subsection, normalize_label(label)))
    for number, section, subsection, label in PLACES:
        if number in GRID_SECTIONS or (section, subsection, normalize_label(label)) in keyed:
            continue
        add(section, label, _Place(None, _subsection_names(subsection)))
    return index


_INDEX: Final = _index()
_READABLE: Final = frozenset(key for key, entry in MAPPING.items() if _readable(entry))


def readable_keys() -> frozenset[str]:
    """Keys a caption can fill on a UAD 3.6 report (:func:`_readable`)."""
    return _READABLE


def _names_line(names: Sequence[tuple[str, bool]], line: str) -> bool:
    return any(line == name or (prefix and line.startswith(name)) for name, prefix in names)


def _by_label(caption: str, keys: Iterable[str | None]) -> str | None:
    """Of several keys sharing a caption, the one whose catalogue label the caption contains
    ("Current Owner of Public Record" is the owner's, not the borrower's), if exactly one."""
    norm = normalize_label(caption)
    hits = {
        key
        for key in keys
        if key is not None
        and any(normalize_label(label) in norm for label in FIELD_BY_KEY[key].labels)
    }
    return hits.pop() if len(hits) == 1 else None


def key_for_caption(caption: str, section: str | None, above: Sequence[str] = ()) -> str | None:
    """The key ``caption`` names when printed in ``section``; ``above`` are the lines printed
    above it in that section, in print order (module doc: the nearest printed subsection
    decides a label several boxes share; an undecided one maps to nothing). Only a key a
    caption may fill (:func:`readable_keys`) is returned."""
    if section is None:
        return None
    places = _INDEX.get((section, normalize_label(caption)))
    if not places:
        return None
    keys = {place.key for place in places}
    if len(keys) > 1:
        decided = False
        for text in reversed(above):
            norm = normalize_label(text)
            hits = {place.key for place in places if _names_line(place.subsection, norm)}
            if hits:
                keys, decided = hits, True
                break
        if len(keys) > 1:
            key = _by_label(caption, keys) if not decided or None not in keys else None
            keys = {key} if key is not None else set()
    key = keys.pop() if len(keys) == 1 else None
    return key if key in _READABLE else None


def _occurrence(lines: Sequence[Line], page: int | None, caption: str, nth: int) -> int | None:
    """Index in ``lines`` of the ``nth`` (0-based) line of ``page`` that is ``caption``."""
    target = normalize_label(caption)
    seen = 0
    for index, line in enumerate(lines):
        if line.page == page and normalize_label(line.text) == target:
            if seen == nth:
                return index
            seen += 1
    return None


def _above(lines: Sequence[Line], index: int) -> list[str]:
    """Lines of the same page and section above ``lines[index]``, in print order."""
    here = lines[index]
    out: list[str] = []
    for line in reversed(lines[:index]):
        if line.page != here.page or line.section != here.section:
            break
        out.append(line.text)
    return out[::-1]


def _value_text(key: str, value: str) -> str:
    """A key-value pair's value as one field value: several printed names (two borrowers,
    one per line) are joined with "; " as the catalogue joins parties; other line breaks are
    spaces."""
    parts = [p.strip() for p in value.splitlines() if p.strip()]
    if MAPPING[key].get("rule") == "name":
        return "; ".join(parts)
    return " ".join(parts)


def _better(key: str, current: str, other: str) -> bool:
    """The first value read for a key wins unless it does not parse as the key's type and a
    later one does ("Known Sales Concessions Yes" before "Total Sales Concessions $5,000")."""
    return clean_field(key, current).error is not None and clean_field(key, other).error is None


# Reader rules that combine the living units' boxes (GLA summed, a count that must agree): one
# printed box is the value only when the report prints a single Unit Interior.
_PER_UNIT_RULES: Final = frozenset({"per_unit", "gla"})
_UNIT_INTERIOR: Final = next(n for n, name in SECTIONS if name == "Unit Interior")


def unit_interiors(lines: Sequence[Line]) -> int:
    """How many Unit Interior sections the report prints ("Unit Interior", "Unit Interior -
    Building 1 - Unit 2", "Unit Interior - Standalone ADU - Cottage"; continuations excluded)."""
    found = {
        normalize_label(line.text)
        for line in lines
        if heading_of(line.text) == _UNIT_INTERIOR and not _CONTINUED.search(line.text.lower())
    }
    return len(found)


def _allowed(key: str, units: int) -> bool:
    return MAPPING[key]["rule"] not in _PER_UNIT_RULES or units <= 1


UNITS_KEY: Final = "improvements.units"
_COUNT: Final = re.compile(r"\d{1,3}")


def _printed_counts(lines: Sequence[Line], source: Mapping[str, Any]) -> list[tuple[int, int]]:
    """``(count, page)`` of each printing of one count data point (``source``, a mapping
    source with its A-1 captions) in the sections A-1 prints it in, outside the comparable
    grids: the subject's sections first, the Summary after (module doc)."""
    captions = {normalize_label(c) for c in source.get("captions", ())}
    sections = {
        section: _section_number(fid)
        for fid, section, _, label in MAPPING[UNITS_KEY]["locations"]
        if normalize_label(label) in captions and _section_number(fid) not in GRID_SECTIONS
    }
    found: list[tuple[bool, int, int]] = []  # (in the Summary, count, page)
    for index, line in enumerate(lines):
        if line.section not in sections:
            continue
        for caption, value in caption_values(line.text):
            if normalize_label(caption) not in captions:
                continue
            if not value and index + 1 < len(lines):  # Azure DI: the value on the next line
                below = lines[index + 1]
                if (below.page, below.section) == (line.page, line.section):
                    value = below.text
            if _COUNT.fullmatch(value):
                found.append((sections[line.section] == 1, int(value), line.page))
    return [(count, page) for _, count, page in sorted(found, key=lambda f: f[0])]


def unit_count_candidate(
    pages: Sequence[str], *, source: Source, confidence: float = REGEX_CONFIDENCE
) -> Candidate | None:
    """``improvements.units`` from the printed Units Excluding ADUs and Accessory Dwelling
    Units counts (module doc); ``None`` when they are not printed or cannot say. Two counts
    make the value, no single box's reading confidence applies: the text pass's."""
    entry = MAPPING[UNITS_KEY]
    if entry["status"] != "mapped" or entry["rule"] != "units" or len(entry["sources"]) != 2:
        return None
    lines = section_lines(pages)
    counts = _printed_counts(lines, entry["sources"][0])
    if not counts:
        return None
    adus = _printed_counts(lines, entry["sources"][1])
    (count, page), adu = counts[0], (adus[0][0] if adus else None)
    text = units_value(count, adu)
    if text is None:
        return None
    return Candidate(key=UNITS_KEY, raw_text=text, page=page, confidence=confidence, source=source)


def azure_candidates(result: AzureDiResult) -> list[Candidate]:
    """Candidates from Azure DI key-value pairs whose caption maps to a key (module doc), and
    the unit count from Azure DI's page text (:func:`unit_count_candidate`)."""
    lines = section_lines(result.page_texts())
    units = unit_interiors(lines)
    found: dict[str, Candidate] = {}
    counted = unit_count_candidate(result.page_texts(), source="azure")
    if counted is not None:
        found[UNITS_KEY] = counted
    seen: dict[tuple[int | None, str], int] = {}
    for pair in result.key_value_pairs:
        norm = normalize_label(pair.key)
        nth = seen.get((pair.page, norm), 0)
        seen[(pair.page, norm)] = nth + 1
        if pair.value is None or not _MARK.sub("", pair.value).strip():
            continue
        index = _occurrence(lines, pair.page, pair.key, nth)
        if index is not None:
            key = key_for_caption(pair.key, lines[index].section, _above(lines, index))
        else:  # a caption Azure DI read across lines: the page's sections, no subsection
            sections = dict.fromkeys(line.section for line in lines if line.page == pair.page)
            keys = {key_for_caption(pair.key, s) for s in sections} - {None}
            key = keys.pop() if len(keys) == 1 else None
        if key is None or not _allowed(key, units):
            continue
        text = _value_text(key, pair.value)
        if key in found and not _better(key, found[key].raw_text, text):
            continue
        box = pair.value_region
        found[key] = Candidate(
            key=key,
            raw_text=text,
            page=pair.page,
            confidence=pair.confidence,
            source="azure",
            regions=(Region(page=box.page, bbox=box.bbox),) if box is not None else (),
        )
    return list(found.values())


# --------------------------------------------------------------------------- text layer

_CAPTIONS: Final = re.compile(
    r"(?:(?<=\s)|^)(" + "|".join(re.escape(label) for label in LABELS) + r")(?=\s|$)"
)


def caption_values(text: str) -> list[tuple[str, str]]:
    """``(caption, value)`` for every A-1 caption on a printed line, each value running to
    the next caption (module doc)."""
    matches = list(_CAPTIONS.finditer(text))
    pairs: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pairs.append((match.group(1), text[match.end() : end].strip()))
    return pairs


def text_candidates(pages: Sequence[str]) -> list[Candidate]:
    """Typed values from the text layer by their UAD 3.6 captions, and the unit count
    (module doc)."""
    lines = section_lines(pages)
    units = unit_interiors(lines)
    found: dict[str, Candidate] = {}
    counted = unit_count_candidate(pages, source="regex")
    if counted is not None:
        found[UNITS_KEY] = counted
    for index, line in enumerate(lines):
        if line.section is None:
            continue
        for caption, value in caption_values(line.text):
            if not value:
                continue
            key = key_for_caption(caption, line.section, _above(lines, index))
            if (
                key is None
                or key in found
                or FIELD_BY_KEY[key].type not in _TEXT_PASS_TYPES
                or not _allowed(key, units)
            ):
                continue
            if clean_field(key, value).error is not None:
                continue
            found[key] = Candidate(
                key=key,
                raw_text=value,
                page=line.page,
                confidence=REGEX_CONFIDENCE,
                source="regex",
            )
    return list(found.values())


# --------------------------------------------------------------------------- fallback


def _where(locations: Iterable[Sequence[str]]) -> list[str]:
    """``Section > Subsection`` of each location: the report's own sections, then the Summary,
    the comparable grids only when nothing else prints the value; at most three."""
    main: list[str] = []
    summary: list[str] = []
    grid: list[str] = []
    for fid, section, subsection, _ in locations:
        number = _section_number(fid)
        where = f"{section} > {subsection}" if subsection else section
        bucket = grid if number in GRID_SECTIONS else summary if number == 1 else main
        if where not in bucket:
            bucket.append(where)
    return (main + summary or grid)[:3]


def _captions(key: str) -> list[str]:
    """The key's A-1 captions, less one another key owns: a caption that contains the other
    key's catalogue label and not this key's ("Current Owner of Public Record" is printed by
    the borrower's data point too, but names the owner)."""
    own = [normalize_label(label) for label in FIELD_BY_KEY[key].labels]
    out = []
    for caption in MAPPING.get(key, {}).get("captions", ()):
        norm = normalize_label(caption)
        mine = any(label in norm for label in own)
        owned = any(
            other != key
            and caption in entry.get("captions", ())
            and any(normalize_label(label) in norm for label in FIELD_BY_KEY[other].labels)
            for other, entry in MAPPING.items()
            if other in FIELD_BY_KEY
        )
        if mine or not owned:
            out.append(caption)
    return out


def describe(key: str) -> str:
    """How the fallback names ``key`` on a UAD 3.6 report: the catalogue meaning, the A-1
    captions it is printed under and the sections they are in."""
    spec = FIELD_BY_KEY[key]
    entry: Mapping[str, Any] = MAPPING.get(key, {})
    captions = ", ".join(f'"{c}"' for c in _captions(key))
    where = "; ".join(_where(entry.get("locations", ())))
    text = f'means "{spec.label}"'
    if captions:
        text += f"; UAD 3.6 caption {captions}"
    if where:
        text += f" in {where}"
    return text
