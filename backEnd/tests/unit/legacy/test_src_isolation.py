"""Invariants for the legacy ``src`` namespace switcher in ``backEnd/conftest.py``.

The switcher lets the evidence_layer and vision_layer suites (each with a top-level
package named ``src``) run in one pytest process. These tests drive it with fake module
tables so they never touch the real ``sys.modules``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

BACKEND = Path(__file__).resolve().parents[3]


def _load_root_conftest() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_basis_root_conftest_under_test", BACKEND / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


root_conftest = _load_root_conftest()
Switcher = root_conftest.SrcNamespaceSwitcher


def fake_module(name: str, root: Path, package: bool = False) -> ModuleType:
    module = ModuleType(name)
    parts = name.split(".")
    if package:
        module.__file__ = str(root.joinpath(*parts, "__init__.py"))
        module.__path__ = [str(root.joinpath(*parts))]
    else:
        module.__file__ = str(root.joinpath(*parts[:-1], parts[-1] + ".py"))
    return module


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    vision, evidence = tmp_path / "vision_layer", tmp_path / "evidence_layer"
    vision.mkdir()
    evidence.mkdir()
    return vision, evidence


def test_switching_swaps_src_modules(roots: tuple[Path, Path]) -> None:
    vision, evidence = roots
    ev_src = fake_module("src", evidence, package=True)
    ev_retrieval = fake_module("src.retrieval", evidence)
    modules: dict[str, ModuleType] = {"src": ev_src, "src.retrieval": ev_retrieval}
    switcher = Switcher([vision, evidence], modules=modules, path=[])

    switcher.activate(vision)
    assert "src" not in modules and "src.retrieval" not in modules

    vi_src = fake_module("src", vision, package=True)
    modules["src"] = vi_src  # what `import src` would do while vision is active

    switcher.activate(evidence)
    assert modules["src"] is ev_src
    assert modules["src.retrieval"] is ev_retrieval

    switcher.activate(vision)
    assert modules["src"] is vi_src
    assert "src.retrieval" not in modules


def test_activate_is_idempotent(roots: tuple[Path, Path]) -> None:
    vision, evidence = roots
    ev_src = fake_module("src", evidence, package=True)
    modules: dict[str, ModuleType] = {"src": ev_src}
    switcher = Switcher([vision, evidence], modules=modules, path=[])
    switcher.activate(evidence)
    switcher.activate(evidence)
    assert modules == {"src": ev_src}


def test_unrelated_and_unknown_modules_untouched(roots: tuple[Path, Path], tmp_path: Path) -> None:
    vision, evidence = roots
    other = tmp_path / "elsewhere"
    srcish = fake_module("src", other, package=True)
    unrelated = fake_module("srcutils", evidence)
    modules: dict[str, ModuleType] = {
        "src": srcish,
        "srcutils": unrelated,
        "json": ModuleType("json"),
    }
    switcher = Switcher([vision, evidence], modules=modules, path=[])
    switcher.activate(vision)
    assert modules["src"] is srcish  # not owned by either root: left alone
    assert modules["srcutils"] is unrelated  # name only starts with "src"
    assert "json" in modules


def test_unknown_root_rejected(roots: tuple[Path, Path], tmp_path: Path) -> None:
    switcher = Switcher(list(roots), modules={}, path=[])
    with pytest.raises(ValueError, match="unknown root"):
        switcher.activate(tmp_path / "nope")


def test_path_reordered_only_when_both_roots_present(roots: tuple[Path, Path]) -> None:
    vision, evidence = roots
    path = ["/usr/lib/python", str(evidence), "/site", str(vision)]
    switcher = Switcher([vision, evidence], modules={}, path=path)

    switcher.activate(vision)
    assert path == ["/usr/lib/python", str(vision), str(evidence), "/site"]

    switcher.activate(evidence)
    assert path == ["/usr/lib/python", str(evidence), str(vision), "/site"]

    only_one = ["/a", str(vision)]
    Switcher([vision, evidence], modules={}, path=only_one).activate(evidence)
    assert only_one == ["/a", str(vision)]  # never adds entries


def test_root_for_maps_paths(roots: tuple[Path, Path], tmp_path: Path) -> None:
    vision, evidence = roots
    switcher = Switcher([vision, evidence], modules={}, path=[])
    assert switcher.root_for(vision / "tests" / "test_x.py") == vision
    assert switcher.root_for(evidence / "tests" / "test_y.py") == evidence
    assert switcher.root_for(tmp_path / "agentic" / "tests" / "test_z.py") == evidence


def test_real_roots_are_the_legacy_packages() -> None:
    assert root_conftest.VISION_ROOT == BACKEND / "vision_layer"
    assert root_conftest.EVIDENCE_ROOT == BACKEND / "evidence_layer"
    assert (root_conftest.VISION_ROOT / "src" / "__init__.py").is_file()
    assert (root_conftest.EVIDENCE_ROOT / "src" / "__init__.py").is_file()


def test_legacy_items_are_marked() -> None:
    assert root_conftest.is_legacy_path(BACKEND / "agentic" / "tests" / "test_config.py")
    assert root_conftest.is_legacy_path(BACKEND / "vision_layer" / "tests" / "test_x.py")
    assert not root_conftest.is_legacy_path(BACKEND / "tests" / "api" / "test_health.py")


@pytest.mark.slow
@pytest.mark.regression
def test_legacy_suites_pass_together_in_both_orders() -> None:
    """Regression: before the switcher, collecting vision after agentic failed with
    ``ModuleNotFoundError: No module named 'src.schemas.detection'``."""
    for order in (
        ["agentic/tests/test_agents.py", "vision_layer/tests"],
        ["vision_layer/tests", "agentic/tests/test_agents.py"],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *order],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            timeout=110,
        )
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
