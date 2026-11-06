const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const H=require('../live/study-overview.js'),G=require('../live/gates.js');
const study=(status='completed',current_stage='completed',components_gate=true)=>({id:'study',property_name:'Example property',revision:3,status,current_stage,components_gate,created_by:'creator',created_at:'2026-10-20T12:00:00Z',updated_at:'2026-10-23T12:00:00Z',documents:[]});
test('overview permits all completed steps and only past/current reviews before completion',()=>{
 for(const legacy of [false,true]){
  const done=H.steps(study('completed','completed',legacy));
  assert.equal(done.length,legacy?7:6);assert(done.every(s=>s.canOpen));assert.equal(done.at(-1).label,'Report & sign');
  for(const stage of G.ORDER){
   const pending=H.steps(study('processing',stage,legacy));
   assert(pending.filter(s=>s.state==='active').every(s=>!s.canOpen));
   assert(pending.filter(s=>s.state==='').every(s=>!s.canOpen));
  }
  for(const stage of ['appraisal_review','rooms_review','assets_review',...(legacy?['components_review']:[])]){
   const list=H.steps(study('waiting_review',stage,legacy));
   assert.equal(list.find(s=>s.state==='active').destination,G.GATE_SCREEN[stage]);
   assert.equal(list.find(s=>s.state==='active').canOpen,true);
   assert(list.filter(s=>s.state==='').every(s=>!s.canOpen));
  }
 }
});
test('draft, failed, stalled and archived studies never expose a current review action',()=>{
 for(const status of ['draft','failed','stalled','archived']){
  const steps=H.steps(study(status,status==='draft'?'uploading':'components_processing'));
  assert.equal(steps.find(s=>s.state==='active').canOpen,false);
 }
});
test('Home prioritizes actionable studies and preserves recent order without mutating input',()=>{
 const rows=['completed','archived','draft','processing','waiting_review','stalled','failed'].map((status,i)=>({...study(status),id:status,updated_at:`2026-10-2${i}T12:00:00Z`}));
 const before=JSON.stringify(rows),view=H.home(rows);
 assert.deepEqual(view.attention.map(s=>s.id),['failed','stalled','waiting_review','draft']);
 assert.deepEqual(view.processing.map(s=>s.id),['processing']);assert.equal(view.completed,1);assert.equal(view.recent.length,4);
 assert.equal(JSON.stringify(rows),before);assert.deepEqual(H.home([]),{attention:[],processing:[],recent:[],completed:0});
});
test('Activity uses only record timestamps and avoids duplicate creation/update events',()=>{
 const a=study(),b={...study(),id:'b',updated_at:a.created_at};
 const events=H.activity([a,b]);assert.equal(events.length,3);assert.equal(events[0].kind,'updated');
 assert(events.every(e=>e.kind==='created'||e.kind==='updated'));assert.deepEqual(H.activity([]),[]);
});
test('creator attribution matches IDs and never substitutes the viewer for another person',()=>{
 const s=study(),me={user:{id:'creator',display_name:'Creator',email:'creator@example.test'}};
 assert.deepEqual(H.creator(s,me),{name:'Creator',email:'creator@example.test'});
 assert.equal(H.creator({...s,created_by:'other'},me),null);
 assert.deepEqual(H.creator({...s,created_by:'other'},me,[{user_id:'other',email:'other@example.test'}]),{name:'other@example.test',email:'other@example.test'});
});
function boot(rows=[]){
 const events={};const root={innerHTML:'',addEventListener(name,handler){events[name]=handler},querySelector:()=>null,querySelectorAll:()=>[],insertAdjacentHTML(){}};
 const globals={BasisErrorHelp:require('../live/error-help.js'),BasisStudyOverview:H,BasisGates:G,BASIS_CONFIG:{mode:'live',apiBase:'http://localhost:8000',firebase:{}},BasisApi:{createClient:()=>({}),pollWorkflow:()=>({stop(){}})},BasisAuth:{init:()=>new Promise(()=>{}),onChange(){}},root,document:{documentElement:{dataset:{}}},state:{screen:'dashboard'},location:{hash:'#dashboard',href:'http://localhost:3100/?mode=live#dashboard'},sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},URL,Intl,esc:s=>String(s??'').replaceAll('<','&lt;').replaceAll('"','&quot;'),icon:()=>'',heading:(t,d='',a='')=>`<h1>${t}</h1><p>${d}</p>${a}`,statusBadge:s=>s,deskStatus:s=>s.status,toast(){}};
 for(const name of ['render','reviewbar','hasUnsavedDraft','presence','login','dashboard','studyLibrary','archiveStudy','upload','processing','studyFiles','activity'])globals[name]=()=>'';
 vm.createContext(globals);vm.runInContext(fs.readFileSync(path.join(__dirname,'../live/live-app.js'),'utf8'),globals);
 const live=globals.BasisLive;live.me=()=>({memberships:[{org_id:'firm',role:'engineer'}]});live.state.cache.set('studies',{done:true,data:rows});return {ctx:globals,live,events};
}
test('every Home, Library and Activity study entry opens progress, with distinct Home and Library controls',()=>{
 const {ctx}=boot([study(),{...study('waiting_review','rooms_review'),id:'review'}]);
 for(const html of [ctx.dashboard(),ctx.studyLibrary(),ctx.activity()]){
  const entries=[...html.matchAll(/href="#([^"/]+)\/(study|review)"/g)];assert(entries.length);assert(entries.every(m=>m[1]==='processing'));
 }
 assert.doesNotMatch(ctx.dashboard(),/live-study-search|<table/);assert.match(ctx.studyLibrary(),/live-study-search/);
 assert.doesNotMatch(ctx.activity(),/Harbor House|Mock|Alex Morgan/);
});
test('progress renders a compact status bar and detailed list, preserving actions and gate access',()=>{
 const {ctx,live}=boot();
 for(const [status,stage,action] of [['completed','completed','Report & sign'],['waiting_review','rooms_review','Review rooms'],['processing','components_processing','Cancel study'],['failed','assets_processing','Retry processing'],['draft','uploading','Start the study'],['archived','rooms_review','This study is archived']]){
  const s={...study(status,stage),org_id:'firm'};s.documents=[{id:'doc',kind:'appraisal',uploaded_at:'now'}];
  live.state.studyId=s.id;live.state.cache.set('study:'+s.id,{done:true,data:s});
  const html=ctx.processing();assert.match(html,new RegExp(action));assert.doesNotMatch(html,/class="steps|>Workflow<|Property photos|processing budget/);
  const bar=html.match(/<nav class="hub-progress"[\s\S]*?<\/nav>/)?.[0];assert(bar);assert.equal((bar.match(/<li /g)||[]).length,7);assert.equal((bar.match(/aria-current="step"/g)||[]).length,1);
  assert.equal((bar.match(/<a /g)||[]).length,H.steps(s).filter(step=>step.canOpen).length);
  if(status==='completed')assert.doesNotMatch(html,/data-live="cancel"|data-live="start"/);
  if(stage==='rooms_review'&&status==='waiting_review'){assert.match(html,/href="#rooms\/study"/);assert.doesNotMatch(html,/href="#components|href="#assets|href="#complete/)}
 }
});

test('failed workflow query overrides a stale gate and viewers have no retry action',()=>{
 const {ctx,live}=boot();const s={...study('waiting_review','rooms_review'),org_id:'firm'};live.state.studyId=s.id;live.state.cache.set('study:'+s.id,{done:true,data:s});live.state.workflow={id:s.id,data:{status:{failed_stage:'extracting',failure_code:'BASIS-COST-001',failure:'test'}}};
 let html=ctx.processing();assert.match(html,/Retry processing/);assert.match(html,/BASIS-COST-001/);assert.doesNotMatch(html,/Review rooms/);
 live.me=()=>({memberships:[{org_id:'firm',role:'viewer'}]});html=ctx.processing();assert.doesNotMatch(html,/data-live="retry"/);
});

test('retry reuses its key after an uncertain failure and renews it after an acknowledged request',async()=>{
 const {ctx,live,events}=boot();const s={...study('failed','assets_processing'),org_id:'firm'};
 live.state.studyId=s.id;live.state.study=s;live.state.workflow={id:s.id,data:{status:{failed_stage:'assets_processing',failure:'provider unavailable'}}};
 let keyNumber=0,fail=true;const keys=[];ctx.BasisApi.newKey=()=>`retry-${++keyNumber}`;
 live.api.retry=async(id,key)=>{keys.push(key);if(fail)throw Error('Connection lost');return {stage:'assets_processing'}};
 live.reloadStudy=async()=>s;
 const click=async()=>{events.click({target:{closest:()=>({dataset:{live:'retry'}})},preventDefault(){},stopImmediatePropagation(){}});await new Promise(resolve=>setImmediate(resolve))};
 await click();fail=false;await click();await click();
 assert.deepEqual(keys,['retry-1','retry-1','retry-2']);assert.equal(live.state.busy.size,0);
 live.me=()=>({memberships:[{org_id:'firm',role:'viewer'}]});await click();assert.equal(keys.length,3);
});


test('completed status is above navigation and read-only is scoped to review pages',()=>{
 const {ctx,live}=boot();const s=study();
 live.reportStateEntry=()=>({done:true,data:{study_id:s.id,revision:3,exported:true,skipped:null,missing_for_final:[],signature:{study_id:s.id,revision:3},changed_since_signing:[]}});
 for(const screen of ['appraisal','rooms','components','assets','asset-detail','complete','files','structure','intake']){
  ctx.state.screen=screen;const html=live.steps(s);
  // Review pages carry the finished state in the step bar and header; progress, results and records keep the strip.
  const review=['appraisal','rooms','components','assets','asset-detail'].includes(screen);
  assert.equal((html.match(/Study completed/g)||[]).length,review?0:1);
  if(!review)assert(html.indexOf('Study completed')<html.indexOf('<nav'));
  assert.equal(/read-only<\/span>/i.test(html),['appraisal','rooms','components','assets','asset-detail'].includes(screen));
 }
 live.state.studyId=s.id;live.state.cache.set('study:'+s.id,{done:true,data:s});
 const overview=ctx.processing();assert(overview.indexOf('Study completed')<overview.indexOf('<nav'));
 assert.doesNotMatch(overview,/Every review is approved|Study complete<|This study is at Completed/);
});

test('read-only status follows the actual gate, failure and legacy contract',()=>{
 for(const [view,stage] of Object.entries({appraisal:'appraisal_review',rooms:'rooms_review',components:'components_review',assets:'assets_review','asset-detail':'assets_review','viewer-components':'components_review','viewer-rooms':'rooms_review'})){
  assert.equal(G.reviewContext(study('waiting_review',stage),view),null);
  assert.equal(G.reviewContext(study('processing',stage),view).readOnly,true);
  assert.equal(G.reviewContext(study('failed',stage),view).tone,'attention');
  assert.equal(G.reviewContext(study('archived',stage),view).title,'Study archived');
 }
 assert.equal(G.reviewContext(study('waiting_review','rooms_review',false),'viewer-components'),null);
 assert.equal(G.reviewContext(study('processing','completed'),'complete'),null);
 assert.equal(G.reviewContext(study('draft','uploading'),'upload'),null);
 for(const view of ['viewer-rooms','viewer-components'])assert.equal(G.reviewContext(study(),view).title,'Checking completion…');
});

test('pending, failed, draft and changed report states never flash completed above a review',()=>{
 const {ctx,live}=boot();const s=study();ctx.state.screen='components';
 for(const e of [null,{done:false},{done:true,error:Error('offline')},{done:true,data:{study_id:s.id,revision:3,exported:true,missing_for_final:['signature'],signature:null}},{done:true,data:{study_id:s.id,revision:3,exported:true,signature:{study_id:s.id,revision:3},changed_since_signing:['study']}}]){
  live.reportStateEntry=()=>e;const html=live.steps(s);
  assert.doesNotMatch(html,/Study completed|study-status--complete/);assert.match(html,/read-only<\/span>/i);assert.match(html,/href="#report\/study"/);
 }
});

test('the report notice appears once: not above Report & sign, not on a finished study\'s review pages',()=>{
 const {ctx,live}=boot();const s=study();
 live.reportStateEntry=()=>({done:true,data:{study_id:s.id,revision:3,exported:true,missing_for_final:[],signature:{study_id:s.id,revision:3},changed_since_signing:['firm']}});
 ctx.state.screen='report';assert.doesNotMatch(live.steps(s),/study-status/);
 ctx.state.screen='processing';assert.match(live.steps(s),/Report needs updating/);// once, on Study progress, with its action
 for(const screen of ['complete','files','structure','history'])ctx.state.screen=screen,assert.doesNotMatch(live.steps(s),/study-status/,screen);// the header badge and step bar say it there
 ctx.state.screen='components';const html=live.steps(s);assert.doesNotMatch(html,/study-status/);assert.match(html,/step-tone-review[^>]*>[\s\S]*?Update required/);
});
