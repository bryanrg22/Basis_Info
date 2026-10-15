"""Root pytest configuration for ``backEnd/``.

Legacy isolation for the top-level ``src`` package name
-------------------------------------------------------
``evidence_layer/`` and ``vision_layer/`` each contain a different top-level package
called ``src``. Legacy agentic code puts ``evidence_layer/`` on ``sys.path`` and runs
``from src.retrieval import ...``; the legacy vision tests put ``vision_layer/`` on
``sys.path`` and run ``from src.schemas.detection import ...``. One interpreter can only
hold one ``sys.modules["src"]``, so the suites pass alone but fail when collected
together (whichever ``src`` is imported first wins).

Until both packages are migrated into ``basis/`` (BUILD_PLAN section 2), this file keeps
two namespaces for ``src``: before a collector or test under ``vision_layer/`` runs, the
vision ``src`` modules are put in ``sys.modules`` (and the evidence ones are stashed);
for everything else the evidence ``src`` modules are active. No legacy file is changed.
"""

from __future__ import annotations

import sys
from collections.abc import Generator, MutableMapping
from pathlib import Path
from types import ModuleType

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent
VISION_ROOT = BACKEND_ROOT / "vision_layer"
EVIDENCE_ROOT = BACKEND_ROOT / "evidence_layer"

SRC_PACKAGE = "src"


def _is_src_name(name: str) -> bool:
    return name == SRC_PACKAGE or name.startswith(SRC_PACKAGE + ".")


def _resolve(entry: str) -> Path | None:
    try:
        return Path(entry or ".").resolve()
    except (OSError, RuntimeError):
        return None


class SrcNamespaceSwitcher:
    """Swap which root's ``src`` package is visible through ``sys.modules``/``sys.path``.

    ``modules`` and ``path`` default to the interpreter's ``sys.modules``/``sys.path``
    and are injectable so the logic can be unit tested without touching global state.
    """

    def __init__(
        self,
        roots: list[Path],
        modules: MutableMapping[str, ModuleType] | None = None,
        path: list[str] | None = None,
    ) -> None:
        self.roots = [root.resolve() for root in roots]
        self.modules: MutableMapping[str, ModuleType] = sys.modules if modules is None else modules
        self.path: list[str] = sys.path if path is None else path
        self.stash: dict[Path, dict[str, ModuleType]] = {root: {} for root in self.roots}

    def owner(self, module: ModuleType) -> Path | None:
        """Return the configured root a module was loaded from, or None if unknown."""
        locations: list[str] = []
        file = getattr(module, "__file__", None)
        if file:
            locations.append(file)
        locations.extend(getattr(module, "__path__", None) or [])
        for location in locations:
            resolved = _resolve(location)
            if resolved is None:
                continue
            for root in self.roots:
                if resolved.is_relative_to(root):
                    return root
        return None

    def activate(self, root: Path) -> None:
        """Make ``root``'s ``src`` package the one that ``import src`` resolves to."""
        root = root.resolve()
        if root not in self.stash:
            raise ValueError(f"unknown root {root}")
        for name in [n for n in self.modules if _is_src_name(n)]:
            module = self.modules[name]
            owner = self.owner(module)
            if owner is None or owner == root:
                continue  # leave unknown modules and the active root's modules alone
            self.stash[owner][name] = module
            del self.modules[name]
        for name, module in self.stash[root].items():
            self.modules.setdefault(name, module)
        self.stash[root].clear()
        self._prefer_on_path(root)

    def _prefer_on_path(self, root: Path) -> None:
        """If both roots are on the path, move ``root`` ahead of the others.

        Only matters for the first ``import src`` of a root; afterwards submodules are
        found through ``src.__path__``. Entries are never added, only reordered.
        """
        indexes = {i: _resolve(entry) for i, entry in enumerate(self.path)}
        mine = [i for i, p in indexes.items() if p == root]
        others = [i for i, p in indexes.items() if p in self.roots and p != root]
        if not mine or not others or min(mine) < min(others):
            return
        entry = self.path.pop(min(mine))
        self.path.insert(min(others), entry)

    def root_for(self, node_path: Path) -> Path:
        """Vision paths get the vision ``src``; everything else gets the evidence one."""
        resolved = node_path.resolve()
        for root in self.roots:
            if root != self.roots[-1] and resolved.is_relative_to(root):
                return root
        return self.roots[-1]


# Order matters: the last root is the default for paths outside every other root.
_switcher = SrcNamespaceSwitcher(roots=[VISION_ROOT, EVIDENCE_ROOT])


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector: pytest.Collector) -> Generator[None, object, object]:
    _switcher.activate(_switcher.root_for(Path(collector.path)))
    return (yield)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    _switcher.activate(_switcher.root_for(Path(item.path)))


LEGACY_ROOTS = tuple(BACKEND_ROOT / name for name in ("agentic", "evidence_layer", "vision_layer"))


def is_legacy_path(path: Path) -> bool:
    resolved = path.resolve()
    return any(resolved.is_relative_to(root) for root in LEGACY_ROOTS)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark legacy tests so ``-m "not legacy"`` runs only the basis suite."""
    for item in items:
        if is_legacy_path(Path(item.path)):
            item.add_marker(pytest.mark.legacy)
