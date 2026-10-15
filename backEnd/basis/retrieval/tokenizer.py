"""IRS-aware tokenization for BM25 (migrated from ``evidence_layer/src/tokenizers.py``).

A plain word tokenizer breaks the codes that matter most in tax references. This one
keeps them whole and adds variants so a query and a passage meet:

* ``§1245`` -> ``§1245`` and ``1245``
* ``168(e)(3)(B)`` -> ``168(e)(3)(b)`` and ``168``
* ``57.0`` and ``00.11`` (asset classes) -> the decimal as one token (the integer part
  is added only on the asset-class pass, which the code pass usually pre-empts: the
  legacy docstring claimed ``57`` too, its code never produced it)
* hyphenated codes -> one token each: ``Rev. Proc. 87-56`` -> ``87-56``,
  ``Table A-1`` -> ``a-1``, ``1.263(a)-3`` -> ``1.263(a)-3`` (added in S3, BUILD_PLAN
  6.1: the legacy tokenizer dropped ``87-56`` entirely because each half is a short
  number; the rule only adds tokens, so every legacy output is still contained)
* other words: lowercased, alphanumeric, at least two characters

Otherwise the behaviour is the legacy module's (its 14 tests are migrated in
``tests/unit/retrieval/test_tokenizer.py``); only typing and the demo ``__main__`` went.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

# §1245, 1245, 168(e)(3), 57.0, 00.11, ...
IRS_CODE_PATTERN: Final = re.compile(
    r"""
    (?:§\s*)?                           # optional § with optional space
    \d+                                 # required digits
    (?:\.\d+)?                          # optional decimal part
    (?:\([a-zA-Z0-9]+\))*               # optional parenthetical refs: (e)(3)
    """,
    re.VERBOSE,
)

# Asset classes such as 57.0, 00.11, 28.0.
ASSET_CLASS_PATTERN: Final = re.compile(r"\b\d{2}\.\d+\b")

# 87-56, A-1, B-1, 1.263(a)-3: a 1-3 letter or numeric (optionally parenthesized) head,
# a hyphen, digits. Not part of a longer hyphenated word (SYN-MLS-0001, 10-year).
HYPHEN_CODE_PATTERN: Final = re.compile(
    r"(?<![\w-])"
    r"(?:[A-Za-z]{1,3}|\d+(?:\.\d+)?(?:\([A-Za-z0-9]+\))*)"
    r"-\d+(?:\.\d+)?(?:\([A-Za-z0-9]+\))*"
    r"(?![\w-])"
)

_BASE_NUMBER: Final = re.compile(r"§?\s*(\d+(?:\.\d+)?)")
_SHORT_NUMBER: Final = re.compile(r"\d{1,2}")
_WORD: Final = re.compile(r"\b[a-zA-Z][a-zA-Z0-9]*\b")
_SIMPLE_WORD: Final = re.compile(r"\b[a-zA-Z0-9]+\b")

Tokenizer = Callable[[str], list[str]]


def irs_tokenize(text: str) -> list[str]:
    """Tokens of ``text`` with IRS codes preserved and their variants added.

    >>> irs_tokenize("§1245 property depreciation")
    ['§1245', '1245', 'property', 'depreciation']
    >>> irs_tokenize("Section 168(e)(3)(B)")
    ['168(e)(3)(b)', '168', 'section']
    >>> irs_tokenize("Asset class 57.0")
    ['57.0', 'asset', 'class']
    """
    tokens: list[str] = []
    code_spans: list[tuple[int, int]] = []

    for match in IRS_CODE_PATTERN.finditer(text):
        code = match.group()
        # A bare one- or two-digit number ("in 5 years") is not a code.
        if _SHORT_NUMBER.fullmatch(code):
            continue
        code_spans.append(match.span())
        tokens.append(code.lower().replace(" ", ""))
        if "§" in code:
            tokens.append(code.replace("§", "").replace(" ", "").lower())
        base = _BASE_NUMBER.match(code)
        if base is not None and base.group(1) not in tokens:
            tokens.append(base.group(1))

    for match in ASSET_CLASS_PATTERN.finditer(text):
        code = match.group().lower()
        if code not in tokens:
            tokens.append(code)
            integer_part = code.split(".")[0]
            if integer_part not in tokens:
                tokens.append(integer_part)

    for match in HYPHEN_CODE_PATTERN.finditer(text):
        code = match.group().lower()
        if code not in tokens:
            tokens.append(code)

    remaining = text
    for start, end in sorted(code_spans, reverse=True):
        remaining = remaining[:start] + " " + remaining[end:]
    tokens.extend(word.lower() for word in _WORD.findall(remaining) if len(word) >= 2)
    return tokens


def simple_tokenize(text: str) -> list[str]:
    """Lowercased alphanumeric words of at least two characters."""
    return [word.lower() for word in _SIMPLE_WORD.findall(text) if len(word) >= 2]


def get_tokenizer(doc_type: str = "irs") -> Tokenizer:
    """``irs_tokenize`` for tax and cost references, ``simple_tokenize`` otherwise."""
    if doc_type in ("irs", "rsmeans"):
        return irs_tokenize
    return simple_tokenize
