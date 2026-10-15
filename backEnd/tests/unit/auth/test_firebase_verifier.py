"""Invariants for Firebase ID token verification (S1_SPEC section 4).

* Each SDK failure maps to a 401 ``Unauthorized`` with its own ``detail``: expired,
  revoked, disabled user, invalid, certificate fetch failure.
* ``check_revoked`` is true only when ``BASIS_ENV=prod``.
* ``FIREBASE_AUTH_EMULATOR_HOST`` is honored by the SDK in dev/test, and the verifier
  (hence the app) refuses to start with it in staging/prod.
* The Firebase app is initialized lazily, once, even under concurrent first calls.
* Claims become a ``Principal(uid, email, display_name)``; a token without a subject is
  rejected.

Most tests inject ``verify_fn``; the ``real SDK`` tests run ``firebase_admin`` itself on
tokens that are rejected (or, with the emulator variable, accepted) before any network
call, to prove the exception classes caught are the ones the SDK raises.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Iterator, Mapping
from typing import Any

import firebase_admin
import google.auth.exceptions
import pytest
from firebase_admin import auth as firebase_auth

from basis.api.app import create_app
from basis.api.errors import ServiceUnavailable, Unauthorized
from basis.auth import firebase as fb
from basis.auth.firebase import FirebaseTokenVerifier, Principal, principal_from_claims
from basis.config import ConfigError, Settings, load_settings

PROJECT = "demo-basis-test"
EMULATOR = "127.0.0.1:9099"
DEPLOYED_VALUES = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",  # pragma: allowlist secret
    "GCS_BUCKET": "basis-test-bucket",
    "FIREBASE_PROJECT_ID": PROJECT,
}


@pytest.fixture(autouse=True)
def _clean_firebase(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(fb.EMULATOR_ENV_VAR, raising=False)
    yield
    for name in (fb.FIREBASE_APP_NAME, fb.FIREBASE_VERIFY_ONLY_APP_NAME):
        try:
            firebase_admin.delete_app(firebase_admin.get_app(name))
        except ValueError:
            pass


def settings_for(env: str, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("BASIS_ENV", env)
    for name, value in DEPLOYED_VALUES.items():
        monkeypatch.setenv(name, value)
    return load_settings()


class RecordingVerify:
    def __init__(self, result: Mapping[str, Any] | Exception) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, token: str, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append((token, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


CLAIMS = {"uid": "u-1", "sub": "u-1", "email": "a@example.com", "name": "Ada"}


# --------------------------------------------------------------------------- mapping


@pytest.mark.parametrize(
    ("exc", "detail"),
    [
        (firebase_auth.ExpiredIdTokenError("expired", cause=None), fb.DETAIL_EXPIRED),
        (firebase_auth.RevokedIdTokenError("revoked"), fb.DETAIL_REVOKED),
        (firebase_auth.UserDisabledError("disabled"), fb.DETAIL_DISABLED),
        (firebase_auth.UserNotFoundError("deleted"), fb.DETAIL_DELETED),
        (firebase_auth.InvalidIdTokenError("bad"), fb.DETAIL_INVALID),
    ],
    ids=["expired", "revoked", "disabled", "deleted", "invalid"],
)
def test_sdk_errors_map_to_unauthorized(settings: Settings, exc: Exception, detail: str) -> None:
    verifier = FirebaseTokenVerifier(settings, verify_fn=RecordingVerify(exc), environ={})
    with pytest.raises(Unauthorized) as caught:
        verifier.verify("tok")
    assert caught.value.status == 401
    assert caught.value.detail == detail
    assert caught.value.headers["WWW-Authenticate"] == 'Bearer error="invalid_token"'
    assert caught.value.__cause__ is exc


def test_certificate_fetch_failure_is_503(settings: Settings) -> None:
    """Unreachable Google keys are a server-side outage, not a bad token: 503, not 401."""
    exc = firebase_auth.CertificateFetchError("no certs", cause=None)
    verifier = FirebaseTokenVerifier(settings, verify_fn=RecordingVerify(exc), environ={})
    with pytest.raises(ServiceUnavailable) as caught:
        verifier.verify("tok")
    assert caught.value.status == 503
    assert caught.value.detail == fb.DETAIL_CERTIFICATES
    assert "WWW-Authenticate" not in caught.value.headers
    assert caught.value.headers["Retry-After"]
    assert caught.value.__cause__ is exc


def test_details_are_distinct() -> None:
    details = {
        fb.DETAIL_EXPIRED,
        fb.DETAIL_REVOKED,
        fb.DETAIL_DISABLED,
        fb.DETAIL_DELETED,
        fb.DETAIL_INVALID,
        fb.DETAIL_CERTIFICATES,
        fb.DETAIL_NO_UID,
    }
    assert len(details) == 7


def test_details_never_include_sdk_message(settings: Settings) -> None:
    exc = firebase_auth.InvalidIdTokenError("token eyJhbGciOi... has wrong aud")
    verifier = FirebaseTokenVerifier(settings, verify_fn=RecordingVerify(exc), environ={})
    with pytest.raises(Unauthorized) as caught:
        verifier.verify("tok")
    assert "eyJ" not in str(caught.value.detail)


def test_unexpected_errors_propagate(settings: Settings) -> None:
    """A misconfiguration (e.g. no project id) is a server error, not a 401."""
    verifier = FirebaseTokenVerifier(
        settings, verify_fn=RecordingVerify(ValueError("no project id")), environ={}
    )
    with pytest.raises(ValueError):
        verifier.verify("tok")


# --------------------------------------------------------------------------- claims


def test_claims_become_principal(settings: Settings) -> None:
    verify = RecordingVerify(CLAIMS)
    verifier = FirebaseTokenVerifier(settings, verify_fn=verify, environ={})
    assert verifier.verify("tok") == Principal("u-1", "a@example.com", "Ada")
    ((token, kwargs),) = verify.calls
    assert token == "tok"
    assert kwargs["app"] is firebase_admin.get_app(fb.FIREBASE_VERIFY_ONLY_APP_NAME)


def test_optional_claims_may_be_absent() -> None:
    assert principal_from_claims({"sub": "u-2"}) == Principal("u-2", None, None)


def test_non_string_optional_claims_are_dropped() -> None:
    assert principal_from_claims({"uid": "u-3", "email": 5, "name": None}) == Principal("u-3")


@pytest.mark.parametrize("claims", [{}, {"uid": ""}, {"uid": 7}, {"email": "a@example.com"}])
def test_claims_without_subject_are_rejected(claims: dict[str, Any]) -> None:
    with pytest.raises(Unauthorized) as caught:
        principal_from_claims(claims)
    assert caught.value.detail == fb.DETAIL_NO_UID


# --------------------------------------------------------------------------- check_revoked


@pytest.mark.parametrize(
    ("env", "expected"), [("dev", False), ("test", False), ("staging", False), ("prod", True)]
)
def test_check_revoked_only_in_prod(
    monkeypatch: pytest.MonkeyPatch, env: str, expected: bool
) -> None:
    verify = RecordingVerify(CLAIMS)
    verifier = FirebaseTokenVerifier(settings_for(env, monkeypatch), verify_fn=verify, environ={})
    verifier.verify("tok")
    assert verifier.check_revoked is expected
    assert verify.calls[0][1]["check_revoked"] is expected


# --------------------------------------------------------------------------- emulator


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_emulator_refused_in_deployed_envs(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    settings = settings_for(env, monkeypatch)
    with pytest.raises(ConfigError, match="FIREBASE_AUTH_EMULATOR_HOST"):
        FirebaseTokenVerifier(settings, environ={fb.EMULATOR_ENV_VAR: EMULATOR})


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_app_refuses_to_start_with_emulator_in_deployed_envs(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    settings = settings_for(env, monkeypatch)
    monkeypatch.setenv(fb.EMULATOR_ENV_VAR, EMULATOR)
    with pytest.raises(ConfigError):
        create_app(settings)


@pytest.mark.parametrize("env", ["dev", "test"])
def test_emulator_allowed_in_dev_and_test(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    settings = settings_for(env, monkeypatch)
    FirebaseTokenVerifier(settings, environ={fb.EMULATOR_ENV_VAR: EMULATOR})


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_blank_emulator_variable_is_not_set(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    FirebaseTokenVerifier(settings_for(env, monkeypatch), environ={fb.EMULATOR_ENV_VAR: ""})


def test_reads_process_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_for("prod", monkeypatch)
    monkeypatch.setenv(fb.EMULATOR_ENV_VAR, EMULATOR)
    with pytest.raises(ConfigError):
        FirebaseTokenVerifier(settings)


# --------------------------------------------------------------------------- lazy init


@pytest.mark.parametrize("admin", [False, True])
def test_firebase_app_initialized_once(monkeypatch: pytest.MonkeyPatch, admin: bool) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []
    real_init = firebase_admin.initialize_app

    def counting_init(*args: Any, **kwargs: Any) -> firebase_admin.App:
        calls.append((args, kwargs))
        return real_init(*args, **kwargs)

    monkeypatch.setattr(firebase_admin, "initialize_app", counting_init)
    apps: list[firebase_admin.App] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        apps.append(fb._firebase_app(PROJECT, admin=admin))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    ((credential,), kwargs) = calls[0]
    assert kwargs["options"] == {"projectId": PROJECT}
    if admin:
        assert kwargs["name"] == fb.FIREBASE_APP_NAME
        assert credential is None  # Application Default Credentials
    else:
        assert kwargs["name"] == fb.FIREBASE_VERIFY_ONLY_APP_NAME
        assert isinstance(credential, fb.VerifyOnlyCredential)
    assert len({id(a) for a in apps}) == 1


def test_prod_uses_admin_app_with_default_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    verify = RecordingVerify(CLAIMS)
    FirebaseTokenVerifier(settings_for("prod", monkeypatch), verify_fn=verify, environ={}).verify(
        "t"
    )
    assert verify.calls[0][1]["app"] is firebase_admin.get_app(fb.FIREBASE_APP_NAME)


def test_verification_never_loads_default_credentials(
    project_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside prod, verifying needs no ADC: no gcloud subprocess, no metadata probe."""

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("google.auth.default must not be called")

    monkeypatch.setattr("google.auth.default", forbidden)
    with pytest.raises(Unauthorized):
        FirebaseTokenVerifier(project_settings).verify("a.b.c")


def test_construction_does_not_initialize_firebase(settings: Settings) -> None:
    FirebaseTokenVerifier(settings, environ={})
    with pytest.raises(ValueError):
        firebase_admin.get_app(fb.FIREBASE_APP_NAME)


def test_default_firebase_app_is_untouched(settings: Settings) -> None:
    FirebaseTokenVerifier(settings, verify_fn=RecordingVerify(CLAIMS), environ={}).verify("t")
    with pytest.raises(ValueError):
        firebase_admin.get_app()


# --------------------------------------------------------------------------- real SDK


def _segment(obj: Mapping[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def unsigned_token(**claims: Any) -> str:
    now = int(time.time())
    payload = {
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "aud": PROJECT,
        "sub": "emu-user",
        "iat": now,
        "exp": now + 3600,
        "auth_time": now,
        "email": "emu@example.com",
        "name": "Emu",
        **claims,
    }
    return f"{_segment({'alg': 'none', 'typ': 'JWT'})}.{_segment(payload)}."


@pytest.fixture
def project_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)
    return load_settings()


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b.c", unsigned_token()])
def test_real_sdk_rejects_bad_tokens_as_invalid(project_settings: Settings, token: str) -> None:
    """No ``kid``/RS256, or not a JWT at all: rejected before any network call."""
    with pytest.raises(Unauthorized) as caught:
        FirebaseTokenVerifier(project_settings).verify(token)
    assert caught.value.detail == fb.DETAIL_INVALID


def _signed_looking_token() -> str:
    now = int(time.time())
    header = {"alg": "RS256", "kid": "k1", "typ": "JWT"}
    payload = {
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "aud": PROJECT,
        "sub": "u",
        "iat": now,
        "exp": now + 60,
    }
    return f"{_segment(header)}.{_segment(payload)}.c2ln"


def test_real_sdk_expired_maps_to_expired(
    project_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def expired(*args: Any, **kwargs: Any) -> None:
        raise ValueError("Token expired, 1 < 2")

    monkeypatch.setattr("google.oauth2.id_token.verify_token", expired)
    with pytest.raises(Unauthorized) as caught:
        FirebaseTokenVerifier(project_settings).verify(_signed_looking_token())
    assert caught.value.detail == fb.DETAIL_EXPIRED


def test_real_sdk_certificate_failure_maps_to_certificates(
    project_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreachable(*args: Any, **kwargs: Any) -> None:
        raise google.auth.exceptions.TransportError("connection refused")

    monkeypatch.setattr("google.oauth2.id_token.verify_token", unreachable)
    with pytest.raises(ServiceUnavailable) as caught:
        FirebaseTokenVerifier(project_settings).verify(_signed_looking_token())
    assert caught.value.status == 503
    assert caught.value.detail == fb.DETAIL_CERTIFICATES


def test_real_sdk_accepts_emulator_tokens_when_emulator_set(
    project_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verifier does nothing special for the emulator; the SDK accepts its tokens."""
    monkeypatch.setenv(fb.EMULATOR_ENV_VAR, EMULATOR)
    principal = FirebaseTokenVerifier(project_settings).verify(unsigned_token())
    assert principal == Principal("emu-user", "emu@example.com", "Emu")


def test_real_sdk_checks_audience_even_with_emulator(
    project_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(fb.EMULATOR_ENV_VAR, EMULATOR)
    with pytest.raises(Unauthorized) as caught:
        FirebaseTokenVerifier(project_settings).verify(unsigned_token(aud="other-project"))
    assert caught.value.detail == fb.DETAIL_INVALID
