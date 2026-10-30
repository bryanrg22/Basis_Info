"""``evals.predict_fields`` and ``evals.predict_assets`` offline, under a scripted model.

Each runner is driven end to end on a tiny PDF written with pymupdf: the production
request (prompt, format, effort, budget), the production guards (the verbatim check for
fields, citation verification for assets), the prediction files ``evals.run`` scores and
the run summary.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from basis.classify.schemas import CitationClaim, Classification
from basis.extraction.fallback import FallbackAnswer, FallbackValue
from basis.llm import prompts
from basis.llm.errors import ProviderAccountError, ProviderBadResponse
from basis.llm.provider import ScriptedProvider, ScriptedTurn, ToolCall
from evals import account_stop, predict_assets, predict_fields
from evals import datasets as ds
from evals.run import score_assets, score_fields

PAGE_1 = "Subject Property Address 1290 Example Way City Montrose"
PAGE_2 = "Improvements Year Built 1998 Effective Age 15 Fireplaces 4"


def write_pdf(path: Path, pages: list[str]) -> None:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(72, 72, 540, 720), text, fontsize=10)
    document.save(path)
    document.close()


def use(module: Any, provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module.ProviderPool, "from_settings", staticmethod(lambda settings: provider)
    )


def results_of(out: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((out / "results.json").read_text())
    return data


def counts_of(out: Path) -> tuple[int, int, int]:
    summary = results_of(out)["summary"]
    return summary["passed"], summary["failed"], summary["test_error"]


# --------------------------------------------------------------------------- fields


@pytest.fixture
def fields_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    (data / "inputs").mkdir(parents=True)
    write_pdf(data / "inputs" / "appraisal.pdf", [PAGE_1, PAGE_2])
    (data / "appraisal_fields_v1.json").write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "document": "inputs/appraisal.pdf",
                        "fields": [
                            {
                                "field": "subject.property_address",
                                "expected": "1290 Example Way",
                                "critical": True,
                            },
                            {"field": "improvements.year_built", "expected": 1998, "type": "int"},
                            {"field": "improvements.fireplaces", "expected": 4, "type": "int"},
                            {"field": "site.frontage_feet", "expected": 1.39, "type": "float"},
                        ],
                    }
                ]
            }
        )
    )
    return data


def answer(**values: str | None) -> ScriptedTurn:
    return ScriptedTurn(
        parsed=FallbackAnswer(
            fields=[
                FallbackValue(key=k, value=v, page=1, confidence=0.9) for k, v in values.items()
            ]
        )
    )


def test_caption_is_the_catalogue_label_or_the_key_in_words() -> None:
    assert predict_fields.caption("improvements.year_built") == "Year Built"
    assert predict_fields.caption("site.frontage_feet") == "site: frontage feet"


def test_accept_applies_the_production_guards() -> None:
    parsed = answer(
        **{"a.in": "Montrose", "a.out": "Springfield", "a.null": None, "z.extra": "Montrose"}
    ).parsed
    assert isinstance(parsed, FallbackAnswer)
    duplicated = FallbackAnswer(fields=[*parsed.fields, parsed.fields[0]])
    accepted, unguarded, dropped = predict_fields.accept(
        duplicated, ["a.in", "a.out", "a.null"], "City  montrose"
    )
    assert accepted == {"a.in": "Montrose"}  # whitespace and case folded
    assert unguarded == {"a.in": "Montrose", "a.out": "Springfield"}
    assert dropped == {
        "a.out": "not_in_text",
        "a.null": "not_found",
        "z.extra": "not_asked",
        "a.in": "duplicate",
    }


async def test_fields_run_writes_guarded_predictions_with_repeats(
    fields_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider(
        [
            answer(
                **{
                    "subject.property_address": "1290 Example Way",
                    "improvements.year_built": "1998",
                    "improvements.fireplaces": "four",  # not printed so: dropped
                    "site.frontage_feet": None,
                }
            ),
            answer(**{"subject.property_address": "1290 Example Way"}),
        ]
    )
    use(predict_fields, provider, monkeypatch)
    out = tmp_path / "out"
    args = ["--data-dir", str(fields_dir), "--out", str(out), "--model", "gpt-x"]
    code = await predict_fields.run(
        predict_fields.parse_args([*args, "--reasoning-effort", "low", "--repeats", "2"])
    )
    assert code == 0
    request = provider.requests[0]
    assert request.prompt_version == "appraisal_field_fallback/v1"
    assert request.reasoning_effort == "low" and request.max_tokens == 8000
    content = request.messages[0].content
    assert '- improvements.year_built: printed as "Year Built" (int)' in content
    assert '- site.frontage_feet: printed as "site: frontage feet" (float)' in content
    assert "<page number=1>" in content and "<page number=2>" in content  # whole document

    saved = json.loads((out / "appraisal_fields.json").read_text())["inputs/appraisal.pdf"]
    assert saved["subject.property_address"] == {"repeats": ["1290 Example Way"] * 2}
    assert saved["improvements.year_built"] == {"repeats": ["1998", None]}
    assert saved["improvements.fireplaces"] == {"repeats": [None, None]}
    unguarded = json.loads((out / "appraisal_fields.unguarded.json").read_text())
    assert unguarded["inputs/appraisal.pdf"]["improvements.fireplaces"]["repeats"][0] == "four"

    run = json.loads((out / "run.json").read_text())
    assert run["models_reported"] == ["scripted-model"] and run["model_calls"] == 2
    assert run["reasoning_effort"] == "low" and run["errors"] == []
    raw = [json.loads(line) for line in (out / "fields_raw.jsonl").read_text().splitlines()]
    assert raw[0]["dropped"] == {
        "improvements.fireplaces": "not_in_text",
        "site.frontage_feet": "not_found",
    }

    data = ds.AppraisalFields.model_validate_json(
        (fields_dir / "appraisal_fields_v1.json").read_text()
    )
    rows = {r.metric: r.value for r in score_fields(data, "dev", 2, saved_doc(out)).rows}
    assert rows["normalized_accuracy"] == pytest.approx(2 / 4)
    assert rows["critical_accuracy"] == 1.0 and rows["critical_pass^2"] == 1.0
    assert rows["pass^2"] == pytest.approx(1 / 4)
    # The run record (evals.record): the prompt, the label file, the PDF read, the ledger.
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.predict_fields"
    assert manifest["calls"]["count"] == 2
    assert manifest["components"]["prompts"] == [
        {
            "name": "appraisal_field_fallback",
            "version": "v1",
            "sha256": prompts.load("appraisal_field_fallback", "v1").sha256,
            "calls": 2,
        }
    ]
    [dataset] = manifest["data"]["datasets"]
    assert (dataset["name"], dataset["version"]) == ("appraisal_fields", "v1")
    assert dataset["sha256"]
    assert [i["name"] for i in manifest["data"]["inputs"]] == ["inputs/appraisal.pdf"]
    assert len((out / "llm_calls.jsonl").read_text().splitlines()) == 2
    assert "wrote" in (out / "console.log").read_text()
    # One case per (document, repeat), both passed (a value survived the guard each time).
    results = results_of(out)
    assert results["summary"]["total"] == 2 and counts_of(out) == (2, 0, 0)
    first, second = results["cases"]
    assert (first["id"], first["kind"], second["id"]) == (
        "inputs/appraisal.pdf#1",
        "document",
        "inputs/appraisal.pdf#2",
    )
    assert first["detail"] == {"fields": 4, "accepted": 2, "unguarded": 3, "dropped": 2}
    assert second["detail"]["accepted"] == 1 and second["seconds"] >= 0
    assert (
        "**results: 2 passed, 0 failed, 0 test errors (2 cases)**"
        in (out / "report.md").read_text()
    )
    assert manifest["tree"] == {
        "component": "appraisal_field_fallback",
        "version": "v1",
        "link": None,
    }


def saved_doc(out: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((out / "appraisal_fields.json").read_text())
    return data


async def test_fields_provider_error_is_a_missing_repeat_not_a_crash(
    fields_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider([ScriptedTurn(raise_error=ProviderBadResponse("400 bad"))])
    use(predict_fields, provider, monkeypatch)
    out = tmp_path / "out"
    code = await predict_fields.run(
        predict_fields.parse_args(
            ["--data-dir", str(fields_dir), "--out", str(out), "--model", "gpt-x"]
        )
    )
    assert code == 0
    run = json.loads((out / "run.json").read_text())
    assert run["errors"][0]["repeat"] == 1 and "400 bad" in run["errors"][0]["error"]
    saved = json.loads((out / "appraisal_fields.json").read_text())
    assert set(saved["inputs/appraisal.pdf"].values()) == {None}
    # An unusable answer is the model's failure on the document, with its catalogue code.
    assert counts_of(out) == (0, 1, 0)
    [case] = results_of(out)["cases"]
    assert (case["id"], case["outcome"], case["observed_error"]) == (
        "inputs/appraisal.pdf#1",
        "failed",
        "BASIS-PROVIDER-006",
    )


def test_error_outcome_tells_the_models_failures_from_the_providers() -> None:
    from basis.llm.errors import ProviderRateLimited, ProviderRequestRejected, ProviderUnavailable

    assert predict_fields.error_outcome(ProviderBadResponse("bad")) == "failed"
    assert (
        predict_fields.error_outcome(ProviderRequestRejected("no", provider="p", status=400))
        == "failed"
    )
    assert predict_fields.error_outcome(ProviderUnavailable("503")) == "test_error"
    assert predict_fields.error_outcome(ProviderRateLimited("429")) == "test_error"


async def test_fields_account_error_stops_the_run(
    fields_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike a bad response (a missing repeat), an account error ends the run."""
    error = ProviderAccountError("bad key", provider="openai", status=401, code="invalid_api_key")
    provider = ScriptedProvider([ScriptedTurn(raise_error=error), answer()])
    use(predict_fields, provider, monkeypatch)
    out = tmp_path / "out"
    code = await predict_fields.run(
        predict_fields.parse_args(
            ["--data-dir", str(fields_dir), "--out", str(out), "--model", "gpt-x", "--repeats", "2"]
        )
    )
    assert code == account_stop.ACCOUNT_STOPPED and len(provider.requests) == 1
    run = json.loads((out / "run.json").read_text())
    assert run["complete"] is False and run["stopped"]["status"] == 401
    assert not (out / "appraisal_fields.json").exists()
    # The refused repeat is a test error with the account error's code; repeat 2 never ran.
    results = results_of(out)
    assert results["summary"]["total"] == 1 and counts_of(out) == (0, 0, 1)
    assert results["cases"][0]["observed_error"] == "BASIS-PROVIDER-001"
    assert json.loads((out / "manifest.json").read_text())["status"] == "stopped"


# --------------------------------------------------------------------------- assets

RULE_TEXT = (
    "Carpeting that is not permanently attached is tangible personal property "
    "under section 1245 and is recovered over 5 years."
)


@pytest.fixture
def assets_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    (data / "corpus").mkdir(parents=True)
    write_pdf(data / "corpus" / "IRS_TEST.pdf", [RULE_TEXT])
    write_pdf(data / "corpus" / "RSMEANS_TEST.pdf", ["Carpet costs per square yard 1245"])
    (data / "assets_v1.json").write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "id": "r1",
                        "component": "Flooring - Carpet",
                        "bucket": "y5",
                        "property_class": "nonresidential",
                    },
                    {
                        "id": "r2",
                        "component": "Roofing",
                        "bucket": "y39",
                        "property_class": "nonresidential",
                    },
                ]
            }
        )
    )
    return data


def chunk_id(doc: str = "IRS_TEST") -> str:
    return str(uuid.uuid5(predict_assets.REFERENCE_VERSION, f"{doc}:1:0"))


def test_corpus_store_chunks_only_the_matching_pdfs(assets_dir: Path) -> None:
    store = predict_assets.corpus_store(assets_dir / "corpus", "IRS_*.pdf")
    [chunk] = store.chunks.values()
    assert (chunk.doc_id, chunk.page_start, chunk.chunk_key) == ("IRS_TEST", 1, "IRS_TEST:1:0")
    assert str(chunk.id) == chunk_id()
    assert "section 1245" in chunk.text


def classify_turns(excerpt: str, bucket: str = "y5") -> list[ScriptedTurn]:
    search = ToolCall(id="c1", name="search_reference", arguments={"query": "carpet 1245"})
    return [
        ScriptedTurn(tool_calls=(search,)),
        ScriptedTurn(
            parsed=Classification(
                category="Carpet",
                section="1245" if bucket != "y39" else "1250",
                bucket=bucket,  # type: ignore[arg-type]
                citations=[
                    CitationClaim(chunk_id=chunk_id(), excerpt=excerpt),
                    CitationClaim(chunk_id=chunk_id("RSMEANS_TEST"), excerpt="Carpet costs"),
                ],
                confidence=0.9,
            )
        ),
    ]


async def test_assets_run_scores_bucket_and_citations(
    assets_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = "tangible personal property under section 1245"
    provider = ScriptedProvider(
        [*classify_turns(good), *classify_turns(good, "y7")], model="claude-x"
    )
    use(predict_assets, provider, monkeypatch)
    out = tmp_path / "out"
    code = await predict_assets.run(
        predict_assets.parse_args(
            [
                "--data-dir",
                str(assets_dir),
                "--out",
                str(out),
                "--model",
                "claude-x",
                "--reasoning-effort",
                "none",
                "--max-tokens",
                "6000",
                "--repeats",
                "2",
                "--limit",
                "1",
                "--concurrency",
                "1",
            ]
        )
    )
    assert code == 0
    first, second = provider.requests[:2]
    assert first.reasoning_effort == "none" and first.max_tokens == 6000
    assert first.prompt_version == "classify/v1" and first.tools
    assert "Flooring - Carpet" in first.messages[0].content
    assert second.messages[-2].tool_calls[0].id == "c1"  # the call replayed natively

    saved = json.loads((out / "assets.json").read_text())
    assert list(saved) == ["r1"]  # --limit 1
    # The RSMeans chunk is not in the corpus: claimed 2, verified 1, on both repeats.
    assert saved["r1"]["repeats"] == [
        {"section": "1245", "bucket": "y5", "citations": {"claimed": 2, "verified": 1}},
        {"section": "1245", "bucket": "y7", "citations": {"claimed": 2, "verified": 1}},
    ]
    run = json.loads((out / "run.json").read_text())
    assert run["models_reported"] == ["claude-x"] and run["model_calls"] == 4
    assert run["reasoning_effort"] == "none" and run["loop_failures"] == []
    assert run["provider_errors"] == []
    raw = json.loads((out / "assets_raw.jsonl").read_text().splitlines()[0])
    assert raw["citations"] == [{"doc_id": "IRS_TEST", "page": 1, "excerpt": good}]
    assert raw["tool_calls"][0]["name"] == "search_reference"

    data = ds.Assets.model_validate_json((assets_dir / "assets_v1.json").read_text())
    data = ds.Assets(assets=data.assets[:1])
    rows = {r.metric: r.value for r in score_assets(data, "dev", 2, saved).rows}
    assert rows["bucket_accuracy"] == 1.0 and rows["bucket_pass^2"] == 0.0
    assert rows["citation_validity"] == pytest.approx(1 / 2)
    # The run record (evals.record): the classifier's calls are in the ledger, with
    # the label file and the corpus read.
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["harness"]["module"] == "evals.predict_assets"
    assert manifest["calls"]["count"] == 4
    [prompt] = manifest["components"]["prompts"]
    assert (prompt["name"], prompt["version"], prompt["calls"]) == ("classify", "v1", 4)
    assert prompt["sha256"]
    [dataset] = manifest["data"]["datasets"]
    assert (dataset["file"], dataset["version"]) == ("assets_v1.json", "v1")
    [corpus] = manifest["data"]["inputs"]
    assert (corpus["name"], corpus["files"]) == ("corpus", 2)
    assert len((out / "llm_calls.jsonl").read_text().splitlines()) == 4
    assert "wrote" in (out / "console.log").read_text()
    # One case per (row, repeat), both classified by the loop: passed, whatever the bucket.
    results = results_of(out)
    assert results["summary"]["total"] == 2 and counts_of(out) == (2, 0, 0)
    first, second = results["cases"]
    assert (first["id"], first["kind"], second["id"]) == ("r1#1", "asset", "r1#2")
    assert first["detail"]["bucket"] == "y5" and second["detail"]["bucket"] == "y7"
    assert first["detail"]["citations"] == {"claimed": 2, "verified": 1}
    assert "observed_error" not in first and first["seconds"] >= 0
    assert (
        "**results: 2 passed, 0 failed, 0 test errors (2 cases)**"
        in (out / "report.md").read_text()
    )
    assert manifest["tree"] == {"component": "classify", "version": "v1", "link": None}


async def test_assets_loop_failure_is_the_production_fallback_with_the_error_kept(
    assets_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = ProviderBadResponse("Function tools with reasoning_effort are not supported")
    provider = ScriptedProvider([ScriptedTurn(raise_error=error)])
    use(predict_assets, provider, monkeypatch)
    out = tmp_path / "out"
    code = await predict_assets.run(
        predict_assets.parse_args(
            ["--data-dir", str(assets_dir), "--out", str(out), "--model", "gpt-x", "--limit", "1"]
        )
    )
    assert code == 0
    saved = json.loads((out / "assets.json").read_text())
    # What production writes when the loop fails: 1250, the building bucket, no citations.
    assert saved["r1"] == {
        "section": "1250",
        "bucket": "y39",
        "citations": {"claimed": 0, "verified": 0},
    }
    run = json.loads((out / "run.json").read_text())
    assert run["loop_failures"] == [
        {"id": "r1", "repeat": 1, "reasons": ["provider_error:ProviderBadResponse"]}
    ]
    assert "not supported" in run["provider_errors"][0]["error"]
    # The fallback row is the loop's failure, with the bad response's catalogue code.
    assert counts_of(out) == (0, 1, 0)
    [case] = results_of(out)["cases"]
    assert (case["id"], case["outcome"], case["observed_error"]) == (
        "r1#1",
        "failed",
        "BASIS-PROVIDER-006",
    )
    assert case["detail"]["review_reasons"] == ["provider_error:ProviderBadResponse"]
    assert case["detail"]["bucket"] == "y39"


def test_asset_outcome_tells_the_loops_failures_from_the_harness_and_the_apis() -> None:
    def item(reasons: list[str], failed: bool = True) -> dict[str, Any]:
        return {
            "confidence": 0.0 if failed else 0.9,
            "category": "Unclassified: x" if failed else "Carpet",
            "review_reasons": reasons,
        }

    assert predict_assets.asset_outcome(item([], failed=False)) == "passed"
    assert predict_assets.asset_outcome(item(["low_confidence"], failed=False)) == "passed"
    assert predict_assets.asset_outcome(item(["invalid_output"])) == "failed"
    assert predict_assets.asset_outcome(item(["timeout"])) == "failed"
    assert predict_assets.asset_outcome(item(["provider_error:ProviderBadResponse"])) == "failed"
    assert (
        predict_assets.asset_outcome(item(["provider_error:ProviderUnavailable"])) == "test_error"
    )
    assert (
        predict_assets.asset_outcome(item(["provider_error:ProviderRateLimited"])) == "test_error"
    )
    assert predict_assets.asset_outcome(item(["budget_exceeded"])) == "test_error"


async def test_assets_account_error_stops_the_run_instead_of_a_fallback_row(
    assets_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = ProviderAccountError("Your credit balance is too low", provider="anthropic", status=400)
    provider = ScriptedProvider([ScriptedTurn(raise_error=error)])
    use(predict_assets, provider, monkeypatch)
    out = tmp_path / "out"
    code = await predict_assets.run(
        predict_assets.parse_args(
            [
                "--data-dir",
                str(assets_dir),
                "--out",
                str(out),
                "--model",
                "claude-x",
                "--limit",
                "1",
            ]
        )
    )
    assert code == account_stop.ACCOUNT_STOPPED and len(provider.requests) == 1
    run = json.loads((out / "run.json").read_text())
    assert run["complete"] is False and run["stopped"]["provider"] == "anthropic"
    assert not (out / "assets.json").exists()  # no 1250/y39 fallback row was written
    # The row whose loop the account error aborted is a test error with the error's code.
    results = results_of(out)
    assert results["summary"]["total"] == 1 and counts_of(out) == (0, 0, 1)
    assert (results["cases"][0]["id"], results["cases"][0]["observed_error"]) == (
        "r1#1",
        "BASIS-PROVIDER-001",
    )


async def test_assets_without_corpus_pdfs_stops_before_any_call(
    assets_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider([])
    use(predict_assets, provider, monkeypatch)
    code = await predict_assets.run(
        predict_assets.parse_args(
            [
                "--data-dir",
                str(assets_dir),
                "--out",
                str(tmp_path / "o"),
                "--model",
                "m",
                "--corpus-glob",
                "NOPE_*.pdf",
            ]
        )
    )
    assert code == 2 and provider.requests == []
