(function(scope){
 'use strict';
 function baseUrl(value){const u=new URL(value);if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.search||u.hash||u.pathname!=='/')throw new Error('Use an HTTP(S) API origin without credentials or a path.');return u.origin}
 // The attachment's file name from a Content-Disposition header; null when it names none.
 function attachmentName(header){const m=/filename="([^"]+)"/.exec(header||'');return m?m[1]:null}
 function createClient({base,fetchImpl=globalThis.fetch.bind(globalThis),getToken=async()=>null,trustedOrigin=null,timeoutMs=8000,now=()=>performance.now()}={}){
  const origin=baseUrl(base);
  // text: true returns the body as text with its content type and attachment name (a run's
  // record file) instead of parsing JSON; a problem body is still parsed into data.
  async function read(path,{authorized=false,signal,text=false}={}){
   const controller=new AbortController(),abort=()=>controller.abort();
   if(signal?.aborted)controller.abort();else signal?.addEventListener('abort',abort,{once:true});
   const started=now(),timer=setTimeout(abort,timeoutMs);
   try{
    const headers={Accept:text?'*/*, application/problem+json':'application/json, application/problem+json'};
    if(authorized){
     if(trustedOrigin!==null&&origin!==baseUrl(trustedOrigin))return {status:0,data:null,error:'untrusted_origin'};
     let token;
     try{token=await Promise.race([getToken(),new Promise((_,reject)=>{const fail=()=>reject(Object.assign(new Error('Aborted'),{name:'AbortError'}));if(controller.signal.aborted)fail();else controller.signal.addEventListener('abort',fail,{once:true})})])}
     catch(error){return {status:0,data:null,error:error?.name==='AbortError'?'timeout':'auth'}}
     if(!token)return {status:401,data:null};
     if(controller.signal.aborted)return {status:0,data:null,error:'timeout'};
     headers.Authorization=`Bearer ${token}`;
    }
    const response=await fetchImpl(origin+path,{method:'GET',headers,signal:controller.signal,cache:'no-store',credentials:'omit',redirect:'error'});
    const contentType=response.headers?.get?.('content-type')||'';
    if(text&&response.status===200){
     return {status:200,data:null,text:await response.text(),contentType,filename:attachmentName(response.headers?.get?.('content-disposition')),latency_ms:Math.max(0,now()-started)};
    }
    let data=null;try{data=await response.json()}catch{}
    return {status:response.status,data,latency_ms:Math.max(0,now()-started)};
   }catch(error){return {status:0,data:null,error:error?.name==='AbortError'?'timeout':'network'}}
   finally{clearTimeout(timer);signal?.removeEventListener('abort',abort)}
  }
  // GET /v1/ops/eval-runs: `component` repeats (one step lists several), the rest are single.
  function evalRunsPath({component=[],limit=50,cursor=null,model=null,labels=null,split=null,status=null}={}){
   const q=new URLSearchParams();
   for(const c of Array.isArray(component)?component:[component])if(c)q.append('component',String(c));
   q.set('limit',String(limit));
   for(const [k,v] of Object.entries({cursor,model,labels,split,status}))if(v)q.set(k,String(v));
   return '/v1/ops/eval-runs?'+q.toString();
  }
  // A run file is fetched through the same authorized client (an <a href> cannot carry the
  // bearer token) and saved by the page; `path` is the API's own relative `files` link.
  function evalRunFile(path,options){
   if(typeof path!=='string'||!path.startsWith('/v1/ops/eval-runs/'))return Promise.resolve({status:0,data:null,error:'network'});
   return read(path,{...options,authorized:true,text:true});
  }
  return {health:options=>read('/health',options),diagnostics:options=>read('/v1/ops/health',{...options,authorized:true}),evalRuns:(query,options)=>read(evalRunsPath(query),{...options,authorized:true}),evalRunFile,evalRunsPath};
 }
 const api={baseUrl,createClient,attachmentName};if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisHealthClient=api;
})(globalThis);
