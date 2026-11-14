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
const EASE = 'cubic-bezier(.22,.61,.36,1)', DWELL = 3200;

// Stacked (tablets and phones) the sources sit under the copy, out of reach of a line from the
// sentence, and listed in full they fill a phone's screen. There they become one card, the way a
// phone shows footnotes (one source at a time, swiped through): the three numbers as tabs naming
// each source, and one source at a time in a panel as tall as the tallest, so nothing below it
// moves. The chips in the sentence, the tabs and a sideways swipe on the panel all choose a
// source; until the reader does, the three take turns, the lit tab filling as its turn runs. The
// second source's sum is the figure itself, so the figure lights with it.
const steps = rail.querySelector('ol'), fig = rail.querySelector('.rail-fig');
const tabs = document.createElement('div');
tabs.className = 'rail-tabs';
tabs.setAttribute('aria-label', 'Sources');
const tabFor = new Map();
for (const [c, n] of pairs) {
  const b = document.createElement('button');
  b.type = 'button';
  b.id = `${n.id}-tab`;
  b.setAttribute('aria-label', `Source ${n.querySelector('.n').textContent}: ${n.dataset.tab}`);
  b.innerHTML = `<span class="t-n">${n.querySelector('.n').textContent}</span><span class="t-l">${n.dataset.tab}</span><span class="t-bar" aria-hidden="true"></span>`;
  b.addEventListener('click', () => choose(c));
  tabs.append(b);
  tabFor.set(c, b);
}
fig.after(tabs);
const stacked = () => !besideCopy.matches;
// The tab roles exist only while the sources are tabs; beside the copy they are a plain list.
function mode() {
  const on = stacked();
  rail.classList.toggle('is-tabs', on);
  if (on) tabs.setAttribute('role', 'tablist'); else tabs.removeAttribute('role');
  for (const [c, n] of pairs) {
    const b = tabFor.get(c);
    for (const [el, role, ref, id] of [[b, 'tab', 'aria-controls', n.id], [n, 'tabpanel', 'aria-labelledby', b.id]]) {
      if (on) { el.setAttribute('role', role); el.setAttribute(ref, id); } else { el.removeAttribute('role'); el.removeAttribute(ref); }
    }
  }
  const c = current; current = null;
  show(c);
}
function bar(chip) {
  for (const b of tabFor.values()) b.querySelector('.t-bar').getAnimations().forEach((a) => a.cancel());
  if (!chip || !stacked() || !idle || reduce.matches) return;
  tabFor.get(chip).querySelector('.t-bar').animate([{ transform: 'scaleX(0)' }, { transform: 'none' }], { duration: DWELL, easing: 'linear', fill: 'forwards' });
}
function show(chip) {
  if (stacked() && !chip) chip = current || pairs[0][0]; // a panel always shows one source
  if (current === chip) return;
  const before = pairs.findIndex(([c]) => c === current), at = pairs.findIndex(([c]) => c === chip);
  const was = current && pairs[before][1];
  current = chip;
  clear(svg);
  for (const [c, n] of pairs) {
    const on = c === chip;
    c.classList.toggle('is-on', on); n.classList.toggle('is-on', on);
    const b = tabFor.get(c);
    b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1;
  }
  rail.classList.toggle('has-on', !!chip);
  rail.classList.toggle('is-sum', at === 1);
  tabs.style.setProperty('--at', String(Math.max(0, at)));
  const note = chip && pairs[at][1];
  if (note && besideCopy.matches) requestAnimationFrame(() => {
    const gap = lede.getBoundingClientRect().bottom - hero.getBoundingClientRect().top + 14;
    link(svg, hero, chip, note.querySelector('.n'), { exit: 'below', gapY: gap });
  });
  if (stacked() && note && was && !reduce.matches) {
    // the new source comes in from the side it is on, the old one leaves by the other
    const dir = at > before ? 1 : -1;
    note.animate([{ opacity: 0, transform: `translateX(${dir * 28}px)` }, { opacity: 1, transform: 'none' }], { duration: 420, easing: EASE });
    was.animate([{ opacity: 1, transform: 'none', visibility: 'visible' }, { opacity: 0, transform: `translateX(${-dir * 28}px)`, visibility: 'visible' }], { duration: 240, easing: 'ease-in' });
  }
  bar(chip);
}
// a reader's choice stops the turns (on a phone they stay stopped: the reader has the card now)
function choose(chip) { idle = false; clearTimeout(timer); show(chip); bar(null); }
const cycle = () => { if (!idle || reduce.matches) return; show(pairs[turn % pairs.length][0]); turn++; timer = setTimeout(cycle, DWELL); };
for (const [c, n] of pairs) {
  for (const el of [c, n]) {
    el.addEventListener('pointerenter', (e) => { if (e.pointerType !== 'mouse' || stacked()) return; idle = false; clearTimeout(timer); show(c); });
    el.addEventListener('pointerleave', (e) => { if (e.pointerType !== 'mouse' || stacked()) return; show(null); idle = true; clearTimeout(timer); timer = setTimeout(cycle, 1600); });
  }
  c.addEventListener('focus', () => { idle = false; clearTimeout(timer); show(c); bar(null); });
  c.addEventListener('click', (e) => {
    e.preventDefault();
    if (stacked()) {
      choose(c);
      // the card is under the copy: bring it into view if it is not
      steps.scrollIntoView({ block: 'nearest', behavior: reduce.matches ? 'auto' : 'smooth' });
      return;
    }
    idle = false; clearTimeout(timer);
    show(current === c ? null : c);
  });
}
// the tabs: arrow keys move between them, as a tab list does
tabs.addEventListener('keydown', (e) => {
  const k = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
  if (!k || !stacked()) return;
  e.preventDefault();
  const at = (pairs.findIndex(([c]) => c === current) + k + pairs.length) % pairs.length;
  choose(pairs[at][0]); tabFor.get(pairs[at][0]).focus();
});
// a sideways swipe on the panel: left for the next source, right for the one before
let sx = null, sy = 0;
steps.addEventListener('pointerdown', (e) => { if (stacked() && e.pointerType !== 'mouse') { sx = e.clientX; sy = e.clientY; } });
steps.addEventListener('pointercancel', () => { sx = null; });
steps.addEventListener('pointerup', (e) => {
  if (sx === null) return;
  const dx = e.clientX - sx, dy = e.clientY - sy; sx = null;
  if (Math.abs(dx) < 40 || Math.abs(dx) < 1.5 * Math.abs(dy)) return;
  const at = pairs.findIndex(([c]) => c === current) + (dx < 0 ? 1 : -1);
  if (at >= 0 && at < pairs.length) choose(pairs[at][0]);
});
addEventListener('resize', () => { const c = current; current = null; if (c) show(c); });
besideCopy.addEventListener('change', mode);
mode();
if (stacked() && !reduce.matches) { turn = 1; bar(current); timer = setTimeout(cycle, DWELL); }
else if (reduce.matches) show(pairs[0][0]); else timer = setTimeout(cycle, 1400);
