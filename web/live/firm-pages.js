/* Live mode: the firm pages that are not a workflow step, for an admin and an engineer.
 *
 *   #dashboard  Home: what needs me (an admin: what needs anyone), what Basis is working on,
 *               what was signed, and the firm's latest activity; an admin's first-run checklist.
 *   #library    every study of the firm; server search (#161), with matched fields highlighted.
 *               The sample also proposes summary identity fields and a firm file number.
 *   #activity   who did what, on which study and step, grouped by day, each entry a link there.
 *   #team       admins: members, roles, removal, invitations; engineers: their colleagues.
 *   #settings   your firm and role (read-only), your signer details; an admin's letterhead.
 *
 * Rules are in live/firm-model.js (node-tested). Loaded after live-team.js; mock mode untouched.
 * What the API does not offer yet (study identity, the member directory below owner, signer
 * details, the firm feed) is PROPOSED in web/live/sample-api.js and FRONTEND_BACKLOG.md section 15;
 * against an API without it each page says what it can show and falls back to what exists.
 */
(function(){
 'use strict';
 const C=globalThis.BASIS_CONFIG;if(!C||C.mode!=='live')return;
 const Live=globalThis.BasisLive,F=globalThis.BasisFirm,M=globalThis.BasisFirmModel,{api,state:L}=Live;
 const P={lib:{q:'',filter:'all',engineer:'',sort:'updated'},act:{who:'everyone',type:'all'},invTab:'pending'};
 const now=()=>Date.now();
 const firstName=n=>String(n||'').trim().split(/\s+/)[0]||'';
 const meName=me=>me?.user?.display_name||me?.user?.email||'there';

 /* ---------- data ---------- */
 // Every study of the firm, archived included (the Library's Archived filter; Home leaves them out).
 const studiesEntry=()=>Live.load('studies-all',()=>api.listAll('/v1/studies',{include_archived:true},{limit:100}).then(p=>p.items));
 const quiet={expect:[403,404]};
 // The member list: every member reads it with the proposed directory (P5); today owners only.
 const membersEntry=org=>Live.load('members:'+org,()=>api.request('GET',`/v1/orgs/${encodeURIComponent(org)}/members`,quiet));
 const invitationsEntry=org=>Live.load('invitations:'+org,()=>api.invitations(org));
 const feedEntry=org=>Live.load('feed:'+org,()=>api.request('GET',`/v1/orgs/${encodeURIComponent(org)}/feed`,{query:{limit:200},...quiet}));
 const myActivityEntry=()=>Live.load('me-activity',()=>api.request('GET','/v1/me/activity',{query:{limit:200}}));
 const firmActivityEntry=org=>Live.load('org-activity:'+org,()=>api.request('GET',`/v1/orgs/${encodeURIComponent(org)}/activity`,{query:{limit:200}}));
 const signerEntry=()=>Live.load('me-signer',()=>api.request('GET','/v1/me/signer',quiet));
 // The same key and answer as live-report.js: null until the firm saves a profile (404).
 const profileEntry=org=>Live.load('firm-profile:'+org,()=>api.request('GET',`/v1/orgs/${encodeURIComponent(org)}/profile`,{expect:[404]}).catch(e=>{if(e.status===404)return null;throw e}));
 // The signed-in engineer's signer details. With the proposed /v1/me/signer (P6) they are their
 // own: {signer, own:true, version}. Without it (404, 405 or 501) the API keeps none, so the
 // engineer types them when signing: {signer:null, own:false, draft}, the draft prefilled from the
 // firm's engineer of record only when that is this user (the same email), else their name. Any
 // other failure is {error}; never someone else's details. org: the study's firm (default: the
 // firm on screen), so a member of two firms reads the right profile.
 const unsupported=e=>[404,405,501].includes(e?.status);
 Live.mySigner=org=>{const s=signerEntry();if(!s.done)return {loading:true};if(!s.error)return {signer:s.data.signer,own:true,version:s.data.version};if(!unsupported(s.error))return {error:s.error,signer:null};
  const id=org||Live.firm()?.org_id,p=id?profileEntry(id):null;if(p&&!p.done)return {loading:true};
  const me=Live.me(),eor=p&&!p.error?p.data?.profile?.engineer||null:null,email=String(me?.user?.email||'').trim().toLowerCase();
  const mine=Boolean(eor?.email&&email&&String(eor.email).trim().toLowerCase()===email);
  return {signer:null,own:false,draft:mine?{...eor}:{name:me?.user?.display_name||''}}};
 // Who may sign: engineers and admins (a viewer never signs, so is never asked for signer details).
 const signs=firm=>Boolean(firm)&&firm.role!=='viewer';
 const people=()=>{const f=Live.firm();if(!f)return [];const e=L.cache.get('members:'+f.org_id);return e?.done&&!e.error?e.data.items:[]};
 const nameOf=id=>{const me=Live.me();if(me&&id===me.user.id)return meName(me);const p=people().find(m=>m.user_id===id);if(p)return p.display_name||p.email;const s=(L.cache.get('studies-all')?.data||[]).find(x=>x.created_by===id);return s?.created_by_name||null};
 const deliveryOf=s=>s.status==='completed'?Live.deliveryPeek(s):null;

 /* ---------- small pieces ---------- */
 const PALETTE=['#2f5c46','#3b5878','#7a4527','#5a3d7a','#265d5d','#5f5322'];// dark enough for 4.5:1 on their own 14% tint
 const hue=t=>{let h=0;for(const c of String(t||''))h=(h*31+c.charCodeAt(0))>>>0;return PALETTE[h%PALETTE.length]};
 const avatar=(name,size='')=>name==='Basis'?`<span class="fp-avatar is-basis ${size}" aria-hidden="true"><img src="assets/basis-mark.png" alt=""></span>`:`<span class="fp-avatar ${size}" aria-hidden="true" style="--av:${hue(name)}">${esc(M.initials(name))}</span>`;
 const KIND=l=>/^sign report/i.test(l)?' is-sign':/^update the report/i.test(l)?' is-update':/^failed/i.test(l)?' is-failed':'';
 const pill=(label,tone)=>`<span class="fp-pill tone-${tone}${KIND(label)}">${esc(label)}</span>`;
 const stripOf=(s,d)=>{const seg=M.strip(s,d),cur=seg.findIndex(x=>x.state==='current'),at=cur<0?seg.length:cur+1;return `<span class="fp-stagebox"><span class="fp-strip" aria-hidden="true">${seg.map(x=>`<i class="is-${x.state} tone-${x.tone}" title="${esc(x.label)}"></i>`).join('')}</span><small>${cur<0?`All ${seg.length} done`:`${at} of ${seg.length} · ${esc(seg[cur].label.replace('Report & sign','Report'))}`}</small></span>`};
 const when=at=>`<time datetime="${esc(at)}" title="${esc(Live.when(at))}">${esc(M.ago(at,now()))}</time>`;
 const href=(s,n)=>Live.route(n.action?n.screen:'processing',s.id);// the step that waits, else the study's page
 Live.actions['fp-reload']=()=>{cancelSearch();for(const k of ['studies-all','members:','feed:','me-activity','org-activity:','invitations:','studies-search:','me-signer','firm-profile:'])Live.invalidate(k);Live.render()};

 /* ---------- Home ---------- */
 function studyRow(r,{owner=false}={}){
  const s=r.study,n=r.next;
  const m=s.summary;
  return `<a class="fp-row" href="${href(s,n)}"><span class="fp-row-main"><strong>${esc(s.property_name)}</strong>${M.place(s)?`<span>${esc(M.place(s))}</span>`:''}${m?.client?`<span class="fp-row-client">${esc(m.client)}</span>`:!M.place(s)&&s.created_by_name?`<span>${esc(s.created_by_name)}</span>`:''}</span>${owner?`<span class="fp-row-owner" title="${esc(s.created_by_name||'')}">${avatar(s.created_by_name||'?','is-small')}<span>${esc(firstName(s.created_by_name)||'—')}</span></span>`:''}${stripOf(s,deliveryOf(s))}${pill(n.label,n.tone)}<span class="fp-row-when">${when(s.updated_at)}</span></a>`;
 }
 function entryHtml(e,{compact=false,inStudy=false}={}){
  const link=e.study?Live.route(e.screen||'processing',e.study.id):`#${e.screen||'activity'}`;
  // On a study's own page the study is known: the sentence links its step instead.
  const what=inStudy?`<a href="${link}">${esc(e.verb)}</a>`:e.study?`${esc(e.verb)} on <a href="${link}">${esc(e.study.name)}</a>`:`<a href="${link}">${esc(e.verb)}</a>`;
  const meta=compact?'':[!inStudy&&e.study?.place,e.detail].filter(Boolean).map(esc).join(' · ');
  return `<li class="fp-event${e.system?' is-system':''}" data-activity-entry>${avatar(e.actor,compact?'is-small':'')}<div class="fp-event-body"><p><strong>${esc(e.actor)}</strong> ${what}</p>${meta?`<p class="fp-event-meta">${meta}</p>`:''}</div>${compact?`<span class="fp-event-when">${esc(M.ago(e.at,now()))}</span>`:`<time class="fp-event-when" datetime="${esc(e.at)}" title="${esc(Live.when(e.at))}">${esc(new Intl.DateTimeFormat('en-US',{hour:'numeric',minute:'2-digit'}).format(new Date(e.at)))}</time>`}</li>`;
 }
 // The feed's entries: the firm feed (P7) when the API has it, else your own activity; an admin
 // also gets the firm's own events (invitations, roles, the letterhead).
 // Home and Activity read the feed again each time they are opened (a decision elsewhere changed
 // it): the first render of a visit drops the cached feed before reading it, so a visit reads it once.
 let lastShown=null;
 function freshFeed(){if(lastShown===state.screen)return;lastShown=state.screen;for(const k of ['feed:','me-activity','org-activity:'])Live.invalidate(k)}
 function feedEntries(){
  const f=Live.firm();if(!f)return {done:true,entries:[],own:false};
  const studies=L.cache.get('studies-all')?.data||[];
  const feed=feedEntry(f.org_id);if(!feed.done)return {done:false};
  let own=false,events=[];
  if(!feed.error)events=feed.data.items;
  else if(!unsupported(feed.error))return {done:true,error:feed.error,entries:[]};
  else{const mine=myActivityEntry();if(!mine.done)return {done:false};if(mine.error)return {done:true,error:mine.error,entries:[]};own=true;
   // Your activity spans every firm you belong to: keep this firm's studies.
   const ids=new Set(Live.firmStudies(studies).map(s=>s.id).filter(Boolean));events=mine.data.items.filter(e=>ids.has(e.study_id))}
  let firmEvents=[];
  if(F.isAdmin(f)){const o=firmActivityEntry(f.org_id);if(!o.done)return {done:false};firmEvents=o.error?[]:o.data.items}
  return {done:true,own,entries:M.feed(events,{firmEvents,name:nameOf,studies})};
 }
 function setupChecklist(firm,studies){
  const prof=profileEntry(firm.org_id),members=membersEntry(firm.org_id),inv=invitationsEntry(firm.org_id),sig=Live.mySigner();
  if(!prof.done||!members.done||!inv.done||sig.loading)return '';
  // Signer details are a step only where Basis keeps them (P6); today they are typed when signing.
  const steps=[
   ['Add your firm’s letterhead','Name, address and phone, printed on every report.',!prof.error&&Boolean(prof.data?.profile),'#settings','Open Settings'],
   sig.own&&['Set your signer details','Your name, credentials and license, used when you sign.',Boolean(sig.signer),'#settings','Open Settings'],
   ['Invite your engineers','They see every study of the firm and sign their own reports.',(members.data?.items?.length||0)>1||(inv.data?.items||[]).some(i=>i.status==='pending'),'#team','Invite'],
   ['Start your first study','Upload an appraisal and the property photos.',studies.length>0,'#upload','Start a study'],
  ].filter(Boolean);
  const left=steps.filter(s=>!s[2]).length;if(!left)return '';
  return `<section class="fp-card fp-setup" aria-labelledby="fp-setup-title"><header><h2 id="fp-setup-title">Set up ${esc(firm.org_name)}</h2><span>${steps.length-left} of ${steps.length} done</span></header><ol>${steps.map(([t,d,done,to,go],i)=>`<li class="${done?'is-done':''}"><span class="fp-check" aria-hidden="true">${done?icon('check',14):i+1}</span><div><strong>${esc(t)}</strong><p>${esc(d)}</p></div>${done?'<span class="fp-done-label">Done</span>':`<a class="button small${steps.findIndex(s=>!s[2])===i?' primary':''}" href="${to}">${go}</a>`}</li>`).join('')}</ol></section>`;
 }
 function welcomeCard(me,firm,studies){
  // A member who just joined: one card until they have a study, and while an engineer still has
  // signer details to set (only where Basis keeps them; a viewer never signs).
  const sig=Live.mySigner(),mine=studies.filter(s=>s.created_by===me.user.id),needsSigner=signs(firm)&&sig.own&&!sig.signer;
  if(F.isAdmin(firm)||sig.loading||mine.length||(!needsSigner&&!L.justJoined))return '';
  return `<section class="fp-card fp-welcome" aria-labelledby="fp-welcome-title"><div><h2 id="fp-welcome-title">Welcome to ${esc(firm.org_name)}, ${esc(firstName(meName(me)))}</h2><p>Every study of the firm is in the Library. ${signs(firm)?'Studies you start, and the reviews waiting on you, appear here.':'Its reports, schedules and activity are yours to read.'}</p></div><div class="fp-welcome-actions">${needsSigner?`<a class="button primary" href="#settings">Set your signer details</a>`:''}<a class="button" href="#library">Open the Library</a>${F.canCreate(firm)?`<a class="button" href="#upload">Start a study</a>`:''}</div></section>`;
 }
 function homePage(){
  const me=Live.me(),firm=Live.firm();if(!me||!firm)return Live.loading();freshFeed();
  const e=studiesEntry();if(!e.done)return Live.loading('Loading your studies…');
  if(e.error?.kind==='no-firm'){Live.invalidate('studies');Live.refreshMe?.();return Live.loading('Checking your firm…')}
  if(e.error)return Live.failed(e.error,'fp-reload');
  const all=Live.firmStudies(e.data),studies=all.filter(s=>s.status!=='archived'),admin=F.isAdmin(firm);
  Live.loadDeliveries(studies);if(!people().length)membersEntry(firm.org_id);
  const h=M.home(studies,me.user,{admin,deliveryOf,now:now()});
  const count=(n,one,many)=>`${n} ${n===1?one:many}`;
  const jumpTo=(id,text)=>`<a href="#dashboard" data-live="fp-jump" data-value="${id}">${esc(text)}</a>`;
  const summary=!all.length?esc(`${firm.org_name} has no studies yet`):[jumpTo('fp-needs-title',admin?count(h.needs.length,'study needs attention','studies need attention'):count(h.needs.length,'study needs you','studies need you')),h.working.length?jumpTo('fp-working-title',count(h.working.length,'processing','processing')):'0 processing',h.finished.length?jumpTo('fp-signed-title',`${h.signedThisMonth} signed in the last 30 days`):`${h.signedThisMonth} signed in the last 30 days`].join(' · ');
  const list=(rows,empty,limit=5)=>rows.length?`<div class="fp-rows">${rows.slice(0,limit).map(r=>studyRow(r,{owner:admin})).join('')}</div>${rows.length>limit?`<a class="fp-more" href="#library">${rows.length-limit} more in the Library ${icon('arrow',14)}</a>`:''}`:`<p class="fp-empty">${empty}</p>`;
  const needs=`<section class="fp-card" aria-labelledby="fp-needs-title"><header><h2 id="fp-needs-title">${admin?'Needs attention':'Needs you'}</h2><span>${h.needs.length}</span></header>${list(h.needs,admin?'Nothing is waiting on anyone. New reviews appear here as Basis finishes each step.':'Nothing is waiting on you. New reviews appear here as Basis finishes each step.')}</section>`;
  const working=h.working.length?`<section class="fp-card" aria-labelledby="fp-working-title"><header><h2 id="fp-working-title">Processing</h2><span>Basis is working; nothing to do yet</span></header>${list(h.working,'',4)}</section>`:'';
  const signed=h.finished.length?`<section class="fp-card fp-finished" aria-labelledby="fp-signed-title"><header><h2 id="fp-signed-title">Recently signed</h2><a href="#library" data-live="fp-lib-filter" data-value="signed">All signed ${icon('arrow',14)}</a></header><div class="fp-rows">${h.finished.slice(0,3).map(r=>studyRow(r,{owner:admin})).join('')}</div></section>`:'';
  const f=feedEntries();
  const feed=`<section class="fp-card fp-feed" aria-labelledby="fp-feed-title"><header><h2 id="fp-feed-title">${f.own?'Your activity':'Firm activity'}</h2><a href="#activity">All activity ${icon('arrow',14)}</a></header>${!f.done?Live.loading():f.error?Live.failed(f.error,'fp-reload'):f.entries.length?`<ol class="fp-events is-compact">${f.entries.slice(0,6).map(x=>entryHtml(x,{compact:true})).join('')}</ol>`:'<p class="fp-empty">No activity yet.</p>'}</section>`;
  let team='';const setup=admin?setupChecklist(firm,all):'';
  if(admin&&!setup){const inv=invitationsEntry(firm.org_id),mem=membersEntry(firm.org_id);if(inv.done&&mem.done&&!inv.error&&!mem.error){const pending=inv.data.items.filter(i=>i.status==='pending').length,noSigner=mem.data.items.filter(m=>m.role!=='viewer'&&'signer' in m&&!m.signer);
   const lines=[pending&&`${pending} pending ${pending===1?'invitation':'invitations'}`,noSigner.length&&`${noSigner.map(m=>firstName(m.display_name||m.email)).join(', ')} ${noSigner.length===1?'has':'have'} no signer details`].filter(Boolean);
   if(lines.length)team=`<section class="fp-card fp-team-note" aria-labelledby="fp-team-title"><header><h2 id="fp-team-title">Team</h2><a href="#team">Manage ${icon('arrow',14)}</a></header><ul>${lines.map(l=>`<li>${esc(l)}</li>`).join('')}</ul></section>`}}
  const head=`<header class="fp-head"><div><h1>${esc(M.greeting(now()))}, ${esc(firstName(meName(me)))}</h1><p class="fp-summary">${summary}</p></div>${F.canCreate(firm)?`<a class="button primary" href="#upload">${icon('plus',16)}Start a study</a>`:''}</header>`;
  return `<div class="fp fp-home">${head}${admin?setup:welcomeCard(me,firm,all)}<div class="fp-home-grid"><div class="fp-col">${needs}${working}${signed}</div><div class="fp-col">${feed}${team}</div></div></div>`;
 }

 /* ---------- Library ---------- */
 const FILTERS=[['all','All'],['review','Needs review'],['sign','To sign'],['update','Update required'],['processing','Processing'],['signed','Signed'],['failed','Failed'],['draft','Drafts'],['archived','Archived']];
 function inFilter(s,f){
  const d=deliveryOf(s);
  switch(f){
   case 'all':return s.status!=='archived';
   case 'review':return ['waiting_review','stalled'].includes(s.status);
   case 'sign':return s.status==='completed'&&d?.tone==='review'&&d.label!=='Update required';
   case 'update':return s.status==='completed'&&d?.label==='Update required';
   case 'processing':return s.status==='processing';
   case 'signed':return s.status==='completed'&&d?.tone==='done';
   case 'failed':return s.status==='failed';
   case 'draft':return s.status==='draft';
   case 'archived':return s.status==='archived';
  }
  return true;
 }
 // Search is answered by the API, including fields StudyOut does not expose. Cache keys
 // use an opaque sequence: a client name/address in a query must never enter load-error logs.
 let searchTimer=null,searchSerial=0,activeSearch=null,previousSearch=null,lastSearchAnnouncement='';
 const searchActive=ctx=>activeSearch===ctx&&!ctx.stopped&&state.screen==='library'&&Live.firm()?.org_id===ctx.org&&P.lib.q.trim()===ctx.q;
 async function searchStudies(ctx){
  // listAll's general-purpose 50-page cap would silently hide matches in a large library.
  const items=[],seen=new Set();let cursor=null;
  do{
   if(!searchActive(ctx))return [];
   const page=await api.request('GET','/v1/studies',{query:{q:ctx.q,include_archived:true,limit:100,cursor}});
   if(!searchActive(ctx))return [];// don't crawl another page of an abandoned search
   if(!page||!Array.isArray(page.items))throw new Error('Search results could not be read completely. Try again.');
   items.push(...page.items);cursor=page.next_cursor;
   if(cursor){if(seen.has(cursor))throw new Error('Search results could not be read completely. Try again.');seen.add(cursor)}
  }while(cursor);
  return items;
 }
 const matchRows=(rows,q)=>rows.filter(s=>!P.lib.engineer||s.created_by===P.lib.engineer).map(s=>({s,m:M.match(s,q)||{field:null,value:null}}));
 function libraryMatches(all){
  const q=P.lib.q.trim(),org=Live.firm().org_id,problem=M.searchProblem(q);
  if(problem)return {hits:[],problem};
  const pending=()=>({hits:previousSearch?.org===org?matchRows(previousSearch.rows,previousSearch.q):[],q:previousSearch?.org===org?previousSearch.q:'',pending:true});
  if(searchTimer!==null)return pending();
  let rows=all;
  if(q){
   if(!activeSearch||activeSearch.q!==q||activeSearch.org!==org||!L.cache.has(activeSearch.key)){
    if(activeSearch)activeSearch.stopped=true;
    activeSearch={q,org,key:'studies-search:'+org+':'+(++searchSerial),stopped:false};
   }
   const ctx=activeSearch,e=Live.load(ctx.key,()=>searchStudies(ctx));
   if(!e.done)return pending();
   if(e.error){const queryError=e.error.status===422&&e.error.errors?.find(x=>Array.isArray(x.loc)&&x.loc.at(-1)==='q');
    if(queryError)return {hits:[],problem:queryError.type==='string_too_long'?'Use 200 characters or fewer to search.':'Use 10 distinct words or fewer to search.'};
    return {hits:[],error:e.error}}
   rows=Live.firmStudies(e.data);Live.loadDeliveries(rows);
  }
  previousSearch={org,rows,q};
  // match() annotates fields we can display; it never discards a server match.
  return {hits:matchRows(rows,q),q};
 }
 function searchBody(search){
  if(search.problem)return `<p class="fp-empty">${esc(search.problem)}</p>`;
  if(search.pending)return `<p class="fp-note">Searching studies…${search.hits.length?' Previous results are shown below.':''}</p>${search.hits.length?resultsHtml(search.hits,search.q):''}`;
  if(search.error)return Live.failed(search.error,'fp-lib-retry');
  return resultsHtml(search.hits);
 }
 function searchStatus(search){return search.problem||(search.pending?'Searching studies…':search.error?'Search could not be completed. Try again.':statusText(search.hits))}
 function announceSearch(search){
  const message=searchStatus(search);if(lastSearchAnnouncement===message)return;lastSearchAnnouncement=message;
  const sr=document.getElementById?.('sr-status');if(!sr)return;sr.textContent='';
  setTimeout(()=>{if(state.screen==='library'&&lastSearchAnnouncement===message)sr.textContent=message},60);
 }
 function cancelSearch(){if(searchTimer!==null)clearTimeout(searchTimer);searchTimer=null;if(activeSearch)activeSearch.stopped=true;activeSearch=null}
 function changeQuery(value){
  const same=value.trim()===P.lib.q.trim();P.lib.q=value;
  if(!same){cancelSearch();Live.invalidate('studies-search:');
   if(value.trim()&&!M.searchProblem(value.trim()))searchTimer=setTimeout(()=>{searchTimer=null;if(state.screen==='library')paintLibrary()},250)}
  paintLibrary();
 }
 function chipsHtml(hits){
  const searching=Boolean(P.lib.q.trim());
  return FILTERS.map(([k,label])=>{const n=hits.filter(x=>inFilter(x.s,k)).length;if(searching&&!n&&k!=='all'&&k!==P.lib.filter)return '';return `<button type="button" class="fp-chip${P.lib.filter===k?' is-on':''}" data-live="fp-lib-filter" data-value="${k}" aria-pressed="${P.lib.filter===k}">${label}<span>${n}</span></button>`}).join('');
 }
 function resultsHtml(hits,q=P.lib.q.trim()){
  const rows=hits.filter(x=>inFilter(x.s,P.lib.filter)).sort((a,b)=>P.lib.sort==='name'?a.s.property_name.localeCompare(b.s.property_name):P.lib.sort==='created'?String(b.s.created_at).localeCompare(String(a.s.created_at)):String(b.s.updated_at).localeCompare(String(a.s.updated_at)));
  // Nothing under this filter, but the search matches elsewhere: say where, with the way there.
  if(!rows.length&&q&&hits.length){const where=FILTERS.filter(([k])=>k!==P.lib.filter&&k!=='all').map(([k,l])=>[k,l,hits.filter(x=>inFilter(x.s,k)).length]).filter(x=>x[2]);
   if(where.length)return `<div class="fp-lib-empty">${icon('search',26)}<h2>No ${P.lib.filter==='all'?'active':esc((FILTERS.find(f=>f[0]===P.lib.filter)||FILTERS[0])[1].toLowerCase())} study matches “${esc(q)}”</h2><p>${where.map(([,l,n])=>`${n} in ${esc(l)}`).join(' · ')}</p><div class="fp-chips">${where.map(([k,l,n])=>`<button class="button small" data-live="fp-lib-filter" data-value="${k}">Show ${esc(l.toLowerCase())} (${n})</button>`).join('')}</div></div>`}
  if(!rows.length)return `<div class="fp-lib-empty">${icon('search',26)}<h2>${q?`No study matches “${esc(q)}”`:'No studies here yet'}</h2><p>${q?'Search looks at the property name, address, city, state, county, ZIP, taxpayer, borrower, owner of record, parcel number, engineer and signer.':'Studies appear here when an engineer starts one.'}</p>${q||P.lib.filter!=='all'||P.lib.engineer?'<button class="button small" data-live="fp-lib-clear">Clear search and filters</button>':''}</div>`;
  // Why it matched, when the field is not already a column or the line under the name.
  const matched=(x)=>x.m.field&&!['Property','Address','File number','Client','Engineer'].includes(x.m.field)?`<span class="fp-match"><em>${esc(x.m.field)}</em> ${M.highlight(x.m.value,q)}</span>`:'';
  return `<table class="fp-lib-table"><caption class="sr-only">Studies</caption><colgroup><col class="c-study"><col class="c-client"><col class="c-eng"><col class="c-stage"><col class="c-when"></colgroup><thead><tr><th scope="col">Study</th><th scope="col">Client</th><th scope="col">Engineer</th><th scope="col">Stage</th><th scope="col" class="fp-num">Updated</th></tr></thead><tbody>${rows.map(x=>{const s=x.s,n=M.next(s,deliveryOf(s)),m=s.summary;return `<tr data-href="${href(s,n)}"><td><a class="fp-lib-name" href="${href(s,n)}">${M.highlight(s.property_name,q)}</a>${M.place(s)?`<span class="fp-lib-sub">${M.highlight(M.place(s),q)}</span>`:''}${m?.file_number?`<span class="fp-file">${M.highlight(m.file_number,q)}</span>`:''}${matched(x)}</td><td>${m?.client?M.highlight(m.client,q):'<span class="fp-muted">—</span>'}</td><td><span class="fp-person">${avatar(s.created_by_name||'?','is-small')}${M.highlight(s.created_by_name||'Unknown',q)}</span></td><td><span class="fp-stage">${stripOf(s,deliveryOf(s))}${pill(n.label,n.tone)}</span></td><td class="fp-num">${when(s.updated_at)}</td></tr>`}).join('')}</tbody></table>`;
 }
 function libraryPage(){
  const firm=Live.firm();if(!firm)return Live.loading();
  const e=studiesEntry();if(!e.done)return Live.loading('Loading the library…');if(e.error)return Live.failed(e.error,'fp-reload');
  const all=Live.firmStudies(e.data);Live.loadDeliveries(all);
  const engineers=[...new Map(all.filter(s=>s.created_by).map(s=>[s.created_by,s.created_by_name||s.created_by_email||'Unknown'])).entries()].sort((a,b)=>a[1].localeCompare(b[1]));
  const search=libraryMatches(all),hits=search.hits;
  const placeholder='Search address, taxpayer, parcel, ZIP, engineer…';
  return `<div class="fp fp-library"><header class="fp-head"><div><h1>Library</h1><p>${all.filter(s=>s.status!=='archived').length} studies in ${esc(firm.org_name)}</p></div>${F.canCreate(firm)?`<a class="button primary" href="#upload">${icon('plus',16)}Start a study</a>`:''}</header>
<div class="fp-search" role="search"><label class="fp-search-box">${icon('search',20)}<span class="sr-only">Search studies</span><input id="fp-search" type="search" value="${esc(P.lib.q)}" placeholder="${placeholder}" autocomplete="off" spellcheck="false" aria-describedby="fp-search-help"><button type="button" class="fp-search-clear" data-live="fp-lib-clear-q" aria-label="Clear the search" ${P.lib.q?'':'hidden'}>${icon('x',16)}</button><kbd aria-hidden="true">/</kbd></label><p id="fp-search-help" class="sr-only">Searches every word across the property name, address, city, state, county, ZIP, taxpayer, borrower, owner of record, parcel number, engineer and signer. Use up to 10 distinct words and 200 characters.</p></div>
<div class="fp-lib-bar"><div class="fp-chips" id="fp-chips" role="group" aria-label="Filter by status">${search.error||search.problem?'':chipsHtml(hits)}</div><div class="fp-lib-controls"><label><span class="sr-only">Engineer</span><select id="fp-engineer" class="fp-select" data-native="true"><option value="">Every engineer</option>${engineers.map(([id,n])=>`<option value="${esc(id)}" ${P.lib.engineer===id?'selected':''}>${esc(n)}</option>`).join('')}</select></label><label><span class="sr-only">Sort</span><select id="fp-sort" class="fp-select" data-native="true"><option value="updated" ${P.lib.sort==='updated'?'selected':''}>Last updated</option><option value="created" ${P.lib.sort==='created'?'selected':''}>Newest</option><option value="name" ${P.lib.sort==='name'?'selected':''}>Name A–Z</option></select></label></div></div>
<p class="sr-only" id="fp-lib-status">${esc(searchStatus(search))}</p><section class="fp-card fp-lib" id="fp-results" aria-busy="${Boolean(search.pending)}">${searchBody(search)}</section></div>`;
 }
 // What a screen reader hears as the results change: a count, not the whole table again.
 function statusText(hits){const n=hits.filter(x=>inFilter(x.s,P.lib.filter)).length,q=P.lib.q.trim();return q?`${n} ${n===1?'study matches':'studies match'} “${q}”`:`${n} ${n===1?'study':'studies'}`}
 function paintLibrary(){
  const all=Live.firmStudies(L.cache.get('studies-all')?.data||[]),search=libraryMatches(all),hits=search.hits;
  const chips=root.querySelector('#fp-chips'),results=root.querySelector('#fp-results'),status=root.querySelector('#fp-lib-status');
  if(chips)chips.innerHTML=search.error||search.problem?'':chipsHtml(hits);if(results){results.innerHTML=searchBody(search);results.setAttribute('aria-busy',String(Boolean(search.pending)))}if(status)status.textContent=searchStatus(search);announceSearch(search);
 }
 Live.actions['fp-jump']=(el,id)=>{const h=root.querySelector('#'+id);if(!h)return;h.closest('section')?.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});h.setAttribute('tabindex','-1');h.focus({preventScroll:true})};
 Live.actions['fp-lib-filter']=(el,v)=>{P.lib.filter=v||'all';if(state.screen!=='library'){location.hash='#library';return}Live.render()};
 Live.actions['fp-lib-retry']=()=>{cancelSearch();Live.invalidate('studies-search:');Live.render()};
 Live.actions['fp-lib-clear']=()=>{cancelSearch();P.lib={...P.lib,q:'',filter:'all',engineer:''};Live.render();root.querySelector('#fp-search')?.focus()};
 root.addEventListener('input',e=>{if(e.target.id==='fp-search'){changeQuery(e.target.value);root.querySelector('.fp-search-clear')?.toggleAttribute('hidden',!P.lib.q)}},true);
 Live.actions['fp-lib-clear-q']=()=>{changeQuery('');const box=root.querySelector('#fp-search');if(box){box.value='';box.focus()}root.querySelector('.fp-search-clear')?.setAttribute('hidden','')};
 root.addEventListener('change',e=>{if(e.target.id==='fp-engineer'){P.lib.engineer=e.target.value;paintLibrary()}else if(e.target.id==='fp-sort'){P.lib.sort=e.target.value;paintLibrary()}else if(e.target.id==='fp-act-person'){P.act.who=e.target.value||'everyone';Live.render()}},true);
 root.addEventListener('keydown',e=>{if(e.target.id==='fp-search'&&e.key==='Escape'&&P.lib.q){changeQuery('');e.target.value=''}},true);
 // "/" focuses the search, as in most search-first tools (not while typing in a field).
 document.addEventListener('keydown',e=>{if(e.key!=='/'||e.metaKey||e.ctrlKey||e.altKey||state.screen!=='library')return;const t=e.target;if(t.closest?.('input,textarea,select,[contenteditable]'))return;const box=root.querySelector('#fp-search');if(box){e.preventDefault();box.focus();box.select()}});
 // A whole result row opens its study (the name stays the link for the keyboard and screen readers).
 root.addEventListener('click',e=>{const row=e.target.closest?.('tr[data-href]');if(!row||e.target.closest('a,button,select,input'))return;location.hash=row.dataset.href},true);

 /* ---------- Activity ---------- */
 const TYPES=[['all','All'],['review','Reviews'],['signature','Reports'],['upload','Uploads'],['team','Team']];
 function activityPage(){
  const me=Live.me(),firm=Live.firm();if(!me||!firm)return Live.loading();freshFeed();
  const e=studiesEntry();if(!e.done)return Live.loading('Loading activity…');if(e.error)return Live.failed(e.error,'fp-reload');
  if(!people().length)membersEntry(firm.org_id);
  const f=feedEntries();if(!f.done)return `<div class="fp fp-activity">${Live.loading('Loading activity…')}</div>`;if(f.error)return `<div class="fp fp-activity">${Live.failed(f.error,'fp-reload')}</div>`;
  const admin=F.isAdmin(firm),types=TYPES.filter(([k])=>k!=='team'||admin);
  const who=P.act.who,mine=me.user.id;
  const shown=f.entries.filter(x=>(who==='everyone'||(who==='me'?x.actorId===mine:x.actorId===who))&&(P.act.type==='all'||x.kind===P.act.type||P.act.type==='review'&&x.kind==='study'));
  const actors=[...new Map(f.entries.filter(x=>x.actorId&&x.actorId!==mine).map(x=>[x.actorId,x.actor])).entries()].sort((a,b)=>a[1].localeCompare(b[1]));
  const days=M.byDay(shown,now());
  const head=`<header class="fp-head"><div><h1>Activity</h1><p>${f.own?'Your own reviews, uploads and signatures. The firm’s feed appears here once the API offers it.':`Who did what across ${esc(firm.org_name)}, newest first.`}</p></div></header>`;
  const filters=`<div class="fp-lib-bar fp-act-bar"><div class="fp-chips" role="group" aria-label="Whose activity">${[['everyone','Everyone'],['me','Me']].map(([k,l])=>`<button type="button" class="fp-chip${who===k?' is-on':''}" data-live="fp-act-who" data-value="${k}" aria-pressed="${who===k}">${l}</button>`).join('')}<label><span class="sr-only">A colleague</span><select id="fp-act-person" data-native="true" class="fp-select${actors.some(([id])=>id===who)?' is-on':''}"><option value="">A colleague…</option>${actors.map(([id,n])=>`<option value="${esc(id)}" ${who===id?'selected':''}>${esc(n)}</option>`).join('')}</select></label></div><div class="fp-chips" role="group" aria-label="Kind of activity">${types.map(([k,l])=>`<button type="button" class="fp-chip${P.act.type===k?' is-on':''}" data-live="fp-act-type" data-value="${k}" aria-pressed="${P.act.type===k}">${l}</button>`).join('')}</div></div>`;
  const jump=days.length>1?`<nav class="fp-day-nav" aria-label="Jump to a day">${days.slice(0,7).map(d=>`<button type="button" class="fp-chip" data-live="fp-day" data-value="${esc(d.key)}">${esc(d.label.replace(/^(\w+), /,''))}<span>${d.entries.length}</span></button>`).join('')}</nav>`:'';
  const body=days.length?days.map(d=>`<section class="fp-day" id="fp-day-sec-${esc(d.key)}" aria-labelledby="fp-day-${esc(d.key)}"><h2 id="fp-day-${esc(d.key)}">${esc(d.label)}</h2><ol class="fp-events">${d.entries.map(x=>entryHtml(x)).join('')}</ol></section>`).join(''):`<p class="fp-empty">Nothing matches these filters.</p>`;
  return `<div class="fp fp-activity">${head}${filters}${jump}<div class="fp-card fp-timeline">${body}</div></div>`;
 }
 Live.actions['fp-act-who']=(el,v)=>{P.act.who=v;Live.render()};
 Live.actions['fp-day']=(el,key)=>{const sec=root.querySelector(`#fp-day-sec-${CSS.escape(key)}`);if(!sec)return;sec.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});sec.querySelector('h2')?.setAttribute('tabindex','-1');sec.querySelector('h2')?.focus({preventScroll:true})};
 Live.actions['fp-act-type']=(el,v)=>{P.act.type=v;Live.render()};

 /* ---------- Team ---------- */
 const ROLE_LABEL=r=>F.roleLabel(r);
 function workOf(id,studies){const own=studies.filter(s=>s.created_by===id&&!['archived','completed'].includes(s.status));return own.length}
 function teamPage(){
  const me=Live.me(),firm=Live.firm();if(!me||!firm)return Live.loading();
  const admin=F.isAdmin(firm),members=membersEntry(firm.org_id),studies=studiesEntry();
  const inv=admin?invitationsEntry(firm.org_id):{done:true,data:{items:[]}};
  if(!members.done||!studies.done||!inv.done)return `<div class="fp fp-team">${Live.loading('Loading the team…')}</div>`;
  const list=Live.firmStudies(studies.data||[]);
  if(!admin)return colleaguesPage(me,firm,members,list);
  if(members.error)return Live.failed(members.error,'fp-reload');if(inv.error)return Live.failed(inv.error,'fp-reload');
  const items=members.data.items.slice().sort((a,b)=>(a.role==='owner'?0:a.role==='engineer'?1:2)-(b.role==='owner'?0:b.role==='engineer'?1:2)||(a.display_name||a.email).localeCompare(b.display_name||b.email));
  const hasSigner=items.some(m=>'signer' in m);
  const rows=items.map(m=>{const self=m.user_id===me.user.id,name=m.display_name||m.email;return `<tr><td><span class="fp-person">${avatar(name)}<span><strong>${esc(name)}${self?' <span class="fp-you">you</span>':''}</strong><small>${esc(m.email)}</small></span></span></td><td><select class="fp-select" data-native="true" data-live-role="${esc(m.user_id)}" aria-label="Role of ${esc(name)}">${F.ROLES.map(r=>`<option value="${r}" ${r===m.role?'selected':''}>${ROLE_LABEL(r)}</option>`).join('')}</select></td>${hasSigner?`<td>${m.signer?`<span class="fp-signer">${esc(M.signerLine(m.signer))}</span>`:m.role==='viewer'?'<span class="fp-muted">—</span>':pill('Not set','review')}</td>`:''}<td class="fp-num">${workOf(m.user_id,list)||'<span class="fp-muted">0</span>'}</td><td>${m.last_seen_at?when(m.last_seen_at):'<span class="fp-muted">Never</span>'}</td><td class="fp-actions"><button class="button small quiet" data-live="remove-member" data-value="${esc(m.user_id)}">${self?'Leave':'Remove'}</button></td></tr>`}).join('');
  const all=inv.data.items,tabs=[['pending','Pending',i=>i.status==='pending'],['expired','Expired',i=>i.status==='expired'],['answered','Accepted & closed',i=>!['pending','expired'].includes(i.status)]];
  const tab=tabs.find(t=>t[0]===P.invTab)||tabs[0],shown=all.filter(tab[2]);
  const delivery={queued:['Sending','progress'],sent:['Emailed','done'],failed:['Email failed','danger'],not_sent:['Not emailed','idle']};
  const status=i=>i.status==='pending'?pill(`Pending · ${(delivery[i.delivery]||delivery.not_sent)[0].toLowerCase()}`,'review'):i.status==='accepted'?pill('Accepted','done'):i.status==='expired'?pill('Expired','danger'):pill(F.STATUS_LABEL[i.status]||i.status,'idle');
  const invRows=shown.map(i=>`<tr><td><strong>${esc(i.email)}</strong></td><td>${ROLE_LABEL(i.role)}</td><td>${status(i)}</td><td>${when(i.created_at)}${i.invited_by_name?` <span class="fp-muted">by ${esc(firstName(i.invited_by_name))}</span>`:''}</td><td>${i.status==='pending'?`<span title="${esc(Live.when(i.expires_at))}">${esc(Live.when(i.expires_at).replace(/,.*$/,''))}</span>`:i.accepted_at?when(i.accepted_at):i.declined_at?when(i.declined_at):i.revoked_at?when(i.revoked_at):'<span class="fp-muted">—</span>'}</td><td class="fp-actions">${i.status==='pending'?`<button class="button small quiet" data-live="fp-resend" data-value="${esc(i.id)}">Resend</button><button class="button small quiet" data-live="revoke-invite" data-value="${esc(i.id)}">Revoke</button>`:i.status==='expired'?`<button class="button small quiet" data-live="fp-resend" data-value="${esc(i.id)}">Invite again</button>`:''}</td></tr>`).join('');
  return `<div class="fp fp-team"><header class="fp-head"><div><h1>Team</h1><p>${items.length} ${items.length===1?'member':'members'} of ${esc(firm.org_name)} · every member sees every study of the firm</p></div><button class="button primary" data-live="invite-member">${icon('plus',16)}Invite people</button></header>
<section class="fp-card" aria-labelledby="fp-members-title"><header><h2 id="fp-members-title">Members</h2><button type="button" class="fp-link" data-live="fp-roles">What each role can do</button></header><div class="fp-table-wrap"><table class="fp-table"><thead><tr><th scope="col">Member</th><th scope="col">Role</th>${hasSigner?'<th scope="col">Signs as</th>':''}<th scope="col" class="fp-num">Open studies</th><th scope="col">Last active</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${rows}</tbody></table></div></section>
<section class="fp-card" aria-labelledby="fp-inv-title"><header><h2 id="fp-inv-title">Invitations</h2><div class="fp-chips" role="group" aria-label="Invitations by status">${tabs.map(([k,l,f])=>`<button type="button" class="fp-chip${tab[0]===k?' is-on':''}" data-live="fp-inv-tab" data-value="${k}" aria-pressed="${tab[0]===k}">${l}<span>${all.filter(f).length}</span></button>`).join('')}</div></header>${shown.length?`<div class="fp-table-wrap"><table class="fp-table"><thead><tr><th scope="col">Email</th><th scope="col">Role</th><th scope="col">Status</th><th scope="col">Invited</th><th scope="col">${tab[0]==='pending'?'Expires':'Answered'}</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${invRows}</tbody></table></div>`:`<p class="fp-empty">${tab[0]==='pending'?'No invitations are waiting. Invite people with the button above.':'None.'}</p>`}</section></div>`;
 }
 function colleaguesPage(me,firm,members,studies){
  // Every member reads the directory with the proposed P5; today the API lists members to owners
  // only, so an engineer sees the people the studies name.
  let list=members.error?[]:members.data.items,partial=Boolean(members.error);
  if(partial){const seen=new Map();for(const s of studies)if(s.created_by&&!seen.has(s.created_by))seen.set(s.created_by,{user_id:s.created_by,display_name:s.created_by_name,email:s.created_by_email,role:null});if(!seen.has(me.user.id))seen.set(me.user.id,{user_id:me.user.id,display_name:me.user.display_name,email:me.user.email,role:firm.role});list=[...seen.values()]}
  list=list.slice().sort((a,b)=>(a.role==='owner'?0:1)-(b.role==='owner'?0:1)||(a.display_name||a.email||'').localeCompare(b.display_name||b.email||''));
  const admins=list.filter(m=>m.role==='owner').map(m=>m.display_name||m.email);
  const hasSigner=list.some(m=>'signer' in m);
  const row=m=>{const name=m.display_name||m.email||'Member',self=m.user_id===me.user.id,open=workOf(m.user_id,studies);return `<tr><td><span class="fp-person">${avatar(name)}<span><strong>${esc(name)}${self?' <span class="fp-you">you</span>':''}</strong>${m.email?`<small><a href="mailto:${esc(m.email)}">${esc(m.email)}</a></small>`:''}</span></span></td><td>${m.role?esc(ROLE_LABEL(m.role)):'Member'}</td>${hasSigner?`<td>${m.signer?`<span class="fp-signer">${esc(M.signerLine(m.signer))}</span>`:'<span class="fp-muted">—</span>'}</td>`:''}<td class="fp-num">${open||'<span class="fp-muted">0</span>'}</td><td>${m.last_seen_at?when(m.last_seen_at):'<span class="fp-muted">Never</span>'}</td></tr>`};
  return `<div class="fp fp-team"><header class="fp-head"><div><h1>Team</h1><p>${list.length} ${list.length===1?'person':'people'} in ${esc(firm.org_name)}${admins.length?` · ${esc(admins.join(', '))} ${admins.length===1?'manages':'manage'} invitations and roles`:''}</p></div></header>${partial?'<p class="fp-note">Showing the people on the firm’s studies. The full member list is shown to admins.</p>':''}<section class="fp-card" aria-labelledby="fp-colleagues-title"><header><h2 id="fp-colleagues-title">Colleagues</h2><span>Read-only</span></header><div class="fp-table-wrap"><table class="fp-table fp-colleagues-table"><thead><tr><th scope="col">Member</th><th scope="col">Role</th>${hasSigner?'<th scope="col">Signs as</th>':''}<th scope="col" class="fp-num">Open studies</th><th scope="col">Last active</th></tr></thead><tbody>${list.map(row).join('')}</tbody></table></div></section><p class="fp-note">Ask an admin to invite someone or change a role.</p></div>`;
 }
 Live.actions['fp-inv-tab']=(el,v)=>{P.invTab=v;Live.render()};
 Live.actions['fp-roles']=()=>{modal('What each role can do',`<dl class="fp-roles">${F.ROLES.map(r=>`<div><dt>${ROLE_LABEL(r)}</dt><dd>${esc(F.ROLE_SCOPE[r])}</dd></div>`).join('')}</dl>`,closeBtn)};
 // Resend: an invitation is emailed once, so the API's way to send again is revoke, then invite again (FIRMS.md 3.1).
 Live.actions['fp-resend']=(el,id)=>{
  const firm=Live.firm();if(!F.isAdmin(firm))return;
  const inv=L.cache.get('invitations:'+firm.org_id)?.data?.items.find(i=>i.id===id);if(!inv)return;
  Live.guarded('invite-resend',async()=>{
   let revoked=false;
   try{if(inv.status==='pending'){await api.revokeInvitation(firm.org_id,id);revoked=true}await api.invite(firm.org_id,{email:inv.email,role:inv.role});toast(`A new invitation was sent to ${inv.email}.`)}
   catch(e){const why=e.status?F.adminError(e):Live.errorText(e);toast(revoked?`The old invitation to ${inv.email} was revoked, but sending a new one failed: ${why} Use Invite people to send it again.`:why)}
   Live.invalidate('invitations:'+firm.org_id);Live.invalidate('org-activity:');Live.render();
  });
 };

 /* ---------- Settings ---------- */
 const sel=(id,value,options,extra='')=>`<select id="${id}" class="fp-select" data-native="true" ${extra}>${options.map(([v,l])=>`<option value="${esc(v)}" ${String(v)===String(value??'')?'selected':''}>${esc(l)}</option>`).join('')}</select>`;
 const input=(id,label,value,extra='',hint='')=>`<div class="fp-field"><label for="${id}">${label}</label><input id="${id}" value="${esc(value??'')}" ${extra}>${hint?`<p class="fp-hint">${hint}</p>`:''}</div>`;
 function signerForm(sig,{onboarding=false}={}){
  // The form for the details Basis keeps (P6). Without them, nothing is kept: the engineer types
  // their details in the sign dialog, so there is no form here (a read-only one would be a dead end).
  const s=sig.signer||{};
  return `<form class="fp-signer-form" data-fp-form="signer" novalidate><div class="fp-grid-2">${input('fp-signer-name','Name as signed',s.name,'autocomplete="name" maxlength="200"')}${input('fp-signer-cred','Credentials',s.credentials,'maxlength="100" placeholder="PE"')}${input('fp-signer-license','License number',s.license_number,'maxlength="50"')}<div class="fp-field"><label for="fp-signer-state">License jurisdiction</label>${sel('fp-signer-state',s.license_state,[['','Choose a jurisdiction'],...M.STATES.map(x=>[x,x])])}</div></div><div class="fp-form-actions">${onboarding?'<button type="button" class="button quiet" data-live="fp-signer-later">Later</button>':''}<button type="submit" class="button primary">${onboarding?'Save and continue':'Save signer details'}</button></div></form>`;
 }
 function settingsPage(){
  const me=Live.me(),firm=Live.firm();if(!me||!firm)return Live.loading();
  const admin=F.isAdmin(firm),sig=Live.mySigner(),prof=profileEntry(firm.org_id);
  if(sig.loading||!prof.done)return `<div class="fp fp-settings">${Live.loading('Loading your settings…')}</div>`;
  const p=prof.error?null:prof.data?.profile;
  const identity=`<section class="fp-card fp-settings-card" aria-labelledby="fp-you-title"><header><h2 id="fp-you-title">You</h2></header><dl class="fp-dl"><div><dt>Name</dt><dd>${esc(me.user.display_name||'—')}</dd></div><div><dt>Email</dt><dd>${esc(me.user.email||'—')} <span class="fp-muted">· sign-in</span></dd></div><div><dt>Firm</dt><dd>${esc(firm.org_name)}</dd></div><div><dt>Role</dt><dd>${pill(ROLE_LABEL(firm.role),'idle')}${admin?'':' <span class="fp-muted">· an admin changes roles</span>'}</dd></div></dl></section>`;
  // Reports waiting for this signer: an admin's, any; an engineer's, their own. Only those
  // studies' report states are read (each answer re-renders the page).
  const studies=Live.firmStudies(L.cache.get('studies-all')?.data||[]),mayList=signs(firm)?studies.filter(x=>x.status==='completed'&&(admin||x.created_by===me.user.id)):[],toSign=mayList.filter(x=>deliveryOf(x)?.tone==='review');
  if(!L.cache.get('studies-all'))studiesEntry();else Live.loadDeliveries(mayList);
  if(/^#report\//.test(lastHash)&&L.study&&lastHash.includes(L.study.id))P.backTo={hash:lastHash,name:L.study.property_name};
  const back=P.backTo?`<a class="button" href="${esc(P.backTo.hash)}">${icon('back',15)}Back to ${esc(P.backTo.name)}’s report</a>`:'';
  const next=(sig.signer||!sig.own)&&(back||toSign.length)?`<div class="fp-signer-next">${back}${toSign.length?`<p>${toSign.length===1?'One report waits':`${toSign.length} reports wait`} for ${admin?'a':'your'} signature</p><ul class="fp-to-sign">${toSign.slice(0,4).map(x=>`<li><span>${esc(x.property_name)}</span><a class="button small" href="${Live.route('report',x.id)}">Open report to sign</a></li>`).join('')}</ul>`:''}</div>`:'';
  const head=sig.own?`${sig.signer?pill('Set','done'):pill('Not set','review')}`:'';
  const lead=sig.own?'Printed in the signature block of every report you sign. A signed report keeps the details it was signed with.'
   :`You enter your name, credentials and license when you sign a report; Basis does not keep them for you yet.${admin?' The engineer of record printed with the letterhead is edited under Firm letterhead.':''}`;
  const signer=!signs(firm)?'':sig.error?`<section class="fp-card fp-settings-card" aria-labelledby="fp-signer-title"><header><h2 id="fp-signer-title">Signer details</h2></header>${Live.failed(sig.error,'fp-reload')}</section>`:`<section class="fp-card fp-settings-card" aria-labelledby="fp-signer-title"><header><h2 id="fp-signer-title">Signer details</h2>${head}</header><p class="fp-card-lead">${lead}</p>${sig.own?signerForm(sig):''}${next}</section>`;
  const letter=p?[p.name,p.address,p.phone].filter(Boolean):[];
  const firmCard=admin&&prof.error?`<section class="fp-card fp-settings-card" aria-labelledby="fp-firm-title"><header><h2 id="fp-firm-title">Firm letterhead</h2></header>${Live.failed(prof.error,'fp-reload')}<p class="fp-card-lead">The letterhead could not be read, so it cannot be edited until it loads.</p></section>`:admin?`<section class="fp-card fp-settings-card" aria-labelledby="fp-firm-title"><header><h2 id="fp-firm-title">Firm letterhead</h2>${p?'':pill('Not added','review')}</header><p class="fp-card-lead">Printed at the top of every report of ${esc(firm.org_name)}. Only admins edit it.</p><form data-fp-form="letterhead" novalidate><div class="fp-grid-2">${input('fp-firm-name','Firm name',p?.name||firm.org_name,'maxlength="200"')}${input('fp-firm-phone','Phone',p?.phone,'maxlength="100" autocomplete="tel"')}</div>${input('fp-firm-address','Address',p?.address,'maxlength="500" autocomplete="street-address"')}${sig.own?'':`<h3 class="fp-sub">Engineer of record</h3><div class="fp-grid-2">${input('fp-eng-name','Name',p?.engineer?.name,'maxlength="200"')}${input('fp-eng-cred','Credentials',p?.engineer?.credentials,'maxlength="100"')}${input('fp-eng-license','License number',p?.engineer?.license_number,'maxlength="50"')}<div class="fp-field"><label for="fp-eng-state">License jurisdiction</label>${sel('fp-eng-state',p?.engineer?.license_state,[['','Choose a jurisdiction'],...M.STATES.map(x=>[x,x])])}</div></div>`}<div class="fp-form-actions"><button type="submit" class="button primary">Save letterhead</button></div></form></section>`
   :`<section class="fp-card fp-settings-card" aria-labelledby="fp-firm-title"><header><h2 id="fp-firm-title">Firm letterhead</h2></header><p class="fp-card-lead">Printed at the top of every report. An admin edits it.</p>${letter.length?`<address class="fp-letterhead">${letter.map(esc).join('<br>')}</address>`:'<p class="fp-empty">Not added yet.</p>'}</section>`;
  const links=admin?`<nav class="fp-settings-links" aria-label="Firm administration"><a href="#team">${icon('users',16)}Team and invitations</a><a href="#cost-indices">${icon('file',16)}Cost indices</a></nav>`:'';
  return `<div class="fp fp-settings"><header class="fp-head"><div><h1>Settings</h1><p>Your details and ${admin?'your firm’s':'your firm'}</p></div></header><div class="fp-settings-grid"><div class="fp-col">${signer}${firmCard}</div><div class="fp-col">${identity}${links}</div></div></div>`;
 }
 async function saveSigner(form,{onboarding=false}={}){
  const v=id=>form.querySelector(id)?.value||'';
  const {body,problems}=M.signerBody({name:v('#fp-signer-name'),credentials:v('#fp-signer-cred'),license_number:v('#fp-signer-license'),license_state:v('#fp-signer-state')});
  if(problems.length){toast(problems.join(' '));return false}
  const cur=signerEntry().data;
  try{await api.request('PUT','/v1/me/signer',{body,headers:{'If-Match':`"${cur?.version??0}"`}})}
  catch(e){if(e.status===412){Live.invalidate('me-signer');P.dropDrafts=true;toast('Your signer details changed in another tab. They were reloaded; apply your change again.');Live.render();return false}throw e}
  Live.invalidate('me-signer');Live.invalidate('members:');toast(onboarding?'Signer details saved.':'Signer details saved. Reports you sign from now on use them.');return true;
 }
 async function saveLetterhead(form){
  const firm=Live.firm(),v=id=>form.querySelector(id)?.value||'',e=profileEntry(firm.org_id);
  // Never replace a profile this page could not read: the save would erase what it did not show.
  if(!e.done||e.error){toast('The letterhead could not be read. Reload it before saving.');return}
  const cur=e.data;
  const sig=Live.mySigner();
  const eng=sig.own?cur?.profile?.engineer||null:{name:v('#fp-eng-name'),credentials:v('#fp-eng-cred'),license_number:v('#fp-eng-license'),license_state:v('#fp-eng-state')};
  const name=v('#fp-firm-name').trim();if(!name){toast('Enter the firm’s name.');return}
  const body={...(cur?.profile||{}),name,address:v('#fp-firm-address').trim()||null,phone:v('#fp-firm-phone').trim()||null,engineer:eng&&eng.name?{name:String(eng.name).trim(),credentials:String(eng.credentials||'').trim(),license_number:String(eng.license_number||'').trim()||null,license_state:String(eng.license_state||'').trim().toUpperCase()||null,...(eng.email?{email:eng.email}:{})}:null};
  if(body.engineer&&!body.engineer.credentials){toast('Enter the engineer of record’s credentials.');return}
  try{await api.request('PUT',`/v1/orgs/${encodeURIComponent(firm.org_id)}/profile`,{body,headers:cur?{'If-Match':`"${cur.version}"`}:{}})}
  catch(err){if(err.status===412){Live.invalidate('firm-profile:');P.dropDrafts=true;toast('The letterhead changed since you opened it. It was reloaded; apply your change again.');Live.render();return}throw err}
  Live.invalidate('firm-profile:');Live.invalidate('report-');Live.invalidate('org-activity:');toast('Letterhead saved. It is printed on reports written from now on.');Live.render();
 }
 root.addEventListener('submit',e=>{
  const f=e.target.closest?.('form[data-fp-form]');if(!f)return;e.preventDefault();e.stopImmediatePropagation();
  if(f.dataset.fpForm==='signer')Live.guarded('signer-save',async()=>{const onboarding=f.closest('.fp-onboarding');if(await saveSigner(f,{onboarding:Boolean(onboarding)})){if(onboarding){L.justJoined=true;Live.finishJoin?.();Live.invalidate('studies');location.hash='#dashboard'}Live.render()}});
  if(f.dataset.fpForm==='letterhead')Live.guarded('letterhead-save',()=>saveLetterhead(f));
 },true);
 Live.actions['fp-signer-later']=()=>{L.justJoined=true;Live.finishJoin?.();location.hash='#dashboard';Live.render()};

 /* ---------- a study's page: identity, deliverables, signatures, its activity ---------- */
 const statusLabel=n=>n.label.replace(/^Review (\w+)$/,(x,w)=>`${w[0].toUpperCase()}${w.slice(1)} review`);
 Live.studyIdentity=s=>{
  const m=s.summary,d=s.status==='completed'?Live.delivery(s):null,n=M.next(s,d),eng=s.created_by_name;
  const meta=[M.place(s),m?.client&&`Client ${m.client}`,m?.file_number&&`File ${m.file_number}`].filter(Boolean).map(esc);
  // The engineer is named in the details beside; the header says where and for whom.
  const line=meta.length?meta:eng?[esc(eng)]:[];
  return `<header class="fp-study-head"><div><h1>${esc(s.property_name)}</h1>${line.length?`<p>${line.join('<span aria-hidden="true"> · </span>')}</p>`:''}</div>${pill(statusLabel(n),n.tone)}</header>`;
 };
 Live.studyDetails=(s,person)=>{
  const m=s.summary||{},row=(k,v)=>v?`<div><dt>${k}</dt><dd>${v}</dd></div>`:'';
  return `<section class="panel panel-body fp-study-details"><h2>Study details</h2><dl>${row('Client',esc(m.client||''))}${row('Taxpayer',esc(m.taxpayer||''))}${row('File number',esc(m.file_number||''))}${row('Parcel number',esc(m.parcel_number||''))}${row('County',esc(m.county||''))}${row('Engineer',person?esc(person.name):esc(s.created_by_name||''))}${row('Created',Live.when(s.created_at))}${row('Last update',Live.when(s.updated_at))}${row('Revision',String(s.revision))}</dl></section>`;
 };
 const reportState=s=>Live.load('report-state:'+s.id+':'+s.revision,()=>api.request('GET',`/v1/studies/${encodeURIComponent(s.id)}/report/state`));
 const revisions=s=>Live.load('revisions:'+s.id+':'+s.revision,()=>api.request('GET',`/v1/studies/${encodeURIComponent(s.id)}/revisions`));
 Live.studyDeliverables=s=>{
  if(!['completed','archived'].includes(s.status))return '';
  const st=reportState(s),rv=revisions(s);if(!st.done||!rv.done)return `<section class="panel fp-deliver">${Live.loading('Loading the report…')}</section>`;
  if(st.error)return '';
  const state=st.data,sig=state.signature,files=new Set(state.files||[]),has=f=>files.has(f)||Boolean(sig&&f==='report.pdf');
  const final=s.status==='completed'?Live.delivery(s):{label:'Archived',tone:'idle'};
  const dl=(fmt,file,text,primary)=>has(file)?`<button class="button ${primary?'primary':''}" data-live="report-download" data-value="${fmt}">${icon('download',15)}${text}</button>`:'';
  const signedBy=x=>x.signed_by_name||nameOf(x.signed_by)||'An engineer';
  const lines=(rv.error?[]:rv.data.items).filter(x=>x.signature).sort((a,b)=>b.revision-a.revision);
  const lead=sig?`<p class="fp-deliver-lead">${icon('check',16)}<span>Signed by <strong>${esc(sig.engineer?.name||signedBy(sig))}</strong>${sig.engineer?.credentials?', '+esc(sig.engineer.credentials):''}${sig.engineer?.license_number?` · ${esc([sig.engineer.license_state,sig.engineer.license_number].filter(Boolean).join(' '))}`:''} · ${esc(Live.when(sig.signed_at))}</span></p>`:`<p class="fp-deliver-lead is-open">${state.exported&&!state.skipped?`Revision ${s.revision} is not signed yet.`:'No report for this revision yet.'}${s.status==='completed'?` <a href="${Live.route('report',s.id)}">Open Report & sign</a>`:''}</p>`;
  const table=lines.length?`<table class="fp-table fp-signatures"><caption class="sr-only">Who signed each revision</caption><thead><tr><th scope="col">Revision</th><th scope="col">Signed by</th><th scope="col">When</th><th scope="col"><span class="sr-only">Download</span></th></tr></thead><tbody>${lines.map(x=>`<tr><td>${x.revision}${x.current?' <span class="fp-muted">· current</span>':''}</td><td>${esc(x.current&&sig?.engineer?.name?`${sig.engineer.name}${sig.engineer.credentials?', '+sig.engineer.credentials:''}`:signedBy(x.signature))}</td><td>${esc(Live.when(x.signature.signed_at))}</td><td class="fp-actions"><button class="button small quiet" data-live="report-download-revision" data-value="${x.revision}">${icon('download',14)}PDF</button></td></tr>`).join('')}</tbody></table>`:'';
  return `<section class="panel fp-deliver" aria-labelledby="fp-deliver-title"><header><h2 id="fp-deliver-title">Report and schedule</h2>${final.tone==='done'?'':pill(final.label,final.tone==='review'?'review':'idle')}</header>${lead}<div class="fp-deliver-row">${dl('pdf','report.pdf',sig?`Signed report · revision ${s.revision}`:'Draft report',true)}${dl('csv','schedule.csv','Schedule CSV')}${dl('xlsx','schedule.xlsx','Schedule XLSX')}</div>${table}</section>`;
 };
 Live.studyActivity=s=>{
  // The timeline is in recorded order, a page at a time (at most 1,000 events): read every page, then take the latest.
  const t=Live.load('fp-timeline:'+s.id+':'+s.updated_at,()=>api.listAll(`/v1/studies/${encodeURIComponent(s.id)}/timeline`,{},{limit:1000}));
  if(!t.done)return `<section class="panel fp-study-activity">${Live.loading('Loading activity…')}</section>`;if(t.error)return '';
  const f=Live.firm();if(f&&!people().length)membersEntry(f.org_id);
  const entries=M.feed(t.data.items.map(e=>({...e,study_id:s.id,property_name:s.property_name,actor_name:e.actor_name||nameOf(e.actor_id)})),{studies:[s]});
  return `<section class="panel fp-study-activity" aria-labelledby="fp-sa-title"><header><h2 id="fp-sa-title">Activity on this study</h2><a href="${Live.route('history',s.id)}">Full history ${icon('arrow',14)}</a></header>${entries.length?`<ol class="fp-events is-compact">${entries.slice(0,6).map(x=>entryHtml(x,{compact:true,inStudy:true})).join('')}</ol>`:'<p class="fp-empty">Nothing recorded yet.</p>'}</section>`;
 };

 // Settings opened from a report offers the way back to it.
 let lastHash=location.hash;
 // The app renders on hashchange before this listener runs, so Settings reads the hash it came from
 // (still in lastHash) while it renders; leaving Settings forgets it.
 globalThis.addEventListener?.('hashchange',()=>{lastHash=location.hash;if(!/^#settings/.test(location.hash))P.backTo=null});

 /* ---------- register ---------- */
 Object.assign(Live.screens,{dashboard:homePage,library:libraryPage,activity:activityPage,team:teamPage,settings:settingsPage});
 Live.signerForm=signerForm;
 // A visit ends when another screen renders (freshFeed then reads the feed again on the next one).
 Live.afterRender.push(()=>{lastShown=state.screen;if(state.screen!=='library'||!Live.firm()){cancelSearch();lastSearchAnnouncement=''}else if(L.cache.get('studies-all')?.done&&!L.cache.get('studies-all')?.error)announceSearch(libraryMatches(Live.firmStudies(L.cache.get('studies-all').data)))});
 // A background re-render (a load answering) rebuilds the page from server values: keep what the
 // user typed in a form, and the focused field with its caret (the Library's search box), so
 // typing is never lost. A save that reloads after a conflict sets P.dropDrafts to show the reload.
 let kept=null;
 (Live.beforeRender||=[]).push(()=>{
  const was=root.querySelector?.('.workspace')?.dataset?.screen;if(!was||!root.querySelectorAll){kept=null;return}
  const changed=el=>el.tagName==='SELECT'?[...el.options].some(o=>o.selected!==o.defaultSelected):el.value!==el.defaultValue;
  const fields=[...root.querySelectorAll('form[data-fp-form] input[id], form[data-fp-form] select[id]')].filter(changed).map(el=>[el.id,el.value]);
  const a=document.activeElement,focus=a&&a!==document.body&&root.contains(a)&&a.id?{id:a.id,start:a.selectionStart??null,end:a.selectionEnd??null}:null;
  kept={screen:was,fields,focus};
 });
 Live.afterRender.push(()=>{
  const k=kept;kept=null;const drop=P.dropDrafts;P.dropDrafts=false;if(!k||drop||k.screen!==state.screen)return;
  for(const [id,v] of k.fields){const el=root.querySelector('#'+CSS.escape(id));if(el?.closest('form[data-fp-form]'))el.value=v}
  if(k.focus){const el=root.querySelector('#'+CSS.escape(k.focus.id));if(el&&document.activeElement!==el){el.focus({preventScroll:true});if(k.focus.start!=null)try{el.setSelectionRange(k.focus.start,k.focus.end)}catch{}}}
 });
 const TITLES={dashboard:'Home',library:'Library',activity:'Activity',team:'Team',settings:'Settings'};
 Live.afterRender.push(()=>{if(TITLES[state.screen]&&Live.me?.())document.title='Basis · '+TITLES[state.screen]});
 Live.render();
})();
