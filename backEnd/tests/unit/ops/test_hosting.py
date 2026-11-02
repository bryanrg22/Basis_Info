"""Hosting labels (basis.ops.hosting): read from the effective runtime, reduced to a label,
never a host name, address or path; ``None`` when the runtime does not establish it."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from basis.config import Settings
from basis.ops import hosting


def test_process_hosting_reads_cloud_run_then_docker_then_local(tmp_path: Path) -> None:
    marker = tmp_path / ".dockerenv"
    assert hosting.process_hosting({}, dockerenv=marker) == "Local process"
    marker.touch()
    assert hosting.process_hosting({}, dockerenv=marker) == "Docker container"
    assert hosting.process_hosting({"K_SERVICE": "basis-api"}, dockerenv=marker) == (
        "Google Cloud Run"
    )
    assert hosting.process_hosting({"K_SERVICE": ""}, dockerenv=marker) == "Docker container"


@pytest.mark.parametrize(
    ("url", "instance", "expected"),
    [
        (None, None, None),
        ("postgresql://u:p@localhost:5432/basis", None, "Local PostgreSQL"),
        ("postgresql+asyncpg://u:p@127.0.0.1/basis", None, "Local PostgreSQL"),
        ("postgresql://u:p@db:5432/basis", None, "Self-hosted PostgreSQL"),
        ("postgresql://u:p@10.1.2.3/basis", None, "Self-hosted PostgreSQL"),
        ("postgresql://u:p@/basis?host=/cloudsql/proj:region:inst", None, "Google Cloud SQL"),
        ("postgresql://u:p@localhost/basis", "proj:region:inst", "Google Cloud SQL"),
        ("not a url at all", None, None),
    ],
)
def test_database_hosting(
    settings: Settings, url: str | None, instance: str | None, expected: str | None
) -> None:
    s = settings.model_copy(update={"database_url": url, "cloud_sql_instance": instance})
    assert hosting.database_hosting(s) == expected


@pytest.mark.parametrize(
    ("address", "key", "expected"),
    [
        ("localhost:7233", None, "Local Temporal server"),
        ("127.0.0.1:7233", None, "Local Temporal server"),
        ("temporal:7233", None, "Self-hosted Temporal"),
        ("basis.a1b2c.tmprl.cloud:7233", None, "Temporal Cloud"),
        ("temporal:7233", "key", "Temporal Cloud"),
    ],
)
def test_temporal_hosting(settings: Settings, address: str, key: str | None, expected: str) -> None:
    s = settings.model_copy(
        update={
            "temporal_address": address,
            "temporal_api_key": None if key is None else SecretStr(key),
        }
    )
    assert hosting.temporal_hosting(s) == expected


def test_storage_and_firebase_labels() -> None:
    assert hosting.storage_hosting("Google Cloud Storage") == "Google Cloud Storage"
    assert hosting.storage_hosting("local") == "Local disk"
    assert hosting.firebase_hosting("emulator") == "Firebase Auth emulator"
    assert hosting.firebase_hosting("Firebase Auth") == "Firebase Auth"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (None, None),
        ("https://sam3-internal-abc123-ue.a.run.app", "Google Cloud Run"),
        ("http://localhost:5000", "Local server"),
        ("http://host.docker.internal:5000", "Local server"),
        ("https://sam3.example.net", "Self-hosted server"),
    ],
)
def test_localizer_hosting_never_echoes_the_host(url: str | None, expected: str | None) -> None:
    label = hosting.localizer_hosting(url)
    assert label == expected
    if url:
        for part in ("sam3", "example", "a.run.app", "localhost"):
            assert part not in (label or "")
