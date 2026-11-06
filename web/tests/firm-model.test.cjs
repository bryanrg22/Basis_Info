// The firm pages' rules (live/firm-model.js): search and why it matched, where a study stands,
// what Home puts in front of whom, the feed's sentences and days, signer details. Also the same
// rules over the sample API's synthetic firm (live/sample-api.js), so the pages' data and the
// rules agree.
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const M=require('../live/firm-model.js'),A=require('../live/sample-api.js');
const fetchAsset=p=>fs.readFileSync(path.join(__dirname,'..',p));
const NOW=Date.parse('2026-11-05T22:30:00Z');
const study=(o={})=>({id:'s1',property_name:'Harbor House',status:'waiting_review',current_stage:'components_review',created_by:'u1',created_by_name:'Jordan Lee',created_by_email:'jordan.lee@example.test',updated_at:'2026-11-05T20:00:00Z',created_at:'2026-10-30T10:00:00Z',summary:{address:'1200 Harbor Boulevard',city:'Ventura',state:'CA',zip:'93001',county:'Ventura',parcel_number:'071-0-224-115',client:'Harbor Property Partners',borrower:'Harbor House Holdings LLC',taxpayer:'Harbor House Holdings LLC',file_number:'AP-2026-0141'},...o});

test('search: every word must match somewhere; the field shown is the one that holds the whole query',()=>{
 const s=study();
 assert.deepEqual(M.match(s,'harbor property partners'),{field:'Client',value:'Harbor Property Partners'});
 assert.equal(M.match(s,'harbor').field,'Property','the name wins when it holds the query');
 assert.equal(M.match(s,'0141').field,'File number');
 assert.equal(M.match(s,'071-0-224').field,'Parcel number');
 assert.equal(M.match(s,'ventura county').field,'County');
 assert.equal(M.match(s,'jordan').field,'Engineer');
 assert.equal(M.match(s,'boulevard 93001').field,'Address','words in any order, one field');
 assert.equal(M.match(s,'partners jordan').field,'Client','across fields: the first word’s field');
 assert.equal(M.match(s,'harbor cedar'),null,'a word nowhere: no match');
 assert.deepEqual(M.match(s,'  '),{field:null,value:null},'a blank query matches everything');
 assert.equal(M.match(study({property_name:'Café Élan',summary:null}),'cafe elan').field,'Property','accents and case are ignored');
 assert.equal(M.match(study({summary:null}),'harbor property'),null,'without the proposed summary only the name and engineer are searched');
});

test('highlight marks every word, escapes HTML, and leaves text whose length changes alone',()=>{
 assert.equal(M.highlight('Harbor House','har hou'),'<mark>Har</mark>bor <mark>Hou</mark>se');
 assert.equal(M.highlight('<b>Harbor</b>','harbor'),'&lt;b&gt;<mark>Harbor</mark>&lt;/b&gt;');
 assert.equal(M.highlight('ﬁle','file'),'ﬁle','no partial marks when normalization changes length (the ligature becomes two letters)');
 assert.equal(M.highlight('Straße','stra'),'<mark>Stra</mark>ße','a length-keeping text is marked');
 assert.equal(M.highlight('Harbor',''),'Harbor');
});

test('the strip: steps before the current one are done, the current one carries the status tone',()=>{
 const tones=s=>M.strip(s).map(x=>x.state==='current'?x.tone:x.state[0]).join(' ');
 assert.equal(tones(study()),'d d d review t t t','components review: three done, the fourth needs review');
 assert.equal(tones(study({status:'processing',current_stage:'extracting'})),'d progress t t t t t');
 assert.equal(tones(study({status:'failed',current_stage:'extracting'})),'d danger t t t t t');
 assert.equal(tones(study({status:'draft',current_stage:'uploading'})),'review t t t t t t');
 // An archived study keeps where it stopped, in a neutral tone: an archived draft is not "All 7 done".
 const archived=M.strip(study({status:'archived',current_stage:'uploading'}));
 assert.deepEqual(archived.map(x=>x.state),['current','todo','todo','todo','todo','todo','todo']);assert.ok(archived.every(x=>x.tone==='idle'));
 assert.ok(M.strip(study({status:'archived',current_stage:'completed'})).every(x=>x.tone==='idle'),'an archived finished study is not green either');
 assert.equal(M.strip(study({status:'completed',current_stage:'completed'}),{tone:'done',label:'Signed'}).every(x=>x.state==='done'),true,'signed: all done');
 assert.equal(tones(study({status:'completed',current_stage:'completed'})),'d d d d d d progress','delivery unknown: the last step is still working');
 assert.equal(M.strip(study({components_gate:false,current_stage:'assets_review'})).length,6,'a legacy study has no Components step');
 assert.equal(tones(study({components_gate:false,current_stage:'assets_review'})),'d d d review t t');
});

test('next: what each study needs and where it goes',()=>{
 const n=(o,d)=>{const x=M.next(study(o),d);return [x.label,x.tone,x.screen,x.action]};
 assert.deepEqual(n({}),['Review components','review','components',true]);
 assert.deepEqual(n({current_stage:'appraisal_review'}),['Review appraisal','review','appraisal',true]);
 assert.deepEqual(n({status:'processing',current_stage:'components_processing'}),['Counting components','progress','processing',false]);
 assert.deepEqual(n({status:'failed',current_stage:'extracting'}),['Failed at Appraisal · retry','danger','processing',true]);
 assert.deepEqual(n({status:'draft',current_stage:'uploading'}),['Finish uploading','review','processing',true]);
 assert.deepEqual(n({status:'completed',current_stage:'completed'},{label:'Needs signature',tone:'review'}),['Sign report','review','report',true]);
 assert.deepEqual(n({status:'completed',current_stage:'completed'},{label:'Update required',tone:'review'}),['Update the report','review','report',true]);
 assert.deepEqual(n({status:'completed',current_stage:'completed'},{label:'Signed',tone:'done'}),['Signed','done','processing',false]);
 assert.deepEqual(n({status:'completed',current_stage:'completed'}),['Checking…','idle','processing',false],'never "Signed" before the report state loads');
 assert.deepEqual(n({status:'archived',current_stage:'completed'}),['Archived','idle','processing',false]);
});

test('Home: an engineer’s own studies that wait on them, failures and signatures first; an admin sees everyone’s',()=>{
 const list=[study({id:'a',updated_at:'2026-11-05T21:00:00Z'}),study({id:'b',status:'failed',current_stage:'extracting'}),study({id:'c',status:'completed',current_stage:'completed'}),study({id:'d',created_by:'u2',created_by_name:'Priya Shah'}),study({id:'e',status:'processing',current_stage:'extracting'}),study({id:'f',status:'archived',current_stage:'completed'}),study({id:'g',status:'completed',current_stage:'completed',updated_at:'2026-09-01T00:00:00Z'})];
 const deliveryOf=s=>s.id==='c'?{label:'Needs signature',tone:'review'}:s.id==='g'?{label:'Signed',tone:'done'}:null;
 const h=M.home(list,{id:'u1'},{deliveryOf,now:NOW});
 assert.deepEqual(h.needs.map(r=>r.study.id),['b','c','a'],'failed, then sign, then review; a colleague’s study is not mine');
 assert.ok(![...h.needs,...h.working,...h.finished].some(r=>r.study.id==='d'),'a colleague’s waiting study is in none of my lists');
 assert.deepEqual(h.working.map(r=>r.study.id),['e']);
 assert.deepEqual(h.finished.map(r=>r.study.id),['g']);
 assert.equal(h.signedThisMonth,0,'signed in September is not this month');
 const admin=M.home(list,{id:'u9'},{admin:true,deliveryOf,now:NOW});
 assert.deepEqual(admin.needs.map(r=>r.study.id).sort(),['a','b','c','d']);
 assert.ok(!admin.needs.some(r=>r.study.id==='f'),'archived studies stay off Home');
});

test('the feed reads as sentences: decisions with their step and corrections, Basis’s own steps, signatures',()=>{
 const ev=(o)=>({seq:1,at:'2026-11-05T20:00:00Z',actor_type:'engineer',actor_id:'u1',actor_name:'Priya Shah',study_id:'s1',property_name:'Juniper Flats',stage:'rooms_review',revision:1,detail:null,after:null,...o});
 const f=M.feed([
  ev({event_type:'review.decided',after:{decision:'approve'},detail:{decision:'approve',corrections:0}}),
  ev({event_type:'review.decided',at:'2026-11-05T19:00:00Z',after:{decision:'reject'},detail:{decision:'reject',comment:'Kitchen is two rooms',corrections:[1,2]}}),
  ev({event_type:'gate.opened',actor_type:'system',actor_id:null,at:'2026-11-05T18:00:00Z'}),
  ev({event_type:'report.signed',stage:'completed',revision:2,at:'2026-11-05T21:00:00Z'}),
  ev({event_type:'activity.completed',actor_type:'system',actor_id:null}),
  ev({event_type:'stage.entered'}),ev({event_type:'correction.recorded'}),
 ]);
 assert.deepEqual(f.map(e=>`${e.actor} ${e.verb}${e.detail?' · '+e.detail:''}`),['Priya Shah signed revision 2','Priya Shah approved Rooms','Priya Shah requested corrections at Rooms · 2 corrections · Kitchen is two rooms','Basis opened the Rooms review'],'bookkeeping (activities, stage moves, recorded corrections) is left out');
 assert.deepEqual(f.map(e=>e.screen),['report','rooms','rooms','rooms'],'each entry links to its step');
 assert.ok(!f.some(e=>/[0-9a-f]{8}-/.test(e.verb+e.actor)),'never an id as the label');
});

test('a study’s creation, uploads and start by one person read as one entry',()=>{
 const base={actor_type:'engineer',actor_id:'u5',actor_name:'Marcus Bell',study_id:'s7',property_name:'Willow Creek Cottages',stage:'uploading',revision:1};
 const t=i=>new Date(Date.parse('2026-11-05T16:00:00Z')+i*60000).toISOString();
 const items=[{...base,event_type:'study.created',at:t(0)},{...base,event_type:'document.uploaded',at:t(1),detail:{kind:'appraisal'}},...[2,3,4].map(i=>({...base,event_type:'document.uploaded',at:t(i),detail:{kind:'photo'}})),{...base,event_type:'study.started',at:t(5)}];
 const f=M.feed(items);
 assert.equal(f.length,1);assert.equal(f[0].verb,'created the study and uploaded the appraisal and 3 photos');assert.equal(f[0].screen,'files');
 const later=M.feed([...items,{...base,event_type:'document.uploaded',at:'2026-11-05T19:00:00Z',detail:{kind:'photo'}}]);
 assert.deepEqual(later.map(e=>e.verb),['uploaded 1 photo','created the study and uploaded the appraisal and 3 photos'],'an upload hours later is its own entry');
});

test('firm events for admins name people and roles, not ids',()=>{
 const name=id=>({u1:'Alex Morgan',u3:'Sam Rivera',u6:'Dana Ortiz'}[id]||null);
 const f=M.feed([],{name,firmEvents:[
  {at:'2026-11-04T10:00:00Z',event_type:'invitation.created',actor_id:'u1',after:{email:'riley.park@example.test',role:'viewer'}},
  {at:'2026-10-24T10:00:00Z',event_type:'invitation.accepted',actor_id:'u6',subject_user_id:'u6',after:{email:'dana.ortiz@example.test',role:'engineer'}},
  {at:'2026-10-15T10:00:00Z',event_type:'member.role_changed',actor_id:'u1',subject_user_id:'u3',before:{role:'engineer'},after:{role:'viewer'}},
  {at:'2026-10-14T10:00:00Z',event_type:'invitation.revoked',actor_id:null,before:{email:'x@example.test'},detail:{reason:'expired'}},
 ]});
 assert.deepEqual(f.map(e=>`${e.actor} ${e.verb}`),['Alex Morgan invited riley.park@example.test as a viewer','Dana Ortiz joined the firm as an engineer','Alex Morgan made Sam Rivera a viewer'],'an expiry closed by a re-invite is not news');
});

test('days: Today, Yesterday, then dates, in the viewer’s time zone; relative times',()=>{
 const at=h=>new Date(NOW-h*3600000).toISOString();
 const g=M.byDay([{at:at(1)},{at:at(2)},{at:at(26)},{at:at(80)}],NOW);
 // The expected labels are computed in the zone the test runs in, as the page does.
 const day=t=>new Date(Date.parse(t)).toDateString(),label=(t,o={})=>new Intl.DateTimeFormat('en-US',{weekday:'long',month:'short',day:'numeric',...o}).format(new Date(t));
 const expect=[at(1),at(26),at(80)].map(t=>day(t)===day(new Date(NOW).toISOString())?'Today':day(t)===day(new Date(NOW-86400000).toISOString())?'Yesterday':label(t));
 assert.deepEqual(g.map(x=>x.label),[...new Set([at(1),at(2),at(26),at(80)].map(t=>expect[[at(1),at(26),at(80)].findIndex(u=>day(u)===day(t))]))]);
 assert.equal(g.reduce((n,x)=>n+x.entries.length,0),4);
 assert.equal(M.byDay([{at:'2025-10-01T18:00:00Z'}],NOW)[0].label,label('2025-10-01T18:00:00Z',{year:'numeric'}),'another year says its year');
 assert.equal(M.ago(new Date(NOW-30000).toISOString(),NOW),'just now');
 assert.equal(M.ago(new Date(NOW-12*60000).toISOString(),NOW),'12 min ago');
 assert.equal(M.ago('not a date',NOW),'');
});

test('signer details: name and credentials required, a license number with its state, a real state code',()=>{
 assert.deepEqual(M.signerBody({name:' Jordan Lee ',credentials:'PE',license_number:'PE-77120',license_state:'ca'}),{body:{name:'Jordan Lee',credentials:'PE',license_number:'PE-77120',license_state:'CA'},problems:[]});
 assert.deepEqual(M.signerBody({name:'A',credentials:'EIT'}).problems,[],'no license is allowed');
 assert.equal(M.signerBody({name:'',credentials:''}).problems.length,2);
 assert.match(M.signerBody({name:'A',credentials:'PE',license_number:'1'}).problems.join(),/state/);
 assert.match(M.signerBody({name:'A',credentials:'PE',license_state:'CA'}).problems.join(),/license number/);
 assert.match(M.signerBody({name:'A',credentials:'PE',license_number:'1',license_state:'XX'}).problems.join(),/state/);
 assert.equal(M.signerLine({credentials:'PE',license_state:'CA',license_number:'PE-1'}),'PE · CA PE-1');
});

/* ---------- the sample firm through the rules ---------- */
async function firm(role){const a=A.create({now:()=>NOW,role,fetchAsset});const studies=(await a.call('GET','/v1/studies?include_archived=true')).body.items;return {a,studies}}

test('the sample firm: a client name finds its study, and every study is found by its file number',async()=>{
 const {studies}=await firm('engineer');
 assert.deepEqual(studies.filter(s=>M.match(s,'Harbor Property Partners')).map(s=>s.property_name),['Harbor House']);
 for(const s of studies)assert.equal(M.match(s,s.summary.file_number).field,'File number',s.property_name);
 assert.ok(studies.filter(s=>M.match(s,'Harbor Bank')).length>=3,'a lender shared by several studies finds them all');
});

test('the sample firm: an engineer’s Home holds their own reviews and signature; the admin’s holds every study that waits',async()=>{
 const e=await firm('engineer'),me=(await e.a.call('GET','/v1/me')).body.user;
 const deliveryOf=s=>s.status==='completed'?{label:s.property_name==='Linden Avenue Townhomes'?'Needs signature':s.property_name==='Redwood Commons'?'Update required':'Signed',tone:['Linden Avenue Townhomes','Redwood Commons'].includes(s.property_name)?'review':'done'}:null;
 const h=M.home(e.studies,me,{deliveryOf,now:NOW});
 assert.deepEqual(h.needs.map(r=>r.study.property_name).sort(),['Bayview Duplex','Harbor House','Linden Avenue Townhomes','Maple Row Lodge']);
 const admin=await firm('admin'),am=(await admin.a.call('GET','/v1/me')).body.user,ha=M.home(admin.studies,am,{admin:true,deliveryOf,now:NOW});
 assert.equal(ha.needs.length,9,'every waiting study: four reviews, two to sign, one failed, one draft, Harbor House');
 assert.ok(ha.needs[0].next.tone==='danger','the failed study leads');
});

test('the sample firm’s feed (proposed P7): names, studies and steps, yesterday has entries, nothing reads as an id',async()=>{
 const {a,studies}=await firm('engineer'),org=(await a.call('GET','/v1/me')).body.memberships[0].org_id;
 const feed=(await a.call('GET',`/v1/orgs/${org}/feed?limit=200`)).body.items;
 const entries=M.feed(feed,{studies});
 assert.ok(entries.length>=20);
 assert.ok(entries.every(e=>e.actor&&e.verb&&!/[0-9a-f]{8}-[0-9a-f]{4}/.test(`${e.actor} ${e.verb} ${e.study?.name}`)));
 const days=M.byDay(entries,NOW);assert.equal(days[0].label,'Today');assert.ok(days.some(d=>d.label==='Yesterday'&&d.entries.some(e=>e.actorId&&e.actor!=='Jordan Lee')),'a colleague did something yesterday');
 assert.ok(entries.some(e=>e.actor==='Alex Morgan'&&/approved Components/.test(e.verb)),'a decision by someone other than the study’s engineer is theirs');
});

// The backend's own event names and shapes (api/routes/report.py, activities/export_report.py):
// the sample once used invented ones, so the feed passed its tests and missed the real events.
test('the real report events: study details saved, and an export that wrote no report',()=>{
 const e=over=>({at:'2026-11-05T20:00:00Z',actor_type:'engineer',actor_id:'u1',stage:'completed',revision:2,study_id:'s',property_name:'X',...over});
 assert.match(M.studyEntry(e({event_type:'study.report_answers_set'})).verb,/updated the study details/);
 const skipped=M.studyEntry(e({event_type:'export.written',actor_type:'system',actor_id:null,detail:{uri:'u',sha256:'h',report:{skipped:{code:'BASIS-REPORT-002',message:'No firm profile'}}}}));
 assert.match(skipped.verb,/could not write the report/);
 const written=M.studyEntry(e({event_type:'export.written',actor_type:'system',actor_id:null,detail:{uri:'u',sha256:'h',report:{files:{pdf:{uri:'p'}},draft:true}}}));
 assert.doesNotMatch(written.verb,/could not/);
 const sample=fs.readFileSync(path.join(__dirname,'../live/sample-api.js'),'utf8');
 assert.doesNotMatch(sample,/report_answers\.saved/,'the sample emits the backend’s event name');
});
