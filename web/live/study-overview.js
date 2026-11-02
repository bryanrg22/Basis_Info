/* Presentation models only; workflow decisions remain in gates.js and the API. */
(function(global){
 'use strict';
 const G=typeof module==='object'&&module.exports?require('./gates.js'):global.BasisGates;
 const hints={Upload:'Appraisal and source photos',Appraisal:'Property details and source evidence',Rooms:'Room names and photo grouping',Components:'Photo outlines and room inventory',Assets:'Classification, quantities and costs',Results:'Allocation grouped by recovery class','Report & sign':'Report details, preview, signature and final downloads'};
 function steps(study,reportEntry){
  return G.navigation(study,'processing',reportEntry).map(step=>({...step,label:step.label==='Schedule'?'Results':step.label,hint:hints[step.label==='Schedule'?'Results':step.label],canOpen:step.available&&step.destination!=='processing'}));
 }
 function home(studies){
  const recent=[...studies].sort((a,b)=>String(b.updated_at).localeCompare(String(a.updated_at)));
  return {attention:recent.filter(s=>['waiting_review','failed','stalled','draft'].includes(s.status)),processing:recent.filter(s=>s.status==='processing'),recent:recent.slice(0,4),completed:recent.filter(s=>s.status==='completed').length};
 }
 // These are observed record timestamps, not an invented audit trail or actor attribution.
 function activity(studies){
  return studies.flatMap(study=>[
   ...(study.created_at?[{study,kind:'created',at:study.created_at}]:[]),
   ...(study.updated_at&&study.updated_at!==study.created_at?[{study,kind:'updated',at:study.updated_at}]:[])
  ]).sort((a,b)=>b.at.localeCompare(a.at));
 }
 function creator(study,me,members=[]){
  const person=me?.user?.id===study.created_by?me.user:members.find(m=>m.user_id===study.created_by);
  return person?{name:person.display_name||person.email||'Team member',email:person.email||null}:null;
 }
 const model={steps,home,activity,creator};
 if(typeof module==='object'&&module.exports)module.exports=model;else global.BasisStudyOverview=model;
})(globalThis);
