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
 function corrections(data,draft){
  const result=[],original=new Map(data.items.map(r=>[r.id,r])),current=new Map(draft.rooms.map(r=>[r.id,r]));
  const add=(kind,id,before,after,title,summary)=>result.push({key:kind+':'+id,entity_type:kind,entity_id:id,before,after,reason:null,title,summary});
  for(const r of draft.rooms){
   const before=original.get(r.id),values={name:r.name,room_type:r.room_type,floor:r.floor};
   if(!before)add('room_create',r.id,null,values,r.name,'create room');
   else {const after=Object.fromEntries(Object.entries(values).filter(([k,v])=>(before[k]??null)!==v));if(Object.keys(after).length)add('room',r.id,{name:before.name,room_type:before.room_type,floor:before.floor??null},after,r.name,'update room details')}
   for(const photo of r.photos){const from=data.items.find(old=>old.photos.some(p=>p.document_id===photo.document_id));if(from?.id!==r.id)add('photo_room',photo.document_id,{room_id:from?.id??null},{room_id:r.id},photo.filename,'move to '+r.name)}
  }
  for(const r of original.values())if(!unassigned(r)&&!current.has(r.id))add('room_delete',r.id,{name:r.name},{},r.name,'remove empty room');
  return result;
 }
 const api={create,find,move,remove,corrections,clone,unassigned};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisRoomPlacement=api;
})(typeof globalThis==='object'?globalThis:this);
