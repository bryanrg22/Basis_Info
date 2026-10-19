# Basis visual design review

This is an isolated, clickable visual proposal. It retains the current app's actual Basis logo PNGs, Inter font, and established green palette. No production frontend routes, Firebase logic, backend files, or API contracts were changed. No external messages, deployment, real uploads, or model calls are performed.

## Open the review

From this directory:

```sh
python3 -m http.server 3100 --bind 127.0.0.1
```

Open http://127.0.0.1:3100/#landing. Use the screen selector at the top to explore 19 views, including existing-page findings and proposed enterprise pages. The review works as static HTML/CSS/JS; no install is required. Do not deploy this fixture gallery as the production website.

## Team workspace extension

The owner/engineer proposal is documented in [TEAM_HANDOFF.md](TEAM_HANDOFF.md). The role selector demonstrates owner-only overview and invitations, all-team versus personal studies, creator/editor attribution, creator-only engineer lists, invitation acceptance states, and polled editing-presence/read-only concepts. No actual team service or permission enforcement was implemented.

`team-verification.json` records 114 repeated desktop view/role checks across 19 views and two roles, plus the role and editing-state interactions exercised. Although viewport changes were requested, the measured page viewport remained desktop-sized. These do not establish final phone/tablet behavior. No page-level overflow or broken images were found at the measured desktop size.

## Direction

Preserve the existing brand and useful workflow. Add depth through soft green backgrounds, translucent navigation, subtle surface highlights, rounded controls, and image-aligned object selection. Keep tables, source documents, and evidence on stable, solid surfaces. Glass is a CSS interpretation inspired by Apple's navigation/material guidance, not a claim of native Liquid Glass refraction.

Motion is limited to user-triggered hover feedback. Reduced-motion, reduced-transparency, and increased-contrast CSS are included. Primary-button gradients were darkened after checking white-text contrast against their lightest color stop.

## Original landing page recovered

The sibling dev/GitHub/basis-landing and dev/GitHub/costseg_landing directories are empty copies. Actual source was found at:

`/Users/bryanramirez-gonzalez/Documents/GitHub/costseg_landing`

Its mounted page contains: navbar, Cost Segregation Accelerated hero, Book a demo, product screenshot, feature cards, marketing metrics, Record / Review / Receive, pricing, final CTA, and footer. FAQs appears in navigation but is not mounted as a section.

Original draft pricing is retained in the proposal for visual review:

- Free: $0/month; 2 projects/month.
- Team: $750/month; 50 projects/month.
- Enterprise: custom, historically described as $2,000–$5,000/month; unlimited projects.

These are recovered marketing terms, not verified current commercial offers. Existing feature, benchmarking, compliance, and numerical performance claims require confirmation before publication. The proposed homepage does not repeat the unverified performance metrics or blanket compliance claim.

The older landing page uses a cube/Base mark, while the current application uses the Basis logo PNGs. This proposal preserves the current application's actual logo assets and does not introduce a new mark. Public content structure, demo entry point, and pricing tiers remain present.

A standalone `basis-landing.jsx` also exists in Documents/GitHub/basis-landing, but that directory's mounted `src/App.jsx` is a Vite starter. The costseg_landing app is the verified mounted original described here.

## Scope and coverage

All 15 `frontend/src/app/**/page.tsx` files were read. Ten distinct legacy screen families and the photo modal were inspected with synthetic/local fixtures. Several paths are redirects rather than separate screens:

- `/` routes to sign-in or dashboard.
- `/study/[id]/review/resources` is appraisal review.
- `/study/[id]/review/first` is photo/room organization.
- `analyze/assets`, `analyze/takeoffs`, and `review/takeoffs` redirect to engineering takeoff.
- Engineering takeoff index routes to the first asset or shows an empty state.

The real app at port 3000 stayed on the authentication loading screen during inspection. To inspect protected layouts, `legacy-preview.py` creates a disposable copy under `/tmp/basis-legacy-visual-audit`, substitutes local fixture providers, disables Firebase initialization and live study service operations, and uses a system-font fallback. The production authentication and data providers are untouched. Do not treat fixture rendering as an end-to-end application test.

To reproduce legacy fixtures:

```sh
python3 design-review/legacy-preview.py
cd /tmp/basis-legacy-visual-audit
npm run dev -- --hostname 127.0.0.1 --port 3001
```

The original landing was separately reproduced under `/tmp/basis-original-landing`, with its layout and components intact, a system-font fallback, and the available Next runtime. Its screenshot is a visual reproduction, not a live deployment inspection.

## Contract boundaries

Checked sources: `docs/api/openapi.json`, `backEnd/basis/api/routes/decisions.py`, `backEnd/basis/api/routes/evidence.py`, and `backEnd/basis/workflow/study_workflow.py`.

- The workflow envelope contains progress inside `status`, which can be null.
- Photo analysis occurs during `extracting`; the current Stage enum has no separate `analyzing_photos` value.
- pending_photos is remaining work. completed_photos is incremented after terminal attempts, including flagged failures; do not label it successful detections.
- `awaiting` determines where a decision is allowed; stage and revision are also required.
- Approval with corrections is rejected. The current OpenAPI example still shows the opposite and needs later backend documentation correction.
- The evidence endpoint returns an empty list.
- Cancellation needs execution-status/run-outcome handling; StudyStatus does not include cancelled. Cooperative cancellation can produce a completed execution with a cancelled run outcome.
- Full appraisal, room inventory, and asset gate read payloads are not exposed by the checked API spec. Presentation fields in this gallery are not frozen API schemas.
- Per-photo object records, SVG outlines, manual additions, source pages, and citations remain illustrative. Outlines are hand-drawn paths aligned to the local photo, not SAM output.
- Residential 27.5-year and nonresidential 39-year categories must remain study-dependent. No tax recommendation or classification is established by sample data.
- Workflow budget is distinct from customer billing.

## Verification

`layout-checks.json` records the initial desktop (1440×1000) and phone (390×844) layout pass across 17 screens: no page-level horizontal overflow and no broken image references. A further 51 repeated desktop layout checks after refinement, plus all baseline/proposal image pairs, are recorded in `verification.json`. The requested size changes did not affect the measured viewport in that pass, so final phone/tablet layout remains unverified. No page-level overflow or broken image references were found at the measured desktop size.

Browser interactions checked:

- Study filters, query match, no-match state, and clearing search.
- Per-object outline selection using keyboard Enter, hide/show outlines, remove/restore, and marking a photo reviewed.
- Appraisal correction entry, approval guard when corrections exist, and returning to extraction.
- All nine processing/exception preview states.
- All five takeoff tabs.
- Export-not-connected messaging and Escape dismissal of the modal.

These checks verify the design gallery only. Backend wiring, real auth, data persistence, uploading, evidence delivery, report export, permission enforcement, and real study completion are not implemented by this work.

## References

- [Apple: Materials](https://developer.apple.com/design/human-interface-guidelines/materials) — glass as a navigation/control layer.
- [Cursor](https://cursor.com/) — product-first public storytelling and interface imagery.
- [Linear](https://linear.app/) — workflow hierarchy and consistent product surfaces.
- [Procore Estimating](https://www.procore.com/estimating) — domain-relevant source-linked takeoff information.

Images are existing local repository property-photo assets. Logo files are byte-for-byte copies of the app assets. Inter is copied from the existing app's generated Next font cache for a self-contained local preview.

## Individual screenshot delivery

` screenshots/index.html ` is a browsable gallery with 21 separate full-page page/role images, 22 tab/state images, 3 updated navigation views, and 2 draft-dialog views. `basis-page-screenshots.zip` contains all 48 images, their manifests, and an offline image index. These are direct browser captures of the proposal, with illustrative data. Main page images are 1778 pixels wide; state captures retain the browser-provided resolution.

## Navigation refinement

The prototype defaults to the compact glass rail. An explicit button expands labels and the workspace/account context. Home uses a house icon; Settings uses a cog; the workspace logo returns to the studies dashboard. Public logos still return to the landing page. The toggle retains keyboard focus and supports Enter, Space, and Escape, following the [WAI disclosure control guidance](https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/).

Verified in the browser: both modes at an actual 820px viewport, Home/logo/Start study/Settings destinations, navigation state across pages, active-page semantics, Owner and Engineer links, unchanged appraisal screen access, and mobile overlay dismissal by clicking the backdrop at an actual 481px viewport. Both scripts pass node --check. These are prototype checks, not production authentication or backend checks. Updated compact and expanded screenshots are included separately; earlier page captures retain their previous navigation.

## Sidebar motion, identity, and hover preview

The sidebar now stays mounted when toggled. A 360 ms CSS transition animates its width, label opacity, and the original logo inside a growing glass pill. The user avatar remains visible when collapsed; expansion reveals name, company, and role. The account entry opens Settings. The design-notes question mark was removed from the product rail; design notes remain in the prototype toolbar.

Mouse hover opens an overlay after 120 ms, preserving the page position. Leaving closes it after 180 ms unless keyboard focus is inside. The toggle pins the sidebar open; Escape dismisses either mode. Hover is limited to fine-pointer devices above the mobile breakpoint; small screens retain button and backdrop control. A pending hover timer is cancelled on navigation, manual toggling, and breakpoint changes. Reduced-motion CSS disables transitions.

Browser verification: intermediate widths were observed in both transition directions; the draft property-name input survived Enter/Space toggles; focused controls remained mounted; hover kept the main page at x=91.98px in a 1158px viewport; moving away closed the preview, pinning retained expansion, and Escape dismissed it. Account navigation, Taylor Reed/Owner and Alex Morgan/Engineer identity, hidden engineer administration, and mobile backdrop dismissal at 481px were checked. Browser console returned no errors. Both scripts passed node --check. No production auth or backend changes.

References: [CSS transitions](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/transition), [reduced motion](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-motion), and [WAI content on hover or focus](https://www.w3.org/WAI/WCAG22/Understanding/content-on-hover-or-focus.html).

## Unsaved changes and local preview drafts

The compact sage glass prompt offers Save draft, Discard changes, and Keep editing. It uses the existing Basis logo and navigation material. Home, the logo, page links, the preview selector, role changes, and browser hash navigation are guarded when the tracked study draft differs from its last saved values. Escape and the close button keep editing; keyboard focus is trapped in the alert dialog. Clicking the backdrop never discards work.

Save draft stores form and staged review edits under a versioned, role-specific localStorage key. It does not upload files, submit gate decisions, or call a backend. File names can be retained, but file bytes are not stored. Asset form values survive tab and component changes. Discard restores the saved baseline, including visible asset inputs. Reverted edits return to a clean state. Storage errors retain the dirty state and show an error without navigating. The demo contains one sample study draft per preview role; it is not a multi-study persistence or authorization implementation.

Browser checks: all three choices; saving, reloading, and restoring the property name; asset quantity and notes across tabs; discarding asset edits back to quantity 1420 and empty notes; Back/Keep editing retaining the input and URL; role-switch protection; Escape, tab order and wrapping; a 481px layout with 16px dialog margins; and no browser console errors. Six node tests cover dirty/revert behavior, unload-listener attachment, storage failure, restoration, corrupt storage, role separation, and field-order equivalence. Run: `node --test design-review/tests/drafts.test.cjs`. All six passed.

A native beforeunload warning is attached only while dirty. Its browser-specific wording, mobile limitations, and actual native prompt appearance were not verified by the browser checks. In-app dialogs and saved-draft restoration were verified. The prototype hash guard restores a blocked address with replaceState; production needs the router's history-aware navigation blocker.

Pinned navigation now docks flush left with square outer edges and fills the viewport below the preview toolbar; the main pane reserves space beside it. Compact and hover modes retain the floating glass rail. Verified dock coordinates: left 0px, top 43.99px, bottom 896.66px at viewport height 896px (rounding from the browser zoom).

References: [beforeunload](https://developer.mozilla.org/en-US/docs/Web/API/Window/beforeunload_event) and [WAI alert dialogs](https://www.w3.org/WAI/ARIA/apg/patterns/alertdialog/).

## Latest workspace refinement (October 16)

Home now differs by role. Owners land on Company overview with company-wide active/completed/total counts and team workload; My work switches to their personal study list plus owned/assigned review queue. Engineers see their own or explicitly assigned work with no owner controls. Company review workload is separate from personal notifications. Activity has been shortened in navigation and redesigned as a dated, attributed history with expandable field changes. The company label sits under Basis; the footer shows name and role. The glass navigation and existing Basis logos are retained.

Run `node --test design-review/tests/*.cjs`: all 16 tests passed, covering drafts, role-scoped fixture visibility, assignments, counts, creator workload, combined filters, pagination, and attributed activity. Browser checks confirmed different owner/personal/engineer views, creator filtering, the owner assigned Fairview review versus the engineer Harbor review, 240-study counts (200 active / 40 completed), final page 231–240, before/after history, and no console errors. Owner and engineer layouts were inspected at an actual 390px viewport with document width 390px. This verifies these workspace views, not every existing page at that width.

The latest individual captures are under screenshots/latest/ and appear first in the screenshot gallery; previous captures are labeled earlier revisions. API wiring, real tenant permissions, live aggregates, invitation delivery, presence, and durable event history remain backend work. The existing reference-study detail screens are not bound to every row in the large fixture. See TEAM_HANDOFF.md for integration requirements.

Company logo candidates are shown in brand-references.html using official, unmodified CSSI and CBIZ wordmarks. README.md lists both firms as pilots; the intended workspace brand is not yet confirmed.

The footer user button now opens an account menu with identity and Log out; the gear still opens settings. Keyboard/outside dismissal and dirty-study navigation protection were verified. Log out is a local sign-in-screen preview, not real session revocation. See TEAM_HANDOFF.md for auth wiring requirements and browser checks.

## Settings page, replacing the profile dialog

The gear is labeled Settings and stays outside the account menu. Both it and Profile settings in the user menu open the full Settings page; the latter selects Profile. Owners see Profile, Workspace, Cost sources, and Team & invitations. Engineers see personal Profile. Name/photo controls live in the page, not a modal. Profile saves remain browser-local; company changes remain unconnected previews.

The full-page structure, entry points, role visibility, name validation/save/Cancel/reload, and 390px fit were verified in the browser. All 20 tests passed. The native photo-picker check was interrupted; photo processing is covered with mocked image/canvas tests, not a completed browser upload check. Latest captures: screenshots/latest/settings-profile.png, settings-workspace.png, settings-engineer.png, and settings-mobile.png. TEAM_HANDOFF.md records the persistence limits and backend work.

## Engineer-first walkthrough

Open [engineer-walkthrough.html](http://127.0.0.1:3100/engineer-walkthrough.html) for all 13 current engineer screens and a short proposal for each. Appraisal review and study Files have new layouts; other views retain their current structure with the shared legibility treatment. The prototype now defaults to Engineer. The sidebar mechanics remain unchanged. The dashboard removes the duplicate summary for engineers and shows creator-owned work as Created by you.

The Files page demonstrates a per-property library, appraisal/photo/supporting-document categories, search, photo previews, and sample image download links. Its Add files control is a design concept; storage and uploads remain unconnected. The original PDF is represented by a clearly labeled placeholder and is not downloadable. The review fixture now uses five photos consistently. See TEAM_HANDOFF.md for verified behavior and the remaining integration work.

The latest engineer image bundle is basis-engineer-screens.zip; it includes the offline walkthrough and individual viewport/full-page/state captures. The earlier image gallery and archive remain available as historical design revisions. All 21 tests passed. New appraisal and Files layouts were checked at measured desktop 1416px and narrow 487px widths, with no document overflow. Other pages are shown for review and still have specific follow-up design proposals.

October 18: Library is now the company-wide study archive, with a separate read-only study record, historical dates, search by property/client/address/engineer/file name, and Studies/Files views. Home stays personal. Client identity is included in the upload form and study list. Upload uses one mixed-file target with local group suggestions and filters. The latest 15-screen guide and screenshot ZIP include these views. See TEAM_HANDOFF.md for the verified behavior and backend work still required.

Follow-up: Library now browses studies only; files/reports/activity stay inside their study. PDF file cards have first-page thumbnails and a real local PDF.js reader (pages, zoom, download). Locally selected PDFs use the same reader. The archive demonstration uses a clearly labeled fictional PDF; original-document storage remains unconnected. Viewer screenshots are included in the guide.

October 18 Library polish: revised the whole study register with a full-width search, compact status filters, custom sort/page-size menus, stronger type/contrast, fewer repeated actions, and responsive cards. The approved sidebar is unchanged. Updated Library, menu, narrow-screen and before-comparison screenshots are in the engineer guide and ZIP.

October 18 engineer-wide pass: the 15-screen walkthrough now contains current screenshots for every engineer view. product-polish.css and product-controls.js unify controls and working surfaces while preserving the sidebar. Start a study also has a sample address-suggestion combobox with manual entry and draft persistence; real address lookup is not connected. The save-before-leaving dialog has corrected inset spacing and a compact glass presentation. See TEAM_HANDOFF.md and the desktop/mobile measurements for verified behavior.

October 18 room-review repair: separate collapsible room groups, full-card component selection, current-room Previous/Next and filmstrip, and a large photo viewer for every fixture photo. See the updated room/viewer screenshots and TEAM_HANDOFF.md. Integration remains with Claude Code.
