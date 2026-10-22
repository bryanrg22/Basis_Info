/* Shared select-only controls for the local design preview.
   Original selects remain the source of values and existing change events. */
'use strict';
let productChoice=null,productChoiceSerial=0,productTypeahead='',productTypeaheadAt=0;
function productSelectName(select){
 return select.getAttribute('aria-label')||[...select.labels||[]].map(l=>l.textContent.trim()).join(' ')||select.name||'Choose an option';
}
function productSelectKey(select){return select.id||select.dataset.uploadGroup||select.dataset.photo||select.name||'choice-'+(++productChoiceSerial)}
function productUpdateSelect(select){
 const button=select.nextElementSibling;if(!button?.matches('.product-select'))return;
 const value=select.selectedOptions[0]?.textContent||'Choose…',caption=button.querySelector('.product-select-value');if(caption.textContent!==value)caption.textContent=value;button.disabled=select.disabled;
 button.setAttribute('aria-label',productSelectName(select));
 if(select.required)button.setAttribute('aria-required','true');
}
function productEnhanceSelects(){
 for(const select of root.querySelectorAll('.main select:not([multiple]),.modal select:not([multiple])')){
  if(select.dataset.productSelect){productUpdateSelect(select);continue}
  select.dataset.productSelect=productSelectKey(select);select.hidden=true;
  const button=document.createElement('button');button.type='button';button.className='product-select';button.dataset.productSelectKey=select.dataset.productSelect;
  button.setAttribute('role','combobox');button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');button.setAttribute('aria-controls','product-choice-list');
  if(select.getAttribute('aria-describedby'))button.setAttribute('aria-describedby',select.getAttribute('aria-describedby'));
  button.innerHTML='<span class="product-select-value"></span>'+icon('down',14);select.after(button);productUpdateSelect(select);
 }
}
function productCloseChoice(restore=false){
 const choice=productChoice;if(!choice)return;productChoice=null;choice.popup.remove();choice.button.setAttribute('aria-expanded','false');choice.button.removeAttribute('aria-activedescendant');
 if(restore&&choice.button.isConnected)choice.button.focus({preventScroll:true});
}
function productPositionChoice(){
 const c=productChoice;if(!c)return;if(!c.button.isConnected){productCloseChoice();return}
 const r=c.button.getBoundingClientRect(),w=Math.min(Math.max(r.width,210),innerWidth-24),height=Math.min(c.popup.scrollHeight,290,innerHeight-30),below=innerHeight-r.bottom-12;
 c.popup.style.width=w+'px';c.popup.style.maxHeight=Math.min(290,innerHeight-30)+'px';c.popup.style.left=Math.max(12,Math.min(r.left,innerWidth-w-12))+'px';c.popup.style.top=(below>=height?r.bottom+7:Math.max(12,r.top-height-7))+'px';
}
function productHighlight(index){
 const c=productChoice;if(!c)return;const options=[...c.select.options];
 if(!options[index]||options[index].disabled)return;c.index=index;
 c.popup.querySelectorAll('[role="option"]').forEach(o=>o.classList.toggle('is-active',Number(o.dataset.choiceIndex)===index));
 c.button.setAttribute('aria-activedescendant','product-choice-'+index);c.popup.querySelector('#product-choice-'+index)?.scrollIntoView({block:'nearest'});
}
function productOpenChoice(button,edge){
 if(productChoice?.button===button){productCloseChoice();return}productCloseChoice();
 const select=button.previousElementSibling;if(!select||select.disabled)return;
 const popup=document.createElement('div');popup.className='product-choice-list';popup.id='product-choice-list';popup.setAttribute('role','listbox');popup.setAttribute('aria-label',productSelectName(select));
 popup.innerHTML=[...select.options].map((o,i)=>`<div role="option" id="product-choice-${i}" data-choice-index="${i}" aria-selected="${i===select.selectedIndex}" ${o.disabled?'aria-disabled="true"':''}><span>${esc(o.textContent)}</span>${i===select.selectedIndex?icon('check',15):'<span class="product-choice-spacer"></span>'}</div>`).join('');
 root.append(popup);productChoice={button,select,popup,index:select.selectedIndex};button.setAttribute('aria-expanded','true');button.focus({preventScroll:true});productPositionChoice();
 const enabled=[...select.options].map((o,i)=>o.disabled?-1:i).filter(i=>i>=0);productHighlight(edge==='last'?enabled.at(-1):edge==='first'?enabled[0]:select.selectedIndex);
}
function productCommitChoice(){
 const c=productChoice;if(!c)return;const {select,index}=c,changed=select.selectedIndex!==index,key=c.button.dataset.productSelectKey;select.selectedIndex=index;productCloseChoice();productUpdateSelect(select);
 if(changed){select.dispatchEvent(new Event('input',{bubbles:true}));select.dispatchEvent(new Event('change',{bubbles:true}))}
 productEnhanceSelects();[...root.querySelectorAll('.product-select')].find(b=>b.dataset.productSelectKey===key)?.focus({preventScroll:true});
}
root.addEventListener('click',e=>{
 const option=e.target.closest('[data-choice-index]');if(option&&productChoice){if(option.getAttribute('aria-disabled')!=='true'){productHighlight(Number(option.dataset.choiceIndex));productCommitChoice()}return}
 const button=e.target.closest('.product-select');if(button){e.preventDefault();productOpenChoice(button);return}
 const label=e.target.closest('label');if(label&&e.target===label){const select=label.control;if(select?.dataset.productSelect){e.preventDefault();select.nextElementSibling.focus()}}
 if(productChoice&&!e.target.closest('.product-choice-list'))productCloseChoice();
});
root.addEventListener('keydown',e=>{
 const button=e.target.closest('.product-select');if(!button)return;
 // Contain keys before the sidebar, forms and modal handlers see them.
 if(['ArrowDown','ArrowUp','Home','End','Enter',' ','Escape'].includes(e.key)){e.preventDefault();e.stopImmediatePropagation()}
 if(e.key==='Escape'){productCloseChoice(true);return}
 if(e.key==='Tab'){if(productChoice)productCommitChoice();return}
 if(!productChoice){if(['ArrowDown','ArrowUp','Enter',' '].includes(e.key))productOpenChoice(button);return}
 const enabled=[...productChoice.select.options].map((o,i)=>o.disabled?-1:i).filter(i=>i>=0),i=enabled.indexOf(productChoice.index);
 if(e.key==='ArrowDown')productHighlight(enabled[Math.min(i+1,enabled.length-1)]);
 if(e.key==='ArrowUp')productHighlight(enabled[Math.max(0,i-1)]);
 if(e.key==='Home')productHighlight(enabled[0]);if(e.key==='End')productHighlight(enabled.at(-1));
 if(e.key==='Enter'||e.key===' ')productCommitChoice();
 if(e.key.length===1&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&e.key!==' '){e.preventDefault();const now=Date.now();productTypeahead=now-productTypeaheadAt>700?e.key:productTypeahead+e.key;productTypeaheadAt=now;const matched=enabled.find(n=>productChoice.select.options[n].textContent.trim().toLowerCase().startsWith(productTypeahead.toLowerCase()));if(matched!==undefined)productHighlight(matched)}
},true);
window.addEventListener('resize',productPositionChoice);
window.addEventListener('scroll',e=>{if(productChoice&&!productChoice.popup.contains(e.target))productCloseChoice()},true);
function productPolishPage(){
 const workspace=root.querySelector('.workspace');if(!workspace)return;
 if(['processing','complete'].includes(state.screen))root.querySelector('.study-presence')?.remove();
 // Workflow scenarios belong to the review toolbar, outside the product's work area.
 const statebar=root.querySelector('.main .statebar');if(statebar){const details=document.createElement('details');details.className='scenario-menu workflow-scenarios';details.innerHTML='<summary>Workflow states</summary><div></div>';details.lastElementChild.append(statebar);root.querySelector('.reviewbar>.row:last-child')?.prepend(details)}
 if(['assets','complete'].includes(state.screen))root.querySelectorAll('.content table').forEach(t=>t.classList.add('asset-register'));
 for(const strip of root.querySelectorAll('.main .steps,.main .tabs,.main .asset-nav')){
  const selected=strip.querySelector('.active');if(!selected||strip.scrollWidth<=strip.clientWidth)continue;
  const parent=strip.getBoundingClientRect(),item=selected.getBoundingClientRect();strip.scrollLeft+=item.left-parent.left-(strip.clientWidth-item.width)/2;
 }
 root.querySelectorAll('.main .tabs button').forEach(button=>button.setAttribute('aria-pressed',String(button.classList.contains('active'))));
 productEnhanceSelects();
}
const renderBeforeProductPolish=render;
render=function(){productCloseChoice();renderBeforeProductPolish();productPolishPage()};
// Search and intake refresh fragments without rebuilding the page.
const productObserver=new MutationObserver(()=>{if(productChoice&&!productChoice.button.isConnected)productCloseChoice();productEnhanceSelects()});
productObserver.observe(root,{childList:true,subtree:true});
render();
