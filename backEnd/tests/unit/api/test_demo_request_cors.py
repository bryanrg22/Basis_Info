"""CORS for the public demo form (``basis.api.cors``, ``install_cors``), no database: a
preflight is answered by the middleware before any route runs.

* In test (and dev) with nothing set, the local static server's origins may preflight
  ``POST /v1/demo-requests`` with ``Content-Type``, and nothing else on the API changes: no
  CORS headers anywhere else, a preflight elsewhere is not answered as one.
* Staging with neither setting: no CORS anywhere, the demo route included.
* Only ``CORS_ALLOWED_ORIGINS``: the app's origins reach the demo route too; an origin in
  both lists gets exactly one ``Access-Control-Allow-Origin`` (the middleware picks one
  policy per path, it does not stack two).
* The site's origins never reach another path, whatever the method.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from basis.api.app import create_app
from basis.config import LOCAL_SITE_ORIGINS, Settings
from basis.storage import LocalStorage

PATH = "/v1/demo-requests"
APP = "https://cost-seg-basis.web.app"
SITE = "https://basis.example"
# The environment is BASIS_ENV (the field's alias); a keyword `env=` would be ignored.
DEPLOYED: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://basis:x@127.0.0.1:1/none",  # pragma: allowlist secret
    "gcs_bucket": "basis-test-bucket",
    "firebase_project_id": "basis-test",
}


def preflight(origin: str, method: str = "POST", headers: str = "content-type") -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": headers,
    }


async def client_for(**settings: Any) -> httpx.AsyncClient:
    # Local storage: the deployed settings would otherwise build a GCS client.
    app = create_app(
        Settings(_env_file=None, temporal_address="", **settings),  # type: ignore[arg-type]
        storage=LocalStorage(Path(tempfile.mkdtemp(prefix="basis-cors-"))),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://basis.test")


@pytest.fixture
async def default_test() -> AsyncIterator[httpx.AsyncClient]:
    async with await client_for(BASIS_ENV="test") as client:
        yield client


async def test_the_local_site_may_preflight_the_form_in_test(
    default_test: httpx.AsyncClient,
) -> None:
    for origin in LOCAL_SITE_ORIGINS:
        response = await default_test.options(PATH, headers=preflight(origin))
        assert response.status_code == 200, response.text
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-methods"] == "POST"
        assert "access-control-allow-credentials" not in response.headers


async def test_nothing_else_on_the_api_changes(default_test: httpx.AsyncClient) -> None:
    for origin in LOCAL_SITE_ORIGINS:
        health = await default_test.get("/health", headers={"Origin": origin})
        assert "access-control-allow-origin" not in health.headers
        other = await default_test.options("/v1/studies", headers=preflight(origin))
        assert "access-control-allow-origin" not in other.headers
        assert other.status_code != 200  # not answered as a preflight
    # The site's origins only POST, with Content-Type only.
    assert (
        await default_test.options(PATH, headers=preflight(LOCAL_SITE_ORIGINS[0], "PUT"))
    ).status_code == 400
    assert (
        await default_test.options(
            PATH, headers=preflight(LOCAL_SITE_ORIGINS[0], headers="authorization")
        )
    ).status_code == 400


async def test_deployed_with_nothing_set_has_no_cors_at_all() -> None:
    async with await client_for(BASIS_ENV="staging", **DEPLOYED) as client:
        response = await client.options(PATH, headers=preflight(LOCAL_SITE_ORIGINS[0]))
        assert "access-control-allow-origin" not in response.headers
        assert response.status_code != 200


async def test_the_apps_origins_reach_the_form_and_one_header_is_sent() -> None:
    async with await client_for(
        BASIS_ENV="staging",
        cors_allowed_origins=(APP,),
        demo_request_allowed_origins=(SITE, APP),
        **DEPLOYED,
    ) as client:
        for origin in (APP, SITE):
            response = await client.options(PATH, headers=preflight(origin))
            assert response.status_code == 200
            assert response.headers.get_list("access-control-allow-origin") == [origin]
        # The site's origin is still the form's only.
        studies = await client.options("/v1/studies", headers=preflight(SITE))
        assert studies.status_code == 400
        assert (await client.options("/v1/studies", headers=preflight(APP))).status_code == 200
        # An actual response to the app's origin elsewhere: one header, as before.
        health = await client.get("/health", headers={"Origin": APP})
        assert health.headers.get_list("access-control-allow-origin") == [APP]


async def test_only_the_apps_origins_set_still_cover_the_form() -> None:
    async with await client_for(
        BASIS_ENV="staging", cors_allowed_origins=(APP,), **DEPLOYED
    ) as client:
        assert (await client.options(PATH, headers=preflight(APP))).status_code == 200
        assert (await client.options(PATH, headers=preflight(SITE))).status_code == 400
