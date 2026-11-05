/* Live mode: the reworked Assets, Results and Report & sign pages (Settings > New design).
 *
 * Off by default (design-flag.js); with it off every screen renders exactly as before. The
 * pages read the same API data and send the same corrections and decisions as the current
 * ones; only the presentation differs:
 *   Assets: one schedule, grouped by recovery class (or room), every row with its quantity,
 *     cost, status and its Edit action, the review queue first.
 *   Results: the verdict (reconciled or not, by how much), where the basis goes by recovery
 *     class, the checks that need the engineer, then the existing detail.
 *   Report & sign: the report itself inline, and beside it one status with its one next step
 *     (what changed since signing and how to update it), the inputs, downloads and history.
 */
(function(){
 'use strict';
 const C=globalThis.BASIS_CONFIG;if(!C||C.mode!=='live')return;
 const Live=globalThis.BasisLive,L=Live.state,api=Live.api,D=globalThis.BasisDesign,P=globalThis.BasisReviewOrganization,V=globalThis.BasisResultView,R=globalThis.BasisReport,G=globalThis.BasisGates;
 if(!D||!P||!V)return;
 const on=()=>D.enabled();
 const num=v=>P.cost(v);
 const usd=(v,digits=0)=>num(v)===null?'—':Number(v).toLocaleString('en-US',{style:'currency',currency:'USD',minimumFractionDigits:digits,maximumFractionDigits:digits});
 const pct=(v,digits=1)=>num(v)===null?'—':`${(Number(v)*100).toFixed(digits)}%`;
 const plural=(n,one,many=one+'s')=>`${n.toLocaleString('en-US')} ${n===1?one:many}`;
 const human=v=>Live.labelName(v);
 const CLASS_ORDER=['y5','y7','y15','y27_5','y39','land'];
 const classTone=b=>({y5:'c5',y7:'c7',y15:'c15',y27_5:'c27',y39:'c39',land:'cland'}[b]||'cx');

 /* ---------- Assets ---------- */
 const assetUi=new Map();
 const uiFor=s=>{const k=`${s.id}:${s.revision}`;if(!assetUi.has(k))assetUi.set(k,{query:'',filter:'all',groupBy:'recovery',closed:new Set()});return assetUi.get(k)};
 function classBar(classes,total,key='total'){
  const parts=classes.filter(c=>num(c[key])>0);
  if(!parts.length||!(total>0))return '';
  return `<div class="nd-classbar" role="img" aria-label="${esc(parts.map(c=>`${c.name} ${pct(num(c[key])/total,0)}`).join(', '))}">${parts.map(c=>`<span class="nd-${classTone(c.bucket)}" style="flex:${num(c[key])}"></span>`).join('')}</div>`;
 }
 function assetsPage(){
  return Live.withStudy(s=>{
   const e=Live.load(`assets:${s.id}:${s.revision}`,()=>api.assets(s.id,s.revision));
   if(!e.done)return `${Live.steps(s)}${Live.loading('Loading assets…')}`;if(e.error)return Live.failed(e.error,'reload-study');
   const ui=uiFor(s),corrections=(L.corrections.assets_review||new Map()),open=Live.gateOpen('assets_review');
   const items=e.data.items,view=P.assetView(items,{query:ui.query,filter:ui.filter==='edited'?'all':ui.filter,groupBy:ui.groupBy,corrections});
   const sum=view.summary,classes=view.classes,edited=items.filter(a=>corrections.has(a.id)).length;
   const groups=ui.filter==='edited'?view.groups.map(g=>({...g,items:g.items.filter(a=>a.edited)})).filter(g=>g.items.length):view.groups;
   const shown=groups.reduce((n,g)=>n+g.items.length,0);
   const filters=[['all','All',sum.count],['review',open?'Needs review':'Flagged',sum.review],['unpriced','Unpriced',sum.unpriced],...(edited?[['edited','Edited',edited]]:[])];
   const row=a=>{
    const unit=a.takeoff?.unit||'',qty=num(a.takeoff?.quantity),total=num(a.cost?.total),c=corrections.get(a.id);
    return `<tr class="${a.needs_review?'is-flagged':''}"><td class="nd-asset"><a href="${Live.route('asset-detail',s.id,a.id)}">${esc(a.label?human(a.label):a.category)}</a>${c?'<span class="nd-tag nd-tag-edited">Edited</span>':''}<small title="${esc(a.category)}">${esc(a.category)}</small></td><td>${esc(a.room?.name||'—')}</td>${ui.groupBy==='room'?`<td><span class="nd-class nd-${classTone(a.bucket)}">${esc(P.buckets[a.bucket]||human(a.bucket))}</span></td>`:''}<td class="num">${qty===null?'—':`${qty.toLocaleString('en-US',{maximumFractionDigits:2})} ${esc(unit)}`}</td><td class="num">${usd(a.cost?.unit_cost,2)}</td><td class="num"><strong>${total===null?'<span class="nd-tag nd-tag-warn">Unpriced</span>':usd(total)}</strong></td><td>${a.needs_review?(open?'<span class="nd-status nd-status-review">Needs review</span>':'<span class="nd-status nd-status-flag">Flagged</span>'):'<span class="nd-status nd-status-ok">OK</span>'}</td><td class="nd-actions">${open?`<button class="button small" data-live="edit-asset" data-value="${esc(a.id)}">${icon('edit',13)}Edit</button>`:''}<a class="button small quiet" href="${Live.route('asset-detail',s.id,a.id)}" aria-label="Details for ${esc(a.label?human(a.label):a.category)}">Details</a></td></tr>`;
   };
   const head=`<tr><th scope="col" class="nd-col-asset">Asset</th><th scope="col" class="nd-col-room">Room</th>${ui.groupBy==='room'?'<th scope="col" class="nd-col-class">Class</th>':''}<th scope="col" class="num nd-col-qty">Quantity</th><th scope="col" class="num nd-col-unit">Unit cost</th><th scope="col" class="num nd-col-total">Total</th><th scope="col" class="nd-col-status">Status</th><th scope="col" class="nd-col-actions"><span class="sr-only">Actions</span></th></tr>`;
   const cols=ui.groupBy==='room'?8:7;
   const body=groups.map(g=>{const closed=ui.closed.has(g.key),st=g.stats;return `<tbody class="nd-group" data-group="${esc(g.key)}"><tr class="nd-group-row"><th colspan="${cols}" scope="rowgroup"><button data-nd="toggle-group" data-value="${esc(g.key)}" aria-expanded="${closed?'false':'true'}">${icon('chevron',14)}${ui.groupBy==='recovery'?`<span class="nd-dot nd-${classTone(g.bucket)}"></span>`:''}<strong>${esc(g.name)}</strong><span>${plural(st.count,'asset')}</span>${st.review?`<span class="nd-status ${open?'nd-status-review':'nd-status-flag'}">${st.review} ${open?'need review':'flagged'}</span>`:''}${st.unpriced?`<span class="nd-muted">${st.unpriced} unpriced</span>`:''}<b>${st.priced?usd(st.total):'—'}</b></button></th></tr>${closed?'':g.items.map(row).join('')}</tbody>`}).join('');
   return `${Live.steps(s)}<div class="nd nd-assets">
<header class="nd-head"><div><h1>Assets</h1><p>${plural(sum.count,'asset')} in ${plural(classes.length,'recovery class','recovery classes')} · revision ${s.revision}</p></div></header>
<section class="nd-asset-summary" aria-label="Asset summary"><div class="nd-total"><span>Priced cost</span><strong>${sum.priced?usd(sum.total):'—'}</strong><small>${sum.priced} of ${sum.count} assets priced</small></div><div class="nd-split">${classBar(classes,sum.total)}<ul>${classes.map(c=>`<li><span class="nd-dot nd-${classTone(c.bucket)}"></span><span>${esc(c.name)}</span><strong>${c.priced?usd(c.total):'—'}</strong><small>${plural(c.count,'asset')}</small></li>`).join('')}</ul></div><div class="nd-queue">${sum.review?`<button class="nd-queue-item ${open?'is-review':''}" data-nd="asset-filter" data-value="review"><strong>${sum.review}</strong><span>${open?'need review':'flagged at review'}</span></button>`:'<div class="nd-queue-item is-ok"><strong>0</strong><span>need review</span></div>'}${sum.unpriced?`<button class="nd-queue-item" data-nd="asset-filter" data-value="unpriced"><strong>${sum.unpriced}</strong><span>unpriced</span></button>`:''}</div></section>
<div class="nd-toolbar"><label class="review-search nd-search">${icon('search',16)}<input id="nd-asset-search" type="search" placeholder="Search assets, rooms, categories" aria-label="Search assets" value="${esc(ui.query)}"></label><div class="nd-segmented" role="group" aria-label="Show">${filters.map(([k,label,n])=>`<button data-nd="asset-filter" data-value="${k}" aria-pressed="${ui.filter===k}">${label} <span>${n}</span></button>`).join('')}</div><div class="nd-segmented" role="group" aria-label="Group by">${[['recovery','By class'],['room','By room']].map(([k,label])=>`<button data-nd="asset-group" data-value="${k}" aria-pressed="${ui.groupBy===k}">${label}</button>`).join('')}</div></div>
${shown?`<div class="nd-table-wrap"><table class="nd-schedule"><caption class="sr-only">Asset schedule, ${shown} assets</caption><thead>${head}</thead>${body}</table></div>`:`<div class="review-empty"><h3>No matching assets</h3><button class="button small" data-nd="asset-clear">Clear search and filters</button></div>`}
</div>${Live.decisionBar?Live.decisionBar('assets_review','Assets'):''}`;
  });
 }

 /* ---------- Results ---------- */
 const CHECK_HOME={worksheet:['intake','Study inputs'],building:['structure','Building model'],depreciation:['appraisal','Appraisal'],reconciliation:['structure','Building model'],allocation:['assets','Assets']};
 function resultsPage(){
  return Live.withStudy(s=>{
   const e=Live.load(`result:${s.id}:${s.revision}:${s.updated_at}`,()=>api.result(s.id,s.revision));
   const assets=e.data?.schedule?.unpriced?.length?Live.load(`assets:${s.id}:${s.revision}`,()=>api.assets(s.id,s.revision)):null;
   const tools=e.done&&!e.error?`<div class="row"><button class="button small" data-live="download-result">${icon('download',14)}Result JSON</button>${e.data.allocation?`<button class="button small" data-live="download-allocation">${icon('download',14)}Allocation CSV</button>`:''}</div>`:'';
   if(!e.done)return `${Live.steps(s)}${Live.loading('Loading results…')}`;if(e.error)return `${Live.steps(s)}${Live.failed(e.error,'reload-insights')}`;
   const r=e.data,rec=r.reconciliation,a=r.allocation,w=r.worksheet,q=r.schedule||{};
   const st=V.resultState(r),ok=st.tone==='success';
   const all=[...(w?.checks||[]).map(c=>({...c,home:'worksheet'})),...(r.structure?.building_rcn?.checks||[]).map(c=>({...c,home:'building'})),...(r.depreciation?.checks||[]).map(c=>({...c,home:'depreciation'})),...(rec?.checks||[]).map(c=>({...c,home:'reconciliation'})),...(rec?.appraisal_check?[{...rec.appraisal_check,home:'reconciliation'}]:[]),...(a?.checks||[]).map(c=>({...c,home:'allocation'}))];
   if(q.unpriced?.length)all.push({severity:'warning',code:'unpriced_assets',message:`${plural(q.unpriced.length,'asset is','assets are')} unpriced and carry no cost in the allocation.`,home:'allocation'});
   const open=all.filter(c=>c.severity!=='ok'),passed=all.filter(c=>c.severity==='ok');
   const gap=num(rec?.gap_share),tol=num(rec?.tolerance);
   const verdict=`<section class="nd-verdict ${ok?'is-ok':'is-warn'}" aria-label="Result"><span class="nd-verdict-mark" aria-hidden="true">${icon(ok?'check':'help',22)}</span><div><h2>${ok?'Reconciliation passed':esc(st.title)}</h2><p>${rec?`Cost less depreciation is ${usd(Math.abs(num(rec.gap)||0))} (${gap===null?'—':(gap>=0?'+':'−')+Math.abs(gap*100).toFixed(1)+'%'}) from the building basis; the limit is ${pct(tol,0)}.`:esc(st.detail)}</p></div>${rec&&gap!==null&&tol?`<div class="nd-gauge" aria-hidden="true"><span style="left:${Math.min(100,Math.abs(gap)/tol*100)}%"></span><i>0</i><i>${pct(tol,0)}</i></div>`:''}${open.length?`<button class="nd-open-count" data-live="result-jump" data-value="nd-checks">${open.length} ${open.length===1?'check':'checks'} to review</button>`:''}</section>`;
   const buckets=(a?.by_bucket||q.by_bucket||[]).slice().sort((x,y)=>CLASS_ORDER.indexOf(x.bucket)-CLASS_ORDER.indexOf(y.bucket));
   const key=a?'allocated':'rcn',total=buckets.reduce((n,b)=>n+(num(b[key])||0),0);
   const fast=buckets.filter(b=>['y5','y7','y15'].includes(b.bucket)).reduce((n,b)=>n+(num(b[key])||0),0);
   const allocation=`<section class="nd-panel nd-allocation" aria-labelledby="nd-alloc-title"><header><h2 id="nd-alloc-title">${a?'Where the basis goes':'Replacement cost by class'}</h2><span>${a?`${usd(a.total)} depreciable basis`:'Allocation withheld'}</span></header>${total?`<div class="nd-fast"><strong>${usd(fast)}</strong><span>${pct(fast/total)} in 5-, 7- and 15-year property</span></div>`:''}${classBar(buckets.map(b=>({...b,name:V.bucket(b.bucket)})),total,key)}<table class="nd-class-table"><thead><tr><th scope="col">Recovery class</th><th scope="col" class="num">Assets</th><th scope="col" class="num">${a?'Allocated basis':'Replacement cost'}</th><th scope="col" class="num">Share</th></tr></thead><tbody>${buckets.map(b=>`<tr><th scope="row"><span class="nd-dot nd-${classTone(b.bucket)}"></span>${esc(V.bucket(b.bucket))}</th><td class="num">${esc(b.rows??b.assets)}</td><td class="num">${usd(b[key])}</td><td class="num">${total?pct((num(b[key])||0)/total):'—'}</td></tr>`).join('')}</tbody></table></section>`;
   const checkItem=c=>{const [screen,label]=CHECK_HOME[c.home]||[];return `<li class="nd-check is-${esc(c.severity)}"><span class="nd-check-mark">${c.severity==='ok'?'Passed':c.severity==='error'?'Fix':c.severity==='not_checked'?'Not checked':'Review'}</span><p>${esc(c.message)}</p>${c.severity!=='ok'&&screen?`<a class="button small" href="${Live.route(screen,s.id)}">${esc(label)}</a>`:''}</li>`};
   const checks=`<section class="nd-panel nd-checks" id="nd-checks" aria-labelledby="nd-checks-title"><header><h2 id="nd-checks-title">Checks</h2><span>${open.length?`${open.length} to review · `:''}${passed.length} passed</span></header>${open.length?`<ul>${open.slice(0,4).map(checkItem).join('')}</ul>${open.length>4?`<details class="nd-passed nd-more"><summary>Show ${open.length-4} more to review</summary><ul>${open.slice(4).map(checkItem).join('')}</ul></details>`:''}`:'<p class="nd-allclear">Every check passed.</p>'}${passed.length?`<details class="nd-passed"><summary>${plural(passed.length,'passed check')}</summary><ul>${passed.map(checkItem).join('')}</ul></details>`:''}</section>`;
   const figures=`<dl class="nd-figures">${[['Purchase price',w?.purchase_price],['Land',w?.land],['Depreciable basis',w?.total_basis],['Replacement cost new',q.rcn_total],['Cost less depreciation',q.rcnld_total]].map(([k,v])=>`<div><dt>${k}</dt><dd>${usd(v)}</dd></div>`).join('')}</dl>`;
   return `${Live.steps(s)}<div class="nd nd-results"><header class="nd-head"><div><h1>Results</h1><p>Revision ${esc(r.revision)}${s.status!=='completed'?' · reviews still in progress':''}</p></div>${tools}</header>${verdict}${figures}<div class="nd-results-grid">${allocation}${checks}</div><section class="nd-detail" aria-labelledby="nd-detail-title"><h2 id="nd-detail-title">Detail</h2>${V.result(r,s,assets?.data?.items||[],{lead:false})}</section></div>`;
  });
 }

 /* ---------- Report & sign ---------- */
 const CHANGED={firm:['Firm profile','The letterhead or the signing engineer changed.'],study:['Study details','The taxpayer, photographs or interviews changed.']};
 const stateEntry=s=>Live.load('report-state:'+s.id+':'+s.revision,()=>api.request('GET',`/v1/studies/${encodeURIComponent(s.id)}/report/state`));
 const answersEntry=s=>Live.load('report-answers:'+s.id,()=>api.request('GET',`/v1/studies/${encodeURIComponent(s.id)}/report-answers`));
 const profileEntry=s=>Live.load('firm-profile:'+s.org_id,()=>api.request('GET',`/v1/orgs/${encodeURIComponent(s.org_id)}/profile`,{expect:[404]}).catch(e=>{if(e.status===404)return null;throw e}));
 const revisionsEntry=s=>Live.load('revisions:'+s.id+':'+s.revision,()=>api.request('GET',`/v1/studies/${encodeURIComponent(s.id)}/revisions`));
 const roleIn=s=>((Live.me?.()?.memberships)||[]).find(m=>m.org_id===s.org_id)?.role||null;
 // The document shown inline: the signed PDF once signed, else the draft preview (whose ETag is
 // what Sign checks, so the engineer signs the report on screen).
 const pdf={key:null,url:null,tag:null,error:null,loading:false,node:null,viewer:null};
 function pdfKey(s,state){return state?.signature?`${s.id}:${s.revision}:signed:${state.signature.id}`:state?.exported&&!state.skipped?`${s.id}:${s.revision}:preview:${s.updated_at}`:null}
 async function loadPdf(s,state,key){
  pdf.loading=true;pdf.error=null;
  try{
   const signed=Boolean(state.signature),response=await api.request('GET',`/v1/studies/${encodeURIComponent(s.id)}/report${signed?'':'/preview'}`,{query:signed?{format:'pdf'}:undefined,raw:true});
   const blob=await response.blob();if(pdf.key!==key)return;
   if(pdf.url)URL.revokeObjectURL(pdf.url);
   pdf.url=URL.createObjectURL(blob);pdf.tag=response.headers.get('ETag');
   if(!signed&&pdf.tag)L.reportPreview={studyId:s.id,revision:s.revision,tag:pdf.tag};
  }catch(e){if(pdf.key===key)pdf.error=e}
  finally{if(pdf.key===key){pdf.loading=false;Live.render()}}
 }
 function resetPdf(){pdf.viewer?.destroy();pdf.viewer=null;pdf.node=null;if(pdf.url)URL.revokeObjectURL(pdf.url);Object.assign(pdf,{key:null,url:null,tag:null,error:null,loading:false})}
 function statusCard(s,state,final,role){
  const sig=state.signature,engineer=(R.RANK[role]??-1)>=R.RANK.engineer,changed=Array.isArray(state.changed_since_signing)?state.changed_since_signing:[];
  if(sig&&changed.length){
   const items=changed.map(k=>CHANGED[k]||[human(k),'Changed after signing.']);
   return `<section class="nd-status-card is-attention" aria-labelledby="nd-status-title"><span class="nd-status-pill">Update required</span><h2 id="nd-status-title">Changed after signing</h2><ul class="nd-changes">${items.map(([t,d])=>`<li><strong>${esc(t)}</strong><span>${esc(d)}</span></li>`).join('')}</ul><p>Revision ${s.revision}'s signed report is kept as it was. To deliver the current details, sign an updated revision.</p>${engineer?`<button class="button primary nd-wide" data-live="report-new-revision">Update the report (revision ${s.revision+1})</button><p class="nd-fine">Reopens Assets with the approved assets; approve it and the new report is ready to sign here.</p>`:'<p class="nd-fine">An engineer or admin updates the report.</p>'}</section>`;
  }
  if(final.complete)return `<section class="nd-status-card is-done" aria-labelledby="nd-status-title"><span class="nd-status-pill">Signed</span><h2 id="nd-status-title">Ready to deliver</h2><p>Signed by ${esc(sig.engineer.name)}${sig.engineer.credentials?', '+esc(sig.engineer.credentials):''} · ${esc(Live.when(sig.signed_at))}</p></section>`;
  const blocker=R.signBlocker(state,role,L.reportPreview&&L.reportPreview.studyId===s.id&&L.reportPreview.revision===s.revision?L.reportPreview.tag:null);
  if(s.status!=='completed')return `<section class="nd-status-card" aria-labelledby="nd-status-title"><span class="nd-status-pill">Not started</span><h2 id="nd-status-title">Finish the reviews first</h2><p>The report is written when the assets review is approved.</p></section>`;
  if(state.skipped)return `<section class="nd-status-card is-attention" aria-labelledby="nd-status-title"><span class="nd-status-pill">Blocked</span><h2 id="nd-status-title">No report for this revision</h2><p>Resolve the findings in Results before the report can be written.</p><a class="button nd-wide" href="${Live.route('complete',s.id)}">Open Results</a></section>`;
  return `<section class="nd-status-card is-attention" aria-labelledby="nd-status-title"><span class="nd-status-pill">${esc(final.status)}</span><h2 id="nd-status-title">Sign revision ${s.revision}</h2><p>${blocker?esc(blocker):'The report on the left is what you sign. Your signature is added to it; a signature is never replaced.'}</p>${engineer?`<button class="button primary nd-wide" data-live="report-sign" ${blocker?'disabled':''}>${icon('check',15)}Sign the report</button>`:''}</section>`;
 }
 function reportPage(){
  return Live.withStudy(s=>{
   const st=stateEntry(s),an=answersEntry(s),pr=profileEntry(s),role=roleIn(s);
   if(!st.done||!an.done||!pr.done)return `${Live.steps(s)}<div class="nd">${Live.loading('Loading the report…')}</div>`;
   if(st.error)return `${Live.steps(s)}${Live.failed(st.error,'report-reload')}`;
   const state=st.data,final=G.completion(s,st),sig=state.signature,engineer=(R.RANK[role]??-1)>=R.RANK.engineer,owner=role==='owner';
   const key=pdfKey(s,state);
   if(key!==pdf.key){resetPdf();pdf.key=key;if(key)loadPdf(s,state,key)}
   const a=an.data?.report_answers,p=pr.data?.profile;
   const items=R.checklist(state);
   const input=(k,label,detail,action)=>{const it=items.find(i=>i.key===k);return `<li class="${it?.done?'is-done':''}"><span class="nd-mark" aria-hidden="true">${it?.done?icon('check',13):''}</span><div><strong>${label}</strong><span>${esc(detail)}</span></div>${action}</li>`};
   const inputs=`<section class="nd-panel nd-inputs" aria-labelledby="nd-inputs-title"><header><h2 id="nd-inputs-title">In the report</h2></header><ul>${input('firm','Firm profile',p?[p.name,p.engineer?.name&&`${p.engineer.name}${p.engineer.credentials?', '+p.engineer.credentials:''}`].filter(Boolean).join(' · '):'Not added yet',owner?`<button class="button small" data-live="report-edit-profile">${p?'Edit':'Add'}</button>`:'')}${input('study','Study details',a?[a.taxpayer_name,a.photographs&&({taxpayer:'Photographs supplied by the taxpayer',engineer:'Photographs taken by the engineer'}[a.photographs]||human(a.photographs))].filter(Boolean).join(' · '):'Not added yet',engineer?`<button class="button small" data-live="report-edit-answers">${a?'Edit':'Add'}</button>`:'')}${input('signature','Engineer’s signature',sig?`${sig.engineer.name} · ${Live.when(sig.signed_at)}`:'Added when you sign','')}</ul></section>`;
   const files=new Set(state.files||[]),has=f=>files.has(f)||Boolean(sig&&f==='report.pdf');
   const dl=(fmt,file,text)=>`<button class="button small" data-live="report-download" data-value="${fmt}" ${has(file)?'':'disabled'}>${icon('download',14)}${text}</button>`;
   const downloads=`<section class="nd-panel nd-downloads" aria-labelledby="nd-dl-title"><header><h2 id="nd-dl-title">Downloads</h2><span>Revision ${s.revision}</span></header><div class="nd-dl-row">${dl('pdf','report.pdf',sig?'Signed report':'Draft report')}${dl('csv','schedule.csv','Schedule CSV')}${dl('xlsx','schedule.xlsx','Schedule XLSX')}</div></section>`;
   const rv=revisionsEntry(s),earlier=(rv.data?.items||[]).filter(x=>x.signature&&x.revision!==s.revision).sort((x,y)=>y.revision-x.revision);
   const history=earlier.length?`<section class="nd-panel nd-history" aria-labelledby="nd-history-title"><header><h2 id="nd-history-title">Earlier signed reports</h2></header><ul>${earlier.map(x=>`<li><span>Revision ${x.revision} · ${esc(Live.when(x.signature.signed_at))}</span><button class="button small quiet" data-live="report-download-revision" data-value="${x.revision}">${icon('download',14)}PDF</button></li>`).join('')}</ul></section>`:'';
   const reopen=Live.canReopen&&s.status==='completed'&&engineer?`<section class="nd-panel nd-reopen" aria-labelledby="nd-reopen-title"><header><h2 id="nd-reopen-title">Change an approved step</h2></header><p>Reopens that step and the steps after it on a new revision. You see what reruns before anything changes.</p><div class="nd-dl-row">${['appraisal','rooms',...(G.componentsGate(s)?['components']:[]),'assets'].map(v=>Live.reopenButton(s,v).replace('study-status-action','small')).join('')}</div></section>`:'';
   const label=sig?`Signed report · revision ${sig.revision??s.revision}`:state.exported&&!state.skipped?`Draft report · revision ${s.revision}`:'Report';
   const doc=`<section class="nd-doc" aria-label="${esc(label)}"><header><strong>${esc(label)}</strong><span class="nd-doc-page" data-nd-page></span></header>${pdf.key?(pdf.error?`<div class="nd-doc-empty">${Live.failed(pdf.error,'nd-pdf-retry')}</div>`:`<div class="nd-pdf-slot" data-nd-pdf></div>`):`<div class="nd-doc-empty"><p>The report is written when every review is approved.</p></div>`}</section>`;
   return `${Live.steps(s)}<div class="nd nd-report"><header class="nd-head"><div><h1>Report & sign</h1><p>${esc(s.property_name)} · revision ${s.revision}</p></div></header><div class="nd-report-grid">${doc}<aside class="nd-rail">${statusCard(s,state,final,role)}${inputs}${downloads}${reopen}${history}</aside></div></div>`;
  });
 }
 // Keep one PDF viewer across renders: the page re-renders on every load, the document must not.
 Live.afterRender.push(()=>{
  const slot=root.querySelector('[data-nd-pdf]');
  if(!slot||!on()||state.screen!=='report'){if(!slot&&pdf.viewer&&state.screen!=='report')resetPdf();return}
  if(!pdf.url){slot.innerHTML=Live.loading('Loading the report…');return}
  if(!pdf.node){
   pdf.node=document.createElement('div');pdf.node.className='nd-pdf';
   pdf.node.innerHTML=`<div class="nd-pdf-tools"><button class="button small quiet" data-nd-zoom="out" aria-label="Zoom out">−</button><button class="button small quiet" data-nd-zoom="fit">Fit width</button><button class="button small quiet" data-nd-zoom="in" aria-label="Zoom in">+</button></div><div class="nd-pdf-frame"><div class="pdf-stage basis-pdf-scroll" tabindex="0" role="region" aria-label="Report PDF" aria-busy="true"></div></div>`;
   const stage=pdf.node.querySelector('.basis-pdf-scroll');
   pdf.viewer=BasisPdfViewer.create({stage,url:pdf.url,onChange:info=>{const el=root.querySelector('[data-nd-page]');if(el)el.textContent=`Page ${info.page} of ${info.total}`},onError:e=>{pdf.error=e;Live.render()}});
   pdf.node.addEventListener('click',e=>{const z=e.target.closest('[data-nd-zoom]')?.dataset.ndZoom;if(!z||!pdf.viewer)return;z==='fit'?pdf.viewer.fitWidth():pdf.viewer.zoom(z==='in'?1.2:1/1.2)});
  }
  slot.replaceWith(pdf.node);
 });

 /* ---------- wiring: the new pages replace the current ones only while the switch is on ---------- */
 const currentAssets=assets,currentReport=Live.screens.report,currentResults=Live.resultScreen;
 assets=function(){return on()?assetsPage():currentAssets()};
 Live.screens.report=()=>on()?reportPage():currentReport();
 if(currentResults){Live.resultScreen=()=>on()?resultsPage():currentResults()}
 // A failed report PDF loads again from scratch (the report state alone would keep the same key).
 Live.actions['nd-pdf-retry']=()=>{resetPdf();Live.render()};
 root.addEventListener('click',e=>{
  const t=e.target.closest('[data-nd]');if(!t||!L.study)return;const ui=uiFor(L.study),v=t.dataset.value;
  if(t.dataset.nd==='asset-filter'){ui.filter=ui.filter===v&&v!=='all'?'all':v;Live.render()}
  else if(t.dataset.nd==='asset-group'){ui.groupBy=v;ui.closed.clear();Live.render()}
  else if(t.dataset.nd==='asset-clear'){ui.query='';ui.filter='all';Live.render()}
  else if(t.dataset.nd==='toggle-group'){ui.closed.has(v)?ui.closed.delete(v):ui.closed.add(v);Live.render();root.querySelector(`[data-nd="toggle-group"][data-value="${CSS.escape(v)}"]`)?.focus({preventScroll:true})}
 });
 root.addEventListener('input',e=>{
  if(e.target.id!=='nd-asset-search'||!L.study)return;const ui=uiFor(L.study),pos=e.target.selectionStart;ui.query=e.target.value;Live.render();
  const input=root.querySelector('#nd-asset-search');input?.focus({preventScroll:true});if(input&&pos!==null)input.setSelectionRange(pos,pos);
 });
 Live.render();
})();
