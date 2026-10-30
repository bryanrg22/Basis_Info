"""``ledger_columns`` is the one mapping from a priced record to the ``llm_calls`` row: the
production repository and the evaluation ledger both write it, so its key set is the
table's column set (minus ``created_at``, which the database fills)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from basis.db.models import LlmCall
from basis.llm.provider import Usage
from basis.llm.recorder import (
    KIND_LOCALIZATION,
    CallRecord,
    LedgerEntry,
    ledger_columns,
    price_entry,
)


def _record(**changes: object) -> CallRecord:
    values: dict[str, object] = {
        "study_id": uuid.uuid4(),
        "run_id": None,
        "activity": "analyze_photo",
        "provider": "openai",
        "model": "gpt-4o-mini-2024-07-18",
        "requested_model": "gpt-4o-mini",
        "usage": Usage(
            input_tokens=1000,
            output_tokens=50,
            cache_read_input_tokens=200,
            reasoning_output_tokens=10,
        ),
        "prompt_version": "photo_countables/v3",
        "request_params": {
            "reasoning_effort": "low",
            "max_output_tokens": 2048,
            "temperature": 0.0,
            "response_format": "ModelPhotoAnalysis",
            "image_count": 1,
            "image_bytes": 12345,
        },
    }
    values.update(changes)
    entry = LedgerEntry(**values)  # type: ignore[arg-type]
    cost, price_row, pricing_version = price_entry(entry)
    return CallRecord(
        id=uuid.uuid4(),
        entry=entry,
        cost_usd=cost,
        price_row=price_row,
        pricing_version=pricing_version,
        request_uri="gs://b/studies/x/llm/y.request.json",
        request_sha256="a" * 64,
    )


def test_the_key_set_is_the_table_without_created_at() -> None:
    columns = {c.name for c in LlmCall.__table__.columns}
    assert set(ledger_columns(_record(), build_sha="abc")) == columns - {"created_at"}


def test_every_column_carries_the_entry_and_the_pricing() -> None:
    record = _record()
    row = ledger_columns(record, build_sha="abc1234")
    assert row["id"] == record.id
    assert row["build_sha"] == "abc1234"
    assert row["prompt_name"] == "photo_countables"
    assert row["requested_model"] == "gpt-4o-mini"
    assert row["model"] == "gpt-4o-mini-2024-07-18"
    assert (row["input_tokens"], row["output_tokens"]) == (1000, 50)
    assert (row["cache_read_input_tokens"], row["reasoning_output_tokens"]) == (200, 10)
    assert (row["reasoning_effort"], row["max_output_tokens"], row["temperature"]) == (
        "low",
        2048,
        0.0,
    )
    assert (row["response_format"], row["image_count"], row["image_bytes"]) == (
        "ModelPhotoAnalysis",
        1,
        12345,
    )
    assert row["cost_usd"] == record.cost_usd and isinstance(row["cost_usd"], Decimal)
    assert row["price_row"] == "gpt-4o-mini"
    assert row["pricing_version"] == record.pricing_version
    assert row["request_uri"] == record.request_uri
    assert row["response_uri"] is None


def test_a_metered_step_fills_quantity_and_unit_and_no_token_parameters() -> None:
    record = _record(
        kind=KIND_LOCALIZATION,
        usage=Usage(input_tokens=0, output_tokens=0),
        prompt_version=None,
        request_params={},
        quantity=Decimal("2.5"),
        unit="second",
        unit_price_usd=Decimal("0.001"),
        cost_usd=Decimal("0.0025"),
    )
    row = ledger_columns(record)
    assert (row["kind"], row["quantity"], row["unit"]) == (
        KIND_LOCALIZATION,
        Decimal("2.5"),
        "second",
    )
    assert row["cost_usd"] == Decimal("0.002500")
    assert row["prompt_name"] is None and row["reasoning_effort"] is None
    assert row["request_params"] is None  # an empty dict is stored as NULL
    assert row["build_sha"] is None
