const {test}=require('node:test');const assert=require('node:assert/strict');
const F=require('../live/firm.js');
const {createClient,ApiError}=require('../live/api-client.js');
const firm=(org_id,role,org_name=org_id)=>({org_id,org_name,role});
const me=(memberships=[],pending=[],verified=true)=>({user:{id:'u1',email:'a@b.test',display_name:'A'},email_verified:verified,memberships,pending_invitations:pending});
const problem=(status,kind)=>new ApiError(status,{type:`https://basis.invalid/problems/${kind}`,title:'x',status,detail:'d'});

test('the gate: no firm and no invitation, invitations, or the app',()=>{
 assert.equal(F.gate(null),'loading');
 assert.equal(F.gate(me()),'no-firm');
 assert.equal(F.gate(me([],[{id:'i1'}])),'join');
 assert.equal(F.gate(me([firm('o1','engineer')])),'app');
 // A member with an invitation still gets the app; the invitation is offered on the dashboard.
 assert.equal(F.gate(me([firm('o1','viewer')],[{id:'i1'}])),'app');
 // An unverified email lists no invitations (the API hides them), so it is the no-firm page.
 assert.equal(F.gate(me([],[],false)),'no-firm');
});
test('roles read Admin / Employee / Viewer, each with its scope',()=>{
 assert.deepEqual(F.ROLES.map(F.roleLabel),['Admin','Employee','Viewer']);
 for(const role of F.ROLES)assert.match(F.ROLE_SCOPE[role],/^[A-Z].*\.$/);
 assert.equal(F.roleLabel('auditor'),'auditor');
});
test('the current firm is the stored choice while still a member, else the first',()=>{
 const m=me([firm('o1','owner','Alpha'),firm('o2','viewer','Beta')]);
 assert.equal(F.currentFirm(m,'o2').org_id,'o2');
 assert.equal(F.currentFirm(m,'gone').org_id,'o1');
 assert.equal(F.currentFirm(m,null).org_id,'o1');
 assert.equal(F.currentFirm(me(),'o1'),null);
 assert.equal(F.isAdmin(firm('o','owner')),true);assert.equal(F.isAdmin(firm('o','engineer')),false);assert.equal(F.isAdmin(null),false);
 assert.equal(F.canCreate(firm('o','engineer')),true);assert.equal(F.canCreate(firm('o','viewer')),false);
});
test('a study is created in the current firm when the user has several, and lists show that firm',()=>{
 const one=me([firm('o1','engineer')]),two=me([firm('o1','engineer'),firm('o2','owner')]);
 assert.deepEqual(F.studyCreateBody(one,F.currentFirm(one),{property_name:'P'}),{property_name:'P'});
 assert.deepEqual(F.studyCreateBody(two,F.currentFirm(two,'o2'),{property_name:'P'}),{property_name:'P',org_id:'o2'});
 const studies=[{id:'s1',org_id:'o1'},{id:'s2',org_id:'o2'}];
 assert.deepEqual(F.firmStudies(one,F.currentFirm(one),studies),studies);
 assert.deepEqual(F.firmStudies(two,F.currentFirm(two,'o2'),studies).map(s=>s.id),['s2']);
});
test('accept and decline refusals become the join states the design shows',()=>{
 assert.equal(F.answerState(problem(404,'not-found')),'wrong-account');
 assert.equal(F.answerState(problem(403,'email-unverified')),'unverified');
 assert.equal(F.answerState(problem(409,'invitation-expired')),'expired');
 assert.equal(F.answerState(problem(409,'invitation-revoked')),'revoked');
 assert.equal(F.answerState(problem(409,'invitation-declined')),'declined');
 assert.equal(F.answerState(problem(409,'invitation-accepted')),'taken');
 assert.equal(F.answerState(problem(500,'x')),'error');
 for(const state of ['wrong-account','unverified','expired','revoked','declined','taken','error']){
  const [title,body]=F.ANSWER_COPY[state];assert.ok(title&&/\.$/.test(body),state);
 }
});
test('admin refusals read as sentences; the last admin is named plainly',()=>{
 assert.match(F.adminError(problem(409,'last-owner')),/at least one admin/);
 assert.match(F.adminError(problem(409,'already-member')),/already belongs/);
 assert.match(F.adminError(problem(409,'invitation-pending')),/pending invitation/);
 assert.equal(F.adminError(problem(422,'validation')),'d');
 assert.equal(F.initials('Basis Engineering'),'BE');assert.equal(F.initials('drew@x.test'),'D');assert.equal(F.initials(''),'?');
});
function fakeFetch(responses){
 const calls=[];
 const impl=async(url,init)=>{calls.push({url,...init});const {status=200,body}=responses.shift()||{};
  return {ok:status>=200&&status<300,status,headers:{get:k=>k.toLowerCase()==='content-type'?'application/json':null},text:async()=>body===undefined?'':JSON.stringify(body)}};
 return {impl,calls};
}
test('the firm endpoints: paths, methods, bodies and idempotency keys',async()=>{
 const {impl,calls}=fakeFetch(Array.from({length:9},()=>({body:{}})));
 const api=createClient({baseUrl:'http://api.test',getToken:async()=>'t',fetchImpl:impl});
 await api.me();await api.members('o 1');await api.setMemberRole('o1','u/2','viewer','k1');await api.removeMember('o1','u2');
 await api.invitations('o1');await api.invite('o1',{email:'a@b.test',role:'engineer'},'k2');await api.revokeInvitation('o1','i1');
 await api.acceptInvitation('i 1','k3');await api.declineInvitation('i1');
 assert.deepEqual(calls.map(c=>`${c.method} ${c.url.replace('http://api.test','')}`),[
  'GET /v1/me','GET /v1/orgs/o%201/members','POST /v1/orgs/o1/members/u%2F2/role','DELETE /v1/orgs/o1/members/u2',
  'GET /v1/orgs/o1/invitations','POST /v1/orgs/o1/invitations','DELETE /v1/orgs/o1/invitations/i1',
  'POST /v1/invitations/i%201/accept','POST /v1/invitations/i1/decline',
 ]);
 assert.equal(calls[2].body,'{"role":"viewer"}');assert.equal(calls[2].headers['Idempotency-Key'],'k1');
 assert.equal(calls[5].body,'{"email":"a@b.test","role":"engineer"}');assert.equal(calls[7].headers['Idempotency-Key'],'k3');
 for(const c of calls)assert.equal(c.headers.Authorization,'Bearer t');
 for(const i of [0,1,4])assert.equal(calls[i].headers['Idempotency-Key'],undefined);
 for(const i of [3,6,8])assert.match(calls[i].headers['Idempotency-Key'],/.{8,}/);
});
