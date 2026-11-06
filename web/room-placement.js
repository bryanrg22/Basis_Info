/* Room placement drafts. Pure projection/diff: the server's revision stays immutable. */
(function(scope){
 'use strict';
 const unassigned=r=>r.name.trim().toLowerCase()==='unassigned';
 const clone=value=>JSON.parse(JSON.stringify(value));
 function create(data){
  const rooms=data.items.filter(r=>!unassigned(r)).map(r=>({id:r.id,name:r.name,room_type:r.room_type,floor:r.floor??null,photos:[...r.photos],inventory:r.inventory||[]}));
  const placed=new Set(rooms.flatMap(r=>r.photos.map(p=>p.document_id)));
  const loose=new Map([...data.items.filter(unassigned).flatMap(r=>r.photos),...(data.unassigned_photos||[]),...(data.unplaced||[])].filter(p=>!placed.has(p.document_id)).map(p=>[p.document_id,p]));
  return {rooms,unassigned:[...loose.values()]};
 }
 function find(draft,doc){return draft.rooms.find(r=>r.photos.some(p=>p.document_id===doc))}
 function move(draft,doc,to){
  const next=clone(draft),target=next.rooms.find(r=>r.id===to),from=find(next,doc);
  const photo=from?.photos.find(p=>p.document_id===doc)||next.unassigned.find(p=>p.document_id===doc);
  if(!target||!photo||from===target)return draft;
  if(from)from.photos=from.photos.filter(p=>p.document_id!==doc);
  next.unassigned=next.unassigned.filter(p=>p.document_id!==doc);target.photos.push(photo);
  // Newly created empty rooms are kept as drop targets; a room emptied by a move leaves.
  if(from&&!from.photos.length)next.rooms=next.rooms.filter(r=>r!==from);
  return next;
 }
 function remove(draft,id,to){
  const source=draft.rooms.find(r=>r.id===id);if(!source)return draft;
  if(source.photos.length&&(!to||to===id||!draft.rooms.some(r=>r.id===to)))throw Error('Choose a destination for the room’s photos.');
  let next=clone(draft);for(const photo of source.photos)next=move(next,photo.document_id,to);
  next.rooms=next.rooms.filter(r=>r.id!==id);return next;
 }
 // Merging is the API's room correction {merge_into}: the room's photos and per-photo rows move to
 // the target (backEnd/basis/activities/apply_corrections.py). The draft records it so the
 // diff sends one merge instead of a photo move per photo.
 function merge(draft,id,into){
  const source=draft.rooms.find(r=>r.id===id);if(!source)return draft;
  if(!into||into===id||!draft.rooms.some(r=>r.id===into))throw Error('Choose the room to merge into.');
  let next=clone(draft);for(const photo of source.photos)next=move(next,photo.document_id,into);
  next.rooms=next.rooms.filter(r=>r.id!==id);next.merges=[...(draft.merges||[]),{id,into}];return next;
 }
 function corrections(data,draft){
  const result=[],original=new Map(data.items.map(r=>[r.id,r])),current=new Map(draft.rooms.map(r=>[r.id,r]));
  const add=(kind,id,before,after,title,summary)=>result.push({key:kind+':'+id,entity_type:kind,entity_id:id,before,after,reason:null,title,summary});
  // Merges first; a merged room's photos that end in its target travel with the merge.
  const into=new Map((draft.merges||[]).filter(m=>original.has(m.id)).map(m=>[m.id,m.into]));
  const end=id=>{const seen=new Set();while(into.has(id)&&!seen.has(id)){seen.add(id);id=into.get(id)}return id};
  for(const [id,target] of into){const r=original.get(id),t=current.get(end(target))||original.get(target);add('room',id,{name:r.name},{merge_into:target},r.name,'merge into '+(t?.name||'another room'))}
  for(const r of draft.rooms){
   const before=original.get(r.id),values={name:r.name,room_type:r.room_type,floor:r.floor};
   if(!before)add('room_create',r.id,null,values,r.name,'create room');
   else {const after=Object.fromEntries(Object.entries(values).filter(([k,v])=>(before[k]??null)!==v));if(Object.keys(after).length)add('room',r.id,{name:before.name,room_type:before.room_type,floor:before.floor??null},after,r.name,'update room details')}
   for(const photo of r.photos){const from=data.items.find(old=>old.photos.some(p=>p.document_id===photo.document_id));if(from?.id===r.id)continue;if(from&&into.has(from.id)&&end(from.id)===r.id)continue;add('photo_room',photo.document_id,{room_id:from?.id??null},{room_id:r.id},photo.filename,'move to '+r.name)}
  }
  // A room emptied by moves needs no correction: the API has no room delete; its photos' moves say it.
  return result;
 }
 const api={create,find,move,remove,merge,corrections,clone,unassigned};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisRoomPlacement=api;
})(typeof globalThis==='object'?globalThis:this);
