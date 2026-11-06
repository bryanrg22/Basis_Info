/* Live mode, part 1: sign-in, routing, data loading, studies, upload, processing, files.
 *
 * Loaded after the design files and active only with ?mode=live (live/config.js). It
 * replaces the study screens' functions with versions that read and write the API and
 * reuses the design's helpers (heading, btn, icon, esc, modal, toast) and CSS classes, so
 * the design files stay the source of truth for the look. Mock mode is untouched.
 *
 * Routes: #<screen>/<study id>[/<document id>], e.g. #rooms/3f2c.../ or #viewer/<id>/<doc>;
 * #components (the components review) is a live screen the design does not have.
 * Live actions use data-live="..." attributes, handled here before the design's handlers.
 *
 * Firms (live/live-team.js): after sign-in GET /v1/me decides the screen (Live.gate): the
 * "not a member" screen, the join screen, or the app in the current firm; #team is live.
 *
 * MOCK-ONLY in live mode (no API yet): the owner overview, editing presence, study drafts,
 * cross-study activity history, client name and address. Study history, results and
 * cost indices are supplied by live-results.js.
 */
(function(){
 'use strict';
 const C=globalThis.BASIS_CONFIG;if(!C||C.mode!=='live')return;
 document.documentElement.dataset.basisMode='live';
 const L={authReady:false,user:null,authError:'',studies:null,studiesError:null,studyId:null,docId:null,study:null,cache:new Map(),blobs:new Map(),poller:null,workflow:null,upload:null,returnTo:null,busy:new Set(),corrections:{},viewer:{mode:'spotlight',focusLabel:null,selectedId:null,tool:null,addLabel:null},assetsFilter:'all',studiesFilter:'All studies',studiesQuery:'',studiesSort:'updated'};
 // Every call is a breadcrumb and every failure an event in the error capture (live/errors.js).
 // The sample API (live/sample-api.js, ?api=sample) answers in the page; the real API path is unchanged.
 const SAMPLE=C.apiBase==='sample'&&globalThis.BasisSampleApi?.active?globalThis.BasisSampleApi:null;
 const api=BasisApi.createClient({baseUrl:SAMPLE?SAMPLE.base:C.apiBase,fetchImpl:SAMPLE?SAMPLE.fetch:undefined,getToken:()=>BasisAuth.getToken(),onUnauthorized:()=>{if(BasisAuth.user)toast('Your session has expired. Sign in again.')},observe:info=>globalThis.BasisErrors?.apiCall(info)});
 const reportError=(err,context)=>{try{globalThis.BasisErrors?.report(err,context)}catch{}};
 // An API refusal the client already recorded (test and fixture harnesses stub BasisApi without ApiError).
 const apiRefusal=e=>typeof BasisApi.ApiError==='function'&&e instanceof BasisApi.ApiError;
 const Live=globalThis.BasisLive={config:C,api,state:L,screens:{}};
 // Proposed capabilities (web/FRONTEND_BACKLOG.md 14, "Engineer overrides"): GET /v1/capabilities
 // says which proposed corrections the API accepts. Anything but a 200 means none, so the current
 // API (404) shows exactly what it showed before. Asked once per session, outside the error capture.
 Live.caps={objectExclusion:false,unitCostOverride:false,roomCreate:false};
 Live.loadCaps=async()=>{
  if(Live.loadCaps.done)return;Live.loadCaps.done=true;
  try{
   const token=await BasisAuth.getToken(),headers={Accept:'application/json'};if(token)headers.Authorization=`Bearer ${token}`;
   const r=await (SAMPLE?SAMPLE.fetch:fetch)((SAMPLE?SAMPLE.base:C.apiBase)+'/v1/capabilities',{headers});
   if(!r.ok)return;const d=await r.json();
   Live.caps={objectExclusion:d?.object_exclusion===true,unitCostOverride:d?.unit_cost_override===true,roomCreate:d?.room_create===true};
   if(Object.values(Live.caps).some(Boolean))Live.render?.();
  }catch{}
 };
 const STAGE={uploading:'Waiting for documents',extracting:'Extracting the appraisal and analyzing photos',appraisal_review:'Appraisal review',rooms_review:'Rooms review',components_processing:'Preparing components',components_review:'Components review',assets_processing:'Preparing asset costs',assets_review:'Assets review',exporting:'Preparing results',completed:'Reviews approved'};
 const STATUS={draft:'Draft',processing:'Processing',waiting_review:'Ready for review',stalled:'Needs attention',completed:'Reviews approved',archived:'Archived',failed:'Failed'};
 // The gates (live/gates.js): the rooms review, then the components review, unless the
 // study's workflow has the single rooms review (studies.components_gate false).
 const G=globalThis.BasisGates,GATE_SCREEN=G.GATE_SCREEN;
 const PUBLIC=new Set(['landing','login']);
 // A completed study's delivery state (signed, update required, needs signature) from its report
 // state, loaded once per revision; "Reviews approved" until it is known.
 // A finished study's delivery state as a pill class: each state reads differently at a glance
 // (Update required solid amber, Needs signature/Verify signature blue, Needs details neutral, Blocked red).
 Live.deliveryClass=d=>d?.tone==='done'?'completed':({'Update required':'waiting_review is-update','Needs signature':'processing is-sign','Verify signature':'processing is-sign','Check status':'processing is-sign','Needs details':'is-details','Not ready':'is-details','Checking…':'is-details','Report status unavailable':'is-details',Blocked:'failed'}[d?.label]||'waiting_review');
 // A finished study's delivery state comes from its report state (GET .../report/state), one request
 // per study: until it loads the study reads Checking…, never Finished.
 const deliveryOf=(s,e)=>!e?.done?{label:'Checking…',tone:'pending'}:e.error?{label:'Report status unavailable',tone:'pending'}:(f=>({label:f.status,tone:f.complete?'done':'review'}))(G.completion(s,e));
 Live.delivery=s=>s?.status==='completed'?deliveryOf(s,Live.reportStateEntry?.(s)):{label:'Reviews approved',tone:'done'};
 // The same, read from the cache without starting a request.
 Live.deliveryPeek=s=>deliveryOf(s,L.cache.get('report-state:'+s.id+':'+s.revision));
 // Load the report state of every finished study, six requests at a time (the study list does not
 // carry it); each answer re-renders the page, which starts the next.
 Live.loadDeliveries=list=>{const todo=list.filter(s=>s.status==='completed'),key=s=>'report-state:'+s.id+':'+s.revision;let busy=todo.filter(s=>L.cache.get(key(s))&&!L.cache.get(key(s)).done).length;for(const s of todo){if(busy>=6)break;if(!L.cache.has(key(s))){Live.reportStateEntry?.(s);busy++}}};
 Live.STAGE=STAGE;Live.STATUS=STATUS;Live.GATE_SCREEN=GATE_SCREEN;Live.gates=G;
 Live.labelName=l=>String(l||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());
 Live.when=v=>v?new Intl.DateTimeFormat('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}).format(new Date(v)):'—';
 Live.route=(screen,studyId=L.studyId,docId,origin)=>`#${screen}${studyId?'/'+encodeURIComponent(studyId):''}${docId?'/'+encodeURIComponent(docId):''}${screen==='viewer'&&['rooms','components'].includes(origin)?'/'+origin:''}`;
 Live.go=(screen,studyId=L.studyId,docId,origin)=>{const hash=Live.route(screen,studyId,docId,origin);if(location.hash===hash)render();else location.hash=hash};
 Live.errorText=e=>e?.code&&globalThis.BasisErrorHelp?`${BasisErrorHelp.explain(e.code,e.message).what} ${BasisErrorHelp.explain(e.code,e.message).fix} (${e.code}${e.requestId?'; request '+e.requestId:''})`:e?(e.status?`${e.message}${e.requestId?` (request ${e.requestId})`:''}`:e.message||String(e)):'';
 const nextScreen=G.nextScreen;
 Live.nextScreen=nextScreen;
 // A study's gate is open for a decision when it waits at that stage.
 Live.gateOpen=(stage)=>L.study&&L.study.current_stage===stage&&L.study.status==='waiting_review';

 /* ---------- data: one cached promise per key; screens render a loading state ---------- */
 function load(key,loader,{force=false}={}){
  const hit=L.cache.get(key);if(hit&&!force)return hit;
  const entry={promise:null,data:undefined,error:null,done:false};L.cache.set(key,entry);
  entry.promise=loader().then(d=>{entry.data=d;entry.done=true;render();return d},e=>{entry.error=e;entry.done=true;if(!apiRefusal(e))reportError(e,{load:key});render();throw e});
  entry.promise.catch(()=>{});return entry;
 }
 Live.load=load;
 Live.invalidate=prefix=>{for(const k of [...L.cache.keys()])if(k.startsWith(prefix))L.cache.delete(k)};
 Live.loading=(what='Loading…')=>`<div class="live-state" role="status"><span class="live-spinner" aria-hidden="true"></span>${esc(what)}</div>`;
 Live.failed=(e,retry)=>`<div class="live-state is-error" role="alert"><strong>${esc(e?.title||'Something went wrong')}</strong><p>${esc(Live.errorText(e))}</p>${retry?`<button class="button small" data-live="${retry}">Try again</button>`:''}</div>`;
 function studyEntry(force=false){return L.studyId?load('study:'+L.studyId,()=>api.getStudy(L.studyId),{force}):null}
 Live.studyEntry=studyEntry;
 // Run fn(study) once the current study is loaded, or return its loading/error state.
 Live.withStudy=fn=>{
  if(!L.studyId)return `<div class="live-state"><p>Choose a study first.</p><a class="button" href="#dashboard">Your studies</a></div>`;
  const s=studyEntry();if(!s.done)return Live.loading('Loading the study…');if(s.error)return Live.failed(s.error,'reload-study');
  L.study=s.data;return fn(s.data);
 };
 Live.reloadStudy=async()=>{Live.invalidate('study:'+L.studyId);const s=studyEntry(true);try{await s.promise}catch{}return s.data};
 /* Only the processing screen polls the study, so a cached study goes stale once the app
  * leaves it: a gate that opened meanwhile stayed read-only until a reload. Entering a study
  * screen (another screen or study) revalidates the cached study in the background: the
  * cached copy stays on screen and the page renders again only if the study changed. */
 const REVALIDATE=new Set(['appraisal','rooms','components','viewer','assets','asset-detail','complete','report','files','processing','history','spend','structure','intake']);
 let entered='';
 function revalidateOnEntry(){
  const at=state.screen+'/'+L.studyId;if(at===entered)return;entered=at;
  if(!REVALIDATE.has(state.screen)||!L.studyId)return;
  Live.invalidate('report-state:'+L.studyId+':');
  const key='study:'+L.studyId,entry=L.cache.get(key);
  if(!entry?.done||entry.error||entry.revalidating)return;// not loaded yet: the load is fresh
  entry.revalidating=true;
  api.getStudy(L.studyId).then(fresh=>{
   if(L.cache.get(key)!==entry||JSON.stringify(fresh)===JSON.stringify(entry.data))return;
   entry.data=fresh;render();
  },()=>{}).finally(()=>{entry.revalidating=false});// a failed check keeps the cached study
 }
 /* Document bytes (photos, the appraisal) need the bearer token, so they are fetched and
  * shown from object URLs, at most six at a time. <img data-live-doc="id"> is filled in.
  * Photos on screen come from /image (an iPhone's HEIC as JPEG, which every browser shows);
  * downloads and the appraisal come from /content, the original bytes. */
 const queue=[];let active=0;
 const photoKey=docId=>'image:'+docId;
 function blobUrl(docId,studyId=L.studyId,shown=false){
  const key=shown?photoKey(docId):docId;
  if(L.blobs.has(key))return L.blobs.get(key);
  const p=new Promise((resolve,reject)=>{queue.push({docId,studyId,key,shown,resolve,reject});pump()});L.blobs.set(key,p);p.catch(()=>L.blobs.delete(key));return p;
 }
 function pump(){
  while(active<6&&queue.length){const job=queue.shift();active++;
   (job.shown?api.documentImage:api.documentContent)(job.studyId,job.docId).then(r=>r.blob()).then(b=>{const url=URL.createObjectURL(b);L.blobReady.set(job.key,url);job.resolve(url)},job.reject).finally(()=>{active--;pump()})}
 }
 L.blobReady=new Map();
 Live.blobUrl=blobUrl;
 Live.photoUrl=(docId,studyId=L.studyId)=>blobUrl(docId,studyId,true);
 function hydrateImages(scope=root){
  for(const img of scope.querySelectorAll('img[data-live-doc]:not([src])')){
   Live.photoUrl(img.dataset.liveDoc).then(url=>{if(img.isConnected&&!img.src)img.src=url},()=>{img.replaceWith(Object.assign(document.createElement('div'),{className:'live-photo-missing',textContent:'Photo unavailable'}))});
  }
 }
 Live.hydrateImages=hydrateImages;
 // A photo already fetched gets its src at once, so re-rendering does not blink it.
 Live.photo=(docId,alt='',cls='')=>{const ready=L.blobReady.get(photoKey(docId));return `<img data-live-doc="${esc(docId)}" ${ready?`src="${ready}"`:''} alt="${esc(alt)}" class="${cls}" decoding="async">`};

 /* ---------- routing and the sign-in gate ---------- */
 function parseHash(){
  const raw=location.hash.slice(1)||'landing',[screen,study,doc,origin]=raw.split('/');
  state.screen=screen||'landing';
  if(study){const id=decodeURIComponent(study);if(id!==L.studyId){L.studyId=id;L.study=null;L.corrections={};stopPolling()}sessionStorage.setItem('basis-live-study',id)}
  else if(!L.studyId)L.studyId=sessionStorage.getItem('basis-live-study');
  L.docId=doc?decodeURIComponent(doc):null;
  L.viewerOrigin=screen==='viewer'&&['rooms','components'].includes(origin)?origin:null;
 }
 const baseRender=render;
 render=function(){
  parseHash();
  if(!L.authReady){root.innerHTML=liveBar()+`<main class="live-splash">${Live.loading('Connecting to sign-in…')}${L.authError?Live.failed({title:'Sign-in is unavailable',message:L.authError}):''}</main>`;return}
  if(!L.user&&!PUBLIC.has(state.screen)){L.returnTo=location.hash||'#dashboard';state.screen='login'}
  if(L.user&&state.screen==='login'){state.screen='dashboard';history.replaceState(null,'','#dashboard')}
  if(state.screen!=='processing')stopPolling();
  // Firms: no firm yet, or an invitation to answer, replaces the app (live/live-team.js).
  const gated=L.user&&Live.gate?Live.gate():'';
  if(gated){root.innerHTML=liveBar()+gated;document.title='Basis · Firm access';return}
  if(L.user)revalidateOnEntry();
  // The components review is a live screen the design's screen table does not have.
  if(state.screen==='components'&&L.user&&Live.componentsScreen){root.innerHTML=reviewbar()+shell(Live.componentsScreen());document.title='Basis · Components review'}
  else if(L.user&&Live.screens[state.screen]){root.innerHTML=reviewbar()+shell(Live.screens[state.screen]());document.title='Basis · '+Live.labelName(state.screen)}
  else baseRender();
  afterRender();
 };
 Live.render=()=>render();
 Live.reportError=reportError;
 Live.screen=()=>state.screen;// the error capture names the screen an error happened on
 function afterRender(){
  const workspace=root.querySelector('.workspace');if(workspace)workspace.dataset.screen=state.screen;
  const bar=root.querySelector('.reviewbar');if(bar)bar.outerHTML=liveBar();
  else root.insertAdjacentHTML('afterbegin',liveBar());
  // The design's sidebar names a sample engineer; show who is signed in.
  if(L.user){const who=L.user.email||L.user.displayName||'Signed in',initials=who.replace(/@.*/,'').split(/[^a-z0-9]+/i).filter(Boolean).slice(0,2).map(w=>w[0].toUpperCase()).join('')||'?';
   for(const el of root.querySelectorAll('.account strong'))el.textContent=who;
   for(const el of root.querySelectorAll('.account .avatar, .account-menu .avatar'))el.textContent=initials;
   for(const el of root.querySelectorAll('.account .account-role'))el.textContent='Signed in';
   root.querySelector('#account-menu-trigger')?.setAttribute('aria-label',who+' — account menu');}
  for(const link of root.querySelectorAll('aside a[href="#admin"]'))if(!link.querySelector('.live-mock-tag'))link.insertAdjacentHTML('beforeend','<span class="live-mock-tag" title="Not connected to the API yet: sample data">Mock</span>');
  const crumb=root.querySelector('.breadcrumbs .strong');if(crumb&&['appraisal','rooms','components','viewer','assets','asset-detail','complete','report','processing','files','history','spend','structure','intake'].includes(state.screen))crumb.innerHTML=!L.study?'Loading study…':state.screen==='processing'?esc(L.study.property_name):`<a class="study-overview-link" href="${Live.route('processing',L.study.id)}" title="Study progress">${esc(L.study.property_name)} ${icon('chevron',12)}</a>`;
  for(const meta of root.querySelectorAll('.topbar .right-meta'))meta.innerHTML=L.study&&L.studyId&&['appraisal','rooms','components','viewer','assets','asset-detail','complete','report','processing','files','history','spend','structure','intake'].includes(state.screen)?`<span>Revision ${L.study.revision}</span>${L.study.status==='completed'?(d=>state.screen==='processing'&&d.tone!=='done'?'':d.tone==='done'?`<span class="badge study-delivery complete">${esc(d.label)}</span>`:`<a class="badge study-delivery review" href="${Live.route('report',L.study.id)}" title="Open Report & sign">${esc(d.label)}</a>`)(Live.delivery(L.study)):statusBadge(L.study.status)}`:'';
  const titles={dashboard:'Home',library:'Library',processing:'Study progress',complete:'Study results',report:'Report & sign',activity:'Activity'};if(titles[state.screen])document.title='Basis · '+titles[state.screen];
  hydrateImages();
  Live.afterRender.forEach(fn=>fn());
 }
 Live.afterRender=[];
 function liveBar(){
  const host=(()=>{try{return new URL(C.apiBase).host}catch{return C.apiBase}})();
  const mock=new URL(location.href);mock.searchParams.delete('mode');
  const developer=new URL('developer/',location.href);developer.search=location.search;developer.hash='';
  const developerLink=['localhost','127.0.0.1','[::1]'].includes(location.hostname)?`<a href="${esc(developer.toString())}">System health</a>`:'';
  return `<div class="reviewbar live-bar"><div class="row"><strong>Basis</strong><span class="live-badge">Live</span><span class="small">${SAMPLE?'Sample data · nothing is saved':`API ${esc(host)}${C.firebase.emulatorHost?' · Auth emulator':''}`}</span></div><div class="row">${L.user&&Live.barExtra?Live.barExtra():''}${L.user?`<span class="small">${esc(L.user.email||L.user.displayName||'Signed in')}</span><button class="button quiet small" data-live="sign-out">Sign out</button>`:''}${developerLink}<a href="${esc(mock.toString())}">Design review (mock)</a></div></div>`;
 }
 reviewbar=liveBar;
 // Study drafts are mock-only: their leave-page guard must not hold up live navigation.
 hasUnsavedDraft=()=>false;
 // Editing presence (who is editing, leases) is mock-only: no API yet.
 presence=()=>'';

 /* ---------- sign-in ---------- */
 login=function(){
  const emulator=Boolean(C.firebase.emulatorHost);
  return `<main class="login"><section class="login-story">${logo()}<h2>A clear view of every study.</h2><p>Your documents, observations, and engineering decisions. Together in one workspace.</p></section><section class="login-form"><div class="login-form-inner"><h1>Welcome to Basis</h1><p>Sign in to your engineering workspace.</p><button class="button" data-live="google">${icon('shield')}Continue with Google</button><p class="small">${emulator?'Local Auth emulator: the Google window offers test accounts.':'Use the Google account associated with your workspace.'}</p>${emulator?`<form class="live-login-form" data-live-form="email" novalidate><h3>Local test account</h3><div class="field"><label for="live-email">Email</label><input id="live-email" type="email" autocomplete="username" required></div><div class="field"><label for="live-password">Password</label><input id="live-password" type="password" autocomplete="current-password" minlength="6" required></div><div class="row"><button class="button primary" type="submit">Sign in</button><button class="button quiet" type="button" data-live="create-account">Create test account</button></div><p class="footnote">Emulator accounts only exist on this machine. The API accepts them only in BASIS_ENV=dev.</p></form>`:''}<p class="live-login-error" role="alert" ${L.authError?'':'hidden'}>${esc(L.authError)}</p><a class="screen-link" href="#landing">Back to Basis</a></div></section></main>`;
 };
 async function afterSignIn(){L.authError='';const target=L.returnTo&&!/^#(login|landing)?$/.test(L.returnTo)?L.returnTo:'#dashboard';L.returnTo=null;location.hash=target;render()}
 async function emailSignIn(create){
  const email=root.querySelector('#live-email')?.value.trim(),password=root.querySelector('#live-password')?.value||'';
  try{await BasisAuth.signInWithEmail(email,password,{create});await afterSignIn()}catch(e){L.authError=e.message;render()}
 }

 /* ---------- studies: the dashboard and the library ---------- */
 function studiesEntry(force=false){return load('studies',()=>api.listStudies().then(p=>p.items),{force})}
 // "Report to sign": finished studies whose report needs the engineer, among those whose report state is loaded.
 const reportDue=s=>s.status==='completed'&&Live.deliveryPeek(s).tone==='review';
 // Finished: delivered. A study whose report state has not loaded (or failed) is in neither count.
 const delivered=s=>s.status==='completed'&&Live.deliveryPeek(s).tone==='done';
 const FILTERS={'All studies':()=>true,'Needs review':s=>['waiting_review','stalled','failed'].includes(s.status),'Report to sign':reportDue,'Processing':s=>s.status==='processing','Finished':delivered,'Drafts':s=>s.status==='draft'};
 function studiesTable(rows){
  return `<div class="collection-table-wrap"><table class="collection-table"><caption class="sr-only">Studies</caption><thead><tr><th scope="col">Study</th><th scope="col">Status</th><th scope="col">Revision</th><th scope="col">Updated</th><th scope="col"><span class="sr-only">Open</span></th></tr></thead><tbody>${rows.map((s,i)=>`<tr><td class="collection-study-cell"><a class="collection-entry" href="${Live.route('processing',s.id)}"><span><strong>${esc(s.property_name)}</strong>${s.status==='completed'&&!s.failure_reason?'':`<span class="collection-address">${esc(STAGE[s.current_stage]||s.current_stage)}${s.failure_reason?' · '+esc(s.failure_reason):''}</span>`}</span></a></td><td class="collection-state-cell">${s.status==='completed'?(d=>`<span class="desk-status ${Live.deliveryClass(d)}">${esc(d.label)}</span>`)(Live.deliveryPeek(s)):deskStatus(s)}</td><td class="num">${s.revision}</td><td class="collection-date-cell"><time datetime="${esc(s.updated_at)}">${Live.when(s.updated_at)}</time></td><td class="collection-open-cell"><a class="collection-open" href="${Live.route('processing',s.id)}" aria-label="Open ${esc(s.property_name)}"><span>Open study</span>${icon('arrow',17)}</a></td></tr>`).join('')||`<tr><td colspan="5"><div class="collection-empty">${icon('search',28)}<h2>${L.studies?.length?'No matching studies':'No studies yet'}</h2><p>${L.studies?.length?'Try another name or status.':'Start a study to upload an appraisal and property photos.'}</p>${L.studies?.length?'':`<a class="button primary" href="#upload">${icon('plus',15)}Start a study</a>`}</div></td></tr>`}</tbody></table></div>`;
 }
 function studiesView(title,subtitle){
  const e=studiesEntry();if(!e.done)return Live.loading('Loading your studies…');
  // Removed from the firm meanwhile: re-read /v1/me (the gate then explains) and the list.
  if(e.error?.kind==='no-firm'){Live.invalidate('studies');Live.refreshMe?.();return Live.loading('Checking your firm…')}
  if(e.error)return Live.failed(e.error,'reload-studies');
  L.studies=Live.firmStudies?Live.firmStudies(e.data):e.data;Live.loadDeliveries(L.studies);const q=L.studiesQuery.trim().toLowerCase();
  const matched=L.studies.filter(s=>!q||s.property_name.toLowerCase().includes(q));
  const rows=matched.filter(FILTERS[L.studiesFilter]).sort((a,b)=>L.studiesSort==='name'?a.property_name.localeCompare(b.property_name):L.studiesSort==='oldest'?a.updated_at.localeCompare(b.updated_at):b.updated_at.localeCompare(a.updated_at));
  return `<div class="collection-desk live-studies"><header class="collection-header"><div><h1>${title}</h1>${subtitle?`<p>${subtitle}</p>`:''}</div><a class="collection-create" href="#upload">${icon('plus',17)}Start a study</a></header><div class="collection-search-area"><label class="collection-search-field">${icon('search',21)}<input id="live-study-search" aria-label="Search studies" placeholder="Search by property name…" value="${esc(L.studiesQuery)}"></label></div><section class="collection-panel" aria-label="Studies"><div class="collection-toolbar"><div class="collection-status-filters" aria-label="Filter studies by status">${Object.keys(FILTERS).map(f=>`<button type="button" data-live="studies-filter" data-value="${f}" aria-pressed="${L.studiesFilter===f}">${f}<span>${matched.filter(FILTERS[f]).length}</span></button>`).join('')}${(n=>n?`<span class="library-checking">Report status for ${n} ${n===1?'study':'studies'} still loading; they are not in Report to sign or Finished yet.</span>`:'')(matched.filter(s=>s.status==='completed'&&Live.deliveryPeek(s).tone==='pending').length)}</div><label class="library-sort"><span class="sr-only">Sort studies</span><select id="live-study-sort" aria-label="Sort studies">${[['updated','Last updated'],['oldest','Oldest first'],['name','Property A–Z']].map(([v,l])=>`<option value="${v}" ${L.studiesSort===v?'selected':''}>${l}</option>`).join('')}</select></label></div><div id="live-study-results">${studiesTable(rows)}</div></section></div>`;
 }
 function withStudies(fn){
  const e=studiesEntry();if(!e.done)return Live.loading('Loading your studies…');
  if(e.error?.kind==='no-firm'){Live.invalidate('studies');Live.refreshMe?.();return Live.loading('Checking your firm…')}
  if(e.error)return Live.failed(e.error,'reload-studies');
  L.studies=Live.firmStudies?Live.firmStudies(e.data):e.data;return fn(L.studies);
 }
 function homeStudy(s){
  // A finished study that still needs the engineer (update required, signature) opens on Report & sign.
  const d=s.status==='completed'?Live.delivery(s):null;
  if(d?.tone==='review')return `<a class="home-study" href="${Live.route('report',s.id)}"><div><strong>${esc(s.property_name)}</strong><span>Report & sign</span></div><span><span class="desk-status ${Live.deliveryClass(d)}">${esc(d.label)}</span></span>${icon('chevron',16)}</a>`;
  return `<a class="home-study" href="${Live.route('processing',s.id)}"><div><strong>${esc(s.property_name)}</strong><span>${esc(s.status==='draft'?'Finish uploading documents':STAGE[s.current_stage]||s.current_stage)}</span></div><span>${statusBadge(s.status)}</span>${icon('chevron',16)}</a>`;
 }
 dashboard=()=>withStudies(studies=>{
  const h=BasisStudyOverview.home(studies);
  // Finished studies whose report needs the engineer join the attention queue. Every finished
  // study's report state is read (six at a time, once per revision); until one loads, that study
  // counts as neither waiting nor finished.
  Live.loadDeliveries(studies);
  for(const s of studies.filter(x=>x.status==='completed').sort((a,b)=>String(b.updated_at).localeCompare(String(a.updated_at))))if(Live.deliveryPeek(s).tone==='review'&&!h.attention.includes(s))h.attention.push(s);
  return `<div class="home-desk">${heading('Home','',`<a class="button primary" href="#upload">${icon('plus',16)}Start a study</a>`)}<dl class="study-summary" aria-label="Study overview"><div><dt>Need attention</dt><dd>${h.attention.length}</dd></div><div><dt>In progress</dt><dd>${h.processing.length}</dd></div><div><dt>Finished</dt><dd>${studies.filter(x=>x.status==='completed'&&!h.attention.includes(x)&&Live.deliveryPeek(x).tone==='done').length}</dd></div></dl><div class="home-columns"><section class="panel home-attention"><div class="panel-head"><h2>Needs your attention</h2><span class="small">${h.attention.length} ${h.attention.length===1?'study':'studies'}</span></div>${h.attention.map(homeStudy).join('')||`<div class="home-empty"><h3>${studies.length?'No reviews waiting':'Your first study starts here'}</h3><p>${studies.length?'Approved studies may still need report details or a signature.':'Upload an appraisal and property photos to get started.'}</p>${studies.length?'':'<a class="button" href="#upload">Start a study</a>'}</div>`}${h.processing.length?`<div class="panel-head"><h2>In progress</h2></div>${h.processing.map(homeStudy).join('')}`:''}</section><section class="home-recent"><div class="row between"><h2>Recently updated</h2><a class="screen-link" href="#library">View library ${icon('arrow',14)}</a></div>${h.recent.map(s=>`<a class="home-recent-study" href="${Live.route('processing',s.id)}"><strong>${esc(s.property_name)}</strong><span>${esc(s.status==='completed'?Live.delivery(s).label:STATUS[s.status]||s.status)} · ${Live.when(s.updated_at)}</span></a>`).join('')||'<p class="small">Your studies will appear here.</p>'}</section></div></div>`;
 });
 activity=()=>withStudies(studies=>{
  const events=BasisStudyOverview.activity(studies);
  return `${heading('Activity','Study creation and the latest saved update for each study.')}<section class="panel study-activity" aria-label="Study updates">${events.map(event=>`<article class="study-update"><span class="study-update-icon">${icon(event.kind==='created'?'plus':'clock',18)}</span><div><p>${event.kind==='created'?'Study created':'Study updated'}</p><a href="${Live.route('processing',event.study.id)}">${esc(event.study.property_name)}</a>${event.kind==='updated'?`<span class="small">Current status: ${esc(STATUS[event.study.status]||event.study.status)}</span>`:''}</div><time datetime="${esc(event.at)}">${Live.when(event.at)}</time></article>`).join('')||'<div class="home-empty"><h3>No study activity yet</h3><p>Updates will appear here as your team works on studies.</p></div>'}</section>`;
 });
 studyLibrary=()=>studiesView('Library','');
 archiveStudy=()=>Live.withStudy(s=>{location.replace(Live.route('processing',s.id));return Live.loading()});

 /* ---------- start a study: create, signed uploads, start ---------- */
 const PHOTO_TYPES={jpg:'image/jpeg',jpeg:'image/jpeg',png:'image/png',heic:'image/heic',heif:'image/heif',webp:'image/webp'};
 const extension=name=>(name.split('.').pop()||'').toLowerCase();
 async function sha256(file){const digest=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());return [...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('')}
 function putSigned(upload,file,onProgress){
  return new Promise((resolve,reject)=>{
   const xhr=new XMLHttpRequest();xhr.open(upload.method||'PUT',upload.url);
   for(const [k,v] of Object.entries(upload.headers||{}))if(!/^(content-length|host)$/i.test(k))xhr.setRequestHeader(k,v);
   xhr.upload.onprogress=e=>{if(e.lengthComputable)onProgress(e.loaded/e.total)};
   xhr.onload=()=>xhr.status>=200&&xhr.status<300?resolve():reject(new Error(`Upload refused (${xhr.status})${xhr.responseText?': '+xhr.responseText.slice(0,160):''}`));
   xhr.onerror=()=>reject(new Error('The upload could not reach storage.'));xhr.send(file);
  });
 }
 function uploadPlan(){
  const items=state.uploadItems.map(item=>({item,file:localStudyFiles.get(item.key)?.file})),missing=items.filter(x=>!x.file);
  const {appraisals,problems}=BasisAppraisalSource.plan(items),photos=items.filter(x=>x.item.kind==='photos'),other=items.filter(x=>x.item.kind==='documents');
  if(!state.propertyName?.trim())problems.push('Enter a property name.');
  if(missing.length)problems.push(`Reselect ${missing.length} ${missing.length===1?'file':'files'} (files are not kept after a reload).`);
  if(other.length)problems.push('Move your appraisal into Appraisal and images into Property photos; remove unsupported supporting files before starting.');
  if(!photos.length)problems.push('Add the property photos.');
  const badPhotos=photos.filter(x=>!PHOTO_TYPES[extension(x.item.name)]);if(badPhotos.length)problems.push(`Photos must be JPEG, PNG, HEIC or WebP: ${badPhotos.map(x=>x.item.name).slice(0,3).join(', ')}.`);
  return {appraisals,photos,other,problems};
 }
 async function startStudy(){
  if(L.upload?.running)return;
  const plan=uploadPlan();if(plan.problems.length){toast(plan.problems.join(' '));return}
  // One upload run per selection: a retry after a failure reuses the study and the files
  // already confirmed (and the same idempotency keys), so nothing is created twice.
  const selection=JSON.stringify([...plan.appraisals,...plan.photos].map(x=>x.item.key));
  if(L.upload?.studyId&&L.upload.selection!==selection){toast('Retry with the original file selection. Start a separate study to change its appraisal.');return}
  const run=L.upload&&L.upload.name===state.propertyName.trim()?L.upload:{name:state.propertyName.trim(),key:BasisApi.newKey(),files:new Map()};
  L.upload=run;run.selection=selection;run.running=true;run.error=null;
  const jobs=[...plan.appraisals.map(x=>({...x,kind:'appraisal',type:BasisAppraisalSource.types[BasisAppraisalSource.format(x.item)]})),...plan.photos.map(x=>({...x,kind:'photo',type:PHOTO_TYPES[extension(x.item.name)]}))];
  for(const job of jobs)if(!run.files.has(job.item.key))run.files.set(job.item.key,{name:job.item.name,kind:job.kind,progress:0,state:'waiting',key:BasisApi.newKey(),doc:null});
  render();
  try{
   for(const job of jobs.filter(j=>j.kind==='appraisal')){const source=await Live.inspectAppraisal(job.file);if(BasisAppraisalSource.types[source.kind]!==job.type)throw new Error(`${job.item.name}: the file contents do not match its extension.`)}
   if(!run.studyId){const body={property_name:run.name,room_aliases:{}},study=await api.createStudy(Live.createBody?Live.createBody(body):body,run.key);run.studyId=study.id;L.studyId=study.id;Live.invalidate('studies')}
   const pending=jobs.filter(j=>run.files.get(j.item.key).state!=='done');let next=0;
   async function worker(one=false){
    while(next<pending.length){
     const job=pending[next++],f=run.files.get(job.item.key);
     try{
      f.state='hashing';paintUpload();
      const digest=await sha256(job.file);
      f.state='uploading';paintUpload();
      const ticket=await api.registerDocument(run.studyId,{filename:job.file.name,bytes:job.file.size,sha256:digest,kind:job.kind,content_type:job.type},f.key);
      f.doc=ticket.document.id;
      await putSigned(ticket.upload,job.file,p=>{f.progress=p;paintUpload()});
      await api.completeDocument(run.studyId,f.doc,BasisApi.newKey());
      f.state='done';f.progress=1;
     }catch(e){f.state='failed';f.error=Live.errorText(e);f.key=BasisApi.newKey();throw e}
     finally{paintUpload()}
     if(one)return;
    }
   }
   while(next<pending.length&&pending[next].kind==='appraisal')await worker(true);
   const results=await Promise.allSettled(Array.from({length:Math.min(C.uploadConcurrency,pending.length)},worker));
   const failed=results.find(r=>r.status==='rejected');if(failed)throw failed.reason;
   run.state='starting';paintUpload();
   await api.start(run.studyId,run.startKey||=BasisApi.newKey());
   Live.invalidate('studies');Live.invalidate('study:'+run.studyId);
   const id=run.studyId;L.upload=null;state.uploadItems=[];toast('Study started. Basis is reading the appraisal and analyzing the photos.');Live.go('processing',id);
  }catch(e){run.error=Live.errorText(e);toast('The upload stopped: '+run.error)}
  finally{run.running=false;paintUpload()}
 }
 function uploadSummary(){
  const run=L.upload;if(!run)return '';
  const files=[...run.files.values()],done=files.filter(f=>f.state==='done').length,failed=files.filter(f=>f.state==='failed');
  const total=files.reduce((a,f)=>a+(f.state==='done'?1:f.progress||0),0)/Math.max(1,files.length);
  return `<section class="panel live-upload-progress" aria-live="polite"><div class="panel-head"><h2>${run.running?'Uploading':run.error?'Upload stopped':'Upload'}</h2><span class="small num">${done} of ${files.length} files</span></div><div class="panel-body"><div class="progress"><span style="width:${Math.round(total*100)}%"></span></div>${run.error?`<p class="live-upload-error">${esc(run.error)}</p><button class="button primary small" data-action="preview-analysis">Retry the failed files</button>`:''}${failed.length?`<ul class="live-upload-failed">${failed.slice(0,6).map(f=>`<li>${esc(f.name)}: ${esc(f.error||'failed')}</li>`).join('')}</ul>`:''}<p class="small">${run.state==='starting'?'Starting the study…':'Each file is checked against its SHA-256 after upload.'}</p></div></section>`;
 }
 function paintUpload(){const slot=root.querySelector('#live-upload-slot');if(slot)slot.innerHTML=uploadSummary()}
 const designUpload=upload;
 upload=function(){
  const reviews=[['Appraisal review','Check extracted property details against the source.'],['Rooms review','Check room names and group photos into the right rooms.'],['Components review','Inspect photo components and confirm each room’s inventory and counts.'],['Assets review','Review classification, quantities, and costs.']];
  const timeline=reviews.map(([title,description],i)=>`<div><span class="check">${i+1}</span><div><strong class="small">${title}</strong><p>${description}</p></div></div>`).join('');
  return designUpload().replace(/<nav class="steps"[^>]*>[\s\S]*?<\/nav>/,Live.steps({components_gate:true,current_stage:'uploading',status:'draft'}))
   .replace(/(<h3>What happens next<\/h3>)<div class="timeline">[\s\S]*?<\/section>/,`$1<div class="timeline">${timeline}</div></section>`)
   .replace('Design preview. Upload and analysis services are not connected.','Your study starts after all selected files finish uploading.').replace('>Preview analysis<','>Upload and start<')+`<div id="live-upload-slot">${uploadSummary()}</div>`;
 };
 Live.afterRender.push(()=>{if(state.screen!=='upload')return;
  // Client name and address have no API field yet: shown, but not sent.
  for(const id of ['client-name','property-address']){const f=root.querySelector('#'+id);if(f&&!f.closest('.field').querySelector('.live-mock-tag'))f.closest('.field').querySelector('label').insertAdjacentHTML('beforeend',' <span class="live-mock-tag" title="No API field yet: not saved">Not saved</span>')}
  const btn=root.querySelector('[data-action="preview-analysis"]');if(btn&&L.upload?.running)btn.disabled=true;
 });

 /* ---------- processing: the workflow status, polled ---------- */
 function stopPolling(){L.poller?.stop();L.poller=null}
 function startPolling(){
  if(L.poller||!L.studyId)return;const id=L.studyId;
  L.poller=BasisApi.pollWorkflow(signal=>api.workflow(id,signal),{initialMs:C.pollInitialMs,maxMs:C.pollMaxMs,
   onUpdate:async w=>{L.workflow={id,data:w,error:null};const before=L.study?.status+'/'+L.study?.current_stage;await Live.reloadStudy();if(state.screen==='processing'){const after=L.study?.status+'/'+L.study?.current_stage;if(before!==after&&L.study?.status==='waiting_review')toast(`${STAGE[L.study.current_stage]} is ready.`);render()}
    // Results and Report & sign show what the run writes: once it ends, read them again.
    else if(['complete','report'].includes(state.screen)&&before!==L.study?.status+'/'+L.study?.current_stage){for(const k of ['result:','report-state:','assets:'])Live.invalidate(k+id);render()}},
   onError:e=>{L.workflow={id,data:L.workflow?.data||null,error:e};if(state.screen==='processing')render()},
   shouldStop:w=>['COMPLETED','FAILED','CANCELED','TERMINATED','TIMED_OUT'].includes(String(w?.execution_status).toUpperCase())});
 }
 Live.startPolling=startPolling;Live.stopPolling=stopPolling;
 // Approving Assets lands on Results while the report is written; follow that run there too.
 Live.afterRender.push(()=>{if(['complete','report'].includes(state.screen)&&L.study?.id===L.studyId&&L.study?.status==='processing')startPolling()});
 function studyPeople(s){
  const me=Live.me?.(),firm=Live.firm?.();
  const known=BasisStudyOverview.creator(s,me,L.cache.get('members:'+s.org_id)?.data?.items||[]);
  if(!known&&firm?.org_id===s.org_id&&firm?.role==='owner')Live.load('members:'+s.org_id,()=>api.members(s.org_id));
  return known;
 }
 // A status label's tone, as the step bar shows it (gates.js navigation): done, review, danger, progress or idle.
 const toneOf=status=>({Completed:'done',Signed:'done','Needs review':'review','Update required':'review','Needs signature':'review','Needs details':'review','Add files':'review','Needs attention':'danger',Blocked:'review',// Blocked: the report waits on the engineer (amber, as in the step bar), not a failure
'In progress':'progress','Checking…':'progress'}[status]||'idle');
 processing=function(){
  return Live.withStudy(s=>{
   // Just reopened a step (live/gate-reopen.js): open its review as soon as it is ready.
   const target=L.reopenTarget;
   if(target?.studyId===s.id&&s.status==='waiting_review'&&s.current_stage===target.stage&&GATE_SCREEN[target.stage]){L.reopenTarget=null;L.lastSent=null;setTimeout(()=>Live.go(GATE_SCREEN[target.stage],s.id));return Live.loading('Opening the review…')}
   const sent=L.lastSent?.studyId===s.id&&s.status==='processing'?L.lastSent:null;
   if(!['draft','completed','archived'].includes(s.status))startPolling();
   const w=L.workflow?.id===s.id?L.workflow:null,p=w?.data?.status||null,docs=s.documents||[],confirmed=docs.filter(d=>d.uploaded_at),photos=confirmed.filter(d=>d.kind==='photo');
   const notStarted=s.status==='draft'||(w?.error?.status===404&&s.status!=='completed'&&s.status!=='archived');
   const gate=s.status==='waiting_review'&&GATE_SCREEN[s.current_stage]?s.current_stage:null;
   const failure=globalThis.BasisErrorHelp?BasisErrorHelp.failureState(s,p||{},Live.me?.()?.memberships||[]):{attention:s.status==='failed',canRetry:false};
   const failed=failure.attention,finished=s.status==='completed',archived=s.status==='archived';
   const list=BasisStudyOverview.steps(s,Live.reportStateEntry?.(s)),done=list.filter(step=>step.state==='done').length,person=studyPeople(s);
   const current=finished?'Every review is approved':archived?'This study is archived':failed?'This study needs attention':gate?`${STAGE[gate]} is ready`:notStarted?'Finish setting up this study':STAGE[s.current_stage]||'Study in progress';
   const detail=finished?`Review the results and reconciliation, or revisit a step below. Revision ${s.revision}.`:archived?'Completed steps remain available to view.':failed?s.failure_reason||p?.failure||'Retry to continue from the step that stopped.':gate?'Review this step to continue the study.':notStarted?`${confirmed.length} of ${docs.length} documents uploaded.`:s.current_stage==='components_processing'?'Counting room components and preparing photo outlines. You can leave this page while this runs.':'You can leave this page while processing continues.';
   const action=finished?`<a class="button primary" href="${Live.route('complete',s.id)}">View results ${icon('arrow',16)}</a>`:failed?(failure.canRetry?'<button class="button primary" data-live="retry">Retry processing</button>':'<p class="small">An engineer or admin can resolve this issue.</p>'):gate?`<a class="button primary" href="${Live.route(GATE_SCREEN[gate],s.id)}">Review ${esc(GATE_SCREEN[gate])} ${icon('arrow',16)}</a>`:notStarted&&confirmed.length?'<button class="button primary" data-live="start">Start the study</button>':'';
   const progressBar=`<nav class="hub-progress" aria-label="Study stages"><ol style="--hub-step-count:${list.length}">${list.map((step,i)=>{
    const content=`<span class="hub-progress-marker" aria-hidden="true">${step.state==='done'?'✓':i+1}</span><span><strong>${esc(step.label)}</strong><small>${esc(step.status)}</small></span>`;
    return `<li class="${step.state==='done'?'is-done':step.state==='active'?'is-current':''} step-tone-${toneOf(step.status)}" ${step.state==='active'?'aria-current="step"':''}>${step.canOpen?`<a class="hub-progress-step" href="${Live.route(step.destination,s.id)}">${content}</a>`:`<span class="hub-progress-step" aria-disabled="true">${content}</span>`}</li>`;
   }).join('')}</ol></nav>`;
   return `${Live.studyIdentity?Live.studyIdentity(s):''}${Live.studyStatus(s,'processing')}${progressBar}<div class="study-hub">${Live.studyIdentity?'':heading('Study progress','')}<div class="hub-layout"><div class="hub-main">${sent?`<section class="hub-sent" aria-label="Corrections sent"><h2>Sent ${sent.items.length} ${sent.items.length===1?'correction':'corrections'} · rerunning as revision ${sent.revision}</h2><ul>${sent.items.map(i=>`<li><strong>${esc(i.title)}</strong><span>${esc(i.changes)}</span></li>`).join('')}</ul>${sent.comment?`<p>${esc(sent.comment)}</p>`:''}<p class="small">The review opens again with these applied; nothing can be approved while it reruns.</p></section>`:''}${finished?'':`<section class="hub-next ${finished?'is-complete':''}" aria-label="${finished?'Study complete':'Current step'}"><div><span class="hub-eyebrow">${finished?'Completed':archived?'Archived':failed?'Needs attention':gate?'Up next':notStarted?'Getting started':'In progress'}</span><h2>${esc(current)}</h2>${failed&&globalThis.BasisErrorHelp?BasisErrorHelp.html(p?.failure_code,s.failure_reason||p?.failure||detail):`<p>${esc(detail)}</p>`}</div>${action}</section>`}${Live.studyDeliverables?Live.studyDeliverables(s):''}${(finished||archived)&&Live.studyIdentity?'':`<section class="panel hub-steps" aria-label="Study progress steps"><div class="panel-head"><h2>Study steps</h2><span class="small">${done} of ${list.length} complete</span></div><ol>${list.map((step,i)=>{
    const content=`<span class="hub-step-marker" aria-hidden="true">${step.state==='done'?'✓':i+1}</span><span class="hub-step-name"><strong>${esc(step.label)}</strong><small>${esc(step.hint)}</small></span><span class="hub-step-status">${esc(step.status)}</span>${step.canOpen?icon('chevron',15):''}`;
    return `<li class="${step.state==='active'?'is-current':''} ${step.state==='done'?'is-done':''} step-tone-${toneOf(step.status)}">${step.canOpen?`<a href="${Live.route(step.destination,s.id)}" ${step.state==='active'?'aria-current="step"':''}>${content}</a>`:`<div ${step.state==='active'?'aria-current="step"':'aria-disabled="true"'}>${content}</div>`}</li>`;
   }).join('')}</ol></section>`}${Live.studyActivity?Live.studyActivity(s):''}${p?.flagged_photos?.length?`<div class="note"><h3>${p.flagged_photos.length} photos need attention</h3><p>These photos remain available in the ${G.componentsGate(s)?'components':'rooms'} review.</p></div>`:''}${Live.studyTools?Live.studyTools(s):''}${w?.error&&w.error.status!==404?`<p class="live-upload-error">Status unavailable: ${esc(Live.errorText(w.error))}. Showing the last confirmed state.</p>`:''}</div><aside class="hub-details">${Live.studyDetails?Live.studyDetails(s,person):`<section class="panel panel-body"><h2>Study details</h2><dl><div><dt>Study</dt><dd>${esc(s.property_name)}</dd></div>${person?`<div><dt>Created by</dt><dd>${esc(person.name)}${person.email&&person.email!==person.name?`<small>${esc(person.email)}</small>`:''}</dd></div>`:''}<div><dt>Created</dt><dd>${Live.when(s.created_at)}</dd></div><div><dt>Last update</dt><dd>${Live.when(s.updated_at)}</dd></div><div><dt>Revision</dt><dd>${s.revision}</dd></div></dl></section>`}<section class="panel panel-body"><h2>Source files</h2><div class="hub-file-row"><span>Appraisal</span><strong>${confirmed.some(d=>d.kind==='appraisal')?'Uploaded':'Missing'}</strong></div><div class="hub-file-row"><span>Photos</span><strong>${photos.length}</strong></div><a class="screen-link" href="${Live.route('files',s.id)}">View files ${icon('arrow',14)}</a></section></aside></div>${!finished&&!archived?`<div class="actionbar"><p>${gate?'Decisions apply to revision '+s.revision+'.':notStarted?'Confirm the appraisal and photos before starting.':'Progress updates automatically.'}</p>${!notStarted?'<button class="button quiet danger small" data-live="cancel">Cancel study</button>':''}</div>`:''}</div>`;
  });
 };
 // Upload · Appraisal · Rooms · Components · Assets · Results · Report & sign (no Components step for a
 // study with the single rooms review).
 Live.studyStatus=(s,view=state.screen)=>{
  // Report & sign states the report's status once, in its own panel; the strip would repeat it.
  // On a finished study's review pages the header badge and the Report & sign step already say
  // it (Update required, Signed), so the strip is kept for progress, results and the records.
  if(view==='report')return '';
  if(s.status==='completed'&&['appraisal','rooms','components','assets','asset-detail','viewer','files'].includes(view))return '';
  const context=G.reviewContext(s,view,Live.reportStateEntry?.(s));if(!context)return '';
  // r26: "Update required" (a signed report is out of date) is said once, on Study progress with its
  // action; elsewhere the header badge (a link to Report & sign) and the step bar already say it.
  // The next step after approval (add details, sign) keeps its strip: it is where the engineer goes next.
  if(s.status==='completed'&&view!=='processing'&&Live.delivery(s).label==='Update required')return '';
  return `<section class="study-status study-status--${context.tone}" aria-label="Study status"><span class="study-status-symbol" aria-hidden="true">${icon(context.tone==='complete'?'check':context.tone==='attention'?'help':'eye',18)}</span><div class="study-status-copy"><div class="study-status-title"><h2>${esc(context.title)}</h2></div><p>${esc(context.detail)}</p></div>${context.action?`<a class="button study-status-action" href="${Live.route(context.action,s.id)}">${esc(context.actionLabel||'Study progress')}${icon('arrow',16)}</a>`:''}</section>`;
 };
 // This page's own access, next to its title: read-only (approved, or not open yet) and, on a
 // completed study, the way to change it. The study-wide report notice stays in the strip above.
 Live.gateAccess=(s,view=state.screen)=>{
  const context=G.reviewContext(s,view,Live.reportStateEntry?.(s));if(!context?.readOnly)return '';
  const reopen=Live.reopenButton?Live.reopenButton(s,view):'';
  return `<div class="gate-access"><span class="study-access">${icon('eye',14)}${s.status==='completed'?'Approved · read-only':'Read-only'}</span>${reopen.replace('button study-status-action','button small')}</div>`;
 };
 Live.steps=s=>`${Live.studyStatus(s)}<nav class="steps study-navigation" aria-label="Study steps">${G.navigation(s,state.screen,Live.reportStateEntry?.(s)).map(({label,state:st,selected,status,tone,destination,available},i)=>{
  const content=`<span class="step-marker" aria-hidden="true">${st==='done'?'✓':i+1}</span><div class="step-copy"><strong>${label}</strong><small>${status}</small></div>`;
  const attrs=`class="step ${st==='done'?'done':''} ${st==='active'?'workflow-current':''} ${selected?'is-selected':''} step-tone-${tone||'idle'}" ${selected?'aria-current="page"':''}`;
  return available?`<a href="${s.id?Live.route(destination,s.id):'#upload'}" ${attrs}>${content}</a>`:`<span ${attrs} aria-disabled="true">${content}</span>`;
 }).join('')}</nav>${Live.gateAccess(s)}`;

 /* ---------- files ---------- */
 studyFiles=function(){
  return Live.withStudy(s=>{const docs=s.documents||[],groups=[['appraisal','Appraisal'],['photo','Property photos'],['other','Supporting documents']];
   return `${Live.steps(s)}<div class="study-files"><header class="review-heading"><div><p>${esc(s.property_name)} <span>Study materials</span></p><h1>Uploaded files</h1></div><a class="button" href="${Live.route('processing',s.id)}">Study progress</a></header>${(()=>{const group=([kind,label])=>{const list=docs.filter(d=>d.kind===kind);return list.length?`<section class="study-files-group" data-files-group="${kind}"><h2 class="live-files-heading">${label} <span>${list.length}</span></h2><div class="study-file-grid" data-file-kind="${kind}">${list.map(d=>`<article class="study-file-card"><button class="file-preview-trigger" data-live="open-doc" data-value="${esc(d.id)}" aria-label="${(Live.isPdf(d)||Live.isAppraisalSource(d))?'Preview':'Open'} ${esc(d.filename)}">${d.kind==='photo'?Live.photo(d.id,d.filename):(Live.isPdf(d)||Live.isAppraisalSource(d))?Live.pdfThumbnail(d):`<div class="file-document-thumbnail">${icon('file',46)}<strong>${esc(d.filename)}</strong></div>`}</button><div class="study-file-meta"><strong>${esc(d.filename)}</strong><span>${(d.bytes/1048576).toFixed(1)} MB <i>·</i> ${d.uploaded_at?'Confirmed':'Upload not confirmed'}</span><div><button data-live="open-doc" data-value="${esc(d.id)}">${(Live.isPdf(d)||Live.isAppraisalSource(d))?'Preview':'Open'}</button><button data-live="download-doc" data-value="${esc(d.id)}">${icon('download',15)}Download</button></div></div></article>`).join('')}</div></section>`:''};
   // The appraisal (and any other document) in a narrow column beside the photos, so the photos start at the top.
   const side=[groups[0],groups[2]].map(group).join(''),photos=group(groups[1]);
   return side||photos?`<div class="study-files-layout">${side?`<div class="study-files-side">${side}</div>`:''}${photos}</div>`:'<div class="file-empty"><h3>No files yet</h3></div>'})()}</div>`});
 };
 Live.openDocument=async(docId,page=1)=>{
  const d=(L.study?.documents||[]).find(x=>x.id===docId);if(!d)return;
  const study=L.study,screen=state.screen;
  try{
   if(Live.isAppraisalSource(d)){
    const source=await Live.appraisalSource(d,study);if(L.study?.id!==study.id||state.screen!==screen)return;
    if(source.url)openPdfPreview({name:source.filename,url:source.url,initialPage:page,uploaded:true,description:source.kind==='zip'?'Report from UAD package':source.kind==='xml'?'Companion PDF · values sourced from MISMO XML':undefined});
    else modal('Appraisal source',`<p>${esc(source.message)}</p><a class="button" href="${esc(source.originalUrl)}" download="${esc(d.filename)}">Download original ${esc(source.kind.toUpperCase())}</a>`,closeBtn);
   }else{const url=await blobUrl(docId,L.studyId,/^image\//.test(d.content_type||''));if(Live.isPdf(d))openPdfPreview({name:d.filename,url,initialPage:page,uploaded:true});else window.open(url,'_blank','noopener')}
  }catch(e){toast('The file could not be opened: '+Live.errorText(e))}
 };

 /* ---------- actions ---------- */
 async function act(name,el){
  const value=el.dataset.value;
  switch(name){
   case 'google':try{if(L.returnTo)sessionStorage.setItem('basis-live-return',L.returnTo);const u=await BasisAuth.signInWithGoogle(C.signInFlow);if(u)await afterSignIn()}catch(e){L.authError=e.message;render()}break;
   case 'create-account':emailSignIn(true);break;
   case 'sign-out':await BasisAuth.signOut();L.cache.clear();L.blobReady.forEach(url=>URL.revokeObjectURL(url));L.blobReady.clear();L.blobs.clear();L.study=null;L.studyId=null;sessionStorage.removeItem('basis-live-study');location.hash='#login';render();break;
   case 'reload-studies':studiesEntry(true);break;
   case 'reload-me':Live.refreshMe?.();break;
   case 'reload-study':Live.reloadStudy();break;
   case 'studies-filter':L.studiesFilter=value;render();break;
   case 'open-doc':Live.openDocument(value);break;
   case 'download-doc':{const d=L.study.documents.find(x=>x.id===value);const url=await blobUrl(value);const a=Object.assign(document.createElement('a'),{href:url,download:d.filename});document.body.append(a);a.click();a.remove();break}
   case 'start':await guarded('start',async()=>{await api.start(L.studyId,L.startKey||=BasisApi.newKey());toast('Study started.');await Live.reloadStudy();startPolling();render()});break;
   case 'retry':if(!BasisErrorHelp.failureState(L.study,L.workflow?.data?.status||{},Live.me?.()?.memberships||[]).canRetry)break;await guarded('retry',async()=>{const retryContext=JSON.stringify([L.studyId,L.study.revision,L.workflow?.data?.status?.failed_stage,L.workflow?.data?.status?.failure]);if(L.retryContext!==retryContext){L.retryContext=retryContext;L.retryKey=BasisApi.newKey()}const r=await api.retry(L.studyId,L.retryKey);L.retryContext=null;L.retryKey=null;toast(r.already_requested?'A retry was already requested.':`Retrying ${STAGE[r.stage]||r.stage}.`);await Live.reloadStudy();stopPolling();startPolling();render()});break;
   case 'cancel':{const m=modal('Cancel this study?','<p>Processing stops. Documents and decisions so far stay on the study.</p><div class="field"><label for="live-cancel-reason">Reason (optional)</label><input id="live-cancel-reason"></div>',`${closeBtn}<button class="button danger" id="live-confirm-cancel">Cancel study</button>`);m.querySelector('#live-confirm-cancel').onclick=()=>guarded('cancel',async()=>{await api.cancel(L.studyId,m.querySelector('#live-cancel-reason').value.trim()||null);m.remove();toast('Cancellation requested.');await Live.reloadStudy();render()});break}
   default:Live.actions[name]?.(el,value);
  }
 }
 Live.actions={};
 // A failed action is shown to the user and, unless it was an API refusal the client already
 // recorded, reported with the action's name.
 async function guarded(name,fn){if(L.busy.has(name))return;L.busy.add(name);try{await fn()}catch(e){toast(Live.errorText(e));if(!apiRefusal(e))reportError(e,{action:name})}finally{L.busy.delete(name)}}
 Live.guarded=guarded;
 root.addEventListener('click',e=>{
  const el=e.target.closest('[data-live]');if(el){e.preventDefault();e.stopImmediatePropagation();act(el.dataset.live,el);return}
  const design=e.target.closest('[data-action="preview-analysis"]');if(design&&state.screen==='upload'){e.preventDefault();e.stopImmediatePropagation();startStudy()}
 },true);
 root.addEventListener('submit',e=>{if(e.target.matches('[data-live-form="email"]')){e.preventDefault();emailSignIn(false)}},true);
 root.addEventListener('input',e=>{if(e.target.id==='live-study-search'){L.studiesQuery=e.target.value;const pos=e.target.selectionStart;render();const box=root.querySelector('#live-study-search');box?.focus();box?.setSelectionRange(pos,pos)}},true);
 root.addEventListener('change',e=>{if(e.target.id==='live-study-sort'){L.studiesSort=e.target.value;render()}},true);

 /* ---------- start ---------- */
 // Another account signed in: nothing cached for the previous one may show.
 BasisAuth.onChange(user=>{if(L.user&&user?.uid!==L.user.uid)L.cache.clear();L.user=user;if(user)Live.loadCaps();if(L.authReady)render()});
 BasisAuth.init(C.firebase).then(()=>{L.authReady=true;L.user=BasisAuth.user;if(L.user)Live.loadCaps();L.authError=BasisAuth.redirectError||'';const back=sessionStorage.getItem('basis-live-return');sessionStorage.removeItem('basis-live-return');if(L.user&&back&&/^#(login)?$/.test(location.hash||'#')){location.hash=back}render()},e=>{L.authError=`The Firebase SDK could not load (${e.message}). Check the network connection.`;render()});
 render();
})();
