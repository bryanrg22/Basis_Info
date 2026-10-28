"""Which URAR an appraisal PDF is: the legacy 1004 (March 2005) or the redesigned UAD 3.6 URAR.

The two forms print the same facts in different places, under different captions, and the
UAD 3.6 URAR (mandatory from 2026-11-02) prints its answers as text where the 1004 has
checkboxes, so the PDF extraction reads each with its own captions
(:mod:`basis.extraction.uad36_pdf`). The form is recognized by what each prints on every
page, not by its content:

* **legacy 1004**: the form footer "Freddie Mac Form 70 March 2005" / "Fannie Mae Form 1004
  March 2005" (also with "UAD Version 9/2011" between them).
* **UAD 3.6 URAR**: the page footer the GSE URAR Delivery Specification (Appendix A-1
  v1.4, section "Header and Footer") prints on every report page: "Fannie Mae | Freddie Mac"
  (HF.003) together with "Appraiser Reference ID" (HF.005) or "Client Reference ID"
  (HF.007). The brand line alone is not enough (a 1004's certification can name both
  agencies); the reference IDs are printed only in the redesigned footer.

A page counts for a form when any of the texts given for it (the PDF text layer, Azure DI's
page text) carries the form's footer. The document is UAD 3.6 when at least
:data:`MIN_UAD36_PAGES` pages carry the UAD 3.6 footer and more pages carry it than the 1004
footer; it is the 1004 when any page carries the 1004 footer; otherwise it is ``unknown``,
which the pipeline treats as the 1004 (today's behaviour). A scan without Azure DI has no
text and is ``unknown``. A one-page fragment of a redesigned report is ``unknown`` on purpose:
two pages keep a stray quotation of the footer from switching the extraction.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

Form = Literal["urar_1004_2005", "uad36_urar", "unknown"]
FORM_1004: Final = "urar_1004_2005"
FORM_UAD36: Final = "uad36_urar"
FORM_UNKNOWN: Final = "unknown"
MIN_UAD36_PAGES: Final = 2

_LEGACY_FOOTER: Final = re.compile(
    r"\b(?:Fannie\s+Mae\s+Form\s+1004|Freddie\s+Mac\s+Form\s+70)\s+March\s+2005\b",
    re.IGNORECASE,
)
_UAD36_BRAND: Final = re.compile(r"\bFannie\s+Mae\s*[|¦│]\s*Freddie\s+Mac\b", re.IGNORECASE)
_UAD36_REFERENCE: Final = re.compile(r"\b(?:Appraiser|Client)\s+Reference\s+ID\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FormDetection:
    form: Form
    pages: int
    uad36_pages: tuple[int, ...]  # 1-based pages carrying the UAD 3.6 footer
    legacy_pages: tuple[int, ...]  # 1-based pages carrying the 1004 footer

    @property
    def is_uad36(self) -> bool:
        return self.form == FORM_UAD36

    def as_json(self) -> dict[str, Any]:
        return {
            "form": self.form,
            "pages": self.pages,
            "uad36_pages": list(self.uad36_pages),
            "legacy_pages": list(self.legacy_pages),
        }


def is_legacy_page(text: str) -> bool:
    return _LEGACY_FOOTER.search(text) is not None


def is_uad36_page(text: str) -> bool:
    return _UAD36_BRAND.search(text) is not None and _UAD36_REFERENCE.search(text) is not None


def detect_form(*sources: Sequence[str]) -> FormDetection:
    """The form of a document given one or more page-text sequences of it (module doc)."""
    pages = max((len(s) for s in sources), default=0)
    uad36: list[int] = []
    legacy: list[int] = []
    for index in range(pages):
        texts = [s[index] for s in sources if index < len(s) and s[index]]
        if any(is_uad36_page(t) for t in texts):
            uad36.append(index + 1)
        if any(is_legacy_page(t) for t in texts):
            legacy.append(index + 1)
    form: Form
    if len(uad36) >= MIN_UAD36_PAGES and len(uad36) > len(legacy):
        form = FORM_UAD36
    elif legacy:
        form = FORM_1004
    else:
        form = FORM_UNKNOWN
    return FormDetection(form, pages, tuple(uad36), tuple(legacy))
