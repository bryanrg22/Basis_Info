"""The UAD 3.6 validator (``evals/appraisal/uad36_validate.py``) on a fabricated spec and XML.

Nothing here comes from a GSE sample or a client appraisal: the A-1 / H-1 sheets below are a
tiny invented specification in the shape of the GSE workbooks (same column names, MISMO-style
element names), and every XML message is written in this file. Each check is exercised on a
passing and a failing message.
"""

from __future__ import annotations

import datetime as dt
import json
import shlex
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from evals.appraisal import uad36_validate as u

NS = "http://www.mismo.org/residential/2009/schemas"
XL = "http://www.w3.org/1999/xlink"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
URN = "urn:fdc:mismo.org:2009:residential/DATA_SOURCE_IsDataSourceFor_SALES_HISTORY"
TODAY = dt.date(2026, 1, 15)

A1_COLUMNS = [
    "Unique ID",
    "XML Sort Number",
    "Additional Context",
    "Report Location",
    "MISMO xPath",
    "MISMO Parent Container",
    "MISMO Data Point Name",
    "MISMO Attribute Name",
    "MISMO Data Point / Attribute Definition",
    "Supported Data Point Enumerations\n\n(Reference the MISMO Data Point Name column)",
    "Supported Attribute Enumerations\n\n(Reference the MISMO Attribute Name column)",
    "UAD Data Point Format Type",
    "UAD Data Point Format Details",
    "Subject Property",
    "Land Comparable",
    "Property Analyzed Not Used",
    "Rental Comparable",
    "Gross Rent Multiplier Comparable",
    "Sales Comparable",
    "Conditionality",
    "Conditionality Details",
    "Cardinality",
    "Implementation Notes\n\n(Only applies when conditionality has been met)",
]
S, C = "Subject Property", "Sales Comparable"
ROOT = "MESSAGE/DEAL/VALUATION_ANALYSIS/"
PROP = ROOT + "PROPERTIES/PROPERTY/"
USE_COLUMNS = {"S": S, "C": C}


def a1(path: str, **kw: str) -> list[str]:
    """One A-1 row; keywords: uid, point, attr, enums, attr_enums, fmt, details, uses ("S",
    "C" or "SC"), cond, when, card, notes."""
    uses = {USE_COLUMNS[c] for c in kw.get("uses", "")}
    cells = {
        "Unique ID": kw.get("uid", ""),
        "MISMO xPath": path,
        "MISMO Data Point Name": kw.get("point", ""),
        "MISMO Attribute Name": kw.get("attr", ""),
        "Supported Data Point Enumerations": kw.get("enums", ""),
        "Supported Attribute Enumerations": kw.get("attr_enums", ""),
        "UAD Data Point Format Type": kw.get("fmt", ""),
        "UAD Data Point Format Details": kw.get("details", ""),
        "Conditionality": kw.get("cond", ""),
        "Conditionality Details": kw.get("when", ""),
        "Cardinality": kw.get("card", ""),
        "Implementation Notes": kw.get("notes", ""),
    }
    row = []
    for column in A1_COLUMNS:
        name = column.split("\n")[0]
        row.append("x" if name in uses else cells.get(name, ""))
    return row


# The fabricated A-1 sheet, one line per row, nested by indentation: ``NAME card`` opens a
# container (closed where the indentation ends); ``@attr`` is a container attribute (``+``
# joins several, as A-1 writes them on one cell's lines); ``Point`` a data point
# (``Point@attr`` with its unit attribute); then ``key=value`` cells (shell quoting).
A1_TREE = """
MESSAGE 1:1
  @MISMOReferenceModelIdentifier uid=9000.0001 attr_enums=3.6.0366 cond=R
  DEAL 1:1
    VALUATION_ANALYSIS 1:1
      SCOPE_OF_WORK 0:1
        SalesComparisonApproachIndicator uid=9000.0050 enums='false|true' fmt=Boolean
      PROPERTIES 1:1
        PROPERTY 1:1
          @ValuationUseType uid=9000.0010 attr_enums=SubjectProperty uses=S cond=R
          ADDRESS 1:1
            AddressLineText uid=9000.0011 fmt=String details=12 uses=S cond=R
            PostalCode uid=9000.0012 fmt=String details=10 uses=S cond=CR when='IF Exists'
          PROPERTY_DETAIL 1:1
            AttachmentType uid=9000.0020 enums='Attached|Detached|Other' fmt=Enumerated uses=S \
                cond=R
            AttachmentTypeOtherDescription uid=9000.0021 fmt=String details=20 uses=S cond=CR \
                when='IF AttachmentType = "Other"'
            PUDIndicator uid=9000.0022 enums='false|true' fmt=Boolean uses=S cond=R
            NewConstructionIndicator uid=9000.0023 enums='false|true' fmt=Boolean uses=S
            LotSizeAreaMeasure@AreaUnitOfMeasureType uid=9000.0024 attr_enums='Acres|SquareFeet' \
                fmt=Numeric details=+6.2 uses=S
            OpinionOfValueAmount uid=9000.0025 fmt=Amount details=+9.0 uses=S
            AppraisalReportEffectiveDate uid=9000.0026 fmt=Date details=YYYY-MM-DD uses=S
            ReportCreatedDatetime uid=9000.0027 fmt=Datetime details=YYYY-MM-DDThh:mm:ssZ uses=S
            LatitudeIdentifier uid=9000.0028 fmt=Numeric details=±3.6 uses=S
            ProjectStatusType uid=9000.0029 enums='Complete|Partial' fmt=Enumerated uses=S \
                cond=CR when='IF PUDIndicator = "true"' \
                notes='Deliver only when the project is new.'
          SALES_HISTORIES 0:1
            SALES_HISTORY 0:2
              @xlink:label uid=9000.0030 attr_enums=SALES_HISTORY_n uses=S
              OwnershipTransferDate uid=9000.0031 fmt=Date details=YYYY-MM-DD uses=S
            SALES_HISTORY 0:1
              OwnershipTransferDate uid=9000.0032 fmt=Date details=YYYY-MM-DD uses=S
              SaleType uid=9000.0033 enums='Arms|Estate' fmt=Enumerated uses=S
        PROPERTY 0:3
          @ValuationUseType+@xlink:label uid=9000.0040 \
              attr_enums='@ValuationUseType - SalesComparable\\n@xlink:label - PROPERTY_n' uses=C \
              cond=CR when='IF SalesComparisonApproachIndicator = "true"'
          ADDRESS 0:1
            AddressLineText uid=9000.0041 fmt=String details=12 uses=C
          PROPERTY_DETAIL 0:1
            AttachmentType uid=9000.0042 enums='Attached|Detached' fmt=Enumerated uses=C
            AdjustmentSummaryDescription uid=9000.0044 fmt=String details=50 uses=C cond=CR \
                when='IF ComparableAdjustmentType <> "View"'
          COMPARABLE_ADJUSTMENTS 0:1
            COMPARABLE_ADJUSTMENT 0:∞
              ComparableAdjustmentType uid=9000.0043 enums='View|LivingUnitBedroomCount' \
                  fmt=Enumerated uses=C
      DATA_SOURCES 0:1
        DATA_SOURCE 0:∞
          @xlink:label uid=9000.0060 attr_enums=DATA_SOURCE_n cond=R
          DataSourceType uid=9000.0061 enums='MLS|Other' fmt=Enumerated cond=R
          DataSourceName uid=9000.0062 enums='Alpha Guide|Beta Guide' fmt=String details=33 \
              notes='Additional values may be provided that are not listed.'
          DataSourceContentIdentifier uid=9000.0063 enums='Spec v1' fmt=String details=20
      RELATIONSHIPS 0:1
        RELATIONSHIP 0:∞
          @xlink:arcrole uid=9000.0090 attr_enums=URN
          @xlink:from uid=9000.0091 attr_enums=DATA_SOURCE_n
          @xlink:to uid=9000.0092 attr_enums=SALES_HISTORY_n
"""


def a1_sheet(tree: str = A1_TREE) -> list[list[str]]:
    rows: list[list[str]] = [A1_COLUMNS]
    stack: list[tuple[int, str]] = []  # (indent, container path)
    for line in tree.strip("\n").split("\n"):
        indent = len(line) - len(line.lstrip())
        head, *cells = shlex.split(line)
        while stack and stack[-1][0] >= indent:
            rows.append(a1(stack.pop()[1]))
        parent = stack[-1][1] if stack else ""
        if head.isupper() and "@" not in head:
            path = f"{parent}{head}/"
            rows.append(a1(path, card=cells[0]))
            stack.append((indent, path))
        else:
            kw = dict(cell.split("=", 1) for cell in cells)
            kw = {k: v.replace("\\n", "\n").replace("URN", URN) for k, v in kw.items()}
            point, _, attr = head.partition("@")
            attrs = "\n".join(f"@{a.lstrip('@')}" for a in attr.split("+") if a)
            rows.append(a1(parent, point=point, attr=attrs, **kw))
    while stack:
        rows.append(a1(stack.pop()[1]))
    return rows


ENUM_SHEET = [
    ["Unique ID", "Report Location", "MISMO Data Point / Attribute Name", "Supported Enumerations",
     "Output Report Display Text", "Implementation Note", "Supported Enumerations Definitions",
     S, "Land Comparable", "Property Analyzed Not Used", "Rental Comparable",
     "Gross Rent Multiplier Comparable", C],
    ["9000.0024", "", "@AreaUnitOfMeasureType", "Hectares", "ha", "", "", "x"],
]  # fmt: skip
ARC_SHEET = [
    ["The ArcRole concept is used to identify relationships."],
    ["Unique ID", "Associated Report Section Reference", "ArcRole Explanation", "From", "Link",
     "To"],
    ["9000.0090", "Sales history data source", "Joins a data source to a sale.", "DATA_SOURCE",
     "IsDataSourceFor", "SALES_HISTORY"],
]  # fmt: skip
H1_COLUMNS = [
    "Unique ID", "Primary Data Element", "Message ID", "Message Text", "Rule Logic", "Severity",
    "Property Affected", "Report Section", "Report Subsection", "Report Label / Value",
    "Data Point Name / Value", " xPath", "Related Value(s)", "Date Format", "Number Format",
    "Min Value", "Max Value",
]  # fmt: skip
VA = "../VALUATION_ANALYSIS/"
PD = VA + "PROPERTIES/PROPERTY/PROPERTY_DETAIL/"
SH = VA + "PROPERTIES/PROPERTY/SALES_HISTORIES/SALES_HISTORY/"
ADJ = VA + "PROPERTIES/PROPERTY/COMPARABLE_ADJUSTMENTS/COMPARABLE_ADJUSTMENT/"
# Message ID, severity, Property Affected, Data Point Name, xPath, Rule Logic
H1_RULES = [
    ("UAD9001", "Fatal", "Subject", "AddressLineText", VA + "PROPERTIES/PROPERTY/ADDRESS/",
     "If AddressLineText is not provided"),
    ("UAD9002", "Fatal", "Subject", "AttachmentTypeOtherDescription", PD,
     'If AttachmentType = "Other" and AttachmentTypeOtherDescription is not provided'),
    ("UAD9003", "Fatal", "Subject", "OwnershipTransferDate (%PV%)", SH,
     "If OwnershipTransferDate does not include year, month and day (YYYY-MM-DD format) in a"
     " given instance of SALES_HISTORY"),
    ("UAD9004", "Warning", "Sales Comparable #n", "AttachmentType", PD,
     'If @ValuationUseType = "SalesComparable" and AttachmentType is not provided'),
    ("UAD9005", "Fatal", "Subject", "@xlink:arcrole", VA + "RELATIONSHIPS/RELATIONSHIP/",
     f'If PUDIndicator = "true", and there is no instance of RELATIONSHIP with @xlink:arcrole ='
     f' "{URN}" that links DATA_SOURCE to SALES_HISTORY'),
    ("UAD9006", "Warning", "Subject", "LotSizeAreaMeasure (%PV%)", PD,
     "If LotSizeAreaMeasure < 1"),
    ("UAD9007", "Fatal", "N/A", "@ValuationUseType", VA + "PROPERTIES/PROPERTY/",
     'If more than one instance of PROPERTY has @ValuationUseType = "SubjectProperty"'),
    ("UAD9008", "Fatal", "Subject", "OpinionOfValueAmount", PD,
     "If OpinionOfValueAmount differs from the value on the PDF"),
    ("UAD9009", "Fatal", "Subject", "AttachmentType", PD,
     "If AttachmentType looks unusual to the reviewer"),
    ("UAD9010", "Fatal", "Subject", "AttachmentType", PD,
     'If Frobnicator = "true" and AttachmentType is not provided'),
    ("UAD9011", "Fatal", "Subject", "LotSizeAreaMeasure", PD,
     'If PUDIndicator = "true", or (AttachmentType = "Detached" and NewConstructionIndicator ='
     ' "true") and LotSizeAreaMeasure is not provided'),
    ("UAD9012", "Fatal", "Subject", "AppraisalReportEffectiveDate", PD,
     "If AppraisalReportEffectiveDate is in the future"),
    ("UAD9013", "Warning", "Subject", "AttachmentType", PD,
     'If AttachmentType <> "Detached" and AttachmentType <> "Attached"'),
    ("UAD9014", "Fatal", "N/A", "ComparableAdjustmentType", ADJ,
     'If there is more than one instance of COMPARABLE_ADJUSTMENT with ComparableAdjustmentType'
     ' = "View" in a given instance of PROPERTY'),
    ("UAD9015", "Fatal", "Subject", "AddressLineText", VA + "PROPERTIES/PROPERTY/ADDRESS/",
     'If AddressLineText is not provided for SalesComparisonApproachIndicator = "true"'),
    ("UAD9016", "Fatal", "N/A", "AttachmentType", PD,
     'If AttachmentType is not provided for @ValuationUseType = "SalesComparable"'),
    ("UAD9017", "Fatal", "Subject", "OwnershipTransferDate", SH,
     'If PUDIndicator = "true" and SaleType = "Estate" and OwnershipTransferDate is not'
     " provided"),
    ("UAD9018", "Fatal", "Sales Comparable #n", "DataSourceType", VA + "DATA_SOURCES/DATA_SOURCE/",
     "If DataSourceType is not provided in a given instance of DATA_SOURCE"),
    ("UAD9019", "Warning", "N/A", "AddressLineText", VA + "DATA_SOURCES/DATA_SOURCE/",
     "If AddressLineText length exceeds 10 characters"),
    ("UAD9020", "Warning", "Subject", "SaleType", SH,
     'If NewConstructionIndicator = "true" and there is no instance of SALES_HISTORY with'
     ' SaleType = "Estate" and OwnershipTransferDate > "2021-01-01"'),
    ("UAD1758", "Fatal", "Sales Comparable", "ComparableAdjustmentType", ADJ,
     'If @ValuationUseType = "SalesComparable" and there is more than one instance of'
     " COMPARABLE_ADJUSTMENT with the same ComparableAdjustmentType enumeration that should"
     " only be adjusted once"),
]  # fmt: skip


def h1_sheet() -> list[list[str]]:
    rows = [H1_COLUMNS]
    for mid, severity, affected, point, xpath, logic in H1_RULES:
        row = [""] * len(H1_COLUMNS)
        row[0], row[1], row[2], row[3] = "9000.0000", point, mid, f"Message {mid}."
        row[4], row[5], row[6], row[10], row[11] = logic, severity, affected, point, xpath
        rows.append(row)
    return rows


ADJ_SHEET = [
    ["ComparableAdjustmentType", "Adjustment Repeatability", "ValuationUseType"],
    ["View", "Adjustment cannot repeat", "SalesComparable"],
    ["LivingUnitBedroomCount", "Adjustment can repeat when there is more than one living unit",
     "SalesComparable"],
]  # fmt: skip
SHEETS: dict[str, Sequence[Sequence[str]]] = {
    u.A1_SHEET: a1_sheet(),
    u.A1_ENUM_SHEET: ENUM_SHEET,
    u.A1_ARC_SHEET: ARC_SHEET,
    u.H1_SHEET: h1_sheet(),
    u.H1_ADJ_SHEET: ADJ_SHEET,
}
SPEC = u.build_spec(SHEETS)

VALID = f"""<?xml version="1.0" encoding="UTF-8"?>
<MESSAGE xmlns="{NS}" xmlns:xlink="{XL}" xmlns:xsi="{XSI}"
         MISMOReferenceModelIdentifier="3.6.0366" xsi:schemaLocation="{NS} schema.xsd">
 <DEAL><VALUATION_ANALYSIS>
  <SCOPE_OF_WORK><SalesComparisonApproachIndicator>true</SalesComparisonApproachIndicator>
  </SCOPE_OF_WORK>
  <PROPERTIES>
   <PROPERTY ValuationUseType="SubjectProperty">
    <ADDRESS><AddressLineText>1 Test Way</AddressLineText></ADDRESS>
    <PROPERTY_DETAIL>
     <AttachmentType>Detached</AttachmentType>
     <PUDIndicator>true</PUDIndicator>
     <NewConstructionIndicator>false</NewConstructionIndicator>
     <LotSizeAreaMeasure AreaUnitOfMeasureType="Acres">2.5</LotSizeAreaMeasure>
     <OpinionOfValueAmount>123000</OpinionOfValueAmount>
     <AppraisalReportEffectiveDate>2025-12-01</AppraisalReportEffectiveDate>
     <ReportCreatedDatetime>2025-12-02T10:30:00Z</ReportCreatedDatetime>
     <LatitudeIdentifier>-12.345678</LatitudeIdentifier>
    </PROPERTY_DETAIL>
    <SALES_HISTORIES>
     <SALES_HISTORY xlink:label="SALES_HISTORY_1">
      <OwnershipTransferDate>2020-02-29</OwnershipTransferDate>
     </SALES_HISTORY>
    </SALES_HISTORIES>
   </PROPERTY>
   <PROPERTY ValuationUseType="SalesComparable" xlink:label="PROPERTY_1">
    <ADDRESS><AddressLineText>2 Test Way</AddressLineText></ADDRESS>
    <PROPERTY_DETAIL><AttachmentType>Attached</AttachmentType></PROPERTY_DETAIL>
    <COMPARABLE_ADJUSTMENTS>
     <COMPARABLE_ADJUSTMENT><ComparableAdjustmentType>View</ComparableAdjustmentType>
     </COMPARABLE_ADJUSTMENT>
     <COMPARABLE_ADJUSTMENT><ComparableAdjustmentType>LivingUnitBedroomCount
     </ComparableAdjustmentType></COMPARABLE_ADJUSTMENT>
     <COMPARABLE_ADJUSTMENT><ComparableAdjustmentType>LivingUnitBedroomCount
     </ComparableAdjustmentType></COMPARABLE_ADJUSTMENT>
    </COMPARABLE_ADJUSTMENTS>
   </PROPERTY>
  </PROPERTIES>
  <DATA_SOURCES>
   <DATA_SOURCE xlink:label="DATA_SOURCE_1"><DataSourceType>MLS</DataSourceType></DATA_SOURCE>
  </DATA_SOURCES>
  <RELATIONSHIPS>
   <RELATIONSHIP xlink:arcrole="{URN}" xlink:from="DATA_SOURCE_1" xlink:to="SALES_HISTORY_1"/>
  </RELATIONSHIPS>
 </VALUATION_ANALYSIS></DEAL>
</MESSAGE>
"""


def run(xml: str, *, today: dt.date = TODAY) -> u.Result:
    return u.validate(xml.encode(), SPEC, today=today)


def rules(result: u.Result, severity: str | None = None) -> set[str]:
    return {f.rule for f in result.findings if severity is None or f.severity == severity}


def edit(old: str, new: str, xml: str = VALID) -> str:
    assert xml.count(old) == 1, old
    return xml.replace(old, new)


def findings(result: u.Result, rule: str) -> list[u.Finding]:
    return [f for f in result.findings if f.rule == rule]


# --------------------------------------------------------------------------- the spec


def test_the_fabricated_spec_builds_blocks_per_instance() -> None:
    props = SPEC.blocks_by_path[tuple(PROP.strip("/").split("/"))]
    assert [b.card for b in props] == [u.Cardinality(1, 1), u.Cardinality(0, 3)]
    assert props[0].uses == {"SubjectProperty"} and props[1].uses == {"SalesComparable"}
    assert [(a.source, a.target, a.urn) for a in SPEC.arcroles] == [
        ("DATA_SOURCE", "SALES_HISTORY", URN)
    ]
    assert SPEC.adjustments["View"][0] == "Adjustment cannot repeat"


def test_a_sheet_whose_containers_do_not_nest_is_refused() -> None:
    rows = [A1_COLUMNS, a1("MESSAGE/", card="1:1"), a1("MESSAGE/A/", card="1:1"), a1("MESSAGE/")]
    with pytest.raises(u.SpecError, match="closes out of order"):
        u.build_spec({u.A1_SHEET: rows})
    with pytest.raises(u.SpecError, match="never closed"):
        u.build_spec({u.A1_SHEET: rows[:3]})


def test_an_arc_role_whose_urn_disagrees_with_a1_is_refused() -> None:
    bad = [*ARC_SHEET[:2], ["9000.0090", "", "", "DATA_SOURCE", "IsDataSourceFor", "PROPERTY"]]
    with pytest.raises(u.SpecError, match="arcrole"):
        u.build_spec({**SHEETS, u.A1_ARC_SHEET: bad})


# --------------------------------------------------------------------------- baseline


def test_the_valid_message_has_no_errors_or_warnings() -> None:
    result = run(VALID)
    assert [f for f in result.findings if f.severity != "info"] == []
    assert result.errors == 0 and result.warnings == 0
    # PostalCode's condition is the bare "Exists": counted as not checked, never as passed
    assert result.not_checked["A1.required (CR condition not mechanical)"] >= 1


def test_xml_that_is_not_well_formed_or_has_a_doctype_is_one_error() -> None:
    assert rules(run("<MESSAGE>")) == {"XML"}
    doctype = '<?xml version="1.0"?><!DOCTYPE m [<!ENTITY e "x">]><MESSAGE/>'
    assert rules(run(doctype)) == {"XML"}


# --------------------------------------------------------------------------- A1.path / usetype


def test_an_unknown_element_is_a_path_error_and_its_subtree_is_skipped() -> None:
    result = run(
        edit("<ADDRESS><AddressLineText>1", "<ADDRESS><Bogus><X>x</X></Bogus><AddressLineText>1")
    )
    paths = findings(result, "A1.path")
    assert len(paths) == 1 and paths[0].xpath.endswith("PROPERTY[1]/ADDRESS/Bogus")


def test_an_element_outside_the_mismo_namespace_is_a_path_error() -> None:
    xml = edit(
        "<ADDRESS><AddressLineText>1",
        '<ADDRESS><x:Extra xmlns:x="urn:example:ext">1</x:Extra><AddressLineText>1',
    )
    assert rules(run(xml), "error") == {"A1.path"}


def test_a_subject_only_data_point_on_a_comparable_is_a_usetype_error() -> None:
    xml = edit(
        "<PROPERTY_DETAIL><AttachmentType>Attached",
        "<PROPERTY_DETAIL><NewConstructionIndicator>false</NewConstructionIndicator>"
        "<AttachmentType>Attached",
    )
    found = findings(run(xml), "A1.usetype")
    assert len(found) == 1 and "SalesComparable" in found[0].message
    assert found[0].xpath.endswith("PROPERTY[2]/PROPERTY_DETAIL/NewConstructionIndicator")


# --------------------------------------------------------------------------- A1.attribute


def test_an_attribute_a1_does_not_list_is_an_error() -> None:
    result = run(edit("<ADDRESS><AddressLineText>1", '<ADDRESS Kind="x"><AddressLineText>1'))
    assert rules(result, "error") == {"A1.attribute"}


def test_schema_location_is_allowed_on_message_only() -> None:
    assert "A1.attribute" not in rules(run(VALID))
    xml = edit("<DEAL>", '<DEAL xsi:schemaLocation="x y">')
    assert rules(run(xml), "error") == {"A1.attribute"}


# --------------------------------------------------------------------------- A1.enum / version


def test_a_value_outside_the_enumeration_is_an_error() -> None:
    result = run(edit("<AttachmentType>Detached", "<AttachmentType>Floating"))
    assert "A1.enum" in rules(result, "error")


def test_enumerations_are_per_property_type() -> None:
    # Other is a subject enumeration only (A-1 row 9000.0042 lists Attached|Detached)
    xml = edit("<AttachmentType>Attached", "<AttachmentType>Other")
    found = findings(run(xml), "A1.enum")
    assert len(found) == 1 and found[0].xpath.endswith("PROPERTY[2]/PROPERTY_DETAIL/AttachmentType")


def test_attribute_enumerations_join_the_enumerations_sheet() -> None:
    hectares = edit('AreaUnitOfMeasureType="Acres"', 'AreaUnitOfMeasureType="Hectares"')
    assert "A1.enum" not in rules(run(hectares))  # listed only in UAD Enumerations
    furlongs = edit('AreaUnitOfMeasureType="Acres"', 'AreaUnitOfMeasureType="Furlongs"')
    assert "A1.enum" in rules(run(furlongs))


def test_a_closed_value_with_a_bad_property_type_attribute_is_an_enum_error() -> None:
    xml = edit('ValuationUseType="SalesComparable"', 'ValuationUseType="SalesComp"')
    assert "A1.enum" in rules(run(xml))


def test_the_model_version_must_be_the_a1_one() -> None:
    xml = edit('MISMOReferenceModelIdentifier="3.6.0366"', 'MISMOReferenceModelIdentifier="3.6"')
    found = findings(run(xml), "A1.version")
    assert len(found) == 1 and found[0].value == "3.6"


# --------------------------------------------------------------------------- A1.format


@pytest.mark.parametrize(
    ("value", "fmt", "details", "ok"),
    [
        ("2024-02-29", "Date", "YYYY-MM-DD", True),
        ("2023-02-29", "Date", "YYYY-MM-DD", False),
        ("02/03/2024", "Date", "YYYY-MM-DD", False),
        ("2024-07", "Date", "YYYY-MM", True),
        ("2024-13", "Date", "YYYY-MM", False),
        ("1999", "Date", "YYYY", True),
        ("2025-01-02T03:04:05Z", "Datetime", "YYYY-MM-DDThh:mm:ssZ", True),
        ("2025-01-02 03:04:05", "Datetime", "YYYY-MM-DDThh:mm:ssZ", False),
        ("2025-01-02T25:04:05Z", "Datetime", "YYYY-MM-DDThh:mm:ssZ", False),
        ("123456789", "Amount", "+9.2", True),
        ("1234567890", "Amount", "+9.2", False),
        ("12.345", "Amount", "+9.2", False),
        ("1,000", "Amount", "+9.0", False),
        ("-5", "Amount", "+9.0", False),
        ("-5", "Numeric", "±3.6", True),
        (".75", "Numeric", "+3.2", True),
        ("5.", "Numeric", "+3.2", False),
        (".", "Numeric", "+3.2", False),
        ("2.5", "Numeric", "+2.0", False),
        ("-3", "Numeric", "-2.0", True),
        ("3", "Numeric", "-2.0", False),
        ("true", "Boolean", "", True),
        ("True", "Boolean", "", False),
        ("abcd", "String", "4", True),
        ("abcde", "String", "4", False),
    ],
)
def test_format_rules(value: str, fmt: str, details: str, ok: bool) -> None:
    assert u.format_supported(fmt, details)
    assert (u.format_error(value, fmt, details) is None) is ok


def test_an_unknown_format_type_is_reported_as_not_checkable() -> None:
    assert not u.format_supported("Percent", "+3.2")
    rows = [*a1_sheet()]
    rows.insert(3, a1("MESSAGE/", point="Ratio", fmt="Percent", details="+3.2"))
    spec = u.build_spec({u.A1_SHEET: rows})
    result = u.validate(VALID.encode(), spec, today=TODAY)
    unchecked = findings(result, "A1.format-unchecked")
    assert [f.value for f in unchecked] == ["Percent +3.2"] and unchecked[0].severity == "info"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("2025-12-01</App", "2025-12-32</App"),  # Date
        ("123000<", "123,000<"),  # Amount with a comma
        ("2025-12-02T10:30:00Z", "2025-12-02"),  # Datetime without time
        ("-12.345678<", "-12.3456789<"),  # Numeric ±3.6: seven decimals
        ("1 Test Way<", "1 Test Way, Apt 900<"),  # String 12
        ("<PUDIndicator>true", "<PUDIndicator>TRUE"),  # Boolean
    ],
)
def test_a_badly_formatted_value_is_one_format_error(old: str, new: str) -> None:
    result = run(edit(old, new))
    assert [f.rule for f in result.findings if f.severity == "error"].count("A1.format") == 1
    assert "A1.enum" not in rules(result)  # a Boolean's case is the format finding only


def test_an_empty_data_point_is_a_format_error() -> None:
    result = run(edit("<OpinionOfValueAmount>123000<", "<OpinionOfValueAmount><"))
    assert "A1.format" in rules(result, "error")


# --------------------------------------------------------------------------- A1.cardinality


def test_a_second_instance_of_a_single_container_is_a_cardinality_error() -> None:
    detail = "<PROPERTY_DETAIL><AttachmentType>Attached</AttachmentType></PROPERTY_DETAIL>"
    found = findings(run(edit(detail, detail + detail)), "A1.cardinality")
    assert len(found) == 1 and found[0].xpath.endswith("PROPERTY[2]") and found[0].value == "2"


def test_more_comparables_than_a1_allows_is_a_cardinality_error() -> None:
    start = VALID.index('<PROPERTY ValuationUseType="SalesComparable"')
    end = VALID.index("</PROPERTY>", start) + len("</PROPERTY>")
    comp = VALID[start:end]
    copies = "".join(comp.replace("PROPERTY_1", f"PROPERTY_{i}") for i in range(1, 5))
    found = findings(run(VALID[:start] + copies + VALID[end:]), "A1.cardinality")
    assert len(found) == 1 and found[0].value == "5" and "(1:1, 0:3)" in found[0].message
    three = "".join(comp.replace("PROPERTY_1", f"PROPERTY_{i}") for i in range(1, 4))
    assert "A1.cardinality" not in rules(run(VALID[:start] + three + VALID[end:]))


def test_a_repeated_data_point_in_one_container_is_an_error() -> None:
    detail = "<PROPERTY_DETAIL><AttachmentType>Attached</AttachmentType></PROPERTY_DETAIL>"
    twice = (
        "<PROPERTY_DETAIL><AttachmentType>Attached</AttachmentType>"
        "<AttachmentType>Detached</AttachmentType></PROPERTY_DETAIL>"
    )
    found = findings(run(edit(detail, twice)), "A1.repeat")
    assert len(found) == 1 and found[0].value == "2" and "AttachmentType" in found[0].message
    assert "A1.repeat" not in rules(run(VALID))


def test_a_missing_required_container_is_a_cardinality_error() -> None:
    xml = edit("<ADDRESS><AddressLineText>1 Test Way</AddressLineText></ADDRESS>", "")
    found = findings(run(xml), "A1.cardinality")
    assert len(found) == 1 and "ADDRESS" in found[0].message and "1:1" in found[0].message


def test_two_subjects_exceed_the_subject_instance() -> None:
    start = VALID.index('<PROPERTY ValuationUseType="SubjectProperty"')
    end = VALID.index("</PROPERTY>", start) + len("</PROPERTY>")
    twin = VALID[start:end].replace("SALES_HISTORY_1", "SALES_HISTORY_2")
    result = run(VALID[:end] + twin + VALID[end:])
    assert any("1:1" in f.message for f in findings(result, "A1.cardinality"))
    assert "UAD9007" in rules(result)


def test_an_attribute_only_one_instance_lists_tells_the_instances_apart() -> None:
    labelled = (
        '<SALES_HISTORY xlink:label="SALES_HISTORY_{n}">'
        "<OwnershipTransferDate>2020-02-29</OwnershipTransferDate></SALES_HISTORY>"
    )
    sale = (
        "<SALES_HISTORY><OwnershipTransferDate>2021-03-01</OwnershipTransferDate></SALES_HISTORY>"
    )
    start = VALID.index("<SALES_HISTORY ")
    end = VALID.index("</SALES_HISTORIES>")
    two = labelled.format(n=1) + labelled.format(n=2) + sale
    assert "A1.cardinality" not in rules(run(VALID[:start] + two + VALID[end:]))
    # three labelled sales: only the 0:2 instance lists @xlink:label, so it holds all three
    three = "".join(labelled.format(n=n) for n in (1, 2, 3))
    found = findings(run(VALID[:start] + three + VALID[end:]), "A1.cardinality")
    assert len(found) == 1 and "(0:2)" in found[0].message and found[0].value == "3"


def test_a_child_only_one_instance_has_tells_the_instances_apart() -> None:
    sale = (
        "<SALES_HISTORY><OwnershipTransferDate>2021-03-01</OwnershipTransferDate>"
        "<SaleType>Arms</SaleType></SALES_HISTORY>"
    )
    start = VALID.index("<SALES_HISTORY ")
    end = VALID.index("</SALES_HISTORIES>")
    assert "A1.cardinality" not in rules(run(VALID[:end] + sale + VALID[end:]))
    # SaleType is only in the 0:1 instance: two such sales are one too many
    found = findings(run(VALID[:start] + sale + sale + VALID[end:]), "A1.cardinality")
    assert len(found) == 1 and "(0:1)" in found[0].message and found[0].value == "2"


# --------------------------------------------------------------------------- A1.arcrole


def test_a_relationship_with_an_unknown_arcrole_is_an_error() -> None:
    xml = edit(f'xlink:arcrole="{URN}"', 'xlink:arcrole="urn:example:Other"')
    found = findings(run(xml), "A1.arcrole")
    assert len(found) == 1 and found[0].value == "urn:example:Other"


def test_an_unresolved_label_is_an_error() -> None:
    found = findings(run(edit('xlink:to="SALES_HISTORY_1"', 'xlink:to="NOWHERE"')), "A1.arcrole")
    assert [f.value for f in found] == ["NOWHERE"] and "no xlink:label" in found[0].message


def test_a_label_on_the_wrong_container_is_an_error() -> None:
    xml = edit('xlink:to="SALES_HISTORY_1"', 'xlink:to="PROPERTY_1"')
    found = findings(run(xml), "A1.arcrole")
    assert len(found) == 1 and "labels a PROPERTY" in found[0].message


def test_duplicate_labels_and_a_relationship_without_an_arcrole_are_errors() -> None:
    xml = edit('xlink:label="PROPERTY_1"', 'xlink:label="DATA_SOURCE_1"')
    assert any("not unique" in f.message for f in findings(run(xml), "A1.arcrole"))
    xml = edit(f'xlink:arcrole="{URN}" ', "")
    assert any("without xlink:arcrole" in f.message for f in findings(run(xml), "A1.arcrole"))


# --------------------------------------------------------------------------- A1.required


def test_a_missing_required_data_point_is_an_error() -> None:
    xml = edit("<AttachmentType>Detached</AttachmentType>", "")
    found = findings(run(xml), "A1.required")
    assert len(found) == 1 and "AttachmentType" in found[0].message and "(R)" in found[0].message


def test_a_missing_required_attribute_is_an_error() -> None:
    xml = edit('<DATA_SOURCE xlink:label="DATA_SOURCE_1">', "<DATA_SOURCE>")
    assert any("@xlink:label" in f.message for f in findings(run(xml), "A1.required"))


def test_other_needs_its_other_description() -> None:
    other = edit("<AttachmentType>Detached", "<AttachmentType>Other")
    found = findings(run(other), "A1.required")
    assert len(found) == 1 and "AttachmentTypeOtherDescription" in found[0].message
    assert "UAD9002" in rules(run(other))
    described = edit(
        "<AttachmentType>Other</AttachmentType>",
        "<AttachmentType>Other</AttachmentType>"
        "<AttachmentTypeOtherDescription>Floating</AttachmentTypeOtherDescription>",
        other,
    )
    assert not {"A1.required", "UAD9002"} & rules(run(described))


def test_a_condition_narrowed_by_its_implementation_note_is_not_checked() -> None:
    # ProjectStatusType: IF PUDIndicator = "true" (true here), note "Deliver only when ...".
    result = run(VALID)
    assert "A1.required" not in rules(result)
    xml = edit("<PUDIndicator>true", "<PUDIndicator>false")  # condition false: no requirement
    assert run(xml).not_checked["A1.required (CR condition not mechanical)"] == (
        result.not_checked["A1.required (CR condition not mechanical)"] - 1
    )


def test_a_comparable_needs_its_attributes_only_when_the_condition_holds() -> None:
    xml = edit(
        'ValuationUseType="SalesComparable" xlink:label="PROPERTY_1"',
        'ValuationUseType="SalesComparable"',
    )
    assert any("@xlink:label" in f.message for f in findings(run(xml), "A1.required"))
    off = edit(
        ">true</SalesComparisonApproachIndicator>", ">false</SalesComparisonApproachIndicator>", xml
    )
    assert "A1.required" not in rules(run(off))


# --------------------------------------------------------------------------- H-1


def test_the_registry_lists_every_rule_with_a_status_and_reason() -> None:
    registry = {s.rule.message_id: s for s in u.rules_registry(SPEC)}
    assert len(registry) == len(H1_RULES)
    assert registry["UAD9001"].implemented and registry["UAD1758"].implemented
    assert registry["UAD9008"].reason == "requires the PDF report"
    assert registry["UAD9009"].reason.startswith("logic not in the supported grammar")
    assert registry["UAD9010"].reason == "names data A-1 does not define: Frobnicator"
    # PARTY-style "for X = v" needs a repeating container holding X and the rule's data
    assert registry["UAD9015"].reason.startswith("condition reaches the data through an arcrole")
    assert registry["UAD9016"].implemented
    report = u.render_rules(list(registry.values()))
    assert (
        "| UAD9008 | 9000.0000 | Fatal | Subject | not implemented | requires the PDF report |"
        in (report)
    )
    assert f"{len(registry)} rules: {len(registry) - 4} implemented, 4 not implemented." in report
    assert registry["UAD9017"].implemented and registry["UAD9018"].implemented


def test_a_presence_rule_fires_when_its_data_point_is_missing() -> None:
    xml = edit("<AddressLineText>1 Test Way</AddressLineText>", "")
    found = findings(run(xml), "UAD9001")
    assert len(found) == 1 and found[0].severity == "error" and found[0].h1_severity == "Fatal"
    assert found[0].xpath.endswith("PROPERTY[1]") and found[0].message == "Message UAD9001."


def test_a_per_instance_rule_reports_the_instance() -> None:
    xml = edit("2020-02-29", "2020-02")
    found = findings(run(xml), "UAD9003")
    assert [f.xpath.rsplit("/", 1)[-1] for f in found] == ["SALES_HISTORY"]
    assert "UAD9003" not in rules(run(VALID))


def test_a_comparable_rule_runs_per_comparable_and_is_a_warning() -> None:
    xml = edit("<PROPERTY_DETAIL><AttachmentType>Attached</AttachmentType></PROPERTY_DETAIL>", "")
    found = findings(run(xml), "UAD9004")
    assert len(found) == 1 and found[0].severity == "warning"
    assert found[0].xpath.endswith("PROPERTY[2]")
    # the subject without AttachmentType is not a comparable finding
    subject = edit("<AttachmentType>Detached</AttachmentType>", "")
    assert "UAD9004" not in rules(run(subject))


def test_a_relationship_rule_needs_a_link_from_the_named_container() -> None:
    link = f'xlink:arcrole="{URN}" xlink:from="DATA_SOURCE_1" xlink:to="SALES_HISTORY_1"'
    no_link = edit(f"<RELATIONSHIP {link}/>", "")
    assert "UAD9005" in rules(run(no_link))
    not_pud = edit("<PUDIndicator>true", "<PUDIndicator>false", no_link)
    assert "UAD9005" not in rules(run(not_pud))


def test_a_for_phrase_binds_the_rule_to_each_matching_instance() -> None:
    assert "UAD9016" not in rules(run(VALID))
    xml = edit("<PROPERTY_DETAIL><AttachmentType>Attached</AttachmentType></PROPERTY_DETAIL>", "")
    found = findings(run(xml), "UAD9016")
    assert [f.xpath.rsplit("/", 1)[-1] for f in found] == ["PROPERTY[2]"]
    # the subject without AttachmentType is not a SalesComparable instance
    assert "UAD9016" not in rules(run(edit("<AttachmentType>Detached</AttachmentType>", "")))


def test_a_rule_reading_a_repeating_container_runs_per_instance() -> None:
    # SaleType and OwnershipTransferDate are read from the same SALES_HISTORY (PUDIndicator,
    # one per property, does not move the anchor): the estate sale without a date fires
    # although another sale has one
    estate = "<SALES_HISTORY><SaleType>Estate</SaleType></SALES_HISTORY>"
    end = VALID.index("</SALES_HISTORIES>")
    found = findings(run(VALID[:end] + estate + VALID[end:]), "UAD9017")
    assert [f.xpath.rsplit("/", 1)[-1] for f in found] == ["SALES_HISTORY[2]"]
    dated = estate.replace(
        "</SaleType>", "</SaleType><OwnershipTransferDate>2021-03-01</OwnershipTransferDate>"
    )
    assert "UAD9017" not in rules(run(VALID[:end] + dated + VALID[end:]))


def test_a_report_level_rule_reads_property_data_from_the_subject() -> None:
    long_comp = edit("2 Test Way<", "2 Test Way 9<")  # 12 characters, on the comparable
    assert "UAD9019" not in rules(run(long_comp))
    long_subject = edit("1 Test Way<", "1 Test Way 9<")
    assert "UAD9019" in rules(run(long_subject))


def test_a_with_phrase_keeps_its_and_comparisons_on_the_same_instance() -> None:
    new = edit("<NewConstructionIndicator>false", "<NewConstructionIndicator>true")
    assert "UAD9020" in rules(run(new))  # no estate sale at all
    end = new.index("</SALES_HISTORIES>")
    recent = "<SALES_HISTORY><OwnershipTransferDate>{}</OwnershipTransferDate><SaleType>Estate"
    recent += "</SaleType></SALES_HISTORY>"
    assert "UAD9020" not in rules(run(new[:end] + recent.format("2021-03-01") + new[end:]))
    # an estate sale before 2021 and a later sale that is not an estate sale: no instance has
    # both, so the rule fires
    later = edit("2020-02-29", "2021-06-01", new)
    end = later.index("</SALES_HISTORIES>")
    found = findings(run(later[:end] + recent.format("2020-01-01") + later[end:]), "UAD9020")
    assert len(found) == 1


def test_an_element_reached_from_every_comparable_is_reported_once() -> None:
    start = VALID.index('<PROPERTY ValuationUseType="SalesComparable"')
    end = VALID.index("</PROPERTY>", start) + len("</PROPERTY>")
    second = VALID[start:end].replace("PROPERTY_1", "PROPERTY_2")
    xml = edit("<DataSourceType>MLS</DataSourceType>", "", VALID[:end] + second + VALID[end:])
    found = findings(run(xml), "UAD9018")
    assert len(found) == 1 and found[0].xpath.endswith("DATA_SOURCES/DATA_SOURCE")


def test_a_string_enumeration_is_closed_unless_its_note_opens_it() -> None:
    named = edit(
        "<DataSourceType>MLS</DataSourceType>",
        "<DataSourceType>MLS</DataSourceType><DataSourceName>Gamma Guide</DataSourceName>",
    )
    assert "A1.enum" not in rules(run(named))
    content = edit(
        "<DataSourceType>MLS</DataSourceType>",
        "<DataSourceType>MLS</DataSourceType>"
        "<DataSourceContentIdentifier>Spec v2</DataSourceContentIdentifier>",
    )
    assert "A1.enum" in rules(run(content))


def test_a_numeric_comparison_rule() -> None:
    assert "UAD9006" in rules(run(edit(">2.5<", ">.5<")))
    assert "UAD9006" not in rules(run(edit(">2.5<", ">1<")))


def test_connectives_read_left_to_right() -> None:
    # "If A, or (B and C) and D" is "(A or (B and C)) and D": with LotSizeAreaMeasure present
    # (D false) the rule holds back even though PUDIndicator (A) is true.
    assert "UAD9011" not in rules(run(VALID))
    no_lot = edit('<LotSizeAreaMeasure AreaUnitOfMeasureType="Acres">2.5</LotSizeAreaMeasure>', "")
    assert "UAD9011" in rules(run(no_lot))
    neither = edit("<PUDIndicator>true", "<PUDIndicator>false", no_lot)
    assert "UAD9011" not in rules(run(neither))


def test_a_date_rule_uses_the_given_day() -> None:
    assert "UAD9012" not in rules(run(VALID, today=dt.date(2026, 1, 1)))
    assert "UAD9012" in rules(run(VALID, today=dt.date(2025, 11, 30)))


def test_not_equal_means_no_value_is_any_listed_one() -> None:
    assert "UAD9013" not in rules(run(VALID))
    assert "UAD9013" in rules(run(edit("<AttachmentType>Detached", "<AttachmentType>Other")))


def test_an_adjustment_that_cannot_repeat_is_reported_once_per_comparable() -> None:
    assert "UAD1758" not in rules(run(VALID))  # the bedroom adjustment may repeat
    view = "<COMPARABLE_ADJUSTMENT><ComparableAdjustmentType>View</ComparableAdjustmentType>"
    twice = edit(view, view + "\n     </COMPARABLE_ADJUSTMENT>" + view)
    found = findings(run(twice), "UAD1758")
    assert [f.value for f in found] == ["View"]


def test_not_equal_over_several_values_needs_none_to_equal() -> None:
    # AdjustmentSummaryDescription: IF ComparableAdjustmentType <> "View", read over the
    # comparable's adjustments: one of them is View, so the condition does not hold.
    assert "A1.required" not in rules(run(VALID))
    view = (
        "<COMPARABLE_ADJUSTMENT><ComparableAdjustmentType>View</ComparableAdjustmentType>\n"
        "     </COMPARABLE_ADJUSTMENT>"
    )
    found = findings(run(edit(view, "")), "A1.required")
    assert len(found) == 1 and "AdjustmentSummaryDescription" in found[0].message


def test_counting_phrases_count_inside_the_bound_instance() -> None:
    start = VALID.index('<PROPERTY ValuationUseType="SalesComparable"')
    end = VALID.index("</PROPERTY>", start) + len("</PROPERTY>")
    second = VALID[start:end].replace("PROPERTY_1", "PROPERTY_2")
    two_comps = VALID[:end] + second + VALID[end:]
    assert "UAD9014" not in rules(run(two_comps))  # one View in each comparable
    view = "<COMPARABLE_ADJUSTMENT><ComparableAdjustmentType>View</ComparableAdjustmentType>"
    twice = edit(view, view + "\n     </COMPARABLE_ADJUSTMENT>" + view)
    assert [f.xpath.rsplit("/", 1)[-1] for f in findings(run(twice), "UAD9014")] == ["PROPERTY[2]"]


@pytest.mark.parametrize(
    ("logic", "kind"),
    [
        ('If X = "a" or "b", and Y is not provided', "and"),
        ('If there is no instance of DATA_SOURCE with DataSourceType = "MLS"', "count"),
        ("If AddressLineText length exceeds 5 characters", "length"),
        ("If AddressLineText is not unique across all instances of ADDRESS", "unique"),
        (
            "If PostalCode is not in the required format (5 digits, or 5 digits, a hyphen, and 4"
            " digits)",
            "pattern",
        ),
        (
            "If AppraisalReportEffectiveDate is more than 367 days before the current date",
            "days_before",
        ),
        (
            "If LatitudeIdentifier is not provided between -90 and 90 with at least 5 digits of"
            " precision to the right of the decimal point",
            "range",
        ),
    ],
)
def test_the_parser_covers_the_h1_phrases(logic: str, kind: str) -> None:
    spec = SPEC
    if logic.startswith('If X = "a"'):
        logic = logic.replace("X", "AttachmentType").replace("Y", "PUDIndicator")
    expr, scope = u._Parser(logic, spec).parse()
    assert expr.kind == kind and scope is None


def test_value_lists_and_hoisted_instance_scopes() -> None:
    expr, scope = u._Parser(
        'If AttachmentType = "Attached" or "Other", and OwnershipTransferDate is not provided'
        " in a given instance of SALES_HISTORY",
        SPEC,
    ).parse()
    assert expr.kind == "and"
    assert expr.items[0].cond == u.Cond("AttachmentType", "=", ("Attached", "Other"))
    assert scope == u.Scope("instance", "SALES_HISTORY")


# --------------------------------------------------------------------------- CLI / loader


def _xlsx(path: Path, sheets: dict[str, Sequence[Sequence[str]]]) -> None:
    """A minimal workbook (inline strings) readable by uad36_spec.read_sheet."""
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    def col(i: int) -> str:
        name = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            name = chr(65 + r) + name
        return name

    def esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    with zipfile.ZipFile(path, "w") as book:
        entries = "".join(
            f'<sheet name="{esc(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
            for i, n in enumerate(sheets)
        )
        book.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>{entries}</sheets></workbook>',
        )
        rels = "".join(
            f'<Relationship Id="rId{i + 1}" Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(sheets))
        )
        book.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>",
        )
        for i, rows in enumerate(sheets.values()):
            body = "".join(
                f'<row r="{r + 1}">'
                + "".join(
                    f'<c r="{col(c)}{r + 1}" t="inlineStr"><is><t>{esc(v)}</t></is></c>'
                    for c, v in enumerate(row)
                    if v
                )
                + "</row>"
                for r, row in enumerate(rows)
            )
            book.writestr(
                f"xl/worksheets/sheet{i + 1}.xml",
                f'<worksheet xmlns="{main}"><sheetData>{body}</sheetData></worksheet>',
            )


def test_the_cli_loads_the_workbooks_and_writes_json_and_the_rules_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    forms = tmp_path / "appraisal" / "forms"
    forms.mkdir(parents=True)
    _xlsx(forms / u.A1_FILE, {k: SHEETS[k] for k in (u.A1_SHEET, u.A1_ENUM_SHEET, u.A1_ARC_SHEET)})
    _xlsx(forms / u.H1_FILE, {k: SHEETS[k] for k in (u.H1_SHEET, u.H1_ADJ_SHEET)})
    good, bad = tmp_path / "good.xml", tmp_path / "bad.xml"
    good.write_text(VALID)
    bad.write_text(edit("<AddressLineText>1 Test Way</AddressLineText>", ""))
    out, report = tmp_path / "out.json", tmp_path / "rules.md"
    argv = [str(good), str(bad), "--data-dir", str(tmp_path), "--today", "2026-01-15"]
    code = u.main([*argv, "--json", str(out), "--rules-report", str(report)])
    assert code == 1  # an error in bad.xml
    payload = json.loads(out.read_text())
    assert [p["errors"] for p in payload] == [0, 2]
    assert {f["rule"] for f in payload[1]["findings"]} == {"A1.required", "UAD9001"}
    assert "| UAD9001 |" in report.read_text()
    assert "| CR partly mechanical | 1 |" in report.read_text()  # PostalCode: IF Exists
    assert "bad.xml: 2 errors, 0 warnings" in capsys.readouterr().out
    assert u.main([str(good), "--data-dir", str(tmp_path), "--today", "2026-01-15"]) == 0
