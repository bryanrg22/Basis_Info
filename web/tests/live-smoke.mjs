#!/usr/bin/env node
/* Live-mode smoke test against a local stack, with synthetic files only.
 *
 *   docker compose -f docker-compose.yml -f docker-compose.web.yml up --build -d
 *   firebase emulators:start --only auth --project demo-basis
 *   node web/tests/live-smoke.mjs --owner-email owner@example.com [--api http://127.0.0.1:8000] [--origin http://127.0.0.1:3100] [--start]
 *
 * Firms are invite-only, so the smoke user needs one. --owner-email (or BASIS_SMOKE_OWNER_EMAIL)
 * names an owner of a firm in the local database (python -m basis.admin create-firm, then
 * accepted): the script signs in as that email with the emulator's Google provider, invites a
 * throwaway emulator user as an engineer, and that user accepts. Without it, it checks that a
 * new user is refused (403 no-firm) and stops.
 *
 * Uses the browser's API client (web/live/api-client.js) to: sign in to the Auth emulator,
 * check the CORS preflight for the frontend origin, join the firm, create a study (and replay the create
 * with the same Idempotency-Key), upload a generated PDF and PNG through signed URLs,
 * confirm them, read the bytes back, check problem+json errors, then archive the study.
 * --start also starts the workflow (it calls the real model providers: costs money). Without it
 * the smoke user is removed from the firm at the end.
 * Exits non-zero on the first failed check.
 */
import {createRequire} from 'node:module';
import {createHash,randomUUID} from 'node:crypto';
import {deflateSync} from 'node:zlib';
const require=createRequire(import.meta.url);
const {createClient,ApiError}=require('../live/api-client.js');
const arg=(name,fallback)=>{const i=process.argv.indexOf('--'+name);return i>0?process.argv[i+1]:fallback};
const OWNER=arg('owner-email',process.env.BASIS_SMOKE_OWNER_EMAIL||''),API=arg('api','http://127.0.0.1:8000'),ORIGIN=arg('origin','http://127.0.0.1:3100'),EMULATOR=arg('emulator','http://127.0.0.1:9099'),START=process.argv.includes('--start');
let passed=0;
const check=(ok,what)=>{if(!ok){console.error(`✖ ${what}`);process.exit(1)}passed++;console.log(`✔ ${what}`)};

// A one-page PDF and a 4 x 3 PNG, generated: no real documents involved.
function pdf(){
 const objs=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>'];
 const text='BT /F1 12 Tf 20 100 Td (Synthetic smoke test) Tj ET';objs.push(`<< /Length ${text.length} >>\nstream\n${text}\nendstream`);
 let out='%PDF-1.4\n';const offsets=[];objs.forEach((o,i)=>{offsets.push(out.length);out+=`${i+1} 0 obj\n${o}\nendobj\n`});
 const xref=out.length;out+=`xref\n0 ${objs.length+1}\n0000000000 65535 f \n${offsets.map(o=>String(o).padStart(10,'0')+' 00000 n \n').join('')}trailer\n<< /Size ${objs.length+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
 return Buffer.from(out,'latin1');
}
function png(){
 const crcTable=Array.from({length:256},(_,n)=>{let c=n;for(let k=0;k<8;k++)c=c&1?0xedb88320^(c>>>1):c>>>1;return c>>>0});
 const crc=b=>{let c=0xffffffff;for(const x of b)c=crcTable[(c^x)&0xff]^(c>>>8);return (c^0xffffffff)>>>0};
 const chunk=(type,data)=>{const t=Buffer.from(type),len=Buffer.alloc(4),sum=Buffer.alloc(4);len.writeUInt32BE(data.length);sum.writeUInt32BE(crc(Buffer.concat([t,data])));return Buffer.concat([len,t,data,sum])};
 const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(4,0);ihdr.writeUInt32BE(3,4);ihdr[8]=8;ihdr[9]=2;
 const rows=Buffer.concat(Array.from({length:3},(_,y)=>Buffer.concat([Buffer.from([0]),Buffer.from(Array.from({length:12},(_,i)=>(i*20+y*60)%256))])));
 return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',ihdr),chunk('IDAT',deflateSync(rows)),chunk('IEND',Buffer.alloc(0))]);
}
async function emulatorToken(){
 const email=`smoke-${randomUUID().slice(0,8)}@example.test`,password=randomUUID();// a throwaway emulator account
 const r=await fetch(`${EMULATOR}/identitytoolkit.googleapis.com/v1/accounts:signUp?key=demo-api-key`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password,returnSecureToken:true})});
 if(!r.ok)throw new Error(`Auth emulator signUp failed (${r.status}): is it running on ${EMULATOR}?`);
 return (await r.json()).idToken;
}

// The emulator's Google provider: an unsigned id_token is accepted, and its email is verified.
async function googleToken(email){
 const idToken=JSON.stringify({sub:`smoke-${createHash('sha256').update(email).digest('hex').slice(0,20)}`,email,email_verified:true});
 const r=await fetch(`${EMULATOR}/identitytoolkit.googleapis.com/v1/accounts:signInWithIdp?key=demo-api-key`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({postBody:`id_token=${encodeURIComponent(idToken)}&providerId=google.com`,requestUri:'http://localhost',returnIdpCredential:true,returnSecureToken:true})});
 if(!r.ok)throw new Error(`Auth emulator signInWithIdp failed (${r.status})`);
 return (await r.json()).idToken;
}

const stranger=createClient({baseUrl:API,getToken:async()=>await emulatorToken()});
const refused=await stranger.listStudies().catch(e=>e);
check(refused instanceof ApiError&&refused.status===403&&refused.kind==='no-firm','a user in no firm is refused (403 no-firm)');
if(!OWNER){console.log(`\n${passed} checks passed. Pass --owner-email <a firm owner> for the study checks (web/README.md).`);process.exit(0)}

const ownerToken=await googleToken(OWNER),owner=createClient({baseUrl:API,getToken:async()=>ownerToken});
const firm=(await owner.me()).memberships.find(m=>m.role==='owner');
check(Boolean(firm),`${OWNER} is an owner of a firm (${firm?.org_name})`);
const smokeEmail=`smoke-${randomUUID().slice(0,8)}@example.test`,token=await googleToken(smokeEmail);
check(Boolean(token),'signed in to the Auth emulator');
const api=createClient({baseUrl:API,getToken:async()=>token});
await owner.invite(firm.org_id,{email:smokeEmail,role:'engineer'});
const invitation=(await api.me()).pending_invitations.find(i=>i.org_id===firm.org_id);
check(Boolean(invitation),'the smoke user sees the invitation in /v1/me');
const joined=await api.acceptInvitation(invitation.id);
check(joined.membership?.role==='engineer','the smoke user accepted and is an engineer of the firm');
const me=await api.me();

const pre=await fetch(`${API}/v1/studies`,{method:'OPTIONS',headers:{Origin:ORIGIN,'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'authorization,content-type,idempotency-key'}});
check(pre.ok&&pre.headers.get('access-control-allow-origin')===ORIGIN,`CORS preflight allows ${ORIGIN} (set CORS_ALLOWED_ORIGINS)`);

const key=randomUUID(),name=`Smoke test ${new Date().toISOString()}`;
const study=await api.createStudy({property_name:name,room_aliases:{},org_id:firm.org_id},key);
check(study.id&&study.status==='draft'&&study.current_stage==='uploading','created a draft study');
const replay=await api.createStudy({property_name:name,room_aliases:{},org_id:firm.org_id},key);
check(replay.id===study.id,'replaying the create with the same Idempotency-Key returns the same study');
check((await api.listStudies()).items.some(s=>s.id===study.id),'the study is in the list');

for(const [kind,filename,type,bytes] of [['appraisal','appraisal.pdf','application/pdf',pdf()],['photo','photo-01.png','image/png',png()]]){
 const sha256=createHash('sha256').update(bytes).digest('hex');
 const ticket=await api.registerDocument(study.id,{filename,bytes:bytes.length,sha256,kind,content_type:type});
 check(ticket.upload.url.startsWith('http'),`registered the ${kind} and got a signed upload URL`);
 const headers={...ticket.upload.headers};
 const put=await fetch(ticket.upload.url,{method:ticket.upload.method||'PUT',headers,body:bytes});
 check(put.ok,`uploaded the ${kind} to the signed URL (${put.status})`);
 const doc=await api.completeDocument(study.id,ticket.document.id);
 check(Boolean(doc.uploaded_at),`confirmed the ${kind} (SHA-256 checked by the API)`);
 const back=Buffer.from(await (await api.documentContent(study.id,doc.id)).arrayBuffer());
 check(back.equals(bytes),`read the ${kind} back byte for byte`);
}
const detail=await api.getStudy(study.id);
check(detail.documents.length===2&&detail.documents.every(d=>d.uploaded_at),'the study lists both confirmed documents');

const notFound=await api.getStudy(randomUUID()).catch(e=>e);
check(notFound instanceof ApiError&&notFound.status===404,'an unknown study is a 404 problem');
const early=await api.decide(study.id,{stage:'appraisal_review',revision:1,decision:'approve',corrections:[]}).catch(e=>e);
check(early instanceof ApiError&&early.status===409,`a decision before the gate opens is refused (409 ${early.kind})`);
const bad=await api.addObject(study.id,detail.documents.find(d=>d.kind==='photo').id,{kind:'point',label:'not_a_label',point:{x:.5,y:.5}}).catch(e=>e);
check(bad instanceof ApiError&&bad.status===422&&bad.errors.length>0,'an invalid object label is a 422 with field errors');

if(START){
 const started=await api.start(study.id);check(Boolean(started.workflow_id),'started the workflow (real providers)');
 const w=await api.workflow(study.id);check(Boolean(w.execution_status),`the workflow reports ${w.execution_status}`);
}else{
 const archived=await owner.archiveStudy(study.id);check(archived.status==='archived','archived the smoke-test study (owner)');
 await owner.removeMember(firm.org_id,me.user.id);
 const gone=await api.getStudy(study.id).catch(e=>e);
 check(gone instanceof ApiError&&gone.status===404,'removed from the firm, the smoke user no longer sees the study');
}
console.log(`\n${passed} checks passed against ${API}`);
