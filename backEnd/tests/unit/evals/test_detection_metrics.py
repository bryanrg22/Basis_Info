"""Detection metrics against hand-computed values: VOC all-point AP, Open Images group-of
and verified-class rules, the door union, counts at a threshold, pass^k of counts."""

from __future__ import annotations

import math

import pytest

from evals.metrics import (
    CLASS_UNION,
    DetectionImage,
    GoldBox,
    PredictedBox,
    average_precision,
    count_pass_hat_k,
    detection_metrics,
    match_class,
    rect_ioa,
    rect_iou,
)


def gold(label: str, x1: float, y1: float, x2: float, y2: float, group: bool = False) -> GoldBox:
    return GoldBox(label, x1, y1, x2, y2, group)


def pred(label: str, score: float, x1: float, y1: float, x2: float, y2: float) -> PredictedBox:
    return PredictedBox(label, score, x1, y1, x2, y2)


def test_iou_and_ioa_by_hand() -> None:
    a = gold("w", 0.0, 0.0, 0.4, 0.4)
    b = pred("w", 1.0, 0.2, 0.0, 0.6, 0.4)
    assert rect_iou(a, b) == pytest.approx(0.08 / (0.16 + 0.16 - 0.08))
    assert rect_ioa(b, a) == pytest.approx(0.08 / 0.16)
    assert rect_iou(a, pred("w", 1, 0.5, 0.5, 0.6, 0.6)) == 0.0
    assert rect_ioa(pred("w", 1, 0.1, 0.1, 0.1, 0.1), a) == 0.0  # empty prediction


def test_voc_all_point_ap_by_hand() -> None:
    # TP, FP, FP, TP over 3 positives: precision 1, 1/2, 1/3, 1/2 at recall 1/3, 1/3, 1/3,
    # 2/3; envelope 1 up to recall 1/3 and 1/2 up to 2/3 -> 1/3 * 1 + 1/3 * 1/2 = 0.5.
    assert average_precision([(0.9, True), (0.8, False), (0.7, False), (0.6, True)], 3) == (
        pytest.approx(0.5)
    )
    assert average_precision([(0.6, True), (0.9, False)], 1) == pytest.approx(0.5)  # sorted
    assert average_precision([(0.9, True)], 1) == 1.0
    assert average_precision([], 2) == 0.0
    assert math.isnan(average_precision([(0.9, False)], 0))


G1 = gold("window", 0.0, 0.0, 0.2, 0.2)
G2 = gold("window", 0.5, 0.5, 0.7, 0.7)
G3 = gold("window", 0.1, 0.1, 0.3, 0.3)


def test_matching_takes_the_best_box_and_duplicates_are_false_positives() -> None:
    scored, positives = match_class(
        [G1, G2],
        [
            pred("window", 0.8, 0.0, 0.0, 0.2, 0.21),  # duplicate on G1 (ranked second)
            pred("window", 0.9, 0.0, 0.0, 0.2, 0.2),  # G1
            pred("window", 0.7, 0.8, 0.8, 0.9, 0.9),  # nothing
            pred("window", 0.6, 0.5, 0.5, 0.7, 0.68),  # G2 (IoU 0.9)
        ],
    )
    assert positives == 2
    assert scored == [(0.9, True), (0.8, False), (0.7, False), (0.6, True)]


def test_group_boxes_absorb_predictions_inside_and_are_never_misses() -> None:
    group = gold("window", 0.0, 0.5, 1.0, 1.0, group=True)
    scored, positives = match_class(
        [G1, group],
        [
            pred("window", 0.9, 0.1, 0.6, 0.2, 0.7),  # inside the group
            pred("window", 0.5, 0.3, 0.6, 0.4, 0.7),  # inside too: ignored
            pred("window", 0.4, 0.0, 0.0, 0.2, 0.2),  # G1
            pred("window", 0.3, 0.6, 0.0, 0.7, 0.1),  # outside both: FP
        ],
    )
    assert positives == 2  # G1 and the hit group box
    assert sorted(scored, reverse=True) == [(0.9, True), (0.4, True), (0.3, False)]
    _, unhit = match_class([G1, group], [])
    assert unhit == 1  # an unhit group box is not a positive


def test_detection_metrics_by_hand() -> None:
    images = [
        DetectionImage("a.jpg", (G1, G2), document="d1"),
        DetectionImage("b.jpg", (G3,), document="d2"),
        DetectionImage("c.jpg", (gold("toilet", 0.1, 0.1, 0.5, 0.5),), document="d2"),
    ]
    predictions = {
        "a.jpg": [
            pred("window", 0.9, 0.0, 0.0, 0.2, 0.2),
            pred("window", 0.8, 0.0, 0.0, 0.2, 0.21),
            pred("window", 0.7, 0.8, 0.8, 0.9, 0.9),
            pred("window", 0.4, 0.5, 0.5, 0.7, 0.7),
        ],
        "c.jpg": [pred("toilet", 0.95, 0.1, 0.1, 0.5, 0.5), pred("sink", 0.9, 0, 0, 1, 1)],
        "unknown.jpg": [pred("window", 0.9, 0, 0, 1, 1)],
    }
    metrics = detection_metrics(images, predictions, threshold=0.5)
    window, toilet = metrics.classes["window"], metrics.classes["toilet"]
    assert window.ap == pytest.approx(0.5) and window.positives == 3
    assert toilet.ap == 1.0 and set(metrics.classes) == {"toilet", "window"}  # sink ignored
    assert metrics.map == pytest.approx(0.75)
    # At 0.5: window predictions 0.9 (TP), 0.8 (FP), 0.7 (FP) -> precision 1/3, recall 1/3.
    assert (window.precision, window.recall) == pytest.approx((1 / 3, 1 / 3))
    # Counts at 0.5: (a, window) 2 gold / 3 predicted, (b, window) 1 / 0, (c, toilet) 1 / 1.
    assert [(p.file, p.label, p.gold, p.predicted) for p in metrics.pairs] == [
        ("c.jpg", "toilet", 1, 1),
        ("a.jpg", "window", 2, 3),
        ("b.jpg", "window", 1, 0),
    ]
    assert window.count_mae == 1.0 and window.count_bias == 0.0
    assert metrics.count_mae == pytest.approx(2 / 3) and metrics.count_bias == 0.0
    # Clustered by document: d1 = {a: +1}, d2 = {b: -1, c: 0}; mean 0, G = 2:
    # SE^2 = 2 * (1^2 + (-1)^2) / 3^2.
    assert metrics.count_bias_se == pytest.approx(math.sqrt(2 * 2 / 9))
    assert (metrics.precision, metrics.recall) == pytest.approx((2 / 4, 2 / 4))


def test_unverified_classes_and_group_counts_are_not_scored() -> None:
    images = [
        DetectionImage("a.jpg", (G1,), verified=frozenset({"window"})),
        DetectionImage("b.jpg", (gold("toilet", 0, 0, 0.5, 0.5),), verified=frozenset({"toilet"})),
        DetectionImage("c.jpg", (gold("window", 0, 0, 1, 1, group=True),)),
    ]
    predictions = {
        "b.jpg": [pred("window", 0.9, 0.5, 0.5, 0.9, 0.9)],  # window not verified in b
        "c.jpg": [pred("window", 0.9, 0.1, 0.1, 0.2, 0.2), pred("window", 0.8, 0.3, 0.3, 0.4, 0.4)],
    }
    metrics = detection_metrics(images, predictions)
    window = metrics.classes["window"]
    assert window.predictions == 1 and window.positives == 2 and window.ap == 0.5
    # c.jpg's window count is unknown (group box), b.jpg's window is unverified.
    assert [(p.file, p.label) for p in metrics.pairs] == [("b.jpg", "toilet"), ("a.jpg", "window")]


def test_door_is_scored_against_interior_and_exterior_predictions() -> None:
    assert CLASS_UNION["door"] == {"interior_door", "exterior_door"}
    door = gold("door", 0.0, 0.0, 0.3, 0.8)
    images = [DetectionImage("a.jpg", (door, gold("door", 0.6, 0.0, 0.9, 0.8)))]
    predictions = {
        "a.jpg": [
            pred("interior_door", 0.9, 0.0, 0.0, 0.3, 0.8),
            pred("exterior_door", 0.8, 0.6, 0.0, 0.9, 0.8),
            pred("exterior_door", 0.7, 0.0, 0.0, 0.3, 0.8),  # the same door twice: FP
            pred("garage_door", 0.9, 0.6, 0.0, 0.9, 0.8),  # not a member
        ]
    }
    metrics = detection_metrics(images, predictions)
    assert metrics.classes["door"].ap == 1.0
    assert metrics.classes["door"].precision == pytest.approx(2 / 3)
    assert [(p.gold, p.predicted) for p in metrics.pairs] == [(2, 3)]


def test_count_pass_hat_k() -> None:
    images = [
        DetectionImage("a.jpg", (G1,)),
        DetectionImage("b.jpg", ()),
        DetectionImage("c.jpg", (gold("toilet", 0, 0, 1, 1),)),
    ]
    right = {"a.jpg": [pred("window", 0.9, 0, 0, 0.2, 0.2)]}
    extra = {
        "a.jpg": [pred("window", 0.9, 0, 0, 0.2, 0.2)],
        "b.jpg": [pred("window", 0.9, 0, 0, 0.2, 0.2)],
    }
    # Pairs: (a, window) right in both; (b, window) right then wrong; (c, toilet) wrong
    # in both (predicted 0, gold 1). pass^2 = (1 + 0 + 0) / 3; pass^1 = (1 + 1/2 + 0) / 3.
    value, n = count_pass_hat_k(images, [right, extra], 0.5, 2)
    assert (value, n) == (pytest.approx(1 / 3), 3)
    assert count_pass_hat_k(images, [right, extra], 0.5, 1)[0] == pytest.approx(0.5)
    assert math.isnan(count_pass_hat_k([], [{}], 0.5, 1)[0])
