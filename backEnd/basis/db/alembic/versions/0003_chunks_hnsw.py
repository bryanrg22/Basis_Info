"""HNSW index on chunk embeddings; published corpus versions become immutable (S3).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-15

Hand-written.

* ``ix_chunks_embedding_hnsw``: ``USING hnsw (embedding vector_cosine_ops)`` with
  pgvector's default build parameters (m = 16, ef_construction = 64). Vector search
  orders by ``embedding <=> :q`` (cosine distance) and the embeddings are unit length,
  so the cosine operator class is the one the query uses. Deferred from 0001 by
  S1_SPEC section 2.
* ``forbid_published_corpus_write()``: ``BEFORE INSERT OR UPDATE`` row trigger on
  ``chunks`` and ``evidence_tables`` that raises SQLSTATE 23001 (restrict_violation)
  when the row's corpus version (old or new) is published. A citation stores a chunk
  id; if a published version could still gain, lose or change chunks, a citation
  verified yesterday could point at different text today. DELETE stays allowed so a
  study's data can still be erased.
* ``forbid_published_version_update()``: ``BEFORE UPDATE`` trigger on
  ``corpus_versions`` that raises the same error once ``published_at`` is set
  (publishing is one-way; the model name and number of a published version are fixed).

Downgrade drops the triggers, the functions and the index.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_CHILD_TABLES = ("chunks", "evidence_tables")

FORBID_PUBLISHED_CORPUS_WRITE_FN = """
CREATE FUNCTION forbid_published_corpus_write() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM corpus_versions
        WHERE published_at IS NOT NULL
          AND (id = NEW.corpus_version_id
               OR (TG_OP = 'UPDATE' AND id = OLD.corpus_version_id))
    ) THEN
        RAISE EXCEPTION '% rows of a published corpus version are immutable: % is not allowed',
            TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$
"""

FORBID_PUBLISHED_VERSION_UPDATE_FN = """
CREATE FUNCTION forbid_published_version_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'corpus version % is published and cannot change', OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$
"""


def upgrade() -> None:
    op.create_index(
        op.f("ix_chunks_embedding_hnsw"),
        "chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.execute(FORBID_PUBLISHED_CORPUS_WRITE_FN)
    for table in IMMUTABLE_CHILD_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_published_immutable BEFORE INSERT OR UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_published_corpus_write()"
        )
    op.execute(FORBID_PUBLISHED_VERSION_UPDATE_FN)
    op.execute(
        "CREATE TRIGGER corpus_versions_published_immutable BEFORE UPDATE ON corpus_versions "
        "FOR EACH ROW WHEN (OLD.published_at IS NOT NULL) "
        "EXECUTE FUNCTION forbid_published_version_update()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER corpus_versions_published_immutable ON corpus_versions")
    op.execute("DROP FUNCTION forbid_published_version_update()")
    for table in reversed(IMMUTABLE_CHILD_TABLES):
        op.execute(f"DROP TRIGGER {table}_published_immutable ON {table}")
    op.execute("DROP FUNCTION forbid_published_corpus_write()")
    op.drop_index(op.f("ix_chunks_embedding_hnsw"), table_name="chunks")
