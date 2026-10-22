/* Local address suggestions, deliberately backed by design fixtures, not a geocoding service. */
(function(global){
 'use strict';
 const examples=[
  {street:'1200 Harbor Boulevard',city:'Ventura',region:'CA',postal:'93001'},
  {street:'64 Lakewood Drive',city:'Los Angeles',region:'CA',postal:'90049'},
  {street:'480 Fairview Avenue',city:'Pasadena',region:'CA',postal:'91101'},
  {street:'820 Willow Street',city:'Long Beach',region:'CA',postal:'90802'},
  {street:'215 Cedar Court',city:'Santa Monica',region:'CA',postal:'90401'},
  {street:'1400 Northgate Drive',city:'Irvine',region:'CA',postal:'92618'}
 ];
 const normalize=text=>String(text||'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();
 const format=address=>`${address.street}, ${address.city}, ${address.region} ${address.postal}`;
 function matches(query){const normalized=normalize(query);if(normalized.length<2)return [];const terms=normalized.split(/\s+/);return examples.filter(a=>terms.every(t=>normalize(format(a)).includes(t))).slice(0,5)}
 if(typeof module!=='undefined'&&module.exports){module.exports={matches,format};return}
 let current=null;
 function close(){if(!current)return;current.panel.remove();current.input.setAttribute('aria-expanded','false');current.input.removeAttribute('aria-activedescendant');current=null}
 function position(){if(!current)return;if(!current.input.isConnected){close();return}const r=current.input.closest('.address-input-wrap').getBoundingClientRect(),height=Math.min(current.panel.scrollHeight,310),width=Math.min(r.width,innerWidth-24);current.panel.style.width=width+'px';current.panel.style.left=Math.max(12,Math.min(r.left,innerWidth-width-12))+'px';current.panel.style.top=(innerHeight-r.bottom>height+15?r.bottom+8:Math.max(12,r.top-height-8))+'px'}
 function highlight(index){if(!current)return;current.index=index;current.panel.querySelectorAll('[role="option"]').forEach((e,i)=>e.classList.toggle('is-active',i===index));current.input.setAttribute('aria-activedescendant','address-suggestion-'+index);current.panel.querySelector('#address-suggestion-'+index)?.scrollIntoView({block:'nearest'})}
 function show(input){
  close();if(input.value.trim().length<2)return;
  const found=matches(input.value),panel=document.createElement('div');panel.className='address-suggestions';panel.id='address-suggestions';panel.setAttribute('role','listbox');panel.setAttribute('aria-label','Suggested addresses');
  panel.innerHTML=found.length?found.map((a,i)=>`<div id="address-suggestion-${i}" role="option" aria-selected="false" data-address-index="${i}"><span class="address-location">${icon('building',18)}</span><span><strong>${esc(a.street)}</strong><span>${esc(a.city)}, ${a.region} ${a.postal}</span></span>${icon('arrow',15)}</div>`).join(''):'<div class="address-no-results" role="presentation">No sample address matches. You can enter the full address manually.</div>';
  panel.insertAdjacentHTML('beforeend','<div class="address-sample-note" role="presentation">Sample suggestions · live lookup is not connected</div>');root.append(panel);current={input,panel,found,index:-1};input.setAttribute('aria-expanded','true');position();
 }
 function choose(index){if(!current?.found[index])return;const input=current.input;input.value=format(current.found[index]);input.dispatchEvent(new Event('input',{bubbles:true}));close();input.focus({preventScroll:true})}
 root.addEventListener('input',e=>{if(e.target.id==='property-address')show(e.target)});
 root.addEventListener('focusin',e=>{if(e.target.id==='property-address'&&e.target.value.trim().length>=2)show(e.target)});
 root.addEventListener('focusout',e=>{if(e.target.id==='property-address')close()});
 root.addEventListener('pointerdown',e=>{if(e.target.closest('[data-address-index]'))e.preventDefault()});
 root.addEventListener('click',e=>{const option=e.target.closest('[data-address-index]');if(option){choose(Number(option.dataset.addressIndex));return}if(e.target.id!=='property-address')close()});
 root.addEventListener('keydown',e=>{
  if(e.target.id!=='property-address'||e.isComposing)return;
  if(e.key==='Escape'&&current){e.preventDefault();e.stopImmediatePropagation();close();return}
  if(e.key==='Tab'){close();return}
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){
   if(!current)show(e.target);if(!current?.found.length)return;e.preventDefault();e.stopImmediatePropagation();highlight(e.key==='ArrowDown'?Math.min(current.index+1,current.found.length-1):current.index<0?current.found.length-1:Math.max(0,current.index-1));return;
  }
  if(e.key==='Enter'&&current?.index>=0){e.preventDefault();e.stopImmediatePropagation();choose(current.index)}
 },true);
 window.addEventListener('resize',position);window.addEventListener('scroll',e=>{if(current&&!current.panel.contains(e.target))position()},true);
 const before=render;render=function(){close();before()};
})(typeof globalThis!=='undefined'?globalThis:this);
