"""``POST /v1/demo-requests`` and ``GET /v1/ops/demo-requests`` (docs/api/DEMO_REQUESTS.md).

The request is what the public site's "Book a demo" form sends. Every string is checked by
its pattern first (so the OpenAPI contract states the rule exactly) and trimmed after; an
optional member that is empty after trimming is absent. Unknown members are refused (422),
like every request body of the API.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, ConfigDict, Field

from basis.api.schemas.common import LINE_PATTERN, RequestModel, ResponseModel
from basis.db.repositories.invitation import EMAIL_PATTERN, MAX_EMAIL_LENGTH
from basis.db.repositories.user import normalize_email

MAX_NAME_LENGTH: Final = 200
MAX_COMPANY_LENGTH: Final = 200
MAX_NOTE_LENGTH: Final = 4000
MAX_PAGE_LENGTH: Final = 2000
MAX_WEBSITE_LENGTH: Final = 2000
# The note is a textarea: tabs and line breaks, no other control character (NUL cannot reach
# PostgreSQL text).
NOTE_PATTERN: Final = r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$"
# One line, possibly empty (a URL): no control characters.
OPTIONAL_LINE_PATTERN: Final = r"^[^\x00-\x1f\x7f-\x9f]*$"
WEBSITE_PATTERN: Final = r"^[^\x00]*$"

DemoRequestRole = Literal["owner_partner", "engineer", "analyst_staff", "other"]
Notification = Literal["queued", "sent", "failed"]


def _trim(value: str) -> str:
    return value.strip()


def _trim_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


class DemoRequestIn(RequestModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "Dana Whitfield",
                    "email": "dana@acme-eng.example",
                    "company": "Acme Engineering",
                    "role": "engineer",
                    "note": "We do about forty studies a year.",
                    "page": "https://basis.example/demo.html",
                    "website": "",
                }
            ]
        }
    )

    name: Annotated[
        str,
        Field(min_length=1, max_length=MAX_NAME_LENGTH, pattern=LINE_PATTERN),
        AfterValidator(_trim),
    ] = Field(description="The visitor's name. One line; trimmed.")
    email: Annotated[
        str,
        Field(max_length=MAX_EMAIL_LENGTH, pattern=EMAIL_PATTERN),
        AfterValidator(normalize_email),
    ] = Field(description="Their work email. Trimmed and lower-cased.")
    company: Annotated[
        str,
        Field(min_length=1, max_length=MAX_COMPANY_LENGTH, pattern=LINE_PATTERN),
        AfterValidator(_trim),
    ] = Field(description="Their firm. One line; trimmed.")
    role: DemoRequestRole = Field(description="What they do at the firm.")
    note: Annotated[
        str | None,
        Field(max_length=MAX_NOTE_LENGTH, pattern=NOTE_PATTERN),
        AfterValidator(_trim_or_none),
    ] = Field(default=None, description="Optional free text; line breaks allowed.")
    page: Annotated[
        str | None,
        Field(max_length=MAX_PAGE_LENGTH, pattern=OPTIONAL_LINE_PATTERN),
        AfterValidator(_trim_or_none),
    ] = Field(default=None, description="The page the form was on (the browser's location).")
    website: Annotated[
        str,
        Field(max_length=MAX_WEBSITE_LENGTH, pattern=WEBSITE_PATTERN),
        AfterValidator(_trim),
    ] = Field(
        default="",
        description="A field people never see; the page always sends it empty. Anything in it "
        "is answered as a success and stored nowhere.",
    )


class DemoRequestAcceptedOut(ResponseModel):
    id: uuid.UUID = Field(description="The request's id.")


class DemoRequestOut(ResponseModel):
    id: uuid.UUID
    seq: int = Field(description="Newest first; the keyset cursor.")
    received_at: datetime = Field(description="The database clock when it was stored.")
    name: str
    email: str
    company: str
    role: DemoRequestRole
    note: str | None
    page: str | None
    remote_addr: str | None = Field(description="The client address the rate limit keyed on.")
    user_agent: str | None
    request_id: str | None = Field(description="The API request that stored it.")
    notification: Notification = Field(
        description="The email to `DEMO_REQUEST_NOTIFY_EMAIL`: `queued` (the relay has not "
        "handled it yet), `sent` (handed to the sender at `notified_at`), `failed` (the last "
        "attempt did not send it and `notify_error` says why: no recipient configured, or the "
        "sender failed, which the relay retries)."
    )
    notified_at: datetime | None
    notify_error: str | None


class DemoRequestPageOut(ResponseModel):
    items: list[DemoRequestOut] = Field(description="Newest first (`seq` descending).")
    next_cursor: str | None = Field(
        description="Pass as `cursor` to get the next (older) page; null on the last page."
    )
