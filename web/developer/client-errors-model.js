/* The operator's client-errors page, the parts that are rules rather than markup
 * (node-tested): the query string from the filter form, the list rows, the detail view of
 * one record, and the escaping that keeps a recorded error message from running as HTML. */
(function(scope){
 'use strict';
 const FILTERS=['org_id','user_id','study_id','code','kind','status','request_id','since','until'];
 const KINDS=['error','unhandledrejection','api','console','resource','report'];
 // The issue statuses a person sets, and the one the ingest sets (regressed).
 const STATUSES=['open','in_progress','resolved','ignored','regressed'];
 const STATUS_LABEL={open:'Open',in_progress:'In progress',resolved:'Resolved',ignored:'Ignored',regressed:'Regressed'};
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
 // GET /v1/ops/client-errors?...: only the filters with a value, trimmed; a date-only
 // `since`/`until` becomes the start (or end) of that day in UTC.
 function query(form,{limit=50,cursor=null}={}){
  const q={limit};
  for(const k of [...FILTERS,'fingerprint']){let v=String(form?.[k]??'').trim();if(!v)continue;
   if((k==='since'||k==='until')&&/^\d{4}-\d{2}-\d{2}$/.test(v))v+=k==='since'?'T00:00:00Z':'T23:59:59Z';
   q[k]=v}
  if(cursor)q.cursor=cursor;
  return q;
 }
 const when=v=>{if(!v)return '—';const d=new Date(v);return isNaN(d)?String(v):d.toISOString().replace('T',' ').replace(/\.\d{3}Z$/,' UTC')};
 const short=(s,n=90)=>{s=String(s??'');return s.length>n?s.slice(0,n-1)+'…':s};
 const idShort=id=>id?String(id).slice(0,8):'—';
 const statusBadge=s=>s?`<span class="status status-${esc(s)}">${esc(STATUS_LABEL[s]||s)}</span>`:'';
 function row(r){
  const where=[r.screen,r.route].filter(Boolean).join(' · ');
  const http=r.http_status!==null&&r.http_status!==undefined?`${r.http_status}${r.code?' '+r.code:''}`:(r.code||'');
  return `<tr data-id="${esc(r.id)}"><td><time datetime="${esc(r.received_at)}">${esc(when(r.received_at))}</time><small>client ${esc(when(r.occurred_at))}</small></td><td><span class="kind kind-${esc(r.kind)}">${esc(r.kind)}</span>${r.error_type?`<small>${esc(r.error_type)}</small>`:''}${statusBadge(r.issue_status)}</td><td class="msg"><a href="#${esc(r.id)}">${esc(short(r.message))}</a>${where?`<small>${esc(where)}</small>`:''}</td><td>${esc(http)}${r.request_id?`<small>req ${esc(r.request_id)}</small>`:''}</td><td><small title="${esc(r.user_id)}">user ${esc(idShort(r.user_id))}</small><small title="${esc(r.org_id||'')}">firm ${esc(idShort(r.org_id))}</small><small title="${esc(r.study_id||'')}">study ${esc(idShort(r.study_id))}${r.revision?` r${esc(r.revision)}`:''}</small></td><td><small>${esc(r.build_sha?String(r.build_sha).slice(0,8):'—')}</small><small>${esc(r.web_version||'')}</small></td></tr>`;
 }
 function rows(items){
  if(!items?.length)return `<tr><td colspan="6" class="empty">No records match.</td></tr>`;
  return items.map(row).join('');
 }
 // The issues table: one row per fingerprint with its counts, status and a status control.
 function issueRow(i){
  const http=i.http_status!==null&&i.http_status!==undefined?`${i.http_status}${i.code?' '+i.code:''}`:(i.code||'');
  return `<tr data-fingerprint="${esc(i.fingerprint)}"><td><span class="kind kind-${esc(i.kind)}">${esc(i.kind)}</span>${i.error_type?`<small>${esc(i.error_type)}</small>`:''}</td><td class="msg"><a href="#issue/${esc(i.fingerprint)}">${esc(short(i.title))}</a><small>${esc([i.route,http].filter(Boolean).join(' · '))}</small></td><td class="num">${esc(i.occurrences)}</td><td><small>first ${esc(when(i.first_seen_at))}</small><small>last ${esc(when(i.last_seen_at))}</small></td><td><small>${esc(i.last_build_sha?String(i.last_build_sha).slice(0,8):'—')}</small></td><td>${statusControl(i)}</td></tr>`;
 }
 function issueRows(items){
  if(!items?.length)return `<tr><td colspan="6" class="empty">No issues match.</td></tr>`;
  return items.map(issueRow).join('');
 }
 function statusControl(i){
  const who=i.status_set_at?`set ${when(i.status_set_at)}${i.status_build_sha?' on '+String(i.status_build_sha).slice(0,8):''}`:'';
  return `<form class="status-form" data-fingerprint="${esc(i.fingerprint)}">${statusBadge(i.status)}<select name="status" aria-label="Status">${STATUSES.filter(s=>s!=='regressed'||i.status==='regressed').map(s=>`<option value="${s}" ${s===i.status?'selected':''}>${esc(STATUS_LABEL[s])}</option>`).join('')}</select><input name="note" placeholder="note (optional)" value="${esc(i.note||'')}" maxlength="500"><button type="submit" class="quiet small">Save</button><small>${esc(who)}</small></form>`;
 }
 // One plain-text brief of a record, ready to paste into a coding agent's prompt.
 function brief(r,issue){
  const e=r.event||{},http=e.http,src=e.source,srv=e._server||{},c=e.client||{},lines=[];
  const line=(k,v)=>{if(v!==null&&v!==undefined&&v!=='')lines.push(`${k}: ${v}`)};
  lines.push(`# Basis client error ${r.id}`);
  line('Message',r.message);
  line('Kind',`${r.kind} (${e.level||'error'})${r.error_type?' · '+r.error_type:''}`);
  line('Issue',issue?`${issue.fingerprint} · ${STATUS_LABEL[issue.status]||issue.status} · ${issue.occurrences} occurrence${issue.occurrences===1?'':'s'} · first ${when(issue.first_seen_at)} · last ${when(issue.last_seen_at)}${issue.note?' · note: '+issue.note:''}`:r.fingerprint);
  line('Occurred',`${r.occurred_at} (client clock${srv.clock_offset_ms!==null&&srv.clock_offset_ms!==undefined?`, offset ${srv.clock_offset_ms} ms from the server`:''}) · received ${r.received_at}${e.since_load_ms!==null&&e.since_load_ms!==undefined?` · ${Math.round(e.since_load_ms)} ms after page load`:''}`);
  line('User',`${e.user?.email||'—'} (uid ${e.user?.uid||'—'}, ${e.user?.role||'no role'}) · user_id ${r.user_id}`);
  line('Firm',`${e.firm?.org_name||'—'} · org_id ${r.org_id||'unverified'}${!r.org_id&&e.firm?.org_id?' (claimed '+e.firm.org_id+')':''}`);
  line('Study',r.study_id?`${r.study_id} · revision ${r.revision??'—'} · stage ${r.stage||'—'}${e.study?.status?' · '+e.study.status:''}`:(e.study?.study_id?`${e.study.study_id} (not visible to the reporter)`:null));
  line('Page',`screen ${r.screen||'—'} · route ${r.route||'—'}${e.page?.path?' · path '+e.page.path:''}`);
  if(http)line('Request',`${http.method||''} ${http.route||''} -> ${http.status}${http.code?' '+http.code:''} · request_id ${http.request_id||'—'} · ${http.latency_ms??'—'} ms · attempt ${http.attempt??1}${http.retry?' (retry)':''}${http.body_fields?.length?' · body fields: '+http.body_fields.join(', '):''}${http.problem_detail?`\n  Problem: ${http.problem_title||''} ${http.problem_detail}`:''}`);
  if(src)line('Source',`${src.file||'—'}:${src.line??'—'}:${src.column??'—'}${src.function?' in '+src.function:''}`);
  line('Build',`API ${r.build_sha||'unknown'}${srv.api_git_sha&&srv.api_git_sha!==r.build_sha?` (API now ${srv.api_git_sha})`:''} · web ${r.web_version||'—'} · capture ${e.capture_version||'—'}${srv.api_env?' · env '+srv.api_env:''}`);
  line('Browser',`${r.user_agent||'—'}\n  ${[c.platform,c.language,c.viewport?`${c.viewport.width}x${c.viewport.height}`:null,c.device_pixel_ratio?`@${c.device_pixel_ratio}x`:null,c.online===false?'offline':'online',c.timezone,c.connection,c.visibility].filter(Boolean).join(' · ')}`);
  line('Repeats / dropped',`${e.repeats??1} / ${e.dropped??0}`);
  line('Report request',`${srv.api_request_id||'—'}${srv.remote_addr?' from '+srv.remote_addr:''}`);
  if(e.stack)lines.push('','Stack:',e.stack);
  if(e.context)lines.push('','Context:',JSON.stringify(e.context,null,2));
  const crumbs=e.breadcrumbs||[];
  lines.push('',`Last actions (${crumbs.length}, oldest first):`);
  crumbs.forEach((b,n)=>lines.push(`${String(n+1).padStart(2,' ')}. [${b.at}${b.since_load_ms!==null&&b.since_load_ms!==undefined?` +${Math.round(b.since_load_ms)}ms`:''}] ${b.kind}: ${b.message||''}${b.data?' '+JSON.stringify(b.data):''}`));
  lines.push('','Where to look next:',`- The failed request's server line: grep the API log for request_id=${http?.request_id||'(none: not an API failure)'}`,`- The whole event: GET /v1/ops/client-errors/${r.id} (operator token)`,`- The study's timeline: GET /v1/studies/${r.study_id||'{study_id}'}/timeline?event_type=client.error`,'- Contract and field meanings: docs/api/CLIENT_ERRORS.md');
  return lines.join('\n');
 }
 // A JSON value as nested definition lists; strings escaped, long ones kept whole in <pre>.
 function tree(value,depth=0){
  if(value===null||value===undefined)return '<i>null</i>';
  if(Array.isArray(value)){if(!value.length)return '<i>[]</i>';return `<ol start="0">${value.map(v=>`<li>${tree(v,depth+1)}</li>`).join('')}</ol>`}
  if(typeof value==='object'){const keys=Object.keys(value);if(!keys.length)return '<i>{}</i>';return `<dl>${keys.map(k=>`<dt>${esc(k)}</dt><dd>${tree(value[k],depth+1)}</dd>`).join('')}</dl>`}
  const s=String(value);
  return s.length>120||s.includes('\n')?`<pre>${esc(s)}</pre>`:`<code>${esc(s)}</code>`;
 }
 function detail(r,issue){
  const e=r.event||{},http=e.http,src=e.source,srv=e._server||{};
  const facts=[['Received',when(r.received_at)],['Occurred (client clock)',when(r.occurred_at)],['Clock offset',srv.clock_offset_ms===null||srv.clock_offset_ms===undefined?'unknown':`${srv.clock_offset_ms} ms`],['Since page load',e.since_load_ms!==undefined&&e.since_load_ms!==null?`${Math.round(e.since_load_ms)} ms`:'—'],['Kind · level',`${r.kind} · ${e.level||'error'}`],['Type',r.error_type||'—'],['User',`${e.user?.email||'—'} (${e.user?.uid||'—'}, ${e.user?.role||'no role'}; user ${r.user_id})`],['Firm',`${e.firm?.org_name||'—'} (${r.org_id||'unverified: '+(e.firm?.org_id||'none')})`],['Study',r.study_id?`${r.study_id} · revision ${r.revision??'—'} · ${r.stage||'—'}`:(e.study?.study_id?`not visible to reporter: ${e.study.study_id}`:'—')],['Page',`${r.screen||'—'} · ${r.route||'—'}`],['Source',src?`${src.file||'—'}:${src.line??'—'}:${src.column??'—'}${src.function?' in '+src.function:''}`:'—'],['Request',http?`${http.method||''} ${http.route||''} → ${http.status} ${http.code||''} · request ${http.request_id||'—'} · ${http.latency_ms??'—'} ms · attempt ${http.attempt??1}${http.retry?' (retry)':''}`:'—'],['Build',`API ${r.build_sha||'unknown'} (now ${srv.api_git_sha||'?'}) · web ${r.web_version||'—'} · capture ${e.capture_version||'—'}`],['Browser',`${r.user_agent||'—'} · ${e.client?.platform||''} · ${e.client?.language||''} · ${e.client?.viewport?e.client.viewport.width+'×'+e.client.viewport.height:''} @${e.client?.device_pixel_ratio||1}x · ${e.client?.online===false?'offline':'online'} · ${e.client?.timezone||''}`],['Repeats · dropped',`${e.repeats??1} · ${e.dropped??0}`],['Report request',`${srv.api_request_id||'—'} from ${srv.remote_addr||'—'}`],['Event id',r.event_id]];
  const crumbs=(e.breadcrumbs||[]).map(c=>`<tr><td>${esc(when(c.at))}</td><td>${esc(c.since_load_ms??'')}</td><td>${esc(c.kind)}</td><td>${esc(c.message||'')}</td><td>${c.data?`<code>${esc(JSON.stringify(c.data))}</code>`:''}</td></tr>`).join('');
  return `<section class="detail"><div class="detail-tools"><button type="button" class="primary" data-copy="brief">Copy for an agent</button><button type="button" data-copy="json">Copy JSON</button><span class="muted" data-copied></span></div><h2>${esc(short(r.message,200))}</h2>${issue?`<div class="issue-box"><strong>Issue</strong> <code>${esc(issue.fingerprint)}</code> · ${esc(issue.occurrences)} occurrence${issue.occurrences===1?'':'s'} · first ${esc(when(issue.first_seen_at))} · last ${esc(when(issue.last_seen_at))}${statusControl(issue)}</div>`:''}<dl class="facts">${facts.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>${e.stack?`<h3>Stack</h3><pre class="stack">${esc(e.stack)}</pre>`:''}${http?.problem_detail?`<h3>Problem</h3><p>${esc(http.problem_title||'')} ${esc(http.problem_detail)}</p>`:''}${e.context?`<h3>Context</h3>${tree(e.context)}`:''}<h3>Last actions (${(e.breadcrumbs||[]).length})</h3>${crumbs?`<table class="crumbs"><thead><tr><th>At</th><th>ms</th><th>Kind</th><th>What</th><th>Data</th></tr></thead><tbody>${crumbs}</tbody></table>`:'<p>None recorded.</p>'}<h3>Whole event</h3>${tree(e)}</section>`;
 }
 const api={FILTERS,KINDS,STATUSES,STATUS_LABEL,esc,query,when,short,row,rows,issueRow,issueRows,statusControl,brief,tree,detail};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisClientErrors=api;
})(typeof globalThis==='object'?globalThis:this);
