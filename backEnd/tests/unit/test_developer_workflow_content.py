"""``web/developer/workflow-content.json`` stays true to the repository it describes.

The developer page's step dialogs (``web/developer/architecture.js``) render this file: the
explanation of each step, the systems its runtime table lists, the measured results, and the
source links. Nothing in it is computed at run time, so this test is what keeps it honest:

* every ``section`` a result cites is a heading of ``EVAL_REPORT.md``, and every figure
  (each comparison cell, metric value, spend field and regression status) appears verbatim
  in the text of that section (from its heading to the next heading of the same or a higher
  level), so a number on the page is a number in the report;
* every link and every explanation's ``source`` is a path that exists in the repository (a
  ``#fragment`` on a Markdown path is a heading of that file, in GitHub's anchor form);
* every runtime row names a service id the health contract lists and a model role the
  worker reports;
* every ``components`` entry (the registry components whose run records the dialog lists
  from ``GET /v1/ops/eval-runs``) is a released prompt name or a component a harness stands
  for in ``evals.record.HARNESS_COMPONENTS``, with none for an engineer's review;
* the shape the renderer relies on holds (three explanation steps, one value per column);
* every comparison row says, for a person, what was compared: a plain name and its setup,
  each model of which (the exact id) appears in the cited section, so the page never names a
  model the report does not; every comparison is filed under the part of "How it works" it
  measured, and a part with no comparison says why (``gaps``) instead of borrowing a table.

Bold and code marks are formatting, not content: a figure is compared with them removed on
both sides. "Not measured", "—" (an empty cell) and "None (no AI model)" (no token figure
applies) are the placeholders.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from basis.llm import prompts
from basis.ops.services import SERVICE_IDS
from evals import record

REPO = Path(__file__).resolve().parents[3]
CONTENT = REPO / "web" / "developer" / "workflow-content.json"
REPORT = REPO / "docs" / "review_2026-10-15" / "EVAL_REPORT.md"
STEP_IDS = (
    "upload",
    "extract",
    "photos",
    "appraisal-review",
    "photo-results",
    "rooms-review",
    "inventory",
    "components-review",
    "assets",
    "assets-review",
    "output",
    "report-sign",
)
MODEL_SETTINGS = (
    "VISION_MODEL",
    "VISION_SURFACES_MODEL",
    "VISION_FUSION_MODEL",
    "ROOM_MODEL",
    "ROOM_MATERIALS_MODEL",
    "ROOM_CALL_MODEL",
    "SURFACE_CROP_MODEL",
    "EXTRACTION_GROUNDED_MODEL",
    "TEXT_MODEL",
    "CLASSIFY_MODEL",
)
NOT_MEASURED = "Not measured"
PLACEHOLDERS = (NOT_MEASURED, "—", "None (no AI model)")
HEADING = re.compile(r"^(#{2,3}) (\d+(?:\.\d+)?)\b")


@cache
def content() -> dict[str, Any]:
    return json.loads(CONTENT.read_text())


def plain(text: str) -> str:
    return text.replace("**", "").replace("`", "")


@cache
def sections() -> dict[str, str]:
    """Section number -> its text, from the heading to the next heading of the same or a
    higher level (``15.1`` ends at the next ``###`` or ``##``; ``15`` at the next ``##``)."""
    lines = REPORT.read_text().splitlines()
    found: dict[str, str] = {}
    for start, line in enumerate(lines):
        match = HEADING.match(line)
        if match is None:
            continue
        level, number = len(match.group(1)), match.group(2)
        end = len(lines)
        for later in range(start + 1, len(lines)):
            nxt = HEADING.match(lines[later])
            if nxt is not None and len(nxt.group(1)) <= level:
                end = later
                break
        found.setdefault(number, "\n".join(lines[start:end]))
    return found


def github_anchor(heading: str) -> str:
    text = heading.lstrip("#").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


@cache
def anchors(path: Path) -> set[str]:
    return {github_anchor(line) for line in path.read_text().splitlines() if line.startswith("#")}


def figures(results: dict[str, Any]) -> list[tuple[str, str]]:
    """``(section, text)`` for every figure the results quote."""
    out: list[tuple[str, str]] = []
    for comparison in results.get("comparisons", []):
        for row in comparison["rows"]:
            out += [(comparison["section"], value) for value in row["values"]]
    for metric in results.get("metrics", []):
        out.append((metric["section"], metric["value"]))
    spend = results.get("spend", {})
    for key in ("cost", "tokens", "latency"):
        section = spend.get(f"{key}_section")
        values = (
            [spend.get("cost_per_study")]
            if key == "cost"
            else [spend.get("input_tokens"), spend.get("output_tokens")]
            if key == "tokens"
            else [spend.get("latency")]
        )
        for value in values:
            if value is not None:
                assert section, f"{value!r} in spend has no {key}_section"
                out.append((section, value))
    regression = results.get("regression")
    if regression:
        out.append((regression["section"], regression["status"]))
    return out


def test_every_step_is_present_once() -> None:
    assert set(content()["steps"]) == set(STEP_IDS)
    assert content()["schema_version"] == 1
    cited = {c["section"] for s in content()["steps"].values() for c in s["results"]["comparisons"]}
    cited |= {m["section"] for s in content()["steps"].values() for m in s["results"]["metrics"]}
    for number, entry in content()["sections"].items():
        assert number in sections(), number
        assert entry["anchor"] in anchors(REPORT), entry
    assert cited <= set(content()["sections"]), cited - set(content()["sections"])


@pytest.mark.parametrize("step_id", STEP_IDS)
def test_explanations_have_three_steps_with_a_source(step_id: str) -> None:
    step = content()["steps"][step_id]
    assert len(step["how"]) == 3
    for item in step["how"]:
        assert item["title"] and item["detail"]
        assert (REPO / item["source"]).is_file(), item["source"]


@pytest.mark.parametrize("step_id", STEP_IDS)
def test_runtime_rows_name_known_services_and_roles(step_id: str) -> None:
    step = content()["steps"][step_id]
    assert step["runtime"], "a step lists what it runs"
    for row in step["runtime"]:
        assert row["service"] in SERVICE_IDS, row
        for role in row.get("roles", []):
            assert role in MODEL_SETTINGS and role in content()["roles"], role


REVIEW_STEPS = ("appraisal-review", "rooms-review", "components-review", "assets-review")


@pytest.mark.parametrize("step_id", STEP_IDS)
def test_components_are_registry_components(step_id: str) -> None:
    step = content()["steps"][step_id]
    components = step["components"]
    assert isinstance(components, list) and len(components) == len(set(components))
    known = set(prompts.manifest()) | set(record.HARNESS_COMPONENTS.values())
    for component in components:
        assert component in known, component
    if step_id in REVIEW_STEPS:
        assert components == [], "a review step has no model call to evaluate"


@pytest.mark.parametrize("step_id", STEP_IDS)
def test_every_figure_is_verbatim_in_its_report_section(step_id: str) -> None:
    results = content()["steps"][step_id]["results"]
    assert results["summary"]
    for comparison in results.get("comparisons", []):
        assert comparison["section"] in sections(), comparison["section"]
        width = len(comparison["columns"]) - 1
        for row in comparison["rows"]:
            assert len(row["values"]) == width, (comparison["title"], row["name"])
        selected = [row for row in comparison["rows"] if row.get("selected")]
        assert len(selected) <= 1 and (not selected or comparison["rows"][0] is selected[0])
    missing = [
        (section, text)
        for section, text in figures(results)
        if text not in PLACEHOLDERS and plain(text) not in plain(sections().get(section, ""))
    ]
    assert not missing, f"{step_id}: not found verbatim in its section: {missing}"


@pytest.mark.parametrize("step_id", STEP_IDS)
def test_every_link_resolves(step_id: str) -> None:
    step = content()["steps"][step_id]
    assert step["links"], "a step keeps its source links"
    for link in step["links"]:
        path, _, fragment = link["path"].partition("#")
        target = REPO / path
        assert target.exists(), link
        if fragment:
            assert target.suffix == ".md" and fragment in anchors(target), link


def test_no_client_data_or_provider_secret_in_the_content() -> None:
    text = CONTENT.read_text()
    for forbidden in ("sk-", "r8_", "basis-eval-data", "AKIA", "@gmail", ".pdf", ".jpg"):
        assert forbidden not in text, forbidden


@pytest.mark.parametrize("step_id", STEP_IDS)
def test_every_comparison_names_its_setups_and_their_models(step_id: str) -> None:
    for comparison in content()["steps"][step_id]["results"].get("comparisons", []):
        text = plain(sections()[comparison["section"]])
        assert comparison.get("question"), comparison["title"]
        assert len(comparison.get("column_notes", [])) == len(comparison["columns"]) - 1
        for row in comparison["rows"]:
            assert row.get("name") and isinstance(row.get("setup"), list), row
            assert row["setup"] or row.get("setup_note"), row["name"]
            for call in row["setup"]:
                assert call["model"] in text, (comparison["section"], call["model"])
                assert call.get("provider"), call


@pytest.mark.parametrize("step_id", STEP_IDS)
def test_every_part_has_its_comparisons_or_says_why_not(step_id: str) -> None:
    step = content()["steps"][step_id]
    results = step["results"]
    parts = {c.get("step") for c in results.get("comparisons", [])}
    for comparison in results.get("comparisons", []):
        assert comparison.get("step") in range(1, len(step["how"]) + 1), comparison["title"]
    gaps = results.get("gaps", {})
    for number in range(1, len(step["how"]) + 1):
        assert (number in parts) != (str(number) in gaps), (step_id, number)
