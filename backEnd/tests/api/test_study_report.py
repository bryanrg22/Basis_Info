"""``GET /v1/studies/{id}/report``: the report and the schedule the export step wrote.

* A viewer gets the file of the latest exported revision (or the one asked for) as an
  attachment with its content type, ``nosniff``, a private cache lifetime and the recorded
  sha256 as the ``ETag``; ``If-None-Match`` with it is 304.
* No export yet, a revision never exported, bytes gone from storage: 404 BASIS-REPORT-006.
  An export that skipped the report: 404 with the skip's own code and message.
* Another firm's study: 404, the bytes never leave.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from apitest_support import Api, bearer, sha256_hex
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.db.enums import Stage
from basis.db.repositories.study_event import StudyEventRepository
from basis.db.session import session_scope
from basis.storage import LocalStorage

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7 report bytes"
CSV = b"line,description\n1,Carpet\n"


async def exported(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    study_id: str,
    revision: int,
    *,
    files: dict[str, bytes] | None = None,
    skipped: dict[str, str] | None = None,
) -> dict[str, Any]:
    written: dict[str, dict[str, str]] = {}
    for suffix, data in (files or {}).items():
        uri = f"gs://basis/studies/{study_id}/exports/rev-{revision}.{suffix}"
        await storage.put(uri, data, "application/octet-stream")
        written[suffix] = {"uri": uri, "sha256": sha256_hex(data)}
    report: dict[str, Any] = {"skipped": skipped} if skipped else {"files": written, "draft": True}
    async with session_scope(sessionmaker) as session:
        await StudyEventRepository(session).record(
            uuid.UUID(study_id),
            "export.written",
            actor="system",
            stage=Stage.EXPORTING,
            revision=revision,
            detail={"uri": "x", "sha256": "y", "report": report},
        )
    return written


def path(study_id: str) -> str:
    return f"/v1/studies/{study_id}/report"


async def test_the_latest_export_s_report_with_its_headers(
    api: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    await exported(sessionmaker, storage, study["id"], 1, files={"report.pdf": b"%PDF old"})
    await exported(
        sessionmaker, storage, study["id"], 2, files={"report.pdf": PDF, "schedule.csv": CSV}
    )
    response = await api.get("alice", path(study["id"]))
    assert response.status_code == 200 and response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["etag"] == f'"{sha256_hex(PDF)}"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="study-{study["id"][:8]}-rev-2-report.pdf"'
    )
    schedule = await api.get("alice", path(study["id"]), params={"format": "csv"})
    assert schedule.content == CSV and schedule.headers["content-type"].startswith("text/csv")
    older = await api.get("alice", path(study["id"]), params={"revision": 1})
    assert older.content == b"%PDF old"
    again = await api.client.get(
        path(study["id"]), headers={**bearer("alice"), "If-None-Match": f'"{sha256_hex(PDF)}"'}
    )
    assert again.status_code == 304 and again.content == b""


async def test_missing_reports_are_404_with_the_reason(
    api: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    none_yet = await api.get("alice", path(study["id"]))
    assert none_yet.status_code == 404 and none_yet.json()["code"] == "BASIS-REPORT-006"
    await exported(
        sessionmaker,
        storage,
        study["id"],
        1,
        skipped={
            "code": "BASIS-REPORT-002",
            "message": "the allocation was withheld (missing: intake)",
        },
    )
    skipped = await api.get("alice", path(study["id"]))
    assert skipped.status_code == 404 and skipped.json()["code"] == "BASIS-REPORT-002"
    assert "missing: intake" in skipped.json()["detail"]
    never = await api.get("alice", path(study["id"]), params={"revision": 9})
    assert never.json()["code"] == "BASIS-REPORT-006"
    written = await exported(sessionmaker, storage, study["id"], 2, files={"report.pdf": PDF})
    no_xlsx = await api.get("alice", path(study["id"]), params={"format": "xlsx"})
    assert no_xlsx.status_code == 404 and no_xlsx.json()["code"] == "BASIS-REPORT-006"
    storage_path = written["report.pdf"]["uri"]
    await storage.delete(storage_path)
    gone = await api.get("alice", path(study["id"]))
    assert gone.status_code == 404 and gone.json()["code"] == "BASIS-REPORT-006"


async def test_another_firm_cannot_read_it_and_bad_parameters_are_refused(
    api: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    await exported(sessionmaker, storage, study["id"], 1, files={"report.pdf": PDF})
    assert (await api.get("mallory", path(study["id"]))).status_code == 404
    assert (await api.get("alice", path(study["id"]), params={"format": "docx"})).status_code == 422
    assert (await api.get("alice", path(study["id"]), params={"revision": 0})).status_code == 422


# --------------------------------------------------------------------- vendor profiles


async def real_export(
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    study_id: str,
    revision: int = 1,
) -> bytes:
    """The synthetic export stored with its generic report files and event, as the export
    step leaves them; returns the export JSON."""
    from basis.activities.export_report import build_report_files
    from tests.unit.report.support import export

    doc = export()
    doc["study_id"] = study_id
    doc["revision"] = revision
    data = json.dumps(doc, sort_keys=True).encode()
    base = f"gs://basis/studies/{study_id}/exports/rev-{revision}"
    await storage.put(f"{base}.json", data, "application/json")
    async with session_scope(sessionmaker) as session:
        files = await build_report_files(session, storage, data, uuid.UUID(study_id), revision)
        written = {}
        for suffix, payload in files.files.items():
            await storage.put(f"{base}.{suffix}", payload, "application/octet-stream")
            written[suffix] = {"uri": f"{base}.{suffix}", "sha256": sha256_hex(payload)}
        await StudyEventRepository(session).record(
            uuid.UUID(study_id),
            "export.written",
            actor="system",
            stage=Stage.EXPORTING,
            revision=revision,
            detail={
                "uri": f"{base}.json",
                "sha256": sha256_hex(data),
                "report": {"files": written, "draft": files.draft},
            },
        )
    return data


async def test_a_vendor_profile_is_rendered_from_the_stored_export(
    api: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    from basis.report import vendors
    from basis.report.model import build_report
    from basis.report.schedule import rows

    study = await api.create_study("alice")
    data = await real_export(sessionmaker, storage, study["id"])
    expected = vendors.to_csv(rows(build_report(json.loads(data))), vendors.SAGE)
    sage = await api.get("alice", path(study["id"]), params={"format": "csv", "profile": "sage"})
    assert sage.status_code == 200 and sage.content == expected
    assert sage.headers["content-type"].startswith("text/csv")
    assert sage.headers["etag"] == f'"{sha256_hex(expected)}"'
    assert sage.headers["content-disposition"] == (
        f'attachment; filename="study-{study["id"][:8]}-rev-1-schedule-sage.csv"'
    )
    assert sage.content.split(b"\n")[0] == ",".join(vendors.SAGE.headers).encode()
    again = await api.client.get(
        path(study["id"]),
        params={"format": "csv", "profile": "sage"},
        headers={**bearer("alice"), "If-None-Match": sage.headers["etag"]},
    )
    assert again.status_code == 304 and again.content == b""
    book = await api.get(
        "alice", path(study["id"]), params={"format": "xlsx", "profile": "proconnect"}
    )
    assert book.status_code == 200 and book.content[:2] == b"PK"
    assert book.content == vendors.to_xlsx(rows(build_report(json.loads(data))), vendors.PROCONNECT)
    assert book.headers["content-disposition"].endswith('schedule-proconnect.xlsx"')
    # the default is still the stored file, byte for byte
    generic = await api.get("alice", path(study["id"]), params={"format": "csv"})
    assert generic.content == await storage.read(
        f"gs://basis/studies/{study['id']}/exports/rev-1.schedule.csv"
    )
    explicit = await api.get(
        "alice", path(study["id"]), params={"format": "csv", "profile": "generic"}
    )
    assert explicit.content == generic.content
    assert explicit.headers["etag"] == generic.headers["etag"]


async def test_profile_parameters_are_checked_and_missing_exports_refused(
    api: Api, storage: LocalStorage, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    study = await api.create_study("alice")
    await real_export(sessionmaker, storage, study["id"])
    unknown = await api.get(
        "alice", path(study["id"]), params={"format": "csv", "profile": "drake"}
    )
    assert unknown.status_code == 422
    with_pdf = await api.get(
        "alice", path(study["id"]), params={"format": "pdf", "profile": "sage"}
    )  # ignored for the PDF, as `variant` is for the schedule
    assert with_pdf.status_code == 200 and with_pdf.content[:5] == b"%PDF-"
    assert with_pdf.headers["content-disposition"].endswith('rev-1-report.pdf"')
    assert (
        await api.get("mallory", path(study["id"]), params={"format": "csv", "profile": "cch"})
    ).status_code == 404
    await storage.delete(f"gs://basis/studies/{study['id']}/exports/rev-1.json")
    gone = await api.get("alice", path(study["id"]), params={"format": "csv", "profile": "cch"})
    assert gone.status_code == 404 and gone.json()["code"] == "BASIS-REPORT-006"
    await exported(
        sessionmaker,
        storage,
        study["id"],
        2,
        skipped={"code": "BASIS-REPORT-002", "message": "the allocation was withheld"},
    )
    skipped = await api.get(
        "alice", path(study["id"]), params={"format": "xlsx", "profile": "bloomberg"}
    )
    assert skipped.status_code == 404 and skipped.json()["code"] == "BASIS-REPORT-002"
