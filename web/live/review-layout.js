/* Presentation only: grouping, disclosure and search. Review actions stay in live-review. */
(function(global){
 'use strict';
 const Live=global.BasisLive;if(!Live)return;
 const P=global.BasisReviewOrganization,states=new Map();
 const money=v=>P.cost(v)===null?'—':Number(v).toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:Number(v)>=1000?0:2});
 const disclosure=label=>`<span class="study-disclosure"><span class="when-closed">Show ${label}</span><span class="when-open">Hide ${label}</span><span class="disclosure-chevron">${icon('chevron',16)}</span></span>`;
 const plural=(n,word)=>`${n} ${word}${n===1?'':word.endsWith('class')?'es':'s'}`;
 const getState=(kind,study)=>{const key=`${kind}:${study.id}:${study.revision}`;if(!states.has(key))states.set(key,{query:'',filter:'all',groupBy:'recovery',open:new Set(kind==='fields'?['overview']:[])});return states.get(key)};
 let assetContext=null,fieldContext=null;
 function results({items,study}){
  const view=P.assetView(items,{groupBy:'recovery'}),total=view.summary;
  return `<section class="results-overview"><div class="results-total"><span>Total allocated cost</span><strong>${total.priced?money(total.total):'—'}</strong><small>${plural(total.count,'asset')} · ${plural(view.groups.length,'recovery class')}${total.unpriced?` · ${total.unpriced} unpriced`:''}</small></div><div class="results-groups">${view.groups.map(group=>`<details class="result-class"><summary><span><strong>${esc(group.name)}</strong><small>${plural(group.stats.count,'asset')}${group.stats.unpriced?` · ${group.stats.unpriced} unpriced`:''}</small></span><span class="result-class-cost"><strong>${group.stats.priced?money(group.stats.total):'—'}</strong><small>${group.stats.priced&&total.total?Math.round(group.stats.total/total.total*100)+'% of priced cost':group.stats.priced?'':'Not priced yet'}</small></span>${disclosure('assets')}</summary><div class="asset-group-table tablewrap"><table><caption class="sr-only">${esc(group.name)} assets</caption><thead><tr><th scope="col">Component</th><th scope="col">Room</th><th scope="col" class="num">Quantity</th><th scope="col" class="num">Total cost</th></tr></thead><tbody>${group.items.map(a=>`<tr><td><strong>${esc(a.label?Live.labelName(a.label):a.category)}</strong><p>${esc(a.category||'')}</p></td><td>${esc(a.room?.name||'Unassigned')}</td><td class="num">${a.takeoff?`${esc(Number(a.takeoff.quantity).toLocaleString('en-US'))} ${esc(a.takeoff.unit)}`:'—'}</td><td class="num strong">${P.cost(a.cost?.total)===null?'Unpriced':money(a.cost.total)}</td></tr>`).join('')}</tbody></table></div></details>`).join('')||'<div class="review-empty"><h3>No assets yet</h3><p>The allocation will appear when assets are ready.</p></div>'}</div></section>`;
 }
 function assetRows(group,s){
  return `<div class="asset-group-table tablewrap"><table><thead><tr><th scope="col">Asset</th><th scope="col">Classification</th><th scope="col" class="num">Quantity</th><th scope="col" class="num">Cost</th><th scope="col">Evidence & review</th><th scope="col"><span class="sr-only">Open asset</span></th></tr></thead><tbody>${group.items.map(a=>`<tr><td><strong>${esc(a.label?Live.labelName(a.label):a.category)}</strong><p>${esc(a.category||'Unclassified')}${a.count!==null&&a.count!==undefined?` · ${a.count} counted`:''}</p>${a.edited?'<span class="live-edited">Correction added</span>':''}${group.bucket?`<p>${esc(a.room?.name||'Unassigned')}</p>`:''}</td><td><span class="badge">${esc(P.buckets[a.bucket]||a.bucket||'Unclassified')}</span><p>${a.irs_section?'§ '+esc(a.irs_section):'—'}</p></td><td class="num">${a.takeoff?`${esc(Number(a.takeoff.quantity).toLocaleString('en-US'))} <span class="muted">${esc(a.takeoff.unit)}</span>`:'—'}</td><td class="num"><strong>${P.cost(a.cost?.total)===null?'<span class="asset-unpriced">Unpriced</span>':money(a.cost.total)}</strong><p>${P.cost(a.cost?.unit_cost)!==null?money(a.cost.unit_cost)+' / '+esc(a.takeoff?.unit||'unit'):''}</p></td><td><span class="badge ${a.needs_review?'review':'complete'}" title="${esc(BasisReasons.title(a.review_reasons||[]))}">${a.needs_review?'Needs review':'No flags'}</span><p>${a.verified_citations??0}/${a.citations??0} citations verified</p></td><td><a class="button small" href="${Live.route('asset-detail',s.id,a.id)}" aria-label="Review ${esc(a.label?Live.labelName(a.label):a.category)} in ${esc(a.room?.name||'Unassigned')}">Review ${icon('chevron',12)}</a></td></tr>`).join('')}</tbody></table></div>`;
 }
 function assetGroupsHtml(context){
  const {items,study,rooms,corrections,ui}=context,view=P.assetView(items,{...ui,corrections});
  return `<div class="asset-results-caption"><span>${plural(view.matching.count,'asset')} in ${plural(view.groups.length,ui.groupBy==='room'?'room':'class')}${ui.query||ui.filter!=='all'?` · ${items.length} total`:''}</span><span>Expand a ${ui.groupBy==='room'?'room':'class'} to review its assets</span></div><div class="asset-groups">${view.groups.map(group=>{
   const room=rooms.find(r=>r.id===group.roomId),photo=room?.photos?.[0],stats=group.stats;
   return `<details class="asset-group" data-review-group="${esc(group.key)}" ${ui.open.has(group.key)?'open':''}><summary><span class="asset-group-photo">${photo?Live.photo(photo.document_id,room.name,''):icon(ui.groupBy==='room'?'home':'file',22)}</span><span class="asset-group-name"><strong>${esc(group.name)}</strong><span>${plural(stats.count,'asset')}${room?.photos?.length?` · ${plural(room.photos.length,'photo')}`:''}</span></span><span class="asset-group-review ${stats.review?'has-flags':''}">${stats.review?plural(stats.review,'asset')+' to review':'No review flags'}${stats.unpriced?`<small>${stats.unpriced} unpriced</small>`:''}</span><span class="asset-group-total"><strong>${stats.priced?money(stats.total):'—'}</strong><span>Priced cost</span></span>${disclosure('assets')}</summary>${assetRows(group,study)}</details>`;
  }).join('')||'<div class="review-empty"><h3>No matching assets</h3><p>Try another search or clear the filters.</p><button class="button small" data-review-action="clear-assets">Clear filters</button></div>'}</div>`;
 }
 function assets({items,study,rooms=[],corrections}){
  const ui=getState('assets',study);assetContext={items,study,rooms,corrections,ui};
  const view=P.assetView(items,{corrections}),s=view.summary,open=Live.gateOpen?Live.gateOpen('assets_review'):true;
  return `<section class="asset-overview" aria-label="Organized asset review"><div class="asset-summary"><div class="asset-summary-total"><span>Priced asset cost</span><strong>${s.priced?money(s.total):'—'}</strong><small>${s.priced} of ${s.count} assets priced</small></div><div><span>Assets</span><strong>${s.count}</strong></div><div><span>${open?'Need review':'Flagged'}</span><strong class="${s.review&&open?'has-flags':''}">${s.review}</strong></div><div><span>Unpriced</span><strong>${s.unpriced}</strong></div></div><div class="asset-classes" aria-label="Recovery classes">${view.classes.map(c=>`<div><span>${esc(c.name)}</span><strong>${c.priced?money(c.total):'Unpriced'}</strong><small>${plural(c.count,'asset')}${c.unpriced&&c.priced?` · ${c.unpriced} unpriced`:''}</small></div>`).join('')}</div><div class="asset-review-toolbar"><label class="review-search">${icon('search',16)}<input id="asset-review-search" type="search" placeholder="Search assets or rooms" aria-label="Search assets or rooms" value="${esc(ui.query)}"></label><div class="review-filter-tabs" role="group" aria-label="Asset status">${[['all','All',s.count],['review',open?'Needs review':'Flagged',s.review],['unpriced','Unpriced',s.unpriced],['ok','No flags',s.count-s.review]].map(([key,label,count])=>`<button data-review-filter="${key}" aria-pressed="${ui.filter===key}">${label}<span>${count}</span></button>`).join('')}</div><label class="asset-group-select">Group by<select id="asset-review-group" aria-label="Group assets by"><option value="room" ${ui.groupBy==='room'?'selected':''}>Room</option><option value="recovery" ${ui.groupBy==='recovery'?'selected':''}>Recovery class</option></select></label></div><div id="asset-review-results">${assetGroupsHtml(assetContext)}</div></section>`;
 }
 function fieldButton(f,featured,context){
  const c=context.corrections.get(f.id),selected=f.id===context.selected;
  return `<button class="field-row ${featured?'property-fact':''} ${P.fieldKey(f)==='subject.property_address'?'property-address':['subject.city','subject.state','subject.zip'].includes(P.fieldKey(f))?'property-location':''} ${selected?'selected':''}" data-live="select-field" data-value="${esc(f.id)}" aria-pressed="${selected}"><span class="field-label">${esc(featured?P.fieldLabel(f):f.label||Live.labelName(f.field))}${f.source==='mismo_xml'?' <small class="field-origin" title="Value extracted from MISMO XML">XML</small>':''}</span><strong>${esc(c?c.display:P.fieldValue(f))}${c?'<small>Correction added</small>':''}${P.needsLook(f)?`<small class="${context.canCorrect?'':'is-retained'}">${context.canCorrect?'Needs a look':'Low confidence'}</small>`:''}</strong><span class="page-tag">${(globalThis.BasisAppraisalSource?.page(f)||f.page)?'p. '+(globalThis.BasisAppraisalSource?.page(f)||f.page):'—'}</span></button>`;
 }
 function fieldList(context){
  const {fields,ui,corrections}=context,groups=P.fieldGroups(fields,{...ui,corrections}),matching=groups.reduce((n,g)=>n+g.fields.length,0),searching=!!ui.query||ui.filter!=='all';
  return `${searching?`<p class="field-results-count" role="status">${matching} of ${fields.length} fields</p>`:''}${groups.map(group=>`<details class="field-group" data-field-group="${esc(group.key)}" ${searching||ui.open.has(group.key)?'open':''}><summary><span><strong>${esc(group.name)}</strong><small>${plural(group.fields.length,'field')}${group.review?` · ${group.review} need a look`:''}</small></span>${disclosure('fields')}</summary><div class="${group.key==='overview'?'property-facts':'field-group-rows'}">${group.fields.map(f=>fieldButton(f,group.key==='overview',context)).join('')}</div></details>`).join('')||'<div class="review-empty"><h3>No matching fields</h3><p>Try another search or clear the filters.</p><button class="button small" data-review-action="clear-fields">Clear filters</button></div>'}`;
 }
 function nextFieldGroup(context){
  const groups=P.fieldGroups(context.fields,{...context.ui,corrections:context.corrections});
  const current=groups.findIndex(g=>g.fields.some(f=>f.id===context.selected));
  if(current<0)return groups.length?{group:groups[0],wrap:false}:null;
  if(current+1<groups.length)return {group:groups[current+1],wrap:false};
  return groups.length>1?{group:groups[0],wrap:true}:null;
 }
 function fieldActions(context){
  const next=nextFieldGroup(context);
  return `${next?`<button class="field-next-section" data-review-action="next-field-section"><span><small>${next.wrap?'Back to':'Next section'}</small><strong>${esc(next.group.name)}</strong></span>${icon('chevron',16)}</button>`:''}${context.canCorrect?`<button class="button small" data-live="correct-field" data-value="${esc(context.selected)}">Correct value</button>`:''}`;
 }
 function appraisal({fields,study,selected,corrections,canCorrect}){
  const ui=getState('fields',study),selectedGroup=P.fieldGroups(fields).find(g=>g.fields.some(f=>f.id===selected));
  if(selectedGroup)ui.open.add(selectedGroup.key);
  fieldContext={fields,study,selected,corrections,canCorrect,ui};
  return `<section class="panel appraisal-details appraisal-organized"><div class="panel-head"><h2>Property details</h2><span class="small">${fields.length} extracted fields</span></div><div class="field-browser-controls"><label class="review-search">${icon('search',16)}<input id="appraisal-field-search" type="search" placeholder="Find a field or value" aria-label="Find an appraisal field" value="${esc(ui.query)}"></label><div class="field-filter-row"><select id="appraisal-field-filter" aria-label="Filter appraisal fields">${[['all','All fields'],['review',`${canCorrect?'Needs a look':'Low confidence'} (${fields.filter(P.needsLook).length})`],['critical',`Key fields (${fields.filter(f=>f.critical).length})`]].map(([v,label])=>`<option value="${v}" ${ui.filter===v?'selected':''}>${label}</option>`).join('')}</select></div></div><div class="ui-field-scroll" tabindex="0" role="region" aria-label="Extracted appraisal fields">${fieldList(fieldContext)}</div><div class="field-review-actions">${fieldActions(fieldContext)}</div></section>`;
 }
 // Only replace the left list for local filtering; never rebuild the source PDF.
 function updateFields(){
  const list=root.querySelector('.appraisal-organized .ui-field-scroll');if(!list||!fieldContext)return;
  list.innerHTML=fieldList(fieldContext);list.scrollTop=0;
  const actions=root.querySelector('.appraisal-organized .field-review-actions');if(actions)actions.innerHTML=fieldActions(fieldContext);
 }
 function renderAssetState(){Live.render()}
 root.addEventListener('input',event=>{
  if(event.target.id==='appraisal-field-search'&&fieldContext){fieldContext.ui.query=event.target.value;updateFields()}
  if(event.target.id==='asset-review-search'&&assetContext){
   const value=event.target.value,position=event.target.selectionStart;assetContext.ui.query=value;renderAssetState();const input=root.querySelector('#asset-review-search');input?.focus({preventScroll:true});if(input&&position!==null)input.setSelectionRange(position,position);
  }
 });
 root.addEventListener('change',event=>{
  if(event.target.id==='asset-review-group'&&assetContext){assetContext.ui.groupBy=event.target.value;renderAssetState()}
  if(event.target.id==='appraisal-field-filter'&&fieldContext){fieldContext.ui.filter=event.target.value;updateFields()}
 });
 root.addEventListener('click',event=>{
  const filter=event.target.closest('[data-review-filter]');if(filter&&assetContext){assetContext.ui.filter=filter.dataset.reviewFilter;renderAssetState();root.querySelector(`[data-review-filter="${filter.dataset.reviewFilter}"]`)?.focus({preventScroll:true})}
  const action=event.target.closest('[data-review-action]')?.dataset.reviewAction;
  if(action==='next-field-section'&&fieldContext){
   const next=nextFieldGroup(fieldContext);if(!next)return;
   fieldContext.ui.open.add(next.group.key);
   Live.actions['select-field'](null,next.group.fields[0].id);
   const group=[...root.querySelectorAll('[data-field-group]')].find(el=>el.dataset.fieldGroup===next.group.key);
   const list=root.querySelector('.appraisal-organized .ui-field-scroll');
   if(group&&list){list.scrollTop+=group.getBoundingClientRect().top-list.getBoundingClientRect().top-10;group.querySelector('summary')?.focus({preventScroll:true})}
  }
  if(action==='clear-assets'&&assetContext){assetContext.ui.query='';assetContext.ui.filter='all';renderAssetState()}
  if(action==='clear-fields'&&fieldContext){fieldContext.ui.query='';fieldContext.ui.filter='all';root.querySelector('#appraisal-field-search').value='';root.querySelector('#appraisal-field-filter').value='all';updateFields()}
 });
 root.addEventListener('toggle',event=>{
  const el=event.target;if(!el.isConnected)return;
  const key=el.dataset?.reviewGroup,fieldKey=el.dataset?.fieldGroup;
  if(key&&assetContext){el.open?assetContext.ui.open.add(key):assetContext.ui.open.delete(key)}
  if(fieldKey&&fieldContext&&!fieldContext.ui.query&&fieldContext.ui.filter==='all'){el.open?fieldContext.ui.open.add(fieldKey):fieldContext.ui.open.delete(fieldKey)}
 },true);
 global.BasisReviewLayout={assets,appraisal,results};
})(globalThis);
