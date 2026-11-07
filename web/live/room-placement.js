/* Editable room grouping, using the same revision-correction API as other review gates. */
(function(){
 'use strict';
 const Live=globalThis.BasisLive,P=globalThis.BasisRoomPlacement;if(!Live||!P)return;
 const L=Live.state,legacyRooms=rooms;let dragging=null;
 const editable=()=>Live.gates.componentsGate(L.study)&&Live.gateOpen('rooms_review');
 function session(){
  const key=L.study.id+':'+L.study.revision,data=Live.roomsEntry(L.study).data;
  if(!data)return null;
  if(L.placement?.key!==key)L.placement={key,data,draft:P.create(data),undo:[]};
  return L.placement;
 }
 function sync(){const s=session();L.corrections.rooms_review=new Map(P.corrections(s.data,s.draft).map(c=>[c.key,c]))}
 function update(next,message){
  if(!editable())return;const s=session();if(next===s.draft)return;
  s.undo.push(P.clone(s.draft));s.draft=next;sync();Live.render();toast(message+' Save changes when you’re ready.');
 }
 Live.roomPlacementReady=()=>{
  if(!Live.gates.componentsGate(L.study))return true;
  const s=session();return Boolean(s&&!s.draft.unassigned.length&&!P.corrections(s.data,s.draft).length);
 };
 function photo(p,compact=false){
  const edit=editable();
  return `<div class="placement-photo ${compact?'placement-thumb':'placement-full'}" data-placement-photo="${esc(p.document_id)}" ${edit?'draggable="true"':''}>
   <a class="placement-image" draggable="false" href="${Live.route('viewer',L.study.id,p.document_id,'rooms')}" aria-label="View ${esc(p.filename)}">${Live.photo(p.document_id,p.filename,compact?'room-summary-thumb':'photo')}</a>
   ${compact?'':`<span class="placement-filename">${esc(p.filename)}</span>${p.analyzed===false?'<span class="small">Analysis unavailable</span>':''}`}
   ${edit?`<button class="placement-move" title="Move to room" data-live="placement-move" data-value="${esc(p.document_id)}" aria-label="Move ${esc(p.filename)} to a room">${compact?icon('move',16):'Move to room'}</button>`:''}</div>`;
 }
 function card(r,i,expanded){
  const open=r.photos.length>0&&expanded.has(r.id),edit=editable(),photoCount=`${r.photos.length} ${r.photos.length===1?'photo':'photos'}`;
  return `<section class="room-section placement-room ${open?'is-expanded':''}" data-placement-room="${esc(r.id)}" aria-label="${esc(r.name)} room">
   <div class="placement-heading"><div class="placement-title">${(()=>{const o=session()?.data?.items?.find(x=>x.id===r.id),edited=o&&(o.name!==r.name||o.room_type!==r.room_type||(o.floor??null)!==(r.floor??null));return `<h2>${edited&&o.name!==r.name?`<s class="live-ai">${esc(o.name)}</s> `:''}${esc(r.name)}</h2><p>${esc(Live.labelName(r.room_type))}${r.floor!==null?' · Floor '+esc(r.floor):''}${edited?'<small class="live-edited-chip">Edited by you · pending</small>':''}</p>${edited&&edit?`<button class="link-button" type="button" data-live="placement-reset" data-value="${esc(r.id)}">Reset to Basis AI</button>`:''}`})()}</div>
   <div class="placement-tools">${edit?`<button class="button placement-edit" data-live="placement-edit" data-value="${esc(r.id)}" aria-label="Edit room: ${esc(r.name)}">Edit room</button>${session().draft.rooms.length>1?`<button class="button placement-remove" data-live="placement-remove" data-value="${esc(r.id)}" aria-label="Merge ${esc(r.name)} into another room">Merge…</button>`:''}`:''}${r.photos.length?`<button class="button placement-toggle" data-live="placement-toggle" data-value="${esc(r.id)}" aria-expanded="${open}" aria-controls="placement-body-${i}" aria-label="${open?'Hide':'Show'} ${photoCount} in ${esc(r.name)}">${open?'Hide':'Show'} ${photoCount}${icon('down',16)}</button>`:''}</div></div>
   <div class="placement-preview"><div class="placement-thumbs" ${open?'hidden':''}>${r.photos.slice(0,4).map(p=>photo(p,true)).join('')}${r.photos.length>4?`<button class="placement-more" data-live="placement-toggle" data-value="${esc(r.id)}" aria-expanded="${open}" aria-controls="placement-body-${i}" aria-label="Show all ${photoCount} in ${esc(r.name)}">+${r.photos.length-4}</button>`:''}${!r.photos.length?'<span class="muted">Drop photos here, or use Move to room on a photo.</span>':''}</div>
</div>
   <div id="placement-body-${i}" class="placement-gallery" ${open?'':'hidden'}>${r.photos.map(p=>photo(p)).join('')}${!r.photos.length?'<p class="muted">Drop photos here, or use Move to room on a photo.</p>':''}</div><span class="placement-drop-label">Move photo to ${esc(r.name)}</span></section>`;
 }
 rooms=function(){
  if(!Live.gates.componentsGate(L.study))return legacyRooms();
  return Live.withStudy(study=>{
   const entry=Live.roomsEntry(study);if(!entry.done)return `${Live.steps(study)}${Live.loading('Loading rooms…')}`;if(entry.error)return Live.failed(entry.error,'reload-study');
   const s=session(),draft=s.draft,edit=editable(),changes=P.corrections(s.data,draft),count=draft.rooms.reduce((n,r)=>n+r.photos.length,0)+draft.unassigned.length;
   L.placementExpanded||=new Map();if(!L.placementExpanded.has(s.key))L.placementExpanded.set(s.key,new Set());const expanded=L.placementExpanded.get(s.key);
   return `${Live.steps(study)}<div class="placement-page-heading"><div><h1>Rooms & photos</h1>${edit?'<p>Drag photos into the right room, or use each photo’s move button.</p>':''}</div>${edit&&Live.caps?.roomCreate?'<button class="button primary" data-live="placement-create">'+icon('plus',16)+'New room</button>':''}</div>
    <div class="placement-overview"><dl class="placement-stats" aria-label="Room grouping summary"><div><dt>Rooms in this study</dt><dd>${draft.rooms.length}</dd></div><div><dt>Total photos</dt><dd>${count}</dd></div>${draft.unassigned.length?`<div class="placement-stat-pending"><dt>Photos needing a room</dt><dd>${draft.unassigned.length}</dd></div>`:''}</dl></div>
    ${draft.unassigned.length?`<section class="placement-unassigned" aria-label="Photos needing placement"><div><h2>Needs placement <span>${draft.unassigned.length}</span></h2><p>These photos are not in a room. Photos marked “Analysis unavailable” have no analysis result. ${edit?'Resolve every photo before approving rooms.':'These photos were not placed in this revision.'}</p></div><div class="placement-unassigned-photos">${draft.unassigned.map(p=>photo(p)).join('')}</div></section>`:''}
    <div class="room-sections placement-rooms">${draft.rooms.map((r,i)=>card(r,i,expanded)).join('')||'<div class="placement-empty"><h2>Create the first room</h2><p>Add a room, then move its photos into it.</p></div>'}</div>
    ${edit?`<div class="actionbar review-decision placement-decision ${changes.length||draft.unassigned.length?'is-attention':''}"><div><strong>${changes.length?'Unsaved room changes':'Room grouping'}</strong><p>${draft.unassigned.length?`${draft.unassigned.length} ${draft.unassigned.length===1?'photo needs':'photos need'} a room before you can continue.`:changes.length?'Save your changes, then review the updated grouping.':'Every photo is in a room. Ready to continue.'}</p></div><div class="row">${s.undo.length?'<button class="button quiet" data-live="placement-undo">Undo</button>':''}${changes.length?'<button class="button primary" data-live="placement-save">Save changes</button>':`<button class="button primary" data-live="approve" data-value="rooms_review" ${draft.unassigned.length?'disabled':''}>${icon('check',16)}Approve rooms</button>`}</div></div>`:''}`;
  });
 };
 const scope=()=>session()?.key;
 Live.actions['placement-toggle']=(el,id)=>{
  const set=L.placementExpanded.get(scope());set.has(id)?set.delete(id):set.add(id);Live.render();
  root.querySelector(`.placement-toggle[data-value="${CSS.escape(id)}"]`)?.focus({preventScroll:true});
 };
 Live.actions['placement-undo']=()=>{if(!editable())return;const s=session();if(!s.undo.length)return;s.draft=s.undo.pop();sync();Live.render();toast('Last room change undone.')};
 function move(doc,target){
  if(!editable())return;const s=session(),to=s.draft.rooms.find(r=>r.id===target);if(!to)return;
  const next=P.move(s.draft,doc,target);if(next===s.draft)return;
  const from=P.find(s.draft,doc),node=from&&root.querySelector(`[data-placement-room="${CSS.escape(from.id)}"]`);
  if(from?.photos.length===1&&node&&!matchMedia('(prefers-reduced-motion: reduce)').matches){
   const captured=s.key;node.animate([{opacity:1,transform:'scale(1)'},{opacity:0,transform:'scale(.96)'}],{duration:170,easing:'ease-out'}).finished.catch(()=>{}).then(()=>{if(session()?.key===captured&&editable())update(P.move(session().draft,doc,target),'Photo moved to '+to.name+'.')});
  }else update(next,'Photo moved to '+to.name+'.');
 }
 function photoById(doc){const d=session().draft;return P.find(d,doc)?.photos.find(p=>p.document_id===doc)||d.unassigned.find(p=>p.document_id===doc)}
 Live.actions['placement-move']=(el,doc)=>{
  if(!editable())return;const s=session(),p=photoById(doc);if(!p)return;
  const from=P.find(s.draft,doc),others=s.draft.rooms.filter(r=>r.id!==from?.id),key=s.key;
  const m=modal('Move photo',`<div class="placement-dialog-photo">${Live.photo(doc,p.filename,'photo')}</div><p>${esc(p.filename)}</p><div class="field"><label for="placement-target">Destination room</label><select data-native="true" id="placement-target"><option value="">Choose a room</option>${others.map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('')}${Live.caps?.roomCreate?'<option value="new">＋ Create a new room</option>':''}</select></div>`,`${closeBtn}<button class="button primary" id="placement-confirm">Move photo</button>`);
  m.querySelector('#placement-confirm').onclick=()=>{if(scope()!==key||!editable()){m.remove();return}const id=m.querySelector('#placement-target').value;if(!id){toast('Choose a room.');return}m.remove();if(id==='new')editRoom(null,doc);else move(doc,id)};
 };
 function editRoom(id,photoId){
  if(!editable())return;const s=session(),r=s.draft.rooms.find(r=>r.id===id),key=s.key;
  const types=['kitchen','living_room','bedroom','bathroom','dining_room','office','hallway','laundry','basement','garage','exterior','other'];if(r?.room_type&&!types.includes(r.room_type))types.unshift(r.room_type);
  const m=modal(r?'Edit room':'New room',`<div class="field"><label for="placement-name">Room name</label><input id="placement-name" required maxlength="120" value="${esc(r?.name||'')}" placeholder="e.g. First floor kitchen"></div><div class="placement-dialog-fields"><div class="field"><label for="placement-type">Room type</label><select data-native="true" id="placement-type">${types.map(t=>`<option value="${t}" ${t===(r?.room_type||'other')?'selected':''}>${esc(Live.labelName(t))}</option>`).join('')}</select></div><div class="field"><label for="placement-floor">Floor (optional)</label><input id="placement-floor" maxlength="64" value="${esc(r?.floor??'')}" placeholder="e.g. 1 or Basement"></div></div>`,`${closeBtn}<button class="button primary" id="placement-confirm">${r?'Update room':'Create room'}</button>`);
  m.querySelector('#placement-confirm').onclick=()=>{
   if(scope()!==key||!editable()){m.remove();return}const name=m.querySelector('#placement-name').value.trim();if(!name||name.toLowerCase()==='unassigned'){toast('Enter a specific room name.');m.querySelector('#placement-name').focus();return}
   let next=P.clone(s.draft),target=r?next.rooms.find(x=>x.id===id):{id:crypto.randomUUID(),photos:[],inventory:[]};Object.assign(target,{name,room_type:m.querySelector('#placement-type').value,floor:m.querySelector('#placement-floor').value.trim()||null});if(!r)next.rooms.push(target);if(photoId)next=P.move(next,photoId,target.id);m.remove();update(next,r?'Room updated.':'Room created.');
  };
  m.querySelector('#placement-name').focus();
 }
 // Reset to AI: the room's name, type and floor as the review opened them.
 Live.actions['placement-reset']=(el,id)=>{if(!editable())return;const s=session(),o=s.data.items.find(x=>x.id===id),next=P.clone(s.draft),r=next.rooms.find(x=>x.id===id);if(!o||!r)return;Object.assign(r,{name:o.name,room_type:o.room_type,floor:o.floor??null});update(next,`${o.name}: back to Basis AI’s details.`)};
 Live.actions['placement-create']=()=>editRoom();Live.actions['placement-edit']=(el,id)=>editRoom(id);
 // The API merges rooms ({merge_into}); it has no room delete. Merging moves the room's photos and
 // per-photo rows into the room you choose.
 Live.actions['placement-remove']=(el,id)=>{
  if(!editable())return;const s=session(),r=s.draft.rooms.find(r=>r.id===id);if(!r)return;const others=s.draft.rooms.filter(x=>x.id!==id),key=s.key;if(!others.length)return;
  const m=modal('Merge '+esc(r.name)+' into another room',`<p>${r.photos.length===1?'Its photo moves to the room you choose, with its components. ':r.photos.length?`Its ${r.photos.length} photos move to the room you choose, with their components. `:''}${esc(r.name)} then leaves the list. The uploaded files are kept.</p><div class="field"><label for="placement-target">Merge into</label><select data-native="true" id="placement-target"><option value="">Choose a room</option>${others.map(o=>`<option value="${esc(o.id)}">${esc(o.name)}</option>`).join('')}</select></div>`,`${closeBtn}<button class="button primary" id="placement-confirm">Merge rooms</button>`);
  m.querySelector('#placement-confirm').onclick=()=>{if(scope()!==key||!editable()){m.remove();return}const to=m.querySelector('#placement-target').value;if(!to){toast('Choose the room to merge into.');return}try{const next=P.merge(s.draft,id,to);m.remove();update(next,`${r.name} merged into ${others.find(o=>o.id===to).name}.`)}catch(e){toast(e.message)}};
 };
 Live.actions['placement-save']=()=>{
  if(!editable())return;sync();const s=session(),key=s.key,changes=P.corrections(s.data,s.draft);
  if(!changes.length)return;
  const m=modal('Save room changes',`<p>This saves the grouping as a new revision. You can review it before approving rooms.</p><ul class="placement-change-list">${changes.map(c=>`<li><strong>${esc(c.title)}</strong><span>${esc(c.summary)}</span></li>`).join('')}</ul>`,`${closeBtn}<button class="button primary" id="placement-confirm">Save changes</button>`);
  m.querySelector('#placement-confirm').onclick=()=>{if(scope()!==key||!editable()){m.remove();return}m.remove();Live.saveRoomChanges()};
 };
 // Native drag images keep the photo under the pointer. Move buttons cover keyboard/touch.
 const clearDrag=()=>{dragging=null;root.querySelectorAll('.placement-drop-active,.placement-dragging').forEach(n=>n.classList.remove('placement-drop-active','placement-dragging'))};
 root.addEventListener('dragstart',e=>{
  const node=e.target.closest('[data-placement-photo]');if(!node||!editable())return;
  const doc=node.dataset.placementPhoto;if(!photoById(doc))return;
  dragging={doc,scope:scope()};e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('application/x-basis-photo',doc);
  const img=node.querySelector('img');if(img)e.dataTransfer.setDragImage(img,Math.min(40,img.width/2),Math.min(30,img.height/2));node.classList.add('placement-dragging');
 });
 root.addEventListener('dragover',e=>{
  if(!dragging||dragging.scope!==scope()||!editable())return;const room=e.target.closest('[data-placement-room]');
  root.querySelectorAll('.placement-drop-active').forEach(n=>{if(n!==room)n.classList.remove('placement-drop-active')});
  if(!room||P.find(session().draft,dragging.doc)?.id===room.dataset.placementRoom)return;
  e.preventDefault();e.dataTransfer.dropEffect='move';room.classList.add('placement-drop-active');
 });
 root.addEventListener('dragleave',e=>{const room=e.target.closest('[data-placement-room]');if(room&&!room.contains(e.relatedTarget))room.classList.remove('placement-drop-active')});
 root.addEventListener('drop',e=>{const room=e.target.closest('[data-placement-room]'),d=dragging;if(!d||!room)return;e.preventDefault();clearDrag();if(d.scope===scope())move(d.doc,room.dataset.placementRoom)});
 root.addEventListener('dragend',clearDrag);window.addEventListener('blur',clearDrag);window.addEventListener('hashchange',clearDrag);document.addEventListener('visibilitychange',()=>{if(document.hidden)clearDrag()});document.addEventListener('keydown',e=>{if(e.key==='Escape')clearDrag()});
})();
