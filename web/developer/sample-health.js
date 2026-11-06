/* The developer page on synthetic health, with no API: developer/?api=sample[&health=<scenario>].
 *
 * Scenarios, as in tests/fixtures/developer-health.html: partial (default: Temporal and the photo
 * localizer down), healthy, stale (answers three minutes old), missing (no operator route),
 * forbidden (not an operator), offline (the API unreachable). Only GET /health and
 * GET /v1/ops/health are answered; any other request fails as the fixture's does. Active only
 * with ?api=sample: otherwise this file does nothing and the page talks to its API.
 */
(function(scope){
 'use strict';
 const q=new URLSearchParams(scope.location?.search||'');if(q.get('api')!=='sample')return;
 const SCENARIOS=['partial','healthy','stale','missing','forbidden','offline'];
 const scenario=SCENARIOS.includes(q.get('health'))?q.get('health'):'partial';
 const BASE='https://sample.basis.invalid';
 // The page sends its token only to the trusted API origin; the sample is that origin here.
 scope.BASIS_CONFIG.apiBase=BASE;scope.BASIS_CONFIG_OVERRIDES={...(scope.BASIS_CONFIG_OVERRIDES||{}),apiBase:BASE};
 scope.BasisAuth={user:{uid:'sample-operator',email:'operator@example.test'},init:async()=>{},getToken:async()=>'sample-token',onChange(){return ()=>{}},signOut:async()=>{}};
 const real=scope.fetch?.bind(scope);
 scope.fetch=async(url,options)=>{
  const u=new URL(String(url),scope.location.href);
  if(u.origin!==BASE)return real(url,options);// the page's own files (logos, workflow content)
  if(scenario==='offline')throw new TypeError('Synthetic network failure');
  const time=new Date(Date.now()-(scenario==='stale'?180000:0)).toISOString();
  const json=(status,body)=>new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json'}});
  if(u.pathname==='/health')return json(200,{status:'ok',service:'basis',env:'sample',version:'sample',git_sha:'sample',checks:{db:{status:'ok',latency_ms:2},temporal:{status:scenario==='partial'?'fail':'ok',latency_ms:6}}});
  if(u.pathname==='/v1/ops/health'){
   if(scenario==='missing'||scenario==='forbidden')return json(scenario==='missing'?404:403,{});
   const disabled=['azure_openai','mask2former'];
   return json(scenario==='partial'?503:200,{schema_version:1,services:scope.BasisHealth.SERVICES.map(s=>({id:s.id,status:disabled.includes(s.id)?'disabled':scenario==='partial'&&['replicate','sam3','vision'].includes(s.id)?'down':'healthy',checked_at:time,check_kind:s.id==='worker'||s.id==='relay'?'heartbeat':'probe',latency_ms:s.id==='sam3'?84:12,required:false,detail:s.id==='sam3'&&scenario==='partial'?'Synthetic: the configured localizer did not respond.':'Synthetic readiness evidence. No real service was contacted.',model:['openai','vision','appraisal'].includes(s.id)?'Synthetic GPT deployment':s.id==='sam3'?'SAM 3':'',adapter:s.id==='sam3'?'replicate':'',hosting:s.id==='temporal'?'Synthetic Docker':undefined,fallbacks:s.id==='sam3'?[]:undefined}))});
  }
  return json(404,{type:'https://basis.invalid/problems/not-found',title:'Not Found',status:404,detail:'The sample answers /health and /v1/ops/health only.'});
 };
})(typeof globalThis==='object'?globalThis:this);
