const {test}=require('node:test');const assert=require('node:assert/strict');
const M=require('../live/manual-add.js');const Rle=require('../live/rle.js');
const name=l=>l.replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());
// A 10 x 10 mask with rows r0..r1-1 and columns c0..c1-1 set (row-major, zeros first).
function mask(r0,r1,c0,c1){const bits=[];for(let r=0;r<10;r++)for(let c=0;c<10;c++)bits.push(r>=r0&&r<r1&&c>=c0&&c<c1?1:0);
 const counts=[];let cur=0,run=0;for(const b of bits){if(b!==cur){counts.push(run);run=0;cur=b}run++}counts.push(run);return {height:10,width:10,counts}}
const item=(id,label,score,m,box,extra={})=>({id,label,score,mask:m,box,counted:score===null||score>=0.5,source:score===null?'manual':'model',...extra});

test('the candidates under a tap are those of the label covering it, highest score first',()=>{
 const run=item('run','kitchen_cabinet',0.8,mask(5,10,0,10),{x1:0,y1:.5,x2:1,y2:1});
 const unit=item('unit','kitchen_cabinet',0.4,mask(5,10,0,3),{x1:0,y1:.5,x2:.3,y2:1});
 const door=item('door','interior_door',0.9,mask(0,10,0,10),{x1:0,y1:0,x2:1,y2:1});
 const box=item('box','kitchen_cabinet',null,null,{x1:0,y1:.5,x2:.2,y2:.9});
 const under=M.candidatesUnder([unit,door,run,box],'kitchen_cabinet',0.1,0.7,Rle);
 assert.deepEqual(under.map(i=>i.id),['box','run','unit']);// a manual box scores 1, as in the API
 assert.deepEqual(M.candidatesUnder([unit,run],'kitchen_cabinet',0.5,0.2,Rle),[]);
 assert.deepEqual(M.candidatesUnder([run],'window',0.1,0.7,Rle),[]);
 assert.deepEqual(M.candidatesUnder([unit,run],'kitchen_cabinet',0.5,0.7,Rle).map(i=>i.id),['run']);
});
test('cycling wraps both ways and tolerates an empty stack',()=>{
 assert.equal(M.cycle(0,3,1),1);assert.equal(M.cycle(2,3,1),0);assert.equal(M.cycle(0,3,-1),2);assert.equal(M.cycle(0,0,1),0);
});
test('the outline text and the message say where an outline came from',()=>{
 const o=extra=>({label:'light_fixture',source:'manual',mask:{},...extra});
 assert.equal(M.outlineText(o({mask_source:'sam_box_prompt'})),'Outline from SAM 3 (your box)');
 assert.equal(M.outlineText(o({mask_source:'candidate'})),'Outline from SAM 3');
 assert.equal(M.outlineText(o({mask_source:'none',mask:null})),'Box only');
 assert.equal(M.outlineText({source:'model',mask:{}}),'Outline from SAM 3');
 assert.equal(M.addedMessage({created:true,object:o({mask_source:'sam_box_prompt'})},name),'Added a light fixture. Outline from SAM 3.');
 assert.equal(M.addedMessage({created:true,object:o({mask_source:'none',mask_reason:'SAM 3 did not answer within 30 s'})},name),'Added your box as a light fixture (box only: SAM 3 did not answer within 30 s).');
 assert.equal(M.addedMessage({created:false,object:o({})},name),'That light fixture is already counted.');
 assert.equal(M.isDuplicate({created:false,duplicate_of:'x',object:{}}),true);
 assert.equal(M.isDuplicate({created:true,duplicate_of:null,object:{}}),false);
});
test('suggestions become a count correction on the room’s row or a component to add',()=>{
 const room={id:'r1',name:'Kitchen',inventory:[
  {component_id:'c-added',label:'window',count:1,added:true},
  {component_id:'c1',label:'window',count:2,material:'vinyl',attachment:'permanent'},
  {component_id:'c2',label:'window',count:1}],
  suggested_counts:[{label:'window',inventory_count:4,photo_count:5,manual_count:2,suggested:5,document_id:'d'},
   {label:'light_fixture',inventory_count:0,photo_count:3,manual_count:3,suggested:3,document_id:'d'},
   {label:'toilet',inventory_count:1,photo_count:1,manual_count:0,suggested:1,document_id:'d'}]};
 const s=M.suggestions(room);
 assert.equal(M.raised(s.get('window')),true);assert.equal(M.raised(s.get('toilet')),false);assert.equal(M.raised(undefined),false);
 assert.equal(M.rowFor(room,'window').component_id,'c1');// not the engineer's added row
 assert.deepEqual(M.missingLabels(room).map(x=>x.label),['light_fixture']);
 const c=M.countCorrection(room,room.inventory[1],5,{after:{material:'wood'},reason:'checked'},name);
 assert.deepEqual([c.key,c.entity_type,c.entity_id],['component:c1','component','c1']);
 assert.deepEqual(c.after,{material:'wood',count:5});assert.equal(c.before.count,2);assert.equal(c.reason,'checked');
 const add=M.roomComponentCorrection(room,{label:'light_fixture',count:3,material:'metal',attachment:null},name);
 assert.deepEqual([add.key,add.entity_type,add.entity_id],['room_component:r1:light_fixture','room_component','r1']);
 assert.deepEqual(add.after,{label:'light_fixture',count:3,material:'metal'});
});
test('a count is a whole number from 1 to the API maximum',()=>{
 assert.equal(M.validCount('3'),3);assert.equal(M.validCount(M.MAX_COUNT),M.MAX_COUNT);
 for(const bad of ['0','-1','2.5','',null,'x',M.MAX_COUNT+1])assert.equal(M.validCount(bad),null,String(bad));
 assert.ok(M.MATERIALS.includes('metal')&&!M.MATERIALS.includes('unknown'));assert.deepEqual(M.ATTACHMENTS,['permanent','removable','freestanding']);
});
