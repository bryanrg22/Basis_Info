"""
Application settings with environment variable support.

Configuration is loaded from environment variables with optional .env file.
Azure OpenAI settings override OpenAI when fully configured.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI settings (default provider)
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")

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
    azure_openai_api_version: str = Field(
        default="2024-02-15-preview", alias="AZURE_OPENAI_API_VERSION"
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


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
