#!/usr/bin/env node
/* Live-mode smoke test against a local stack, with synthetic files only.
 *
 *   docker compose -f docker-compose.yml -f docker-compose.web.yml up --build -d
 *   firebase emulators:start --only auth --project demo-basis
 *   node web/tests/live-smoke.mjs [--api http://127.0.0.1:8000] [--origin http://127.0.0.1:3100] [--start]
 *
 * Uses the browser's API client (web/live/api-client.js) to: sign in to the Auth emulator,
 * check the CORS preflight for the frontend origin, create a study (and replay the create
 * with the same Idempotency-Key), upload a generated PDF and PNG through signed URLs,
 * confirm them, read the bytes back, check problem+json errors, then archive the study.
 * --start also starts the workflow (it calls the real model providers: costs money).
 * Exits non-zero on the first failed check.
 */
import {createRequire} from 'node:module';
import {createHash,randomUUID} from 'node:crypto';
import {deflateSync} from 'node:zlib';
const require=createRequire(import.meta.url);
const {createClient,ApiError}=require('../live/api-client.js');
const arg=(name,fallback)=>{const i=process.argv.indexOf('--'+name);return i>0?process.argv[i+1]:fallback};
const API=arg('api','http://127.0.0.1:8000'),ORIGIN=arg('origin','http://127.0.0.1:3100'),EMULATOR=arg('emulator','http://127.0.0.1:9099'),START=process.argv.includes('--start');
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

const token=await emulatorToken();check(Boolean(token),'signed in to the Auth emulator');
const api=createClient({baseUrl:API,getToken:async()=>token});

const pre=await fetch(`${API}/v1/studies`,{method:'OPTIONS',headers:{Origin:ORIGIN,'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'authorization,content-type,idempotency-key'}});
check(pre.ok&&pre.headers.get('access-control-allow-origin')===ORIGIN,`CORS preflight allows ${ORIGIN} (set CORS_ALLOWED_ORIGINS)`);

const key=randomUUID(),name=`Smoke test ${new Date().toISOString()}`;
const study=await api.createStudy({property_name:name,room_aliases:{}},key);
check(study.id&&study.status==='draft'&&study.current_stage==='uploading','created a draft study');
const replay=await api.createStudy({property_name:name,room_aliases:{}},key);
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
 const archived=await api.archiveStudy(study.id);check(archived.status==='archived','archived the smoke-test study');
}
console.log(`\n${passed} checks passed against ${API}`);
