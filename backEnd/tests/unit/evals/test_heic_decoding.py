"""``evals.heic_decoding`` on the synthetic HEIC fixtures, without Apple's decoder: three
cases per file (decode, prepare, cut short), all passed, and a damaged file fails its decode
and prepare cases rather than stopping the run (EVAL_REPORT 81)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fixtures import heic_fixture

from evals import heic_decoding

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "heic"


def run(photos: Path, out: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, dict]:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)  # no verification-tree link
    code = heic_decoding.main(["--photos", str(photos), "--out", str(out), "--no-reference"])
    return code, json.loads((out / "results.json").read_text(encoding="utf-8"))


def test_every_fixture_passes_its_three_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, results = run(FIXTURES, tmp_path / "run", monkeypatch)
    ids = sorted(c["id"] for c in results["cases"])
    assert code == 0
    assert len(ids) == 9 and all(c["outcome"] == "passed" for c in results["cases"])
    decode = next(c for c in results["cases"] if c["id"] == "rotated_90cw.heic:decode")
    assert decode["detail"]["size"] == [240, 320]
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["harness"]["module"] == "evals.heic_decoding"


def test_a_damaged_file_fails_its_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    shutil.copy(FIXTURES / "photo.heic", photos / "photo.heic")
    (photos / "broken.heic").write_bytes(heic_fixture("photo.heic")[:300])
    code, results = run(photos, tmp_path / "run", monkeypatch)
    outcomes = {c["id"]: c["outcome"] for c in results["cases"]}
    assert code == 1
    assert outcomes["broken.heic:decode"] == "failed"
    assert outcomes["broken.heic:prepare"] == "failed"
    assert outcomes["photo.heic:decode"] == "passed"
