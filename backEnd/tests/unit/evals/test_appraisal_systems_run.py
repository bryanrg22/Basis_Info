"""The sweep's scorer additions and the runner's ``--system`` flags (no network).

* highlight correct on a reportlab PDF (pdfplumber words): correct, wrong box, wrong page,
  ``also_on``, no region, n/a without regions, a checkbox enum's label; its report rows;
* usage: model dollars, tokens, Azure DI pages priced per model id, p50/p95 latency;
* ``run.py``: a live ``llm_all`` run with fake clients records the layout and the model call
  and its replay reproduces the predictions (regions included); ``azure_1004`` records and
  replays its own analysis; ``vote`` replays two voters from one recording directory;
  argument validation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from basis.config import Settings
from basis.llm import ScriptedProvider, ScriptedTurn
from evals.appraisal import recordings
from evals.appraisal.run import LiveDeps, Options, _pdf_words, parse_args, run_eval
from evals.appraisal.score import (
    PageWords,
    highlight_status,
    render_report,
    score_run,
    usage_metrics,
)
from evals.appraisal.systems import ModelChoice, SystemSpec, VoterSpec
from evals.appraisal.systems.grounded import GroundedAnswer, GroundedValue
from evals.appraisal.truth import FieldTruth, dump_truth

from .appraisal_support import field, prediction_doc, predictions, repeat, truth_doc, truth_set
from .appraisal_systems_support import pdf_bytes, raw_layout
from .test_appraisal_systems_mortgage import mortgage_raw

FORM = [
    "Subject",
    "Occupant :selected: Owner :unselected: Tenant",
    "Contract",
    "Contract Price $ 412,000",
]


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    path = tmp_path / "form.pdf"
    path.write_bytes(pdf_bytes([FORM, ["Page two"]]))
    return path


def around(words: PageWords, *texts: str) -> list[float]:
    """A page-fraction box around the named words (a small margin)."""
    picked = [w for w in words.words if w[0] in texts]
    assert picked, texts
    x0 = min(w[1] for w in picked) - 2
    top = min(w[2] for w in picked) - 2
    x1 = max(w[3] for w in picked) + 2
    bottom = max(w[4] for w in picked) + 2
    return [x0 / words.width, top / words.height, x1 / words.width, bottom / words.height]


def truth(key: str, **entry: Any) -> FieldTruth:
    return FieldTruth.model_validate({"key": key, "status": "present", "page": 1, **entry})


PRICE = dict(type="currency", value=412000, printed="412,000")


def test_highlight_status(pdf_path: Path) -> None:
    words = _pdf_words(str(pdf_path))
    price = truth("listing_and_contract.contract_price", **PRICE)
    good = [{"page": 1, "bbox": around(words[0], "$", "412,000")}]
    assert highlight_status(price, good, words) == "correct"
    assert (
        highlight_status(price, [{"page": 1, "bbox": around(words[0], "Subject")}], words)
        == "wrong_box"
    )
    assert highlight_status(price, [{"page": 2, "bbox": good[0]["bbox"]}], words) == "wrong_page"
    elsewhere = truth("listing_and_contract.contract_price", **{**PRICE, "page": 2})
    assert highlight_status(elsewhere, good, words) == "wrong_page"  # cited 1, truth 2
    also = truth("listing_and_contract.contract_price", also_on=[1], page=2, **PRICE)
    assert highlight_status(also, good, words) == "correct"
    assert highlight_status(price, [], words) == "no_region"
    assert highlight_status(price, None, words) is None  # the system has no regions
    assert highlight_status(truth("subject.zip", type="text", value="1"), good, words) is None
    occupant = truth("subject.occupant", type="enum", value="owner", printed="X Owner")
    assert (
        highlight_status(occupant, [{"page": 1, "bbox": around(words[0], "Owner")}], words)
        == "correct"
    )
    assert (
        highlight_status(occupant, [{"page": 1, "bbox": around(words[0], "Tenant")}], words)
        == "wrong_box"
    )
    # A scanned page has no text layer: the box cannot be checked (not "wrong_box").
    scanned = [PageWords(page.width, page.height, ()) for page in words]
    assert highlight_status(price, good, scanned) is None


def _line(*tokens: str) -> PageWords:
    """One line of words at y 100-110 on a 600 x 800 page, 10 points per token, 5 apart."""
    words, x = [], 50.0
    for token in tokens:
        words.append((token, x, 100.0, x + 10.0 * len(token), 110.0))
        x += 10.0 * len(token) + 5.0
    return PageWords(600.0, 800.0, tuple(words))


def _box(page: PageWords, first: int, last: int) -> list[dict[str, Any]]:
    w = page.words
    return [{"page": 1, "bbox": [w[first][1] / 600, 0.12, w[last][3] / 600, 0.14]}]


def test_highlight_targets_are_the_value_not_its_print_conventions() -> None:
    # An "x" between numbers is part of the value (it was stripped as a checkbox mark).
    dims = _line("Dimensions", "50", "x", "150")
    site = truth("site.dimensions", type="text", value="50 x 150", printed="50 x 150")
    assert highlight_status(site, _box(dims, 1, 3), [dims]) == "correct"
    # An en dash prints where the vocabulary label has a hyphen.
    time = _line("Marketing", "Time", "3\u20136", "mths")
    months = truth(
        "neighborhood.marketing_time", type="enum", value="3_6_mths", printed="3\u20136 mths"
    )
    assert highlight_status(months, _box(time, 2, 3), [time]) == "correct"
    # An Other box's write-in is what the box reads.
    gas = _line("Gas", "Propane")
    other = truth(
        "site.utilities_gas",
        type="enum",
        value={"code": "other", "text": "Propane"},
        printed="Propane",
    )
    assert highlight_status(other, _box(gas, 1, 1), [gas]) == "correct"
    # A number may be boxed without the caption or unit the truth's print carries, but only as
    # a whole word: "2" is not inside "2244".
    fire = _line("Total", "Number", "-", "4", "2244")
    four = truth("improvements.fireplaces", type="int", value=4, printed="Total Number - 4")
    assert highlight_status(four, _box(fire, 3, 3), [fire]) == "correct"
    two = truth("improvements.fireplaces", type="int", value=2, printed="Total Number - 2")
    assert highlight_status(two, _box(fire, 4, 4), [fire]) == "wrong_box"
    grouped = _line("Area", "2,244")
    assert highlight_status(two, _box(grouped, 1, 1), [grouped]) == "wrong_box"  # not in 2,244
    # A number glued to the next word by a semicolon is still that number.
    dom = _line("DOM", "250;The")
    days = truth("listing_and_contract.days_on_market", type="int", value=250, printed="DOM 250")
    assert highlight_status(days, _box(dom, 1, 1), [dom]) == "correct"
    # A text value's words in another reading order (a wrapped or offset line) still count.
    lender = PageWords(
        600.0,
        800.0,
        (("Piper", 50.0, 101.0, 90.0, 111.0), ("Mortgage", 95.0, 100.0, 155.0, 110.0)),
    )
    name = truth(
        "subject.lender_client", type="text", value="Piper Mortgage", printed="Piper Mortgage"
    )
    assert highlight_status(name, _box(lender, 0, 1), [lender]) == "correct"


def test_highlight_in_the_score_and_report(pdf_path: Path) -> None:
    words = _pdf_words(str(pdf_path))
    doc = truth_doc(
        {"listing_and_contract.contract_price": {"status": "present", "page": 1, **PRICE}}, pages=2
    )
    with_region = field(value_text="$ 412,000", value_num="412000") | {
        "regions": [{"page": 1, "bbox": around(words[0], "$", "412,000")}],
        "origin": "llm",
    }
    score = score_run(
        {"synthetic_1004": truth_set(doc)},
        predictions(
            prediction_doc("doc_a", repeat({"listing_and_contract.contract_price": with_region}))
        ),
        page_words=lambda d: words,
    )
    assert score["highlight"]["available"] and score["highlight"]["correct"] == 1
    assert score["highlight"]["by"]["origin"]["llm"]["rate"] == 1.0
    assert score["by"]["origin"]["llm"]["correct"] == 1
    report = render_report(score)
    assert "| highlight correct (correct values) | 1 | 1.000 |" in report
    no_regions = field(value_text="$ 412,000", value_num="412000")  # production: no regions key
    plain = score_run(
        {"synthetic_1004": truth_set(doc)},
        predictions(
            prediction_doc("doc_a", repeat({"listing_and_contract.contract_price": no_regions}))
        ),
        page_words=lambda d: words,
    )
    assert not plain["highlight"]["available"]
    assert "n/a (no regions)" in render_report(plain)
    assert "412,000" not in report  # no values in the report


def test_usage_metrics() -> None:
    def doc(cost: float, tokens: int, seconds: float, pages: dict[str, int]) -> dict[str, Any]:
        rep = repeat({}) | {
            "cost_usd": cost,
            "tokens_input": tokens,
            "tokens_output": tokens // 10,
            "llm_calls": 1,
            "seconds": seconds,
            "model_latency_ms": seconds * 500,
        }
        return prediction_doc("d", rep) | {"azure_pages": pages}

    documents = [
        doc(0.02, 10_000, 4.0, {"prebuilt-layout": 20}),
        doc(0.04, 30_000, 10.0, {"prebuilt-layout": 20, "prebuilt-mortgage.us.1004": 20}),
        doc(0.03, 20_000, 6.0, {"prebuilt-layout": 10, "something-new": 3}),
        prediction_doc("failed", status="missing_recording"),
    ]
    usage = usage_metrics(documents)
    assert usage["documents"] == 3
    assert usage["model_usd_per_document"] == pytest.approx(0.03)
    assert usage["azure_pages"] == {
        "prebuilt-layout": 50,
        "prebuilt-mortgage.us.1004": 20,
        "something-new": 3,
    }
    assert usage["azure_usd"] == pytest.approx(0.7)  # 70 priced pages at $10 per 1,000
    assert usage["azure_unpriced_models"] == ["something-new"]
    assert usage["tokens_input_per_document"] == pytest.approx(20_000)
    assert (usage["seconds_p50"], usage["seconds_p95"]) == (6.0, 10.0)  # nearest rank
    assert usage["model_seconds_p50"] == 3.0
    assert usage["usd_per_document"] == pytest.approx(0.03 + 0.7 / 3)


# --------------------------------------------------------------------------- run.py

PAGES = [FORM]


def setup(tmp_path: Path) -> tuple[Path, str]:
    appraisal = tmp_path / "appraisal"
    path = appraisal / "synthetic_1004/doc_a/appraisal.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(pdf_bytes(PAGES))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    doc = truth_doc(
        {
            "listing_and_contract.contract_price": {"status": "present", "page": 1, **PRICE},
            "subject.occupant": {"status": "present", "type": "enum", "value": "owner", "page": 1},
        },
        pages=1,
        sha256=sha,
    )
    dump_truth(truth_set(doc), appraisal / "truth/synthetic_1004.json")
    return appraisal, sha


class FakeRaw:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.calls = 0

    async def analyze_raw(self, pdf_bytes: bytes) -> Any:
        self.calls += 1
        return recordings.rebuild_sdk_result(self.raw)


def grounded_turn() -> ScriptedTurn:
    return ScriptedTurn(
        parsed=GroundedAnswer(
            fields=[
                GroundedValue(
                    key="listing_and_contract.contract_price",
                    status="present",
                    value="$ 412,000",
                    page=1,
                    quote="Contract Price $ 412,000",
                ),
                GroundedValue(
                    key="subject.occupant", status="present", value="Owner", page=1, quote=FORM[1]
                ),
            ]
        )
    )


def options(tmp_path: Path, mode: str, spec: SystemSpec, out: str) -> Options:
    return Options(
        sets=("synthetic_1004",), mode=mode, data_dir=tmp_path, out=tmp_path / out, system=spec
    )


def outcome(score: dict[str, Any], key: str) -> str:
    return str(next(r["outcome"] for r in score["fields"] if r["key"] == key))


def test_llm_all_live_records_and_replay_reproduces(tmp_path: Path) -> None:
    appraisal, sha = setup(tmp_path)
    spec = SystemSpec("llm_all", ModelChoice("gpt-6-luna", "low", 8000), min_page_words=1)
    azure = FakeRaw(raw_layout(PAGES))
    provider = ScriptedProvider([grounded_turn()], model="gpt-6-luna")
    live = asyncio.run(
        run_eval(
            options(tmp_path, "live", spec, "live"),
            settings=Settings(),
            live=LiveDeps(azure=azure, provider=provider),
        )
    )
    assert live is not None
    directory = recordings.recording_dir(appraisal, sha)
    assert (directory / recordings.AZURE_RAW).is_file()
    assert len(list((directory / recordings.LLM_DIR).glob("*.json"))) == 1
    assert not (directory / recordings.CONTEXT).exists()  # not a production-settings system
    assert outcome(live.score, "listing_and_contract.contract_price") == "correct"
    assert outcome(live.score, "subject.occupant") == "correct"
    assert live.score["highlight"]["available"]
    assert live.score["usage"]["tokens_input_per_document"] == 100  # ScriptedTurn's usage
    assert live.score["usage"]["azure_pages"] == {"prebuilt-layout": 1}
    assert live.run["system"]["label"] == "llm_all-gpt-6-luna-low"
    replay = asyncio.run(run_eval(options(tmp_path, "replay", spec, "replay"), settings=Settings()))
    assert replay is not None
    live_doc = json.loads((tmp_path / "live/predictions.json").read_text())["documents"][0]
    replay_doc = json.loads((tmp_path / "replay/predictions.json").read_text())["documents"][0]
    assert replay_doc["repeats"][0]["fields"] == live_doc["repeats"][0]["fields"]
    price = replay_doc["repeats"][0]["fields"]["listing_and_contract.contract_price"]
    assert price["regions"][0]["page"] == 1 and price["quote"] == "Contract Price $ 412,000"
    assert replay.score["overall"] == live.score["overall"]
    # Another model is another recording: the replay fails that document, not the run.
    other = SystemSpec("llm_all", ModelChoice("claude-haiku-5-5", "low", 8000), min_page_words=1)
    missing = asyncio.run(
        run_eval(options(tmp_path, "replay", other, "missing"), settings=Settings())
    )
    assert missing is not None
    assert missing.score["documents"]["failed"][0]["status"] == "missing_recording"


def test_azure_1004_records_and_replays(tmp_path: Path) -> None:
    appraisal, sha = setup(tmp_path)
    spec = SystemSpec("azure_1004")
    mortgage = FakeRaw(mortgage_raw())
    live = asyncio.run(
        run_eval(
            options(tmp_path, "live", spec, "live"),
            settings=Settings(),
            live=LiveDeps(azure=FakeRaw(raw_layout(PAGES)), provider=None, mortgage=mortgage),
        )
    )
    assert live is not None and mortgage.calls == 1
    assert (recordings.recording_dir(appraisal, sha) / recordings.AZURE_1004_RAW).is_file()
    assert outcome(live.score, "subject.occupant") == "correct"
    replay = asyncio.run(run_eval(options(tmp_path, "replay", spec, "replay"), settings=Settings()))
    assert replay is not None and replay.score["overall"] == live.score["overall"]
    assert replay.score["usage"]["azure_pages"] == {"prebuilt-mortgage.us.1004": 1}


def test_vote_replays_two_voters_from_one_recording(tmp_path: Path) -> None:
    setup(tmp_path)
    spec_a = SystemSpec("llm_all", ModelChoice("gpt-6-luna", "low", 8000), min_page_words=1)
    spec_b = SystemSpec("llm_all", ModelChoice("claude-haiku-5-5", "low", 8000), min_page_words=1)
    for spec, model in ((spec_a, "gpt-6-luna"), (spec_b, "claude-haiku-5-5")):
        asyncio.run(
            run_eval(
                options(tmp_path, "live", spec, f"live-{model}"),
                settings=Settings(),
                live=LiveDeps(
                    azure=FakeRaw(raw_layout(PAGES)),
                    provider=ScriptedProvider([grounded_turn()], model=model),
                ),
            )
        )
    vote = SystemSpec(
        "vote",
        voters=(
            VoterSpec("llm_all", ModelChoice("gpt-6-luna", "low", 8000)),
            VoterSpec("llm_all", ModelChoice("claude-haiku-5-5", "low", 8000)),
        ),
        min_page_words=1,
    )
    out = asyncio.run(run_eval(options(tmp_path, "replay", vote, "vote"), settings=Settings()))
    assert out is not None
    doc = json.loads((tmp_path / "vote/predictions.json").read_text())["documents"][0]
    price = doc["repeats"][0]["fields"]["listing_and_contract.contract_price"]
    assert (price["confidence"], price["origin"]) == (0.95, "vote_agree")
    assert doc["repeats"][0]["llm_calls"] == 2


def test_parse_args_systems() -> None:
    base = ["--set", "client", "--mode", "replay"]
    assert parse_args(base).system == SystemSpec()
    o = parse_args(
        [
            *base,
            "--system",
            "llm_pages",
            "--model",
            "claude-opus-5-5",
            "--effort",
            "low",
            "--dpi",
            "120",
        ]
    )
    assert (o.system.name, o.system.model.model, o.system.dpi) == (
        "llm_pages",
        "claude-opus-5-5",
        120,
    )
    o = parse_args(
        [*base, "--system", "validate_reask", "--base", "llm_all", "--model", "gpt-6-luna"]
    )
    assert o.system.base == "llm_all"
    o = parse_args(
        [*base, "--system", "vote", "--voters", "llm_all@gpt-6-luna@low,checkbox_context"]
    )
    assert o.system.voters[0] == VoterSpec("llm_all", ModelChoice("gpt-6-luna", "low", 16000))
    assert o.system.voters[1].model.model is None
    for bad in (
        ["--system", "llm_all"],  # needs --model
        ["--system", "llm_all", "--model", "claude-sonnet-5-5", "--effort", "none"],
        ["--system", "llm_all", "--model", "gpt-6.1-sol", "--effort", "none"],
        ["--system", "validate_reask", "--model", "gpt-6-luna"],  # needs --base
        ["--system", "llm_all", "--model", "x", "--base", "production"],
        ["--system", "vote", "--voters", "llm_all@gpt-6-luna"],  # one voter
        ["--system", "vote", "--voters", "llm_all,llm_pages@x"],  # llm voter without a model
        ["--system", "vote", "--voters", "vote@x,llm_all@y"],
        ["--system", "llm_all", "--model", "x", "--azure-only"],
        ["--voters", "llm_all@x,llm_all@y"],
    ):
        with pytest.raises(SystemExit):
            parse_args([*base, *bad])
