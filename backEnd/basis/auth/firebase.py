"""Firebase ID token verification.

``verify_id_token`` checks the RS256 signature against Google's public certificates
(fetched over HTTPS and cached by the SDK), the ``aud``/``iss`` claims against the
project id, and ``exp``/``iat``/``auth_time``. With ``check_revoked=True`` it also reads
the user record to reject revoked sessions and disabled accounts; that is a network
call per request, so it is enabled only in ``prod`` (S1_SPEC section 4).

Emulator: when ``FIREBASE_AUTH_EMULATOR_HOST`` is set the SDK itself accepts unsigned
emulator tokens. That must never happen in a deployed environment, so constructing the
verifier with the variable set and ``env`` in ``staging``/``prod`` raises
:class:`~basis.config.ConfigError` and the app refuses to start.

Credentials: verifying a token needs only Google's public certificates, so outside
``prod`` the Firebase app is initialized with anonymous credentials and never looks for
Application Default Credentials (which would otherwise be loaded, or probed for over the
network, the first time the auth client is built). ``prod`` uses ADC, which the
revocation check needs to read user records.

``verify`` is synchronous (the SDK is); call it from a thread in async code.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

import firebase_admin
import google.auth.credentials
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials as firebase_credentials

from basis.api.errors import ServiceUnavailable, Unauthorized
from basis.config import ConfigError, Settings

EMULATOR_ENV_VAR: Final = "FIREBASE_AUTH_EMULATOR_HOST"
FIREBASE_APP_NAME: Final = "basis"
FIREBASE_VERIFY_ONLY_APP_NAME: Final = "basis-verify-only"
_DEPLOYED_ENVS: Final = frozenset({"staging", "prod"})

DETAIL_EXPIRED: Final = "The ID token has expired; refresh it and retry."
DETAIL_REVOKED: Final = "The ID token has been revoked; sign in again."
DETAIL_DISABLED: Final = "The user account is disabled."
DETAIL_DELETED: Final = "The user account no longer exists."
DETAIL_INVALID: Final = "The ID token is invalid."
DETAIL_CERTIFICATES: Final = "The ID token could not be verified: signing keys are unavailable."
DETAIL_NO_UID: Final = "The ID token has no subject."

logger = logging.getLogger(__name__)

VerifyFn = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, as asserted by a verified ID token."""

    uid: str
    email: str | None = None
    display_name: str | None = None
    # The ``email_verified`` claim: Google sign-in asserts it; an email/password account
    # does not until the address is confirmed. Answering a firm invitation needs it.
    email_verified: bool = False


def ensure_emulator_not_deployed(settings: Settings, environ: Mapping[str, str]) -> None:
    """Raise when the auth emulator is configured in a deployed environment."""
    if environ.get(EMULATOR_ENV_VAR) and settings.env in _DEPLOYED_ENVS:
        raise ConfigError(
            f"{EMULATOR_ENV_VAR} is set but BASIS_ENV={settings.env}; the auth emulator "
            "accepts unsigned tokens and is allowed only in dev and test"
        )


class VerifyOnlyCredential(firebase_credentials.Base):  # type: ignore[misc]
    """Anonymous credentials: enough to verify ID tokens, not to call admin APIs."""

    def get_credential(self) -> google.auth.credentials.Credentials:
        return google.auth.credentials.AnonymousCredentials()  # type: ignore[no-untyped-call]


_init_lock = threading.Lock()


def _firebase_app(project_id: str | None, *, admin: bool) -> firebase_admin.App:
    """The named Firebase app, initialized on first use; safe to call repeatedly.

    Named apps (not ``[DEFAULT]``) so legacy code that initializes the default app in
    the same process cannot conflict. ``admin`` selects ADC instead of anonymous
    credentials, under a different name so the two never share a cached auth client.
    """
    name = FIREBASE_APP_NAME if admin else FIREBASE_VERIFY_ONLY_APP_NAME
    with _init_lock:
        try:
            return firebase_admin.get_app(name)
        except ValueError:
            options = {"projectId": project_id} if project_id else None
            credential = None if admin else VerifyOnlyCredential()
            return firebase_admin.initialize_app(credential, options=options, name=name)


class FirebaseTokenVerifier:
    """Verify Firebase ID tokens and map SDK failures to 401 problems."""

    def __init__(
        self,
        settings: Settings,
        *,
        verify_fn: VerifyFn | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        ensure_emulator_not_deployed(settings, os.environ if environ is None else environ)
        self._project_id = settings.firebase_project_id
        self._check_revoked = settings.env == "prod"
        self._verify_fn: VerifyFn = verify_fn or firebase_auth.verify_id_token

    @property
    def check_revoked(self) -> bool:
        return self._check_revoked

    def verify(self, token: str) -> Principal:
        app = _firebase_app(self._project_id, admin=self._check_revoked)
        try:
            claims = self._verify_fn(token, app=app, check_revoked=self._check_revoked)
        # Expired and Revoked subclass InvalidIdTokenError, so they come first.
        except firebase_auth.ExpiredIdTokenError as exc:
            raise Unauthorized(DETAIL_EXPIRED) from exc
        except firebase_auth.RevokedIdTokenError as exc:
            raise Unauthorized(DETAIL_REVOKED) from exc
        except firebase_auth.UserDisabledError as exc:
            raise Unauthorized(DETAIL_DISABLED) from exc
        except firebase_auth.UserNotFoundError as exc:
            # check_revoked reads the user record; a deleted user's token is still signed.
            raise Unauthorized(DETAIL_DELETED) from exc
        except firebase_auth.InvalidIdTokenError as exc:
            raise Unauthorized(DETAIL_INVALID) from exc
        except firebase_auth.CertificateFetchError as exc:
            logger.warning("firebase certificate fetch failed", exc_info=True)
            # Google's public keys were unreachable: the server, not the token, is at fault.
            raise ServiceUnavailable(DETAIL_CERTIFICATES) from exc
        return principal_from_claims(claims)


def principal_from_claims(claims: Mapping[str, Any]) -> Principal:
    uid = claims.get("uid") or claims.get("sub")
    if not isinstance(uid, str) or not uid:
        raise Unauthorized(DETAIL_NO_UID)
    email = claims.get("email")
    name = claims.get("name")
    return Principal(
        uid=uid,
        email=email if isinstance(email, str) else None,
        display_name=name if isinstance(name, str) else None,
        # Only a JSON true counts: a missing, string or other value is unverified.
        email_verified=claims.get("email_verified") is True,
    )
