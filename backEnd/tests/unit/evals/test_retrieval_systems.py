"""Chunk-level retrieval systems of ``retrieval_qrels`` (evals/retrieval.py, run.py).

Synthetic corpus and the hash embedder only: no model download, no database. The real
corpus and MiniLM are covered by tests/integration/retrieval/test_real_corpus.py.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from pathlib import Path

import pytest

from basis.config import get_settings, reset_settings
from basis.retrieval.bm25 import Bm25Doc, Bm25Index
from basis.retrieval.chunk import chunk_document
from basis.retrieval.embed import HashEmbedder
from basis.retrieval.hybrid import CANDIDATE_FACTOR, fuse, hybrid_search
from basis.retrieval.parse import parse_pdf
from basis.retrieval.types import Hit
from evals import datasets as ds
from evals import retrieval as rt
from evals.run import (
    RETRIEVAL_SYSTEMS,
    TaskResult,
    evaluate,
    main,
    paired_comparison,
    parse_args,
    score_chunk_systems,
)

CORPUS = ds.SYNTHETIC_DIR / "corpus"


def synthetic_index() -> tuple[rt.ChunkIndex, HashEmbedder]:
    chunks = rt.parser_chunks(ds.corpus_pdfs(CORPUS))
    embedder = HashEmbedder()
    return rt.ChunkIndex(chunks, embedder.embed([c.text for c in chunks])), embedder


def queries() -> dict[str, str]:
    data = ds.synthetic_smoke()["retrieval_qrels"]
    assert isinstance(data, ds.RetrievalQrels)
    return {q.id: q.query for q in data.queries}


# --------------------------------------------------------------------------- chunks


def test_parser_chunks_are_the_ingested_chunks_in_store_order() -> None:
    pdfs = ds.corpus_pdfs(CORPUS)
    chunks = rt.parser_chunks(pdfs)
    expected = [
        d
        for doc_id, path in pdfs.items()
        for d in chunk_document(parse_pdf(path.read_bytes(), doc_id))
    ]
    assert sorted((c.chunk_key, c.text) for c in chunks) == sorted(
        (d.chunk_key, d.text) for d in expected
    )
    keys = [c.chunk_key for c in chunks]
    assert keys == sorted(keys)  # ORDER BY chunk_key, as the store loads BM25 rows
    assert all(c.chunk_key.startswith(f"{c.doc_id}:{c.page}:") for c in chunks)


def test_bm25_is_the_production_index_over_the_same_rows() -> None:
    index, _ = synthetic_index()
    reference = Bm25Index(
        Bm25Doc(uuid.uuid5(rt.CHUNK_VERSION, c.chunk_key), c.text) for c in index.chunks
    )
    for query in queries().values():
        assert [(h.chunk_id, h.score) for h in index.bm25(query, 20)] == reference.search(query, 20)


def test_vector_search_is_exact_cosine_with_chunk_key_ties() -> None:
    chunks = [rt.CorpusChunk("D", p, f"D:{p}:0", f"t{p}") for p in (1, 2, 3, 4)]
    index = rt.ChunkIndex(chunks, [[1.0, 0.0], [0.0, 2.0], [3.0, 0.0], [1.0, 1.0]])
    hits = index.vector([2.0, 0.0], 4)
    assert [h.page_start for h in hits] == [1, 3, 4, 2]  # pages 1 and 3 tie: key order
    assert [round(h.score, 6) for h in hits] == [1.0, 1.0, round(1 / math.sqrt(2), 6), 0.0]
    assert index.vector([0.0, 0.0], 4) == [] and index.vector([1.0, 0.0], 0) == []
    with pytest.raises(ValueError):
        rt.ChunkIndex(chunks, [[1.0, 0.0]])


def test_zero_embedding_rows_never_divide_by_zero() -> None:
    chunks = [rt.CorpusChunk("D", 1, "D:1:0", "a"), rt.CorpusChunk("D", 2, "D:2:0", "b")]
    index = rt.ChunkIndex(chunks, [[0.0, 0.0], [0.0, 1.0]])
    assert [h.page_start for h in index.vector([0.0, 1.0], 2)] == [2, 1]


def test_index_serves_hybrid_search_and_get_chunk() -> None:
    index, embedder = synthetic_index()
    query = "recovery classes under section 168(e)(3)"
    hits = asyncio.run(hybrid_search(index, embedder, rt.CHUNK_VERSION, query, 5))
    bm25 = index.bm25(query, 5 * CANDIDATE_FACTOR)
    vector = index.vector(embedder.embed([query])[0], 5 * CANDIDATE_FACTOR)
    assert hits == fuse(bm25, vector, "rrf", limit=5)
    chunk = asyncio.run(index.get_chunk(hits[0].chunk_id))
    assert chunk is not None and chunk.text == hits[0].text
    assert asyncio.run(index.get_chunk(uuid.uuid4())) is None
    assert asyncio.run(index.get_table(uuid.uuid4())) is None


def test_pages_dedupes_chunks_and_cuts_at_ten() -> None:
    def hit(doc: str, page: int) -> Hit:
        return Hit(uuid.uuid4(), doc, page, page, 1.0, "")

    hits = [hit("A", 1), hit("A", 1), hit("B", 2)] + [hit("C", p) for p in range(1, 20)]
    assert rt.pages(hits)[:3] == ["A:1", "B:2", "C:1"]
    assert len(rt.pages(hits)) == 10


# --------------------------------------------------------------------------- systems


def test_run_chunk_systems_uses_the_production_fusion_and_refuse_reproduces_it() -> None:
    index, embedder = synthetic_index()
    for fusion in (rt.Fusion("rrf", 0.5), rt.Fusion("weighted", 0.3)):
        runs = rt.run_chunk_systems(index, embedder, queries(), fusion, depth=10)
        assert runs.fusion == fusion
        for qid in runs.bm25:
            assert len(runs.bm25[qid]) <= 10 * CANDIDATE_FACTOR
        # The candidates kept are exactly what hybrid_search fused.
        assert rt.refuse(runs, fusion, depth=10) == runs.page_run("hybrid")


def test_weight_one_is_bm25_and_weight_zero_is_vector() -> None:
    index, embedder = synthetic_index()
    runs = rt.run_chunk_systems(index, embedder, queries(), rt.Fusion("rrf", 0.5), depth=10)
    only_bm25 = rt.refuse(runs, rt.Fusion("weighted", 1.0), depth=10)
    only_vector = rt.refuse(runs, rt.Fusion("weighted", 0.0), depth=10)
    for qid in runs.bm25:
        # Weight 1.0 is the BM25 order followed by vector-only hits, so its first pages are
        # BM25's (likewise for 0.0 and the vector order).
        bm25_pages = rt.pages(runs.bm25[qid])
        assert only_bm25[qid][: len(bm25_pages)] == bm25_pages[: len(only_bm25[qid])]
        assert only_vector[qid] == rt.pages(runs.vector[qid][:10])


def test_production_fusion_reads_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_settings()
    try:
        default = get_settings()
        assert rt.production_fusion() == rt.Fusion(
            default.retrieval_fusion, default.retrieval_bm25_weight
        )
        monkeypatch.setenv("RETRIEVAL_FUSION", "weighted")
        monkeypatch.setenv("RETRIEVAL_BM25_WEIGHT", "0.25")
        reset_settings()
        assert rt.production_fusion() == rt.Fusion("weighted", 0.25)
    finally:
        reset_settings()


def test_fusion_sweep_rows_and_leave_one_out() -> None:
    def hits(*pages: int) -> list[Hit]:
        return [Hit(uuid.uuid5(rt.CHUNK_VERSION, f"D:{p}"), "D", p, p, 1.0 / (i + 1), "")
                for i, p in enumerate(pages)]  # fmt: skip

    runs = rt.ChunkRuns(
        bm25={"q1": hits(1, 2), "q2": hits(3, 4)},
        vector={"q1": hits(2, 1), "q2": hits(4, 3)},
        hybrid={},
        fusion=rt.Fusion("rrf", 0.5),
    )

    def first_page_is_relevant(run: dict[str, list[str]]) -> dict[str, float]:
        relevant = {"q1": "D:1", "q2": "D:4"}  # BM25 is right on q1, vectors on q2
        return {q: float(run[q][0] == relevant[q]) for q in run}

    rows, loo, chosen = rt.fusion_sweep(runs, first_page_is_relevant, weights=(0.0, 1.0))  # type: ignore[arg-type]
    assert [r.label for r in rows] == ["weighted 0.0", "weighted 1.0", "rrf"]
    assert [r.mean for r in rows] == [0.5, 0.5, 0.5]
    # Holding out q1, weight 0.0 wins on q2 (and scores 0 on q1); holding out q2,
    # weight 1.0 wins on q1 (and scores 0 on q2): choosing the weight is worth nothing.
    assert chosen == ["weighted 0.0", "weighted 1.0"]
    assert loo == {"q1": 0.0, "q2": 0.0}


# --------------------------------------------------------------------------- sentences


def test_reference_sentences_split_and_window() -> None:
    text = (
        "Generally, if you can depreciate intangible property, you usually use the\n"
        "straight line method. Short one. (a) The second sentence is long enough to be "
        "counted by the check. " + "X" * 250 + "."
    )
    assert rt.reference_sentences(text) == [
        "Generally, if you can depreciate intangible property, you usually use the "
        "straight line method.",
        "(a) The second sentence is long enough to be counted by the check.",
    ]
    assert rt.reference_sentences("") == []


def test_intact_sentences_need_one_chunk_to_hold_the_whole_sentence() -> None:
    sentence = "You usually use the straight line method of depreciation."
    chunks = ["... you usually use the straight\nline method of depreciation. More", "other"]
    split = ["you usually use the straight", "line method of depreciation."]
    assert rt.intact_sentences([sentence], chunks) == [True]
    assert rt.intact_sentences([sentence], split) == [False]  # straddles two chunks
    assert rt.intact_sentences([sentence], []) == [False]


# --------------------------------------------------------------------------- harness


def test_score_chunk_systems_on_the_synthetic_corpus() -> None:
    data = ds.synthetic_smoke()["retrieval_qrels"]
    assert isinstance(data, ds.RetrievalQrels)
    results = score_chunk_systems(
        data, "dev", CORPUS, ["bm25_chunks", "vector_chunks", "hybrid"], HashEmbedder(), sweep=True
    )
    assert [r.system for r in results] == ["bm25_chunks", "vector_chunks", "hybrid"]
    for result in results:
        assert not result.skipped
        metrics = {r.metric: r for r in result.rows}
        assert set(metrics) == {"ndcg_10", "recall_5", "recall_10", "mrr", "hole_rate_at_10"}
        assert metrics["ndcg_10"].n == len(data.qrels("dev"))
        assert 0.0 <= metrics["ndcg_10"].value <= 1.0
        assert len(metrics["mrr"].details["per_query"]) == metrics["mrr"].n
    by_system = {r.system: {m.metric: m.value for m in r.rows} for r in results}
    # The synthetic qrels are mostly exact-code queries, which BM25 answers.
    assert by_system["bm25_chunks"]["recall_10"] >= 0.95
    assert "fusion rrf" in " ".join(results[-1].notes)
    [(title, headers, body)] = results[-1].tables
    assert title.startswith("Fusion sweep") and body[-1][0] == "leave-one-out choice"
    assert len(body) == len(rt.SWEEP_WEIGHTS) + 2


def test_score_chunk_systems_skips_without_queries_or_corpus(tmp_path: Path) -> None:
    data = ds.synthetic_smoke()["retrieval_qrels"]
    assert isinstance(data, ds.RetrievalQrels)
    for split, corpus in (("held_out", CORPUS), ("dev", tmp_path), ("dev", None)):
        results = score_chunk_systems(data, split, corpus, ["hybrid"], HashEmbedder())
        assert [(r.system, r.skipped) for r in results] == [("hybrid", True)]


def test_paired_comparison_rows_and_table() -> None:
    from evals.run import MetricRow

    def system(name: str, values: list[float]) -> TaskResult:
        per_query = {f"q{i}": v for i, v in enumerate(values)}
        rows = [
            MetricRow(m, sum(values) / len(values), len(values), details={"per_query": per_query})
            for m in ("ndcg_10", "recall_5", "recall_10", "mrr")
        ]
        return TaskResult("retrieval_qrels", name, rows=rows)

    a, b = system("a", [0.0, 0.5, 1.0, 0.5]), system("b", [1.0, 0.5, 1.0, 1.0])
    skipped = TaskResult("retrieval_qrels", "c", skipped=True)
    result = paired_comparison([a, b, skipped], iterations=500)
    assert [r.metric for r in result.rows][0] == "ndcg_10:b-a"
    assert len(result.rows) == 4
    first = result.rows[0]
    assert first.value == pytest.approx(0.375) and first.n == 4  # (1 + 0 + 0 + 0.5) / 4
    assert first.details["wins"] == 2 and first.details["ties"] == 2
    assert first.interval is not None and first.interval[0] <= 0.375 <= first.interval[1]
    assert result.tables[0][2][0][0] == "b - a"
    assert paired_comparison([a]).skipped


def test_evaluate_with_every_system_adds_the_comparison() -> None:
    results = evaluate(
        ds.synthetic_smoke(),
        split="dev",
        repeats=1,
        predictions=None,
        corpus_dir=CORPUS,
        corpus_text="parser",
        retrieval_systems=RETRIEVAL_SYSTEMS,
        embedder=HashEmbedder(),
    )
    retrieval = [r for r in results if r.dataset == "retrieval_qrels"]
    assert [r.system for r in retrieval] == [
        "bm25_pages_parser", "bm25_chunks", "vector_chunks", "hybrid", "paired_bootstrap",
    ]  # fmt: skip
    pairs = {r.metric.split(":")[1] for r in retrieval[-1].rows}
    assert len(pairs) == 6  # every two of the four systems
    with pytest.raises(ValueError):
        evaluate(ds.synthetic_smoke(), split="dev", repeats=1, predictions=None,
                 corpus_dir=CORPUS, retrieval_systems=["dense"])  # fmt: skip


def test_default_retrieval_is_unchanged() -> None:
    results = evaluate(
        ds.synthetic_smoke(), split="dev", repeats=1, predictions=None, corpus_dir=CORPUS
    )
    assert [r.system for r in results if r.dataset == "retrieval_qrels"] == ["bm25_pages"]


def test_retrieval_system_flags() -> None:
    assert parse_args([]).retrieval_systems == ["bm25_pages"]
    assert parse_args(["--retrieval-systems", "all"]).retrieval_systems == list(RETRIEVAL_SYSTEMS)
    args = parse_args(["--retrieval-systems", "hybrid, bm25_chunks", "--fusion-sweep"])
    assert args.retrieval_systems == ["hybrid", "bm25_chunks"] and args.fusion_sweep
    for bad in (["--retrieval-systems", "dense"], ["--retrieval-systems", "hybrid,hybrid"],
                ["--retrieval-systems", ","], ["--fusion-sweep"]):  # fmt: skip
        with pytest.raises(SystemExit):
            parse_args(bad)


def test_dry_run_cli_with_chunk_systems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("EMBEDDING_MODEL", "hash")
    reset_settings()
    from basis.retrieval import embed

    embed._embedder.cache_clear()
    try:
        report = tmp_path / "eval.md"
        argv = ["--dry-run", "--retrieval-systems", "bm25_chunks,hybrid", "--fusion-sweep",
                "--report", str(report)]  # fmt: skip
        assert main(argv) == 0
    finally:
        reset_settings()
        embed._embedder.cache_clear()
    text = report.read_text()
    assert "## retrieval_qrels (system: `bm25_chunks`)" in text
    assert "## retrieval_qrels (system: `paired_bootstrap`)" in text
    assert "### Paired differences" in text and "### Fusion sweep" in text
    assert "hash-v1-384" in text


def test_hole_rate_counts_pages_unjudged_for_that_query() -> None:
    from evals.run import _ir_rows

    qrels = {"q1": {"A:1": 2, "A:2": 0}, "q2": {"B:1": 1}}
    # A:2 is judged (grade 0) for q1; B:1 is judged for q2 but not for q1: a hole there.
    run = {"q1": ["A:1", "A:2", "B:1", "C:1"], "q2": ["B:1", "A:1"]}
    rows = {r.metric: r for r in _ir_rows(run, qrels)}
    hole = rows["hole_rate_at_10"]
    assert hole.n == 6 and hole.value == pytest.approx(3 / 6)  # q1: B:1, C:1; q2: A:1
