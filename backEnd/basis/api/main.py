"""Process entry point: ``uvicorn basis.api.main:app``."""

from __future__ import annotations

from basis.api.app import create_app
from basis.config import get_settings
from basis.obs.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level, json=settings.env != "dev")
app = create_app(settings)
