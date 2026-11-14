// The demo page's study overview: what a finished study looks like in basis, from
// src/data/overview.json (the Harbor House sample exactly as the sample engine has it;
// tools/terminal_data.cjs). The steps the study went through (appraisal read, photos sorted into
// rooms, assets priced, reconciled to the price, signed) check off one by one, the status turns to
// Signed, then the basis splits into its recovery classes and the amount moved into 5-, 7- and
// 15-year property counts up.
// Played once, when the panel comes into view. Transform, opacity and text only, in a panel of
// fixed height; the count runs in a box held at its final width, so nothing moves the page. The
// hold state (.is-hold) is set by this script only, while it waits. Reduced motion: the finished
// state, still.
const reduce = matchMedia('(prefers-reduced-motion: reduce)');
const EASE = 'cubic-bezier(.22,.61,.36,1)';
const dollars = (n) => `$${Math.round(n).toLocaleString('en-US')}`;
const YEARS = { y5: '5-yr', y7: '7-yr', y15: '15-yr', y39: '39-yr' };
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const CHECK = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.4 5 8.8l4.6-5.3"/></svg>';

async function mount(box) {
  const d = await (await fetch(new URL('data/overview.json', import.meta.url))).json();
  const gap = Math.abs(Number(d.reconciliation.gap_share) * 100).toFixed(2);
  const steps = [
    // [what always shows, what a narrow panel leaves out]
    ['Appraisal read', [`${dollars(d.price)} price`, ` · ${dollars(d.land)} land`]],
    ['Photos sorted', [`${d.photos} photos into ${d.rooms} rooms`]],
    ['Assets priced', [`${d.priced} cost lines`]],
    ['Reconciled', [`within ${gap} % of the price`]],
    ['Signed', [d.signer]],
  ];
  const fast = d.allocation.filter((b) => b.bucket !== 'y39').reduce((s, b) => s + Number(b.allocated), 0);

  box.replaceChildren();
  const bar = el('div', 'ovw-bar');
  const state = el('span', 'ovw-state');
  bar.append(el('span', 'wm', 'basis'), el('span', 'ovw-name', `${d.study.name} · Rev ${d.study.revision}`), state);
  const list = el('ol', 'ovw-steps');
  const items = steps.map(([k, v]) => {
    const li = el('li'); const dot = el('span', 'ovw-dot'); dot.innerHTML = CHECK;
    const val = el('span', 'ovw-v', v[0]); if (v[1]) val.append(el('span', 'ovw-x', v[1]));
    li.append(dot, el('span', 'ovw-k', k), val); list.append(li); return li;
  });
  const alloc = el('div', 'ovw-alloc');
  const lede = el('p', 'ovw-lede');
  const fig = el('b', null, dollars(fast));
  lede.append(el('span', 'ovw-cap', 'Moved into 5-, 7- and 15-year property'), fig, el('span', 'ovw-of', `of a ${dollars(d.basis)} basis`));
  const track = el('div', 'ovw-track');
  const segs = d.allocation.map((b) => { const s = el('span', `seg ${b.bucket}`); s.style.flexGrow = String(Number(b.share)); track.append(s); return s; });
  const key = el('ul', 'ovw-key');
  for (const b of d.allocation) { const li = el('li'); li.append(el('i', `sw ${b.bucket}`), el('span', null, YEARS[b.bucket]), el('b', null, dollars(b.allocated))); key.append(li); }
  alloc.append(lede, track, key);
  box.append(bar, list, alloc);

  const done = () => { state.textContent = 'Signed'; state.classList.add('is-done'); };
  if (reduce.matches || !('IntersectionObserver' in window) || !box.animate) { done(); return; }

  // hold: nothing checked, the values and the split not yet shown, the figure at $0
  const final = fig.textContent;
  fig.style.minWidth = `${fig.getBoundingClientRect().width}px`;
  state.textContent = 'In progress'; box.classList.add('is-hold'); fig.textContent = '$0';

  const T0 = 250, STEP = 620;
  const play = () => {
    box.classList.remove('is-hold');
    const anims = [];
    const fade = (n, delay, from = 'translateX(-6px)', ms = 420) => anims.push(n.animate([{ opacity: 0, transform: from }, { opacity: 1, transform: 'none' }], { duration: ms, delay, easing: EASE, fill: 'backwards' }));
    items.forEach((li, i) => {
      const t = T0 + i * STEP;
      anims.push(li.querySelector('.ovw-dot svg').animate([{ opacity: 0, transform: 'scale(.4)' }, { opacity: 1, transform: 'none' }], { duration: 300, delay: t, easing: EASE, fill: 'backwards' }));
      anims.push(li.querySelector('.ovw-dot').animate([{ backgroundColor: 'transparent' }, { backgroundColor: 'var(--accent)' }], { duration: 200, delay: t, fill: 'backwards' }));
      fade(li.querySelector('.ovw-v'), t + 90);
    });
    const tEnd = T0 + steps.length * STEP;
    setTimeout(done, tEnd - STEP + 120);
    segs.forEach((s, i) => anims.push(s.animate([{ transform: 'scaleX(0)' }, { transform: 'none' }], { duration: 700, delay: tEnd + i * 110, easing: EASE, fill: 'backwards' })));
    fade(key, tEnd + 200, 'translateY(4px)', 500);
    const t0 = performance.now() + tEnd, ms = 1200;
    const tick = (t) => {
      const k = Math.min(1, Math.max(0, (t - t0) / ms));
      if (k >= 1 || reduce.matches) { fig.textContent = final; return; }
      fig.textContent = dollars(fast * (1 - Math.pow(1 - k, 3)));
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    reduce.addEventListener('change', () => { if (reduce.matches) { anims.forEach((a) => a.finish()); fig.textContent = final; done(); } }, { once: true });
  };
  const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { io.disconnect(); play(); } }, { threshold: 0.35 });
  io.observe(box);
  reduce.addEventListener('change', () => { if (reduce.matches && box.classList.contains('is-hold')) { io.disconnect(); box.classList.remove('is-hold'); fig.textContent = final; done(); } });
}

document.querySelectorAll('[data-overview]').forEach((box) => mount(box).catch(() => { box.hidden = true; }));
