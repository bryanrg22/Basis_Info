// Motion for the page's sections. Each section rises in once as it comes into view: its heading
// first, then its figure, by opacity and transform only (no layout shift). Inside a figure, parts
// marked data-rv="<ms>" follow on their own delay (the partner logos one by one, then the quote;
// the trust facts row by row), and numbers marked data-count count up from zero in a box held at
// their final width, so nothing beside them moves. The page head adds html.rv only when motion is
// allowed and IntersectionObserver exists, and removes it again after 4 s if this module never
// ran, so the page is never left hidden. In the speed section the long bar grows from zero, then
// the short one: to scale, both at once would hide the point.
const root = document.documentElement;
const reduce = matchMedia('(prefers-reduced-motion: reduce)');
const EASE = 'cubic-bezier(.22,.61,.36,1)';
window.__rv = true;

function countUp(el) {
  const end = Number(el.dataset.count), text = el.textContent;
  el.style.minWidth = `${el.getBoundingClientRect().width}px`; // held at the final width
  const fmt = (n) => n.toLocaleString('en-US');
  const t0 = performance.now() + 250, ms = 1400;
  const step = (t) => {
    const k = Math.min(1, Math.max(0, (t - t0) / ms)), e = 1 - Math.pow(1 - k, 3);
    el.textContent = k < 1 ? fmt(Math.round(end * e)) : text;
    if (k < 1 && !reduce.matches) requestAnimationFrame(step); else el.textContent = text;
  };
  el.textContent = fmt(0);
  requestAnimationFrame(step);
}

// Stacked (tablets and phones) a section is taller than the screen and a reader scrolls through it
// in a second or two, so the motion there is shorter and starts just before the section enters,
// never leaving a reader in front of content that has not arrived yet.
const stacked = matchMedia('(max-width: 980px)').matches;
const T = stacked ? { ms: 450, step: 60, scale: 0.35, margin: '0px 0px 10% 0px' } : { ms: 800, step: 160, scale: 1, margin: '0px 0px -12% 0px' };

if (root.classList.contains('rv')) {
  const rise = (el, delay) => el.animate(
    [{ opacity: 0, transform: `translateY(${stacked ? 12 : 22}px)` }, { opacity: 1, transform: 'none' }],
    { duration: T.ms, delay, easing: EASE, fill: 'backwards' });
  const reveal = (sec) => {
    [...sec.children].forEach((el, i) => rise(el, i * T.step));
    sec.querySelectorAll('[data-rv]').forEach((el) => rise(el, Number(el.dataset.rv) * T.scale));
    sec.querySelectorAll('[data-count]').forEach(countUp);
    sec.classList.add('is-in');
  };
  const io = new IntersectionObserver((es) => {
    for (const e of es) if (e.isIntersecting) { io.unobserve(e.target); reveal(e.target); }
  }, { rootMargin: T.margin });
  document.querySelectorAll('main > .sec, main > .closing').forEach((s) => io.observe(s));
}

const speed = document.querySelector('[data-speed]');
if (speed && !reduce.matches && 'IntersectionObserver' in window && speed.animate) {
  const [long, short] = [speed.querySelector('.speed-row:not(.is-us) .speed-fill'), speed.querySelector('.speed-row.is-us .speed-fill')];
  speed.classList.add('is-hold');
  const sio = new IntersectionObserver(([e]) => {
    if (!e.isIntersecting) return;
    sio.disconnect();
    const [ld, lm, sm] = stacked ? [150, 1000, 400] : [500, 1800, 500];
    long.animate([{ transform: 'scaleX(0)' }, { transform: 'none' }], { duration: lm, delay: ld, easing: EASE, fill: 'backwards' });
    short.animate([{ transform: 'scaleX(0)' }, { transform: 'none' }], { duration: sm, delay: ld + lm, easing: EASE, fill: 'backwards' });
    speed.classList.remove('is-hold');
  }, { threshold: stacked ? 0.25 : 0.5 });
  sio.observe(speed);
}

// switching to reduced motion mid-page: everything to its end state at once
reduce.addEventListener('change', () => {
  if (!reduce.matches) return;
  root.classList.remove('rv');
  speed?.classList.remove('is-hold');
  document.getAnimations().forEach((a) => a.finish());
});
