"""Writes one ``llm_calls`` row per paid call, in its own short transaction: the ledger.

The row is written right after the call returns (or fails after reaching the model),
independently of the activity's work transaction: the tokens were spent even if the
activity later rolls back or is retried, and the study budget is a SUM over these rows
(BUILD_PLAN section 3), so a rolled-back record would let a retried activity overspend.

What a row holds (EVAL_REPORT section 38; the columns of :class:`basis.db.models.LlmCall`,
named after the OpenTelemetry GenAI semantic conventions where one exists): which component
called (stage, activity and attempt, Python module and function, prompt name, version and
sha256, worker build), which model it asked for and which the provider says answered (with
the response id and the endpoint), the request parameters as sent, what the call was about
(``entity_type``/``entity_id``), the tokens of every kind as reported, the cost with the
price row and table version it came from, the latency, the finish reason, and references to
the stored request and response. Rows of ``kind`` other than ``chat`` are the non-token
paid steps (an Azure Document Intelligence analysis per page, a SAM 3 prediction per
second): :meth:`Recorder.record_step`.

Payloads: the request (system prompt, messages as sent with every image replaced by its ref,
media type, byte size and sha256, the tool definitions, the response schema) and the raw
response go to ``studies/<study id>/llm/<call id>.request.json`` / ``.response.json`` through
the storage abstraction; the row keeps each object's URI and sha256. Anything shaped like an
API key is redacted before writing (:func:`basis.llm.errors.redact_api_keys`); credentials
never reach the payload builder in the first place (headers are not recorded). A storage
failure is logged and the row is still written, without references: the spend must be
counted even when the archive is unavailable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.repositories.llm_call import LlmCallRepository
from basis.db.session import session_scope
from basis.llm.cost import COST_QUANTUM, PRICING_VERSION, cost_decimal, priced_as
from basis.llm.errors import redact_api_keys
from basis.llm.provider import Usage
from basis.storage.base import SCHEME, Storage, StorageError

logger = logging.getLogger("basis.llm.recorder")

KIND_CHAT = "chat"
KIND_DOCUMENT_ANALYSIS = "document_analysis"
KIND_LOCALIZATION = "localization"
KIND_SEGMENTATION = "segmentation"
KINDS = (KIND_CHAT, KIND_DOCUMENT_ANALYSIS, KIND_LOCALIZATION, KIND_SEGMENTATION)
STATUS_OK = "ok"
STATUS_ERROR = "error"
CONTENT_TYPE = "application/json"
MAX_ERROR = 1000
NO_USAGE = Usage(input_tokens=0, output_tokens=0)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """Everything a paid call is recorded with, before the recorder prices and stores it.

    Build one with :func:`basis.llm.call.chat_entry` for a model call; the activities build
    the non-token kinds directly. ``request_payload``/``response_payload`` are the documents
    to archive (``None``: nothing to store).
    """

    study_id: uuid.UUID
    run_id: uuid.UUID | None
    activity: str | None
    provider: str
    model: str
    usage: Usage = NO_USAGE
    latency_ms: int | None = None
    prompt_version: str | None = None
    kind: str = KIND_CHAT
    status: str = STATUS_OK
    error: str | None = None
    stage: str | None = None
    revision: int | None = None
    activity_attempt: int | None = None
    component: str | None = None
    prompt_name: str | None = None
    prompt_sha256: str | None = None
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    requested_model: str | None = None
    endpoint: str | None = None
    response_id: str | None = None
    request_id: str | None = None
    finish_reason: str | None = None
    request_params: dict[str, Any] = field(default_factory=dict)
    # Non-token kinds: what was bought and at what unit price; ``cost_usd`` is what the
    # provider charged (or what the budget was charged), stored as given.
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price_usd: Decimal | None = None
    cost_usd: Decimal | None = None
    elapsed_ms: int | None = None
    request_payload: dict[str, Any] | None = None
    response_payload: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CallRecord:
    """What was recorded: the entry, priced, with its id and payload references."""

    id: uuid.UUID
    entry: LedgerEntry
    cost_usd: Decimal
    price_row: str | None
    pricing_version: str | None
    request_uri: str | None = None
    request_sha256: str | None = None
    response_uri: str | None = None
    response_sha256: str | None = None

    # The fields tests and callers read most, as attributes of the record itself.
    @property
    def study_id(self) -> uuid.UUID:
        return self.entry.study_id

    @property
    def run_id(self) -> uuid.UUID | None:
        return self.entry.run_id

    @property
    def activity(self) -> str | None:
        return self.entry.activity

    @property
    def prompt_version(self) -> str | None:
        return self.entry.prompt_version

    @property
    def provider(self) -> str:
        return self.entry.provider

    @property
    def model(self) -> str:
        return self.entry.model

    @property
    def usage(self) -> Usage:
        return self.entry.usage

    @property
    def latency_ms(self) -> int | None:
        return self.entry.latency_ms

    @property
    def kind(self) -> str:
        return self.entry.kind

    @property
    def status(self) -> str:
        return self.entry.status

    @property
    def component(self) -> str | None:
        return self.entry.component

    @property
    def entity_type(self) -> str | None:
        return self.entry.entity_type

    @property
    def entity_id(self) -> uuid.UUID | None:
        return self.entry.entity_id

    @property
    def request_params(self) -> dict[str, Any]:
        return self.entry.request_params


class Recorder(Protocol):
    async def record(self, entry: LedgerEntry) -> CallRecord:
        """Persist the call, priced, and return what was recorded."""
        ...


# --------------------------------------------------------------------------- pricing


def price_entry(entry: LedgerEntry) -> tuple[Decimal, str | None, str | None]:
    """``(cost, price_row, pricing_version)`` of an entry: a model call is priced from its
    usage by the table (:func:`basis.llm.cost.cost_decimal`); a non-token kind keeps the
    cost it was given (what the provider metered), or ``quantity x unit_price`` when the
    caller gave none, and names no price row."""
    if entry.kind == KIND_CHAT:
        return cost_decimal(entry.model, entry.usage), priced_as(entry.model), PRICING_VERSION
    if entry.cost_usd is not None:
        cost = Decimal(entry.cost_usd)
    elif entry.quantity is not None and entry.unit_price_usd is not None:
        cost = Decimal(entry.quantity) * Decimal(entry.unit_price_usd)
    else:
        cost = Decimal(0)
    return cost.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP), None, PRICING_VERSION


# --------------------------------------------------------------------------- payloads


def payload_bytes(payload: dict[str, Any]) -> bytes:
    """Canonical JSON of a payload with API-key-shaped strings redacted."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return redact_api_keys(text).encode("utf-8")


def payload_uri(bucket: str, study_id: uuid.UUID, call_id: uuid.UUID, part: str) -> str:
    return f"{SCHEME}{bucket}/studies/{study_id}/llm/{call_id}.{part}.json"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


Put = Callable[[str, bytes, str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PayloadRefs:
    request_uri: str | None = None
    request_sha256: str | None = None
    response_uri: str | None = None
    response_sha256: str | None = None


def priced_payload(record: CallRecord, part: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The stored document: the payload under the call id, with the ledger's pricing on
    the response part (cost, price row, pricing version), so each file lists its model,
    parameters, tokens and cost on its own."""
    document: dict[str, Any] = {"call_id": str(record.id), **payload}
    if part == "response":
        document["cost_usd"] = str(record.cost_usd)
        document["price_row"] = record.price_row
        document["pricing_version"] = record.pricing_version
    return document


async def archive_payloads(record: CallRecord, bucket: str, put: Put) -> PayloadRefs:
    """Write the entry's request and response payloads through ``put`` and return their
    references; a payload whose write fails is logged and left unreferenced."""
    entry = record.entry
    refs: dict[str, str] = {}
    parts = (("request", entry.request_payload), ("response", entry.response_payload))
    for part, payload in parts:
        if payload is None:
            continue
        data = payload_bytes(priced_payload(record, part, payload))
        uri = payload_uri(bucket, entry.study_id, record.id, part)
        try:
            await put(uri, data, CONTENT_TYPE)
        except StorageError:
            logger.error("ledger payload %s not stored; the row is written without it", uri)
            continue
        refs[f"{part}_uri"] = uri
        refs[f"{part}_sha256"] = sha256_hex(data)
    return PayloadRefs(**refs)


def with_refs(record: CallRecord, refs: PayloadRefs) -> CallRecord:
    return replace(
        record,
        request_uri=refs.request_uri,
        request_sha256=refs.request_sha256,
        response_uri=refs.response_uri,
        response_sha256=refs.response_sha256,
    )


# --------------------------------------------------------------------------- recorders


class LlmCallRecorder:
    """The production recorder: an ``llm_calls`` row through :class:`LlmCallRepository`,
    the payloads through ``storage`` (none archived when no storage is given)."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        storage: Storage | None = None,
        bucket: str | None = None,
        build_sha: str | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._storage = storage if bucket else None
        self._bucket = bucket
        self._build_sha = build_sha

    async def record(self, entry: LedgerEntry) -> CallRecord:
        cost, price_row, pricing_version = price_entry(entry)
        record = CallRecord(
            id=uuid.uuid4(),
            entry=entry,
            cost_usd=cost,
            price_row=price_row,
            pricing_version=pricing_version,
        )
        if self._storage is not None and self._bucket is not None:
            refs = await archive_payloads(record, self._bucket, self._storage.put)
            record = with_refs(record, refs)
        async with session_scope(self._sessionmaker) as session:
            await LlmCallRepository(session).add_record(record, build_sha=self._build_sha)
        return record

    async def spent_usd(self, study_id: uuid.UUID) -> Decimal:
        async with session_scope(self._sessionmaker) as session:
            return await LlmCallRepository(session).spent_usd(study_id)


@dataclass
class MemoryRecorder:
    """Keeps records in a list and payloads in a dict (unit tests that need no database)."""

    records: list[CallRecord] = field(default_factory=list)
    payloads: dict[str, bytes] = field(default_factory=dict)
    bucket: str = "memory"

    async def record(self, entry: LedgerEntry) -> CallRecord:
        cost, price_row, pricing_version = price_entry(entry)
        record = CallRecord(
            id=uuid.uuid4(),
            entry=entry,
            cost_usd=cost,
            price_row=price_row,
            pricing_version=pricing_version,
        )

        async def keep(uri: str, data: bytes, content_type: str) -> None:
            self.payloads[uri] = data

        record = with_refs(record, await archive_payloads(record, self.bucket, keep))
        self.records.append(record)
        return record

    def spent(self) -> Decimal:
        return sum((r.cost_usd for r in self.records), Decimal(0))
