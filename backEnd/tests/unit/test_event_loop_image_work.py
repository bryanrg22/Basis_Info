"""No ``async def`` in ``basis/`` decodes, resizes, renders or crops an image on the event loop.

The worker runs every async activity and Temporal's workflow activations on one event loop,
and the SDK times each activation with a 2 s timer on that loop (``TMPRL1101``, "Potential
deadlock detected"). CPU work inside an ``async def`` holds the loop: preparing one
12-megapixel photo is about 0.08 s, and 20 photo activities started together held the loop
1.7 s on a fast laptop, so a slower host turns a quick activation into a reported deadlock.
The same work on a thread (``asyncio.to_thread``, :func:`basis.vision.prep.prepare_image_in_thread`)
held it at most 0.08 s (EVAL_REPORT 75).

The walk flags a direct call to one of :data:`HEAVY` (or ``Image.open``) in the body of an
``async def``; nested ``def`` and ``lambda`` bodies are skipped, since they are what gets
handed to a thread. Passing the function itself to ``asyncio.to_thread`` is not a call and
is allowed. :data:`ALLOWED` lists an exception, with the reason, by ``path:function``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import basis

PACKAGE = Path(basis.__file__).resolve().parent

# Image and document work measured or known to take CPU time per call (decode, resample,
# blend, encode, PDF parse or render). ``build_request`` is heavy only where it prepares
# images itself (surface_crops), so it is checked in that module alone.
HEAVY = frozenset(
    {
        "prepare_image",
        "crop_image",
        "jpeg_bytes",
        "render_pdf",
        "render_marks",
        "read_pdf",
        "read_appraisal_upload",
        "_open_rgb",
        "_crop_jpeg",
        "_prepared_jpeg",
        "tiles",
        "exhibit_photo",
        "masks_from_colours",
    }
)
HEAVY_IN_MODULE = {"vision/surface_crops.py": frozenset({"build_request"})}
ALLOWED: dict[str, str] = {}


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "open"
        and isinstance(func.value, ast.Name)
        and func.value.id == "Image"
    ):
        return "Image.open"
    return None


def _direct_calls(body: list[ast.stmt]) -> list[ast.Call]:
    """Calls in ``body``, not descending into nested functions or lambdas."""
    found: list[ast.Call] = []
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        if isinstance(node, ast.Call):
            found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def offenders(package: Path = PACKAGE) -> list[str]:
    out: list[str] = []
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(package).as_posix()
        heavy = HEAVY | HEAVY_IN_MODULE.get(rel, frozenset())
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if f"{rel}:{node.name}" in ALLOWED:
                continue
            for call in _direct_calls(node.body):
                name = _called_name(call)
                if name == "Image.open" or name in heavy:
                    out.append(f"{rel}:{call.lineno} {node.name} calls {name}")
    return out


def test_no_async_function_does_image_work_on_the_event_loop() -> None:
    assert offenders() == []


def test_the_walk_finds_a_direct_call_and_allows_a_thread(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        "import asyncio\n"
        "from PIL import Image\n"
        "async def bad(data):\n"
        "    return prepare_image(data)\n"
        "async def opens(data):\n"
        "    return Image.open(data)\n"
        "async def good(data):\n"
        "    return await asyncio.to_thread(prepare_image, data)\n"
        "async def nested(data):\n"
        "    def work():\n"
        "        return prepare_image(data)\n"
        "    return await asyncio.to_thread(work)\n"
        "def sync_caller(data):\n"
        "    return prepare_image(data)\n",
        encoding="utf-8",
    )
    assert offenders(tmp_path) == [
        "mod.py:4 bad calls prepare_image",
        "mod.py:6 opens calls Image.open",
    ]
