/* Read-only previews of the same localized objects shown in the photo workspace. */
(function(scope){
 'use strict';
 // Share the viewer's revision-keyed objects cache; bound requests without rebuilding
 // the whole page each time a thumbnail finishes. A waiting viewer still gets notified.
 function createLoader(cache,fetch,{concurrency=3,onSettled=()=>{}}={}){
  const queue=[];let active=0;
  function pump(){
   while(active<concurrency&&queue.length){
    const {key,source,entry,current}=queue.shift();active++;
    Promise.resolve().then(()=>{
     if(cache.get(key)!==entry||!current())throw Object.assign(new Error('Preview changed'),{name:'AbortError'});
     return fetch(source);
    }).then(data=>{entry.data=data;entry.done=true;entry.resolve(data)},error=>{
     entry.error=error;entry.done=true;if(cache.get(key)===entry)cache.delete(key);entry.reject(error);
    }).finally(()=>{active--;if(cache.get(key)===entry||!cache.has(key))onSettled(source);pump()});
   }
  }
  return (source,current=()=>true)=>{
   const key=`objects:${source.studyId}:${source.revision}:${source.docId}`,hit=cache.get(key);
   if(hit)return hit.done?(hit.error?Promise.reject(hit.error):Promise.resolve(hit.data)):hit.promise;
   const entry={done:false,data:undefined,error:null};
   entry.promise=new Promise((resolve,reject)=>Object.assign(entry,{resolve,reject}));
   cache.set(key,entry);queue.push({key,source,entry,current});pump();return entry.promise;
  };
 }
 if(typeof module==='object'&&module.exports){module.exports={createLoader};return}
 const Live=scope.BasisLive;if(!Live)return;
 const L=Live.state;
 const load=createLoader(L.cache,({studyId,docId,revision})=>Live.api.photoObjects(studyId,docId,revision),{
  onSettled:source=>{if(state.screen==='viewer'&&L.studyId===source.studyId&&L.docId===source.docId&&L.study?.revision===source.revision)Live.render()},
 });
 Live.componentPreview=(docId,alt='',cls='')=>`<span class="component-mask-preview ${cls}" data-component-preview="${esc(docId)}">${Live.photo(docId,alt)}<span class="component-preview-status">Loading outlines…</span></span>`;
 let clean=()=>{};
 Live.afterRender.push(()=>{
  clean();
  const hosts=[...root.querySelectorAll('[data-component-preview]')],study=L.study,user=L.user?.uid,firm=Live.firm?.()?.org_id;
  if(!hosts.length||!study||!user)return;
  let disposed=false;const layers=new Map(),pending=new Set();
  const current=()=>!disposed&&L.user?.uid===user&&Live.firm?.()?.org_id===firm&&L.studyId===study.id&&L.study?.revision===study.revision;
  const sessionCurrent=()=>L.user?.uid===user&&Live.firm?.()?.org_id===firm&&L.studyId===study.id&&L.study?.revision===study.revision;
  function status(host,text){const el=host.querySelector('.component-preview-status');if(el)el.textContent=text;host.title=text}
  async function show(host){
   if(!current()||!host.isConnected||layers.has(host)||pending.has(host))return;
   pending.add(host);
   try{
    const data=await load({studyId:study.id,revision:study.revision,docId:host.dataset.componentPreview},sessionCurrent);
    if(!current()||!host.isConnected)return;
    const img=host.querySelector('img');if(!img)return;
    if(!data.items.some(item=>item.counted)){status(host,'No outlines yet');return}
    const layer=BasisMaskLayer.attach(img,{interactive:false});
    layer.set({instances:data.items,mode:'overview'});layers.set(host,layer);
    host.querySelector('.component-preview-status')?.remove();host.title='Component outlines';
   }catch(error){if(current()&&host.isConnected&&error.name!=='AbortError')status(host,'Outlines unavailable')}
   finally{pending.delete(host)}
  }
  // Hidden room bodies and photos below the fold do not request/paint masks yet.
  const observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting)show(entry.target)},{rootMargin:'160px'});
  hosts.forEach(host=>observer.observe(host));
  clean=()=>{disposed=true;observer.disconnect();for(const layer of layers.values())layer.destroy();layers.clear()};
 });
})(globalThis);
