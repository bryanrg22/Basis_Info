"""``process_assets`` with an engineer's traced measurement (EVAL_REPORT 97).

The patio of the measured house (a site item priced per SF that the appraisal cannot size)
is corrected at the assets review: its takeoff is replaced by the traced shape's quantity
(the computed one kept, the shape recorded under ``inputs["measurement"]``) and priced; each
review flag appears exactly when it should and never changes the quantity; a typed quantity
writes byte for byte what it wrote before (the values below were read from ``main`` at
e885ca1 with the same scenario); a measurement-only correction is handled exactly as a
quantity-only one (both a ``manual`` asset, both a convention candidate); a later
correction's quantity replaces an earlier one's, typed or traced; a bad measurement is
refused by ``apply_corrections`` and ``process_assets`` without a retry."""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fixtures import fixture_path
from lifecycletest_support import SeededStudy, intake_document, make_deps, set_intake
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from basis.activities import Deps
from basis.activities.apply_corrections import ApplyCorrections
from basis.activities.process_assets import ProcessAssets
from basis.activities.promote_conventions import _candidates
from basis.db.enums import ClassificationMethod
from basis.db.models import Asset, CostEstimate, Takeoff
from basis.db.session import session_scope
from basis.domain import traced
from basis.errors.codes import E
from basis.workflow.contracts import ApplyCorrectionsInput, CorrectionInput

from .conftest import Env
from .test_process_assets import (
    MEASURED_FIELDS,
    add_site_components,
    analyzed,
    labelled,
    process,
    seed_extraction,
    start_revision_two,
)

__all__ = ["analyzed"]  # the fixture, imported for this module

pytestmark = pytest.mark.integration

D = Decimal
# 20 x 40 ft traced on a site plan at 0.2 ft per pixel: 800 SF, expected error 7 %.
PLAN: dict[str, Any] = {
    "unit": "SF",
    "space": "image",
    "shapes": [{"kind": "area", "ring": [[0, 0], [100, 0], [100, 200], [0, 200]]}],
    "scale": {"points": [[0, 0], [100, 0]], "length": 20, "unit": "ft"},
    "image": {"source": "site plan sheet C1", "captured_on": "2025-04-01"},
}
TYPED = {"quantity": "812.5", "quantity_unit": "SF", "quantity_source": "site plan sheet C1"}


def variant(path: list[Any], value: Any) -> dict[str, Any]:
    body = copy.deepcopy(PLAN)
    node: Any = body
    for key in path[:-1]:
        node = node[key]
    if value is None:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return body


async def priced_deps(deps: Deps) -> Deps:
    settings = deps.settings.model_copy(
        update={"cost_table_path": str(fixture_path("cost_table.json")), "cost_year": 2026}
    )
    return make_deps(deps.sessionmaker, deps.storage, settings)


async def corrected(
    deps: Deps,
    seeded: SeededStudy,
    activity_env: Env,
    after_of: dict[str, dict[str, Any] | list[dict[str, Any]]],
    *,
    intake: bool = False,
) -> tuple[dict[str, Asset], dict[str, Asset], dict[str, Takeoff], dict[str, CostEstimate]]:
    """Revision 1 of the measured house, then an assets rejection correcting the labels of
    ``after_of`` (a list: several corrections of the same asset, in order); returns the
    first asset of each label at revisions 1 and 2, and revision 2's takeoffs and costs."""
    priced = await priced_deps(deps)
    await seed_extraction(deps, seeded, MEASURED_FIELDS)
    await add_site_components(deps, seeded)
    if intake:
        await set_intake(deps.sessionmaker, seeded.study_id, intake_document())
    await activity_env.run(ProcessAssets(priced).run, process(seeded))
    first: dict[str, Asset] = {}
    for label, asset in await labelled(deps, seeded.study_id, 1):
        first.setdefault(label, asset)
    await start_revision_two(deps, seeded, activity_env)
    corrections = []
    for label, afters in after_of.items():
        for after in afters if isinstance(afters, list) else [afters]:
            corrections.append(
                CorrectionInput(entity_type="asset", entity_id=str(first[label].id), after=after)
            )
    await activity_env.run(
        ProcessAssets(priced).run, process(seeded, revision=2, corrections=tuple(corrections))
    )
    second: dict[str, Asset] = {}
    for label, asset in await labelled(deps, seeded.study_id, 2):
        second.setdefault(label, asset)
    async with session_scope(deps.sessionmaker) as session:
        takeoffs = {t.asset_id: t for t in (await session.scalars(select(Takeoff))).all()}
        costs = {c.asset_id: c for c in (await session.scalars(select(CostEstimate))).all()}
    return (
        first,
        second,
        {label: takeoffs[a.id] for label, a in second.items()},
        {label: costs[a.id] for label, a in second.items()},
    )


def reasons(asset: Asset) -> list[str]:
    return [r for r in (asset.review_reason or "").split("; ") if r]


async def test_a_traced_shape_replaces_the_takeoff_and_prices_it(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    first, second, takeoffs, costs = await corrected(
        deps, analyzed, activity_env, {"patio": {"measurement": PLAN}}, intake=True
    )
    patio, takeoff, cost = second["patio"], takeoffs["patio"], costs["patio"]
    assert (takeoff.quantity, takeoff.unit) == (D("800.00"), "SF")
    assert takeoff.formula == (
        "800.00 SF traced by the engineer on site plan sheet C1 (captured 2025-04-01), 1 area "
        "of 4 corners, scale 0.2 ft per pixel; expected error up to 7.00 %"
    )
    inputs = takeoff.inputs
    assert inputs["quantity_source"] == "engineer"
    assert inputs["measured_by_engineer"] == "site plan sheet C1"
    assert inputs["computed_quantity"] == "1.00 EA"  # the count the takeoff had, kept
    assert "needs_measurement" not in inputs and "missing_input" not in inputs
    record = inputs["measurement"]
    assert record == traced.measure(PLAN).record()
    assert (record["area_sf"], record["quantity"], record["method"]) == (
        "800.00",
        "800.00",
        "image_scale",
    )
    assert inputs["asset_correction"]["after"] == {"measurement": PLAN}
    # Priced per SF from the table; the trail carries the quantity with the shape.
    assert cost.total is not None and cost.unit_cost is not None
    assert cost.total == (cost.unit_cost * D("800.00")).quantize(D("0.01"))
    assert cost.trail["quantity"]["value"] == "800.00"
    assert cost.trail["quantity"]["inputs"]["measurement"]["area_sf"] == "800.00"
    assert first["patio"].review_reason and "COST_NEEDS_QUANTITY" in first["patio"].review_reason
    assert not any(r.startswith(("measure:", "cross:COST_NEEDS")) for r in reasons(patio))
    assert patio.method is ClassificationMethod.MANUAL  # as a typed quantity's (below)


@pytest.mark.parametrize(
    ("measurement", "flags"),
    [
        (variant(["image", "captured_on"], None), [traced.UNDATED]),
        (variant(["image", "captured_on"], "2026-05-01"), []),  # 365 days after 2025-05-01
        (variant(["image", "captured_on"], "2026-05-02"), [traced.AFTER_SERVICE]),
        (
            # 1 ft per pixel: 120 ft x 1 / 800 + 2 x 2 / 20 = 35 %.
            variant(["scale"], {"points": [[0, 0], [20, 0]], "length": 20, "unit": "ft"}),
            [traced.COARSE],
        ),
        (
            {
                "unit": "SF",
                "space": "lonlat",
                "shapes": [
                    {
                        "kind": "area",
                        "ring": [
                            [-88.9, 43.8],
                            [-88.8999, 43.8],
                            [-88.8999, 43.8001],
                            [-88.9, 43.8001],
                        ],
                    }
                ],
                "image": {"source": "NAIP 2025", "captured_on": "2025-04-01"},
            },
            [traced.ERROR_UNKNOWN],
        ),
    ],
    ids=["undated", "a year after service", "a day past a year", "coarse", "no pixel size"],
)
async def test_each_flag_appears_exactly_when_it_should_and_never_changes_the_quantity(
    deps: Deps,
    analyzed: SeededStudy,
    activity_env: Env,
    measurement: dict[str, Any],
    flags: list[str],
) -> None:
    _, second, takeoffs, _ = await corrected(
        deps, analyzed, activity_env, {"patio": {"measurement": measurement}}, intake=True
    )
    assert [r for r in reasons(second["patio"]) if r.startswith("measure:")] == flags
    assert takeoffs["patio"].quantity == traced.measure(measurement).quantity
    if flags:
        assert second["patio"].needs_review


async def test_without_an_intake_the_after_service_check_is_skipped(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    late = variant(["image", "captured_on"], "2031-01-01")
    _, second, _, _ = await corrected(
        deps, analyzed, activity_env, {"patio": {"measurement": late}}
    )
    assert not [r for r in reasons(second["patio"]) if r.startswith("measure:")]


async def test_a_typed_quantity_writes_what_it_wrote_before(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """The regression line: every value below was read from main (e885ca1) for the same
    scenario before section 97's change, only the asset id left out."""
    first, second, takeoffs, costs = await corrected(deps, analyzed, activity_env, {"patio": TYPED})
    patio, takeoff, cost = second["patio"], takeoffs["patio"], costs["patio"]
    assert (patio.method.value, patio.review_reason, patio.needs_review) == (
        "manual",
        "cost:YEAR_INDEX_MISSING",
        True,
    )
    assert (str(takeoff.quantity), takeoff.unit) == ("812.5000", "SF")
    assert takeoff.formula == "812.50 SF measured by the engineer (site plan sheet C1)"
    location = (
        "state IL (subject.state (azure)): mean of 19 IL cities (0.92-1.00), RSMeans "
        "Residential 2020 Location Factors - Residential pp.718-722"
    )
    assumption = (
        "no published cost index for 2026 is loaded; the latest, 239.1 (Jan 2020, est.), is "
        "applied. Enter the 2026 Historical Cost Index (RSMeans quarterly Construction Cost "
        "Index or the year's book; POST /v1/cost-indices or python -m basis.costs.refresh "
        "enter) before the study is signed"
    )
    assert takeoff.inputs == {
        "count": 1,
        "method": "area",
        "component": "patio",
        "assumptions": [],
        "min_per_room": 1,
        "room_area_sf": "150",
        "property_class": "residential_rental",
        "location_source": location,
        "quantity_source": "engineer",
        "asset_correction": {"after": TYPED, "from_asset_id": str(first["patio"].id)},
        "cost_year_source": "COST_YEAR setting",
        "room_area_source": "default_150_sf",
        "computed_quantity": "1.00 EA",
        "cost_year_assumption": assumption,
        "measured_by_engineer": "site plan sheet C1",
        "property_class_source": "PROPERTY_CLASS setting",
    }
    assert (str(cost.unit_cost), str(cost.total)) == ("4.32", "3510.00")
    assert cost.source_row == (
        "synthetic_2020[patio] synthetic 32-0001, p. 6 per SF; Total Incl O&P (base 2020, "
        "x1.0000 for 2026 = HCI 239.1 (Jan 2020, est.) / 239.1 (Jan 2020, est.), RSMeans "
        "Building Construction Costs 2020 p.778; no 2026 index loaded, the latest applied "
        f"(assumption), from COST_YEAR setting; x0.959 location from {location})"
    )
    assert cost.trail["quantity"]["value"] == "812.50"
    assert cost.trail["quantity"]["inputs"] == takeoff.inputs


async def test_a_measurement_only_correction_is_handled_as_a_quantity_only_one(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    """Both are an engineer's correction of the asset: a ``manual`` asset with the old
    classification, a candidate for the firm's conventions at approval (REVIEW_RULES 7.4),
    the same today for a typed quantity (EVAL_REPORT 97 records it as the maintainer's
    decision); a price-only correction is neither (OVERRIDES.md section 3)."""
    _, second, _, _ = await corrected(
        deps,
        analyzed,
        activity_env,
        {"patio": {"measurement": PLAN}, "landscaping": TYPED},
    )
    traced_asset, typed_asset = second["patio"], second["landscaping"]
    assert traced_asset.method is typed_asset.method is ClassificationMethod.MANUAL
    assert (traced_asset.confidence, typed_asset.confidence) == (1.0, 1.0)
    async with session_scope(deps.sessionmaker) as session:
        found = await _candidates(session, deps.settings, analyzed.study_id, 2, None)
    candidates = {c.asset.id for c in found}
    assert {traced_asset.id, typed_asset.id} <= candidates


@pytest.mark.parametrize(
    ("afters", "quantity", "source"),
    [
        ([TYPED, {"measurement": PLAN}], D("800.00"), "engineer"),
        ([{"measurement": PLAN}, TYPED], D("812.50"), "engineer"),
    ],
    ids=["typed then traced", "traced then typed"],
)
async def test_a_later_corrections_quantity_replaces_an_earlier_one(
    deps: Deps,
    analyzed: SeededStudy,
    activity_env: Env,
    afters: list[dict[str, Any]],
    quantity: Decimal,
    source: str,
) -> None:
    _, _, takeoffs, _ = await corrected(deps, analyzed, activity_env, {"patio": afters})
    assert takeoffs["patio"].quantity == quantity
    assert takeoffs["patio"].inputs["quantity_source"] == source
    assert ("measurement" in takeoffs["patio"].inputs) is (quantity == D("800.00"))


async def test_a_document_id_is_taken_as_checked_by_the_api(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    named = variant(["image", "document_id"], "6f9619ff-8b86-d011-b42d-00c04fc964ff")
    _, _, takeoffs, _ = await corrected(
        deps, analyzed, activity_env, {"patio": {"measurement": named}}
    )
    assert (
        takeoffs["patio"].inputs["measurement"]["image"]["document_id"]
        == named["image"]["document_id"]
    )


async def test_a_bad_measurement_is_refused_and_not_retried(
    deps: Deps, analyzed: SeededStudy, activity_env: Env
) -> None:
    await activity_env.run(ProcessAssets(deps).run, process(analyzed))
    target = next(a for label, a in await labelled(deps, analyzed.study_id, 1))
    bad = CorrectionInput(
        entity_type="asset", entity_id=str(target.id), after={"measurement": {**PLAN, "unit": "EA"}}
    )
    with pytest.raises(ApplicationError) as applied:
        await activity_env.run(
            ApplyCorrections(deps).run,
            ApplyCorrectionsInput(
                study_id=str(analyzed.study_id),
                stage="assets_review",
                from_revision=1,
                to_revision=2,
                corrections=(bad,),
            ),
        )
    assert applied.value.non_retryable and E.CORRECT_MEASUREMENT in str(applied.value)
    await start_revision_two(deps, analyzed, activity_env)
    with pytest.raises(ApplicationError) as caught:
        await activity_env.run(ProcessAssets(deps).run, process(analyzed, 2, corrections=(bad,)))
    assert caught.value.non_retryable and E.CORRECT_MEASUREMENT in str(caught.value)
    async with session_scope(deps.sessionmaker) as session:
        written = (
            await session.scalars(
                select(Asset).where(Asset.study_id == analyzed.study_id, Asset.revision == 2)
            )
        ).all()
    assert written == []


def test_the_placed_in_service_date_of_the_default_intake() -> None:
    # The flag cases above count from it: 2025-05-01 plus 365 days is 2026-05-01.
    assert intake_document().placed_in_service_date == date(2025, 5, 1)
