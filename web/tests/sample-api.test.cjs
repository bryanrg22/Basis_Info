// The sample API (web/live/sample-api.js): every answer validated against docs/api/openapi.json,
// the write paths, the proposed section, and that the fixture holds no client data.
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),vm=require('node:vm');
const A=require('../live/sample-api.js');
const WEB=path.join(__dirname,'..'),OPENAPI=JSON.parse(fs.readFileSync(path.join(WEB,'..','docs','api','openapi.json'),'utf8'));
const fetchAsset=p=>fs.readFileSync(path.join(WEB,p));

/* ---------- a small JSON-schema validator (the subset openapi.json uses) ---------- */
// Proposed fields are the only extras allowed, and only where the contract puts them.
const PROPOSED_EXTRAS=new Set(['PhotoObjectOut.exclusion','CostOut.override']);
function validate(schema,value,where='$',owner=null,errors=[]){
 if(schema.$ref){const name=schema.$ref.split('/').pop();return validate(OPENAPI.components.schemas[name],value,where,name,errors)}
 if(schema.allOf)for(const s of schema.allOf)validate(s,value,where,owner,errors);
 for(const k of ['anyOf','oneOf'])if(schema[k]){
  const passing=schema[k].filter(s=>!validate(s,value,where,owner,[]).length).length;
  if(k==='anyOf'?passing<1:passing!==1)errors.push(`${where}: ${k} matched ${passing} of ${schema[k].length} (${JSON.stringify(value)?.slice(0,120)})`);
 }
 if('const' in schema&&value!==schema.const)errors.push(`${where}: expected const ${JSON.stringify(schema.const)}`);
 if(schema.enum&&!schema.enum.includes(value))errors.push(`${where}: ${JSON.stringify(value)} not in enum`);
 const type=schema.type||(schema.properties?'object':null);
 if(type){
  const types=Array.isArray(type)?type:[type];
  const is={null:value===null,string:typeof value==='string',boolean:typeof value==='boolean',integer:Number.isInteger(value),number:typeof value==='number'&&Number.isFinite(value),array:Array.isArray(value),object:value!==null&&typeof value==='object'&&!Array.isArray(value)};
  if(!types.some(t=>is[t])){errors.push(`${where}: expected ${types.join('|')}, got ${value===null?'null':Array.isArray(value)?'array':typeof value}`);return errors}
 }
 if(typeof value==='string'){
  if(schema.minLength!==undefined&&value.length<schema.minLength)errors.push(`${where}: shorter than ${schema.minLength}`);
  if(schema.maxLength!==undefined&&value.length>schema.maxLength)errors.push(`${where}: longer than ${schema.maxLength}`);
  if(schema.pattern&&!new RegExp(schema.pattern,'u').test(value))errors.push(`${where}: does not match ${schema.pattern}`);
 }
 if(typeof value==='number'){
  if(schema.minimum!==undefined&&value<schema.minimum)errors.push(`${where}: below ${schema.minimum}`);
  if(schema.maximum!==undefined&&value>schema.maximum)errors.push(`${where}: above ${schema.maximum}`);
 }
 if(Array.isArray(value)&&schema.items)value.forEach((v,i)=>validate(schema.items,v,`${where}[${i}]`,null,errors));
 if(value&&typeof value==='object'&&!Array.isArray(value)){
  const props=schema.properties||{};
  for(const r of schema.required||[])if(!(r in value))errors.push(`${where}: missing required ${r}`);
  for(const [k,v] of Object.entries(value)){
   if(props[k]){validate(props[k],v,`${where}.${k}`,null,errors);continue}
   if(schema.additionalProperties===false)errors.push(`${where}: ${k} is not allowed (additionalProperties false)`);
   else if(schema.additionalProperties&&typeof schema.additionalProperties==='object')validate(schema.additionalProperties,v,`${where}.${k}`,null,errors);
   else if(schema.properties&&!PROPOSED_EXTRAS.has(`${owner}.${k}`))errors.push(`${where}: ${k} is not in ${owner||'the schema'}`);
  }
 }
 return errors;
}
// The 200 (or given status) schema of a route, by its openapi template.
function responseSchema(method,template,status){
 const op=OPENAPI.paths[template]?.[method.toLowerCase()];assert.ok(op,`${method} ${template} is in openapi.json`);
 const r=op.responses[String(status)];assert.ok(r,`${method} ${template} documents ${status}`);
 return r.content?.['application/json']?.schema||r.content?.['application/problem+json']?.schema;
}
function check(method,template,res,status=200){
 assert.equal(res.status,status,`${method} ${template}: ${JSON.stringify(res.body).slice(0,300)}`);
 const errors=validate(responseSchema(method,template,status),res.body);
 assert.deepEqual(errors,[],`${method} ${template}`);
 return res.body;
}
const problem=(res,status,suffix)=>{assert.equal(res.status,status,JSON.stringify(res.body));assert.deepEqual(validate({$ref:'#/components/schemas/ProblemDetail'},res.body),[]);if(suffix)assert.equal(res.body.type,'https://basis.invalid/problems/'+suffix);return res.body};

function instance(state,{clock}={}){
 const t={now:Date.parse('2026-11-05T12:00:00Z')};
 const a=A.create({state,now:()=>t.now,fetchAsset});
 return {a,t,advance:(ms=2001)=>{t.now+=ms},id:a.mainStudyId(),get:(p,q='')=>a.call('GET',p+q),post:(p,body,headers)=>a.call('POST',p,{body,headers}),...clock};
}
const S=id=>`/v1/studies/${id}`;

/* ---------- every read, every state, against openapi.json ---------- */
async function readAll(a,studyId,label){
 const T=S('{study_id}'),get=p=>a.call('GET',p);
 check('GET','/health',await get('/health'));
 const me=check('GET','/v1/me',await get('/v1/me')),org=me.memberships[0].org_id;
 check('GET','/v1/orgs/{org_id}/profile',await get(`/v1/orgs/${org}/profile`));
 check('GET','/v1/orgs/{org_id}/members',await get(`/v1/orgs/${org}/members`));
 check('GET','/v1/orgs/{org_id}/invitations',await get(`/v1/orgs/${org}/invitations`));
 check('GET','/v1/studies',await get('/v1/studies'));
 const study=check('GET',T,await get(S(studyId)));
 check('GET',T+'/workflow',await get(S(studyId)+'/workflow'));
 const fields=check('GET',T+'/fields',await get(S(studyId)+'/fields?revision='+study.revision));
 const rooms=check('GET',T+'/rooms',await get(S(studyId)+'/rooms?revision='+study.revision));
 for(const d of study.documents.filter(d=>d.kind==='photo')){
  const o=check('GET',T+'/photos/{document_id}/objects',await get(`${S(studyId)}/photos/${d.id}/objects?revision=${study.revision}`));
  const counted={};for(const x of o.items)if(x.counted)counted[x.label]=(counted[x.label]||0)+1;assert.deepEqual(o.counts,counted,`${label}: counts are the counted instances`);
  const img=await get(`${S(studyId)}/documents/${d.id}/image`);assert.equal(img.status,200);assert.equal(img.headers['content-type'],'image/webp');assert.equal(img.body.length,d.bytes,'the photo is served whole');
 }
 const pdf=study.documents.find(d=>d.kind==='appraisal'),bytes=await get(`${S(studyId)}/documents/${pdf.id}/content`);
 assert.equal(bytes.headers['content-type'],'application/pdf');assert.equal(Buffer.from(bytes.body.slice(0,5)).toString(),'%PDF-');
 for(const f of fields.items)for(const r of f.regions){assert.ok(r.page>=1&&r.page<=8);assert.equal(r.bbox.length,4);assert.ok(r.bbox[0]<r.bbox[2]&&r.bbox[1]<r.bbox[3])}
 const assets=check('GET',T+'/assets',await get(S(studyId)+'/assets?revision='+study.revision));
 check('GET',T+'/evidence',await get(S(studyId)+'/evidence'));
 if(assets.items.length){
  check('GET',T+'/evidence',await get(`${S(studyId)}/evidence?asset_id=${assets.items[0].id}`));
  const result=check('GET',T+'/result',await get(S(studyId)+'/result'));
  consistent(result,assets.items,label);
 }
 check('GET',T+'/report/state',await get(S(studyId)+'/report/state'));
 check('GET',T+'/report-answers',await get(S(studyId)+'/report-answers'));
 check('GET',T+'/revisions',await get(S(studyId)+'/revisions'));
 check('GET',T+'/timeline',await get(S(studyId)+'/timeline?limit=100'));
 check('GET',T+'/timeline/steps',await get(S(studyId)+'/timeline/steps'));
 check('GET',T+'/audit',await get(S(studyId)+'/audit'));
 check('GET',T+'/spend',await get(S(studyId)+'/spend'));
 check('GET',T+'/calls',await get(S(studyId)+'/calls?limit=50'));
 check('GET','/v1/cost-indices',await get('/v1/cost-indices?limit=100'));
 check('GET','/v1/cost-indices/runs',await get('/v1/cost-indices/runs'));
 return {study,fields,rooms,assets};
}
const cents=s=>Math.round(Number(s)*100);
function consistent(result,assets,label){
 for(const x of assets)if(x.cost?.total&&x.cost.unit_cost)assert.equal(cents(x.cost.total),Math.round(cents(x.cost.unit_cost)*Number(x.takeoff.quantity)),`${label}: ${x.id} total = quantity × unit cost`);
 const priced=assets.filter(x=>x.cost?.total);
 assert.equal(result.schedule.assets,assets.length);assert.equal(result.schedule.priced,priced.length);
 assert.equal(cents(result.schedule.rcn_total),priced.reduce((s,x)=>s+cents(x.cost.total),0));
 assert.equal(result.schedule.unpriced.length,assets.length-priced.length);
 const al=result.allocation;if(!al)return;
 assert.equal(al.assets.reduce((s,x)=>s+cents(x.allocated),0),cents(al.total),`${label}: rows sum to the total`);
 assert.equal(al.rows_sum,al.total);
 assert.equal(al.by_bucket.reduce((s,b)=>s+cents(b.allocated),0),cents(al.total));
 assert.equal(al.by_bucket.reduce((s,b)=>s+cents(b.building),0),cents(al.building_basis));
 assert.equal(al.by_bucket.reduce((s,b)=>s+cents(b.closing_costs),0),cents(al.closing_costs));
 for(const b of al.by_bucket)assert.equal(cents(b.building)+cents(b.closing_costs),cents(b.allocated));
 assert.equal(cents(result.worksheet.total_basis),cents(al.total));
 // The structure rows hold each component's residual of the building's RCN.
 const s=result.structure;for(const c of s.components)assert.equal(cents(c.top_down),cents(c.itemized)+cents(c.residual)-cents(c.clamped));
 assert.equal(s.components.reduce((t,c)=>t+cents(c.top_down),0),cents(s.building_rcn.amount));
}

for(const state of A.STATES)test(`every GET in the "${state}" state validates against openapi.json`,async()=>{
 const {a}=instance(state);
 for(const st of a.studies())await readAll(a,st.id,`${state}/${st.name}`);
});

test('each state seeds the main study where the contract says',async()=>{
 const want={appraisal:['waiting_review','appraisal_review'],rooms:['waiting_review','rooms_review'],components:['waiting_review','components_review'],assets:['waiting_review','assets_review'],processing:['processing','components_processing'],unsigned:['completed','completed'],signed:['completed','completed'],'update-required':['completed','completed']};
 for(const [state,[status,stage]] of Object.entries(want)){
  const {a,id}=instance(state),s=(await a.call('GET',S(id))).body;assert.deepEqual([s.status,s.current_stage],[status,stage],state);
  const r=(await a.call('GET',S(id)+'/report/state')).body;
  if(state==='unsigned'){assert.equal(r.signature,null);assert.deepEqual(r.missing_for_final,['signature'])}
  if(state==='signed')assert.deepEqual(r.changed_since_signing,[]);
  if(state==='update-required')assert.deepEqual(r.changed_since_signing,['firm']);
 }
 const {a}=instance('components'),list=a.studies();
 assert.equal(list.length,3);assert.deepEqual(list.slice(1).map(s=>s.status),['completed','waiting_review']);
 assert.equal(A.create().state,'components','components is the default');
});

test('the Harbor House fixture: rooms, photos, a low-confidence field, flagged and unpriced assets',async()=>{
 const {a,id}=instance('assets');
 const rooms=(await a.call('GET',S(id)+'/rooms')).body.items;
 assert.deepEqual(rooms.map(r=>[r.name,r.photos.map(p=>p.filename)]),[['Kitchen',['kitchen.webp','kitchen-detail.webp']],['Living room',['living.webp']],['Primary bedroom',['bedroom.webp']],['Exterior & pool',['exterior.webp']]]);
 assert.ok(rooms.every(r=>r.inventory.length&&r.suggested_counts.length));
 const kitchen=rooms[0].suggested_counts.find(s=>s.label==='light_fixture');assert.ok(kitchen.suggested>kitchen.inventory_count,'the photos suggest a third pendant');
 const fields=(await a.call('GET',S(id)+'/fields')).body.items;assert.ok(fields.length>=25);assert.ok(fields.some(f=>f.confidence<0.7),'some fields need a look');
 const assets=(await a.call('GET',S(id)+'/assets')).body.items;
 assert.ok(assets.length>=25&&assets.length<=40);
 assert.deepEqual([...new Set(assets.map(x=>x.bucket))].sort(),['y15','y39','y5','y7']);
 assert.ok(assets.filter(x=>x.needs_review&&x.review_codes.length&&x.review_reasons.length).length>=4);
 assert.ok(assets.filter(x=>!x.cost.total).length>=2);
 const result=(await a.call('GET',S(id)+'/result')).body;assert.equal(result.reconciliation.status,'reconciled');assert.ok(result.structure.building_rcn.formula.includes('3,240 SF'));
});

/* ---------- writes ---------- */
const decision=(s,decision,corrections=[],comment=null)=>({stage:s.current_stage,revision:s.revision,decision,comment,corrections});
const study=async(x)=>(await x.a.call('GET',S(x.id))).body;

test('approve moves to the next gate after the rerun; assets approve completes with an unsigned report',async()=>{
 const x=instance('components');let s=await study(x);
 const r=check('POST','/v1/studies/{study_id}/decisions',await x.post(S(x.id)+'/decisions',decision(s,'approve')),202);
 assert.equal(r.current_stage,'assets_processing');assert.equal(r.revision,s.revision);
 s=await study(x);assert.deepEqual([s.status,s.current_stage],['processing','assets_processing']);
 problem(await x.post(S(x.id)+'/decisions',{...decision(s,'approve'),stage:'components_review'}),409,'stale-state');
 x.advance();s=await study(x);assert.deepEqual([s.status,s.current_stage],['waiting_review','assets_review']);
 assert.ok((await x.a.call('GET',S(x.id)+'/assets')).body.items.length);
 check('POST','/v1/studies/{study_id}/decisions',await x.post(S(x.id)+'/decisions',decision(s,'approve')),202);
 x.advance();s=await study(x);assert.deepEqual([s.status,s.current_stage],['completed','completed']);
 const st=(await x.a.call('GET',S(x.id)+'/report/state')).body;assert.equal(st.exported,true);assert.equal(st.signature,null);
 assert.equal((await x.a.call('GET',S(x.id)+'/workflow')).body.execution_status,'COMPLETED');
});

test('the gate refuses: a processing study (gate-not-open), approve with corrections, a wrong type or target, an unknown study',async()=>{
 const x=instance('processing');let s=await study(x);
 problem(await x.post(S(x.id)+'/decisions',{stage:'components_processing',revision:s.revision,decision:'approve',corrections:[]}),422,'validation');
 const y=instance('components');s=await study(y);
 await y.post(S(y.id)+'/decisions',decision(s,'approve'));
 const z=instance('components');s=await study(z);
 const rooms=(await z.a.call('GET',S(z.id)+'/rooms')).body.items;
 let p=problem(await z.post(S(z.id)+'/decisions',decision(s,'approve',[{entity_type:'component',entity_id:rooms[0].inventory[0].component_id,after:{count:3}}])),422,'validation');
 assert.equal(p.errors[0].type,'approval_with_corrections');assert.equal(p.code,'BASIS-STUDY-016');
 p=problem(await z.post(S(z.id)+'/decisions',decision(s,'reject',[{entity_type:'room_delete',entity_id:rooms[0].id,after:{}}])),422,'validation');
 assert.deepEqual(p.errors[0].loc,['body','corrections',0,'entity_type']);assert.equal(p.errors[0].type,'correction_type');
 p=problem(await z.post(S(z.id)+'/decisions',decision(s,'reject',[{entity_type:'component',entity_id:'nope',after:{count:3}}])),422,'validation');
 assert.equal(p.errors[0].type,'correction_target');
 p=problem(await z.post(S(z.id)+'/decisions',decision(s,'reject',[{entity_type:'component',entity_id:rooms[0].inventory[0].component_id,after:{count:0}}])),422);
 assert.deepEqual(p.errors[0].loc,['body','corrections',0,'after']);
 problem(await z.a.call('GET',S('5a300000-0000-4000-8000-000000000999')),404,'not-found');
 problem(await z.a.call('GET',S(z.id)+'/fields?revision='+(s.revision+1)),404,'not-found');
 // A study waiting on the workflow: gate-not-open.
 await z.post(S(z.id)+'/decisions',decision(s,'reject',[],'Count again.'));
 const after=await study(z);assert.equal(after.status,'processing');
 problem(await z.post(S(z.id)+'/decisions',{stage:'components_review',revision:after.revision,decision:'approve',corrections:[]}),409,'stale-state');
 const w=instance('components');s=await study(w);
 await w.post(S(w.id)+'/decisions',decision(s,'reject',[],'Count again.'));// components_processing, revision + 1
 w.a._inst.studies[0].stage='components_review';// at the stage, still processing: the gate is not open
 const q=problem(await w.post(S(w.id)+'/decisions',{stage:'components_review',revision:s.revision+1,decision:'approve',corrections:[]}),409,'gate-not-open');
 assert.equal(q.code,'BASIS-STUDY-005');
});

test('reject at the components review applies count, label, material, remove and add on revision + 1',async()=>{
 const x=instance('components');let s=await study(x);const rev=s.revision;
 const rooms=(await x.a.call('GET',S(x.id)+'/rooms')).body.items,kitchen=rooms[0],living=rooms[1];
 const light=kitchen.inventory.find(i=>i.label==='light_fixture'),chair=kitchen.inventory.find(i=>i.label==='chair'),panel=living.inventory.find(i=>i.label==='wall_finish');
 problem(await x.post(S(x.id)+'/decisions',decision(s,'reject',[{entity_type:'component_remove',entity_id:chair.component_id,after:{},reason:''}])),422);
 const r=check('POST','/v1/studies/{study_id}/decisions',await x.post(S(x.id)+'/decisions',decision(s,'reject',[
  {entity_type:'component',entity_id:light.component_id,before:{count:2},after:{count:3},reason:'Three pendants over the island.'},
  {entity_type:'component',entity_id:panel.component_id,before:{label:'wall_finish',material:'wood'},after:{label:'wainscoting',material:'composite'}},
  {entity_type:'component_remove',entity_id:chair.component_id,before:null,after:{},reason:'Loose bar stools do not convey.'},
  {entity_type:'room_component',entity_id:kitchen.id,before:null,after:{label:'dishwasher',count:1,material:'metal',attachment:'permanent'}},
  {entity_type:'photo',entity_id:kitchen.photos[0].document_id,before:null,after:{},reason:'Re-analyze this photo.'},
 ],'Pendants and stools.')),202);
 assert.equal(r.corrections_recorded,5);assert.equal(r.revision,rev+1);assert.equal(r.current_stage,'components_processing');
 s=await study(x);assert.equal(s.status,'processing');
 x.advance();s=await study(x);assert.deepEqual([s.status,s.current_stage,s.revision],['waiting_review','components_review',rev+1]);
 const now=(await x.a.call('GET',S(x.id)+'/rooms')).body.items,k=now[0];
 assert.equal(k.inventory.find(i=>i.component_id===light.component_id).count,3);
 assert.equal(k.inventory.find(i=>i.component_id===light.component_id).provenance.origin,'engineer_modified');
 assert.ok(!k.inventory.some(i=>i.label==='chair'));
 const added=k.inventory.find(i=>i.label==='dishwasher');assert.equal(added.added,true);assert.equal(added.provenance.origin,'engineer_added');
 assert.deepEqual(['label','material'].map(f=>now[1].inventory.find(i=>i.component_id===panel.component_id)[f]),['wainscoting','composite']);
 // The decided revision keeps what it had.
 const old=(await x.a.call('GET',S(x.id)+'/rooms?revision='+rev)).body;assert.equal(old.revision,rev);assert.equal(old.items[0].inventory.find(i=>i.label==='light_fixture').count,2);
 const revs=check('GET','/v1/studies/{study_id}/revisions',await x.a.call('GET',S(x.id)+'/revisions'));
 assert.deepEqual(revs.items.at(-1).origin.kind,'rejection');assert.equal(revs.items.at(-2).corrections.component,2);
 assert.ok((await x.a.call('GET',S(x.id)+'/audit')).body.items.some(i=>i.kind==='correction'&&i.action==='removed'));
});

test('reject at the rooms review: rename, move a photo, merge, and the proposed new room',async()=>{
 const x=instance('rooms');let s=await study(x);
 const rooms=(await x.a.call('GET',S(x.id)+'/rooms')).body.items,[kitchen,family,bedroom,exterior]=rooms,detail=kitchen.photos[1].document_id;
 const newRoom='9d3c1f00-0000-4000-8000-00000000abcd';
 problem(await x.post(S(x.id)+'/decisions',decision(s,'reject',[{entity_type:'room',entity_id:family.id,after:{merge_into:bedroom.id,name:'x'}}])),422);
 problem(await x.post(S(x.id)+'/decisions',decision(s,'reject',[{entity_type:'photo_room',entity_id:detail,after:{room_id:'not-a-room'}}])),422);
 check('POST','/v1/studies/{study_id}/decisions',await x.post(S(x.id)+'/decisions',decision(s,'reject',[
  {entity_type:'room',entity_id:family.id,before:{name:'Family room'},after:{name:'Living room',room_type:'living_room'}},
  {entity_type:'room_create',entity_id:newRoom,before:null,after:{name:'Pantry',room_type:'storage',floor:1}},
  {entity_type:'photo_room',entity_id:detail,before:{room_id:kitchen.id},after:{room_id:newRoom}},
  {entity_type:'room',entity_id:exterior.id,before:null,after:{merge_into:bedroom.id}},
 ])),202);
 x.advance();s=await study(x);assert.deepEqual([s.status,s.current_stage],['waiting_review','rooms_review']);
 const now=(await x.a.call('GET',S(x.id)+'/rooms')).body.items;
 assert.deepEqual(now.map(r=>r.name),['Kitchen','Living room','Primary bedroom','Pantry']);
 const pantry=now.find(r=>r.id===newRoom);assert.deepEqual(pantry.photos.map(p=>p.filename),['kitchen-detail.webp']);assert.equal(pantry.floor,'1');assert.equal(pantry.provenance.origin,'engineer_added');
 assert.ok(pantry.inventory.some(i=>i.label==='range_hood'),'components seen only in the moved photo move with it');
 assert.deepEqual(now.find(r=>r.id===bedroom.id).photos.map(p=>p.filename),['bedroom.webp','exterior.webp']);
 assert.ok(!now.some(r=>r.id===exterior.id),'the merged room is dropped');
});

test('reject at the appraisal review corrects a field on revision + 1',async()=>{
 const x=instance('appraisal');let s=await study(x);
 const f=(await x.a.call('GET',S(x.id)+'/fields')).body.items.find(f=>f.key==='improvements.stories');
 problem(await x.post(S(x.id)+'/decisions',decision(s,'reject',[{entity_type:'extraction_field',entity_id:f.id,after:{value_num:'two'}}])),422);
 await x.post(S(x.id)+'/decisions',decision(s,'reject',[{entity_type:'extraction_field',entity_id:f.id,before:{value_num:'2'},after:{value_num:'3'},reason:'Three levels with the basement.'}]));
 assert.equal((await x.a.call('GET',S(x.id)+'/fields')).body.items.length,0,'nothing while it reruns');
 x.advance();
 const g=check('GET','/v1/studies/{study_id}/fields',await x.a.call('GET',S(x.id)+'/fields')).items.find(x=>x.id===f.id);
 assert.equal(g.value_num,'3');assert.equal(g.provenance.origin,'engineer_modified');assert.equal(g.source,'manual');
});

test('asset corrections: classification and a measured quantity price the fence; totals stay consistent',async()=>{
 const x=instance('assets');let s=await study(x);
 const assets=(await x.a.call('GET',S(x.id)+'/assets')).body.items,fence=assets.find(a=>a.label==='fence'),panel=assets.find(a=>a.label==='wall_finish');
 assert.equal(fence.cost.total,null);
 problem(await x.post(S(x.id)+'/decisions',decision(s,'reject',[{entity_type:'asset',entity_id:fence.id,after:{quantity:60}}])),422);
 await x.post(S(x.id)+'/decisions',decision(s,'reject',[
  {entity_type:'asset',entity_id:fence.id,after:{quantity:62,quantity_unit:'LF',quantity_source:'Site plan, sheet 2'}},
  {entity_type:'asset',entity_id:panel.id,after:{bucket:'y39',irs_section:'1250',category:'Interior wall finish'}},
 ]));
 x.advance();
 const now=check('GET','/v1/studies/{study_id}/assets',await x.a.call('GET',S(x.id)+'/assets')).items;
 const f=now.find(a=>a.id===fence.id),p=now.find(a=>a.id===panel.id);
 assert.deepEqual([f.takeoff.quantity,f.takeoff.unit],['62','LF']);assert.ok(Number(f.cost.total)>0);assert.equal(f.needs_review,false);
 assert.deepEqual([p.bucket,p.recovery_period,p.irs_section,p.category,p.provenance.origin],['y39','39','1250','Interior wall finish','engineer_modified']);
 consistent(check('GET','/v1/studies/{study_id}/result',await x.a.call('GET',S(x.id)+'/result')),now,'after corrections');
});

test('objects: a duplicate box answers 200 created false; a new box 201; delete only what the engineer added',async()=>{
 const x=instance('components'),s=await study(x),doc=s.documents.find(d=>d.filename==='kitchen.webp').id,P=`${S(x.id)}/photos/${doc}/objects`,T='/v1/studies/{study_id}/photos/{document_id}/objects';
 const before=(await x.a.call('GET',P)).body,pendant=before.items.find(o=>o.label==='light_fixture');
 const dup=await x.post(P,{kind:'box',label:'light_fixture',box:{x1:pendant.box.x1+0.002,y1:pendant.box.y1,x2:pendant.box.x2,y2:pendant.box.y2+0.002}});
 assert.equal(dup.status,200);assert.deepEqual(validate(responseSchema('POST',T,201),dup.body),[]);
 assert.equal(dup.body.created,false);assert.equal(dup.body.duplicate_of,pendant.id);assert.ok(dup.body.overlap>=0.5);
 const added=check('POST',T,await x.post(P,{kind:'box',label:'light_fixture',box:{x1:.6,y1:.05,x2:.66,y2:.1}}),201);
 assert.equal(added.created,true);assert.equal(added.object.source,'manual');assert.equal(added.object.mask_source,'sam_box_prompt','a drawn box comes back outlined, as SAM 3 outlines it');assert.ok(added.object.mask&&added.object.mask.counts.reduce((a,b)=>a+b,0)===added.object.mask.width*added.object.mask.height,'the outline is a whole-photo MaskOut');
 const anyway=await x.post(P,{kind:'box',label:'light_fixture',box:{x1:pendant.box.x1,y1:pendant.box.y1,x2:pendant.box.x2,y2:pendant.box.y2},allow_duplicate:true});assert.equal(anyway.status,201);
 const cand=before.items.find(o=>!o.counted),fromCandidate=check('POST',T,await x.post(P,{kind:'candidate',candidate_id:cand.id}),201);
 assert.equal(fromCandidate.object.mask_source,'candidate');assert.equal(fromCandidate.object.derived_from,cand.id);
 problem(await x.post(P,{kind:'point',label:'sink',point:{x:.99,y:.01}}),422,'nothing-at-point');
 problem(await x.post(P,{kind:'box',label:'not_a_label',box:{x1:0,y1:0,x2:.1,y2:.1}}),422,'validation');
 let after=check('GET',T,await x.a.call('GET',P));assert.equal(after.counts.light_fixture,5);
 const rooms=(await x.a.call('GET',S(x.id)+'/rooms')).body.items,sug=rooms[0].suggested_counts.find(s=>s.label==='light_fixture');assert.ok(sug.manual_count>=1);
 // openapi SuggestedCountOut: photo_count includes the manual instances; suggested = max(inventory_count, photo_count).
 assert.equal(sug.photo_count,5);assert.equal(sug.suggested,Math.max(sug.inventory_count,sug.photo_count));
 check('DELETE',T+'/{object_id}',await x.a.call('DELETE',`${P}/${added.object.id}`));
 problem(await x.a.call('DELETE',`${P}/${pendant.id}`),409,'not-manual');
 problem(await x.a.call('DELETE',`${P}/nope`),404,'not-found');
 after=(await x.a.call('GET',P)).body;assert.ok(!after.items.some(o=>o.id===added.object.id));
 const y=instance('assets'),ys=await study(y),ydoc=ys.documents.find(d=>d.kind==='photo').id;
 problem(await y.post(`${S(y.id)}/photos/${ydoc}/objects`,{kind:'box',label:'window',box:{x1:0,y1:0,x2:.1,y2:.1}}),409,'gate-not-open');
});

test('reopen: the dry run is the plan; reopening opens the gate on a new revision; a study not completed is refused',async()=>{
 const x=instance('signed'),s=await study(x);
 const plan=check('POST','/v1/studies/{study_id}/reopen',await x.post(S(x.id)+'/reopen',{stage:'components_review',dry_run:true}),200);
 assert.deepEqual(plan.recomputed,['assets_processing','exporting']);assert.deepEqual(plan.reviews,['components_review','assets_review']);
 const r=check('POST','/v1/studies/{study_id}/reopen',await x.post(S(x.id)+'/reopen',{stage:'components_review'}),202);
 assert.equal(r.revision,s.revision+1);
 x.advance();const after=await study(x);assert.deepEqual([after.status,after.current_stage,after.revision],['waiting_review','components_review',s.revision+1]);
 assert.equal((await x.a.call('GET',S(x.id)+'/assets')).body.items.length,0,'assets recompute after the components review');
 assert.ok((await x.a.call('GET',S(x.id)+'/report/state')).body.signature===null,'the new revision is signed on its own');
 assert.ok((await x.a.call('GET',S(x.id)+'/revisions')).body.items.find(i=>i.revision===s.revision).signature,'the signed revision keeps its signature');
 problem(await x.post(S(x.id)+'/reopen',{stage:'assets_review'}),409,'study-not-completed');
 problem(await x.post(S(x.id)+'/reopen',{stage:'uploading'}),422,'validation');
});

test('report: preview ETag, sign, then a firm profile change marks the signature (update required)',async()=>{
 const x=instance('unsigned'),T='/v1/studies/{study_id}/report';
 const preview=await x.a.call('GET',S(x.id)+'/report/preview');assert.equal(preview.status,200);assert.equal(preview.headers['content-type'],'application/pdf');
 const tag=preview.headers.etag.replace(/"/g,'');assert.match(tag,/^[0-9a-f]{64}$/);
 const engineer={name:'Alex Morgan',credentials:'PE',license_state:'CA'},body={revision:(await study(x)).revision,reviewed_sha256:tag,engineer,attest:true,statement_version:1};
 // A stale preview: 412 BASIS-REPORT-010 with the current preview's ETag, as the real route answers.
 const stale=await x.post(S(x.id)+'/report/sign',{...body,reviewed_sha256:'0'.repeat(64)});problem(stale,412);assert.equal(stale.body.code,'BASIS-REPORT-010');assert.equal(stale.headers.etag,`"${tag}"`);
 problem(await x.post(S(x.id)+'/report/sign',{...body,attest:false}),422);
 const sig=check('POST',T+'/sign',await x.post(S(x.id)+'/report/sign',body),201);assert.equal(sig.reviewed_sha256,tag);
 problem(await x.post(S(x.id)+'/report/sign',body),409);
 let st=check('GET',T+'/state',await x.a.call('GET',S(x.id)+'/report/state'));assert.deepEqual(st.changed_since_signing,[]);
 const pdf=await x.a.call('GET',S(x.id)+'/report?format=pdf');assert.equal(Buffer.from(pdf.body.slice(0,5)).toString(),'%PDF-');assert.match(pdf.headers['content-disposition'],/signed/);
 const csv=await x.a.call('GET',S(x.id)+'/report?format=csv');assert.match(Buffer.from(csv.body).toString(),/^asset_id,category,bucket/);
 const org=(await x.a.call('GET','/v1/me')).body.memberships[0].org_id,prof=(await x.a.call('GET',`/v1/orgs/${org}/profile`)).body;
 problem(await x.a.call('PUT',`/v1/orgs/${org}/profile`,{body:{...prof.profile,phone:'(555) 010-0100'},headers:{'If-Match':'"99"'}}),412);
 problem(await x.a.call('PUT',`/v1/orgs/${org}/profile`,{body:{...prof.profile,unknown:1},headers:{'If-Match':`"${prof.version}"`}}),422);
 check('PUT','/v1/orgs/{org_id}/profile',await x.a.call('PUT',`/v1/orgs/${org}/profile`,{body:{...prof.profile,phone:'(555) 010-0100'},headers:{'If-Match':`"${prof.version}"`}}));
 st=(await x.a.call('GET',S(x.id)+'/report/state')).body;assert.deepEqual(st.changed_since_signing,['firm']);
 const ans=(await x.a.call('GET',S(x.id)+'/report-answers')).body;
 check('PUT','/v1/studies/{study_id}/report-answers',await x.a.call('PUT',S(x.id)+'/report-answers',{body:{...ans.report_answers,taxpayer_name:'Harbor House Trust (sample)'},headers:{'If-Match':`"${ans.report_answers_version}"`}}));
 st=(await x.a.call('GET',S(x.id)+'/report/state')).body;assert.deepEqual(st.changed_since_signing,['firm','study']);
});

test('an Idempotency-Key replays the first answer instead of acting twice',async()=>{
 const x=instance('components'),s=await study(x),doc=s.documents.find(d=>d.kind==='photo').id,P=`${S(x.id)}/photos/${doc}/objects`,body={kind:'box',label:'window',box:{x1:.9,y1:.9,x2:.95,y2:.95}};
 const one=await x.post(P,body,{'Idempotency-Key':'k-1'}),two=await x.post(P,body,{'Idempotency-Key':'k-1'});
 assert.equal(one.status,201);assert.deepEqual(two.body,one.body);
 assert.equal((await x.a.call('GET',P)).body.items.filter(o=>o.source==='manual').length,1);
});

test('the Response surface: JSON, problem+json, bytes, and the bearer token',async()=>{
 const {a,id}=instance('components');
 let r=await a.fetch(`${A.BASE}/v1/studies/${id}`,{headers:{Authorization:'Bearer t'}});assert.equal(r.status,200);assert.match(r.headers.get('content-type'),/json/);assert.equal((await r.json()).id,id);
 r=await a.fetch(`${A.BASE}/v1/nothing`,{headers:{Authorization:'Bearer t'}});assert.equal(r.status,404);assert.equal(r.headers.get('content-type'),'application/problem+json');
 r=await a.fetch(`${A.BASE}/v1/me`,{});assert.equal(r.status,401);
 const doc=(await a.call('GET',S(id))).body.documents.find(d=>d.kind==='photo');
 r=await a.fetch(`${A.BASE}/v1/studies/${id}/documents/${doc.id}/image`,{headers:{Authorization:'Bearer t'}});assert.equal((await r.blob()).size,doc.bytes);
 // The live client works against it unchanged.
 const client=require('../live/api-client.js').createClient({baseUrl:a.base,fetchImpl:a.fetch,getToken:async()=>'sample-token'});
 assert.equal((await client.listStudies()).items.length,3);
 await assert.rejects(client.decide(id,{stage:'appraisal_review',revision:1,decision:'approve',corrections:[]}),e=>e.status===409&&e.kind==='stale-state');
});

/* ---------- PROPOSED (the sample API's own section) ---------- */
test('P0: capabilities say what the proposed section offers',async()=>{
 const {a}=instance('components');
 assert.deepEqual((await a.call('GET','/v1/capabilities')).body,{object_exclusion:true,unit_cost_override:true,room_create:true});
});

test('P1: exclude an instance with a reason, then restore it; the counts follow',async()=>{
 const x=instance('components'),s=await study(x),doc=s.documents.find(d=>d.filename==='kitchen.webp').id,P=`${S(x.id)}/photos/${doc}/objects`;
 const list=(await x.a.call('GET',P)).body,chair=list.items.find(o=>o.label==='chair');
 assert.ok(list.items.every(o=>o.exclusion===null));
 problem(await x.a.call('PATCH',`${P}/${chair.id}`,{body:{counted:false,reason:'  '}}),422);
 problem(await x.a.call('PATCH',`${P}/nope`,{body:{counted:false,reason:'x'}}),404);
 const out=(await x.a.call('PATCH',`${P}/${chair.id}`,{body:{counted:false,reason:'Loose furniture.'}})).body;
 assert.deepEqual(validate({$ref:'#/components/schemas/PhotoObjectOut'},out),[]);
 assert.equal(out.counted,false);assert.deepEqual([out.exclusion.by,out.exclusion.by_name,out.exclusion.reason],['sample-user-1','Alex Morgan','Loose furniture.']);
 let now=(await x.a.call('GET',P)).body;assert.equal(now.counts.chair,list.counts.chair-1);assert.ok(now.items.some(o=>o.id===chair.id),'the AI box stays as evidence');
 const back=(await x.a.call('PATCH',`${P}/${chair.id}`,{body:{counted:true}})).body;assert.equal(back.counted,true);assert.equal(back.exclusion,null);
 now=(await x.a.call('GET',P)).body;assert.equal(now.counts.chair,list.counts.chair);
 const y=instance('assets'),ys=await study(y),yd=ys.documents.find(d=>d.filename==='kitchen.webp').id,yo=(await y.a.call('GET',`${S(y.id)}/photos/${yd}/objects`)).body.items[0];
 problem(await y.a.call('PATCH',`${S(y.id)}/photos/${yd}/objects/${yo.id}`,{body:{counted:false,reason:'x'}}),409,'gate-not-open');
});

test('P2: a unit price override on an asset, and on a component for every asset taken off it',async()=>{
 const x=instance('assets');let s=await study(x);
 const chair=(await x.a.call('GET',S(x.id)+'/assets')).body.items.find(a=>a.label==='chair');
 problem(await x.post(S(x.id)+'/decisions',decision(s,'reject',[{entity_type:'asset',entity_id:chair.id,after:{unit_cost:310,unit_cost_source:'Vendor quote'}}])),422);
 problem(await x.post(S(x.id)+'/decisions',decision(s,'reject',[{entity_type:'asset',entity_id:chair.id,after:{unit_cost:0,unit_cost_source:'Vendor quote'},reason:'Quote'}])),422);
 await x.post(S(x.id)+'/decisions',decision(s,'reject',[{entity_type:'asset',entity_id:chair.id,after:{unit_cost:310,unit_cost_source:'Vendor quote 2026-10-02'},reason:'Priced from the seller’s invoice.'}]));
 x.advance();
 let a=check('GET','/v1/studies/{study_id}/assets',await x.a.call('GET',S(x.id)+'/assets')).items.find(a=>a.id===chair.id);
 assert.deepEqual([a.cost.unit_cost,a.cost.total],['310.00','1240.00']);
 assert.deepEqual([a.cost.override.unit_cost,a.cost.override.source,a.cost.override.by],[310,'Vendor quote 2026-10-02','sample-user-1']);
 assert.ok((await x.a.call('GET',S(x.id)+'/assets')).body.items.filter(a=>a.cost.override).length===1,'override only where set');
 const y=instance('components');s=await study(y);
 const stove=(await y.a.call('GET',S(y.id)+'/rooms')).body.items[0].inventory.find(i=>i.label==='stove');
 await y.post(S(y.id)+'/decisions',decision(s,'reject',[{entity_type:'component',entity_id:stove.component_id,after:{unit_cost:7200,unit_cost_source:'Dealer price list'},reason:'Model in the photo.'}]));
 y.advance();s=await study(y);await y.post(S(y.id)+'/decisions',decision(s,'approve'));y.advance();
 a=(await y.a.call('GET',S(y.id)+'/assets')).body.items.find(a=>a.component_id===stove.component_id);
 assert.deepEqual([a.cost.unit_cost,a.cost.override.source],['7200.00','Dealer price list']);
});

test('P3: room_create only at the rooms review; never room_delete',async()=>{
 const x=instance('components'),s=await study(x);
 const p=problem(await x.post(S(x.id)+'/decisions',decision(s,'reject',[{entity_type:'room_create',entity_id:'9d3c1f00-0000-4000-8000-000000000001',before:null,after:{name:'Pantry',room_type:'storage',floor:null}}])),422);
 assert.equal(p.errors[0].type,'correction_type');
 const y=instance('rooms'),ys=await study(y);
 problem(await y.post(S(y.id)+'/decisions',decision(ys,'reject',[{entity_type:'room_create',entity_id:'9d3c1f00-0000-4000-8000-000000000001',before:null,after:{name:'',room_type:'storage'}}])),422);
 problem(await y.post(S(y.id)+'/decisions',decision(ys,'reject',[{entity_type:'room_delete',entity_id:(await y.a.call('GET',S(y.id)+'/rooms')).body.items[0].id,after:{}}])),422);
 // A new room no photo moves into is dropped, as every empty room is.
 await y.post(S(y.id)+'/decisions',decision(ys,'reject',[{entity_type:'room_create',entity_id:'9d3c1f00-0000-4000-8000-000000000002',before:null,after:{name:'Empty',room_type:'other',floor:null}}]));
 y.advance();assert.equal((await y.a.call('GET',S(y.id)+'/rooms')).body.items.length,4);
});

/* ---------- loading in a page ---------- */
const load=search=>{const ctx={location:{search,href:'http://127.0.0.1/index.html'+search},URLSearchParams,URL,Response,Headers,TextEncoder,console,BasisAuth:{real:true}};ctx.globalThis=ctx;vm.runInNewContext(fs.readFileSync(path.join(WEB,'live','sample-api.js'),'utf8'),ctx);return ctx};
test('in a page: inactive without ?api=sample; with it, active with a signed-in stub of BasisAuth',async()=>{
 let ctx=load('?mode=live');assert.deepEqual({...ctx.BasisSampleApi},{active:false});assert.equal(ctx.BasisAuth.real,true);
 ctx=load('?mode=live&api=sample&sample=signed');
 assert.equal(ctx.BasisSampleApi.active,true);assert.equal(ctx.BasisSampleApi.base,A.BASE);assert.equal(ctx.BasisSampleApi.state,'signed');
 const authSource=fs.readFileSync(path.join(WEB,'live','auth.js'),'utf8'),surface=[...authSource.match(/scope\.BasisAuth=\{.*\};/)[0].matchAll(/[{,](?:get )?([A-Za-z]+)/g)].map(m=>m[1]).filter(n=>n!=="return");
 assert.deepEqual(Object.keys(ctx.BasisAuth).sort(),surface.sort());
 await ctx.BasisAuth.init({});assert.equal(ctx.BasisAuth.user.uid,'sample-user-1');assert.equal(ctx.BasisAuth.user.email,'alex.morgan@example.test');assert.equal(await ctx.BasisAuth.getToken(),'sample-token');
 let seen;ctx.BasisAuth.onChange(u=>seen=u);await ctx.BasisAuth.signOut();assert.equal(seen,null);assert.equal(await ctx.BasisAuth.getToken(),null);
 await ctx.BasisAuth.signInWithGoogle();assert.equal(seen.displayName,'Alex Morgan');
 ctx.BasisSampleApi.reset('appraisal');const st=(await ctx.BasisSampleApi.call('GET','/v1/studies/'+ctx.BasisSampleApi.mainStudyId())).body;assert.equal(st.current_stage,'appraisal_review');
});

/* ---------- no client data ---------- */
// Digests (sha256 of the lowercased text, with its length) of distinctive strings from a real
// study's recorded answers: property names, addresses, the firm (18 characters or more, so a
// digest cannot be guessed back). The strings stay out of this file.
const FORBIDDEN=[["1aedd99571fca4a1599d05e945cef988a93d17477f56572a04be19996a894467", 73], ["4424e2e8ffe2469c219cbd21aaea211cb2b87ff03ea87f14d2fff92618c8a083", 22], ["4a5e8a51c818745a4fd074570af7ddcabc4402d74599f12dfc006b32458c69f7", 53], ["5d5b3e3019447dad9f128c511bd981b0c77d1480b8e6a7e0bc2aeb522c587fae", 25], ["8f1cefdd9863d1760b4c87ac53ed87ed589f811ffa488aa7451685fc7bef4e91", 18], ["a4403c6b9ec6e54edf9fe7d29c286d5150d046f93e33ff15e1aaf90b3e1d3a2a", 18], ["a9ded9a10b6f5f59d4a01c58463986e7ad536970e1f11807602e1c9cb66cf5c1", 69], ["afe8000c82cd4bf853af04f697ff1518a7631f6498e4be3108099da4bebde799", 27], ["bca85de6eaf29da11f7c4db936f1abea41849e55133232bcd939de86cdde612e", 63], ["dacb7afc956e06893f9956525add375ea96e73a5b579de0c233bc7578a3fe1b1", 65], ["e9c60f772e432d62aa4abbefe7189691bf3d04494c4a9dcbede6fce8d28a924e", 64], ["fe5d48d560dc5650d0683c14ad3b9427313833e8075ef1d3463b6eef21715f17", 57]];
test('the fixture contains none of the distinctive strings of the recorded real study',()=>{
 const targets=FORBIDDEN;assert.ok(targets.length>=10);
 for(const file of ['live/sample-api.js','tests/sample-api.test.cjs']){
  const text=fs.readFileSync(path.join(WEB,file),'utf8').toLowerCase(),byLength=new Map();
  for(const [hash,len] of targets){if(!byLength.has(len))byLength.set(len,new Set());byLength.get(len).add(hash)}
  for(const [len,hashes] of byLength)for(let i=0;i+len<=text.length;i++){const h=crypto.createHash('sha256').update(text.slice(i,i+len)).digest('hex');assert.ok(!hashes.has(h),`${file} holds a recorded client string at offset ${i}`)}
 }
});

test('the validator itself refuses what openapi.json refuses',()=>{
 const ref=n=>({$ref:'#/components/schemas/'+n});
 const box={x1:0,y1:0,x2:1,y2:1},obj={id:'o',document_id:'d',revision:1,label:'sink',phrase:null,score:null,box,mask:null,area:1,source:'model',counted:true,derived_from:null,created_by:null,created_at:'t'};
 assert.deepEqual(validate(ref('PhotoObjectOut'),obj),[]);
 assert.deepEqual(validate(ref('PhotoObjectOut'),{...obj,exclusion:null}),[],'the proposed field where the contract puts it');
 assert.match(validate(ref('BoxOut'),{...box,exclusion:null}).join(),/exclusion is not in BoxOut/,'an extra anywhere else');
 assert.match(validate(ref('PhotoObjectOut'),{...obj,source:'ai'}).join(),/not in enum/);
 assert.match(validate(ref('PhotoObjectOut'),{...obj,revision:'1'}).join(),/expected integer/);
 const {label,...missing}=obj;assert.match(validate(ref('PhotoObjectOut'),missing).join(),/missing required label/);
 assert.match(validate(ref('StudyList'),{items:[],total:-1,next_cursor:null}).join(),/below 0/);
 assert.match(validate(ref('SignIn'),{revision:1,reviewed_sha256:'x',engineer:{name:'A',credentials:'PE'},attest:true,statement_version:1}).join(),/does not match/);
 assert.match(validate(ref('DecisionIn'),{stage:'appraisal_review',revision:1,decision:'approve',extra:1}).join(),/additionalProperties false/);
 assert.match(validate(ref('CostOut'),{unit_cost:null,total:null,source_row:null,location_factor:null,cost_year:null,override:{}}).join(),/^$/,'override on CostOut');
});
test('deleting the PROPOSED section, as its header says, leaves an API that answers like the real one',async()=>{
 const src=fs.readFileSync(path.join(__dirname,'../live/sample-api.js'),'utf8');
 const start=src.indexOf(' /* ===',src.indexOf('PROPOSED: not in the real API')-400),end=src.indexOf('/* END PROPOSED');
 assert(start>0&&end>start,'the section markers are where the header says');
 const head=src.lastIndexOf('\n',src.indexOf('PROPOSED: not in the real API'));
 const trimmed=src.slice(0,src.lastIndexOf('/*',head))+src.slice(src.indexOf('\n',end)+1);
 assert.doesNotMatch(trimmed,/PROPOSED_ROUTES\.push|object_exclusion:true/);
 const m={exports:{}};new Function('module','exports','require',trimmed)(m,m.exports,require);
 const a=m.exports.create({state:'components',fetchAsset});
 assert.equal((await a.call('GET','/v1/capabilities')).status,404,'no capabilities: live mode shows only what the API has');
 assert.equal((await a.call('GET','/health')).status,200);
});

test('If-Match on the report answers reads as the real API does: * needs existing answers, only strong quoted tags match',async()=>{
 const x=instance('unsigned'),P=S(x.id)+'/report-answers';
 const ans=(await x.a.call('GET',P)).body,v=ans.report_answers_version,put=h=>x.a.call('PUT',P,{body:{...ans.report_answers,taxpayer_name:'Harbor House Trust (sample)'},headers:h});
 assert.ok(ans.report_answers,'the unsigned sample has answers');
 const unquoted=await put({'If-Match':String(v)});problem(unquoted,412);assert.equal(unquoted.headers.etag,`"${v}"`);
 problem(await put({'If-Match':`W/"${v}"`}),412);// weak tags never match
 assert.equal((await put({'If-Match':`"98", "${v}"`})).status,200,'a list holding the current tag');
 const now=(await x.a.call('GET',P)).body.report_answers_version;
 assert.equal((await put({'If-Match':'*'})).status,200,'* holds: the answers exist');
 assert.equal((await put({})).status,200,'no condition');
 assert.ok((await x.a.call('GET',P)).body.report_answers_version>now);
 // Absent answers: * does not hold.
 const y=instance('components'),Q=S(y.id)+'/report-answers',none=(await y.a.call('GET',Q)).body;
 if(!none.report_answers){problem(await y.a.call('PUT',Q,{body:{taxpayer_name:'T',photographs:'taxpayer_supplied',photographed_by:'T',photographed_on:'2026-10-01'},headers:{'If-Match':'*'}}),412)}
});
