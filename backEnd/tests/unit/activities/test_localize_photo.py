"""``localize_photo``: SAM 3 instances of an analyzed photo stored as ``photo_objects``.

The photo is analyzed first by the real ``analyze_photo`` (offline fixture provider), so
the labels the localizer is prompted with come from real component rows. ``FakeLocalizer``
answers per phrase; its scripted instances carry run-length masks. Rows are real
(Postgres container).
"""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np
import pytest
from lifecycletest_support import SeededStudy
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.analyze_photo import AnalyzePhoto
from basis.activities.localize_photo import (
    LOCALIZER_UNAVAILABLE,
    NO_BUDGET,
    NO_LOCALIZER,
    LocalizePhoto,
)
from basis.db.models import PhotoLocalization, PhotoObject
from basis.db.session import session_scope
from basis.vision.localize import (
    CANDIDATE_THRESHOLD,
    Box,
    FakeLocalizer,
    Instance,
    Localization,
    LocalizerBadResponse,
    LocalizerUnavailable,
    Phrase,
    Rle,
)
from basis.vision.prep import PreparedImage
from basis.vision.schemas import ComponentLabel
from basis.workflow.contracts import AnalyzePhotoInput, LocalizePhotoInput

from .conftest import Env

pytestmark = pytest.mark.integration


def instance(label: str, score: float, x1: float = 0.1, with_mask: bool = True) -> Instance:
    # Each instance its own mask (where its box is): identical masks of one label would be
    # suppressed as one object by the localizer's same-label dedupe.
    mask = np.zeros((10, 20), dtype=bool)
    left = int(x1 * 20)
    mask[2:5, left : left + 6] = True
    return Instance(
        label=ComponentLabel(label),
        phrase=label,
        score=score,
        box=Box(x1=x1, y1=0.2, x2=x1 + 0.3, y2=0.5),
        mask=Rle.encode(mask) if with_mask else None,
        area=0.09,
    )


def script() -> dict[str, list[Instance]]:
    return {
        # window: counted at 0.6, so 0.55 is a candidate; 0.2 is below the candidate floor
        "window": [instance("window", 0.9), instance("window", 0.55, 0.5), instance("window", 0.2)],
        "kitchen cabinet": [instance("kitchen_cabinet", 0.52, with_mask=False)],
        "countertop": [instance("countertop", 0.7)],
    }


def localize_input(
    seeded: SeededStudy, index: int = 0, *, budget: float = 1.0, revision: int = 1
) -> LocalizePhotoInput:
    doc = str(seeded.photo_ids[index])
    return LocalizePhotoInput(
        study_id=str(seeded.study_id),
        document_id=doc,
        revision=revision,
        idempotency_key=f"k-localize-{doc}-r{revision}",
        budget_remaining_usd=budget,
    )


async def analyzed(deps: Deps, seeded: SeededStudy, activity_env: Env, index: int = 0) -> None:
    doc = str(seeded.photo_ids[index])
    await activity_env.run(
        AnalyzePhoto(deps).run,
        AnalyzePhotoInput(
            study_id=str(seeded.study_id),
            document_id=doc,
            revision=1,
            idempotency_key=f"k-photo-{doc}-r1",
            budget_remaining_usd=1.0,
        ),
    )


async def stored(deps: Deps, model: type[Any]) -> list[Any]:
    async with session_scope(deps.sessionmaker) as session:
        return list((await session.scalars(select(model))).all())


async def test_instances_are_stored_with_counted_flags_and_masks(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await analyzed(deps, seeded, activity_env)  # kitchen_1.jpg: kitchen_cabinet, countertop
    localizer = FakeLocalizer(script=script(), seconds_per_call=2.0)
    output = await activity_env.run(
        LocalizePhoto(dataclasses.replace(deps, localizer=localizer)).run, localize_input(seeded)
    )
    assert (output.status, output.instances, output.counted) == ("done", 4, 3)
    assert output.cost_usd == pytest.approx(2.0 * 0.000975)

    [(_, phrases, threshold)] = localizer.calls
    assert threshold == CANDIDATE_THRESHOLD  # one full-frame call, candidates kept
    assert {"window", "kitchen cabinet", "countertop", "electrical outlet"} <= set(phrases)
    assert "wall" not in phrases and "carpet" not in phrases  # surfaces are not prompted

    rows = await stored(deps, PhotoObject)
    assert sorted((r.label, round(r.score or 0, 2)) for r in rows) == [
        ("countertop", 0.7),
        ("kitchen_cabinet", 0.52),
        ("window", 0.55),
        ("window", 0.9),
    ]  # the 0.2 window is below the candidate threshold and never stored
    by_label: dict[str, list[PhotoObject]] = {}
    for row in rows:
        by_label.setdefault(row.label, []).append(row)
    windows = sorted(by_label["window"], key=lambda r: -(r.score or 0))
    assert [w.counted for w in windows] == [True, False]  # window threshold 0.6 (10.2)
    assert by_label["kitchen_cabinet"][0].counted is True  # default threshold 0.5
    assert by_label["kitchen_cabinet"][0].mask is None
    top = windows[0]
    assert (top.source, top.created_by, top.revision) == ("model", None, 1)
    assert (top.x1, top.y1, top.x2, top.y2) == pytest.approx((0.1, 0.2, 0.4, 0.5))
    mask = Rle.model_validate(top.mask)
    assert (mask.height, mask.width, mask.area()) == (10, 20, 18)

    [status] = await stored(deps, PhotoLocalization)
    assert (status.status, status.instances, status.reason) == ("done", 4, None)
    assert status.params["count_thresholds"] == {"default": 0.5, "window": 0.6}
    assert status.params["tiling"] is False
    assert status.image_width is not None and status.cost_usd == pytest.approx(0.00195)
    assert activity_env.heartbeats


async def test_a_second_execution_returns_the_stored_outcome(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await analyzed(deps, seeded, activity_env)
    localizer = FakeLocalizer(script=script())
    activity = LocalizePhoto(dataclasses.replace(deps, localizer=localizer))
    first = await activity_env.run(activity.run, localize_input(seeded))
    again = await activity_env.run(
        activity.run, localize_input(seeded).model_copy(update={"idempotency_key": "other"})
    )
    assert len(localizer.calls) == 1  # the row exists: no second prediction
    assert (again.status, again.instances, again.counted, again.cost_usd) == (
        "done",
        first.instances,
        first.counted,
        0.0,
    )
    assert len(await stored(deps, PhotoObject)) == first.instances


async def test_without_a_localizer_the_photo_is_skipped(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await analyzed(deps, seeded, activity_env)
    output = await activity_env.run(LocalizePhoto(deps).run, localize_input(seeded))
    assert (output.status, output.instances, output.reason) == ("skipped", 0, NO_LOCALIZER)
    [status] = await stored(deps, PhotoLocalization)
    assert (status.status, status.model_version) == ("skipped", None)
    assert status.params["labels"]  # what would have been prompted is still recorded
    assert await stored(deps, PhotoObject) == []


async def test_without_budget_the_photo_is_skipped(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await analyzed(deps, seeded, activity_env)
    localizer = FakeLocalizer(script=script())
    output = await activity_env.run(
        LocalizePhoto(dataclasses.replace(deps, localizer=localizer)).run,
        localize_input(seeded, budget=0.0),
    )
    assert (output.status, output.reason) == ("skipped", NO_BUDGET)
    assert localizer.calls == []


class FailingLocalizer(FakeLocalizer):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def localize(
        self, image: PreparedImage, phrases: Any, *, threshold: float
    ) -> Localization:
        self.calls.append((image.sha256, tuple(p.text for p in phrases), threshold))
        raise self.error


async def test_a_throttled_localizer_is_retried_by_temporal_then_recorded_failed(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await analyzed(deps, seeded, activity_env)
    localizer = FailingLocalizer(LocalizerUnavailable("POST /predictions returned 429", status=429))
    activity = LocalizePhoto(dataclasses.replace(deps, localizer=localizer))
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(activity.run, localize_input(seeded))
    assert raised.value.type == LOCALIZER_UNAVAILABLE and not raised.value.non_retryable
    assert await stored(deps, PhotoLocalization) == []  # nothing recorded while retrying

    last = activity_env.with_info(attempt=3)  # PROVIDER_ATTEMPTS
    output = await last.run(activity.run, localize_input(seeded))
    assert output.status == "failed" and "429" in (output.reason or "")
    [status] = await stored(deps, PhotoLocalization)
    assert status.status == "failed" and status.instances == 0


async def test_a_bad_response_is_recorded_failed_at_once(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    await analyzed(deps, seeded, activity_env)
    localizer = FailingLocalizer(LocalizerBadResponse("prediction p1 failed: CUDA OOM"))
    output = await activity_env.run(
        LocalizePhoto(dataclasses.replace(deps, localizer=localizer)).run, localize_input(seeded)
    )
    assert output.status == "failed" and "CUDA OOM" in (output.reason or "")
    assert len(localizer.calls) == 1


async def test_a_photo_of_another_study_is_a_validation_error(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    bad = localize_input(seeded).model_copy(
        update={"document_id": "00000000-0000-4000-8000-000000000000"}
    )
    with pytest.raises(ApplicationError) as raised:
        await activity_env.run(LocalizePhoto(deps).run, bad)
    assert raised.value.non_retryable


def test_phrase_type_is_what_the_fake_is_scripted_by() -> None:
    """The script above keys on phrase texts of the v1 table (guards a silent no-match)."""
    from basis.vision.localize import PHRASES

    texts = {p for phrases in PHRASES.values() for p in phrases}
    assert set(script()) <= texts
    assert Phrase(label=ComponentLabel.WINDOW, text="window").text in texts
