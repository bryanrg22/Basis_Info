"""Shared request/response building blocks.

Text that reaches PostgreSQL must not contain NUL: ``TEXT`` columns and ``JSONB`` strings
reject it, which would surface as a 500. Request strings therefore carry
:data:`NO_NUL_PATTERN`, so the OpenAPI contract states the rule and clients get a 422.
Integers that reach an ``INT`` column are bounded to int32 for the same reason.

Numbers must be JSON numbers: :data:`JSON_NUMBER` refuses strings and booleans that
pydantic's lax mode would otherwise coerce (``"1"`` -> 1, ``true`` -> 1), so a value the
schema calls invalid is never accepted.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

NO_NUL_PATTERN: Final = r"^[^\x00]*$"
# One-line text with at least one visible character: no control characters (C0, DEL, C1)
# and not only whitespace. Values are trimmed after validation.
_LINE_CHAR: Final = r"[^\x00-\x1f\x7f-\x9f]"
_VISIBLE_CHAR: Final = r"[^\x00-\x1f\x7f-\x9f\s]"
LINE_PATTERN: Final = rf"^{_LINE_CHAR}*{_VISIBLE_CHAR}{_LINE_CHAR}*$"
INT32_MAX: Final = 2**31 - 1


def _json_number(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("must be a JSON number, not a string or boolean")
    return value


# The validator comes after the constraints in the metadata so the constraints stay on
# the integer schema and appear in the JSON schema as minimum/maximum.
JSON_NUMBER: Final = BeforeValidator(_json_number)
# A rejection stores revision + 1 in an INT column, so the largest decidable revision is
# one below the int32 maximum (found by the Schemathesis contract test: INT32_MAX
# overflowed the transition's bind parameter and answered 500).
MAX_REVISION: Final = INT32_MAX - 1
Revision = Annotated[
    int,
    Field(ge=1, le=MAX_REVISION, description="Output revision (>= 1)."),
    JSON_NUMBER,
]


class RequestModel(BaseModel):
    """Base for request bodies: unknown fields are rejected, not silently dropped.

    Strings are not stripped before validation: the patterns in the schema are what the
    server enforces, character for character, so the OpenAPI contract is exact.
    """

    model_config = ConfigDict(extra="forbid")


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
