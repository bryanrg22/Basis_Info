# The cost index store: `/v1/cost-indices`

Every priced asset's dollars are `RSMeans 2020 unit cost x quantity x year factor x location
factor`, and the two factors are published RSMeans indices (docs/review_2026-10-15/COST_INDICES.md).
The store is where every index value the product has obtained lives, with its citation, who
obtained it and when (sections 9-12 of that document). The routes read it and let a firm owner
enter a value by hand; the refresh job (`python -m basis.costs.refresh`) fills it from the
sources. Nothing in a request fetches anything.

**Scope: one store per deployment, shared by every firm.** The `cost_indices` rows carry no
firm (there is no `org_id` column), every firm member reads the same rows, and the selection
that prices a study (`basis/costs/indices/select.py`) reads them whatever the study's firm. A
value a firm owner enters with `POST` therefore prices every firm's studies on the deployment
from then on (a manual entry outranks the other sources for its year). It is not a private
per-firm override; that is why entering one needs the owner role and a citation, and why the
ledger records who entered it.

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
deployment (the value prices every firm's studies, see the scope above) and a value is a
published fact, cited. Idempotent (`Idempotency-Key`). Appends the
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

## Per-asset cost trail

Every cost row the assets step writes carries its trail (`cost_estimates.trail`, migration
`0018`; `basis/domain/cost_trail.py`): how its dollars were reached, recorded once from the
objects the row was priced with and never rewritten (the table refuses updates).
`GET /v1/studies/{id}/assets` returns it as `cost.trail` with `cost.trail_recorded`, and the
JSON export as each asset's `cost_trail` (the CSV has no trail column). Each takeoff also
returns its stored `inputs`, `method` and `assumptions`.

The rules for a reader:

* Show the trail as recorded. Never derive a base price from the rounded amounts
  (`unit_cost / factors` loses cents: a base of 1.6650 per SF and the cent-rounded 1.67 give
  different unit costs), and never substitute today's index for the one recorded: a value
  entered in the store later reaches a study only when its assets step reruns.
* A row priced before the trail existed has `trail: null` and `trail_recorded: false`; it has
  only its `source_row` sentence. Nothing is back-filled.
* The trail's `unit_cost`, `total`, `location_factor` and `cost_year` are the row's own; for
  `kind` `unit_price`, `base.unit_cost x year.factor x location.factor` rounded half up to the
  cent is `unit_cost`, and `unit_cost x quantity.value` so rounded is `total`.

Every Decimal is a string exactly as used (`"1.2330"`, not `1.233`); dates are ISO 8601.

```json
{
  "version": 1,
  "kind": "unit_price",
  "quantity": {"value": "18", "unit": "EA", "formula": "max(count=18, min_per_room=1)",
               "method": "count", "inputs": {"…": "the takeoff inputs as stored"},
               "assumptions": []},
  "base": {"unit_cost": "425", "unit": "EA", "table": "rsmeans_2020", "row": "kitchen_cabinet",
           "tier": null, "source": "<book, line, page as the table records it>",
           "base_year": 2020, "row_unit": "Ea.", "conversion": null,
           "assumption": "one cabinet unit priced as a 24-inch base cabinet"},
  "year": {"cost_year": 2024, "factor": "1.2330",
           "index": {"year": 2024, "value": "294.8", "as_of": "2024-01-01",
                     "citation": "RSMeans 2024 Building Construction Costs p.778",
                     "estimated": false, "derived": null,
                     "store": {"id": "…", "source": "manual", "version": 2}},
           "base": {"year": 2020, "value": "239.1", "as_of": "2020-01-01",
                    "citation": "RSMeans Building Construction Costs 2020 p.778",
                    "estimated": true, "derived": null, "store": null},
           "assumption": null, "conversion": null,
           "citation": "HCI 294.8 (Jan 2024) [RSMeans 2024 …] / 239.1 (Jan 2020, est.) […]",
           "from": "placed in service 2024-03-01 (intake)"},
  "location": {"factor": "0.98", "from": "zip 12345 (intake): 123 Schenectady 0.98, …",
               "lookup": "zip", "zip": "12345", "lookup_from": "intake", "state": "NY",
               "city": "Schenectady", "zip_prefixes": "123", "cities": 1, "low": "0.98",
               "high": "0.98", "pages": "720", "table_year": 2020,
               "origin": "rsmeans_2020_book", "citation": "123 Schenectady 0.98, …",
               "store": null},
  "structure": null,
  "intake_version": 3,
  "property_class": "residential_rental",
  "cost_year": 2024,
  "location_factor": "0.98",
  "unit_cost": "513.54",
  "total": "9243.72",
  "rounding": "unit_cost = base x year factor x location factor, rounded half up to the cent; total = unit_cost x quantity, rounded half up to the cent",
  "priced_per": null,
  "missing": null
}
```

* `kind`: `unit_price` (a cost row priced it), `unpriced` (no cost row, or a table row in
  another unit: `missing` is the row's `source_row` text, `priced_per` the row's unit; `base`,
  `unit_cost`, `total` and `rounding` are null; `year` and `location` are what the study would
  price with), `structure_residual` (a concealed row of the structure model whose total is the
  residual carved from the building's replacement cost new: `base` null; `structure.building`
  carries the building RCN's amount, source, formula, inputs and assumptions; `year` and
  `location` are recorded when the square-foot model applied them and null when the
  appraiser's cost new was used, whose own time adjustment is in `structure.building.inputs`;
  `unit_cost` is the total over the quantity, rounded half even, as the row is written). A
  small system of the structure model (smoke alarms, data outlets, ...) is priced from the cost
  table like any label: `unit_price` or `unpriced`, with `structure` set and `building` null.
* `base.unit_cost` is per takeoff unit at the row's base year, before the factors, exactly as
  multiplied (it can carry more than two decimals). `table` is `TYPICAL_UNIT_COSTS` (the legacy
  placeholder rows: `tier` set, `source` null) or the configured table's name; `conversion` is
  how the row's unit became the takeoff's (`x9 from per SF`, `x0.01 from per Sq.`).
* `store` names the `cost_indices` row (id, source, version) a value was read from; it is null
  for a value from the released book file, and for a city row of the 2020 book's location table
  read from the store's seed (the seed is the file's table). A state mean names its table
  (`table_year`, `origin`) and the cities averaged (`cities`, `low`, `high`), not each store row.
* `intake_version` is `studies.intake_version` read with the intake the row was priced from
  (null when the study has no intake).

## The operator CLI

`python -m basis.costs.refresh` (cron or Cloud Scheduler, monthly, or on demand): `refresh`
(default; every enabled source, only for what the store lacks), `seed` (the released 2020-book
tables), `enter` (one value by hand, `--by` who), `import <file>` (a supplement-format JSON),
`export` (the current Historical Cost Index values in that format, for the offline replays),
`list`, `runs`. Settings: `COST_INDEX_FETCH` (default `off`: network sources are not asked),
`COST_INDEX_SOURCES` (default `rsmeans_2020_book,rsmeans_public`), `COST_INDEX_FETCH_TIMEOUT_S`,
`RSMEANS_API_URL` / `RSMEANS_API_KEY` (the subscription; the `rsmeans_api` source is a stub
without them). Exit 1 when a source failed, 2 on a usage or configuration error.
