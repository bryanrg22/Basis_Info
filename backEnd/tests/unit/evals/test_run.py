"""The runner: dry run on the synthetic fixtures, saved predictions, report, DB rows."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

import evals.run as run_module
from basis.vision.reconcile import heuristic_merge
from basis.vision.schemas import Component, PhotoAnalysis, RoomType
from evals import datasets as ds
from evals import record
from evals.run import (
    CORPUS_TEXT,
    NO_PREDICTION,
    MetricRow,
    TaskResult,
    evaluate,
    main,
    max_merge,
    parse_args,
    parser_page_texts,
    pdf_page_texts,
    render_report,
    run_component,
    run_errors,
    score_assets,
    score_cases,
    score_detection_boxes,
    score_fields,
    score_photo_components,
    score_photo_counts,
    score_photo_rooms,
    score_report_rows,
    score_retrieval,
    score_room_inventory,
    score_rule_citations,
    write_results,
)


def test_dry_run_writes_a_report_for_every_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    report = tmp_path / "eval.md"
    assert main(["--split", "dev", "--repeats", "3", "--dry-run", "--report", str(report)]) == 0
    text = report.read_text()
    for name in ds.DATASETS:
        assert f"## {name}" in text
    assert "| normalized_accuracy | 1.0000 | 110 |" in text  # printed values normalize
    assert "| exact_accuracy |" in text and "| pass^3 |" in text
    assert "| ndcg_10 |" in text and "bm25_pages" in text
    assert "rules_table" in text
    # The synthetic corpus holds none of the documents the rules table cites.
    n = len(ds.rule_citations().citations)
    assert f"| resolved_fraction | 0.0000 | {n} |" in text
    assert f"status counts: document_missing {n}" in text
    out = capsys.readouterr().out
    assert "eval_runs" not in out  # no database configured
    assert "scores appended" not in out  # a baseline has no predictions directory
    assert not list(tmp_path.glob("**/scores.jsonl"))


def test_scoring_a_predictions_directory_appends_the_scores_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--predictions DIR`` leaves one ``scores.jsonl`` line in DIR naming the label file of
    every scored dataset with its version and sha256 (``None`` when the file does not exist:
    the synthetic dry run has no label files, only fixtures), the git state and the options."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    predictions = tmp_path / "pred"
    predictions.mkdir()
    (predictions / "photo_rooms.json").write_text(
        json.dumps({"photos/01_kitchen.jpg": "kitchen", "photos/02_bathroom.jpg": "bathroom"})
    )
    report = tmp_path / "eval.md"
    argv = ["--dry-run", "--repeats", "2", "--predictions", str(predictions), "--report"]
    assert main([*argv, str(report)]) == 0
    assert f"scores appended to {predictions / record.SCORES_FILE}" in capsys.readouterr().out
    [entry] = record.read_scores(predictions)
    assert entry["kind"] == "scores" and entry["format"] == record.FORMAT
    assert entry["harness"]["module"] == "evals.run"
    assert entry["data"]["data_dir"] == str(ds.SYNTHETIC_DIR)
    assert (entry["data"]["split"], entry["data"]["repeats"]) == ("dev", 2)
    assert entry["options"]["predictions"] == str(predictions)
    assert entry["options"]["repeats"] == 2 and entry["options"]["dry_run"] is True
    assert set(entry["results"]) == set(ds.DATASETS)
    assert entry["results"]["photo_rooms"]["system"] == "predictions"
    assert "accuracy" in entry["results"]["photo_rooms"]["metrics"]
    # Every file dataset names its label file; the synthetic directory holds none of them,
    # so the sha256 is None (asserted against the file system, not assumed).
    for name, filename in ds.DATASET_FILES.items():
        path = ds.SYNTHETIC_DIR / filename
        assert entry["data"]["labels"][name] == {
            "file": filename,
            "version": "v1",
            "sha256": record.sha256_file(path) if path.is_file() else None,
        }
    assert not path.is_file()
    assert entry["data"]["labels"]["rule_citations"] == {
        "file": None,
        "version": None,
        "sha256": None,
    }
    state = record.git_state()
    assert entry["git"]["sha"] == state["sha"] and entry["git"]["release"] == state["release"]
    assert isinstance(entry["git"]["dirty"], bool)
    # The report carries the same release and dirty flag.
    text = report.read_text()
    assert f"- release: `{state['release']}`" in text if state["release"] else "- release:" in text
    assert f"- dirty: {'yes' if state['dirty'] else 'no'}" in text
    # A second scoring appends a second line.
    assert main([*argv, str(report)]) == 0
    assert len(record.read_scores(predictions)) == 2


def test_the_scores_record_hashes_a_label_file_that_exists(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    label_file = data_dir / "detection_boxes_v1.json"
    label_file.write_text(detection_dataset().model_dump_json())
    predictions = tmp_path / "pred"
    predictions.mkdir()
    (predictions / "detection_boxes.json").write_text(
        json.dumps({"a.jpg": [box("window", 0.9, 0.0, 0.0, 0.2, 0.2)]})
    )
    args = ["--data-dir", str(data_dir), "--predictions", str(predictions), "--no-db"]
    assert main([*args, "--report", str(tmp_path / "r.md")]) == 0
    [entry] = record.read_scores(predictions)
    assert entry["data"]["data_dir"] == str(data_dir)
    assert entry["data"]["labels"]["detection_boxes"] == {
        "file": "detection_boxes_v1.json",
        "version": "v1",
        "sha256": record.sha256_file(label_file),
    }
    assert entry["results"]["detection_boxes"]["skipped"] is False
    assert entry["results"]["assets"]["skipped"] is True  # no assets_v1.json in data_dir


def _photo_room_predictions(
    predictions: Path,
) -> tuple[list[ds.PhotoRoom], ds.PhotoRoom, list[ds.PhotoRoom], ds.PhotoRoom]:
    """A ``photo_rooms.json`` built from the fixture labels: the first three photos right,
    the fourth wrong, the rest absent; returns (right, wrong, absent, errored), ``errored``
    the absent photo whose run case is a ``test_error`` in the directory's own results."""
    photos = list(ds.synthetic_smoke()["photo_rooms"].photos)
    assert all(p.split == "dev" for p in photos) and len(photos) >= 6
    right, wrong, absent = photos[:3], photos[3], photos[4:]
    vocabulary = sorted({p.room_type for p in photos})
    other = next(r for r in vocabulary if r != wrong.room_type)
    saved = {p.file: p.room_type for p in right} | {wrong.file: other}
    (predictions / "photo_rooms.json").write_text(json.dumps(saved))
    errored = absent[0]
    run = record.EvalRun.begin(
        predictions,
        harness="evals.predict_photos",
        log=False,
        component="photo_countables",
        version="v3",
        tree=None,
    )
    run.case(f"{right[0].file}#1", "passed", kind="photo")
    run.case(f"{errored.file}#1", "test_error", kind="photo", observed_error=RuntimeError("x"))
    run.finish({"photos": len(photos)})
    return right, wrong, absent, errored


def test_scoring_a_predictions_directory_records_a_case_per_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every scored item becomes a ``kind: score`` case in the directory's ``results.json``
    and ``report.md``: passed/failed by the dataset's criterion, ``test_error`` ("no
    prediction") for an item the runner itself failed on, ``failed`` ("no prediction") for
    one it simply did not predict; the counts reach ``scores.jsonl`` and the manifest."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    predictions = tmp_path / "pred"
    predictions.mkdir()
    right, wrong, absent, errored = _photo_room_predictions(predictions)
    before = record.read_results(predictions)["cases"]
    assert len(before) == 2  # the runner's own cases, kept below
    argv = ["--dry-run", "--predictions", str(predictions), "--report", str(tmp_path / "r.md")]
    assert main(argv) == 0
    out = capsys.readouterr().out
    total = len(right) + 1 + len(absent)
    assert f"scored {total} cases into {predictions / record.RESULTS_FILE}" in out
    # Expected counts from the fixture: right -> passed; wrong and absent-but-not-errored
    # -> failed; the absent photo with a run test_error -> test_error.
    expected = {
        "passed": len(right),
        "failed": 1 + len(absent) - 1,
        "test_error": 1,
    }
    results = record.read_results(predictions)
    scored = {c["id"]: c for c in results["cases"] if c["kind"] == "score"}
    assert set(scored) == {f"photo_rooms:{p.file}" for p in (*right, wrong, *absent)}
    assert results["summary"]["by_kind"]["score"] == expected
    for photo in right:
        case = scored[f"photo_rooms:{photo.file}"]
        assert case["outcome"] == "passed"
        assert case["detail"] == {"predicted": photo.room_type, "expected": photo.room_type}
    bad = scored[f"photo_rooms:{wrong.file}"]
    assert bad["outcome"] == "failed" and bad["detail"]["expected"] == wrong.room_type
    assert bad["detail"]["predicted"] != wrong.room_type
    assert scored[f"photo_rooms:{errored.file}"] == {
        "id": f"photo_rooms:{errored.file}",
        "kind": "score",
        "outcome": "test_error",
        "detail": NO_PREDICTION,
    }
    for photo in absent[1:]:
        assert scored[f"photo_rooms:{photo.file}"]["outcome"] == "failed"
        assert scored[f"photo_rooms:{photo.file}"]["detail"] == NO_PREDICTION
    # The runner's own cases are still there, untouched; baselines added none.
    kept = {c["id"]: c for c in results["cases"] if c["kind"] == "photo"}
    assert kept == {c["id"]: c for c in before}
    assert not any(c["id"].startswith(("assets:", "rule_citations:")) for c in results["cases"])
    # report.md and the manifest's results summary were rewritten with the merged cases.
    report = (predictions / record.REPORT_FILE).read_text()
    assert record.summary_line(results["summary"]) in report
    assert f"| `photo_rooms:{errored.file}` | score | test_error | no prediction |" in report
    assert f"| `photo_rooms:{right[0].file}` | score | passed |" in report
    manifest = json.loads((predictions / record.MANIFEST_FILE).read_text())
    assert manifest["results"] == results["summary"]
    assert manifest["results"]["total"] == len(before) + total
    # scores.jsonl carries the per-dataset case counts; a baseline dataset has none.
    [entry] = record.read_scores(predictions)
    assert entry["results"]["photo_rooms"]["cases"] == expected
    assert "cases" not in entry["results"]["assets"]  # rules_table baseline
    assert "cases" not in entry["results"]["rule_citations"]  # derived from the code
    assert "cases" not in entry["results"]["appraisal_fields"]  # printed_text baseline


def test_main_reads_the_component_from_the_predictions_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``eval_runs`` rows of a ``--predictions`` scoring carry the component and version
    the directory's manifest files it under; a baseline run carries neither."""
    calls: list[dict[str, Any]] = []

    async def fake(results: Any, split: str, sha: str, **kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(run_module, "_write_to_configured_db", fake)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://unused/unused")
    predictions = tmp_path / "pred"
    predictions.mkdir()
    _photo_room_predictions(predictions)
    manifest = json.loads((predictions / record.MANIFEST_FILE).read_text())
    tree = manifest["tree"]
    assert (tree["component"], tree["version"]) == ("photo_countables", "v3")
    report = str(tmp_path / "r.md")
    assert main(["--dry-run", "--predictions", str(predictions), "--report", report]) == 0
    assert main(["--dry-run", "--report", report]) == 0
    scored, baseline = calls
    assert (scored["component"], scored["component_version"]) == ("photo_countables", "v3")
    assert scored["run_dir"] == predictions
    assert (baseline["component"], baseline["component_version"]) == (None, None)
    assert baseline["run_dir"] is None


def test_run_component_reads_the_manifest_tree(tmp_path: Path) -> None:
    assert run_component(None) == (None, None)
    assert run_component(tmp_path) == (None, None)  # no manifest
    manifest = tmp_path / record.MANIFEST_FILE
    manifest.write_text("not json")
    assert run_component(tmp_path) == (None, None)
    manifest.write_text(json.dumps({"kind": "eval_run"}))
    assert run_component(tmp_path) == (None, None)  # no tree section
    manifest.write_text(json.dumps({"tree": {"component": "detection", "version": "sam3"}}))
    assert run_component(tmp_path) == ("detection", "sam3")
    manifest.write_text(json.dumps({"tree": {"component": "scoring", "version": None}}))
    assert run_component(tmp_path) == ("scoring", None)


def test_run_errors_are_the_test_error_case_ids_before_the_hash(tmp_path: Path) -> None:
    assert run_errors(tmp_path) == set()  # no results.json
    run = record.EvalRun.begin(tmp_path, harness="evals.x", log=False, tree=None)
    run.case("photos/a.jpg#1", "test_error", kind="photo", observed_error=RuntimeError("x"))
    run.case("photos/a.jpg#2", "passed", kind="photo")
    run.case("photos/b.jpg#1", "failed", kind="photo")
    run.case("doc_1", "test_error", kind="document", observed_error=RuntimeError("y"))
    run.finish({})
    assert run_errors(tmp_path) == {"photos/a.jpg", "doc_1"}


def test_score_cases_turns_items_into_record_cases_and_counts() -> None:
    results = [
        TaskResult(
            "photo_rooms",
            "predictions",
            cases=[
                ("a.jpg", True, {"predicted": "kitchen", "expected": "kitchen"}),
                ("b.jpg", False, {"predicted": "garage", "expected": "kitchen"}),
                run_module._no_prediction("c.jpg", "c.jpg"),
                run_module._no_prediction("d.jpg", "d.jpg"),
            ],
        ),
        TaskResult("assets", "rules_table"),  # a baseline: no cases, no count
        TaskResult("photo_counts", "predictions", cases=[("a.jpg:door", True, {})]),
    ]
    cases, counts = score_cases(results, errored={"c.jpg"})
    assert [(c["id"], c["kind"], c["outcome"]) for c in cases] == [
        ("photo_rooms:a.jpg", "score", "passed"),
        ("photo_rooms:b.jpg", "score", "failed"),
        ("photo_rooms:c.jpg", "score", "test_error"),
        ("photo_rooms:d.jpg", "score", "failed"),
        ("photo_counts:a.jpg:door", "score", "passed"),
    ]
    assert cases[2]["detail"] == cases[3]["detail"] == NO_PREDICTION
    assert cases[1]["detail"] == {"predicted": "garage", "expected": "kitchen"}
    assert counts == {
        "photo_rooms": {"passed": 1, "failed": 2, "test_error": 1},
        "photo_counts": {"passed": 1, "failed": 0, "test_error": 0},
    }
    # Without an errored set every missing prediction is a failure.
    _, plain = score_cases(results)
    assert plain["photo_rooms"] == {"passed": 1, "failed": 3, "test_error": 0}


def _outcomes(result: TaskResult) -> dict[str, str]:
    """``{item: outcome}`` of a scorer's cases, missing predictions resolved as failed."""
    cases, _ = score_cases([result])
    prefix = f"{result.dataset}:"
    return {c["id"].removeprefix(prefix): c["outcome"] for c in cases}


def _passed(result: TaskResult) -> dict[str, bool]:
    """``{item: passed}`` as the scorer recorded it (before missing predictions resolve)."""
    return {item: passed for item, passed, _ in result.cases}


def _missing(result: TaskResult) -> set[str]:
    """The items of a scorer's cases that have no saved prediction."""
    cases, _ = score_cases([result])
    return {
        c["id"].removeprefix(f"{result.dataset}:") for c in cases if c["detail"] == NO_PREDICTION
    }


def test_baselines_record_no_cases() -> None:
    results = evaluate(
        ds.synthetic_smoke(),
        split="dev",
        repeats=2,
        predictions=None,
        corpus_dir=ds.SYNTHETIC_DIR / "corpus",
    )
    assert results and all(r.cases == [] for r in results)


def test_photo_scorers_record_one_case_per_photo_or_count() -> None:
    data = ds.synthetic_smoke()
    rooms, components = data["photo_rooms"], data["photo_components"]
    assert isinstance(rooms, ds.PhotoRooms) and isinstance(components, ds.PhotoComponentsSet)
    first, second, *rest = rooms.photos
    saved = {first.file: first.room_type, second.file: {"repeats": ["garage", second.room_type]}}
    result = score_photo_rooms(rooms, "dev", 2, saved)
    outcomes = _outcomes(result)
    # Repeat 1 decides: the second photo's first repeat is wrong.
    assert outcomes[first.file] == "passed" and outcomes[second.file] == "failed"
    assert _missing(result) == {p.file for p in rest}
    assert len(outcomes) == len(rooms.photos)
    # Components: the exact set, with 'other' and loose furniture ignored.
    truth = sorted(c.label for c in components.photos[0].components)
    saved_components = {
        components.photos[0].file: [*truth, "other", "chair"],
        components.photos[1].file: ["toilet"],
    }
    result = score_photo_components(components, "dev", 1, saved_components)
    outcomes = _outcomes(result)
    assert outcomes[components.photos[0].file] == "passed"
    assert result.cases[0][2] == {"predicted": truth, "expected": truth}
    assert outcomes[components.photos[1].file] == "failed"
    assert _missing(result) == {p.file for p in components.photos[2:]}
    # Counts: one case per (photo, class) of the photo's verified classes.
    result = score_photo_counts(_counts_dataset(), "dev", 1, {"a.jpg": {"window": 2, "door": 3}})
    outcomes = _outcomes(result)
    assert outcomes == {
        "a.jpg:window": "passed",
        "a.jpg:door": "failed",
        "b.jpg:window": "failed",
        "b.jpg:door": "failed",
    }
    assert _missing(result) == {"b.jpg:window", "b.jpg:door"}
    assert _passed(result)["a.jpg:door"] is False
    assert result.cases[1][2] == {"predicted": 3, "expected": 1}


def test_asset_cases_judge_the_bucket_when_labelled_else_the_section() -> None:
    data = ds.Assets(
        assets=[
            ds.AssetLabel(id="a", component="carpet", section="1245", bucket="y5"),
            ds.AssetLabel(id="b", component="carpet", section="1245", bucket="y5"),
            ds.AssetLabel(id="c", component="carpet", section="1250"),
            ds.AssetLabel(id="d", component="carpet", section="1250"),
            ds.AssetLabel(id="e", component="carpet"),  # nothing labelled: no case
            ds.AssetLabel(id="f", component="carpet", section="1245"),  # no prediction
        ]
    )
    saved = {
        "a": {"section": "1250", "bucket": "y5"},  # bucket right, section wrong: passed
        "b": {"section": "1245", "bucket": "y27_5"},  # bucket wrong: failed
        "c": {"section": "1250"},
        "d": {"section": "1245", "bucket": "y27_5"},  # no bucket label: the section decides
        "e": {"section": "1245"},
    }
    result = score_assets(data, "dev", 1, saved)
    expected = {"a": "passed", "b": "failed", "c": "passed", "d": "failed", "f": "failed"}
    assert _outcomes(result) == expected
    assert _missing(result) == {"f"}
    assert _passed(result)["a"] is True
    assert result.cases[0][2] == {
        "predicted": {"section": "1250", "bucket": "y5"},
        "expected": {"section": "1245", "bucket": "y5"},
    }
    assert score_assets(data, "dev", 1, None).cases == []  # the rules-table baseline


def test_detection_cases_are_the_count_pairs_at_the_threshold() -> None:
    sure, unsure = box("window", 0.9, 0.0, 0.0, 0.2, 0.2), box("window", 0.3, 0.5, 0.5, 0.7, 0.7)
    result = score_detection_boxes(detection_dataset(), "dev", 1, {"a.jpg": [sure, unsure]}, 0.5)
    # a.jpg: 2 gold windows, 1 predicted at >= 0.5; b.jpg has no prediction for its door.
    assert _outcomes(result) == {"a.jpg:window": "failed", "b.jpg:door": "failed"}
    assert _missing(result) == {"b.jpg:door"}
    assert _passed(result)["a.jpg:window"] is False
    details = {item: detail for item, _, detail in result.cases}
    assert details["a.jpg:window"] == {"predicted": 1, "expected": 2}
    exact = {"a.jpg": [sure, unsure | {"score": 0.8}]}
    assert _outcomes(score_detection_boxes(detection_dataset(), "dev", 1, exact, 0.5)) == {
        "a.jpg:window": "passed",
        "b.jpg:door": "failed",
    }


def test_retrieval_cases_pass_when_a_relevant_page_is_in_the_top_10() -> None:
    data = ds.synthetic_smoke()["retrieval_qrels"]
    assert isinstance(data, ds.RetrievalQrels)
    qrels = data.qrels("dev")
    relevant = next(iter(qrels["q01"]))
    saved = {"q01": [relevant], "q02": ["SYN_PUB_Z:99"] + [f"SYN_PUB_Z:{i}" for i in range(9)]}
    result = score_retrieval(data, "dev", 1, saved, corpus_dir=None)
    outcomes = _outcomes(result)
    assert outcomes["q01"] == "passed" and outcomes["q02"] == "failed"
    assert _missing(result) == set(qrels) - {"q01", "q02"}
    assert len(outcomes) == len(qrels)
    by_item = {item: detail for item, _, detail in result.cases}
    assert by_item["q01"]["recall_10"] > 0 and by_item["q01"]["mrr"] == 1.0
    assert by_item["q02"] == {"recall_10": 0.0, "mrr": 0.0}
    # The page-level BM25 baseline records none.
    assert score_retrieval(data, "dev", 1, None, ds.SYNTHETIC_DIR / "corpus").cases == []


def test_room_inventory_and_report_row_cases() -> None:
    rooms, _ = two_photo_room()
    saved = {"1_kitchen": {"sink": 2, "countertop": 1, "other": 9}}
    result = score_room_inventory(rooms, "dev", 1, saved, None)
    assert _outcomes(result) == {"1_kitchen": "passed", "b_bath": "failed"}
    assert _missing(result) == {"b_bath"}
    assert result.cases[0][2] == {
        "predicted": {"sink": 2, "countertop": 1},
        "expected": {"sink": 2, "countertop": 1},
    }
    wrong = score_room_inventory(rooms, "dev", 1, {"1_kitchen": {"sink": 1}, "b_bath": {}}, None)
    assert _outcomes(wrong) == {"1_kitchen": "failed", "b_bath": "failed"}
    # Report rows: one case per photo-derivable row, evidenced or not, from the gold
    # inventory read as a prediction; the vocabulary-gap row can never be evidenced.
    data = ds.synthetic_smoke()
    report_rows, inventory = data["report_rows"], data["room_inventory"]
    assert isinstance(report_rows, ds.ReportRows) and isinstance(inventory, ds.RoomInventorySet)
    gold = {r.room_id: r.counts() for r in inventory.rooms}
    result = score_report_rows(report_rows, "dev", 1, gold, inventory)
    derivable = [r for r in report_rows.rows if r.photo_derivable]
    assert [item for item, _, _ in result.cases] == [r.id for r in derivable]
    coverage = next(r for r in result.rows if r.metric == "row_coverage")
    per_row = {r["id"]: r["outcome"] for r in coverage.details["rows"]}
    for item, passed, detail in result.cases:
        assert passed == (per_row[item] == "evidenced") and detail["outcome"] == per_row[item]
    gaps = [r.id for r in derivable if r.vocabulary_gap]
    assert gaps and all(_outcomes(result)[g] == "failed" for g in gaps)
    assert sum(1 for _, passed, _ in result.cases if passed) == len(derivable) - len(gaps)
    assert score_report_rows(report_rows, "dev", 1, None, inventory).cases == []


def test_field_cases_are_the_normalized_match_per_document_field() -> None:
    data = ds.synthetic_smoke()["appraisal_fields"]
    assert isinstance(data, ds.AppraisalFields)
    [document] = data.documents
    first, second, *rest = document.fields
    saved = {document.document: {first.field: first.printed, second.field: "not the value"}}
    result = score_fields(data, "dev", 1, saved)
    outcomes = _outcomes(result)
    assert outcomes[f"{document.document}:{first.field}"] == "passed"  # printed normalizes
    assert outcomes[f"{document.document}:{second.field}"] == "failed"
    # A covered document's unpredicted fields are predictions of nothing: failed, not missing.
    assert all(outcomes[f"{document.document}:{f.field}"] == "failed" for f in rest)
    assert _missing(result) == set()
    assert len(outcomes) == len(document.fields)
    assert result.cases[0][2] == {"predicted": first.printed, "expected": first.expected}
    # A document the predictions do not cover has no prediction for any of its fields.
    uncovered = score_fields(data, "dev", 1, {"some_other_doc": {}})
    assert _missing(uncovered) == {f"{document.document}:{f.field}" for f in document.fields}
    assert score_fields(data, "dev", 1, None).cases == []  # the printed_text baseline


def test_rules_table_baseline_shows_the_contested_rows() -> None:
    """The synthetic corpus treats bathtubs and toilets as 1250; the legacy rows say 1245."""
    [assets] = [
        r
        for r in evaluate(
            ds.synthetic_smoke(), split="dev", repeats=1, predictions=None, corpus_dir=None
        )
        if r.dataset == "assets"
    ]
    rows = {r.metric: r for r in assets.rows}
    assert rows["section_accuracy"].value == pytest.approx(2 / 6)
    assert rows["section_accuracy"].details["confusion"]["1250"] == {"1245": 2, "abstain": 2}
    assert rows["section_coverage"].value == pytest.approx(4 / 6)


def test_saved_predictions_are_scored_with_repeats(tmp_path: Path) -> None:
    predictions = tmp_path / "pred"
    predictions.mkdir()
    (predictions / "photo_rooms.json").write_text(
        json.dumps(
            {
                "photos/01_kitchen.jpg": {"repeats": ["kitchen", "kitchen", "bathroom"]},
                "photos/02_bathroom.jpg": "bathroom",
            }
        )
    )
    (predictions / "retrieval_qrels.json").write_text(json.dumps({"q01": ["SYN_PUB_A:1"]}))
    results = {
        r.dataset: r
        for r in evaluate(
            ds.synthetic_smoke(), split="dev", repeats=3, predictions=predictions, corpus_dir=None
        )
    }
    rooms = {r.metric: r.value for r in results["photo_rooms"].rows}
    assert results["photo_rooms"].system == "predictions"
    # 8 photos: kitchen right on repeat 1, bathroom right, 6 unpredicted (ambiguous):
    # only the truly ambiguous photo is right among those -> 3/8
    assert rooms["accuracy"] == pytest.approx(3 / 8)
    # pass^3: the kitchen fails one repeat -> items passing all 3: bathroom, ambiguous
    assert rooms["pass^3"] == pytest.approx(2 / 8)
    retrieval = {r.metric: r.value for r in results["retrieval_qrels"].rows}
    assert retrieval["mrr"] == pytest.approx(1 / 20)  # one of 20 queries answered, rank 1


def two_photo_room() -> tuple[ds.RoomInventorySet, ds.PhotoComponentsSet]:
    """One kitchen in two photos: photo A shows a sink and the countertop, photo B the
    second sink. The room has two sinks and one countertop."""
    rooms = ds.RoomInventorySet.model_validate(
        {
            "rooms": [
                {
                    "room_id": "1_kitchen",
                    "room_type": "kitchen",
                    "photos": [
                        "21_1st Floor Kitchen photo 1.JPG",
                        "22_1st Floor Kitchen photo 2.JPG",
                    ],
                    "objects": [
                        {"label": "sink", "photos": ["21_1st Floor Kitchen photo 1.JPG"]},
                        {"label": "sink", "photos": ["22_1st Floor Kitchen photo 2.JPG"]},
                        {"label": "countertop", "count": 1},
                        {"label": "other", "count": 3, "note": "stools"},
                    ],
                },
                {
                    "room_id": "b_bath",
                    "room_type": "bathroom",
                    "photos": ["45_Basement Bath.JPG"],
                    "objects": [{"label": "toilet"}],
                    "document": "other_package",
                },
            ]
        }
    )
    components = ds.PhotoComponentsSet.model_validate(
        {
            "photos": [
                {
                    "file": "21_1st Floor Kitchen photo 1.JPG",
                    "components": [{"label": "sink"}, {"label": "countertop"}],
                },
                {
                    "file": "22_1st Floor Kitchen photo 2.JPG",
                    "components": [{"label": "sink"}, {"label": "countertop"}],
                },
            ]
        }
    )
    return rooms, components


def test_predicted_other_labels_are_not_false_positives() -> None:
    """``other`` is excluded on both sides: the labels file loose items there, and so do
    the prompts, so a predicted ``other`` must not count against precision."""
    data = ds.PhotoComponentsSet.model_validate(
        {"photos": [{"file": "a.jpg", "components": [{"label": "sink"}, {"label": "other"}]}]}
    )
    rows = {
        r.metric: r
        for r in score_photo_components(data, "dev", 1, {"a.jpg": ["sink", "other", "Other"]}).rows
    }
    assert (rows["precision"].value, rows["recall"].value, rows["f1"].value) == (1.0, 1.0, 1.0)
    assert rows["precision"].n == 1 and rows["photo_exact_set"].value == 1.0


def test_per_photo_max_baseline_under_counts_distinct_identical_objects() -> None:
    rooms, components = two_photo_room()
    result = score_room_inventory(rooms, "dev", 1, None, components)
    assert result.system == "per_photo_max"
    rows = {r.metric: r for r in result.rows}
    # kitchen: countertop (1, 1), sink (2, 1); bath: toilet (1, 0) -- its photo has no
    # labels, so the baseline predicts nothing there.
    assert rows["count_mae"].n == 3 and rows["count_mae"].value == pytest.approx(2 / 3)
    assert rows["exact_count_rate"].value == pytest.approx(1 / 3)
    assert rows["signed_bias"].value == pytest.approx(-2 / 3)
    assert rows["signed_bias"].details["by_label"] == {
        "countertop": [1, 0],
        "sink": [1, -1],
        "toilet": [1, -1],
    }
    assert rows["room_exact_inventory"].value == 0.0 and rows["room_exact_inventory"].n == 2
    assert rows["label_recall"].value == pytest.approx((1.0 + 0.0) / 2)
    assert any("1 room photos have no photo_components labels" in n for n in result.notes)
    assert "pass^1" not in rows  # one repeat: no pass^k


def test_room_inventory_predictions_with_repeats() -> None:
    rooms, _ = two_photo_room()
    saved = {
        "1_kitchen": {
            "repeats": [
                {"sink": 2, "Countertop": 1, "other": 4},  # right ("other" is ignored)
                {"sink": 1, "countertop": 1},
                {"sink": 2, "countertop": 1},
            ]
        },
        "b_bath": {"toilet": 1},
        "x_ghost": {"sink": 1},
    }
    result = score_room_inventory(rooms, "dev", 3, saved, None)
    assert result.system == "predictions"
    rows = {r.metric: r.value for r in result.rows}
    assert rows["count_mae"] == 0.0 and rows["signed_bias"] == 0.0
    assert rows["room_exact_inventory"] == 1.0 and rows["label_f1"] == 1.0
    # kitchen right in 2 of 3 repeats -> C(2,3)/C(3,3) = 0; bath 3 of 3 -> 1.
    assert rows["pass^3"] == pytest.approx(0.5)
    assert rows["pass^1"] == pytest.approx((2 / 3 + 1) / 2)
    assert any("x_ghost" in n for n in result.notes)


def test_loose_furniture_is_kept_out_of_the_recognition_and_count_scores() -> None:
    """The FF&E convention (EVAL_REPORT section 19): loose furniture is listed as
    ``furniture`` with a count so the engineer can price the FF&E row, but like ``other``
    it never counts for or against recognition or counts; report_rows still reads it."""
    data = ds.PhotoComponentsSet.model_validate(
        {"photos": [{"file": "a.jpg", "components": [{"label": "sink"}, {"label": "furniture"}]}]}
    )
    rows = {
        r.metric: r.value
        for r in score_photo_components(data, "dev", 1, {"a.jpg": ["sink", "chair", "desk"]}).rows
    }
    assert (rows["precision"], rows["recall"]) == (1.0, 1.0)
    rooms, _ = two_photo_room()
    rooms = rooms.model_copy(
        update={
            "rooms": [
                r.model_copy(
                    update={
                        "objects": [
                            *r.objects,
                            ds.InventoryObjectLabel(label="furniture", count=4),
                        ]
                    }
                )
                for r in rooms.rooms
            ]
        }
    )
    saved = {"1_kitchen": {"sink": 2, "countertop": 1, "furniture": 9}, "b_bath": {"toilet": 1}}
    scored = {r.metric: r.value for r in score_room_inventory(rooms, "dev", 1, saved, None).rows}
    assert scored["count_mae"] == 0.0 and scored["label_f1"] == 1.0
    ffe = ds.ReportRows.model_validate(
        {
            "rows": [
                {
                    "id": "ffe",
                    "system": "FF&E",
                    "bucket": "y5",
                    "cost_share": 1.0,
                    "kind": "A",
                    "labels": ["furniture"],
                    "count_required": True,
                }
            ]
        }
    )
    coverage = score_report_rows(ffe, "dev", 1, saved, rooms).rows[0]
    assert coverage.metric == "row_coverage" and coverage.value == 1.0


def test_room_inventory_needs_predictions_or_photo_labels() -> None:
    rooms, _ = two_photo_room()
    result = score_room_inventory(rooms, "dev", 1, None, None)
    assert result.skipped and "no saved predictions" in result.notes[0]
    assert score_room_inventory(rooms, "held_out", 1, {}, None).skipped
    with pytest.raises(ValueError, match="label: count"):
        score_room_inventory(rooms, "dev", 1, {"b_bath": ["toilet"]}, None)


def test_max_merge_is_the_heuristic_merge_rule() -> None:
    per_photo = [{"sink": 1, "countertop": 1}, {}, {"sink": 2, "stove": 1}]
    assert max_merge(per_photo) == {"sink": 2, "countertop": 1, "stove": 1}

    def photo(counts: dict[str, int]) -> PhotoAnalysis:
        return PhotoAnalysis(
            room_type=RoomType.KITCHEN,
            room_confidence=0.9,
            components=[
                Component.model_validate(
                    {
                        "label": label,
                        "count": count,
                        "material": "unknown",
                        "attachment": "unknown",
                        "confidence": 0.5,
                    }
                )
                for label, count in counts.items()
            ],
        )

    merged = heuristic_merge([photo(c) for c in per_photo])
    assert {o.label.value: o.count for o in merged} == max_merge(per_photo)


def test_dry_run_room_inventory_comes_from_the_fixture_photos() -> None:
    [rooms] = [
        r
        for r in evaluate(
            ds.synthetic_smoke(), split="dev", repeats=1, predictions=None, corpus_dir=None
        )
        if r.dataset == "room_inventory"
    ]
    # Every fixture photo is its own room, so the per-photo labels are the inventory.
    rows = {r.metric: r for r in rooms.rows}
    assert rows["room_exact_inventory"].n == 8 and rows["room_exact_inventory"].value == 1.0
    assert rows["count_mae"].value == 0.0


def test_missing_dataset_and_empty_split_are_reported_as_skipped() -> None:
    data = ds.synthetic_smoke()
    del data["assets"]
    results = evaluate(data, split="held_out", repeats=1, predictions=None, corpus_dir=None)
    assert all(r.skipped for r in results)
    text = render_report(results, split="held_out", repeats=1, source="x", git_sha="abc")
    assert "dataset file not found" in text and "no held_out items" in text


def test_report_header_names_the_release_and_the_dirty_flag() -> None:
    results = evaluate(
        ds.synthetic_smoke(), split="dev", repeats=1, predictions=None, corpus_dir=None
    )
    git = {"sha": "abc" * 13 + "d", "short": "abcabca", "dirty": True, "release": "v0.9.0"}
    text = render_report(results, split="dev", repeats=1, source="x", git_sha="abcabca", git=git)
    head = text.split("\n\n")[1].splitlines()
    assert head[3:6] == ["- git sha: `abcabca`", "- release: `v0.9.0`", "- dirty: yes"]
    clean = render_report(
        results, split="dev", repeats=1, source="x", git_sha="abcabca", git=git | {"dirty": False}
    )
    assert "- dirty: no" in clean
    # No tag reachable from HEAD: the release is none; without a git state both are unknown.
    untagged = render_report(
        results, split="dev", repeats=1, source="x", git_sha="x", git=git | {"release": None}
    )
    assert "- release: none" in untagged
    legacy = render_report(results, split="dev", repeats=1, source="x", git_sha="x")
    assert "- release: unknown" in legacy and "- dirty: unknown" in legacy


def detection_dataset() -> ds.DetectionBoxes:
    window = {"label": "window", "x1": 0.0, "y1": 0.0, "x2": 0.2, "y2": 0.2}
    return ds.DetectionBoxes.model_validate(
        {
            "images": [
                {
                    "file": "a.jpg",
                    "width": 100,
                    "height": 100,
                    "document": "a",
                    "boxes": [window, window | {"x1": 0.5, "x2": 0.7, "y1": 0.5, "y2": 0.7}],
                },
                {
                    "file": "b.jpg",
                    "width": 100,
                    "height": 100,
                    "document": "b",
                    "verified": ["door"],
                    "boxes": [{"label": "door", "x1": 0.1, "y1": 0.1, "x2": 0.4, "y2": 0.9}],
                },
                {"file": "c.jpg", "width": 100, "height": 100, "split": "held_out", "boxes": []},
            ]
        }
    )


def box(label: str, score: float, x1: float, y1: float, x2: float, y2: float) -> dict[str, object]:
    return {"label": label, "score": score, "x1": x1, "y1": y1, "x2": x2, "y2": y2}


def test_detection_boxes_are_scored_from_saved_predictions_with_repeats() -> None:
    first = {
        "a.jpg": [box("window", 0.9, 0.0, 0.0, 0.2, 0.2), box("window", 0.3, 0.5, 0.5, 0.7, 0.7)],
        "b.jpg": [
            box("Interior Door", 0.8, 0.1, 0.1, 0.4, 0.9),  # normalized; counts as a door
            box("window", 0.9, 0.5, 0.5, 0.6, 0.6),  # window is not verified in b.jpg
        ],
    }
    second = {
        "a.jpg": [box("window", 0.9, 0.0, 0.0, 0.2, 0.2), box("window", 0.8, 0.5, 0.5, 0.7, 0.7)]
    }
    saved = {f: {"repeats": [first.get(f), second.get(f)]} for f in ("a.jpg", "b.jpg")}
    result = score_detection_boxes(detection_dataset(), "dev", 2, saved, threshold=0.5)
    rows = {r.metric: r for r in result.rows}
    assert result.system == "predictions" and not result.skipped
    assert rows["map_50"].value == 1.0 and rows["map_50"].n == 2  # door and window
    assert rows["ap_50:window"].value == 1.0 and rows["ap_50:door"].value == 1.0
    # Repeat 1 at 0.5: a.jpg window 2 gold / 1 predicted, b.jpg door 1 / 1.
    assert rows["count_mae"].value == pytest.approx(0.5) and rows["count_mae"].n == 2
    assert rows["count_signed_bias"].value == pytest.approx(-0.5)
    assert rows["count_mae:window"].value == 1.0 and rows["count_mae:door"].value == 0.0
    assert rows["precision_at_threshold"].value == 1.0
    assert rows["recall_at_threshold"].value == pytest.approx(2 / 3)
    assert (rows["precision_at_threshold"].n, rows["recall_at_threshold"].n) == (2, 3)
    # Repeat 2 gets a.jpg right and predicts no door: pass^2 = 0 for both pairs.
    assert rows["count_pass^2"].value == 0.0 and rows["count_pass^1"].value == pytest.approx(0.5)


def test_detection_boxes_need_predictions_and_items() -> None:
    data = detection_dataset()
    skipped = score_detection_boxes(data, "dev", 1, None)
    assert skipped.skipped and "evals.predict_boxes" in skipped.notes[0]
    assert score_detection_boxes(data, "regression", 1, {}).skipped
    partial = score_detection_boxes(data, "dev", 1, {"a.jpg": []})
    assert "1 images have no predictions" in partial.notes[0]
    with pytest.raises(ValueError):
        score_detection_boxes(data, "dev", 1, {"a.jpg": {"label": "window"}})


def test_detection_threshold_option_reaches_the_scorer(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "detection_boxes_v1.json").write_text(detection_dataset().model_dump_json())
    predictions = tmp_path / "pred"
    predictions.mkdir()
    (predictions / "detection_boxes.json").write_text(
        json.dumps({"a.jpg": [box("window", 0.4, 0.0, 0.0, 0.2, 0.2)]})
    )
    report = tmp_path / "r.md"
    args = ["--data-dir", str(data_dir), "--predictions", str(predictions), "--no-db"]
    assert main([*args, "--report", str(report), "--detection-threshold", "0.3"]) == 0
    assert "| recall_at_threshold | 0.3333 |" in report.read_text()
    assert main([*args, "--report", str(report)]) == 0
    assert "| recall_at_threshold | 0.0000 |" in report.read_text()
    with pytest.raises(SystemExit):
        main([*args, "--detection-threshold", "2"])


def citations(*items: tuple[str, str, int, str]) -> ds.RuleCitations:
    return ds.RuleCitations(
        rules_version="test",
        citations=[
            ds.RuleCitationItem(rule=r, doc_id=d, page=p, excerpt=e, contested=r == "toilet")
            for r, d, p, e in items
        ],
    )


FAKE_PAGES = {
    "DOC_A": ["Front matter.", "Carpeting will be treated as not permanently attached."],
    "DOC_B": ["Asset class 00.3 land improvements: sidewalks, roads, fences."],
}


def fake_corpus(tmp_path: Path) -> Path:
    for doc_id in [*FAKE_PAGES, "UNCITED"]:
        (tmp_path / f"{doc_id}.pdf").write_bytes(b"%PDF-1.4 stub")  # read by the fake below
    return tmp_path


def test_rule_citations_resolve_against_page_text(tmp_path: Path) -> None:
    read: list[str] = []

    def page_texts(path: Path) -> list[str]:
        read.append(path.stem)
        return FAKE_PAGES[path.stem]

    data = citations(
        ("carpet", "DOC_A", 2, "carpeting will be treated as not permanently attached"),
        ("carpet", "DOC_A", 415, "Carpeting will be treated"),  # page out of range
        ("fence", "DOC_B", 1, "sidewalks, roads, fences"),
        ("toilet", "DOC_A", 1, "Plumbing fixtures - toilets"),  # paraphrase, wrong page
        ("toilet", "DOC_C", 1, "a document the corpus lacks"),
    )
    result = score_rule_citations(data, fake_corpus(tmp_path), page_texts)
    assert not result.skipped and result.system == "page_text"
    rows = {r.metric: r for r in result.rows}
    fraction = rows["resolved_fraction"]
    assert (fraction.value, fraction.n) == (pytest.approx(2 / 5), 5)
    assert fraction.interval == pytest.approx((0.1176, 0.7693), abs=1e-4)  # Wilson, 2 of 5
    assert fraction.details["by_status"] == {
        "resolved": 2,
        "page_out_of_range": 1,
        "excerpt_not_on_page": 1,
        "document_missing": 1,
    }
    assert [(u["rule"], u["status"]) for u in fraction.details["unresolved"]] == [
        ("carpet", "page_out_of_range"),
        ("toilet", "excerpt_not_on_page"),
        ("toilet", "document_missing"),
    ]
    # A rule resolves only when every citation does: fence yes, carpet and toilet no.
    rules = rows["rules_resolved"]
    assert (rules.value, rules.n) == (pytest.approx(1 / 3), 3)
    assert rules.details["unresolved_rules"] == ["carpet", "toilet"]
    assert "unresolved rules: carpet, toilet" in result.notes
    assert any("1 contested rules" in n and "toilet" in n for n in result.notes)
    assert sorted(read) == ["DOC_A", "DOC_B"]  # only cited documents are read


def test_rule_citations_all_resolved(tmp_path: Path) -> None:
    data = citations(("fence", "DOC_B", 1, "sidewalks, roads, fences"))
    result = score_rule_citations(data, fake_corpus(tmp_path), lambda p: FAKE_PAGES[p.stem])
    rows = {r.metric: r for r in result.rows}
    assert rows["resolved_fraction"].value == 1.0
    assert rows["resolved_fraction"].interval == pytest.approx((0.2065, 1.0), abs=1e-4)
    assert "unresolved rules: none" in result.notes


def test_rule_citations_without_a_corpus_are_skipped(tmp_path: Path) -> None:
    data = citations(("fence", "DOC_B", 1, "sidewalks, roads, fences"))
    for corpus in (None, tmp_path / "missing", tmp_path):  # tmp_path has no PDFs
        result = score_rule_citations(data, corpus, lambda p: [])
        assert result.skipped and "no corpus PDFs" in result.notes[0]


def test_rule_citations_dry_run_resolves_nothing_against_the_synthetic_corpus() -> None:
    [result] = [
        r
        for r in evaluate(
            ds.synthetic_smoke(),
            split="dev",
            repeats=1,
            predictions=None,
            corpus_dir=ds.SYNTHETIC_DIR / "corpus",
        )
        if r.dataset == "rule_citations"
    ]
    rows = {r.metric: r for r in result.rows}
    n = len(ds.rule_citations().citations)
    assert (rows["resolved_fraction"].value, rows["resolved_fraction"].n) == (0.0, n)
    assert rows["resolved_fraction"].details["by_status"] == {"document_missing": n}


def test_rule_citations_are_scored_on_every_split(tmp_path: Path) -> None:
    data = {"rule_citations": citations(("fence", "DOC_B", 1, "sidewalks, roads, fences"))}
    corpus = fake_corpus(tmp_path)
    (tmp_path / "DOC_B.pdf").unlink()
    for split in ("dev", "regression", "held_out"):
        results = evaluate(data, split=split, repeats=1, predictions=None, corpus_dir=corpus)
        [result] = [r for r in results if r.dataset == "rule_citations"]
        assert not result.skipped
        assert result.rows[0].details["by_status"] == {"document_missing": 1}


def test_parser_page_texts_are_the_ingested_chunks_per_page() -> None:
    from basis.retrieval.chunk import chunk_document
    from basis.retrieval.parse import parse_pdf

    path = ds.SYNTHETIC_DIR / "corpus" / "SYN_PUB_A.pdf"
    pages = parser_page_texts(path)
    assert len(pages) == len(pdf_page_texts(path)) == 4
    drafts = chunk_document(parse_pdf(path.read_bytes(), "SYN_PUB_A"))
    for draft in drafts:  # every stored chunk is on its page's text
        assert draft.text in pages[draft.page - 1]
    assert "Section 168(e)(3) groups property into recovery classes" in pages[0]


def test_corpus_text_selects_the_page_text_and_names_the_system() -> None:
    assert parse_args([]).corpus_text == "pymupdf"
    assert parse_args(["--corpus-text", "parser"]).corpus_text == "parser"
    with pytest.raises(SystemExit):
        parse_args(["--corpus-text", "ocr"])
    assert CORPUS_TEXT == {"pymupdf": pdf_page_texts, "parser": parser_page_texts}
    by_text = {}
    for corpus_text in CORPUS_TEXT:
        results = evaluate(
            ds.synthetic_smoke(),
            split="dev",
            repeats=1,
            predictions=None,
            corpus_dir=ds.SYNTHETIC_DIR / "corpus",
            corpus_text=corpus_text,
        )
        by_text[corpus_text] = {r.dataset: r for r in results}
    assert by_text["pymupdf"]["retrieval_qrels"].system == "bm25_pages"
    assert by_text["parser"]["retrieval_qrels"].system == "bm25_pages_parser"
    assert by_text["pymupdf"]["rule_citations"].system == "page_text"
    assert by_text["parser"]["rule_citations"].system == "page_text_parser"
    for result in by_text["parser"].values():
        if result.dataset == "retrieval_qrels":
            assert not result.skipped and {r.metric for r in result.rows} >= {"ndcg_10", "mrr"}


class _FakeSession:
    """Collects what ``write_results`` adds; ``flush`` assigns the ids the database would."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    @asynccontextmanager
    async def begin(self) -> Any:
        yield self


def _fake_sessionmaker(session: _FakeSession) -> Any:
    @asynccontextmanager
    async def make() -> Any:
        yield session

    return make


def _manifest(results: list[TaskResult], data: Path) -> dict[str, Any]:
    return record.score_entry(
        results,
        harness="evals.run",
        data_dir=data,
        label_files={"photo_components": "photo_components_v1.json", "assets": "assets_v1.json"},
        split="dev",
        repeats=2,
        options={"predictions": "out/run"},
    )


async def test_write_results_fills_the_manifest_columns(tmp_path: Path) -> None:
    from basis.db.models import EvalResult, EvalRun

    data = tmp_path / "data"
    data.mkdir()
    (data / "photo_components_v1.json").write_text("{}")  # assets_v1.json does not exist
    results = [
        TaskResult(
            "photo_components", "predictions", rows=[MetricRow("f1", 0.5, 10, 0.1, (0.2, 0.8))]
        ),
        TaskResult("assets", "rules_table", rows=[MetricRow("section_accuracy", 1.0, 6)]),
        TaskResult("photo_counts", "predictions", skipped=True, notes=["no file"]),
    ]
    manifest = _manifest(results, data)
    session = _FakeSession()
    ids = await write_results(
        _fake_sessionmaker(session),
        results,
        split="dev",
        git_sha="abc1234",
        manifest=manifest,
        run_dir=tmp_path / "run",
        component="photo_countables",
        component_version="v3",
    )
    runs = {r.dataset: r for r in session.added if isinstance(r, EvalRun)}
    rows = [r for r in session.added if isinstance(r, EvalResult)]
    assert set(runs) == {"photo_components", "assets"} and len(ids) == 2  # skipped: no row
    assert len(rows) == 2 and {r.eval_run_id for r in rows} == set(ids)
    components, assets = runs["photo_components"], runs["assets"]
    assert components.git_sha == "abc1234" and components.model == "predictions"
    assert components.git_dirty == manifest["git"]["dirty"]
    assert components.release == manifest["git"]["release"]
    assert components.run_dir == str(tmp_path / "run")
    assert components.label_sha256 == record.sha256_file(data / "photo_components_v1.json")
    assert components.label_version == "v1"
    assert (components.component, components.component_version) == ("photo_countables", "v3")
    assert (assets.component, assets.component_version) == ("photo_countables", "v3")
    assert components.manifest == manifest  # the whole scores.jsonl entry, on every row
    assert assets.manifest == manifest
    # The label file the manifest could not hash leaves the sha NULL, the version stands.
    assert (assets.label_sha256, assets.label_version) == (None, "v1")
    assert assets.run_dir == str(tmp_path / "run")
    # The manifest is what the row stores: it must survive JSON (JSONB) as it is.
    json.dumps(components.manifest)
    # Without a manifest (the pre-0014 call) every new column stays NULL.
    legacy = _FakeSession()
    await write_results(_fake_sessionmaker(legacy), results, split="dev", git_sha="abc1234")
    for run in (r for r in legacy.added if isinstance(r, EvalRun)):
        assert (run.git_dirty, run.release, run.run_dir) == (None, None, None)
        assert (run.label_sha256, run.label_version, run.manifest) == (None, None, None)
        assert (run.component, run.component_version) == (None, None)


@pytest.mark.integration
async def test_results_are_written_to_eval_tables(database_url: str) -> None:
    from dbtest_support import db_settings, truncate_all
    from sqlalchemy import select

    from basis.db.engine import create_engine
    from basis.db.models import EvalResult, EvalRun
    from basis.db.session import create_sessionmaker, session_scope

    engine = create_engine(db_settings(database_url))
    await truncate_all(engine)
    sm = create_sessionmaker(engine)
    try:
        results = evaluate(
            ds.synthetic_smoke(),
            split="dev",
            repeats=2,
            predictions=None,
            corpus_dir=ds.SYNTHETIC_DIR / "corpus",
        )
        manifest = record.score_entry(
            results,
            harness="evals.run",
            data_dir=ds.SYNTHETIC_DIR,
            label_files=ds.DATASET_FILES,
            split="dev",
            repeats=2,
        )
        ids = await write_results(
            sm,
            results,
            split="dev",
            git_sha="abc123",
            manifest=manifest,
            component="photo_countables",
            component_version="v3",
        )
        # The six file datasets the dry run builds plus the derived rule_citations;
        # detection_boxes has no synthetic file (so no derived photo_counts either): both
        # are reported missing, not written; the other seven files plus rule_citations are.
        assert len(ids) == len(ds.DATASETS) - 2 == 8
        async with session_scope(sm) as session:
            runs = (await session.scalars(select(EvalRun))).all()
            rows = (await session.scalars(select(EvalResult))).all()
        assert {r.dataset for r in runs} == set(ds.DATASETS) - {"detection_boxes", "photo_counts"}
        assert {(r.split, r.git_sha) for r in runs} == {("dev", "abc123")}
        # The manifest columns, read back from Postgres: the baseline has no run directory,
        # the synthetic directory has no label files (sha NULL, version from the name), and
        # the whole entry round-trips through JSONB.
        assert {r.run_dir for r in runs} == {None}
        assert {(r.component, r.component_version) for r in runs} == {("photo_countables", "v3")}
        assert {r.git_dirty for r in runs} == {manifest["git"]["dirty"]}
        assert {r.release for r in runs} == {manifest["git"]["release"]}
        assets = next(r for r in runs if r.dataset == "assets")
        assert (assets.label_sha256, assets.label_version) == (None, "v1")
        citations = next(r for r in runs if r.dataset == "rule_citations")
        assert (citations.label_sha256, citations.label_version) == (None, None)
        assert assets.manifest == manifest
        ndcg = next(r for r in rows if r.metric == "ndcg_10")
        assert ndcg.n == 20 and ndcg.value is not None and ndcg.stderr is not None
        accuracy = next(r for r in rows if r.metric == "section_accuracy")
        assert accuracy.details["interval"][0] < accuracy.value < accuracy.details["interval"][1]
        assert accuracy.details["system"] == "rules_table"
    finally:
        await engine.dispose()


def _counts_dataset() -> ds.PhotoCounts:
    return ds.PhotoCounts(
        photos=[
            ds.PhotoCountLabel(file="a.jpg", counts={"window": 2, "door": 1}),
            ds.PhotoCountLabel(file="b.jpg", counts={"window": 0, "door": 2}, document="d2"),
        ]
    )


def test_photo_counts_are_scored_per_class_with_door_as_a_union() -> None:
    saved = {
        "a.jpg": {
            "repeats": [{"Window": 2, "interior_door": 1}, {"window": 3, "exterior_door": 1}]
        },
        "b.jpg": {"door": 1},  # one value for every repeat; windows absent = 0
    }
    rows = {r.metric: r for r in score_photo_counts(_counts_dataset(), "dev", 2, saved).rows}
    # pairs: a r1 (2,2) (1,1); a r2 (3,2) (1,1); b r1 (0,0) (1,2); b r2 (0,0) (1,2)
    assert rows["count_mae"].n == 8 and rows["count_mae"].value == pytest.approx(3 / 8)
    assert rows["exact_count_rate"].value == pytest.approx(5 / 8)
    assert rows["count_signed_bias"].value == pytest.approx(-1 / 8)
    assert rows["count_mae:window"].value == pytest.approx(1 / 4)
    assert rows["count_recall:door"].value == pytest.approx(4 / 6)
    assert rows["count_precision:window"].value == pytest.approx(4 / 5)


def test_photo_counts_fall_back_to_the_raw_records(tmp_path: Path) -> None:
    predictions = tmp_path / "pred"
    predictions.mkdir()
    records = [
        {
            "file": "a.jpg",
            "repeat": 2,
            "analysis": {"components": [{"label": "window", "count": 1}]},
        },
        {
            "file": "a.jpg",
            "repeat": 1,
            "analysis": {
                "components": [
                    {"label": "window", "count": 1},
                    {"label": "window", "count": 2},
                    {"label": "door"},
                ]
            },
        },
        {"file": "b.jpg", "repeat": 1, "error": "timed out"},
    ]
    (predictions / "photo_raw.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    from evals.run import _counts_from_raw

    counts = _counts_from_raw(predictions)
    assert counts == {"a.jpg": {"repeats": [{"window": 3, "door": 1}, {"window": 1}]}}
    assert _counts_from_raw(tmp_path) is None
    result = score_photo_counts(_counts_dataset(), "dev", 2, counts)
    rows = {r.metric: r for r in result.rows}
    assert rows["count_mae"].n == 4  # b.jpg has no prediction
    assert any("2 (photo, repeat) pairs have no saved prediction" in n for n in result.notes)


def test_photo_counts_without_predictions_are_skipped() -> None:
    result = score_photo_counts(_counts_dataset(), "dev", 1, None)
    assert result.skipped and "no saved predictions" in result.notes[0]
