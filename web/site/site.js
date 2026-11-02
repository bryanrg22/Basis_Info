/* Public interactions only. No platform controllers, auth state, API calls or study data. */
(function(){
 'use strict';
 const header=document.querySelector('.site-header'),menu=document.querySelector('.menu-toggle'),mobile=document.querySelector('#mobile-nav');
 function closeMenu(restore=false){mobile.hidden=true;menu.setAttribute('aria-expanded','false');menu.setAttribute('aria-label','Open navigation');if(restore)menu.focus()}
 menu.addEventListener('click',()=>{const open=menu.getAttribute('aria-expanded')==='true';mobile.hidden=open;menu.setAttribute('aria-expanded',String(!open));menu.setAttribute('aria-label',open?'Open navigation':'Close navigation')});
 mobile.addEventListener('click',e=>{if(e.target.closest('a'))closeMenu()});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!mobile.hidden)closeMenu(true)});
 document.addEventListener('click',e=>{if(!mobile.hidden&&!header.contains(e.target))closeMenu()});
 const onScroll=()=>header.classList.toggle('scrolled',scrollY>24);addEventListener('scroll',onScroll,{passive:true});onScroll();
 const wide=matchMedia('(min-width:801px)');wide.addEventListener('change',e=>{if(e.matches)closeMenu()});
 // Each tab group owns its selection and keyboard cycle.
 document.querySelectorAll('[role="tablist"]').forEach(group=>{
  const tabs=[...group.querySelectorAll('[role="tab"]')];
  function activate(tab,focus=false){
   const changed=tab.getAttribute('aria-selected')!=='true';
   tabs.forEach(t=>{const chosen=t===tab,panel=document.getElementById(t.getAttribute('aria-controls'));t.setAttribute('aria-selected',String(chosen));t.tabIndex=chosen?0:-1;panel.hidden=!chosen;panel.classList.toggle('panel-enter',chosen&&changed)});
   if(focus)tab.focus();
  }
  tabs.forEach((tab,i)=>{
   tab.addEventListener('click',()=>activate(tab));
   tab.addEventListener('keydown',e=>{let next;if(e.key==='ArrowRight')next=(i+1)%tabs.length;else if(e.key==='ArrowLeft')next=(i+tabs.length-1)%tabs.length;else if(e.key==='Home')next=0;else if(e.key==='End')next=tabs.length-1;if(next!==undefined){e.preventDefault();activate(tabs[next],true)}});
  });
 });
 // One entrance per element; content remains visible without JS or motion support.
 const motionPreference=matchMedia('(prefers-reduced-motion: reduce)');
 if('IntersectionObserver' in window&&!motionPreference.matches){
  const entranceObserver=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add('has-entered');entranceObserver.unobserve(entry.target)}}),{threshold:.15});
  document.querySelectorAll('[data-entrance]').forEach(el=>entranceObserver.observe(el));
 }
 document.querySelectorAll('.faqs details').forEach(d=>d.setAttribute('name','questions'));
})();
