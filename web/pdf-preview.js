/* Local PDF.js viewer shared by study files and unsent upload selections. */
'use strict';
let pdfModulePromise;
function previewPageNumber(value,total){return Math.max(1,Math.min(total,Math.round(Number(value)||1)))}
function previewZoom(value){return Math.max(.25,Math.min(3,Number(value)||1))}
async function openPdfPreview(file){
 const url=file.pdf||file.url;if(!url)return;
 const viewer=modal(esc(file.name),`<button class="pdf-close" data-close aria-label="Close PDF preview">${icon('x',19)}</button><div class="pdf-file-meta"><span>${file.sample?'Design sample · not a professional appraisal':'Local PDF · not uploaded'}</span><a href="${esc(url)}" download="${esc(file.name)}">${icon('download',15)}Download PDF</a></div><div class="pdf-controls"><div class="pdf-page-controls"><button data-pdf-action="previous" aria-label="Previous PDF page" disabled>${icon('back',16)}</button><label>Page<input id="pdf-page-number" aria-label="PDF page number" type="number" value="1" min="1" disabled></label><span class="pdf-page-total">of —</span><button data-pdf-action="next" aria-label="Next PDF page" disabled>${icon('arrow',16)}</button></div><div class="pdf-zoom-controls"><button data-pdf-action="out" aria-label="Zoom out PDF" disabled>−</button><span class="pdf-zoom-level">100%</span><button data-pdf-action="in" aria-label="Zoom in PDF" disabled>+</button><button class="pdf-fit" data-pdf-action="fit" disabled>Fit width</button></div></div><div class="pdf-status" role="status">Loading PDF…</div><div class="pdf-stage" aria-busy="true"><div class="pdf-paper"><canvas aria-label="PDF page preview"></canvas></div><div class="pdf-accessible-text sr-only"></div></div>`,'');
 const panel=viewer.querySelector('.modal');panel.classList.add('pdf-modal');
 const stage=viewer.querySelector('.pdf-stage'),canvas=viewer.querySelector('canvas'),status=viewer.querySelector('.pdf-status'),input=viewer.querySelector('#pdf-page-number');
 let doc,loadingTask,renderTask,page=1,scale=1,fit=true,generation=0,disposed=false;
 function controls(){
  input.value=page;input.max=doc.numPages;input.disabled=false;
  viewer.querySelector('.pdf-page-total').textContent='of '+doc.numPages;
  for(const button of viewer.querySelectorAll('[data-pdf-action]'))button.disabled=false;
  viewer.querySelector('[data-pdf-action=previous]').disabled=page===1;
  viewer.querySelector('[data-pdf-action=next]').disabled=page===doc.numPages;
  viewer.querySelector('[data-pdf-action=out]').disabled=!fit&&scale<=.25;
  viewer.querySelector('[data-pdf-action=in]').disabled=!fit&&scale>=3;
 }
 async function drawPage(){
  if(!doc||disposed)return;const version=++generation;stage.setAttribute('aria-busy','true');status.hidden=false;status.textContent='Rendering page '+page+'…';
  const previous=renderTask;if(previous){previous.cancel();try{await previous.promise}catch{/* Cancelled renders do not replace newer pages. */}}
  if(version!==generation||disposed)return;
  status.hidden=false;status.textContent='Rendering page '+page+'…';
  try{
   const pdfPage=await doc.getPage(page);if(version!==generation||disposed)return;
   const base=pdfPage.getViewport({scale:1});
   if(fit)scale=Math.max(.1,Math.min(3,(stage.clientWidth-48)/base.width));
   const viewport=pdfPage.getViewport({scale}),pixelRatio=Math.min(devicePixelRatio||1,2,Math.sqrt(16000000/(viewport.width*viewport.height)));
   canvas.hidden=false;canvas.width=Math.max(1,Math.floor(viewport.width*pixelRatio));canvas.height=Math.max(1,Math.floor(viewport.height*pixelRatio));
   canvas.style.width=viewport.width+'px';canvas.style.height=viewport.height+'px';canvas.setAttribute('aria-label',`${file.name}, page ${page} of ${doc.numPages}`);
   renderTask=pdfPage.render({canvasContext:canvas.getContext('2d'),viewport,transform:[pixelRatio,0,0,pixelRatio,0,0]});await renderTask.promise;
   if(version!==generation||disposed)return;status.textContent=`Page ${page} of ${doc.numPages}`;
   viewer.querySelector('.pdf-zoom-level').textContent=Math.round(scale*100)+'%';controls();
   const text=await pdfPage.getTextContent();if(version===generation&&!disposed){viewer.querySelector('.pdf-accessible-text').textContent=text.items.map(item=>item.str).join(' ');stage.dataset.renderedPage=String(page);stage.setAttribute('aria-busy','false');status.hidden=true;}
  }catch(error){if(error.name!=='RenderingCancelledException'&&!disposed&&version===generation){stage.setAttribute('aria-busy','false');canvas.hidden=true;status.hidden=false;status.textContent='This page could not be displayed. Download the PDF to open it on your computer.'}}
 }
 viewer.addEventListener('click',e=>{
  const action=e.target.closest('[data-pdf-action]')?.dataset.pdfAction;if(!action||!doc)return;
  if(action==='previous'||action==='next'){page=previewPageNumber(page+(action==='next'?1:-1),doc.numPages);stage.scrollTop=0}
  if(action==='out'||action==='in'){fit=false;scale=previewZoom(scale+(action==='in' ? .25 : -.25))}
  if(action==='fit')fit=true;
  controls();drawPage();
 });
 function commitPage(){if(doc){page=previewPageNumber(input.value,doc.numPages);controls();stage.scrollTop=0;drawPage()}}
 input.addEventListener('change',commitPage);input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();commitPage()}});
 const observer=new MutationObserver(()=>{if(!viewer.isConnected){disposed=true;generation++;renderTask?.cancel();loadingTask?.destroy().catch(()=>{});resize.disconnect();observer.disconnect()}});observer.observe(root,{childList:true});
 let lastWidth=stage.clientWidth;const resize=new ResizeObserver(()=>{const width=stage.clientWidth;if(fit&&width!==lastWidth){lastWidth=width;drawPage()}});resize.observe(stage);
 try{
  pdfModulePromise??=import('./assets/vendor/pdfjs/pdf.min.mjs');const pdfjs=await pdfModulePromise;if(disposed)return;
  pdfjs.GlobalWorkerOptions.workerSrc=new URL('./assets/vendor/pdfjs/pdf.worker.min.mjs',location.href).href;
  loadingTask=pdfjs.getDocument({url,standardFontDataUrl:new URL('./assets/vendor/pdfjs/standard_fonts/',location.href).href,cMapUrl:new URL('./assets/vendor/pdfjs/cmaps/',location.href).href,cMapPacked:true,wasmUrl:new URL('./assets/vendor/pdfjs/wasm/',location.href).href,isEvalSupported:false});doc=await loadingTask.promise;
  if(!disposed){page=previewPageNumber(file.initialPage||1,doc.numPages);controls();await drawPage()}
 }catch(error){if(!disposed){stage.setAttribute('aria-busy','false');canvas.hidden=true;status.hidden=false;status.textContent=error.name==='PasswordException'?'This PDF is password protected. Download it to open it securely on your computer.':'This PDF could not be opened. Try another file or download the original.'}}
}
root.addEventListener('click',e=>{const button=e.target.closest('[data-upload-pdf-preview]');if(!button)return;const file=state.uploadItems.find(f=>f.key===button.dataset.uploadPdfPreview),local=localStudyFiles.get(button.dataset.uploadPdfPreview);if(file&&local)openPdfPreview({name:file.name,url:local.url,sample:false})});
