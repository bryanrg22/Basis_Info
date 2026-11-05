// The UI review (November 2026): delivery status from the report, reopening an approved step,
// forwarding to the reopened review, and the New design switch.
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const H=require('../live/study-overview.js'),G=require('../live/gates.js'),R=require('../live/report.js');
const study=(status='completed',current_stage='completed',components_gate=true)=>({id:'study',org_id:'firm',property_name:'Example property',revision:3,status,current_stage,components_gate,created_at:'2026-10-20T12:00:00Z',updated_at:'2026-10-23T12:00:00Z',documents:[]});
const plain=x=>JSON.parse(JSON.stringify(x));
const reportState=(changed,signed=true)=>({done:true,data:{study_id:'study',revision:3,exported:true,skipped:null,files:['report.pdf'],missing_for_final:signed?[]:['signature'],signature:signed?{study_id:'study',revision:3}:null,changed_since_signing:signed?changed:null}});

function boot({role='engineer'}={}){
 const root={innerHTML:'',addEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],insertAdjacentHTML(){}};
 const timers=[],calls=[],modals=[];
 const globals={BasisErrorHelp:require('../live/error-help.js'),BasisStudyOverview:H,BasisGates:G,BasisReport:R,BASIS_CONFIG:{mode:'live',apiBase:'http://localhost:8000',firebase:{}},BasisApi:{createClient:()=>({request:async(method,url,opts)=>{calls.push({method,url,body:opts?.body});return {revision:4,from_revision:3,stage:opts?.body?.stage,reviews:['components_review','assets_review'],recomputed:['assets_processing','exporting'],carried:['fields']}}}),pollWorkflow:()=>({stop(){}})},BasisAuth:{init:()=>new Promise(()=>{}),onChange(){}},root,document:{documentElement:{dataset:{}},title:'',addEventListener(){},querySelector:()=>null},location:{hash:'',hostname:'localhost',href:'http://localhost/'},sessionStorage:{getItem:()=>null,setItem(){}},state:{screen:'components'},icon:()=>'',esc:v=>String(v??''),heading:(t)=>`<h1>${t}</h1>`,toast(){},setTimeout:fn=>{timers.push(fn);return timers.length},clearTimeout(){},console,URL,Intl,Date,JSON,Promise,Map,Set,Math,Number,String,Array,Object,Boolean,RegExp,Error,encodeURIComponent,decodeURIComponent,structuredClone};
 globals.modal=(title,body)=>{const el={title,body,removed:false,remove(){this.removed=true},buttons:{},querySelector(sel){return this.buttons[sel]||(this.buttons[sel]={})}};modals.push(el);return el};
 for(const name of ['render','reviewbar','hasUnsavedDraft','presence','login','dashboard','studyLibrary','archiveStudy','upload','processing','studyFiles','activity'])globals[name]=()=>'';
 globals.globalThis=globals;
 vm.createContext(globals);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../live/live-app.js'),'utf8'),globals);
 const live=globals.BasisLive;live.me=()=>({memberships:[{org_id:'firm',role}]});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../live/gate-reopen.js'),'utf8'),globals);
 return {ctx:globals,live,calls,modals,timers};
}

test('a finished study reads its delivery state from its report, and "Reviews approved" until that is known',()=>{
 const {live}=boot();const s=study();
 live.reportStateEntry=()=>undefined;assert.deepEqual(plain(live.delivery(s)),{label:'Reviews approved',tone:'done'});
 live.reportStateEntry=()=>({done:false});assert.equal(live.delivery(s).label,'Reviews approved');
 live.reportStateEntry=()=>({done:true,error:Error('offline')});assert.equal(live.delivery(s).label,'Reviews approved');
 live.reportStateEntry=()=>reportState(['firm']);assert.deepEqual(plain(live.delivery(s)),{label:'Update required',tone:'review'});
 live.reportStateEntry=()=>reportState([]);assert.deepEqual(plain(live.delivery(s)),{label:'Signed',tone:'done'});
 live.reportStateEntry=()=>reportState(null,false);assert.deepEqual(plain(live.delivery(s)),{label:'Needs signature',tone:'review'});
 live.reportStateEntry=()=>{throw Error('a study in progress must not read its report')};assert.equal(live.delivery(study('waiting_review','rooms_review')).label,'Reviews approved');
});

test('Change <step> is offered on approved review pages of a completed study, to engineers and owners only',()=>{
 for(const role of ['engineer','owner']){
  const {live}=boot({role});
  for(const [view,stage] of [['appraisal','appraisal_review'],['rooms','rooms_review'],['components','components_review'],['assets','assets_review'],['asset-detail','assets_review']]){
   const html=live.reopenButton(study(),view);assert.match(html,new RegExp(`data-live="reopen-gate" data-value="${stage}"`));
  }
  for(const view of ['processing','complete','report','files','viewer'])assert.equal(live.reopenButton(study(),view),'');
  assert.equal(live.reopenButton(study('waiting_review','assets_review'),'rooms'),'','the API reopens completed studies only');
 }
 assert.equal(boot({role:'viewer'}).live.reopenButton(study(),'components'),'');
 // A study with the single rooms review edits components on its rooms gate.
 assert.match(boot().live.reopenButton(study('completed','completed',false),'components'),/data-value="rooms_review"/);
});

test('reopening reads the plan first (dry run), writes only on confirmation, then forwards to the reopened review',async()=>{
 const {ctx,live,calls,modals,timers}=boot();const s=study();live.state.study=s;live.state.studyId=s.id;
 live.reloadStudy=async()=>s;const went=[];live.go=(screen,id)=>went.push([screen,id]);
 await live.actions['reopen-gate'](null,'components_review');
 assert.equal(calls.length,1);assert.deepEqual(plain(calls[0].body),{stage:'components_review',dry_run:true});
 assert.match(modals[0].title,/Change Components\?/);assert.match(modals[0].body,/Components/);assert.match(modals[0].body,/Assets/);assert.match(modals[0].body,/assets and costs and the report/);
 await modals[0].buttons['#live-reopen-gate-go'].onclick();
 assert.equal(calls.length,2);assert.deepEqual(plain(calls[1].body),{stage:'components_review'});assert.deepEqual(plain(went.at(-1)),['processing','study']);
 assert.deepEqual(plain(live.state.reopenTarget),{studyId:'study',stage:'components_review'});
 // Study progress forwards once the reopened review is open, and only then.
 const reopened={...s,status:'processing',current_stage:'components_review',revision:4};live.state.cache.set('study:study',{done:true,data:reopened});
 ctx.state.screen='processing';ctx.processing();assert.equal(timers.length,0,'still copying: stays on progress');
 live.state.cache.set('study:study',{done:true,data:{...reopened,status:'waiting_review'}});
 assert.match(ctx.processing(),/Opening the review/);timers.shift()();assert.deepEqual(plain(went.at(-1)),['components','study']);assert.equal(live.state.reopenTarget,null);
});

test('the New design switch is off by default, survives storage failures as off, and round-trips',()=>{
 const load=storage=>{const g={localStorage:storage,document:{documentElement:{dataset:{}}}};g.globalThis=g;vm.createContext(g);vm.runInContext(fs.readFileSync(path.join(__dirname,'../design-flag.js'),'utf8'),g);return g};
 const mem=new Map(),store={getItem:k=>mem.get(k)??null,setItem:(k,v)=>mem.set(k,v),removeItem:k=>mem.delete(k)};
 let g=load(store);assert.equal(g.BasisDesign.enabled(),false);assert.equal(g.document.documentElement.dataset.design,'current');
 assert.equal(g.BasisDesign.set(true),true);assert.equal(mem.get('basis-new-design-v1'),'on');assert.equal(load(store).BasisDesign.enabled(),true);
 g.BasisDesign.set(false);assert.equal(mem.has('basis-new-design-v1'),false);
 const broken={getItem(){throw Error('blocked')},setItem(){throw Error('blocked')},removeItem(){throw Error('blocked')}};
 g=load(broken);assert.equal(g.BasisDesign.enabled(),false);assert.equal(g.BasisDesign.set(true),true,'the choice holds for this page even when it cannot be saved');
});
