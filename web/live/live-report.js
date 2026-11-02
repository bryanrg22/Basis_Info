/* Live mode, part 4: the final Report & sign screen (STUDY_REPORT_SPEC 11.13).
 *
 * After Results: where the current revision's report stands (GET .../report/
 * state), the three inputs of a final report (the firm profile, the study's details, the
 * engineer's signature) with their forms, the preview, the downloads (the report PDF, signed
 * once it is; the preparer's schedule as CSV and XLSX) and Sign.
 *
 * * The forms write whole documents with If-Match on their version (412: someone saved
 *   meanwhile; the form says so and reloads).
 * * Sign is enabled once the preview was opened (live/report.js signBlocker): its ETag is the
 *   reviewed_sha256 the API checks, so the engineer signs the report they saw. Any save
 *   clears it. A 412 from the API (the report changed since) asks for the preview again.
 */
(function(){
 'use strict';
 const C=globalThis.BASIS_CONFIG;if(!C||C.mode!=='live')return;
 const Live=globalThis.BasisLive,L=Live.state,api=Live.api,R=globalThis.BasisReport;
 const sid=()=>encodeURIComponent(L.studyId);
 L.reportPreview=null;// {studyId, revision, tag}
 const roleIn=s=>((Live.me()?.memberships)||[]).find(m=>m.org_id===s.org_id)?.role||null;
 const stateEntry=(s,force=false)=>Live.load('report-state:'+s.id+':'+s.revision,()=>api.request('GET',`/v1/studies/${encodeURIComponent(s.id)}/report/state`),{force});
 Live.reportStateEntry=s=>s.status==='completed'?stateEntry(s):null;
 const answersEntry=(s,force=false)=>Live.load('report-answers:'+s.id,()=>api.request('GET',`/v1/studies/${sid()}/report-answers`),{force});
 const profileEntry=(s,force=false)=>Live.load('firm-profile:'+s.org_id,()=>api.request('GET',`/v1/orgs/${encodeURIComponent(s.org_id)}/profile`,{expect:[404]}).catch(e=>{if(e.status===404)return null;throw e}),{force});
 function refresh(s){Live.invalidate('report-state:'+s.id);Live.invalidate('report-answers:'+s.id);Live.invalidate('firm-profile:'+s.org_id);L.reportPreview=null;Live.render()}
 const previewTag=s=>L.reportPreview&&L.reportPreview.studyId===s.id&&L.reportPreview.revision===s.revision?L.reportPreview.tag:null;
 // The revision history (GET .../revisions): which earlier revisions were signed, for their PDFs.
 const revisionsEntry=(s,force=false)=>Live.load('revisions:'+s.id+':'+s.revision,()=>api.request('GET',`/v1/studies/${encodeURIComponent(s.id)}/revisions`),{force});
 // A signature is never replaced: a report with other inputs is a new revision, signed on its
 // own (docs/api/REPORT.md "After signing"). The documented way to make one from a completed
 // study is POST .../reopen at the assets review (docs/api/REVISIONS.md section 2): the
 // approved assets are carried, only the export reruns, and the new report carries the stored
 // firm profile and study details. The previous revision's signed PDF stays as it was.
 const canRevise=(s,state,role)=>Boolean(state?.signature)&&s.status==='completed'&&(R.RANK[role]??-1)>=R.RANK.engineer;
 function earlierSigned(s){
  if(!(s.revision>1))return '';
  const e=revisionsEntry(s);
  if(!e.done)return `<p class="small live-report-earlier-state">Loading earlier revisions…</p>`;
  if(e.error)return `<p class="small live-report-earlier-state">Earlier revisions could not be loaded. ${esc(Live.errorText(e.error))}</p>`;
  const signed=(e.data?.items||[]).filter(r=>r.signature&&r.revision!==s.revision).sort((a,b)=>b.revision-a.revision);
  if(!signed.length)return '';
  return `<section class="live-report-earlier" aria-labelledby="live-report-earlier-title"><h3 id="live-report-earlier-title">Earlier signed reports</h3><p class="small">Each revision's signed PDF is kept as it was signed.</p><ul>${signed.map(r=>`<li><span>Revision ${r.revision} · signed ${esc(Live.when(r.signature.signed_at))}</span><button class="button small" data-live="report-download-revision" data-value="${r.revision}">${icon('download',15)}Signed report (PDF), revision ${r.revision}</button></li>`).join('')}</ul></section>`;
 }

 Live.reportSummary=s=>{
  const entry=stateEntry(s),summary=entry.error?{tone:'attention',title:'Report status unavailable',detail:'The report status could not be loaded. Open Client report to retry.'}:!entry.done?{tone:'neutral',title:'Checking report status…',detail:'Loading the saved report state.'}:R.summary(entry.data);
  return `<section class="report-status report-status--${summary.tone}" aria-label="Report status"><span class="report-status-symbol" aria-hidden="true">${icon('file',21)}</span><div><h2>${esc(summary.title)}</h2><p>${esc(summary.detail)}</p></div><a class="button" href="${Live.route('report',s.id)}">Report & sign${icon('arrow',16)}</a></section>`;
 };
 const BADGE={signed:['complete','Signed'],draft:['review','Draft'],not_exported:['','Not written yet'],skipped:['danger','No report'],loading:['','Loading']};
 function panel(s){
  const st=stateEntry(s),an=answersEntry(s),pr=profileEntry(s);
  if(!st.done||!an.done||!pr.done)return `<section class="panel live-report">${Live.loading('Loading the report…')}</section>`;
  if(st.error)return `<section class="panel live-report">${Live.failed(st.error,'report-reload')}</section>`;
  const state=st.data,role=roleIn(s),status=R.status(state),final=globalThis.BasisGates.completion(s,st),[cls,label]=status==='signed'&&!final.complete?['review','Signed earlier']:BADGE[status];
  const blocker=R.signBlocker(state,role,previewTag(s)),owner=role==='owner',engineer=(R.RANK[role]??-1)>=R.RANK.engineer;
  const intro={signed:final.complete?'The signed report and preparer’s schedule are ready to deliver.':'This is a previously signed report. Check the study status above before delivery.',draft:'The report is a DRAFT until an engineer signs it in Basis. Fill in what it lacks, open the preview, then sign.',not_exported:'The report and the preparer’s schedule are written when the assets review is approved.',skipped:`The export wrote no report: ${state.skipped?.message||''}`}[status];
  const action={firm:owner?`<button class="button small" data-live="report-edit-profile">${pr.data?'Edit':'Add'} firm profile</button>`:`<span class="small">${pr.data?'':'An owner adds it.'}</span>`,study:engineer?`<button class="button small" data-live="report-edit-answers">${an.data?.report_answers?'Edit':'Add'} study details</button>`:'',signature:''};
  const items=R.checklist(state).map(i=>`<li class="${i.done?'is-done':''}"><span class="live-report-mark" aria-hidden="true">${i.done?'✓':''}</span><div><strong>${esc(i.label)}</strong><p class="small">${esc(i.key==='study'&&an.data?.report_answers?`${an.data.report_answers.taxpayer_name} · ${R.PHOTOGRAPHS[an.data.report_answers.photographs]}`:i.key==='firm'&&pr.data?`${pr.data.profile.name}${pr.data.profile.engineer?' · '+pr.data.profile.engineer.name+', '+pr.data.profile.engineer.credentials:''}`:i.hint)}</p></div>${action[i.key]}</li>`).join('');
  const sig=state.signature,changeNotice=R.changeNotice(state);
  const signed=sig?`<div class="live-report-signature"><strong>Signed in Basis</strong> by ${esc(sig.engineer.name)}, ${esc(sig.engineer.credentials)} · ${esc(Live.when(sig.signed_at))}<p class="small">Signature ${esc(sig.id)} · PDF sha256 ${esc(sig.pdf_sha256.slice(0,16))}…</p></div>`:'';
  const files=new Set(state.files||[]),has=f=>files.has(f)||Boolean(sig&&f==='report.pdf');
  const dl=(fmt,file,text)=>`<button class="button small" data-live="report-download" data-value="${fmt}" ${has(file)?'':'disabled'}>${icon('download',15)}${text}</button>`;
  const canPreview=state.exported&&!state.skipped&&!sig;
  const revise=canRevise(s,state,role)?`<div class="live-report-revise"><p class="small">${changeNotice?'To deliver a report with the current details, start a new revision: the assets review opens again with the approved assets, approving it writes the updated report, and you preview and sign that revision on its own.':'A signature is never replaced. To change the report, start a new revision and sign it on its own.'}</p><button class="button small" data-live="report-new-revision">Start a new revision</button></div>`:'';
  return `<section class="panel live-report" aria-labelledby="live-report-title"><div class="panel-head"><h2 id="live-report-title">Client report</h2><span class="badge ${cls}">${label}</span></div><div class="panel-body"><p>${esc(intro)}</p>${changeNotice?`<p class="result-warning" role="status">${esc(changeNotice)}</p>`:''}<ul class="live-report-checklist">${items}</ul>${signed}<div class="row live-report-actions">${canPreview?`<button class="button small" data-live="report-preview">${previewTag(s)?'Preview again':'Open the preview'}</button>`:''}${dl('pdf','report.pdf',sig?'Signed report (PDF)':'Draft report (PDF)')}${dl('csv','schedule.csv','Schedule (CSV)')}${dl('xlsx','schedule.xlsx','Schedule (XLSX)')}${sig?'':`<button class="button primary small" data-live="report-sign" ${blocker?`disabled title="${esc(blocker)}"`:''}>Sign report</button>`}</div>${!sig&&blocker&&state.exported&&!state.skipped?`<p class="small live-report-why">${esc(blocker)}</p>`:''}${revise}${earlierSigned(s)}</div></section>`;
 }

 // A dedicated final step keeps allocation review separate from report completion.
 Live.screens.report=()=>Live.withStudy(s=>{
  const entry=stateEntry(s),final=globalThis.BasisGates.completion(s,entry);
  const subtitle=final.complete?'Download the signed report and preparer’s schedule.':entry.data?.signature?'Review the signed report and any changes since signing.':'Complete the report details, preview, then sign.';
  return `${Live.steps(s)}${heading('Report & sign',subtitle,`<a class="button" href="${Live.route('complete',s.id)}">${icon('back',16)}Back to results</a>`)}${panel(s)}`;
 });

 async function pdfFetch(path,query){
  const response=await api.request('GET',path,{query,raw:true});
  return {blob:await response.blob(),tag:response.headers.get('ETag')};
 }
 function save(blob,name){const url=URL.createObjectURL(blob),a=Object.assign(document.createElement('a'),{href:url,download:name});document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
 const fileBase=s=>`${s.property_name.replace(/[^\w-]+/g,'_')}_r${s.revision}`;

 Live.actions['report-reload']=()=>refresh(L.study);
 Live.actions['report-preview']=()=>Live.guarded('report-preview',async()=>{
  const s=L.study,{blob,tag}=await pdfFetch(`/v1/studies/${sid()}/report/preview`);
  if(!tag)throw new Error('The preview came without its ETag; the API must expose it (CORS).');
  // The page renders first (Sign enabled): the viewer is a modal inside the app's root,
  // which a later render would replace.
  L.reportPreview={studyId:s.id,revision:s.revision,tag};
  Live.render();
  openPdfPreview({name:`${fileBase(s)}_report_preview.pdf`,url:URL.createObjectURL(blob),description:`Draft report · revision ${s.revision}`,onClose(){URL.revokeObjectURL(this.url)}});
 });
 Live.actions['report-download-revision']=(el,rev)=>Live.guarded('report-download',async()=>{
  const s=L.study,revision=Number(rev),{blob}=await pdfFetch(`/v1/studies/${sid()}/report`,{format:'pdf',revision});
  save(blob,`${s.property_name.replace(/[^\w-]+/g,'_')}_r${revision}_report_signed.pdf`);
 });
 // The recovery path after a signature: a new revision at the assets review (POST .../reopen).
 // The plan is read first (dry_run) and shown; the engineer confirms; the study then reads
 // processing at the assets gate, and the progress page follows it.
 Live.actions['report-new-revision']=()=>Live.guarded('report-new-revision',async()=>{
  const s=L.study,plan=await api.request('POST',`/v1/studies/${sid()}/reopen`,{body:{stage:'assets_review',dry_run:true}});
  const m=modal('Start a new revision',`<p>Revision ${plan.revision} opens at the assets review with the assets of revision ${plan.from_revision} as they were approved. Approving it writes a new report with the current firm profile and study details; you then preview and sign revision ${plan.revision} on its own.</p><p>The signed report of revision ${s.revision} is kept and stays available here.</p>`,`${closeBtn}<button class="button primary" id="live-reopen-go">Start revision ${plan.revision}</button>`);
  m.querySelector('#live-reopen-go').onclick=()=>Live.guarded('report-reopen',async()=>{
   let started;
   try{started=await api.request('POST',`/v1/studies/${sid()}/reopen`,{body:{stage:'assets_review'}})}
   catch(e){m.remove();refresh(s);if(e.status===409){toast(e.kind==='workflow-running'?'This study is still processing. Try again in a moment.':e.kind==='study-archived'?'The study is archived: unarchive it first.':'The study is no longer completed. It has been refreshed.');await Live.reloadStudy();Live.render();return}throw e}
   m.remove();toast(`Revision ${started.revision} started. Approve the assets review to write the updated report, then sign it.`);
   Live.invalidate('studies');Live.invalidate('revisions:'+s.id);Live.invalidate('report-state:'+s.id);L.reportPreview=null;
   await Live.reloadStudy();Live.go('processing',s.id);
  });
 });
 Live.actions['report-download']=(el,fmt)=>Live.guarded('report-download',async()=>{
  const s=L.study,state=stateEntry(s).data,{blob}=await pdfFetch(`/v1/studies/${sid()}/report`,{format:fmt});
  save(blob,`${fileBase(s)}_${fmt==='pdf'?(state?.signature?'report_signed':'report_draft'):'schedule'}.${fmt}`);
 });

 const field=(id,label,value='',extra='')=>`<div class="field" style="margin-top:12px"><label for="${id}">${label}</label><input id="${id}" value="${esc(value??'')}" ${extra}></div>`;
 async function put(path,body,version){return api.request('PUT',path,{body,headers:{'If-Match':`"${version}"`}})}
 function conflict(e,what){if(e?.status===412){toast(`Someone saved the ${what} since you opened it. It has been reloaded: apply your change again.`);refresh(L.study);return true}return false}

 Live.actions['report-edit-answers']=()=>{
  const s=L.study,data=answersEntry(s).data,a=data?.report_answers||{};
  const radio=(v,text)=>`<label class="live-report-radio"><input type="radio" name="live-photographs" value="${v}" ${a.photographs===v?'checked':''}> ${text}</label>`;
  const m=modal('Study details for the report',`<p>The report states these exactly. It never claims a site visit that did not happen.</p>${field('live-taxpayer','Taxpayer (the owner of record)',a.taxpayer_name,'maxlength="500"')}<fieldset class="live-report-radios"><legend>Who photographed the property</legend>${radio('engineer_site_visit','An engineer, on a site visit')}${radio('taxpayer_supplied','The taxpayer supplied the photographs')}</fieldset>${field('live-photographed-by','Photographs taken by',a.photographed_by,'maxlength="500"')}${field('live-photographed-on','Date taken',a.photographed_on,'type="date"')}`,`${closeBtn}<button class="button primary" id="live-save-answers">Save</button>`);
  m.querySelector('#live-save-answers').onclick=()=>Live.guarded('report-save',async()=>{
   const {body,problems}=R.answersBody({taxpayer_name:m.querySelector('#live-taxpayer').value,photographs:m.querySelector('input[name="live-photographs"]:checked')?.value,photographed_by:m.querySelector('#live-photographed-by').value,photographed_on:m.querySelector('#live-photographed-on').value});
   if(problems.length){toast(problems.join(' '));return}
   const full={...a,...body};// interviews and prior depreciation, if the API has them, are kept
   try{await put(`/v1/studies/${sid()}/report-answers`,full,data?.report_answers_version??0)}catch(e){if(conflict(e,'study details'))return m.remove();throw e}
   m.remove();toast('Study details saved.');refresh(s);
  });
 };

 Live.actions['report-edit-profile']=()=>{
  const s=L.study,data=profileEntry(s).data,p=data?.profile||{},e=p.engineer||{};let logo=p.logo_png||null;
  const m=modal('Firm profile',`<p>Printed on every report of the firm: the letterhead and the engineer who signs. A signed report keeps the profile it was signed with.</p>${field('live-firm-name','Firm name',p.name,'maxlength="500"')}${field('live-firm-address','Address',p.address,'maxlength="500"')}${field('live-firm-phone','Phone',p.phone,'maxlength="500"')}${field('live-eng-name','Signing engineer',e.name,'maxlength="500"')}${field('live-eng-cred','Credentials',e.credentials,'maxlength="500" placeholder="PE, CCSP"')}${field('live-eng-license','License number',e.license_number,'maxlength="500"')}${field('live-eng-state','License state',e.license_state,'maxlength="2" placeholder="NY"')}<div class="field" style="margin-top:12px"><label for="live-firm-logo">Logo (PNG, at most 512 KB)</label><input id="live-firm-logo" type="file" accept="image/png"><p class="small" id="live-logo-note">${logo?'A logo is saved. Choose a file to replace it.':'No logo: the firm’s name is printed instead.'}</p></div>`,`${closeBtn}<button class="button primary" id="live-save-profile">Save</button>`);
  m.querySelector('#live-firm-logo').onchange=ev=>{const f=ev.target.files[0];if(!f)return;if(f.type!=='image/png'||f.size>524288){toast('The logo must be a PNG of at most 512 KB.');ev.target.value='';return}
   const r=new FileReader();r.onload=()=>{logo=String(r.result).split(',')[1];m.querySelector('#live-logo-note').textContent=`${f.name} will be saved.`};r.readAsDataURL(f)};
  m.querySelector('#live-save-profile').onclick=()=>Live.guarded('report-save',async()=>{
   const v=id=>m.querySelector(id).value;
   const {body,problems}=R.profileBody({name:v('#live-firm-name'),address:v('#live-firm-address'),phone:v('#live-firm-phone'),engineer_name:v('#live-eng-name'),credentials:v('#live-eng-cred'),license_number:v('#live-eng-license'),license_state:v('#live-eng-state')},logo);
   if(problems.length){toast(problems.join(' '));return}
   if(p.limiting_conditions)body.limiting_conditions=p.limiting_conditions;
   try{await put(`/v1/orgs/${encodeURIComponent(s.org_id)}/profile`,body,data?.version??0)}catch(err){if(conflict(err,'firm profile'))return m.remove();throw err}
   m.remove();toast('Firm profile saved.');refresh(s);
  });
 };

 Live.actions['report-sign']=()=>{
  const s=L.study,state=stateEntry(s).data,tag=previewTag(s),answers=answersEntry(s).data?.report_answers,e=profileEntry(s).data?.profile?.engineer||{};
  if(R.signBlocker(state,roleIn(s),tag)){toast(R.signBlocker(state,roleIn(s),tag));return}
  const m=modal('Sign the report',`<p>You are signing revision ${s.revision} of ${esc(s.property_name)}: the report in the preview you opened, with your signature added. A signature is never replaced; a later revision is signed on its own.</p>${field('live-sign-name','Your name',e.name,'maxlength="500"')}${field('live-sign-cred','Credentials',e.credentials,'maxlength="500"')}${field('live-sign-license','License number',e.license_number,'maxlength="500"')}${field('live-sign-state','License state',e.license_state,'maxlength="2"')}<div class="live-report-cert"><p class="strong">I certify that, to the best of my knowledge and belief:</p><ul>${R.certification(answers).map(t=>`<li>${esc(t)}</li>`).join('')}</ul></div><label class="live-report-attest"><input type="checkbox" id="live-sign-attest"> I have reviewed the report and I sign it.</label>`,`${closeBtn}<button class="button primary" id="live-sign-go">Sign report</button>`);
  m.querySelector('#live-sign-go').onclick=()=>Live.guarded('report-sign',async()=>{
   const v=id=>m.querySelector(id).value;
   if(!m.querySelector('#live-sign-attest').checked){toast('Check the box to confirm you reviewed and sign the report.');return}
   const st=v('#live-sign-state').trim().toUpperCase();
   if(!v('#live-sign-name').trim()||!v('#live-sign-cred').trim()){toast('Enter your name and credentials.');return}
   if(st&&!/^[A-Z]{2}$/.test(st)){toast('The license state is a two-letter code.');return}
   try{await api.request('POST',`/v1/studies/${sid()}/report/sign`,{body:R.signBody(state,tag,{name:v('#live-sign-name'),credentials:v('#live-sign-cred'),license_number:v('#live-sign-license'),license_state:st})})}
   catch(err){if(err.status===412){m.remove();toast('The report changed since you opened the preview. Open it again, review it, then sign.');refresh(s);return}if(err.status===409){m.remove();refresh(s)}throw err}
   m.remove();toast('Report signed. The signed PDF is ready to download.');refresh(s);
  });
 };
 Live.render();
})();
