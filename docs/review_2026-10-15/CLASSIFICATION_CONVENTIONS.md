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
migration docstring). `ClassificationCacheRepository.get_many(org_id, keys)` (replaced by
`get_for_labels` in section 9) and `put(org_id, key, ...)`.

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

## 8. As built (2026-10-29), where it differs from the sections above

- Timeline events are dotted, as the vocabulary requires: `convention.promoted` and
  `convention.replaced` (not `convention_promoted` / `convention_replaced`).
- The provenance snapshot lives on the asset: `assets.convention` (JSONB, migration 0014,
  allowed only when `method = 'cache'`), with `key`, `study_id`, `approved_at`,
  `approvals`, `agrees_with_rules`, `reviewer_id`, `rule_key` and `citation`
  (`"firm convention, uncited"` or null). The export's `assets[]` and `GET /assets` read it.
- An approved `cache` asset (a convention the engineer let stand) adds one approval to its
  convention; it never creates or replaces one. Priority inside one study when several
  approved assets share a key: manual, then cited llm, then cache; if the winning group
  disagrees on bucket, period or section, the key is skipped.
- The promotion activity reads the `reviews` approve row only when there is a candidate to
  promote; a revision with nothing to promote returns an empty output.
- On the convention path only validator *errors* send the asset to review; the
  label-heuristic warnings (`PERSONAL_AS_BUILDING`, `STRUCTURAL_AS_PERSONAL`) do not,
  since a ruling against the rule row is the point of a convention.
- `ClassifyContext.org_id`: the router never serves a convention of another firm.
- `rule_key` on the convention path carries the rules-table key, so takeoffs use the same
  key on every path.
- The `promote_to_cache` function and `NotApprovedError` of the old cache are gone.
- Not done here: the CSV export has no convention column (the JSON export and `/assets`
  have it); no new replay history was recorded for the `firm-conventions-v1` patch (the
  fresh-history test covers the new branch); the classifier prompt is unchanged.

## 9. The key hierarchy: a ruling matches the next study's component when the photo step reads it differently (design, 2026-10-30)

Why: EVAL_REPORT section 42.5, finding 1. On the live pair of studies, study B's two
fireplaces came out of the photo step with no material where study A's rulings were keyed
on `wood` and `other`, so no convention matched and Fireplaces was the one wrong row; nine
more convention-row assets had no ruling because their room or material was one the first
study never showed. The material and attachment the vision step reads vary between runs;
a ruling keyed on all six parts does not generalize as far as the engineer meant it.
EVAL_REPORT section 46 reports the result.

### 9.1 Three tiers, most specific first

The context key stays `label|material|attachment|room|property_class|RULES_VERSION` and
the table keeps one row per exact key. The *reader* derives a hierarchy from it:

| tier | `matched_tier` | the firm's rows that are candidates | beats |
|---|---|---|---|
| 1 | `exact` | the row with the component's exact key | everything (as today) |
| 2 | `label_room` | the rows with the same label, room, property class and rules version, whatever their material and attachment | the rules table and the model |
| 3 | `label` | the rows with the same label, property class and rules version, whatever their material, attachment and room | the model, and a *contested* rule row |

The most specific tier with a candidate serves. Property class and rules version are never
relaxed: a ruling for a residential rental is not a ruling for a nonresidential building,
and a ruling made under older rules is never served (section 3).

Two labels are exceptions, measured on the live pair before they were written down
(`evals.conventions_replay`, section 9.5):

- **A label whose material is the item keeps it at every tier** (`MATERIAL_LABELS`). The
  generic floors `flooring`, `floor`, `floor_covering` and `floors` carry no answer of
  their own; the rules table resolves them by material (vinyl, carpet, tile ...). The
  surface finishes `wall_finish` and `ceiling_finish` are the same: on the live pair the
  firm ruled wallpaper and wood paneling 5-year (wall coverings) and drywall, painted
  masonry and fiberglass building (the wall), and with the material relaxed those rulings
  conflicted on six of study B's walls (9.3) where keeping it serves four of them from the
  matching material's ruling and sends the two unruled materials to the model. So the
  tiers for these labels are `label|material|attachment|room`, then
  `label|material|·|room`, then `label|material|·|·`. Every other label carries the item
  (carpet and tile, baseboard and crown molding are different labels), so the material is
  a reading of the photo, not part of the ruling.
- **The catch-all label never generalizes.** `other` names nothing: a ruling on
  `other|wood|freestanding|exterior` (a bench) says nothing about `other|fabric|exterior`
  (an awning). On study B the label tiers would have served 16 `other` assets from
  unrelated rulings, with 11 conflicts. `other` matches at the exact tier only.

Attachment is relaxed at tiers 2 and 3 for the same reason as material: `permanent`,
`removable` and `freestanding` are what the vision step read this time. Where a firm has
ruled the same label in the same room differently by attachment (a hanging mirror 5-year,
a glued one building), the two rulings disagree at tier 2 and the rules table, whose mirror
row keys on the attachment, decides (9.3).

### 9.2 Why the label tier does not outrank an uncontested rule row

A tier-2 match is the engineer's ruling on this item in this room, read with another
material: it is the ruling. A tier-3 match is the ruling on this item in *another* room,
and the room is the dimension the rules table itself keys on (section 4: a cabinet in a
bathroom, furniture in an office). Measured on study B with the plain label tier: the
firm's two sink rulings (the kitchen sink and the garage utility sink, 5-year, against the
guide) would have served the seven bathroom sinks, which the guide puts with the building
(ATG p.315) and the engineer's Plumbing row agrees. The engineer never ruled on a bathroom
sink because the rule row was already right, and a rule asset is never promoted (section
5), so the table holds no counter-evidence.

So: at tier 3 a ruling beats the model and beats a *contested* rule row (the guide names
no passage for the item, or practitioners differ: the row goes to the engineer anyway, and
the firm's position across rooms is the better default), but not an uncontested one (the
guide names the item for this context; the engineer's correction in *this* room, which
tier 2 then serves, is what outranks it). On study B that gate blocks exactly the seven
bathroom sinks and nothing else. The hardwood floors in the lobby (contested row) and the
baseboards and wainscoting in rooms study A never showed (no rule row) are served.

### 9.3 Disagreement inside a tier

Several rows can be candidates at tiers 2 and 3. They are grouped by answer
(`answer_of`: bucket, recovery period, section). One answer: the row with the most
approvals serves (then the smallest key, so the choice is stable). Several answers: the
answer whose strongest ruling has the most approvals wins; when the strongest rulings are
equally approved the firm is split on this component, no coarser tier is consulted (it is
less specific still), and the rules table decides, then the model. The asset the table or
the model classifies then carries `review_reasons` `convention_conflict:<tier>`
(`BASIS-ASSETS-014`) naming the tier where the firm's rulings disagreed, so the engineer
can settle it: their ruling at this study's gate becomes the exact row that wins next time.

### 9.4 Counting, provenance, the writer

- **Approvals and hits are counted on the row that served.** `process_assets` records the
  hit on the served row's key; when the study is approved, an asset let stand (`cache`)
  confirms the row named in its `convention.key`, not a row for its own exact key (there
  is none). `promote_conventions` therefore groups a `cache` asset under the served key.
- **The asset says which tier matched.** `assets.convention` gains `matched_tier`
  (`exact`, `label_room`, `label`); the rationale gains `matched: exact` or
  `matched: label and room (ruling keyed fireplace|wood|permanent|living_room|...)` /
  `matched: label only (...)`. `GET /assets` (`ConventionOut.matched_tier`) and the
  export's `assets[].convention` carry it; an asset classified before this change has no
  member and is read as `exact`. The rest of the provenance (`key`, `agrees_with_rules`,
  `rule_key`, approvals, reviewer, study, citation) is the served row's.
- **The writer still writes exact keys only.** The tiers are derived at read time, so an
  engineer's later ruling on the exact context always wins at tier 1, and no row ever has
  to be rewritten when a coarser one would have served. No migration: the table's key is
  unchanged.
- **The prefetch is by label.** `process_assets` fetches the firm's rows whose label is one
  of the revision's labels at the current rules version (`get_for_labels`), including the
  concealed rows' labels (the structure model's views were not prefetched before, so a
  ruling on a concealed row could never be served). `route()` builds the tiers from them
  (`ConventionIndex`).

### 9.5 Measured offline (EVAL_REPORT section 46)

`evals.conventions_replay`: study B's stored export and a dump of the firm's conventions
as of study A's approval; for every asset the tier that would serve, the conflicts, the
convention-row assets still without a ruling, and the rescored export through
`evals.e2e_report`, under the policy above and under the plain label tier (no gate, no
catch-all exception) so the two guards are measured, not asserted.

## 10. A ruling is kept until the rule it was made under changes (design, 2026-10-31)

### 10.1 The defect

The key's sixth part was `RULES_VERSION`, and the reader served only rows at the running
version. #94 and #100 moved it from `2026-10-29.1` to `2026-10-31.1` by adding the structure
rows: `git diff 8661710 9c430aa -- basis/domain/rules.py` is 222 insertions and one deletion
(the version string). No row a ruling was made under changed, yet every ruling stopped
serving. Live run 4 (2026-10-31, main 9c430aa) measured the cost: the firm's 157 rulings
(233 approvals) were hidden, and the study scored 27 of 35 rows against 34-35 of 35 when
they were served. The eight rows it lost were exactly the ones the firm had ruled on
(baseboard, hardwood floor, wainscoting, half wall, fireplace, sink at 5 years). Run 3's own
build also changed a row without a bump (7c1aa9e, the closet-shelving variant, stayed at
`2026-10-29.1`), so a hand-bumped global version is both too coarse and easy to forget.

### 10.2 The standard

A cache entry is invalidated when one of its inputs changes, and only then. Build caches key
an action by a digest of its declared inputs (Bazel's action key is the digest of the
canonical action and its input digests, so only actions whose inputs changed rerun). Rails
versions each record's cache entry separately rather than bumping a global namespace. The
input of a convention is the rules-table entry its label resolves to.

### 10.3 The key

```
key = label | material | attachment | room | property_class | rule_fingerprint
```

`rule_fingerprint(label, material)` (`basis/domain/rules.py`) resolves the label as `lookup`
does: a generic floor by its material (`MATERIAL_RULES`), any other label by
`canonical_key` (direct, alias, trailing `s`). It is `r<scheme>-none` when nothing matches,
otherwise `r<scheme>-` plus the first 16 hex digits of the SHA-256 of the canonical JSON of
the resolved row. That JSON holds:

* the rule key;
* every field of the row and of each variant (its `When` sets sorted) except the
  explanation: `category`, `irs_note` and `citations` (`EVIDENCE_FIELDS`), which `answer_of`
  leaves out of an answer for the same reason. Rewording a note or re-citing a page, as the
  2026-10-15 re-citation pass did to every row, retires no ruling;
* fields at their dataclass default left out, so adding a defaulted field to `Rule` changes
  no fingerprint;
* `STRUCTURAL_ATTACHMENTS` when the row or a variant is section 1245.

A ruling on `baseboard` stops serving when the `baseboard` row, one of its variants, or the
alias that leads to it changes. It keeps serving when an unrelated row is added.

Why the granularity is the whole row, not the variant that fired: the label tier serves a
kitchen ruling in a bedroom (section 9.1). A finer fingerprint would let a changed
bedroom variant keep serving a ruling made under the old one.

What the data cannot see: `lookup`'s own logic, such as the order of `canonical_key`'s
fallbacks or `bucket_for`. A change there bumps `RULE_FINGERPRINT_SCHEME`, which changes
every fingerprint. This plays the role of Bazel's declared toolchain. `RULES_VERSION` stays
the table's release name. It is still recorded on each row (`rules_version`, the version
that seeded or last replaced it), in the eval ledger and in asset provenance, but it no
longer decides serving.

Consequence: approvals accumulate across releases. A ruling the next release does not
touch keeps its key, so the next approval confirms it instead of seeding a parallel row.

### 10.4 Existing rows (migration 0019)

Rows already stored end in a version string. Migration 0019 re-keys each one to the
fingerprint of the rule it was made under. That is the released table of its version on
main:

| version | released by |
|---|---|
| `2026-10-29.1` | #93, a0bb73a |
| `2026-10-30.1` | #94, 2911b13 |
| `2026-10-31.1` | #100, 07e9807 |

Each version is one released state; branch commits in between never ran on a shared
environment. The migration carries those tables' resolution data frozen (aliases, material
map, generic floors, each row's fingerprint, the structural set), generated by
`evals/freeze_rule_fingerprints.py` from the three commits. It does not import the live
table, which will have moved on by the time the migration runs.

The reader then serves a re-keyed row only if its fingerprint equals the live one. A row
made under a rule that has since changed stays stored and is never served, as before.

When two rows land on the same new key:

* the same answer merges (approvals and hits add up, and the earliest ruling stays the
  seed);
* different answers are settled as `put` settles them: the latest approval holds the key,
  and the older row keeps its old key, stored and never served.

A row with an unknown version or a malformed key is left alone. Downgrade restores the
version suffix from `rules_version`; rows merged on upgrade stay merged.

The migration is plain SQL, so `alembic upgrade --sql` renders it and online and offline
runs execute the same statements:

* the frozen tables load into temporary tables;
* a key's fingerprint is a lookup there, ordered as `canonical_key` orders it (row key,
  alias, the singular);
* the merge uses window functions, comparing answers as `answer_of` does.

Its integration test checks it on Postgres for every way into each frozen table. The
reference is the live `resolve_fingerprint` over that table.

### 10.5 Tests

* The fingerprint is stable under an unrelated added row, and it changes when the label's
  row, a variant, the alias target or the material map entry changes.
* A defaulted field does not change it, and a label with no row gets `-none`.
* Generic floors resolve by material.
* The reader serves a row at the live fingerprint and leaves out one at another
  fingerprint or at an old version suffix.
* `put` keeps one key across a `RULES_VERSION` bump.
* The migration's frozen data, regenerated from git, equals what is committed.
* The migration re-keys, merges and settles conflicts on a database, and downgrade works.
* An offline replay of run 4 with the firm's stored rulings, which must restore the rows
  the live run lost.

### 10.6 As built and measured (EVAL_REPORT section 50)

The build matches 10.3-10.5. `rule_fingerprint`, `rule_digest`, `resolve_fingerprint` and
`RULE_FINGERPRINT_SCHEME` are in `basis/domain/rules.py`. `cache_key` takes `fingerprint=`,
and `is_current` filters `get_for_labels`; `put` records the running release in
`rules_version`. `evals.conventions_replay` re-keys rows to the running fingerprints, and
`--keep-rules-version` keeps their own keys.

Migration 0019 on a copy of the stack's database re-keyed all 157 rulings the firm held
before run 4. All 157 are current, and replaying run 4 with them scores 34 of 35 rows against
27 of 35 live.

One known gap. A study in flight across the deploy carries the old key in its assets'
`convention` provenance, so its approval cannot confirm the re-keyed row (`confirm` finds
nothing and writes nothing): one approval is lost per such key, and nothing is served wrongly.

## 11. A decision outranks a model answer at every tier (2026-11-01)

**What went wrong.** Live run 5 (EVAL_REPORT 54) classified Fireplaces as 39-year although the
firm's engineer had ruled 5-year on study 3A. The ruling was keyed with the material
(`fireplace|other|…|bedroom`, `fireplace|wood|…|living_room`). Study 3B, a plain run approved
without a correction, had promoted the model's 39-year answer for `fireplace||…|bedroom` (no
material read) as an approved model answer with two approvals. Run 5's bedroom fireplaces, also
read without a material, met that row at the exact tier, and an exact match consults no coarser
tier; the engineer's ruling, one tier down, never spoke. Specificity outranked the kind of row.

The same shape at tiers 2 and 3: two blanket approvals of a model answer outvote one correction,
because 9.3 settles a disagreement by approvals alone.

**The rule.** The firm's rows are of two kinds, read from the row's `result.method`:

| `origin` | seeded by | weight |
|---|---|---|
| `decision` | a `manual` asset: an engineer's correction at the assets gate | a decision |
| `model` | an `llm` asset with reference citations, approved untouched | an answer the engineer did not object to |

`ConventionIndex.match` now looks for the firm's **decisions** first, at their most specific
tier, with 9.1 to 9.3 unchanged among them; only when no decision reaches the component at any
tier does it look at the model answers, again by tier. Approvals still settle a disagreement, but
only among rows of the same kind: nine blanket approvals of a model answer settle nothing between
two rulings. `promote_conventions` never replaces a decision with a model answer (a key whose
standing row is a decision with a different answer is skipped). The provenance carries `origin`,
and the rationale says `the engineer's ruling` or `an approved model answer`.

**Why not stop promoting model answers.** Measured on run 5's export (replay, no paid run):

| rows | served by rulings | bucket right | max share error |
|---|---|---|---|
| as before (every row) | 201 | 34/35 | 3.3 |
| decisions only (29 rows) | 47 | 35/35 | 1.8 |
| every row, decisions first (this section) | 201 | 35/35 | 1.8 |

Decisions alone fix the row but hand about 140 items per study back to the model (about 1.50 a
study at run 5's prices); decisions first keeps both. The blanket rows of the test runs (3B, run 4)
are removed from the dev database regardless: they were approved by a script, not an engineer.

**Tests:** `tests/unit/classify/test_router.py` (a decision at a coarser tier beats an exact
model answer with more approvals; decisions that disagree are a conflict whatever the model
answers say; the origin is read from the row), `tests/node/test_process_assets.py` (a model
answer does not replace a ruling).
