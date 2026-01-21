"""
FastAPI application for Basis agentic workflow.

Provides REST endpoints for:
- Starting/resuming workflows
- Triggering specific stages
- Checking workflow status
- Health checks
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from ..config.settings import get_settings
from ..observability.tracing import configure_langsmith
from .routes import workflow_router, health_router, checkpoints_router
from fastapi.exceptions import HTTPException
from .exceptions import APIError, api_error_handler, generic_exception_handler, http_exception_handler
from .rate_limit import limiter, rate_limit_exceeded_handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    configure_langsmith()
    yield
    # Shutdown (nothing to clean up)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Basis Agentic Workflow API",
        description="Stage-gated agentic workflow for cost segregation studies",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Add rate limiter (with Slack alerting)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    # Add exception handlers (with Slack alerting for errors)
    app.add_exception_handler(HTTPException, http_exception_handler)  # Auth errors, etc.
    app.add_exception_handler(APIError, api_error_handler)  # Custom API errors
    app.add_exception_handler(Exception, generic_exception_handler)  # Catch-all

    # Include routers
    app.include_router(health_router)
    app.include_router(workflow_router)
    app.include_router(checkpoints_router)  # Phase 6: Checkpoint history endpoints

    @app.get("/")
    async def root():
        """Root endpoint."""
        return {
            "service": "basis-agentic",
            "version": "0.1.0",
            "docs": "/docs",
        }

    return app


# Create the app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "agentic.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
