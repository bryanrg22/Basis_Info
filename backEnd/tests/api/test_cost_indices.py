"""``/v1/cost-indices`` on the real routes: a firm owner enters a value with its citation
(201, the row and its ledger row with who and when), an engineer or a user in no firm cannot
(403), the body is validated (422), the same value twice is 409 and nothing is written, a
revision is the next version and the old row stays, the listing and the ledger read back."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from apitest_support import Api, Seed, new_key
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.errors import PROBLEM_JSON
from basis.api.idempotency import REPLAYED_HEADER
from basis.db.enums import OrgRole
from basis.db.models import CostIndexRun, CostIndexValue
from basis.db.session import session_scope
from basis.errors.codes import E

pytestmark = pytest.mark.integration

VALUE_2024: dict[str, Any] = {
    "series": "historical",
    "year": 2024,
    "value": "294.8",
    "as_of": "2024-07-01",
    "citation": "RSMeans data from Gordian, 2024 cost index page: The actual July 2024 "
    "Historical Index = 294.8",
    "url": "https://www.rsmeans.com/landing-pages/2024-rsmeans-cost-index",
}


async def firm_with_roles(seed: Seed) -> str:
    org = await seed.org("Basis")
    await seed.member(org, "olivia", OrgRole.OWNER, own_firm=False)
    await seed.member(org, "ed", OrgRole.ENGINEER, own_firm=False)
    await seed.member(org, "vic", OrgRole.VIEWER, own_firm=False)
    await seed.user("nina", own_firm=False)
    return str(org)


async def counts(sessionmaker: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with session_scope(sessionmaker) as session:
        values = await session.scalar(select(func.count()).select_from(CostIndexValue))
        runs = await session.scalar(select(func.count()).select_from(CostIndexRun))
        return int(values or 0), int(runs or 0)


async def test_an_owner_enters_a_value_and_the_ledger_says_who(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await firm_with_roles(seed)
    response = await invite_only_api.post("olivia", "/v1/cost-indices", VALUE_2024)
    assert response.status_code == 201, response.text
    body = response.json()
    value, run = body["value"], body["run"]
    assert (value["source"], value["series"], value["year"], value["region"]) == (
        "manual",
        "historical",
        2024,
        "US",
    )
    assert Decimal(value["value"]) == Decimal("294.8") and value["as_of"] == "2024-07-01"
    assert value["version"] == 1 and value["derived"] is None
    assert value["citation"] == VALUE_2024["citation"] and value["url"] == VALUE_2024["url"]
    assert value["obtained_by_name"] == "olivia@example.test"
    assert value["run_id"] == run["id"]
    assert (run["trigger"], run["actor_type"], run["status"], run["rows_added"]) == (
        "manual",
        "engineer",
        "ok",
        1,
    )
    assert run["years_added"] == [2024] and run["actor_name"] == "olivia@example.test"
    async with session_scope(sessionmaker) as session:
        row = await session.get(CostIndexValue, value["id"])
        assert row is not None and str(row.obtained_by) == value["obtained_by"]
    # Everyone in a firm reads it back; the listing is newest first, current versions only.
    listed = await invite_only_api.get("vic", "/v1/cost-indices", params={"year": 2024})
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()["items"]] == [value["id"]]
    runs = await invite_only_api.get("ed", "/v1/cost-indices/runs")
    assert runs.status_code == 200 and [r["id"] for r in runs.json()["items"]] == [run["id"]]


async def test_only_an_owner_may_enter_and_only_a_member_may_read(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await firm_with_roles(seed)
    for uid in ("ed", "vic"):
        response = await invite_only_api.post(uid, "/v1/cost-indices", VALUE_2024)
        assert response.status_code == 403, response.text
        assert response.headers["content-type"] == PROBLEM_JSON
        assert response.json()["code"] == E.API_FORBIDDEN
    nobody = await invite_only_api.post("nina", "/v1/cost-indices", VALUE_2024)
    assert nobody.status_code == 403 and nobody.json()["type"].endswith("no-firm")
    assert (await invite_only_api.get("nina", "/v1/cost-indices")).status_code == 403
    assert (await invite_only_api.get("nina", "/v1/cost-indices/runs")).status_code == 403
    assert await counts(sessionmaker) == (0, 0)


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"region": "CA"}, "historical value is national"),
        ({"as_of": "2023-07-01"}, "as_of must fall"),
        ({"value": "0"}, "above zero"),
        ({"value": "abc"}, "pattern"),
        ({"value": 294.8}, "string"),
        ({"value": "12345678901.5"}, "pattern"),
        ({"citation": ""}, "citation"),
        ({"series": "city"}, "needs a region"),
        ({"series": "city", "region": "CA:900-902"}, "detail.city"),
        ({"region": "ca"}, "pattern"),
        ({"year": 1800}, "greater than or equal"),
        ({"extra": 1}, "extra"),
    ],
)
async def test_the_body_is_validated(
    invite_only_api: Api, seed: Seed, change: dict[str, Any], field: str
) -> None:
    await firm_with_roles(seed)
    response = await invite_only_api.post("olivia", "/v1/cost-indices", {**VALUE_2024, **change})
    assert response.status_code == 422, response.text
    assert field.lower() in response.text.lower()


async def test_the_same_value_twice_is_a_conflict_and_a_new_value_the_next_version(
    invite_only_api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await firm_with_roles(seed)
    first = await invite_only_api.post("olivia", "/v1/cost-indices", VALUE_2024)
    assert first.status_code == 201
    duplicate = await invite_only_api.post("olivia", "/v1/cost-indices", VALUE_2024)
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["type"].endswith("duplicate-value")
    assert duplicate.json()["code"] == E.COST_INDEX_DUPLICATE
    assert await counts(sessionmaker) == (1, 1)  # neither a row nor a ledger row
    revised = await invite_only_api.post(
        "olivia",
        "/v1/cost-indices",
        {
            **VALUE_2024,
            "value": "295.0",
            "citation": "RSMeans Building Construction Costs 2025 p.1",
        },
    )
    assert revised.status_code == 201 and revised.json()["value"]["version"] == 2
    current = await invite_only_api.get("olivia", "/v1/cost-indices", params={"year": 2024})
    assert [r["version"] for r in current.json()["items"]] == [2]
    every = await invite_only_api.get(
        "olivia", "/v1/cost-indices", params={"year": 2024, "all_versions": "true"}
    )
    assert sorted(r["version"] for r in every.json()["items"]) == [1, 2]
    assert await counts(sessionmaker) == (2, 2)


async def test_a_city_row_and_a_converted_value_are_entered_with_their_detail(
    invite_only_api: Api, seed: Seed
) -> None:
    await firm_with_roles(seed)
    city = await invite_only_api.post(
        "olivia",
        "/v1/cost-indices",
        {
            "series": "city",
            "year": 2024,
            "region": "CA:900-902",
            "value": "1.05",
            "as_of": "2024-01-01",
            "citation": "RSMeans Residential 2024 Location Factors p.720",
            "detail": {"city": "Los Angeles", "state": "CA", "printed_page": 720},
        },
    )
    assert city.status_code == 201, city.text
    assert city.json()["value"]["detail"]["city"] == "Los Angeles"
    converted = await invite_only_api.post(
        "olivia",
        "/v1/cost-indices",
        {
            **VALUE_2024,
            "year": 2025,
            "as_of": "2025-07-01",
            "value": "308.4",
            "derived": "Turner BCI 2025 Q4 1510 / 2020 Q4 1171 x 239.1 (error band -2 to +1 %)",
        },
    )
    assert converted.status_code == 201 and converted.json()["value"]["derived"].startswith(
        "Turner"
    )
    listed = await invite_only_api.get("olivia", "/v1/cost-indices", params={"series": "city"})
    assert [r["region"] for r in listed.json()["items"]] == ["CA:900-902"]


async def test_the_write_replays_under_its_idempotency_key(
    invite_only_api: Api, seed: Seed
) -> None:
    await firm_with_roles(seed)
    key = new_key()
    first = await invite_only_api.post("olivia", "/v1/cost-indices", VALUE_2024, key=key)
    replay = await invite_only_api.post("olivia", "/v1/cost-indices", VALUE_2024, key=key)
    assert first.status_code == replay.status_code == 201
    assert replay.headers.get(REPLAYED_HEADER) == "true"
    assert replay.json()["value"]["id"] == first.json()["value"]["id"]


async def test_the_listing_pages_with_a_cursor_and_refuses_a_bad_one(
    invite_only_api: Api, seed: Seed
) -> None:
    await firm_with_roles(seed)
    for year in (2021, 2022, 2023):
        response = await invite_only_api.post(
            "olivia",
            "/v1/cost-indices",
            {
                **VALUE_2024,
                "year": year,
                "as_of": f"{year}-07-01",
                "value": f"{250 + year - 2021}.0",
            },
        )
        assert response.status_code == 201
    first = await invite_only_api.get("olivia", "/v1/cost-indices", params={"limit": 2})
    assert len(first.json()["items"]) == 2 and first.json()["next_cursor"]
    second = await invite_only_api.get(
        "olivia", "/v1/cost-indices", params={"limit": 2, "cursor": first.json()["next_cursor"]}
    )
    assert len(second.json()["items"]) == 1 and second.json()["next_cursor"] is None
    seen = {r["year"] for r in first.json()["items"]} | {r["year"] for r in second.json()["items"]}
    assert seen == {2021, 2022, 2023}
    bad = await invite_only_api.get("olivia", "/v1/cost-indices", params={"cursor": "nonsense"})
    assert bad.status_code == 422 and bad.json()["type"].endswith("invalid-cursor")
