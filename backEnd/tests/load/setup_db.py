"""Prepare a load-test database: ``python tests/load/setup_db.py`` (``DATABASE_URL`` set).

1. ``alembic upgrade head``.
2. A published reference corpus, unless one exists: the synthetic chunks the lifecycle
   tests use (``classifytest_support.lifecycle_reference_chunks``: one chunk per
   rules-table citation page), so rule citations verify and the residual classifier's
   searches hit Postgres BM25 as in production, without the IRS PDFs.
3. ``pg_stat_statements`` when the server preloads it (``tooling/load/compose.load.yml``),
   reset so a run's statements start from zero (``--reset-statements`` alone does only that).
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
BACKEND = TESTS.parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from lifecycletest_support import seed_reference_corpus  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from basis.config import load_settings  # noqa: E402
from basis.db.engine import create_engine  # noqa: E402
from basis.db.enums import Corpus  # noqa: E402
from basis.db.models import CorpusVersion  # noqa: E402
from basis.db.session import create_sessionmaker  # noqa: E402


async def seed() -> str:
    engine = create_engine(load_settings())
    try:
        sm = create_sessionmaker(engine)
        async with sm() as session:
            published = await session.scalar(
                select(CorpusVersion.id).where(
                    CorpusVersion.corpus == Corpus.REFERENCE.value,
                    CorpusVersion.published_at.is_not(None),
                )
            )
        if published is not None:
            return f"reference corpus already published ({published})"
        store = await seed_reference_corpus(sm)
        return f"reference corpus seeded ({len(store.chunks)} chunks)"
    finally:
        await engine.dispose()


async def reset_statements() -> str:
    engine = create_engine(load_settings())
    try:
        async with engine.begin() as conn:
            loaded = await conn.scalar(
                text(
                    "select count(*) from pg_settings where name = 'shared_preload_libraries' "
                    "and setting like '%pg_stat_statements%'"
                )
            )
            if not loaded:
                return "pg_stat_statements not preloaded: skipped"
            await conn.execute(text("create extension if not exists pg_stat_statements"))
            await conn.execute(text("select pg_stat_statements_reset()"))
        return "pg_stat_statements reset"
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset-statements", action="store_true", help="only reset statements")
    args = parser.parse_args()
    if not args.reset_statements:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, check=True
        )
        print(asyncio.run(seed()))
    print(asyncio.run(reset_statements()))


if __name__ == "__main__":
    main()
