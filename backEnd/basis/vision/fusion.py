"""Per-class fusion of object counts from several sources (EVAL_REPORT section 15).

A *source* is anything that counts objects in one photo: a vision-language model's answer
(``vlm``), a second model (``vlm2``), SAM 3's instances at a score threshold
(``sam3@0.7``), or the instances a crop-verification pass confirmed. Each gives
``{label: count}``. A :class:`Rule` combines sources for one count class:

* ``pick``: one source's count;
* ``max``: the largest count of the sources (a detector as a floor under the VLM: an
  object either one sees is counted);
* ``min``: the smallest (a detector as a ceiling);
* ``median``: the lower median (three or more sources; :func:`statistics.median_low`).

:func:`fit` chooses, per count class, the candidate rule with the lowest mean absolute
error on labelled photos. That is ensemble selection by validation (Caruana et al., ICML
2004), one choice per class: which counter to trust for windows need not be the one to trust
for outlets. Choosing on the same photos it is then scored on is optimistic, so the
evaluation runner (``evals.fuse_counts``) scores every photo with rules fitted on the
others (leave-one-photo-out cross-validation) and reports that number. Ties go to the
earlier candidate, so listing the plain VLM first makes "no change" the default when
nothing is better.

Count classes are the evaluation's: ``door`` pools ``door``, ``interior_door`` and
``exterior_door`` (:data:`CLASS_LABELS`); every other class is its own label.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

Op = Literal["pick", "max", "min", "median"]
OPS: Final[tuple[str, ...]] = ("pick", "max", "min", "median")

# The labels whose counts add up to one count class (the classes not listed are themselves).
CLASS_LABELS: Final[dict[str, tuple[str, ...]]] = {
    "door": ("door", "interior_door", "exterior_door"),
}

SourceCounts = Mapping[str, Mapping[str, int]]


def class_count(counts: Mapping[str, int], count_class: str) -> int:
    """The count of ``count_class`` in one source's ``{label: count}``."""
    return sum(int(counts.get(label, 0)) for label in CLASS_LABELS.get(count_class, (count_class,)))


@dataclass(frozen=True, slots=True)
class Rule:
    op: Op
    sources: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise ValueError(f"unknown op {self.op!r}; expected one of {OPS}")
        if not self.sources:
            raise ValueError("a rule needs at least one source")
        if self.op == "pick" and len(self.sources) != 1:
            raise ValueError("pick takes exactly one source")
        if self.op == "median" and len(self.sources) < 3:
            raise ValueError("median takes three or more sources")

    @property
    def name(self) -> str:
        if self.op == "pick":
            return self.sources[0]
        return f"{self.op}({', '.join(self.sources)})"

    def apply(self, by_source: SourceCounts, count_class: str) -> int:
        """The class count; a source missing for this photo is an error, not a zero."""
        missing = [s for s in self.sources if s not in by_source]
        if missing:
            raise KeyError(f"no counts from {missing} for this photo")
        values = [class_count(by_source[s], count_class) for s in self.sources]
        if self.op == "pick":
            return values[0]
        if self.op == "max":
            return max(values)
        if self.op == "min":
            return min(values)
        return int(statistics.median_low(values))


def fuse(by_source: SourceCounts, rules: Mapping[str, Rule], base: str) -> dict[str, int]:
    """``base``'s counts with every class in ``rules`` replaced by its rule's count.

    A class whose rule picks ``base`` keeps ``base``'s own labels (so its interior/exterior
    door split survives); any other rule writes the pooled count under the class name and
    drops the class's other labels. Zero counts are dropped.
    """
    out = {k: int(v) for k, v in by_source[base].items()}
    for count_class, rule in rules.items():
        if rule == Rule("pick", (base,)):
            continue
        for label in CLASS_LABELS.get(count_class, (count_class,)):
            out.pop(label, None)
        value = rule.apply(by_source, count_class)
        if value:
            out[count_class] = value
    return {k: v for k, v in out.items() if v}


@dataclass(frozen=True, slots=True)
class Example:
    """One labelled (photo, sample): every source's counts and the true class counts."""

    photo: str
    by_source: SourceCounts
    truth: Mapping[str, int]


def class_error(rule: Rule, examples: Sequence[Example], count_class: str) -> tuple[float, int]:
    """Total absolute error of ``rule`` on ``count_class`` and the number of pairs scored
    (examples whose truth does not have the class are not scored)."""
    total = 0.0
    n = 0
    for example in examples:
        if count_class not in example.truth:
            continue
        total += abs(rule.apply(example.by_source, count_class) - example.truth[count_class])
        n += 1
    return total, n


def fit(
    examples: Sequence[Example], candidates: Sequence[Rule], classes: Sequence[str]
) -> dict[str, Rule]:
    """Per class, the candidate with the lowest total absolute error on ``examples``;
    ties (and a class no example scores) go to the first candidate."""
    if not candidates:
        raise ValueError("no candidate rules")
    chosen: dict[str, Rule] = {}
    for count_class in classes:
        best = candidates[0]
        best_error, _ = class_error(best, examples, count_class)
        for rule in candidates[1:]:
            error, _ = class_error(rule, examples, count_class)
            if error < best_error:
                best, best_error = rule, error
        chosen[count_class] = best
    return chosen


__all__ = [
    "CLASS_LABELS",
    "OPS",
    "Example",
    "Rule",
    "class_count",
    "class_error",
    "fit",
    "fuse",
]
