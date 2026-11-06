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
// fail: {"<path regex>": status} answers those GETs with that status (a transient 503, a missing 404).
// intercept(path, query, init): a body answers 200; a Response is returned as it is.
// team: also run live/live-team.js (the join page); report: also run live/live-report.js (signing).
async function boot({role='engineer',proposed=true,onboarding,fail={},intercept=null,team=false,report=false}={}){
 const A=sampleModule(proposed),sample=A.create({now:()=>NOW,role,onboarding,fetchAsset});
 const fetchImpl=async(url,init={})=>{const u=new URL(String(url)),path=u.pathname;if(intercept){const body=intercept(path,u.searchParams,init);if(body instanceof Response)return body;if(body)return new Response(JSON.stringify(body),{status:200,headers:{'content-type':'application/json'}})}for(const [re,status] of Object.entries(fail))if(new RegExp(re).test(path)&&(!init.method||init.method==='GET'))return new Response(JSON.stringify({type:'https://basis.invalid/problems/unavailable',title:'Unavailable',status,detail:'Injected by the test.'}),{status,headers:{'content-type':'application/problem+json'}});return sample.fetch(url,init)};
 const api=require('../live/api-client.js').createClient({baseUrl:sample.base,fetchImpl,getToken:async()=>'sample-token'});
 api.invitations=org=>api.request('GET',`/v1/orgs/${org}/invitations`);api.timeline=(id,q)=>api.request('GET',`/v1/studies/${id}/timeline`,{query:q});
 const cache=new Map(),events={},me=await api.request('GET','/v1/me');
 const F=require('../live/firm.js'),M=require('../live/firm-model.js'),firm=F.currentFirm(me,null);
 const load=(key,fn)=>{if(cache.has(key))return cache.get(key);const e={done:false};cache.set(key,e);e.promise=Promise.resolve().then(fn).then(d=>{e.data=d;e.done=true},err=>{e.error=err;e.done=true});return e};
 const Live={api,state:{cache,studyId:null},screens:{},actions:{},afterRender:[],load,invalidate:p=>{for(const k of [...cache.keys()])if(k.startsWith(p))cache.delete(k)},
  me:()=>me,firm:()=>firm,firmStudies:s=>s,loadDeliveries(){},deliveryPeek:s=>({label:'Signed',tone:'done'}),delivery:s=>({label:'Signed',tone:'done'}),
  route:(screen,id)=>`#${screen}${id?'/'+id:''}`,guarded:async(name,fn)=>fn(),when:v=>v?String(v).slice(0,10):'—',loading:()=>'<div class="live-state">Loading</div>',failed:e=>`<div role="alert">${e?.title||e?.message}</div>`,render(){},refreshMe:async()=>me};
 // The page reads the time from Date: pin it to the sample's now, as a capture pins the browser clock.
 class FixedDate extends Date{constructor(...a){super(...(a.length?a:[NOW]))}static now(){return NOW}}
 const g={BASIS_CONFIG:{mode:'live'},BasisLive:Live,BasisFirm:F,BasisFirmModel:M,Date:FixedDate,Intl,setTimeout,URLSearchParams,Promise,
  icon:()=>'<svg></svg>',esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),toasts:[],toast(t){g.toasts.push(t)},modals:[],closeBtn:'',
  // A dialog: its HTML is kept; its inputs answer with the value the HTML gives them, or a value the test set.
  modal(title,html){const typed={},m={title,html,typed,removed:false,go:{},querySelector(sel){if(sel==='#live-sign-attest')return {checked:true};if(sel==='#live-sign-go')return m.go;const id=sel.slice(1),v=new RegExp(`id="${id}" value="([^"]*)"`).exec(html);return v||id in typed?{value:id in typed?typed[id]:v[1].replace(/&#39;/g,"'").replace(/&quot;/g,'"').replace(/&amp;/g,'&')}:null},remove(){m.removed=true}};g.modals.push(m);return m},
  root:{addEventListener(name,fn){(events[name]||=[]).push(fn)},querySelector:()=>null},document:{addEventListener(){}},state:{screen:'dashboard'},location:{hash:''}};
 g.globalThis=g;vm.createContext(g);
 if(team){g.REVIEW={screens:[]};g.logo=()=>'<a class="logo"></a>';g.heading=(t,d)=>`<h1>${t}</h1><p>${d}</p>`;g.localStorage={getItem:()=>null,setItem(){}};Live.state.user={uid:'u',email:me.user.email};cache.set('me',{done:true,data:me});vm.runInContext(read('live/live-team.js'),g)}
 vm.runInContext(read('live/firm-pages.js'),g);
 if(report){g.BasisReport=require('../live/report.js');Live.withStudy=fn=>'';Live.steps=()=>'';Live.errorText=e=>e?.message||'';vm.runInContext(read('live/live-report.js'),g)}
 // Render until every load the page starts has answered.
 async function page(name){let html;for(let i=0;i<12;i++){g.state.screen=name;html=Live.screens[name]();const open=[...cache.values()].filter(e=>!e.done);if(!open.length&&!/live-state/.test(html))break;await Promise.all(open.map(e=>e.promise))}return html}
 return {Live,page,events,g,me,M,sample,api};
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
 // Basis set the firm up and invited its owner: the activity says so, not that Morgan created it.
 assert.match(h,/<strong>Basis<\/strong> <a [^>]*>created Hale Cost Engineering/);assert.match(h,/<strong>Morgan Hale<\/strong> <a [^>]*>joined the firm as an admin/);assert.doesNotMatch(h,/Morgan Hale<\/strong> <a [^>]*>created/);
});

test('against today’s API (no PROPOSED section) each page falls back and says so',async()=>{
 const e=await boot({proposed:false});
 assert.match(await e.page('library'),/Search by property name or engineer/);
 const act=await e.page('activity');assert.match(act,/Your own reviews, uploads and signatures/);assert.doesNotMatch(act,/<strong>Priya Shah<\/strong>/);
 const team=await e.page('team');assert.match(team,/Showing the people on the firm’s studies/);
 const s=await e.page('settings');assert.match(s,/You enter your name, credentials and license when you sign a report/);assert.doesNotMatch(s,/data-fp-form="signer"|readonly/,'no form that cannot be saved');
 assert.match(await e.page('dashboard'),/Your activity/);
});

/* ---------- the code review's findings (Sol, round 6): each fails on the code before its fix ---------- */
test('onboarding without personal signer details (today’s API) still has a way on',async()=>{
 const join=async proposed=>{const e=await boot({proposed,role:'engineer',onboarding:'invited',team:true});const inv=e.me.pending_invitations[0];e.Live.gate();
  await e.Live.actions['accept-invite'](null,inv.id);let html;for(let i=0;i<8;i++){html=e.Live.gate();const open=[...e.Live.state.cache.values()].filter(x=>!x.done);if(!open.length)break;await Promise.all(open.map(x=>x.promise))}return html};
 const today=await join(false);
 assert.doesNotMatch(today,/data-fp-form="signer"|readonly/);assert.match(today,/You enter your name, credentials and license when you sign a report/);assert.match(today,/data-live="open-firm"/);
 const proposed=await join(true);assert.match(proposed,/Step 2 of 2/);assert.match(proposed,/data-fp-form="signer"/);
});
test('a letterhead that could not be read is not offered as an empty form, and is not saved over',async()=>{
 const a=await boot({role:'admin',fail:{'/profile$':503}}),s=await a.page('settings');
 assert.doesNotMatch(s,/data-fp-form="letterhead"/);assert.match(s,/could not be read/);
 let sent=null;const put=a.api.request;a.api.request=(m,p,o)=>{if(m==='PUT')sent=[m,p];return put(m,p,o)};
 const form={querySelector:id=>({'#fp-firm-name':{value:'X'}}[id]||{value:''})};
 for(const fn of a.events.submit||[])fn({target:{closest:()=>({dataset:{fpForm:'letterhead'},querySelector:form.querySelector})},preventDefault(){},stopImmediatePropagation(){}});
 await new Promise(r=>setTimeout(r,20));assert.equal(sent,null,'nothing is PUT over an unread profile');
});
test('a signer read that fails is an error, never the engineer of record’s details',async()=>{
 const e=await boot({fail:{'/v1/me/signer$':503}});await e.page('settings');
 const m=e.Live.mySigner();assert.ok(m.error);assert.equal(m.signer,null);
 assert.doesNotMatch(await e.page('settings'),/value="Alex Morgan"/);
 const missing=await boot({proposed:false});await missing.page('settings');assert.equal(missing.Live.mySigner().own,false,'a missing route still falls back');
});
test('your activity, the fallback, keeps to the firm on screen',async()=>{
 const e=await boot({proposed:false});
 const orig=e.api.request;e.api.request=async(m,p,o)=>{const r=await orig(m,p,o);if(p==='/v1/me/activity')r.items=[{...r.items[0],seq:9999,event_type:'review.decided',stage:'rooms_review',after:{decision:'approve'},detail:{decision:'approve',corrections:0},actor_type:'engineer',study_id:'other-firm-study',property_name:'Another Firm’s Tower',at:new Date(NOW-1000).toISOString()},...r.items];return r};
 const act=await e.page('activity');assert.doesNotMatch(act,/Another Firm’s Tower/);
});
test('a study’s activity reads every page of its timeline',async()=>{
 let pages=0;
 const e=await boot({intercept:(path,q)=>{if(!/\/studies\/x\/timeline$/.test(path))return null;pages++;
  if(!q.get('cursor'))return {study_id:'x',items:Array.from({length:3},(_,i)=>({seq:i+1,at:new Date(NOW-9e6+i).toISOString(),event_type:'activity.completed',actor_type:'system'})),next_cursor:'p2'};
  return {study_id:'x',items:[{seq:9,at:new Date(NOW-1000).toISOString(),event_type:'report.signed',actor_type:'engineer',actor_id:'sample-user-2',stage:'completed',revision:2}],next_cursor:null}}});
 const s={id:'x',property_name:'X',updated_at:'u',org_id:'o'};e.Live.studyActivity(s);await new Promise(r=>setTimeout(r,30));
 assert.equal(pages,2,'both pages are read');assert.match(e.Live.studyActivity(s),/signed revision 2/,'the latest event, on the second page, is shown');
});
test('opening Home or Activity reads the feed once per visit, and never renders inside a render (it recursed without end)',async()=>{
 let feeds=0;const e=await boot({intercept:(path)=>{if(/\/feed$/.test(path))feeds++;return null}});let renders=0;e.Live.render=()=>{renders++};
 const hooks=()=>{for(const h of e.Live.afterRender)h()};
 await e.page('activity');hooks();assert.equal(feeds,1,'the first visit reads the feed once');
 await e.page('activity');hooks();assert.equal(feeds,1,'a re-render in the same visit reads nothing again');
 e.g.state.screen='library';hooks();
 await e.page('activity');hooks();assert.equal(feeds,2,'the next visit reads it once more');
 await e.page('dashboard');hooks();assert.equal(feeds,3,'so does opening Home');
 assert.equal(renders,0,'no hook renders');
});

/* ---------- the code review's findings (Opus): each fails on the code before its fix ---------- */
const linden=async e=>{const all=await e.api.listAll('/v1/studies',{},{limit:100});return all.items.find(s=>s.property_name==='Linden Avenue Townhomes')};
async function signDialog({proposed,role='engineer',typed={},hold=false}){
 let sent=null;const e=await boot({proposed,role,report:true,intercept:(path,q,init)=>{if(/\/report\/sign$/.test(path)&&init.method==='POST'){sent=JSON.parse(init.body);return {}}return null}});
 const s=await linden(e);
 for(const [k,url] of [['report-state:'+s.id+':'+s.revision,`/v1/studies/${s.id}/report/state`],['report-answers:'+s.id,`/v1/studies/${s.id}/report-answers`]])e.Live.state.cache.set(k,{done:true,data:await e.api.request('GET',url)});
 Object.assign(e.Live.state,{study:s,studyId:s.id,reportPreview:{studyId:s.id,revision:s.revision,tag:'"'+'a'.repeat(64)+'"'}});
 if(!hold){e.Live.mySigner(s.org_id);await Promise.all([...e.Live.state.cache.values()].filter(x=>!x.done).map(x=>x.promise));e.Live.mySigner(s.org_id);await Promise.all([...e.Live.state.cache.values()].filter(x=>!x.done).map(x=>x.promise))}
 e.Live.actions['report-sign']();const m=e.g.modals.at(-1);
 if(m){Object.assign(m.typed,typed);await m.go.onclick?.();await new Promise(r=>setTimeout(r,10))}
 return {e,m,sent:()=>sent,s};
}
test('signing on today’s API: the engineer types their own details; the engineer of record’s are never sent for them',async()=>{
 const {m,sent}=await signDialog({proposed:false,typed:{'live-sign-cred':'PE','live-sign-license':'PE-77120','live-sign-state':'ca'}});
 assert.match(m.html,/id="live-sign-name" value="Jordan Lee"/,'prefilled with their own name');assert.doesNotMatch(m.html,/Alex Morgan|PE-60218/);
 assert.deepEqual(sent().engineer,{name:'Jordan Lee',credentials:'PE',license_number:'PE-77120',license_state:'CA'});
 const bad=await signDialog({proposed:false,typed:{'live-sign-cred':''}});assert.equal(bad.sent(),null,'no credentials, nothing sent');assert.match(bad.e.g.toasts.join(' '),/credentials/);
 const admin=await signDialog({proposed:false,role:'admin'});assert.match(admin.m.html,/id="live-sign-license" value="PE-60218"/,'the engineer of record prefills only their own dialog (same email)');
 const own=await signDialog({proposed:true});assert.match(own.m.html,/Signing as<\/span><strong>Jordan Lee, PE · CA PE-77120/);assert.doesNotMatch(own.m.html,/live-sign-name/);
 assert.equal(own.sent().engineer.name,'Jordan Lee');
});
test('signing while the signer details load waits instead of sending the user to Settings',async()=>{
 const {e,m}=await signDialog({proposed:true,hold:true});
 assert.equal(m,undefined,'no dialog yet');assert.equal(e.g.location.hash,'','not moved to Settings');assert.match(e.g.toasts.join(' '),/still loading/);
});
test('the fallback reads the study’s firm profile, not the firm on screen',async()=>{
 const asked=[];const e=await boot({proposed:false,intercept:(path)=>{if(/\/profile$/.test(path))asked.push(path);return null}});
 for(let i=0;i<3;i++){e.Live.mySigner('org-of-the-study');await Promise.all([...e.Live.state.cache.values()].filter(x=>!x.done).map(x=>x.promise))}
 assert.ok(asked.some(p=>p.includes('/orgs/org-of-the-study/profile')));
});
test('a viewer is never asked for signer details; Settings has no signer card for them',async()=>{
 const v=await boot({role:'viewer'}),home=await v.page('dashboard'),set=await v.page('settings');
 assert.doesNotMatch(home,/Set your signer details/);assert.doesNotMatch(set,/Signer details|data-fp-form="signer"/);
});
test('Settings reads the report state only of the studies its to-sign list can show',async()=>{
 const e=await boot();const asked=[];e.Live.loadDeliveries=list=>{asked.push(...list.map(s=>s.created_by))};
 await e.page('settings');assert.ok(asked.length>0);assert.ok(asked.every(id=>id===e.me.user.id),'an engineer’s own studies only');
});
test('a Resend that revoked the old invitation and failed to send says both',async()=>{
 const a=await boot({role:'admin',intercept:(path,q,init)=>/\/invitations$/.test(path)&&init.method==='POST'?new Response(JSON.stringify({type:'about:blank',title:'Unavailable',status:503,detail:'down'}),{status:503,headers:{'content-type':'application/problem+json'}}):null});
 await a.page('team');const inv=a.Live.state.cache.get('invitations:'+a.Live.firm().org_id).data.items.find(i=>i.status==='pending');
 a.api.revokeInvitation=async()=>({});a.api.invite=(org,body)=>a.api.request('POST',`/v1/orgs/${org}/invitations`,{body});
 a.Live.actions['fp-resend'](null,inv.id);await new Promise(r=>setTimeout(r,20));
 assert.match(a.g.toasts.at(-1),/was revoked, but sending a new one failed/);
});
test('the Library announces a count, not its table',async()=>{
 const e=await boot(),lib=await e.page('library');
 assert.match(lib,/<p class="sr-only" id="fp-lib-status" role="status">\d+ studies<\/p>/);assert.doesNotMatch(lib,/id="fp-results" aria-live/);
});
test('Home with deliveries that are not signed: Sign report and Update the report wait, before reviews',async()=>{
 const e=await boot({role:'admin'});const by={'Linden Avenue Townhomes':{label:'Needs signature',tone:'review'},'Redwood Commons':{label:'Update required',tone:'review'}};
 e.Live.deliveryPeek=s=>by[s.property_name]||{label:'Signed',tone:'done'};
 const needs=(await e.page('dashboard')).split('id="fp-needs-title"')[1].split('</section>')[0];
 assert.match(needs,/Linden Avenue Townhomes[\s\S]*?is-sign">Sign report</);assert.match(needs,/href="#report\/[^"]+"/,'a signature opens Report & sign');
 assert.match(needs,/Redwood Commons[\s\S]*?is-update">Update the report</);
 assert.ok(needs.indexOf('Sign report')<needs.indexOf('Review '),'signatures before reviews');
});
// Firms are set up by Basis (python -m basis.admin create-firm); POST /v1/orgs is off by default.
test('the no-firm screen offers no way to create a firm, for every role; it says to ask for an invitation or contact Basis',async()=>{
 for(const role of ['admin','engineer','viewer']){
  const e=await boot({role,onboarding:'nofirm',team:true});e.g.state.screen='dashboard';
  let html;for(let i=0;i<4;i++){html=e.Live.gate();const open=[...e.Live.state.cache.values()].filter(x=>!x.done);if(!open.length)break;await Promise.all(open.map(x=>x.promise))}
  assert.match(html,/You’re not in a firm yet/,role);
  assert.match(html,/You are signed in as <strong>taylor\.brooks@example\.test<\/strong>\. If your firm uses Basis, ask its admin to invite this address: the invitation appears here\. Setting up a new firm\? Contact Basis\./);
  assert.match(html,/data-live="reload-me">Check for an invitation</);assert.match(html,/data-live="sign-out">Use another account</);
  assert.doesNotMatch(html,/<form|<input|create-firm|Create firm|Start your firm|Firm name/i,'no form and no create control');
  assert.equal((html.match(/<button/g)||[]).length,2,'only the two buttons');
  // Nothing in the pages answers a create-firm form, and the sample API has no POST /v1/orgs.
  const sent=[];const req=e.api.request;e.api.request=(m,p,o)=>{sent.push([m,p]);return req(m,p,o)};
  for(const fn of e.events.submit||[])fn({target:{closest:()=>({dataset:{fpForm:'create-firm'},querySelector:()=>({value:'Harbor Cost Engineering'}),closest:()=>null})},preventDefault(){},stopImmediatePropagation(){}});
  await new Promise(r=>setTimeout(r,10));assert.deepEqual(sent,[],'no request is sent');
  const r=await e.sample.fetch(e.sample.base+'/v1/orgs',{method:'POST',headers:{'content-type':'application/json',authorization:'Bearer sample-token','idempotency-key':'k1'},body:JSON.stringify({name:'Harbor Cost Engineering'})});
  assert.notEqual(r.status,201,'the sample creates no firm');
 }
});
