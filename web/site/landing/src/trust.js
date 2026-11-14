// The trust section's top row, played once as it comes into view (the signing card is sign.js): the
// quote rises in, then the four results one by one; a result marked data-to
// counts up from zero as it rises (none does today); the zeros simply arrive. Transform, opacity and text only; a
// count runs in a box held at its final width, so nothing beside it moves, and ends on the page's
// own text. No script or reduced motion: the final state, as written in the page. The hold state
// (.is-hold: the rows hidden, counts at 0) is set by this script only, and only while it waits.
const reduce = matchMedia('(prefers-reduced-motion: reduce)');
const EASE = 'cubic-bezier(.22,.61,.36,1)';
const easeOut = (k) => 1 - Math.pow(1 - k, 3);
const fmt = (n) => n.toLocaleString('en-US');

function count(el, delay, ms) {
  const to = Number(el.dataset.to), suffix = el.dataset.suffix || '', text = el.dataset.final;
  const t0 = performance.now() + delay;
  const step = (t) => {
    const k = Math.min(1, Math.max(0, (t - t0) / ms));
    if (k >= 1 || reduce.matches) { el.textContent = text; return; }
    el.textContent = fmt(Math.round(to * easeOut(k))) + suffix;
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
// Stacked (tablets and phones) the row is taller than the screen: shorter motion that starts just
// before it enters, so a reader scrolling through never meets an empty card (reveal.js does the same).
const stacked = matchMedia('(max-width: 980px)').matches;
const K = stacked ? 0.4 : 1;
const rise = (el, delay, ms = 750) => el.animate(
  [{ opacity: 0, transform: 'translateY(14px)' }, { opacity: 1, transform: 'none' }],
  { duration: Math.max(400, ms * (stacked ? 0.6 : 1)), delay: delay * K, easing: EASE, fill: 'backwards' });

const row = document.querySelector('.trust-row');
if (row && !reduce.matches && 'IntersectionObserver' in window && row.animate) {
  const counts = [...row.querySelectorAll('.r-n[data-to]')];
  for (const el of counts) {
    el.dataset.final = el.textContent;
    el.style.minWidth = `${el.getBoundingClientRect().width}px`;
    el.textContent = `0${el.dataset.suffix || ''}`;
  }
  row.classList.add('is-hold');
  const io = new IntersectionObserver(([e]) => {
    if (!e.isIntersecting) return;
    io.disconnect();
    rise(row.querySelector('blockquote'), 0, 800);
    rise(row.querySelector('figcaption'), 200, 800);
    row.querySelectorAll('.res li').forEach((li, i) => {
      const delay = 250 + i * 150;
      rise(li, delay);
      const n = li.querySelector('.r-n[data-to]');
      if (n) count(n, delay * K, 1300);
    });
    row.classList.remove('is-hold');
  }, stacked ? { rootMargin: '0px 0px 10% 0px' } : { threshold: 0.35 });
  io.observe(row);

  // switching to reduced motion mid-page: everything to its end state at once
  reduce.addEventListener('change', () => {
    if (!reduce.matches) return;
    io.disconnect();
    row.classList.remove('is-hold');
    counts.forEach((el) => { el.textContent = el.dataset.final; });
    row.getAnimations({ subtree: true }).forEach((a) => a.finish());
  });
}
