# Classification by context, and the firm's conventions (design, 2026-10-29)

Why: the two live runs of 2026-10-29 (EVAL_REPORT section 38) put 24 of 36 evidenced
engineer rows in the right bucket. Of the 12 wrong rows, five are rule rows the table kept
at a legacy 5-year treatment with a "contested" note although the IRS guide puts the item
with the building (plumbing fixtures, light fixtures and ceiling fans, cemented tile,
restroom cabinetry), one is an asset class chosen by the item's name instead of the
business it serves (furniture, 00.11 vs 57.0), and the rest are the firm's own conventions
(moldings, fireplaces, paneling, wall coverings, half walls, window guards, exterior
lighting). Correcting assets at the gate does not change the next study: the classification
cache that was meant to carry engineer decisions forward has no writer, sits behind the
rules table in the router, and is keyed without the room or the firm.

This document is the contract for the change. EVAL_REPORT section 42 reports the result.

## 1. The principle the rules follow

The IRS Cost Segregation Audit Techniques Guide (ATG, PDF pages 19-21) states the test:
there is no bright line; the question is whether the item is a structural component,
which "hinges on what constitutes an inherently permanent structure, how permanently the
asset is attached to such a structure and whether it relates to the operation or
maintenance of the structure" (p.20, quoting Treas. Reg. 1.48-1(c)-(e); Whiteco factors
p.21). The regulation lists "plumbing and plumbing fixtures, such as sinks and bathtubs;
electric wiring and lighting fixtures" and "any permanent coverings therefor such as
paneling or tiling" as structural components (p.19). The ATG's residential rental matrix
(Exhibit A, pp.296-322) applies that test item by item, and distinguishes by **function
and attachment, not by name**:

| item | building (section 1250) | personal property (section 1245, 5 years) |
|---|---|---|
| electrical | branch circuits and "outlets of general applicability and accessibility" (p.301) | wiring and outlets *dedicated* to an appliance (p.301, p.311) |
| lighting | fixtures that "provide substantially all the artificial illumination" (p.313); ceiling fans whose light is the primary source (p.298) | accent/decorative fixtures not needed for operation; "If the decorative lighting is the primary source of lighting, then it is § 1250 property" (p.313) |
| plumbing | "bathroom plumbing fixtures (e.g. sinks, bath tubs, showers, toilets)", kitchen sinks, water heaters as building plumbing (p.315, p.311) | water or gas branch hook-ups "directly connected to appliances" (p.311) |
| floor coverings | "affixed with permanent adhesive or nailed or screwed in place ... ceramic or quarry tile ... and wood flooring" (p.306) | "strippable adhesives ... VCT, sheet vinyl, and carpeting" (p.307) |
| cabinetry | kitchen and restroom cabinets, counters, sinks (p.298, p.311, p.317); built-in shelving in closets (p.305) | (none in the residential matrix; Metro National, ATG p.100, is a nonresidential case) |
| furnishings | | rental office furniture 00.11, 7 years (p.307); residential furnishings "beds, chairs, sofas, and tables" 57.0, 5 years (p.308) |
| mirrors | "mirror attached with permanent adhesive" is a restroom component (p.317) | hanging mirrors as décor (restaurant chapter, p.191) |

So the general mechanism is a **context dimension** on the rule: the same label resolves
to a different row by the room it is in, how it is attached, and the property class. The
burden of proving section 1245 is the taxpayer's, so the default for an item the guide
names as a building component is the building bucket, and the 1245 variant needs
evidence (an attachment the vision step saw, a room, or a label that names the specialty
form).

Decision rule for every row in this change: **an item the guide names takes the guide's
bucket; an item no document names keeps its bucket and its contested note.** The firm's
different conventions are not rule changes; they are layer 1 below.

## 2. Three layers, in order of precedence

1. **Firm conventions** (`classification_cache`, scoped by `org_id`). An engineer's
   approved decision for a context key. Beats everything. Seeded automatically when an
   `assets_review` is approved. Carries who, which study, when, and whether it agrees
   with the rules table.
2. **The rules table** (`basis.domain.rules`), with the context dimension. Deterministic,
   free, every row cited to a verified passage.
3. **The retrieval classifier** (`basis.classify.residual`) for labels neither layer
   covers. Unchanged in this change.

Today's router order is rules, cache, model; the cache can never override a rule row.
The new order is conventions, rules, model.

## 3. The context key

```
key = label | material | attachment | room | property_class | RULES_VERSION
```

`room` is `basis.domain.rooms.normalize_room_type(room_type)` (`""` when unknown);
`attachment` is the vision vocabulary (`permanent`, `removable`, `freestanding`,
`unknown`/empty). The same key is used by `lookup` (to pick a variant), by the firm
conventions (to match a prior ruling), and by the cache. `RULES_VERSION` becomes
`2026-10-29.1`; the old cache keys (five parts) never match again, which is intended: the
table is empty on every environment (verified on the local stack on 2026-10-29; staging is
parked).

## 4. Rules: the context dimension (`basis/domain/rules.py`)

`lookup(label, material=None, attachment=None, property_class="residential_rental",
room_type=None)`: `room_type` is a new keyword; every existing call keeps working.

A `Rule` gains `variants: tuple[Variant, ...]`, each `Variant(when: When, rule: Rule)`
where `When(rooms=frozenset(), attachments=frozenset(), property_classes=frozenset())`
matches when every non-empty set contains the normalized value. The first matching variant
is the effective rule; otherwise the base row. `RuleHit` gains `context: str | None`
naming the variant that fired (e.g. `"room=bathroom"`, `"attachment=removable"`) so the
asset's rationale and the export say why.

Row changes (each new excerpt verbatim on its page, recorded in RULES_RECITATION.md,
checked by the `rule_citations` eval and the real-corpus integration test):

| key | before | after | why |
|---|---|---|---|
| `toilet`, `bathtub`, `shower` | 5-year, contested | building (`_bldg`), not contested | ATG p.315 Plumbing - Building; p.317 Restroom Components |
| `sink` | 5-year, contested | building, not contested | p.315 (bathroom fixtures and kitchen sinks); p.311 Kitchen Components / Kitchen - Plumbing |
| `water_heater` | 5-year, contested | building, not contested | p.315 "all other components of a building plumbing system" |
| `garbage_disposal` | 5-year, contested | building, not contested | p.311 Kitchen - Electrical / Kitchen - Plumbing |
| `light_fixture` | 5-year, contested | building, not contested | p.313 Light Fixtures - Interior - Building |
| new `accent_lighting` (aliases `track_lighting`, `decorative_lighting`, `neon_lighting`, `landscape_lighting`) | (`decorative_lighting` aliased to `light_fixture`) | 5-year 57.0 | p.313 Accent/Decorative/Special |
| `ceiling_fan` | 5-year, contested | building, not contested | p.298 Ceiling Fans |
| `smoke_detector` | 5-year, contested | building, not contested | p.305 Fire Alarm and CO2 Detectors |
| `thermostat` | 5-year, contested | building, not contested | p.304 Energy Management Systems; p.310 HVAC |
| `tile` | 5-year, contested | building, not contested | p.306 Floor Coverings - Permanent |
| `hardwood_floor` | 5-year, contested | building, contested kept ("studies commonly treat glued wood flooring as 5-year; p.306 lists wood flooring as permanent") | p.306 |
| `bathroom_vanity` | 5-year, contested | building, not contested | p.298 Cabinetry - Restroom; p.317 |
| `kitchen_cabinet`, `countertop` | 5-year, contested | base unchanged (5-year, contested); variant `room=bathroom` → building | p.298 / p.317 restroom cabinetry; the kitchen row stays as it is because the ATG's own case table (Metro National, p.100) gives practitioners a 1245 path for nonresidential cabinetry, and the engineer's report uses it |
| `closet_shelving` | 5-year, contested | building, not contested | p.305 Finish Carpentry |
| `mirror` | 5-year, contested | building (permanent adhesive, p.317); variant `attachment in {removable, freestanding}` → 5-year 57.0 (décor, p.191) | |
| `furniture`, `chair` | 7-year 00.11, contested | 5-year 57.0 (p.308 Furnishings - Residential; p.297); variant `room=office` → 7-year 00.11 (p.307 Furnishings - Rental Office Furniture); not contested | the asset class follows the activity the furniture serves, not the item |
| `desk`, `filing_cabinet` | 7-year 00.11 | 5-year 57.0 with the same `room=office` variant → 7-year 00.11 | same |
| `doorbell`, `laminate_flooring`, `half_wall`, `window_guard`, `shed`, `concrete_pad`, `deck`, `outdoor_lighting` | | unchanged | no document names the item, or the guide gives both sides |

`contested` after this change means: the documents' position is adopted but no passage
fixes the item, or practitioners commonly differ; the hit still goes to the engineer
(`rule_contested:<key>`). Rows the guide names outright lose the note.

Not changed here: the classifier prompt (`classify/v1`). A v2 that states the function
and attachment tests is worth measuring with the `assets` eval before it becomes the
default; it is not part of this change.

## 5. Firm conventions (`basis/classify/cache.py`, migration 0014)

`classification_cache` becomes firm-scoped:

| column | change |
|---|---|
| `org_id` | new, `uuid NOT NULL`, FK `orgs.id`; primary key becomes `(org_id, key)` |
| `key` | the six-part context key |
| `result` | as today: category, section, bucket, asset_class, citations, plus `rationale` |
| `source` | `approved_asset:<asset id>` as today |
| `study_id`, `reviewer_id`, `approved_at` | new: the study whose approval seeded it, the reviewer (`reviews.reviewer_id`), when |
| `rule_key`, `agrees_with_rules` | new: what the rules table says for the same key, and whether the convention agrees (`NULL` when no rule row) |
| `approvals` | new, `int DEFAULT 1`: how many approved studies at this firm produced the same answer (a later identical approval increments; a different answer replaces the row and resets to 1, with the previous row kept in `study_events` as a `convention_replaced` event) |
| `hits` | as today |

The table is empty everywhere, so the migration drops and recreates it (documented in the
migration docstring). `ClassificationCacheRepository.get_many(org_id, keys)` and
`put(org_id, key, ...)`.

**Writer:** a new activity `promote_conventions` runs in the workflow right after the
`assets_review` approval and before `exporting`. For the approved revision it promotes:

- every asset with `method = manual` (an engineer's correction): the ruling itself, with
  the correction's `reason` as the rationale; citations are the asset's reference
  citations when it has any, else none (a manual ruling needs no citation to be cached,
  unlike today's `promote_to_cache`, but the export marks a convention without a citation
  `citation: firm convention, uncited`);
- every asset with `method = llm` that has at least one verified reference citation: an
  engineer-approved model answer, so the next identical component costs nothing;
- never `rule` (free and deterministic) or `cache` (already there; increments `approvals`
  instead).

The activity is idempotent (re-running on the same revision changes nothing), records a
`study_events` row per promoted key (`convention_promoted`, with the key, bucket and
`agrees_with_rules`), and is in `activity_options.py` with a short timeout and no
heartbeat. A failure is not fatal to the study: the workflow logs it with its error code
and continues to `exporting` (the study's result does not depend on the cache).

**Reader:** `process_assets` prefetches conventions for the study's `org_id` with the
context keys of its components (it already prefetches cache entries by key). The router's
`route()` checks conventions first, then rules, then the model. A convention hit:

- `method = cache`, `confidence = CACHE_CONFIDENCE`, `needs_review = False` even when the
  convention disagrees with the rules table (that is the point of a ruling), but the
  `review_reason`-free provenance says so: `rationale = "firm convention (approved by
  <reviewer> on study <id>, <date>; <n> approvals); agrees with the rules table: yes|no
  (<rule key> says <bucket>)"`;
- the export's asset record carries `convention: {key, study_id, approved_at, approvals,
  agrees_with_rules}` so the engineer and an examiner can see where the position came
  from;
- the `CITATION_UNVERIFIED` path stays as today for conventions that carry citations.

Error codes: `BASIS-ASSETS-012` convention promotion failed (non-fatal), `BASIS-ASSETS-013`
convention replaced (an event, not a failure). Catalogue and `docs/ERROR_CODES.md` updated.

## 6. Tests

- `tests/unit/domain/test_rules_table.py`: every row above pinned to its new section and
  bucket for both property classes; variants (bathroom cabinet → building, office
  furniture → 7-year, removable mirror → 5-year, bedroom furniture → 5-year); `room_type`
  aliases (`restroom` → bathroom) resolve; an unknown room falls to the base row; the
  contested set is exactly the rows section 4 leaves contested; every new excerpt is at
  least 12 characters and names a real page; `RULES_VERSION` bumped.
- `tests/unit/classify/test_router.py`: the key has six parts; a convention beats a rule
  row; the convention carries its provenance into the result; the same label in another
  room or firm does not match.
- `tests/node/test_process_assets.py` and `tests/integration/db/...`: `promote_conventions`
  seeds from manual and cited-llm assets only, is idempotent, increments `approvals` on a
  repeat, replaces on a different answer with an event, and a second study at the same
  firm classifies from the convention with no model call; a study at another firm does
  not; a failed promotion does not fail the workflow.
- Migration 0014 forward and back; the API/export schema tests for the new asset field.
- `docs/review_2026-10-15/RULES_RECITATION.md` gains one entry per new excerpt; the
  `rule_citations` eval (`evals.run --dry-run` builds it) and
  `tests/integration/retrieval/test_real_corpus.py` (eval data) must resolve every one.

## 7. Measuring the change without a paid run

`evals/reclassify_export.py <export.json> --out <new export.json>`: re-runs `lookup` with
the context key on every asset of a stored export (label, material, attachment, room
type, property class), replaces the classification where a rule now hits (the router
would have taken the rule before the model), leaves model-classified assets alone, and
writes an export the scorer (`evals.e2e_report`) reads. Applied to run 1 and run 2 of
section 38, the before/after of every engineer row is the measurement in EVAL_REPORT
section 42. The firm-conventions layer is measured with the node test's second study, not
with the client data (run 2 approved no asset corrections, so there is nothing to seed).
