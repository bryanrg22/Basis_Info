"""The interior truth's own error against the Faro laser scan (EVAL_REPORT 107.1).

The truth (:mod:`evals.interior_truth`) is measured on the iPad's lidar mesh. ARKitScenes also
scanned each venue with a Faro Focus laser scanner (millimetre class), but a scan is about 1.9 GB
(42 million points of 44 bytes). :func:`sample_scan` reads a thin, even sample of it with HTTP
range requests (``chunks`` runs of ``points_per_chunk`` consecutive points spread over the file:
the scanner writes its points in scan order, so evenly spaced runs are evenly spaced scan
columns around the sphere), about 25 MB instead of 1.9 GB.

:func:`register` then fits a similarity (rotation, translation and **scale**) from the laser
sample to the mesh: nearest mesh vertex for each laser point within a shrinking distance,
Umeyama's closed form on the pairs, iterated. The scale it finds is the mesh's own scale error
(the laser is the reference); the residual distances are the mesh's local error. The scan's
registration pose (``<id>_pose.txt``) is in another frame than the iPad's, so the fit starts from
a coarse search (:func:`coarse`: floors matched, every 10 degrees of yaw, a 0.5 m grid).

``python -m evals.interior_laser ROOM VISIT SCAN`` runs it on one room (its mesh fetched by
``evals.interior_data mesh``) and writes ``laser/<room>_<scan>.json``.
"""

from __future__ import annotations

import json
import math
import sys
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt
from scipy.spatial import cKDTree

from evals.interior_data import HOST, USER_AGENT

Array = npt.NDArray[np.float64]
FARO_DTYPE: Final = np.dtype(
    [
        ("x", "<f8"),
        ("y", "<f8"),
        ("z", "<f8"),
        ("rgba", "u1", (4,)),
        ("quality", "<f8"),
        ("radius", "<f8"),
    ]
)


def scan_url(visit_id: str, scan_id: str, suffix: str = ".ply") -> str:
    return f"{HOST}/raw/laser_scanner_point_clouds/{visit_id}/{scan_id}{suffix}"


def _get(url: str, start: int | None = None, end: int | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if start is not None:
        headers["Range"] = f"bytes={start}-{end}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
        return bytes(r.read())


def read_pose(visit_id: str, scan_id: str) -> Array:
    """The scan's 4 x 4 registration (rows 0-2: rotation, row 3: translation)."""
    rows = [
        [float(v) for v in line.split(",")]
        for line in _get(scan_url(visit_id, scan_id, "_pose.txt")).decode().splitlines()
        if line.strip()
    ]
    return np.asarray(rows, dtype=np.float64)


def sample_scan(
    visit_id: str, scan_id: str, *, chunks: int = 300, points_per_chunk: int = 2000
) -> tuple[Array, int]:
    """An even sample of the scan's points (x, y, z) and the bytes read."""
    url = scan_url(visit_id, scan_id)
    head = _get(url, 0, 4095)
    end = head.index(b"end_header\n") + len(b"end_header\n")
    header = head[:end].decode("ascii")
    if "property double quality" not in header:
        raise ValueError(f"{scan_id}: unexpected PLY layout")
    n = int(header.split("element vertex ")[1].split()[0])
    step = FARO_DTYPE.itemsize
    starts = np.linspace(0, n - points_per_chunk, chunks).astype(np.int64)
    parts = []
    read = 0
    for s in starts:
        a = end + int(s) * step
        b = a + points_per_chunk * step - 1
        data = _get(url, a, b)
        read += len(data)
        arr = np.frombuffer(data, dtype=FARO_DTYPE, count=len(data) // step)
        parts.append(np.stack([arr["x"], arr["y"], arr["z"]], axis=1))
    pts = np.concatenate(parts)
    pts = pts[np.all(np.isfinite(pts), axis=1) & (np.linalg.norm(pts, axis=1) > 0.05)]
    return pts, read


def umeyama(src: Array, dst: Array) -> tuple[float, Array, Array]:
    """The similarity ``dst ~ s R src + t`` (least squares, Umeyama 1991)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    cov = b.T @ a / len(src)
    u, d, vt = np.linalg.svd(cov)
    sign = np.eye(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        sign[2, 2] = -1
    r = u @ sign @ vt
    var = float((a**2).sum() / len(src))
    s = float(np.trace(np.diag(d) @ sign) / var)
    t = mu_d - s * r @ mu_s
    return s, r, t


@dataclass(frozen=True, slots=True)
class Registration:
    scale: float
    rotation: Array
    translation: Array
    inliers: int
    median_residual_m: float
    p90_residual_m: float


def yaw(theta: float) -> Array:
    c, s = math.cos(theta), math.sin(theta)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def floor_level(z: Array) -> float:
    """The 2 cm height bin holding the most points in the lower half (the floor)."""
    lo, hi = np.percentile(z, [1, 50])
    hist, edges = np.histogram(z[(z >= lo) & (z <= hi)], bins=max(1, int((hi - lo) / 0.02)))
    k = int(np.argmax(hist))
    return float((edges[k] + edges[k + 1]) / 2)


def coarse(
    laser: Array, mesh_points: Array, laser_floor: float, mesh_floor: float
) -> list[tuple[Array, Array]]:
    """Starts for the fit: floors matched, every 10 degrees of yaw, the laser's centre moved
    over a 0.5 m grid across the mesh's extent; the five with most mesh points within 10 cm."""
    rng = np.random.default_rng(107)
    probe = laser[rng.choice(len(laser), min(3000, len(laser)), replace=False)]
    tree = cKDTree(mesh_points[:: max(1, len(mesh_points) // 300_000)])
    lo, hi = mesh_points[:, :2].min(0), mesh_points[:, :2].max(0)
    xs = np.arange(lo[0], hi[0] + 0.01, 0.5)
    ys = np.arange(lo[1], hi[1] + 0.01, 0.5)
    centre = np.array([probe[:, 0].mean(), probe[:, 1].mean(), laser_floor])
    scored = []
    for k in range(36):
        r = yaw(math.radians(10 * k))
        rotated = (probe - centre) @ r.T
        for x in xs:
            for y in ys:
                t = np.array([x, y, mesh_floor])
                d, _ = tree.query(rotated + t, distance_upper_bound=0.1)
                scored.append((int(np.isfinite(d).sum()), r, t - r @ centre))
    scored.sort(key=lambda s: -s[0])
    return [(r, t) for _, r, t in scored[:5]]


def register(
    laser: Array,
    mesh_points: Array,
    *,
    laser_floor: float,
    mesh_floor: float,
    iterations: int = 40,
) -> Registration:
    """Laser to mesh: from each coarse start, nearest-neighbour pairs within a shrinking
    distance and Umeyama's similarity, iterated; the fit with most inliers (5 cm) is kept."""
    tree = cKDTree(mesh_points)
    best: Registration | None = None
    for r, t in coarse(laser, mesh_points, laser_floor, mesh_floor):
        s = 1.0
        for i in range(iterations):
            moved = s * laser @ r.T + t
            dist, idx = tree.query(moved, distance_upper_bound=np.inf)
            limit = max(0.05, 1.0 * (0.8**i))
            ok = dist < limit
            if ok.sum() < 200:
                break
            s, r, t = umeyama(laser[ok], mesh_points[idx[ok]])
        moved = s * laser @ r.T + t
        dist, _ = tree.query(moved)
        ok = dist < 0.05
        reg = Registration(
            s,
            r,
            t,
            int(ok.sum()),
            float(np.median(dist[ok])) if ok.any() else math.inf,
            float(np.percentile(dist[ok], 90)) if ok.any() else math.inf,
        )
        if best is None or reg.inliers > best.inliers:
            best = reg
    assert best is not None
    return best


def main(argv: Sequence[str] | None = None) -> int:
    from evals import interior_truth as it
    from evals.interior_data import interiors_dir

    room, visit, scan = (argv if argv is not None else sys.argv[1:])[:3]
    base = interiors_dir()
    points, _ = it.read_ply(base / "raw" / room / "mesh.ply")
    truth = it.load(base / "truth" / f"{room}.json")
    if truth.floor_z_m is None:
        raise SystemExit(f"{room}: no floor in its truth")
    laser, read = sample_scan(visit, scan)
    reg = register(laser, points, laser_floor=floor_level(laser[:, 2]), mesh_floor=truth.floor_z_m)
    out = base / "laser" / f"{room}_{scan}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "room": room,
                "visit": visit,
                "scan": scan,
                "bytes_read": read,
                "laser_points": len(laser),
                "scale_laser_to_mesh": reg.scale,
                "inliers_5cm": reg.inliers,
                "median_residual_m": reg.median_residual_m,
                "p90_residual_m": reg.p90_residual_m,
            },
            indent=1,
        )
        + "\n"
    )
    median = reg.median_residual_m
    print(f"{room}: scale {reg.scale:.5f}, {reg.inliers} inliers, median {median:.4f} m")
    return 0


__all__ = [
    "Registration",
    "coarse",
    "floor_level",
    "read_pose",
    "register",
    "sample_scan",
    "umeyama",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
