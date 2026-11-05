/* Live mode: find a room on Rooms and Components (a 33-room study is a lot of scrolling).
 *
 * A search box above the room cards hides the rooms whose name, type or floor do not match.
 * It only hides cards: drafts, drag and drop and pending corrections are untouched, and the
 * query survives re-renders of the same screen. */
(function(){
 'use strict';
 const C=globalThis.BASIS_CONFIG;if(!C||C.mode!=='live')return;
 const Live=globalThis.BasisLive,L=Live.state;
 L.roomQuery||={};
 const text=card=>[card.querySelector('h2')?.textContent,card.querySelector('.placement-title p, .component-room-meta')?.textContent].join(' ').toLowerCase();
 function apply(screen){
  const grid=root.querySelector('.room-sections');if(!grid)return;
  const terms=(L.roomQuery[screen]||'').toLowerCase().trim().split(/\s+/).filter(Boolean);
  const cards=[...grid.querySelectorAll(':scope > .room-section')];let shown=0;
  for(const card of cards){const ok=terms.every(t=>text(card).includes(t));card.hidden=!ok;if(ok)shown++}
  const count=root.querySelector('#room-filter-count');if(count)count.textContent=terms.length?`${shown} of ${cards.length} rooms`:'';
 }
 Live.afterRender.push(()=>{
  const screen=state.screen;if(!['rooms','components'].includes(screen))return;
  const grid=root.querySelector('.room-sections');if(!grid||root.querySelector('#room-filter'))return;
  if(grid.querySelectorAll(':scope > .room-section').length<6)return;// a handful of rooms needs no search
  grid.insertAdjacentHTML('beforebegin',`<div class="room-filter"><label class="review-search">${icon('search',16)}<input id="room-filter" type="search" placeholder="Find a room by name, type or floor" aria-label="Find a room" autocomplete="off" value="${esc(L.roomQuery[screen]||'')}"></label><span id="room-filter-count" role="status"></span></div>`);
  apply(screen);
 });
 root.addEventListener('input',e=>{if(e.target.id!=='room-filter')return;L.roomQuery[state.screen]=e.target.value;apply(state.screen)});
})();
