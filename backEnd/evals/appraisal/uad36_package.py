"""Build a UAD 3.6 appraisal package (MISMO 3.6 XML + report PDF + Images/) from templates.

A UAD 3.6 delivery is a ZIP holding the MISMO 3.6 XML, the PDF of the redesigned URAR and
an ``Images`` folder (``docs/review_2026-10-15/APPRAISAL_RESEARCH.md`` section 1.2). To test
extraction on the same property in both the legacy 1004 layout and the UAD 3.6 layout, a
known appraisal is re-expressed in UAD 3.6 by filling a real UAD 3.6 sample (the template)
with the property's facts. This module holds the reusable, data-free parts; the mapping for
a given property lives with that property's private data.

* XML (:class:`XmlFill`): edit a template message in place (set values, clone or graft
  elements from a second template, add elements), then :meth:`XmlFill.prune` drops every
  leaf the fill did not write, the containers left empty, and the xlink relationships whose
  ends are gone. :func:`xml_leftovers` then diffs the output against the templates' values
  and reports every template value that survived without being written on purpose.
* PDF (:class:`PageEditor`): remove a template value's glyphs (text-only redaction: rules,
  shading and labels stay), write the new value at the same baseline and size, wipe whole
  regions, stamp a template glyph (a checkbox state) elsewhere, place photos, draw table
  rules. Fonts can be reused from the template (:func:`embedded_font`); characters a subset
  lacks fall back. With ``reflow`` the page's whole text layer is rewritten in reading order,
  so each new value follows its caption for text-layer readers. :func:`surviving_spans`
  lists template spans still printed that were not declared kept.
* :func:`zip_package` writes the delivery ZIP.

Nothing here contains values from a template or a client appraisal.
"""

from __future__ import annotations

import copy
import re
import zipfile
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from xml.etree import ElementTree as ET

import pymupdf

# pymupdf ships no type information; one Any-typed handle keeps mypy strict elsewhere.
_mu: Any = pymupdf

XLINK: Final = "http://www.w3.org/1999/xlink"
XSI: Final = "http://www.w3.org/2001/XMLSchema-instance"

# Attributes that carry structure, not appraisal facts (links, roles, units, versions).
STRUCTURAL_ATTRIBUTES: Final = frozenset(
    {
        f"{{{XLINK}}}label",
        f"{{{XLINK}}}from",
        f"{{{XLINK}}}to",
        f"{{{XLINK}}}arcrole",
        f"{{{XSI}}}schemaLocation",
        "MISMOReferenceModelIdentifier",
        "ValuationUseType",
        "SequenceNumber",
    }
)


def local(tag: str) -> str:
    """The element name without its namespace."""
    return tag.rsplit("}", 1)[-1]


def _is_structural_attribute(name: str) -> bool:
    return name in STRUCTURAL_ATTRIBUTES or name.endswith("UnitOfMeasureType")


# ---------------------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------------------


def namespace_declarations(path: Path) -> list[tuple[str, str]]:
    """The (prefix, uri) declarations of a document, in order (the root's, in practice)."""
    found: list[tuple[str, str]] = []
    # Local template files chosen by the caller; no external entities are resolved.
    for _event, item in ET.iterparse(path, events=("start-ns",)):  # noqa: S314
        prefix, uri = item
        if (prefix, uri) not in found:
            found.append((prefix, uri))
    return found


def parse_xml(path: Path) -> ET.ElementTree[ET.Element]:
    """Parse a template; comments (notices, scenario names) are dropped."""
    return ET.parse(path)  # noqa: S314


def _split_steps(path: str) -> list[str]:
    steps: list[str] = []
    depth = 0
    quote: str | None = None
    current = ""
    for ch in path:
        if quote:
            current += ch
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "/" and depth == 0:
            steps.append(current)
            current = ""
            continue
        current += ch
    steps.append(current)
    return steps


def qualify(path: str, ns: str, prefixes: Mapping[str, str] | None = None) -> str:
    """Turn ``A/B[@x='1']/C[D='t']`` into an ElementTree path in namespace ``ns``.

    ``prefixes`` maps attribute prefixes (``xlink``) to URIs. Steps ``.``, ``..``, ``*`` and
    empty steps (``.//``) are kept; positional predicates ``[2]`` are kept.
    """
    prefixes = {"xlink": XLINK, "xsi": XSI, **(prefixes or {})}
    out: list[str] = []
    for step in _split_steps(path):
        m = re.fullmatch(r"([^\[]*)((?:\[.*?\])*)", step)
        if m is None:
            raise ValueError(f"bad path step {step!r}")
        name, predicates = m.group(1), m.group(2)
        if name and name not in (".", "..", "*"):
            name = f"{{{ns}}}{name}"
        quals = []
        for pred in re.findall(r"\[(.*?)\](?=\[|$)", predicates):
            if pred.startswith("@"):
                attr = pred[1:]
                if ":" in attr.split("=")[0]:
                    prefix, rest = attr.split(":", 1)
                    attr = f"{{{prefixes[prefix]}}}{rest}"
                quals.append(f"[@{attr}]")
            elif pred[:1].isdigit() or pred.startswith("last()"):
                quals.append(f"[{pred}]")
            else:
                quals.append(f"[{{{ns}}}{pred}]")
        out.append(name + "".join(quals))
    return "/".join(out)


def element_paths(root: ET.Element) -> Iterator[tuple[str, ET.Element]]:
    """Every element with its index path (``/A[1]/B[2]``, local names, 1-based per tag)."""

    def walk(el: ET.Element, path: str) -> Iterator[tuple[str, ET.Element]]:
        yield path, el
        counts: dict[str, int] = {}
        for child in el:
            if not isinstance(child.tag, str):
                continue
            name = local(child.tag)
            counts[name] = counts.get(name, 0) + 1
            yield from walk(child, f"{path}/{name}[{counts[name]}]")

    yield from walk(root, f"/{local(root.tag)}[1]")


def _text(el: ET.Element) -> str | None:
    if el.text is None:
        return None
    value = el.text.strip()
    return value or None


def _format(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


@dataclass
class PruneReport:
    leaves: int = 0
    containers: int = 0
    relationships: int = 0


class XmlFill:
    """A template message being filled with another property's values.

    Every value the caller writes is recorded; :meth:`prune` removes everything else. Element
    paths given to the methods are :func:`qualify` paths in the message's default namespace.
    """

    def __init__(self, template: Path) -> None:
        self.template = template
        self.tree = parse_xml(template)
        self.root = self.tree.getroot()
        self.declarations = namespace_declarations(template)
        defaults = [uri for prefix, uri in self.declarations if prefix == ""]
        if not defaults:
            raise ValueError(f"{template}: no default namespace")
        self.ns = defaults[0]
        # id -> element; holding the element keeps the id from being reused after removals
        self._written: dict[int, ET.Element] = {}
        self._kept: dict[int, ET.Element] = {}

    # selection -------------------------------------------------------------------------
    def q(self, path: str) -> str:
        return qualify(path, self.ns)

    def findall(self, path: str, base: ET.Element | None = None) -> list[ET.Element]:
        return (base if base is not None else self.root).findall(self.q(path))

    def find(self, path: str, base: ET.Element | None = None) -> ET.Element:
        found = self.findall(path, base)
        if len(found) != 1:
            raise LookupError(f"{path}: {len(found)} matches, expected 1")
        return found[0]

    def parent(self, el: ET.Element) -> ET.Element:
        for candidate in self.root.iter():
            for child in candidate:
                if child is el:
                    return candidate
        raise LookupError("element is not in the tree")

    # writing ---------------------------------------------------------------------------
    def set(
        self,
        target: ET.Element | str,
        value: object,
        base: ET.Element | None = None,
        /,
        **attrs: str,
    ) -> ET.Element:
        """Write a leaf value (``bool`` as ``true``/``false``) and optional attributes."""
        el = self.find(target, base) if isinstance(target, str) else target
        if len(el):
            raise ValueError(f"{local(el.tag)} has children; values go on leaves")
        el.text = _format(value)
        for name, attr_value in attrs.items():
            el.set(name, attr_value)
        self._written[id(el)] = el
        return el

    def add(self, parent: ET.Element, path: str, value: object = None, **attrs: str) -> ET.Element:
        """Create ``path`` (``A/B/Leaf``) under ``parent``, reusing no existing element.

        With a value the last element is written; otherwise it is kept as a container.
        """
        el = parent
        for name in path.split("/"):
            el = ET.SubElement(el, f"{{{self.ns}}}{name}")
        if value is not None:
            self.set(el, value, **attrs)
        else:
            for name, attr_value in attrs.items():
                el.set(name, attr_value)
        return el

    def ensure(self, parent: ET.Element, path: str, value: object, **attrs: str) -> ET.Element:
        """Write ``path`` under ``parent``, creating the missing elements along it."""
        el = parent
        for name in path.split("/"):
            found = el.find(f"{{{self.ns}}}{name}")
            el = found if found is not None else ET.SubElement(el, f"{{{self.ns}}}{name}")
        return self.set(el, value, **attrs)

    def clone(self, el: ET.Element, *, after: ET.Element | None = None) -> ET.Element:
        """A deep copy of ``el`` inserted after ``after`` (default: after ``el``).

        Copied values are not written: whatever the caller does not set is pruned.
        """
        anchor = after if after is not None else el
        parent = self.parent(anchor)
        duplicate = copy.deepcopy(el)
        parent.insert(list(parent).index(anchor) + 1, duplicate)
        return duplicate

    def graft(self, el: ET.Element, parent: ET.Element, index: int | None = None) -> ET.Element:
        """A deep copy of an element from another template, inserted under ``parent``."""
        duplicate = copy.deepcopy(el)
        if index is None:
            parent.append(duplicate)
        else:
            parent.insert(index, duplicate)
        return duplicate

    def remove(self, el: ET.Element) -> None:
        self.parent(el).remove(el)

    def keep(self, el: ET.Element) -> None:
        """Keep an element that carries no text (an empty container or a link) through prune."""
        self._kept[id(el)] = el

    def written(self, el: ET.Element) -> bool:
        return id(el) in self._written

    # pruning ---------------------------------------------------------------------------
    def prune(self) -> PruneReport:
        """Drop unwritten leaves, then empty containers, then dangling relationships."""
        report = PruneReport()
        while True:
            changed = False
            labels = {
                el.get(f"{{{XLINK}}}label") for el in self.root.iter() if isinstance(el.tag, str)
            }
            for parent in list(self.root.iter()):
                for child in list(parent):
                    if not isinstance(child.tag, str):
                        parent.remove(child)
                        changed = True
                        continue
                    if len(child):
                        continue
                    if id(child) in self._written or id(child) in self._kept:
                        continue
                    if local(child.tag) == "RELATIONSHIP":
                        ends = (child.get(f"{{{XLINK}}}from"), child.get(f"{{{XLINK}}}to"))
                        if all(end in labels for end in ends):
                            continue
                        report.relationships += 1
                    elif _text(child) is not None:
                        report.leaves += 1
                    else:
                        report.containers += 1
                    parent.remove(child)
                    changed = True
            if not changed:
                return report

    def order_like(self, *templates: Path) -> None:
        """Sort every element's children into the order the templates use for those names
        (MISMO containers are sequences). Names no template uses keep their relative order
        after the known ones."""
        sequences: dict[str, list[str]] = {}
        for path in (self.template, *templates):
            for el in parse_xml(path).getroot().iter():
                seq = sequences.setdefault(local(el.tag), [])
                previous: str | None = None
                for child in el:
                    name = local(child.tag)
                    if name not in seq:
                        seq.insert(seq.index(previous) + 1 if previous in seq else 0, name)
                    previous = name
        for el in self.root.iter():
            order = sequences.get(local(el.tag))
            if not order or len(el) < 2:
                continue
            rank = {name: i for i, name in enumerate(order)}
            keyed = sorted(
                enumerate(list(el)),
                key=lambda ic: (rank.get(local(ic[1].tag), len(rank)), ic[0]),
            )
            el[:] = [c for _, c in keyed]

    # output ----------------------------------------------------------------------------
    def manifest(self) -> dict[str, str]:
        """Index path -> value of every written element still in the tree."""
        return {
            path: el.text or "" for path, el in element_paths(self.root) if id(el) in self._written
        }

    def tostring(self, comment: str | None = None) -> bytes:
        for prefix, uri in self.declarations:
            ET.register_namespace(prefix, uri)
        ET.indent(self.root, space="\t")
        body = ET.tostring(self.root, encoding="unicode", short_empty_elements=True)
        # ElementTree declares only the namespaces it uses; keep the template's declarations
        start = body.index(">")
        head = body[:start]
        for prefix, uri in self.declarations:
            attr = f'xmlns:{prefix}="{uri}"' if prefix else f'xmlns="{uri}"'
            if attr not in head:
                head += f" {attr}"
        body = head + body[start:]
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        if comment:
            if "--" in comment:
                raise ValueError("an XML comment cannot contain '--'")
            lines.append(f"<!--{comment}-->")
        return ("\n".join(lines) + "\n" + body + "\n").encode("utf-8")

    def write(self, path: Path, comment: str | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.tostring(comment)
        ET.fromstring(data)  # noqa: S314  # well-formed, or raise before writing
        path.write_bytes(data)


@dataclass(frozen=True)
class Leftover:
    path: str
    value: str
    kind: str  # "template_value": a template's value not written; "undeclared": not written


@dataclass
class LeftoverReport:
    leftovers: list[Leftover] = field(default_factory=list)
    declared_equal_template: list[tuple[str, str]] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.leftovers


def _template_values(paths: Iterable[Path]) -> set[str]:
    values: set[str] = set()
    for path in paths:
        for el in parse_xml(path).getroot().iter():
            if not isinstance(el.tag, str):
                continue
            text = _text(el)
            if text is not None:
                values.add(text)
            for name, value in el.attrib.items():
                if not _is_structural_attribute(name):
                    values.add(value.strip())
    return values


def xml_leftovers(
    output: Path,
    templates: Sequence[Path],
    declared: Mapping[str, str],
    structural_tags: Collection[str] = (),
) -> LeftoverReport:
    """Diff an output message against its templates' values.

    Every text node and non-structural attribute of ``output`` must be either *declared*
    (``declared`` maps its index path, ``/A[1]/B[1]`` or ``/A[1]/B[1]/@name``, to the value
    written on purpose) or a structural constant (an element named in ``structural_tags``).
    An undeclared value equal to any template value is a ``template_value`` leftover; any
    other undeclared value is ``undeclared``. Declared values that happen to equal a template
    value are listed for review (``declared_equal_template``), not failed.
    """
    template_values = _template_values(templates)
    report = LeftoverReport()
    root = parse_xml(output).getroot()
    for path, el in element_paths(root):
        items: list[tuple[str, str]] = []
        text = _text(el)
        if text is not None and local(el.tag) not in structural_tags:
            items.append((path, text))
        for name, value in el.attrib.items():
            if _is_structural_attribute(name):
                continue
            items.append((f"{path}/@{local(name)}", value.strip()))
        for item_path, value in items:
            report.checked += 1
            if declared.get(item_path) == value:
                if value in template_values:
                    report.declared_equal_template.append((item_path, value))
                continue
            kind = "template_value" if value in template_values else "undeclared"
            report.leftovers.append(Leftover(item_path, value, kind))
    return report


# ---------------------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------------------

Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Span:
    """One text span of a page (pymupdf ``dict`` output)."""

    index: int
    text: str
    bbox: Rect
    origin: tuple[float, float]
    size: float
    font: str
    color: int

    @property
    def clean(self) -> str:
        return " ".join(self.text.split())

    @property
    def bold(self) -> bool:
        return "Bold" in self.font or "Black" in self.font


def page_spans(page: Any) -> list[Span]:
    out: list[Span] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for s in line["spans"]:
                out.append(
                    Span(
                        len(out),
                        s["text"],
                        tuple(s["bbox"]),
                        tuple(s["origin"]),
                        s["size"],
                        s["font"],
                        s["color"],
                    )
                )
    return out


def embedded_font(doc: Any, base_name: str) -> bytes | None:
    """The largest embedded font program whose base name ends with ``base_name``."""
    best: bytes | None = None
    for page in doc:
        for xref, _ext, _type, name, *_ in page.get_fonts():
            if name.split("+")[-1] != base_name:
                continue
            buffer = doc.extract_font(xref)[3]
            if buffer and (best is None or len(buffer) > len(best)):
                best = buffer
    return best


@dataclass
class FontFace:
    """A primary font (maybe a subset taken from the template) and a base-14 fallback."""

    name: str
    buffer: bytes | None = None
    fallback: str = "helv"

    def __post_init__(self) -> None:
        self._font = _mu.Font(fontbuffer=self.buffer) if self.buffer else None
        self._fallback = _mu.Font(self.fallback)
        # A subset often maps two code points to one glyph (space and U+00A0, '-' and the
        # soft hyphen U+00AD), and the ToUnicode map pymupdf writes then names the other code
        # point. Space and hyphen stay in the font (commit rewrites their ToUnicode entries,
        # see SHARED_GLYPH_UNICODE); other characters on shared glyphs use the fallback.
        self._shared: set[int] = set()
        if self._font is not None:
            seen: dict[int, int] = {}
            for cp in [*range(0x20, 0x250), *range(0x2000, 0x2070)]:
                gid = self._font.has_glyph(cp)
                if gid:
                    if gid in seen:
                        self._shared.add(gid)
                    seen[gid] = cp

    def covers(self, ch: str) -> bool:
        if self._font is None:
            return False
        gid = self._font.has_glyph(ord(ch))
        return bool(gid) and (gid not in self._shared or ch in SHARED_GLYPH_UNICODE.values())

    def runs(self, text: str) -> list[tuple[bool, str]]:
        """``(primary?, text)`` runs."""
        out: list[tuple[bool, str]] = []
        for ch in text:
            primary = self.covers(ch)
            if out and out[-1][0] == primary:
                out[-1] = (primary, out[-1][1] + ch)
            else:
                out.append((primary, ch))
        return out

    def width(self, text: str, size: float) -> float:
        total = 0.0
        for primary, run in self.runs(text):
            font = self._font if primary and self._font is not None else self._fallback
            total += float(font.text_length(run, fontsize=size))
        return total


# ToUnicode targets rewritten for fonts inserted from a subset: written as -> meant
SHARED_GLYPH_UNICODE: Final = {"\u00a0": " ", "\u00ad": "-"}
DEFAULT_COLOR: Final = 0x231F20
RULE_GRAY: Final = (0.577, 0.586, 0.596)


def _rgb(color: int) -> tuple[float, float, float]:
    return ((color >> 16 & 255) / 255, (color >> 8 & 255) / 255, (color & 255) / 255)


@dataclass
class _Write:
    x: float
    y: float
    text: str
    size: float
    bold: bool
    color: int
    italic: bool = False


@dataclass
class Stamp:
    """A glyph (e.g. a checked box) cut from a template page, to place elsewhere."""

    doc: Any
    clip: Rect
    text: str


def make_stamp(doc: Any, page_no: int, span_text: str, nth: int = 0) -> Stamp:
    """A one-page copy of ``doc[page_no]`` holding only the ``nth`` span whose text is
    ``span_text`` (all other text removed), to be shown clipped to that span."""
    stamp = _mu.open()
    stamp.insert_pdf(doc, from_page=page_no, to_page=page_no)
    page = stamp[0]
    spans = [s for s in page_spans(page) if s.text == span_text]
    if len(spans) <= nth:
        raise LookupError(f"no span {span_text!r} #{nth} on page {page_no + 1}")
    target = spans[nth]
    for s in page_spans(page):
        if s.index != target.index and s.text.strip():
            page.add_redact_annot(_inset(s.bbox), fill=False)
    page.apply_redactions(
        images=_mu.PDF_REDACT_IMAGE_REMOVE,
        graphics=_mu.PDF_REDACT_LINE_ART_NONE,
        text=_mu.PDF_REDACT_TEXT_REMOVE,
    )
    return Stamp(stamp, target.bbox, span_text)


def _inset(bbox: Rect) -> Any:
    x0, y0, x1, y1 = bbox
    dy = (y1 - y0) * 0.25
    return _mu.Rect(x0 + 0.2, y0 + dy, x1 - 0.2, y1 - dy)


class PageEditor:
    """Edits to one page, applied together by :meth:`commit`.

    Template values are removed with text-only redactions (rules, shading, labels and images
    stay) and replacements are written at the removed span's origin and size. ``wipe`` paints
    a region white and removes everything in it. Every span the caller touched or declared is
    *handled*; :meth:`sweep` blanks the rest that the caller's ``keep`` test rejects.
    """

    def __init__(
        self,
        page: Any,
        regular: FontFace,
        bold: FontFace | None = None,
        italic: FontFace | None = None,
        *,
        reflow: bool = False,
    ) -> None:
        """``reflow``: on commit, also rewrite the template text that stays, so the whole
        text layer is in reading order (rows top to bottom, left to right) with the new
        values next to their captions. Without it, new text follows all template text in
        the content stream, far from its caption for any text-layer reader."""
        self.page = page
        self.regular = regular
        self.bold = bold or regular
        self.italic = italic or regular
        self.reflow = reflow
        self.spans = page_spans(page)
        self.handled: set[int] = set()
        self.kept: set[int] = set()
        self._redact: list[Rect] = []
        self._wipe: list[Rect] = []
        self._writes: list[_Write] = []
        self._stamps: list[tuple[Stamp, tuple[float, float]]] = []
        self._images: list[tuple[Rect, Path]] = []
        self._lines: list[tuple[Rect, tuple[float, float, float], float]] = []
        self._fills: list[tuple[Rect, tuple[float, float, float]]] = []
        self.written: list[tuple[Rect, str]] = []

    # finding ---------------------------------------------------------------------------
    def find_all(
        self, text: str, *, clip: Rect | None = None, contains: bool = False
    ) -> list[Span]:
        want = " ".join(text.split())
        out = []
        for s in self.spans:
            if clip is not None and not _inside(s.bbox, clip):
                continue
            if (want in s.clean) if contains else (s.clean == want):
                out.append(s)
        return out

    def find(
        self, text: str, *, clip: Rect | None = None, nth: int = 0, contains: bool = False
    ) -> Span:
        found = self.find_all(text, clip=clip, contains=contains)
        if len(found) <= nth:
            raise LookupError(f"page {self.page.number + 1}: no span {text!r} (#{nth}) in {clip}")
        return found[nth]

    def line_of(self, span: Span, *, right: float | None = None) -> list[Span]:
        """Spans on the same baseline to the right of ``span`` (up to ``right``)."""
        return [
            s
            for s in self.spans
            if abs(s.origin[1] - span.origin[1]) < 1.5
            and s.bbox[0] >= span.bbox[2] - 0.5
            and (right is None or s.bbox[0] < right)
            and s.clean
        ]

    def value_block(
        self, label: Span, *, right: float | None = None, below: float | None = None
    ) -> list[Span]:
        """The value printed beside ``label``: the spans right of it on its baseline, then
        the continuation lines starting at the same x as that value (at most ``leading`` 16pt
        apart), down to the next span in the label's column (or ``below``)."""
        same_column = [
            s
            for s in self.spans
            if abs(s.bbox[0] - label.bbox[0]) < 3 and s.origin[1] > label.origin[1] + 2 and s.clean
        ]
        stop = min([s.bbox[1] for s in same_column] + [below or 1e9])

        def in_columns(s: Span, x_from: float) -> bool:
            return (
                bool(s.clean)
                and s.bbox[0] >= x_from
                and (right is None or s.bbox[0] < right)
                and s.index != label.index
            )

        first = [
            s
            for s in self.spans
            if in_columns(s, label.bbox[2] - 0.5) and abs(s.origin[1] - label.origin[1]) < 1.5
        ]
        if not first:
            return []
        x0 = min(s.bbox[0] for s in first)
        block = list(first)
        baseline = label.origin[1]
        while True:
            row = [
                s
                for s in self.spans
                if in_columns(s, x0 - 3)
                and 1.5 < s.origin[1] - baseline <= 16
                and s.bbox[1] < stop - 1
            ]
            aligned = [s for s in row if abs(s.bbox[0] - x0) < 3]
            if not aligned:
                return block
            baseline = aligned[0].origin[1]
            block += [s for s in row if abs(s.origin[1] - baseline) < 1.5]

    # editing ---------------------------------------------------------------------------
    def keep(self, *spans: Span) -> None:
        """Declare spans as intentionally left (labels, or values equal to the new ones)."""
        self.handled.update(s.index for s in spans)
        self.kept.update(s.index for s in spans)

    def blank(self, *spans: Span) -> None:
        for s in spans:
            self.handled.add(s.index)
            self._redact.append(s.bbox)

    def replace(
        self,
        span: Span,
        text: str | None,
        *,
        size: float | None = None,
        bold: bool | None = None,
        align: str = "left",
        right: float | None = None,
        dx: float = 0.0,
    ) -> None:
        """Remove ``span`` and write ``text`` at its origin (``None`` = leave blank).

        ``align="right"`` right-aligns at the span's right edge (or ``right``)."""
        self.blank(span)
        if text is None or text == "":
            return
        x = span.origin[0] + dx
        if align == "right":
            face = self.bold if (span.bold if bold is None else bold) else self.regular
            x = (right if right is not None else span.bbox[2]) - face.width(text, size or span.size)
        self.write(
            x,
            span.origin[1],
            text,
            size=size or span.size,
            bold=span.bold if bold is None else bold,
            color=span.color,
        )

    def set_value(
        self,
        label: str | Span,
        value: str | Sequence[str] | None,
        *,
        clip: Rect | None = None,
        nth: int = 0,
        right: float | None = None,
        below: float | None = None,
        x: float | None = None,
        leading: float = 13.0,
    ) -> Span:
        """Replace the value block beside a label with ``value`` (one string per line).

        Template lines beyond the new value are blanked; extra new lines are written below
        the last template line at ``leading``. ``x`` places the value when the template
        printed none (an empty block)."""
        lab = self.find(label, clip=clip, nth=nth) if isinstance(label, str) else label
        self.keep(lab)
        block = self.value_block(lab, right=right, below=below)
        lines: list[str] = (
            [] if value is None else [value] if isinstance(value, str) else list(value)
        )
        rows: list[list[Span]] = []
        for s in sorted(block, key=lambda s: (round(s.origin[1], 1), s.bbox[0])):
            if rows and abs(rows[-1][0].origin[1] - s.origin[1]) < 1.5:
                rows[-1].append(s)
            else:
                rows.append([s])
        for row in rows:
            self.blank(*row)
        if not lines:
            return lab
        if rows:
            first = rows[0][0]
            x0, y0, size, color = first.origin[0], first.origin[1], first.size, first.color
        else:
            if x is None:
                raise LookupError(f"no template value beside {lab.clean!r}; pass x")
            x0, y0, size, color = x, lab.origin[1], lab.size, DEFAULT_COLOR
        for i, line in enumerate(lines):
            y = rows[i][0].origin[1] if i < len(rows) else y0 + leading * i
            self.write(x0, y, line, size=size, color=color)
        return lab

    def write(
        self,
        x: float,
        y: float,
        text: str,
        *,
        size: float = 9.0,
        bold: bool = False,
        color: int = DEFAULT_COLOR,
        max_width: float | None = None,
    ) -> None:
        """Write ``text`` with its baseline at ``y``; shrink to ``max_width`` if given."""
        face = self.bold if bold else self.regular
        if max_width is not None:
            width = face.width(text, size)
            if width > max_width:
                size = size * max_width / width
        self._writes.append(_Write(x, y, text, size, bold, color))
        width = face.width(text, size)
        self.written.append(((x, y - size * 0.8, x + width, y + size * 0.25), text))

    def wrap(self, text: str, width: float, size: float, *, bold: bool = False) -> list[str]:
        """Greedy word wrap of ``text`` into lines no wider than ``width``."""
        face = self.bold if bold else self.regular
        lines: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if current and face.width(candidate, size) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    def paragraph(
        self,
        rect: Rect,
        text: str,
        *,
        size: float = 9.0,
        leading: float = 11.0,
        min_size: float = 6.5,
        first_indent: float = 0.0,
        color: int = DEFAULT_COLOR,
    ) -> float:
        """Write ``text`` wrapped into ``rect`` (top = first baseline minus ``size``).

        The size shrinks (leading in proportion) until the text fits the rect's height;
        raises if it does not fit at ``min_size``. Returns the last baseline."""
        x0, y0, x1, y1 = rect
        while True:
            lines = self._wrap_indent(text, x1 - x0, size, first_indent)
            last = y0 + size + leading * (len(lines) - 1)
            if last <= y1 or size <= min_size:
                break
            leading *= (size - 0.25) / size
            size -= 0.25
        if last > y1 + 0.5:
            raise ValueError(f"text does not fit {rect} at {size}pt")
        for i, line in enumerate(lines):
            x = x0 + (first_indent if i == 0 else 0.0)
            self.write(x, y0 + size + leading * i, line, size=size, color=color)
        return last

    def _wrap_indent(self, text: str, width: float, size: float, indent: float) -> list[str]:
        if not indent:
            return self.wrap(text, width, size)
        words = text.split()
        first = ""
        while words:
            candidate = f"{first} {words[0]}".strip()
            if first and self.regular.width(candidate, size) > width - indent:
                break
            first = candidate
            words.pop(0)
        return [first, *self.wrap(" ".join(words), width, size)] if words else [first]

    def line(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        color: tuple[float, float, float] = RULE_GRAY,
        width: float = 0.5,
    ) -> None:
        """Draw a rule (after wipes and redactions, before text)."""
        self._lines.append(((x0, y0, x1, y1), color, width))

    def fill(self, rect: Rect, color: tuple[float, float, float] = RULE_GRAY) -> None:
        """Paint a filled box (a table's shaded total cell)."""
        self._fills.append((rect, color))

    def blank_region(self, rect: Rect) -> None:
        """Remove the text spans centred in ``rect`` (graphics stay)."""
        self.blank(*(s for s in self.spans if s.clean and _inside(s.bbox, rect)))

    def wipe(self, rect: Rect) -> None:
        """Paint ``rect`` white, removing its text, images and covered line art."""
        for s in self.spans:
            if _overlaps(s.bbox, rect):
                self.handled.add(s.index)
        self._wipe.append(rect)

    def stamp(self, stamp: Stamp, at: tuple[float, float]) -> None:
        """Show a template glyph with its top-left corner at ``at``."""
        self._stamps.append((stamp, at))
        x0, y0, x1, y1 = stamp.clip
        self.written.append(((at[0], at[1], at[0] + x1 - x0, at[1] + y1 - y0), stamp.text))

    def set_glyph(self, span: Span, stamp: Stamp) -> None:
        """Replace a glyph span (a checkbox pair) with ``stamp`` at the same place."""
        if span.text == stamp.text:
            self.keep(span)
            return
        self.blank(span)
        self.stamp(stamp, (span.bbox[0], span.bbox[1]))

    def image(self, rect: Rect, path: Path, *, wipe: bool = True) -> None:
        """Place a photo inside ``rect`` (aspect kept), clearing the placeholder first."""
        if wipe:
            self.wipe(rect)
        self._images.append((rect, path))

    def sweep(self, keep: Callable[[Span], bool], *, clip: Rect | None = None) -> list[Span]:
        """Blank every unhandled span (in ``clip``) that ``keep`` rejects; return them."""
        out = []
        for s in self.spans:
            if s.index in self.handled or not s.clean:
                continue
            if clip is not None and not _inside(s.bbox, clip):
                continue
            if keep(s):
                self.keep(s)
                continue
            self.blank(s)
            out.append(s)
        return out

    def unhandled(self) -> list[Span]:
        return [s for s in self.spans if s.clean and s.index not in self.handled]

    def commit(self) -> None:
        page = self.page
        if self._wipe:
            for rect in self._wipe:
                page.add_redact_annot(_mu.Rect(rect), fill=(1, 1, 1))
            page.apply_redactions(
                images=_mu.PDF_REDACT_IMAGE_REMOVE,
                graphics=_mu.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                text=_mu.PDF_REDACT_TEXT_REMOVE,
            )
        if self._redact:
            for rect in self._redact:
                page.add_redact_annot(_inset(rect), fill=False)
            page.apply_redactions(
                images=_mu.PDF_REDACT_IMAGE_NONE,
                graphics=_mu.PDF_REDACT_LINE_ART_NONE,
                text=_mu.PDF_REDACT_TEXT_REMOVE,
            )
        if self.reflow:
            rest = [s for s in page_spans(page) if s.text.strip() and "Wingdings" not in s.font]
            for s in rest:
                page.add_redact_annot(_inset(s.bbox), fill=False)
            if rest:
                page.apply_redactions(
                    images=_mu.PDF_REDACT_IMAGE_NONE,
                    graphics=_mu.PDF_REDACT_LINE_ART_NONE,
                    text=_mu.PDF_REDACT_TEXT_REMOVE,
                )
            for s in rest:
                text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", s.text.replace("\t", " ")).rstrip()
                if not text.strip():
                    continue
                self._writes.append(
                    _Write(
                        s.origin[0],
                        s.origin[1],
                        text,
                        s.size,
                        s.bold,
                        s.color,
                        italic="It" in s.font and not s.bold,
                    )
                )
            ordered: list[_Write] = []
            row: list[_Write] = []
            for w in sorted(self._writes, key=lambda w: w.y):
                if row and w.y - row[0].y > 2.0:
                    ordered += sorted(row, key=lambda w: w.x)
                    row = []
                row.append(w)
            self._writes = ordered + sorted(row, key=lambda w: w.x)
        for rect, color in self._fills:
            page.draw_rect(_mu.Rect(rect), color=None, fill=color, width=0)
        for (x0, y0, x1, y1), color, width in self._lines:
            page.draw_line((x0, y0), (x1, y1), color=color, width=width)
        fonts: dict[str, str] = {}

        def font_name(face: FontFace, primary: bool) -> str:
            if not primary or face.buffer is None:
                return face.fallback
            if face.name not in fonts:
                page.insert_font(fontname=face.name, fontbuffer=face.buffer)
                fonts[face.name] = face.name
            return fonts[face.name]

        for w in self._writes:
            face = self.italic if w.italic else self.bold if w.bold else self.regular
            x = w.x
            for primary, run in face.runs(w.text):
                name = font_name(face, primary)
                page.insert_text((x, w.y), run, fontsize=w.size, fontname=name, color=_rgb(w.color))
                font = face._font if primary and face._font is not None else face._fallback
                x += float(font.text_length(run, fontsize=w.size))
        for xref, _ext, _type, _base, name, *_ in page.get_fonts():
            if name in fonts:
                fix_tounicode(page.parent, xref)
        for stamp, (x, y) in self._stamps:
            x0, y0, x1, y1 = stamp.clip
            dest = _mu.Rect(x, y, x + (x1 - x0), y + (y1 - y0))
            page.show_pdf_page(dest, stamp.doc, 0, clip=_mu.Rect(stamp.clip))
        for rect, path in self._images:
            page.insert_image(_mu.Rect(rect), filename=str(path), keep_proportion=True)
        self._wipe, self._redact, self._writes, self._stamps, self._images = [], [], [], [], []
        self._lines, self._fills = [], []


def fix_tounicode(doc: Any, font_xref: int) -> bool:
    """Point a font's ToUnicode entries for U+00A0 / U+00AD at space / hyphen
    (:data:`SHARED_GLYPH_UNICODE`). Returns whether the map changed."""
    kind, value = doc.xref_get_key(font_xref, "ToUnicode")
    if kind != "xref":
        return False
    xref = int(value.split()[0])
    data = doc.xref_stream(xref).decode("latin-1")
    fixed = data
    for wrong, right in SHARED_GLYPH_UNICODE.items():
        target = f"<{ord(right):04x}>"
        for hexed in (f"<{ord(wrong):04x}>", f"<{ord(wrong):04X}>"):
            fixed = fixed.replace(hexed, target)
    if fixed == data:
        return False
    doc.update_stream(xref, fixed.encode("latin-1"))
    return True


def filled_box_around(page: Any, bbox: Rect) -> Rect:
    """The smallest filled rectangle drawn on ``page`` that contains ``bbox``'s centre
    (a photo placeholder's gray box around its caption)."""
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    best: Rect | None = None
    for drawing in page.get_drawings():
        if drawing.get("fill") is None:
            continue
        r = drawing["rect"]
        if r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 and r.width > 20 and r.height > 20:
            box = (r.x0, r.y0, r.x1, r.y1)
            if best is None or (r.width * r.height) < (best[2] - best[0]) * (best[3] - best[1]):
                best = box
    if best is None:
        raise LookupError(f"no filled box around {bbox} on page {page.number + 1}")
    return best


def _inside(bbox: Rect, clip: Rect) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return clip[0] <= cx <= clip[2] and clip[1] <= cy <= clip[3]


def _overlaps(bbox: Rect, rect: Rect) -> bool:
    return bbox[0] < rect[2] and bbox[2] > rect[0] and bbox[1] < rect[3] and bbox[3] > rect[1]


def renumber_pages(doc: Any, pattern: str, fmt: str, face: FontFace, *, first: int = 1) -> int:
    """Rewrite running page numbers (``pattern``, e.g. ``Page \\d+ of \\d+``) as
    ``fmt.format(n=..., total=...)``, counting the pages that print one from ``first``.
    A page that prints its number twice (an overprinted duplicate) gets one. Returns how
    many pages were renumbered."""
    rx = re.compile(pattern)
    numbered = [page for page in doc if any(rx.fullmatch(s.clean) for s in page_spans(page))]
    total = len(numbered) + first - 1
    for n, page in enumerate(numbered, start=first):
        editor = PageEditor(page, face)
        matches = [s for s in editor.spans if rx.fullmatch(s.clean)]
        editor.replace(matches[0], fmt.format(n=n, total=total), align="right")
        editor.blank(*matches[1:])
        editor.commit()
    return len(numbered)


def surviving_spans(
    template_page: Any,
    output_page: Any,
    kept: Collection[int],
    written: Collection[tuple[Rect, str]] = (),
    tolerance: float = 0.6,
) -> list[Span]:
    """Template spans still printed on ``output_page`` (same text at the same place) that
    were not declared kept (``kept``: indices into :func:`page_spans` of the template page).

    The output page is a copy of the template page with values replaced; any template span
    that survives without being declared is a template value left visible. Spans the
    editor wrote (``written``: :attr:`PageEditor.written`) are not template spans, even
    when the new value equals the old one at the same place."""

    def mine(s: Span) -> bool:
        cy = (s.bbox[1] + s.bbox[3]) / 2
        return any(
            " ".join(text.split()) == s.clean and abs(r[0] - s.bbox[0]) <= 1 and r[1] <= cy <= r[3]
            for r, text in written
        )

    printed = [s for s in page_spans(output_page) if s.clean and not mine(s)]
    out = []
    for t in page_spans(template_page):
        if not t.clean or t.index in kept:
            continue
        for s in printed:
            if (
                s.clean == t.clean
                and abs(s.bbox[0] - t.bbox[0]) <= tolerance
                and abs(s.origin[1] - t.origin[1]) <= tolerance
            ):
                out.append(t)
                break
    return out


# ---------------------------------------------------------------------------------------
# Package
# ---------------------------------------------------------------------------------------


def zip_package(out: Path, xml: Path, pdf: Path, images: Path | None = None) -> list[str]:
    """Write the delivery ZIP: the XML and PDF at the root, ``Images/`` beside them.

    Entries are sorted and dated 1980-01-01 so the same inputs give the same bytes."""
    entries: list[tuple[str, Path]] = [(xml.name, xml), (pdf.name, pdf)]
    if images is not None and images.is_dir():
        entries += [(f"Images/{p.name}", p) for p in sorted(images.iterdir()) if p.is_file()]
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, path in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())
    return [name for name, _ in sorted(entries)]
