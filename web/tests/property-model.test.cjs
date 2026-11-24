// The Property area's pure rules (web/live/property-model.js): the traced measurement against the
// server's documented values (backEnd/tests/unit/domain/test_traced.py on the measurement branch),
// the ellipsoid's closed-form area, and the server module's own answers on synthetic shapes
// (tests/fixtures/traced-reference.json, written by running basis/domain/traced.py); the
// georeference round trips; the capture and gather status texts; the history strip; capabilities.
const {test}=require('node:test');const assert=require('node:assert/strict');
const M=require('../live/property-model.js');
const REF=require('./fixtures/traced-reference.json');

/* ---------- rounding as the server rounds ---------- */
test('round2 is Python Decimal ROUND_HALF_UP on the shortest repr',()=>{
 for(const [x,want] of [[0.005,'0.01'],[0.015,'0.02'],[1.005,'1.01'],[2.675,'2.68'],[1.0049999,'1.00'],[800,'800.00'],[88.888888,'88.89'],[1e-7,'0.00'],[-1.005,'-1.01'],[10000000,'10000000.00'],[39400.00000000001,'39400.00']])assert.equal(M.round2(x),want,String(x));
});

/* ---------- the server's documented examples (test_traced.py) ---------- */
const SCALE={points:[[0,0],[100,0]],length:20,unit:'ft'};// 0.2 ft a pixel
const RECT=[[0,0],[100,0],[100,200],[0,200]];// 20 x 40 ft
const imageArea=(ring,extra={})=>({unit:'SF',space:'image',shapes:[{kind:'area',ring}],scale:{...SCALE},image:{source:'site plan sheet C1',captured_on:'2020-04-18'},...extra});
test('an image rectangle is its pixels times the scale: 800 SF, 7.00 % (test_traced)',()=>{
 const r=M.measure(imageArea(RECT));
 assert.equal(r.quantity,'800.00');assert.equal(r.area_sf,'800.00');assert.equal(r.length_ft,null);assert.equal(r.expected_error_pct,'7.00');
 assert.deepEqual([r.shape_count,r.corner_count,r.cutout_count,r.method],[1,4,0,'image_scale']);
 assert.equal(M.measure(imageArea(RECT,{unit:'SY'})).quantity,'88.89');
 assert.equal(M.measure(imageArea(RECT,{scale:{points:[[0,0],[100,0]],length:6.096,unit:'m'}})).quantity,'800.00');
 assert.equal(M.measure(imageArea(RECT.slice().reverse())).quantity,'800.00','either winding');
});
test('lines, cutouts, several areas and a wall face (test_traced)',()=>{
 const line=M.measure({...imageArea(RECT),unit:'LF',shapes:[{kind:'line',path:[[0,0],[300,0],[300,400]]}]});
 assert.equal(line.quantity,'140.00');assert.equal(line.expected_error_pct,'2.29');
 assert.match(M.formula(line,{source:'x',captured_on:'2020-04-18'}),/1 line of 3 points/);
 const big=[[0,0],[1000,0],[1000,1000],[0,1000]],holes=[[[100,100],[200,100],[200,200],[100,200]],[[500,500],[600,500],[600,600]]];
 const cut=M.measure(imageArea(big,{shapes:[{kind:'area',ring:big,cutouts:holes}]}));
 assert.equal(cut.quantity,'39400.00');assert.equal(cut.cutout_count,2);assert.equal(cut.corner_count,11);
 assert.match(M.formula(cut,{source:'x'}),/1 area of 11 corners \(2 cut out\)/);
 assert.equal(M.measure(imageArea(big,{shapes:[{kind:'area',ring:big,cutouts:[[[100,100],[200,100],[200,200],[100,200]]]}]})).expected_error_pct,'4.44');
 assert.equal(M.measure(imageArea(big,{shapes:[{kind:'area',ring:big,cutouts:[[[0,0],[100,0],[100,100],[0,100]]]}]})).quantity,'39600.00','a cutout touching its ring from inside');
 const two=M.measure(imageArea(RECT,{shapes:[{kind:'area',ring:RECT},{kind:'area',ring:[[200,200],[300,200],[300,300],[200,300]]}]}));
 assert.equal(two.quantity,'1200.00');
 for(const [unit,q] of [['SF','200.00'],['SY','22.22']]){const w=M.measure({...imageArea(RECT),unit,shapes:[{kind:'line',path:[[0,0],[250,0]]}],height_ft:4});assert.equal(w.quantity,q);assert.equal(w.length_ft,'50.00')}
 assert.equal(M.measure({...imageArea(RECT),shapes:[{kind:'line',path:[[0,0],[250,0]]}],height_ft:4}).expected_error_pct,'2.80','the height is typed: not in the error');
 assert.equal(M.measure(imageArea([[0,0],[10000,0],[10000,1]])).quantity,'200.00','a simple sliver');
});
test('the expected error formula (test_traced)',()=>{
 const e=M.expectedErrorPct;
 assert.equal(e({areaSf:800,edgesFt:120,lines:1,pixelFt:0.5}),'7.50');
 assert.equal(e({areaSf:800,edgesFt:120,lines:1,pixelFt:0.5,scalePx:50}),'15.50');
 assert.equal(e({areaSf:null,lengthFt:150,edgesFt:150,lines:2,pixelFt:0.5}),'1.33');
 assert.equal(e({areaSf:null,lengthFt:150,edgesFt:150,lines:1,pixelFt:0.5,scalePx:100}),'2.67');
 assert.equal(e({areaSf:800,edgesFt:120,lines:1,pixelFt:null}),null);
});

/* ---------- lon/lat against the ellipsoid's closed form ---------- */
// The WGS84 area between two parallels and two meridians (evals/trace_geometry.py box_area_closed_sf):
// a^2 * dlambda / 2 * (q(lat2) - q(lat1)), q the authalic function.
const A=6378137,F=1/298.257223563,E2=F*(2-F),E=Math.sqrt(E2),SF=M.FT_PER_M**2;
const q=lat=>{const s=Math.sin(lat*Math.PI/180);return (1-E2)*(s/(1-E2*s*s)-(1/(2*E))*Math.log((1-E*s)/(1+E*s)))};
const closedSf=(lat1,lat2,dlon)=>A*A*(dlon*Math.PI/180)/2*(q(lat2)-q(lat1))*SF;
function box(lat,eastFt,northFt,lon0=-100.5){const [m,n]=M.radii(lat),dlat=northFt/M.FT_PER_M/m*180/Math.PI,dlon=eastFt/M.FT_PER_M/(n*Math.cos(lat*Math.PI/180))*180/Math.PI;return {ring:[[lon0,lat],[lon0+dlon,lat],[lon0+dlon,lat+dlat],[lon0,lat+dlat]],dlat,dlon}}
test('a 20 x 40 ft rectangle at 25 to 49 N is within 0.01 % of the ellipsoid (and to the cent)',()=>{
 for(let lat=25;lat<=49;lat++){
  const {ring,dlat,dlon}=box(lat,20,40),r=M.measure({unit:'SF',space:'lonlat',shapes:[{kind:'area',ring}],image:{source:'t',pixel_size_m:0.1524}});
  const key=closedSf(lat,lat+dlat,dlon);
  assert.ok(Math.abs(Number(r.area_sf)-key)/key<1e-4,`${lat}: ${r.area_sf} vs ${key}`);
  assert.ok(Math.abs(Number(r.area_sf)-key)<=0.006,`${lat}: within the quantum`);
  assert.equal(r.method,'local_tangent_plane_wgs84');
 }
});
test('the server example: a 20 x 50 ft box at 25, 37 and 49 N, 0.1524 m pixels, 7.00 %',()=>{
 for(const lat of [25,37,49]){const {ring,dlat,dlon}=box(lat,20,50),r=M.measure({unit:'SF',space:'lonlat',shapes:[{kind:'area',ring}],image:{source:'t',pixel_size_m:0.1524}});
  assert.ok(Math.abs(Number(r.area_sf)-closedSf(lat,lat+dlat,dlon))<=0.006);assert.equal(r.expected_error_pct,'7.00')}
});
test('a shape across the antimeridian measures as the small shape it is',()=>{
 const {ring}=box(10,100,100,0),d=ring[1][0]-ring[0][0];
 const across=[[180-d/2,10],[-180+d/2,10],[-180+d/2,ring[2][1]],[180-d/2,ring[2][1]]];
 assert.ok(Math.abs(Number(M.measure({unit:'SF',space:'lonlat',shapes:[{kind:'area',ring:across}],image:{source:'t'}}).quantity)-10000)<0.1);
});
test('the server module’s own answers on 24 synthetic shapes, to the cent',()=>{
 assert.equal(REF.cases.length,24);
 for(const c of REF.cases){const r=M.measure(c.body);assert.equal(r.quantity,c.quantity,JSON.stringify(c.body.shapes[0]).slice(0,80));assert.equal(r.expected_error_pct,c.expected_error_pct);
  assert.equal(M.formula(r,{source:c.body.image.source,captured_on:c.body.image.captured_on,height_ft:c.body.height_ft}),c.formula)}
});

/* ---------- refusals before sending ---------- */
test('the shapes the server refuses are refused here with its words',()=>{
 const bow=M.problems(imageArea([[0,0],[100,100],[100,0],[0,100]]));assert.match(bow[0],/shapes\[0\]\.ring crosses or touches itself/);
 assert.match(M.problems(imageArea([[0,0],[1,1],[2,2]]))[0],/has no area|folds back/);
 assert.match(M.problems(imageArea(RECT,{shapes:[{kind:'area',ring:RECT,cutouts:[[[50,50],[150,50],[150,100]]]}]}))[0],/cutouts\[0\] is not inside its ring/);
 assert.match(M.problems({...imageArea(RECT),unit:'LF'})[0],/LF is measured with line shapes/);
 assert.match(M.problems({...imageArea(RECT),shapes:[{kind:'line',path:[[0,0],[5,0]]}]})[0],/height_ft is required/);
 assert.match(M.problems({...imageArea(RECT),image:{source:' '}})[0],/image\.source is required/);
 assert.match(M.problems(imageArea([[0,0],[0.01,0],[0.01,0.01],[0,0.01]]))[0],/must be above 0/);
 assert.deepEqual(M.problems(imageArea(RECT)),[]);
});

/* ---------- review flags ---------- */
test('the four review flags, as the server raises them',()=>{
 const r=M.measure(imageArea(RECT));
 assert.deepEqual(M.reviewFlags(r,{captured_on:'2020-04-18',placedInService:'2020-06-01'}),[]);
 assert.deepEqual(M.reviewFlags(r,{captured_on:null}),['measure:IMAGE_UNDATED']);
 assert.deepEqual(M.reviewFlags(r,{captured_on:'2021-06-01',placedInService:'2020-06-01'}),[],'365 days is the limit');
 assert.deepEqual(M.reviewFlags(r,{captured_on:'2021-06-02',placedInService:'2020-06-01'}),['measure:IMAGE_AFTER_SERVICE']);
 assert.deepEqual(M.reviewFlags({...r,expected_error_pct:null},{captured_on:'2020-04-18'}),['measure:ERROR_UNKNOWN']);
 assert.deepEqual(M.reviewFlags({...r,expected_error_pct:'10.01'},{captured_on:'2020-04-18'}),['measure:COARSE']);
 assert.deepEqual(M.reviewFlags({...r,expected_error_pct:'10.00'},{captured_on:'2020-04-18'}),[]);
});

/* ---------- georeference ---------- */
const VIEW={id:'v',kind:'aerial',label:'aerial-close',lat:43.5,lon:-87,heading_deg:0,tilt_deg:0,pixel_size_m:0.0841,width:1440,height:900};
test('pixel <-> lon/lat round trips on a property view, and the centre is the view’s point',()=>{
 const g=M.georef(VIEW);
 assert.deepEqual(g.toLonLat(720,450),[-87,43.5]);
 for(const [x,y] of [[0,0],[1440,900],[100.5,833.25],[1439,1]]){const [lon,lat]=g.toLonLat(x,y),[x2,y2]=g.toPixel(lon,lat);assert.ok(Math.abs(x-x2)<1e-6&&Math.abs(y-y2)<1e-6)}
 const [lonE]=g.toLonLat(820,450),[,latN]=g.toLonLat(720,350);assert.ok(lonE>-87&&latN>43.5,'east is right, north is up');
});
test('a 20 x 40 ft rectangle traced in pixels lands as 800 SF through the georeference',()=>{
 for(const lat of [25,31,37,43.5,49]){
  const v={...VIEW,lat},s=v.pixel_size_m,w=20/M.FT_PER_M/s,h=40/M.FT_PER_M/s,x=300,y=200;
  const body=M.measurementBody({unit:'SF',shapes:[{kind:'area',ring:[[x,y],[x+w,y],[x+w,y+h],[x,y+h]]}],view:{...v,source:'Aerial imagery viewed online; date as the imagery shows it',imagery_on:'2026-06-18'}});
  const r=M.measure(body);assert.ok(Math.abs(Number(r.quantity)-800)/800<1e-4,`${lat}: ${r.quantity}`);
  assert.equal(body.space,'lonlat');assert.equal(body.image.captured_on,'2026-06-18');assert.equal(body.image.pixel_size_m,0.0841);
 }
});
test('the backlog’s rounded metres-a-degree would have biased the same rectangle (why the radii are used)',()=>{
 const s=0.0841,w=20/M.FT_PER_M/s,h=40/M.FT_PER_M/s,lat=43.5,ll=([x,y])=>[-87+(x-720)*s/(111320*Math.cos(lat*Math.PI/180)),lat+(450-y)*s/110540];
 const r=M.measure({unit:'SF',space:'lonlat',shapes:[{kind:'area',ring:[[300,200],[300+w,200],[300+w,200+h],[300,200+h]].map(ll)}],image:{source:'t'}});
 const bias=Number(r.quantity)/800-1;assert.ok(Math.abs(bias)>0.002,`the constants are ${(bias*100).toFixed(2)} % off`);
});
test('a public view converts through its exact Mercator box; tilted and street views do not convert',()=>{
 const lat=43.5,lon=-87,side=600,px=0.15,scale=1/Math.cos(lat*Math.PI/180),[cx,cy]=M.toMercator(lon,lat),half=side*px*scale/2;
 const v={source_id:'synthetic-ortho',crs:'EPSG:3857',bbox:[cx-half,cy-half,cx+half,cy+half],width:side,height:side,pixel_size_m:px,lat,lon,credit:'State orthoimagery (sample)',year:2024};
 const g=M.georef(v),[l2,t2]=g.toLonLat(300,300);assert.ok(Math.abs(l2-lon)<1e-9&&Math.abs(t2-lat)<1e-9);
 const [x,y]=g.toPixel(...g.toLonLat(12.25,590.5));assert.ok(Math.abs(x-12.25)<1e-6&&Math.abs(y-590.5)<1e-6);
 // A pixel rectangle on a north-up Mercator view is a box of meridians and parallels: the server's
 // area of it is the ellipsoid's closed form (pixel_size_m is the spherical scale at the centre,
 // about 0.04 % from the ellipsoid here, which is why the exact box is used).
 const w=20/M.FT_PER_M/px,h=40/M.FT_PER_M/px,body=M.measurementBody({unit:'SF',shapes:[{kind:'area',ring:[[100,100],[100+w,100],[100+w,100+h],[100,100+h]]}],view:v}),r=M.measure(body);
 const [[w0,s0],[e0],[,n0]]=[body.shapes[0].ring[3],body.shapes[0].ring[1],body.shapes[0].ring[1]];
 assert.ok(Math.abs(Number(r.area_sf)-closedSf(s0,n0,e0-w0))<=0.02,`${r.area_sf} vs ${closedSf(s0,n0,e0-w0)}`);
 assert.ok(Math.abs(Number(r.quantity)-800)/800<1e-3,r.quantity);
 const img=M.imageOf(v);assert.equal(img.captured_on,undefined,'a year is not a date');assert.match(img.source,/State orthoimagery \(sample\), 2024/);
 assert.equal(M.georef({...VIEW,tilt_deg:60,pixel_size_m:null}),null);assert.equal(M.georef({...VIEW,kind:'street',pixel_size_m:null}),null);assert.equal(M.measurable({...VIEW,pixel_size_m:null}),false);
});

/* ---------- capture status ---------- */
test('each capture state reads in plain words, with no provider named',()=>{
 const now=Date.parse('2026-11-23T20:00:00Z'),tz='America/Chicago';
 const s=(capture,pin={version:2,lat:1,lon:1})=>M.captureStatus(capture,{pin,now,timeZone:tz});
 assert.equal(M.captureStatus(null,{pin:null}).action,'pin');
 assert.equal(M.captureStatus(null,{pin:{version:1,address:'x'}}).action,'start');
 assert.match(s({status:'queued',message:'Property pictures are queued.'}).text,/queued\. They are usually ready within two minutes/);
 const run=s({status:'running',message:'Taking property pictures.'});assert.equal(run.polling,true);assert.match(run.text,/two minutes/);
 const paused=s({status:'waiting',code:'BASIS-CAPTURE-009',message:'Property pictures are paused; they will resume on their own.',resume_at:'2026-11-23T21:00:00Z'});
 assert.equal(paused.state,'paused');assert.equal(paused.text,'Property pictures are paused until 3:00 pm; they will resume on their own.');
 const tomorrow=s({status:'waiting',code:'BASIS-CAPTURE-010',resume_at:'2026-11-24T15:30:00Z'});assert.match(tomorrow.text,/until Tue, Nov 24, 9:30 am/);
 assert.equal(s({status:'waiting',message:'Waiting for another property’s pictures to finish; this one is next.'}).state,'waiting');
 const failed=s({status:'failed',code:'BASIS-CAPTURE-006',message:null});assert.equal(failed.action,'retake');assert.match(failed.text,/did not lead to a place/);
 assert.equal(s({status:'failed',code:'BASIS-CAPTURE-099'}).text,'Property pictures could not be taken. Take them again later.');
 assert.equal(s({status:'succeeded',pin_version:2,views:[]}).state,'ready');
 const stale=s({status:'succeeded',pin_version:1,views:[{stale:true}]});assert.equal(stale.action,'retake');assert.equal(stale.stale,true);
 for(const st of ['queued','running','waiting','failed','succeeded'])assert.doesNotMatch(JSON.stringify(s({status:st,code:'BASIS-CAPTURE-006'})),/google|earth|street view/i);
 assert.equal(M.noteText({code:'BASIS-CAPTURE-020'}),'Street-level pictures are not available yet.');
});
test('the gather status says when the pin moved and when nothing came back',()=>{
 assert.equal(M.gatherStatus(null).state,'none');
 assert.equal(M.gatherStatus({status:'running'}).polling,true);
 assert.equal(M.gatherStatus({status:'succeeded',stale:true,sources:[]}).tone,'review');
 assert.match(M.gatherStatus({status:'succeeded',stale:false,sources:[{status:'failed'}]}).text,/No public source answered/);
});

/* ---------- the views and the history strip ---------- */
test('history newest first, leaf marks, year only, undated, at placed in service, the suggested view',()=>{
 const h=(d,leaf,id)=>({id,kind:'history',label:d?`history-${d}`:'history-x',imagery_on:d,leaf,pixel_size_m:0.0841,width:1440,height:900,lat:43.5,lon:-87,heading_deg:0,tilt_deg:0});
 const views=[h('2019-05-02','unknown','a'),h('2026-06-18','leaf-on','b'),h('2021-12-31','unknown','c'),h('2025-11-20','leaf-off','d'),h(null,'unknown','e')];
 const strip=M.historyStrip(views,{placedInService:'2026-11-01',measureOn:'d'});
 assert.deepEqual(strip.map(x=>x.id),['b','d','c','a','e']);
 assert.equal(strip.find(x=>x.id==='d').suggested,true);
 assert.deepEqual(strip.filter(x=>x.atService).map(x=>x.id),['b'],'Jun 18 2026 is 136 days before Nov 1 2026; Nov 20 2025 is 346');
 assert.equal(strip.find(x=>x.id==='e').undated,true);assert.equal(strip.find(x=>x.id==='c').yearOnly,true);
});
test('the view nearest the placed-in-service date is marked, a year-only date never is',()=>{
 const h=(d,id)=>({id,kind:'history',imagery_on:d});
 const s=M.historyStrip([h('2026-06-18','b'),h('2025-11-20','d'),h('2026-12-31','y')],{placedInService:'2026-11-01'});
 assert.deepEqual(s.filter(x=>x.atService).map(x=>x.id),['b']);
 assert.equal(M.dateText('2021-12-31'),'2021 (year only)');assert.equal(M.dateText(null),'Undated');assert.equal(M.dateText('2026-06-18'),'Jun 18, 2026');
 assert.equal(M.leafText('leaf-off'),'Trees bare');assert.equal(M.leafText('leaf-on'),'Trees in leaf');assert.equal(M.leafText('unknown'),'Season unknown');
});
test('views sorted for the screen; sides in a fixed order; the default measuring view',()=>{
 const v=(label,kind,extra={})=>({id:label,label,kind,pixel_size_m:kind==='oblique'?null:0.0841,width:1440,height:900,lat:43.5,lon:-87,heading_deg:0,tilt_deg:kind==='oblique'?60:0,...extra});
 const cap={measure_on:'history-2025-11-20',views:[v('side-east','oblique'),v('aerial-wide','aerial'),v('aerial-close','aerial'),v('side-south','oblique'),v('history-2025-11-20','history',{imagery_on:'2025-11-20'})]};
 const out=M.viewsOf(cap);assert.equal(out.close.label,'aerial-close');assert.deepEqual(out.sides.map(x=>x.label),['side-south','side-east']);
 assert.equal(M.defaultMeasureView(cap).label,'history-2025-11-20');
 assert.equal(M.defaultMeasureView({...cap,measure_on:null}).label,'aerial-close');
 assert.equal(M.compass(180),'south');
});

/* ---------- parcels and site check ---------- */
test('a parcel ring draws in pixels on the view it lies on; the site check reads in words',()=>{
 const g=M.georef(VIEW),ring=[[0,0],[100,0],[100,80],[0,80]].map(([x,y])=>g.toLonLat(x+200,y+300));
 const px=M.parcelRings({geometry:{type:'Polygon',coordinates:[ring]}},g)[0];
 assert.ok(Math.abs(px[2][0]-300)<1e-6&&Math.abs(px[2][1]-380)<1e-6);
 assert.equal(M.siteCheckText({status:'disagree',flag:true,parcel_sqft:10500,appraisal_sqft:9500,difference_pct:10.5,tolerance_pct:5}).flag,true);
 assert.equal(M.siteCheckText({status:'no_appraisal'}),null);
 assert.match(M.siteCheckText({status:'agree',parcel_sqft:9600,appraisal_sqft:9500,tolerance_pct:5,difference_pct:1.05}).text,/within 5 %/);
});

/* ---------- renovation brackets ---------- */
test('the bracket orders the two dates and refuses equal or undated ones',()=>{
 const a={date:'2024-04-09',reference:'view a'},b={date:'2023-07-30',reference:'view b'};
 assert.deepEqual(M.bracket(a,b).bracket,{last_without:{date:'2023-07-30',reference:'view b'},first_with:{date:'2024-04-09',reference:'view a'}});
 assert.ok(M.bracket(a,a).error);assert.ok(M.bracket(a,{date:null}).error);
 const doc=M.withBracket(null,{kind:'label',label:'patio'},M.bracket(a,b).bracket);
 assert.deepEqual(doc.projects,[]);assert.equal(doc.exterior_brackets[0].label,'patio');
 const again=M.withBracket(doc,{kind:'label',label:'patio'},M.bracket(a,{date:'2025-01-01',reference:'c'}).bracket);assert.equal(again.exterior_brackets.length,1,'one entry per label');
 const withProject=M.withBracket({projects:[{key:'deck-2025'}],exterior_brackets:[]},{kind:'project',key:'deck-2025'},M.bracket(a,b).bracket);assert.equal(withProject.projects[0].aerial.first_with.date,'2024-04-09');
});

/* ---------- what to measure, capabilities ---------- */
test('an exterior row held for a measured length or area offers the aerial; an interior or counted one does not',()=>{
 const fence={room:{room_type:'exterior'},review_reasons:['cross:COST_NEEDS_QUANTITY'],takeoff:{unit:'EA'},cost:{trail:{base:{unit:'LF'}}}};
 assert.deepEqual(M.needsMeasurement(fence),{needed:true,offer:true,reason:'cross:COST_NEEDS_QUANTITY',unit:'LF'});
 assert.equal(M.needsMeasurement({...fence,review_reasons:['input:exterior_area'],cost:{trail:{base:{unit:'SF'}}}}).unit,'SF');
 assert.equal(M.needsMeasurement({...fence,room:{room_type:'kitchen'}}).needed,false);
 assert.equal(M.needsMeasurement({room:{room_type:'exterior'},review_reasons:[],takeoff:{unit:'EA'},cost:{trail:{base:{unit:'EA'}}}}).offer,false);
 assert.equal(M.needsMeasurement({room:null,review_reasons:['input:room'],takeoff:{unit:'SF'}}).needed,true);
});
test('capabilities: only true is true, so today’s API hides everything',()=>{
 assert.deepEqual(M.caps(null),{propertyCapture:false,publicGeo:false,tracedMeasurement:false});
 assert.deepEqual(M.caps({object_exclusion:true}),{propertyCapture:false,publicGeo:false,tracedMeasurement:false});
 assert.deepEqual(M.caps({property_capture:'true',public_geo:1,traced_measurement:true}),{propertyCapture:false,publicGeo:false,tracedMeasurement:true});
 assert.equal(M.anyProperty(M.caps({public_geo:true})),true);assert.equal(M.anyProperty(M.caps({})),false);
});
