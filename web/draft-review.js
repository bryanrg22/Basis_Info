/* Local design-preview drafts. No upload, account, or workflow API is called. */
'use strict';
const draftScreens=new Set(['upload','appraisal','rooms','viewer','assets','asset-detail','verification']);
Object.assign(state,{assetEdits:{},reviewedAssets:[],correctionReasons:{},componentEdits:{},componentEditor:null});
function componentEditorSnapshot(){const editor=state.componentEditor;return editor&&["label","count","material"].some(k=>editor[k]!==editor.initial[k])?JSON.parse(JSON.stringify(editor)):null}
function draftSnapshot(){return {
 uploadItems:(state.uploadItems||[]).map(f=>({...f})),propertyName:state.propertyName,propertyAddress:state.propertyAddress||'',clientName:state.clientName||'',uploads:[...state.uploads],photoUploads:(state.photoUploads||[]).map(f=>({...f})),group:{...state.group},
 removed:[...state.removed].sort(),verifiedFields:[...state.verifiedFields].sort(),
 corrections:{...state.corrections},correctionReasons:{...state.correctionReasons},
 photoReviewed:state.photoReviewed,assetEdits:JSON.parse(JSON.stringify(state.assetEdits)),
 componentEdits:JSON.parse(JSON.stringify(state.componentEdits)),componentEditor:componentEditorSnapshot(),
 reviewedAssets:[...state.reviewedAssets].sort()
}}
const draftStringify=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
const draftDefaults=draftSnapshot();
let draftRole=state.role,draftBaseline=draftStringify(draftDefaults),draftSavedAt=null,draftDialog=null,draftPending=null,unloadGuardAttached=false;
const draftStorageKey=()=>`basis-design-preview:draft:v1:${draftRole}:sample-study`;
function restoreDraft(value){
 state.uploadItems=(value.uploadItems||[...(value.uploads||[]).map(name=>({key:'legacy:'+name,name,size:0,kind:'appraisal'})),...(value.photoUploads||[]).map(f=>({...f,size:0,kind:'photos'}))]).map(f=>({...f}));state.propertyName=value.propertyName;state.propertyAddress=value.propertyAddress??'';state.clientName=value.clientName??'';state.uploads=[...value.uploads];state.photoUploads=(value.photoUploads||[]).map(f=>({...f}));state.group={...value.group};
 state.removed=new Set(value.removed);state.verifiedFields=new Set(value.verifiedFields);
 state.corrections={...value.corrections};state.correctionReasons={...value.correctionReasons};
 state.photoReviewed=value.photoReviewed;state.assetEdits=JSON.parse(JSON.stringify(value.assetEdits));
 state.reviewedAssets=[...value.reviewedAssets];state.componentEdits=JSON.parse(JSON.stringify(value.componentEdits||{}));state.componentEditor=value.componentEditor?JSON.parse(JSON.stringify(value.componentEditor)):null;
}
function validComponentValues(value){return value&&typeof value.label==='string'&&value.label.length<=120&&typeof value.material==='string'&&value.material.length<=120&&Number.isSafeInteger(value.count)&&value.count>0}
function validComponentEditor(value){return value===null||(value&&typeof value.id==='string'&&['label','count','material'].every(k=>typeof value[k]==='string'&&typeof value.initial?.[k]==='string'))}
function validDraft(d){return d&&(d.componentEdits===undefined||(d.componentEdits&&typeof d.componentEdits==='object'&&!Array.isArray(d.componentEdits)&&Object.values(d.componentEdits).every(validComponentValues)))&&(d.componentEditor===undefined||validComponentEditor(d.componentEditor))&&(d.uploadItems===undefined||(Array.isArray(d.uploadItems)&&d.uploadItems.every(f=>f&&typeof f.name==='string'&&typeof f.key==='string'&&['appraisal','photos','documents'].includes(f.kind)&&typeof f.size==='number')))&&typeof d.propertyName==='string'&&(d.propertyAddress===undefined||typeof d.propertyAddress==='string')&&(d.clientName===undefined||typeof d.clientName==='string')&&(d.photoUploads===undefined||(Array.isArray(d.photoUploads)&&d.photoUploads.every(f=>f&&typeof f.name==='string'&&typeof f.key==='string')))&&Array.isArray(d.uploads)&&d.uploads.every(x=>typeof x==='string')&&Array.isArray(d.removed)&&d.removed.every(x=>typeof x==='string')&&Array.isArray(d.verifiedFields)&&d.verifiedFields.every(Number.isInteger)&&Array.isArray(d.reviewedAssets)&&d.reviewedAssets.every(Number.isInteger)&&typeof d.photoReviewed==='boolean'&&['group','corrections','correctionReasons','assetEdits'].every(k=>d[k]&&typeof d[k]==='object'&&!Array.isArray(d[k]))}
function loadLocalDraft(){
 restoreDraft(draftDefaults);draftSavedAt=null;
 try{const stored=JSON.parse(localStorage.getItem(draftStorageKey()));if(stored?.version===1&&validDraft(stored.data)){restoreDraft(stored.data);draftSavedAt=stored.savedAt}}
 catch{/* Unavailable storage or an invalid old draft must not break the preview. */}
 draftBaseline=draftStringify(draftSnapshot());
}
function captureAssetDraft(){
 const panel=root.querySelector('.asset-main[data-draft-asset]');if(!panel)return;
 const key=panel.dataset.draftAsset,values={...(state.assetEdits[key]||{})};
 for(const field of panel.querySelectorAll('input[id],textarea[id],select[id]')){
  if(field.disabled||field.dataset.draftDefault===undefined)continue;
  if(field.value!==field.dataset.draftDefault)values[field.id]=field.value;else delete values[field.id];
 }
 if(Object.keys(values).length)state.assetEdits[key]=values;else delete state.assetEdits[key];
}
function hydrateAssetDraft(){
 const panel=root.querySelector('.asset-main');if(!panel)return;
 panel.dataset.draftAsset=String(state.selectedAsset);
 for(const field of panel.querySelectorAll('input[id],textarea[id],select[id]')){
  field.dataset.draftDefault=field.value;
  const saved=state.assetEdits[state.selectedAsset]?.[field.id];
  if(typeof saved==='string')field.value=saved;
 }
}
function hasUnsavedDraft(){captureAssetDraft();return draftStringify(draftSnapshot())!==draftBaseline}
function beforeDraftUnload(e){e.preventDefault();e.returnValue=''}
function syncDraftStatus(){
 const dirty=hasUnsavedDraft();
 if(dirty!==unloadGuardAttached){window[dirty?'addEventListener':'removeEventListener']('beforeunload',beforeDraftUnload);unloadGuardAttached=dirty}
 let bar=root.querySelector('.draft-status');
 if(!draftScreens.has(state.screen)){bar?.remove();return}
 if(!bar){bar=document.createElement('div');bar.className='draft-status';const content=root.querySelector('.content');const steps=content?.querySelector('.steps'),slot=root.querySelector('.study-draft-slot');if(slot)slot.append(bar);else content?.insertBefore(bar,steps||content.firstChild)}
 bar.classList.toggle('is-dirty',dirty);
 const signature=`${dirty}:${draftSavedAt||''}`;
 if(bar.dataset.state!==signature){bar.dataset.state=signature;bar.innerHTML=`<div><span class="draft-indicator" aria-hidden="true"></span><span class="draft-state" role="status">${dirty?'Unsaved changes':draftSavedAt?'Saved in this browser':'No unsaved changes'}</span><span class="draft-local-note">Local preview draft</span></div><button class="button small" data-action="save-study-draft" ${dirty?'':'disabled'}>Save draft</button>`}
}
function saveLocalDraft(){
 captureAssetDraft();const data=draftSnapshot(),savedAt=new Date().toISOString();
 try{localStorage.setItem(draftStorageKey(),JSON.stringify({version:1,savedAt,data}))}
 catch{toast('Draft could not be saved in this browser. Your changes are still open. Keep editing or choose Discard changes.');return false}
 draftBaseline=draftStringify(data);draftSavedAt=savedAt;syncDraftStatus();return true;
}
function closeDraftDialog(){draftDialog?.remove();draftDialog=null;draftPending=null;syncDraftStatus()}
function askBeforeLeaving(continueNavigation){
 if(!hasUnsavedDraft())return false;
 if(draftDialog)return true;
 clearSidebarHoverTimers();state.sidebarPeek=false;updateSidebarAppearance();
 const previous=document.activeElement;
 draftPending=continueNavigation;
 draftDialog=document.createElement('div');draftDialog.className='modal-backdrop draft-dialog';
 draftDialog.innerHTML=`<section class="modal" role="alertdialog" aria-modal="true" aria-labelledby="draft-dialog-title" aria-describedby="draft-dialog-description" tabindex="-1"><div class="draft-dialog-top"><img src="assets/basis-logo.png" alt="Basis"><button class="draft-dismiss" data-draft-choice="keep" aria-label="Keep editing">${icon('x',17)}</button></div><div class="draft-dialog-copy"><div class="draft-property"><span class="draft-indicator" aria-hidden="true"></span><span>${esc(state.propertyName||'Untitled study')}</span><span class="draft-unsaved">Unsaved changes</span></div><h2 id="draft-dialog-title">Save before leaving?</h2><p id="draft-dialog-description">Keep your progress so you can pick up here later.</p></div><div class="actions"><button class="button draft-keep" data-draft-choice="keep">Keep editing</button><div class="draft-leave-actions"><button class="button draft-discard" data-draft-choice="discard">Discard changes</button><button class="button primary" data-draft-choice="save">Save draft</button></div></div><p class="draft-storage-note">Form and review edits save in this browser only.</p><p class="draft-save-error" role="alert" hidden></p></section>`;
 root.append(draftDialog);
 const keep=()=>{closeDraftDialog();if(previous?.isConnected)previous.focus();else root.querySelector('.draft-status button')?.focus()};
 draftDialog.addEventListener('click',e=>{
  const choice=e.target.closest('[data-draft-choice]')?.dataset.draftChoice;
  if(!choice)return; // Clicking the backdrop never discards work.
  e.preventDefault();e.stopPropagation();
  if(choice==='keep'){keep();return}
  if(choice==='save'&&!saveLocalDraft()){const error=draftDialog.querySelector('.draft-save-error');error.hidden=false;error.textContent='Couldn’t save this draft. Your changes are still here. Keep editing and try again.';return;}
  if(choice==='discard'){
   restoreDraft(JSON.parse(draftBaseline));
   const panel=root.querySelector('.asset-main[data-draft-asset]');
   if(panel)for(const field of panel.querySelectorAll('[data-draft-default]'))field.value=state.assetEdits[panel.dataset.draftAsset]?.[field.id]??field.dataset.draftDefault;
  }
  const next=draftPending;closeDraftDialog();syncDraftStatus();next?.();
 });
 draftDialog.addEventListener('keydown',e=>{
  if(e.key==='Escape'){e.preventDefault();e.stopPropagation();keep();return}
  if(e.key==='Tab'){
   const buttons=[...draftDialog.querySelectorAll('button:not(:disabled)')],first=buttons[0],last=buttons.at(-1);
   if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}
   else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}
  }
 });
 draftDialog.querySelector('.draft-keep').focus();return true;
}
const navigateWithoutDraftGuard=navigate;
navigate=function(screen){if(screen!==state.screen&&askBeforeLeaving(()=>navigateWithoutDraftGuard(screen)))return;navigateWithoutDraftGuard(screen)};
function guardStudyHashChange(next,event){
 if(next===state.screen||!hasUnsavedDraft())return false;
 // The hash has moved, but the editor remains mounted until the user decides.
 history.replaceState(null,'',event.oldURL);
 return askBeforeLeaving(()=>navigateWithoutDraftGuard(next));
}
root.addEventListener('click',e=>{
 if(e.target.closest('.draft-dialog'))return;
 const save=e.target.closest('[data-action="save-study-draft"]');
 if(save){e.preventDefault();e.stopImmediatePropagation();if(saveLocalDraft())toast('Draft saved in this browser. No review decision was submitted.');return}
 const link=e.target.closest('a[href]');
 if(link&&!e.metaKey&&!e.ctrlKey&&!e.shiftKey&&!e.altKey&&e.button===0&&!link.target){
  const url=new URL(link.href,location.href),screen=url.hash.slice(1)||'landing';
  if(url.origin===location.origin&&url.pathname===location.pathname&&screen!==state.screen&&hasUnsavedDraft()){
   e.preventDefault();e.stopImmediatePropagation();askBeforeLeaving(()=>navigateWithoutDraftGuard(screen));return;
  }
 }
 if(e.target.id==='save-correction'){
  const reason=document.getElementById('correction-reason'),value=document.getElementById('correction-value');
  if(reason?.value.trim()&&value?.value.trim())state.correctionReasons[state.appraisal]=reason.value.trim();
 }
 const action=e.target.closest('[data-action]')?.dataset.action;
 if(action==='asset-reviewed'&&!blockedEdit()&&!state.reviewedAssets.includes(state.selectedAsset))state.reviewedAssets.push(state.selectedAsset);
 // State-changing click handlers run before this microtask, including modal actions.
 queueMicrotask(syncDraftStatus);
},true);
root.addEventListener('input',e=>{if(e.target.matches('.asset-main input,.asset-main textarea'))captureAssetDraft();if(['property-name','client-name','property-address'].includes(e.target.id)||e.target.closest('.asset-main'))syncDraftStatus()});
root.addEventListener('change',e=>{
 if(e.target.id==='screen-select'&&hasUnsavedDraft()&&e.target.value!==state.screen){e.preventDefault();e.stopImmediatePropagation();const target=e.target.value;e.target.value=state.screen;navigate(target);return}
 if(e.target.id==='preview-role'&&e.target.value!==state.role&&hasUnsavedDraft()){
  const select=e.target,target=select.value;select.value=state.role;e.preventDefault();e.stopImmediatePropagation();
  askBeforeLeaving(()=>{select.value=target;select.dispatchEvent(new Event('change',{bubbles:true}))});return;
 }
 queueMicrotask(()=>{captureAssetDraft();syncDraftStatus()});
},true);
const renderWithoutDrafts=render;
render=function(){
 captureAssetDraft();
 if(draftRole!==state.role){draftRole=state.role;loadLocalDraft()}
 renderWithoutDrafts();hydrateAssetDraft();syncDraftStatus();
};
loadLocalDraft();render();
