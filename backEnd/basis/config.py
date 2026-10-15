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

from pydantic import Field, SecretStr, field_validator, model_validator
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
