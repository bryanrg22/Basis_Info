"""Fabricated UAD 3.6 (MISMO 3.6) deliveries for the reader and intake tests.

Every value here is invented: a minimal MESSAGE with the containers the A-1 xPaths name, a
subject and a comparable that differ in every value the tests look at, the parties, the
reconciliation and an optional cost approach; plus an Azure DI result whose page words print
some of the values next to their captions.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence

from basis.extraction.azure_di import AzureDiPage, AzureDiResult, AzureDiWord

NS = "http://www.mismo.org/residential/2009/schemas"
XLINK = "http://www.w3.org/1999/xlink"


def _unit(gla: int, bedrooms: int, occupancy: str, adu: bool = False, label: str = "U1") -> str:
    return f"""
<PROPERTY_UNIT xlink:label="PROPERTY_UNIT_{label}">
  <LEVELS><LEVEL><GradeLevelType>PartiallyBelowGrade</GradeLevelType>
    <LevelFinishedAreaMeasure AreaUnitOfMeasureType="SquareFeet">600</LevelFinishedAreaMeasure>
    <LevelUnfinishedAreaMeasure AreaUnitOfMeasureType="SquareFeet">200</LevelUnfinishedAreaMeasure>
  </LEVEL></LEVELS>
  <PROPERTY_UNIT_AREA>
    <UnitStandardAboveGradeFinishedAreaMeasure AreaUnitOfMeasureType="SquareFeet">{gla}
    </UnitStandardAboveGradeFinishedAreaMeasure>
  </PROPERTY_UNIT_AREA>
  <PROPERTY_UNIT_DETAIL>
    <AccessoryDwellingUnitIndicator>{"true" if adu else "false"}</AccessoryDwellingUnitIndicator>
    <BedroomCount>{bedrooms}</BedroomCount>
    <FullBathroomCount>2</FullBathroomCount><HalfBathroomCount>1</HalfBathroomCount>
    <UnitOccupancyType>{occupancy}</UnitOccupancyType>
  </PROPERTY_UNIT_DETAIL>
</PROPERTY_UNIT>"""


def _property(
    use: str,
    address: str,
    gla: int,
    *,
    units: Sequence[str] = (),
    estate: str = "FeeSimple",
    heating: str = "ForcedWarmAir",
    unit_counts: tuple[int, int | None] | None = None,
) -> str:
    unit_xml = "".join(units) or _unit(gla, 3, "OwnerOccupied")
    counts = ""
    if unit_counts is not None:
        living, adus = unit_counts
        counts = f"<LivingUnitExcludingADUCount>{living}</LivingUnitExcludingADUCount>"
        if adus is not None:
            counts += f"<AccessoryDwellingUnitTotalCount>{adus}</AccessoryDwellingUnitTotalCount>"
    return f"""
<PROPERTY ValuationUseType="{use}">
  <ADDRESS><AddressLineText>{address}</AddressLineText><CityName>Faketown</CityName>
    <PostalCode>00001</PostalCode><StateCode>ZZ</StateCode></ADDRESS>
  <IMPROVEMENTS><IMPROVEMENT xlink:label="IMPROVEMENT_{use}">
    <IMPROVEMENT_DETAIL><ImprovementType>Dwelling</ImprovementType>
      <PropertyStructureBuiltYear>1999</PropertyStructureBuiltYear></IMPROVEMENT_DETAIL>
    <PROPERTY_UNITS>{unit_xml}</PROPERTY_UNITS>
    <SYSTEM><HEATING_SYSTEMS><HEATING_SYSTEM><HeatingSystemType>{heating}</HeatingSystemType>
      <HeatingSystemTypeOtherDescription>Geothermal loop</HeatingSystemTypeOtherDescription>
    </HEATING_SYSTEM></HEATING_SYSTEMS></SYSTEM>
  </IMPROVEMENT></IMPROVEMENTS>
  <PROPERTY_DETAIL>{counts}<PropertyEstateType>{estate}</PropertyEstateType>
    <AttachmentType>Detached</AttachmentType></PROPERTY_DETAIL>
  <SALES_CONTRACTS><SALES_CONTRACT><SALES_CONTRACT_DETAIL>
    <SalesContractAmount>250000</SalesContractAmount>
    <SalesConcessionIndicator>false</SalesConcessionIndicator>
  </SALES_CONTRACT_DETAIL></SALES_CONTRACT></SALES_CONTRACTS>
</PROPERTY>"""


def _party(role: str, first: str, last: str, label: str) -> str:
    return f"""
<PARTY><INDIVIDUAL><NAME><FirstName>{first}</FirstName><LastName>{last}</LastName></NAME>
  </INDIVIDUAL><ROLES><ROLE xlink:label="{label}"><ROLE_DETAIL><PartyRoleType>{role}</PartyRoleType>
  </ROLE_DETAIL></ROLE></ROLES></PARTY>"""


def fabricated_xml(
    *,
    cost_developed: bool = False,
    units: Sequence[str] = (),
    estate: str = "FeeSimple",
    heating: str = "ForcedWarmAir",
    with_subject_marker: bool = True,
    unit_counts: tuple[int, int | None] | None = None,
) -> bytes:
    """A small UAD 3.6 MESSAGE: subject "100 Test Ln" (2,000 sq ft), one sales comparable
    "999 Comp Rd" (9,999 sq ft), an appraiser and a borrower, an opinion of value of $300,000,
    effective 2026-01-15, and a cost approach (site value $55,000) when ``cost_developed``."""
    subject_use = "SubjectProperty" if with_subject_marker else ""
    subject = _property(
        subject_use,
        "100 Test Ln",
        2000,
        units=units,
        estate=estate,
        heating=heating,
        unit_counts=unit_counts,
    )
    if not with_subject_marker:
        subject = subject.replace(' ValuationUseType=""', "")
    comparable = _property("SalesComparable", "999 Comp Rd", 9999, estate="Leasehold")
    cost = (
        """<COST_APPROACH><COST_APPROACH_DETAIL><SiteEstimatedValueAmount>55000</SiteEstimatedValueAmount>
        <ValueIndicatedByCostApproachAmount>310000</ValueIndicatedByCostApproachAmount>
        </COST_APPROACH_DETAIL></COST_APPROACH>"""
        if cost_developed
        else ""
    )
    flag = "true" if cost_developed else "false"
    borrower = _party("Borrower", "Pat", "Placeholder", "ROLE_B")
    appraiser = _party("Appraiser", "Alex", "Example", "ROLE_Appraiser")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<MESSAGE xmlns="{NS}" xmlns:xlink="{XLINK}" MISMOReferenceModelIdentifier="3.6.0366">
<DOCUMENT_SETS><DOCUMENT_SET><DOCUMENTS><DOCUMENT>
<DEAL_SETS><DEAL_SET><DEALS><DEAL><SERVICES><SERVICE>
<PARTIES>{borrower}{appraiser}</PARTIES>
<VALUATION><VALUATION_RESPONSE><VALUATION_ANALYSES><VALUATION_ANALYSIS>
<PROPERTIES>{subject}{comparable}</PROPERTIES>
<VALUATION_REPORT>
  <APPROACH_TO_VALUE>{cost}</APPROACH_TO_VALUE>
  <SCOPE_OF_WORK><SCOPE_OF_WORK_DETAIL>
    <CostApproachIndicator>{flag}</CostApproachIndicator>
    <IncomeApproachIndicator>false</IncomeApproachIndicator>
    <SiteValueIndicator>{flag}</SiteValueIndicator>
    <ValuationAssignmentType>Purchase</ValuationAssignmentType>
  </SCOPE_OF_WORK_DETAIL></SCOPE_OF_WORK>
  <VALUATION_RECONCILIATION><VALUATION_RECONCILIATION_SUMMARY><VALUATION_RECONCILIATION_SUMMARY_DETAIL>
    <AppraisalReportEffectiveDate>2026-01-15</AppraisalReportEffectiveDate>
    <OpinionOfValueAmount>300000</OpinionOfValueAmount>
  </VALUATION_RECONCILIATION_SUMMARY_DETAIL></VALUATION_RECONCILIATION_SUMMARY></VALUATION_RECONCILIATION>
</VALUATION_REPORT>
</VALUATION_ANALYSIS></VALUATION_ANALYSES></VALUATION_RESPONSE></VALUATION>
</SERVICE></SERVICES></DEAL></DEALS></DEAL_SET></DEAL_SETS>
</DOCUMENT></DOCUMENTS></DOCUMENT_SET></DOCUMENT_SETS></MESSAGE>
""".encode()


def unit(gla: int, bedrooms: int, occupancy: str, *, adu: bool = False, label: str = "U1") -> str:
    return _unit(gla, bedrooms, occupancy, adu, label)


def words_page(number: int, lines: Sequence[Sequence[str]]) -> AzureDiPage:
    """A page whose ``lines`` (each a list of words) are laid out top to bottom, words left to
    right, as page fractions."""
    words: list[AzureDiWord] = []
    for row, line in enumerate(lines):
        y = 0.05 + row * 0.04
        for column, text in enumerate(line):
            x = 0.05 + column * 0.12
            words.append(AzureDiWord(text=text, bbox=(x, y, x + 0.1, y + 0.02)))
    return AzureDiPage(page_number=number, width=8.5, height=11.0, unit="inch", words=tuple(words))


def azure_printing(*pages: Sequence[Sequence[str]]) -> AzureDiResult:
    return AzureDiResult(
        pages=tuple(words_page(i + 1, page) for i, page in enumerate(pages)), synthetic=True
    )


def fabricated_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()
