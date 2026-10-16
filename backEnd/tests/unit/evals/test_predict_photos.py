"""``evals.predict_photos``: the photo model on a directory -> prediction files the harness scores.

Offline: the provider pool is replaced by the fixture vision provider, so the run uses the
real pipeline (image preparation, released prompt, structured output) on the synthetic
fixture photos and writes rooms, component sets, the raw records and the run summary.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from llm_fakes import FixtureVisionProvider, photo_labels

from basis.llm.errors import ProviderUnavailable
from evals import predict_photos

pytestmark = pytest.mark.asyncio

KITCHEN = "photos/01_kitchen.jpg"
BATHROOM = "photos/02_bathroom.jpg"


@pytest.fixture
def photo_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "photos"
    directory.mkdir()
    for name in (KITCHEN, BATHROOM):
        shutil.copy(fixture_path(*name.split("/")), directory / Path(name).name)
    (directory / "notes.txt").write_text("not a photo")
    return directory


def test_photo_files_takes_images_only_in_name_order(photo_dir: Path) -> None:
    assert [p.name for p in predict_photos.photo_files(photo_dir)] == [
        "01_kitchen.jpg",
        "02_bathroom.jpg",
    ]


async def test_run_writes_predictions_in_the_harness_format(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FixtureVisionProvider()
    monkeypatch.setattr(
        predict_photos.ProviderPool, "from_settings", staticmethod(lambda settings: provider)
    )
    out = tmp_path / "out"
    code = await predict_photos.run(
        predict_photos.parse_args(
            ["--photos", str(photo_dir), "--out", str(out), "--repeats", "2", "--concurrency", "2"]
        )
    )
    assert code == 0
    rooms = json.loads((out / "photo_rooms.json").read_text())
    components = json.loads((out / "photo_components.json").read_text())
    labels = photo_labels()
    # Two repeats -> the harness's {"repeats": [...]} form, one value per repeat.
    assert rooms["01_kitchen.jpg"] == {"repeats": [labels[KITCHEN]["room_type"]] * 2}
    assert rooms["02_bathroom.jpg"] == {"repeats": [labels[BATHROOM]["room_type"]] * 2}
    expected = sorted({c["label"] for c in labels[KITCHEN]["components"]})
    assert components["01_kitchen.jpg"] == {"repeats": [expected, expected]}
    raw = [json.loads(line) for line in (out / "photo_raw.jsonl").read_text().splitlines()]
    assert len(raw) == 4 and {r["repeat"] for r in raw} == {1, 2}
    assert all(r["model"] and r["attempts"] == 1 and r["errors"] == [] for r in raw)
    run = json.loads((out / "run.json").read_text())
    assert (run["photos"], run["repeats"], run["prompt"]) == (2, 2, "photo_analysis/v1")
    assert run["spent_usd"] == pytest.approx(4 * 0.01)  # the fixture prices a photo at $0.01
    assert len(provider.requests) == 4


async def test_single_repeat_writes_plain_values(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        predict_photos.ProviderPool,
        "from_settings",
        staticmethod(lambda settings: FixtureVisionProvider()),
    )
    out = tmp_path / "out"
    assert (
        await predict_photos.run(
            predict_photos.parse_args(["--photos", str(photo_dir), "--out", str(out)])
        )
        == 0
    )
    rooms = json.loads((out / "photo_rooms.json").read_text())
    assert rooms["01_kitchen.jpg"] == photo_labels()[KITCHEN]["room_type"]


async def test_no_provider_is_an_error(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        predict_photos.ProviderPool, "from_settings", staticmethod(lambda settings: None)
    )
    assert (
        await predict_photos.run(
            predict_photos.parse_args(["--photos", str(photo_dir), "--out", str(tmp_path / "o")])
        )
        == 2
    )


async def test_prompt_and_model_options_reach_the_request(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FixtureVisionProvider()
    monkeypatch.setattr(
        predict_photos.ProviderPool, "from_settings", staticmethod(lambda settings: provider)
    )
    out = tmp_path / "out"
    code = await predict_photos.run(
        predict_photos.parse_args(
            ["--photos", str(photo_dir), "--out", str(out), "--prompt", "v2", "--model", "gpt-4.1"]
        )
    )
    assert code == 0
    assert {r.prompt_version for r in provider.requests} == {"photo_analysis/v2"}
    assert {r.model for r in provider.requests} == {"gpt-4.1"}
    run = json.loads((out / "run.json").read_text())
    assert (run["prompt"], run["model_requested"]) == ("photo_analysis/v2", "gpt-4.1")


class FlakyProvider:
    """Fails the first ``failures`` calls with a transient error, then answers."""

    name = "flaky"

    def __init__(self, failures: int) -> None:
        self.inner = FixtureVisionProvider()
        self.failures = failures
        self.calls = 0

    async def complete(self, request: Any) -> Any:
        self.calls += 1
        if self.calls <= self.failures:
            raise ProviderUnavailable("timed out")
        return await self.inner.complete(request)


async def test_transient_provider_errors_are_retried_then_recorded(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FlakyProvider(failures=1)
    monkeypatch.setattr(
        predict_photos.ProviderPool, "from_settings", staticmethod(lambda settings: provider)
    )
    monkeypatch.setattr(predict_photos, "RETRY_DELAYS_S", (0.0, 0.0))
    out = tmp_path / "out"
    assert (
        await predict_photos.run(
            predict_photos.parse_args(
                ["--photos", str(photo_dir), "--out", str(out), "--concurrency", "1"]
            )
        )
        == 0
    )
    rooms = json.loads((out / "photo_rooms.json").read_text())
    assert set(rooms) == {"01_kitchen.jpg", "02_bathroom.jpg"}  # the failure was retried
    assert json.loads((out / "run.json").read_text())["errors"] == []

    hopeless = FlakyProvider(failures=99)
    monkeypatch.setattr(
        predict_photos.ProviderPool, "from_settings", staticmethod(lambda settings: hopeless)
    )
    out2 = tmp_path / "out2"
    assert (
        await predict_photos.run(
            predict_photos.parse_args(
                ["--photos", str(photo_dir), "--out", str(out2), "--concurrency", "1"]
            )
        )
        == 0
    )
    run = json.loads((out2 / "run.json").read_text())
    assert [e["file"] for e in run["errors"]] == ["01_kitchen.jpg", "02_bathroom.jpg"]
    assert json.loads((out2 / "photo_rooms.json").read_text()) == {}
    assert hopeless.calls == 2 * (len(predict_photos.RETRY_DELAYS_S) + 1)


async def test_prompt_family_option_selects_a_decomposed_prompt(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FixtureVisionProvider()
    monkeypatch.setattr(
        predict_photos.ProviderPool, "from_settings", staticmethod(lambda settings: provider)
    )
    out = tmp_path / "out"
    code = await predict_photos.run(
        predict_photos.parse_args(
            ["--photos", str(photo_dir), "--out", str(out), "--prompt-name", "photo_countables"]
        )
    )
    assert code == 0
    assert {r.prompt_version for r in provider.requests} == {"photo_countables/v1"}
    assert json.loads((out / "run.json").read_text())["prompt"] == "photo_countables/v1"
