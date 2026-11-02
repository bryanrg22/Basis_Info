/* Step dialogs of the Cost Segregation Workflow: what each step runs, on which systems, and
   what was measured. The text and the figures come from workflow-content.json (every
   figure verbatim from EVAL_REPORT.md, every source path real: both are checked by
   backEnd/tests/unit/test_developer_workflow_content.py). The runtime table comes only from
   the health feed (GET /v1/ops/health): a model id, a host or a backup is never typed here.
   The "Run records" block under Tests & results lists the evaluation registry's runs for the
   step's components (workflow-content.json maps each step to them) from GET /v1/ops/eval-runs,
   with the same unavailable, unauthorized and no-data states as the health feed; every figure
   there is what a run recorded, with its source, and nothing is compared across label files.
   This page is the one customer-adjacent surface that may name a provider. */
(function(scope){
 'use strict';
 const REPOSITORY='https://github.com/bryanrg22/Basis_Info/blob/main/';
 const CONTENT_PATH='workflow-content.json';
 const NOT_REPORTED='Not reported';
 const PROVIDER_BRAND={openai:'openai',azure_openai:'azure',anthropic:'claude',replicate:'replicate'};
 let content=null,contentError=null;
 function validContent(data){return !!(data&&data.schema_version===1&&data.steps&&typeof data.steps==='object'&&data.roles&&typeof data.roles==='object')}
 function setContent(data){if(!validContent(data)){content=null;contentError='The workflow content file does not match the supported shape.';return false}content=data;contentError=null;return true}
 async function load(fetchImpl=globalThis.fetch){
  try{const response=await fetchImpl(CONTENT_PATH,{cache:'no-store'});if(!response.ok){contentError=`The workflow content file answered HTTP ${response.status}.`;content=null;return false}return setContent(await response.json())}
  catch{contentError='The workflow content file could not be loaded.';content=null;return false}
 }
 function sectionLink(section,H){
  const entry=content?.sections?.[section];
  const label=`section ${section}`;
  if(!entry||!content?.source?.report)return H.esc(label);
  return `<a href="${H.esc(REPOSITORY+content.source.report+'#'+entry.anchor)}" target="_blank" rel="noopener noreferrer" title="${H.esc(entry.title)}">${H.esc(label)} ↗</a>`;
 }
 function hopLabel(hop,byId,H){
  if(!hop)return 'no provider';
  const provider=byId.get(hop.service);
  return `${H.esc(provider?.adapter||hop.provider)} · ${H.esc(hop.target)}`;
 }
 function backups(list,H){return list===null||list===undefined?NOT_REPORTED:list.length?list.map(H.esc).join(' → '):'None configured'}
 function serviceRow(s,H){
  return `<tr class="runtime-service"><th scope="row"><span class="runtime-system">${H.serviceLogos(s)}<span><span class="runtime-name">${H.esc(s.name)}</span>${H.badge(s.status)}</span></span></th><td>${H.esc(s.model||NOT_REPORTED)}</td><td>${H.esc(H.hostLabel(s))}</td><td>${H.esc(s.adapter||NOT_REPORTED)}</td><td>${backups(s.fallbacks,H)}</td></tr>`;
 }
 function routeRow(route,role,byId,H){
  const primary=route?.primary||null,provider=primary?byId.get(primary.service):null;
  const brand=primary?PROVIDER_BRAND[primary.service]:null;
  const logo=brand?H.logo(brand):'<span class="provider-logo-placeholder" aria-hidden="true"></span>';
  const title=content?.roles?.[role]||role;
  const model=!route?NOT_REPORTED:!primary?`${H.esc(route.model)} (no configured provider)`:primary.target&&primary.target!==route.model?`${H.esc(route.model)} → ${H.esc(primary.target)}`:H.esc(route.model);
  const hosting=provider?.hosting||(provider?H.hostLabel(provider):NOT_REPORTED);
  const adapter=provider?.adapter||NOT_REPORTED;
  const fallbacks=!route?null:route.fallbacks.map(h=>`${route.model} → ${hopLabel(h,byId,H).replace(/&[a-z#0-9]+;/g,m=>({'&amp;':'&','&lt;':'<','&gt;':'>','&quot;':'"','&#39;':"'"}[m]||m))}`);
  return `<tr class="runtime-route"><th scope="row"><span class="runtime-system">${logo}<span><span class="runtime-name">${H.esc(title)}</span><small><code>${H.esc(role)}</code></small></span></span></th><td>${model}</td><td>${H.esc(hosting)}</td><td>${H.esc(adapter)}</td><td>${backups(fallbacks,H)}</td></tr>`;
 }
 function runtimeRows(rows,services,H){
  const byId=new Map(services.map(s=>[s.id,s]));
  return rows.map(({service:id,roles})=>{
   const s=byId.get(id);
   if(!s)return '';
   let html=serviceRow(s,H);
   const routes=Array.isArray(s.models)?s.models:[];
   const wanted=Array.isArray(roles)&&roles.length?roles:routes.map(r=>r.setting);
   for(const role of wanted){
    const route=routes.find(r=>r.setting===role)||null;
    // A role the worker does not run under its effective settings (for example the room
    // call, off by default) is listed only when the feed reports it; the roles a step
    // always runs show "Not reported" until the feed is connected.
    if(!route&&routes.length)continue;
    html+=routeRow(route,role,byId,H);
   }
   return html;
  }).join('');
 }
 function comparisonTable(c,H,open){
  const rows=c.rows.map(r=>`<tr${r.selected?' class="selected-architecture"':''}><th scope="row">${r.selected?'<span class="selection-label">Selected</span>':''}${H.esc(r.name)}</th>${r.values.map(v=>`<td>${H.esc(v)}</td>`).join('')}</tr>`).join('');
  const meta=[c.dataset,c.date].filter(Boolean).map(H.esc).join(' · ');
  return `<details class="comparison"${open?' open':''}><summary><strong>${H.esc(c.title)}</strong> <span class="comparison-meta">${meta}${meta?' · ':''}${sectionLink(c.section,H)}</span></summary><div class="table-scroll" role="region" tabindex="0" aria-label="${H.esc(c.title)}"><table><thead><tr>${c.columns.map(h=>`<th>${H.esc(h)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>${c.winner?`<p class="comparison-why"><strong>Chosen:</strong> ${H.esc(c.winner)}.${c.why?' '+H.esc(c.why):''}</p>`:''}</details>`;
 }
 function metricsTable(metrics,H){
  if(!metrics?.length)return '';
  return `<div class="table-scroll" role="region" tabindex="0" aria-label="Measured results"><table class="metrics"><thead><tr><th>Measure</th><th>Value</th><th>Source</th></tr></thead><tbody>${metrics.map(m=>`<tr><th scope="row">${H.esc(m.label)}${m.note?`<small>${H.esc(m.note)}</small>`:''}</th><td class="metric-value">${H.esc(m.value)}</td><td>${sectionLink(m.section,H)}</td></tr>`).join('')}</tbody></table></div>`;
 }
 function spendLine(spend,H){
  if(!spend)return '';
  const parts=[];
  if(spend.cost_per_study)parts.push(`Cost ${H.esc(spend.cost_per_study)}${spend.cost_section?` (${sectionLink(spend.cost_section,H)})`:''}`);
  if(spend.input_tokens||spend.output_tokens)parts.push(`Tokens ${H.esc(spend.input_tokens||'Not measured')} in / ${H.esc(spend.output_tokens||'Not measured')} out${spend.tokens_section?` (${sectionLink(spend.tokens_section,H)})`:''}`);
  if(spend.latency)parts.push(`Latency ${H.esc(spend.latency)}${spend.latency_section?` (${sectionLink(spend.latency_section,H)})`:''}`);
  return parts.length?`<p class="spend-line">${parts.join(' · ')}</p>`:'<p class="spend-line">No paid call in this step; no cost or token figure applies.</p>';
 }
 // ---- Run records (GET /v1/ops/eval-runs) ----
 const NOT_RECORDED='Not recorded';
 const SELECTION_LABEL={current:'Current pipeline',alternative:'Alternative',not_in_pipeline:'Not in the pipeline',unknown:'Standing unknown'};
 function validRunsPage(data){return !!(data&&Array.isArray(data.items)&&typeof data.total==='number'&&data.items.every(r=>r&&typeof r.run_id==='string'&&typeof r.name==='string'))}
 // One sentence for a feed result that is not a page of runs; null when it is one.
 function runsMessage(result){
  if(result.error==='untrusted_origin')return 'Run records require a trusted API origin in deployment configuration.';
  if(result.error==='auth')return 'The sign-in service could not refresh your session. Reconnect diagnostics to read run records.';
  if(result.error==='timeout')return 'Run records did not answer in time. Retry when the API is reachable.';
  if(result.error)return 'Run records unavailable. Retry when the API is reachable.';
  if(result.status===401)return 'Sign in to Basis, then connect again. Run records require an authenticated session.';
  if(result.status===403)return 'This account cannot read run records. The backend must grant operator access.';
  if(result.status===404&&result.data?.code==='BASIS-API-046')return typeof result.data.detail==='string'&&result.data.detail?result.data.detail:'No evaluation data on this host.';
  if(result.status===404)return 'Run records endpoint unavailable.';
  if(result.status===429)return 'Run records are rate limited. Wait before opening another step.';
  if(result.status!==200)return `Run records returned HTTP ${result.status}.`;
  if(!validRunsPage(result.data))return 'The run records response does not match the supported contract.';
  return null;
 }
 const num=v=>typeof v==='number'&&Number.isFinite(v)?v:null;
 function metric(value){const v=num(value);return v===null?'null':Math.abs(v)>=100?v.toFixed(1):v.toFixed(3)}
 function duration(seconds){const s=num(seconds);if(s===null)return NOT_RECORDED;if(s<1)return '< 1 s';if(s<90)return `${Math.round(s)} s`;if(s<5400)return `${(s/60).toFixed(1)} min`;if(s<172800)return `${(s/3600).toFixed(1)} h`;return `${(s/86400).toFixed(1)} days`}
 function tokens(t,H){
  if(!t||typeof t!=='object')return NOT_RECORDED;
  const parts=[`${num(t.input)??'?'} in / ${num(t.output)??'?'} out`];
  if(num(t.cache_read))parts.push(`${t.cache_read} cached`);
  if(num(t.cache_creation))parts.push(`${t.cache_creation} cache writes`);
  if(num(t.reasoning))parts.push(`${t.reasoning} reasoning`);
  return H.esc(parts.join(' · '));
 }
 function cost(spend,H){
  const usd=num(spend?.cost_usd);if(usd===null)return NOT_RECORDED;
  const source=spend.cost_source==='ledger'?'recorded calls':spend.cost_source==='summary'?"runner's summary":'source not stated';
  return `$${usd>=1?usd.toFixed(2):usd.toFixed(3)} <small>${H.esc(source)}</small>`;
 }
 function labelsOf(run){
  const labels=run.labels&&typeof run.labels==='object'?Object.values(run.labels):[];
  return labels.map(l=>typeof l?.file==='string'?l.file.replace(/\.[a-z]+$/,''):null).filter(Boolean).sort();
 }
 function groupKey(run){const stems=labelsOf(run);return stems.length?`${stems.join(', ')}|${run.split||''}`:''}
 function groupTitle(key,H){
  if(!key)return 'Unscored runs (no label file recorded)';
  const [stems,split]=key.split('|');
  return `Scored against ${H.esc(stems)}${split?` · split ${H.esc(split)}`:' · split not recorded'}`;
 }
 function recordCell(run,H){
  const record=run.record||{};
  const kind=record.error?'Record unreadable':record.backfilled?'Backfilled manifest':record.manifest?'Recorded manifest':'run.json only';
  return `${H.esc(kind)}${record.error?`<small>${H.esc(record.error)}</small>`:''}`;
 }
 // The file name a download gets when the API's Content-Disposition is not readable (an
 // origin that does not expose it): the API's own naming, <run>.<file>.
 const FILE_SUFFIX={manifest:'manifest.json',report:'report.md',ledger:'llm_calls.jsonl',console:'console.log'};
 function fileName(kind,runName){return `${String(runName||'run').replace(/[^\w.@+-]+/g,'_')}.${FILE_SUFFIX[kind]||'txt'}`}
 function fileButtons(run,H){
  const files=run.files&&typeof run.files==='object'?run.files:{};
  const labels={manifest:'Manifest',report:'Report',ledger:'Ledger',console:'Console log'};
  const buttons=Object.entries(labels).filter(([name])=>typeof files[name]==='string').map(([name,label])=>`<button type="button" class="run-file" data-run-file="${H.esc(files[name])}" data-run-kind="${name}" data-run-name="${H.esc(run.name)}">${label}</button>`);
  return buttons.length?buttons.join(''):'<span class="run-none">None</span>';
 }
 function runRow(run,H){
  const git=run.git||{};
  const code=git.sha?`<code>${H.esc(String(git.sha).slice(0,7))}</code>${git.dirty===true?' <small>dirty tree</small>':git.dirty===null||git.dirty===undefined?' <small>dirty flag unknown</small>':''}${git.release?`<small>${H.esc(git.release)}</small>`:''}`:NOT_RECORDED;
  const prompts=(Array.isArray(run.prompts)?run.prompts:[]).map(p=>`${p.name}${p.version?` ${p.version}`:' (version not recorded)'}`);
  const architecture=`${run.component?`<strong>${H.esc(run.component)}</strong>${run.version?` <code>${H.esc(run.version)}</code>`:''}`:NOT_RECORDED}${prompts.length?`<small>${H.esc(prompts.join(', '))}</small>`:''}`;
  const sel=run.selection&&SELECTION_LABEL[run.selection.status]?run.selection:{status:'unknown',reasons:[]};
  const selection=`<span class="selection selection-${H.esc(sel.status)}" title="${H.esc((sel.reasons||[]).join(' '))}">${SELECTION_LABEL[sel.status]}</span>`;
  const models=(Array.isArray(run.models_requested)?run.models_requested:[]);
  const model=models.length?`${H.esc(models.join(', '))}${Array.isArray(run.reasoning_effort)&&run.reasoning_effort.length?`<small>effort ${H.esc(run.reasoning_effort.join(', '))}</small>`:''}`:NOT_RECORDED;
  const results=run.results&&typeof run.results==='object'?`${num(run.results.passed)??0} passed / ${num(run.results.failed)??0} failed / ${num(run.results.test_error)??0} test errors`:'No cases recorded';
  const status=`${run.status?H.esc(run.status):NOT_RECORDED}<small>${H.esc(results)}</small>`;
  const headline=Array.isArray(run.headline)?run.headline.filter(h=>h&&typeof h.dataset==='string'):[];
  const metrics=headline.length?headline.map(h=>`<span class="run-metric">${H.esc(h.dataset)} ${H.esc(h.metric)} = <strong>${metric(h.value)}</strong></span>`).join(''):'<span class="run-none">Not scored</span>';
  const spend=run.spend||{};
  const when=typeof run.started_at==='string'?H.esc(run.started_at.slice(0,16).replace('T',' ')):NOT_RECORDED;
  const dur=`${duration(spend.duration_seconds)}${spend.duration_source==='file_times'?' <small>estimated from file times</small>':''}`;
  return `<tr><th scope="row"><code>${H.esc(run.name)}</code><small>${H.esc(run.kind||'')}${run.harness?` · ${H.esc(String(run.harness).replace(/^evals\./,''))}`:''}</small></th><td>${architecture}${selection}</td><td>${model}</td><td>${code}</td><td>${when}</td><td>${status}</td><td class="run-metrics">${metrics}</td><td>${cost(spend,H)}</td><td>${tokens(spend.tokens,H)}</td><td>${dur}</td><td>${recordCell(run,H)}</td><td class="run-files">${fileButtons(run,H)}</td></tr>`;
 }
 function runsTable(title,runs,H){
  return `<div class="run-group"><h4>${title} <span class="comparison-meta">${runs.length} run${runs.length===1?'':'s'}</span></h4><div class="table-scroll" role="region" tabindex="0" aria-label="${title.replace(/<[^>]+>/g,'')}"><table class="run-records-table"><thead><tr><th>Run</th><th>Architecture</th><th>Model</th><th>Code</th><th>Started</th><th>Status</th><th>Measured</th><th>Cost (USD)</th><th>Tokens</th><th>Duration</th><th>Record</th><th>Files</th></tr></thead><tbody>${runs.map(r=>runRow(r,H)).join('')}</tbody></table></div></div>`;
 }
 // `runs`: {status: 'idle'|'loading'|'done', result, items, next} kept by the page per step.
 function renderRunRecords(components,runs,H){
  const list=Array.isArray(components)?components:[];
  const named=list.length?`<code>${list.map(H.esc).join('</code>, <code>')}</code>`:'';
  const head='<h4 class="run-records-title">Run records</h4>';
  if(!list.length)return `${head}<p class="detail-note">No evaluation component is filed for this step (an engineer\'s review or a step without a model call), so no run record applies.</p>`;
  const state=runs||{status:'idle'};
  if(state.status==='idle')return `${head}<p class="detail-note">Connect diagnostics to list the registry\'s runs for ${named}. Platform operators only; nothing is listed from this page\'s own files.</p>`;
  if(state.status==='loading')return `${head}<p class="detail-note" role="status">Reading run records for ${named}…</p>`;
  const result=state.result||{status:0,error:'network'};
  const message=runsMessage(result);
  if(message)return `${head}<p class="detail-note run-records-message" role="status">${H.esc(message)}</p>`;
  const items=Array.isArray(state.items)&&state.items.length?state.items:result.data.items;
  const total=result.data.total;
  if(!items.length)return `${head}<p class="detail-note run-records-message" role="status">The registry has no run that names ${named}. Runs of other components exist on this host.</p>`;
  const groups=new Map();
  for(const run of items){const key=groupKey(run);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(run)}
  const ordered=[...groups.entries()].sort((a,b)=>(a[0]===''?1:0)-(b[0]===''?1:0));
  const tables=ordered.map(([key,rows])=>runsTable(groupTitle(key,H),rows,H)).join('');
  const more=state.next?`<p class="run-records-more"><button type="button" data-run-more="${H.esc(state.next)}">Load older runs</button> <span class="comparison-meta">${items.length} of ${total} shown</span></p>`:`<p class="comparison-meta">${items.length} of ${total} run${total===1?'':'s'} for ${named}.</p>`;
  return `${head}${tables}${more}<p class="run-file-status" role="status" aria-live="polite"></p><p class="detail-note">Each table holds runs scored against the same label files and split; the architectures and models differ within a table, and that is the comparison. Every figure is what the run recorded: a cost from recorded calls is the priced sum of its ledger, a cost from the runner\'s summary is its own figure, a duration from file times is the backfill\'s estimate; "Not recorded" is a fact the files do not state. Files download through the API with the operator check.</p>`;
 }
 function links(entry,H){
  const all=[...(entry.links||[]),{label:'Run record format',path:'backEnd/evals/record.py'},{label:'Run registry',path:'backEnd/evals/registry.py'}];
  const seen=new Set();
  return all.filter(l=>!seen.has(l.path)&&seen.add(l.path)).map(l=>`<a href="${H.esc(REPOSITORY+l.path)}" target="_blank" rel="noopener noreferrer">${H.esc(l.label)} ↗</a>`).join('');
 }
 function render(node,services,H,runs){
  const entry=content?.steps?.[node.id];
  if(!entry){
   const rows=node.services.map(id=>({service:id}));
   return `<section><h3>How it works</h3><p class="detail-note">${H.esc(contentError||'The workflow content has not loaded yet.')} Explanations and measured results are in ${H.esc(CONTENT_PATH)}.</p></section><section><h3>Current runtime</h3><div class="table-scroll" role="region" tabindex="0" aria-label="Current runtime"><table><thead><tr><th>System</th><th>Model</th><th>Hosting / provider</th><th>Adapter</th><th>Backups</th></tr></thead><tbody>${runtimeRows(rows,services,H)}</tbody></table></div><p class="detail-note">Runtime fields come from the connected health API. Service health never says whether a study is signed or completed.</p></section>`;
  }
  const results=entry.results||{};
  const how=`<section><h3>How it works</h3><ol class="step-sequence">${entry.how.map((step,i)=>`<li><span class="step-number">${i+1}</span><div><strong>${H.esc(step.title)}</strong><p>${H.esc(step.detail)}</p><a class="source-ref" href="${H.esc(REPOSITORY+step.source)}" target="_blank" rel="noopener noreferrer">${H.esc(step.source.split('/').pop())} ↗</a></div></li>`).join('')}</ol></section>`;
  const runtime=`<section><h3>Current runtime</h3><div class="table-scroll" role="region" tabindex="0" aria-label="Current runtime"><table><thead><tr><th>System</th><th>Model</th><th>Hosting / provider</th><th>Adapter</th><th>Backups</th></tr></thead><tbody>${runtimeRows(entry.runtime,services,H)}</tbody></table></div><p class="detail-note">Every row is read from the connected health API: the model ids are what the worker reports it runs, hosting is what each process established about itself, and a backup is a configured fallback route. Service health never says whether a study is signed or completed.</p></section>`;
  const comparisons=(results.comparisons||[]).map((c,i)=>comparisonTable(c,H,i===0)).join('');
  const regression=results.regression?`<p class="regression-line"><strong>Regression:</strong> ${H.esc(results.regression.status)} (${sectionLink(results.regression.section,H)}). ${H.esc(results.regression.detail||'')}</p>`:'';
  const records=`<div class="run-records" id="run-records" data-step="${H.esc(node.id)}">${renderRunRecords(entry.components,runs,H)}</div>`;
  const tests=`<section><h3>Tests &amp; results</h3><p>${H.esc(results.summary||'')}</p>${comparisons}${metricsTable(results.metrics,H)}${spendLine(results.spend,H)}${regression}<div class="source-links">${links(entry,H)}</div><p class="detail-note">Figures are copied verbatim from ${H.esc(content.source.report)} and checked against it by ${H.esc('backEnd/tests/unit/test_developer_workflow_content.py')}; they are historical measurements, not a live benchmark.</p>${records}</section>`;
  const note=entry.note?`<p class="detail-note">${H.esc(entry.note)}</p>`:'';
  return how+runtime+note+tests;
 }
 function componentsOf(stepId){const list=content?.steps?.[stepId]?.components;return Array.isArray(list)?list.filter(c=>typeof c==='string'):[]}
 const api={CONTENT_PATH,REPOSITORY,load,setContent,validContent,render,runtimeRows,renderRunRecords,runsMessage,validRunsPage,componentsOf,fileName,duration,get content(){return content},get contentError(){return contentError}};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisArchitecture=api;
})(globalThis);
