"""``python -m evals.room_metadata``: the rooms the Rooms review opens on, scored.

What the photo stage leaves for the engineer before any review (EVAL_REPORT section 48):
the rooms, each with its name, room type and floor, and the photos left without a room.
The grouping is the production one, :func:`basis.vision.rooms.initial_rooms` (the function
``analyze_photo`` and ``reconcile_rooms``' vote apply, so this is what the database holds),
run on the file names and one repeat of a stored per-photo run. No model call, no database.

Scenarios (each per repeat of ``--per-photo``):

* ``filenames``: the package's file names, no aliases (the first Rooms review);
* ``aliases``: the same with ``--aliases`` (the labels' merges, as after the engineer's);
* ``camera``: every file renamed ``IMG_<n>.JPG`` (no name says a room: the model's room
  identity groups, and an ambiguous answer leaves the photo unassigned).

Scores against the private labels (``$BASIS_EVAL_DATA_DIR/labels/``):

* per room (``filenames``, ``aliases``), against the room the key names in
  ``room_inventory_v1.json`` (or, for a view the labels alias into another room, its photos'
  ``photo_rooms_v1.json`` labels): ``room_type`` and ``floor`` exact (a label floor
  ``basement`` is ``"0"``, a missing one ``None``); ``name`` against the engineer's own words
  for the room (``engineer_label`` of the key's own photo, without ``photo N`` and without a
  leading ``<n>th Floor`` / ``Basement`` that the floor already says), compared case- and
  space-insensitively. The name score checks the format the Rooms review should show (the
  floor in its own field, the ordinal kept); it is not a model accuracy;
* per photo (every scenario): the type and floor of the room the photo is in against the
  photo's label, and the photos without a room.

The labels' ``entry`` is ``hallway`` and ``stairs`` is ``stairway`` (the model vocabulary).

Output: ``<out>/rooms.json`` (every scenario's rooms per repeat) and ``<out>/summary.json``
(the scores), and the summary on stdout. Usage, from ``backEnd/``::

    python -m evals.room_metadata --per-photo ~/basis-eval-data/predictions/<run> --out out/rm
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from basis.vision.rooms import InitialRoom, PhotoInput, initial_rooms, name_hint
from basis.vision.schemas import PhotoAnalysis

LABEL_TYPES: Final = {"entry": "hallway", "stairs": "stairway"}
_PHOTO_SUFFIX: Final = re.compile(r"\s*\bphoto\s*\d+\s*$", re.IGNORECASE)
_FLOOR_WORDS: Final = re.compile(r"^(?:\d+(?:st|nd|rd|th)\s+floor|basement)\s+(?=\S)", re.I)
SCENARIOS: Final = ("filenames", "aliases", "camera")

Grouper = Callable[[Sequence[PhotoInput], Mapping[str, str]], list[InitialRoom]]


@dataclass(frozen=True, slots=True)
class Truth:
    name: str | None
    room_type: str
    floor: str | None


def _floor(value: Any) -> str | None:
    if value is None:
        return None
    return "0" if value == "basement" else str(value)


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def engineer_name(label: str) -> str:
    """The engineer's room words: no ``photo N``, no leading floor the floor field says."""
    words = " ".join(_PHOTO_SUFFIX.sub("", label).split())
    return _FLOOR_WORDS.sub("", words)


def load_labels(directory: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    rooms = json.loads((directory / "room_inventory_v1.json").read_text(encoding="utf-8"))
    photos = json.loads((directory / "photo_rooms_v1.json").read_text(encoding="utf-8"))
    aliases = json.loads((directory / "room_aliases_v1.json").read_text(encoding="utf-8"))
    by_room = {r["room_id"]: r for r in rooms["rooms"]}
    by_photo = {p["file"]: p for p in photos["items"]}
    return by_room, by_photo, aliases


def _photo_truth(item: Mapping[str, Any]) -> tuple[str, str | None]:
    room_type = str(item["room_type"])
    return LABEL_TYPES.get(room_type, room_type), _floor(item.get("floor"))


def room_truth(
    room: InitialRoom, by_room: Mapping[str, Any], by_photo: Mapping[str, Any]
) -> Truth | None:
    """What the room keyed ``room.key`` should say, or ``None`` for a model-keyed room."""
    if room.key is None or not room.photos:
        return None
    # The key's own photo (the labelling rule's room id is the key), as the name comes from.
    own_file = next((f for f in room.photos if name_hint(f).room_id == room.key), room.photos[0])
    own = by_photo.get(own_file)
    name = engineer_name(str(own["engineer_label"])) if own is not None else None
    label = by_room.get(room.key)
    if label is not None:
        room_type = str(label["room_type"])
        return Truth(name, LABEL_TYPES.get(room_type, room_type), _floor(label.get("floor")))
    answers = [_photo_truth(by_photo[f]) for f in room.photos if f in by_photo]
    if not answers:
        return None
    room_type, floor = Counter(answers).most_common(1)[0][0]
    return Truth(name, room_type, floor)


def score(
    rooms: Sequence[InitialRoom],
    files: Sequence[str],
    by_room: Mapping[str, Any],
    by_photo: Mapping[str, Any],
    renamed: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The scores of one grouping (module doc). ``renamed`` maps a scenario's file name back
    to the package's (the ``camera`` scenario)."""
    back = dict(renamed or {})
    placed: dict[str, InitialRoom] = {}
    for room in rooms:
        for photo in room.photos:
            placed[back.get(photo, photo)] = room
    unassigned = [f for f in files if f not in placed or placed[f].unassigned]
    room_scores = {"rooms": 0, "name": 0, "room_type": 0, "floor": 0, "all_three": 0}
    misses: list[dict[str, Any]] = []
    for room in rooms:
        if room.unassigned:
            continue
        room_scores["rooms"] += 1
        truth = room_truth(room, by_room, by_photo) if not back else None
        if truth is None:
            continue
        ok = {
            "name": truth.name is not None and _norm(room.name) == _norm(truth.name),
            "room_type": room.room_type == truth.room_type,
            "floor": room.floor == truth.floor,
        }
        for key, hit in ok.items():
            room_scores[key] += hit
        room_scores["all_three"] += all(ok.values())
        if not all(ok.values()):
            misses.append(
                {
                    "key": room.key,
                    "got": {"name": room.name, "room_type": room.room_type, "floor": room.floor},
                    "want": asdict(truth),
                    "wrong": sorted(k for k, hit in ok.items() if not hit),
                }
            )
    photo_type = photo_floor = 0
    for file in files:
        holder = placed.get(file)
        if holder is None or holder.unassigned or file not in by_photo:
            continue
        room_type, floor = _photo_truth(by_photo[file])
        photo_type += holder.room_type == room_type
        photo_floor += holder.floor == floor
    return {
        **room_scores,
        "ambiguous_rooms": sum(r.room_type == "ambiguous" and not r.unassigned for r in rooms),
        "photos": len(files),
        "photos_unassigned": len(unassigned),
        "unassigned": unassigned,
        "photo_room_type": photo_type,
        "photo_floor": photo_floor,
        "misses": misses,
    }


def load_answers(directory: Path) -> dict[int, dict[str, PhotoAnalysis | None]]:
    """``{repeat: {file: analysis}}`` from a stored per-photo run's ``photo_raw.jsonl``."""
    found: dict[int, dict[str, PhotoAnalysis | None]] = {}
    for line in (directory / "photo_raw.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        analysis = record.get("analysis")
        found.setdefault(int(record.get("repeat", 1)), {})[record["file"]] = (
            None if analysis is None else PhotoAnalysis.model_validate(analysis)
        )
    return found


def run(
    answers: Mapping[int, Mapping[str, PhotoAnalysis | None]],
    labels: Path,
    aliases: Mapping[str, str] | None = None,
    grouper: Grouper = initial_rooms,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(rooms, summary)`` of every scenario and repeat (module doc)."""
    by_room, by_photo, label_aliases = load_labels(labels)
    merges = dict(label_aliases if aliases is None else aliases)
    rooms_out: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    for scenario in SCENARIOS:
        per_repeat = []
        for repeat, analyses in sorted(answers.items()):
            files = sorted(analyses)
            renamed = (
                {f"IMG_{i + 1:04d}.JPG": f for i, f in enumerate(files)}
                if scenario == "camera"
                else {}
            )
            inputs = [
                PhotoInput(file=name, analysis=analyses[original])
                for name, original in (renamed.items() if renamed else ((f, f) for f in files))
            ]
            rooms = grouper(inputs, merges if scenario == "aliases" else {})
            rooms_out[f"{scenario}/{repeat}"] = [asdict(r) for r in rooms]
            per_repeat.append({"repeat": repeat, **score(rooms, files, by_room, by_photo, renamed)})
        summary[scenario] = per_repeat
    return rooms_out, summary


def _line(scenario: str, result: Mapping[str, Any]) -> str:
    rooms = result["rooms"]
    keyed = (
        ""
        if scenario == "camera"
        else (
            f" name {result['name']}/{rooms} type {result['room_type']}/{rooms}"
            f" floor {result['floor']}/{rooms} all {result['all_three']}/{rooms};"
        )
    )
    return (
        f"{scenario} r{result['repeat']}: rooms {rooms};{keyed} ambiguous rooms "
        f"{result['ambiguous_rooms']}; photos type {result['photo_room_type']}/{result['photos']}"
        f" floor {result['photo_floor']}/{result['photos']} unassigned "
        f"{result['photos_unassigned']}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.room_metadata", description=__doc__)
    parser.add_argument("--per-photo", type=Path, required=True, help="a stored per-photo run")
    parser.add_argument("--labels", type=Path, default=None, help="default: $BASIS_EVAL_DATA_DIR")
    parser.add_argument("--aliases", type=Path, default=None, help="default: the labels' aliases")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    labels = args.labels
    if labels is None:
        root = os.environ.get("BASIS_EVAL_DATA_DIR")
        if not root:
            print("set BASIS_EVAL_DATA_DIR or pass --labels", file=sys.stderr)
            return 2
        labels = Path(root) / "labels"
    aliases = None
    if args.aliases is not None:
        aliases = json.loads(args.aliases.read_text(encoding="utf-8"))
    rooms, summary = run(load_answers(args.per_photo), labels, aliases)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "rooms.json").write_text(json.dumps(rooms, indent=2) + "\n", encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for scenario, results in summary.items():
        for result in results:
            print(_line(scenario, result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
