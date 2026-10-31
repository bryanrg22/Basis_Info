"""The dev upload route (``basis.api.local_uploads``): a browser's upload on a local stack.

* With ``LOCAL_UPLOAD_BASE_URL``, local storage signs http URLs on the API; a ``PUT`` of
  the exact bytes with the signed headers stores the object (200, empty body, no bearer
  token), as GCS does for a V4 signed URL.
* Whatever GCS refuses is refused with 403: a different content type, a missing or
  different content-length range, a body of another size, an unknown or expired token.
  A body over the 50 MiB upload limit is 413 before it is read.
* The route is mounted only for local storage in dev and test, is not in the OpenAPI
  document, and CORS allows the browser's range header only then (``PUT`` itself is allowed
  everywhere, for the intake).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from basis.api.app import create_app, local_uploads_enabled
from basis.api.schemas.documents import MAX_UPLOAD_BYTES
from basis.config import Settings
from basis.storage import LocalStorage, ObjectNotFound
from basis.storage.base import CONTENT_LENGTH_RANGE_HEADER

BASE = "http://basis.test"
FRONTEND = "http://127.0.0.1:3100"
URI = "gs://basis-local/studies/s/d/photo.jpg"
DATA = b"\xff\xd8\xff" + b"jpeg bytes " * 100


def build(**overrides: object) -> Settings:
    return Settings(_env_file=None, env="test", temporal_address="", **overrides)  # type: ignore[arg-type]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def storage(tmp_path: Path, clock: Clock) -> LocalStorage:
    return LocalStorage(tmp_path / "objects", clock=clock, upload_url_base=BASE)


@pytest.fixture
async def client(storage: LocalStorage) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(build(cors_allowed_origins=(FRONTEND,)), storage=storage)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


async def sign(storage: LocalStorage, size: int = len(DATA)) -> tuple[str, dict[str, str]]:
    signed = await storage.signed_put_url(URI, "image/jpeg", size, timedelta(minutes=15))
    return signed.url, signed.headers


async def test_signed_url_is_an_http_url_on_the_api(storage: LocalStorage) -> None:
    url, headers = await sign(storage)
    assert url.startswith(f"{BASE}/dev/local-uploads/")
    assert headers == {
        "Content-Type": "image/jpeg",
        CONTENT_LENGTH_RANGE_HEADER: f"{len(DATA)},{len(DATA)}",
    }


async def test_put_with_the_signed_headers_stores_the_object(
    client: httpx.AsyncClient, storage: LocalStorage
) -> None:
    url, headers = await sign(storage)
    response = await client.put(url, content=DATA, headers=headers)
    assert response.status_code == 200, response.text
    assert response.content == b""
    assert await storage.read(URI) == DATA
    assert (await storage.stat(URI)).content_type == "image/jpeg"


async def test_the_put_url_can_still_be_used_through_put_signed(storage: LocalStorage) -> None:
    url, headers = await sign(storage)
    assert storage.put_signed(url, DATA, headers) == URI


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"Content-Type": "image/png"}, "Content-Type"),
        ({CONTENT_LENGTH_RANGE_HEADER: "1,1"}, "content-length range"),
        ({CONTENT_LENGTH_RANGE_HEADER: None}, "content-length range"),
    ],
)
async def test_headers_that_differ_from_the_signature_are_refused(
    client: httpx.AsyncClient, storage: LocalStorage, change: dict[str, str | None], reason: str
) -> None:
    url, headers = await sign(storage)
    sent = {k: v for k, v in {**headers, **change}.items() if v is not None}
    response = await client.put(url, content=DATA, headers=sent)
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert reason in response.json()["detail"]
    with pytest.raises(ObjectNotFound):
        await storage.read(URI)


async def test_a_body_of_another_size_is_refused(
    client: httpx.AsyncClient, storage: LocalStorage
) -> None:
    url, headers = await sign(storage)
    response = await client.put(url, content=DATA + b"x", headers=headers)
    assert response.status_code == 403
    assert "body size" in response.json()["detail"]


async def test_unknown_and_expired_tokens_are_refused(
    client: httpx.AsyncClient, storage: LocalStorage, clock: Clock
) -> None:
    url, headers = await sign(storage)
    unknown = await client.put(f"{BASE}/dev/local-uploads/forged", content=DATA, headers=headers)
    assert unknown.status_code == 403 and "unknown" in unknown.json()["detail"]
    clock.now += timedelta(minutes=16)
    expired = await client.put(url, content=DATA, headers=headers)
    assert expired.status_code == 403 and "expired" in expired.json()["detail"]


async def test_a_token_with_unsafe_characters_is_a_validation_error(
    client: httpx.AsyncClient,
) -> None:
    response = await client.put(f"{BASE}/dev/local-uploads/a.b", content=DATA)
    assert response.status_code == 422


async def test_a_body_over_the_upload_limit_is_refused_before_reading(
    client: httpx.AsyncClient, storage: LocalStorage
) -> None:
    url, headers = await sign(storage)
    response = await client.put(
        url,
        content=DATA,
        headers={**headers, "Content-Length": str(MAX_UPLOAD_BYTES + 1)},
    )
    assert response.status_code == 413


async def test_the_route_needs_no_bearer_token_and_is_not_in_openapi(
    client: httpx.AsyncClient, storage: LocalStorage
) -> None:
    url, headers = await sign(storage)
    assert (await client.put(url, content=DATA, headers=headers)).status_code == 200
    spec = (await client.get("/openapi.json")).json()
    assert not [path for path in spec["paths"] if "local-uploads" in path]


async def test_cors_allows_the_browsers_put_for_local_uploads(client: httpx.AsyncClient) -> None:
    response = await client.options(
        "/dev/local-uploads/token",
        headers={
            "Origin": FRONTEND,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": f"content-type,{CONTENT_LENGTH_RANGE_HEADER}",
        },
    )
    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]
    assert CONTENT_LENGTH_RANGE_HEADER in response.headers["access-control-allow-headers"].lower()


async def test_without_local_storage_there_is_no_route_and_no_upload_header_in_cors(
    tmp_path: Path,
) -> None:
    """PUT itself is an allowed method everywhere since the intake's conditional write
    (``PUT /v1/studies/{id}/intake`` with ``If-Match``); the upload route stays unmounted and
    its signed range header is not allowed, so a browser cannot upload through the API."""

    class OtherStorage:  # stands in for GCS: anything that is not LocalStorage
        pass

    app = create_app(build(cors_allowed_origins=(FRONTEND,)), storage=OtherStorage())  # type: ignore[arg-type]
    assert not local_uploads_enabled(app, build())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        put = await c.put(f"{BASE}/dev/local-uploads/token", content=DATA)
        preflight = await c.options(
            "/v1/studies",
            headers={
                "Origin": FRONTEND,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": CONTENT_LENGTH_RANGE_HEADER,
            },
        )
    assert put.status_code in (404, 405)
    assert preflight.status_code == 400  # the upload's range header is not allowed there


def test_local_uploads_are_off_outside_dev_and_test(tmp_path: Path) -> None:
    app = create_app(build(), storage=LocalStorage(tmp_path))
    staging = Settings.model_construct(env="staging")
    prod = Settings.model_construct(env="prod")
    assert local_uploads_enabled(app, build())
    assert not local_uploads_enabled(app, staging)
    assert not local_uploads_enabled(app, prod)
