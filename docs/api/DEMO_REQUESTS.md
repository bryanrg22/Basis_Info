# Demo requests: `POST /v1/demo-requests`, `GET /v1/ops/demo-requests`

The public site's "Book a demo" form (`web/site/directions/demo.html`, script
`directions/b/entry.js`) posts a visitor's name, work email, company, role and an optional
note here. The API stores the request, emails it once to the founder
(`DEMO_REQUEST_NOTIFY_EMAIL`) through the outbox and the relay, and lists every request to
platform operators. It is the API's one write without a token.

Design record: EVAL_REPORT section 95.

Contents: 1 the request · 2 abuse limits · 3 CORS · 4 the email · 5 the operator read ·
6 retention · 7 settings and running it locally · 8 before it is deployed.

## 1. `POST /v1/demo-requests`

```
POST /v1/demo-requests
Content-Type: application/json            (no Authorization, no Idempotency-Key)
{"name": "Dana Whitfield", "email": "dana@acme-eng.com", "company": "Acme Engineering",
 "role": "engineer", "note": "optional free text", "page": "https://.../demo.html", "website": ""}

202 {"id": "<uuid>"}
```

| member | required | rule (checked as sent, then trimmed) |
|---|---|---|
| `name` | yes | one line (no control characters), a visible character, at most 200 |
| `email` | yes | an address (`local@domain.tld`, visible ASCII, one `@`), at most 254 (RFC 5321's path limit; the 320 often quoted is 64 + 1 + 255, which no path can carry); stored lower-cased |
| `company` | yes | as `name` |
| `role` | yes | `owner_partner`, `engineer`, `analyst_staff` or `other` |
| `note` | no | at most 4000; line breaks and tabs allowed, no other control character; blank is absent |
| `page` | no | one line, at most 2000 (the page sends `location.href`); blank is absent |
| `website` | no | the spam trap (section 2); at most 2000 |

* **202, not 201.** The visitor cannot read the request back (no public read, no
  `Location`), the email is sent later by the relay, and the trap and a duplicate must look
  exactly like a new request. The body is the request's id and nothing else; it says nothing
  about any other request. (`POST /v1/client-errors` answers 202 for the same reasons.)
* **422** problem details (`BASIS-API-013`) with `errors` naming each field
  (`["body", "email"]`), for a missing or blank required member, a length over its bound,
  an email that is not one, a control character where one line is expected, an unknown
  role, any member not listed above (`extra_forbidden`), a body that is not a JSON object
  (a form-encoded or `text/plain` body included). Nothing is stored.
* **400** (`BASIS-API-025`) for bytes that are not UTF-8 JSON (the parser refuses them before
  validation).
* **429** past the rate limit (section 2), with `Retry-After`; **413** over 1 MiB; **503**
  while the database is unavailable. Every error is `application/problem+json`.
* What the server keeps beside what was typed: the time (the database clock), the client
  address (`request.client.host`, the address the rate limit keys on), the `User-Agent`
  (first 500 characters) and the API request id (`X-Request-Id`).

## 2. Abuse limits

* **Rate limit per client address**: `DEMO_REQUESTS_PER_HOUR` (default 10) through the
  API's rate limiter (the Postgres store in staging and prod, so one budget across
  instances; key `demo-requests:ip:<address>`). It runs before the body is read, so
  malformed, trapped and duplicate requests count too. A person sends one or two; an office
  behind one address, a few.
* **The spam trap**: the page has a `website` field people do not see (a `trap` box,
  `aria-hidden`, `tabindex=-1`, `autocomplete=off`) that it always sends empty. Filled in
  (anything but whitespace), the request is answered 202 with a fresh id, exactly as a stored
  one, and nothing is stored, emailed or listed; the log line `demo request trapped`
  (`BASIS-DEMO-003`) carries the client address and nothing the sender typed. A browser that
  autofills the hidden field would lose a real request; the line is how an operator would
  see it.
* **The duplicate window**: the same email (compared lower-cased) again within ten minutes
  is answered with the first request's id; nothing new is stored and no second email is
  sent (`BASIS-DEMO-002`). The first request's text is kept. Two identical requests at once
  (a double click, a retry racing the first) are serialized by a transaction-scoped advisory
  lock on the email, so the second sees the first (`DemoRequestRepository.record`; the race
  is made real in `tests/api/test_demo_requests.py`). After the window the same email is a
  new request and a new email: someone writing again later may have something new to say.
* **Size**: the API's 1 MiB body limit; the lengths above.

What it does not have, and why: no CAPTCHA (a third-party script on the public page, and the
volume does not call for one yet); no email verification (the founder replies to the
address, which is the verification); no `Idempotency-Key` (keys are scoped per user, and a
visitor is none; the duplicate window stands in).

## 3. CORS

The page is served from the site's origin, not the API's, and posts JSON, so the browser
sends a preflight. This route has its own CORS policy (`basis.api.cors`): the site's origins
(`DEMO_REQUEST_ALLOWED_ORIGINS`) and the app's (`CORS_ALLOWED_ORIGINS`), method `POST`,
request headers `Content-Type` and `X-Request-Id`, exposed `X-Request-Id` and `Retry-After`,
no credentials, preflight cached 600 s. The site's origins reach no other route. An origin
that is not listed gets a 400 preflight and no `Access-Control-Allow-Origin`.

Unset, `DEMO_REQUEST_ALLOWED_ORIGINS` is the local static server in dev and test
(`http://127.0.0.1:8752`, `http://localhost:8752`) and nothing in staging and prod, where it
must name the site's https origin. The `CORS_ALLOWED_ORIGINS` rules apply: an origin with no
path, `*` and plain http (other than localhost) refused outside dev and test.

CORS is the browser's rule, not access control: a non-browser client can post from anywhere.
The rate limit and the trap are what limit abuse.

## 4. The email

A new request enqueues one `notification` outbox row, `{"type": "demo_request",
"demo_request_id": "<id>"}`, in the transaction that stores it (the outbox pattern of the
invitation emails). The relay loads the request and sends one plain-text email through the
configured sender (`EMAIL_PROVIDER`: `log` writes it to the relay's log, `smtp` sends it):

```
To: <DEMO_REQUEST_NOTIFY_EMAIL>
Subject: Demo request: Dana Whitfield, Acme Engineering

Someone asked for a demo on the Basis site.

Name:     Dana Whitfield
Email:    dana@acme-eng.com
Company:  Acme Engineering
Role:     Engineer
Received: 2026-11-12 18:04 UTC
Page:     https://.../demo.html

Note:
optional free text

Reply to dana@acme-eng.com to set up the meeting.
Request <id>; every request: GET /v1/ops/demo-requests.
```

The key `demo-request-<id>` (the SMTP `Message-ID`) names the one send; the request's
`notified_at` is set when the sender accepted it, so a redelivered outbox row sends nothing.
The visitor's request never waits on mail: a slow or failing server delays the email, not
the answer.

| relay outcome | outbox row | the request |
|---|---|---|
| sent | `delivered` | `notified_at` set, `notify_error` cleared |
| sender failed (SMTP down, 4xx) | retried with backoff, `dead` after 50 attempts (`BASIS-DEMO-005`) | `notify_error` |
| recipient refused (5xx) | `dead` at once (`BASIS-DEMO-005`) | `notify_error` |
| no `DEMO_REQUEST_NOTIFY_EMAIL` | `delivered`, WARNING with the id (`BASIS-DEMO-004`) | `notify_error` |
| already sent, or purged first | `delivered`, nothing sent | unchanged |

The outbox payload carries the id only, so the retention purge leaves nothing personal in
`outbox`. The API's log lines carry the request id and role, never the name, email, company
or note. With `EMAIL_PROVIDER=log` the relay's log has the rendered email (that is the log
sender's purpose); a deployment with a mail provider uses `smtp`.

## 5. `GET /v1/ops/demo-requests`

Platform operators only (`OPS_OPERATOR_UIDS`; a firm role never grants it: 403
`not-operator`, `BASIS-API-043`; no token 401). Newest first, `limit` 1 to 200 (default
50), `cursor` from `next_cursor` (keyset on `seq`; a malformed cursor is 422).

```json
{"items": [{"id": "...", "seq": 12, "received_at": "2026-11-12T18:04:05Z",
            "name": "Dana Whitfield", "email": "dana@acme-eng.com",
            "company": "Acme Engineering", "role": "engineer", "note": "...", "page": "...",
            "remote_addr": "203.0.113.7", "user_agent": "...", "request_id": "...",
            "notification": "sent", "notified_at": "...", "notify_error": null}],
 "next_cursor": "11"}
```

`notification` is `queued` (the relay has not handled it), `sent`, or `failed` (the last
attempt did not send it; `notify_error` says why). The developer page does not show these
yet (`web/FRONTEND_BACKLOG.md`).

## 6. Retention

A request is a visitor's contact details, given to be contacted about a demo, so it is
kept while that purpose lasts: `DEMO_REQUESTS_RETENTION_DAYS` (default 365, 1 to 3650),
applied by

```bash
python -m basis.retention purge-demo-requests --dry-run     # counts; changes nothing
python -m basis.retention purge-demo-requests               # deletes, in batches of 1000
python -m basis.retention purge-demo-requests --days 90     # a one-off shorter window
```

Why 365: a professional-services sales cycle runs months, and a firm that writes again a
season later should be recognised; past a year, an unanswered lead's details have no
purpose here (the storage-limitation principle, GDPR Article 5(1)(e); the CCPA asks a
business to state each category's retention, which this setting is). A firm that became a
customer is in the firm records, not here. The firm retention rule (`orgs.retention_days`)
does not apply: a visitor belongs to no firm. Run it on the retention purge's schedule
(infra/staging/RUNBOOK.md section 3.1). Code `BASIS-DEMO-006`.

## 7. Settings and running it locally

| setting | default | |
|---|---|---|
| `DEMO_REQUEST_NOTIFY_EMAIL` | unset | the one address each request is emailed to (relay); unset, nobody is emailed |
| `DEMO_REQUEST_ALLOWED_ORIGINS` | dev/test: `http://127.0.0.1:8752,http://localhost:8752`; staging/prod: none | the site's origins (section 3); empty is unset |
| `DEMO_REQUESTS_PER_HOUR` | 10 | per client address (section 2) |
| `DEMO_REQUESTS_RETENTION_DAYS` | 365 | section 6 |

Locally: `docker compose up --build`, `docker compose exec api alembic upgrade head`, serve
the site with `python3 -m http.server 8752 --bind 127.0.0.1` from `web/site`, and open
`http://127.0.0.1:8752/directions/demo.html` (its `basis-api` meta is
`http://127.0.0.1:8000`). With `EMAIL_PROVIDER=log` (the default) the email appears in
`docker compose logs relay` once `DEMO_REQUEST_NOTIFY_EMAIL` is set in `backEnd/.env`; the
request is in `GET /v1/ops/demo-requests` either way. Under Docker Desktop the API sees
the VM's gateway (`192.168.65.1` on the maintainer's Mac) as every browser's address, so
locally the rate limit is one budget.

## 8. Before it is deployed

* `DEMO_REQUEST_ALLOWED_ORIGINS` = the site's https origin, and the page's `basis-api` meta
  = the API's URL.
* `DEMO_REQUEST_NOTIFY_EMAIL` and `EMAIL_PROVIDER=smtp` with its `SMTP_*` and `EMAIL_FROM` on
  the relay.
* The client address: behind Cloud Run's front end `request.client.host` is the proxy unless
  uvicorn runs with `--proxy-headers --forwarded-allow-ips` (the Dockerfile's command does
  not today; `basis.api.ratelimit`). Until it does, every visitor shares one budget of
  `DEMO_REQUESTS_PER_HOUR`: raise it, or add the flags. This applies to every
  unauthenticated limit, not only this one.
* Schedule `purge-demo-requests` with the retention purge.
