// The "Run records" block of the developer page's step dialogs (web/developer/architecture.js,
// health-client.js): the content file maps steps to registry components; the block lists what
// GET /v1/ops/eval-runs answers, one table per label set, every figure with its source, and
// says plainly when the feed is not connected, refused, unavailable or without data.
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const H=require('../developer/health-model.js'),A=require('../developer/architecture.js'),C=require('../developer/health-client.js');
const CONTENT=JSON.parse(fs.readFileSync(path.join(__dirname,'..','developer','workflow-content.json'),'utf8'));
const now=Date.parse('2026-11-02T12:00:00Z');
const basic={status:200,data:{status:'ok',service:'basis',checks:{db:{status:'ok'},temporal:{status:'ok'}}}};
const services=()=>H.snapshot(basic,null,now);
const node=id=>H.FLOW.flat().find(n=>n.id===id);
const fresh=()=>{A.setContent(JSON.parse(JSON.stringify(CONTENT)));return A};
const run=(over={})=>({
 run_id:'cHJlZGljdGlvbnMvYXJjaDI2',name:'arch26_countables_v3_sol',path:'predictions/arch26_countables_v3_sol',kind:'predictions',
 component:'photo_countables',version:'v3',harness:'evals.predict_photos',started_at:'2026-10-20T10:00:00+00:00',finished_at:'2026-10-20T10:12:00+00:00',
 status:'ok',results:{passed:50,failed:2,test_error:0},git:{sha:'b'.repeat(40),dirty:false,release:'v0.9.0'},
 prompts:[{name:'photo_countables',version:'v3',sha256:'a'.repeat(64),calls:52}],components:['photo_countables','photo_countables/v3'],
 models_requested:['gpt-6.1-sol'],models_answered:['gpt-6.1-sol'],reasoning_effort:['low'],
 labels:{photo_components:{file:'photo_components_v1.json',version:'v1',sha256:'a'.repeat(64),scored_at:'2026-10-21T11:00:00+00:00'}},split:'dev',repeats:3,
 scores:{photo_components:{f1:0.7406,precision:0.66}},headline:[{dataset:'photo_components',metric:'f1',value:0.7406}],
 spend:{cost_usd:1.5,cost_source:'ledger',calls:52,tokens:{input:120000,output:4000,cache_read:0,cache_creation:0,reasoning:900},duration_seconds:720,duration_source:'recorded'},
 record:{manifest:true,backfilled:false,format:1,error:null},selection:{status:'current',reasons:['photo_countables v3 on gpt-6.1-sol is the pipeline\'s (VISION_MODEL)']},
 report_sections:[15],files:{manifest:'/v1/ops/eval-runs/cHJlZGljdGlvbnMvYXJjaDI2/files/manifest',report:'/v1/ops/eval-runs/cHJlZGljdGlvbnMvYXJjaDI2/files/report',ledger:'/v1/ops/eval-runs/cHJlZGljdGlvbnMvYXJjaDI2/files/ledger',console:'/v1/ops/eval-runs/cHJlZGljdGlvbnMvYXJjaDI2/files/console'},
 ...over,
});
const page=items=>({status:200,data:{items,next_cursor:null,total:items.length}});
const done=(result,extra={})=>({status:'done',result,...extra});

test('every step maps to registry components: none for the reviews, the photo step to its prompts and detector',()=>{
 for(const n of H.FLOW.flat()){
  const step=CONTENT.steps[n.id];
  assert.ok(Array.isArray(step.components),n.id);
  assert.deepEqual(step.components,[...new Set(step.components)],n.id);
  if(n.review||n.signing)assert.deepEqual(step.components,[],n.id);
 }
 assert.ok(CONTENT.steps.photos.components.includes('photo_countables')&&CONTENT.steps.photos.components.includes('detection'));
 assert.ok(CONTENT.steps.inventory.components.includes('room_inventory'));
 assert.deepEqual(fresh().componentsOf('appraisal-review'),[]);
 assert.deepEqual(fresh().componentsOf('photos'),CONTENT.steps.photos.components);
 assert.deepEqual(fresh().componentsOf('nope'),[]);
 // The dialog renders the block for every step, inside Tests & results.
 for(const n of H.FLOW.flat()){const html=fresh().render(n,services(),H,{status:'idle'});assert.ok(html.includes('id="run-records"'),n.id);assert.ok(html.indexOf('Run records')>html.indexOf('Tests &amp; results'),n.id)}
});
test('a review step says no run record applies; an unconnected step asks to connect and names the components',()=>{
 const review=fresh().renderRunRecords([],{status:'idle'},H);
 assert.ok(review.includes('no run record applies'));
 const idle=fresh().renderRunRecords(['photo_countables','detection'],{status:'idle'},H);
 assert.ok(idle.includes('Connect diagnostics')&&idle.includes('<code>photo_countables</code>')&&idle.includes('<code>detection</code>'));
 assert.ok(!idle.includes('<table'));
 assert.ok(fresh().renderRunRecords(['x'],{status:'loading'},H).includes('Reading run records'));
 assert.ok(fresh().renderRunRecords(['x'],undefined,H).includes('Connect diagnostics'));
});
test('each feed failure has its own sentence and never a table or a "no runs" claim',()=>{
 const cases=[
  [{status:0,error:'untrusted_origin'},'trusted API origin'],
  [{status:0,error:'auth'},'sign-in service'],
  [{status:0,error:'timeout'},'did not answer in time'],
  [{status:0,error:'network'},'Run records unavailable'],
  [{status:401,data:null},'Sign in to Basis'],
  [{status:403,data:{code:'BASIS-API-043'}},'operator access'],
  [{status:404,data:{code:'BASIS-API-046',detail:'No evaluation data on this host: BASIS_EVAL_DATA_DIR is not set.'}},'BASIS_EVAL_DATA_DIR is not set'],
  [{status:404,data:{code:'BASIS-API-011'}},'endpoint unavailable'],
  [{status:429,data:null},'rate limited'],
  [{status:500,data:null},'HTTP 500'],
  [{status:200,data:{items:'nope'}},'does not match the supported contract'],
  [{status:200,data:{items:[{}],total:1}},'does not match the supported contract'],
  [{status:200,data:null},'does not match the supported contract'],
 ];
 for(const [result,needle] of cases){
  const html=fresh().renderRunRecords(['photo_countables'],done(result),H);
  assert.ok(html.includes(needle),`${JSON.stringify(result)} -> ${needle}`);
  assert.ok(!html.includes('<table')&&!/no run that names/i.test(html),needle);
  assert.equal(A.runsMessage(result)!==null,true);
 }
 assert.equal(A.runsMessage(page([])),null);
 const none=fresh().renderRunRecords(['photo_marks'],done(page([])),H);
 assert.ok(none.includes('no run that names')&&none.includes('<code>photo_marks</code>')&&!none.includes('<table'));
});
test('runs are grouped by label file and split, never compared across them, with every figure and its source',()=>{
 const items=[
  run(),
  run({run_id:'two',name:'arch26_countables_v2_sol',version:'v2',prompts:[{name:'photo_countables',version:'v2',sha256:null,calls:null}],git:{sha:null,dirty:null,release:null},
   spend:{cost_usd:1.1,cost_source:'summary',calls:null,tokens:null,duration_seconds:1123200,duration_source:'file_times'},record:{manifest:true,backfilled:true,format:1,error:null},
   selection:{status:'alternative',reasons:['photo_countables: the run used v2, the pipeline runs v3']},split:null,files:{manifest:'/v1/ops/eval-runs/two/files/manifest',report:'/v1/ops/eval-runs/two/files/report',ledger:null,console:null}}),
  run({run_id:'three',name:'held_out_check',labels:{photo_components:{file:'photo_components_v2.json',version:'v2',sha256:null,scored_at:null}},split:'held_out',headline:[{dataset:'photo_components',metric:'f1',value:0.81}]}),
  run({run_id:'four',name:'unscored',labels:{},scores:{},headline:[],status:null,results:null,models_requested:[],reasoning_effort:[],component:null,version:null,prompts:[],
   spend:{cost_usd:null,cost_source:null,calls:null,tokens:null,duration_seconds:null,duration_source:null},record:{manifest:false,backfilled:false,format:null,error:null},
   selection:{status:'unknown',reasons:['the run names no prompt']},files:{manifest:null,report:null,ledger:null,console:null}}),
  run({run_id:'five',name:'broken',record:{manifest:true,backfilled:false,format:null,error:'AttributeError: str has no attribute get'},labels:{},headline:[],selection:{status:'unknown',reasons:['the record could not be read']},files:{manifest:'/v1/ops/eval-runs/five/files/manifest',report:null,ledger:null,console:null}}),
 ];
 const html=fresh().renderRunRecords(['photo_countables'],done(page(items)),H);
 const groups=[...html.matchAll(/<h4>(.*?) <span/g)].map(m=>m[1]);
 assert.deepEqual(groups,['Scored against photo_components_v1 · split dev','Scored against photo_components_v1 · split not recorded','Scored against photo_components_v2 · split held_out','Unscored runs (no label file recorded)']);
 assert.equal((html.match(/<table/g)||[]).length,4);
 // The first group holds the v3 run alone; the v2 run (split unknown) is in its own table.
 const first=html.split('<h4>')[1];
 assert.ok(first.includes('arch26_countables_v3_sol')&&!first.includes('arch26_countables_v2_sol'));
 assert.ok(html.includes('Current pipeline')&&html.includes('Alternative')&&html.includes('Standing unknown'));
 assert.ok(html.includes('photo_components f1 = <strong>0.741</strong>')&&html.includes('<strong>0.810</strong>'));
 assert.ok(html.includes('$1.50 <small>recorded calls</small>')&&html.includes('$1.10 <small>runner&#39;s summary</small>'));
 assert.ok(html.includes('120000 in / 4000 out · 900 reasoning'));
 assert.ok(html.includes('12.0 min')&&html.includes('13.0 days <small>estimated from file times</small>'));
 assert.ok(html.includes('<code>bbbbbbb</code>')&&html.includes('v0.9.0'));
 assert.ok(html.includes('50 passed / 2 failed / 0 test errors'));
 assert.ok(html.includes('Recorded manifest')&&html.includes('Backfilled manifest')&&html.includes('run.json only')&&html.includes('Record unreadable')&&html.includes('AttributeError'));
 // Unknowns are "Not recorded", never zero or a guess.
 const unscored=html.split('<h4>')[4];
 assert.ok((unscored.match(/Not recorded/g)||[]).length>=5&&unscored.includes('Not scored')&&unscored.includes('No cases recorded'));
 assert.ok(!unscored.includes('$0')&&!unscored.includes('0 s'));
 // File buttons only for the files a run has, carrying the API's own relative link.
 assert.equal((html.match(/class="run-file"/g)||[]).length,4+2+4+0+1);
 assert.ok(html.includes('data-run-file="/v1/ops/eval-runs/two/files/report"')&&!html.includes('data-run-file="null"'));
 assert.ok(html.includes('5 of 5 runs for <code>photo_countables</code>'));
 assert.ok(html.includes('same label files and split'));
 assert.ok(!html.includes('Load older runs'));
});
test('older pages append behind a button and hostile text from the feed is escaped',()=>{
 const items=[run(),run({run_id:'x',name:'<img src=x onerror=1>',component:'<b>c</b>',models_requested:['<script>1</script>'],git:{sha:'<i>sha</i>',dirty:true,release:'<u>r</u>'},
  selection:{status:'weird',reasons:['<x>']},headline:[{dataset:'<d>',metric:'<m>',value:'nope'}],record:{manifest:true,backfilled:false,format:1,error:'<e>'},files:{manifest:'javascript:alert(1)',report:null,ledger:null,console:null}})];
 const html=fresh().renderRunRecords(['photo_countables'],done({status:200,data:{items,next_cursor:'bmV4dA',total:9}},{items,next:'bmV4dA'}),H);
 assert.ok(html.includes('data-run-more="bmV4dA"')&&html.includes('2 of 9 shown'));
 for(const bad of ['<img src=x','<script>1','<b>c</b>','<i>sha</i>','<u>r</u>','<x>','<d>','<e>'])assert.ok(!html.includes(bad),bad);
 assert.ok(html.includes('&lt;script&gt;1&lt;/script&gt;')&&html.includes('Standing unknown')&&html.includes('= <strong>null</strong>'));
 assert.ok(html.includes('dirty tree'));
 // The client refuses a file link that is not the API's own.
 const client=C.createClient({base:'http://127.0.0.1:8000',getToken:async()=>'t',fetchImpl:async()=>{throw new Error('must not be called')}});
 return client.evalRunFile('javascript:alert(1)').then(r=>assert.equal(r.error,'network'));
});
test('the client reads run records and files with the bearer token and the repeated component parameter',async()=>{
 const calls=[];
 const fetchImpl=async(url,options)=>{calls.push({url,options});
  if(url.includes('/files/'))return {status:200,headers:new Map([['content-type','application/json'],['content-disposition','attachment; filename="arch26.manifest.json"']]),text:async()=>'{"name":"arch26"}',json:async()=>({})};
  return {status:200,headers:new Map([['content-type','application/json']]),json:async()=>({items:[],next_cursor:null,total:0})}};
 const client=C.createClient({base:'http://127.0.0.1:8000',getToken:async()=>'session-token',fetchImpl});
 const list=await client.evalRuns({component:['photo_countables','detection'],limit:25,cursor:'abc'});
 assert.equal(list.status,200);assert.deepEqual(list.data,{items:[],next_cursor:null,total:0});
 assert.equal(calls[0].url,'http://127.0.0.1:8000/v1/ops/eval-runs?component=photo_countables&component=detection&limit=25&cursor=abc');
 assert.equal(calls[0].options.headers.Authorization,'Bearer session-token');assert.equal(calls[0].options.method,'GET');
 const file=await client.evalRunFile('/v1/ops/eval-runs/cHJl/files/manifest');
 assert.equal(file.status,200);assert.equal(file.text,'{"name":"arch26"}');assert.equal(file.filename,'arch26.manifest.json');assert.equal(file.contentType,'application/json');
 assert.equal(calls[1].options.headers.Authorization,'Bearer session-token');
 assert.equal(client.evalRunsPath({component:'room_inventory'}),'/v1/ops/eval-runs?component=room_inventory&limit=50');
 assert.equal(C.attachmentName('attachment; filename="a.b"'),'a.b');assert.equal(C.attachmentName(null),null);
 // No session: no request leaves the page.
 let called=false;const anonymous=C.createClient({base:'http://127.0.0.1:8000',fetchImpl:async()=>{called=true}});
 assert.equal((await anonymous.evalRuns({component:['x']})).status,401);assert.equal(called,false);
 // An untrusted origin cannot receive the token.
 const other=C.createClient({base:'http://evil.example',trustedOrigin:'http://127.0.0.1:8000',getToken:async()=>'t',fetchImpl:async()=>{called=true}});
 assert.equal((await other.evalRuns({component:['x']})).error,'untrusted_origin');assert.equal(called,false);
});
test('a download falls back to the API\'s own file naming and sub-second durations are not zero',()=>{
 assert.equal(A.fileName('manifest','arch26_countables_v3_sol'),'arch26_countables_v3_sol.manifest.json');
 assert.equal(A.fileName('ledger','llm_all@gpt-6-luna'),'llm_all@gpt-6-luna.llm_calls.jsonl');
 assert.equal(A.fileName('console','a b/c'),'a_b_c.console.log');assert.equal(A.fileName('odd','x'),'x.txt');
 assert.equal(A.duration(0.12),'< 1 s');assert.equal(A.duration(0),'< 1 s');assert.equal(A.duration(61),'61 s');assert.equal(A.duration(null),'Not recorded');
 const html=fresh().renderRunRecords(['photo_countables'],done(page([run()])),H);
 assert.ok(html.includes('data-run-kind="manifest"')&&html.includes('data-run-kind="console"'));
});
test('a problem body on a file read is parsed so the page can explain it',async()=>{
 const fetchImpl=async()=>({status:404,headers:new Map([['content-type','application/problem+json']]),json:async()=>({code:'BASIS-API-046',detail:'No evaluation data on this host: BASIS_EVAL_DATA_DIR is not set.'})});
 const client=C.createClient({base:'http://127.0.0.1:8000',getToken:async()=>'t',fetchImpl});
 const result=await client.evalRunFile('/v1/ops/eval-runs/x/files/manifest');
 assert.equal(result.status,404);assert.equal(result.data.code,'BASIS-API-046');
 assert.ok(A.runsMessage(result).includes('No evaluation data on this host'));
});
test('nothing in the block says a study is signed or completed, and the health dialog text is unchanged',()=>{
 const html=fresh().render(node('photos'),services(),H,done(page([run()])));
 assert.ok(html.includes('never says whether a study is signed or completed'));
 assert.ok(!/this study is (signed|completed)/i.test(html));
 assert.ok(html.includes('historical measurements, not a live benchmark')&&html.includes('arch26_countables_v3_sol'));
});
