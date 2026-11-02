/* The operator's client-errors page: GET /v1/ops/client-errors (the list, with filters and
 * paging) and GET /v1/ops/client-errors/{id} (one record with the whole event). Platform
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
 const S={cursor:null,stack:[],items:[]};
 const problem=e=>e?.status===403?'This account is not a platform operator (OPS_OPERATOR_UIDS).':e?.status===401?'Sign in to Basis first, then reload this page.':e?.message||String(e);
 function form(){const f={};for(const k of M.FILTERS)f[k]=$(k).value;return f}
 async function load({cursor=null,push=false}={}){
  $('status').textContent='Loading…';
  try{
   const page=await api.request('GET','/v1/ops/client-errors',{query:M.query(form(),{limit:Number($('limit').value)||50,cursor})});
   if(push)S.stack.push(S.cursor);S.cursor=cursor;S.items=page.items;S.next=page.next_cursor;
   $('rows').innerHTML=M.rows(page.items);
   $('status').textContent=`${page.items.length} record${page.items.length===1?'':'s'}${page.next_cursor?' · more':''}`;
   $('older').disabled=!page.next_cursor;$('newer').disabled=!S.stack.length;
  }catch(e){$('rows').innerHTML=`<tr><td colspan="6" class="empty">${M.esc(problem(e))}</td></tr>`;$('status').textContent='Could not load.'}
 }
 async function show(id){
  $('detail').innerHTML='<p class="muted">Loading the record…</p>';$('detail').hidden=false;
  try{const r=await api.request('GET',`/v1/ops/client-errors/${encodeURIComponent(id)}`);$('detail').innerHTML=`<button type="button" id="close" class="quiet">Close</button>`+M.detail(r);$('close').onclick=()=>{$('detail').hidden=true;history.replaceState(null,'',location.pathname+location.search)};$('detail').scrollIntoView({block:'start'})}
  catch(e){$('detail').innerHTML=`<p class="error">${M.esc(problem(e))}</p>`}
 }
 $('filters').addEventListener('submit',e=>{e.preventDefault();S.stack=[];load()});
 $('reset').addEventListener('click',()=>{for(const k of M.FILTERS)$(k).value='';S.stack=[];load()});
 $('older').addEventListener('click',()=>load({cursor:S.next,push:true}));
 $('newer').addEventListener('click',()=>load({cursor:S.stack.pop()||null}));
 $('rows').addEventListener('click',e=>{const tr=e.target.closest('tr[data-id]');if(!tr)return;e.preventDefault();location.hash=tr.dataset.id;show(tr.dataset.id)});
 window.addEventListener('hashchange',()=>{const id=location.hash.slice(1);if(id)show(id)});
 (async()=>{
  try{await BasisAuth.init(C.firebase)}catch(e){$('status').textContent='Sign-in is unavailable: '+e.message;return}
  if(!BasisAuth.user){$('status').textContent='Sign in to Basis first, then reload this page.';$('rows').innerHTML='<tr><td colspan="6" class="empty">Not signed in.</td></tr>';return}
  $('who').textContent=BasisAuth.user.email||BasisAuth.user.uid;
  await load();
  const id=location.hash.slice(1);if(id)show(id);
 })();
})();
