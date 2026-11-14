// The kitchen photo through Basis's localizer. Every outline is a mask SAM 3 returned for this
// photo (src/data/kitchen.json, written by tools/localize_photos.py with the product's phrases and
// thresholds); the bar stools are the engineer's boxes run through the same box-to-add call the
// app uses. Once the section's two lines and the figure are on screen, the outlines trace
// themselves class by class; then the section's second line ("You always have the final say")
// rises in as the engineer steps in, marks one door Not counted and draws the four stools the
// first pass missed. Hovering or tapping an
// outline lights its row and its class; a row lights its outlines. Without JavaScript the ledger
// reads in its final state; under reduced motion the final frame shows, still.

const SVGNS = 'http://www.w3.org/2000/svg';
const EASE = 'cubic-bezier(.22,.61,.36,1)';
const reduce = matchMedia('(prefers-reduced-motion: reduce)');

// What the figure shows, by rule (rerun-safe), and where each group's one label goes: a point
// in the photo (fractions) on empty ceiling, wall or floor beside the group, never on another
// object. Recheck the anchors and NOT_COUNTED with tools/crop_check.py after a rerun.
const GROUPS = [
  { row: 'light', name: 'Light fixture', pick: (i) => i.label === 'light_fixture' && i.counted, chip: [.300, .060] },
  { row: 'counter', name: 'Countertop', pick: (i) => i.label === 'countertop' && i.counted, chip: [.006, .640] },
  { row: 'range', name: 'Range', pick: (i) => i.label === 'stove' && i.counted, chip: [.300, .515] },
  { row: 'outlet', name: 'Outlet', pick: (i) => i.label === 'electrical_outlet' && i.counted, chip: [.292, .662] },
  { row: 'door', name: 'Interior door', pick: (i) => i.label === 'interior_door' && i.counted, chip: [.835, .600] },
  { row: 'stool', name: 'Bar stool', pick: (i) => i.source === 'manual', chip: [.370, .885], engineer: true },
];
const NOT_COUNTED = { id: 'k22', chip: [.790, .665] }; // the stair opening's edge, scored 0.54 as a door
const ENGINEER = 'John Doe';

const el = (tag, attrs = {}, ns) => {
  const n = ns ? document.createElementNS(ns, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const pct = (v) => `${(v * 100).toFixed(2)}%`;

export async function mountKitchen(fig) {
  const data = await (await fetch(new URL('./data/kitchen.json', import.meta.url))).json();
  const stage = fig.querySelector('.detect-stage');
  const svg = el('svg', { viewBox: '0 0 1 1', preserveAspectRatio: 'none', class: 'detect-svg', 'aria-hidden': 'true' }, SVGNS);
  const chips = el('div', { class: 'detect-chips', 'aria-hidden': 'true' });
  const groups = [];
  for (const g of GROUPS) {
    const items = data.instances.filter(g.pick);
    const masks = items.map((inst) => {
      const m = el('g', { class: `mask${inst.source === 'manual' ? ' is-drawn' : ''}`, 'data-row': g.row, 'data-id': inst.id }, SVGNS);
      const p = el('path', { d: inst.path, pathLength: '1' }, SVGNS);
      m.append(p); svg.append(m);
      return { inst, m, p };
    });
    const chip = el('span', { class: `chip${g.engineer ? ' is-engineer' : ''}`, 'data-row': g.row });
    chip.style.left = pct(g.chip[0]); chip.style.top = pct(g.chip[1]);
    const n = items.length;
    chip.innerHTML = `<b>${g.name}</b>${n > 1 || g.engineer ? `<i>${n > 1 ? `×${n}` : ''}${g.engineer ? ` · drawn by ${ENGINEER}` : ''}</i>` : ''}`;
    chips.append(chip);
    groups.push({ ...g, masks, chip, n });
  }
  const door = groups.find((g) => g.row === 'door'), stools = groups.find((g) => g.row === 'stool');
  const struck = door.masks.find((x) => x.inst.id === NOT_COUNTED.id);
  const ncChip = el('span', { class: 'chip is-engineer is-struck', 'data-row': 'door' });
  ncChip.style.left = pct(NOT_COUNTED.chip[0]); ncChip.style.top = pct(NOT_COUNTED.chip[1]);
  ncChip.innerHTML = `<b>Door</b><i>not counted · ${struck.inst.score.toFixed(2)}</i>`;
  chips.append(ncChip);
  const [sx1, sy1, sx2, sy2] = struck.inst.box;
  const strike = el('path', { d: `M${sx1} ${sy1}L${sx2} ${sy2}`, class: 'det-strike', pathLength: '1' }, SVGNS);
  struck.m.append(strike);
  const drag = el('div', { class: 'drag-box', 'aria-hidden': 'true' });
  const cursor = el('div', { class: 'detect-cursor', 'aria-hidden': 'true' });
  cursor.innerHTML = `<svg viewBox="0 0 20 22" width="20" height="22"><path d="M2 1.5 17 12l-6.6.9L7 19.6z"/></svg><span>${ENGINEER}</span>`;
  stage.append(svg, chips, drag);
  fig.append(cursor);

  const rows = new Map([...fig.querySelectorAll('.detect-rows [data-row]')].map((r) => [r.dataset.row, r]));
  const changes = [...fig.querySelectorAll('[data-change]')];
  const replay = fig.querySelector('.detect-replay');
  const sec = fig.closest('section');
  const say2 = sec && sec.querySelector('.cs-2'), say2in = say2 && say2.querySelector('.cs-in');

  // Labels are placed by fractions of the photo but keep their own size, so on a narrower photo a
  // label near the edge would run off it; nudge each one back inside.
  function keepChipsIn() {
    const r = stage.getBoundingClientRect();
    for (const c of chips.children) {
      c.style.translate = '';
      const b = c.getBoundingClientRect();
      if (!b.width) continue; // hidden (phones show only the lit class's label)
      const dx = Math.min(0, r.right - 6 - b.right) || Math.max(0, r.left + 6 - b.left);
      if (dx) c.style.translate = `${Math.round(dx)}px 0`;
    }
  }
  keepChipsIn();
  new ResizeObserver(keepChipsIn).observe(stage);
  const doorChip = door.chip;
  const doorText = (n) => { doorChip.innerHTML = `<b>${door.name}</b><i>×${n}</i>`; };

  // Focus: a class (its outlines, label and row) leads; the rest recede. While the sequence plays,
  // pointing or focusing is remembered (wanted), not shown, so it cannot dim outlines mid-trace;
  // the class still under the pointer leads once the sequence ends.
  let hot = null, wanted = null, playing = false;
  const want = (row) => { wanted = row; stopTour(); if (!playing) setHot(row); };
  function setHot(row) {
    if (hot === row) return;
    hot = row;
    fig.classList.toggle('has-hot', !!row);
    for (const g of groups) for (const { m } of g.masks) m.classList.toggle('is-hot', g.row === row);
    for (const c of chips.children) c.classList.toggle('is-hot', c.dataset.row === row);
    for (const [k, r] of rows) r.classList.toggle('is-hot', k === row);
    keepChipsIn();
  }
  svg.addEventListener('pointerover', (e) => { const m = e.target.closest('.mask'); if (m && e.pointerType === 'mouse') want(m.dataset.row); });
  svg.addEventListener('click', (e) => { const m = e.target.closest('.mask'); want(m && wanted !== m.dataset.row ? m.dataset.row : null); });
  stage.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse') want(null); });
  for (const [k, r] of rows) {
    r.addEventListener('pointerenter', (e) => { if (e.pointerType === 'mouse') want(k); });
    r.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse' && wanted === k) want(null); });
    r.addEventListener('click', () => want(wanted === k ? null : k));
    r.addEventListener('focus', () => { if (r.matches(':focus-visible')) want(k); }); // keyboard focus; a tap's click decides on its own
    r.addEventListener('blur', () => { if (wanted === k) want(null); });
  }

  // Phones have no hover, and the photo is too small for every label at once. There one class at a
  // time leads, its outlines, its label on the photo and its row together, touring the classes
  // (the engineer's two corrections first) until the visitor taps a row or an outline; from then
  // on their choice leads. Under reduced motion the tour holds on the drawn bar stools.
  const narrow = matchMedia('(max-width: 700px)');
  const TOUR = ['stool', 'door', 'light', 'counter', 'range', 'outlet'];
  let tourAt = 0, tourTimer = 0, touring = false, inView = false;
  function tourStep() {
    if (!touring || wanted || playing) return;
    setHot(TOUR[tourAt++ % TOUR.length]);
    tourTimer = setTimeout(tourStep, 2800);
  }
  function startTour() {
    if (!narrow.matches || wanted || touring) return;
    if (reduce.matches) { setHot(TOUR[0]); return; }
    if (!inView) return;
    touring = true; tourStep();
  }
  function stopTour() { touring = false; clearTimeout(tourTimer); }
  new IntersectionObserver(([e]) => { inView = e.isIntersecting; if (inView && !playing) startTour(); else if (!inView) stopTour(); }, { threshold: 0.3 }).observe(fig);
  narrow.addEventListener('change', () => { stopTour(); if (!wanted) setHot(null); startTour(); });

  function setState(state) {
    const start = state === 'start';
    for (const g of groups) {
      for (const { m } of g.masks) m.style.visibility = start ? 'hidden' : '';
      g.chip.classList.toggle('is-on', !start);
    }
    struck.m.classList.toggle('is-struck', !start);
    strike.style.visibility = start ? 'hidden' : '';
    ncChip.classList.toggle('is-on', !start);
    doorText(start ? door.n : door.n - 1);
    rows.get('door').classList.toggle('is-done', !start);
    rows.get('stool').classList.toggle('is-done', !start);
    for (const c of changes) c.classList.toggle('is-on', !start);
    cursor.classList.remove('is-on');
    drag.style.opacity = 0;
    if (say2) { say2in.getAnimations().forEach((a) => a.cancel()); say2.classList.toggle('is-hold', start); }
  }

  let run = 0, cx = 0, cy = 0;
  const wait = (ms, token) => new Promise((ok, no) => setTimeout(() => (token === run ? ok() : no(new Error('stale'))), ms));
  const onStage = (x, y) => {
    const f = fig.getBoundingClientRect(), r = stage.getBoundingClientRect();
    return [r.left - f.left + r.width * x, r.top - f.top + r.height * y];
  };
  function move(to, ms) {
    cursor.classList.toggle('is-left', to[0] > fig.clientWidth - 110); // the name tag flips inside near the right edge
    const a = cursor.animate([{ transform: `translate(${cx}px,${cy}px)` }, { transform: `translate(${to[0]}px,${to[1]}px)` }], { duration: ms, easing: EASE, fill: 'forwards' });
    [cx, cy] = to;
    return a.finished;
  }
  const trace = (p, ms, delay = 0) => p.animate([{ strokeDashoffset: 1, strokeDasharray: 1 }, { strokeDashoffset: 0, strokeDasharray: 1 }], { duration: ms, delay, easing: EASE, fill: 'backwards' }).finished;

  async function play() {
    const token = ++run;
    playing = true; stopTour(); setHot(null);
    replay.hidden = true;
    setHot(null);
    setState('start');
    try {
      await wait(250, token);
      // 1. the first pass: each class's outlines trace themselves, then its label
      for (const g of groups) {
        if (g.row === 'stool') continue;
        const all = g.masks.map(({ m, p }, i) => { m.style.visibility = ''; return trace(p, 560, Math.min(i, 8) * 40); });
        await Promise.all(all);
        g.chip.classList.add('is-on');
        await wait(140, token);
      }
      await wait(1100, token);
      // 2. the second line rises in as the engineer steps in and marks the stair edge Not counted
      if (say2) {
        say2.classList.remove('is-hold');
        say2in.animate([{ transform: 'translateY(105%)' }, { transform: 'none' }], { duration: 800, easing: EASE });
        await wait(250, token);
      }
      [cx, cy] = onStage(.92, .9);
      cursor.classList.add('is-on');
      await move(onStage((sx1 + sx2) / 2, (sy1 + sy2) / 2), 900);
      struck.m.classList.add('is-struck');
      strike.style.visibility = '';
      await trace(strike, 320);
      ncChip.classList.add('is-on');
      doorText(door.n - 1);
      rows.get('door').classList.add('is-done');
      changes[0].classList.add('is-on');
      await wait(600, token);
      // 3. the engineer draws a box on each stool; Basis traces the stool inside it
      for (const { inst, m, p } of stools.masks) {
        const [x1, y1, x2, y2] = inst.drawn_box;
        await move(onStage(x1, y1), 420);
        drag.style.left = pct(x1); drag.style.top = pct(y1); drag.style.width = pct(x2 - x1); drag.style.height = pct(y2 - y1);
        drag.style.opacity = 1;
        await Promise.all([
          move(onStage(x2, y2), 340),
          drag.animate([{ clipPath: 'inset(0 100% 100% 0)' }, { clipPath: 'inset(0 0 0 0)' }], { duration: 340, easing: EASE }).finished,
        ]);
        m.style.visibility = '';
        await trace(p, 320);
        drag.style.opacity = 0;
      }
      stools.chip.classList.add('is-on');
      rows.get('stool').classList.add('is-done');
      changes[1].classList.add('is-on');
      await wait(700, token);
      cursor.classList.remove('is-on');
      done();
    } catch { /* replaced by a newer run */ }
  }
  function done() { playing = false; if (fig.classList.contains('is-live')) replay.hidden = false; setHot(wanted); startTour(); }

  replay.addEventListener('click', () => play());
  setState('final');
  if (reduce.matches) { startTour(); return; }
  fig.classList.add('is-live'); // Replay shows once the first run has ended
  setState('start');
  // Start once the section's lines and the figure are all on screen under the nav (on a phone, where
  // the ledger stacks below, the lines and the photo), or the top has reached the nav when the
  // screen is too short to hold it all.
  const top = (sec && sec.querySelector('.count-say')) || fig, ledger = fig.querySelector('.detect-ledger'), NAV = 96;
  const ready = () => {
    const t = top.getBoundingClientRect().top;
    const stacked = ledger.getBoundingClientRect().top >= stage.getBoundingClientRect().bottom - 1;
    const b = (stacked ? stage : fig).getBoundingClientRect().bottom;
    return b <= innerHeight + 2 || (b - t > innerHeight - NAV && t <= NAV);
  };
  let ticking = false;
  const check = () => {
    ticking = false;
    if (!ready()) return;
    removeEventListener('scroll', onScroll);
    if (fig.getBoundingClientRect().bottom < NAV) { setState('final'); done(); return; } // jumped past it
    play();
    // scrolled away mid-run: show the finished figure rather than play on off screen
    const away = new IntersectionObserver((es) => {
      if (es.some((e) => e.isIntersecting)) return;
      away.disconnect();
      if (playing) { run++; setState('final'); done(); }
    });
    away.observe(fig);
  };
  const onScroll = () => { if (!ticking) { ticking = true; requestAnimationFrame(check); } };
  addEventListener('scroll', onScroll, { passive: true });
  onScroll();
  reduce.addEventListener('change', (e) => { if (e.matches) { run++; setState('final'); done(); } });
}

// The outlines (src/data/kitchen.json, 79 KB) load as the figure comes near, not with the page, so
// they never sit in front of the first paint; the figure is a screen or more below the hero.
for (const fig of document.querySelectorAll('[data-kitchen]')) {
  const near = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { near.disconnect(); mountKitchen(fig); } }, { rootMargin: '600px 0px' });
  near.observe(fig);
}
