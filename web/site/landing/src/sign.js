// The signing card: once, as it comes into view, the engineer's signature writes itself on the
// signature line, the DRAFT stamp fades, and the Signed seal settles. Stroke-dashoffset,
// opacity and transform only, so nothing moves the layout. The page's resting state (no script,
// reduced motion, or after the play) is the signed block: the stamp gone, the signature and the
// seal in place. The figure is held at its first frame (.is-hold) only while waiting to play.
const fig = document.querySelector('[data-sign]');
const reduce = matchMedia('(prefers-reduced-motion: reduce)');
const PEN = 'cubic-bezier(.45,.05,.4,1)';

if (fig && !reduce.matches && 'IntersectionObserver' in window && fig.animate) {
  const inks = [...fig.querySelectorAll('.sp-ink')], draft = fig.querySelector('.sp-draft'), seal = fig.querySelector('.sp-seal-in');
  const lens = inks.map((p) => p.getTotalLength());
  inks.forEach((p, i) => { p.style.strokeDasharray = `${lens[i]} ${lens[i]}`; p.style.strokeDashoffset = lens[i]; });
  fig.classList.add('is-hold');
  const play = () => {
    fig.classList.remove('is-hold');
    inks.forEach((p) => { p.style.strokeDashoffset = ''; });
    // timing: the pen starts at once and writes for about 1.35 s; as the last stroke ends the DRAFT
    // stamp fades (300 ms) while the Signed seal presses in over it at the same spot (260 ms, no
    // overshoot), so the block always shows one of the two.
    const total = lens.reduce((a, b) => a + b, 0), WRITE = 900, LIFT = 30, START = 150;
    let t = START;
    inks.forEach((p, i) => {
      const d = Math.max(90, (lens[i] / total) * WRITE);
      p.animate([{ strokeDashoffset: lens[i] }, { strokeDashoffset: 0 }], { duration: d, delay: t, easing: PEN, fill: 'backwards' });
      t += d + LIFT;
    });
    draft.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 300, delay: t - LIFT, easing: 'ease-out', fill: 'backwards' });
    seal.animate([{ opacity: 0, transform: 'rotate(-8deg) scale(1.08)' }, { opacity: 1, transform: 'rotate(-8deg) scale(1)' }],
      { duration: 260, delay: t + 40, easing: 'cubic-bezier(.2,.8,.3,1)', fill: 'backwards' });
  };
  const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { io.disconnect(); play(); } }, { threshold: 0.55 });
  io.observe(fig);
  // switching to reduced motion mid-page: straight to the signed block
  reduce.addEventListener('change', () => { if (!reduce.matches) return; io.disconnect(); fig.classList.remove('is-hold'); inks.forEach((p) => { p.style.strokeDashoffset = ''; }); fig.getAnimations({ subtree: true }).forEach((a) => a.finish()); });
}
