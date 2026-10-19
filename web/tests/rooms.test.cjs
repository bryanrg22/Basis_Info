const {test}=require('node:test');const assert=require('node:assert/strict');const {groupPhotos}=require('../room-model.js');
const photos=[['a','Kitchen / 01','Kitchen'],['b','Kitchen / 02','Kitchen'],['c','Living room / 01','Living room']];
test('room sections retain every source index exactly once and keep photos in their room',()=>{
 const groups=groupPhotos(photos);assert.deepEqual(groups.map(g=>[g.name,g.photos.length]),[['Kitchen',2],['Living room',1],['Bedroom',0],['Exterior',0]]);
 assert.deepEqual(groups.flatMap(g=>g.photos.map(p=>p.index)),[0,1,2]);assert.equal(groups[0].photos[1].photo,photos[1]);
});
test('moving a photo updates both room groups without losing the original identity',()=>{
 const groups=groupPhotos(photos,{0:'Bedroom',2:'Unassigned'});assert.deepEqual(groups.find(g=>g.name==='Kitchen').photos.map(p=>p.index),[1]);assert.equal(groups.find(g=>g.name==='Bedroom').photos[0].index,0);assert.equal(groups.find(g=>g.name==='Unassigned').photos[0].index,2);assert.equal(groups.reduce((n,g)=>n+g.photos.length,0),3);
});
