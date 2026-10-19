const {test}=require('node:test');const assert=require('node:assert/strict');
const {createClient,pollWorkflow,ApiError}=require('../live/api-client.js');
const Config=require('../live/config.js');
function fakeFetch(responses){
 const calls=[];
 const impl=async(url,init)=>{calls.push({url,...init});const r=responses.shift();if(r instanceof Error)throw r;
  const {status=200,body,type='application/json'}=r;
  return {ok:status>=200&&status<300,status,headers:{get:k=>k.toLowerCase()==='content-type'?type:null},text:async()=>body===undefined?'':typeof body==='string'?body:JSON.stringify(body)}};
 return {impl,calls};
}
test('sends the bearer token on every request and an Idempotency-Key on POST and DELETE only',async()=>{
 const {impl,calls}=fakeFetch([{body:{items:[],next_cursor:null}},{status:201,body:{id:'s1'}},{body:{id:'s1'}}]);
 const api=createClient({baseUrl:'http://api.test/',getToken:async()=>'tok',fetchImpl:impl});
 await api.listStudies();await api.createStudy({property_name:'P'},'key-1');await api.archiveStudy('s1');
 assert.equal(calls[0].url,'http://api.test/v1/studies?limit=100');assert.equal(calls[0].headers.Authorization,'Bearer tok');assert.equal(calls[0].headers['Idempotency-Key'],undefined);
 assert.equal(calls[1].method,'POST');assert.equal(calls[1].headers['Idempotency-Key'],'key-1');assert.equal(calls[1].body,'{"property_name":"P"}');assert.equal(calls[1].headers['Content-Type'],'application/json');
 assert.equal(calls[2].method,'DELETE');assert.match(calls[2].headers['Idempotency-Key'],/.{8,}/);
});
test('a retried action reuses the caller’s key, so the API can replay instead of acting twice',async()=>{
 const {impl,calls}=fakeFetch([{status:202,body:{}},{status:202,body:{}}]);
 const api=createClient({baseUrl:'http://api.test',fetchImpl:impl});
 await api.start('s1','same');await api.start('s1','same');
 assert.equal(calls[0].headers['Idempotency-Key'],'same');assert.equal(calls[1].headers['Idempotency-Key'],'same');assert.equal(calls[0].headers.Authorization,undefined);
});
test('problem+json errors become ApiError with status, kind, field errors and request id',async()=>{
 const problem={type:'https://basis.dev/problems/gate-not-open',title:'Conflict',status:409,detail:'The rooms review is not open yet.',request_id:'r-1',errors:[{loc:['body','x'],detail:'bad',type:'value_error'}]};
 const {impl}=fakeFetch([{status:409,body:problem,type:'application/problem+json'}]);
 const api=createClient({baseUrl:'http://api.test',fetchImpl:impl});
 const error=await api.decide('s1',{}).catch(e=>e);
 assert.ok(error instanceof ApiError);assert.equal(error.status,409);assert.equal(error.kind,'gate-not-open');assert.equal(error.message,'The rooms review is not open yet.');assert.equal(error.requestId,'r-1');assert.equal(error.errors.length,1);
});
test('a 401 calls onUnauthorized; a network failure is an ApiError with status 0',async()=>{
 let seen=null;const {impl}=fakeFetch([{status:401,body:{title:'Unauthorized',status:401},type:'application/problem+json'},new TypeError('Failed to fetch')]);
 const api=createClient({baseUrl:'http://api.test',fetchImpl:impl,onUnauthorized:e=>{seen=e}});
 await assert.rejects(api.getStudy('s1'),e=>e.status===401);assert.equal(seen.status,401);
 await assert.rejects(api.getStudy('s1'),e=>e instanceof ApiError&&e.status===0&&/could not be reached/.test(e.message));
});
test('a non-JSON error body still produces a readable error',async()=>{
 const {impl}=fakeFetch([{status:502,body:'Bad gateway',type:'text/plain'}]);
 const api=createClient({baseUrl:'http://api.test',fetchImpl:impl});
 await assert.rejects(api.getStudy('s1'),e=>e.status===502&&e.message==='Bad gateway');
});
test('listAll follows next_cursor and returns every item with the first page’s envelope',async()=>{
 const {impl,calls}=fakeFetch([{body:{revision:2,items:[1,2],next_cursor:'c2'}},{body:{revision:2,items:[3],next_cursor:null}}]);
 const api=createClient({baseUrl:'http://api.test',fetchImpl:impl});
 const page=await api.rooms('s1',2);
 assert.deepEqual(page,{revision:2,items:[1,2,3],next_cursor:null});assert.match(calls[1].url,/cursor=c2/);assert.match(calls[0].url,/revision=2/);assert.doesNotMatch(calls[0].url,/cursor=/);
});
test('raw requests return the response for binary content; ids are URL-encoded',async()=>{
 const {impl,calls}=fakeFetch([{body:'bytes',type:'image/jpeg'}]);
 const api=createClient({baseUrl:'http://api.test',fetchImpl:impl});
 const r=await api.documentContent('s 1','d/2');assert.equal(r.status,200);assert.equal(calls[0].url,'http://api.test/v1/studies/s%201/documents/d%2F2/content');
});
function fakeTimers(){const q=[];return {q,setTimeout:(fn,ms)=>{q.push({fn,ms});return q.length},clearTimeout:()=>{}}}
const flush=()=>new Promise(r=>setImmediate(r));
test('polling backs off while nothing changes, resets on change, and stops when told',async()=>{
 const timers=fakeTimers(),snapshots=[{status:{stage:'extracting',completed_photos:1}},{status:{stage:'extracting',completed_photos:1}},{status:{stage:'extracting',completed_photos:1}},{status:{stage:'extracting',completed_photos:2}},{status:{stage:'appraisal_review'}}];
 const seen=[];pollWorkflow(async()=>snapshots.shift(),{onUpdate:s=>seen.push(s.status.completed_photos??s.status.stage),shouldStop:s=>s.status.stage==='appraisal_review',initialMs:100,maxMs:300,timers,doc:null});
 for(let i=0;i<5;i++){await flush();if(timers.q.length)timers.q.shift().fn()}
 await flush();
 assert.deepEqual(seen,[1,2,'appraisal_review']);
 assert.deepEqual(timers.q.length,0);
});
test('polling errors back off and keep polling',async()=>{
 const timers=fakeTimers(),errors=[];let n=0;
 const p=pollWorkflow(async()=>{n++;throw new Error('down')},{onError:e=>errors.push(e.message),initialMs:100,maxMs:250,timers,doc:null});
 await flush();assert.equal(timers.q.at(-1).ms,200);timers.q.shift().fn();await flush();assert.equal(timers.q.at(-1).ms,250);
 p.stop();assert.equal(errors.length,2);assert.equal(n,2);
});
test('config: mock by default; ?mode=live and ?api= override; a bad api URL is ignored',()=>{
 assert.equal(Config.resolve('').mode,'mock');
 const c=Config.resolve('?mode=live&api=http://127.0.0.1:9000/');assert.equal(c.mode,'live');assert.equal(c.apiBase,'http://127.0.0.1:9000');
 assert.equal(Config.resolve('?api=javascript:alert(1)').apiBase,Config.defaults.apiBase);
 assert.equal(Config.resolve('?mode=other').mode,'mock');
 assert.equal(Config.resolve('',{firebase:{projectId:'cost-seg-basis',emulatorHost:null}}).firebase.emulatorHost,null);
});
