# Primary frontend recovery and workflow review

Date: 2026-11-01. This is a local recovery and read-only browser review, not a
deployment or a claim that every workflow mutation is integrated.

## Source and recovery

The approved redesign was intact in the `vision-review-workbench` worktree
(`codex/vision-review-workbench`, base `a987253b86e0bc3024b0a2c3d2c342d7bfd4f26d`).
Port 3100 had served the different `web-walkthrough` worktree. The work was not
deleted. The recovery archive contains 382 tracked/changed source files and its
manifest was checked byte-for-byte; no credentials or runtime files were copied.
Archive SHA-256: `860907e312265c9e34cff3c06016e9d40c5c883f56a33cdb1632290f1ec05d7f`.

`codex/frontend-primary` starts at `ed3d47a01792a1af2de0a82836028458396efd88`,
restores the approved web source, and keeps the current report module. The original
worktree and recovery archive remain available. The active server now serves this
checkout. The concurrent root checkout and backend processes were left alone.

## Integration included

- The recovered results screen and the current client-report controller load
  together once. Draft preview and existing signed state remain available.
- Existing signed reports show a warning when the API reports changed inputs.
- Room placement accepts both the current `unplaced` field and the earlier
  `unassigned_photos` field, preserving `analyzed: false` and deduplicating photos.
  The room-origin photo viewer accepts the same shapes without component overlays.
- The local `web/serve.py` launcher resolves its own web directory, binds to
  loopback and disables conditional/stale development caches. It does not depend
  on the terminal working directory.
- The current invitation-email wording was retained.

## Verification

`node --test web/tests/*.test.cjs`: 283 passed, zero failed. Coverage includes the
recovered module load order, result/report coexistence, signed-state rendering,
both room-response shapes, unplaced deduplication and an actual local HTTP server
started from a different working directory. The conditional request receives the
current entry with `Cache-Control: no-store`. First-party JavaScript syntax,
`git diff --check` and the staged client-data guard were also checked.

Browser observations on the local live API:

- Existing study entry opens progress; the active Rooms action opens its gate.
- Recovered room metrics, labeled edit/expand controls and move handles render.
  Edit and move dialogs were opened and dismissed without saving.
- Rooms opens a photo-only viewer; Components opens outlines and its inspector.
- Appraisal has the restored larger PDF, source highlighting and field search.
- Components, grouped assets, asset details, cost evidence, basis worksheet,
  reconciliation, concealed systems, inputs, history and original files load.
- Draft report details open, the 42-page draft PDF renders, and an existing signed
  report shows its changed-input warning. No report was signed in this review.
- Four screens were measured at 390 CSS px: Rooms, Appraisal, Assets and Results.
  No page-level horizontal overflow was observed, but the rail, cramped appraisal
  columns and wrapping currency values need responsive design work.
- No new console error appeared after explicitly refreshing the recovered entry.
  One earlier log entry came from the stale pre-refresh live-review module; the
  refreshed DOM loads vision-model before the recovered live-review version.

Private screenshots and the annotated review are outside the repository. Red
boxes are proposals for the next pass, not changes already implemented.

## Open contracts and limits

1. The current backend requires intake before Start. The restored upload screen
   creates a study without the required purchase/use/land intake. Wire this flow
   before claiming new-study end-to-end success. No start request was submitted.
2. Intake writes need `If-Match` and the supported reopen/new-revision path for
   started studies. The restored form still offers in-place edits and compares
   before writing, which is not an atomic precondition. The backend correctly
   locks started intake; no intake save was attempted here.
3. Room create/remove backend work remains in the separate room-management
   worktree. Integrate and run its API/lifecycle tests before enabling a release.
4. The running API OpenAPI response still has `RoomsPageOut` without `unplaced`.
   A completed study's uploads and room-photo counts disagree. The frontend
   adapter is covered, but missing-photo visibility is not live-verified until
   that backend change is running.
5. Uploads, paid processing, correction saves, gate approvals, repricing, signing
   and destructive operations were deliberately excluded from this read-only
   review. No deployment, migration, merge or complete backend CI claim is made.
6. Developer error history, hosting/backups and evaluation-record integration
   remain deferred to the concurrent backend work.

## Next visual pass

Keep the approved style. Priorities are intake readiness, report visibility and
status, useful appraisal warnings, room/component filtering, readable cost
evidence, clear asset navigation, mobile layout and consistent button targets.
Do not treat restored controls as proof that their corresponding saves work.

The staged client-data guard initially rejected four restored marketing PNGs.
Each was visually inspected (three existing team headshots and one Google logo),
then pinned by SHA-256 in the existing exceptions file. No study photos were
waived or added. Vendored PDF viewer whitespace was normalized and documented.
