// The sign-in page's terminal: a study's asset schedule priced line by line, from
// src/data/terminal.json (the Harbor House sample exactly as the sample engine prices it;
// tools/terminal_data.cjs). Each priced asset arrives with its cost line, total and recovery class,
// and the class totals underneath add up as the rows arrive; at the end they equal the schedule's
// own totals, and the status reads the reconciliation to the price.
// The panel opens already full (all but its last slot), so it never shows empty rows; then a row
// every 700 ms, the newest marked; after the last row it holds, then starts again. It runs only
// while the panel is on screen and the tab is visible. Reduced motion: the finished state, still
// (the last rows and the full totals). Text and a background only; the panel has a fixed height,
// so nothing moves the page.
const reduce = matchMedia('(prefers-reduced-motion: reduce)');
const money = (cents) => (cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const cents = (s) => Math.round(Number(s) * 100);
const YEARS = { y5: '5-yr', y7: '7-yr', y15: '15-yr', y39: '39-yr' };
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

const FEEDS = {
  schedule: {
    title: 'Asset schedule',
    head: [['Asset', ''], ['Cost line', 'c-line'], ['Total', 'r'], ['Class', ''], ['§', 'c-irs']],
    rows: (d) => d.rows,
    cells: (r) => [[r.name, ''], [r.line || 'structure', 'c-line'], [money(cents(r.total)), 'r amt'], [YEARS[r.bucket], r.bucket], [r.irs, 'c-irs']],
    totals: (d) => d.by_bucket.map((b) => [YEARS[b.bucket], b.bucket]),
    add: (acc, r) => { acc[r.bucket] = (acc[r.bucket] || 0) + cents(r.total); },
    status: (d, n, total) => [`${n} of ${total} assets priced`, n === total ? `Reconciled to price ${(Number(d.reconciliation.gap_share) * 100).toFixed(2).replace('-', '−')} %` : 'Pricing…'],
  },
};

async function mount(box) {
  const feed = FEEDS[box.dataset.feed];
  const data = await (await fetch(new URL('data/terminal.json', import.meta.url))).json();
  const rows = feed.rows(data), N = () => Number(getComputedStyle(box.querySelector('.term-rows') || box).getPropertyValue('--n')) || 11;

  box.replaceChildren();
  const bar = el('div', 'term-bar'); bar.append(el('b', null, 'basis'), el('span', null, `${data.study.name} · ${feed.title}`), el('span', 'live', 'Live'));
  const head = el('div', 'term-head'); for (const [t, c] of feed.head) head.append(el('span', c, t));
  const list = el('div', 'term-rows');
  const foot = el('div', 'term-foot'), tot = el('div', 'term-tot'), status = el('div', 'term-status');
  const slots = feed.totals(data).map(([label, key]) => { const d = el('div'); const b = el('b'); d.append(el('i', null, label), b); tot.append(d); return [key, b]; });
  const left = el('span'), right = el('span'); status.append(left, right);
  foot.append(tot, status); box.append(bar, head, list, foot);

  let i = 0, acc = {}, timer = 0, on = false;
  const paint = () => {
    for (const [key, b] of slots) b.textContent = feed.show ? feed.show(key, acc) : `$${money(acc[key] || 0)}`;
    const [l, r] = feed.status(data, i, rows.length); left.textContent = l; right.textContent = r; right.className = i === rows.length ? 'ok' : '';
  };
  // A fixed set of row slots whose text is rewritten: a new row moves the text up a slot, never an
  // element, so the panel never registers as a layout shift.
  let shown = [], slotsN = 0;
  const layout = () => {
    if (slotsN === N()) return; slotsN = N();
    list.replaceChildren(...Array.from({ length: slotsN }, () => { const e = el('div', 'term-row'); for (const [, c] of feed.head) e.append(el('span', c, '')); return e; }));
  };
  const render = (mark) => {
    layout(); shown = shown.slice(-slotsN);
    [...list.children].forEach((e, k) => {
      const r = shown[k], cells = r ? feed.cells(r) : null;
      [...e.children].forEach((span, j) => { span.textContent = cells ? cells[j][0] : ''; span.className = cells ? cells[j][1] : feed.head[j][1]; });
      e.classList.toggle('is-new', mark && k === shown.length - 1);
    });
  };
  const push = (r, mark) => { shown.push(r); render(mark); };
  const finished = () => { acc = {}; rows.forEach((r) => feed.add(acc, r)); shown = rows.slice(); render(false); i = rows.length; paint(); };
  // the opening state: the first rows already priced, one slot left for the next
  const opening = () => { layout(); i = Math.max(0, Math.min(rows.length - 1, slotsN - 1)); acc = {}; shown = rows.slice(0, i); shown.forEach((r) => feed.add(acc, r)); render(false); paint(); };
  const step = () => {
    if (!on) return;
    if (i >= rows.length) { timer = setTimeout(() => { opening(); timer = setTimeout(step, 600); }, 4200); return; }
    const r = rows[i++]; feed.add(acc, r); push(r, true); paint();
    timer = setTimeout(step, 700);
  };
  const start = () => { if (on || reduce.matches) return; on = true; timer = setTimeout(step, 300); };
  const stop = () => { on = false; clearTimeout(timer); };

  if (reduce.matches || !('IntersectionObserver' in window)) { finished(); return; }
  opening();
  addEventListener('resize', () => render(false));
  let visible = false;
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; visible && !document.hidden ? start() : stop(); }, { threshold: 0.2 }).observe(box);
  document.addEventListener('visibilitychange', () => { visible && !document.hidden ? start() : stop(); });
  reduce.addEventListener('change', () => { if (reduce.matches) { stop(); finished(); } });
}

document.querySelectorAll('[data-feed]').forEach((box) => mount(box).catch(() => { box.hidden = true; }));
