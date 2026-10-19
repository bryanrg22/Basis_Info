/* Presentation-only study and activity models; not an API or authorization contract. */
'use strict';
const WorkspaceModel=(()=>{
 const statuses=['waiting_review','processing','draft','completed','waiting_review','stalled'];
 const stages={waiting_review:'appraisal_review',processing:'extracting',draft:'uploading',completed:'completed',stalled:'extracting'};
 const clients=['Harbor Property Partners','Fairview Health Group','Willow Residential','Northgate Properties','Lakewood Holdings','Cedar Communities'];
 const words=['Alder','Juniper','Pacific','Cypress','Laurel','Oak','Canyon','Willow','Cedar','Park','Ocean','Maple'];
 function studies(seed,members,large=false){
  const rows=seed.map((s,i)=>({...s,client_name:s.client_name??clients[i]??'',id:`study-${String(i+1).padStart(3,'0')}`,address:['1200 Harbor Boulevard, Ventura, CA','480 Fairview Avenue, Pasadena, CA','820 Willow Street, Long Beach, CA','1400 Northgate Drive, Irvine, CA','64 Lakewood Drive, Los Angeles, CA','215 Cedar Court, Santa Monica, CA'][i],property_type:['Residential','Commercial','Multifamily','Commercial','Residential','Multifamily'][i],assigned_reviewer_ids:i===0?['alex']:i===1?['taylor']:[],updated_at:`2026-10-${String(16-Math.floor(i/2)).padStart(2,'0')}T${['09:42','08:30','08:12','16:20','14:05','11:30'][i]}:00-07:00`,editing_by:i===0?'alex':null}));
  rows.forEach(s=>{if(s.status==='completed'){s.completed_at='2025-09-18T16:20:00-07:00';s.updated_at=s.completed_at;s.updated='Sep 18, 2025'}});
  if(!large)return rows;
  for(let i=6;i<240;i++){
   const person=members[i%members.length],status=statuses[i%statuses.length],type=['Residential','Commercial','Multifamily'][i%3],hour=8+i%9;
   rows.push({id:`study-${String(i+1).padStart(3,'0')}`,property_name:`${words[i%words.length]} ${['Residence','Plaza','Apartments'][i%3]} ${Math.floor(i/12)+1}`,address:`${100+i*7} ${words[(i+3)%words.length]} ${i%2?'Avenue':'Drive'}, ${['Los Angeles','Irvine','Pasadena','Long Beach'][i%4]}, CA`,property_type:type,client_name:clients[i%clients.length],detail:type,status,current_stage:stages[status],revision:1+i%3,updated_at:`2026-10-${String(16-i%14).padStart(2,'0')}T${String(hour).padStart(2,'0')}:${String(i%60).padStart(2,'0')}:00-07:00`,updated:`Oct ${16-i%14}`,image:['exterior.webp','living.webp','bedroom.webp','kitchen.webp'][i%4],next:status==='completed'?'complete':status==='draft'?'upload':status==='waiting_review'?'appraisal':'processing',created_by_id:person.id,created_by_name:person.name,last_edited_by:person.name,assigned_reviewer_ids:status==='waiting_review'?[members[(i+1)%members.length].id]:[],editing_by:null});
  }
  return rows;
 }
 const isMine=(s,user)=>s.created_by_id===user.id;
 const assigned=(s,user)=>(s.assigned_reviewer_ids||[]).includes(user.id);
 const visible=(s,user)=>user.role==='Owner'||isMine(s,user)||assigned(s,user);
 const needsReview=s=>['waiting_review','stalled'].includes(s.status);
 function queue(studies,user){return studies.filter(s=>visible(s,user)&&(isMine(s,user)||assigned(s,user))&&s.status==='waiting_review').sort((a,b)=>a.updated_at.localeCompare(b.updated_at))}
 function scopeRows(studies,user,scope='Workspace studies',creator='Everyone'){
  return studies.filter(s=>visible(s,user)&&(user.role!=='Owner'||scope!=='My studies'||isMine(s,user))&&(user.role!=='Owner'||creator==='Everyone'||s.created_by_id===creator));
 }
 function portfolioStats(studies){
  return {total:studies.length,active:studies.filter(s=>['draft','processing','waiting_review','stalled'].includes(s.status)).length,completed:studies.filter(s=>s.status==='completed').length,review:studies.filter(s=>s.status==='waiting_review').length,attention:studies.filter(s=>s.status==='stalled').length};
 }
 function teamWorkload(studies,members){return members.map(member=>({...member,...portfolioStats(studies.filter(s=>s.created_by_id===member.id))}))}
 function matchesStatus(s,status){return status==='Needs review'?needsReview(s):status==='Processing'?s.status==='processing':status==='Completed'?s.status==='completed':status==='Drafts'?s.status==='draft':true}
 function list({studies,user,scope,creator,query='',status='All studies',sort='updated',page=1,pageSize=10,companyArchive=false}){
  const scoped=companyArchive?studies:scopeRows(studies,user,scope,creator),needle=query.trim().toLowerCase();
  const filtered=scoped.filter(s=>matchesStatus(s,status)&&(!needle||[s.property_name,s.client_name,s.address,s.created_by_name,s.last_edited_by].some(v=>(v||'').toLowerCase().includes(needle))));
  filtered.sort((a,b)=>sort==='name'?a.property_name.localeCompare(b.property_name):sort==='oldest'?a.updated_at.localeCompare(b.updated_at):b.updated_at.localeCompare(a.updated_at));
  const size=[10,25,50].includes(Number(pageSize))?Number(pageSize):10,pages=Math.max(1,Math.ceil(filtered.length/size)),current=Math.min(pages,Math.max(1,Number(page)||1));
  return {scoped,filtered,rows:filtered.slice((current-1)*size,current*size),total:filtered.length,page:current,pages,pageSize:size,start:filtered.length?(current-1)*size+1:0,end:Math.min(current*size,filtered.length)};
 }
 function events(study){
  const actor={id:study.created_by_id,name:study.created_by_name},t=new Date(study.updated_at).getTime(),stamp=offset=>new Date(t-offset*60000).toISOString();
  const result=[
   {id:study.id+'-created',study_id:study.id,actor,type:'created',title:'created the study',at:stamp(40),revision:1,details:[]},
   {id:study.id+'-upload',study_id:study.id,actor,type:'uploads',title:'added source documents',at:stamp(32),revision:1,files:['Appraisal.pdf','Property photos · 5 files'],details:[]}
  ];
  if(study.status==='draft')return result.slice(0,1);
  if(study.status!=='draft')result.push({id:study.id+'-processing',study_id:study.id,actor:{id:'basis',name:'Basis'},type:'processing',title:'completed appraisal extraction',at:stamp(18),revision:study.revision,details:[]});
  if(['waiting_review','completed','stalled'].includes(study.status))result.push({id:study.id+'-change',study_id:study.id,actor,type:'changes',title:study.current_stage==='assets_review'?'saved a quantity correction':'saved appraisal corrections',at:stamp(8),revision:study.revision,context:study.current_stage==='assets_review'?'Engineering takeoff / Carpeting':'Appraisal / Property details',details:study.current_stage==='assets_review'?[{field:'Quantity',before:'1,320 sq ft',after:'1,420 sq ft'},{field:'Engineer note',before:'—',after:'Updated from the floor plan.'}]:[{field:'Year built',before:'1996',after:'1998'},{field:'Engineer note',before:'—',after:'Confirmed against appraisal page 3.'}]});
  if(study.status==='waiting_review')result.push({id:study.id+'-ready',study_id:study.id,actor:{id:'basis',name:'Basis'},type:'reviews',title:`prepared ${study.current_stage==='assets_review'?'assets':'the appraisal'} for review`,at:stamp(0),revision:study.revision,details:[]});
  if(study.status==='completed')result.push({id:study.id+'-approved',study_id:study.id,actor,type:'reviews',title:'approved the final asset review',at:stamp(0),revision:study.revision,details:[{field:'Decision',before:'Awaiting review',after:'Approved'},{field:'Schedule',before:'In progress',after:'Completed'}]});
  return result;
 }
 function activity(studies,user,{studyId='all',actor='all',type='all'}={}){
  return studies.filter(s=>visible(s,user)&&(studyId==='all'||s.id===studyId)).flatMap(s=>events(s).map(e=>({...e,study:s}))).filter(e=>(actor==='all'||e.actor.id===actor)&&(type==='all'||e.type===type)).sort((a,b)=>b.at.localeCompare(a.at));
 }
 function fileList({studies,user,files,query='',studyId='all',category='all',companyArchive=false}){
  const permitted=new Map(studies.filter(s=>companyArchive||visible(s,user)).map(s=>[s.id,s])),needle=query.trim().toLowerCase();
  return files.filter(f=>permitted.has(f.study_id)&&(studyId==='all'||f.study_id===studyId)&&(category==='all'||f.category===category)).map(f=>({...f,study:permitted.get(f.study_id)})).filter(f=>!needle||[f.name,f.label,f.study.property_name,f.study.client_name,f.study.address,f.study.created_by_name,f.study.last_edited_by].some(v=>(v||'').toLowerCase().includes(needle)));
 }
 return {portfolioStats,teamWorkload,studies,isMine,assigned,visible,needsReview,queue,scopeRows,matchesStatus,list,events,activity,fileList};
})();
if(typeof module!=='undefined')module.exports=WorkspaceModel;
