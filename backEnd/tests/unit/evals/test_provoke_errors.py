"""``evals.provoke_errors``: provoked provider failures are cases that name the expected
BASIS code, the observed one and whether they matched; a mismatch is ``failed``, an
unconfigured provider or a harness exception is ``test_error``, never a pass. Scripted
providers only (``--dry-run``): no network, no key."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basis.errors.codes import E
from evals import provoke_errors, record


async def test_the_dry_run_records_every_outcome_and_files_the_run_by_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    monkeypatch.setenv("BASIS_EVAL_DATA_DIR", str(data))
    out = data / "predictions" / "errors_dry"
    code = await provoke_errors.run(provoke_errors.parse_args(["--out", str(out), "--dry-run"]))
    assert code == 1  # the deliberate mismatch is a failed case

    results = json.loads((out / record.RESULTS_FILE).read_text())
    scenarios = {s.id: s for s in provoke_errors.dry_scenarios()}
    cases = {c["id"]: c for c in results["cases"]}
    assert set(cases) == set(scenarios)
    # Every scripted scenario raises what it expects: matched, passed.
    for scenario in provoke_errors.SCENARIOS:
        case = cases[scenario.id]
        assert case["kind"] == "expected_error"
        assert case["expected_error"] == scenario.expected
        assert case["observed_error"] == scenario.expected
        assert case["matched"] is True and case["outcome"] == record.PASSED
        assert case["observed_message"]
    mismatch = cases["dry_mismatch"]
    assert mismatch["expected_error"] == E.PROVIDER_CONFIG
    assert mismatch["observed_error"] == E.PROVIDER_REQUEST_REJECTED
    assert mismatch["matched"] is False and mismatch["outcome"] == record.FAILED
    unconfigured = cases["dry_unconfigured"]
    assert unconfigured["outcome"] == record.TEST_ERROR
    assert unconfigured["observed_error"] is None and unconfigured["matched"] is False
    assert "not configured" in unconfigured["detail"]
    n = len(provoke_errors.SCENARIOS)
    assert results["summary"] == {
        "total": n + 2,
        "passed": n,
        "failed": 1,
        "test_error": 1,
        "expected_errors": {"cases": n + 2, "matched": n, "mismatched": 1, "not_observed": 1},
        "by_kind": {"expected_error": {"passed": n, "failed": 1, "test_error": 0 + 1}},
    }

    manifest = json.loads((out / record.MANIFEST_FILE).read_text())
    assert manifest["harness"]["module"] == "evals.provoke_errors"
    assert manifest["tree"]["component"] == provoke_errors.COMPONENT
    assert manifest["tree"]["version"] == (manifest["git"]["release"] or record.UNVERSIONED)
    link = (
        data
        / "verification"
        / provoke_errors.COMPONENT
        / manifest["tree"]["version"]
        / "errors_dry"
    )
    assert link.is_symlink() and link.resolve() == out.resolve()
    assert manifest["calls"]["count"] == 0  # nothing reached a model
    assert manifest["summary"]["dry_run"] is True
    assert manifest["summary"]["results"] == results["summary"]

    report = (out / record.REPORT_FILE).read_text()
    assert f"results: {n} passed, 1 failed, 1 test errors ({n + 2} cases)" in report
    assert f"expected errors: {n} matched, 1 mismatched, 1 not observed" in report
    row = "| `dry_mismatch` | expected_error | failed | BASIS-PROVIDER-002 | BASIS-PROVIDER-005 |"
    assert row + " no |" in report
    console = (out / record.LOG_FILE).read_text()
    captured = capsys.readouterr()
    for text in ("openai_bad_key: passed", "dry_mismatch: failed", "dry_unconfigured: test_error"):
        assert text in console and text in captured.out  # the tee: console and file agree
    assert "sk-ant-basis" not in console and "[redacted]" not in console  # no key was typed


async def test_only_selects_scenarios_and_an_all_matched_run_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)
    out = tmp_path / "errors"
    args = provoke_errors.parse_args(
        ["--out", str(out), "--dry-run", "--only", "openai_bad_key", "anthropic_timeout"]
    )
    assert await provoke_errors.run(args) == 0
    results = record.read_results(out)
    assert [c["id"] for c in results["cases"]] == ["openai_bad_key", "anthropic_timeout"]
    assert results["summary"]["passed"] == 2 and results["summary"]["failed"] == 0


async def test_a_harness_exception_leaves_a_manifest_with_status_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASIS_EVAL_DATA_DIR", raising=False)
    out = tmp_path / "errors"

    def boom(settings: object) -> object:
        raise RuntimeError("the harness broke")

    scenario = provoke_errors.Scenario(
        "broken", E.PROVIDER_ACCOUNT, "x", "openai", boom, lambda s: provoke_errors._request("m")
    )
    args = provoke_errors.parse_args(["--out", str(out), "--dry-run"])
    # A scenario whose *build* raises is a harness failure: the run itself fails and the
    # manifest says so (the case loop did not reach ``run.case``).
    with pytest.raises(RuntimeError):
        await provoke_errors.run(args, scenarios=[scenario])
    manifest = json.loads((out / record.MANIFEST_FILE).read_text())
    assert manifest["status"] == "error" and "the harness broke" in manifest["error"]


def test_every_live_scenario_names_a_catalogued_code_and_a_known_provider() -> None:
    from basis.errors.codes import all_codes

    codes = set(all_codes().values())
    for scenario in provoke_errors.SCENARIOS:
        assert scenario.expected in codes
        assert scenario.provider in ("openai", "anthropic")
        assert scenario.description
    assert len({s.id for s in provoke_errors.SCENARIOS}) == len(provoke_errors.SCENARIOS)
