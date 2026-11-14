// The landing page: the header menu, the hero's footnotes, and the figures.
import './kitchen.js';
import './rooms.js';
import './record.js';
import './reveal.js';
import './sign.js';
import './trust.js';
import './scroll.js';
import { overlay, link, clear } from './connect.js';

const reduce = matchMedia('(prefers-reduced-motion: reduce)');

// The phone menu (the current site's, web/site/site.js).
const header = document.querySelector('.site-header'), menu = header.querySelector('.menu-toggle'), mobile = header.querySelector('#mobile-nav');
function closeMenu(restore = false) { mobile.hidden = true; menu.setAttribute('aria-expanded', 'false'); menu.setAttribute('aria-label', 'Open navigation'); if (restore) menu.focus(); }
menu.addEventListener('click', () => { const open = menu.getAttribute('aria-expanded') === 'true'; mobile.hidden = open; menu.setAttribute('aria-expanded', String(!open)); menu.setAttribute('aria-label', open ? 'Open navigation' : 'Close navigation'); });
mobile.addEventListener('click', (e) => { if (e.target.closest('a')) closeMenu(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !mobile.hidden) closeMenu(true); });
document.addEventListener('click', (e) => { if (!mobile.hidden && (!header.contains(e.target) || e.target === header)) closeMenu(); }); // outside the menu, or on the dimmed page behind it (the header's own backdrop)

// Footnotes: a chip draws a line to its source in the rail, and the source card comes forward.
// The line drops from the chip (on the paragraph's last line) into the gap under the paragraph,
// runs along it clear of the text, and turns into the card's number. Until the reader points at
// a chip, the three take turns; leaving settles back to the turns. All three sources stay at
// full ink; the selection shows by the white card, the green number and the line.
const hero = document.querySelector('.hero'), rail = hero.querySelector('.rail'), lede = hero.querySelector('.lede');
const svg = overlay(hero);
const pairs = [...hero.querySelectorAll('.cite')].map((c) => [c, document.getElementById(c.dataset.note)]);
let current = null, turn = 0, timer = 0, idle = true;
const besideCopy = matchMedia('(min-width: 981px)');
// Stacked (tablets and phones) the sources sit under the copy, so no line can reach them from the
// sentence. There they read as a derivation instead: one rule runs through the three numbers, and
// it fills in green down to the source that is lit (1, then 2, then 3), as the figure is built up
// from the cost line, through the appraisal, to its class. trail() measures where the rule runs.
const steps = rail.querySelector('ol');
function trail() {
  if (besideCopy.matches) return;
  const box = steps.getBoundingClientRect();
  const centre = (n) => { const r = n.querySelector('.n').getBoundingClientRect(); return [r.left + r.width / 2 - box.left, r.top + r.height / 2 - box.top]; };
  const [x, top] = centre(pairs[0][1]), [, bottom] = centre(pairs[pairs.length - 1][1]);
  steps.style.setProperty('--trail-x', `${x}px`);
  steps.style.setProperty('--trail-top', `${top}px`);
  steps.style.setProperty('--trail-height', `${bottom - top}px`);
}
function show(chip) {
  if (current === chip) return;
  current = chip;
  clear(svg);
  for (const [c, n] of pairs) { c.classList.toggle('is-on', c === chip); n.classList.toggle('is-on', c === chip); }
  rail.classList.toggle('has-on', !!chip);
  const at = pairs.findIndex(([c]) => c === chip);
  steps.style.setProperty('--trail-fill', at > 0 ? String(at / (pairs.length - 1)) : '0');
  const note = chip && pairs.find(([c]) => c === chip)[1];
  if (note && besideCopy.matches) requestAnimationFrame(() => {
    const gap = lede.getBoundingClientRect().bottom - hero.getBoundingClientRect().top + 14;
    link(svg, hero, chip, note.querySelector('.n'), { exit: 'below', gapY: gap });
  });
}
const cycle = () => { if (!idle || reduce.matches) return; show(pairs[turn % pairs.length][0]); turn++; timer = setTimeout(cycle, 3200); };
for (const [c, n] of pairs) {
  for (const el of [c, n]) {
    el.addEventListener('pointerenter', (e) => { if (e.pointerType !== 'mouse') return; idle = false; clearTimeout(timer); show(c); });
    el.addEventListener('pointerleave', (e) => { if (e.pointerType !== 'mouse') return; show(null); idle = true; clearTimeout(timer); timer = setTimeout(cycle, 1600); });
  }
  c.addEventListener('focus', () => { idle = false; clearTimeout(timer); show(c); });
  c.addEventListener('click', (e) => {
    e.preventDefault(); idle = false; clearTimeout(timer);
    const next = current === c ? null : c;
    show(next);
    // stacked, the source is further down the page: bring it into view
    if (next && !besideCopy.matches) n.scrollIntoView({ block: 'center', behavior: reduce.matches ? 'auto' : 'smooth' });
  });
}
addEventListener('resize', () => { const c = current; current = null; if (c) show(c); });
new ResizeObserver(trail).observe(rail);
document.fonts?.ready.then(trail);
if (reduce.matches) show(pairs[0][0]); else timer = setTimeout(cycle, 1400);
