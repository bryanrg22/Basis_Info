// The measure tool's geometry (web/live/property-model.js, EVAL_REPORT 104): a length on every
// edge computed as the server computes it (the labels sum to the server's length and perimeter,
// pinned against values printed by basis.domain.traced), feet and inches written and read back,
// labels outside the shape and never upside down, a typed length that gives the edge exactly that
// length, editing that keeps the ring simple, snapping, undo and redo, the record read back, and
// the provenance keys checked with the server's words.
const {test}=require('node:test');const assert=require('node:assert/strict');
const PM=require('../live/property-model.js');

const VIEW={id:'v',label:'aerial-close',width:1440,height:900,lat:43.5,lon:-87,pixel_size_m:0.0841};
const RING=[[300.5,200.25],[910.75,212.5],[880,640.125],[402.3,600.9],[350,420]];
const frame=PM.frameOf(VIEW);
const sum=a=>a.reduce((t,x)=>t+x,0);
const image=(ft=20,px=100)=>PM.frameOf(null,{scale:{points:[[0,0],[px,0]],length:ft,unit:'ft'}});

/* ---------- lengths as the server measures them ---------- */
test('each edge is measured on the tangent plane at its own middle latitude, digit for digit with the server',()=>{
 // backEnd: lonlat_path_length_ft([a, b]) for each edge of this ring's lon/lat (the body's,
 // rounded to 1e-9 degree), and for the closed path: printed 2026-11-25.
 const lengths=PM.edgeLengths(RING,true,frame);
 assert.deepEqual(lengths,[168.41265105573947,118.29454147701402,132.25031971214378,51.95785887387127,62.152118164820415]);
 assert.equal(sum(lengths),533.0674892835889);
});
test('a line’s labels sum to the length the server computes for it',()=>{
 const path=[[100,100],[500,120],[640,480],[1200,470]],labels=PM.edgeLengths(path,false,frame);
 const body=PM.measurementBody({unit:'LF',shapes:[{kind:'line',path}],view:VIEW});
 assert.equal(PM.round2(sum(labels)),PM.measure(body).length_ft);
 // and in image space: pixels times the scale
 const f=image(),img=PM.edgeLengths([[0,0],[100,0],[100,50]],false,f);
 assert.deepEqual(img,[20,10]);
});
test('a ring’s labels sum to its perimeter, the closed path measured as the server measures a line',()=>{
 const shape={kind:'area',ring:RING},s=PM.shapeSummary(shape,frame,{unit:'SF'});
 const closed=PM.measurementBody({unit:'LF',shapes:[{kind:'line',path:[...RING,RING[0]]}],view:VIEW});
 assert.equal(PM.round2(s.perimeterFt),PM.measure(closed).length_ft);
 assert.equal(PM.round2(sum(PM.edgeLengths(RING,true,frame))),'533.07');
 // the area at the middle is the server's (17,177.85 SF printed by traced.measure)
 assert.equal(PM.round2(s.areaSf),'17177.85');
 assert.equal(PM.measure(PM.measurementBody({unit:'SF',shapes:[shape],view:VIEW})).area_sf,'17177.85');
 assert.equal(s.area,'17,178 SF');assert.equal(PM.shapeSummary(shape,frame,{unit:'SY'}).area,'1,908.6 SY');
});
test('a cut-out is taken out on the ring’s own plane; a wall face is length times height',()=>{
 const f=image(),shape={kind:'area',ring:[[0,0],[100,0],[100,200],[0,200]],cutouts:[[[10,10],[20,10],[20,20],[10,20]]]};
 assert.equal(PM.shapeSummary(shape,f,{unit:'SF'}).areaSf,800-4);
 const wall=PM.shapeSummary({kind:'line',path:[[0,0],[100,0]]},f,{unit:'SF',heightFt:8});
 assert.equal(wall.text,`20'-0" × 8'-0" = 160 SF`);
 assert.equal(PM.shapeSummary({kind:'line',path:[[0,0],[100,0],[100,100]]},f,{unit:'LF'}).text,'40 LF');
});

/* ---------- feet and inches ---------- */
test('lengths are written in feet and inches to the nearest inch, or whole feet when short',()=>{
 assert.equal(PM.ftIn(54.25),`54'-3"`);assert.equal(PM.ftIn(54),`54'-0"`);assert.equal(PM.ftIn(0.5),`0'-6"`);
 assert.equal(PM.ftIn(11.99),`12'-0"`,'11 ft 11.88 in rounds up to the next foot, never 11\'-12"');
 assert.equal(PM.ftIn(1234.5),`1,234'-6"`);assert.equal(PM.ftIn(54.4,{short:true}),`54'`);
 assert.equal(PM.ftIn(NaN),'—');assert.equal(PM.ftInWords(54.25),'54 feet 3 inches');assert.equal(PM.ftInWords(1+1/12),'1 feet 1 inch');
});
test('a typed length is read in every way engineers write it',()=>{
 const ok={[`54'3"`]:54.25,[`54' 3"`]:54.25,[`54'-3"`]:54.25,[`54' - 3"`]:54.25,'54 ft 3 in':54.25,'54ft3in':54.25,[`54'3`]:54.25,'54.25':54.25,'54.25 ft':54.25,'54.25 feet':54.25,[`54.25'`]:54.25,
  '54’ 3”':54.25,'54′3″':54.25,[`54''`]:54/12,'36"':3,'36 in':3,'36 inches':3,'.5':0.5,'1,234.5':1234.5,[`1,234'-6"`]:1234.5,'16.5 m':16.5/0.3048,'16.5m':16.5/0.3048,'16.5 metres':16.5/0.3048,'1650 cm':16.5/0.3048,'  54 FT  ':54,[`54'3.5"`]:54+3.5/12};
 for(const [text,ft] of Object.entries(ok)){const r=PM.parseLength(text);assert.ok(Math.abs(r.ft-ft)<1e-9,`${text} -> ${JSON.stringify(r)}`)}
});
test('a typed length that is not one is refused in plain words, never guessed',()=>{
 for(const bad of ['','   ','abc','54 yards','-5','0',`0'-0"`,'1e3','54..2','five feet','54 3','54"3\'','100001',`54'15"`])assert.ok(PM.parseLength(bad).error,`${bad} is refused`);
 assert.match(PM.parseLength(`54'15"`).error,/15 inches is a foot or more: write 55'-3" instead/);
 assert.match(PM.parseLength('abc').error,/“abc” is not a length/);
 assert.match(PM.parseLength('0').error,/above zero/);assert.match(PM.parseLength('100001').error,/at most 100,000 ft/);
});

/* ---------- where the labels go ---------- */
test('each label is outside the shape at its edge’s middle, upright, whichever way the ring winds',()=>{
 const f=image(2,2),sq=[[100,100],[300,100],[300,300],[100,300]];
 for(const ring of [sq,sq.slice().reverse()]){
  const labels=PM.edgeLabels(ring,true,PM.edgeLengths(ring,true,f),{k:1,font:12});
  assert.equal(labels.length,4);
  for(const l of labels){assert.ok(!PM.insideOrOn([l.x,l.y],ring),`label ${l.i} is outside`);assert.ok(l.angle>-90&&l.angle<=90,`label ${l.i} is upright (${l.angle})`);assert.equal(l.mode,'full');assert.equal(l.text,`200'-0"`)}
 }
 // a right-to-left edge reads left to right; a straight-down edge reads bottom to top
 const [top]=PM.edgeLabels([[300,100],[100,100],[100,300]],false,[1,1],{k:1});assert.equal(top.angle,0);
 const [down]=PM.edgeLabels([[100,100],[100,300]],false,[1],{k:1});assert.equal(down.angle,90);
});
test('a short edge on screen gets whole feet, then no label (the panel still lists it); zooming in brings it back',()=>{
 const f=image(10,100),ring=[[0,0],[1000,0],[1000,50],[0,50]],lengths=PM.edgeLengths(ring,true,f);
 let labels=PM.edgeLabels(ring,true,lengths,{k:1,font:12});
 assert.deepEqual(labels.map(l=>l.mode),['full','short','full','short'],'50 px fits 5\' but not 5\'-0"');
 assert.equal(labels[1].text,`5'`);
 labels=PM.edgeLabels(ring,true,lengths,{k:4,font:12});assert.deepEqual(labels.map(l=>l.mode),['full','hidden','full','hidden'],'zoomed out: 12.5 screen px fits nothing');
 assert.equal(labels[1].text,'');
 labels=PM.edgeLabels(ring,true,lengths,{k:0.5,font:12});assert.deepEqual(labels.map(l=>l.mode),['full','full','full','full'],'zoomed in');
 assert.equal(labels[1].text,`5'-0"`);assert.equal(labels[1].words,'5 feet 0 inches');
});
test('the area label sits inside even a concave ring',()=>{
 const u=[[0,0],[300,0],[300,300],[200,300],[200,100],[100,100],[100,300],[0,300]];// a U: its centroid is in the notch
 const p=PM.labelPoint(u);assert.ok(PM.insideOrOn(p,u));
 assert.deepEqual(PM.labelPoint([[0,0],[10,0],[10,10],[0,10]]),[5,5]);
});

/* ---------- typed lengths: the far corner moves along the edge ---------- */
test('a typed length gives the edge exactly that length in image space; the next edge stretches',()=>{
 const f=image(),ring=[[0,0],[100,0],[100,200],[0,200]];
 const out=PM.setEdgeLength(ring,0,30,true,f);
 assert.deepEqual(out[1],[150,0]);assert.deepEqual(out[0],ring[0]);assert.deepEqual(out.slice(2),ring.slice(2));
 assert.equal(PM.edgeLengths(out,true,f)[0],30);
 // the closing edge moves the first corner
 const closing=PM.setEdgeLength(ring,3,50,true,f);assert.equal(PM.edgeLengths(closing,true,f)[3],50);assert.deepEqual(closing[0],[0,-50]);
 // shorter, on a slanted edge: along the edge, not towards an axis
 const slant=PM.setEdgeLength([[0,0],[300,400]],0,50,false,f);assert.ok(Math.abs(slant[1][0]-150)<1e-9&&Math.abs(slant[1][1]-200)<1e-9);
});
test('a typed length on an aerial lands on the server’s length to well under an inch',()=>{
 for(const [i,ft] of [[0,54.25],[1,100],[2,12+7/12],[4,300]]){
  const out=PM.setEdgeLength(RING,i,ft,true,frame),got=PM.edgeLengths(out,true,frame)[i];
  assert.ok(Math.abs(got-ft)<0.001,`edge ${i}: ${got} for ${ft}`);assert.equal(PM.ftIn(got),PM.ftIn(ft));
  // and the body the tool sends computes it the same way
  const body=PM.measurementBody({unit:'LF',shapes:[{kind:'line',path:[out[i],out[(i+1)%out.length]]}],view:VIEW});
  assert.ok(Math.abs(Number(PM.measure(body).length_ft)-ft)<0.006);
 }
 assert.throws(()=>PM.setEdgeLength([[0,0],[0,0],[1,1]],0,5,true,frame),/no length/);
 assert.throws(()=>PM.setEdgeLength(RING,9,5,true,frame),/No such edge/);
 assert.throws(()=>PM.setEdgeLength([[0,0],[1,1]],1,5,false,frame),/No such edge/,'a line has no closing edge');
});

/* ---------- editing keeps the ring simple ---------- */
test('dragging a corner across the shape makes it cross itself, which the server’s check refuses',()=>{
 const f=image(),ring=[[0,0],[100,0],[100,100],[0,100]],body=r=>({unit:'SF',space:'image',shapes:[{kind:'area',ring:r}],scale:{points:[[0,0],[100,0]],length:20,unit:'ft'},image:{source:'x'}});
 assert.deepEqual(PM.problems(body(PM.movePoint(ring,2,[120,130]))),[]);
 const crossed=PM.movePoint(ring,2,[-20,50]);// its edge from corner 1 now crosses the closing edge
 assert.deepEqual(PM.problems(body(crossed)),['measurement.shapes[0].ring crosses or touches itself (two of its edges meet)']);
 assert.equal(PM.ringProblem(crossed),'crosses or touches itself (two of its edges meet)');
 const onto=PM.movePoint(ring,1,[0,0]);assert.match(PM.problems(body(onto))[0],/repeated corner/);
 // adding a corner in an edge's middle and dragging it out keeps the shape simple
 const added=PM.insertPoint(ring,0,[50,-20]);assert.deepEqual(added,[[0,0],[50,-20],[100,0],[100,100],[0,100]]);assert.deepEqual(PM.problems(body(added)),[]);
 assert.deepEqual(PM.removePoint(added,1),ring);assert.equal(PM.minPoints(true),3);assert.equal(PM.minPoints(false),2);
 assert.ok(!PM.onPicture([-1,5],100,100)&&PM.onPicture([100,100],100,100));
});
test('a corner snaps to the shape’s corners and the parcel’s corners first, then the parcel’s edges, within the radius',()=>{
 const parcel=[[0,0],[500,0],[500,500],[0,500]],corners=[[200,200],...parcel],segments=PM.ringSegments([parcel]);
 assert.deepEqual(PM.snapPoint([203,198],{corners,segments,radius:8}),{point:[200,200],d:Math.hypot(3,2),to:'corner'});
 assert.deepEqual(PM.snapPoint([250,5],{corners,segments,radius:8}).point,[250,0]);assert.equal(PM.snapPoint([250,5],{corners,segments,radius:8}).to,'edge');
 assert.deepEqual(PM.snapPoint([4,3],{corners,segments,radius:8}).point,[0,0],'a corner wins over the edges it is on');
 assert.deepEqual(PM.snapPoint([250,50],{corners,segments,radius:8}),{point:[250,50],d:0,to:null},'nothing near: no snap');
 assert.equal(PM.snapPoint([250,9],{corners,segments,radius:8}).to,null,'9 px is outside an 8 px radius');
});
test('undo and redo walk the edits back and forth; a new edit drops the redo',()=>{
 const t={shapes:[],draft:[]};
 PM.remember(t);t.draft.push([1,1]);PM.remember(t);t.draft.push([2,2]);PM.remember(t);t.shapes.push({kind:'line',path:t.draft});t.draft=[];
 assert.equal(t.shapes.length,1);
 assert.ok(PM.undoTrace(t));assert.deepEqual([t.shapes.length,t.draft.length],[0,2]);
 assert.ok(PM.undoTrace(t));assert.deepEqual(t.draft,[[1,1]]);
 assert.ok(PM.redoTrace(t));assert.deepEqual(t.draft,[[1,1],[2,2]]);
 PM.remember(t);t.draft.push([3,3]);assert.equal(PM.redoTrace(t),false,'a new edit clears the redo');
 assert.ok(PM.undoTrace(t));assert.ok(PM.undoTrace(t));assert.ok(PM.undoTrace(t));assert.equal(PM.undoTrace(t),false);assert.deepEqual(t.draft,[]);
 // the scale is part of the history
 const s={shapes:[],draft:[],scale:{points:[[0,0],[10,0]],length:'7',unit:'ft'}};PM.remember(s);s.scale.points=[[0,0],[20,0]];PM.undoTrace(s);assert.deepEqual(s.scale.points,[[0,0],[10,0]]);
});

/* ---------- the record read back, and redrawn ---------- */
test('a stored measurement becomes a body again (its computed keys dropped) and redraws on any georeferenced view',()=>{
 const body=PM.measurementBody({unit:'SF',shapes:[{kind:'area',ring:RING}],view:VIEW}),record={...body,...PM.measure(body),original:{...body,...PM.measure(body),drawn_by:'model:x@1'}};
 const back=PM.bodyOfRecord(record);
 assert.deepEqual(Object.keys(back).sort(),['image','original','shapes','space','unit']);assert.deepEqual(Object.keys(back.original).sort(),['drawn_by','image','shapes','space','unit']);
 assert.deepEqual(PM.problems(back),[]);
 const px=PM.pixelShapes(back,VIEW);for(let i=0;i<RING.length;i++)for(const k of [0,1])assert.ok(Math.abs(px.shapes[0].ring[i][k]-RING[i][k])<0.01);
 // the wide view (another pixel size) draws the same ground
 const wide={...VIEW,id:'w',pixel_size_m:0.2447},onWide=PM.pixelShapes(back,wide).shapes[0].ring;
 assert.ok(Math.abs((onWide[1][0]-onWide[0][0])-(RING[1][0]-RING[0][0])*0.0841/0.2447)<0.01);
 // an image body is scaled from the frame it was drawn in
 const img={unit:'SF',space:'image',shapes:[{kind:'area',ring:[[0,0],[100,0],[100,100]]}],scale:{points:[[0,0],[100,0]],length:5,unit:'ft'},image:{source:'p'},view:{kind:'document',id:'d',width:2000,height:1000}};
 assert.deepEqual(PM.pixelShapes(img,null,{width:1000,height:500}).shapes[0].ring,[[0,0],[50,0],[50,50]]);
 assert.equal(PM.pixelShapes({},null),null);assert.equal(PM.pixelShapes({...back},null),null,'lon/lat needs a georeferenced view');
});
test('an edit keeps the first original: a model’s draft stays the original through every later edit',()=>{
 const draft={unit:'SF',space:'image',shapes:[{kind:'area',ring:[[0,0],[100,0],[100,100]]}],scale:{points:[[0,0],[100,0]],length:5,unit:'ft'},image:{source:'p'},drawn_by:'model:x@1',verified:false};
 const first=PM.withProvenance({...draft,shapes:[{kind:'area',ring:[[0,0],[120,0],[100,100]]}]},{verified:true,replaces:draft,view:{kind:'document',id:'d'}});
 assert.equal(first.drawn_by,'engineer');assert.equal(first.verified,true);assert.deepEqual(first.original,draft);
 const second=PM.withProvenance({...first,shapes:[{kind:'area',ring:[[0,0],[130,0],[100,100]]}]},{verified:true,replaces:first});
 assert.deepEqual(second.original,draft,'not the first edit');assert.ok(!('original' in second.original));
 assert.deepEqual(PM.problems(second),[]);
 const r=PM.measure(second);assert.equal(r.original.quantity,PM.measure(draft).quantity);assert.equal(r.original.drawn_by,'model:x@1');
 assert.equal(PM.formula(r,{source:'p',drawn_by:'engineer',verified:true}).endsWith('; verified by the engineer; replaces 12.50 SF drafted by Basis'),true);
 assert.ok(PM.sameShapes(draft,{...draft,drawn_by:'engineer'}));assert.ok(!PM.sameShapes(draft,first));
 const cmp=PM.edgeComparison([10,20,30],[10,21,30]);assert.deepEqual(cmp.map(c=>c.change),[0,-1,0]);assert.equal(PM.edgeComparison([1,2],[1,2,3]),null);
});
test('a photo’s body: image space, the scale, the document, the frame it was drawn in',()=>{
 const body=PM.imageBody({unit:'LF',shapes:[{kind:'line',path:[[10.12345,20],[410,20]]}],scale:{points:[[0,0],[200,0]],length:'34.5',unit:'in'},doc:{id:'5a310001-0000-4000-8000-000000000002',filename:'kitchen.webp'}});
 assert.equal(body.space,'image');assert.deepEqual(body.shapes[0].path[0],[10.123,20]);assert.equal(body.image.document_id,'5a310001-0000-4000-8000-000000000002');
 assert.match(body.image.source,/kitchen\.webp/);
 assert.deepEqual(PM.viewRef({kind:'document',id:'d',label:'kitchen.webp'},{width:1600,height:1200}),{kind:'document',id:'d',label:'kitchen.webp',width:1600,height:1200});
 assert.deepEqual(PM.viewRef({id:'pub',source_id:'naip',year:2022}),{kind:'public',id:'pub',label:'2022'});
 assert.deepEqual(PM.viewRef(VIEW),{kind:'capture',id:'v',label:'aerial-close'});
 assert.ok(PM.KNOWN_LENGTHS.some(k=>k.length===80&&/Door/.test(k.label))&&PM.KNOWN_LENGTHS.some(k=>k.length===34.5)&&PM.KNOWN_LENGTHS.some(k=>k.length===36&&/Counter/.test(k.label)));
});

/* ---------- the provenance keys, checked in the browser with the server's words ---------- */
test('the browser refuses the provenance the server refuses, in the same words (test_traced_provenance)',()=>{
 const base={unit:'SF',space:'image',shapes:[{kind:'area',ring:[[0,0],[100,0],[100,200],[0,200]]}],scale:{points:[[0,0],[100,0]],length:20,unit:'ft'},image:{source:'kitchen photo',captured_on:'2020-04-18'}};
 const one=extra=>PM.problems({...base,...extra});
 assert.deepEqual(one({drawn_by:'someone'}),["measurement.drawn_by must be engineer or model:<name>@<version> (letters, digits and . _ - / +), got 'someone'"]);
 assert.deepEqual(one({drawn_by:5}),['measurement.drawn_by must be engineer or model:<name>@<version> (letters, digits and . _ - / +), got 5']);
 assert.deepEqual(one({drawn_by:'model:sam3@1'}),[]);assert.deepEqual(one({drawn_by:'engineer'}),[]);
 assert.deepEqual(one({verified:'yes'}),["measurement.verified must be true or false, got 'yes'"]);
 assert.deepEqual(one({verified:1}),['measurement.verified must be true or false, got 1']);
 assert.deepEqual(one({view:{kind:'street',id:'x'}}),["measurement.view.kind must be capture, public or document, got 'street'"]);
 assert.deepEqual(one({view:{kind:'capture'}}),['measurement.view must name the picture by its id or its label']);
 assert.deepEqual(one({view:{kind:'capture',id:'x',zoom:2}}),['measurement.view has unknown keys: zoom']);
 assert.deepEqual(one({view:{kind:'document',id:'p',width:1600}}),['measurement.view takes width and height together (the pixel frame of the points)']);
 assert.deepEqual(one({view:{kind:'document',id:'p',width:0,height:10}}),['measurement.view.width must be above 0 and at most 100000, got 0']);
 assert.deepEqual(one({view:{kind:'capture',id:'a\u0000b'}}),["measurement.view.id must be text of 1 to 200 characters, got 'a\\x00b'"]);
 assert.deepEqual(one({colour:'red'}),['measurement has unknown keys: colour']);
 assert.deepEqual(one({original:{...base,original:base}}),['measurement.original.original cannot hold an original of its own: an edit keeps the first original only']);
 assert.deepEqual(one({original:{...base,shapes:[{kind:'area',ring:[[0,0],[100,100],[100,0],[0,100]]}]}}),['measurement.original.shapes[0].ring crosses or touches itself (two of its edges meet)']);
 assert.deepEqual(one({original:'draft'}),['measurement.original must be an object (unit, space, shapes, image)']);
 // and none of it changes the quantity
 assert.equal(PM.measure({...base,drawn_by:'model:x@1',verified:false,original:base}).quantity,PM.measure(base).quantity);
});
test('the formula reads as the server’s: a model draft says Basis, a verified mark and what it replaced are said',()=>{
 const base={unit:'SF',space:'image',shapes:[{kind:'area',ring:[[0,0],[100,0],[100,200],[0,200]]}],scale:{points:[[0,0],[100,0]],length:20,unit:'ft'},image:{source:'kitchen photo',captured_on:'2020-04-18'}};
 // traced.formula on the same bodies (test_traced_provenance)
 assert.equal(PM.formula(PM.measure(base),{source:'kitchen photo',captured_on:'2020-04-18'}),'800.00 SF traced by the engineer on kitchen photo (captured 2020-04-18), 1 area of 4 corners, scale 0.2 ft per pixel; expected error up to 7.00 %');
 const edit={...base,shapes:[{kind:'area',ring:[[0,0],[110,0],[110,200],[0,200]]}],drawn_by:'engineer',verified:true,original:{...base,drawn_by:'model:sam3@1',verified:false}};
 assert.equal(PM.formula(PM.measure(edit),{source:'kitchen photo',captured_on:'2020-04-18',drawn_by:'engineer',verified:true}),'880.00 SF traced by the engineer on kitchen photo (captured 2020-04-18), 1 area of 4 corners, scale 0.2 ft per pixel; expected error up to 6.82 %; verified by the engineer; replaces 800.00 SF drafted by Basis');
 assert.match(PM.formula(PM.measure(base),{source:'k',drawn_by:'model:sam3@1'}),/^800\.00 SF drafted by Basis on k/);
});

/* ---------- what can be measured ---------- */
test('measuring widens to photos for any component with an area or a length; the aerial answer is unchanged',()=>{
 const counter={label:'countertop',room:{room_type:'kitchen'},takeoff:{unit:'LF',quantity:'24'},review_reasons:[]};
 const floor={label:'hardwood_floor',room:{room_type:'living_room'},takeoff:{unit:'SF'},review_reasons:['input:room']};
 const fence={label:'fence',room:{room_type:'exterior'},takeoff:{unit:'EA'},cost:{trail:{base:{unit:'LF'}}},review_reasons:['cross:COST_NEEDS_QUANTITY']};
 const sink={label:'sink',room:{room_type:'kitchen'},takeoff:{unit:'EA'},review_reasons:[]};
 const wiring={label:null,room:null,takeoff:{unit:'SF'},review_reasons:['unpriced']};
 const c=PM.measureOffer(counter);assert.deepEqual([c.aerial,c.photo,c.photoNeeded,c.any,c.anyNeeded],[false,true,false,true,false]);
 const fl=PM.measureOffer(floor);assert.deepEqual([fl.aerial,fl.photo,fl.photoNeeded,fl.anyNeeded,fl.unit],[false,true,true,true,'SF']);
 const fe=PM.measureOffer(fence);assert.deepEqual([fe.aerial,fe.needed,fe.photo,fe.photoNeeded,fe.anyNeeded],[true,true,true,false,true]);
 for(const a of [counter,floor,fence,sink,wiring]){const n=PM.needsMeasurement(a),o=PM.measureOffer(a);assert.deepEqual([o.needed,o.offer,o.reason,o.unit],[n.needed,n.offer,n.reason,n.unit])}
 assert.equal(PM.measureOffer(sink).any,false);assert.equal(PM.measureOffer(wiring).any,false,'a building-wide row has no photo of its own');
 const stored={shapes:[{kind:'area',ring:[[0,0],[1,0],[1,1]]}]};assert.equal(PM.measureOffer({...counter,takeoff:{...counter.takeoff,inputs:{measurement:stored}}}).stored,stored);
 assert.equal(PM.measureOffer({...counter,takeoff:{...counter.takeoff,inputs:{measurement:'x'}}}).stored,null);
});
