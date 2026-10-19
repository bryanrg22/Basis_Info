"""``PUT /dev/local-uploads/{token}``: the browser's upload on a local stack (dev and test).

On GCS the browser PUTs the file to a V4 signed URL and the bucket enforces the signature.
:class:`~basis.storage.LocalStorage` has no such server, so with ``LOCAL_UPLOAD_BASE_URL``
set it signs URLs that point here, and this route plays the bucket: it hands the body and
headers to :meth:`~basis.storage.LocalStorage.put_signed_token`, which enforces what the
signature would (unexpired token, the signed ``Content-Type`` and content-length range,
the exact size). Like a signed URL the token is the authorization, so the route takes no
bearer token; it answers 200 with an empty body on success, as GCS does, and 403 when the
upload does not match its signature.

Mounted only by :func:`basis.api.app.create_app` when the storage is local and
``BASIS_ENV`` is dev or test, and kept out of the OpenAPI document: it is a stand-in for
the storage service, not part of the API. Outside ``/v1/`` so the 1 MiB JSON body limit
does not apply; the body is read up to :data:`MAX_UPLOAD_BYTES` instead.
"""

from __future__ import annotations

from typing import Final

import anyio.to_thread
from fastapi import APIRouter, Path, Request, Response
from fastapi.responses import PlainTextResponse

from basis.api.errors import Forbidden, PayloadTooLarge
from basis.api.schemas.documents import MAX_UPLOAD_BYTES
from basis.storage import LocalStorage, UploadRejected
from basis.storage.local import LOCAL_UPLOAD_PATH

TOKEN_MAX_LENGTH: Final = 64

router = APIRouter(include_in_schema=False)


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge(f"The upload is larger than {MAX_UPLOAD_BYTES} bytes.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise PayloadTooLarge(f"The upload is larger than {MAX_UPLOAD_BYTES} bytes.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.put(LOCAL_UPLOAD_PATH + "{token}")
async def put_local_upload(
    request: Request,
    token: str = Path(min_length=1, max_length=TOKEN_MAX_LENGTH, pattern=r"^[A-Za-z0-9_-]+$"),
) -> Response:
    storage = request.app.state.storage
    if not isinstance(storage, LocalStorage):  # mounted only for local storage
        raise Forbidden("Local uploads are not enabled.")
    data = await _read_body(request)
    headers = dict(request.headers)
    try:
        await anyio.to_thread.run_sync(storage.put_signed_token, token, data, headers)
    except UploadRejected as exc:
        raise Forbidden(f"Upload rejected: {exc}.") from None
    return PlainTextResponse("", status_code=200)
