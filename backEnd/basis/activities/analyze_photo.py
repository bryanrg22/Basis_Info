"""``analyze_photo``: one photo -> its room and components (S3 body).

Downloads the photo, prepares it (resize, EXIF strip, re-encode), makes one structured
vision call through the provider pool (:func:`basis.vision.analyze.analyze_photo_detailed`)
charged to the remaining budget the workflow passes in, stores the raw analysis next to
the photo in storage, and writes the room (shared by every photo with the same identity
in the revision), the ``photo_rooms`` assignment and one ``components`` row per component
kind (:mod:`basis.vision.rooms`).

Order of work: a read-only check first (an assignment already written for this photo and
revision is returned as is, with cost 0: carried over by a rejection, or an earlier
execution), then the model call outside any transaction, then ``run_once`` writes the
rows and completes the idempotency key in one transaction.

Errors: a missing file or an image that cannot be analyzed (empty, not an image, too
small, too many pixels) -> non-retryable ``ValidationError`` (never an "unknown" room);
no model provider configured -> non-retryable ``ProviderNotConfigured``; the budget cannot
cover the call -> non-retryable ``BudgetExceededError`` (no rows); provider 429/5xx and
timeouts -> retryable. An answer that stays invalid after one retry is an ``ambiguous``
analysis with ``needs_review`` (the workflow flags the photo).

Test hooks (``SimulateFault``) are honoured only when ``BASIS_ENV`` is dev or test:
``fail_attempts`` fails the first N attempts (retryable), ``fail_permanently`` fails with
a non-retryable error, and ``sleep_seconds`` sleeps inside the work transaction, after the
room is written, heartbeating every second (to test cancellation and worker loss).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.exceptions import ApplicationError

from basis.activities._model_calls import (
    PROVIDER_NOT_CONFIGURED,
    heartbeating,
    provider_failure,
    run_pk,
)
from basis.activities._support import (
    budget_exceeded,
    heartbeat,
    parse_uuid,
    run_once,
    validation_error,
)
from basis.activities.deps import Deps
from basis.db.enums import DocumentKind
from basis.db.repositories.document import DocumentRepository
from basis.db.repositories.room import RoomRepository
from basis.db.session import session_scope
from basis.llm import prompts
from basis.llm.call import CallContext
from basis.llm.errors import ProviderError
from basis.llm.provider import Budget, BudgetExceededError
from basis.llm.recorder import LlmCallRecorder
from basis.storage import ObjectNotFound
from basis.vision.analyze import PROMPT_NAME, PhotoResult, VisionContext, analyze_photo_detailed
from basis.vision.prep import ImageRejected
from basis.vision.rooms import assign_room, write_photo
from basis.workflow.contracts import AnalyzePhotoInput, AnalyzePhotoOutput, SimulateFault

ACTIVITY: Final = "analyze_photo"
SIMULATED_FAILURE: Final = "SimulatedFailure"


def check_faults(simulate: SimulateFault | None, attempt: int) -> None:
    """Raise the failure a ``SimulateFault`` asks for on this attempt, if any."""
    if simulate is None:
        return
    if simulate.fail_permanently:
        raise ApplicationError(
            "simulated permanent failure", type=SIMULATED_FAILURE, non_retryable=True
        )
    if attempt <= simulate.fail_attempts:
        raise ApplicationError(f"simulated failure on attempt {attempt}", type=SIMULATED_FAILURE)


async def sleep_heartbeating(seconds: float) -> None:
    remaining = seconds
    while remaining > 0:
        heartbeat("sleeping")
        step = min(1.0, remaining)
        await asyncio.sleep(step)
        remaining -= step


def analysis_uri(bucket: str, study_id: uuid.UUID, document_id: uuid.UUID, revision: int) -> str:
    return f"gs://{bucket}/studies/{study_id}/analyses/{document_id}/r{revision}.json"


class AnalyzePhoto:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="analyze_photo")
    async def run(self, input: AnalyzePhotoInput) -> AnalyzePhotoOutput:
        study_id = parse_uuid(input.study_id, "study_id")
        document_id = parse_uuid(input.document_id, "document_id")
        simulate = input.simulate if self._deps.faults_enabled else None
        heartbeat("start")
        check_faults(simulate, activity.info().attempt)

        async with session_scope(self._deps.sessionmaker) as session:
            document = await DocumentRepository(session).get(study_id, document_id)
            if document is None or document.kind is not DocumentKind.PHOTO:
                raise validation_error(f"photo {document_id} not found in study {study_id}")
            existing = await RoomRepository(session).photo_room(document_id, input.revision)
            run_id = await run_pk(session)
            gcs_uri = document.gcs_uri

        result: PhotoResult | None = None
        cost = 0.0
        uri: str | None = None
        if existing is None:
            result, cost = await self._analyze(input, study_id, document_id, gcs_uri, run_id)
            uri = analysis_uri(self._deps.bucket, study_id, document_id, input.revision)
            await self._deps.storage.put(uri, _raw_json(result, uri), "application/json")

        async def work(session: AsyncSession) -> AnalyzePhotoOutput:
            rooms = RoomRepository(session)
            written = await rooms.photo_room(document_id, input.revision)
            if written is not None:
                count = await rooms.count_components(study_id, input.revision, document_id)
                return AnalyzePhotoOutput(
                    document_id=str(document_id), room_id=str(written.room_id), components=count
                )
            if result is None:  # pragma: no cover - only if the row vanished meanwhile
                raise RuntimeError("photo assignment disappeared between the check and the write")
            analysis = result.analysis
            room_id, _ = await assign_room(
                session, study_id=study_id, revision=input.revision, analysis=analysis
            )
            if simulate is not None and simulate.sleep_seconds > 0:
                await sleep_heartbeating(simulate.sleep_seconds)
            count = await write_photo(
                session,
                study_id=study_id,
                document_id=document_id,
                revision=input.revision,
                room_id=room_id,
                analysis=analysis,
                context={
                    "analysis_uri": uri,
                    "room_type": analysis.room_type.value,
                    "room_confidence": analysis.room_confidence,
                    "floor": analysis.floor,
                    "model": result.model,
                    "provider": result.provider,
                    "prompt_version": prompts.latest(PROMPT_NAME).version_id,
                },
            )
            heartbeat("written")
            return AnalyzePhotoOutput(
                document_id=str(document_id),
                room_id=str(room_id),
                components=count,
                cost_usd=cost,
                needs_review=analysis.needs_review,
            )

        return await run_once(
            self._deps,
            name=ACTIVITY,
            key=input.idempotency_key,
            output_type=AnalyzePhotoOutput,
            work=work,
        )

    async def _analyze(
        self,
        input: AnalyzePhotoInput,
        study_id: uuid.UUID,
        document_id: uuid.UUID,
        gcs_uri: str,
        run_id: uuid.UUID | None,
    ) -> tuple[PhotoResult, float]:
        if self._deps.llm is None:
            raise ApplicationError(
                "no vision model provider is configured (OPENAI_API_KEY or AZURE_OPENAI_*)",
                type=PROVIDER_NOT_CONFIGURED,
                non_retryable=True,
            )
        try:
            image_bytes = await self._deps.storage.read(gcs_uri)
        except ObjectNotFound:
            raise validation_error(f"photo file {gcs_uri} is missing from storage") from None
        settings = self._deps.settings
        budget = Budget(remaining_usd=input.budget_remaining_usd)
        ctx = VisionContext(
            call=CallContext(
                study_id=study_id,
                run_id=run_id,
                activity=ACTIVITY,
                budget=budget,
                recorder=LlmCallRecorder(self._deps.sessionmaker),
            ),
            provider=self._deps.llm,
            prompt=prompts.latest(PROMPT_NAME),
            model=settings.vision_model,
            max_tokens=settings.vision_max_tokens,
            heartbeat=heartbeat,
        )
        try:
            async with heartbeating("analyzing"):
                result = await analyze_photo_detailed(image_bytes, ctx)
        except ImageRejected as exc:
            raise validation_error(f"photo {document_id} cannot be analyzed: {exc}") from exc
        except BudgetExceededError as exc:
            raise budget_exceeded(f"photo {document_id}: {exc}") from exc
        except ProviderError as exc:
            raise provider_failure(exc, f"photo {document_id}") from exc
        return result, round(budget.spent_usd, 6)


def _raw_json(result: PhotoResult, uri: str) -> bytes:
    payload: dict[str, Any] = {
        "uri": uri,
        "analysis": result.analysis.model_dump(mode="json"),
        "attempts": result.attempts,
        "errors": result.errors,
        "provider": result.provider,
        "model": result.model,
        "image": {
            "sha256": result.image.sha256,
            "width": result.image.width,
            "height": result.image.height,
            "original_width": result.image.original_width,
            "original_height": result.image.original_height,
            "original_format": result.image.original_format,
        },
        "responses": result.raw,
    }
    return json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
