"""``GET /v1/studies/{id}/result``: the basis worksheet and the allocated schedule
(docs/api/STUDY_RESULT.md). Real Postgres; synthetic assets and fabricated intake values."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from apitest_support import Api, Seed
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import Bucket, ClassificationMethod, OrgRole, Stage
from basis.db.repositories.asset import AssetRepository
from basis.db.session import session_scope

pytestmark = pytest.mark.integration

INTAKE: dict[str, Any] = {
    "purchase_price": "100000.00",
    "acquisition_costs": [{"category": "recording_fees", "amount": "150.00"}],
    "land_method": "appraisal_site_value",
    "appraisal_site_value": "20000.00",
    "appraisal_effective_date": "2025-03-01",
    "property_use": "nonresidential",
    "transient_lodging": True,
    "placed_in_service_date": "2025-05-01",
    "acquisition_date": "2025-04-15",
    "state": "CA",
    "zip": "90001",
}


async def seed_assets(
    sm: async_sessionmaker[AsyncSession],
    study_id: str,
    revision: int,
    specs: list[tuple[str, Bucket, str | None]],
) -> list[uuid.UUID]:
    """Assets of ``revision`` with a cost total (or none), no component; returns their ids."""
    ids: list[uuid.UUID] = []
    async with session_scope(sm) as session:
        repo = AssetRepository(session)
        for category, bucket, total in specs:
            asset = await repo.create(
                study_id=uuid.UUID(study_id),
                revision=revision,
                component_id=None,
                category=category,
                bucket=bucket,
                recovery_period=Decimal("39") if bucket is Bucket.Y39 else Decimal("5"),
                irs_section=None,
                method=ClassificationMethod.RULE,
                confidence=None,
            )
            await repo.add_cost_estimate(
                asset=asset,
                unit_cost=None,
                total=None if total is None else Decimal(total),
                source_row="synthetic",
            )
            ids.append(asset.id)
    return ids


async def test_result_without_intake_is_the_schedule_alone(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    ids = await seed_assets(
        sessionmaker,
        study["id"],
        1,
        [("Carpet", Bucket.Y5, "100.00"), ("Toilet", Bucket.Y39, None)],
    )
    response = await api.get("alice", f"/v1/studies/{study['id']}/result")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["revision"], body["intake"], body["missing"]) == (1, False, ["intake"])
    assert body["worksheet"] is None and body["allocation"] is None
    assert body["schedule"]["rcn_total"] == "100.00"
    assert body["schedule"]["unpriced"] == [str(ids[1])]
    assert body["schedule"]["by_bucket"] == [
        {"bucket": "y5", "assets": 1, "rcn": "100.00", "rcnld": None},
        {"bucket": "y39", "assets": 1, "rcn": "0.00", "rcnld": None},
    ]
    assert [n["system"] for n in body["not_produced"]][:2] == ["Foundations", "Structural Framing"]


async def test_result_allocates_the_revision_to_the_intake_basis(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice", intake=INTAKE)
    await seed.stage(study["id"], Stage.ASSETS_REVIEW, revision=2)
    # Revision 1 is history; revision 2 is current and has a land asset to keep out.
    await seed_assets(sessionmaker, study["id"], 1, [("Carpet", Bucket.Y5, "10.00")])
    ids = await seed_assets(
        sessionmaker,
        study["id"],
        2,
        [
            ("Carpet", Bucket.Y5, "1000.00"),
            ("Kitchen cabinets", Bucket.Y5, "2000.00"),
            ("Framing", Bucket.Y39, "7000.00"),
            ("Site", Bucket.LAND, "1.00"),
            ("Toilet", Bucket.Y39, None),
        ],
    )
    response = await api.get("alice", f"/v1/studies/{study['id']}/result")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 2 and body["missing"] == []
    ws = body["worksheet"]
    # 100000 − 20000 = 80000 (B); + 150 = 80150 (T).
    assert (ws["land"], ws["building_basis"], ws["capitalized_costs"], ws["total_basis"]) == (
        "20000.00",
        "80000.00",
        "150.00",
        "80150.00",
    )
    assert ws["recovery_period"] == "39"
    assert ws["acquisition_costs"][0]["authority"].startswith("Reg. 1.263(a)-2(f)(2)(ii)(H)")
    checks = {c["code"]: c["severity"] for c in ws["checks"]}
    assert checks["land_share_of_price"] == "ok" and checks["land_value_date"] == "ok"
    a = body["allocation"]
    # f = 80150 / 10000 = 8.015: 8015.00, 16030.00, 56105.00.
    assert a["factor"] == "8.015000000"
    assert {r["category"]: r["allocated"] for r in a["assets"]} == {
        "Carpet": "8015.00",
        "Kitchen cabinets": "16030.00",
        "Framing": "56105.00",
    }
    assert [r["asset_id"] for r in a["assets"]] == sorted(str(i) for i in ids[:3])
    assert a["rows_sum"] == "80150.00"
    y5, y39 = a["by_bucket"]
    assert (y5["allocated"], y5["percent"], y5["building"], y5["closing_costs"]) == (
        "24045.00",
        "30.0",
        "24000.00",
        "45.00",
    )
    assert (y39["allocated"], y39["percent"], y39["building"], y39["closing_costs"]) == (
        "56105.00",
        "70.0",
        "56000.00",
        "105.00",
    )
    # Rows with no label are Miscellaneous Building Components: the page is always built.
    assert [s["system"] for s in a["by_system"]] == ["Miscellaneous Building Components"]
    assert {c["code"]: c["severity"] for c in a["checks"]} == {"uniform_factor": "error"}
    assert body["schedule"]["land"] == [str(ids[3])]
    assert body["schedule"]["unpriced"] == [str(ids[4])]

    history = await api.get("alice", f"/v1/studies/{study['id']}/result", params={"revision": 1})
    assert history.json()["allocation"]["rows_sum"] == "80150.00"
    assert history.json()["schedule"]["rcn_total"] == "10.00"
    assert (
        await api.get("alice", f"/v1/studies/{study['id']}/result", params={"revision": 3})
    ).status_code == 404


async def test_result_access_follows_the_study(api: Api, seed: Seed) -> None:
    study = await api.create_study("alice")
    org = study["org_id"]
    await seed.member(uuid.UUID(org), "vera", OrgRole.VIEWER)
    assert (await api.get("vera", f"/v1/studies/{study['id']}/result")).status_code == 200
    assert (await api.get("bob", f"/v1/studies/{study['id']}/result")).status_code == 404
    assert (await api.get("alice", f"/v1/studies/{uuid.uuid4()}/result")).status_code == 404
