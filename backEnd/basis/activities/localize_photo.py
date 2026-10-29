"""``localize_photo``: every instance of the photo's countable objects, as box and mask.

Runs after ``analyze_photo`` succeeded for the photo and revision (the workflow schedules
it, at most ``LOCALIZER_CONCURRENCY`` per study, behind the rooms review since EVAL_REPORT
section 30). What it does, following EVAL_REPORT section 10:

* Labels: the photo's component labels at this revision without the surfaces
  (:func:`basis.vision.localize.object_labels`), always with the countable set; phrases
  from the default phrase table (:data:`~basis.vision.localize.DEFAULT_PHRASE_SET`, ``v3``
  since EVAL_REPORT section 19; :func:`~basis.vision.localize.phrases_for`).
* One full-frame SAM 3 prediction (no tiling: 10.3 measured it hurting on these photos)
  at :data:`~basis.vision.localize.CANDIDATE_THRESHOLD`, same-label duplicates found by two
  phrases suppressed by the localizer as in the harness. Every instance is stored; it is
  ``counted`` when its score clears its class's threshold (the phrase set's table, section
  19 for v3), so the counted set is exactly what the harness scored and the rest are candidates an
  engineer's tap can select (``POST /v1/studies/{id}/photos/{document_id}/objects``).
* The photo is prepared exactly as for the vision call (:func:`~basis.vision.prep.prepare_image`),
  so boxes are normalized to the same image and masks are at its resolution.

Outcome, one ``photo_localizations`` row per photo and revision:

* ``done``: the localizer answered (``instances`` rows written, possibly none);
* ``skipped``: no localizer configured (neither ``LOCALIZER_URL``, a self-hosted SAM 3
  Cog server, nor ``REPLICATE_API_TOKEN`` set) or no budget left;
* ``failed``: the localizer did not answer. Rate limits, 5xx and timeouts are first retried
  inside the localizer (bounded backoff, ``Retry-After`` honoured: the Replicate throttle
  answers 429 for minutes), then by Temporal (``LocalizerUnavailable`` is retryable) while
  attempts remain; on the last attempt, and at once for an error a retry cannot fix (a
  rejected request, a failed prediction, an unreadable result, an image that cannot be
  prepared), the failure is recorded and the activity completes. A failed localization
  never fails the photo or the study (the workflow also tolerates the activity failing).

Heartbeats every few seconds while the prediction is awaited, so a lost worker is noticed
within the heartbeat timeout even while the localizer sleeps between retries. The cost
(Replicate predict seconds, or a self-hosted server's request seconds) is returned and
the workflow charges it to the study budget; the localizer is shared by concurrent
activities, so the budget is checked here rather than reserved inside it. Idempotent per
photo and revision: an existing row is returned.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.exceptions import ApplicationError

from basis.activities._model_calls import heartbeating
from basis.activities._support import heartbeat, parse_uuid, run_once, validation_error
from basis.activities.deps import Deps
from basis.db.enums import DocumentKind
from basis.db.models import Component, PhotoLocalization
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.photo_object import NewObject, PhotoObjectRepository
from basis.db.session import session_scope
from basis.errors.codes import E, tag
from basis.storage import ObjectNotFound
from basis.vision.localize import (
    CANDIDATE_THRESHOLD,
    COUNT_THRESHOLD_DEFAULT,
    COUNT_THRESHOLDS,
    DEFAULT_PHRASE_SET,
    Instance,
    Localization,
    LocalizerError,
    LocalizerUnavailable,
    count_threshold,
    object_labels,
    phrases_for,
)
from basis.vision.prep import ImageRejected, prepare_image
from basis.workflow.activity_options import options_for
from basis.workflow.contracts import LocalizePhotoInput, LocalizePhotoOutput

ACTIVITY: Final = "localize_photo"
LOCALIZER_UNAVAILABLE: Final = "LocalizerUnavailable"
PHRASE_SET: Final = DEFAULT_PHRASE_SET
NO_LOCALIZER: Final = "no localizer configured (LOCALIZER_URL or REPLICATE_API_TOKEN)"
NO_BUDGET: Final = "the study budget is exhausted"
MAX_REASON: Final = 500


class LocalizePhoto:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="localize_photo")
    async def run(self, input: LocalizePhotoInput) -> LocalizePhotoOutput:
        study_id = parse_uuid(input.study_id, "study_id")
        document_id = parse_uuid(input.document_id, "document_id")
        heartbeat("start")
        async with session_scope(self._deps.sessionmaker) as session:
            document = await DocumentRepository(session).get(study_id, document_id)
            if document is None or document.kind is not DocumentKind.PHOTO:
                raise validation_error(
                    f"photo {document_id} not found in study {study_id}", E.LOCALIZE_PHOTO_NOT_FOUND
                )
            objects = PhotoObjectRepository(session)
            existing = await objects.localization(document_id, input.revision)
            if existing is not None:
                return await _output(objects, study_id, existing)
            labels = await _component_labels(session, study_id, document_id, input.revision)
            gcs_uri = document.gcs_uri

        chosen = object_labels(labels)
        phrases = phrases_for(chosen, PHRASE_SET)
        params: dict[str, Any] = {
            "labels": [label.value for label in chosen],
            "phrases": [p.text for p in phrases],
            "phrase_set": PHRASE_SET,
            "candidate_threshold": CANDIDATE_THRESHOLD,
            "count_thresholds": {
                "default": COUNT_THRESHOLD_DEFAULT,
                **{label.value: value for label, value in COUNT_THRESHOLDS.items()},
            },
            "tiling": False,
        }
        localizer = self._deps.localizer
        found: Localization | None = None
        status, reason = "done", None
        if localizer is None:
            status, reason = "skipped", tag(E.PROVIDER_NO_LOCALIZER, NO_LOCALIZER)
        elif input.budget_remaining_usd <= 0:
            status, reason = "skipped", tag(E.BUDGET_LOCALIZE_SKIPPED, NO_BUDGET)
        else:
            try:
                image = prepare_image(await self._deps.storage.read(gcs_uri))
                async with heartbeating("localizing"):
                    found = await localizer.localize(image, phrases, threshold=CANDIDATE_THRESHOLD)
            except ObjectNotFound:
                status = "failed"
                reason = tag(
                    E.LOCALIZE_FILE_MISSING, f"photo file {gcs_uri} is missing from storage"
                )
            except ImageRejected as exc:
                status = "failed"
                reason = tag(E.LOCALIZE_IMAGE_REJECTED, f"the photo cannot be prepared: {exc}")
            except LocalizerUnavailable as exc:
                if activity.info().attempt < options_for(ACTIVITY).maximum_attempts:
                    raise ApplicationError(
                        tag(E.PROVIDER_LOCALIZER_UNAVAILABLE, f"photo {document_id}: {exc}"),
                        type=LOCALIZER_UNAVAILABLE,
                    ) from exc
                status = "failed"
                reason = tag(E.PROVIDER_LOCALIZER_UNAVAILABLE, f"{type(exc).__name__}: {exc}")
            except LocalizerError as exc:
                status = "failed"
                reason = tag(E.PROVIDER_LOCALIZER_FAILED, f"{type(exc).__name__}: {exc}")

        model_version = getattr(localizer, "version", None) if localizer is not None else None

        async def work(session: AsyncSession) -> LocalizePhotoOutput:
            repo = PhotoObjectRepository(session)
            written = await repo.localization(document_id, input.revision)
            if written is not None:
                return await _output(repo, study_id, written)
            instances = list(found.instances) if found is not None else []
            for instance in instances:
                heartbeat("instance")
                await repo.add(
                    study_id=study_id,
                    document_id=document_id,
                    revision=input.revision,
                    new=model_object(instance),
                )
            row = await repo.record_localization(
                study_id=study_id,
                document_id=document_id,
                revision=input.revision,
                status=status,
                reason=None if reason is None else reason[:MAX_REASON],
                instances=len(instances),
                model_version=str(model_version) if model_version is not None else None,
                params=params,
                image_width=found.width if found is not None else None,
                image_height=found.height if found is not None else None,
                predict_seconds=round(found.predict_seconds, 3) if found is not None else None,
                cost_usd=round(found.cost_usd, 6) if found is not None else None,
            )
            counted = sum(1 for i in instances if i.score >= count_threshold(i.label))
            return LocalizePhotoOutput(
                document_id=str(document_id),
                status=status,  # type: ignore[arg-type]
                instances=len(instances),
                counted=counted,
                cost_usd=row.cost_usd or 0.0,
                reason=row.reason,
            )

        return await run_once(
            self._deps,
            name=ACTIVITY,
            key=input.idempotency_key,
            output_type=LocalizePhotoOutput,
            work=work,
        )


def model_object(instance: Instance) -> NewObject:
    """A localizer instance as a ``photo_objects`` row (``source="model"``)."""
    box = instance.box
    return NewObject(
        label=instance.label.value,
        phrase=instance.phrase,
        score=instance.score,
        x1=box.x1,
        y1=box.y1,
        x2=box.x2,
        y2=box.y2,
        area=instance.area,
        mask=instance.mask.model_dump(mode="json") if instance.mask is not None else None,
        source="model",
        counted=instance.score >= count_threshold(instance.label),
    )


async def _component_labels(
    session: AsyncSession, study_id: uuid.UUID, document_id: uuid.UUID, revision: int
) -> list[str]:
    stmt = (
        select(Component.label)
        .where(
            Component.study_id == study_id,
            Component.document_id == document_id,
            Component.revision == revision,
        )
        .order_by(Component.label)
    )
    return list((await session.scalars(stmt)).all())


async def _output(
    repo: PhotoObjectRepository, study_id: uuid.UUID, row: PhotoLocalization
) -> LocalizePhotoOutput:
    """The stored outcome (an earlier execution, or carried over), with cost 0."""
    active = await repo.active(study_id, row.revision, [row.document_id])
    return LocalizePhotoOutput(
        document_id=str(row.document_id),
        status=row.status,  # type: ignore[arg-type]
        instances=row.instances,
        counted=sum(1 for r in active if r.counted),
        reason=row.reason,
    )
