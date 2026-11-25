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
  if(caps().publicGeo&&gs&&(gs.state!=='ready'||gs.stale))rows.push(`<div class="pa-status is-${gs.tone}" role="status"><span class="pa-dot" aria-hidden="true"></span><div><strong>Public records · ${esc(gs.title)}</strong><p>${esc(gs.text)}</p></div></div>`);
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
  const g=PM.georef(v),w=v.width,h=v.height,parts=[],parcelPx=[];
  const diagram=diagramOn(v);// the diagram draws the lot lines itself
  if(g&&pub?.parcels?.length)for(const parcel of pub.parcels)for(const ring of PM.parcelRings(parcel,g)){parcelPx.push(ring);if(u.layers.parcel&&!diagram)parts.push(`<polygon class="pa-parcel ${parcel.under_pin?'':'is-adjacent'}" points="${ring.map(p=>p.map(n=>n.toFixed(1)).join(',')).join(' ')}"/>`)}
  if(g&&u.layers.pin&&prop.pin?.lat!=null){const [x,y]=g.toPixel(prop.pin.lon,prop.pin.lat);parts.push(marker(x,y,w,'pa-pin'))}
  if(g&&u.candidate){const [x,y]=g.toPixel(...u.candidate);parts.push(marker(x,y,w,'pa-pin is-candidate'))}
  // The measure tool while measuring here; after saving, the saved shape with its labels.
  const tracing=u.mode==='measure'&&u.trace&&u.trace.viewId===v.id&&!(u.saved?.assetId===L.docId&&!u.trace.shapes.length&&!u.trace.draft.length);
  const buildingPx=PM.buildingOnView(pub?.lidar,v)?.points||null;
  if(tracing){scopes.property={s,t:u.trace,v,w,h,parcels:parcelPx,building:buildingPx,doc:null};parts.push(toolLayer(u.trace,v))}
  else if(u.saved?.viewId===v.id&&u.saved.assetId===L.docId)parts.push(toolLayer({shapes:u.saved.shapes,draft:[],tool:'area',unit:u.saved.unit,space:'lonlat',k:u.trace?.k||null},v,{readOnly:true}));
  const zoom=u.zoom||1,mode=u.mode==='pin'?'is-pin':u.mode==='measure'?'is-measure':'';
  return `<div class="pa-stage ${mode}" data-zoom="${zoom}"><div class="mt-stage-view"><div class="pa-zoom" tabindex="0" aria-label="Picture; scroll to pan when zoomed"><div class="pa-canvas${diagram?' is-diagram':''}" style="width:${zoom*100}%;aspect-ratio:${w}/${h}">${diagram?diagramFor('property',s,v,{except:tracing?u.trace.assetId:null,trace:tracing?u.trace:null,pin:g&&u.layers.pin&&prop.pin?.lat!=null?{at:g.toPixel(prop.pin.lon,prop.pin.lat),r:Math.max(7,w/110)*1.9}:null}):img(s,v,v.label||'Property picture','pa-photo')}<svg class="pa-overlay" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" data-pa-stage="${esc(v.id)}" data-mt-scope="property" role="img" aria-label="${esc(overlayLabel(prop,pub,u))}">${parts.join('')}</svg>${tracing?editBox(u.trace,v,w,h,'property'):''}</div></div>${diagram?furnitureHtml('property'):''}</div><div class="pa-stage-tools"><div class="pa-zoom-tools" role="group" aria-label="Zoom"><button class="pa-icon-btn" data-live="pa-zoom" data-value="out" aria-label="Zoom out" ${zoom<=1?'disabled':''}>−</button><span>${zoom*100}%</span><button class="pa-icon-btn" data-live="pa-zoom" data-value="in" aria-label="Zoom in" ${zoom>=4?'disabled':''}>+</button></div>${pictureToggle(v)}${g?`<label class="pa-toggle"><input type="checkbox" data-pa-layer="pin" ${u.layers.pin?'checked':''}> Pin</label>${pub?.parcels?.length&&!diagram?`<label class="pa-toggle"><input type="checkbox" data-pa-layer="parcel" ${u.layers.parcel?'checked':''}> Parcel</label>`:''}`:''}</div>${caption(v)}</div>`;
 }
 const overlayLabel=(prop,pub,u)=>[u.mode==='pin'?'Click the house to move the pin.':u.mode==='measure'?'Click corners to trace.':'',prop.pin?'The pin marks the house.':'',pub?.parcels?.length?'The parcel outline is drawn.':''].filter(Boolean).join(' ');
 const marker=(x,y,w,cls)=>{const r=Math.max(7,w/110);return `<g class="${cls}" transform="translate(${x.toFixed(1)} ${y.toFixed(1)})"><g class="pa-pin-mark"><circle r="${r*1.9}" class="pa-pin-halo"/><circle r="${r}" class="pa-pin-dot"/></g></g>`};
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

 /* ---------- the measure tool (EVAL_REPORT 104; docs/review_2026-10-15/AI_MEASUREMENT_PLAN.md sections 3 and 6) ----------
  * One tool in two places: the Property page's panel (scope "property": ui(s).trace) and a dialog
  * over the Assets list and the asset page (scope "dialog": L.mtDialog). A trace is drawn in the
  * picture's own pixels; its frame (live/property-model.js frameOf) turns pixels into feet as the
  * server will. On the shape: a length on every edge (feet and inches, outside the shape, upright,
  * whole feet or hidden when the edge is short on screen), area and perimeter (or the length, or a
  * wall's length x height) at its middle. Corners drag (pointer events, touch too), snap to the
  * shape's corners and the parcel's corners and edges within a few screen pixels, are added by
  * dragging an edge's middle handle, deleted with Delete or Backspace or the button; every edit is
  * undoable, and an edit that makes a ring cross itself is refused with the server's own words.
  * Clicking an edge's label types its length: the far corner moves along the edge (Fusion's sketch
  * dimension; the label always matches the geometry). A photo is measured in its pixels with a
  * scale set from two clicked points and a typed known length. The measurement records who drew
  * it, whether it was verified, the picture, and the stored measurement it replaced. */
 const MT_FONT=12,SNAP_PX=8,HANDLE_PX=6,KEY_STEP_PX=1;
 const r3=n=>Math.round(n*1000)/1000,rp=p=>[r3(p[0]),r3(p[1])],f1=n=>(Math.round(n*10)/10).toString();
 const ringsOf=sh=>sh.kind==='line'?[{r:'o',pts:sh.path,closed:false}]:[{r:'o',pts:sh.ring,closed:true},...(sh.cutouts||[]).map((c,k)=>({r:k,pts:c,closed:true}))];
 const ptsAt=(t,s,r)=>{const sh=t.shapes[s];if(!sh)return null;return sh.kind==='line'?sh.path:r==='o'?sh.ring:sh.cutouts?.[r]||null};
 const setPts=(t,s,r,pts)=>{const sh=t.shapes[s];if(sh.kind==='line')sh.path=pts;else if(r==='o')sh.ring=pts;else sh.cutouts[r]=pts};
 const parseRef=v=>{const [s,r,i]=String(v).split(':');return {s:Number(s),r:r==='o'?'o':Number(r),i:Number(i)}};
 const refKey=(s,r,i)=>`${s}:${r}:${i}`;
 // A scale's typed length (feet) -> the frame; lon/lat views carry their own.
 function frameFor(t,v){
  if(t.space==='image')return t.scale?.points?.length===2&&Number(t.scale.length)>0?PM.frameOf(null,{scale:{points:t.scale.points,length:Number(t.scale.length),unit:'ft'}}):null;
  return PM.frameOf(v);
 }
 const heightOf=t=>(t.tool==='line'||t.shapes.some(x=>x.kind==='line'))&&t.unit!=='LF'?(t.height||null):null;
 // The body the server will compute (before provenance): lon/lat on a view, pixels with a scale on a photo.
 function traceBody(t,v,c){
  if(t.space==='image'){if(!(t.scale?.points?.length===2&&Number(t.scale.length)>0))throw Object.assign(new Error('Set the scale first: click the two ends of something whose length you know, then type it.'),{problems:['Set the scale first: click the two ends of something whose length you know, then type it.']});
   return PM.imageBody({unit:t.unit,shapes:t.shapes,scale:{points:t.scale.points,length:Number(t.scale.length),unit:'ft'},doc:c.doc,heightFt:heightOf(t)})}
  return PM.measurementBody({unit:t.unit,shapes:t.shapes,view:v,heightFt:heightOf(t)});
 }
 const bodyOf=(v,t)=>traceBody(t,v,{});
 // With provenance: an untouched stored measurement is sent as it is, marked; an edit is the
 // engineer's, keeping what it replaced (the stored measurement's own original when it has one).
 function finalBody(t,v,c){
  if(t.fromBase&&t.base){const out=JSON.parse(JSON.stringify(t.base));out.verified=!!t.verified;return out}
  const view=t.space==='image'?PM.viewRef({kind:'document',id:c.doc.id,label:c.doc.filename},{width:c.w,height:c.h}):PM.viewRef(v);
  return PM.withProvenance(traceBody(t,v,c),{drawnBy:'engineer',verified:!!t.verified,view,replaces:t.base||null});
 }
 // The original to draw dashed beside the edit: the stored measurement's original, or the stored
 // measurement itself once the engineer has changed it (SiteRecon's locked base view).
 const originalOf=t=>t.base?(t.base.original||(t.fromBase?null:t.base)):null;
 // Edge lengths straight from a body's coordinates (the original, on any view).
 function bodyEdges(body){
  if(!body?.shapes)return [];
  const fpx=body.space==='image'?PM.scaleFeetPerPx(body.scale):null;
  const seg=(a,b)=>body.space==='image'?Math.hypot(a[0]-b[0],a[1]-b[1])*fpx:PM.lonlatPathFt([a,b]);
  return body.shapes.map(sh=>{const pts=sh.kind==='line'?sh.path:sh.ring,n=pts.length,closed=sh.kind!=='line',out=[];for(let i=0;i<(closed?n:n-1);i++)out.push(seg(pts[i],pts[(i+1)%n]));return out});
 }

 /* ----- what the tool draws ----- */
 const labelBox=(text,k)=>({w:(text.length*0.62*MT_FONT+10)*k,h:18*k});
 function labelSvg(l,{k,ref,editing,was,readOnly,what}){
  if(l.mode==='hidden'||editing)return '';
  const b=labelBox(l.text,k),font=MT_FONT*k,tip=`${l.full}${was!=null?` · Basis’s draft ${PM.ftIn(was)}`:''}`;
  return `<g class="mt-label is-${l.mode}${was!=null&&PM.ftIn(was)!==l.full?' is-changed':''}" transform="translate(${f1(l.x)} ${f1(l.y)}) rotate(${f1(l.angle)})" ${readOnly?'':`data-mt-edge="${ref}" tabindex="0" role="button" aria-label="${esc(`Edge ${l.i+1}${what}: ${l.words}${was!=null?`, Basis’s draft ${PM.ftInWords(was)}`:''}. Press Enter to type its length.`)}"`}><title>${esc(tip)}</title><rect x="${f1(-b.w/2)}" y="${f1(-b.h/2)}" width="${f1(b.w)}" height="${f1(b.h)}" rx="${f1(4*k)}"/><text y="${f1(0.36*font)}" font-size="${f1(font)}">${esc(l.text)}</text></g>`;
 }
 const handleSvg=(cls,attr,p,r,label)=>`<g class="${cls}" ${attr} transform="translate(${f1(p[0])} ${f1(p[1])})"${label?` tabindex="0" role="button" aria-label="${esc(label)}"`:''}><circle class="mt-hit" r="${f1(r*2)}"/><circle class="mt-dot" r="${f1(r)}"/></g>`;
 // The SVG layer of a trace on its view (read-only for a stored measurement on the asset page).
 function toolLayer(t,v,{frame=frameFor(t,v),readOnly=false}={}){
  const k=t.k||1,hr=HANDLE_PX*k,pts=p=>p.map(q=>q.map(n=>n.toFixed(1)).join(',')).join(' '),out=[],labels=[],handles=[];
  const orig=originalOf(t),origPx=orig&&!readOnly?(t.origPx||(originalPixels(t,v,v.width,v.height),t.origPx)):null,origEdges=orig?bodyEdges(orig):[];
  // The original, dashed, over the edit's fill and under its labels and handles.
  const dashed=[];if(origPx)for(const sh of origPx.shapes){if(sh.kind==='line')dashed.push(`<polyline class="mt-original is-line" points="${pts(sh.path)}"/>`);else{dashed.push(`<polygon class="mt-original" points="${pts(sh.ring)}"/>`);for(const c of sh.cutouts||[])dashed.push(`<polygon class="mt-original is-cut" points="${pts(c)}"/>`)}}
  const many=t.shapes.length>1;
  t.shapes.forEach((sh,s)=>{
   for(const {r,pts:p,closed} of ringsOf(sh)){
    out.push(closed?`<polygon class="pa-trace mt-shape${r!=='o'?' is-cut':''}" points="${pts(p)}"/>`:`<polyline class="pa-trace mt-shape is-line" points="${pts(p)}"/>`);
    const what=`${r==='o'?'':' of the cut-out'}${many?` of shape ${s+1}`:''}`;
    if(frame){
     const lengths=PM.edgeLengths(p,closed,frame),cmp=r==='o'&&orig?PM.edgeComparison(lengths,origEdges[s]||null):null;
     for(const l of PM.edgeLabels(p,closed,lengths,{k,font:MT_FONT}))labels.push(labelSvg(l,{k,ref:refKey(s,r,l.i),editing:t.edit&&t.edit.s===s&&t.edit.r===r&&t.edit.i===l.i,was:cmp?cmp[l.i].was:null,readOnly,what}));
    }
    if(readOnly)continue;
    const n=p.length;
    for(let i=0;i<(closed?n:n-1);i++){const a=p[i],b=p[(i+1)%n];if(Math.hypot(b[0]-a[0],b[1]-a[1])/k>hr*5/k)handles.push(handleSvg('mt-mid',`data-mt-mid="${refKey(s,r,i)}"`,[(a[0]+b[0])/2,(a[1]+b[1])/2],hr*0.75,''))}
    p.forEach((q,i)=>{const sel=t.sel&&t.sel.s===s&&t.sel.r===r&&t.sel.i===i;handles.push(handleSvg(`mt-vertex${sel?' is-selected':''}`,`data-mt-vertex="${refKey(s,r,i)}"`,q,sel?hr*1.25:hr,`Corner ${i+1}${what}: drag it, or use the arrow keys; Delete removes it, plus adds a corner after it`))});
   }
   if(frame){
    const sum=PM.shapeSummary(sh,frame,{unit:t.unit,heightFt:Number(heightOf(t))||null}),font=MT_FONT*k;
    if(sh.kind==='area'){const [x,y]=PM.labelPoint(sh.ring);labels.push(`<g class="mt-total" transform="translate(${f1(x)} ${f1(y)})"><text font-size="${f1(font*1.2)}">${esc(sum.area)}</text><text y="${f1(font*1.35)}" font-size="${f1(font*0.95)}">${esc(sum.perimeter)}</text></g>`)}
    else if(sh.path.length>2||Number(heightOf(t))>0){const q=sh.path[sh.path.length-1];labels.push(`<g class="mt-total is-line" transform="translate(${f1(q[0]+10*k)} ${f1(q[1]-10*k)})"><text font-size="${f1(font*1.05)}">${esc(sum.text)}</text></g>`)}
   }
  });
  if(t.draft?.length){
   out.push(`<polyline class="pa-trace is-draft ${t.tool==='cutout'?'is-cut':''}" points="${pts(t.draft)}"/>`);
   if(frame&&t.draft.length>1)for(const l of PM.edgeLabels(t.draft,false,PM.edgeLengths(t.draft,false,frame),{k,font:MT_FONT}))labels.push(labelSvg(l,{k,ref:'',readOnly:true}));
   t.draft.forEach((p,i)=>out.push(`<circle class="pa-vertex ${i===0&&t.tool!=='line'&&t.draft.length>=3?'is-close':''}" cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="${f1(i===0?hr*1.3:hr*0.8)}"/>`));
  }
  if(t.space==='image'&&t.scale?.points?.length){
   const sp=t.scale.points;if(sp.length===2){out.push(`<line class="mt-scale" x1="${f1(sp[0][0])}" y1="${f1(sp[0][1])}" x2="${f1(sp[1][0])}" y2="${f1(sp[1][1])}"/>`);
    if(Number(t.scale.length)>0){const lab=PM.edgeLabels(sp,false,[Number(t.scale.length)],{k,font:MT_FONT})[0];if(lab.mode!=='hidden')labels.push(`<g class="mt-label is-scale" transform="translate(${f1(lab.x)} ${f1(lab.y)}) rotate(${f1(lab.angle)})"><title>The known length the scale is set from</title><rect x="${f1(-labelBox('Scale '+lab.text,k).w/2)}" y="${f1(-9*k)}" width="${f1(labelBox('Scale '+lab.text,k).w)}" height="${f1(18*k)}" rx="${f1(4*k)}"/><text y="${f1(0.36*MT_FONT*k)}" font-size="${f1(MT_FONT*k)}">Scale ${esc(lab.text)}</text></g>`)}}
   if(!readOnly)sp.forEach((q,i)=>handles.push(handleSvg('mt-vertex is-scale',`data-mt-scale="${i}"`,q,hr,`End ${i+1} of the known length: drag it onto the object's end`)));
  }
  if(t.snapAt)out.push(`<circle class="mt-snap" cx="${f1(t.snapAt[0])}" cy="${f1(t.snapAt[1])}" r="${f1(hr*2.2)}"/>`);
  return `<g class="pa-traces mt-layer" data-mt-layer>${out.join('')}${dashed.join('')}${labels.join('')}${handles.join('')}</g>`;
 }
 // The box around a trace's shapes (and its original), in picture pixels, or null.
 function shapesBox(t){
  const pts=[...t.shapes.flatMap(sh=>sh.kind==='line'?sh.path:sh.ring),...(t.origPx?.shapes||[]).flatMap(sh=>sh.kind==='line'?sh.path:sh.ring)];
  if(!pts.length)return null;const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]),x=Math.min(...xs),y=Math.min(...ys);
  return {x,y,w:Math.max(...xs)-x,h:Math.max(...ys)-y};
 }
 // The typed-length box, over the edge's label.
 function editBox(t,v,w,h,scope){
  if(!t.edit)return '';const frame=frameFor(t,v),p=ptsAt(t,t.edit.s,t.edit.r);if(!p||!frame)return '';
  const closed=t.shapes[t.edit.s].kind!=='line'||t.edit.r!=='o',lengths=PM.edgeLengths(p,closed,frame),l=PM.edgeLabels(p,closed,lengths,{k:t.k||1,font:MT_FONT})[t.edit.i];if(!l)return '';
  return `<form class="mt-edit" data-mt-form="edge" data-mt-scope="${scope}" style="left:${(l.x/w*100).toFixed(3)}%;top:${(l.y/h*100).toFixed(3)}%"><label class="sr-only" for="mt-edit-input">Length of edge ${l.i+1}</label><input id="mt-edit-input" name="edge_length" autocomplete="off" spellcheck="false" value="${esc(t.edit.value??l.full)}" aria-describedby="mt-edit-help"><button class="button small primary" type="submit">Set</button><small id="mt-edit-help">${t.edit.error?`<span class="pa-problem">${esc(t.edit.error)}</span>`:`54'-3", 54.25 or 16.5 m · Enter sets it, Esc cancels`}</small></form>`;
 }
 // The picture with the tool over it (the Property page's stage builds its own frame around it).
 // The dialog's picture fits the height it is given (CSS container units: --mt-ar is its aspect).
 function toolCanvas(scope,t,v,{w,h,imgHtml,extraSvg='',label='Picture to measure on',zoom=1}){
  return `<div class="pa-canvas mt-canvas${diagramOn(v)?' is-diagram':''}" style="--mt-zoom:${zoom};--mt-ar:${(w/h).toFixed(6)};aspect-ratio:${w}/${h}">${imgHtml}<svg class="pa-overlay mt-overlay" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" data-pa-stage="${esc(v.id)}" data-mt-scope="${scope}" role="img" aria-label="${esc(label)}">${extraSvg}${toolLayer(t,v)}</svg>${editBox(t,v,w,h,scope)}</div>`;
 }

 /* ----- the panel ----- */
 const reasonText=r=>{const x=globalThis.BasisReasons?.explainAll([r])[0]?.text;return x||'it needs a measured quantity.'};
 function provisional(v,t,c={}){
  const shapes=t.tool==='cutout'?t.shapes.map((sh,i)=>i===lastArea(t)?{...sh,cutouts:[...(sh.cutouts||[]),t.draft]}:sh):[...t.shapes,t.tool==='line'?{kind:'line',path:t.draft}:{kind:'area',ring:t.draft}];
  try{return PM.measure(traceBody({...t,shapes},v,c))}catch{return null}
 }
 const lastArea=t=>{for(let i=t.shapes.length-1;i>=0;i--)if(t.shapes[i].kind==='area')return i;return -1};
 function readoutHtml(t,v,c){
  let r=null,problems=[];
  if(t.shapes.length){try{r=PM.measure(traceBody(t,v,c))}catch(e){problems=e.problems||[e.message]}}
  const prov=!r&&t.draft.length>=(t.tool==='line'?2:3)&&!problems.length?provisional(v,t,c):null;
  const msg=t.msg?`<span class="pa-problem" role="alert">${esc(t.msg)}</span>`:'';
  return {r,html:`${r?`<strong>${fmt(r.quantity,2)} ${esc(r.unit)}</strong><span>${r.expected_error_pct===null?'Expected error unknown (no pixel size)':`Expected error up to ${esc(r.expected_error_pct)} %`} · ${r.shape_count} ${r.area_sf!==null?'area':'line'}${r.shape_count===1?'':'s'}${r.cutout_count?`, ${r.cutout_count} cut out`:''}</span>`:prov?`<strong class="is-provisional">${fmt(prov.quantity,2)} ${esc(prov.unit)}</strong><span>while drawing</span>`:problems.length?`<span class="pa-problem">${esc(problems[0].replace(/^measurement/,'The shape'))}</span>`:'<span>Nothing traced yet.</span>'}${msg}`};
 }
 // Every edge in a table (the labels a short edge hides, a keyboard way to type a length, and the
 // per-edge before and after against the original).
 function edgeTable(t,v,scope,c={}){
  const frame=frameFor(t,v);if(!frame||!t.shapes.length)return '';
  const orig=originalOf(t),origEdges=orig?bodyEdges(orig):[],rows=[];
  t.shapes.forEach((sh,s)=>{const p=sh.kind==='line'?sh.path:sh.ring,closed=sh.kind!=='line',lengths=PM.edgeLengths(p,closed,frame),cmp=orig?PM.edgeComparison(lengths,origEdges[s]||null):null;
   lengths.forEach((ft,i)=>rows.push(`<tr><th scope="row">${t.shapes.length>1?`${s+1}.`:''}${i+1}</th><td><button class="mt-len" data-live="mt-edge-edit" data-value="${refKey(s,'o',i)}" data-mt-scope="${scope}" aria-label="Type the length of edge ${i+1}">${esc(PM.ftIn(ft))}</button></td>${orig?`<td>${cmp?esc(PM.ftIn(cmp[i].was)):'—'}</td><td class="${cmp&&Math.abs(cmp[i].change)>=1/24?'mt-delta':''}">${cmp?(Math.abs(cmp[i].change)<1/24?'same':`${cmp[i].change>0?'+':'−'}${esc(PM.ftIn(Math.abs(cmp[i].change)))}`):'—'}</td>`:''}</tr>`))});
  const total=orig?(()=>{try{const now=PM.measure(traceBody(t,v,c)),was=PM.measure(orig);return `<p class="pa-muted">${esc(PM.drawnByText(orig.drawn_by))}: ${fmt(was.quantity,2)} ${esc(was.unit)} · now ${fmt(now.quantity,2)} ${esc(now.unit)}${!origEdges.every((e,s)=>e.length===(t.shapes[s]?PM.edgeLengths(t.shapes[s].kind==='line'?t.shapes[s].path:t.shapes[s].ring,t.shapes[s].kind!=='line',frame).length:-1))?' (the corners differ, so the edges are compared as totals)':''}</p>`}catch{return ''}})():'';
  return `<details class="mt-edges" ${rows.length<=12?'open':''}><summary>Edges <span class="pa-muted">${rows.length}</span></summary><table class="mt-edge-table"><thead><tr><th scope="col">Edge</th><th scope="col">Length</th>${orig?`<th scope="col">${orig.drawn_by&&orig.drawn_by!=='engineer'?'Basis’s draft':'Before'}</th><th scope="col">Change</th>`:''}</tr></thead><tbody>${rows.join('')}</tbody></table>${total}</details>`;
 }
 function toolPanel(scope,s,a,t,v,c){
  const unit=t.unit,image=t.space==='image',scaled=!image||(t.scale?.points?.length===2&&Number(t.scale.length)>0);
  const {r,html}=readoutHtml(t,v,c);
  const tools=[...(image?[['scale','Scale']]:[]),['area','Area'],['line','Line'],...(t.shapes.some(x=>x.kind==='area')?[['cutout','Cut out']]:[])];
  const orig=originalOf(t),base=t.base;
  const hint=t.tool==='scale'?'Click the two ends of something whose length you know (a door, a cabinet), then type that length.':t.tool==='line'?'Click along the line; click the last point again (or press Enter) to finish it.':t.tool==='cutout'?'Click the corners of the part to leave out; click the first corner to close it.':'Click each corner; click the first corner to close the shape.';
  const scaleBlock=image?`<div class="mt-scale-box"><label>Known length <input data-mt-input="scale" data-mt-scope="${scope}" inputmode="decimal" value="${esc(t.scale.text||'')}" placeholder="e.g. 80 in" aria-describedby="mt-known-help"></label><p class="pa-muted" id="mt-known-help">${t.scale.points.length<2?'Choose Scale and click both ends of it on the photo.':Number(t.scale.length)>0?`Set: ${esc(PM.ftIn(Number(t.scale.length)))} between the two points. Drag either end to adjust.`:'Type the length between the two points.'}</p><div class="mt-known" role="group" aria-label="Common known lengths">${PM.KNOWN_LENGTHS.map(k=>`<button class="pa-chip" data-live="mt-known" data-value="${k.length} in" data-mt-scope="${scope}" title="${esc(k.note)}">${esc(k.label)} ${k.length} in</button>`).join('')}</div>${t.scaleError?`<p class="pa-problem" role="alert">${esc(t.scaleError)}</p>`:''}</div>`:'';
  const prov=base?`<p class="mt-prov ${base.drawn_by&&base.drawn_by!=='engineer'?'is-draft':''}">${esc(PM.drawnByText(base.drawn_by))}${base.verified?' · verified':' · not verified'}${orig&&!t.fromBase?' · your edit replaces it; the original stays dashed on the picture':t.fromBase?' · check it, change what is wrong, then mark it verified':''}</p>`:'';
  const del=!!t.sel&&t.sel.scale===undefined;
  return `${prov}<div class="pa-measure-row"><label>Unit <select data-mt-input="unit" data-mt-scope="${scope}">${['SF','SY','LF'].map(x=>`<option ${x===unit?'selected':''}>${x}</option>`).join('')}</select></label><div class="pa-seg" role="group" aria-label="Tool">${tools.map(([k,l])=>`<button data-live="pa-tool" data-value="${k}" data-mt-scope="${scope}" aria-pressed="${t.tool===k}" ${(unit==='LF'&&(k==='area'||k==='cutout'))||(!scaled&&k!=='scale')?'disabled':''}>${l}</button>`).join('')}</div><label class="pa-toggle mt-snap-toggle"><input type="checkbox" data-mt-input="snap" data-mt-scope="${scope}" ${t.snap!==false?'checked':''}> Snap</label></div>
${scaleBlock}
${t.tool==='line'&&unit!=='LF'?`<label class="pa-height">Wall height (ft) <input data-mt-input="height" data-mt-scope="${scope}" inputmode="decimal" value="${esc(t.height||'')}" placeholder="for a wall face"></label>`:''}
<p class="pa-hint">${hint} Drag a corner or an edge’s middle dot; click a length to type it; Esc cancels.</p>
<div class="pa-readout" aria-live="polite" data-mt-readout="${scope}">${html}</div>
${edgeTable(t,v,scope,c)}
<label class="mt-verified"><input type="checkbox" data-mt-input="verified" data-mt-scope="${scope}" ${t.verified?'checked':''} ${r||t.fromBase?'':'disabled'}> <span><strong>Verified</strong> I checked this measurement against the picture</span></label>
<div class="row wrap pa-measure-actions"><button class="button" data-live="pa-trace-undo" data-mt-scope="${scope}" ${t.hist?.past.length||t.shapes.length||t.draft.length?'':'disabled'}>Undo</button><button class="button" data-live="mt-redo" data-mt-scope="${scope}" ${t.hist?.future.length?'':'disabled'}>Redo</button><button class="button" data-live="mt-delete" data-mt-scope="${scope}" ${del?'':'disabled'}>Delete corner</button><button class="button" data-live="pa-trace-clear" data-mt-scope="${scope}" ${t.shapes.length||t.draft.length?'':'disabled'}>Clear</button>${t.tool==='line'&&t.draft.length>=2?`<button class="button" data-live="pa-trace-finish" data-mt-scope="${scope}">Finish line</button>`:''}<button class="button primary" data-live="pa-trace-save" data-mt-scope="${scope}" ${r||(t.fromBase&&t.base)?'':'disabled'}>Save measurement</button></div>`;
 }
 function savedPanel(s,a,u,scope='property'){
  const sv=u.saved,texts=(globalThis.BasisReasons?.explainAll(sv.flags)||sv.flags.map(f=>({text:f}))).map(x=>x.text);
  const back=scope==='dialog'?`<button class="button primary" data-live="mt-close">Back to ${esc(L.mtDialog?.from==='asset-detail'?'the asset':'the assets')}</button>`:`<a class="button primary" href="${Live.route('assets',s.id)}">Back to assets</a>`;
  return `<section class="pa-card pa-measure is-saved"><header class="pa-measure-head"><h2>${esc(a.label?name(a.label):a.category)} measured</h2></header><p><strong>${fmt(sv.quantity,2)} ${esc(sv.unit)}</strong> is in the assets review’s corrections, waiting for Review &amp; send.</p><p class="pa-muted">${esc(sv.formula)}</p>${texts.length?`<div class="pa-flags"><h3>The review will flag it</h3><ul>${texts.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`:'<p class="pa-muted">No review flag: the picture is dated no later than a year after the placed-in-service date and is sharp enough for its size.</p>'}<div class="row wrap">${back}<button class="button" data-live="pa-trace-again" data-mt-scope="${scope}">Trace it again</button></div></section>`;
 }
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
  const dated=v.source_id?null:PM.dateOf(v);
  return `<section class="pa-card pa-measure is-active" aria-labelledby="pa-measure-title" data-mt-root="property">${head.replace('<h2>','<h2 id="pa-measure-title">')}
<p class="pa-muted">${need.needed?`Basis could not size it. ${esc(reasonText(need.reason))}`:'Basis’s quantity: '+esc(`${fmt(a.takeoff?.quantity,2)} ${a.takeoff?.unit||''}`)+'.'} Trace it on this picture; you stay the owner of the number.</p>
${toolPanel('property',s,a,t,v,{})}
<p class="pa-muted pa-on">On ${esc(v.source_id?`${v.credit}, ${viewDate(v)}`:`the picture of ${PM.dateText(dated)}`)} · ${fmt(v.pixel_size_m*100,1)} cm a pixel${v.source_id?'. A public image has a year only, so the measurement is marked undated.':''}</p></section>`;
 }

 /* ---------- a trace: started empty, from the unsent correction, or from the stored measurement ---------- */
 // The stored measurement (a sent trace, later a model's draft) is the base an edit replaces; an
 // unsent correction's measurement is where the engineer left off (it already names that base).
 function newTrace(a,v,{space='lonlat',doc=null,w=null,h=null}={}){
  const off=PM.measureOffer(a),unit=off.unit||'SF',stored=off.stored?PM.bodyOfRecord(off.stored):null;
  const pending=Live.pendingCorrection?.('assets_review',a.id)?.after?.measurement||null;
  const t={assetId:a.id,viewId:v?.id||null,space,docId:doc?.id||null,unit,tool:unit==='LF'?'line':'area',shapes:[],draft:[],height:'',
   scale:space==='image'?{points:[],length:'',unit:'ft',text:''}:null,sel:null,edit:null,hist:{past:[],future:[]},snap:true,k:null,
   base:stored,fromBase:false,verified:!stored,msg:null,origPx:null};
  const start=pending||stored;
  if(start&&start.space===space&&(space!=='image'||start.image?.document_id===doc?.id)){
   const px=PM.pixelShapes(start,v,{width:w,height:h});
   if(px){t.shapes=px.shapes.map(sh=>sh.kind==='line'?{kind:'line',path:sh.path.map(rp)}:{kind:'area',ring:sh.ring.map(rp),...(sh.cutouts?{cutouts:sh.cutouts.map(c=>c.map(rp))}:{})});
    if(px.scale&&t.scale){t.scale.points=px.scale.points.map(rp);t.scale.length=start.scale.unit==='m'?start.scale.length*PM.FT_PER_M:start.scale.length;t.scale.text=PM.ftIn(Number(t.scale.length))}
    t.unit=start.unit;t.tool=t.shapes[0]?.kind==='line'?'line':'area';if(start.height_ft!=null)t.height=String(start.height_ft);
    // Untouched while it is the stored measurement itself (or the unsent copy that only marks it).
    t.fromBase=!!stored&&(!pending||pending.drawn_by===stored.drawn_by&&!pending.original===!stored.original&&PM.sameShapes(pending,stored));
    t.verified=start.verified??!stored;
   }
  }
  // A photo is scaled before anything is drawn on it.
  if(space==='image'&&!(t.scale?.points?.length===2&&Number(t.scale.length)>0))t.tool='scale';
  originalPixels(t,v,w,h);
  return t;
 }
 // The original drawn on this view: lon/lat on any georeferenced view; a photo's only on its photo.
 function originalPixels(t,v,w,h){
  const o=originalOf(t);t.origPx=null;if(!o)return;
  if(o.space==='lonlat'&&t.space==='lonlat')t.origPx=PM.pixelShapes(o,v);
  else if(o.space==='image'&&t.space==='image'&&o.image?.document_id===t.docId)t.origPx=PM.pixelShapes(o,null,{width:w,height:h});
 }
 // A shape belongs to the ground, not the pixels: on lon/lat views it carries to any other
 // georeferenced view (each converted through lon/lat); a photo's shapes stay on their photo.
 function carryTrace(u,v){
  const all=allViews(L.study),from=all.find(x=>x.id===u.trace.viewId),t=u.trace;
  const gFrom=from&&PM.georef(from),gTo=PM.georef(v),move=p=>rp(gTo.toPixel(...gFrom.toLonLat(p[0],p[1])));
  if(gFrom&&gTo&&t.space==='lonlat'){
   t.shapes=t.shapes.map(sh=>sh.kind==='line'?{kind:'line',path:sh.path.map(move)}:{kind:'area',ring:sh.ring.map(move),...(sh.cutouts?{cutouts:sh.cutouts.map(c=>c.map(move))}:{})});t.draft=t.draft.map(move);
  }else{if(t.shapes.length||t.draft.length)toast('The tracing was cleared: a shape belongs to the picture it was drawn on.');t.shapes=[];t.draft=[]}
  t.viewId=v.id;t.sel=null;t.edit=null;t.k=null;originalPixels(t,v,v.width,v.height);
 }

 /* ---------- the scopes: which trace, view and picture an event is about ---------- */
 const scopes={};// scope -> {s, a, t, v, w, h, doc, parcels} as last rendered
 function scopeOf(el){return el?.closest?.('[data-mt-scope]')?.dataset.mtScope||el?.closest?.('[data-mt-root]')?.dataset.mtRoot||'property'}
 function ctxOf(scope){
  const c=scopes[scope];if(c&&c.t)return c;
  if(scope==='property'&&L.study){const u=ui(L.study),t=u.trace;if(!t)return null;const v=allViews(L.study).find(x=>x.id===t.viewId);return v?{s:L.study,t,v,w:v.width,h:v.height,parcels:[],doc:null}:null}
  return null;
 }
 const traceOf=scope=>ctxOf(scope)?.t||null;
 // Every corner the dragged one may snap to (the shape's own, the parcel's) and the parcel's edges.
 // In order, the first within reach wins: the shape's own corners, the building outline's corners
 // then its edges (EVAL_REPORT 104.2), the parcel's corners then its edges.
 function snapTiers(c,except){
  const own=[],t=c.t,b=c.building?[c.building]:[];
  t.shapes.forEach((sh,s)=>{for(const {r,pts} of ringsOf(sh))pts.forEach((p,i)=>{if(!except||except.s!==s||except.r!==r||except.i!==i)own.push(p)})});
  return [{corners:own},{corners:b.flat()},{segments:PM.ringSegments(b)},{corners:(c.parcels||[]).flat()},{segments:PM.ringSegments(c.parcels||[])}];
 }
 function snapped(c,p,except){
  const t=c.t;if(t.snap===false)return {point:rp(p),to:null};
  for(const tier of snapTiers(c,except)){const s=PM.snapPoint(p,{...tier,radius:SNAP_PX*(t.k||1)});if(s.to)return {point:rp(s.point),to:s.to}}
  return {point:rp(p),to:null};
 }
 // The ring problems the server would give (traced.ring_problem through PM.problems), or null.
 function shapeProblem(c){
  const t=c.t;if(!t.shapes.length)return null;
  try{const ps=PM.problems(traceBody(t,c.v,c)).filter(x=>/ring|cutouts|shapes\[\d+\]( |$)/.test(x));return ps[0]?ps[0].replace(/^measurement\.shapes\[(\d+)\]\.(ring|cutouts\[(\d+)\])/,(m,s,what,k)=>`${t.shapes.length>1?`Shape ${Number(s)+1}`:'The shape'}${k!==undefined?`’s cut-out ${Number(k)+1}`:''}`):null}
  catch{return null}
 }
 // Apply an edit and keep it only if it leaves every corner on the picture and does not make a
 // ring cross itself (an edit of a ring that already crosses is let through, so it can be
 // untangled); remember it for Undo. A drag passes the snapshot taken when it started.
 function edit(c,fn,{remembered=null}={}){
  const t=c.t,before=remembered||PM.snapshotOf(t),was=JSON.parse(before),fromBase=t.fromBase;
  const prior=shapeProblem({...c,t:{...t,shapes:was.shapes,draft:was.draft,scale:was.scale||t.scale}});
  fn(t);t.fromBase=false;t.snapAt=null;
  const off=t.shapes.some(sh=>ringsOf(sh).some(({pts})=>pts.some(p=>!PM.onPicture(p,c.w,c.h))));
  const problem=off?'A corner would leave the picture: keep every corner on it.':shapeProblem(c);
  if(off||(problem&&!prior)){t.shapes=was.shapes;t.draft=was.draft;if(was.scale&&t.scale)Object.assign(t.scale,was.scale);t.fromBase=fromBase;t.msg=`${problem.replace(/\.?$/,'.')} The change was undone.`;return false}
  t.hist||={past:[],future:[]};t.hist.past.push(before);if(t.hist.past.length>200)t.hist.past.shift();t.hist.future=[];t.msg=null;return true;
 }

 /* ---------- the dialog: measuring from the Assets list and the asset page ---------- */
 const photoDims=new Map();// document id -> [width, height] of the photo as shown (GET .../image)
 function dimsOf(docId){
  const d=photoDims.get(docId);if(Array.isArray(d))return d;if(d)return null;
  photoDims.set(docId,'loading');
  Live.photoUrl(docId).then(url=>{const im=new Image();im.onload=()=>{photoDims.set(docId,[im.naturalWidth,im.naturalHeight]);Live.render()};im.onerror=()=>photoDims.set(docId,'failed');im.src=url},()=>photoDims.set(docId,'failed'));
  return null;
 }
 Live.measurePhotoDims=photoDims;// tests set the sizes the browser would read
 // The pictures an asset can be measured on: the property's top-down views (outside items, the
 // pin confirmed), then its photos (the evidence's first), each with a known length to scale it.
 function dialogViews(s,a){
  const off=PM.measureOffer(a),out=[];
  if(off.aerial&&shows()){
   const p=propEntry(s).data,pub=pubEntry(s)?.data,cap=p?shownCapture(s,p):null,V=PM.viewsOf(cap);
   if(p?.pin?.confirmed){
    const seen=new Set(),add=(v,label)=>{if(v&&PM.measurable(v)&&!seen.has(v.id)&&!(v.source_id&&PM.publicSeries({history:[v]})[0].coarse)){seen.add(v.id);out.push({key:v.id,kind:'view',view:v,label})}};
    add(PM.defaultMeasureView(cap),'Suggested aerial');add(V.close,'Close aerial');for(const h of V.history)add(h,PM.dateText(PM.dateOf(h)));if(pub?.current)add(pub.current,`Public ${viewDate(pub.current)}`);
   }
  }
  if(off.photo){
   const ev=Live.load(`evidence:${s.id}:${s.revision}:${a.id}`,()=>api.evidence(s.id,a.id)).data?.items?.[0],seen=new Set();
   // Its own photos first: where it was seen (the evidence), then its room's, then the rest.
   const photos=(s.documents||[]).filter(d=>d.kind==='photo'),first=(ev?.photos||[]).map(p=>p.document_id);
   const room=a.room?.id?(Live.roomsEntry?.(s)?.data?.items||[]).find(r=>r.id===a.room.id):null,inRoom=(room?.photos||[]).map(p=>p.document_id);
   const put=(d,group)=>{if(d&&!seen.has(d.id)){seen.add(d.id);out.push({key:'doc:'+d.id,kind:'photo',doc:d,label:d.filename,evidence:group==='evidence',group})}};
   for(const id of first)put(photos.find(p=>p.id===id),'evidence');for(const id of inRoom)put(photos.find(p=>p.id===id),'room');for(const d of photos)put(d,'other');
  }
  return out;
 }
 function openDialog(a,from){
  const s=L.study;L.mtDialog={studyId:s.id,assetId:a.id,from,key:null,trace:null,saved:null,opener:`[data-live="mt-open"][data-value="${a.id}"]`,focus:true};
  if(shows()){propEntry(s);pubEntry(s)}Live.load(`evidence:${s.id}:${s.revision}:${a.id}`,()=>api.evidence(s.id,a.id));
  Live.render();
 }
 Live.openMeasure=openDialog;
 function closeDialog(){const D=L.mtDialog;L.mtDialog=null;delete scopes.dialog;Live.render();if(D?.opener)setTimeout(()=>document.querySelector?.(D.opener)?.focus?.(),0)}
 // The dialog's HTML, appended to the Assets and asset pages while it is open.
 Live.measureDialog=()=>{
  const D=L.mtDialog,s=L.study;if(!D||!s||D.studyId&&D.studyId!==s.id)return '';
  const a=(assetsEntry(s).data?.items||[]).find(x=>x.id===D.assetId);if(!a){return ''}
  const title=a.label?name(a.label):a.category,open=Live.gateOpen('assets_review');
  const head=`<header class="mt-dialog-head"><div><h2 id="mt-dialog-title">Measure ${esc(title)}</h2><p class="pa-muted">${esc(a.room?.name||'No room')} · Basis’s quantity ${esc(`${fmt(a.takeoff?.quantity,2)} ${a.takeoff?.unit||''}`)}</p></div><button class="pa-x" data-live="mt-close" aria-label="Close the measuring">${icon('x',18)}</button></header>`;
  const wrap=body=>`<div class="mt-dialog-backdrop" data-mt-root="dialog"><section class="mt-dialog" role="dialog" aria-modal="true" aria-labelledby="mt-dialog-title" tabindex="-1">${head}${body}</section></div>`;
  if(!open)return wrap(`<p class="mt-dialog-note">The assets review is not open, so a measurement cannot be sent now.</p>`);
  if(narrow())return wrap(`<p class="mt-dialog-note">Measuring needs a larger screen: open this study on a computer or a tablet.</p>`);
  if(D.saved&&!D.trace?.shapes.length)return wrap(savedPanel(s,a,{saved:D.saved},'dialog'));
  const views=dialogViews(s,a),off=PM.measureOffer(a);
  const loading=(off.aerial&&shows()&&(!propEntry(s).done||pubEntry(s)&&!pubEntry(s).done))||!Live.load(`evidence:${s.id}:${s.revision}:${a.id}`,()=>api.evidence(s.id,a.id)).done;
  if(loading)return wrap(Live.loading('Loading the pictures…'));
  if(!views.length)return wrap(loading?Live.loading('Loading the pictures…'):`<p class="mt-dialog-note">There is no picture to measure ${esc(title)} on: ${off.aerial&&!propEntry(s).data?.pin?.confirmed?'confirm the house on the Property page first, or ':''}add a photo of it to the study.</p>`);
  // The stored measurement opens on its own picture; otherwise the aerial for an outside item, its photo for an inside one.
  const want=D.key||(()=>{const st=off.stored,vid=st?.view?.kind==='document'||st?.space==='image'?'doc:'+(st?.image?.document_id||st?.view?.id):st?.view?.id;return views.find(x=>x.key===vid||(!st?.view?.id&&st?.view?.label&&x.view?.label===st.view.label))?.key||views[0].key})();
  const pick=views.find(x=>x.key===want)||views[0];// D.key is the engineer's choice only (mt-view)
  let w,h,v,doc=null,imgHtml;
  if(pick.kind==='view'){v=pick.view;w=v.width;h=v.height;imgHtml=diagramOn(v)?null:img(s,v,v.label||'Property picture','pa-photo')}
  else{doc=pick.doc;const d=dimsOf(doc.id);if(!d)return wrap(`${chips(views,pick)}${Live.loading('Loading the photo…')}`);[w,h]=d;v={id:'doc:'+doc.id,kind:'document',width:w,height:h};imgHtml=Live.photo(doc.id,doc.filename,'pa-photo')}
  const space=pick.kind==='view'?'lonlat':'image';
  if(!D.trace||D.trace.assetId!==a.id)D.trace=newTrace(a,v,{space,doc,w,h});
  else if(D.trace.viewId!==v.id){
   const t=D.trace;if(t.space==='lonlat'&&space==='lonlat'){const from=views.find(x=>x.key===t.viewId)?.view;if(from){const g1=PM.georef(from),g2=PM.georef(v),mv=p=>rp(g2.toPixel(...g1.toLonLat(p[0],p[1])));t.shapes=t.shapes.map(sh=>sh.kind==='line'?{kind:'line',path:sh.path.map(mv)}:{kind:'area',ring:sh.ring.map(mv),...(sh.cutouts?{cutouts:sh.cutouts.map(c=>c.map(mv))}:{})});t.draft=t.draft.map(mv)}}
   else{const keep=t.shapes.length||t.draft.length;D.trace=newTrace(a,v,{space,doc,w,h});if(keep&&!(D.trace.shapes.length))toast('The tracing was cleared: a shape belongs to the picture it was drawn on.')}
   D.trace.viewId=v.id;D.trace.k=null;D.trace.sel=null;D.trace.edit=null;originalPixels(D.trace,v,w,h);
  }
  const t=D.trace,g=PM.georef(v),pub=pubEntry(s)?.data;
  const parcels=space==='lonlat'&&g&&pub?.parcels?.length?pub.parcels.flatMap(p=>PM.parcelRings(p,g)):[];
  scopes.dialog={s,a,t,v,w,h,doc,parcels,building:space==='lonlat'?PM.buildingOnView(pub?.lidar,v)?.points||null:null};
  if(imgHtml===null)imgHtml=diagramFor('dialog',s,v,{except:a.id,trace:t});// its labels make room for the trace's
  const extra=diagramOn(v)?'':parcels.map(r=>`<polygon class="pa-parcel" points="${r.map(p=>p.map(n=>n.toFixed(1)).join(',')).join(' ')}"/>`).join('');
  const on=pick.kind==='view'?(v.source_id?`${v.credit}, ${viewDate(v)}`:`Picture of ${PM.dateText(PM.dateOf(v))}`)+` · ${fmt(v.pixel_size_m*100,1)} cm a pixel`:`${doc.filename}${pick.evidence?' · where it was seen':''} · scaled by the known length you set`;
  // A small shape opens zoomed in on it (a patio on a 120 m wide aerial is a few dozen pixels).
  if(!D.zoom){const box=shapesBox(t);D.zoom=box&&box.w<w/8&&box.h<h/8?4:box&&box.w<w/4&&box.h<h/4?2:1;D.centre=D.zoom>1}
  const zoom=D.zoom;
  return wrap(`<div class="mt-dialog-body"><div class="mt-dialog-main">${chips(views,pick)}<div class="pa-stage is-measure mt-stage"><div class="mt-stage-view"><div class="pa-zoom" tabindex="0" aria-label="Picture to measure on; scroll to pan when zoomed">${toolCanvas('dialog',t,v,{w,h,imgHtml,extraSvg:extra,label:`Measuring ${title} on ${on}`,zoom})}</div>${diagramOn(v)?furnitureHtml('dialog'):''}</div><div class="pa-stage-tools"><div class="pa-zoom-tools" role="group" aria-label="Zoom"><button class="pa-icon-btn" data-live="mt-zoom" data-value="out" aria-label="Zoom out" ${zoom<=1?'disabled':''}>−</button><span>${zoom*100}%</span><button class="pa-icon-btn" data-live="mt-zoom" data-value="in" aria-label="Zoom in" ${zoom>=8?'disabled':''}>+</button></div>${pictureToggle(v)}<p class="pa-caption">${esc(on)}</p></div></div></div><aside class="mt-dialog-side" data-mt-root="dialog">${toolPanel('dialog',s,a,t,v,{doc,w,h})}</aside></div>`);
 };
 // The picture picker: the top-down views as chips; the asset's own photos (where it was seen,
 // then its room's) as a few thumbnails; every other photo behind one "All photos" list.
 const MAX_THUMBS=8;
 function chips(views,pick){
  const aerial=views.filter(x=>x.kind==='view'),photos=views.filter(x=>x.kind==='photo');
  const own=photos.filter(x=>x.group!=='other').slice(0,MAX_THUMBS);if(pick.kind==='photo'&&!own.includes(pick))own.push(pick);
  const thumb=x=>`<button class="mt-thumb" data-live="mt-view" data-value="${esc(x.key)}" aria-pressed="${x.key===pick.key}" title="${esc(x.label)}${x.evidence?' · where it was seen':x.group==='room'?' · its room':''}">${Live.photo(x.doc.id,'','mt-thumb-img')}<span>${esc(x.label)}</span></button>`;
  const all=photos.length>own.length?`<label class="mt-all">All photos (${photos.length}) <select data-mt-pick aria-label="Choose any of the study’s ${photos.length} photos"><option value="">Choose a photo…</option>${photos.map(x=>`<option value="${esc(x.key)}" ${x.key===pick.key?'selected':''}>${esc(x.label)}${x.evidence?' (where it was seen)':x.group==='room'?' (its room)':''}</option>`).join('')}</select></label>`:'';
  return `<nav class="mt-views" aria-label="Pictures to measure on">${aerial.length?`<div class="mt-aerials">${aerial.map(x=>`<button class="pa-chip" data-live="mt-view" data-value="${esc(x.key)}" aria-pressed="${x.key===pick.key}">${esc(x.label)}</button>`).join('')}</div>`:''}${photos.length?`<div class="mt-photo-row"><span class="mt-views-label">Photos</span>${own.map(thumb).join('')}${all}</div>`:''}</nav>`;
 }
 // A Measure button for an asset (the Assets list, the asset page).
 Live.measureButton=(s,a,{primary=false,label=null}={})=>{
  if(!caps().tracedMeasurement||!Live.gateOpen('assets_review'))return '';
  const off=PM.measureOffer(a);if(!off.any)return '';
  const pending=!!Live.pendingCorrection?.('assets_review',a.id)?.after?.measurement;
  return `<button class="button small ${primary&&!pending?'primary':''}" data-live="mt-open" data-value="${esc(a.id)}" title="Measure it on ${off.aerial?'a top-down picture or ':''}a photo">${esc(label||(off.stored||pending?'Edit measurement':'Measure'))}</button>`;
 };

 /* ---------- Photo | Diagram (EVAL_REPORT 104.1) ----------
  * On a georeferenced top-down view the picture can be swapped for a clean plan drawn in the same
  * pixels (live/property-model.js diagramModel): the overlay (the tool, its labels and handles, the
  * pin) stays exactly where it was. The viewer's choice is kept in this browser (localStorage,
  * wrapped: blocked storage keeps it for the session only); Photo by default. Photos of the study
  * have no georeference and no toggle. */
 const PICTURE_KEY='basis.measure.picture';let pictureMode=null;
 function pictureModeOf(){if(pictureMode)return pictureMode;let v=null;try{v=globalThis.localStorage?.getItem(PICTURE_KEY)}catch{}pictureMode=v==='diagram'?'diagram':'photo';return pictureMode}
 function setPictureMode(m){pictureMode=m==='diagram'?'diagram':'photo';try{globalThis.localStorage?.setItem(PICTURE_KEY,pictureMode)}catch{}}
 Live.measurePicture={get:pictureModeOf,set:setPictureMode,reset(){pictureMode=null}};
 const georeferenced=v=>!!v&&v.kind!=='document'&&!!PM.georef(v);
 const diagramOn=v=>georeferenced(v)&&pictureModeOf()==='diagram';
 const pictureToggle=v=>georeferenced(v)?`<div class="pa-seg mt-picture-toggle" role="group" aria-label="Show the picture or a diagram">${[['photo','Photo'],['diagram','Diagram']].map(([k,l])=>`<button data-live="mt-picture" data-value="${k}" aria-pressed="${pictureModeOf()===k}">${l}</button>`).join('')}</div>`:'';
 const diagrams={};// scope -> {model, frame, k} as last drawn
 function diagramFor(scope,s,v,{except=null,pin=null,trace=null}={}){
  const pub=pubEntry(s)?.data,items=assetsEntry(s).data?.items||[],pending=L.corrections?.assets_review||null;
  const model=PM.diagramModel({view:v,parcels:pub?.parcels||[],measured:PM.measuredOnView(items,v,{pending,except}),editing:!!except,building:PM.buildingOnView(pub?.lidar,v)});
  diagrams[scope]={model,view:v,frame:PM.frameOf(v),pin,trace,k:diagrams[scope]?.model?.w===model.w?diagrams[scope].k:null};
  const body=diagramBody(diagrams[scope]),m=model,what=[m.lots.length?`${m.lots.length} lot ${m.lots.length===1?'line':'lines'}`:'no lot line',m.legend.length?`measured: ${m.legend.map(x=>`${x.name} ${x.quantity}`).join(', ')}`:'nothing measured'].join('; ');
  return `<svg class="mt-diagram" viewBox="0 0 ${m.w} ${m.h}" preserveAspectRatio="none" data-mt-diagram="${scope}" role="img" aria-label="${esc(`Diagram of the property: ${what}. North is up; scale bar ${m.scale?.label||'unknown'}.${droppedText(diagrams[scope])}`)}">${body}</svg>`;
 }
 // No label meets another or the pin (which the overlay draws over the diagram). Labels are placed
 // in a fixed order, each against every one already placed: the active trace's (drawn by the tool,
 // never moved or hidden), then each measured item's edge lengths and name, then the building's.
 // An edge length that fits nowhere (PM.placeEdgeLabel) is left out of the drawing and named in the
 // diagram's title; a name goes where PM.placeName finds room.
 function traceBoxes(t,v,k){
  const frame=t?frameFor(t,v):null,font=MT_FONT*k,out=[];if(!frame)return out;
  const edge=(p,closed)=>{for(const l of PM.edgeLabels(p,closed,PM.edgeLengths(p,closed,frame),{k,font:MT_FONT}))if(l.mode!=='hidden'){const tb=PM.turnedBox(labelBox(l.text,k),l.angle);out.push({at:[l.x,l.y],w:tb.w,h:tb.h})}};
  for(const sh of t.shapes||[]){for(const {pts,closed} of ringsOf(sh))edge(pts,closed);
   const sum=PM.shapeSummary(sh,frame,{unit:t.unit,heightFt:Number(heightOf(t))||null});
   if(sh.kind==='area'){const [x,y]=PM.labelPoint(sh.ring),w=Math.max(sum.area.length*1.2,sum.perimeter.length*0.95)*0.62*font+8*k;out.push({at:[x,y+font*0.4],w,h:font*2.7})}
   else if(sh.path.length>2||Number(heightOf(t))>0){const q=sh.path[sh.path.length-1],w=sum.text.length*0.62*font*1.05+8*k;out.push({at:[q[0]+10*k+w/2,q[1]-10*k],w,h:font*1.5})}}
  if(t.draft?.length>1)edge(t.draft,false);
  return out;
 }
 const droppedText=d=>d?.dropped?.length?` Not drawn for lack of room: ${d.dropped.join(', ')} (every length is in the edge list).`:'';
 function diagramBody(d){
  const {model:m,frame,pin=null,trace=null,view=null}=d,k=d.k||m.w/900;
  const font=MT_FONT*k,P=pts=>pts.map(q=>q.map(n=>n.toFixed(1)).join(',')).join(' '),out=[],labels=[],dropped=[];
  const taken=[...(trace&&view?traceBoxes(trace,view,k):[]),...(d.obstacles||[])];
  const edgeLabels=(points,closed,cls,colour,what)=>{if(!frame)return;for(const l of PM.edgeLabels(points,closed,PM.edgeLengths(points,closed,frame),{k,font:MT_FONT})){if(l.mode==='hidden')continue;
   const b=labelBox(l.text,k),spot=PM.placeEdgeLabel(l,points,closed,b,{pin,boxes:taken});
   if(!spot){dropped.push(`${what} ${l.full}`);continue}
   taken.push(spot);labels.push(`<g class="mt-d-label${cls}" transform="translate(${f1(spot.at[0])} ${f1(spot.at[1])}) rotate(${f1(l.angle)})"${colour?` style="--c:${colour}"`:''}><title>${esc(`${what} · ${l.full}`)}</title><rect x="${f1(-b.w/2)}" y="${f1(-b.h/2)}" width="${f1(b.w)}" height="${f1(b.h)}" rx="${f1(4*k)}"/><text y="${f1(0.36*font)}" font-size="${f1(font)}">${esc(l.text)}</text></g>`)}};
  // A name of two lines: the first's baseline at the anchor, the box's middle 0.35 em below it.
  const nameBox=(a,b,fa,fb)=>({w:Math.max(a.length*0.62*fa,b.length*0.62*fb)+8*k,h:font*2.7});
  const name=(n)=>{const box=nameBox(n.lines[0],n.lines[1],n.sizes[0],n.sizes[1]);
   const start=n.start?[n.start[0]+box.w/2,n.start[1]]:(()=>{const q=PM.labelPoint(n.points);return [q[0],q[1]+font*0.35]})();
   const c=PM.placeName(n.points,box,{pin,boxes:taken,start,within:n.within});taken.push({at:c,w:box.w,h:box.h});
   const ax=n.line?c[0]-box.w/2:c[0],ay=c[1]-font*0.35;
   labels.push(`<g class="mt-d-total${n.cls}" transform="translate(${f1(ax)} ${f1(ay)})"${n.colour?` style="--c:${n.colour}"`:''} data-box="${f1(box.w)} ${f1(box.h)}"><text font-size="${f1(n.sizes[0])}">${esc(n.lines[0])}</text><text y="${f1(n.gap2)}" font-size="${f1(n.sizes[1])}">${esc(n.lines[1])}</text></g>`)};
  out.push(`<rect class="mt-d-ground" x="0" y="0" width="${m.w}" height="${m.h}" fill="${m.ground}"/>`);
  for(const lot of m.lots)out.push(`<polygon class="mt-d-lot${lot.adjacent?' is-adjacent':''}" points="${P(lot.points)}"/>`);
  if(m.building)out.push(`<polygon class="mt-d-building" points="${P(m.building.points)}"/>`);
  // Measured items: their shapes, then (in order) their lengths and their names.
  for(const sh of m.shapes)for(const one of sh.shapes){
   const rings=one.kind==='line'?[{p:one.path,closed:false}]:[{p:one.ring,closed:true},...(one.cutouts||[]).map(c=>({p:c,closed:true,cut:true}))];
   for(const r of rings)out.push(r.closed?`<polygon class="mt-d-shape${r.cut?' is-cut':''}" points="${P(r.p)}" style="--c:${sh.colour}"/>`:`<polyline class="mt-d-shape is-line" points="${P(r.p)}" style="--c:${sh.colour}"/>`);
  }
  for(const sh of m.shapes)for(const one of sh.shapes){
   const rings=one.kind==='line'?[{p:one.path,closed:false}]:[{p:one.ring,closed:true},...(one.cutouts||[]).map(c=>({p:c,closed:true}))];
   for(const r of rings)edgeLabels(r.p,r.closed,'',sh.colour,typeName(sh.label));
   if(frame){const sum=PM.shapeSummary(one,frame,{unit:sh.unit,heightFt:sh.heightFt}),line=one.kind==='line',end=line?one.path[one.path.length-1]:null;
    name({points:line?one.path:one.ring,within:!line,start:line?[end[0]+10*k,end[1]-10*k+font*0.35]:null,lines:[typeName(sh.label),line?sum.text:sum.area],sizes:[font*1.1,font*0.95],cls:line?' is-line':'',colour:sh.colour,gap2:font*1.3,line})}
  }
  // The building: the lidar's roof outline (overhangs included), its lengths, its name.
  if(m.building){const bp=m.building.points;edgeLabels(bp,true,' is-building',null,'Building');
   name({points:bp,within:true,start:null,lines:['Building','roof outline'],sizes:[font,font*0.85],cls:' is-building',colour:null,gap2:font*1.25})}
  d.dropped=dropped;
  return `${dropped.length?`<title>${esc(`Diagram.${droppedText(d)}`)}</title>`:''}${out.join('')}${labels.join('')}`;
 }
 // Tests reach the layout with a model and a view of their own.
 Live.diagramBodyFor=(model,view,{k=null,pin=null,trace=null,obstacles=[]}={})=>{const d={model,view,frame:PM.frameOf(view),k,pin,trace,obstacles};const html=diagramBody(d);return {html,dropped:d.dropped}};
 const typeName=l=>String(l||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());
 // The diagram's key, north arrow, scale bar and source sit in the stage's corners (HTML over the
 // zoomed picture), so they stay on screen when it is zoomed and panned. The scale bar is chosen
 // for the screen: a round length no wider than SCALE_MAX_PX at the picture's pixel size times k
 // (picture pixels per screen pixel), so it stays true at every zoom.
 const SCALE_MAX_PX=140;
 function furnitureHtml(scope){
  const d=diagrams[scope];if(!d)return '';const m=d.model,k=d.k||m.w/900;
  const bar=PM.scaleBar(m.pixelSizeM*k,{maxPx:SCALE_MAX_PX});
  const rows=[...(m.building?[`<li><span class="mt-f-swatch is-building"></span>${esc(m.building.label)}${m.building.footprintSqft?` · ${fmt(m.building.footprintSqft)} SF`:''}</li>`]:[]),...m.legend.map(x=>`<li><span class="mt-f-swatch" style="--c:${x.colour}"></span>${esc(x.name)}${x.count>1?` (${x.count})`:''} · ${esc(x.quantity)}</li>`)];
  return `<div class="mt-furniture" data-mt-furniture="${scope}">${rows.length||m.note?`<div class="mt-f-key">${rows.length?`<strong>Key</strong><ul>${rows.join('')}</ul>`:''}${m.note?`<p>${esc(m.note)}</p>`:''}</div>`:''}<div class="mt-f-north" aria-label="North is up" role="img"><svg viewBox="-12 -24 24 36" width="22" height="33" aria-hidden="true"><g transform="rotate(${m.north.angle})"><path d="M0 -18 L8 10 L0 4 L-8 10 Z"/></g><text y="-19" font-size="9">N</text></svg></div>${bar?`<div class="mt-f-scale" data-scale-px="${bar.px}" data-scale-ft="${bar.ft}" data-k="${k}"><span>${esc(bar.label)}</span><i style="width:${bar.px.toFixed(2)}px"></i></div>`:''}<div class="mt-f-credit">Diagram · ${esc(m.credit)}</div></div>`;
 }
 Live.diagramFurniture=furnitureHtml;

 /* ---------- the stored measurement, read-only, on the asset page ---------- */
 const cards=new Map();// asset id -> the read-only trace drawn on the asset page
 Live.measurementCard=(s,a)=>{
  const off=PM.measureOffer(a);if(!off.stored)return '';
  const rec=off.stored,body=PM.bodyOfRecord(rec);let v=null,w,h,imgHtml='',where='';
  if(body.space==='lonlat'){
   if(shows()){const p=propEntry(s).data,pub=pubEntry(s)?.data,cap=p?shownCapture(s,p):null,all=[...(cap?.views||[]),...(pub?PM.publicSeries(pub).map(x=>x.view):[])];
    const own=x=>rec.view?.id?x.id===rec.view.id:!!rec.view?.label&&x.label===rec.view.label;
    v=all.find(own)||PM.defaultMeasureView(cap)||all.find(PM.measurable)||null;if(v)v.__own=own(v)}
   if(v){w=v.width;h=v.height;imgHtml=img(s,v,'Where it was measured','pa-photo');where=v.__own?'on the picture it was drawn on':'on today’s aerial (its own picture is not in this capture)'}
  }else{const id=rec.image?.document_id,d=id?dimsOf(id):null;if(d){[w,h]=d;v={id:'doc:'+id,width:w,height:h};imgHtml=Live.photo(id,'Where it was measured','pa-photo');where='on its photo'}}
  const facts=[[ 'Drawn',PM.drawnByText(rec.drawn_by)],['Verified',rec.verified===true?'Yes':rec.verified===false?'Not yet':'Not recorded'],['Expected error',rec.expected_error_pct==null?'Unknown':`up to ${rec.expected_error_pct} %`],['Picture',rec.image?.captured_on?PM.dateText(rec.image.captured_on):'Undated']];
  let stage='';
  if(v&&w){const px=PM.pixelShapes(body,v,{width:w,height:h});if(px){const t={shapes:px.shapes,draft:[],tool:'area',unit:body.unit,space:body.space,height:rec.height_ft!=null?String(rec.height_ft):'',scale:px.scale?{points:px.scale.points,length:body.scale.unit==='m'?body.scale.length*PM.FT_PER_M:body.scale.length,unit:'ft'}:null,k:cards.get(a.id)?.k||null,base:null};cards.set(a.id,{t,v});
   stage=`<div class="pa-stage mt-mini"><div class="pa-canvas" style="aspect-ratio:${w}/${h}">${imgHtml}<svg class="pa-overlay" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" data-mt-card="${esc(a.id)}" role="img" aria-label="${esc(`The measured shape with its edge lengths, ${where}`)}">${toolLayer(t,v,{readOnly:true})}</svg></div><p class="pa-caption">${esc(where)}</p></div>`}}
  const orig=rec.original?`<p class="pa-muted">Replaces ${fmt(rec.original.quantity,2)} ${esc(rec.original.unit)} ${rec.original.drawn_by&&rec.original.drawn_by!=='engineer'?'drafted by Basis':'traced earlier'}.</p>`:'';
  return `<section class="mt-card" aria-labelledby="mt-card-title"><h3 id="mt-card-title">Measured: ${fmt(rec.quantity,2)} ${esc(rec.unit)}</h3><p class="pa-muted">${esc(a.takeoff?.formula||'')}</p><dl class="pa-dl">${facts.map(([k,x])=>`<div><dt>${k}</dt><dd>${esc(x)}</dd></div>`).join('')}</dl>${stage||`<p class="pa-muted">${body.space==='lonlat'&&!shows()?'The property’s pictures are not available on this server to draw it on.':'Loading the picture it was drawn on…'}</p>`}${orig}</section>`;
 };

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
  if(L.docId){const ae=assetsEntry(s);if(ae.done&&ae.data)Live.ensureAssetsDraft?.(s,ae.data.items);if(!u.trace||u.trace.assetId!==L.docId){u.trace=null;if(u.measuring!==L.docId){u.view=null;u.saved=null}}u.measuring=L.docId}else u.measuring=null;
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
 // Zoom keeps the picture point at the middle of the view in the middle (it used to keep the
 // scroll in pixels, so the view slid to the top left).
 function paneGeometry(pane){const canvas=pane?.querySelector?.('.pa-canvas');if(!canvas)return null;const pr=pane.getBoundingClientRect(),cr=canvas.getBoundingClientRect();
  return {scrollLeft:pane.scrollLeft,scrollTop:pane.scrollTop,clientWidth:pane.clientWidth,clientHeight:pane.clientHeight,canvasLeft:cr.left-pr.left-(pane.clientLeft||0)+pane.scrollLeft,canvasTop:cr.top-pr.top-(pane.clientTop||0)+pane.scrollTop,canvasWidth:cr.width,canvasHeight:cr.height,scrollWidth:pane.scrollWidth,scrollHeight:pane.scrollHeight}}
 const centreOf=pane=>{const g=paneGeometry(pane);return g&&g.canvasWidth&&g.canvasHeight?PM.viewCentre(g):null};
 function centreOn(pane,f){const g=paneGeometry(pane);if(!g||!f)return;const s=PM.scrollToCentre(f,g);pane.scrollLeft=s.scrollLeft;pane.scrollTop=s.scrollTop}
 A['pa-zoom']=(el,dir)=>{const u=ui(L.study),pane=document.querySelector?.('.pa-main .pa-stage .pa-zoom');u.keep=centreOf(pane);u.zoom=Math.max(1,Math.min(4,dir==='in'?u.zoom*2:u.zoom/2));Live.render()};
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
 // Measuring (both scopes; the pa- names are the Property page's, kept).
 const mtScope=el=>scopeOf(el);
 const announce=message=>{const sr=document.getElementById?.('sr-status');if(!sr)return;sr.textContent='';setTimeout(()=>{sr.textContent=message},60)};
 A['pa-tool']=(el,k)=>{const t=traceOf(mtScope(el));if(!t)return;t.tool=k;t.draft=[];t.sel=null;t.edit=null;Live.render()};
 A['pa-trace-undo']=el=>{const t=traceOf(mtScope(el));if(!t)return;t.edit=null;t.msg=null;
  if(!PM.undoTrace(t)){if(t.draft.length)t.draft.pop();else{const last=t.shapes[t.shapes.length-1];if(last?.kind==='area'&&last.cutouts?.length)last.cutouts.pop();else t.shapes.pop()}}
  t.fromBase=t.fromBase&&!t.hist.past.length;Live.render()};
 A['mt-redo']=el=>{const t=traceOf(mtScope(el));if(t&&PM.redoTrace(t)){t.edit=null;t.msg=null;Live.render()}};
 A['pa-trace-clear']=el=>{const t=traceOf(mtScope(el));if(!t)return;PM.remember(t);t.shapes=[];t.draft=[];t.sel=null;t.edit=null;t.fromBase=false;Live.render()};
 A['pa-trace-finish']=el=>{const t=traceOf(mtScope(el));if(t&&t.tool==='line'&&t.draft.length>=2){PM.remember(t);t.shapes.push({kind:'line',path:t.draft});t.draft=[];t.fromBase=false;Live.render()}};
 A['pa-trace-again']=el=>{const scope=mtScope(el);if(scope==='dialog'&&L.mtDialog){L.mtDialog.saved=null;L.mtDialog.trace=null}else ui(L.study).saved=null;Live.render()};
 A['pa-trace-save']=el=>saveTrace(mtScope(el));
 function deleteCorner(scope){
  const c=ctxOf(scope),t=c?.t,sel=t?.sel;if(!sel||sel.scale)return;
  const p=ptsAt(t,sel.s,sel.r),closed=t.shapes[sel.s].kind!=='line'||sel.r!=='o';
  if(p.length<=PM.minPoints(closed)){t.msg=closed?'A shape needs at least 3 corners: Undo or Clear it instead.':'A line needs at least 2 points: Undo or Clear it instead.';Live.render();return}
  if(edit(c,tt=>{setPts(tt,sel.s,sel.r,PM.removePoint(p,sel.i));tt.sel=null}))announce(`Corner ${sel.i+1} deleted.`);
  Live.render();
 }
 A['mt-delete']=el=>deleteCorner(mtScope(el));
 function openEdit(scope,ref){const c=ctxOf(scope);if(!c||!frameFor(c.t,c.v))return;c.t.edit={...ref,value:null,error:null};c.t.sel=null;c.t.focusEdit=true;Live.render()}
 A['mt-edge-edit']=(el,v)=>openEdit(mtScope(el),parseRef(v));
 A['mt-known']=(el,v)=>{const c=ctxOf(mtScope(el));if(!c)return;setScale(c.t,v);Live.render()};
 A['mt-open']=(el,id)=>{const a=(assetsEntry(L.study).data?.items||[]).find(x=>x.id===id);if(a)openDialog(a,Live.screen())};
 A['mt-close']=()=>closeDialog();
 A['mt-picture']=(el,m)=>{setPictureMode(m);Live.render()};
 A['mt-view']=(el,key)=>{if(!L.mtDialog)return;L.mtDialog.key=key;L.mtDialog.zoom=null;L.mtDialog.scroll=null;Live.render()};
 // Zooming keeps the middle of what is on screen in the middle.
 A['mt-zoom']=(el,dir)=>{const D=L.mtDialog;if(!D)return;const c=centreOf(document.querySelector?.('.mt-stage .pa-zoom'));
  D.zoom=Math.max(1,Math.min(8,dir==='in'?(D.zoom||1)*2:(D.zoom||1)/2));D.keep=c;if(D.trace)D.trace.k=null;Live.render()};
 function setScale(t,text){
  if(!t.scale)return;const p=PM.parseLength(text);t.scale.text=text;
  if(p.error){t.scaleError=p.error;return}
  PM.remember(t);t.scale.length=p.ft;t.scaleError=null;t.fromBase=false;
  if(t.scale.points.length===2&&t.tool==='scale')t.tool=t.unit==='LF'?'line':'area';
 }
 function saveTrace(scope){
  const c=ctxOf(scope);if(!c||!Live.gateOpen('assets_review'))return;
  const s=L.study,t=c.t,v=c.v,a=(assetsEntry(s).data?.items||[]).find(x=>x.id===t.assetId);if(!v||!a)return;
  let body,r;try{body=finalBody(t,v,c);r=PM.measure(body)}catch(e){toast((e.problems||[e.message])[0]);return}
  const cur=Live.pendingCorrection?.('assets_review',a.id),after={...(cur?.after||{})};for(const k of ['quantity','quantity_unit','quantity_source'])delete after[k];after.measurement=body;
  const formula=PM.formula(r,{source:body.image.source,captured_on:body.image.captured_on,height_ft:body.height_ft,drawn_by:body.drawn_by,verified:body.verified});
  const on=t.space==='image'?`photo ${c.doc?.filename||''}`:v.source_id?viewDate(v):PM.dateText(PM.dateOf(v));
  Live.addCorrection('assets_review',{key:a.id,entity_type:'asset',entity_id:a.id,before:{...(cur?.before||{}),quantity:a.takeoff?.quantity??null,quantity_unit:a.takeoff?.unit??null},after,reason:cur?.reason??null,title:a.label?name(a.label):a.category,summary:`${t.space==='image'?'measured':'traced'} ${r.quantity} ${r.unit} on ${on}${body.verified?', verified':''}`});
  const saved={assetId:a.id,viewId:v.id,shapes:t.shapes,quantity:r.quantity,unit:r.unit,formula,flags:PM.reviewFlags(r,{captured_on:body.image.captured_on,placedInService:s.intake?.placed_in_service_date})};
  if(scope==='dialog'&&L.mtDialog){L.mtDialog.saved=saved;L.mtDialog.trace={...t,shapes:[],draft:[]}}else ui(s).saved=saved;
  t.shapes=[];t.draft=[];t.sel=null;t.edit=null;Live.render();
 }

 /* ---------- pointer and keys on the stage ---------- */
 const HIT_PX=12;
 function pixelOf(svg,ev){const r=svg.getBoundingClientRect(),vb=svg.viewBox.baseVal;return [(ev.clientX-r.left)/r.width*vb.width,(ev.clientY-r.top)/r.height*vb.height,vb.width/r.width]}
 // Repaint the overlay and the readout while dragging (a full render would drop the pointer).
 function paint(scope){
  const c=ctxOf(scope);if(!c||typeof document.querySelector!=='function')return;
  const layer=document.querySelector(`svg[data-mt-scope="${scope}"] [data-mt-layer]`);if(layer)layer.outerHTML=toolLayer(c.t,c.v);
  const ro=document.querySelector(`[data-mt-readout="${scope}"]`);if(ro)ro.innerHTML=readoutHtml(c.t,c.v,c).html;
 }
 const measuring=scope=>scope==='dialog'?!!L.mtDialog:Live.screen()==='property'&&!!L.study&&ui(L.study).mode==='measure';
 let drag=null,suppressClick=false;
 document.addEventListener('pointerdown',ev=>{
  const svg=ev.target.closest?.('svg[data-mt-scope]');if(!svg)return;const scope=svg.dataset.mtScope;if(!measuring(scope)||narrow())return;
  const c=ctxOf(scope);if(!c)return;const t=c.t,vx=ev.target.closest('[data-mt-vertex]'),mid=ev.target.closest('[data-mt-mid]'),sc=ev.target.closest('[data-mt-scale]');
  if(!vx&&!mid&&!sc)return;
  ev.preventDefault();const before=PM.snapshotOf(t);t.edit=null;
  if(vx){const ref=parseRef(vx.dataset.mtVertex);t.sel=ref;drag={scope,svg,ref,before,moved:false,id:ev.pointerId}}
  else if(mid){const ref=parseRef(mid.dataset.mtMid),p=ptsAt(t,ref.s,ref.r),a=p[ref.i],b=p[(ref.i+1)%p.length];setPts(t,ref.s,ref.r,PM.insertPoint(p,ref.i,rp([(a[0]+b[0])/2,(a[1]+b[1])/2])));const nref={s:ref.s,r:ref.r,i:ref.i+1};t.sel=nref;drag={scope,svg,ref:nref,before,moved:true,id:ev.pointerId}}
  else{const i=Number(sc.dataset.mtScale);t.sel={scale:true,i};drag={scope,svg,scaleIndex:i,before,moved:false,id:ev.pointerId}}
  try{svg.setPointerCapture(ev.pointerId)}catch{}
  paint(scope);
 });
 document.addEventListener('pointermove',ev=>{
  if(!drag||ev.pointerId!==drag.id)return;const c=ctxOf(drag.scope);if(!c)return;const t=c.t;
  const [x,y]=pixelOf(drag.svg,ev),q=[Math.max(0,Math.min(c.w,x)),Math.max(0,Math.min(c.h,y))];
  if(drag.scaleIndex!==undefined)t.scale.points[drag.scaleIndex]=rp(q);
  else{const s=snapped(c,q,drag.ref),p=ptsAt(t,drag.ref.s,drag.ref.r);setPts(t,drag.ref.s,drag.ref.r,PM.movePoint(p,drag.ref.i,s.point));t.snapAt=s.to?s.point:null}
  drag.moved=true;paint(drag.scope);
 });
 function endDrag(ev){
  if(!drag||ev.pointerId!==drag.id)return;const d=drag;drag=null;const c=ctxOf(d.scope);if(!c)return;
  try{d.svg.releasePointerCapture(ev.pointerId)}catch{}
  if(d.moved){suppressClick=true;setTimeout(()=>{suppressClick=false},50);edit(c,()=>{},{remembered:d.before})}
  c.t.snapAt=null;setTimeout(()=>Live.render(),0);// after the click this pointer makes
 }
 document.addEventListener('pointerup',endDrag);document.addEventListener('pointercancel',endDrag);
 // A click adds a corner (snapped like a drag), closes a ring on its first corner, finishes a line
 // on its last point, sets the scale's ends, opens a label's length, or clears a selection.
 function addPoint(c,raw,k){
  const t=c.t,near=p=>Math.hypot(p[0]-raw[0],p[1]-raw[1])<=HIT_PX*k;
  if(t.tool==='scale'){PM.remember(t);if(t.scale.points.length>=2)t.scale.points=[];t.scale.points.push(rp(raw));t.fromBase=false;if(t.scale.points.length===2&&Number(t.scale.length)>0)t.tool=t.unit==='LF'?'line':'area';return}
  const p=snapped(c,raw,null).point;
  if(t.tool==='line'){PM.remember(t);if(t.draft.length>=2&&near(t.draft[t.draft.length-1])){t.shapes.push({kind:'line',path:t.draft});t.draft=[];t.fromBase=false}else t.draft.push(p)}
  else if(t.draft.length>=3&&near(t.draft[0])){
   PM.remember(t);if(t.tool==='cutout'){const i=lastArea(t);if(i>=0)(t.shapes[i].cutouts||=[]).push(t.draft)}else t.shapes.push({kind:'area',ring:t.draft});
   t.draft=[];t.fromBase=false;if(t.tool==='cutout')t.tool='area';
  }else{PM.remember(t);t.draft.push(p)}
 }
 document.addEventListener('click',ev=>{
  const svg=ev.target.closest?.('svg[data-pa-stage]');if(!svg)return;
  const scope=svg.dataset.mtScope||'property';if(scope==='property'&&Live.screen()!=='property')return;
  if(suppressClick){suppressClick=false;return}
  const [x,y,k]=pixelOf(svg,ev);
  if(scope==='property'){
   const s=L.study,u=ui(s),v=allViews(s).find(x=>x.id===svg.dataset.paStage);if(!v)return;
   if(u.mode==='pin'){const g=PM.georef(v);if(!g)return;u.candidate=g.toLonLat(x,y);Live.render();return}
   if(u.mode!=='measure'||!u.trace||narrow())return;
  }
  const c=ctxOf(scope);if(!c)return;const t=c.t;
  if(ev.target.closest('[data-mt-vertex],[data-mt-mid],[data-mt-scale]'))return;
  const lab=ev.target.closest('[data-mt-edge]');if(lab){openEdit(scope,parseRef(lab.dataset.mtEdge));return}
  t.edit=null;t.msg=null;
  if(t.sel&&!t.draft.length){t.sel=null;Live.render();return}
  if(t.space==='image'&&t.tool!=='scale'&&!frameFor(t,c.v)){t.msg='Set the scale first: choose Scale, click both ends of something whose length you know, and type it.';Live.render();return}
  addPoint(c,[x,y],k);Live.render();
 });
 function keyMove(c,dx,dy){const t=c.t,sel=t.sel,k=t.k||1;if(!sel)return;
  if(sel.scale){PM.remember(t);const p=t.scale.points[sel.i];t.scale.points[sel.i]=rp([Math.max(0,Math.min(c.w,p[0]+dx*k)),Math.max(0,Math.min(c.h,p[1]+dy*k))]);return}
  const p=ptsAt(t,sel.s,sel.r),q=p[sel.i];edit(c,tt=>setPts(tt,sel.s,sel.r,PM.movePoint(p,sel.i,rp([q[0]+dx*k,q[1]+dy*k]))))}
 document.addEventListener('keydown',ev=>{
  const form=ev.target.closest?.('form[data-mt-form]');
  if(form){if(ev.key==='Escape'){const t=traceOf(form.dataset.mtScope);if(t){ev.preventDefault();ev.stopPropagation();t.edit=null;Live.render()}}return}
  if(ev.target.closest?.('input,select,textarea'))return;
  const scope=L.mtDialog?'dialog':Live.screen()==='property'?'property':null;if(!scope||!L.study)return;
  const u=scope==='property'?ui(L.study):null,c=ctxOf(scope),t=c?.t||null;
  if(ev.key==='Escape'){
   if(u?.mode==='pin'){u.mode=null;u.candidate=null;Live.render();return}
   if(t?.edit){t.edit=null;Live.render();return}
   if(t?.draft.length){PM.remember(t);t.draft=[];Live.render();return}
   if(t?.sel){t.sel=null;Live.render();return}
   if(scope==='dialog'){ev.preventDefault();closeDialog()}
   return;
  }
  if(!t||!measuring(scope))return;
  const onLabel=ev.target.closest?.('[data-mt-edge]'),onVertex=ev.target.closest?.('[data-mt-vertex],[data-mt-scale]');
  if(onLabel&&(ev.key==='Enter'||ev.key===' ')){ev.preventDefault();openEdit(scope,parseRef(onLabel.dataset.mtEdge));return}
  if(onVertex&&(ev.key==='Enter'||ev.key===' ')){ev.preventDefault();const r=onVertex.dataset.mtVertex;t.sel=r?parseRef(r):{scale:true,i:Number(onVertex.dataset.mtScale)};t.focusSel=true;Live.render();return}
  if(ev.key==='Enter'&&t.tool==='line'&&t.draft.length>=2){PM.remember(t);t.shapes.push({kind:'line',path:t.draft});t.draft=[];t.fromBase=false;Live.render();return}
  const mod=ev.metaKey||ev.ctrlKey;
  if(mod&&(ev.key==='z'||ev.key==='Z')){ev.preventDefault();(ev.shiftKey?A['mt-redo']:A['pa-trace-undo'])(svgFor(scope));return}
  if(mod&&(ev.key==='y'||ev.key==='Y')){ev.preventDefault();A['mt-redo'](svgFor(scope));return}
  if(!t.sel)return;
  if(ev.key==='Delete'||ev.key==='Backspace'){ev.preventDefault();t.focusSel=false;deleteCorner(scope);return}
  if((ev.key==='+'||ev.key==='=')&&!t.sel.scale){ev.preventDefault();const sel=t.sel,p=ptsAt(t,sel.s,sel.r),closed=t.shapes[sel.s].kind!=='line'||sel.r!=='o';if(!closed&&sel.i>=p.length-1)return;const a=p[sel.i],b=p[(sel.i+1)%p.length];
   if(edit(c,tt=>{setPts(tt,sel.s,sel.r,PM.insertPoint(p,sel.i,rp([(a[0]+b[0])/2,(a[1]+b[1])/2])));tt.sel={...sel,i:sel.i+1}}))announce('Corner added in the middle of the next edge.');t.focusSel=true;Live.render();return}
  const step=ev.shiftKey?10*KEY_STEP_PX:KEY_STEP_PX,mv={ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,-step],ArrowDown:[0,step]}[ev.key];
  if(mv){ev.preventDefault();keyMove(c,...mv);t.focusSel=true;Live.render()}
 });
 const svgFor=scope=>document.querySelector?.(`svg[data-mt-scope="${scope}"]`)||{closest:()=>null,dataset:{mtScope:scope}};
 document.addEventListener('change',ev=>{
  const el=ev.target;
  if(el.dataset&&'mtPick' in el.dataset){if(el.value&&L.mtDialog){L.mtDialog.key=el.value;L.mtDialog.zoom=null;L.mtDialog.scroll=null;Live.render()}return}
  if(el.dataset?.mtInput){
   const c=ctxOf(scopeOf(el)),t=c?.t;if(!t)return;const k=el.dataset.mtInput;
   if(k==='unit'){PM.remember(t);t.unit=el.value;if(el.value==='LF')t.tool='line';t.shapes=t.shapes.filter(x=>x.kind===(t.tool==='line'?'line':'area'));t.fromBase=false}
   else if(k==='height'){PM.remember(t);t.height=el.value.trim();t.fromBase=false}
   else if(k==='scale')setScale(t,el.value.trim());
   else if(k==='verified')t.verified=el.checked;
   else if(k==='snap')t.snap=el.checked;
   Live.render();return;
  }
  if(Live.screen()!=='property'||!L.study)return;const u=ui(L.study);
  if(el.dataset.paLayer){u.layers[el.dataset.paLayer]=el.checked;Live.render()}
  else if(el.dataset.paCompare){u.compare[el.dataset.paCompare]=el.value;Live.render()}
 });
 // The typed length: the far corner moves along the edge so the edge has exactly that length.
 document.addEventListener('submit',ev=>{
  const f=ev.target.closest?.('form[data-mt-form="edge"]');if(!f)return;ev.preventDefault();ev.stopPropagation();
  const c=ctxOf(f.dataset.mtScope);if(!c?.t.edit)return;const t=c.t,{s,r,i}=t.edit,raw=f.elements.namedItem('edge_length').value,parsed=PM.parseLength(raw);
  if(parsed.error){t.edit={...t.edit,value:raw,error:parsed.error};t.focusEdit=true;Live.render();return}
  const frame=frameFor(t,c.v),p=ptsAt(t,s,r),closed=t.shapes[s].kind!=='line'||r!=='o';
  let next;try{next=PM.setEdgeLength(p,i,parsed.ft,closed,frame).map(rp)}catch(e){t.edit={...t.edit,value:raw,error:e.message};t.focusEdit=true;Live.render();return}
  if(edit(c,tt=>setPts(tt,s,r,next))){t.edit=null;announce(`Edge ${i+1} set to ${PM.ftInWords(parsed.ft)}.`)}
  else{t.edit={...t.edit,value:raw,error:t.msg};t.msg=null;t.focusEdit=true}
  Live.render();
 },true);
 // After a render: the screen's scale (picture pixels per screen pixel) for the labels, and focus.
 function measureLabels(){
  if(typeof document.querySelectorAll!=='function')return;
  for(const svg of document.querySelectorAll('svg[data-mt-diagram]')){const d=diagrams[svg.dataset.mtDiagram],box=svg.getBoundingClientRect?.();if(!d||!box?.width)continue;const k=svg.viewBox.baseVal.width/box.width;if(!d.k||Math.abs(d.k-k)/k>0.03){d.k=k;svg.innerHTML=diagramBody(d);svg.setAttribute('aria-label',(svg.getAttribute('aria-label')||'').replace(/ Not drawn for lack of room:.*$/,'')+droppedText(d));const f=document.querySelector(`[data-mt-furniture="${svg.dataset.mtDiagram}"]`);if(f)f.outerHTML=furnitureHtml(svg.dataset.mtDiagram)}}
  for(const svg of document.querySelectorAll('svg[data-mt-scope],svg[data-mt-card]')){
   const box=svg.getBoundingClientRect?.();if(!box?.width)continue;const k=svg.viewBox.baseVal.width/box.width;
   const card=svg.dataset.mtCard?cards.get(svg.dataset.mtCard):null,t=card?card.t:traceOf(svg.dataset.mtScope);if(!t)continue;
   if(!t.k||Math.abs(t.k-k)/k>0.03){t.k=k;const layer=svg.querySelector('[data-mt-layer]');if(layer)layer.outerHTML=card?toolLayer(t,card.v,{readOnly:true}):toolLayer(t,ctxOf(svg.dataset.mtScope).v);
    if(!card&&t.edit){const c=ctxOf(svg.dataset.mtScope),box2=svg.parentElement.querySelector('form[data-mt-form]');if(box2)box2.outerHTML=editBox(t,c.v,c.w,c.h,svg.dataset.mtScope)}}
  }
  for(const scope of ['property','dialog']){const t=traceOf(scope);if(!t)continue;
   if(t.focusEdit){t.focusEdit=false;const i=document.querySelector(`form[data-mt-scope="${scope}"] input`);if(i){i.focus();i.select?.()}}
   else if(t.focusSel&&t.sel){t.focusSel=false;const ref=t.sel.scale?`[data-mt-scale="${t.sel.i}"]`:`[data-mt-vertex="${refKey(t.sel.s,t.sel.r,t.sel.i)}"]`;document.querySelector(`svg[data-mt-scope="${scope}"] ${ref}`)?.focus?.()}}
  const D=L.mtDialog;if(D?.focus){D.focus=false;document.querySelector('.mt-dialog')?.focus?.()}
  // Scroll the zoomed picture: onto the shape when it opens, onto the same middle after a zoom.
  const pane=D&&document.querySelector('.mt-stage .pa-zoom');
  if(pane&&(D.centre||D.keep)){const c=scopes.dialog,box=D.centre&&c?shapesBox(c.t):null,at=box?[(box.x+box.w/2)/c.w,(box.y+box.h/2)/c.h]:D.keep;D.centre=false;D.keep=null;
   if(at)centreOn(pane,at)}
  else if(pane&&D.scroll){pane.scrollLeft=D.scroll[0];pane.scrollTop=D.scroll[1]}
  // The Property page's zoomed picture keeps its place too.
  const stagePane=Live.screen?.()==='property'&&L.study&&document.querySelector('.pa-main .pa-stage .pa-zoom'),u=L.study&&L.property?.[L.study.id];
  if(stagePane&&u?.keep){centreOn(stagePane,u.keep);u.keep=null}
  else if(stagePane&&u?.scroll){stagePane.scrollLeft=u.scroll[0];stagePane.scrollTop=u.scroll[1]}
 }
 Live.afterRender.push(measureLabels);
 if(typeof addEventListener==='function'){let timer=null;addEventListener('resize',()=>{clearTimeout(timer);timer=setTimeout(measureLabels,120)})}
 // The dialog belongs to the Assets and asset pages of its study.
 // A render rebuilds the page: a zoomed picture's scroll is kept across it.
 (Live.beforeRender||=[]).push(()=>{if(typeof document.querySelector!=='function')return;const D=L.mtDialog,pane=D&&document.querySelector('.mt-stage .pa-zoom');if(pane)D.scroll=[pane.scrollLeft,pane.scrollTop];
  const sp=L.study&&document.querySelector('.pa-main .pa-stage .pa-zoom');if(sp&&L.property?.[L.study.id])L.property[L.study.id].scroll=[sp.scrollLeft,sp.scrollTop]});
 (Live.beforeRender||=[]).push(()=>{const D=L.mtDialog;if(!D)return;const [screen,study]=(typeof location==='object'?location.hash.slice(1):'').split('/');if(!['assets','asset-detail'].includes(screen)||(study&&decodeURIComponent(study)!==D.studyId)){L.mtDialog=null;delete scopes.dialog}});
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
