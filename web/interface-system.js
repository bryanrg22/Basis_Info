/* Progressive layout enhancements only: existing mock/live controllers retain ownership. */
(function(){
 'use strict';
 const base=render;
 let lastRoute='',barObserver=null,tableObserver=null,fieldIntent=null;
 root.addEventListener('click',event=>{
  const field=event.target.closest('.appraisal-details [data-live="select-field"],.appraisal-details [data-action="select-field"]');
  if(field)fieldIntent={kind:field.dataset.live?'live':'action',value:field.dataset.value};
 },true);
 function measureBar(){const bar=root.querySelector('.reviewbar');document.documentElement.style.setProperty('--ui-bar-height',(bar?.getBoundingClientRect().height||0)+'px')}
 function enhance(){
  measureBar();barObserver?.disconnect();const bar=root.querySelector('.reviewbar');
  if(bar){barObserver=new ResizeObserver(measureBar);barObserver.observe(bar)}
  const steps=root.querySelector('.study-navigation'),selected=steps?.querySelector('[aria-current="page"]');
  if(selected&&steps.scrollWidth>steps.clientWidth){const at=selected.getBoundingClientRect(),bar=steps.getBoundingClientRect();steps.scrollLeft+=at.left-bar.left-(steps.clientWidth-at.width)/2;}
  const details=root.querySelector('.workspace[data-screen="appraisal"] .appraisal-details');
  if(details&&!details.querySelector('.ui-field-scroll')){
   const rows=[...details.children].filter(el=>el.matches('.field-row,.live-field-section'));
   if(rows.length){const list=document.createElement('div');list.className='ui-field-scroll';list.tabIndex=0;list.setAttribute('role','region');list.setAttribute('aria-label','Extracted appraisal fields');rows[0].before(list);rows.forEach(el=>list.append(el))}
  }
  // Only overflowing horizontal tables need a separate keyboard scroll target.
  // Vertical wheel/trackpad input continues through to the document in CSS.
  tableObserver?.disconnect();
  const syncTable=wrap=>{
   const overflow=wrap.scrollWidth>wrap.clientWidth+1;
   if(overflow){
    if(!wrap.hasAttribute('tabindex')){wrap.tabIndex=0;wrap.dataset.uiScrollTab='true'}
    if(!wrap.hasAttribute('aria-label')&&!wrap.hasAttribute('role')){wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Study data table, scroll horizontally');wrap.dataset.uiScrollRegion='true'}
   }else{
    if(wrap.dataset.uiScrollTab){wrap.removeAttribute('tabindex');delete wrap.dataset.uiScrollTab}
    if(wrap.dataset.uiScrollRegion){wrap.removeAttribute('role');wrap.removeAttribute('aria-label');delete wrap.dataset.uiScrollRegion}
   }
  };
  tableObserver=new ResizeObserver(entries=>{for(const {target} of entries){const wrap=target.closest('.tablewrap');if(wrap)syncTable(wrap)}});
  for(const wrap of root.querySelectorAll('.workspace:not([data-screen="viewer"]) .tablewrap:not(.live-inventory)')){
   syncTable(wrap);tableObserver.observe(wrap);const table=wrap.querySelector('table');if(table)tableObserver.observe(table);
  }
  for(const th of root.querySelectorAll('.workspace:not([data-screen="viewer"]) table thead th'))if(!th.hasAttribute('scope'))th.scope='col';
 }
 render=function(){
  const route=location.pathname+location.search+location.hash,same=route===lastRoute,old=root.querySelector('.ui-field-scroll'),top=old?.scrollTop||0,active=document.activeElement;
  const field=same&&state.screen==='appraisal'?(fieldIntent||(active?.closest('.appraisal-details')?(active.dataset.live?{kind:'live',value:active.dataset.value}:active.dataset.action?{kind:'action',value:active.dataset.value}:null):null)):null;
  fieldIntent=null;
  base();enhance();lastRoute=route;
  const next=root.querySelector('.ui-field-scroll');if(same&&next)next.scrollTop=top;
  if(field){const target=[...root.querySelectorAll(field.kind==='live'?'[data-live="select-field"]':'[data-action="select-field"]')].find(el=>el.dataset.value===field.value);target?.focus({preventScroll:true})}
 };
 addEventListener('resize',measureBar,{passive:true});
 render();
})();
