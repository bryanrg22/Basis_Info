// The Property area as rendered (web/live/property.js) on the sample API through the real API
// client, in a VM: what shows for each capability set (today's API: nothing), each capture
// state, the phone, the measurement it adds to the assets review, and the 412 and 429 answers.
// What a render does in a browser (layout, focus, pointer) is checked by the captures listed in
// web/DESIGN_NOTES.md, "Property".
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const WEB=path.join(__dirname,'..'),read=f=>fs.readFileSync(path.join(WEB,f),'utf8');
const A=require('../live/sample-api.js'),PM=require('../live/property-model.js'),PS=require('../live/sample-property.js');
const NOW=Date.parse('2026-11-23T20:00:00Z');

async function boot({caps={property_capture:true,public_geo:true,traced_measurement:true},variant,state='assets',narrow=false,intercept=null,docId=null}={}){
 const sample=A.create({state,now:()=>NOW,fetchAsset:p=>fs.readFileSync(path.join(WEB,p))});if(variant)sample._inst.propertyState=variant;
 const fetchImpl=async(url,init={})=>{if(intercept){const r=await intercept(new URL(String(url)).pathname,init);if(r)return r}return sample.fetch(url,init)};
 const api=require('../live/api-client.js').createClient({baseUrl:sample.base,fetchImpl,getToken:async()=>'sample-token'});
 const study=await api.getStudy(sample.mainStudyId());
 const cache=new Map(),added=[],toasts=[],listeners={};
 const load=(key,fn)=>{if(cache.has(key))return cache.get(key);const e={done:false};cache.set(key,e);e.promise=Promise.resolve().then(fn).then(d=>{e.data=d;e.done=true},err=>{e.error=err;e.done=true});return e};
 const Live={api,caps:{objectExclusion:true,...PM.caps(caps)},state:{cache,studyId:study.id,study,docId},screens:{},actions:{},afterRender:[],load,
  invalidate:p=>{for(const k of [...cache.keys()])if(k.startsWith(p))cache.delete(k)},withStudy:fn=>fn(study),me:()=>({memberships:[{org_id:study.org_id,role:'owner'}]}),
  route:(screen,id,doc)=>`#${screen}${id?'/'+id:''}${doc?'/'+doc:''}`,when:v=>v?String(v).slice(0,16):'—',loading:t=>`<div class="live-state">${t}</div>`,failed:e=>`<div role="alert">${e?.message}</div>`,
  render(){},screen:()=>'property',labelName:l=>String(l||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase()),gateOpen:stage=>study.current_stage===stage&&study.status==='waiting_review',
  guarded:async(name,fn)=>fn(),errorText:e=>e?.message||'',addCorrection:(stage,c)=>added.push({stage,c}),pendingCorrection:()=>null,studyTools:s=>`<section class="study-tools"><a>History</a></section>`};
 const g={BASIS_CONFIG:{mode:'live'},BasisLive:Live,BasisPropertyModel:PM,BasisReasons:require('../live/review-reasons.js'),Intl,URL,Date,Promise,Map,Set,JSON,Math,Number,String,Array,Object,Boolean,RegExp,Error,encodeURIComponent,console,
  setTimeout:()=>0,clearTimeout(){},matchMedia:()=>({matches:narrow}),icon:()=>'<svg></svg>',heading:(t,d,a)=>`<h1>${t}</h1>${a||''}`,toast:t=>toasts.push(t),
  esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  document:{addEventListener:(n,fn)=>(listeners[n]||=[]).push(fn),querySelector:()=>null,querySelectorAll:()=>[],hidden:false}};
 g.globalThis=g;vm.createContext(g);vm.runInContext(read('live/property.js'),g);
 // Render until every load the page started has answered.
 async function page(fn=()=>Live.screens.property()){let html='';for(let i=0;i<8;i++){html=fn();const open=[...cache.values()].filter(e=>!e.done);if(!open.length)break;await Promise.all(open.map(e=>e.promise))}return fn()}
 return {Live,g,api,sample,study,cache,added,toasts,page,listeners};
}
const text=html=>html.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ');

test('today’s API (no capabilities): no Property area, no card, no link, no Measure',async()=>{
 const x=await boot({caps:{}});
 assert.equal(x.Live.studyProperty(x.study),'');
 assert.match(text(await x.page()),/Property pictures are not available on this server/);
 assert.doesNotMatch(x.Live.studyTools(x.study),/Property/);
 assert.equal([...x.cache.keys()].filter(k=>k.startsWith('property')).length,0,'nothing is requested');
});
test('the default sample: the house question on the wide aerial, with the pin and the parcel drawn over it',async()=>{
 const x=await boot(),html=await x.page(),t=text(html);
 assert.match(t,/Is this the right house\?/);assert.match(t,/Yes, this is the house/);assert.match(t,/No, move the pin/);
 assert.match(t,/Nothing is measured on these pictures until the pin is confirmed/);
 assert.match(html,/data-pa-stage=/);assert.match(html,/class="pa-pin"/);assert.match(html,/class="pa-parcel "/);assert.match(html,/pa-parcel is-adjacent/);
 assert.match(html,/aria-pressed="true"[^>]*>Wide aerial/);
 for(const chip of ['Close aerial','Four sides','Street level','Public image'])assert.match(t,new RegExp(chip));
 assert.match(t,/Jun 18, 2026 · Trees in leaf · 24\.5 cm a pixel/);assert.match(t,/Aerial imagery viewed online; date as the imagery shows it/);
 assert.match(t,/Dated pictures newest first/);assert.match(t,/Suggested for measuring/);assert.match(t,/At placed in service/);assert.match(t,/2021 \(year only\)/);
 assert.match(t,/Public imagery by year/);assert.match(t,/For seeing, not measuring/);assert.match(t,/Sample state orthoimagery program \(synthetic image\)/);
 assert.match(t,/Lot area 15,156 SF/);assert.match(t,/Roof peak 29\.2 ft above the ground/);assert.match(t,/including the overhangs/);
 assert.match(t,/Before and after/);
 assert.doesNotMatch(html,/google|earth\b|bing/i,'no provider named');
 const card=x.Live.studyProperty(x.study);assert.match(text(card),/House not confirmed/);assert.match(text(card),/Is this the right house\?/);
 assert.match(x.Live.studyTools(x.study),/>Property</);
});
test('street level is not available, with the reason the capture gave; the four sides say they are not for measuring',async()=>{
 const x=await boot();await x.page();
 x.Live.actions['pa-view'](null,'street');assert.match(text(x.Live.screens.property()),/Street level: not available Street-level pictures are not available yet\./);
 x.Live.actions['pa-view'](null,'sides');const t=text(x.Live.screens.property());
 for(const side of ['South face','West face','North face','East face'])assert.match(t,new RegExp(side));assert.match(t,/not for measuring/);
});
test('the capture states in plain words: paused until a time, waiting its turn, running, failed with the code for support',async()=>{
 const paused=text(await (await boot({variant:'paused'})).page());
 assert.match(paused,/Property pictures · Paused Property pictures are paused until .+; they will resume on their own\./);assert.match(paused,/BASIS-CAPTURE-009/);
 assert.match(paused,/Showing the pictures taken .+ until the new ones are ready/);
 assert.match(text(await (await boot({variant:'waiting'})).page()),/Waiting its turn/);
 assert.match(text(await (await boot({variant:'running'})).page()),/Taking pictures .*usually ready within two minutes/);
 const failed=await (await boot({variant:'failed'})).page();
 assert.match(text(failed),/Not taken Taking the pictures took too long\. Take them again later\./);assert.match(failed,/data-live="pa-capture" data-value="force"/);
 const noplace=text(await (await boot({variant:'noplace'})).page());
 assert.match(noplace,/did not lead to a place/);assert.match(noplace,/Where is the property\?/);
 assert.match(await (await boot({variant:'nopin'})).page(),/data-pa-form="address"/);
});
test('public records only: no capture status and no street level; the public image is the stage',async()=>{
 const x=await boot({caps:{public_geo:true}}),t=text(await x.page());
 assert.doesNotMatch(t,/Property pictures ·|Street level|Take them again/);assert.match(t,/Public image/);assert.match(t,/Lot area/);
});
test('measuring: the fence traced on the suggested view goes to the assets review’s corrections as a measurement',async()=>{
 const x=await boot({variant:'confirmed'}),items=(await x.api.assets(x.study.id,x.study.revision)).items,f=items.find(a=>a.label==='fence');
 x.Live.state.docId=f.id;let t=text(await x.page());
 assert.match(t,/Measure Fence/);assert.match(t,/Nov 20, 2025/,'the suggested (leaf-off) view');assert.match(t,/Nothing traced yet/);
 const u=x.Live.state.property[x.study.id],v=PM.viewsOf((await x.api.request('GET',`/v1/studies/${x.study.id}/property`)).capture).history.find(h=>h.id===u.trace.viewId);
 assert.equal(u.trace.unit,'LF');assert.equal(u.trace.tool,'line');
 u.trace.shapes=[{kind:'line',path:PS.SITE.fence.map(([e,n])=>[720+e/v.pixel_size_m,450-n/v.pixel_size_m])}];
 t=text(x.Live.screens.property());assert.match(t,/209\.97 LF/);assert.match(t,/Expected error up to 0\.26 %/);
 x.Live.actions['pa-trace-save']();
 assert.equal(x.added.length,1);const c=x.added[0].c;
 assert.deepEqual([x.added[0].stage,c.entity_type,c.entity_id],['assets_review','asset',f.id]);
 assert.equal(PM.measure(c.after.measurement).quantity,'209.97');assert.equal(c.after.measurement.image.captured_on,'2025-11-20');
 assert.ok(!('quantity' in c.after),'never with a typed quantity');
 t=text(x.Live.screens.property());assert.match(t,/Fence measured 209\.97 LF is in the assets review’s corrections/);assert.match(t,/No review flag/);
});
test('measuring is refused where it must be: an unconfirmed pin, a closed review, a phone',async()=>{
 let x=await boot(),f=(await x.api.assets(x.study.id,x.study.revision)).items.find(a=>a.label==='fence');x.Live.state.docId=f.id;
 assert.match(text(await x.page()),/Confirm the pin first/);
 x=await boot({variant:'confirmed',narrow:true});x.Live.state.docId=f.id;
 assert.match(text(await x.page()),/Measuring needs a larger screen/);
 x=await boot({variant:'confirmed',state:'unsigned'});const g=(await x.api.assets(x.study.id,x.study.revision)).items.find(a=>a.label==='fence');x.Live.state.docId=g.id;
 assert.match(text(await x.page()),/The assets review is not open/);
});
test('a pin write that lost the race (412) reloads and says so; a capture refused with 429 waits the time the server gave',async()=>{
 const x=await boot({intercept:(p,init)=>init.method==='PUT'&&p.endsWith('/property/pin')?new Response(JSON.stringify({type:'https://basis.invalid/problems/precondition-failed',title:'Precondition Failed',status:412,detail:'stale',code:'BASIS-CAPTURE-016'}),{status:412,headers:{'content-type':'application/problem+json'}}):p.endsWith('/property/captures')&&init.method==='POST'?new Response(JSON.stringify({title:'Too Many Requests',status:429,detail:'slow down'}),{status:429,headers:{'content-type':'application/problem+json','Retry-After':'30'}}):null});
 await x.page();await x.Live.actions['pa-pin-confirm']();
 assert.match(x.toasts.at(-1),/Someone changed the pin since this page loaded/);
 assert.ok(![...x.cache.keys()].includes('property:'+x.study.id),'the property is read again');
 await x.page();await x.Live.actions['pa-capture'](null,'force');
 assert.match(x.toasts.at(-1),/Try again in 30 seconds/);
 assert.match(x.Live.screens.property(),/disabled>Take them again \(in 30 s\)/);
});
