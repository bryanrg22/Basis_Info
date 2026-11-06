// The firm pages as rendered (live/firm-pages.js), for an admin and an engineer, on the sample
// API through the real API client; and against the sample with its PROPOSED section deleted, the
// API as it is today, where each page falls back and says so.
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const WEB=path.join(__dirname,'..'),NOW=Date.parse('2026-11-05T22:30:00Z');
const fetchAsset=p=>fs.readFileSync(path.join(WEB,p));
const read=f=>fs.readFileSync(path.join(WEB,f),'utf8');

function sampleModule(proposed=true){
 let src=read('live/sample-api.js');
 if(!proposed){const head=src.lastIndexOf('\n',src.indexOf('PROPOSED: not in the real API')),end=src.indexOf('/* END PROPOSED');src=src.slice(0,src.lastIndexOf('/*',head))+src.slice(src.indexOf('\n',end)+1)}
 const m={exports:{}};new Function('module','exports','require',src)(m,m.exports,require);return m.exports;
}
async function boot({role='engineer',proposed=true,onboarding}={}){
 const A=sampleModule(proposed),sample=A.create({now:()=>NOW,role,onboarding,fetchAsset});
 const api=require('../live/api-client.js').createClient({baseUrl:sample.base,fetchImpl:sample.fetch,getToken:async()=>'sample-token'});
 api.invitations=org=>api.request('GET',`/v1/orgs/${org}/invitations`);api.timeline=(id,q)=>api.request('GET',`/v1/studies/${id}/timeline`,{query:q});
 const cache=new Map(),events={},me=await api.request('GET','/v1/me');
 const F=require('../live/firm.js'),M=require('../live/firm-model.js'),firm=F.currentFirm(me,null);
 const load=(key,fn)=>{if(cache.has(key))return cache.get(key);const e={done:false};cache.set(key,e);e.promise=Promise.resolve().then(fn).then(d=>{e.data=d;e.done=true},err=>{e.error=err;e.done=true});return e};
 const Live={api,state:{cache,studyId:null},screens:{},actions:{},afterRender:[],load,invalidate:p=>{for(const k of [...cache.keys()])if(k.startsWith(p))cache.delete(k)},
  me:()=>me,firm:()=>firm,firmStudies:s=>s,loadDeliveries(){},deliveryPeek:s=>({label:'Signed',tone:'done'}),delivery:s=>({label:'Signed',tone:'done'}),
  route:(screen,id)=>`#${screen}${id?'/'+id:''}`,when:v=>v?String(v).slice(0,10):'—',loading:()=>'<div class="live-state">Loading</div>',failed:e=>`<div role="alert">${e?.title||e?.message}</div>`,render(){},refreshMe:async()=>me};
 // The page reads the time from Date: pin it to the sample's now, as a capture pins the browser clock.
 class FixedDate extends Date{constructor(...a){super(...(a.length?a:[NOW]))}static now(){return NOW}}
 const g={BASIS_CONFIG:{mode:'live'},BasisLive:Live,BasisFirm:F,BasisFirmModel:M,Date:FixedDate,Intl,URLSearchParams,Promise,
  icon:()=>'<svg></svg>',esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),toast(){},modal(){return {querySelector:()=>null,remove(){}}},closeBtn:'',
  root:{addEventListener(name,fn){(events[name]||=[]).push(fn)},querySelector:()=>null},document:{addEventListener(){}},state:{screen:'dashboard'},location:{hash:''}};
 g.globalThis=g;vm.createContext(g);vm.runInContext(read('live/firm-pages.js'),g);
 // Render until every load the page starts has answered.
 async function page(name){let html;for(let i=0;i<12;i++){g.state.screen=name;html=Live.screens[name]();const open=[...cache.values()].filter(e=>!e.done);if(!open.length&&!/live-state/.test(html))break;await Promise.all(open.map(e=>e.promise))}return html}
 return {Live,page,events,g,me,M};
}
const ids=/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/;
const visible=html=>html.replace(/<[^>]+>/g,' ');

test('Home: an engineer sees their own waiting studies and the firm’s activity; an admin sees every waiting study with its engineer',async()=>{
 const e=await boot({role:'engineer'}),home=await e.page('dashboard');
 assert.match(home,/Good (morning|afternoon|evening), Jordan/);
 for(const name of ['Harbor House','Maple Row Lodge','Bayview Duplex','Linden Avenue Townhomes'])assert.match(home,new RegExp(name));
 assert.doesNotMatch(home.split('fp-feed')[0],/Juniper Flats/,'a colleague’s review is not in Needs you');
 assert.match(home,/href="#components\/[^"]+"/,'a waiting review opens at its step');
 assert.match(home,/Firm activity/);assert.doesNotMatch(visible(home),ids,'no id is shown as text');
 const a=await boot({role:'admin'}),ah=await a.page('dashboard');
 assert.match(ah,/Needs attention/);assert.match(ah,/Juniper Flats/);assert.match(ah,/fp-row-owner/);
 assert.ok(ah.indexOf('Granite Peak Office')<ah.indexOf('Juniper Flats'),'the failed study leads');
});

test('Library: a client’s name finds its study and says the field; the archive is its own filter',async()=>{
 const e=await boot(),lib=await e.page('library');
 assert.match(lib,/Search address, client, file number/);
 assert.ok(!/Pinecrest Bungalow/.test(lib.split('fp-lib-table')[1]),'archived studies are not in All');
 for(const fn of e.events.input)fn({target:{id:'fp-search',value:'Harbor Property Partners'}});
 const hit=await e.page('library'),rows=hit.split('<tbody>')[1];
 assert.equal((rows.match(/<tr /g)||[]).length,1);assert.match(rows,/<mark>Harbor<\/mark> House<\/a>/);assert.match(rows,/<td><mark>Harbor<\/mark> <mark>Property<\/mark> <mark>Partners<\/mark><\/td>/,"the client column marks the words");
 for(const fn of e.events.input)fn({target:{id:'fp-search',value:'Pinecrest'}});
 const none=await e.page('library');assert.match(none,/No active study matches “Pinecrest”/);assert.match(none,/1 in Archived/);assert.match(none,/data-value="archived">Show archived \(1\)/);
 e.Live.actions['fp-lib-filter'](null,'archived');e.g.state.screen='library';
 assert.match(await e.page('library'),/Pinecrest<\/mark> Bungalow/);
});

test('Activity: day groups, colleagues by name, each entry linking to its study and step',async()=>{
 const e=await boot(),act=await e.page('activity');
 assert.match(act,/<h2 id="fp-day-[^"]+">Today<\/h2>/);assert.match(act,/>Yesterday<\/h2>/);
 assert.match(act,/Alex Morgan<\/strong> approved Components on <a href="#components\/[^"]+">Orchard Street Fourplex<\/a>/);
 assert.doesNotMatch(visible(act),ids);assert.doesNotMatch(act,/activity\.completed|stage\.entered|run\.started/);
 e.Live.actions['fp-act-who'](null,'me');
 const mine=await e.page('activity');assert.ok(!/<strong>Priya Shah<\/strong>/.test(mine),'Me shows only mine');
});

test('Team: an admin manages roles and invitations; an engineer sees colleagues and nothing to manage',async()=>{
 const a=await boot({role:'admin'}),t=await a.page('team');
 assert.match(t,/data-live-role=/);assert.match(t,/data-live="invite-member"/);assert.match(t,/Pending · emailed/);assert.match(t,/Not set/,'a member without signer details says so');
 const e=await boot({role:'engineer'}),c=await e.page('team');
 assert.doesNotMatch(c,/data-live-role|invite-member|Remove/);assert.match(c,/Priya Shah/);assert.match(c,/PE · CA PE-71324/);assert.match(c,/Alex Morgan manages invitations and roles/);
});

test('Settings: firm and role read-only, signer details editable, no New design switch; the admin edits the letterhead',async()=>{
 const e=await boot(),s=await e.page('settings');
 assert.match(s,/id="fp-signer-name" value="Jordan Lee"/);assert.match(s,/id="fp-signer-license" value="PE-77120"/);assert.match(s,/<option value="CA" selected>/);
 assert.doesNotMatch(s,/New design/);assert.doesNotMatch(s,/data-fp-form="letterhead"/);assert.match(s,/400 Example Way/);
 const a=await boot({role:'admin'});assert.match(await a.page('settings'),/data-fp-form="letterhead"/);
});

test('the admin of a new firm gets the setup checklist; nothing else is invented',async()=>{
 const a=await boot({role:'admin',onboarding:'new-firm'}),h=await a.page('dashboard');
 assert.match(h,/Set up Hale Cost Engineering/);assert.match(h,/0 of 4 done/);assert.match(h,/has no studies yet/);
});

test('against today’s API (no PROPOSED section) each page falls back and says so',async()=>{
 const e=await boot({proposed:false});
 assert.match(await e.page('library'),/Search by property name or engineer/);
 const act=await e.page('activity');assert.match(act,/Your own reviews, uploads and signatures/);assert.doesNotMatch(act,/<strong>Priya Shah<\/strong>/);
 const team=await e.page('team');assert.match(team,/Showing the people on the firm’s studies/);
 const s=await e.page('settings');assert.match(s,/engineer of record, which an admin edits/);assert.match(s,/readonly/);
 assert.match(await e.page('dashboard'),/Your activity/);
});
