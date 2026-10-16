"""``basis.vision.fusion``: per-class count rules over several sources, fitted by error."""

from __future__ import annotations

import pytest

from basis.vision.fusion import Example, Rule, class_count, class_error, fit, fuse

VLM = "vlm"
SAM = "sam3@0.7"


def test_class_count_pools_doors() -> None:
    counts = {"interior_door": 2, "exterior_door": 1, "door": 1, "window": 3}
    assert class_count(counts, "door") == 4
    assert class_count(counts, "window") == 3
    assert class_count(counts, "electrical_outlet") == 0


@pytest.mark.parametrize(
    ("op", "sources", "expected"),
    [
        ("pick", (VLM,), 2),
        ("pick", (SAM,), 5),
        ("max", (VLM, SAM), 5),
        ("min", (VLM, SAM), 2),
        ("median", (VLM, SAM, "vlm2"), 3),
    ],
)
def test_rule_ops(op: str, sources: tuple[str, ...], expected: int) -> None:
    by_source = {VLM: {"window": 2}, SAM: {"window": 5}, "vlm2": {"window": 3}}
    assert Rule(op, sources).apply(by_source, "window") == expected  # type: ignore[arg-type]


def test_rule_validation() -> None:
    with pytest.raises(ValueError, match="unknown op"):
        Rule("mean", (VLM,))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one"):
        Rule("max", ())
    with pytest.raises(ValueError, match="exactly one"):
        Rule("pick", (VLM, SAM))
    with pytest.raises(ValueError, match="three or more"):
        Rule("median", (VLM, SAM))


def test_rule_names() -> None:
    assert Rule("pick", (VLM,)).name == "vlm"
    assert Rule("max", (VLM, SAM)).name == "max(vlm, sam3@0.7)"


def test_missing_source_is_an_error_not_a_zero() -> None:
    with pytest.raises(KeyError, match="sam3"):
        Rule("max", (VLM, SAM)).apply({VLM: {"window": 1}}, "window")


def test_fuse_replaces_only_the_ruled_classes() -> None:
    by_source = {
        VLM: {"window": 2, "interior_door": 1, "exterior_door": 1, "carpet": 1, "outlet": 1},
        SAM: {"window": 4, "interior_door": 3, "electrical_outlet": 2},
    }
    rules = {
        "window": Rule("max", (VLM, SAM)),
        "door": Rule("pick", (SAM,)),
        "electrical_outlet": Rule("pick", (VLM,)),
    }
    fused = fuse(by_source, rules, VLM)
    # window from the max, door pooled under "door", the VLM's other labels untouched
    assert fused == {"window": 4, "door": 3, "carpet": 1, "outlet": 1}
    # inputs are not modified
    assert by_source[VLM]["interior_door"] == 1


def test_fuse_keeps_the_base_door_split_when_the_rule_picks_the_base() -> None:
    by_source = {VLM: {"interior_door": 1, "exterior_door": 2}, SAM: {"interior_door": 9}}
    fused = fuse(by_source, {"door": Rule("pick", (VLM,))}, VLM)
    assert fused == {"interior_door": 1, "exterior_door": 2}


def test_fuse_drops_a_class_fused_to_zero() -> None:
    by_source = {VLM: {"window": 2}, SAM: {}}
    assert fuse(by_source, {"window": Rule("min", (VLM, SAM))}, VLM) == {}


def examples() -> list[Example]:
    # the VLM is right on windows and one short on outlets; SAM 3 is right on outlets and
    # over-counts windows
    return [
        Example(
            photo=f"p{i}",
            by_source={
                VLM: {"window": 2, "electrical_outlet": 1},
                SAM: {"window": 4, "electrical_outlet": 2},
            },
            truth={"window": 2, "electrical_outlet": 2},
        )
        for i in range(3)
    ]


def test_class_error_skips_unscored_classes() -> None:
    data = examples() + [Example("q", {VLM: {"door": 5}, SAM: {}}, {"window": 2})]
    total, n = class_error(Rule("pick", (VLM,)), data, "electrical_outlet")
    assert (total, n) == (3.0, 3)


def test_fit_picks_the_best_rule_per_class() -> None:
    candidates = [Rule("pick", (VLM,)), Rule("pick", (SAM,)), Rule("max", (VLM, SAM))]
    rules = fit(examples(), candidates, ["window", "electrical_outlet", "kitchen_cabinet"])
    assert rules["window"] == Rule("pick", (VLM,))
    # SAM and max tie on outlets (both exact): the earlier candidate wins
    assert rules["electrical_outlet"] == Rule("pick", (SAM,))
    # no example scores cabinets: the first candidate (the plain VLM)
    assert rules["kitchen_cabinet"] == Rule("pick", (VLM,))


def test_fit_needs_candidates() -> None:
    with pytest.raises(ValueError, match="no candidate"):
        fit(examples(), [], ["window"])
