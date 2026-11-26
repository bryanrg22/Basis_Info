// The measure tool's Draft it and facade mode (web/live/property.js, EVAL_REPORT 111) on the sample
// API through the real API client, in a VM, driven through the same document listeners a browser
// calls (the harness of measure-screen.test.cjs): a box dragged on the aerial, the draft received
// and drawn with a length on every edge, edited with its original kept, sent; a refusal shown in
// plain words; Undo and Redo of a draft; the facade's corners clicked on a photo, the wall's length
// from the lidar shell, the openings counted and measured as the server measures them, a corner
// dragged, an opening off the wall, a photo with no lens; the correction sent and accepted.
// The pictures as a browser draws them are the headless captures of EVAL_REPORT 111.
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const WEB=path.join(__dirname,'..'),read=f=>fs.readFileSync(path.join(WEB,f),'utf8');
const A=require('../live/sample-api.js'),PM=require('../live/property-model.js'),PS=require('../live/sample-property.js');
const NOW=Date.parse('2026-11-23T20:00:00Z');

async function boot({variant='confirmed',screen='property',facade=null,caps={}}={}){
 const sample=A.create({state:'assets',now:()=>NOW,facade,fetchAsset:p=>fs.readFileSync(path.join(WEB,p))});sample._inst.propertyState=variant;
 const api=require('../live/api-client.js').createClient({baseUrl:sample.base,fetchImpl:(u,i)=>sample.fetch(u,i),getToken:async()=>'sample-token'});
 const study=await api.getStudy(sample.mainStudyId());
 const cache=new Map(),added=new Map(),toasts=[],listeners={};let scr=screen;
 const load=(key,fn)=>{if(cache.has(key))return cache.get(key);const e={done:false};cache.set(key,e);e.promise=Promise.resolve().then(fn).then(d=>{e.data=d;e.done=true},err=>{e.error=err;e.done=true});return e};
 const Live={api,caps:PM.caps({property_capture:true,public_geo:true,traced_measurement:true,site_drafts:true,facade_measure:true,...caps}),state:{cache,studyId:study.id,study,docId:null,corrections:{}},screens:{},actions:{},afterRender:[],beforeRender:[],load,
  invalidate:p=>{for(const k of [...cache.keys()])if(k.startsWith(p))cache.delete(k)},withStudy:fn=>fn(study),me:()=>({memberships:[{org_id:study.org_id,role:'owner'}]}),
  route:(s,id,doc)=>`#${s}${id?'/'+id:''}${doc?'/'+doc:''}`,when:v=>v?String(v).slice(0,16):'—',loading:t=>`<div class="live-state">${t}</div>`,failed:e=>`<div role="alert">${e?.message}</div>`,
  render(){},screen:()=>scr,labelName:l=>String(l||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase()),gateOpen:stage=>study.current_stage===stage&&study.status==='waiting_review',
  guarded:async(n,fn)=>fn(),errorText:e=>e?.message||'',addCorrection:(stage,c)=>added.set(c.key,{stage,c}),pendingCorrection:(stage,key)=>added.get(key)?.c||null,
  photo:(id,alt,cls)=>`<img data-live-doc="${id}" class="${cls}">`,photoUrl:()=>new Promise(()=>{}),studyTools:s=>''};
 const g={BASIS_CONFIG:{mode:'live'},BasisLive:Live,BasisPropertyModel:PM,BasisReasons:require('../live/review-reasons.js'),Intl,URL,Date,Promise,Map,Set,JSON,Math,Number,String,Array,Object,Boolean,RegExp,Error,encodeURIComponent,decodeURIComponent,console,
  setTimeout:()=>0,clearTimeout(){},matchMedia:()=>({matches:false}),icon:()=>'<svg></svg>',heading:(t,d,a)=>`<h1>${t}</h1>${a||''}`,toast:t=>toasts.push(t),
  esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  document:{addEventListener:(n,fn)=>(listeners[n]||=[]).push(fn),querySelector:()=>null,querySelectorAll:()=>[],getElementById:()=>null,hidden:false}};
 g.globalThis=g;vm.createContext(g);vm.runInContext(read('live/property.js'),g);
 async function page(fn=()=>Live.screens.property()){for(let i=0;i<10;i++){fn();const open=[...cache.values()].filter(e=>!e.done);if(!open.length)break;await Promise.all(open.map(e=>e.promise))}return fn()}
 const items=(await api.assets(study.id,study.revision)).items,asset=l=>items.find(a=>a.label===l);
 const fire=(name,ev)=>{for(const fn of listeners[name]||[])fn(ev)};
 return {Live,g,api,sample,study,cache,added,toasts,page,items,asset,fire,listeners,setScreen:s=>{scr=s}};
}
const plain=o=>JSON.parse(JSON.stringify(o));
const text=html=>html.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').replace(/&#39;/g,"'").replace(/&quot;/g,'"');
const labelsOf=html=>[...html.matchAll(/<g class="mt-label[^"]*"[^>]*>.*?<text[^>]*>([^<]*)<\/text><\/g>/g)].map(m=>m[1].replace(/&#39;/g,"'").replace(/&quot;/g,'"'));
function el(attrs={},{svg=null}={}){return {dataset:attrs.dataset||{},closest(sel){
 if(/data-fc-vertex/.test(sel)&&attrs.fc!==undefined)return {dataset:{fcVertex:attrs.fc}};
 if(/data-mt-vertex/.test(sel)&&attrs.vertex!==undefined)return {dataset:{mtVertex:attrs.vertex}};
 if(/data-mt-edge/.test(sel)&&attrs.edge!==undefined)return {dataset:{mtEdge:attrs.edge}};
 if(/svg\[/.test(sel))return svg;return null}}}
function stageSvg(scope,v){return {dataset:{mtScope:scope,paStage:v.id},getBoundingClientRect:()=>({left:0,top:0,width:v.width,height:v.height}),viewBox:{baseVal:{width:v.width,height:v.height}},setPointerCapture(){},releasePointerCapture(){},closest(){return this}}}
const at=(svg,p,extra={})=>({target:el(extra,{svg}),clientX:p[0],clientY:p[1],pointerId:1,preventDefault(){},stopPropagation(){}});
const scoped=scope=>({closest:()=>({dataset:{mtScope:scope}}),dataset:{mtScope:scope}});
const settle=()=>new Promise(r=>setImmediate(r));

/* ---------- Draft it ---------- */
async function measuring(label){
 const x=await boot(),a=x.asset(label);x.Live.state.docId=a.id;await x.page();
 const u=x.Live.state.property[x.study.id],v=(await x.api.request('GET',`/v1/studies/${x.study.id}/property`)).capture.views.find(h=>h.id===u.trace.viewId);
 return {...x,a,u,t:u.trace,v,svg:stageSvg('property',v)};
}
const sitePx=(v,key)=>PS.SITE[key].map(([e,n])=>[v.width/2+e/v.pixel_size_m,v.height/2-n/v.pixel_size_m]);
function dragBox(x,from,to){x.fire('pointerdown',at(x.svg,from));x.fire('pointermove',at(x.svg,to));x.fire('pointerup',at(x.svg,to))}
const boxAround=(pts,pad=6)=>{const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);return [[Math.min(...xs)-pad,Math.min(...ys)-pad],[Math.max(...xs)+pad,Math.max(...ys)+pad]]};

test('the Draft it block is offered for an area on a top-down view, with the asset’s family',async()=>{
 const x=await measuring('patio');let html=x.Live.screens.property();
 assert.match(text(html),/Let Basis draft it\. Choose Box, drag a box around it on the picture, then Draft it\./);
 assert.match(html,/<option value="patio" selected>Patio<\/option>/);
 assert.match(html,/data-live="mt-draft" data-mt-scope="property" disabled>Draft it</,'nothing to send before a box');
 const fence=await measuring('fence');html=fence.Live.screens.property();
 assert.doesNotMatch(html,/data-live="mt-draft"/);assert.match(text(html),/a line \(a fence, a curb\) is traced by hand/);
 const off=await boot({caps:{site_drafts:false}}),p=off.asset('patio');off.Live.state.docId=p.id;await off.page();
 assert.doesNotMatch(off.Live.screens.property(),/Let Basis draft it/,'off: today’s tool, unchanged');
});

test('a dragged box is drawn and kept; a box too small is dropped; no corner is added while boxing',async()=>{
 const x=await measuring('patio');
 x.Live.actions['pa-tool'](scoped('property'),'box');assert.equal(x.t.tool,'box');
 dragBox(x,[100,100],[102,101]);assert.equal(x.t.box,null,'a click-sized box is not a box');
 const [a,b]=boxAround(sitePx(x.v,'patio'));dragBox(x,b,a);// dragged from the bottom right
 assert.deepEqual(plain(x.t.box.map(Math.round)),[...a,...b].map(Math.round));
 x.fire('click',at(x.svg,[300,300]));assert.equal(x.t.draft.length,0);assert.equal(x.t.shapes.length,0);
 const html=x.Live.screens.property();assert.match(html,/<rect class="mt-draftbox"/);assert.match(html,/data-live="mt-draft" data-mt-scope="property" >Draft it</);
});

test('Draft it: the draft comes back drawn with its lengths, not verified, and is sent as it is once verified',async()=>{
 const x=await measuring('patio');x.Live.actions['pa-tool'](scoped('property'),'box');
 const [a,b]=boxAround(sitePx(x.v,'patio'));dragBox(x,a,b);
 await x.Live.requestDraft('property');
 const t=x.t;assert.equal(t.drafting,false);assert.equal(t.draftMsg,null);
 assert.equal(t.base.drawn_by,'model:sam3@site-v1');assert.equal(t.fromBase,true);assert.equal(t.verified,false);assert.equal(t.tool,'area');assert.equal(t.box,null);
 assert.equal(t.shapes.length,1);assert.equal(t.shapes[0].ring.length,4);
 const html=x.Live.screens.property(),r=PM.measure(PM.bodyOfRecord(t.base));
 assert.match(text(html),/Drafted by Basis · not verified · a starting outline, expected error up to 46 % · check it, change what is wrong, then mark it verified/);
 assert.equal(labelsOf(html).length,4,'a length on every edge');assert.doesNotMatch(html,/mt-original/);
 assert.ok(Math.abs(Number(r.quantity)/PS.truth.patioSf-1.02)<0.03,r.quantity);
 t.verified=true;x.Live.actions['pa-trace-save'](scoped('property'));
 const m=x.added.get(x.a.id).c.after.measurement;
 assert.equal(m.drawn_by,'model:sam3@site-v1');assert.equal(m.verified,true);assert.ok(!m.original);
 assert.deepEqual(plain(m.shapes),plain(t.base.shapes));assert.deepEqual(plain(PM.problems(m)),[]);
});

test('an edited draft is the engineer’s, with the draft kept as its original and dashed',async()=>{
 const x=await measuring('patio');x.Live.actions['pa-tool'](scoped('property'),'box');
 const [a,b]=boxAround(sitePx(x.v,'patio'));dragBox(x,a,b);await x.Live.requestDraft('property');
 const t=x.t,draft=plain(t.base),q=t.shapes[0].ring[1];
 x.fire('pointerdown',at(x.svg,q,{vertex:'0:o:1'}));x.fire('pointermove',at(x.svg,[q[0]+5,q[1]]));x.fire('pointerup',at(x.svg,[q[0]+5,q[1]]));
 assert.equal(t.fromBase,false);
 const html=x.Live.screens.property();assert.match(html,/class="mt-original"/);assert.match(text(html),/your edit replaces it; the original stays dashed/);
 t.verified=true;x.Live.actions['pa-trace-save'](scoped('property'));
 const m=x.added.get(x.a.id).c.after.measurement;
 assert.equal(m.drawn_by,'engineer');assert.deepEqual(plain(m.original),draft);
 assert.match(PM.formula(PM.measure(m),{source:m.image.source,captured_on:m.image.captured_on,drawn_by:m.drawn_by,verified:m.verified}),/; verified by the engineer; replaces [\d,.]+ SF drafted by Basis$/);
});

test('a refusal is shown in plain words, and Undo and Redo take a draft back whole',async()=>{
 const x=await measuring('patio');x.Live.actions['pa-tool'](scoped('property'),'box');
 dragBox(x,[60,60],[160,140]);// lawn: nothing there
 await x.Live.requestDraft('property');
 assert.equal(x.t.draftMsg,'Basis found nothing to outline in the box. Draw the box closer around the item, or trace it by hand.');
 assert.match(x.Live.screens.property(),/<p class="pa-problem" role="alert">Basis found nothing to outline/);
 assert.equal(x.t.base,null);assert.equal(x.t.shapes.length,0);
 x.t.family='fence';const [a,b]=boxAround(sitePx(x.v,'patio'));dragBox(x,a,b);await x.Live.requestDraft('property');
 assert.match(x.t.draftMsg,/^Fences are not drafted from above/);
 x.t.family='patio';await x.Live.requestDraft('property');assert.equal(x.t.base.drawn_by,'model:sam3@site-v1');
 x.Live.actions['pa-trace-undo'](scoped('property'));
 assert.equal(x.t.shapes.length,0);assert.equal(x.t.base,null,'the draft’s provenance goes with it');assert.equal(x.t.draftInfo,null);
 x.Live.actions['mt-redo'](scoped('property'));
 assert.equal(x.t.shapes.length,1);assert.equal(x.t.base.drawn_by,'model:sam3@site-v1');assert.equal(x.t.fromBase,true);
 // The sample answered with the event the server records.
 const events=(await x.api.request('GET',`/v1/studies/${x.study.id}/timeline`,{query:{event_type:'measurement.drafted'}})).items;
 assert.ok(events.length>=3);assert.ok(events.some(e=>e.code==='BASIS-SITE-003')&&events.some(e=>e.code==='BASIS-SITE-008'));
});

test('a busy or unavailable drafter reads as Basis being busy, never a provider',async()=>{
 const x=await measuring('patio');x.Live.actions['pa-tool'](scoped('property'),'box');const [a,b]=boxAround(sitePx(x.v,'patio'));dragBox(x,a,b);
 x.api.request=async()=>{throw Object.assign(new Error('rate'),{status:429,retryAfter:'12'})};
 x.g.BasisLive.api.request=x.api.request;
 await x.Live.requestDraft('property');assert.equal(x.t.draftMsg,'Basis is busy drafting. Try again in 12 seconds, or trace it by hand.');
 x.api.request=async()=>{throw Object.assign(new Error('x'),{status:503})};x.g.BasisLive.api.request=x.api.request;
 await x.Live.requestDraft('property');assert.equal(x.t.draftMsg,'Drafting is unavailable right now. Trace it by hand, or try again in a minute.');
 assert.equal(x.t.drafting,false);
});

/* ---------- facade mode ---------- */
async function facadeDialog(mode='exif'){
 const x=await boot({screen:'assets',facade:mode}),sid=x.asset('siding');assert.ok(sid,'the sample has a siding row');
 const doc=x.study.documents.find(d=>d.filename==='west-wall.jpg');
 x.Live.measurePhotoDims.set(doc.id,[1600,1200]);
 x.Live.openMeasure(sid,'assets');if(mode!=='exif')x.Live.state.mtDialog.key='doc:'+doc.id;// with no lens the first photo of the evidence opens
 await x.page(()=>x.Live.measureDialog());
 const html=x.Live.measureDialog(),svg=stageSvg('facade',{id:'doc:'+doc.id,width:1600,height:1200});
 return {...x,sid,doc,html,svg,P:PS.FACADE.projection(),F:()=>x.Live.facadeScope()?.F};
}
const clickAll=(x,pts)=>{for(const p of pts)x.fire('click',at(x.svg,p))};

test('the siding opens in facade mode on its photo, with the lens from the photo',async()=>{
 const x=await facadeDialog();
 assert.match(x.html,/data-live="mt-mode" data-value="facade" aria-pressed="true"/);
 assert.match(text(x.html),/Lens: 28 mm \(35 mm equivalent\), from the photo · Sample Phone 12\./);
 assert.match(text(x.html),/Click the wall’s four corners to start/);
 assert.match(x.html,/data-live="fc-tool" data-value="window" aria-pressed="false" disabled/);
 assert.match(text(x.html),/West side 39'-4"/,'the lidar shell’s sides as lengths');
 x.Live.actions['mt-mode']({},'trace');assert.match(x.Live.measureDialog(),/data-live="pa-tool" data-value="scale"/,'Trace is one click away');
 // On the aerial the siding is traced, never drafted: Draft it is for site items (EVAL_REPORT 106).
 x.Live.state.mtDialog.key=x.Live.state.mtDialog.trace?.viewId&&null;x.Live.actions['mt-view']({},(await x.api.request('GET',`/v1/studies/${x.study.id}/property`)).capture.views[0].id);
 const aerial=x.Live.measureDialog();assert.match(aerial,/data-mt-scope="dialog"/);assert.doesNotMatch(aerial,/Let Basis draft it/);
});

test('the wall and its openings clicked in any order measure as the server measures them, close to the truth',async()=>{
 const x=await facadeDialog(),P=x.P;
 clickAll(x,[P.wall[2],P.wall[0],P.wall[3],P.wall[1]]);// any order
 let F=x.F();assert.equal(F.wall.length,4);assert.equal(F.tool,'window');
 assert.match(text(x.Live.measureDialog()),/Give the wall’s length/);
 x.Live.actions['fc-side']({},'west');assert.equal(F.length.from,"the lidar shell's west side (2021 lidar)");
 for(const o of P.openings){if(o.kind==='door')x.Live.actions['fc-tool']({},'door');else x.Live.actions['fc-tool']({},'window');clickAll(x,o.quad)}
 F=x.F();assert.equal(F.openings.length,6);
 const html=x.Live.measureDialog(),body=x.Live.facadeScope()&&(()=>{const r=PM.measure({unit:'SF',space:'facade',facade:{wall:PM.orderQuad(F.wall),openings:F.openings.map(o=>({kind:o.kind,corners:PM.orderQuad(o.pts)})),wall_length_ft:Math.round(F.length.ft*1e4)/1e4,wall_length_from:F.length.from,lens:{focal_length_35mm:28,from:'photo'}},image:{source:'Photo west-wall.jpg of this study',document_id:x.doc.id},view:{kind:'document',id:x.doc.id,label:'west-wall.jpg',width:1600,height:1200},drawn_by:'engineer',verified:true});return r})();
 assert.match(text(html),new RegExp(`${body.quantity.replace('.','\\.').replace(/\B(?=(\d{3})+(?!\d))/g,',')} SF net siding`));
 assert.match(text(html),/6 openings \(5 windows, 1 door\)/);
 const m=body.facade_measured,truthNet=PS.FACADE.truth.gross_sf-PS.FACADE.truth.openings_sf;
 assert.ok(Math.abs(Number(body.quantity)/truthNet-1)<0.002,`${body.quantity} vs ${truthNet}`);
 assert.ok(Math.abs(Number(m.openings_sf)/PS.FACADE.truth.openings_sf-1)<0.005);
 assert.ok(Math.abs(Number(m.height_ft)-5.6/0.3048)<0.02);
 assert.match(text(html),/Expected error up to [\d.]+ % \(the corners; the wall’s length not counted\)/);
 assert.equal(labelsOf(html).filter(l=>/^\d$/.test(l)).length,6,'each opening is numbered on the photo');assert.match(html,/<title>Opening 3: 3&#39;-[12]&quot; × 6&#39;-1[01]&quot;<\/title>/);
 // Saved: the measurement in space facade, the counts as the correction's note, accepted by the API.
 x.Live.actions['fc-save']();
 const c=x.added.get(x.sid.id).c,sent=c.after.measurement;
 assert.equal(sent.space,'facade');assert.equal(sent.facade.lens.from,'photo');assert.equal(sent.verified,true);assert.equal(PM.measure(sent).quantity,body.quantity);
 assert.match(c.reason,/^Facade measured on photo west-wall\.jpg: 5 windows, 1 door, [\d.]+ LF of opening trim/);
 assert.match(text(x.Live.measureDialog()),/Siding measured/);
 const decided=await x.api.request('POST',`/v1/studies/${x.study.id}/decisions`,{body:{stage:'assets_review',revision:x.study.revision,decision:'reject',corrections:[{entity_type:'asset',entity_id:x.sid.id,after:c.after,reason:c.reason}]}});
 assert.equal(decided.corrections_recorded,1);
});

test('a dragged corner moves the numbers and Undo puts it back; an opening off the wall is named',async()=>{
 const x=await facadeDialog(),P=x.P;clickAll(x,P.wall);x.Live.actions['fc-side']({},'west');clickAll(x,P.openings[0].quad);
 const before=text(x.Live.measureDialog()).match(/([\d,.]+) SF net siding/)[1];
 const q=P.wall[2];x.fire('pointerdown',at(x.svg,q,{fc:'w:2'}));x.fire('pointermove',at(x.svg,[q[0],q[1]-30]));x.fire('pointerup',at(x.svg,[q[0],q[1]-30]));x.fire('click',at(x.svg,[q[0],q[1]-30],{fc:'w:2'}));// the click a browser sends after the drag
 const after=text(x.Live.measureDialog()).match(/([\d,.]+) SF net siding/)[1];assert.notEqual(after,before);
 x.Live.actions['fc-undo']();assert.equal(text(x.Live.measureDialog()).match(/([\d,.]+) SF net siding/)[1],before);
 clickAll(x,[[5,1190],[60,1190],[60,1150],[5,1150]]);// a window below the wall
 assert.match(text(x.Live.measureDialog()),/Opening 2 \(window\) is outside the wall: move its corners onto the wall\./);
 assert.match(x.Live.measureDialog(),/data-live="fc-save" disabled/);
 x.Live.actions['fc-remove']({},'1');assert.match(text(x.Live.measureDialog()),/SF net siding/);
});

test('a photo with no lens asks for it; the wall’s own lines stand in, and a typed lens is used',async()=>{
 const x=await facadeDialog('nolens'),P=x.P;
 assert.match(text(x.html),/This photo does not say which lens took it\. Type its 35 mm equivalent/);
 clickAll(x,P.wall);x.Live.actions['fc-side']({},'west');
 let html=x.Live.measureDialog();assert.match(text(html),/lens worked out from vanishing points/);
 const F=x.F();
 // A wall whose verticals are parallel and no lens: refused in plain words.
 F.wall=[[300,900],[1300,860],[1300,300],[300,260]];html=x.Live.measureDialog();
 assert.match(text(html),/This photo does not say which lens took it, and the wall’s own lines cannot tell\. Type the lens/);
 x.fire('change',{target:{dataset:{fcInput:'lens'},value:'28'}});
 assert.equal(F.lens.f35,28);assert.equal(F.lens.from,'engineer');
 html=x.Live.measureDialog();assert.match(text(html),/SF net siding/);assert.doesNotMatch(text(html),/lens worked out/);
 x.fire('change',{target:{dataset:{fcInput:'lens'},value:'wide'}});assert.match(x.F().msg,/The lens is a focal length in millimetres/);
});
