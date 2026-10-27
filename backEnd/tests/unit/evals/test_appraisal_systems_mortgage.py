"""``azure_1004`` mapping, the ``production_plus_1004`` merge rules, ``checkbox_context``, the
production system unchanged, the key-value-pair regions, and recording/replay of the 1004
analysis (fabricated SDK-shaped results; no network)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from basis.extraction.fallback import FallbackAnswer, FallbackValue
from basis.extraction.pipeline import extract_appraisal
from basis.extraction.results import Candidate, ExtractedField
from basis.extraction.sections import azure_candidates
from basis.llm import ScriptedProvider, ScriptedTurn
from basis.llm.errors import ProviderBadResponse
from evals.appraisal import recordings
from evals.appraisal.normalize import ENUMS
from evals.appraisal.systems.base import CHECKBOX_KEYS, SINGLE_CHECKBOX_KEYS, ModelChoice
from evals.appraisal.systems.mortgage import (
    FIELD_MAP,
    Azure1004,
    ProductionPlus1004,
    map_1004,
    merge_1004,
)
from evals.appraisal.systems.production import (
    CheckboxContext,
    Production,
    kv_regions,
    production_context,
)

from .appraisal_systems_support import (
    RawClient,
    RawModel,
    make_env,
    pdf_bytes,
    raw_layout,
    region_of,
    token_box,
)

FORM = [
    "Subject",
    "Property Address 1290 Example Way",
    "Occupant :selected: Owner :unselected: Tenant :unselected: Vacant",
    "Site",
    "Water :selected: Public :unselected: Other Municipal",
    "Foundation Walls Concrete",
    "Contract",
    "Contract Price $ 412,000",
]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def node(
    kind: str, confidence: float = 0.9, box: tuple[float, ...] | None = None, **values: Any
) -> dict[str, Any]:
    out: dict[str, Any] = {"type": kind, "confidence": confidence, **values}
    if box is not None:
        out["boundingRegions"] = [region_of(1, box)]  # type: ignore[arg-type]
    return out


def occupant_box() -> tuple[float, float, float, float]:
    tokens = FORM[2].split(" ")
    left, top = token_box(2, tokens, 0)[:2]
    right, bottom = token_box(2, tokens, len(tokens) - 1)[2:]
    return (left, top, right, bottom)


def mortgage_raw(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "Subject": node(
            "object",
            valueObject={
                "PropertyAddress": node(
                    "address",
                    0.95,
                    content="1290 Example Way, Springfield, IL 62701",
                    valueAddress={
                        "streetAddress": "1290 Example Way",
                        "city": "Springfield",
                        "state": "IL",
                        "postalCode": "62701",
                    },
                    box=token_box(1, FORM[1].split(" "), 2),
                ),
                "OccupantType": node(
                    "selectionGroup",
                    0.88,
                    content=":selected: Owner :unselected: Tenant :unselected: Vacant",
                    valueSelectionGroup=["Owner"],
                    box=occupant_box(),
                ),
                "TaxYear": node("integer", 0.97, content="2025", valueInteger=2025),
                "AssignmentType": node("selectionGroup", 0.7, valueSelectionGroup=["Other"]),
                "OtherAssignment": node("string", 0.7, content="Estate planning"),
            },
        ),
        "Contract": node(
            "object",
            valueObject={
                "ContractPrice": node("number", 0.93, content="$ 412,000", valueNumber=412000),
                "ContractDate": node("date", 0.9, valueDate="2026-03-01"),
            },
        ),
        "Site": node(
            "object",
            valueObject={
                "Utilities": node(
                    "object",
                    valueObject={
                        "WaterType": node("selectionGroup", 0.8, valueSelectionGroup=["Public"])
                    },
                ),
            },
        ),
        "Improvements": node(
            "object",
            valueObject={
                "FoundationType": node(
                    "selectionGroup", 0.8, valueSelectionGroup=["Crawl Space", "Full Basement"]
                ),
                "YearBuilt": node("integer", 0.9, content="unknown"),  # unparseable
            },
        ),
        **overrides,
    }
    return raw_layout(
        [FORM],
        model_id="prebuilt-mortgage.us.1004",
        documents=[{"docType": "mortgage.us.1004", "fields": fields, "confidence": 0.9}],
    )


def test_checkbox_keys_cover_the_scored_enums() -> None:
    enum_keys = {k for k in ENUMS if not k.startswith("improvements.garage_type")}
    assert enum_keys <= CHECKBOX_KEYS
    assert "site.special_flood_hazard" in SINGLE_CHECKBOX_KEYS
    assert all(key.count(".") == 1 for _, key, _ in FIELD_MAP)


def test_map_1004_values_confidences_regions() -> None:
    fields, regions, dropped = map_1004(mortgage_raw())
    assert fields["subject.property_address"].value_text == "1290 Example Way"
    assert (fields["subject.city"].value_text, fields["subject.state"].value_text) == (
        "Springfield",
        "IL",
    )
    assert fields["subject.zip"].value_text == "62701"
    assert fields["subject.occupant"].value_text == "Owner"
    assert fields["subject.occupant"].confidence == 0.88  # Azure's own field confidence
    assert fields["subject.assignment_type"].value_text == "Other: Estate planning"
    assert fields["subject.tax_year"].value_num == 2025
    assert fields["listing_and_contract.contract_price"].value_num == 412000
    assert fields["listing_and_contract.contract_date"].value_date.isoformat() == "2026-03-01"  # type: ignore[union-attr]
    assert fields["site.utilities_water"].value_text == "Public"
    assert fields["improvements.foundation_type"].value_text == "Crawl Space, Full Basement"
    assert all(item.source == "azure" for item in fields.values())
    assert "improvements.year_built" in dropped and "improvements.year_built" not in fields
    assert "improvements.gla_sqft" not in fields  # not in the 1004 schema
    # The selection group's box narrows to the checked label's words.
    [occupant] = regions["subject.occupant"]
    owner = token_box(2, FORM[2].split(" "), 2)
    assert occupant == {"page": 1, "bbox": pytest.approx([round(v, 4) for v in owner], abs=1e-4)}
    assert regions["listing_and_contract.contract_price"] == []  # the field has no polygon
    assert map_1004({"documents": [], "pages": []}) == ({}, {}, {})


def f(key: str, raw: str, confidence: float, source: str = "azure") -> ExtractedField:
    return ExtractedField.from_candidate(
        Candidate(key=key, raw_text=raw, page=1, confidence=confidence, source=source)  # type: ignore[arg-type]
    )


def test_merge_rules() -> None:
    production = {
        "subject.occupant": f("subject.occupant", "Owner", 0.6, "llm"),  # agrees
        "site.utilities_water": f("site.utilities_water", "Municipal", 0.97),  # checkbox, disagrees
        "listing_and_contract.contract_price": f(
            "listing_and_contract.contract_price", "$412,000", 0.98
        ),  # typed, disagrees
        "subject.borrower": f("subject.borrower", "Jane Doe", 0.9),  # production only
    }
    mortgage = {
        "subject.occupant": f("subject.occupant", "owner", 0.88),
        "site.utilities_water": f("site.utilities_water", "Public", 0.8),
        "listing_and_contract.contract_price": f(
            "listing_and_contract.contract_price", "$ 421,000", 0.93
        ),
        "subject.city": f("subject.city", "Springfield", 0.7),  # 1004 only
    }
    merged, origins, issues = merge_1004(production, mortgage)
    assert merged["subject.occupant"].confidence == 0.88  # max of the two
    assert merged["subject.occupant"].corroborated_by == ("azure_1004",)
    assert origins["subject.occupant"] == "llm+azure_1004"
    assert merged["site.utilities_water"].value_text == "Public"  # checkbox: the 1004 wins
    assert merged["site.utilities_water"].confidence == 0.5
    assert merged["listing_and_contract.contract_price"].value_num == 412000  # production kept
    assert merged["listing_and_contract.contract_price"].confidence == 0.5
    assert merged["subject.borrower"].confidence == 0.9
    assert (merged["subject.city"].value_text, origins["subject.city"]) == (
        "Springfield",
        "azure_1004",
    )
    assert {i.keys[0]: i.severity for i in issues} == {
        "site.utilities_water": "warning",
        "listing_and_contract.contract_price": "error",
    }
    assert all(i.rule == "sources_disagree" for i in issues)


def test_azure_1004_system() -> None:
    model = RawModel(mortgage_raw())
    env = make_env(mortgage=model)
    output = run(Azure1004().run(env))
    assert output.result.method == "azure_1004"
    assert output.result.fields["subject.occupant"].value_text == "Owner"
    assert output.origins["subject.occupant"] == "azure_1004"
    assert env.sources.pages_by_model == {"prebuilt-mortgage.us.1004": 1}
    run(Azure1004().run(env))
    assert model.calls == 1  # analyzed once per document


# --------------------------------------------------------------------------- production


def kv(key: str, value: str, line: int, index: int, length: int) -> dict[str, Any]:
    tokens = FORM[line].split(" ")
    first, last = token_box(line, tokens, index), token_box(line, tokens, index + length - 1)
    return {
        "key": {
            "content": key,
            "boundingRegions": [region_of(1, token_box(line, tokens, 0))],
            "spans": [],
        },
        "value": {
            "content": value,
            "boundingRegions": [region_of(1, (first[0], first[1], last[2], last[3]))],
            "spans": [],
        },
        "confidence": 0.97,
    }


PAIRS = [
    kv("Contract Price", "$ 412,000", 7, 2, 2),
    kv("Foundation", "Walls Concrete", 5, 1, 2),  # the wrong-box pair of the client
    kv("Water", "Municipal", 4, 5, 1),
]


def layout_raw() -> dict[str, Any]:
    return raw_layout([FORM], key_value_pairs=PAIRS)


def test_kv_regions_follow_azure_candidates() -> None:
    raw = layout_raw()
    client = RawClient(raw)
    found = kv_regions(client.result, raw)
    candidates, _ = azure_candidates(client.result)
    assert set(found) == {c.key for c in candidates}
    assert found["listing_and_contract.contract_price"][2] == "$ 412,000"
    page, box, _ = found["listing_and_contract.contract_price"]
    tokens = FORM[7].split(" ")
    assert page == 1 and box == pytest.approx(
        (
            token_box(7, tokens, 2)[0],
            token_box(7, tokens, 2)[1],
            token_box(7, tokens, 3)[2],
            token_box(7, tokens, 3)[3],
        )
    )


def test_production_system_is_extract_appraisal_unchanged() -> None:
    pdf = pdf_bytes([FORM])
    env = make_env(layout=RawClient(layout_raw()), provider=None, pdf=pdf)
    output = run(Production().run(env))
    direct = run(
        extract_appraisal(
            pdf, "doc_a", production_context(make_env(layout=RawClient(layout_raw()), pdf=pdf))
        )
    )
    assert output.regions is None  # today's pipeline keeps no polygons
    assert output.result.model_dump(exclude={"raw"}) == direct.model_dump(exclude={"raw"})


def test_checkbox_context_sends_marks_and_asks_every_checkbox() -> None:
    answer = FallbackAnswer(
        fields=[
            FallbackValue(key="site.utilities_water", value="Public", page=1, confidence=0.9),
            FallbackValue(key="subject.occupant", value="Owner", page=1, confidence=0.9),
        ]
    )
    provider = ScriptedProvider([ScriptedTurn(parsed=answer)], model="gpt-6-luna")
    pdf = pdf_bytes([FORM])
    env = make_env(layout=RawClient(layout_raw()), provider=provider, pdf=pdf)
    output = run(CheckboxContext(ModelChoice()).run(env))
    [request] = provider.requests
    assert request.prompt_version == "appraisal_field_fallback_marks/v1"
    assert (request.model, request.reasoning_effort) == ("gpt-6-luna", "low")  # TEXT_MODEL
    content = request.messages[0].content
    assert "Water :selected: Public :unselected: Other Municipal" in content
    assert "- site.utilities_water:" in content  # Azure DI read it ("Municipal"): asked anyway
    assert all(f"- {key}:" in content for key in CHECKBOX_KEYS)
    water = output.result.fields["site.utilities_water"]
    assert (water.value_text, water.source, water.confidence) == ("Public", "llm", 0.6)
    assert output.result.method.endswith("+checkbox_context")
    # Regions: the Azure DI pair's own value box; a model value located on its page.
    price = output.regions["listing_and_contract.contract_price"]  # type: ignore[index]
    assert price[0]["page"] == 1
    water_box = token_box(4, FORM[4].split(" "), 2)
    [water_region] = output.regions["site.utilities_water"]  # type: ignore[index]
    assert water_region["bbox"] == pytest.approx([round(v, 4) for v in water_box], abs=1e-4)


def test_checkbox_context_overrides_the_model() -> None:
    provider = ScriptedProvider([ScriptedTurn(parsed=FallbackAnswer(fields=[]))], model="x")
    env = make_env(layout=RawClient(layout_raw()), provider=provider, pdf=pdf_bytes([FORM]))
    run(CheckboxContext(ModelChoice("claude-haiku-5-5", "medium")).run(env))
    assert (provider.requests[0].model, provider.requests[0].reasoning_effort) == (
        "claude-haiku-5-5",
        "medium",
    )


def test_production_plus_1004_end_to_end() -> None:
    provider = ScriptedProvider(
        [ScriptedTurn(parsed=FallbackAnswer(fields=[]))], model="gpt-6-luna"
    )
    env = make_env(
        layout=RawClient(layout_raw()),
        mortgage=RawModel(mortgage_raw()),
        provider=provider,
        pdf=pdf_bytes([FORM]),
    )
    output = run(ProductionPlus1004().run(env))
    water = output.result.fields["site.utilities_water"]
    assert water.value_text == "Public" and output.origins["site.utilities_water"] == "azure_1004"
    assert any(
        i.rule == "sources_disagree" and i.keys == ("site.utilities_water",)
        for i in output.result.issues
    )
    assert output.origins["listing_and_contract.contract_price"] == "azure+azure_1004"
    assert (
        output.result.fields["listing_and_contract.contract_price"].corroborated_by[-1]
        == "azure_1004"
    )
    assert output.result.fields["subject.city"].value_text == "Springfield"
    assert output.regions is not None and output.regions["subject.occupant"]  # from the 1004 box
    assert env.sources.pages_by_model == {"prebuilt-layout": 1, "prebuilt-mortgage.us.1004": 1}


# --------------------------------------------------------------------------- recordings


class FakeRaw:
    def __init__(self, raw: dict[str, Any] | Exception) -> None:
        self.raw = raw

    async def analyze_raw(self, pdf_bytes: bytes) -> Any:
        if isinstance(self.raw, Exception):
            raise self.raw
        return recordings.rebuild_sdk_result(self.raw)


def test_1004_record_then_replay(tmp_path: Path) -> None:
    raw = mortgage_raw()
    recorder = recordings.RecordingRawModel(FakeRaw(raw), tmp_path)
    live = run(recorder.analyze(b"pdf"))
    assert (tmp_path / recordings.AZURE_1004_RAW).is_file()
    assert (recorder.calls, recorder.pages) == (1, 1)
    replayed = run(recordings.ReplayRawModel(tmp_path).analyze(b"pdf"))
    assert replayed == live
    assert map_1004(replayed)[0].keys() == map_1004(raw)[0].keys()
    with pytest.raises(recordings.MissingRecording):
        run(recordings.ReplayRawModel(tmp_path / "other").analyze(b"pdf"))


def test_1004_refusal_replays_as_refused(tmp_path: Path) -> None:
    refused = ProviderBadResponse("unsupported", provider="azure_di", status=400)
    with pytest.raises(ProviderBadResponse):
        run(recordings.RecordingRawModel(FakeRaw(refused), tmp_path).analyze(b"pdf"))
    assert json.loads((tmp_path / recordings.AZURE_1004_ERROR).read_text())["status"] == 400
    with pytest.raises(ProviderBadResponse):
        run(recordings.ReplayRawModel(tmp_path).analyze(b"pdf"))


def test_layout_recording_keeps_the_raw(tmp_path: Path) -> None:
    raw = layout_raw()
    client = recordings.RecordingAzureClient(FakeRaw(raw), tmp_path)
    run(client.analyze(b"pdf"))
    assert client.raw is not None and client.raw["content"] == raw["content"]
    replay = recordings.ReplayAzureClient(tmp_path)
    run(replay.analyze(b"pdf"))
    assert replay.raw == client.raw
