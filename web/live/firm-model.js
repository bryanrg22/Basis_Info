/* The firm pages' rules, without the DOM (node-tested in tests/firm-model.test.cjs): what
 * identifies a study and what a search matched, where a study stands as seven steps, what Home
 * puts in front of whom, and how a study or firm event reads as a sentence, grouped by day.
 *
 * A study's identity comes from `summary` (PROPOSED, FRONTEND_BACKLOG.md section 15: the
 * appraisal's address, county and parcel number, the client and borrower, the taxpayer, the
 * firm's file number). Without it a study is its property name and its engineer.
 */
(function(scope){
 'use strict';
 const DAY=86400000;
 const norm=s=>String(s??'').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g,'');
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

 /* ---------- identity and search ---------- */
 function place(study){
  const m=study?.summary;if(!m)return null;
  const city=[m.city,[m.state,m.zip].filter(Boolean).join(' ')].filter(Boolean).join(', ');
  return [m.address,city].filter(Boolean).join(', ')||null;
 }
 // The fields a study is found by, in the order a match is reported (the first field that holds
 // the whole query wins, so "harbor" on Harbor House reports the name, not the address).
 function fields(study){
  const m=study?.summary||{};
  return [
   ['Property',study?.property_name],['Address',place(study)],['Client',m.client],['Taxpayer',m.taxpayer],
   ['File number',m.file_number],['Parcel number',m.parcel_number],['County',m.county&&`${m.county} County`],
   ['Borrower',m.borrower],['Engineer',study?.created_by_name],['Engineer email',study?.created_by_email],
  ].filter(([,v])=>v!=null&&String(v).trim()).map(([field,value])=>({field,value:String(value)}));
 }
 // A query matches when every word appears in some field (any order, case and accents ignored).
 // Returns the field to show beside the result: the first holding the whole query, else the first
 // holding its first word; null when nothing matches.
 function match(study,query){
  const q=norm(query).trim();if(!q)return {field:null,value:null};
  const words=q.split(/\s+/).filter(Boolean),list=fields(study),hay=list.map(f=>norm(f.value));
  if(!words.every(w=>hay.some(h=>h.includes(w))))return null;
  const whole=list.findIndex((f,i)=>hay[i].includes(q));
  const first=whole>=0?whole:list.findIndex((f,i)=>hay[i].includes(words[0]));
  return list[first]||null;
 }
 // `text` with every word of `query` marked (<mark>), HTML-escaped.
 function highlight(text,query){
  const t=String(text??''),words=norm(query).trim().split(/\s+/).filter(Boolean);if(!words.length)return esc(t);
  const n=norm(t),marks=new Array(t.length).fill(false);
  // norm() keeps one character per character for the Latin text studies use; fall back to no marks otherwise.
  if(n.length!==t.length)return esc(t);
  for(const w of words){let i=n.indexOf(w);while(i>=0){for(let k=i;k<i+w.length;k++)marks[k]=true;i=n.indexOf(w,i+w.length)}}
  let out='',open=false;for(let i=0;i<t.length;i++){if(marks[i]&&!open){out+='<mark>';open=true}if(!marks[i]&&open){out+='</mark>';open=false}out+=esc(t[i])}
  return out+(open?'</mark>':'');
 }

 /* ---------- where a study stands ---------- */
 const STEPS=['Upload','Appraisal','Rooms','Components','Assets','Results','Report & sign'];
 const AT={uploading:0,extracting:1,appraisal_review:1,rooms_review:2,components_processing:3,components_review:3,assets_processing:4,assets_review:4,exporting:5,completed:6};
 // delivery: the finished study's report state as Live.delivery gives it ({label, tone}).
 function strip(study,delivery){
  const legacy=study?.components_gate===false,labels=legacy?STEPS.filter(s=>s!=='Components'):STEPS;
  let at=AT[study?.current_stage]??0;if(legacy&&at>=3)at=Math.max(2,at-1);
  const status=study?.status;
  const tone=status==='failed'||status==='stalled'?'danger':status==='waiting_review'?'review':status==='processing'?'progress':status==='draft'?'review':status==='completed'?(delivery?.tone==='done'?'done':delivery?.tone==='review'?'review':'progress'):status==='archived'?'done':'idle';
  const finished=status==='archived'||status==='completed'&&delivery?.tone==='done';
  return labels.map((label,i)=>({label,state:finished||i<at?'done':i===at?'current':'todo',tone:finished||i<at?'done':i===at?tone:'idle'}));
 }
 const GATE_STEP={appraisal_review:['Appraisal','appraisal'],rooms_review:['Rooms','rooms'],components_review:['Components','components'],assets_review:['Assets','assets']};
 const WORKING={uploading:'Waiting for files',extracting:'Reading the appraisal',components_processing:'Counting components',assets_processing:'Pricing the assets',exporting:'Writing the report'};
 // The one thing the study needs next, for its row: what to do, its tone, where it goes.
 function next(study,delivery){
  const s=study||{},gate=GATE_STEP[s.current_stage];
  if(s.status==='archived')return {label:'Archived',tone:'idle',screen:'processing',action:false};
  if(s.status==='failed'||s.status==='stalled')return {label:s.status==='failed'?'Failed · retry':'Needs attention',tone:'danger',screen:'processing',action:true};
  if(s.status==='draft')return {label:'Finish uploading',tone:'review',screen:'processing',action:true};
  if(s.status==='waiting_review'&&gate)return {label:`Review ${gate[0].toLowerCase()}`,tone:'review',screen:gate[1],action:true};
  if(s.status==='processing')return {label:WORKING[s.current_stage]||'Processing',tone:'progress',screen:'processing',action:false};
  if(s.status==='completed'){
   const d=delivery||{label:'Checking…',tone:'pending'};
   if(d.tone==='done')return {label:d.label==='Signed'?'Signed':d.label,tone:'done',screen:'processing',action:false};
   if(d.tone==='review')return {label:d.label==='Needs signature'?'Sign report':d.label==='Update required'?'Update required':d.label,tone:'review',screen:'report',action:true};
   return {label:d.label,tone:'idle',screen:'processing',action:false};
  }
  return {label:s.status||'Unknown',tone:'idle',screen:'processing',action:false};
 }
 // Home: what needs this person (their own studies, or every study for an admin), what Basis is
 // working on, and what was finished. `deliveryOf(study)` gives a finished study's report state.
 function home(studies,me,{admin=false,deliveryOf=()=>null,now=Date.now()}={}){
  const live=(studies||[]).filter(s=>s.status!=='archived');
  const mine=s=>admin||s.created_by===me?.id;
  const byUpdate=(a,b)=>String(b.updated_at).localeCompare(String(a.updated_at));
  const rows=live.map(s=>({study:s,next:next(s,deliveryOf(s))}));
  const needs=rows.filter(r=>r.next.action&&mine(r.study)).sort((a,b)=>rank(a)-rank(b)||byUpdate(a.study,b.study));
  const working=rows.filter(r=>r.study.status==='processing'&&mine(r.study)).sort((a,b)=>byUpdate(a.study,b.study));
  const finished=rows.filter(r=>r.study.status==='completed'&&r.next.tone==='done').sort((a,b)=>byUpdate(a.study,b.study));
  const others=admin?[]:rows.filter(r=>r.next.action&&!mine(r.study)).sort((a,b)=>byUpdate(a.study,b.study));
  const recent=finished.filter(r=>now-Date.parse(r.study.updated_at)<=30*DAY);
  return {needs,working,finished,others,signedThisMonth:recent.length,checking:rows.filter(r=>r.study.status==='completed'&&r.next.tone==='pending').length};
 }
 // Failures first, then what waits on a signature, then reviews (oldest wait first within each).
 const rank=r=>r.next.tone==='danger'?0:r.next.label==='Sign report'||r.next.label==='Update required'?1:r.study.status==='draft'?3:2;

 /* ---------- events as sentences ---------- */
 const STEP_OF={uploading:'Upload',extracting:'Upload',appraisal_review:'Appraisal',rooms_review:'Rooms',components_processing:'Components',components_review:'Components',assets_processing:'Assets',assets_review:'Assets',exporting:'Results',completed:'Report & sign'};
 const SCREEN_OF={appraisal_review:'appraisal',rooms_review:'rooms',components_review:'components',assets_review:'assets'};
 const ROLE={owner:'an admin',engineer:'an engineer',viewer:'a viewer'};
 const ROLE_NAME={owner:'Admin',engineer:'Engineer',viewer:'Viewer'};
 const plural=(n,one,many=one+'s')=>`${n} ${n===1?one:many}`;
 // Study events worth reading, as {kind, verb, step, screen, detail}; null for bookkeeping
 // (activities, stage moves, run starts, the corrections a decision already counts).
 function studyEntry(e){
  const step=STEP_OF[e.stage]||null,gateScreen=SCREEN_OF[e.stage]||'processing';
  switch(e.event_type){
   case 'review.decided':{
    const approve=e.after?.decision==='approve'||e.detail?.decision==='approve',c=e.detail?.corrections,n=Array.isArray(c)?c.length:Number(c)||0,comment=e.detail?.comment;
    return {kind:'review',verb:approve?`approved ${step}`:`requested corrections at ${step}`,step,screen:gateScreen,detail:approve?(comment||null):[n?plural(n,'correction'):null,comment].filter(Boolean).join(' · ')||null,tone:approve?'done':'review'};
   }
   case 'gate.opened':return {kind:'review',verb:`opened the ${step} review`,step,screen:gateScreen,detail:null,tone:'review',system:true};
   case 'report.signed':return {kind:'signature',verb:`signed revision ${e.revision}`,step:'Report & sign',screen:'report',detail:null,tone:'done'};
   case 'export.written':return e.detail?.skipped?{kind:'signature',verb:'could not write the report',step:'Results',screen:'complete',detail:null,tone:'danger',system:true}:{kind:'signature',verb:`wrote the report for revision ${e.revision}`,step:'Report & sign',screen:'report',detail:null,tone:'progress',system:true};
   case 'report_answers.saved':return {kind:'signature',verb:'updated the study details for the report',step:'Report & sign',screen:'report',detail:null,tone:'idle'};
   case 'study.reopened':return {kind:'review',verb:`reopened ${step||'a step'}`,step,screen:gateScreen,detail:e.detail?.from_revision?`Revision ${e.detail.from_revision} → ${e.revision}`:null,tone:'review'};
   case 'study.archived':return {kind:'study',verb:'archived the study',step:null,screen:'processing',detail:null,tone:'idle'};
   case 'study.unarchived':return {kind:'study',verb:'restored the study from the archive',step:null,screen:'processing',detail:null,tone:'idle'};
   case 'study.retry_requested':return {kind:'study',verb:'retried processing',step,screen:'processing',detail:null,tone:'progress'};
   case 'study.cancel_requested':return {kind:'study',verb:'cancelled processing',step,screen:'processing',detail:null,tone:'idle'};
   case 'study.restarted':return {kind:'study',verb:'restarted the study',step,screen:'processing',detail:null,tone:'progress'};
   case 'stage.failed':return {kind:'study',verb:e.code==='BASIS-INGEST-005'?'could not read the appraisal':`stopped at ${step||'a step'}`,step,screen:'processing',detail:e.code==='BASIS-INGEST-005'?'The PDF has no readable text: upload a text PDF and retry.':'Retry from the study page.',tone:'danger',system:true};
   case 'study.created':case 'document.uploaded':case 'study.started':return {kind:'upload'};// combined by feed()
   default:return null;
  }
 }
 // Firm events (admins: GET /v1/orgs/{id}/activity) as entries; `name(id)` resolves a person.
 function firmEntry(e,name){
  const who=name(e.subject_user_id)||e.after?.email||e.before?.email||'a member';
  switch(e.event_type){
   case 'invitation.created':return {kind:'team',verb:`invited ${e.after?.email} as ${ROLE[e.after?.role]||'a member'}`,screen:'team',detail:null,tone:'idle'};
   case 'invitation.accepted':return {kind:'team',verb:`joined the firm as ${ROLE[e.after?.role]||'a member'}`,screen:'team',detail:null,tone:'done'};
   case 'invitation.declined':return {kind:'team',verb:'declined the invitation',screen:'team',detail:null,tone:'idle'};
   case 'invitation.revoked':return e.detail?.reason==='revoked'?{kind:'team',verb:`revoked the invitation for ${e.before?.email}`,screen:'team',detail:null,tone:'idle'}:null;
   case 'member.role_changed':return {kind:'team',verb:`made ${who} ${ROLE[e.after?.role]||'a member'}`,screen:'team',detail:`Was ${ROLE_NAME[e.before?.role]||e.before?.role}`,tone:'idle'};
   case 'member.removed':return {kind:'team',verb:e.detail?.self?'left the firm':`removed ${who} from the firm`,screen:'team',detail:null,tone:'idle'};
   case 'profile.set':return {kind:'team',verb:'updated the firm letterhead',screen:'settings',detail:null,tone:'idle'};
   case 'org.created':return {kind:'team',verb:`created ${e.after?.name||'the firm'}`,screen:'team',detail:null,tone:'done'};
   case 'org.renamed':return {kind:'team',verb:`renamed the firm to ${e.after?.name}`,screen:'settings',detail:null,tone:'idle'};
   default:return null;
  }
 }
 // The feed: study events (each with study_id, property_name, actor_id, actor_name) and firm
 // events, newest first, as readable entries. A study's creation, uploads and start by one person
 // within an hour read as one entry ("created … and uploaded the appraisal and 5 photos").
 function feed(studyEvents,{firmEvents=[],name=()=>null,studies=[]}={}){
  const byStudy=new Map(studies.map(s=>[s.id,s]));
  const out=[];
  const sorted=[...studyEvents].sort((a,b)=>String(b.at).localeCompare(String(a.at)));
  const used=new Set();
  sorted.forEach((e,i)=>{
   if(used.has(i))return;
   const d=studyEntry(e);if(!d)return;
   const base={at:e.at,actorId:e.actor_type==='engineer'?e.actor_id:null,actor:e.actor_type==='engineer'?(e.actor_name||name(e.actor_id)||'A member'):'Basis',study:{id:e.study_id,name:e.property_name||byStudy.get(e.study_id)?.property_name||'A study',place:e.property_address||place(byStudy.get(e.study_id))},revision:e.revision};
   if(d.kind==='upload'){
    // Gather this person's creation, uploads and start on this study within the hour.
    const t=Date.parse(e.at),group=[];
    sorted.forEach((x,j)=>{if(!used.has(j)&&x.study_id===e.study_id&&x.actor_id===e.actor_id&&['study.created','document.uploaded','study.started'].includes(x.event_type)&&Math.abs(Date.parse(x.at)-t)<=3600000){group.push(x);used.add(j)}});
    const docs=group.filter(x=>x.event_type==='document.uploaded'),photos=docs.filter(x=>x.detail?.kind==='photo').length,appraisal=docs.some(x=>x.detail?.kind==='appraisal');
    const created=group.some(x=>x.event_type==='study.created'),started=group.some(x=>x.event_type==='study.started');
    const what=[appraisal&&'the appraisal',photos&&plural(photos,'photo')].filter(Boolean).join(' and ');
    const verb=created?(what?`created the study and uploaded ${what}`:'created the study'):what?`uploaded ${what}`:started?'started processing':'updated the study';
    out.push({...base,at:group[0]?.at||e.at,kind:'upload',verb,step:'Upload',screen:'files',detail:started&&created?'Basis started reading it':null,tone:'idle'});
    return;
   }
   used.add(i);out.push({...base,...d});
  });
  for(const e of firmEvents){const d=firmEntry(e,name);if(!d)continue;out.push({at:e.at,actorId:e.actor_id,actor:e.actor_id?(name(e.actor_id)||'A member'):'Basis',study:null,...d,subject:e.event_type==='invitation.accepted'?name(e.subject_user_id):null})}
  return out.sort((a,b)=>String(b.at).localeCompare(String(a.at)));
 }
 // Entries grouped by calendar day in the viewer's time zone: Today, Yesterday, then dates.
 function byDay(entries,now=Date.now(),locale='en-US'){
  const key=t=>{const d=new Date(t);return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`};
  const today=key(now),yesterday=key(now-DAY),groups=[];
  for(const e of entries){const k=key(Date.parse(e.at));let g=groups[groups.length-1];if(!g||g.key!==k){g={key:k,label:k===today?'Today':k===yesterday?'Yesterday':new Intl.DateTimeFormat(locale,{weekday:'long',month:'short',day:'numeric'}).format(new Date(e.at)),entries:[]};groups.push(g)}g.entries.push(e)}
  return groups;
 }
 // "just now", "12 min ago", "3 h ago", "Yesterday", "Mon", "Oct 2".
 function ago(at,now=Date.now(),locale='en-US'){
  const t=Date.parse(at);if(!Number.isFinite(t))return '';
  const s=Math.max(0,(now-t)/1000);
  if(s<60)return 'just now';if(s<3600)return `${Math.floor(s/60)} min ago`;
  const d=new Date(t),n=new Date(now),sameDay=d.toDateString()===n.toDateString();
  if(sameDay)return `${Math.floor(s/3600)} h ago`;
  if(new Date(now-DAY).toDateString()===d.toDateString())return 'Yesterday';
  if(s<6*86400)return new Intl.DateTimeFormat(locale,{weekday:'short'}).format(d);
  return new Intl.DateTimeFormat(locale,{month:'short',day:'numeric'}).format(d);
 }
 function greeting(now=Date.now()){const h=new Date(now).getHours();return h<12?'Good morning':h<18?'Good afternoon':'Good evening'}
 const initials=t=>String(t||'').replace(/@.*/,'').split(/[^a-z0-9]+/i).filter(Boolean).slice(0,2).map(w=>w[0].toUpperCase()).join('')||'?';
 // Signer details as one line: "PE · CA PE-77120".
 const signerLine=s=>s?[s.credentials,[s.license_state,s.license_number].filter(Boolean).join(' ')].filter(Boolean).join(' · '):'';
 const STATES=['AL','AK','AZ','AR','CA','CO','CT','DE','DC','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY'];
 // A signer form's values as the body of PUT /v1/me/signer, with the problems to show.
 function signerBody(form){
  const t=v=>String(v??'').trim(),body={name:t(form.name),credentials:t(form.credentials),license_number:t(form.license_number)||null,license_state:t(form.license_state).toUpperCase()||null},problems=[];
  if(!body.name)problems.push('Enter the name you sign with.');
  if(!body.credentials)problems.push('Enter your credentials, for example PE.');
  if(body.license_state&&!STATES.includes(body.license_state))problems.push('Choose the state that issued your license.');
  if(Boolean(body.license_number)!==Boolean(body.license_state))problems.push(body.license_number?'Choose the state that issued your license.':'Enter the license number for that state.');
  return {body,problems};
 }
 const api={fields,match,highlight,place,strip,next,home,STEPS,studyEntry,firmEntry,feed,byDay,ago,greeting,initials,signerLine,signerBody,STATES,ROLE_NAME};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisFirmModel=api;
})(typeof globalThis==='object'?globalThis:this);
