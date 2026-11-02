const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const R=require('../live/report.js');
const root=path.join(__dirname,'..');
test('the primary entry loads restored workflow modules and the report controller exactly once',()=>{
 const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
 const scripts=[...html.matchAll(/<script src="([^"?]+)[^"]*"/g)].map(m=>m[1]);
 assert.equal(scripts.length,new Set(scripts).size);
 for(const f of ['live/study-overview.js','live/review-layout.js','live/room-placement.js','live/live-results.js','live/report.js','live/live-report.js','vision-workbench.js','interface-system.js'])assert.ok(scripts.includes(f),f);
 assert.ok(scripts.indexOf('live/live-report.js')>scripts.indexOf('live/live-results.js'));
 assert.ok(scripts.indexOf('vision-model.js')<scripts.indexOf('live/live-review.js'));
 assert.ok(scripts.includes('vision-model.js'));
 for(const f of scripts)assert.ok(fs.existsSync(path.join(root,f)),f);
});
function reportHarness(over={}){
 const study={id:'s1',revision:5,org_id:'org',property_name:'Synthetic study',status:'completed'},state={study_id:study.id,revision:5,exported:true,skipped:null,files:['report.pdf','schedule.csv','schedule.xlsx'],missing_for_final:['signature'],signature:null,...over};
 const calls=[],Live={state:{study,studyId:study.id},actions:{},screens:{},withStudy:fn=>fn(study),steps:()=>'<nav>Report & sign</nav>',api:{request:(...args)=>{calls.push(args);return Promise.resolve({})}},me:()=>({memberships:[{org_id:'org',role:'owner'}]}),load:key=>({done:true,data:key.startsWith('report-state:')?state:key.startsWith('report-answers:')?{report_answers_version:1}:null}),route:(view,id)=>`#${view}/${id}`,render(){},when:()=> 'date',failed:()=> 'error',loading:()=> 'loading'};
 const context={BASIS_CONFIG:{mode:'live'},BasisLive:Live,BasisReport:R,BasisGates:require('../live/gates.js'),heading:t=>`<h1>${t}</h1>`,complete:()=>'<main>Recorded allocation and reconciliation</main>',esc:String,icon:()=>'',};
 vm.runInNewContext(fs.readFileSync(path.join(root,'live/live-report.js'),'utf8'),context);
 return {context,Live,calls};
}
test('report controls have a dedicated final step after results and do not submit requests while rendering',()=>{
 const h=reportHarness(),html=h.Live.screens.report();
 assert.match(h.context.complete(),/Recorded allocation/);assert.doesNotMatch(h.context.complete(),/Sign report/);
 assert.match(html,/Report & sign/);assert.match(html,/Client report/);assert.match(html,/report-preview/);assert.match(html,/Sign report/);
 assert.equal((html.match(/id="live-report-title"/g)||[]).length,1);assert.equal(h.calls.length,0);
 for(const action of ['report-preview','report-download','report-edit-answers','report-edit-profile','report-sign'])assert.equal(typeof h.Live.actions[action],'function');
});
test('existing signed report remains downloadable and shows changed-input warning on the final step',()=>{
 const h=reportHarness({signature:{id:'sig',engineer:{name:'Engineer',credentials:'PE'},signed_at:'2026-01-01',pdf_sha256:'a'.repeat(64)},changed_since_signing:['study']}),html=h.Live.screens.report();
 assert.doesNotMatch(html,/ready to deliver|the one to deliver/);assert.match(html,/Signed report \(PDF\)/);assert.match(html,/Signed with earlier information/);assert.doesNotMatch(html,/data-live="report-sign"/);
});


test('report summary shows truthful loading/errors, escapes detail, and links to report controls',()=>{
 const h=reportHarness();h.context.state={screen:'complete'};
 let html=h.Live.reportSummary(h.Live.state.study);
 assert.match(html,/Draft report/);assert.match(html,/href="#report\/s1"/);assert.equal(h.calls.length,0);
 h.context.state.screen='processing';html=h.Live.reportSummary(h.Live.state.study);assert.match(html,/href="#report\/s1"/);
 h.Live.load=()=>({done:false});assert.match(h.Live.reportSummary(h.Live.state.study),/Checking report status/);
 h.Live.load=()=>({done:true,error:Error('network')});assert.match(h.Live.reportSummary(h.Live.state.study),/Report status unavailable/);
 h.context.esc=v=>String(v).replaceAll('<','&lt;');h.Live.load=()=>({done:true,data:{skipped:{message:'<script>unsafe</script>'}}});
 html=h.Live.reportSummary(h.Live.state.study);assert.doesNotMatch(html,/<script>/);assert.match(html,/&lt;script>/);
});

test('report state cache is scoped to revision and requests the supplied study, not another open study',()=>{
 const h=reportHarness(),loads=[];
 h.Live.load=(key,loader)=>{loads.push(key);loader();return {done:false}};
 h.Live.reportStateEntry({...h.Live.state.study,id:'another study',revision:6});
 assert.deepEqual(loads,['report-state:another study:6']);
 assert.deepEqual(h.calls[0],['GET','/v1/studies/another%20study/report/state']);
 assert.equal(h.Live.reportStateEntry({...h.Live.state.study,status:'processing'}),null);
});
test('current signed report is ready to deliver only on matching study and revision',()=>{
 const h=reportHarness({missing_for_final:[],changed_since_signing:[],signature:{id:'sig',study_id:'s1',revision:5,engineer:{name:'Engineer',credentials:'PE'},signed_at:'2026-01-01',pdf_sha256:'a'.repeat(64)}});
 assert.match(h.Live.screens.report(),/ready to deliver/);assert.equal(h.calls.length,0);
});

const signedState={missing_for_final:[],changed_since_signing:['study'],signature:{id:'sig',study_id:'s1',revision:5,engineer:{name:'Engineer',credentials:'PE'},signed_at:'2026-01-01',pdf_sha256:'a'.repeat(64)}};
test('a signed study offers the documented recovery path: a new revision at the assets review, for engineers of a completed study only',async()=>{
 const h=reportHarness(signedState),html=h.Live.screens.report();
 assert.match(html,/data-live="report-new-revision"/);assert.match(html,/Start a new revision/);assert.match(html,/start a new revision: the assets review opens again/);
 // A viewer may not; neither may anyone on an archived study; an unsigned draft has nothing to revise.
 const viewer=reportHarness(signedState);viewer.Live.me=()=>({memberships:[{org_id:'org',role:'viewer'}]});assert.doesNotMatch(viewer.Live.screens.report(),/report-new-revision/);
 const archived=reportHarness(signedState);archived.Live.state.study.status='archived';assert.doesNotMatch(archived.Live.screens.report(),/report-new-revision/);
 assert.doesNotMatch(reportHarness().Live.screens.report(),/report-new-revision/);
 // The action reads the plan first (dry run), shows it, and only the confirmation reopens.
 const opened=[];h.Live.guarded=(n,fn)=>fn();h.Live.invalidate=()=>{};h.Live.reloadStudy=async()=>{};h.Live.go=(...a)=>opened.push(a);
 h.Live.api.request=(...args)=>{h.calls.push(args);return Promise.resolve(args[2]?.body?.dry_run?{stage:'assets_review',from_revision:5,revision:6,recomputed:['exporting'],reviews:['assets_review'],carried:['assets']}:{revision:6,stage:'assets_review'})};
 let confirm=null,modalHtml='';h.context.modal=(title,body)=>{modalHtml=title+body;return {querySelector:()=>({set onclick(fn){confirm=fn}}),remove(){}}};h.context.closeBtn='';h.context.toast=()=>{};
 await h.Live.actions['report-new-revision']();
 assert.deepEqual(JSON.parse(JSON.stringify(h.calls)),[['POST','/v1/studies/s1/reopen',{body:{stage:'assets_review',dry_run:true}}]]);
 assert.match(modalHtml,/Revision 6 opens at the assets review with the assets of revision 5/);assert.match(modalHtml,/signed report of revision 5 is kept/);
 assert.equal(opened.length,0);
 await confirm();
 assert.deepEqual(JSON.parse(JSON.stringify(h.calls[1])),['POST','/v1/studies/s1/reopen',{body:{stage:'assets_review'}}]);assert.deepEqual(JSON.parse(JSON.stringify(opened)),[['processing','s1']]);
});
test('earlier signed revisions are listed with their own PDF download, by revision number',()=>{
 const h=reportHarness({missing_for_final:['signature'],signature:null});
 const base=h.Live.load;h.Live.load=key=>key.startsWith('revisions:')?{done:true,data:{items:[{revision:5,current:true,signature:null},{revision:4,current:false,signature:{id:'old',signed_by:'u',signed_at:'2026-01-01'}},{revision:3,current:false,signature:null}]}}:base(key);
 const html=h.Live.screens.report();
 assert.match(html,/Earlier signed reports/);assert.match(html,/data-live="report-download-revision" data-value="4"/);assert.doesNotMatch(html,/data-value="3"/);assert.doesNotMatch(html,/data-value="5"/);
 assert.equal(typeof h.Live.actions['report-download-revision'],'function');
 // Revision 1 asks nothing; a failed history says so without hiding the report.
 const first=reportHarness();first.Live.state.study.revision=1;assert.doesNotMatch(first.Live.screens.report(),/Earlier signed reports|Loading earlier/);
 const failed=reportHarness();failed.Live.load=key=>key.startsWith('revisions:')?{done:true,error:Error('offline')}:base(key);failed.Live.errorText=e=>e.message;
 assert.match(failed.Live.screens.report(),/Earlier revisions could not be loaded/);assert.match(failed.Live.screens.report(),/Client report/);
});
