"""The OpenAPI contract (S1_SPEC sections 7 and 8, BUILD_PLAN 6.4).

1. ``docs/api/openapi.json`` is exactly what the app generates (fails when stale; fix with
   ``uv run python -m basis.api.openapi --write``).
2. The document states the error contract: every 4xx/5xx is ``application/problem+json``
   with the ``ProblemDetail`` schema, and ``Idempotency-Key`` is required on POST/DELETE.
3. Schemathesis generates requests for every operation of the committed document (valid
   and invalid data) and runs them against the real app with a real PostgreSQL database:
   no 5xx, every status and body conforms to the document, invalid data is rejected and
   valid data is not.

Schemathesis setup, and why:

* The app runs with a bearer token for a seeded user (registered as a Schemathesis auth
  provider, so its auth checks can also probe without it), a study at
  ``appraisal_review`` owned by that user, and rate limits out of the way. Generated
  ``study_id`` values are replaced by that study's id in valid-data cases (``DELETE`` gets
  a second study, since it archives), so the routes run past the 404; the random-id 404
  path is still exercised by invalid-data cases and the other API tests.
* ``Idempotency-Key`` is replaced by a fresh UUID in valid-data cases: the generator
  reuses short values across operations, and a reused key with a different body is
  (correctly) 422, which is not what the case is testing.
* ``cursor`` is dropped from valid-data cases: it is an opaque token the server issues;
  no schema can describe which strings decode, and an undecodable one is (correctly) 422.
  Cursor behavior is tested in ``test_studies.py``.
* The engine uses ``NullPool``: Schemathesis's ASGI client runs the app on its own event
  loop, and pooled asyncpg connections cannot move between loops.

Defects this test found when it was written (each fixed, with a targeted test): ``Allow``
on 405 listed only the first route's methods (``test_errors.py``); a rejection at revision
2**31 - 1 overflowed the INT bind parameter and answered 500 (``test_decisions.py``);
correction field-name rules were published as ``patternProperties``, which admits other
keys (``test_decisions.py``).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import schemathesis
from apitest_support import FakeVerifier, Seed, api_settings, build_api_app, token_for
from dbtest_support import truncate_all
from fastapi import FastAPI
from hypothesis import HealthCheck
from hypothesis import settings as hypothesis_settings
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from workflowtest_support import FakeWorkflowService

from basis.api.app import create_app
from basis.api.errors import PROBLEM_JSON
from basis.api.idempotency import ENFORCED_METHODS, IDEMPOTENCY_HEADER
from basis.api.openapi import OPENAPI_PATH, render
from basis.config import Settings
from basis.db.engine import async_database_url
from basis.db.enums import Stage
from basis.db.repositories import StudyRepository
from basis.db.session import create_sessionmaker
from basis.storage import LocalStorage

COMMITTED = json.loads(Path(OPENAPI_PATH).read_text(encoding="utf-8"))
# Loaded from the running app's /openapi.json, which test_committed_document_is_current
# proves identical to the committed file.
LIVE_SCHEMA = schemathesis.pytest.from_fixture("contract_schema")
PROBLEM_REF = "#/components/schemas/ProblemDetail"
USER = "contract-user"


# --------------------------------------------------------------------------- the file


def test_committed_document_is_current(settings: Settings) -> None:
    generated = render(create_app(settings))
    committed = Path(OPENAPI_PATH).read_text(encoding="utf-8")
    assert committed == generated, (
        "docs/api/openapi.json is stale; run: uv run python -m basis.api.openapi --write"
    )


def operations() -> Iterator[tuple[str, str, dict[str, Any]]]:
    for path, methods in COMMITTED["paths"].items():
        for method, operation in methods.items():
            yield method.upper(), path, operation


@pytest.mark.parametrize(("method", "path", "operation"), list(operations()))
def test_every_error_response_is_a_problem(
    method: str, path: str, operation: dict[str, Any]
) -> None:
    errors = {code: spec for code, spec in operation["responses"].items() if code[0] in "45"}
    if path.startswith("/v1/"):
        assert {"401", "422", "429", "503"} <= set(errors), f"{method} {path}"
    for code, spec in errors.items():
        assert spec["content"] == {PROBLEM_JSON: {"schema": {"$ref": PROBLEM_REF}}}, code
    assert "HTTPValidationError" not in json.dumps(operation)


@pytest.mark.parametrize(("method", "path", "operation"), list(operations()))
def test_idempotency_key_is_required_on_mutations(
    method: str, path: str, operation: dict[str, Any]
) -> None:
    headers = {p["name"]: p for p in operation.get("parameters", []) if p["in"] == "header"}
    if method in ENFORCED_METHODS:
        key = headers[IDEMPOTENCY_HEADER]
        assert key["required"] is True
        assert key["schema"]["maxLength"] == 128
        assert {"400", "409"} <= set(operation["responses"])
    else:
        assert IDEMPOTENCY_HEADER not in headers


def test_problem_schema_has_the_rfc_9457_members() -> None:
    problem = COMMITTED["components"]["schemas"]["ProblemDetail"]
    assert {"type", "title", "status", "detail", "instance", "request_id", "errors"} <= set(
        problem["properties"]
    )
    assert set(problem["required"]) == {"title", "status"}


# --------------------------------------------------------------------------- fuzzing


@pytest.fixture(scope="module")
def contract_app(database_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FastAPI]:
    engine = create_async_engine(async_database_url(database_url), poolclass=NullPool)
    sessionmaker = create_sessionmaker(engine)

    async def prepare() -> dict[str, str]:
        await truncate_all(engine)
        seed = Seed(sessionmaker)
        user = await seed.user(USER)
        org = await seed.personal_org(USER)
        async with sessionmaker() as session, session.begin():
            studies = StudyRepository(session)
            main = await studies.create(org, user.id, "Contract test")
            archivable = await studies.create(org, user.id, "Contract test (DELETE)")
        await seed.stage(main.id, Stage.APPRAISAL_REVIEW)
        return {"main": str(main.id), "archivable": str(archivable.id)}

    study_ids = asyncio.run(prepare())
    app = build_api_app(
        api_settings(database_url),
        sessionmaker,
        LocalStorage(tmp_path_factory.mktemp("contract-objects")),
        FakeVerifier(),
        # S2: an in-memory Temporal, so the workflow routes run instead of answering 503.
        FakeWorkflowService(),
    )
    app.state.contract_study_ids = study_ids
    try:
        yield app
    finally:
        asyncio.run(truncate_all(engine))
        asyncio.run(engine.dispose())


@pytest.fixture(scope="module")
def contract_schema(contract_app: FastAPI) -> Any:
    schema = schemathesis.openapi.from_asgi("/openapi.json", contract_app)

    class BearerToken:
        """Registered as the auth provider (not sent as a fixed header) so Schemathesis
        knows the credentials are valid and its auth checks can probe without them."""

        def get(self, case: Any, ctx: Any) -> str:
            return token_for(USER)

        def set(self, case: Any, data: str, ctx: Any) -> None:
            case.headers = {**(case.headers or {}), "Authorization": f"Bearer {data}"}

    schema.auth()(BearerToken)
    return schema


def prepare_case(case: Any, study_ids: dict[str, str]) -> None:
    if case.meta is None or not case.meta.generation.mode.is_positive:
        return
    if case.path_parameters and "study_id" in case.path_parameters:
        # DELETE archives; it gets its own study so the others keep accepting writes.
        which = "archivable" if case.method.upper() == "DELETE" else "main"
        case.path_parameters["study_id"] = study_ids[which]
    if case.headers and IDEMPOTENCY_HEADER in case.headers:
        case.headers[IDEMPOTENCY_HEADER] = str(uuid.uuid4())
    if case.query and "cursor" in case.query:
        del case.query["cursor"]


@pytest.mark.integration
@pytest.mark.slow
@LIVE_SCHEMA.parametrize()
@hypothesis_settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
def test_api_conforms_to_its_openapi_document(case: Any, contract_app: FastAPI) -> None:
    prepare_case(case, contract_app.state.contract_study_ids)
    case.call_and_validate()
