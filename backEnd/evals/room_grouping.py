"""``python -m evals.room_grouping``: sorting photos into rooms when the file names are useless
(``IMG_1234.JPG``) or misleading (a file named "kitchen" that shows a bathroom), scored on the
labelled rooms without any paid call.

Production (:func:`basis.vision.rooms.initial_rooms`) groups by the room the file name says,
after the study's aliases, and falls back to the per-photo model's room identity (type,
floor, ordinal) only when the name says nothing (EVAL_REPORT sections 27.1 and 48.4). This
harness takes the same 52 photos and the same stored per-photo answers (``--per-photo``, a
run's ``photo_raw.jsonl``) and measures, for the **camera-name** case (every name
``IMG_<n>.JPG``), the free alternatives:

* ``today``: the model's room identity (what production does);
* ``ai_type``: the model's room type alone (its floor and ordinal ignored);
* ``singletons``: every photo its own room (the engineer merges at the Rooms review);
* ``exterior_one_room``: the photos the model calls exterior are one grounds room, every
  other photo its own room (the engineer's own convention in the labels: 10 of the 16
  aliases merge exterior views into one room);
* ``today_exterior_one_room``: today's rule with the photos the model calls exterior merged
  into one grounds room (the interior photos still grouped by the model's identity);
* ``<model>/<linkage>``: agglomerative clustering (single or average linkage, cosine
  distance) of image embeddings from a model that runs locally (``dinov2``:
  ``facebook/dinov2-small`` CLS token; ``clip``: ``openai/clip-vit-base-patch32`` image
  embedding), scored at the best cut of the dendrogram (``oracle``, tuned on the labels
  themselves, an upper bound) and with the threshold tuned leave-one-out (``loo``: for
  each photo the threshold is chosen on the other 51, then the photo's own placement is
  scored), each by two objectives (pairwise F1, which the one 12-photo room dominates, and
  BCubed F1, which weighs every photo alike);
* ``<model>/<linkage>/within_ai_type``: the same clustering with photos of different model
  room types never merged;
* ``<model>/<linkage>/exterior_one_room_within_ai_type``: the same, with the photos the
  model calls exterior forced into one room (their distances set to 0), so the threshold
  only decides which interior views of one type merge;
* ``exif``: capture-time gaps, when the photos carry EXIF capture times (the package's
  photos carry none: the candidate is reported as dropped, not scored).

and, for the **misleading-name** case, the real names with the labels' aliases after the
names of ``--swap-pairs`` photo pairs from different rooms are exchanged (seed
``--seed``), today's rule: how many photos land in the wrong room, and whether a free check
of the name's room type (:func:`basis.vision.rooms.name_room_type`) against the model's
room type would flag them (and how many right photos it would flag too). The harder case,
``--same-type-pairs`` pairs whose names say the same room type (bedroom 2 named bedroom 3),
is run the same way: the check cannot see those by construction, and the run shows it.

The **regression** block replays today's rule on the real names, without and with the
labels' aliases, for comparison with EVAL_REPORT 48.4's "after" rows (49 rooms and 33 rooms,
no photo without a room); the camera-name ``today`` rows compare with 48.4's camera rows.
A **nearest-neighbour** diagnostic per embedding model says, for every photo whose
labelled room has other photos, whether its nearest photo by cosine distance is one of
them (all such photos, and those of rooms the labels do not type exterior).

Scores, against ``room_inventory_v1.json`` (33 rooms, every one of the 52 photos in exactly
one): rooms formed; photos in the right room (each formed room matched one-to-one to a
labelled room by the Hungarian assignment maximising the photos in common, the standard
"clustering accuracy"); rooms exactly right (a matched room with exactly its labelled
photos); photos with no room; pairwise precision, recall and F1 over the 1,326 photo pairs
("same room" against "same labelled room"); BCubed precision, recall and F1 (Amigó et al.
2009: per photo, the share of its room that belongs with it and the share of its labelled
room it was put with, averaged over photos). A photo with no room counts as a room of its
own in the pair and BCubed scores. Every candidate is free: cost $0, no tokens.

The run leaves the standard record (:mod:`evals.record`: ``run.json`` with every score,
``partitions.json`` with each candidate's rooms, ``manifest.json``, ``results.json``, one
case per scenario, stored run and repeat) filed under ``room_grouping/v1`` in the
verification tree. Usage, from ``backEnd/``::

    python -m evals.room_grouping --per-photo $BASIS_EVAL_DATA_DIR/predictions/<run> \\
        --photos $BASIS_EVAL_DATA_DIR/inputs/photos --out $BASIS_EVAL_DATA_DIR/predictions/rg
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from basis.vision.rooms import PhotoInput, initial_rooms, name_room_type, room_name
from basis.vision.schemas import PhotoAnalysis, RoomType
from evals.record import PASSED, EvalRun, finish_pending
from evals.room_metadata import load_answers, load_labels

COMPONENT: Final = "room_grouping"
VERSION: Final = "v1"
EMBEDDING_MODELS: Final[Mapping[str, str]] = {
    "dinov2": "facebook/dinov2-small",
    "clip": "openai/clip-vit-base-patch32",
}
LINKAGES: Final = ("single", "average")
OBJECTIVES: Final = ("pairwise_f1", "bcubed_f1")
PHOTO_SUFFIXES: Final = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif"})
EXIF_DATETIME_ORIGINAL: Final = 36867
EXIF_DATETIME: Final = 306
EXIF_IFD: Final = 0x8769
Partition = list[object | None]  # one room id per photo in file order; None = no room
Vector = npt.NDArray[np.float64]


# --------------------------------------------------------------------------- scoring


def matched_photos(pred: Sequence[object | None], truth: Sequence[str]) -> list[bool]:
    """Per photo, whether its formed room is the labelled room the Hungarian assignment
    matches it to (rooms and labelled rooms matched one-to-one, maximising the photos in
    common). A photo with no room is never right."""
    from scipy.optimize import linear_sum_assignment

    cluster_ids = list(dict.fromkeys(p for p in pred if p is not None))
    room_ids = list(dict.fromkeys(truth))
    if not cluster_ids:
        return [False] * len(pred)
    ci = {c: k for k, c in enumerate(cluster_ids)}
    ri = {r: k for k, r in enumerate(room_ids)}
    m = np.zeros((len(cluster_ids), len(room_ids)), dtype=int)
    for p, t in zip(pred, truth, strict=True):
        if p is not None:
            m[ci[p], ri[t]] += 1
    rows, cols = linear_sum_assignment(m, maximize=True)
    match = {cluster_ids[r]: room_ids[c] for r, c in zip(rows, cols, strict=True)}
    return [p is not None and match.get(p) == t for p, t in zip(pred, truth, strict=True)]


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def score_partition(pred: Sequence[object | None], truth: Sequence[str]) -> dict[str, Any]:
    """The scores of one partition (module doc) against the labelled rooms."""
    n = len(pred)
    if len(truth) != n:
        raise ValueError(f"{n} photos but {len(truth)} labels")
    own: list[object] = [p if p is not None else ("__none__", i) for i, p in enumerate(pred)]
    same_room = np.array([[a == b for b in truth] for a in truth])
    same_pred = np.array([[a == b for b in own] for a in own])
    upper = np.triu_indices(n, 1)
    tp = int((same_pred & same_room)[upper].sum())
    fp = int((same_pred & ~same_room)[upper].sum())
    fn = int((~same_pred & same_room)[upper].sum())
    pair_p = tp / (tp + fp) if tp + fp else 0.0
    pair_r = tp / (tp + fn) if tp + fn else 0.0
    both = same_pred & same_room
    bc_p = float(np.mean(both.sum(1) / same_pred.sum(1)))
    bc_r = float(np.mean(both.sum(1) / same_room.sum(1)))
    right = matched_photos(pred, truth)
    room_size = {r: truth.count(r) for r in set(truth)}
    cluster_photos: dict[object, list[int]] = {}
    for i, p in enumerate(pred):
        if p is not None:
            cluster_photos.setdefault(p, []).append(i)
    exact = 0
    for members in cluster_photos.values():
        if all(right[i] for i in members) and len(members) == room_size[truth[members[0]]]:
            exact += 1
    return {
        "rooms": len(cluster_photos),
        "photos_right": sum(right),
        "rooms_exact": exact,
        "photos_unassigned": sum(p is None for p in pred),
        "pairwise_precision": round(pair_p, 4),
        "pairwise_recall": round(pair_r, 4),
        "pairwise_f1": round(_f1(pair_p, pair_r), 4),
        "bcubed_precision": round(bc_p, 4),
        "bcubed_recall": round(bc_r, 4),
        "bcubed_f1": round(_f1(bc_p, bc_r), 4),
    }


# --------------------------------------------------------------------------- the free rules


def camera_names(files: Sequence[str]) -> dict[str, str]:
    """``{camera name: original}``, the renaming ``evals.room_metadata``'s camera scenario
    uses (``IMG_0001.JPG`` ... in sorted file order)."""
    return {f"IMG_{i + 1:04d}.JPG": f for i, f in enumerate(sorted(files))}


def rooms_partition(
    files: Sequence[str],
    analyses: Mapping[str, PhotoAnalysis | None],
    names: Mapping[str, str],
    aliases: Mapping[str, str] | None = None,
) -> Partition:
    """Today's rule, :func:`initial_rooms`, on the photos named by ``names``
    (``{name used: original file}``): each original file's room index, ``None`` for a photo
    left in the Unassigned room or without an analysis."""
    placed = _rooms_of(analyses, names, aliases)
    return [placed.get(f, (None, None))[0] for f in files]


def room_labels(
    files: Sequence[str],
    analyses: Mapping[str, PhotoAnalysis | None],
    names: Mapping[str, str],
    aliases: Mapping[str, str] | None = None,
) -> list[str | None]:
    """Today's rule as the Rooms review labels it: each original file's room key (a
    file-name room's canonical id, which the labels' room ids are) or an identity room's
    name; ``None`` for a photo without a room. Unlike :func:`rooms_partition` this sees a
    photo filed under another room's name even when the room sets are unchanged (two
    single-photo rooms whose names were exchanged)."""
    placed = _rooms_of(analyses, names, aliases)
    return [placed.get(f, (None, None))[1] for f in files]


def _rooms_of(
    analyses: Mapping[str, PhotoAnalysis | None],
    names: Mapping[str, str],
    aliases: Mapping[str, str] | None,
) -> dict[str, tuple[int, str]]:
    back = dict(names)
    inputs = [PhotoInput(file=name, analysis=analyses[original]) for name, original in back.items()]
    placed: dict[str, tuple[int, str]] = {}
    for index, room in enumerate(initial_rooms(inputs, aliases or {})):
        if room.unassigned:
            continue
        for photo in room.photos:
            placed[back[photo]] = (index, room.key if room.key is not None else room.name)
    return placed


def ai_type_partition(
    files: Sequence[str], analyses: Mapping[str, PhotoAnalysis | None]
) -> Partition:
    """The model's room type alone; ambiguous or failed photos have no room."""
    out: Partition = []
    for f in files:
        a = analyses.get(f)
        out.append(None if a is None or a.room_type is RoomType.AMBIGUOUS else a.room_type.value)
    return out


def singleton_partition(files: Sequence[str]) -> Partition:
    return list(range(len(files)))


def exterior_one_room_partition(
    files: Sequence[str], analyses: Mapping[str, PhotoAnalysis | None]
) -> Partition:
    """One grounds room for the photos the model calls exterior, every other analyzed photo
    its own room."""
    out: Partition = []
    for i, f in enumerate(files):
        a = analyses.get(f)
        if a is None:
            out.append(None)
        else:
            out.append("exterior" if a.room_type is RoomType.EXTERIOR else i)
    return out


def exterior_merged(part: Sequence[object | None], groups: Sequence[object | None]) -> Partition:
    """``part`` with every photo whose ``groups`` entry is ``exterior`` moved into one room
    (the other photos keep their rooms)."""
    return [
        "exterior" if g == RoomType.EXTERIOR.value else p for p, g in zip(part, groups, strict=True)
    ]


# --------------------------------------------------------------------------- embeddings


def _transformers() -> Any:
    import importlib

    return importlib.import_module("transformers")


def embed_photos(model: str, paths: Sequence[Path]) -> tuple[Vector, dict[str, Any]]:
    """L2-normalised embeddings of the photos (one row per path, in order) from a model that
    runs locally on the CPU, and ``{checkpoint, dim, load_seconds, embed_seconds}``. The
    model's weights come from the Hugging Face cache (downloaded once; not a paid call)."""
    import torch
    from PIL import Image

    checkpoint = EMBEDDING_MODELS[model]
    hf = _transformers()
    t0 = time.perf_counter()
    if model == "dinov2":
        processor = hf.AutoImageProcessor.from_pretrained(checkpoint)
        net = hf.AutoModel.from_pretrained(checkpoint).eval()
    else:
        processor = hf.CLIPImageProcessor.from_pretrained(checkpoint)
        net = hf.CLIPModel.from_pretrained(checkpoint).eval()
    loaded = time.perf_counter()
    rows: list[Vector] = []
    with torch.no_grad():
        for start in range(0, len(paths), 8):
            images = [Image.open(p).convert("RGB") for p in paths[start : start + 8]]
            inputs = processor(images=images, return_tensors="pt")
            if model == "dinov2":
                out = net(**inputs).last_hidden_state[:, 0]
            else:
                got = net.get_image_features(**inputs)
                out = got if isinstance(got, torch.Tensor) else got.pooler_output
            rows.append(out.to(torch.float64).numpy())
    x = np.concatenate(rows)
    x = x / np.linalg.norm(x, axis=1, keepdims=True)
    done = time.perf_counter()
    return x, {
        "checkpoint": checkpoint,
        "dim": int(x.shape[1]),
        "load_seconds": round(loaded - t0, 3),
        "embed_seconds": round(done - loaded, 3),
    }


def cosine_distance_matrix(x: Vector) -> Vector:
    """Square cosine distances of L2-normalised rows (zero diagonal)."""
    d = 1.0 - x @ x.T
    np.fill_diagonal(d, 0.0)
    return np.clip(d, 0.0, 2.0)


def block_matrix(d: Vector, groups: Sequence[object]) -> Vector:
    """``d`` with the distance between photos of different ``groups`` set to 2 (the cosine
    maximum), so no cut below 2 merges across groups."""
    g = np.array([str(v) for v in groups])
    out = d.copy()
    out[g[:, None] != g[None, :]] = 2.0
    return out


def force_together(d: Vector, members: Sequence[bool]) -> Vector:
    """``d`` with the distance between every two ``members`` set to 0, so every cut joins
    them (single linkage merges them first; average linkage then treats them as one room)."""
    m = np.asarray(members, dtype=bool)
    out = d.copy()
    out[np.ix_(m, m)] = 0.0
    np.fill_diagonal(out, 0.0)
    return out


def nearest_mates(
    d: Vector, truth: Sequence[str], mask: Sequence[bool] | None = None
) -> dict[str, int]:
    """For the photos whose labelled room has other photos (and ``mask`` is true): how
    many there are, and how many have one of their room-mates as their nearest photo
    (ties: the first in file order)."""
    n = len(truth)
    keep = [True] * n if mask is None else list(mask)
    size = {r: truth.count(r) for r in set(truth)}
    far = d.copy()
    np.fill_diagonal(far, np.inf)
    shared = hits = 0
    for i in range(n):
        if not keep[i] or size[truth[i]] < 2:
            continue
        shared += 1
        hits += truth[int(np.argmin(far[i]))] == truth[i]
    return {"photos_in_shared_rooms": shared, "nearest_is_room_mate": hits}


def _linkage(d: Vector, method: str) -> Any:
    from scipy.cluster.hierarchy import linkage
    from scipy.spatial.distance import squareform

    return linkage(squareform(d, checks=False), method=method)


def cut(z: Any, threshold: float) -> Partition:
    from scipy.cluster.hierarchy import fcluster

    return [int(c) for c in fcluster(z, t=threshold, criterion="distance")]


def candidate_thresholds(z: Any) -> list[float]:
    """Every distinct cut of a dendrogram: 0 (all apart), the midpoints between successive
    merge heights, and above the last merge (all together)."""
    heights = sorted({float(h) for h in z[:, 2]})
    cuts = [0.0]
    cuts += [(a + b) / 2 for a, b in zip(heights, heights[1:], strict=False)]
    cuts.append(heights[-1] + 1.0 if heights else 1.0)
    return cuts


def best_threshold(
    z: Any, truth: Sequence[str], objective: str = "pairwise_f1"
) -> tuple[float, dict[str, Any]]:
    """The cut with the highest ``objective`` score (``pairwise_f1`` or ``bcubed_f1``; ties:
    the lowest threshold, fewer merges)."""
    if objective not in OBJECTIVES:
        raise ValueError(f"objective {objective!r} is not one of {OBJECTIVES}")
    best: tuple[float, dict[str, Any]] | None = None
    for t in candidate_thresholds(z):
        s = score_partition(cut(z, t), truth)
        if best is None or s[objective] > best[1][objective]:
            best = (t, s)
    assert best is not None
    return best


def loo_tuned(
    d: Vector, truth: Sequence[str], method: str, objective: str = "pairwise_f1"
) -> dict[str, Any]:
    """Leave-one-out: for each photo, the threshold with the best ``objective`` on the other
    photos is chosen, the whole set is cut at it, and whether that photo is in the right
    room is recorded. Returns the photos right that way, the thresholds chosen, and the
    scores of the whole set cut at their median."""
    n = len(truth)
    z_all = _linkage(d, method)
    right = 0
    thresholds: list[float] = []
    for i in range(n):
        keep = [j for j in range(n) if j != i]
        z = _linkage(d[np.ix_(keep, keep)], method)
        t, _ = best_threshold(z, [truth[j] for j in keep], objective)
        thresholds.append(t)
        right += matched_photos(cut(z_all, t), truth)[i]
    median = float(statistics.median(thresholds))
    return {
        "photos_right_loo": right,
        "threshold_median": round(median, 4),
        "threshold_min": round(min(thresholds), 4),
        "threshold_max": round(max(thresholds), 4),
        "at_median": score_partition(cut(z_all, median), truth),
        "partition_at_median": cut(z_all, median),
    }


def exif_capture_times(paths: Sequence[Path]) -> dict[str, str | None]:
    """Each photo's EXIF ``DateTimeOriginal`` (else ``DateTime``) as the file carries it,
    ``None`` when the file has none."""
    from PIL import Image

    out: dict[str, str | None] = {}
    for p in paths:
        with Image.open(p) as image:
            exif = image.getexif()
            value = exif.get_ifd(EXIF_IFD).get(EXIF_DATETIME_ORIGINAL) if exif else None
            if value is None and exif:
                value = exif.get(EXIF_DATETIME)
        out[p.name] = None if value is None else str(value)
    return out


# --------------------------------------------------------------------------- misleading names


def swap_pairs(
    files: Sequence[str],
    truth: Mapping[str, str],
    pairs: int,
    seed: int,
    *,
    same_type: bool = False,
) -> list[tuple[str, str]]:
    """``pairs`` disjoint photo pairs from different labelled rooms, chosen with ``seed``
    (a fixed seed gives the same pairs on the same files). With ``same_type`` both names
    must say the same room type (:func:`name_room_type`), the swap a type check cannot
    see."""
    rng = random.Random(seed)
    pool = sorted(files)
    rng.shuffle(pool)
    chosen: list[tuple[str, str]] = []
    used: set[str] = set()

    def named_type(f: str) -> RoomType | None:
        return name_room_type(room_name(f))

    for a in pool:
        if a in used or len(chosen) == pairs:
            continue
        if same_type and named_type(a) is None:
            continue
        for b in pool:
            if b in used or b == a or truth[a] == truth[b]:
                continue
            if same_type and named_type(b) is not named_type(a):
                continue
            chosen.append((a, b))
            used.update((a, b))
            break
    if len(chosen) < pairs:
        raise ValueError(f"only {len(chosen)} pairs from different rooms among {len(files)}")
    return chosen


def swapped_names(files: Sequence[str], pairs: Sequence[tuple[str, str]]) -> dict[str, str]:
    """``{name used: original file}`` with each pair's names exchanged."""
    names = {f: f for f in files}
    for a, b in pairs:
        names[a], names[b] = b, a
    return {name: original for original, name in names.items()}


def name_type_flag(filename: str, analysis: PhotoAnalysis | None) -> bool:
    """Whether the name's room type and the model's room type contradict each other: the
    name says a type, the model answered another (``ambiguous`` is not a contradiction)."""
    named = name_room_type(room_name(filename))
    if named is None or analysis is None or analysis.room_type is RoomType.AMBIGUOUS:
        return False
    return analysis.room_type is not named


def misleading_scores(
    files: Sequence[str],
    analyses: Mapping[str, PhotoAnalysis | None],
    truth: Sequence[str],
    aliases: Mapping[str, str],
    pairs: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    """Today's rule on the real names with ``pairs`` exchanged: the partition scores, the
    photos in the room the labels name for them (``photos_in_named_room``: the room's key
    against the photo's labelled room id, which sees a photo filed under another room's
    name), how many swapped and unswapped photos are in a wrong named room, how many of the
    swapped the name-type check flags (``caught``), how many right photos it flags too
    (``false_alarms``), how many swapped photos' new names say a room type at all
    (``swapped_typed_names``: the most the check can catch), and how many pairs the check
    could catch on both photos (the two names say different room types)."""
    names = swapped_names(files, pairs)
    by_original = {original: name for name, original in names.items()}
    pred = rooms_partition(files, analyses, names, aliases)
    labels = room_labels(files, analyses, names, aliases)
    right = [label == t for label, t in zip(labels, truth, strict=True)]
    swapped = {f for pair in pairs for f in pair}
    flagged = {f for f in files if name_type_flag(by_original[f], analyses.get(f))}
    catchable = sum(
        1
        for a, b in pairs
        if name_room_type(room_name(a)) != name_room_type(room_name(b))
        and None not in (name_room_type(room_name(a)), name_room_type(room_name(b)))
    )
    return {
        **score_partition(pred, truth),
        "photos_in_named_room": sum(right),
        "pairs": len(pairs),
        "swapped_photos": len(swapped),
        "swapped_wrong": sum(1 for i, f in enumerate(files) if f in swapped and not right[i]),
        "unswapped_wrong": sum(1 for i, f in enumerate(files) if f not in swapped and not right[i]),
        "caught": len(flagged & swapped),
        "swapped_typed_names": sum(
            1 for f in swapped if name_room_type(room_name(by_original[f])) is not None
        ),
        "false_alarms": len(flagged - swapped),
        "pairs_catchable_by_type": catchable,
    }


# --------------------------------------------------------------------------- the run


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.room_grouping", description=__doc__)
    parser.add_argument(
        "--per-photo", type=Path, action="append", required=True, help="a stored per-photo run"
    )
    parser.add_argument("--photos", type=Path, required=True, help="the photos directory")
    parser.add_argument("--labels", type=Path, default=None, help="default: $BASIS_EVAL_DATA_DIR")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--models",
        nargs="*",
        default=list(EMBEDDING_MODELS),
        choices=list(EMBEDDING_MODELS),
        help="embedding models to run (none: rules only)",
    )
    parser.add_argument("--linkages", nargs="*", default=list(LINKAGES), choices=list(LINKAGES))
    parser.add_argument("--swap-pairs", nargs="*", type=int, default=[5, 10])
    parser.add_argument(
        "--same-type-pairs",
        nargs="*",
        type=int,
        default=[5],
        help="swaps whose two names say the same room type",
    )
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args(argv)


def photo_paths(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in PHOTO_SUFFIXES)


def _timed(fn: Any, *args: Any) -> tuple[Any, float]:
    t0 = time.perf_counter()
    value = fn(*args)
    return value, round(time.perf_counter() - t0, 4)


def run_camera_rules(
    files: Sequence[str], analyses: Mapping[str, PhotoAnalysis | None], truth: Sequence[str]
) -> tuple[dict[str, Any], dict[str, Partition]]:
    """The rule candidates on one repeat's answers: ``{candidate: scores + seconds}`` and
    their partitions."""
    scores: dict[str, Any] = {}
    parts: dict[str, Partition] = {}
    rules = {
        "today": lambda: rooms_partition(files, analyses, camera_names(files)),
        "ai_type": lambda: ai_type_partition(files, analyses),
        "singletons": lambda: singleton_partition(files),
        "exterior_one_room": lambda: exterior_one_room_partition(files, analyses),
        "today_exterior_one_room": lambda: exterior_merged(
            rooms_partition(files, analyses, camera_names(files)),
            ai_type_partition(files, analyses),
        ),
    }
    for name, rule in rules.items():
        part, seconds = _timed(rule)
        scores[name] = {**score_partition(part, truth), "seconds": seconds}
        parts[name] = part
    return scores, parts


def regression_scores(
    files: Sequence[str],
    analyses: Mapping[str, PhotoAnalysis | None],
    truth: Sequence[str],
    aliases: Mapping[str, str],
) -> dict[str, Any]:
    """Today's rule on the real names, without and with the aliases (EVAL_REPORT 48.4's
    "after" rows: 49 rooms and 33 rooms, no photo without a room)."""
    real = {f: f for f in files}
    return {
        "names": score_partition(rooms_partition(files, analyses, real), truth),
        "names_with_aliases": score_partition(
            rooms_partition(files, analyses, real, aliases), truth
        ),
    }


def run_embedding_candidates(
    d: Vector, truth: Sequence[str], linkages: Sequence[str], embed_seconds: float
) -> tuple[dict[str, Any], dict[str, Partition]]:
    """Oracle and leave-one-out rows of one distance matrix, per linkage and objective
    (``<linkage>/<objective>/oracle`` and ``/loo``)."""
    scores: dict[str, Any] = {}
    parts: dict[str, Partition] = {}
    for method in linkages:
        for objective in OBJECTIVES:
            t0 = time.perf_counter()
            z = _linkage(d, method)
            threshold, best = best_threshold(z, truth, objective)
            oracle_seconds = time.perf_counter() - t0
            parts[f"{method}/{objective}/oracle"] = cut(z, threshold)
            scores[f"{method}/{objective}/oracle"] = {
                **best,
                "threshold": round(threshold, 4),
                "seconds": round(oracle_seconds + embed_seconds, 4),
            }
            t0 = time.perf_counter()
            loo = loo_tuned(d, truth, method, objective)
            loo_seconds = time.perf_counter() - t0
            parts[f"{method}/{objective}/loo"] = loo.pop("partition_at_median")
            scores[f"{method}/{objective}/loo"] = {
                **loo.pop("at_median"),
                **loo,
                "seconds": round(loo_seconds + embed_seconds, 4),
            }
    return scores, parts


def _run(args: argparse.Namespace, run: EvalRun, labels: Path) -> int:
    by_room, by_photo, label_aliases = load_labels(labels)
    for name in ("room_inventory_v1.json", "photo_rooms_v1.json", "room_aliases_v1.json"):
        run.dataset(labels / name)
    run.input("photos", args.photos)
    truth_of = {f: r["room_id"] for r in by_room.values() for f in r["photos"]}
    files = sorted(truth_of)
    paths = photo_paths(args.photos)
    if [p.name for p in paths] != files:
        raise SystemExit("the photos directory and the room labels list different files")
    truth = [truth_of[f] for f in files]
    summary: dict[str, Any] = {
        "photos": len(files),
        "rooms": len(by_room),
        "aliases": len(label_aliases),
        "cost_usd": 0.0,
        "baselines": {
            "singletons": score_partition(singleton_partition(files), truth),
            "one_room": score_partition([0] * len(files), truth),
        },
    }
    partitions: dict[str, Any] = {"files": files, "truth": truth}

    times = exif_capture_times(paths)
    with_time = sum(v is not None for v in times.values())
    summary["exif"] = {
        "photos_with_capture_time": with_time,
        "status": "scored" if with_time else "dropped: no photo carries an EXIF capture time",
    }
    run.note(exif_photos_with_capture_time=with_time)

    interior = [by_room[t]["room_type"] != RoomType.EXTERIOR.value for t in truth]
    embeddings: dict[str, Vector] = {}
    summary["embeddings"] = {}
    summary["camera_embeddings"] = {}
    for model in args.models:
        x, info = embed_photos(model, paths)
        embeddings[model] = x
        summary["embeddings"][model] = info
        d = cosine_distance_matrix(x)
        info["nearest_neighbour"] = {
            "all": nearest_mates(d, truth),
            "not_exterior": nearest_mates(d, truth, interior),
        }
        scores, parts = run_embedding_candidates(d, truth, args.linkages, info["embed_seconds"])
        summary["camera_embeddings"][model] = scores
        partitions[f"camera_embeddings/{model}"] = parts
        print(f"{model}: {info}", file=sys.stderr)
        for name, s in scores.items():
            print(f"  {name}: {_line(s)}", file=sys.stderr)
        run.case(f"camera/{model}", PASSED, kind="embedding", detail=scores)

    summary["camera"] = {}
    summary["misleading"] = {}
    summary["regression"] = {}
    for per_photo in args.per_photo:
        run.input(per_photo.name, per_photo)
        answers = load_answers(per_photo)
        summary["camera"][per_photo.name] = {}
        summary["misleading"][per_photo.name] = {}
        summary["regression"][per_photo.name] = {}
        for repeat, analyses in sorted(answers.items()):
            if sorted(analyses) != files:
                raise SystemExit(f"{per_photo} repeat {repeat} answers different files")
            summary["regression"][per_photo.name][repeat] = regression_scores(
                files, analyses, truth, label_aliases
            )
            scores, parts = run_camera_rules(files, analyses, truth)
            groups = ai_type_partition(files, analyses)
            exterior = [g == RoomType.EXTERIOR.value for g in groups]
            for model, x in embeddings.items():
                d = block_matrix(cosine_distance_matrix(x), groups)
                for suffix, matrix in (
                    ("within_ai_type", d),
                    ("exterior_one_room_within_ai_type", force_together(d, exterior)),
                ):
                    more, more_parts = run_embedding_candidates(
                        matrix, truth, args.linkages, summary["embeddings"][model]["embed_seconds"]
                    )
                    for name, s in more.items():
                        scores[f"{model}/{name}/{suffix}"] = s
                        parts[f"{model}/{name}/{suffix}"] = more_parts[name]
            summary["camera"][per_photo.name][repeat] = scores
            partitions[f"camera/{per_photo.name}/{repeat}"] = parts
            print(f"camera {per_photo.name} r{repeat}:", file=sys.stderr)
            for name, s in scores.items():
                print(f"  {name}: {_line(s)}", file=sys.stderr)
            run.case(f"camera/{per_photo.name}/r{repeat}", PASSED, kind="repeat", detail=scores)

            swaps: dict[str, Any] = {}
            scenarios = [(str(n), n, False) for n in [0, *args.swap_pairs]]
            scenarios += [(f"{n}_same_type", n, True) for n in args.same_type_pairs]
            for key, pairs, same in scenarios:
                chosen = (
                    swap_pairs(files, truth_of, pairs, args.seed, same_type=same) if pairs else []
                )
                result, seconds = _timed(
                    misleading_scores, files, analyses, truth, label_aliases, chosen
                )
                swaps[key] = {**result, "seconds": seconds}
                print(
                    f"misleading {per_photo.name} r{repeat} {key} pairs: {_line(result)}; "
                    f"in named room {result['photos_in_named_room']}; swapped wrong "
                    f"{result['swapped_wrong']}/{result['swapped_photos']} caught "
                    f"{result['caught']} false alarms {result['false_alarms']}",
                    file=sys.stderr,
                )
            summary["misleading"][per_photo.name][repeat] = swaps
            run.case(f"misleading/{per_photo.name}/r{repeat}", PASSED, kind="repeat", detail=swaps)

    (args.out / "partitions.json").write_text(json.dumps(partitions, indent=1) + "\n")
    run.finish(summary)
    return 0


def _line(s: Mapping[str, Any]) -> str:
    return (
        f"rooms {s['rooms']} right {s['photos_right']} exact {s['rooms_exact']} "
        f"unassigned {s['photos_unassigned']} pairF1 {s['pairwise_f1']} bcubedF1 {s['bcubed_f1']}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    labels = args.labels
    if labels is None:
        root = os.environ.get("BASIS_EVAL_DATA_DIR")
        if not root:
            print("set BASIS_EVAL_DATA_DIR or pass --labels", file=sys.stderr)
            return 2
        labels = Path(root) / "labels"
    run = EvalRun.begin(args.out, harness=__name__, args=args, component=COMPONENT, version=VERSION)
    try:
        return _run(args, run, labels)
    except BaseException as exc:
        finish_pending(args, exit_code=1, error=exc)
        raise


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
