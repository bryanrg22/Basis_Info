/* developer/client-errors-model.js: the operator page's query, rows, detail and escaping. */
const {test}=require('node:test');const assert=require('node:assert/strict');
const M=require('../developer/client-errors-model.js');
const record={id:'11111111-2222-4333-8444-555555555555',seq:9,fingerprint:'0123456789abcdef',issue_status:'in_progress',event_id:'e1',occurred_at:'2026-11-01T18:00:00+00:00',received_at:'2026-11-01T18:00:02.500+00:00',user_id:'aaaaaaaa-0000-4000-8000-000000000000',org_id:'bbbbbbbb-0000-4000-8000-000000000000',study_id:'cccccccc-0000-4000-8000-000000000000',revision:2,stage:'rooms_review',route:'#rooms/{id}',screen:'rooms',kind:'api',error_type:'ApiError',message:'POST /v1/studies/{id}/decisions failed with 409 <script>alert(1)</script>',code:'BASIS-STUDY-003',http_status:409,request_id:'r-2',build_sha:'abc1234def',web_version:'live-16',user_agent:'TestBrowser/1.0',
 event:{level:'warning',since_load_ms:1234,user:{uid:'u1',email:'eng@firm.test',role:'engineer'},firm:{org_id:'bbbbbbbb-0000-4000-8000-000000000000',org_name:'Acme'},source:{file:'live-app.js',line:120,column:9,function:'render'},http:{method:'POST',route:'/v1/studies/{id}/decisions',status:409,code:'BASIS-STUDY-003',request_id:'r-2',latency_ms:12,attempt:2,retry:true,problem_title:'Conflict',problem_detail:'stale'},stack:'TypeError: x\n    at render (live-app.js:120:9)',context:{action:'approve',secret:'***'},breadcrumbs:[{at:'2026-11-01T17:59:59+00:00',since_load_ms:900,kind:'click',message:'approve rooms_review',data:{action:'approve'}}],client:{platform:'MacIntel',language:'en-US',viewport:{width:1280,height:720},device_pixel_ratio:2,online:true,timezone:'America/New_York'},repeats:3,dropped:1,capture_version:'client-errors-1',_server:{clock_offset_ms:-120,api_git_sha:'srv1234',api_request_id:'req-report',remote_addr:'127.0.0.1'}}};
const issue={fingerprint:'0123456789abcdef',kind:'api',error_type:'ApiError',title:'POST /v1/studies/{id}/decisions failed with 409',route:'/v1/studies/{id}/decisions',code:'BASIS-STUDY-003',http_status:409,status:'in_progress',note:'looking at it',status_set_by:'aaaaaaaa-0000-4000-8000-000000000000',status_set_at:'2026-11-01T19:00:00+00:00',status_build_sha:'abc1234def',occurrences:7,first_seen_at:'2026-10-30T10:00:00+00:00',last_seen_at:'2026-11-01T18:00:00+00:00',last_build_sha:'abc1234def'};
test('query keeps only the filters with a value, widens a bare date to the whole UTC day, and carries limit and cursor',()=>{
 assert.deepEqual(M.query({org_id:' b ',code:'',kind:'api',status:'open',fingerprint:'0123456789abcdef',since:'2026-11-01',until:'2026-11-02',request_id:''},{limit:25,cursor:'77'}),{limit:25,org_id:'b',kind:'api',status:'open',fingerprint:'0123456789abcdef',since:'2026-11-01T00:00:00Z',until:'2026-11-02T23:59:59Z',cursor:'77'});
 assert.deepEqual(M.query({since:'2026-11-01T10:00:00Z'}),{limit:50,since:'2026-11-01T10:00:00Z'});
 assert.deepEqual(M.query(null),{limit:50});
});
test('rows render every record with its times, kind, message, code, ids and build, escaped',()=>{
 const html=M.rows([record]);
 assert.match(html,/data-id="11111111-2222-4333-8444-555555555555"/);
 assert.match(html,/2026-11-01 18:00:02 UTC/);assert.match(html,/client 2026-11-01 18:00:00 UTC/);
 assert.match(html,/kind-api/);assert.match(html,/ApiError/);assert.match(html,/status-in_progress">In progress/);assert.match(html,/409 BASIS-STUDY-003/);assert.match(html,/req r-2/);
 assert.match(html,/user aaaaaaaa/);assert.match(html,/firm bbbbbbbb/);assert.match(html,/study cccccccc r2/);assert.match(html,/abc1234d/);assert.match(html,/rooms · #rooms\/\{id\}/);
 assert.doesNotMatch(html,/<script>/);assert.match(html,/&lt;script&gt;/);
 assert.match(M.rows([]),/No records match/);
 assert.match(M.rows([{...record,org_id:null,study_id:null,http_status:null,code:null,request_id:null,build_sha:null,revision:null}]),/firm —/);
});
test('the detail shows every fact, the stack, the context, the breadcrumbs and the whole event, escaped, with the copy buttons and the issue box',()=>{
 const html=M.detail(record,issue);
 assert.match(html,/data-copy="brief"/);assert.match(html,/data-copy="json"/);
 assert.match(html,/7 occurrences/);assert.match(html,/<option value="in_progress" selected>/);assert.match(html,/value="looking at it"/);assert.doesNotMatch(html,/<option value="regressed"/);
 for(const needle of ['Clock offset','-120 ms','1234 ms','api · warning','eng@firm.test','Acme','revision 2 · rooms_review','live-app.js:120:9 in render','POST /v1/studies/{id}/decisions → 409 BASIS-STUDY-003','attempt 2 (retry)','API abc1234def (now srv1234)','capture client-errors-1','1280×720 @2x','3 · 1','req-report from 127.0.0.1','approve rooms_review','Last actions (1)','Whole event','Conflict stale'])assert.ok(html.includes(needle),needle);
 assert.doesNotMatch(html,/<script>alert/);
 assert.match(html,/class="stack"/);
 const bare=M.detail({...record,event:{}});assert.match(bare,/None recorded/);assert.match(bare,/API abc1234def \(now \?\)/);
 const unverified=M.detail({...record,org_id:null,study_id:null,event:{firm:{org_id:'x'},study:{study_id:'y'}}});
 assert.match(unverified,/unverified: x/);assert.match(unverified,/not visible to reporter: y/);
});
test('tree renders nested values and escapes strings; long strings go in pre',()=>{
 const html=M.tree({a:[1,{b:'<x>'}],c:null,d:'y'.repeat(130)});
 assert.match(html,/<dt>a<\/dt>/);assert.match(html,/&lt;x&gt;/);assert.match(html,/<i>null<\/i>/);assert.match(html,/<pre>y{130}<\/pre>/);
 assert.equal(M.tree([]),'<i>[]</i>');assert.equal(M.tree({}),'<i>{}</i>');
});
test('the issues table shows one row per fingerprint with its counts, build and a status form; a regressed issue keeps its option',()=>{
 const html=M.issueRows([issue,{...issue,fingerprint:'fedcba9876543210',status:'regressed',note:null,status_set_at:null}]);
 assert.match(html,/data-fingerprint="0123456789abcdef"/);assert.match(html,/href="#issue\/0123456789abcdef"/);
 assert.match(html,/<td class="num">7<\/td>/);assert.match(html,/first 2026-10-30 10:00:00 UTC/);assert.match(html,/last 2026-11-01 18:00:00 UTC/);assert.match(html,/abc1234d/);
 assert.match(html,/set 2026-11-01 19:00:00 UTC on abc1234d/);
 assert.match(html,/status-regressed">Regressed/);assert.match(html,/<option value="regressed" selected>/);
 assert.match(M.issueRows([]),/No issues match/);
});
test('the brief is one plain-text block with every fact, the stack, the context, the breadcrumbs and where to look next',()=>{
 const text=M.brief(record,issue);
 for(const needle of ['# Basis client error 11111111-2222-4333-8444-555555555555','Message: POST /v1/studies/{id}/decisions failed with 409 <script>alert(1)</script>','Kind: api (warning) · ApiError','Issue: 0123456789abcdef · In progress · 7 occurrences','note: looking at it','offset -120 ms from the server','1234 ms after page load','User: eng@firm.test (uid u1, engineer) · user_id aaaaaaaa','Firm: Acme · org_id bbbbbbbb','Study: cccccccc-0000-4000-8000-000000000000 · revision 2 · stage rooms_review','Page: screen rooms · route #rooms/{id}','Request: POST /v1/studies/{id}/decisions -> 409 BASIS-STUDY-003 · request_id r-2 · 12 ms · attempt 2 (retry)','Problem: Conflict stale','Source: live-app.js:120:9 in render','Build: API abc1234def (API now srv1234) · web live-16 · capture client-errors-1','Browser: TestBrowser/1.0','MacIntel · en-US · 1280x720 · @2x · online · America/New_York','Repeats / dropped: 3 / 1','Report request: req-report from 127.0.0.1','Stack:\nTypeError: x','Context:\n{\n  "action": "approve",\n  "secret": "***"\n}','Last actions (1, oldest first):',' 1. [2026-11-01T17:59:59+00:00 +900ms] click: approve rooms_review {"action":"approve"}','grep the API log for request_id=r-2','GET /v1/ops/client-errors/11111111-2222-4333-8444-555555555555','GET /v1/studies/cccccccc-0000-4000-8000-000000000000/timeline?event_type=client.error','docs/api/CLIENT_ERRORS.md'])assert.ok(text.includes(needle),needle);
 assert.doesNotMatch(text,/&lt;/);// plain text, not HTML-escaped
 const bare=M.brief({...record,study_id:null,org_id:null,event:{firm:{org_id:'x'},study:{study_id:'y'}}},null);
 assert.match(bare,/Issue: 0123456789abcdef/);assert.match(bare,/org_id unverified \(claimed x\)/);assert.match(bare,/y \(not visible to the reporter\)/);assert.match(bare,/request_id=\(none: not an API failure\)/);assert.match(bare,/Last actions \(0, oldest first\)/);
});
