"""``evals.room_grouping``: the scores, the free grouping rules, the clustering helpers and the
misleading-name swap, on synthetic photos (no embedding model is loaded, no paid call).

Seven synthetic photos named the engineer's way: a bathroom, two views of the kitchen (an
alias merges ``Kitchen 2`` into ``Kitchen``), two second-floor bedrooms the model cannot
tell apart (same type, floor and ordinal), and two views of the grounds (an alias merges
``Rear`` into ``Front``).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from basis.vision.schemas import PhotoAnalysis, RoomType
from evals import room_grouping as rg

FILES = sorted(
    [
        "1st Floor Bath.jpg",
        "1st Floor Kitchen.jpg",
        "1st Floor Kitchen 2.jpg",
        "2nd Floor Bed 2.jpg",
        "2nd Floor Bed 3.jpg",
        "Front.jpg",
        "Rear.jpg",
    ]
)
TRUTH_OF = {
    "1st Floor Bath.jpg": "1_bath",
    "1st Floor Kitchen.jpg": "1_kitchen",
    "1st Floor Kitchen 2.jpg": "1_kitchen",
    "2nd Floor Bed 2.jpg": "2_bed_2",
    "2nd Floor Bed 3.jpg": "2_bed_3",
    "Front.jpg": "x_front",
    "Rear.jpg": "x_front",
}
TRUTH = [TRUTH_OF[f] for f in FILES]
ALIASES = {"1_kitchen_2": "1_kitchen", "x_rear": "x_front"}


def analysis(room: RoomType, floor: int | None = None) -> PhotoAnalysis:
    return PhotoAnalysis(room_type=room, room_confidence=0.9, floor=floor)


ANALYSES: dict[str, PhotoAnalysis | None] = {
    "1st Floor Bath.jpg": analysis(RoomType.BATHROOM, 1),
    "1st Floor Kitchen.jpg": analysis(RoomType.KITCHEN, 1),
    "1st Floor Kitchen 2.jpg": analysis(RoomType.KITCHEN, 1),
    "2nd Floor Bed 2.jpg": analysis(RoomType.BEDROOM, 2),
    "2nd Floor Bed 3.jpg": analysis(RoomType.BEDROOM, 2),
    "Front.jpg": analysis(RoomType.EXTERIOR),
    "Rear.jpg": analysis(RoomType.EXTERIOR),
}


# --------------------------------------------------------------------------- scoring


def test_perfect_partition_scores_one() -> None:
    s = rg.score_partition(TRUTH, TRUTH)
    assert s["rooms"] == 5
    assert s["photos_right"] == 7
    assert s["rooms_exact"] == 5
    assert s["photos_unassigned"] == 0
    assert s["pairwise_f1"] == 1.0
    assert s["bcubed_f1"] == 1.0


def test_singletons_and_one_room_against_hand_computed_values() -> None:
    truth = ["a", "a", "b"]
    singles = rg.score_partition([0, 1, 2], truth)
    # No pair together: pairwise precision and recall 0. BCubed precision 1; recall per
    # photo 1/2, 1/2, 1 -> 2/3.
    assert singles["pairwise_f1"] == 0.0
    assert singles["bcubed_precision"] == 1.0
    assert singles["bcubed_recall"] == pytest.approx(2 / 3, abs=1e-4)
    assert singles["photos_right"] == 2  # one photo of a, the photo of b
    assert singles["rooms_exact"] == 1  # b only
    one = rg.score_partition([0, 0, 0], truth)
    # 3 pairs, 1 truly together: precision 1/3, recall 1. BCubed precision 2/3, 2/3, 1/3.
    assert one["pairwise_precision"] == pytest.approx(1 / 3, abs=1e-4)
    assert one["pairwise_recall"] == 1.0
    assert one["bcubed_precision"] == pytest.approx(5 / 9, abs=1e-4)
    assert one["photos_right"] == 2
    assert one["rooms_exact"] == 0


def test_a_photo_with_no_room_is_its_own_room_and_never_right() -> None:
    s = rg.score_partition([0, None, None], ["a", "a", "a"])
    assert s["photos_unassigned"] == 2
    assert s["rooms"] == 1
    assert s["photos_right"] == 1
    assert s["pairwise_recall"] == 0.0  # the two unplaced photos are not together
    assert rg.matched_photos([None, None], ["a", "b"]) == [False, False]


def test_matching_is_one_to_one() -> None:
    # Both formed rooms hold two photos of "a"; only one can be matched to it. Room 0 -> a
    # and room 1 -> b keeps 3 photos (the other way keeps 2).
    right = rg.matched_photos([0, 0, 1, 1, 1], ["a", "a", "a", "a", "b"])
    assert right == [True, True, False, False, True]


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="2 photos but 3 labels"):
        rg.score_partition([0, 1], ["a", "b", "c"])


# --------------------------------------------------------------------------- the free rules


def test_camera_names_are_sorted_and_numbered() -> None:
    names = rg.camera_names(["b.jpg", "a.jpg"])
    assert names == {"IMG_0001.JPG": "a.jpg", "IMG_0002.JPG": "b.jpg"}


def test_today_on_real_names_with_aliases_is_the_labelled_rooms() -> None:
    real = {f: f for f in FILES}
    assert rg.score_partition(rg.rooms_partition(FILES, ANALYSES, real, ALIASES), TRUTH) == (
        rg.score_partition(TRUTH, TRUTH)
    )
    assert rg.room_labels(FILES, ANALYSES, real, ALIASES) == TRUTH
    # Without the aliases every view is its own room: 7 rooms.
    assert rg.score_partition(rg.rooms_partition(FILES, ANALYSES, real), TRUTH)["rooms"] == 7
    regression = rg.regression_scores(FILES, ANALYSES, TRUTH, ALIASES)
    assert regression["names"]["rooms"] == 7
    assert regression["names_with_aliases"]["photos_right"] == 7


def test_today_on_camera_names_groups_by_the_model_identity() -> None:
    part = rg.rooms_partition(FILES, ANALYSES, rg.camera_names(FILES))
    s = rg.score_partition(part, TRUTH)
    # bathroom, kitchen (2), bedroom floor 2 (both bedrooms merged), exterior (2)
    assert s["rooms"] == 4
    assert s["photos_right"] == 6
    assert s["rooms_exact"] == 3


def test_ambiguous_and_failed_photos_have_no_room_under_today_and_ai_type() -> None:
    analyses = dict(ANALYSES)
    analyses["1st Floor Bath.jpg"] = analysis(RoomType.AMBIGUOUS)
    analyses["Rear.jpg"] = None
    part = rg.rooms_partition(FILES, analyses, rg.camera_names(FILES))
    assert rg.score_partition(part, TRUTH)["photos_unassigned"] == 2
    types = rg.ai_type_partition(FILES, analyses)
    assert types[FILES.index("1st Floor Bath.jpg")] is None
    assert types[FILES.index("Rear.jpg")] is None
    ext = rg.exterior_one_room_partition(FILES, analyses)
    assert ext[FILES.index("Rear.jpg")] is None  # failed: no room
    assert ext[FILES.index("1st Floor Bath.jpg")] is not None  # ambiguous: its own room


def test_exterior_rules() -> None:
    ext = rg.exterior_one_room_partition(FILES, ANALYSES)
    s = rg.score_partition(ext, TRUTH)
    assert s["rooms"] == 6  # grounds + 5 interior singletons
    assert s["photos_right"] == 6  # every photo but the second kitchen view
    merged = rg.exterior_merged([0, 1, 2], ["exterior", "kitchen", "exterior"])
    assert merged == ["exterior", 1, "exterior"]
    today = rg.rooms_partition(FILES, ANALYSES, rg.camera_names(FILES))
    groups = rg.ai_type_partition(FILES, ANALYSES)
    assert rg.score_partition(rg.exterior_merged(today, groups), TRUTH)["rooms"] == 4


def test_singletons() -> None:
    assert rg.singleton_partition(["a", "b", "c"]) == [0, 1, 2]


# --------------------------------------------------------------------------- clustering


def _two_clusters() -> tuple[np.ndarray, list[str]]:
    x = np.array(
        [[1.0, 0.0, 0.0], [0.99, 0.1, 0.0], [0.0, 1.0, 0.0], [0.1, 0.99, 0.0], [0.0, 0.0, 1.0]]
    )
    x = x / np.linalg.norm(x, axis=1, keepdims=True)
    return x, ["a", "a", "b", "b", "c"]


def test_best_threshold_recovers_clear_clusters_and_loo_agrees() -> None:
    x, truth = _two_clusters()
    d = rg.cosine_distance_matrix(x)
    assert np.allclose(np.diag(d), 0.0)
    assert np.allclose(d, d.T)
    for method in rg.LINKAGES:
        z = rg._linkage(d, method)
        cuts = rg.candidate_thresholds(z)
        assert cuts[0] == 0.0 and cuts[-1] > float(z[:, 2].max())
        t, s = rg.best_threshold(z, truth, "pairwise_f1")
        assert s["pairwise_f1"] == 1.0
        assert rg.score_partition(rg.cut(z, t), truth)["rooms_exact"] == 3
        loo = rg.loo_tuned(d, truth, method, "bcubed_f1")
        assert loo["photos_right_loo"] == 5
        assert loo["at_median"]["bcubed_f1"] == 1.0
    with pytest.raises(ValueError, match="objective"):
        rg.best_threshold(rg._linkage(d, "single"), truth, "accuracy")


def test_block_matrix_never_merges_across_groups() -> None:
    x, truth = _two_clusters()
    d = rg.block_matrix(rg.cosine_distance_matrix(x), ["k", "k", "k", "k", "k"])
    assert np.array_equal(d, rg.cosine_distance_matrix(x))
    blocked = rg.block_matrix(rg.cosine_distance_matrix(x), ["k", "j", "k", "k", "k"])
    assert blocked[0, 1] == 2.0 and blocked[1, 0] == 2.0
    z = rg._linkage(blocked, "single")
    part = rg.cut(z, 1.5)
    assert part[0] != part[1]


def test_force_together_joins_at_every_cut() -> None:
    x, _ = _two_clusters()
    d = rg.force_together(rg.cosine_distance_matrix(x), [True, False, True, False, False])
    assert d[0, 2] == 0.0 and d[2, 0] == 0.0
    assert np.allclose(np.diag(d), 0.0)
    part = rg.cut(rg._linkage(d, "average"), 0.0)
    assert part[0] == part[2]
    assert len(set(part)) == 4


def test_nearest_mates_counts_shared_rooms_only() -> None:
    x, truth = _two_clusters()
    d = rg.cosine_distance_matrix(x)
    assert rg.nearest_mates(d, truth) == {"photos_in_shared_rooms": 4, "nearest_is_room_mate": 4}
    masked = rg.nearest_mates(d, truth, [True, True, False, False, True])
    assert masked == {"photos_in_shared_rooms": 2, "nearest_is_room_mate": 2}
    # Swap the labels of photos 1 and 2: their nearest photos are now in another room.
    crossed = rg.nearest_mates(d, ["a", "b", "a", "b", "c"])
    assert crossed["nearest_is_room_mate"] == 0


def test_exif_capture_times(tmp_path: Path) -> None:
    plain = tmp_path / "plain.jpg"
    Image.new("RGB", (8, 8)).save(plain)
    stamped = tmp_path / "stamped.jpg"
    exif = Image.Exif()
    exif.get_ifd(rg.EXIF_IFD)[rg.EXIF_DATETIME_ORIGINAL] = "2025:01:02 03:04:05"
    Image.new("RGB", (8, 8)).save(stamped, exif=exif)
    fallback = tmp_path / "fallback.jpg"
    exif2 = Image.Exif()
    exif2[rg.EXIF_DATETIME] = "2025:06:07 08:09:10"
    Image.new("RGB", (8, 8)).save(fallback, exif=exif2)
    got = rg.exif_capture_times([plain, stamped, fallback])
    assert got == {
        "plain.jpg": None,
        "stamped.jpg": "2025:01:02 03:04:05",
        "fallback.jpg": "2025:06:07 08:09:10",
    }


# --------------------------------------------------------------------------- misleading names


def test_swap_pairs_are_seeded_disjoint_and_cross_room() -> None:
    a = rg.swap_pairs(FILES, TRUTH_OF, 3, seed=7)
    assert a == rg.swap_pairs(FILES, TRUTH_OF, 3, seed=7)
    used = [f for pair in a for f in pair]
    assert len(used) == len(set(used)) == 6
    assert all(TRUTH_OF[x] != TRUTH_OF[y] for x, y in a)
    # A smaller draw with the same seed is the start of the larger one.
    assert rg.swap_pairs(FILES, TRUTH_OF, 2, seed=7) == a[:2]
    with pytest.raises(ValueError, match="only"):
        rg.swap_pairs(FILES, TRUTH_OF, 4, seed=7)


def test_same_type_swap_pairs_names_of_one_type() -> None:
    pairs = rg.swap_pairs(FILES, TRUTH_OF, 1, seed=0, same_type=True)
    assert {frozenset(p) for p in pairs} == {
        frozenset({"2nd Floor Bed 2.jpg", "2nd Floor Bed 3.jpg"})
    }
    with pytest.raises(ValueError, match="only 1 pairs"):
        rg.swap_pairs(FILES, TRUTH_OF, 2, seed=0, same_type=True)


def test_swapped_names_exchange_each_pair() -> None:
    names = rg.swapped_names(FILES, [("1st Floor Bath.jpg", "Front.jpg")])
    assert names["Front.jpg"] == "1st Floor Bath.jpg"  # the bath photo now named Front
    assert names["1st Floor Bath.jpg"] == "Front.jpg"
    assert names["Rear.jpg"] == "Rear.jpg"
    assert sorted(names.values()) == FILES


def test_name_type_flag() -> None:
    bath = analysis(RoomType.BATHROOM)
    assert rg.name_type_flag("1st Floor Kitchen.jpg", bath)
    assert not rg.name_type_flag("1st Floor Bath.jpg", bath)
    assert not rg.name_type_flag("Front.jpg", bath)  # the name says no type
    assert not rg.name_type_flag("IMG_0001.JPG", bath)
    assert not rg.name_type_flag("1st Floor Kitchen.jpg", analysis(RoomType.AMBIGUOUS))
    assert not rg.name_type_flag("1st Floor Kitchen.jpg", None)


def test_no_swap_is_the_regression() -> None:
    s = rg.misleading_scores(FILES, ANALYSES, TRUTH, ALIASES, [])
    assert s["photos_in_named_room"] == 7
    assert s["photos_right"] == 7
    assert s["swapped_wrong"] == s["caught"] == s["false_alarms"] == 0


def test_a_type_swap_is_wrong_and_caught() -> None:
    s = rg.misleading_scores(
        FILES, ANALYSES, TRUTH, ALIASES, [("1st Floor Bath.jpg", "1st Floor Kitchen.jpg")]
    )
    assert s["swapped_photos"] == 2
    assert s["swapped_wrong"] == 2
    assert s["unswapped_wrong"] == 0
    assert s["photos_in_named_room"] == 5
    # The one-to-one matching still calls the two swapped photos right (each formed room
    # holds one photo of the other room): why the named-room count is reported too.
    assert s["photos_right"] == 6
    assert s["caught"] == 2
    assert s["false_alarms"] == 0
    assert s["swapped_typed_names"] == 2
    assert s["pairs_catchable_by_type"] == 1


def test_a_swap_onto_an_untyped_name_is_caught_once() -> None:
    s = rg.misleading_scores(FILES, ANALYSES, TRUTH, ALIASES, [("1st Floor Bath.jpg", "Front.jpg")])
    assert s["swapped_wrong"] == 2
    assert s["caught"] == 1  # the grounds photo named Bath; the bath photo named Front says no type
    assert s["swapped_typed_names"] == 1
    assert s["pairs_catchable_by_type"] == 0


def test_a_same_type_swap_is_wrong_and_not_caught() -> None:
    s = rg.misleading_scores(
        FILES, ANALYSES, TRUTH, ALIASES, [("2nd Floor Bed 2.jpg", "2nd Floor Bed 3.jpg")]
    )
    assert s["swapped_wrong"] == 2
    assert s["photos_in_named_room"] == 5
    # The room sets are unchanged (two one-photo rooms exchanged), so only the named-room
    # count sees the error.
    assert s["photos_right"] == 7
    assert s["caught"] == 0
    assert s["swapped_typed_names"] == 2


def test_a_wrong_model_type_is_a_false_alarm() -> None:
    analyses = dict(ANALYSES)
    analyses["2nd Floor Bed 2.jpg"] = analysis(RoomType.LIVING_ROOM, 2)
    s = rg.misleading_scores(FILES, analyses, TRUTH, ALIASES, [])
    assert s["false_alarms"] == 1
    assert s["photos_in_named_room"] == 7  # the name still files it right


# --------------------------------------------------------------------------- the run


def _write_package(root: Path) -> tuple[Path, Path, Path]:
    labels = root / "labels"
    labels.mkdir()
    rooms: dict[str, list[str]] = {}
    for f, r in TRUTH_OF.items():
        rooms.setdefault(r, []).append(f)
    room_type = {
        "1_bath": "bathroom",
        "1_kitchen": "kitchen",
        "2_bed_2": "bedroom",
        "2_bed_3": "bedroom",
        "x_front": "exterior",
    }
    (labels / "room_inventory_v1.json").write_text(
        json.dumps(
            {
                "rooms": [
                    {"room_id": r, "photos": sorted(p), "room_type": room_type[r]}
                    for r, p in rooms.items()
                ]
            }
        )
    )
    (labels / "photo_rooms_v1.json").write_text(
        json.dumps({"items": [{"file": f, "room_type": "x"} for f in FILES]})
    )
    (labels / "room_aliases_v1.json").write_text(json.dumps(ALIASES))
    photos = root / "photos"
    photos.mkdir()
    for f in FILES:
        Image.new("RGB", (8, 8)).save(photos / f)
    per_photo = root / "stored_run"
    per_photo.mkdir()
    lines = [
        json.dumps({"file": f, "repeat": 1, "analysis": a.model_dump(mode="json") if a else None})
        for f, a in ANALYSES.items()
    ]
    (per_photo / "photo_raw.jsonl").write_text("\n".join(lines) + "\n")
    return labels, photos, per_photo


def test_main_rules_only_writes_the_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASIS_EVAL_DATA_DIR", str(tmp_path))
    labels, photos, per_photo = _write_package(tmp_path)
    out = tmp_path / "out"
    code = rg.main(
        [
            "--per-photo",
            str(per_photo),
            "--photos",
            str(photos),
            "--labels",
            str(labels),
            "--out",
            str(out),
            "--models",
            "--swap-pairs",
            "1",
            "--same-type-pairs",
            "1",
        ]
    )
    assert code == 0
    summary = json.loads((out / "run.json").read_text())
    assert summary["cost_usd"] == 0.0
    assert summary["exif"]["photos_with_capture_time"] == 0
    assert summary["exif"]["status"].startswith("dropped")
    camera = summary["camera"]["stored_run"]["1"]
    assert set(camera) == {
        "today",
        "ai_type",
        "singletons",
        "exterior_one_room",
        "today_exterior_one_room",
    }
    assert camera["today"]["rooms"] == 4
    assert summary["regression"]["stored_run"]["1"]["names_with_aliases"]["photos_right"] == 7
    misleading = summary["misleading"]["stored_run"]["1"]
    assert set(misleading) == {"0", "1", "1_same_type"}
    assert misleading["1_same_type"]["caught"] == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["calls"]["count"] == 0
    assert manifest["tree"]["component"] == "room_grouping"
    partitions = json.loads((out / "partitions.json").read_text())
    assert partitions["files"] == FILES


def test_main_refuses_photos_that_do_not_match_the_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BASIS_EVAL_DATA_DIR", str(tmp_path))
    labels, photos, per_photo = _write_package(tmp_path)
    (photos / "Rear.jpg").unlink()
    with pytest.raises(SystemExit, match="different files"):
        rg.main(
            [
                "--per-photo",
                str(per_photo),
                "--photos",
                str(photos),
                "--labels",
                str(labels),
                "--out",
                str(tmp_path / "out"),
                "--models",
            ]
        )
