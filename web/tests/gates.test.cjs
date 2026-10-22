const {test}=require('node:test');const assert=require('node:assert/strict');
const G=require('../live/gates.js');
const study=(current_stage,status,components_gate)=>({current_stage,status,components_gate});
const bar=s=>G.steps(s).map(x=>x.label+(x.state==='active'?'*':x.state==='done'?'+':'')).join(' ');

test('a study with the components gate, or not started yet, has six steps',()=>{
 for(const gate of [true,null,undefined]){
  assert.equal(bar(study('rooms_review','waiting_review',gate)),'Upload+ Appraisal+ Rooms* Components Assets Schedule');
 }
 assert.equal(bar(study('uploading','draft',null)),'Upload* Appraisal Rooms Components Assets Schedule');
 assert.equal(bar(study('extracting','processing',true)),'Upload* Appraisal Rooms Components Assets Schedule');
 // The room step after the rooms approval belongs to the Components step.
 assert.equal(bar(study('components_processing','processing',true)),'Upload+ Appraisal+ Rooms+ Components* Assets Schedule');
 assert.equal(bar(study('components_review','waiting_review',true)),'Upload+ Appraisal+ Rooms+ Components* Assets Schedule');
 assert.equal(bar(study('assets_processing','processing',true)),'Upload+ Appraisal+ Rooms+ Components+ Assets* Schedule');
 assert.equal(bar(study('exporting','processing',true)),'Upload+ Appraisal+ Rooms+ Components+ Assets+ Schedule*');
 assert.equal(bar(study('completed','completed',true)),'Upload+ Appraisal+ Rooms+ Components+ Assets+ Schedule+');
 assert.deepEqual(G.steps(study('rooms_review','waiting_review',true)).map(x=>x.screen),['processing','appraisal','rooms','components','assets','complete']);
});

test('a study with the single rooms review keeps the five steps it had',()=>{
 assert.equal(bar(study('rooms_review','waiting_review',false)),'Upload+ Appraisal+ Rooms* Assets Schedule');
 assert.equal(bar(study('assets_processing','processing',false)),'Upload+ Appraisal+ Rooms+ Assets* Schedule');
 assert.equal(bar(study('completed','completed',false)),'Upload+ Appraisal+ Rooms+ Assets+ Schedule+');
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
