// One record. An engineer's day with a study spread across a group chat, mail and a drive: the
// chat fills, faster and faster, until it is buried; a clean X closes it; Basis says what it is
// in large type; and the Harbor House record fills in, each row answering one of the questions
// from the chat (desktop: a line from the question to its row; phones: the question on the row).
//
// Every row is something Basis does, with the sample study's values (web/live/sample-api.js):
// the library is searchable (GET /v1/studies, docs/api/STUDY_LIBRARY.md), the appraisal and the
// photos are uploaded to the study (/documents), the rooms are sorted by Basis (vision/rooms.py),
// every engineer change keeps before, after, who and when (GET .../audit), and each revision
// keeps its signed report (GET .../revisions, .../report). The people are the sample firm's.
//
// The record is the page's content and the finished state: it is what shows without script,
// under reduced motion and to screen readers. The chat is a decorative layer (aria-hidden) laid
// over the same box while it plays, so nothing in the layout moves (CLS 0); only transform and
// opacity animate, and the pile fades as one element.

const EASE = 'cubic-bezier(.22,.61,.36,1)';
const reduce = matchMedia('(prefers-reduced-motion: reduce)');
const wide = matchMedia('(min-width: 981px)');

const PEOPLE = { SR: 'Sam Rivera', JL: 'Jordan Lee', AM: 'Alex Morgan', MC: 'Mike Chen' };
// The six questions the record answers, in the order the chat asks them, with the noise between.
const Q = [['SR', 'Which study? The one on Ocean Ave?'], ['JL', 'Where’s the appraisal for Harbor House?'], ['SR', 'The photos are still on Mike’s phone'],
  ['JL', 'Which photo is the primary bedroom?'], ['AM', 'Who changed the pendant count?'], ['SR', 'Which version did the client get?']];
const N = (who, text) => [who, text];
const F = (who, name) => [who, name, true]; // a file sent to the chat
const CHAT = [
  Q[0], F('MC', 'photos.zip · 48 MB'), Q[1], F('MC', 'photos (1).zip · 48 MB'), F('JL', 'Harbor_House_Appraisal_FINAL.pdf'),
  Q[2], F('AM', 'Harbor_House_Appraisal_FINAL_v2.pdf'), N('SR', 'wrong version'), Q[3], N('AM', '?'), N('JL', 'bump'), Q[4],
  N('SR', 'anyone?'), N('AM', 'which study??'), Q[5], N('MC', 'sending again'), N('JL', '??'), N('SR', 'bump'),
  N('SR', 'which study??'), N('AM', 'bump'), N('JL', 'anyone?'), F('MC', 'photos (2).zip · 48 MB'), N('SR', '?'),
  F('JL', 'Harbor_House_Appraisal_FINAL.pdf'), N('AM', 'wrong version'), N('SR', '@Mike'), N('JL', 'still waiting'),
  N('AM', 'which one??'), N('SR', 'bump'), N('MC', 'sending again'), N('JL', '?'), F('AM', 'Harbor_House_Appraisal_FINAL_v3.pdf'),
  N('SR', 'anyone??'), N('JL', 'which study??'), N('AM', '??'), N('SR', 'wrong version'),
];
const TOASTS = [[12, 'Mail', 'RE: RE: RE: Harbor House appraisal'], [18, 'Drive', 'Mike Chen shared “Harbor House photos (2)”'],
  [24, 'Missed call', 'Jordan Lee'], [29, 'Mail', 'Fwd: which version went to the client?']];
const CALM = 5; // the first few arrive like a normal chat, one under the other

// a small deterministic generator, so the pile lands the same way every time
function rng(seed) { return () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; }; }

export function mountRecord(root) {
  const say = root.querySelector('.say'), answer = root.querySelector('.answer'), svg = root.querySelector('.links');
  const asks = [...root.querySelectorAll('.asks .msg')], rows = [...root.querySelectorAll('.rec-row')];
  const replay = root.querySelector('.onerec-replay');
  const NS = 'http://www.w3.org/2000/svg';
  let links = [];

  // Desktop: each question sits level with its row, and a line runs from the bubble to the row.
  function layout() {
    root.classList.toggle('is-wide', wide.matches);
    svg.replaceChildren();
    links = [];
    if (!wide.matches) return;
    const A = answer.getBoundingClientRect();
    asks.forEach((m, i) => {
      const r = rows[i].getBoundingClientRect();
      m.style.top = `${r.top - A.top + r.height / 2 - m.offsetHeight / 2}px`;
    });
    asks.forEach((m, i) => {
      const b = m.querySelector('.msg-b').getBoundingClientRect(), r = rows[i].getBoundingClientRect();
      const x1 = b.right - A.left, y1 = b.top - A.top + b.height / 2, x2 = r.left - A.left, y2 = r.top - A.top + r.height / 2;
      const pend = rows[i].classList.contains('is-pending') ? ' is-pending' : '';
      const g = document.createElementNS(NS, 'g');
      g.setAttribute('class', pend.trim());
      g.innerHTML = `<path pathLength="1" d="M${x1} ${y1} C ${x1 + 22} ${y1}, ${x2 - 22} ${y2}, ${x2} ${y2}"/><circle cx="${x2}" cy="${y2}" r="3.5"/>`;
      svg.append(g);
      links.push(g);
    });
  }
  let frame = 0;
  addEventListener('resize', () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(layout); });
  layout();
  if (reduce.matches) return;

  root.classList.add('is-motion');
  replay.hidden = false;
  let run = 0, stage = null;
  const wait = (ms, token) => new Promise((go, stop) => setTimeout(() => (token === run ? go() : stop()), ms));
  const pending = (on) => {
    rows.forEach((r) => { r.classList.toggle('is-pending', on); r.classList.remove('is-new'); });
    asks.forEach((m) => m.classList.toggle('is-pending', on));
    links.forEach((g) => g.classList.toggle('is-pending', on));
  };

  function finish() {
    run++;
    watch?.disconnect();
    stage?.remove();
    stage = null;
    say.getAnimations().forEach((a) => a.cancel());
    answer.getAnimations().forEach((a) => a.cancel());
    root.classList.remove('is-playing');
    pending(false);
  }

  // The chat's stage: on desktop the whole box, the chat window at its left and the pile spilling
  // across it; on phones a phone-sized chat over the line and the top of the record, whose rows
  // are still empty under it.
  function build() {
    const W = root.clientWidth, H = wide.matches ? answer.offsetTop + answer.offsetHeight : Math.min(480, root.clientHeight); // desktop: down to the record's last row, not the Replay button
    const cw = wide.matches ? 420 : W, ch = wide.matches ? Math.min(620, H - 20) : H;
    const small = innerWidth <= 560; // the phone sizes in paper.css
    const bw = small ? 240 : 300, tw = small ? 270 : 300; // a bubble's and an alert's widest
    const el = document.createElement('div');
    el.className = 'chat-stage';
    el.setAttribute('aria-hidden', 'true');
    el.style.height = `${H}px`;
    const rand = rng(11);
    // the pile: a jittered grid over the region, so it buries the whole of it
    const region = wide.matches ? [W, H] : [cw, ch];
    const cols = wide.matches ? 6 : 2, rowsN = Math.ceil((CHAT.length - CALM) / cols);
    const cells = [];
    for (let r = 0; r < rowsN; r++) for (let c = 0; c < cols; c++) cells.push([c, r]);
    for (let i = cells.length - 1; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [cells[i], cells[j]] = [cells[j], cells[i]]; }
    const cellW = (region[0] - bw) / Math.max(cols - 1, 1), cellH = (region[1] - 140) / rowsN;
    const msgs = CHAT.map(([who, text, file], n) => {
      let x, y, rot;
      if (n < CALM) { x = 14; y = 72 + n * 70; rot = 0; } else {
        const [c, r] = cells[n - CALM];
        x = Math.min(Math.max(c * cellW + (rand() - .5) * cellW * .7, 0), region[0] - bw);
        y = 76 + r * cellH + (rand() - .5) * cellH * .6;
        rot = (rand() - .5) * 12;
      }
      const body = file ? `<span class="att"><i>${text.includes('.pdf') ? 'PDF' : 'ZIP'}</i>${text}</span>` : text;
      return `<li class="msg${n < CALM ? ' is-calm' : ''}" data-r="${rot.toFixed(1)}" style="left:${x.toFixed(0)}px;top:${y.toFixed(0)}px;z-index:${n + 1}"><span class="av av-${who.toLowerCase()}">${who}</span><span class="msg-b"><b>${PEOPLE[who]}</b>${body}</span></li>`;
    }).join('');
    const tpos = wide.matches
      ? [[W * .66, 30, 1.5], [W * .40, H * .32, -2], [W * .70, H * .52, 2.5], [W * .30, H * .74, -1]]
      : [[16, 70, -2], [40, ch * .42, 2], [8, ch * .70, -1.5], [30, ch * .86, 1]];
    const toasts = TOASTS.map(([, a, t], i) => `<div class="toast" data-r="${tpos[i][2]}" style="left:${Math.min(tpos[i][0], region[0] - tw).toFixed(0)}px;top:${tpos[i][1].toFixed(0)}px;z-index:${60 + i}"><b>${a}</b>${t}</div>`).join('');
    el.innerHTML = `<div class="chat-win" style="width:${cw}px;height:${ch}px"><div class="chat-head"><span class="chat-badge">0</span><b>Harbor House</b><i>Alder &amp; Pine · 4 people</i><span class="chat-x"><svg viewBox="0 0 16 16" width="16" height="16"><path d="M3 3l10 10M13 3 3 13"/></svg></span></div><span class="chat-typing">Sam, Jordan and Mike are typing…</span></div><ol class="pile">${msgs}${toasts}</ol>`;
    root.prepend(el);
    return el;
  }


  // one row answers its question: the line draws to it (desktop), the row fills, the question is checked
  async function fill(i, token) {
    const g = links[i];
    if (g) {
      g.classList.remove('is-pending');
      g.querySelector('path').animate([{ strokeDasharray: 1, strokeDashoffset: 1 }, { strokeDasharray: 1, strokeDashoffset: 0 }], { duration: 380, easing: EASE });
      g.querySelector('circle').animate([{ transform: 'scale(0)' }, { transform: 'scale(0)', offset: .75 }, { transform: 'scale(1)' }], { duration: 460, easing: EASE });
      await wait(300, token);
    }
    rows.forEach((r) => r.classList.remove('is-new'));
    rows[i].classList.remove('is-pending');
    rows[i].classList.add('is-new');
    asks[i].classList.remove('is-pending');
  }
  // each row fills as it comes into view, in order, so none is answered off screen (the record is
  // taller than a phone, and its last rows sit below the fold of a laptop)
  let watch = null;
  function fillOnScroll(token) {
    return new Promise((done, stop) => {
      let next = 0, busy = false;
      const seen = new Set();
      const step = async () => {
        if (busy) return;
        busy = true;
        try {
          while (next < rows.length && seen.has(rows[next])) { await fill(next, token); next++; await wait(wide.matches ? 480 : 380, token); }
        } catch { watch?.disconnect(); return stop(); }
        busy = false;
        if (next === rows.length) { watch.disconnect(); done(); }
      };
      watch = new IntersectionObserver((es) => { for (const e of es) if (e.isIntersecting) seen.add(e.target); step(); }, { rootMargin: wide.matches ? '0px' : '0px 0px -18% 0px', threshold: .6 }); // phones: well into the screen, as the reader scrolls
      rows.forEach((r) => watch.observe(r));
    });
  }

  async function play() {
    finish();
    const token = run;
    root.classList.add('is-playing');
    pending(true);
    stage = build();
    const pile = stage.querySelector('.pile'), badge = stage.querySelector('.chat-badge'), x = stage.querySelector('.chat-x');
    const items = [...pile.querySelectorAll('.msg')], toasts = [...pile.querySelectorAll('.toast')];
    // a pop, never half-transparent, so every bubble reads at full contrast
    const pop = (el) => { const r = `rotate(${el.dataset.r}deg)`; el.animate([{ opacity: 1, transform: `${r} translateY(8px) scale(.55)` }, { opacity: 1, transform: `${r} scale(1.05)`, offset: .6 }, { opacity: 1, transform: r }], { duration: 240, easing: EASE, fill: 'forwards' }); };
    try {
      // 1. the chat fills, then buries itself: one message every half second, then faster
      let gap = 520, unread = 0;
      for (let n = 0; n < items.length; n++) {
        pop(items[n]);
        badge.textContent = String(unread += n < CALM ? 1 : 2);
        const t = TOASTS.findIndex(([at]) => at === n);
        if (t >= 0) pop(toasts[t]);
        if (n === items.length - 8) stage.querySelector('.chat-typing').classList.add('is-on');
        if (n >= CALM - 1) gap = Math.max(48, gap * .82);
        await wait(gap, token);
      }
      await wait(500, token);
      // 2. a clean X closes it
      x.classList.add('is-pressed');
      x.animate([{ transform: 'scale(1)' }, { transform: 'scale(.86)' }, { transform: 'scale(1.06)' }, { transform: 'scale(1)' }], { duration: 320, easing: 'ease-out' });
      await wait(220, token);
      // everything folds into the X: a shrink, never a fade, so nothing is ever half-transparent
      const S = stage.getBoundingClientRect(), X = x.getBoundingClientRect();
      stage.style.transformOrigin = `${X.left - S.left + X.width / 2}px ${X.top - S.top + X.height / 2}px`;
      await stage.animate([{ transform: 'none' }, { transform: 'scale(.02)' }], { duration: 460, easing: 'cubic-bezier(.55,0,.75,.2)', fill: 'forwards' }).finished;
      if (token !== run) throw 0;
      stage.style.visibility = 'hidden';
      await wait(300, token);
      stage.remove();
      stage = null;
      // 3. what Basis is, large, in the cleared space
      const s = wide.matches ? 1.22 : 1;
      const ty = wide.matches ? Math.max(0, root.clientHeight * .36 - (say.offsetHeight * s) / 2) : 0;
      const big = `translate(0,${ty}px) scale(${s})`;
      say.animate([{ opacity: 0, transform: `translate(0,${ty + 26}px) scale(${s})` }, { opacity: 1, transform: big }], { duration: 700, easing: EASE, fill: 'forwards' });
      await wait(2100, token);
      // 4. the line moves up, and the record answers the chat one row at a time
      say.animate([{ opacity: 1, transform: big }, { opacity: 1, transform: 'none' }], { duration: 650, easing: EASE, fill: 'forwards' });
      // desktop: the record arrives (on phones it has been in view, empty, all along)
      if (wide.matches) answer.animate([{ opacity: 0, transform: 'translateY(16px)' }, { opacity: 1, transform: 'none' }], { duration: 600, delay: 250, easing: EASE, fill: 'both' });
      root.classList.remove('is-playing');
      await wait(900, token);
      await fillOnScroll(token);
      await wait(900, token);
      rows.forEach((r) => r.classList.remove('is-new'));
      finish();
    } catch { /* replaced by a newer run, a resize or reduced motion */ }
  }

  replay.addEventListener('click', play);
  // a new width leaves the pile and the lines behind; a phone's toolbar changing the height does not
  let width = innerWidth;
  addEventListener('resize', () => { if (innerWidth === width) return; width = innerWidth; if (stage || root.classList.contains('is-playing')) finish(); });
  reduce.addEventListener('change', () => { if (reduce.matches) { finish(); replay.hidden = true; } });
  // it plays once the chat's stage is in view; until then the record waits empty
  pending(true);
  root.classList.add('is-playing');
  // It starts once the stage is on screen whole under the nav: on desktop the heading down to the
  // record's last row, on phones the heading and the chat. Checked on scroll, once a frame at most.
  const head = (root.closest('section') || root).querySelector('h2'), NAV = 96;
  let ticking = false;
  const ready = () => {
    const top = head.getBoundingClientRect().top;
    const bottom = wide.matches ? answer.getBoundingClientRect().bottom : root.getBoundingClientRect().top + Math.min(480, root.clientHeight);
    return bottom <= innerHeight + 2 || (bottom - top > innerHeight - NAV && top <= NAV);
  };
  const check = () => {
    ticking = false;
    if (!ready()) return;
    removeEventListener('scroll', onScroll);
    if (root.getBoundingClientRect().bottom < NAV) { finish(); return; } // jumped past it
    play();
    // scrolled away mid-run (a nav link's smooth scroll passes through it): no one is watching,
    // so it shows the finished record instead of playing on off screen
    const away = new IntersectionObserver((es) => {
      if (es.some((e) => e.isIntersecting)) return;
      away.disconnect();
      if (root.classList.contains('is-playing')) finish();
    });
    away.observe(root);
  };
  const onScroll = () => { if (!ticking) { ticking = true; requestAnimationFrame(check); } };
  addEventListener('scroll', onScroll, { passive: true });
  onScroll();
}

for (const root of document.querySelectorAll('[data-onerec]')) mountRecord(root);
