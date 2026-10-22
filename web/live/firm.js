/* Live mode: invite-only firms, the parts that are rules rather than markup (node-tested).
 *
 * A firm is an organization in the API (GET /v1/me, /v1/orgs/{id}/...). The API's roles are
 * owner / engineer / viewer; the UI says Admin / Employee / Viewer. Everyone signs in on the
 * same page; GET /v1/me then decides what they see:
 *   no firm and no invitation  -> the "Not a member of a firm yet" screen
 *   invitations, and no firm   -> the join screen (accept or decline)
 *   a firm                     -> the app, in the current firm (a choice when several)
 * Errors from accept/decline are problem types (basis.api.errors): their last segment says
 * which join state to show.
 */
(function(scope){
 'use strict';
 const ROLE_LABEL={owner:'Admin',engineer:'Employee',viewer:'Viewer'};
 // What each role can do, shown next to the role wherever one is chosen or offered.
 const ROLE_SCOPE={
  owner:'Manages the firm: invites people, changes roles, removes members, and sees and works on every study.',
  engineer:'Creates studies and reviews them at each gate. Cannot manage the team.',
  viewer:'Reads the firm’s studies. Cannot create, review or change them.',
 };
 const ROLES=['owner','engineer','viewer'];
 const RANK={viewer:0,engineer:1,owner:2};
 const roleLabel=role=>ROLE_LABEL[role]||String(role||'');
 // Which screen the signed-in user gets: 'no-firm', 'join' or 'app'.
 function gate(me){
  if(!me)return 'loading';
  if(me.memberships?.length)return 'app';
  return me.pending_invitations?.length?'join':'no-firm';
 }
 // The current firm: the stored choice when still a member, else the first (by name).
 function currentFirm(me,storedOrgId){
  const list=me?.memberships||[];
  return list.find(m=>m.org_id===storedOrgId)||list[0]||null;
 }
 const isAdmin=firm=>firm?.role==='owner';
 const canCreate=firm=>Boolean(firm)&&RANK[firm.role]>=RANK.engineer;
 // The body of POST /v1/studies: org_id whenever the user is in several firms.
 function studyCreateBody(me,firm,body){
  return (me?.memberships?.length||0)>1&&firm?{...body,org_id:firm.org_id}:body;
 }
 // Studies of the current firm only, when the user belongs to several.
 function firmStudies(me,firm,studies){
  return (me?.memberships?.length||0)>1&&firm?studies.filter(s=>s.org_id===firm.org_id):studies;
 }
 // An accept/decline failure as a join-card state.
 function answerState(error){
  const kind=String(error?.type||'').split('/').pop();
  if(error?.status===404)return 'wrong-account';
  if(kind==='email-unverified')return 'unverified';
  const closed={'invitation-expired':'expired','invitation-revoked':'revoked','invitation-declined':'declined','invitation-accepted':'taken'};
  return closed[kind]||'error';
 }
 const ANSWER_COPY={
  'wrong-account':['This invitation is not for this account','It may have been sent to another email address. Sign in with the Google account for the invited address.'],
  unverified:['Use your invited email','Your sign-in does not confirm this email address. Sign in with Google using the invited address.'],
  expired:['This invitation has expired','Ask the firm’s admin to send a new invitation.'],
  revoked:['This invitation is no longer available','The firm’s admin revoked it. Contact them if you still need access.'],
  declined:['You declined this invitation','Ask the firm’s admin for a new one if you change your mind.'],
  taken:['This invitation was already used','Another account accepted it. Sign in with that account, or ask the firm’s admin for a new invitation.'],
  error:['The invitation could not be answered','Try again in a moment.'],
 };
 const STATUS_LABEL={pending:'Pending',accepted:'Accepted',declined:'Declined',revoked:'Revoked',expired:'Expired'};
 const STATUS_BADGE={pending:'review',accepted:'complete',declined:'',revoked:'',expired:'error'};
 // Admin actions refused by the API, as one sentence for a toast.
 function adminError(error){
  const kind=String(error?.type||'').split('/').pop();
  const known={
   'last-owner':'A firm must keep at least one admin. Make another member an admin first.',
   'already-member':'This email already belongs to a member of the firm. Change their role in the member list instead.',
   'invitation-pending':'This email already has a pending invitation. Revoke it to invite again with another role.',
   forbidden:'Only the firm’s admins can do this.',
  };
  return known[kind]||error?.detail||error?.message||'The change was not saved.';
 }
 function initials(text){return String(text||'').replace(/@.*/,'').split(/[^a-z0-9]+/i).filter(Boolean).slice(0,2).map(w=>w[0].toUpperCase()).join('')||'?'}
 const api={ROLE_LABEL,ROLE_SCOPE,ROLES,roleLabel,gate,currentFirm,isAdmin,canCreate,studyCreateBody,firmStudies,answerState,ANSWER_COPY,STATUS_LABEL,STATUS_BADGE,adminError,initials};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisFirm=api;
})(typeof globalThis==='object'?globalThis:this);
