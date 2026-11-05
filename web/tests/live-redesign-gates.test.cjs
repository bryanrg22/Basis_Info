const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const G=require('../live/gates.js'),M=require('../live/manual-add.js'),V=require('../vision-model.js');
function boot(components_gate,current_stage,status='waiting_review'){
 const study={id:'study',property_name:'Gate regression',components_gate,current_stage,status,revision:1};
 const room={id:'kitchen',name:'Kitchen',room_type:'kitchen',photos:[{document_id:'photo',filename:'Kitchen.png'}],inventory:[{component_id:'c1',label:'light_fixture',count:1,material:'metal'},{component_id:'c2',label:'light_fixture',count:6}],suggested_counts:[{label:'light_fixture',suggested:8,inventory_count:7,photo_count:8}]};
 const rooms={items:[room,{id:'living',name:'Living room',room_type:'living',photos:[],inventory:[],suggested_counts:[]}]};
 const items=[{id:'object',label:'light_fixture',counted:true,source:'manual',box:{x1:.1,y1:.1,x2:.2,y2:.2}}];
 const live={state:{study,studyId:study.id,docId:'photo',corrections:{},viewer:{}},api:{},STAGE:{},gates:G,labelName:s=>s,actions:{},afterRender:[],withStudy:fn=>fn(study),load:()=>({done:true,data:rooms}),gateOpen:stage=>status==='waiting_review'&&current_stage===stage,route:(screen,id=study.id,doc,origin)=>`#${screen}/${id}${doc?'/'+doc:''}${origin?'/'+origin:''}`,steps:()=>'',render:()=>{},photo:()=>'<img>',loading:()=>'',failed:()=>''};
 let key=0;const calls=[];live.api.decide=async(id,body,k)=>{calls.push({id,body,key:k});return{corrections_recorded:body.corrections.length}};
 live.stopPolling=()=>{};live.invalidate=()=>{};live.reloadStudy=async()=>{};live.go=()=>{};live.guarded=(name,fn)=>live.lastAction=fn();
 const root={querySelector:()=>null,addEventListener(){}};
 const sandbox={BasisAppraisalSource:require('../live/appraisal-source.js'),BasisApi:{newKey:()=>`test-key-${++key}`},BasisLive:live,BasisManualAdd:M,BasisVision:V,RoomPresentation:require('../room-model.js'),root,render:()=>{},state:{screen:'viewer'},location:{hash:'#viewer/study/photo'},document:{documentElement:{classList:{toggle(){}}}},esc:s=>String(s??''),icon:()=>'',heading:(t,p='')=>`<h1>${t}</h1><p>${p}</p>`,toast(){},modal(){const controls={'#live-decision-comment':{value:'QA decision'},'#live-confirm':{}};const m={querySelector:s=>controls[s],remove(){}};sandbox.lastModal=m;return m},closeBtn:'',viewer(){},appraisal(){},rooms(){},assets(){},assetDetail(){},complete(){}};
 vm.createContext(sandbox);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../live/component-previews.js'),'utf8'),sandbox);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../live/live-review.js'),'utf8'),sandbox);
 const loads=[];live.load=key=>{loads.push(key);return {done:true,data:key.startsWith('objects:')?{items}:rooms}};
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../vision-workbench.js'),'utf8'),sandbox);
 return {live,ctx:sandbox,room,rooms,study,calls,loads};
}
test('six-step Rooms offers grouping corrections without inventories or object editing',()=>{
 const {ctx}=boot(true,'rooms_review');
 const html=ctx.rooms();
 assert.doesNotMatch(html,/data-component-preview/);assert.match(html,/data-live="move-photo"/);assert.match(html,/data-live="edit-room"/);
 assert.doesNotMatch(html,/live-inventory|data-live="edit-component"|data-live="flag-photo"/);
 const photo=ctx.viewer();assert.match(photo,/Back to rooms/);assert.match(photo,/vision-photo-only/);assert.doesNotMatch(photo,/vision-readonly|vision-inspector|vision-pin-layer|vision-drawing|All types|Hide outlines|data-vision="add"/);
});
test('explicit Rooms entry stays photo-only at every study stage without requesting objects',()=>{
 for(const [stage,status] of [['rooms_review','waiting_review'],['components_review','waiting_review'],['completed','completed']]){
  const {ctx,live,loads}=boot(true,stage,status);live.state.viewerOrigin='rooms';
  const html=ctx.viewer();assert.match(html,/aria-label="Room photos"/);assert.match(html,/Back to rooms/);
  assert.match(html,/aria-label="Zoom in"/);assert.match(html,/aria-label="Next photo"/);
  assert.doesNotMatch(html,/vision-readonly|vision-inspector|vision-pin-layer|vision-drawing|All types|Hide outlines|Room inventory/);
  assert.equal(loads.filter(k=>k.startsWith('objects:')).length,0);
 }
});
test('explicit Components entry retains component review even during room grouping',()=>{
 const {ctx,live,loads}=boot(true,'rooms_review');live.state.viewerOrigin='components';
 const html=ctx.viewer();assert.match(html,/Components inspector/);assert.match(html,/Hide outlines/);
 assert.match(html,/Back to components/);assert.match(html,/vision-readonly/);
 assert.doesNotMatch(html,/vision-photo-only|data-vision="add"/);
 assert.equal(loads.filter(k=>k.startsWith('objects:')).length,1);
});
test('room photo viewer follows unsaved moves and names without changing the draft',()=>{
 const {ctx,live,room}=boot(true,'rooms_review');live.state.viewerOrigin='rooms';
 const draft={rooms:[{id:'new-room',name:'New bedroom',photos:[...room.photos,{document_id:'second',filename:'Second.png'}]}],unassigned:[]};
 live.state.placement={key:'study:1',draft};const before=JSON.stringify(draft);
 let html=ctx.viewer();assert.match(html,/<h1>New bedroom<\/h1>/);assert.match(html,/Photo 2: Second.png/);assert.match(html,/>1 \/ 2</);
 live.state.docId='second';html=ctx.viewer();assert.match(html,/>2 \/ 2</);assert.match(html,/Second.png/);
 assert.equal(JSON.stringify(draft),before);
 live.state.docId='photo';live.state.viewerOrigin='components';html=ctx.viewer();assert.match(html,/<h1>Kitchen /);assert.doesNotMatch(html,/New bedroom/);
});
test('room photo viewer ignores another study or revision draft',()=>{
 for(const key of ['other:1','study:2']){
  const {ctx,live}=boot(true,'rooms_review');live.state.viewerOrigin='rooms';
  live.state.placement={key,draft:{rooms:[],unassigned:[]}};
  assert.match(ctx.viewer(),/<h1>Kitchen<\/h1>/);
 }
});
test('initial unassigned photos and locally assigned photos have a usable photo viewer',()=>{
 const {ctx,live,rooms}=boot(true,'rooms_review');live.state.viewerOrigin='rooms';live.state.docId='unplaced';
 rooms.unassigned_photos=[{document_id:'unplaced',filename:'Unknown.png'}];
 assert.match(ctx.viewer(),/<h1>Needs placement<\/h1>/);
 live.state.placement={key:'study:1',draft:{rooms:[{id:'new',name:'Study',photos:rooms.unassigned_photos}],unassigned:[]}};
 const html=ctx.viewer();assert.match(html,/<h1>Study<\/h1>/);assert.doesNotMatch(html,/Needs placement/);
});
test('Rooms and Components links carry their entry context',()=>{
 const {ctx,live}=boot(true,'completed','completed');
 assert.match(ctx.rooms(),/href="#viewer\/study\/photo\/rooms"/);
 assert.match(live.componentsScreen(),/href="#viewer\/study\/photo\/components"/);
});
test('Components owns counts and photo edits, and returns the fitted viewer to Components',()=>{
 const {ctx,live}=boot(true,'components_review');
 const html=live.componentsScreen();
 assert.equal((html.match(/data-component-preview="photo"/g)||[]).length,2);
 assert.match(html,/live-inventory/);assert.match(html,/data-live="use-suggested"/);assert.match(html,/data-live="flag-photo"/);
 assert.doesNotMatch(html,/data-live="edit-room"|data-live="move-photo"/);
 const photo=ctx.viewer();assert.match(photo,/href="#components\/study"/);assert.match(photo,/Back to components/);assert.match(photo,/data-vision="add"[^>]*>[^<]*(<[^>]*>[^<]*)*Add object</);assert.doesNotMatch(photo,/vision-readonly/);
});
test('legacy Rooms keeps grouping, inventories and editable photo review together',()=>{
 const {ctx}=boot(false,'rooms_review');const html=ctx.rooms();
 assert.match(html,/data-live="edit-room"/);assert.match(html,/live-inventory/);assert.match(html,/data-live="use-suggested"/);
 const photo=ctx.viewer();assert.match(photo,/Back to rooms/);assert.match(photo,/href="#rooms\/study"/);assert.match(photo,/data-vision="add"[^>]*>[^<]*(<[^>]*>[^<]*)*Add object</);
});
test('Use N stages the row delta at the correct gate and preserves other edits',()=>{
 for(const gate of [true,false]){
  const {live,ctx}=boot(gate,gate?'components_review':'rooms_review'),stage=G.editStage(live.state.study);
  live.state.corrections[stage]=new Map([['component:c1',{after:{material:'glass'},reason:'Checked source'}]]);
  live.actions['use-suggested']({dataset:{room:'kitchen'}},'c1');
  const c=live.state.corrections[stage].get('component:c1');
  assert.equal(c.after.count,2);assert.equal(c.after.material,'glass');assert.equal(c.reason,'Checked source');
  assert.equal(c.after.count+6,8);assert.equal(live.state.corrections[gate?'rooms_review':'components_review'],undefined);
  live.state.corrections[stage].set('room_component:kitchen:window',{key:'room_component:kitchen:window',entity_type:'room_component',entity_id:'kitchen',after:{label:'window',count:1}});
  assert.match(gate?live.componentsScreen():ctx.rooms(),new RegExp('data-stage="'+stage+'"'));
 }
});
test('processing and finished components remain inspection-only',()=>{
 for(const [stage,status] of [['components_processing','processing'],['assets_review','waiting_review'],['completed','completed']]){
  const {ctx}=boot(true,stage,status);const html=ctx.viewer();
  assert.match(html,/vision-readonly/);assert.doesNotMatch(html,/data-vision="add"[^>]*>[^<]*(<[^>]*>[^<]*)*Add object</);assert.match(html,/Back to components/);
 }
});
test('the loader installs gates before live controllers and keeps the redesigned viewer last',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
 const scripts=[...html.matchAll(/<script src="([^"?]+)/g)].map(m=>m[1]);
 assert(scripts.indexOf('live/gates.js')<scripts.indexOf('live/live-app.js'));
 assert(scripts.indexOf('live/live-review.js')<scripts.indexOf('vision-workbench.js'));
});

test('decision retries reuse keys only for the same study and exact payload',async()=>{
 const {live,ctx,study,calls}=boot(false,'rooms_review');
 async function reject(id,count){
  live.state.studyId=id;study.id=id;
  live.state.corrections.rooms_review=new Map([['component:c1',M.countCorrection({name:'Kitchen'},{component_id:'c1',label:'light_fixture',count:1},count,null,s=>s)]]);
  live.actions.reject(null,'rooms_review');ctx.lastModal.querySelector('#live-confirm').onclick();await live.lastAction;
 }
 await reject('first',2);await reject('first',2);await reject('second',2);await reject('second',3);
 assert.equal(calls.length,4);assert.equal(calls[0].key,calls[1].key);
 assert.notEqual(calls[1].key,calls[2].key);assert.notEqual(calls[2].key,calls[3].key);
 assert.equal(calls[2].id,'second');assert.equal(calls[3].body.corrections[0].after.count,3);
});

// A hidden button is not an authorization boundary: the action must also reject a closed gate.
test('completed appraisal rejects corrections even when its action is invoked directly',()=>{
 for(const gate of [true,false]){
  const {live,ctx}=boot(gate,'assets_review');
  live.actions['correct-field'](null,'field');
  assert.equal(ctx.lastModal,undefined);
  assert.equal(Object.keys(live.state.corrections).length,0);
 }
});

test('room previews start collapsed, preserve explicit expansion, and isolate study, revision and page',()=>{
 const {ctx,live,study}=boot(true,'components_review');
 let html=live.componentsScreen();assert.match(html,/aria-expanded="false"/);assert.match(html,/room-summary-previews/);assert.match(html,/>7 components</);
 assert.doesNotMatch(html,/Counts are the room’s inventory|The photos show .*inventory does not list/);
 const body={hidden:true},attrs={'aria-controls':'live-room-body-0'};ctx.root.querySelector=()=>body;
 const el={dataset:{roomScope:'study:1:components'},setAttribute:(key,value)=>attrs[key]=value,getAttribute:key=>attrs[key]};
 live.actions['toggle-room'](el,'kitchen');assert.equal(body.hidden,false);assert.equal(attrs['aria-expanded'],'true');
 assert.match(live.componentsScreen(),/aria-expanded="true"/);
 assert.doesNotMatch(ctx.rooms(),/aria-expanded="true"/);
 study.revision=2;assert.doesNotMatch(live.componentsScreen(),/aria-expanded="true"/);
 study.id='other';assert.doesNotMatch(live.componentsScreen(),/aria-expanded="true"/);
 study.id='study';study.revision=1;live.actions['toggle-room'](el,'kitchen');assert.equal(body.hidden,true);assert.equal(attrs['aria-expanded'],'false');
});

test('Components previews summarize only three types while keeping every inventory row behind expansion',()=>{
 const {ctx,live,room}=boot(true,'components_review');
 room.inventory.push({component_id:'c3',label:'window',count:2},{component_id:'c4',label:'door',count:3},{component_id:'c5',label:'sink',count:1});
 const html=live.componentsScreen(),summary=html.split('room-summary-categories')[1].split('</div><div class="room-section-body"')[0];
 assert.equal((summary.match(/class="room-category-stat"/g)||[]).length,3);
 assert.match(summary,/light_fixture/);assert.match(summary,/>7<\/strong>/);assert.match(summary,/\+1 more types/);assert.doesNotMatch(summary,/sink/);
 assert.match(html,/>4 types</);assert.match(html,/>13 components</);assert.match(html,/id="live-room-body-0" hidden/);
 assert.match(html,/<b>sink<\/b>/);assert.doesNotMatch(ctx.rooms(),/room-summary-categories/);
});

test('live appraisal gives the source its own height and removes duplicated field/status headers',()=>{
 const {ctx,live,study}=boot(true,'completed','completed');
 const fields=[{id:'address',key:'subject.property_address',section:'subject',field:'property_address',label:'Property Address',value_text:'Test address',page:1}];
 study.documents=[{id:'pdf',kind:'appraisal',filename:'appraisal.pdf'}];
 ctx.BasisReviewOrganization=require('../review-organization.js');
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../live/review-layout.js'),'utf8'),ctx);
 live.load=()=>({done:true,data:{items:fields}});
 const html=ctx.appraisal();
 assert.match(html,/appraisal-review-layout/);assert.match(html,/aria-label="Appraisal PDF report"/);assert.match(html,/data-doc="pdf"/);
 assert.doesNotMatch(html,/source-focus|audit-banner|Selected field|<strong>Property Address/);
 assert.match(html,/data-live="open-pdf"/);assert.doesNotMatch(html,/data-live="correct-field"/);
});


test('CSV export retains zero quantities and costs when results are regrouped',async()=>{
 const {ctx,live,study}=boot(true,'completed','completed');let exported;
 live.load=()=>({done:true,data:{items:[{label:'countertop',category:'Countertop',bucket:'y5',takeoff:{quantity:0,unit:'SF'},cost:{unit_cost:0,total:0},needs_review:false}]}});
 ctx.Blob=Blob;ctx.URL={createObjectURL:blob=>{exported=blob;return 'blob:test'},revokeObjectURL(){}};ctx.setTimeout=()=>{};
 ctx.document.createElement=()=>({click(){},remove(){}});ctx.document.body={append(){}};
 live.actions['export-csv']();
 const cells=(await exported.text()).split('\n')[1].split(',');
 assert.equal(cells[6],'0');assert.equal(cells[8],'0');assert.equal(cells[9],'0');
});

test('Save room changes sends a revision-safe correction batch through the decision API',async()=>{
 const {live,calls}=boot(true,'rooms_review');
 const change={key:'room_create:draft',entity_type:'room_create',entity_id:'draft',before:null,after:{name:'Office',room_type:'office',floor:null},reason:null,title:'Office',summary:'create room'};
 live.state.corrections.rooms_review=new Map([[change.key,change]]);
 await live.saveRoomChanges();
 assert.equal(calls.length,1);assert.equal(calls[0].body.stage,'rooms_review');assert.equal(calls[0].body.decision,'reject');assert.equal(calls[0].body.revision,1);
 assert.equal(calls[0].body.corrections[0].after.name,'Office');assert.equal(calls[0].body.corrections[0].title,undefined);assert.equal(live.state.corrections.rooms_review.size,0);
});
test('approval cannot bypass the placement guard and failed room saves retain the draft',async()=>{
 const {live,calls}=boot(true,'rooms_review');live.roomPlacementReady=()=>false;
 live.actions.approve(null,'rooms_review');assert.equal(calls.length,0);
 const change={entity_type:'photo_room',entity_id:'p',after:{room_id:'k'}};live.state.corrections.rooms_review=new Map([['move',change]]);
 live.api.decide=async()=>{throw {status:422,errors:[{msg:'Place every photo.'}]}};
 await live.saveRoomChanges();assert.equal(live.state.corrections.rooms_review.get('move'),change);
});

// Floor zero must remain visible alongside the distinct name and type.
test('component room metadata preserves floor zero and labels the disclosure',()=>{
 const {live,room}=boot(true,'components_review');room.floor=0;room.name='Kitchen';room.room_type='kitchen';
 const html=live.componentsScreen();assert.match(html,/Type <strong>kitchen<\/strong>/);assert.match(html,/Floor <strong>0<\/strong>/);
 assert.match(html,/Show components/);assert.match(html,/Hide components/);assert.match(html,/Rooms in this study/);assert.match(html,/Total photos/);
 room.floor=null;assert.doesNotMatch(live.componentsScreen(),/Floor <strong>/);
});
