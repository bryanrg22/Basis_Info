"""Response bodies of the paid-call ledger: ``GET /v1/studies/{id}/spend`` and ``/calls``
(EVAL_REPORT section 40; docs/api/SPEND.md)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from basis.api.schemas.common import ResponseModel


class SpendLineOut(ResponseModel):
    """One line of a rollup: the group's key and what it spent."""

    key: str | None = Field(
        description="The group: a stage, activity, component (`module.function`), model or "
        "kind; null for rows recorded without one (rows written before the ledger)."
    )
    calls: int = Field(description="Paid calls, error rows included.")
    errors: int = Field(description="Calls that reached the provider and failed (still billed).")
    input_tokens: int = Field(description="Prompt tokens, cached ones included.")
    cache_read_input_tokens: int = Field(description="Of `input_tokens`, served from a cache.")
    cache_creation_input_tokens: int = Field(
        description="Of `input_tokens`, written to a cache (Anthropic)."
    )
    output_tokens: int = Field(description="Answer tokens, reasoning included.")
    reasoning_output_tokens: int = Field(
        description="Of `output_tokens`, spent reasoning (OpenAI reports it; Anthropic does not)."
    )
    cost_usd: Decimal = Field(description="Dollars, to the microdollar.")
    latency_ms_total: int = Field(description="Sum of the calls' answering round trips.")
    latency_ms_max: int | None = Field(description="The slowest call's round trip.")
    quantity: Decimal | None = Field(
        description="For non-token kinds: pages analyzed or predict seconds, summed."
    )


class StudySpendOut(ResponseModel):
    study_id: uuid.UUID
    total: SpendLineOut = Field(description="The whole study (`key` null).")
    by_stage: list[SpendLineOut] = Field(
        description="By the study's stage at the time of the call."
    )
    by_activity: list[SpendLineOut] = Field(
        description="By Temporal activity (`object_prompt` for the API's manual SAM 3 prompts)."
    )
    by_component: list[SpendLineOut] = Field(
        description="By the calling Python module and function."
    )
    by_model: list[SpendLineOut] = Field(
        description="By the model that answered (dated snapshot on OpenAI)."
    )
    by_kind: list[SpendLineOut] = Field(
        description="`chat`, `document_analysis`, `localization`, `segmentation`."
    )


class CallOut(ResponseModel):
    """One row of the ledger: everything about one paid call."""

    id: uuid.UUID
    created_at: datetime
    kind: str
    status: str = Field(
        description="`ok`, or `error` for a call that reached the provider and failed."
    )
    error: str | None
    run_id: uuid.UUID | None
    stage: str | None
    revision: int | None
    activity: str | None
    activity_attempt: int | None
    component: str | None = Field(description="The Python `module.function` that made the call.")
    entity_type: str | None = Field(description="`photo`, `room`, `component` or `appraisal`.")
    entity_id: uuid.UUID | None
    provider: str
    endpoint: str | None
    requested_model: str | None
    model: str = Field(description="The model the provider says answered.")
    response_id: str | None
    request_id: str | None
    finish_reason: str | None
    prompt_version: str | None = Field(description="`<name>/v<N>` of the released prompt.")
    prompt_name: str | None
    prompt_sha256: str | None
    reasoning_effort: str | None
    max_output_tokens: int | None
    temperature: float | None
    response_format: str | None = Field(description="The response schema's name.")
    image_count: int | None
    image_bytes: int | None
    request_params: dict[str, Any] | None = Field(
        description="Every parameter as sent (tool names included), never the content."
    )
    input_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    quantity: Decimal | None
    unit: str | None
    unit_price_usd: Decimal | None
    cost_usd: Decimal
    price_row: str | None = Field(
        description="The price-table row the cost came from; `unknown` when the most "
        "expensive row was charged."
    )
    pricing_version: str | None
    latency_ms: int | None = Field(description="The answering round trip.")
    elapsed_ms: int | None = Field(
        description="Wall time of the call, retries and fallback included."
    )
    build_sha: str | None
    request_uri: str | None = Field(
        description="The stored request (messages as sent, images by ref and size)."
    )
    request_sha256: str | None
    response_uri: str | None = Field(description="The stored raw response.")
    response_sha256: str | None


class CallsPageOut(ResponseModel):
    items: list[CallOut] = Field(description="Oldest first.")
    next_cursor: str | None = Field(
        description="Pass as `cursor` for the next page; null on the last."
    )
