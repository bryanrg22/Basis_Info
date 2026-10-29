"""Authentication dependencies.

* :func:`get_optional_principal`: ``None`` when the request has no ``Authorization``
  header; 401 when the header is malformed or the token fails verification.
* :func:`get_principal`: the same, but 401 when the header is missing.

``get_principal`` depends on ``get_optional_principal``, and FastAPI caches a
dependency's result per request, so a route that also uses the rate limiter (which
reads the optional principal) verifies the token once.

The verifier is resolved through :func:`get_token_verifier`; tests replace it with
``app.dependency_overrides[get_token_verifier] = lambda: FakeVerifier()`` so the header
parsing and error mapping still run.

The database user (upsert on first sight, personal org) and study authorization build
on ``get_principal`` in :mod:`basis.api.users` (``CurrentUserDep``, ``study_access``).
"""

from __future__ import annotations

from typing import Annotated, Final, Protocol

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from basis.api.errors import Unauthorized
from basis.auth.firebase import Principal
from basis.errors.codes import E

AUTHORIZATION_HEADER: Final = "Authorization"
DETAIL_MISSING: Final = "Missing Authorization header; send 'Authorization: Bearer <ID token>'."
DETAIL_MALFORMED: Final = "Malformed Authorization header; expected 'Bearer <ID token>'."


class TokenVerifier(Protocol):
    """Turns a bearer token into a principal or raises ``Unauthorized``."""

    def verify(self, token: str) -> Principal: ...


# Declared only so OpenAPI advertises the bearer scheme; parsing is done below so a
# missing header and a malformed one get different messages.
_bearer_scheme = HTTPBearer(
    auto_error=False, scheme_name="FirebaseIdToken", description="Firebase ID token"
)


def get_token_verifier(request: Request) -> TokenVerifier:
    verifier: TokenVerifier = request.app.state.token_verifier
    return verifier


def parse_bearer(header: str) -> str:
    """Return the token from ``Bearer <token>``; raise ``Unauthorized`` otherwise.

    The scheme is case-insensitive (RFC 9110 section 11.1); the token must be a single
    non-empty run of non-whitespace characters.
    """
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise Unauthorized(DETAIL_MALFORMED, error=None, code=E.API_AUTH_MALFORMED)
    return parts[1]


async def get_optional_principal(
    request: Request,
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
    _scheme: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)],
) -> Principal | None:
    header = request.headers.get(AUTHORIZATION_HEADER)
    if header is None:
        return None
    token = parse_bearer(header)
    return await run_in_threadpool(verifier.verify, token)


async def get_principal(
    principal: Annotated[Principal | None, Depends(get_optional_principal)],
) -> Principal:
    if principal is None:
        raise Unauthorized(DETAIL_MISSING, error=None, code=E.API_AUTH_MISSING)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(get_optional_principal)]
