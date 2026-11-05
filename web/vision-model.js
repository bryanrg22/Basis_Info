/* Geometry and selection shared by the real and fixture review workspaces. */
(function(scope){
 'use strict';
 const LABELS=["carpet","hardwood_floor","vinyl_flooring","laminate_flooring","tile","wall_finish","ceiling_finish","baseboard","crown_molding","wainscoting","concrete_floor","half_wall","interior_door","exterior_door","window","blinds","window_treatment","stairs","railing","fireplace","closet_shelving","built_in_shelving","mirror","kitchen_cabinet","countertop","sink","appliance","refrigerator","dishwasher","stove","oven","microwave","range_hood","garbage_disposal","washer","dryer","bathroom_vanity","toilet","bathtub","shower","bathroom_accessory","light_fixture","ceiling_fan","outdoor_lighting","electrical_panel","electrical_outlet","hvac_unit","ac_condenser","hvac_vent","thermostat","water_heater","smoke_detector","sprinkler_system","security_system","doorbell","window_guard","furniture","desk","chair","filing_cabinet","garage_door","roof","siding","gutter","deck","patio","porch","pool","fence","retaining_wall","driveway","sidewalk","parking_lot","landscaping","signage","shed","concrete_pad","other"];
 const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
 function fit(width,height,naturalWidth,naturalHeight){
  if(![width,height,naturalWidth,naturalHeight].every(n=>Number.isFinite(n)&&n>0))return {x:0,y:0,width:0,height:0,scale:0};
  const scale=Math.min(width/naturalWidth,height/naturalHeight),w=Math.min(width,naturalWidth*scale),h=Math.min(height,naturalHeight*scale);
  return {x:(width-w)/2,y:(height-h)/2,width:w,height:h,scale};
 }
 function select(view,item){return {...view,focusLabel:item?.label||null,selectedId:item?.id||null}}
 function toggleType(view,label){return {...view,focusLabel:view.focusLabel===label?null:label,selectedId:null}}
 function under(items,u,v,Rle){
  return items.filter(i=>{if(!i.counted&&!i.exclusion)return false;
   if(i.polygon?.length){let inside=false;const p=i.polygon;for(let a=0,b=p.length-1;a<p.length;b=a++){if((p[a].y>v)!==(p[b].y>v)&&u<(p[b].x-p[a].x)*(v-p[a].y)/(p[b].y-p[a].y)+p[a].x)inside=!inside}return inside}
   if(i.mask?.counts?.length&&Rle){try{return Rle.contains(i.mask,i._bits||(i._bits=Rle.decode(i.mask)),u,v)}catch{/* recover with box */}}
   const b=i.box;return b&&u>=b.x1&&u<=b.x2&&v>=b.y1&&v<=b.y2;
  }).sort((a,b)=>((a.box.x2-a.box.x1)*(a.box.y2-a.box.y1))-((b.box.x2-b.box.x1)*(b.box.y2-b.box.y1))||a.id.localeCompare(b.id));
 }
 function cycle(hits,selected){if(!hits.length)return null;return hits[(hits.findIndex(i=>i.id===selected)+1)%hits.length]}
 /* Pins occupy cells, not overlapping centroids. Overflow is a single keyboard-accessible
    cluster picker. Leaders retain the relationship to each original object anchor. */
 function pins(anchors,width,height,size=36){
  if(width<size||height<size)return [];
  const cols=Math.floor(width/size),rows=Math.floor(height/size),capacity=cols*rows,used=new Set(),out=[];
  const overflow=anchors.length>capacity,visible=overflow?Math.max(0,capacity-1):anchors.length;
  for(let n=0;n<visible;n++){
   const p=anchors[n],ax=clamp(p.x,size/2,width-size/2),ay=clamp(p.y,size/2,height-size/2);let best=null;
   for(let y=0;y<rows;y++)for(let x=0;x<cols;x++){
    const k=y*cols+x;if(used.has(k))continue;const px=(x+.5)*width/cols,py=(y+.5)*height/rows,d=(px-ax)**2+(py-ay)**2;
    if(!best||d<best.d)best={k,x:px,y:py,d};
   }
   used.add(best.k);out.push({...p,x:best.x,y:best.y,anchorX:p.x,anchorY:p.y});
  }
  if(overflow){let k=0;while(used.has(k))k++;out.push({id:'cluster',x:(k%cols+.5)*width/cols,y:(Math.floor(k/cols)+.5)*height/rows,items:anchors.slice(visible)})}
  return out;
 }
 function zoomAt(view,next,x,y){const zoom=clamp(next,1,6),ratio=zoom/view.zoom;return {zoom,x:x-(x-view.x)*ratio,y:y-(y-view.y)*ratio}}
 function roomCount(room,label,corrections=new Map()){
  const total=(room.inventory||[]).reduce((n,i)=>{const a=corrections.get('component:'+i.component_id)?.after||{};return n+((a.label||i.label)===label?(a.count??i.count):0)},0);
  return [...corrections.values()].reduce((n,c)=>n+(c.entity_type==='room_component'&&c.entity_id===room.id&&c.after.label===label?c.after.count:0),total);
 }
 function sampleItems(doc){
  // The same illustrative photo fixture feeds review and public previews.
  const box=(id,label,x1,y1,x2,y2)=>({id,label,box:{x1,y1,x2,y2},mask:null,score:.9,source:'model',counted:true,area:(x2-x1)*(y2-y1)});
  return String(doc)==='0'?[
   box('light-1','light_fixture',.30,.245,.37,.32),box('light-2','light_fixture',.43,.255,.50,.33),box('light-3','light_fixture',.54,.30,.60,.36),
   box('cabinet-1','kitchen_cabinet',.04,.29,.31,.59),box('cabinet-2','kitchen_cabinet',.62,.29,.78,.72),box('cabinet-3','kitchen_cabinet',.64,.30,.71,.53),box('cabinet-4','kitchen_cabinet',.71,.30,.77,.53),
   box('counter-1','countertop',.23,.56,.66,.67),box('sink-1','sink',.46,.57,.53,.61),box('stools','chair',.37,.66,.65,.87)
  ]:[];
 }
 const api={sampleItems,LABELS,fit,select,toggleType,under,cycle,pins,zoomAt,clamp,roomCount};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisVision=api;
})(globalThis);
