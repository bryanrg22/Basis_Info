"""``basis.extraction.forms``: legacy 1004 vs UAD 3.6 URAR from the printed page footers."""

from __future__ import annotations

from basis.extraction.forms import (
    FORM_1004,
    FORM_UAD36,
    FORM_UNKNOWN,
    detect_form,
    is_legacy_page,
    is_uad36_page,
)

LEGACY = (
    "Gross Living Area 1,850\n"
    "Freddie Mac Form 70 March 2005 Page 1 of 6 Fannie Mae Form 1004 March 2005"
)
LEGACY_UAD26 = (
    "Freddie Mac Form 70 March 2005 UAD Version 9/2011 Page 2 of 6 Fannie Mae Form 1004 March 2005"
)
UAD36 = (
    "Year Built\n2004\nAppraisal Version #4\nFannie Mae | Freddie Mac\nSeptember 2024\n"
    "Appraiser Reference ID\nAA12345\nClient Reference ID\n1234567"
)
UAD36_ONE_LINE = (
    "Appraisal Version Appraiser Reference ID 12ab Fannie Mae | Freddie Mac September 2024"
)
COVER = (
    "This cover page re-expresses an appraisal issued on the legacy Uniform Residential "
    "Appraisal Report (Fannie Mae Form 1004 / Freddie Mac Form 70) in the redesigned layout."
)


def test_page_footers() -> None:
    assert is_legacy_page(LEGACY) and is_legacy_page(LEGACY_UAD26)
    assert is_uad36_page(UAD36) and is_uad36_page(UAD36_ONE_LINE)
    assert not is_uad36_page(LEGACY) and not is_legacy_page(UAD36)
    # naming both agencies, or the 1004 without its edition date, is not a footer
    assert not is_uad36_page("Fannie Mae | Freddie Mac guidelines apply")
    assert not is_uad36_page("Appraiser Reference ID 1 (Fannie Mae and Freddie Mac)")
    assert not is_legacy_page(COVER)


def test_a_legacy_1004() -> None:
    found = detect_form(["", LEGACY, LEGACY_UAD26, "addendum"])
    assert found.form == FORM_1004 and found.legacy_pages == (2, 3) and found.uad36_pages == ()
    assert not found.is_uad36


def test_a_uad36_report_with_a_cover_page_that_names_the_1004() -> None:
    found = detect_form([COVER, UAD36, UAD36, UAD36])
    assert found.form == FORM_UAD36 and found.uad36_pages == (2, 3, 4)
    assert found.as_json() == {
        "form": FORM_UAD36,
        "pages": 4,
        "uad36_pages": [2, 3, 4],
        "legacy_pages": [],
    }


def test_unknown_falls_back_and_one_page_is_not_enough() -> None:
    assert detect_form([]).form == FORM_UNKNOWN
    assert detect_form(["", ""]).form == FORM_UNKNOWN  # a scan without Azure DI
    assert detect_form(["some letter", UAD36]).form == FORM_UNKNOWN  # MIN_UAD36_PAGES
    assert detect_form(["Uniform Residential Appraisal Report"]).form == FORM_UNKNOWN


def test_the_majority_decides_a_mixed_document() -> None:
    assert detect_form([LEGACY, LEGACY, LEGACY, UAD36, UAD36]).form == FORM_1004
    assert detect_form([LEGACY, UAD36, UAD36, UAD36]).form == FORM_UAD36


def test_any_text_source_of_a_page_counts() -> None:
    """A scan has no text layer; Azure DI's page text carries the footer."""
    text_layer = ["", "", ""]
    azure = [UAD36, UAD36, ""]
    assert detect_form(text_layer).form == FORM_UNKNOWN
    found = detect_form(text_layer, azure)
    assert found.form == FORM_UAD36 and found.pages == 3 and found.uad36_pages == (1, 2)
