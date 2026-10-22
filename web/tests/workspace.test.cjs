const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const Model=require('../workspace-model.js');
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../review-data.js'),'utf8'),context);
const members=[{id:'taylor',name:'Taylor Reed',role:'Owner'},{id:'alex',name:'Alex Morgan',role:'Engineer'},{id:'jordan',name:'Jordan Lee',role:'Engineer'},{id:'casey',name:'Casey Park',role:'Engineer'}];
const owners=['alex','jordan','casey','taylor','alex','jordan'];
const seed=Array.from(vm.runInContext('REVIEW.studies',context),(s,i)=>({...s,created_by_id:owners[i],created_by_name:members.find(m=>m.id===owners[i]).name,last_edited_by:members.find(m=>m.id===owners[i]).name}));
const studies=Model.studies(seed,members),owner=members[0],engineer=members[1];
test('owner review queue contains owned or explicitly assigned reviews, not every visible team review',()=>{
 assert.deepEqual(Model.queue(studies,owner).map(s=>s.property_name),['Fairview Medical Center']);
 assert.equal(Model.visible(studies[0],owner),true);
 assert.equal(Model.queue(studies,owner).some(s=>s.property_name==='Harbor House'),false);
 assert.equal(Model.queue(studies,{id:'unassigned-owner',role:'Owner'}).length,0);
});
test('engineer sees their own studies and their own appraisal review',()=>{
 assert.deepEqual(Model.scopeRows(studies,engineer).map(s=>s.property_name),['Harbor House','Lakewood Residence']);
 assert.deepEqual(Model.queue(studies,engineer).map(s=>s.property_name),['Harbor House']);
 assert.equal(Model.visible(studies[1],engineer),false);
});
test('an explicit review assignment grants fixture visibility while another teammate remains excluded',()=>{
 const assigned={...studies[1],assigned_reviewer_ids:['alex']};
 assert.equal(Model.visible(assigned,engineer),true);assert.equal(Model.queue([assigned],engineer).length,1);
 assert.equal(Model.visible(assigned,members[3]),false);
});
test('team, my studies, and creator filters return different correct sets',()=>{
 assert.equal(Model.scopeRows(studies,owner).length,6);
 assert.deepEqual(Model.scopeRows(studies,owner,'My studies').map(s=>s.property_name),['Northgate Business Park']);
 assert.deepEqual(Model.scopeRows(studies,owner,'Workspace studies','alex').map(s=>s.property_name),['Harbor House','Lakewood Residence']);
});
test('search and status filters can be combined and produce a real empty state',()=>{
 assert.equal(Model.list({studies,user:engineer,query:'  HARBOR  ',status:'Needs review'}).total,1);
 assert.equal(Model.list({studies,user:engineer,query:'Harbor',status:'Completed'}).total,0);
 assert.equal(Model.list({studies,user:owner,query:'Pasadena'}).total,1);
 assert.equal(Model.list({studies,user:owner,status:'Needs review'}).total,3);
});
test('client search respects status and study visibility',()=>{
 assert.equal(Model.list({studies,user:engineer,query:'  LAKEWOOD HOLDINGS  '}).rows[0].property_name,'Lakewood Residence');
 assert.equal(Model.list({studies,user:engineer,query:'Lakewood Holdings',status:'Needs review'}).total,0);
 assert.equal(Model.list({studies,user:engineer,query:'Fairview Health'}).total,0);
 assert.equal(Model.list({studies,user:owner,query:'Fairview Health'}).total,1);
 assert.equal(Model.studies([{...seed[0],client_name:'Custom client'}],members)[0].client_name,'Custom client');
});
test('240-study fixture has unique IDs and pagination has no overlap or omitted rows',()=>{
 const large=Model.studies(seed,members,true);assert.equal(large.length,240);assert.equal(new Set(large.map(s=>s.id)).size,240);
 const first=Model.list({studies:large,user:owner,page:1,pageSize:10}),second=Model.list({studies:large,user:owner,page:2,pageSize:10}),last=Model.list({studies:large,user:owner,page:999,pageSize:10});
 assert.equal(first.pages,24);assert.equal(first.rows.length,10);assert.equal(last.page,24);assert.equal(last.start,231);assert.equal(last.end,240);
 assert.equal(first.rows.some(a=>second.rows.some(b=>a.id===b.id)),false);
 assert.equal(Model.list({studies:large,user:owner,page:1,pageSize:25}).rows.length,25);
});
test('search after pagination clamps to the remaining page and sorting changes order',()=>{
 const large=Model.studies(seed,members,true);
 const match=Model.list({studies:large,user:owner,page:24,query:'Harbor House'});assert.equal(match.page,1);assert.equal(match.rows[0].property_name,'Harbor House');
 const byName=Model.list({studies,user:owner,sort:'name'}).rows.map(s=>s.property_name);
 assert.deepEqual(byName,[...byName].sort((a,b)=>a.localeCompare(b)));
 assert.notEqual(Model.list({studies,user:owner,sort:'updated'}).rows[0].id,Model.list({studies,user:owner,sort:'oldest'}).rows[0].id);
});
test('activity is scoped to permitted studies and exposes attributed field changes',()=>{
 const events=Model.activity(studies,engineer);assert(events.length>0);assert(events.every(e=>['study-001','study-005'].includes(e.study_id)));
 assert.equal(Model.activity(studies,engineer,{studyId:'study-002'}).length,0);
 const changes=Model.activity(studies,owner,{studyId:'study-002',type:'changes'});
 assert.equal(changes.length,1);assert.equal(changes[0].actor.name,'Jordan Lee');assert.equal(changes[0].details[0].before,'1,320 sq ft');assert.equal(changes[0].details[0].after,'1,420 sq ft');assert(changes[0].at.endsWith('Z'));
});
test('company statistics count only active stages and completed schedules',()=>{
 assert.deepEqual(Model.portfolioStats(studies),{total:6,active:5,completed:1,review:2,attention:1});
 const mixed=[...studies,{status:'archived'},{status:'cancelled'}];
 assert.equal(Model.portfolioStats(mixed).total,8);assert.equal(Model.portfolioStats(mixed).active,5);
 assert.deepEqual(Model.portfolioStats([]),{total:0,active:0,completed:0,review:0,attention:0});
});
test('team workload is attributed by creator and reconciles to company statistics',()=>{
 const workload=Model.teamWorkload(studies,members);
 assert.deepEqual(workload.map(m=>[m.id,m.active,m.completed]),[['taylor',0,1],['alex',2,0],['jordan',2,0],['casey',1,0]]);
 assert.equal(workload.reduce((n,m)=>n+m.total,0),studies.length);
 const large=Model.studies(seed,members,true);
 assert.equal(Model.teamWorkload(large,members).reduce((n,m)=>n+m.active,0),Model.portfolioStats(large).active);
});
test('an unstarted draft does not invent uploaded files in its activity history',()=>{
 const events=Model.activity(studies,engineer,{studyId:'study-005'});
 assert.deepEqual(events.map(e=>e.type),['created']);
});
test('library file search matches property, client and filename without revealing another engineers files',()=>{
 const files=[{name:'Kitchen.jpg',category:'Property photos',study_id:'study-001'},{name:'Appraisal.pdf',category:'Appraisal',study_id:'study-002'}];
 assert.equal(Model.fileList({studies,user:engineer,files,query:'  HARBOR PROPERTY  '}).length,1);
 assert.equal(Model.fileList({studies,user:engineer,files,query:'kitchen'}).length,1);
 assert.equal(Model.fileList({studies,user:engineer,files,query:'appraisal'}).length,0);
 assert.equal(Model.fileList({studies,user:owner,files,studyId:'study-002',category:'Appraisal'}).length,1);
 assert.equal(Model.fileList({studies,user:engineer,files,studyId:'study-002'}).length,0);
});
test('the company archive includes every engineers history while Home remains personal',()=>{
 const home=Model.list({studies,user:engineer});
 const archive=Model.list({studies,user:engineer,companyArchive:true});
 assert.equal(home.total,2);assert.equal(archive.total,6);
 assert.equal(Model.list({studies,user:engineer,companyArchive:true,status:'Completed'}).rows[0].property_name,'Northgate Business Park');
 assert.equal(Model.list({studies,user:engineer,companyArchive:true,query:'Taylor Reed'}).total,1);
 assert.equal(Model.list({studies,user:engineer,companyArchive:true,query:'Fairview Avenue'}).total,1);
 const files=[{name:'Report.pdf',category:'Appraisal',study_id:'study-004'}];
 assert.equal(Model.fileList({studies,user:engineer,files,companyArchive:true,query:'TAYLOR'}).length,1);
 assert.equal(Model.fileList({studies,user:engineer,files,companyArchive:true,query:'Northgate Drive'}).length,1);
 assert.equal(Model.fileList({studies,user:engineer,files,query:'Taylor'}).length,0);
});
