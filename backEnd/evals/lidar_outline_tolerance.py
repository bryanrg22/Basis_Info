"""The lidar roof outline's simplification tolerance, measured (EVAL_REPORT 104.2).

Buildings of known shape (rectangles turned 0 to 45 degrees, an L, a T, a house with a 1 m jog)
are rasterized at the lidar grid (0.5 m cells, a cell is the building's when its centre is
inside), traced with :func:`basis.geo.lidar.trace_outline` and simplified with
:func:`basis.geo.lidar.simplify_ring` at each candidate tolerance. Per shape and tolerance: the
corners kept (against the shape's own), the outline's area against the cells' area (the
footprint the summary reports) and against the true area, and whether the ring stays simple.
CPU only, no data, no paid call::

    uv run python -m evals.lidar_outline_tolerance
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from basis.domain.traced import ring_problem, shoelace
from basis.geo.lidar import CELL_M, simplify_ring, trace_outline

Ring = list[tuple[float, float]]
TOLERANCES = (0.0, 0.25, 0.5, 0.71, 1.0)


def turned(ring: Ring, degrees: float, at: tuple[float, float] = (0.0, 0.0)) -> Ring:
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return [(at[0] + x * c - y * s, at[1] + x * s + y * c) for x, y in ring]


def inside(x: np.ndarray, y: np.ndarray, ring: Sequence[tuple[float, float]]) -> np.ndarray:
    out = np.zeros(x.shape, dtype=bool)
    n = len(ring)
    for k in range(n):
        (ax, ay), (bx, by) = ring[k], ring[(k + 1) % n]
        crosses = (ay > y) != (by > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xs = ax + (y - ay) * (bx - ax) / (by - ay)
        out ^= crosses & (x < xs)
    return out


def rasterize(ring: Ring, offset: float = 0.13) -> np.ndarray:
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    x0, y0 = math.floor(min(xs)) - 2 + offset, math.floor(min(ys)) - 2 + offset
    nx = math.ceil((max(xs) - x0 + 2) / CELL_M)
    ny = math.ceil((max(ys) - y0 + 2) / CELL_M)
    i, j = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    return inside(x0 + (i + 0.5) * CELL_M, y0 + (j + 0.5) * CELL_M, ring)


SHAPES: list[tuple[str, Ring, int]] = [
    *[
        (f"rectangle 12 x 8 m at {d} deg", turned([(0, 0), (12, 0), (12, 8), (0, 8)], d), 4)
        for d in (0, 10, 22.5, 30, 45)
    ],
    *[
        (
            f"L 10 x 10 m less 5 x 5 at {d} deg",
            turned([(0, 0), (10, 0), (10, 5), (5, 5), (5, 10), (0, 10)], d),
            6,
        )
        for d in (0, 30)
    ],
    (
        "T 12 x 4 m bar on a 4 x 8 m stem",
        [(4, 0), (8, 0), (8, 8), (12, 8), (12, 12), (0, 12), (0, 8), (4, 8)],
        8,
    ),
    ("house 14 x 9 m with a 1 m jog", [(0, 0), (14, 0), (14, 9), (7, 9), (7, 8), (0, 8)], 6),
]


def rows() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for name, ring, corners in SHAPES:
        mask = rasterize(ring)
        cells = float(mask.sum()) * CELL_M * CELL_M
        true = abs(shoelace(ring))
        traced = [(i * CELL_M, j * CELL_M) for i, j in trace_outline(mask)]
        for tol in TOLERANCES:
            simple = simplify_ring(traced, tol)
            area = abs(shoelace(simple))
            out.append(
                {
                    "shape": name,
                    "true corners": corners,
                    "tolerance m": tol,
                    "corners": len(simple),
                    "area vs cells %": round((area - cells) / cells * 100, 2),
                    "area vs true %": round((area - true) / true * 100, 2),
                    "simple": ring_problem(simple) is None,
                }
            )
    return out


def main() -> None:
    table = rows()
    keys = list(table[0])
    print("| " + " | ".join(keys) + " |")
    print("|" + "---|" * len(keys))
    for r in table:
        print("| " + " | ".join(str(r[k]) for k in keys) + " |")


if __name__ == "__main__":
    main()
