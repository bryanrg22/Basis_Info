(function(){
 'use strict';
 const H=BasisHealth,$=id=>document.getElementById(id),C=BASIS_CONFIG;
 let basic={error:'pending'},extended=null,receivedAt=null,connected=false,busy=false,refreshPending=false,stopped=false,request=null;
 let client;
 try{client=BasisHealthClient.createClient({base:C.apiBase,trustedOrigin:globalThis.BASIS_CONFIG_OVERRIDES?.apiBase||'http://127.0.0.1:8000',getToken:()=>BasisAuth.getToken()});$('api-origin').textContent=BasisHealthClient.baseUrl(C.apiBase)}
 catch(error){$('overall').textContent='Invalid API address';$('counts').textContent=error.message;$('refresh').disabled=true;return}
 const back=new URL('../',document.baseURI);back.search=location.search;back.searchParams.set('mode','live');back.hash='dashboard';$('app-link').href=back.toString();
 let services=H.SERVICES.map(s=>({...s,...H.normalize()}));
 function redraw(){
  const opened=new Set([...document.querySelectorAll('.service[open]')].map(el=>el.id));
  const focus=document.activeElement,focusService=focus?.closest?.('.service')?.id,dependency=focus?.dataset?.service,step=focus?.dataset?.step;
  if(receivedAt!==null)services=H.snapshot(basic,extended,receivedAt).map(s=>({...s,...H.normalize(s,Date.now())}));
  $('service-groups').innerHTML=H.renderServices(services,{query:$('search').value,attention:$('attention').checked});
  for(const id of opened)if($(id))$(id).open=true;
  $('flow').innerHTML=H.renderFlow(services);
  drawConnections();updateFlowControls();
  if(focusService)$(focusService)?.querySelector('summary')?.focus({preventScroll:true});
  if(dependency)document.querySelector(`[data-service="${dependency}"]`)?.focus({preventScroll:true});
  if(step)document.querySelector(`[data-step="${step}"]`)?.focus({preventScroll:true});
  const overall=H.aggregate(services);
  $('overall').textContent={healthy:'Healthy',down:'Failed checks',degraded:'Degraded checks',stale:'Stale checks',unknown:'Not fully checked'}[overall]||'Not fully checked';
  $('counts').innerHTML=H.renderCounts(services);
  $('flow-legend').innerHTML=H.renderLegend();
 }
 function drawConnections(){
  const flow=$('flow'),box=flow.getBoundingClientRect(),svg=flow.querySelector('svg');if(!svg)return;
  svg.setAttribute('viewBox',`0 0 ${box.width} ${box.height}`);
  const relative=el=>{const r=el.getBoundingClientRect();return {left:r.left-box.left,right:r.right-box.left,top:r.top-box.top,bottom:r.bottom-box.top,width:r.width,height:r.height}};
  svg.querySelector('g').innerHTML=H.EDGES.map(([from,to])=>{
   const a=relative(flow.querySelector(`[data-node="${from}"]`)),b=relative(flow.querySelector(`[data-node="${to}"]`));
   const path=H.connectionPath(from,to,a,b);
   return `<path class="flow-wire-halo" d="${path}"/><path class="flow-wire" d="${path}" marker-end="url(#flow-arrow)"/>`;
  }).join('');
 }
 new ResizeObserver(drawConnections).observe($('flow-scroll'));
 function feedMessage(result){
  if(result.error==='untrusted_origin')return 'Authenticated diagnostics require a trusted API origin in deployment configuration. A URL override can read public health only.';
  if(result.error==='auth')return 'The sign-in service could not refresh your session. Check Firebase, then reconnect diagnostics.';
  if(result.error)return 'Diagnostics unavailable. Retry when the API is reachable.';
  if(result.status===401)return 'Sign in to Basis, then connect again. Developer diagnostics require an authenticated session.';
  if(result.status===403)return 'This account cannot access developer diagnostics. The backend must grant operator access.';
  if(result.status===404)return 'Diagnostics endpoint unavailable.';
  if(result.status===429)return 'Diagnostics are rate limited. Wait before refreshing; no provider checks were started by this page.';
  if(![200,503].includes(result.status))return `Diagnostics returned HTTP ${result.status}. Extended checks are unavailable.`;
  if(result.data?.schema_version!==1||!Array.isArray(result.data.services))return 'The diagnostics response does not match the supported contract. Extended checks remain unknown.';
  return 'Connected';
 }
 async function refresh(){
  if(document.hidden||stopped)return;
  if(busy){refreshPending=true;return}
  busy=true;request=new AbortController();$('refresh').disabled=true;$('refresh').textContent='Checking…';
  try{
   const [health,details]=await Promise.all([client.health({signal:request.signal}),connected?client.diagnostics({signal:request.signal}):Promise.resolve(null)]);
   if(stopped)return;
   basic=health;receivedAt=Date.now();
   // A failed feed clears previously green provider checks, rather than leaving false confidence.
   extended=details&&[200,503].includes(details.status)&&details.data?.schema_version===1?details.data:null;
   if(details)$('feed-message').textContent=feedMessage(details);
   if(health.data?.service==='basis')$('build').textContent=[health.data.env,health.data.version,health.data.git_sha&&health.data.git_sha!=='unknown'?health.data.git_sha.slice(0,8):''].filter(Boolean).join(' · ');else $('build').textContent='';
   $('last-refresh').textContent=`Last refresh ${H.time(new Date(receivedAt).toISOString())}`;
   redraw();
  }finally{busy=false;$('refresh').disabled=false;$('refresh').textContent='Refresh checks';if(refreshPending){refreshPending=false;queueMicrotask(refresh)}}
 }
 $('refresh').addEventListener('click',refresh);
 $('search').addEventListener('input',redraw);$('attention').addEventListener('change',redraw);
 $('connect').addEventListener('click',async()=>{
  $('connect').disabled=true;$('connect').textContent='Connecting…';
  try{
   // Existing same-origin Firebase session only; provider keys never enter this page.
   await Promise.race([BasisAuth.init(C.firebase),new Promise((_,reject)=>setTimeout(()=>reject(new Error('Sign-in did not respond. Check the Firebase service, then reload this page.')),10000))]);
   if(!BasisAuth.user){$('feed-message').textContent='Sign in through Basis first, then connect again. The Basis link above returns to the app.';return}
   connected=true;$('feed-message').textContent='Reading authenticated diagnostics…';await refresh();
  }catch(error){$('feed-message').textContent=error.message||'Could not connect to sign-in.'}
  finally{$('connect').disabled=false;$('connect').textContent=connected?'Reconnect diagnostics':'Connect diagnostics'}
 });
 let activeStep=null;
 // The explanations and measured results (workflow-content.json) load once; a dialog opened
 // before they arrive shows the runtime table and says the content is not loaded.
 const contentLoaded=BasisArchitecture.load().then(ok=>{if(activeStep&&$('architecture-dialog').open)renderStep(activeStep);return ok});
 function renderStep(id){
  const node=H.FLOW.flat().find(n=>n.id===id);if(!node)return;
  $('architecture-title').textContent=node.title;
  $('architecture-content').innerHTML=BasisArchitecture.render(node,services,H);
 }
 function openStep(id){
  const node=H.FLOW.flat().find(n=>n.id===id);if(!node)return;
  activeStep=id;renderStep(id);
  $('architecture-dialog').showModal();$('architecture-dialog').scrollTop=0;
 }
 window.addEventListener('load',()=>contentLoaded.catch(()=>{}));
 $('close-architecture').addEventListener('click',()=>$('architecture-dialog').close());
 $('architecture-dialog').addEventListener('close',()=>document.querySelector(`[data-step="${activeStep}"]`)?.focus({preventScroll:true}));
 $('flow').addEventListener('click',event=>{
  const button=event.target.closest('[data-service]');
  if(!button){const node=event.target.closest('[data-node]');if(node)openStep(node.dataset.node);return}
  $('search').value='';$('attention').checked=false;redraw();
  const row=$('service-'+button.dataset.service);if(row){row.open=true;row.scrollIntoView({block:'center',behavior:'instant'});row.querySelector('summary').focus({preventScroll:true})}
 });
 for(const [id,direction] of [['flow-left',-1],['flow-right',1]])$(id).addEventListener('click',()=>{const column=$('flow').querySelector('.flow-column');const distance=column.getBoundingClientRect().width+parseFloat(getComputedStyle($('flow')).columnGap);$('flow-scroll').scrollBy({left:direction*distance,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'})});
 document.addEventListener('visibilitychange',()=>{if(!document.hidden){redraw();if($('auto-refresh').checked)refresh()}else $('last-refresh').textContent='Auto-refresh pauses while this tab is hidden.'});
 $('flow-scroll').addEventListener('scroll',updateFlowControls,{passive:true});
 function updateFlowControls(){const el=$('flow-scroll');$('flow-left').disabled=el.scrollLeft<2;$('flow-right').disabled=el.scrollLeft+el.clientWidth>=el.scrollWidth-2}
 new ResizeObserver(updateFlowControls).observe($('flow-scroll'));
 const poll=setInterval(()=>{if($('auto-refresh').checked)refresh()},30000);
 const age=setInterval(()=>{if(!document.hidden&&!busy)redraw()},15000);
 window.addEventListener('pagehide',()=>{stopped=true;request?.abort();clearInterval(poll);clearInterval(age)});
 window.addEventListener('pageshow',event=>{if(event.persisted)location.reload()});
 redraw();refresh();
})();
