/* First-page previews use the authenticated original document, never sample imagery. */
(function(scope){
 'use strict';
 const isPdf=doc=>/^application\/pdf(?:\s*;|$)/i.test(doc.content_type||'')||/\.pdf$/i.test(doc.filename||'');
 const cancelled=()=>Object.assign(new Error('Preview session changed'),{name:'AbortError'});

 // Bound PDF workers and retain only a small in-memory set of rendered thumbnails.
 function createCache(render,{concurrency=2,limit=24}={}){
  const entries=new Map(),queue=[];let active=0,generation=0;
  function pump(){
   while(active<concurrency&&queue.length){
    const job=queue.shift();active++;
    Promise.resolve().then(()=>render(job.source)).then(result=>{
     if(job.generation!==generation)throw cancelled();
     job.result=result;job.resolve(result);
     const completed=[...entries].filter(([,entry])=>entry.result);
     while(completed.length>limit)entries.delete(completed.shift()[0]);
    }).catch(error=>{if(entries.get(job.key)===job)entries.delete(job.key);job.reject(error)}).finally(()=>{active--;pump()});
   }
  }
  return {
   get(key,source){
    if(entries.has(key)){const hit=entries.get(key);entries.delete(key);entries.set(key,hit);return hit.promise}
    const job={key,source,generation};job.promise=new Promise((resolve,reject)=>Object.assign(job,{resolve,reject}));
    entries.set(key,job);queue.push(job);pump();return job.promise;
   },
   peek:key=>entries.get(key)?.result,
   clear(){generation++;entries.clear();for(const job of queue.splice(0))job.reject(cancelled())},
  };
 }

 async function renderFirstPage(pdfjs,options,createCanvas=()=>document.createElement('canvas')){
  const task=pdfjs.getDocument(options);let canvas;
  try{
   const pdf=await task.promise,page=await pdf.getPage(1),base=page.getViewport({scale:1});
   const viewport=page.getViewport({scale:Math.min(1280/base.width,1680/base.height)});
   canvas=createCanvas();canvas.width=Math.ceil(viewport.width);canvas.height=Math.ceil(viewport.height);
   await page.render({canvasContext:canvas.getContext('2d'),viewport,background:'#ffffff'}).promise;
   return {src:canvas.toDataURL('image/png'),pages:pdf.numPages};
  }finally{
   if(canvas){canvas.width=0;canvas.height=0}
   await task.destroy();
  }
 }
 const helpers={isPdf,createCache,renderFirstPage};
 if(typeof module==='object'&&module.exports){module.exports=helpers;return}
 const Live=scope.BasisLive;if(!Live)return;
 Live.isPdf=isPdf;
 const cache=createCache(async({doc,studyId,study})=>{
  const source=Live.isAppraisalSource(doc)?await Live.appraisalSource(doc,study):{url:await Live.blobUrl(doc.id,studyId)};
  if(!source.url)return {message:source.message,kind:source.kind};
  const pdfjs=await loadPdfModule();return renderFirstPage(pdfjs,pdfDocumentOptions(source.url));
 });
 let account=Live.state.user?.uid;
 BasisAuth.onChange(user=>{if(user?.uid!==account){account=user?.uid;cache.clear()}});
 Live.pdfThumbnail=doc=>`<div class="file-document-thumbnail live-pdf-thumbnail" data-live-pdf-thumbnail="${esc(doc.id)}" aria-busy="${Boolean(doc.uploaded_at)}"><span class="live-pdf-thumbnail-status" role="status">${doc.uploaded_at?'<span class="live-spinner" aria-hidden="true"></span>Loading preview…':'Finish uploading to preview'}</span></div>`;
 Live.afterRender.push(()=>{
  const study=Live.state.study,uid=Live.state.user?.uid;if(!study||!uid)return;
  for(const host of root.querySelectorAll('[data-live-pdf-thumbnail]')){
   if(host.dataset.requested)continue;
   const doc=study.documents?.find(d=>d.id===host.dataset.livePdfThumbnail);
   if(!doc?.uploaded_at||!(isPdf(doc)||Live.isAppraisalSource(doc)))continue;
   host.dataset.requested='true';
   const current=()=>host.isConnected&&Live.state.user?.uid===uid;
   const show=preview=>{
    if(!current())return;
    if(preview.message){host.textContent=preview.message;host.setAttribute('aria-busy','false');return}
    const img=document.createElement('img');img.src=preview.src;img.alt=`First page of ${doc.filename}`;
    const badge=document.createElement('span');badge.className='live-pdf-thumbnail-badge';badge.textContent=`PDF · ${preview.pages} ${preview.pages===1?'page':'pages'}`;
    host.replaceChildren(img,badge);host.dataset.loaded='true';host.setAttribute('aria-busy','false');
   };
   const companion=BasisAppraisalSource.format(doc)==='xml'?BasisAppraisalSource.companion(doc,study.documents||[]):null;
   const key=[uid,study.id,doc.id,doc.uploaded_at,companion?.id,companion?.uploaded_at].join(':'),ready=cache.peek(key);
   if(ready){show(ready);continue}
   cache.get(key,{doc,studyId:study.id,study}).then(show,error=>{
    if(!current()||error.name==='AbortError')return;
    host.setAttribute('aria-busy','false');host.dataset.failed='true';
    const message=host.querySelector('[role=status]');
    message.textContent=error.name==='PasswordException'?'Password-protected PDF. Download to open.':'Preview unavailable. Open the PDF to retry.';
   });
  }
 });
})(globalThis);
