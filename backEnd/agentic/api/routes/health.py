"""Health check endpoints."""

from fastapi import APIRouter

from ...config.settings import get_settings


router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health_check():
    """Basic health check."""
    return {"status": "healthy", "service": "basis-agentic"}


@router.get("/ready")
async def readiness_check():
    """
    Readiness check - verifies all dependencies are available.
    """
    settings = get_settings()

    checks = {
        "llm_configured": bool(settings.openai_api_key or settings.is_azure_configured()),
        "langsmith_configured": settings.is_langsmith_configured(),
        "firebase_configured": bool(
            settings.google_application_credentials or settings.firebase_project_id
        ),
    }

    all_ready = all(checks.values())

    return {
        "status": "ready" if all_ready else "degraded",
        "checks": checks,
    }


@router.get("/config")
async def config_info():
    """
    Configuration info (non-sensitive).
    """
    settings = get_settings()

    return {
        "llm_provider": "azure" if settings.is_azure_configured() else "openai",
        "llm_model": settings.openai_model,
        "langsmith_project": settings.langchain_project if settings.is_langsmith_configured() else None,
        "langsmith_enabled": settings.is_langsmith_configured(),
    }
