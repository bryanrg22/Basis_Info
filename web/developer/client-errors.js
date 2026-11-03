/* The operator's client-errors page: GET /v1/ops/client-errors (the list, with filters and
 * paging), GET /v1/ops/client-errors/{id} (one record with the whole event) and, for a
 * study_input record, GET /v1/ops/studies/{id}/documents/{id}/content (open a firm's file,
 * recorded on the study's history). Platform
 * operators only (OPS_OPERATOR_UIDS); the page signs in with the same Firebase session as
 * the app (live/auth.js) and reads through the same API client (live/api-client.js). The
 * rules and markup are in client-errors-model.js (node-tested). */
(function(){
 'use strict';
 const M=BasisClientErrors,C=BASIS_CONFIG,$=id=>document.getElementById(id);
 const api=BasisApi.createClient({baseUrl:C.apiBase,getToken:()=>BasisAuth.getToken()});
 const back=new URL('../',document.baseURI);back.search=location.search;back.searchParams.set('mode','live');back.hash='dashboard';$('app-link').href=back.toString();
 $('api-origin').textContent=C.apiBase;
 for(const k of M.KINDS)$('kind').insertAdjacentHTML('beforeend',`<option value="${k}">${k}</option>`);
 for(const k of M.STATUSES)$('status').insertAdjacentHTML('beforeend',`<option value="${k}">${M.STATUS_LABEL[k]}</option>`);
 const S={cursor:null,stack:[],items:[],issues:[],current:null};
 const problem=e=>e?.status===403?'This account is not a platform operator (OPS_OPERATOR_UIDS).':e?.status===401?'Sign in to Basis first, then reload this page.':e?.message||String(e);
 function form(){const f={};for(const k of [...M.FILTERS,'fingerprint'])f[k]=$(k).value;return f}
 async function loadIssues(){
  try{const page=await api.request('GET','/v1/ops/client-errors/issues',{query:{limit:50,...($('status').value?{status:$('status').value}:{}),...($('kind').value?{kind:$('kind').value}:{})}});S.issues=page.items;$('issue-rows').innerHTML=M.issueRows(page.items);$('issue-count').textContent=`${page.items.length} issue${page.items.length===1?'':'s'}${page.next_cursor?' · more':''}`}
  catch(e){$('issue-rows').innerHTML=`<tr><td colspan="6" class="empty">${M.esc(problem(e))}</td></tr>`}
 }
 async function load({cursor=null,push=false}={}){
  $('list-status').textContent='Loading…';
  try{
   const page=await api.request('GET','/v1/ops/client-errors',{query:M.query(form(),{limit:Number($('limit').value)||50,cursor})});
   if(push)S.stack.push(S.cursor);S.cursor=cursor;S.items=page.items;S.next=page.next_cursor;
   $('rows').innerHTML=M.rows(page.items);
   $('list-status').textContent=`${page.items.length} record${page.items.length===1?'':'s'}${page.next_cursor?' · more':''}`;
   $('older').disabled=!page.next_cursor;$('newer').disabled=!S.stack.length;
  }catch(e){$('rows').innerHTML=`<tr><td colspan="6" class="empty">${M.esc(problem(e))}</td></tr>`;$('list-status').textContent='Could not load.'}
  if(!cursor)loadIssues();
 }
 async function copyText(text,note){
  try{await navigator.clipboard.writeText(text);$('detail').querySelector('[data-copied]').textContent=note}
  catch{const ta=document.createElement('textarea');ta.value=text;document.body.append(ta);ta.select();try{document.execCommand('copy');$('detail').querySelector('[data-copied]').textContent=note}catch{$('detail').querySelector('[data-copied]').textContent='Copy failed: select the text below.'}ta.remove()}
 }
 async function setStatus(form){
  const fingerprint=form.dataset.fingerprint,status=form.status.value,note=form.note.value.trim()||null,button=form.querySelector('button');
  button.disabled=true;
  try{await api.request('PUT',`/v1/ops/client-errors/issues/${encodeURIComponent(fingerprint)}/status`,{body:{status,note}});await loadIssues();if(S.current)await show(S.current.id,{quiet:true});await load({cursor:S.cursor})}
  catch(e){alert(problem(e))}
  finally{button.disabled=false}
 }
 // Open one of the firm's files: the reason is recorded on the study's history (the firm sees
 // it); the file is shown in the page with a Download link (M.viewer).
 let fileUrl=null;
 function closeFile(view){if(fileUrl){URL.revokeObjectURL(fileUrl);fileUrl=null}if(view){view.hidden=true;view.innerHTML=''}}
 async function openFile(r,button){
  const reason=prompt("Why are you opening this file? It is recorded on the study's history, which the firm sees.",M.accessReason(r));
  if(reason===null)return;
  const view=$('file-view'),doc=(r.event?.documents||[]).find(d=>d.document_id===button.dataset.open)||{};
  button.disabled=true;
  try{
   const response=await api.request('GET',M.filePath(r.study_id,button.dataset.open),{query:{reason:reason.trim(),client_error_id:r.id},raw:true});
   const blob=await response.blob();closeFile(view);fileUrl=URL.createObjectURL(blob);
   view.innerHTML=M.viewer(doc.filename||'file',response.headers.get('content-type')||blob.type,fileUrl);view.hidden=false;
   view.querySelector('[data-close-file]').onclick=()=>closeFile(view);
   view.scrollIntoView({block:'start'});
  }catch(e){alert(e?.status===422?'Give a reason of at least 3 characters.':problem(e))}
  finally{button.disabled=false}
 }
 async function show(id,{quiet=false}={}){
  if(!quiet){$('detail').innerHTML='<p class="muted">Loading the record…</p>';$('detail').hidden=false}
  try{
   const r=await api.request('GET',`/v1/ops/client-errors/${encodeURIComponent(id)}`);
   const issue=await api.request('GET',`/v1/ops/client-errors/issues/${encodeURIComponent(r.fingerprint)}`).catch(()=>null);
   S.current=r;
   $('detail').innerHTML=`<button type="button" id="close" class="quiet">Close</button>`+M.detail(r,issue);
   $('close').onclick=()=>{closeFile(null);$('detail').hidden=true;S.current=null;history.replaceState(null,'',location.pathname+location.search)};
   $('detail').querySelector('[data-copy="brief"]').onclick=()=>copyText(M.brief(r,issue),'Copied the brief: paste it into Claude Code or Codex.');
   $('detail').querySelector('[data-copy="json"]').onclick=()=>copyText(JSON.stringify({record:r,issue},null,2),'Copied the record as JSON.');
   for(const b of $('detail').querySelectorAll('[data-open]'))b.onclick=()=>openFile(r,b);
   if(!quiet)$('detail').scrollIntoView({block:'start'});
  }catch(e){$('detail').innerHTML=`<p class="error">${M.esc(problem(e))}</p>`}
 }
 $('filters').addEventListener('submit',e=>{e.preventDefault();S.stack=[];load()});
 $('reset').addEventListener('click',()=>{for(const k of [...M.FILTERS,'fingerprint'])$(k).value='';S.stack=[];load()});
 $('older').addEventListener('click',()=>load({cursor:S.next,push:true}));
 $('newer').addEventListener('click',()=>load({cursor:S.stack.pop()||null}));
 $('rows').addEventListener('click',e=>{const tr=e.target.closest('tr[data-id]');if(!tr)return;e.preventDefault();location.hash=tr.dataset.id;show(tr.dataset.id)});
 // An issue's row filters the records to its fingerprint; its status form saves.
 $('issue-rows').addEventListener('click',e=>{if(e.target.closest('form'))return;const tr=e.target.closest('tr[data-fingerprint]');if(!tr)return;e.preventDefault();$('fingerprint').value=tr.dataset.fingerprint;S.stack=[];load()});
 document.addEventListener('submit',e=>{const form=e.target.closest('form.status-form');if(!form)return;e.preventDefault();setStatus(form)});
 window.addEventListener('hashchange',()=>{const id=location.hash.slice(1);if(id&&!id.startsWith('issue/'))show(id)});
 (async()=>{
  try{await BasisAuth.init(C.firebase)}catch(e){$('list-status').textContent='Sign-in is unavailable: '+e.message;return}
  if(!BasisAuth.user){$('list-status').textContent='Sign in to Basis first, then reload this page.';$('rows').innerHTML='<tr><td colspan="6" class="empty">Not signed in.</td></tr>';return}
  $('who').textContent=BasisAuth.user.email||BasisAuth.user.uid;
  await load();
  const id=location.hash.slice(1);if(id&&!id.startsWith('issue/'))show(id);
 })();
})();
