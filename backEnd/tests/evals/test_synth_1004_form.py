"""``evals.appraisal.synth_1004`` on the private blank Form 70 (``eval_data``).

Generates two documents into a temporary directory and checks, independently of the
generator's own verifier where it can: every present field's printed text is in the text
layer of its truth page (and its ``also_on`` pages), every quote is a span of that page,
every planned ``X`` is inside its checkbox square and no other square is marked, the
printed form text survives, the output is byte-identical for the same seed, and a scan
copy has no text layer. Skipped without ``BASIS_EVAL_DATA_DIR`` or the blank form; the
generated PDFs never leave the temporary directory.
"""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
from typing import Final

import pytest

from evals.appraisal import synth_1004 as S
from evals.appraisal.truth import TruthSet, dump_truth

ENV_VAR: Final = "BASIS_EVAL_DATA_DIR"


def _form() -> Path | None:
    value = os.environ.get(ENV_VAR)
    if not value:
        return None
    path = Path(value).expanduser() / "appraisal" / S.FORM_FILE
    return path if path.is_file() else None


FORM: Final = _form()

pytestmark = [
    pytest.mark.eval_data,
    pytest.mark.skipif(
        FORM is None,
        reason=f"{ENV_VAR} is not set or lacks appraisal/{S.FORM_FILE} (private eval data)",
    ),
]

_WS = re.compile(r"\s+")


def _texts(pdf: bytes) -> list[str]:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return [_WS.sub(" ", page.extract_text() or "") for page in doc.pages]


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, TruthSet, list[S.Generated]]:
    assert FORM is not None
    out = tmp_path_factory.mktemp("synth_1004")
    truth, results = S.generate(FORM, out, count=2, scans=0, seed=11)
    dump_truth(truth, out / "truth" / f"{S.SET_NAME}.json")
    return out, truth, results


def test_generator_verifier_finds_no_problem(
    generated: tuple[Path, TruthSet, list[S.Generated]],
) -> None:
    _, _, results = generated
    assert len(results) == 2
    for r in results:
        assert r.problems == [], (r.plan.name, r.problems[:10])


def test_printed_values_are_in_the_text_layer_of_their_pages(
    generated: tuple[Path, TruthSet, list[S.Generated]],
) -> None:
    out, truth, _ = generated
    loaded = TruthSet.model_validate(json.loads((out / "truth" / f"{S.SET_NAME}.json").read_text()))
    assert loaded == truth
    checked = 0
    for doc in truth.documents:
        pdf = (out / doc.pdf).read_bytes()
        texts = _texts(pdf)
        assert len(texts) == doc.pages
        for f in doc.fields:
            if f.printed is None:
                continue
            assert f.page is not None
            page = texts[f.page - 1]
            parts = f.printed.split("; ") if f.type == "enum_multi" else [f.printed]
            for part in parts:
                assert part in page, (doc.document, f.key, part)
            if f.status == "present":
                assert f.quote is not None and f.quote in page, (doc.document, f.key)
            for other in f.also_on:
                core = f.printed.removesuffix(" sq.ft.")
                assert core in texts[other - 1], (doc.document, f.key, other)
            checked += 1
    assert checked > 200


def test_x_marks_sit_in_their_squares(
    generated: tuple[Path, TruthSet, list[S.Generated]],
) -> None:
    import pdfplumber

    out, _, results = generated
    geo = S.FormGeometry(FORM.read_bytes() if FORM else b"")
    for r in results:
        pdf = (out / r.truth.pdf).read_bytes()
        groups = {k: fp.checks for k, fp in r.plan.fields.items() if fp.checks}
        groups.update(r.plan.checks)
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            for group, options in S.CHECKS.items():
                for code, check in options.items():
                    x0, top, x1, bottom = geo.square(check, f"{group}.{code}")
                    marks = [
                        c
                        for c in doc.pages[check.page - 1].chars
                        if c["text"] == "X"
                        and x0 < (c["x0"] + c["x1"]) / 2 < x1
                        and top < (c["top"] + c["bottom"]) / 2 < bottom
                    ]
                    # The X glyph lies wholly inside the square.
                    for c in marks:
                        assert x0 < c["x0"] and c["x1"] < x1, (group, code)
                    assert len(marks) == (1 if code in groups.get(group, ()) else 0), (
                        r.plan.name,
                        group,
                        code,
                    )


def test_same_seed_same_bytes_and_a_scan_has_no_text(
    generated: tuple[Path, TruthSet, list[S.Generated]],
) -> None:
    out, _, results = generated
    assert FORM is not None
    geo = S.FormGeometry(FORM.read_bytes())
    plan = results[0].plan
    again = S.render_document(plan, geo)
    assert again == (out / results[0].truth.pdf).read_bytes()
    scan_plan = S.DocPlan(**{**vars(plan), "scan_dpi": 150, "scan_angle": 0.4})
    scan = S.rasterize(again, scan_plan)
    texts = _texts(scan)
    assert len(texts) == plan.pages and not any(t.strip() for t in texts)
    assert S.rasterize(again, scan_plan) == scan  # the noise is seeded too
