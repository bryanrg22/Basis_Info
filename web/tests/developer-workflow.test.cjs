// The step dialogs of the developer page (web/developer/architecture.js): the content file
// drives the text and the figures, the health feed alone drives the runtime table, nothing
// is invented when either is missing, and backend text is escaped.
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const H=require('../developer/health-model.js'),A=require('../developer/architecture.js');
const CONTENT=JSON.parse(fs.readFileSync(path.join(__dirname,'..','developer','workflow-content.json'),'utf8'));
const now=Date.parse('2026-11-02T12:00:00Z'),at=new Date(now).toISOString();
const ok={status:'healthy',check_kind:'probe',checked_at:at};
const basic={status:200,data:{status:'ok',service:'basis',checks:{db:{status:'ok'},temporal:{status:'ok'}}}};
const hop=(provider,service,target)=>({provider,service,target});
const feed={schema_version:1,services:[
 {id:'openai',...ok,adapter:'OpenAI API',hosting:'OpenAI',fallbacks:[],model:'gpt-6.1-sol, gpt-6-luna'},
 {id:'azure_openai',...ok,adapter:'Azure OpenAI',hosting:'Microsoft Azure',fallbacks:['gpt-6.1-sol → OpenAI API · gpt-6.1-sol']},
 {id:'anthropic',...ok,adapter:'Anthropic API',hosting:'Anthropic',fallbacks:[]},
 {id:'replicate',...ok,adapter:'Replicate API',hosting:'Replicate',fallbacks:[]},
 {id:'worker',...ok,check_kind:'heartbeat',adapter:'temporal worker',model:'build feedface0011',hosting:'Docker container',fallbacks:[]},
 {id:'vision',...ok,check_kind:'heartbeat',adapter:'decomposed + count fusion',hosting:'Study worker (Docker container)',model:'gpt-6.1-sol via azure (basis-vision) · gpt-6-luna via openai · claude-opus-5-5 via anthropic',
  fallbacks:['gpt-6.1-sol → OpenAI API · gpt-6.1-sol'],
  models:[
   {setting:'VISION_MODEL',model:'gpt-6.1-sol',primary:hop('azure','azure_openai','basis-vision'),fallbacks:[hop('openai','openai','gpt-6.1-sol')]},
   {setting:'VISION_SURFACES_MODEL',model:'gpt-6-luna',primary:hop('openai','openai','gpt-6-luna'),fallbacks:[]},
   {setting:'VISION_FUSION_MODEL',model:'claude-opus-5-5',primary:hop('anthropic','anthropic','claude-opus-5-5'),fallbacks:[]}]},
 {id:'sam3',...ok,adapter:'replicate',hosting:'Replicate',model:'SAM 3 (vufinder/sam3@1bf97763d5df)',fallbacks:[],models:[]},
 {id:'room_inventory',...ok,check_kind:'heartbeat',adapter:'reconcile',hosting:'Study worker (Docker container)',fallbacks:[],
  models:[{setting:'ROOM_MODEL',model:'gpt-6-luna',primary:hop('openai','openai','gpt-6-luna'),fallbacks:[]},{setting:'ROOM_MATERIALS_MODEL',model:'claude-sonnet-5-5',primary:hop('anthropic','anthropic','claude-sonnet-5-5'),fallbacks:[]}]},
]};
const services=()=>H.snapshot(basic,feed,now);
const node=id=>H.FLOW.flat().find(n=>n.id===id);
const fresh=()=>{A.setContent(JSON.parse(JSON.stringify(CONTENT)));return A};

test('the content file has every workflow step, three explanation steps each, and loads',()=>{
 assert.ok(fresh().validContent(CONTENT));
 for(const n of H.FLOW.flat()){const step=CONTENT.steps[n.id];assert.ok(step,n.id);assert.equal(step.how.length,3);assert.ok(step.runtime.length>0);assert.ok(step.results.summary);assert.ok(step.links.length>0);
  for(const row of step.runtime)assert.ok(H.SERVICES.some(s=>s.id===row.service),row.service)}
});
test('build inventory speaks of components, with one runtime row per model role and the SAM 3 row',()=>{
 const html=fresh().render(node('inventory'),services(),H);
 assert.ok(html.includes('Count each component once'));
 assert.ok(!/room inventory call/i.test(html.split('Current runtime')[0]));
 assert.ok(html.includes('ROOM_MODEL')&&html.includes('gpt-6-luna')&&html.includes('claude-sonnet-5-5'));
 assert.ok(html.includes('logos/openai.svg')&&html.includes('logos/claude.svg')&&html.includes('logos/meta.svg'));
 assert.ok(html.includes('SAM 3 (vufinder/sam3@1bf97763d5df)'));
 // The room call and surface crops are off in this worker: not listed as if they ran.
 assert.ok(!html.includes('ROOM_CALL_MODEL')&&!html.includes('SURFACE_CROP_MODEL'));
});
test('photo analysis lists the three model roles with the deployment and the fallback, plus SAM 3',()=>{
 const html=fresh().render(node('photos'),services(),H);
 assert.ok(html.includes('gpt-6.1-sol → basis-vision'));
 assert.ok(html.includes('gpt-6.1-sol → OpenAI API · gpt-6.1-sol'));
 assert.ok(html.includes('Microsoft Azure')&&html.includes('logos/azure.svg'));
 assert.ok(html.includes('VISION_FUSION_MODEL')&&html.includes('claude-opus-5-5'));
 assert.ok(html.includes('None configured'));
 assert.ok(html.includes('Count fusion'));
});
test('without a feed the runtime table still lists every system and role as Not reported, never a typed model id',()=>{
 const html=fresh().render(node('photos'),H.snapshot(basic,null,now),H).split('<h3>Current runtime</h3>')[1].split('<h3>Tests &amp; results</h3>')[0];
 assert.ok(html.includes('VISION_MODEL')&&html.includes('VISION_SURFACES_MODEL')&&html.includes('VISION_FUSION_MODEL'));
 assert.ok(!html.includes('gpt-6.1-sol')&&!html.includes('claude-opus'));
 assert.ok((html.match(/Not reported/g)||[]).length>=8);
});
test('the measured results come from the content file with the section links and verbatim figures',()=>{
 const html=fresh().render(node('photos'),services(),H);
 assert.ok(html.includes('0.124')&&html.includes('$2.20'));
 assert.ok(html.includes('#151-every-variant-against-the-current-best'));
 assert.ok(html.includes('class="selected-architecture"'));
 assert.ok(html.includes('No regression on the final build'));
 assert.ok(html.includes('backEnd/evals/record.py')&&html.includes('backEnd/evals/registry.py'));
 assert.ok(html.includes('historical measurements, not a live benchmark'));
});
test('a step with no paid call says so instead of a cost',()=>{
 const html=fresh().render(node('photo-results'),services(),H);
 assert.ok(html.includes('No paid call in this step'));
});
test('a missing or malformed content file leaves the runtime table and says the content is not loaded',async()=>{
 assert.equal(A.setContent({schema_version:2}),false);
 const html=A.render(node('photos'),services(),H);
 assert.ok(html.includes('does not match the supported shape'));
 assert.ok(html.includes('Current runtime')&&html.includes('gpt-6.1-sol'));
 assert.ok(!html.includes('Tests &amp; results'));
 assert.equal(await A.load(async()=>({ok:false,status:404})),false);
 assert.ok(A.contentError.includes('404'));
 assert.equal(await A.load(async()=>{throw new Error('offline')}),false);
 assert.equal(await A.load(async()=>({ok:true,json:async()=>CONTENT})),true);
});
test('feed text is escaped and a malformed route cannot name a model',()=>{
 const hostile={schema_version:1,services:[{id:'vision',...ok,model:'<img src=x onerror=1>',hosting:'<b>x</b>',models:[{setting:'VISION_MODEL',model:'<script>1</script>',primary:{provider:'openai'},fallbacks:[{}]},{setting:7}]}]};
 const html=fresh().render(node('photos'),H.snapshot(basic,hostile,now),H);
 assert.ok(!html.includes('<img src=x')&&!html.includes('<script>1')&&html.includes('&lt;script&gt;1'));
 assert.ok(html.includes('no configured provider'));
 const route=H.normalize(hostile.services[0],now).models;
 assert.equal(route.length,1);assert.equal(route[0].primary,null);assert.deepEqual(route[0].fallbacks,[]);
});
test('nothing in a dialog says a study is signed or completed',()=>{
 for(const n of H.FLOW.flat()){const html=fresh().render(n,services(),H);assert.ok(html.includes('never says whether a study is signed or completed'),n.id);assert.ok(!/<(strong|span|h\d)[^>]*>\s*(study )?(signed|completed)\s*</i.test(html),n.id);assert.ok(!/this study is (signed|completed)/i.test(html),n.id)}
});
test('every source path and link in the content points into the repository',()=>{
 const root=path.join(__dirname,'..','..');
 for(const step of Object.values(CONTENT.steps)){
  for(const how of step.how)assert.ok(fs.existsSync(path.join(root,how.source)),how.source);
  for(const link of step.links)assert.ok(fs.existsSync(path.join(root,link.path.split('#')[0])),link.path);
 }
});
