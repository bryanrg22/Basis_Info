/* Live mode: change an approved step of a completed study (POST .../reopen, docs/api/REVISIONS.md).
 *
 * A completed study's review pages are read-only. "Change this step" asks the API for the plan
 * first (dry_run: nothing is written), shows exactly which steps reopen and what is redone, and
 * reopens only when the engineer confirms. Reopening starts a new revision: the approved revision,
 * its report and its signature stay as they were. An in-progress study has no such route (the API
 * reopens completed studies only), so the button is shown on completed studies alone.
 */
(function(){
 'use strict';
 const C=globalThis.BASIS_CONFIG;if(!C||C.mode!=='live')return;
 const Live=globalThis.BasisLive,L=Live.state,api=Live.api,G=globalThis.BasisGates,R=globalThis.BasisReport;
 const NAME={appraisal_review:'Appraisal',rooms_review:'Rooms',components_review:'Components',assets_review:'Assets'};
 const REDONE={components_processing:'room component counts',assets_processing:'assets and costs',exporting:'the report'};
 // The review gate a screen shows, for a study that has it.
 function gateFor(study,view){
  const stage={appraisal:'appraisal_review',rooms:'rooms_review',components:G.editStage(study),assets:'assets_review','asset-detail':'assets_review'}[view];
  return stage&&NAME[stage]?stage:null;
 }
 const role=s=>((Live.me?.()?.memberships)||[]).find(m=>m.org_id===s.org_id)?.role||null;
 Live.canReopen=(s,view)=>Boolean(s&&s.status==='completed'&&gateFor(s,view)&&(R?.RANK?.[role(s)]??-1)>=(R?.RANK?.engineer??1));
 Live.reopenButton=(s,view)=>Live.canReopen(s,view)?`<button class="button study-status-action" data-live="reopen-gate" data-value="${gateFor(s,view)}">${icon('edit',15)}Change ${esc(NAME[gateFor(s,view)])}</button>`:'';
 const list=items=>items.length>1?`${items.slice(0,-1).join(', ')} and ${items.at(-1)}`:items[0]||'';

 Live.actions['reopen-gate']=(el,stage)=>Live.guarded('reopen-gate',async()=>{
  const s=L.study;if(!NAME[stage])return;
  const plan=await api.request('POST',`/v1/studies/${encodeURIComponent(s.id)}/reopen`,{body:{stage,dry_run:true}});
  const reviews=(plan.reviews||[]).map(x=>NAME[x]).filter(Boolean),redone=(plan.recomputed||[]).map(x=>REDONE[x]).filter(Boolean);
  const m=modal(`Change ${esc(NAME[stage])}?`,`<div class="reopen-plan"><p>This starts revision ${plan.revision} at ${esc(NAME[stage])}, with everything as you approved it in revision ${plan.from_revision}.</p><dl><div><dt>Reopens for your review</dt><dd>${reviews.map(r=>`<span class="reopen-step">${esc(r)}</span>`).join('')}</dd></div>${redone.length?`<div><dt>Redone after you approve</dt><dd>${esc(list(redone))}</dd></div>`:''}<div><dt>Report</dt><dd>Revision ${plan.revision} gets a new report, signed on its own. Revision ${plan.from_revision} and its signed report stay unchanged.</dd></div></dl></div>`,`<button class="button" data-close>Cancel</button><button class="button primary" id="live-reopen-gate-go">Reopen ${esc(NAME[stage])}</button>`);
  m.querySelector('#live-reopen-gate-go').onclick=()=>Live.guarded('reopen-gate-go',async()=>{
   let started;
   try{started=await api.request('POST',`/v1/studies/${encodeURIComponent(s.id)}/reopen`,{body:{stage}})}
   catch(e){m.remove();Live.invalidate('study:'+s.id);Live.render();if(e.status===409){toast(e.kind==='workflow-running'?'This study is still processing. Try again in a moment.':e.kind==='study-archived'?'The study is archived: unarchive it first.':'The study is no longer completed. It has been reloaded.');return}throw e}
   m.remove();toast(`Revision ${started.revision} started at ${NAME[stage]}.`);
   Live.invalidate('studies');Live.invalidate('revisions:'+s.id);Live.invalidate('report-state:'+s.id);L.reportPreview=null;
   // Study progress polls while the rows are copied, then forwards to the reopened review.
   L.reopenTarget={studyId:s.id,stage};
   await Live.reloadStudy();Live.go('processing',s.id);
  });
 });
 Live.render();
})();
