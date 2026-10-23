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
 * activity history, creator names, client name and address, cost sources.
 */
(function(){
 'use strict';
 const C=globalThis.BASIS_CONFIG;if(!C||C.mode!=='live')return;
 document.documentElement.dataset.basisMode='live';
 const L={authReady:false,user:null,authError:'',studies:null,studiesError:null,studyId:null,docId:null,study:null,cache:new Map(),blobs:new Map(),poller:null,workflow:null,upload:null,returnTo:null,busy:new Set(),corrections:{},viewer:{mode:'spotlight',focusLabel:null,selectedId:null,tool:null,addLabel:null},assetsFilter:'all',studiesFilter:'All studies',studiesQuery:'',studiesSort:'updated'};
 const api=BasisApi.createClient({baseUrl:C.apiBase,getToken:()=>BasisAuth.getToken(),onUnauthorized:()=>{if(BasisAuth.user)toast('Your session has expired. Sign in again.')}});
 const Live=globalThis.BasisLive={config:C,api,state:L};
 const STAGE={uploading:'Waiting for documents',extracting:'Extracting the appraisal and analyzing photos',appraisal_review:'Appraisal review',rooms_review:'Rooms review',components_processing:'Preparing components',components_review:'Components review',assets_processing:'Building the asset schedule',assets_review:'Assets review',exporting:'Preparing the schedule',completed:'Completed'};
 const STATUS={draft:'Draft',processing:'Processing',waiting_review:'Ready for review',stalled:'Needs attention',completed:'Completed',archived:'Archived',failed:'Failed'};
 // The gates (live/gates.js): the rooms review, then the components review, unless the
 // study's workflow has the single rooms review (studies.components_gate false).
 const G=globalThis.BasisGates,GATE_SCREEN=G.GATE_SCREEN;
 const PUBLIC=new Set(['landing','login']);
 Live.STAGE=STAGE;Live.STATUS=STATUS;Live.GATE_SCREEN=GATE_SCREEN;Live.gates=G;
 Live.labelName=l=>String(l||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());
 Live.when=v=>v?new Intl.DateTimeFormat('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}).format(new Date(v)):'—';
 Live.route=(screen,studyId=L.studyId,docId)=>`#${screen}${studyId?'/'+encodeURIComponent(studyId):''}${docId?'/'+encodeURIComponent(docId):''}`;
 Live.go=(screen,studyId=L.studyId,docId)=>{const hash=Live.route(screen,studyId,docId);if(location.hash===hash)render();else location.hash=hash};
 Live.errorText=e=>e?(e.status?`${e.message}${e.requestId?` (request ${e.requestId})`:''}`:e.message||String(e)):'';
 const nextScreen=G.nextScreen;
 Live.nextScreen=nextScreen;
 // A study's gate is open for a decision when it waits at that stage.
 Live.gateOpen=(stage)=>L.study&&L.study.current_stage===stage&&L.study.status==='waiting_review';

 /* ---------- data: one cached promise per key; screens render a loading state ---------- */
 function load(key,loader,{force=false}={}){
  const hit=L.cache.get(key);if(hit&&!force)return hit;
  const entry={promise:null,data:undefined,error:null,done:false};L.cache.set(key,entry);
  entry.promise=loader().then(d=>{entry.data=d;entry.done=true;render();return d},e=>{entry.error=e;entry.done=true;render();throw e});
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
 const REVALIDATE=new Set(['appraisal','rooms','components','viewer','assets','asset-detail','complete','files']);
 let entered='';
 function revalidateOnEntry(){
  const at=state.screen+'/'+L.studyId;if(at===entered)return;entered=at;
  if(!REVALIDATE.has(state.screen)||!L.studyId)return;
  const key='study:'+L.studyId,entry=L.cache.get(key);
  if(!entry?.done||entry.error||entry.revalidating)return;// not loaded yet: the load is fresh
  entry.revalidating=true;
  api.getStudy(L.studyId).then(fresh=>{
   if(L.cache.get(key)!==entry||JSON.stringify(fresh)===JSON.stringify(entry.data))return;
   entry.data=fresh;render();
  },()=>{}).finally(()=>{entry.revalidating=false});// a failed check keeps the cached study
 }
 /* Document bytes (photos, the appraisal) need the bearer token, so they are fetched and
  * shown from object URLs, at most six at a time. <img data-live-doc="id"> is filled in. */
 const queue=[];let active=0;
 function blobUrl(docId){
  if(L.blobs.has(docId))return L.blobs.get(docId);
  const p=new Promise((resolve,reject)=>{queue.push({docId,studyId:L.studyId,resolve,reject});pump()});L.blobs.set(docId,p);p.catch(()=>L.blobs.delete(docId));return p;
 }
 function pump(){
  while(active<6&&queue.length){const job=queue.shift();active++;
   api.documentContent(job.studyId,job.docId).then(r=>r.blob()).then(b=>{const url=URL.createObjectURL(b);L.blobReady.set(job.docId,url);job.resolve(url)},job.reject).finally(()=>{active--;pump()})}
 }
 L.blobReady=new Map();
 Live.blobUrl=blobUrl;
 function hydrateImages(scope=root){
  for(const img of scope.querySelectorAll('img[data-live-doc]:not([src])')){
   blobUrl(img.dataset.liveDoc).then(url=>{if(img.isConnected&&!img.src)img.src=url},()=>{img.replaceWith(Object.assign(document.createElement('div'),{className:'live-photo-missing',textContent:'Photo unavailable'}))});
  }
 }
 Live.hydrateImages=hydrateImages;
 // A photo already fetched gets its src at once, so re-rendering does not blink it.
 Live.photo=(docId,alt='',cls='')=>{const ready=L.blobReady.get(docId);return `<img data-live-doc="${esc(docId)}" ${ready?`src="${ready}"`:''} alt="${esc(alt)}" class="${cls}" decoding="async">`};

 /* ---------- routing and the sign-in gate ---------- */
 function parseHash(){
  const raw=location.hash.slice(1)||'landing',[screen,study,doc]=raw.split('/');
  state.screen=screen||'landing';
  if(study){const id=decodeURIComponent(study);if(id!==L.studyId){L.studyId=id;L.study=null;L.corrections={};stopPolling()}sessionStorage.setItem('basis-live-study',id)}
  else if(!L.studyId)L.studyId=sessionStorage.getItem('basis-live-study');
  L.docId=doc?decodeURIComponent(doc):null;
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
  else baseRender();
  afterRender();
 };
 Live.render=()=>render();
 function afterRender(){
  const bar=root.querySelector('.reviewbar');if(bar)bar.outerHTML=liveBar();
  else root.insertAdjacentHTML('afterbegin',liveBar());
  // The design's sidebar names a sample engineer; show who is signed in.
  if(L.user){const who=L.user.email||L.user.displayName||'Signed in',initials=who.replace(/@.*/,'').split(/[^a-z0-9]+/i).filter(Boolean).slice(0,2).map(w=>w[0].toUpperCase()).join('')||'?';
   for(const el of root.querySelectorAll('.account strong'))el.textContent=who;
   for(const el of root.querySelectorAll('.account .avatar, .account-menu .avatar'))el.textContent=initials;
   for(const el of root.querySelectorAll('.account .account-role'))el.textContent='Signed in';
   root.querySelector('#account-menu-trigger')?.setAttribute('aria-label',who+' — account menu');}
  for(const link of root.querySelectorAll('aside a[href="#activity"], aside a[href="#admin"]'))if(!link.querySelector('.live-mock-tag'))link.insertAdjacentHTML('beforeend','<span class="live-mock-tag" title="Not connected to the API yet: sample data">Mock</span>');
  const crumb=root.querySelector('.breadcrumbs .strong');if(crumb&&L.study&&['appraisal','rooms','components','viewer','assets','asset-detail','complete','processing','files'].includes(state.screen))crumb.textContent=L.study.property_name;
  for(const meta of root.querySelectorAll('.topbar .right-meta'))meta.innerHTML=L.study&&L.studyId&&state.screen!=='dashboard'&&state.screen!=='library'?`<span>Revision ${L.study.revision}</span>${statusBadge(L.study.status)}`:'';
  hydrateImages();
  Live.afterRender.forEach(fn=>fn());
 }
 Live.afterRender=[];
 function liveBar(){
  const host=(()=>{try{return new URL(C.apiBase).host}catch{return C.apiBase}})();
  const mock=new URL(location.href);mock.searchParams.delete('mode');
  return `<div class="reviewbar live-bar"><div class="row"><strong>Basis</strong><span class="live-badge">Live</span><span class="small">API ${esc(host)}${C.firebase.emulatorHost?' · Auth emulator':''}</span></div><div class="row">${L.user&&Live.barExtra?Live.barExtra():''}${L.user?`<span class="small">${esc(L.user.email||L.user.displayName||'Signed in')}</span><button class="button quiet small" data-live="sign-out">Sign out</button>`:''}<a href="${esc(mock.toString())}">Design review (mock)</a></div></div>`;
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
 const FILTERS={'All studies':()=>true,'Needs review':s=>['waiting_review','stalled','failed'].includes(s.status),'Processing':s=>s.status==='processing','Completed':s=>s.status==='completed','Drafts':s=>s.status==='draft'};
 function studiesTable(rows){
  return `<div class="collection-table-wrap"><table class="collection-table"><caption class="sr-only">Studies</caption><thead><tr><th scope="col">Study</th><th scope="col">Status</th><th scope="col">Revision</th><th scope="col">Updated</th><th scope="col"><span class="sr-only">Open</span></th></tr></thead><tbody>${rows.map(s=>`<tr><td class="collection-study-cell"><a class="collection-entry" href="${Live.route(nextScreen(s),s.id)}"><span><strong>${esc(s.property_name)}</strong><span class="collection-address">${esc(STAGE[s.current_stage]||s.current_stage)}${s.failure_reason?' · '+esc(s.failure_reason):''}</span></span></a></td><td class="collection-state-cell">${deskStatus(s)}</td><td class="num">${s.revision}</td><td class="collection-date-cell"><time datetime="${esc(s.updated_at)}">${Live.when(s.updated_at)}</time></td><td class="collection-open-cell"><a class="collection-open" href="${Live.route(nextScreen(s),s.id)}" aria-label="Open ${esc(s.property_name)}"><span>${s.status==='waiting_review'?'Review':s.status==='completed'?'View schedule':'Open'}</span>${icon('arrow',17)}</a></td></tr>`).join('')||`<tr><td colspan="5"><div class="collection-empty">${icon('search',28)}<h2>${L.studies?.length?'No matching studies':'No studies yet'}</h2><p>${L.studies?.length?'Try another name or status.':'Start a study to upload an appraisal and property photos.'}</p>${L.studies?.length?'':`<a class="button primary" href="#upload">${icon('plus',15)}Start a study</a>`}</div></td></tr>`}</tbody></table></div>`;
 }
 function studiesView(title,subtitle){
  const e=studiesEntry();if(!e.done)return Live.loading('Loading your studies…');
  // Removed from the firm meanwhile: re-read /v1/me (the gate then explains) and the list.
  if(e.error?.kind==='no-firm'){Live.invalidate('studies');Live.refreshMe?.();return Live.loading('Checking your firm…')}
  if(e.error)return Live.failed(e.error,'reload-studies');
  L.studies=Live.firmStudies?Live.firmStudies(e.data):e.data;const q=L.studiesQuery.trim().toLowerCase();
  const matched=L.studies.filter(s=>!q||s.property_name.toLowerCase().includes(q));
  const rows=matched.filter(FILTERS[L.studiesFilter]).sort((a,b)=>L.studiesSort==='name'?a.property_name.localeCompare(b.property_name):L.studiesSort==='oldest'?a.updated_at.localeCompare(b.updated_at):b.updated_at.localeCompare(a.updated_at));
  const ready=L.studies.filter(s=>s.status==='waiting_review');
  return `<div class="collection-desk live-studies"><header class="collection-header"><div><h1>${title}</h1><p>${subtitle}</p></div><a class="collection-create" href="#upload">${icon('plus',17)}Start a study</a></header>${ready.length?`<div class="attention"><div class="row">${icon('clock',19)}<div><span class="strong">${ready.length===1?esc(ready[0].property_name)+' is ready for '+esc((STAGE[ready[0].current_stage]||'review').toLowerCase())+'.':ready.length+' studies are ready for review.'}</span><p>The study waits at each review gate for an engineer’s decision.</p></div></div><a class="button" href="${Live.route(nextScreen(ready[0]),ready[0].id)}">Review ${esc(ready[0].property_name)}</a></div>`:''}<div class="collection-search-area"><label class="collection-search-field">${icon('search',21)}<input id="live-study-search" aria-label="Search studies" placeholder="Search by property name…" value="${esc(L.studiesQuery)}"></label></div><section class="collection-panel" aria-label="Studies"><div class="collection-toolbar"><div class="collection-status-filters" aria-label="Filter studies by status">${Object.keys(FILTERS).map(f=>`<button type="button" data-live="studies-filter" data-value="${f}" aria-pressed="${L.studiesFilter===f}">${f}<span>${matched.filter(FILTERS[f]).length}</span></button>`).join('')}</div><label class="library-sort"><span class="sr-only">Sort studies</span><select id="live-study-sort" aria-label="Sort studies">${[['updated','Last updated'],['oldest','Oldest first'],['name','Property A–Z']].map(([v,l])=>`<option value="${v}" ${L.studiesSort===v?'selected':''}>${l}</option>`).join('')}</select></label></div><div id="live-study-results">${studiesTable(rows)}</div></section><p class="desk-footer">${icon('shield',12)}Studies your workspace membership lets you see.<span>Search, filters and sort run in the browser: the API lists studies by most recent.</span></p></div>`;
 }
 dashboard=()=>studiesView('Your studies','Continue a study or start a new one.');
 studyLibrary=()=>studiesView('Library','Every study in your workspace.');
 archiveStudy=()=>Live.withStudy(s=>{location.replace(Live.route(nextScreen(s),s.id));return Live.loading()});

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
  const appraisals=items.filter(x=>x.item.kind==='appraisal'),photos=items.filter(x=>x.item.kind==='photos'),other=items.filter(x=>x.item.kind==='documents');
  const problems=[];
  if(!state.propertyName?.trim())problems.push('Enter a property name.');
  if(missing.length)problems.push(`Reselect ${missing.length} ${missing.length===1?'file':'files'} (files are not kept after a reload).`);
  if(appraisals.length!==1)problems.push(appraisals.length?'Choose one appraisal PDF.':'Add the appraisal PDF (set its group to Appraisal).');
  else if(extension(appraisals[0].item.name)!=='pdf')problems.push('The appraisal must be a PDF.');
  if(!photos.length)problems.push('Add the property photos.');
  const badPhotos=photos.filter(x=>!PHOTO_TYPES[extension(x.item.name)]);if(badPhotos.length)problems.push(`Photos must be JPEG, PNG, HEIC or WebP: ${badPhotos.map(x=>x.item.name).slice(0,3).join(', ')}.`);
  return {appraisal:appraisals[0],photos,other,problems};
 }
 async function startStudy(){
  if(L.upload?.running)return;
  const plan=uploadPlan();if(plan.problems.length){toast(plan.problems.join(' '));return}
  // One upload run per selection: a retry after a failure reuses the study and the files
  // already confirmed (and the same idempotency keys), so nothing is created twice.
  const run=L.upload&&L.upload.name===state.propertyName.trim()?L.upload:{name:state.propertyName.trim(),key:BasisApi.newKey(),files:new Map()};
  L.upload=run;run.running=true;run.error=null;
  const jobs=[{...plan.appraisal,kind:'appraisal',type:'application/pdf'},...plan.photos.map(x=>({...x,kind:'photo',type:PHOTO_TYPES[extension(x.item.name)]}))];
  for(const job of jobs)if(!run.files.has(job.item.key))run.files.set(job.item.key,{name:job.item.name,kind:job.kind,progress:0,state:'waiting',key:BasisApi.newKey(),doc:null});
  render();
  try{
   if(!run.studyId){const body={property_name:run.name,room_aliases:{}},study=await api.createStudy(Live.createBody?Live.createBody(body):body,run.key);run.studyId=study.id;L.studyId=study.id;Live.invalidate('studies')}
   const pending=jobs.filter(j=>run.files.get(j.item.key).state!=='done');let next=0;
   async function worker(){
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
    }
   }
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
 upload=function(){return designUpload().replace('Design preview. Upload and analysis services are not connected.','Uploads go to the API’s signed URLs; the study starts when every file is confirmed.').replace('>Preview analysis<','>Upload and start<')+`<div id="live-upload-slot">${uploadSummary()}</div>`};
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
   onUpdate:async w=>{L.workflow={id,data:w,error:null};const before=L.study?.status+'/'+L.study?.current_stage;await Live.reloadStudy();if(state.screen==='processing'){const after=L.study?.status+'/'+L.study?.current_stage;if(before!==after&&L.study?.status==='waiting_review')toast(`${STAGE[L.study.current_stage]} is ready.`);render()}},
   onError:e=>{L.workflow={id,data:L.workflow?.data||null,error:e};if(state.screen==='processing')render()},
   shouldStop:w=>['COMPLETED','FAILED','CANCELED','TERMINATED','TIMED_OUT'].includes(String(w?.execution_status).toUpperCase())});
 }
 Live.startPolling=startPolling;Live.stopPolling=stopPolling;
 processing=function(){
  return Live.withStudy(s=>{
   if(s.status!=='draft'&&s.status!=='completed')startPolling();
   const w=L.workflow?.id===s.id?L.workflow:null,p=w?.data?.status||null,// photo counts; its stage/revision can lag the study row after a decision
    docs=s.documents||[],photos=docs.filter(d=>d.kind==='photo'),confirmed=docs.filter(d=>d.uploaded_at);
   const notStarted=w?.error?.status===404||s.status==='draft';
   // The study row is the authority for an open gate; the workflow snapshot can predate a decision.
   const gate=s.status==='waiting_review'&&GATE_SCREEN[s.current_stage]?s.current_stage:null;
   const total=p?(p.pending_photos+p.completed_photos):photos.length;
   const title=s.status==='failed'?'Processing stopped':s.status==='stalled'?'This study needs attention':gate?`${STAGE[gate]} is ready`:notStarted?'Ready to start':s.status==='completed'?'Study completed':STAGE[s.current_stage]||'Processing';
   // After the rooms review approves the grouping the room step counts each room's
   // components, and the components review opens when it and SAM 3's outlines are done.
   const preparing=s.current_stage==='components_processing'&&s.status==='processing';
   const desc=s.status==='failed'?(s.failure_reason||p?.failure||'The workflow stopped at a stage. Retry it to continue from there.'):s.status==='stalled'?'The processing budget was reached or a step needs a decision. Review the flagged photos, then retry.':gate?'The study is waiting for your decision.':notStarted?`${confirmed.length} of ${docs.length} documents confirmed. Start the study when the appraisal and photos are in.`:s.status==='completed'?'Every review is approved. The schedule is final for revision '+s.revision+'.':preparing?'Basis is counting each room’s components across its photos, using the rooms you approved, and finishing the photo outlines. The components review opens when that is done, usually within a few minutes.':'Basis is working on this study. You can leave this screen; it keeps running.';
   return `${Live.steps(s)}${heading(esc(title),esc(desc),`<a class="button" href="#dashboard">Back to studies</a>`)}<div class="two-col"><section class="panel"><div class="panel-head"><h2>Study progress</h2>${statusBadge(s.status)}</div><div class="panel-body"><div class="row between"><h3>Property photos</h3><span class="small num">${p?`${p.completed_photos} of ${total} analyzed`:`${photos.length} uploaded`}</span></div>${p&&total?`<div class="progress" style="margin-top:17px"><span style="width:${Math.round(100*p.completed_photos/total)}%"></span></div>`:''}<div class="timeline" style="margin-top:25px">${G.timeline(s).map((stage,n)=>{const order=G.ORDER,at=order.indexOf(s.current_stage),i=order.indexOf(stage),done=i<at||s.status==='completed',current=i===at&&s.status!=='completed';return `<div><span class="check">${done?'✓':current?(s.status==='failed'||s.status==='stalled'?'!':'•'):n+1}</span><div><strong class="small">${STAGE[stage]}</strong><p>${current?(gate===stage?`<a href="${Live.route(GATE_SCREEN[stage])}">Open the review</a>`:s.status==='failed'?'Stopped here.':'In progress.'):done?'Done.':'Not started.'}</p></div></div>`}).join('')}</div>${p?.flagged_photos?.length?`<div class="note" style="margin-top:20px"><h3>${p.flagged_photos.length} flagged ${p.flagged_photos.length===1?'photo':'photos'}</h3><p>These photos could not be analyzed fully. They stay visible in the ${G.componentsGate(s)?'components':'rooms'} review.</p></div>`:''}${w?.error&&w.error.status!==404?`<p class="live-upload-error">Status unavailable: ${esc(Live.errorText(w.error))}. The last confirmed state is shown; checking again…</p>`:''}</div></section><aside class="stack" style="align-content:start"><div class="panel panel-body"><h3>${esc(s.property_name)}</h3><div class="line-item"><span>Revision</span><strong>${s.revision}</strong></div><div class="line-item"><span>Workflow</span><strong>${esc(w?.data?.execution_status||(notStarted?'Not started':'—'))}</strong></div>${p?`<div class="line-item"><span>Remaining processing budget</span><strong class="num">$${Number(p.budget_remaining_usd).toFixed(2)}</strong></div>`:''}<div class="line-item"><span>Last update</span><strong>${Live.when(s.updated_at)}</strong></div><p class="footnote">The processing budget caps model spend for this study; it is not an invoice.</p></div><div class="panel panel-body"><h3>Documents</h3><div class="line-item"><span>Appraisal</span><strong>${docs.filter(d=>d.kind==='appraisal'&&d.uploaded_at).length?'Confirmed':'Missing'}</strong></div><div class="line-item"><span>Photos</span><strong>${photos.filter(d=>d.uploaded_at).length}</strong></div><a class="screen-link" href="${Live.route('files')}">All files</a></div></aside></div><div class="actionbar"><p>${gate?'Decisions apply to revision '+s.revision+'.':s.status==='failed'||s.status==='stalled'?'Retry continues from the stage that stopped.':'Progress updates automatically.'}</p><div class="row">${!['completed','archived'].includes(s.status)&&!notStarted?`<button class="button danger" data-live="cancel">Cancel study</button>`:''}${s.status==='failed'||s.status==='stalled'?`<button class="button primary" data-live="retry">Retry</button>`:''}${notStarted&&confirmed.length?`<button class="button primary" data-live="start">Start the study</button>`:''}${gate?`<a class="button primary" href="${Live.route(GATE_SCREEN[gate])}">Open ${esc(STAGE[gate].toLowerCase())}</a>`:''}${s.status==='completed'?`<a class="button primary" href="${Live.route('complete')}">View the schedule</a>`:''}</div></div>`;
  });
 };
 // Upload · Appraisal · Rooms · Components · Assets · Schedule (no Components step for a
 // study with the single rooms review).
 Live.steps=s=>`<nav class="steps" aria-label="Study progress">${G.steps(s).map(({label,screen,state:st},i)=>`<a href="${Live.route(screen)}" class="step ${st}"><span>${st==='done'?'✓':i+1}</span>${label}</a>`).join('')}</nav>`;

 /* ---------- files ---------- */
 studyFiles=function(){
  return Live.withStudy(s=>{const docs=s.documents||[],groups=[['appraisal','Appraisal'],['photo','Property photos'],['other','Supporting documents']];
   return `<div class="study-files"><header class="review-heading"><div><p>${esc(s.property_name)} <span>Study materials</span></p><h1>Files</h1><div>Everything uploaded for this property.</div></div></header>${groups.map(([kind,label])=>{const list=docs.filter(d=>d.kind===kind);return list.length?`<h2 class="live-files-heading">${label} <span>${list.length}</span></h2><div class="study-file-grid">${list.map(d=>`<article class="study-file-card"><button class="file-preview-trigger" data-live="open-doc" data-value="${esc(d.id)}" aria-label="Open ${esc(d.filename)}">${d.kind==='photo'?Live.photo(d.id,d.filename):`<div class="file-document-thumbnail">${icon('file',46)}<strong>${esc(d.filename)}</strong></div>`}</button><div class="study-file-meta"><strong>${esc(d.filename)}</strong><span>${(d.bytes/1048576).toFixed(1)} MB <i>·</i> ${d.uploaded_at?'Confirmed':'Upload not confirmed'}</span><div><button data-live="open-doc" data-value="${esc(d.id)}">Open</button><button data-live="download-doc" data-value="${esc(d.id)}">${icon('download',15)}Download</button></div></div></article>`).join('')}</div>`:''}).join('')||'<div class="file-empty"><h3>No files yet</h3></div>'}</div>`});
 };
 Live.openDocument=async(docId,page=1)=>{
  const d=(L.study?.documents||[]).find(x=>x.id===docId);if(!d)return;
  try{const url=await blobUrl(docId);if(d.content_type==='application/pdf')openPdfPreview({name:d.filename,url,initialPage:page});else window.open(url,'_blank','noopener')}catch(e){toast('The file could not be opened: '+Live.errorText(e))}
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
   case 'retry':await guarded('retry',async()=>{const r=await api.retry(L.studyId);toast(r.already_requested?'A retry was already requested.':`Retrying ${STAGE[r.stage]||r.stage}.`);await Live.reloadStudy();stopPolling();startPolling();render()});break;
   case 'cancel':{const m=modal('Cancel this study?','<p>Processing stops. Documents and decisions so far stay on the study.</p><div class="field"><label for="live-cancel-reason">Reason (optional)</label><input id="live-cancel-reason"></div>',`${closeBtn}<button class="button danger" id="live-confirm-cancel">Cancel study</button>`);m.querySelector('#live-confirm-cancel').onclick=()=>guarded('cancel',async()=>{await api.cancel(L.studyId,m.querySelector('#live-cancel-reason').value.trim()||null);m.remove();toast('Cancellation requested.');await Live.reloadStudy();render()});break}
   default:Live.actions[name]?.(el,value);
  }
 }
 Live.actions={};
 async function guarded(name,fn){if(L.busy.has(name))return;L.busy.add(name);try{await fn()}catch(e){toast(Live.errorText(e))}finally{L.busy.delete(name)}}
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
 BasisAuth.onChange(user=>{if(L.user&&user?.uid!==L.user.uid)L.cache.clear();L.user=user;if(L.authReady)render()});
 BasisAuth.init(C.firebase).then(()=>{L.authReady=true;L.user=BasisAuth.user;L.authError=BasisAuth.redirectError||'';const back=sessionStorage.getItem('basis-live-return');sessionStorage.removeItem('basis-live-return');if(L.user&&back&&/^#(login)?$/.test(location.hash||'#')){location.hash=back}render()},e=>{L.authError=`The Firebase SDK could not load (${e.message}). Check the network connection.`;render()});
 render();
})();
