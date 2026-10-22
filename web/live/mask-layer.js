/* Live mode: SAM 3 instances drawn over a photo on a canvas (the photo component page).
 *
 *   spotlight  one component: its instances bright with a soft green glow and a white
 *              edge, numbered pins (8 pins = a count of 8), the rest of the photo dimmed
 *              to grey. The default.
 *   overview   every counted instance tinted by component, for spotting what is missing.
 *   single     one instance (checking a tap-to-add result, or before deleting it).
 *
 * The canvas sizes itself to the photo as the page lays it out (ResizeObserver, any
 * object-fit), so the design can move the photo freely. Masks are decoded once and cached
 * cropped to their bounds (a photo can have 80+ candidates at 800 x 600); an instance
 * without a mask is drawn as its box. Clicks report the instance under the pointer (the
 * smallest, so a cabinet door wins over the cabinet run) or the empty photo point.
 */
(function(scope){
 'use strict';
 const GREEN=[163,230,140],FOREST='#223d30',WHITE=[255,255,255];
 const CANVAS_FILTER=typeof CanvasRenderingContext2D!=='undefined'&&'filter' in CanvasRenderingContext2D.prototype;
 const PALETTE=[[102,154,128],[214,140,69],[88,140,200],[196,92,120],[150,120,200],[205,180,70],[80,180,180],[170,110,80],[120,170,90],[220,110,90],[110,110,190],[190,150,190]];
 function labelColor(label,labels){const i=Math.max(0,labels.indexOf(label));return PALETTE[i%PALETTE.length]}
 function contentRect(img){
  // Where the photo's pixels are inside the <img> box, for object-fit contain/cover/fill.
  const w=img.clientWidth,h=img.clientHeight,nw=img.naturalWidth,nh=img.naturalHeight,fit=getComputedStyle(img).objectFit;
  let s;
  if(!nw||!nh)return {x:0,y:0,w,h};
  if(fit==='contain')s=Math.min(w/nw,h/nh);
  else if(fit==='scale-down')s=Math.min(1,w/nw,h/nh);
  else if(fit==='cover')s=Math.max(w/nw,h/nh);
  else return {x:0,y:0,w,h};// fill (the default): the photo is stretched to the box
  return {x:(w-nw*s)/2,y:(h-nh*s)/2,w:nw*s,h:nh*s};
 }
 function bitmap(width,height,data){
  const c=document.createElement('canvas');c.width=width;c.height=height;
  c.getContext('2d').putImageData(new ImageData(data,width,height),0,0);return c;
 }
 function crop(bits,mask,b){
  const w=b.x2-b.x1,h=b.y2-b.y1,out=new Uint8Array(w*h);
  for(let y=0;y<h;y++){const src=(b.y1+y)*mask.width+b.x1;out.set(bits.subarray(src,src+w),y*w)}
  return out;
 }
 function prepare(instance){
  if(instance._layer!==undefined)return instance._layer;
  const Rle=scope.BasisRle;let prepared=null;
  if(instance.mask&&instance.mask.counts?.length){
   try{
    const bits=Rle.decode(instance.mask),stats=Rle.stats(instance.mask,bits);
    if(stats.area){
     const b=stats.bounds,w=b.x2-b.x1,h=b.y2-b.y1,inner=crop(bits,instance.mask,b),edge=crop(Rle.edges(instance.mask,bits),instance.mask,b);
     prepared={bits,stats,mask:instance.mask,b,fill:bitmap(w,h,Rle.rgba(inner,WHITE)),edge:bitmap(w,h,Rle.rgba(edge,WHITE)),tint:new Map()};
    }
   }catch(error){console.warn('Mask could not be decoded; showing its box.',instance.id,error)}
  }
  instance._layer=prepared;return prepared;
 }
 function attach(img,{onPick,onHover}={}){
  const canvas=document.createElement('canvas');canvas.className='live-mask-canvas';canvas.setAttribute('aria-hidden','true');
  img.insertAdjacentElement('afterend',canvas);
  const host=img.parentElement;if(getComputedStyle(host).position==='static')host.style.position='relative';
  let view={instances:[],mode:'spotlight',focusLabel:null,selectedId:null,labels:[],hoverId:null},rect={x:0,y:0,w:0,h:0},dpr=1,destroyed=false;
  function place(){
   rect=contentRect(img);dpr=Math.min(2,globalThis.devicePixelRatio||1);
   Object.assign(canvas.style,{position:'absolute',left:img.offsetLeft+'px',top:img.offsetTop+'px',width:img.clientWidth+'px',height:img.clientHeight+'px',pointerEvents:'none'});
   canvas.width=Math.max(1,Math.round(img.clientWidth*dpr));canvas.height=Math.max(1,Math.round(img.clientHeight*dpr));
   draw();
  }
  const toCanvas=(u,v)=>[(rect.x+u*rect.w)*dpr,(rect.y+v*rect.h)*dpr];
  function drawInstance(ctx,inst,kind,color){
   const p=prepare(inst);
   if(p){
    const [x,y]=toCanvas(p.b.x1/p.mask.width,p.b.y1/p.mask.height),w=(p.b.x2-p.b.x1)/p.mask.width*rect.w*dpr,h=(p.b.y2-p.b.y1)/p.mask.height*rect.h*dpr;
    let source=kind==='edge'?p.edge:p.fill;
    if(color&&color!==WHITE){const key=kind+color.join();if(!p.tint.has(key)){const t=document.createElement('canvas');t.width=source.width;t.height=source.height;const c=t.getContext('2d');c.drawImage(source,0,0);c.globalCompositeOperation='source-in';c.fillStyle=`rgb(${color})`;c.fillRect(0,0,t.width,t.height);p.tint.set(key,t)}source=p.tint.get(key)}
    ctx.drawImage(source,x,y,w,h);
    if(kind==='edge'){ctx.drawImage(source,x+dpr*.8,y,w,h);ctx.drawImage(source,x,y+dpr*.8,w,h)}
    return;
   }
   const b=inst.box,[x1,y1]=toCanvas(b.x1,b.y1),[x2,y2]=toCanvas(b.x2,b.y2);
   ctx.save();if(kind==='edge'){ctx.strokeStyle=`rgb(${color||WHITE})`;ctx.lineWidth=2*dpr;ctx.setLineDash(inst.mask?[]:[6*dpr,4*dpr]);ctx.strokeRect(x1,y1,x2-x1,y2-y1)}else{ctx.fillStyle=`rgb(${color||WHITE})`;ctx.fillRect(x1,y1,x2-x1,y2-y1)}ctx.restore();
  }
  function pinAt(inst){
   const p=prepare(inst),b=inst.box;
   if(p){const u=p.stats.cx/p.mask.width,v=p.stats.cy/p.mask.height;return p.stats.fraction>0.015?[u,v]:[u,Math.max(0.03,p.b.y1/p.mask.height-0.035)]}
   return b.y2-b.y1>0.12?[(b.x1+b.x2)/2,(b.y1+b.y2)/2]:[(b.x1+b.x2)/2,Math.max(0.03,b.y1-0.035)];
  }
  function pin(ctx,u,v,text,active){
   const [x,y]=toCanvas(u,v),r=12*dpr;ctx.save();ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fillStyle=active?FOREST:'#fff';ctx.fill();ctx.lineWidth=2.5*dpr;ctx.strokeStyle=active?'#fff':FOREST;ctx.stroke();
   ctx.fillStyle=active?'#fff':FOREST;ctx.font=`600 ${11*dpr}px Inter, system-ui, sans-serif`;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(text,x,y+0.5*dpr);ctx.restore();
  }
  function highlight(ctx,group){
   // Dim and desaturate the photo, cut the instances out of the dim layer, then glow + edge.
   if(CANVAS_FILTER){ctx.save();ctx.filter='grayscale(1) brightness(0.45)';ctx.drawImage(img,rect.x*dpr,rect.y*dpr,rect.w*dpr,rect.h*dpr);ctx.restore()}
   else{ctx.fillStyle='rgba(18,28,22,.6)';ctx.fillRect(rect.x*dpr,rect.y*dpr,rect.w*dpr,rect.h*dpr)}// no canvas filter (Safari before 18): plain dim
   ctx.save();ctx.globalCompositeOperation='destination-out';group.forEach(i=>drawInstance(ctx,i,'fill'));ctx.restore();
   ctx.save();ctx.shadowColor=`rgba(${GREEN},.95)`;ctx.shadowBlur=14*dpr;group.forEach(i=>drawInstance(ctx,i,'edge',GREEN));ctx.restore();
   group.forEach(i=>drawInstance(ctx,i,'edge',WHITE));
  }
  function draw(){
   if(destroyed)return;const ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);
   if(!img.complete||!img.naturalWidth||!view.instances.length)return;
   const {mode,instances}=view;
   if(mode==='overview'){
    const counted=instances.filter(i=>i.counted);
    [...counted].sort((a,b)=>(b.area||0)-(a.area||0)).forEach(i=>{ctx.save();ctx.globalAlpha=.42;drawInstance(ctx,i,'fill',labelColor(i.label,view.labels));ctx.restore()});
    counted.forEach(i=>drawInstance(ctx,i,'edge',labelColor(i.label,view.labels)));
    const selected=instances.find(i=>i.id===view.selectedId);if(selected)drawInstance(ctx,selected,'edge',WHITE);
    return;
   }
   if(mode==='single'){
    const one=instances.find(i=>i.id===view.selectedId);if(!one)return;highlight(ctx,[one]);const [u,v]=pinAt(one),n=orderedFocus().findIndex(i=>i.id===one.id);pin(ctx,u,v,n>=0?String(n+1):'•',true);return;
   }
   const group=orderedFocus();if(!group.length)return;highlight(ctx,group);
   group.forEach((i,n)=>{const [u,v]=pinAt(i);pin(ctx,u,v,String(n+1),i.id===view.selectedId||i.id===view.hoverId)});
  }
  // The focused component's counted instances, numbered left to right (as the pins read).
  function orderedFocus(){return view.instances.filter(i=>i.label===view.focusLabel&&i.counted).sort((a,b)=>a.box.x1-b.box.x1||a.box.y1-b.box.y1)}
  function pick(u,v){
   const hits=view.instances.filter(i=>{const p=prepare(i);if(p)return scope.BasisRle.contains(p.mask,p.bits,u,v);const b=i.box;return u>=b.x1&&u<=b.x2&&v>=b.y1&&v<=b.y2});
   const visible=view.mode==='overview'?hits.filter(i=>i.counted):hits.filter(i=>i.counted||i.id===view.selectedId);
   const preferred=visible.filter(i=>i.label===view.focusLabel),pool=preferred.length&&view.mode!=='overview'?preferred:visible;
   return pool.sort((a,b)=>(a.area||1)-(b.area||1))[0]||null;
  }
  const pointToPhoto=e=>{const r=img.getBoundingClientRect();return [(e.clientX-r.left-rect.x)/rect.w,(e.clientY-r.top-rect.y)/rect.h]};
  const onClick=e=>{const [u,v]=pointToPhoto(e);if(u<0||u>1||v<0||v>1)return;onPick?.(pick(u,v),u,v,e)};
  const onMove=e=>{const [u,v]=pointToPhoto(e);const hit=u>=0&&u<=1&&v>=0&&v<=1?pick(u,v):null;img.style.cursor=hit?'pointer':'crosshair';if((hit?.id||null)!==view.hoverId){view.hoverId=hit?.id||null;onHover?.(hit);draw()}};
  img.addEventListener('click',onClick);img.addEventListener('mousemove',onMove);
  const observer=new ResizeObserver(place);observer.observe(img);
  img.addEventListener('load',place);if(img.complete)place();
  return {
   set(next){view={...view,...next};if(next.instances)view.labels=[...new Set(view.instances.map(i=>i.label))].sort();draw()},
   orderedFocus,labelColor:l=>labelColor(l,view.labels),redraw:place,
   destroy(){destroyed=true;observer.disconnect();img.removeEventListener('click',onClick);img.removeEventListener('mousemove',onMove);img.removeEventListener('load',place);canvas.remove()},
  };
 }
 scope.BasisMaskLayer={attach,labelColor:(l,labels)=>labelColor(l,labels),PALETTE};
})(globalThis);
