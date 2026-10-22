"""Give the load-test engineers a firm: ``python tooling/load/provision.py --users N``.

Firms are invite-only (a user in no firm gets 403 ``no-firm`` on the study routes), so
before Locust runs, the ``load-eng-<n>`` users it signs in as need a membership. This
script writes it directly to the stack's own database, through the repositories, with
no HTTP route involved: it is an operator step, like ``python -m basis.admin``, and needs
the database credentials.

* Creates the firm named ``--firm`` ("Load test") if no firm has that name (the oldest one
  is used when several do).
* For each uid ``<prefix>-0`` ... ``<prefix>-<N-1>``: the ``users`` row by Firebase uid
  (``UserRepository.upsert_by_firebase_uid``, the same row the API creates on the user's
  first request; email ``<uid>@example.test`` as ``plan.emulator_token`` asserts), then an
  ``engineer`` membership (``grant_membership``: a higher role is kept). Re-running is
  idempotent.

Local stack (``stack.sh up`` runs it with ``LOAD_USERS``, default 50): ``DATABASE_URL`` of
``compose.load.yml``. Deployed stack (``run_remote.sh``): an operator with database access
runs it once against that database (Cloud SQL proxy) for the uids ``mint_tokens.py`` mints:

    cd backEnd && DATABASE_URL=postgresql+asyncpg://... BASIS_ENV=staging \\
        .venv/bin/python ../tooling/load/provision.py --users 25

Run it with the backend's environment (``backEnd/.venv``); prints one JSON line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import LOAD_DISPLAY_NAME, LOAD_FIRM, LOAD_UID_PREFIX, load_email, load_uids  # noqa: E402


async def provision(session: Any, uids: Sequence[str], firm: str) -> dict[str, Any]:
    """The firm (created if missing) and an engineer membership for each uid."""
    from basis.db.enums import OrgRole
    from basis.db.repositories import OrgRepository, UserRepository

    firms, users = OrgRepository(session), UserRepository(session)
    existing = await firms.by_name(firm)
    org = existing[0] if existing else await firms.create(firm)
    await firms.lock(org.id)  # the lock order every writer of a firm uses
    roles: dict[str, int] = {}
    for uid in uids:
        user = await users.upsert_by_firebase_uid(uid, load_email(uid), LOAD_DISPLAY_NAME)
        role = await users.grant_membership(org.id, user.id, OrgRole.ENGINEER)
        roles[role.value] = roles.get(role.value, 0) + 1
    return {
        "firm": firm,
        "org_id": str(org.id),
        "created": not existing,
        "users": len(uids),
        "roles": roles,
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    from basis.config import load_settings
    from basis.db.engine import create_engine
    from basis.db.session import create_sessionmaker, session_scope

    engine = create_engine(load_settings())
    try:
        async with session_scope(create_sessionmaker(engine)) as session:
            return await provision(session, load_uids(args.uid_prefix, args.users), args.firm)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--users", type=int, required=True, help="how many load-test users")
    parser.add_argument("--uid-prefix", default=LOAD_UID_PREFIX, help="as locustfile --uid-prefix")
    parser.add_argument("--firm", default=LOAD_FIRM, help="as locustfile --firm")
    args = parser.parse_args(argv)
    if args.users < 1:
        parser.error("--users must be at least 1")
    print(json.dumps(asyncio.run(_run(args))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
