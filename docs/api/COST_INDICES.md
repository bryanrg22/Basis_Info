# The cost index store: `/v1/cost-indices`

Every priced asset's dollars are `RSMeans 2020 unit cost x quantity x year factor x location
factor`, and the two factors are published RSMeans indices (docs/review_2026-10-15/COST_INDICES.md).
The store is where every index value the product has obtained lives, with its citation, who
obtained it and when (sections 9-12 of that document). The routes read it and let a firm owner
enter a value by hand; the refresh job (`python -m basis.costs.refresh`) fills it from the
sources. Nothing in a request fetches anything.

## `GET /v1/cost-indices`

Any member of a firm. The current version of every value, newest first; `all_versions=true`
lists the superseded rows too. Filters: `series` (`historical` | `city`), `year`, `source`
(`rsmeans_2020_book`, `rsmeans_public`, `manual`, `rsmeans_api`). Keyset pagination: `limit`
(default 200, at most 1000) and `cursor` (the previous page's `next_cursor`; a malformed one is
422 `invalid-cursor`).

```json
{
  "items": [
    {
      "id": "0f8c…",
      "source": "rsmeans_public",
      "series": "historical",
      "year": 2024,
      "region": "US",
      "value": "294.8",
      "as_of": "2024-07-01",
      "citation": "RSMeans data from Gordian, 2024 cost index page: \"The actual July 2024 Historical Index = 294.8\" (Jan. 1, 1993 = 100 (30-city national average)), https://www.rsmeans.com/landing-pages/2024-rsmeans-cost-index",
      "url": "https://www.rsmeans.com/landing-pages/2024-rsmeans-cost-index",
      "derived": null,
      "version": 1,
      "obtained_at": "2026-10-30T18:02:11.412Z",
      "obtained_by": null,
      "obtained_by_name": "rsmeans_public",
      "document_sha256": "fe9223a7…",
      "run_id": "7c1d…",
      "detail": {"month": "July", "quarters": {"January": "295.6", "April": "295.5", "July": "294.8", "October": "296.3"}, "wording": "The actual July 2024 Historical Index = 294.8", "base": "Jan. 1, 1993 = 100 (30-city national average)", "provisional": false, "rule": "…"}
    }
  ],
  "next_cursor": null
}
```

* `series` `historical`: the RSMeans Historical Cost Index (Jan. 1, 1993 = 100), one national
  value per year (`region` `US`); the year factor of a study is this value over 239.1 (the 2020
  books' Jan. 1, 2020 cost basis). `city`: a location factor (the City Cost Index's weighted
  average over 100) of one city row (`region` `<state>:<ZIP prefixes>` as the book prints them,
  the city's name in `detail.city`) or a state.
* `version`: 1 for the first value of a key (`source`, `series`, `year`, `region`); a revised
  value is the next version and the old row stays (the table is append-only). The pipeline
  uses the current version of the best-ranked source: a manual entry first, then the sources
  in `COST_INDEX_SOURCES` order, then the released book; a `derived` value never outranks a
  published one.
* `obtained_by` is the user of a manual entry (`obtained_by_name` their email or name);
  for a fetched value `obtained_by_name` is the fetcher's name. `document_sha256` is the hash
  of the fetched document (the released file, the web page).

## `POST /v1/cost-indices`

A firm owner (the admin role) of any firm: the store is shared by every firm on the
deployment and a value is a published fact, cited. Idempotent (`Idempotency-Key`). Appends the
value as source `manual` with the entering user and the time, and a ledger row (`run`) that
says the same; 201 with both.

```json
{
  "series": "historical",
  "year": 2024,
  "region": "US",
  "value": "294.8",
  "as_of": "2024-07-01",
  "citation": "RSMeans Building Construction Costs 2025, Historical Cost Indexes, p.<n> (July 2024)",
  "url": null,
  "derived": null,
  "detail": null
}
```

* `value`: the index as printed, a decimal string (up to ten digits and four decimals, above
  zero). `as_of`: the date the value is for, in the value's year (`2024-07-01` for a July
  figure; the books' Historical Cost Index table lists past years at July 1). `citation`:
  publication, table and page, or the URL and the line as printed (what the IRS guide asks of a
  cost source, ATG ch. 3 C.2(2)).
* A `historical` value is national (`region` must be `US`); a `city` value needs a region, and
  a city row (`CA:900-902`) needs `detail.city`.
* `derived`: only for a value you converted from another series (the conversion and its
  error band, COST_INDICES.md section 11); every row priced with it is flagged
  `cost:YEAR_INDEX_CONVERTED` until a published value for the year outranks it.
* The same value and citation as the current version of that key is 409 `duplicate-value`
  (`BASIS-COST-003`) and nothing is written, not even a ledger row. A different value or
  citation is the next version (201).
* 403 for an engineer or viewer (`BASIS-API-010`), 403 `no-firm` for a user in no firm, 422
  with per-field `errors` for a body that breaks a rule above.

Response:

```json
{
  "value": {"…": "the row as GET lists it, source manual, version 1"},
  "run": {"id": "…", "source": "manual", "trigger": "manual", "actor_type": "engineer", "actor_id": "…", "actor_name": "olivia@firm.example", "status": "ok", "started_at": "…", "finished_at": "…", "rows_added": 1, "years_wanted": [2024], "years_added": [2024], "failures": null, "document_sha256": null, "detail": {"series": "historical", "year": 2024, "region": "US", "unchanged": 0}}
}
```

## `GET /v1/cost-indices/runs`

Any member of a firm. The ledger, newest first (`limit`, `source`): one row per attempt to
obtain values: a refresh run per source (`trigger` `refresh` or `seed`, `actor_type`
`system`; `status` `ok` with `years_wanted` and `years_added`, `skipped` with `detail.reason`
while fetching is off or the source lacks credentials, `failed` with `failures`), a manual
entry (`manual`, the user), a file import (`import`). It is how a reviewer sees when a value
arrived and why one did not.

## What a study does with it

The assets step reads the store once (inside its transaction) and prices every row with the
current values; the result and the export say which index applied (`summary.takeoff.
cost_year_source`, each asset's `cost_source`, `summary.basis.cost_index`). A cost year with no
loaded index is priced at the latest loaded one, flagged `cost:YEAR_INDEX_MISSING`
(`BASIS-COST-001`) on every priced row, with `summary.basis.cost_index.status` `missing` and
`needed` naming the value to enter (docs/api/STUDY_RESULT.md). A value entered or fetched later
reaches a study when its assets step reruns (a rejection at the assets gate, or
`POST /v1/studies/{id}/retry`); the store never changes a completed revision.

## The operator CLI

`python -m basis.costs.refresh` (cron or Cloud Scheduler, monthly, or on demand): `refresh`
(default; every enabled source, only for what the store lacks), `seed` (the released 2020-book
tables), `enter` (one value by hand, `--by` who), `import <file>` (a supplement-format JSON),
`export` (the current Historical Cost Index values in that format, for the offline replays),
`list`, `runs`. Settings: `COST_INDEX_FETCH` (default `off`: network sources are not asked),
`COST_INDEX_SOURCES` (default `rsmeans_2020_book,rsmeans_public`), `COST_INDEX_FETCH_TIMEOUT_S`,
`RSMEANS_API_URL` / `RSMEANS_API_KEY` (the subscription; the `rsmeans_api` source is a stub
without them). Exit 1 when a source failed, 2 on a usage or configuration error.
