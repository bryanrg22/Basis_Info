// The measure tool as rendered (web/live/property.js, EVAL_REPORT 104) on the sample API through
// the real API client, in a VM, driven through the same document listeners a browser calls:
// labels on every edge, a typed length on a label, a drag that would cross refused, snapping,
// undo, redo and delete, the body sent with its provenance, the dialog on a photo with a scale,
// a model's draft kept as the original, and the stored measurement read back on the asset page.
// Layout, focus and pointer capture in a real browser are checked by the headless captures listed
// in web/DESIGN_NOTES.md ("Measure tool").
const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const WEB=path.join(__dirname,'..'),read=f=>fs.readFileSync(path.join(WEB,f),'utf8');
const A=require('../live/sample-api.js'),PM=require('../live/property-model.js'),PS=require('../live/sample-property.js');
const NOW=Date.parse('2026-11-23T20:00:00Z');

async function boot({variant='confirmed',measureDraft=false,screen='property'}={}){
 const sample=A.create({state:'assets',now:()=>NOW,measureDraft,fetchAsset:p=>fs.readFileSync(path.join(WEB,p))});sample._inst.propertyState=variant;
 const api=require('../live/api-client.js').createClient({baseUrl:sample.base,fetchImpl:(u,i)=>sample.fetch(u,i),getToken:async()=>'sample-token'});
 const study=await api.getStudy(sample.mainStudyId());
 const cache=new Map(),added=new Map(),toasts=[],listeners={};let scr=screen;
 const load=(key,fn)=>{if(cache.has(key))return cache.get(key);const e={done:false};cache.set(key,e);e.promise=Promise.resolve().then(fn).then(d=>{e.data=d;e.done=true},err=>{e.error=err;e.done=true});return e};
 const Live={api,caps:PM.caps({property_capture:true,public_geo:true,traced_measurement:true}),state:{cache,studyId:study.id,study,docId:null,corrections:{}},screens:{},actions:{},afterRender:[],beforeRender:[],load,
  invalidate:p=>{for(const k of [...cache.keys()])if(k.startsWith(p))cache.delete(k)},withStudy:fn=>fn(study),me:()=>({memberships:[{org_id:study.org_id,role:'owner'}]}),
  route:(s,id,doc)=>`#${s}${id?'/'+id:''}${doc?'/'+doc:''}`,when:v=>v?String(v).slice(0,16):'—',loading:t=>`<div class="live-state">${t}</div>`,failed:e=>`<div role="alert">${e?.message}</div>`,
  render(){},screen:()=>scr,labelName:l=>String(l||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase()),gateOpen:stage=>study.current_stage===stage&&study.status==='waiting_review',
  guarded:async(n,fn)=>fn(),errorText:e=>e?.message||'',addCorrection:(stage,c)=>added.set(c.key,{stage,c}),pendingCorrection:(stage,key)=>added.get(key)?.c||null,
  photo:(id,alt,cls)=>`<img data-live-doc="${id}" class="${cls}">`,photoUrl:()=>new Promise(()=>{}),studyTools:s=>''};
 const g={BASIS_CONFIG:{mode:'live'},BasisLive:Live,BasisPropertyModel:PM,BasisReasons:require('../live/review-reasons.js'),Intl,URL,Date,Promise,Map,Set,JSON,Math,Number,String,Array,Object,Boolean,RegExp,Error,encodeURIComponent,decodeURIComponent,console,
  setTimeout:()=>0,clearTimeout(){},matchMedia:()=>({matches:false}),icon:()=>'<svg></svg>',heading:(t,d,a)=>`<h1>${t}</h1>${a||''}`,toast:t=>toasts.push(t),
  esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  document:{addEventListener:(n,fn)=>(listeners[n]||=[]).push(fn),querySelector:()=>null,querySelectorAll:()=>[],getElementById:()=>null,hidden:false}};
 g.globalThis=g;vm.createContext(g);vm.runInContext(read('live/property.js'),g);
 async function page(fn=()=>Live.screens.property()){for(let i=0;i<10;i++){fn();const open=[...cache.values()].filter(e=>!e.done);if(!open.length)break;await Promise.all(open.map(e=>e.promise))}return fn()}
 const items=(await api.assets(study.id,study.revision)).items,asset=l=>items.find(a=>a.label===l);
 const fire=(name,ev)=>{for(const fn of listeners[name]||[])fn(ev)};
 return {Live,g,api,sample,study,cache,added,toasts,page,items,asset,fire,listeners,setScreen:s=>{scr=s}};
}
const plain=o=>JSON.parse(JSON.stringify(o));
const text=html=>html.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ');
const labelsOf=html=>[...html.matchAll(/<g class="mt-label[^"]*"[^>]*>.*?<text[^>]*>([^<]*)<\/text><\/g>/g)].map(m=>m[1].replace(/&#39;/g,"'").replace(/&quot;/g,'"'));
// A fake element for the listeners: closest() answers the selectors the tool asks about.
function el(attrs={},{svg=null,form=null}={}){
 const self={dataset:attrs.dataset||{},closest(sel){
  if(/data-mt-vertex/.test(sel)&&attrs.vertex!==undefined)return {dataset:{mtVertex:attrs.vertex}};
  if(/data-mt-mid/.test(sel)&&attrs.mid!==undefined)return {dataset:{mtMid:attrs.mid}};
  if(/data-mt-edge/.test(sel)&&attrs.edge!==undefined)return {dataset:{mtEdge:attrs.edge}};
  if(/svg\[/.test(sel))return svg;if(/form\[/.test(sel))return form;return null}};
 return self;
}
function stageSvg(scope,v){return {dataset:{mtScope:scope,paStage:v.id},getBoundingClientRect:()=>({left:0,top:0,width:v.width,height:v.height}),viewBox:{baseVal:{width:v.width,height:v.height}},setPointerCapture(){},releasePointerCapture(){},closest(){return this}}}
const at=(svg,p,extra={})=>({target:el(extra,{svg}),clientX:p[0],clientY:p[1],pointerId:1,preventDefault(){},stopPropagation(){}});
const fencePx=v=>PS.SITE.fence.map(([e,n])=>[v.width/2+e/v.pixel_size_m,v.height/2-n/v.pixel_size_m]);

async function measuringFence(){
 const x=await boot(),f=x.asset('fence');x.Live.state.docId=f.id;await x.page();
 const u=x.Live.state.property[x.study.id],v=(await x.api.request('GET',`/v1/studies/${x.study.id}/property`)).capture.views.find(h=>h.id===u.trace.viewId);
 return {...x,f,u,t:u.trace,v,svg:stageSvg('property',v)};
}

test('a traced line shows a length on every edge that sums to the total the server computes',async()=>{
 const x=await measuringFence();
 for(const p of fencePx(x.v))x.fire('click',at(x.svg,p));
 x.fire('click',at(x.svg,fencePx(x.v)[3]));// the last point again finishes the line
 assert.equal(x.t.shapes.length,1);
 const html=x.Live.screens.property(),labels=labelsOf(html);
 assert.deepEqual(labels,[`52'-6"`,`105'-0"`,`52'-6"`]);
 assert.match(text(html),/209\.97 LF/);assert.match(html,/class="mt-total is-line"[^>]*><text[^>]*>210 LF/);
 assert.match(html,/data-mt-edge="0:o:0" tabindex="0" role="button" aria-label="Edge 1: 52 feet 6 inches\. Press Enter to type its length\."/);
 assert.match(html,/data-mt-vertex="0:o:2"/);assert.match(html,/data-mt-mid="0:o:1"/);
 assert.match(text(html),/Edges 3/);
});
test('clicking a label and typing a length moves the far corner along the edge; a bad entry is refused in words',async()=>{
 const x=await measuringFence();x.t.shapes=[{kind:'line',path:fencePx(x.v)}];x.Live.screens.property();
 x.fire('click',at(x.svg,[0,0],{edge:'0:o:0'}));assert.deepEqual([x.t.edit.s,x.t.edit.r,x.t.edit.i],[0,'o',0]);
 let html=x.Live.screens.property();assert.match(html,/<form class="mt-edit" data-mt-form="edge" data-mt-scope="property"/);assert.match(html,/value="52&#39;-6&quot;"/);
 const form=value=>({dataset:{mtScope:'property'},elements:{namedItem:()=>({value})},closest(){return this}});
 const submit=value=>{const f=form(value);x.fire('submit',{target:{closest:()=>f},preventDefault(){},stopPropagation(){}})};
 submit('five feet');assert.match(x.t.edit.error,/is not a length/);
 submit(`54'-3"`);assert.equal(x.t.edit,null);
 const frame=PM.frameOf(x.v),lengths=PM.edgeLengths(x.t.shapes[0].path,false,frame);
 assert.ok(Math.abs(lengths[0]-54.25)<0.001,String(lengths[0]));
 assert.deepEqual(labelsOf(x.Live.screens.property()).slice(0,1),[`54'-3"`]);
 assert.equal(x.t.hist.past.length,1,'one undo step');
 x.Live.actions['pa-trace-undo']();assert.ok(Math.abs(PM.edgeLengths(x.t.shapes[0].path,false,frame)[0]-52.49)<0.05);
 x.Live.actions['mt-redo']();assert.ok(Math.abs(PM.edgeLengths(x.t.shapes[0].path,false,frame)[0]-54.25)<0.001);
});
test('dragging a corner moves it and snaps; a drag that makes the ring cross is undone with the server’s words',async()=>{
 const x=await measuringFence(),v=x.v;x.t.unit='SF';x.t.tool='area';
 const ring=[[400,200],[700,200],[700,400],[400,400]];x.t.shapes=[{kind:'area',ring}];x.Live.screens.property();
 // drag corner 2 near the parcel's... first a free move
 x.fire('pointerdown',at(x.svg,[700,400],{vertex:'0:o:2'}));x.fire('pointermove',at(x.svg,[760,430]));x.fire('pointerup',at(x.svg,[760,430]));
 assert.deepEqual(plain(x.t.shapes[0].ring[2]),[760,430]);assert.equal(x.t.hist.past.length,1);
 // a corner dropped 3 px from another shape corner snaps onto it
 x.t.shapes.push({kind:'area',ring:[[900,500],[1000,500],[1000,600]]});x.t.k=1;
 x.fire('pointerdown',at(x.svg,[760,430],{vertex:'0:o:2'}));x.fire('pointermove',at(x.svg,[903,498]));x.fire('pointerup',at(x.svg,[903,498]));
 assert.deepEqual(plain(x.t.shapes[0].ring[2]),[900,500],'snapped to the other shape’s corner');
 x.t.shapes.pop();x.t.shapes[0].ring=ring.map(p=>p.slice());
 // a bow tie: corner 1 dragged past the closing edge
 x.fire('pointerdown',at(x.svg,[700,200],{vertex:'0:o:1'}));x.fire('pointermove',at(x.svg,[300,300]));x.fire('pointerup',at(x.svg,[300,300]));
 assert.deepEqual(plain(x.t.shapes[0].ring),ring,'the crossing drag is undone');
 assert.match(x.t.msg,/^The shape crosses or touches itself \(two of its edges meet\)\. The change was undone\./);
 // with Snap off a corner lands where it is dropped
 x.t.snap=false;x.fire('pointerdown',at(x.svg,[400,400],{vertex:'0:o:3'}));x.fire('pointermove',at(x.svg,[402.5,401]));x.fire('pointerup',at(x.svg,[402.5,401]));
 assert.deepEqual(plain(x.t.shapes[0].ring[3]),[402.5,401]);
});
test('a corner is added by dragging an edge’s middle, deleted with Delete, and never below three',async()=>{
 const x=await measuringFence();x.t.unit='SF';x.t.tool='area';x.t.shapes=[{kind:'area',ring:[[400,200],[700,200],[700,400],[400,400]]}];x.Live.screens.property();
 x.fire('pointerdown',at(x.svg,[550,200],{mid:'0:o:0'}));x.fire('pointermove',at(x.svg,[550,150]));x.fire('pointerup',at(x.svg,[550,150]));
 assert.deepEqual(plain(x.t.shapes[0].ring),[[400,200],[550,150],[700,200],[700,400],[400,400]]);
 const key=k=>x.fire('keydown',{key:k,target:el(),preventDefault(){},stopPropagation(){}});
 assert.deepEqual(plain(x.t.sel),{s:0,r:'o',i:1});key('Delete');assert.equal(x.t.shapes[0].ring.length,4);
 x.t.sel={s:0,r:'o',i:0};key('ArrowRight');assert.deepEqual(plain(x.t.shapes[0].ring[0]),[401,200],'one screen pixel');
 key('Backspace');key('Escape');x.t.sel={s:0,r:'o',i:0};key('Delete');
 assert.equal(x.t.shapes[0].ring.length,3);assert.match(x.t.msg,/at least 3 corners/);
 key('z');assert.equal(x.t.shapes[0].ring.length,3,'z alone is not undo');
 x.fire('keydown',{key:'z',metaKey:true,target:el(),preventDefault(){}});assert.equal(x.t.shapes[0].ring.length,4);
});
test('the measurement sent carries who drew it, that it was verified, and the picture',async()=>{
 const x=await measuringFence();x.t.shapes=[{kind:'line',path:fencePx(x.v)}];x.Live.screens.property();
 x.Live.actions['pa-trace-save']();
 const c=x.added.get(x.f.id).c,m=c.after.measurement;
 assert.equal(m.drawn_by,'engineer');assert.equal(m.verified,true,'an engineer’s own trace starts verified');
 assert.deepEqual(plain(m.view),{kind:'capture',id:x.v.id,label:x.v.label});assert.ok(!('original' in m));
 assert.deepEqual(plain(PM.problems(m)),[]);assert.equal(PM.measure(m).quantity,'209.97');
 assert.match(c.summary,/traced 209\.97 LF on .*, verified/);
 // the sample (the server's mirror) takes it as a decision
 const r=await x.api.decide(x.study.id,{stage:'assets_review',revision:x.study.revision,decision:'reject',comment:null,corrections:[{entity_type:'asset',entity_id:x.f.id,before:c.before,after:c.after,reason:null}]},'k');
 assert.equal(r.corrections_recorded,1);
});
test('a model’s draft opens as it is, sent unchanged only marked verified, and an edit keeps it as the original',async()=>{
 const x=await boot({measureDraft:true,screen:'assets'}),p=x.asset('patio');
 assert.equal(p.takeoff.inputs.measurement.drawn_by,'model:sample-drafter@1');assert.equal(p.takeoff.quantity,PM.measure(PS.patioDraft()).quantity);
 x.Live.openMeasure(p,'assets');await x.page(()=>x.Live.measureDialog());
 let html=x.Live.measureDialog(),t=x.Live.state.mtDialog.trace;
 assert.match(text(html),/Measure Patio/);assert.match(text(html),/Drafted by Basis · not verified · check it, change what is wrong, then mark it verified/);
 assert.equal(t.fromBase,true);assert.equal(t.verified,false,'a draft is not verified until the engineer says so');
 assert.equal(labelsOf(html).length,4);assert.doesNotMatch(html,/mt-original/,'no dashed original while it is the draft itself');
 // marked verified, unchanged: the draft itself, verified
 t.verified=true;x.Live.actions['pa-trace-save']({closest:()=>({dataset:{mtScope:'dialog'}}),dataset:{mtScope:'dialog'}});
 let m=x.added.get(p.id).c.after.measurement;assert.equal(m.drawn_by,'model:sample-drafter@1');assert.equal(m.verified,true);assert.ok(!m.original);
 assert.deepEqual(plain(m.shapes),PS.patioDraft().shapes);
 // edited: the engineer's, with the draft as its original
 x.Live.state.mtDialog=null;x.Live.openMeasure(p,'assets');x.added.clear();x.Live.measureDialog();t=x.Live.state.mtDialog.trace;
 const frame=PM.frameOf(x.Live.state.mtDialog&&t&&(await x.api.request('GET',`/v1/studies/${x.study.id}/property`)).capture.views.find(v=>v.id===t.viewId));
 t.shapes[0].ring=PM.setEdgeLength(t.shapes[0].ring,0,40,true,frame);t.fromBase=false;
 html=x.Live.measureDialog();assert.match(html,/class="mt-original"/);assert.match(html,/Basis’s draft<\/th><th scope="col">Change/);assert.match(text(html),/Drafted by Basis: 845\.40 SF · now/);
 x.Live.actions['pa-trace-save']({closest:()=>({dataset:{mtScope:'dialog'}}),dataset:{mtScope:'dialog'}});
 m=x.added.get(p.id).c.after.measurement;assert.equal(m.drawn_by,'engineer');
 assert.deepEqual(plain(m.original),PS.patioDraft(),'the draft, unchanged');assert.deepEqual(plain(PM.problems(m)),[]);
 assert.match(PM.formula(PM.measure(m),{source:m.image.source,captured_on:m.image.captured_on,drawn_by:m.drawn_by,verified:m.verified}),/; replaces 845\.40 SF drafted by Basis$/);
});
test('an inside item is measured on its photo: a scale from two points and a known length, then the shape',async()=>{
 const x=await boot({screen:'asset-detail'}),ct=x.asset('countertop');
 x.Live.measurePhotoDims.set('5a310001-0000-4000-8000-000000000002',[1200,800]);
 x.Live.openMeasure(ct,'asset-detail');let html=await x.page(()=>x.Live.measureDialog());
 assert.match(text(html),/Photo · kitchen\.webp/);assert.match(text(html),/Door, height 80 in/);assert.match(text(html),/Base cabinet, height 34\.5 in/);assert.match(text(html),/Counter, height 36 in/);
 const t=x.Live.state.mtDialog.trace,v={id:'doc:5a310001-0000-4000-8000-000000000002',width:1200,height:800},svg=stageSvg('dialog',v);
 assert.equal(t.space,'image');assert.equal(t.tool,'line');
 x.fire('click',at(svg,[100,100]));assert.match(t.msg,/Set the scale first/);assert.equal(t.draft.length,0);
 x.Live.actions['pa-tool']({closest:()=>({dataset:{mtScope:'dialog'}})},'scale');
 x.fire('click',at(svg,[200,500]));x.fire('click',at(svg,[500,500]));
 x.Live.actions['mt-known']({closest:()=>({dataset:{mtScope:'dialog'}})},'36 in');
 assert.equal(t.scale.length,3);assert.equal(t.tool,'line','the scale set, the line tool comes back');
 x.fire('click',at(svg,[100,300]));x.fire('click',at(svg,[1000,300]));x.fire('click',at(svg,[1000,300]));
 html=x.Live.measureDialog();assert.deepEqual(labelsOf(html),[`9'-0"`,`Scale 3'-0"`]);assert.match(text(html),/9\.00 LF/);
 x.Live.actions['pa-trace-save']({closest:()=>({dataset:{mtScope:'dialog'}}),dataset:{mtScope:'dialog'}});
 const m=x.added.get(ct.id).c.after.measurement;
 assert.equal(m.space,'image');assert.deepEqual(plain(m.scale),{points:[[200,500],[500,500]],length:3,unit:'ft'});assert.equal(m.image.document_id,'5a310001-0000-4000-8000-000000000002');
 assert.deepEqual(plain(m.view),{kind:'document',id:'5a310001-0000-4000-8000-000000000002',label:'kitchen.webp',width:1200,height:800});
 assert.equal(PM.measure(m).quantity,'9.00');assert.match(text(x.Live.measureDialog()),/Countertop measured 9\.00 LF/);
});
test('after the rerun the asset page shows the stored measurement with its labels, read-only',async()=>{
 const x=await boot({measureDraft:true,screen:'asset-detail'}),p=x.asset('patio');
 await x.page(()=>x.Live.measurementCard(x.study,p));const html=x.Live.measurementCard(x.study,p);
 assert.match(text(html),/Measured: 845\.40 SF/);assert.match(text(html),/Drawn Drafted by Basis/);assert.match(text(html),/Verified Not yet/);
 assert.match(text(html),/on the picture it was drawn on/);assert.equal(labelsOf(html).length,4);
 assert.doesNotMatch(html,/data-mt-vertex|data-mt-edge/,'read-only: no handles, no typing');
 assert.equal(x.Live.measurementCard(x.study,x.asset('fence')),'','nothing stored, nothing shown');
});
test('the Measure button: offered for any area or length the gate can still change, never for a count',async()=>{
 const x=await boot({screen:'assets'});
 assert.match(x.Live.measureButton(x.study,x.asset('countertop')),/data-live="mt-open"[^>]*>Measure</);
 assert.match(x.Live.measureButton(x.study,x.asset('fence'),{primary:true}),/button small primary/);
 assert.equal(x.Live.measureButton(x.study,x.asset('sink')),'');
 const done=await boot({screen:'assets'});done.study.status='completed';done.study.current_stage='completed';
 assert.equal(done.Live.measureButton(done.study,done.asset('countertop')),'','a closed review');
});
