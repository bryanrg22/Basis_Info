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

from basis.classify.store_protocol import ChunkStore
from basis.config import Settings
from basis.extraction.azure_di import AzureDiClient
from basis.llm.provider import ChatProvider
from basis.retrieval import Retrieval, default_retrieval
from basis.storage import Storage
from basis.vision.localize import Localizer
from basis.vision.surface_segmenter import SurfaceSegmenter


@dataclass(frozen=True, slots=True)
class Deps:
    sessionmaker: async_sessionmaker[AsyncSession]
    storage: Storage
    # Bucket the exports are written to (gs://<bucket>/studies/<id>/exports/...).
    bucket: str
    settings: Settings
    # Embedder + BM25 registry. None means the process defaults for ``settings``
    # (``EMBEDDING_MODEL``); tests pass a hash embedder so no model is downloaded.
    retrieval: Retrieval | None = None

    def retrieval_deps(self) -> Retrieval:
        return self.retrieval if self.retrieval is not None else default_retrieval(self.settings)

    # Model provider (a ProviderPool in the worker; a scripted or fixture provider in tests)
    # and Azure Document Intelligence. None means not configured: extraction then uses the
    # text layer only and skips the LLM fallback; photo analysis fails non-retryably.
    llm: ChatProvider | None = None
    azure_di: AzureDiClient | None = None

    # The model provider and the retrieval store for classification (process_assets).
    # ``None`` means "not wired": rule hits still classify; everything else is written
    # needs_review (classifier_unavailable) and no rule citation can be resolved. Tests
    # pass ScriptedProvider and InMemoryChunkStore; the worker wires the real ones.
    chat_provider: ChatProvider | None = None
    chunk_store: ChunkStore | None = None

    # SAM 3 object localization (``localize_photo``). ``None`` means not configured
    # (no ``REPLICATE_API_TOKEN``): every photo's localization is recorded ``skipped``.
    # Tests pass a ``FakeLocalizer``.
    localizer: Localizer | None = None

    # Wall, floor and ceiling masks for MATERIALS_SECOND_LOOK=surface_crops
    # (``reconcile_rooms``; basis.vision.surface_segmenter). ``None``: not built (the option
    # is off); with the option on, every room then keeps its per-photo materials, flagged.
    segmenter: SurfaceSegmenter | None = None

    @property
    def faults_enabled(self) -> bool:
        """Test fault hooks (``SimulateFault``) are honoured only in dev and test."""
        return self.settings.env in ("dev", "test")
