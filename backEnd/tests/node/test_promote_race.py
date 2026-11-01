"""Two ``promote_conventions`` runs of one firm, for two studies, promoting the same key at
the same moment (EVAL_REPORT 59): both approvals count, nothing is lost, one row.

The activity body (``promote``) runs in two transactions released together by a barrier
(``dbtest_support.race``). The firm's row for the key already exists (a first study
created it), so each run is one more approval of the same answer: ``put`` locks the row
(``FOR UPDATE``) before it reads ``approvals``, the second waits for the first to commit
and reads 2, and the row ends at 3.

What the broken versions taught: removing the lock from ``put`` alone does *not* lose the
update between two ``put``s, because ``put`` first runs ``INSERT ... ON CONFLICT DO
NOTHING`` and PostgreSQL's unique check waits for the in-flight transaction that holds the
conflicting row, so the second run's read already sees the first's commit. It does lose one
against a concurrent ``confirm`` (whose read-modify-write is already in flight when the
insert finds nothing in flux; ``tests/integration/db/test_classification_cache.py``). This
test, whose two runs are both ``put``s, was therefore proved against a ``put`` with neither
the lock nor the insert on the existing-row path (a read-then-write, the shape before the
lock existed): both read 1, both write 2.
"""

from __future__ import annotations

import pytest
from dbtest_support import race
from lifecycletest_support import SeededStudy
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.activities.process_assets import ProcessAssets
from basis.activities.promote_conventions import PromoteConventions, promote
from basis.classify.store_protocol import InMemoryChunkStore
from basis.config import Settings
from basis.storage import LocalStorage
from basis.workflow.contracts import PromoteConventionsOutput

from .conftest import Env, node_deps
from .test_process_assets import (
    GARAGE_KEY,
    add_kept_room,
    approve_assets,
    conventions,
    events,
    garage_turns,
    process,
    promote_input,
    study_at_firm,
)

pytestmark = pytest.mark.integration


async def test_two_promotions_of_one_key_at_once_both_count(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    db_test_settings: Settings,
    reference_store: InMemoryChunkStore,
    study: SeededStudy,
    activity_env: Env,
) -> None:
    sm = sessionmaker
    second = await study_at_firm(sm, study)
    third = await study_at_firm(sm, study)
    # Three studies of the firm, each with the model's cited garage-door answer.
    for seeded, key in ((study, "k-a"), (second, "k-b"), (third, "k-c")):
        await add_kept_room(sm, seeded.study_id, "Garage", "garage", "garage_door")
        deps = node_deps(sm, storage, db_test_settings, reference_store, garage_turns())
        await activity_env.run(ProcessAssets(deps).run, process(seeded, key=key))
        await approve_assets(sm, seeded)
    promoter = PromoteConventions(node_deps(sm, storage, db_test_settings, reference_store, []))
    first = await activity_env.run(promoter.run, promote_input(study, 1, "k-promote-a"))
    assert first == PromoteConventionsOutput(created=1)

    outcomes = await race(
        sm,
        lambda session: promote(session, db_test_settings, second.study_id, 1),
        lambda session: promote(session, db_test_settings, third.study_id, 1),
    )

    assert outcomes == [PromoteConventionsOutput(confirmed=1)] * 2, outcomes
    rows = await conventions(sm, study.org_id)
    assert list(rows) == [GARAGE_KEY]
    row = rows[GARAGE_KEY]
    # Both approvals counted; the row keeps the ruling that seeded it.
    assert (row.approvals, row.study_id, row.result["bucket"]) == (3, study.study_id, "y27_5")
    recorded = []
    for seeded in (second, third):
        [event] = await events(sm, seeded.study_id, "convention.promoted")
        recorded.append(event.detail["approvals"])
    assert sorted(recorded) == [2, 3]
