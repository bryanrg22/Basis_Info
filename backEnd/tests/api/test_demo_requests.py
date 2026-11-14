"""``POST /v1/demo-requests`` (the public site's "Book a demo" form) and the operators'
``GET /v1/ops/demo-requests``, on the real app and database (docs/api/DEMO_REQUESTS.md,
EVAL_REPORT section 95).

* The page's exact body (``web/site/directions/b/entry.js``) is 202 with the request's id and
  nothing else; the row keeps what was typed (trimmed, the email lower-cased), the client
  address, the user agent and the request id; exactly one ``notification`` outbox row of
  ``type: demo_request`` names it, and carries nothing personal; no token is needed, and a
  token sent anyway changes nothing. The log line carries the id and role, never what the
  visitor typed.
* Every malformed body is 422 problem details naming the field, and stores nothing: each
  required member missing or blank, each length bound, an email that is not one, control
  characters where a line is expected, an unknown role, an unknown member, a non-object.
* The spam trap: a filled-in ``website`` is answered exactly like a new request (202, an
  id) and stores nothing, emails nothing, lists nothing.
* The duplicate window: the same email (any case) again within ten minutes is the first
  request's id, one row, one email; after the window it is a new request. Two identical
  requests at once are made a real race (both queued behind the email's advisory lock)
  and store one row and one email.
* The per-address rate limit: ``DEMO_REQUESTS_PER_HOUR`` requests (a trapped one counts),
  then 429 with ``Retry-After``; another address has its own budget.
* CORS: the site's origin may preflight and post here (and read the error responses), the
  app's origin too; an unlisted origin is refused at the preflight and gets no
  ``Access-Control-Allow-Origin``; the site's origin reaches no other route.
* Operators list newest first with a keyset cursor and each request's email outcome; no
  token is 401, a firm owner 403 ``not-operator``; bad parameters 422.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, api_settings, bearer, build_api_app
from dbtest_support import queued_behind_a_lock
from fastapi import FastAPI
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.errors import PROBLEM_JSON
from basis.db.models import DemoRequest, OutboxMessage
from basis.db.repositories.demo_request import _EMAIL_LOCK_SEED, DUPLICATE_WINDOW
from basis.errors.codes import E
from basis.obs.logging import configure_logging
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

PATH = "/v1/demo-requests"
OPS = "/v1/ops/demo-requests"
OPERATOR = "ops-admin"
SITE = "https://basis.example"
APP_ORIGIN = "https://app.basis.example"
EVIL = "https://evil.example"


def form(**overrides: Any) -> dict[str, Any]:
    """The body the landing page sends (b/entry.js), with ``overrides`` replacing members."""
    body: dict[str, Any] = {
        "name": "Dana Whitfield",
        "email": "dana@acme-eng.example",
        "company": "Acme Engineering",
        "role": "engineer",
        "note": "We do about forty studies a year.",
        "page": "https://basis.example/directions/demo.html",
        "website": "",
    }
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not ...}


def settings_for(database_url: str, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "ops_operator_uids": (OPERATOR,),
        "cors_allowed_origins": (APP_ORIGIN,),
        "demo_request_allowed_origins": (SITE,),
        **overrides,
    }
    return api_settings(database_url, **values)


@pytest.fixture
def demo_app(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> FastAPI:
    return build_api_app(settings_for(database_url), sessionmaker, storage, verifier)


def client_for(app: FastAPI, address: str = "203.0.113.7") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(address, 51234))
    return httpx.AsyncClient(transport=transport, base_url=BASE_URL)


@pytest.fixture
async def site(demo_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """A visitor's browser: no token."""
    async with client_for(demo_app) as client:
        yield client


@pytest.fixture
async def ops(demo_app: FastAPI) -> AsyncIterator[Api]:
    async with client_for(demo_app, "198.51.100.9") as client:
        yield Api(client)


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


def lines(stream: io.StringIO, message: str) -> list[dict[str, Any]]:
    out = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    return [e for e in out if e["message"] == message]


async def stored(sm: async_sessionmaker[AsyncSession]) -> list[DemoRequest]:
    async with sm() as session:
        return list((await session.scalars(select(DemoRequest).order_by(DemoRequest.seq))).all())


async def outbox(sm: async_sessionmaker[AsyncSession]) -> list[OutboxMessage]:
    async with sm() as session:
        stmt = select(OutboxMessage).order_by(OutboxMessage.created_at, OutboxMessage.id)
        return list((await session.scalars(stmt)).all())


# --------------------------------------------------------------------------- the request


async def test_the_pages_request_is_stored_and_its_email_queued_once(
    site: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    log_stream: io.StringIO,
) -> None:
    response = await site.post(
        PATH,
        json=form(
            name="  Dana Whitfield ",
            email=" Dana@ACME-eng.example ",
            company=" Acme Engineering ",
            note="  Forty studies a year.\nMostly multifamily.  ",
        ),
        headers={"User-Agent": "Mozilla/5.0 (Test)", "Accept": "application/json"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == {"id"}
    [row] = await stored(sessionmaker)
    assert str(row.id) == body["id"]
    assert (row.name, row.email, row.company, row.role) == (
        "Dana Whitfield",
        "dana@acme-eng.example",
        "Acme Engineering",
        "engineer",
    )
    assert row.note == "Forty studies a year.\nMostly multifamily."
    assert row.page == "https://basis.example/directions/demo.html"
    assert row.remote_addr == "203.0.113.7"
    assert row.user_agent == "Mozilla/5.0 (Test)"
    assert row.request_id == response.headers["x-request-id"]
    assert row.notified_at is None and row.notify_error is None
    [message] = await outbox(sessionmaker)
    assert message.kind == "notification"
    assert message.payload == {"type": "demo_request", "demo_request_id": body["id"]}
    [line] = lines(log_stream, "demo request recorded")
    assert line["error_code"] == E.DEMO_REQUEST_RECORDED
    assert line["demo_request_id"] == body["id"] and line["role"] == "engineer"
    text_logged = log_stream.getvalue().lower()
    for typed in ("whitfield", "dana@acme", "acme engineering", "forty studies"):
        assert typed not in text_logged


async def test_the_optional_members_may_be_absent_or_blank(
    site: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # What the page sends when the note is empty: no `note` member at all.
    assert (await site.post(PATH, json=form(note=...))).status_code == 202
    # No page and no trap either; blank optional text is absent.
    bare = form(email="lee@firm.example", note="   ", page="", website=...)
    assert (await site.post(PATH, json=bare)).status_code == 202
    first, second = await stored(sessionmaker)
    assert first.note is None
    assert (second.note, second.page) == (None, None)


async def test_a_token_is_neither_needed_nor_used(
    site: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # A bearer token, valid or not, is not read: the visitor is nobody's user.
    for headers in (bearer("alice"), {"Authorization": "Bearer expired"}):
        response = await site.post(
            PATH, json=form(email=f"{uuid.uuid4().hex}@x.example"), headers=headers
        )
        assert response.status_code == 202, response.text
    assert len(await stored(sessionmaker)) == 2


LONG = "x" * 201


@pytest.mark.parametrize(
    ("body", "where"),
    [
        (form(name=...), ["body", "name"]),
        (form(name=""), ["body", "name"]),
        (form(name="   "), ["body", "name"]),
        (form(name=LONG), ["body", "name"]),
        (form(name="Dana\nBcc: x@evil.example"), ["body", "name"]),
        (form(name=5), ["body", "name"]),
        (form(email=...), ["body", "email"]),
        (form(email=""), ["body", "email"]),
        (form(email="dana"), ["body", "email"]),
        (form(email="dana@localhost"), ["body", "email"]),
        (form(email="dana smith@acme.example"), ["body", "email"]),
        (form(email="dana@@acme.example"), ["body", "email"]),
        (form(email="a" * 250 + "@x.example"), ["body", "email"]),
        (form(company=...), ["body", "company"]),
        (form(company=" "), ["body", "company"]),
        (form(company=LONG), ["body", "company"]),
        (form(role=...), ["body", "role"]),
        (form(role="ceo"), ["body", "role"]),
        (form(role="Engineer"), ["body", "role"]),
        (form(note="n" * 4001), ["body", "note"]),
        (form(note="bad\x00byte"), ["body", "note"]),
        (form(note="bell\x07"), ["body", "note"]),
        (form(page="p" * 2001), ["body", "page"]),
        (form(page="https://x.example/\nInjected: 1"), ["body", "page"]),
        (form(website="w" * 2001), ["body", "website"]),
        (form(phone="555-0100"), ["body", "phone"]),
        ([form()], ["body"]),
    ],
)
async def test_a_malformed_request_is_422_naming_the_field(
    site: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    body: Any,
    where: list[Any],
) -> None:
    response = await site.post(PATH, json=body)
    assert response.status_code == 422, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    problem = response.json()
    assert problem["code"] == E.API_VALIDATION
    assert where in [e["loc"] for e in problem["errors"]], problem["errors"]
    assert await stored(sessionmaker) == []
    assert await outbox(sessionmaker) == []


async def test_a_body_that_is_not_json_is_422_and_stores_nothing(
    site: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    for content, kind in (
        (b"{not json", "application/json"),
        (b"name=Dana&email=dana@acme.example", "application/x-www-form-urlencoded"),
        (json.dumps(form()).encode(), "text/plain"),
    ):
        response = await site.post(PATH, content=content, headers={"Content-Type": kind})
        assert response.status_code == 422, (kind, response.text)
    # Bytes that are not UTF-8 fail in the parser: 400, documented (found by the contract fuzz).
    response = await site.post(
        PATH, content=b"8m\xff\xfe\xfa", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == E.API_HTTP_ERROR
    assert await stored(sessionmaker) == []


async def test_the_lengths_at_the_bounds_are_accepted(
    site: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    longest_email = "a" * 244 + "@x.example"  # 254 characters, RFC 5321's path limit
    assert len(longest_email) == 254
    body = form(
        name="n" * 200,
        company="c" * 200,
        email=longest_email,
        note="t" * 4000,
        page="https://x.example/" + "p" * 1982,
    )
    assert (await site.post(PATH, json=body)).status_code == 202
    [row] = await stored(sessionmaker)
    assert (len(row.name), len(row.company), len(row.note or ""), len(row.page or "")) == (
        200,
        200,
        4000,
        2000,
    )


# --------------------------------------------------------------------------- abuse


async def test_the_spam_trap_answers_like_a_request_and_stores_nothing(
    site: httpx.AsyncClient,
    ops: Api,
    sessionmaker: async_sessionmaker[AsyncSession],
    log_stream: io.StringIO,
) -> None:
    trapped = await site.post(PATH, json=form(website="https://cheap-seo.example"))
    real = await site.post(PATH, json=form(email="real@acme.example"))
    assert trapped.status_code == real.status_code == 202
    assert set(trapped.json()) == set(real.json()) == {"id"}
    uuid.UUID(trapped.json()["id"])
    [row] = await stored(sessionmaker)
    assert row.email == "real@acme.example"
    assert len(await outbox(sessionmaker)) == 1
    listed = (await ops.get(OPERATOR, OPS)).json()["items"]
    assert [item["id"] for item in listed] == [str(row.id)]
    [line] = lines(log_stream, "demo request trapped")
    assert line["error_code"] == E.DEMO_REQUEST_TRAPPED and line["remote_addr"] == "203.0.113.7"
    assert "cheap-seo" not in log_stream.getvalue()
    # Whitespace only is empty: not a trap.
    assert (
        await site.post(PATH, json=form(email="third@acme.example", website="  "))
    ).status_code == 202
    assert len(await stored(sessionmaker)) == 2


async def test_the_same_email_within_the_window_is_one_request(
    site: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    log_stream: io.StringIO,
) -> None:
    first = await site.post(PATH, json=form())
    again = await site.post(
        PATH, json=form(email="DANA@acme-eng.example", note="Sorry, sent twice.", role="other")
    )
    assert first.status_code == again.status_code == 202
    assert again.json() == first.json()
    [row] = await stored(sessionmaker)
    assert row.note == "We do about forty studies a year." and row.role == "engineer"
    assert len(await outbox(sessionmaker)) == 1
    [line] = lines(log_stream, "demo request duplicate")
    assert line["error_code"] == E.DEMO_REQUEST_DUPLICATE
    assert line["demo_request_id"] == first.json()["id"]
    # Another email is another request.
    other = await site.post(PATH, json=form(email="sam@acme-eng.example"))
    assert other.json() != first.json()

    # Past the window, the same email is a new request with its own email.
    async with sessionmaker() as session, session.begin():
        await session.execute(
            update(DemoRequest)
            .where(DemoRequest.id == row.id)
            .values(
                created_at=func.now() - DUPLICATE_WINDOW - func.make_interval(0, 0, 0, 0, 0, 0, 1)
            )
        )
    later = await site.post(PATH, json=form(note="Writing again."))
    assert later.status_code == 202 and later.json() != first.json()
    assert len(await stored(sessionmaker)) == 3
    assert len(await outbox(sessionmaker)) == 3

    # Just inside the window is still the same request.
    async with sessionmaker() as session, session.begin():
        await session.execute(
            update(DemoRequest)
            .where(DemoRequest.id == uuid.UUID(later.json()["id"]))
            .values(
                created_at=func.now() - DUPLICATE_WINDOW + func.make_interval(0, 0, 0, 0, 0, 0, 5)
            )
        )
    assert (await site.post(PATH, json=form())).json() == later.json()


async def test_two_identical_requests_at_once_store_one(
    site: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Both requests are queued behind the email's advisory lock (held here), so both have
    passed validation and neither has checked for the other when they race. Without the lock
    in ``DemoRequestRepository.record`` neither waits and the helper fails; without the check
    after it, two rows and two emails."""
    lock = text("SELECT pg_advisory_xact_lock(hashtextextended(:email, :seed))").bindparams(
        email="dana@acme-eng.example", seed=_EMAIL_LOCK_SEED
    )
    responses = await queued_behind_a_lock(
        sessionmaker,
        lock,
        lambda: site.post(PATH, json=form()),
        lambda: site.post(PATH, json=form(email="Dana@Acme-Eng.example")),
    )
    assert [r.status_code for r in responses] == [202, 202], [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    assert len(await stored(sessionmaker)) == 1
    assert len(await outbox(sessionmaker)) == 1


async def test_the_rate_limit_is_per_client_address(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
) -> None:
    app = build_api_app(
        settings_for(database_url, demo_requests_per_hour=3), sessionmaker, storage, verifier
    )
    async with client_for(app, "203.0.113.50") as one, client_for(app, "203.0.113.51") as two:
        assert (await one.post(PATH, json=form(email="a@x.example"))).status_code == 202
        assert (await one.post(PATH, json=form(website="spam"))).status_code == 202  # counts
        assert (await one.post(PATH, json=form(email="a@x.example"))).status_code == 202  # dup
        refused = await one.post(PATH, json=form(email="b@x.example"))
        assert refused.status_code == 429, refused.text
        assert refused.headers["content-type"] == PROBLEM_JSON
        assert refused.json()["code"] == E.API_RATE_LIMITED
        assert 1 <= int(refused.headers["retry-after"]) <= 3600
        # A malformed body counts too: the limit runs before the body is read.
        assert (await one.post(PATH, json={})).status_code == 429
        # Another address has its own budget.
        assert (await two.post(PATH, json=form(email="b@x.example"))).status_code == 202
    assert [r.email for r in await stored(sessionmaker)] == ["a@x.example", "b@x.example"]


# --------------------------------------------------------------------------- CORS


def preflight(origin: str, method: str = "POST", headers: str = "content-type") -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": headers,
    }


def listed(value: str) -> set[str]:
    return {part.strip().lower() for part in value.split(",")}


async def test_the_sites_origin_may_post_the_form(
    site: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    allowed = await site.options(PATH, headers=preflight(SITE))
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["access-control-allow-origin"] == SITE
    assert "post" in listed(allowed.headers["access-control-allow-methods"])
    assert "content-type" in listed(allowed.headers["access-control-allow-headers"])
    assert "access-control-allow-credentials" not in allowed.headers
    assert await stored(sessionmaker) == []  # a preflight never reaches the route

    posted = await site.post(PATH, json=form(), headers={"Origin": SITE})
    assert posted.status_code == 202
    assert posted.headers["access-control-allow-origin"] == SITE
    assert {"x-request-id", "retry-after"} <= listed(
        posted.headers["access-control-expose-headers"]
    )
    # The page reads its errors too: a 422 carries the header.
    refused = await site.post(PATH, json=form(role="ceo"), headers={"Origin": SITE})
    assert refused.status_code == 422
    assert refused.headers["access-control-allow-origin"] == SITE


async def test_the_apps_origin_may_post_it_too(site: httpx.AsyncClient) -> None:
    allowed = await site.options(PATH, headers=preflight(APP_ORIGIN))
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == APP_ORIGIN


async def test_an_unlisted_origin_is_refused(
    site: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    refused = await site.options(PATH, headers=preflight(EVIL))
    assert refused.status_code == 400
    assert "access-control-allow-origin" not in refused.headers
    # Only the methods and headers the form needs are allowed.
    assert (await site.options(PATH, headers=preflight(SITE, "DELETE"))).status_code == 400
    assert (
        await site.options(PATH, headers=preflight(SITE, headers="content-type,authorization"))
    ).status_code == 400
    # CORS is the browser's rule, not access control: a request that does get through
    # (a non-browser client sends any Origin) is answered without the header, so a browser
    # on that origin cannot read it; the rate limit and the trap are what stop abuse.
    posted = await site.post(PATH, json=form(), headers={"Origin": EVIL})
    assert posted.status_code == 202
    assert "access-control-allow-origin" not in posted.headers


async def test_the_sites_origin_reaches_no_other_route(site: httpx.AsyncClient) -> None:
    other = await site.options(
        "/v1/studies", headers=preflight(SITE, headers="authorization,content-type")
    )
    assert other.status_code == 400
    assert "access-control-allow-origin" not in other.headers
    assert (
        "access-control-allow-origin"
        not in (await site.get("/health", headers={"Origin": SITE})).headers
    )
    assert (
        "access-control-allow-origin"
        not in (await site.options(OPS, headers=preflight(SITE, "GET", "authorization"))).headers
    )
    # The app's origin keeps the whole API, as before.
    app_route = await site.options(
        "/v1/studies", headers=preflight(APP_ORIGIN, headers="authorization,content-type")
    )
    assert app_route.status_code == 200
    assert app_route.headers["access-control-allow-origin"] == APP_ORIGIN


# --------------------------------------------------------------------------- operators


async def test_operators_list_newest_first_with_a_cursor(site: httpx.AsyncClient, ops: Api) -> None:
    ids = []
    for n in range(3):
        response = await site.post(PATH, json=form(email=f"p{n}@firm.example", role="other"))
        ids.append(response.json()["id"])
    first = await ops.get(OPERATOR, OPS, params={"limit": 2})
    assert first.status_code == 200, first.text
    page = first.json()
    assert [item["id"] for item in page["items"]] == [ids[2], ids[1]]
    item = page["items"][0]
    assert {k: item[k] for k in ("email", "company", "role", "notification")} == {
        "email": "p2@firm.example",
        "company": "Acme Engineering",
        "role": "other",
        "notification": "queued",
    }
    assert item["remote_addr"] == "203.0.113.7" and item["received_at"]
    assert item["notified_at"] is None and item["notify_error"] is None
    rest = (await ops.get(OPERATOR, OPS, params={"limit": 2, "cursor": page["next_cursor"]})).json()
    assert [i["id"] for i in rest["items"]] == [ids[0]]
    assert rest["next_cursor"] is None


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 201}, {"cursor": "abc"}, {"cursor": "9" * 40}]
)
async def test_operators_get_422_for_bad_parameters(ops: Api, params: dict[str, Any]) -> None:
    assert (await ops.get(OPERATOR, OPS, params=params)).status_code == 422


async def test_only_operators_read_the_requests(site: httpx.AsyncClient, ops: Api) -> None:
    await site.post(PATH, json=form())
    assert (await ops.client.get(OPS)).status_code == 401
    forbidden = await ops.get("alice", OPS)  # a firm owner (her own firm), not an operator
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == E.API_NOT_OPERATOR
    assert "acme" not in forbidden.text.lower()
