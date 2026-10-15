"""Build a Temporal client from settings.

Temporal Cloud uses an API key over TLS; the local dev server uses neither. The
pydantic data converter is used everywhere so the contract models in
``basis.workflow.contracts`` round-trip without custom code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from basis.config import Settings


@dataclass(frozen=True)
class ClientConnectConfig:
    target_host: str
    namespace: str
    api_key: str | None
    tls: bool

    def connect_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "namespace": self.namespace,
            "data_converter": pydantic_data_converter,
        }
        if self.api_key is not None:
            kwargs["api_key"] = self.api_key
            kwargs["tls"] = True
        else:
            kwargs["tls"] = self.tls
        return kwargs


def client_connect_config(settings: Settings) -> ClientConnectConfig:
    api_key = settings.temporal_api_key.get_secret_value() if settings.temporal_api_key else None
    return ClientConnectConfig(
        target_host=settings.temporal_address,
        namespace=settings.temporal_namespace,
        api_key=api_key,
        tls=api_key is not None,
    )


def build_id(settings: Settings) -> str:
    """The worker build id: explicit setting, else the git sha the image was built from."""
    return settings.temporal_build_id or settings.git_sha


def temporal_configured(settings: Settings) -> bool:
    """True when a Temporal address is set (``TEMPORAL_ADDRESS=""`` turns Temporal off)."""
    return bool(settings.temporal_address.strip())


async def connect(settings: Settings, *, lazy: bool = False) -> Client:
    """Connect to Temporal.

    ``lazy=True`` returns at once without a network call; the connection is made on the
    first request and retried on every later request until it succeeds (verified against
    the SDK: a refused first connect does not poison the client). The API and the outbox
    relay use it so they start, and keep serving what does not need Temporal, while
    Temporal is unreachable.
    """
    config = client_connect_config(settings)
    return await Client.connect(config.target_host, lazy=lazy, **config.connect_kwargs())
