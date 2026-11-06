"""``study_search``: one search document per study, so the library's ``q`` finds a study by its
address, client, parcel number, ZIP or engineer, not only by its name
(docs/api/STUDY_LIBRARY.md, EVAL_REPORT section 92).

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-05

Hand-written to match ``basis.db.models`` (``alembic check`` proves it).

Revises 0033 (``photo_objects`` exclusion, #159); nothing here depends on 0031 to 0033.

The document is every searched value of one study, one per line (so a word of the query
never spans two values), lower-cased and accent-folded by ``study_search_fold``
(``lower(unaccent(...))``): the property name, the intake's ZIP and state, the report
answers' taxpayer, the creator's name and email, the appraisal's subject fields that identify
the property and its client (``property_address``, ``city``, ``state``, ``zip``, ``county``,
``borrower``, ``owner_of_public_record``, ``assessors_parcel``, every revision's, corrections
included since they overwrite the row), and each signature's attested engineer name and
email.

Kept by triggers, in the transaction of the write that changes a source, so no code path can
forget it: ``studies`` (insert; update of ``property_name``, ``intake``, ``report_answers``,
``created_by``), ``extraction_fields`` (insert, update, delete of a searched subject field),
``report_signatures`` (insert; the table is never updated) and ``users`` (a change of name or
email rewrites the documents of the studies the user created; the update every request makes
to ``last_seen_at`` does not fire it). A separate table, not a column of ``studies``, because
``set_updated_at()`` bumps ``studies.updated_at`` on any update, which would move a study to
the top of the library whenever its document changed.

Lock order: ``study_search_refresh`` locks the study's row (``FOR NO KEY UPDATE``) before it
writes the document. A study row writer (an intake or a rename) takes the study row first and
the document second, so every other writer must too; otherwise a field write followed by a
stage move in one transaction and a concurrent study update deadlock
(``tests/integration/db/test_study_search.py`` makes the race real).

The index is pg_trgm GIN (``gin_trgm_ops``), which serves ``LIKE '%word%'``. ``pg_trgm`` and
``unaccent`` are contrib extensions marked trusted (a database owner may create them); both
are in the pgvector image the stack and CI use and on Cloud SQL. Nothing else in Basis uses
them, so the downgrade drops them too (without ``CASCADE``: an object someone else built on
them stops the downgrade rather than vanishing). Every existing study is backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "study_search"
INDEX = "ix_study_search_document_trgm"
SUBJECT_FIELDS = (
    "property_address",
    "city",
    "state",
    "zip",
    "county",
    "borrower",
    "owner_of_public_record",
    "assessors_parcel",
)


def _subject(row: str) -> str:
    names = ", ".join(f"'{name}'" for name in SUBJECT_FIELDS)
    return f"{row}section = 'subject' AND {row}field IN ({names})"


FOLD_FN = """
CREATE FUNCTION study_search_fold(value text) RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT lower(public.unaccent('public.unaccent'::regdictionary, value))
$$
"""

DOCUMENT_FN = f"""
CREATE FUNCTION study_search_document(sid uuid) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT study_search_fold(concat_ws(E'\\n',
        s.property_name,
        s.intake->>'zip',
        s.intake->>'state',
        s.report_answers->>'taxpayer_name',
        u.display_name,
        u.email,
        (SELECT string_agg(f.value_text, E'\\n' ORDER BY e.revision, f.field)
           FROM extractions e JOIN extraction_fields f ON f.extraction_id = e.id
          WHERE e.study_id = s.id AND {_subject("f.")}),
        (SELECT string_agg(concat_ws(E'\\n', r.engineer->>'name', r.engineer->>'email'),
                           E'\\n' ORDER BY r.revision)
           FROM report_signatures r WHERE r.study_id = s.id)))
    FROM studies s LEFT JOIN users u ON u.id = s.created_by
    WHERE s.id = sid
$$
"""

REFRESH_FN = """
CREATE FUNCTION study_search_refresh(sid uuid) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    -- The study row first, then the document: the order a study update takes them.
    PERFORM 1 FROM studies WHERE id = sid FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    INSERT INTO study_search (study_id, document)
    VALUES (sid, study_search_document(sid))
    ON CONFLICT (study_id) DO UPDATE SET document = EXCLUDED.document
    WHERE study_search.document IS DISTINCT FROM EXCLUDED.document;
END
$$
"""

FROM_STUDY_FN = """
CREATE FUNCTION study_search_from_study() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM study_search_refresh(NEW.id);
    RETURN NULL;
END
$$
"""

FROM_CHILD_FN = """
CREATE FUNCTION study_search_from_child() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM study_search_refresh(OLD.study_id);
    END IF;
    IF TG_OP = 'INSERT' OR (TG_OP = 'UPDATE' AND NEW.study_id IS DISTINCT FROM OLD.study_id) THEN
        PERFORM study_search_refresh(NEW.study_id);
    END IF;
    RETURN NULL;
END
$$
"""

# A rename rewrites every document of the user's studies, in one statement and in the order
# of the study ids (the lock order is the same for two renames of overlapping studies).
FROM_USER_FN = """
CREATE FUNCTION study_search_from_user() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM study_search_refresh(s.id)
       FROM studies s WHERE s.created_by = NEW.id ORDER BY s.id;
    RETURN NULL;
END
$$
"""

TRIGGERS = (
    (
        "study_search_study",
        "studies",
        "AFTER INSERT OR UPDATE OF property_name, intake, report_answers, created_by",
        None,
        "study_search_from_study",
    ),
    (
        "study_search_field_insert",
        "extraction_fields",
        "AFTER INSERT",
        _subject("NEW."),
        "study_search_from_child",
    ),
    (
        "study_search_field_update",
        "extraction_fields",
        "AFTER UPDATE",
        f"({_subject('NEW.')}) OR ({_subject('OLD.')})",
        "study_search_from_child",
    ),
    (
        "study_search_field_delete",
        "extraction_fields",
        "AFTER DELETE",
        _subject("OLD."),
        "study_search_from_child",
    ),
    (
        "study_search_signature",
        "report_signatures",
        "AFTER INSERT",
        None,
        "study_search_from_child",
    ),
    (
        "study_search_user",
        "users",
        "AFTER UPDATE OF display_name, email",
        "OLD.display_name IS DISTINCT FROM NEW.display_name "
        "OR OLD.email IS DISTINCT FROM NEW.email",
        "study_search_from_user",
    ),
)
FUNCTIONS = (
    "study_search_from_study()",
    "study_search_from_child()",
    "study_search_from_user()",
    "study_search_refresh(uuid)",
    "study_search_document(uuid)",
    "study_search_fold(text)",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.create_table(
        TABLE,
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("document", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["study_id"],
            ["studies.id"],
            name=op.f("fk_study_search_study_id_studies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("study_id", name=op.f("pk_study_search")),
    )
    for statement in (
        FOLD_FN,
        DOCUMENT_FN,
        REFRESH_FN,
        FROM_STUDY_FN,
        FROM_CHILD_FN,
        FROM_USER_FN,
    ):
        op.execute(statement)
    for name, table, timing, when, function in TRIGGERS:
        condition = f" WHEN ({when})" if when else ""
        op.execute(
            f"CREATE TRIGGER {name} {timing} ON {table} FOR EACH ROW{condition} "
            f"EXECUTE FUNCTION {function}()"
        )
    # Every study that exists; the triggers keep the rest.
    op.execute(
        f"INSERT INTO {TABLE} (study_id, document) "
        "SELECT id, study_search_document(id) FROM studies"
    )
    op.create_index(
        op.f(INDEX),
        TABLE,
        ["document"],
        postgresql_using="gin",
        postgresql_ops={"document": "gin_trgm_ops"},
    )


def downgrade() -> None:
    for name, table, _, _, _ in TRIGGERS:
        op.execute(f"DROP TRIGGER {name} ON {table}")
    op.drop_index(op.f(INDEX), table_name=TABLE)
    op.drop_table(TABLE)
    for function in FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}")
    op.execute("DROP EXTENSION IF EXISTS unaccent")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
