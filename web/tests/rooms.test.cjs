const {test}=require('node:test');const assert=require('node:assert/strict');const {groupPhotos}=require('../room-model.js');
const photos=[['a','Kitchen / 01','Kitchen'],['b','Kitchen / 02','Kitchen'],['c','Living room / 01','Living room']];
test('room sections retain every source index exactly once and keep photos in their room',()=>{
 const groups=groupPhotos(photos);assert.deepEqual(groups.map(g=>[g.name,g.photos.length]),[['Kitchen',2],['Living room',1],['Bedroom',0],['Exterior',0]]);
 assert.deepEqual(groups.flatMap(g=>g.photos.map(p=>p.index)),[0,1,2]);assert.equal(groups[0].photos[1].photo,photos[1]);
});
test('moving a photo updates both room groups without losing the original identity',()=>{
 const groups=groupPhotos(photos,{0:'Bedroom',2:'Unassigned'});assert.deepEqual(groups.find(g=>g.name==='Kitchen').photos.map(p=>p.index),[1]);assert.equal(groups.find(g=>g.name==='Bedroom').photos[0].index,0);assert.equal(groups.find(g=>g.name==='Unassigned').photos[0].index,2);assert.equal(groups.reduce((n,g)=>n+g.photos.length,0),3);
});

const {componentCount}=require('../room-model.js');
test('summary counts inventory instances once, includes staged additions and preserves zero corrections',()=>{
 const room={id:'kitchen',photos:[{},{},{}],inventory:[{component_id:'a',count:2},{component_id:'b',count:4}],suggested_counts:[{suggested:20}]};
 assert.equal(componentCount(room),6);
 const edits=new Map([['component:a',{after:{count:0}}],['component:b',{after:{material:'wood'}}],['add',{entity_type:'room_component',entity_id:'kitchen',after:{count:3}}],['elsewhere',{entity_type:'room_component',entity_id:'bedroom',after:{count:50}}]]);
 assert.equal(componentCount(room,edits),7);assert.equal(room.inventory[0].count,2);
 edits.set('component:b',{after:{count:5}});assert.equal(componentCount(room,edits),8);
 assert.equal(componentCount({id:'empty'}),0);
});

const {componentSummary}=require('../room-model.js');
test('compact type statistics aggregate inventory rows and reflect pending label/count changes',()=>{
 const room={id:'r',inventory:[
  {component_id:'a',label:'window',count:2},{component_id:'b',label:'window',count:3},
  {component_id:'c',label:'light_fixture',count:4},{component_id:'d',label:'other',count:20},
  {component_id:'e',label:'door',count:1},{component_id:'f',label:'empty',count:0}
 ]};
 assert.deepEqual(componentSummary(room),{count:30,types:[{label:'window',count:5},{label:'light_fixture',count:4},{label:'door',count:1},{label:'other',count:20}]});
 const edits=new Map([
  ['component:a',{after:{label:'light_fixture',count:1}}],['component:b',{after:{count:0}}],
  ['add',{entity_type:'room_component',entity_id:'r',after:{label:'door',count:2}}],
  ['elsewhere',{entity_type:'room_component',entity_id:'other',after:{label:'window',count:99}}]
 ]);
 assert.deepEqual(componentSummary(room,edits),{count:28,types:[{label:'light_fixture',count:5},{label:'door',count:3},{label:'other',count:20}]});
 assert.equal(room.inventory[0].label,'window');assert.equal(room.inventory[1].count,3);
});
test('component summaries handle empty inventories and invalid quantities without phantom types',()=>{
 assert.deepEqual(componentSummary({}),{count:0,types:[]});
 assert.deepEqual(componentSummary({inventory:[{label:'invalid',count:-3},{label:'invalid',count:Infinity},{label:'invalid',count:'no'},{label:'window',count:'2'},{count:1}]}),{count:3,types:[{label:'window',count:2},{label:'other',count:1}]});
});
test('a row removed and pending send leaves the room summary; Reset to AI brings it back',()=>{
 const room={id:'r1',inventory:[{component_id:'a',label:'light_fixture',count:2},{component_id:'b',label:'chair',count:4}]};
 const pending=new Map([['component_remove:a',{entity_type:'component_remove',entity_id:'a',after:{}}]]);
 const s=componentSummary(room,pending);
 assert.equal(s.count,4);assert.deepEqual(s.types,[{label:'chair',count:4}]);
 pending.delete('component_remove:a');assert.equal(componentSummary(room,pending).count,6);
});
