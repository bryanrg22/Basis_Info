"""The rules tables migration 0019 re-keys stored conventions under, frozen from git
(CLASSIFICATION_CONVENTIONS.md section 10.4).

For each released ``RULES_VERSION`` the commit on main that released it is loaded from
git, and its resolution data is computed with the running :func:`~basis.domain.rules.rule_digest`:
each row's digest, the aliases, the material map of the generic floors and the generic floor
labels. The result is the literal between the ``FROZEN`` markers of
``basis/db/alembic/versions/0019_convention_rule_fingerprints.py``.

    uv run python -m evals.freeze_rule_fingerprints           # rewrite the literal
    uv run python -m evals.freeze_rule_fingerprints --check   # exit 1 if it differs

Run it after a change to the canonical encoding (``RULE_FINGERPRINT_SCHEME``), never to add
a release: a release after 0019 needs no re-keying (its rows are written by fingerprint).
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import pprint
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from basis.domain.rules import rule_digest

BACKEND: Final = Path(__file__).resolve().parents[1]
MIGRATION: Final = (
    BACKEND / "basis" / "db" / "alembic" / "versions" / "0019_convention_rule_fingerprints.py"
)
RULES_PATH: Final = "backEnd/basis/domain/rules.py"
# Every RULES_VERSION a stored convention can carry, and the merge on main that released it.
RELEASES: Final[tuple[tuple[str, str], ...]] = (
    ("2026-10-29.1", "a0bb73a"),  # #93, the conventions
    ("2026-10-30.1", "2911b13"),  # #94, the structure model
    ("2026-10-31.1", "07e9807"),  # #100, the reconciliation gap
)
BEGIN: Final = "# BEGIN FROZEN (evals.freeze_rule_fingerprints)\n"
END: Final = "# END FROZEN\n"


class FreezeError(RuntimeError):
    """A release cannot be read from git, or the migration has no markers."""


def load_rules(commit: str) -> ModuleType:
    """``basis/domain/rules.py`` as of ``commit``, as a module of its own."""
    try:
        source = subprocess.run(
            ["git", "show", f"{commit}:{RULES_PATH}"],
            cwd=BACKEND,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FreezeError(f"git show {commit}:{RULES_PATH} failed (a shallow clone?)") from exc
    name = f"_frozen_rules_{commit}"
    spec = importlib.util.spec_from_loader(name, loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve string annotations through it
    exec(compile(source, f"{commit}:{RULES_PATH}", "exec"), module.__dict__)
    return module


def table_data(rules: ModuleType) -> dict[str, Any]:
    """What resolving a label needs, for one table (``resolve_fingerprint``'s inputs)."""
    structural = frozenset(rules.STRUCTURAL_ATTACHMENTS)
    return {
        "rules_version": rules.RULES_VERSION,
        "row_digests": {k: rule_digest(r, structural) for k, r in sorted(rules.RULES.items())},
        "aliases": dict(sorted(rules.ALIASES.items())),
        "material_rules": dict(sorted(rules.MATERIAL_RULES.items())),
        "generic_floors": sorted(rules.GENERIC_FLOOR_LABELS),
    }


def frozen() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for version, commit in RELEASES:
        data = table_data(load_rules(commit))
        if data.pop("rules_version") != version:
            raise FreezeError(f"{commit} does not release {version}")
        out[version] = data
    return out


def render(data: dict[str, dict[str, Any]]) -> str:
    literal = pprint.pformat(data, width=96, sort_dicts=True)
    return f"{BEGIN}FROZEN: Final[dict[str, dict[str, Any]]] = {literal}\n{END}"


def current_block(text: str) -> str:
    start, stop = text.find(BEGIN), text.find(END)
    if start < 0 or stop < start:
        raise FreezeError(f"{MIGRATION.name}: no FROZEN markers")
    return text[start : stop + len(END)]


def committed(text: str) -> object:
    """The literal between the markers, as data (the formatter may reflow its text)."""
    block = current_block(text)
    return ast.literal_eval(block[block.index("=", block.index("FROZEN:")) + 1 : block.rindex(END)])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the literal differs")
    args = parser.parse_args(argv)
    try:
        text = MIGRATION.read_text(encoding="utf-8")
        data = frozen()
        block = render(data)
        old = current_block(text)
    except FreezeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.check:
        same = committed(text) == data
        print("frozen tables match git" if same else "frozen tables differ from git")
        return 0 if same else 1
    MIGRATION.write_text(text.replace(old, block), encoding="utf-8")
    subprocess.run(["ruff", "format", str(MIGRATION)], cwd=BACKEND, check=False)
    print(json.dumps({v: len(d["row_digests"]) for v, d in data.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
