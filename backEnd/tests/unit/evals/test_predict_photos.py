"""``evals.predict_photos``: the photo model on a directory -> prediction files the harness scores.

Offline: the provider pool is replaced by the fixture vision provider, so the run uses the
real pipeline (image preparation, released prompt, structured output) on the synthetic
fixture photos and writes rooms, component sets, the raw records and the run summary.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from llm_fakes import FixtureVisionProvider, photo_labels

from basis.llm import prompts
from basis.llm.errors import ProviderAccountError, ProviderRequestRejected, ProviderUnavailable
from evals import account_stop, predict_photos

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


def results_of(out: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((out / "results.json").read_text())
    return data


def counts_of(out: Path) -> tuple[int, int, int]:
    summary = results_of(out)["summary"]
    return summary["passed"], summary["failed"], summary["test_error"]


def test_photo_outcome_fails_the_invalid_output_fallback_only() -> None:
    assert predict_photos.photo_outcome({"room_type": "kitchen", "review_reason": None}) == "passed"
    assert predict_photos.photo_outcome({"review_reason": "low_confidence"}) == "passed"
    assert predict_photos.photo_outcome({"review_reason": "invalid_model_output"}) == "failed"
    assert (
        predict_photos.photo_outcome({"review_reason": "invalid_model_output:countables"})
        == "failed"
    )


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
    # The run record (evals.record): the manifest names the harness, the prompt and the
    # inputs; the ledger has one line per call; the log has the terminal output.
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.predict_photos"
    assert manifest["status"] == "ok" and manifest["calls"]["count"] == 4
    released = prompts.load("photo_analysis", "v1")
    assert manifest["components"]["prompts"] == [
        {"name": "photo_analysis", "version": "v1", "sha256": released.sha256, "calls": 4}
    ]
    [photos_input] = manifest["data"]["inputs"]
    assert (photos_input["name"], photos_input["files"]) == ("photos", 3)  # notes.txt too
    assert len((out / "llm_calls.jsonl").read_text().splitlines()) == 4
    assert "wrote" in (out / "console.log").read_text()
    # One case per (photo, repeat), every one passed; the report carries the summary line;
    # the run files under the prompt that made the calls (no data directory: no link).
    results = results_of(out)
    assert results["summary"]["total"] == 4 and counts_of(out) == (4, 0, 0)
    assert {c["id"] for c in results["cases"]} == {
        "01_kitchen.jpg#1",
        "01_kitchen.jpg#2",
        "02_bathroom.jpg#1",
        "02_bathroom.jpg#2",
    }
    assert all(c["kind"] == "photo" and c["seconds"] >= 0 for c in results["cases"])
    assert results["cases"][0]["detail"]["room_type"] == labels[KITCHEN]["room_type"]
    assert (
        "**results: 4 passed, 0 failed, 0 test errors (4 cases)**"
        in (out / "report.md").read_text()
    )
    assert manifest["results"] == results["summary"]
    assert manifest["tree"] == {"component": "photo_analysis", "version": "v1", "link": None}


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


async def test_a_claude_model_is_requested_as_is_and_the_effort_recorded(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FixtureVisionProvider()
    monkeypatch.setattr(
        predict_photos.ProviderPool, "from_settings", staticmethod(lambda settings: provider)
    )
    monkeypatch.setenv("ANTHROPIC_EFFORT", "medium")
    out = tmp_path / "out"
    code = await predict_photos.run(
        predict_photos.parse_args(
            ["--photos", str(photo_dir), "--out", str(out), "--model", "claude-fable-5-1"]
        )
    )
    assert code == 0
    assert {r.model for r in provider.requests} == {"claude-fable-5-1"}
    assert {r.reasoning_effort for r in provider.requests} == {None}  # the provider's default
    run = json.loads((out / "run.json").read_text())
    # Without --reasoning-effort the Anthropic default (ANTHROPIC_EFFORT) is what ran.
    assert (run["model_requested"], run["reasoning_effort"]) == ("claude-fable-5-1", "medium")
    out2 = tmp_path / "out2"
    args = ["--photos", str(photo_dir), "--out", str(out2), "--model", "claude-opus-5-5"]
    assert (
        await predict_photos.run(predict_photos.parse_args([*args, "--reasoning-effort", "low"]))
        == 0
    )
    assert {r.reasoning_effort for r in provider.requests[2:]} == {"low"}
    assert json.loads((out2 / "run.json").read_text())["reasoning_effort"] == "low"
    # models_reported comes from the recorder: the fixture answers as its own model, which
    # is how a run that silently fell back to another provider would show up.
    assert run["models_reported"] == ["gpt-4.1-nano"]


async def test_openai_runs_record_no_effort(
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
            predict_photos.parse_args(
                ["--photos", str(photo_dir), "--out", str(out), "--model", "gpt-4.1"]
            )
        )
        == 0
    )
    assert json.loads((out / "run.json").read_text())["reasoning_effort"] is None


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
    assert counts_of(out) == (2, 0, 0)  # a retried outage is not a case of anything

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
    # An outage after every retry says nothing about the model: a test error, with the
    # provider's catalogue code, never a failure.
    results = results_of(out2)
    assert results["summary"]["total"] == 2 and counts_of(out2) == (0, 0, 2)
    assert [(c["id"], c["observed_error"]) for c in results["cases"]] == [
        ("01_kitchen.jpg#1", "BASIS-PROVIDER-007"),
        ("02_bathroom.jpg#1", "BASIS-PROVIDER-007"),
    ]
    assert "timed out" in results["cases"][0]["observed_message"]
    assert "results: 0 passed, 0 failed, 2 test errors" in (out2 / "report.md").read_text()


QUOTA = ProviderAccountError(
    "You exceeded your current quota.", provider="openai", status=429, code="insufficient_quota"
)


class BrokeAfter:
    """Answers the first ``answers`` calls, then refuses the account on every call."""

    name = "broke"

    def __init__(self, answers: int) -> None:
        self.inner = FixtureVisionProvider()
        self.answers = answers
        self.calls = 0

    async def complete(self, request: Any) -> Any:
        self.calls += 1
        if self.calls > self.answers:
            raise QUOTA
        return await self.inner.complete(request)


async def test_the_first_account_error_stops_the_run_with_a_partial_run_json(
    photo_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """EVAL_REPORT 26.12: the run used to go on, record every later photo as
    ``invalid_model_output`` and exit 0. Now: one refused call, then stop."""
    provider = BrokeAfter(answers=2)  # repeat 1 (two photos) answers, repeat 2 is refused
    monkeypatch.setattr(
        predict_photos.ProviderPool, "from_settings", staticmethod(lambda settings: provider)
    )
    monkeypatch.setattr(predict_photos, "RETRY_DELAYS_S", (0.0, 0.0))
    out = tmp_path / "out"
    (out).mkdir()
    (out / "run.json").write_text(json.dumps({"complete": True, "stale": "an earlier run"}))
    code = await predict_photos.run(
        predict_photos.parse_args(
            ["--photos", str(photo_dir), "--out", str(out), "--repeats", "2", "--concurrency", "1"]
        )
    )
    assert code == account_stop.ACCOUNT_STOPPED
    assert provider.calls == 3  # the refused call is not retried and nothing follows it
    run = json.loads((out / "run.json").read_text())
    assert run["complete"] is False and "stale" not in run
    assert run["stopped"] == {
        "reason": "provider_account_error",
        "provider": "openai",
        "status": 429,
        "code": "insufficient_quota",
        "message": str(QUOTA),
    }
    assert run["spent_usd"] > 0  # repeat 1's two calls
    assert "photo_raw.jsonl" in run["files_written"]
    raw = (out / "photo_raw.jsonl").read_text().splitlines()
    assert [json.loads(line)["repeat"] for line in raw] == [1, 1]  # repeat 1 only
    assert not (out / "photo_rooms.json").exists()  # no prediction file looks complete
    err = capsys.readouterr().err
    assert "STOPPED: openai account error (HTTP 429, insufficient_quota)" in err
    # The results hold what ran before the stop: repeat 1's two cases, nothing for repeat 2.
    assert counts_of(out) == (2, 0, 0)
    assert json.loads((out / "manifest.json").read_text())["status"] == "stopped"


class RefusesFirstPhoto:
    """Refuses the input of the first call (a content filter), answers the rest."""

    name = "refuses"

    def __init__(self) -> None:
        self.inner = FixtureVisionProvider()
        self.calls = 0

    async def complete(self, request: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            raise ProviderRequestRejected(
                "The response was filtered", provider="azure", status=400, code="content_filter"
            )
        return await self.inner.complete(request)


async def test_a_refused_photo_is_recorded_as_rejected_and_the_run_goes_on(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Like a photo the preparation rejects: recorded, not retried, never an analysis with
    no component; the other photos are analyzed and the run completes."""
    provider = RefusesFirstPhoto()
    monkeypatch.setattr(
        predict_photos.ProviderPool, "from_settings", staticmethod(lambda settings: provider)
    )
    out = tmp_path / "out"
    code = await predict_photos.run(
        predict_photos.parse_args(
            ["--photos", str(photo_dir), "--out", str(out), "--concurrency", "1"]
        )
    )
    assert code == 0 and provider.calls == 2
    raw = [json.loads(line) for line in (out / "photo_raw.jsonl").read_text().splitlines()]
    assert raw[0]["file"] == "01_kitchen.jpg" and "content_filter" in raw[0]["rejected"]
    assert "analysis" not in raw[0] and "analysis" in raw[1]
    # The refused photo is the system's failure (production fails it the same way), with
    # the rejection's catalogue code; the other photo passed.
    results = results_of(out)
    assert counts_of(out) == (1, 1, 0)
    refused, analyzed = results["cases"]
    assert (refused["id"], refused["outcome"], refused["observed_error"]) == (
        "01_kitchen.jpg#1",
        "failed",
        "BASIS-PROVIDER-005",
    )
    assert (analyzed["id"], analyzed["outcome"]) == ("02_bathroom.jpg#1", "passed")
    assert "| `01_kitchen.jpg#1` | photo | failed |" in (out / "report.md").read_text()


class HangThenRefuse:
    """The first photo's call hangs; the second is refused. Records the cancel."""

    name = "hang"

    def __init__(self) -> None:
        self.calls = 0
        self.cancelled = False

    async def complete(self, request: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        raise QUOTA


async def test_an_account_error_cancels_the_calls_still_in_flight(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = HangThenRefuse()
    monkeypatch.setattr(
        predict_photos.ProviderPool, "from_settings", staticmethod(lambda settings: provider)
    )
    out = tmp_path / "out"
    code = await asyncio.wait_for(
        predict_photos.run(
            predict_photos.parse_args(
                ["--photos", str(photo_dir), "--out", str(out), "--concurrency", "2"]
            )
        ),
        timeout=10,
    )
    assert code == account_stop.ACCOUNT_STOPPED
    assert provider.cancelled and provider.calls == 2
    assert json.loads((out / "run.json").read_text())["complete"] is False


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
    latest = prompts.versions("photo_countables")[-1]
    assert {r.prompt_version for r in provider.requests} == {f"photo_countables/{latest}"}
    assert json.loads((out / "run.json").read_text())["prompt"] == f"photo_countables/{latest}"


async def test_reasoning_effort_and_max_tokens_reach_the_request(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FixtureVisionProvider()
    monkeypatch.setattr(
        predict_photos.ProviderPool, "from_settings", staticmethod(lambda settings: provider)
    )
    out = tmp_path / "out"
    code = await predict_photos.run(
        predict_photos.parse_args(
            [
                "--photos",
                str(photo_dir),
                "--out",
                str(out),
                "--model",
                "gpt-6.1-sol",
                "--reasoning-effort",
                "low",
                "--max-tokens",
                "6000",
            ]
        )
    )
    assert code == 0
    assert {(r.reasoning_effort, r.max_tokens) for r in provider.requests} == {("low", 6000)}
    summary = json.loads((out / "run.json").read_text())
    assert (summary["reasoning_effort"], summary["max_tokens"]) == ("low", 6000)


async def test_photo_counts_file_adds_up_same_label_entries(
    photo_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FixtureVisionProvider()
    monkeypatch.setattr(
        predict_photos.ProviderPool, "from_settings", staticmethod(lambda settings: provider)
    )
    out = tmp_path / "out"
    assert (
        await predict_photos.run(
            predict_photos.parse_args(["--photos", str(photo_dir), "--out", str(out)])
        )
        == 0
    )
    counts = json.loads((out / "photo_counts.json").read_text())
    raw = [json.loads(line) for line in (out / "photo_raw.jsonl").read_text().splitlines()]
    for record in raw:
        expected = predict_photos.label_counts(record["analysis"]["components"])
        assert counts[record["file"]] == expected and sum(expected.values()) >= 1
    assert predict_photos.label_counts([{"label": "x", "count": 2}, {"label": "x"}]) == {"x": 3}
