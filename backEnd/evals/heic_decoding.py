"""``python -m evals.heic_decoding``: does Basis read a folder of HEIC photos as Apple does?
(EVAL_REPORT 81.)

For each ``.heic``/``.heif`` file in ``--photos``, three cases:

* ``decode``: :func:`basis.vision.prep.decode_photo` with the EXIF orientation applied, as the
  model's preparation does, against Apple's own decoder (``sips -s format jpeg``, macOS only;
  ``--no-reference`` or another platform skips the comparison). Passed when the size and
  orientation match and the mean pixel difference, both scaled to 256 x 256, is at most
  :data:`MAX_MEAN_DIFFERENCE` of 255. The detail keeps the size, the raw EXIF orientation, the
  difference, the difference had the picture been turned 90, 180 or 270 degrees, and the
  decode time.
* ``prepare``: :func:`basis.vision.prep.prepare_image` succeeds (the model's copy).
* ``cut_short``: the file cut to :data:`CUT_TO` of its bytes is refused as ``not_an_image``.

Nothing is paid and no photo is copied: the run directory keeps numbers and file names only.
The run files under ``verification/heic_decoding/<decoder version>/``.

Usage (from ``backEnd/``)::

    uv run python -m evals.heic_decoding --photos ~/Downloads \\
        --out "$BASIS_EVAL_DATA_DIR/predictions/heic_decoding_<date>"
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Final

import numpy as np
import pi_heif
from PIL import Image, ImageOps

from basis.vision.prep import ImageRejected, decode_photo, prepare_image
from evals import record

MAX_MEAN_DIFFERENCE: Final = 1.0
CUT_TO: Final = 0.7
SIDE: Final = 256


def apple_reference(path: Path, tmp: Path) -> Image.Image:
    """The photo as Apple's ImageIO decodes it, upright."""
    out = tmp / f"{path.stem}.jpg"
    subprocess.run(
        ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "best", str(path)]
        + ["--out", str(out)],
        check=True,
        capture_output=True,
    )
    with Image.open(out) as reference:
        return (ImageOps.exif_transpose(reference) or reference).convert("RGB")


def difference(a: Image.Image, b: Image.Image) -> float:
    left = np.asarray(a.resize((SIDE, SIDE))).astype(float)
    right = np.asarray(b.resize((SIDE, SIDE))).astype(float)
    return float(np.abs(left - right).mean())


def raw_orientation(data: bytes) -> int | None:
    """The EXIF orientation the file carries, before the decoder resets it."""
    heif = pi_heif.open_heif(io.BytesIO(data), apply_transformations=False)
    exif = heif.info.get("exif")
    if not exif:
        return None
    tags = Image.Exif()
    tags.load(exif)
    value = tags.get(0x0112)
    return int(value) if value is not None else None


def check(path: Path, run: record.EvalRun, tmp: Path, reference: bool) -> None:
    data = path.read_bytes()
    name = path.name
    started = time.perf_counter()
    try:
        image = decode_photo(data)
        seconds = time.perf_counter() - started
        ours = (ImageOps.exif_transpose(image) or image).convert("RGB")
    except ImageRejected as exc:
        run.case(f"{name}:decode", "failed", detail={"refused": exc.reason, "message": str(exc)})
        ours = None
    if ours is not None:
        detail: dict[str, Any] = {
            "size": list(ours.size),
            "exif_orientation": raw_orientation(data),
            "decode_ms": round(seconds * 1000),
            "bytes": len(data),
        }
        outcome = "passed"
        if reference:
            try:
                apple = apple_reference(path, tmp)
            except (OSError, subprocess.CalledProcessError) as exc:
                run.case(f"{name}:decode", "test_error", detail={**detail, "reference": str(exc)})
                apple = None
            if apple is not None:
                same = ours.size == apple.size
                mad = difference(ours, apple) if same else None
                turned = {
                    str(k): round(difference(ours.rotate(k, expand=True), apple), 1)
                    for k in (90, 180, 270)
                    if ours.rotate(k, expand=True).size == apple.size
                }
                detail.update(
                    reference_size=list(apple.size), mean_difference=mad, turned_difference=turned
                )
                close = mad is not None and mad <= MAX_MEAN_DIFFERENCE
                outcome = "passed" if same and close else "failed"
                run.case(f"{name}:decode", outcome, detail=detail, seconds=seconds)
        else:
            run.case(f"{name}:decode", outcome, detail=detail, seconds=seconds)
    try:
        prepared = prepare_image(data)
        run.case(f"{name}:prepare", "passed", detail={"size": [prepared.width, prepared.height]})
    except ImageRejected as exc:
        run.case(f"{name}:prepare", "failed", detail={"refused": exc.reason, "message": str(exc)})
    try:
        decode_photo(data[: int(len(data) * CUT_TO)])
        run.case(f"{name}:cut_short", "failed", detail={"kept": CUT_TO, "refused": None})
    except ImageRejected as exc:
        outcome = "passed" if exc.reason == "not_an_image" else "failed"
        run.case(f"{name}:cut_short", outcome, detail={"kept": CUT_TO, "refused": exc.reason})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--photos", type=Path, required=True, help="a folder of HEIC files")
    parser.add_argument("--out", type=Path, required=True, help="the run directory")
    parser.add_argument("--no-reference", action="store_true", help="skip Apple's decoder")
    args = parser.parse_args(argv)
    files = sorted(
        p for p in args.photos.iterdir() if p.is_file() and p.suffix.lower() in (".heic", ".heif")
    )
    reference = not args.no_reference and sys.platform == "darwin"
    version = f"pi-heif-{pi_heif.__version__}"
    run = record.EvalRun.begin(
        args.out, harness=__name__, args=args, component="heic_decoding", version=version
    )
    run.note(
        decoder={"pi_heif": pi_heif.__version__, "libheif": pi_heif.libheif_info().get("libheif")},
        reference="Apple ImageIO (sips)" if reference else None,
        max_mean_difference=MAX_MEAN_DIFFERENCE,
        cut_to=CUT_TO,
    )
    for path in files:  # each file by its digest, not the whole folder
        run.input(path.name, path)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for path in files:
                check(path, run, Path(tmp), reference)
    except BaseException as exc:
        run.finish(None, status="error", exit_code=1, error=repr(exc))
        raise
    summary = record.summarize_cases(run.cases)
    run.finish({"files": len(files), "version": version, **summary})
    print(record.summary_line(summary))
    return 0 if summary.get("failed", 0) == 0 and summary.get("test_error", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
