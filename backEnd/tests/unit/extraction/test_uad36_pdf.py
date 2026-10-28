"""``basis.extraction.uad36_pdf``: UAD 3.6 captions and sections from the generated A-1 map.

The page texts are fabricated in the layout the GSE specification describes (section headings,
"Caption value" pairs, the page footer); no sample report content."""

from __future__ import annotations

from decimal import Decimal

from basis.extraction import uad36_pdf
from basis.extraction.azure_di import AzureDiKeyValue, AzureDiPage, AzureDiResult
from basis.extraction.field_mappings import FIELD_BY_KEY
from basis.extraction.results import ExtractedField
from basis.extraction.uad36_map import MAPPING
from basis.extraction.uad36_pdf import (
    CARRIED,
    NOT_CARRIED,
    azure_candidates,
    caption_values,
    describe,
    heading_of,
    key_for_caption,
    section_lines,
    text_candidates,
)

FOOTER = "Fannie Mae | Freddie Mac\nAppraiser Reference ID\nA1"
CONTACTS = [
    "Contact Information",
    "Client/Lender",
    "Company Name",
    "Test Bank",
    "Appraisal Management Company",
    "Company Name",
    "Test AMC",
    "ID",
    "777",
    "Appraiser",
    "Name",
    "Pat Appraiser",
    "Company Name",
    "Pat Appraisals",
    "Credentials",
    "ID",
    "LIC-1",
]


def page(*lines: str) -> str:
    return "\n".join([*lines, FOOTER])


def azure(pages: list[str], pairs: list[tuple[int, str, str | None]]) -> AzureDiResult:
    return AzureDiResult(
        pages=tuple(
            AzureDiPage(page_number=n, lines=tuple(text.splitlines()))
            for n, text in enumerate(pages, start=1)
        ),
        key_value_pairs=tuple(
            AzureDiKeyValue(key=key, value=value, confidence=0.95, page=n)
            for n, key, value in pairs
        ),
        synthetic=True,
    )


def test_carried_keys_follow_the_spec_statuses() -> None:
    for key, entry in MAPPING.items():
        assert (key in CARRIED) == (entry["status"] == "mapped"), key
    assert {"subject.tax_year", "improvements.total_rooms", "neighborhood.location"} <= NOT_CARRIED
    assert "improvements.walls" in NOT_CARRIED  # unresolved: no data point carries the box
    assert {"improvements.year_built", "reconciliation.final_market_value"} <= CARRIED


def test_headings() -> None:
    assert heading_of("SUMMARY") == 1
    assert heading_of("Subject Property (continued)") == 3
    assert heading_of("Unit Interior - Building 1 - Unit 2") == 10
    assert heading_of("Cost Approach") == 25
    assert heading_of("Year Built") is None
    assert heading_of("Site Influence") is None


def test_sections_move_forward_within_a_page() -> None:
    """The reconciliation's approach rows repeat earlier section names; a page's first heading
    may go back (the Summary after a cover's Revision History)."""
    pages = [
        page("Revision History", "Date"),
        page("SUMMARY", "Opinion of Market Value", "$1"),
        page("Reconciliation", "Approaches to Value", "Cost Approach", "Indicated Value", "$2"),
    ]
    lines = section_lines(pages)
    by_text = {(line.page, line.text): line.section for line in lines}
    assert by_text[(2, "Opinion of Market Value")] == "Summary"
    assert by_text[(3, "Cost Approach")] == "Reconciliation"
    assert by_text[(3, "Indicated Value")] == "Reconciliation"


def test_a_caption_maps_only_in_the_section_that_prints_it() -> None:
    assert key_for_caption("Year Built", "Dwelling Exterior") == "improvements.year_built"
    assert key_for_caption("Year Built", "Outbuilding") is None
    assert key_for_caption("Contract Price", "Sales Contract") == (
        "listing_and_contract.contract_price"
    )
    # the comparable grids head a column of the subject and the comparables
    assert key_for_caption("Contract Price", "Sales Comparison Approach") is None
    assert key_for_caption("Year Built", None) is None


def test_the_printed_subsection_decides_a_shared_caption() -> None:
    lines = CONTACTS
    assert key_for_caption("Company Name", "Assignment Information", lines[:2]) == (
        "subject.lender_client"
    )
    assert key_for_caption("Company Name", "Assignment Information", lines[:12]) == (
        "appraiser.company"
    )
    # the AMC's ID sits under the client subsection: not the appraiser's license
    assert key_for_caption("ID", "Assignment Information", lines[:7]) is None
    assert key_for_caption("ID", "Assignment Information", lines[:15]) == (
        "appraiser.license_number"
    )


def test_a_shared_caption_without_a_subsection_goes_to_the_key_it_names() -> None:
    assert key_for_caption("Current Owner of Public Record", "Assignment Information") == (
        "subject.owner_of_public_record"
    )
    assert key_for_caption("Borrower Name", "Assignment Information") == "subject.borrower"
    # four approaches share "Indicated Value" in the reconciliation table: none is chosen
    assert key_for_caption("Indicated Value", "Reconciliation", ["Approaches to Value"]) is None


def test_keys_read_from_several_boxes_or_table_rows_are_left_to_the_model() -> None:
    assert MAPPING["improvements.bathrooms"]["rule"] not in uad36_pdf.SINGLE_BOX_RULES
    assert key_for_caption("Total Bathrooms - Full", "Unit Interior") is None
    # a utility's Public/Private cell is a table row chosen by its utility
    assert key_for_caption("Public/Private", "Site") is None
    assert "site.utilities_water" not in uad36_pdf.readable_keys()


def test_azure_pairs_mapped_by_occurrence_section_and_subsection() -> None:
    pages = [
        page(
            "Assignment Information",
            "Borrower Name",
            "Ann Buyer",
            "Bo Buyer",
            "Current Owner of Public Record",
            "Sam Seller",
            *CONTACTS,
        ),
        page(
            "Sales Contract",
            "Known Sales Concessions",
            "Yes",
            "Total Sales Concessions",
            "$5,000",
            "Contract Price",
            "$400,000",
        ),
        page("Dwelling Exterior", "Year Built", "1999", "Outbuilding", "Year Built", "2015"),
    ]
    pairs: list[tuple[int, str, str | None]] = [
        (1, "Borrower Name", "Ann Buyer\nBo Buyer"),
        (1, "Current Owner of Public Record", "Sam Seller"),
        (1, "Company Name", "Test Bank"),
        (1, "Company Name", "Test AMC"),
        (1, "ID", "777"),
        (1, "Name", "Pat Appraiser"),
        (1, "Company Name", "Pat Appraisals"),
        (1, "ID", "LIC-1"),
        (2, "Known Sales Concessions", "Yes"),
        (2, "Total Sales Concessions", "$5,000"),
        (2, "Contract Price", "$400,000"),
        (2, "Contract Price", ":selected:"),  # a bare mark is no value
        (3, "Year Built", "1999"),
        (3, "Year Built", "2015"),
    ]
    found = {c.key: c for c in azure_candidates(azure(pages, pairs))}
    values = {key: c.raw_text for key, c in found.items()}
    assert values == {
        "subject.borrower": "Ann Buyer; Bo Buyer",
        "subject.owner_of_public_record": "Sam Seller",
        "subject.lender_client": "Test Bank",
        "appraiser.name": "Pat Appraiser",
        "appraiser.company": "Pat Appraisals",
        "appraiser.license_number": "LIC-1",
        # "Yes" does not parse as an amount: the total printed after it is the value
        "listing_and_contract.seller_concessions": "$5,000",
        "listing_and_contract.contract_price": "$400,000",
        "improvements.year_built": "1999",  # the outbuilding's is not the dwelling's
    }
    assert {c.source for c in found.values()} == {"azure"}
    assert found["improvements.year_built"].page == 3


def test_a_per_unit_box_is_read_only_when_one_unit_interior_is_printed() -> None:
    one = [page("Unit Interior", "Finished Above Grade", "1,500 Sq. Ft.")]
    pairs: list[tuple[int, str, str | None]] = [(1, "Finished Above Grade", "1,500 Sq. Ft.")]
    assert [c.key for c in azure_candidates(azure(one, pairs))] == ["improvements.gla_sqft"]
    two = [
        page("Unit Interior - Building 1 - Unit 1", "Finished Above Grade", "800 Sq. Ft."),
        page("Unit Interior - Building 1 - Unit 2", "Finished Above Grade", "700 Sq. Ft."),
    ]
    pairs = [(1, "Finished Above Grade", "800 Sq. Ft."), (2, "Finished Above Grade", "700 Sq. Ft.")]
    assert azure_candidates(azure(two, pairs)) == []


def test_text_layer_pairs_run_to_the_next_caption_and_keep_typed_values_only() -> None:
    pairs = caption_values(
        "Opinion of Market Value $491,000 Market Value Condition Subject to Repair"
    )
    assert pairs[0] == ("Opinion of Market Value", "$491,000")
    # "Subject" is an A-1 caption too: a free-text value's extent cannot be told from the
    # captions, which is why the pass keeps typed values only
    assert ("Market Value Condition", "") in pairs
    pages = [
        page(
            "SUMMARY",
            "Opinion of Market Value $491,000 Market Value Condition Subject to Repair",
            "Effective Date of Appraisal 09/20/2019 Property Valuation Method Traditional",
            "Borrower Name Ann Buyer Yes No",
        ),
        page("Dwelling Exterior", "Dwelling Style Colonial Year Built 2004 Effective Age ten"),
    ]
    found = {c.key: c for c in text_candidates(pages)}
    assert {k: c.raw_text for k, c in found.items()} == {
        "reconciliation.final_market_value": "$491,000",
        "reconciliation.effective_date_of_appraisal": "09/20/2019",
        "improvements.year_built": "2004",
    }
    item = ExtractedField.from_candidate(found["reconciliation.final_market_value"])
    assert item.value_num == Decimal("491000") and item.source == "regex"


def test_fallback_descriptions_name_the_uad36_caption_and_section() -> None:
    text = describe("improvements.year_built")
    assert text == 'means "Year Built"; UAD 3.6 caption "Year Built" in Dwelling Exterior'
    borrower = describe("subject.borrower")
    assert '"Borrower Name"' in borrower and "Current Owner" not in borrower
    assert "Current Owner of Public Record" in describe("subject.owner_of_public_record")
    # the comparable grids only when nothing else prints the value
    assert "Sales Comparison Approach" not in describe("subject.property_address")
    for key in CARRIED:
        assert describe(key).startswith(f'means "{FIELD_BY_KEY[key].label}"'), key


def test_the_generated_map_carries_the_report_layout() -> None:
    from basis.extraction.uad36_map import LABELS, PLACES, SECTIONS

    assert [n for n, _ in SECTIONS] == list(range(1, 30))
    assert SECTIONS[0] == (1, "Summary") and SECTIONS[-1] == (29, "Certifications")
    assert {"Year Built", "Fannie Mae | Freddie Mac", "Company Name"} <= set(LABELS)
    assert all(isinstance(place[0], int) and len(place) == 4 for place in PLACES)
    for key in CARRIED:
        assert MAPPING[key]["locations"], key
