const {test}=require('node:test'),assert=require('node:assert/strict'),P=require('../room-placement.js');
const photo=id=>({document_id:id,filename:id+'.jpg'});
const room=(id,photos=[],name=id)=>({id,name,room_type:'other',floor:null,photos:photos.map(photo),inventory:[]});
const data=()=>({items:[room('a',['1','2']),room('b',['3'])],unassigned_photos:[photo('4')]});
test('an ambiguous room is still a room; only unmatched photos appear in the placement tray',()=>{
 const d=data();d.items[0].room_type='ambiguous';d.items.push(room('u',['5'],'Unassigned'));d.unassigned_photos.push(photo('5'),photo('1'));
 const draft=P.create(d);assert.equal(draft.rooms.length,2);assert.deepEqual(draft.unassigned.map(p=>p.document_id),['5','4']);
});
test('moving a photo changes only its room, preserves the source data, and emits the API contract',()=>{
 const d=data(),initial=P.create(d),next=P.move(initial,'1','b');assert.equal(d.items[0].photos.length,2);assert.equal(initial.rooms[0].photos.length,2);
 assert.equal(next.rooms[0].photos.length,1);assert.equal(next.rooms[1].photos.length,2);
 assert.deepEqual(P.corrections(d,next).map(c=>[c.entity_type,c.entity_id,c.before,c.after]),[['photo_room','1',{room_id:'a'},{room_id:'b'}]]);
});
test('last-photo move removes the source, retains every photo, sends only the move (the API has no room delete), and undo returns a clean diff',()=>{
 const d=data(),initial=P.create(d),next=P.move(initial,'3','a');assert.equal(next.rooms.length,1);assert.equal(next.rooms[0].photos.length,3);
 assert.deepEqual(P.corrections(d,next).map(c=>c.entity_type),['photo_room']);assert.deepEqual(P.corrections(d,P.clone(initial)),[]);
});
test('same-room, foreign-photo and missing-target drops are no-ops',()=>{
 const d=P.create(data());for(const [photo,target] of [['1','a'],['missing','b'],['1','absent']])assert.equal(P.move(d,photo,target),d);
});
test('placing an unmatched photo records a null original assignment and cannot duplicate it',()=>{
 const d=data(),next=P.move(P.create(d),'4','b');assert.equal(next.unassigned.length,0);assert.equal(next.rooms[1].photos.filter(p=>p.document_id==='4').length,1);
 assert.deepEqual(P.corrections(d,next)[0].before,{room_id:null});assert.equal(P.move(next,'4','b'),next);
});
test('create, edit and place produce a coherent correction batch with a draft destination id',()=>{
 const d=data();let next=P.create(d);next.rooms.push(room('new'));next=P.move(next,'3','new');next.rooms.find(r=>r.id==='new').name='Office';
 const edits=P.corrections(d,next);assert.equal(edits[0].entity_type,'room_create');assert.equal(edits[0].after.name,'Office');assert.equal(edits[1].after.room_id,'new');assert.equal(edits.length,2,'no room_delete: the emptied room leaves with its last photo');
});
test('removing a populated room requires a valid destination and never deletes uploads',()=>{
 const d=data(),draft=P.create(d);for(const target of [null,'a','missing'])assert.throws(()=>P.remove(draft,'a',target));
 const next=P.remove(draft,'a','b');assert.equal(next.rooms.length,1);assert.deepEqual(next.rooms[0].photos.map(p=>p.document_id).sort(),['1','2','3']);assert.equal(next.unassigned.length,1);
});
test('empty draft creation/removal cancels without a server operation',()=>{
 const d=data(),draft=P.create(d);draft.rooms.push(room('new'));assert.deepEqual(P.corrections(d,P.remove(draft,'new')),[]);
});
test('room name, type and floor edits include clearing an existing floor',()=>{
 const d=data();d.items[0].floor='2';const draft=P.create(d);Object.assign(draft.rooms[0],{name:'Kitchen',room_type:'kitchen',floor:null});
 assert.deepEqual(P.corrections(d,draft)[0].after,{name:'Kitchen',room_type:'kitchen',floor:null});
});

test('current unplaced responses keep unanalyzed photos visible and preserve analysis state',()=>{
 const d={items:[room('a',['1'])],unplaced:[{...photo('2'),analyzed:false},{...photo('3'),analyzed:true}]};
 const result=P.create(d);assert.deepEqual(result.unassigned.map(p=>[p.document_id,p.analyzed]),[['2',false],['3',true]]);
 assert.equal(P.corrections(d,result).length,0);
});
test('mixed legacy and current unplaced lists cannot duplicate already placed or loose photos',()=>{
 const d={items:[room('a',['1'])],unassigned_photos:[photo('2'),photo('1')],unplaced:[{...photo('2'),analyzed:false},photo('3')]};
 assert.deepEqual(P.create(d).unassigned.map(p=>p.document_id),['2','3']);
 assert.equal(P.create(d).unassigned[0].analyzed,false);
});

test('merging sends the API merge_into correction instead of a move per photo, follows chains, and refuses a bad target',()=>{
 const d={items:[room('a',['1','2']),room('b',['3']),room('c',['4'])]},draft=P.create(d);
 for(const target of [null,'a','missing'])assert.throws(()=>P.merge(draft,'a',target));
 const ab=P.merge(draft,'a','b');assert.deepEqual(ab.rooms.map(r=>r.id),['b','c']);assert.deepEqual(ab.rooms[0].photos.map(p=>p.document_id).sort(),['1','2','3']);
 assert.deepEqual(P.corrections(d,ab).map(c=>[c.entity_type,c.entity_id,c.after]),[['room','a',{merge_into:'b'}]]);
 assert.deepEqual(P.corrections(d,P.merge(ab,'b','c')).map(c=>[c.entity_type,c.entity_id,c.after]),[['room','a',{merge_into:'b'}],['room','b',{merge_into:'c'}]]);
 assert.deepEqual(P.corrections(d,P.move(ab,'1','c')).map(c=>[c.entity_type,c.entity_id,c.after]),[['room','a',{merge_into:'b'}],['photo_room','1',{room_id:'c'}]],'a photo moved on after its merge still says where it ends');
 assert.ok(!P.corrections(d,ab).some(c=>c.entity_type==='room_delete'));
});
