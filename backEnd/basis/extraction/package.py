"""Recognize an uploaded appraisal: a PDF, a bare MISMO 3.6 XML, or a UAD 3.6 delivery ZIP.

A UAD 3.6 delivery is a ZIP holding the MISMO 3.6 XML (root ``MESSAGE``), the PDF of the report
and an ``Images`` folder. :func:`read_appraisal_upload` sniffs the bytes (never the file name or
the declared content type) and returns what the extraction needs: the XML and the PDF.

ZIP safety: at most :data:`MAX_MEMBERS` entries; a member name that is absolute, has a ``..``
segment, a drive or a backslash is refused (path traversal); encrypted members are refused;
only the XML and the PDF are read, each through a bounded reader that stops at
:data:`MAX_MEMBER_BYTES` whatever the archive header claims, and the total read is capped at
:data:`MAX_TOTAL_BYTES` (zip bombs); nothing is written to disk. The XML is parsed with
:func:`basis.extraction.mismo36.parse_mismo` (no DOCTYPE or entities).

Which PDF: the one the XML names (``VIEW_FILE/FOREIGN_OBJECT/ObjectURL``, matched by file name)
when present, else the only PDF; several PDFs and no reference is refused (ambiguous), and so is
a ZIP with no PDF at all (a UAD 3.6 delivery always carries the report PDF; a bare XML is
uploaded as an XML). :attr:`AppraisalUpload.pdf_name` is the chosen member's name.

:func:`check_declared` is the upload-confirm check (``POST .../documents/{id}/complete``): the
bytes must be what :func:`read_appraisal_upload` accepts and of the kind the declared content
type names, so a document's ``content_type`` can be trusted to tell a PDF from a UAD 3.6
delivery without reading it again.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Final, Literal
from xml.etree.ElementTree import Element

from basis.extraction.mismo36 import MISMO_NS, InvalidMismo, local, parse_mismo

MAX_MEMBERS: Final = 2000
MAX_MEMBER_BYTES: Final = 60 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 120 * 1024 * 1024
_PDF_MAGIC: Final = b"%PDF-"
_ZIP_MAGIC: Final = b"PK\x03\x04"
_CHUNK: Final = 1024 * 1024

Kind = Literal["pdf", "xml", "zip"]
# The declared content type of an appraisal upload -> the kind its bytes must be.
DECLARED_KINDS: Final[dict[str, Kind]] = {
    "application/pdf": "pdf",
    "application/zip": "zip",
    "application/xml": "xml",
    "text/xml": "xml",
}


class InvalidPackage(ValueError):
    """The upload is not a PDF, a MISMO 3.6 XML or a safe UAD 3.6 ZIP."""


@dataclass(frozen=True)
class AppraisalUpload:
    kind: Kind
    pdf: bytes | None = None
    xml: bytes | None = None
    members: tuple[str, ...] = field(default=())  # the ZIP's entries (names only)
    notes: tuple[str, ...] = ()
    pdf_name: str | None = None  # the ZIP member the PDF was read from


def is_pdf(data: bytes) -> bool:
    return _PDF_MAGIC in data[:1024]


def is_zip(data: bytes) -> bool:
    return data.startswith(_ZIP_MAGIC)


def looks_like_xml(data: bytes) -> bool:
    head = data[:512].lstrip(b"\xef\xbb\xbf \t\r\n")
    return head.startswith(b"<") or data[:2] in (b"\xff\xfe", b"\xfe\xff")


def _safe_name(name: str) -> bool:
    if not name or "\\" in name or name.startswith("/") or ":" in name.split("/")[0]:
        return False
    return ".." not in PurePosixPath(name).parts


def _read_bounded(archive: zipfile.ZipFile, info: zipfile.ZipInfo, budget: int) -> bytes:
    """The member's bytes, refusing more than :data:`MAX_MEMBER_BYTES` (or the remaining
    ``budget``) however small its header says it is."""
    limit = min(MAX_MEMBER_BYTES, budget)
    if info.file_size > limit:
        raise InvalidPackage(f"{info.filename} is larger than {limit} bytes")
    out = io.BytesIO()
    with archive.open(info) as stream:
        while chunk := stream.read(_CHUNK):
            if out.tell() + len(chunk) > limit:
                raise InvalidPackage(f"{info.filename} expands past {limit} bytes")
            out.write(chunk)
    return out.getvalue()


def _referenced_pdf(root: Element) -> str | None:
    """The file name of the PDF the XML names (``FOREIGN_OBJECT/ObjectURL``)."""
    for node in root.iter(f"{{{MISMO_NS}}}ObjectURL"):
        url = (node.text or "").strip().replace("\\", "/")
        if url.lower().endswith(".pdf"):
            return url.rsplit("/", 1)[-1]
    return None


def read_zip(data: bytes) -> AppraisalUpload:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise InvalidPackage(f"not a readable ZIP: {exc}") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            raise InvalidPackage(f"the ZIP has more than {MAX_MEMBERS} entries")
        for info in infos:
            if not _safe_name(info.filename):
                raise InvalidPackage(f"unsafe path in the ZIP: {info.filename!r}")
            if info.flag_bits & 0x1:
                raise InvalidPackage(f"encrypted entry in the ZIP: {info.filename!r}")
        files = [i for i in infos if not i.is_dir()]
        xmls = [i for i in files if i.filename.lower().endswith(".xml")]
        pdfs = [i for i in files if i.filename.lower().endswith(".pdf")]
        budget = MAX_TOTAL_BYTES
        messages: list[tuple[zipfile.ZipInfo, bytes, Element]] = []
        for info in xmls:
            body = _read_bounded(archive, info, budget)
            budget -= len(body)
            try:
                root = parse_mismo(body)
            except InvalidMismo:
                continue  # another XML (a manifest): not the appraisal
            messages.append((info, body, root))
        if len(messages) != 1:
            raise InvalidPackage(f"the ZIP holds {len(messages)} MISMO XML documents, not one")
        _, xml, root = messages[0]
        named = _referenced_pdf(root)
        chosen = [i for i in pdfs if named and i.filename.rsplit("/", 1)[-1] == named]
        if not chosen and len(pdfs) == 1:
            chosen = pdfs
        if not chosen and len(pdfs) > 1:
            raise InvalidPackage("the ZIP holds several PDFs and the XML names none of them")
        if not chosen:
            raise InvalidPackage("the ZIP holds no PDF of the report")
        pdf = _read_bounded(archive, chosen[0], budget)
        if not is_pdf(pdf):
            raise InvalidPackage(f"{chosen[0].filename} is not a PDF")
        return AppraisalUpload(
            kind="zip",
            pdf=pdf,
            xml=xml,
            members=tuple(i.filename for i in infos),
            pdf_name=chosen[0].filename,
        )


def read_appraisal_upload(data: bytes) -> AppraisalUpload:
    """What an uploaded appraisal is (module doc); :class:`InvalidPackage` otherwise."""
    if data.startswith(_ZIP_MAGIC):
        return read_zip(data)
    if is_pdf(data):
        return AppraisalUpload(kind="pdf", pdf=data)
    if looks_like_xml(data):
        try:
            root = parse_mismo(data)
        except InvalidMismo as exc:
            raise InvalidPackage(str(exc)) from None
        if local(root.tag) != "MESSAGE":  # pragma: no cover - parse_mismo checks the root
            raise InvalidPackage("not a MISMO MESSAGE")
        return AppraisalUpload(kind="xml", xml=data)
    raise InvalidPackage("not a PDF, a MISMO 3.6 XML or a UAD 3.6 ZIP")


def check_declared(data: bytes, content_type: str) -> AppraisalUpload:
    """``data`` read as an appraisal upload (:func:`read_appraisal_upload`), refused unless its
    kind is the one ``content_type`` declares (a ZIP labelled ``application/pdf`` is refused)."""
    upload = read_appraisal_upload(data)
    declared = DECLARED_KINDS.get(content_type)
    if declared != upload.kind:
        raise InvalidPackage(
            f"the file is a {upload.kind.upper()} but was declared {content_type!r}"
        )
    return upload
