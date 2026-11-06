/* Presentation grouping shared by the room sections and photo viewer. */
(function(global){
 'use strict';
 const names=['Kitchen','Living room','Bedroom','Exterior','Unassigned'];
 function groupPhotos(photos,changes={}){
  const groups=names.map(name=>({name,photos:[]}));
  photos.forEach((photo,index)=>{const name=changes[index]||photo[2]||'Unassigned';let group=groups.find(g=>g.name===name);if(!group){group={name,photos:[]};groups.push(group)}group.photos.push({photo,index})});
  return groups.filter(g=>g.name!=='Unassigned'||g.photos.length);
 }
 // Sum inventory entries, including staged corrections, without double-counting
 // the same physical objects appearing in several photos.
 function componentSummary(room,corrections=new Map()){
  const types=new Map();
  const add=item=>{
   const count=Number(item.count);if(!Number.isFinite(count)||count<=0)return;
   const label=String(item.label||'other').trim()||'other';
   types.set(label,(types.get(label)||0)+count);
  };
  for(const item of room.inventory||[]){
   if(corrections.has('component_remove:'+item.component_id))continue;// a row the engineer removed, pending send
   const after=corrections.get('component:'+item.component_id)?.after||{};
   add({label:after.label||item.label,count:after.count??item.count});
  }
  for(const change of corrections.values()){
   if(change.entity_type==='room_component'&&change.entity_id===room.id)add(change.after||{});
  }
  // Put named types first so a generic "Other" bucket does not obscure the preview.
  const categories=[...types].map(([label,count])=>({label,count})).sort((a,b)=>(a.label==='other')-(b.label==='other')||b.count-a.count||a.label.localeCompare(b.label));
  return {count:categories.reduce((total,item)=>total+item.count,0),types:categories};
 }
 function componentCount(room,corrections=new Map()){return componentSummary(room,corrections).count}
 const model={groupPhotos,componentCount,componentSummary};if(typeof module!=='undefined'&&module.exports)module.exports=model;else global.RoomPresentation=model;
})(typeof globalThis!=='undefined'?globalThis:this);
