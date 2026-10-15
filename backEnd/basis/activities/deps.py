"""What every activity needs from the process, bound once when the worker starts.

Choice: class-based activities. Each activity module defines a small class whose
constructor takes :class:`Deps` and whose single ``run`` method is the
``@activity.defn``. The worker builds the instances once (``build_activities``) and
registers their bound methods. This is the pattern the Temporal Python SDK documents for
sharing clients across activities; it keeps the dependencies explicit and typed (no module
globals, no closures capturing mutable state), and a test swaps one activity by
registering any other callable with the same ``@activity.defn(name=...)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.config import Settings
from basis.storage import Storage


@dataclass(frozen=True, slots=True)
class Deps:
    sessionmaker: async_sessionmaker[AsyncSession]
    storage: Storage
    # Bucket the exports are written to (gs://<bucket>/studies/<id>/exports/...).
    bucket: str
    settings: Settings

    @property
    def faults_enabled(self) -> bool:
        """Test fault hooks (``SimulateFault``) are honoured only in dev and test."""
        return self.settings.env in ("dev", "test")
