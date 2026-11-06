// The UI review (November 2026): delivery status from the report, reopening an approved step,
// forwarding to the reopened review.
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

test('a finished study reads its delivery state from its report, and Checking… until that is known (never Finished by default)',()=>{
 const {live}=boot();const s=study();
 live.reportStateEntry=()=>undefined;assert.deepEqual(plain(live.delivery(s)),{label:'Checking…',tone:'pending'});
 live.reportStateEntry=()=>({done:false});assert.equal(live.delivery(s).label,'Checking…');
 live.reportStateEntry=()=>({done:true,error:Error('offline')});assert.deepEqual(plain(live.delivery(s)),{label:'Report status unavailable',tone:'pending'});
 live.reportStateEntry=()=>reportState(['firm']);assert.deepEqual(plain(live.delivery(s)),{label:'Update required',tone:'review'});
 live.reportStateEntry=()=>reportState([]);assert.deepEqual(plain(live.delivery(s)),{label:'Signed',tone:'done'});
 live.reportStateEntry=()=>reportState(null,false);assert.deepEqual(plain(live.delivery(s)),{label:'Needs signature',tone:'review'});
 live.reportStateEntry=()=>{throw Error('a study in progress must not read its report')};assert.equal(live.delivery(study('waiting_review','rooms_review')).label,'Reviews approved');
});
test('every finished study\'s report state loads, six requests at a time, and an unloaded one is neither due nor finished',()=>{
 const {live}=boot();const list=Array.from({length:8},(_,i)=>({...study(),id:'s'+i,revision:3}));list.push({...study('waiting_review','rooms_review'),id:'open'});
 const asked=[];live.reportStateEntry=x=>{asked.push(x.id);const e={done:false};live.state.cache.set('report-state:'+x.id+':'+x.revision,e);return e};
 live.loadDeliveries(list);assert.deepEqual(asked,['s0','s1','s2','s3','s4','s5']);
 live.loadDeliveries(list);assert.equal(asked.length,6,'six in flight: wait');
 live.state.cache.get('report-state:s0:3').done=true;const signed=reportState([]);signed.data.study_id='s0';signed.data.signature.study_id='s0';Object.assign(live.state.cache.get('report-state:s0:3'),signed);
 live.loadDeliveries(list);assert.deepEqual(asked.slice(6),['s6'],'one answered, one more starts');
 assert.equal(live.deliveryPeek(list[0]).label,'Signed');assert.equal(live.deliveryPeek(list[7]).label,'Checking…');
 assert(!asked.includes('open'),'a study still in review never reads its report');
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

test('the building formula renders one step per row, keeps every recorded word, and falls back when it does not parse',()=>{
 const V=require('../live/result-view.js');
 const f='(124.15/SF at 3200 SF x 3200 SF living area + basement (28.60 finished / 11.55 unfinished per SF of living area, 80.0 % finished) 80608 + baths 6 full: +5 x 9780 = 43062 (RSMeans Residential Costs 2020, facing page, printed p.64, Modifications)) x 1.2330 for 2024 (HCI 294.8 (July 2024) [index page: "The actual July 2024 Historical Index = 294.8"] / 239.1 (Jan 2020, est.)) from placed in service 2024-12-03 (intake) x 1.001 location from state CA (intake): mean of 37 CA cities (0.94-1.07), RSMeans Residential 2020 Location Factors';
 const html=V.formulaSteps(f,'686535.94');
 assert.equal((html.match(/<tr>/g)||[]).length,1+5,'a header row and five steps');
 assert.match(html,/calc-total[^]*\$686,535\.94/);
 const plainText=html.replace(/<[^>]+>/g,' ').replace(/&quot;/g,'"').replace(/&#39;/g,"'");
 for(const word of f.replace(/[()[\]]/g,' ').split(/\s+/).filter(Boolean))assert.ok(plainText.includes(word),`kept: ${word}`);
 for(const bad of ['',null,undefined,'a + b','(a + b','(a) x (b','a x 2','(a + "b) x 2'])assert.equal(V.formulaSteps(bad,1),'',`falls back: ${bad}`);
});

test('an asset flag shows each recorded reason once, with its fix and code, and no placeholder text',()=>{
 globalThis.BasisReasons=require('../live/review-reasons.js');
 const H=require('../live/error-help.js');
 const html=H.flags(['model:The label is "other"; the notes are empty.','cross:COST_ROW_MISSING','takeoff:upper_bound'],['BASIS-ASSETS-009','BASIS-CHECK-012','BASIS-ASSETS-010','BASIS-ASSETS-008']);
 const rows=html.split('</li>').filter(Boolean);
 assert.equal(rows.length,4,'three reasons and the one code no reason matches');
 assert.match(rows[0],/asked for review: “The label is &quot;other&quot;; the notes are empty\.”/);
 assert.match(rows[0],/BASIS-ASSETS-009/);assert.doesNotMatch(html,/Read the reason/);
 assert.match(rows[1],/no cost yet[^]*cost table[^]*BASIS-CHECK-012/);assert.doesNotMatch(html,/Price it by hand/);
 assert.match(rows[2],/upper bound/);assert.doesNotMatch(rows[2],/flag-fix/,'its sentence already says what to do');
 assert.match(rows[3],/BASIS-ASSETS-008/);
 assert.equal(H.flags([],[]),'');
 delete globalThis.BasisReasons;
});

test('the finished-study banner names the next report action',()=>{
 const label=entry=>G.reviewContext(study(),'complete',entry).actionLabel;
 assert.equal(label(reportState(null,false)),'Review and sign revision 3');
 assert.equal(label(reportState(['firm'])),'Update the report');
 assert.equal(label(reportState([])),'View signed report');
 assert.equal(G.reviewContext(study(),'report',reportState(null,false)).action,null,'no link to the page you are on');
});
