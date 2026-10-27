"""``evals.appraisal.synth_1004`` without the blank form: profiles, the value generator,
the truth it plans for every catalogue key, the layout tables and the quote builder.

The PDF itself (drawing on the private Form 70, placement, scans) is checked by
``tests/evals/test_synth_1004_form.py`` (``eval_data``).
"""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from basis.extraction.field_mappings import FIELDS
from evals.appraisal import synth_1004 as S
from evals.appraisal.truth import CATALOGUE_KEYS, TruthSet, dump_truth

COUNT, SCANS, SEED = 24, 3, 7
FAKE_SHA = "0" * 64


@pytest.fixture(scope="module")
def plans() -> list[S.DocPlan]:
    return S.plan_set(COUNT, SCANS, SEED)


def num(plan: S.DocPlan, key: str) -> Any:
    """A present field's value, 0 for a not-developed one (a blank or skipped line)."""
    fp = plan.fields[key]
    return fp.value if fp.status == "present" else 0


# --------------------------------------------------------------------------- profiles


def test_every_edge_case_is_given_to_some_document() -> None:
    profiles = S.assign_profiles(COUNT, SCANS, SEED)
    assert len(profiles) == COUNT + SCANS
    assert [p.scan for p in profiles] == [False] * COUNT + [True] * SCANS
    flags = {
        "cost_not_developed": [not p.cost_developed for p in profiles],
        "site_value_blank": [not p.cost_developed and not p.site_value_printed for p in profiles],
        "site_value_alone": [not p.cost_developed and p.site_value_printed for p in profiles],
        "income_zero": [p.income_zero for p in profiles],
        "income_blank": [not p.income_zero for p in profiles],
        "half_bath": [p.half_bath for p in profiles],
        "acres": [p.site_acres for p in profiles],
        "multi_car": [p.multi_car_storage for p in profiles],
        "heating_other": [p.heating_other for p in profiles],
        "foundation_multi": [p.foundation_multi for p in profiles],
        "photo_page": [p.photo_page for p in profiles],
        "addendum": [p.addendum for p in profiles],
        "long_legal": [p.long_legal for p in profiles],
        "unit_suffixes": [p.unit_suffixes for p in profiles],
        "refinance": [p.assignment == "refinance" for p in profiles],
    }
    for name, marks in flags.items():
        assert any(marks), name
    planted = Counter(p.planted for p in profiles if p.planted)
    assert set(planted) == {"cost_indicated_total", "cost_new_total", "reconciliation_mismatch"}
    # An arithmetic error needs a cost section to plant it in.
    assert all(p.cost_developed for p in profiles if p.planted)
    splits = Counter(p.split for p in profiles)
    assert splits["regression"] == COUNT // 8 and splits["dev"] == COUNT + SCANS - COUNT // 8
    assert all(p.split == "dev" for p in profiles if p.scan)


def test_a_two_document_run_still_covers_both_sides_of_each_switch() -> None:
    profiles = S.assign_profiles(2, 0, SEED)
    assert {p.cost_developed for p in profiles} == {True, False}
    assert any(p.planted for p in profiles)
    for attr in ("half_bath", "site_acres", "photo_page", "addendum", "heating_other"):
        assert any(getattr(p, attr) for p in profiles), attr


def test_profiles_and_plans_are_deterministic_per_seed() -> None:
    assert S.assign_profiles(COUNT, SCANS, SEED) == S.assign_profiles(COUNT, SCANS, SEED)
    a = S.plan_set(5, 1, SEED)
    b = S.plan_set(5, 1, SEED)
    assert a == b
    other = S.plan_set(5, 1, SEED + 1)
    assert [p.fields["subject.property_address"].value for p in a] != [
        p.fields["subject.property_address"].value for p in other
    ]


# --------------------------------------------------------------------------- truth coverage


def test_every_catalogue_key_has_one_plan_per_document(plans: list[S.DocPlan]) -> None:
    assert len(CATALOGUE_KEYS) == len(FIELDS) == 134
    for plan in plans:
        assert set(plan.fields) == CATALOGUE_KEYS, plan.name
        for key, fp in plan.fields.items():
            assert fp.key == key
            if fp.status == "present":
                assert fp.type is not None and fp.value is not None and fp.page is not None
                assert fp.printed, key
            if fp.status == "absent":
                assert fp.page is None and fp.printed is None and fp.note


def test_only_boxes_form_70_lacks_are_absent(plans: list[S.DocPlan]) -> None:
    for plan in plans:
        absent = {k for k, fp in plan.fields.items() if fp.status == "absent"}
        expected = set(S.ABSENT_KEYS)
        if not plan.profile.site_acres:
            expected.add("site.area_acres")  # no acres printed: nothing to read
        assert absent == expected, plan.name


def test_every_drawn_field_has_a_box_in_the_layout(plans: list[S.DocPlan]) -> None:
    for plan in plans:
        for key, fp in plan.fields.items():
            if fp.checks:
                assert all(code in S.CHECKS[key] for code in fp.checks), key
            elif fp.printed is not None:
                slot = fp.slot or S.COMPOSITE_SOURCES[key]
                assert slot in S.SLOTS, key
        for group, codes in plan.checks.items():
            assert all(code in S.CHECKS[group] for code in codes), group
        for name in plan.texts:
            assert name in S.SLOTS, name
        for name in plan.blocks:
            assert name in S.BLOCKS, name
        for row, col in plan.grid:
            assert row in S.GRID_ROWS, row
            S.grid_slot(row, col)  # every cell resolves to a slot
    # Composite slots are drawn from the plan's own texts (one source of truth).
    for key, slot in S.COMPOSITE_SOURCES.items():
        assert slot in S.SLOTS, key
    # Every catalogue key except the absent ones is fillable on Form 70.
    fillable = {k for k in CATALOGUE_KEYS if k in S.SLOTS or k in S.CHECKS}
    fillable |= set(S.COMPOSITE_SOURCES)
    assert fillable == CATALOGUE_KEYS - set(S.ABSENT_KEYS)


def test_composite_lines_contain_their_fields(plans: list[S.DocPlan]) -> None:
    for plan in plans:
        for key, slot in S.COMPOSITE_SOURCES.items():
            fp = plan.fields[key]
            if fp.status != "present":
                continue
            line = plan.texts.get(slot) or plan.fields[slot].printed
            assert line is not None and fp.printed is not None and fp.printed in line, key


def test_enum_values_use_the_spec_vocabulary(plans: list[S.DocPlan]) -> None:
    for plan in plans:
        for key, fp in plan.fields.items():
            if fp.status != "present" or fp.type not in ("enum", "enum_multi"):
                continue
            codes = fp.value if fp.type == "enum_multi" else [fp.value]
            for code in codes:
                name = code["code"] if isinstance(code, dict) else code
                assert name in S.ENUM_LABELS[key] or name == "none", (key, code)
                if isinstance(code, dict):
                    assert name == "other" and code["text"]
            if fp.type == "enum_multi":
                plain = [c for c in codes if isinstance(c, str)]
                assert plain == sorted(plain), key


# --------------------------------------------------------------------------- arithmetic


def test_cost_approach_adds_up_unless_an_error_is_planted(plans: list[S.DocPlan]) -> None:
    checked = 0
    for plan in plans:
        if not plan.profile.cost_developed:
            continue
        f = plan.fields
        assert num(plan, "cost_approach.dwelling_sqft") == num(plan, "improvements.gla_sqft")
        for row in ("dwelling", "basement", "garage"):
            if f[f"cost_approach.{row}_cost"].status == "present":
                area = num(plan, f"cost_approach.{row}_sqft")
                rate = Decimal(str(num(plan, f"cost_approach.{row}_cost_per_sqft")))
                cost = int((area * rate).quantize(Decimal(1), rounding="ROUND_HALF_EVEN"))
                assert abs(cost - num(plan, f"cost_approach.{row}_cost")) <= 1, (plan.name, row)
        rows = sum(
            num(plan, f"cost_approach.{k}")
            for k in ("dwelling_cost", "basement_cost", "garage_cost", "mechanicals_misc")
        )
        depreciation = sum(
            num(plan, f"cost_approach.depreciation_{k}")
            for k in ("physical", "functional", "external")
        )
        assert num(plan, "cost_approach.depreciation") == depreciation > 0
        depreciated = num(plan, "cost_approach.depreciated_cost_improvements")
        assert depreciated == rows - depreciation  # computed from the rows, not the total
        indicated = (
            depreciated
            + num(plan, "cost_approach.site_improvements_value")
            + num(plan, "cost_approach.site_value")
        )
        total = num(plan, "cost_approach.total_cost_new")
        printed = num(plan, "cost_approach.indicated_value")
        recon = num(plan, "reconciliation.indicated_value_cost_approach")
        rules = {i.rule for i in plan.expected_issues}
        planted = plan.profile.planted
        assert (total != rows) == (planted == "cost_new_total") == ("cost_new_total" in rules)
        assert (
            (printed != indicated)
            == (planted == "cost_indicated_total")
            == ("cost_approach_total" in rules)
        )
        assert (
            (recon != printed)
            == (planted == "reconciliation_mismatch")
            == ("cost_reconciliation_mismatch" in rules)
        )
        if recon == printed:  # the same fact on pages 2 and 3
            assert f["cost_approach.indicated_value"].also_on == (2,)
            assert f["reconciliation.indicated_value_cost_approach"].also_on == (3,)
        else:
            assert not f["cost_approach.indicated_value"].also_on
        checked += 1
    assert checked >= 15


def test_values_are_internally_consistent(plans: list[S.DocPlan]) -> None:
    for plan in plans:
        f = plan.fields
        rooms, beds = num(plan, "improvements.total_rooms"), num(plan, "improvements.bedrooms")
        baths = f["improvements.bathrooms"].value
        assert rooms >= beds + baths["full"] + baths["half"]  # no rooms_vs_bed_bath false alarm
        assert f["improvements.bathrooms"].printed == f"{baths['full']}.{baths['half']}"
        assert baths["half"] == (1 if plan.profile.half_bath else 0)
        gla = num(plan, "improvements.gla_sqft")
        assert 190 * rooms <= gla <= 300 * rooms
        assert 0 <= num(plan, "improvements.basement_area_sqft") <= gla
        effective = S.date.fromisoformat(f["reconciliation.effective_date_of_appraisal"].value)
        signed = S.date.fromisoformat(f["appraiser.signature_date"].value)
        assert effective < signed <= S.date(2026, 10, 26)
        year_built = num(plan, "improvements.year_built")
        assert num(plan, "improvements.effective_age_years") <= effective.year - year_built
        final = num(plan, "reconciliation.final_market_value")
        assert final == num(plan, "sales_comparison.indicated_value")
        assert final == num(plan, "appraiser.appraised_value")
        if plan.profile.assignment == "purchase":
            contract = S.date.fromisoformat(f["listing_and_contract.contract_date"].value)
            listed = S.date.fromisoformat(f["listing_and_contract.listing_date"].value)
            assert listed < contract < effective
            assert (contract - listed).days == num(plan, "listing_and_contract.days_on_market")
            price = num(plan, "listing_and_contract.contract_price")
            assert abs(price - final) / final < 0.03
            assert num(plan, "listing_and_contract.offering_price") >= price - 100
        land_use = [
            num(plan, f"neighborhood.present_land_use_{k}")
            for k in ("one_unit", "two_to_four", "multi_family", "commercial", "other")
        ]
        assert sum(land_use) == 100
        assert (
            num(plan, "neighborhood.price_low_thousands")
            < num(plan, "neighborhood.price_predominant_thousands")
            < num(plan, "neighborhood.price_high_thousands")
        )
        if plan.profile.cost_developed:
            assert abs(num(plan, "cost_approach.indicated_value") - final) / final < 0.1


def test_site_area_in_acres_or_square_feet(plans: list[S.DocPlan]) -> None:
    for plan in plans:
        area = plan.fields["site.area_sqft"]
        assert plan.fields["site.area"].printed == area.printed
        if plan.profile.site_acres:
            acres = plan.fields["site.area_acres"].value
            assert area.value == {"sqft": round(acres * 43560), "printed_unit": "ac"}
            assert area.printed is not None and area.printed.startswith(f"{acres:.2f} ac")
        else:
            assert isinstance(area.value, int)
            assert area.printed is not None
            assert area.printed.replace(",", "").split()[0] == str(area.value)


# --------------------------------------------------------------------------- statuses


def test_not_developed_and_zero_policy(plans: list[S.DocPlan]) -> None:
    cost_keys = [k for k in CATALOGUE_KEYS if k.startswith("cost_approach.")]
    for plan in plans:
        f = plan.fields
        income = f["reconciliation.indicated_value_income_approach"]
        assert income.status == "not_developed" and income.page == 2
        assert income.printed == ("0" if plan.profile.income_zero else None)
        if not plan.profile.cost_developed:
            for key in cost_keys:
                if key == "cost_approach.site_value" and plan.profile.site_value_printed:
                    assert f[key].status == "present"
                else:
                    assert f[key].status == "not_developed" and f[key].page == 3, key
            assert f["reconciliation.indicated_value_cost_approach"].status == "not_developed"
        else:
            for key in ("functional", "external"):
                fp = f[f"cost_approach.depreciation_{key}"]
                # SPEC principle 2: a skipped cost line printed 0 is not developed...
                assert (
                    fp.status == "present"
                    and fp.value > 0
                    or (fp.status == "not_developed" and fp.printed == "0")
                )
            assert f["cost_approach.depreciation"].value > 0  # =$( N) is positive
        # ...while $0 outside the valuation approaches is a real value.
        assert f["subject.special_assessments"].status == "present"
        assert f["subject.special_assessments"].value == 0
        assert f["subject.hoa_dues"].status == "present"
        concessions = f["listing_and_contract.seller_concessions"]
        if plan.profile.assignment == "purchase":
            assert concessions.status == "present"
        else:
            assert concessions.status == "not_developed"
            assert f["listing_and_contract.contract_price"].status == "not_developed"
        garage = f["improvements.garage_cars"]
        has_garage = "garage" in f["improvements.car_storage"].value
        assert (garage.status == "present") == has_garage


def test_heating_other_is_an_object_inside_the_list(plans: list[S.DocPlan]) -> None:
    seen_double = False
    for plan in plans:
        heating = plan.fields["improvements.heating_type"]
        assert heating.type == "enum_multi"
        others = [c for c in heating.value if isinstance(c, dict)]
        assert bool(others) == plan.profile.heating_other
        if others:
            assert others == [{"code": "other", "text": others[0]["text"]}]
            assert "other" in heating.checks
        seen_double |= len(heating.checks) == 2
    assert seen_double  # an appraiser can check two boxes (SPEC section 4)


def test_multi_checks_and_units_profiles(plans: list[S.DocPlan]) -> None:
    for plan in plans:
        cars = plan.fields["improvements.car_storage"].value
        if plan.profile.multi_car_storage:
            assert len(cars) >= 4
        foundation = plan.fields["improvements.foundation_type"].value
        assert (len(foundation) == 2) == plan.profile.foundation_multi
        gla = plan.fields["improvements.gla_sqft"]
        assert gla.printed is not None
        assert gla.printed.endswith(" sq.ft.") == plan.profile.unit_suffixes
        assert gla.also_text == f"{gla.value:,}"
        age = plan.fields["improvements.effective_age_years"].printed
        assert age is not None and age.endswith(" Years") == plan.profile.unit_suffixes


def test_also_on_follows_the_appended_pages(plans: list[S.DocPlan]) -> None:
    for plan in plans:
        photo = plan.photo_page
        rooms = plan.fields["improvements.total_rooms"]
        assert 2 in rooms.also_on
        assert (photo in rooms.also_on) == (photo is not None)
        site_value = plan.fields["cost_approach.site_value"]
        if site_value.status == "present" and plan.addendum_page:
            assert plan.addendum_page in site_value.also_on
        for fp in plan.fields.values():
            assert all(1 <= p <= plan.pages and p != fp.page for p in fp.also_on), fp.key
        if photo is not None:
            labels = dict(plan.photo_block)
            assert labels["Total Rooms"] == rooms.printed
            assert labels["Gross Living Area"] == plan.fields["improvements.gla_sqft"].also_text


# --------------------------------------------------------------------------- truth file


def test_truth_validates_and_round_trips(plans: list[S.DocPlan], tmp_path: Path) -> None:
    docs = tuple(
        S.document_truth(p, f"synthetic_1004/{p.name}/appraisal.pdf", FAKE_SHA, p.pages)
        for p in plans
    )
    truth = TruthSet(set=S.SET_NAME, documents=docs)
    path = tmp_path / "truth.json"
    dump_truth(truth, path)
    loaded = TruthSet.model_validate(json.loads(path.read_text()))
    assert loaded == truth
    first = loaded.documents[0]
    assert first.source == "synthetic" and first.form == "urar_1004_2005"
    assert first.note is not None and first.note.startswith("profile: ")
    assert all(f.verified for f in first.fields)
    issues = [i for d in loaded.documents for i in d.expected_issues]
    assert {i.rule for i in issues} == {
        "cost_approach_total",
        "cost_new_total",
        "cost_reconciliation_mismatch",
    }
    scans = [d for d in loaded.documents if d.note and "scan" in d.note.split("; ")[0]]
    assert len(scans) == SCANS


# --------------------------------------------------------------------------- quotes


@pytest.mark.parametrize(
    ("line", "at", "printed", "caption", "quote"),
    [
        (
            "Property Address 12 Elm St City Oakton",
            17,
            "12 Elm St",
            "Property Address",
            "Property Address 12 Elm St",
        ),
        # A short value inside another number on the same line is never the match.
        (
            "Depreciation 93,109 0 6,900 =$( 100,009)",
            20,
            "0",
            "Depreciation",
            "Depreciation 93,109 0",
        ),
        # The caption after the value ("7 Rooms").
        ("contains: 7 Rooms 3 Bedrooms", 10, "7", "Rooms", "7 Rooms"),
        # A repeated caption: the nearest one before the value.
        ("Exterior Walls Stucco Walls Drywall", 28, "Drywall", "Walls", "Walls Drywall"),
        # No caption: a little left context, cut at a word boundary.
        (
            "OPINION OF SITE VALUE......= $ 68,000",
            31,
            "68,000",
            None,
            "OF SITE VALUE......= $ 68,000",
        ),
        ("abc", 1, "zz", None, None),
    ],
)
def test_make_quote(
    line: str, at: int, printed: str, caption: str | None, quote: str | None
) -> None:
    assert S.make_quote(line, at, printed, caption) == quote


def test_absent_keys_are_documented() -> None:
    assert set(S.ABSENT_KEYS) <= CATALOGUE_KEYS
    assert all(note for note in S.ABSENT_KEYS.values())
