const {test}=require('node:test');const assert=require('node:assert/strict');
const Rle=require('../live/rle.js');
// Encoded by the backend's basis.vision.localize.Rle.encode (synthetic masks, no photos).
const cases=require('./fixtures/rle-cases.json');
test('decodes every backend-encoded mask pixel for pixel, row-major, zeros first',()=>{
 for(const [name,{mask,bits}] of Object.entries(cases))assert.deepEqual(Array.from(Rle.decode(mask)),bits,name);
});
test('a mask whose first pixel is set starts with an empty zero run',()=>{
 const {mask}=cases.first_pixel_set;assert.equal(mask.counts[0],0);assert.equal(Rle.decode(mask)[0],1);
});
test('refuses runs that do not cover the mask, or that are negative or fractional',()=>{
 assert.throws(()=>Rle.decode({width:2,height:2,counts:[1,2]}),/cover 3 pixels/);
 assert.throws(()=>Rle.decode({width:2,height:1,counts:[3,-1]}),/whole number/);
 assert.throws(()=>Rle.decode({width:2,height:1,counts:[1.5,0.5]}),/whole number/);
 assert.throws(()=>Rle.decode(null),/not a mask/);
});
test('stats give area, exclusive bounds and centroid; an empty mask has none',()=>{
 const s=Rle.stats(cases.rectangle.mask);
 assert.equal(s.area,30);assert.deepEqual(s.bounds,{x1:3,y1:2,x2:9,y2:7});assert.equal(s.cx,5.5);assert.equal(s.cy,4);assert.equal(s.fraction,30/120);
 assert.deepEqual(Rle.stats(cases.all_zero.mask),{area:0,fraction:0,cx:null,cy:null,bounds:null});
});
test('edges keep the outline of a filled shape and drop its interior',()=>{
 const {mask}=cases.rectangle,e=Rle.edges(mask),at=(x,y)=>e[y*mask.width+x];
 assert.equal(at(3,2),1);assert.equal(at(8,6),1);assert.equal(at(5,4),0);assert.equal(at(0,0),0);
 assert.equal(Array.from(e).reduce((a,b)=>a+b,0),2*6+2*5-4);
});
test('hit testing maps a normalized point onto mask pixels and rejects points outside the photo',()=>{
 const {mask}=cases.rectangle,bits=Rle.decode(mask);
 assert.equal(Rle.contains(mask,bits,5.5/12,4.5/10),true);assert.equal(Rle.contains(mask,bits,0.05,0.05),false);
 assert.equal(Rle.contains(mask,bits,-0.1,0.5),false);assert.equal(Rle.contains(mask,bits,0.5,1),false);
});
test('rgba colours set pixels only',()=>{
 const px=Rle.rgba(Uint8Array.from([0,1]),[10,20,30],200);assert.deepEqual(Array.from(px),[0,0,0,0,10,20,30,200]);
});
