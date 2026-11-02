(function(scope){
 'use strict';
 const catalogue=typeof module==='object'&&module.exports?require('./error-catalogue.js'):scope.BasisErrorCatalogue;
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function explain(code,message=''){
  const key=code||String(message).match(/BASIS-[A-Z]+-\d{3}/)?.[0],entry=catalogue[key];
  return {code:key||null,known:!!entry,what:entry?.what||message||'No failure detail was recorded.',fix:entry?.fix||'Review the recorded detail. If the issue persists, share the code and request ID with your administrator.',retry:entry?.retry||'unknown'};
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
 function failureState(study,progress={},memberships=[]){
  const terminal=['completed','archived'].includes(study.status),failed=!terminal&&(study.status==='failed'||!!progress.failed_stage),stalled=!terminal&&(study.status==='stalled'||!!progress.stalled);
  return {attention:failed||stalled,canRetry:failed&&memberships.some(m=>m.org_id===study.org_id&&['owner','engineer'].includes(m.role))};
 }
 const api={explain,html,reviews,failureState};if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisErrorHelp=api;
})(globalThis);
