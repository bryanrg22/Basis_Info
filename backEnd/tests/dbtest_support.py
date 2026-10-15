"""Helpers shared by the database-backed tests (``tests/integration/db`` and ``tests/api``).

Imported as a top-level module: pytest puts ``tests/`` on ``sys.path`` because
``tests/conftest.py`` lives there; the name is unique to avoid collisions with the legacy
``tests`` packages. The session-wide ``postgres`` and ``database_url`` fixtures that use
it are in ``tests/conftest.py``, so every suite shares one container.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import asyncpg
from alembic.config import Config
from sqlalchemy import text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from basis.config import Settings
from basis.db.enums import OrgRole, Stage, status_for_stage
from basis.db.models import Base, Org, OrgMember, Study, User
from basis.db.repositories import StudyRepository, UserRepository

PG_IMAGE = "pgvector/pgvector:pg16"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def alembic_config(database_url: str, **attributes: Any) -> Config:
    """Alembic config for ``backEnd/alembic.ini`` pointed at ``database_url``."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    config.attributes["configure_logger"] = False
    config.attributes.update(attributes)
    return config


def plain_dsn(database_url: str) -> str:
    """asyncpg DSN (no ``+asyncpg`` driver suffix) for direct admin connections."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


async def execute_admin(database_url: str, *statements: str) -> None:
    connection = await asyncpg.connect(plain_dsn(database_url))
    try:
        for statement in statements:
            await connection.execute(statement)
    finally:
        await connection.close()


def db_settings(database_url: str, **overrides: Any) -> Settings:
    """Settings for the test database (BASIS_ENV=test comes from tests/conftest.py)."""
    return Settings(_env_file=None, database_url=database_url, **overrides)


async def truncate_all(engine: AsyncEngine) -> None:
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} CASCADE"))


class Factory:
    """Creates committed-or-flushed rows through the real repositories."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def user(self, uid: str | None = None, email: str | None = None) -> User:
        uid = uid or f"uid-{uuid.uuid4().hex}"
        return await UserRepository(self.session).upsert_by_firebase_uid(
            uid, email or f"{uid}@example.test", None
        )

    async def personal_org_id(self, user: User) -> uuid.UUID:
        memberships = await UserRepository(self.session).memberships(user.id)
        owned = [org_id for org_id, role in memberships.items() if role is OrgRole.OWNER]
        assert len(owned) == 1
        return owned[0]

    async def org(self, name: str = "Acme Engineering") -> Org:
        org = Org(name=name)
        self.session.add(org)
        await self.session.flush()
        return org

    async def member(self, org_id: uuid.UUID, user: User, role: OrgRole) -> None:
        self.session.add(OrgMember(org_id=org_id, user_id=user.id, role=role.value))
        await self.session.flush()

    async def study(
        self,
        owner: User | None = None,
        *,
        org_id: uuid.UUID | None = None,
        stage: Stage = Stage.UPLOADING,
        revision: int = 1,
        property_name: str = "12 Main St",
    ) -> Study:
        owner = owner or await self.user()
        org_id = org_id or await self.personal_org_id(owner)
        study = await StudyRepository(self.session).create(org_id, owner.id, property_name)
        if stage is not Stage.UPLOADING or revision != 1:
            await self.session.execute(
                update(Study)
                .where(Study.id == study.id)
                .values(current_stage=stage, status=status_for_stage(stage), revision=revision)
            )
            await self.session.refresh(study)
        return study
