// AI-drafted measurements in the app (EVAL_REPORT 110, docs/api/MEASURE_DRAFTS.md;
// web/FRONTEND_BACKLOG.md sections 23 and 24): web/live/measure-drafts.js's text and correction
// body on records shaped as basis/domain/measure_drafts.py writes them; then, on the sample API with
// &drafts=on through the real API client in a VM, the asset page's card, the held rows on Assets and
// Results (highest exposure first, Verify each), the measure tool's factor beside its total, Use this
// number and the measure tool's verified save sent as one decision, and the blocker gone after the
// rerun; last, the facade mode's corners moved, deleted, undone and redone with the keyboard, driven
// through the same document listeners a browser calls (the harness of measure-tool-drafts.test.cjs).
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const WEB=path.join(__dirname,'..'),read=f=>fs.readFileSync(path.join(WEB,f),'utf8');
const MD=require('../live/measure-drafts.js'),A=require('../live/sample-api.js'),PM=require('../live/property-model.js'),PS=require('../live/sample-property.js');
const NOW=Date.parse('2026-11-23T20:00:00Z');
const text=html=>html.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').replace(/&#39;/g,"'").replace(/&quot;/g,'"').replace(/&amp;/g,'&').trim();

/* ---------- the pure part, on records as the backend writes them ---------- */
// basis.domain.measure_drafts.draft_record + priced (a pending exterior roof draft).
const ROOF={drawn_by:'model:lidar-shell@v1',verified:false,quantity:'4216.89',unit:'SF',expected_error_pct:'10.4',error_source:'EVAL_REPORT 105.1: roof surface, two houses',
 source:'USGS 3DEP lidar (2021), public records gather g1',replaces:{formula:'footprint x pitch',quantity:'3900 SF'},factor:'1.2357',factor_reason:"the roof planes' slope, 31.0 degrees on average",
 exposure_usd:'1950.08',verify_above_usd:'250.0',blocks:true};
const COUNTER={drawn_by:'model:claude-sonnet-5-5@measure_interior_reference-v1',verified:false,quantity:'42.00',unit:'SF',expected_error_pct:'20.8',error_source:'EVAL_REPORT 107.4: countertop, 3 kitchens',
 source:'3 photos of the room',replaces:{missing_input:'cabinet_run'},photos:['d1','d2','d3'],answer:{floor_area_sf:'235.0',length_ft:'17.5',width_ft:'13.4',ceiling_height_ft:'8.0',countertop_sf:'42.0'},
 notes:'The island is half hidden.',confidence:'low',room_fully_seen:false,exposure_usd:'229.67',verify_above_usd:'250.0',blocks:false};
const asset=(id,label,draft,extra={})=>({id,label,category:label,room:{name:'Kitchen'},takeoff:{quantity:draft?.quantity??'1',unit:draft?.unit??'EA',inputs:{quantity_source:'draft',draft,...extra}}});

test('1. the factor beside the total: the shape, the factor in the record’s words, the row',()=>{
 // the backlog's example: a roof outline in plan times the lidar planes' slope
 assert.equal(MD.factorLine({factor:'1.2357',factor_reason:"the roof planes' slope"},'3412.55','SF',{kind:'area'}).text,"3,412.55 SF in plan × 1.2357 (the roof planes' slope) = 4,216.89 SF");
 // the siding: the walls' gross face less the openings
 assert.equal(MD.factorLine({factor:'0.85',factor_reason:'less 15 % openings'},'2400.00','SF',{kind:'line'}).text,'2,400 SF of wall face × 0.85 (less 15 % openings) = 2,040 SF');
 // untouched: the record's own quantity (half up, measure_drafts._q); an edit: half even (_with_factor)
 assert.equal(MD.factorLine({factor:'0.1',factor_reason:'x',quantity:'0.03'},'0.25','SF',{untouched:true}).row,'0.03');
 assert.equal(MD.factorLine({factor:'0.1',factor_reason:'x',quantity:'0.03'},'0.25','SF').row,'0.02');
 assert.equal(MD.times('0.35','0.1'),'0.04');assert.equal(MD.times('0.25','0.1','up'),'0.03');assert.equal(MD.times('2212.63','1.1180','up'),'2473.72');
 // no factor (the gutters), a factor of 1, or no shape: no line
 assert.equal(MD.factorLine({factor:null},'127.3','LF'),null);assert.equal(MD.factorLine({factor:'1'},'10','SF'),null);assert.equal(MD.factorLine(ROOF,null,'SF'),null);
});

test('2 and 6. the held rows: only pending drafts that block, highest exposure first; why each holds or not',()=>{
 const items=[asset('a','gutter',{...ROOF,exposure_usd:'103.47',blocks:false}),asset('b','countertop',{...COUNTER,exposure_usd:'1208.45',blocks:true}),asset('c','roof',ROOF),
  asset('d','hardwood_floor',{...COUNTER,exposure_usd:'1556.02',blocks:true}),asset('e','paint',{...COUNTER,exposure_usd:'9000',blocks:true,verified:true}),asset('f','carpet',{...COUNTER,exposure_usd:'8000',blocks:true,replaced_by:'engineer',engineer_quantity:'40.00 SF'}),asset('g','sink',null)];
 assert.deepEqual(MD.held(items).map(a=>a.id),['c','d','b'],'roof $1,950.08, floor $1,556.02, counter $1,208.45; the gutter is below; verified and replaced rows hold nothing');
 assert.equal(MD.exposureText(ROOF),'Could move about $1,950.08 (its expected error times its price), more than $250: it holds the report until you verify it.');
 assert.equal(MD.exposureText(COUNTER),'Could move about $229.67 (its expected error times its price), not more than $250: it does not hold the report.');
 assert.equal(MD.exposureText({...COUNTER,exposure_usd:null,blocks:false}),'Not priced, so it moves no dollars and does not hold the report.');
 assert.equal(MD.exposureText({...COUNTER,verified:true}),null,'a verified draft moves nothing');
 const html=MD.heldHtml(items,{open:true,route:id=>`#asset-detail/s/${id}`,blocker:{code:'measure:draft_unverified',message:'3 measurements drafted by Basis are not verified and could each move more than $250.00 (its expected error times its price)',assets:3}});
 assert.match(text(html),/The report waits for these 3 measurements 3 measurements drafted by Basis are not verified and could each move more than \$250\.00 \(its expected error times its price\)\./);
 assert.deepEqual([...html.matchAll(/data-live="(draft-verify|draft-use)" data-value="(\w)"/g)].map(m=>m[1]+':'+m[2]),['draft-verify:c','draft-use:d','draft-use:b'],'Verify in Measure for the roof; Use this number for the room drafts');
 assert.match(html,/\$1,950\.08<\/strong><small>could move/);
 assert.doesNotMatch(MD.heldHtml(items,{open:false}),/data-live=/,'a closed review: links, no actions');
 assert.equal(MD.heldHtml([asset('a','gutter',{...ROOF,blocks:false})]),'','nothing held, no blocker: nothing shown');
});

test('3. an interior draft: its line, the model’s answer and notes, and Use this number’s correction exactly',()=>{
 assert.equal(MD.interiorLine(COUNTER),'Drafted by Basis from 3 photos of the room · not verified · expected error about 20.8 %');
 assert.equal(MD.interiorLine({...COUNTER,photos:['x']}),'Drafted by Basis from 1 photo of the room · not verified · expected error about 20.8 %');
 assert.equal(MD.interiorLine({...COUNTER,photos:undefined}),'Drafted by Basis from 3 photos of the room · not verified · expected error about 20.8 %','the count from source when photos are absent');
 assert.deepEqual(MD.answerFacts(COUNTER),[['Length','17.5 ft'],['Width','13.4 ft'],['Ceiling','8 ft'],['Floor','235 SF'],['Countertop','42 SF']]);
 const a=asset('a1','countertop',COUNTER);
 assert.deepEqual(MD.useNumberCorrection(a,null,{title:'Countertop'}),{key:'a1',entity_type:'asset',entity_id:'a1',before:{quantity:'42.00',quantity_unit:'SF'},
  after:{quantity:42,quantity_unit:'SF',quantity_source:'verified Basis draft from the room photos'},reason:null,title:'Countertop',summary:'used Basis’s draft, 42 SF, as verified'});
 // merged into a pending correction: its classification and reason stay, a measurement goes (one way to state a quantity)
 const cur={after:{category:'Stone countertops',measurement:{space:'image'}},before:{category:'countertop'},reason:'Stone, not laminate'};
 const merged=MD.useNumberCorrection(a,cur,{title:'Countertop'});
 assert.deepEqual(merged.after,{category:'Stone countertops',quantity:42,quantity_unit:'SF',quantity_source:'verified Basis draft from the room photos'});
 assert.deepEqual(merged.before,{category:'countertop',quantity:'42.00',quantity_unit:'SF'});assert.equal(merged.reason,'Stone, not laminate');
 assert.equal(MD.usingNumber(a,merged),true);assert.equal(MD.usingNumber(a,cur),false);
 const card=MD.cardHtml(a,{open:true});
 for(const s of ['Drafted by Basis: 42 SF','Drafted by Basis from 3 photos of the room · not verified · expected error about 20.8 %','Length 17.5 ft','Basis’s notes The island is half hidden.','Use this number'])assert.ok(text(card).includes(s),s);
 assert.match(text(MD.cardHtml(a,{open:true,cur:merged})),/42 SF is in the corrections as verified, waiting for Review & send\./);
 assert.doesNotMatch(card,/claude|sonnet|lidar-shell/i,'drawn_by is never shown');
});

test('4. the was line from replaces, and after verification who acted with the model’s number kept',()=>{
 assert.equal(MD.wasLine(ROOF),'Was 3,900 SF (footprint x pitch).');
 assert.equal(MD.wasLine(COUNTER),'Was: no quantity, so the row was unpriced (the cabinet run was missing).');
 assert.equal(MD.resolvedLine(COUNTER),null,'pending: nobody acted');
 assert.equal(MD.resolvedLine({...ROOF,verified:true}),'Basis drafted 4,216.89 SF; engineer verified.');
 assert.equal(MD.resolvedLine({...COUNTER,replaced_by:'engineer',engineer_quantity:'42.00 SF'}),'Basis drafted 42 SF; engineer verified.','Use this number: the same number typed');
 assert.equal(MD.resolvedLine({...COUNTER,replaced_by:'engineer',engineer_quantity:'40.00 SF'}),'Basis drafted 42 SF; engineer changed it to 40 SF.');
 const card=text(MD.cardHtml(asset('c','countertop',{...COUNTER,replaced_by:'engineer',engineer_quantity:'40.00 SF'}),{open:true}));
 assert.match(card,/^Measured: 40 SF/);assert.match(card,/Basis drafted 42 SF; engineer changed it to 40 SF\./);assert.doesNotMatch(card,/Use this number|Could move/);
});

test('5. a row not drafted says why and the fix',()=>{
 assert.match(MD.notUsedHint('the pin is not confirmed (nothing is measured before it)').fix,/^Confirm the pin on the Property page/);
 assert.match(MD.notUsedHint('the study has no pin on the building').fix,/^Put the pin on the house/);
 for(const r of ['the newest shell was gathered around an earlier pin','no public records gather stored a building shell (GEO_SHELL)','the shell could not be read (BASIS-GEO-012)'])assert.match(MD.notUsedHint(r).fix,/^Gather the public records again/,r);
 assert.equal(MD.notUsedHint('no public records gather stored a building shell (GEO_SHELL)').why,'Basis did not draft it: no public records gather stored a building shell.');
 const other=MD.notUsedHint('no walls drafted (BASIS-GEO-020)');assert.equal(other.why,'Basis could not draft it from the lidar: no walls drafted (BASIS-GEO-020).');assert.equal(other.fix,'Measure it yourself with Measure.');assert.equal(other.to,null);
 assert.equal(MD.notUsedHint(null),null);
 const card=MD.cardHtml({id:'s',label:'siding',takeoff:{inputs:{draft_not_used:'the pin is not confirmed (nothing is measured before it)',draft_not_used_code:'BASIS-MEASURE-004'}}},{open:true,propertyHref:'#property/s1'});
 assert.match(text(card),/Not drafted Basis did not draft it: the pin is not confirmed\. Confirm the pin on the Property page/);assert.match(card,/href="#property\/s1">Property page</);
});

/* ---------- on the sample API (&drafts=on), through the real API client ---------- */
async function boot({variant='confirmed',screen='assets',facade=null}={}){
 const sample=A.create({state:'assets',now:()=>NOW,delayMs:0,measureDrafts:true,facade,fetchAsset:p=>fs.readFileSync(path.join(WEB,p))});sample._inst.propertyState=variant;
 const api=require('../live/api-client.js').createClient({baseUrl:sample.base,fetchImpl:(u,i)=>sample.fetch(u,i),getToken:async()=>'sample-token'});
 const study=await api.getStudy(sample.mainStudyId());
 const cache=new Map(),added=new Map(),toasts=[],listeners={};let scr=screen;
 const load=(key,fn)=>{if(cache.has(key))return cache.get(key);const e={done:false};cache.set(key,e);e.promise=Promise.resolve().then(fn).then(d=>{e.data=d;e.done=true},err=>{e.error=err;e.done=true});return e};
 const Live={api,caps:PM.caps({property_capture:true,public_geo:true,traced_measurement:true,facade_measure:true}),state:{cache,studyId:study.id,study,docId:null,corrections:{}},screens:{},actions:{},afterRender:[],beforeRender:[],load,
  invalidate:p=>{for(const k of [...cache.keys()])if(k.startsWith(p))cache.delete(k)},withStudy:fn=>fn(study),me:()=>({memberships:[{org_id:study.org_id,role:'owner'}]}),steps:()=>'',decisionBar:()=>'',ensureAssetsDraft(){},resultScreen:()=>'',
  route:(s,id,doc)=>`#${s}${id?'/'+id:''}${doc?'/'+doc:''}`,when:v=>v?String(v).slice(0,16):'—',loading:t=>`<div class="live-state">${t}</div>`,failed:e=>`<div role="alert">${e?.message}</div>`,
  render(){},screen:()=>scr,labelName:l=>String(l||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase()),gateOpen:stage=>study.current_stage===stage&&study.status==='waiting_review',
  guarded:async(n,fn)=>fn(),errorText:e=>e?.message||'',addCorrection:(stage,c)=>added.set(c.key,{stage,c}),pendingCorrection:(stage,key)=>added.get(key)?.c||null,
  photo:(id,alt,cls)=>`<img data-live-doc="${id}" class="${cls}">`,photoUrl:()=>new Promise(()=>{}),studyTools:()=>''};
 const g={BASIS_CONFIG:{mode:'live'},BasisLive:Live,BasisPropertyModel:PM,BasisReasons:require('../live/review-reasons.js'),BasisReviewOrganization:require('../review-organization.js'),BasisResultView:require('../live/result-view.js'),BasisReport:require('../live/report.js'),BasisGates:require('../live/gates.js'),
  Intl,URL,Date,Promise,Map,Set,JSON,Math,Number,String,Array,Object,Boolean,RegExp,Error,BigInt,encodeURIComponent,decodeURIComponent,console,
  setTimeout:()=>0,clearTimeout(){},matchMedia:()=>({matches:false}),icon:()=>'<svg></svg>',heading:(t,d,a)=>`<h1>${t}</h1>${a||''}`,toast:t=>toasts.push(t),
  esc:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),root:{addEventListener(){},querySelector:()=>null},state:{screen},assets(){},assetDetail(){},
  document:{addEventListener:(n,fn)=>(listeners[n]||=[]).push(fn),querySelector:()=>null,querySelectorAll:()=>[],getElementById:()=>null,hidden:false}};
 g.globalThis=g;vm.createContext(g);
 for(const f of ['live/new-design.js','live/property.js','live/measure-drafts.js'])vm.runInContext(read(f),g);
 async function page(fn){for(let i=0;i<10;i++){fn();const open=[...cache.values()].filter(e=>!e.done);if(!open.length)break;await Promise.all(open.map(e=>e.promise))}return fn()}
 const items=(await api.assets(study.id,study.revision)).items,find=(l,room)=>items.find(a=>a.label===l&&(!room||a.room?.name===room));
 const fire=(name,ev)=>{for(const fn of listeners[name]||[])fn(ev)};
 return {Live,g,api,sample,study,cache,added,toasts,page,items,find,fire,setScreen:s=>{scr=s}};
}

test('the sample (drafts on, pin confirmed) follows the API: records, exposure, the blocker; drafts off is unchanged',async()=>{
 const x=await boot(),roof=x.find('roof'),gutter=x.find('gutter'),siding=x.find('siding'),counter=x.find('countertop'),floor=x.find('hardwood_floor','Kitchen');
 const d=MD.draftOf(roof);
 assert.equal(roof.takeoff.inputs.quantity_source,'draft');assert.equal(roof.takeoff.inputs.measurement.drawn_by,'model:lidar-shell@v1');assert.equal(roof.takeoff.inputs.measurement.verified,false);
 assert.equal(d.factor,'1.1180');assert.equal(d.quantity,MD.times(PM.measure(PS.shellDrafts().roof.body).quantity,'1.1180','up'));assert.equal(roof.takeoff.quantity,d.quantity);
 assert.deepEqual([d.blocks,MD.draftOf(gutter).blocks,MD.draftOf(counter).blocks,MD.draftOf(floor).blocks],[true,false,true,true]);
 assert.equal(d.verify_above_usd,'250.0');assert.equal(Number(d.exposure_usd),Math.round(Number(roof.cost.total)*10.4)/100,'exposure = expected error x priced total');
 assert.equal(siding.takeoff.inputs.draft_not_used,'no walls drafted (BASIS-GEO-020)');assert.equal(siding.takeoff.inputs.draft_not_used_code,'BASIS-MEASURE-004');
 assert.ok(roof.review_reasons.includes('measure:DRAFT_UNVERIFIED'));assert.ok(roof.review_codes.includes('BASIS-MEASURE-001'));
 assert.deepEqual(MD.draftOf(counter).replaces,{missing_input:'cabinet_run'});assert.equal(MD.draftOf(counter).photos.length,2);
 const r=await x.api.result(x.study.id,x.study.revision);
 assert.deepEqual(r.blockers,[{code:'measure:draft_unverified',message:'3 measurements drafted by Basis are not verified and could each move more than $250.00 (its expected error times its price)',assets:3,fix:'verify or edit this measurement in the measure tool'}]);
 assert.equal(r.allocation,null);assert.ok(r.missing.includes('blockers'));
 // the pin not confirmed: every shell row says why; the room drafts are there
 const ready=await boot({variant:'ready'});
 for(const l of ['roof','siding','gutter'])assert.equal(ready.find(l).takeoff.inputs.draft_not_used,'the pin is not confirmed (nothing is measured before it)');
 // drafts off (the default): no roof or gutter rows, no draft anywhere, no blocker
 const off=A.create({state:'assets',now:()=>NOW}),api=require('../live/api-client.js').createClient({baseUrl:off.base,fetchImpl:(u,i)=>off.fetch(u,i),getToken:async()=>'t'}),s=await api.getStudy(off.mainStudyId());
 const items=(await api.assets(s.id,s.revision)).items;assert.ok(!items.some(a=>a.takeoff?.inputs?.draft||a.takeoff?.inputs?.draft_not_used||a.label==='roof'));assert.deepEqual((await api.result(s.id,s.revision)).blockers,[]);
});

/* ---------- the real API's rows (tests/fixtures/measure-drafts-api.json) ---------- */
// What GET .../assets and GET .../result serve after process_assets ran with both draft settings on
// (backEnd/tests/unit/activities/test_measure_drafts_api_reference.py writes it from the API's own
// serializers on Postgres; ids and times as placeholders). The app's code runs on those rows, and
// the sample's drafted rows carry the same fields, so the sample cannot drift from the API unseen.
const REAL=require('./fixtures/measure-drafts-api.json');
const DRAFT_INPUTS=['quantity_source','draft','measurement','shape_quantity','draft_factor','draft_not_used','draft_not_used_code','replaced_quantity','replaced_formula','replaced_missing_input'];
const kindOf=a=>{const d=MD.draftOf(a);return d?(MD.interior(d)?'interior':'exterior'):a.takeoff?.inputs?.draft_not_used?'not drafted':null};
const fieldsBy=(rows,pick)=>{const m={};for(const a of rows){const k=kindOf(a);if(k)for(const f of Object.keys(pick(a)||{}))(m[k]||=new Set()).add(f)}return Object.fromEntries(Object.entries(m).map(([k,v])=>[k,[...v].sort()]))};

test('the real API’s drafted rows: held by exposure as the blocker counts, the factor gives the row, the texts read whole',()=>{
 const items=REAL.drafted.items,blocker=REAL.drafted.blockers.find(b=>b.code===MD.BLOCKER);
 assert.deepEqual(MD.held(items).map(a=>a.label),['roof','countertop','wall_finish'],'$539.66, $117.59, $80.71; the unpriced siding, gutter and flooring hold nothing');
 assert.equal(MD.held(items).length,blocker.assets);
 // the factor line's row is the row the server priced, for every exterior draft with a factor
 const factored=items.filter(a=>kindOf(a)==='exterior'&&MD.draftOf(a).factor);
 assert.deepEqual(factored.map(a=>a.label),['roof','siding']);
 for(const a of factored){const d=MD.draftOf(a),[shape]=a.takeoff.inputs.shape_quantity.split(' ');
  const line=MD.factorLine(d,shape,d.unit,{kind:/slope/.test(d.factor_reason)?'area':'line',untouched:true});
  assert.equal(Number(line.row),Number(a.takeoff.quantity),a.label)}
 assert.equal(MD.factorLine(MD.draftOf(factored[0]),'2011.23','SF',{kind:'area',untouched:true}).text,"2,011.23 SF in plan × 1.1518 (the roof planes' slope, 29.5 degrees on average) = 2,316.53 SF");
 // Use this number sends the draft's own quantity and unit
 const counter=items.find(a=>a.label==='countertop');
 assert.deepEqual(MD.useNumberCorrection(counter,null).after,{quantity:28,quantity_unit:'SF',quantity_source:MD.INTERIOR_SOURCE});
 assert.equal(MD.wasLine(MD.draftOf(counter)),'Was: no quantity, so the row was unpriced (the cabinet run was missing).');
 // every card and the held list read whole: no undefined, NaN, null, an empty figure or a model id
 for(const a of [...items,...REAL.not_drafted.items]){const t=text(MD.cardHtml(a,{open:true,propertyHref:'#property/s'}));
  assert.ok(t.length>20,a.label);assert.doesNotMatch(t,/undefined|NaN|null|\[object|model:|claude|lidar-shell|—/,`${a.label}: ${t}`)}
 assert.doesNotMatch(text(MD.heldHtml(items,{open:true,blocker})),/undefined|NaN|null|model:/);
 for(const a of REAL.not_drafted.items)assert.equal(MD.notUsedHint(a.takeoff.inputs.draft_not_used).to,'property',a.label);
});

test('the sample’s drafted rows carry the real API’s fields: the record, the asset, the takeoff, the draft inputs, the blocker',async()=>{
 const x=await boot(),real=[...REAL.drafted.items,...REAL.not_drafted.items];
 const pickInputs=a=>Object.fromEntries(Object.entries(a.takeoff.inputs).filter(([k])=>DRAFT_INPUTS.includes(k)));
 for(const [what,pick] of [['asset',a=>a],['takeoff',a=>a.takeoff],['draft inputs',pickInputs],['draft record',a=>MD.draftOf(a)]])
  assert.deepEqual(fieldsBy(x.items,pick),fieldsBy(real,pick),what);
 const sample=(await x.api.result(x.study.id,x.study.revision)).blockers.find(b=>b.code===MD.BLOCKER);
 assert.deepEqual(Object.keys(sample).sort(),Object.keys(REAL.drafted.blockers.find(b=>b.code===MD.BLOCKER)).sort());
});

test('the Assets page lists the held rows by exposure and tags each draft; Results shows the blocker with them',async()=>{
 const x=await boot();
 x.cache.set(`assets:${x.study.id}:${x.study.revision}`,{done:true,data:{items:x.items}});
 const html=x.g.assets(),ids=[...html.matchAll(/data-live="(?:draft-verify|draft-use)" data-value="([^"]+)"/g)].map(m=>m[1]);
 const want=MD.held(x.items).map(a=>a.id);assert.equal(want.length,3);assert.deepEqual(ids,want);
 assert.deepEqual(want.map(id=>x.items.find(a=>a.id===id).label),['roof','hardwood_floor','countertop'],'roof $1,950, floor $1,556, counter $1,208');
 const row=l=>html.split('<tr').find(r=>new RegExp(`>${l}</a>`).test(r))||'';
 assert.match(row('Gutter'),/nd-tag-draft" title="Drafted by Basis\. Could move about \$103\.47 \(its expected error times its price\), not more than \$250: it does not hold the report\."/);
 assert.match(row('Countertop'),/nd-tag-draft is-held" title="Drafted by Basis from the room’s photos\. Could move about \$1,208\.45/);
 const res=await x.page(()=>x.Live.resultScreen());
 assert.match(text(res),/The report waits for these 3 measurements 3 measurements drafted by Basis are not verified/);
 assert.deepEqual([...res.matchAll(/data-live="(?:draft-verify|draft-use)" data-value="([^"]+)"/g)].map(m=>m[1]),want);
});

test('the asset page’s card: an interior draft with Use this number, the roof’s factor and was line, the siding’s hint',async()=>{
 const x=await boot(),counter=x.find('countertop'),roof=x.find('roof');
 x.cache.set(`assets:${x.study.id}:${x.study.revision}`,{done:true,data:{items:x.items}});
 const card=text(x.Live.draftCard(x.study,counter));
 assert.match(card,/^Drafted by Basis: 42 SF Holds the report Drafted by Basis from 2 photos of the room · not verified · expected error about 20\.8 %/);
 assert.match(card,/Countertop 42 SF/);assert.match(card,/Basis’s notes Scaled from the 36 in counter height/);assert.match(card,/Was: no quantity, so the row was unpriced \(the cabinet run was missing\)\./);
 assert.match(card,/Could move about \$1,208\.45 \(its expected error times its price\), more than \$250: it holds the report until you verify it\./);
 assert.match(x.Live.draftCard(x.study,counter),/data-live="draft-use" data-value="[^"]+">Use this number</);
 x.Live.actions['draft-use']({},counter.id);
 assert.deepEqual(JSON.parse(JSON.stringify(x.added.get(counter.id).c.after)),{quantity:42,quantity_unit:'SF',quantity_source:'verified Basis draft from the room photos'});
 assert.match(text(x.Live.draftCard(x.study,counter)),/42 SF is in the corrections as verified/);
 const rc=text(x.Live.draftCard(x.study,roof)),d=MD.draftOf(roof),shape=roof.takeoff.inputs.shape_quantity.split(' ')[0];
 assert.ok(rc.includes(`${Number(shape).toLocaleString('en-US',{maximumFractionDigits:2})} SF in plan × 1.1180 (the roof planes' slope, 26.6 degrees on average) = ${Number(d.quantity).toLocaleString('en-US',{maximumFractionDigits:2})} SF`),rc);
 assert.match(rc,/Was 2,610 SF \(footprint 2,210 SF x 1\.18 pitch factor \(6\/12 assumed\)\)\./);assert.match(rc,/Open it with Measure, check the outline/);
 assert.match(text(x.Live.draftCard(x.study,x.find('siding'))),/Basis could not draft it from the lidar: no walls drafted \(BASIS-GEO-020\)\. Measure it yourself with Measure\./);
 assert.equal(x.Live.draftCard(x.study,x.find('sink')),'','a row Basis did not draft and never tried to: nothing');
});

test('verifying: the measure tool shows the factor, its verified save and Use this number clear the blocker after the rerun',async()=>{
 const x=await boot(),roof=x.find('roof'),counter=x.find('countertop'),floor=x.find('hardwood_floor','Kitchen');
 x.cache.set(`assets:${x.study.id}:${x.study.revision}`,{done:true,data:{items:x.items}});
 x.Live.actions['draft-verify']({},roof.id);assert.equal(x.Live.state.mtDialog.assetId,roof.id,'Verify opens the measure tool on the row');
 let dlg=await x.page(()=>x.Live.measureDialog());
 const d=MD.draftOf(roof),shape=PM.measure(PM.bodyOfRecord(roof.takeoff.inputs.measurement)).quantity;
 assert.ok(text(dlg).includes(`${Number(shape).toLocaleString('en-US',{maximumFractionDigits:2})} SF in plan × 1.1180 (the roof planes' slope, 26.6 degrees on average) = ${Number(d.quantity).toLocaleString('en-US',{maximumFractionDigits:2})} SF on the row`),'the factor beside the total');
 x.fire('change',{target:{dataset:{mtInput:'verified',mtScope:'dialog'},checked:true,closest:sel=>/data-mt-scope|data-mt-root/.test(sel)?{dataset:{mtScope:'dialog'}}:null}});
 x.Live.actions['pa-trace-save']({closest:()=>({dataset:{mtScope:'dialog'}}),dataset:{mtScope:'dialog'}});
 const sent=x.added.get(roof.id).c.after.measurement;assert.equal(sent.drawn_by,'model:lidar-shell@v1');assert.equal(sent.verified,true,'the untouched draft sent back verified');
 x.Live.actions['draft-use']({},counter.id);x.Live.actions['draft-use']({},floor.id);
 const held=x.g.assets();assert.equal((held.match(/Verified · Review &amp; send/g)||[]).length,3,'each held row says it is queued');
 const corrections=[...x.added.values()].map(({c})=>({entity_type:'asset',entity_id:c.entity_id,after:c.after,reason:c.reason}));
 const decided=await x.api.request('POST',`/v1/studies/${x.study.id}/decisions`,{body:{stage:'assets_review',revision:x.study.revision,decision:'reject',corrections}});assert.equal(decided.corrections_recorded,3);
 const s2=await x.api.getStudy(x.study.id);assert.equal(s2.revision,x.study.revision+1);assert.equal(s2.current_stage,'assets_review');
 const after=(await x.api.assets(s2.id,s2.revision)).items,by=id=>after.find(a=>a.id===id);
 const r2=MD.draftOf(by(roof.id));assert.equal(r2.verified,true);assert.equal(r2.blocks,undefined);assert.equal(by(roof.id).takeoff.quantity,d.quantity,'verified at the same quantity: shape x factor');
 assert.deepEqual(r2.replaces,d.replaces,'what the draft replaced is still today’s formula, not the engineer’s measurement');
 assert.equal(MD.resolvedLine(r2),`Basis drafted ${Number(d.quantity).toLocaleString('en-US',{maximumFractionDigits:2})} SF; engineer verified.`);
 const c2=MD.draftOf(by(counter.id));assert.equal(c2.replaced_by,'engineer');assert.equal(c2.engineer_quantity,'42.00 SF');assert.equal(MD.resolvedLine(c2),'Basis drafted 42 SF; engineer verified.');
 assert.equal(by(counter.id).cost.total,counter.cost.total,'the typed 42 SF is priced as the draft was');
 const res=await x.api.result(s2.id,s2.revision);assert.deepEqual(res.blockers,[]);assert.ok(!res.missing.includes('blockers'));
 assert.equal(MD.heldHtml(after),'','nothing is held any more');
});

/* ---------- 24: the facade's corners with the keyboard ---------- */
async function facade(){
 const x=await boot({facade:'exif'}),sid=x.find('siding'),doc=x.study.documents.find(d=>d.filename==='west-wall.jpg');
 x.cache.set(`assets:${x.study.id}:${x.study.revision}`,{done:true,data:{items:x.items}});
 x.Live.measurePhotoDims.set(doc.id,[1600,1200]);x.Live.openMeasure(sid,'assets');await x.page(()=>x.Live.measureDialog());
 const svg={dataset:{mtScope:'facade',paStage:'doc:'+doc.id},getBoundingClientRect:()=>({left:0,top:0,width:1600,height:1200}),viewBox:{baseVal:{width:1600,height:1200}},setPointerCapture(){},releasePointerCapture(){},closest(){return this}};
 const P=PS.FACADE.projection(),F=()=>x.Live.facadeScope().F;
 const target=fc=>({dataset:{},closest:sel=>/data-fc-vertex/.test(sel)&&fc?{dataset:{fcVertex:fc}}:/svg\[/.test(sel)?svg:null});
 const click=p=>x.fire('click',{target:target(null),clientX:p[0],clientY:p[1],pointerId:1,preventDefault(){},stopPropagation(){}});
 const key=(k,{fc=null,shift=false,mod=false}={})=>x.fire('keydown',{key:k,shiftKey:shift,metaKey:mod,ctrlKey:false,target:target(fc),preventDefault(){},stopPropagation(){}});
 for(const p of P.wall)click(p);x.Live.actions['fc-side']({},'west');for(const p of P.openings[0].quad)click(p);for(const p of P.openings[1].quad)click(p);
 const net=()=>text(x.Live.measureDialog()).match(/([\d,.]+) SF net siding/)?.[1]||null;
 return {...x,F,key,click,net,P};
}
const at=(F,ref)=>{const [w,a,b]=ref.split(':');return JSON.parse(JSON.stringify(w==='w'?F.wall[a]:F.openings[a].pts[b]))};// a plain array (the VM's are another realm's)

test('24. a facade corner: Enter selects it, arrows move it a pixel (Shift ten), the numbers follow, Undo and Redo',async()=>{
 const x=await facade(),F=x.F(),before=x.net(),start=[...at(F,'w:2')];assert.ok(before);
 const steps=F.hist.length;x.key('Enter',{fc:'w:2'});assert.equal(F.sel,'w:2');assert.equal(F.hist.length,steps,'selecting is not a step');assert.match(x.Live.measureDialog(),/class="mt-vertex is-selected" data-fc-vertex="w:2"/);assert.match(x.Live.measureDialog(),/data-live="fc-delete" >Delete corner/);
 x.key('ArrowUp');assert.deepEqual(at(F,'w:2'),[start[0],start[1]-1]);
 x.key('ArrowLeft',{shift:true});assert.deepEqual(at(F,'w:2'),[start[0]-10,start[1]-1]);
 assert.notEqual(x.net(),before,'the net siding is measured again');
 assert.equal(F.hist.length,steps+2,'one history step a press');const depth=F.hist.length;
 x.key('z',{mod:true});assert.deepEqual(at(F,'w:2'),[start[0],start[1]-1]);x.key('z',{mod:true});assert.deepEqual(at(F,'w:2'),start);assert.equal(x.net(),before);assert.equal(F.hist.length,depth-2);
 x.key('z',{mod:true,shift:true});assert.deepEqual(at(F,'w:2'),[start[0],start[1]-1],'Redo (Mod+Shift+Z)');x.key('y',{mod:true});assert.deepEqual(at(F,'w:2'),[start[0]-10,start[1]-1],'Redo (Mod+Y)');
 x.Live.actions['fc-undo']();x.Live.actions['fc-undo']();assert.deepEqual(at(F,'w:2'),start);x.Live.actions['fc-redo']();assert.deepEqual(at(F,'w:2'),[start[0],start[1]-1],'the Redo button');
 // a new change drops what was undone
 x.Live.actions['fc-undo']();x.key('Enter',{fc:'o:0:1'});x.key('ArrowDown');assert.equal(F.future.length,0);assert.match(x.Live.measureDialog(),/data-live="fc-redo" disabled/);
 // kept on the photo: a corner at the edge does not move past it, and that is no step
 F.wall[0]=[0,F.wall[0][1]];x.key('Enter',{fc:'w:0'});const n=F.hist.length;x.key('ArrowLeft');assert.equal(F.wall[0][0],0);assert.equal(F.hist.length,n);
 // the keys do nothing to the facade without a selection, and Escape drops the selection first
 x.key('Escape');assert.equal(F.sel,null);const w=JSON.stringify(F.wall);x.key('ArrowRight');assert.equal(JSON.stringify(F.wall),w);
 // the hidden trace is left alone
 assert.equal(x.Live.state.mtDialog.trace,null);
});

test('24. Delete takes a finished wall or opening back to three corners; one click finishes it in its place; Undo restores it',async()=>{
 const x=await facade(),F=x.F(),before=x.net(),ops=JSON.stringify(F.openings);
 // an opening's corner: the opening goes back to its three others, still the first opening once finished
 const gone=[...at(F,'o:0:1')];x.key('Enter',{fc:'o:0:1'});x.key('Delete');
 assert.equal(F.openings.length,1);assert.equal(F.current.length,3);assert.equal(F.tool,'window');assert.equal(F.sel,null);
 assert.match(text(x.Live.measureDialog()),/3 of 4 clicked\./);
 x.click(gone);assert.equal(F.openings.length,2);assert.deepEqual(JSON.parse(JSON.stringify(F.openings[0].pts)).sort(),JSON.parse(ops)[0].pts.sort(),'back in its place');assert.equal(x.net(),before);
 // the wall's corner: the wall goes back to three corners; its openings stay
 const corner=[...at(F,'w:3')];x.key('Enter',{fc:'w:3'});x.key('Backspace');
 assert.equal(F.wall.length,0);assert.equal(F.current.length,3);assert.equal(F.tool,'wall');assert.equal(F.openings.length,2);
 assert.match(text(x.Live.measureDialog()),/Click the wall’s four corners to start/);assert.match(x.Live.measureDialog(),/data-live="fc-tool" data-value="window" aria-pressed="false" disabled/);
 x.key('z',{mod:true});assert.equal(F.wall.length,4);assert.equal(F.current.length,0);assert.equal(x.net(),before,'Undo puts the wall back whole');
 x.key('y',{mod:true});assert.equal(F.wall.length,0);x.click(corner);assert.equal(F.wall.length,4);assert.equal(F.tool,'window');assert.equal(x.net(),before);
 // a corner being clicked is simply dropped; a finished one waits until the clicking is done
 x.click([700,500]);x.click([760,500]);assert.equal(F.current.length,2);
 x.key('Enter',{fc:'c:1'});x.key('Delete');assert.equal(F.current.length,1);
 x.key('Enter',{fc:'w:0'});x.key('Delete');assert.equal(F.wall.length,4,'refused while a window is half clicked');assert.match(F.msg,/^Finish the window you are clicking first, or press Escape to drop its corners\./);
 x.key('Escape');x.key('Escape');assert.equal(F.current.length,0,'Escape: the selection, then the corners being clicked');
 x.key('Escape');assert.equal(x.Live.state.mtDialog,null,'then the dialog');
});

test('24. the facade’s handles and hint say how to use the keys; Trace mode leaves the facade’s keys',async()=>{
 const x=await facade(),html=x.Live.measureDialog();
 assert.match(html,/aria-label="Wall corner 1: drag it onto the corner; Enter selects it, then the arrow keys move it and Delete removes it"/);
 assert.match(text(html),/Drag a corner to move it, or select it and use the arrow keys \(Shift for larger steps\); Delete removes it\./);
 // the same dialog in Trace mode: the facade's keys stand aside (the trace's keys are covered by measure-screen.test.cjs)
 x.Live.actions['mt-mode']({},'trace');x.Live.measureDialog();
 assert.equal(x.Live.state.mtDialog.facadeShown,false);
});
