/* Live mode: the rules of adding what the pipeline missed, without the DOM (node-tested).
 *
 * The photo component page (live/live-review.js) adds an object with a drawn box first
 * (POST .../objects kind "box": a stored SAM 3 candidate, else SAM 3 prompted with the box,
 * else the box alone) or a tap that previews the stored instances under it and lets the
 * engineer cycle them before confirming one (kind "candidate"). The API answers a
 * selection that duplicates a counted instance with created: false and duplicate_of; the
 * page then offers "Use the existing one" or "Add anyway" (allow_duplicate). On the rooms
 * review, each room's suggested_counts (computed by the API: the most counted instances in
 * one photo of the room against the room's count) become a count correction of an
 * inventory row or a room_component correction (a component the inventory is missing);
 * both go with Request corrections, like every correction.
 */
(function(scope){
 'use strict';
 // basis.vision.schemas Material and Attachment ("unknown" is sent as no value).
 const MATERIALS=['wood','metal','steel','aluminum','vinyl','laminate','ceramic','porcelain','stone','granite','quartz','concrete','asphalt','brick','glass','fabric','plastic','drywall','fiberglass','composite','wallpaper','painted_masonry','other'];
 const ATTACHMENTS=['permanent','removable','freestanding'];
 const MAX_COUNT=10000;// basis.domain.counts.MAX_COUNT

 /* Every instance of `label` whose mask (its box without one) covers the normalized point,
  * highest score first, a manual box (no score) counting as 1 like the API's tap does
  * (basis.api.routes.objects.as_instance): the first is what a plain tap would take, the
  * rest are what cycling shows. */
 function candidatesUnder(items,label,u,v,Rle){
  const covers=i=>{
   if(i.mask&&i.mask.counts?.length){try{return Rle.contains(i.mask,i._bits||(i._bits=Rle.decode(i.mask)),u,v)}catch{return false}}
   const b=i.box;return u>=b.x1&&u<=b.x2&&v>=b.y1&&v<=b.y2;
  };
  return items.filter(i=>i.label===label&&covers(i)).sort((a,b)=>(b.score??1)-(a.score??1));
 }
 /* The next index when cycling `n` candidates by `step` (+1 / -1), wrapping around. */
 const cycle=(at,n,step=1)=>n?((at+step)%n+n)%n:0;

 /* How an instance's outline was made, for the panel and the toast. */
 function outlineText(o){
  if(o.source!=='manual')return o.mask?'Outline from SAM 3':'Box only';
  if(o.mask_source==='sam_box_prompt')return 'Outline from SAM 3 (your box)';
  if(o.mask_source==='candidate')return 'Outline from SAM 3';
  return 'Box only';
 }
 function addedMessage(result,labelName){
  const o=result.object,what=labelName(o.label).toLowerCase();
  if(!result.created)return `That ${what} is already counted.`;
  if(o.mask_source==='sam_box_prompt')return `Added a ${what}. Outline from SAM 3.`;
  if(o.mask_source==='candidate')return `Added a ${what} SAM 3 had found.`;
  return `Added your box as a ${what} (box only${o.mask_reason?': '+o.mask_reason:''}).`;
 }
 const isDuplicate=result=>!result.created&&Boolean(result.duplicate_of);

 /* Room suggestions by label (GET /rooms suggested_counts). */
 function suggestions(room){return new Map((room.suggested_counts||[]).map(s=>[s.label,s]))}
 /* Raised: the photos show more than the room counts. */
 const raised=s=>Boolean(s)&&s.suggested>s.inventory_count;
 /* The inventory row a raised count belongs on: the first row of that label not added by
  * an engineer (an added component's count is set where it was added). */
 function rowFor(room,label){return (room.inventory||[]).find(c=>c.label===label&&!c.added)||null}
 /* Labels the photos show that the room's inventory lacks. */
 function missingLabels(room){return (room.suggested_counts||[]).filter(s=>s.inventory_count===0)}

 function validCount(value){const n=Number(value);return Number.isInteger(n)&&n>=1&&n<=MAX_COUNT?n:null}
 /* A drag from (x0, y0) to (x1, y1), screen pixels, as a box normalized to the photo's
  * pixels on screen (`photo`: their left, top, width and height, the letterbox excluded),
  * corners sorted and clamped to the photo. */
 function dragBox(photo,x0,y0,x1,y1){
  const u=x=>Math.min(1,Math.max(0,(x-photo.left)/photo.width)),v=y=>Math.min(1,Math.max(0,(y-photo.top)/photo.height));
  return {x1:u(Math.min(x0,x1)),y1:v(Math.min(y0,y1)),x2:u(Math.max(x0,x1)),y2:v(Math.max(y0,y1))};
 }
 /* A count correction of an inventory row; merged with the row's pending edit, if any. */
 function countCorrection(room,item,count,pending,labelName){
  const current=pending?.after||{},after={...current,count};
  return {key:'component:'+item.component_id,entity_type:'component',entity_id:item.component_id,
   before:{label:item.label,material:item.material,attachment:item.attachment,count:item.count},after,reason:pending?.reason??null,
   title:`${labelName(item.label)} (${room.name})`,summary:Object.entries(after).map(([k,v])=>`${k} → ${v??'none'}`).join(', ')};
 }
 /* A component added to the room (entity_id is the room). One per room and label. */
 function roomComponentCorrection(room,{label,material,attachment,count},labelName){
  const after={label,count};if(material)after.material=material;if(attachment)after.attachment=attachment;
  return {key:`room_component:${room.id}:${label}`,entity_type:'room_component',entity_id:room.id,before:null,after,reason:null,
   title:room.name,summary:`add ${count} ${labelName(label).toLowerCase()}${material?' ('+material+')':''}`};
 }
 const api={MATERIALS,ATTACHMENTS,MAX_COUNT,candidatesUnder,cycle,dragBox,outlineText,addedMessage,isDuplicate,suggestions,raised,rowFor,missingLabels,validCount,countCorrection,roomComponentCorrection};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisManualAdd=api;
})(typeof globalThis==='object'?globalThis:this);
