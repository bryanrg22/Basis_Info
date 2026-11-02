const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const P=require('../review-organization.js');
const asset=(id,overrides={})=>({id,label:'window',category:'Windows',bucket:'y27_5',room:{id:'kitchen',name:'Kitchen'},needs_review:false,cost:{total:'12.50'},...overrides});
const fields=[
 {id:'cost',key:'cost_approach.dwelling_cost',section:'cost_approach',field:'dwelling_cost',label:'Dwelling cost',value_num:'250000',confidence:.9},
 {id:'beds',key:'improvements.bedrooms',section:'improvements',field:'bedrooms',label:'Bedrooms',value_num:3,critical:true,confidence:.6,page:1},
 {id:'address',key:'subject.property_address',section:'subject',field:'property_address',label:'Property Address',value_text:'123 Test Street',critical:true,confidence:.95,page:1},
 {id:'gla',key:'improvements.gla_sqft',section:'improvements',field:'gla_sqft',label:'Gross Living Area',value_num:1500,critical:true,confidence:null,page:5},
 {id:'new',key:'future_section.future_field',section:'future_section',field:'future_field',value_text:'Future data',confidence:null},
 {id:'basement',key:'improvements.basement_area_sqft',section:'improvements',field:'basement_area_sqft',label:'Basement area',value_num:0,confidence:0},
 {id:'contract',key:'listing_and_contract.contract_date',section:'listing_and_contract',field:'contract_date',label:'Contract date',value_date:'2026-01-01',confidence:.8}
];
test('room grouping retains every asset once, including same-named rooms and unassigned rows',()=>{
 const items=[asset('one'),asset('two',{room:{id:'other-kitchen',name:'Kitchen'}}),asset('none',{room:null}),asset('four')];
 const view=P.assetView(items);
 assert.equal(view.groups.length,3);
 assert.deepEqual(view.groups.flatMap(g=>g.items.map(a=>a.id)).sort(),['four','none','one','two']);
 assert.equal(view.groups.find(g=>g.key==='room:kitchen').stats.count,2);
 assert.equal(view.groups.find(g=>g.key==='room:unassigned').name,'Unassigned');
});
test('priced totals distinguish zero from missing or invalid prices and reconcile across rooms',()=>{
 const items=[asset('a'),asset('b',{cost:{total:0},needs_review:true}),asset('c',{cost:{total:null}}),asset('d',{cost:null}),asset('e',{cost:{total:'not a number'}}),asset('f',{cost:{total:''}})];
 const {summary,groups}=P.assetView(items);
 assert.deepEqual(summary,{count:6,review:1,unpriced:4,priced:2,total:12.5});
 assert.equal(groups.reduce((n,g)=>n+g.stats.total,0),summary.total);
 assert.deepEqual(P.assetView(items,{filter:'unpriced'}).groups.flatMap(g=>g.items.map(a=>a.id)),['c','d','e','f']);
});
test('every recovery class is visible even when entirely unpriced or unknown',()=>{
 const items=['y5','y7','y15','y27_5','y39','land','future'].map((bucket,i)=>asset(String(i),{bucket,cost:null}));
 const view=P.assetView(items,{groupBy:'recovery'});
 assert.equal(view.classes.length,7);assert.equal(view.groups.length,7);assert.equal(view.summary.unpriced,7);
 assert.deepEqual(view.classes.map(c=>c.bucket),['y5','y7','y15','y27_5','y39','land','future']);
});
test('search combines room and asset terms; status filters are independent of grouping',()=>{
 const items=[asset('a',{needs_review:true}),asset('b',{label:'door',category:'Door'}),asset('c',{room:{id:'bed',name:'Bedroom'},needs_review:true})];
 assert.deepEqual(P.assetView(items,{query:'WINDOW kitchen',filter:'review'}).groups.flatMap(g=>g.items.map(a=>a.id)),['a']);
 assert.equal(P.assetView(items,{query:'nomatch'}).groups.length,0);
 assert.equal(P.assetView(items,{filter:'ok',groupBy:'recovery'}).matching.count,1);
 assert.equal(P.assetView(items,{filter:'review'}).matching.count+P.assetView(items,{filter:'ok'}).matching.count,3);
});
test('pending classification corrections affect groups and search without mutating API rows',()=>{
 const items=[asset('a')],before=JSON.stringify(items),corrections=new Map([['a',{after:{bucket:'y5',category:'Corrected fixture',irs_section:'1245'}}]]);
 const view=P.assetView(items,{corrections,groupBy:'recovery',query:'corrected 1245'});
 assert.equal(view.groups[0].name,'5-year');assert.equal(view.groups[0].items[0].edited,true);assert.equal(view.summary.total,12.5);
 assert.equal(JSON.stringify(items),before);
});
test('flagged assets appear first within a room without dropping unflagged assets',()=>{
 const items=[asset('z',{label:'appliance'}),asset('a',{label:'window',needs_review:true}),asset('b',{label:'door',needs_review:true})];
 assert.deepEqual(P.assetView(items).groups[0].items.map(a=>a.id),['b','a','z']);
});
test('property overview prioritizes identity and layout instead of API section order',()=>{
 const groups=P.fieldGroups(fields);
 assert.equal(groups[0].key,'overview');assert.deepEqual(groups[0].fields.map(f=>f.id),['address','beds','gla']);
 assert.equal(P.fieldLabel(fields[3]),'Living area (sq ft)');
 assert.equal(P.fieldGroups(fields).find(g=>g.key==='building').fields[0].id,'basement');
});
test('all appraisal fields remain accessible exactly once, including future schema sections',()=>{
 const before=JSON.stringify(fields),groups=P.fieldGroups(fields);
 assert.deepEqual(groups.flatMap(g=>g.fields.map(f=>f.id)).sort(),fields.map(f=>f.id).sort());
 assert.equal(new Set(groups.flatMap(g=>g.fields.map(f=>f.id))).size,fields.length);
 assert.equal(groups.find(g=>g.key==='source:future_section').name,'Future section');
 assert.equal(JSON.stringify(fields),before);
});
test('appraisal filters preserve zero values and distinguish importance from low confidence',()=>{
 assert.deepEqual(P.fieldGroups(fields,{filter:'review'}).flatMap(g=>g.fields.map(f=>f.id)).sort(),['basement','beds']);
 assert.deepEqual(P.fieldGroups(fields,{filter:'critical'}).flatMap(g=>g.fields.map(f=>f.id)).sort(),['address','beds','gla']);
 assert.equal(P.fieldValue(fields[5]),'0');assert.equal(P.needsLook(fields[3]),false);
 assert.equal(P.fieldGroups(fields,{query:'future data'})[0].fields[0].id,'new');
 assert.equal(P.fieldGroups(fields,{query:'basement area'})[0].fields[0].id,'basement');
});
test('field search includes pending corrected values and stays keyed to the original source ID',()=>{
 const corrections=new Map([['beds',{display:'Four bedrooms'}]]);
 assert.equal(P.fieldGroups(fields,{query:'four',corrections})[0].fields[0].id,'beds');
 assert.equal(P.fieldGroups(fields,{query:'no match'}).length,0);
 assert.deepEqual(P.fieldGroups([]),[]);
});
function layout(){
 const handlers={},list={innerHTML:'',scrollTop:200},footer={innerHTML:''},selected=[],root={addEventListener:(name,fn)=>handlers[name]=fn,querySelector:selector=>selector==='.appraisal-organized .ui-field-scroll'?list:selector==='.appraisal-organized .field-review-actions'?footer:null,querySelectorAll:()=>[]};
 let renders=0;const live={actions:{'select-field':(_,id)=>selected.push(id)},labelName:s=>s||'',render:()=>renders++,route:(screen,id,doc)=>`#${screen}/${id}/${doc}`,photo:()=>'<img>'};
 const sandbox={BasisReviewOrganization:P,BasisLive:live,BasisReasons:{title:()=>''},root,esc:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),icon:()=>''};
 vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,'../live/review-layout.js'),'utf8'),sandbox);
 return {view:sandbox.BasisReviewLayout,handlers,list,footer,selected,renders:()=>renders};
}
test('appraisal filtering updates only the left list and leaves the source PDF/controller intact',()=>{
 const {view,handlers,list,renders}=layout();
 view.appraisal({fields,study:{id:'s',revision:1},selected:'address',corrections:new Map(),canCorrect:false});
 handlers.input({target:{id:'appraisal-field-search',value:'basement'}});
 assert.match(list.innerHTML,/data-value="basement"/);assert.doesNotMatch(list.innerHTML,/data-value="address"/);assert.equal(renders(),0);
 handlers.change({target:{id:'appraisal-field-filter',value:'review'}});
 assert.equal(renders(),0);assert.equal(list.scrollTop,0);
});
test('field source IDs, pending values, closed-gate controls and escaping survive the new layout',()=>{
 const {view}=layout(),corrections=new Map([['beds',{display:'<corrected>'}]]);
 const html=view.appraisal({fields,study:{id:'s',revision:1},selected:'beds',corrections,canCorrect:false});
 assert.match(html,/data-live="select-field" data-value="beds" aria-pressed="true"/);
 assert.match(html,/&lt;corrected&gt;/);assert.doesNotMatch(html,/data-live="correct-field"/);
 assert.match(view.appraisal({fields,study:{id:'s',revision:1},selected:'beds',corrections,canCorrect:true}),/data-live="correct-field" data-value="beds"/);
});
test('asset summary starts collapsed and preserves full review routes without new API operations',()=>{
 const {view}=layout();
 const html=view.assets({items:[asset('a')],study:{id:'s',revision:1},rooms:[],corrections:new Map()});
 assert.doesNotMatch(html,/<details[^>]*\sopen/);
 assert.match(html,/href="#asset-detail\/s\/a"/);assert.match(html,/1 asset/);
});
test('disclosure state stays separate by study and ignores detached toggle events',()=>{
 const {view,handlers}=layout(),props={items:[asset('a')],study:{id:'s',revision:1},rooms:[],corrections:new Map()};
 view.assets(props);handlers.toggle({target:{dataset:{reviewGroup:'bucket:y27_5'},open:true,isConnected:true}});
 assert.match(view.assets(props),/data-review-group="bucket:y27_5" open/);
 handlers.toggle({target:{dataset:{reviewGroup:'bucket:y27_5'},open:false,isConnected:false}});
 assert.match(view.assets(props),/data-review-group="bucket:y27_5" open/);
 assert.doesNotMatch(view.assets({...props,study:{id:'other',revision:1}}),/data-review-group="bucket:y27_5" open/);
});

const nextSectionEvent=()=>({target:{closest:selector=>selector==='[data-review-action]'?{dataset:{reviewAction:'next-field-section'}}:null}});
const ownership={id:'owner',key:'subject.owner',section:'subject',field:'owner',label:'Owner',value_text:'Test Owner',page:1,confidence:.9};
test('next section goes to ownership and selects its source field instead of repeating the current field',()=>{
 const {view,handlers,selected}=layout(),props={fields:[...fields,ownership],study:{id:'s',revision:1},selected:'address',corrections:new Map(),canCorrect:false};
 const html=view.appraisal(props),footer=html.split('class="field-review-actions"')[1];
 assert.match(footer,/Next section/);assert.match(footer,/Property &amp; ownership/);assert.doesNotMatch(footer,/Property Address|Appraisal page/);
 handlers.click(nextSectionEvent());assert.deepEqual(selected,['owner']);
 const next=view.appraisal({...props,selected:'owner'});assert.match(next.split('class="field-review-actions"')[1],/Building &amp; layout/);
});
test('section navigation skips absent groups and returns from the final group to the overview',()=>{
 const {view,handlers,selected}=layout(),props={fields,study:{id:'s',revision:1},selected:'address',corrections:new Map(),canCorrect:false};
 assert.match(view.appraisal(props).split('class="field-review-actions"')[1],/Building &amp; layout/);
 handlers.click(nextSectionEvent());assert.deepEqual(selected,['basement']);
 assert.match(view.appraisal({...props,selected:'new'}).split('class="field-review-actions"')[1],/Back to.*Property overview/);
 handlers.click(nextSectionEvent());assert.deepEqual(selected,['basement','address']);
});
test('section navigation follows filtered results and does not navigate when nothing matches',()=>{
 const {view,handlers,selected,footer}=layout();
 view.appraisal({fields:[...fields,ownership],study:{id:'s',revision:1},selected:'address',corrections:new Map(),canCorrect:false});
 handlers.input({target:{id:'appraisal-field-search',value:'future data'}});
 assert.match(footer.innerHTML,/Future section/);handlers.click(nextSectionEvent());assert.deepEqual(selected,['new']);
 handlers.input({target:{id:'appraisal-field-search',value:'no matching field'}});
 assert.equal(footer.innerHTML,'');handlers.click(nextSectionEvent());assert.deepEqual(selected,['new']);
});
test('single-section reviews retain corrections when editable without an empty next-section button',()=>{
 const {view}=layout(),props={fields:[ownership],study:{id:'s',revision:1},selected:'owner',corrections:new Map(),canCorrect:true};
 const footer=view.appraisal(props).split('class="field-review-actions"')[1];
 assert.doesNotMatch(footer,/next-field-section/);assert.match(footer,/data-live="correct-field" data-value="owner"/);
 assert.doesNotMatch(view.appraisal({...props,canCorrect:false}),/data-live="correct-field"/);
});


test('results group every asset once by recovery class and distinguish zero and missing prices',()=>{
 const {view}=layout();
 const html=view.results({items:[asset('one',{label:'Unique five',bucket:'y5',cost:{total:0}}),asset('two',{label:'Unique fifteen',bucket:'y15',cost:null}),asset('three',{label:'Unique unknown',bucket:null,cost:{total:20}})],study:{id:'s',revision:1}});
 assert.equal((html.match(/<details class="result-class"/g)||[]).length,3);
 assert.doesNotMatch(html,/<details[^>]*\sopen/);
 for(const label of ['Unique five','Unique fifteen','Unique unknown'])assert.equal(html.split('<strong>'+label+'</strong>').length-1,1);
 assert.match(html,/Unclassified/);assert.match(html,/Unpriced/);assert.match(html,/\$0\.00/);assert.match(html,/3 recovery classes/);
 assert.match(view.results({items:[],study:{id:'s',revision:1}}),/No assets yet/);
});

test('unlocated XML values need review without flagging an absent XML value or a located zero',()=>{
 assert(P.needsLook({source:'mismo_xml',value_num:'0',confidence:1,regions:[]}));
 assert(!P.needsLook({source:'mismo_xml',value_num:null,confidence:1,regions:[]}));
 assert(!P.needsLook({source:'mismo_xml',value_num:'0',confidence:1,regions:[{page:1,bbox:[0,0,.1,.1]}]}));
});
