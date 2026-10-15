"""``export_report``: canonical JSON in storage; same revision -> byte-identical output.

BUILD_PLAN 6.2 ``node/test_export_report.py``.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from temporalio.exceptions import ApplicationError
from workflowtest_support import SeededStudy

from basis.activities import Deps
from basis.activities.analyze_photo import AnalyzePhoto
from basis.activities.export_report import ExportReport, canonical_json
from basis.activities.extract_fields import ExtractFields
from basis.activities.process_assets import ProcessAssets
from basis.workflow.contracts import (
    AnalyzePhotoInput,
    ExportReportInput,
    ExtractFieldsInput,
    ProcessAssetsInput,
)

from .conftest import Env

pytestmark = pytest.mark.integration


@pytest.fixture
async def processed(deps: Deps, seeded: SeededStudy, activity_env: Env) -> SeededStudy:
    sid = str(seeded.study_id)
    await activity_env.run(
        ExtractFields(deps).run,
        ExtractFieldsInput(
            study_id=sid, document_id=str(seeded.appraisal_id), revision=1, idempotency_key="k-x"
        ),
    )
    for photo in seeded.photo_ids:
        await activity_env.run(
            AnalyzePhoto(deps).run,
            AnalyzePhotoInput(
                study_id=sid,
                document_id=str(photo),
                revision=1,
                idempotency_key=f"k-p-{photo}",
                budget_remaining_usd=1.0,
            ),
        )
    await activity_env.run(
        ProcessAssets(deps).run,
        ProcessAssetsInput(study_id=sid, revision=1, idempotency_key="k-a", budget_remaining_usd=1),
    )
    return seeded


def export(seeded: SeededStudy, key: str = "k-export") -> ExportReportInput:
    return ExportReportInput(study_id=str(seeded.study_id), revision=1, idempotency_key=key)


async def test_writes_the_revision_as_canonical_json(
    deps: Deps, processed: SeededStudy, activity_env: Env
) -> None:
    output = await activity_env.run(ExportReport(deps).run, export(processed))
    assert output.export_uri == f"gs://basis-test/studies/{processed.study_id}/exports/rev-1.json"
    data = await deps.storage.read(output.export_uri)
    assert hashlib.sha256(data).hexdigest() == output.sha256
    document = json.loads(data)
    assert canonical_json(document) == data  # sorted keys, no whitespace
    assert (document["format"], document["revision"]) == ("basis-export/1", 1)
    assert len(document["extraction"][0]["fields"]) == 5
    assert (len(document["rooms"]), len(document["photos"]), len(document["components"])) == (
        2,
        3,
        6,
    )
    assert len(document["assets"]) == 6
    for name in ("extraction", "rooms", "photos", "components", "assets"):
        expected = hashlib.sha256(canonical_json(document[name])).hexdigest()
        assert document["checksums"][name] == expected
    stat = await deps.storage.stat(output.export_uri)
    assert stat.content_type == "application/json"


async def test_same_revision_exports_byte_identical_output(
    deps: Deps, processed: SeededStudy, activity_env: Env
) -> None:
    activity = ExportReport(deps)
    first = await activity_env.run(activity.run, export(processed))
    first_bytes = await deps.storage.read(first.export_uri)
    second = await activity_env.run(activity.run, export(processed, key="k-export-again"))
    assert second.sha256 == first.sha256
    assert await deps.storage.read(second.export_uri) == first_bytes


async def test_unknown_study_is_not_retried(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    payload = ExportReportInput(
        study_id="00000000-0000-0000-0000-000000000000", revision=1, idempotency_key="k"
    )
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ExportReport(deps).run, payload)
    assert caught.value.non_retryable
