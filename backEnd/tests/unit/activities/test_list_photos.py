"""``list_photos``: uploaded photos of the study only."""

from __future__ import annotations

import secrets
import uuid

import pytest
from lifecycletest_support import TEST_BUCKET, SeededStudy

from basis.activities import Deps
from basis.activities.list_photos import ListPhotos
from basis.db.enums import DocumentKind
from basis.db.repositories.document import DocumentRepository
from basis.db.session import session_scope
from basis.workflow.contracts import ListPhotosInput

from .conftest import Env

pytestmark = pytest.mark.integration


async def test_lists_uploaded_photos_only(
    deps: Deps, seeded: SeededStudy, activity_env: Env
) -> None:
    async with session_scope(deps.sessionmaker) as session:
        pending = uuid.uuid4()
        await DocumentRepository(session).create(  # registered, never uploaded
            study_id=seeded.study_id,
            kind=DocumentKind.PHOTO,
            filename="pending.jpg",
            content_type="image/jpeg",
            size_bytes=10,
            sha256=secrets.token_hex(32),
            gcs_uri=f"gs://{TEST_BUCKET}/{pending}",
            uploaded_by=seeded.owner_id,
            document_id=pending,
        )
    output = await activity_env.run(
        ListPhotos(deps).run, ListPhotosInput(study_id=str(seeded.study_id))
    )
    assert sorted(output.document_ids) == sorted(str(p) for p in seeded.photo_ids)
    assert str(seeded.appraisal_id) not in output.document_ids
    assert str(pending) not in output.document_ids
