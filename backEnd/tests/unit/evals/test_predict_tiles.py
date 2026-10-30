"""``evals.predict_tiles``: enlarged grid parts of each photo -> summed or joint counts."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from llm_fakes import PHOTO_MODEL, PHOTO_USAGE, analysis_for
from PIL import Image

from basis.llm import prompts
from basis.llm.errors import ProviderBadResponse
from basis.llm.pool import ProviderPool
from basis.llm.provider import ChatRequest, ChatResponse
from basis.vision.prep import prepare_image
from evals import predict_tiles


def jpeg(width: int, height: int, color: tuple[int, int, int] = (200, 100, 50)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_tiles_cut_a_grid_and_enlarge_each_part() -> None:
    parts = predict_tiles.tiles(prepare_image(jpeg(800, 600)), 2, 2.0)
    assert [(t.row, t.col) for t in parts] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert all((t.image.width, t.image.height) == (800, 600) for t in parts)
    assert parts[0].position == "top-left quarter"
    assert parts[3].position == "bottom-right quarter"
    assert parts[0].cut_edges == ["bottom", "right"]
    assert parts[3].cut_edges == ["top", "left"]


def test_tiles_keep_the_one_megapixel_cap_and_odd_sizes() -> None:
    parts = predict_tiles.tiles(prepare_image(jpeg(801, 409)), 2, 3.0)
    # 400 or 401 wide parts, enlarged 3x, then capped at 1024 px by prepare_image
    assert max(max(t.image.width, t.image.height) for t in parts) <= 1024
    assert {t.image.width for t in parts} == {1024}
    middle = predict_tiles.tiles(prepare_image(jpeg(900, 900)), 3, 1.0)
    assert middle[4].cut_edges == ["top", "bottom", "left", "right"]
    assert middle[4].position == "part in row 2 of 3, column 2 of 3"


def test_tiles_validate_arguments() -> None:
    image = prepare_image(jpeg(100, 100))
    with pytest.raises(ValueError, match="grid"):
        predict_tiles.tiles(image, 0, 2.0)
    with pytest.raises(ValueError, match="upscale"):
        predict_tiles.tiles(image, 2, 0.5)


def test_tile_text_names_the_cut_edges() -> None:
    parts = predict_tiles.tiles(prepare_image(jpeg(800, 600)), 2, 2.0)
    text = predict_tiles.tile_text(parts[1], 2.0)
    assert "top-right quarter" in text and "enlarged 2 times" in text
    assert "bottom, left edges are cuts" in text
    whole = predict_tiles.tiles(prepare_image(jpeg(800, 600)), 1, 1.0)[0]
    assert "whole photo" in predict_tiles.tile_text(whole, 1.0)


@dataclass
class CountingProvider:
    """Answers every request with ``windows`` windows (per request), optionally failing
    the first ``bad`` requests with a bad response."""

    windows: int = 1
    bad: int = 0
    name: str = "fake"
    requests: list[ChatRequest] = field(default_factory=list)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if len(self.requests) <= self.bad:
            raise ProviderBadResponse("garbled", usage=PHOTO_USAGE, raw={"bad": True})
        entry = {
            "room_type": "kitchen",
            "floor": None,
            "components": [
                {"label": "window", "count": self.windows},
                {"label": "sink", "count": 1},
            ],
        }
        analysis = analysis_for(entry)
        return ChatResponse(
            parsed=analysis,
            text=analysis.model_dump_json(),
            usage=PHOTO_USAGE,
            model=PHOTO_MODEL,
            provider=self.name,
        )


@pytest.fixture
def photo_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "photos"
    directory.mkdir()
    (directory / "a.jpg").write_bytes(jpeg(800, 600))
    (directory / "b.jpg").write_bytes(jpeg(800, 600, (10, 20, 30)))
    return directory


def use(monkeypatch: pytest.MonkeyPatch, provider: CountingProvider) -> None:
    monkeypatch.setattr(ProviderPool, "from_settings", staticmethod(lambda settings: provider))


@pytest.mark.asyncio
async def test_separate_mode_sums_the_parts(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = CountingProvider(windows=1)
    use(monkeypatch, provider)
    out = tmp_path / "out"
    args = ["--photos", str(photo_dir), "--out", str(out), "--mode", "separate", "--repeats", "2"]
    assert await predict_tiles.run(predict_tiles.parse_args(args)) == 0
    assert len(provider.requests) == 2 * 2 * 4
    request = provider.requests[0]
    assert request.prompt_version == "photo_countables_tile/v1"
    assert len(request.messages[0].images) == 1
    counts = json.loads((out / "photo_counts.json").read_text())
    assert counts["a.jpg"] == {"repeats": [{"window": 4, "sink": 4}] * 2}
    tile_counts = json.loads((out / "tile_counts.json").read_text())
    assert tile_counts["a.jpg"]["repeats"][0] == [{"window": 1, "sink": 1}] * 4
    assert json.loads((out / "photo_components.json").read_text())["b.jpg"] == {
        "repeats": [["sink", "window"]] * 2
    }
    assert not (out / "photo_rooms.json").exists()
    run = json.loads((out / "run.json").read_text())
    assert (run["calls"], run["missing"], run["models_reported"]) == (16, [], [PHOTO_MODEL])
    assert run["spent_usd"] == pytest.approx(16 * 0.01)
    # The run record (evals.record): one ledger line per call, the prompt declared.
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.predict_tiles"
    assert manifest["calls"]["count"] == 16
    assert manifest["components"]["prompts"] == [
        {
            "name": "photo_countables_tile",
            "version": "v1",
            "sha256": prompts.load("photo_countables_tile", "v1").sha256,
            "calls": 16,
        }
    ]
    assert [i["name"] for i in manifest["data"]["inputs"]] == ["photos"]
    assert len((out / "llm_calls.jsonl").read_text().splitlines()) == 16
    assert "wrote" in (out / "run.log").read_text()


@pytest.mark.asyncio
async def test_joint_mode_sends_the_photo_and_its_parts_in_one_call(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = CountingProvider(windows=3)
    use(monkeypatch, provider)
    out = tmp_path / "out"
    args = ["--photos", str(photo_dir), "--out", str(out), "--mode", "joint"]
    assert await predict_tiles.run(predict_tiles.parse_args(args)) == 0
    assert len(provider.requests) == 2
    request = provider.requests[0]
    assert request.prompt_version == "photo_countables_joint/v1"
    images = request.messages[0].images
    assert len(images) == 5
    assert "top-left quarter, top-right quarter" in request.messages[0].content
    counts = json.loads((out / "photo_counts.json").read_text())
    assert counts["a.jpg"] == {"window": 3, "sink": 1}
    assert json.loads((out / "photo_rooms.json").read_text()) == {
        "a.jpg": "kitchen",
        "b.jpg": "kitchen",
    }


@pytest.mark.asyncio
async def test_an_unusable_answer_is_retried_once(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = CountingProvider(windows=2, bad=1)
    use(monkeypatch, provider)
    out = tmp_path / "out"
    args = ["--photos", str(photo_dir), "--out", str(out), "--mode", "joint", "--concurrency", "1"]
    assert await predict_tiles.run(predict_tiles.parse_args(args)) == 0
    assert len(provider.requests) == 3
    raw = [json.loads(line) for line in (out / "photo_raw.jsonl").read_text().splitlines()]
    retried = [c for r in raw for c in r["calls"] if c["attempts"] == 2]
    assert len(retried) == 1 and "garbled" in retried[0]["errors"][0]
    assert json.loads((out / "run.json").read_text())["missing"] == []


@pytest.mark.asyncio
async def test_a_part_unusable_twice_makes_the_photo_missing_not_zero(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = CountingProvider(windows=1, bad=2)
    use(monkeypatch, provider)
    out = tmp_path / "out"
    args = [
        "--photos", str(photo_dir), "--out", str(out), "--mode", "joint", "--concurrency", "1",
    ]  # fmt: skip
    assert await predict_tiles.run(predict_tiles.parse_args(args)) == 0
    assert json.loads((out / "run.json").read_text())["missing"] == ["a.jpg#1"]
    assert "a.jpg" not in json.loads((out / "photo_counts.json").read_text())


@pytest.mark.asyncio
async def test_seconds_are_call_time_not_queue_time(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, CountingProvider())
    out = tmp_path / "out"
    args = ["--photos", str(photo_dir), "--out", str(out), "--mode", "separate"]
    assert await predict_tiles.run(predict_tiles.parse_args(args)) == 0
    raw = [json.loads(line) for line in (out / "photo_raw.jsonl").read_text().splitlines()]
    for record in raw:
        assert all("seconds" in c for c in record["calls"])
        assert record["seconds"] == pytest.approx(
            sum(c["seconds"] for c in record["calls"]), abs=0.05
        )
