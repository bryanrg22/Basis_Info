"""``/v1/studies/{id}/photos/{document_id}/objects``: read, add a manual selection, delete.

Rows are seeded through the repository as ``localize_photo`` writes them (two windows, one
counted and one candidate below the window threshold, a counted cabinet) on a synthetic
photo of the study; the study is moved to ``rooms_review`` directly.

The box-first add (a box that matches no stored instance prompts SAM 3) runs against a
fake localizer injected into the app (:class:`Sam`: scripted instances, an error, a delay
past ``OBJECT_PROMPT_TIMEOUT_S``, or a callback while the call is in flight), with the
photo's bytes in storage; the default ``api`` app has no localizer.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
import pytest
from apitest_support import BASE_URL, Api, FakeVerifier, Seed, api_settings, build_api_app
from PIL import Image
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basis.api.errors import PROBLEM_JSON
from basis.db.enums import OrgRole, Stage, StudyStatus
from basis.db.models import Document, ObjectPrompt, PhotoObject, Study
from basis.db.repositories.photo_object import NewObject, PhotoObjectRepository
from basis.db.session import session_scope
from basis.storage import LocalStorage
from basis.vision.localize import (
    Box,
    FakeLocalizer,
    Instance,
    LocalizerBadResponse,
    LocalizerUnavailable,
    Prompted,
    Rle,
)
from basis.vision.prep import PreparedImage
from basis.vision.schemas import ComponentLabel

pytestmark = pytest.mark.integration


def mask(r0: int, r1: int, c0: int, c1: int) -> dict[str, Any]:
    grid = np.zeros((10, 10), dtype=bool)
    grid[r0:r1, c0:c1] = True
    return Rle.encode(grid).model_dump(mode="json")


async def seeded_photo(
    api: Api,
    seed: Seed,
    sm: async_sessionmaker[AsyncSession],
    stage: Stage = Stage.ROOMS_REVIEW,
    *,
    components_gate: bool | None = None,
) -> tuple[str, str, dict[str, uuid.UUID]]:
    """(study id, photo id, {name: object id}) with three model rows and a status. The
    study's ``components_gate`` is left null (the single rooms review, where these tests
    edit at ``rooms_review``) unless given."""
    study = await api.create_study("alice")
    sid = str(study["id"])
    outputs = await seed.outputs(sid)
    await seed.stage(sid, stage, components_gate=components_gate)
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
    assert (added["mask_source"], added["mask_reason"]) == ("candidate", None)
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
    assert response.json()["duplicate_of"] == str(ids["window"])
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
    assert added["mask_source"] == "none" and "no localizer" in added["mask_reason"]


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


@pytest.mark.parametrize(
    ("components_gate", "stage", "editable"),
    [
        # Patch components-gate-v1: the objects are edited at the components review only.
        (True, Stage.COMPONENTS_REVIEW, True),
        (True, Stage.ROOMS_REVIEW, False),
        (True, Stage.COMPONENTS_PROCESSING, False),
        (True, Stage.ASSETS_REVIEW, False),
        # A study whose workflow has the single rooms review keeps editing there.
        (False, Stage.ROOMS_REVIEW, True),
        (None, Stage.ROOMS_REVIEW, True),
        (False, Stage.COMPONENTS_REVIEW, False),
    ],
)
async def test_the_edit_stage_follows_the_studys_gates(
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    components_gate: bool | None,
    stage: Stage,
    editable: bool,
) -> None:
    sid, photo, ids = await seeded_photo(
        api, seed, sessionmaker, stage=stage, components_gate=components_gate
    )
    added = await api.post("alice", path(sid, photo), box("window", 0.6, 0.6, 0.7, 0.7))
    if not editable:
        assert added.status_code == 409, added.text
        assert added.json()["type"].endswith("stale-state")
        expected = "components_review" if components_gate else "rooms_review"
        assert f"only while the study waits at {expected}" in added.json()["detail"]
        deleted = await api.delete("alice", f"{path(sid, photo)}/{ids['window']}")
        assert deleted.status_code == 409
        assert await object_rows(sessionmaker) == 3
        return
    assert added.status_code == 201, added.text
    manual = added.json()["object"]
    deleted = await api.delete("alice", f"{path(sid, photo)}/{manual['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_at"] is not None


async def test_edits_before_the_components_gate_opens_are_409(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """At components_review but not waiting (a failed room step holds it): no edit."""
    sid, photo, _ = await seeded_photo(
        api, seed, sessionmaker, stage=Stage.COMPONENTS_REVIEW, components_gate=True
    )
    async with session_scope(sessionmaker) as session:
        await session.execute(
            update(Study).where(Study.id == uuid.UUID(sid)).values(status=StudyStatus.FAILED)
        )
    added = await api.post("alice", path(sid, photo), box("window", 0.6, 0.6, 0.7, 0.7))
    assert added.status_code == 409 and added.json()["type"].endswith("gate-not-open")
    assert "components review is not open" in added.json()["detail"]
    assert await object_rows(sessionmaker) == 3


async def test_edits_before_the_rooms_gate_opens_are_409(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """EVAL_REPORT 27: at rooms_review but processing (the photo stage still runs behind
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


async def test_a_selection_before_the_photo_is_localized_is_409(
    api: Api, seed: Seed, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """EVAL_REPORT 30: the rooms review is open while SAM 3 still runs; a photo with no
    localization row yet cannot take a selection (it would not resolve against the
    instances about to arrive), and the GET says it is pending (no localization)."""
    study = await api.create_study("alice")
    sid = str(study["id"])
    outputs = await seed.outputs(sid)
    await seed.stage(sid, Stage.ROOMS_REVIEW)
    photo = outputs["photo"]
    response = await api.post("alice", path(sid, photo), box("window", 0.1, 0.1, 0.2, 0.2))
    assert response.status_code == 409
    assert response.json()["type"].endswith("localization-pending")
    assert await object_rows(sessionmaker) == 0
    listed = (await api.get("alice", path(sid, photo))).json()
    assert listed["localization"] is None and listed["items"] == []


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


# --------------------------------------------------------------------------- box-first add

# The photo the box prompts read from storage: 320 x 240 (prepare_image keeps it as is), so
# SAM 3's masks are 240 x 320 while the seeded rows' masks are 10 x 10 (the guard then
# compares boxes, as it does whenever two outlines differ in size).
PHOTO_SIZE = (320, 240)


def photo_jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", PHOTO_SIZE, "white").save(out, format="JPEG", quality=90)
    return out.getvalue()


def sam_instance(
    image: PreparedImage, box: tuple[float, float, float, float], score: float
) -> Instance:
    """A SAM 3 instance on the prepared photo: a filled rectangle mask over ``box``."""
    width, height = image.width, image.height
    x1, y1, x2, y2 = (
        round(v * s) for v, s in zip(box, (width, height, width, height), strict=True)
    )
    grid = np.zeros((height, width), dtype=bool)
    grid[y1:y2, x1:x2] = True
    rle = Rle.encode(grid)
    return Instance(
        label=ComponentLabel.WINDOW,
        phrase="house window",
        score=score,
        box=Box.from_pixels(x1, y1, x2, y2, width, height),
        mask=rle,
        area=rle.area() / (width * height),
    )


@dataclass
class Sam(FakeLocalizer):
    """The fake localizer with the failures a live call has: an error, or a delay longer
    than OBJECT_PROMPT_TIMEOUT_S; ``during`` runs while the call is in flight."""

    fail: Exception | None = None
    delay_s: float = 0.0
    during: Callable[[], Awaitable[None]] | None = None
    version: str = "v-sam-test"

    async def segment_prompt(self, image: PreparedImage, **kwargs: Any) -> Prompted:
        if self.during is not None:
            await self.during()
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fail is not None:
            self.prompted.append((image.sha256, kwargs))
            raise self.fail
        return await super().segment_prompt(image, **kwargs)


TIMEOUT_S = 0.5


@pytest.fixture
def sam() -> Sam:
    return Sam(seconds_per_call=4.0)


@pytest.fixture
async def sam_api(
    database_url: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    verifier: FakeVerifier,
    sam: Sam,
) -> AsyncIterator[Api]:
    settings = api_settings(database_url, object_prompt_timeout_s=TIMEOUT_S)
    app = build_api_app(settings, sessionmaker, storage, verifier, localizer=sam)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield Api(client)


async def stored_photo(
    api: Api, seed: Seed, sm: async_sessionmaker[AsyncSession], storage: LocalStorage
) -> tuple[str, str, dict[str, uuid.UUID]]:
    """:func:`seeded_photo` with the photo's bytes in storage."""
    sid, photo, ids = await seeded_photo(api, seed, sm)
    async with sm() as session:
        uri = await session.scalar(select(Document.gcs_uri).where(Document.id == uuid.UUID(photo)))
    assert uri is not None
    await storage.put(uri, photo_jpeg(), "image/jpeg")
    return sid, photo, ids


async def prompts(sm: async_sessionmaker[AsyncSession]) -> list[ObjectPrompt]:
    async with sm() as session:
        return list((await session.scalars(select(ObjectPrompt))).all())


DRAWN = (0.55, 0.55, 0.85, 0.85)  # no stored window there


async def test_a_box_over_nothing_stored_is_outlined_by_sam(
    sam_api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    sam: Sam,
) -> None:
    """SAM 3 is prompted with the label's v3 phrase and the box; of the similar instances
    it returns (concept mode) the one overlapping the box best is stored with its mask and
    score; the call's cost is recorded and linked."""
    sid, photo, _ = await stored_photo(sam_api, seed, sessionmaker, storage)
    sam.script = {
        "house window": lambda image: [
            sam_instance(image, (0.56, 0.56, 0.84, 0.86), 0.88),  # the drawn window
            sam_instance(image, (0.70, 0.55, 0.95, 0.85), 0.93),  # overlaps it at IoU 0.375
            sam_instance(image, (0.10, 0.60, 0.30, 0.90), 0.97),  # a similar window elsewhere
        ]
    }
    response = await sam_api.post("alice", path(sid, photo), box("window", *DRAWN))
    assert response.status_code == 201, response.text
    body = response.json()
    added = body["object"]
    assert (body["created"], body["duplicate_of"], added["source"]) == (True, None, "manual")
    assert (added["mask_source"], added["mask_reason"], added["phrase"]) == (
        "sam_box_prompt",
        None,
        "house window",
    )
    assert added["score"] == pytest.approx(0.88) and added["derived_from"] is None
    assert (added["mask"]["height"], added["mask"]["width"]) == (240, 320)
    assert added["box"] == pytest.approx(
        {"x1": 0.5594, "y1": 0.5583, "x2": 0.8406, "y2": 0.8583}, abs=1e-3
    )
    [(_, sent)] = sam.prompted
    assert sent["boxes"] == [Box(x1=0.55, y1=0.55, x2=0.85, y2=0.85)]
    assert (sent["phrases"], sent["threshold"], sent["label"]) == (
        ["house window"],
        0.5,
        ComponentLabel.WINDOW,
    )
    [call] = await prompts(sessionmaker)
    assert (call.status, call.returned, call.reason, call.label) == ("done", 3, None, "window")
    assert call.kept_score == pytest.approx(0.88) and call.model_version == "v-sam-test"
    assert (call.predict_seconds, call.cost_usd) == (4.0, pytest.approx(4.0 * 0.000975))
    assert call.params["phrases"] == ["house window"] and call.params["min_iou"] == 0.5
    assert call.params["box"] == {"x1": 0.55, "y1": 0.55, "x2": 0.85, "y2": 0.85}
    async with sessionmaker() as session:
        row = await session.get(PhotoObject, uuid.UUID(added["id"]))
    assert row is not None and row.prompt_id == call.id
    listed = (await sam_api.get("alice", path(sid, photo))).json()
    assert listed["counts"] == {"kitchen_cabinet": 1, "window": 2}


async def test_a_box_sam_cannot_outline_is_stored_alone_with_the_reason(
    sam_api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    sam: Sam,
) -> None:
    sid, photo, _ = await stored_photo(sam_api, seed, sessionmaker, storage)
    sam.script = {"house window": lambda image: [sam_instance(image, (0.1, 0.6, 0.3, 0.9), 0.97)]}
    response = await sam_api.post("alice", path(sid, photo), box("window", *DRAWN))
    assert response.status_code == 201, response.text
    added = response.json()["object"]
    assert (added["mask_source"], added["mask"], added["score"]) == ("none", None, None)
    assert added["mask_reason"] == (
        "SAM 3 returned 1 window instance(s), none overlapping the box at IoU >= 0.5 with "
        "score >= 0.5"
    )
    assert added["box"] == pytest.approx({"x1": 0.55, "y1": 0.55, "x2": 0.85, "y2": 0.85})
    [call] = await prompts(sessionmaker)
    assert (call.status, call.returned, call.kept_score) == ("done", 1, None)


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (
            LocalizerUnavailable("POST https://api.replicate.com/v1/predictions returned 503"),
            "SAM 3 failed: LocalizerUnavailable: POST https://api.replicate.com/v1/predictions "
            "returned 503",
        ),
        (LocalizerBadResponse("prediction p1 failed: CUDA"), "SAM 3 failed: LocalizerBadResponse"),
        ("slow", f"SAM 3 did not answer within {TIMEOUT_S:g} s"),
    ],
)
async def test_a_failed_or_slow_call_never_fails_the_add(
    sam_api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    sam: Sam,
    failure: Exception | str,
    reason: str,
) -> None:
    """The box is the engineer's evidence: it is stored, and the reason says why it has
    no outline; the failed call is recorded (it may have been billed)."""
    sid, photo, _ = await stored_photo(sam_api, seed, sessionmaker, storage)
    if failure == "slow":
        sam.delay_s = 5.0  # ten times OBJECT_PROMPT_TIMEOUT_S
    else:
        assert isinstance(failure, Exception)
        sam.fail = failure
    response = await sam_api.post("alice", path(sid, photo), box("window", *DRAWN))
    assert response.status_code == 201, response.text
    added = response.json()["object"]
    assert added["mask_source"] == "none" and added["mask_reason"].startswith(reason)
    [call] = await prompts(sessionmaker)
    assert call.status == "failed" and call.reason is not None and call.reason.startswith(reason)
    assert call.cost_usd is None and call.model_version == "v-sam-test"
    assert await object_rows(sessionmaker) == 4


async def test_without_the_photo_or_a_localizer_the_box_is_stored_with_the_reason(
    sam_api: Api,
    api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    sam: Sam,
) -> None:
    sid, photo, _ = await seeded_photo(sam_api, seed, sessionmaker)  # no bytes in storage
    response = await sam_api.post("alice", path(sid, photo), box("window", *DRAWN))
    assert response.status_code == 201, response.text
    assert response.json()["object"]["mask_reason"] == "the photo file is missing from storage"
    no_localizer = await api.post("alice", path(sid, photo), box("window", 0.9, 0.9, 0.95, 0.95))
    assert no_localizer.status_code == 201, no_localizer.text
    assert no_localizer.json()["object"]["mask_reason"] == (
        "no localizer is configured (LOCALIZER_URL or REPLICATE_API_TOKEN)"
    )
    assert sam.prompted == [] and await prompts(sessionmaker) == []  # no call, no record


async def test_an_idempotent_replay_does_not_call_sam_again(
    sam_api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    sam: Sam,
) -> None:
    sid, photo, _ = await stored_photo(sam_api, seed, sessionmaker, storage)
    sam.script = {
        "house window": lambda image: [sam_instance(image, (0.56, 0.56, 0.84, 0.86), 0.9)]
    }
    key = str(uuid.uuid4())
    first = await sam_api.post("alice", path(sid, photo), box("window", *DRAWN), key=key)
    again = await sam_api.post("alice", path(sid, photo), box("window", *DRAWN), key=key)
    assert (first.status_code, again.status_code) == (201, 201)
    assert again.headers["idempotent-replayed"] == "true" and again.json() == first.json()
    assert len(sam.prompted) == 1 and len(await prompts(sessionmaker)) == 1
    assert await object_rows(sessionmaker) == 4


async def test_a_box_over_a_stored_candidate_never_calls_sam(
    sam_api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    sam: Sam,
) -> None:
    sid, photo, ids = await stored_photo(sam_api, seed, sessionmaker, storage)
    response = await sam_api.post("alice", path(sid, photo), box("window", 0.6, 0.1, 0.9, 0.42))
    assert response.status_code == 201, response.text
    added = response.json()["object"]
    assert (added["derived_from"], added["mask_source"]) == (str(ids["candidate"]), "candidate")
    assert added["mask"] is not None and sam.prompted == []


async def test_a_candidate_chosen_by_id_is_promoted(
    sam_api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    sam: Sam,
) -> None:
    """The review previews the candidates under a tap and the engineer confirms one."""
    sid, photo, ids = await stored_photo(sam_api, seed, sessionmaker, storage)
    chosen = {"kind": "candidate", "candidate_id": str(ids["candidate"])}
    response = await sam_api.post("alice", path(sid, photo), chosen)
    assert response.status_code == 201, response.text
    added = response.json()["object"]
    assert (added["derived_from"], added["mask_source"], added["label"]) == (
        str(ids["candidate"]),
        "candidate",
        "window",
    )
    assert added["score"] == pytest.approx(0.45) and added["mask"] is not None
    # Choosing it again: the new manual instance covers it, so nothing is added.
    again = await sam_api.post("alice", path(sid, photo), chosen)
    assert again.status_code == 200 and again.json()["duplicate_of"] == added["id"]
    # A counted instance by id: nothing to add.
    counted = {"kind": "candidate", "candidate_id": str(ids["window"])}
    response = await sam_api.post("alice", path(sid, photo), counted)
    assert response.status_code == 200
    assert response.json()["duplicate_of"] == str(ids["window"])
    # Not an instance of this photo at this revision: 404; not a UUID, or extra fields: 422.
    for body, status_code in (
        ({"kind": "candidate", "candidate_id": str(uuid.uuid4())}, 404),
        ({"kind": "candidate", "candidate_id": "nope"}, 422),
        ({"kind": "candidate", "candidate_id": str(ids["candidate"]), "label": "x"}, 422),
    ):
        got = await sam_api.post("alice", path(sid, photo), body)
        assert got.status_code == status_code, (body, got.text)
    assert sam.prompted == [] and await object_rows(sessionmaker) == 4


async def test_a_duplicate_answers_the_counted_instance_until_added_anyway(
    sam_api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    sam: Sam,
) -> None:
    """A box drawn inside the counted window (IoU 0.25 with it, so SAM 3 is asked) whose
    outline lies inside the counted one is that window (intersection over the smaller:
    1.0); ``allow_duplicate`` adds it (the two-lights case)."""
    sid, photo, ids = await stored_photo(sam_api, seed, sessionmaker, storage)
    inner = (0.1, 0.1, 0.3, 0.3)
    sam.script = {"house window": lambda image: [sam_instance(image, inner, 0.9)]}
    response = await sam_api.post("alice", path(sid, photo), box("window", *inner))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["created"], body["duplicate_of"], body["overlap"]) == (
        False,
        str(ids["window"]),
        1.0,
    )
    assert body["object"]["id"] == str(ids["window"]) and await object_rows(sessionmaker) == 3
    [call] = await prompts(sessionmaker)  # the call was made (and billed) all the same
    assert call.status == "done"
    anyway = await sam_api.post(
        "alice", path(sid, photo), {**box("window", *inner), "allow_duplicate": True}
    )
    assert anyway.status_code == 201, anyway.text
    assert anyway.json()["object"]["mask_source"] == "sam_box_prompt"
    listed = (await sam_api.get("alice", path(sid, photo))).json()
    assert listed["counts"]["window"] == 2
    # A box right over the counted window, or a tap on it: the window, no call.
    calls = len(sam.prompted)
    for selection in (box("window", 0.1, 0.1, 0.5, 0.5), tap("window", 0.3, 0.3)):
        got = await sam_api.post("alice", path(sid, photo), selection)
        assert got.status_code == 200 and got.json()["duplicate_of"] == str(ids["window"])
    assert len(sam.prompted) == calls
    assert (
        await sam_api.post(
            "alice", path(sid, photo), {**tap("window", 0.3, 0.3), "allow_duplicate": "yes"}
        )
    ).status_code == 422


async def test_a_decision_during_the_call_makes_the_add_stale(
    sam_api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    sam: Sam,
) -> None:
    """No lock is held while SAM 3 runs, so the gate can move meanwhile: the write checks
    the stage again and refuses (409), and the billed call stays recorded."""
    sid, photo, _ = await stored_photo(sam_api, seed, sessionmaker, storage)
    sam.script = {
        "house window": lambda image: [sam_instance(image, (0.56, 0.56, 0.84, 0.86), 0.9)]
    }

    async def approve_meanwhile() -> None:
        await seed.stage(sid, Stage.ASSETS_PROCESSING)

    sam.during = approve_meanwhile
    response = await sam_api.post("alice", path(sid, photo), box("window", *DRAWN))
    assert response.status_code == 409 and response.json()["type"].endswith("stale-state")
    assert await object_rows(sessionmaker) == 3
    assert [c.status for c in await prompts(sessionmaker)] == ["done"]


async def test_the_call_holds_no_database_connection(
    sam_api: Api,
    seed: Seed,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    sam: Sam,
    db_engine: AsyncEngine,
) -> None:
    """EVAL_REPORT 32: a slow call inside a transaction held a pooled connection (and the
    study's share lock). While SAM 3 runs, every connection is back in the pool."""
    sid, photo, _ = await stored_photo(sam_api, seed, sessionmaker, storage)
    held = {"now": 0}
    pool = db_engine.sync_engine.pool

    def out(*_: Any) -> None:
        held["now"] += 1

    def back(*_: Any) -> None:
        held["now"] -= 1

    seen: list[int] = []

    async def checked_out() -> None:
        seen.append(held["now"])

    sam.during = checked_out
    event.listen(pool, "checkout", out)
    event.listen(pool, "checkin", back)
    try:
        response = await sam_api.post("alice", path(sid, photo), box("window", *DRAWN))
    finally:
        event.remove(pool, "checkout", out)
        event.remove(pool, "checkin", back)
    assert response.status_code == 201, response.text
    assert seen == [0] and held["now"] == 0
