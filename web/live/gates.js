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
 // The backend completes processing before report signing. Delivery is a separate check.
 function completion(study,entry){
  const pending=(title,detail,status='Needs attention',tone='attention')=>({complete:false,title,detail,status,tone});
  if(study.status!=='completed')return pending('Study in progress','Finish the review steps before preparing the report.','Not started','neutral');
  if(!entry?.done)return pending('Checking completion…','Reviews are approved. Checking the current report and signature.','Checking…','neutral');
  const report=entry.data,sig=report?.signature;
  if(entry.error||!report||report.study_id!==study.id||report.revision!==study.revision)return pending('Completion not verified','The current report status could not be verified. Retry in Report & sign.','Check status');
  if(report.skipped)return pending('Report not ready','Resolve the findings in Results before the report can be prepared.','Blocked');
  if(!report.exported)return pending('Report not ready','Reviews are approved. The current report has not been generated.','Not ready');
  if(sig){
   if(sig.study_id!==study.id||sig.revision!==study.revision||!Array.isArray(report.changed_since_signing))return pending('Signature needs verification','A signed report exists but could not be matched to this study. Check it before delivery.','Verify signature');
   if(report.changed_since_signing.length)return pending('Report needs updating','Details changed after signing. Sign an updated revision before delivery; the signed PDF is kept.','Update required');
   if(!Array.isArray(report.missing_for_final)||report.missing_for_final.length)return pending('Report details need review','The current report has incomplete details. Review them before delivery.','Needs details');
   return {complete:true,tone:'complete',title:'Study completed',detail:`Revision ${study.revision} is approved and signed. The report matches the current details and is ready to deliver.`,status:'Signed'};
  }
  if(!Array.isArray(report.missing_for_final))return pending('Completion not verified','The report checklist could not be verified. Retry in Report & sign.','Check status');
  if(report.missing_for_final.some(key=>key!=='signature'))return pending('Report details needed','Reviews are approved. Add the report details, then sign.','Needs details');
  return pending('Awaiting signature','Reviews are approved. Sign the report to finish the study.','Needs signature');
 }
 // Results can be ready while the final Report & sign step is still open.
 function steps(study,reportEntry){
  const gate=componentsGate(study);
  const labels=[['upload','Upload','processing'],['appraisal_review','Appraisal','appraisal'],['rooms_review','Rooms','rooms'],...(gate?[['components_review','Components','components']]:[]),['assets_review','Assets','assets'],['completed','Results','complete']];
  const at=ORDER.indexOf(study.current_stage),done=study.status==='completed';
  const found=labels.findIndex(([stage])=>stage==='upload'?at<=1:ORDER.indexOf(stage)>=at);
  const index=found<0?labels.length-1:found;
  return [...labels.map(([,label,screen],i)=>({label,screen,state:i<index||done?'done':i===index?'active':''})),{label:'Report & sign',screen:'report',state:done?(completion(study,reportEntry).complete?'done':'active'):''}];
 }
 // Location and workflow progress are independent: a completed step can be viewed.
 function navigation(study,view,reportEntry){
  const list=steps(study,reportEntry),current=list.findIndex(step=>step.state==='active');
  const selected=view==='files'?'Upload':view==='upload'?'Upload':view==='viewer'?(componentsGate(study)?'Components':'Rooms'):view==='asset-detail'?'Assets':null;
  return list.map((step,i)=>{
   const isCurrent=i===current,complete=step.state==='done',report=step.screen==='report';
   const status=report?completion(study,reportEntry).status:complete?'Completed':!isCurrent?'Not started':study.status==='waiting_review'?'Needs review':['failed','stalled'].includes(study.status)?'Needs attention':study.status==='archived'?'Archived':study.current_stage==='uploading'?'Add files':'In progress';
   const destination=report?'report':i===0?(complete?'files':'processing'):isCurrent&&study.status!=='waiting_review'?'processing':step.screen;
   const viewing=selected?step.label===selected:view==='processing'?isCurrent:step.screen===view;
   // How the status reads at a glance: done, needs the engineer, needs attention, running, not yet.
   const tone=report?completion(study,reportEntry).tone==='complete'?'done':step.state==='active'||completion(study,reportEntry).tone==='attention'?'review':'idle':complete?'done':!isCurrent?'idle':status==='Needs review'||status==='Add files'?'review':status==='Needs attention'?'danger':status==='Archived'?'idle':'progress';
   return {...step,selected:viewing,status,tone,destination,available:complete||isCurrent||viewing};
  });
 }
 function reviewContext(study,view,reportEntry){
  const stage={appraisal:'appraisal_review',rooms:'rooms_review',components:editStage(study),'asset-detail':'assets_review',assets:'assets_review','viewer-components':editStage(study),'viewer-rooms':'rooms_review'}[view];
  const readOnly=Boolean(stage)&&!(study.status==='waiting_review'&&study.current_stage===stage);
  if(study.status==='completed'){
   const final=completion(study,reportEntry);
   return {...final,readOnly,action:view==='report'?null:'report',actionLabel:final.complete?'View signed report':'Report & sign'};
  }
  if(study.status==='archived')return {tone:'neutral',title:'Study archived',readOnly,detail:'This study is archived. Recorded steps remain available to view.',action:view==='processing'?null:'processing'};
  if(!readOnly)return null;
  if(['failed','stalled'].includes(study.status))return {tone:'attention',title:'Study needs attention',readOnly,detail:'Processing stopped. View study progress for the issue and next action.',action:'processing'};
  return {tone:'neutral',title:'Review not open',readOnly,detail:study.status==='processing'?'This review is not open for changes while the study is processing.':'Changes are available only at the current review step.',action:'processing'};
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
 const api={ORDER,GATE_SCREEN,componentsGate,editStage,editScreen,nextScreen,completion,steps,navigation,reviewContext,timeline,moveCorrection};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisGates=api;
})(typeof globalThis==='object'?globalThis:this);
