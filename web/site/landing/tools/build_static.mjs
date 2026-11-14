// Builds the landing site as a self-contained static folder for hosting (Vercel or any static
// host): the three pages at the root, their scripts and data under src/, and every asset they
// load under assets/. In the repository the pages reach two asset folders, web/assets
// (../../assets) and web/site/assets (../assets); at a site root both resolve to /assets, so the
// build merges the two (their file names do not collide; the build stops if they ever do).
//
//   node web/site/landing/tools/build_static.mjs [outDir]     (default: web/site/landing/dist)
//
// The room photos (web/assets/*.webp and thumbs/) are not in the repository (see .gitignore and
// web/README.md); the build copies them when they are present locally and says so when not.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const landing = path.resolve(here, '..');
const web = path.resolve(landing, '../..');
const out = path.resolve(process.argv[2] || path.join(landing, 'dist'));

const PAGES = ['index.html', 'signin.html', 'demo.html'];
// Every asset the pages load, by source folder. Keep in step with the pages (README.md, "Checking
// a build", shows how to confirm nothing is missing).
const FROM_WEB = ['basis-logo.png', 'basis-mark.png', 'inter-latin.woff2'];
const PHOTOS = ['kitchen.webp', 'kitchen-detail.webp', 'living.webp', 'bedroom.webp', 'exterior.webp',
  'thumbs/kitchen.webp', 'thumbs/kitchen-detail.webp', 'thumbs/living.webp', 'thumbs/bedroom.webp', 'thumbs/exterior.webp'];
const FROM_SITE = ['bryan-96.webp', 'brands/chatgpt.svg', 'brands/claude.svg', 'brands/color/google.svg', 'brands/color/nvidia.svg',
  'brands/color/openai.svg', 'brands/color/usc.svg', 'fonts/ibm-plex-mono-latin-400-normal.woff2', 'fonts/ibm-plex-mono-latin-500-normal.woff2',
  'fonts/newsreader-latin-opsz-400-500.woff2', 'fonts/OFL-ibm-plex-mono.txt', 'fonts/OFL-newsreader.txt', 'partners/cssi.svg',
  'partners/gtg-mark.webp', 'partners/mcguire-sponsel.png', 'partners/source-advisors.webp', 'vendor/lenis-1.3.26.mjs'];

const copy = (from, to) => { fs.mkdirSync(path.dirname(to), { recursive: true }); fs.cpSync(from, to, { recursive: true }); };
const placed = new Map();
function asset(srcRoot, rel) {
  const from = path.join(srcRoot, rel);
  if (!fs.existsSync(from)) return false;
  if (placed.has(rel)) throw new Error(`assets/${rel} would come from both ${placed.get(rel)} and ${srcRoot}`);
  placed.set(rel, srcRoot);
  copy(from, path.join(out, 'assets', rel));
  return true;
}

fs.rmSync(out, { recursive: true, force: true });
for (const page of PAGES) copy(path.join(landing, page), path.join(out, page));
copy(path.join(landing, 'src'), path.join(out, 'src'));
for (const f of FROM_WEB) if (!asset(path.join(web, 'assets'), f)) throw new Error(`missing web/assets/${f}`);
for (const f of FROM_SITE) if (!asset(path.join(web, 'site', 'assets'), f)) throw new Error(`missing web/site/assets/${f}`);
const missing = PHOTOS.filter((f) => !asset(path.join(web, 'assets'), f));
copy(path.join(landing, 'vercel.json'), path.join(out, 'vercel.json'));

console.log(`built ${out}: ${PAGES.length} pages, ${placed.size} assets`);
if (missing.length) console.log(`room photos not found locally, left out: ${missing.join(', ')}`);
