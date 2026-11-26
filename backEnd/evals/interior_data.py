"""``python -m evals.interior_data``: fetch the ARKitScenes rooms the interior eval uses
(EVAL_REPORT 107) into ``$BASIS_EVAL_DATA_DIR/measure_truth/interiors/``, never the repository.

ARKitScenes is used as an internal evaluation set only (its LICENSE speaks of "Apple Software";
see :mod:`evals.interior_truth`). Only what the eval needs is fetched, from Apple's public
download host (the URLs of the dataset's own ``download_data.py``), one request at a time:

* ``annotations``: each candidate room's 3dod annotation (about 10 KB: the objects boxed in it,
  which say whether it is a kitchen, a bedroom, a bathroom or a living room);
* ``mesh``: the chosen rooms' 3dod mesh (about 25 MB, the iPad lidar reconstruction the truth is
  measured on) and the camera path (``lowres_wide.traj``, about 60 KB);
* ``frames``: a few 640 x 480 frames per room (``vga_wide``), chosen to look like an owner's
  photos (:func:`choose_frames`), read out of the 0.5 to 0.8 GB frame archive with HTTP range
  requests (:class:`HttpRangeFile`: the archive's directory and the chosen members only, about
  0.5 MB a frame), turned upright with the video's sky direction, with their intrinsics.

``--delete-meshes`` removes the meshes once the truth is measured (the truth files and check
pictures stay).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
import time
import urllib.request
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np

from evals import datasets
from evals import interior_truth as it

HOST: Final = "https://docs-assets.developer.apple.com/ml-research/datasets/arkitscenes/v1"
USER_AGENT: Final = "eval-client"
PAUSE_S: Final = 0.2
# Owner-like photos (choose_frames): a phone about level or tipped a little down (pitch from 25
# degrees down to 5 up), held 1.1 to 1.9 m above the floor, looking across the room, four views
# whose directions differ by at least 60 degrees; of the frames within SHARPEST_WINDOW_S of each
# chosen one, the sharpest (variance of the Laplacian), as a person retakes a blurred photo.
PITCH_DEG: Final = (-25.0, 5.0)
CAMERA_HEIGHT_M: Final = (1.1, 1.9)
MIN_VIEW_SEPARATION_DEG: Final = 60.0
FRAMES_PER_ROOM: Final = 4
SHARPEST_WINDOW_S: Final = 0.25
SHARPEST_CANDIDATES: Final = 5
# The frame selection's coarse projection: the stored 640 x 480 frame with the iPad wide
# camera's focal length (about 531 px, read in the frames' intrinsics), a 24 x 32 grid, a view
# the mesh fills at least 80 % of.
SELECT_SIZE: Final = (640, 480)
SELECT_FOCAL: Final = 531.0
DEPTH_GRID: Final = (24, 32)
MIN_FILLED: Final = 0.8
VIEW_DEPTH_SHARE: Final = 0.4
MIN_VIEW_DEPTH_M: Final = 1.2


def interiors_dir() -> Path:
    return datasets.data_dir() / "measure_truth" / "interiors"


def fetch(url: str, path: Path) -> int:
    """Fetch ``url`` to ``path`` (skipped when present); bytes written."""
    if path.exists():
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.rename(path)
    time.sleep(PAUSE_S)
    return len(data)


class HttpRangeFile(io.RawIOBase):
    """A seekable, read-only file over HTTP range requests (``zipfile`` reads the archive's
    directory and the members asked for, nothing else)."""

    def __init__(self, url: str) -> None:
        self.url = url
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            self.size = int(r.headers["Content-Length"])
        self.pos = 0
        self.fetched = 0

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self.pos, 2: self.size}[whence]
        self.pos = base + offset
        return self.pos

    def read(self, size: int | None = -1) -> bytes:
        n = self.size - self.pos if size is None or size < 0 else size
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        req = urllib.request.Request(
            self.url,
            headers={"Range": f"bytes={self.pos}-{self.pos + n - 1}", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        self.pos += len(data)
        self.fetched += len(data)
        return bytes(data)

    def readinto(self, buffer: Any) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


@dataclass(frozen=True, slots=True)
class Video:
    video_id: str
    visit_id: str
    fold: str
    sky: str  # Up | Down | Left | Right: where the sky is in the stored frame

    def url(self, name: str) -> str:
        return f"{HOST}/raw/{self.fold}/{self.video_id}/{name}"


def read_videos(metadata: Path) -> dict[str, Video]:
    out = {}
    with metadata.open() as f:
        for r in csv.DictReader(f):
            out[r["video_id"]] = Video(r["video_id"], r["visit_id"], r["fold"], r["sky_direction"])
    return out


# ------------------------------------------------------------------ choosing the photos


@dataclass(frozen=True, slots=True)
class Pose:
    timestamp: float
    rotation: Any  # camera to world, 3 x 3 (camera: x right, y down, z forward)
    centre: Any


def upright_rotation(sky: str) -> int:
    """Degrees to rotate the stored frame counter-clockwise so the sky is up."""
    return {"Up": 0, "Left": 270, "Right": 90, "Down": 180}[sky]


def view_angles(pose: Pose, sky: str) -> tuple[float, float]:
    """(pitch, yaw) of the upright photo's optical axis, degrees (pitch 0: level)."""
    forward = pose.rotation @ np.array([0.0, 0.0, 1.0])
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, float(forward[2])))))
    yaw = math.degrees(math.atan2(float(forward[1]), float(forward[0])))
    return pitch, yaw


def visible_depths(pose: Pose, points: Any) -> Any:
    """The nearest mesh point's depth in each cell of a coarse grid over the stored frame
    (:data:`DEPTH_GRID`, NaN where nothing projects): a z-buffer of the mesh's vertices, enough
    to tell a photo across the room from one of a wall at arm's length."""
    rows, cols = DEPTH_GRID
    cam = (points - pose.centre) @ pose.rotation  # world to camera (x right, y down, z ahead)
    ahead = cam[:, 2] > 0.1
    cam = cam[ahead]
    u = SELECT_FOCAL * cam[:, 0] / cam[:, 2] + SELECT_SIZE[0] / 2
    v = SELECT_FOCAL * cam[:, 1] / cam[:, 2] + SELECT_SIZE[1] / 2
    inside = (u >= 0) & (u < SELECT_SIZE[0]) & (v >= 0) & (v < SELECT_SIZE[1])
    col = (u[inside] * cols / SELECT_SIZE[0]).astype(int)
    row = (v[inside] * rows / SELECT_SIZE[1]).astype(int)
    depth = np.full(rows * cols, np.inf)
    np.minimum.at(depth, row * cols + col, cam[inside, 2])
    depth[~np.isfinite(depth)] = np.nan
    return depth


def choose_frames(
    poses: Sequence[Pose],
    truth: it.RoomTruth,
    sky: str,
    points: Any,
    *,
    count: int = FRAMES_PER_ROOM,
) -> list[Pose]:
    """Frames that look like an owner's photos of the room: about level (pitch in
    :data:`PITCH_DEG`), held at a person's height above the floor, and looking across the room
    (a scanning video mostly looks at a wall or a counter from close by; an owner stands back):
    the mesh's vertices are projected into each candidate (:func:`visible_depths`), frames
    whose view the mesh does not fill (:data:`MIN_FILLED` of the grid) or whose median visible
    depth is under :data:`VIEW_DEPTH_SHARE` of the room's diagonal (and
    :data:`MIN_VIEW_DEPTH_M`) are skipped, the ones that see farthest are taken first, then
    views at least :data:`MIN_VIEW_SEPARATION_DEG` apart. Every fifth frame is a candidate
    (about 2 a second). A scan that never stands back gives no photo: the room is not in the
    photo eval (EVAL_REPORT 107.1 counts them)."""
    if truth.floor_z_m is None or truth.length_ft is None or truth.width_ft is None:
        return []
    diagonal_m = math.hypot(truth.length_ft, truth.width_ft) * 0.3048
    need = max(MIN_VIEW_DEPTH_M, VIEW_DEPTH_SHARE * diagonal_m)
    candidates = []
    for p in poses[::5]:
        pitch, yaw = view_angles(p, sky)
        height = float(p.centre[2]) - truth.floor_z_m
        if not PITCH_DEG[0] <= pitch <= PITCH_DEG[1]:
            continue
        if not CAMERA_HEIGHT_M[0] <= height <= CAMERA_HEIGHT_M[1]:
            continue
        depth = visible_depths(p, points)
        filled = np.isfinite(depth)
        if filled.mean() < MIN_FILLED:
            continue
        seen = float(np.median(depth[filled]))
        if seen < need:
            continue
        candidates.append((seen, yaw, p))
    candidates.sort(key=lambda c: -c[0])
    chosen: list[tuple[float, Pose]] = []
    for _, yaw, p in candidates:
        if all(abs((yaw - y + 180) % 360 - 180) >= MIN_VIEW_SEPARATION_DEG for y, _ in chosen):
            chosen.append((yaw, p))
        if len(chosen) == count:
            break
    return [p for _, p in chosen]


def nearest_member(names: Sequence[str], video_id: str, timestamp: float) -> str:
    """The archive member whose timestamp (``<video>_<ts>.png``) is nearest ``timestamp``."""

    def ts(name: str) -> float:
        return float(name.rsplit("_", 1)[1].rsplit(".", 1)[0])

    return nearest_members(names, video_id, timestamp, 1)[0]


def member_time(name: str) -> float:
    return float(name.rsplit("_", 1)[1].rsplit(".", 1)[0])


def nearest_members(names: Sequence[str], video_id: str, timestamp: float, count: int) -> list[str]:
    mine = [n for n in names if n.rsplit("/", 1)[-1].startswith(f"{video_id}_")]
    return sorted(mine, key=lambda n: abs(member_time(n) - timestamp))[:count]


def sharpness(image: Any) -> float:
    """The variance of the Laplacian of the grey picture (higher: sharper)."""
    g = np.asarray(image.convert("L"), dtype=np.float64)
    lap = g[1:-1, :-2] + g[1:-1, 2:] + g[:-2, 1:-1] + g[2:, 1:-1] - 4 * g[1:-1, 1:-1]
    return float(lap.var())


def fetch_frames(video: Video, poses: Sequence[Pose], out: Path) -> list[dict[str, Any]]:
    """The chosen frames (upright PNG) and their intrinsics (as the upright frame sees them)."""
    from PIL import Image

    frames = zipfile.ZipFile(io.BufferedReader(HttpRangeFile(video.url("vga_wide.zip"))))
    pincams = zipfile.ZipFile(
        io.BufferedReader(HttpRangeFile(video.url("vga_wide_intrinsics.zip")))
    )
    frame_names = [n for n in frames.namelist() if n.endswith(".png")]
    pincam_names = [n for n in pincams.namelist() if n.endswith(".pincam")]
    rotate = upright_rotation(video.sky)
    records = []
    for k, pose in enumerate(poses):
        options = [
            n
            for n in nearest_members(
                frame_names, video.video_id, pose.timestamp, SHARPEST_CANDIDATES
            )
            if abs(member_time(n) - pose.timestamp) <= SHARPEST_WINDOW_S
        ] or [nearest_member(frame_names, video.video_id, pose.timestamp)]
        pictures = {n: Image.open(io.BytesIO(frames.read(n))).convert("RGB") for n in options}
        name = max(pictures, key=lambda n: sharpness(pictures[n]))
        image = pictures[name]
        stamp = member_time(name)
        pin_name = nearest_member(pincam_names, video.video_id, stamp)
        w, h, fx, fy, cx, cy = map(float, pincams.read(pin_name).decode().split())
        if rotate:
            image = image.rotate(rotate, expand=True)
        # The intrinsics of the upright picture.
        if rotate in (90, 270):
            fx, fy = fy, fx
            if rotate == 270:  # clockwise: (x, y) -> (h - 1 - y, x)
                cx, cy = h - cy, cx
            else:  # counter-clockwise: (x, y) -> (y, w - 1 - x)
                cx, cy = cy, w - cx
            w, h = h, w
        elif rotate == 180:
            cx, cy = w - cx, h - cy
        path = out / f"photo_{k + 1}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        pitch, yaw = view_angles(pose, video.sky)
        records.append(
            {
                "file": path.name,
                "member": name,
                "timestamp": stamp,
                "width": int(w),
                "height": int(h),
                "fx": fx,
                "fy": fy,
                "cx": cx,
                "cy": cy,
                "pitch_deg": round(pitch, 2),
                "yaw_deg": round(yaw, 2),
                "camera_height_m": None,
            }
        )
    return records


def poses_of(traj: Path) -> list[Pose]:
    ts, rot, centre = it.read_traj(traj)
    return [Pose(float(t), r, c) for t, r, c in zip(ts, rot, centre, strict=True)]


# ------------------------------------------------------------------ the command


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.interior_data", description=__doc__)
    parser.add_argument("step", choices=("mesh", "truth", "repeat", "frames", "delete-meshes"))
    parser.add_argument("--rooms", help="comma-separated video ids (default: rooms.json)")
    parser.add_argument("--dir", type=Path, default=None, help="default: interiors_dir()")
    return parser.parse_args(argv)


def measure(base: Path, video_id: str, mesh_dir: Path, name: str, out: str) -> it.RoomTruth:
    """Measure one scan's truth into ``<out>/<name>.json`` and its check picture."""
    points, faces = it.read_ply(mesh_dir / "mesh.ply")
    _, _, cams = it.read_traj(mesh_dir / "lowres_wide.traj")
    ann = base / "annotations" / f"{video_id}.json"
    columns = it.box_axes_layout(points, ann)
    boxes = it.read_boxes(ann, transpose=columns)
    truth, rasters = it.measure_room(video_id, points, faces, cams, boxes)
    truth.method["box_axes"] = "columns" if columns else "rows"
    it.save(truth, base / out / f"{name}.json")
    it.check_picture(truth, rasters, base / f"check_{out}" / f"{name}.png")
    return truth


def other_video(videos: dict[str, Video], video_id: str) -> Video | None:
    """Another scan of the same room (ARKitScenes scanned each venue about three times)."""
    visit = videos[video_id].visit_id
    others = sorted(
        (v for v in videos.values() if v.visit_id == visit and v.video_id != video_id),
        key=lambda v: v.video_id,
    )
    return others[0] if others else None


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    base = args.dir or interiors_dir()
    videos = read_videos(base / "src" / "raw_metadata.csv")
    if args.rooms:
        ids = args.rooms.split(",")
    else:
        ids = json.loads((base / "rooms.json").read_text())["rooms"]
        if args.step in ("repeat", "frames"):  # only the rooms whose truth was measured
            ids = [i for i in ids if it.load(base / "truth" / f"{i}.json").status == "ok"]
    total = 0
    for vid in ids:
        v = videos[vid]
        room = base / "raw" / vid
        if args.step == "mesh":
            total += fetch(v.url(f"{vid}_3dod_mesh.ply"), room / "mesh.ply")
            total += fetch(v.url("lowres_wide.traj"), room / "lowres_wide.traj")
            total += fetch(
                v.url(f"{vid}_3dod_annotation.json"), base / "annotations" / f"{vid}.json"
            )
        elif args.step == "truth":
            truth = measure(base, vid, room, vid, "truth")
            print(vid, truth.room_type, truth.status, truth.reason or "", truth.quantities())
        elif args.step == "repeat":
            # The same room's next scan, measured the same way; its mesh is then deleted.
            other = other_video(videos, vid)
            if other is None:
                print(vid, "no other scan")
                continue
            o = other.video_id
            tmp = base / "raw_repeat" / o
            total += fetch(other.url(f"{o}_3dod_mesh.ply"), tmp / "mesh.ply")
            total += fetch(other.url("lowres_wide.traj"), tmp / "lowres_wide.traj")
            total += fetch(
                other.url(f"{o}_3dod_annotation.json"), base / "annotations" / f"{o}.json"
            )
            truth = measure(base, o, tmp, vid, "truth_repeat")
            (tmp / "mesh.ply").unlink()
            print(vid, o, truth.status, truth.reason or "", truth.quantities())
        elif args.step == "frames":
            truth = it.load(base / "truth" / f"{vid}.json")
            points, _ = it.read_ply(room / "mesh.ply")
            sample = points[:: max(1, len(points) // 60_000)]
            chosen = choose_frames(poses_of(room / "lowres_wide.traj"), truth, v.sky, sample)
            if not chosen:
                print(vid, "0 photos: no owner-like view in the scan")
                continue
            records = fetch_frames(v, chosen, base / "photos" / vid)
            for rec, pose in zip(records, chosen, strict=True):
                rec["camera_height_m"] = round(float(pose.centre[2]) - (truth.floor_z_m or 0.0), 3)
            (base / "photos" / vid / "photos.json").write_text(
                json.dumps({"room": vid, "sky": v.sky, "photos": records}, indent=1) + "\n"
            )
            print(vid, len(records), "photos")
        elif args.step == "delete-meshes":
            mesh = room / "mesh.ply"
            if mesh.exists():
                total += mesh.stat().st_size
                mesh.unlink()
    print(f"bytes {total:,}")
    return 0


__all__ = [
    "HttpRangeFile",
    "Pose",
    "Video",
    "choose_frames",
    "fetch",
    "fetch_frames",
    "interiors_dir",
    "nearest_member",
    "read_videos",
    "upright_rotation",
    "view_angles",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
