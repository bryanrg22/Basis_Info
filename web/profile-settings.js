/* Browser-only profile preview. No account, company, or audit record is changed. */
'use strict';
const localProfiles={};
for(const member of TEAM_MEMBERS){
 try{const stored=JSON.parse(localStorage.getItem('basis-profile-preview-v1:'+member.id));if(stored&&typeof stored.name==='string'&&stored.name.trim()&&stored.name.length<=80&&(!stored.photo||(typeof stored.photo==='string'&&/^data:image\/(png|jpeg|webp);base64,/.test(stored.photo)&&stored.photo.length<180000)))localProfiles[member.id]={name:stored.name.trim(),photo:stored.photo||''}}catch{};
}
function profileForUser(user){const profile=localProfiles[user.id];return profile?{...user,...profile,initials:profile.name.split(/\s+/).map(s=>Array.from(s)[0]).slice(0,2).join('').toUpperCase()}:user}
function profileAvatar(user){return user.photo?`<img src="${esc(user.photo)}" alt="">`:esc(user.name.split(/\s+/).map(s=>Array.from(s)[0]).slice(0,2).join('').toUpperCase())}
function syncProfileIdentity(){
 const user=previewUser(),trigger=document.getElementById('account-menu-trigger');if(!trigger)return;
 trigger.setAttribute('aria-label',`${user.name}, ${state.role} — account menu`);
 trigger.querySelector('.avatar').innerHTML=profileAvatar(user);trigger.querySelector('.account-details strong').textContent=user.name;
 if(!['appraisal','rooms','viewer','assets','asset-detail','verification','complete','processing'].includes(state.screen)){
  const meta=root.querySelector('.right-meta>span:last-child');if(meta)meta.textContent=user.name;
 }
}
async function readProfilePhoto(file){
 if(!['image/jpeg','image/png','image/webp'].includes(file.type))throw Error('Choose a JPG, PNG, or WebP image.');
 if(file.size>5*1024*1024)throw Error('Choose an image smaller than 5 MB.');
 const url=URL.createObjectURL(file);
 try{
  const img=new Image();await new Promise((resolve,reject)=>{img.onload=resolve;img.onerror=()=>reject(Error('This image could not be opened. Try another file.'));img.src=url});
  if(!img.naturalWidth||!img.naturalHeight||img.naturalWidth*img.naturalHeight>25000000)throw Error('Choose an image with fewer than 25 million pixels.');
  const canvas=document.createElement('canvas');canvas.width=canvas.height=256;const ctx=canvas.getContext('2d');
  const side=Math.min(img.naturalWidth,img.naturalHeight);ctx.drawImage(img,(img.naturalWidth-side)/2,(img.naturalHeight-side)/2,side,side,0,0,256,256);
  return canvas.toDataURL('image/webp',.88);
 }finally{URL.revokeObjectURL(url)}
}

const profileEdits={};
state.setting='Profile';
REVIEW.screens.find(s=>s[0]==='settings')[1]='Settings';
function profileDraft(user=previewUser()){return profileEdits[user.id]||(profileEdits[user.id]={name:user.name,photo:user.photo||''})}
function openProfileSettings(){state.setting='Profile';navigate('settings')}
function profileSettingsContent(){
 const user=previewUser(),draft=profileDraft(user);
 return `<section class="profile-settings-pane" aria-labelledby="profile-heading"><header class="settings-section-heading"><h2 id="profile-heading">Profile</h2><p>Your name and photo, wherever you work in Basis.</p></header><form id="profile-form" data-profile-user="${user.id}"><div class="profile-photo-row"><span class="profile-photo" id="profile-photo-preview">${profileAvatar({...user,...draft})}</span><div class="profile-photo-controls"><label class="button" for="profile-photo-input">${icon('image',15)}Change photo<input class="profile-file-input" type="file" id="profile-photo-input" accept="image/jpeg,image/png,image/webp" aria-describedby="profile-photo-help"></label><button type="button" class="profile-remove" id="profile-photo-remove" ${draft.photo?'':'disabled'}>Remove photo</button><p id="profile-photo-help">JPG, PNG or WebP · up to 5 MB</p></div></div><div class="profile-field-row"><div><h3>Full name</h3><p>Shown in your profile and account menu.</p></div><div class="field"><label for="profile-display-name" class="sr-only">Full name</label><input id="profile-display-name" name="display-name" autocomplete="name" maxlength="80" required value="${esc(draft.name)}"></div></div><div class="profile-field-row"><div><h3>Account email</h3><p>Managed by your Google account.</p></div><div class="profile-account-details"><strong>${esc(user.email)}</strong><span>Used to sign in to Basis</span></div></div><p class="profile-error" role="alert" id="profile-error" hidden></p><div class="actions"><span class="profile-local-note" id="profile-save-status" role="status">${draft.name!==user.name||draft.photo!==(user.photo||'')?'Unsaved changes':'Changes save in this browser for the preview.'}</span><button type="button" class="button" id="profile-reset">Cancel</button><button class="button primary" type="submit" id="profile-save">Save changes</button></div></form></section>`;
}
function companySettingsContent(){
 if(state.setting==='Cost sources')return `<section class="profile-settings-pane"><header class="settings-section-heading"><h2>Cost sources</h2><p>The licensed cost data your company uses for studies.</p></header><div class="settings-source"><span class="settings-source-icon">${icon('file',24)}</span><div><h3>Workspace cost database</h3><p>No import service connected in this preview.</p></div><span class="desk-status">Not connected</span></div><div class="settings-source-note"><h3>Keep your sources traceable</h3><p>Edition, location adjustments, licensing, and import status will appear alongside each source.</p></div>${btn('Preview import flow','future','Import needs validation, version, source, and processing state once backend ingestion exists.','','upload')}</section>`;
 return `<section class="profile-settings-pane"><header class="settings-section-heading"><h2>Workspace</h2><p>Your company’s identity and shared preferences.</p></header><div class="workspace-identity-preview"><span class="company-monogram">HE</span><div><strong>Harbor Engineering</strong><p>Enterprise workspace</p></div></div><div class="profile-field-row"><div><h3>Company name</h3><p>Visible to everyone in this workspace.</p></div><div class="field"><label class="sr-only" for="setting-name">Company name</label><input id="setting-name" value="Harbor Engineering"></div></div><div class="profile-field-row"><div><h3>Workspace timezone</h3><p>Used when displaying company activity.</p></div><div class="field"><label class="sr-only" for="setting-timezone">Workspace timezone</label><select id="setting-timezone"><option>America/Los_Angeles</option></select></div></div><div class="actions"><span class="profile-local-note">Company settings preview · not connected</span>${btn('Preview save','future','Workspace preferences need a company-scoped settings API. No company details were changed.','primary')}</div></section>`;
}
settings=function(){
 if(!isOwner()||!['Profile','Workspace','Cost sources'].includes(state.setting))state.setting='Profile';
 return `<div class="settings-page"><header class="desk-heading"><div><h1>Settings</h1><p>${isOwner()?'Your account and your company’s workspace.':'Your profile and account preferences.'}</p></div></header><div class="settings-layout"><nav class="settings-sections" aria-label="Settings sections"><span>Personal</span><button data-action="setting" data-value="Profile" ${state.setting==='Profile'?'aria-current="page"':''}>${icon('users',16)}Profile</button>${isOwner()?`<span>Company</span><button data-action="setting" data-value="Workspace" ${state.setting==='Workspace'?'aria-current="page"':''}>${icon('building',16)}Workspace</button><button data-action="setting" data-value="Cost sources" ${state.setting==='Cost sources'?'aria-current="page"':''}>${icon('file',16)}Cost sources</button><a href="#team">${icon('users',16)}Team & invitations${icon('chevron',12)}</a>`:''}</nav><div class="settings-detail">${state.setting==='Profile'?profileSettingsContent():companySettingsContent()}</div></div></div>`;
};
function bindProfileSettings(){
 const form=root.querySelector('#profile-form');if(!form)return;
 let user=previewUser();const draft=profileDraft(user);let pending=false,selection=0;
 const name=form.querySelector('#profile-display-name'),preview=form.querySelector('#profile-photo-preview'),remove=form.querySelector('#profile-photo-remove'),save=form.querySelector('#profile-save'),error=form.querySelector('#profile-error'),status=form.querySelector('#profile-save-status');
 const showError=message=>{error.hidden=false;error.textContent=message};
 const updatePhoto=()=>{preview.innerHTML=profileAvatar({...user,...draft,initials:(draft.name.trim()||user.name).split(/\s+/).map(s=>Array.from(s)[0]).slice(0,2).join('').toUpperCase()});remove.disabled=!draft.photo;status.textContent=draft.name!==user.name||draft.photo!==(user.photo||'')?'Unsaved changes':'Changes save in this browser for the preview.'};
 name.addEventListener('input',()=>{draft.name=name.value;name.setCustomValidity('');updatePhoto();error.hidden=true});
 form.querySelector('#profile-photo-input').addEventListener('change',async e=>{
  const file=e.target.files[0];if(!file)return;const request=++selection;pending=true;save.disabled=true;error.hidden=true;save.textContent='Preparing photo…';
  try{const result=await readProfilePhoto(file);if(request===selection){draft.photo=result;if(form.isConnected)updatePhoto()}}
  catch(err){if(request===selection&&form.isConnected)showError(err.message)}
  finally{if(request===selection){pending=false;if(form.isConnected){save.disabled=false;save.textContent='Save changes';e.target.value=''}}}
 });
 remove.onclick=()=>{selection++;pending=false;draft.photo='';save.disabled=false;save.textContent='Save changes';error.hidden=true;updatePhoto()};
 form.querySelector('#profile-reset').onclick=()=>{selection++;profileEdits[user.id]={name:user.name,photo:user.photo||''};render();root.querySelector('#profile-display-name')?.focus()};
 form.addEventListener('submit',e=>{
  e.preventDefault();if(pending)return;
  const value=name.value.trim();if(!value){name.setCustomValidity('Enter your name.');name.reportValidity();return}
  const profile={name:value,photo:draft.photo};
  try{localStorage.setItem('basis-profile-preview-v1:'+user.id,JSON.stringify(profile))}catch{showError('Couldn’t save this profile. Your changes are still here. Try again.');return}
  localProfiles[user.id]=profile;Object.assign(draft,profile);user=previewUser();syncProfileIdentity();status.textContent='Saved in this browser';toast('Profile saved in this browser.');
 });
}
const renderBeforeProfilePage=render;
render=function(){renderBeforeProfilePage();bindProfileSettings()};
render();
