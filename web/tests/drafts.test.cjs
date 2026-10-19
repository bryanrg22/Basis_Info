const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../draft-review.js'),'utf8');
function preview(){
 const records=new Map(),events=new Set(),messages=[];
 let storageBlocked=false;
 const bar={dataset:{},classList:{toggle(){}},innerHTML:''};
 const state={screen:'upload',role:'Owner',propertyName:'Harbor House',uploads:[],group:{},removed:new Set(),verifiedFields:new Set(),corrections:{},photoReviewed:false,selectedAsset:0};
 const context=vm.createContext({state,root:{querySelector(s){return s==='.draft-status'?bar:null},addEventListener(){}},document:{activeElement:null},window:{addEventListener(name){events.add(name)},removeEventListener(name){events.delete(name)}},localStorage:{getItem(k){return records.get(k)||null},setItem(k,v){if(storageBlocked)throw Error('Storage unavailable');records.set(k,v)}},render(){},navigate(){},toast(message){messages.push(message)},queueMicrotask,clearSidebarHoverTimers(){},updateSidebarAppearance(){}});
 vm.runInContext(source,context);
 return {context,records,events,messages,blockStorage(){storageBlocked=true}};
}
test('editing and reverting a form updates dirty state and unload protection',()=>{
 const p=preview();assert.equal(p.context.hasUnsavedDraft(),false);
 p.context.state.propertyName='Edited';p.context.syncDraftStatus();
 assert.equal(p.context.hasUnsavedDraft(),true);assert.equal(p.events.has('beforeunload'),true);
 p.context.state.propertyName='Harbor House';p.context.syncDraftStatus();
 assert.equal(p.context.hasUnsavedDraft(),false);assert.equal(p.events.has('beforeunload'),false);
});
test('storage failure retains unsaved work and does not mark it saved',()=>{
 const p=preview();p.context.state.propertyName='Important edit';p.context.syncDraftStatus();p.blockStorage();
 assert.equal(p.context.saveLocalDraft(),false);assert.equal(p.context.hasUnsavedDraft(),true);
 assert.equal(p.context.state.propertyName,'Important edit');assert.equal(p.events.has('beforeunload'),true);assert.equal(p.records.size,0);
});
test('saving and reloading restores form and review edits',()=>{
 const p=preview();p.context.state.propertyName='Saved study';p.context.state.removed.add('pendants');p.context.state.assetEdits[0]={quantity:'27'};
 assert.equal(p.context.saveLocalDraft(),true);assert.equal(p.context.hasUnsavedDraft(),false);
 p.context.state.propertyName='Temporary edit';p.context.loadLocalDraft();
 assert.equal(p.context.state.propertyName,'Saved study');assert.equal(p.context.state.removed.has('pendants'),true);assert.equal(p.context.state.assetEdits[0].quantity,'27');assert.equal(p.context.hasUnsavedDraft(),false);
});
test('an invalid saved draft falls back to a usable clean editor',()=>{
 const p=preview();p.records.set('basis-design-preview:draft:v1:Owner:sample-study','{invalid JSON');
 assert.doesNotThrow(()=>p.context.loadLocalDraft());assert.equal(p.context.state.propertyName,'Harbor House');assert.equal(p.context.hasUnsavedDraft(),false);
 p.records.set('basis-design-preview:draft:v1:Owner:sample-study',JSON.stringify({version:1,data:{propertyName:42}}));
 assert.doesNotThrow(()=>p.context.loadLocalDraft());assert.equal(p.context.hasUnsavedDraft(),false);
});
test('owner and engineer previews keep separate local drafts',()=>{
 const p=preview();p.context.state.propertyName='Owner draft';p.context.saveLocalDraft();
 p.context.state.role='Engineer';p.context.render();assert.equal(p.context.state.propertyName,'Harbor House');
 p.context.state.propertyName='Engineer draft';p.context.saveLocalDraft();
 p.context.state.role='Owner';p.context.render();assert.equal(p.context.state.propertyName,'Owner draft');assert.equal(p.records.size,2);
});
test('equivalent field values do not become dirty because key order changed',()=>{
 const p=preview();p.context.state.assetEdits[0]={quantity:'27',unit:'each'};p.context.saveLocalDraft();
 p.context.state.assetEdits[0]={unit:'each',quantity:'27'};assert.equal(p.context.hasUnsavedDraft(),false);
});
test('client name participates in save, restore, discard and old draft compatibility',()=>{
 const p=preview();
 p.context.state.clientName='Client A';assert.equal(p.context.hasUnsavedDraft(),true);
 const before=p.context.draftSnapshot();
 p.context.state.clientName='Temporary client';p.context.restoreDraft(before);
 assert.equal(p.context.state.clientName,'Client A');
 p.context.saveLocalDraft();p.context.state.clientName='Client B';p.context.loadLocalDraft();
 assert.equal(p.context.state.clientName,'Client A');assert.equal(p.context.hasUnsavedDraft(),false);
 const legacy=p.context.draftSnapshot();delete legacy.clientName;
 assert.equal(p.context.validDraft(legacy),true);p.context.restoreDraft(legacy);
 assert.equal(p.context.state.clientName,'');
 assert.equal(p.context.validDraft({...legacy,clientName:42}),false);
});
test('unified file names and corrected groups survive draft save and restore without file bytes',()=>{
 const p=preview();p.context.state.uploadItems=[{key:'photo-key',name:'photo.jpg',size:100,kind:'documents'}];
 assert.equal(p.context.hasUnsavedDraft(),true);p.context.saveLocalDraft();
 p.context.state.uploadItems=[];p.context.loadLocalDraft();
 assert.equal(p.context.state.uploadItems[0].kind,'documents');assert.equal(p.context.hasUnsavedDraft(),false);
 const old=p.context.draftSnapshot();delete old.uploadItems;assert.equal(p.context.validDraft(old),true);
 p.context.restoreDraft(old);assert.equal(p.context.state.uploadItems.length,0);
});
test('property address survives save, restore and legacy drafts without replacing study or client names',()=>{
 const p=preview();p.context.state.clientName='Client A';p.context.state.propertyAddress='1200 Harbor Boulevard, Ventura, CA 93001';
 assert.equal(p.context.hasUnsavedDraft(),true);p.context.saveLocalDraft();
 p.context.state.propertyAddress='Manual address';p.context.loadLocalDraft();
 assert.equal(p.context.state.propertyAddress,'1200 Harbor Boulevard, Ventura, CA 93001');
 assert.equal(p.context.state.propertyName,'Harbor House');assert.equal(p.context.state.clientName,'Client A');
 const old=p.context.draftSnapshot();delete old.propertyAddress;
 assert.equal(p.context.validDraft(old),true);p.context.restoreDraft(old);assert.equal(p.context.state.propertyAddress,'');
 assert.equal(p.context.validDraft({...old,propertyAddress:42}),false);
});
test('component corrections and unfinished input survive draft save/load; unchanged editors do not become dirty',()=>{
 const p=preview();const initial={label:'Pendant lights',count:'3',material:'Metal / glass'};
 p.context.state.componentEditor={id:'pendants',...initial,initial:{...initial}};assert.equal(p.context.hasUnsavedDraft(),false);
 p.context.state.componentEditor.count='';assert.equal(p.context.hasUnsavedDraft(),true);p.context.saveLocalDraft();
 p.context.state.componentEditor=null;p.context.loadLocalDraft();assert.equal(p.context.state.componentEditor.count,'');
 p.context.state.componentEditor=null;p.context.state.componentEdits.pendants={label:'Pendant fixtures',count:4,material:'Brass'};
 p.context.saveLocalDraft();p.context.state.componentEdits={};p.context.loadLocalDraft();assert.equal(p.context.state.componentEdits.pendants.count,4);
 const legacy=p.context.draftSnapshot();delete legacy.componentEdits;delete legacy.componentEditor;
 assert.equal(p.context.validDraft(legacy),true);p.context.restoreDraft(legacy);assert.equal(Object.keys(p.context.state.componentEdits).length,0);
 assert.equal(p.context.validDraft({...legacy,componentEdits:{bad:{label:'Bad',count:-1,material:''}}}),false);
});
