"""``production`` (today's pipeline, unchanged) and ``checkbox_context`` (its minimal fix).

``production`` calls :func:`basis.extraction.pipeline.extract_appraisal` exactly as the
``extract_fields`` activity does and reports **no regions**: the pipeline discards Azure DI's
polygons, so today's review page cannot highlight a value (the highlight metric is n/a).

``checkbox_context`` is the same pipeline with two context hooks
(:class:`~basis.extraction.pipeline.ExtractionContext` ``fallback_pages`` and
``always_ask``), aimed at the dominant measured defect: the fallback model was given Azure DI's
page *lines*, which carry no ``:selected:`` marks, so it read the free text beside a box
("Municipal" instead of the checked Public). Here the model gets each page's ``content``
*with* the marks, each followed by its box's label read from the page geometry
(:mod:`~evals.appraisal.systems.marks`: ``:selected:[Public]``), every checkbox key
(:data:`CHECKBOX_KEYS`) is asked even when an Azure DI key-value pair filled it (the wrong-box
pairs: foundation "Walls"), and the prompt ``appraisal_field_fallback_marks`` explains the
marks. Everything else (the verbatim guard,
the 0.6 confidence cap of a model value, the review gate) is production's.

Regions for ``checkbox_context`` (it has the raw layout): an Azure DI key-value value's own
polygon (:func:`kv_regions` repeats ``azure_candidates``' choice of pair), else the value's
words on its cited page, nearest the field's caption (:func:`locate_field`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from basis.extraction.azure_di import AzureDiResult
from basis.extraction.fallback import PROMPT_NAME
from basis.extraction.field_mappings import FIELD_BY_KEY, key_for_label
from basis.extraction.pipeline import ExtractionContext, extract_appraisal
from basis.extraction.regex_fields import section_lines
from basis.extraction.results import ExtractionResult
from basis.extraction.sections import _section_of_caption, _sections_on_page
from basis.llm import prompts
from evals.appraisal.systems.base import (
    CHECKBOX_KEYS,
    ModelChoice,
    SystemEnv,
    SystemOutput,
)
from evals.appraisal.systems.layout import Box, Layout, box_of, locate, region
from evals.appraisal.systems.marks import labelled_texts

MARKS_PROMPT: Final = "appraisal_field_fallback_marks"


class _SourcesAzure:
    """The pipeline's Azure DI client seam over :class:`DocumentSources` (analyzed once)."""

    name = "azure_di"

    def __init__(self, env: SystemEnv) -> None:
        self._env = env

    async def analyze(self, pdf_bytes: bytes) -> AzureDiResult:
        result = await self._env.sources.layout(pdf_bytes)
        assert result is not None  # only built when the sources have a layout client
        return result


def production_context(
    env: SystemEnv,
    *,
    model: ModelChoice | None = None,
    prompt_name: str = PROMPT_NAME,
) -> ExtractionContext:
    """The context the ``extract_fields`` activity builds, from the recorded settings
    (``--model``/``--effort`` override the text model and its effort)."""
    settings = env.extraction
    text_model = model.model if model is not None and model.model else settings.text_model
    effort = model.effort if model is not None and model.effort else settings.reasoning_effort
    return ExtractionContext(
        call=env.call,
        provider=env.provider,
        azure=_SourcesAzure(env) if env.sources.has_layout else None,
        prompt=prompts.latest(prompt_name),
        text_model=text_model,
        review_threshold=settings.review_threshold,
        fallback_max_tokens=settings.output_budget,
        fallback_reasoning_effort=effort,
        fallback_scope="missing" if settings.fallback_scope == "missing" else "critical",
        fallback_context_chars=settings.context_chars,
    )


class Production:
    name = "production"

    async def run(self, env: SystemEnv) -> SystemOutput:
        result = await extract_appraisal(env.pdf, env.doc_id, production_context(env))
        return SystemOutput(result=result, regions=None)


class CheckboxContext:
    name = "checkbox_context"

    def __init__(self, model: ModelChoice) -> None:
        self.model = model

    async def run(self, env: SystemEnv) -> SystemOutput:
        ctx = production_context(env, model=self.model, prompt_name=MARKS_PROMPT)

        def pages_with_marks(azure: AzureDiResult) -> Sequence[str]:
            geometry = env.sources.geometry_if_loaded()
            if geometry is None:  # no raw layout: production's lines (and say so)
                return azure.page_texts()
            return labelled_texts(geometry)

        ctx.fallback_pages = pages_with_marks
        ctx.always_ask = CHECKBOX_KEYS
        result = await extract_appraisal(env.pdf, env.doc_id, ctx)
        result = result.model_copy(update={"method": f"{result.method}+checkbox_context"})
        geometry = env.sources.geometry_if_loaded()
        if geometry is None:
            result.notes.append("no raw Azure DI layout: the model got page lines, no regions")
            return SystemOutput(result=result, regions=None)
        raw = env.sources.layout_raw()
        return SystemOutput(result=result, regions=production_regions(result, geometry, raw))


# --------------------------------------------------------------------------- regions


def kv_regions(azure: AzureDiResult, raw: Mapping[str, Any]) -> dict[str, tuple[int, Box, str]]:
    """Per catalogue key, the page, value box and value text of the key-value pair
    :func:`basis.extraction.sections.azure_candidates` maps to it (same order, same rules:
    the first pair with a value whose caption names the key wins)."""
    sizes = {
        int(p["pageNumber"]): (float(p.get("width") or 0), float(p.get("height") or 0))
        for p in raw.get("pages") or ()
    }
    raw_pairs = [
        p
        for p in raw.get("keyValuePairs") or ()
        if p.get("key") is not None and str((p["key"] or {}).get("content") or "").strip()
    ]
    if len(raw_pairs) != len(azure.key_value_pairs):
        return {}  # not the analysis the result was converted from
    lines = section_lines(azure.page_texts())
    found: dict[str, tuple[int, Box, str]] = {}
    seen: set[str] = set()
    for pair, raw_pair in zip(azure.key_value_pairs, raw_pairs, strict=True):
        if pair.value is None or not pair.value.strip():
            continue
        sections: list[str | None] = []
        located = _section_of_caption(lines, pair.page, pair.key)
        if located is not None:
            sections.append(located)
        sections.extend(s for s in _sections_on_page(lines, pair.page) if s != located)
        sections.append(None)
        key = next(
            (k for k in (key_for_label(pair.key, s) for s in sections) if k is not None), None
        )
        if key is None or key in seen:
            continue
        seen.add(key)
        value = raw_pair.get("value") or {}
        for bounding in value.get("boundingRegions") or ():
            number = int(bounding.get("pageNumber", 0))
            width, height = sizes.get(number, (0.0, 0.0))
            box = box_of(bounding.get("polygon"), width, height)
            if box is not None:
                found[key] = (number, box, pair.value)
                break
    return found


def caption_box(geometry: Layout, page: int | None, key: str) -> Box | None:
    spec = FIELD_BY_KEY.get(key)
    if spec is None or not spec.captioned:
        return None
    for label in spec.labels:
        box = locate(geometry.page(page), label)
        if box is not None:
            return box
    return None


def locate_field(
    geometry: Layout, key: str, page: int | None, texts: Sequence[str | None]
) -> dict[str, Any] | None:
    """The first of ``texts`` found on ``page``, nearest the key's caption."""
    if page is None:
        return None
    near = caption_box(geometry, page, key)
    for text in texts:
        box = locate(geometry.page(page), text, near=near)
        if box is not None:
            return region(page, box)
    return None


def production_regions(
    result: ExtractionResult, geometry: Layout, raw: Mapping[str, Any] | None
) -> dict[str, list[dict[str, Any]]]:
    """Regions for a production-pipeline result (module doc)."""
    pairs: dict[str, tuple[int, Box, str]] = {}
    azure = result.raw.get("azure_di")
    if raw is not None and isinstance(azure, Mapping):
        pairs = kv_regions(AzureDiResult.model_validate(azure), raw)
    regions: dict[str, list[dict[str, Any]]] = {}
    for key, item in result.fields.items():
        pair = pairs.get(key)
        if item.source == "azure" and pair is not None and pair[2] == item.raw_text:
            regions[key] = [region(pair[0], pair[1])]
            continue
        found = locate_field(geometry, key, item.page, [item.raw_text, item.value_text])
        regions[key] = [found] if found is not None else []
    return regions
