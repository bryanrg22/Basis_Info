/* One local intake area. Groups are suggestions, not document understanding. */
'use strict';
Object.assign(state,{uploadItems:[],uploadFilter:'all'});
const localStudyFiles=new Map();
const uploadGroups={appraisal:'Appraisal',photos:'Property photos',documents:'Supporting documents'};
const uploadKey=f=>JSON.stringify([f.name,f.size,f.lastModified||0]);
function uploadFileError(file){
 if(!file||!file.size)return 'Empty files and folders cannot be added.';
 return /\.(pdf|jpe?g|png|webp|heic|heif|tiff?|bmp|gif|docx?|xlsx?|csv|txt|zip|dwg|dxf)$/i.test(file.name)?'':'Choose a PDF, photo, spreadsheet, document, CAD file, or ZIP.';
}
function suggestedUploadGroup(file){
 if(/\.(jpe?g|png|webp|heic|heif|tiff?|bmp|gif)$/i.test(file.name))return 'photos';
 return /\.pdf$/i.test(file.name)&&/apprais/i.test(file.name)?'appraisal':'documents';
}
function uploadWorkspaceBody(){
 const files=state.uploadItems.filter(f=>state.uploadFilter==='all'||f.kind===state.uploadFilter);
 return `<label class="study-dropzone unified-dropzone" data-upload-zone="all" for="study-file-input">${icon('upload',28)}<strong>Drop your study files here</strong><span>or <b>browse files</b></span><small>PDFs, property photos, spreadsheets and supporting files</small><input class="upload-file-input" id="study-file-input" type="file" multiple accept=".pdf,.jpg,.jpeg,.png,.webp,.heic,.heif,.tif,.tiff,.bmp,.gif,.doc,.docx,.xls,.xlsx,.csv,.txt,.zip,.dwg,.dxf" aria-label="Choose study files"></label><p class="upload-local-note">Files are grouped automatically. You can change a group below.</p>${state.uploadItems.length?`<div class="intake-filters" aria-label="Filter selected files">${[['all','All files'],...Object.entries(uploadGroups)].map(([key,label])=>`<button type="button" data-upload-filter="${key}" aria-pressed="${state.uploadFilter===key}">${label}<span>${state.uploadItems.filter(f=>key==='all'||f.kind===key).length}</span></button>`).join('')}</div><div class="intake-files ${state.uploadFilter==='photos'?'photo-intake':''}">${files.map(uploadFileRow).join('')||'<p class="intake-empty">No files in this group yet.</p>'}</div><p class="upload-local-note">Preview only · Files stay in this tab. Saved drafts keep names and groups; reselect files after reopening.</p>`:`<div class="intake-guide"><span>${icon('file',16)}Appraisal</span><span>${icon('image',16)}Property photos</span><span>${icon('layers',16)}Supporting documents</span></div><p class="upload-local-note">Your files will appear here, organized into groups. Nothing is uploaded in this preview.</p>`}`;
}
function uploadFileRow(f){
 const local=localStudyFiles.get(f.key),thumbnail=f.kind==='photos'&&local;
 return `<article class="intake-file">${thumbnail?`<img src="${local.url}" alt="${esc(f.name)}">`:`<div class="intake-file-icon">${icon(f.kind==='photos'?'image':'file',21)}</div>`}<div class="intake-file-name"><strong>${esc(f.name)}</strong><span>${!f.size?'Size unavailable':f.size>=1048576?(f.size/1048576).toFixed(1)+' MB':Math.max(1,Math.round(f.size/1024))+' KB'}${local?' · Selected locally':' · Reselect file to preview'}</span></div>${local&&/\.pdf$/i.test(f.name)?`<button type="button" class="intake-preview" data-upload-pdf-preview="${esc(f.key)}" aria-label="Preview ${esc(f.name)}">Preview</button>`:''}<label class="intake-group"><span class="sr-only">Group for ${esc(f.name)}</span><select data-upload-group="${esc(f.key)}" aria-label="Group for ${esc(f.name)}">${Object.entries(uploadGroups).map(([key,label])=>`<option value="${key}" ${f.kind===key?'selected':''}>${label}</option>`).join('')}</select></label><button type="button" class="intake-remove" data-upload-remove="${esc(f.key)}" aria-label="Remove ${esc(f.name)}">${icon('x',15)}</button></article>`;
}
function refreshUploadFiles(){const body=root.querySelector('.unified-dropzone')?.parentElement;if(body)body.innerHTML=uploadWorkspaceBody()}
function selectStudyFiles(files){
 const chosen=Array.from(files||[]);if(!chosen.length)return;
 let added=0,duplicates=0;const errors=[];
 for(const file of chosen){const error=uploadFileError(file);if(error){errors.push(`${file.name}: ${error}`);continue}
  const key=uploadKey(file),existing=state.uploadItems.find(f=>f.key===key);
  if(existing&&localStudyFiles.has(key)){duplicates++;continue}
  if(!localStudyFiles.has(key))localStudyFiles.set(key,{file,url:URL.createObjectURL(file)});
  if(!existing)state.uploadItems.push({key,name:file.name,size:file.size,kind:suggestedUploadGroup(file)});
  added++;
 }
 if(added){state.uploadFilter='all';render()}
 toast([added?`${added} ${added===1?'file':'files'} added and grouped locally.`:'',duplicates?`${duplicates} already added.`:'',...errors].filter(Boolean).join(' '));
 if(typeof syncDraftStatus==='function')syncDraftStatus();
}
root.addEventListener('change',e=>{
 if(e.target.id==='study-file-input')selectStudyFiles(e.target.files);
 if(e.target.matches('[data-upload-group]')){const file=state.uploadItems.find(f=>f.key===e.target.dataset.uploadGroup);if(file&&Object.hasOwn(uploadGroups,e.target.value)){file.kind=e.target.value;if(state.uploadFilter!=='all'&&state.uploadFilter!==file.kind)state.uploadFilter='all';refreshUploadFiles();if(typeof syncDraftStatus==='function')syncDraftStatus()}}
});
const isFileDrag=e=>Array.from(e.dataTransfer?.types||[]).includes('Files');
for(const event of ['dragenter','dragover'])root.addEventListener(event,e=>{if(!isFileDrag(e))return;const zone=e.target.closest('[data-upload-zone]');if(zone){e.preventDefault();zone.classList.add('is-dragging');e.dataTransfer.dropEffect='copy'}});
root.addEventListener('dragleave',e=>{const zone=e.target.closest('[data-upload-zone]');if(zone&&!zone.contains(e.relatedTarget))zone.classList.remove('is-dragging')});
root.addEventListener('drop',e=>{if(!isFileDrag(e))return;e.preventDefault();const zone=e.target.closest('[data-upload-zone]');root.querySelectorAll('.is-dragging').forEach(z=>z.classList.remove('is-dragging'));if(zone)selectStudyFiles(e.dataTransfer.files)});
window.addEventListener('dragover',e=>{if(isFileDrag(e)){e.preventDefault();if(!e.target.closest('[data-upload-zone]'))e.dataTransfer.dropEffect='none'}});
window.addEventListener('drop',e=>{if(isFileDrag(e))e.preventDefault()});
root.addEventListener('click',e=>{
 const filter=e.target.closest('[data-upload-filter]');if(filter){state.uploadFilter=filter.dataset.uploadFilter;refreshUploadFiles();root.querySelector(`[data-upload-filter="${state.uploadFilter}"]`)?.focus();return}
 const button=e.target.closest('[data-upload-remove]');if(!button)return;state.uploadItems=state.uploadItems.filter(f=>f.key!==button.dataset.uploadRemove);render();root.querySelector('#study-file-input')?.focus();if(typeof syncDraftStatus==='function')syncDraftStatus();
});
window.addEventListener('pagehide',e=>{if(e?.persisted)return;for(const item of localStudyFiles.values())URL.revokeObjectURL(item.url);localStudyFiles.clear()});
root.addEventListener('error',e=>{const image=e.target;if(image.tagName!=='IMG'||!image.closest('.intake-file'))return;const placeholder=document.createElement('div');placeholder.className='intake-file-icon';placeholder.textContent='Photo';image.replaceWith(placeholder);},true);
