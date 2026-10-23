/* Live mode: the study's review gates, without the DOM (node-tested).
 *
 * Since v0.12 (the workflow's patch components-gate-v1) a study has two rooms gates: the
 * rooms review approves the grouping (room names, types and floors, merges, a photo moved to
 * another room: a photo_room correction), the room step then counts each room's components
 * (components_processing, "Preparing components"), and the components review takes the
 * component, count and re-analysis corrections and the per-photo add and fix tools. A
 * study whose workflow has the single rooms review (studies.components_gate false: started
 * before it) keeps one rooms gate for all of that. components_gate is null until the
 * workflow starts; such a study is shown with the components gate, which every new study
 * gets.
 */
(function(scope){
 'use strict';
 const ORDER=['uploading','extracting','appraisal_review','rooms_review','components_processing','components_review','assets_processing','assets_review','exporting','completed'];
 const GATE_SCREEN={appraisal_review:'appraisal',rooms_review:'rooms',components_review:'components',assets_review:'assets'};
 const componentsGate=study=>Boolean(study)&&study.components_gate!==false;
 // Where the photo objects and the room inventories are reviewed and edited.
 const editStage=study=>componentsGate(study)?'components_review':'rooms_review';
 const editScreen=study=>GATE_SCREEN[editStage(study)];
 function nextScreen(study){
  if(study.status==='completed')return 'complete';
  if(study.status==='waiting_review'&&GATE_SCREEN[study.current_stage])return GATE_SCREEN[study.current_stage];
  return 'processing';
 }
 // The step bar: Upload · Appraisal · Rooms · [Components ·] Assets · Schedule.
 function steps(study){
  const gate=componentsGate(study);
  const labels=[['upload','Upload','processing'],['appraisal_review','Appraisal','appraisal'],['rooms_review','Rooms','rooms'],...(gate?[['components_review','Components','components']]:[]),['assets_review','Assets','assets'],['completed','Schedule','complete']];
  const at=ORDER.indexOf(study.current_stage),done=study.status==='completed';
  const found=labels.findIndex(([stage])=>stage==='upload'?at<=1:ORDER.indexOf(stage)>=at);
  const index=found<0?labels.length-1:found;
  return labels.map(([,label,screen],i)=>({label,screen,state:i<index||done?'done':i===index?'active':''}));
 }
 // The stages the progress page lists, in order.
 function timeline(study){
  const base=['extracting','appraisal_review','rooms_review'];
  return [...base,...(componentsGate(study)?['components_processing','components_review']:[]),'assets_processing','assets_review','completed'];
 }
 // A pending move of a photo to another room of the revision (the rooms review).
 function moveCorrection(photo,from,to){
  return {key:`photo_room:${photo.document_id}`,entity_type:'photo_room',entity_id:photo.document_id,before:{room_id:from?from.id:null},after:{room_id:to.id},reason:null,title:photo.filename,summary:`move to ${to.name}`,display:to.name};
 }
 const api={ORDER,GATE_SCREEN,componentsGate,editStage,editScreen,nextScreen,steps,timeline,moveCorrection};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisGates=api;
})(typeof globalThis==='object'?globalThis:this);
