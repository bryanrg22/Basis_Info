const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function setup(){
 const rootEvents={},windowEvents={},messages=[],released=[];let renders=0;
 const context=vm.createContext({state:{uploads:[]},root:{addEventListener:(name,fn)=>rootEvents[name]=fn,querySelectorAll:()=>[],querySelector:()=>null},window:{addEventListener:(name,fn)=>windowEvents[name]=fn},URL:{createObjectURL:f=>'blob:'+f.name,revokeObjectURL:url=>released.push(url)},render:()=>renders++,toast:m=>messages.push(m)});
 vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../upload-review.js'),'utf8'),context);
 return {context,rootEvents,windowEvents,messages,released,renders:()=>renders};
}
const file=(name,type='application/pdf')=>({name,type,size:100,lastModified:1});
test('mixed intake groups photos, named appraisals and other documents without treating every PDF as an appraisal',()=>{
 const p=setup();p.context.selectStudyFiles([file('Property_Appraisal.PDF'),file('kitchen.jpg','image/jpeg'),file('Floor_plan.pdf'),file('Costs.xlsx','')]);
 assert.deepEqual(Array.from(p.context.state.uploadItems,f=>f.kind),['appraisal','photos','documents','documents']);
 assert.equal(p.renders(),1);assert.match(p.messages.at(-1),/4 files added/);
 assert.equal(p.context.uploadFileError({name:'blank.pdf',size:0}),'Empty files and folders cannot be added.');
 assert.match(p.context.uploadFileError(file('script.exe','application/octet-stream')),/Choose a PDF/);
});
test('duplicate detection retains distinct same-name files and reattaches saved metadata to reselected files',()=>{
 const p=setup(),photo=file('kitchen.jpg','image/jpeg');p.context.selectStudyFiles([photo,photo]);
 assert.equal(p.context.state.uploadItems.length,1);assert.match(p.messages.at(-1),/already added/);
 p.context.selectStudyFiles([{...photo,lastModified:2}]);assert.equal(p.context.state.uploadItems.length,2);
 p.windowEvents.pagehide();p.context.state.uploadItems[0].kind='documents';p.context.selectStudyFiles([photo]);
 assert.equal(p.context.state.uploadItems.length,2);assert.equal(p.context.state.uploadItems[0].kind,'documents');
 p.context.selectStudyFiles([]);assert.equal(p.renders(),3);
});
test('drop and browse share one intake path; outside file drops are prevented while text dragging remains untouched',()=>{
 const p=setup(),zone={dataset:{uploadZone:'all'},classList:{add(){},remove(){}}};let prevented=0;
 const transfer={types:['Files'],files:[file('a.pdf'),file('photo.webp','image/webp')],dropEffect:''};
 p.rootEvents.dragover({target:{closest:()=>zone},dataTransfer:transfer,preventDefault:()=>prevented++});assert.equal(transfer.dropEffect,'copy');
 p.rootEvents.drop({target:{closest:()=>zone},dataTransfer:transfer,preventDefault:()=>prevented++});assert.equal(p.context.state.uploadItems.length,2);
 p.rootEvents.change({target:{id:'study-file-input',matches:()=>false,files:[file('Appraisal.pdf')]}});assert.equal(p.context.state.uploadItems.length,3);
 p.windowEvents.drop({dataTransfer:transfer,preventDefault:()=>prevented++});assert.equal(prevented,3);
 p.rootEvents.drop({target:{closest:()=>null},dataTransfer:transfer,preventDefault:()=>prevented++});assert.equal(p.context.state.uploadItems.length,3);
 p.windowEvents.drop({dataTransfer:{types:['text/plain']},preventDefault:()=>assert.fail('Text dragging should be untouched')});
 p.windowEvents.pagehide({persisted:true});assert.equal(p.released.length,0);p.windowEvents.pagehide();assert.equal(p.released.length,3);
});
