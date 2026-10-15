#!/usr/bin/env python3
"""Refuse client data, credentials, and oversized files in this public repository.

The repository is public (BUILD_PLAN section 0, rule 1). This guard runs as a
pre-commit hook (``--staged``) and as a CI job (``--ci``) and fails when a file
that is about to be committed, or that is already tracked, looks like client
data or a credential.

Rules, applied to every checked file:

1. Blocked hash: the file's sha256 is listed in ``blocked_file_hashes.txt``
   (known client evaluation files). Never overridable.
2. Blocked location: the path is under a directory that only ever holds client
   copies (``frontend/src/mock/``, ``frontend/public/mock/``,
   ``backEnd/evidence_layer/data/studies/``). Never overridable.
3. Data extension: pdf, images, spreadsheets, docx, csv, jsonl, pkl, faiss
   outside the allowlisted directories.
4. Size: larger than 5 MiB.
5. Sensitive path: a ``credentials/`` directory, a ``.env``-style file, or a
   service-account key file name.

Rules 3-5 can be waived for one exact file by a pinned entry in
``client_data_exceptions.txt`` (``<sha256>  <path>``). The entry matches only
while both the path and the content hash are unchanged, so editing an excepted
file re-triggers the check and needs a fresh, reviewed exception.

Content is read from the git index (``git cat-file``), not the working tree, so
the guard checks exactly what is staged or committed.

Standard library only; compatible with Python 3.9+.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
DEFAULT_BLOCKLIST = HERE / "blocked_file_hashes.txt"
DEFAULT_EXCEPTIONS = HERE / "client_data_exceptions.txt"

MAX_BYTES = 5 * 1024 * 1024  # 5 MiB

DATA_EXTENSIONS = frozenset(
    {
        "pdf",
        "jpg",
        "jpeg",
        "png",
        "webp",
        "heic",
        "tif",
        "tiff",
        "xlsx",
        "xls",
        "docx",
        "csv",
        "jsonl",
        "pkl",
        "faiss",
    }
)

# Directories where data-like extensions are expected (rule 3 only).
DEFAULT_ALLOWED_DIRS = (
    "docs/",
    "frontend/public/images/",
    "frontend/src/app/",  # favicons and app icons
    "backEnd/tests/fixtures/synthetic/",
)

# Directories that only ever hold client copies; anything tracked there fails.
BLOCKED_PREFIXES = (
    "frontend/src/mock/",
    "frontend/public/mock/",
    "backEnd/evidence_layer/data/studies/",
)

# Git file modes that do not carry file content we can check.
SKIP_MODES = frozenset({"160000"})  # submodule (gitlink)


class Entry(NamedTuple):
    path: str
    mode: str
    blob: str


class Finding(NamedTuple):
    path: str
    reason: str


# --------------------------------------------------------------------------- git


def _git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {msg}")
    return proc.stdout


def repo_root(start: Path) -> Path:
    out = _git(start, "rev-parse", "--show-toplevel")
    return Path(out.decode("utf-8").strip())


def _split_z(data: bytes) -> list[str]:
    return [p.decode("utf-8", "surrogateescape") for p in data.split(b"\0") if p]


def tracked_entries(repo: Path) -> list[Entry]:
    """Every index entry: (path, mode, blob sha)."""
    entries = []
    for record in _split_z(_git(repo, "ls-files", "-s", "-z")):
        meta, path = record.split("\t", 1)
        mode, blob, _stage = meta.split(" ")
        entries.append(Entry(path=path, mode=mode, blob=blob))
    return entries


def staged_entries(repo: Path) -> list[Entry]:
    """Added, modified, or type-changed index entries relative to HEAD.

    Uses ``--raw`` so the staged blob sha comes straight from the diff: no
    second lookup by path (which git would treat as a glob pathspec) and no
    argument-length limit on very large commits. ``--no-renames`` makes a
    rename appear as delete + add, so a renamed file is checked under its new
    name instead of slipping past the A/M/T filter.
    """
    fields = _split_z(
        _git(
            repo,
            "diff",
            "--cached",
            "--raw",
            "--no-abbrev",
            "--no-renames",
            "--diff-filter=AMT",
            "-z",
        )
    )
    entries = []
    # -z raw output alternates ":<src mode> <dst mode> <src sha> <dst sha> <status>" and <path>.
    for meta, path in zip(fields[0::2], fields[1::2]):
        _src_mode, dst_mode, _src_sha, dst_sha, _status = meta.lstrip(":").split(" ")
        entries.append(Entry(path=path, mode=dst_mode, blob=dst_sha))
    return entries


def hash_blobs(repo: Path, blobs: Iterable[str]) -> dict[str, tuple[str, int]]:
    """Stream blobs through one ``git cat-file --batch`` process.

    Returns {blob sha: (sha256 hex, size in bytes)}.
    """
    unique = sorted(set(blobs))
    result: dict[str, tuple[str, int]] = {}
    if not unique:
        return result
    proc = subprocess.Popen(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    assert proc.stdin is not None and proc.stdout is not None
    try:
        for blob in unique:
            proc.stdin.write(blob.encode("ascii") + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline().decode("ascii").split()
            if len(header) != 3:
                raise RuntimeError(f"git cat-file: unexpected reply for {blob}: {header}")
            size = int(header[2])
            digest = hashlib.sha256()
            remaining = size
            while remaining:
                chunk = proc.stdout.read(min(remaining, 1 << 20))
                if not chunk:
                    raise RuntimeError(f"git cat-file: short read for {blob}")
                digest.update(chunk)
                remaining -= len(chunk)
            proc.stdout.read(1)  # trailing newline after the content
            result[blob] = (digest.hexdigest(), size)
    finally:
        proc.stdin.close()
        proc.wait()
    return result


# ----------------------------------------------------------------- list files


def load_hashes(path: Path) -> set[str]:
    hashes: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if line:
            if len(line) != 64 or any(c not in "0123456789abcdef" for c in line):
                raise ValueError(f"{path}: not a sha256 hex digest: {raw!r}")
            hashes.add(line)
    return hashes


def load_exceptions(path: Path) -> set[tuple[str, str]]:
    """Parse ``<sha256>  <path>  # reason`` lines into {(sha256, path)}."""
    pairs: set[tuple[str, str]] = set()
    if not path.exists():
        return pairs
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f"{path}: expected '<sha256>  <path>', got {raw!r}")
        pairs.add((parts[0].lower(), parts[1].strip()))
    return pairs


# ---------------------------------------------------------------------- rules


def _norm_dir(d: str) -> str:
    d = d.replace("\\", "/").strip("/")
    return d + "/" if d else ""


def is_sensitive_path(path: str) -> str | None:
    lowered = path.lower()
    parts = lowered.split("/")
    name = parts[-1]
    if "credentials" in parts[:-1]:
        return "path is inside a credentials/ directory"
    if name.startswith(".env") or name.endswith(".env"):
        return "looks like an environment file (.env*)"
    if name.endswith("-sa-key.json") or name.endswith("-service-account.json"):
        return "looks like a service-account key file"
    if "-firebase-adminsdk-" in name and name.endswith(".json"):
        return "looks like a Firebase Admin SDK key file"
    return None


def check_entries(
    entries: Sequence[Entry],
    digests: dict[str, tuple[str, int]],
    blocked: set[str],
    exceptions: set[tuple[str, str]],
    allowed_dirs: Sequence[str],
) -> list[Finding]:
    findings: list[Finding] = []
    allowed = tuple(_norm_dir(d) for d in allowed_dirs)
    for entry in entries:
        path = entry.path
        sha256, size = digests[entry.blob]

        # Rules that can never be waived.
        if sha256 in blocked:
            findings.append(
                Finding(path, f"content matches a blocked client file hash ({sha256[:12]}...)")
            )
            continue
        # Case-insensitive: macOS and Windows checkouts treat "Mock/" as "mock/".
        blocked_prefix = next(
            (p for p in BLOCKED_PREFIXES if path.lower().startswith(p.lower())), None
        )
        if blocked_prefix:
            findings.append(
                Finding(path, f"{blocked_prefix} holds client copies and must never be tracked")
            )
            continue

        if (sha256, path) in exceptions:
            continue

        reasons = []
        ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
        if ext in DATA_EXTENSIONS and not path.startswith(allowed):
            reasons.append(f".{ext} file outside allowlisted directories ({', '.join(allowed)})")
        if size > MAX_BYTES:
            reasons.append(f"{size / (1024 * 1024):.1f} MiB exceeds the 5 MiB limit")
        sensitive = is_sensitive_path(path)
        if sensitive:
            reasons.append(sensitive)
        for reason in reasons:
            findings.append(Finding(path, reason))
    return findings


# ----------------------------------------------------------------------- main


def run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="check files staged for commit")
    mode.add_argument("--ci", action="store_true", help="check every tracked file")
    parser.add_argument(
        "--allow-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="extra directory where data extensions are allowed (repeatable)",
    )
    parser.add_argument("--blocklist", type=Path, default=DEFAULT_BLOCKLIST)
    parser.add_argument("--exceptions", type=Path, default=DEFAULT_EXCEPTIONS)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="path inside the repo")
    args = parser.parse_args(argv)

    root = repo_root(args.repo)
    blocked = load_hashes(args.blocklist)
    exceptions = load_exceptions(args.exceptions)

    if args.staged:
        entries = staged_entries(root)
        scope = "staged"
    else:
        entries = tracked_entries(root)
        scope = "tracked"
    entries = [e for e in entries if e.mode not in SKIP_MODES]

    digests = hash_blobs(root, (e.blob for e in entries))
    findings = check_entries(
        entries,
        digests,
        blocked,
        exceptions,
        list(DEFAULT_ALLOWED_DIRS) + list(args.allow_dir),
    )

    if not findings:
        print(f"client-data-guard: {len(entries)} {scope} file(s) checked, no problems.")
        return 0

    print(f"client-data-guard: {len(findings)} problem(s) in {scope} files:", file=sys.stderr)
    for f in findings:
        print(f"  BLOCKED {f.path}: {f.reason}", file=sys.stderr)
    fix = (
        "Unstage it (git restore --staged <path>)"
        if args.staged
        else "Remove it from git (git rm --cached <path>)"
    )
    print(
        "\nThis repository is public. Client documents, derived data, credentials\n"
        "and large binaries must stay out of git (BUILD_PLAN section 0).\n"
        f"{fix} and keep the file outside the repo.\n"
        "A genuine false positive can be waived only by a pinned\n"
        "'<sha256>  <path>  # reason' line in tooling/client_data_exceptions.txt,\n"
        "added in a reviewed change.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(run())
