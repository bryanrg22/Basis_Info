"""Worker process for the worker-kill tests: ``python tests/failure/kill_worker.py``.

The production entrypoint (:mod:`basis.worker.main`) with one difference, the
composition root: the model providers are the offline fixtures and the embedder is the
hash embedder, so the process needs no credentials and downloads nothing. Everything
else, settings from the environment, the Temporal client, the Postgres chunk store, the
health server, signal handling and the drain, is the production code path.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from lifecycletest_support import scripted_classifier  # noqa: E402
from llm_fakes import FixtureAzureDi, FixtureVisionProvider  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine  # noqa: E402

from basis.activities import Deps  # noqa: E402
from basis.api.app import default_storage  # noqa: E402
from basis.config import Settings  # noqa: E402
from basis.db.session import create_sessionmaker  # noqa: E402
from basis.retrieval import Retrieval  # noqa: E402
from basis.retrieval.bm25 import Bm25Registry  # noqa: E402
from basis.retrieval.embed import HashEmbedder  # noqa: E402
from basis.retrieval.store import PostgresChunkStore  # noqa: E402
from basis.worker.main import main  # noqa: E402


def fixture_deps(settings: Settings, engine: AsyncEngine) -> Deps:
    sessionmaker = create_sessionmaker(engine)
    storage, bucket = default_storage(settings)
    registry = Bm25Registry()
    return Deps(
        sessionmaker=sessionmaker,
        storage=storage,
        bucket=bucket,
        settings=settings,
        retrieval=Retrieval(embedder=HashEmbedder(), registry=registry),
        llm=FixtureVisionProvider(),
        azure_di=FixtureAzureDi(),
        chat_provider=scripted_classifier(),
        chunk_store=PostgresChunkStore(sessionmaker, registry=registry),
    )


if __name__ == "__main__":
    main(deps_factory=fixture_deps)
