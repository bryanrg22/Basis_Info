"""``extract_fields`` with ``TAKEOFF_SKETCH=on``: the ``gla_vs_sketch`` flag through a
request for corrections (EVAL_REPORT 96; docs/api/REVIEW_RULES.md section 4.1).

The appraisal is the sample PDF with a fabricated sketch page added to its document
analysis. A sketch whose living area is not the GLA box's flags the appraisal; the engineer
correcting the GLA to the sketch's figure clears the flag on the next revision, where the
takeoff will then use the sketch; a correction of another field leaves it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from lifecycletest_support import SeededStudy, make_deps
from llm_fakes import load_azure_fixture

from basis.activities import Deps
from basis.activities.extract_fields import ExtractFields
from basis.domain.measurements import measurements_from_fields
from basis.extraction.azure_di import AzureDiPage, AzureDiResult
from basis.workflow.contracts import CorrectionInput as Correction

from .conftest import Env
from .test_extract_fields import StaticAzure, extract, extraction, fields, with_deps

pytestmark = pytest.mark.integration

REASON = "gla_vs_sketch:improvements.gla_sqft,sketch.living_area_sqft"
SKETCH_PAGE = [
    "Area Calculations Summary",
    "Living Area",
    "First Floor",
    "1200 Sq ft",
    "40 x 30 = 1200",
    "Second Floor",
    "900 Sq ft",
    "30 x 30 = 900",
    "Total Living Area (Rounded):",
    "2100 Sq ft",
]


def with_sketch() -> AzureDiResult:
    base = load_azure_fixture()
    page = AzureDiPage(page_number=9, lines=tuple(SKETCH_PAGE))
    return base.model_copy(update={"pages": (*base.pages, page)})


def sketch_on(deps: Deps) -> Deps:
    settings = deps.settings.model_copy(update={"takeoff_sketch": "on"})
    return make_deps(
        deps.sessionmaker, deps.storage, settings, llm=deps.llm, azure_di=StaticAzure(with_sketch())
    )


async def test_off_a_sketch_in_the_document_raises_nothing(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    activity = ExtractFields(with_deps(deps, azure_di=StaticAzure(with_sketch())))
    out = await activity_env.run(activity.run, extract(seeded))
    row = await extraction(deps, out.extraction_id or "")
    assert REASON not in row.raw["review_reasons"] and "sketch" not in row.raw
    assert not any(key.startswith("sketch.") for key in await fields(deps, out.extraction_id or ""))


async def test_a_disagreeing_sketch_flags_the_appraisal_and_a_corrected_gla_clears_it(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    activity = ExtractFields(sketch_on(deps))
    rev1 = await activity_env.run(activity.run, extract(seeded))
    assert rev1.needs_review
    row = await extraction(deps, rev1.extraction_id or "")
    assert REASON in row.raw["review_reasons"]
    assert row.raw["sketch"]["gla_tolerance"] == "0.01"
    old = await fields(deps, rev1.extraction_id or "")
    living = old["sketch.living_area_sqft"]
    assert (living.value_num, living.page, living.source) == (Decimal(2100), 9, "azure")
    assert old["improvements.gla_sqft"].value_num != Decimal(2100)
    # The takeoff of this revision would not use the sketch.
    refused = measurements_from_fields(old.values(), sketch_tolerance=Decimal("0.01")).sketch
    assert refused is not None and not refused.accepted

    # A correction elsewhere reads the document again and the flag is still there.
    other = (
        Correction(
            entity_type="extraction_field",
            entity_id=str(old["subject.city"].id),
            after={"value_text": "Springfield"},
        ),
    )
    rev2 = await activity_env.run(activity.run, extract(seeded, 2, corrections=other))
    row2 = await extraction(deps, rev2.extraction_id or "")
    assert rev2.needs_review and REASON in row2.raw["review_reasons"]

    # The engineer agrees with the sketch and corrects the GLA box.
    now = await fields(deps, rev2.extraction_id or "")
    fix = (
        Correction(
            entity_type="extraction_field",
            entity_id=str(now["improvements.gla_sqft"].id),
            after={"value_num": "2100", "value_text": "2,100"},
        ),
    )
    rev3 = await activity_env.run(activity.run, extract(seeded, 3, corrections=fix))
    row3 = await extraction(deps, rev3.extraction_id or "")
    assert REASON not in row3.raw["review_reasons"]
    assert not any(issue["rule"] == "gla_vs_sketch" for issue in row3.raw["issues"])
    final = await fields(deps, rev3.extraction_id or "")
    assert final["improvements.gla_sqft"].source == "manual"
    accepted = measurements_from_fields(final.values(), sketch_tolerance=Decimal("0.01")).sketch
    assert accepted is not None and accepted.accepted
