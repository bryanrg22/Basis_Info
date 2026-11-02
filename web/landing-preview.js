/* Public preview uses the same fixture and canvas renderer as photo review.
 * No separate illustration, inference request, or study mutation is involved. */
(function(){
 'use strict';
 const base=render;
 let previews=[];
 const names={kitchen_cabinet:'Cabinets',light_fixture:'Lights',countertop:'Countertop',sink:'Sink',chair:'Chair'};
 function mount(el){
  const img=el.querySelector('.mini-photo img'),status=el.querySelector('.mini-preview-status');
  if(!img||!globalThis.BasisMaskLayer||!globalThis.BasisVision)return ()=>{};
  const items=BasisVision.sampleItems('0');
  let focus='';
  const layer=BasisMaskLayer.attach(img,{onPick:item=>choose(item?.label||'')});
  function choose(label){
   focus=label;
   const visible=items.filter(i=>!label||i.label===label);
   // Overview is the product's existing non-glowing treatment. Filtering keeps the
   // source photo in color and avoids the old fabricated green pendant halo.
   layer.set({instances:visible,mode:'overview',externalPins:true,selectedId:null,hoverId:null});
   el.querySelectorAll('[data-preview-label]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.previewLabel===label)));
   status.textContent=label?`${names[label]||label} · ${visible.length} ${visible.length===1?'object':'objects'}`:'Select a component to inspect it.';
  }
  const click=e=>{const b=e.target.closest('[data-preview-label]');if(b)choose(focus===b.dataset.previewLabel?'':b.dataset.previewLabel)};
  el.addEventListener('click',click);
  choose('');
  return ()=>{layer.destroy();el.removeEventListener('click',click)};
 }
 render=function(){previews.forEach(dispose=>dispose());previews=[];base();previews=[...root.querySelectorAll('.product-preview')].map(mount)};
 render();
})();
