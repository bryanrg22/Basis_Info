"""``python -m evals.backfill_manifests``: a run manifest for every run made before manifests.

The runners write ``manifest.json`` from this release on (:mod:`evals.record`). The runs made
before it (the ``predictions/`` and ``appraisal/runs/`` directories under the data dir) have
only what their runner left: ``run.json`` (models, prompt ids, effort, spend), sometimes a
sibling ``<name>.log``, sometimes an ``eval.md`` scoring report, and the directory's own
timestamps. This tool writes, for each such directory, a manifest of the same shape with
``"backfilled": true`` and, per field, where the value came from (``sources``); a field the
files do not establish is ``"unknown"``, never a guess. Nothing is moved or deleted; an
existing manifest is left alone unless ``--force`` is given and it is itself a backfill.

What can be recovered, and from where (EVAL_REPORT section 43 and
``docs/review_2026-10-15/EVAL_RUN_BACKFILL.md``):

* the harness: from the key set of ``run.json`` (each runner has its own), else from the
  files present; ``unknown`` when neither says;
* the prompts: the ids in ``run.json`` (``prompt``, ``prompts``, ``prompt_versions``); their
  sha256 from the prompt manifest, which is exact because released versions are immutable;
* the models, effort, output budget, repeats, spend, call counts: ``run.json``;
* the time: the directory's file birth and modification times (local clock);
* the code: the appraisal runner recorded ``git_sha`` from the start, so its release is the
  nearest tag reachable from that commit; the other runners recorded none, and the runs
  were made from feature branches, so the sha is ``unknown``: the manifest records the
  ``main`` commit at the run's finish time (a lower bound on the code) and the releases
  tagged before and after it; ``dirty`` is unknown for every backfilled run;
* the labels: for a run with ``eval.md``, the datasets it scored by saved predictions and the
  label files those datasets read (by name, so the version); the files' sha256 at the time
  is unknown (``room_inventory_v1.json`` was edited on 2026-10-16; a ``.bak`` exists);
* the terminal output: the sibling ``<name>.log`` where one was kept (referenced, not moved);
* the EVAL_REPORT sections that cite the run by name, and the section a name prefix encodes
  (``cost28_``, ``rooms16_``, ``arch26_``, ``s29_``);
* where the run files in the verification tree (``<data dir>/verification/<component>/
  <version>/``): the first prompt named (call counts are unknown), else what the harness
  stands for; with ``--write`` a relative symbolic link to the directory is made there, so
  the old runs are reachable from the tree without moving them (EVAL_REPORT, the repro
  scripts and the README all name ``predictions/<run>`` paths); a nested appraisal run is
  linked as ``<sweep>__<cell>`` because the sweeps reuse cell names.

Usage (from ``backEnd/``)::

    python -m evals.backfill_manifests                      # dry run: what would be written
    python -m evals.backfill_manifests --write --note docs/review_2026-10-15/EVAL_RUN_BACKFILL.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from basis.llm import prompts
from evals import datasets as ds
from evals import record, registry

__all__ = [
    "PREFIX_SECTIONS",
    "backfill",
    "backfill_directory",
    "guess_harness",
    "main",
    "render_note",
    "report_mentions",
]

UNKNOWN: Final = record.UNKNOWN
DEFAULT_REPORT: Final = record.REPO_DIR.parents[1] / "docs" / "review_2026-10-15" / "EVAL_REPORT.md"
# A name prefix that encodes the EVAL_REPORT section the run was made for.
PREFIX_SECTIONS: Final[dict[str, int]] = {
    "arch26_": 26,
    "cost28_": 28,
    "rooms16_": 16,
    "rooms19_": 19,
    "s25_": 25,
    "s27_": 27,
    "s29_": 29,
}
# ``run.json`` key sets that identify a runner (checked in order; the first match wins).
RUN_SIGNATURES: Final[tuple[tuple[frozenset[str], str], ...]] = (
    (frozenset({"detector", "checkpoint"}), "evals.predict_detector"),
    (frozenset({"scope", "template_rows"}), "evals.predict_multiview"),
    (frozenset({"merged_from"}), "evals.merge_photo_runs"),
    (frozenset({"aggregated_from", "rule"}), "evals.aggregate_runs"),
    (frozenset({"rules_fitted_on_all"}), "evals.fuse_counts"),
    (frozenset({"room_step_mode"}), "evals.replay_rooms"),
    (frozenset({"base", "override", "rooms"}), "evals.combine_rooms"),
    (frozenset({"crops", "materials_changed"}), "evals.predict_surface_crops"),
    (frozenset({"crops", "surfaces"}), "evals.predict_surface_crops"),
    (frozenset({"documents", "fields"}), "evals.predict_fields"),
    (frozenset({"rows", "corpus_glob"}), "evals.predict_assets"),
    (frozenset({"phrases_from", "tiling"}), "evals.predict_boxes"),
    (frozenset({"marks_per_photo"}), "evals.predict_marks"),
    (frozenset({"pointer"}), "evals.predict_points"),
    (frozenset({"grid", "mode"}), "evals.predict_tiles"),
    (frozenset({"detections", "nms_iou"}), "evals.verify_boxes"),
    (frozenset({"room_model"}), "evals.predict_rooms"),
    (frozenset({"prompts", "rooms"}), "evals.predict_rooms"),
    (frozenset({"prompt", "photos"}), "evals.predict_photos"),
    (frozenset({"sets", "mode", "system"}), "evals.appraisal.run"),
    (frozenset({"sets", "mode", "prompt_version"}), "evals.appraisal.run"),
)
# Directories without ``run.json``: the files a runner leaves.
FILE_SIGNATURES: Final[tuple[tuple[frozenset[str], str], ...]] = (
    (frozenset({"selection.json"}), "evals.sam3_select"),
    (frozenset({"prompted.json"}), "evals.manual_add"),
    (frozenset({"replay.json"}), "docs/review_2026-10-15/repro/extraction_replay.py"),
    (frozenset({"grouping.json", "assets.json"}), "live study export (repro/live_export.py)"),
    (frozenset({"boxes.jsonl"}), "evals.predict_boxes (probe)"),
    (frozenset({"instances_raw.jsonl"}), "evals.predict_boxes"),
    (frozenset({"room_inventory.heuristic.json"}), "evals.predict_rooms"),
    (frozenset({"room_inventory.json"}), "evals.predict_rooms"),
    (frozenset({"photo_raw.jsonl"}), "evals.predict_photos"),
    (frozenset({"score.json", "predictions.json"}), "evals.appraisal.run"),
)
_TOKEN: Final = re.compile(r"[A-Za-z0-9_.@-]+")


# --------------------------------------------------------------------------- git


def _git(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True, timeout=60
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip()


class GitHistory:
    """What the repository can say about a moment or a commit: the tags with their dates,
    the ``main`` commit at a time, the release a commit belongs to."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.tags: list[tuple[str, datetime]] = []
        text = _git(
            repo,
            "for-each-ref",
            "--format=%(refname:short) %(creatordate:iso-strict)",
            "refs/tags",
        )
        for line in (text or "").splitlines():
            name, _, when = line.partition(" ")
            try:
                self.tags.append((name, datetime.fromisoformat(when)))
            except ValueError:
                continue
        self.tags.sort(key=lambda t: t[1])

    def releases_around(self, when: datetime | None) -> dict[str, str | None]:
        if when is None or not self.tags:
            return {"before": None, "after": None}
        before = [t for t in self.tags if t[1] <= when]
        after = [t for t in self.tags if t[1] > when]
        return {
            "before": before[-1][0] if before else None,
            "after": after[0][0] if after else None,
        }

    def main_at(self, when: datetime | None) -> str | None:
        if when is None:
            return None
        return _git(self.repo, "rev-list", "-1", f"--before={when.isoformat()}", "main") or None

    def release_of(self, sha: str | None) -> str | None:
        if not sha or _git(self.repo, "cat-file", "-e", f"{sha}^{{commit}}") is None:
            return None
        return _git(self.repo, "describe", "--tags", "--abbrev=0", sha) or None

    def known(self, sha: str | None) -> bool:
        return bool(sha) and _git(self.repo, "cat-file", "-e", f"{sha}^{{commit}}") is not None


# --------------------------------------------------------------------------- the report


def report_mentions(text: str, names: Sequence[str]) -> dict[str, list[int]]:
    """``{run name: [sections]}`` for every run name that appears as a whole token in the
    EVAL_REPORT, by the ``## N.`` section it appears in."""
    wanted = set(names)
    found: dict[str, set[int]] = {}
    section: int | None = None
    for line in text.splitlines():
        heading = re.match(r"^## (\d+)\.", line)
        if heading:
            section = int(heading.group(1))
        if section is None:
            continue
        for token in _TOKEN.findall(line):
            token = token.strip(".@-")
            if token in wanted:
                found.setdefault(token, set()).add(section)
            elif "/" in token and token.rsplit("/", 1)[-1] in wanted:
                found.setdefault(token.rsplit("/", 1)[-1], set()).add(section)
    return {name: sorted(sections) for name, sections in found.items()}


def _prefix_sections(name: str) -> set[int]:
    section = prefix_section(name)
    return set() if section is None else {section}


def prefix_section(name: str) -> int | None:
    for prefix, section in PREFIX_SECTIONS.items():
        if name.startswith(prefix):
            return section
    return None


# --------------------------------------------------------------------------- reading a run


def _load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def guess_harness(summary: Mapping[str, Any] | None, files: set[str]) -> tuple[str, str]:
    """``(harness module, how it was decided)``."""
    if summary:
        keys = frozenset(summary)
        for signature, harness in RUN_SIGNATURES:
            if signature <= keys:
                return harness, f"run.json keys {sorted(signature)}"
        if "complete" in summary and "stopped" in summary:
            pass  # an account stop record: fall through to the files
    for signature, harness in FILE_SIGNATURES:
        if signature <= files:
            return harness, f"files {sorted(signature)}"
    return UNKNOWN, "no run.json key set or file set matches a runner"


def _times(directory: Path) -> tuple[datetime | None, datetime | None]:
    files = [p for p in directory.rglob("*") if p.is_file()]
    if not files:
        return None, None
    stats = [p.stat() for p in files]
    births = [getattr(s, "st_birthtime", s.st_mtime) for s in stats]
    return (
        datetime.fromtimestamp(min(births)).astimezone(),
        datetime.fromtimestamp(max(s.st_mtime for s in stats)).astimezone(),
    )


def _prompt_entries(ids: Sequence[str]) -> list[dict[str, Any]]:
    """In the order given (the prompts a run called before the one it merely declared, so
    the tree files the run under what ran), without duplicates."""
    released = prompts.manifest()
    out: list[dict[str, Any]] = []
    for version_id in dict.fromkeys(ids):
        name, _, version = version_id.partition("/")
        sha = released.get(name, {}).get(version) if version else None
        out.append(
            {"name": name, "version": version or None, "sha256": sha or UNKNOWN, "calls": UNKNOWN}
        )
    return out


def _ids(summary: Mapping[str, Any]) -> list[str]:
    """The prompt ids a ``run.json`` names: the ones its calls used first (``prompt_versions``,
    ``prompts``), then the one it ran with (``prompt``), then the default it declared
    (``prompt_version``, the appraisal runner's fallback prompt)."""
    ids: list[str] = []
    for key in ("prompt_versions", "prompts"):
        value = summary.get(key)
        if isinstance(value, dict):
            ids += [v for v in value.values() if isinstance(v, str) and v]
        elif isinstance(value, list):
            ids += [v for v in value if isinstance(v, str) and v]
    for key in ("prompt", "prompt_version"):
        value = summary.get(key)
        if isinstance(value, str) and "/" in value:
            ids.append(value)
    return ids


def _source_prompt_version(summary: Mapping[str, Any]) -> str | None:
    """A merged run's version from its sources' prompts (``merged_from[*].prompt``):
    ``photo_countables-v2+photo_surfaces-v1``."""
    parts: list[str] = []
    for source in summary.get("merged_from") or []:
        prompt = source.get("prompt") if isinstance(source, dict) else None
        if isinstance(prompt, str) and "/" in prompt:
            parts.append(prompt.replace("/", "-"))
    return "+".join(parts) if parts else None


def _tree_for(
    harness: str, summary: Mapping[str, Any], components: Mapping[str, Any]
) -> tuple[str, str, str]:
    """``(component, version, how)`` a backfilled run files under."""
    manifest = {"components": components, "harness": {"module": harness}}
    if harness == "evals.appraisal.run" and not (summary.get("models") or summary.get("llm_calls")):
        system = summary.get("system")
        name = system.get("name") if isinstance(system, dict) else system
        return (
            "appraisal_fields",
            str(name or record.UNVERSIONED),
            "the appraisal runner made no model call: the system's name is the version",
        )
    if harness == "evals.merge_photo_runs":
        version = _source_prompt_version(summary)
        if version:
            return "photo_analysis", version, "merged_from[*].prompt of run.json"
    if harness == "evals.predict_assets" and not components.get("prompts"):
        released = prompts.versions("classify")
        if len(released) == 1:
            return "classify", released[0], "the one released classify prompt version"
    component, version = record.tree_location(manifest)
    return (
        component,
        version,
        "the prompt the run called (call counts unknown: the first named), else what the "
        "harness stands for",
    )


def _models(summary: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
    requested: list[str] = []
    for key in ("model_requested", "room_model", "overlap_model", "materials_model", "model"):
        value = summary.get(key)
        if isinstance(value, str) and value:
            requested.append(value)
    system = summary.get("system")
    if isinstance(system, dict):
        model = (
            (system.get("model") or {}).get("model")
            if isinstance(system.get("model"), dict)
            else None
        )
        if model:
            requested.append(str(model))
    answered = [
        str(m) for m in (summary.get("models_reported") or summary.get("models") or []) if m
    ]
    providers = [str(p) for p in summary.get("providers_reported") or [] if p]
    return sorted(set(requested)), sorted(set(answered)), sorted(set(providers))


def _efforts(summary: Mapping[str, Any]) -> list[str]:
    values: set[str] = set()
    effort = summary.get("reasoning_effort")
    if isinstance(effort, str):
        values.add(effort)
    settings = summary.get("settings")
    if isinstance(settings, dict) and isinstance(settings.get("reasoning_effort"), str):
        values.add(settings["reasoning_effort"])
    system = summary.get("system")
    if isinstance(system, dict) and isinstance(system.get("model"), dict):
        if isinstance(system["model"].get("effort"), str):
            values.add(system["model"]["effort"])
    return sorted(values)


def _labels_from_report(directory: Path) -> tuple[dict[str, Any], str]:
    report = directory / "eval.md"
    if not report.is_file():
        return {}, "no eval.md in the directory: the labels it was scored against are unknown"
    scored = registry.parse_eval_md(report.read_text(encoding="utf-8"))
    labels: dict[str, Any] = {}
    for dataset in scored:
        filename = ds.DATASET_FILES.get(dataset)
        labels[dataset] = {
            "file": filename,
            "version": record.label_version(filename) if filename else None,
            "sha256": UNKNOWN,
        }
    return labels, (
        "eval.md: the datasets scored by saved predictions, the label file each reads by "
        "name; the file's sha256 at scoring time was not recorded"
    )


def _appraisal_datasets(summary: Mapping[str, Any], root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for set_name in summary.get("sets") or []:
        path = root / "appraisal" / "truth" / f"{set_name}.json"
        out.append(
            {
                "name": set_name,
                "file": path.name,
                "version": None,
                "path": str(path),
                "sha256": UNKNOWN,
                "sha256_now": record.sha256_file(path) if path.is_file() else None,
            }
        )
    return out


def backfill_directory(
    directory: Path,
    *,
    root: Path,
    history: GitHistory,
    mentions: Mapping[str, Sequence[int]],
) -> dict[str, Any]:
    """The backfilled manifest of one run directory."""
    summary = _load(directory / record.SUMMARY_FILE)
    files = {p.name for p in directory.iterdir() if p.is_file()}
    harness, harness_source = guess_harness(summary, files)
    started, finished = _times(directory)
    sources: dict[str, str] = {
        "harness": harness_source,
        "started_at": "earliest file birth time in the directory (local clock)",
        "finished_at": "latest file modification time in the directory (local clock)",
    }
    kind = "appraisal" if "appraisal" in directory.relative_to(root).parts else "predictions"
    name = directory.name
    summary = summary or {}

    # --- code
    recorded_sha = summary.get("git_sha") if isinstance(summary.get("git_sha"), str) else None
    if recorded_sha and history.known(recorded_sha):
        git: dict[str, Any] = {
            "sha": recorded_sha,
            "short": recorded_sha[:7],
            "dirty": UNKNOWN,
            "branch": UNKNOWN,
            "release": history.release_of(recorded_sha),
            "commits_since_release": None,
            "describe": _git(history.repo, "describe", "--tags", "--long", recorded_sha),
        }
        sources["git"] = (
            "run.json git_sha (recorded by the appraisal runner); release = nearest tag "
            "reachable from it; dirty flag never recorded"
        )
    else:
        around = history.releases_around(finished)
        git = {
            "sha": recorded_sha or UNKNOWN,
            "short": recorded_sha[:7] if recorded_sha else UNKNOWN,
            "dirty": UNKNOWN,
            "branch": UNKNOWN,
            "release": UNKNOWN,
            "main_at_finish": history.main_at(finished),
            "release_before": around["before"],
            "release_after": around["after"],
            "describe": None,
        }
        sources["git"] = (
            "not recorded (runs were made from feature branches): main_at_finish is the main "
            "commit at the directory's last modification time, a lower bound on the code; "
            "release_before/after are the tags around that time"
            + (" (the recorded sha is not in this clone)" if recorded_sha else "")
        )

    # --- components
    ids = _ids(summary)
    requested, answered, providers = _models(summary)
    efforts = _efforts(summary)
    sources["prompts"] = (
        "run.json prompt ids; sha256 from basis/prompts/manifest.json (released versions are "
        "immutable)"
        if ids
        else "run.json names no prompt"
    )
    sources["models"] = "run.json" if requested or answered else "run.json names no model"
    if harness == "evals.predict_detector":
        detector = summary.get("detector")
        checkpoint = summary.get("checkpoint")
    else:
        detector = checkpoint = None
    components: dict[str, Any] = {
        "prompts": _prompt_entries(ids),
        "models": {"requested": requested, "answered": answered, "providers": providers},
        "request_params": {
            "reasoning_effort": efforts or [UNKNOWN],
            "max_output_tokens": [str(summary["max_tokens"])]
            if summary.get("max_tokens")
            else [UNKNOWN],
        },
        "pricing_version": UNKNOWN,
        "rules_version": UNKNOWN,
        "embedder": {"model": summary["embedding_model"], "revision": UNKNOWN}
        if summary.get("embedding_model")
        else None,
        "cost_table": None,
    }
    if detector:
        components["detector"] = f"{detector}:{checkpoint}" if checkpoint else detector
    if harness == "evals.predict_boxes" and summary.get("version"):
        components["localizer"] = f"sam3:{summary['version']}"
    sources["pricing_version"] = "not recorded; spent_usd was computed with the table of the day"

    # --- data
    labels: dict[str, Any] = {}
    datasets: list[dict[str, Any]] = []
    if kind == "appraisal":
        datasets = _appraisal_datasets(summary, root)
        sources["datasets"] = (
            "run.json sets -> appraisal/truth/<set>.json; the file's sha256 at run time was not "
            "recorded (sha256_now is today's)"
        )
    else:
        labels, sources["labels"] = _labels_from_report(directory)

    # --- cost and calls
    cost = summary.get("spent_usd", summary.get("cost_usd"))
    if summary.get("mode") == "replay":
        cost = summary.get("cost_usd", 0.0)
    calls = summary.get("calls", summary.get("model_calls", summary.get("llm_calls")))
    sources["calls"] = "run.json (spent_usd/cost_usd and calls/model_calls/llm_calls)"

    # --- status
    if not files and not any(directory.iterdir()):
        status = "empty"
    elif summary.get("complete") is False:
        status = "stopped"
    elif summary:
        status = "ok"
    else:
        status = UNKNOWN
    sources["status"] = (
        "run.json complete=false -> stopped; a run.json without it -> ok (assumed: the runner "
        "writes run.json last); no run.json -> unknown"
    )

    # --- files
    sibling_log = directory.parent / f"{name}.log"
    log = f"../{sibling_log.name}" if sibling_log.is_file() else None
    sources["log"] = (
        "sibling <name>.log kept by hand (referenced, not moved)" if log else "no log was kept"
    )

    sections = sorted(set(mentions.get(name, [])) | _prefix_sections(name))
    sources["report_sections"] = (
        "EVAL_REPORT sections citing the name; the section a name prefix encodes"
    )

    component, version, how = _tree_for(harness, summary, components)
    sources["tree"] = how + "; a backfilled run is filed under it as a link"
    started_iso = started.isoformat() if started else None
    finished_iso = finished.isoformat() if finished else None
    return {
        "format": record.FORMAT,
        "kind": "eval_run",
        "id": str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"basis-eval-backfill:{directory.relative_to(root)}"),
        ),
        "name": name,
        "backfilled": True,
        "backfilled_at": datetime.now(UTC).isoformat(),
        "sources": sources,
        "harness": {"module": harness, "sha256": UNKNOWN, "command": None, "options": None},
        "started_at": started_iso,
        "finished_at": finished_iso,
        "wall_seconds": (finished - started).total_seconds() if started and finished else None,
        "status": status,
        "exit_code": None,
        "error": None,
        "git": git,
        "environment": {
            "basis_version": UNKNOWN,
            "pricing_version": UNKNOWN,
            "rules_version": UNKNOWN,
        },
        "settings": summary.get("settings") if isinstance(summary.get("settings"), dict) else {},
        "components": components,
        "data": {
            "data_dir": str(root),
            "inputs": [],
            "datasets": datasets,
            "labels": labels,
            "split": summary.get("split"),
            "repeats": summary.get("repeats"),
        },
        "calls": {
            "count": calls if calls is not None else UNKNOWN,
            "cost_usd": cost if cost is not None else UNKNOWN,
        },
        "files": {
            "log": log,
            "summary": record.SUMMARY_FILE if (directory / record.SUMMARY_FILE).is_file() else None,
            "ledger": None,
            "report": "eval.md" if (directory / "eval.md").is_file() else None,
        },
        "report_sections": sections,
        "tree": {"component": component, "version": version, "link": None},
        "summary": summary or None,
    }


# --------------------------------------------------------------------------- the sweep


def _run_id(directory: Path, root: Path) -> str:
    """The name a run takes in the tree: its directory name under ``predictions/``; under
    ``appraisal/runs/`` the path below it with ``/`` as ``__`` (the sweeps nest cells of the
    same name: ``sweep1__llm_all@gpt-6-luna``)."""
    parts = directory.relative_to(root).parts
    if parts[:2] == ("appraisal", "runs"):
        return "__".join(parts[2:])
    return directory.name


def backfill(
    root: Path,
    *,
    repo: Path,
    report: Path | None,
    write: bool,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Backfill every run directory under ``root``; returns one line per directory:
    ``{"path", "action": written | would_write | kept, "manifest"}``."""
    history = GitHistory(repo)
    directories = registry.run_directories(root)
    mentions: dict[str, list[int]] = {}
    if report is not None and report.is_file():
        mentions = report_mentions(
            report.read_text(encoding="utf-8"), [d.name for d in directories]
        )
    lines: list[dict[str, Any]] = []
    for directory in directories:
        existing = _load(directory / record.MANIFEST_FILE)
        if existing is not None and not (force and existing.get("backfilled")):
            lines.append(
                {"path": str(directory.relative_to(root)), "action": "kept", "manifest": existing}
            )
            continue
        manifest = backfill_directory(directory, root=root, history=history, mentions=mentions)
        if write:
            tree = record.tree_root(root)
            if tree is not None:
                try:
                    manifest["tree"] = record.link_into_tree(
                        directory,
                        manifest["tree"]["component"],
                        manifest["tree"]["version"],
                        root=tree,
                        run_id=_run_id(directory, root),
                    )
                except OSError as exc:
                    manifest["tree"]["error"] = f"{type(exc).__name__}: {exc}"
            text = json.dumps(manifest, indent=1, sort_keys=True, default=str) + "\n"
            (directory / record.MANIFEST_FILE).write_text(text, encoding="utf-8")
        lines.append(
            {
                "path": str(directory.relative_to(root)),
                "action": "written" if write else "would_write",
                "manifest": manifest,
            }
        )
    return lines


# --------------------------------------------------------------------------- the note


def _count(lines: Sequence[Mapping[str, Any]], predicate: Any) -> int:
    return sum(1 for line in lines if predicate(line["manifest"]))


def render_note(lines: Sequence[Mapping[str, Any]], *, root: Path) -> str:
    """The Markdown note: what the backfill recovered and what it could not, in counts and
    run names (no scores, no client figures)."""
    backfilled = [line for line in lines if line["manifest"].get("backfilled")]
    kept = [line for line in lines if line["action"] == "kept"]
    predictions = [line for line in backfilled if line["path"].startswith("predictions/")]
    appraisal = [line for line in backfilled if line["path"].startswith("appraisal/")]

    def names(selected: Sequence[Mapping[str, Any]]) -> str:
        return ", ".join(f"`{line['manifest']['name']}`" for line in selected) or "none"

    unknown_harness = [
        line for line in backfilled if line["manifest"]["harness"]["module"] == UNKNOWN
    ]
    no_summary = [line for line in backfilled if line["manifest"]["files"]["summary"] is None]
    with_log = [line for line in backfilled if line["manifest"]["files"]["log"]]
    with_labels = [line for line in predictions if line["manifest"]["data"]["labels"]]
    with_sha = [line for line in backfilled if line["manifest"]["git"]["sha"] != UNKNOWN]
    with_release = [line for line in backfilled if line["manifest"]["git"]["release"] != UNKNOWN]
    with_prompts = [line for line in backfilled if line["manifest"]["components"]["prompts"]]
    with_models = [
        line
        for line in backfilled
        if line["manifest"]["components"]["models"]["requested"]
        or line["manifest"]["components"]["models"]["answered"]
    ]
    cited = [line for line in backfilled if line["manifest"]["report_sections"]]
    filed = [line for line in backfilled if (line["manifest"].get("tree") or {}).get("link")]
    components: dict[str, int] = {}
    for line in backfilled:
        key = (line["manifest"].get("tree") or {}).get("component", UNKNOWN)
        components[key] = components.get(key, 0) + 1
    stopped = [line for line in backfilled if line["manifest"]["status"] == "stopped"]
    empty = [line for line in backfilled if line["manifest"]["status"] == "empty"]
    harnesses: dict[str, int] = {}
    for line in backfilled:
        module = line["manifest"]["harness"]["module"]
        harnesses[module] = harnesses.get(module, 0) + 1

    out = [
        "# Backfilled run manifests",
        "",
        f"Written by `python -m evals.backfill_manifests` over `{root}` on "
        f"{datetime.now(UTC).strftime('%Y-%m-%d')}: one `manifest.json` with `backfilled: true` "
        "in every run directory that had none, each field with its source, `unknown` where "
        "the files do not say (EVAL_REPORT section 43). Nothing was moved or deleted.",
        "",
        "## Counts",
        "",
        "| | runs |",
        "|---|---|",
        f"| run directories found | {len(lines)} |",
        f"| already had a manifest (left alone) | {len(kept)} |",
        f"| backfilled | {len(backfilled)} ({len(predictions)} under `predictions/`, "
        f"{len(appraisal)} under `appraisal/runs/`) |",
        f"| with a `run.json` summary | {len(backfilled) - len(no_summary)} |",
        f"| harness identified | {len(backfilled) - len(unknown_harness)} |",
        f"| prompts named (sha256 exact from the prompt manifest) | {len(with_prompts)} |",
        f"| models named | {len(with_models)} |",
        f"| git sha recorded (appraisal runner only) | {len(with_sha)} |",
        f"| release known from the recorded sha | {len(with_release)} |",
        f"| label files known (from `eval.md`; sha256 at the time unknown) | {len(with_labels)} |",
        f"| terminal log kept (sibling `.log`) | {len(with_log)} |",
        f"| cited by name or prefix in EVAL_REPORT | {len(cited)} |",
        f"| filed in `verification/<component>/<version>/` as a link | {len(filed)} |",
        f"| stopped by an account error | {len(stopped)} |",
        f"| empty directories | {len(empty)} |",
        "",
        "## Component per run (where the tree files it)",
        "",
        "| component | runs |",
        "|---|---|",
        *(f"| `{name}` | {count} |" for name, count in sorted(components.items())),
        "",
        "## Harness per run (from `run.json` keys or the files present)",
        "",
        "| harness | runs |",
        "|---|---|",
        *(f"| `{module}` | {count} |" for module, count in sorted(harnesses.items())),
        "",
        "## Not recoverable",
        "",
        "* **The exact commit of every non-appraisal run.** The runners did not record a git "
        "sha and the runs were made from feature branches; the manifest records "
        "`git.main_at_finish` (the `main` commit at the directory's last write, a lower bound) "
        "and `release_before` / `release_after` (the tags around that time). `dirty` is unknown "
        "for every backfilled run, the appraisal runs included.",
        "* **The label files' content at scoring time.** Only the file names are known (from "
        "`eval.md`), so the version is, but `room_inventory_v1.json` and `LABEL_GUIDE.md` were "
        "edited on 2026-10-16 (`.bak-2026-10-16` copies exist); a score from before that edit "
        "cannot be tied to either content. Runs without an `eval.md` have no label record.",
        "* **Per-call records and payloads.** The `MemoryRecorder` was discarded at exit; only "
        "the runners' own raw files (`photo_raw.jsonl`, `room_raw.jsonl`, `fields_raw.jsonl`, "
        "`calls.jsonl`) hold answers, and no request, token split, latency or response id.",
        "* **The price table and rules table versions**, the settings in effect, the reasoning "
        "effort where `run.json` has none, and the exit status (a `run.json` present is read as "
        "a completed run because every runner writes it last).",
        f"* **Terminal output** for {len(backfilled) - len(with_log)} runs: no log was kept.",
        "",
        "## Runs whose harness could not be identified",
        "",
        names(unknown_harness),
        "",
        "## Runs without a `run.json`",
        "",
        names(no_summary),
        "",
    ]
    return "\n".join(out)


# --------------------------------------------------------------------------- cli


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evals.backfill_manifests", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--root", type=Path, default=None, help=f"default: ${ds.ENV_VAR}")
    parser.add_argument("--repo", type=Path, default=record.REPO_DIR, help="the git checkout")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="EVAL_REPORT.md")
    parser.add_argument(
        "--write", action="store_true", help="write the manifests (default: dry run)"
    )
    parser.add_argument("--force", action="store_true", help="rewrite backfilled manifests")
    parser.add_argument("--note", type=Path, default=None, help="write the Markdown note here")
    parser.add_argument("--json", type=Path, default=None, help="write every manifest as JSON")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    env = os.environ.get(ds.ENV_VAR)
    root = args.root or (Path(env) if env else None)
    if root is None or not root.is_dir():
        print(f"no data directory ({ds.ENV_VAR} or --root)", file=sys.stderr)
        return 2
    lines = backfill(root, repo=args.repo, report=args.report, write=args.write, force=args.force)
    for line in lines:
        manifest = line["manifest"]
        print(
            f"{line['action']:<11} {line['path']:<60} {manifest['harness']['module']:<32} "
            f"{manifest.get('git', {}).get('release', '?')}"
        )
    counts: dict[str, int] = {}
    for line in lines:
        counts[line["action"]] = counts.get(line["action"], 0) + 1
    print(", ".join(f"{k}: {v}" for k, v in sorted(counts.items())), file=sys.stderr)
    if args.note:
        args.note.write_text(render_note(lines, root=root), encoding="utf-8")
        print(f"note written to {args.note}", file=sys.stderr)
    if args.json:
        args.json.write_text(json.dumps(lines, indent=1, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
