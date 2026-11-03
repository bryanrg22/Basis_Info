// Photos on screen come from /image (an iPhone's HEIC as JPEG, which Chrome, Edge and
// Firefox cannot show as uploaded); downloads and the appraisal keep the original from
// /content (docs/api/PHOTOS.md, EVAL_REPORT 81).
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const H=require('../live/study-overview.js'),G=require('../live/gates.js');
function boot(){
 const calls=[];
 const reply=route=>(study,doc)=>{calls.push(`${route} ${study} ${doc}`);return Promise.resolve({blob:async()=>new Blob([`${route}:${doc}`])})};
 const client={documentContent:reply('content'),documentImage:reply('image')};
 const root={innerHTML:'',addEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],insertAdjacentHTML(){}};
 const globals={BasisErrorHelp:require('../live/error-help.js'),BasisStudyOverview:H,BasisGates:G,BASIS_CONFIG:{mode:'live',apiBase:'http://localhost:8000',firebase:{}},BasisApi:{createClient:()=>client,pollWorkflow:()=>({stop(){}})},BasisAuth:{init:()=>new Promise(()=>{}),onChange(){}},root,document:{documentElement:{dataset:{}}},state:{screen:'dashboard'},location:{hash:'#dashboard',href:'http://localhost:3100/?mode=live#dashboard'},sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},URL,Blob,Intl,esc:s=>String(s??'').replaceAll('<','&lt;').replaceAll('"','&quot;'),icon:()=>'',heading:(t,d='',a='')=>`<h1>${t}</h1><p>${d}</p>${a}`,statusBadge:s=>s,deskStatus:s=>s.status,toast(){}};
 for(const name of ['render','reviewbar','hasUnsavedDraft','presence','login','dashboard','studyLibrary','archiveStudy','upload','processing','studyFiles','activity'])globals[name]=()=>'';
 vm.createContext(globals);vm.runInContext(fs.readFileSync(path.join(__dirname,'../live/live-app.js'),'utf8'),globals);
 return {live:globals.BasisLive,calls};
}
test('a photo on screen is fetched from /image, the original from /content',async()=>{
 const {live,calls}=boot();
 const shown=await live.photoUrl('d1','s1'),original=await live.blobUrl('d1','s1');
 assert.deepEqual(calls,['image s1 d1','content s1 d1']);
 assert.notEqual(shown,original);
 assert.equal(await (await fetch(shown)).text(),'image:d1');
 assert.equal(await (await fetch(original)).text(),'content:d1');
});
test('each copy is fetched once, and a fetched photo renders with its src at once',async()=>{
 const {live,calls}=boot();
 assert.doesNotMatch(live.photo('d2','Kitchen'),/src=/);
 const [a,b]=await Promise.all([live.photoUrl('d2','s1'),live.photoUrl('d2','s1')]);
 assert.equal(a,b);assert.deepEqual(calls,['image s1 d2']);
 assert.match(live.photo('d2','Kitchen'),new RegExp(`src="${a}"`));
 await live.blobUrl('d2','s1');
 assert.match(live.photo('d2','Kitchen'),new RegExp(`src="${a}"`)); // the original never replaces the shown copy
});
