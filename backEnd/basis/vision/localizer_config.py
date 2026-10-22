"""The SAM 3 localizer the settings select, for the worker and the API.

The worker localizes every photo (``localize_photo``, long timeouts and minutes of
backoff: Replicate's throttle answers 429 for minutes); the API prompts it while an
engineer waits (a box drawn at the rooms review, ``POST .../objects``), so its client is
interactive: ``timeout_s`` bounds the call and transient errors are retried once
(:func:`basis.vision.localize.build_replicate_localizer`). Both come from the same
settings, so the API prompts the same model version on the same host the photos were
localized with.
"""

from __future__ import annotations

from basis.config import Settings
from basis.vision.localize import (
    Localizer,
    build_cog_localizer,
    build_replicate_localizer,
    google_id_token,
    static_token,
)


def build_localizer(settings: Settings, *, timeout_s: float | None = None) -> Localizer | None:
    """SAM 3 on the self-hosted Cog server at ``LOCALIZER_URL`` when it is set, else on
    Replicate when ``REPLICATE_API_TOKEN`` is set, else None (skipped).

    No budget is bound to the shared localizer: concurrent activities would share it. The
    activity charges each prediction's cost to the study budget instead. ``timeout_s``
    builds the interactive client the API uses.
    """
    if settings.localizer_url:
        token_source = None
        if settings.localizer_token is not None and settings.localizer_token.get_secret_value():
            token_source = static_token(settings.localizer_token.get_secret_value())
        elif settings.localizer_google_auth:
            token_source = google_id_token(settings.localizer_url)
        return build_cog_localizer(
            settings.localizer_url,
            settings.sam3_replicate_version,
            token_source=token_source,
            concurrency=settings.localizer_concurrency,
            usd_per_second=settings.localizer_usd_per_second,
            timeout_s=timeout_s,
        )
    token = settings.replicate_api_token
    if token is None or not token.get_secret_value().strip():
        return None
    return build_replicate_localizer(
        token.get_secret_value(),
        settings.sam3_replicate_version,
        concurrency=settings.localizer_concurrency,
        timeout_s=timeout_s,
    )


__all__ = ["build_localizer"]
