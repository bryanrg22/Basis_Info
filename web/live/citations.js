/* Live mode: the rule citations on the asset page (GET /v1/studies/{id}/evidence).
 *
 * A citation is an excerpt of a reference-corpus chunk: source.doc_id is the ingested
 * file's stem (docs/deploy/STAGING.md 1.8), pages are PDF pages of that file, section_path
 * is the chunk's heading path (" > " separated). The asset stage stores only citations
 * that passed verification (basis.classify.verify drops the rest and flags the asset
 * citation_unverified), and verification_method says how: "<path>:<checks>", path being
 * rule, cache or llm (basis.classify.router).
 */
(function(scope){
 'use strict';
 const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 // Reference corpus v1, titles as printed on each document.
 const DOCS={
  IRS_IRS_COST_SEG_ATG__2024:'IRS Cost Segregation Audit Techniques Guide (Pub. 5653)',
  IRS_IRS_PUB_527__2024:'IRS Publication 527 (2024), Residential Rental Property',
  IRS_IRS_PUB_946__2024:'IRS Publication 946 (2024), How To Depreciate Property',
  IRS_REV_PROC_87_56:'Revenue Procedure 87-56',
  RSMEANS_RSMEANS_BUILDING_2020:'Building Construction Costs with RSMeans data (2020)',
  RSMEANS_RSMEANS_RESIDENTIAL_2020:'Residential Costs with RSMeans data (2020)',
 };
 // An unknown id (a document added after v1) still reads as words: IRS_PUB_535 -> "IRS PUB 535".
 const docTitle=id=>id?DOCS[id]||String(id).replace(/_+/g,' ').trim():'Reference';
 const pageLabel=(start,end)=>{
  if(!Number.isInteger(start))return '';
  return Number.isInteger(end)&&end>start?`pp. ${start}–${end}`:`p. ${start}`;
 };
 const PATHS={rule:'Classification rule',llm:'Model citation',cache:'Cached classification'};
 // Every check name the backend writes; the excerpt test is the one an engineer acts on.
 const CHECKS={resolved:'the cited passage exists in the reference corpus',retrieved:'the model retrieved it during this classification',normalized_substring:'the excerpt appears word for word in it',substring:'the excerpt appears word for word in it'};
 function method(value){
  if(!value)return {path:'',checks:[]};
  const [path,rest]=value.includes(':')?value.split(/:(.*)/s):['',value];
  const checks=[...new Set((rest||'').split('+').filter(Boolean).map(c=>CHECKS[c]||c.replace(/_/g,' ')))];
  return {path:PATHS[path]||path.replace(/_/g,' '),checks};
 }
 function card(ci){
  const src=ci.source||{},m=method(ci.verification_method),pages=pageLabel(src.page_start,src.page_end);
  const title=ci.verified?`Verified${m.checks.length?': '+m.checks.join('; '):''}${ci.verification_method?` (${ci.verification_method})`:''}`:'Not verified against the reference text';
  return `<article class="live-citation"><div class="row between"><div><strong class="small">${esc(docTitle(src.doc_id))}${pages?' · '+esc(pages):''}</strong>${src.section_path?`<p class="small muted live-citation-section">${esc(src.section_path)}</p>`:''}</div><span class="badge ${ci.verified?'complete':'review'}" title="${esc(title)}">${ci.verified?'Verified':'Not verified'}</span></div><blockquote>${esc(ci.excerpt)}</blockquote>${m.path?`<p class="small muted">${esc(m.path)}</p>`:''}</article>`;
 }
 // citation_unverified: a citation was dropped (unresolved, out of scope, or its excerpt not
 // in the passage), or the model path ended with none verified (basis.classify.router).
 const unverified=reasons=>(reasons||[]).includes('citation_unverified');
 function list(citations,reviewReasons){
  if(!citations?.length)return unverified(reviewReasons)
   ?'<p class="small">No verified citation supports this classification. Basis keeps only citations whose excerpt it finds in the reference text. Check the classification against the guidance before approving it.</p>'
   :'<p class="small">No citations for this asset.</p>';
  return `${unverified(reviewReasons)?'<p class="small">Some proposed citations did not match the reference text and were dropped. The ones below matched.</p>':''}<div class="live-citations">${citations.map(card).join('')}</div>`;
 }
 const api={docTitle,pageLabel,method,card,list};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisCitations=api;
})(typeof globalThis==='object'?globalThis:this);
