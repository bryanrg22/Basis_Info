"""Runtime configuration.

Rules (BUILD_PLAN C1):

* The environment is explicit: ``BASIS_ENV`` must be set in the process environment to one
  of ``dev``, ``test``, ``staging``, ``prod``. Nothing is inferred from which other
  variables happen to be present.
* A ``.env`` file is read only when ``BASIS_ENV=dev``. In ``test``, ``staging`` and
  ``prod`` every value comes from the process environment (in the cloud, Secret Manager
  values are injected as environment variables by Cloud Run).
* ``staging`` and ``prod`` refuse to start when a required value is missing.
* Secret values are ``SecretStr`` and connection strings are excluded from ``repr``, so
  printing or logging the settings object does not leak them. Validation errors hide
  their input for the same reason.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, get_args

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["dev", "test", "staging", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

ENV_VAR: Final = "BASIS_ENV"
DEFAULT_DEV_ENV_FILE: Final = ".env"
VALID_ENVS: Final[tuple[str, ...]] = get_args(Env)

_REQUIRED_IN_DEPLOYED: Final = ("database_url", "gcs_bucket", "firebase_project_id")
_REQUIRED: Final[dict[str, tuple[str, ...]]] = {
    "dev": (),
    "test": (),
    "staging": _REQUIRED_IN_DEPLOYED,
    "prod": _REQUIRED_IN_DEPLOYED,
}


class ConfigError(RuntimeError):
    """Configuration is missing or invalid.

    Deliberately not a ``ValueError``: pydantic would wrap a ``ValueError`` raised in a
    validator into a ``ValidationError``; this one propagates with only our message.
    """


def required_in(env: Env) -> tuple[str, ...]:
    """Names of the settings fields that must be set in ``env``."""
    return _REQUIRED[env]


class Settings(BaseSettings):
    """Process configuration. Build it with :func:`load_settings`, not directly."""

    model_config = SettingsConfigDict(
        env_file=None,  # chosen per construction by load_settings()
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    env: Env = Field(validation_alias=ENV_VAR)
    git_sha: str = "unknown"
    log_level: LogLevel = "INFO"
    database_url: str | None = Field(default=None, repr=False)
    # Connections held per process (SQLAlchemy pool_size; max_overflow is always 0).
    db_pool_size: int = Field(default=5, ge=1, le=100)
    # Cloud SQL instance connection name ("project:region:instance"). When set, the
    # engine connects through the Cloud SQL Python Connector instead of database_url.
    cloud_sql_instance: str | None = None
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_api_key: SecretStr | None = None
    temporal_task_queue: str = "basis-studies"
    temporal_worker_versioning: bool = False
    temporal_deployment_name: str = "basis-worker"
    temporal_build_id: str | None = None  # defaults to git_sha when unset
    photo_concurrency: int = Field(default=5, ge=1, le=32)
    review_reminder_days: int = Field(default=7, ge=1)
    review_deadline_days: int = Field(default=30, ge=1)
    study_budget_usd: float = Field(default=25.0, gt=0)
    relay_poll_seconds: float = Field(default=2.0, gt=0)
    gcs_bucket: str | None = None
    firebase_project_id: str | None = None
    rate_limit_reads_per_min: int = Field(default=240, gt=0)
    rate_limit_mutations_per_min: int = Field(default=60, gt=0)
    # Sentence-embedding model for the evidence corpora (recorded per corpus version).
    # "hash" selects the deterministic test embedder and is refused outside dev and test.
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # ------------------------------------------------------------------ model providers
    # Environment names match the legacy backEnd/.env (agentic/config/settings.py), so an
    # existing .env works unchanged; the newer names are accepted too. Mapping:
    #   OPENAI_API_KEY                       -> openai_api_key
    #   OPENAI_MODEL | TEXT_MODEL            -> text_model   (OpenAI model for text calls)
    #   VISION_MODEL                         -> vision_model (OpenAI model for photos)
    #   AZURE_OPENAI_ENDPOINT / _API_KEY     -> azure_openai_endpoint / azure_openai_api_key
    #   AZURE_OPENAI_API_VERSION             -> azure_openai_api_version (>= 2024-08-01)
    #   AZURE_OPENAI_DEPLOYMENT_NAME         -> azure_openai_deployment_name (default)
    #   AZURE_OPENAI_VISION_DEPLOYMENT       -> azure_openai_vision_deployment
    #   AZURE_OPENAI_NANO_DEPLOYMENT | AZURE_OPENAI_TEXT_DEPLOYMENT
    #                                        -> azure_openai_text_deployment
    #   AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT / _KEY -> azure_document_intelligence_*
    #   LLM_REQUEST_TIMEOUT | LLM_REQUEST_TIMEOUT_SECONDS -> llm_request_timeout_seconds
    openai_api_key: SecretStr | None = None
    text_model: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("text_model", "TEXT_MODEL", "OPENAI_MODEL"),
    )
    vision_model: str = "gpt-4o-mini"
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment_name: str | None = None
    azure_openai_vision_deployment: str | None = None
    azure_openai_text_deployment: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "azure_openai_text_deployment",
            "AZURE_OPENAI_TEXT_DEPLOYMENT",
            "AZURE_OPENAI_NANO_DEPLOYMENT",
        ),
    )
    # Which configured provider is tried first; the other is the per-call fallback.
    llm_primary: Literal["azure", "openai"] = "azure"
    llm_request_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=600,
        validation_alias=AliasChoices(
            "llm_request_timeout_seconds", "LLM_REQUEST_TIMEOUT_SECONDS", "LLM_REQUEST_TIMEOUT"
        ),
    )
    # Concurrent model calls per worker process (the provider pool's semaphore).
    llm_concurrency: int = Field(default=4, ge=1, le=64)
    vision_max_tokens: int = Field(default=1200, ge=64, le=16384)
    extraction_fallback_max_tokens: int = Field(default=800, ge=64, le=16384)
    azure_document_intelligence_endpoint: str | None = None
    azure_document_intelligence_key: SecretStr | None = None
    azure_di_timeout_seconds: float = Field(default=180.0, gt=0, le=1800)
    # A critical appraisal field below this confidence sends the extraction to review
    # (S3_SPEC section 4; tuned later by the real-data eval).
    extraction_review_threshold: float = Field(default=0.90, ge=0.0, le=1.0)

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _check_required(self) -> Settings:
        missing = [name for name in required_in(self.env) if not _is_set(getattr(self, name))]
        if missing:
            names = ", ".join(name.upper() for name in missing)
            raise ConfigError(f"{ENV_VAR}={self.env} requires these settings to be set: {names}")
        if self.embedding_model == "hash" and self.env not in ("dev", "test"):
            raise ConfigError(f"EMBEDDING_MODEL=hash is for tests only, not {ENV_VAR}={self.env}")
        return self


def _is_set(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value().strip())
    if isinstance(value, str):
        return bool(value.strip())
    return True


def load_settings(env_file: str | Path = DEFAULT_DEV_ENV_FILE) -> Settings:
    """Build settings from the process environment.

    ``BASIS_ENV`` is read from the process environment first (never from a file) and
    decides whether ``env_file`` is consulted at all: only ``dev`` reads it. Real
    environment variables always take precedence over the file.
    """
    raw = os.environ.get(ENV_VAR)
    if raw is None or not raw.strip():
        raise ConfigError(f"{ENV_VAR} is required and must be one of: {', '.join(VALID_ENVS)}")
    if raw not in VALID_ENVS:
        raise ConfigError(
            f"{ENV_VAR}={raw!r} is not valid; expected one of: {', '.join(VALID_ENVS)}"
        )
    chosen_file = env_file if raw == "dev" else None
    # env is read from the environment through its BASIS_ENV alias.
    return Settings(_env_file=chosen_file)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, built once."""
    return load_settings()


def reset_settings() -> None:
    """Drop the cached settings so the next :func:`get_settings` re-reads the environment."""
    get_settings.cache_clear()
