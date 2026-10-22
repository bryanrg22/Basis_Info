/* Live mode: a small client for the Basis /v1 API (docs/api/openapi.json).
 *
 * * Every request carries the signed-in user's Firebase ID token (Authorization: Bearer).
 * * POST and DELETE carry an Idempotency-Key. A caller that retries the same action passes
 *   the same key (the API replays the first answer instead of acting twice).
 * * Errors are application/problem+json: thrown as ApiError with status, type, title,
 *   detail, the field errors and the request id (quote it when reporting a bug).
 * * pollWorkflow polls GET .../workflow with backoff: quick while something changes,
 *   slower while nothing does, paused while the tab is hidden.
 */
(function(scope){
 'use strict';
 class ApiError extends Error{
  constructor(status,problem={},fallback=''){
   super(problem.detail||problem.title||fallback||`Request failed (${status})`);
   this.name='ApiError';this.status=status;this.type=problem.type||'about:blank';this.title=problem.title||'';
   this.detail=problem.detail||'';this.errors=problem.errors||[];this.requestId=problem.request_id||null;
  }
  // The problem type's last path segment, e.g. "stale", "gate-not-open", "upload-missing".
  get kind(){return String(this.type).split('/').pop()}
 }
 const newKey=()=>(globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(16).slice(2)}`);
 function createClient({baseUrl,getToken,fetchImpl=globalThis.fetch?.bind(globalThis),onUnauthorized}={}){
  if(!baseUrl)throw new Error('baseUrl is required');
  const base=baseUrl.replace(/\/+$/,'');
  function url(path,query){
   const u=new URL(base+path);
   for(const [k,v] of Object.entries(query||{}))if(v!==undefined&&v!==null&&v!=='')u.searchParams.set(k,String(v));
   return u.toString();
  }
  async function request(method,path,{query,body,key,raw=false,signal,headers:extra}={}){
   const headers={Accept:'application/json, application/problem+json',...extra},token=getToken?await getToken():null;
   if(token)headers.Authorization=`Bearer ${token}`;
   if(method==='POST'||method==='DELETE')headers['Idempotency-Key']=key||newKey();
   if(body!==undefined)headers['Content-Type']='application/json';
   let response;
   try{response=await fetchImpl(url(path,query),{method,headers,body:body===undefined?undefined:JSON.stringify(body),signal})}
   catch(error){if(error?.name==='AbortError')throw error;throw new ApiError(0,{title:'Network error',detail:'The Basis API could not be reached. Check that it is running and that this page’s origin is allowed (CORS_ALLOWED_ORIGINS).'})}
   if(raw&&response.ok)return response;
   if(response.status===304)return null;
   const type=response.headers.get('content-type')||'',text=await response.text();
   let data=null;if(text&&/json/.test(type)){try{data=JSON.parse(text)}catch{data=null}}
   if(!response.ok){
    const error=new ApiError(response.status,data||{},text.slice(0,200));
    if(response.status===401&&onUnauthorized)onUnauthorized(error);
    throw error;
   }
   return data;
  }
  // Every page of a cursor-paginated list ({items, next_cursor}).
  async function listAll(path,query={},{limit=200,maxPages=50}={}){
   const items=[];let cursor=null,first=null;
   for(let page=0;page<maxPages;page++){
    const data=await request('GET',path,{query:{...query,limit,cursor}});
    if(!first)first=data;items.push(...data.items);cursor=data.next_cursor;if(!cursor)break;
   }
   return {...first,items,next_cursor:null};
  }
  const study=id=>`/v1/studies/${encodeURIComponent(id)}`;
  const org=id=>`/v1/orgs/${encodeURIComponent(id)}`;
  return {
   request,listAll,url,
   // Invite-only firms: who you are, your firms and invitations; owner administration.
   me:()=>request('GET','/v1/me'),
   members:orgId=>request('GET',`${org(orgId)}/members`),
   setMemberRole:(orgId,userId,role,key)=>request('POST',`${org(orgId)}/members/${encodeURIComponent(userId)}/role`,{body:{role},key}),
   removeMember:(orgId,userId,key)=>request('DELETE',`${org(orgId)}/members/${encodeURIComponent(userId)}`,{key}),
   invitations:orgId=>request('GET',`${org(orgId)}/invitations`),
   invite:(orgId,body,key)=>request('POST',`${org(orgId)}/invitations`,{body,key}),
   revokeInvitation:(orgId,id,key)=>request('DELETE',`${org(orgId)}/invitations/${encodeURIComponent(id)}`,{key}),
   acceptInvitation:(id,key)=>request('POST',`/v1/invitations/${encodeURIComponent(id)}/accept`,{key}),
   declineInvitation:(id,key)=>request('POST',`/v1/invitations/${encodeURIComponent(id)}/decline`,{key}),
   listStudies:()=>listAll('/v1/studies',{},{limit:100}),
   getStudy:id=>request('GET',study(id)),
   createStudy:(body,key)=>request('POST','/v1/studies',{body,key}),
   archiveStudy:(id,key)=>request('DELETE',study(id),{key}),
   registerDocument:(id,body,key)=>request('POST',`${study(id)}/documents`,{body,key}),
   completeDocument:(id,doc,key)=>request('POST',`${study(id)}/documents/${encodeURIComponent(doc)}/complete`,{key}),
   documentContent:(id,doc,signal)=>request('GET',`${study(id)}/documents/${encodeURIComponent(doc)}/content`,{raw:true,signal}),
   start:(id,key)=>request('POST',`${study(id)}/start`,{key}),
   retry:(id,key)=>request('POST',`${study(id)}/retry`,{key}),
   cancel:(id,reason,key)=>request('POST',`${study(id)}/cancel`,{body:reason?{reason}:undefined,key}),
   workflow:(id,signal)=>request('GET',`${study(id)}/workflow`,{signal}),
   fields:(id,revision)=>listAll(`${study(id)}/fields`,{revision}),
   rooms:(id,revision)=>listAll(`${study(id)}/rooms`,{revision}),
   assets:(id,revision)=>listAll(`${study(id)}/assets`,{revision}),
   evidence:(id,assetId)=>listAll(`${study(id)}/evidence`,{asset_id:assetId},{limit:50}),
   photoObjects:(id,doc,revision)=>request('GET',`${study(id)}/photos/${encodeURIComponent(doc)}/objects`,{query:{revision}}),
   addObject:(id,doc,body,key)=>request('POST',`${study(id)}/photos/${encodeURIComponent(doc)}/objects`,{body,key}),
   deleteObject:(id,doc,objectId,key)=>request('DELETE',`${study(id)}/photos/${encodeURIComponent(doc)}/objects/${encodeURIComponent(objectId)}`,{key}),
   decide:(id,body,key)=>request('POST',`${study(id)}/decisions`,{body,key}),
  };
 }
 /* Poll until stop() or shouldStop(snapshot). Backoff doubles from initialMs up to maxMs
  * while the snapshot is unchanged and resets when it changes or the tab becomes visible.
  * Errors back off the same way and are reported through onError; polling continues. */
 function pollWorkflow(fetchSnapshot,{onUpdate,onError,shouldStop,initialMs=1500,maxMs=15000,timers=globalThis,doc=globalThis.document}={}){
  let delay=initialMs,last=null,timer=null,stopped=false,inflight=null;
  const schedule=()=>{if(!stopped)timer=timers.setTimeout(tick,delay)};
  async function tick(){
   timer=null;if(stopped)return;
   if(doc?.hidden){schedule();return}
   inflight=new AbortController();
   try{
    const snapshot=await fetchSnapshot(inflight.signal),fingerprint=JSON.stringify(snapshot?.status??snapshot);
    if(stopped)return;
    if(fingerprint!==last){last=fingerprint;delay=initialMs;onUpdate?.(snapshot)}else delay=Math.min(maxMs,delay*2);
    if(shouldStop?.(snapshot)){stopped=true;return}
   }catch(error){if(stopped||error?.name==='AbortError')return;delay=Math.min(maxMs,delay*2);onError?.(error)}
   finally{inflight=null}
   schedule();
  }
  const onVisible=()=>{if(!doc.hidden&&!stopped&&timer!==null){timers.clearTimeout(timer);delay=initialMs;tick()}};
  doc?.addEventListener?.('visibilitychange',onVisible);
  tick();
  return {stop(){stopped=true;if(timer!==null)timers.clearTimeout(timer);inflight?.abort();doc?.removeEventListener?.('visibilitychange',onVisible)},get delay(){return delay}};
 }
 const api={ApiError,createClient,pollWorkflow,newKey};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisApi=api;
})(typeof globalThis==='object'?globalThis:this);
