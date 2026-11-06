const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const P=require('../room-placement.js');
function harness({unassigned=false,editable=true,roomCreate=true,second=false}={}){
 const data={items:[{id:'k',name:'Kitchen',room_type:'kitchen',floor:'1',photos:[{document_id:'p',filename:'Kitchen.jpg'}]},...(second?[{id:'d',name:'Dining',room_type:'dining_room',floor:'1',photos:[{document_id:'q',filename:'Dining.jpg'}]}]:[])],unassigned_photos:unassigned?[{document_id:'u',filename:'Unknown.jpg'}]:[]};
 const state={study:{id:'study',revision:1,editable},corrections:{rooms_review:new Map()}},dialogs=[],messages=[];let saved=0;
 const Live={state,caps:{roomCreate},gates:{componentsGate:()=>true},gateOpen:()=>state.study.editable,roomsEntry:()=>({done:true,data}),withStudy:f=>f(state.study),steps:()=>'',labelName:v=>v,route:()=> '#photo',photo:(id,name)=>`<img alt="${name}">`,actions:{},render:()=>context.rooms(),saveRoomChanges:()=>saved++};
 const context=vm.createContext({BasisLive:Live,BasisRoomPlacement:P,rooms:()=>'',root:{addEventListener(){},querySelector:()=>null,querySelectorAll:()=>[]},window:{addEventListener(){}},document:{addEventListener(){}},esc:String,icon:()=>'',toast:v=>messages.push(v),closeBtn:'',crypto:{randomUUID:()=> 'new'},CSS:{escape:v=>v},matchMedia:()=>({matches:true}),modal:(title,body,actions)=>{
  const fields={};const dialog={title,body,actions,removed:false,querySelector(selector){return fields[selector]||=( {value:'',focus(){}} )},remove(){this.removed=true}};dialogs.push(dialog);return dialog;
 }});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../live/room-placement.js'),'utf8'),context);
 return {Live,state,data,dialogs,messages,render:()=>context.rooms(),get saved(){return saved}};
}
test('unmatched photos prevent approval until placed and saved, while undo restores the blocker',()=>{
 const h=harness({unassigned:true});assert.match(h.render(),/Photos needing placement/);assert.match(h.render(),/data-value="rooms_review" disabled/);assert.equal(h.Live.roomPlacementReady(),false);
 h.Live.actions['placement-move'](null,'u');const d=h.dialogs.at(-1);d.querySelector('#placement-target').value='k';d.querySelector('#placement-confirm').onclick();
 assert.equal(h.state.placement.draft.unassigned.length,0);assert.equal(h.Live.roomPlacementReady(),false);assert.match(h.render(),/Unsaved room changes/);assert.match(h.render(),/placement-decision is-attention/);
 h.Live.actions['placement-undo']();assert.equal(h.state.placement.draft.unassigned.length,1);assert.equal(h.state.corrections.rooms_review.size,0);
});
test('create from the move dialog (with room_create offered) emits a create and a move, never a room delete; save needs explicit confirmation',()=>{
 const h=harness();h.render();h.Live.actions['placement-move'](null,'p');let d=h.dialogs.at(-1);d.querySelector('#placement-target').value='new';d.querySelector('#placement-confirm').onclick();
 d=h.dialogs.at(-1);d.querySelector('#placement-name').value='Office';d.querySelector('#placement-type').value='office';d.querySelector('#placement-floor').value='2';d.querySelector('#placement-confirm').onclick();
 assert.deepEqual([...h.state.corrections.rooms_review.values()].map(c=>c.entity_type),['room_create','photo_room']);
 assert.equal(h.state.placement.draft.rooms[0].photos[0].document_id,'p');h.Live.actions['placement-save']();assert.equal(h.saved,0);h.dialogs.at(-1).querySelector('#placement-confirm').onclick();assert.equal(h.saved,1);
});
test('completed studies expose photos but no editing or approval, and action handlers refuse mutations',()=>{
 const h=harness({editable:false});const html=h.render();assert.doesNotMatch(html,/Drag photos into the right room/);assert.doesNotMatch(html,/placement-readonly/);assert.doesNotMatch(html,/draggable="true"|data-live="placement-create"|data-live="approve"/);
 for(const name of ['placement-create','placement-edit','placement-remove','placement-move','placement-save','placement-undo'])h.Live.actions[name](null,'k');
 assert.equal(h.dialogs.length,0);assert.equal(h.state.corrections.rooms_review.size,0);assert.equal(h.saved,0);
});
test('a dialog opened for an old revision cannot apply changes after the revision changes',()=>{
 const h=harness();h.render();h.Live.actions['placement-create']();const d=h.dialogs.at(-1);h.state.study.revision=2;d.querySelector('#placement-name').value='Office';d.querySelector('#placement-confirm').onclick();assert.equal(d.removed,true);assert.equal(h.state.placement.draft.rooms.length,1);assert.equal(h.state.corrections.rooms_review.size,0);
});
test('a lone room offers no merge, and a merge without a target never loses photos',()=>{
 const h=harness();assert.doesNotMatch(h.render(),/data-live="placement-remove"/);h.Live.actions['placement-remove'](null,'k');assert.equal(h.dialogs.length,0);assert.equal(h.state.corrections.rooms_review.size,0);
 const two=harness({second:true});assert.match(two.render(),/Merge…/);two.Live.actions['placement-remove'](null,'k');two.dialogs.at(-1).querySelector('#placement-confirm').onclick();
 assert.equal(two.state.placement.draft.rooms.length,2);assert.equal(two.state.corrections.rooms_review.size,0);assert.match(two.messages.at(-1),/Choose the room to merge into/);
});
test('merging sends one merge_into correction and moves the photos with it',()=>{
 const h=harness({second:true});h.render();h.Live.actions['placement-remove'](null,'k');const d=h.dialogs.at(-1);d.querySelector('#placement-target').value='d';d.querySelector('#placement-confirm').onclick();
 assert.deepEqual(h.state.placement.draft.rooms.map(r=>r.id),['d']);assert.deepEqual(h.state.placement.draft.rooms[0].photos.map(p=>p.document_id).sort(),['p','q']);
 assert.deepEqual([...h.state.corrections.rooms_review.values()].map(c=>[c.entity_type,c.entity_id,c.after]),[['room','k',{merge_into:'d'}]]);
});
test('without the room_create capability there is no New room button and no create option when moving',()=>{
 const h=harness({roomCreate:false,second:true});assert.doesNotMatch(h.render(),/data-live="placement-create"/);
 h.Live.actions['placement-move'](null,'p');assert.doesNotMatch(h.dialogs.at(-1).body,/Create a new room/);
});
test('a renamed room shows the AI name struck through and Reset to AI restores it with no correction left',()=>{
 const h=harness();h.render();h.Live.actions['placement-edit'](null,'k');const d=h.dialogs.at(-1);d.querySelector('#placement-name').value='Galley kitchen';d.querySelector('#placement-type').value='kitchen';d.querySelector('#placement-floor').value='1';d.querySelector('#placement-confirm').onclick();
 const html=h.render();assert.match(html,/<s class="live-ai">Kitchen<\/s> Galley kitchen/);assert.match(html,/Edited by you · pending/);assert.match(html,/data-live="placement-reset"/);
 assert.equal(h.state.corrections.rooms_review.size,1);
 h.Live.actions['placement-reset'](null,'k');assert.equal(h.state.placement.draft.rooms[0].name,'Kitchen');assert.equal(h.state.corrections.rooms_review.size,0);assert.doesNotMatch(h.render(),/live-ai/);
});
