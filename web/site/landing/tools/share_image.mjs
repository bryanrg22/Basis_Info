// Renders the link preview's image and the touch icon from tools/share_image.html:
//   web/site/assets/share/card.png             2400 x 1260 (1200 x 630 at 2x): og:image, twitter:image
//   web/site/assets/share/apple-touch-icon.png  360 x 360 (180 x 180 at 2x): the icon iOS and Messages use
//
//   node web/site/landing/tools/share_image.mjs
//
// Needs Playwright with its Chromium (npm i --no-save playwright && npx playwright install chromium), or
// BASIS_PLAYWRIGHT set to a Playwright package's index.mjs. The logo is share_logo.svg: the PNG logo
// (web/assets/basis-logo.png) traced at 6x with potrace, the circle replaced by an exact one (centre and
// radius measured from the PNG); share_mark.svg is the same drawing cropped to the circle.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(here, '../../assets/share');
const { chromium } = await import(process.env.BASIS_PLAYWRIGHT || 'playwright');

fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 900 }, deviceScaleFactor: 2 });
await page.goto(pathToFileURL(path.join(here, 'share_image.html')).href);
await page.evaluate(() => Promise.all([...document.images].map((i) => i.decode())));
await page.locator('#card').screenshot({ path: path.join(out, 'card.png') });
await page.locator('#icon').screenshot({ path: path.join(out, 'apple-touch-icon.png') });
await browser.close();
console.log(`wrote ${path.relative(process.cwd(), out)}/card.png and apple-touch-icon.png`);
