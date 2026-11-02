(function(scope){
 'use strict';
 function baseUrl(value){const u=new URL(value);if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.search||u.hash||u.pathname!=='/')throw new Error('Use an HTTP(S) API origin without credentials or a path.');return u.origin}
 function createClient({base,fetchImpl=globalThis.fetch.bind(globalThis),getToken=async()=>null,trustedOrigin=null,timeoutMs=8000,now=()=>performance.now()}={}){
  const origin=baseUrl(base);
  async function read(path,{authorized=false,signal}={}){
   const controller=new AbortController(),abort=()=>controller.abort();
   if(signal?.aborted)controller.abort();else signal?.addEventListener('abort',abort,{once:true});
   const started=now(),timer=setTimeout(abort,timeoutMs);
   try{
    const headers={Accept:'application/json, application/problem+json'};
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
    let data=null;try{data=await response.json()}catch{}
    return {status:response.status,data,latency_ms:Math.max(0,now()-started)};
   }catch(error){return {status:0,data:null,error:error?.name==='AbortError'?'timeout':'network'}}
   finally{clearTimeout(timer);signal?.removeEventListener('abort',abort)}
  }
  return {health:options=>read('/health',options),diagnostics:options=>read('/v1/ops/health',{...options,authorized:true})};
 }
 const api={baseUrl,createClient};if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisHealthClient=api;
})(globalThis);
