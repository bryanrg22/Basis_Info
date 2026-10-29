"""Grounded model extraction for the sweep: ``llm_all``, ``llm_pages``, ``llm_sections``.

The method is production's (:mod:`basis.extraction.grounded`: the request, the mechanical
checks, the presence rules, the regions; ``EXTRACTION_METHOD=grounded`` runs ``llm_all``);
this module adapts it to a sweep system (:class:`~evals.appraisal.systems.base.SystemEnv` in,
:class:`~evals.appraisal.systems.base.SystemOutput` out) and re-exports its names for the
other systems (``validate_reask`` builds its re-ask with them).

* ``llm_all``: every page's text in one call.
* ``llm_pages``: the same plus page images (pymupdf at ``--dpi``, default 150, PNG) of the
  form pages only: a page with fewer than ``--min-page-words`` Azure DI words, or mostly
  covered by figures, is a photo/sketch/map page and gets no image (its text is still sent).
  Vision models only.
* ``llm_sections``: one call per form section group (:data:`SECTION_GROUPS`), calls in
  parallel, each with every page's text (routing a group to the pages of its headings lost
  page 1 on most synthetic 1004s, where Azure DI reads the vertical section headings as
  single letters); ``--with-images`` adds the images of the form pages.

The result is closed the sweep's way (:func:`~evals.appraisal.systems.base.finish`: the
per-field and cross-field validation and the review gate), with no derived site-area fields:
what EVAL_REPORT section 33.5 measured. Production adds those (:mod:`basis.extraction.pipeline`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from basis.extraction import grounded as method
from basis.extraction.grounded import (
    FIGURE_PAGE_FRACTION,
    FIGURE_PAGE_MAX_WORDS,
    PROMPT,
    SECTION_GROUPS,
    UAD36_NOTE,
    CallResult,
    Checked,
    GroundedAnswer,
    GroundedModel,
    GroundedValue,
    NoProvider,
    Plan,
    check_answer,
    field_list,
    form_pages,
    is_form_page,
    page_block,
    quote_region,
    render_pages,
    request_content,
    summary,
)
from basis.llm.call import CallContext
from basis.llm.provider import ChatProvider, ChatRequest, ImagePart
from evals.appraisal.systems.base import (
    ASKED_KEYS,
    MissingInput,
    ModelChoice,
    SystemEnv,
    SystemOutput,
    finish,
)
from evals.appraisal.systems.layout import Layout

ACTIVITY: Final = "extract_fields"

__all__ = [
    "ACTIVITY",
    "FIGURE_PAGE_FRACTION",
    "FIGURE_PAGE_MAX_WORDS",
    "PROMPT",
    "SECTION_GROUPS",
    "UAD36_NOTE",
    "CallResult",
    "Checked",
    "Grounded",
    "GroundedAnswer",
    "GroundedValue",
    "Plan",
    "ask",
    "build_request",
    "check_answer",
    "field_list",
    "form_pages",
    "grounded_model",
    "is_form_page",
    "llm_all",
    "llm_pages",
    "llm_sections",
    "page_block",
    "quote_region",
    "render_pages",
    "request_content",
    "summary",
]


def grounded_model(model: ModelChoice) -> GroundedModel:
    """``--model`` / ``--effort`` / ``--max-tokens`` as the production model choice."""
    if not model.model:
        raise MissingInput("this system needs --model")
    return GroundedModel(model.model, model.effort, model.max_tokens)


def build_request(
    keys: Sequence[str],
    context: str,
    *,
    prompt_name: str,
    model: ModelChoice,
    call: CallContext,
    images: Sequence[ImagePart] = (),
    image_pages: Sequence[int] = (),
    note: str = "",
    uad36: bool = False,
) -> ChatRequest:
    """:func:`basis.extraction.grounded.build_request` with the sweep's model choice."""
    return method.build_request(
        keys,
        context,
        prompt_name=prompt_name,
        model=grounded_model(model),
        call=call,
        images=images,
        image_pages=image_pages,
        note=note,
        uad36=uad36,
    )


async def ask(provider: ChatProvider | None, request: ChatRequest, call: CallContext) -> CallResult:
    """:func:`basis.extraction.grounded.ask`; no provider is a :class:`MissingInput`."""
    try:
        return await method.ask(provider, request, call)
    except NoProvider as exc:
        raise MissingInput("this system needs a model provider") from exc


class Grounded:
    """``llm_all`` (no images, one call), ``llm_pages`` (images, one call), ``llm_sections``
    (one call per section group, images optional)."""

    def __init__(
        self,
        name: str,
        model: ModelChoice,
        *,
        images: bool,
        sections: bool,
        dpi: int,
        min_page_words: int,
    ) -> None:
        self.name = name
        self.model = model
        self.images = images
        self.sections = sections
        self.dpi = dpi
        self.min_page_words = min_page_words

    def plans(self, geometry: Layout, asked: Sequence[str] = ASKED_KEYS) -> list[Plan]:
        return method.plan_calls(
            geometry,
            asked,
            images=self.images,
            sections=self.sections,
            min_page_words=self.min_page_words,
        )

    async def run(self, env: SystemEnv) -> SystemOutput:
        geometry = await env.sources.geometry(env.pdf)
        try:
            reading = await method.read_fields(
                geometry,
                provider=env.provider,
                call=env.call,
                model=grounded_model(self.model),
                pdf=env.pdf,
                images=self.images,
                sections=self.sections,
                dpi=self.dpi,
                min_page_words=self.min_page_words,
            )
        except NoProvider as exc:
            raise MissingInput("this system needs a model provider") from exc
        checked = reading.checked
        result = finish(
            doc_id=env.doc_id,
            method=self.name,
            page_count=len(geometry.pages),
            fields=checked.fields,
            review_threshold=env.extraction.review_threshold,
            fallback=reading.summary(),
        )
        return SystemOutput(
            result=result,
            regions=checked.regions,
            origins={key: "llm" for key in checked.fields},
            quotes=checked.quotes,
        )


def llm_all(model: ModelChoice, *, dpi: int, min_page_words: int) -> Grounded:
    return Grounded(
        "llm_all", model, images=False, sections=False, dpi=dpi, min_page_words=min_page_words
    )


def llm_pages(model: ModelChoice, *, dpi: int, min_page_words: int) -> Grounded:
    return Grounded(
        "llm_pages", model, images=True, sections=False, dpi=dpi, min_page_words=min_page_words
    )


def llm_sections(
    model: ModelChoice, *, dpi: int, min_page_words: int, with_images: bool
) -> Grounded:
    return Grounded(
        "llm_sections",
        model,
        images=with_images,
        sections=True,
        dpi=dpi,
        min_page_words=min_page_words,
    )
