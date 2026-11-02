const {test}=require('node:test');const assert=require('node:assert/strict');
const G=require('../viewer-gestures.js');
function surface(){const listeners=new Map();return {clientHeight:600,clientWidth:800,scrollLeft:0,scrollTop:0,getBoundingClientRect:()=>({left:10,top:20}),listeners,addEventListener:(n,f,o)=>listeners.set(n,{f,o}),removeEventListener:n=>listeners.delete(n),emit(n,props={}){const e={deltaX:0,deltaY:0,deltaMode:0,clientX:100,clientY:150,prevented:false,preventDefault(){this.prevented=true},...props};listeners.get(n)?.f(e);return e}}}
test('Mac trackpad pinch zooms; ordinary scrolling and page zoom outside the surface are not hijacked',()=>{
 const el=surface(),calls=[],off=G.attach(el,{zoom:(...args)=>calls.push(args)});
 assert.equal(el.listeners.get('wheel').o.passive,false);
 assert.equal(el.emit('wheel',{deltaY:40}).prevented,false);assert.equal(calls.length,0);
 assert.equal(el.emit('wheel',{deltaY:-20,ctrlKey:true}).prevented,true);assert(calls[0][0]>1);assert.deepEqual(calls[0][1],{x:100,y:150});
 el.emit('wheel',{deltaY:20,ctrlKey:true});assert(Math.abs(calls[0][0]*calls[1][0]-1)<1e-12);
 off();assert.equal(el.listeners.size,0);el.emit('wheel',{ctrlKey:true});assert.equal(calls.length,2);
});
test('normalizes wheel units, handles horizontal scrolling and bounds extreme deltas',()=>{
 assert.deepEqual(G.wheelPixels({deltaX:2,deltaY:3,deltaMode:1}),{x:32,y:48});
 assert.deepEqual(G.wheelPixels({deltaX:1,deltaY:-1,deltaMode:2},600),{x:600,y:-600});
 assert.equal(G.factor({deltaY:0}),1);assert(Number.isFinite(G.factor({deltaY:-1e8})));assert(G.factor({deltaY:1e8})>0);
 const el=surface();let delta;G.attach(el,{zoom(){},pan:d=>{delta=d;return true}});assert(el.emit('wheel',{deltaX:30,deltaY:50}).prevented);assert.deepEqual(delta,{x:30,y:50});
});
test('Safari cumulative gesture scales are incremental and never doubled by a simultaneous wheel event',()=>{
 const el=surface(),calls=[];G.attach(el,{zoom:n=>calls.push(n)});
 assert(el.emit('gesturestart',{scale:1}).prevented);el.emit('gesturechange',{scale:1.2});el.emit('gesturechange',{scale:1.5});
 el.emit('wheel',{ctrlKey:true,deltaY:-50});assert.deepEqual(calls,[1.2,1.25]);
 el.emit('gesturechange',{scale:0});el.emit('gesturechange',{scale:NaN});assert.equal(calls.length,2);
 assert(el.emit('gestureend').prevented);el.emit('wheel',{ctrlKey:true,deltaY:-10});assert.equal(calls.length,3);
});
test('editing or unloaded surfaces do not change zoom and refused pan remains native',()=>{
 const el=surface();G.attach(el,{zoom:()=>assert.fail('Disabled zoom'),canZoom:()=>false,pan:()=>false});
 assert(!el.emit('wheel',{ctrlKey:true,deltaY:-20}).prevented);assert(!el.emit('wheel',{deltaY:20}).prevented);assert(!el.emit('gesturestart',{scale:1}).prevented);el.emit('gesturechange',{scale:2});
});
test('zoom anchoring keeps the same content point under the pointer, including centered content',()=>{
 const stage=surface();let rect={left:110,top:-80,width:400,height:600};const content={getBoundingClientRect:()=>rect};
 const a=G.anchor(content,{x:310,y:220});assert.equal(a.x,.5);assert.equal(a.y,.5);
 rect={left:10,top:-80,width:800,height:1200};G.restore(stage,content,a);
 assert.equal(stage.scrollLeft,100);assert.equal(stage.scrollTop,300);
 assert.equal(rect.left+a.x*rect.width-stage.scrollLeft,a.point.x);assert.equal(rect.top+a.y*rect.height-stage.scrollTop,a.point.y);
});
