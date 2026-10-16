#!/usr/bin/env python3
"""Capture a reproducible test-evidence bundle for one version of Basis.

Writes ``docs/test-evidence/<label>/`` with:

* ``manifest.json`` / ``README.md``: exactly what was under test (git sha, tag, branch,
  dirty flag), the toolchain (Python, uv, Temporal CLI, Docker, the Postgres image used by
  the tests), the versions of the key packages from ``uv.lock``, the versioned artefacts
  inside the code (prompt versions, rules version, workflow version, embedding model),
  the machine, and the exact commands that were run.
* ``tests_collected.txt``: every test id pytest collected for this run.
* ``gates.log``: the full output of ruff, ruff format, mypy, the OpenAPI check and pytest.
* ``junit.xml``: the JUnit report pytest wrote.
* ``summary.json``: pass/fail/skip/xfail counts parsed from the JUnit report, with the
  reason for every skip and expected failure.

The main pytest run is the CI set: it deselects the optional sets below. Each optional
set runs as its own pytest pass, only when the data it needs is present on this
machine, and writes ``junit_<name>.xml``, ``summary_<name>.json`` and a ``pytest_<name>``
section of ``gates.log``; ``manifest.json`` records, for every optional set, whether it
ran and why not. ``--skip-optional`` leaves them out.

* ``real_corpus`` (marker ``eval_data``): ``tests/integration/retrieval/test_real_corpus.py``,
  the six private reference PDFs through the ingest CLI with the real embedder (chunk
  counts against the pinned baseline, embeddings, two-column sentences, the rules-table
  citations, ingest and cold BM25 timings). Needs ``BASIS_EVAL_DATA_DIR`` with a
  ``corpus/`` directory, Docker and MiniLM in the Hugging Face cache (the test skips
  itself with the reason if the PDFs or the model are missing). Takes several minutes.

Usage, from the repository root::

    uv run --project backEnd python tooling/evidence/capture.py --label v0.3.0 [--extra-args "-m 'not slow'"]
    BASIS_EVAL_DATA_DIR=~/basis-eval-data uv run --project backEnd python tooling/evidence/capture.py --label v0.8.0

The script never reads ``.env`` and never prints secrets; it records names, not values.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backEnd"
KEY_PACKAGES = [
    "fastapi",
    "pydantic",
    "sqlalchemy",
    "asyncpg",
    "alembic",
    "pgvector",
    "temporalio",
    "firebase-admin",
    "google-cloud-storage",
    "openai",
    "azure-ai-documentintelligence",
    "rank-bm25",
    "sentence-transformers",
    "pdfplumber",
    "pymupdf",
    "tiktoken",
    "pytest",
    "testcontainers",
    "schemathesis",
    "ruff",
    "mypy",
]


# Test sets that need data CI does not have: name -> (pytest marker, what they need).
OPTIONAL_SUITES: dict[str, tuple[str, str]] = {
    "real_corpus": ("eval_data", "BASIS_EVAL_DATA_DIR with a corpus/ directory"),
}


def optional_suite_missing(name: str, env: dict[str, str]) -> str | None:
    """Why optional set ``name`` cannot run here, or ``None`` when its data is present."""
    if name == "real_corpus":
        value = env.get("BASIS_EVAL_DATA_DIR")
        if not value:
            return "BASIS_EVAL_DATA_DIR is not set"
        if not (Path(value).expanduser() / "corpus").is_dir():
            return "BASIS_EVAL_DATA_DIR has no corpus/ directory"
        return None
    raise KeyError(name)


def deselect_optional() -> list[str]:
    """The pytest arguments that leave every optional set out (the CI set)."""
    return ["-m", " and ".join(f"not {marker}" for marker, _ in OPTIONAL_SUITES.values())]


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def git(*args: str) -> str:
    return run(["git", *args], ROOT)[1].strip()


def tool_version(cmd: list[str]) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    code, out = run(cmd, ROOT)
    return out.strip().splitlines()[0] if code == 0 and out.strip() else None


def locked_versions() -> dict[str, str]:
    lock = tomllib.loads((BACKEND / "uv.lock").read_text())
    wanted = {name.lower() for name in KEY_PACKAGES}
    return {
        pkg["name"]: pkg["version"]
        for pkg in lock.get("package", [])
        if pkg.get("name", "").lower() in wanted
    }


def grep_first(path: Path, pattern: str) -> str | None:
    if not path.exists():
        return None
    match = re.search(pattern, path.read_text())
    return match.group(1) if match else None


def alembic_head() -> str | None:
    versions = sorted((BACKEND / "basis" / "db" / "alembic" / "versions").glob("[0-9]*.py"))
    if not versions:
        return None
    return grep_first(versions[-1], r'^revision(?::\s*str)?\s*=\s*"([^"]+)"') or versions[-1].stem


def code_artefact_versions() -> dict[str, object]:
    prompts: dict[str, list[str]] = {}
    prompts_dir = BACKEND / "basis" / "prompts"
    if prompts_dir.exists():
        for name_dir in sorted(
            p for p in prompts_dir.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
        ):
            prompts[name_dir.name] = sorted(p.stem for p in name_dir.glob("*.md"))
    return {
        "prompts": prompts,
        "rules_version": grep_first(
            BACKEND / "basis" / "domain" / "rules.py", r'RULES_VERSION(?::\s*Final)?\s*=\s*"([^"]+)"'
        ),
        "postgres_test_image": grep_first(
            BACKEND / "tests" / "dbtest_support.py", r'PG_IMAGE\s*=\s*"([^"]+)"'
        ),
        "embedding_model_default": grep_first(
            BACKEND / "basis" / "config.py", r'embedding_model:\s*str\s*=\s*"([^"]+)"'
        ),
        "alembic_head": alembic_head(),
        "workflow_version_source": "settings.git_sha (the commit the worker image is built from)",
    }


def parse_junit(path: Path) -> dict[str, object]:
    tree = ET.parse(path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    skips: list[dict[str, str]] = []
    xfails: list[dict[str, str]] = []
    failures: list[str] = []
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0))
        for case in suite.iter("testcase"):
            test_id = f"{case.get('classname')}::{case.get('name')}"
            skipped = case.find("skipped")
            if skipped is not None:
                entry = {"test": test_id, "reason": skipped.get("message", "")}
                if (skipped.get("type") or "").endswith("xfail") or "xfail" in (
                    skipped.get("message") or ""
                ).lower():
                    xfails.append(entry)
                else:
                    skips.append(entry)
            if case.find("failure") is not None or case.find("error") is not None:
                failures.append(test_id)
    return {
        "totals": totals,
        "passed": totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"],
        "skipped": skips,
        "xfailed": xfails,
        "failed": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="version label, e.g. v0.3.0 or stage2-<sha>")
    parser.add_argument(
        "--extra-args", default="", help="extra pytest args (an -m here replaces the CI-set -m)"
    )
    parser.add_argument("--skip-gates", action="store_true", help="only capture metadata")
    parser.add_argument(
        "--skip-optional", action="store_true", help="do not run the optional data-gated sets"
    )
    args = parser.parse_args()

    out = ROOT / "docs" / "test-evidence" / args.label
    out.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")

    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH"}}
    env["BASIS_ENV"] = "test"
    env["GIT_SHA"] = "test-sha"

    commands = {
        "collect": ["uv", "run", "--no-sync", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        "ruff_check": ["uv", "run", "--no-sync", "ruff", "check", "basis", "tests"],
        "ruff_format": ["uv", "run", "--no-sync", "ruff", "format", "--check", "basis", "tests"],
        "mypy": ["uv", "run", "--no-sync", "mypy"],
        "openapi": ["uv", "run", "--no-sync", "python", "-m", "basis.api.openapi", "--check"],
        "pytest": [
            "uv",
            "run",
            "--no-sync",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--timeout=600",
            "-rfEsx",
            f"--junitxml={out / 'junit.xml'}",
            *deselect_optional(),
            *(args.extra_args.split() if args.extra_args else []),
        ],
    }
    optional: dict[str, dict[str, object]] = {}
    for name, (marker, needs) in OPTIONAL_SUITES.items():
        missing = "--skip-optional given" if args.skip_optional else optional_suite_missing(name, env)
        optional[name] = {"marker": marker, "needs": needs, "ran": False, "reason": missing}
        if missing is None:
            commands[f"pytest_{name}"] = [
                "uv", "run", "--no-sync", "pytest", "-q", "-p", "no:cacheprovider",
                "--timeout=3600", "-rfEsxP", "-m", marker,
                f"--junitxml={out / f'junit_{name}.xml'}",
            ]  # fmt: skip

    results: dict[str, int] = {}
    if not args.skip_gates:
        code, collected = run(commands["collect"], BACKEND, env)
        (out / "tests_collected.txt").write_text(collected)
        results["collect"] = code
        gates = ["ruff_check", "ruff_format", "mypy", "openapi", "pytest"]
        gates += [name for name in commands if name.startswith("pytest_")]
        with (out / "gates.log").open("w") as log:
            log.write(f"# evidence capture {started} label={args.label}\n")
            for name in gates:
                log.write(f"\n## {name}\n$ {' '.join(commands[name])}\n")
                code, text = run(commands[name], BACKEND, env)
                results[name] = code
                log.write(text)
                log.write(f"\n[exit {code}]\n")
                suite = name.removeprefix("pytest_")
                if suite in optional:
                    optional[suite]["ran"] = True
                    junit = out / f"junit_{suite}.xml"
                    if junit.exists():
                        (out / f"summary_{suite}.json").write_text(
                            json.dumps(parse_junit(junit), indent=2)
                        )
    elif not args.skip_optional:
        for entry in optional.values():
            entry["reason"] = entry["reason"] or "--skip-gates given"

    manifest = {
        "label": args.label,
        "captured_at": started,
        "git": {
            "sha": git("rev-parse", "HEAD"),
            "short": git("rev-parse", "--short", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "describe": git("describe", "--tags", "--always"),
            # Uncommitted changes outside the evidence tree (re-capturing a label rewrites
            # tracked files under docs/test-evidence, which is not what "dirty" means).
            "dirty": bool(
                git("status", "--porcelain", "--untracked-files=no", "--", ".", ":!docs/test-evidence")
            ),
            "uv_lock_sha256": subprocess.run(
                ["shasum", "-a", "256", str(BACKEND / "uv.lock")], capture_output=True, text=True
            ).stdout.split()[0],
        },
        "toolchain": {
            "python": sys.version.split()[0],
            "python_pinned": (BACKEND / ".python-version").read_text().strip(),
            "uv": tool_version(["uv", "--version"]),
            "temporal_cli": tool_version(["temporal", "--version"]),
            "docker": tool_version(["docker", "--version"]),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "packages": locked_versions(),
        "code_artefacts": code_artefact_versions(),
        "commands": {k: " ".join(v) for k, v in commands.items()},
        "exit_codes": results,
        "optional_suites": optional,
        "notes": "Secrets are never recorded. Real-provider evaluations are reported separately in EVAL_REPORT.md.",
    }
    summary = parse_junit(out / "junit.xml") if (out / "junit.xml").exists() else None
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if summary is not None:
        (out / "summary.json").write_text(json.dumps(summary, indent=2))

    lines = [
        f"# Test evidence: {args.label}",
        "",
        f"Captured {started} on {manifest['toolchain']['platform']} ({manifest['toolchain']['machine']}).",
        f"Commit `{manifest['git']['sha']}` on `{manifest['git']['branch']}` ({manifest['git']['describe']}){', with uncommitted changes' if manifest['git']['dirty'] else ''}.",
        "",
        "## What was under test",
        "",
        f"- Python {manifest['toolchain']['python']} (pinned {manifest['toolchain']['python_pinned']}), {manifest['toolchain']['uv']}, {manifest['toolchain']['temporal_cli']}, {manifest['toolchain']['docker']}",
        f"- Postgres test image: `{manifest['code_artefacts']['postgres_test_image']}`; Alembic head: `{manifest['code_artefacts']['alembic_head']}`",
        f"- Prompt versions: `{json.dumps(manifest['code_artefacts']['prompts'])}`; rules version: `{manifest['code_artefacts']['rules_version']}`; embedding model default: `{manifest['code_artefacts']['embedding_model_default']}`",
        "- Key packages: " + ", ".join(f"{k} {v}" for k, v in sorted(manifest["packages"].items())),
        f"- `uv.lock` sha256 `{manifest['git']['uv_lock_sha256']}`",
        "",
        "## Commands",
        "",
        *[f"- `{name}`: `{cmd}`" for name, cmd in manifest["commands"].items()],
        "",
        "## Result",
        "",
    ]
    if summary is not None:
        t = summary["totals"]
        skipped_lines = [f"- `{s['test']}`: {s['reason']}" for s in summary["skipped"]] or ["- none"]
        xfail_lines = [f"- `{x['test']}`: {x['reason']}" for x in summary["xfailed"]] or ["- none"]
        failed_lines = [f"- `{f}`" for f in summary["failed"]] or ["- none"]
        lines += [
            f"- pytest: **{summary['passed']} passed**, {t['failures']} failed, {t['errors']} errors, {len(summary['skipped'])} skipped, {len(summary['xfailed'])} expected failures (of {t['tests']} collected)",
            "- gate exit codes: " + ", ".join(f"{k}={v}" for k, v in results.items()),
            "",
            "### Skipped (with reason)",
            "",
            *skipped_lines,
            "",
            "### Expected failures (with reason)",
            "",
            *xfail_lines,
            "",
            "### Failed",
            "",
            *failed_lines,
        ]
    else:
        lines.append("- gates not run (metadata only)")
    lines += ["", "### Optional sets (data-gated, not in CI)", ""]
    for name, entry in optional.items():
        if entry["ran"]:
            path = out / f"summary_{name}.json"
            extra = json.loads(path.read_text()) if path.exists() else None
            counts = (
                f"{extra['passed']} passed, {len(extra['failed'])} failed, {len(extra['skipped'])} skipped"
                if extra
                else "no JUnit report"
            )
            lines.append(f"- `{name}` (`-m {entry['marker']}`): ran, {counts}, exit {results[f'pytest_{name}']}")
        else:
            lines.append(f"- `{name}` (`-m {entry['marker']}`): not run: {entry['reason']} (needs {entry['needs']})")
    lines += ["", "Files: `manifest.json`, `summary.json`, `tests_collected.txt`, `gates.log`, `junit.xml`; per optional set that ran, `junit_<name>.xml` and `summary_<name>.json`."]
    (out / "README.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {out} ; exit codes {results}")
    return 0 if all(code == 0 for code in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
