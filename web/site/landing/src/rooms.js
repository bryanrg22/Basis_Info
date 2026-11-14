// Photos sorting themselves into rooms. The study's photos start strewn across the figure among the places a
// study lives today (a shared drive, email threads, a spreadsheet; the group chat has its own section, src/record.js); when the
// section scrolls in they fly to their rooms.
// One photo (a close-up through the patio door: kitchen, living room or outside?) is the kind the
// room step leaves in its Unassigned room rather than guess (backEnd/basis/vision/rooms.py); it
// waits under "Needs placement" while Basis's sorted rooms glow once, then the engineer drags it
// into the kitchen, as the rooms gate allows (web/live/room-placement.js). Phones and reduced
// motion get the end state: the photo in the kitchen with its "Placed by John Doe" tag.
// Pointing at a room (or focusing it) lifts it, traces Basis's outlines on its lead photo and
// lists what they count; a tap does the same on touch. The list is made from the outlines
// themselves, so the counts and the drawing never disagree. On phones one room is open at a
// time, picked from tabs, with its outlines and counts in view.
// Rooms, areas and inventories: the sample study (sample-api.js ROOMS, INVENTORY).
import { ROOMS as SAMPLE_ROOMS, PHOTOS } from './harbor.js';

const EASE = 'cubic-bezier(.22,.61,.36,1)';
const reduce = matchMedia('(prefers-reduced-motion: reduce)');
const phone = matchMedia('(max-width: 700px)');
const TILES = {
  // [photo, crop, zoom, held]: the held tile is the one the engineer places
  kitchen: [['kitchen', '50% 50%', 1], ['kitchen', '88% 46%', 2.2], ['kitchen', '36% 34%', 2.4], ['kitchen', '44% 72%', 2], ['kitchen-detail', '12% 44%', 2.4, true]],
  living: [['living', '50% 50%', 1], ['living', '46% 58%', 2.2], ['living', '24% 44%', 2.4]],
  bedroom: [['bedroom', '50% 50%', 1], ['bedroom', '82% 40%', 2.3], ['bedroom', '30% 62%', 2]],
  exterior: [['exterior', '50% 50%', 1], ['exterior', '50% 92%', 1.9], ['exterior', '76% 44%', 2.1]],
};
const ENGINEER = 'John Doe';
// The outline classes as the app names them (a kitchen's chairs are its bar stools).
const NAMES = { light_fixture: 'Light fixture', outdoor_lighting: 'Outdoor light', countertop: 'Countertop', stove: 'Range', electrical_outlet: 'Electrical outlet', interior_door: 'Interior door', chair: 'Chair', furniture: 'Furniture', hvac_vent: 'Air vent', built_in_shelving: 'Built-in shelving', pool: 'Swimming pool', landscaping: 'Landscaping', patio: 'Patio', roof: 'Roof', kitchen: { chair: 'Bar stool' } };
const MESS = [['Shared drive', 'Harbor House / Photos / IMG_2041–2398'], ['Email thread', 'RE: RE: appraisal_final_v3.pdf'], ['Spreadsheet', 'takeoff (2).xlsx · Sheet3']];

export function mountRooms(root) {
  const base = '../../assets/';
  const ROOMS = SAMPLE_ROOMS;
  root.insertAdjacentHTML('beforeend', `
    <ul class="rooms-mess" aria-hidden="true">${MESS.map(([a, b]) => `<li><b>${a}</b><span>${b}</span></li>`).join('')}</ul>
    <div class="rooms-tabs" role="tablist" aria-label="Rooms">${ROOMS.map((r, i) => `<button type="button" role="tab" id="tab-${r.key}" aria-controls="room-${r.key}" aria-selected="${i === 0}">${r.name}</button>`).join('')}</div>
    <ol class="rooms-list">${ROOMS.map((r, i) => `
      <li class="room${i === 0 ? ' is-picked' : ''}" id="room-${r.key}" data-room="${r.key}" tabindex="0" aria-label="${r.name}">
        <div class="room-head"><h3>${r.name}</h3>${r.area ? `<span>${r.area} SF · floor ${r.floor}</span>` : ''}</div>
        <div class="room-tiles">${TILES[r.key].map(([p, pos, z, held], j) => `<span class="tile${j === 0 ? ' is-lead' : ''}${held ? ' is-held' : ''}"><img src="${base}${PHOTOS[p].src}" alt="" loading="lazy" decoding="async" width="${PHOTOS[p].w}" height="${PHOTOS[p].h}" style="object-position:${pos};transform:scale(${z});transform-origin:${pos}">${held ? `<span class="tile-tag" aria-hidden="true"><span class="tag-long">Placed by </span>${ENGINEER}</span>` : ''}</span>`).join('')}</div>
        <div class="room-inv" aria-hidden="true"><p>Counted in this photo</p><ul></ul></div>
      </li>`).join('')}
    </ol>`);
  const rooms = [...root.querySelectorAll('.room')];
  const tabs = [...root.querySelectorAll('.rooms-tabs button')];
  // busy: the rooms hold still from the moment the photos are strewn until the engineer has
  // placed the last one. Pointing or focusing in that time is remembered (wanted), not shown,
  // and the room still under the pointer lights up once the rooms are free.
  let hot = null, busy = false, wanted = null;
  function show(room) {
    if (hot === room) return;
    hot = room;
    root.classList.toggle('has-hot', !!room);
    for (const r of rooms) { r.classList.toggle('is-hot', r === room); r.classList.toggle('is-masked', r === room); }
    if (!room) return;
    // the list opens just under the lead photo, over the smaller ones, and may run past the card
    // where the card is short (a long list, a narrow window) rather than cover the outlines
    const inv = room.querySelector('.room-inv'), lead = room.querySelector('.tile.is-lead');
    inv.style.top = `${lead.getBoundingClientRect().bottom - room.getBoundingClientRect().top + 6}px`;
    trace(room);
  }
  function want(r) { wanted = r; if (!busy) show(r); }
  const release = () => { busy = false; show(wanted); };
  for (const r of rooms) {
    r.addEventListener('pointerenter', (e) => { if (e.pointerType === 'mouse') want(r); });
    r.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse' && wanted === r) want(null); });
    r.addEventListener('focus', () => { if (r.matches(':focus-visible')) want(r); }); // keyboard; a tap's focus is left to its click
    r.addEventListener('blur', () => { if (wanted === r) want(null); });
    // touch has no hover: a tap does the same, and a second tap puts it away (phones show all of it)
    r.addEventListener('click', (e) => { if (e.pointerType !== 'mouse' && !phone.matches) want(wanted === r ? null : r); });
  }

  // The outlines: Basis's localizer (SAM 3) on each room's lead photo, counted instances only
  // (src/data/room-masks.json, packed by tools/room_masks.py), fetched as the section comes near so
  // the page's first load never waits on them and the first hover draws at once.
  const near = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { near.disconnect(); loadMasks(); } }, { rootMargin: '600px 0px' });
  near.observe(root);
  async function loadMasks() {
    const all = await fetch(new URL('./data/room-masks.json', import.meta.url)).then((res) => res.json()).catch(() => ({}));
    for (const r of rooms) {
      const data = all[r.dataset.room];
      if (!data) continue;
      const { w, h } = PHOTOS[data.photo];
      r.querySelector('.tile.is-lead').insertAdjacentHTML('beforeend', `<svg class="tile-masks" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid slice" aria-hidden="true"><g transform="scale(${w} ${h})">${data.masks.map(([l, d]) => `<path data-l="${l}" d="${d}" pathLength="1"/>`).join('')}</g></svg>`);
      const counts = new Map();
      for (const [l] of data.masks) counts.set(l, (counts.get(l) || 0) + 1);
      const rows = [...counts].sort((x, y) => y[1] - x[1]).map(([l, n]) => [l, (NAMES[r.dataset.room] || {})[l] || NAMES[l] || l, n]);
      r.querySelector('.room-inv ul').innerHTML = rows.map(([l, name, n]) => `<li data-l="${l}"><span>${name}</span><b>${n}</b></li>`).join('');
      r.setAttribute('aria-label', `${r.querySelector('h3').textContent}: ${rows.map(([, name, n]) => `${name} ${n}`).join(', ')}`);
    }
    if (hot) trace(hot);
  }
  // trace each outline quickly, a few at a time, then wash its colour in
  function trace(r) {
    if (reduce.matches || phone.matches) return;
    [...r.querySelectorAll('.tile-masks path')].forEach((p, i) => {
      p.animate([{ strokeDasharray: 1, strokeDashoffset: 1, fillOpacity: 0 }, { strokeDasharray: 1, strokeDashoffset: 0, fillOpacity: 0, offset: .7 }, { strokeDasharray: 1, strokeDashoffset: 0, fillOpacity: 1 }], { duration: 520, delay: Math.min(i, 24) * 14, easing: EASE, fill: 'backwards' });
    });
  }
  // phones: one room at a time
  tabs.forEach((t, i) => t.addEventListener('click', () => {
    tabs.forEach((u, j) => u.setAttribute('aria-selected', String(i === j)));
    rooms.forEach((r, j) => r.classList.toggle('is-picked', i === j));
  }));
  if (reduce.matches || phone.matches) return;

  // The sort: the photos start strewn across the whole figure among the places a study
  // lives today, then fly home to their rooms in a wave (FLIP: the layout is the final one).
  const tiles = rooms.flatMap((r) => [...r.querySelectorAll('.tile')].map((t) => [r, t]));
  const kitchen = rooms.find((r) => r.dataset.room === 'kitchen'), living = rooms.find((r) => r.dataset.room === 'living');
  const held = kitchen.querySelector('.tile.is-held');
  root.classList.add('is-messy');
  busy = true;
  const hold = holdUp(); // where the held photo waits, or null when the layout leaves no room for the tray
  const scatter = () => {
    const R = root.getBoundingClientRect();
    tiles.forEach(([, t], i) => {
      const r = t.getBoundingClientRect();
      const sx = R.width * (.06 + ((i * 0.618) % 1) * .8), sy = R.height * (.12 + ((i * 0.382 + .2) % 1) * .62);
      const dx = sx - (r.left - R.left), dy = sy - (r.top - R.top), rot = ((i * 37) % 24) - 12;
      t.style.transform = `translate(${dx}px,${dy}px) rotate(${rot}deg) scale(.82)`;
    });
  };
  scatter();
  const io = new IntersectionObserver((es) => {
    if (!es.some((e) => e.isIntersecting)) return;
    io.disconnect();
    setTimeout(sort, 750);
  }, { threshold: .35 });
  io.observe(root);
  function sort() {
    root.classList.add('is-sorting');
    const anims = tiles.map(([, t], i) => {
      const from = t.style.transform, to = t === held && hold ? hold.at : 'none';
      t.style.transform = to === 'none' ? '' : to;
      return t.animate([{ transform: from }, { transform: to }], { duration: 900, delay: 250 + i * 70, easing: EASE, fill: 'backwards' }).finished;
    });
    Promise.all(anims).then(() => { root.classList.remove('is-messy', 'is-sorting'); root.classList.add('is-sorted'); if (hold) place(); else release(); }, () => {});
  }

  // The rooms other than the kitchen sit at their own height while the photo waits, so the tray
  // has the space under the living room; measured here, before the photos are strewn.
  function holdUp() {
    root.classList.add('is-holding');
    const R = root.getBoundingClientRect(), K = kitchen.getBoundingClientRect(), L = living.getBoundingClientRect();
    const TH = Math.min(84, K.bottom - L.bottom - 12), top = K.bottom - R.top - TH; // the tray fits the kitchen's extra row
    if (L.left <= K.left || TH < 60) { root.classList.remove('is-holding'); return null; }
    const tray = document.createElement('div');
    tray.className = 'rooms-tray';
    tray.setAttribute('aria-hidden', 'true');
    tray.style.cssText = `left:${L.left - R.left}px;top:${top}px;width:${L.width}px;height:${TH}px`;
    tray.innerHTML = '<span class="tray-slot"></span><span class="tray-text"><b>Needs placement</b><span>1 photo</span></span>';
    root.append(tray);
    const slot = document.createElement('span');
    slot.className = 'tile-slot';
    slot.style.cssText = `left:${held.offsetLeft}px;top:${held.offsetTop}px;width:${held.offsetWidth}px;height:${held.offsetHeight}px`;
    held.parentElement.prepend(slot); // first, so the photo paints over it as it lands
    const s = tray.querySelector('.tray-slot').getBoundingClientRect(), t = held.getBoundingClientRect();
    const at = `translate(${s.left + s.width / 2 - (t.left + t.width / 2)}px,${s.top + s.height / 2 - (t.top + t.height / 2)}px) scale(${s.width / t.width})`;
    return { tray, slot, at };
  }

  // Basis's rooms glow once, then the engineer drags the waiting photo into the kitchen.
  async function place() {
    let stop = false;
    const finish = () => {
      if (stop) return;
      stop = true;
      removeEventListener('resize', finish);
      held.getAnimations().forEach((a) => a.cancel());
      held.style.transform = '';
      held.classList.remove('is-lifted');
      root.classList.add('is-placed');
      root.classList.remove('is-holding');
      cursor.classList.remove('is-on');
      setTimeout(() => { hold.tray.remove(); hold.slot.remove(); cursor.remove(); }, 400);
      release();
    };
    addEventListener('resize', finish); // a new layout would leave the measured path behind
    const wait = (ms) => new Promise((go) => setTimeout(go, ms));
    const R = () => root.getBoundingClientRect();
    const centre = (e) => { const r = e.getBoundingClientRect(), o = R(); return [r.left - o.left + r.width / 2, r.top - o.top + r.height / 2]; };
    const cursor = document.createElement('div');
    cursor.className = 'detect-cursor';
    cursor.setAttribute('aria-hidden', 'true');
    cursor.innerHTML = `<svg viewBox="0 0 20 22" width="20" height="22"><path d="M2 1.5 17 12l-6.6.9L7 19.6z"/></svg><span>${ENGINEER}</span>`;
    root.append(cursor);
    let at = [0, 0];
    const move = (to, ms) => { const a = cursor.animate([{ transform: `translate(${at[0]}px,${at[1]}px)` }, { transform: `translate(${to[0]}px,${to[1]}px)` }], { duration: ms, easing: EASE, fill: 'forwards' }); at = to; return a.finished; };

    const OFF = '0 0 0 0 rgb(95 148 120 / 0), 0 0 0 0 rgb(76 170 120 / 0)', ON = '0 0 0 2px rgb(95 148 120 / .55), 0 0 24px 2px rgb(76 170 120 / .45)', LINE = '#d8d4c8';
    for (const r of rooms) r.animate([{ boxShadow: OFF, borderColor: LINE }, { boxShadow: ON, borderColor: '#5f9478', offset: .3 }, { boxShadow: ON, borderColor: '#5f9478', offset: .5 }, { boxShadow: OFF, borderColor: LINE }], { duration: 1400, easing: 'ease-out' });
    await wait(1150);
    if (stop) return;
    const from = centre(held), tray = hold.tray.getBoundingClientRect(), o = R();
    at = [tray.right - o.left - 24, tray.bottom - o.top + 26];
    cursor.style.transform = `translate(${at[0]}px,${at[1]}px)`;
    cursor.classList.add('is-on');
    await move(from, 650);
    if (stop) return;
    held.classList.add('is-lifted');
    await wait(160);
    if (stop) return;
    held.style.transform = '';
    const to = centre(hold.slot);
    await Promise.all([held.animate([{ transform: hold.at }, { transform: 'none' }], { duration: 850, easing: EASE }).finished, move(to, 850)]).catch(() => {});
    if (stop) return;
    held.classList.remove('is-lifted');
    root.classList.add('is-placed');
    await wait(350);
    if (stop) return;
    // the other rooms take the row's height again
    const others = rooms.filter((r) => r !== kitchen), before = others.map((r) => r.offsetHeight);
    root.classList.remove('is-holding');
    others.forEach((r, i) => r.animate([{ height: `${before[i]}px` }, { height: `${r.offsetHeight}px` }], { duration: 350, easing: EASE }));
    await move([at[0] + 30, at[1] + 34], 500);
    finish();
  }
}

for (const root of document.querySelectorAll('[data-rooms]')) mountRooms(root);
