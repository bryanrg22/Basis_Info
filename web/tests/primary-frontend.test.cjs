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
 const study={id:'s1',revision:5,org_id:'org',property_name:'Synthetic study'},state={revision:5,exported:true,skipped:null,files:['report.pdf','schedule.csv','schedule.xlsx'],missing_for_final:['signature'],signature:null,...over};
 const calls=[],Live={state:{study,studyId:study.id},actions:{},api:{request:(...args)=>{calls.push(args);return Promise.resolve({})}},me:()=>({memberships:[{org_id:'org',role:'owner'}]}),load:key=>({done:true,data:key.startsWith('report-state:')?state:key.startsWith('report-answers:')?{report_answers_version:1}:null}),render(){},when:()=> 'date',failed:()=> 'error',loading:()=> 'loading'};
 const context={BASIS_CONFIG:{mode:'live'},BasisLive:Live,BasisReport:R,complete:()=>'<main>Recorded allocation and reconciliation</main>',esc:String,icon:()=>'',};
 vm.runInNewContext(fs.readFileSync(path.join(root,'live/live-report.js'),'utf8'),context);
 return {context,Live,calls};
}
test('report controls coexist with the recovered result and do not submit requests while rendering',()=>{
 const h=reportHarness(),html=h.context.complete();
 assert.match(html,/Recorded allocation and reconciliation/);assert.match(html,/Client report/);assert.match(html,/report-preview/);assert.match(html,/Sign report/);
 assert.equal((html.match(/id="live-report-title"/g)||[]).length,1);assert.equal(h.calls.length,0);
 for(const action of ['report-preview','report-download','report-edit-answers','report-edit-profile','report-sign'])assert.equal(typeof h.Live.actions[action],'function');
});
test('existing signed report remains downloadable and shows changed-input warning alongside results',()=>{
 const h=reportHarness({signature:{id:'sig',engineer:{name:'Engineer',credentials:'PE'},signed_at:'2026-01-01',pdf_sha256:'a'.repeat(64)},changed_since_signing:['study']}),html=h.context.complete();
 assert.match(html,/Recorded allocation/);assert.match(html,/Signed report \(PDF\)/);assert.match(html,/Signed with earlier information/);assert.doesNotMatch(html,/data-live="report-sign"/);
});
