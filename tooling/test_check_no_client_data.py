"""Tests for tooling/check_no_client_data.py.

Each test builds a throwaway git repository, stages or commits files, and runs
the guard as a subprocess exactly as the pre-commit hook and CI job do.

Run: python3 -m pytest tooling -q
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check_no_client_data.py"
REAL_BLOCKLIST = Path(__file__).resolve().parent / "blocked_file_hashes.txt"
MIB = 1024 * 1024


@pytest.fixture()
def git_env(tmp_path: Path) -> dict[str, str]:
    """Isolate git from the developer's global and system config and hooks."""
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("")
    env = dict(os.environ)
    env.update(
        {
            "GIT_CONFIG_GLOBAL": str(empty),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
    )
    return env


class Repo:
    def __init__(self, root: Path, env: dict[str, str]) -> None:
        self.root = root
        self.env = env
        self.blocklist = root.parent / "blocklist.txt"
        self.blocklist.write_text("")
        self.exceptions = root.parent / "exceptions.txt"
        self.exceptions.write_text("")
        self.git("init", "-q")

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            check=True,
        )
        return proc.stdout.decode()

    def write(self, rel: str, data: bytes) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def add(self, rel: str, data: bytes = b"content\n") -> Path:
        path = self.write(rel, data)
        self.git("add", "--", rel)
        return path

    def commit(self) -> None:
        self.git("commit", "-q", "--no-verify", "-m", "test")

    def guard(self, mode: str, *extra: str) -> tuple[int, str]:
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                mode,
                "--blocklist",
                str(self.blocklist),
                "--exceptions",
                str(self.exceptions),
                *extra,
            ],
            cwd=self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return proc.returncode, proc.stdout.decode()


@pytest.fixture()
def repo(tmp_path: Path, git_env: dict[str, str]) -> Repo:
    root = tmp_path / "repo"
    root.mkdir()
    return Repo(root, git_env)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blocked_lines(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if line.strip().startswith("BLOCKED")]


# ------------------------------------------------------------ blocked hashes


def test_blocked_hash_detected(repo: Repo) -> None:
    secret = b"client appraisal bytes"
    repo.blocklist.write_text(f"# client files\n{sha256(secret)}\n")
    repo.add("notes/innocent_name.txt", secret)
    code, out = repo.guard("--staged")
    assert code == 1
    assert "notes/innocent_name.txt" in out
    assert "blocked client file hash" in out


def test_blocked_hash_detected_inside_allowed_dir_and_despite_exception(repo: Repo) -> None:
    secret = b"client photo bytes"
    repo.blocklist.write_text(sha256(secret) + "\n")
    repo.exceptions.write_text(f"{sha256(secret)}  docs/photo.png  # attempted waiver\n")
    repo.add("docs/photo.png", secret)
    code, out = repo.guard("--staged")
    assert code == 1
    assert "blocked client file hash" in out


def test_blocked_hash_detected_in_ci_mode(repo: Repo) -> None:
    secret = b"committed client bytes"
    repo.blocklist.write_text(sha256(secret).upper() + "\n")  # case-insensitive list
    repo.add("a/b.txt", secret)
    repo.commit()
    code, out = repo.guard("--ci")
    assert code == 1
    assert "a/b.txt" in out


def test_real_blocklist_is_well_formed() -> None:
    lines = [ln for ln in REAL_BLOCKLIST.read_text().splitlines() if ln.split("#")[0].strip()]
    assert lines, "blocklist must not be empty"
    for ln in lines:
        h = ln.split("#")[0].strip()
        assert len(h) == 64 and all(c in "0123456789abcdef" for c in h), ln


def test_malformed_blocklist_is_an_error_not_a_silent_pass(repo: Repo) -> None:
    repo.blocklist.write_text("not-a-hash\n")
    repo.add("ok.txt")
    code, out = repo.guard("--staged")
    assert code != 0
    assert "not a sha256" in out


# ------------------------------------------------------------ allowed dirs


@pytest.mark.parametrize(
    "path",
    [
        "docs/diagram.png",
        "docs/review/screens/shot.jpg",
        "frontend/public/images/logo.png",
        "frontend/src/app/favicon.png",
        "backEnd/tests/fixtures/synthetic/sample.pdf",
        "backEnd/tests/fixtures/synthetic/table.csv",
    ],
)
def test_allowed_dir_passes(repo: Repo, path: str) -> None:
    repo.add(path, b"synthetic")
    code, out = repo.guard("--staged")
    assert code == 0, out
    assert "no problems" in out


def test_plain_source_files_pass(repo: Repo) -> None:
    for path in ["backEnd/basis/app.py", "frontend/src/lib/environment.ts", "README.md"]:
        repo.add(path)
    code, out = repo.guard("--staged")
    assert code == 0, out


def test_allow_dir_flag_extends_allowlist(repo: Repo) -> None:
    repo.add("eval/fixtures/x.pdf", b"synthetic")
    code, _ = repo.guard("--staged")
    assert code == 1
    code, out = repo.guard("--staged", "--allow-dir", "eval/fixtures", "--allow-dir", "other/")
    assert code == 0, out


def test_allowed_dir_prefix_is_not_a_substring_match(repo: Repo) -> None:
    # "docs-private/" must not be treated as "docs/".
    repo.add("docs-private/scan.pdf", b"x")
    code, out = repo.guard("--staged")
    assert code == 1
    assert "docs-private/scan.pdf" in out


# ------------------------------------------------------ disallowed extension


@pytest.mark.parametrize(
    "path",
    [
        "backEnd/report.pdf",
        "frontend/src/components/photo.JPG",
        "data/site.jpeg",
        "data/site.heic",
        "data/plan.tiff",
        "data/costs.xlsx",
        "data/costs.xls",
        "data/memo.docx",
        "data/rows.csv",
        "data/chunks.jsonl",
        "data/index.faiss",
        "data/model.pkl",
        "data/photo.webp",
    ],
)
def test_disallowed_extension_fails(repo: Repo, path: str) -> None:
    repo.add(path, b"x")
    code, out = repo.guard("--staged")
    assert code == 1
    assert path in out


def test_staged_rename_is_checked_under_new_name(repo: Repo) -> None:
    repo.add("docs/scan.pdf", b"pdf bytes that are allowed in docs")
    repo.commit()
    (repo.root / "backEnd").mkdir()
    repo.git("mv", "docs/scan.pdf", "backEnd/scan.pdf")
    code, out = repo.guard("--staged")
    assert code == 1
    assert "backEnd/scan.pdf" in out


def test_mock_dirs_are_blocked_whatever_the_extension(repo: Repo) -> None:
    repo.add("frontend/src/mock/studyData.ts", b"export const x = 1;\n")
    repo.add("frontend/public/mock/readme.txt", b"x")
    repo.add("backEnd/evidence_layer/data/studies/s1/chunks.json", b"{}")
    code, out = repo.guard("--staged")
    assert code == 1
    assert len(blocked_lines(out)) == 3


def test_mock_dir_match_is_case_insensitive(repo: Repo) -> None:
    repo.add("frontend/src/Mock/studyData.ts", b"export const x = 1;\n")
    code, out = repo.guard("--staged")
    assert code == 1
    assert "frontend/src/Mock/studyData.ts" in out


# ------------------------------------------------------------- large files


def test_large_file_fails(repo: Repo) -> None:
    repo.add("backEnd/blob.bin", b"\0" * (5 * MIB + 1))
    code, out = repo.guard("--staged")
    assert code == 1
    assert "exceeds the 5 MiB limit" in out


def test_file_at_exact_limit_passes(repo: Repo) -> None:
    repo.add("backEnd/blob.bin", b"\0" * (5 * MIB))
    code, out = repo.guard("--staged")
    assert code == 0, out


def test_large_file_in_allowed_dir_still_fails(repo: Repo) -> None:
    repo.add("docs/huge.png", b"\0" * (6 * MIB))
    code, out = repo.guard("--staged")
    assert code == 1
    assert "exceeds the 5 MiB limit" in out


# --------------------------------------------------------- credentials paths


@pytest.mark.parametrize(
    "path",
    [
        "backEnd/credentials/basis.json",
        "credentials/.gitkeep",
        "backEnd/.env",
        "frontend/.env.local",
        "backEnd/.env.example",
        "deploy/prod.env",
        ".envrc",
        "backEnd/basis-sa-key.json",
        "keys/project-service-account.json",
        "keys/proj-firebase-adminsdk-abc12.json",
    ],
)
def test_credentials_path_fails(repo: Repo, path: str) -> None:
    repo.add(path, b"{}")
    code, out = repo.guard("--staged")
    assert code == 1
    assert path in out


# ------------------------------------------------------------- mode details


def test_staged_mode_ignores_unstaged_and_committed_files(repo: Repo) -> None:
    repo.add("old/legacy.pdf", b"legacy")
    repo.commit()
    repo.write("untracked/new.pdf", b"not staged")
    repo.add("src/ok.py")
    code, out = repo.guard("--staged")
    assert code == 0, out
    assert "1 staged file(s)" in out


def test_staged_mode_checks_index_content_not_working_tree(repo: Repo) -> None:
    secret = b"client bytes"
    repo.blocklist.write_text(sha256(secret) + "\n")
    path = repo.add("notes.txt", secret)
    path.write_bytes(b"harmless working tree edit")  # index still holds the secret
    code, out = repo.guard("--staged")
    assert code == 1
    assert "blocked client file hash" in out


def test_glob_characters_in_filenames_are_literal(repo: Repo) -> None:
    secret = b"client bytes"
    repo.blocklist.write_text(sha256(secret) + "\n")
    repo.add("notes/a[1]*.txt", secret)
    repo.add("notes/a1x.txt", b"harmless")
    code, out = repo.guard("--staged")
    assert code == 1
    assert "notes/a[1]*.txt" in out
    assert "notes/a1x.txt" not in out


def test_many_staged_files(repo: Repo) -> None:
    for i in range(3000):
        repo.write(f"many/file_{i:05d}_with_a_reasonably_long_name.txt", b"%d" % i)
    repo.write("many/zz.pdf", b"x")
    repo.git("add", "many")
    code, out = repo.guard("--staged")
    assert code == 1
    assert "many/zz.pdf" in out
    assert len(blocked_lines(out)) == 1


def test_staged_deletion_is_not_flagged(repo: Repo) -> None:
    repo.add("old/legacy.pdf", b"legacy")
    repo.commit()
    repo.git("rm", "-q", "--cached", "old/legacy.pdf")
    code, out = repo.guard("--staged")
    assert code == 0, out


def test_ci_mode_scans_all_tracked_files(repo: Repo) -> None:
    repo.add("src/ok.py")
    repo.add("old/legacy.pdf", b"legacy")
    repo.commit()
    code, out = repo.guard("--ci")
    assert code == 1
    assert "old/legacy.pdf" in out
    assert "src/ok.py" not in out


def test_clean_repo_passes_ci(repo: Repo) -> None:
    repo.add("src/ok.py")
    repo.commit()
    code, out = repo.guard("--ci")
    assert code == 0, out


def test_mode_is_required(repo: Repo) -> None:
    code, _ = repo.guard("--allow-dir", "x")  # no --staged / --ci
    assert code == 2


# ------------------------------------------------------------- exceptions


def test_exception_is_pinned_to_path_and_content(repo: Repo) -> None:
    data = b"public reference pdf"
    repo.exceptions.write_text(f"{sha256(data)}  ref/irs.pdf  # public IRS publication\n")
    repo.add("ref/irs.pdf", data)
    code, out = repo.guard("--staged")
    assert code == 0, out

    repo.add("ref/irs.pdf", data + b" edited")  # new content, same path
    code, out = repo.guard("--staged")
    assert code == 1
    assert "ref/irs.pdf" in out


def test_exception_does_not_waive_blocked_directory(repo: Repo) -> None:
    data = b"x"
    repo.exceptions.write_text(f"{sha256(data)}  frontend/src/mock/a.ts\n")
    repo.add("frontend/src/mock/a.ts", data)
    code, _ = repo.guard("--staged")
    assert code == 1


def _run_real_guard_ci(cwd: Path) -> int | None:
    proc = subprocess.run([sys.executable, str(SCRIPT), "--ci"], cwd=cwd, stdout=subprocess.PIPE)
    return proc.returncode


def test_script_runs_on_this_repository() -> None:
    # Smoke test: the guard executes end to end against the real repo and
    # returns a defined status (0 clean, 1 findings) rather than crashing.
    assert _run_real_guard_ci(SCRIPT.parent) in (0, 1)
