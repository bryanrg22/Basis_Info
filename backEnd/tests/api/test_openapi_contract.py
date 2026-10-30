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
* Generated ``org_id`` values are replaced by the seeded user's firm (the user is its
  owner) in valid-data cases, so the firm administration routes run past the 404.
* ``Idempotency-Key`` is replaced by a fresh UUID in valid-data cases: the generator
  reuses short values across operations, and a reused key with a different body is
  (correctly) 422, which is not what the case is testing.
* ``cursor`` is dropped from valid-data cases: it is an opaque token the server issues;
  no schema can describe which strings decode, and an undecodable one is (correctly) 422.
  Cursor behavior is tested in ``test_studies.py``.
* ``corrections`` is emptied in valid-data cases (S2-C): a correction must name an
  existing row of the decided revision, of a type the decided stage accepts, which the
  schema cannot describe (existence is state; the type depends on ``stage``), so a
  generated correction is (correctly) 422. Correction targets are tested in
  ``test_decisions.py``.
* ``room_aliases`` is emptied in valid-data cases (v0.9): an alias map must not loop
  (``{"a": "b", "b": "a"}`` or ``{"a": "a"}``), a relation between keys and values that
  JSON Schema cannot express, so a generated map can be schema-valid and (correctly) 422.
  Loops, self-aliases and the accepted forms are tested in ``test_studies.py``.
* The ``main`` study is seeded with a complete intake (EVAL_REPORT 37: ``start`` refuses a
  study without one with 422 ``intake-required``, which Schemathesis's positive-data check
  does not accept for a well-formed request; the 422 is the route tests'), so a positive
  ``start`` answers 409 ``appraisal-required`` as before.
* ``intake`` is dropped from ``POST /studies`` bodies and the ``PUT .../intake`` body is
  replaced by one valid intake in valid-data cases: the intake's rules relate fields (a land
  method to its input, the use to the 80 % test), which JSON Schema cannot express, so a
  generated body is (correctly) 422. The rules are tested in ``test_studies.py`` and
  ``tests/unit/domain/test_intake.py``.
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
from typing import Any, Final

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
from basis.api.openapi import IDEMPOTENCY_KEY_PATTERN, OPENAPI_PATH, render
from basis.config import Settings
from basis.db.engine import async_database_url
from basis.db.enums import Stage
from basis.db.repositories import StudyRepository
from basis.db.session import create_sessionmaker
from basis.domain.intake import StudyIntakeIn, with_location
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
        assert key["schema"]["maxLength"] == 130  # 128 characters, or 128 in double quotes
        assert key["schema"]["pattern"] == IDEMPOTENCY_KEY_PATTERN
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
        intake = with_location(
            StudyIntakeIn.model_validate(VALID_INTAKE), appraisal_state=None, appraisal_zip=None
        )
        async with sessionmaker() as session, session.begin():
            studies = StudyRepository(session)
            main = await studies.create(org, user.id, "Contract test", intake=intake)
            archivable = await studies.create(org, user.id, "Contract test (DELETE)")
        await seed.stage(main.id, Stage.APPRAISAL_REVIEW)
        return {"main": str(main.id), "archivable": str(archivable.id), "org": str(org)}

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


VALID_COST_INDEX: Final[dict[str, Any]] = {
    "series": "historical",
    "year": 2024,
    "region": "US",
    "value": "294.8",
    "as_of": "2024-07-01",
    "citation": "RSMeans data from Gordian, 2024 cost index page (July 2024 = 294.8)",
}

VALID_INTAKE: Final[dict[str, Any]] = {
    "purchase_price": "650000.00",
    "acquisition_costs": [{"category": "recording_fees", "amount": "180.00"}],
    "land_method": "appraisal_site_value",
    "appraisal_site_value": "90000.00",
    "property_use": "nonresidential",
    "transient_lodging": True,
    "placed_in_service_date": "2025-05-01",
    "state": "NY",
}


def prepare_case(case: Any, study_ids: dict[str, str]) -> None:
    if case.meta is None or not case.meta.generation.mode.is_positive:
        return
    if case.path_parameters and "study_id" in case.path_parameters:
        # DELETE archives; it gets its own study so the others keep accepting writes.
        which = "archivable" if case.method.upper() == "DELETE" else "main"
        case.path_parameters["study_id"] = study_ids[which]
    if case.path_parameters and "org_id" in case.path_parameters:
        case.path_parameters["org_id"] = study_ids["org"]
    if case.headers and IDEMPOTENCY_HEADER in case.headers:
        case.headers[IDEMPOTENCY_HEADER] = str(uuid.uuid4())
    if case.query and "cursor" in case.query:
        del case.query["cursor"]
    if isinstance(case.body, dict) and case.body.get("corrections"):
        case.body["corrections"] = []
    if isinstance(case.body, dict) and case.body.get("room_aliases"):
        case.body["room_aliases"] = {}
    if isinstance(case.body, dict) and "intake" in case.body:
        case.body["intake"] = None
    if case.path.endswith("/intake") and case.method.upper() == "PUT":
        case.body = dict(VALID_INTAKE)
    if case.path.endswith("/cost-indices") and case.method.upper() == "POST":
        # Cross-field rules (a historical value is national, as_of in the year, a city row
        # names its city, the value above zero) are not in the schema; a valid body, like
        # the intake's.
        case.body = dict(VALID_COST_INDEX)


@pytest.mark.integration
@pytest.mark.slow
# One test for every operation (40 examples each): its run time grows with the API. With
# the v0.9 routes (retry, fields, rooms, assets) it no longer fits the suite's 120 s.
@pytest.mark.timeout(300)
@LIVE_SCHEMA.parametrize()
@hypothesis_settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
def test_api_conforms_to_its_openapi_document(case: Any, contract_app: FastAPI) -> None:
    prepare_case(case, contract_app.state.contract_study_ids)
    case.call_and_validate()
