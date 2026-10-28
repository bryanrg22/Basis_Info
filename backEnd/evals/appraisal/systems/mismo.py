"""``mismo_xml``: the UAD 3.6 XML reader (:mod:`basis.extraction.mismo36`).

A UAD 3.6 delivery carries the MISMO 3.6 XML next to the PDF. This system reads the document's
sibling ``appraisal.xml`` (the truth set's PDF path with the file name replaced), and locates each
value on the PDF with the recorded Azure DI layout of that PDF (the regions and the
``xml_pdf_mismatch`` cross-check). No model is called. A document without an XML (a PDF-only
appraisal) is a :class:`MissingInput`: this system does not fall back to the PDF reader.
"""

from __future__ import annotations

from typing import Final

from basis.extraction.mismo36 import extract_mismo
from basis.extraction.regions import region_json
from evals.appraisal.systems.base import MissingInput, SystemEnv, SystemOutput

XML_NAME: Final = "appraisal.xml"


class MismoXml:
    name = "mismo_xml"

    async def run(self, env: SystemEnv) -> SystemOutput:
        if env.pdf_path is None:
            raise MissingInput("mismo_xml needs the document's path to find its XML")
        xml_path = env.pdf_path.with_name(XML_NAME)
        if not xml_path.is_file():
            raise MissingInput(f"no {XML_NAME} next to {env.pdf_path.name}")
        layout = await env.sources.geometry(env.pdf) if env.sources.has_layout else None
        result = extract_mismo(
            xml_path.read_bytes(),
            env.doc_id,
            layout=layout,
            page_count=len(layout.pages) if layout is not None else 0,
            review_threshold=env.extraction.review_threshold,
        )
        regions = (
            {key: region_json(item.regions) for key, item in result.fields.items()}
            if layout is not None
            else None
        )
        return SystemOutput(result=result, regions=regions)
