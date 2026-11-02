const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const A=require('../live/appraisal-source.js');
const source=fs.readFileSync(require.resolve('../live/live-app.js'),'utf8');
function boot(names,failStart=false){
 const calls=[],items=names.map(name=>({key:name,name,kind:/\.jpg$/.test(name)?'photos':'appraisal',size:100}));
 const L={},state={propertyName:'Fixture',uploadItems:items};let attempt=0;
 const api={createStudy:async()=>{calls.push('create');return{id:'study'}},registerDocument:async(id,body)=>{calls.push(['register',body.filename,body.content_type]);return{document:{id:body.filename},upload:{}}},completeDocument:async(id,doc)=>calls.push(['complete',doc]),start:async()=>{calls.push('start');if(failStart&&!attempt++)throw Error('Connection lost')}};
 const Live={inspectAppraisal:async f=>({kind:A.format(f)}),invalidate(){},route(){},go(){},errorText:e=>e.message};
 const ctx={BasisAppraisalSource:A,BasisApi:{newKey:()=>String(Math.random())},L,Live,api,state,PHOTO_TYPES:{jpg:'image/jpeg'},extension:n=>n.split('.').pop(),localStudyFiles:new Map(items.map(i=>[i.key,{file:{name:i.name,size:i.size}}])),toast:message=>calls.push(['toast',message]),render(){},paintUpload(){},sha256:async()=> 'digest',putSigned:async()=>{},C:{uploadConcurrency:3},exports:{}};
 vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf(' function uploadPlan(){'),source.indexOf(' function uploadSummary(){'))+'\nexports.start=startStudy;',ctx);
 return {ctx,calls,start:ctx.exports.start,Live};
}
test('XML is registered before companion PDF even when selected second; MIME types and completion precede start',async()=>{
 const {start,calls}=boot(['report.pdf','values.xml','photo.jpg']);await start();
 assert.deepEqual(calls.filter(x=>Array.isArray(x)&&x[0]==='register'),[['register','values.xml','application/xml'],['register','report.pdf','application/pdf'],['register','photo.jpg','image/jpeg']]);
 assert(calls.findIndex(x=>x==='start')>calls.findLastIndex(x=>x[0]==='complete'));
});
test('ZIP and legacy PDF use their correct registration type',async()=>{
 for(const name of ['delivery.zip','appraisal.pdf']){const {start,calls}=boot([name,'photo.jpg']);await start();assert.equal(calls.find(x=>x[0]==='register')[2],name.endsWith('.zip')?'application/zip':'application/pdf')}
});
test('invalid payload and multiple reports are rejected before creating a study',async()=>{
 const a=boot(['delivery.zip','photo.jpg']);a.Live.inspectAppraisal=async()=>{throw Error('Invalid package')};await a.start();assert(!a.calls.includes('create'));
 const b=boot(['a.pdf','b.pdf','photo.jpg']);await b.start();assert(!b.calls.includes('create'));
 const c=boot(['fake.pdf','photo.jpg']);c.Live.inspectAppraisal=async()=>({kind:'xml'});await c.start();assert(!c.calls.includes('create'));
});
test('a start retry reuses confirmed files and rejects changes to the primary selection',async()=>{
 const {start,calls,ctx}=boot(['values.xml','report.pdf','photo.jpg'],true);await start();await start();assert.equal(calls.filter(x=>x==='create').length,1);assert.equal(calls.filter(x=>x[0]==='register').length,3);
 const b=boot(['values.xml','photo.jpg'],true);await b.start();b.ctx.state.uploadItems[0].key='changed';b.ctx.localStudyFiles.set('changed',{file:{name:'values.xml',size:100}});await b.start();assert(b.calls.some(x=>x[0]==='toast'&&x[1].includes('original file selection')));
});
