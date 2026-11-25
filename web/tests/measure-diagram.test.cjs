// Photo | Diagram on the measure picture (web/live/property.js, live/property-model.js diagramModel;
// EVAL_REPORT 104.1): the diagram is drawn in the view's own pixels so the overlay stays put; the
// scale bar is the view's pixel size; stored lon/lat shapes are projected with the inverse of the
// georeference the tool traces with; image-space and off-picture measurements are skipped; one
// legend entry per type; an empty diagram still has north, a scale bar and a note; the toggle is
// absent on photos; the choice is remembered, and works with storage blocked.
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const WEB=path.join(__dirname,'..'),A=require('../live/sample-api.js'),PM=require('../live/property-model.js'),PS=require('../live/sample-property.js');
const NOW=Date.parse('2026-11-23T20:00:00Z');
const VIEW={id:'v',label:'aerial-close',width:1440,height:900,lat:43.5,lon:-87,pixel_size_m:0.0841,heading_deg:0,tilt_deg:0,imagery_on:'2026-06-18',source:'Aerial imagery'};
const plain=o=>JSON.parse(JSON.stringify(o));
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
 assert.equal(PM.diagramModel({view:VIEW,editing:true}).note,'No lot line was found for this property.','while a shape is being drawn, nothing is said about measurements');
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
 const fire=(name,ev)=>{for(const fn of listeners[name]||[])fn(ev)};
 return {Live,page,items,study,fire,setScreen:s=>{scr=s}};
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
 // the key, north and the scale bar sit in the stage's corners, outside the zoomed picture
 const furniture=/<div class="mt-furniture" data-mt-furniture="property">[\s\S]*?<\/div><\/div><\/div>/.exec(html)?.[0]||'';
 assert.match(html,/<\/div><\/div><div class="mt-furniture" data-mt-furniture="property">/,'after the zoom pane, not inside it');
 assert.match(furniture,/Building · roof outline from 2021 lidar · 2,210 SF/);assert.match(furniture,/Patio · 845\.4 SF/);assert.match(furniture,/mt-f-north/);
 const bar=/data-scale-px="([^"]+)" data-scale-ft="([^"]+)" data-k="([^"]+)"/.exec(html);
 assert.ok(Math.abs(Number(bar[1])*Number(bar[3])*0.0841*PM.FT_PER_M-Number(bar[2]))<1e-9,'screen pixels x picture pixels per screen pixel x pixel size = the length');
 assert.ok(Number(bar[1])<=140);
 // the building outline is drawn with its edge lengths
 assert.match(html,/class="mt-d-building"/);assert.match(html,/mt-d-label is-building/);
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

test('the lidar roof outline is drawn in the view’s pixels and is optional',()=>{
 const outline=[[-7.2,-5.2],[12.2,-5.2],[12.2,2.2],[5.2,2.2],[5.2,7.2],[-7.2,7.2]].map(([e,n])=>PS.toLonLat(e,n));
 const lidar={year:2021,building:{footprint_m2:205.3,outline}};
 const b=PM.buildingOnView(lidar,VIEW);
 assert.equal(b.points.length,6);assert.deepEqual(b.points[0],PM.georef(VIEW).toPixel(...outline[0]));
 assert.equal(b.label,'Building · roof outline from 2021 lidar');assert.ok(Math.abs(b.footprintSqft-205.3*PM.FT_PER_M**2)<1e-9);
 for(const old of [null,{year:2019,building:null},{year:2019,building:{footprint_m2:100}},{year:2019,building:{outline:[[1,2]]}}])assert.equal(PM.buildingOnView(old,VIEW),null,'a gather without an outline');
 assert.equal(PM.diagramModel({view:VIEW,building:b}).building,b);
});
test('a dragged corner snaps to the shape’s own corners first, then the building’s corners and edges, then the parcel',async()=>{
 const x=await boot({storage:memory()});x.setScreen('assets');
 const patio=x.items.find(a=>a.label==='patio');x.Live.openMeasure(patio,'assets');await x.page(()=>x.Live.measureDialog());
 const t=x.Live.state.mtDialog.trace,svg={dataset:{mtScope:'dialog',paStage:t.viewId},getBoundingClientRect:()=>({left:0,top:0,width:1440,height:900}),viewBox:{baseVal:{width:1440,height:900}},setPointerCapture(){},releasePointerCapture(){},closest(){return this}};
 const at=(p,vertex)=>({target:{closest:sel=>/data-mt-vertex/.test(sel)&&vertex?{dataset:{mtVertex:vertex}}:/svg\[/.test(sel)?svg:null},clientX:p[0],clientY:p[1],pointerId:1,preventDefault(){}});
 const drag=(from,to)=>{x.fire('pointerdown',at(from,'0:o:0'));x.fire('pointermove',at(to));x.fire('pointerup',at(to))};
 t.k=1;const building=PM.buildingOnView({year:2021,building:{outline:[[-7.2,-5.2],[12.2,-5.2],[12.2,2.2],[5.2,2.2],[5.2,7.2],[-7.2,7.2]].map(([e,n])=>PS.toLonLat(e,n))}},{id:t.viewId,width:1440,height:900,lat:43.5,lon:-87,pixel_size_m:0.0841}).points;
 const corner=building[5],c0=t.shapes[0].ring[0];
 drag(c0,[corner[0]+3,corner[1]-2]);
 assert.deepEqual(JSON.parse(JSON.stringify(t.shapes[0].ring[0])),corner.map(n=>Math.round(n*1000)/1000),'onto the building’s corner');
 const mid=[(building[0][0]+building[1][0])/2,building[0][1]];
 drag(t.shapes[0].ring[0],[mid[0],mid[1]+4]);
 assert.ok(Math.abs(t.shapes[0].ring[0][1]-mid[1])<0.01,'onto the building’s edge');
});

/* ---------- zoom keeps the middle of the view; labels stay clear of the pin ---------- */
// A pane of the Property page (the canvas is zoom x the pane's width) or of the dialog (a canvas
// narrower than the pane is centred in it), as the browser lays them out.
function pane(zoom,{clientWidth=900,clientHeight=560,fit=false}={}){
 const base=fit?Math.min(clientWidth,clientHeight*1440/900):clientWidth,canvasWidth=base*zoom,canvasHeight=canvasWidth*900/1440;
 const canvasLeft=Math.max(0,(clientWidth-canvasWidth)/2),canvasTop=fit?Math.max(0,(clientHeight-canvasHeight)/2):0;
 return {clientWidth,clientHeight,canvasWidth,canvasHeight,canvasLeft,canvasTop,scrollWidth:Math.max(clientWidth,canvasLeft*2+canvasWidth),scrollHeight:Math.max(clientHeight,canvasTop*2+canvasHeight),scrollLeft:0,scrollTop:0};
}
function zoomTo(from,zoom,opts){const f=PM.viewCentre(from),g=pane(zoom,opts),s=PM.scrollToCentre(f,g);return {...g,...s,f}}
test('zooming keeps the picture point at the middle of the view in the middle, at every level, on both stages',()=>{
 for(const opts of [{},{fit:true}]){
  // start zoomed in on a point inside the picture (the building, not the top left)
  let g=pane(4,opts);Object.assign(g,PM.scrollToCentre([0.62,0.3],g));
  for(const z of [2,1,2,4,8,4,2,4]){
   const next=zoomTo(g,z,opts),after=PM.viewCentre(next);
   const maxL=next.scrollWidth-next.clientWidth,maxT=next.scrollHeight-next.clientHeight;
   const clampedX=next.scrollLeft===0||Math.abs(next.scrollLeft-maxL)<1e-9,clampedY=next.scrollTop===0||Math.abs(next.scrollTop-maxT)<1e-9;
   if(!clampedX)assert.ok(Math.abs((after[0]-next.f[0])*next.canvasWidth)<=1,`x at ${z}x`);
   if(!clampedY)assert.ok(Math.abs((after[1]-next.f[1])*next.canvasHeight)<=1,`y at ${z}x`);
   assert.ok(next.scrollLeft>=0&&next.scrollLeft<=Math.max(0,maxL)&&next.scrollTop>=0&&next.scrollTop<=Math.max(0,maxT),'inside what the pane can scroll');
   g=next;
  }
 }
 // the old behaviour (the scroll kept in pixels) moved the middle: the reason for the fix
 const at4=pane(4);Object.assign(at4,PM.scrollToCentre([0.5,0.5],at4));
 const kept={...pane(2),scrollLeft:Math.min(at4.scrollLeft,pane(2).scrollWidth-900),scrollTop:Math.min(at4.scrollTop,pane(2).scrollHeight-560)};
 assert.ok(Math.abs(PM.viewCentre(kept)[0]-0.5)*kept.canvasWidth>100);
});
test('at an edge the scroll clamps there, and zooming in and out again does not drift',()=>{
 let g=pane(2);Object.assign(g,PM.scrollToCentre([0.02,0.98],g));// the bottom-left corner
 assert.equal(g.scrollLeft,0);assert.equal(g.scrollTop,g.scrollHeight-g.clientHeight);
 const seen=new Set();
 for(let i=0;i<10;i++){g=zoomTo(g,4);const at4=`${g.scrollLeft.toFixed(6)},${g.scrollTop.toFixed(6)}`;g=zoomTo(g,2);seen.add(`${at4}|${g.scrollLeft.toFixed(6)},${g.scrollTop.toFixed(6)}`)}
 assert.equal(seen.size,1,'the same views every time');
 assert.equal(g.scrollLeft,0);assert.equal(g.scrollTop,g.scrollHeight-g.clientHeight);
});
test('no label of the diagram sits under the pin; the building’s label moves below the outline when the pin is inside it',async()=>{
 const x=await boot({storage:memory()});x.Live.measurePicture.set('diagram');
 const html=await x.page(()=>x.Live.screens.property());
 const pinAt=/class="pa-pin" transform="translate\(([\d.]+) ([\d.]+)\)"/.exec(html).slice(1).map(Number),pin={at:pinAt,r:Math.max(7,1440/110)*1.9};
 const k=1440/900,font=12*k;// the first render's scale (the browser re-measures it)
 const outline=PM.buildingOnView({year:2021,building:{outline:[[-7.2,-5.2],[12.2,-5.2],[12.2,2.2],[5.2,2.2],[5.2,7.2],[-7.2,7.2]].map(([e,n])=>PS.toLonLat(e,n))}},{width:1440,height:900,lat:43.5,lon:-87,pixel_size_m:0.0841}).points;
 assert.ok(PM.insideOrOn(pinAt,outline),'the sample’s pin is inside the roof outline');
 const centre=PM.labelPoint(outline),box={w:12*0.62*font+8*k,h:font*2.7};
 assert.ok(PM.boxMeetsCircle([centre[0],centre[1]+font*0.35],box,pin.at,pin.r),'where the label used to go, the pin covered it');
 const b=/mt-d-total is-building" transform="translate\(([\d.]+) ([\d.]+)\)"/.exec(html).slice(1).map(Number);
 assert.ok(!PM.boxMeetsCircle([b[0],b[1]+font*0.35],box,pin.at,pin.r),'clear of the pin');
 assert.ok(b[1]>Math.max(...outline.map(p=>p[1])),'below the outline');
 for(const m of html.matchAll(/class="mt-d-label[^"]*" transform="translate\(([\d.]+) ([\d.]+)\) rotate\(([-\d.]+)\)"[^>]*>(?:<title>[^<]*<\/title>)?<rect x="[-\d.]+" y="[-\d.]+" width="([\d.]+)" height="([\d.]+)"/g)){
  const [lx,ly,a,w,h]=m.slice(1).map(Number);assert.ok(!PM.boxMeetsCircle([lx,ly],PM.turnedBox({w,h},a),pin.at,pin.r),`edge label at ${lx},${ly}`);
 }
 // without the pin (the layer off) the label stays in the middle of the outline
 x.Live.state.property[x.study.id].layers.pin=false;const off=await x.page(()=>x.Live.screens.property());
 const c=/mt-d-total is-building" transform="translate\(([\d.]+) ([\d.]+)\)"/.exec(off).slice(1).map(Number);
 assert.ok(Math.abs(c[0]-centre[0])<0.1&&Math.abs(c[1]-centre[1])<0.1);
});
test('clearOfPin: clear labels stay; covered ones go below the shape, above it, or straight away from the pin',()=>{
 const pin={at:[100,100],r:20},box={w:60,h:20};
 assert.deepEqual(PM.clearOfPin([300,300],box,pin),[300,300]);
 const shape={cx:100,minY:60,maxY:140},below=PM.clearOfPin([100,100],box,pin,{shape});
 assert.deepEqual(below,[100,140+4+10]);assert.ok(!PM.boxMeetsCircle(below,box,pin.at,pin.r));
 const above=PM.clearOfPin([100,140],box,{at:[100,150],r:20},{shape});assert.deepEqual(above,[100,60-4-10],'below is under the pin too: above');
 const pushed=PM.clearOfPin([110,100],box,pin);assert.ok(!PM.boxMeetsCircle(pushed,box,pin.at,pin.r+4)&&pushed[0]>110&&Math.abs(pushed[1]-100)<1e-9,'straight away, to the right');
 assert.deepEqual(PM.clearOfPin([100,100],box,null),[100,100]);
 assert.deepEqual(PM.turnedBox({w:60,h:20},90).w.toFixed(6),'20.000000');
});

/* ---------- a shape's name never overlaps an edge label or the pin ---------- */
// The live case (2026-11-25): a roof outline 180 x 165 px on a 1440 x 900 close aerial, the pin
// inside it, the bottom edge's length label present; at 100 % (k 1.6) the name was placed below
// the outline, on that label.
const ROOF=[[896.6,295.8],[1069.3,284],[1075.3,141.6],[1003.8,141.6],[980,129.8],[896.6,135.7]];
const LIVE_VIEW={width:1440,height:900,lat:39.79715705,lon:-89.64513155,pixel_size_m:0.0840796770343956};
function layout(k,pin){
 const frame=PM.frameOf(LIVE_VIEW),font=12*k,taken=[];
 for(const l of PM.edgeLabels(ROOF,true,PM.edgeLengths(ROOF,true,frame),{k,font:12}))if(l.mode!=='hidden'){
  const b={w:(l.text.length*0.62*12+10)*k,h:18*k},tb=PM.turnedBox(b,l.angle);taken.push({at:PM.clearOfPin([l.x,l.y],tb,pin),w:tb.w,h:tb.h,text:l.text,i:l.i})}
 const box={w:Math.max(8*0.62*font,12*0.62*font*0.85)+8*k,h:font*2.7},q=PM.labelPoint(ROOF);
 return {taken,box,name:PM.placeName(ROOF,box,{pin,boxes:taken,start:[q[0],q[1]+font*0.35]})};
}
test('the building’s name is placed after the edge labels, clear of every one of them and of the pin (the live geometry)',()=>{
 const pin={at:[980,195],r:Math.max(7,1440/110)*1.9};
 assert.ok(PM.insideOrOn(pin.at,ROOF),'the pin is inside the outline');
 for(const k of [1.6,0.8,0.4]){
  const {taken,box,name}=layout(k,pin);
  assert.ok(taken.some(t=>t.i===0),'the bottom edge’s label is there');
  for(const t of taken)assert.ok(!PM.boxesMeet({at:name,...box},t),`k ${k}: the name meets the label ${t.text}`);
  assert.ok(!PM.boxMeetsCircle(name,box,pin.at,pin.r),`k ${k}: the name is under the pin`);
 }
 // where it goes: inside the outline when the whole box fits there clear of the pin
 const at04=layout(0.4,pin);const fits=[[-1,-1],[1,-1],[1,1],[-1,1]].every(([sx,sy])=>PM.insideOrOn([at04.name[0]+sx*at04.box.w/2,at04.name[1]+sy*at04.box.h/2],ROOF));
 assert.ok(fits,'zoomed in, it fits inside the outline away from the pin');
 // with no pin and nothing in the way it stays at the outline's label point
 const free=PM.placeName(ROOF,{w:20,h:10},{boxes:[]});assert.deepEqual(free,PM.labelPoint(ROOF));
});
test('placeName tries inside, then below and above sliding past what is in the way, then beside',()=>{
 const sq=[[0,0],[100,0],[100,100],[0,100]],big={w:150,h:20};
 assert.deepEqual(PM.placeName(sq,big,{pin:{at:[50,50],r:10}}),[50,100+4+10],'too wide for the inside: below');
 const below={at:[50,114],w:200,h:20};
 const p=PM.placeName(sq,big,{pin:{at:[50,50],r:10},boxes:[below]});assert.ok(!PM.boxesMeet({at:p,...big},below,4)&&p[1]<0,'below is taken: above');
 const p2=PM.placeName(sq,big,{pin:{at:[50,50],r:10},boxes:[{at:[50,40],w:400,h:400}]});assert.ok(!PM.boxesMeet({at:p2,...big},{at:[50,40],w:400,h:400},4));
 const small={w:20,h:10},q=PM.placeName(sq,small,{pin:{at:[50,50],r:10}});
 assert.ok(PM.insideOrOn(q,sq)&&!PM.boxMeetsCircle(q,small,[50,50],14),'small enough: inside, away from the pin');
});
test('on the rendered diagram no name box meets an edge label or the pin',async()=>{
 const x=await boot({storage:memory()});x.Live.measurePicture.set('diagram');
 const html=await x.page(()=>x.Live.screens.property());
 const pinAt=/class="pa-pin" transform="translate\(([\d.]+) ([\d.]+)\)"/.exec(html).slice(1).map(Number),r=Math.max(7,1440/110)*1.9,font=12*1440/900;
 const edges=[...html.matchAll(/class="mt-d-label[^"]*" transform="translate\(([\d.]+) ([\d.]+)\) rotate\(([-\d.]+)\)"[^>]*>(?:<title>[^<]*<\/title>)?<rect x="[-\d.]+" y="[-\d.]+" width="([\d.]+)" height="([\d.]+)"/g)].map(m=>{const [x0,y0,a,w,h]=m.slice(1).map(Number);return {at:[x0,y0],...PM.turnedBox({w,h},a)}});
 const names=[...html.matchAll(/class="mt-d-total([^"]*)" transform="translate\(([\d.]+) ([\d.]+)\)"[^>]*data-box="([\d.]+) ([\d.]+)"/g)].map(m=>{const [ax,ay,w,h]=m.slice(2).map(Number),line=/is-line/.test(m[1]);return {at:[line?ax+w/2:ax,ay+font*0.35],w,h}});
 assert.ok(edges.length>=6&&names.length>=2,'the building’s and the patio’s');
 for(const n of names){for(const e of edges)assert.ok(!PM.boxesMeet(n,e),'a name on an edge label');assert.ok(!PM.boxMeetsCircle(n.at,n,pinAt,r),'a name under the pin')}
 for(let i=0;i<names.length;i++)for(let j=i+1;j<names.length;j++)assert.ok(!PM.boxesMeet(names[i],names[j]),'two names on each other');
});

/* ---------- no label meets another, of any shape, or the pin ---------- */
// Every label on the picture as an upright box: the diagram's edge lengths and names, and the
// trace's edge lengths (the overlay's); the pin as a circle.
function labelBoxes(html,k){
 const font=12*k,boxes=[];
 for(const m of html.matchAll(/class="(mt-d-label|mt-label)[^"]*" transform="translate\(([-\d.]+) ([-\d.]+)\) rotate\(([-\d.]+)\)"[^>]*>(?:<title>[^<]*<\/title>)?<rect x="[-\d.]+" y="[-\d.]+" width="([\d.]+)" height="([\d.]+)"/g)){
  const [x0,y0,a,w,h]=m.slice(2).map(Number);boxes.push({kind:m[1],at:[x0,y0],...PM.turnedBox({w,h},a)});
 }
 for(const m of html.matchAll(/class="mt-d-total([^"]*)" transform="translate\(([-\d.]+) ([-\d.]+)\)"[^>]*data-box="([\d.]+) ([\d.]+)"/g)){
  const [ax,ay,w,h]=m.slice(2).map(Number);boxes.push({kind:'name',at:[/is-line/.test(m[1])?ax+w/2:ax,ay+font*0.35],w,h});
 }
 return boxes;
}
function assertClear(boxes,pin,what){
 for(let i=0;i<boxes.length;i++){
  if(pin&&boxes[i].kind!=='mt-label')assert.ok(!PM.boxMeetsCircle(boxes[i].at,boxes[i],pin.at,pin.r),`${what}: a ${boxes[i].kind} under the pin`);
  for(let j=i+1;j<boxes.length;j++)assert.ok(!PM.boxesMeet(boxes[i],boxes[j]),`${what}: ${boxes[i].kind} at ${boxes[i].at.map(Math.round)} meets ${boxes[j].kind} at ${boxes[j].at.map(Math.round)}`);
 }
}
async function sampleModel(x){
 const prop=await x.Live.api.request('GET',`/v1/studies/${x.study.id}/property`),pub=await x.Live.api.request('GET',`/v1/studies/${x.study.id}/property/public`);
 const v=PM.viewsOf(prop.capture).close,g=PM.georef(v);
 const model=PM.diagramModel({view:v,parcels:pub.parcels,measured:PM.measuredOnView(x.items,v),building:PM.buildingOnView(pub.lidar,v)});
 return {model,v,pin:{at:g.toPixel(prop.pin.lon,prop.pin.lat),r:Math.max(7,v.width/110)*1.9}};
}
test('the sample’s default view: no two labels meet and none is under the pin, at 100, 200 and 400 %',async()=>{
 const x=await boot({storage:memory()});const {model,v,pin}=await sampleModel(x);
 assert.ok(model.building&&model.shapes.length,'the patio and the building');
 for(const k of [1.6,0.8,0.4]){const {html}=x.Live.diagramBodyFor(model,v,{k,pin});const boxes=labelBoxes(html,k);assert.ok(boxes.length>=10);assertClear(boxes,pin,`k ${k}`)}
 // the collision the lead measured (the patio's bottom edge and the building's top edge meet at 100 %):
 // the building's, lower in priority, has no room and is left out, named in the title
 const {html,dropped}=x.Live.diagramBodyFor(model,v,{k:1.6,pin});
 assert.deepEqual(plain(dropped),[`Building 40'-8"`]);assert.match(html,/^<title>Diagram\. Not drawn for lack of room: Building 40&#39;-8&quot;/);
 const drawn=[...html.matchAll(/<title>(Patio|Building) · ([^<]*)<\/title>/g)].map(m=>m[1]);
 assert.equal(drawn.filter(w=>w==='Patio').length,4,'every patio length is drawn');// an edge too short on screen for a label (mode hidden) is in the edge list only, as before
 const bp=model.building.points,shown=PM.edgeLabels(bp,true,PM.edgeLengths(bp,true,PM.frameOf(v)),{k:1.6,font:12}).filter(l=>l.mode!=='hidden').length;
 assert.ok(shown>=5,'most building edges are long enough for a label');assert.equal(drawn.filter(w=>w==='Building').length+dropped.length,shown,'every building length long enough for a label is drawn or named');
});
test('the live roof: no two labels meet and none is under the pin, at 100, 200 and 400 %',async()=>{
 const x=await boot({storage:memory()}),g=PM.georef(LIVE_VIEW),view={...LIVE_VIEW,id:'live'};
 const building=PM.buildingOnView({year:2019,building:{footprint_m2:200.5,outline:ROOF.map(p=>g.toLonLat(...p))}},view);
 const model=PM.diagramModel({view,building}),pin={at:[980,195],r:Math.max(7,1440/110)*1.9};
 for(const k of [1.6,0.8,0.4]){const {html}=x.Live.diagramBodyFor(model,view,{k,pin});assertClear(labelBoxes(html,k),pin,`k ${k}`)}
});
test('a fence traced across the patio and the building: its labels stay where the tool puts them and nothing meets them',async()=>{
 const x=await boot({storage:memory()});x.Live.measurePicture.set('diagram');
 const fence=x.items.find(a=>a.label==='fence');x.Live.state.docId=fence.id;await x.page(()=>x.Live.screens.property());
 const u=x.Live.state.property[x.study.id],t=u.trace,px=([e,n])=>[720+e/0.0841,450-n/0.0841];
 t.shapes=[{kind:'line',path:[[-14,12],[8,12],[8,-2]].map(px)}];// across the patio, then down across the garage
 const html=await x.page(()=>x.Live.screens.property()),k=1440/900;
 const boxes=labelBoxes(html,k),traced=boxes.filter(b=>b.kind==='mt-label');
 assert.equal(traced.length,2,'the trace’s two lengths are drawn, never dropped');
 const pinAt=/class="pa-pin" transform="translate\(([\d.]+) ([\d.]+)\)"/.exec(html).slice(1).map(Number);
 assertClear(boxes,{at:pinAt,r:Math.max(7,1440/110)*1.9},'with the trace');
});
test('a length that fits nowhere is left out of the drawing and named in the diagram’s title',async()=>{
 const x=await boot({storage:memory()});const {model,v,pin}=await sampleModel(x);
 const wall={at:[720,450],w:4000,h:4000};// everything taken
 const {html,dropped}=x.Live.diagramBodyFor(model,v,{k:1.6,pin,obstacles:[wall]});
 assert.ok(dropped.length>=8&&dropped.some(t=>/^Building /.test(t))&&dropped.some(t=>/^Patio /.test(t)),String(dropped));
 assert.doesNotMatch(html,/class="mt-d-label/);
 assert.match(html,/^<title>Diagram\. Not drawn for lack of room: Patio 40&#39;-/);
 assert.match(html,/every length is in the edge list/);
 const ok=x.Live.diagramBodyFor(model,v,{k:0.4,pin});assert.deepEqual(plain(ok.dropped),[],'zoomed in, everything fits');assert.doesNotMatch(ok.html,/<title>Diagram\./);
});
test('placeEdgeLabel: its own side, the other side, slid along the middle half, nearer; else nothing',()=>{
 const sq=[[0,0],[200,0],[200,100],[0,100]],l=PM.edgeLabels(sq,true,[20,10,20,10],{k:1,font:12})[0],box={w:60,h:18};
 assert.deepEqual(PM.placeEdgeLabel(l,sq,true,box).at,[l.x,l.y]);
 const other=PM.placeEdgeLabel(l,sq,true,box,{boxes:[{at:[l.x,l.y],w:60,h:18}]});assert.ok(other.at[1]>0,'inside, the other side');
 const slid=PM.placeEdgeLabel(l,sq,true,box,{boxes:[{at:[100,0],w:20,h:60}]});assert.ok(Math.abs(slid.at[0]-100)<=50+1e-9&&Math.abs(slid.at[0]-100)>0,'slid, within the middle half');
 assert.equal(PM.placeEdgeLabel(l,sq,true,box,{boxes:[{at:[100,0],w:400,h:200}]}),null);
});
