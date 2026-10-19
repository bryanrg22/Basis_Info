/* Account-menu presentation only. No authentication state is changed here. */
'use strict';
function closeAccountMenu(restoreFocus=false){
 const popup=document.getElementById('account-popover'),trigger=document.getElementById('account-menu-trigger');
 popup?.remove();trigger?.setAttribute('aria-expanded','false');
 if(restoreFocus)trigger?.focus({preventScroll:true});
}
function positionAccountMenu(){
 const popup=document.getElementById('account-popover'),trigger=document.getElementById('account-menu-trigger');if(!popup||!trigger)return;
 const rect=trigger.getBoundingClientRect(),width=Math.min(268,innerWidth-24);
 popup.style.width=width+'px';
 popup.style.left=Math.max(12,Math.min(rect.left,innerWidth-width-12))+'px';
 popup.style.bottom=Math.max(12,Math.min(innerHeight-rect.top+10,innerHeight-popup.offsetHeight-12))+'px';
}
function openAccountMenu(last=false){
 const trigger=document.getElementById('account-menu-trigger');if(!trigger)return;
 clearSidebarHoverTimers();
 const user=previewUser(),popup=document.createElement('div');popup.id='account-popover';popup.className='account-popover';
 popup.innerHTML=`<div class="account-menu-identity"><span class="avatar">${typeof profileAvatar==='function'?profileAvatar(user):user.initials}</span><div><strong>${esc(user.name)}</strong><span>${esc(user.email)}</span></div></div><div class="account-menu-workspace"><span>Harbor Engineering</span><span>${esc(state.role)}</span></div><div role="menu" id="account-menu" aria-label="Your account"><button type="button" role="menuitem" tabindex="-1" id="account-profile">${icon('settings',17)}Profile settings</button><button type="button" role="menuitem" tabindex="-1" id="account-log-out"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 4H4v16h5 M10 12h11 M17 8l4 4-4 4"/></svg>Log out</button></div>`;
 root.append(popup);trigger.setAttribute('aria-expanded','true');positionAccountMenu();const items=popup.querySelectorAll('[role="menuitem"]');items[last?items.length-1:0].focus({preventScroll:true});
}
root.addEventListener('click',e=>{
 if(e.target.closest('#account-menu-trigger')){
  e.preventDefault();document.getElementById('account-popover')?closeAccountMenu(true):openAccountMenu();
 }
 if(e.target.closest('#account-profile')){e.preventDefault();closeAccountMenu(true);openProfileSettings()}
 if(e.target.closest('#account-log-out')){e.preventDefault();closeAccountMenu(true);navigate('login')}
});
document.addEventListener('pointerdown',e=>{
 if(document.getElementById('account-popover')&&!e.target.closest('#account-popover,#account-menu-trigger')){closeAccountMenu();scheduleSidebarClose()}
},true);
document.addEventListener('keydown',e=>{
 const trigger=e.target.closest('#account-menu-trigger'),popup=document.getElementById('account-popover');
 if(trigger&&['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();if(!popup)openAccountMenu(e.key==='ArrowUp');else {const items=popup.querySelectorAll('[role="menuitem"]');items[e.key==='ArrowUp'?items.length-1:0].focus()}return}
 if(!popup)return;
 if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeAccountMenu(true);return}
 if(e.target.closest('#account-menu')&&['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();const items=[...popup.querySelectorAll('[role="menuitem"]')],current=items.indexOf(document.activeElement);const next=e.key==='Home'?0:e.key==='End'?items.length-1:(current+(e.key==='ArrowDown'?1:-1)+items.length)%items.length;items[next].focus()}
 if(e.key==='Tab'){closeAccountMenu(true)}
},true);
document.addEventListener('focusin',e=>{if(document.getElementById('account-popover')&&!e.target.closest('#account-popover,#account-menu-trigger'))closeAccountMenu()});
window.addEventListener('resize',positionAccountMenu);
window.addEventListener('scroll',positionAccountMenu,true);
const renderBeforeAccountMenu=render;
render=function(){closeAccountMenu();renderBeforeAccountMenu()};
