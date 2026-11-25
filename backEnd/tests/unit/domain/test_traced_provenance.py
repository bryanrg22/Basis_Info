"""``basis.domain.traced``: who drew a measurement, whether it was verified, the picture it was
drawn on, and the measurement it replaced (EVAL_REPORT 104).

Each key accepted in every allowed form and refused in every other with its message; the
original checked and computed by the same rules, named under ``measurement.original``, never
nested; none of it changes the quantity (an unverified model draft is priced exactly as an
engineer's trace); a body without provenance stores, records and reads exactly as before; and
the request schema's depth limit still takes an original with cutouts."""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

import pytest

from basis.api.schemas.reviews import CorrectionIn
from basis.domain import traced
from basis.domain.traced import MeasurementError, measure, problems

D = Decimal
SCALE = {"points": [[0, 0], [100, 0]], "length": 20, "unit": "ft"}  # 0.2 ft per pixel
RECT = [[0, 0], [100, 0], [100, 200], [0, 200]]  # 20 x 40 ft = 800 SF
WIDER = [[0, 0], [110, 0], [110, 200], [0, 200]]  # 22 x 40 ft = 880 SF


def image_area(ring: list[list[float]] = RECT, **extra: Any) -> dict[str, Any]:
    return {
        "unit": "SF",
        "space": "image",
        "shapes": [{"kind": "area", "ring": [list(p) for p in ring]}],
        "scale": dict(SCALE),
        "image": {"source": "kitchen photo", "captured_on": "2020-04-18"},
        **extra,
    }


AERIAL_LINE: dict[str, Any] = {
    "unit": "LF",
    "space": "lonlat",
    "shapes": [{"kind": "line", "path": [[-88.9, 43.8], [-88.8995, 43.8], [-88.8995, 43.8004]]}],
    "image": {"source": "State orthoimagery 2020", "pixel_size_m": 0.15},
}


def refused(value: Any) -> list[str]:
    with pytest.raises(MeasurementError) as raised:
        measure(value)
    assert raised.value.problems == problems(value)
    return raised.value.problems


# --------------------------------------------------------------------------- regression


def test_a_body_without_provenance_is_stored_recorded_and_read_exactly_as_before() -> None:
    t = measure(image_area())
    assert (t.drawn_by, t.verified, t.view, t.original, t.by_model) == (None,) * 4 + (False,)
    assert set(t.record()) == {
        "unit",
        "space",
        "shapes",
        "scale",
        "image",
        "quantity",
        "method",
        "expected_error_pct",
        "shape_count",
        "corner_count",
        "cutout_count",
        "area_sf",
        "scale_ft_per_px",
    }
    assert traced.formula(t) == (
        "800.00 SF traced by the engineer on kitchen photo (captured 2020-04-18), "
        "1 area of 4 corners, scale 0.2 ft per pixel; expected error up to 7.00 %"
    )


def test_provenance_never_changes_the_quantity_or_the_expected_error() -> None:
    plain = measure(image_area())
    draft = measure(
        image_area(
            drawn_by="model:sam3-surface@2026-11-25",
            verified=False,
            view={"kind": "document", "id": "photo-1", "width": 1600, "height": 1200},
        )
    )
    assert (draft.quantity, draft.area_sf, draft.expected_error_pct) == (
        plain.quantity,
        plain.area_sf,
        plain.expected_error_pct,
    )
    assert traced.review_flags(
        draft, placed_in_service=None, after_service_days=365, error_flag_pct=D(10)
    ) == traced.review_flags(
        plain, placed_in_service=None, after_service_days=365, error_flag_pct=D(10)
    )


# --------------------------------------------------------------------------- drawn_by


@pytest.mark.parametrize(
    "who",
    ["engineer", "model:sam3@1", "model:claude-sonnet-4.5@surface_v2", "model:org/m_1@v1.2+b"],
)
def test_drawn_by_takes_the_engineer_or_a_model_with_its_version(who: str) -> None:
    t = measure(image_area(drawn_by=who))
    assert t.drawn_by == who and t.stored["drawn_by"] == who
    assert t.by_model is (who != "engineer")


@pytest.mark.parametrize(
    "who",
    [
        "",
        "Engineer",
        "model",
        "model:",
        "model:sam3",
        "model:@1",
        "model:sam3@",
        "model:sam 3@1",
        "Model:sam3@1",
        "model:sam3@1 ",
        "model:-x@1",
        "model:" + "a" * 101 + "@1",
        "model:a@" + "1" * 51,
        5,
        True,
        ["engineer"],
    ],
)
def test_drawn_by_refuses_anything_else(who: Any) -> None:
    assert refused(image_area(drawn_by=who)) == [
        "measurement.drawn_by must be engineer or model:<name>@<version> (letters, digits "
        f"and . _ - / +), got {traced._short(who)}"  # a long value is shortened
    ]


def test_a_models_draft_reads_drafted_by_basis_never_the_models_name() -> None:
    t = measure(image_area(drawn_by="model:sam3@1"))
    assert traced.formula(t).startswith("800.00 SF drafted by Basis on kitchen photo")
    assert "sam3" not in traced.formula(t)


# --------------------------------------------------------------------------- verified


@pytest.mark.parametrize(
    ("value", "tail"), [(True, "; verified by the engineer"), (False, "; not verified")]
)
def test_verified_is_a_boolean_and_the_formula_says_it(value: bool, tail: str) -> None:
    t = measure(image_area(verified=value))
    assert t.verified is value and t.stored["verified"] is value
    assert traced.formula(t).endswith("expected error up to 7.00 %" + tail)


@pytest.mark.parametrize("value", [1, 0, "true", "yes", [], {}])
def test_verified_refuses_what_is_not_a_boolean(value: Any) -> None:
    assert refused(image_area(verified=value)) == [
        f"measurement.verified must be true or false, got {value!r}"
    ]


# --------------------------------------------------------------------------- view


@pytest.mark.parametrize(
    "view",
    [
        {"kind": "capture", "id": "5a3c0001-0000-4000-8000-000000000001", "label": "aerial-close"},
        {"kind": "capture", "label": "history-2023-07-30"},
        {"kind": "public", "id": "naip-2022"},
        {
            "kind": "document",
            "id": "photo-1",
            "label": "kitchen.webp",
            "width": 1600,
            "height": 1200,
        },
    ],
)
def test_view_names_the_picture_it_was_drawn_on(view: dict[str, Any]) -> None:
    t = measure(image_area(view=copy.deepcopy(view)))
    assert t.view == view and t.stored["view"] == view and t.record()["view"] == view


def test_view_text_is_trimmed() -> None:
    t = measure(image_area(view={"kind": "public", "id": "  naip-2022 "}))
    assert t.view == {"kind": "public", "id": "naip-2022"}


@pytest.mark.parametrize(
    ("view", "problem"),
    [
        ("aerial", "measurement.view must be an object with kind and an id or a label"),
        (
            {"id": "x"},
            "measurement.view.kind must be capture, public or document, got None",
        ),
        (
            {"kind": "street", "id": "x"},
            "measurement.view.kind must be capture, public or document, got 'street'",
        ),
        ({"kind": "capture"}, "measurement.view must name the picture by its id or its label"),
        ({"kind": "capture", "id": "x", "zoom": 2}, "measurement.view has unknown keys: zoom"),
        (
            {"kind": "capture", "id": "   "},
            "measurement.view.id must be text of 1 to 200 characters, got '   '",
        ),
        (
            {"kind": "capture", "label": 7},
            "measurement.view.label must be text of 1 to 200 characters, got 7",
        ),
        (
            {"kind": "capture", "id": "a\x00b"},
            "measurement.view.id must be text of 1 to 200 characters, got 'a\\x00b'",
        ),
        (
            {"kind": "document", "id": "p", "width": 1600},
            "measurement.view takes width and height together (the pixel frame of the points)",
        ),
        (
            {"kind": "document", "id": "p", "width": 0, "height": 10},
            "measurement.view.width must be above 0 and at most 100000, got 0",
        ),
        (
            {"kind": "document", "id": "p", "width": 10, "height": 100001},
            "measurement.view.height must be above 0 and at most 100000, got 100001",
        ),
        (
            {"kind": "document", "id": "p", "width": True, "height": 10},
            "measurement.view.width must be a number, got True",
        ),
    ],
)
def test_view_refuses_every_bad_form(view: Any, problem: str) -> None:
    assert refused(image_area(view=view)) == [problem]


def test_view_text_over_200_characters_is_refused() -> None:
    [problem] = refused(image_area(view={"kind": "capture", "id": "x" * 201}))
    assert problem.startswith("measurement.view.id must be text of 1 to 200 characters")


# --------------------------------------------------------------------------- original


def test_the_original_is_checked_computed_and_kept_beside_the_edit() -> None:
    draft = image_area(drawn_by="model:sam3@1", verified=False)
    edit = image_area(WIDER, drawn_by="engineer", verified=True, original=draft)
    t = measure(edit)
    assert t.quantity == D("880.00")
    assert t.original is not None and t.original.quantity == D("800.00")
    assert t.original.drawn_by == "model:sam3@1" and t.original.verified is False
    # Stored: the original's body as validated; recorded: with what it computes.
    assert t.stored["original"] == measure(draft).stored
    record = t.record()["original"]
    assert record["quantity"] == "800.00" and record["area_sf"] == "800.00"
    assert record["method"] == "image_scale" and record["drawn_by"] == "model:sam3@1"
    assert traced.formula(t) == (
        "880.00 SF traced by the engineer on kitchen photo (captured 2020-04-18), "
        "1 area of 4 corners, scale 0.2 ft per pixel; expected error up to 6.82 %; "
        "verified by the engineer; replaces 800.00 SF drafted by Basis"
    )


def test_an_original_may_be_on_another_picture_and_in_another_space() -> None:
    t = measure({**copy.deepcopy(AERIAL_LINE), "original": image_area()})
    assert t.space == "lonlat" and t.original is not None and t.original.space == "image"
    assert traced.formula(t).endswith("; replaces 800.00 SF traced by the engineer")


def test_an_original_does_not_change_the_quantity() -> None:
    assert (
        measure(image_area(WIDER, original=image_area())).quantity
        == measure(image_area(WIDER)).quantity
    )


def test_the_originals_problems_are_named_under_measurement_original() -> None:
    bad = image_area([[0, 0], [100, 100], [100, 0], [0, 100]], colour="red")  # a bow tie
    assert refused(image_area(original=bad)) == [
        "measurement.original has unknown keys: colour",
    ]
    del bad["colour"]
    assert refused(image_area(original=bad)) == [
        "measurement.original.shapes[0].ring crosses or touches itself (two of its edges meet)"
    ]
    assert refused(image_area(original="the AI draft")) == [
        "measurement.original must be an object (unit, space, shapes, image)"
    ]
    tiny = image_area([[0, 0], [0.001, 0], [0, 0.001]])
    [problem] = refused(image_area(original=tiny))
    assert problem.startswith("measurement.original comes to 0.0000 SF")


def test_an_original_holds_no_original_of_its_own() -> None:
    first = image_area(drawn_by="model:sam3@1")
    second = image_area(WIDER, original=first)
    assert refused(image_area(RECT, original=second)) == [
        "measurement.original.original cannot hold an original of its own: an edit keeps the "
        "first original only"
    ]


def test_problems_in_the_edit_and_in_its_original_are_all_reported() -> None:
    bad_original = image_area(verified="no")
    found = refused(image_area(drawn_by="someone", original=bad_original))
    assert found == [
        "measurement.drawn_by must be engineer or model:<name>@<version> (letters, digits and "
        ". _ - / +), got 'someone'",
        "measurement.original.verified must be true or false, got 'no'",
    ]


def test_the_request_schema_takes_an_original_with_cutouts_at_its_depth_limit() -> None:
    holed = {
        "kind": "area",
        "ring": [[0, 0], [100, 0], [100, 200], [0, 200]],
        "cutouts": [[[10, 10], [20, 10], [20, 20], [10, 20]]],
    }
    draft = {**image_area(), "shapes": [holed], "drawn_by": "model:sam3@1"}
    body = {**image_area(WIDER), "shapes": [copy.deepcopy(holed)], "original": draft}
    item = CorrectionIn.model_validate(
        {
            "entity_type": "asset",
            "entity_id": "5a360001-0000-4000-8000-000000000002",
            "before": None,
            "after": {"measurement": body},
        }
    )
    assert measure(item.after["measurement"]).original is not None
