"""``notify_review_pending``: remind the reviewers that a gate is still waiting.

Stage 2 logs the reminder and enqueues an outbox row of kind ``notification``; the relay
logs it, and S5 wires email/Slack. The idempotency claim makes a retried reminder a no-op,
so one timer firing yields exactly one notification row.
"""

from __future__ import annotations

import logging
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import activity_key, parse_uuid, run_once_no_output
from basis.activities.deps import Deps
from basis.db.repositories.outbox import OutboxRepository
from basis.db.repositories.study_event import StudyEventRepository
from basis.workflow.contracts import NotifyInput

NOTIFICATION: Final = "notification"

logger = logging.getLogger(__name__)


class NotifyReviewPending:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="notify_review_pending")
    async def run(self, input: NotifyInput) -> None:
        study_id = parse_uuid(input.study_id, "study_id")

        async def work(session: AsyncSession) -> None:
            await OutboxRepository(session).enqueue(
                NOTIFICATION,
                {
                    "type": "review_pending",
                    "study_id": str(study_id),
                    "stage": input.stage,
                    "revision": input.revision,
                    "waiting_days": input.waiting_days,
                },
            )
            await StudyEventRepository(session).record(
                study_id,
                "review.reminded",
                actor="system",
                stage=input.stage,
                revision=input.revision,
                detail={"waiting_days": input.waiting_days},
            )
            logger.info(
                "review pending: study %s %s revision %s for %s days",
                study_id,
                input.stage,
                input.revision,
                input.waiting_days,
            )

        await run_once_no_output(
            self._deps, name="notify_review_pending", key=activity_key(), work=work
        )
