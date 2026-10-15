"""What ``create_app`` selects from settings, without a database (S1-C wiring)."""

from __future__ import annotations

from pathlib import Path

from basis.api.app import LOCAL_BUCKET, create_app
from basis.api.idempotency import InMemoryIdempotencyStore
from basis.config import Settings
from basis.storage import GcsStorage, LocalStorage


def build(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_gcs_storage_when_a_bucket_is_configured() -> None:
    app = create_app(build(gcs_bucket="basis-test-bucket"))
    assert isinstance(app.state.storage, GcsStorage)
    assert app.state.storage.bucket_name == "basis-test-bucket"
    assert app.state.storage_bucket == "basis-test-bucket"


def test_local_storage_without_a_bucket() -> None:
    app = create_app(build())
    assert isinstance(app.state.storage, LocalStorage)
    assert app.state.storage_bucket == LOCAL_BUCKET


def test_injected_storage_wins(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    app = create_app(build(gcs_bucket="basis-test-bucket"), storage=storage)
    assert app.state.storage is storage
    assert app.state.storage_bucket == "basis-test-bucket"


def test_no_database_means_no_sessionmaker_and_in_memory_idempotency() -> None:
    app = create_app(build())
    assert app.state.db_sessionmaker is None
    assert isinstance(app.state.idempotency_store, InMemoryIdempotencyStore)


async def test_lifespan_without_a_database_configures_nothing() -> None:
    app = create_app(build())
    async with app.router.lifespan_context(app):
        assert app.state.db_sessionmaker is None
        assert isinstance(app.state.idempotency_store, InMemoryIdempotencyStore)
