// The sample API's property routes (web/live/sample-property.js, registered from the PROPOSED
// section of web/live/sample-api.js) and the traced measurement on an asset correction (P9): the
// contracts of docs/api/PROPERTY_CAPTURE.md, PUBLIC_GEO.md, RENOVATIONS.md and OVERRIDES.md 4 on
// their open branches, as the Property area (web/live/property.js) uses them.
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const A=require('../live/sample-api.js'),PM=require('../live/property-model.js'),PS=require('../live/sample-property.js');
const WEB=path.join(__dirname,'..'),fetchAsset=p=>fs.readFileSync(path.join(WEB,p));
function instance(state='assets',variant){
 const t={now:Date.parse('2026-11-23T20:00:00Z')},a=A.create({state,now:()=>t.now,fetchAsset,delayMs:2000});
 if(variant)a._inst.propertyState=variant;
 const id=a.mainStudyId(),P=`/v1/studies/${id}`;
 return {a,t,id,P,get:p=>a.call('GET',P+p),put:(p,body,h={})=>a.call('PUT',P+p,{body,headers:h}),post:(p,body)=>a.call('POST',P+p,{body}),advance:ms=>{t.now+=ms}};
}

test('the pin: the search placed it, If-Match guards it, a stale tag is a 412 with the current tag',async()=>{
 const x=instance(),r=await x.get('/property');
 assert.equal(r.status,200);assert.equal(r.headers.etag,'"1"');
 assert.deepEqual([r.body.pin.source,r.body.pin.confirmed,r.body.enabled],['search',false,true]);
 assert.equal(r.body.capture.status,'succeeded');assert.equal(r.body.capture.views.length,12,'close, wide, four sides, six dated views');
 const stale=await x.put('/property/pin',{address:r.body.pin.address,lat:r.body.pin.lat,lon:r.body.pin.lon,confirmed:true},{'If-Match':'"0"'});
 assert.equal(stale.status,412);assert.equal(stale.body.code,'BASIS-CAPTURE-016');assert.equal(stale.headers.etag,'"1"');
 const ok=await x.put('/property/pin',{address:r.body.pin.address,lat:r.body.pin.lat,lon:r.body.pin.lon,confirmed:true},{'If-Match':'"1"'});
 assert.equal(ok.status,200);assert.equal(ok.body.version,2);assert.equal(ok.body.source,'search','the point did not move');
 const again=await x.put('/property/pin',{address:r.body.pin.address,lat:r.body.pin.lat,lon:r.body.pin.lon,confirmed:true},{'If-Match':'"2"'});
 assert.equal(again.body.version,2,'an unchanged document writes nothing');
 // As the server does: a view is stale once the pin's version moved, even when only the confirmation changed.
 const after=(await x.get('/property')).body;assert.ok(after.capture.views.every(v=>v.stale));
 assert.ok(after.capture.views.filter(v=>v.kind!=='street').every(v=>v.lat===after.pin.lat&&v.lon===after.pin.lon),'still centred on the same point (the app shows them as current)');
 for(const bad of [{lat:1},{confirmed:true,address:'x'},{},{address:'x',extra:1}])assert.equal((await x.put('/property/pin',bad)).status,422,JSON.stringify(bad));
});

test('moving the pin makes it the engineer’s and withdraws a confirmation the request does not repeat',async()=>{
 const x=instance(undefined,'confirmed'),p=(await x.get('/property')).body.pin;
 const moved=await x.put('/property/pin',{address:p.address,lat:p.lat+0.0001,lon:p.lon},{'If-Match':`"${p.version}"`});
 assert.deepEqual([moved.body.source,moved.body.confirmed,moved.body.place_label],['engineer',false,null]);
});

test('captures: 202 queued, then running, then ready around the pin; 409 while one runs; 200 for a recent one; 422 with no pin',async()=>{
 const x=instance();
 const p=(await x.get('/property')).body.pin;
 await x.put('/property/pin',{address:p.address,lat:p.lat+0.0002,lon:p.lon,confirmed:true},{'If-Match':`"${p.version}"`});
 const q=await x.post('/property/captures',{force:true});assert.equal(q.status,202);assert.equal(q.body.status,'queued');
 assert.equal((await x.post('/property/captures',{force:true})).body.code,'BASIS-CAPTURE-014');
 x.advance(4000);assert.equal((await x.get('/property')).body.capture.status,'running');
 // While it runs the newest capture has no views; the list holds the earlier one that succeeded.
 const list=(await x.get('/property/captures')).body.items;assert.equal(list[0].views.length,0);assert.equal(list[1].status,'succeeded');
 x.advance(9000);const done=(await x.get('/property')).body;
 assert.equal(done.capture.status,'succeeded');assert.ok(done.capture.views.every(v=>!v.stale));
 assert.equal(done.capture.views[0].lat,p.lat+0.0002);assert.ok(done.capture.measure_on);
 const recent=await x.post('/property/captures',{});assert.equal(recent.status,200,'the same pin was captured within 12 hours');assert.equal(recent.body.id,done.capture.id);
 const none=instance(undefined,'nopin');assert.equal((await none.post('/property/captures',{})).body.code,'BASIS-CAPTURE-015');
});

test('each sample state answers as the contract says, and nothing names a provider',async()=>{
 const want={running:['running',null],paused:['waiting','BASIS-CAPTURE-009'],waiting:['waiting',null],failed:['failed','BASIS-CAPTURE-007'],noplace:['failed','BASIS-CAPTURE-006']};
 for(const [variant,[status,code]] of Object.entries(want)){
  const b=(await instance(undefined,variant).get('/property')).body;
  assert.equal(b.capture.status,status,variant);assert.equal(b.capture.code,code,variant);
  assert.doesNotMatch(JSON.stringify(b),/google|bing|apple|earth|street view/i,variant);
 }
 assert.ok(Date.parse((await instance(undefined,'paused').get('/property')).body.capture.resume_at)>Date.parse('2026-11-23T20:00:00Z'));
 assert.equal((await instance(undefined,'nopin').get('/property')).body.pin,null);
});

test('a view’s image is drawn at its own scale: the drawn patio and fence measure what they are',async()=>{
 const x=instance(),cap=(await x.get('/property')).body.capture,close=cap.views.find(v=>v.label==='aerial-close');
 const img=await x.get(`/property/views/${close.id}/image`);assert.equal(img.status,200);
 const svg=Buffer.from(img.body).toString('utf8');assert.match(svg,/^<svg/);assert.match(svg,/width="1440" height="900"/);
 // The site plan in metres, through the view's georeference, measures the truth.
 const g=PM.georef(close),px=([e,n])=>[720+e/close.pixel_size_m,450-n/close.pixel_size_m];
 const fence=PM.measure(PM.measurementBody({unit:'LF',shapes:[{kind:'line',path:PS.SITE.fence.map(px)}],view:close}));
 assert.ok(Math.abs(Number(fence.quantity)-PS.truth.fenceFt)<0.05,fence.quantity);
 const patio=PM.measure(PM.measurementBody({unit:'SF',shapes:[{kind:'area',ring:PS.SITE.patio.map(px)}],view:close}));
 assert.ok(Math.abs(Number(patio.quantity)-800)<0.05,patio.quantity);
 assert.ok(g);
 // Before the patio was built it is not drawn.
 const early=cap.views.find(v=>v.label==='history-2023-07-30'),late=cap.views.find(v=>v.label==='history-2024-04-09');
 const color='#c9b79a',draw=async v=>Buffer.from((await x.get(`/property/views/${v.id}/image`)).body).toString('utf8');
 assert.ok(!(await draw(early)).includes(color));assert.ok((await draw(late)).includes(color));
});

test('public records: imagery with credit and year, the parcel, the site check, the heights',async()=>{
 const x=instance(),b=(await x.get('/property/public')).body;
 assert.equal(b.gather.status,'succeeded');assert.equal(b.current.kind,'current');assert.equal(b.history.length,4);
 for(const v of b.history){assert.ok(v.credit&&v.year);assert.equal(v.crs,'EPSG:3857');assert.equal(v.bbox.length,4)}
 assert.equal(b.parcels.filter(p=>p.under_pin).length,1);assert.equal(b.site_check.status,'no_appraisal');
 assert.ok(Math.abs(b.parcels[0].area_sqft-PS.truth.lotSqft)<0.1);
 assert.equal(b.lidar.building.peak_m,8.9);
 const site=(await instance(undefined,'site').get('/property/public')).body.site_check;
 assert.deepEqual([site.status,site.flag,site.code],['disagree',true,'BASIS-GEO-012']);
 const img=await x.get(`/property/public/views/${b.current.id}/image`);assert.equal(img.status,200);
 assert.equal((await instance(undefined,'nopin').post('/property/public',{})).body.code,'BASIS-GEO-002');
});

test('renovations: the bracket is written with If-Match, out-of-order dates are refused, 412 names the tag',async()=>{
 const x=instance(),r=await x.get('/renovations');assert.deepEqual([r.body.renovations,r.body.renovations_version,r.headers.etag],[null,0,'"0"']);
 const br=PM.bracket({date:'2023-07-30',reference:'view a'},{date:'2024-04-09',reference:'view b'}).bracket;
 const doc=PM.withBracket(null,{kind:'label',label:'patio'},br);
 const ok=await x.put('/renovations',doc,{'If-Match':'"0"'});assert.equal(ok.status,200);assert.equal(ok.body.renovations_version,1);
 assert.equal((await x.put('/renovations',doc,{'If-Match':'"0"'})).status,412);
 const backwards={...doc,exterior_brackets:[{label:'patio',bracket:{last_without:br.first_with,first_with:br.last_without}}]};
 assert.equal((await x.put('/renovations',backwards,{'If-Match':'"1"'})).status,422);
});

/* ---------- P9: the measurement on an asset correction ---------- */
async function fence(x){const items=(await x.get('/assets')).body.items;return items.find(a=>a.label==='fence')}
const decide=(x,corrections)=>x.a.call('GET',x.P).then(s=>x.a.call('POST',x.P+'/decisions',{body:{stage:'assets_review',revision:s.body.revision,decision:'reject',comment:null,corrections}}));
test('a traced fence prices the row: the takeoff is the computed length, with its formula and no flag',async()=>{
 const x=instance(),a=await fence(x),cap=(await x.get('/property')).body.capture,v=cap.views.find(w=>w.id===cap.measure_on);
 assert.deepEqual(a.review_reasons,['cross:COST_NEEDS_QUANTITY']);assert.equal(PM.needsMeasurement(a).unit,'LF');
 const px=([e,n])=>[720+e/v.pixel_size_m,450-n/v.pixel_size_m];
 const measurement=PM.measurementBody({unit:'LF',shapes:[{kind:'line',path:PS.SITE.fence.map(px)}],view:v});
 const r=await decide(x,[{entity_type:'asset',entity_id:a.id,before:null,after:{measurement},reason:null}]);
 assert.equal(r.status,202,JSON.stringify(r.body));
 x.advance(2500);const b=await fence(x);
 assert.equal(b.takeoff.unit,'LF');assert.equal(Number(b.takeoff.quantity),209.97,'64 m of fence is 209.97 ft');
 assert.match(b.takeoff.formula,/^209\.97 LF traced by the engineer on Aerial imagery viewed online; date as the imagery shows it \(captured 2025-11-20\), 1 line of 4 points; expected error up to 0\.\d\d %$/);
 assert.equal(b.takeoff.inputs.measurement.method,'local_tangent_plane_wgs84');
 assert.deepEqual(b.review_reasons,[]);assert.ok(Number(b.cost.total)>0,'priced by the cost table');
});
test('a measurement on a public image has a year only: it is flagged undated; with a typed quantity it is refused',async()=>{
 const x=instance(),a=await fence(x),pub=(await x.get('/property/public')).body.current;
 const g=PM.georef(pub),line=[[100,100],[400,100],[400,300]];
 const measurement=PM.measurementBody({unit:'LF',shapes:[{kind:'line',path:line}],view:pub});
 assert.equal(measurement.image.captured_on,undefined);
 const refused=await decide(x,[{entity_type:'asset',entity_id:a.id,before:null,after:{measurement,quantity:5,quantity_unit:'LF',quantity_source:'tape'},reason:null}]);
 assert.equal(refused.status,422);
 const bad=await decide(x,[{entity_type:'asset',entity_id:a.id,before:null,after:{measurement:{...measurement,unit:'SF'}},reason:null}]);
 assert.equal(bad.status,422);assert.match(JSON.stringify(bad.body),/height_ft is required/);
 assert.equal((await decide(x,[{entity_type:'asset',entity_id:a.id,before:null,after:{measurement},reason:null}])).status,202);
 x.advance(2500);const b=await fence(x);
 assert.deepEqual(b.review_reasons,['measure:IMAGE_UNDATED']);assert.ok(b.review_codes.includes('BASIS-ASSETS-021'));assert.equal(b.needs_review,true);
 assert.ok(g);
});

test('deleting the PROPOSED section removes every property route and the measurement',async()=>{
 const src=fs.readFileSync(path.join(WEB,'live/sample-api.js'),'utf8'),head=src.lastIndexOf('\n',src.indexOf('PROPOSED: not in the real API')),end=src.indexOf('/* END PROPOSED');
 const trimmed=src.slice(0,src.lastIndexOf('/*',head))+src.slice(src.indexOf('\n',end)+1),m={exports:{}};new Function('module','exports','require',trimmed)(m,m.exports,require);
 const a=m.exports.create({state:'assets',fetchAsset}),P=`/v1/studies/${a.mainStudyId()}`;
 for(const p of ['/property','/property/public','/renovations'])assert.equal((await a.call('GET',P+p)).status,404,p);
 const f=(await a.call('GET',P+'/assets')).body.items.find(x=>x.label==='fence');
 const r=await a.call('POST',P+'/decisions',{body:{stage:'assets_review',revision:2,decision:'reject',comment:null,corrections:[{entity_type:'asset',entity_id:f.id,before:null,after:{measurement:{unit:'LF'}},reason:null}]}});
 assert.equal(r.status,422,'today’s API refuses the field');
});
