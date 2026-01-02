"""
Application settings with environment variable support.

Configuration is loaded from environment variables with optional .env file.
Azure OpenAI settings override OpenAI when fully configured.
"""

from functools import lru_cache
from pathlib import Path
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

    # Replicate settings (for Grounding DINO and SAM 2)
    replicate_api_token: Optional[str] = Field(
        default=None, alias="REPLICATE_API_TOKEN"
    )

    # Firebase settings
    google_application_credentials: Optional[str] = Field(
        default=None, alias="GOOGLE_APPLICATION_CREDENTIALS"
    )
    firebase_project_id: Optional[str] = Field(
        default=None, alias="FIREBASE_PROJECT_ID"
    )

    # Vision pipeline settings
    detection_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    crop_padding: float = Field(default=0.2, ge=0.0, le=1.0)
    crops_dir: Optional[Path] = Field(default=None, alias="VISION_CROPS_DIR")

    # VLM behavior settings
    vlm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    vlm_max_tokens: int = Field(default=500, ge=1)

    # Review thresholds
    low_confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)

    def is_azure_configured(self) -> bool:
        """Check if Azure OpenAI is fully configured."""
        return all([
            self.azure_openai_endpoint,
            self.azure_openai_api_key,
            self.azure_openai_deployment_name,
        ])

    def is_replicate_configured(self) -> bool:
        """Check if Replicate is configured for detection models."""
        return self.replicate_api_token is not None

    def get_vlm_api_key(self) -> Optional[str]:
        """Get the appropriate VLM API key based on configuration."""
        if self.is_azure_configured():
            return self.azure_openai_api_key
        return self.openai_api_key


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
