"""``python -m basis.retrieval.ingest`` against the test database (hash embedder).

``main`` runs in a worker thread because it calls ``asyncio.run`` itself, exactly as it
does from the command line; one test also runs the module as a real subprocess.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from dbtest_support import BACKEND_ROOT
from fixtures import fixture_path

from basis.retrieval.ingest import main
from basis.retrieval.store import PostgresChunkStore

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("EMBEDDING_MODEL", "hash")


def pdf(doc_id: str) -> str:
    return str(fixture_path("corpus", f"{doc_id}.pdf"))


async def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = await asyncio.to_thread(main, list(argv))
    out, err = capsys.readouterr()
    return code, out, err


async def test_ingest_then_list_versions(
    store: PostgresChunkStore, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = await run(
        capsys, "--corpus", "reference", "--pdf", pdf("SYN_PUB_A"), "--doc-id", "SYN_PUB_A",
        "--pdf", pdf("SYN_PUB_B"), "--doc-id", "SYN_PUB_B",
    )  # fmt: skip
    assert code == 0, err
    summary = json.loads(out)
    assert (summary["version"], summary["embedding_model"]) == (1, "hash-v1-384")
    assert [d["doc_id"] for d in summary["documents"]] == ["SYN_PUB_A", "SYN_PUB_B"]
    assert summary["chunks"] > 0 and "published version 1" in err

    code, out, _ = await run(capsys, "--corpus", "reference", "--pdf", pdf("SYN_PUB_C"),
                             "--doc-id", "SYN_PUB_C")  # fmt: skip
    assert code == 0 and json.loads(out)["carried_forward_chunks"] > 0
    code, out, _ = await run(capsys, "--pdf", pdf("SYN_PUB_C"), "--doc-id", "SYN_PUB_C", "--fresh")
    assert code == 0 and json.loads(out)["carried_forward_chunks"] == 0

    code, out, _ = await run(capsys, "--list-versions")
    assert code == 0
    rows = [json.loads(line) for line in out.splitlines()]
    assert [(r["version"], r["doc_ids"]) for r in rows] == [
        (1, ["SYN_PUB_A", "SYN_PUB_B"]),
        (2, ["SYN_PUB_A", "SYN_PUB_B", "SYN_PUB_C"]),
        (3, ["SYN_PUB_C"]),
    ]
    assert all(r["published_at"] for r in rows)
    latest = await store.latest_version("reference", None)
    assert latest is not None and str(latest.id) == rows[-1]["corpus_version_id"]


async def test_unusable_pdf_fails_cleanly_and_writes_nothing(
    store: PostgresChunkStore, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    for bad in ("not_a_pdf.pdf", "empty.pdf"):
        code, out, err = await run(
            capsys, "--pdf", str(fixture_path("malformed", bad)), "--doc-id", "BAD"
        )
        assert (code, out) == (2, "") and err.startswith("error:")
    code, _, err = await run(capsys, "--pdf", str(tmp_path / "missing.pdf"), "--doc-id", "X")
    assert code == 2 and "missing.pdf" in err
    assert await store.list_versions("reference") == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--pdf", "a.pdf"],  # no --doc-id
        ["--pdf", "a.pdf", "--doc-id", "A", "--doc-id", "B"],
        [],  # nothing to do
        ["--corpus", "study", "--pdf", "a.pdf", "--doc-id", "A"],
        ["--list-versions", "--corpus", "study"],  # needs --study-id
        ["--pdf", "a.pdf", "--doc-id", "A", "--max-pages", "0"],
    ],
)
def test_bad_arguments_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(argv)
    assert caught.value.code == 2


def test_runs_as_a_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "basis.retrieval.ingest", "--help"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0 and "--list-versions" in result.stdout
