const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function setup({pages=20,initialPage=1,documentPromise,loadPromise}={}){
 const events=new Map(),changes=[],errors=[],layers=[],instances=[];let destroyed=0,detached=0,disconnected=0,gesture;
 class EventBus{on(name,fn){events.set(name,fn)}emit(name,payload={}){events.get(name)?.(payload)}}
 class PDFViewer{
  constructor(options){this.options=options;this.currentPageNumber=1;this.currentScale=1;this.renderingQueue={};instances.push(this)}
  setDocument(doc){this.doc=doc;if(doc)this.options.eventBus.emit('pagesinit')}
  set currentScaleValue(value){this.fitValue=value;this.currentScale=1.25;this.options.eventBus.emit('scalechanging')}
  scrollPageIntoView({pageNumber}){this.currentPageNumber=pageNumber;this.options.eventBus.emit('pagechanging')}
  updateScale(options){this.lastZoom=options;this.currentScale*=options.scaleFactor;this.options.eventBus.emit('scalechanging')}
  update(){this.updated=true}
  getPageView(){return {textLayer:{div:'rendered text'}}}
 }
 const doc={numPages:pages},loadingTask={promise:documentPromise||Promise.resolve(doc),destroy:async()=>{destroyed++}};
 const pdfjs={getDocument:()=>loadingTask},load=()=>loadPromise||Promise.resolve({pdfjs,PDFViewer,EventBus});
 const stage={dataset:{},clientWidth:800,attrs:{},ownerDocument:{createElement:()=>({})},replaceChildren(node){this.children=[node]},setAttribute(k,v){this.attrs[k]=v}};
 const context=vm.createContext({module:{exports:{}},AbortController,clearTimeout,pdfDocumentOptions:url=>({url}),
  ResizeObserver:class{constructor(fn){this.callback=fn}observe(){}disconnect(){disconnected++}},
  BasisGestures:{center:()=>({x:400,y:300}),attach:(element,options)=>{gesture=options;return ()=>{detached++}}}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../pdf-continuous.js'),'utf8'),context);
 const api=context.module.exports;
 const report=api.create({stage,url:'blob:local-test',initialPage,load,onChange:c=>changes.push(c),onError:e=>errors.push(e),onTextLayer:l=>layers.push(l)});
 return {report,api,stage,changes,errors,layers,instances,events,stats:()=>({destroyed,detached,disconnected}),gesture:()=>gesture};
}
test('the shared viewer opens the entire document and starts at the requested cited page',async()=>{
 const p=setup({initialPage:7});await p.report.ready;
 const viewer=p.instances[0];assert.equal(viewer.doc.numPages,20);assert.equal(viewer.currentPageNumber,7);
 assert.equal(viewer.fitValue,'page-width');assert.equal(p.stage.dataset.pages,'20');
 assert.equal(p.stage.children[0].className,'pdfViewer basis-pdf-pages');
 p.report.destroy();
});
test('page jumps clamp to the document bounds and ordinary scrolling updates the current page',async()=>{
 const p=setup();await p.report.ready;
 p.report.goTo(20);assert.equal(p.stage.dataset.page,'20');
 p.report.goTo(999);assert.equal(p.stage.dataset.page,'20');
 p.report.goTo(-1);assert.equal(p.stage.dataset.page,'1');
 p.instances[0].currentPageNumber=11;p.events.get('pagechanging')();
 assert.equal(p.changes.at(-1).page,11);assert.equal(p.changes.at(-1).total,20);p.report.destroy();
});
test('trackpad zoom uses the cursor origin and bounded scale while retaining the document',async()=>{
 const p=setup();await p.report.ready;p.report.goTo(10);
 p.gesture().zoom(2,{x:320,y:440});assert.equal(p.stage.dataset.zoom,'2.5');
 assert.deepEqual(Array.from(p.instances[0].lastZoom.origin),[320,440]);
 assert.equal(p.instances[0].lastZoom.drawingDelay,120);
 p.report.zoom(100);assert.equal(p.stage.dataset.zoom,'3');p.report.zoom(.001);assert.equal(p.stage.dataset.zoom,'0.25');
 p.report.fitWidth();assert.equal(p.instances[0].fitValue,'page-width');assert.equal(p.stage.dataset.page,'10');p.report.destroy();
});
test('text events identify each rendered page so source highlighting stays on its cited page',async()=>{
 const p=setup();await p.report.ready;
 p.events.get('textlayerrendered')({pageNumber:12});assert.equal(p.layers[0].page,12);assert.equal(p.layers[0].total,20);
 p.events.get('textlayerrendered')({pageNumber:13,error:new Error('Failed')});assert.equal(p.layers.length,1);
 p.events.get('pagerendered')({pageNumber:12});assert.equal(p.stage.attrs['aria-busy'],'false');p.report.destroy();
});
test('closing or navigating cancels observers, gestures, rendering and the worker exactly once',async()=>{
 const p=setup();await p.report.ready;p.report.destroy();p.report.destroy();
 assert.equal(p.instances[0].doc,null);assert.equal(p.instances[0].options.abortSignal.aborted,true);
 assert.deepEqual(p.stats(),{destroyed:1,detached:1,disconnected:1});
 const count=p.changes.length;p.report.goTo(8);p.events.get('pagerendered')({pageNumber:8});assert.equal(p.changes.length,count);
});
test('closing during document loading cannot mount a stale report',async()=>{
 let resolve;const promise=new Promise(r=>{resolve=r}),p=setup({documentPromise:promise});
 await new Promise(setImmediate);p.report.destroy();resolve({numPages:20});await p.report.ready;
 assert.equal(p.instances[0].doc,null);assert.equal(p.stats().destroyed,1);assert.equal(p.changes.length,0);
});
test('password and document failures surface an error instead of a stuck loading state',async()=>{
 const error=Object.assign(new Error('Password needed'),{name:'PasswordException'});
 const p=setup({documentPromise:Promise.reject(error)});await p.report.ready;
 assert.equal(p.errors[0],error);assert.equal(p.stage.attrs['aria-busy'],'false');p.report.destroy();
});
test('a one-page PDF stays bounded and invalid input returns to page one',async()=>{
 const p=setup({pages:1,initialPage:50});await p.report.ready;p.report.goTo('invalid');
 assert.equal(p.stage.dataset.page,'1');assert.equal(p.stage.dataset.pages,'1');p.report.destroy();
});

test('closing before the viewer module loads does not create a worker or layout',async()=>{
 let resolve;const promise=new Promise(r=>{resolve=r}),p=setup({loadPromise:promise});
 p.report.destroy();resolve({});await p.report.ready;
 assert.equal(p.instances.length,0);assert.equal(p.stats().destroyed,0);assert.equal(p.errors.length,0);
});
