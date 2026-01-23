"""
Application settings with environment variable support.

Configuration is loaded from environment variables with optional .env file.
Azure OpenAI settings override OpenAI when fully configured.
"""

import os
from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment variables."""

    model_config = SettingsConfigDict(
        # Skip .env file in Docker (env_file in docker-compose loads vars)
        # This prevents file lock conflicts when multiple containers start
        env_file=".env" if not os.environ.get("PYTHONPATH") else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI settings (default provider)
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")

    # Azure OpenAI settings (overrides OpenAI if all are set)
    azure_openai_endpoint: Optional[str] = Field(
        default=None, alias="AZURE_OPENAI_ENDPOINT"
    )
    azure_openai_api_key: Optional[str] = Field(
        default=None, alias="AZURE_OPENAI_API_KEY"
    )
    azure_openai_deployment_name: Optional[str] = Field(
        default=None, alias="AZURE_OPENAI_DEPLOYMENT_NAME"
    )
    # Separate deployments for vision and text
    # Vision: GPT-4o-mini for testing, swap to GPT-5.2 when Azure registration approved
    azure_openai_vision_deployment: Optional[str] = Field(
        default=None, alias="AZURE_OPENAI_VISION_DEPLOYMENT"
    )
    # Text: GPT-5-nano (cheapest, no registration required)
    azure_openai_nano_deployment: Optional[str] = Field(
        default=None, alias="AZURE_OPENAI_NANO_DEPLOYMENT"
    )
    # Legacy fallback
    azure_openai_mini_deployment: Optional[str] = Field(
        default=None, alias="AZURE_OPENAI_MINI_DEPLOYMENT"
    )
    azure_openai_api_version: str = Field(
        default="2024-02-15-preview", alias="AZURE_OPENAI_API_VERSION"
    )

    # Azure Document Intelligence settings
    azure_document_intelligence_endpoint: Optional[str] = Field(
        default=None, alias="AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"
    )
    azure_document_intelligence_key: Optional[str] = Field(
        default=None, alias="AZURE_DOCUMENT_INTELLIGENCE_KEY"
    )

    # LangSmith settings
    langchain_api_key: Optional[str] = Field(default=None, alias="LANGCHAIN_API_KEY")
    langchain_project: str = Field(default="basis-agentic", alias="LANGCHAIN_PROJECT")
    langchain_tracing_v2: bool = Field(default=True, alias="LANGCHAIN_TRACING_V2")

    # Firebase settings
    google_application_credentials: Optional[str] = Field(
        default=None, alias="GOOGLE_APPLICATION_CREDENTIALS"
    )
    firebase_project_id: Optional[str] = Field(
        default=None, alias="FIREBASE_PROJECT_ID"
    )

    # Evidence layer settings
    evidence_data_dir: str = Field(
        default="data", alias="EVIDENCE_DATA_DIR"
    )

    # GCS settings for production index storage
    gcs_bucket_name: Optional[str] = Field(
        default=None, alias="GCS_BUCKET_NAME"
    )
    gcs_index_prefix: str = Field(
        default="indexes", alias="GCS_INDEX_PREFIX"
    )
    use_local_indexes: bool = Field(
        default=True, alias="USE_LOCAL_INDEXES"
    )
    local_cache_dir: Optional[str] = Field(
        default=None, alias="LOCAL_CACHE_DIR"
    )

    # LLM behavior settings
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=4096, ge=1)

    # LLM retry settings
    llm_max_retries: int = Field(
        default=3, ge=1, le=10,
        alias="LLM_MAX_RETRIES",
        description="Maximum retry attempts for LLM calls on rate limit errors",
    )
    llm_retry_base_delay: float = Field(
        default=2.0, ge=0.5, le=30.0,
        alias="LLM_RETRY_BASE_DELAY",
        description="Base delay in seconds for exponential backoff",
    )
    llm_retry_max_delay: float = Field(
        default=30.0, ge=5.0, le=120.0,
        alias="LLM_RETRY_MAX_DELAY",
        description="Maximum delay in seconds for exponential backoff",
    )
    llm_request_timeout: int = Field(
        default=60, ge=10, le=300,
        alias="LLM_REQUEST_TIMEOUT",
        description="Request timeout in seconds for LLM calls",
    )

    # Agent behavior settings
    agent_max_iterations: int = Field(
        default=5, ge=1, le=20,
        alias="AGENT_MAX_ITERATIONS",
        description="Maximum iterations for agent ReAct loops",
    )

    # Phase 5: Job queue settings
    job_poll_interval_seconds: float = Field(
        default=5.0, ge=1.0, le=60.0,
        alias="JOB_POLL_INTERVAL_SECONDS",
        description="Seconds between job queue polls by workers",
    )
    job_default_timeout_seconds: int = Field(
        default=300, ge=30, le=3600,
        alias="JOB_DEFAULT_TIMEOUT_SECONDS",
        description="Default job execution timeout in seconds",
    )
    job_max_retries: int = Field(
        default=3, ge=0, le=10,
        alias="JOB_MAX_RETRIES",
        description="Maximum retry attempts for failed jobs",
    )
    job_stale_cleanup_minutes: int = Field(
        default=30, ge=5, le=120,
        alias="JOB_STALE_CLEANUP_MINUTES",
        description="Minutes after which claimed jobs are considered stale",
    )

    # Phase 5: Cross-validation settings
    cross_validation_enabled: bool = Field(
        default=True,
        alias="CROSS_VALIDATION_ENABLED",
        description="Enable cross-stage validation checks",
    )

    # Phase 6: Alert settings
    alert_slack_webhook: Optional[str] = Field(
        default=None,
        alias="ALERT_SLACK_WEBHOOK",
        description="Slack webhook URL for workflow alerts",
    )
    alert_webhook_url: Optional[str] = Field(
        default=None,
        alias="ALERT_WEBHOOK_URL",
        description="Generic webhook URL for alerts",
    )
    alert_throttle_seconds: int = Field(
        default=300,
        ge=0,
        le=3600,
        alias="ALERT_THROTTLE_SECONDS",
        description="Minimum seconds between similar alerts",
    )

    # CORS settings
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:3001"],
        alias="CORS_ORIGINS",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Parse CORS origins from comma-separated string or list."""
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    def is_azure_configured(self) -> bool:
        """Check if Azure OpenAI is fully configured."""
        return all([
            self.azure_openai_endpoint,
            self.azure_openai_api_key,
            self.azure_openai_deployment_name,
        ])

    def is_langsmith_configured(self) -> bool:
        """Check if LangSmith is configured."""
        return self.langchain_api_key is not None

    def is_gcs_configured(self) -> bool:
        """Check if GCS index storage is configured."""
        return self.gcs_bucket_name is not None and not self.use_local_indexes

    def is_document_intelligence_configured(self) -> bool:
        """Check if Azure Document Intelligence is configured."""
        return all([
            self.azure_document_intelligence_endpoint,
            self.azure_document_intelligence_key,
        ])


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
