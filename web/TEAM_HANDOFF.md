# Firm workspaces, team access, and editing presence

Design preparation requested October 16, 2026. This document describes proposed behavior for later backend wiring. The current deliverable is a local visual prototype; no authentication, permissions, invitation delivery, polling, leases, or persistent team data was implemented.

## Product rule agreed in this conversation

Sell to the firm owner. Give the owner an administration area visible only to owners. Owners invite workers by a specific email and see their own work plus work across the firm. An engineer sees their own studies and cannot browse coworkers' studies. Show who created the study and who last changed it. If someone is working on a study, show their identity and an appropriate read-only state. Live collaborative cursors and WebSockets are not required.

The first version should interpret “own studies” as studies created by that engineer. Assignment, shared ownership, and cross-engineer collaboration are additional product decisions, not implicit permission grants.

## Preview routes

- `#admin`: owner overview, member workload, pending invitations, and sample “Working now” status.
- `#team`: owner-only member list and email-specific invitation management.
- `#dashboard`: owner can switch Workspace studies / My studies and filter by creator; engineer sees only My studies.
- `#join`: invitation acceptance, wrong Google account, expired, revoked, and accepted states.
- Existing study views: editing availability, current editor, last-check status, read-only mode, and newer-revision state.
- `#settings`: owners see workspace configuration; engineers see their profile.

Use the Preview role selector to switch between Owner / Engineer. It is a design control, not a real role switcher or a security boundary.

## Existing source findings

`backEnd/basis/api/users.py` authorizes study access using organization membership and role ordering (`viewer < engineer < owner`). It distinguishes a missing/non-member study with 404 and insufficient role with 403. `backEnd/basis/db/repositories/study.py` confirms that get_visible and list_for_user scope by organization membership rather than study creator. The requested creator-only engineer access policy is additional work.

Study responses already include `org_id` and `created_by` in the checked OpenAPI snapshot. A display name for the creator, last-editor attribution, invite state, member workloads, and active editing sessions are presentation fixtures here, not verified existing response fields.

The backend is being worked on concurrently. Recheck its current source and regenerate OpenAPI before wiring. The design work should not change the vision implementation.

## Permission behavior to enforce later

Owners may list/read the firm's studies, including their own and team members' work. They may administer memberships and invitations. Whether they can approve all review gates should follow existing workflow role requirements and the final product policy.

Engineers may list/read and act on their own studies within a workspace where they have an active membership. A typical visibility predicate is organization membership plus `(owner OR study.created_by == current_user.id)`, subject to any explicitly approved assignment model.

Apply the policy to all related resources: study listing, counts, search, direct study URLs, source documents/photos, signed download URLs, evidence, decisions, history, exports, and presence records. Filtering an already downloaded all-team array in the frontend is not sufficient. Do not reveal other engineers' properties through aggregate counts, notifications, file names, thumbnails, or search results.

Keep original creator attribution immutable. Store last editor and meaningful actions separately. Removing a member should revoke access and active leases while preserving attributed historical records. Define how their studies are retained or reassigned. Protect the last active owner from removal/demotion.

## Invitation flow

1. Owner enters a work email and selects Engineer or Owner. Explain the exact scope next to the role.
2. Server verifies owner authority and creates a workspace-scoped, expiring, single-use invitation.
3. Email delivery is recorded distinctly from invitation creation. A pending row must not claim delivery success before it occurs.
4. Recipient signs in with the invited, verified email account. An unrelated Google account cannot accept it.
5. Acceptance is atomic, confirms current invitation status, creates the membership once, and records the event. Replays are idempotent.
6. Support duplicate invitations, existing members, expired/revoked links, wrong accounts, delivery failure, and owner cancellation. Revocation must invalidate the server token.

The prototype creates in-memory invitation rows only. No mail is sent. Identity/token verification, email normalization rules, membership creation, and last-owner protection belong in the backend.

## Editing presence without WebSockets

Use two separate ideas:

- A presence indicator says who last reported an editing session and when that report was checked.
- A server-enforced lease plus edit-version preconditions controls whether a write is accepted.

Polling can refresh a presence read model every few seconds while the study is open. Polling alone cannot prevent two browsers from saving over each other. Define a server-owned lease with an opaque session token, holder identity, expiry, and renewal. Acquire/renew/release must be atomic and permission-checked. Use server time; do not infer availability from the browser's clock or a missing heartbeat alone.

Only the lease holder should make protected edits. The server must reject mismatched or expired sessions even if the UI shows an enabled button. Use conditional writes / edit revisions to reject stale content. The existing workflow revision changes on review rejection; do not assume it also protects all draft edits. A separate content version may be needed.

When another person is editing, show their name and a last-checked time, then offer read-only viewing. A handoff request is optional future behavior. The preview does not allow silent takeover of a current session. If status is stale or unavailable, preserve local unsaved work and keep editing paused until a fresh server response confirms availability. Closing a tab is not proof of lease release; expiry and server validation handle crashes/disconnects.

UI scenarios to support: available, own active session, other active session, uncertain/stale status, newer revision, lease lost during a save, member revoked while editing, and duplicate tabs from the same person. Read-only viewing need not acquire an editing lease.

## Later backend work

- Membership listing/admin and owner authority checks.
- Invitation lifecycle and email delivery, tied to verified identity and the specific workspace.
- Creator-scoped study policies across every read/write/resource path.
- Attributed creator/editor/review history and owner aggregate views.
- Presence read model and atomic acquire/renew/release operations.
- Content-version checks and clear conflict/error responses.
- Regression coverage for tenant boundaries, coworker isolation, direct resource URLs, expired/replayed invites, revoked members, last-owner rules, two simultaneous editors, lease loss, stale writes, and status failure.

Endpoint names and payload fields should be agreed with the backend owner. This document does not freeze a new API contract.

## Standards informing the proposal

- [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html): least privilege, deny-by-default, and permission checks on every request.
- [RFC 9110, If-Match](https://www.rfc-editor.org/rfc/rfc9110.html#name-if-match): conditional requests to prevent lost updates.
- [Apple materials](https://developer.apple.com/design/human-interface-guidelines/materials): the visual treatment keeps translucent controls separate from content surfaces.


## Draft preservation handoff

The local design preview now demonstrates unsaved-change protection and browser-only draft storage. Production needs a study- and user-scoped draft read/write API with the current revision and editing-session checks, a saved-at receipt, and surfaced save failures. Keep draft persistence separate from gate approval. Persist selected document registrations/upload completion through the existing document flow; browser file bytes are not draft JSON. The router must guard links and history navigation while retaining the pending destination; reload/close uses the native dirty-only warning. Replace the prototype role-keyed localStorage fixture with authenticated server data.

## Owner company home, personal work, and activity (October 16)

The owner is the company administrator. Their default Home must be a company overview: counts of active/completed/total studies, review and attention workload, attributed work by team member, and team management. A separate My work view lets the owner do their own studies and assigned reviews with the same workbench used by engineers. Engineers do not receive company-wide statistics or administration. In the prototype, My studies means creator-owned; explicitly assigned reviews also appear in the personal review queue. These scopes should be named and agreed in the backend contract.

Active currently means draft, processing, waiting_review, or stalled. Archived and cancelled studies are excluded from active totals; completed means a finished schedule. Production aggregates must be computed over authorized company data on the server, not over a single paginated response. Provide totals and per-person workload independently of the current list/search filter, with an agreed treatment of archived/cancelled records and time ranges. The sample UI has no trends or invented performance claims.

Personal review prompts must target studies created by or explicitly assigned to the signed-in person and parked at a review gate. Owner visibility alone does not make every team review a personal task. Authorization and assignments must be enforced for list/detail/document/history reads and all writes. Client-side fixture filtering is not an access-control implementation.

The study library demonstrates search by property/address/creator, stage/status, creator, sorting, and 10/25/50-row pagination using 6- or 240-study fixtures. Production needs stable server pagination and authorized result counts. The Activity screen and per-study history action demonstrate actor, timestamp, revision, uploads, review events, and expandable before/after field changes. Production needs an append-only attributed event read model, resource scoping, pagination, and stable identifiers. The illustrated event stream is not generated by real persisted edits. All review/detail links still open the existing reference-study screens, not a fully bound multi-study application.

Workspace identity beneath the Basis brand/toggle has two lines: the company name above, and Enterprise workspace below. Harbor Engineering is the existing illustrative company placeholder; the compact mark uses HE. The footer contains the person's name and role. README.md identifies both CSSI and CBIZ as pilot firms; official unmodified wordmarks were saved to assets/cssi-logo.svg and assets/cbiz-logo.svg for comparison. Neither firm has been selected as the default tenant. References: https://cssiservices.com/ and https://www.cbiz.com/ .

## Account menu

The avatar/name control opens a glass account menu containing the current preview identity and Log out; it no longer navigates to workspace settings. The gear remains the preferences entry. The menu follows the WAI menu-button pattern: native button, aria-haspopup/expanded, focus on the menu item, Enter/Space/arrow opening, Escape restoration, Tab and outside-click dismissal. It is positioned outside the sidebar scroll container, clamped to the viewport, and does not rebuild study forms or trigger the leave guard merely by opening.

Log out currently navigates to the sign-in preview through the existing draft navigation guard. It does not end a Firebase or backend session. Production must call the real auth sign-out operation after the save/discard decision, report sign-out failures, then clear user-scoped cached data and editing presence as appropriate. Never treat opening the account menu as leaving a study.

Browser checks: owner/engineer identity, click/Enter/Space/arrow opening, Escape focus return, Tab and outside-click dismissal, unchanged route on opening, separate gear navigation, clean sign-out to the sign-in preview, dirty sign-out presenting Save/Discard/Keep editing, Keep editing preserving the property name, and Discard continuing to sign-in. The menu stayed inside a measured 487px viewport with no page overflow; no console errors were reported. Reference: https://www.w3.org/WAI/ARIA/apg/patterns/menu-button/ .

## Unified Settings page (latest)

Settings is now a normal page at #settings, with personal Profile and owner-only company sections for Workspace and Cost sources, plus a Team & invitations link. The gear remains outside the account menu and is labeled Settings for both roles. Profile settings in the account menu opens the same page with Profile selected, through the existing study navigation guard. It no longer opens a dialog. Company settings and personal profile fields remain separate sections; an engineer receives no company sections.

Profile controls stage name/photo changes until Save changes. Cancel restores the saved profile. Staged profile edits survive section and page navigation in this tab; unsaved profile edits are not persisted across a reload. Saved preview profiles are user-keyed in localStorage; names/photos do not update real account, company, team, or historical audit records. Production needs authenticated profile read/write and avatar storage, surfaced save failures, and agreed handling of unsaved profile edits on reload. Profile photo selection is local and performs no upload.

Browser verified: both Settings entry points reach the full page, no profile dialog, owner/engineer section visibility, company/profile fields separated, staged name preserved across sections, Cancel restoring values, blank-name validation, saving and reload persistence, updated footer identity, and a measured 390px layout with no horizontal overflow. All 20 node tests passed. Four profile tests cover user isolation/invalid stored JSON, file-type/size rejection, center-crop coordinates/URL cleanup, unreadable images, and dimension limits using mocked image/canvas APIs. The earlier native file-picker test was interrupted; real browser photo selection/conversion/removal has not been verified end to end. References: https://www.w3.org/WAI/ARIA/apg/patterns/menu-button/ and https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/input/file .

## Engineer-first design pass and study Files (October 17)

The prototype now starts in Engineer mode. The Basis sidebar styling, mounted width transitions, hover preview, pinning, and account mechanics are retained. This pass concentrates on the engineer workflow; it does not add owner management work.

The dashboard removes its duplicate At a glance panel for engineers. A creator-owned next review says Created by you; Assigned to you remains available for explicitly assigned work. Each study row now has Files and Activity entries. The dedicated Files page groups appraisal, property photos, and supporting documents, supports local fixture search and photo previews, and links to sample-image downloads. Harbor House has five sample photos and one appraisal placeholder. Lakewood is an unstarted draft with no file records. Upload/progress/history fixture counts were aligned to those five photos; an unstarted draft no longer invents upload activity. All other study review/detail screens remain the Harbor House reference fixture.

Files is presentation only. Add files explains the intended upload entry without sending data. No blob store, tenant file listing API, real original PDF, bulk ZIP service, or document registration was connected. The sample PDF has no download bytes and its download control is disabled. Existing image files support actual browser download links; download completion receipts were not tested. Production will need study-scoped material listing, original-file preview/download access, upload and failure states, category metadata, and authorization across every file/resource. Keep retrieval of an original file separate from running analysis. Pending/failed uploads should remain visible and recoverable. A finished study must retain its material library.

Appraisal review uses solid working surfaces, larger dark field text, a selected-row marker, a distinct document canvas, compact review progress, and a decision footer. The source document is explicitly illustrative. Source-page changes, corrections, and the approve-with-corrections restriction were verified. Presence/handoff uses a compact readable strip; the design-only editing selector moved into Scenarios in the prototype toolbar. Read-only approval/edit controls remain disabled with legible text. The existing handoff preview opens without sending a notification. Draft state lives in the study header instead of a separate full-width card, and dirty navigation to Files still offers Save/Discard/Keep editing.

The engineer walkthrough at engineer-walkthrough.html contains 13 current screens, takeoff tab variants, an expanded change log, narrow-screen examples, and an appraisal previous-state/read-only comparison. Every page has a short Keep and Next improvement note. Shared contrast changes do not mean every page has received a full layout redesign; the guide marks those remaining proposals explicitly. No backend or production frontend files changed.

Browser verification: all 13 engineer screens rendered without broken images or horizontal page overflow at a measured 1416px viewport. New review and Files views were also checked at an actual 487px viewport; the Files header control needed a narrow-layout visibility correction and was rechecked. Files categories returned five photos, kitchen search returned two, photo preview retained the expected local image/download name, the empty supporting-documents view rendered, and Lakewood Files stayed empty. The dashboard showed no duplicate summary and creator wording was correct. Request handoff remained enabled while approval was disabled; a correction showed in its field and blocked approval. Dirty navigation to Files invoked the existing draft prompt. No browser console errors were reported. All 21 node tests passed, including the new draft-history regression. Narrow screens beyond these views were not exhaustively tested.

Computed appraisal color spot checks: selected field label 6.48:1, selected field value 11.37:1, hovered field label 6.57:1, main heading 11.87:1, available-editing status 8.25:1. These sampled solid-surface ratios are not a full accessibility audit. References informing this pass: https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html , https://www.bluebeam.com/product/markups-and-data/ , https://www.procore.com/fc/takeoff .

## Company archive, client identity and unified intake (October 18)

The latest product direction separates Home (personal work and explicitly assigned reviews) from Library (the entire company's cost-seg history, across engineers, including completed studies). This supersedes the earlier personal-only Library concept; it does not change Home visibility. The new archive record route keeps the chosen property, client, creator and completion date, with Files, Report and Activity views. Another engineer's archive record is read-only. An older completed sample is dated September 2025 so historical dates include the year. The final-report entry is explicitly a placeholder: no original report PDF or real schedule download was connected. Only Harbor House includes original sample-image assets and an appraisal placeholder; the other properties do not invent stored file bytes.

Client names are shown in the study list, next-review card and upload form, and are searchable. The main study area now uses neutral working surfaces within the existing sage frame, stronger text hierarchy and labelled row actions. Sidebar styling and hover/pin mechanics remain intact; Library is the added navigation entry. Library search supports property name, client, street address, creator/last editor and filename; filename matches also locate the parent study. Library has Studies/Files views, status/file-type/study filters and pagination. The fixture-only companyArchive option is presentation scope, never production authorization. The backend must provide a tenant-scoped archive read capability separately from edit/decision capabilities. No cross-company catalog is proposed.

Start a study now has ONE mixed-file input/drop target. Images suggest Property photos; PDFs named as appraisals suggest Appraisal; other documents suggest Supporting documents. Group suggestions are editable and filterable. This is deterministic file-extension/name grouping, not semantic document analysis. Document-content classification, scanned documents, authenticated upload registration, persisted file bytes, failures/retries and blob storage remain backend work. Local selection supports multi-file input, duplicate detection, category corrections, removal, image previews and invalid/empty-file feedback. Form/file metadata participates in local draft protection; saved drafts retain names/groups, and files must be reselected after reopening. The native file chooser was verified with one example PDF, one CSV and two photos in a single selection. Drop handlers use the same selection path and are covered with simulated drag events; an OS Finder-to-browser drag was not separately exercised.

Verification: 29 Node tests passed. Browser checks covered client search, creator and street-address search, file-name search locating its study, mixed selection/category corrections/photo filtering, loaded photo previews, dirty navigation, the company-wide six-study Engineer view versus personal Home, 240-study pagination and a 40-completed-study filter, read-only completed archive/report placeholder/history, and actual 389px layouts without page overflow. No browser console errors were reported. Backend APIs, authentication, Firestore and the production Next app were not modified. The screenshot guide now contains 15 screens; older study-layout screenshots remain from October 17, as marked in its introduction.

References reviewed for this pass: https://developer.mozilla.org/en-US/docs/Web/API/HTML_Drag_and_Drop_API/File_drag_and_drop and https://www.carbondesignsystem.com/building-blocks/core/components/data-table/guidelines .

## Study hierarchy and real PDF previews (October 18 follow-up)

Library is now a single company-wide study list. Its separate Studies/Files browsing switch is removed. Filename search locates the parent study, and each row's Files action opens that study record's Files section. Reports and Activity remain within the same property context. Home keeps its prior personal scope.

Study file cards now show a rendered first-page PDF thumbnail and open a local PDF.js 5.6.205 reader. The reader supports previous/next, a bounded page-number input (Enter or blur), zoom, Fit width and a download link. Rendering cancellation handles rapid page changes; closing the modal cancels work and destroys the loading task/worker. The source-PDF entry in appraisal review starts on the currently selected field's source page. PDFs selected in the unified intake have a Preview action and use the same reader via their local object URL. No file is sent to a server. Unreadable/password-protected documents receive explicit feedback and retain a download option.

The viewer demonstration uses output/pdf/harbor-house-sample.pdf, an eight-page generated fixture clearly marked as fictional and not an appraisal. It matches the design fixture's source-page references and values. Its generator is scripts/make-preview-pdf.py; its first-page thumbnail is assets/harbor-house-sample-page-1.png. All eight pages were rendered with Poppler and visually inspected. This sample replaces the previous placeholder PDF bytes; it does not supply a real original appraisal, a final report, or backend storage. PDF.js rendering/worker/fonts/CMaps/WASM and Apache license are bundled locally under assets/vendor/pdfjs; no runtime CDN is used. Production integration should use the actual original document URL or bytes, with the same viewer.

Browser verified: thumbnail loaded; the sample rendered as eight pages; first/last bounds, Enter page changes, rapid previous-page clicks and zoom worked; selected Year built opened source page 3 with the expected value; a locally selected one-page PDF rendered; malformed PDF selection produced readable error feedback; close/reopen worked; and the viewer fit an actual 389px screen without page overflow. Filename search still returned Harbor House and its Files shortcut opened Harbor House with six file cards. All 29 existing regression tests passed. The screenshot gallery and ZIP include the viewer and the revised study hierarchy.

Renderer reference: https://mozilla.github.io/pdf.js/examples/ .

## Library visual polish (October 18)

The Library now uses library.css for one coordinated surface/control/type treatment: a prominent search field, counted status filters, custom sort/page-size menus, semantic study table, and compact responsive study cards. Client identity stays visible. Library rows have one View study action; the nested study record retains Files, Report and Activity. Repeated instructional copy and native select controls were removed from the Library body. Sidebar styling and mechanics were not redesigned.

Custom menus follow the WAI APG menu-button interaction pattern (https://www.w3.org/WAI/ARIA/apg/patterns/menu-button/): keyboard opening, arrows/Home/End, selection, Escape with focus restoration, Tab and outside-click dismissal. Menu Escape is stopped before the existing sidebar Escape handler, so closing a menu preserves a pinned sidebar. Cmd/Ctrl K focuses search.

Verified in browser: searches by person, client, address and PDF filename return the intended parent studies; no-result clearing; completed status; alphabetical sorting; menu keyboard navigation/focus restoration; 240-study pagination with 25 per page and page/search reset; Harbor study opens with its files and PDF page 1 renders; actual 389px screen has no document overflow and its sort menu fits. Final desktop, mobile, menu and previous-state captures are in screenshots/engineer. No console errors were captured. All 29 existing Node regression tests passed; app.js and library-review.js pass node --check. This is still a local design prototype; no backend or production frontend changes.

## Engineer-wide product polish and address lookup (October 18)

product-polish.css now supplies a common control/surface/type treatment across the engineer pages, while preserving the approved sidebar rules. The upload area, appraisal workbench, photo groups and viewer, asset register/takeoff, schedule, files, activity and settings have page-specific layout refinements. The Library retains its existing composition. Working values use opaque surfaces, with glass restricted to chrome, menus and dialogs. Main review text/control sizes and contrast were increased. The shared primary-button light stop was darkened from #60836a to #52775b: white text against that stop measures 5.06:1; this is a sampled solid-color check, not a complete accessibility audit.

product-controls.js progressively enhances single-choice selects in the main work area and dialogs. The original native value and change event remain authoritative, preserving existing draft/filter handlers. It supports pointer selection, arrow/Home/End navigation, Enter/Space, Escape, Tab, open-list typeahead, outside dismissal, disabled state, focus restoration and viewport positioning. Original preview toolbar controls remain native. Choice popovers stop Escape before the sidebar handler. The current study step and editor tab scroll into view on narrow screens. Existing study-workspace container naming was preserved after mobile verification caught a Home card-layout regression.

The save-before-leaving dialog initially inherited a zero-padding shared rule, which caused content/actions to touch the edges. That rule was corrected, the dialog was reduced to 480px with inset content, a compact brand pill, a lighter backdrop and a clear three-action hierarchy. Desktop/mobile screenshots show the corrected result. Keep/Escape retains edits; Discard restores the saved state.

Address lookup: property-address is a free-entry combobox alongside property/client identity. address-lookup.js matches six local fictional fixture addresses by street, city or ZIP, shows street plus city/state/ZIP, accepts pointer or keyboard selection, and permits any manual address. The popup explicitly labels suggestions as samples. It does not geocode, validate delivery addresses or contact a provider. propertyAddress is a local draft string, with backward-compatible restoration for old drafts; no backend schema is introduced. Address text updates unsaved status and participates in save/restore/discard. A real provider and structured address contract remain integration work.

Verified: all 15 engineer desktop views rendered at actual 1626px without broken images or document overflow. All 14 remaining engineer views rendered at actual 389px without document overflow; the Library had its own earlier mobile pass. Address and choice popovers and the draft dialog fit the narrow viewport. Browser checks covered address keyboard/pointer selection, unknown/manual entry, save/reload and discard; mixed PDF/photo selection and manual file grouping; room selection and draft guard; activity filters and expanded before/after changes; takeoff units retained across tabs and discard; selected photo outlines, keep/remove/restore and hide/show; disabled appraisal approval with enabled Request handoff; PDF source page 3 rendering; and nine processing/exception states. Owner dashboard/team/settings and public login/landing received desktop regression smoke checks, not a full new page-design pass. No browser console errors were captured.

All 32 Node tests pass, including new address-query and draft-compatibility checks. Desktop/mobile measurements are in desktop-polish-checks.json and mobile-polish-checks.json. The guide and ZIP now include current viewport/full-page captures for all engineer pages plus address, file intake, read-only and corrected-dialog states. No backend or production frontend code was changed.

Pattern references: https://www.w3.org/WAI/ARIA/apg/patterns/combobox/ and https://www.w3.org/WAI/ARIA/apg/patterns/tabs/ .

## Room grouping and photo-review repair (October 18)

Design-only scope confirmed: Claude Code handles integration. room-model.js groups every source photo exactly once; photo-review.js restores separate collapsible room sections, refreshes both groups after a move, and retains stable original photo indices. Default room assignments do not create redundant overrides. Two room-model tests were added.

The component workbench now has full-card selection targets, independent Keep/Remove/Restore buttons, room switching, bounded Previous/Next controls, a current-room filmstrip, and Back to rooms. The selected SVG outline is drawn last and its label follows its actual geometry. Only Kitchen / 01 has fixture annotations; other photos show their original image and an explicit unavailable-annotations state. No fake masks or per-photo object schema were introduced.

Every fixture photo opens in a dedicated near-full-screen viewer instead of the small confirmation modal. It includes study-photo thumbnails, bounded navigation, keyboard arrows, 100–300% zoom, Fit and original-download links. Dialog behavior follows WAI APG (https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/): inert background, focus containment, visible Close, Escape, and focus restoration. Original file previews use the same photo viewer.

Verified in the browser: room collapse/expand; photo reassignment changes both group counts and restoring it returns the original grouping; clicking the blank/card area beside Pendant lights selects it; independent remove/restore; Kitchen / 02 navigation with no borrowed masks; switching to Exterior and back; last-photo bounds; zoom/Fit; Escape restores focus and clears inert/background scroll lock; desktop and actual 437px narrow layouts have no document overflow or broken images. Desktop screenshots and the guide were refreshed. Narrow screenshot capture has extra canvas/scroll-offset artifacts from the browser viewport override; DOM geometry checks confirm the dialog fits the viewport. No browser console errors were captured. All 34 Node tests passed. Backend and production frontend remain untouched.

## Inline component editing (October 18)

Each component has an explicit pencil + Edit action beside Keep/Remove. Editing opens labelled Label, Count and Material fields inside that component row, beside its photo, with Save changes and Cancel. Fields use opaque working surfaces and the existing sage selection treatment. Corrections update the list, count and selected outline label, retain original model confidence, and show an Edited marker. They do not change segmentation paths.

The editor validates a nonempty label, positive safe whole-number count, and bounded label/material text. Cancel restores the previously applied values; returning to original values removes the correction marker. Local study drafts include both applied corrections and unfinished editor input, with backward-compatible restoration of existing drafts. Opening an unchanged editor does not create unsaved changes. Existing simulated read-only states disable Edit/Save. Production APIs and schemas remain untouched.

Browser verified: open editor/focus; zero-count error; cancellation clears temporary changes; changed label/count/material update the list and outline label; study Save draft and reload retain changes; restoring original values removes Edited; unfinished input survives next/previous photo and Save draft/reload; all four Edit buttons are disabled when someone else is editing. Actual 437px editor fields fit with no horizontal document/editor overflow. All 38 Node tests passed, including correction validation, source reset and draft compatibility/persistence. Design reference reviewed: https://design-system.service.gov.uk/patterns/check-answers/ for explicit per-item change actions. Screenshot: screenshots/engineer/component-edit.jpg.
