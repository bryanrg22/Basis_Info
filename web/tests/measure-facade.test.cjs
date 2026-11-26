// The facade on a photo (EVAL_REPORT 111): web/live/property-model.js's port of the server's
// single-view metrology (basis/geo/facade.py) and of traced.py's space "facade", checked against
// what the server computed on synthetic photographed walls (tests/fixtures/facade-reference.json,
// written by backEnd/tests/unit/geo/test_facade_reference.py, which also fails when the server
// drifts from the fixture): every quantity to 1e-9, the expected errors, the refusals and their
// words, the records, the sentence and the problems of bad bodies.
const {test}=require('node:test');const assert=require('node:assert/strict');
const M=require('../live/property-model.js');
const REF=require('./fixtures/facade-reference.json');

const close=(a,b,path='')=>{
 if(typeof a==='number'||typeof b==='number'){if(a===null||b===null||Math.abs(a-b)>1e-9*Math.max(1,Math.abs(b)))return [`${path}: ${a} != ${b}`];return []}
 if(Array.isArray(a)&&Array.isArray(b))return a.length!==b.length?[`${path}: ${a.length} != ${b.length}`]:a.flatMap((x,i)=>close(x,b[i],`${path}[${i}]`));
 if(a&&b&&typeof a==='object'&&typeof b==='object'){const ka=Object.keys(a).sort(),kb=Object.keys(b).sort();if(ka.join()!==kb.join())return [`${path}: keys ${ka} != ${kb}`];return ka.flatMap(k=>close(a[k],b[k],`${path}.${k}`))}
 return a===b?[]:[`${path}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`];
};
const pick=(f,keys)=>Object.fromEntries(keys.map(k=>[k,f[k]===undefined?null:f[k]]));
const KEYS=['code','refusals','height_m','gross_m2','openings_m2','net_m2','trim_m','windows','doors','focal_px','focal_from','view_deg','skew_deg','wall_px','expected_error_pct','left_out'];

test('the metrology gives the server’s numbers on every reference wall',()=>{
 assert.ok(REF.metrology.length>=26);
 for(const [i,c] of REF.metrology.entries()){
  const got=M.facadeMeasure(c.camera,c.wall,c.length_m,c.openings.map(o=>({kind:o.kind,quad:o.corners})));
  const mine=pick(got,KEYS),theirs=pick(c.facade,KEYS);
  if(!theirs.code&&theirs.height_m==null){continue}
  mine.openings=(got.openings||[]).map(o=>pick(o,['kind','width_m','height_m','area_m2','perimeter_m']));theirs.openings=c.facade.openings;
  // A refused facade has no openings or errors on either side.
  assert.deepEqual(close(mine,theirs,`case ${i}`),[]);
 }
});

test('every outcome is covered: measured three ways and each refusal',()=>{
 const codes=new Set(REF.metrology.map(c=>c.facade.code)),froms=new Set(REF.metrology.map(c=>c.facade.focal_from));
 assert.deepEqual([...codes].sort(),[null,'BASIS-GEO-023','BASIS-GEO-024','BASIS-GEO-025'].sort());
 for(const f of ['field of view','vanishing points','square-on'])assert.ok(froms.has(f),f);
 const refused=REF.metrology.filter(c=>c.facade.code).map(c=>M.facadeMeasure(c.camera,c.wall,c.length_m,c.openings.map(o=>({kind:o.kind,quad:o.corners}))).refusals[0][2]);
 assert.ok(refused.some(t=>/px wide in the photo, under 120/.test(t))&&refused.some(t=>/degrees off square, over 70/.test(t))&&refused.some(t=>/vanishing points/.test(t)));
});

test('a correction body measures to the server’s record and sentence',()=>{
 for(const c of REF.bodies.filter(x=>x.record)){
  const r=M.measure(c.body),want=c.record;
  for(const k of ['quantity','unit','area_sf','method','expected_error_pct','shape_count','corner_count','cutout_count'])assert.equal(r[k],want[k],k);
  assert.deepEqual(close(r.facade_measured,want.facade_measured),[]);
  if(want.original)assert.equal(r.original.quantity,want.original.quantity);
  assert.equal(M.formula(r,{source:c.body.image.source,captured_on:c.body.image.captured_on,drawn_by:c.body.drawn_by,verified:c.body.verified,facade:c.body.facade}),c.formula);
  // The record read back is the body again (the computed keys stripped).
  assert.deepEqual(M.bodyOfRecord({...c.body,...r}).facade,c.body.facade);
 }
});

test('a bad body is refused in the server’s words',()=>{
 for(const c of REF.bodies.filter(x=>x.problems)){
  const mine=M.problems(c.body);
  assert.ok(mine.length>0,JSON.stringify(c.problems));
  assert.equal(mine[0],c.problems[0]);
 }
});

test('clicked corners are put in order and the lens is on the diagonal',()=>{
 assert.deepEqual(M.orderQuad([[700,100],[100,600],[100,100],[700,600]]),[[100,600],[700,600],[700,100],[100,100]]);
 assert.deepEqual(M.orderQuad([[1,2]]),[[1,2]]);
 assert.ok(Math.abs(M.hfovFrom35(28,4032,3024)-63.44)<0.01);
 assert.throws(()=>M.hfovFrom35(0,4,3));
 assert.equal(M.caps({site_drafts:true,facade_measure:true}).siteDrafts,true);
 assert.equal(M.caps({}).facadeMeasure,false);
 assert.match(M.FACADE_TEXT['BASIS-GEO-024'],/Type the lens/);
 for(const t of Object.values(M.FACADE_TEXT))assert.doesNotMatch(t,/SAM|Replicate|OpenAI|Anthropic/);
});
