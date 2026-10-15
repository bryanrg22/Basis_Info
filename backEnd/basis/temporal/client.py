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


async def connect(settings: Settings) -> Client:
    config = client_connect_config(settings)
    return await Client.connect(config.target_host, **config.connect_kwargs())
