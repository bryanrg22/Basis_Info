"""``evals.appraisal.uad36_package`` on a fabricated MISMO-like template and fabricated PDFs.

Nothing here comes from a GSE sample or a client appraisal: the XML below is invented with
MISMO-style element names, and every PDF is drawn with pymupdf. The tests cover the XML fill
(paths, writes, clones, grafts, pruning of unwritten values and dangling links, element
order, namespace declarations), the leftover diff, the PDF editor (value replacement next to
a label, text-only redaction that keeps rules, wipes, glyph stamps, reading-order reflow,
paragraph fitting, the ToUnicode fix for shared glyphs), the surviving-span check, page
renumbering and the deterministic ZIP.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf
import pytest

from evals.appraisal import uad36_package as u

NS = "http://www.mismo.org/residential/2009/schemas"
XL = "http://www.w3.org/1999/xlink"

TEMPLATE = f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- a notice that must not survive -->
<MESSAGE xmlns="{NS}" xmlns:xlink="{XL}" xmlns:gse="http://example.org/ext"
         MISMOReferenceModelIdentifier="3.6.0">
  <PROPERTIES>
    <PROPERTY ValuationUseType="SubjectProperty" xlink:label="P_SUBJECT">
      <ADDRESS>
        <AddressLineText>1 Template Way</AddressLineText><CityName>Oldtown</CityName>
      </ADDRESS>
      <SITE><LotSizeAreaMeasure AreaUnitOfMeasureType="Acres">9.5</LotSizeAreaMeasure></SITE>
      <POOL><PoolType>Heated</PoolType></POOL>
    </PROPERTY>
    <PROPERTY ValuationUseType="SalesComparable" xlink:label="P_COMP1">
      <ADDRESS><AddressLineText>7 Sample St</AddressLineText></ADDRESS>
    </PROPERTY>
  </PROPERTIES>
  <RELATIONSHIPS>
    <RELATIONSHIP xlink:from="P_COMP1" xlink:to="P_SUBJECT" xlink:arcrole="urn:x/IsComparableFor"/>
    <RELATIONSHIP xlink:from="P_SUBJECT" xlink:to="P_SUBJECT" xlink:arcrole="urn:x/Self"/>
  </RELATIONSHIPS>
</MESSAGE>
"""

SECOND = f"""<?xml version="1.0" encoding="UTF-8"?>
<MESSAGE xmlns="{NS}">
  <COST_APPROACH><COST_APPROACH_DETAIL>
    <SiteEstimatedValueAmount>111000</SiteEstimatedValueAmount>
    <ValueIndicatedByCostApproachAmount>222000</ValueIndicatedByCostApproachAmount>
  </COST_APPROACH_DETAIL></COST_APPROACH>
</MESSAGE>
"""


@pytest.fixture
def template(tmp_path: Path) -> Path:
    path = tmp_path / "template.xml"
    path.write_text(TEMPLATE)
    return path


@pytest.fixture
def second(tmp_path: Path) -> Path:
    path = tmp_path / "second.xml"
    path.write_text(SECOND)
    return path


# --- XML -------------------------------------------------------------------------------


def test_qualify_handles_predicates_and_prefixes() -> None:
    q = u.qualify(".//PROPERTY[@ValuationUseType='SubjectProperty']/ADDRESS/CityName", NS)
    assert q == (
        f".//{{{NS}}}PROPERTY[@ValuationUseType='SubjectProperty']/{{{NS}}}ADDRESS/{{{NS}}}CityName"
    )
    assert u.qualify("A[@xlink:label='x/y']", NS) == f"{{{NS}}}A[@{{{XL}}}label='x/y']"
    assert u.qualify("A[2]/B[C='t']", NS) == f"{{{NS}}}A[2]/{{{NS}}}B[{{{NS}}}C='t']"
    assert u.qualify("../A", NS) == f"../{{{NS}}}A"


def test_fill_writes_prunes_and_keeps_declarations(template: Path, tmp_path: Path) -> None:
    fill = u.XmlFill(template)
    subject = fill.find(".//PROPERTY[@ValuationUseType='SubjectProperty']")
    fill.set("ADDRESS/AddressLineText", "2 New Road", subject)
    fill.set("SITE/LotSizeAreaMeasure", 1.25, subject, AreaUnitOfMeasureType="Acres")
    fill.add(subject, "PROPERTY_DETAIL/PUDIndicator", False)
    with pytest.raises(ValueError, match="has children"):
        fill.set("ADDRESS", "x", subject)
    report = fill.prune()
    # CityName and PoolType were never written; POOL then empties; the comparable keeps
    # nothing written, so it goes, and so does the relationship that pointed at it
    assert report.leaves == 3  # CityName, PoolType, the comp's AddressLineText
    assert report.relationships == 1
    out = tmp_path / "out.xml"
    fill.write(out, comment=" synthetic ")
    text = out.read_text()
    assert "a notice" not in text and "<!-- synthetic -->" in text
    assert 'xmlns:gse="http://example.org/ext"' in text  # unused, but kept like the template
    assert 'MISMOReferenceModelIdentifier="3.6.0"' in text
    root = ET.parse(out).getroot()
    values = [(u.local(e.tag), (e.text or "").strip()) for e in root.iter() if not len(e)]
    assert ("AddressLineText", "2 New Road") in values
    assert ("LotSizeAreaMeasure", "1.25") in values
    assert ("PUDIndicator", "false") in values
    assert not any(name in ("CityName", "PoolType", "POOL") for name, _ in values)
    arcs = [e.get(f"{{{XL}}}arcrole") for e in root.iter(f"{{{NS}}}RELATIONSHIP")]
    assert arcs == ["urn:x/Self"]  # both ends still exist


def test_clone_graft_keep_and_order(template: Path, second: Path, tmp_path: Path) -> None:
    fill = u.XmlFill(template)
    comp = fill.find(".//PROPERTY[@ValuationUseType='SalesComparable']")
    fill.set("ADDRESS/AddressLineText", "7 Changed St", comp)
    twin = fill.clone(comp)
    twin.set(f"{{{XL}}}label", "P_COMP2")
    fill.set("ADDRESS/AddressLineText", "9 Other Ave", twin)
    cost = ET.parse(second).getroot().find(u.qualify("COST_APPROACH", NS))
    assert cost is not None
    grafted = fill.graft(cost, fill.root)
    fill.set("COST_APPROACH_DETAIL/SiteEstimatedValueAmount", 5000, grafted)
    subject = fill.find(".//PROPERTY[@ValuationUseType='SubjectProperty']")
    fill.add(subject, "PROPERTY_DETAIL/PUDIndicator", False)  # a name no template uses
    site = fill.find("SITE", subject)
    fill.set("LotSizeAreaMeasure", 2, site)
    subject.remove(site)
    subject.insert(len(subject), site)  # SITE now after PROPERTY_DETAIL: out of sequence
    rel = fill.find("RELATIONSHIPS")
    link = fill.add(rel, "RELATIONSHIP")
    link.set(f"{{{XL}}}from", "P_GONE")
    fill.keep(link)  # kept on purpose although its end does not exist
    fill.prune()
    fill.order_like(second)
    # the template's sequence (ADDRESS, SITE, ...) first, the unknown name after
    assert [u.local(c.tag) for c in subject] == ["SITE", "PROPERTY_DETAIL"]  # ADDRESS pruned
    assert [u.local(c.tag) for c in fill.root] == ["COST_APPROACH", "PROPERTIES", "RELATIONSHIPS"]
    out = tmp_path / "out.xml"
    fill.write(out)
    root = ET.parse(out).getroot()
    lines = [e.text for e in root.iter(f"{{{NS}}}AddressLineText")]
    assert lines == ["7 Changed St", "9 Other Ave"]
    detail = [u.local(e.tag) for e in root.iter() if u.local(e.tag).endswith("Amount")]
    assert detail == ["SiteEstimatedValueAmount"]  # the unwritten grafted value is pruned
    assert any(e.get(f"{{{XL}}}from") == "P_GONE" for e in root.iter(f"{{{NS}}}RELATIONSHIP"))


def test_xml_leftovers(template: Path, second: Path, tmp_path: Path) -> None:
    fill = u.XmlFill(template)
    subject = fill.find(".//PROPERTY[@ValuationUseType='SubjectProperty']")
    fill.set("ADDRESS/AddressLineText", "2 New Road", subject)
    fill.set("SITE/LotSizeAreaMeasure", "9.5", subject)  # the client's value equals the template's
    fill.prune()
    out = tmp_path / "out.xml"
    fill.write(out)
    clean = u.xml_leftovers(out, [template, second], fill.manifest())
    assert clean.ok and clean.checked == 2
    assert [v for _, v in clean.declared_equal_template] == ["9.5"]
    # a template value left in without being written, and a value nobody declared
    root = ET.parse(out).getroot()
    address = root.find(f".//{{{NS}}}ADDRESS")
    assert address is not None
    ET.SubElement(address, f"{{{NS}}}CityName").text = "Oldtown"
    ET.SubElement(address, f"{{{NS}}}PostalCode").text = "00000"
    ET.ElementTree(root).write(out)
    bad = u.xml_leftovers(out, [template, second], fill.manifest())
    kinds = {lo.value: lo.kind for lo in bad.leftovers}
    assert kinds == {"Oldtown": "template_value", "00000": "undeclared"}
    # structural tags are not counted
    assert u.xml_leftovers(
        out, [template], fill.manifest(), structural_tags=("CityName", "PostalCode")
    ).ok


# --- PDF -------------------------------------------------------------------------------


def _font() -> u.FontFace:
    return u.FontFace("TstR", pymupdf.Font("helv").buffer)


def _form(path: Path) -> Path:
    """A fabricated form page: bold captions, values, a rule, a checkbox pair, a photo box."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((40, 100), "Opinion of Value", fontsize=9, fontname="hebo")
    page.insert_text((181, 100), "$111,000", fontsize=9, fontname="helv")
    page.insert_text((40, 113), "Owner Name", fontsize=9, fontname="hebo")
    page.insert_text((181, 113), "Pat Template", fontsize=9, fontname="helv")
    page.insert_text((181, 126), "Second Owner", fontsize=9, fontname="helv")
    page.insert_text((40, 139), "County", fontsize=9, fontname="hebo")
    page.insert_text((181, 139), "Oldcounty", fontsize=9, fontname="helv")
    page.draw_line((36, 103), (300, 103), color=(0.5, 0.5, 0.5), width=0.5)
    page.insert_text((40, 160), "Pool", fontsize=9, fontname="hebo")
    page.insert_text((181, 160), "Yes No", fontsize=9, fontname="zadb")  # glyph pair stand-in
    page.draw_rect(pymupdf.Rect(320, 80, 560, 200), color=None, fill=(0.9, 0.9, 0.9))
    page.insert_text((360, 140), "Photo placeholder", fontsize=9, fontname="helv")
    page.insert_text((500, 40), "Page 3 of 9", fontsize=9, fontname="helv")
    doc.save(path)
    return path


def _words(page: pymupdf.Page) -> str:
    return " ".join(page.get_text().split())


def test_set_value_replaces_the_block_and_keeps_the_rule(tmp_path: Path) -> None:
    doc = pymupdf.open(_form(tmp_path / "form.pdf"))
    page = doc[0]
    ed = u.PageEditor(page, _font())
    owner = ed.find("Owner Name")
    block = ed.value_block(owner, right=300)
    assert [s.clean for s in block] == ["Pat Template", "Second Owner"]
    ed.set_value("Opinion of Value", "$222,000", right=300)
    ed.set_value(owner, "New Owner-Smith", right=300)  # one line: the second is blanked
    ed.set_value("County", None, right=300)
    ed.commit()
    text = _words(page)
    assert "$222,000" in text and "New Owner-Smith" in text
    for gone in ("$111,000", "Pat Template", "Second Owner", "Oldcounty"):
        assert gone not in text
    for label in ("Opinion of Value", "Owner Name", "County"):
        assert label in text
    rules = [d for d in page.get_drawings() if d["rect"].y0 == pytest.approx(103, abs=0.5)]
    assert rules, "text-only redaction must keep the rule under the value"


def test_wipe_image_and_filled_box(tmp_path: Path) -> None:
    doc = pymupdf.open(_form(tmp_path / "form.pdf"))
    page = doc[0]
    ed = u.PageEditor(page, _font())
    holder = ed.find("Photo placeholder")
    box = u.filled_box_around(page, holder.bbox)
    assert box == pytest.approx((320, 80, 560, 200))
    img = tmp_path / "photo.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    pix.save(img)
    ed.image(box, img)
    ed.commit()
    assert "Photo placeholder" not in _words(page)
    assert len(page.get_images()) == 1


def test_stamp_moves_only_the_glyph(tmp_path: Path) -> None:
    src = pymupdf.open(_form(tmp_path / "form.pdf"))
    stamp = u.make_stamp(src, 0, "Yes No")
    out = pymupdf.open()
    page = out.new_page(width=612, height=792)
    ed = u.PageEditor(page, _font())
    ed.stamp(stamp, (100, 400))
    ed.commit()
    spans = [s for s in u.page_spans(page) if s.clean]
    assert [s.clean for s in spans] == ["Yes No"]  # nothing else of the source page came along
    assert spans[0].bbox[0] == pytest.approx(100, abs=1)
    assert ed.written and ed.written[0][1] == "Yes No"


def test_reflow_puts_values_after_their_captions(tmp_path: Path) -> None:
    doc = pymupdf.open(_form(tmp_path / "form.pdf"))
    page = doc[0]
    plain = u.PageEditor(page, _font())
    plain.set_value("County", "Newcounty", right=300)
    plain.commit()
    lines = page.get_text().splitlines()
    assert lines[-1] == "Newcounty"  # without reflow the new value trails the page
    doc = pymupdf.open(_form(tmp_path / "form2.pdf"))
    page = doc[0]
    ed = u.PageEditor(page, _font(), reflow=True)
    ed.set_value("County", "Newcounty", right=300)
    ed.commit()
    text = _words(page)
    assert "County Newcounty" in text
    assert text.index("Opinion of Value") < text.index("County Newcounty") < text.index("Pool")


def test_paragraph_wraps_and_shrinks(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    ed = u.PageEditor(page, _font())
    words = "alpha beta gamma delta " * 12
    last = ed.paragraph((40, 100, 240, 160), words, size=9, leading=11)
    assert last <= 160
    sizes = {w.size for w in ed._writes}
    assert len(sizes) == 1 and next(iter(sizes)) < 9  # shrank to fit
    with pytest.raises(ValueError, match="does not fit"):
        ed.paragraph((40, 100, 60, 110), words, size=9, min_size=8)


def test_fix_tounicode_maps_shared_glyphs_to_ascii(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="F0", fontbuffer=pymupdf.Font("helv").buffer)
    page.insert_text((40, 100), "A-B C", fontsize=9, fontname="F0")
    xref = next(f[0] for f in page.get_fonts() if f[4] == "F0")
    _, ref = doc.xref_get_key(xref, "ToUnicode")
    stream = int(ref.split()[0])
    cmap = doc.xref_stream(stream).decode("latin-1")
    # simulate a subset whose map names U+00A0 / U+00AD for the space and hyphen glyphs
    doc.update_stream(stream, cmap.replace("<0020>", "<00a0>").replace("<002d>", "<00ad>").encode())
    assert " " in page.get_text() or "­" in page.get_text()
    assert u.fix_tounicode(doc, xref)
    assert not u.fix_tounicode(doc, xref)  # idempotent
    fixed = pymupdf.open(stream=doc.tobytes())
    assert fixed[0].get_text().strip() == "A-B C"


def test_surviving_spans(tmp_path: Path) -> None:
    template = pymupdf.open(_form(tmp_path / "form.pdf"))
    out = pymupdf.open(_form(tmp_path / "copy.pdf"))
    ed = u.PageEditor(out[0], _font())
    labels = [ed.find(t) for t in ("Opinion of Value", "Owner Name", "County", "Pool")]
    ed.keep(*labels)
    ed.replace(ed.find("$111,000"), "$111,000")  # the new value equals the old one
    ed.blank(ed.find("Pat Template"), ed.find("Second Owner"))
    ed.commit()
    left = u.surviving_spans(template[0], out[0], ed.kept, ed.written)
    assert sorted(s.clean for s in left) == [
        "Oldcounty",
        "Page 3 of 9",
        "Photo placeholder",
        "Yes No",
    ]


def test_renumber_pages_handles_duplicate_numbers(tmp_path: Path) -> None:
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        if i:
            for _ in range(2 if i == 2 else 1):  # page 3 prints its number twice
                page.insert_text((500, 40), "Page 7 of 30", fontsize=9, fontname="helv")
            page.insert_text((40, 700), "Footer label", fontsize=9, fontname="helv")
    assert u.renumber_pages(doc, r"Page \d+ of \d+", "Page {n} of {total}", _font(), first=2) == 2
    assert _words(doc[1]).count("Page 2 of 3") == 1
    text = _words(doc[2])
    assert text.count("Page 3 of 3") == 1 and "Page 7" not in text and "Footer label" in text


def test_zip_package_is_deterministic(tmp_path: Path) -> None:
    (tmp_path / "Images").mkdir()
    (tmp_path / "Images" / "b.jpg").write_bytes(b"b")
    (tmp_path / "Images" / "a.jpg").write_bytes(b"a")
    xml = tmp_path / "appraisal.xml"
    pdf = tmp_path / "appraisal.pdf"
    xml.write_text("<MESSAGE/>")
    pdf.write_bytes(b"%PDF-1.7")
    names = u.zip_package(tmp_path / "one.zip", xml, pdf, tmp_path / "Images")
    assert names == ["Images/a.jpg", "Images/b.jpg", "appraisal.pdf", "appraisal.xml"]
    u.zip_package(tmp_path / "two.zip", xml, pdf, tmp_path / "Images")
    assert (tmp_path / "one.zip").read_bytes() == (tmp_path / "two.zip").read_bytes()
    with zipfile.ZipFile(tmp_path / "one.zip") as zf:
        assert zf.read("Images/a.jpg") == b"a"
