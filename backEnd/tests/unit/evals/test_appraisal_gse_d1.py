"""``evals.appraisal.gse_d1`` on a fabricated MISMO-like report and a fabricated PDF.

Nothing here comes from the GSE samples: the XML below is invented with the element names
the map reads, and the PDF is drawn with pymupdf (white bold section headings like the
redesigned URAR's, a cover page carrying the "not part of the URAR" mark). The tests cover
the element map, the SPEC section 4 conversions, the statuses (absent, not developed,
unscorable), page finding (section first, caption next to the value, cover skipped, other
pages as ``also_on``), a print that differs from the XML, and the arithmetic issues.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from basis.extraction.field_mappings import FIELDS
from evals.appraisal import gse_d1
from evals.appraisal.truth import TruthSet, load_truth

NS = "http://www.mismo.org/residential/2009/schemas"
XLINK = "http://www.w3.org/1999/xlink"


def unit_xml(bedrooms: int, occupancy: str, gla: int, below_fin: int, below_unf: int) -> str:
    return f"""
      <PROPERTY_UNIT>
        <PROPERTY_UNIT_AREA>
          <UnitStandardAboveGradeFinishedAreaMeasure>{gla}</UnitStandardAboveGradeFinishedAreaMeasure>
          <UnitStandardBelowGradeFinishedAreaMeasure>{below_fin}</UnitStandardBelowGradeFinishedAreaMeasure>
          <UnitBelowGradeUnfinishedAreaMeasure>{below_unf}</UnitBelowGradeUnfinishedAreaMeasure>
        </PROPERTY_UNIT_AREA>
        <PROPERTY_UNIT_DETAIL>
          <AccessoryDwellingUnitIndicator>false</AccessoryDwellingUnitIndicator>
          <BedroomCount>{bedrooms}</BedroomCount>
          <FullBathroomCount>2</FullBathroomCount>
          <HalfBathroomCount>1</HalfBathroomCount>
          <UnitOccupancyType>{occupancy}</UnitOccupancyType>
        </PROPERTY_UNIT_DETAIL>
      </PROPERTY_UNIT>"""


def report_xml(
    *,
    cost: bool = True,
    indicated: int = 425_000,
    units: int = 1,
    zoning: str = "Residential single family",
) -> str:
    unit_parts = [unit_xml(4, "OwnerOccupied", 2000, 800, 200)]
    if units == 2:
        unit_parts.append(unit_xml(2, "Tenant", 900, 0, 0))
    cost_xml = (
        f"""
        <COST_APPROACH>
          <COST_APPROACH_DETAIL>
            <DepreciatedCostDwellingsTotalAmount>295000</DepreciatedCostDwellingsTotalAmount>
            <SiteEstimatedValueAmount>100000</SiteEstimatedValueAmount>
            <SiteOtherImprovementsAsIsAmount>30000</SiteOtherImprovementsAsIsAmount>
            <SiteValuationMethodType>SalesComparison</SiteValuationMethodType>
            <ValueIndicatedByCostApproachAmount>{indicated}</ValueIndicatedByCostApproachAmount>
          </COST_APPROACH_DETAIL>
          <IMPROVEMENT_COST_AND_DEPRECIATED_VALUES>
            <IMPROVEMENT_COST_AND_DEPRECIATED_VALUE xlink:label="COST_DWELLING">
              <IMPROVEMENT_COST>
                <REPLACEMENT_REPRODUCTION_COSTS>
                  <REPLACEMENT_REPRODUCTION_COST>
                    <PricePerAreaAmount>150.004</PricePerAreaAmount>
                    <ReplacementOrReproductionCostAmount>300008</ReplacementOrReproductionCostAmount>
                    <ReplacementOrReproductionCostAreaMeasure>2000</ReplacementOrReproductionCostAreaMeasure>
                    <ReplacementOrReproductionCostAreaType>StandardFinishedAreaAboveGrade</ReplacementOrReproductionCostAreaType>
                  </REPLACEMENT_REPRODUCTION_COST>
                  <REPLACEMENT_REPRODUCTION_COST>
                    <PricePerAreaAmount>50</PricePerAreaAmount>
                    <ReplacementOrReproductionCostAmount>24000</ReplacementOrReproductionCostAmount>
                    <ReplacementOrReproductionCostAreaMeasure>480</ReplacementOrReproductionCostAreaMeasure>
                    <ReplacementOrReproductionCostAreaType>AttachedGarage</ReplacementOrReproductionCostAreaType>
                  </REPLACEMENT_REPRODUCTION_COST>
                </REPLACEMENT_REPRODUCTION_COSTS>
              </IMPROVEMENT_COST>
              <IMPROVEMENT_DEPRECIATED_VALUE_SUMMARY>
                <ImprovementEstimatedTotalCostAmount>295000</ImprovementEstimatedTotalCostAmount>
              </IMPROVEMENT_DEPRECIATED_VALUE_SUMMARY>
              <IMPROVEMENT_DEPRECIATION><DepreciationTotalAmount>29008</DepreciationTotalAmount></IMPROVEMENT_DEPRECIATION>
            </IMPROVEMENT_COST_AND_DEPRECIATED_VALUE>
          </IMPROVEMENT_COST_AND_DEPRECIATED_VALUES>
        </COST_APPROACH>"""
        if cost
        else """
        <COST_APPROACH>
          <VALUATION_APPROACH_EXCLUSION_REASONS><VALUATION_APPROACH_EXCLUSION_REASON>
            <ValuationApproachExclusionReasonType>NotNecessaryForCredibleResults</ValuationApproachExclusionReasonType>
          </VALUATION_APPROACH_EXCLUSION_REASON></VALUATION_APPROACH_EXCLUSION_REASONS>
        </COST_APPROACH>"""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<MESSAGE xmlns="{NS}" xmlns:xlink="{XLINK}">
 <SERVICES><SERVICE>
  <PARTIES>
   <PARTY><INDIVIDUAL><NAME><FirstName>Pat</FirstName><LastName>Buyer</LastName></NAME></INDIVIDUAL>
     <ROLES><ROLE><ROLE_DETAIL><PartyRoleType>Borrower</PartyRoleType></ROLE_DETAIL></ROLE></ROLES></PARTY>
   <PARTY><LEGAL_ENTITY><LEGAL_ENTITY_DETAIL><FullName>Test Lending</FullName>
     </LEGAL_ENTITY_DETAIL></LEGAL_ENTITY>
     <ROLES><ROLE><ROLE_DETAIL><PartyRoleType>Lender</PartyRoleType></ROLE_DETAIL></ROLE></ROLES></PARTY>
  </PARTIES>
  <VALUATION_ANALYSIS>
   <PROPERTIES>
    <PROPERTY ValuationUseType="SubjectProperty">
     <ADDRESS><AddressLineText>9 Test Ln</AddressLineText><CityName>Sampleton</CityName>
       <StateCode>ZZ</StateCode><PostalCode>00001</PostalCode></ADDRESS>
     <CAR_STORAGES><CAR_STORAGE><CAR_STORAGE_DETAIL>
       <CarStorageAreaMeasure>480</CarStorageAreaMeasure><CarStorageAttachmentType>Attached</CarStorageAttachmentType>
       <CarStorageType>Garage</CarStorageType><ParkingSpacesCount>2</ParkingSpacesCount>
     </CAR_STORAGE_DETAIL></CAR_STORAGE></CAR_STORAGES>
     <IMPROVEMENTS><IMPROVEMENT>
       <IMPROVEMENT_DETAIL><ImprovementType>Dwelling</ImprovementType>
         <PropertyStructureBuiltYear>1999</PropertyStructureBuiltYear></IMPROVEMENT_DETAIL>
       <PROPERTY_UNITS>{"".join(unit_parts)}</PROPERTY_UNITS>
       <SYSTEM><HEATING_SYSTEMS><HEATING_SYSTEM><HeatingFuelType>NaturalGas</HeatingFuelType>
         <HeatingSystemType>ForcedWarmAir</HeatingSystemType></HEATING_SYSTEM></HEATING_SYSTEMS></SYSTEM>
     </IMPROVEMENT></IMPROVEMENTS>
     <PROPERTY_DETAIL><AttachmentType>Detached</AttachmentType>
       <LivingUnitExcludingADUCount>{units}</LivingUnitExcludingADUCount>
       <AccessoryDwellingUnitTotalCount>0</AccessoryDwellingUnitTotalCount>
       <TotalFinishedAreaOfAllLivingUnitsIncludingADUAreaMeasure>2900</TotalFinishedAreaOfAllLivingUnitsIncludingADUAreaMeasure>
     </PROPERTY_DETAIL>
     <SALES_CONTRACTS><SALES_CONTRACT><SALES_CONTRACT_DETAIL>
       <SalesConcessionIndicator>false</SalesConcessionIndicator>
       <SalesContractAmount>480000</SalesContractAmount><SalesContractDate>2020-06-01</SalesContractDate>
       <SalesContractExistsIndicator>true</SalesContractExistsIndicator>
     </SALES_CONTRACT_DETAIL></SALES_CONTRACT></SALES_CONTRACTS>
     <SITE>
       <SITE_SIZE_LAYOUT>
         <LotSizeAreaMeasure AreaUnitOfMeasureType="Acres">2.5</LotSizeAreaMeasure>
       </SITE_SIZE_LAYOUT>
       <SITE_ZONING><SITE_ZONING_DETAIL>
         <SiteZoningClassificationDescription>{zoning}</SiteZoningClassificationDescription>
         <SiteZoningComplianceType>LegalNonConforming</SiteZoningComplianceType>
       </SITE_ZONING_DETAIL></SITE_ZONING>
     </SITE>
    </PROPERTY>
   </PROPERTIES>
   <VALUATION_REPORT>
    <APPROACH_TO_VALUE>{cost_xml}
     <INCOME_APPROACH>
       <VALUATION_APPROACH_EXCLUSION_REASONS><VALUATION_APPROACH_EXCLUSION_REASON>
         <ValuationApproachExclusionReasonType>NotNecessaryForCredibleResults</ValuationApproachExclusionReasonType>
       </VALUATION_APPROACH_EXCLUSION_REASON></VALUATION_APPROACH_EXCLUSION_REASONS>
     </INCOME_APPROACH>
     <SALES_COMPARISON_APPROACH><SALES_COMPARISON_APPROACH_DETAIL>
       <ValueIndicatedBySalesComparisonAmount>500000</ValueIndicatedBySalesComparisonAmount>
     </SALES_COMPARISON_APPROACH_DETAIL></SALES_COMPARISON_APPROACH>
    </APPROACH_TO_VALUE>
    <SCOPE_OF_WORK><SCOPE_OF_WORK_DETAIL>
      <CostApproachIndicator>{"true" if cost else "false"}</CostApproachIndicator>
      <IncomeApproachIndicator>false</IncomeApproachIndicator>
      <ValuationAssignmentType>Purchase</ValuationAssignmentType>
    </SCOPE_OF_WORK_DETAIL></SCOPE_OF_WORK>
    <VALUATION_RECONCILIATION><VALUATION_RECONCILIATION_SUMMARY>
      <VALUATION_CONDITIONS><VALUATION_CONDITION>
        <PropertyValuationConditionalConclusionType>AsIs</PropertyValuationConditionalConclusionType>
      </VALUATION_CONDITION></VALUATION_CONDITIONS>
      <VALUATION_RECONCILIATION_SUMMARY_DETAIL>
        <AppraisalReportEffectiveDate>2020-07-01</AppraisalReportEffectiveDate>
        <OpinionOfValueAmount>500000</OpinionOfValueAmount>
      </VALUATION_RECONCILIATION_SUMMARY_DETAIL>
    </VALUATION_RECONCILIATION_SUMMARY></VALUATION_RECONCILIATION>
   </VALUATION_REPORT>
  </VALUATION_ANALYSIS>
 </SERVICE></SERVICES>
</MESSAGE>
"""


# Each page: (heading or None, lines). A heading is drawn white and bold like the URAR's.
Page = list[tuple[str | None, list[str]]]


def pdf_pages(*, zoning: str = "Residential single family") -> list[Page]:
    return [
        [(None, ["Fabricated appraisal for tests", "This cover is not part of the URAR."])],
        # summary page: the same facts repeated (also_on), and a contract price decoy
        [(None, ["Opinion of Market Value $500,000", "Contract Price $480,000"])],
        [
            ("Subject Property", ["Physical Address 9 Test Ln", "Sampleton, ZZ 00001"]),
            ("Site", ["Total Site Size 2.5 Acres", "Compliance Legal Nonconforming"]),
            ("Site", [f"Classification Code Description {zoning}"]),
            ("Sales Contract", ["Contract Price $480,000", "Contract Date 06/01/2020"]),
            ("Sales Contract", ["Known Sales Concessions No"]),
        ],
        [
            (
                "Unit Interior",
                [
                    "Finished Above Grade 2,000 Sq. Ft.",
                    "Finished Below Grade 800 Sq. Ft.",
                    "Unfinished Below Grade 200 Sq. Ft.",
                    "Occupancy Owner",
                    "Total Bedrooms 4",
                    "Total Bathrooms - Full 2",
                    "Total Bathrooms - Half 1",
                ],
            ),
            ("Vehicle Storage", ["Garage 2 Attached", "480 Sq. Ft."]),
        ],
        [
            (
                "Cost Approach",
                [
                    "Indicated Value by Cost Approach $425,000",
                    "Depreciated Cost of Dwellings $295,000",
                    "As Is Value of Site Improvements $30,000",
                    "Opinion of Site Value $100,000",
                    "Above Grade Finished Area 2,000 Sq. Ft. @ $150.00 $300,008",
                    "Attached Garage 480 Sq. Ft. @ $50.00 $24,000",
                    "Total Depreciation $(29,008)",
                ],
            ),
            (
                "Reconciliation",
                [
                    "Indicated Value $500,000 $425,000",
                    "Reason for Exclusion Not Necessary for Credible Results",
                    "Opinion of Market Value $500,000",
                    "Effective Date of Appraisal 07/01/2020",
                    "Market Value Condition As Is",
                ],
            ),
        ],
    ]


def write_pdf(path: Path, pages: list[Page]) -> None:
    document = pymupdf.open()
    for blocks in pages:
        page = document.new_page(width=612, height=1008)
        y = 60.0
        for heading, lines in blocks:
            if heading:
                page.draw_rect(
                    pymupdf.Rect(36, y - 11, 300, y + 3), color=(0, 0, 0), fill=(0, 0, 0)
                )
                page.insert_text((42, y), heading, fontname="hebo", fontsize=9, color=(1, 1, 1))
                y += 20
            for line in lines:
                page.insert_text((40, y), line, fontname="helv", fontsize=9)
                y += 14
            y += 10
    document.save(path)
    document.close()


def write_scenario(root: Path, name: str, xml: str, pages: list[Page]) -> Path:
    folder = root / "appraisal" / "gse_d1" / name
    folder.mkdir(parents=True)
    (folder / "appraisal.xml").write_text(xml)
    write_pdf(folder / "appraisal.pdf", pages)
    return folder


def build(tmp_path: Path, xml: str, pages: list[Page] | None = None) -> gse_d1.DocumentTruth:
    folder = write_scenario(tmp_path, "T1", xml, pages or pdf_pages())
    return gse_d1.build_document(
        "T1", folder / "appraisal.xml", folder / "appraisal.pdf", "gse_d1/T1/appraisal.pdf"
    )


def test_every_catalogue_key_and_the_set_validates(tmp_path: Path) -> None:
    doc = build(tmp_path, report_xml())
    keys = {f.key for f in doc.fields}
    assert {s.key for s in FIELDS} <= keys
    TruthSet(set="gse_d1", documents=(doc,))  # validates
    assert doc.form == "uad36_urar" and doc.source == "gse_d1" and doc.split == "dev"
    assert doc.pages == 5


def test_values_types_and_pages(tmp_path: Path) -> None:
    doc = build(tmp_path, report_xml())
    price = doc.field("listing_and_contract.contract_price")
    # the Sales Contract box on page 3, not the summary page; the summary is also_on
    assert (price.status, price.type, price.value) == ("present", "currency", 480000)
    assert price.page == 3 and price.also_on == (2,) and price.verified
    assert price.printed == "$480,000" and "Contract Price" in (price.quote or "")
    assert doc.field("listing_and_contract.contract_date").value == "2020-06-01"
    area = doc.field("site.area_sqft")
    assert area.type == "area_sqft"
    assert area.value == {"sqft": round(2.5 * 43560), "printed_unit": "ac"}
    assert doc.field("site.area_acres").value == 2.5
    assert doc.field("site.zoning_compliance").value == "legal_nonconforming"
    assert doc.field("improvements.bathrooms").value == {"full": 2, "half": 1}
    assert doc.field("improvements.gla_sqft").value == 2000
    assert doc.field("improvements.heating_type").value == ["fwa"]
    assert doc.field("improvements.car_storage").value == ["attached", "garage"]
    assert doc.field("subject.occupant").value == "owner"
    assert doc.field("subject.borrower").value == "Pat Buyer"
    final = doc.field("reconciliation.final_market_value")
    assert final.page == 5 and final.also_on == (2,)
    assert doc.field("reconciliation.effective_date_of_appraisal").value == "2020-07-01"
    assert doc.field("reconciliation.appraisal_made").value == "as_is"


def test_concessions_no_is_present_zero_unverified(tmp_path: Path) -> None:
    field = build(tmp_path, report_xml()).field("listing_and_contract.seller_concessions")
    assert (field.status, field.value, field.printed, field.verified) == ("present", 0, "No", False)


def test_derived_basement_is_unverified_with_a_note(tmp_path: Path) -> None:
    doc = build(tmp_path, report_xml())
    basement = doc.field("improvements.basement_area_sqft")
    assert basement.value == 1000 and not basement.verified and "derived" in (basement.note or "")
    finish = doc.field("improvements.basement_finish_percent")
    assert finish.value == 80 and finish.tolerance == 1.0
    extra = doc.field("improvements.below_grade_unfinished_sqft")
    assert extra.extra and extra.value == 200


def test_no_basement_is_a_printed_zero(tmp_path: Path) -> None:
    """No below-grade area: the form prints "Finished Below Grade 0 Sq. Ft." and the legacy
    box took a 0 (UAD Appendix D): present with 0, not ``not_developed``."""
    xml = report_xml().replace(
        "<UnitStandardBelowGradeFinishedAreaMeasure>800<",
        "<UnitStandardBelowGradeFinishedAreaMeasure>0<",
    )
    xml = xml.replace(
        "<UnitBelowGradeUnfinishedAreaMeasure>200<", "<UnitBelowGradeUnfinishedAreaMeasure>0<"
    )
    pages = pdf_pages()
    lines = pages[3][0][1]
    lines[1] = "Finished Below Grade 0 Sq. Ft."
    lines[2] = "Unfinished Below Grade 0 Sq. Ft."
    doc = build(tmp_path, xml, pages)
    basement = doc.field("improvements.basement_area_sqft")
    assert (basement.status, basement.type, basement.value) == ("present", "area_sqft", 0)
    assert basement.printed == "0 Sq. Ft." and basement.page == 4 and basement.verified
    finish = doc.field("improvements.basement_finish_percent")
    assert (finish.status, finish.type, finish.value) == ("present", "percent", 0)
    assert not finish.verified and "no percent printed" in (finish.note or "")


EARLIER_LISTING = """
      <LISTING_INFORMATION><LISTING_INFORMATION_DETAIL>
        <DaysOnMarketCount>60</DaysOnMarketCount><FinalListPriceAmount>650000</FinalListPriceAmount>
        <ListingStartDate>2020-01-10</ListingStartDate><ListingStatusType>OffMarket</ListingStatusType>
      </LISTING_INFORMATION_DETAIL></LISTING_INFORMATION>"""
CURRENT_LISTING = """
      <LISTING_INFORMATION><LISTING_INFORMATION_DETAIL>
        <DaysOnMarketCount>21</DaysOnMarketCount><FinalListPriceAmount>495000</FinalListPriceAmount>
        <ListingStartDate>2020-05-11</ListingStartDate><ListingStatusType>Pending</ListingStatusType>
      </LISTING_INFORMATION_DETAIL></LISTING_INFORMATION>"""
LISTING_SUMMARY = """
     <LISTING_INFORMATION_SUMMARY>
       <CumulativeDaysOnMarketCount>81</CumulativeDaysOnMarketCount>
       <ListedWithinPreviousYearIndicator>true</ListedWithinPreviousYearIndicator>
     </LISTING_INFORMATION_SUMMARY>"""


def with_listings(*listings: str, summary: str = "") -> str:
    block = f"<LISTING_INFORMATIONS>{''.join(listings)}</LISTING_INFORMATIONS>{summary}"
    return report_xml().replace("<SALES_CONTRACTS>", block + "<SALES_CONTRACTS>", 1)


def listing_facts(xml: str) -> tuple[object, ...]:
    keys = ("offering_price", "listing_date", "days_on_market")
    return tuple(derive(xml, f"listing_and_contract.{k}").value for k in keys)


def test_listing_is_the_current_one_and_its_own_dom() -> None:
    """Two listings (an earlier one taken off the market, then the current one): the offering
    price, start date and DOM are the current listing's, not the first in document order, and
    the DOM is that listing's (DaysOnMarketCount), not the Total DOM."""
    xml = with_listings(EARLIER_LISTING, CURRENT_LISTING, summary=LISTING_SUMMARY)
    assert listing_facts(xml) == (495000, "2020-05-11", 21)
    assert "not the Total DOM" in (derive(xml, "listing_and_contract.days_on_market").note or "")
    # document order does not matter: the latest start date is the current listing
    swapped = with_listings(CURRENT_LISTING, EARLIER_LISTING, summary=LISTING_SUMMARY)
    assert listing_facts(swapped) == (495000, "2020-05-11", 21)


def test_single_listing_and_no_listing() -> None:
    one = (
        "<LISTING_INFORMATION><LISTING_INFORMATION_DETAIL>"
        "<DaysOnMarketCount>0</DaysOnMarketCount><FinalListPriceAmount>480000"
        "</FinalListPriceAmount><ListingStatusType>Pending</ListingStatusType>"
        "</LISTING_INFORMATION_DETAIL></LISTING_INFORMATION>"
    )
    xml = with_listings(one)
    assert derive(xml, "listing_and_contract.offering_price").value == 480000
    dom = derive(xml, "listing_and_contract.days_on_market")
    assert (dom.status, dom.value, dom.note) == ("present", 0, None)  # a 0 DOM is a value
    assert derive(xml, "listing_and_contract.listing_date").status == "absent"
    for key in ("offering_price", "listing_date", "days_on_market"):
        assert derive(report_xml(), f"listing_and_contract.{key}").status == "absent", key


HOA_SPECIAL_ASSESSMENT = """
     <HOMEOWNERS_ASSOCIATIONS><HOMEOWNERS_ASSOCIATION><ASSOCIATION_CHARGES>
       <ASSOCIATION_CHARGE><ASSOCIATION_CHARGE_DETAIL>
         <AssociationChargeBalanceAmount>1900</AssociationChargeBalanceAmount>
         <AssociationChargeType>AssociationSpecialAssessment</AssociationChargeType>
         <AssociationSpecialAssessmentStatusType>Existing</AssociationSpecialAssessmentStatusType>
       </ASSOCIATION_CHARGE_DETAIL></ASSOCIATION_CHARGE>
     </ASSOCIATION_CHARGES></HOMEOWNERS_ASSOCIATION></HOMEOWNERS_ASSOCIATIONS>"""


def with_special(tax: str | None, hoa: bool) -> str:
    block = HOA_SPECIAL_ASSESSMENT if hoa else ""
    if tax is not None:
        block += (
            "<PROPERTY_TAXES><PROPERTY_TAX><PROPERTY_TAX_DETAIL><SpecialTaxAssessmentsIndicator>"
            f"{tax}</SpecialTaxAssessmentsIndicator></PROPERTY_TAX_DETAIL></PROPERTY_TAX>"
            "</PROPERTY_TAXES>"
        )
    return report_xml().replace("<SALES_CONTRACTS>", block + "<SALES_CONTRACTS>", 1)


def test_special_assessments_is_the_special_tax_assessment() -> None:
    """The key is the Subject section's Special Tax Assessments (G-1: 1004 1-23), never the
    Project section's association special assessment (its remaining balance here)."""
    key = "subject.special_assessments"
    no = derive(with_special("false", hoa=True), key)
    assert (no.status, no.type, no.value, no.search) == ("present", "currency", 0, ("No",))
    assert no.judged
    assert derive(with_special("true", hoa=True), key).status == "absent"
    assert derive(with_special("false", hoa=False), key).value == 0
    missing = derive(with_special(None, hoa=True), key)
    assert missing.status == "absent" and "special tax" in (missing.note or "")


DESIGN = """
       <STRUCTURE><ARCHITECTURAL_DESIGN>{children}</ARCHITECTURAL_DESIGN></STRUCTURE>"""


def with_design(children: str) -> str:
    return report_xml().replace(
        "</IMPROVEMENT_DETAIL>", "</IMPROVEMENT_DETAIL>" + DESIGN.format(children=children), 1
    )


def test_design_style_is_the_dwelling_style_only() -> None:
    """G-1 maps the 1004's Design (Style) to the Dwelling Style (ArchitecturalDesignCategoryType)
    only; an attached unit's Structure Design (Mid-rise, Rowhouse / Townhouse) is the 1073's
    Project Description, so the key is absent for it."""
    for structural in ("Midrise", "Highrise", "RowhouseTownhouse"):
        fact = derive(
            with_design(f"<StructuralDesignType>{structural}</StructuralDesignType>"),
            "improvements.design_style",
        )
        assert fact.status == "absent", structural
        assert "Structure Design" in (fact.note or "")
    both = with_design(
        "<ArchitecturalDesignCategoryType>Colonial</ArchitecturalDesignCategoryType>"
        "<StructuralDesignType>RowhouseTownhouse</StructuralDesignType>"
    )
    assert derive(both, "improvements.design_style").value == "Colonial"
    assert derive(report_xml(), "improvements.design_style").status == "absent"


def test_cost_approach_developed(tmp_path: Path) -> None:
    doc = build(tmp_path, report_xml())
    site = doc.field("cost_approach.site_value")
    assert (site.value, site.page, site.verified) == (100000, 5, True)
    assert doc.field("cost_approach.indicated_value").value == 425000
    assert doc.field("cost_approach.dwelling_sqft").value == 2000
    assert doc.field("cost_approach.garage_cost").value == 24000
    # parentheses are the form's subtraction mark: the depreciation is positive
    dep = doc.field("cost_approach.depreciation")
    assert dep.value == 29008 and dep.printed == "$(29,008)"
    assert doc.field("cost_approach.total_cost_new").status == "absent"
    assert doc.field("reconciliation.indicated_value_cost_approach").value == 425000
    income = doc.field("reconciliation.indicated_value_income_approach")
    assert income.status == "not_developed" and income.page == 5
    assert income.printed == "Not Necessary for Credible Results"
    # 100,000 + 30,000 + 295,000 = 425,000 and 2,000 x 150.004 = 300,008: no issue
    assert doc.expected_issues == ()
    assert "2000 x 150.004" in (doc.note or "")


def test_cost_arithmetic_error_is_an_expected_issue(tmp_path: Path) -> None:
    doc = build(tmp_path, report_xml(indicated=435_000))
    rules = [i.rule for i in doc.expected_issues]
    assert rules == ["cost_approach_total"]
    assert "425,000" in (doc.expected_issues[0].note or "")


def test_dwelling_total_that_does_not_add_up_is_an_issue(tmp_path: Path) -> None:
    xml = report_xml().replace(
        "<DepreciatedCostDwellingsTotalAmount>295000", "<DepreciatedCostDwellingsTotalAmount>295500"
    )
    rules = sorted(i.rule for i in build(tmp_path, xml).expected_issues)
    assert rules == ["cost_approach_total", "cost_dwellings_total"]


def test_cost_not_developed(tmp_path: Path) -> None:
    doc = build(tmp_path, report_xml(cost=False))
    for key in gse_d1.COST_KEYS:
        assert doc.field(key).status == "not_developed", key
    assert doc.field("reconciliation.indicated_value_cost_approach").status == "not_developed"
    assert not any(f.key.startswith("cost_approach.") and f.extra for f in doc.fields)


def test_not_on_uad36_keys_are_absent(tmp_path: Path) -> None:
    doc = build(tmp_path, report_xml())
    for key, note in gse_d1.NOT_ON_UAD36.items():
        field = doc.field(key)
        assert field.status == "absent" and note == field.note
    assert "not on the UAD 3.6 URAR" in (doc.field("improvements.total_rooms").note or "")


def test_multi_unit_keys_are_unscorable(tmp_path: Path) -> None:
    doc = build(tmp_path, report_xml(units=2))
    assert doc.field("improvements.bedrooms").status == "unscorable"
    assert doc.field("subject.occupant").status == "unscorable"
    assert doc.field("improvements.units").value == 2
    gla = doc.field("improvements.gla_sqft")  # the building total, not printed in this PDF
    assert gla.value == 2900 and gla.page is None and not gla.verified


def test_print_that_differs_from_the_xml_wins(tmp_path: Path) -> None:
    pages = pdf_pages(zoning="Residental single family")  # the PDF misspells it
    field = build(tmp_path, report_xml(), pages).field("site.zoning_description")
    assert field.value == "Residental single family" and field.page == 3
    assert not field.verified and "the PDF prints" in (field.note or "")


def test_cover_pages_are_not_searched(tmp_path: Path) -> None:
    pages = pdf_pages()
    pages[0][0][1].append("Contract Price $480,000")
    price = build(tmp_path, report_xml(), pages).field("listing_and_contract.contract_price")
    assert 1 not in (price.page, *price.also_on)


def test_value_not_in_the_pdf_has_no_page(tmp_path: Path) -> None:
    pages = pdf_pages()
    pages[3][0][1].remove("Total Bedrooms 4")
    field = build(tmp_path, report_xml(), pages).field("improvements.bedrooms")
    assert (field.status, field.value, field.page, field.verified) == ("present", 4, None, False)


def test_printed_formats() -> None:
    assert gse_d1.money(489000) == "$489,000" and gse_d1.money(95.66) == "$95.66"
    assert gse_d1.area(14950) == "14,950 Sq. Ft."
    assert gse_d1.us_date("2019-09-17") == "09/17/2019"
    assert gse_d1.printed_enum("ForcedWarmAir") == "Forced Warm Air"
    assert gse_d1.printed_enum("OwnerOccupied") == "Owner"
    assert gse_d1.section_id("Unit Interior - Building 1 - Unit 1") == "unit_interior"
    assert gse_d1.section_id("Subject Property Amenities") == "amenities"
    assert gse_d1.section_id("Subject Property") == "subject"


def test_every_catalogue_key_has_a_rule_or_an_absent_reason() -> None:
    for spec in FIELDS:
        assert spec.key in gse_d1.RULES or spec.key in gse_d1.NOT_ON_UAD36, spec.key
    assert not set(gse_d1.NOT_ON_UAD36) & {k for k, r in gse_d1.RULES.items() if not r.extra}
    assert set(gse_d1.mapped_keys()) == {s.key for s in FIELDS} - set(gse_d1.NOT_ON_UAD36)


OTHER_STRUCTURE = """
       <STRUCTURE>
         <ARCHITECTURAL_DESIGN>
           <ArchitecturalDesignCategoryType>Other</ArchitecturalDesignCategoryType>
           <ArchitecturalDesignCategoryTypeOtherDescription>Barn Loft
           </ArchitecturalDesignCategoryTypeOtherDescription>
         </ARCHITECTURAL_DESIGN>
         <STRUCTURE_COMPONENTS>
           <STRUCTURE_COMPONENT>
             <EXTERIOR_WALLS><EXTERIOR_WALL><WallMaterialType>Other</WallMaterialType>
               <WallMaterialTypeOtherDescription>Faux Log</WallMaterialTypeOtherDescription>
             </EXTERIOR_WALL></EXTERIOR_WALLS>
             <STRUCTURE_COMPONENT_DETAIL><ImprovementComponentType>ExteriorWallsAndTrim</ImprovementComponentType></STRUCTURE_COMPONENT_DETAIL>
           </STRUCTURE_COMPONENT>
           <STRUCTURE_COMPONENT>
             <ROOF><ROOF_MATERIALS><ROOF_MATERIAL><RoofMaterialType>Other</RoofMaterialType>
               <RoofMaterialTypeOtherDescription>Green Sod</RoofMaterialTypeOtherDescription>
             </ROOF_MATERIAL></ROOF_MATERIALS></ROOF>
             <STRUCTURE_COMPONENT_DETAIL><ImprovementComponentType>Roof</ImprovementComponentType></STRUCTURE_COMPONENT_DETAIL>
           </STRUCTURE_COMPONENT>
           <STRUCTURE_COMPONENT>
             <FOUNDATIONS><FOUNDATION><FOUNDATION_MATERIALS><FOUNDATION_MATERIAL>
               <FoundationMaterialType>Other</FoundationMaterialType>
               <FoundationMaterialTypeOtherDescription>Rammed Earth
               </FoundationMaterialTypeOtherDescription>
             </FOUNDATION_MATERIAL></FOUNDATION_MATERIALS></FOUNDATION></FOUNDATIONS>
             <STRUCTURE_COMPONENT_DETAIL><ImprovementComponentType>Foundation</ImprovementComponentType></STRUCTURE_COMPONENT_DETAIL>
           </STRUCTURE_COMPONENT>
         </STRUCTURE_COMPONENTS>
       </STRUCTURE>"""

OTHER_FLOORING = """
        <INTERIOR_COMPONENTS><INTERIOR_COMPONENT><FLOOR_COVERINGS>
          <FLOOR_COVERING><FlooringType>Carpet</FlooringType></FLOOR_COVERING>
          <FLOOR_COVERING><FlooringType>Other</FlooringType>
            <FlooringTypeOtherDescription>Cork Plank</FlooringTypeOtherDescription></FLOOR_COVERING>
        </FLOOR_COVERINGS></INTERIOR_COMPONENT></INTERIOR_COMPONENTS>
        <PROPERTY_UNIT_DETAIL>"""

MECHANICALS_LINE = """
                  <REPLACEMENT_REPRODUCTION_COST>
                    <ReplacementOrReproductionCostAmount>10000</ReplacementOrReproductionCostAmount>
                    <ReplacementOrReproductionCostAreaType>Other</ReplacementOrReproductionCostAreaType>
                    <ReplacementOrReproductionCostAreaTypeOtherDescription>Mechanicals/Misc</ReplacementOrReproductionCostAreaTypeOtherDescription>
                  </REPLACEMENT_REPRODUCTION_COST>
                </REPLACEMENT_REPRODUCTION_COSTS>"""


def derive(xml: str, key: str) -> gse_d1.Fact:
    report = gse_d1.Report(gse_d1.ET.fromstring(xml))
    return gse_d1.RULES[key].derive(report)


def test_other_codes_take_their_other_description() -> None:
    """Gap 1: the URAR prints the OtherDescription, never the code ``Other``."""
    xml = report_xml().replace("</IMPROVEMENT_DETAIL>", "</IMPROVEMENT_DETAIL>" + OTHER_STRUCTURE)
    xml = xml.replace("<PROPERTY_UNIT_DETAIL>", OTHER_FLOORING, 1)
    expected = {
        "improvements.design_style": "Barn Loft",
        "improvements.exterior_walls": "Faux Log",
        "improvements.roof_surface": "Green Sod",
        "improvements.foundation_walls": "Rammed Earth",
        "improvements.floors": "Carpet, Cork Plank",
    }
    for key, value in expected.items():
        fact = derive(xml, key)
        assert (fact.status, fact.value) == ("present", value), key
        assert "Other" not in fact.search, key


def test_parties_are_read_from_every_service() -> None:
    """A message may carry a SERVICE before the valuation service (GSE Condo1: a property data
    collection service holding only its collector): the borrower, lender and appraiser are
    still read, from whichever SERVICE carries them."""
    collector = """<SERVICE><PARTIES><PARTY><INDIVIDUAL><NAME><FirstName>Data</FirstName>
     <LastName>Collector</LastName></NAME></INDIVIDUAL><ROLES><ROLE><ROLE_DETAIL>
     <PartyRoleType>PropertyDataCollector</PartyRoleType></ROLE_DETAIL></ROLE></ROLES></PARTY>
     </PARTIES></SERVICE>"""
    xml = report_xml().replace("<SERVICES><SERVICE>", "<SERVICES>" + collector + "<SERVICE>", 1)
    assert derive(xml, "subject.borrower").value == "Pat Buyer"
    assert derive(xml, "subject.lender_client").value == "Test Lending"
    report = gse_d1.Report(gse_d1.ET.fromstring(xml))
    assert report.anchors["service"].find(".//VALUATION_ANALYSIS") is not None


def test_year_month_cost_data_date_is_a_month() -> None:
    """Gap 2: a cost-data effective date given as YYYY-MM is a ``month`` (MM/YYYY)."""
    assert gse_d1.us_date("2025-01") == "01/2025"
    assert gse_d1.us_date("2019-09-17") == "09/17/2019"
    month = gse_d1.date_fact("2025-01")
    assert (month.type, month.value, month.search) == ("month", "2025-01", ("01/2025",))
    day = gse_d1.date_fact("2019-09-17T00:00:00")
    assert (day.type, day.value) == ("date", "2019-09-17")
    assert gse_d1.date_fact("soon").status == "unscorable"
    source = """<DATA_SOURCES><DATA_SOURCE>
        <DataSourceEffectiveDate>2025-01</DataSourceEffectiveDate>
        <DataSourceName>Cost Book</DataSourceName><DataSourceType>CostService</DataSourceType>
      </DATA_SOURCE></DATA_SOURCES>
   <PROPERTIES>"""
    xml = report_xml().replace("<PROPERTIES>", source, 1)
    fact = derive(xml, "cost_approach.effective_date_of_cost_data")
    assert (fact.status, fact.type, fact.value) == ("present", "month", "2025-01")


def with_mechanicals(xml: str, improvement_total: int = 305000) -> str:
    xml = xml.replace("</REPLACEMENT_REPRODUCTION_COSTS>", MECHANICALS_LINE, 1)
    xml = xml.replace(
        "<ImprovementEstimatedTotalCostAmount>295000",
        f"<ImprovementEstimatedTotalCostAmount>{improvement_total}",
    )
    return xml.replace(
        "<DepreciatedCostDwellingsTotalAmount>295000",
        f"<DepreciatedCostDwellingsTotalAmount>{improvement_total}",
    )


def test_mechanicals_line_is_read() -> None:
    """Gap 3: a printed Mechanicals/Misc. cost line fills cost_approach.mechanicals_misc."""
    fact = derive(with_mechanicals(report_xml(indicated=435_000)), "cost_approach.mechanicals_misc")
    assert (fact.status, fact.type, fact.value, fact.search) == (
        "present",
        "currency",
        10000,
        ("$10,000",),
    )
    assert derive(report_xml(), "cost_approach.mechanicals_misc").status == "absent"


def test_lines_without_an_area_count_in_the_improvement_total(tmp_path: Path) -> None:
    """Gap 4: 300,008 + 24,000 + 10,000 (no area) - 29,008 = 305,000 is consistent."""
    doc = build(tmp_path, with_mechanicals(report_xml(indicated=435_000)))
    assert doc.expected_issues == ()
    assert "300,008 +" not in (doc.note or "")  # the lump-sum line is in the lines total
    assert "334,008 + 0 - 29,008 = 305,000" in (doc.note or "")


def test_improvement_total_missing_the_lump_sum_line_is_an_issue(tmp_path: Path) -> None:
    xml = with_mechanicals(report_xml(indicated=425_000), improvement_total=295000)
    rules = [i.rule for i in build(tmp_path / "a", xml).expected_issues]
    assert rules == ["cost_improvement_total"]


def test_line_product_issue_names_the_line_key(tmp_path: Path) -> None:
    xml = report_xml().replace(
        "<PricePerAreaAmount>50</PricePerAreaAmount>", "<PricePerAreaAmount>60</PricePerAreaAmount>"
    )
    issues = build(tmp_path, xml).expected_issues
    assert [(i.rule, i.keys) for i in issues] == [
        ("cost_line_product", ("cost_approach.garage_cost",))
    ]


def test_cli_writes_the_truth_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_scenario(tmp_path, "T1", report_xml(), pdf_pages())
    write_scenario(tmp_path, "T2", report_xml(cost=False), pdf_pages())
    assert gse_d1.main(["--data", str(tmp_path)]) == 0
    truth = load_truth("gse_d1", tmp_path)
    assert [d.document for d in truth.documents] == ["T1", "T2"]
    data = json.loads((tmp_path / "appraisal" / "truth" / "gse_d1.json").read_text())
    assert data["set"] == "gse_d1" and data["documents"][0]["pdf"] == "gse_d1/T1/appraisal.pdf"
    assert "T2" in capsys.readouterr().out


def test_cli_without_the_samples_exits_2(tmp_path: Path) -> None:
    (tmp_path / "appraisal").mkdir()
    assert gse_d1.main(["--data", str(tmp_path)]) == 2
