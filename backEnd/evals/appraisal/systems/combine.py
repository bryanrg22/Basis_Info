"""Systems built on other systems: ``validate_reask`` and ``vote``.

**validate_reask** (``--base NAME``): run the base system, run the validators
(:mod:`evals.appraisal.systems.validators`) and report their findings as
``ExtractionResult.issues``; then re-ask **only the keys the validator issues name**, once,
in one grounded call (prompt ``appraisal_fields_reask``, the same mechanical checks as
``llm_all``) with the text and images of the pages those keys were read on (the pages of
their sections when a key has no page). A re-read that passes the checks and differs from
the base value replaces it (``llm`` at 0.6, origin ``llm_reask``); one equal to the base
value keeps the base field. Then validation runs again: an issue that survives the re-ask
stays an issue (the document really is inconsistent, e.g. a cost total misprinted by the
appraiser). Keys no validator named are never re-asked, and production's own issues
(merge disagreements, Azure DI refusals) are carried over.

**vote** (``--voters a,b``; ``system@model[@effort]`` each): run the voters in parallel on
the same analyses; per key, agreement (:func:`~evals.appraisal.systems.base.same_value`)
keeps the first voter's field at confidence 0.95 (two independent readers, a documented
exception to the 0.6 model cap: the spec's rule); disagreement keeps the higher-ranked
(first) voter's value at 0.5 with a ``voters_disagree:<key>`` review reason. A key only one
voter read is a disagreement too (about whether the box holds a value): its value is kept,
at 0.5, flagged, since dropping it would turn a reviewable value into a silent miss.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, Final

from basis.extraction.fallback import context_pages
from basis.extraction.results import ExtractedField, ValidationIssue
from evals.appraisal.systems.base import (
    ASKED_KEYS,
    ModelChoice,
    System,
    SystemEnv,
    SystemOutput,
    finish,
    own_issues,
    same_value,
)
from evals.appraisal.systems.grounded import (
    Checked,
    ask,
    build_request,
    check_answer,
    page_block,
    render_pages,
)
from evals.appraisal.systems.marks import labelled_texts
from evals.appraisal.systems.validators import VALIDATOR_RULES, validate

REASK_PROMPT: Final = "appraisal_fields_reask"
AGREE_CONFIDENCE: Final = 0.95
DISAGREE_CONFIDENCE: Final = 0.5


def implicated(issues: Sequence[ValidationIssue]) -> list[str]:
    """The catalogue keys the validator issues name, in catalogue order."""
    named = {k for issue in issues if issue.rule in VALIDATOR_RULES for k in issue.keys}
    return [key for key in ASKED_KEYS if key in named]


def reask_pages(
    keys: Sequence[str], fields: Mapping[str, ExtractedField], lines: Sequence[str]
) -> list[int]:
    """The pages the keys were read on; the pages of their sections for a key without one."""
    pages = {fields[k].page for k in keys if k in fields and fields[k].page is not None}
    unplaced = [k for k in keys if k not in fields or fields[k].page is None]
    if unplaced:
        pages.update(context_pages(unplaced, lines))
    return sorted(p for p in pages if p is not None)


class ValidateReask:
    name = "validate_reask"

    def __init__(self, base: System, model: ModelChoice, *, dpi: int) -> None:
        self.base = base
        self.model = model
        self.dpi = dpi

    async def run(self, env: SystemEnv) -> SystemOutput:
        base = await self.base.run(env)
        geometry = await env.sources.geometry(env.pdf)
        azure = await env.sources.layout(env.pdf)
        texts = labelled_texts(geometry)
        lines = azure.page_texts() if azure is not None else texts
        carried = own_issues(base.result)
        found = validate(base.result.fields, lines)
        keys = implicated(found)
        fields = dict(base.result.fields)
        regions = dict(base.regions or {}) if base.regions is not None else None
        origins = {k: base.origin(k) for k in fields}
        quotes = dict(base.quotes)
        reask: dict[str, Any] = {"asked": keys, "changed": [], "issues_before": _rules(found)}
        if keys:
            pages = reask_pages(keys, fields, lines)
            context, sent = page_block(texts, pages)
            request = build_request(
                keys,
                context,
                prompt_name=REASK_PROMPT,
                model=self.model,
                call=env.call,
                images=render_pages(env.pdf, sent, self.dpi),
                image_pages=sent,
                note="Checks that failed: " + "; ".join(sorted({i.rule for i in found})) + ".\n\n",
            )
            result = await ask(env.provider, request, env.call)
            reask["status"] = result.status
            reask["pages"] = sent
            checked = Checked()
            if result.answer is not None:
                check_answer(result.answer, keys, texts, sent, geometry, out=checked)
            reask["dropped"] = checked.dropped
            for key, reread in checked.fields.items():
                current = fields.get(key)
                if current is not None and same_value(current, reread):
                    continue
                fields[key] = reread
                origins[key] = "llm_reask"
                quotes[key] = checked.quotes[key]
                if regions is not None:
                    regions[key] = checked.regions.get(key, [])
                reask["changed"].append(key)
        after = validate(fields, lines)
        reask["issues_after"] = _rules(after)
        out = finish(
            doc_id=env.doc_id,
            method=f"{base.result.method}+validate_reask",
            page_count=base.result.page_count,
            fields=fields,
            review_threshold=env.extraction.review_threshold,
            extra_issues=[*carried, *after],
            fallback={**base.result.fallback, "reask": reask},
            notes=base.result.notes,
            raw={k: v for k, v in base.result.raw.items() if k != "issues"},
        )
        out.comparables = base.result.comparables
        return SystemOutput(result=out, regions=regions, origins=origins, quotes=quotes)


def _rules(issues: Sequence[ValidationIssue]) -> list[dict[str, Any]]:
    return [{"rule": i.rule, "keys": list(i.keys)} for i in issues]


# --------------------------------------------------------------------------- vote


def vote_fields(
    outputs: Sequence[SystemOutput],
) -> tuple[dict[str, ExtractedField], dict[str, int], list[str], dict[str, str]]:
    """Merged fields, which voter each came from, the disagreeing keys, and origins."""
    first, second = outputs[0].result.fields, outputs[1].result.fields
    fields: dict[str, ExtractedField] = {}
    chosen: dict[str, int] = {}
    origins: dict[str, str] = {}
    disagree: list[str] = []
    for key in [k for k in ASKED_KEYS if k in first or k in second] + [
        k for k in {*first, *second} if k not in ASKED_KEYS
    ]:
        a, b = first.get(key), second.get(key)
        if a is not None and a.is_empty:
            a = None
        if b is not None and b.is_empty:
            b = None
        if a is None and b is None:
            continue
        if a is not None and b is not None and same_value(a, b):
            fields[key] = a.model_copy(update={"confidence": AGREE_CONFIDENCE})
            chosen[key], origins[key] = 0, "vote_agree"
            continue
        index = 0 if a is not None else 1
        pick = a if a is not None else b
        assert pick is not None
        fields[key] = pick.model_copy(update={"confidence": DISAGREE_CONFIDENCE})
        chosen[key], origins[key] = index, f"vote_disagree_{index + 1}"
        disagree.append(key)
    return fields, chosen, disagree, origins


class Vote:
    name = "vote"

    def __init__(self, voters: Sequence[System], labels: Sequence[str]) -> None:
        if len(voters) != 2:
            raise ValueError("vote takes exactly two voters")
        self.voters = list(voters)
        self.labels = list(labels)

    async def run(self, env: SystemEnv) -> SystemOutput:
        outputs = await asyncio.gather(*(voter.run(env) for voter in self.voters))
        fields, chosen, disagree, origins = vote_fields(outputs)
        first = outputs[0].result
        regions: dict[str, list[dict[str, Any]]] | None = None
        if any(o.regions is not None for o in outputs):
            regions = {k: (outputs[i].regions or {}).get(k, []) for k, i in chosen.items()}
        quotes = {k: q for k, i in chosen.items() if (q := outputs[i].quotes.get(k))}
        result = finish(
            doc_id=env.doc_id,
            method="vote:" + ",".join(self.labels),
            page_count=first.page_count,
            fields=fields,
            review_threshold=env.extraction.review_threshold,
            extra_issues=own_issues(first),
            extra_reasons=[f"voters_disagree:{key}" for key in disagree],
            fallback={
                "status": "vote",
                "asked": [],
                "filled": sorted(fields),
                "dropped": {},
                "voters": self.labels,
                "disagree": disagree,
                "voter_fallbacks": [o.result.fallback for o in outputs],
            },
            notes=[n for o in outputs for n in o.result.notes],
        )
        result.comparables = first.comparables
        return SystemOutput(result=result, regions=regions, origins=origins, quotes=quotes)
