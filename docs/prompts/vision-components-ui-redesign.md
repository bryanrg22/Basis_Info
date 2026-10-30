# Prompt: redesign and test the Basis vision components page

You are working on Basis, a cost segregation product for engineering firms. Repo: `bryanrg22/Basis_Info`. The frontend is plain HTML, CSS and JavaScript in `web/` (no framework, no build step). `web/live/` is the live mode that talks to the real API (`?mode=live`). Mock mode is the default and must keep working. Read `web/README.md`, `web/DESIGN_NOTES.md` and `web/TEAM_HANDOFF.md` before changing anything. Do not edit `design-review/` or `frontend/`.

## Who uses this page

Cost segregation engineers and their staff. They are not software people. They open a property's photos, check what the system found in each photo (windows, light fixtures, cabinets, outlets, doors and so on), fix what is wrong, and approve the room so the costs and IRS classes can be built. The page has to make that fast and obvious. It must feel like a finished, expensive professional tool (think Linear, Figma, Stripe Dashboard, Apple Photos), not an MVP.

## The page

Route `#viewer/<studyId>/<photoId>` in live mode (`web/live/live-review.js`, `web/live/mask-layer.js`, CSS in `web/live/live.css` plus the shared design CSS). It shows one photo with object outlines (SAM 3 masks, run-length encoded, decoded in `web/live/rle.js`), numbered pins, and a right-hand list of component types with counts.

## What is wrong today (found by the product owner, all must be fixed)

1. Once a component type is selected (e.g. Kitchen cabinet) there is no way to deselect it and get back to the list of all types.
2. Pins that sit close together (4 and 5, 6 and 8) overlap and cannot be clicked; hovering a pin and clicking it does nothing.
3. Clicking an object on the photo (e.g. the sink) makes the page jump, and the matching row in the side list is not highlighted.
4. Clicking a component type in the side list scrolls the page down.
5. The list shows "Score 0.74" style numbers. Customers must not see model scores or percentages of the photo. Keep the data in the API and the page's data model (it is kept for later model fine-tuning), just do not show it. An engineer-facing word like "Needs a look" for low-confidence items is fine if it helps them.
6. The footer "SAM 3 · 38 counted instances · model 1bf97763" must go. No model names or ids anywhere a customer sees.
7. The "Show all" button looks broken (its border and focus state). Fix the control set.
8. Switching between photos of the room is tiny thumbnails at the very bottom with faint overlapping file names. Replace it with a clear filmstrip or previous/next with the photo count, keyboard arrows, and the room's photos visible without scrolling.
9. At 100% browser zoom on a normal laptop screen half the photo is cut off and the side list is taller than the screen. The photo must fit the available space, with zoom and pan (scroll or pinch, buttons, double-click to zoom, "fit" to reset), and the side panel scrolls on its own, never the whole page.
10. There is no way to act on a single object: mark it wrong, change its type, delete it, or confirm it. There is no way to add an object under a type, or add a new component type to the photo.
11. Outlines can be wrong in ways an engineer must be able to fix: one outline covering two cabinets, two outlines on the same cabinet (in the kitchen photo cabinet 6 covers what 7 and 8 also cover), a countertop outline that also covers the sink.
12. When a study is past the review stage the page is read-only; say so once, calmly, and hide the edit controls rather than showing disabled ones everywhere.

## The add and fix experience (design this carefully; it is the core of the page)

* The engineer picks the component type first (from the list, or "Add a component" for a type not in the photo yet), then drags a box around the object on the photo. The system returns an outline (the backend asks SAM 3 with that box). The engineer sees the outline as a preview and confirms, or adjusts.
* If the outline is wrong or missing (the model did not understand), the engineer can refine it, and if that still fails, keep a plain box or draw the shape by hand (polygon). The object still counts. Make the "it did not work, keep my box" path feel normal, not like an error.
* Clicking on a place where several outlines overlap must let the engineer cycle through them (the outline under the pointer highlights, click again or use a key to go to the next) and pick the right one.
* If what they add overlaps something already counted, ask: "Use the existing one" or "Add anyway".
* Selecting an object on the photo selects the same row in the list and the other way round, with no page jumps.
* Changes to counts must be visible at room level (the room's inventory count for that type), because that count is what gets priced. Show the suggested room count when the engineer's additions raise it, and make it editable.
* Keyboard: Escape cancels a draw, Delete removes the selected manual object, arrows switch photos, number or letter shortcuts are a plus. Everything must also work with a mouse alone and on a tablet.

Study how professional tools do this before designing: CVAT (AI tools, interactive segmentation), Label Studio (SAM integration), Roboflow Annotate (Smart Polygon, SAM 3 Smart Select), V7 Darwin, Encord, Segments.ai, and the Segment Anything demo. Also look at how Figma and Apple Photos handle a zoomable canvas with an inspector panel. Take the interaction patterns, not the visual style.

## The backend you can use

Start from `main`. The API contract is `docs/api/openapi.json`; read it and do not invent endpoints. `web/live/` already wires a first version of the add flow (read it, then redesign it):

* `GET /v1/studies/{id}/photos/{document_id}/objects`: every object on the photo, counted ones and uncounted candidates (`counted`), with `box`, `mask` (run-length encoded), `source` (`model` or `manual`), `score` (keep it in data, never show it), and for manual objects where the outline came from (`candidate`, `sam_box_prompt` or `none`).
* `POST .../objects` (only while the study waits at the rooms review), one of: `{kind: "box", label, box}`: matches a stored candidate, or else the server asks SAM 3 with that box and stores the outline, or else keeps the plain box; `{kind: "point", label, point}`: picks the stored candidate under the tap (422 when there is none: tell the engineer to draw a box); `{kind: "candidate", candidate_id}`: promotes the exact candidate the engineer chose while cycling. Any of them with `allow_duplicate: true`. The answer has `object`, `created`, and when it overlaps an already counted object `duplicate_of` and `overlap` (show "Use the existing one" / "Add anyway"). A box call can take a second or two: show progress on the box.
* `DELETE .../objects/{object_id}`: manual objects only.
* `GET /v1/studies/{id}/rooms`: each room's inventory plus `suggested_counts` (`label`, `inventory_count`, `photo_count`, `manual_count`, `suggested`).
* `POST /v1/studies/{id}/decisions`: approve, or reject with corrections. Room count changes are corrections with `entity_type: "component"` and `after: {count}`; adding a component a room is missing is `entity_type: "room_component"` with label, material, attachment and count. They go with "Request changes" (a rejection), which produces the next revision; approving with corrections is refused.
* `GET .../documents/{document_id}/content`: the photo bytes.

Where the UI needs something the API does not offer yet (for example refining an outline with extra clicks, splitting or merging two outlines, deleting a model-found object, changing an object's type), design and build the UI, but in live mode show it as not available yet, and list exactly what API it needs in `web/README.md`. Never fake persistence in live mode.

## Design direction

* Keep the existing design language: the sage and green palette, the glass surfaces, the Basis logo and the collapsible left sidebar (it is liked; refine it, do not replace it). Make it feel calmer, more precise and more expensive: fewer boxes inside boxes, consistent spacing on an 8 px grid, a clear type scale, one accent color used sparingly, real hierarchy, subtle motion (150 to 250 ms, no bounce), clear hover, focus and selected states.
* Use design tokens (CSS custom properties) for color, spacing, radius, shadow and type, and use them everywhere you touch.
* Copy is plain, short and specific. No marketing lines, no quotes, no emoji, no exclamation marks, no filler like "Seamlessly". Labels say what a thing is; buttons say what they do.
* Accessibility: WCAG 2.2 AA contrast, visible focus, every action reachable by keyboard, hit targets at least 32 px (pins included: separate overlapping pins, or show a picker for a cluster), `aria` labels on icon buttons, respects `prefers-reduced-motion`.
* Layouts to check: 1280×800, 1440×900, 1920×1080, iPad (1024×768 both ways) and a 390 px phone (view and approve only is fine on a phone; say so).

Research references for the visual quality bar: Refactoring UI (Wathan and Schoger), the Linear, Vercel and Stripe dashboards, Apple's Human Interface Guidelines for direct manipulation and inspectors, Nielsen Norman Group's heuristics. Write down in `web/DESIGN_NOTES.md` what you took from each and why, briefly.

## Test it like a real user, then prove it

1. Run it locally as `web/README.md` describes, in mock mode and in live mode against the local stack.
2. Use a real browser with automation (Playwright or equivalent) and do every interaction by hand, not just look: select and deselect a type, click every pin including overlapping ones, click objects on the photo, cycle overlapping outlines, zoom and pan, switch photos with buttons and keys, draw a box to add an object, take the "keep my box" path, trigger the duplicate prompt, change a room count, delete a manual object, and check the read-only state. Check nothing scrolls the page unexpectedly and the selection stays in sync both ways.
3. Screenshots of every state at the sizes above. Compare before and after for each numbered problem in this prompt and say how it was fixed.
4. No console errors, `node --test web/tests/*.test.cjs` passes, add tests for the new view logic (selection sync, pin clustering, overlap cycling, fit-to-screen math).
5. Do not commit client photos or anything from `~/basis-eval-data`; screenshots of real client photos stay out of the repo.

## Deliver

A branch with focused commits and a short write-up: what changed for each numbered problem, the add-and-fix flow with screenshots, what is available in live mode versus waiting on the API (with the exact API each needs), and what you could not verify.
