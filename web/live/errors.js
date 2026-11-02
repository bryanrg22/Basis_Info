/* Error capture: every error a user hits in the browser is recorded in extreme detail and
 * sent to POST /v1/client-errors (docs/api/CLIENT_ERRORS.md), so a bug can be diagnosed later
 * from the record alone. Loaded first, before the design scripts, so an error at load is
 * captured too; it depends on nothing at install time and reads BASIS_CONFIG, BasisAuth and
 * BasisLive lazily when an event is built or sent. Mock mode records breadcrumbs and events
 * but never sends (there is no API).
 *
 * What is captured (the kinds): window 'error' (uncaught exceptions, and resource load
 * failures), 'unhandledrejection', console.error, failed /v1 calls reported by the API client
 * (createClient's observe hook), and explicit Basis.reportError(err, context) from a screen.
 *
 * What an event carries: an event id and the client's clock (ISO 8601 with offset) and
 * monotonic time since load; the signed-in user (uid, email, role), the firm (id, name), the
 * study (id, revision, stage, status) and the page (hash route, screen); the error's type,
 * message, stack and first source location; the failed request (method, route with ids
 * redacted, status, code, request id, latency, attempt, body field names only); the last 50
 * user actions as breadcrumbs (clicks on data-live actions, navigation, API calls with status
 * and duration, gate decisions, console errors); the API build (/health git_sha) and this
 * module's asset version; the browser (user agent, platform, language, viewport, pixel
 * ratio, online, timezone, connection, visibility).
 *
 * What is never recorded: an Authorization header or bearer token, a request body (field
 * names only), a query string's values, a key whose name looks like a secret (masked), a
 * url's ids (replaced by {id}). The shape follows Sentry's event payload (event_id, level,
 * exception type/value/stack, breadcrumbs, user, release) with OpenTelemetry's names where
 * they fit; the design and the limits are EVAL_REPORT section 65.
 *
 * Transport: batches of up to 20 events, at most one batch every 5 s, fetch with
 * keepalive (so a batch survives the page unloading; sendBeacon cannot carry the bearer
 * token), an Idempotency-Key per batch (a retry replays), retried on 429/5xx/network with
 * backoff, dropped on any other 4xx. At most 30 events a minute leave the page; the rest are
 * counted as `dropped` on the next event, so a render loop that throws on every frame cannot
 * flood the API. Identical errors within 5 s are one event with `repeats`. Nothing this
 * module does can itself produce an event (its own route and console use are excluded).
 */
(function(scope){
 'use strict';
 const VERSION='client-errors-1';
 const ROUTE='/v1/client-errors';
 const LIMITS={maxBreadcrumbs:50,maxPerMinute:30,queueMax:50,batchSize:20,flushMs:5000,dedupeMs:5000,retryDelaysMs:[2000,8000,32000],message:4000,stack:16000,short:500,contextChars:16000,breadcrumbChars:2000};
 const SENSITIVE=/(?:key|token|secret|password|authorization|cookie|session)/i;
 const UUID=/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
 const LONG_HEX=/\b[0-9a-f]{24,}\b/gi;
 const LONG_NUMBER=/\b\d{6,}\b/g;
 const BEARER=/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;
 const JWT=/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g;
 const cut=(s,n)=>{s=String(s??'');return s.length>n?s.slice(0,n-1)+'…':s};
 // Ids out of a path or route: UUIDs, long hex, long numbers become {id}; a query string
 // keeps its keys and loses its values.
 function redactPath(path){
  if(path===null||path===undefined)return null;
  let s=String(path);
  const q=s.indexOf('?');let query='';
  if(q>=0){query=s.slice(q+1);s=s.slice(0,q)}
  s=s.replace(UUID,'{id}').replace(LONG_HEX,'{id}').replace(LONG_NUMBER,'{id}');
  if(query){const keys=query.split('&').map(p=>p.split('=')[0]).filter(Boolean);if(keys.length)s+='?'+keys.map(k=>k+'=…').join('&')}
  return cut(s,LIMITS.short);
 }
 // Free text that may quote a token or a URL with ids (an error message, a stack line).
 function redactText(text,max=LIMITS.message){
  if(text===null||text===undefined)return null;
  return cut(String(text).replace(BEARER,'Bearer ***').replace(JWT,'***').replace(/\x00/g,''),max);
 }
 // A caller's context object: secret-looking keys masked, bounded depth, size and strings.
 function sanitize(value,depth=3){
  if(value===null||value===undefined)return value;
  if(typeof value==='string')return redactText(value,LIMITS.short);
  if(typeof value==='number')return Number.isFinite(value)?value:String(value);
  if(typeof value==='boolean')return value;
  if(value instanceof Error)return {name:value.name,message:redactText(value.message,LIMITS.short)};
  if(depth<=0)return Array.isArray(value)?'[Array]':'[Object]';
  if(Array.isArray(value))return value.slice(0,20).map(v=>sanitize(v,depth-1));
  if(typeof value==='object'){const out={};let n=0;for(const [k,v] of Object.entries(value)){if(n++>=50)break;out[cut(k,100)]=SENSITIVE.test(k)?'***':sanitize(v,depth-1)}return out}
  return String(value);
 }
 // A request body's field names, never its values (the intake, a form, a correction).
 function safeKeys(body){
  if(body===null||body===undefined)return null;
  if(typeof body==='string'){try{body=JSON.parse(body)}catch{return ['<text>']}}
  if(Array.isArray(body))return ['[]'];
  if(typeof body!=='object')return [typeof body];
  return Object.keys(body).slice(0,100).map(k=>cut(k,100));
 }
 // The first source location in a V8 ("at fn (file:line:col)", "at file:line:col") or
 // Firefox/Safari ("fn@file:line:col") stack.
 function parseStack(stack){
  if(!stack)return null;
  for(const raw of String(stack).split('\n')){
   const line=raw.trim();
   let m=/^at\s+(?:(.*?)\s+\()?(.+?):(\d+):(\d+)\)?$/.exec(line)||/^(?:(.*?)@)?(.+?):(\d+):(\d+)$/.exec(line);
   if(m&&!/^https?:$/.test(m[2])){const file=m[2].replace(/^\(/,'');return {function:m[1]?cut(m[1],LIMITS.short):null,file:redactPath(file),line:Number(m[3]),column:Number(m[4])}}
  }
  return null;
 }
 const fingerprint=e=>[e.kind,e.error_type||'',cut(e.message,200),(e.stack||'').split('\n').slice(0,2).join('|'),e.http?.route||'',e.http?.status||''].join('\u001f');
 function describeError(err){
  if(err instanceof Error||(err&&typeof err==='object'&&'message' in err&&('stack' in err||'name' in err)))return {error_type:cut(err.name||'Error',LIMITS.short),message:redactText(err.message||String(err))||'(no message)',stack:redactText(err.stack,LIMITS.stack)};
  if(typeof err==='string')return {error_type:null,message:redactText(err)||'(empty)',stack:null};
  let text;try{text=JSON.stringify(sanitize(err))}catch{text=String(err)}
  return {error_type:err===null?'null':typeof err,message:redactText(text)||'(no message)',stack:null};
 }

 function createCapture(deps={}){
  const d={now:()=>Date.now(),monotonic:()=>(deps.performance||scope.performance)?.now?.()??0,uuid:()=>(deps.crypto||scope.crypto)?.randomUUID?.()||`${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`,setTimeout:(fn,ms)=>setTimeout(fn,ms),clearTimeout:t=>clearTimeout(t),...deps};
  const S={breadcrumbs:[],queue:[],sent:0,dropped:0,suppressed:0,minute:[],last:null,timer:null,inflight:null,apiSha:null,apiShaPromise:null,installed:false,inHandler:false,batches:[]};
  const listeners=new Set();
  const config=()=>d.config||scope.BASIS_CONFIG||null;
  const context=()=>{try{return d.getContext?.()||{}}catch{return {}}};
  const tzone=()=>{try{return Intl.DateTimeFormat().resolvedOptions().timeZone||null}catch{return null}};
  // The client's clock as local time with its UTC offset (ISO 8601): the same instant as
  // Date.now(), shifted into the zone so the text reads as the user saw it.
  const stamp=()=>{const t=new Date(d.now()),o=-t.getTimezoneOffset(),s=o>=0?'+':'-',p=n=>String(Math.floor(Math.abs(n))).padStart(2,'0');
   return new Date(t.getTime()+o*60000).toISOString().replace('Z','')+s+p(o/60)+':'+p(o%60)};
  function breadcrumb(kind,message,data){
   const crumb={at:stamp(),since_load_ms:Math.round(d.monotonic()),kind,message:message?cut(message,LIMITS.short):null,data:data?boundedData(data):null};
   S.breadcrumbs.push(crumb);if(S.breadcrumbs.length>LIMITS.maxBreadcrumbs)S.breadcrumbs.shift();
   return crumb;
  }
  function boundedData(data){const clean=sanitize(data,3);try{const text=JSON.stringify(clean);if(text.length>LIMITS.breadcrumbChars)return {truncated:true,keys:Object.keys(clean).slice(0,20)}}catch{return null}return clean}
  function clientInfo(){
   const nav=d.navigator||scope.navigator||{},win=d.window||scope,doc=d.document||scope.document;
   return {user_agent:nav.userAgent?cut(nav.userAgent,1000):null,platform:cut(nav.userAgentData?.platform||nav.platform||'',LIMITS.short)||null,language:nav.language||null,languages:Array.isArray(nav.languages)?nav.languages.slice(0,10):null,
    viewport:{width:win.innerWidth??null,height:win.innerHeight??null},device_pixel_ratio:typeof win.devicePixelRatio==='number'?win.devicePixelRatio:null,online:typeof nav.onLine==='boolean'?nav.onLine:null,timezone:tzone(),connection:nav.connection?.effectiveType||null,visibility:doc?.visibilityState||null,cookies_enabled:typeof nav.cookieEnabled==='boolean'?nav.cookieEnabled:null};
  }
  function page(){const loc=d.location||scope.location||{},ctx=context();return {hash:redactPath(loc.hash||''),screen:ctx.page?.screen||null,path:redactPath(loc.pathname||'')}}
  function build(){return {api_git_sha:S.apiSha,web_version:d.version||VERSION}}
  // The API's build, once, from /health (unauthenticated); an event sent before it answers
  // carries null and the next ones the sha.
  function apiBuild(){
   if(S.apiSha||S.apiShaPromise)return S.apiShaPromise;
   const base=config()?.apiBase;if(!base||config()?.mode!=='live'||!d.fetch)return null;
   S.apiShaPromise=Promise.resolve().then(()=>d.fetch(base.replace(/\/+$/,'')+'/health',{cache:'no-store'})).then(r=>r.json()).then(h=>{S.apiSha=h?.git_sha||null;for(const e of S.queue)if(e.build&&!e.build.api_git_sha)e.build.api_git_sha=S.apiSha}).catch(()=>{S.apiShaPromise=null});
   return S.apiShaPromise;
  }
  function capture(kind,parts){
   if(S.inHandler)return null;S.inHandler=true;
   try{
    const ctx=context(),base={event_id:d.uuid(),occurred_at:stamp(),since_load_ms:Math.round(d.monotonic()),kind,level:parts.level||'error',message:cut(parts.message||'(no message)',LIMITS.message),error_type:parts.error_type||null,stack:parts.stack?cut(parts.stack,LIMITS.stack):null,
     source:parts.source||parseStack(parts.stack),page:page(),user:ctx.user?{uid:ctx.user.uid||null,email:ctx.user.email||null,role:ctx.user.role||null}:null,firm:ctx.firm?{org_id:ctx.firm.org_id||null,org_name:ctx.firm.org_name?cut(ctx.firm.org_name,LIMITS.short):null}:null,
     study:ctx.study?{study_id:ctx.study.study_id||null,revision:ctx.study.revision??null,stage:ctx.study.stage||null,status:ctx.study.status||null}:null,http:parts.http||null,breadcrumbs:S.breadcrumbs.slice(),build:build(),client:clientInfo(),context:parts.context?bounded(sanitize(parts.context,3)):null,repeats:1,dropped:0,capture_version:VERSION};
    const fp=fingerprint(base),nowMs=d.now();
    if(S.last&&S.last.fp===fp&&nowMs-S.last.at<LIMITS.dedupeMs){S.last.event.repeats++;S.last.at=nowMs;S.suppressed++;return null}
    S.minute=S.minute.filter(t=>nowMs-t<60000);
    if(S.minute.length>=LIMITS.maxPerMinute){S.dropped++;return null}
    S.minute.push(nowMs);
    base.dropped=S.dropped;S.dropped=0;
    S.last={fp,at:nowMs,event:base};
    S.queue.push(base);if(S.queue.length>LIMITS.queueMax){S.queue.shift();S.dropped++}
    listeners.forEach(fn=>{try{fn(base)}catch{}});
    apiBuild();schedule();
    return base;
   }finally{S.inHandler=false}
  }
  function bounded(value){try{const text=JSON.stringify(value);if(text.length>LIMITS.contextChars)return {truncated:true,keys:Object.keys(value).slice(0,20)}}catch{return null}return value}
  function schedule(){if(S.timer!==null||!S.queue.length)return;S.timer=d.setTimeout(()=>{S.timer=null;flush()},LIMITS.flushMs)}
  // One batch at a time; the same key across its retries so the API replays, not stores twice.
  async function flush({unload=false}={}){
   if(S.inflight)return S.inflight;
   if(!S.queue.length)return null;
   const cfg=config();if(!cfg||cfg.mode!=='live'||!d.fetch)return null;
   let token=null;try{token=await (d.getToken?.()??null)}catch{token=null}
   if(!token)return null;// not signed in yet: keep the queue, send after sign-in
   const events=S.queue.splice(0,LIMITS.batchSize),batch={key:d.uuid(),events,attempt:0};
   S.inflight=send(batch,token,unload).finally(()=>{S.inflight=null;if(S.queue.length)schedule()});
   return S.inflight;
  }
  async function send(batch,token,unload){
   const base=String(config().apiBase||'').replace(/\/+$/,'');
   const body=JSON.stringify({events:batch.events,sent_at:stamp()});
   for(;;){
    batch.attempt++;
    let status=0;
    try{const r=await d.fetch(base+ROUTE,{method:'POST',keepalive:true,headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json',Accept:'application/json, application/problem+json','Idempotency-Key':batch.key},body});status=r.status}
    catch{status=0}
    S.batches.push({key:batch.key,attempt:batch.attempt,status,events:batch.events.length});
    if(status>=200&&status<300){S.sent+=batch.events.length;return true}
    const retry=status===0||status===429||status>=500;
    if(!retry||unload||batch.attempt>LIMITS.retryDelaysMs.length){S.dropped+=batch.events.length;return false}
    await new Promise(r=>d.setTimeout(r,LIMITS.retryDelaysMs[batch.attempt-1]));
    try{token=await (d.getToken?.()??token)||token}catch{}
   }
  }
  // --- the hooks
  function onErrorEvent(ev){
   if(ev&&ev.target&&ev.target!==(d.window||scope)&&!(ev instanceof (scope.ErrorEvent||function(){}))&&(ev.target.src||ev.target.href)){
    const el=ev.target,url=redactPath(String(el.src||el.href||''));
    capture('resource',{message:`Failed to load ${String(el.tagName||'resource').toLowerCase()} ${url}`,error_type:'ResourceError',source:{file:url,line:null,column:null,function:null}});
    return;
   }
   const err=ev?.error,desc=err?describeError(err):{error_type:'Error',message:redactText(ev?.message)||'Script error',stack:null};
   const source=ev?.filename?{file:redactPath(ev.filename),line:ev.lineno??null,column:ev.colno??null,function:null}:undefined;
   capture('error',{...desc,source:source||parseStack(desc.stack)});
  }
  function onRejection(ev){const desc=describeError(ev?.reason);capture('unhandledrejection',{...desc})}
  let originalConsoleError=null;
  function onConsoleError(args){
   const errs=args.filter(a=>a instanceof Error),text=args.map(a=>a instanceof Error?`${a.name}: ${a.message}`:typeof a==='string'?a:(()=>{try{return JSON.stringify(sanitize(a))}catch{return String(a)}})()).join(' ');
   breadcrumb('console',cut(redactText(text),LIMITS.short));
   capture('console',{message:redactText(text)||'(empty)',error_type:errs[0]?errs[0].name:null,stack:errs[0]?redactText(errs[0].stack,LIMITS.stack):null});
  }
  // The API client's observe hook: every call is a breadcrumb; a failed one is an event.
  function apiCall(info){
   if(!info||String(info.path||'').startsWith(ROUTE))return;
   const route=redactPath(info.path),http={method:info.method||null,route,status:Number.isFinite(info.status)?info.status:0,code:info.code||null,request_id:info.requestId||null,problem_type:info.problemType?cut(info.problemType,LIMITS.short):null,problem_title:info.title?cut(info.title,LIMITS.short):null,problem_detail:info.detail?redactText(info.detail):null,latency_ms:Number.isFinite(info.durationMs)?Math.max(0,Math.round(info.durationMs)):null,attempt:info.attempt||1,retry:Boolean(info.retry),body_fields:safeKeys(info.body)};
   breadcrumb('api',`${http.method} ${route} → ${http.status}`,{status:http.status,request_id:http.request_id,code:http.code,latency_ms:http.latency_ms,attempt:http.attempt});
   if(http.status>=200&&http.status<400)return;
   // A refusal the caller named as expected (a 404 for a profile not saved yet) is handled, not an error.
   if(info.aborted||info.expected)return;
   const title=http.status===0?'The API could not be reached':`${http.method} ${route} failed with ${http.status}`;
   capture('api',{level:http.status>=500||http.status===0?'error':'warning',message:cut(`${title}${http.code?` (${http.code})`:''}${http.problem_detail?`: ${http.problem_detail}`:''}`,LIMITS.message),error_type:http.status===0?'NetworkError':'ApiError',stack:info.stack?redactText(info.stack,LIMITS.stack):null,http});
  }
  function report(err,context){const desc=describeError(err);return capture('report',{...desc,context:context||null,level:context?.level==='warning'?'warning':'error'})}
  function onClick(ev){
   const el=ev?.target?.closest?.('[data-live],[data-action],button,a,[role="button"]');if(!el)return;
   const action=el.dataset?.live||el.dataset?.action||null,value=el.dataset?.value?redactPath(el.dataset.value):null,text=cut((el.getAttribute?.('aria-label')||el.textContent||'').trim().replace(/\s+/g,' '),60);
   breadcrumb('click',action?`${action}${value?' '+value:''}`:text,{tag:String(el.tagName||'').toLowerCase(),action,value,text,screen:context().page?.screen||null});
  }
  function onHashChange(ev){breadcrumb('navigation',`${redactPath(ev?.oldURL?new URL(ev.oldURL).hash:'')} → ${redactPath(ev?.newURL?new URL(ev.newURL).hash:(d.location||scope.location)?.hash||'')}`)}
  function install(){
   if(S.installed)return api;S.installed=true;
   const win=d.window||scope,doc=d.document||scope.document,con=d.console||scope.console;
   win.addEventListener?.('error',onErrorEvent,true);
   win.addEventListener?.('unhandledrejection',onRejection);
   win.addEventListener?.('hashchange',onHashChange);
   win.addEventListener?.('pagehide',()=>flush({unload:true}));
   doc?.addEventListener?.('visibilitychange',()=>{if(doc.visibilityState==='hidden')flush({unload:true})});
   doc?.addEventListener?.('click',onClick,true);
   if(con&&typeof con.error==='function'){originalConsoleError=con.error.bind(con);con.error=(...args)=>{try{onConsoleError(args)}catch{}originalConsoleError(...args)}}
   breadcrumb('navigation',`load ${redactPath((d.location||scope.location)?.hash||'')}`);
   return api;
  }
  const api={install,capture,report,breadcrumb,apiCall,flush,setContext(fn){d.getContext=fn},onEvent(fn){listeners.add(fn);return ()=>listeners.delete(fn)},
   get state(){return S},get queue(){return S.queue},get breadcrumbs(){return S.breadcrumbs},LIMITS,VERSION,
   // Test and page hooks: what happened to the batches, and the hooks themselves.
   _hooks:{onErrorEvent,onRejection,onConsoleError,onClick,onHashChange}};
  return api;
 }
 const exported={createCapture,redactPath,redactText,sanitize,safeKeys,parseStack,describeError,fingerprint,LIMITS,VERSION};
 if(typeof module==='object'&&module.exports){module.exports=exported;return}
 const script=scope.document?.currentScript?.src||'';
 const version=(/[?&]v=([^&#]+)/.exec(script)||[])[1]||VERSION;
 const capture=createCapture({version,fetch:scope.fetch?.bind(scope),getToken:()=>scope.BasisAuth?.getToken?.()??null,
  getContext:()=>{const L=scope.BasisLive,st=L?.state,firm=L?.firm?.(),user=scope.BasisAuth?.user;
   return {user:user?{uid:user.uid,email:user.email,role:firm?.role||null}:null,firm:firm?{org_id:firm.org_id,org_name:firm.org_name}:null,
    study:st?.studyId?{study_id:st.studyId,revision:st.study?.revision??null,stage:st.study?.current_stage||null,status:st.study?.status||null}:null,
    page:{screen:L?.screen?.()||null}}}});
 scope.BasisErrors=Object.assign(capture.install(),exported);
 scope.Basis=scope.Basis||{};scope.Basis.reportError=(err,context)=>capture.report(err,context);
})(typeof globalThis==='object'?globalThis:this);
