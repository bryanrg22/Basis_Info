"""``python -m evals.appraisal.run`` end to end on a fabricated PDF (no network).

* replay from a hand-written Azure DI recording and truth (``--azure-only``, the CLI);
* a live run with fake clients (an SDK-shaped Azure DI result, a scripted model) records,
  analyzes Azure DI once over two repeats, and a replay reproduces its predictions;
* a missing recording fails only its document; a set without a truth file is skipped;
* the baseline: accepted, then a regression is reported (and refused with failures).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from basis.config import Settings
from basis.extraction.fallback import FallbackAnswer, FallbackValue
from basis.llm import ScriptedProvider, ScriptedTurn
from evals.appraisal import baseline, recordings
from evals.appraisal.run import LiveDeps, Options, main, run_eval
from evals.appraisal.truth import dump_truth

from .appraisal_support import truth_doc, truth_set

LINES = ["Cost Approach", "Opinion of Site Value = $ 85,000", "Improvements", "GLA 1,740 sq ft"]


def make_pdf(path: Path, lines: list[str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=LETTER)
    for i, line in enumerate(lines):
        pdf.drawString(72, 720 - 18 * i, line)
    pdf.showPage()
    pdf.save()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_azure(lines: list[str]) -> dict[str, Any]:
    region = [{"pageNumber": 1, "polygon": []}]
    return {
        "apiVersion": "2024-11-30",
        "modelId": "prebuilt-layout",
        "content": "\n".join(lines),
        "pages": [
            {
                "pageNumber": 1,
                "spans": [],
                "lines": [{"content": line, "polygon": [], "spans": []} for line in lines],
            }
        ],
        "keyValuePairs": [
            {
                "key": {"content": "Opinion of Site Value", "boundingRegions": region, "spans": []},
                "value": {"content": "$ 85,000", "boundingRegions": region, "spans": []},
                "confidence": 0.97,
            }
        ],
        "tables": [],
    }


TRUTH = {
    "cost_approach.site_value": {
        "status": "present",
        "type": "currency",
        "value": 85000,
        "printed": "85,000",
        "page": 1,
    },
    "improvements.gla_sqft": {"status": "present", "type": "int", "value": 1740, "page": 1},
}


def setup_data(tmp_path: Path, *, second: bool = False) -> tuple[Path, str]:
    """The data dir with doc_a (and doc_b, never recorded) and the synthetic truth file."""
    appraisal = tmp_path / "appraisal"
    sha = make_pdf(appraisal / "synthetic_1004/doc_a/appraisal.pdf", LINES)
    docs = [truth_doc(TRUTH, document="doc_a", pages=1, sha256=sha)]
    if second:
        other = make_pdf(appraisal / "synthetic_1004/doc_b/appraisal.pdf", ["Another form"])
        docs.append(truth_doc({}, document="doc_b", pages=1, sha256=other))
    dump_truth(truth_set(*docs), appraisal / "truth/synthetic_1004.json")
    return appraisal, sha


class FakeRawAzure:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.calls = 0

    async def analyze_raw(self, pdf_bytes: bytes) -> Any:
        self.calls += 1
        return recordings.rebuild_sdk_result(self.raw)


def gla_answer() -> ScriptedTurn:
    value = FallbackValue(key="improvements.gla_sqft", value="1,740", page=1, confidence=0.9)
    return ScriptedTurn(parsed=FallbackAnswer(fields=[value]))


def outcome(score: dict[str, Any], key: str) -> str:
    rows = [r for r in score["fields"] if r["key"] == key and r["repeat"] == 1]
    assert len(rows) == 1
    return str(rows[0]["outcome"])


def test_replay_from_a_hand_written_recording_via_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    appraisal, sha = setup_data(tmp_path)
    directory = recordings.recording_dir(appraisal, sha)
    directory.mkdir(parents=True)
    (directory / recordings.AZURE_RAW).write_text(json.dumps(raw_azure(LINES)))
    out = tmp_path / "run"
    code = main(
        ["--set", "all", "--mode", "replay", "--azure-only", "--out", str(out)]
        + ["--data-dir", str(tmp_path)]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "skipping set client: no truth file" in printed
    assert "skipping set gse_d1" in printed
    for name in ("predictions.json", "score.json", "report.md", "run.json"):
        assert (out / name).is_file(), name
    score = json.loads((out / "score.json").read_text())
    assert outcome(score, "cost_approach.site_value") == "correct"
    assert outcome(score, "improvements.gla_sqft") == "missed"  # no model: --azure-only
    predictions = json.loads((out / "predictions.json").read_text())
    site = predictions["documents"][0]["repeats"][0]["fields"]["cost_approach.site_value"]
    assert (site["source"], site["page"], site["value_num"]) == ("azure", 1, "85000")
    run = json.loads((out / "run.json").read_text())
    assert run["systems"] == {"azure_di": True, "llm": False, "azure_1004": False}
    assert run["sets"] == ["synthetic_1004"] and run["git_sha"]
    assert "85,000" not in (out / "report.md").read_text()


async def test_live_records_and_replay_reproduces(tmp_path: Path, settings: Settings) -> None:
    appraisal, sha = setup_data(tmp_path)
    azure = FakeRawAzure(raw_azure(LINES))
    provider = ScriptedProvider([gla_answer(), gla_answer()], model="gpt-4o-mini")
    live = await run_eval(
        opts(tmp_path, mode="live", repeats=2, out=tmp_path / "live"),
        settings=settings,
        live=LiveDeps(azure=azure, provider=provider),
    )
    assert live is not None
    assert azure.calls == 1  # analyzed once; the second repeat reuses the analysis
    assert len(provider.requests) == 2
    directory = recordings.recording_dir(appraisal, sha)
    assert (directory / recordings.AZURE_RAW).is_file()
    assert len(list((directory / recordings.LLM_DIR).glob("*.json"))) == 1
    assert recordings.read_context(directory)["text_model"] == settings.text_model  # type: ignore[index]
    assert outcome(live.score, "cost_approach.site_value") == "correct"
    assert outcome(live.score, "improvements.gla_sqft") == "correct"
    assert live.score["repeats"]["repeats"] == 2
    assert live.run["llm_calls"] == 2 and live.run["cost_usd"] > 0
    assert live.run["models"] == ["gpt-4o-mini"]
    assert live.run["azure_di_calls"] == 1

    replay = await run_eval(
        opts(tmp_path, out=tmp_path / "replay"),
        settings=settings,
    )
    assert replay is not None
    live_fields = _fields(tmp_path / "live", repeat=1)
    assert _fields(tmp_path / "replay", repeat=1) == live_fields
    assert replay.run["cost_usd"] == 0.0 and replay.run["replayed_cost_usd"] > 0
    # Recorded under one model setting, replayed under another: the recorded context wins.
    other = settings.model_copy(update={"text_model": "gpt-4.1"})
    again = await run_eval(
        opts(tmp_path, out=tmp_path / "again"),
        settings=other,
    )
    assert again is not None and _fields(tmp_path / "again", repeat=1) == live_fields


async def test_missing_recording_fails_only_its_document(
    tmp_path: Path, settings: Settings
) -> None:
    appraisal, sha = setup_data(tmp_path, second=True)
    directory = recordings.recording_dir(appraisal, sha)
    directory.mkdir(parents=True)
    (directory / recordings.AZURE_RAW).write_text(json.dumps(raw_azure(LINES)))
    # doc_a has its Azure DI recording but no model answer: the fallback request misses.
    output = await run_eval(
        opts(tmp_path, out=tmp_path / "r"),
        settings=settings,
    )
    assert output is not None
    failed = {f["document"]: f["status"] for f in output.score["documents"]["failed"]}
    assert failed == {"doc_a": "missing_recording", "doc_b": "missing_recording"}
    # --azure-only needs no model answer: doc_a is scored, doc_b still lacks Azure DI.
    output = await run_eval(
        opts(tmp_path, azure_only=True, out=tmp_path / "s"),
        settings=settings,
    )
    assert output is not None
    assert output.score["documents"]["scored"] == 1
    assert [f["document"] for f in output.score["documents"]["failed"]] == ["doc_b"]


async def test_a_changed_pdf_is_refused(tmp_path: Path, settings: Settings) -> None:
    appraisal, _ = setup_data(tmp_path)
    make_pdf(appraisal / "synthetic_1004/doc_a/appraisal.pdf", ["changed"])
    output = await run_eval(
        opts(tmp_path, out=tmp_path / "r"),
        settings=settings,
    )
    assert output is not None
    assert output.score["documents"]["failed"][0]["status"] == "sha256_mismatch"


async def test_baseline_accept_and_regression(tmp_path: Path, settings: Settings) -> None:
    appraisal, sha = setup_data(tmp_path, second=True)
    directory = recordings.recording_dir(appraisal, sha)
    directory.mkdir(parents=True)
    (directory / recordings.AZURE_RAW).write_text(json.dumps(raw_azure(LINES)))
    base = opts(tmp_path, azure_only=True)
    # doc_b has no recording: accepting a baseline with a failed document is refused.
    with pytest.raises(ValueError, match="failed documents"):
        await run_eval(replace(base, accept_baseline=True, out=tmp_path / "1"), settings=settings)
    only_a = replace(base, documents=("doc_a",))
    first = await run_eval(
        replace(only_a, accept_baseline=True, out=tmp_path / "2"), settings=settings
    )
    assert first is not None and first.regressions == []
    accepted = baseline.load(appraisal / baseline.FILE)
    assert accepted is not None
    assert accepted["synthetic_1004/doc_a/cost_approach.site_value"] == "correct"
    assert set(json.loads((appraisal / baseline.FILE).read_text())) == {
        "schema",
        "accepted",
        "run",
        "git_sha",
        "outcomes",
    }
    # The truth changes under the recording: the site value no longer matches.
    changed = dict(TRUTH)
    changed["cost_approach.site_value"] = {**TRUTH["cost_approach.site_value"], "value": 86000}
    dump_truth(
        truth_set(truth_doc(changed, document="doc_a", pages=1, sha256=sha)),
        appraisal / "truth/synthetic_1004.json",
    )
    second = await run_eval(replace(only_a, out=tmp_path / "3"), settings=settings)
    assert second is not None
    assert second.regressions == [
        {
            "field": "synthetic_1004/doc_a/cost_approach.site_value",
            "baseline": "correct",
            "now": "wrong",
        }
    ]


def test_cli_reports_a_refused_baseline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    setup_data(tmp_path)  # doc_a has no recording: it fails, so no baseline is accepted
    argv = ["--set", "synthetic_1004", "--mode", "replay", "--accept-baseline"]
    code = main(argv + ["--out", str(tmp_path / "r"), "--data-dir", str(tmp_path)])
    assert code == 1
    assert "error: not accepting a baseline with failed documents" in capsys.readouterr().out
    assert not (tmp_path / "appraisal" / baseline.FILE).exists()


def test_cli_refuses_bad_flags() -> None:
    for argv in (
        ["--set", "client", "--mode", "replay", "--repeats", "2"],
        ["--set", "client", "--mode", "live", "--no-azure", "--azure-only"],
        ["--set", "client", "--mode", "live", "--repeats", "0"],
        ["--set", "nope", "--mode", "live"],
    ):
        with pytest.raises(SystemExit):
            main(argv)


def opts(tmp_path: Path, **changes: Any) -> Options:
    base = Options(sets=("synthetic_1004",), mode="replay", data_dir=tmp_path)
    return replace(base, **changes)


def _fields(run_dir: Path, *, repeat: int) -> dict[str, Any]:
    data = json.loads((run_dir / "predictions.json").read_text())
    return {
        d["document"]: next(r for r in d["repeats"] if r["repeat"] == repeat)["fields"]
        for d in data["documents"]
    }
