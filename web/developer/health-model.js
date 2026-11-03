/* Health is evidence, not configuration. Missing, old or malformed checks never go green. */
(function(scope){
 'use strict';
 const GROUPS=['Infrastructure','AI providers','Processing & reference data'];
 const SERVICES=[
  ['api','Basis API','Infrastructure','FastAPI · requests and review decisions'],
  ['db','PostgreSQL','Infrastructure','Study records, revisions, pgvector and audit'],
  ['temporal','Temporal','Infrastructure','Durable workflows and review checkpoints'],
  ['worker','Study worker','Infrastructure','Activity polling and model execution'],
  ['relay','Decision relay','Infrastructure','Delivers saved decisions to Temporal'],
  ['storage','File storage','Infrastructure','Local storage or Google Cloud Storage'],
  ['firebase','Firebase Auth','Infrastructure','Sign-in and token verification'],
  ['openai','OpenAI / GPT','AI providers','Appraisal, photo analysis and room reconciliation'],
  ['azure_openai','Azure OpenAI','AI providers','Alternative GPT deployments'],
  ['anthropic','Anthropic / Claude','AI providers','Count fusion, materials and asset classification'],
  ['replicate','Replicate','AI providers','Hosted segmentation when selected'],
  ['azure_di','Azure Document Intelligence','Processing & reference data','Appraisal text, tables and page geometry'],
  ['appraisal','Appraisal extraction','Processing & reference data','PDF / UAD 3.6 XML + validation + grounded model'],
  ['vision','Photo analysis','Processing & reference data','Countable objects, surfaces and room identity'],
  ['sam3','SAM 3','Processing & reference data','Meta · object outlines · self-hosted or Replicate'],
  ['mask2former','Mask2Former','Processing & reference data','Meta · optional surface crops and material second look'],
  ['room_inventory','Room inventory','Processing & reference data','Reconcile photos, counts and materials'],
  ['embeddings','Embedding model','Processing & reference data','Sentence Transformers · evidence retrieval'],
  ['retrieval','Evidence search','Processing & reference data','Reference corpus · BM25 / vector search'],
  ['classification','Asset classification','Processing & reference data','IRS rules, firm rulings and model fallback'],
  ['takeoff','Quantity takeoff','Processing & reference data','Appraisal areas, inventory and study inputs'],
  ['structure','Concealed systems','Processing & reference data','Structure model and building assumptions'],
  ['rsmeans','RSMeans cost table','Processing & reference data','Licensed unit-cost rows and source citations'],
  ['cost_indices','Cost indices','Processing & reference data','Year and location factors with citations'],
  ['exports','Result exports','Processing & reference data','Approved study reports, schedules and supporting files'],
 ].map(([id,name,group,description])=>({id,name,group,description}));
 // Runtime model names and provider routes must come from the feed, never these labels.
 const FLOW=[
  [{id:'upload',title:'Engineer uploads the files',description:'The engineer uploads the appraisal and the property photos, enters the property details and starts the study.',services:['api','firebase','storage','db','temporal','worker']}],
  [{id:'extract',title:'Basis reads the appraisal',description:'Reads every page, then extracts each field (living area, year built, price...) with the exact words it came from.',services:['appraisal','azure_di']},
   {id:'photos',title:'Basis analyzes the photos',description:'Counts the objects in each photo, reads its surfaces and room type, and outlines every object.',services:['vision','sam3']}],
  [{id:'appraisal-review',title:'Engineer reviews the appraisal',description:'Checks each value against its source on the page, corrects what is wrong, then approves.',review:true,services:['api','db','relay','temporal']},
   {id:'photo-results',title:'Basis groups photos into rooms',description:'Groups the photos into rooms by file name and names each room with its type and floor.',services:['vision']}],
  [{id:'rooms-review',title:'Engineer reviews the rooms',description:'Puts every photo in the right room, fixes names, types and floors, then approves.',review:true,services:['api','db','relay','temporal']}],
  [{id:'inventory',title:'Basis counts each room\'s objects',description:'Combines a room\'s photos so each object is counted once, and double-checks wall, wainscot and tile materials.',services:['room_inventory','sam3']},
   {id:'components-review',title:'Engineer reviews the components',description:'Checks each room\'s counts and materials against the outlined photos, corrects, then approves.',review:true,services:['api','db','relay','temporal']}],
  [{id:'assets',title:'Basis classifies and prices',description:'Gives each item its tax class, measures and prices it from RSMeans, and balances the total to the purchase price.',services:['classification','retrieval','embeddings','takeoff','structure','rsmeans','cost_indices']},
   {id:'assets-review',title:'Engineer reviews the assets',description:'Checks tax classes, quantities, prices and citations, fixes what is wrong, then approves.',review:true,services:['api','db','relay','temporal']}],
  [{id:'output',title:'Basis exports the study',description:'Saves the engineer\'s rulings for the firm and exports the approved study with its evidence and costs.',services:['exports','storage','db']}],
  [{id:'report-sign',title:'Engineer signs the report',description:'Fills in the report details, previews the PDF and signs it.',signing:true,services:['api','firebase','db','storage','exports']}],
 ];
 // Brand identity is static; check status and selected runtime adapters remain independent.
 const PHASES=['Intake','Parallel analysis','Prepare review','Room review','Components','Classify & cost','Results','Report & sign'];
 const EDGES=[['upload','extract'],['upload','photos'],['extract','appraisal-review'],['photos','photo-results'],['appraisal-review','rooms-review'],['photo-results','rooms-review'],['rooms-review','inventory'],['inventory','components-review'],['components-review','assets'],['assets','assets-review'],['assets-review','output'],['output','report-sign']];
 const BRANDS={basis:'Basis',python:'Python',huggingface:'Hugging Face',googlecloud:'Google Cloud',openai:'OpenAI',meta:'Meta',claude:'Anthropic / Claude',replicate:'Replicate',azure:'Microsoft Azure',postgresql:'PostgreSQL',temporal:'Temporal',firebase:'Firebase',fastapi:'FastAPI'};
 // Basis's own mark is the app's (web/assets); the providers' logos are in logos/.
 const LOGO_SRC={basis:'../assets/basis-mark.png',huggingface:'logos/huggingface.png',googlecloud:'logos/googlecloud.png',python:'logos/python.png'};
 const SERVICE_BRANDS={api:'fastapi',db:'postgresql',temporal:'temporal',firebase:'firebase',openai:'openai',azure_openai:'azure',anthropic:'claude',replicate:'replicate',azure_di:'azure',sam3:'meta',mask2former:'meta'};
 function logo(brand){return BRANDS[brand]?`<img class="provider-logo" src="${LOGO_SRC[brand]||`logos/${brand}.svg`}" width="24" height="24" alt="" aria-hidden="true" title="${esc(BRANDS[brand])}">`:''}
 const LABELS={healthy:'Healthy',degraded:'Degraded',down:'Failed',unknown:'Not checked',stale:'Stale',disabled:'Not enabled'};
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function stamp(value){return typeof value==='string'&&Number.isFinite(Date.parse(value))?Date.parse(value):NaN}
 function normalize(raw={},now=Date.now()){
  const checked=stamp(raw.checked_at),expiry=stamp(raw.expires_at);
  let status=Object.hasOwn(LABELS,raw.status)?raw.status:'unknown';
  const kind=['probe','heartbeat','model_run','configuration'].includes(raw.check_kind)?raw.check_kind:'unknown';
  if(status!=='unknown'&&(!Number.isFinite(checked)||checked>now+60000))status='unknown';
  // Configuration alone proves neither credentials nor execution readiness.
  if(status==='healthy'&&(kind==='configuration'||kind==='unknown'))status='unknown';
  if(status!=='unknown'&&Number.isFinite(checked)&&now>Math.min(checked+120000,Number.isFinite(expiry)?expiry:Infinity))status='stale';
  return {status,checked_at:Number.isFinite(checked)?raw.checked_at:null,expires_at:Number.isFinite(expiry)?raw.expires_at:null,latency_ms:typeof raw.latency_ms==='number'&&Number.isFinite(raw.latency_ms)&&raw.latency_ms>=0?raw.latency_ms:null,
   check_kind:kind,detail:typeof raw.detail==='string'?raw.detail:'No check has been reported.',model:typeof raw.model==='string'?raw.model:'',adapter:typeof raw.adapter==='string'?raw.adapter:'',hosting:typeof raw.hosting==='string'?raw.hosting:'',fallbacks:Array.isArray(raw.fallbacks)?raw.fallbacks.filter(v=>typeof v==='string'):null,models:modelRoutes(raw.models),last_inference:raw.last_inference&&typeof raw.last_inference.model==='string'&&Number.isFinite(stamp(raw.last_inference.at))?{model:raw.last_inference.model,at:raw.last_inference.at}:null,required:typeof raw.required==='boolean'?raw.required:null};
 }
 // The structured routes of GET /v1/ops/health (`models`): kept only when well formed, so a
 // malformed entry can neither name a model nor crash the dialog.
 function hop(value){return value&&typeof value.provider==='string'&&typeof value.service==='string'&&typeof value.target==='string'?{provider:value.provider,service:value.service,target:value.target}:null}
 function modelRoutes(list){
  if(!Array.isArray(list))return [];
  return list.filter(r=>r&&typeof r.setting==='string'&&typeof r.model==='string').map(r=>({setting:r.setting,model:r.model,primary:hop(r.primary),fallbacks:Array.isArray(r.fallbacks)?r.fallbacks.map(hop).filter(Boolean):[]}));
 }
 function basicChecks(result,now=Date.now()){
  const checked_at=new Date(now).toISOString(),data=result?.data;
  const output={api:{status:'unknown',checked_at,check_kind:'probe',detail:'The health response was not recognized.'}};
  if(result?.error){output.api.status='down';output.api.detail='API unreachable from this browser. Check the server, network and CORS.';return output}
  if(data?.status==='ok'&&data?.service==='basis'&&result.status===200)output.api={...output.api,status:'healthy',detail:'GET /health responded successfully.',latency_ms:result.latency_ms};
  else if(result?.status>=500)output.api={...output.api,status:'degraded',detail:`Health endpoint responded HTTP ${result.status}. Inspect the dependency checks.`,latency_ms:result.latency_ms};
  else if(result?.status)output.api.detail=`GET /health returned HTTP ${result.status}; readiness is unknown.`;
  for(const id of ['db','temporal'])if(['ok','fail'].includes(data?.checks?.[id]?.status))output[id]={status:data.checks[id].status==='ok'?'healthy':'down',checked_at,check_kind:'probe',latency_ms:data.checks[id].latency_ms,detail:id==='db'?'Database SELECT 1 check.':'Temporal namespace check. This does not verify worker polling.'};
  return output;
 }
 function snapshot(basic,extended,now=Date.now()){
  const checks=basicChecks(basic,now),items=extended?.schema_version===1&&Array.isArray(extended.services)?extended.services:[];
  const seen=new Set();
  for(const item of items){if(!item||typeof item.id!=='string'||seen.has(item.id))continue;seen.add(item.id);
   // Direct /health observations take precedence for its three checks.
   if(['api','db','temporal'].includes(item.id)&&checks[item.id])checks[item.id]={...item,...checks[item.id],expires_at:null};else checks[item.id]=item;
  }
  return SERVICES.map(service=>({...service,...normalize(checks[service.id],now)}));
 }
 function aggregate(services){
  const active=services.filter(s=>!(s.status==='disabled'&&s.required!==true));
  if(!active.length)return 'unknown';
  if(active.some(s=>s.status==='down'))return 'down';
  if(active.some(s=>s.status==='degraded'||(s.status==='disabled'&&s.required===true)))return 'degraded';
  if(active.some(s=>s.status==='stale'))return 'stale';
  if(active.some(s=>s.status!=='healthy'))return 'unknown';
  return 'healthy';
 }
 function statusIcon(status){
  const paths={healthy:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',down:'<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/>',degraded:'<path d="m12 3 10 18H2Z"/><path d="M12 9v5m0 3v.1"/>',stale:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',disabled:'<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',unknown:'<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4m0 3v.1"/>'};
  return `<svg class="status-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths[status]||paths.unknown}</svg>`;
 }
 function badge(status,label){return `<span class="status status-${esc(status)}">${statusIcon(status)}${esc(label||LABELS[status]||LABELS.unknown)}</span>`}
 function renderCounts(services){return ['down','degraded','stale','unknown','healthy','disabled'].map(status=>{const n=services.filter(s=>s.status===status).length;return `<span class="health-count health-count-${status}">${statusIcon(status)}<strong>${n}</strong><span>${LABELS[status]}</span></span>`}).join('')}
 function renderLegend(){return Object.keys(LABELS).map(status=>badge(status)).join('')+'<span class="review-marker">Engineer review</span>'}
 // Explicit edges retain the parallel split and merge. Port offsets keep join arrows distinct.
 function connectionPath(from,to,a,b){
  if(Math.abs(a.left-b.left)<2){const x=a.left+a.width/2;return `M ${x} ${a.bottom} V ${b.top-3}`}
  const y=a.top+a.height*.5;
  const ty=b.top+b.height*(to==='rooms-review'?(from==='photo-results'?.7:.4):.5);
  const x=a.right,tx=b.left-3,m=(x+tx)/2;
  return `M ${x} ${y} H ${m} V ${ty} H ${tx}`;
 }
 function time(value){return value?new Date(value).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'Never'}
 // Failed and degraded rows precede every normal category, without duplication.
 function serviceGroups(services,{query='',attention=false}={}){
  const priority={down:0,degraded:1,stale:2};
  const term=query.trim().toLowerCase();
  const visible=services.filter(s=>(!attention||['down','degraded','stale','unknown'].includes(s.status)||(s.status==='disabled'&&s.required))&&`${s.name} ${s.description} ${s.model} ${s.adapter} ${s.hosting}`.toLowerCase().includes(term));
  const urgent=s=>Object.hasOwn(priority,s.status)||(s.status==='disabled'&&s.required===true);
  return [{name:'Needs attention',urgent:true,rows:visible.filter(urgent).sort((a,b)=>(priority[a.status]??1)-(priority[b.status]??1))},...GROUPS.map(name=>({name,rows:visible.filter(s=>s.group===name&&!urgent(s))}))].filter(g=>g.rows.length);
 }
 function hostLabel(s){
  if(s.hosting)return s.hosting;
  // Only adapters explicitly reported by the API identify a provider. Temporal alone does not name a host.
  return ({replicate:'Replicate','OpenAI API':'OpenAI','Anthropic API':'Anthropic','Azure OpenAI':'Microsoft Azure',self_hosted:'Self-hosted','self-hosted':'Self-hosted',local:'Local process'})[s.adapter]||'Not reported';
 }
 function serviceLogos(s){
  const primary=SERVICE_BRANDS[s.id]||(/gpt-/i.test(s.model||'')?'openai':/claude-/i.test(s.model||'')?'claude':null);
  const host=({replicate:'replicate','OpenAI API':'openai','Anthropic API':'claude','Azure OpenAI':'azure'})[s.adapter];
  return `<span class="service-logos">${logo(primary)||'<span class="provider-logo-placeholder" aria-hidden="true"></span>'}${host&&host!==primary?logo(host):''}</span>`;
 }
 function renderService(s){
  return `<details class="service service-${esc(s.status)}" id="service-${esc(s.id)}"><summary><span class="service-identity">${serviceLogos(s)}<span class="service-name">${esc(s.name)}<small>${esc(s.model||s.description)}</small><small class="runtime-host">Hosting: ${esc(hostLabel(s))}</small></span></span>${badge(s.status)}<span class="latency">${s.latency_ms===null?'—':Math.round(s.latency_ms)+' ms'}</span><span class="checked">${esc(time(s.checked_at))}</span><span class="disclose" aria-hidden="true">⌄</span></summary><div class="service-detail"><p>${esc(s.detail)}</p><dl><div><dt>Evidence</dt><dd>${esc({probe:'Dependency probe',heartbeat:'Heartbeat',model_run:'Recorded model run',configuration:'Configuration only',unknown:'None received'}[s.check_kind])}</dd></div><div><dt>Model</dt><dd>${esc(s.model||'Not reported')}</dd></div><div><dt>Adapter</dt><dd>${esc(s.adapter||'Not reported')}</dd></div><div><dt>Hosting</dt><dd>${esc(hostLabel(s))}</dd></div><div><dt>Backups</dt><dd>${esc(s.fallbacks===null?'Not reported':s.fallbacks?.length?s.fallbacks.join(' → '):'None configured')}</dd></div><div><dt>Last checked</dt><dd>${esc(s.checked_at?new Date(s.checked_at).toLocaleString():'Never')}</dd></div>${s.last_inference?`<div><dt>Last successful model call</dt><dd>${esc(s.last_inference.model)} · ${esc(new Date(s.last_inference.at).toLocaleString())}</dd></div>`:''}</dl></div></details>`;
 }
 function renderServices(services,options={}){
  const groups=serviceGroups(services,options);
  if(!groups.length)return '<p class="empty">No systems match this filter.</p>';
  return groups.map(g=>`<section class="service-group ${g.urgent?'attention-group':''}" aria-label="${esc(g.name)}"><h3>${esc(g.name)}</h3><div class="service-list">${g.rows.map(renderService).join('')}</div></section>`).join('');
 }
 function systemChips(list){return `<div class="node-systems">${list.map(x=>`<span class="system-chip">${(x.brand&&logo(x.brand))||'<span class="uses-dot" aria-hidden="true"></span>'}<span>${esc(x.label)}</span></span>`).join('')}</div>`}
 // systems: {stepId: [{label, brand}]} from the content file (the systems each step runs); a
 // step without it falls back to the health services that drive its status.
 function renderFlow(services,systems){
  const byId=new Map(services.map(s=>[s.id,s]));
  return '<svg class="flow-wires" aria-hidden="true"><defs><marker id="flow-arrow" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="18" markerHeight="18" markerUnits="userSpaceOnUse" orient="auto"><path d="M 1 1 L 11 6 L 1 11 z" fill="#244e3d"/></marker></defs><g></g></svg>'+FLOW.map((column,index)=>`<div class="flow-column"><div class="flow-column-title"><span class="phase-number">${index+1}</span><div><strong>${PHASES[index]}</strong><span>Step ${index+1} of ${FLOW.length}</span></div></div>${index===1?'<div class="flow-branch-note">Runs in parallel</div>':''}${index===3?'<div class="flow-branch-note">Both paths join here</div>':''}${column.map(node=>{const deps=node.services.map(id=>byId.get(id)).filter(Boolean),state=aggregate(deps);return `<article data-node="${node.id}" class="flow-node ${node.review||node.signing?'review-node':''} node-${state}"><div class="node-heading"><div class="node-status">${badge(state)}${node.review||node.signing?`<span class="review-marker">${node.signing?'Engineer signature':'Engineer review'}</span>`:''}</div><h3><button type="button" class="node-open" data-step="${node.id}" aria-haspopup="dialog">${esc(node.title)}<span class="node-open-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m9 5 7 7-7 7"/></svg></span></button></h3></div><p>${esc(node.description)}</p>${systems?.[node.id]?.length?systemChips(systems[node.id]):`<div class="node-services">${deps.map(s=>`<button type="button" data-service="${s.id}" class="dependency dependency-${s.status}" aria-label="${esc(s.name+': '+LABELS[s.status]+'. Show health check')}">${serviceLogos(s)}<span>${esc(s.name)}${s.model?`<small>${esc(s.model)}</small>`:''}</span>${statusIcon(s.status)}</button>`).join('')}</div>`}</article>`}).join('')}</div>`).join('');
 }
 const api={PHASES,EDGES,connectionPath,statusIcon,renderCounts,renderLegend,GROUPS,SERVICES,FLOW,LABELS,esc,normalize,basicChecks,snapshot,aggregate,badge,time,serviceGroups,hostLabel,renderServices,renderFlow,serviceLogos,logo};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisHealth=api;
})(globalThis);
