const test=require('node:test'),assert=require('node:assert/strict');
const H=require('../developer/health-model.js'),C=require('../developer/health-client.js');
const now=Date.parse('2026-10-31T14:00:00Z'),at=new Date(now).toISOString();
const ok={status:'healthy',check_kind:'probe',checked_at:at};
const basic={status:200,data:{status:'ok',service:'basis',checks:{db:{status:'ok',latency_ms:1},temporal:{status:'fail',latency_ms:7}}},latency_ms:8};
test('200 API with failed Temporal never produces all-green health',()=>{const rows=H.snapshot(basic,null,now);assert.equal(rows.find(s=>s.id==='api').status,'healthy');assert.equal(rows.find(s=>s.id==='temporal').status,'down');assert.equal(rows.find(s=>s.id==='openai').status,'unknown');assert.equal(H.aggregate(rows),'down')});
test('database checks survive a 503 problem response',()=>{const rows=H.snapshot({status:503,data:{checks:{db:{status:'fail'},temporal:{status:'ok'}}}},null,now);assert.equal(rows.find(s=>s.id==='db').status,'down');assert.equal(rows.find(s=>s.id==='temporal').status,'healthy');assert.equal(rows.find(s=>s.id==='api').status,'degraded')});
test('API network failure does not falsely claim all providers failed',()=>{const rows=H.snapshot({error:'network'},null,now);assert.equal(rows[0].status,'down');assert.ok(rows.slice(1).every(s=>s.status==='unknown'))});
test('missing checks, unknown schema, HTML and arbitrary 200 JSON are unknown',()=>{for(const data of [null,{},'html',{status:'ok'}])assert.equal(H.snapshot({status:200,data},{schema_version:2,services:[{id:'openai',...ok}]},now).find(s=>s.id==='openai').status,'unknown');assert.equal(H.basicChecks({status:200,data:{}},now).api.status,'unknown')});
test('evidence must be recent, timestamped and stronger than configuration',()=>{assert.equal(H.normalize(ok,now).status,'healthy');assert.equal(H.normalize({...ok,checked_at:null},now).status,'unknown');assert.equal(H.normalize({...ok,checked_at:new Date(now+61000).toISOString()},now).status,'unknown');assert.equal(H.normalize({...ok,check_kind:'configuration'},now).status,'unknown');assert.equal(H.normalize({...ok,check_kind:'other'},now).status,'unknown');assert.equal(H.normalize(ok,now+120001).status,'stale');assert.equal(H.normalize({...ok,expires_at:new Date(now-1).toISOString()},now).status,'stale')});
test('disabled optional dependencies are neutral but required disabled is degraded',()=>{assert.equal(H.aggregate([{...ok,status:'healthy'},{status:'disabled',required:false}]),'healthy');assert.equal(H.aggregate([{status:'disabled',required:true}]),'degraded');assert.equal(H.aggregate([{status:'disabled',required:false}]),'unknown')});
test('latency zero is preserved and malformed latency is rejected',()=>{assert.equal(H.normalize({...ok,latency_ms:0},now).latency_ms,0);for(const v of [-1,Infinity,'2'])assert.equal(H.normalize({...ok,latency_ms:v},now).latency_ms,null)});
test('runtime evidence updates known services and cannot override a direct failing probe',()=>{const rows=H.snapshot(basic,{schema_version:1,services:[{id:'temporal',...ok},{id:'sam3',...ok,adapter:'replicate',model:'version-x'}]},now);assert.equal(rows.find(s=>s.id==='temporal').status,'down');assert.equal(rows.find(s=>s.id==='sam3').adapter,'replicate')});
test('service details escape backend text; unknown state has text as well as color',()=>{const rows=H.snapshot(basic,{schema_version:1,services:[{id:'sam3',...ok,detail:'<script>secret</script>',model:'<img src=x>'}]},now);const html=H.renderServices(rows);assert.ok(html.includes('&lt;script&gt;'));assert.ok(!html.includes('<script>'));assert.ok(html.includes('Not checked'));assert.ok(H.renderServices(rows,{query:'sam 3'}).includes('SAM 3'));assert.ok(!H.renderServices(rows,{query:'sam 3'}).includes('PostgreSQL'))});
test('workflow includes both parallel branches and four engineer checkpoints',()=>{const nodes=H.FLOW.flat();assert.equal(nodes.filter(n=>n.review).length,4);assert.deepEqual(H.FLOW[1].map(n=>n.id),['extract','photos']);const ids=new Set(H.SERVICES.map(s=>s.id));for(const n of nodes)for(const id of n.services)assert.ok(ids.has(id));const html=H.renderFlow(H.snapshot(basic,null,now));assert.ok(html.includes('Runs in parallel'));assert.ok(html.includes('SAM 3'));assert.ok(html.includes('Engineer review'))});
test('refresh uses only GET health paths, never provider APIs and no token on public probe',async()=>{const calls=[];const client=C.createClient({base:'http://127.0.0.1:8000',getToken:async()=>'session-token',fetchImpl:async(url,options)=>{calls.push({url,options});return {status:200,json:async()=>({})}}});await client.health();await client.diagnostics();assert.deepEqual(calls.map(c=>c.url),['http://127.0.0.1:8000/health','http://127.0.0.1:8000/v1/ops/health']);assert.ok(calls.every(c=>c.options.method==='GET'&&c.options.redirect==='error'));assert.equal(calls[0].options.headers.Authorization,undefined);assert.equal(calls[1].options.headers.Authorization,'Bearer session-token')});
test('private diagnostics do not request without a session',async()=>{let called=false;const client=C.createClient({base:'http://localhost:8000',fetchImpl:async()=>{called=true}});assert.equal((await client.diagnostics()).status,401);assert.equal(called,false)});
test('client retains partial JSON on errors and handles non-JSON',async()=>{for(const status of [401,403,404,429,503]){const client=C.createClient({base:'http://localhost:8000',fetchImpl:async()=>({status,json:async()=>({checks:{db:{status:'fail'}}})})});assert.equal((await client.health()).status,status)}const client=C.createClient({base:'http://localhost:8000',fetchImpl:async()=>({status:200,json:async()=>{throw new Error()}})});assert.equal((await client.health()).data,null)});
test('requests time out and errors are sanitized',async()=>{const client=C.createClient({base:'http://localhost:8000',timeoutMs:5,fetchImpl:async(url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Object.assign(new Error('secret'),{name:'AbortError'}))))});assert.deepEqual(await client.health(),{status:0,data:null,error:'timeout'})});
test('API origin rejects credentials, paths, query and non-HTTP schemes',()=>{for(const url of ['https://user:password@example.com','file:///tmp/x','https://example.com/api','https://example.com/?token=x'])assert.throws(()=>C.baseUrl(url));assert.equal(C.baseUrl('http://localhost:8000/'),'http://localhost:8000')});

test('auth refresh failures are distinct from API outages and auth waits are bounded',async()=>{
 const broken=C.createClient({base:'http://localhost:8000',getToken:async()=>{throw Error('private session error')}});
 assert.equal((await broken.diagnostics()).error,'auth');
 let called=false;const hung=C.createClient({base:'http://localhost:8000',timeoutMs:5,getToken:()=>new Promise(()=>{}),fetchImpl:async()=>{called=true}});
 assert.equal((await hung.diagnostics()).error,'timeout');assert.equal(called,false);
});
test('query-selected origins cannot receive authenticated diagnostics',async()=>{
 let tokenRead=false,called=false;const client=C.createClient({base:'https://unexpected.example',trustedOrigin:'http://localhost:8000',getToken:async()=>{tokenRead=true;return 'token'},fetchImpl:async()=>{called=true}});
 assert.equal((await client.diagnostics()).error,'untrusted_origin');assert.equal(tokenRead,false);assert.equal(called,false);
});
test('disabled configuration ages out too and empty dependency sets are never green',()=>{
 assert.equal(H.normalize({...ok,status:'disabled'},now+120001).status,'stale');assert.equal(H.aggregate([]),'unknown');
});

test('all failures lead across categories and no service appears twice',()=>{
 const rows=H.snapshot(basic,{schema_version:1,services:[{id:'replicate',...ok,status:'down'},{id:'vision',...ok,status:'degraded'},{id:'sam3',...ok,checked_at:new Date(now-180000).toISOString()}]},now);
 const groups=H.serviceGroups(rows),ids=groups.flatMap(g=>g.rows.map(s=>s.id));
 assert.equal(groups[0].name,'Needs attention');
 assert.deepEqual(groups[0].rows.map(s=>s.id),['temporal','replicate','vision','sam3']);
 assert.equal(new Set(ids).size,H.SERVICES.length);
 assert.equal(H.serviceGroups(rows,{query:'replicate'})[0].rows[0].id,'replicate');
 assert.ok(H.serviceGroups(rows,{attention:true}).flatMap(g=>g.rows).every(s=>s.status!=='healthy'));
});
test('a recovered service returns to its category without retaining a failure',()=>{
 const healthyBasic={status:200,data:{status:'ok',service:'basis',checks:{db:{status:'ok'},temporal:{status:'ok'}}}};
 const rows=H.snapshot(healthyBasic,{schema_version:1,services:[{id:'replicate',...ok}]},now);
 assert.equal(H.serviceGroups(rows)[0].name,'Infrastructure');
 assert.ok(!H.renderServices(rows).includes('service-down'));
});
test('fresh public probes retain runtime metadata without inheriting expired status evidence',()=>{
 const rows=H.snapshot(basic,{schema_version:1,services:[{id:'temporal',...ok,adapter:'Temporal',hosting:'Local Docker',fallbacks:[],expires_at:new Date(now-1).toISOString()}]},now);
 const temporal=rows.find(s=>s.id==='temporal');
 assert.equal(temporal.status,'down');assert.equal(temporal.hosting,'Local Docker');assert.deepEqual(temporal.fallbacks,[]);
 const api=H.snapshot({status:200,data:{status:'ok',service:'basis'}},{schema_version:1,services:[{id:'api',...ok,expires_at:new Date(now-1).toISOString()}]},now)[0];
 assert.equal(api.status,'healthy');
});
test('short expiry survives redraw and last inference is not readiness evidence',()=>{
 const s=H.normalize({...ok,expires_at:new Date(now+1000).toISOString()},now);
 assert.equal(H.normalize(s,now+1001).status,'stale');
 const last={model:'observed-model',at};
 assert.equal(H.normalize({last_inference:last},now).status,'unknown');
 assert.deepEqual(H.normalize({last_inference:last},now).last_inference,last);
});
test('hosting and backups distinguish unknowns from configured adapters and empty fallback routes',()=>{
 assert.equal(H.hostLabel({adapter:'Temporal'}),'Not reported');
 assert.equal(H.hostLabel({adapter:'replicate'}),'Replicate');
 assert.equal(H.normalize({},now).fallbacks,null);
 const rows=H.snapshot(basic,{schema_version:1,services:[{id:'sam3',...ok,model:'SAM 3',adapter:'replicate',fallbacks:[],hosting:'<script>host</script>'}]},now);
 const html=H.renderServices(rows,{query:'SAM 3'});
 assert.ok(html.includes('logos/meta.svg')&&html.includes('logos/replicate.svg'));
 assert.ok(html.includes('None configured'));assert.ok(html.includes('&lt;script&gt;host'));assert.ok(!html.includes('<script>'));
});
test('every workflow box has a simple three-step explanation and source links',()=>{
 const A=require('../developer/architecture.js');
 const services=H.snapshot(basic,null,now);
 for(const node of H.FLOW.flat()){
  assert.equal(A.STEPS[node.id].steps.length,3);
  const html=A.render(node,services,H);
  assert.ok(html.includes('How it works'));assert.ok(html.includes('Current runtime'));assert.ok(html.includes('https://github.com/bryanrg22/Basis_Info/blob/15cd291/'));
 }
 assert.equal(A.PHOTO_ROWS[0].selected,true);
 assert.equal(A.PHOTO_ROWS[0].error,'0.124');assert.equal(A.PHOTO_ROWS[0].cost,'$2.20');
 assert.ok(A.PHOTO_ROWS.some(r=>r.seconds==='Not measured'));
 const photos=A.render(H.FLOW.flat().find(n=>n.id==='photos'),services,H);
 assert.ok(photos.includes('pixel masks, outlines and bounding boxes'));
 assert.ok(photos.includes('historical results, not a live benchmark'));
});
test('workflow detail escapes runtime metadata and exposes keyboard dialog controls',()=>{
 const A=require('../developer/architecture.js');
 const services=H.snapshot(basic,{schema_version:1,services:[{id:'vision',...ok,model:'<img src=x onerror=alert(1)>',hosting:'<script>host</script>',fallbacks:['<script>backup</script>']}]},now);
 const html=A.render(H.FLOW.flat().find(n=>n.id==='photos'),services,H);
 assert.ok(!html.includes('<img src=x'));assert.ok(!html.includes('<script>'));
 assert.ok(html.includes('&lt;script&gt;backup'));
 assert.equal((H.renderFlow(services).match(/aria-haspopup="dialog"/g)||[]).length,H.FLOW.flat().length);
});


test('workflow phases are numbered consistently and every node exposes health before its title',()=>{
 const rows=H.snapshot(basic,null,now),html=H.renderFlow(rows);
 for(let i=1;i<=H.FLOW.length;i++)assert.ok(html.includes(`Step ${i} of ${H.FLOW.length}`));
 assert.equal(H.PHASES.length,H.FLOW.length);
 for(const article of html.split('<article ').slice(1)){assert.ok(article.indexOf('node-status')<article.indexOf('<h3>'));assert.ok(article.includes('status-icon'));}
 assert.equal((html.match(/class="node-open-icon"/g)||[]).length,H.FLOW.flat().length);
 assert.ok(!html.includes('↗'));
});
test('status counts reconcile with services and legend supplies distinct symbols and labels',()=>{
 const rows=H.snapshot(basic,null,now),html=H.renderCounts(rows);
 const totals=[...html.matchAll(/<strong>(\d+)<\/strong>/g)].map(m=>Number(m[1]));
 assert.equal(totals.reduce((a,b)=>a+b,0),rows.length);
 for(const status of Object.keys(H.LABELS)){assert.ok(H.renderLegend().includes(H.LABELS[status]));assert.ok(H.badge(status).includes('aria-hidden="true"'));}
 assert.equal(new Set(Object.keys(H.LABELS).map(H.statusIcon)).size,6);
});
test('workflow connections preserve split, join and vertical review edges',()=>{
 const nodes=new Set(H.FLOW.flat().map(n=>n.id));
 for(const [a,b] of H.EDGES){assert.ok(nodes.has(a)&&nodes.has(b));}
 assert.equal(H.EDGES.filter(e=>e[0]==='upload').length,2);
 assert.equal(H.EDGES.filter(e=>e[1]==='rooms-review').length,2);
 const a={left:0,right:290,top:100,bottom:400,width:290,height:300},b={left:338,right:628,top:100,bottom:400,width:290,height:300};
 assert.equal(H.connectionPath('upload','extract',a,b),'M 290 250 H 312.5 V 250 H 335');
 assert.notEqual(H.connectionPath('appraisal-review','rooms-review',a,b),H.connectionPath('photo-results','rooms-review',a,b));
 assert.equal(H.connectionPath('inventory','components-review',a,{...a,top:438,bottom:738}),'M 145 400 V 435');
});


test('report signing follows export and stays distinct from the four review gates',()=>{
 const A=require('../developer/architecture.js'),nodes=H.FLOW.flat(),last=nodes.at(-1);
 assert.equal(last.id,'report-sign');assert.equal(last.signing,true);
 assert.equal(H.PHASES.at(-1),'Report & sign');
 assert.deepEqual(H.EDGES.filter(([from])=>from==='output'),[['output','report-sign']]);
 assert.equal(nodes.filter(n=>n.review).length,4);
 const services=H.SERVICES.map(s=>({...s,...H.normalize(ok,now)}));
 const html=H.renderFlow(services),detail=A.render(last,services,H);
 assert.ok(html.includes('Engineer signature'));
 assert.ok(html.includes('Report &amp; sign'));
 assert.ok(!html.includes('Study completed'));
 assert.match(detail,/changed preview or revision/);
 assert.match(detail,/missing or unknown evidence stays incomplete/);
 assert.match(detail,/test_report_signing.py/);
 assert.match(A.render(nodes.find(n=>n.id==='output'),services,H),/Exporting files does not complete the study/);
 for(const status of ['healthy','down','unknown','stale']){
  const rows=services.map(s=>s.id==='storage'?{...s,status}:s);
  const card=H.renderFlow(rows).split('data-node="report-sign"')[1].split('</article>')[0];
  assert.ok(card.includes(`node-${status}`));
  assert.ok(!card.includes('Study completed'));
 }
});
