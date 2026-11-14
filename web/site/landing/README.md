# Landing site

The public landing page and its two entry pages, as plain HTML, CSS and JavaScript modules with
no build step.

| Page | File | What it is |
| --- | --- | --- |
| Landing | `index.html` | The product story, section by section, each with a working figure |
| Sign in | `signin.html` | The public sign-in page. Its buttons do not sign anyone in yet; they point visitors to the demo page. The team's real sign-in is the app (`web/index.html?mode=live#login`), which no public page links to |
| Book a demo | `demo.html` | The demo request form; it posts to the Basis API (`POST /v1/demo-requests`, `docs/api/DEMO_REQUESTS.md`) at the address in the page's `basis-api` meta tag |

## Layout

```
index.html, signin.html, demo.html
src/
  paper.css      the landing page's styles, and the base the entry pages build on
  entry.css      the sign-in and demo pages
  paper.js       the landing page's entry module (imports the figures below)
  entry.js       the entry pages' forms (imports terminal.js and overview.js)
  record.js, rooms.js, kitchen.js, trust.js, sign.js, reveal.js, connect.js, scroll.js
                 one module per figure or behaviour; each file's header says what it shows
  terminal.js, overview.js
                 the sign-in terminal and the demo page's study overview
  harbor.js      the Harbor House sample's room photos and inventories
  data/          what the figures draw, written by the tools below (never typed by hand)
tools/           the scripts that write src/data/, and the static build
vercel.json      headers and clean URLs for hosting
```

Every figure uses the synthetic Harbor House sample study (`web/live/sample-api.js`); no client
data appears anywhere on these pages.

### Styles: desktop, tablet and phone

Each stylesheet is organised by component, in page order. A component's base (desktop) rules come
first and its responsive rules (`@media`) sit directly beneath them, so a component's desktop,
tablet and phone styles are read and changed in one place rather than split across a separate
mobile file. The breakpoints are listed at the top of `paper.css`: 980px (tablets), 800px (the
header folds into the menu), 700px (phones) and 560px (small phones).

Where a desktop figure depends on side-by-side layout, the phone version is redesigned rather than
stacked: the hero's sources become one card with the three numbers as tabs and one source shown at
a time (tapped, swiped, or taking turns until the reader chooses), and the record shows its finished
rows at once (the group chat story plays on desktop only).

## Running it locally

From the repository root:

```sh
python3 web/serve.py --port 3100
```

Then open `http://127.0.0.1:3100/site/landing/`. The figures that show room photos need the five
sample photos and their thumbnails in `web/assets/` (`kitchen.webp`, `kitchen-detail.webp`,
`living.webp`, `bedroom.webp`, `exterior.webp`, and `thumbs/`). They are not in git (listing-style
images of unknown licence); `web/README.md` says where to copy them from.

## Regenerating the figures' data

| Data | Command | Source |
| --- | --- | --- |
| `terminal.json`, `overview.json` | `node web/site/landing/tools/terminal_data.cjs` | the sample engine's priced schedule and result |
| `kitchen.json`, `living.json`, `bedroom.json`, `exterior.json` | `tools/localize_photos.py` (header has the command) | the product's photo localizer; a paid call per photo |
| `room-masks.json` | `python3 web/site/landing/tools/room_masks.py` | the localizer output above |

`tools/crop_check.py` checks that the kitchen figure's labels sit on empty wall or floor.

## Building for hosting

```sh
node web/site/landing/tools/build_static.mjs [outDir]
```

writes a self-contained folder (default `web/site/landing/dist/`, not tracked): the three pages at
the root, `src/`, every asset the pages load under `assets/`, and `vercel.json`. Deploy that folder
to any static host; with Vercel, `vercel deploy <outDir>`. Before a public deploy, set the demo
page's `basis-api` meta tag to the deployed API, and add the site's origin to the API's
`DEMO_REQUEST_ALLOWED_ORIGINS`.

### Checking a build

Serve the folder (`python3 -m http.server -d <outDir> 8790`) and load each page at desktop and
phone widths with the browser's network panel open: no request should fail. A page that adds an
asset must add it to the lists at the top of `tools/build_static.mjs`.
