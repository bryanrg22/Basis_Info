/* Shared continuous document surface. PDF.js owns page layout, visibility,
 * rendering priority and canvas eviction; Basis owns controls and source focus. */
(function(scope){
 'use strict';
 let viewerModulePromise;
 async function loadViewer(){
  const pdfjs=await loadPdfModule();
  viewerModulePromise??=import('./assets/vendor/pdfjs/pdf_viewer.mjs');
  const viewer=await viewerModulePromise.catch(error=>{viewerModulePromise=undefined;throw error});
  return {pdfjs,...viewer};
 }
 const pageNumber=(value,total)=>Math.max(1,Math.min(total,Math.round(Number(value)||1)));
 const zoomScale=value=>Math.max(.25,Math.min(3,Number(value)||1));
 function create({stage,url,initialPage=1,onChange=()=>{},onTextLayer=()=>{},onError=()=>{},load=loadViewer}){
  let pdfViewer,loadingTask,doc,disposed=false,initialized=false,fit=true,resize,stopGestures;
  const abort=new AbortController(),pages=stage.ownerDocument.createElement('div');
  pages.className='pdfViewer basis-pdf-pages';stage.replaceChildren(pages);
  stage.setAttribute('aria-busy','true');
  function changed(){
   if(disposed||!initialized)return;
   stage.dataset.page=String(pdfViewer.currentPageNumber);
   stage.dataset.pages=String(doc.numPages);
   stage.dataset.zoom=String(pdfViewer.currentScale);
   onChange({page:pdfViewer.currentPageNumber,total:doc.numPages,scale:pdfViewer.currentScale});
  }
  function goTo(value){
   if(!initialized||disposed)return;
   pdfViewer.scrollPageIntoView({pageNumber:pageNumber(value,doc.numPages)});changed();
  }
  function zoom(factor,point=BasisGestures.center(stage)){
   if(!initialized||disposed)return;
   const scale=pdfViewer.currentScale,next=zoomScale(scale*factor);if(next===scale)return;
   fit=false;pdfViewer.updateScale({scaleFactor:next/scale,origin:[point.x,point.y],drawingDelay:120});changed();
  }
  function fitWidth(){if(initialized&&!disposed){fit=true;pdfViewer.currentScaleValue='page-width';changed()}}
  const ready=(async()=>{
   try{
    const {pdfjs,PDFViewer,EventBus}=await load();if(disposed)return;
    const eventBus=new EventBus();
    pdfViewer=new PDFViewer({container:stage,viewer:pages,eventBus,abortSignal:abort.signal,
     removePageBorders:false,textLayerMode:1,annotationMode:0,annotationEditorMode:-1,
     enableAutoLinking:false,maxCanvasPixels:16000000,enableDetailCanvas:false});
    eventBus.on('pagesinit',()=>{
     if(disposed)return;initialized=true;pdfViewer.currentScaleValue='page-width';goTo(initialPage);
    });
    eventBus.on('pagechanging',changed);eventBus.on('scalechanging',changed);
    eventBus.on('pagerendered',event=>{
     if(disposed)return;
     stage.setAttribute('aria-busy','false');
     if(event.error)onError(event.error);else {stage.dataset.renderedPage=String(event.pageNumber);changed()}
    });
    eventBus.on('textlayerrendered',event=>{
     if(disposed||event.error)return;
     const layer=pdfViewer.getPageView(event.pageNumber-1)?.textLayer?.div;
     if(layer)onTextLayer({page:event.pageNumber,total:doc.numPages,layer});
    });
    loadingTask=pdfjs.getDocument(pdfDocumentOptions(url));doc=await loadingTask.promise;
    if(disposed)return;
    pdfViewer.setDocument(doc);
    pdfViewer.pagesPromise?.catch(error=>{if(!disposed){stage.setAttribute('aria-busy','false');onError(error)}});
    stopGestures=BasisGestures.attach(stage,{canZoom:()=>initialized&&!disposed,zoom});
    let width=stage.clientWidth;
    resize=new ResizeObserver(()=>{
     if(disposed||stage.clientWidth===width)return;width=stage.clientWidth;
     if(fit)fitWidth();else pdfViewer.update();
    });resize.observe(stage);
   }catch(error){if(!disposed){stage.setAttribute('aria-busy','false');onError(error)}}
  })();
  function destroy(){
   if(disposed)return;disposed=true;stopGestures?.();resize?.disconnect();
   pdfViewer?.setDocument(null);abort.abort();
   // PDFViewer's idle timer is separate from its document/scroll listeners.
   if(pdfViewer?.renderingQueue)clearTimeout(pdfViewer.renderingQueue.idleTimeout);
   loadingTask?.destroy().catch(()=>{});
  }
  return {ready,goTo,zoom,fitWidth,destroy};
 }
 const api={create,pageNumber,zoomScale};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisPdfViewer=api;
})(typeof globalThis==='object'?globalThis:this);
