const {test}=require('node:test');const assert=require('node:assert/strict');
const G=require('../live/gates.js');
const study=(current_stage,status,components_gate)=>({id:'s1',revision:3,current_stage,status,components_gate});
const bar=s=>G.steps(s).map(x=>x.label+(x.state==='active'?'*':x.state==='done'?'+':'')).join(' ');

test('a study with the components gate, or not started yet, has seven steps including report signing',()=>{
 for(const gate of [true,null,undefined]){
  assert.equal(bar(study('rooms_review','waiting_review',gate)),'Upload+ Appraisal+ Rooms* Components Assets Results Report & sign');
 }
 assert.equal(bar(study('uploading','draft',null)),'Upload* Appraisal Rooms Components Assets Results Report & sign');
 assert.equal(bar(study('extracting','processing',true)),'Upload* Appraisal Rooms Components Assets Results Report & sign');
 // The room step after the rooms approval belongs to the Components step.
 assert.equal(bar(study('components_processing','processing',true)),'Upload+ Appraisal+ Rooms+ Components* Assets Results Report & sign');
 assert.equal(bar(study('components_review','waiting_review',true)),'Upload+ Appraisal+ Rooms+ Components* Assets Results Report & sign');
 assert.equal(bar(study('assets_processing','processing',true)),'Upload+ Appraisal+ Rooms+ Components+ Assets* Results Report & sign');
 assert.equal(bar(study('exporting','processing',true)),'Upload+ Appraisal+ Rooms+ Components+ Assets+ Results* Report & sign');
 assert.equal(bar(study('completed','completed',true)),'Upload+ Appraisal+ Rooms+ Components+ Assets+ Results+ Report & sign*');
 assert.deepEqual(G.steps(study('rooms_review','waiting_review',true)).map(x=>x.screen),['processing','appraisal','rooms','components','assets','complete','report']);
});

test('a study with the single rooms review keeps its original reviews plus the final report step',()=>{
 assert.equal(bar(study('rooms_review','waiting_review',false)),'Upload+ Appraisal+ Rooms* Assets Results Report & sign');
 assert.equal(bar(study('assets_processing','processing',false)),'Upload+ Appraisal+ Rooms+ Assets* Results Report & sign');
 assert.equal(bar(study('completed','completed',false)),'Upload+ Appraisal+ Rooms+ Assets+ Results+ Report & sign*');
});

test('the objects and inventories are edited at the components review, or the single rooms review',()=>{
 assert.equal(G.editStage(study('components_review','waiting_review',true)),'components_review');
 assert.equal(G.editStage(study('rooms_review','waiting_review',null)),'components_review');
 assert.equal(G.editStage(study('rooms_review','waiting_review',false)),'rooms_review');
 assert.equal(G.editScreen(study('rooms_review','waiting_review',true)),'components');
 assert.equal(G.editScreen(study('rooms_review','waiting_review',false)),'rooms');
 assert.equal(G.componentsGate(null),false);
});

test('an open gate opens its screen; anything else is the progress page',()=>{
 assert.equal(G.nextScreen(study('components_review','waiting_review',true)),'components');
 assert.equal(G.nextScreen(study('rooms_review','waiting_review',true)),'rooms');
 assert.equal(G.nextScreen(study('components_processing','processing',true)),'processing');
 assert.equal(G.nextScreen(study('components_review','failed',true)),'processing');
 assert.equal(G.nextScreen(study('completed','completed',true)),'complete');
});

test('the progress page lists the components stages only with the gate',()=>{
 assert.deepEqual(G.timeline(study('extracting','processing',true)),['extracting','appraisal_review','rooms_review','components_processing','components_review','assets_processing','assets_review','completed']);
 assert.deepEqual(G.timeline(study('extracting','processing',false)),['extracting','appraisal_review','rooms_review','assets_processing','assets_review','completed']);
 // Every stage the API knows is ordered (basis.db.enums.Stage).
 assert.deepEqual(G.ORDER,['uploading','extracting','appraisal_review','rooms_review','components_processing','components_review','assets_processing','assets_review','exporting','completed']);
});

test('a move is a photo_room correction naming the target room',()=>{
 const c=G.moveCorrection({document_id:'p1',filename:'Kitchen photo 2.jpg'},{id:'r1',name:'Kitchen'},{id:'r2',name:'Bathroom'});
 assert.deepEqual({entity_type:c.entity_type,entity_id:c.entity_id,before:c.before,after:c.after,key:c.key},{entity_type:'photo_room',entity_id:'p1',before:{room_id:'r1'},after:{room_id:'r2'},key:'photo_room:p1'});
 assert.equal(c.summary,'move to Bathroom');
 assert.deepEqual(G.moveCorrection({document_id:'p1',filename:'x'},null,{id:'r2',name:'B'}).before,{room_id:null});
});


test('viewing an approved step does not move workflow progress in either flow',()=>{
 for(const gate of [true,false])for(const view of ['rooms','appraisal','files']){
  const nav=G.navigation(study('assets_review','waiting_review',gate),view);
  const selected=nav.filter(s=>s.selected);assert.equal(selected.length,1);
  assert.equal(selected[0].label,{rooms:'Rooms',appraisal:'Appraisal',files:'Upload'}[view]);
  assert.equal(selected[0].state,'done');assert.equal(selected[0].status,'Completed');
  assert.equal(nav.find(s=>s.label==='Assets').status,'Needs review');
  assert.equal(nav[0].destination,'files');assert.equal(nav.at(-1).available,false);
 }
});
test('progress routes, child views and finished studies keep selection and availability distinct',()=>{
 for(const gate of [true,false]){
  assert.equal(G.navigation(study('assets_review','waiting_review',gate),'asset-detail').find(x=>x.selected).label,'Assets');
  assert.equal(G.navigation(study('rooms_review','waiting_review',gate),'viewer').find(x=>x.selected).label,gate?'Components':'Rooms');
  const finished=G.navigation(study('completed','completed',gate),'appraisal');
  assert(finished.every(x=>x.available));assert(finished.slice(0,-1).every(x=>x.status==='Completed'));assert.equal(finished.at(-1).status,'Checking…');assert.equal(finished.filter(x=>x.selected).length,1);
 }
 const processing=G.navigation(study('components_processing','processing',true),'processing');
 const current=processing.find(x=>x.selected);assert.equal(current.label,'Components');assert.equal(current.status,'In progress');assert.equal(current.destination,'processing');
 assert.equal(G.navigation(study('assets_processing','failed',true),'processing').find(x=>x.selected).status,'Needs attention');
 const draft=G.navigation(study('uploading','draft',true),'upload').find(x=>x.selected);
 assert.equal(draft.label,'Upload');assert.equal(draft.destination,'processing');
 assert.equal(G.navigation(study('extracting','processing',true),'processing')[0].destination,'processing');
});

const signedEntry=()=>({done:true,data:{study_id:'s1',revision:3,exported:true,skipped:null,missing_for_final:[],changed_since_signing:[],signature:{id:'sig',study_id:'s1',revision:3}}});
test('study completion requires current, signed, unchanged report evidence, not processing status',()=>{
 const s=study('completed','completed',true),entry=signedEntry();
 assert.equal(G.completion(s,entry).complete,true);
 assert.equal(G.completion(s,entry).title,'Study completed');
 assert.equal(G.navigation(s,'complete',entry).at(-1).status,'Signed');
 assert(G.steps(s,entry).every(x=>x.state==='done'));
 for(const patch of [
  {signature:null,missing_for_final:['signature']},{signature:null,missing_for_final:['firm','study','signature']},
  {changed_since_signing:['study']},{changed_since_signing:['firm']},{changed_since_signing:['firm','study']},
  {changed_since_signing:null},{changed_since_signing:undefined},{exported:false},{skipped:{message:'Allocation withheld'}},
  {missing_for_final:['firm']},{missing_for_final:undefined},{revision:2},{study_id:'other'},
  {signature:{id:'sig',study_id:'s1',revision:2}},{signature:{id:'sig',study_id:'other',revision:3}}
 ]){
  const e={done:true,data:{...entry.data,...patch}};
  assert.equal(G.completion(s,e).complete,false,JSON.stringify(patch));
  assert.notEqual(G.reviewContext(s,'components',e).tone,'complete');
  assert.equal(G.steps(s,e).at(-1).state,'active');
 }
 for(const e of [undefined,{done:false},{done:true,error:Error('offline')},{done:true,data:null}])assert.equal(G.completion(s,e).complete,false);
 for(const status of ['draft','processing','waiting_review','failed','stalled','archived'])assert.equal(G.completion({...s,status},entry).complete,false);
});
test('the final report is the next action for an unsigned study and changed signatures require attention',()=>{
 const s=study('completed','completed',true),entry=signedEntry();entry.data.signature=null;entry.data.missing_for_final=['signature'];
 const next=G.navigation(s,'processing',entry).find(x=>x.state==='active');
 assert.equal(next.label,'Report & sign');assert.equal(next.destination,'report');assert.equal(next.status,'Needs signature');assert(next.available);
 assert.equal(G.reviewContext(s,'complete',entry).title,'Awaiting signature');
 assert.equal(G.reviewContext(s,'complete',entry).action,'report');
 const changed=signedEntry();changed.data.changed_since_signing=['study'];
 assert.equal(G.reviewContext(s,'report',changed).title,'Report needs updating');
 assert.equal(G.reviewContext(s,'report',changed).action,null);
});
