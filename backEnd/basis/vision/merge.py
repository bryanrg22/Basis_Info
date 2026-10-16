"""Merging the outputs of several narrow calls on one photo (EVAL_REPORT section 11).

Task decomposition sends the same photo once per job (``photo_countables``: find and count
the countable objects; ``photo_surfaces``: name the surfaces and their materials). This is
the one implementation of the merge that puts the answers back together: the pipeline
(:func:`basis.vision.analyze.analyze_photo_decomposed`) and the evaluation harness
(``evals.merge_photo_runs``) both call :func:`merge_components`, so what the harness
scored is what the pipeline runs.

The rule, per photo:

* Components are the union by label. A label two calls both list keeps the material,
  attachment and note of the call that listed it first (the countables call in the
  pipeline) and the largest count and confidence. ``other`` entries are kept from every
  call: they are distinct unlisted items (and excluded from scoring anyway).
* The room type, its confidence, the floor and the ordinal come from one designated call
  (the surfaces call: section 11 found it answers the room question better).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from basis.vision.schemas import (
    MAX_COMPONENTS,
    MAX_DESCRIPTION_CHARS,
    Component,
    ComponentLabel,
    ModelPhotoAnalysis,
    PhotoAnalysis,
)


def merge_components(parts: Sequence[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Union by label; the first part that lists a label gives its material, attachment and
    note, the count and confidence are the largest; ``other`` entries are kept from every
    part (they are distinct unlisted items, excluded from scoring anyway).

    Works on plain dicts (``Component.model_dump``) so the harness can merge stored runs
    and the pipeline can merge live answers with the same code."""
    merged: dict[str, dict[str, Any]] = {}
    others: list[dict[str, Any]] = []
    for components in parts:
        for component in components:
            label = component["label"]
            if label == ComponentLabel.OTHER.value:
                others.append(component)
            elif label in merged:
                merged[label]["count"] = max(int(merged[label]["count"]), int(component["count"]))
                merged[label]["confidence"] = max(
                    float(merged[label]["confidence"]), float(component["confidence"])
                )
            else:
                merged[label] = dict(component)
    return [*merged.values(), *others]


@dataclass(frozen=True, slots=True)
class MergedAnalysis:
    analysis: PhotoAnalysis
    # Components the schema's cap forced out (``other`` entries go first, as they are last
    # in the merged order). Zero in practice: the two vocabularies together hold fewer
    # labels than the cap; only a model that repeats ``other`` many times can reach it.
    dropped: int


def merge_analyses(countables: ModelPhotoAnalysis, surfaces: ModelPhotoAnalysis) -> MergedAnalysis:
    """One :class:`PhotoAnalysis` from the two decomposed answers on one photo.

    Components: :func:`merge_components` with the countables answer first, so a label both
    list keeps the countables call's material and attachment. Room type, confidence, floor
    and ordinal: the surfaces answer. Description: both sentences, the countables one first,
    cut to the schema's length.
    """
    merged = merge_components(
        [
            [c.model_dump(mode="json") for c in countables.components],
            [c.model_dump(mode="json") for c in surfaces.components],
        ]
    )
    kept = merged[:MAX_COMPONENTS]
    sentences = [s.strip() for s in (countables.description, surfaces.description) if s.strip()]
    analysis = PhotoAnalysis(
        room_type=surfaces.room_type,
        room_confidence=surfaces.room_confidence,
        floor=surfaces.floor,
        room_ordinal=surfaces.room_ordinal,
        components=[Component.model_validate(c) for c in kept],
        description=" ".join(sentences)[:MAX_DESCRIPTION_CHARS],
    )
    return MergedAnalysis(analysis=analysis, dropped=len(merged) - len(kept))
