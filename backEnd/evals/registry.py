"""``python -m evals.registry``: which runs exercised which component, on which model, against
which labels, and what they scored, from the run directories alone.

The question this answers is the one that decides whether a run has to be repeated: *has
``photo_countables/v3`` on ``gpt-6.1-sol`` been tested against ``photo_components_v1``, and
what did it score, cost and when?* Every run directory under ``<data dir>/predictions/`` and
``<data dir>/appraisal/runs/`` is read (EVAL_REPORT section 42; formats in
:mod:`evals.record`):

* ``manifest.json``: the run manifest (written by the runner, or backfilled by
  :mod:`evals.backfill_manifests` with ``backfilled: true`` and ``"unknown"`` where the
  files did not say);
* ``run.json``: the runner's summary (models, prompt, spend);
* ``scores.jsonl``: every scoring of the directory by ``evals.run --predictions`` (the label
  file, version and sha256 per dataset, the metrics); the latest scoring per dataset wins;
* ``eval.md``: an older scoring's report (metrics parsed from its tables, labels by name);
* ``score.json`` (appraisal runs): the appraisal scorer's output;
* ``results.json``: the run's cases (passed / failed / test_error) and, from its manifest's
  ``tree`` section, the component and version it is filed under in
  ``<data dir>/verification/<component>/<version>/<run>`` (runs written inside the tree
  itself are scanned too; the links there point back at ``predictions/``).

Usage (from ``backEnd/``)::

    python -m evals.registry                                  # every run, newest first
    python -m evals.registry --component photo_countables/v3 --model gpt-6.1-sol \\
        --labels photo_components_v1                          # the question above
    python -m evals.registry --component room_inventory --matrix   # prompt version x model
    python -m evals.registry --json runs.json --markdown runs.md

Filters: ``--component NAME[/vN]`` (a prompt name or version id, a detector, a localizer:
anything the manifest lists under components), ``--model M`` (requested or answered;
``M*`` for a prefix), ``--labels FILE_STEM`` / ``--dataset NAME``, ``--release vX.Y.Z``,
``--harness evals.predict_photos``, ``--status ok|stopped|error|unknown``, ``--since DATE``.
``--component`` also matches the tree component (``room_inventory``, ``room_inventory/v3``).
``--matrix`` prints, for the matching runs, prompt version ids against models with the
number of runs in each cell (and the label versions seen), the "fully tested?" view. The
``results`` column is ``passed / failed / test_error`` from ``results.json``.
Nothing here calls a model or reads client data beyond the run directories' own files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from evals import datasets as ds
from evals import record

__all__ = [
    "HEADLINE_METRICS",
    "RunEntry",
    "filter_runs",
    "main",
    "matrix",
    "parse_eval_md",
    "read_run",
    "render_json",
    "render_markdown",
    "render_matrix",
    "run_directories",
    "scan",
    "tree_directories",
]

# The one metric per dataset the table shows (every metric is in the JSON output).
HEADLINE_METRICS: Final[dict[str, str]] = {
    "photo_rooms": "accuracy",
    "photo_components": "f1",
    "photo_counts": "count_mae",
    "room_inventory": "count_mae",
    "appraisal_fields": "normalized_accuracy",
    "assets": "bucket_accuracy",
    "retrieval_qrels": "ndcg_10",
    "detection_boxes": "map",
    "report_rows": "row_coverage",
    "rule_citations": "resolved_fraction",
}
_EVAL_MD_SECTION: Final = re.compile(r"^## (\S+) \(system: `([^`]*)`\)", re.MULTILINE)
_EVAL_MD_ROW: Final = re.compile(r"^\| ([^|]+?) \| ([-0-9.naN]+|nan) \| (\d+) \|", re.MULTILINE)
UNKNOWN: Final = record.UNKNOWN


@dataclass
class RunEntry:
    """One run directory as the registry sees it."""

    name: str
    path: str
    kind: str  # predictions | appraisal
    harness: str | None
    status: str | None
    started_at: str | None
    finished_at: str | None
    git_sha: str | None
    git_dirty: bool | str | None
    release: str | None
    prompts: list[dict[str, Any]]  # {name, version, sha256}
    components: list[str]  # every component id: prompt version ids, detector:x, localizer:x
    models_requested: list[str]
    models_answered: list[str]
    reasoning_effort: list[str]
    labels: dict[str, dict[str, Any]]  # dataset -> {file, version, sha256, scored_at}
    scores: dict[str, dict[str, float | None]]  # dataset -> metric -> value (Scores)
    cost_usd: float | None
    calls: int | None
    manifest: bool
    backfilled: bool
    component: str | None = None  # where the run files in the verification tree
    version: str | None = None
    results: dict[str, int] = field(default_factory=dict)  # passed / failed / test_error
    report_sections: list[int] = field(default_factory=list)
    files: dict[str, str | None] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def label_stems(self) -> set[str]:
        return {
            Path(str(v.get("file"))).stem
            for v in self.labels.values()
            if v.get("file") not in (None, UNKNOWN)
        }


# --------------------------------------------------------------------------- reading


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


Scores = dict[str, dict[str, float | None]]
# The scorer's system when it scored the directory's own files (``evals.run``); a dataset the
# directory has no file for is scored by a baseline, which says nothing about the run.
SAVED_PREDICTIONS: Final = "predictions"


def parse_eval_md(text: str, *, system: str | None = SAVED_PREDICTIONS) -> Scores:
    """``{dataset: {metric: value}}`` from an ``evals.run`` Markdown report, the datasets
    scored by ``system`` only (``None``: every dataset, baselines included)."""
    scores: Scores = {}
    sections = list(_EVAL_MD_SECTION.finditer(text))
    for i, match in enumerate(sections):
        if system is not None and match.group(2) != system:
            continue
        end = sections[i + 1].start() if i + 1 < len(sections) else len(text)
        body = text[match.end() : end]
        metrics: dict[str, float | None] = {}
        for row in _EVAL_MD_ROW.finditer(body):
            name, value = row.group(1).strip(), row.group(2)
            if name == "metric":
                continue
            try:
                metrics[name] = float(value)
            except ValueError:
                metrics[name] = None
        if metrics or "Skipped" not in body:
            scores[match.group(1)] = metrics
    return scores


def _latest_scores(directory: Path) -> tuple[dict[str, dict[str, Any]], Scores]:
    """Labels and metrics per dataset from ``scores.jsonl`` (the latest scoring of the
    directory's own predictions per dataset)."""
    labels: dict[str, dict[str, Any]] = {}
    scores: Scores = {}
    for entry in record.read_scores(directory):
        when = entry.get("scored_at")
        for dataset, result in (entry.get("results") or {}).items():
            if result.get("skipped") or result.get("system") != SAVED_PREDICTIONS:
                continue
            label = dict((entry.get("data") or {}).get("labels", {}).get(dataset) or {})
            label["scored_at"] = when
            labels[dataset] = label
            scores[dataset] = {
                metric: (value or {}).get("value")
                for metric, value in (result.get("metrics") or {}).items()
            }
    return labels, scores


def _appraisal_scores(directory: Path) -> Scores:
    score = _load_json(directory / "score.json")
    if not score:
        return {}
    metrics: dict[str, float | None] = {}
    for key, value in score.items():
        if isinstance(value, int | float) and not isinstance(value, bool):
            metrics[key] = float(value)
        elif isinstance(value, dict):
            for sub, inner in value.items():
                if isinstance(inner, int | float) and not isinstance(inner, bool):
                    metrics[f"{key}.{sub}"] = float(inner)
    return {"appraisal": metrics} if metrics else {}


def _known(value: Any) -> Any:
    """A backfilled manifest writes ``"unknown"`` where the files did not say; the registry
    reads that as not known."""
    return None if value == UNKNOWN else value


def _as_list(value: Any) -> list[str]:
    if value is None or value == UNKNOWN:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(v) for v in value.values() if v]
    return [str(v) for v in value if v]


def _prompts_from_summary(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for version_id in _as_list(summary.get("prompt")) + _as_list(summary.get("prompts")):
        name, _, version = version_id.partition("/")
        found.append({"name": name, "version": version or None, "sha256": None})
    return found


def _prompt_id(prompt: Mapping[str, Any]) -> str:
    return f"{prompt['name']}/{prompt['version']}" if prompt.get("version") else str(prompt["name"])


def _components_of(
    manifest: Mapping[str, Any] | None, prompts: Sequence[Mapping[str, Any]]
) -> list[str]:
    ids = {_prompt_id(p) for p in prompts}
    ids |= {str(p["name"]) for p in prompts}
    if manifest:
        components = manifest.get("components") or {}
        for key in ("detector", "localizer", "segmenter", "embedder_id"):
            value = components.get(key) or (manifest.get("notes") or {}).get(key)
            if value and value != UNKNOWN:
                ids.add(f"{key}:{value}")
        embedder = components.get("embedder")
        if isinstance(embedder, dict) and embedder.get("model"):
            ids.add(f"embedder:{embedder['model']}")
    return sorted(ids)


def read_run(directory: Path, root: Path) -> RunEntry:
    """Everything the registry knows about one run directory."""
    manifest = _load_json(directory / record.MANIFEST_FILE)
    summary = _load_json(directory / record.SUMMARY_FILE) or {}
    if manifest and isinstance(manifest.get("summary"), dict) and not summary:
        summary = manifest["summary"]
    kind = "appraisal" if "appraisal" in directory.relative_to(root).parts else "predictions"
    labels, scores = _latest_scores(directory)
    if not scores and (directory / "eval.md").is_file():
        scores = parse_eval_md((directory / "eval.md").read_text(encoding="utf-8"))
        for dataset in scores:
            filename = ds.DATASET_FILES.get(dataset)
            labels.setdefault(
                dataset,
                {
                    "file": filename,
                    "version": record.label_version(filename) if filename else None,
                    "sha256": None,
                    "scored_at": None,
                },
            )
    if not scores and kind == "appraisal":
        scores = _appraisal_scores(directory)
    components = (manifest or {}).get("components") or {}
    prompts = list(components.get("prompts") or []) or _prompts_from_summary(summary)
    models = components.get("models") or {}
    git = (manifest or {}).get("git") or {}
    data = (manifest or {}).get("data") or {}
    for dataset, label in (data.get("labels") or {}).items():
        labels.setdefault(dataset, dict(label))
    for dataset_entry in data.get("datasets") or []:
        labels.setdefault(dataset_entry.get("name", "?"), dict(dataset_entry))
    calls = (manifest or {}).get("calls") or {}
    cost = calls.get("cost_usd")
    if cost is None:
        cost = summary.get("spent_usd", summary.get("cost_usd"))
    efforts = (components.get("request_params") or {}).get("reasoning_effort")
    if not efforts:
        efforts = _as_list(summary.get("reasoning_effort"))
    tree = (manifest or {}).get("tree") or {}
    outcomes = (
        record.read_results(directory)["summary"]
        if (directory / record.RESULTS_FILE).is_file()
        else {}
    )
    return RunEntry(
        name=directory.name,
        path=str(directory.relative_to(root)),
        component=_known(tree.get("component")),
        version=_known(tree.get("version")),
        results={k: int(outcomes[k]) for k in record.OUTCOMES if k in outcomes},
        kind=kind,
        harness=((manifest or {}).get("harness") or {}).get("module"),
        status=(manifest or {}).get("status")
        or ("stopped" if summary.get("complete") is False else None),
        started_at=(manifest or {}).get("started_at"),
        finished_at=(manifest or {}).get("finished_at"),
        git_sha=_known(git.get("sha")),
        git_dirty=_known(git.get("dirty")),
        release=_known(git.get("release")),
        prompts=prompts,
        components=_components_of(manifest, prompts),
        models_requested=_as_list(models.get("requested"))
        or _as_list(summary.get("model_requested"))
        or _as_list(summary.get("room_model"))
        or _as_list(summary.get("model")),
        models_answered=_as_list(models.get("answered"))
        or _as_list(summary.get("models_reported")),
        reasoning_effort=[e for e in efforts if e not in ("null", None)],
        labels=labels,
        scores=scores,
        cost_usd=None if cost in (None, UNKNOWN) else float(cost),
        calls=calls.get("count")
        if calls.get("count") not in (None, UNKNOWN)
        else summary.get("calls"),
        manifest=manifest is not None,
        backfilled=bool((manifest or {}).get("backfilled")),
        report_sections=list((manifest or {}).get("report_sections") or []),
        files=dict((manifest or {}).get("files") or {}),
        summary=summary,
    )


def run_directories(root: Path) -> list[Path]:
    """The run directories: every top-level directory under ``predictions/`` (a runner's
    sub-directories, such as ``loop_masked/``, are part of their run) and every directory
    under ``appraisal/runs/`` that holds a ``run.json``, ``score.json`` or
    ``predictions.json`` (the appraisal sweeps nest their cells)."""
    found: list[Path] = []
    predictions = root / "predictions"
    if predictions.is_dir():
        found += sorted(p for p in predictions.iterdir() if p.is_dir())
    appraisal = root / "appraisal" / "runs"
    if appraisal.is_dir():
        for path in sorted(appraisal.rglob("*")):
            if path.is_dir() and any(
                (path / f).is_file() for f in ("run.json", "score.json", "predictions.json")
            ):
                found.append(path)
    return found


def tree_directories(root: Path) -> list[Path]:
    """Run directories written directly inside ``verification/<component>/<version>/``
    (not the links, which point at ``predictions/`` or ``appraisal/runs/`` and are already
    scanned there)."""
    tree = record.tree_root(root)
    if tree is None or not tree.is_dir():
        return []
    found: list[Path] = []
    for component in sorted(p for p in tree.iterdir() if p.is_dir()):
        for version in sorted(p for p in component.iterdir() if p.is_dir()):
            for run in sorted(p for p in version.iterdir() if p.is_dir() and not p.is_symlink()):
                found.append(run)
    return found


def scan(root: Path) -> list[RunEntry]:
    """Every run directory under ``root`` (:func:`run_directories` and the runs written
    inside the verification tree), newest first (by start time, then name)."""
    directories = run_directories(root) + tree_directories(root)
    entries = [read_run(directory, root) for directory in directories]
    entries.sort(key=lambda e: (e.started_at or "", e.name), reverse=True)
    return entries


# --------------------------------------------------------------------------- filtering


def _matches_model(entry: RunEntry, wanted: str) -> bool:
    models = set(entry.models_requested) | set(entry.models_answered)
    if wanted.endswith("*"):
        return any(m.startswith(wanted[:-1]) for m in models)
    return wanted in models


def filter_runs(
    entries: Iterable[RunEntry],
    *,
    component: str | None = None,
    model: str | None = None,
    labels: str | None = None,
    dataset: str | None = None,
    release: str | None = None,
    harness: str | None = None,
    status: str | None = None,
    since: str | None = None,
) -> list[RunEntry]:
    out: list[RunEntry] = []
    for entry in entries:
        if (
            component
            and component not in entry.components
            and component
            not in (
                entry.component,
                f"{entry.component}/{entry.version}",
            )
        ):
            continue
        if model and not _matches_model(entry, model):
            continue
        if labels and labels not in entry.label_stems:
            continue
        if dataset and dataset not in entry.scores and dataset not in entry.labels:
            continue
        if release and entry.release != release:
            continue
        if harness and entry.harness != harness:
            continue
        if status and (entry.status or UNKNOWN) != status:
            continue
        if since and (entry.started_at or "") < since:
            continue
        out.append(entry)
    return out


# --------------------------------------------------------------------------- rendering


def _fmt(value: Any) -> str:
    if value is None:
        return "?"
    if isinstance(value, float):
        return f"{value:.3f}" if abs(value) < 100 else f"{value:.1f}"
    return str(value)


def _headline(entry: RunEntry) -> str:
    parts = []
    for dataset, metrics in sorted(entry.scores.items()):
        metric = HEADLINE_METRICS.get(dataset)
        if metric and metric in metrics:
            parts.append(f"{dataset} {metric}={_fmt(metrics[metric])}")
        elif metrics:
            first = next(iter(metrics))
            parts.append(f"{dataset} {first}={_fmt(metrics[first])}")
    return "; ".join(parts) if parts else "not scored"


def _results(entry: RunEntry) -> str:
    if not entry.results:
        return "-"
    r = entry.results
    return f"{r.get('passed', 0)} / {r.get('failed', 0)} / {r.get('test_error', 0)}"


def _labels(entry: RunEntry) -> str:
    stems = sorted(entry.label_stems)
    return ", ".join(stems) if stems else "?"


def render_markdown(entries: Sequence[RunEntry]) -> str:
    lines = [
        "| run | component | version | started | release | git | harness | prompts | model | "
        "effort | labels | results | scores | cost | calls | record |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in entries:
        prompts = ", ".join(_prompt_id(p) for p in e.prompts)
        git = (e.git_sha or "?")[:7] + ("*" if e.git_dirty is True else "")
        sections = ",".join(map(str, e.report_sections))
        source = ("backfilled" if e.backfilled else "manifest") if e.manifest else "run.json"
        lines.append(
            "| "
            + " | ".join(
                cell.replace("|", "/")
                for cell in (
                    f"`{e.name}`",
                    e.component or "?",
                    e.version or "?",
                    (e.started_at or "?")[:16],
                    e.release or "?",
                    git,
                    (e.harness or "?").removeprefix("evals."),
                    prompts or "-",
                    ", ".join(e.models_requested) or "-",
                    ", ".join(e.reasoning_effort) or "-",
                    _labels(e),
                    _results(e),
                    _headline(e),
                    "?" if e.cost_usd is None else f"${e.cost_usd:.2f}",
                    _fmt(e.calls),
                    source + (f"; §{sections}" if sections else ""),
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def render_json(entries: Sequence[RunEntry]) -> str:
    return json.dumps([asdict(e) for e in entries], indent=1, sort_keys=True, default=str) + "\n"


def matrix(entries: Sequence[RunEntry]) -> dict[str, dict[str, dict[str, Any]]]:
    """``{prompt version id: {model: {"runs": n, "labels": [...], "releases": [...]}}}``."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for e in entries:
        ids = [_prompt_id(p) for p in e.prompts if p.get("version")] or ["(no prompt)"]
        models = e.models_requested or e.models_answered or ["(no model)"]
        for version_id in ids:
            for model in models:
                cell = out.setdefault(version_id, {}).setdefault(
                    model, {"runs": 0, "labels": set(), "releases": set()}
                )
                cell["runs"] += 1
                cell["labels"] |= e.label_stems
                if e.release:
                    cell["releases"].add(e.release)
    return {
        v: {
            m: {**c, "labels": sorted(c["labels"]), "releases": sorted(c["releases"])}
            for m, c in ms.items()
        }
        for v, ms in out.items()
    }


def render_matrix(cells: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> str:
    models = sorted({m for ms in cells.values() for m in ms})
    lines = ["| component | " + " | ".join(models) + " |", "|---|" + "---|" * len(models)]
    for version_id in sorted(cells):
        row = [f"`{version_id}`"]
        for model in models:
            cell = cells[version_id].get(model)
            if cell is None:
                row.append("-")
            else:
                labels = ",".join(cell["labels"]) or "unscored"
                row.append(f"{cell['runs']} ({labels})")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- cli


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evals.registry", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--root", type=Path, default=None, help=f"default: ${ds.ENV_VAR}")
    parser.add_argument("--component", default=None, help="prompt name or name/vN (or detector:x)")
    parser.add_argument(
        "--model", default=None, help="requested or answered model; M* for a prefix"
    )
    parser.add_argument("--labels", default=None, help="label file stem, e.g. photo_components_v1")
    parser.add_argument("--dataset", default=None, help="a scored dataset, e.g. photo_counts")
    parser.add_argument("--release", default=None, help="nearest release tag, e.g. v0.9.0")
    parser.add_argument("--harness", default=None, help="e.g. evals.predict_photos")
    parser.add_argument("--status", default=None, choices=("ok", "stopped", "error", "unknown"))
    parser.add_argument("--since", default=None, help="ISO date: runs started on or after")
    parser.add_argument("--matrix", action="store_true", help="prompt version x model table")
    parser.add_argument("--json", type=Path, default=None, help="write the entries as JSON")
    parser.add_argument("--markdown", type=Path, default=None, help="write the table to a file")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    env = os.environ.get(ds.ENV_VAR)
    root = args.root or (Path(env) if env else None)
    if root is None or not root.is_dir():
        print(f"no data directory ({ds.ENV_VAR} or --root)", file=sys.stderr)
        return 2
    entries = filter_runs(
        scan(root),
        component=args.component,
        model=args.model,
        labels=args.labels,
        dataset=args.dataset,
        release=args.release,
        harness=args.harness,
        status=args.status,
        since=args.since,
    )
    text = render_matrix(matrix(entries)) if args.matrix else render_markdown(entries)
    if args.markdown:
        args.markdown.write_text(text, encoding="utf-8")
    if args.json:
        args.json.write_text(render_json(entries), encoding="utf-8")
    if not args.markdown or not args.json:
        sys.stdout.write(text)
    print(f"{len(entries)} runs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
