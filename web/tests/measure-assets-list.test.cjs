// The Assets list's Measure button (web/live/new-design.js, EVAL_REPORT 104): every row with an
// area or a length gets it (the engineer can measure and change any such quantity, not only one
// the takeoff could not size), primary only on a row held for a measured quantity; a count never
// gets it; a closed review shows none. Rendered from the sample API's assets in a VM with the real
// measure tool (live/property.js) loaded beside it.
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const WEB=path.join(__dirname,'..'),A=require('../live/sample-api.js'),PM=require('../live/property-model.js');
const NOW=Date.parse('2026-11-23T20:00:00Z');

async function boot({state='assets'}={}){
 const sample=A.create({state,now:()=>NOW,fetchAsset:p=>fs.readFileSync(path.join(WEB,p))});
 const api=require('../live/api-client.js').createClient({baseUrl:sample.base,fetchImpl:(u,i)=>sample.fetch(u,i),getToken:async()=>'t'});
 const study=await api.getStudy(sample.mainStudyId()),assets=await api.assets(study.id,study.revision);
 const cache=new Map([[`assets:${study.id}:${study.revision}`,{done:true,data:assets}]]);
 const Live={api,caps:PM.caps({property_capture:true,public_geo:true,traced_measurement:true}),state:{cache,studyId:study.id,study,corrections:{}},screens:{},actions:{},afterRender:[],beforeRender:[],
  load:(k,fn)=>cache.get(k)||(cache.set(k,{done:false,promise:new Promise(()=>{})}),cache.get(k)),invalidate(){},withStudy:fn=>fn(study),steps:()=>'',me:()=>({memberships:[]}),route:(s,id,doc)=>`#${s}/${id}${doc?'/'+doc:''}`,
  render(){},screen:()=>'assets',labelName:l=>String(l||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase()),gateOpen:st=>study.current_stage===st&&study.status==='waiting_review',loading:()=>'',failed:()=>'',
  pendingCorrection:()=>null,decisionBar:()=>'',photo:()=>'',ensureAssetsDraft(){}};
 const g={BASIS_CONFIG:{mode:'live'},BasisLive:Live,BasisPropertyModel:PM,BasisReviewOrganization:require('../review-organization.js'),BasisResultView:require('../live/result-view.js'),BasisReport:require('../live/report.js'),BasisGates:require('../live/gates.js'),
  Intl,URL,Date,Promise,Map,Set,JSON,Math,Number,String,Array,Object,Boolean,RegExp,Error,encodeURIComponent,decodeURIComponent,console,setTimeout:()=>0,clearTimeout(){},matchMedia:()=>({matches:false}),
  icon:()=>'',heading:t=>t,toast(){},esc:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  root:{addEventListener(){},querySelector:()=>null},state:{screen:'assets'},document:{addEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],getElementById:()=>null},assets(){},assetDetail(){}};
 g.globalThis=g;vm.createContext(g);
 for(const f of ['live/new-design.js','live/property.js'])vm.runInContext(fs.readFileSync(path.join(WEB,f),'utf8'),g);
 return {g,study,items:assets.items};
}
const rowOf=(html,label)=>html.split('<tr').find(r=>new RegExp(`>${label}</a>`).test(r))||'';

test('every row with an area or a length offers Measure; a count does not; primary only where a quantity is needed',async()=>{
 const x=await boot(),html=x.g.assets();
 const measurable=x.items.filter(a=>PM.measureOffer(a).any);
 assert.ok(measurable.length>=8,'the sample has floors, a countertop, a wall finish, the patio, the fence');
 assert.equal((html.match(/data-live="mt-open"/g)||[]).length,measurable.length);
 for(const label of ['Countertop','Hardwood floor','Wall finish','Patio','Landscaping','Porch'])assert.match(rowOf(html,label),/<button class="button small " data-live="mt-open"[^>]*>Measure</,label);
 assert.match(rowOf(html,'Fence'),/<button class="button small primary" data-live="mt-open"[^>]*>Measure</,'the fence has no measured length: primary');
 assert.doesNotMatch(rowOf(html,'Sink'),/mt-open/);assert.doesNotMatch(rowOf(html,'Chair'),/mt-open/);
 assert.match(html,/nd-schedule has-measure/);
});
test('a closed assets review shows no Measure button',async()=>{
 const x=await boot({state:'unsigned'});assert.doesNotMatch(x.g.assets(),/data-live="mt-open"/);
});
