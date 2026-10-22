/* Live mode, part 3: invite-only firms. Loaded after live/live-app.js; mock mode untouched.
 *
 * * GET /v1/me after sign-in (cached as 'me'). Live.gate, called by the live render before
 *   the design renders, returns the page that replaces the app: "Not a member of a firm yet"
 *   (no firm, no invitation) or the join page (invitations to answer, or #join). Otherwise
 *   the app runs in the current firm (the stored choice per account, else the first).
 * * The sidebar names the firm and the role (Admin / Employee / Viewer; the API says owner /
 *   engineer / viewer). Several firms: a firm switcher in the top bar; the studies list
 *   shows the current firm's studies and a new study is created in it (org_id).
 * * #team (admins only; anyone else gets a no-access page): members with role change and
 *   removal, invitations with create and revoke. The API enforces every rule (the last admin,
 *   existing members, duplicate invitations); refusals are shown as they come back.
 * Rules and copy are in live/firm.js (node-tested).
 */
(function(){
 'use strict';
 const C=globalThis.BASIS_CONFIG;if(!C||C.mode!=='live')return;
 const Live=globalThis.BasisLive,F=globalThis.BasisFirm,{api,state:L}=Live;
 const T={joined:null,answered:[]};// the accepted card, and invitations answered with a problem
 const orgKey=()=>`basis-live-org:${L.user?.uid||''}`;
 const stored={get(){try{return localStorage.getItem(orgKey())}catch{return null}},set(v){try{localStorage.setItem(orgKey(),v)}catch{}}};
 const meEntry=(force=false)=>Live.load('me',()=>api.me(),{force});
 Live.me=()=>{const e=L.cache.get('me');return e?.done&&!e.error?e.data:null};
 Live.firm=()=>F.currentFirm(Live.me(),stored.get());
 Live.refreshMe=async()=>{Live.invalidate('me');const e=meEntry(true);try{await e.promise}catch{}return e.data};
 Live.createBody=body=>F.studyCreateBody(Live.me(),Live.firm(),body);
 Live.firmStudies=studies=>F.firmStudies(Live.me(),Live.firm(),studies);
 const article=label=>/^[AEIOU]/.test(label)?'an':'a';
 // The design labels #team as a proposal; in live mode it is the real page.
 const teamScreen=REVIEW.screens.find(s=>s[0]==='team');if(teamScreen)teamScreen[1]='Team & invitations';

 /* ---------- the gate ---------- */
 Live.gate=()=>{
  // Another account signed in: the previous one's join results must not show.
  if(T.uid!==L.user?.uid){T.uid=L.user?.uid;T.joined=null;T.answered=[]}
  if(['landing','login'].includes(state.screen))return '';
  const e=meEntry();
  if(!e.done)return `<main class="live-splash">${Live.loading('Loading your firm…')}</main>`;
  if(e.error)return `<main class="live-splash">${Live.failed(e.error,'reload-me')}</main>`;
  const me=e.data,firm=Live.firm();
  // The design shows owner-only navigation by state.role; it follows the current firm.
  state.role=F.isAdmin(firm)?'Owner':'Engineer';
  if(T.joined||T.answered.length&&!me.memberships.length)return joinPage(me);
  const gate=F.gate(me);
  if(gate==='no-firm')return noFirm(me);
  if(gate==='join'||state.screen==='join')return joinPage(me);
  return '';
 };
 function noFirm(me){
  const email=me.user.email||L.user?.email||'this account';
  return `<main class="invite-page live-firm-page"><div class="content"><section class="invitation-card panel"><div class="panel-body">${logo()}<h2>Not a member of a firm yet</h2><p>You are signed in as <strong>${esc(email)}</strong>. Basis is invite-only: ask your firm’s admin to invite this email address. The invitation appears here the next time you sign in.</p>${me.email_verified?'':`<div class="note"><strong class="small">This email address is not confirmed</strong><p>Invitations can only be shown to a confirmed address. Sign in with Google to use one.</p></div>`}<div class="row wrap"><button class="button primary" data-live="reload-me">Check again</button><button class="button" data-live="sign-out">Sign out and use another account</button></div><p class="footnote">An invitation is tied to one email address. If your firm invited a different address, sign in with that account.</p></div></section></div></main>`;
 }
 function inviter(inv){const who=inv.invited_by_name||inv.invited_by_email;return who?`${esc(who)} invited you`:'You were invited'}
 function pendingCard(me,inv){
  const label=F.roleLabel(inv.role);
  return `<section class="invitation-card panel" data-invitation="${esc(inv.id)}"><div class="panel-body">${logo()}<div class="invitation-workspace"><span class="avatar">${esc(F.initials(inv.org_name))}</span><span>${esc(inv.org_name)}</span></div><h2>Join ${esc(inv.org_name)}</h2><p>${inviter(inv)} as ${article(label)} ${esc(label.toLowerCase())}, using ${esc(me.user.email||'')}.</p><div class="note"><strong class="small">${esc(label)} access</strong><p>${esc(F.ROLE_SCOPE[inv.role]||'')}</p></div><div class="row wrap"><button class="button primary" data-live="accept-invite" data-value="${esc(inv.id)}">Accept and join</button><button class="button quiet" data-live="decline-invite" data-value="${esc(inv.id)}">Decline</button></div><p class="footnote">Expires ${esc(Live.when(inv.expires_at))}. No email is sent: invitations appear here when you sign in with the invited address.</p></div></section>`;
 }
 function answeredCard({invitation:inv,state:answer}){
  const [title,body]=F.ANSWER_COPY[answer]||F.ANSWER_COPY.error;
  const switchAccount=['wrong-account','unverified','taken'].includes(answer);
  return `<section class="invitation-card panel" data-invitation="${esc(inv.id)}"><div class="panel-body">${logo()}<div class="invitation-workspace"><span class="avatar">${esc(F.initials(inv.org_name))}</span><span>${esc(inv.org_name)}</span></div><h2>${esc(title)}</h2><p>${esc(body)}</p>${switchAccount?'<button class="button" data-live="sign-out">Sign in with another account</button>':''}</div></section>`;
 }
 function joinPage(me){
  let cards='';
  if(T.joined){
   const label=F.roleLabel(T.joined.role);
   cards=`<section class="invitation-card panel"><div class="panel-body">${logo()}<div class="invitation-workspace"><span class="avatar">${esc(F.initials(T.joined.org_name))}</span><span>${esc(T.joined.org_name)}</span></div><h2>You’re ready to start</h2><p>You joined ${esc(T.joined.org_name)} as ${article(label)} ${esc(label.toLowerCase())}.</p><div class="note"><strong class="small">${esc(label)} access</strong><p>${esc(F.ROLE_SCOPE[T.joined.role]||'')}</p></div><button class="button primary" data-live="open-firm">Open your studies</button></div></section>`;
  }else{
   const done=new Set(T.answered.map(a=>a.invitation.id));
   cards=T.answered.map(answeredCard).join('')+me.pending_invitations.filter(i=>!done.has(i.id)).map(i=>pendingCard(me,i)).join('');
   if(!cards)cards=`<section class="invitation-card panel"><div class="panel-body">${logo()}<h2>No invitations waiting</h2><p>There is nothing to answer for ${esc(me.user.email||'this account')}.</p><a class="button primary" href="#dashboard">Back to your studies</a></div></section>`;
   else if(!me.memberships.length&&T.answered.length&&!me.pending_invitations.some(i=>!done.has(i.id)))cards+=`<p class="footnote live-join-note">You are not a member of a firm yet. <button class="button quiet small" data-live="join-done">Continue</button></p>`;
  }
  return `<main class="invite-page live-firm-page"><div class="content">${heading('Firm invitation','Join the firm that invited you. Your role decides what you can do there.')}${cards}</div></main>`;
 }

 /* ---------- answering ---------- */
 const findInvitation=id=>Live.me()?.pending_invitations.find(i=>i.id===id);
 Live.actions['accept-invite']=(el,id)=>Live.guarded('answer',async()=>{
  const inv=findInvitation(id);if(!inv)return;
  try{
   const result=await api.acceptInvitation(id);
   stored.set(result.membership?.org_id||inv.org_id);
   T.joined={org_name:inv.org_name,role:result.membership?.role||inv.role};
   Live.invalidate('studies');
  }catch(e){if(!e.status)throw e;T.answered.push({invitation:inv,state:F.answerState(e)})}
  await Live.refreshMe();Live.render();
 });
 Live.actions['decline-invite']=(el,id)=>{
  const inv=findInvitation(id);if(!inv)return;
  const m=modal('Decline this invitation?',`<p>You will not join ${esc(inv.org_name)}. Its admin can invite you again later.</p>`,`${closeBtn}<button class="button danger" id="live-confirm-decline">Decline</button>`);
  m.querySelector('#live-confirm-decline').onclick=()=>Live.guarded('answer',async()=>{
   m.remove();
   try{await api.declineInvitation(id);T.answered.push({invitation:inv,state:'declined'})}
   catch(e){if(!e.status)throw e;T.answered.push({invitation:inv,state:F.answerState(e)})}
   await Live.refreshMe();Live.render();
  });
 };
 Live.actions['open-firm']=()=>{T.joined=null;T.answered=[];location.hash='#dashboard';Live.render()};
 Live.actions['join-done']=()=>{T.answered=[];location.hash='#dashboard';Live.render()};

 /* ---------- firm identity, the switcher, the invitation notice ---------- */
 Live.barExtra=()=>{
  const me=Live.me(),firm=Live.firm();if(!me||me.memberships.length<2||!firm)return '';
  return `<label class="small live-firm-switch">Firm <select id="live-firm" aria-label="Current firm">${me.memberships.map(m=>`<option value="${esc(m.org_id)}" ${m.org_id===firm.org_id?'selected':''}>${esc(m.org_name)} · ${esc(F.roleLabel(m.role))}</option>`).join('')}</select></label>`;
 };
 root.addEventListener('change',e=>{
  if(e.target.id==='live-firm'){stored.set(e.target.value);Live.render()}
 },true);
 Live.afterRender.push(()=>{
  const me=Live.me(),firm=Live.firm();if(!me||!firm)return;
  const label=F.roleLabel(firm.role),who=me.user.display_name||me.user.email||'Signed in';
  for(const el of root.querySelectorAll('.sidebar-company strong'))el.textContent=firm.org_name;
  for(const el of root.querySelectorAll('.sidebar-company .company-copy span'))el.textContent=me.memberships.length>1?`${label} · ${me.memberships.length} firms`:label;
  for(const el of root.querySelectorAll('.sidebar-company .company-monogram'))el.textContent=F.initials(firm.org_name);
  for(const el of root.querySelectorAll('.sidebar-company'))el.title=`${firm.org_name} · ${label}`;
  for(const el of root.querySelectorAll('.account .account-role'))el.textContent=label;
  root.querySelector('#account-menu-trigger')?.setAttribute('aria-label',`${who}, ${label} — account menu`);
  // The design names its sample role in the top bar outside a study.
  for(const el of root.querySelectorAll('.topbar .right-meta .badge.complete'))el.textContent=label;
  if(me.pending_invitations.length&&state.screen==='dashboard'&&!root.querySelector('.live-invite-note')){
   const n=me.pending_invitations.length;
   root.querySelector('.collection-header')?.insertAdjacentHTML('afterend',`<div class="attention live-invite-note"><div class="row">${icon('mail',19)}<div><span class="strong">${n===1?`${esc(me.pending_invitations[0].org_name)} invited you.`:`${n} firms invited you.`}</span><p>Answer the invitation to join or decline.</p></div></div><a class="button" href="#join">Review</a></div>`);
  }
 });

 /* ---------- #team ---------- */
 const membersKey=org=>'members:'+org,invitationsKey=org=>'invitations:'+org;
 function noAccess(firm){
  return `<div class="empty-screen live-no-access">${icon('shield',36)}<h2>Admin access required</h2><p>Team and invitation management is available to the firm’s admins.${firm?` You are ${article(F.roleLabel(firm.role))} ${esc(F.roleLabel(firm.role).toLowerCase())} in ${esc(firm.org_name)}.`:''}</p><a class="button primary" href="#dashboard">Go to your studies</a></div>`;
 }
 function roleSelect(member,me){
  const self=member.user_id===me.user.id;
  return `<select class="select-field" data-live-role="${esc(member.user_id)}" aria-label="Role of ${esc(member.email||member.display_name||'member')}">${F.ROLES.map(r=>`<option value="${r}" ${r===member.role?'selected':''}>${F.roleLabel(r)}${self?' (you)':''}</option>`).join('')}</select>`;
 }
 function membersTable(firm,me,members){
  return `<section class="panel tablewrap live-members"><div class="panel-head"><h2>Members</h2><span class="small muted">${members.length} ${members.length===1?'member':'members'}</span></div><table><thead><tr><th>Member</th><th>Role</th><th>Joined</th><th>Last active</th><th><span class="sr-only">Actions</span></th></tr></thead><tbody>${members.map(m=>{const self=m.user_id===me.user.id,name=m.display_name||m.email||m.user_id;return `<tr><td><div class="property"><span class="avatar">${esc(F.initials(m.display_name||m.email))}</span><div><b>${esc(name)}${self?' · you':''}</b><small>${esc(m.email||'')}</small></div></div></td><td>${roleSelect(m,me)}</td><td class="muted">${esc(Live.when(m.joined_at))}</td><td class="muted">${esc(Live.when(m.last_seen_at))}</td><td class="text-right"><button class="button small ${self?'quiet':''}" data-live="remove-member" data-value="${esc(m.user_id)}">${self?'Leave firm':'Remove'}</button></td></tr>`}).join('')}</tbody></table></section>`;
 }
 function invitationsTable(invitations){
  const rows=invitations.map(i=>`<tr><td><strong>${esc(i.email)}</strong></td><td>${esc(F.roleLabel(i.role))}</td><td><span class="badge ${F.STATUS_BADGE[i.status]||''}">${esc(F.STATUS_LABEL[i.status]||i.status)}</span></td><td class="muted">${esc(Live.when(i.created_at))}${i.invited_by_name||i.invited_by_email?` · ${esc(i.invited_by_name||i.invited_by_email)}`:' · Basis'}</td><td class="muted">${i.status==='pending'?esc(Live.when(i.expires_at)):'—'}</td><td class="text-right">${i.status==='pending'?`<button class="button small quiet" data-live="revoke-invite" data-value="${esc(i.id)}">Revoke</button>`:''}</td></tr>`).join('');
  return `<section class="panel live-invitations" style="margin-top:25px"><div class="panel-head"><h2>Invitations</h2><span class="small muted">Each one works only for its email address</span></div><div class="tablewrap"><table><thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Invited</th><th>Expires</th><th><span class="sr-only">Actions</span></th></tr></thead><tbody>${rows||'<tr><td colspan="6"><div class="empty-screen"><h3>No invitations yet</h3><p>Invite someone with their work email.</p></div></td></tr>'}</tbody></table></div></section>`;
 }
 team=function(){
  const me=Live.me(),firm=Live.firm();
  if(!me||!F.isAdmin(firm))return noAccess(firm);
  const members=Live.load(membersKey(firm.org_id),()=>api.members(firm.org_id)),invitations=Live.load(invitationsKey(firm.org_id),()=>api.invitations(firm.org_id));
  const head=heading('Team & invitations',`Invite people to ${esc(firm.org_name)} and manage who has access.`,`<button class="button primary" data-live="invite-member">${icon('plus')}Invite team member</button>`);
  const policy=`<div class="team-policy live-team-policy">${F.ROLES.map(r=>`<div><strong>${F.roleLabel(r)}</strong><p>${esc(F.ROLE_SCOPE[r])}</p></div>`).join('')}</div>`;
  const body=!members.done||!invitations.done?Live.loading('Loading the team…'):members.error?Live.failed(members.error,'reload-team'):invitations.error?Live.failed(invitations.error,'reload-team'):membersTable(firm,me,members.data.items)+invitationsTable(invitations.data.items);
  return `${head}${policy}${body}<p class="footnote">No email is sent. An invited person sees the invitation when they sign in with that email address, confirmed by Google sign-in.</p>`;
 };
 const reloadTeam=org=>{Live.invalidate(membersKey(org));Live.invalidate(invitationsKey(org));Live.render()};
 Live.actions['reload-team']=()=>{const f=Live.firm();if(f)reloadTeam(f.org_id)};
 async function adminCall(fn,ok){
  try{await fn();if(ok)toast(ok)}catch(e){toast(e.status?F.adminError(e):Live.errorText(e))}
  const f=Live.firm();if(f)reloadTeam(f.org_id);
 }
 root.addEventListener('change',e=>{
  const select=e.target.closest?.('select[data-live-role]');if(!select||state.screen!=='team')return;
  e.stopImmediatePropagation();
  const firm=Live.firm(),role=select.value;if(!firm)return;
  select.disabled=true;
  Live.guarded('member-role',()=>adminCall(()=>api.setMemberRole(firm.org_id,select.dataset.liveRole,role),`Role changed to ${F.roleLabel(role)}.`).then(()=>Live.refreshMe()).then(()=>Live.render()));
 },true);
 Live.actions['remove-member']=(el,userId)=>{
  const firm=Live.firm(),me=Live.me();if(!firm||!me)return;
  const member=L.cache.get(membersKey(firm.org_id))?.data?.items.find(m=>m.user_id===userId),self=userId===me.user.id;
  const name=member?.display_name||member?.email||'this member';
  const m=modal(self?`Leave ${esc(firm.org_name)}?`:`Remove ${esc(name)}?`,`<p>${self?'Your':'Their'} access to ${esc(firm.org_name)} ends immediately. Studies ${self?'you':'they'} created stay in the firm and keep ${self?'your':'their'} name as creator.</p>`,`${closeBtn}<button class="button danger" id="live-confirm-remove">${self?'Leave firm':'Remove'}</button>`);
  m.querySelector('#live-confirm-remove').onclick=()=>Live.guarded('member-remove',async()=>{
   m.remove();
   await adminCall(()=>api.removeMember(firm.org_id,userId),self?`You left ${firm.org_name}.`:`${name} was removed.`);
   if(self){await Live.refreshMe();location.hash='#dashboard';Live.render()}
  });
 };
 Live.actions['revoke-invite']=(el,id)=>{
  const firm=Live.firm();if(!firm)return;
  const inv=L.cache.get(invitationsKey(firm.org_id))?.data?.items.find(i=>i.id===id);
  const m=modal('Revoke this invitation?',`<p>${esc(inv?.email||'The invitee')} will not be able to use it. You can invite them again later.</p>`,`${closeBtn}<button class="button danger" id="live-confirm-revoke">Revoke</button>`);
  m.querySelector('#live-confirm-revoke').onclick=()=>Live.guarded('invite-revoke',async()=>{m.remove();await adminCall(()=>api.revokeInvitation(firm.org_id,id),'Invitation revoked.')});
 };
 Live.actions['invite-member']=()=>{
  const firm=Live.firm();if(!F.isAdmin(firm))return;
  const help=r=>`<strong class="small">${F.roleLabel(r)}</strong><p>${esc(F.ROLE_SCOPE[r])}</p>`;
  const m=modal('Invite a team member',`<p>Give someone access to ${esc(firm.org_name)} with their work email.</p><div class="field"><label for="live-invite-email">Work email</label><input id="live-invite-email" type="email" autocomplete="off" placeholder="name@company.com" required></div><div class="field" style="margin-top:17px"><label for="live-invite-role">Role</label><select id="live-invite-role">${['engineer','owner','viewer'].map(r=>`<option value="${r}">${F.roleLabel(r)}</option>`).join('')}</select></div><div class="invite-role-help note" style="margin-top:18px">${help('engineer')}</div><p class="footnote" style="margin-top:16px">No email is sent. They see the invitation when they sign in to Basis with this address.</p>`,`${closeBtn}<button class="button primary" id="live-create-invite">Create invitation</button>`);
  const roleField=m.querySelector('#live-invite-role');
  roleField.onchange=()=>{m.querySelector('.invite-role-help').innerHTML=help(roleField.value)};
  m.querySelector('#live-create-invite').onclick=()=>Live.guarded('invite-create',async()=>{
   const input=m.querySelector('#live-invite-email'),email=input.value.trim();
   if(!email||!input.checkValidity()){input.reportValidity();toast('Enter a valid work email.');return}
   try{await api.invite(firm.org_id,{email,role:roleField.value});m.remove();toast(`Invitation created for ${email.toLowerCase()}. No email was sent.`);reloadTeam(firm.org_id)}
   catch(e){toast(e.status?F.adminError(e):Live.errorText(e))}
  });
 };
})();
