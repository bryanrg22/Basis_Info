/* Live mode: the study report's checklist and signing rules, without the DOM (node-tested).
 *
 * GET /v1/studies/{id}/report/state says where the current revision's report stands: was it
 * exported, did the export skip it, what a final report still lacks (firm, study,
 * signature) and the signature. Signing (POST .../report/sign) needs the export, the firm
 * profile and the study's answers, an engineer or owner, no signature yet, and the preview
 * the engineer opened: its ETag is the reviewed_sha256 (STUDY_REPORT_SPEC 11.13).
 */
(function(scope){
 'use strict';
 const RANK={viewer:0,engineer:1,owner:2};
 // The certification the signer attests to: the report's section 10 (templates/report.typ),
 // version 1. The API refuses another version, so a change here goes with the template's.
 const CERTIFICATION_VERSION=1;
 const longDate=iso=>new Date(iso+'T00:00:00Z').toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric',timeZone:'UTC'});
 // The statements as the report prints them for these answers (null answers: placeholder).
 function certification(answers){
  const inspection=!answers?'[the inspection statement]':answers.photographs==='engineer_site_visit'?`I inspected the property on ${longDate(answers.photographed_on)}.`:`I did not inspect the property; the analysis relies on photographs supplied by ${answers.photographed_by}.`;
  return [
   'I developed the analysis in this report, and the statements of fact in it are true and correct.',
   inspection,
   'The recovery periods follow Rev. Proc. 87-56 and IRC § 168.',
   'My compensation is not contingent on the results of this study, and I have no present or prospective interest in the property.',
  ];
 }
 const PHOTOGRAPHS={engineer_site_visit:'An engineer’s site visit',taxpayer_supplied:'Photographs supplied by the taxpayer'};
 function status(state){
  if(!state)return 'loading';
  if(state.signature)return 'signed';
  if(!state.exported)return 'not_exported';
  if(state.skipped)return 'skipped';
  return 'draft';
 }
 function changeNotice(state){
  if(!state?.signature)return '';
  if(state.changed_since_signing==null)return 'The signed PDF is preserved. Changes since signing were not recorded for this signature.';
  const names=state.changed_since_signing.map(key=>({firm:'firm profile',study:'study details'}[key])).filter(Boolean);
  return names.length?'Signed with earlier information. The '+names.join(' and ')+' changed after signing; the signed PDF is unchanged.':'';
 }
 // The three inputs of a final report and whether each is in.
 function checklist(state){
  const missing=new Set(state?.missing_for_final||[]);
  return [
   {key:'firm',label:'Firm profile',done:!missing.has('firm'),hint:'Letterhead and the signing engineer. An owner saves it once for the firm.'},
   {key:'study',label:'Study details',done:!missing.has('study'),hint:'The taxpayer, and who photographed the property and when.'},
   {key:'signature',label:'Engineer’s signature',done:Boolean(state?.signature),hint:'Signed in Basis after reviewing the preview.'},
  ];
 }
 // Whether the signed-in user may sign now, and if not why (one sentence for the button's title).
 function signBlocker(state,role,previewTag){
  if(!state)return 'Loading.';
  if(state.signature)return 'This revision is signed.';
  if(!state.exported)return 'The report is written when the assets review is approved.';
  if(state.skipped)return `The export wrote no report: ${state.skipped.message}`;
  if((RANK[role]??-1)<RANK.engineer)return 'Only an engineer or an owner signs.';
  const missing=(state.missing_for_final||[]).filter(m=>m!=='signature');
  if(missing.includes('firm'))return 'Save the firm profile first.';
  if(missing.includes('study'))return 'Save the study details first.';
  if(!previewTag)return 'Open the preview first: you sign the report you reviewed.';
  return '';
 }
 // The body POST .../report/sign takes.
 function signBody(state,previewTag,engineer){
  const clean=Object.fromEntries(Object.entries(engineer||{}).map(([k,v])=>[k,typeof v==='string'?v.trim():v]).filter(([,v])=>v!==''&&v!==null&&v!==undefined));
  return {revision:state.revision,reviewed_sha256:String(previewTag).replace(/^"|"$/g,''),engineer:clean,attest:true,statement_version:CERTIFICATION_VERSION};
 }
 // Form values -> the StudyAnswers document (PUT .../report-answers).
 function answersBody(form){
  const out={taxpayer_name:(form.taxpayer_name||'').trim(),photographs:form.photographs,photographed_by:(form.photographed_by||'').trim(),photographed_on:form.photographed_on};
  const problems=[];
  if(!out.taxpayer_name)problems.push('Enter the taxpayer’s name.');
  if(!PHOTOGRAPHS[out.photographs])problems.push('Choose who photographed the property.');
  if(!out.photographed_by)problems.push('Enter who took the photographs.');
  if(!/^\d{4}-\d{2}-\d{2}$/.test(out.photographed_on||''))problems.push('Enter the date the photographs were taken.');
  return {body:out,problems};
 }
 // Form values -> the FirmProfile document (PUT /v1/orgs/{id}/profile), keeping a logo.
 function profileBody(form,logo){
  const opt=v=>{const t=(v||'').trim();return t||null};
  const state=opt(form.license_state);
  const body={name:(form.name||'').trim(),address:opt(form.address),phone:opt(form.phone),logo_png:logo||null,
   engineer:{name:(form.engineer_name||'').trim(),credentials:(form.credentials||'').trim(),license_number:opt(form.license_number),license_state:state?state.toUpperCase():null}};
  const problems=[];
  if(!body.name)problems.push('Enter the firm’s name.');
  if(!body.engineer.name)problems.push('Enter the signing engineer’s name.');
  if(!body.engineer.credentials)problems.push('Enter the engineer’s credentials (e.g. PE).');
  if(body.engineer.license_state&&!/^[A-Z]{2}$/.test(body.engineer.license_state))problems.push('The license state is a two-letter code.');
  return {body,problems};
 }
 const api={changeNotice,RANK,certification,CERTIFICATION_VERSION,PHOTOGRAPHS,status,checklist,signBlocker,signBody,answersBody,profileBody};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisReport=api;
})(typeof globalThis==='object'?globalThis:this);
