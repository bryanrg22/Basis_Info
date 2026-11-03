# Support access to a firm's files (`/v1/ops/studies/{id}/documents`)

A firm's studies are visible only to its members (docs/api/FIRMS.md). This is the one
exception, decided by Bryan on 2026-11-02: Basis support may open the files a firm uploaded to
a study (the appraisal, the photos), because the first firms are new to the product and
support has to see what they uploaded to help. A missing-input record (CLIENT_ERRORS.md 4.1)
lists the study's documents by id; these routes list and open them.

## 1. Who

Platform operators only: a verified token whose uid is in `OPS_OPERATOR_UIDS`
(`basis.api.operators.require_operator`, as for `GET /v1/ops/client-errors`). A firm role,
owner included, never grants it: 403 `not-operator` (`BASIS-API-043`), and nothing is recorded.
The firm's contract with Basis should say that Basis support may view its uploads for support
(Bryan's to do; not a code change).

## 2. `GET /v1/ops/studies/{study_id}/documents`

Every document of the study, oldest first, confirmed or not, with `uploaded_by`, and the
study's name, status, firm id and firm name. Names and sizes, no content: nothing is recorded
on the study's timeline; the API log has one line (the operator, the study, the firm). An
unknown study is 404 (`BASIS-STUDY-017`).

## 3. `GET /v1/ops/studies/{study_id}/documents/{document_id}/content?reason=...`

The document's bytes, as the firm's own `GET /v1/studies/{id}/documents/{id}/content`
serves them (the registered content type, `Content-Disposition` with the file's name,
`X-Content-Type-Options: nosniff`), with two differences:

- **Never cached**: `Cache-Control: no-store`, no `ETag`, and `If-None-Match` is ignored (no
  304). Each open is an access, so each is answered and recorded, and the operator's browser
  keeps no copy.
- **A reason is required**: `reason`, 3 to 300 characters, what support is helping with (for
  example `support: study_input input:gla (record 1a2b3c4d)`); it is shown to the firm, so it
  carries no client figures. `client_error_id` (optional) names the record the open comes
  from.

| answer | when |
|---|---|
| 200 | the bytes |
| 403 `not-operator` | not an operator (nothing recorded) |
| 404 `BASIS-STUDY-017` | no such study (nothing recorded) |
| 404 `BASIS-UPLOAD-006` | no such document in that study (nothing recorded) |
| 409 `upload-missing` | the upload is not confirmed (nothing recorded) |
| 404 `BASIS-UPLOAD-007` | the bytes are gone from storage (the retention purge): the access was granted and stays recorded |
| 422 | no reason, a reason under 3 or over 300 characters, a malformed `client_error_id` (nothing recorded) |

## 4. The access record

Every granted open appends one row to the study's timeline (`study_events`, append-only:
`forbid_mutation()` refuses an update or delete) in its own transaction, committed before the
bytes are read:

| field | value |
|---|---|
| `event_type` | `operator.document_accessed` |
| `actor_type`, `actor_id` | `operator`, the operator's `users` row (migration `0030` added the actor) |
| `stage`, `revision` | the study's at the time |
| `entity_type`, `entity_id` | `document`, the document |
| `request_id` | the API request |
| `detail` | `reason`, `client_error_id`, `filename`, `kind`, `sha256`, `bytes` |

The firm reads it on `GET /v1/studies/{id}/timeline` (any member): who at Basis opened which
file, when and why. This is the practice of a cloud provider's access-transparency log, which
records each access by its staff to a customer's content with the resource, the time, the
justification and the accessor (Google Cloud Access Transparency). Not modelled on it: the
customer's prior approval of each access (Access Approval, Customer Lockbox); Bryan chose
access without per-study approval while the product is new. The audit
(`GET .../audit`, the engineer's actions) does not list it; `GET /v1/me/activity` lists only a
member's own actions in their firm's studies.

## 5. The developer page

`web/developer/client-errors.html`: a `study_input` record's detail shows "Files the firm
uploaded" (name, kind, type, size, upload time and uploader, sha256) with an Open button for
each confirmed file. Open asks for the reason (prefilled with the record's kind, blocker and
id), sends `client_error_id`, and shows the file in the page under the table with a Download
link (a `blob:` URL, revoked when the view is closed or replaced). No popup tab: a tab opened
after the request is blocked by browsers, which the live check met. Only an image (in `<img>`)
or a PDF is shown inline; any other type (a MISMO XML, a UAD ZIP, text) is a download only,
because markup in it would run with the page's origin from a `blob:` URL.
