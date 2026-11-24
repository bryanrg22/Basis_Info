/* Live mode: the study's Property area (#property/<study>[/<asset to measure>]) and its card on
 * Study progress.
 *
 * What it reads and writes (open backend branches; web/FRONTEND_BACKLOG.md section 21):
 *   GET  .../property, PUT .../property/pin (If-Match), POST/GET .../property/captures,
 *   GET  .../property/views/{id}/image                 docs/api/PROPERTY_CAPTURE.md
 *   GET/POST .../property/public, .../public/views/{id}/image   docs/api/PUBLIC_GEO.md
 *   GET/PUT  .../renovations (If-Match)                 docs/api/RENOVATIONS.md (the aerial brackets)
 *   an `asset` correction's `measurement`, sent with the assets review's corrections
 *                                                      docs/api/OVERRIDES.md section 4
 * Each part appears only when GET /v1/capabilities says so (property_capture, public_geo,
 * traced_measurement); the renovations part only when its GET answers. Today's API shows nothing
 * new. The rules without the DOM are live/property-model.js.
 *
 * The screen: the pin and "Is this the right house?" on the aerial (move it by clicking where the
 * house is), the capture's status in plain words, today's views, the four sides, street level,
 * the dated views newest first with a before/after compare, the public imagery with its credit
 * and year, the parcel drawn on the image with the lot area and the appraisal check, and the
 * building's heights. From an asset that needs a measured quantity, a tracing tool on the chosen
 * top-down view (desktop and tablet; phones are told to use a larger screen).
 */
(function(){
 'use strict';
 const C=globalThis.BASIS_CONFIG;if(!C||C.mode!=='live')return;
 const Live=globalThis.BasisLive,PM=globalThis.BasisPropertyModel;if(!Live||!PM)return;
 const {api,state:L}=Live;
 const enc=encodeURIComponent,base=id=>`/v1/studies/${enc(id)}`;
 const caps=()=>Live.caps||{};
 const shows=()=>PM.anyProperty(caps());
 const POLL_MS=10000;// FRONTEND_BACKLOG 18: every 10 to 30 s is plenty
 const narrow=()=>typeof matchMedia==='function'&&matchMedia('(max-width: 767px)').matches;
 const name=Live.labelName;
 const fmt=(v,d=0)=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});

 /* ---------- data ---------- */
 const propEntry=s=>Live.load('property:'+s.id,()=>api.request('GET',`${base(s.id)}/property`));
 const pubEntry=s=>caps().publicGeo?Live.load('property-public:'+s.id,()=>api.request('GET',`${base(s.id)}/property/public`)):null;
 // While a new capture runs the newest capture has no views: the last one that succeeded is shown.
 const capturesEntry=(s,newestId)=>Live.load(`property-captures:${s.id}:${newestId}`,()=>api.request('GET',`${base(s.id)}/property/captures`,{query:{limit:20}}));
 const renoEntry=s=>Live.load('renovations:'+s.id,()=>api.request('GET',`${base(s.id)}/renovations`,{expect:[404]}).catch(e=>{if(e.status===404)return {unavailable:true};throw e}));
 const assetsEntry=s=>Live.load(`assets:${s.id}:${s.revision}`,()=>api.assets(s.id,s.revision));
 function shownCapture(s,prop){
  const c=prop?.capture;if(!c||c.status==='succeeded'||(c.views||[]).length)return c;
  const list=capturesEntry(s,c.id);return list.data?.items?.find(x=>x.status==='succeeded')||null;
 }
 const ui=s=>{L.property||={};return L.property[s.id]||={view:null,zoom:1,layers:{parcel:true,pin:true},mode:null,candidate:null,compare:{a:null,b:null,mode:null,pos:50},trace:null,retryAt:0,saved:null}};

 /* ---------- images (bearer token: fetched, shown from object URLs) ---------- */
 const images=new Map();
 function imageUrl(kind,s,view){
  const key=`${kind}:${view.id}:${view.sha256||''}`;if(images.has(key))return images.get(key);
  const path=kind==='public'?`${base(s.id)}/property/public/views/${enc(view.id)}/image`:`${base(s.id)}/property/views/${enc(view.id)}/image`;
  const p=api.request('GET',path,{raw:true}).then(r=>r.blob()).then(b=>{const url=URL.createObjectURL(b);p.url=url;return url});
  images.set(key,p);p.catch(()=>images.delete(key));return p;
 }
 const kindOf=v=>v?.source_id?'public':'capture';
 const img=(s,view,alt,cls='')=>{const p=images.get(`${kindOf(view)}:${view.id}:${view.sha256||''}`);return `<img class="${cls}" data-pa-img="${esc(view.id)}" data-pa-kind="${kindOf(view)}" ${p?.url?`src="${p.url}"`:''} alt="${esc(alt)}" width="${view.width}" height="${view.height}" decoding="async">`};
 function hydrate(){
  const s=L.study;if(!s||!document.querySelector('[data-pa-img]'))return;
  const all=allViews(s);
  for(const el of document.querySelectorAll('img[data-pa-img]:not([src])')){
   const v=all.find(x=>x.id===el.dataset.paImg);if(!v)continue;
   imageUrl(el.dataset.paKind,s,v).then(url=>{if(el.isConnected&&!el.src)el.src=url},()=>{el.replaceWith(Object.assign(document.createElement('div'),{className:'pa-missing',textContent:'Picture unavailable'}))});
  }
 }
 function allViews(s){
  const p=L.cache.get('property:'+s.id)?.data,cap=p?shownCapture(s,p):null,pub=L.cache.get('property-public:'+s.id)?.data;
  return [...(cap?.views||[]),...(pub?PM.publicSeries(pub).map(x=>x.view):[])];
 }

 /* ---------- polling while something runs ---------- */
 let pollTimer=null;
 function schedulePoll(s,running){
  clearTimeout(pollTimer);pollTimer=null;
  if(!running)return;
  pollTimer=setTimeout(()=>{pollTimer=null;if(L.studyId!==s.id||!['property','processing'].includes(Live.screen()))return;if(document.hidden){schedulePoll(s,true);return}Live.invalidate('property:'+s.id);Live.invalidate('property-public:'+s.id);Live.invalidate('property-captures:'+s.id);Live.render()},POLL_MS);
 }

 /* ---------- the pieces ---------- */
 const pill=(text,tone)=>`<span class="pa-pill is-${tone}">${esc(text)}</span>`;
 const viewDate=v=>v.source_id?(v.year_label||v.year||'Year unknown'):PM.dateText(PM.dateOf(v));
 // The line under every image: its date, season, pixel size and where it came from.
 function caption(v){
  if(!v)return '';
  if(v.source_id)return `<p class="pa-caption"><strong>${esc(String(viewDate(v)))}</strong>${v.leaf_off===true?' · Trees bare':v.leaf_off===false?' · Trees in leaf':''}${v.pixel_size_m?` · ${fmt(v.pixel_size_m*100,1)} cm a pixel`:''}<span class="pa-credit">${esc(v.credit||'')}</span></p>`;
  const date=PM.dateOf(v),undated=!date;
  return `<p class="pa-caption"><strong>${esc(PM.dateText(date))}</strong>${undated?' · the date could not be read':''}${v.leaf?` · ${esc(PM.leafText(v.leaf))}`:''}${v.pixel_size_m?` · ${fmt(v.pixel_size_m*100,1)} cm a pixel`:''}${v.stale&&!sameSpot(v)?' · <span class="pa-warn">taken around an earlier pin</span>':''}<span class="pa-credit">${esc(v.source||'')}</span></p>`;
 }
 // A view is "stale" when its pin version is older; confirming the pin bumps the version without
 // moving it, so a view whose centre is still the pin is shown as current (web/DESIGN_NOTES.md).
 let pinNow=null;
 const sameSpot=v=>!!pinNow&&pinNow.lat!=null&&Math.abs(v.lat-pinNow.lat)<1e-7&&Math.abs(v.lon-pinNow.lon)<1e-7;
 function statusBlock(st,gs){
  const rows=[];
  if(caps().propertyCapture&&st&&(st.state!=='ready'||st.stale))rows.push(`<div class="pa-status is-${st.tone}" role="status"><span class="pa-dot" aria-hidden="true"></span><div><strong>Property pictures · ${esc(st.title)}</strong><p>${esc(st.text)}</p>${st.code&&['failed','paused'].includes(st.state)?`<p class="pa-code">For support: <code>${esc(st.code)}</code>${globalThis.BasisErrorHelp?.explain(st.code).known?` · ${esc(BasisErrorHelp.explain(st.code).what)}`:''}</p>`:''}</div></div>`);
  if(caps().publicGeo&&gs&&gs.state!=='ready')rows.push(`<div class="pa-status is-${gs.tone}" role="status"><span class="pa-dot" aria-hidden="true"></span><div><strong>Public records · ${esc(gs.title)}</strong><p>${esc(gs.text)}</p></div></div>`);
  return rows.length?`<div class="pa-statuses">${rows.join('')}</div>`:'';
 }
 function actionButtons(s,prop,st,gs,u){
  const out=[],engineer=canEdit(s),wait=Math.max(0,Math.ceil((u.retryAt-Date.now())/1000));
  if(!engineer)return '';
  if(caps().propertyCapture&&prop.pin&&['retake','retake-optional','start'].includes(st?.action)){
   const label=st.action==='start'?'Take the pictures':'Take them again';
   out.push(`<button class="button ${st.action==='retake'||st.action==='start'?'primary':''}" data-live="pa-capture" data-value="${st.action==='retake'?'force':''}" ${wait?'disabled':''}>${label}${wait?` (in ${wait} s)`:''}</button>`);
  }
  if(caps().publicGeo&&prop.pin?.lat!=null&&gs&&['ready','failed','none'].includes(gs.state)&&(gs.stale||gs.state!=='ready'))out.push(`<button class="button" data-live="pa-gather" ${wait?'disabled':''}>${gs.state==='none'?'Gather public records':'Gather them again'}</button>`);
  return out.join('');
 }
 const canEdit=s=>{const role=((Live.me?.()?.memberships)||[]).find(m=>m.org_id===s.org_id)?.role;return role?role!=='viewer':true};

 // The pin card: "Is this the right house?" until the engineer confirms it.
 function pinCard(s,prop,u,stage){
  const p=prop.pin,engineer=canEdit(s);
  if(!p)return `<section class="pa-card"><h2>Where is the property?</h2><p>Give the address and Basis takes dated pictures of it in the background.</p>${engineer?addressForm(s,null):''}</section>`;
  if(p.lat==null)return `<section class="pa-card is-ask"><h2>Where is the property?</h2><p>No point yet: the address is searched when the pictures are taken.</p>${engineer?addressForm(s,p):`<p class="pa-address">${esc(p.address||'')}</p>`}</section>`;
  const where=p.source==='search'?`Placed by the address search${p.place_label?`: “${esc(p.place_label)}”`:''}.`:'Placed by an engineer.';
  if(u.mode==='pin')return `<section class="pa-card is-active"><h2>Move the pin</h2><p>Click the house on the picture. ${u.candidate?'Then save the new point.':''}</p>${u.candidate?`<p class="pa-mono">${fmt(u.candidate[1],6)}, ${fmt(u.candidate[0],6)}</p><div class="row wrap"><button class="button primary" data-live="pa-pin-save">Save the pin here</button><button class="button" data-live="pa-pin-cancel">Cancel</button></div><p class="pa-muted">Saving confirms this is the house. The pictures were taken around the old point; take them again afterwards.</p>`:`<button class="button" data-live="pa-pin-cancel">Cancel</button>`}</section>`;
  const canMove=!!stage&&!!PM.georef(stage);
  if(!p.confirmed)return `<section class="pa-card is-ask"><h2>Is this the right house?</h2><p>${where}</p><p class="pa-address">${esc(p.address||'')}</p>${engineer&&p.lat!=null?`<div class="row wrap"><button class="button primary" data-live="pa-pin-confirm">Yes, this is the house</button>${canMove?'<button class="button" data-live="pa-pin-move">No, move the pin</button>':''}</div><p class="pa-muted">Nothing is measured on these pictures until the pin is confirmed.</p>`:''}${engineer?`<details class="pa-more"><summary>Change the address</summary>${addressForm(s,p)}</details>`:''}</section>`;
  return `<section class="pa-card"><h2>The house</h2><p class="pa-address">${esc(p.address||'')}</p><p class="pa-muted">${icon('check',14)} Confirmed${p.confirmed_at?' '+esc(Live.when(p.confirmed_at)):''} · ${p.source==='search'?'found by the address search':'placed by an engineer'}</p>${engineer&&canMove?'<button class="button small" data-live="pa-pin-move">Move the pin</button>':''}</section>`;
 }
 function addressForm(s,p){
  const m=s.summary||{},guess=p?.address||[m.address,m.city,[m.state,m.zip].filter(Boolean).join(' ')].filter(Boolean).join(', ');
  return `<form class="pa-address-form" data-pa-form="address"><label for="pa-address">Address</label><input id="pa-address" name="address" maxlength="300" value="${esc(guess||'')}" autocomplete="street-address" required><button class="button ${p?'':'primary'}" type="submit">${p?'Save the address':'Save and take pictures'}</button></form>`;
 }

 // The stage: one view, with the parcel, the pin and the tracing drawn over it in its pixels.
 function stage(s,v,prop,pub,u){
  if(!v)return `<div class="pa-stage is-empty"><p>No picture yet.</p></div>`;
  const g=PM.georef(v),w=v.width,h=v.height,parts=[];
  if(g&&u.layers.parcel&&pub?.parcels?.length)for(const parcel of pub.parcels)for(const ring of PM.parcelRings(parcel,g))parts.push(`<polygon class="pa-parcel ${parcel.under_pin?'':'is-adjacent'}" points="${ring.map(p=>p.map(n=>n.toFixed(1)).join(',')).join(' ')}"/>`);
  if(g&&u.layers.pin&&prop.pin?.lat!=null){const [x,y]=g.toPixel(prop.pin.lon,prop.pin.lat);parts.push(marker(x,y,w,'pa-pin'))}
  if(g&&u.candidate){const [x,y]=g.toPixel(...u.candidate);parts.push(marker(x,y,w,'pa-pin is-candidate'))}
  if(u.trace&&u.trace.viewId===v.id&&(u.trace.shapes.length||u.trace.draft.length))parts.push(traceSvg(u.trace,w));
  else if(u.saved?.viewId===v.id&&u.saved.assetId===L.docId)parts.push(traceSvg({shapes:u.saved.shapes,draft:[],tool:'area'},w));
  const zoom=u.zoom||1,mode=u.mode==='pin'?'is-pin':u.mode==='measure'?'is-measure':'';
  return `<div class="pa-stage ${mode}" data-zoom="${zoom}"><div class="pa-zoom" tabindex="0" aria-label="Picture; scroll to pan when zoomed"><div class="pa-canvas" style="width:${zoom*100}%;aspect-ratio:${w}/${h}">${img(s,v,v.label||'Property picture','pa-photo')}<svg class="pa-overlay" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" data-pa-stage="${esc(v.id)}" role="img" aria-label="${esc(overlayLabel(prop,pub,u))}">${parts.join('')}</svg></div></div><div class="pa-stage-tools"><div class="pa-zoom-tools" role="group" aria-label="Zoom"><button class="pa-icon-btn" data-live="pa-zoom" data-value="out" aria-label="Zoom out" ${zoom<=1?'disabled':''}>−</button><span>${zoom*100}%</span><button class="pa-icon-btn" data-live="pa-zoom" data-value="in" aria-label="Zoom in" ${zoom>=4?'disabled':''}>+</button></div>${g?`<label class="pa-toggle"><input type="checkbox" data-pa-layer="pin" ${u.layers.pin?'checked':''}> Pin</label>${pub?.parcels?.length?`<label class="pa-toggle"><input type="checkbox" data-pa-layer="parcel" ${u.layers.parcel?'checked':''}> Parcel</label>`:''}`:''}</div>${caption(v)}</div>`;
 }
 const overlayLabel=(prop,pub,u)=>[u.mode==='pin'?'Click the house to move the pin.':u.mode==='measure'?'Click corners to trace.':'',prop.pin?'The pin marks the house.':'',pub?.parcels?.length?'The parcel outline is drawn.':''].filter(Boolean).join(' ');
 const marker=(x,y,w,cls)=>{const r=Math.max(7,w/110);return `<g class="${cls}" transform="translate(${x.toFixed(1)} ${y.toFixed(1)})"><g class="pa-pin-mark"><circle r="${r*1.9}" class="pa-pin-halo"/><circle r="${r}" class="pa-pin-dot"/></g></g>`};
 function traceSvg(t,w){
  const r=Math.max(4,w/280),pts=p=>p.map(q=>q.map(n=>n.toFixed(1)).join(',')).join(' '),out=[];
  for(const sh of t.shapes){if(sh.kind==='area'){out.push(`<polygon class="pa-trace" points="${pts(sh.ring)}"/>`);for(const c of sh.cutouts||[])out.push(`<polygon class="pa-trace is-cut" points="${pts(c)}"/>`)}else out.push(`<polyline class="pa-trace is-line" points="${pts(sh.path)}"/>`)}
  if(t.draft.length){out.push(`<polyline class="pa-trace is-draft ${t.tool==='cutout'?'is-cut':''}" points="${pts(t.draft)}"/>`);t.draft.forEach((p,i)=>out.push(`<circle class="pa-vertex ${i===0&&t.tool!=='line'&&t.draft.length>=3?'is-close':''}" cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="${i===0?r*1.6:r}"/>`))}
  return `<g class="pa-traces">${out.join('')}</g>`;
 }

 // The view picker: today's views, the sides, street level, the public image.
 function picker(cap,pub,u,current){
  const V=PM.viewsOf(cap),chip=(id,label,on,extra='')=>`<button class="pa-chip" data-live="pa-view" data-value="${esc(id)}" aria-pressed="${on}" ${extra}>${label}</button>`;
  const chips=[];
  if(V.close)chips.push(chip(V.close.id,'Close aerial',current?.id===V.close.id));
  if(V.wide)chips.push(chip(V.wide.id,'Wide aerial',current?.id===V.wide.id));
  if(V.sides.length)chips.push(chip('sides','Four sides',u.view==='sides'));
  if(caps().propertyCapture)chips.push(chip('street','Street level',u.view==='street'));
  if(pub?.current)chips.push(chip(pub.current.id,'Public image',current?.id===pub.current.id));
  return `<nav class="pa-chips" aria-label="Views">${chips.join('')}</nav>`;
 }
 function sidesGrid(s,cap){
  const V=PM.viewsOf(cap);
  return `<div class="pa-sides">${V.sides.map(v=>`<figure>${img(s,v,PM.SIDE_TEXT[v.label]||v.label,'pa-photo')}<figcaption><strong>${esc(PM.SIDE_TEXT[v.label]||name(v.label))}</strong> <span class="pa-muted">${esc(PM.LOOKING[v.label]||'')} · ${esc(PM.dateText(PM.dateOf(v)))} · not for measuring</span><span class="pa-credit">${esc(v.source||'')}</span></figcaption></figure>`).join('')}</div>`;
 }
 function streetCard(s,cap){
  const v=PM.viewsOf(cap).street;
  if(v)return `<figure class="pa-street">${img(s,v,'Street-level picture of the front','pa-photo')}<figcaption><strong>The front</strong> <span class="pa-muted">looking ${esc(PM.compass(v.heading_deg)||'at the house')} from the street · ${esc(PM.dateText(PM.dateOf(v)))} · for reading the front, never for measuring</span><span class="pa-credit">${esc(v.source||'')}</span></figcaption></figure>`;
  const note=(cap?.notes||[]).find(n=>n.part==='street');
  return `<div class="pa-empty-view"><h3>Street level: not available</h3><p>${esc(note?PM.noteText(note):'Street-level pictures are not taken on this server.')}</p></div>`;
 }

 // The dated views: the property's history and the public series, each newest first.
 function strips(s,cap,pub,u,current){
  const parts=[],pis=s.intake?.placed_in_service_date||null;
  if(cap){
   const items=PM.historyStrip(PM.viewsOf(cap).history,{placedInService:pis,measureOn:cap.measure_on});
   if(items.length)parts.push(`<div class="pa-strip-block"><h3>Dated pictures <span class="pa-muted">newest first</span></h3><ol class="pa-strip">${items.map(it=>`<li><button class="pa-thumb ${current?.id===it.id?'is-on':''} ${it.suggested?'is-suggested':''}" data-live="pa-view" data-value="${esc(it.id)}" aria-pressed="${current?.id===it.id}">${img(s,it.view,'Picture of '+PM.dateText(it.date),'pa-thumb-img')}<span class="pa-thumb-date">${esc(PM.dateText(it.date))}</span><span class="pa-thumb-marks">${it.leaf==='leaf-off'?'<b class="pa-mark is-leaf">Trees bare</b>':it.leaf==='leaf-on'?'<b class="pa-mark">Trees in leaf</b>':'<b class="pa-mark">Season unknown</b>'}${it.suggested?'<b class="pa-mark is-measure">Suggested for measuring</b>':''}${it.atService?'<b class="pa-mark is-service">At placed in service</b>':''}</span></button></li>`).join('')}</ol></div>`);
   const hist=(cap.notes||[]).find(n=>n.part==='history');if(hist)parts.push(`<p class="pa-note">${esc(PM.noteText(hist))}</p>`);
  }
  if(pub){
   const series=PM.publicSeries(pub);
   if(series.length)parts.push(`<div class="pa-strip-block"><h3>Public imagery by year</h3><ol class="pa-strip">${series.map(it=>`<li><button class="pa-thumb ${current?.id===it.id?'is-on':''}" data-live="pa-view" data-value="${esc(it.id)}" aria-pressed="${current?.id===it.id}">${img(s,it.view,'Public image of '+it.yearLabel,'pa-thumb-img')}<span class="pa-thumb-date">${esc(it.yearLabel)}</span><span class="pa-thumb-marks">${it.leafOff===true?'<b class="pa-mark is-leaf">Trees bare</b>':''}${it.coarse?'<b class="pa-mark">For seeing, not measuring</b>':''}</span><span class="pa-thumb-credit">${esc(it.view.credit)}</span></button></li>`).join('')}</ol></div>`);
  }
  return parts.join('');
 }

 // Before and after: two chosen views side by side, or one over the other with a slider.
 function compare(s,cap,pub,u){
  const dated=[...(cap?PM.viewsOf(cap).history:[]).filter(v=>PM.dateOf(v)),...(pub?PM.publicSeries(pub).map(x=>x.view):[])];
  if(dated.length<2)return '';
  const key=v=>v.source_id?`${v.year||0}-00-00`:PM.dateOf(v);
  const sorted=dated.slice().sort((a,b)=>String(key(a)).localeCompare(String(key(b))));
  const own=sorted.filter(v=>!v.source_id),c=u.compare,A=sorted.find(v=>v.id===c.a)||own[0]||sorted[0],B=sorted.find(v=>v.id===c.b)||own[own.length-1]||sorted[sorted.length-1];
  c.shownA=A.id;c.shownB=B.id;
  const label=v=>`${v.source_id?'Public · ':''}${viewDate(v)}`;
  const opts=sel=>sorted.map(v=>`<option value="${esc(v.id)}" ${v.id===sel.id?'selected':''}>${esc(label(v))}</option>`).join('');
  const aligned=A.width===B.width&&A.height===B.height&&Math.abs((A.pixel_size_m||0)-(B.pixel_size_m||0))<1e-9&&Math.abs(A.lat-B.lat)<1e-9&&Math.abs(A.lon-B.lon)<1e-9;
  const mode=aligned&&(c.mode||(narrow()?'slider':'side'))==='slider'?'slider':'side';
  const body=mode==='slider'?`<div class="pa-slider" style="--pos:${c.pos}%;aspect-ratio:${A.width}/${A.height}">${img(s,B,'After: '+label(B),'pa-photo')}<div class="pa-slider-top">${img(s,A,'Before: '+label(A),'pa-photo')}</div><span class="pa-slider-line" aria-hidden="true"></span><span class="pa-slider-tag is-a">${esc(label(A))}</span><span class="pa-slider-tag is-b">${esc(label(B))}</span></div><label class="pa-range"><span class="sr-only">Before and after</span><input type="range" min="0" max="100" value="${c.pos}" data-pa-slider aria-label="Slide between ${esc(label(A))} and ${esc(label(B))}"></label>`
   :`<div class="pa-pair"><figure>${img(s,A,'Before: '+label(A),'pa-photo')}<figcaption>${caption(A)}</figcaption></figure><figure>${img(s,B,'After: '+label(B),'pa-photo')}<figcaption>${caption(B)}</figcaption></figure></div>`;
  return `<section class="pa-compare" aria-labelledby="pa-compare-title"><header><h2 id="pa-compare-title">Before and after</h2><div class="pa-compare-pick"><label>Before <select data-pa-compare="a">${opts(A)}</select></label><label>After <select data-pa-compare="b">${opts(B)}</select></label>${aligned?`<div class="pa-seg" role="group" aria-label="Compare as"><button data-live="pa-compare-mode" data-value="side" aria-pressed="${mode==='side'}">Side by side</button><button data-live="pa-compare-mode" data-value="slider" aria-pressed="${mode==='slider'}">Slider</button></div>`:''}</div></header>${body}${aligned?'':'<p class="pa-muted">These two pictures have different scales, so they are shown side by side.</p>'}${evidence(s,A,B,u)}</section>`;
 }

 // Renovation evidence (docs/api/RENOVATIONS.md): the compare's two dates as the last view
 // without the work and the first with it, for a project or an exterior component.
 function evidence(s,A,B,u){
  const e=renoEntry(s);if(!e.done||e.error||e.data?.unavailable)return '';
  const doc=e.data.renovations,dA=A.source_id?null:PM.dateOf(A),dB=B.source_id?null:PM.dateOf(B);
  const existing=[...(doc?.projects||[]).filter(p=>p.aerial).map(p=>[`Project ${p.key}`,p.aerial]),...(doc?.exterior_brackets||[]).map(x=>[name(x.label),x.bracket])];
  const list=existing.length?`<ul class="pa-brackets">${existing.map(([what,b])=>`<li><strong>${esc(what)}</strong> not there on ${esc(PM.dateText(b.last_without.date))}; there on ${esc(PM.dateText(b.first_with.date))}</li>`).join('')}</ul>`:'';
  if(!canEdit(s))return list?`<div class="pa-evidence"><h3>Renovation evidence</h3>${list}</div>`:'';
  const labels=[...new Set((assetsEntry(s).data?.items||[]).filter(a=>!a.room||a.room.room_type==='exterior').map(a=>a.label).filter(Boolean))];
  const targets=[...(doc?.projects||[]).map(p=>[`project:${p.key}`,`Project: ${p.scope?p.scope.slice(0,60):p.key}`]),...labels.map(l=>[`label:${l}`,name(l)])];
  const why=!dA||!dB?'Public imagery has a year only; renovation evidence needs two dated pictures.':dA===dB?'Choose two different dates.':'';
  const [early,late]=dA&&dB&&dA<dB?[A,B]:[B,A];
  return `<div class="pa-evidence"><h3>Renovation evidence</h3><p class="pa-muted">Corroboration only: the dates say when the work became visible, never its cost or its date in service.</p>${list}${why?`<p class="pa-muted">${esc(why)}</p>`:`<form data-pa-form="bracket" class="pa-bracket-form"><label>What appears <select name="target" required>${targets.length?targets.map(([v,l])=>`<option value="${esc(v)}">${esc(l)}</option>`).join(''):'<option value="">No project or exterior component</option>'}</select></label><p>Last picture without it: <strong>${esc(PM.dateText(PM.dateOf(early)))}</strong> · first with it: <strong>${esc(PM.dateText(PM.dateOf(late)))}</strong></p><button class="button" type="submit" ${targets.length?'':'disabled'}>Record these dates</button></form>`}</div>`;
 }

 // The lot and the building: the parcel, the appraisal check, the heights.
 function facts(s,pub){
  if(!pub)return '';
  const under=pub.parcels?.find(p=>p.under_pin),adj=(pub.parcels||[]).filter(p=>!p.under_pin),sc=PM.siteCheckText(pub.site_check),l=PM.lidarFacts(pub.lidar);
  const rows=[];
  if(under)rows.push(`<div><dt>Lot area</dt><dd>${fmt(under.area_sqft)} SF <span class="pa-muted">(${fmt(under.area_acres,2)} ac${under.layer_acres!=null?`; the county layer says ${fmt(under.layer_acres,2)} ac`:''})</span></dd></div>`);
  if(under?.parcel_id)rows.push(`<div><dt>Parcel</dt><dd>${esc(under.parcel_id)}</dd></div>`);
  if(adj.length)rows.push(`<div><dt>Same owner next door</dt><dd>${adj.map(p=>`${esc(p.parcel_id||'Parcel')} · ${fmt(p.area_sqft)} SF`).join('<br>')} <span class="pa-muted">(dashed)</span></dd></div>`);
  const check=sc?`<div class="pa-check is-${sc.tone}" ${sc.flag?'role="alert"':''}><strong>${esc(sc.title)}</strong><p>${esc(sc.text)}</p>${sc.flag?`<a class="button small" href="${Live.route('appraisal',s.id)}">Open the appraisal</a>`:''}</div>`:pub.site_check?.status==='no_appraisal'?`<p class="pa-muted">${esc(pub.site_check.message||'The appraisal prints no site area to compare.')}</p>`:'';
  const heights=l?.building?`<dl class="pa-dl"><div><dt>Roof peak</dt><dd>${fmt(l.building.peakFt,1)} ft above the ground</dd></div><div><dt>Eaves</dt><dd>${fmt(l.building.eaveFt,1)} ft</dd></div><div><dt>Roof outline</dt><dd>${fmt(l.building.footprintSf)} SF <span class="pa-muted">including the overhangs</span></dd></div><div><dt>Surveyed</dt><dd>${esc(String(l.year||'year unknown'))} · ${esc(l.project||'')}</dd></div></dl>`:l?`<p class="pa-muted">${esc(l.text)}</p>`:'';
  if(!rows.length&&!check&&!heights)return '';
  return `<section class="pa-card"><h2>Lot and building</h2>${rows.length?`<dl class="pa-dl">${rows.join('')}</dl>`:''}${check}${heights?`<h3 class="pa-sub">Heights from the elevation survey</h3>${heights}`:''}${under?`<p class="pa-credit">${esc(under.credit)}${pub.lidar?' · '+esc(pub.lidar.credit):''}</p>`:''}</section>`;
 }

 /* ---------- measuring ---------- */
 function measurePanel(s,prop,v,u){
  if(!caps().tracedMeasurement)return '';
  const a=(assetsEntry(s).data?.items||[]).find(x=>x.id===L.docId);
  const open=Live.gateOpen('assets_review'),offer=(assetsEntry(s).data?.items||[]).filter(x=>PM.needsMeasurement(x).offer);
  if(!a)return offer.length&&open&&v&&PM.measurable(v)?`<section class="pa-card pa-measure-pick"><h2>Measure an asset</h2><p class="pa-muted">Trace an outside item on a top-down picture; the quantity goes to the assets review.</p><ul class="pa-asset-list">${offer.map(x=>`<li><a href="${Live.route('property',s.id,x.id)}">${esc(x.label?name(x.label):x.category)}</a> <span class="pa-muted">${PM.needsMeasurement(x).needed?'needs a measured quantity':esc(`${fmt(x.takeoff?.quantity,0)} ${x.takeoff?.unit||''}`)}</span></li>`).join('')}</ul></section>`:'';
  const t=u.trace,need=PM.needsMeasurement(a),title=a.label?name(a.label):a.category;
  const head=`<header class="pa-measure-head"><h2>Measure ${esc(title)}</h2><a class="pa-x" href="${Live.route('property',s.id)}" aria-label="Stop measuring">${icon('x',16)}</a></header>`;
  if(narrow())return `<section class="pa-card pa-measure">${head}<p>Measuring needs a larger screen: open this study on a computer or a tablet to trace on the aerial.</p></section>`;
  if(!open)return `<section class="pa-card pa-measure">${head}<p>The assets review is not open, so a measurement cannot be sent now. Measurements go with the assets review’s corrections.</p></section>`;
  if(!prop.pin?.confirmed)return `<section class="pa-card pa-measure">${head}<p>Confirm the pin first: is the point on the right house?</p></section>`;
  if(!v||!PM.measurable(v))return `<section class="pa-card pa-measure">${head}<p>Choose a top-down picture to measure on: the close aerial, a dated picture or a sharp public image.</p></section>`;
  if(v.source_id&&PM.publicSeries({history:[v]})[0].coarse)return `<section class="pa-card pa-measure">${head}<p>This public image is too coarse to measure on (${fmt(v.pixel_size_m*100,0)} cm a pixel): use it for seeing what existed. Choose a sharper picture.</p></section>`;
  if(u.saved?.assetId===a.id&&!t?.shapes.length&&!t?.draft.length)return savedPanel(s,a,u);
  const unit=t.unit,body=t.shapes.length?bodyOf(v,t):null;let r=null,problems=[];
  if(body){try{r=PM.measure(body)}catch(e){problems=e.problems||[e.message]}}
  const prov=!r&&t.draft.length>=(t.tool==='line'?2:3)&&!problems.length?provisional(v,t):null;
  const tools=[['area','Area'],['line','Line'],...(t.shapes.some(x=>x.kind==='area')?[['cutout','Cut out']]:[])];
  const dated=v.source_id?null:PM.dateOf(v);
  return `<section class="pa-card pa-measure is-active" aria-labelledby="pa-measure-title">${head.replace('<h2>','<h2 id="pa-measure-title">')}
<p class="pa-muted">${need.needed?`Basis could not size it. ${esc(reasonText(need.reason))}`:'Basis’s quantity: '+esc(`${fmt(a.takeoff?.quantity,2)} ${a.takeoff?.unit||''}`)+'.'} Trace it on this picture; you stay the owner of the number.</p>
<div class="pa-measure-row"><label>Unit <select data-pa-trace="unit">${['SF','SY','LF'].map(x=>`<option ${x===unit?'selected':''}>${x}</option>`).join('')}</select></label><div class="pa-seg" role="group" aria-label="Tool">${tools.map(([k,l])=>`<button data-live="pa-tool" data-value="${k}" aria-pressed="${t.tool===k}" ${unit==='LF'&&k!=='line'?'disabled':''}>${l}</button>`).join('')}</div></div>
${t.tool==='line'&&unit!=='LF'?`<label class="pa-height">Wall height (ft) <input data-pa-trace="height" inputmode="decimal" value="${esc(t.height||'')}" placeholder="for a wall face"></label>`:''}
<p class="pa-hint">${t.tool==='line'?'Click along the line; click the last point again (or press Enter) to finish it.':t.tool==='cutout'?'Click the corners of the part to leave out; click the first corner to close it.':'Click each corner; click the first corner to close the shape.'} Esc cancels the shape being drawn.</p>
<div class="pa-readout" aria-live="polite">${r?`<strong>${fmt(r.quantity,2)} ${esc(r.unit)}</strong><span>${r.expected_error_pct===null?'Expected error unknown (no pixel size)':`Expected error up to ${esc(r.expected_error_pct)} %`} · ${r.shape_count} ${r.area_sf!==null?'area':'line'}${r.shape_count===1?'':'s'}${r.cutout_count?`, ${r.cutout_count} cut out`:''}</span>`:prov?`<strong class="is-provisional">${fmt(prov.quantity,2)} ${esc(prov.unit)}</strong><span>while drawing</span>`:problems.length?`<span class="pa-problem">${esc(problems[0].replace(/^measurement/,'The shape'))}</span>`:'<span>Nothing traced yet.</span>'}</div>
<p class="pa-muted pa-on">On ${esc(v.source_id?`${v.credit}, ${viewDate(v)}`:`the picture of ${PM.dateText(dated)}`)} · ${fmt(v.pixel_size_m*100,1)} cm a pixel${v.source_id?'. A public image has a year only, so the measurement is marked undated.':''}</p>
<div class="row wrap pa-measure-actions"><button class="button" data-live="pa-trace-undo" ${t.shapes.length||t.draft.length?'':'disabled'}>Undo</button><button class="button" data-live="pa-trace-clear" ${t.shapes.length||t.draft.length?'':'disabled'}>Clear</button>${t.tool==='line'&&t.draft.length>=2?'<button class="button" data-live="pa-trace-finish">Finish line</button>':''}<button class="button primary" data-live="pa-trace-save" ${r?'':'disabled'}>Save measurement</button></div></section>`;
 }
 const reasonText=r=>{const x=globalThis.BasisReasons?.explainAll([r])[0]?.text;return x||'it needs a measured quantity.'};
 const bodyOf=(v,t)=>PM.measurementBody({unit:t.unit,shapes:t.shapes,view:v,heightFt:t.tool==='line'||t.shapes.some(x=>x.kind==='line')?(t.height||null):null});
 function provisional(v,t){
  const shapes=t.tool==='cutout'?t.shapes.map((sh,i)=>i===lastArea(t)?{...sh,cutouts:[...(sh.cutouts||[]),t.draft]}:sh):[...t.shapes,t.tool==='line'?{kind:'line',path:t.draft}:{kind:'area',ring:t.draft}];
  try{return PM.measure(PM.measurementBody({unit:t.unit,shapes,view:v,heightFt:t.tool==='line'?(t.height||null):null}))}catch{return null}
 }
 const lastArea=t=>{for(let i=t.shapes.length-1;i>=0;i--)if(t.shapes[i].kind==='area')return i;return -1};
 function savedPanel(s,a,u){
  const sv=u.saved,texts=(globalThis.BasisReasons?.explainAll(sv.flags)||sv.flags.map(f=>({text:f}))).map(x=>x.text);
  return `<section class="pa-card pa-measure is-saved"><header class="pa-measure-head"><h2>${esc(a.label?name(a.label):a.category)} measured</h2></header><p><strong>${fmt(sv.quantity,2)} ${esc(sv.unit)}</strong> is in the assets review’s corrections, waiting for Review &amp; send.</p><p class="pa-muted">${esc(sv.formula)}</p>${texts.length?`<div class="pa-flags"><h3>The review will flag it</h3><ul>${texts.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`:'<p class="pa-muted">No review flag: the picture is dated before the placed-in-service date and sharp enough for its size.</p>'}<div class="row wrap"><a class="button primary" href="${Live.route('assets',s.id)}">Back to assets</a><button class="button" data-live="pa-trace-again">Trace it again</button></div></section>`;
 }

 /* ---------- the page ---------- */
 function pickView(s,cap,pub,u,prop){
  const all=[...(cap?.views||[]),...(pub?PM.publicSeries(pub).map(x=>x.view):[])],by=id=>all.find(v=>v.id===id);
  if(u.view&&by(u.view))return by(u.view);
  if(u.view==='sides'||u.view==='street')return null;
  // Measuring opens on the suggested view; otherwise the wide aerial while the pin is unconfirmed
  // (the house in its setting), the close one after.
  const V=PM.viewsOf(cap);
  if(L.docId)return PM.defaultMeasureView(cap)||(pub?.current&&!PM.publicSeries({history:[pub.current]})[0].coarse?pub.current:null)||V.close;
  return (!prop?.pin?.confirmed&&!narrow()&&V.wide)||V.close||pub?.current||null;
 }
 Live.screens.property=()=>Live.withStudy(s=>{
  const back=`<a class="button" href="${Live.route('processing',s.id)}">${icon('back')}Study progress</a>`;
  if(!shows())return `${heading('Property','',back)}<div class="live-state"><p>Property pictures are not available on this server.</p></div>`;
  const e=propEntry(s);
  if(!e.done)return `${heading('Property','',back)}${Live.loading('Loading the property…')}`;
  if(e.error)return `${heading('Property','',back)}${Live.failed(e.error,'pa-reload')}`;
  const prop=e.data,pe=pubEntry(s),pub=pe?.done&&!pe.error?pe.data:null,u=ui(s);pinNow=prop.pin;
  if(L.docId){assetsEntry(s);if(!u.trace||u.trace.assetId!==L.docId){u.trace=null;if(u.measuring!==L.docId){u.view=null;u.saved=null}}u.measuring=L.docId}else u.measuring=null;
  const cap=shownCapture(s,prop),st=caps().propertyCapture?PM.captureStatus(prop.capture,{pin:prop.pin}):null,gs=caps().publicGeo?PM.gatherStatus(pub?.gather||null):null;
  if(st)st.stale=st.stale&&(cap?.views||[]).some(v=>v.stale&&!sameSpot(v));
  if(st?.state==='ready'&&!st.stale){st.tone='done';st.title='Ready';st.action='retake-optional';st.text=prop.capture.message||'Property pictures are ready.'}
  schedulePoll(s,!!(st?.polling||gs?.polling));
  const v=pickView(s,cap,pub,u,prop);
  if(L.docId&&v&&(!u.trace||u.trace.assetId!==L.docId)){const a=(assetsEntry(s).data?.items||[]).find(x=>x.id===L.docId);if(a)u.trace=newTrace(a,v)}
  if(u.trace&&v&&u.trace.viewId!==v.id)carryTrace(u,v);
  if(L.docId&&v&&u.mode!=='pin')u.mode='measure';else if(!L.docId&&u.mode==='measure')u.mode=null;
  const address=prop.pin?.address||'No address yet';
  const main=u.view==='sides'&&cap?sidesGrid(s,cap):u.view==='street'?streetCard(s,cap):stage(s,v,prop,pub,u);
  const noViews=!cap?.views?.length&&!pub?.current;
  const earlier=cap&&prop.capture&&cap.id!==prop.capture.id?`<p class="pa-note pa-earlier">Showing the pictures taken ${esc(Live.when(cap.finished_at||cap.requested_at))} until the new ones are ready.</p>`:'';
  return `<div class="pa">
<header class="pa-head"><div><p class="pa-crumb"><a href="${Live.route('processing',s.id)}">${esc(s.property_name)}</a></p><h1>Property</h1><p>${esc(address)}</p></div><div class="row wrap pa-head-actions">${actionButtons(s,prop,st,gs,u)}${back}</div></header>
${statusBlock(st,gs)}${earlier}
<div class="pa-layout"><section class="pa-main" aria-label="Pictures">${noViews?'':picker(cap,pub,u,v)}${noViews?`<div class="pa-stage is-empty"><p>${st?.polling?'The pictures appear here as soon as they are taken.':'No pictures yet.'}</p></div>`:main}</section>
<aside class="pa-side">${L.docId?measurePanel(s,prop,v,u):''}${pinCard(s,prop,u,v)}${facts(s,pub)}${L.docId?'':measurePanel(s,prop,v,u)}${(cap?.notes||[]).filter(n=>n.part!=='street'&&n.part!=='history').map(n=>`<p class="pa-note">${esc(PM.noteText(n))}</p>`).join('')}</aside></div>
${noViews?'':`<section class="pa-history" aria-label="Dated views">${strips(s,cap,pub,u,v)}</section>${compare(s,cap,pub,u)}`}
</div>`;
 });
 function newTrace(a,v){const unit=PM.needsMeasurement(a).unit||'SF';return {assetId:a.id,viewId:v.id,unit,tool:unit==='LF'?'line':'area',shapes:[],draft:[],height:''}}
 // A shape belongs to the pixels it was drawn on: it carries to another view only when that view
 // has the same georeference (every dated view shares the close aerial's camera).
 function carryTrace(u,v){
  const all=allViews(L.study),from=all.find(x=>x.id===u.trace.viewId);
  const same=from&&from.width===v.width&&from.height===v.height&&from.pixel_size_m===v.pixel_size_m&&from.lat===v.lat&&from.lon===v.lon&&!from.source_id&&!v.source_id;
  if(!same&&(u.trace.shapes.length||u.trace.draft.length))toast('The tracing was cleared: a shape belongs to the picture it was drawn on.');
  if(!same){u.trace.shapes=[];u.trace.draft=[]}
  u.trace.viewId=v.id;
 }

 /* ---------- the card on Study progress ---------- */
 Live.studyProperty=s=>{
  if(!shows())return '';
  const e=propEntry(s);if(!e.done)return `<section class="panel pa-hub">${Live.loading('Loading the property…')}</section>`;
  if(e.error)return '';
  const prop=e.data,cap=shownCapture(s,prop),st=caps().propertyCapture?PM.captureStatus(prop.capture,{pin:prop.pin}):null,V=PM.viewsOf(cap),v=V.close||V.wide;pinNow=prop.pin;
  if(st?.polling)schedulePoll(s,true);
  const ask=prop.pin&&prop.pin.lat!=null&&!prop.pin.confirmed;
  const g=v&&PM.georef(v),pin=g&&prop.pin?.lat!=null?g.toPixel(prop.pin.lon,prop.pin.lat):null;
  const thumb=v?`<a class="pa-hub-thumb" href="${Live.route('property',s.id)}" style="aspect-ratio:${v.width}/${v.height}">${img(s,v,'Aerial of the property','pa-photo')}${pin?`<svg viewBox="0 0 ${v.width} ${v.height}" preserveAspectRatio="none" aria-hidden="true">${marker(pin[0],pin[1],v.width*2.5,'pa-pin')}</svg>`:''}</a>`:'';
  return `<section class="panel pa-hub" aria-labelledby="pa-hub-title"><header><h2 id="pa-hub-title">Property</h2>${ask?pill('House not confirmed','review'):st?pill(st.title,st.tone):''}</header>${thumb}<p class="pa-address">${esc(prop.pin?.address||'No address yet')}</p>${st&&st.state!=='ready'?`<p class="pa-muted">${esc(st.text)}</p>`:''}<a class="button ${ask||!prop.pin?'primary':''} small" href="${Live.route('property',s.id)}">${ask?'Is this the right house?':!prop.pin?'Add the address':'Open the property'}</a></section>`;
 };

 /* ---------- actions ---------- */
 const A=Live.actions;
 const reload=s=>{Live.invalidate('property:'+s.id);Live.invalidate('property-public:'+s.id);Live.invalidate('property-captures:'+s.id);Live.render()};
 A['pa-reload']=()=>reload(L.study);
 A['pa-view']=(el,id)=>{const u=ui(L.study);u.view=id;if(u.mode==='pin'){u.mode=null;u.candidate=null}Live.render()};
 A['pa-zoom']=(el,dir)=>{const u=ui(L.study);u.zoom=Math.max(1,Math.min(4,dir==='in'?u.zoom*2:u.zoom/2));Live.render()};
 A['pa-compare-mode']=(el,m)=>{ui(L.study).compare.mode=m;Live.render()};
 A['pa-pin-move']=()=>{const u=ui(L.study);u.mode='pin';u.candidate=null;Live.render()};
 A['pa-pin-cancel']=()=>{const u=ui(L.study);u.mode=null;u.candidate=null;Live.render()};
 // The pin is written whole (PUT with If-Match on its version); 412: reload and say so.
 async function putPin(s,body){
  const prop=propEntry(s).data,version=prop?.pin?.version??0;
  try{await api.request('PUT',`${base(s.id)}/property/pin`,{body,headers:{'If-Match':`"${version}"`}});return true}
  catch(e){if(e.status===412){reload(s);toast('Someone changed the pin since this page loaded. It has been reloaded: check it and try again.');return false}throw e}
 }
 A['pa-pin-confirm']=()=>Live.guarded('pa-pin',async()=>{const s=L.study,p=propEntry(s).data?.pin;if(!p||p.lat==null)return;if(await putPin(s,{address:p.address,lat:p.lat,lon:p.lon,confirmed:true})){toast('Pin confirmed: the pictures are of the right house.');reload(s)}});
 A['pa-pin-save']=()=>Live.guarded('pa-pin',async()=>{const s=L.study,u=ui(s),p=propEntry(s).data?.pin;if(!u.candidate)return;const [lon,lat]=u.candidate.map(n=>Math.round(n*1e7)/1e7);
  if(await putPin(s,{address:p?.address||null,lat,lon,confirmed:true})){u.mode=null;u.candidate=null;toast('Pin moved and confirmed. Take the pictures again around it.');reload(s)}});
 async function capture(force){
  const s=L.study,u=ui(s);
  try{const r=await api.request('POST',`${base(s.id)}/property/captures`,{body:force?{force:true}:{}});toast(r?.status==='succeeded'?'These pictures were taken in the last few hours; they are shown.':'Taking the pictures in the background. You can keep working.');reload(s)}
  catch(e){
   if(e.status===409&&e.kind==='capture-in-progress'){toast('Pictures are already being taken; this page follows them.');reload(s);return}
   if(e.status===429||e.status===503){const wait=Number(e.retryAfter)||(e.status===429?60:30);u.retryAt=Date.now()+wait*1000;setTimeout(()=>Live.render(),wait*1000+50);toast(`Basis is busy. Try again in ${wait} seconds.`);Live.render();return}
   if(e.status===422&&e.kind==='capture-no-address'){toast('Add the address first.');return}
   toast(Live.errorText(e));
  }
 }
 A['pa-capture']=(el,v)=>Live.guarded('pa-capture',()=>capture(v==='force'));
 A['pa-gather']=()=>Live.guarded('pa-gather',async()=>{
  const s=L.study,u=ui(s);
  try{await api.request('POST',`${base(s.id)}/property/public`,{body:{force:true}});toast('Gathering the public records in the background.');reload(s)}
  catch(e){if(e.status===409&&e.kind==='geo-in-progress'){reload(s);return}if(e.status===429||e.status===503){const wait=Number(e.retryAfter)||60;u.retryAt=Date.now()+wait*1000;setTimeout(()=>Live.render(),wait*1000+50);toast(`Basis is busy. Try again in ${wait} seconds.`);Live.render();return}toast(Live.errorText(e))}
 });
 // Tracing.
 const tr=()=>ui(L.study).trace;
 A['pa-tool']=(el,k)=>{const t=tr();if(!t)return;t.tool=k;t.draft=[];Live.render()};
 A['pa-trace-undo']=()=>{const t=tr();if(!t)return;if(t.draft.length)t.draft.pop();else{const last=t.shapes[t.shapes.length-1];if(last?.kind==='area'&&last.cutouts?.length)last.cutouts.pop();else t.shapes.pop()}Live.render()};
 A['pa-trace-clear']=()=>{const t=tr();if(!t)return;t.shapes=[];t.draft=[];Live.render()};
 A['pa-trace-finish']=()=>{const t=tr();if(t&&t.tool==='line'&&t.draft.length>=2){t.shapes.push({kind:'line',path:t.draft});t.draft=[];Live.render()}};
 A['pa-trace-again']=()=>{const u=ui(L.study);u.saved=null;Live.render()};
 A['pa-trace-save']=()=>{
  const s=L.study,u=ui(s),t=u.trace;if(!t||!Live.gateOpen('assets_review'))return;
  const v=allViews(s).find(x=>x.id===t.viewId),a=(assetsEntry(s).data?.items||[]).find(x=>x.id===t.assetId);if(!v||!a)return;
  let body,r;try{body=bodyOf(v,t);r=PM.measure(body)}catch(e){toast((e.problems||[e.message])[0]);return}
  const cur=Live.pendingCorrection?.('assets_review',a.id),after={...(cur?.after||{})};for(const k of ['quantity','quantity_unit','quantity_source'])delete after[k];after.measurement=body;
  const formula=PM.formula(r,{source:body.image.source,captured_on:body.image.captured_on,height_ft:body.height_ft});
  Live.addCorrection('assets_review',{key:a.id,entity_type:'asset',entity_id:a.id,before:{...(cur?.before||{}),quantity:a.takeoff?.quantity??null,quantity_unit:a.takeoff?.unit??null},after,reason:cur?.reason??null,title:a.label?name(a.label):a.category,summary:`traced ${r.quantity} ${r.unit} on ${v.source_id?viewDate(v):PM.dateText(PM.dateOf(v))}`});
  u.saved={assetId:a.id,viewId:v.id,shapes:t.shapes,quantity:r.quantity,unit:r.unit,formula,flags:PM.reviewFlags(r,{captured_on:body.image.captured_on,placedInService:s.intake?.placed_in_service_date})};
  t.shapes=[];t.draft=[];Live.render();
 };

 /* ---------- pointer and keys on the stage ---------- */
 const HIT_PX=12;
 function pixelOf(svg,ev){const r=svg.getBoundingClientRect(),vb=svg.viewBox.baseVal;return [(ev.clientX-r.left)/r.width*vb.width,(ev.clientY-r.top)/r.height*vb.height,vb.width/r.width]}
 document.addEventListener('click',ev=>{
  const svg=ev.target.closest?.('svg[data-pa-stage]');if(!svg||Live.screen()!=='property')return;
  const s=L.study,u=ui(s),v=allViews(s).find(x=>x.id===svg.dataset.paStage);if(!v)return;
  const [x,y,k]=pixelOf(svg,ev);
  if(u.mode==='pin'){const g=PM.georef(v);if(!g)return;u.candidate=g.toLonLat(x,y);Live.render();return}
  if(u.mode!=='measure'||!u.trace||narrow())return;
  const t=u.trace,near=(p)=>Math.hypot(p[0]-x,p[1]-y)<=HIT_PX*k;
  if(t.tool==='line'){if(t.draft.length>=2&&near(t.draft[t.draft.length-1])){t.shapes.push({kind:'line',path:t.draft});t.draft=[]}else t.draft.push([x,y])}
  else if(t.draft.length>=3&&near(t.draft[0])){
   if(t.tool==='cutout'){const i=lastArea(t);if(i>=0)(t.shapes[i].cutouts||=[]).push(t.draft)}else t.shapes.push({kind:'area',ring:t.draft});
   t.draft=[];if(t.tool==='cutout')t.tool='area';
  }else t.draft.push([x,y]);
  Live.render();
 });
 document.addEventListener('keydown',ev=>{
  if(Live.screen()!=='property'||!L.study||ev.target.closest?.('input,select,textarea'))return;
  const u=ui(L.study),t=u.trace;
  if(ev.key==='Escape'){if(u.mode==='pin'){u.mode=null;u.candidate=null;Live.render()}else if(t?.draft.length){t.draft=[];Live.render()}}
  if(ev.key==='Enter'&&t?.tool==='line'&&t.draft.length>=2){t.shapes.push({kind:'line',path:t.draft});t.draft=[];Live.render()}
  if((ev.key==='z'||ev.key==='Z')&&(ev.metaKey||ev.ctrlKey)&&t){ev.preventDefault();A['pa-trace-undo']()}
 });
 document.addEventListener('change',ev=>{
  if(Live.screen()!=='property'||!L.study)return;const u=ui(L.study),el=ev.target;
  if(el.dataset.paLayer){u.layers[el.dataset.paLayer]=el.checked;Live.render()}
  else if(el.dataset.paCompare){u.compare[el.dataset.paCompare]=el.value;Live.render()}
  else if(el.dataset.paTrace==='unit'&&u.trace){u.trace.unit=el.value;if(el.value==='LF')u.trace.tool='line';u.trace.shapes=u.trace.shapes.filter(x=>x.kind===(u.trace.tool==='line'?'line':'area'));Live.render()}
  else if(el.dataset.paTrace==='height'&&u.trace){u.trace.height=el.value.trim();Live.render()}
 });
 document.addEventListener('input',ev=>{
  if(Live.screen()!=='property'||!ev.target.matches?.('[data-pa-slider]'))return;
  const u=ui(L.study);u.compare.pos=Number(ev.target.value);ev.target.closest('.pa-compare')?.querySelector('.pa-slider')?.style.setProperty('--pos',u.compare.pos+'%');
 });
 document.addEventListener('submit',ev=>{
  const f=ev.target.closest?.('form[data-pa-form]');if(!f||!L.study)return;ev.preventDefault();
  const s=L.study;
  if(f.dataset.paForm==='address')Live.guarded('pa-address',async()=>{
   const address=f.elements.address.value.trim();if(!address){toast('Give the address.');return}
   const p=propEntry(s).data?.pin;
   if(!(await putPin(s,{address})))return;
   if(!p||p.address!==address){await capture(false)}else reload(s);
  });
  if(f.dataset.paForm==='bracket')Live.guarded('pa-bracket',async()=>{
   const e=renoEntry(s),u=ui(s),all=allViews(s),[kind,...rest]=f.elements.target.value.split(':'),key=rest.join(':');if(!key)return;
   const a=all.find(v=>v.id===u.compare.shownA),b=all.find(v=>v.id===u.compare.shownB);
   if(!a||!b||a.source_id||b.source_id){toast('Choose two dated pictures.');return}
   const br=PM.bracket({date:PM.dateOf(a),reference:`property view ${a.id} (${a.label})`},{date:PM.dateOf(b),reference:`property view ${b.id} (${b.label})`});if(br.error){toast(br.error);return}
   const doc=PM.withBracket(e.data?.renovations||null,kind==='project'?{kind:'project',key}:{kind:'label',label:key},br.bracket);
   try{await api.request('PUT',`${base(s.id)}/renovations`,{body:doc,headers:{'If-Match':`"${e.data?.renovations_version??0}"`}});Live.invalidate('renovations:'+s.id);toast('Recorded: the dates are on the study’s renovations.');Live.render()}
   catch(err){if(err.status===412){Live.invalidate('renovations:'+s.id);Live.render();toast('The renovations changed since this page loaded. They have been reloaded: record the dates again.');return}
    toast(err.errors?.length?err.errors.map(x=>x.detail||x.msg).join(' '):Live.errorText(err))}
  });
 },true);

 Live.afterRender.push(hydrate);
 // Study tools: a link beside History, Study inputs, Results.
 const tools=Live.studyTools;
 if(tools)Live.studyTools=s=>{const html=tools(s);return shows()?html.replace('</section>',`<a class="button" href="${Live.route('property',s.id)}">Property</a></section>`):html};
})();
