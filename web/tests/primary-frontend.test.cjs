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
