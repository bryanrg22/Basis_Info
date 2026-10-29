"""Azure DI page geometry for the sweep systems: :mod:`basis.extraction.geometry` (moved there
so the pipeline's highlight regions and the sweep share one implementation).

:func:`from_raw` reads a raw recording (``recordings/<sha>/azure_di.json``, the SDK's
``as_dict()``; the ``prebuilt-mortgage.us.1004`` result has the same ``pages`` shape) through
the production conversion (:func:`~basis.extraction.azure_di.from_sdk`), so a recording and a
stored analysis give the same geometry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from basis.extraction.azure_di import bbox_of as box_of
from basis.extraction.azure_di import from_sdk
from basis.extraction.geometry import (
    LABELLED_MARK_TOKEN,
    MARK_TOKEN,
    Box,
    Layout,
    Mark,
    Page,
    Word,
    find_all,
    fold_chars,
    layout_of,
    locate,
    region,
    union,
)

__all__ = [
    "LABELLED_MARK_TOKEN",
    "MARK_TOKEN",
    "Box",
    "Layout",
    "Mark",
    "Page",
    "Word",
    "box_of",
    "find_all",
    "fold_chars",
    "from_raw",
    "locate",
    "region",
    "union",
]


def from_raw(raw: Mapping[str, Any]) -> Layout:
    """The geometry of a raw ``AnalyzeResult.as_dict()`` (camelCase REST names)."""
    from evals.appraisal.recordings import rebuild_sdk_result

    return layout_of(from_sdk(rebuild_sdk_result(raw)))
