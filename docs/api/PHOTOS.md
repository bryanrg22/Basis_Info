# Photos: formats, and the copy a browser shows

An engineer uploads property photos as JPEG, PNG, WebP, HEIC or HEIF
(`POST /v1/studies/{id}/documents`, `content_type` one of `image/jpeg`, `image/png`,
`image/webp`, `image/heic`, `image/heif`). HEIC is what an iPhone saves by default. Every
format is read the same way for the analysis: the photo is decoded, turned upright, stripped
of its metadata (GPS included), shrunk to at most 1,024 px on its long side and sent to the
model as a JPEG (`basis/vision/prep.py`). HEIC is decoded by `pi-heif` (libheif), measured
in EVAL_REPORT section 81.

## `GET /v1/studies/{study_id}/documents/{document_id}/image`

The photo as a browser can show it. Safari shows HEIC; Chrome, Edge and Firefox do not, so
the web app shows every photo from this route and keeps `.../content` for downloads.

| The photo is | The response is |
|---|---|
| JPEG, PNG or WebP | Its own bytes and content type, `ETag: "<sha256>"` (the same as `.../content`) |
| HEIC or HEIF | A JPEG (`image/jpeg`), named `<name>.jpg`: upright, the long side at most 4,096 px (an iPhone's 12-megapixel photo keeps every pixel), quality 90, the colour profile kept, EXIF (with GPS) and XMP dropped. `ETag: "<sha256>.display-v1"` |

Both carry `Cache-Control: private, max-age=3600`, `X-Content-Type-Options: nosniff` and an
inline `Content-Disposition`. `If-None-Match` with the ETag is 304 with no body. The
`display-v1` in a converted photo's ETag names the conversion rule: a later rule gets a new
name, so no browser keeps a copy made by the old one.

A viewer of the study (or higher) may read it.

| Status | `type` ends with | Code | When |
|---|---|---|---|
| 404 | `not-found` | `BASIS-UPLOAD-006` | No such document in the study, or a study the caller cannot see |
| 404 | `not-found` | `BASIS-UPLOAD-007` | The bytes are gone from storage |
| 409 | `upload-missing` | `BASIS-UPLOAD-002` | The upload is not confirmed yet |
| 409 | `not-a-photo` | `BASIS-UPLOAD-012` | The document is a PDF, ZIP, XML or text file (use `.../content` or `.../pdf`) |
| 409 | `unreadable-photo` | `BASIS-UPLOAD-013` | A HEIC or HEIF that cannot be decoded (damaged, cut short, over 80 megapixels); the analysis flags the same photo as not readable |

A conversion takes about 0.1 to 0.7 s for an iPhone photo and runs on a thread, one at a time
per API process (a 24-megapixel HEIC needs about 370 MB while it decodes; the API container
has 1 GiB).

## A HEIC cut short is refused

libheif decodes an iPhone photo that ends early with the missing parts left blank and no
error. Basis refuses a HEIC shorter than its own top-level box sizes declare, both for the
analysis (the photo is flagged as not readable) and on this route (409 `unreadable-photo`).
A JPEG cut short is refused the same way, by Pillow.
