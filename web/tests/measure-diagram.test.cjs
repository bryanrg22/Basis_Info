// Photo | Diagram on the measure picture (web/live/property.js, live/property-model.js diagramModel;
// EVAL_REPORT 104.5): the diagram is drawn in the view's own pixels so the overlay stays put; the
// scale bar is the view's pixel size; stored lon/lat shapes are projected with the inverse of the
// georeference the tool traces with; image-space and off-picture measurements are skipped; one
// legend entry per type; an empty diagram still has north, a scale bar and a note; the toggle is
// absent on photos; the choice is remembered, and works with storage blocked.
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const WEB=path.join(__dirname,'..'),A=require('../live/sample-api.js'),PM=require('../live/property-model.js'),PS=require('../live/sample-property.js');
const NOW=Date.parse('2026-11-23T20:00:00Z');
const VIEW={id:'v',label:'aerial-close',width:1440,height:900,lat:43.5,lon:-87,pixel_size_m:0.0841,heading_deg:0,tilt_deg:0,imagery_on:'2026-06-18',source:'Aerial imagery'};
const ring=PS.SITE.patio.map(([e,n])=>PS.toLonLat(e,n));
const body=(extra={})=>({unit:'SF',space:'lonlat',shapes:[{kind:'area',ring}],image:{source:'Aerial imagery',pixel_size_m:0.0841},...extra});
const asset=(id,label,m)=>({id,label,category:label,takeoff:{unit:'SF',inputs:m?{measurement:m}:{}}});

test('the diagram is in the view’s own pixels: a lot corner and a measured corner land where the overlay puts them',()=>{
 const g=PM.georef(VIEW),parcel={under_pin:true,geometry:{type:'Polygon',coordinates:[PS.SITE.lot.map(([e,n])=>PS.toLonLat(e,n))]}};
 const m=PM.diagramModel({view:VIEW,parcels:[parcel],measured:PM.measuredOnView([asset('a','patio',body())],VIEW)});
 assert.equal(m.w,1440);assert.equal(m.h,900);
 const [lon,lat]=parcel.geometry.coordinates[0][2];assert.deepEqual(m.lots[0].points[2],g.toPixel(lon,lat));
 assert.deepEqual(m.lots[0].points,PM.parcelRings(parcel,g)[0],'the same rings the photo overlay draws');
 // the measured corner: the pixel the tool would put it at, and back through the tool's own frame
 const px=m.shapes[0].shapes[0].ring,frame=PM.frameOf(VIEW);
 for(let i=0;i<ring.length;i++){assert.deepEqual(px[i],g.toPixel(...ring[i]));const back=frame.toLonLat(...px[i]);assert.ok(Math.abs(back[0]-ring[i][0])<1e-9&&Math.abs(back[1]-ring[i][1])<1e-9)}
});
test('the scale bar is a round length in feet at the view’s exact pixel size',()=>{
 for(const s of [0.0841,0.2447,0.15,0.6]){const b=PM.scaleBar(s,{maxPx:360});
  assert.ok(Math.abs(b.px*s*PM.FT_PER_M-b.ft)<1e-9,`${s} m a pixel`);assert.ok(b.px<=360);
  assert.ok([1,2,5].includes(Math.round(b.ft/Math.pow(10,Math.floor(Math.log10(b.ft))))),`${b.ft} is 1, 2 or 5 x 10^n`);
  assert.ok(b.px*2.5>360,'the largest round length that fits')}
 assert.deepEqual(PM.scaleBar(0.0841,{maxPx:360}),{ft:50,px:50/(0.0841*PM.FT_PER_M),label:'50 ft'});
 assert.equal(PM.scaleBar(0.2447,{maxPx:360}).label,'200 ft');assert.equal(PM.scaleBar(null),null);
 assert.equal(PM.diagramModel({view:VIEW}).scale.ft,50,'a quarter of the picture’s width at most');
});
test('only lon/lat measurements on this picture are drawn; a pending correction replaces the stored one; the edited asset is left to the tool',()=>{
 const img={unit:'SF',space:'image',shapes:[{kind:'area',ring:[[0,0],[100,0],[100,100]]}],scale:{points:[[0,0],[100,0]],length:5,unit:'ft'},image:{source:'photo'}};
 const far=body({shapes:[{kind:'area',ring:ring.map(([lon,lat])=>[lon+0.05,lat])}]});// 4 km east: off the picture
 const moved=body({shapes:[{kind:'area',ring:ring.map(([lon,lat])=>[lon+0.00002,lat])}]});
 const items=[asset('a','patio',body()),asset('b','countertop',img),asset('c','driveway',far),asset('d','fence',null),asset('e','porch',body({drawn_by:'model:x@1',verified:false})),asset('f','deck','not a measurement')];
 const pending=new Map([['e',{after:{measurement:moved}}]]);
 const got=PM.measuredOnView(items,VIEW,{pending,except:'a'});
 assert.deepEqual(got.map(m=>m.assetId),['e']);assert.equal(got[0].pending,true);
 assert.deepEqual(got[0].shapes[0].ring[0],PM.georef(VIEW).toPixel(...moved.shapes[0].ring[0]));
 assert.deepEqual(PM.measuredOnView(items,VIEW).map(m=>m.assetId),['a','e']);
 assert.deepEqual(PM.measuredOnView(items,{...VIEW,heading_deg:30}),[],'a view that is not north up has no diagram');
});
test('the legend lists each measured type once, with its total, in a colour that stays the same',()=>{
 const half=body({shapes:[{kind:'area',ring:ring.slice(0,3)}]});
 const items=[asset('a','patio',body()),asset('b','patio',half),asset('c','driveway',body())];
 const m=PM.diagramModel({view:VIEW,measured:PM.measuredOnView(items,VIEW)});
 assert.deepEqual(m.legend.map(x=>[x.name,x.count]),[['Driveway',1],['Patio',2]]);
 const q=Number(PM.measure(body()).quantity)+Number(PM.measure(half).quantity);
 assert.equal(m.legend[1].quantity,`${q.toLocaleString('en-US',{maximumFractionDigits:2})} SF`);
 assert.equal(new Set(m.shapes.filter(s=>s.label==='patio').map(s=>s.colour)).size,1);assert.notEqual(m.legend[0].colour,m.legend[1].colour);
 for(const c of PM.DIAGRAM_COLOURS)assert.match(c,/^#[0-9a-f]{6}$/);
});
test('with no lot line and nothing measured the diagram still has north, a scale bar and a note',()=>{
 const m=PM.diagramModel({view:VIEW});
 assert.deepEqual([m.lots.length,m.shapes.length,m.legend.length],[0,0,0]);assert.equal(m.north.angle===0||Object.is(m.north.angle,-0),true);
 assert.ok(m.scale.px>0);assert.match(m.note,/No lot line and nothing measured on this picture yet/);
 assert.match(m.credit,/Picture of Jun 18, 2026 · 8\.4 cm a pixel · Aerial imagery/);
 assert.match(PM.diagramModel({view:VIEW,parcels:[{under_pin:true,geometry:{type:'Polygon',coordinates:[PS.SITE.lot.map(([e,n])=>PS.toLonLat(e,n))]}}]}).note,/Nothing measured/);
 assert.equal(PM.diagramModel({view:{...VIEW,pixel_size_m:null}}),null,'no georeference, no diagram');
});

/* ---------- the screen ---------- */
async function boot({storage}={}){
 const sample=A.create({state:'assets',now:()=>NOW,measureDraft:true,fetchAsset:p=>fs.readFileSync(path.join(WEB,p))});sample._inst.propertyState='confirmed';
 const api=require('../live/api-client.js').createClient({baseUrl:sample.base,fetchImpl:(u,i)=>sample.fetch(u,i),getToken:async()=>'t'});
 const study=await api.getStudy(sample.mainStudyId());
 const cache=new Map(),listeners={};let scr='property';
 const load=(key,fn)=>{if(cache.has(key))return cache.get(key);const e={done:false};cache.set(key,e);e.promise=Promise.resolve().then(fn).then(d=>{e.data=d;e.done=true},err=>{e.error=err;e.done=true});return e};
 const Live={api,caps:PM.caps({property_capture:true,public_geo:true,traced_measurement:true}),state:{cache,studyId:study.id,study,docId:null,corrections:{}},screens:{},actions:{},afterRender:[],beforeRender:[],load,
  invalidate(){},withStudy:fn=>fn(study),me:()=>({memberships:[{org_id:study.org_id,role:'owner'}]}),route:(s,id,doc)=>`#${s}/${id||''}${doc?'/'+doc:''}`,when:()=>'',loading:t=>t,failed:e=>e?.message,
  render(){},screen:()=>scr,labelName:l=>String(l||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase()),gateOpen:st=>study.current_stage===st&&study.status==='waiting_review',
  guarded:async(n,fn)=>fn(),errorText:e=>e?.message||'',addCorrection(){},pendingCorrection:()=>null,photo:(id,alt,cls)=>`<img data-live-doc="${id}" class="${cls}">`,photoUrl:()=>new Promise(()=>{}),studyTools:()=>''};
 const g={BASIS_CONFIG:{mode:'live'},BasisLive:Live,BasisPropertyModel:PM,BasisReasons:require('../live/review-reasons.js'),Intl,URL,Date,Promise,Map,Set,JSON,Math,Number,String,Array,Object,Boolean,RegExp,Error,encodeURIComponent,decodeURIComponent,console,
  setTimeout:()=>0,clearTimeout(){},matchMedia:()=>({matches:false}),icon:()=>'',heading:t=>t,toast(){},esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  document:{addEventListener:(n,fn)=>(listeners[n]||=[]).push(fn),querySelector:()=>null,querySelectorAll:()=>[],getElementById:()=>null}};
 if(storage)g.localStorage=storage;
 g.globalThis=g;vm.createContext(g);vm.runInContext(fs.readFileSync(path.join(WEB,'live/property.js'),'utf8'),g);
 async function page(fn){for(let i=0;i<10;i++){fn();const open=[...cache.values()].filter(e=>!e.done);if(!open.length)break;await Promise.all(open.map(e=>e.promise))}return fn()}
 const items=(await api.assets(study.id,study.revision)).items;
 return {Live,page,items,study,setScreen:s=>{scr=s}};
}
const memory=()=>{const m=new Map();return {getItem:k=>m.has(k)?m.get(k):null,setItem:(k,v)=>m.set(k,String(v)),m}};
const blocked={getItem(){throw new Error('SecurityError')},setItem(){throw new Error('SecurityError')}};

test('the Property page draws the diagram in place of the picture, under the same overlay, with the same box',async()=>{
 const x=await boot({storage:memory()});
 let html=await x.page(()=>x.Live.screens.property());
 assert.match(html,/data-live="mt-picture" data-value="photo" aria-pressed="true"/);assert.doesNotMatch(html,/mt-diagram/);
 x.Live.actions['mt-picture'](null,'diagram');html=await x.page(()=>x.Live.screens.property());
 const vb=[...html.matchAll(/viewBox="([^"]+)"/g)].map(m=>m[1]);
 assert.match(html,/<svg class="mt-diagram" viewBox="0 0 1440 900"/);assert.ok(vb.filter(v=>v==='0 0 1440 900').length>=2,'diagram and overlay share the box');
 assert.match(html,/<div class="pa-canvas is-diagram"[^>]*><svg class="mt-diagram"/,'the picture is replaced by the diagram');
 assert.match(html,/class="mt-d-lot"/);assert.match(html,/class="mt-d-lot is-adjacent"/);assert.doesNotMatch(html,/class="pa-parcel/,'the overlay leaves the lot lines to the diagram');
 assert.match(html,/class="mt-d-legend"[\s\S]*Patio · 845\.4 SF/);assert.match(html,/class="mt-d-north"/);
 const bar=/data-scale-px="([^"]+)" data-scale-ft="([^"]+)"/.exec(html);assert.equal(Number(bar[2]),50);assert.ok(Math.abs(Number(bar[1])*0.0841*PM.FT_PER_M-50)<1e-9);
 assert.match(html,/class="pa-pin"/,'the pin stays on the overlay');
});
test('in the Measure dialog the traced shape and its handles stay put in diagram mode; photos have no toggle',async()=>{
 const x=await boot({storage:memory()});x.setScreen('assets');
 const patio=x.items.find(a=>a.label==='patio');x.Live.openMeasure(patio,'assets');
 const photo=await x.page(()=>x.Live.measureDialog()),layer=h=>/<g class="pa-traces mt-layer" data-mt-layer>[\s\S]*?<\/svg>/.exec(h)[0];
 x.Live.actions['mt-picture'](null,'diagram');const diagram=x.Live.measureDialog();
 assert.equal(layer(diagram),layer(photo),'the overlay is the same, corner for corner');
 assert.match(diagram,/data-mt-diagram="dialog"/);assert.doesNotMatch(diagram,/Patio · /,'the asset being edited is drawn by the tool, not twice');
 const ct=x.items.find(a=>a.label==='countertop');x.Live.state.mtDialog=null;x.Live.measurePhotoDims.set('5a310001-0000-4000-8000-000000000002',[1200,800]);x.Live.openMeasure(ct,'assets');
 const onPhoto=await x.page(()=>x.Live.measureDialog());assert.match(onPhoto,/mt-thumb/);assert.doesNotMatch(onPhoto,/mt-picture-toggle|mt-diagram/);
});
test('the choice is remembered in this browser, and still works for the session when storage is blocked',async()=>{
 const store=memory();let x=await boot({storage:store});assert.equal(x.Live.measurePicture.get(),'photo','Photo by default');
 x.Live.actions['mt-picture'](null,'diagram');assert.equal(store.m.get('basis.measure.picture'),'diagram');
 x=await boot({storage:store});assert.equal(x.Live.measurePicture.get(),'diagram','a new page reads it');
 const y=await boot({storage:blocked});assert.equal(y.Live.measurePicture.get(),'photo');
 y.Live.actions['mt-picture'](null,'diagram');assert.equal(y.Live.measurePicture.get(),'diagram');
 assert.match(await y.page(()=>y.Live.screens.property()),/mt-diagram/);
 const z=await boot();assert.equal(z.Live.measurePicture.get(),'photo','no storage at all');
});
