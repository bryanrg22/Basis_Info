"""Fixtures from ``workflowtest_support``: ``sm`` (committing sessionmaker on a truncated
database), ``temporal_env`` (a local Temporal dev server), ``task_queue``, and
``fake_workflows`` (the dev server's client with a worker running the fake workflow).

``tests/api`` is put on ``sys.path`` so ``apitest_support`` (the API test client and the
fake Firebase verifier) can be imported when this directory runs on its own; pytest adds
it by itself only when it collects ``tests/api``.
"""

import sys
from pathlib import Path

from workflowtest_support import (  # noqa: F401 - imported fixtures are registered here
    fake_workflows,
    sm,
    task_queue,
    temporal_env,
    wf_engine,
)

API_TESTS = str(Path(__file__).resolve().parents[2] / "api")
if API_TESTS not in sys.path:
    sys.path.insert(0, API_TESTS)
