"""``production`` (the ``extract_fields`` activity's path as configured), ``pipeline`` (the
pipeline method, before EVAL_REPORT section 33) and ``checkbox_context`` (the pipeline's
minimal fix).

``production`` calls :func:`basis.extraction.pipeline.extract_appraisal` exactly as the
``extract_fields`` activity does, with the method the extraction settings name
(``EXTRACTION_METHOD``; ``grounded``, the default since section 33.5, reads the fields in one
grounded call on ``EXTRACTION_GROUNDED_MODEL``, :mod:`basis.extraction.grounded`, and falls
back to the pipeline on the same analysis when it cannot run). ``--model`` / ``--effort`` /
``--max-tokens``, when ``--model`` is given, choose the grounded model instead of
``EXTRACTION_GROUNDED_*``. ``pipeline`` is the same call with the method forced to
``pipeline``: Azure DI, the text layer, one ``TEXT_MODEL`` fallback call; what the regression
baseline (``baseline.json``) and the sweep's other pipeline-based systems measure. Their
regions are the pipeline's own (each field's highlight box, :mod:`basis.extraction.regions`)
or, for the grounded reading, its quote's box.

``checkbox_context`` is the pipeline with two context hooks
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
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from basis.extraction.azure_di import AzureDiResult
from basis.extraction.fallback import PROMPT_NAME
from basis.extraction.geometry import has_geometry, layout_of
from basis.extraction.grounded import GroundedModel
from basis.extraction.marks import labelled_texts
from basis.extraction.pipeline import ExtractionContext, ExtractionMethod, extract_appraisal
from basis.extraction.regions import region_json
from basis.extraction.results import ExtractionResult
from basis.llm import prompts
from evals.appraisal.systems.base import (
    CHECKBOX_KEYS,
    ModelChoice,
    SystemEnv,
    SystemOutput,
)

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
    method: ExtractionMethod | None = None,
    grounded: ModelChoice | None = None,
) -> ExtractionContext:
    """The context the ``extract_fields`` activity builds, from the recorded settings
    (``model``: ``--model``/``--effort`` override the text model and its effort; ``grounded``
    with a model: the grounded model, effort and output budget; ``method``: forced instead of
    the settings')."""
    settings = env.extraction
    text_model = model.model if model is not None and model.model else settings.text_model
    effort = model.effort if model is not None and model.effort else settings.reasoning_effort
    if grounded is not None and grounded.model:
        reader = GroundedModel(grounded.model, grounded.effort, grounded.max_tokens)
    else:
        reader = GroundedModel(
            settings.grounded_model,
            settings.grounded_reasoning_effort,
            settings.grounded_max_tokens,
        )
    chosen = method or settings.method
    if chosen not in ("grounded", "pipeline"):
        raise ValueError(f"unknown extraction method {chosen!r} in the extraction settings")
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
        method="grounded" if chosen == "grounded" else "pipeline",
        grounded=reader,
    )


class Production:
    """``production`` (``method=None``: the settings' method) or ``pipeline``."""

    def __init__(
        self, method: ExtractionMethod | None = None, model: ModelChoice | None = None
    ) -> None:
        self.method = method
        self.model = model
        self.name = "pipeline" if method == "pipeline" else "production"

    async def run(self, env: SystemEnv) -> SystemOutput:
        ctx = production_context(env, method=self.method, grounded=self.model)
        result = await extract_appraisal(env.pdf, env.doc_id, ctx)
        quotes = result.raw.get("grounded", {}).get("quotes") or {}
        return SystemOutput(result=result, regions=result_regions(result), quotes=dict(quotes))


def pipeline() -> Production:
    """Today's pipeline before section 33 (the regression baseline's system)."""
    return Production("pipeline")


class CheckboxContext:
    name = "checkbox_context"

    def __init__(self, model: ModelChoice) -> None:
        self.model = model

    async def run(self, env: SystemEnv) -> SystemOutput:
        ctx = production_context(env, model=self.model, prompt_name=MARKS_PROMPT, method="pipeline")

        def pages_with_marks(azure: AzureDiResult) -> Sequence[str]:
            if not has_geometry(azure):  # no geometry: production's lines (and say so)
                return azure.page_texts()
            return labelled_texts(layout_of(azure))

        ctx.fallback_pages = pages_with_marks
        ctx.always_ask = CHECKBOX_KEYS
        result = await extract_appraisal(env.pdf, env.doc_id, ctx)
        result = result.model_copy(update={"method": f"{result.method}+checkbox_context"})
        regions = result_regions(result)
        if regions is None:
            result.notes.append("no Azure DI geometry: the model got page lines, no regions")
        return SystemOutput(result=result, regions=regions)


# --------------------------------------------------------------------------- regions


def result_regions(result: ExtractionResult) -> dict[str, list[dict[str, Any]]] | None:
    """The pipeline's own regions per field (:mod:`basis.extraction.regions`); ``None`` when
    its Azure DI analysis kept no geometry (no highlight to score)."""
    stored = result.raw.get("azure_di")
    if not isinstance(stored, dict) or not has_geometry(AzureDiResult.model_validate(stored)):
        return None
    return {key: region_json(item.regions) for key, item in result.fields.items()}
