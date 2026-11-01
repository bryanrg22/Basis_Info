const {test}=require('node:test');const assert=require('node:assert/strict');
const R=require('../live/report.js');
const state=(over={})=>({study_id:'s',revision:2,exported:true,skipped:null,files:['report.pdf','schedule.csv','schedule.xlsx'],missing_for_final:['signature'],signature:null,firm_profile_version:1,report_answers_version:1,...over});
const TAG='a'.repeat(64);

test('the status follows the state: signed, not exported, skipped, draft',()=>{
 assert.equal(R.status(null),'loading');
 assert.equal(R.status(state({signature:{id:'x'}})),'signed');
 assert.equal(R.status(state({exported:false})),'not_exported');
 assert.equal(R.status(state({skipped:{code:'BASIS-REPORT-002',message:'withheld'}})),'skipped');
 assert.equal(R.status(state()),'draft');
});

test('the checklist marks each input of a final report',()=>{
 const items=R.checklist(state({missing_for_final:['firm','study','signature']}));
 assert.deepEqual(items.map(i=>[i.key,i.done]),[['firm',false],['study',false],['signature',false]]);
 assert.deepEqual(R.checklist(state({missing_for_final:[],signature:{id:'x'}})).map(i=>i.done),[true,true,true]);
});

test('signing waits for the export, the inputs, the role and the opened preview, in that order',()=>{
 assert.match(R.signBlocker(state({exported:false}),'owner',TAG),/assets review is approved/);
 assert.match(R.signBlocker(state({skipped:{message:'the allocation was withheld'}}),'owner',TAG),/withheld/);
 assert.match(R.signBlocker(state(),'viewer',TAG),/engineer or an owner/);
 assert.match(R.signBlocker(state(),null,TAG),/engineer or an owner/);
 assert.match(R.signBlocker(state({missing_for_final:['firm','study','signature']}),'engineer',TAG),/firm profile/);
 assert.match(R.signBlocker(state({missing_for_final:['study','signature']}),'engineer',TAG),/study details/);
 assert.match(R.signBlocker(state(),'engineer',null),/Open the preview first/);
 assert.equal(R.signBlocker(state(),'engineer',TAG),'');
 assert.equal(R.signBlocker(state(),'owner',TAG),'');
 assert.match(R.signBlocker(state({signature:{id:'x'}}),'owner',TAG),/signed/);
});

test('the sign body carries the revision, the unquoted preview tag and the trimmed engineer',()=>{
 const body=R.signBody(state(),`"${TAG}"`,{name:' A. Engineer ',credentials:'PE',license_number:'',license_state:null});
 assert.deepEqual(body,{revision:2,reviewed_sha256:TAG,engineer:{name:'A. Engineer',credentials:'PE'},attest:true,statement_version:R.CERTIFICATION_VERSION});
});

test('the certification states the inspection as the report prints it',()=>{
 const visit=R.certification({photographs:'engineer_site_visit',photographed_by:'A. Engineer',photographed_on:'2025-03-01'});
 assert.equal(visit[1],'I inspected the property on March 1, 2025.');
 const supplied=R.certification({photographs:'taxpayer_supplied',photographed_by:'the owner',photographed_on:'2025-02-20'});
 assert.equal(supplied[1],'I did not inspect the property; the analysis relies on photographs supplied by the owner.');
 assert.equal(R.certification(null)[1],'[the inspection statement]');
 assert.equal(visit.length,4);
});

test('the answers form checks every field the API requires',()=>{
 const ok=R.answersBody({taxpayer_name:' Holdings LLC ',photographs:'taxpayer_supplied',photographed_by:'the owner',photographed_on:'2025-02-20'});
 assert.deepEqual(ok,{body:{taxpayer_name:'Holdings LLC',photographs:'taxpayer_supplied',photographed_by:'the owner',photographed_on:'2025-02-20'},problems:[]});
 const bad=R.answersBody({taxpayer_name:'',photographs:'drone',photographed_by:'',photographed_on:'02/20/2025'});
 assert.equal(bad.problems.length,4);
});

test('the profile form keeps the logo, upper-cases the state and refuses a bad one',()=>{
 const {body,problems}=R.profileBody({name:'Example Engineering',address:'',phone:' 555 ',engineer_name:'A. Engineer',credentials:'PE',license_number:'123',license_state:'ny'},'iVBORw0KGgo=');
 assert.deepEqual(problems,[]);
 assert.deepEqual(body,{name:'Example Engineering',address:null,phone:'555',logo_png:'iVBORw0KGgo=',engineer:{name:'A. Engineer',credentials:'PE',license_number:'123',license_state:'NY'}});
 assert.equal(R.profileBody({name:'x',engineer_name:'y',credentials:'PE',license_state:'New York'}).problems.length,1);
 assert.equal(R.profileBody({}).problems.length,3);
});
