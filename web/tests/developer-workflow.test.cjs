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

test('the content file has every workflow step, three explanation steps each with the systems they run, and loads',()=>{
 assert.ok(fresh().validContent(CONTENT));
 for(const n of H.FLOW.flat()){const step=CONTENT.steps[n.id];assert.ok(step,n.id);assert.equal(step.how.length,3);assert.ok(step.results.summary);assert.ok(step.links.length>0);
  for(const part of step.how){assert.ok(Array.isArray(part.uses)&&part.uses.length>0,`${n.id}: ${part.title}`);for(const u of part.uses)assert.ok(u.name&&u.system&&u.provider,JSON.stringify(u))}}
});
test('every dialog explains its parts in plain words with the systems each one runs, and has no runtime table',()=>{
 for(const n of H.FLOW.flat()){
  const html=fresh().render(n,services(),H);
  assert.ok(html.includes('class="dialog-section-title">How it works')&&html.includes('class="dialog-section-title">Tests &amp; results'),n.id);
  assert.ok(!html.includes('Current runtime'),n.id);
  assert.ok(html.includes('class="uses"'),n.id);
  assert.ok(!/[A-Z]+_MODEL\b/.test(html.split('Tests &amp; results')[0]),`${n.id}: a setting name in How it works`);
 }
});
test('build inventory names the models it runs today, with provider, effort and temperature, and the logos',()=>{
 const html=fresh().render(node('inventory'),services(),H).split('Tests &amp; results')[0];
 assert.ok(html.includes('Count each object once'));
 assert.ok(html.includes('<code>gpt-6-luna</code>')&&html.includes('<code>claude-sonnet-5-5</code>'));
 assert.ok(html.includes('effort low')&&html.includes('temperature default (not settable)'));
 assert.ok(html.includes('logos/openai.svg')&&html.includes('logos/claude.svg'));
 assert.ok(!html.includes('Mask2Former'),'off by default: not listed as in use');
});
test('photo analysis lists both counting models, the surfaces model and SAM 3 with its logo',()=>{
 const html=fresh().render(node('photos'),services(),H).split('Tests &amp; results')[0];
 assert.ok(html.includes('<code>gpt-6.1-sol</code>')&&html.includes('<code>gpt-6-luna</code>')&&html.includes('<code>claude-opus-5-5</code>'));
 assert.ok(html.includes('effort medium'));
 assert.ok(html.includes('<code>SAM 3</code>')&&html.includes('logos/meta.svg'));
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
test('a missing or malformed content file says the content is not loaded and shows no results',async()=>{
 assert.equal(A.setContent({schema_version:2}),false);
 const html=A.render(node('photos'),services(),H);
 assert.ok(html.includes('does not match the supported shape'));
 assert.ok(!html.includes('Current runtime')&&!html.includes('Tests &amp; results'));
 assert.equal(await A.load(async()=>({ok:false,status:404})),false);
 assert.ok(A.contentError.includes('404'));
 assert.equal(await A.load(async()=>{throw new Error('offline')}),false);
 assert.equal(await A.load(async()=>({ok:true,json:async()=>CONTENT})),true);
});
test('text in a part is escaped, and a malformed health route cannot name a model',()=>{
 const c=JSON.parse(JSON.stringify(CONTENT));
 c.steps.photos.how[0].uses=[{name:'<img src=x onerror=1>',system:'<script>1</script>',provider:'p',brand:'no-such-brand'}];
 const A2=fresh();A2.setContent(c);
 const html=A2.render(node('photos'),services(),H);
 assert.ok(!html.includes('<img src=x')&&!html.includes('<script>1')&&html.includes('&lt;script&gt;1'));
 assert.ok(html.includes('class="uses-dot"')||!html.includes('logos/no-such-brand'));
 const hostile={schema_version:1,services:[{id:'vision',...ok,models:[{setting:'VISION_MODEL',model:'<script>1</script>',primary:{provider:'openai'},fallbacks:[{}]},{setting:7}]}]};
 const route=H.normalize(hostile.services[0],now).models;
 assert.equal(route.length,1);assert.equal(route[0].primary,null);assert.deepEqual(route[0].fallbacks,[]);
});
test('nothing in a dialog says a study is signed or completed',()=>{
 for(const n of H.FLOW.flat()){const html=fresh().render(n,services(),H);assert.ok(!/<(strong|span|h\d)[^>]*>\s*(study )?(signed|completed)\s*</i.test(html),n.id);assert.ok(!/this study is (signed|completed)/i.test(html),n.id)}
});
test('every source path and link in the content points into the repository',()=>{
 const root=path.join(__dirname,'..','..');
 for(const step of Object.values(CONTENT.steps)){
  for(const how of step.how)assert.ok(fs.existsSync(path.join(root,how.source)),how.source);
  for(const link of step.links)assert.ok(fs.existsSync(path.join(root,link.path.split('#')[0])),link.path);
 }
});
test('a comparison reads for a person: the question, each setup with its exact models, effort and temperature, tokens, and what each column means',()=>{
 const html=fresh().render(node('photos'),services(),H);
 assert.ok(html.includes('Which setup counts the objects in a photo most accurately?'));
 assert.ok(html.includes('Two models count each photo'));
 assert.ok(html.includes('<code>gpt-6.1-sol</code>')&&html.includes('<code>claude-opus-5-5</code>'));
 assert.ok(html.includes('effort medium')&&html.includes('temperature default (not settable)'));
 assert.ok(html.includes('Tokens (input / output)')&&html.includes('Not recorded'));
 assert.ok(html.includes('What the columns mean')&&html.includes('lower is better'));
 assert.ok(html.includes('Model names used on this page')&&html.includes('$2 / $10'));
 assert.ok(!html.includes('sol low + Opus medium per class'),'the engineering label is replaced by the plain name');
});
test('results are filed under the part of the step they measured, and a part with none says why',()=>{
 const html=fresh().render(node('extract'),services(),H);
 const parts=html.split('class="part-results"').length-1;
 assert.equal(parts,3);
 assert.ok(html.includes('No comparison table: the presence rules'));
 assert.ok(!html.includes('No comparison has been measured for this part yet.'));
});
test('a setup without a model says so, and row text is escaped',()=>{
 const A2=fresh(),c=JSON.parse(JSON.stringify(CONTENT));
 c.steps.photos.results.comparisons[0].rows[0].name='<img src=x onerror=alert(1)>';
 c.steps.photos.results.comparisons[0].rows[0].setup=[];c.steps.photos.results.comparisons[0].rows[0].setup_note='No AI model';
 A2.setContent(c);
 const html=A2.render(node('photos'),services(),H);
 assert.ok(!html.includes('<img src=x')&&html.includes('&lt;img src=x'));
 assert.ok(html.includes('No AI model'));
});
test('the parts Basis built carry the Basis mark; a provider keeps its own logo',()=>{
 const html=fresh().render(node('assets'),services(),H).split('Tests &amp; results')[0];
 assert.ok(html.includes('src="../assets/basis-mark.png"'));
 assert.ok(html.includes('Basis classification rules')&&html.includes('Basis quantity takeoff')&&html.includes('Basis structure model'));
 assert.ok(html.includes('logos/claude.svg'));
 assert.ok(!html.includes('No AI in this part'));
 assert.ok(H.logo('basis').includes('../assets/basis-mark.png')&&H.logo('openai').includes('logos/openai.svg'));
});
test('the workflow cards say who does each step and show the systems it runs, with logos',()=>{
 const A2=fresh();
 const html=H.renderFlow(services(),A2.systemsByStep());
 for(const title of ['Engineer uploads the files','Basis reads the appraisal','Basis analyzes the photos','Engineer reviews the appraisal','Engineer signs the report'])assert.ok(html.includes(title),title);
 const photos=html.split('data-node="photos"')[1].split('</article>')[0];
 assert.ok(photos.includes('class="system-chip"')&&photos.includes('gpt-6.1-sol')&&photos.includes('claude-opus-5-5')&&photos.includes('SAM 3')&&photos.includes('logos/meta.svg'));
 assert.ok(!photos.includes('Basis API'));
 // Without the content file the cards fall back to the health services.
 assert.ok(H.renderFlow(services()).includes('data-service='));
});
