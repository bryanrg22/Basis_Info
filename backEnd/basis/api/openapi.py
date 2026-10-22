"""The OpenAPI document: how it is built, and the committed copy at ``docs/api/openapi.json``.

FastAPI generates the document from the routes; :func:`build_openapi` then makes it state
what the server really does:

* ``ProblemDetail`` (RFC 9457 plus ``request_id`` and ``errors``) is added to the
  components; routes reference it for every 4xx/5xx under ``application/problem+json``
  (see :func:`basis.api.routes.common.problems`), which also replaces FastAPI's default
  ``HTTPValidationError`` 422.
* ``Idempotency-Key`` is marked required on ``POST``/``DELETE`` with its length and
  character rules. The dependency declares it optional only so that a missing key is
  answered with the documented 400 problem rather than FastAPI's generic 422.

Regenerate the committed file after any API change (a test fails while it is stale)::

    uv run python -m basis.api.openapi --write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from fastapi.openapi.utils import get_openapi

from basis.api.errors import ProblemDetail
from basis.api.idempotency import ENFORCED_METHODS, IDEMPOTENCY_HEADER, MAX_KEY_LENGTH

if TYPE_CHECKING:
    from fastapi import FastAPI

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
OPENAPI_PATH: Final = REPO_ROOT / "docs" / "api" / "openapi.json"
# Visible ASCII without the double quote: the documented form of the key. The server also
# accepts the structured-field form "..." (draft-ietf-httpapi-idempotency-key-header).
# Bare or enclosed in double quotes (draft-ietf-httpapi-idempotency-key-header); the
# character class is basis.api.idempotency._KEY_PATTERN, so the contract test's
# negative cases (keys the document calls invalid) are refused by the parser too.
IDEMPOTENCY_KEY_PATTERN: Final = rf'^("[!#-~]{{1,{MAX_KEY_LENGTH}}}"|[!#-~]{{1,{MAX_KEY_LENGTH}}})$'

DESCRIPTION: Final = """\
Basis cost segregation API.

**Authentication.** Every `/v1` route needs `Authorization: Bearer <Firebase ID token>`.
A user is created on first sight, in no organization: firms are invite-only.
`GET /v1/me` lists your firms and the invitations waiting for your verified email; until
you accept one, the study list and study creation answer 403 `no-firm`.

**Authorization.** Studies belong to organizations (firms). A study or firm you are not a
member of is 404, not 403; one you can see but whose action needs a higher role
(viewer < engineer < owner) is 403. Owners administer their firm under
`/v1/orgs/{org_id}`: members, roles and invitations.

**Idempotency.** `POST` and `DELETE` need an `Idempotency-Key` header (a UUID is
recommended). Retrying with the same key and body replays the stored response with
`Idempotent-Replayed: true` for 24 hours; the same key with a different body is 422; a
retry while the first request is still running is 409. Keys are scoped per user.

**Rate limits.** Per user, by default 240 reads and 60 mutations per minute; 429 with
`Retry-After`.

**Errors.** Every 4xx/5xx is `application/problem+json` (RFC 9457): `type`, `title`,
`status`, `detail`, `instance`, `request_id` (equal to the `X-Request-Id` response
header), and `errors` (one entry per field) on validation failures.

**Limits.** JSON bodies up to 1 MiB (413 beyond). Files are never sent to the API: request
a signed URL, upload to it, then confirm.
"""


def _problem_components() -> dict[str, Any]:
    schema = ProblemDetail.model_json_schema(ref_template="#/components/schemas/{model}")
    components: dict[str, Any] = schema.pop("$defs", {})
    schema["description"] = (
        "RFC 9457 problem details. `type` is `about:blank` for plain HTTP errors and "
        "`https://basis.invalid/problems/<name>` for errors clients can branch on."
    )
    components["ProblemDetail"] = schema
    return components


def _require_idempotency_key(schema: dict[str, Any]) -> None:
    for operations in schema.get("paths", {}).values():
        for method, operation in operations.items():
            if method.upper() not in ENFORCED_METHODS:
                continue
            for parameter in operation.get("parameters", []):
                if parameter.get("in") == "header" and parameter.get("name") == IDEMPOTENCY_HEADER:
                    parameter["required"] = True
                    parameter["schema"] = {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_KEY_LENGTH + 2,  # the quoted form
                        "pattern": IDEMPOTENCY_KEY_PATTERN,
                        "title": IDEMPOTENCY_HEADER,
                    }


def build_openapi(app: FastAPI) -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=DESCRIPTION,
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("schemas", {}).update(_problem_components())
    _require_idempotency_key(schema)
    app.openapi_schema = schema
    return schema


def render(app: FastAPI) -> str:
    """The document exactly as committed: 2-space JSON, UTF-8, trailing newline."""
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def _spec_app() -> FastAPI:
    from basis.api.app import create_app
    from basis.config import Settings

    # The document does not depend on settings; build them without reading the environment.
    return create_app(Settings.model_construct(env="test"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or check docs/api/openapi.json.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write the file")
    mode.add_argument("--check", action="store_true", help="exit 1 if the file is stale")
    parser.add_argument("--path", type=Path, default=OPENAPI_PATH)
    args = parser.parse_args(argv)

    rendered = render(_spec_app())
    if args.write:
        args.path.parent.mkdir(parents=True, exist_ok=True)
        args.path.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.path}")
        return 0
    current = args.path.read_text(encoding="utf-8") if args.path.exists() else None
    if current != rendered:
        print(f"{args.path} is stale; run: uv run python -m basis.api.openapi --write")
        return 1
    print(f"{args.path} is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
