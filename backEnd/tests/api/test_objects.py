"""``/v1/studies/{id}/photos/{document_id}/objects``: read, add a manual selection, delete.

Rows are seeded through the repository as ``localize_photo`` writes them (two windows, one
counted and one candidate below the window threshold, a counted cabinet) on a synthetic
photo of the study; the study is moved to ``rooms_review`` directly.
"""

from __future__ import annotations

import uuid
from typing import Any

import numpy as np
import pytest
from apitest_support import Api, Seed
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basis.api.errors import PROBLEM_JSON
from basis.db.enums import OrgRole, Stage, StudyStatus
from basis.db.models import PhotoObject, Study
from basis.db.repositories.photo_object import NewObject, PhotoObjectRepository
from basis.db.session import session_scope
from basis.vision.localize import Rle

pytestmark = pytest.mark.integration


def mask(r0: int, r1: int, c0: int, c1: int) -> dict[str, Any]:
    grid = np.zeros((10, 10), dtype=bool)
    grid[r0:r1, c0:c1] = True
    return Rle.encode(grid).model_dump(mode="json")


async def seeded_photo(
    api: Api, seed: Seed, sm: async_sessionmaker[AsyncSession], stage: Stage = Stage.ROOMS_REVIEW
) -> tuple[str, str, dict[str, uuid.UUID]]:
    """(study id, photo id, {name: object id}) with three model rows and a status."""
    study = await api.create_study("alice")
    sid = str(study["id"])
    outputs = await seed.outputs(sid)
    await seed.stage(sid, stage)
    photo = uuid.UUID(outputs["photo"])
    ids: dict[str, uuid.UUID] = {}
    async with session_scope(sm) as session:
        repo = PhotoObjectRepository(session)
        for name, new in {
            "window": NewObject(
                label="window",
                score=0.9,
                x1=0.1,
                y1=0.1,
                x2=0.5,
                y2=0.5,
                area=0.16,
                mask=mask(1, 5, 1, 5),
                source="model",
                phrase="window",
            ),
            "candidate": NewObject(
                label="window",
                score=0.45,
                x1=0.6,
                y1=0.1,
                x2=0.9,
                y2=0.4,
                area=0.09,
                mask=mask(1, 4, 6, 9),
                source="model",
                counted=False,
                phrase="window",
            ),
            "cabinet": NewObject(
                label="kitchen_cabinet",
                score=0.7,
                x1=0.1,
                y1=0.6,
                x2=0.4,
                y2=0.9,
                area=0.09,
                source="model",
                phrase="kitchen cabinet",
            ),
        }.items():
            row = await repo.add(study_id=uuid.UUID(sid), document_id=photo, revision=1, new=new)
            ids[name] = row.id
        await repo.record_localization(
            study_id=uuid.UUID(sid),
            document_id=photo,
            revision=1,
            status="done",
            instances=3,
            model_version="v-test",
        )
    return sid, str(photo), ids


def path(sid: str, photo: str) -> str:
    return f"/v1/studies/{sid}/photos/{photo}/objects"


def tap(label: str, x: float, y: float) -> dict[str, Any]:
    return {"kind": "point", "label": label, "point": {"x": x, "y": y}}


def box(label: str, x1: float, y1: float, x2: float, y2: float) -> dict[str, Any]:
    return {"kind": "box", "label": label, "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}}


async def object_rows(sm: async_sessionmaker[AsyncSession]) -> int:
    async with sm() as session:
        return int(await session.scalar(select(func.count()).select_from(PhotoObject)) or 0)


# --------------------------------------------------------------------------- GET


async def test_get_returns_instances_counts_and_status(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    response = await api.get("alice", path(sid, photo))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["document_id"], body["revision"]) == (photo, 1)
    assert body["counts"] == {"kitchen_cabinet": 1, "window": 1}  # the candidate is not counted
    assert body["localization"]["status"] == "done"
    assert body["localization"]["model_version"] == "v-test"
    labels = [(i["label"], i["score"], i["counted"]) for i in body["items"]]
    assert labels == [
        ("kitchen_cabinet", pytest.approx(0.7), True),
        ("window", pytest.approx(0.9), True),
        ("window", pytest.approx(0.45), False),
    ]
    window = body["items"][1]
    assert window["id"] == str(ids["window"])
    assert window["box"] == pytest.approx({"x1": 0.1, "y1": 0.1, "x2": 0.5, "y2": 0.5})
    assert window["mask"]["height"] == 10 and sum(window["mask"]["counts"]) == 100
    assert body["items"][0]["mask"] is None


async def test_get_for_a_photo_without_localization(api: Api, seed: Seed) -> None:
    study = await api.create_study("alice")
    outputs = await seed.outputs(study["id"])
    response = await api.get("alice", path(study["id"], outputs["photo"]))
    assert response.status_code == 200
    assert response.json()["localization"] is None and response.json()["items"] == []


async def test_get_at_another_revision(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, _ = await seeded_photo(api, seed, sessionmaker)
    response = await api.get("alice", path(sid, photo), params={"revision": 2})
    assert response.status_code == 200
    assert response.json()["revision"] == 2 and response.json()["items"] == []


async def test_a_viewer_may_read(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, _ = await seeded_photo(api, seed, sessionmaker)
    org = await seed.personal_org("alice")
    await seed.member(org, "victor", OrgRole.VIEWER)
    assert (await api.get("victor", path(sid, photo))).status_code == 200
    assert (await api.get("mallory", path(sid, photo))).status_code == 404  # not a member


@pytest.mark.parametrize("which", ["unknown", "appraisal", "other-study"])
async def test_a_document_that_is_not_a_photo_of_the_study_is_404(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], which: str
) -> None:
    sid, _, _ = await seeded_photo(api, seed, sessionmaker)
    if which == "unknown":
        document = str(uuid.uuid4())
    elif which == "appraisal":
        async with sessionmaker() as session:
            from basis.db.enums import DocumentKind
            from basis.db.models import Document

            document = str(
                await session.scalar(
                    select(Document.id).where(
                        Document.study_id == uuid.UUID(sid),
                        Document.kind == DocumentKind.APPRAISAL,
                    )
                )
            )
    else:
        other = await api.create_study("alice", "Elsewhere")
        document = (await seed.outputs(other["id"]))["photo"]
    response = await api.get("alice", path(sid, document))
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# --------------------------------------------------------------------------- POST


async def test_a_tap_on_a_candidate_adds_a_manual_instance_with_its_mask(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    alice = await seed.user("alice")
    response = await api.post("alice", path(sid, photo), tap("window", 0.75, 0.25))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created"] is True
    added = body["object"]
    assert (added["source"], added["counted"], added["label"]) == ("manual", True, "window")
    assert added["derived_from"] == str(ids["candidate"])
    assert added["created_by"] == str(alice.id) and added["created_at"]
    assert added["mask"] is not None and added["score"] == pytest.approx(0.45)
    listed = (await api.get("alice", path(sid, photo))).json()
    assert listed["counts"] == {"kitchen_cabinet": 1, "window": 2}


async def test_a_tap_on_a_counted_instance_adds_nothing(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    response = await api.post("alice", path(sid, photo), tap("window", 0.3, 0.3))
    assert response.status_code == 200, response.text
    assert response.json()["created"] is False
    assert response.json()["object"]["id"] == str(ids["window"])
    assert await object_rows(sessionmaker) == 3


async def test_a_box_over_nothing_is_stored_as_a_manual_box(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, _ = await seeded_photo(api, seed, sessionmaker)
    response = await api.post(
        "alice", path(sid, photo), box("electrical_outlet", 0.8, 0.9, 0.7, 0.8)
    )
    assert response.status_code == 201, response.text
    added = response.json()["object"]
    assert added["box"] == pytest.approx({"x1": 0.7, "y1": 0.8, "x2": 0.8, "y2": 0.9})
    assert (added["mask"], added["score"], added["derived_from"]) == (None, None, None)
    assert added["area"] == pytest.approx(0.01)


async def test_a_box_matching_a_candidate_takes_the_candidates_mask(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    response = await api.post("alice", path(sid, photo), box("window", 0.6, 0.1, 0.9, 0.42))
    assert response.status_code == 201
    assert response.json()["object"]["derived_from"] == str(ids["candidate"])


async def test_a_tap_on_nothing_is_422(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, _ = await seeded_photo(api, seed, sessionmaker)
    response = await api.post("alice", path(sid, photo), tap("toilet", 0.5, 0.5))
    assert response.status_code == 422
    assert response.json()["type"].endswith("nothing-at-point")
    assert await object_rows(sessionmaker) == 3


@pytest.mark.parametrize(
    "stage", [Stage.APPRAISAL_REVIEW, Stage.ASSETS_PROCESSING, Stage.ASSETS_REVIEW, Stage.COMPLETED]
)
async def test_edits_outside_rooms_review_are_409(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], stage: Stage
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker, stage=stage)
    added = await api.post("alice", path(sid, photo), box("window", 0.1, 0.1, 0.2, 0.2))
    assert added.status_code == 409
    deleted = await api.delete("alice", f"{path(sid, photo)}/{ids['window']}")
    assert deleted.status_code == 409
    assert await object_rows(sessionmaker) == 3


async def test_edits_before_the_rooms_gate_opens_are_409(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """EVAL_REPORT 26: at rooms_review but processing (the photo stage still runs behind
    the appraisal review): the localizations are not final, so no edit yet."""
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    async with session_scope(sessionmaker) as session:
        await session.execute(
            update(Study).where(Study.id == uuid.UUID(sid)).values(status=StudyStatus.PROCESSING)
        )
    added = await api.post("alice", path(sid, photo), box("window", 0.1, 0.1, 0.2, 0.2))
    assert added.status_code == 409 and added.json()["type"].endswith("gate-not-open")
    deleted = await api.delete("alice", f"{path(sid, photo)}/{ids['window']}")
    assert deleted.status_code == 409
    assert await object_rows(sessionmaker) == 3


async def test_an_archived_study_is_409(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, _ = await seeded_photo(api, seed, sessionmaker)
    assert (await api.delete("alice", f"/v1/studies/{sid}")).status_code == 200
    response = await api.post("alice", path(sid, photo), box("window", 0.1, 0.1, 0.2, 0.2))
    assert response.status_code == 409
    assert response.json()["type"].endswith("study-archived")


async def test_a_viewer_cannot_edit(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, _ = await seeded_photo(api, seed, sessionmaker)
    org = await seed.personal_org("alice")
    await seed.member(org, "victor", OrgRole.VIEWER)
    response = await api.post("victor", path(sid, photo), box("window", 0.1, 0.1, 0.2, 0.2))
    assert response.status_code == 403


async def test_a_retry_with_the_same_key_replays_without_a_second_row(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, _ = await seeded_photo(api, seed, sessionmaker)
    key = str(uuid.uuid4())
    body = box("window", 0.1, 0.6, 0.2, 0.7)
    first = await api.post("alice", path(sid, photo), body, key=key)
    again = await api.post("alice", path(sid, photo), body, key=key)
    assert (first.status_code, again.status_code) == (201, 201)
    assert first.json() == again.json()
    assert await object_rows(sessionmaker) == 4


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "point", "label": "window"},
        {"kind": "box", "label": "window", "point": {"x": 0.1, "y": 0.1}},
        {"kind": "circle", "label": "window", "point": {"x": 0.1, "y": 0.1}},
        tap("not_a_label", 0.1, 0.1),
        tap("window", 1.5, 0.1),
        {**tap("window", 0.1, 0.1), "extra": 1},
        {"kind": "point", "label": "window", "point": {"x": "0.1", "y": 0.1}},
    ],
)
async def test_malformed_selections_are_422(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession], body: dict[str, Any]
) -> None:
    sid, photo, _ = await seeded_photo(api, seed, sessionmaker)
    response = await api.post("alice", path(sid, photo), body)
    assert response.status_code == 422


async def test_post_requires_an_idempotency_key(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, _ = await seeded_photo(api, seed, sessionmaker)
    response = await api.client.post(
        path(sid, photo),
        json=box("window", 0.1, 0.1, 0.2, 0.2),
        headers={"Authorization": "Bearer uid:alice"},
    )
    assert response.status_code == 400
    assert response.json()["type"].endswith("idempotency-key-missing")


# --------------------------------------------------------------------------- DELETE


async def test_delete_soft_deletes_a_manual_instance(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, _ = await seeded_photo(api, seed, sessionmaker)
    alice = await seed.user("alice")
    added = (await api.post("alice", path(sid, photo), box("window", 0.1, 0.6, 0.2, 0.7))).json()
    object_id = added["object"]["id"]
    response = await api.delete("alice", f"{path(sid, photo)}/{object_id}")
    assert response.status_code == 200, response.text
    assert response.json()["deleted_by"] == str(alice.id) and response.json()["deleted_at"]
    listed = (await api.get("alice", path(sid, photo))).json()
    assert object_id not in {i["id"] for i in listed["items"]}
    assert await object_rows(sessionmaker) == 4  # the row stays, deleted
    again = await api.delete("alice", f"{path(sid, photo)}/{object_id}")
    assert again.status_code == 404


async def test_a_model_instance_cannot_be_deleted(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, photo, ids = await seeded_photo(api, seed, sessionmaker)
    response = await api.delete("alice", f"{path(sid, photo)}/{ids['window']}")
    assert response.status_code == 409
    assert response.json()["type"].endswith("not-manual")


async def test_delete_of_an_object_of_another_photo_is_404(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    sid, _, ids = await seeded_photo(api, seed, sessionmaker)
    other_photo = (await seed.outputs(sid))["photo"]
    response = await api.delete("alice", f"{path(sid, other_photo)}/{ids['window']}")
    assert response.status_code == 404
