"""The runner: dry run on the synthetic fixtures, saved predictions, report, DB rows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basis.vision.reconcile import heuristic_merge
from basis.vision.schemas import Component, PhotoAnalysis, RoomType
from evals import datasets as ds
from evals.run import (
    evaluate,
    main,
    max_merge,
    render_report,
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
        assert (
            len(ids) == len(ds.DATASET_FILES) + 1 == 7
        )  # six files plus the derived rule_citations
        async with session_scope(sm) as session:
            runs = (await session.scalars(select(EvalRun))).all()
            rows = (await session.scalars(select(EvalResult))).all()
        assert {r.dataset for r in runs} == set(ds.DATASETS)
        assert {(r.split, r.git_sha) for r in runs} == {("dev", "abc123")}
        ndcg = next(r for r in rows if r.metric == "ndcg_10")
        assert ndcg.n == 20 and ndcg.value is not None and ndcg.stderr is not None
        accuracy = next(r for r in rows if r.metric == "section_accuracy")
        assert accuracy.details["interval"][0] < accuracy.value < accuracy.details["interval"][1]
        assert accuracy.details["system"] == "rules_table"
    finally:
        await engine.dispose()
