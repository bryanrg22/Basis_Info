/* Live mode, part 2: the review gates, the photo component page, assets, schedule.
 *
 * Gates: appraisal_review (extraction_field corrections), rooms_review (the grouping: room
 * renames, retypes and merges, photo_room moves), components_review (#components:
 * component, room_component and photo corrections; per-photo objects added or deleted),
 * assets_review (asset corrections). A study whose workflow has the single rooms review
 * (live/gates.js) takes the rooms and components corrections at rooms_review on one page,
 * as before. Adding what the pipeline missed (live/manual-add.js): a drawn box
 * first (SAM 3 outlines it), or a tap that previews the stored candidates under it to
 * cycle and confirm; a duplicate offers the existing object or "Add anyway"; on the rooms
 * review, the counts the photos suggest become count corrections or added components. Approve sends no corrections (the API refuses approve-with-corrections);
 * Request corrections rejects with them and the workflow re-runs that stage.
 */
(function(){
 'use strict';
 const Live=globalThis.BasisLive;if(!Live)return;
 const {api,state:L}=Live,name=Live.labelName,M=globalThis.BasisManualAdd;
 const LABELS=BasisVision.LABELS;
 const BUCKETS={y5:'5-year',y7:'7-year',y15:'15-year',y27_5:'27.5-year',y39:'39-year',land:'Land'};
 const money=v=>v===null||v===undefined||v===''?'—':Number(v).toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:Number(v)>=1000?0:2});
 const pending=stage=>(L.corrections[stage]||=new Map());
 const G=Live.gates,editStage=()=>G.editStage(L.study);

 /* ---------- decisions (shared by the review gates) ---------- */
 function decisionBar(stage,what){
  const list=[...pending(stage).values()],open=Live.gateOpen(stage);
  if(!open)return '';
  return `<div class="actionbar review-decision ${list.length?'is-attention':''}"><div><strong>${esc(what)} decision · revision ${L.study.revision}</strong><p>${list.length?`${list.length} ${list.length===1?'correction':'corrections'} ready to send. Corrections return this stage for another pass.`:'Approve this stage, or return it with corrections.'}</p>${list.length?`<details class="live-corrections"><summary>Review corrections</summary><ul>${list.map(c=>`<li><strong>${esc(c.title)}</strong> ${esc(c.summary)}<button class="button quiet small" data-live="drop-correction" data-stage="${stage}" data-value="${esc(c.key)}">Remove</button></li>`).join('')}</ul></details>`:''}</div><div class="row"><button class="button" data-live="reject" data-value="${stage}" ${open?'':'disabled'}>Request corrections</button><button class="button primary" data-live="approve" data-value="${stage}" ${open&&!list.length?'':'disabled'} title="${list.length?'Corrections cannot be approved. Request corrections instead.':''}">${icon('check')}Approve ${esc(what.toLowerCase())}</button></div></div>`;
 }
 Live.decisionBar=decisionBar;// the reworked Assets page (live/new-design.js) shares it
 async function decide(stage,decision,comment){
  if(!Live.gateOpen(stage))return;
  if(decision==='approve'&&stage==='rooms_review'&&Live.roomPlacementReady&&!Live.roomPlacementReady()){toast('Place every photo and save your changes before approving rooms.');return}
  const corrections=decision==='reject'?[...pending(stage).values()].map(({entity_type,entity_id,before,after,reason})=>({entity_type,entity_id,before,after,reason})):[];
  const body={stage,revision:L.study.revision,decision,comment:comment||null,corrections};
  // Retry only the same request: studies and edited payloads need distinct keys.
  const key=JSON.stringify([L.studyId,body]);L.decisionKeys||={};L.decisionKeys[key]||=BasisApi.newKey();
  try{
   globalThis.BasisErrors?.breadcrumb('decision',`${decision} ${stage}`,{stage,decision,revision:L.study.revision,corrections:corrections.length});
   const r=await api.decide(L.studyId,body,L.decisionKeys[key]);
   // Study progress shows what was sent while the stage reruns (cleared when the review reopens).
   L.lastSent=decision==='reject'?{studyId:L.studyId,stage,revision:L.study.revision+1,items:[...pending(stage).values()].map(c=>({title:c.title,changes:Live.correctionChanges(c)})),comment:comment||null}:null;
   pending(stage).clear();L.workflow=null;Live.stopPolling();toast(decision==='approve'?`${Live.STAGE[stage]} approved.`:`Sent ${r.corrections_recorded} ${r.corrections_recorded===1?'correction':'corrections'}. Basis is re-running this stage.`);
   Live.invalidate('studies');Live.invalidate(`fields:${L.studyId}`);Live.invalidate(`rooms:${L.studyId}`);Live.invalidate(`assets:${L.studyId}`);Live.invalidate(`objects:${L.studyId}`);
   await Live.reloadStudy();Live.go(L.study.status==='completed'||(decision==='approve'&&stage==='assets_review')?'complete':'processing');
   // The reload re-rendered the review page at the new revision before the workflow wrote
   // its rows; drop what that render cached so the next gate reads them afresh.
   for(const k of ['fields','rooms','assets','objects'])Live.invalidate(`${k}:${L.studyId}`);
  }catch(e){
   if(e.status===409){await Live.reloadStudy();Live.render()}
   toast(e.errors?.length?e.errors.map(x=>x.detail||x.msg).filter(Boolean).join(' '):e.kind==='gate-not-open'?'This review is not open yet.':e.status===409?'The study moved on since this page loaded. It has been refreshed.':Live.errorText(e));
  }
 }
 Live.saveRoomChanges=()=>Live.guarded('decide',()=>decide('rooms_review','reject','Updated room grouping.'));
 Live.actions['approve']=(el,stage)=>{if(!Live.gateOpen(stage))return;if(stage==='rooms_review'&&Live.roomPlacementReady&&!Live.roomPlacementReady()){toast('Place every photo and save your changes before approving rooms.');return}const m=modal(`Approve ${esc(Live.STAGE[stage].toLowerCase())}?`,`<p>This confirms revision ${L.study.revision} and moves the study to the next stage.</p><div class="field"><label for="live-decision-comment">Comment (optional)</label><textarea id="live-decision-comment"></textarea></div>`,`${closeBtn}<button class="button primary" id="live-confirm">Approve</button>`);m.querySelector('#live-confirm').onclick=()=>Live.guarded('decide',async()=>{const c=m.querySelector('#live-decision-comment').value.trim();m.remove();await decide(stage,'approve',c)})};
 // One correction's changes as "field: old → new" (send dialog, Study progress after sending).
 Live.correctionChanges=c=>Object.keys(c.after||{}).filter(k=>!['quantity_unit','quantity_source'].includes(k)).map(k=>{const v=x=>x==null?'—':k==='label'?name(x):x;return `${k==='label'?'type':/^value_(num|text|date)$/.test(k)?'value':k.replace(/_/g,' ')}: ${v(c.before?.[k])} → ${v(c.after[k])}`}).join(' · ')||c.summary||'';
 Live.actions['reject']=(el,stage)=>{const n=pending(stage).size;const m=modal(n?`Review and send ${n} ${n===1?'correction':'corrections'}`:'Request corrections',`<p>${n?`${n} ${n===1?'correction':'corrections'} will be sent.`:'No item corrections yet: describe what needs another pass.'} The stage runs again as revision ${L.study.revision+1}.</p>${n?`<ul class="live-send-list">${[...pending(stage).values()].map(c=>`<li><strong>${esc(c.title)}</strong><span>${esc(Live.correctionChanges(c))}</span></li>`).join('')}</ul>`:''}<div class="field"><label for="live-decision-comment">Instructions${n?' (optional)':''}</label><textarea id="live-decision-comment" placeholder="Describe what needs to be corrected."></textarea></div>`,`${closeBtn}<button class="button primary" id="live-confirm">Request corrections</button>`);m.querySelector('#live-confirm').onclick=()=>{const c=m.querySelector('#live-decision-comment').value.trim();if(!c&&!n){toast('Add instructions or at least one correction.');return}Live.guarded('decide',async()=>{m.remove();L.reopenTarget={studyId:L.studyId,stage};// Study progress returns to this review once it reruns
await decide(stage,'reject',c)})}};
 Live.actions['drop-correction']=(el,key)=>{pending(el.dataset.stage).delete(key);Live.render()};
 function addCorrection(stage,c){pending(stage).set(c.key,c);Live.render();toast('Correction added. Send it with Request corrections.')}

 /* ---------- appraisal review ---------- */
 const fieldValue=f=>f.value_text??(f.value_num!==null&&f.value_num!==undefined?Number(f.value_num).toLocaleString('en-US'):null)??f.value_date??'—';
 const fieldKind=f=>f.value_num!==null&&f.value_num!==undefined?'value_num':f.value_date?'value_date':'value_text';
 appraisal=function(){
  return Live.withStudy(s=>{
   const e=Live.load(`fields:${s.id}:${s.revision}`,()=>api.fields(s.id,s.revision));
   if(!e.done)return `${Live.steps(s)}${Live.loading('Loading the extracted fields…')}`;if(e.error)return Live.failed(e.error,'reload-study');
   const fields=e.data.items;
   if(!fields.length)return `${Live.steps(s)}${heading('Review the appraisal','No fields were extracted for this revision yet.')}`;
   L.fieldId=fields.some(f=>f.id===L.fieldId)?L.fieldId:BasisReviewOrganization.fieldGroups(fields)[0].fields[0].id;const field=fields.find(f=>f.id===L.fieldId),corr=pending('appraisal_review');
   const appraisalDoc=(s.documents||[]).find(d=>field.document_id?d.id===field.document_id:d.kind==='appraisal');
   return `${Live.steps(s)}<div class="review-split appraisal-workbench appraisal-review-layout"><header class="review-heading"><div><h1>Review the appraisal</h1><div>Select a field to see it in the report.</div></div><button type="button" class="review-heading-progress" data-review-action="filter-critical" title="Show the key fields"><strong>${fields.filter(f=>f.critical).length}<span> key fields</span></strong><span>of ${fields.length} extracted</span></button></header>${BasisReviewLayout.appraisal({fields,study:s,selected:L.fieldId,corrections:corr,canCorrect:Live.gateOpen('appraisal_review')})}<section class="panel source-panel" aria-label="Appraisal source"><div class="appraisal-source-toolbar"><span class="small" id="live-pdf-position" aria-live="off"></span>${appraisalDoc?`<button class="source-page" data-live="open-pdf" data-value="${field.page||1}" aria-label="Expand appraisal PDF">Expand</button>`:''}</div><p class="live-pdf-status is-quiet" role="status">${field.page?'Finding this value on page '+field.page+'…':'No page reference supplied. Showing the document.'}</p><div class="basis-pdf-frame"><div class="document-canvas basis-pdf-scroll" id="live-pdf-document" data-source-page="${BasisAppraisalSource.page(field)||1}" data-doc="${esc(appraisalDoc?.id||'')}" tabindex="0" role="region" aria-label="Appraisal PDF report" aria-busy="true"></div></div></section></div>${decisionBar('appraisal_review','Appraisal')}`;
  });
 };
 Live.actions['select-field']=(el,id)=>{L.fieldId=id;L.sourceIntent=id;Live.render()};
 Live.actions['open-pdf']=(el,page)=>{const stage=root.querySelector('#live-pdf-document');if(stage?.dataset.doc)Live.openDocument(stage.dataset.doc,Number(stage.dataset.page)||Number(page)||1)};
 Live.actions['correct-field']=(el,id)=>{
  if(!Live.gateOpen('appraisal_review')){toast('This appraisal review is complete or not open for editing.');return}
  const f=Live.load(`fields:${L.studyId}:${L.study.revision}`,()=>api.fields(L.studyId,L.study.revision)).data.items.find(x=>x.id===id),kind=fieldKind(f),current=pending('appraisal_review').get(id);
  const m=modal('Correct '+esc(f.label||name(f.field)),`<p>The corrected value is sent with Request corrections; the appraisal is extracted again with it.</p><div class="field"><label for="live-correct-value">Correct value${kind==='value_num'?' (a number)':kind==='value_date'?' (YYYY-MM-DD)':''}</label><input id="live-correct-value" ${kind==='value_date'?'type="date"':kind==='value_num'?'inputmode="decimal"':''} value="${esc(current?current.after[kind]:(f[kind]??''))}"></div><div class="field" style="margin-top:15px"><label for="live-correct-reason">Reason</label><textarea id="live-correct-reason" placeholder="Where the correct value comes from.">${esc(current?.reason||'')}</textarea></div>`,`${closeBtn}<button class="button primary" id="live-save">Add correction</button>`);
  m.querySelector('#live-save').onclick=()=>{const raw=m.querySelector('#live-correct-value').value.trim(),reason=m.querySelector('#live-correct-reason').value.trim();
   if(!raw||!reason){toast('Enter a value and a reason.');return}
   if(kind==='value_num'&&!Number.isFinite(Number(raw.replace(/,/g,'')))){toast('Enter a number.');return}
   const value=kind==='value_num'?raw.replace(/,/g,''):raw;
   m.remove();addCorrection('appraisal_review',{key:id,entity_type:'extraction_field',entity_id:id,before:{[kind]:f[kind]},after:{[kind]:value},reason,title:f.label||name(f.field),summary:`${fieldValue(f)} → ${value}`,display:value});
  };
 };
 // The full report stays scrollable; field selection navigates to its source page.
 let appraisalPdf=null,pdfRender=0;
 Live.afterRender.push(async()=>{
  const ticket=++pdfRender;appraisalPdf?.destroy();appraisalPdf=null;
  if(state.screen==='appraisal'&&L.study&&!Live.gateOpen('appraisal_review')){
   const crumb=root.querySelector('.breadcrumbs .strong');
   crumb?.insertAdjacentHTML('beforeend',`<span class="appraisal-study-status">${esc(L.study.status==='completed'?'Completed':Live.STAGE[L.study.current_stage]||L.study.current_stage)} · Read-only</span>`);
  }
  const stage=root.querySelector('#live-pdf-document');if(!stage||state.screen!=='appraisal')return;
  const status=root.querySelector('.live-pdf-status'),doc=L.study?.documents?.find(d=>d.id===stage.dataset.doc),page=Number(stage.dataset.sourcePage)||1;
  const field=Live.load(`fields:${L.studyId}:${L.study.revision}`,()=>api.fields(L.studyId,L.study.revision)).data?.items?.find(f=>f.id===L.fieldId);
  const current=()=>ticket===pdfRender&&stage.isConnected;
  // Announce source results without repeating the selected field above the document.
  // Missing sources and ambiguous matches remain visible so they cannot look verified.
  const sourceStatus=(message,notice=false)=>{status.textContent=message;status.classList.toggle('is-quiet',!notice)};
  if(!doc||!field){sourceStatus('The source document is not available.',true);stage.setAttribute('aria-busy','false');return}
  try{
   const source=await Live.appraisalSource(doc);if(!current())return;
   const expand=root.querySelector('[data-live="open-pdf"]');
   if(!source.url){
    expand?.remove();stage.setAttribute('aria-busy','false');sourceStatus(source.message,true);
    const note=document.createElement('p');note.className='live-state';note.textContent='Review the extracted values on the left. This appraisal has no PDF to display.';stage.replaceChildren(note);return;
   }
   const url=source.url,allRegions=BasisAppraisalSource.regions(field),hasCitation=!!BasisAppraisalSource.page(field);
   if(field.source==='mismo_xml'&&!allRegions.length)sourceStatus('XML value · no verified location in the PDF. Compare it with the report.',true);
   let focused=false;
   appraisalPdf=BasisPdfViewer.create({stage,url,initialPage:page,onChange:info=>{
    if(!current())return;
    const position=root.querySelector('#live-pdf-position');if(position)position.textContent=`Page ${info.page} of ${info.total}`;
    if(page>info.total||page<1)sourceStatus(`The cited page ${page} is outside this ${info.total}-page PDF. Showing the available report.`,true);
    else if(!hasCitation&&field.source!=='mismo_xml')sourceStatus(`No page reference supplied. Full report · ${info.total} pages.`,true);
   },onTextLayer:({page:renderedPage,total,layer})=>{
    if(!current()||(!allRegions.length&&renderedPage!==page))return;
    for(const mark of layer.querySelectorAll('mark')){const parent=mark.parentNode;mark.replaceWith(...mark.childNodes);parent.normalize()}
    // The API's regions place the value exactly (page fractions, top-left origin); the text
    // search below is the fallback for a field without one (an older extraction, an unlocated value).
    const regions=allRegions.filter(r=>r.page===renderedPage);
    let firstMark=null;
    if(regions.length){
     for(const {bbox:[x0,y0,x1,y1]} of regions){
      const box=document.createElement('mark');box.className='field-region';box.setAttribute('aria-hidden','true');
      box.style.cssText=`position:absolute;pointer-events:none;box-sizing:border-box;left:${x0*100}%;top:${y0*100}%;width:${(x1-x0)*100}%;height:${(y1-y0)*100}%`;
      layer.append(box);firstMark??=box;
     }
     stage.dataset.matches=String(allRegions.length);
     sourceStatus(`Source page ${page} · Value highlighted${field.source==='mismo_xml'?' · XML source':''}`);
    }else if(allRegions.length){return}else if(field.source==='mismo_xml'){
     stage.dataset.matches='0';sourceStatus('XML value · no verified location in the PDF. Compare it with the report.',true);
    }else{
     const spans=[...layer.querySelectorAll('span')].filter(span=>!span.classList.contains('markedContent')&&!span.querySelector('span'));
     const strings=spans.map(span=>span.textContent),matches=field.page?BasisPdfSource.matchField(strings,field):[];
     for(const part of matches.flat().sort((a,b)=>b.index-a.index||b.start-a.start)){
      const node=spans[part.index]?.firstChild;if(!node||node.nodeType!==3)continue;
      const range=document.createRange();range.setStart(node,part.start);range.setEnd(node,part.end);
      const mark=document.createElement('mark');range.surroundContents(mark);firstMark=mark;
     }
     stage.dataset.matches=String(matches.length);
     sourceStatus(!field.page?`No page reference supplied. Full report · ${total} pages.`:matches.length===1?`Source page ${page} · Matching text highlighted`:matches.length>1?`Source page ${page} · ${matches.length} text matches highlighted; confirm the correct one`:`Source page ${page} · ${strings.some(s=>s.trim())?'No exact text match on this page':'No searchable text on this page'}`,!field.page||matches.length!==1);
    }
    if(!focused&&renderedPage===page){
     focused=true;
     if(firstMark){const r=firstMark.getBoundingClientRect(),s=stage.getBoundingClientRect();stage.scrollTop=Math.max(0,stage.scrollTop+r.top-s.top-stage.clientHeight*.35)}
     if(L.sourceIntent===field.id){L.sourceIntent=null;if(matchMedia('(max-width:999px)').matches)root.querySelector('.source-panel')?.scrollIntoView({block:'start',behavior:'instant'})}
    }
   },onError:error=>{if(current())sourceStatus('The report could not be shown: '+Live.errorText(error),true)}});
  }catch(error){if(current()){stage.setAttribute('aria-busy','false');sourceStatus('The report could not be shown: '+Live.errorText(error),true)}}
 });

 /* ---------- rooms review, components review ---------- */
 const roomsEntry=s=>Live.load(`rooms:${s.id}:${s.revision}`,()=>api.rooms(s.id,s.revision));
 Live.roomsEntry=roomsEntry;
 /* One page, three uses: "grouping" (the rooms review with the components gate: rename,
  * retype, merge, move a photo), "components" (the components review: inventories, counts,
  * re-analysis, the photos' component pages) and "single" (the single rooms review of a
  * study without the gate: all of it, as before). */
 const PAGE={
  grouping:{stage:'rooms_review',what:'Rooms',title:'Rooms & photos',intro:'Check how the photos are grouped into rooms: rename or retype a room, merge two rooms, or move a photo to another room. Approve the grouping and Basis counts each room’s components.'},
  components:{stage:'components_review',what:'Components',title:'Components',intro:'Check each room’s component inventory and counts. Open a photo to review, add or fix its components.'},
  single:{stage:'rooms_review',what:'Rooms',title:'Rooms & photos',intro:'Check each room’s photos and component inventory. Open a photo to review its components.'},
 };
 function roomsPage(s,mode){
  const {stage,what,title,intro}=PAGE[mode],grouping=mode!=='components',counting=mode!=='grouping';
  const e=roomsEntry(s);if(!e.done)return `${Live.steps(s)}${Live.loading('Loading rooms…')}`;if(e.error)return Live.failed(e.error,'reload-study');
  const list=e.data.items,photoCount=list.reduce((n,r)=>n+r.photos.length,0),corr=pending(stage),open=Live.gateOpen(stage);
  const scope=s.id+':'+s.revision+':'+mode;
  L.roomExpansion||=new Map();if(!L.roomExpansion.has(scope))L.roomExpansion.set(scope,new Set());
  const expanded=L.roomExpansion.get(scope),summaries=new Map(list.map(r=>[r.id,RoomPresentation.componentSummary(r,corr)]));
  const total=[...summaries.values()].reduce((sum,summary)=>sum+summary.count,0),flagged=list.filter(r=>r.needs_review).length;
  const stat=(value,label)=>`<div><dt>${label}</dt><dd>${value}</dd></div>`;
  const preview=(p,alt,cls)=>mode==='components'?Live.componentPreview(p.document_id,alt,cls):Live.photo(p.document_id,alt,cls);
  const card=p=>{const pc=counting?corr.get('photo:'+p.document_id):undefined,mv=mode==='grouping'?corr.get('photo_room:'+p.document_id):undefined;return `<article class="photo-card"><a class="photo-trigger" href="${Live.route('viewer',s.id,p.document_id,mode==='grouping'?'rooms':mode==='components'?'components':undefined)}" aria-label="${counting?'Review components in':'View'} ${esc(p.filename)}">${preview(p,p.filename,'photo')}</a><div class="photo-meta"><div class="row between"><h3>${esc(p.filename)}</h3>${pc?'<span class="badge review">Re-analyze</span>':''}${mv?`<span class="badge review">Moving to ${esc(mv.display)}</span>`:''}</div><div class="row between" style="margin-top:10px"><a class="button small" href="${Live.route('viewer',s.id,p.document_id,mode==='grouping'?'rooms':mode==='components'?'components':undefined)}">${counting?'Review components':'View photo'}</a>${open&&counting?`<button class="button small" data-live="flag-photo" data-value="${esc(p.document_id)}" data-name="${esc(p.filename)}">${pc?'Keep as is':'Re-analyze'}</button>`:''}${open&&mode==='grouping'&&list.length>1?`<button class="button small" data-live="move-photo" data-value="${esc(p.document_id)}">${mv?'Change move':'Move to another room'}</button>`:''}</div></div></article>`};
  return `${Live.steps(s)}${heading(title,open?intro:'')}<dl class="study-summary" aria-label="${what} overview">${stat(list.length,'Rooms in this study')}${stat(photoCount,'Total photos')}${counting?stat(total,'Total components'):''}${flagged?stat(flagged,'Rooms to review'):''}</dl><div class="room-sections">${list.map((r,i)=>{
   const collapsed=!expanded.has(r.id),rc=grouping?corr.get('room:'+r.id):undefined,summary=summaries.get(r.id),count=summary.count,types=summary.types;
   const typePreview=counting&&types.length?`<span class="room-summary-categories">${types.slice(0,3).map(t=>`<span class="room-category-stat"><span>${esc(name(t.label))}</span><strong>${t.count}</strong></span>`).join('')}${types.length>3?`<span class="room-category-more">+${types.length-3} more types</span>`:''}</span>`:'';
   const roomName=rc?.after.name||r.name,roomType=rc?.after.room_type||r.room_type,floor=rc?.after.floor!==undefined?rc.after.floor:r.floor;
   return `<section class="room-section live-room component-room-card" aria-labelledby="live-room-${i}"><div class="component-room-summary"><div class="component-room-heading"><h2 id="live-room-${i}">${esc(roomName)}</h2><div class="component-room-meta"><span>Type <strong>${esc(name(roomType))}</strong></span>${floor!==null&&floor!==undefined&&floor!==''?`<span>Floor <strong>${esc(floor)}</strong></span>`:''}${r.needs_review?'<span class="room-needs-review">Needs review</span>':''}</div></div><div class="component-room-counts"><span>${r.photos.length} ${r.photos.length===1?'photo':'photos'}</span>${counting?`<span>${count} ${count===1?'component':'components'}</span><span>${types.length} ${types.length===1?'type':'types'}</span>`:''}</div><div class="component-room-preview"><span class="room-summary-previews">${r.photos.slice(0,3).map((p,n)=>`<a class="room-summary-link" href="${Live.route('viewer',s.id,p.document_id,mode==='grouping'?'rooms':mode==='components'?'components':undefined)}" aria-label="Open photo ${n+1} of ${esc(roomName)}">${preview(p,'','room-summary-thumb')}</a>`).join('')}${r.photos.length>3?`<span class="room-preview-more">+${r.photos.length-3}</span>`:''}${!r.photos.length?'<span class="room-preview-empty">No photos</span>':''}</span><button class="button small component-room-toggle" type="button" data-live="toggle-room" data-value="${esc(r.id)}" data-room-scope="${esc(scope)}" aria-expanded="${!collapsed}" aria-controls="live-room-body-${i}"><span class="when-closed">Show ${counting?'components':'photos'}</span><span class="when-open">Hide ${counting?'components':'photos'}</span><span class="sr-only"> in ${esc(roomName)}</span>${icon('down',16)}</button></div>${typePreview}</div><div class="room-section-body" id="live-room-body-${i}" ${collapsed?'hidden':''}><div class="live-room-tools">${open&&grouping?`<button class="button small" data-live="edit-room" data-value="${esc(r.id)}">${icon('edit',13)}Edit room</button>${list.length>1?`<button class="button small" data-live="merge-room" data-value="${esc(r.id)}">Merge into another room</button>`:''}`:''}${rc?`<span class="live-mock-tag">${esc(rc.summary)}</span>`:''}</div><div class="room-photo-grid">${r.photos.map(card).join('')}</div>${counting?inventoryTable(r,open,stage):''}</div></section>`
  }).join('')||'<div class="empty-screen"><h2>No rooms yet</h2><p>Rooms appear when the photos are analyzed.</p></div>'}</div>${decisionBar(stage,what)}`;
 }
 rooms=function(){return Live.withStudy(s=>roomsPage(s,G.componentsGate(s)?'grouping':'single'))};
 Live.componentsScreen=()=>Live.withStudy(s=>G.componentsGate(s)?roomsPage(s,'components'):`${Live.steps(s)}${heading('Components','This study reviews its components on the rooms page (it started before the components review existed).',`<a class="button primary" href="${Live.route('rooms')}">Open the rooms review</a>`)}`);
 function inventoryTable(r,open,stage){
  const corr=pending(stage),sug=M.suggestions(r);
  const adding=[...corr.values()].filter(c=>c.entity_type==='room_component'&&c.entity_id===r.id);
  const addButton=open?`<button class="button small" data-live="add-room-component" data-value="${esc(r.id)}">${icon('plus',13)}Add a component to this room</button>`:'';

  if(!r.inventory.length&&!adding.length)return `<p class="room-section-empty">No components in this room’s inventory.</p><div class="live-room-tools">${addButton}</div>`;
  const row=c=>{const k=corr.get('component:'+c.component_id),a=k?.after||{},s=sug.get(c.label),suggest=open&&M.raised(s)&&a.count===undefined&&M.rowFor(r,c.label)?.component_id===c.component_id;
   const count=a.count!==undefined&&a.count!==c.count?`<s class="muted">${c.count}</s> ${a.count}`:String(c.count);
   return `<tr><td><b>${esc(name(a.label||c.label))}</b>${c.added?'<small class="live-mock-tag">added</small>':''}${k?'<small class="live-edited"> · edited</small>':''}</td><td class="num">${count}${suggest?`<div class="live-suggest-count"><span title="The most counted in one photo of this room${s.manual_count?`, ${s.manual_count} of them added by an engineer`:''}">Photos show ${s.suggested}</span> <button class="button small" data-live="use-suggested" data-value="${esc(c.component_id)}" data-room="${esc(r.id)}">Use ${s.suggested}</button></div>`:''}</td><td>${esc(a.material??c.material??'—')}</td><td>${esc(a.attachment??c.attachment??'—')}</td><td class="text-right">${open?`<button class="button small" data-live="edit-component" data-value="${esc(c.component_id)}" data-room="${esc(r.id)}">${icon('edit',13)}Edit</button>`:''}</td></tr>`};
  const pendingRow=c=>`<tr><td><b>${esc(name(c.after.label))}</b><small class="live-edited"> · to add</small></td><td class="num">${c.after.count}</td><td>${esc(c.after.material||'—')}</td><td>${esc(c.after.attachment||'—')}</td><td class="text-right"><button class="button small" data-live="drop-correction" data-stage="${stage}" data-value="${esc(c.key)}">Remove</button></td></tr>`;
  return `<div class="tablewrap live-inventory"><table><caption class="sr-only">${esc(r.name)} inventory</caption><thead><tr><th>Component</th><th>Count</th><th>Material</th><th>Attachment</th><th></th></tr></thead><tbody>${r.inventory.map(row).join('')}${adding.map(pendingRow).join('')}</tbody></table><div class="live-room-tools">${addButton}</div></div>`;
 }
 const roomById=id=>roomsEntry(L.study).data.items.find(r=>r.id===id);
 Live.actions['toggle-room']=(el,id)=>{
  const expanded=L.roomExpansion?.get(el.dataset.roomScope);if(!expanded)return;
  expanded.has(id)?expanded.delete(id):expanded.add(id);const open=expanded.has(id);
  el.setAttribute('aria-expanded',String(open));root.querySelector('#'+el.getAttribute('aria-controls')).hidden=!open;
 };
 Live.actions['flag-photo']=(el,doc)=>{const key='photo:'+doc,c=pending(editStage());if(c.has(key)){c.delete(key);Live.render();return}addCorrection(editStage(),{key,entity_type:'photo',entity_id:doc,before:null,after:{},reason:'Re-analyze this photo.',title:el.dataset.name,summary:'re-analyze the photo'})};
 Live.actions['move-photo']=(el,doc)=>{const all=roomsEntry(L.study).data.items,from=all.find(r=>r.photos.some(p=>p.document_id===doc)),photo=from?.photos.find(p=>p.document_id===doc)||{document_id:doc,filename:'Photo'},others=all.filter(r=>r.id!==from?.id),key='photo_room:'+doc,cur=pending('rooms_review').get(key);
  const m=modal('Move '+esc(photo.filename),`<p>The photo moves to the room you choose when you request corrections; its components go with it. Nothing is analyzed again.</p><div class="field"><label for="live-move">Move to</label><select id="live-move">${others.map(o=>`<option value="${esc(o.id)}" ${cur?.after.room_id===o.id?'selected':''}>${esc(o.name)}</option>`).join('')}</select></div>`,`${cur?'<button class="button" id="live-unmove">Keep it here</button>':closeBtn}<button class="button primary" id="live-save">Add correction</button>`);
  m.querySelector('#live-unmove')?.addEventListener('click',()=>{pending('rooms_review').delete(key);m.remove();Live.render()});
  m.querySelector('#live-save').onclick=()=>{const to=others.find(o=>o.id===m.querySelector('#live-move').value);m.remove();addCorrection('rooms_review',G.moveCorrection(photo,from,to))}};
 Live.actions['edit-room']=(el,id)=>{const r=roomById(id),cur=pending('rooms_review').get('room:'+id)?.after||{};
  const m=modal('Rename or retype '+esc(r.name),`<div class="field"><label for="live-room-name">Room name</label><input id="live-room-name" value="${esc(cur.name??r.name)}" maxlength="120"></div><div class="field" style="margin-top:12px"><label for="live-room-type">Room type</label><input id="live-room-type" value="${esc(cur.room_type??r.room_type)}" maxlength="64"></div><div class="field" style="margin-top:12px"><label for="live-room-floor">Floor</label><input id="live-room-floor" value="${esc(cur.floor??r.floor??'')}" maxlength="64"></div>`,`${closeBtn}<button class="button primary" id="live-save">Add correction</button>`);
  m.querySelector('#live-save').onclick=()=>{const after={},n=m.querySelector('#live-room-name').value.trim(),t=m.querySelector('#live-room-type').value.trim(),f=m.querySelector('#live-room-floor').value.trim();
   if(n&&n!==r.name)after.name=n;if(t&&t!==r.room_type)after.room_type=t;if(f!==(r.floor||''))after.floor=f||null;
   if(!Object.keys(after).length){pending('rooms_review').delete('room:'+id);m.remove();Live.render();return}
   m.remove();addCorrection('rooms_review',{key:'room:'+id,entity_type:'room',entity_id:id,before:{name:r.name,room_type:r.room_type,floor:r.floor},after,reason:null,title:r.name,summary:Object.entries(after).map(([k,v])=>`${k} → ${v??'none'}`).join(', ')});
  }};
 Live.actions['merge-room']=(el,id)=>{const r=roomById(id),others=roomsEntry(L.study).data.items.filter(x=>x.id!==id);
  const m=modal('Merge '+esc(r.name),`<p>Its photos and inventory move into the room you choose.</p><div class="field"><label for="live-merge">Merge into</label><select id="live-merge">${others.map(o=>`<option value="${esc(o.id)}">${esc(o.name)}</option>`).join('')}</select></div>`,`${closeBtn}<button class="button primary" id="live-save">Add correction</button>`);
  m.querySelector('#live-save').onclick=()=>{const into=m.querySelector('#live-merge').value,target=others.find(o=>o.id===into);m.remove();addCorrection('rooms_review',{key:'room:'+id,entity_type:'room',entity_id:id,before:null,after:{merge_into:into},reason:null,title:r.name,summary:'merge into '+target.name})}};
 Live.actions['edit-component']=(el,cid)=>{const r=roomById(el.dataset.room),c=r.inventory.find(x=>x.component_id===cid),cur=pending(editStage()).get('component:'+cid)?.after||{};
  const m=modal(`Correct room inventory · ${esc(r.name)} · ${esc(name(c.label))}`,`<p class="live-scope">Changes what ${esc(r.name)} counts as ${esc(name(c.label).toLowerCase())} for pricing, not an outline on a photo. The assets are rebuilt with it when you send corrections.</p><div class="field"><label for="live-comp-label">Component type</label><select id="live-comp-label">${LABELS.map(l=>`<option value="${l}" ${(cur.label||c.label)===l?'selected':''}>${esc(name(l))}</option>`).join('')}</select></div><div class="field" style="margin-top:12px"><label for="live-comp-count">Count in this room</label><input id="live-comp-count" type="number" min="1" max="${M.MAX_COUNT}" step="1" inputmode="numeric" value="${esc(cur.count??c.count)}"></div><div class="field" style="margin-top:12px"><label for="live-comp-material">Material</label><input id="live-comp-material" value="${esc(cur.material??c.material??'')}" maxlength="120"></div><div class="field" style="margin-top:12px"><label for="live-comp-attachment">Attachment</label><input id="live-comp-attachment" value="${esc(cur.attachment??c.attachment??'')}" maxlength="120" placeholder="e.g. built-in, removable"></div>`,`${closeBtn}<button class="button primary" id="live-save">Add correction</button>`);
  m.querySelector('#live-save').onclick=()=>{const after={},l=m.querySelector('#live-comp-label').value,mat=m.querySelector('#live-comp-material').value.trim(),att=m.querySelector('#live-comp-attachment').value.trim(),count=M.validCount(m.querySelector('#live-comp-count').value);
   if(count===null){toast(`The count is a whole number from 1 to ${M.MAX_COUNT}.`);return}
   if(l!==c.label)after.label=l;if(count!==c.count)after.count=count;if(mat!==(c.material||''))after.material=mat||null;if(att!==(c.attachment||''))after.attachment=att||null;
   m.remove();if(!Object.keys(after).length){pending(editStage()).delete('component:'+cid);Live.render();return}
   addCorrection(editStage(),{key:'component:'+cid,entity_type:'component',entity_id:cid,before:{label:c.label,material:c.material,attachment:c.attachment,count:c.count},after,reason:null,title:`${name(c.label)} (${r.name})`,summary:Object.entries(after).map(([k,v])=>`${k==='label'?'type':k} → ${k==='label'?name(v):v??'none'}`).join(', ')});
  }};
 Live.actions['use-suggested']=(el,cid)=>{const r=roomById(el.dataset.room),c=r.inventory.find(x=>x.component_id===cid),s=M.suggestions(r).get(c.label),key='component:'+cid;
  addCorrection(editStage(),M.countCorrection(r,c,M.suggestedRowCount(c,s),pending(editStage()).get(key),name))};
 Live.actions['add-room-component']=(el,rid)=>{const r=roomById(rid),label=el.dataset.label||'',count=el.dataset.count||'1';
  const options=(list,chosen)=>`<option value="">Not specified</option>${list.map(v=>`<option value="${v}" ${v===chosen?'selected':''}>${esc(name(v))}</option>`).join('')}`;
  const m=modal('Add a component to '+esc(r.name),`<p>For a component the room’s inventory is missing. It is classified by the rules table like any component and priced at this count when you request corrections.</p><div class="field"><label for="live-add-comp-label">Component</label><select id="live-add-comp-label">${LABELS.map(l=>`<option value="${l}" ${l===label?'selected':''}>${esc(name(l))}</option>`).join('')}</select></div><div class="field" style="margin-top:12px"><label for="live-add-comp-count">Count in this room</label><input id="live-add-comp-count" type="number" min="1" max="${M.MAX_COUNT}" step="1" inputmode="numeric" value="${esc(count)}"></div><div class="field" style="margin-top:12px"><label for="live-add-comp-material">Material</label><select id="live-add-comp-material">${options(M.MATERIALS,'')}</select></div><div class="field" style="margin-top:12px"><label for="live-add-comp-attachment">Attachment</label><select id="live-add-comp-attachment">${options(M.ATTACHMENTS,'')}</select></div>`,`${closeBtn}<button class="button primary" id="live-save">Add correction</button>`);
  m.querySelector('#live-save').onclick=()=>{const n=M.validCount(m.querySelector('#live-add-comp-count').value);
   if(n===null){toast(`The count is a whole number from 1 to ${M.MAX_COUNT}.`);return}
   const values={label:m.querySelector('#live-add-comp-label').value,count:n,material:m.querySelector('#live-add-comp-material').value||null,attachment:m.querySelector('#live-add-comp-attachment').value||null};
   m.remove();addCorrection(editStage(),M.roomComponentCorrection(r,values,name));
  }};

 /* ---------- the photo component page ---------- */
 // Photo review is implemented by the shared vision-workbench.js.
 /* ---------- assets review ---------- */
 const assetsEntry=s=>Live.load(`assets:${s.id}:${s.revision}`,()=>api.assets(s.id,s.revision));
 assets=function(){
  return Live.withStudy(s=>{
   const e=assetsEntry(s);if(!e.done)return `${Live.steps(s)}${Live.loading('Loading assets…')}`;if(e.error)return Live.failed(e.error,'reload-study');
   const roomEntry=Live.load(`rooms:${s.id}:${s.revision}`,()=>api.rooms(s.id,s.revision));
   return `${Live.steps(s)}${heading('Review assets','')}${BasisReviewLayout.assets({items:e.data.items,study:s,rooms:roomEntry.data?.items||[],corrections:pending('assets_review')})}${decisionBar('assets_review','Assets')}`;
  });
 };
 assetDetail=function(){
  return Live.withStudy(s=>{
   const e=assetsEntry(s);if(!e.done)return Live.loading('Loading assets…');if(e.error)return Live.failed(e.error,'reload-study');
   const items=e.data.items,a=items.find(x=>x.id===L.docId)||items[0];if(!a)return heading('No assets','This study has no assets yet.');
   const ev=Live.load(`evidence:${s.id}:${s.revision}:${a.id}`,()=>api.evidence(s.id,a.id)),evidence=ev.data?.items?.[0],c=pending('assets_review').get(a.id),after=c?.after||{};
   return `${Live.steps(s)}${heading(esc(a.label?name(a.label):a.category),`${esc(s.property_name)}${a.room?' / '+esc(a.room.name):''} / ${esc(a.category)}`,`<a class="button" href="${Live.route('assets')}">${icon('back')}All assets</a>`)}<div class="asset-layout"><nav class="asset-nav" aria-label="Assets">${items.map(x=>`<a class="${x.id===a.id?'active':''}" href="${Live.route('asset-detail',s.id,x.id)}"><span class="asset-nav-name">${esc(x.label?name(x.label):x.category)}${x.room?.name?`<small>${esc(x.room.name)}</small>`:''}</span><span class="small">${x.needs_review?'○':'✓'}</span></a>`).join('')}</nav><section class="asset-main"><div class="row between">${a.needs_review&&!Live.gateOpen('assets_review')?'<span class="badge">Flagged at review</span>':`<span class="badge ${a.needs_review?'review':'complete'}">${a.needs_review?'Needs review':'OK'}</span>`}</div>${(()=>{const q=a.takeoff?.quantity!=null?Number(a.takeoff.quantity):null,uc=a.cost?.unit_cost,t=a.cost?.total,usd=v=>v==null||v===''?'Unpriced':Number(v).toLocaleString('en-US',{style:'currency',currency:'USD'});return `<dl class="asset-facts"><div><dt>Quantity</dt><dd>${q===null?'—':`${q.toLocaleString('en-US',{maximumFractionDigits:2})} ${esc(a.takeoff?.unit||'')}`}</dd></div><div><dt>Unit cost</dt><dd>${esc(usd(uc))}</dd></div><div><dt>Total</dt><dd>${esc(usd(t))}</dd></div><div><dt>Recovery class</dt><dd>${esc(BUCKETS[a.bucket]||a.bucket||'—')}</dd></div><div><dt>Room</dt><dd>${esc(a.room?.name||'—')}</dd></div></dl>`})()}${a.review_reasons.length?`<details class="note asset-review-warning ${Live.gateOpen('assets_review')?'':'is-retained'}" style="margin-top:14px" open><summary><h3>${Live.gateOpen('assets_review')?'Why it needs review':'Why it was flagged'}</h3></summary><ul class="flag-list">${globalThis.BasisErrorHelp?BasisErrorHelp.flags(a.review_reasons,a.review_codes):BasisReasons.items(a.review_reasons)}</ul></details>`:''}<div class="line-item"><span class="muted">Category</span><strong>${esc(after.category||a.category)}</strong></div><div class="line-item"><span class="muted">Recovery period</span><strong>${esc(BUCKETS[after.bucket||a.bucket])} · ${esc(after.recovery_period||a.recovery_period)} years</strong></div><div class="line-item"><span class="muted">Method</span><strong>${esc(a.method)}</strong></div><div class="line-item"><span class="muted">IRS section</span><strong>${esc(after.irs_section||a.irs_section||'—')}</strong></div>${Live.gateOpen('assets_review')?`<button class="button" data-live="edit-asset" data-value="${esc(a.id)}" style="margin-top:16px">${icon('edit',14)}${c?'Edit correction':'Correct classification'}</button>`:''}${Live.assetInsights?Live.assetInsights(a,s):''}<h3 style="margin-top:24px">Citations</h3>${!ev.done?Live.loading('Loading evidence…'):ev.error?Live.failed(ev.error):BasisCitations.list(evidence?.citations,a.review_reasons)}</section><aside class="reference-panel"><div><h3>Photo evidence</h3>${!ev.done?'':evidence?.photos?.length?evidence.photos.map(p=>`<figure class="live-evidence-photo"><div class="photo-canvas">${Live.photo(p.document_id,p.filename,'live-evidence-img')}</div><figcaption class="live-evidence-caption"><span class="live-evidence-file" title="${esc(p.filename)}">${esc(p.filename)}</span><span>${p.role==='source'?'Where it was seen':'Room photo'} · ${(n=>n?`${n} counted`:'none counted')(p.instances.filter(i=>i.counted&&(!evidence.component_label||i.label===evidence.component_label)).length)}</span><a class="screen-link" href="${Live.route('viewer',s.id,p.document_id)}">Open photo</a></figcaption></figure>`).join(''):'<p class="small">No photos linked to this asset.</p>'}</div></aside></div>${decisionBar('assets_review','Assets')}`;
  });
 };
 const evidenceLayers=[];
 Live.afterRender.push(()=>{
  evidenceLayers.splice(0).forEach(l=>l.destroy());if(state.screen!=='asset-detail')return;
  const a=L.cache.get(`assets:${L.studyId}:${L.study?.revision}`)?.data?.items,asset=a?.find(x=>x.id===L.docId)||a?.[0];if(!asset)return;
  const ev=L.cache.get(`evidence:${L.studyId}:${L.study.revision}:${asset.id}`)?.data?.items?.[0];if(!ev)return;
  root.querySelectorAll('img.live-evidence-img').forEach(img=>{const p=ev.photos.find(x=>x.document_id===img.dataset.liveDoc);if(!p?.instances.length)return;
   // The photo's instances of this component: counted ones are lit, candidates are not.
   const label=ev.component_label||p.instances[0].label,l=BasisMaskLayer.attach(img,{});l.set({instances:p.instances,mode:'spotlight',focusLabel:label});evidenceLayers.push(l)});
 });
 // From an asset to the room its component is counted in: Components, filtered to that room (live/room-filter.js).
 Live.actions['open-component']=(el,room)=>{L.roomQuery||={};L.roomQuery.components=room||'';Live.go(G.editScreen(L.study),L.studyId)};
 Live.actions['edit-asset']=(el,id)=>{const a=assetsEntry(L.study).data.items.find(x=>x.id===id),cur=pending('assets_review').get(id)?.after||{};
  const m=modal('Correct '+esc(a.label?name(a.label):a.category),`<p>Sent with Request corrections; the schedule is rebuilt with it.</p><div class="field"><label for="live-asset-category">Category</label><input id="live-asset-category" value="${esc(cur.category??a.category)}" maxlength="120"></div><div class="field" style="margin-top:12px"><label for="live-asset-bucket">Recovery class</label><select id="live-asset-bucket">${Object.entries(BUCKETS).map(([k,l])=>`<option value="${k}" ${(cur.bucket||a.bucket)===k?'selected':''}>${l}</option>`).join('')}</select></div><div class="field" style="margin-top:12px"><label for="live-asset-period">Recovery period (years)</label><input id="live-asset-period" inputmode="decimal" value="${esc(cur.recovery_period??a.recovery_period)}"></div><div class="field" style="margin-top:12px"><label for="live-asset-irs">IRS section</label><input id="live-asset-irs" value="${esc(cur.irs_section??a.irs_section??'')}" maxlength="64"></div><fieldset class="live-asset-quantity"><legend>Measured quantity</legend><div class="field"><label for="live-asset-qty">Quantity</label><input id="live-asset-qty" inputmode="decimal" value="${esc(cur.quantity??(a.takeoff?.quantity!=null?Number(a.takeoff.quantity):''))}"></div><div class="field"><label for="live-asset-unit">Unit</label><select id="live-asset-unit" data-native="true">${['EA','SF','LF','SY'].map(u=>`<option ${(cur.quantity_unit||a.takeoff?.unit)===u?'selected':''}>${u}</option>`).join('')}</select></div><div class="field live-asset-qty-source"><label for="live-asset-qty-source">Where it was measured</label><input id="live-asset-qty-source" maxlength="300" placeholder="e.g. appraisal sketch p. 4, site measurement" value="${esc(cur.quantity_source||'')}"></div></fieldset><div class="field" style="margin-top:12px"><label for="live-asset-reason">Reason</label><textarea id="live-asset-reason">${esc(pending('assets_review').get(id)?.reason||'')}</textarea></div><p class="live-asset-limits">What an asset counts comes from its component${a.room?.name?` (<button type="button" class="link-button" data-live="open-component" data-value="${esc(a.room.name)}" data-close>${esc(a.room.name)} at Components</button>)`:''}; prices come from the firm’s cost table. Removing an asset or overriding its price is not available yet.</p>`,`${closeBtn}<button class="button primary" id="live-save">Add correction</button>`);
  m.querySelector('#live-save').onclick=()=>{const after={},cat=m.querySelector('#live-asset-category').value.trim(),b=m.querySelector('#live-asset-bucket').value,p=m.querySelector('#live-asset-period').value.trim(),irs=m.querySelector('#live-asset-irs').value.trim(),reason=m.querySelector('#live-asset-reason').value.trim()||null;
   if(cat&&cat!==a.category)after.category=cat;if(b!==a.bucket)after.bucket=b;if(p&&p!==String(a.recovery_period))after.recovery_period=p;if(irs!==(a.irs_section||''))after.irs_section=irs||null;
   if(p&&!(Number(p)>0&&Number(p)<1000)){toast('The recovery period is a positive number of years.');return}
   // A measured quantity goes with its unit and where it was measured (EVAL_REPORT 78).
   const qRaw=m.querySelector('#live-asset-qty').value.trim(),unit=m.querySelector('#live-asset-unit').value,src=m.querySelector('#live-asset-qty-source').value.trim(),q=Number(qRaw);
   const wasQ=a.takeoff?.quantity!=null?Number(a.takeoff.quantity):null,qChanged=qRaw!==''&&(q!==wasQ||unit!==(a.takeoff?.unit||unit));
   if(qChanged){
    if(!(Number.isFinite(q)&&q>0&&q<=10000000)){toast('The quantity is a positive number up to 10,000,000.');return}
    if(!src){toast('Say where the quantity was measured.');return}
    Object.assign(after,{quantity:q,quantity_unit:unit,quantity_source:src});
   }
   m.remove();if(!Object.keys(after).length){pending('assets_review').delete(id);Live.render();return}
   addCorrection('assets_review',{key:id,entity_type:'asset',entity_id:id,before:{category:a.category,bucket:a.bucket,recovery_period:a.recovery_period,irs_section:a.irs_section,...(after.quantity!==undefined?{quantity:a.takeoff?.quantity??null,quantity_unit:a.takeoff?.unit??null}:{})},after,reason,title:a.label?name(a.label):a.category,summary:Object.entries(after).filter(([k])=>!['quantity_unit','quantity_source'].includes(k)).map(([k,v])=>k==='quantity'?`quantity → ${v} ${after.quantity_unit}`:`${k} → ${k==='bucket'?BUCKETS[v]:v??'none'}`).join(', ')});
  }};

 /* ---------- completed schedule ---------- */
 complete=function(){return Live.resultScreen?Live.resultScreen():Live.loading('Loading results…')};
 Live.actions['export-csv']=()=>{const items=assetsEntry(L.study).data.items,rows=[['component','category','room','class','recovery_period','method','quantity','unit','unit_cost','total','needs_review']].concat(items.map(a=>[a.label||'',a.category,a.room?.name||'',BUCKETS[a.bucket],a.recovery_period,a.method,a.takeoff?.quantity??'',a.takeoff?.unit||'',a.cost?.unit_cost??'',a.cost?.total??'',a.needs_review]));
  const csv=rows.map(r=>r.map(v=>{const s=String(v??'');return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s}).join(',')).join('\n'),url=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
  const a=Object.assign(document.createElement('a'),{href:url,download:`${L.study.property_name.replace(/[^\w-]+/g,'_')}_schedule_r${L.study.revision}.csv`});document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)};
 Live.render();
})();
