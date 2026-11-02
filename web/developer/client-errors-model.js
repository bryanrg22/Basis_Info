/* The operator's client-errors page, the parts that are rules rather than markup
 * (node-tested): the query string from the filter form, the list rows, the detail view of
 * one record, and the escaping that keeps a recorded error message from running as HTML. */
(function(scope){
 'use strict';
 const FILTERS=['org_id','user_id','study_id','code','kind','request_id','since','until'];
 const KINDS=['error','unhandledrejection','api','console','resource','report'];
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
 // GET /v1/ops/client-errors?...: only the filters with a value, trimmed; a date-only
 // `since`/`until` becomes the start (or end) of that day in UTC.
 function query(form,{limit=50,cursor=null}={}){
  const q={limit};
  for(const k of FILTERS){let v=String(form?.[k]??'').trim();if(!v)continue;
   if((k==='since'||k==='until')&&/^\d{4}-\d{2}-\d{2}$/.test(v))v+=k==='since'?'T00:00:00Z':'T23:59:59Z';
   q[k]=v}
  if(cursor)q.cursor=cursor;
  return q;
 }
 const when=v=>{if(!v)return '—';const d=new Date(v);return isNaN(d)?String(v):d.toISOString().replace('T',' ').replace(/\.\d{3}Z$/,' UTC')};
 const short=(s,n=90)=>{s=String(s??'');return s.length>n?s.slice(0,n-1)+'…':s};
 const idShort=id=>id?String(id).slice(0,8):'—';
 function row(r){
  const where=[r.screen,r.route].filter(Boolean).join(' · ');
  const http=r.http_status!==null&&r.http_status!==undefined?`${r.http_status}${r.code?' '+r.code:''}`:(r.code||'');
  return `<tr data-id="${esc(r.id)}"><td><time datetime="${esc(r.received_at)}">${esc(when(r.received_at))}</time><small>client ${esc(when(r.occurred_at))}</small></td><td><span class="kind kind-${esc(r.kind)}">${esc(r.kind)}</span>${r.error_type?`<small>${esc(r.error_type)}</small>`:''}</td><td class="msg"><a href="#${esc(r.id)}">${esc(short(r.message))}</a>${where?`<small>${esc(where)}</small>`:''}</td><td>${esc(http)}${r.request_id?`<small>req ${esc(r.request_id)}</small>`:''}</td><td><small title="${esc(r.user_id)}">user ${esc(idShort(r.user_id))}</small><small title="${esc(r.org_id||'')}">firm ${esc(idShort(r.org_id))}</small><small title="${esc(r.study_id||'')}">study ${esc(idShort(r.study_id))}${r.revision?` r${esc(r.revision)}`:''}</small></td><td><small>${esc(r.build_sha?String(r.build_sha).slice(0,8):'—')}</small><small>${esc(r.web_version||'')}</small></td></tr>`;
 }
 function rows(items){
  if(!items?.length)return `<tr><td colspan="6" class="empty">No records match.</td></tr>`;
  return items.map(row).join('');
 }
 // A JSON value as nested definition lists; strings escaped, long ones kept whole in <pre>.
 function tree(value,depth=0){
  if(value===null||value===undefined)return '<i>null</i>';
  if(Array.isArray(value)){if(!value.length)return '<i>[]</i>';return `<ol start="0">${value.map(v=>`<li>${tree(v,depth+1)}</li>`).join('')}</ol>`}
  if(typeof value==='object'){const keys=Object.keys(value);if(!keys.length)return '<i>{}</i>';return `<dl>${keys.map(k=>`<dt>${esc(k)}</dt><dd>${tree(value[k],depth+1)}</dd>`).join('')}</dl>`}
  const s=String(value);
  return s.length>120||s.includes('\n')?`<pre>${esc(s)}</pre>`:`<code>${esc(s)}</code>`;
 }
 function detail(r){
  const e=r.event||{},http=e.http,src=e.source,srv=e._server||{};
  const facts=[['Received',when(r.received_at)],['Occurred (client clock)',when(r.occurred_at)],['Clock offset',srv.clock_offset_ms===null||srv.clock_offset_ms===undefined?'unknown':`${srv.clock_offset_ms} ms`],['Since page load',e.since_load_ms!==undefined&&e.since_load_ms!==null?`${Math.round(e.since_load_ms)} ms`:'—'],['Kind · level',`${r.kind} · ${e.level||'error'}`],['Type',r.error_type||'—'],['User',`${e.user?.email||'—'} (${e.user?.uid||'—'}, ${e.user?.role||'no role'}; user ${r.user_id})`],['Firm',`${e.firm?.org_name||'—'} (${r.org_id||'unverified: '+(e.firm?.org_id||'none')})`],['Study',r.study_id?`${r.study_id} · revision ${r.revision??'—'} · ${r.stage||'—'}`:(e.study?.study_id?`not visible to reporter: ${e.study.study_id}`:'—')],['Page',`${r.screen||'—'} · ${r.route||'—'}`],['Source',src?`${src.file||'—'}:${src.line??'—'}:${src.column??'—'}${src.function?' in '+src.function:''}`:'—'],['Request',http?`${http.method||''} ${http.route||''} → ${http.status} ${http.code||''} · request ${http.request_id||'—'} · ${http.latency_ms??'—'} ms · attempt ${http.attempt??1}${http.retry?' (retry)':''}`:'—'],['Build',`API ${r.build_sha||'unknown'} (now ${srv.api_git_sha||'?'}) · web ${r.web_version||'—'} · capture ${e.capture_version||'—'}`],['Browser',`${r.user_agent||'—'} · ${e.client?.platform||''} · ${e.client?.language||''} · ${e.client?.viewport?e.client.viewport.width+'×'+e.client.viewport.height:''} @${e.client?.device_pixel_ratio||1}x · ${e.client?.online===false?'offline':'online'} · ${e.client?.timezone||''}`],['Repeats · dropped',`${e.repeats??1} · ${e.dropped??0}`],['Report request',`${srv.api_request_id||'—'} from ${srv.remote_addr||'—'}`],['Event id',r.event_id]];
  const crumbs=(e.breadcrumbs||[]).map(c=>`<tr><td>${esc(when(c.at))}</td><td>${esc(c.since_load_ms??'')}</td><td>${esc(c.kind)}</td><td>${esc(c.message||'')}</td><td>${c.data?`<code>${esc(JSON.stringify(c.data))}</code>`:''}</td></tr>`).join('');
  return `<section class="detail"><h2>${esc(short(r.message,200))}</h2><dl class="facts">${facts.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>${e.stack?`<h3>Stack</h3><pre class="stack">${esc(e.stack)}</pre>`:''}${http?.problem_detail?`<h3>Problem</h3><p>${esc(http.problem_title||'')} ${esc(http.problem_detail)}</p>`:''}${e.context?`<h3>Context</h3>${tree(e.context)}`:''}<h3>Last actions (${(e.breadcrumbs||[]).length})</h3>${crumbs?`<table class="crumbs"><thead><tr><th>At</th><th>ms</th><th>Kind</th><th>What</th><th>Data</th></tr></thead><tbody>${crumbs}</tbody></table>`:'<p>None recorded.</p>'}<h3>Whole event</h3>${tree(e)}</section>`;
 }
 const api={FILTERS,KINDS,esc,query,when,short,row,rows,tree,detail};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisClientErrors=api;
})(typeof globalThis==='object'?globalThis:this);
