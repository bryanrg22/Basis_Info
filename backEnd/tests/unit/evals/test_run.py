"""The runner: dry run on the synthetic fixtures, saved predictions, report, DB rows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basis.vision.reconcile import heuristic_merge
from basis.vision.schemas import Component, PhotoAnalysis, RoomType
from evals import datasets as ds
from evals.run import (
    CORPUS_TEXT,
    evaluate,
    main,
    max_merge,
    parse_args,
    parser_page_texts,
    pdf_page_texts,
    render_report,
    score_detection_boxes,
    score_photo_components,
    score_photo_counts,
    score_report_rows,
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
    assert "eval_runs" not in capsys.readouterr().out  # no database configured


def test_rules_table_baseline_agrees_on_bathtubs_and_toilets() -> None:
    """The synthetic corpus treats bathtubs and toilets as 1250, and so do the rows since
    RULES_VERSION 2026-10-29.1 (they were 1245 before); the two labels without a row
    abstain."""
    [assets] = [
        r
        for r in evaluate(
            ds.synthetic_smoke(), split="dev", repeats=1, predictions=None, corpus_dir=None
        )
        if r.dataset == "assets"
    ]
    rows = {r.metric: r for r in assets.rows}
    assert rows["section_accuracy"].value == pytest.approx(4 / 6)
    assert rows["section_accuracy"].details["confusion"]["1250"] == {"1250": 2, "abstain": 2}
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
        ids = await write_results(sm, results, split="dev", git_sha="abc123")
        # The six file datasets the dry run builds plus the derived rule_citations;
        # detection_boxes has no synthetic file (so no derived photo_counts either): both
        # are reported missing, not written; the other seven files plus rule_citations are.
        assert len(ids) == len(ds.DATASETS) - 2 == 8
        async with session_scope(sm) as session:
            runs = (await session.scalars(select(EvalRun))).all()
            rows = (await session.scalars(select(EvalResult))).all()
        assert {r.dataset for r in runs} == set(ds.DATASETS) - {"detection_boxes", "photo_counts"}
        assert {(r.split, r.git_sha) for r in runs} == {("dev", "abc123")}
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
