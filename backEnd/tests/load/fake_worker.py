"""Load-test worker: ``python tests/load/fake_worker.py [--providers fake|real]``.

The production entrypoint (:mod:`basis.worker.main`) with one difference in fake mode
(the default), the composition root, as in ``tests/failure/kill_worker.py``: the model
providers, Azure DI and SAM 3 are :mod:`load_fakes`, which answer like the fixtures and
sleep like the live study (docs/perf/LOAD_TESTING.md section 3). The fake provider sits
behind the real :class:`~basis.llm.pool.ProviderPool`, so ``LLM_CONCURRENCY`` bounds it
exactly as it bounds the real providers. Everything else is production: settings from the
environment, the Temporal client and worker, the activities, the Postgres chunk store, the
health server, the Prometheus metrics on ``--metrics-port``, signal handling and the drain.

``--providers real`` runs the production composition root (:func:`basis.worker.main.build_deps`)
unchanged: real providers, real spend. Use it only for the capped probe (LOAD_TESTING.md
section 6).

Environment: ``LOAD_LATENCY_SCALE`` (default 1.0) multiplies every fake latency;
``LOAD_LATENCY_PROFILE`` names another profile JSON (default: the 27.8 live study);
``LOAD_PROVIDER_LIMITS`` (a ``tooling/load/provider_limits.py`` JSON) emulates our accounts'
rate limits (``rate_limits.py``).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
for extra in (TESTS, HERE):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from load_fakes import (  # noqa: E402
    PROFILE,
    LatencyAzureDi,
    LatencyLocalizer,
    LatencyModel,
    LoadProvider,
)
from rate_limits import RateLimits  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine  # noqa: E402

from basis.activities import Deps  # noqa: E402
from basis.api.app import default_storage  # noqa: E402
from basis.config import Settings  # noqa: E402
from basis.db.session import create_sessionmaker  # noqa: E402
from basis.llm.pool import ProviderPool  # noqa: E402
from basis.retrieval import Retrieval  # noqa: E402
from basis.retrieval.bm25 import Bm25Registry  # noqa: E402
from basis.retrieval.embed import HashEmbedder  # noqa: E402
from basis.retrieval.store import PostgresChunkStore  # noqa: E402
from basis.worker.main import build_deps, main  # noqa: E402

logger = logging.getLogger("basis.load.worker")


def latency_model() -> LatencyModel:
    scale = float(os.environ.get("LOAD_LATENCY_SCALE", "1.0"))
    profile = Path(os.environ.get("LOAD_LATENCY_PROFILE", str(PROFILE)))
    return LatencyModel.from_file(profile, scale=scale)


def load_deps(settings: Settings, engine: AsyncEngine) -> Deps:
    """Production composition with the providers replaced by the latency fakes."""
    latency = latency_model()
    sessionmaker = create_sessionmaker(engine)
    storage, bucket = default_storage(settings)
    registry = Bm25Registry()
    limits_file = os.environ.get("LOAD_PROVIDER_LIMITS")
    limits = (
        RateLimits.from_file(Path(limits_file), scale=latency.scale or 1.0) if limits_file else None
    )
    pool = ProviderPool(
        [LoadProvider(latency, limits=limits)], concurrency=settings.llm_concurrency
    )
    logger.warning(
        "LOAD-TEST WORKER: fake providers (profile %s, latency scale %s, provider limits %s, "
        "llm_concurrency %d, localizer_concurrency %d)",
        latency.profile.get("source"),
        latency.scale,
        limits_file or "off",
        settings.llm_concurrency,
        settings.localizer_concurrency,
    )
    return Deps(
        sessionmaker=sessionmaker,
        storage=storage,
        bucket=bucket,
        settings=settings,
        retrieval=Retrieval(embedder=HashEmbedder(), registry=registry),
        llm=pool,
        azure_di=LatencyAzureDi(latency),
        chat_provider=pool,
        chunk_store=PostgresChunkStore(sessionmaker, registry=registry),
        localizer=LatencyLocalizer(latency, concurrency=settings.localizer_concurrency),
    )


def parse(argv: list[str]) -> tuple[str, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--providers", choices=("fake", "real"), default="fake")
    known, rest = parser.parse_known_args(argv)
    return known.providers, rest


if __name__ == "__main__":
    providers, rest = parse(sys.argv[1:])
    main(rest, deps_factory=load_deps if providers == "fake" else build_deps)
