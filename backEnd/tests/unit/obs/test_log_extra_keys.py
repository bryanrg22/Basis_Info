"""No logging call in ``basis/`` passes an ``extra=`` key that a LogRecord already has.

``Logger.makeRecord`` raises ``KeyError("Attempt to overwrite 'name' in LogRecord")`` when an
``extra`` key is a LogRecord attribute (``name``, ``msg``, ``module``...) or ``message`` /
``asctime``. The record is only built when the level is enabled, so a suite that runs with
the root logger at WARNING never sees an INFO call fail; at the deployed ``LOG_LEVEL=INFO``
the request answers 500 (``POST /v1/orgs``, EVAL_REPORT 66). This walks the source instead
of relying on the level a test happens to run at.

Ruff's G101 (``logging-extra-attr-clash``, enabled in ``pyproject.toml``) is the standard
check and runs at commit time; it reads only the literal at the call. This test adds what
G101 does not follow (measured in EVAL_REPORT 66): a key reached through ``{**fields}`` or
``extra=fields``, and every ``extra=`` neither can read.

* Every ``extra={...}`` or ``extra=dict(...)`` on a logger method (any receiver, so
  ``workflow.logger`` too) is checked key by key, including a ``**spread`` or a bare name
  whose source is a dict assigned once in the same function.
* An ``extra=`` whose keys cannot be read (another call, a parameter) is listed in
  ``DYNAMIC_EXTRAS`` after its keys were read by hand, so a new one fails until someone
  does the same.
* The reserved set is computed from the running interpreter's LogRecord, the same test
  ``makeRecord`` applies, so a Python upgrade that adds an attribute is picked up.
"""

from __future__ import annotations

import ast
import logging
import textwrap
from dataclasses import dataclass
from pathlib import Path

import basis

PACKAGE = Path(basis.__file__).resolve().parent
LOG_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"}
)
# The check in ``logging.Logger.makeRecord``: ``key in ["message", "asctime"]`` or an
# attribute the record already carries.
RESERVED = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None)).keys() | {"message", "asctime"}
)
# ``extra=`` values that are not dict literals, as ``path:function``, each read by hand:
# the keys they can carry, and why none is reserved.
DYNAMIC_EXTRAS = {
    # IngestResult.summary(): corpus_version_id, corpus, study_id, version, embedding_model,
    # chunker, chunks, carried_forward_chunks, scanned_docs, documents, seconds.
    "retrieval/ingest.py:write_prepared",
}


@dataclass(frozen=True)
class Extra:
    where: str  # path:line
    function: str
    keys: frozenset[str]
    dynamic: bool


def _is_dict_call(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict"


def _mapping(value: ast.expr, scope: ast.AST | None) -> ast.Dict | ast.Call | None:
    """``value`` as a readable mapping: a dict literal, ``dict(...)``, or a bare name
    assigned exactly once in ``scope`` to one of those."""
    if isinstance(value, ast.Dict):
        return value
    if isinstance(value, ast.Call):
        return value if _is_dict_call(value) else None
    if not isinstance(value, ast.Name) or scope is None:
        return None
    assigned = [
        node.value
        for node in ast.walk(scope)
        if isinstance(node, ast.Assign | ast.AnnAssign)
        and any(
            isinstance(t, ast.Name) and t.id == value.id
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    ]
    if len(assigned) == 1:
        source = assigned[0]
        if isinstance(source, ast.Dict) or (isinstance(source, ast.Call) and _is_dict_call(source)):
            return source
    return None


def _keys(node: ast.Dict | ast.Call, scope: ast.AST | None) -> tuple[set[str], bool]:
    """The string keys of a mapping and whether part of it could not be read."""
    keys: set[str] = set()
    dynamic = False
    if isinstance(node, ast.Call):  # dict(a=1, **other); dict(other) is not readable
        dynamic = bool(node.args)
        pairs: list[tuple[ast.expr | None, ast.expr]] = [
            (None if k.arg is None else ast.Constant(k.arg), k.value) for k in node.keywords
        ]
    else:
        pairs = list(zip(node.keys, node.values, strict=True))
    for key, value in pairs:
        if key is None:  # a **spread
            source = _mapping(value, scope)
            if source is None:
                dynamic = True
            else:
                more, more_dynamic = _keys(source, scope)
                keys |= more
                dynamic |= more_dynamic
        elif isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
        else:
            dynamic = True
    return keys, dynamic


class _ExtraFinder(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.scopes: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        self.found: list[Extra] = []

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scopes.append(node)
        self.generic_visit(node)
        self.scopes.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in LOG_METHODS:
            scope = self.scopes[-1] if self.scopes else None
            for keyword in node.keywords:
                if keyword.arg != "extra":
                    continue
                mapping = _mapping(keyword.value, scope)
                keys, dynamic = _keys(mapping, scope) if mapping else (set(), True)
                self.found.append(
                    Extra(
                        f"{self.path}:{node.lineno}",
                        scope.name if scope else "<module>",
                        frozenset(keys),
                        dynamic,
                    )
                )
        self.generic_visit(node)


def extras_in(source: str, path: str) -> list[Extra]:
    """Every ``extra=`` passed to a logger method in ``source``, in source order."""
    finder = _ExtraFinder(path)
    finder.visit(ast.parse(source))
    return finder.found


def all_extras() -> list[Extra]:
    found: list[Extra] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        found.extend(extras_in(path.read_text(), relative))
    return found


# --------------------------------------------------------------------------- the source


def test_no_extra_key_is_a_log_record_attribute() -> None:
    clashes = [(e.where, sorted(e.keys & RESERVED)) for e in all_extras() if e.keys & RESERVED]
    assert clashes == [], (
        f"extra= keys that LogRecord reserves (rename them, e.g. name -> firm_name): {clashes}"
    )


def test_every_dynamic_extra_was_read_by_hand() -> None:
    dynamic = {f"{e.where.split(':')[0]}:{e.function}" for e in all_extras() if e.dynamic}
    assert dynamic == DYNAMIC_EXTRAS, (
        "an extra= the walk cannot read: check its keys against RESERVED by hand, then list "
        f"it in DYNAMIC_EXTRAS (new: {sorted(dynamic - DYNAMIC_EXTRAS)}, gone: "
        f"{sorted(DYNAMIC_EXTRAS - dynamic)})"
    )


def test_the_walk_sees_the_backend_s_logging_calls() -> None:
    # A walk that silently found nothing would pass the two tests above.
    extras = all_extras()
    assert len(extras) > 50
    assert any(e.where.startswith("api/routes/orgs.py:") for e in extras)


# --------------------------------------------------------------------------- the walk


def test_reserved_matches_what_make_record_refuses() -> None:
    logger = logging.Logger("basis.tests.reserved")
    for key in sorted(RESERVED):
        try:
            logger.makeRecord("n", logging.INFO, "f", 1, "m", None, None, extra={key: 1})
        except KeyError:
            continue
        raise AssertionError(f"{key} is in RESERVED but makeRecord accepts it")
    logger.makeRecord("n", logging.INFO, "f", 1, "m", None, None, extra={"firm_name": 1})
    assert {"name", "msg", "args", "module", "message", "asctime", "taskName"} <= RESERVED


def test_the_walk_flags_the_original_bug() -> None:
    source = textwrap.dedent(
        """
        async def create_firm(org, user):
            logger.info(
                "firm created",
                extra={"org_id": str(org.id), "user_id": str(user.id), "name": org.name},
            )
        """
    )
    [extra] = extras_in(source, "api/routes/orgs.py")
    assert extra.keys & RESERVED == {"name"} and not extra.dynamic
    assert extra.function == "create_firm"


def test_the_walk_follows_a_local_spread_and_marks_the_rest_dynamic() -> None:
    source = textwrap.dedent(
        """
        def deliver(message, result):
            fields = {"message_id": message.id, "module": message.kind}
            log.info("delivered", extra={**fields, "attempts": 1})
            log.warning("refused", extra={**message.fields(), "error": result})
            log.error("failed", extra=fields)
            log.log(logging.INFO, "plain", extra={"msecs": 3})
            settings = ConfigDict(extra="forbid")
            other.record("x", extra={"name": 1})
        """
    )
    spread, opaque, variable, level = extras_in(source, "relay/delivery.py")
    assert spread.keys == {"message_id", "module", "attempts"} and not spread.dynamic
    assert spread.keys & RESERVED == {"module"}
    assert opaque.keys == {"error"} and opaque.dynamic
    assert variable.keys & RESERVED == {"module"} and not variable.dynamic
    assert variable.function == "deliver"
    assert level.keys & RESERVED == {"msecs"}


def test_a_name_assigned_twice_or_from_a_call_is_dynamic() -> None:
    source = textwrap.dedent(
        """
        def run(result):
            fields = {"a": 1}
            fields = {"b": 2}
            log.info("x", extra=fields)
            log.info("y", extra=result.summary())
        log.info("z", extra={"msg": 1})
        """
    )
    twice, call, module_level = extras_in(source, "x.py")
    assert twice.dynamic and call.dynamic
    assert module_level.function == "<module>" and module_level.keys & RESERVED == {"msg"}


def test_the_walk_reads_dict_calls_and_temporal_s_loggers() -> None:
    source = textwrap.dedent(
        """
        @workflow.run
        async def run(self, other):
            base = dict(study_id=1, lineno=2)
            workflow.logger.warning("a", extra=dict(name=1))
            activity.logger.info("b", extra={**base, "stage": "x"})
            workflow.logger.info("c", extra=dict(other, error_code=3))
        """
    )
    called, spread, positional = extras_in(source, "workflow/study_workflow.py")
    assert called.keys == {"name"} and not called.dynamic
    assert spread.keys == {"study_id", "lineno", "stage"} and not spread.dynamic
    assert spread.keys & RESERVED == {"lineno"}
    assert positional.keys == {"error_code"} and positional.dynamic
