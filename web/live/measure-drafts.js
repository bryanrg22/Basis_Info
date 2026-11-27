/* AI-drafted measurements on the asset (EVAL_REPORT 110; docs/api/MEASURE_DRAFTS.md, the contract;
 * web/FRONTEND_BACKLOG.md section 23). The field names are the backend's
 * (basis/domain/measure_drafts.py draft_record, priced, resolved; basis/activities/process_assets.py):
 *
 *   takeoff.inputs.quantity_source  "draft" while the row stands on a draft
 *   takeoff.inputs.draft            the record: drawn_by, verified, quantity, unit,
 *                                   expected_error_pct, error_source, source, replaces
 *                                   ({formula, quantity} or {missing_input}); exterior: factor,
 *                                   factor_reason; interior: photos, answer, notes, confidence,
 *                                   room_fully_seen; while pending: exposure_usd (null when
 *                                   unpriced), verify_above_usd, blocks; once acted on:
 *                                   verified true, or replaced_by + engineer_quantity
 *   takeoff.inputs.measurement      exterior only: the shape (drawn_by model:lidar-shell@v1)
 *   takeoff.inputs.shape_quantity   exterior: the shape's own quantity ("3412.55 SF")
 *   takeoff.inputs.draft_not_used   a roof, siding or gutter row not drafted: why
 *   GET .../result blockers         {code: "measure:draft_unverified", message, assets, fix}
 *
 * The pure part (text, ordering, the correction body) is exported for node tests; in live mode the
 * same file adds the asset page's card (Live.draftCard), the held rows (Live.heldDrafts) and their
 * actions. Customer-facing words say "drafted by Basis"; drawn_by is never shown.
 */
(function(scope){
 'use strict';
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const BLOCKER='measure:draft_unverified';
 const INTERIOR_SOURCE='verified Basis draft from the room photos';// the typed quantity's quantity_source (backlog 23.3)

 /* ---------- reading the record ---------- */
 const inputsOf=a=>{const i=a?.takeoff?.inputs;return i&&typeof i==='object'&&!Array.isArray(i)?i:{}};
 function draftOf(a){const d=inputsOf(a).draft;return d&&typeof d==='object'&&!Array.isArray(d)?d:null}
 // basis.domain.measure_drafts.pending: not verified and not replaced.
 const pending=d=>!!d&&d.verified!==true&&(d.replaced_by===undefined||d.replaced_by===null);
 // An interior draft has no shape: the room's photos and the model's numbers (draft_record extra).
 const interior=d=>!!d&&(Array.isArray(d.photos)||(d.answer&&typeof d.answer==='object')||/@measure_interior_reference-v\d+$/.test(String(d.drawn_by||'')));
 const num=v=>{if(v===null||v===undefined||v===''||typeof v==='boolean')return null;const n=Number(String(v).replace(/,/g,''));return Number.isFinite(n)?n:null};
 // "42.00" -> "42", "4216.89" -> "4,216.89"
 const fmt=(v,d=2)=>{const n=num(v);return n===null?'—':n.toLocaleString('en-US',{maximumFractionDigits:d})};
 const qty=(v,unit)=>`${fmt(v)}${unit?' '+unit:''}`;
 const money=v=>{const n=num(v);return n===null?null:n.toLocaleString('en-US',{style:'currency',currency:'USD',minimumFractionDigits:n%1?2:0,maximumFractionDigits:2})};
 // "357 SF" or "357.00 SF" (the backend writes "<quantity> <unit>") -> [number, unit]
 const splitQty=s=>{const m=/^\s*(-?[\d.,]+)\s*([A-Za-z]*)\s*$/.exec(String(s??''));return m?[num(m[1]),m[2]||'']:[null,''] };

 /* ---------- exact decimals for the factor (the server multiplies Decimals) ---------- */
 function dec(s){const m=/^(-?)(\d+)(?:\.(\d+))?$/.exec(String(s).trim());if(!m)return null;const f=m[3]||'';return {n:BigInt(m[1]+m[2]+f),s:f.length}}
 // a x b rounded to 2 places: half even (Decimal.quantize in _with_factor, for an engineer's edit)
 // or half up (_q in measure_drafts, the draft itself).
 function times(a,b,mode='even'){
  const x=dec(a),y=dec(b);if(!x||!y)return null;let p=x.n*y.n,sc=x.s+y.s;
  if(sc<=2){return (Number(p)*10**(2-sc)/100).toFixed(2)}
  const div=10n**BigInt(sc-2),neg=p<0n;if(neg)p=-p;let q=p/div;const r=p%div,half=div/2n;
  if(r>half||(r===half&&(mode==='up'||q%2n===1n)))q+=1n;
  const s=q.toString().padStart(3,'0');return `${neg?'-':''}${s.slice(0,-2)}.${s.slice(-2)}`;
 }

 /* ---------- 1. the factor beside the tool's total ---------- */
 // shape: the measurement's own quantity (a string, as PM.measure gives it) and its kind; untouched:
 // the shape is the stored draft itself (its row quantity is the record's own, rounded half up).
 function factorLine(d,shapeQuantity,unit,{kind=null,untouched=false}={}){
  if(!d||!d.factor||num(d.factor)===1||num(shapeQuantity)===null)return null;
  const reason=String(d.factor_reason||'the draft’s factor');
  const what=kind==='area'&&/slope/.test(reason)?' in plan':kind==='line'&&unit!=='LF'?' of wall face':'';
  const row=untouched&&d.quantity!=null?String(d.quantity):times(shapeQuantity,d.factor,untouched?'up':'even');
  return {row,text:`${qty(shapeQuantity,unit)}${what} × ${d.factor} (${reason}) = ${qty(row,unit)}`};
 }

 // The row a measurement sent on a drafted row is priced at (process_assets: a measurement that is
 // the draft or an edit of it keeps the draft's factor; a fresh trace is taken as it is). body: the
 // measurement as sent; r: what it measures (PM.measure). null when no factor applies.
 function measuredRow(a,body,r){
  const d=draftOf(a),fromModel=x=>typeof x?.drawn_by==='string'&&x.drawn_by.startsWith('model:');
  if(!d||!body||!r||!(fromModel(body)||fromModel(body.original)))return null;
  return factorLine(d,r.quantity,r.unit,{kind:body.shapes?.[0]?.kind||null,untouched:fromModel(body)});
 }

 /* ---------- 2 and 6. the rows that hold the report, and why ---------- */
 const exposure=d=>num(d?.exposure_usd);
 function held(items){
  return (items||[]).filter(a=>draftOf(a)?.blocks===true&&pending(draftOf(a)))
   .sort((x,y)=>(exposure(draftOf(y))??-1)-(exposure(draftOf(x))??-1)||String(x.label||x.category).localeCompare(String(y.label||y.category))||String(x.id).localeCompare(String(y.id)));
 }
 // Why this draft holds the report or does not (pricing rule (a), EVAL_REPORT 110.4).
 function exposureText(d){
  if(!pending(d))return null;
  const e=exposure(d),t=num(d.verify_above_usd);
  if(e===null)return 'Not priced, so it moves no dollars and does not hold the report.';
  const could=`Could move about ${money(e)} (its expected error times its price)`;
  if(t===null)return `${could}.`;
  return d.blocks?`${could}, more than ${money(t)}: it holds the report until you verify it.`:`${could}, not more than ${money(t)}: it does not hold the report.`;
 }

 /* ---------- 3. interior drafts: no shape, the model's numbers ---------- */
 const ANSWER=[['length_ft','Length','ft'],['width_ft','Width','ft'],['ceiling_height_ft','Ceiling','ft'],['floor_area_sf','Floor','SF'],['wall_area_sf','Walls','SF'],['perimeter_ft','Perimeter','ft'],['countertop_sf','Countertop','SF'],['cabinet_lf','Cabinet run','LF']];
 const photoCount=d=>Array.isArray(d?.photos)?d.photos.length:(n=>n===null?null:n)(num(/^(\d+) photos?/.exec(String(d?.source||''))?.[1]));
 function interiorLine(d){
  const n=photoCount(d),err=d?.expected_error_pct!=null?` · expected error about ${fmt(d.expected_error_pct,1)} %`:'';
  return `Drafted by Basis from ${n===null?'the':n} photo${n===1?'':'s'} of the room · ${pending(d)?'not verified':'reviewed'}${err}`;
 }
 // An exterior draft's line (the lidar shell), and either kind's.
 const exteriorLine=d=>`Drafted by Basis from the lidar · ${pending(d)?'not verified':'reviewed'}${d?.expected_error_pct!=null?` · expected error about ${fmt(d.expected_error_pct,1)} %`:''}`;
 const draftLine=d=>interior(d)?interiorLine(d):exteriorLine(d);
 function answerFacts(d){const a=d?.answer;if(!a||typeof a!=='object')return [];return ANSWER.filter(([k])=>num(a[k])!==null).map(([k,label,unit])=>[label,`${fmt(a[k],1)} ${unit}`])}
 // The typed-quantity correction "Use this number" queues (docs/api/MEASURE_DRAFTS.md section 3:
 // a typed quantity replaces the draft), merged into a pending one as the measure tool does in
 // reverse (live/property.js saveTrace: a measurement replaces a typed quantity).
 function useNumberCorrection(a,cur,{title}={}){
  const d=draftOf(a);if(!d||num(d.quantity)===null||!d.unit)return null;
  const after={...(cur?.after||{})};delete after.measurement;
  Object.assign(after,{quantity:num(d.quantity),quantity_unit:d.unit,quantity_source:INTERIOR_SOURCE});
  const before={...(cur?.before||{}),quantity:a.takeoff?.quantity??null,quantity_unit:a.takeoff?.unit??null};
  return {key:a.id,entity_type:'asset',entity_id:a.id,before,after,reason:cur?.reason??null,title:title||a.category,summary:`used Basis’s draft, ${qty(d.quantity,d.unit)}, as verified`};
 }
 const usingNumber=(a,cur)=>{const d=draftOf(a),x=cur?.after;return !!d&&!!x&&x.measurement===undefined&&num(x.quantity)===num(d.quantity)&&x.quantity_unit===d.unit&&x.quantity_source===INTERIOR_SOURCE};

 /* ---------- 4. what it replaced, and who acted ---------- */
 const MISSING={cabinet_run:'the cabinet run',gla:'the living area',stories:'the story count'};
 function wasLine(d){
  const r=d?.replaces;if(!r||typeof r!=='object')return null;
  if(r.missing_input)return `Was: no quantity, so the row was unpriced (${MISSING[r.missing_input]||String(r.missing_input).replace(/_/g,' ')} was missing).`;
  if(r.quantity!=null||r.formula){const [n,u]=splitQty(r.quantity);return `Was ${n===null?String(r.quantity):qty(n,u)}${r.formula?` (${r.formula})`:''}.`}
  return null;
 }
 function resolvedLine(d){
  if(!d||pending(d))return null;const drafted=`Basis drafted ${qty(d.quantity,d.unit)}`;
  if(d.verified===true)return `${drafted}; engineer verified.`;
  const [n,u]=splitQty(d.engineer_quantity);
  if(n!==null&&n===num(d.quantity)&&(u||d.unit)===d.unit)return `${drafted}; engineer verified.`;
  return `${drafted}; engineer changed it to ${n===null?'their own quantity':qty(n,u||d.unit)}.`;
 }

 /* ---------- 5. why a roof, siding or gutter row was not drafted ---------- */
 // The reasons basis/activities/draft_inputs.py and measure_drafts.exterior_drafts write, with the
 // fix the engineer can make. Unknown text falls through to measuring it by hand.
 function notUsedHint(reason){
  const r=String(reason||'');if(!r)return null;
  if(/no pin on the building/.test(r))return {why:'Basis did not draft it: the study has no pin on the building.',fix:'Put the pin on the house on the Property page and confirm it; Basis drafts the roof, siding and gutters from the lidar the next time the assets are processed.',to:'property'};
  if(/pin is not confirmed/.test(r))return {why:'Basis did not draft it: the pin is not confirmed.',fix:'Confirm the pin on the Property page; Basis drafts the roof, siding and gutters from the lidar the next time the assets are processed.',to:'property'};
  if(/earlier pin|no public records gather|could not be read/.test(r))return {why:`Basis did not draft it: ${r.replace(/\s*\(GEO_SHELL\)/,'')}.`,fix:'Gather the public records again on the Property page; Basis drafts it from the new lidar the next time the assets are processed.',to:'property'};
  return {why:`Basis could not draft it from the lidar: ${r}.`,fix:'Measure it yourself with Measure.',to:null};
 }

 /* ---------- what each piece renders (HTML strings; the live part adds the actions) ---------- */
 // The card on the asset page: every line the row has, and its action while the review is open.
 function cardHtml(a,{open=false,cur=null,measure='',propertyHref=null}={}){
  const d=draftOf(a),why=notUsedHint(inputsOf(a).draft_not_used);
  if(!d)return why?`<section class="mt-card md-card is-not-used" aria-labelledby="md-card-title"><h3 id="md-card-title">Not drafted</h3><p>${esc(why.why)}</p><p class="pa-muted">${esc(why.fix)}</p>${why.to&&propertyHref?`<a class="button small" href="${esc(propertyHref)}">Property page</a>`:''}</section>`:'';
  const inside=interior(d),was=wasLine(d),done=resolvedLine(d),exp=exposureText(d),queued=inside?usingNumber(a,cur):!!cur?.after?.measurement;
  const head=pending(d)?`Drafted by Basis: ${qty(d.quantity,d.unit)}`:`Measured: ${qty(splitQty(d.engineer_quantity)[0]??d.quantity,splitQty(d.engineer_quantity)[1]||d.unit)}`;
  const facts=inside?answerFacts(d):[];
  const line=draftLine(d);
  const factor=!inside&&d.factor&&num(d.factor)!==1?factorLine(d,splitQty(inputsOf(a).shape_quantity)[0],d.unit,{kind:/slope/.test(String(d.factor_reason))?'area':'line',untouched:true}):null;
  const action=!open||!pending(d)?'':queued?`<p class="md-queued">${inside?`${esc(qty(d.quantity,d.unit))} is in the corrections as verified, waiting for Review &amp; send.`:'Your measurement is in the corrections, waiting for Review &amp; send.'}</p>`:
   inside?`<div class="row wrap md-actions"><button class="button primary small" data-live="draft-use" data-value="${esc(a.id)}">Use this number</button>${measure}</div><p class="pa-muted">Use this number sends ${esc(qty(d.quantity,d.unit))} as your verified quantity. To measure it yourself, trace it on the room’s photo with Measure (a 36 in counter sets the scale).</p>`:
   `<p class="pa-muted">Open it with Measure, check the outline against the picture, tick Verified and save.</p>`;
  return `<section class="mt-card md-card ${pending(d)?'is-pending':'is-resolved'}${d.blocks&&pending(d)?' is-held':''}" aria-labelledby="md-card-title"><h3 id="md-card-title">${esc(head)}${d.blocks&&pending(d)?' <small class="nd-tag md-tag-held">Holds the report</small>':''}</h3>
<p class="md-line">${esc(line)}</p>${done?`<p class="md-done">${esc(done)}</p>`:''}${factor?`<p class="md-factor">${esc(factor.text)}</p>`:''}${facts.length?`<dl class="pa-dl md-answer">${facts.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>`:''}${inside&&d.notes?`<p class="md-notes"><span>Basis’s notes</span> ${esc(d.notes)}</p>`:''}${was?`<p class="md-was">${esc(was)}</p>`:''}${exp?`<p class="md-exposure">${esc(exp)}</p>`:''}${action}</section>`;
 }
 // The held rows, highest exposure first, each with its Verify action.
 function heldHtml(items,{open=false,route=id=>'#',pendingOf=()=>null,name=a=>a.label||a.category,blocker=null,heading='h2'}={}){
  const rows=held(items);if(!rows.length&&!blocker)return '';
  const H=heading,count=blocker?.assets??rows.length;
  const li=a=>{const d=draftOf(a),inside=interior(d),cur=pendingOf(a.id),queued=inside?usingNumber(a,cur):cur?.after?.measurement?.verified===true||(!!cur?.after?.measurement&&cur.after.measurement.drawn_by==='engineer');
   const act=!open?`<a class="button small" href="${esc(route(a.id))}">Open</a>`:queued?'<span class="nd-status nd-status-ok">Verified · Review &amp; send</span>':inside?`<button class="button small primary" data-live="draft-use" data-value="${esc(a.id)}">Use this number</button>`:`<button class="button small primary" data-live="draft-verify" data-value="${esc(a.id)}">Verify</button>`;
   return `<li class="md-held-row"><div><a href="${esc(route(a.id))}"><strong>${esc(name(a))}</strong></a>${a.room?.name?` <span class="pa-muted">${esc(a.room.name)}</span>`:''}<span class="md-held-q">${esc(qty(d.quantity,d.unit))} · ${esc(draftLine(d))}</span></div><span class="md-held-x"><strong>${esc(money(d.exposure_usd)||'—')}</strong><small>could move</small></span>${act}</li>`};
  const msg=(blocker?.message?blocker.message.replace(/^./,c=>c.toUpperCase()):`${count} ${count===1?'measurement':'measurements'} drafted by Basis ${count===1?'is':'are'} not verified`).replace(/\.\s*$/,'');
  return `<section class="nd-panel md-held" aria-labelledby="md-held-title"><header><${H} id="md-held-title">The report waits for ${count===1?'this measurement':`these ${count} measurements`}</${H}></header><p>${esc(msg)}.</p>${rows.length?`<ol class="md-held-list">${rows.map(li).join('')}</ol>`:''}<p class="pa-muted">${open?'Verify each in Measure (the roof, siding and gutters) or use the number Basis drafted from the room’s photos, then Review &amp; send.':'Reopen the assets review to verify them.'}</p></section>`;
 }

 const api={BLOCKER,INTERIOR_SOURCE,draftOf,pending,interior,held,factorLine,measuredRow,times,exposureText,interiorLine,exteriorLine,draftLine,answerFacts,useNumberCorrection,usingNumber,wasLine,resolvedLine,notUsedHint,cardHtml,heldHtml};
 if(typeof module==='object'&&module.exports){module.exports=api;return}
 scope.BasisMeasureDrafts=api;

 /* ---------- live mode: the hooks the asset page, the Assets list and Results call ---------- */
 const C=scope.BASIS_CONFIG;if(!C||C.mode!=='live'||!scope.BasisLive)return;
 const Live=scope.BasisLive,L=Live.state;
 const name=a=>a.label?Live.labelName(a.label):a.category;
 const assetsOf=s=>Live.load(`assets:${s.id}:${s.revision}`,()=>Live.api.assets(s.id,s.revision)).data?.items||[];
 Live.draftCard=(s,a)=>{const open=Live.gateOpen('assets_review');
  return cardHtml(a,{open,cur:Live.pendingCorrection?.('assets_review',a.id),propertyHref:Live.caps?.propertyCapture||Live.caps?.publicGeo?Live.route('property',s.id):null})};// the page's own Measure button sits above the card
 Live.heldDrafts=(s,items,{blocker=null}={})=>heldHtml(items,{open:Live.gateOpen('assets_review'),route:id=>Live.route('asset-detail',s.id,id),pendingOf:id=>Live.pendingCorrection?.('assets_review',id),name,blocker});
 Live.actions['draft-use']=(el,id)=>{const s=L.study,a=assetsOf(s).find(x=>x.id===id);if(!a||!Live.gateOpen('assets_review'))return;
  const c=useNumberCorrection(a,Live.pendingCorrection?.('assets_review',id),{title:name(a)});if(c)Live.addCorrection('assets_review',c)};
 // Verifying an exterior draft is the measure tool's verified save: open it on the row (on the
 // asset's page when this is another page, since the dialog belongs to the Assets and asset pages).
 Live.actions['draft-verify']=(el,id)=>{const s=L.study,a=assetsOf(s).find(x=>x.id===id);if(!a||!Live.openMeasure)return;
  const screen=Live.screen();if(screen!=='assets'&&screen!=='asset-detail'&&typeof location==='object'){location.hash=Live.route('asset-detail',s.id,id).slice(1);L.docId=id}
  Live.openMeasure(a,screen==='assets'?'assets':'asset-detail')};
})(typeof globalThis==='object'?globalThis:this);
