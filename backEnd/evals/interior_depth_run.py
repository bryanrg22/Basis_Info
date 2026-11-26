"""The depth models on the interior photos (EVAL_REPORT 107): run in the depth tool environment,
not the project's (the models' packages are not project dependencies).

Usage (the environment and the models' code as EVAL_REPORT 107.2 records)::

    <tool venv>/bin/python evals/interior_depth_run.py MODEL PHOTOS_DIR OUT_DIR [--known-fov]

``MODEL``:

* ``moge2-s``: MoGe-2 ViT-S with normals (``Ruicheng/moge-2-vits-normal``, MIT), one photo at a
  time; a metric point map in the camera frame; the field of view estimated by the model, or the
  photo's own with ``--known-fov`` (as a phone's EXIF gives it).
* ``da3-metric``: Depth Anything 3 Metric-Large (``depth-anything/DA3METRIC-LARGE``,
  Apache-2.0), one photo at a time; metric depth = focal (px) x output / 300 (the model's FAQ),
  unprojected with the photo's own intrinsics (the model does not estimate them).
* ``da3-small-mv``: Depth Anything 3 Small (``depth-anything/DA3-SMALL``, Apache-2.0), all of a
  room's photos at once: depth, camera poses and intrinsics in one frame, up to scale.

Each room's result is ``OUT_DIR/<room>.npz``: per photo ``points_<k>`` (float16, the camera frame
for the single-view models, the shared frame for the multi-view one, x right, y down, z forward,
at half the photo's size), ``valid_<k>``, the intrinsics used, and the seconds of the model call
on the Mac's GPU (MPS; synchronised before the clock stops). No network once the weights are in
the Hugging Face cache.
"""

from __future__ import annotations

import importlib
import json
import sys
import time
import types
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

STRIDE = 2  # points are kept at half the photo's size


def _stub_optional_modules() -> None:
    """Depth Anything 3 imports export helpers at load time whose packages (video, COLMAP,
    trajectory tools) are not needed for inference; empty modules stand in for them."""
    for name in ("moviepy", "moviepy.editor", "pycolmap", "open3d", "e3nn", "evo", "evo.core"):
        sys.modules.setdefault(name, types.ModuleType(name))
    traj = types.ModuleType("evo.core.trajectory")
    traj.PosePath3D = object  # type: ignore[attr-defined]
    sys.modules.setdefault("evo.core.trajectory", traj)


def _sync(torch: Any) -> None:
    if torch.backends.mps.is_available():
        torch.mps.synchronize()


def intrinsics(photo: dict[str, Any]) -> np.ndarray:
    return np.array(
        [[photo["fx"], 0, photo["cx"]], [0, photo["fy"], photo["cy"]], [0, 0, 1]], dtype=np.float64
    )


def unproject(depth: np.ndarray, k: np.ndarray) -> np.ndarray:
    h, w = depth.shape
    u, v = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    x = (u - k[0, 2]) / k[0, 0] * depth
    y = (v - k[1, 2]) / k[1, 1] * depth
    return np.stack([x, y, depth], axis=-1)


def run_moge(photos: list[dict[str, Any]], folder: Path, known_fov: bool) -> dict[str, Any]:
    torch = importlib.import_module("torch")
    moge = importlib.import_module("moge.model.v2")
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    model = _cache.get("moge")
    if model is None:
        model = moge.MoGeModel.from_pretrained("Ruicheng/moge-2-vits-normal").to(device).eval()
        _cache["moge"] = model
    out: dict[str, Any] = {}
    for k, p in enumerate(photos):
        img = np.asarray(Image.open(folder / p["file"]).convert("RGB"), dtype=np.float32) / 255
        t = torch.tensor(img, device=device).permute(2, 0, 1)
        fov = None
        if known_fov:
            fov = float(np.degrees(2 * np.arctan(p["width"] / (2 * p["fx"]))))
        _sync(torch)
        started = time.monotonic()
        with torch.no_grad():
            res = model.infer(t, fov_x=fov, use_fp16=False)
        _sync(torch)
        seconds = time.monotonic() - started
        pts = res["points"].float().cpu().numpy()
        mask = res["mask"].cpu().numpy().astype(bool) & np.all(np.isfinite(pts), axis=-1)
        kn = res["intrinsics"].float().cpu().numpy()
        kpx = kn * np.array([[p["width"]], [p["height"]], [1.0]])
        out[f"points_{k}"] = np.nan_to_num(pts[::STRIDE, ::STRIDE]).astype(np.float16)
        out[f"valid_{k}"] = mask[::STRIDE, ::STRIDE]
        out[f"K_{k}"] = kpx
        out[f"seconds_{k}"] = np.array(seconds)
    return out


def run_da3_metric(photos: list[dict[str, Any]], folder: Path) -> dict[str, Any]:
    torch = importlib.import_module("torch")
    _stub_optional_modules()
    api = importlib.import_module("depth_anything_3.api")
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    model = _cache.get("da3m")
    if model is None:
        model = (
            api.DepthAnything3.from_pretrained("depth-anything/DA3METRIC-LARGE").to(device).eval()
        )
        _cache["da3m"] = model
    out: dict[str, Any] = {}
    for k, p in enumerate(photos):
        img = np.asarray(Image.open(folder / p["file"]).convert("RGB"))
        _sync(torch)
        started = time.monotonic()
        pred = model.inference([img])
        _sync(torch)
        seconds = time.monotonic() - started
        net = np.asarray(pred.depth[0], dtype=np.float64)
        h, w = net.shape
        k_orig = intrinsics(p)
        sx, sy = w / p["width"], h / p["height"]
        focal_proc = (k_orig[0, 0] * sx + k_orig[1, 1] * sy) / 2
        depth = focal_proc * net / 300.0
        k_proc = k_orig * np.array([[sx], [sy], [1.0]])
        pts = unproject(depth, k_proc)
        # Back to the photo's half size, nearest pixel.
        rows = np.clip(
            ((np.arange(p["height"] // STRIDE) + 0.5) * STRIDE * sy).astype(int), 0, h - 1
        )
        cols = np.clip(
            ((np.arange(p["width"] // STRIDE) + 0.5) * STRIDE * sx).astype(int), 0, w - 1
        )
        pts = pts[rows][:, cols]
        out[f"points_{k}"] = pts.astype(np.float16)
        out[f"valid_{k}"] = np.all(np.isfinite(pts), axis=-1) & (pts[..., 2] > 0)
        out[f"K_{k}"] = k_orig
        out[f"seconds_{k}"] = np.array(seconds)
    return out


def run_da3_mv(photos: list[dict[str, Any]], folder: Path) -> dict[str, Any]:
    torch = importlib.import_module("torch")
    _stub_optional_modules()
    api = importlib.import_module("depth_anything_3.api")
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    model = _cache.get("da3s")
    if model is None:
        model = api.DepthAnything3.from_pretrained("depth-anything/DA3-SMALL").to(device).eval()
        _cache["da3s"] = model
    imgs = [np.asarray(Image.open(folder / p["file"]).convert("RGB")) for p in photos]
    _sync(torch)
    started = time.monotonic()
    pred = model.inference(imgs)
    _sync(torch)
    seconds = time.monotonic() - started
    out: dict[str, Any] = {"seconds_all": np.array(seconds)}
    for k, p in enumerate(photos):
        depth = np.asarray(pred.depth[k], dtype=np.float64)
        h, w = depth.shape
        kk = np.asarray(pred.intrinsics[k], dtype=np.float64)
        w2c = np.eye(4)
        w2c[:3, :4] = np.asarray(pred.extrinsics[k], dtype=np.float64)
        c2w = np.linalg.inv(w2c)
        cam = unproject(depth, kk)
        world = cam @ c2w[:3, :3].T + c2w[:3, 3]
        sx, sy = w / p["width"], h / p["height"]
        rows = np.clip(
            ((np.arange(p["height"] // STRIDE) + 0.5) * STRIDE * sy).astype(int), 0, h - 1
        )
        cols = np.clip(
            ((np.arange(p["width"] // STRIDE) + 0.5) * STRIDE * sx).astype(int), 0, w - 1
        )
        world = world[rows][:, cols]
        conf = np.asarray(pred.conf[k], dtype=np.float64)[rows][:, cols]
        out[f"points_{k}"] = world.astype(np.float32)
        out[f"valid_{k}"] = np.all(np.isfinite(world), axis=-1) & (conf >= np.percentile(conf, 20))
        out[f"K_{k}"] = kk / np.array([[sx], [sy], [1.0]])
        out[f"c2w_{k}"] = c2w
        out[f"seconds_{k}"] = np.array(seconds / len(photos))
    return out


_cache: dict[str, Any] = {}


def main() -> int:
    model, photos_dir, out_dir = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    known_fov = "--known-fov" in sys.argv
    out_dir.mkdir(parents=True, exist_ok=True)
    for meta in sorted(photos_dir.glob("*/photos.json")):
        room = meta.parent.name
        target = out_dir / f"{room}.npz"
        if target.exists():
            continue
        photos = json.loads(meta.read_text())["photos"]
        if not photos:
            continue
        if model == "moge2-s":
            res = run_moge(photos, meta.parent, known_fov)
        elif model == "da3-metric":
            res = run_da3_metric(photos, meta.parent)
        elif model == "da3-small-mv":
            res = run_da3_mv(photos, meta.parent)
        else:
            raise SystemExit(f"unknown model {model}")
        np.savez_compressed(target, **res)
        secs = sum(
            float(v) for k, v in res.items() if k.startswith("seconds_") and k != "seconds_all"
        )
        print(room, len(photos), f"{secs:.2f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
