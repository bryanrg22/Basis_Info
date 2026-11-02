/* Authenticated/local appraisal source resolution; no document leaves this browser. */
(function(scope){
 'use strict';
 const Live=scope.BasisLive;if(!Live)return;
 const A=scope.BasisAppraisalSource,cache=new Map();let zipPromise,generation=0;
 function loadZip(){
  if(scope.zip)return Promise.resolve(scope.zip);
  return zipPromise??=new Promise((resolve,reject)=>{
   const script=document.createElement('script');script.src='assets/vendor/zipjs/zip.min.js';
   script.onload=()=>resolve(scope.zip);script.onerror=()=>{zipPromise=null;script.remove();reject(new Error('The ZIP preview reader could not be loaded.'))};document.head.append(script);
  });
 }
 async function inspect(blob){return A.inspect(blob,{zip:await A.sniff(blob)==='zip'?await loadZip():null})}
 function clear(){generation++;for(const result of cache.values())result.then(r=>{if(r.owned)URL.revokeObjectURL(r.url)},()=>{});cache.clear()}
 let account=Live.state.user?.uid;BasisAuth.onChange(user=>{if(user?.uid!==account){account=user?.uid;clear()}});
 Live.appraisalSource=(doc,study=Live.state.study)=>{
  const companion=A.companion(doc,study.documents||[]),ticket=generation,key=[account,study.id,doc.id,doc.uploaded_at,companion?.id,companion?.uploaded_at].join(':');
  if(cache.has(key))return cache.get(key);
  const promise=(async()=>{
   const originalUrl=await Live.blobUrl(doc.id,study.id),blob=await (await fetch(originalUrl)).blob(),source=await inspect(blob);
   if(ticket!==generation)throw Object.assign(new Error('Source session changed'),{name:'AbortError'});
   if(source.kind==='xml'&&companion){
    const url=await Live.blobUrl(companion.id,study.id);
    if(await A.sniff(await (await fetch(url)).blob())!=='pdf')throw new Error('The matching report is not a readable PDF.');
    return {...source,url,filename:companion.filename,originalUrl};
   }
   const owned=source.kind==='zip'&&!!source.pdf,url=owned?URL.createObjectURL(source.pdf):source.pdf?originalUrl:null;
   return {...source,pdf:undefined,url,owned,filename:source.filename||doc.filename,originalUrl};
  })();
  cache.set(key,promise);promise.catch(()=>{if(cache.get(key)===promise)cache.delete(key)});return promise;
 };
 Live.isAppraisalSource=doc=>doc.kind==='appraisal'&&A.supported(doc);
 Live.inspectAppraisal=inspect;
 Live.previewLocalAppraisal=async(item,local)=>{
  let url;
  try{
   const source=await inspect(local.file);
   if(!source.pdf){toast(source.message);return}
   url=source.kind==='pdf'?local.url:URL.createObjectURL(source.pdf);
   await openPdfPreview({name:source.filename||item.name,url,description:source.kind==='zip'?'PDF from local UAD package · not uploaded':undefined,onClose:source.kind==='zip'?()=>URL.revokeObjectURL(url):null});
  }catch(error){if(url&&url!==local.url)URL.revokeObjectURL(url);toast(error.message)}
 };
 root.addEventListener('click',event=>{
  const button=event.target.closest('[data-upload-appraisal-preview]');if(!button)return;
  const item=state.uploadItems.find(f=>f.key===button.dataset.uploadAppraisalPreview),local=localStudyFiles.get(button.dataset.uploadAppraisalPreview);
  if(item&&local)Live.previewLocalAppraisal(item,local);
 });
})(globalThis);
