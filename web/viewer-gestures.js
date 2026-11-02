/* Trackpad pinch is Ctrl+wheel in Chromium/Firefox and GestureEvent in Safari.
 * Only the viewing surface consumes zoom; ordinary scrolling stays native. */
(function(scope){
 'use strict';
 const clamp=(n,min,max)=>Math.max(min,Math.min(max,n));
 function wheelPixels(e,height=800){const unit=e.deltaMode===1?16:e.deltaMode===2?height:1;return {x:e.deltaX*unit||0,y:e.deltaY*unit||0}}
 function factor(e,height){return Math.exp(-clamp(wheelPixels(e,height).y,-100,100)*.01)}
 function anchor(content,point){const r=content.getBoundingClientRect();return {x:(point.x-r.left)/r.width,y:(point.y-r.top)/r.height,point}}
 function restore(stage,content,a){const r=content.getBoundingClientRect();stage.scrollLeft+=r.left+a.x*r.width-a.point.x;stage.scrollTop+=r.top+a.y*r.height-a.point.y}
 function center(stage){const r=stage.getBoundingClientRect();return {x:r.left+stage.clientWidth/2,y:r.top+stage.clientHeight/2}}
 function attach(stage,{zoom,canZoom=()=>true,pan}){
  let gesture=null;
  const point=e=>Number.isFinite(e.clientX)&&Number.isFinite(e.clientY)?{x:e.clientX,y:e.clientY}:center(stage);
  function wheel(e){
   if(e.ctrlKey){
    if(!canZoom())return;e.preventDefault();
    if(!gesture)zoom(factor(e,stage.clientHeight),point(e));
   }else if(pan){const d=wheelPixels(e,stage.clientHeight);if(pan(d))e.preventDefault()}
  }
  function start(e){if(!canZoom())return;e.preventDefault();gesture={scale:Number(e.scale)||1}}
  function change(e){if(!gesture)return;e.preventDefault();const scale=Number(e.scale);if(!Number.isFinite(scale)||scale<=0)return;const f=scale/gesture.scale;gesture.scale=scale;if(canZoom())zoom(f,point(e))}
  function end(e){if(gesture)e.preventDefault();gesture=null}
  const handlers={wheel,gesturestart:start,gesturechange:change,gestureend:end};
  for(const [name,handler] of Object.entries(handlers))stage.addEventListener(name,handler,{passive:false});
  return ()=>{for(const [name,handler] of Object.entries(handlers))stage.removeEventListener(name,handler);gesture=null};
 }
 // Scale a source page and its text highlights together. Reserve its scaled dimensions
 // so native two-finger scrolling can reach every edge, including when centered.
 function scaledScroll(stage,content,{min=1,max=3,onSettle=()=>{}}={}){
  const width=content.offsetWidth,height=content.offsetHeight,space=content.ownerDocument.createElement('div');
  space.className='viewer-zoom-space';Object.assign(space.style,{width:width+'px',height:height+'px',margin:'0 auto',flex:'none'});
  content.before(space);space.append(content);content.style.margin='0';content.style.transformOrigin='0 0';let scale=1,timer;
  const detach=attach(stage,{zoom:(factor,point)=>{
   const next=clamp(scale*factor,min,max);if(next===scale)return;
   const a=anchor(content,point);scale=next;space.style.width=width*scale+'px';space.style.height=height*scale+'px';content.style.transform=`scale(${scale})`;restore(stage,content,a);
   stage.dataset.zoom=String(scale);clearTimeout(timer);timer=setTimeout(()=>onSettle(scale),120);
  }});
  return ()=>{detach();clearTimeout(timer)};
 }
 const api={clamp,wheelPixels,factor,anchor,restore,center,attach,scaledScroll};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisGestures=api;
})(typeof globalThis==='object'?globalThis:this);
