"""``list_photos``: the study's uploaded photo documents, in a stable order.

Not in the S2_SPEC section 4 table: the workflow needs the photo ids to fan out, and the
workflow input carries only the study id, so a DB-only activity reads them.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from basis.activities._support import activity_key, parse_uuid, run_once
from basis.activities.deps import Deps
from basis.db.enums import DocumentKind
from basis.db.repositories.document import DocumentRepository
from basis.workflow.contracts import ListPhotosInput, ListPhotosOutput


class ListPhotos:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="list_photos")
    async def run(self, input: ListPhotosInput) -> ListPhotosOutput:
        study_id = parse_uuid(input.study_id, "study_id")

        async def work(session: AsyncSession) -> ListPhotosOutput:
            documents = await DocumentRepository(session).list_for_study(study_id)
            photos = [
                str(doc.id)
                for doc in documents
                if doc.kind is DocumentKind.PHOTO and doc.uploaded_at is not None
            ]
            return ListPhotosOutput(document_ids=tuple(photos))

        return await run_once(
            self._deps,
            name="list_photos",
            key=activity_key(),
            output_type=ListPhotosOutput,
            work=work,
        )
