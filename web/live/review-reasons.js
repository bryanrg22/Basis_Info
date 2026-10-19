/* Live mode: an asset's review_reasons (GET /v1/studies/{id}/assets) as sentences.
 *
 * The backend stores the reasons as one "; "-joined string (assets.review_reason) and the
 * API splits it back on "; " (basis.api.routes.outputs), so a model's own text that contains
 * "; " arrives as several items. Codes never contain whitespace; free text always does, so
 * an item with whitespace after a model: item is the rest of that text and is joined back.
 *
 * Every code the backend writes, by where it comes from:
 *   basis.classify.router      citation_unverified, missing_evidence, rule_contested:<label>,
 *                              model:<the model's text, cut at 300 characters>,
 *                              classifier_unavailable, and the residual loop's failures
 *                              (basis.classify.residual): no_answer, tool_cap_exceeded,
 *                              invalid_output, iteration_cap, timeout, budget_exceeded,
 *                              provider_error:<exception class>
 *   basis.domain.validators    validation:<code>, warnings and errors only
 *   basis.domain.cross_validation  cross:<code>, warnings and errors only
 *                              (basis.activities.process_assets)
 * Info-severity codes are never written; they are listed so a change there still reads well.
 */
(function(scope){
 'use strict';
 const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const words=s=>String(s).replace(/_+/g,' ').trim();
 const sentence=s=>{const w=words(s);return w?w[0].toUpperCase()+w.slice(1):w};
 // The fallback (basis.classify.router._fallback): no usable answer, so the asset is put in
 // the property class's building bucket (no accelerated depreciation) with confidence 0.
 const FALLBACK='The asset was put in the building class, with no accelerated depreciation, until you classify it.';
 const fallback=what=>`The classification model ${what}. ${FALLBACK}`;
 const PLAIN={
  citation_unverified:'Some of its citations, or all of them, could not be found in the reference documents. Check the classification against the guidance.',
  missing_evidence:'The classification model answered without searching the reference documents, so nothing in them backs its answer.',
  classifier_unavailable:`The classification model was not available. ${FALLBACK}`,
  no_answer:fallback('gave no answer'),
  tool_cap_exceeded:fallback('kept searching past its lookup limit without answering'),
  invalid_output:fallback('did not return a valid answer'),
  iteration_cap:fallback('reached its step limit without answering'),
  timeout:fallback('timed out'),
  budget_exceeded:fallback('stopped at the study’s model budget'),
 };
 const CHECKS={
  // validation: (basis.domain.validators)
  LAND_NOT_DEPRECIABLE:'It is classified as land, which is not depreciable.',
  SECTION_MISSING:'No IRS section is recorded.',
  SECTION_UNKNOWN:'The IRS section is neither 1245 nor 1250.',
  SECTION_BUCKET_MISMATCH:'The recovery period does not fit the IRS section: section 1245 takes 5, 7 or 15 years, section 1250 takes 15, 27.5 or 39.',
  RECOVERY_PERIOD_MISMATCH:'The recovery period in years does not match its class.',
  BUILDING_BUCKET_PROPERTY_CLASS:'The building class does not match the property: 27.5 years for residential rental, 39 for nonresidential.',
  STRUCTURAL_AS_PERSONAL:'It looks structural but is classified as section 1245 personal property.',
  PERSONAL_AS_BUILDING:'It looks like personal property but is classified as part of the building.',
  ROOM_CONTEXT:'Items in a mechanical room are often section 1245 equipment.',
  // cross: (basis.domain.cross_validation)
  SECTION_1245_SF_LARGE:'It is section 1245 but measured as more than 100 SF. Section 1245 property is usually equipment or removable finishes, not large areas.',
  COST_NEEDS_QUANTITY:'The cost table prices it by area or length, but the takeoff only counts it, so it has no cost yet. Measure it to price it.',
  COST_ROW_MISSING:'No cost table row matches it, so its cost needs a manual estimate.',
  COST_BELOW_RANGE:'Its unit cost is far below the typical range for this component.',
  COST_ABOVE_RANGE:'Its unit cost is far above the typical range for this component.',
  COST_TOTAL_MISMATCH:'Its total cost is not the unit cost times the quantity.',
  COST_MULTIPLIER_DETECTED:'Its unit cost includes a large year or location adjustment.',
  UNIT_BUCKET_MISMATCH:'Its takeoff unit is unusual for its recovery class.',
  '39_YEAR_EA_UNIT':'It is 39-year property counted in each (EA); building components are usually measured in SF or LF.',
 };
 const MODEL_MAX=300;// router: f"model:{reason}"[:300]
 // One code -> {code, kind, text}. kind "model" carries the model's own words in text.
 function explain(code){
  const raw=String(code??'').trim(),at=raw.indexOf(':'),prefix=at>0?raw.slice(0,at):'',rest=at>0?raw.slice(at+1):'';
  if(prefix==='model'){
   const own=rest.trim(),cut=raw.length>=MODEL_MAX&&!/[.!?)"'”’]$/.test(own);
   return {code:raw,kind:'model',text:!own||own==='requested review'?'':own+(cut?'…':'')};
  }
  let text;
  if(PLAIN[raw])text=PLAIN[raw];
  else if((prefix==='validation'||prefix==='cross')&&CHECKS[rest])text=CHECKS[rest];
  else if(prefix==='rule_contested')text=`Basis classified it by its rule for “${words(rest)}”, but the reference documents do not settle that treatment. Confirm the classification.`;
  else if(prefix==='provider_error')text=fallback(`call failed${rest?` (${rest})`:''}`);
  else text=(prefix==='validation'||prefix==='cross')&&rest?`${prefix==='cross'?'Cost and quantity check':'Classification check'}: ${words(rest).toLowerCase()}.`:sentence(raw.replace(/:/g,': '));
  return {code:raw,kind:'basis',text};
 }
 function explainAll(reasons){
  const out=[];
  for(const r of reasons||[]){
   const item=String(r??'').trim();
   if(!item)continue;
   const last=out[out.length-1];
   if(last?.kind==='model'&&/\s/.test(item)){last.code+='; '+item;Object.assign(last,explain(last.code));continue}
   out.push(explain(item));
  }
  return out;
 }
 const MODEL_ASKED='The classification model asked for review';
 const plain=r=>r.kind==='model'?(r.text?`${MODEL_ASKED}: “${r.text}”`:`${MODEL_ASKED}.`):r.text;
 // For a title attribute: one reason per line.
 const title=reasons=>explainAll(reasons).map(plain).join('\n');
 // <li> items for the asset page; the raw code stays on hover for whoever reads the logs.
 const items=reasons=>explainAll(reasons).map(r=>r.kind==='model'
  ?`<li title="${esc(r.code.length>80?r.code.slice(0,80)+'…':r.code)}">${MODEL_ASKED}${r.text?`, in its words: <q class="live-reason-model">${esc(r.text)}</q>`:'.'}</li>`
  :`<li title="${esc(r.code)}">${esc(r.text)}</li>`).join('');
 const api={explain,explainAll,title,items};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisReasons=api;
})(typeof globalThis==='object'?globalThis:this);
