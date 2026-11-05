/* One mounted photo workspace for fixtures and live data. Selection never rebuilds the
 * app shell. Live mutations use only the published object/decision API. */
(function(){
 'use strict';
 const Live=globalThis.BasisLive,V=BasisVision,M=BasisManualAdd;
 const labels=V.LABELS;
  const reviewStage=()=>Live?Live.gates.editStage(Live.state.study):'rooms_review';
 const name=l=>String(l||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());
 let context=null,cleanup=()=>{},mounted=null;
 const view={doc:null,focusLabel:null,selectedId:null,hide:false,zoom:1,x:0,y:0,tool:null,addLabel:null,draft:null,points:[],busy:false,overlap:[],newId:null,notice:'',error:false,hand:false,candidates:null};
 const fixture={items:new Map(),counts:new Map(),reviewed:new Set()};
 function sampleItems(doc){
  if(fixture.items.has(doc))return fixture.items.get(doc);
  const items=V.sampleItems(doc);
  if(doc==='0'&&new URLSearchParams(location.search).get('fixture')==='dense'){for(let n=0;n<28;n++){const x=.64+(n%7)*.002,y=.32+Math.floor(n/7)*.002;items.push(box('dense-'+n,'kitchen_cabinet',x,y,x+.05,y+.07))}}
  fixture.items.set(doc,items);return items;
 }
 const photo=(p,alt='',cls='')=>Live?Live.photo(p.document_id,alt,cls):`<img class="${cls}" src="assets/${p.asset}.webp" alt="${esc(alt)}" draggable="false">`;
 const act=(action,text,value='',extra='')=>`<button type="button" data-vision="${action}" data-value="${esc(value)}" ${extra}>${text}</button>`;
 function mockContext(){
  const g=componentPhotoGroup(),p=g.items.find(p=>p.index===g.index),doc=String(g.index);
  return {id:'fixture',doc,property:'Harbor House',room:{id:g.room,name:g.room,inventory:[{component_id:'sample-cabinet',label:'kitchen_cabinet',count:4},{component_id:'sample-light',label:'light_fixture',count:3}]},
   rooms:RoomPresentation.groupPhotos(photos,state.group).filter(g=>g.photos.length).map(g=>({id:g.name,name:g.name,doc:String(g.photos[0].index)})),
   photos:g.items.map(p=>({document_id:String(p.index),filename:p.photo[1],asset:p.photo[0]})),at:g.items.indexOf(p),items:sampleItems(doc),open:!blockedEdit(),revision:1};
 }
 viewer=function(){
  if(!Live){context=mockContext();return markup(context)}
  return Live.withStudy(s=>{
   const re=Live.roomsEntry(s);if(!re.done)return Live.loading('Loading rooms…');if(re.error)return Live.failed(re.error,'reload-study');
   // The entry route owns the viewer mode; a completed study still has a plain Rooms gallery.
   const origin=Live.state.viewerOrigin,photoOnly=origin==='rooms'||(!origin&&Live.gates.componentsGate(s)&&s.current_stage==='rooms_review');
   const placement=Live.state.placement,draft=photoOnly&&placement?.key===s.id+':'+s.revision?placement.draft:null;
   const all=[...(draft?.rooms||re.data.items)],unassigned=draft?.unassigned||[...new Map([...(re.data.unassigned_photos||[]),...(re.data.unplaced||[])].map(p=>[p.document_id,p])).values()];
   if(photoOnly&&unassigned.length)all.push({id:'unassigned',name:'Needs placement',photos:unassigned});
   const doc=Live.state.docId||all.find(r=>r.photos.length)?.photos[0]?.document_id,room=all.find(r=>r.photos.some(p=>p.document_id===doc));
   if(!room)return '<div class="live-state">No photos are available in this room.</div>';
   const e=photoOnly?{done:true}:Live.load(`objects:${s.id}:${s.revision}:${doc}`,()=>Live.api.photoObjects(s.id,doc,s.revision));
   context={id:s.id,doc,photoOnly,origin:origin||(photoOnly?'rooms':undefined),property:s.property_name,room,rooms:all.filter(r=>r.photos.length).map(r=>({id:r.id,name:r.name,doc:r.photos[0].document_id})),photos:room.photos,at:room.photos.findIndex(p=>p.document_id===doc),items:e.data?.items||[],open:!photoOnly&&Live.gateOpen(Live.gates.editStage(s)),stage:Live.gates.editStage(s),back:photoOnly?'rooms':Live.gates.editScreen(s),revision:s.revision,loading:!e.done,error:e.error};
   return markup(context);
  });
 };
 function reset(c){
  if(view.doc===c.id+':'+c.revision+':'+c.doc+':'+Boolean(c.photoOnly))return;
  Object.assign(view,{doc:c.id+':'+c.revision+':'+c.doc+':'+Boolean(c.photoOnly),focusLabel:null,selectedId:null,hide:false,zoom:1,x:0,y:0,tool:null,draft:null,points:[],overlap:[],newId:null,notice:'',error:false,hand:false,candidates:null});
 }
 function markup(c){
  reset(c);if(!c.open)Object.assign(view,{tool:null,draft:null,points:[],candidates:null});const p=c.photos[c.at],back=c.back||'rooms',backLabel=back==='components'?'Back to components':'Back to rooms';
  return `${Live?.studyStatus?Live.studyStatus(Live.state.study,c.photoOnly?'viewer-rooms':'viewer-components'):''}<section class="vision-workspace${c.photoOnly?' vision-photo-only':''}" aria-label="${c.photoOnly?'Room photos':'Photo component review'}"><header class="vision-header"><div><a class="vision-back" href="${Live?Live.route(back,c.id):'#rooms'}">${icon('back',16)}${backLabel}</a><h1>${esc(c.room.name)}${c.photoOnly?'':` <span>/ ${c.at+1}</span>`}</h1><p>${esc(c.property)}<span class="vision-filename" title="${esc(p.filename)}">${esc(p.filename)}</span></p></div><div class="vision-header-actions"><label class="vision-room">Room<select id="vision-room" aria-label="Choose room">${c.rooms.map(r=>`<option value="${esc(r.doc)}" ${r.id===c.room.id?'selected':''}>${esc(r.name)}</option>`).join('')}</select></label><nav aria-label="Room photo navigation">${act('previous',icon('back',16),'','aria-label="Previous photo" '+(!c.at?'disabled':''))}<span>${c.at+1} / ${c.photos.length}</span>${act('next',icon('arrow',16),'','aria-label="Next photo" '+(c.at===c.photos.length-1?'disabled':''))}</nav></div></header>
  ${!c.photoOnly&&!c.open&&!Live?.studyStatus?`<div class="vision-readonly">Read-only · ${Live&&Live.gates.componentsGate(Live.state.study)?'Component editing is available at the Components review.':'This room’s review is closed.'} You can still inspect every photo.</div>`:''}${c.photoOnly?'':'<div class="vision-phone-note">On a phone, inspect photos and review the room. Use a larger screen to edit outlines.</div>'}
  <div class="vision-body"><section class="vision-photo" aria-label="${c.photoOnly?'Photo':'Photo and outlines'}"><div class="vision-toolbar"><div class="vision-tools">${c.photoOnly?'':act('all','All types','','aria-pressed="'+(!view.focusLabel)+'"')+act('outlines',view.hide?'Show outlines':'Hide outlines','','aria-pressed="'+!view.hide+'"')}${act('hand','Pan','','aria-pressed="'+view.hand+'"')}</div><div class="vision-zoom">${act('out','−','','aria-label="Zoom out"')}<output id="vision-zoom">100%</output>${act('in','+','','aria-label="Zoom in"')}${act('fit','Fit','','aria-label="Fit photo"')}</div></div>
  <div class="vision-canvas" tabindex="0" aria-label="Photo canvas. ${c.photoOnly?'':'Click objects to select. Repeat click or press the right bracket key while the canvas is focused to cycle overlapping objects. '}Pinch or use plus and minus to zoom. Scroll to pan when zoomed."><div class="vision-image">${photo(p,p.filename,'vision-source')}${c.photoOnly?'':'<div class="vision-pin-layer"></div><svg class="vision-drawing" viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true"></svg>'}</div><div class="vision-canvas-note" role="status" aria-live="polite"></div></div>
  ${c.photoOnly?'':'<div class="vision-feedback" role="status" aria-live="polite"></div>'}<footer class="vision-filmstrip"><nav aria-label="Photos in this room">${c.photos.map((p,i)=>act('photo',photo(p,'')+`<span>${i+1}</span>`,p.document_id,`aria-label="Photo ${i+1}: ${esc(p.filename)}" aria-current="${i===c.at?'true':'false'}" title="${esc(p.filename)}"`)).join('')}</nav><span>${c.photos.length} ${c.photos.length===1?'photo':'photos'}</span>${Live?`<button type="button" data-live="download-doc" data-value="${esc(c.doc)}" aria-label="Download original photo">${icon('download',16)}</button>`:`<a href="assets/${p.asset}.webp" download aria-label="Download original photo">${icon('download',16)}</a>`}</footer></section>
  ${c.photoOnly?'':`<aside class="vision-inspector" aria-label="Components inspector"><header><div><h2>Components</h2><p>${c.items.filter(i=>i.counted).length} objects in this photo</p></div>${c.open&&!c.loading&&!c.error?act('add',icon('plus',16)+'Add object','','class="vision-edit vision-add-object"'):''}</header><div class="vision-inspector-scroll" tabindex="0" aria-label="Component list"><div class="vision-inspector-body"></div></div><footer class="vision-inspector-footer">${Live?`<a href="${Live.route(c.back||'rooms',c.id)}">Room inventory ${icon('arrow',14)}</a>`:act('rooms','Room inventory '+icon('arrow',14))}<span>Counts used for pricing live at room level.</span></footer></aside>`}</div></section>`;
 }
 function current(){return context.items.find(i=>i.id===view.selectedId)}
 function counted(){return context.items.filter(i=>i.counted)}
 function sorted(label){return counted().filter(i=>!label||i.label===label).sort((a,b)=>a.box.x1-b.box.x1||a.box.y1-b.box.y1||a.id.localeCompare(b.id))}
 // Live mode offers only what the API does today. A found object's type and count are
 // corrected through the room inventory (a component correction, sent with Request
 // corrections); an object an engineer added can be deleted. The per-object operations in
 // web/README.md ("API work still required") stay in mock mode until the API has them.
 function liveObjectActions(s){
  if(s.source==='manual')return `<div class="vision-action-row vision-edit">${view.newId===s.id?act('confirm','Keep addition','','class="vision-primary"'):''}${act('delete','Delete','','class="vision-danger"')}</div>`;
  return '';// The room inventory box above carries the correction for detected objects.
 }
 function inspector(){
  const c=context,s=current(),types=[...new Set(counted().map(i=>i.label))].sort(),label=view.focusLabel,objects=sorted(label);
  if(c.loading)return '<p class="vision-empty">Loading components…</p>';
  if(c.error)return `<div class="vision-empty"><p>Components could not be loaded.</p>${act('retry','Try again')}</div>`;
  let html='';
  if(view.tool){
   const d=view.draft,candidate=view.candidates?c.items.find(i=>i.id===view.candidates.ids[view.candidates.at]):null;
   html+=`<section class="vision-add"><h3>${d?'Check your selection':'Add an object'}</h3><label>Component type<select id="vision-add-label" aria-label="Component type" ${view.busy?'disabled':''}>${[...new Set([...labels,...types])].sort().map(l=>`<option value="${l}" ${l===view.addLabel?'selected':''}>${esc(name(l))}</option>`).join('')}</select></label><p>${view.busy?'Adding the object and finding its outline…':d?'Your selection stays on the photo until you add it.':view.tool==='polygon'?'Click around its edge, then finish the shape.':view.tool==='candidate'?'Click an object to preview what was found.':'Draw a box around one object.'}</p>${candidate?`<p>${view.candidates.at+1} of ${view.candidates.ids.length} under your selection. ${candidate.counted?'Already counted.':'Not counted yet.'}</p><div class="vision-action-row">${act('candidate-prev','Previous')}${act('candidate-next','Next')}${act('candidate-use','Use this object','','class="vision-primary"')}</div>`:''}<div class="vision-action-row">${d?act('commit',Live?'Find outline & add':'Add object','','class="vision-primary" '+(view.busy?'disabled':'')):view.tool==='polygon'?act('finish','Finish shape','','class="vision-primary" '+(view.points.length<3?'disabled':'')):''}${act('cancel','Cancel','',''+(view.busy?'disabled':''))}</div>${!view.busy?`<div class="vision-action-row">${act('box-tool','Draw box')}${act('polygon-tool','Draw shape')}${act('candidate-tool','Pick a found object')}</div>`:''}${Live?'<p class="vision-fine">Adding saves the object immediately. You can remove your addition afterward.</p>':'<p class="vision-fine">Design preview · changes stay in this tab.</p>'}</section>`;
   return html;
  }
  if(label){
   const typeTitle=`<div class="vision-type-title">${act('all',icon('back',14)+'All types')}<h3>${esc(name(label))}</h3><span>${objects.length} ${Live?'detected in this photo':'in photo'}</span></div>`;
   // Live: the heading rides in the sticky room inventory box, so scrolling to an object never hides it.
   if(!Live)html+=typeTitle;
   const rows=(c.room.inventory||[]).filter(i=>i.label===label),row=rows.find(i=>!i.added)||rows[0],pending=Live?.state.corrections[reviewStage()]?.get('component:'+row?.component_id)||Live?.state.corrections[reviewStage()]?.get(`room_component:${c.room.id}:${label}`),suggest=(c.room.suggested_counts||[]).find(i=>i.label===label),roomCount=Live?(V.roomCount(c.room,label,Live.state.corrections[reviewStage()])||undefined):fixture.counts.get(c.room.id+':'+label)??row?.count;
   if(Live){
    // Live: the room inventory is what a correction changes; it says so, with before and after.
    const before=V.roomCount(c.room,label)||0,queued=Live.state.corrections[reviewStage()]?.size||0;
    const retype=pending?.after?.label&&pending.after.label!==label?pending.after.label:null,moved=retype?(pending.after.count??row?.count??before):null;
    const value=retype?`<span class="vision-room-value"><span><s>${esc(name(label))}</s> → <b>${esc(name(retype))}</b></span><strong>${moved}</strong></span>`:`<span class="vision-room-value"><b>Total in this room</b><strong>${pending&&roomCount!==before?`<s>${before}</s> → ${roomCount??0}`:roomCount??'Not listed'}</strong></span>`;
    html+=`<section class="vision-room-count is-live" aria-label="Room inventory correction">${typeTitle}<span class="vision-room-title">Room inventory · ${esc(c.room.name)}</span>${value}${retype?`<p class="vision-room-note">After you send, ${esc(c.room.name)} counts ${esc(name(retype).toLowerCase())} in place of ${esc(name(label).toLowerCase())}. Outlines on the photo keep their detected label.${roomCount?` ${roomCount} ${esc(name(label).toLowerCase())} still counted from other entries.`:''}</p>`:''}${c.open?`<div class="vision-action-row vision-edit">${act('type','Correct room inventory','','class="vision-primary"')}</div>`:''}${suggest&&suggest.suggested>(roomCount||0)?`<p>Photos suggest ${suggest.suggested} in this room.</p>`:''}${queued?`<p class="vision-queued">${queued} ${queued===1?'correction':'corrections'} waiting · <a href="${Live.route(c.back||'components',c.id)}">Review</a> · <button type="button" class="link-button" data-live="reject" data-value="${reviewStage()}">Send</button></p>`:''}</section>`;
   }else html+=`<div class="vision-room-count"><span>Room inventory<strong>${roomCount??'Not listed'}</strong></span>${c.open?act('count','Edit count',label,'class="vision-edit"'):''}${suggest&&suggest.suggested>(roomCount||0)?`<p>Photos suggest ${suggest.suggested} in this room.</p>`:''}${pending?'<p>Correction pending · send from Room inventory.</p>':''}</div>`;
  }
  if(s){const index=objects.findIndex(i=>i.id===s.id)+1;
   html+=`<section class="vision-detail"><div><span>Selected object ${index}</span>${act('clear','×','','aria-label="Deselect object"')}</div><h3>${esc(name(s.label))}</h3><p>${s.source==='manual'?'Added by an engineer':'Found in photo'} · ${s.polygon?'Hand-drawn shape':s.mask?'Object outline':'Box outline'}${s.review?' · '+(s.review==='confirm'?'Confirmed':'Marked wrong'):''}</p>${s.score!==null&&s.score<.65?'<p>Needs a look</p>':''}${view.newId===s.id?'<p class="vision-receipt">Added to this photo. Check its outline.</p>':''}${c.open?(Live?liveObjectActions(s):`<div class="vision-action-row vision-edit">${act('confirm',view.newId===s.id?'Keep addition':'Confirm','','class="vision-primary"')}${act('wrong','Mark wrong')}${act('type','Change type')}${act('delete','Delete','','class="vision-danger"')}</div><details class="vision-fix vision-edit"><summary>Fix the outline</summary><p>Adjust an edge, separate objects, or remove an extra outline.</p><div class="vision-action-row">${act('refine','Refine')}${act('split','Split')}${act('merge','Merge')}${act('keep-box','Keep my box')}</div></details>`):''}</section>`;
  }
  if(label){html+=`${Live?`<p class="vision-list-label">Photo detections · this photo only</p>`:''}<div class="vision-object-list" role="group" aria-label="${esc(name(label))} objects">${objects.map((i,n)=>act('select',`<span class="vision-number">${n+1}</span><span>${esc(name(i.label))} ${n+1}<small>${i.source==='manual'?'Added':'Found in photo'}</small></span>${i.id===view.selectedId?icon('check',14):''}`,i.id,`class="vision-instance ${i.id===view.selectedId?'is-selected':''}" aria-pressed="${i.id===view.selectedId}"`)).join('')}</div>${c.open?act('add',icon('plus',14)+'Add '+esc(name(label).toLowerCase()),label,'class="vision-add-type vision-edit"'):''}`}
  else html+=`<div class="vision-type-list" role="group" aria-label="Component types">${types.map(l=>act('type-select',`<span>${esc(name(l))}</span><strong>${sorted(l).length}</strong>${icon('chevron',14)}`,l,'class="vision-type" aria-pressed="false"')).join('')||'<p class="vision-empty">No components found in this photo.</p>'}</div>`;
  if(c.open&&!Live)html+='<p class="vision-fine">Illustrative boxes · not analysis results.</p>';
  return html;
 }
 function mount(){
  cleanup();mounted=root.querySelector('.vision-workspace');if(!mounted)return;
  if(context.photoOnly)document.title='Basis · Room photos';
  const c=context,el=mounted,canvas=el.querySelector('.vision-canvas'),image=el.querySelector('.vision-image'),img=el.querySelector('.vision-source'),pinLayer=el.querySelector('.vision-pin-layer'),drawing=el.querySelector('.vision-drawing'),scroller=el.querySelector('.vision-inspector-scroll');
  const layer=c.photoOnly?null:BasisMaskLayer.attach(img,{});let base={width:0,height:0},drag=null,suppressClick=false,pointers=new Map(),pinch=null;
  function drawShape(){
   if(c.photoOnly)return;
   image.querySelector('.vision-draft-status')?.remove();
   const b=view.draft?.box,points=view.draft?.polygon||view.points;
   if(b){const status=document.createElement('span');status.className='vision-draft-status';status.textContent=view.busy?'Finding outline…':'Your selection';status.style.left=(b.x1*base.width)+'px';status.style.top=(b.y1*base.height)+'px';status.style.transform=`translateY(-100%) scale(${1/view.zoom})`;image.append(status)}
   drawing.innerHTML=points.length?`<polyline points="${points.map(p=>p.x*1000+','+p.y*1000).join(' ')}"/>${points.map(p=>`<circle cx="${p.x*1000}" cy="${p.y*1000}" r="5"/>`).join('')}`:b?`<rect x="${b.x1*1000}" y="${b.y1*1000}" width="${(b.x2-b.x1)*1000}" height="${(b.y2-b.y1)*1000}"/>`:'';
  }
  function pinPaint(){
   if(c.photoOnly)return;
   pinLayer.replaceChildren();if(view.hide||view.tool||!view.focusLabel)return;
   const anchors=sorted(view.focusLabel).map((i,n)=>({id:i.id,n:n+1,x:(i.box.x1+i.box.x2)/2*base.width*view.zoom,y:(i.box.y1+i.box.y2)/2*base.height*view.zoom}));
   const placed=V.pins(anchors,base.width*view.zoom,base.height*view.zoom);
   const leaders=document.createElementNS('http://www.w3.org/2000/svg','svg');leaders.setAttribute('viewBox',`0 0 ${base.width} ${base.height}`);leaders.classList.add('vision-leaders');leaders.setAttribute('aria-hidden','true');
   leaders.innerHTML=placed.filter(p=>!p.items).map(p=>`<line x1="${p.anchorX/view.zoom}" y1="${p.anchorY/view.zoom}" x2="${p.x/view.zoom}" y2="${p.y/view.zoom}"/>`).join('');pinLayer.append(leaders);
   // Pin positions are in visual pixels, keeping 36px hit targets even when zoomed.
   for(const p of placed){const b=document.createElement('button');b.type='button';b.className='vision-pin'+(p.id===view.selectedId?' is-selected':'');b.dataset.vision='select';b.dataset.value=p.id;b.style.left=p.x/view.zoom+'px';b.style.top=p.y/view.zoom+'px';b.style.transform=`translate(-50%,-50%) scale(${1/view.zoom})`;
    b.textContent=p.items?'+'+p.items.length:String(p.n);b.setAttribute('aria-label',p.items?'Choose from '+p.items.length+' nearby objects':`Select ${name(c.items.find(i=>i.id===p.id).label)} ${p.n}`);b.setAttribute('aria-pressed',String(p.id===view.selectedId));
    if(p.items){b.onclick=e=>{e.stopPropagation();picker(p.items.map(a=>c.items.find(i=>i.id===a.id)))};delete b.dataset.vision}
    pinLayer.append(b);
   }
  }
  function transform(){
   if(!el.isConnected)return;
   if(innerWidth<=600&&view.tool&&!view.busy){Object.assign(view,{tool:null,draft:null,points:[],candidates:null,replacing:null,notice:'Drawing cancelled. Use a larger screen to edit.'});paint()}
   canvas.classList.toggle('is-zoomed',view.zoom>1);
   const r=canvas.getBoundingClientRect(),fit=V.fit(r.width-32,r.height-32,img.naturalWidth||1200,img.naturalHeight||800);base=fit;
   image.style.width=fit.width+'px';image.style.height=fit.height+'px';
   const limitX=Math.max(0,(fit.width*view.zoom-r.width)/2+24),limitY=Math.max(0,(fit.height*view.zoom-r.height)/2+24);
   view.x=V.clamp(view.x,-limitX,limitX);view.y=V.clamp(view.y,-limitY,limitY);
   image.style.transform=`translate(-50%,-50%) translate(${view.x}px,${view.y}px) scale(${view.zoom})`;
   el.querySelector('#vision-zoom').textContent=Math.round(view.zoom*100)+'%';layer?.redraw();pinPaint();drawShape();
  }
  function paint(keepFocus=true){
   if(!el.isConnected)return;
   if(c.photoOnly){
    el.querySelector('[data-vision="hand"]').setAttribute('aria-pressed',String(view.hand));
    el.querySelector('.vision-canvas-note').textContent=view.error?view.notice:!img.complete?'Loading photo…':view.hand?'Drag to pan':'';
    canvas.classList.toggle('is-panning',view.hand);return;
   }
   const active=document.activeElement,action=active?.dataset.vision,value=active?.dataset.value,scroll=scroller.scrollTop;
   el.querySelector('.vision-inspector header p').textContent=counted().length+' objects in this photo';
   el.querySelector('.vision-inspector-body').innerHTML=inspector();scroller.scrollTop=scroll;
   el.querySelector('[data-vision="all"]').setAttribute('aria-pressed',String(!view.focusLabel));
   el.querySelector('[data-vision="outlines"]').textContent=view.hide?'Show outlines':'Hide outlines';
   el.querySelector('[data-vision="outlines"]').setAttribute('aria-pressed',String(!view.hide));el.querySelector('[data-vision="hand"]').setAttribute('aria-pressed',String(view.hand));
   el.querySelector('.vision-feedback').textContent=view.notice|| (view.tool?'Select a type, then draw around one object. Escape cancels.':view.overlap.length>1?`${view.overlap.length} objects here. Click again or press ] on the canvas to cycle.`:'Click an object or choose a component.');
   el.querySelector('.vision-feedback').classList.toggle('is-error',view.error);
   el.querySelector('.vision-canvas-note').textContent=view.busy?'Adding object…':view.tool==='polygon'?'Draw the object’s edge':view.tool==='box'?'Drag around one object':view.hand?'Drag to pan':!img.complete?'Loading photo…':'';
   layer?.set({instances:view.hide?[]:c.items,mode:view.candidates?'single':view.tool?'overview':view.selectedId?'single':view.focusLabel?'spotlight':'overview',focusLabel:view.focusLabel,selectedId:view.candidates?view.candidates.ids[view.candidates.at]:view.tool?null:view.selectedId,externalPins:true});
   pinPaint();drawShape();canvas.classList.toggle('is-drawing',Boolean(view.tool));canvas.classList.toggle('is-panning',view.hand);
   if(keepFocus&&action&&!active.isConnected){const target=[...el.querySelectorAll('[data-vision]')].find(b=>b.dataset.vision===action&&b.dataset.value===value);(target||canvas).focus({preventScroll:true})}
  }
  function selection(item){Object.assign(view,V.select(view,item),{tool:null,draft:null,points:[],candidates:null,notice:'',error:false});paint();
   const row=el.querySelector('.vision-instance.is-selected');if(row){const a=row.getBoundingClientRect(),b=scroller.getBoundingClientRect();if(a.bottom>b.bottom)scroller.scrollTop+=a.bottom-b.bottom+8;else if(a.top<b.top)scroller.scrollTop-=b.top-a.top+8;}
  }
  function pickPoint(ev){const r=image.getBoundingClientRect();return {x:(ev.clientX-r.left)/r.width,y:(ev.clientY-r.top)/r.height}}
  function pick(ev){
   if(c.photoOnly)return;
   if(suppressClick){suppressClick=false;return}if(view.busy||ev.target.closest('button'))return;const p=pickPoint(ev);if(p.x<0||p.x>1||p.y<0||p.y>1)return;
   if(view.tool==='polygon'){view.points.push(p);paint();return}
   if(view.tool==='box')return;
   if(view.tool==='candidate'){const hits=M.candidatesUnder(c.items,view.addLabel,p.x,p.y,BasisRle);if(!hits.length){view.tool='box';view.notice='Nothing matched there. Draw a box instead.';paint();return}const previous=view.candidates,again=previous?.point&&Math.hypot(p.x-previous.point.x,p.y-previous.point.y)<.015;view.candidates={ids:hits.map(i=>i.id),at:again?M.cycle(previous.at,hits.length):0,point:p};paint();return}
   const hits=V.under(c.items,p.x,p.y,BasisRle),same=view.lastPoint&&Math.hypot(p.x-view.lastPoint.x,p.y-view.lastPoint.y)<.015;
   const item=same?V.cycle(hits,view.selectedId):hits[0];selection(item||null);view.overlap=hits;view.lastPoint=p;paint();
  }
  async function submit(body){
   if(!c.open||innerWidth<=600||view.busy||c.loading||c.error)return;view.busy=true;view.notice='Adding the object and finding its outline…';view.error=false;paint();
   let saved=false;
   try{
    let r;
    if(Live)r=await Live.api.addObject(c.id,c.doc,body,view.mutationKey||=BasisApi.newKey());
    else{
     const old=V.under(c.items,(body.box?.x1+body.box?.x2)/2,(body.box?.y1+body.box?.y2)/2,BasisRle).find(i=>i.label===body.label);
     const candidate=c.items.find(i=>i.id===body.candidate_id);
     if((old||candidate?.counted)&&!body.allow_duplicate)r={created:false,duplicate_of:(old||candidate).id,object:old||candidate};
     else{const object={id:crypto.randomUUID(),label:body.label||candidate.label,box:body.box||candidate.box,polygon:view.draft?.polygon,source:'manual',counted:true,mask:null,mask_source:'none',score:null};c.items.push(object);r={created:true,object}}
    }
    view.mutationKey=null;
    if(!r.created&&!M.isDuplicate(r)){view.busy=false;selection(r.object);view.notice='That object is already counted.';return}
    if(M.isDuplicate(r)){view.busy=false;selection(r.object);duplicate(body);return}
    saved=true;if(!c.items.some(i=>i.id===r.object.id))c.items.push(r.object);selection(r.object);view.newId=r.object.id;if(Live)await refresh();view.notice=r.object.polygon?'Added your hand-drawn shape in this design preview.':r.object.mask?'Added to this photo. Check its outline.':'Added with your box. It counts like any other object.';
   }catch(e){view.error=true;view.notice=saved?'The object was added. Refresh the page to update the room inventory.':e.status===403?'Editing is not available for this study.':e.status===409?'The review changed. Refreshing the study…':'The object could not be added. Try again.';if(e.status===409&&Live){await Live.reloadStudy();Live.render();return}}
   finally{view.busy=false;paint()}
  }
  async function refresh(){
   const data=await Live.api.photoObjects(c.id,c.doc,c.revision);c.items=data.items;
   const rooms=await Live.api.rooms(c.id,c.revision);const room=rooms.items.find(r=>r.id===c.room.id);if(room)c.room=room;
   Live.state.cache.set(`objects:${c.id}:${c.revision}:${c.doc}`,{done:true,data});Live.state.cache.set(`rooms:${c.id}:${c.revision}`,{done:true,data:rooms});
   el.querySelector('.vision-inspector header p').textContent=counted().length+' objects in this photo';
  }
  function duplicate(body){
   const m=modal('Already counted?','<p>This overlaps an object already counted in this photo. Use it, or add another when they are separate objects.</p>',`<button class="button" id="vision-existing">Use the existing one</button><button class="button primary" id="vision-anyway">Add anyway</button>`);
   m.querySelector('#vision-existing').onclick=()=>{m.remove();view.notice='Kept the existing object.';paint()};
   m.querySelector('#vision-anyway').onclick=()=>{m.remove();submit({...body,allow_duplicate:true})};
  }
  function picker(items,candidate=false){
   const m=modal(candidate?'Choose the object to add':'Nearby objects',`<div class="vision-picker">${items.map((i,n)=>`<button class="button" data-pick="${esc(i.id)}">${esc(name(i.label))} ${i.counted?sorted(i.label).findIndex(o=>o.id===i.id)+1:n+1} ${i.counted?'· counted':'· not counted'}</button>`).join('')}</div>`,closeBtn);
   m.querySelectorAll('[data-pick]').forEach(b=>b.onclick=()=>{const i=items.find(i=>i.id===b.dataset.pick);m.remove();if(candidate)submit({kind:'candidate',candidate_id:i.id});else selection(i)});
  }
  async function remove(){
   const s=current();if(!s||!c.open||innerWidth<=600||view.busy)return;
   if(Live&&s.source!=='manual'){unavailable('Delete this object','Removing a found object needs a server operation that preserves its evidence and updates the room count. For now, correct the count in Room inventory.');return}
   const m=modal(s.source==='manual'?'Remove this added object?':'Remove this object?','<p>It will no longer count in this photo. Check the room inventory afterward.</p>',`${closeBtn}<button class="button danger" id="vision-remove">Remove object</button>`);
   m.querySelector('#vision-remove').onclick=async()=>{m.remove();view.busy=true;try{if(Live){await Live.api.deleteObject(c.id,c.doc,s.id);await refresh()}else c.items.splice(c.items.indexOf(s),1);selection(null);view.notice='Object removed.'}catch(e){view.notice=Live?Live.errorText(e):e.message;view.error=true}finally{view.busy=false;paint()}};
  }
  function unavailable(title,text){modal(esc(title),`<p>${esc(text)}</p><p class="vision-unavailable">Not available in live mode yet. This action did not change the study.</p>`,closeBtn)}
  function liveType(){
   const label=current()?.label||view.focusLabel;if(!label)return;const rows=(c.room.inventory||[]).filter(i=>i.label===label),row=rows.find(i=>!i.added)||rows[0];
   if(row)Live.actions['edit-component']({dataset:{room:c.room.id}},row.component_id);
   else Live.actions['add-room-component']({dataset:{label,count:String(sorted(label).length)}},c.room.id);
  }
  function countEdit(){
   const label=view.focusLabel;if(!label)return;const rows=(c.room.inventory||[]).filter(i=>i.label===label),row=rows.find(i=>!i.added)||rows[0];
   // Live mode corrects the room inventory through one dialog (Correct room inventory); this is the mock preview.
   const m=modal('Room inventory count',`<p>This is the count used for pricing across the room.</p><label class="field">Count<input id="vision-count" type="number" min="1" max="10000" value="${fixture.counts.get(c.room.id+':'+label)??row?.count??sorted(label).length}"></label>`,`${closeBtn}<button class="button primary" id="vision-save-count">Save count</button>`);
   m.querySelector('#vision-save-count').onclick=()=>{const n=M.validCount(m.querySelector('input').value);if(n===null){toast('Enter a whole number from 1 to 10,000.');return}fixture.counts.set(c.room.id+':'+label,n);m.remove();paint()};
  }
  function fix(action){
   const s=current();if(!s)return;
   if(action==='keep-box'&&!s.mask&&!s.polygon){view.newId=null;view.notice='Your box is kept. The object stays counted.';paint();return}
   if(action==='confirm'&&view.newId===s.id){view.newId=null;view.notice='Kept your addition. It is counted in this photo.';paint();return}
   if(Live){const descriptions={confirm:'Object confirmation needs a per-object review record. Room approval is available from Room inventory.',wrong:'Marking an object wrong needs a per-object review record. You can request a photo re-analysis or correct the room inventory.',type:'Changing an object type needs an update operation that also reconciles room counts.',refine:'Refining an outline needs positive and negative point prompts and a replacement outline operation.',split:'Splitting needs an operation that replaces one object with separate outlines and updates counts.',merge:'Merging needs an operation that replaces the selected objects with one outline and updates counts.','keep-box':'Keeping only your box needs an operation to replace the stored outline. A box-only result already returned by the server counts normally.'};unavailable(name(action),descriptions[action]);return}
   if(action==='confirm'||action==='wrong'){fixture.reviewed.add(s.id);s.review=action;view.notice=action==='confirm'?'Confirmed in this design preview.':'Marked wrong in this design preview.';paint();return}
   if(action==='keep-box'){s.polygon=null;s.mask=null;view.notice='Kept the box in this design preview.';paint();return}
   if(action==='refine'){view.addLabel=s.label;view.tool='polygon';view.replacing=s.id;view.points=[];view.draft=null;paint();return}
   const options=action==='type'?`<label class="field">Component type<select id="vision-type">${labels.map(l=>`<option value="${l}" ${s.label===l?'selected':''}>${esc(name(l))}</option>`).join('')}</select></label>`:action==='merge'?`<label class="field">Merge with<select id="vision-merge">${counted().filter(i=>i.id!==s.id&&i.label===s.label).map((i,n)=>`<option value="${i.id}">${esc(name(i.label))} ${n+1}</option>`).join('')}</select></label>`:'<p>Preview a vertical split into two boxes. Outline refinement will be needed for exact edges.</p>';
   const m=modal(action==='type'?'Change component type':action==='merge'?'Merge objects':'Split object',options+'<p class="vision-fine">Design preview only.</p>',`${closeBtn}<button class="button primary" id="vision-apply">Apply preview</button>`);
   m.querySelector('#vision-apply').onclick=()=>{if(action==='type')s.label=m.querySelector('#vision-type').value;
    else if(action==='merge'){const other=c.items.find(i=>i.id===m.querySelector('#vision-merge').value);if(!other)return;const a=s.box,b=other.box;s.box={x1:Math.min(a.x1,b.x1),y1:Math.min(a.y1,b.y1),x2:Math.max(a.x2,b.x2),y2:Math.max(a.y2,b.y2)};c.items.splice(c.items.indexOf(other),1)}
    else{const b={...s.box},mid=(b.x1+b.x2)/2;s.box={...b,x2:mid};c.items.push({...s,id:crypto.randomUUID(),box:{...b,x1:mid}})}m.remove();selection(s)};
  }
  function changePhoto(id){if(view.busy){toast('Wait until the object finishes saving.');return}if(view.tool||view.draft){view.notice='Finish or cancel your selection before switching photos.';paint();return}if(Live)Live.go('viewer',c.id,id,c.origin);else{state.componentPhoto=Number(id);render()}}
  function navigationGuard(e){if(view.busy&&e.target.closest('a')){e.preventDefault();e.stopImmediatePropagation();toast('Wait until the object finishes saving.')}}
  function handle(e){const b=e.target.closest('[data-vision]');if(!b)return;e.preventDefault();e.stopPropagation();if(view.busy)return;const a=b.dataset.vision,value=b.dataset.value;
   if(a==='select'){selection(c.items.find(i=>i.id===value));return}
   if(a==='all'){Object.assign(view,V.select(view,null));view.tool=null;paint();return}
   if(a==='type-select'){Object.assign(view,V.toggleType(view,value));paint();return}
   if(a==='clear'){view.selectedId=null;paint();return}
   if(a==='outlines'){view.hide=!view.hide;paint();return}
   if(a==='hand'){view.hand=!view.hand;paint();return}
   if(a==='in'||a==='out'||a==='fit'){if(a==='fit')Object.assign(view,{zoom:1,x:0,y:0});else view.zoom=V.clamp(view.zoom+(a==='in'?.25:-.25),1,6);transform();return}
   if(a==='photo'){changePhoto(value);return}if(a==='next'||a==='previous'){const p=c.photos[c.at+(a==='next'?1:-1)];if(p)changePhoto(p.document_id);return}
   if(a==='rooms'){if(Live)Live.go(c.back||'rooms',c.id);else navigate('rooms');return}if(a==='retry'){Live.invalidate(`objects:${c.id}`);Live.render();return}
   if(!c.open)return;
   if(a==='add'){Object.assign(view,{tool:'box',candidates:null,addLabel:value||view.focusLabel||labels[0],mutationKey:null,draft:null,points:[],notice:'',error:false,hand:false,candidates:null});paint();return}
   if(a==='cancel'){Object.assign(view,{tool:null,draft:null,points:[],candidates:null,replacing:null,mutationKey:null});paint();return}
   if(a==='box-tool'||a==='polygon-tool'||a==='candidate-tool'){if(a==='polygon-tool'&&Live){unavailable('Draw a shape','Saving a hand-drawn shape needs polygon or mask input in the object API. Draw a box to add an object today.');return}view.tool=a==='box-tool'?'box':a==='polygon-tool'?'polygon':'candidate';view.draft=null;view.points=[];view.candidates=null;view.mutationKey=null;paint();return}
   if(a==='candidate-prev'||a==='candidate-next'){const p=view.candidates;if(p){p.at=M.cycle(p.at,p.ids.length,a==='candidate-next'?1:-1);paint()}return}
   if(a==='candidate-use'&&view.candidates){submit({kind:'candidate',candidate_id:view.candidates.ids[view.candidates.at]});return}
   if(a==='finish'){if(view.points.length<3)return;const points=view.points,box={x1:Math.min(...points.map(p=>p.x)),y1:Math.min(...points.map(p=>p.y)),x2:Math.max(...points.map(p=>p.x)),y2:Math.max(...points.map(p=>p.y))};view.draft={box,polygon:[...points,points[0]]};paint();return}
   if(a==='commit'&&view.draft){if(view.replacing&&!Live){const s=c.items.find(i=>i.id===view.replacing);s.polygon=view.draft.polygon;s.box=view.draft.box;view.replacing=null;selection(s);return}submit({kind:'box',label:view.addLabel,box:view.draft.box});return}
   if(a==='delete'){remove();return}if(a==='count'){countEdit();return}
   if(Live&&a==='type'){liveType();return}if(['confirm','wrong','type','refine','split','merge','keep-box'].includes(a))fix(a);
  }
  function down(e){
   if(e.target.closest('button')||view.busy||e.button!==0)return;
   pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});canvas.setPointerCapture(e.pointerId);
   if(pointers.size===2){const [a,b]=[...pointers.values()];pinch={distance:Math.hypot(a.x-b.x,a.y-b.y),zoom:view.zoom};drag=null;return}
   const p=pickPoint(e);if(view.tool==='box'){if(p.x<0||p.x>1||p.y<0||p.y>1)return;drag={kind:'box',p};view.mutationKey=null;view.draft=null;e.preventDefault()}
   else if(view.hand||view.zoom>1)drag={kind:'pan',x:e.clientX,y:e.clientY,ox:view.x,oy:view.y,moved:false};
  }
  function move(e){
   if(pointers.has(e.pointerId))pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
   if(pinch&&pointers.size===2){const [a,b]=[...pointers.values()];view.zoom=V.clamp(pinch.zoom*Math.hypot(a.x-b.x,a.y-b.y)/pinch.distance,1,6);transform();suppressClick=true;return}
   if(drag?.kind==='pan'){const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.hypot(dx,dy)>4)drag.moved=true;view.x=drag.ox+dx;view.y=drag.oy+dy;transform();return}
   if(drag?.kind==='box'){const p=pickPoint(e),a=drag.p;view.draft={box:{x1:Math.min(a.x,V.clamp(p.x,0,1)),y1:Math.min(a.y,V.clamp(p.y,0,1)),x2:Math.max(a.x,V.clamp(p.x,0,1)),y2:Math.max(a.y,V.clamp(p.y,0,1))}};drawShape();return}
   if(!c.photoOnly&&!view.tool){const p=pickPoint(e),hit=V.under(c.items,p.x,p.y,BasisRle)[0];layer?.set({hoverId:hit?.id||null})}
  }
  function up(e){pointers.delete(e.pointerId);pinch=null;if(drag?.kind==='box'){suppressClick=true;const b=view.draft?.box;if(!b||b.x2-b.x1<.01||b.y2-b.y1<.01){view.draft=null;view.notice='Draw a larger box around one object.'}paint()}if(drag?.moved)suppressClick=true;drag=null}
  function cancelPointer(){if(drag||pinch)suppressClick=true;drag=null;pinch=null;for(const id of pointers.keys()){if(canvas.hasPointerCapture(id))canvas.releasePointerCapture(id)}pointers.clear();if(view.tool==='box'){view.draft=null;paint()}}
  const stopGestures=BasisGestures.attach(canvas,{
   canZoom:()=>!view.tool&&!view.busy&&Boolean(img.naturalWidth),
   zoom:(factor,point)=>{const r=canvas.getBoundingClientRect();Object.assign(view,V.zoomAt(view,view.zoom*factor,point.x-r.left-r.width/2,point.y-r.top-r.height/2));transform()},
   pan:delta=>{if(view.tool||view.busy||view.zoom<=1)return false;view.x-=delta.x;view.y-=delta.y;transform();return true},
  });
  function double(){if(view.tool)return;view.zoom=view.zoom>1?1:2;view.x=view.y=0;transform()}
  function key(e){
   if(document.querySelector('.modal-backdrop')||e.target.closest('input,select,textarea,[role="dialog"],[contenteditable="true"]'))return;
   if(e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();if(view.busy){toast('Wait until the object finishes saving.');return}cancelPointer();Object.assign(view,{tool:null,draft:null,points:[],candidates:null,selectedId:null,replacing:null});paint();return}
   if(view.busy)return;
   if(e.key==='Delete'&&current()&&c.open){e.preventDefault();remove();return}
   if((e.key===']'||e.key==='[')&&view.overlap.length>1){e.preventDefault();const hits=view.overlap;selection(V.cycle(e.key==='['?[...hits].reverse():hits,view.selectedId));view.overlap=hits;return}
   if(e.key==='Enter'&&view.tool==='polygon'&&view.points.length>=3){e.preventDefault();el.querySelector('[data-vision="finish"]').click();return}
   if(view.candidates&&(e.key==='ArrowRight'||e.key==='ArrowLeft'||e.key==='Enter')){e.preventDefault();if(e.key==='Enter')submit({kind:'candidate',candidate_id:view.candidates.ids[view.candidates.at]});else{view.candidates.at=M.cycle(view.candidates.at,view.candidates.ids.length,e.key==='ArrowRight'?1:-1);paint()}return}
   if(view.tool)return;
   if(e.key==='ArrowRight'||e.key==='ArrowLeft'){if(e.target.closest('[role="combobox"],[role="menu"],[role="listbox"]'))return;e.preventDefault();const p=c.photos[c.at+(e.key==='ArrowRight'?1:-1)];if(p)changePhoto(p.document_id)}
   if(e.key==='+'||e.key==='='||e.key==='-'){e.preventDefault();view.zoom=V.clamp(view.zoom+(e.key==='-'?-.25:.25),1,6);transform()}
   if(e.key==='0'){view.zoom=1;view.x=view.y=0;transform()}
  }
  document.addEventListener('click',navigationGuard,true);el.addEventListener('pointerover',e=>{const b=e.target.closest('.vision-pin');if(b&&b.dataset.value)layer?.set({hoverId:b.dataset.value})});el.addEventListener('focusin',e=>{const b=e.target.closest('.vision-pin');if(b&&b.dataset.value)layer?.set({hoverId:b.dataset.value})});el.addEventListener('pointerleave',()=>layer?.set({hoverId:null}));el.addEventListener('click',handle);canvas.addEventListener('click',pick);canvas.addEventListener('pointerdown',down);canvas.addEventListener('pointermove',move);canvas.addEventListener('pointerup',up);canvas.addEventListener('pointercancel',cancelPointer);canvas.addEventListener('dblclick',double);document.addEventListener('keydown',key,true);
  el.querySelector('#vision-room').onchange=e=>changePhoto(e.target.value);
  const change=e=>{if(e.target.id==='vision-add-label'){const hadCandidates=Boolean(view.candidates);view.addLabel=e.target.value;view.mutationKey=null;view.candidates=null;if(hadCandidates)paint()}};el.addEventListener('change',change);
  img.onload=()=>{transform();paint(false)};img.onerror=()=>{view.notice='Photo could not be loaded. Try another photo or download the original.';view.error=true;paint()};
  const observer=new ResizeObserver(transform);observer.observe(canvas);
  paint(false);transform();
  cleanup=()=>{stopGestures();document.removeEventListener('click',navigationGuard,true);observer.disconnect();layer?.destroy();document.removeEventListener('keydown',key,true);pointers.clear();mounted=null};
 }
 const previousRender=render;
 render=function(){cleanup();document.documentElement.classList.toggle('vision-screen',state.screen==='viewer'||location.hash.startsWith('#viewer'));previousRender();document.documentElement.classList.toggle('vision-screen',Boolean(root.querySelector('.vision-workspace')));const bar=root.querySelector('.reviewbar');if(bar)document.documentElement.style.setProperty('--vision-bar-height',bar.getBoundingClientRect().height+'px');mount()};
 render();
})();
