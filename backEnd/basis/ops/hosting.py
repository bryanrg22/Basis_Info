"""Hosting labels for ``GET /v1/ops/health``: where a process or a dependency runs, read
from the effective runtime and reduced to a display label (docs/api/OPS_HEALTH.md section
2). A label never carries a host name, an address, a path or a credential: the developer page
shows it to platform operators, and the worker writes it into its heartbeat report.

Each function answers ``None`` when the runtime does not establish the hosting: a label is a
fact about the process, never a guess from a service name (``temporal`` alone does not mean
Temporal Cloud).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from basis.config import Settings

CLOUD_RUN_ENV: Final = "K_SERVICE"  # set on every Cloud Run instance (service name)
DOCKERENV: Final = Path("/.dockerenv")
TEMPORAL_CLOUD_SUFFIX: Final = ".tmprl.cloud"
CLOUD_RUN_SUFFIX: Final = ".run.app"
LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", "host.docker.internal"})


def process_hosting(environ: Mapping[str, str] | None = None, dockerenv: Path = DOCKERENV) -> str:
    """Where this process runs: Cloud Run (its ``K_SERVICE`` variable), a Docker container
    (``/.dockerenv``), or a local process."""
    env = os.environ if environ is None else environ
    if env.get(CLOUD_RUN_ENV):
        return "Google Cloud Run"
    if dockerenv.exists():
        return "Docker container"
    return "Local process"


HOST_SHAPE: Final = re.compile(r"^[A-Za-z0-9._\-\[\]:]+$")


def _host_of(url: str) -> str | None:
    try:
        parts = urlsplit(url if "//" in url else f"//{url}")
    except ValueError:
        return None
    host = parts.hostname
    return host if host and HOST_SHAPE.match(host) else None


def database_hosting(settings: Settings) -> str | None:
    """Cloud SQL (through the connector or its Unix socket), a local server, a self-hosted
    server, or not established."""
    if settings.cloud_sql_instance:
        return "Google Cloud SQL"
    if not settings.database_url:
        return None
    if "/cloudsql/" in settings.database_url:
        return "Google Cloud SQL"
    host = _host_of(settings.database_url)
    if host is None:
        return None
    if host in LOCAL_HOSTS:
        return "Local PostgreSQL"
    return "Self-hosted PostgreSQL"


def temporal_hosting(settings: Settings) -> str | None:
    """Temporal Cloud (its address or an API key), a local dev server, or self-hosted."""
    host = _host_of(settings.temporal_address)
    if settings.temporal_api_key is not None or (host or "").endswith(TEMPORAL_CLOUD_SUFFIX):
        return "Temporal Cloud"
    if host is None:
        return None
    if host in LOCAL_HOSTS:
        return "Local Temporal server"
    return "Self-hosted Temporal"


def storage_hosting(adapter: str) -> str:
    return "Google Cloud Storage" if adapter == "Google Cloud Storage" else "Local disk"


def firebase_hosting(adapter: str) -> str:
    return "Firebase Auth emulator" if adapter == "emulator" else "Firebase Auth"


def localizer_hosting(url: str | None) -> str | None:
    """The self-hosted SAM 3 server: Cloud Run (a ``run.app`` host), local, or self-hosted."""
    if not url:
        return None
    host = _host_of(url)
    if host is None:
        return "Self-hosted server"
    if host.endswith(CLOUD_RUN_SUFFIX):
        return "Google Cloud Run"
    if host in LOCAL_HOSTS:
        return "Local server"
    return "Self-hosted server"
