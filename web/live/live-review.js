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
 const LABELS=["carpet","hardwood_floor","vinyl_flooring","laminate_flooring","tile","wall_finish","ceiling_finish","baseboard","crown_molding","wainscoting","concrete_floor","half_wall","interior_door","exterior_door","window","blinds","window_treatment","stairs","railing","fireplace","closet_shelving","built_in_shelving","mirror","kitchen_cabinet","countertop","sink","appliance","refrigerator","dishwasher","stove","oven","microwave","range_hood","garbage_disposal","washer","dryer","bathroom_vanity","toilet","bathtub","shower","bathroom_accessory","light_fixture","ceiling_fan","outdoor_lighting","electrical_panel","electrical_outlet","hvac_unit","ac_condenser","hvac_vent","thermostat","water_heater","smoke_detector","sprinkler_system","security_system","doorbell","window_guard","furniture","desk","chair","filing_cabinet","garage_door","roof","siding","gutter","deck","patio","porch","pool","fence","retaining_wall","driveway","sidewalk","parking_lot","landscaping","signage","shed","concrete_pad","other"];
 const BUCKETS={y5:'5-year',y7:'7-year',y15:'15-year',y27_5:'27.5-year',y39:'39-year',land:'Land'};
 const money=v=>v===null||v===undefined||v===''?'—':Number(v).toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:Number(v)>=1000?0:2});
 // Share of the photo an instance covers; small fixtures are well under 0.1%.
 const sizeLabel=a=>a>0&&a<0.001?'<0.1%':(a*100).toFixed(1)+'%';
 const pct=v=>v===null||v===undefined?'—':Math.round(v*100)+'%';
 const pending=stage=>(L.corrections[stage]||=new Map());
 const G=Live.gates,editStage=()=>G.editStage(L.study);
 const readOnlyNote=stage=>Live.gateOpen(stage)?'':`<div class="audit-banner">${L.study.current_stage===stage&&L.study.status==='processing'?'This review is not open yet: Basis is still preparing it.':stage==='components_review'&&L.study.current_stage==='components_processing'&&L.study.status==='processing'?'Preparing components: Basis is counting each room’s components on the rooms you approved. This review opens when that is done.':`This study is at ${esc(Live.STAGE[L.study.current_stage]||L.study.current_stage)}. This page is read-only.`}</div>`;

 /* ---------- decisions (shared by the three gates) ---------- */
 function decisionBar(stage,what){
  const list=[...pending(stage).values()],open=Live.gateOpen(stage);
  return `<div class="actionbar review-decision"><div><strong>${esc(what)} decision · revision ${L.study.revision}</strong><p>${list.length?`${list.length} ${list.length===1?'correction':'corrections'} ready to send. Corrections return this stage for another pass.`:'Approve this stage, or return it with corrections.'}</p>${list.length?`<details class="live-corrections"><summary>Review corrections</summary><ul>${list.map(c=>`<li><strong>${esc(c.title)}</strong> ${esc(c.summary)}<button class="button quiet small" data-live="drop-correction" data-stage="${stage}" data-value="${esc(c.key)}">Remove</button></li>`).join('')}</ul></details>`:''}</div><div class="row"><button class="button" data-live="reject" data-value="${stage}" ${open?'':'disabled'}>Request corrections</button><button class="button primary" data-live="approve" data-value="${stage}" ${open&&!list.length?'':'disabled'} title="${list.length?'Corrections cannot be approved. Request corrections instead.':''}">${icon('check')}Approve ${esc(what.toLowerCase())}</button></div></div>`;
 }
 async function decide(stage,decision,comment){
  const corrections=decision==='reject'?[...pending(stage).values()].map(({entity_type,entity_id,before,after,reason})=>({entity_type,entity_id,before,after,reason})):[];
  const key=`${stage}:${L.study.revision}:${decision}`;L.decisionKeys||={};L.decisionKeys[key]||=BasisApi.newKey();
  try{
   const r=await api.decide(L.studyId,{stage,revision:L.study.revision,decision,comment:comment||null,corrections},L.decisionKeys[key]);
   pending(stage).clear();L.workflow=null;Live.stopPolling();toast(decision==='approve'?`${Live.STAGE[stage]} approved.`:`Sent ${r.corrections_recorded} ${r.corrections_recorded===1?'correction':'corrections'}. Basis is re-running this stage.`);
   Live.invalidate('studies');Live.invalidate(`fields:${L.studyId}`);Live.invalidate(`rooms:${L.studyId}`);Live.invalidate(`assets:${L.studyId}`);Live.invalidate(`objects:${L.studyId}`);
   await Live.reloadStudy();Live.go(L.study.status==='completed'||(decision==='approve'&&stage==='assets_review')?'complete':'processing');
   // The reload re-rendered the review page at the new revision before the workflow wrote
   // its rows; drop what that render cached so the next gate reads them afresh.
   for(const k of ['fields','rooms','assets','objects'])Live.invalidate(`${k}:${L.studyId}`);
  }catch(e){
   if(e.status===409){await Live.reloadStudy();Live.render()}
   toast(e.kind==='gate-not-open'?'This review is not open yet.':e.status===409?'The study moved on since this page loaded. It has been refreshed.':Live.errorText(e));
  }
 }
 Live.actions['approve']=(el,stage)=>{const m=modal(`Approve ${esc(Live.STAGE[stage].toLowerCase())}?`,`<p>This confirms revision ${L.study.revision} and moves the study to the next stage.</p><div class="field"><label for="live-decision-comment">Comment (optional)</label><textarea id="live-decision-comment"></textarea></div>`,`${closeBtn}<button class="button primary" id="live-confirm">Approve</button>`);m.querySelector('#live-confirm').onclick=()=>Live.guarded('decide',async()=>{const c=m.querySelector('#live-decision-comment').value.trim();m.remove();await decide(stage,'approve',c)})};
 Live.actions['reject']=(el,stage)=>{const n=pending(stage).size;const m=modal('Request corrections',`<p>${n?`${n} ${n===1?'correction':'corrections'} will be sent.`:'No item corrections yet: describe what needs another pass.'} The stage runs again as revision ${L.study.revision+1}.</p><div class="field"><label for="live-decision-comment">Instructions${n?' (optional)':''}</label><textarea id="live-decision-comment" placeholder="Describe what needs to be corrected."></textarea></div>`,`${closeBtn}<button class="button primary" id="live-confirm">Request corrections</button>`);m.querySelector('#live-confirm').onclick=()=>{const c=m.querySelector('#live-decision-comment').value.trim();if(!c&&!n){toast('Add instructions or at least one correction.');return}Live.guarded('decide',async()=>{m.remove();await decide(stage,'reject',c)})}};
 Live.actions['drop-correction']=(el,key)=>{pending(el.dataset.stage).delete(key);Live.render()};
 function addCorrection(stage,c){pending(stage).set(c.key,c);Live.render();toast('Correction added. Send it with Request corrections.')}

 /* ---------- appraisal review ---------- */
 const fieldValue=f=>f.value_text??(f.value_num!==null&&f.value_num!==undefined?Number(f.value_num).toLocaleString('en-US'):null)??f.value_date??'—';
 const fieldKind=f=>f.value_num!==null&&f.value_num!==undefined?'value_num':f.value_date?'value_date':'value_text';
 appraisal=function(){
  return Live.withStudy(s=>{
   const e=Live.load(`fields:${s.id}:${s.revision}`,()=>api.fields(s.id,s.revision));
   if(!e.done)return `${Live.steps(s)}${Live.loading('Loading the extracted fields…')}`;if(e.error)return Live.failed(e.error,'reload-study');
   const fields=e.data.items,appraisalDoc=(s.documents||[]).find(d=>d.kind==='appraisal');
   if(!fields.length)return `${Live.steps(s)}${heading('Review the appraisal','No fields were extracted for this revision yet.')}${readOnlyNote('appraisal_review')}`;
   L.fieldId=fields.some(f=>f.id===L.fieldId)?L.fieldId:fields[0].id;const field=fields.find(f=>f.id===L.fieldId),corr=pending('appraisal_review');
   const sections=[...new Set(fields.map(f=>f.section))];
   return `${Live.steps(s)}<header class="review-heading"><div><p>${esc(s.property_name)} <span>Appraisal review</span></p><h1>Review the appraisal</h1><div>Compare each extracted value with its page in the appraisal.</div></div><div class="review-heading-progress"><strong>${fields.filter(f=>f.critical).length}<span> critical</span></strong><span>of ${fields.length} fields</span></div></header>${readOnlyNote('appraisal_review')}<div class="review-split appraisal-workbench"><section class="panel appraisal-details"><div class="panel-head"><h2>Property details</h2><span class="small">Select a field to see its page</span></div><div class="appraisal-table-head"><span>Extracted field</span><span>Value</span><span>Source</span></div>${sections.map(sec=>`<div class="live-field-section">${esc(name(sec))}</div>${fields.filter(f=>f.section===sec).map(f=>{const c=corr.get(f.id);return `<button class="field-row ${f.id===L.fieldId?'selected':''}" data-live="select-field" data-value="${esc(f.id)}" aria-pressed="${f.id===L.fieldId}"><span class="field-label">${esc(f.label||name(f.field))}${f.critical?' <span class="live-critical" title="Critical field">•</span>':''}</span><strong>${esc(c?c.display:fieldValue(f))}${c?'<small>Correction added</small>':''}${f.confidence!==null&&f.confidence<0.7?'<small>Low confidence</small>':''}</strong><span class="page-tag">${f.page?`p. ${f.page}`:'—'}</span></button>`}).join('')}`).join('')}<div class="field-review-actions"><div><span>Selected field</span><strong>${esc(field.label||name(field.field))}</strong><span class="small">Source: ${esc(field.source)} · confidence ${pct(field.confidence)}</span></div><div class="row"><button class="button small" data-live="correct-field" data-value="${esc(field.id)}" ${Live.gateOpen('appraisal_review')?'':'disabled'}>Correct value</button></div></div></section><section class="panel source-panel"><div class="panel-head"><div class="row">${icon('file',18)}<div><strong>${esc(appraisalDoc?.filename||'Appraisal')}</strong><p>${esc(s.property_name)}</p></div></div>${appraisalDoc?`<button class="source-page" data-live="open-pdf" data-value="${field.page||1}" aria-label="Open the appraisal at this page">Page ${field.page||'—'} <span>open</span></button>`:''}</div><div class="document-canvas live-pdf-page"><canvas id="live-pdf-canvas" data-page="${field.page||1}" aria-label="Appraisal page ${field.page||1}"></canvas><p class="live-pdf-status small" role="status">${field.page?'Loading page '+field.page+'…':'This field has no page reference.'}</p></div></section></div>${decisionBar('appraisal_review','Appraisal')}`;
  });
 };
 Live.actions['select-field']=(el,id)=>{L.fieldId=id;Live.render()};
 Live.actions['open-pdf']=(el,page)=>{const d=L.study.documents.find(x=>x.kind==='appraisal');if(d)Live.openDocument(d.id,Number(page)||1)};
 Live.actions['correct-field']=(el,id)=>{
  const f=Live.load(`fields:${L.studyId}:${L.study.revision}`,()=>api.fields(L.studyId,L.study.revision)).data.items.find(x=>x.id===id),kind=fieldKind(f),current=pending('appraisal_review').get(id);
  const m=modal('Correct '+esc(f.label||name(f.field)),`<p>The corrected value is sent with Request corrections; the appraisal is extracted again with it.</p><div class="field"><label for="live-correct-value">Correct value${kind==='value_num'?' (a number)':kind==='value_date'?' (YYYY-MM-DD)':''}</label><input id="live-correct-value" ${kind==='value_date'?'type="date"':kind==='value_num'?'inputmode="decimal"':''} value="${esc(current?current.after[kind]:(f[kind]??''))}"></div><div class="field" style="margin-top:15px"><label for="live-correct-reason">Reason</label><textarea id="live-correct-reason" placeholder="Where the correct value comes from.">${esc(current?.reason||'')}</textarea></div>`,`${closeBtn}<button class="button primary" id="live-save">Add correction</button>`);
  m.querySelector('#live-save').onclick=()=>{const raw=m.querySelector('#live-correct-value').value.trim(),reason=m.querySelector('#live-correct-reason').value.trim();
   if(!raw||!reason){toast('Enter a value and a reason.');return}
   if(kind==='value_num'&&!Number.isFinite(Number(raw.replace(/,/g,'')))){toast('Enter a number.');return}
   const value=kind==='value_num'?raw.replace(/,/g,''):raw;
   m.remove();addCorrection('appraisal_review',{key:id,entity_type:'extraction_field',entity_id:id,before:{[kind]:f[kind]},after:{[kind]:value},reason,title:f.label||name(f.field),summary:`${fieldValue(f)} → ${value}`,display:value});
  };
 };
 // Draw the cited page inline with the design's PDF.js copy.
 const pdfDocs=new Map();let pdfRender=0;
 Live.afterRender.push(async()=>{
  const canvas=root.querySelector('#live-pdf-canvas');if(!canvas||state.screen!=='appraisal')return;
  const doc=L.study?.documents?.find(d=>d.kind==='appraisal'),status=root.querySelector('.live-pdf-status'),page=Number(canvas.dataset.page)||1,ticket=++pdfRender;
  if(!doc){status.textContent='No appraisal document on this study.';return}
  try{
   // import() resolves against this script (live/), so the URL is made absolute; a failed
   // load is not kept in the design's shared promise.
   pdfModulePromise??=import(new URL('assets/vendor/pdfjs/pdf.min.mjs',location.href).href);const pdfjs=await pdfModulePromise.catch(e=>{pdfModulePromise=undefined;throw e});
   pdfjs.GlobalWorkerOptions.workerSrc||=new URL('./assets/vendor/pdfjs/pdf.worker.min.mjs',location.href).href;
   if(!pdfDocs.has(doc.id))pdfDocs.set(doc.id,Live.blobUrl(doc.id).then(url=>pdfjs.getDocument({url,standardFontDataUrl:new URL('./assets/vendor/pdfjs/standard_fonts/',location.href).href}).promise));
   const pdf=await pdfDocs.get(doc.id);if(ticket!==pdfRender||!canvas.isConnected)return;
   const p=await pdf.getPage(Math.min(page,pdf.numPages)),width=canvas.parentElement.clientWidth-24,base=p.getViewport({scale:1}),scale=Math.max(.5,width/base.width),vp=p.getViewport({scale:scale*Math.min(2,devicePixelRatio||1)});
   canvas.width=vp.width;canvas.height=vp.height;canvas.style.width=(vp.width/Math.min(2,devicePixelRatio||1))+'px';
   await p.render({canvasContext:canvas.getContext('2d'),viewport:vp}).promise;if(status.isConnected)status.textContent=`Page ${page} of ${pdf.numPages}`;
  }catch(e){if(ticket===pdfRender&&status)status.textContent='The page could not be shown: '+Live.errorText(e)}
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
  L.collapsedRooms||=new Set();
  const card=p=>{const pc=counting?corr.get('photo:'+p.document_id):undefined,mv=mode==='grouping'?corr.get('photo_room:'+p.document_id):undefined;return `<article class="photo-card"><a class="photo-trigger" href="${Live.route('viewer',s.id,p.document_id)}" aria-label="${counting?'Review components in':'View'} ${esc(p.filename)}">${Live.photo(p.document_id,p.filename,'photo')}</a><div class="photo-meta"><div class="row between"><h3>${esc(p.filename)}</h3>${pc?'<span class="badge review">Re-analyze</span>':''}${mv?`<span class="badge review">Moving to ${esc(mv.display)}</span>`:''}</div><div class="row between" style="margin-top:10px"><a class="screen-link" href="${Live.route('viewer',s.id,p.document_id)}">${counting?'Review components':'View photo'}</a>${open&&counting?`<button class="button quiet small" data-live="flag-photo" data-value="${esc(p.document_id)}" data-name="${esc(p.filename)}">${pc?'Keep as is':'Re-analyze'}</button>`:''}${open&&mode==='grouping'&&list.length>1?`<button class="button quiet small" data-live="move-photo" data-value="${esc(p.document_id)}">${mv?'Change move':'Move to another room'}</button>`:''}</div></div></article>`};
  return `${Live.steps(s)}${heading(title,intro)}${readOnlyNote(stage)}<div class="rooms-overview"><span>${list.length} rooms<span>·</span>${photoCount} photos</span><span>${list.filter(r=>r.needs_review).length} rooms flagged for review</span></div><div class="room-sections">${list.map((r,i)=>{const collapsed=L.collapsedRooms.has(r.id),rc=grouping?corr.get('room:'+r.id):undefined;return `<section class="room-section live-room" aria-labelledby="live-room-${i}"><h2 class="room-section-heading"><button type="button" data-live="toggle-room" data-value="${esc(r.id)}" aria-expanded="${!collapsed}"><span class="room-title" id="live-room-${i}">${esc(rc?.after.name||r.name)}</span><span class="room-photo-count">${r.photos.length} ${r.photos.length===1?'photo':'photos'}</span><span class="room-review-count">${esc(name(r.room_type))}${r.floor?' · '+esc(r.floor):''}${r.needs_review?' · Needs review':''}</span>${icon('down',17)}</button></h2><div class="room-section-body" ${collapsed?'hidden':''}><div class="live-room-tools">${open&&grouping?`<button class="button quiet small" data-live="edit-room" data-value="${esc(r.id)}">${icon('edit',13)}Rename or retype</button>${list.length>1?`<button class="button quiet small" data-live="merge-room" data-value="${esc(r.id)}">Merge into another room</button>`:''}`:''}${rc?`<span class="live-mock-tag">${esc(rc.summary)}</span>`:''}</div><div class="room-photo-grid">${r.photos.map(card).join('')}</div>${counting?inventoryTable(r,open,stage):''}</div></section>`}).join('')||'<div class="empty-screen"><h2>No rooms yet</h2><p>Rooms appear when the photos are analyzed.</p></div>'}</div>${decisionBar(stage,what)}`;
 }
 rooms=function(){return Live.withStudy(s=>roomsPage(s,G.componentsGate(s)?'grouping':'single'))};
 Live.componentsScreen=()=>Live.withStudy(s=>G.componentsGate(s)?roomsPage(s,'components'):`${Live.steps(s)}${heading('Components','This study reviews its components on the rooms page (it started before the components review existed).',`<a class="button primary" href="${Live.route('rooms')}">Open the rooms review</a>`)}`);
 function inventoryTable(r,open,stage){
  const corr=pending(stage),sug=M.suggestions(r);
  const adding=[...corr.values()].filter(c=>c.entity_type==='room_component'&&c.entity_id===r.id);
  const missing=M.missingLabels(r).filter(s=>!corr.has(`room_component:${r.id}:${s.label}`));
  const addButton=open?`<button class="button quiet small" data-live="add-room-component" data-value="${esc(r.id)}">${icon('plus',13)}Add a component to this room</button>`:'';
  const missingNote=missing.map(s=>`<p class="live-suggest">The photos show ${s.suggested} ${esc(name(s.label).toLowerCase())} this room’s inventory does not list${s.manual_count?` (${s.manual_count} added by an engineer)`:''}.${open?` <button class="button quiet small" data-live="add-room-component" data-value="${esc(r.id)}" data-label="${esc(s.label)}" data-count="${s.suggested}">Add ${s.suggested} to the room</button>`:''}</p>`).join('');
  if(!r.inventory.length&&!adding.length)return `<p class="room-section-empty">No components in this room’s inventory.</p>${missingNote}<div class="live-room-tools">${addButton}</div>`;
  const row=c=>{const k=corr.get('component:'+c.component_id),a=k?.after||{},s=sug.get(c.label),suggest=open&&M.raised(s)&&a.count===undefined&&M.rowFor(r,c.label)?.component_id===c.component_id;
   const count=a.count!==undefined&&a.count!==c.count?`<s class="muted">${c.count}</s> ${a.count}`:String(c.count);
   return `<tr><td><b>${esc(name(a.label||c.label))}</b>${c.added?'<small class="live-mock-tag">added</small>':''}${k?'<small class="live-edited"> · edited</small>':''}</td><td class="num">${count}${suggest?`<div class="live-suggest-count"><span title="The most counted in one photo of this room${s.manual_count?`, ${s.manual_count} of them added by an engineer`:''}">Photos show ${s.suggested}</span> <button class="button quiet small" data-live="use-suggested" data-value="${esc(c.component_id)}" data-room="${esc(r.id)}">Use ${s.suggested}</button></div>`:''}</td><td>${esc(a.material??c.material??'—')}</td><td>${esc(a.attachment??c.attachment??'—')}</td><td class="num">${pct(c.confidence)}</td><td class="text-right">${open?`<button class="button quiet small" data-live="edit-component" data-value="${esc(c.component_id)}" data-room="${esc(r.id)}">${icon('edit',13)}Edit</button>`:''}</td></tr>`};
  const pendingRow=c=>`<tr><td><b>${esc(name(c.after.label))}</b><small class="live-edited"> · to add</small></td><td class="num">${c.after.count}</td><td>${esc(c.after.material||'—')}</td><td>${esc(c.after.attachment||'—')}</td><td class="num">—</td><td class="text-right"><button class="button quiet small" data-live="drop-correction" data-stage="${stage}" data-value="${esc(c.key)}">Remove</button></td></tr>`;
  return `<div class="tablewrap live-inventory"><table><caption class="sr-only">${esc(r.name)} inventory</caption><thead><tr><th>Component</th><th>Count</th><th>Material</th><th>Attachment</th><th>Confidence</th><th></th></tr></thead><tbody>${r.inventory.map(row).join('')}${adding.map(pendingRow).join('')}</tbody></table>${missingNote}<div class="live-room-tools">${addButton}</div><p class="footnote">Counts are the room’s inventory. When the objects counted in one photo (SAM 3’s and the ones you add) exceed it, Basis suggests the higher count; confirm it, edit any count, or add a missing component. They are sent with Request corrections, and the assets use the corrected counts.</p></div>`;
 }
 const roomById=id=>roomsEntry(L.study).data.items.find(r=>r.id===id);
 Live.actions['toggle-room']=(el,id)=>{L.collapsedRooms.has(id)?L.collapsedRooms.delete(id):L.collapsedRooms.add(id);Live.render()};
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
  const m=modal('Edit '+esc(name(c.label)),`<p>Corrects the component’s label, count, material or attachment. The assets are rebuilt with them.</p><div class="field"><label for="live-comp-count">Count in this room</label><input id="live-comp-count" type="number" min="1" max="${M.MAX_COUNT}" step="1" inputmode="numeric" value="${esc(cur.count??c.count)}"></div><div class="field" style="margin-top:12px"><label for="live-comp-label">Component</label><select id="live-comp-label">${LABELS.map(l=>`<option value="${l}" ${(cur.label||c.label)===l?'selected':''}>${esc(name(l))}</option>`).join('')}</select></div><div class="field" style="margin-top:12px"><label for="live-comp-material">Material</label><input id="live-comp-material" value="${esc(cur.material??c.material??'')}" maxlength="120"></div><div class="field" style="margin-top:12px"><label for="live-comp-attachment">Attachment</label><input id="live-comp-attachment" value="${esc(cur.attachment??c.attachment??'')}" maxlength="120" placeholder="e.g. built-in, removable"></div>`,`${closeBtn}<button class="button primary" id="live-save">Add correction</button>`);
  m.querySelector('#live-save').onclick=()=>{const after={},l=m.querySelector('#live-comp-label').value,mat=m.querySelector('#live-comp-material').value.trim(),att=m.querySelector('#live-comp-attachment').value.trim(),count=M.validCount(m.querySelector('#live-comp-count').value);
   if(count===null){toast(`The count is a whole number from 1 to ${M.MAX_COUNT}.`);return}
   if(l!==c.label)after.label=l;if(count!==c.count)after.count=count;if(mat!==(c.material||''))after.material=mat||null;if(att!==(c.attachment||''))after.attachment=att||null;
   m.remove();if(!Object.keys(after).length){pending(editStage()).delete('component:'+cid);Live.render();return}
   addCorrection(editStage(),{key:'component:'+cid,entity_type:'component',entity_id:cid,before:{label:c.label,material:c.material,attachment:c.attachment,count:c.count},after,reason:null,title:`${name(c.label)} (${r.name})`,summary:Object.entries(after).map(([k,v])=>`${k} → ${v??'none'}`).join(', ')});
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
 const objectsEntry=(s,doc)=>Live.load(`objects:${s.id}:${s.revision}:${doc}`,()=>api.photoObjects(s.id,doc,s.revision));
 let layer=null,drag=null;
 viewer=function(){
  return Live.withStudy(s=>{
   const re=roomsEntry(s);if(!re.done)return Live.loading('Loading rooms…');if(re.error)return Live.failed(re.error,'reload-study');
   const all=re.data.items,doc=L.docId||all.find(r=>r.photos.length)?.photos[0]?.document_id;if(!doc)return heading('No photos','This study has no analyzed photos yet.');
   const room=all.find(r=>r.photos.some(p=>p.document_id===doc))||all[0],photos=room?.photos||[],at=photos.findIndex(p=>p.document_id===doc),photo=photos[at]||{document_id:doc,filename:'Photo'};
   const oe=objectsEntry(s,doc),v=L.viewer;if(v.doc!==doc){Object.assign(v,{doc,mode:'spotlight',focusLabel:null,selectedId:null,tool:null,tap:null})}
   const data=oe.done&&!oe.error?oe.data:null,items=data?.items||[],counts=data?.counts||{},labels=Object.keys(counts).filter(l=>counts[l]>0).sort((a,b)=>counts[b]-counts[a]||a.localeCompare(b));
   if(data&&(!v.focusLabel||!counts[v.focusLabel]))v.focusLabel=labels[0]||null;
   // Edited at the components review (the single rooms review for a study without it).
   const back=G.editScreen(s),backLabel=back==='components'?'Back to components':'Back to rooms';
   const open=Live.gateOpen(editStage()),focus=v.focusLabel,focusItems=items.filter(i=>i.label===focus&&i.counted).sort((a,b)=>a.box.x1-b.box.x1||a.box.y1-b.box.y1),sel=items.find(i=>i.id===v.selectedId);
   const loc=data?.localization,inv=label=>(room?.inventory||[]).filter(c=>c.label===label&&(c.document_id===doc||(c.seen_in||[]).includes(doc)));
   const prev=photos[at-1],next=photos[at+1];
   return `${heading(esc(photo.filename),'Review the components found in this photo, then continue through the room.',`<a class="button" href="${Live.route(back)}">${icon('back')}${backLabel}</a>`)}${readOnlyNote(editStage())}<div class="component-review-nav"><label for="live-room-select">Room<select id="live-room-select" aria-label="Choose room to review">${all.filter(r=>r.photos.length).map(r=>`<option value="${esc(r.photos[0].document_id)}" ${r.id===room?.id?'selected':''}>${esc(r.name)}</option>`).join('')}</select></label><nav aria-label="Room photo navigation">${prev?`<a class="button small" href="${Live.route('viewer',s.id,prev.document_id)}">${icon('back',16)}Previous photo</a>`:`<button class="button small" disabled>${icon('back',16)}Previous photo</button>`}<span>${at+1} of ${photos.length}</span>${next?`<a class="button primary small" href="${Live.route('viewer',s.id,next.document_id)}">Next photo ${icon('arrow',16)}</a>`:`<button class="button primary small" disabled>Next photo ${icon('arrow',16)}</button>`}</nav></div><div class="viewer component-workbench live-workbench"><div class="photo-stage"><div class="photo-toolbar"><span>${esc(s.property_name)} / ${esc(room?.name||'')}</span><div class="row gap6"><div class="live-mode-switch" role="group" aria-label="Highlight">${[['spotlight','Spotlight'],['overview','Show all']].map(([m,l])=>`<button class="button small ${v.mode===m||(m==='spotlight'&&v.mode==='single')?'primary':''}" data-live="viewer-mode" data-value="${m}" aria-pressed="${v.mode===m}">${l}</button>`).join('')}</div>${open?`<button class="button small ${v.tool?'primary':''}" data-live="add-object" aria-pressed="${Boolean(v.tool)}">${icon('plus',14)}${v.tool?'Adding…':'Add object'}</button>`:''}</div></div>${v.tool?addBar(v,items,focus):''}<div class="photo-canvas live-photo-canvas ${v.tool?'is-adding':''}">${Live.photo(doc,photo.filename,'live-viewer-photo')}${drag?.rect?'':''}</div><div class="photo-caption"><span>${!data?'Loading objects…':loc?(loc.status==='done'?`SAM 3 · ${items.filter(i=>i.counted).length} counted instances${loc.model_version?` · <span title="${esc(loc.model_version)}">model ${esc(loc.model_version.slice(0,8))}</span>`:''}`:loc.status==='failed'?'Localization failed: '+esc(loc.reason||'unknown reason')+'. Boxes and taps still work.':'Localization skipped'+(loc.reason?': '+esc(loc.reason):'')):'Not localized yet'}</span><button class="button quiet small" data-live="download-doc" data-value="${esc(doc)}">${icon('download',13)}Download original</button></div><nav class="component-filmstrip" aria-label="Photos in ${esc(room?.name||'room')}"><span>${esc(room?.name||'')} photos</span><div>${photos.map(p=>`<a href="${Live.route('viewer',s.id,p.document_id)}" aria-label="Review ${esc(p.filename)}" aria-current="${p.document_id===doc}">${Live.photo(p.document_id,'')}<span>${esc(p.filename)}</span></a>`).join('')}</div></nav></div><aside class="object-aside" id="live-components">${!oe.done?Live.loading('Loading objects…'):oe.error?Live.failed(oe.error,'reload-objects'):componentsPanel({items,counts,labels,focus,focusItems,sel,open,inv})}</aside></div><div class="actionbar component-review-actions"><p>${esc(room?.name||'')} · ${photos.length} ${photos.length===1?'photo':'photos'}</p><div class="row">${next?`<a class="button" href="${Live.route('viewer',s.id,next.document_id)}">Next photo ${icon('arrow',16)}</a>`:`<a class="button" href="${Live.route(back)}">${icon('back',16)}${backLabel}</a>`}</div></div>`;
  });
 };
 function addBar(v,items,focus){
  const tools=[['box','Draw a box'],['point','Tap it']],tap=v.tap,shown=tap?items.find(i=>i.id===tap.ids[tap.at]):null;
  const help=v.busy?`<span class="small live-busy"><span class="live-spinner"></span>${esc(v.busy)}</span>`:v.tool==='box'?'<span class="small">Drag a box around the object. Basis outlines it with SAM 3.</span>':tap&&shown?`<span class="small"><b>${tap.at+1} of ${tap.ids.length}</b> under your tap · ${shown.score===null?'added box':'score '+shown.score.toFixed(2)} · ${shown.counted?'already counted':'not counted yet'}</span><button class="button quiet small" data-live="tap-cycle" data-value="-1" ${tap.ids.length>1?'':'disabled'} aria-label="Previous candidate">${icon('back',13)}</button><button class="button quiet small" data-live="tap-cycle" data-value="1" ${tap.ids.length>1?'':'disabled'} aria-label="Next candidate">${icon('arrow',13)}</button><button class="button primary small" data-live="tap-confirm" ${shown.counted?'disabled title="Already counted"':''}>Use this one</button>`:'<span class="small">Tap the object: Basis shows what SAM 3 found under it. Tap again or press ← → to see the others, then confirm.</span>';
  return `<div class="live-add-bar" role="region" aria-label="Add an object"><label>Component<select id="live-add-label">${LABELS.filter(l=>l!=='other').map(l=>`<option value="${l}" ${l===(v.addLabel||focus)?'selected':''}>${esc(name(l))}</option>`).join('')}</select></label><div class="live-mode-switch" role="group" aria-label="How to add">${tools.map(([t,l])=>`<button class="button small ${v.tool===t?'primary':''}" data-live="add-tool" data-value="${t}" aria-pressed="${v.tool===t}">${l}</button>`).join('')}</div>${help}<button class="button quiet small" data-live="add-cancel">Cancel</button></div>`;
 }
 function componentsPanel({items,counts,labels,focus,focusItems,sel,open,inv}){
  const v=L.viewer,colors=label=>BasisMaskLayer.labelColor(label,[...new Set(items.map(i=>i.label))].sort());
  if(v.mode==='single'&&sel){
   const n=focusItems.findIndex(i=>i.id===sel.id)+1,manual=sel.source==='manual';
   const outline=`<div class="line-item"><span>Outline</span><strong>${esc(M.outlineText(sel))}</strong></div>${manual&&sel.mask_reason?`<p class="footnote">No outline: ${esc(sel.mask_reason)}</p>`:''}`;
   return `<div class="panel-head"><div><h2>${esc(name(sel.label))}${n?' · '+n:''}</h2><p class="small">${manual?'Added by an engineer':'Found by SAM 3'}</p></div><button class="button quiet small" data-live="viewer-mode" data-value="spotlight">${icon('back',13)}All ${esc(name(sel.label).toLowerCase())}</button></div><div class="live-instance-detail"><div class="line-item"><span>Score</span><strong>${sel.score===null?'—':sel.score.toFixed(2)}</strong></div><div class="line-item"><span>Size</span><strong>${sizeLabel(sel.area)} of the photo</strong></div>${outline}<div class="line-item"><span>Counted</span><strong>${sel.counted?'Yes':'No (a candidate)'}</strong></div>${manual?`<div class="line-item"><span>Added</span><strong>${Live.when(sel.created_at)}</strong></div>`:''}${open?(manual?`<button class="button danger" data-live="delete-object" data-value="${esc(sel.id)}">Delete this object</button>`:`<p class="footnote">Objects SAM 3 found stay as evidence and cannot be deleted. Correct the component in the room inventory, or ask for the photo to be re-analyzed.</p>`):''}</div>`;
  }
  if(v.mode==='overview')return `<div class="panel-head"><div><h2>All components</h2><p class="small">${items.filter(i=>i.counted).length} counted instances</p></div></div><div class="object-list">${labels.map(l=>`<div class="object-row"><button class="object-select" data-live="focus-label" data-value="${l}"><b><span class="live-swatch" style="background:rgb(${colors(l)})"></span>${esc(name(l))}</b><strong class="num">${counts[l]}</strong></button></div>`).join('')||'<p class="small">No counted components.</p>'}</div>`;
  return `<div class="panel-head"><div><h2>Components</h2><p class="small">${labels.length} ${labels.length===1?'type':'types'} · ${items.filter(i=>i.counted).length} counted</p></div><span class="small muted">Count</span></div><div class="object-list">${labels.map(l=>{const room=inv(l)[0];return `<div class="object-row ${l===focus?'selected':''}"><button class="object-select" data-live="focus-label" data-value="${l}" aria-pressed="${l===focus}"><b>${esc(name(l))}</b><strong class="num">${counts[l]}</strong></button>${room?`<p>${esc(room.material||'Material not specified')}${room.attachment?' · '+esc(room.attachment):''} · ${room.count} in the room</p>`:''}${l===focus?`<ol class="live-instances">${focusItems.map((i,n)=>`<li><button data-live="select-object" data-value="${esc(i.id)}"><span class="live-pin">${n+1}</span>${i.source==='manual'?'Added':'Score '+(i.score??0).toFixed(2)}<span class="small">${sizeLabel(i.area)}</span></button></li>`).join('')}</ol>`:''}</div>`}).join('')||`<div class="component-empty">${icon('image',27)}<h3>No components counted</h3><p>${open?'Use Add object to tap or box a component.':'Nothing was counted on this photo.'}</p></div>`}</div><div class="object-footer"><p class="small">Pins number each counted instance: the count is the number of pins.</p></div>`;
 }
 Live.afterRender.push(()=>{
  layer?.destroy();layer=null;
  const img=root.querySelector('img.live-viewer-photo');if(!img||state.screen!=='viewer')return;
  const v=L.viewer,e=L.cache.get(`objects:${L.studyId}:${L.study?.revision}:${v.doc}`);
  layer=BasisMaskLayer.attach(img,{onPick:(hit,u,x,ev)=>onPhotoClick(hit,u,x,ev)});
  // A tap preview shows the candidate being considered alone, counted or not.
  if(e?.data)layer.set({instances:e.data.items,mode:v.tap?'single':v.mode,focusLabel:v.focusLabel,selectedId:v.tap?v.tap.ids[v.tap.at]:v.selectedId});
  img.addEventListener('pointerdown',startBox);
 });
 function onPhotoClick(hit,u,v,ev){
  const vw=L.viewer;
  if(vw.tool==='point'){previewTap(u,v);return}
  if(vw.tool==='box')return;
  if(hit){if(vw.mode==='overview'){vw.focusLabel=hit.label;vw.mode='spotlight';vw.selectedId=null}else{vw.focusLabel=hit.label;vw.selectedId=hit.id;vw.mode='single'}}
  else if(vw.mode==='single'){vw.mode='spotlight';vw.selectedId=null}
  else return;
  Live.render();
 }
 function previewTap(u,v){
  const vw=L.viewer,label=selectedAddLabel(),items=L.cache.get(`objects:${L.studyId}:${L.study?.revision}:${vw.doc}`)?.data?.items||[];
  const tap=vw.tap,again=tap&&tap.label===label&&Math.hypot(u-tap.u,v-tap.v)<0.02;
  if(again)tap.at=M.cycle(tap.at,tap.ids.length,1);
  else{
   const under=M.candidatesUnder(items,label,u,v,globalThis.BasisRle);
   if(!under.length){vw.tap=null;vw.tool='box';toast(`SAM 3 found no ${name(label).toLowerCase()} there. Draw a box around it instead.`);Live.render();return}
   vw.tap={u,v,label,ids:under.map(i=>i.id),at:0};
  }
  Live.render();
 }
 const selectedAddLabel=()=>root.querySelector('#live-add-label')?.value||L.viewer.addLabel||L.viewer.focusLabel||'light_fixture';
 function startBox(ev){
  if(L.viewer.tool!=='box'||ev.button!==0)return;ev.preventDefault();
  // The photo's pixels inside the <img> box (object-fit letterboxes a 4:3 photo in the
  // design's 3:2 frame): the box is clamped to them and normalized to them, as the masks are.
  const img=ev.currentTarget,el=img.getBoundingClientRect(),c=BasisMaskLayer.contentRect(img),r={left:el.left+c.x,top:el.top+c.y,right:el.left+c.x+c.w,bottom:el.top+c.y+c.h,width:c.w,height:c.h},band=document.createElement('div');band.className='live-box-band';img.parentElement.append(band);
  const clamp=(x,a,b)=>Math.max(a,Math.min(b,x)),x0=clamp(ev.clientX,r.left,r.right),y0=clamp(ev.clientY,r.top,r.bottom);
  const move=e=>{const x=clamp(e.clientX,r.left,r.right),y=clamp(e.clientY,r.top,r.bottom);Object.assign(band.style,{left:(Math.min(x,x0)-el.left+img.offsetLeft)+'px',top:(Math.min(y,y0)-el.top+img.offsetTop)+'px',width:Math.abs(x-x0)+'px',height:Math.abs(y-y0)+'px'})};
  const up=e=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);band.remove();
   const box=M.dragBox(r,x0,y0,clamp(e.clientX,r.left,r.right),clamp(e.clientY,r.top,r.bottom));
   if(box.x2-box.x1<0.01||box.y2-box.y1<0.01){toast('Drag a larger box around the object.');return}
   addObject({kind:'box',label:selectedAddLabel(),box},'Outlining it with SAM 3…');
  };
  window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);move(ev);
 }
 async function addObject(body,busy='Adding…'){
  const v=L.viewer,doc=v.doc;
  await Live.guarded('add-object',async()=>{
   v.busy=busy;Live.render();
   try{
    const r=await api.addObject(L.studyId,doc,body);
    // Nothing was added: no reload (a reload would re-render the page under the dialog).
    if(M.isDuplicate(r)){v.busy=null;Object.assign(v,{tool:null,tap:null,focusLabel:r.object.label,selectedId:r.object.id,mode:'single'});Live.render();askDuplicate(body,r);return}
    Live.invalidate(`objects:${L.studyId}:${L.study.revision}:${doc}`);Live.invalidate(`rooms:${L.studyId}`);
    Object.assign(v,{tool:null,tap:null,focusLabel:r.object.label,selectedId:r.object.id,mode:'single'});
    toast(M.addedMessage(r,name));
    Live.render();
   }catch(e){
    if(e.status===422&&body.kind==='point'){v.tool='box';toast('Nothing matched there. Draw a box around the object instead.');Live.render();return}
    throw e;
   }finally{v.busy=null}
  });
 }
 // The API found the selection already counted: show that object, offer to add anyway.
 function askDuplicate(body,r){
  const what=name(r.object.label).toLowerCase(),share=r.overlap===null||r.overlap===undefined?'':` (it lies ${Math.round(r.overlap*100)}% inside it)`;
  const m=modal('Already counted?',`<p>This looks like the ${esc(what)} already counted on this photo, now highlighted${esc(share)}.</p><p class="small">Add it anyway when they are different objects, such as two lights one outline covers.</p>`,`<button class="button" id="live-use-existing">Use the existing one</button><button class="button primary" id="live-add-anyway">Add anyway</button>`);
  m.querySelector('#live-use-existing').onclick=()=>{m.remove();toast(`Kept the counted ${what}.`)};
  m.querySelector('#live-add-anyway').onclick=()=>{m.remove();addObject({...body,allow_duplicate:true},body.kind==='box'?'Outlining it with SAM 3…':'Adding…')};
 }
 Live.actions['viewer-mode']=(el,m)=>{Object.assign(L.viewer,{mode:m,selectedId:null});Live.render()};
 Live.actions['focus-label']=(el,l)=>{Object.assign(L.viewer,{focusLabel:l,mode:'spotlight',selectedId:null});Live.render()};
 Live.actions['select-object']=(el,id)=>{Object.assign(L.viewer,{selectedId:id,mode:'single'});Live.render()};
 Live.actions['add-object']=()=>{const v=L.viewer;v.tool=v.tool?null:'box';v.tap=null;v.addLabel=v.focusLabel;Live.render()};
 Live.actions['add-tool']=(el,t)=>{L.viewer.addLabel=selectedAddLabel();L.viewer.tool=t;L.viewer.tap=null;Live.render()};
 Live.actions['add-cancel']=()=>{L.viewer.tool=null;L.viewer.tap=null;Live.render()};
 Live.actions['tap-cycle']=(el,step)=>{const t=L.viewer.tap;if(!t)return;t.at=M.cycle(t.at,t.ids.length,Number(step));Live.render()};
 Live.actions['tap-confirm']=()=>{const t=L.viewer.tap;if(!t)return;addObject({kind:'candidate',candidate_id:t.ids[t.at]})};
 Live.actions['reload-objects']=()=>{Live.invalidate(`objects:${L.studyId}`);Live.render()};
 Live.actions['delete-object']=(el,id)=>{const m=modal('Delete this object?','<p>It is removed from the photo’s count. The deletion is recorded with your name.</p>',`${closeBtn}<button class="button danger" id="live-confirm">Delete</button>`);
  m.querySelector('#live-confirm').onclick=()=>Live.guarded('delete-object',async()=>{m.remove();const doc=L.viewer.doc;await api.deleteObject(L.studyId,doc,id);Live.invalidate(`objects:${L.studyId}:${L.study.revision}:${doc}`);Live.invalidate(`rooms:${L.studyId}`);Object.assign(L.viewer,{selectedId:null,mode:'spotlight'});toast('Object deleted.');Live.render()})};
 root.addEventListener('change',e=>{
  if(e.target.id==='live-room-select'){e.stopImmediatePropagation();Live.go('viewer',L.studyId,e.target.value)}
  if(e.target.id==='live-add-label'){e.stopImmediatePropagation();L.viewer.addLabel=e.target.value;if(L.viewer.tap){L.viewer.tap=null;Live.render()}}
  if(e.target.id==='live-assets-filter'){e.stopImmediatePropagation();L.assetsFilter=e.target.value;Live.render()}
 },true);
 document.addEventListener('keydown',e=>{if(state.screen!=='viewer'||e.target.closest('input,select,textarea,.modal'))return;
  const tap=L.viewer.tap;
  if(tap&&(e.key==='ArrowRight'||e.key==='ArrowLeft')){e.preventDefault();tap.at=M.cycle(tap.at,tap.ids.length,e.key==='ArrowRight'?1:-1);Live.render();return}
  if(tap&&e.key==='Enter'){e.preventDefault();Live.actions['tap-confirm']();return}
  if(e.key==='Escape'&&tap){L.viewer.tap=null;Live.render();return}
  if(e.key==='Escape'&&(L.viewer.tool||L.viewer.mode==='single')){L.viewer.tool=null;if(L.viewer.mode==='single'){L.viewer.mode='spotlight';L.viewer.selectedId=null}Live.render()}
  if((e.key==='ArrowRight'||e.key==='ArrowLeft')&&!L.viewer.tool){const link=root.querySelector(`.component-review-nav nav a.button${e.key==='ArrowRight'?'.primary':':not(.primary)'}`);link?.click()}
 });

 /* ---------- assets review ---------- */
 const assetsEntry=s=>Live.load(`assets:${s.id}:${s.revision}`,()=>api.assets(s.id,s.revision));
 function bucketTotals(items){const t={};for(const a of items){const v=Number(a.cost?.total||0);t[a.bucket]=(t[a.bucket]||0)+v}return t}
 assets=function(){
  return Live.withStudy(s=>{
   const e=assetsEntry(s);if(!e.done)return `${Live.steps(s)}${Live.loading('Loading assets…')}`;if(e.error)return Live.failed(e.error,'reload-study');
   const items=e.data.items,shown=items.filter(a=>L.assetsFilter==='all'||(L.assetsFilter==='review'?a.needs_review:!a.needs_review)),totals=bucketTotals(items),total=Object.values(totals).reduce((a,b)=>a+b,0),corr=pending('assets_review');
   return `${Live.steps(s)}${heading('Review assets','Check each component’s classification, quantity and cost against its evidence.')}${readOnlyNote('assets_review')}<div class="metrics"><div class="metric"><p>Assets</p><div class="value num">${items.length}</div><span class="small">${items.filter(a=>a.needs_review).length} need review</span></div>${Object.keys(BUCKETS).filter(b=>totals[b]).slice(0,2).map(b=>`<div class="metric"><p>${BUCKETS[b]} property</p><div class="value num">${money(totals[b])}</div></div>`).join('')}<div class="metric"><p>Total cost</p><div class="value num">${money(total)}</div><span class="small">From the cost table</span></div></div><div class="toolbar"><label class="library-sort">Show<select id="live-assets-filter" aria-label="Filter assets">${[['all','All assets'],['review','Needs review'],['ok','Reviewed by Basis']].map(([v,l])=>`<option value="${v}" ${L.assetsFilter===v?'selected':''}>${l}</option>`).join('')}</select></label></div><div class="panel tablewrap"><table><thead><tr><th>Component</th><th>Room</th><th>Recovery period</th><th>Quantity</th><th>Unit cost</th><th>Total cost</th><th>Citations</th><th>Review</th><th></th></tr></thead><tbody>${shown.map(a=>{const c=corr.get(a.id);return `<tr><td><b>${esc(a.label?name(a.label):a.category)}</b><p class="small">${esc(a.category)}${a.count?` · ${a.count}`:''}${c?' · <span class="live-edited">edited</span>':''}</p></td><td>${esc(a.room?.name||'—')}</td><td><span class="badge">${esc(BUCKETS[c?.after.bucket||a.bucket]||a.bucket)}</span><p class="small">${esc(a.method)}${a.irs_section?' · '+esc(a.irs_section):''}</p></td><td class="num">${a.takeoff?`${esc(Number(a.takeoff.quantity).toLocaleString('en-US'))} <span class="muted">${esc(a.takeoff.unit)}</span>`:'—'}</td><td class="num">${money(a.cost?.unit_cost)}</td><td class="num strong">${money(a.cost?.total)}</td><td class="num">${a.verified_citations}/${a.citations}</td><td><span class="badge ${a.needs_review?'review':'complete'}" title="${esc(BasisReasons.title(a.review_reasons))}">${a.needs_review?'Needs review':'OK'}</span></td><td><a class="button small" href="${Live.route('asset-detail',s.id,a.id)}">Review ${icon('chevron',12)}</a></td></tr>`}).join('')||'<tr><td colspan="9"><div class="empty-screen"><h3>No assets here</h3></div></td></tr>'}</tbody></table></div>${decisionBar('assets_review','Assets')}`;
  });
 };
 assetDetail=function(){
  return Live.withStudy(s=>{
   const e=assetsEntry(s);if(!e.done)return Live.loading('Loading assets…');if(e.error)return Live.failed(e.error,'reload-study');
   const items=e.data.items,a=items.find(x=>x.id===L.docId)||items[0];if(!a)return heading('No assets','This study has no assets yet.');
   const ev=Live.load(`evidence:${s.id}:${s.revision}:${a.id}`,()=>api.evidence(s.id,a.id)),evidence=ev.data?.items?.[0],c=pending('assets_review').get(a.id),after=c?.after||{};
   return `${Live.steps(s)}${heading(esc(a.label?name(a.label):a.category),`${esc(s.property_name)}${a.room?' / '+esc(a.room.name):''} / ${esc(a.category)}`,`<a class="button" href="${Live.route('assets')}">${icon('back')}All assets</a>`)}<div class="asset-layout"><nav class="asset-nav" aria-label="Assets">${items.map(x=>`<a class="${x.id===a.id?'active':''}" href="${Live.route('asset-detail',s.id,x.id)}">${esc(x.label?name(x.label):x.category)}<span class="small">${x.needs_review?'○':'✓'}</span></a>`).join('')}</nav><section class="asset-main"><div class="row between"><span class="badge ${a.needs_review?'review':'complete'}">${a.needs_review?'Needs review':'OK'}</span><span class="small muted">Confidence ${pct(a.confidence)}</span></div>${a.review_reasons.length?`<div class="note" style="margin-top:14px"><h3>Why it needs review</h3><ul>${BasisReasons.items(a.review_reasons)}</ul></div>`:''}<div class="line-item"><span class="muted">Category</span><strong>${esc(after.category||a.category)}</strong></div><div class="line-item"><span class="muted">Recovery period</span><strong>${esc(BUCKETS[after.bucket||a.bucket])} · ${esc(after.recovery_period||a.recovery_period)} years</strong></div><div class="line-item"><span class="muted">Method</span><strong>${esc(a.method)}</strong></div><div class="line-item"><span class="muted">IRS section</span><strong>${esc(after.irs_section||a.irs_section||'—')}</strong></div><div class="line-item"><span class="muted">Quantity</span><strong>${a.takeoff?`${esc(a.takeoff.quantity)} ${esc(a.takeoff.unit)}`:'—'}</strong></div>${a.takeoff?.formula?`<p class="small">${esc(a.takeoff.formula)}</p>`:''}<div class="line-item"><span class="muted">Unit cost</span><strong>${money(a.cost?.unit_cost)}</strong></div><div class="line-item"><span class="muted">Total cost</span><strong>${money(a.cost?.total)}</strong></div>${a.cost?`<p class="small">Cost row ${esc(a.cost.source_row||'—')}${a.cost.location_factor?' · location factor '+esc(a.cost.location_factor):''}${a.cost.cost_year?' · '+a.cost.cost_year+' costs':''}</p>`:''}${Live.gateOpen('assets_review')?`<button class="button" data-live="edit-asset" data-value="${esc(a.id)}" style="margin-top:16px">${icon('edit',14)}${c?'Edit correction':'Correct classification'}</button>`:''}<h3 style="margin-top:24px">Citations</h3>${!ev.done?Live.loading('Loading evidence…'):ev.error?Live.failed(ev.error):BasisCitations.list(evidence?.citations,a.review_reasons)}</section><aside class="reference-panel"><div><h3>Photo evidence</h3>${!ev.done?'':evidence?.photos?.length?evidence.photos.map(p=>`<figure class="live-evidence-photo"><div class="photo-canvas">${Live.photo(p.document_id,p.filename,'live-evidence-img')}</div><figcaption class="small">${esc(p.filename)} · ${p.role==='source'?'where it was seen':'its room'} · ${(n=>`${n} counted ${n===1?'instance':'instances'}`)(p.instances.filter(i=>i.counted&&(!evidence.component_label||i.label===evidence.component_label)).length)} <a class="screen-link" href="${Live.route('viewer',s.id,p.document_id)}">Open</a></figcaption></figure>`).join(''):'<p class="small">No photos linked to this asset.</p>'}</div></aside></div>${decisionBar('assets_review','Assets')}`;
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
 Live.actions['edit-asset']=(el,id)=>{const a=assetsEntry(L.study).data.items.find(x=>x.id===id),cur=pending('assets_review').get(id)?.after||{};
  const m=modal('Correct '+esc(a.label?name(a.label):a.category),`<p>The asset schedule is rebuilt with your correction when you request corrections.</p><div class="field"><label for="live-asset-category">Category</label><input id="live-asset-category" value="${esc(cur.category??a.category)}" maxlength="120"></div><div class="field" style="margin-top:12px"><label for="live-asset-bucket">Recovery class</label><select id="live-asset-bucket">${Object.entries(BUCKETS).map(([k,l])=>`<option value="${k}" ${(cur.bucket||a.bucket)===k?'selected':''}>${l}</option>`).join('')}</select></div><div class="field" style="margin-top:12px"><label for="live-asset-period">Recovery period (years)</label><input id="live-asset-period" inputmode="decimal" value="${esc(cur.recovery_period??a.recovery_period)}"></div><div class="field" style="margin-top:12px"><label for="live-asset-irs">IRS section</label><input id="live-asset-irs" value="${esc(cur.irs_section??a.irs_section??'')}" maxlength="64"></div><div class="field" style="margin-top:12px"><label for="live-asset-reason">Reason</label><textarea id="live-asset-reason">${esc(pending('assets_review').get(id)?.reason||'')}</textarea></div>`,`${closeBtn}<button class="button primary" id="live-save">Add correction</button>`);
  m.querySelector('#live-save').onclick=()=>{const after={},cat=m.querySelector('#live-asset-category').value.trim(),b=m.querySelector('#live-asset-bucket').value,p=m.querySelector('#live-asset-period').value.trim(),irs=m.querySelector('#live-asset-irs').value.trim(),reason=m.querySelector('#live-asset-reason').value.trim()||null;
   if(cat&&cat!==a.category)after.category=cat;if(b!==a.bucket)after.bucket=b;if(p&&p!==String(a.recovery_period))after.recovery_period=p;if(irs!==(a.irs_section||''))after.irs_section=irs||null;
   if(p&&!(Number(p)>0&&Number(p)<1000)){toast('The recovery period is a positive number of years.');return}
   m.remove();if(!Object.keys(after).length){pending('assets_review').delete(id);Live.render();return}
   addCorrection('assets_review',{key:id,entity_type:'asset',entity_id:id,before:{category:a.category,bucket:a.bucket,recovery_period:a.recovery_period,irs_section:a.irs_section},after,reason,title:a.label?name(a.label):a.category,summary:Object.entries(after).map(([k,v])=>`${k} → ${k==='bucket'?BUCKETS[v]:v??'none'}`).join(', ')});
  }};

 /* ---------- completed schedule ---------- */
 complete=function(){
  return Live.withStudy(s=>{
   const e=assetsEntry(s);if(!e.done)return Live.loading('Loading the schedule…');if(e.error)return Live.failed(e.error,'reload-study');
   const items=e.data.items,totals=bucketTotals(items),total=Object.values(totals).reduce((a,b)=>a+b,0);
   return `${Live.steps(s)}${heading('Study schedule',s.status==='completed'?'The engineer-approved allocation.':'The schedule as it stands. It is final once the assets review is approved.')}<section class="panel"><div class="report-cover"><div><span class="badge ${s.status==='completed'?'complete':'review'}">${esc(Live.STATUS[s.status]||s.status)}</span><h2>${esc(s.property_name)}</h2><p>Cost segregation schedule</p><p class="small" style="margin-top:10px">Revision ${s.revision} · updated ${Live.when(s.updated_at)}</p></div><div>${logo()}</div></div><div class="panel-body"><div class="metrics"><div class="metric"><p>Total allocated cost</p><div class="value num">${money(total)}</div></div>${Object.keys(BUCKETS).filter(b=>totals[b]).map(b=>`<div class="metric"><p>${BUCKETS[b]}</p><div class="value num">${money(totals[b])}</div><span class="small">${total?Math.round(100*totals[b]/total):0}%</span></div>`).join('')}</div><div class="panel tablewrap"><table><thead><tr><th>Component</th><th>Room</th><th>Class</th><th>Quantity</th><th>Total</th></tr></thead><tbody>${items.map(a=>`<tr><td><b>${esc(a.label?name(a.label):a.category)}</b><p class="small">${esc(a.category)}</p></td><td>${esc(a.room?.name||'—')}</td><td><span class="badge">${esc(BUCKETS[a.bucket])}</span></td><td class="num">${a.takeoff?`${esc(a.takeoff.quantity)} ${esc(a.takeoff.unit)}`:'—'}</td><td class="num strong">${money(a.cost?.total)}</td></tr>`).join('')}</tbody></table></div><div class="actionbar"><p>Export is not available yet: the API has no report or export endpoint.</p><div class="row"><button class="button" data-live="export-csv">${icon('download')}Download table (CSV)</button></div></div></div></section>`;
  });
 };
 Live.actions['export-csv']=()=>{const items=assetsEntry(L.study).data.items,rows=[['component','category','room','class','recovery_period','method','quantity','unit','unit_cost','total','needs_review']].concat(items.map(a=>[a.label||'',a.category,a.room?.name||'',BUCKETS[a.bucket],a.recovery_period,a.method,a.takeoff?.quantity||'',a.takeoff?.unit||'',a.cost?.unit_cost||'',a.cost?.total||'',a.needs_review]));
  const csv=rows.map(r=>r.map(v=>{const s=String(v??'');return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s}).join(',')).join('\n'),url=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
  const a=Object.assign(document.createElement('a'),{href:url,download:`${L.study.property_name.replace(/[^\w-]+/g,'_')}_schedule_r${L.study.revision}.csv`});document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)};
 Live.render();
})();
