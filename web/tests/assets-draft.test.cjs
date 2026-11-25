// The assets review's corrections kept as the engineer's gate draft on the server (web/live/
// live-review.js; docs/api/GATE_DRAFTS_AND_RESUME.md), against the sample API's drafts routes,
// which mirror backEnd/basis/api/routes/drafts.py: a traced measurement saved on the page is PUT
// with If-Match, survives a reload (a new page restores it), a 412 is re-read and saved again, an
// empty list deletes the draft, the decision takes the draft with it, and a draft from another
// revision is not restored.
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const WEB=path.join(__dirname,'..'),A=require('../live/sample-api.js'),PM=require('../live/property-model.js'),PS=require('../live/sample-property.js');
const NOW=Date.parse('2026-11-23T20:00:00Z');
const settle=async()=>{for(let i=0;i<20;i++)await new Promise(r=>setImmediate(r))};
const plain=o=>JSON.parse(JSON.stringify(o));

function page(sample,api,study,{onRequest=null}={}){
 const toasts=[],calls=[];
 const request=async(method,p,opts={})=>{calls.push({method,path:p,headers:opts.headers||{}});if(onRequest){const r=await onRequest(method,p,opts);if(r!==undefined)return r}return api.request(method,p,opts)};
 const live={state:{study,studyId:study.id,corrections:{},cache:new Map()},api:{...api,request},STAGE:{},gates:require('../live/gates.js'),labelName:s=>String(s||''),actions:{},afterRender:[],withStudy:fn=>fn(study),load:()=>({done:false}),
  gateOpen:stage=>study.current_stage===stage&&study.status==='waiting_review',route:()=>'#',steps:()=>'',render(){},photo:()=>'',loading:()=>'',failed:()=>'',errorText:e=>e?.message||''};
 const sandbox={BasisAppraisalSource:require('../live/appraisal-source.js'),BasisApi:{newKey:()=>'k'},BasisLive:live,BasisManualAdd:require('../live/manual-add.js'),BasisVision:require('../vision-model.js'),BasisPropertyModel:PM,RoomPresentation:require('../room-model.js'),
  root:{querySelector:()=>null,addEventListener(){}},render(){},state:{screen:'assets'},location:{hash:'#assets'},document:{documentElement:{classList:{toggle(){}}}},esc:s=>String(s??''),icon:()=>'',heading:t=>t,toast:t=>toasts.push(t),modal(){},closeBtn:'',viewer(){},appraisal(){},rooms(){},assets(){},assetDetail(){},complete(){}};
 vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(WEB,'live/live-review.js'),'utf8'),sandbox);
 return {live,toasts,calls};
}
async function boot(){
 const sample=A.create({state:'assets',now:()=>NOW,fetchAsset:p=>fs.readFileSync(path.join(WEB,p))});
 const api=require('../live/api-client.js').createClient({baseUrl:sample.base,fetchImpl:(u,i)=>sample.fetch(u,i),getToken:async()=>'t'});
 const study=await api.getStudy(sample.mainStudyId()),items=(await api.assets(study.id,study.revision)).items;
 return {sample,api,study,items,fence:items.find(a=>a.label==='fence')};
}
const draftUrl=s=>`/v1/studies/${s.id}/drafts/assets_review`;
function fenceMeasurement(){
 const v={id:'v',label:'history-2025-11-20',width:1440,height:900,lat:43.5,lon:-87,pixel_size_m:0.0841,imagery_on:'2025-11-20',source:'Aerial imagery'};
 const path_=PS.SITE.fence.map(([e,n])=>[720+e/0.0841,450-n/0.0841]);
 return PM.withProvenance(PM.measurementBody({unit:'LF',shapes:[{kind:'line',path:path_}],view:v}),{verified:true,view:PM.viewRef(v)});
}

test('a traced measurement is saved as the gate draft and a reloaded page brings it back',async()=>{
 const x=await boot(),p1=page(x.sample,x.api,x.study);
 p1.live.ensureAssetsDraft(x.study,x.items);await settle();
 assert.equal(p1.calls[0].method,'GET');
 const m=fenceMeasurement();
 p1.live.addCorrection('assets_review',{key:x.fence.id,entity_type:'asset',entity_id:x.fence.id,before:{quantity:'1',quantity_unit:'EA'},after:{measurement:m},reason:null,title:'Fence',summary:'traced'});
 await settle();
 const put=p1.calls.find(c=>c.method==='PUT');assert.ok(put,'saved');assert.equal(put.headers['If-Match'],'"0"');
 const saved=await x.api.request('GET',draftUrl(x.study));
 assert.equal(saved.draft.version,1);assert.deepEqual(saved.draft.corrections[0].after.measurement,m);
 // A reload: a new page, nothing pending, the draft restored with its title and summary.
 const p2=page(x.sample,x.api,x.study);p2.live.ensureAssetsDraft(x.study,x.items);await settle();
 const back=p2.live.pendingCorrection('assets_review',x.fence.id);
 assert.ok(back);assert.deepEqual(plain(back.after.measurement),m);assert.equal(back.title,'fence');assert.match(back.summary,/quantity: 1 EA → 209\.97 LF/);
 assert.match(p2.toasts[0],/1 unsent correction brought back from your saved draft/);
 // a second change chains on the version the save returned
 p2.live.addCorrection('assets_review',{...back,after:{measurement:{...m,verified:false}}});await settle();
 assert.equal(p2.calls.filter(c=>c.method==='PUT').at(-1).headers['If-Match'],'"1"');
 assert.equal((await x.api.request('GET',draftUrl(x.study))).draft.version,2);
});
test('a save that lost the race (412) re-reads the version and saves again; an empty list deletes the draft',async()=>{
 const x=await boot(),p=page(x.sample,x.api,x.study);p.live.ensureAssetsDraft(x.study,x.items);await settle();
 // another tab saved first
 await x.api.request('PUT',draftUrl(x.study),{body:{revision:x.study.revision,comment:null,corrections:[]},headers:{'If-Match':'"0"'}});
 const m=fenceMeasurement();
 p.live.addCorrection('assets_review',{key:x.fence.id,entity_type:'asset',entity_id:x.fence.id,before:null,after:{measurement:m},reason:null,title:'Fence',summary:''});await settle();
 const puts=p.calls.filter(c=>c.method==='PUT');assert.deepEqual(puts.map(c=>c.headers['If-Match']),['"0"','"1"']);
 assert.equal((await x.api.request('GET',draftUrl(x.study))).draft.corrections.length,1);
 assert.equal(p.toasts.filter(t=>/could not be saved/.test(t)).length,0,'no complaint for a race it resolved');
 p.live.actions['drop-correction']({dataset:{stage:'assets_review'}},x.fence.id);await settle();
 assert.equal(p.calls.at(-1).method,'DELETE');assert.equal((await x.api.request('GET',draftUrl(x.study))).draft,null);
});
test('a draft the server refuses stays on the page and the engineer is told once; an API without drafts changes nothing',async()=>{
 const x=await boot(),p=page(x.sample,x.api,x.study);p.live.ensureAssetsDraft(x.study,x.items);await settle();
 const bad={unit:'SF',space:'image',shapes:[],image:{source:'x'}};
 p.live.addCorrection('assets_review',{key:x.fence.id,entity_type:'asset',entity_id:x.fence.id,before:null,after:{measurement:bad},reason:null,title:'Fence',summary:''});await settle();
 assert.match(p.toasts.find(t=>/could not be saved/.test(t))||'',/could not be saved on the server .*They stay on this page until you send them/);
 assert.ok(p.live.pendingCorrection('assets_review',x.fence.id));
 const old=page(x.sample,x.api,x.study,{onRequest:async(method,p_)=>{if(p_.includes('/drafts/')){const e=new Error('Not Found');e.status=404;throw e}}});
 old.live.ensureAssetsDraft(x.study,x.items);await settle();
 old.live.addCorrection('assets_review',{key:x.fence.id,entity_type:'asset',entity_id:x.fence.id,before:null,after:{bucket:'y7'},reason:null,title:'Fence',summary:''});await settle();
 assert.deepEqual(old.calls.map(c=>c.method),['GET'],'one look, then the page keeps them as before');assert.equal(old.toasts.filter(t=>/could not be saved/.test(t)).length,0);
});
test('the decision takes the draft with it, and a draft from an earlier revision is not restored',async()=>{
 const x=await boot(),p=page(x.sample,x.api,x.study);p.live.ensureAssetsDraft(x.study,x.items);await settle();
 const m=fenceMeasurement();
 p.live.addCorrection('assets_review',{key:x.fence.id,entity_type:'asset',entity_id:x.fence.id,before:null,after:{measurement:m},reason:null,title:'Fence',summary:''});await settle();
 await x.api.decide(x.study.id,{stage:'assets_review',revision:x.study.revision,decision:'reject',comment:null,corrections:[{entity_type:'asset',entity_id:x.fence.id,before:null,after:{measurement:m},reason:null}]},'k1');
 assert.equal((await x.api.request('GET',draftUrl(x.study))).draft,null);
 // a draft left on revision 2 by another path is stale once the study moves on
 const y=await boot();await y.api.request('PUT',draftUrl(y.study),{body:{revision:y.study.revision,comment:null,corrections:[{entity_type:'asset',entity_id:y.fence.id,before:null,after:{bucket:'y7'},reason:null}]}});
 await y.api.decide(y.study.id,{stage:'assets_review',revision:y.study.revision,decision:'approve',comment:null,corrections:[]},'k2');
 const later=await y.api.getStudy(y.study.id);
 const q=page(y.sample,y.api,{...later,current_stage:'assets_review',status:'waiting_review'});q.live.ensureAssetsDraft({...later},y.items);await settle();
 assert.equal(q.live.pendingCorrection('assets_review',y.fence.id),null);
});
