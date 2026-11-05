/* Display the API's recorded amounts. Never recompute allocation in the browser. */
(function(scope){
 'use strict';
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const present=v=>v!==null&&v!==undefined&&v!=='';
 const number=v=>present(v)&&Number.isFinite(Number(v))?Number(v):null;
 const money=(v,digits=2)=>number(v)===null?'Not recorded':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:digits,maximumFractionDigits:digits}).format(Number(v));
 const percent=v=>number(v)===null?'Not recorded':new Intl.NumberFormat('en-US',{style:'percent',maximumFractionDigits:1}).format(Number(v));
 const words=v=>String(v??'').replace(/[_\.]+/g,' ').replace(/^./,s=>s.toUpperCase());
 const buckets={y5:'5-year',y7:'7-year',y15:'15-year',y27_5:'27.5-year',y39:'39-year',land:'Land'};
 const bucket=v=>buckets[v]||words(v);
 const when=v=>v&&!Number.isNaN(Date.parse(v))?new Intl.DateTimeFormat('en-US',{dateStyle:'medium',timeStyle:'short'}).format(new Date(v)):'Not recorded';
 const duration=v=>number(v)===null?'Not recorded':v<1000?`${v} ms`:v<60000?`${(v/1000).toFixed(1)} sec`:v<3600000?`${(v/60000).toFixed(1)} min`:`${(v/3600000).toFixed(1)} hr`;
 const text=v=>v===null||v===undefined?'Not recorded':typeof v==='object'?JSON.stringify(v,null,2):String(v);
 const route=(screen,id,entity)=>`#${screen}/${encodeURIComponent(id)}${entity?'/'+encodeURIComponent(entity):''}`;
 const link=(screen,id,label,entity)=>`<a class="button small" href="${route(screen,id,entity)}">${esc(label)}</a>`;
 const pair=(label,value)=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;
 const empty=message=>`<p class="result-empty">${esc(message)}</p>`;
 const table=(caption,headers,rows)=>`<div class="result-table-scroll" tabindex="0" role="region" aria-label="${esc(caption)}"><table class="result-table"><caption>${esc(caption)}</caption><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
 const row=values=>`<tr>${values.map((v,i)=>i?`<td>${v}</td>`:`<th scope="row">${v}</th>`).join('')}</tr>`;
 const section=(title,body,extra='')=>`<section class="result-section"><header><h2>${esc(title)}</h2>${extra}</header>${body}</section>`;
 const disclosure=(title,body,open=false)=>`<details class="result-disclosure" ${open?'open':''}><summary>${esc(title)}<span aria-hidden="true" class="result-disclosure-action"></span></summary><div>${body}</div></details>`;
 function checkList(checks){return checks?.length?`<ul class="result-checks">${checks.map(c=>`<li class="check-${esc(c.severity)}"><span class="result-check-status">${esc({ok:'Passed',error:'Needs correction',warning:'Review',not_checked:'Not checked'}[c.severity]||c.severity)}</span><div><p>${esc(c.message)}</p><code>${esc(c.code)}</code></div></li>`).join('')}</ul>`:''}
 function resultState(r){
  if(!r?.allocation)return {tone:'warning',title:'Allocation withheld',detail:'Resolve the items below before a final allocation can be produced.'};
  const checks=[...(r.worksheet?.checks||[]),...(r.allocation.checks||[]),...(r.reconciliation?.checks||[])];
  if(r.reconciliation?.status!=='reconciled'||checks.some(c=>c.severity==='error')||(r.missing||[]).length)return {tone:'warning',title:r.reconciliation?'Allocation needs review':'Legacy allocation · reconciliation unavailable',detail:'The recorded allocation is shown below. It is not presented as a reconciled final result.'};
  return {tone:'success',title:'Allocation reconciled',detail:'The replacement cost less depreciation is within the recorded reconciliation tolerance.'};
 }
 const missingInfo={intake:['Add the purchase price, land value and acquisition costs.','intake','Review study inputs'],building_rcn:['Check the living area and the appraisal cost approach.','appraisal','Review appraisal'],depreciation:['Check effective age, economic life or physical depreciation in the appraisal.','appraisal','Review appraisal'],cost_index:['A cited cost index is needed for the study’s cost year.','cost-indices','Review cost indices'],allocation:['Review the unpriced assets, building assumptions and reconciliation findings.','assets','Review assets']};
 function result(r,s,assets=[],{lead=true}={}){
  const st=resultState(r),w=r.worksheet,a=r.allocation,q=r.schedule||{},rec=r.reconciliation;
  const fixes=(r.missing||[]).filter(k=>k!=='cost_index'||!r.cost_index||r.cost_index.status==='published').map(k=>{const m=missingInfo[k]||[words(k),null,null];return `<li><span>${esc(m[0])}</span>${m[1]?link(m[1],s.id,m[2]):''}</li>`}).join('');
  const index=r.cost_index;
  const hero=`<section class="result-status is-${st.tone}"><div><h2>${st.title}</h2><p>${st.detail}</p>${s.status!=='completed'?'<p>Study review is still in progress.</p>':''}</div><span class="badge ${st.tone==='success'?'complete':'review'}">Revision ${esc(r.revision)}</span></section>${fixes?`<ul class="result-fixes">${fixes}</ul>`:''}${index&&index.status!=='published'?`<aside class="result-warning"><h3>${esc(index.cost_year)} cost index ${esc(index.status)}</h3><p>${esc(index.needed||index.assumption)}</p><p class="small">Applied: ${esc(index.applied)}</p>${link('cost-indices',s.id,'Review cost indices')}</aside>`:''}`;
  const stats=`<dl class="result-summary">${pair('Depreciable basis',money(w?.total_basis))}${pair('Replacement cost new',money(q.rcn_total))}${pair('Cost less depreciation',money(q.rcnld_total))}${pair('Allocated basis',a?money(a.total):'Withheld')}</dl>`;
  const worksheet=w?`<dl class="result-worksheet">${pair('Purchase price',money(w.purchase_price))}${pair('Less land',money(w.land))}${pair('Building basis',money(w.building_basis))}${pair('Capitalized acquisition costs',money(w.capitalized_costs))}${pair('Depreciable basis',money(w.total_basis))}</dl><p class="small">${esc(w.land_source)}</p><dl class="result-facts">${pair('Property use',words(w.property_use))}${pair('Building recovery',`${w.recovery_period} years`)}${pair('Placed in service',w.placed_in_service_date)}${pair('Location',[w.state,w.zip].filter(Boolean).join(' ')||'Not recorded')}</dl>${w.acquisition_costs?.length?disclosure('Acquisition cost detail',table('Capitalized acquisition costs',['Cost','Amount','Authority'],w.acquisition_costs.map(c=>row([esc(c.description||words(c.category)),money(c.amount),esc(c.authority)])))):''}${checkList(w.checks)}`:empty('Study inputs are missing. Add them to calculate depreciable basis.');
  const reconciliation=rec?`<dl class="result-facts">${pair('Result',words(rec.status))}${pair('Building basis',money(rec.building_basis))}${pair('Cost less depreciation',money(rec.rcnld_total))}${pair('Gap',money(rec.gap))}${pair('Gap / building basis',percent(rec.gap_share))}${pair('Allowed difference',percent(rec.tolerance))}</dl>${checkList(rec.checks)}${rec.appraisal_check?checkList([rec.appraisal_check]):''}${rec.causes?.length?disclosure('What could explain the difference',`<ul class="result-causes">${rec.causes.map(c=>`<li><strong>${esc(c.cause)}</strong><p>${esc(c.finding)}</p><code>${esc(c.code)}</code></li>`).join('')}</ul>`,rec.status!=='reconciled'):''}${rec.unassigned_remainders?.length?disclosure('Unassigned building costs',table('Unassigned remainders',['System','Model cost','Itemized cost','Concealed cost'],rec.unassigned_remainders.map(c=>row([esc(words(c.component)),money(c.top_down),money(c.itemized),money(c.concealed)])))):''}`:empty('This server or revision does not provide reconciliation. No reconciliation result is inferred.');
  const costs=a?.by_bucket||q.by_bucket||[];
  const allocation=table(a?'Allocation by recovery class':'Replacement cost by recovery class',['Recovery class','Assets','Replacement cost','Allocated basis','Share'],costs.map(c=>row([esc(bucket(c.bucket)),esc(c.rows??c.assets),money(c.rcn),a?money(c.allocated):'Withheld',a&&present(c.percent)?esc(c.percent)+'%':'—'])))+(a?`<dl class="result-facts">${pair('Allocation method',a.depreciated?'Cost less depreciation':'Undepreciated cost (legacy)')}${pair('Allocation factor',a.factor)}${pair('Allocated row sum',money(a.rows_sum))}${pair('Rounding adjustment',`${a.residual_cents} cents`)}</dl>${checkList(a.checks)}`:'');
  const systems=a?.by_system?.length?table('Allocation by building system',['Building system','Replacement cost','Cost less depreciation','Allocated basis'],a.by_system.map(c=>row([esc(c.system),money(c.rcn),money(c.rcnld),money(c.allocated)]))):empty(a?.by_system_reason||'Building-system allocation is unavailable until allocation is produced.');
  const assetRows=a?.assets?.length?disclosure('Allocated assets',table('Allocated assets',['Asset','Recovery class','Replacement cost','Allocated basis'],a.assets.map(c=>row([`<a href="${route('asset-detail',s.id,c.asset_id)}">${esc(words(c.label||c.category))}</a>`,esc(bucket(c.bucket)),money(c.rcn),money(c.allocated)])))):'';
  const unpriced=q.unpriced?.length?disclosure(`${q.unpriced.length} unpriced ${q.unpriced.length===1?'asset':'assets'}`,`<ul class="result-causes">${q.unpriced.map(id=>{const asset=assets.find(a=>a.id===id);return `<li>${link('asset-detail',s.id,asset?`${words(asset.label||asset.category)}${asset.room?.name?' · '+asset.room.name:''}`:`Review asset ${id}`,id)}</li>`}).join('')}</ul>`):'';
  return `<div class="results-workspace">${lead?hero+stats:''}<nav class="result-section-nav" aria-label="Result sections"><button data-live="result-jump" data-value="basis-worksheet">Basis worksheet</button><button data-live="result-jump" data-value="basis-reconciliation">Reconciliation</button><button data-live="result-jump" data-value="basis-allocation">Allocation</button>${link('structure',s.id,'Concealed systems')}${link('history',s.id,'Study history')}</nav><div class="result-columns"><div id="basis-worksheet">${section('Basis worksheet',worksheet,link('intake',s.id,'Review inputs'))}</div><div id="basis-reconciliation">${section('Reconciliation',reconciliation)}</div></div><div id="basis-allocation">${section('Recovery classes',allocation+unpriced+assetRows)}</div>${section('Building systems',systems)}${r.not_produced?.length?section('Systems not priced',`<ul class="result-causes">${r.not_produced.map(c=>`<li><strong>${esc(c.system)}</strong><p>${esc(c.reason)}</p></li>`).join('')}</ul>`):''}</div>`;
 }
 /* Split a recorded formula at operators outside brackets and quotes; null when it does not parse cleanly. */
 function splitTop(f,sep){
  const out=[];let depth=0,quote=false,start=0;
  for(let i=0;i<f.length;i++){
   const c=f[i];
   if(c==='"'){quote=!quote;continue}
   if(quote)continue;
   if(c==='('||c==='[')depth++;
   else if(c===')'||c===']'){depth--;if(depth<0)return null}
   else if(!depth&&f.startsWith(sep,i)){out.push(f.slice(start,i).trim());start=i+sep.length;i+=sep.length-1}
  }
  if(depth||quote)return null;
  out.push(f.slice(start).trim());
  return out;
 }
 /* The inside of one bracket group that spans the whole text, else null. */
 function unwrap(f){
  if(!f.startsWith('(')||!f.endsWith(')'))return null;
  let d=0,quote=false;
  for(let i=0;i<f.length;i++){
   const c=f[i];
   if(c==='"'){quote=!quote;continue}
   if(quote)continue;
   if(c==='('||c==='[')d++;
   else if(c===')'||c===']'){d--;if(!d&&i<f.length-1)return null}
  }
  return d?null:f.slice(1,-1);
 }
 /* Move long bracketed citations (or a long tail after a colon) out of a step into its source line. */
 function sourceOf(part){
  const keep=[],src=[];let d=0,quote=false,start=0,buf='';
  for(let i=0;i<part.length;i++){
   const c=part[i];
   if(c==='"')quote=!quote;
   if(quote)continue;
   if(c==='('||c==='['){if(!d){buf+=part.slice(start,i);start=i}d++}
   else if(c===')'||c===']'){d--;if(!d){const g=part.slice(start,i+1);if(g.length>40)src.push(g.slice(1,-1));else buf+=g;start=i+1}}
  }
  buf+=part.slice(start);
  let what=buf.replace(/\s+/g,' ').trim();
  if(!src.length){const k=what.indexOf(': ');if(k>0&&what.length-k>40){src.push(what.slice(k+2));what=what.slice(0,k)}}
  return [what,src.join('; ')];
 }
 function formulaSteps(f,amount){
  const factors=f?splitTop(f,' x '):null;
  if(!factors||factors.length<2)return '';
  const base=unwrap(factors[0]),addends=base?splitTop(base,' + '):null;
  if(!addends)return '';
  const line=(op,part)=>{const [what,src]=sourceOf(part);return `<tr><td class="calc-op" aria-label="${op==='+'?'plus':op==='×'?'times':''}">${op}</td><td>${esc(what)}${src?`<small>${esc(src)}</small>`:''}</td></tr>`};
  return `<div class="result-table-scroll" tabindex="0" role="region" aria-label="Building cost calculation"><table class="result-table calc-steps"><caption>Building cost calculation</caption><colgroup><col class="calc-op-col"><col></colgroup><thead><tr><th scope="col"><span class="sr-only">Operator</span></th><th scope="col">Step and source</th></tr></thead><tbody>${addends.map((a,i)=>line(i?'+':'',a)).join('')}${factors.slice(1).map(x=>line('×',x)).join('')}<tr class="calc-total"><td class="calc-op">=</td><td><strong>${money(amount)}</strong> building replacement cost</td></tr></tbody></table></div>`;
 }
 function structure(r,s){
  const b=r.structure?.building_rcn,m=b?.model,rows=r.structure?.concealed||[];
  return `<div class="results-workspace">${!r.structure?empty('This server or revision has no recorded structure model. Photographs alone do not establish concealed systems.'):''}${b?section('Building cost model',`<dl class="result-summary">${pair('Building replacement cost',money(b.amount))}${pair('Quality class',words(m?.cls)||'Not recorded')}${pair('Building type',m?.type||'Not recorded')}</dl><dl class="result-facts">${pair('Cost basis',words(b.source)||'Not recorded')}${pair('Model',m?.name||'Not recorded')}</dl>${formulaSteps(b.formula,b.amount)||`<p class="result-formula">${esc(b.formula)}</p>`}<p class="small">${esc(m?.source)}</p>${disclosure('Recorded inputs and formula',`<dl class="result-facts">${Object.entries(b.inputs||{}).map(([k,v])=>pair(words(k),text(v))).join('')}</dl>${formulaSteps(b.formula,b.amount)?`<p class="result-formula">${esc(b.formula)}</p>`:''}`)}${disclosure('Model choice and assumptions',`<p>${esc(m?.class_source)}</p><p>${esc(m?.type_source)}</p><ul>${(b.assumptions||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`,b.needs_review)}${checkList(b.checks)}`,link('appraisal',s.id,'Review appraisal inputs')):section('Building model',empty('The model cannot be calculated from the available appraisal inputs.'))}${section('Concealed systems',rows.length?table('Concealed systems',['System / asset','Recovery class','Replacement cost'],rows.map(c=>row([`<a href="${route('asset-detail',s.id,c.asset_id)}">${esc(c.system)} · ${esc(words(c.label))}</a>`,esc(bucket(c.bucket)),money(c.rcn)]))):empty('No concealed-system rows were returned.'))}${r.structure?.components?.length?disclosure('How the building model is split',table('Model cost reconciliation',['System','Model cost','Itemized','Concealed','Residual','Excess'],r.structure.components.map(c=>row([esc(c.name),money(c.top_down),money(c.itemized),money(c.concealed),money(c.residual),money(c.clamped)])))):''}${r.depreciation?section('Physical depreciation',`<dl class="result-facts">${pair('Rate',percent(r.depreciation.rate))}${pair('Method',words(r.depreciation.method))}</dl><p>${esc(r.depreciation.source)}</p>${checkList(r.depreciation.checks)}`):''}</div>`;
 }
 function costTrail(a){
  const c=a.cost,t=a.takeoff;
  return section('Cost calculation',`<div class="cost-equation"><div><span>Quantity</span><strong>${t?`${esc(t.quantity)} ${esc(t.unit)}`:'Not recorded'}</strong></div><span aria-hidden="true">×</span><div><span>Adjusted unit cost</span><strong>${money(c?.unit_cost)}</strong></div><span aria-hidden="true">=</span><div><span>Replacement cost</span><strong>${money(c?.total)}</strong></div></div>${t?.formula?`<p class="small">Quantity: ${esc(t.formula)}</p>`:''}<dl class="result-facts">${pair('Cost year',c?.cost_year??'Not recorded')}${pair('Location factor',c?.location_factor??'Not recorded')}</dl>${c?.source_row?`<div class="recorded-source"><h3>Recorded cost source</h3><p>${esc(c.source_row)}</p></div>`:empty('No cost source was recorded for this asset.')}<p class="small">The unit cost above already includes year and location adjustments. A separate base unit cost is unavailable in the recorded cost details.</p>`);
 }
 function changes(before,after,keys){
  const fields=keys?.length?keys:[...new Set([...Object.keys(before&&typeof before==='object'?before:{}),...Object.keys(after&&typeof after==='object'?after:{})])];
  return fields.length?table('Recorded changes',['Field','Before','After'],fields.map(k=>row([esc(words(k)),`<pre>${esc(text(before?.[k]))}</pre>`,`<pre>${esc(text(after?.[k]))}</pre>`]))):`<div class="result-before-after"><div><h4>Before</h4><pre>${esc(text(before))}</pre></div><div><h4>After</h4><pre>${esc(text(after))}</pre></div></div>`;
 }
 function provenance(a,s,person=id=>id||'Not recorded'){
  const p=a.provenance,c=a.convention;
  const origin={rule:'Classification rule',model:'Model classification',llm:'Model classification',cache:'Firm ruling',manual:'Engineer classification'}[a.method]||words(a.method)||'Not recorded';
  return section('Classification source',`<dl class="result-facts">${pair('Method',origin)}${pair('Row origin',p?words(p.origin):'Not recorded')}</dl>${c?`<dl class="result-facts">${pair('Approved by',person(c.reviewer_id))}${pair('Approved on',when(c.approved_at))}${pair('Approved studies',c.approvals)}${pair('Match level',c.matched_tier?words(c.matched_tier):'Not recorded')}${pair('Rules comparison',c.agrees_with_rules===true?'Agrees with the rules table':c.agrees_with_rules===false?'Disagrees with the rules table':'Not recorded')}</dl>${c.agrees_with_rules===false?'<p class="result-warning">The firm ruling disagrees with the rules table. Review the cited guidance before approving.</p>':''}${c.citation?`<p>${esc(c.citation)}</p>`:''}${link('processing',c.study_id,'Open originating study')}${disclosure('Ruling context',`<p>${esc(c.key)}</p>${c.rule_key?`<p>Rule: ${esc(c.rule_key)}</p>`:''}`)}`:a.method==='cache'?empty('The originating ruling was not recorded for this revision.'):''}${p&&p.origin!=='model'?`<p>${esc(person(p.by))} changed this at ${esc(words(p.stage))}, revision ${esc(p.revision??'not recorded')} · ${when(p.at)}</p>${disclosure('Engineer changes',changes(p.before,p.after,p.changed))}`:''}`);
 }
 function csv(r){
  const cols=['asset_id','category','recovery_class','replacement_cost','cost_less_depreciation','allocated_basis','rounding_cents'];
  const rows=[cols,...(r.allocation?.assets||[]).map(a=>[a.asset_id,a.category,bucket(a.bucket),a.rcn,a.rcnld??'',a.allocated,a.rounding_cents])];
  return rows.map(r=>r.map(v=>{let s=String(v??'');if(/^[=+@\t\r]/.test(s)||(/^[-]/.test(s)&&!/^-[\d.]+$/.test(s)))s="'"+s;return '"'+s.replace(/"/g,'""')+'"'}).join(',')).join('\r\n');
 }
 function indexBody(d){
  const body={series:d.series,year:Number(d.year),region:d.series==='historical'?'US':String(d.region||'').trim().toUpperCase(),value:String(d.value||'').trim(),as_of:d.as_of,citation:String(d.citation||'').trim(),url:d.url?.trim()||null,derived:d.derived?.trim()||null};
  const errors=[];
  if(!['historical','city'].includes(body.series))errors.push('Choose an index series.');
  if(!Number.isInteger(body.year)||body.year<1900||body.year>2200)errors.push('Enter a year from 1900 to 2200.');
  if(!/^[0-9]{1,10}(\.[0-9]{1,4})?$/.test(body.value)||Number(body.value)<=0)errors.push('Enter a positive value with up to four decimal places.');
  if(!/^\d{4}-\d{2}-\d{2}$/.test(body.as_of)||Number(body.as_of.slice(0,4))!==body.year||Number.isNaN(Date.parse(body.as_of))||new Date(body.as_of).toISOString().slice(0,10)!==body.as_of)errors.push('The effective date must be a valid date in the selected year.');
  if(!body.citation||body.citation.length>1000||/[\x00-\x1f\x7f]/.test(body.citation))errors.push('Enter a single-line citation of up to 1,000 characters.');
  if(body.derived&&(body.derived.length>1000||/[\x00-\x1f\x7f]/.test(body.derived)))errors.push('Enter a single-line conversion description of up to 1,000 characters.');
  if(body.url&&!/^https?:\/\//i.test(body.url))errors.push('The source URL must start with https:// or http://.');
  if(body.series==='city'&&(!/^[A-Z]{2}(:\d{3}(-\d{3})?(,\d{3}(-\d{3})?)*)?$/.test(body.region)||body.region==='US'))errors.push('Use a state code or ZIP-prefix region, for example CA or CA:900-902.');
  if(body.series==='city'&&body.region.includes(':')){if(!d.city?.trim())errors.push('Enter the city name for a ZIP-prefix region.');else body.detail={city:d.city.trim()}}
  return {body,errors};
 }
 const api={esc,present,number,money,percent,words,bucket,when,duration,text,route,link,pair,empty,table,row,section,disclosure,checkList,resultState,result,structure,formulaSteps,costTrail,changes,provenance,csv,indexBody};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisResultView=api;
})(globalThis);
