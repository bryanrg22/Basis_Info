/* Local PDF.js viewer shared by study files and unsent upload selections. */
'use strict';
let pdfModulePromise;
async function loadPdfModule(){
 pdfModulePromise??=import('./assets/vendor/pdfjs/pdf.min.mjs');
 const pdfjs=await pdfModulePromise.catch(error=>{pdfModulePromise=undefined;throw error});
 pdfjs.GlobalWorkerOptions.workerSrc=new URL('./assets/vendor/pdfjs/pdf.worker.min.mjs',location.href).href;
 return pdfjs;
}
function pdfDocumentOptions(url){return {url,standardFontDataUrl:new URL('./assets/vendor/pdfjs/standard_fonts/',location.href).href,cMapUrl:new URL('./assets/vendor/pdfjs/cmaps/',location.href).href,cMapPacked:true,wasmUrl:new URL('./assets/vendor/pdfjs/wasm/',location.href).href,isEvalSupported:false}}

async function openPdfPreview(file){
 const url=file.pdf||file.url;if(!url)return;
 const viewer=modal(esc(file.name),`<button class="pdf-close" data-close aria-label="Close PDF preview">${icon('x',19)}</button><div class="pdf-file-meta"><span>${file.description?esc(file.description):file.sample?'Design sample · not a professional appraisal':file.uploaded?'Uploaded study document':'Local PDF · not uploaded'}</span><a href="${esc(url)}" download="${esc(file.name)}">${icon('download',15)}Download PDF</a></div><div class="pdf-controls"><div class="pdf-page-controls"><button data-pdf-action="previous" aria-label="Previous PDF page" disabled>${icon('back',16)}</button><label>Page<input id="pdf-page-number" aria-label="PDF page number" type="number" value="1" min="1" disabled></label><span class="pdf-page-total">of —</span><button data-pdf-action="next" aria-label="Next PDF page" disabled>${icon('arrow',16)}</button></div><div class="pdf-zoom-controls"><button data-pdf-action="out" aria-label="Zoom out PDF" disabled>−</button><span class="pdf-zoom-level">100%</span><button data-pdf-action="in" aria-label="Zoom in PDF" disabled>+</button><button class="pdf-fit" data-pdf-action="fit" disabled>Fit width</button></div></div><div class="pdf-status" role="status">Loading PDF…</div><div class="basis-pdf-frame"><div class="pdf-stage basis-pdf-scroll" tabindex="0" role="region" aria-label="PDF report" aria-busy="true"></div></div>`,'');
 const panel=viewer.querySelector('.modal');panel.classList.add('pdf-modal');
 const stage=viewer.querySelector('.pdf-stage'),status=viewer.querySelector('.pdf-status'),input=viewer.querySelector('#pdf-page-number');
 let page=1,scale=1;
 const report=BasisPdfViewer.create({stage,url,initialPage:file.initialPage||1,onChange:info=>{
  page=info.page;scale=info.scale;
  input.value=page;
  input.max=info.total;input.disabled=false;
  viewer.querySelector('.pdf-page-total').textContent='of '+info.total;
  viewer.querySelector('.pdf-zoom-level').textContent=Math.round(scale*100)+'%';
  for(const button of viewer.querySelectorAll('[data-pdf-action]'))button.disabled=false;
  viewer.querySelector('[data-pdf-action=previous]').disabled=page===1;
  viewer.querySelector('[data-pdf-action=next]').disabled=page===info.total;
  viewer.querySelector('[data-pdf-action=out]').disabled=scale<=.25;
  viewer.querySelector('[data-pdf-action=in]').disabled=scale>=3;
  status.hidden=true;
 },onError:error=>{
  status.hidden=false;
  status.textContent=error.name==='PasswordException'?'This PDF is password protected. Download it to open it securely on your computer.':'This PDF could not be displayed. Download the original or close and reopen the preview.';
 }});
 viewer.addEventListener('click',e=>{
  const action=e.target.closest('[data-pdf-action]')?.dataset.pdfAction;
  if(action==='previous'||action==='next')report.goTo(page+(action==='next'?1:-1));
  if(action==='out'||action==='in')report.zoom((scale+(action==='in'?.25:-.25))/scale);
  if(action==='fit')report.fitWidth();
 });
 function commitPage(){const target=BasisPdfViewer.pageNumber(input.value,Number(input.max)||1);input.value=target;report.goTo(target)}
 input.addEventListener('change',commitPage);input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();commitPage()}});
 const observer=new MutationObserver(()=>{if(!viewer.isConnected){report.destroy();file.onClose?.();observer.disconnect()}});observer.observe(root,{childList:true});
}

root.addEventListener('click',e=>{const button=e.target.closest('[data-upload-pdf-preview]');if(!button)return;const file=state.uploadItems.find(f=>f.key===button.dataset.uploadPdfPreview),local=localStudyFiles.get(button.dataset.uploadPdfPreview);if(file&&local)openPdfPreview({name:file.name,url:local.url,sample:false})});
