"""The SQLAlchemy metadata matches S1_SPEC §2 (no database needed).

The spec's tables, columns, enum labels, composite foreign keys, unique constraints,
checks and indexes are written out here independently of ``basis.db.models``. Columns
the models add beyond the spec are listed in ``EXTRA_COLUMNS`` with the reason, so any
other difference fails. ``alembic check`` (integration) then proves the migration matches
these models.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    REAL,
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKeyConstraint,
    Numeric,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB

from basis.db import enums
from basis.db.models import APPEND_ONLY_TABLES, PG_ENUMS, UPDATED_AT_TABLES, Base

VERSIONS = Path(__file__).resolve().parents[3] / "basis/db/alembic/versions"
MIGRATION = VERSIONS / "0001_initial.py"

SPEC_TABLES = {
    "orgs": "id name created_at",
    "users": "id firebase_uid email display_name created_at last_seen_at",
    "org_members": "org_id user_id role",
    # v0.9: room_aliases added by migration 0005_room_grouping (EVAL_REPORT section 27).
    # v0.12: components_gate (migration 0009_components_gate).
    # Basis and land allocation: intake (migration 0011_study_intake).
    # Error codes: failure_code (migration 0013_study_events).
    "studies": "id org_id created_by property_name status current_stage workflow_id "
    "room_aliases failure_reason failure_code components_gate intake intake_version created_at "
    "updated_at",
    "documents": "id study_id kind filename content_type bytes sha256 gcs_uri uploaded_by "
    "uploaded_at",
    "document_pages": "document_id page_no gcs_uri",
    "extractions": "id study_id document_id revision method overall_confidence needs_review "
    "raw audit created_at",
    # v0.13: regions (migration 0010_field_regions): where each value is printed.
    "extraction_fields": "id extraction_id study_id section field value_text value_num "
    "value_date confidence source page regions",
    # Provenance: carried_from (migration 0013_study_events).
    "rooms": "id study_id revision name room_type floor source group_key carried_from created_at",
    "photo_rooms": "study_id document_id room_id revision",
    "components": "id study_id revision document_id room_id label material attachment "
    "confidence bbox raw",
    # Firm conventions: convention (migration 0014_firm_conventions).
    "assets": "id study_id revision component_id category bucket recovery_period irs_section "
    "method confidence needs_review review_reason verified convention",
    "takeoffs": "id study_id asset_id quantity unit formula inputs",
    # The recorded cost trail (migration 0018_workspace_contracts).
    "cost_estimates": "id study_id asset_id unit_cost total source_row location_factor cost_year "
    "trail",
    "corpus_versions": "id corpus study_id version embedding_model published_at",
    "chunks": "id corpus_version_id doc_id chunk_key page_start page_end section_path text "
    "embedding table_id",
    "evidence_tables": "id corpus_version_id doc_id page headers rows markdown",
    "citations": "id study_id asset_id chunk_id excerpt verified_at verification_method",
    "reviews": "id study_id stage revision decision reviewer_id comment idempotency_key created_at",
    "corrections": "id study_id entity_type entity_id revision before after reason "
    "reviewer_id created_at",
    "runs": "id study_id workflow_id run_id workflow_version input_revision started_at "
    "ended_at outcome",
    "activity_results": "id run_id activity idempotency_key status output_ref error "
    "created_at updated_at",
    "llm_calls": "id study_id run_id activity provider model prompt_version input_tokens "
    "output_tokens cost_usd latency_ms created_at "
    # The ledger (migration 0012_call_ledger, EVAL_REPORT section 40).
    "kind status error stage revision activity_attempt component prompt_name prompt_sha256 "
    "build_sha entity_type entity_id requested_model endpoint response_id request_id "
    "finish_reason reasoning_effort max_output_tokens temperature response_format "
    "image_count image_bytes request_params cache_read_input_tokens "
    "cache_creation_input_tokens reasoning_output_tokens quantity unit unit_price_usd "
    "price_row pricing_version elapsed_ms request_uri request_sha256 response_uri "
    "response_sha256",
    # Firm conventions (migration 0014_firm_conventions): scoped by org_id, with the approval
    # that seeded the row and its agreement with the rules table.
    "classification_cache": "org_id key result hits source rules_version study_id reviewer_id "
    "approved_at rule_key agrees_with_rules approvals created_at updated_at",
    "idempotency_keys": "key user_id route request_hash status_code response created_at expires_at",
    # S2: ``status`` added by migration 0002_outbox_status (tests/unit/db/test_outbox_status.py).
    "outbox": "id kind payload status attempts next_attempt_at delivered_at last_error created_at",
    # Migration 0015_eval_run_manifest: the run-record columns (evals.record).
    "eval_runs": "id dataset split git_sha model prompt_version created_at git_dirty release "
    "run_dir label_sha256 label_version component component_version manifest",
    "eval_results": "id eval_run_id metric value n stderr details",
    # v0.6: migration 0004_photo_objects (SAM 3 localization in the product path).
    "photo_localizations": "study_id document_id revision status reason instances "
    "model_version params image_width image_height predict_seconds cost_usd "
    "carried_from_revision",
    # Manual add (migration 0008_object_prompts): mask_source, mask_reason, prompt_id.
    "photo_objects": "id study_id document_id revision label phrase score x1 y1 x2 y2 mask "
    "area source counted derived_from carried_from created_by deleted_at deleted_by "
    "mask_source mask_reason prompt_id",
    "object_prompts": "id study_id document_id revision label params status reason returned "
    "kept_score model_version predict_seconds cost_usd created_by",
    # Invite-only firms: migration 0007_invitations.
    "invitations": "id org_id email role created_by expires_at accepted_at accepted_by "
    "declined_at declined_by revoked_at revoked_by",
    # The per-study timeline and audit trail (migration 0013_study_events).
    "study_events": "id study_id seq event_type stage revision actor_type actor_id workflow_id "
    "run_id activity activity_id attempt started_at duration_ms entity_type entity_id code "
    "request_id before after detail",
    # The cost index store and its ledger (migration 0016_cost_indices).
    "cost_indices": "id source series year region value as_of citation url derived version "
    "obtained_at obtained_by obtained_by_name document_sha256 run_id detail",
    "cost_index_runs": "id source trigger actor_type actor_id actor_name status started_at "
    "finished_at rows_added years_wanted years_added failures document_sha256 request_id "
    "detail",
}

# Beyond the per-table lists: S1_SPEC §1 says every table has created_at; studies.revision
# is the current output revision that transition() compares (S1_SPEC §3 expected_revision).
EXTRA_COLUMNS = {table: {"created_at"} for table in SPEC_TABLES} | {
    "studies": {"created_at", "revision"},
}

SPEC_ENUMS = {
    # v0.9: "failed" (migration 0006, EVAL_REPORT 27).
    "study_status": [
        "draft",
        "processing",
        "waiting_review",
        "stalled",
        "completed",
        "archived",
        "failed",
    ],
    "stage": [
        "uploading",
        "extracting",
        "appraisal_review",
        "rooms_review",
        # v0.12: migration 0009_components_gate (patch components-gate-v1).
        "components_processing",
        "components_review",
        "assets_processing",
        "assets_review",
        "exporting",
        "completed",
    ],
    "document_kind": ["appraisal", "photo", "other"],
    "decision": ["approve", "reject"],
    "classification_method": ["rule", "cache", "llm", "manual"],
    "bucket": ["y5", "y7", "y15", "y27_5", "y39", "land"],
    # S2: migration 0002_outbox_status.
    "outbox_status": ["pending", "delivered", "dead"],
}

ENUM_COLUMNS = {
    ("studies", "status"): "study_status",
    ("studies", "current_stage"): "stage",
    ("documents", "kind"): "document_kind",
    ("reviews", "stage"): "stage",
    ("reviews", "decision"): "decision",
    ("assets", "bucket"): "bucket",
    ("assets", "method"): "classification_method",
    ("outbox", "status"): "outbox_status",
    ("study_events", "stage"): "stage",
}

# (child table, child columns, parent table, parent columns)
SPEC_COMPOSITE_FKS = {
    ("photo_rooms", ("study_id", "room_id"), "rooms", ("study_id", "id")),
    ("components", ("study_id", "room_id"), "rooms", ("study_id", "id")),
    ("assets", ("study_id", "component_id"), "components", ("study_id", "id")),
    ("takeoffs", ("study_id", "asset_id"), "assets", ("study_id", "id")),
    ("cost_estimates", ("study_id", "asset_id"), "assets", ("study_id", "id")),
    ("citations", ("study_id", "asset_id"), "assets", ("study_id", "id")),
    # Same tenancy rule (S1_SPEC §1) applied to the remaining study-scoped parents.
    ("photo_rooms", ("study_id", "document_id"), "documents", ("study_id", "id")),
    ("components", ("study_id", "document_id"), "documents", ("study_id", "id")),
    ("extractions", ("study_id", "document_id"), "documents", ("study_id", "id")),
    ("extraction_fields", ("study_id", "extraction_id"), "extractions", ("study_id", "id")),
    ("photo_localizations", ("study_id", "document_id"), "documents", ("study_id", "id")),
    ("photo_objects", ("study_id", "document_id"), "documents", ("study_id", "id")),
    ("object_prompts", ("study_id", "document_id"), "documents", ("study_id", "id")),
}

SPEC_SIMPLE_FKS = {
    ("org_members", "org_id", "orgs"),
    ("org_members", "user_id", "users"),
    ("studies", "org_id", "orgs"),
    ("studies", "created_by", "users"),
    ("documents", "study_id", "studies"),
    ("documents", "uploaded_by", "users"),
    ("document_pages", "document_id", "documents"),
    ("corpus_versions", "study_id", "studies"),
    ("chunks", "corpus_version_id", "corpus_versions"),
    ("evidence_tables", "corpus_version_id", "corpus_versions"),
    ("citations", "chunk_id", "chunks"),
    ("reviews", "study_id", "studies"),
    ("reviews", "reviewer_id", "users"),
    ("corrections", "study_id", "studies"),
    ("corrections", "reviewer_id", "users"),
    ("runs", "study_id", "studies"),
    ("activity_results", "run_id", "runs"),
    ("llm_calls", "study_id", "studies"),
    ("llm_calls", "run_id", "runs"),
    ("idempotency_keys", "user_id", "users"),
    ("eval_results", "eval_run_id", "eval_runs"),
    ("photo_objects", "created_by", "users"),
    ("photo_objects", "deleted_by", "users"),
    ("photo_objects", "prompt_id", "object_prompts"),
    ("object_prompts", "created_by", "users"),
    ("invitations", "org_id", "orgs"),
    ("invitations", "created_by", "users"),
    ("invitations", "accepted_by", "users"),
    ("invitations", "declined_by", "users"),
    ("invitations", "revoked_by", "users"),
    ("study_events", "study_id", "studies"),
    ("study_events", "actor_id", "users"),
    ("classification_cache", "org_id", "orgs"),
    ("classification_cache", "study_id", "studies"),
    ("classification_cache", "reviewer_id", "users"),
    ("cost_indices", "obtained_by", "users"),
    ("cost_indices", "run_id", "cost_index_runs"),
    ("cost_index_runs", "actor_id", "users"),
}

SPEC_UNIQUES = {
    ("users", ("firebase_uid",)),
    ("studies", ("workflow_id",)),
    ("documents", ("gcs_uri",)),
    ("documents", ("study_id", "sha256")),
    ("extractions", ("document_id", "revision")),
    ("extraction_fields", ("extraction_id", "section", "field")),
    ("rooms", ("study_id", "id")),
    ("takeoffs", ("asset_id",)),
    ("cost_estimates", ("asset_id",)),
    ("corpus_versions", ("corpus", "study_id", "version")),
    ("chunks", ("corpus_version_id", "chunk_key")),
    ("evidence_tables", ("corpus_version_id", "doc_id", "page", "id")),
    ("reviews", ("study_id", "stage", "revision")),
    ("reviews", ("idempotency_key",)),
    ("runs", ("workflow_id", "run_id")),
    ("activity_results", ("idempotency_key",)),
    ("photo_objects", ("study_id", "id")),
    ("study_events", ("seq",)),
    ("cost_indices", ("source", "series", "year", "region", "version")),
}

SPEC_PRIMARY_KEYS = {
    "org_members": ("org_id", "user_id"),
    "document_pages": ("document_id", "page_no"),
    "photo_rooms": ("document_id", "revision"),
    "photo_localizations": ("document_id", "revision"),
    "classification_cache": ("org_id", "key"),
    "idempotency_keys": ("key",),
}

SPEC_CHECKS = {
    "ck_org_members_role_valid",
    "ck_documents_bytes_positive",
    "ck_corpus_versions_corpus_valid",
    "ck_extractions_overall_confidence_range",
    "ck_extraction_fields_confidence_range",
    "ck_components_confidence_range",
    "ck_assets_confidence_range",
    "ck_photo_localizations_status_valid",
    "ck_photo_localizations_instances_nonnegative",
    "ck_photo_objects_source_valid",
    "ck_photo_objects_box_normalized",
    "ck_photo_objects_score_range",
    "ck_photo_objects_area_range",
    "ck_photo_objects_model_has_no_author",
    "ck_photo_objects_deleted_by_whom",
    "ck_photo_objects_mask_source_valid",
    "ck_photo_objects_manual_has_mask_source",
    "ck_object_prompts_status_valid",
    "ck_object_prompts_returned_nonnegative",
    "ck_invitations_role_valid",
    "ck_invitations_email_normalized",
    "ck_invitations_expires_after_created",
    "ck_invitations_accepted_by_whom",
    "ck_invitations_declined_by_whom",
    "ck_invitations_revoked_by_whom",
    "ck_invitations_one_outcome",
    "ck_study_events_actor_valid",
    "ck_study_events_duration_nonnegative",
    "ck_classification_cache_approvals_positive",
    "ck_classification_cache_rule_agreement",
    "ck_assets_convention_from_cache",
    "ck_cost_indices_series_valid",
    "ck_cost_indices_value_positive",
    "ck_cost_indices_version_positive",
    "ck_cost_indices_year_range",
    "ck_cost_index_runs_actor_valid",
    "ck_cost_index_runs_status_valid",
    "ck_studies_intake_version_not_negative",
    "ck_cost_index_runs_rows_added_nonnegative",
}

SPEC_NOT_NULL = {
    "orgs": "name",
    "users": "firebase_uid",
    "studies": "org_id created_by property_name status current_stage",
    "documents": "kind filename content_type bytes sha256 gcs_uri",
    "extractions": "revision method needs_review raw",
    "extraction_fields": "section field source",
    "rooms": "revision name room_type source",
    "assets": "revision category bucket recovery_period method needs_review verified",
    "takeoffs": "quantity unit",
    "corpus_versions": "corpus version embedding_model",
    "chunks": "corpus_version_id doc_id chunk_key text",
    "citations": "chunk_id excerpt",
    "reviews": "stage revision decision reviewer_id idempotency_key",
    "corrections": "entity_type entity_id revision after reviewer_id",
    "runs": "workflow_id run_id workflow_version input_revision",
    "activity_results": "activity idempotency_key status",
    "llm_calls": "provider model input_tokens output_tokens cost_usd kind status "
    "cache_read_input_tokens cache_creation_input_tokens reasoning_output_tokens",
    "classification_cache": "org_id result hits source rules_version study_id reviewer_id "
    "approved_at approvals",
    "idempotency_keys": "route request_hash expires_at",
    "outbox": "kind payload status attempts next_attempt_at",
    "photo_localizations": "status instances",
    "photo_objects": "revision label x1 y1 x2 y2 area source counted",
    "object_prompts": "revision label params status returned created_by",
    "invitations": "org_id email role expires_at",
    "study_events": "study_id seq event_type actor_type",
    "cost_indices": "source series year region value as_of citation version obtained_at "
    "obtained_by_name",
    "cost_index_runs": "source trigger actor_type status started_at finished_at rows_added",
}

STUDY_SCOPED = {
    "documents",
    "extractions",
    "extraction_fields",
    "rooms",
    "photo_rooms",
    "components",
    "assets",
    "takeoffs",
    "cost_estimates",
    "citations",
    "reviews",
    "corrections",
    "runs",
    "llm_calls",
    "photo_localizations",
    "photo_objects",
    "object_prompts",
    "study_events",
}


def table(name: str) -> Table:
    return Base.metadata.tables[name]


def load_migration(path: Path = MIGRATION) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_spec_table_exists_and_nothing_else() -> None:
    missing = set(SPEC_TABLES) - set(Base.metadata.tables)
    assert not missing, f"tables in S1_SPEC §2 missing from the models: {sorted(missing)}"
    assert set(Base.metadata.tables) == set(SPEC_TABLES)


@pytest.mark.parametrize("name", sorted(SPEC_TABLES))
def test_columns_match_spec(name: str) -> None:
    expected = set(SPEC_TABLES[name].split()) | EXTRA_COLUMNS[name]
    assert set(table(name).columns.keys()) == expected


@pytest.mark.parametrize("name", sorted(SPEC_NOT_NULL))
def test_spec_not_null_columns(name: str) -> None:
    for column in SPEC_NOT_NULL[name].split():
        assert not table(name).c[column].nullable, f"{name}.{column} must be NOT NULL"


@pytest.mark.parametrize("name", sorted(STUDY_SCOPED))
def test_study_scoped_tables_have_not_null_study_id_fk(name: str) -> None:
    column = table(name).c.study_id
    assert not column.nullable  # a NULL would disable every composite FK on the row
    assert (name, ("study_id",), "studies", ("id",)) in foreign_keys()


def test_every_table_has_created_at_with_default() -> None:
    for t in Base.metadata.sorted_tables:
        column = t.c.created_at
        assert not column.nullable
        assert str(column.server_default.arg) == "now()"  # type: ignore[union-attr]


def test_uuid_primary_keys_default_to_gen_random_uuid() -> None:
    for t in Base.metadata.sorted_tables:
        if t.name in SPEC_PRIMARY_KEYS:
            continue
        assert [c.name for c in t.primary_key.columns] == ["id"], t.name
        assert str(t.c.id.server_default.arg) == "gen_random_uuid()"  # type: ignore[union-attr]


@pytest.mark.parametrize(("name", "columns"), sorted(SPEC_PRIMARY_KEYS.items()))
def test_natural_primary_keys(name: str, columns: tuple[str, ...]) -> None:
    assert tuple(c.name for c in table(name).primary_key.columns) == columns


def test_enum_labels_match_spec_and_python_enums() -> None:
    assert {e.name: list(e.enums) for e in PG_ENUMS} == SPEC_ENUMS
    python = {
        "study_status": enums.StudyStatus,
        "stage": enums.Stage,
        "document_kind": enums.DocumentKind,
        "decision": enums.Decision,
        "classification_method": enums.ClassificationMethod,
        "bucket": enums.Bucket,
        "outbox_status": enums.OutboxStatus,
    }
    for name, enum_cls in python.items():
        assert [member.value for member in enum_cls] == SPEC_ENUMS[name]


@pytest.mark.parametrize(("location", "enum_name"), sorted(ENUM_COLUMNS.items()))
def test_enum_columns_use_named_native_enums(location: tuple[str, str], enum_name: str) -> None:
    column = table(location[0]).c[location[1]]
    assert isinstance(column.type, ENUM)
    assert column.type.name == enum_name
    assert column.type.native_enum


def test_migration_enum_labels_match_models() -> None:
    # alembic check does not compare enum labels, so pin the migrations to the models.
    created = load_migration().ENUMS | load_migration(VERSIONS / "0002_outbox_status.py").ENUMS
    # 0006 appends "failed" to study_status (ALTER TYPE ... ADD VALUE), after 0001's labels.
    added = load_migration(VERSIONS / "0006_study_failed.py")
    assert created["study_status"] == added.PREVIOUS
    created = {**created, "study_status": (*added.PREVIOUS, "failed")}
    # 0009 adds the components stages before "assets_processing" (ADD VALUE ... BEFORE).
    components = load_migration(VERSIONS / "0009_components_gate.py")
    assert created["stage"] == components.PREVIOUS
    at = components.PREVIOUS.index("assets_processing")
    stages = (*components.PREVIOUS[:at], *components.ADDED, *components.PREVIOUS[at:])
    created = {**created, "stage": stages}
    assert created == {name: tuple(labels) for name, labels in SPEC_ENUMS.items()}


def test_migration_trigger_tables_match_models() -> None:
    migration = load_migration()
    assert migration.UPDATED_AT_TABLES == UPDATED_AT_TABLES
    # 0013 adds study_events to the append-only tables (the same forbid_mutation() trigger).
    events = load_migration(VERSIONS / "0013_study_events.py")
    # 0016 adds the cost index store and its ledger.
    store = load_migration(VERSIONS / "0016_cost_indices.py")
    assert (
        *migration.APPEND_ONLY_TABLES,
        *events.APPEND_ONLY_TABLES,
        *store.APPEND_ONLY_TABLES,
    ) == APPEND_ONLY_TABLES
    with_updated_at = {t.name for t in Base.metadata.sorted_tables if "updated_at" in t.c}
    assert with_updated_at == set(UPDATED_AT_TABLES)


def foreign_keys() -> set[tuple[str, tuple[str, ...], str, tuple[str, ...]]]:
    found = set()
    for t in Base.metadata.sorted_tables:
        for constraint in t.constraints:
            if isinstance(constraint, ForeignKeyConstraint):
                found.add(
                    (
                        t.name,
                        tuple(constraint.column_keys),
                        constraint.referred_table.name,
                        tuple(element.column.name for element in constraint.elements),
                    )
                )
    return found


@pytest.mark.parametrize("fk", sorted(SPEC_COMPOSITE_FKS))
def test_composite_tenancy_foreign_keys(
    fk: tuple[str, tuple[str, ...], str, tuple[str, ...]],
) -> None:
    assert fk in foreign_keys()


def test_composite_fk_parents_have_unique_study_id_id() -> None:
    for _, _, parent, parent_columns in SPEC_COMPOSITE_FKS:
        assert (parent, parent_columns) in uniques(), parent


@pytest.mark.parametrize("fk", sorted(SPEC_SIMPLE_FKS))
def test_simple_foreign_keys(fk: tuple[str, str, str]) -> None:
    child, column, parent = fk
    assert (child, (column,), parent, ("id",)) in foreign_keys()


def uniques() -> set[tuple[str, tuple[str, ...]]]:
    found = set()
    for t in Base.metadata.sorted_tables:
        for constraint in t.constraints:
            if isinstance(constraint, UniqueConstraint):
                found.add((t.name, tuple(c.name for c in constraint.columns)))
    return found


@pytest.mark.parametrize("unique", sorted(SPEC_UNIQUES))
def test_unique_constraints(unique: tuple[str, tuple[str, ...]]) -> None:
    assert unique in uniques()


def test_reference_corpus_uniqueness_treats_null_study_as_equal() -> None:
    (constraint,) = [
        c
        for c in table("corpus_versions").constraints
        if isinstance(c, UniqueConstraint)
        and c.name == "uq_corpus_versions_corpus_study_id_version"
    ]
    assert constraint.dialect_options["postgresql"]["nulls_not_distinct"] is True


def test_check_constraints() -> None:
    names = {
        str(c.name)
        for t in Base.metadata.sorted_tables
        for c in t.constraints
        if isinstance(c, CheckConstraint)
    }
    assert SPEC_CHECKS <= names


def test_confidence_columns_are_real_with_range_check() -> None:
    for name, column in [
        ("extractions", "overall_confidence"),
        ("extraction_fields", "confidence"),
        ("components", "confidence"),
        ("assets", "confidence"),
    ]:
        assert isinstance(table(name).c[column].type, REAL)
        checks = {str(c.sqltext) for c in table(name).constraints if isinstance(c, CheckConstraint)}
        assert f"{column} BETWEEN 0 AND 1" in checks


@pytest.mark.parametrize(
    ("name", "column", "precision", "scale"),
    [
        ("takeoffs", "quantity", 14, 4),
        ("cost_estimates", "unit_cost", 14, 2),
        ("cost_estimates", "total", 14, 2),
        ("cost_estimates", "location_factor", 6, 3),
        ("assets", "recovery_period", 4, 1),
        ("llm_calls", "cost_usd", 10, 6),
    ],
)
def test_numeric_precision(name: str, column: str, precision: int, scale: int) -> None:
    column_type = table(name).c[column].type
    assert isinstance(column_type, Numeric)
    assert (column_type.precision, column_type.scale) == (precision, scale)


def test_special_column_types() -> None:
    embedding = table("chunks").c.embedding.type
    assert isinstance(embedding, Vector) and embedding.dim == 384
    assert isinstance(table("documents").c.bytes.type, BigInteger)
    assert isinstance(table("documents").c.sha256.type, CHAR)
    assert table("documents").c.sha256.type.length == 64
    assert isinstance(table("idempotency_keys").c.request_hash.type, CHAR)
    assert isinstance(table("eval_results").c.value.type, Double)


def test_jsonb_columns_store_none_as_sql_null() -> None:
    jsonb = [
        (t.name, c.name)
        for t in Base.metadata.sorted_tables
        for c in t.columns
        if isinstance(c.type, JSONB)
    ]
    # v0.6: photo_localizations.params, photo_objects.mask; v0.9: studies.room_aliases;
    # manual add: object_prompts.params; v0.13: extraction_fields.regions; the ledger
    # (0012): llm_calls.request_params; the timeline (0013): study_events.before/after/detail;
    # firm conventions (0014): assets.convention; the run record (0015): eval_runs.manifest;
    # the cost index store (0016): cost_indices.detail, cost_index_runs.years_wanted /
    # years_added / failures / detail
    assert len(jsonb) == 30
    for name, column in jsonb:
        assert table(name).c[column].type.none_as_null, f"{name}.{column}"


def test_indexes() -> None:
    indexes = {
        index.name: [str(e) for e in index.expressions]
        for t in Base.metadata.sorted_tables
        for index in t.indexes
    }
    assert indexes["ix_studies_org_id_created_at"] == ["studies.org_id", "created_at DESC"]
    assert indexes["ix_chunks_corpus_version_id_doc_id"] == [
        "chunks.corpus_version_id",
        "chunks.doc_id",
    ]
    # S2: migration 0002 moved the relay's claim index from delivered_at to status.
    assert indexes["ix_outbox_status_next_attempt_at"] == [
        "outbox.status",
        "outbox.next_attempt_at",
    ]
    assert indexes["ix_llm_calls_study_id"] == ["llm_calls.study_id"]
    assert indexes["ix_photo_objects_document_id_revision"] == [
        "photo_objects.document_id",
        "photo_objects.revision",
    ]
    assert indexes["ix_invitations_email"] == ["invitations.email"]
    assert indexes["uq_invitations_org_id_email_open"] == [
        "invitations.org_id",
        "invitations.email",
    ]


def test_one_open_invitation_per_firm_and_email() -> None:
    """Partial unique index: an answered or revoked invitation does not block a new one."""
    (index,) = [
        i for i in table("invitations").indexes if i.name == "uq_invitations_org_id_email_open"
    ]
    assert index.unique
    where = str(index.dialect_options["postgresql"]["where"])
    assert where == "accepted_at IS NULL AND declined_at IS NULL AND revoked_at IS NULL"


def test_server_defaults() -> None:
    def default(name: str, column: str) -> str:
        return str(table(name).c[column].server_default.arg)  # type: ignore[union-attr]

    assert default("studies", "status") == "'draft'"
    assert default("studies", "current_stage") == "'uploading'"
    assert default("extractions", "needs_review") == "false"
    assert default("assets", "verified") == "false"
    assert default("classification_cache", "hits") == "0"
    assert default("classification_cache", "approvals") == "1"
    assert default("outbox", "status") == "'pending'"
    assert default("outbox", "attempts") == "0"
    assert default("outbox", "next_attempt_at") == "now()"
    assert default("studies", "intake_version") == "0"


def test_constraint_and_index_names_fit_postgres_identifier_limit() -> None:
    for t in Base.metadata.sorted_tables:
        for item in [*t.constraints, *t.indexes]:
            assert item.name is not None, f"unnamed constraint on {t.name}"
            assert len(str(item.name)) <= 63, item.name
            assert str(item.name).split("_")[0] in {"pk", "fk", "uq", "ck", "ix"}, item.name
