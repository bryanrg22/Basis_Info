"""``ClassificationCacheRepository`` on Postgres: a convention serves while the rule it was
made under is the running one, whatever the table's release (CLASSIFICATION_CONVENTIONS.md
section 10.3). Live run 4 lost every firm ruling to a release that only added rows."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from dbtest_support import Factory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from basis.classify.cache import ClassificationCacheRepository, cache_key, is_current
from basis.db.models import ClassificationCacheEntry
from basis.domain.rules import RULES_VERSION, rule_fingerprint

pytestmark = pytest.mark.integration

PC = "nonresidential"
WHEN = datetime(2026, 10, 30, tzinfo=UTC)


def ruling(bucket: str) -> dict[str, object]:
    return {"bucket": bucket, "category": "c", "section": None, "method": "manual"}


async def test_the_reader_serves_rows_at_the_running_fingerprint_only(
    session: AsyncSession, make: Factory
) -> None:
    user = await make.user()
    study = await make.study(user)
    repo = ClassificationCacheRepository(session)
    current = cache_key("baseboard", "wood", "permanent", PC, "bathroom")
    stale = cache_key("baseboard", "wood", "permanent", PC, "bedroom", fingerprint="r1-0" * 4)
    versioned = cache_key("baseboard", "wood", None, PC, "hallway", fingerprint=RULES_VERSION)
    no_row = cache_key("fireplace", "wood", None, PC, "living_room")
    for key in (current, stale, versioned, no_row):
        await repo.put(
            study.org_id,
            key,
            ruling("y5"),
            source="approved_asset:x",
            study_id=study.id,
            reviewer_id=user.id,
            approved_at=WHEN,
            rule_key=None,
            agrees_with_rules=None,
        )
    served = {c.key for c in await repo.get_for_labels(study.org_id, {"Baseboard", "fireplace"})}
    # A row made under another version of its rule, or keyed by a release name (a row 0019
    # could not re-key), is stored but not served; a label without a row has "r1-none".
    assert served == {current, no_row}
    assert no_row.endswith("|r1-none") and is_current(no_row)
    assert not is_current(stale) and not is_current(versioned) and not is_current("a|b")
    stored = await session.scalars(select(ClassificationCacheEntry.key))
    assert set(stored.all()) == {current, stale, versioned, no_row}


async def test_a_ruling_keeps_its_key_and_approvals_across_a_release(
    session: AsyncSession, make: Factory
) -> None:
    user = await make.user()
    study = await make.study(user)
    repo = ClassificationCacheRepository(session)
    key = cache_key("hardwood_floor", "wood", None, PC, "bedroom")
    assert key.endswith(rule_fingerprint("hardwood_floor"))

    async def approve(release: str) -> object:
        return await repo.put(
            study.org_id,
            key,
            ruling("y5"),
            source="approved_asset:x",
            study_id=study.id,
            reviewer_id=user.id,
            approved_at=WHEN,
            rule_key="hardwood_floor",
            agrees_with_rules=False,
            rules_version=release,
        )

    first = await approve("2026-10-31.1")
    # The next release does not touch the hardwood row: the same key, one more approval.
    second = await approve("2026-11-15.1")
    assert (first.outcome, second.outcome, second.approvals) == ("created", "confirmed", 2)  # type: ignore[attr-defined]
    [row] = (await session.scalars(select(ClassificationCacheEntry))).all()
    assert (row.key, row.approvals, row.rules_version) == (key, 2, "2026-10-31.1")
    [served] = await repo.get_for_labels(study.org_id, {"hardwood_floor"})
    assert served.approvals == 2
