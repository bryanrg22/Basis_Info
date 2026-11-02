"""Creating and renaming a firm with the root logger at DEBUG, as a deployed API logs.

The suite's root logger sits at WARNING, so an ``logger.info(..., extra=...)`` record is
never built there and a reserved ``extra`` key (``name``) never raised. Deployed stacks run
``LOG_LEVEL=INFO``, where ``POST /v1/orgs`` answered 500 with ``KeyError: "Attempt to
overwrite 'name' in LogRecord"`` (EVAL_REPORT 66). Here the Basis handler is installed at
DEBUG, so every log call on these routes builds and formats its record.

* ``POST /v1/orgs`` answers 201 and logs ``firm created`` with the firm's name.
* ``PATCH /v1/orgs/{id}`` answers 200 and logs ``firm renamed`` with the new name.
* The record's ``logger`` field is still the module's logger, not the firm name.

``tests/unit/obs/test_log_extra_keys.py`` checks every ``extra=`` in ``basis/`` without a
database; this file is the end-to-end proof on the route that failed.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from apitest_support import Api, Seed, bearer

from basis.db.enums import OrgRole
from basis.obs.logging import configure_logging

pytestmark = pytest.mark.integration


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    root = logging.getLogger()
    previous = root.level
    stream = io.StringIO()
    handler = configure_logging("DEBUG", stream=stream)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        handler.close()
        root.setLevel(previous)


def entries(stream: io.StringIO, message: str) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    return [e for e in lines if e["message"] == message]


async def test_creating_a_firm_at_debug_answers_201_and_logs_the_name(
    invite_only_api: Api, log_stream: io.StringIO
) -> None:
    assert logging.getLogger("basis.api.routes.orgs").isEnabledFor(logging.INFO)

    created = await invite_only_api.post("nina", "/v1/orgs", {"name": "Harbor Cost Seg"})

    assert created.status_code == 201, created.text
    firm = created.json()
    (line,) = entries(log_stream, "firm created")
    assert line["severity"] == "INFO"
    assert line["logger"] == "basis.api.routes.orgs"
    assert (line["org_id"], line["firm_name"]) == (firm["id"], "Harbor Cost Seg")
    assert "name" not in line


async def test_renaming_a_firm_at_debug_answers_200_and_logs_the_new_name(
    invite_only_api: Api, seed: Seed, log_stream: io.StringIO
) -> None:
    # Seeded rather than created through POST, so this fails on the rename alone.
    org = await seed.org("Harbor")
    await seed.member(org, "olivia", OrgRole.OWNER, own_firm=False)

    renamed = await invite_only_api.client.patch(
        f"/v1/orgs/{org}", json={"name": "Harbor Cost Seg"}, headers=bearer("olivia")
    )

    assert renamed.status_code == 200, renamed.text
    (line,) = entries(log_stream, "firm renamed")
    assert line["logger"] == "basis.api.routes.orgs"
    assert (line["org_id"], line["firm_name"]) == (str(org), "Harbor Cost Seg")
