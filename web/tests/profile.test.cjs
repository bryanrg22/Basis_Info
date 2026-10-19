const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function load({saved={},width=400,height=200,broken=false}={}){
 const calls={revoked:[],draw:null};
 const members=[{id:'taylor',name:'Taylor Reed',initials:'TR'},{id:'alex',name:'Alex Morgan',initials:'AM'}];
 const context=vm.createContext({
  TEAM_MEMBERS:members,state:{},REVIEW:{screens:[['settings','Settings']]},root:{querySelector:()=>null},render:()=>{},
  localStorage:{getItem:key=>saved[key]??null},esc:String,
  URL:{createObjectURL:()=> 'blob:profile-test',revokeObjectURL:url=>calls.revoked.push(url)},
  Image:class{constructor(){this.naturalWidth=width;this.naturalHeight=height}set src(v){queueMicrotask(()=>broken?this.onerror():this.onload())}},
  document:{getElementById:()=>null,createElement:()=>({width:0,height:0,getContext:()=>({drawImage:(...args)=>calls.draw=args.slice(1)}),toDataURL:()=> 'data:image/webp;base64,fixture'})},
  previewUser:()=>members[0]
 });
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../profile-settings.js'),'utf8'),context);
 return {context,calls,members};
}
test('profile identities remain separate for each user and invalid stored values fall back',()=>{
 const {context,members}=load({saved:{'basis-profile-preview-v1:taylor':JSON.stringify({name:'Taylor Reed Jr.',photo:''}),'basis-profile-preview-v1:alex':'invalid json'}});
 assert.equal(context.profileForUser(members[0]).name,'Taylor Reed Jr.');
 assert.equal(context.profileForUser(members[0]).initials,'TR');
 assert.equal(context.profileForUser(members[1]).name,'Alex Morgan');
 assert.equal(context.profileAvatar({name:'Alex   Morgan'}),'AM');
});
test('profile photo validation rejects unsupported formats and oversized images before decoding',async()=>{
 const {context,calls}=load();
 await assert.rejects(context.readProfilePhoto({type:'image/svg+xml',size:50}),/JPG, PNG, or WebP/);
 await assert.rejects(context.readProfilePhoto({type:'image/png',size:5*1024*1024+1}),/smaller than 5 MB/);
 assert.equal(calls.revoked.length,0);
});
test('profile photo conversion center-crops a wide image and releases its temporary URL',async()=>{
 const {context,calls}=load();
 assert.equal(await context.readProfilePhoto({type:'image/png',size:500}),'data:image/webp;base64,fixture');
 assert.deepEqual(calls.draw,[100,0,200,200,0,0,256,256]);
 assert.deepEqual(calls.revoked,['blob:profile-test']);
});
test('unreadable images and excessive dimensions report errors and release temporary URLs',async()=>{
 const unreadable=load({broken:true});
 await assert.rejects(unreadable.context.readProfilePhoto({type:'image/png',size:500}),/could not be opened/);
 assert.equal(unreadable.calls.revoked.length,1);
 const large=load({width:10000,height:10000});
 await assert.rejects(large.context.readProfilePhoto({type:'image/jpeg',size:500}),/25 million/);
 assert.equal(large.calls.revoked.length,1);
});
