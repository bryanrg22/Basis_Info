"""Alternative appraisal-extraction systems for the method/model sweep (``run.py --system``).

Every system reads the PDF (and the document's recorded analyses) and returns the production
``ExtractionResult`` plus per-field regions (:mod:`.base`). Designs, one module each:

=====================  ============================================================
``production``         the ``extract_fields`` activity's path as configured: the grounded
                       reading on ``EXTRACTION_GROUNDED_MODEL`` by default, ``--model``
                       to choose another (:mod:`.production`)
``pipeline``           the pipeline method before section 33, the regression baseline's
                       system (:mod:`.production`)
``azure_1004``         ``prebuilt-mortgage.us.1004`` mapped to the catalogue (:mod:`.mortgage`)
``production_plus_1004``  the pipeline merged with the 1004 values (:mod:`.mortgage`)
``checkbox_context``   the pipeline, the fallback given page content with selection marks
                       and asked every checkbox key (:mod:`.production`)
``llm_all``            one grounded call for every key, page text with marks (:mod:`.grounded`)
``llm_pages``          ``llm_all`` plus form-page images (:mod:`.grounded`)
``llm_sections``       one grounded call per section group, in parallel (:mod:`.grounded`)
``validate_reask``     validators on a base system, implicated keys re-asked (:mod:`.combine`)
``vote``               two systems or models, agreement kept, disagreement flagged (:mod:`.combine`)
``mismo_xml``          the UAD 3.6 XML beside the PDF, located on the PDF (:mod:`.mismo`)
=====================  ============================================================
"""

from __future__ import annotations

from typing import Final

from evals.appraisal.systems.base import (
    ModelChoice,
    System,
    SystemEnv,
    SystemOutput,
    SystemSpec,
    VoterSpec,
)
from evals.appraisal.systems.combine import ValidateReask, Vote
from evals.appraisal.systems.grounded import llm_all, llm_pages, llm_sections
from evals.appraisal.systems.mismo import MismoXml
from evals.appraisal.systems.mortgage import Azure1004, ProductionPlus1004
from evals.appraisal.systems.production import CheckboxContext, Production, pipeline

SYSTEMS: Final = (
    "production",
    "pipeline",
    "azure_1004",
    "production_plus_1004",
    "checkbox_context",
    "llm_all",
    "llm_pages",
    "llm_sections",
    "validate_reask",
    "vote",
    "mismo_xml",
)
# Systems whose answer comes from a model named by --model (the others use TEXT_MODEL or none).
MODEL_SYSTEMS: Final = frozenset({"llm_all", "llm_pages", "llm_sections", "validate_reask"})
MORTGAGE_SYSTEMS: Final = frozenset({"azure_1004", "production_plus_1004"})
# Systems that call no model at all.
NO_MODEL_SYSTEMS: Final = frozenset({"azure_1004", "mismo_xml"})
# Systems that run the production pipeline, whose replay uses the recorded context.json.
PRODUCTION_SYSTEMS: Final = frozenset(
    {"production", "pipeline", "checkbox_context", "production_plus_1004"}
)

__all__ = [
    "MODEL_SYSTEMS",
    "NO_MODEL_SYSTEMS",
    "SYSTEMS",
    "ModelChoice",
    "System",
    "SystemEnv",
    "SystemOutput",
    "SystemSpec",
    "VoterSpec",
    "build_system",
    "needs_mortgage",
    "uses_production_settings",
]


def _simple(name: str, spec: SystemSpec, model: ModelChoice) -> System:
    if name == "production":
        return Production(model=model if model.model else None)
    if name == "pipeline":
        return pipeline()
    if name == "azure_1004":
        return Azure1004()
    if name == "mismo_xml":
        return MismoXml()
    if name == "production_plus_1004":
        return ProductionPlus1004()
    if name == "checkbox_context":
        return CheckboxContext(model)
    if name == "llm_all":
        return llm_all(model, dpi=spec.dpi, min_page_words=spec.min_page_words)
    if name == "llm_pages":
        return llm_pages(model, dpi=spec.dpi, min_page_words=spec.min_page_words)
    if name == "llm_sections":
        return llm_sections(
            model, dpi=spec.dpi, min_page_words=spec.min_page_words, with_images=spec.with_images
        )
    raise ValueError(f"unknown system {name!r} (choose from {', '.join(SYSTEMS)})")


def build_system(spec: SystemSpec) -> System:
    """The system ``spec`` names, its base or voters built the same way."""
    if spec.name == "validate_reask":
        if not spec.base or spec.base in ("validate_reask", "vote"):
            raise ValueError("validate_reask needs --base, a system other than itself or vote")
        if not spec.model.model:
            raise ValueError("validate_reask needs --model (the re-ask model)")
        base = _simple(spec.base, spec, spec.base_model or spec.model)
        return ValidateReask(base, spec.model, dpi=spec.dpi)
    if spec.name == "vote":
        if len(spec.voters) != 2:
            raise ValueError("vote needs --voters with exactly two system@model entries")
        voters = [_simple(v.system, spec, v.model) for v in spec.voters]
        labels = [f"{v.system}@{v.model.model or 'text_model'}" for v in spec.voters]
        return Vote(voters, labels)
    return _simple(spec.name, spec, spec.model)


def _names(spec: SystemSpec) -> set[str]:
    return {spec.name, *([spec.base] if spec.base else []), *(v.system for v in spec.voters)}


def needs_mortgage(spec: SystemSpec) -> bool:
    return bool(_names(spec) & MORTGAGE_SYSTEMS)


def uses_production_settings(spec: SystemSpec) -> bool:
    return bool(_names(spec) & PRODUCTION_SYSTEMS)
