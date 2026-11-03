# Browser error capture: `POST /v1/client-errors`, `GET /v1/ops/client-errors`

Every error a user hits in the browser is recorded with every detail the browser knew,
so the bug can be diagnosed later from the record alone: who, in which firm, on which
study and screen, what they did last, what failed and where, on which build, in which
browser. The web app's capture module (`web/live/errors.js`) sends the events; the API
stores them, links them to the study's timeline, and serves them to platform operators;
the developer page (`web/developer/client-errors.html`) lists and shows them.

Design record and research: EVAL_REPORT section 65.

Contents: 1 what is captured · 2 the event · 3 `POST /v1/client-errors` · 4 the record,
the timeline, the logs · 5 the operator reads, the issues and their status, the copy brief ·
6 retention · 7 what cannot be captured.

## 1. What is captured, and what never

The module is loaded first on the page, installs its hooks and reads everything else
lazily. The kinds:

| `kind` | Hook | Level |
|---|---|---|
| `error` | `window` `error` event: an uncaught exception (`error.name`, message, stack, the file, line and column the browser reported) | error |
| `unhandledrejection` | a promise nobody caught; a non-Error reason is serialized | error |
| `api` | a `/v1` request the API client made that answered 4xx/5xx or got no response (`observe` hook of `createClient`); 304 and aborted requests are not errors | warning for 4xx, error for 5xx and no response |
| `console` | `console.error(...)` (the arguments joined; an Error argument gives the type and stack) | error |
| `resource` | a script, stylesheet or image that failed to load (`error` event on the element) | error |
| `report` | `Basis.reportError(err, context)` from a screen: the live app calls it for a failed action (`guarded`) and a failed loader that was not an API refusal | error (`context.level: "warning"` lowers it) |

Every event carries the breadcrumbs: the last 50 of the user's actions, oldest first:
`click` (a `data-live` or `data-action` element: the action name, its value with ids
redacted, the visible text), `navigation` (the hash route before and after), `api` (every
call: method, route, status, duration, request id, code, attempt), `decision` (a gate
decision: stage, decision, revision, the number of corrections), `console`, `custom`.

Never recorded, by construction: a bearer token or `Authorization` header (the module
never sees the headers; free text is scrubbed of `Bearer ...` and JWT-shaped strings); a
request body (field names only); a query string's values (keys only, `?limit=…`); an id in
a path or route (`{id}`); a key whose name looks like a secret (`key`, `token`, `secret`,
`password`, `authorization`, `cookie`, `session`: masked `***` in the context and the
breadcrumb data, and again by the server over the whole event); document content, a
photo, a price (none of those pass through the hooks).

Bounds on the client: 50 breadcrumbs, a stack of 16,000 characters, a message of 4,000,
short strings 500, a context of 16,000 characters serialized (over that: its keys), one
event per identical error within 5 s (`repeats` counts the others), 30 events a minute
(`dropped` on the next event counts the rest), a queue of 50 unsent events, 20 events a
batch, one batch every 5 s. The module's own route and its own `console` use produce no
event, so it cannot report on itself.

## 2. The event

The shape follows Sentry's event payload (`event_id`, `timestamp`, `level`, the exception's
type, value and stack, `breadcrumbs`, `user`, `release`, the request) with OpenTelemetry's
names where they fit (`exception.type`/`message`/`stacktrace`, `browser.language`,
`browser.platform`, `user_agent.original`, `http.request.method`,
`http.response.status_code`) and the Basis ids on top. One event:

```json
{
 "event_id": "9a1e5e7e-1cbe-4b1e-9d7f-0d0e2c5f7c41",
 "occurred_at": "2026-11-01T18:02:11.482-05:00",
 "since_load_ms": 48211,
 "kind": "api", "level": "warning",
 "message": "POST /v1/studies/{id}/decisions failed with 409 (BASIS-STUDY-003): revision 1 is stale",
 "error_type": "ApiError", "stack": null,
 "source": null,
 "page": {"hash": "#rooms/{id}", "screen": "rooms", "path": "/"},
 "user": {"uid": "firebase-uid", "email": "eng@firm.test", "role": "engineer"},
 "firm": {"org_id": "0b8f0f5e-…", "org_name": "Acme Engineering"},
 "study": {"study_id": "3f2c1a2b-…", "revision": 2, "stage": "rooms_review", "status": "waiting_review"},
 "http": {"method": "POST", "route": "/v1/studies/{id}/decisions", "status": 409, "code": "BASIS-STUDY-003",
          "request_id": "0193a1b2-…", "problem_type": "https://basis.invalid/problems/stale-state",
          "problem_title": "Conflict", "problem_detail": "revision 1 is stale", "latency_ms": 12,
          "attempt": 1, "retry": false, "body_fields": ["stage", "revision", "decision", "comment"]},
 "breadcrumbs": [
  {"at": "2026-11-01T18:01:40.000-05:00", "since_load_ms": 16700, "kind": "navigation", "message": "#dashboard → #rooms/{id}", "data": null},
  {"at": "2026-11-01T18:02:09.100-05:00", "since_load_ms": 45800, "kind": "click", "message": "approve rooms_review", "data": {"tag": "button", "action": "approve", "value": "rooms_review", "text": "Approve rooms review", "screen": "rooms"}},
  {"at": "2026-11-01T18:02:11.470-05:00", "since_load_ms": 48199, "kind": "decision", "message": "approve rooms_review", "data": {"stage": "rooms_review", "decision": "approve", "revision": 2, "corrections": 0}},
  {"at": "2026-11-01T18:02:11.482-05:00", "since_load_ms": 48211, "kind": "api", "message": "POST /v1/studies/{id}/decisions → 409", "data": {"status": 409, "request_id": "0193a1b2-…", "code": "BASIS-STUDY-003", "latency_ms": 12, "attempt": 1}}
 ],
 "build": {"api_git_sha": "15cd291", "web_version": "live-16"},
 "client": {"user_agent": "Mozilla/5.0 …", "platform": "macOS", "language": "en-US", "languages": ["en-US", "es"],
            "viewport": {"width": 1440, "height": 812}, "device_pixel_ratio": 2, "online": true,
            "timezone": "America/New_York", "connection": "4g", "visibility": "visible", "cookies_enabled": true},
 "context": null,
 "repeats": 1, "dropped": 0,
 "capture_version": "client-errors-1"
}
```

| Member | Meaning |
|---|---|
| `event_id` | Minted by the browser when the error happened (UUID); the server stores each once. |
| `occurred_at`, `since_load_ms` | The browser's clock with its UTC offset (required), and `performance.now()`. |
| `kind`, `level` | Section 1. |
| `message`, `error_type`, `stack`, `source` | The exception's value, `name`, stack and the first source location (file with its query string redacted, line, column, function). |
| `page` | The hash route with ids redacted, the screen the app rendered, the document path. |
| `user`, `firm`, `study` | What the app knew; the server verifies the firm and the study (section 4). |
| `http` | The failed request (kind `api`), or null. `route` has ids redacted, `body_fields` the request body's field names, `attempt` how many times the idempotency key was sent. |
| `breadcrumbs` | Up to 50, oldest first, each with the client's clock, the time since load, the kind, a message and bounded data. |
| `build` | `api_git_sha`: what `GET /health` reported (read once per page; null if an event left first); `web_version`: the module's `?v=` asset version. |
| `client` | Browser and device: user agent, platform (`userAgentData.platform` or `navigator.platform`), language(s), viewport, pixel ratio, online, time zone, connection type, visibility, cookies. |
| `context` | What `Basis.reportError(err, context)` was given, sanitized. |
| `repeats`, `dropped` | Identical errors this event stands for; events dropped under the per-minute cap before it. |

Every string is bounded and free of NUL; every list is bounded; `context` and a
breadcrumb's `data` are bounded by their serialized size (16,000 and 2,000 characters).
An event is at most about 40 KB.

## 3. `POST /v1/client-errors`

```
POST /v1/client-errors
Authorization: Bearer <ID token>      Idempotency-Key: <one per batch>
{"events": [ ... 1 to 20 events ... ], "sent_at": "2026-11-01T18:02:16.000-05:00"}

202 {"accepted": 3, "stored": 3, "duplicates": 0, "ids": ["<record id>", ...]}
```

* Any signed-in user, any firm role, a user with no firm too (the firm column stays
  null). The reporter is the token's user, whatever `user` claims.
* An ordinary write route: the mutation rate limit, `Idempotency-Key` per batch (a retry
  with the same key replays the answer). Two limits of its own: the batch is at most
  **256 KiB** (413 `payload-too-large`, `BASIS-API-045`, checked before the body is
  parsed) and at most **30 batches a minute per user** (429 with `Retry-After`).
* `sent_at`, optional, is the browser's clock when the batch left; the server derives
  `clock_offset_ms` from it (section 4).
* A malformed event is 422 with `errors` naming the field; the whole batch is refused
  (the module sends only what it built, so a 422 means a bug in the module).
* `202`: the events were stored (`stored`) or were stored before (`duplicates`: the same
  `event_id`); `ids` are the records stored now, in batch order. The client never reads
  them back.

The module sends with `fetch(..., {keepalive: true})` so a batch survives the page
unloading (`navigator.sendBeacon` cannot carry the bearer token), retries 429, 5xx and a
lost connection with backoff (2 s, 8 s, 32 s) under the same key, drops a batch on any
other 4xx, keeps events queued while nobody is signed in and sends them after sign-in,
and flushes at once when the page hides.

## 4. The record, the timeline, the logs

**`client_errors`** (migration `0028`): one row per event. Columns for what an operator
filters on: `occurred_at` (the client's clock), `created_at` (`received_at` in the API: the
database clock), `user_id` (the reporter), `org_id`, `study_id`, `revision`, `stage`,
`route`, `screen`, `kind`, `error_type`, `message`, `code`, `http_status`, `request_id`,
`build_sha`, `web_version`, `user_agent`, `fingerprint` (the issue, section 5.2); `event` is
the whole event as JSONB, after the server masked any key that looks like a secret, plus
`_server`:

```json
"_server": {"received_at": "…", "sent_at": "…", "clock_offset_ms": -1480,
            "api_request_id": "<X-Request-Id of the report>", "api_git_sha": "15cd291", "api_env": "staging",
            "reporter": {"user_id": "…", "uid": "…"}, "org_verified": true, "study_visible": true,
            "remote_addr": "203.0.113.10"}
```

`clock_offset_ms` is the browser's clock minus the server's at send time: add it to
`occurred_at` to place the event on the server's timeline. Rows are never updated
(`forbid_mutation()` on UPDATE); the retention purge deletes them (section 6).

**Verified, not trusted.** `org_id` is set to the claimed firm only when the reporter is a
member of it, else to their one firm when they belong to exactly one, else null.
`study_id` is set only when the reporter can see the study (a member of its firm), else
null. The claims stay inside `event` either way, with `_server.org_verified` and
`study_visible` saying what was checked, so a browser cannot write into another firm's
study and an operator still sees what the client thought.

**The timeline.** An event on a visible study appends `client.error` to the study's
timeline in the same transaction (docs/api/TIMELINE_AND_AUDIT.md): actor the engineer,
`request_id` the report's own, `code` the failed request's catalogue code, `entity_type`
`client_error`, `entity_id` the record, `stage`/`revision` the study row's at the time,
`detail` the kind, level, type, message (first 500 characters), the screen and route, the
client's revision and stage, the failed request's method, route, status, code and request
id, the build and the client's event id. It shows in `GET .../timeline`
(`event_type=client.error`) and under the user in `GET /v1/me/activity`; it is not an
engineer action, so `GET .../audit` does not list it.

**The logs.** The API logs `client error recorded` with `error_code` `BASIS-CLIENT-001`,
`client_error_id`, `kind`, `client_code`, `client_request_id`, `client_http_status`,
`org_id`, `study_id`, `screen`, `build_sha`. The failed request's own server record is
found by its request id: every problem response's log line (`problem`) and every unhandled
exception's (`unhandled exception`, with `stack_trace`) carries `request_id`, the caller's
`uid` and `user_id`, the `study_id`, `org_id` and `role` once the route resolved them, the
method, path and `route` template, the `status`, the catalogue `error_code` and the
exception's type and message; the worker's `activity failed` line carries the study, the
execution, the attempt, the code and the traceback. So `event.http.request_id` finds one
server line with everything about the request that failed.

### 4.1 Missing inputs: kind `study_input` (EVAL_REPORT 78)

Not a fault in the software, but where a firm new to the product gets stuck: a study whose
dollars are held by a missing input (`GET .../result` `blockers`: the appraisal lacks a field,
a room has no measured area, an item has no count, the cost table has no row, a row is
unclassified, the location or the cost year's index is missing). The assets step writes one
record per blocker per revision into this store, server-side (`basis.evidence.input_records`;
the browser's `POST` does not accept the kind):

| column | value |
|---|---|
| `kind`, `code`, `error_type` | `study_input`, `BASIS-STUDY-037`, the blocker's code (`input:gla`, `unpriced`, ...) |
| `org_id`, `study_id`, `revision`, `stage` | the firm, the study, the revision, the study's stage when recorded |
| `user_id` | the person whose decision started the run (the study's latest review), else the study's creator |
| `message` | `<study name>: <what is missing>` |
| `event` | `blocker` (code, message, assets, fix), `fix_stage`, `asset_ids` (up to 200) and `assets_held`, every blocker of the revision and the result's `missing`, the `study` (name, status, stage, creator), the `firm` (id, name), the `person` (id, email, name, what they decided), the `intake`'s location and dates (no money), and every `documents` entry (id, kind, filename, content type, bytes, sha256, uploader, upload time, storage URI) |

`event_id` is a UUID5 of the study, revision and blocker, so a retried activity records nothing
twice. The records group into one issue per firm and blocker (not per message), so an operator
sees "this firm keeps missing X" and marks it `in_progress` or `resolved` with a note as for any
issue. Recording runs in a savepoint and never fails the study. The developer page's filter
lists the kind. The record's detail on the developer page lists the files with an Open button
(`GET /v1/ops/studies/{id}/documents/{id}/content` with a reason; every open is recorded on the
study's timeline, which the firm sees: docs/api/OPS_DOCUMENTS.md).

## 5. The operator reads

```
GET /v1/ops/client-errors?limit=50&cursor=<next_cursor>&org_id=&user_id=&study_id=&code=BASIS-STUDY-003
                          &kind=api&request_id=&since=2026-11-01T00:00:00Z&until=…
GET /v1/ops/client-errors/{client_error_id}
```

* Platform operators only: the token's uid in `OPS_OPERATOR_UIDS` (docs/api/OPS_HEALTH.md
  section 1); a firm owner is 403 `not-operator`. The usual read rate limit.
* The list is newest first (`seq` descending), keyset-paginated (`next_cursor`, null on
  the last page), `limit` 1 to 200 (default 50). Filters combine with AND: `org_id`,
  `user_id`, `study_id` (UUIDs), `code` (`BASIS-AREA-nnn`), `kind`, `request_id` (the
  failed request's), `since`/`until` on the time received (RFC 3339; a naive time is UTC).
  Each item is the record's columns (`received_at` is `created_at`); the detail adds
  `event`.
* 404 for an unknown id; 422 for a bad parameter or cursor.

Each record also carries `fingerprint` and `issue_status`; `status=` and `fingerprint=`
filter the records by their issue.

### 5.2 Issues: the same error seen again, and its status

Records are grouped the way Sentry groups events: by a **fingerprint**, sixteen hex
characters of a hash over the kind, the error type, the message with numbers and ids
normalized (`Cannot read 'rooms' of {id} at row #`), the top frame's file (without its
query string) and function, and the route and status of a failed call. Two users on two
firms hitting the same bug make one issue; a different message or a different frame makes
another. `client_error_issues` has one row per fingerprint:

```
GET /v1/ops/client-errors/issues?status=open&kind=error&limit=50&cursor=<next_cursor>
GET /v1/ops/client-errors/issues/{fingerprint}
PUT /v1/ops/client-errors/issues/{fingerprint}/status   {"status": "resolved", "note": "fixed in #123"}
```

```json
{"fingerprint": "d5076d7e3c7774ca", "kind": "error", "error_type": "TypeError",
 "title": "Cannot read properties of undefined (reading 'rooms')", "route": null, "code": null, "http_status": null,
 "status": "regressed", "note": "fixed in #123", "status_set_by": "de30e188-…", "status_set_at": "2026-11-01T19:00:00Z",
 "status_build_sha": "15cd291", "occurrences": 7, "first_seen_at": "…", "last_seen_at": "…", "last_build_sha": "a1b2c3d"}
```

* **What the ingest keeps**: `occurrences`, `first_seen_at`, `last_seen_at` (the client's
  clock), `last_build_sha` (the newest API build the issue was seen on), the `title` (the
  first occurrence's message) and the route, code and status of a failed call. A duplicate
  `event_id` counts nothing.
* **What a person sets** (operators only, last write wins): `status` `open` (the state on
  first sight), `in_progress`, `resolved` or `ignored`, and a `note` (500 characters). The
  write records who set it, when, and `status_build_sha`: the build the issue had last
  been seen on at that moment.
* **The one automatic transition**: a `resolved` issue seen again on a build **other than**
  `status_build_sha` becomes `regressed` (the fix was deployed and the bug is back); seen
  again on the same build it stays `resolved` (the fix is not deployed yet) and the count
  moves. `regressed` cannot be set by hand; set `in_progress` or `resolved` again to take it
  back. Nothing detects that a bug is fixed: that is the person's call, as in every tracker;
  what the system can tell is that it came back, and it does.
* The list is most recently seen first, keyset-paginated (`next_cursor`; a malformed cursor
  is 422 `invalid-cursor`); `status` and `kind` filter.

### 5.3 The developer page and the copy brief

The developer page `web/developer/client-errors.html` (served at `/developer/`, beside
the system-health page) has the issues table (counts, first and last seen, last build, the
status control with its note), the records list with the filters (an issue's row narrows the
records to its fingerprint) and a detail view: the facts, the stack, the context, the last
actions and the whole event. It signs in with the app's Firebase session and reads through
the app's API client.

**Copy for an agent** puts the record into one plain-text brief on the clipboard, ready to
paste into Claude Code or Codex: the message, kind and type; the issue with its status,
count and note; when (both clocks and the offset, the time since load); who (email, uid,
role, user id); the firm; the study with revision and stage; the screen and route; the
failed request with its code, request id, latency and attempt; the source location; the
build and browser; the stack; the context; the last actions, oldest first, with their times;
and where to look next (the server log line by request id, the record's URL, the study's
timeline, this document). **Copy JSON** copies the record and its issue as JSON.

## 6. Retention

A fixed period, not the firm's rule: `CLIENT_ERRORS_RETENTION_DAYS` (default 180, 1 to
3650), applied by

```bash
python -m basis.retention purge-client-errors --dry-run     # counts; changes nothing
python -m basis.retention purge-client-errors               # deletes, in batches of 1000
python -m basis.retention purge-client-errors --days 30     # a one-off shorter window
```

Why: the records are operational data about the application (what a browser did when
something broke), not client files; they name a user, a firm and a study id, never a
document, a photo or a price. The firm's rule (`orgs.retention_days`,
docs/api/RETENTION_AND_ALERTS.md) governs the client's uploads and purges them per study;
a bug a firm reports a quarter late still needs its record, so 180 days (Sentry keeps 90).
The `client.error` timeline rows stay: the timeline is append-only and the event's summary
there is the study's own record. Run it on the same schedule as the retention purge
(infra/staging/RUNBOOK.md section 3.1). Codes: `BASIS-CLIENT-001` recorded,
`BASIS-CLIENT-002` purged, `BASIS-API-045` batch too large (docs/ERROR_CODES.md).

## 7. What cannot be captured

* **Cross-origin script errors** arrive as `Script error.` with no stack (the browser's
  policy for scripts without CORS headers): the Firebase SDK from `gstatic.com` is one.
  The event still carries the page, the user and the breadcrumbs.
* **The browser's own failures**: a tab crash, a frozen page, a navigation away during a
  request. The last batch may be lost when the page unloads before `keepalive` sends it
  (the browser caps keepalive bodies at 64 KB per origin in flight).
* **Errors before the module loads** (a syntax error in `errors.js` itself, a page that
  never reaches its first script) and errors while not signed in that are never followed
  by a sign-in on that page (they wait in memory; a reload loses them).
* **The user's intent**: breadcrumbs say what was clicked and called, not why; the text
  typed into a form is never recorded.
* **Minified sources**: the app ships unminified files with `?v=` versions, so stacks name
  real lines; if a build step is added, source maps become necessary.
* **The server's view** is not copied into the record; it is found by the request id in
  the API and worker logs (section 4), which have their own retention.
* **Events beyond the caps** are counted (`dropped`, `repeats`), not kept.
