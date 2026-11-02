/* live/errors.js: what an event carries, what is never recorded, the breadcrumb ring, the
 * caps, dedupe, batching, retries, and that the module cannot report on itself. A fake
 * browser (window, document, navigator, location, fetch, clock, timers) makes every path
 * deterministic; the sample event the last test writes is what the API test posts, so the
 * client's shape is proven against the server's schema (docs/api/CLIENT_ERRORS.md). */
// The browser's clock offset is part of an event; pin the zone so the fixture is the same
// on every machine (node reads TZ at runtime).
process.env.TZ='America/New_York';
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');
const E=require('../live/errors.js');
const {createClient}=require('../live/api-client.js');

function fakeBrowser({mode='live',token='tok',responses=[],health={git_sha:'test-sha'}}={}){
 const listeners={window:{},document:{}};
 const opts={token,health,context:()=>({user:{uid:'u1',email:'eng@firm.test',role:'engineer'},firm:{org_id:'0b8f0f5e-0000-4000-8000-000000000001',org_name:'Acme Engineering'},study:{study_id:'3f2c1a2b-1234-4bcd-8ef0-123456789abc',revision:2,stage:'rooms_review',status:'waiting_review'},page:{screen:'rooms'}})};
 const on=store=>(name,fn)=>{(store[name]||=[]).push(fn)};
 const fire=(store,name,ev)=>(store[name]||[]).forEach(fn=>fn(ev));
 const calls=[];let clock=1_700_000_000_000,mono=1000;const timers=[];
 // /health answers from `health` (null: it fails); the POSTs consume `responses` in order.
 const fetch=async(url,init)=>{calls.push({url,...init,headers:{...init?.headers}});
  if(url.endsWith('/health')){if(!opts.health)throw new Error('no health');return {status:200,ok:true,json:async()=>opts.health,headers:{get:()=>null}}}
  const r=responses.shift()||{status:202};if(r instanceof Error)throw r;
  return {status:r.status,ok:r.status<300,json:async()=>r.body||{},headers:{get:()=>null}}};
 const con={error(){},warn(){},log(){}};
 const env={
  config:mode?{mode,apiBase:'http://api.test/'}:null,
  window:{addEventListener:on(listeners.window),innerWidth:1280,innerHeight:720,devicePixelRatio:2},
  document:{addEventListener:on(listeners.document),visibilityState:'visible'},
  navigator:{userAgent:'TestBrowser/1.0',platform:'MacIntel',language:'en-US',languages:['en-US','es'],onLine:true,cookieEnabled:true,connection:{effectiveType:'4g'}},
  location:{hash:'#rooms/3f2c1a2b-1234-4bcd-8ef0-123456789abc',pathname:'/'},
  console:con,fetch,getToken:async()=>opts.token,now:()=>clock,monotonic:()=>mono,
  uuid:(()=>{let n=0;return()=>`00000000-0000-4000-8000-${String(++n).padStart(12,'0')}`})(),
  setTimeout:(fn,ms)=>{timers.push({fn,ms,at:clock+ms});return timers.length},clearTimeout(){},
  version:'live-16',
  getContext:()=>opts.context(),
 };
 const cap=E.createCapture(env);cap.install();
 const tick=async(ms)=>{clock+=ms;mono+=ms;const due=timers.filter(t=>t.at<=clock&&!t.done);for(const t of due){t.done=true;t.fn()}await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r))};
 return {cap,calls,opts,fire:(where,name,ev)=>fire(listeners[where],name,ev),tick,timers,console:con,env,get clock(){return clock}};
}

test('redaction: ids in paths become {id}, query values go, tokens are masked, bodies keep field names only, secret-looking keys are masked',()=>{
 assert.equal(E.redactPath('/v1/studies/3f2c1a2b-1234-4bcd-8ef0-123456789abc/photos/5f0c6a52aaaa4bbbbcccc1234567890ab/objects?limit=50&cursor=9981234567'),'/v1/studies/{id}/photos/{id}/objects?limit=…&cursor=…');
 // A JWT-shaped token, assembled at run time so the source holds none (the secrets scan reads the diff).
 const jwt=['eyJhbGciOiJSUzI1NiIsImtpZCI6IjEifQ','eyJzdWIiOiJ1MSJ9','c2lnbmF0dXJlc2lnbmF0dXJl'].join('.');
 assert.equal(E.redactText(`Authorization: Bearer ${jwt} failed`),'Authorization: Bearer *** failed');
 assert.equal(E.redactText(`token ${jwt} here`),'token *** here');
 assert.deepEqual(E.safeKeys({purchase_price:'650000',acquisition_costs:[],state:'NY'}),['purchase_price','acquisition_costs','state']);
 assert.deepEqual(E.safeKeys('{"a":1}'),['a']);assert.equal(E.safeKeys(undefined),null);
 assert.deepEqual(E.sanitize({apiKey:'x',nested:{password:'p',ok:1,deep:{deeper:{deepest:1}}},list:[1,'two']}),{apiKey:'***',nested:{password:'***',ok:1,deep:{deeper:'[Object]'}},list:[1,'two']});
 assert.equal(E.redactText('a\x00b'),'ab');
});
test('the client clock stamp is the same instant as Date.now(), written in local time with its offset',()=>{
 const b=fakeBrowser();
 const ev=b.cap.report(new Error('when'));
 assert.equal(Date.parse(ev.occurred_at),b.clock);// found live: the first version appended the offset to a UTC time, 7 h off
 assert.match(ev.occurred_at,/-05:00$|-04:00$/);// America/New_York
 assert.equal(Date.parse(ev.breadcrumbs[0].at),b.clock);
});
test('parseStack reads V8 and Firefox frames and redacts the file',()=>{
 assert.deepEqual(E.parseStack('TypeError: x\n    at render (http://127.0.0.1:3100/live/live-app.js?v=14:120:9)\n    at y'),{function:'render',file:'http://127.0.0.1:3100/live/live-app.js?v=…',line:120,column:9});
 assert.deepEqual(E.parseStack('render@http://h/live/x.js:3:4'),{function:'render',file:'http://h/live/x.js',line:3,column:4});
 assert.deepEqual(E.parseStack('    at http://h/live/x.js:3:4'),{function:null,file:'http://h/live/x.js',line:3,column:4});
 assert.equal(E.parseStack(''),null);
});
test('an uncaught error becomes an event with every field: ids, times, user, firm, study, page, source, build, client, breadcrumbs',async()=>{
 const b=fakeBrowser();
 b.cap.breadcrumb('click','approve rooms_review',{action:'approve'});
 const err=new TypeError('Cannot read properties of undefined (reading "rooms")');err.stack='TypeError: Cannot read properties of undefined (reading "rooms")\n    at render (http://127.0.0.1:3100/live/live-app.js?v=live-16:120:9)';
 b.fire('window','error',{error:err,message:err.message,filename:'http://127.0.0.1:3100/live/live-app.js?v=live-16',lineno:120,colno:9});
 const [ev]=b.cap.queue;
 assert.equal(ev.event_id,'00000000-0000-4000-8000-000000000001');
 assert.match(ev.occurred_at,/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$/);
 assert.equal(ev.since_load_ms,1000);assert.equal(ev.kind,'error');assert.equal(ev.level,'error');
 assert.equal(ev.error_type,'TypeError');assert.match(ev.message,/reading "rooms"/);assert.match(ev.stack,/live-app\.js/);
 assert.deepEqual(ev.source,{file:'http://127.0.0.1:3100/live/live-app.js?v=…',line:120,column:9,function:null});
 assert.deepEqual(ev.page,{hash:'#rooms/{id}',screen:'rooms',path:'/'});
 assert.deepEqual(ev.user,{uid:'u1',email:'eng@firm.test',role:'engineer'});
 assert.deepEqual(ev.firm,{org_id:'0b8f0f5e-0000-4000-8000-000000000001',org_name:'Acme Engineering'});
 assert.deepEqual(ev.study,{study_id:'3f2c1a2b-1234-4bcd-8ef0-123456789abc',revision:2,stage:'rooms_review',status:'waiting_review'});
 assert.deepEqual(ev.build,{api_git_sha:null,web_version:'live-16'});// /health has not answered yet
 assert.deepEqual(ev.client,{user_agent:'TestBrowser/1.0',platform:'MacIntel',language:'en-US',languages:['en-US','es'],viewport:{width:1280,height:720},device_pixel_ratio:2,online:true,timezone:ev.client.timezone,connection:'4g',visibility:'visible',cookies_enabled:true});
 assert.equal(ev.breadcrumbs.length,2);assert.equal(ev.breadcrumbs[0].kind,'navigation');assert.equal(ev.breadcrumbs[1].message,'approve rooms_review');
 assert.equal(ev.repeats,1);assert.equal(ev.dropped,0);assert.equal(ev.capture_version,E.VERSION);assert.equal(ev.http,null);assert.equal(ev.context,null);
});
test('the API build is read once from /health and fills events that were queued before it answered',async()=>{
 const b=fakeBrowser({health:{git_sha:'abc1234'}});
 b.fire('window','unhandledrejection',{reason:new Error('later')});
 await b.tick(0);
 assert.equal(b.calls[0].url,'http://api.test/health');
 assert.equal(b.cap.queue[0].build.api_git_sha,'abc1234');
 b.cap.report('second');assert.equal(b.cap.queue[1].build.api_git_sha,'abc1234');
 await b.tick(5000);
 assert.equal(b.calls.filter(c=>c.url.endsWith('/health')).length,1);
});
test('a rejection with a non-Error reason, a resource failure, a console.error and an explicit report each record their kind',()=>{
 const b=fakeBrowser();
 b.fire('window','unhandledrejection',{reason:{status:500,detail:'x'}});
 b.fire('window','error',{target:{tagName:'IMG',src:'http://api.test/v1/studies/3f2c1a2b-1234-4bcd-8ef0-123456789abc/documents/5f0c6a52-1234-4bcd-8ef0-123456789abc/content'}});
 b.console.error('Could not open %s',new RangeError('bad range'));
 b.cap.report(new Error('from a screen'),{action:'move-photo',secretThing:'x',note:'kept'});
 const kinds=b.cap.queue.map(e=>e.kind);
 assert.deepEqual(kinds,['unhandledrejection','resource','console','report']);
 assert.equal(b.cap.queue[0].error_type,'object');assert.equal(b.cap.queue[0].message,'{"status":500,"detail":"x"}');
 assert.equal(b.cap.queue[1].message,'Failed to load img http://api.test/v1/studies/{id}/documents/{id}/content');
 assert.equal(b.cap.queue[2].error_type,'RangeError');assert.match(b.cap.queue[2].message,/Could not open %s RangeError: bad range/);
 assert.deepEqual(b.cap.queue[3].context,{action:'move-photo',secretThing:'***',note:'kept'});
 assert.equal(b.cap.breadcrumbs.filter(c=>c.kind==='console').length,1);
});
test('API calls: every call is a breadcrumb with status, duration, request id and attempt; a failure is an event with the problem code and field names only; its own route is ignored',()=>{
 const b=fakeBrowser();
 b.cap.apiCall({method:'GET',path:'/v1/studies/3f2c1a2b-1234-4bcd-8ef0-123456789abc/rooms?revision=2&cursor=abc',status:200,durationMs:41.6,requestId:'r-1',attempt:1});
 b.cap.apiCall({method:'POST',path:'/v1/studies/3f2c1a2b-1234-4bcd-8ef0-123456789abc/decisions',status:409,durationMs:12,requestId:'r-2',code:'BASIS-STUDY-003',problemType:'https://basis.invalid/problems/stale-state',title:'Conflict',detail:'revision 1 is stale',attempt:2,retry:true,body:{stage:'rooms_review',revision:1,decision:'approve',comment:'secret text'}});
 b.cap.apiCall({method:'POST',path:'/v1/client-errors',status:500,durationMs:3});
 b.cap.apiCall({method:'GET',path:'/v1/me',status:0,durationMs:3000,detail:'The Basis API could not be reached.'});
 const crumbs=b.cap.breadcrumbs.filter(c=>c.kind==='api');
 assert.equal(crumbs.length,3);
 assert.equal(crumbs[0].message,'GET /v1/studies/{id}/rooms?revision=…&cursor=… → 200');
 assert.deepEqual(crumbs[0].data,{status:200,request_id:'r-1',code:null,latency_ms:42,attempt:1});
 assert.deepEqual(b.cap.queue.map(e=>e.kind),['api','api']);
 const [conflict,network]=b.cap.queue;
 assert.equal(conflict.level,'warning');assert.equal(conflict.error_type,'ApiError');
 assert.equal(conflict.message,'POST /v1/studies/{id}/decisions failed with 409 (BASIS-STUDY-003): revision 1 is stale');
 assert.deepEqual(conflict.http,{method:'POST',route:'/v1/studies/{id}/decisions',status:409,code:'BASIS-STUDY-003',request_id:'r-2',problem_type:'https://basis.invalid/problems/stale-state',problem_title:'Conflict',problem_detail:'revision 1 is stale',latency_ms:12,attempt:2,retry:true,body_fields:['stage','revision','decision','comment']});
 assert.doesNotMatch(JSON.stringify(conflict),/secret text/);
 assert.equal(network.level,'error');assert.equal(network.error_type,'NetworkError');assert.equal(network.http.status,0);
});
test('the API client reports every request to observe with the attempt under its key, never the token or response',async()=>{
 const seen=[];let n=0;
 const impl=async()=>{n++;return {ok:n!==2,status:n===2?422:201,headers:{get:k=>k==='content-type'?'application/problem+json':k==='x-request-id'?`rid-${n}`:null},text:async()=>JSON.stringify(n===2?{status:422,title:'Unprocessable',code:'BASIS-API-013',request_id:'rid-2',detail:'bad'}:{id:'s1'})}};
 const api=createClient({baseUrl:'http://api.test',getToken:async()=>'tok',fetchImpl:impl,observe:i=>seen.push(i),now:()=>0});
 await api.createStudy({property_name:'P'},'key-1');
 await api.createStudy({property_name:'P'},'key-1').catch(()=>{});
 await api.getStudy('s1').catch(()=>{});
 assert.equal(seen.length,3);
 assert.deepEqual({...seen[0],body:undefined,stack:undefined},{method:'POST',path:'/v1/studies',status:201,durationMs:0,requestId:'rid-1',code:null,problemType:null,title:null,detail:null,attempt:1,retry:false,body:undefined,aborted:false,stack:undefined});
 assert.deepEqual(seen[0].body,{property_name:'P'});
 assert.equal(seen[1].attempt,2);assert.equal(seen[1].retry,true);assert.equal(seen[1].status,422);assert.equal(seen[1].code,'BASIS-API-013');assert.equal(seen[1].requestId,'rid-2');
 assert.equal(seen[2].attempt,1);assert.equal(seen[2].status,201);
 assert.ok(!JSON.stringify(seen).includes('tok'));
 const failing=createClient({baseUrl:'http://api.test',fetchImpl:impl,observe:()=>{throw new Error('observer bug')}});
 assert.equal((await failing.getStudy('s1')).id,'s1');// a broken observer never breaks a request
});
test('clicks on data-live actions, hash navigation and gate decisions are breadcrumbs; the ring keeps the last 50',()=>{
 const b=fakeBrowser();
 const el={dataset:{live:'move-photo',value:'5f0c6a52-1234-4bcd-8ef0-123456789abc'},tagName:'BUTTON',textContent:'  Move\n photo ',getAttribute:()=>null};
 b.fire('document','click',{target:{closest:()=>el}});
 b.fire('window','hashchange',{oldURL:'http://h/?mode=live#rooms/3f2c1a2b-1234-4bcd-8ef0-123456789abc',newURL:'http://h/?mode=live#viewer/3f2c1a2b-1234-4bcd-8ef0-123456789abc/5f0c6a52-1234-4bcd-8ef0-123456789abc'});
 b.cap.breadcrumb('decision','approve rooms_review',{stage:'rooms_review',decision:'approve',revision:2,corrections:0});
 const [,click,nav,decision]=b.cap.breadcrumbs;
 assert.equal(click.kind,'click');assert.equal(click.message,'move-photo {id}');assert.deepEqual(click.data,{tag:'button',action:'move-photo',value:'{id}',text:'Move photo',screen:'rooms'});
 assert.equal(nav.message,'#rooms/{id} → #viewer/{id}/{id}');
 assert.equal(decision.kind,'decision');
 for(let i=0;i<60;i++)b.cap.breadcrumb('custom',`c${i}`);
 assert.equal(b.cap.breadcrumbs.length,50);assert.equal(b.cap.breadcrumbs[49].message,'c59');assert.equal(b.cap.breadcrumbs[0].message,'c10');
 b.fire('document','click',{target:{closest:()=>null}});// a click on nothing actionable adds nothing
 assert.equal(b.cap.breadcrumbs.length,50);
});
test('identical errors within 5 s are one event with repeats; after the window a new event',async()=>{
 const b=fakeBrowser();
 for(let i=0;i<4;i++)b.cap.report(new Error('same'));
 assert.equal(b.cap.queue.length,1);assert.equal(b.cap.queue[0].repeats,4);
 await b.tick(6000);
 b.cap.report(new Error('same'));
 assert.equal(b.cap.queue.length,1);// the first batch left; a new event is queued
 assert.equal(b.cap.queue[0].repeats,1);
});
test('at most 30 distinct events a minute; the rest are counted as dropped on the next accepted event',async()=>{
 const b=fakeBrowser({responses:Array(10).fill({status:202})});
 for(let i=0;i<45;i++)b.cap.report(new Error(`e${i}`));
 assert.equal(b.cap.queue.length,30);
 await b.tick(61000);
 b.cap.report(new Error('after the minute'));
 const last=b.cap.queue.at(-1);
 assert.equal(last.message,'after the minute');assert.equal(last.dropped,15);
});
test('a render loop that throws on every frame sends one batch of at most 20 and queues at most 50',async()=>{
 const b=fakeBrowser({responses:Array(10).fill({status:202})});
 for(let i=0;i<200;i++){b.cap.report(new Error(`frame ${i}`));await 0}
 assert.ok(b.cap.queue.length<=50);
 await b.tick(5000);
 const posts=b.calls.filter(c=>c.url.endsWith('/v1/client-errors'));
 assert.equal(posts.length,1);assert.ok(JSON.parse(posts[0].body).events.length<=20);
});
test('batches: one POST after 5 s with the bearer token, keepalive and an Idempotency-Key; the same key on retries after 5xx and network failure; dropped after three retries; 4xx drops at once',async()=>{
 const b=fakeBrowser({responses:[{status:503},new Error('offline'),{status:202}]});
 b.cap.report(new Error('one'));b.cap.report(new Error('two'));
 assert.equal(b.calls.length,0);
 await b.tick(5000);
 const post=b.calls.find(c=>c.url==='http://api.test/v1/client-errors');
 assert.equal(post.method,'POST');assert.equal(post.keepalive,true);assert.equal(post.headers.Authorization,'Bearer tok');assert.match(post.headers['Idempotency-Key'],/^0000/);
 const body=JSON.parse(post.body);assert.equal(body.events.length,2);assert.match(body.sent_at,/[+-]\d{2}:\d{2}$/);
 await b.tick(2000);await b.tick(8000);
 const posts=b.calls.filter(c=>c.url.endsWith('/v1/client-errors'));
 assert.equal(posts.length,3);assert.ok(posts.every(p=>p.headers['Idempotency-Key']===posts[0].headers['Idempotency-Key']));
 assert.equal(b.cap.state.sent,2);assert.equal(b.cap.queue.length,0);
 const c=fakeBrowser({responses:[{status:500},{status:500},{status:500},{status:500}]});
 c.cap.report(new Error('x'));await c.tick(5000);await c.tick(2000);await c.tick(8000);await c.tick(32000);
 assert.equal(c.calls.filter(x=>x.url.endsWith('/v1/client-errors')).length,4);assert.equal(c.cap.state.dropped,1);assert.equal(c.cap.state.sent,0);
 const d=fakeBrowser({responses:[{status:422}]});
 d.cap.report(new Error('x'));await d.tick(5000);await d.tick(2000);
 assert.equal(d.calls.filter(x=>x.url.endsWith('/v1/client-errors')).length,1);assert.equal(d.cap.state.dropped,1);
});
test('without a signed-in user events wait in the queue and leave once a token exists; in mock mode nothing is sent',async()=>{
 const b=fakeBrowser({token:null});
 b.cap.report(new Error('before sign-in'));await b.tick(5000);
 assert.equal(b.calls.filter(c=>c.url.endsWith('/v1/client-errors')).length,0);assert.equal(b.cap.queue.length,1);
 b.opts.token='tok';b.cap.report(new Error('after'));await b.tick(5000);
 assert.equal(JSON.parse(b.calls.find(c=>c.url.endsWith('/v1/client-errors')).body).events.length,2);
 const m=fakeBrowser({mode:'mock'});m.cap.report(new Error('x'));await m.tick(5000);
 assert.equal(m.calls.length,0);assert.equal(m.cap.queue.length,1);
});
test('the page hiding flushes at once with keepalive; a failure during unload is not retried',async()=>{
 const b=fakeBrowser({responses:[{status:500}]});
 b.cap.report(new Error('x'));
 b.env.document.visibilityState='hidden';b.fire('document','visibilitychange');
 await b.tick(0);
 assert.equal(b.calls.filter(c=>c.url.endsWith('/v1/client-errors')).length,1);
 await b.tick(40000);
 assert.equal(b.calls.filter(c=>c.url.endsWith('/v1/client-errors')).length,1);
});
test('an error thrown while building an event, or by the console wrapper, never escapes or recurses',()=>{
 const b=fakeBrowser();b.opts.context=()=>{throw new Error('context broke')};
 const ev=b.cap.report(new Error('still recorded'));
 assert.equal(ev.kind,'report');assert.equal(ev.user,null);
 b.cap.onEvent(()=>{b.console.error('listener logs');});// console.error inside capture: ignored, no loop
 const before=b.cap.queue.length;b.cap.report(new Error('again'));
 assert.equal(b.cap.queue.length,before+1);
});
test('the sample event posted by the API test is what this module produces',()=>{
 const b=fakeBrowser();
 b.cap.breadcrumb('click','approve rooms_review',{action:'approve'});
 b.cap.apiCall({method:'GET',path:'/v1/studies/3f2c1a2b-1234-4bcd-8ef0-123456789abc/rooms?revision=2',status:200,durationMs:41,requestId:'r-1',attempt:1});
 b.cap.apiCall({method:'POST',path:'/v1/studies/3f2c1a2b-1234-4bcd-8ef0-123456789abc/decisions',status:409,durationMs:12,requestId:'r-2',code:'BASIS-STUDY-003',problemType:'https://basis.invalid/problems/stale-state',title:'Conflict',detail:'revision 1 is stale',attempt:1,retry:false,body:{stage:'rooms_review',revision:1,decision:'approve'}});
 const err=new TypeError('Cannot read properties of undefined (reading "rooms")');err.stack='TypeError: Cannot read properties of undefined (reading "rooms")\n    at render (http://127.0.0.1:3100/live/live-app.js?v=live-16:120:9)';
 b.fire('window','error',{error:err,message:err.message,filename:'http://127.0.0.1:3100/live/live-app.js?v=live-16',lineno:120,colno:9});
 b.cap.report(new Error('from a screen'),{action:'move-photo',apiKey:'x'});
 const events=b.cap.queue.map(e=>({...e,client:{...e.client,timezone:'America/New_York'}}));
 const file=path.join(__dirname,'fixtures','client-error-sample.json');
 const sample={events,sent_at:events[0].occurred_at};
 const text=JSON.stringify(sample,null,1)+'\n';
 if(process.env.UPDATE_FIXTURES)fs.writeFileSync(file,text);
 assert.equal(fs.readFileSync(file,'utf8'),text,'run with UPDATE_FIXTURES=1 to regenerate web/tests/fixtures/client-error-sample.json');
 assert.deepEqual(events.map(e=>e.kind),['api','error','report']);
});
