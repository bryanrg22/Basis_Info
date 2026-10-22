/* Presentation grouping shared by the room sections and photo viewer. */
(function(global){
 'use strict';
 const names=['Kitchen','Living room','Bedroom','Exterior','Unassigned'];
 function groupPhotos(photos,changes={}){
  const groups=names.map(name=>({name,photos:[]}));
  photos.forEach((photo,index)=>{const name=changes[index]||photo[2]||'Unassigned';let group=groups.find(g=>g.name===name);if(!group){group={name,photos:[]};groups.push(group)}group.photos.push({photo,index})});
  return groups.filter(g=>g.name!=='Unassigned'||g.photos.length);
 }
 const model={groupPhotos};if(typeof module!=='undefined'&&module.exports)module.exports=model;else global.RoomPresentation=model;
})(typeof globalThis!=='undefined'?globalThis:this);
