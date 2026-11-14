// Weighted scroll, as on the TroyLabs site (src/scripts/scroll.ts): Lenis 1.3.26 at lerp 0.09
// turns a mouse wheel's steps into one gliding motion. Lenis scrolls the real document, so the
// fixed header, the reveals (IntersectionObserver) and every measurement on the page are unchanged.
// Desktop mice and trackpads only (touch is native and already has its own inertia), never under
// reduced motion, and loaded only then, so phones never download it. In-page links glide too and
// stop under the header (Lenis reads html's scroll-padding-top). The frame loop runs only while
// something moves and stops once the page is still, so an idle page does no work.
const motion = matchMedia('(prefers-reduced-motion: no-preference)');
const fine = matchMedia('(hover: hover) and (pointer: fine)');

// imported without await, so the rest of the page's script never waits on this download
if (motion.matches && fine.matches) import('../../assets/vendor/lenis-1.3.26.mjs').then(({ default: Lenis }) => {
  const lenis = new Lenis({ lerp: 0.09, wheelMultiplier: 1, smoothWheel: true, syncTouch: false });
  let running = false, still = 0;
  const raf = (t) => {
    lenis.raf(t);
    still = lenis.isScrolling ? 0 : still + 1;
    if (document.hidden || still > 30) { running = false; return; }
    requestAnimationFrame(raf);
  };
  const wake = () => { still = 0; if (!running && !document.hidden) { running = true; requestAnimationFrame(raf); } };
  for (const e of ['wheel', 'keydown', 'pointerdown', 'resize']) addEventListener(e, wake, { passive: true });
  // In-page links glide (Lenis's own anchors option starts a glide but lets the browser's jump land
  // first). Once there, the address and the focus move as a plain link would move them, so the skip
  // link and the next Tab still start from the target. Modified clicks keep their usual meaning.
  document.addEventListener('click', (e) => {
    const a = e.target.closest?.('a[href^="#"]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const hash = a.getAttribute('href'), target = hash.length > 1 && document.getElementById(decodeURIComponent(hash.slice(1)));
    if (!target) return;
    e.preventDefault();
    wake();
    // a scroll Lenis has not heard about yet (a scrollbar drag, a script) would put the glide's
    // start in the wrong place; take the page's real position first
    if (!lenis.isScrolling && Math.abs(lenis.animatedScroll - scrollY) > 1) lenis.scrollTo(scrollY, { immediate: true });
    lenis.scrollTo(target, { duration: 1.1, onComplete() {
      if (location.hash !== hash) history.pushState(null, '', hash);
      if (!target.matches('a[href],button,input,select,textarea,summary,[tabindex]')) target.setAttribute('tabindex', '-1');
      target.focus({ preventScroll: true });
    } });
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) wake(); });
  // a change to reduced motion mid-visit hands scrolling straight back to the browser
  motion.addEventListener('change', () => { if (!motion.matches) { lenis.destroy(); running = false; } });
  window.__lenis = lenis;
}, () => {}); // if it fails to load, the browser's own scrolling stays
