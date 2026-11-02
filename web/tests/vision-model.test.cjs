const {test}=require('node:test');
const assert=require('node:assert/strict');
const V=require('../vision-model.js');
test('component choices match the published API taxonomy without duplicate values',()=>{
 const spec=require('../../docs/api/openapi.json');
 assert.deepEqual([...V.LABELS].sort(),[...spec.components.schemas.ComponentLabel.enum].sort());
 assert.equal(new Set(V.LABELS).size,V.LABELS.length);
});
const item=(id,label,box)=>({id,label,box,counted:true,score:.74});
test('selecting from either surface has the same state; clicking a type again clears it',()=>{
 const o=item('sink','sink',{x1:0,y1:0,x2:1,y2:1}),initial={focusLabel:null,selectedId:null,zoom:2};
 const fromPin=V.select(initial,o),fromRow=V.select(initial,o);
 assert.deepEqual(fromPin,fromRow);assert.equal(fromPin.zoom,2);assert.equal(fromPin.focusLabel,'sink');
 assert.deepEqual(V.toggleType(fromPin,'sink'),initial);assert.deepEqual(V.select(fromPin,null),initial);
 assert.equal(o.score,.74);
});
test('overlapping nested objects cycle without hiding other component types',()=>{
 const big=item('cabinet','cabinet',{x1:0,y1:0,x2:1,y2:1}),small=item('sink','sink',{x1:.4,y1:.4,x2:.6,y2:.6});
 const hits=V.under([big,small,{...small,id:'candidate',counted:false}],.5,.5);
 assert.deepEqual(hits.map(i=>i.id),['sink','cabinet']);assert.equal(V.cycle(hits,null).id,'sink');
 assert.equal(V.cycle(hits,'sink').id,'cabinet');assert.equal(V.cycle(hits,'cabinet').id,'sink');
 assert.equal(V.cycle([],null),null);assert.deepEqual(V.under([big],1.1,.5),[]);
});
test('dense pins stay separated by at least 36px and every item is reachable through overflow',()=>{
 for(const [width,height,n] of [[600,400,38],[90,90,38],[36,36,8]]){
  const anchors=Array.from({length:n},(_,i)=>({id:String(i),x:width/2,y:height/2})),pins=V.pins(anchors,width,height);
  const ids=pins.flatMap(p=>p.items?p.items.map(i=>i.id):[p.id]);assert.equal(new Set(ids).size,n);
  for(const p of pins){assert(p.x>=18&&p.x<=width-18);assert(p.y>=18&&p.y<=height-18)}
  for(let i=0;i<pins.length;i++)for(let j=i+1;j<pins.length;j++)assert(Math.hypot(pins[i].x-pins[j].x,pins[i].y-pins[j].y)>=36);
 }
 assert.deepEqual(V.pins([{id:'a',x:0,y:0}],0,0),[]);
});
test('fit contains portrait, panoramic and ordinary photos at laptop and tablet sizes',()=>{
 for(const [w,h] of [[700,420],[1024,768],[768,1024],[200,150]])for(const [nw,nh] of [[4000,3000],[3000,4000],[8000,1000]]){
  const f=V.fit(w,h,nw,nh);assert(f.width<=w+.0001&&f.height<=h+.0001);assert(f.x>=0&&f.y>=0);assert(Math.abs(f.width/f.height-nw/nh)<.0001);
 }
 assert.equal(V.fit(0,200,100,100).scale,0);assert.equal(V.fit(200,200,NaN,100).scale,0);
});
test('zoom keeps the point under the pointer stationary and clamps the range',()=>{
 const v={zoom:1,x:0,y:0},next=V.zoomAt(v,2,100,50);
 assert.equal((100-next.x)/next.zoom,100);assert.equal((50-next.y)/next.zoom,50);
 assert.equal(V.zoomAt(v,20,0,0).zoom,6);assert.equal(V.zoomAt(v,.1,0,0).zoom,1);
});
test('room totals use every inventory entry and pending count, type and missing-component corrections',()=>{
 const room={id:'r',inventory:[{component_id:'a',label:'sink',count:2},{component_id:'b',label:'sink',count:3}]};
 const pending=new Map();assert.equal(V.roomCount(room,'sink',pending),5);
 pending.set('component:a',{after:{count:4}});assert.equal(V.roomCount(room,'sink',pending),7);
 pending.set('component:b',{after:{label:'countertop',count:1}});assert.equal(V.roomCount(room,'sink',pending),4);
 pending.set('new',{entity_type:'room_component',entity_id:'r',after:{label:'sink',count:2}});assert.equal(V.roomCount(room,'sink',pending),6);
 assert.equal(V.roomCount(room,'countertop',pending),1);assert.equal(room.inventory[0].count,2);
});
test('polygon selection follows the drawn shape instead of its surrounding box',()=>{
 const triangle={...item('shape','sink',{x1:0,y1:0,x2:1,y2:1}),polygon:[{x:0,y:0},{x:1,y:0},{x:0,y:1}]};
 assert.equal(V.under([triangle],.1,.1).length,1);assert.equal(V.under([triangle],.8,.8).length,0);
});
