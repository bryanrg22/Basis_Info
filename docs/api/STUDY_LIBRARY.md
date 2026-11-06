# The study library: search, filters, sort, pages, archive and unarchive

`GET /v1/studies` is the firm's library: every study of every firm the caller belongs to,
narrowed, ordered and counted on the server. Before this the route listed newest first and
the web app (`web/live/live-app.js`) searched, filtered and sorted the whole list in the
browser; that still works (the response shape is a superset), and section 5 says how to
move each piece to the server.

## 1. The request

```
GET /v1/studies?q=harbor&status=waiting_review&status=failed&created_by=<user>&include_archived=false&sort=updated_desc&limit=50&cursor=<next_cursor>
```

| Parameter | Meaning |
|---|---|
| `q` | Search words (section 1.1): each must be found, as a substring with case and accents ignored, in one of the study's searched values (the property name, the address, the client, the parcel number, the ZIP, the creator, the signer). Blank matches everything. `%`, `_` and `\` are literal characters, not wildcards. At most 200 characters. |
| `status` | Repeatable: only studies in these statuses (`draft`, `processing`, `waiting_review`, `stalled`, `completed`, `failed`, `archived`). |
| `created_by` | Only studies this user created (`users.id`, the `created_by` member of a study). |
| `include_archived` | `true` lists archived studies too. They are hidden by default, and shown when `status` names `archived`, so a client can show the archive without the switch. |
| `sort` | `updated_desc` (default), `created_desc` or `name_asc` (section 2). |
| `limit`, `cursor` | Page size (1 to 100, default 50) and the previous page's `next_cursor`. |

Every filter is applied before counting and paging: the page, `next_cursor` and `total`
all describe the same filtered listing.

### 1.1 What `q` searches (EVAL_REPORT 92)

`q` is split on whitespace into words; a study matches when **every** word is a substring of
**some** searched value of that study, in any order. Case and accents never decide a match
(`cafe` finds `Café`, `CAFÉ` finds `cafe`): both sides go through the database's
`study_search_fold` (`lower(unaccent(...))`). A query that matched before this release (a
substring of the property name) still matches.

| Searched value | Where it lives |
|---|---|
| Property name | `studies.property_name` |
| ZIP and state the engineer entered | the intake (`studies.intake`: `zip`, `state`) |
| Taxpayer (the client) | the report answers (`studies.report_answers.taxpayer_name`) |
| Address, city, state, ZIP, county | the appraisal's subject fields (`property_address`, `city`, `state`, `zip`, `county`), every revision's, an engineer's correction included |
| Borrower, owner of public record | the appraisal's subject fields (`borrower`, `owner_of_public_record`) |
| Parcel number | the appraisal's `assessors_parcel` (`205-118-34`; any part of it) |
| Creator | the creating user's name and email (`users`), as they are now (a rename rewrites the documents of the studies they created) |
| Signer | each signature's attested engineer name and email (`report_signatures.engineer`) |

Not searched: the appraisal's lender/client (the same few banks across a library, so it
filters nothing), every other appraisal field (a year, a material), the study id. **There is
no file or job number in Basis**: no study, intake or report field carries a firm's own file
number, and the appraisal's file number is not extracted. A firm that searches by file number
needs that field added first (an intake or study field the engineer types); once it is a
column or an intake member it joins the document below with one line in the migration that
adds it.

A ZIP or a parcel number is matched like any other text: `93035` finds the ZIP, `9303` finds
every ZIP that starts (or contains) it, `118-34` finds the parcel `205-118-34`. One or two
letters match a great deal, as the web app's in-browser search did; the server answers them
as fast as longer words (EVAL_REPORT 92).

How: one search document per study (`study_search`, migration 0034), kept by the database's
triggers in the same transaction as the write that changes a source (the study's name, intake
or report answers; an extracted subject field; a signature; the creator's name or email),
folded, one value per line, with a pg_trgm GIN index that serves `LIKE '%word%'`. Each word is
one `EXISTS` on the document, so a rare word starts from the index. A document change never
moves `updated_at` (the default sort): the document is its own table.

## 2. The response and the sorts

```json
{"items": [...], "total": 128, "next_cursor": "eyJzIjoidXBkYXRlZF9kZXNjIiwi..."}
```

`total` is how many studies match the filters in all, as of this request. `items` is one
page in the sort's order; `next_cursor` is null on the last page.

| `sort` | Order | Key |
|---|---|---|
| `updated_desc` | most recently updated first | `(updated_at DESC, id DESC)`. `updated_at` is set by the database trigger on every write to the study row: an intake write, a stage move, a decision's revision bump, an archive. A study moves to the top when anything about it changes. |
| `created_desc` | newest first | `(created_at DESC, id DESC)`, the order the route had before sorting existed. |
| `name_asc` | property name A to Z | `(lower(property_name) ASC, id ASC)`: case does not split the alphabet; equal names keep a stable order across pages. |

The default changed from newest first to most recently updated first, which is what the web
app shows by default ("Last updated").

Why the cursor is keyset, not an offset: a study inserted or updated between two page
requests shifts every offset after it, so an offset page repeats or skips a row. The cursor
names the last row's sort key, and the next page starts strictly after it, so pages are
consistent while rows arrive (`tests/api/test_study_library.py` inserts a row between pages
and checks). The cursor encodes the sort it was issued under; a cursor replayed under another
sort is 422 `invalid-cursor` (`BASIS-API-030`), since its key would not compare. A cursor
issued before this release (the `{t, id}` form) is refused the same way: fetch the first page
again.

`total` costs one `count(*)` over the filtered join per request. The listing is the
caller's firms' rows (the membership join, `ix_studies_org_id_created_at`); each word of `q`
is an index probe of the study's search document (section 1.1) and the sorts order the rows.
Measured on synthetic libraries of 2,000, 20,000 and 200,000 studies (the caller's firm a
quarter of them), the search request's median and 95th percentile are in EVAL_REPORT 92. No
index was added for the new sorts: an index on `(org_id, updated_at)` is a one-line
migration when a firm's library outgrows a scan.

## 3. Status filter groups the web app uses

| Web filter | `status` values |
|---|---|
| All studies | (none) |
| Needs review | `waiting_review`, `stalled`, `failed` |
| Processing | `processing` |
| Completed | `completed` |
| Drafts | `draft` |

The web app shows a count on each filter chip. On the server that is one request per chip
(`limit=1` and read `total`), or one unfiltered listing counted in the browser as today.

## 4. Archive and unarchive

```
DELETE /v1/studies/{id}             owner; the study becomes `archived` (soft delete)
POST   /v1/studies/{id}/unarchive   owner; the study comes back
```

Archiving keeps the row and every output; the study leaves the default listing and refuses
writes (the intake and the report answers answer 409 `stale-state`, an upload 409
`study-archived`, a decision 409 `stale-state` because its stage move skips an archived
row). Unarchiving
restores the status the study's stage implies, the way the workflow derives it:

| Stage | Status after unarchive |
|---|---|
| `uploading` | `draft` |
| a processing stage | `processing` |
| a review stage | `waiting_review` (a study that was `stalled` comes back `waiting_review`; the reminder step marks it stalled again if the review is still waiting) |
| `completed` | `completed` |
| any, with a failure recorded (`failure_reason` set) | `failed` |

Both are idempotent: archiving an archived study, or unarchiving one that is not archived,
returns it unchanged and records nothing. Whether there is anything to archive or restore is
decided under the study's row lock, so several archives (or unarchives) at once all answer 200
and exactly one records the change (the tests hold the row locked while the requests queue on
it; EVAL_REPORT 56 and 59). Each change is on the timeline (`study.archived`,
`study.unarchived` with `after.status`), in the audit listing (`archived`, `unarchived`), and
in the owner's `GET /v1/me/activity`.

### A running study is not archived (EVAL_REPORT 62)

A study whose workflow execution is **running** (in progress, waiting at a review gate, or
holding a failed stage for a retry) answers `409 workflow-running` (`BASIS-STUDY-035`) and is
unchanged. The route asks Temporal (`describe`) when the study has a `workflow_id` and is not
archived already; a study never started asks nothing. The owner cancels first
(`POST /v1/studies/{id}/cancel`), waits for `GET .../workflow` to show `execution_status` other
than `RUNNING` (the workflow stops its activities and records the run's end, a few seconds),
then archives. An execution that ended any way (completed, cancelled, terminated, timed out,
failed), or one Temporal no longer knows (retention), is over: the study archives.

Why refuse rather than cancel on the owner's behalf: every transition activity refuses an
archived study, so an execution left running would fail at its next step, and one left waiting
at a gate would wait for a decision the API refuses; and a cancel is an explicit, recorded act
(`study.cancel_requested` with its reason) that a soft delete should not perform silently.

Two edges, both tested on the real workflow and on the routes:

| Case | Answer |
|---|---|
| Temporal not configured or unreachable, and the study has a workflow | `503` (as on the workflow routes); a draft still archives, nothing can be running for it |
| the study is started while the archive is being decided (an engineer's `POST .../start` lands between the route's read and its write) | the archive's compare-and-set (`workflow_id` must still be what the route read) finds the study started and answers `409 workflow-running`; the study stays `processing` with its execution |
| a decision for an archived study (the execution was cancelled, then the study archived) | `409 stale-state` from the API; the closed execution refuses the update too. Unarchived, the study reads as it was (for example `waiting_review` at the gate) with a closed execution (`runs[].outcome` `cancelled`; the cancel signal ends the execution `COMPLETED` with that outcome, a Temporal-level cancel `CANCELED`): `POST .../restart` runs it again (docs/api/WORKFLOW.md) |

## 5. Moving the web app's library to the server

What the web app does in the browser today (`web/live/live-app.js`: `FILTERS`,
`L.studiesQuery`, `L.studiesSort`) and the request that does the same on the server:

| Web app state | Request |
|---|---|
| `L.studiesQuery` (the search box, `property_name.toLowerCase().includes(q)`) | `q=<text>`: the server finds everything the browser found and more (the address, client, parcel, ZIP, creator, signer; accents ignored; words in any order, section 1.1) |
| `L.studiesFilter` (a `FILTERS` key) | the `status` values of section 3 |
| `L.studiesSort` `updated` ("Last updated") | `sort=updated_desc` (the default) |
| `L.studiesSort` `oldest` ("Oldest first") | not a server sort: the app sorts by `updated_at` ascending. Fetch with `sort=updated_desc` and reverse the page, or add `updated_asc` when it is wanted |
| `L.studiesSort` `name` ("Property A–Z", `localeCompare`) | `sort=name_asc` (lower-cased comparison; `localeCompare` and `lower()` agree on ASCII names and may differ on accented ones) |
| the chip counts (`matched.filter(FILTERS[f]).length`) | `total` of one request per chip, or count the unfiltered listing as today |
| `listAll('/v1/studies', {}, {limit: 100})` (every page, then filter in the browser) | the same loop with the filters in the query; `total` says how many pages to expect |

Nothing in the web app has to change for this release: with no parameters the route answers
the same items as before plus `total`, in `updated_desc` order instead of `created_desc`,
which the app re-sorts anyway.
