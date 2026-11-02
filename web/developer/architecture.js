/* Step dialogs of the Cost Segregation Workflow: what each step runs, on which systems, and
   what was measured. The text and the figures come from workflow-content.json (every
   figure verbatim from EVAL_REPORT.md, every source path real: both are checked by
   backEnd/tests/unit/test_developer_workflow_content.py). The runtime table comes only from
   the health feed (GET /v1/ops/health): a model id, a host or a backup is never typed here.
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
 function links(entry,H){
  const all=[...(entry.links||[]),{label:'Run record format',path:'backEnd/evals/record.py'},{label:'Run registry',path:'backEnd/evals/registry.py'}];
  const seen=new Set();
  return all.filter(l=>!seen.has(l.path)&&seen.add(l.path)).map(l=>`<a href="${H.esc(REPOSITORY+l.path)}" target="_blank" rel="noopener noreferrer">${H.esc(l.label)} ↗</a>`).join('');
 }
 function render(node,services,H){
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
  const tests=`<section><h3>Tests &amp; results</h3><p>${H.esc(results.summary||'')}</p>${comparisons}${metricsTable(results.metrics,H)}${spendLine(results.spend,H)}${regression}<div class="source-links">${links(entry,H)}</div><p class="detail-note">Figures are copied verbatim from ${H.esc(content.source.report)} and checked against it by ${H.esc('backEnd/tests/unit/test_developer_workflow_content.py')}; they are historical measurements, not a live benchmark.</p></section>`;
  const note=entry.note?`<p class="detail-note">${H.esc(entry.note)}</p>`:'';
  return how+runtime+note+tests;
 }
 const api={CONTENT_PATH,REPOSITORY,load,setContent,validContent,render,runtimeRows,get content(){return content},get contentError(){return contentError}};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisArchitecture=api;
})(globalThis);
