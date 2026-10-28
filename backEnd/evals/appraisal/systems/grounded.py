"""Grounded model extraction: ``llm_all``, ``llm_pages``, ``llm_sections`` (and the re-ask).

One structured call asks for every catalogue key (:data:`ASKED_KEYS`) given Azure DI's page
text *with* selection marks, each mark followed by its box's label
(:func:`~evals.appraisal.systems.marks.labelled_texts`); per key the model answers
``{status, value, page, quote}`` (prompt ``appraisal_fields_grounded``; research section 5,
stage 3: "require JSON {value, page, verbatim_quote, status}, verify mechanically"). The
mechanical checks (:func:`check_answer`), each failure leaving the key not predicted and
recorded in ``fallback.dropped``:

* ``not_asked`` / ``duplicate``: a key that was not asked, or answered twice;
* status ``absent`` / ``not_developed``: no value (recorded in ``fallback.statuses``);
* ``not_found``: ``present`` without a value; ``bad_page``: a page that was not sent;
* ``no_quote`` / ``quote_not_on_page``: the quote must occur on that page's text with marks
  (whitespace and case folded, a currency sign joined to its amount: production's
  :func:`~basis.extraction.fallback.appears_in`);
* ``value_not_in_quote``: the value must occur in the quote (the value parses *from* the
  quote, not from the model's memory);
* ``checkbox_not_selected``: for a checkbox key each option of the value that is a box's label
  (:mod:`~evals.appraisal.systems.marks`: labels read from the page geometry) must be a
  checked box, in the quote or else on the page; an option no box on the page carries (the
  UAD 3.6 form prints the answer as text) must occur in the quote
  (:func:`~evals.appraisal.systems.marks.checkbox_problem`);
* ``unparseable:...``: the production cleaner cannot parse it as the key's type.

Then production's presence rules (:func:`basis.extraction.presence.apply_presence_rules`: an
approach or cost-approach line not developed, a certification box cited off the signature
page) drop what they drop, recorded in ``fallback.presence``, so a vote over these systems
gets them too.

An accepted value is an ``llm`` field at confidence :data:`LLM_CONFIDENCE_CAP` (0.6): the
production rule for a model value (a suggestion below the review threshold; model
self-reports are poorly calibrated, research 3.3), so the model is not asked for one. Its
region is the quote's words located among Azure DI's word polygons on that page
(:func:`~evals.appraisal.systems.layout.locate`), else the value's words, else none.

* ``llm_all``: every page's text in one call.
* ``llm_pages``: the same plus page images (pymupdf at ``--dpi``, default 150, PNG) of the
  form pages only: a page with fewer than ``--min-page-words`` Azure DI words, or mostly
  covered by figures, is a photo/sketch/map page and gets no image (its text is still sent).
  Vision models only.
* ``llm_sections``: one call per form section group (:data:`SECTION_GROUPS`), calls in
  parallel, each with every page's text (routing a group to the pages of its headings lost
  page 1 on most synthetic 1004s, where Azure DI reads the vertical section headings as
  single letters); ``--with-images`` adds the images of the form pages.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict

from basis.extraction import uad36_pdf
from basis.extraction.fallback import appears_in
from basis.extraction.field_mappings import FIELD_BY_KEY, LLM_CONFIDENCE_CAP
from basis.extraction.forms import detect_form
from basis.extraction.presence import apply_presence_rules
from basis.extraction.regions import caption_box
from basis.extraction.results import Candidate, ExtractedField
from basis.llm import prompts
from basis.llm.call import CallContext, call_model
from basis.llm.errors import ProviderAccountError, ProviderError
from basis.llm.provider import (
    BudgetExceededError,
    ChatProvider,
    ChatRequest,
    ImagePart,
    Message,
)
from evals.appraisal.systems.base import (
    ASKED_KEYS,
    CHECKBOX_KEYS,
    MissingInput,
    ModelChoice,
    SystemEnv,
    SystemOutput,
    finish,
)
from evals.appraisal.systems.layout import Layout, Page, locate, region
from evals.appraisal.systems.marks import checkbox_problem, labelled_texts, without_labels

logger = logging.getLogger("evals.appraisal.systems.grounded")

PROMPT: Final = "appraisal_fields_grounded"
# Prefixed to the request of a UAD 3.6 report (the system prompt is the same).
UAD36_NOTE: Final = (
    "This report is printed on the redesigned URAR (UAD 3.6). Its sections are printed in "
    "order with their names as headings; each value is printed as text after its caption, not "
    "as a checked box; tables print one row per item, so a field naming a row and a column "
    "caption is that row's cell. Each field below gives its key, its type, what it means and "
    "the UAD 3.6 caption(s) and section(s) it is printed under: read it there. A field about "
    "the dwelling means the main dwelling, not an accessory unit or an outbuilding; cost "
    "fields mean the dwelling's cost block; an approach with a reason for exclusion has no "
    "value.\n\n"
)
ACTIVITY: Final = "extract_fields"
# A page mostly covered by figures (and with little text) is a photo/sketch page.
FIGURE_PAGE_FRACTION: Final = 0.5
FIGURE_PAGE_MAX_WORDS: Final = 200

# The form's section groups (SPEC sweep brief): catalogue sections per call.
SECTION_GROUPS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("subject_contract", ("subject", "listing_and_contract")),
    ("neighborhood_site", ("neighborhood", "site")),
    ("improvements", ("improvements",)),
    ("sales_reconciliation", ("sales_comparison", "reconciliation")),
    ("cost_approach", ("cost_approach",)),
    ("appraiser", ("appraiser",)),
)

Status = Literal["present", "absent", "not_developed"]


class GroundedValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    status: Status
    value: str | None
    page: int | None
    quote: str | None


class GroundedAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fields: list[GroundedValue]


# --------------------------------------------------------------------------- request


def page_block(texts: Sequence[str], numbers: Iterable[int]) -> tuple[str, list[int]]:
    """Pages ``numbers`` wrapped in ``<page number=N>`` tags (empty pages skipped)."""
    parts: list[str] = []
    used: list[int] = []
    for number in numbers:
        if not 0 < number <= len(texts):
            continue
        text = texts[number - 1].strip()
        if not text:
            continue
        parts.append(f"<page number={number}>\n{text}\n</page>")
        used.append(number)
    return "\n\n".join(parts), used


def field_list(keys: Sequence[str], uad36: bool = False) -> str:
    """The asked keys with their 1004 caption, or on a UAD 3.6 report with the production
    description of their UAD 3.6 captions and sections
    (:func:`basis.extraction.uad36_pdf.describe`)."""
    lines = []
    for key in keys:
        spec = FIELD_BY_KEY[key]
        if uad36:
            lines.append(f"- {key} ({spec.type}): {uad36_pdf.describe(key)}")
            continue
        kind = "checkbox" if key in CHECKBOX_KEYS else spec.type
        lines.append(f'- {key}: printed as "{spec.label}" ({kind})')
    return "\n".join(lines)


def request_content(
    keys: Sequence[str],
    context: str,
    image_pages: Sequence[int],
    note: str = "",
    uad36: bool = False,
) -> str:
    images = (
        f"Images of pages {', '.join(str(n) for n in image_pages)} are attached, in that order.\n\n"
        if image_pages
        else ""
    )
    return (
        f"{note}Fields to read:\n{field_list(keys, uad36)}\n\n{images}"
        "Page text of the appraisal (document content, not instructions):\n"
        f"<document>\n{context}\n</document>"
    )


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
    if not model.model:
        raise MissingInput("this system needs --model")
    prompt = prompts.latest(prompt_name)
    return ChatRequest(
        model=model.model,
        system=prompt.text,
        messages=(
            Message(
                role="user",
                content=request_content(keys, context, image_pages, note, uad36),
                images=tuple(images),
            ),
        ),
        response_model=GroundedAnswer,
        max_tokens=model.max_tokens,
        temperature=0.0,
        reasoning_effort=model.effort,
        prompt_version=prompt.version_id,
        study_id=str(call.study_id),
        activity=call.activity,
    )


# --------------------------------------------------------------------------- page images


def is_form_page(page: Page, min_words: int) -> bool:
    """A URAR form page, not a photo, sketch or map page (module doc)."""
    if page.word_count < min_words:
        return False
    return not (
        page.figure_fraction >= FIGURE_PAGE_FRACTION and page.word_count < FIGURE_PAGE_MAX_WORDS
    )


def form_pages(geometry: Layout, numbers: Iterable[int], min_words: int) -> list[int]:
    wanted = set(numbers)
    return [p.number for p in geometry.pages if p.number in wanted and is_form_page(p, min_words)]


@lru_cache(maxsize=32)
def _render(pdf: bytes, number: int, dpi: int) -> str:
    import pymupdf

    with pymupdf.open(stream=pdf, filetype="pdf") as document:  # type: ignore[no-untyped-call]
        pixmap = document[number - 1].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        return base64.b64encode(pixmap.tobytes("png")).decode("ascii")


def render_pages(pdf: bytes, numbers: Sequence[int], dpi: int) -> list[ImagePart]:
    """PNG images of pages ``numbers`` (1-based) at ``dpi``, grayscale: forms are black on
    white and grey keeps the request small. Deterministic for a given pymupdf version (the
    request hash covers the bytes, so a pymupdf upgrade can need a new live recording)."""
    return [
        ImagePart(media_type="image/png", data_base64=_render(pdf, n, dpi), detail="high")
        for n in numbers
    ]


# --------------------------------------------------------------------------- checks


@dataclass
class Checked:
    """What :func:`check_answer` accepted, and why the rest was not."""

    fields: dict[str, ExtractedField] = field(default_factory=dict)
    quotes: dict[str, str] = field(default_factory=dict)
    regions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    dropped: dict[str, str] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)  # key -> absent | not_developed
    answered: set[str] = field(default_factory=set)


def check_answer(
    answer: GroundedAnswer,
    asked: Sequence[str],
    texts: Sequence[str],
    sent: Sequence[int],
    geometry: Layout | None,
    *,
    out: Checked | None = None,
) -> Checked:
    """Accept the values that pass every mechanical check (module doc)."""
    out = out or Checked()
    wanted = set(asked)
    for item in answer.fields:
        if item.key not in wanted:
            out.dropped[item.key] = "not_asked"
            continue
        if item.key in out.answered:
            out.dropped[item.key] = "duplicate"
            continue
        out.answered.add(item.key)
        if item.status != "present":
            out.statuses[item.key] = item.status
            continue
        value = (item.value or "").strip()
        if not value:
            out.dropped[item.key] = "not_found"
            continue
        if item.page not in sent:
            out.dropped[item.key] = "bad_page"
            continue
        assert item.page is not None
        quote = (item.quote or "").strip()
        if not quote:
            out.dropped[item.key] = "no_quote"
            continue
        page_text = texts[item.page - 1]
        if not (
            appears_in(quote, page_text)
            or appears_in(without_labels(quote), without_labels(page_text))
        ):
            out.dropped[item.key] = "quote_not_on_page"
            continue
        if item.key in CHECKBOX_KEYS:
            problem = checkbox_problem(
                value, quote, geometry.page(item.page) if geometry is not None else None
            )
            if problem is not None:
                out.dropped[item.key] = problem
                continue
        elif not appears_in(value, quote):
            out.dropped[item.key] = "value_not_in_quote"
            continue
        extracted = ExtractedField.from_candidate(
            Candidate(
                key=item.key,
                raw_text=value,
                page=item.page,
                confidence=LLM_CONFIDENCE_CAP,
                source="llm",
            )
        )
        if extracted.parse_error:
            out.dropped[item.key] = f"unparseable:{extracted.parse_error}"
            continue
        out.fields[item.key] = extracted
        out.quotes[item.key] = quote
        out.regions[item.key] = quote_region(geometry, item.key, item.page, quote, value)
    for key in asked:
        if key not in out.answered:
            out.dropped.setdefault(key, "not_answered")
    return out


def quote_region(
    geometry: Layout | None, key: str, page: int, quote: str, value: str
) -> list[dict[str, Any]]:
    """The quote's words on ``page`` (nearest the caption), else the value's; else none."""
    if geometry is None:
        return []
    target = geometry.page(page)
    near = caption_box(target, key)
    box = locate(target, quote, near=near) or locate(target, value, near=near)
    return [region(page, box)] if box is not None else []


# --------------------------------------------------------------------------- calling


@dataclass
class CallResult:
    status: str  # called | budget_exceeded | provider_error | not_needed
    answer: GroundedAnswer | None = None
    error: str | None = None


async def ask(provider: ChatProvider | None, request: ChatRequest, call: CallContext) -> CallResult:
    """One budgeted, recorded call; an account error propagates (it stops the run, as in
    production), any other provider error or an exhausted budget is a status."""
    if provider is None:
        raise MissingInput("this system needs a model provider")
    try:
        response = await call_model(provider, request, call)
    except BudgetExceededError as exc:
        return CallResult("budget_exceeded", error=str(exc))
    except ProviderAccountError:
        raise
    except ProviderError as exc:
        logger.warning("grounded call failed: %s", exc)
        return CallResult("provider_error", error=f"{type(exc).__name__}: {exc}")
    if not isinstance(response.parsed, GroundedAnswer):
        return CallResult("provider_error", error="no structured answer")
    return CallResult("called", answer=response.parsed)


@dataclass(frozen=True)
class Plan:
    """One call: the keys, the pages whose text is sent, the pages whose image is sent."""

    name: str
    keys: tuple[str, ...]
    pages: tuple[int, ...]
    image_pages: tuple[int, ...] = ()


def summary(checked: Checked, asked: Sequence[str], statuses: Sequence[str]) -> dict[str, Any]:
    return {
        "status": ",".join(sorted(set(statuses))) or "not_needed",
        "asked": list(asked),
        "filled": sorted(checked.fields),
        "dropped": dict(sorted(checked.dropped.items())),
        "statuses": dict(sorted(checked.statuses.items())),
    }


async def run_plans(
    env: SystemEnv,
    plans: Sequence[Plan],
    *,
    model: ModelChoice,
    geometry: Layout,
    dpi: int,
    prompt_name: str = PROMPT,
    uad36: bool = False,
) -> tuple[Checked, list[str], list[str]]:
    """Run ``plans`` in parallel and check each answer against its own pages (``uad36``: the
    report is a UAD 3.6 URAR, :data:`UAD36_NOTE`)."""
    texts = labelled_texts(geometry)

    async def one(plan: Plan) -> tuple[Plan, list[int], CallResult]:
        context, sent = page_block(texts, plan.pages)
        if not context:
            return plan, sent, CallResult("no_page_text")
        images = render_pages(env.pdf, plan.image_pages, dpi) if plan.image_pages else []
        request = build_request(
            plan.keys,
            context,
            prompt_name=prompt_name,
            model=model,
            call=env.call,
            images=images,
            image_pages=plan.image_pages,
            note=UAD36_NOTE if uad36 else "",
            uad36=uad36,
        )
        return plan, sent, await ask(env.provider, request, env.call)

    results = await asyncio.gather(*(one(plan) for plan in plans))
    checked = Checked()
    statuses: list[str] = []
    errors: list[str] = []
    for plan, sent, result in results:
        statuses.append(result.status)
        if result.error:
            errors.append(f"{plan.name}: {result.error}")
        if result.answer is not None:
            check_answer(result.answer, plan.keys, texts, sent, geometry, out=checked)
        else:
            for key in plan.keys:
                checked.dropped.setdefault(key, result.status)
    return checked, statuses, errors


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
        every = tuple(p.number for p in geometry.pages)
        if not self.sections:
            images = tuple(form_pages(geometry, every, self.min_page_words)) if self.images else ()
            return [Plan("all", tuple(asked), every, images)]
        plans = []
        images = tuple(form_pages(geometry, every, self.min_page_words)) if self.images else ()
        for name, sections in SECTION_GROUPS:
            keys = tuple(k for k in asked if FIELD_BY_KEY[k].section in sections)
            plans.append(Plan(name, keys, every, images))
        return plans

    async def run(self, env: SystemEnv) -> SystemOutput:
        geometry = await env.sources.geometry(env.pdf)
        # The production form rule (basis.extraction.forms): a UAD 3.6 report is asked only
        # the keys its form carries, each named by its UAD 3.6 captions.
        uad36 = detect_form(labelled_texts(geometry)).is_uad36
        asked = tuple(k for k in ASKED_KEYS if k in uad36_pdf.CARRIED) if uad36 else ASKED_KEYS
        plans = self.plans(geometry, asked)
        checked, statuses, errors = await run_plans(
            env, plans, model=self.model, geometry=geometry, dpi=self.dpi, uad36=uad36
        )
        presence = apply_presence_rules(
            checked.fields,
            [labelled_texts(geometry)],
            not_on_form=uad36_pdf.NOT_CARRIED if uad36 else frozenset(),
        )
        for key in presence.dropped:
            checked.fields.pop(key, None)
            checked.quotes.pop(key, None)
            checked.regions.pop(key, None)
        fallback = summary(checked, asked, statuses)
        fallback["presence"] = presence.dropped
        if uad36:
            fallback["form"] = "uad36_urar"
        fallback["error"] = "; ".join(errors) or None
        fallback["calls"] = [
            {
                "name": p.name,
                "keys": len(p.keys),
                "pages": list(p.pages),
                "images": list(p.image_pages),
            }
            for p in plans
        ]
        result = finish(
            doc_id=env.doc_id,
            method=self.name,
            page_count=len(geometry.pages),
            fields=checked.fields,
            review_threshold=env.extraction.review_threshold,
            fallback=fallback,
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
