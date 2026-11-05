(function(scope){
 'use strict';
 const catalogue=typeof module==='object'&&module.exports?require('./error-catalogue.js'):scope.BasisErrorCatalogue;
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 // Engineer-facing fixes the app can offer today, where the generated catalogue's text names a
 // route the API does not have yet (web/FRONTEND_BACKLOG.md section 13). The catalogue is unchanged.
 // An empty string means the reason's own sentence already says what to do.
 const FIX={'BASIS-ASSETS-005':'','BASIS-ASSETS-007':'','BASIS-ASSETS-010':'','BASIS-ASSETS-008':'Confirm the classification against the guidance yourself.','BASIS-CHECK-012':'Correct the asset’s category to one the firm’s cost table prices, or add a row for it to the cost table. A price override is not available yet.'};
 function explain(code,message=''){
  const key=code||String(message).match(/BASIS-[A-Z]+-\d{3}/)?.[0],entry=catalogue[key];
  return {code:key||null,known:!!entry,what:entry?.what||message||'No failure detail was recorded.',fix:FIX[key]||entry?.fix||'Review the recorded detail. If the issue persists, share the code and request ID with your administrator.',retry:entry?.retry||'unknown'};
 }
 function html(code,message=''){
  const e=explain(code,message);
  return `<div class="error-guidance"><p><strong>${esc(e.what)}</strong></p><p>${esc(e.fix)}</p>${e.code?`<code>${esc(e.code)}</code>`:''}${message&&e.known?`<details><summary>Recorded detail</summary><p>${esc(message)}</p></details>`:''}</div>`;
 }
 function reviews(reasons=[],codes=[]){
  // review_codes is deduplicated and skips unknown reasons; it is NOT parallel to reasons.
  const known=[...new Set(codes)].filter(c=>catalogue[c]);
  const hints=known.map(c=>`<li>${html(c)}</li>`);
  const details=scope.BasisReasons?scope.BasisReasons.items(reasons):reasons.map(r=>`<li>${esc(r)}</li>`).join('');
  return hints.join('')+(details?`<li><details><summary>Recorded review reasons</summary><ul>${details}</ul></details></li>`:'');
 }
 // An asset's flags, one row per recorded reason: what it says, how to fix it (from the code
 // whose signal matches the reason), and the code. Codes no reason matches get their own row.
 const fixFor=e=>e&&e.fix&&!/^Read the reason\.?$/.test(e.fix)?e.fix:'';
 function matches(signal,raw){return !!signal&&(raw===signal||(signal.endsWith(':')?raw.startsWith(signal):raw.endsWith(':'+signal)))}
 function flags(reasons=[],codes=[]){
  const R=scope.BasisReasons;if(!R)return reviews(reasons,codes);
  const known=[...new Set(codes)].filter(c=>catalogue[c]),used=new Set();
  const rows=R.explainAll(reasons).map(r=>{
   const code=known.find(c=>matches(catalogue[c].signal,r.code));if(code)used.add(code);
   const text=r.kind==='model'?(r.text?`The classification model asked for review: “${r.text}”`:'The classification model asked for review.'):r.text;
   return {text,fix:code?fixFor({fix:code in FIX?FIX[code]:catalogue[code].fix}):'',code};
  });
  for(const c of known)if(!used.has(c))rows.push({text:catalogue[c].what,fix:fixFor({fix:c in FIX?FIX[c]:catalogue[c].fix}),code:c});
  return rows.map(r=>`<li class="flag-row"><p>${esc(r.text)}</p>${r.fix?`<p class="flag-fix">${esc(r.fix)}</p>`:''}${r.code?`<code>${esc(r.code)}</code>`:''}</li>`).join('');
 }
 function failureState(study,progress={},memberships=[]){
  const terminal=['completed','archived'].includes(study.status),failed=!terminal&&(study.status==='failed'||!!progress.failed_stage),stalled=!terminal&&(study.status==='stalled'||!!progress.stalled);
  return {attention:failed||stalled,canRetry:failed&&memberships.some(m=>m.org_id===study.org_id&&['owner','engineer'].includes(m.role))};
 }
 const api={explain,html,reviews,flags,failureState};if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisErrorHelp=api;
})(globalThis);
