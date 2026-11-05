/* Live mode on a sample API: the live UI with no backend, on synthetic data.
 *
 *   index.html?mode=live&api=sample[&sample=<state>]
 *
 * Active only with ?api=sample (otherwise BasisSampleApi is {active: false} and nothing else
 * happens). It answers in the page: BasisSampleApi.fetch(url, init) returns a Response for
 * every route the live screens use, shaped exactly as docs/api/openapi.json says, and
 * BasisAuth is replaced by a stub signed in as a synthetic owner (Alex Morgan).
 *
 * The data is synthetic: Harbor House, the illustrative appraisal
 * (web/output/pdf/harbor-house-sample.pdf), the photos in web/assets/*.webp and the report
 * web/assets/sample-report.pdf. No value comes from a real study.
 *
 * States (&sample=): appraisal | rooms | components (default) | assets | processing |
 * unsigned | signed | update-required. Two more studies sit on the dashboard (one signed,
 * one at the appraisal review). Decisions, reopen, objects, the report answers, the firm
 * profile and signing all work and change what the next GET answers; a decision reruns for
 * about two seconds (status processing), then the next gate opens.
 *
 * Three things the real API does not have yet are implemented only in the PROPOSED section
 * near the end (web/FRONTEND_BACKLOG.md, .ui-climb/override-contract.md); deleting that
 * section removes them and leaves the API as it is.
 *
 * Node: require('./sample-api.js').create({state, now, fetchAsset}) makes an instance.
 */
(function(scope){
 'use strict';
 const BASE='https://sample.basis.invalid',PROBLEM='https://basis.invalid/problems/';
 const STATES=['appraisal','rooms','components','assets','processing','unsigned','signed','update-required'];
 const ORDER=['uploading','extracting','appraisal_review','rooms_review','components_processing','components_review','assets_processing','assets_review','exporting','completed'];
 const GATES=['appraisal_review','rooms_review','components_review','assets_review'];
 const LABELS=["carpet","hardwood_floor","vinyl_flooring","laminate_flooring","tile","wall_finish","ceiling_finish","baseboard","crown_molding","wainscoting","concrete_floor","half_wall","interior_door","exterior_door","window","blinds","window_treatment","stairs","railing","fireplace","closet_shelving","built_in_shelving","mirror","kitchen_cabinet","countertop","sink","appliance","refrigerator","dishwasher","stove","oven","microwave","range_hood","garbage_disposal","washer","dryer","bathroom_vanity","toilet","bathtub","shower","bathroom_accessory","light_fixture","ceiling_fan","outdoor_lighting","electrical_panel","electrical_outlet","hvac_unit","ac_condenser","hvac_vent","thermostat","water_heater","smoke_detector","sprinkler_system","security_system","doorbell","window_guard","furniture","desk","chair","filing_cabinet","garage_door","roof","siding","gutter","deck","patio","porch","pool","fence","retaining_wall","driveway","sidewalk","parking_lot","landscaping","signage","shed","concrete_pad","other"];
 const MATERIALS=['wood','metal','steel','aluminum','vinyl','laminate','ceramic','porcelain','stone','granite','quartz','concrete','asphalt','brick','glass','fabric','plastic','drywall','fiberglass','composite','wallpaper','painted_masonry','other'];
 const ATTACHMENTS=['permanent','removable','freestanding'];
 const ROOM_TYPES=['kitchen','bathroom','bedroom','living_room','dining_room','family_room','laundry','hallway','stairway','closet','basement','attic','garage','office','lobby','storage','parking','exterior','mechanical','other','ambiguous'];
 const BUCKET_YEARS={y5:'5',y7:'7',y15:'15',y27_5:'27.5',y39:'39',land:'0'};
 const MAX_COUNT=10000;
 // The decision route's correction types per gate (backEnd/basis/workflow/contracts.py).
 const CORRECTION_TYPES={appraisal_review:['extraction_field','extraction_field_add'],rooms_review:['room','photo_room'],components_review:['component','component_remove','room_component','photo'],assets_review:['asset','reprice','structure_model']};
 // Fields an `after` may carry, per type (PROPOSED adds unit_cost and unit_cost_source).
 const AFTER_KEYS={component:['label','count','material','attachment'],asset:['category','bucket','recovery_period','irs_section','quantity','quantity_unit','quantity_source'],room:['name','room_type','floor']};
 // Error codes (backEnd/basis/errors/codes.py).
 const E={NOT_FOUND:'BASIS-API-011',VALIDATION:'BASIS-API-013',STALE:'BASIS-STUDY-004',GATE:'BASIS-STUDY-005',NO_WORKFLOW:'BASIS-STUDY-009',NO_REVISION:'BASIS-STUDY-010',C_TYPE:'BASIS-STUDY-013',C_TARGET:'BASIS-STUDY-014',C_VALUE:'BASIS-STUDY-015',C_APPROVE:'BASIS-STUDY-016',STUDY_NOT_FOUND:'BASIS-STUDY-017',C_REASON:'BASIS-STUDY-020',C_DUP:'BASIS-STUDY-021',C_LAST:'BASIS-STUDY-023',C_EXISTS:'BASIS-STUDY-024',NOT_COMPLETED:'BASIS-STUDY-030',OBJ_PENDING:'BASIS-OBJECT-001',OBJ_POINT:'BASIS-OBJECT-002',OBJ_NOT_MANUAL:'BASIS-OBJECT-003',OBJ_NOT_FOUND:'BASIS-OBJECT-004',OBJ_PHOTO:'BASIS-OBJECT-005',OBJ_GATE:'BASIS-OBJECT-008',REPORT_NOT_FOUND:'BASIS-REPORT-006',NOT_SIGNABLE:'BASIS-REPORT-007',INPUTS_MISSING:'BASIS-REPORT-008',SIGNED:'BASIS-REPORT-009',CHANGED:'BASIS-REPORT-010',PROFILE_412:'BASIS-REPORT-011',ANSWERS_412:'BASIS-REPORT-012'};
 const VALIDATION_CODE={correction_type:E.C_TYPE,correction_target:E.C_TARGET,correction_value:E.C_VALUE,approval_with_corrections:E.C_APPROVE,correction_reason:E.C_REASON,correction_duplicate:E.C_DUP,correction_last_inventory:E.C_LAST,correction_field_exists:E.C_EXISTS};

 /* ---------- small helpers ---------- */
 const clone=v=>v===undefined?undefined:JSON.parse(JSON.stringify(v));
 const toCents=s=>Math.round(Number(s)*100);
 const money=c=>{const n=Math.round(c),neg=n<0,a=Math.abs(n);return (neg?'-':'')+Math.floor(a/100)+'.'+String(a%100).padStart(2,'0')};
 const blank=s=>typeof s!=='string'||!s.trim();
 const iso=ms=>new Date(ms).toISOString();
 // A deterministic 64-hex digest (FNV-1a, widened): ETags and signature hashes, not security.
 function digest(text){let out='';for(let round=0;round<8;round++){let h=0x811c9dc5^round;for(let i=0;i<text.length;i++){h^=text.charCodeAt(i);h=Math.imul(h,0x01000193)>>>0}out+=h.toString(16).padStart(8,'0')}return out}
 // Uniform largest-remainder rounding: integer parts that sum to `total`.
 function apportion(weights,total){
  const sum=weights.reduce((a,b)=>a+b,0);if(!weights.length)return [];if(!sum)return weights.map((w,i)=>i===0?total:0);
  const raw=weights.map(w=>w*total/sum),floor=raw.map(Math.floor);let left=total-floor.reduce((a,b)=>a+b,0);
  const order=raw.map((r,i)=>[r-Math.floor(r),i]).sort((a,b)=>b[0]-a[0]||a[1]-b[1]);
  for(const [,i] of order){if(left<=0)break;floor[i]++;left--}
  return floor;
 }
 const iou=(a,b)=>{const ix=Math.max(0,Math.min(a.x2,b.x2)-Math.max(a.x1,b.x1)),iy=Math.max(0,Math.min(a.y2,b.y2)-Math.max(a.y1,b.y1)),inter=ix*iy,area=r=>(r.x2-r.x1)*(r.y2-r.y1),union=area(a)+area(b)-inter;return {iou:union>0?inter/union:0,smaller:Math.min(area(a),area(b))>0?inter/Math.min(area(a),area(b)):0}};
 const round4=n=>Math.round(n*10000)/10000;

 /* ---------- the synthetic fixture ---------- */
 const FIRM={name:'Alder & Pine Engineering (sample)'};
 const PEOPLE=[
  {id:'sample-user-1',email:'alex.morgan@example.test',name:'Alex Morgan',role:'owner'},
  {id:'sample-user-2',email:'jordan.lee@example.test',name:'Jordan Lee',role:'engineer'},
  {id:'sample-user-3',email:'sam.rivera@example.test',name:'Sam Rivera',role:'viewer'},
 ];
 const ME=PEOPLE[0],ORG_ID='5a3f0000-0000-4000-8000-000000000001';
 const PROFILE={name:FIRM.name,address:'400 Example Way, Suite 2, Ventura, CA 93001',phone:'(555) 010-0144',logo_png:null,engineer:{name:'Alex Morgan',credentials:'PE',license_number:'SAMPLE-00000',license_state:'CA',email:'alex.morgan@example.test'},limiting_conditions:null};
 const ANSWERS={taxpayer_name:'Harbor House Holdings LLC (sample)',photographs:'engineer_site_visit',photographed_by:'Alex Morgan',photographed_on:'2026-10-20',interviews:[],prior_depreciation:null};
 // Documents: the illustrative appraisal and five photos (sizes and digests of the files).
 const DOCS=[
  {key:'appraisal',kind:'appraisal',filename:'Harbor_House_Sample_Appraisal.pdf',content_type:'application/pdf',bytes:11186,sha256:'a028ddc10c152258a008336b2b98c6827538525c27de881b696eff0bc403fa64',path:'output/pdf/harbor-house-sample.pdf'},
  {key:'kitchen',kind:'photo',filename:'kitchen.webp',content_type:'image/webp',bytes:106092,sha256:'78e2ba3e8cc427a96ed4c98d4cb8a55733f0c7b3f6a5de84248b00fa09917cc5',path:'assets/kitchen.webp'},
  {key:'kitchen-detail',kind:'photo',filename:'kitchen-detail.webp',content_type:'image/webp',bytes:97906,sha256:'26be32dcacd4e3d808babe18f2312b110362a89e419ac677be59f19ed4946f8c',path:'assets/kitchen-detail.webp'},
  {key:'living',kind:'photo',filename:'living.webp',content_type:'image/webp',bytes:100298,sha256:'26efd19dfb8a9b8f1d462b01474d45d7390a5d6ffddb4e3f8270dc542e0c4dfd',path:'assets/living.webp'},
  {key:'bedroom',kind:'photo',filename:'bedroom.webp',content_type:'image/webp',bytes:124510,sha256:'19d8ccbed96953ff1e0334c123fb104a6bad728adbb17ba400f7f176333a576e',path:'assets/bedroom.webp'},
  {key:'exterior',kind:'photo',filename:'exterior.webp',content_type:'image/webp',bytes:167240,sha256:'1b042095d3e3e5f0c77bf48b36e97dd85670d60b10fd125ad8093f16a059e396',path:'assets/exterior.webp'},
 ];
 const REPORT_PATH='assets/sample-report.pdf';
 // Appraisal fields: [key, label, kind, value, page(s), confidence, critical]; regions are the
 // printed value's box on the sample PDF (page fractions, top-left origin; pdftotext -bbox).
 const ROW_Y=[0.3057,0.3676,0.4294,0.4913,0.5532,0.6150];
 const box=(row,x1)=>[0.3791,ROW_Y[row],x1,ROW_Y[row]+0.0128];
 const FIELDS=[
  ['subject.property_name','Property name','text','Harbor House',[[1,box(0,0.4979)],[8,box(0,0.4979)]],0.98,false],
  ['subject.property_address','Property address','text','1200 Harbor Boulevard',[[1,box(1,0.5759)],[7,box(0,0.5759)]],0.97,true],
  ['subject.city','City','text','Ventura',[[1,[0.3791,ROW_Y[2],0.4380,ROW_Y[2]+0.0128]]],0.95,false],
  ['subject.state','State','text','CA',[[1,[0.4440,ROW_Y[2],0.5389,ROW_Y[2]+0.0128]]],0.93,true],
  ['subject.property_type','Property type','text','Single-family residential',[[1,box(3,0.5859)]],0.91,false],
  ['subject.client','Client','text','Harbor Property Partners',[[1,box(4,0.5948)]],0.88,false],
  ['reconciliation.effective_date','Effective date','date','2026-09-30',[[1,box(5,0.5469)],[8,box(3,0.5469)]],0.96,true],
  ['subject.use','Use','text','Residential',[[2,box(0,0.4750)]],0.9,false],
  ['subject.occupancy','Occupancy','text','Owner occupied',[[2,box(1,0.5189)]],0.62,false],
  ['improvements.stories','Stories','num','2',[[2,box(2,0.4150)]],0.66,false],
  ['improvements.design_style','Architectural style','text','Contemporary',[[2,box(3,0.5019)]],0.89,false],
  ['neighborhood.description','Neighborhood','text','Illustrative residential neighborhood',[[2,box(4,0.6897)]],0.55,false],
  ['improvements.year_built','Year built','num','1998',[[3,box(0,0.4191)]],0.97,true],
  ['improvements.gla_sqft','Gross living area','num','3240',[[3,box(1,0.4670)]],0.95,true],
  ['improvements.bedrooms','Bedrooms','num','4',[[3,box(2,0.3891)]],0.94,false],
  ['improvements.bathrooms','Bathrooms','num','3',[[3,box(3,0.3891)]],0.93,false],
  ['improvements.construction','Construction','text','Wood frame',[[3,box(4,0.4829)]],0.9,false],
  ['improvements.condition','Condition','text','Illustrative average condition',[[3,box(5,0.6278)]],0.58,false],
  ['improvements.living_spaces','Living spaces','text','Living room, kitchen and dining area',[[4,box(0,0.6927)]],0.84,false],
  ['improvements.kitchen','Kitchen','text','Built-in cabinetry and work surfaces',[[4,box(1,0.6887)]],0.82,false],
  ['improvements.flooring','Flooring','text','Mixed carpet and hard surfaces',[[4,box(2,0.6488)]],0.64,false],
  ['improvements.lighting','Lighting','text','Recessed and decorative fixtures',[[4,box(3,0.6648)]],0.8,false],
  ['site.site_value','Site value','num','280000',[[7,box(1,0.4540)],[8,box(2,0.4540)]],0.96,true],
  ['site.value_type','Value type','text','Illustrative land value',[[7,box(2,0.5619)]],0.86,false],
  ['reconciliation.final_market_value','Appraised value','num','1250000',[[8,box(1,0.4690)]],0.97,true],
 ];
 // Rooms as the model grouped them. The living room is first named "Family room": the seeded
 // studies past the rooms review carry the engineer's rename.
 const ROOMS=[
  {key:'kitchen',name:'Kitchen',room_type:'kitchen',floor:'1',area:340,photos:['kitchen','kitchen-detail']},
  {key:'living',name:'Family room',room_type:'family_room',floor:'1',area:460,photos:['living']},
  {key:'bedroom',name:'Primary bedroom',room_type:'bedroom',floor:'2',area:280,photos:['bedroom']},
  {key:'exterior',name:'Exterior & pool',room_type:'exterior',floor:null,area:null,photos:['exterior']},
 ];
 // Room inventories: [label, count, material, attachment, confidence, seen in].
 const INVENTORY={
  kitchen:[['kitchen_cabinet',4,'wood','permanent',0.93,['kitchen','kitchen-detail']],['countertop',2,'stone','permanent',0.9,['kitchen','kitchen-detail']],['sink',1,'porcelain','permanent',0.88,['kitchen','kitchen-detail']],['stove',1,'metal','freestanding',0.91,['kitchen','kitchen-detail']],['range_hood',1,'metal','permanent',0.86,['kitchen-detail']],['light_fixture',2,'metal','permanent',0.74,['kitchen','kitchen-detail']],['chair',4,'wood','freestanding',0.81,['kitchen']],['hardwood_floor',1,'wood','permanent',0.95,['kitchen']],['built_in_shelving',1,'wood','permanent',0.7,['kitchen-detail']],['window',1,'glass','permanent',0.9,['kitchen-detail']],['exterior_door',1,'glass','permanent',0.87,['kitchen-detail']],['hvac_vent',1,'metal','permanent',0.78,['kitchen']]],
  living:[['fireplace',1,'metal','permanent',0.9,['living']],['built_in_shelving',2,'wood','permanent',0.84,['living']],['wall_finish',1,'wood','permanent',0.66,['living']],['light_fixture',1,'metal','permanent',0.72,['living']],['hvac_vent',2,'metal','permanent',0.8,['living']],['exterior_door',1,'glass','permanent',0.9,['living']],['window',1,'glass','permanent',0.85,['living']],['furniture',1,'fabric','freestanding',0.83,['living']],['hardwood_floor',1,'wood','permanent',0.94,['living']]],
  bedroom:[['window',2,'glass','permanent',0.92,['bedroom']],['exterior_door',1,'glass','permanent',0.88,['bedroom']],['light_fixture',4,'metal','permanent',0.86,['bedroom']],['hvac_vent',1,'metal','permanent',0.79,['bedroom']],['smoke_detector',1,'plastic','permanent',0.71,['bedroom']],['hardwood_floor',1,'wood','permanent',0.93,['bedroom']]],
  exterior:[['pool',1,'concrete','permanent',0.96,['exterior']],['patio',1,'stone','permanent',0.9,['exterior']],['landscaping',1,'other','permanent',0.85,['exterior']],['outdoor_lighting',3,'metal','permanent',0.8,['exterior']],['security_system',1,'plastic','permanent',0.62,['exterior']],['fence',1,'wood','permanent',0.77,['exterior']],['porch',1,'wood','permanent',0.82,['exterior']]],
 };
 // The model's instances per photo: [label, x1, y1, x2, y2, score, counted]. Boxes are
 // image fractions drawn on the sample photos.
 const OBJECTS={
  kitchen:[['light_fixture',.301,.324,.353,.368,.93,1],['light_fixture',.400,.318,.450,.372,.92,1],['light_fixture',.478,.368,.513,.402,.88,1],
   ['kitchen_cabinet',.019,.246,.134,.608,.9,1],['kitchen_cabinet',.136,.279,.417,.469,.94,1],['kitchen_cabinet',.030,.597,.268,.737,.87,1],['kitchen_cabinet',.469,.301,.737,.703,.91,1],
   ['countertop',.238,.571,.588,.865,.9,1],['countertop',.019,.558,.469,.619,.83,1],['sink',.400,.525,.430,.595,.71,1],['stove',.246,.562,.357,.605,.79,1],
   ['chair',.363,.640,.450,.868,.86,1],['chair',.444,.622,.519,.820,.85,1],['chair',.510,.608,.563,.770,.8,1],['chair',.554,.599,.599,.746,.77,1],
   ['hardwood_floor',.134,.640,1,1,.95,1],['hvac_vent',.097,.076,.164,.179,.74,1],['kitchen_cabinet',0,.748,.134,1,.34,0]],
  'kitchen-detail':[['light_fixture',.202,.020,.334,.156,.91,1],['light_fixture',.345,.221,.411,.284,.89,1],['window',.470,.314,.636,.560,.93,1],['exterior_door',.224,.267,.334,.622,.86,1],
   ['range_hood',.781,.293,.981,.378,.84,1],['stove',.697,.635,.977,1,.9,1],['sink',.286,.664,.417,.710,.82,1],['built_in_shelving',.326,.361,.443,.501,.76,1],
   ['kitchen_cabinet',.719,0,.998,.404,.92,1],['kitchen_cabinet',.365,.592,.664,.723,.88,1],['kitchen_cabinet',.156,.625,.495,1,.86,1],['countertop',0,.609,.495,.911,.89,1],['countertop',.308,.570,.790,.638,.84,1]],
  living:[['fireplace',.385,.543,.531,.583,.92,1],['built_in_shelving',.236,.310,.311,.582,.9,1],['built_in_shelving',.603,.310,.676,.582,.89,1],['wall_finish',.365,.307,.549,.523,.68,1],['light_fixture',.380,.293,.531,.313,.73,1],
   ['hvac_vent',.181,.215,.239,.276,.81,1],['hvac_vent',.621,.215,.670,.276,.8,1],['exterior_door',.699,.254,.764,.671,.88,1],['window',0,.345,.156,.632,.84,1],['furniture',.193,.603,.717,.818,.9,1],['hardwood_floor',0,.66,1,1,.94,1]],
  bedroom:[['window',.452,.323,.504,.642,.93,1],['window',.739,.215,.891,.626,.94,1],['exterior_door',.562,.277,.651,.702,.88,1],
   ['light_fixture',.114,.104,.141,.121,.84,1],['light_fixture',.411,.249,.427,.259,.8,1],['light_fixture',.545,.187,.562,.198,.81,1],['light_fixture',.779,.073,.807,.092,.83,1],
   ['hvac_vent',.629,.092,.692,.177,.78,1],['smoke_detector',.432,.089,.455,.110,.7,1],['hardwood_floor',0,.68,1,1,.93,1]],
  exterior:[['pool',0,.804,1,1,.96,1],['patio',0,.580,1,.815,.88,1],['landscaping',.158,.603,.690,.778,.85,1],['outdoor_lighting',.272,.231,.289,.257,.79,1],['outdoor_lighting',.305,.279,.320,.299,.77,1],['outdoor_lighting',.344,.326,.356,.341,.74,1],
   ['security_system',.225,.179,.252,.209,.6,1],['fence',.521,.446,.662,.525,.72,1],['porch',.532,.140,1,.759,.83,1],['chair',.380,.510,.520,.600,.29,0]],
 };
 const PHRASES={light_fixture:'pendant light',kitchen_cabinet:'cabinet',hardwood_floor:'wood floor',hvac_vent:'air vent',built_in_shelving:'built-in shelves',exterior_door:'glass door',outdoor_lighting:'wall light',security_system:'security camera',wall_finish:'wood wall panel',porch:'covered patio',landscaping:'lawn'};
 // Classification and pricing per label (synthetic sample cost table, 2020 dollars per unit).
 // comp: the building-model component the row is itemized against (null: site, outside the model).
 const RULES={
  kitchen_cabinet:{category:'Kitchen cabinets',bucket:'y5',irs:'1245',unit:'EA',base:2850,comp:'E',row:'S-12 35 30.10 Casework, kitchen cabinet, wood'},
  countertop:{category:'Countertops',bucket:'y5',irs:'1245',unit:'LF',base:118,comp:'E',row:'S-12 36 61.16 Countertop, stone',qty:'count_lf',per:12},
  sink:{category:'Kitchen sink and faucet',bucket:'y39',irs:'1250',unit:'EA',base:1150,comp:'D',row:'S-22 41 16.30 Sink, kitchen, with faucet'},
  stove:{category:'Range',bucket:'y5',irs:'1245',unit:'EA',base:6400,comp:'E',row:'S-11 31 13.13 Range, gas, professional'},
  range_hood:{category:'Range hood',bucket:'y5',irs:'1245',unit:'EA',base:2100,comp:'E',row:'S-11 31 13.23 Range hood, ducted'},
  light_fixture:{category:'Decorative light fixtures',bucket:'y5',irs:'1245',unit:'EA',base:640,comp:'D',row:'S-26 51 13.50 Pendant fixture, decorative'},
  chair:{category:'Furniture (bar stools)',bucket:'y7',irs:'1245',unit:'EA',base:null,comp:null,row:null},
  furniture:{category:'Furniture (sofa)',bucket:'y7',irs:'1245',unit:'EA',base:null,comp:null,row:null},
  hardwood_floor:{category:'Hardwood flooring',bucket:'y39',irs:'1250',unit:'SF',base:14.5,comp:'C',row:'S-09 64 29.10 Wood strip flooring, oak',qty:'room_area'},
  built_in_shelving:{category:'Built-in shelving',bucket:'y5',irs:'1245',unit:'EA',base:1850,comp:'E',row:'S-06 22 13.15 Shelving, built-in, wood'},
  window:{category:'Windows',bucket:'y39',irs:'1250',unit:'EA',base:1450,comp:'B',row:'S-08 51 13.20 Window, aluminum, fixed'},
  exterior_door:{category:'Exterior glass doors',bucket:'y39',irs:'1250',unit:'EA',base:4800,comp:'B',row:'S-08 32 13.10 Door, sliding glass'},
  hvac_vent:{category:'HVAC diffusers',bucket:'y39',irs:'1250',unit:'EA',base:260,comp:'D',row:'S-23 37 13.10 Diffuser, linear slot'},
  fireplace:{category:'Fireplace (linear, gas)',bucket:'y39',irs:'1250',unit:'EA',base:5200,comp:'C',row:'S-10 31 13.10 Fireplace, gas, linear'},
  wall_finish:{category:'Decorative wood wall panel',bucket:'y5',irs:'1245',unit:'SF',base:38,comp:'C',row:'S-06 42 16.10 Wood paneling, decorative',qty:'count_sf',per:64},
  smoke_detector:{category:'Smoke detector',bucket:'y39',irs:'1250',unit:'EA',base:85,comp:'D',row:'S-28 31 46.50 Smoke detector, hard-wired'},
  pool:{category:'Swimming pool',bucket:'y15',irs:'1250',unit:'EA',base:68000,comp:null,row:'S-13 11 13.50 Swimming pool, residential, concrete'},
  patio:{category:'Patio paving',bucket:'y15',irs:'1250',unit:'SF',base:22,comp:null,row:'S-32 14 40.10 Paving, stone',qty:'fixed',fixed:850},
  landscaping:{category:'Landscaping (artificial turf)',bucket:'y15',irs:'1250',unit:'SF',base:9.5,comp:null,row:'S-32 18 23.40 Synthetic turf',qty:'fixed',fixed:900},
  outdoor_lighting:{category:'Exterior site lighting',bucket:'y15',irs:'1250',unit:'EA',base:420,comp:null,row:'S-26 56 19.20 Wall light, exterior'},
  security_system:{category:'Security camera',bucket:'y7',irs:'1245',unit:'EA',base:450,comp:null,row:'S-28 23 23.10 Camera, exterior'},
  fence:{category:'Fencing',bucket:'y15',irs:'1250',unit:'LF',base:48,comp:null,row:'S-32 31 13.20 Fence, wood lattice',qty:'needs_lf'},
  porch:{category:'Detached pavilion',bucket:'y39',irs:'1250',unit:'SF',base:95,comp:null,row:'S-13 34 13.13 Pavilion, open, wood frame',qty:'fixed',fixed:420},
 };
 // Room-specific rule changes and the flags the model and the checks raise (review_reasons).
 const RULE_BY_ROOM={'bedroom:light_fixture':{category:'Recessed lighting',bucket:'y39',irs:'1250',base:240,row:'S-26 51 13.30 Downlight, recessed'}};
 const FLAGS={
  'kitchen:light_fixture':['citation_unverified'],
  'kitchen:chair':['cross:COST_ROW_MISSING','model:Bar stools are loose furniture; confirm they convey with the property before keeping them on the schedule.'],
  'living:furniture':['cross:COST_ROW_MISSING'],
  'kitchen:built_in_shelving':['rule_contested:built_in_shelving'],
  'living:built_in_shelving':['rule_contested:built_in_shelving'],
  'living:wall_finish':['model:Decorative wood panel behind the fireplace; confirm it is a removable decorative finish rather than part of the wall.'],
  'exterior:fence':['cross:COST_NEEDS_QUANTITY'],
 };
 const REASON_CODES=[['cross:COST_ROW_MISSING','BASIS-CHECK-012'],['cross:COST_NEEDS_QUANTITY','BASIS-CHECK-011'],['rule_contested:','BASIS-ASSETS-005'],['model:','BASIS-ASSETS-009'],['citation_unverified','BASIS-ASSETS-007'],['missing_evidence','BASIS-ASSETS-008']];
 const codesFor=reasons=>[...new Set(reasons.map(r=>REASON_CODES.find(([p])=>r===p||r.startsWith(p))?.[1]).filter(Boolean))];
 const CITATIONS={
  y5:['rp-87-56-57.0','Rev. Proc. 87-56, asset class 57.0 (distributive trades and services): 5-year recovery for personal property used in the activity (paraphrased for the sample).',[12,12]],
  y7:['rp-87-56-00.11','Rev. Proc. 87-56, asset class 00.11 (office furniture, fixtures and equipment): 7-year recovery (paraphrased for the sample).',[9,9]],
  y15:['rp-87-56-00.3','Rev. Proc. 87-56, asset class 00.3 (land improvements): 15-year recovery for sidewalks, landscaping, fencing and similar improvements (paraphrased for the sample).',[10,10]],
  y39:['irc-168-c','IRC § 168(c): nonresidential real property, 39-year recovery; structural components follow the building (paraphrased for the sample).',[3,3]],
 };
 // The building model (synthetic square-foot model) and its components.
 const COST={baseYear:2020,costYear:2026,yearFactor:1.234,location:1.18,indexBase:'100.0',indexYear:'123.4'};
 const MODEL={sf:3240,perSf:236.75,name:'Sample square-foot model, two-story, custom',cls:'custom',type:'two_story'};
 const COMPONENTS=[['A','Substructure',0.1,'Foundations and slab (structure)'],['B','Shell',0.3,'Exterior walls, roof and openings (structure)'],['C','Interiors',0.2,'Interior construction and finishes (structure)'],['D','Services',0.3,'Plumbing, HVAC, electrical and fire protection (structure)'],['E','Equipment & furnishings',0.1,'Built-in equipment (structure)']];
 const DEPRECIATION={rate:'0.266667',effectiveAge:16,life:60};
 const INTAKE={purchase_price:'1240000.00',acquisition_costs:[{category:'title_insurance_owner',amount:'3150.00',description:null},{category:'legal_fees',amount:'2400.00',description:null},{category:'recording_fees',amount:'450.00',description:null}],land_method:'appraisal_site_value',appraisal_site_value:'280000.00',appraisal_effective_date:'2026-09-30',land_appraisal_value:null,land_appraisal_date:null,assessor_land_value:null,assessor_improvement_value:null,flat_land_percentage:null,flat_land_source:null,property_use:'nonresidential',dwelling_unit_income_share:null,transient_lodging:true,placed_in_service_date:'2026-11-01',acquisition_date:'2026-10-15',state:'CA',zip:null,location_source:'appraisal'};
 const STUDIES=[
  {n:1,name:'Harbor House',main:true},
  {n:2,name:'Cedar Court Inn',state:'signed',offsetDays:-21},
  {n:3,name:'Maple Row Lodge',state:'appraisal',offsetDays:-2},
 ];
 const T0=Date.parse('2026-10-28T15:00:00Z');

 /* ---------- one study: state, data, the workflow's steps ---------- */
 function makeStudy(spec,inst){
  const n=spec.n,uid=(kind,i)=>`5a3${kind}${n.toString(16).padStart(4,'0')}-0000-4000-8000-${String(i).padStart(12,'0')}`;
  let seq=1000;const next=kind=>uid(kind,seq++);
  const S={n,uid,next,id:uid(0,0),name:spec.name,status:'draft',stage:'uploading',revision:1,created_at:null,updated_at:null,
   docs:[],fields:[],extractionId:uid('a',1),rooms:[],objects:{},has:{},assetEdits:{},compPrices:{},reviews:[],corrections:[],revisions:[],events:[],audit:[],steps:[],stages:[],calls:[],snapshots:{},pending:null,runs:[],exports:{},signatures:{},answers:null,answersVersion:0,intakeVersion:1,seedClock:T0+(spec.offsetDays||0)*86400000};
  S.clock=()=>inst.seeding?(S.seedClock+=97000):inst.now();
  let i=0;for(const d of DOCS)S.docs.push({...d,id:uid(1,++i)});
  S.doc=key=>S.docs.find(d=>d.key===key);
  return S;
 }
 const docId=(S,key)=>S.doc(key).id;
 function buildData(S){
  const at=iso(S.clock()),prov=null;
  S.fields=FIELDS.map(([key,label,kind,value,places,confidence,critical],i)=>{const [section,...rest]=key.split('.');return {id:S.uid(2,i+1),extraction_id:S.extractionId,document_id:docId(S,'appraisal'),pdf_document_id:docId(S,'appraisal'),key,section,field:rest.join('.'),label,critical,value_text:kind==='text'?value:null,value_num:kind==='num'?value:null,value_date:kind==='date'?value:null,confidence,source:'llm',page:places[0][0],regions:places.map(([page,bbox])=>({page,bbox})),provenance:{origin:'model',correction_id:null,review_id:null,stage:'extracting',revision:S.revision,at,by:null,before:null,after:null,changed:[],carried_from:null}}});
  let c=0;
  S.rooms=ROOMS.map((r,ri)=>({id:S.uid(3,ri+1),key:r.key,name:r.name,room_type:r.room_type,floor:r.floor,area:r.area,source:'vision',group_key:r.key,photos:r.photos.map(k=>docId(S,k)),provenance:prov,
   inventory:INVENTORY[r.key].map(([label,count,material,attachment,confidence,seen])=>({component_id:S.uid(4,++c),label,count,material,attachment,confidence,document_id:docId(S,seen[0]),seen_in:seen.map(k=>docId(S,k)),added:false,provenance:null,roomKey:r.key}))}));
  let o=0;S.objects={};
  for(const [key,list] of Object.entries(OBJECTS))S.objects[docId(S,key)]=list.map(([label,x1,y1,x2,y2,score,counted])=>({id:S.uid(5,++o),label,phrase:PHRASES[label]||label.replace(/_/g,' '),score,box:{x1,y1,x2,y2},area:round4((x2-x1)*(y2-y1)),source:'model',counted:Boolean(counted),derived_from:null,created_by:null,created_at:at,deleted_at:null,deleted_by:null,mask_source:null,mask_reason:null}));
 }

 /* ---------- events, audit, steps ---------- */
 let requestSeq=0;
 function event(S,type,{actor='system',by=null,stage=S.stage,revision=S.revision,entity_type=null,entity_id=null,before=null,after=null,detail=null,activity=null,duration=null,code=null}={}){
  const at=iso(S.clock());S.updated_at=at;
  S.events.push({seq:S.events.length+1,at,event_type:type,actor_type:actor,actor_id:actor==='engineer'?(by||ME.id):null,stage,revision,workflow_id:S.runs.length?'study-'+S.id:null,run_id:S.runs.length?S.runs[S.runs.length-1].run_id:null,activity,activity_id:activity?activity+'-'+S.events.length:null,attempt:activity?1:null,started_at:duration!=null?iso(Date.parse(at)-duration):null,duration_ms:duration,entity_type,entity_id,code,request_id:actor==='engineer'?'sample-req-'+(++requestSeq):null,before,after,detail});
  return at;
 }
 function audit(S,kind,action,extra={}){S.audit.push({kind,at:iso(S.clock()),by:ME.id,stage:S.stage,revision:S.revision,action,entity_type:null,entity_id:null,before:null,after:null,changed:[],reason:null,review_id:null,correction_id:null,produced:[],...extra})}
 // The workflow's activities for one processing step, recorded as finished.
 function activities(S,names){
  for(const name of names){const ms=1200+(name.length*137)%2400,end=S.clock();S.steps.push({activity:name,activity_id:`${name}-${S.steps.length+1}`,started_at:iso(end-ms),ended_at:iso(end),duration_ms:ms,attempts:[{attempt:1,started_at:iso(end-ms),ended_at:iso(end),duration_ms:ms,outcome:'completed',code:null,error:null}],outcome:'completed',code:null});event(S,'activity.completed',{activity:name,duration:ms})}
 }
 const photoKeys=()=>DOCS.filter(d=>d.kind==='photo').map(d=>d.key);
 const WORK={
  appraisal_review:()=>['ingest_appraisal','extract_fields',...photoKeys().map(()=>'analyze_photo')],
  rooms_review:()=>['reconcile_rooms'],
  components_review:()=>['reconcile_rooms',...photoKeys().map(()=>'localize_photo')],
  assets_review:()=>['process_assets'],
  completed:()=>['promote_conventions','export_report'],
 };
 function enterStage(S,stage,by){
  const at=iso(S.clock()),open=S.stages[S.stages.length-1];
  if(open&&!open.left_at){open.left_at=at;open.duration_ms=Date.parse(at)-Date.parse(open.entered_at)}
  const before={stage:S.stage,revision:S.revision};
  S.stage=stage;S.stages.push({stage,revision:S.revision,entered_at:at,entered_by:by?'engineer':'system',left_at:null,duration_ms:null,gate_opened_at:null,decided_at:null,decision:null,review_wait_ms:null});
  event(S,'stage.entered',{actor:by?'engineer':'system',before,after:{stage,revision:S.revision}});
 }
 function startRun(S,reason){
  const run={workflow_id:'study-'+S.id,run_id:S.next('a'),workflow_version:'sample-1',input_revision:S.revision,started_at:iso(S.clock()),ended_at:null,outcome:null};
  S.runs.push(run);event(S,'run.started',{detail:{reason}});
 }
 // Where a decision leaves the study while the workflow runs, and which gate opens next.
 const AFTER_APPROVE={appraisal_review:['rooms_review','rooms_review'],rooms_review:['components_processing','components_review'],components_review:['assets_processing','assets_review'],assets_review:['exporting','completed']};
 const AFTER_REJECT={appraisal_review:'extracting',rooms_review:'extracting',components_review:'components_processing',assets_review:'assets_processing'};
 function openGate(S,stage){
  (WORK[stage]?activities(S,WORK[stage]()):null);
  S.calls.push(...callsFor(S,stage));
  if(stage==='completed'){
   if(S.stage!=='exporting')enterStage(S,'exporting');
   enterStage(S,'completed');S.status='completed';
   const r=S.result=resultOf(S),skipped=r.allocation?null:{code:E.INPUTS_MISSING,message:'The allocation was withheld: '+(r.missing.join(', ')||'see the result')};
   S.exports[S.revision]={uri:`sample://exports/${S.id}/rev-${S.revision}/summary.json`,sha256:digest(`export:${S.id}:${S.revision}:${JSON.stringify(r.schedule)}`),at:iso(S.clock()),skipped};
   event(S,'export.written',{detail:{skipped}});
   const run=S.runs[S.runs.length-1];if(run){run.ended_at=iso(S.clock());run.outcome='completed'}
   return;
  }
  if(S.stage!==stage)enterStage(S,stage);
  S.status='waiting_review';
  S.has.fields=true;if(stage!=='appraisal_review')S.has.rooms=true;if(stage==='components_review'||stage==='assets_review')S.has.objects=true;if(stage==='assets_review')S.has.assets=true;
  const st=S.stages[S.stages.length-1];st.gate_opened_at=iso(S.clock());
  event(S,'gate.opened');
 }
 // A pending step finishes once its time has come (any request checks; no timers).
 function tick(S,inst){if(S.pending&&inst.now()>=S.pending.until){const p=S.pending;S.pending=null;openGate(S,p.gate)}}
 function schedule(S,inst,gate){S.status='processing';S.pending={gate,until:inst.seeding?0:inst.now()+inst.delayMs};if(inst.seeding){S.pending=null;openGate(S,gate)}}

 /* ---------- the views (the API's response bodies) ---------- */
 const reached=(S,stage)=>S.status==='completed'||ORDER.indexOf(S.stage)>=ORDER.indexOf(stage);
 function studyOut(S){return {id:S.id,org_id:ORG_ID,created_by:ME.id,created_by_name:ME.name,created_by_email:ME.email,property_name:S.name,status:S.status,current_stage:S.stage,revision:S.revision,room_aliases:{},failure_reason:null,components_gate:true,intake:clone(INTAKE),intake_version:S.intakeVersion,purged_at:null,created_at:S.created_at,updated_at:S.updated_at}}
 function studyDetail(S){
  const latest=new Map();for(const r of S.reviews)latest.set(r.stage,r);
  return {...studyOut(S),documents:S.docs.map(d=>({id:d.id,study_id:S.id,kind:d.kind,filename:d.filename,content_type:d.content_type,bytes:d.bytes,sha256:d.sha256,uploaded_at:S.created_at,created_at:S.created_at})),latest_reviews:[...latest.values()].map(clone)};
 }
 function fieldsOut(S){return S.fields.map(f=>clone(f))}
 // Suggested counts: the most counted instances of a label in one photo of the room (model
 // and manual), against the room's inventory count.
 function suggested(S,room){
  const out=new Map();
  for(const doc of room.photos){
   const per=new Map();for(const o of liveObjects(S,doc).filter(o=>o.counted)){const p=per.get(o.label)||{model:0,manual:0};p[o.source==='manual'?'manual':'model']++;per.set(o.label,p)}
   for(const [label,p] of per){const best=out.get(label);if(!best||p.model+p.manual>best.photo_count+best.manual_count)out.set(label,{label,inventory_count:0,photo_count:p.model,manual_count:p.manual,document_id:doc,suggested:0})}
  }
  for(const s of out.values()){s.inventory_count=room.inventory.filter(i=>i.label===s.label).reduce((a,i)=>a+i.count,0);s.suggested=Math.max(s.inventory_count,s.photo_count+s.manual_count)}
  return [...out.values()];
 }
 function roomsOut(S){
  const before=!reached(S,'components_review');
  return {items:S.rooms.map(r=>({id:r.id,name:r.name,room_type:r.room_type,floor:r.floor,source:r.source,group_key:r.group_key,photos:r.photos.map(d=>({document_id:d,filename:S.docs.find(x=>x.id===d).filename,provenance:null})),inventory_source:before?'photos':'room_inventory',needs_review:r.inventory.some(i=>i.confidence!==null&&i.confidence<0.7),inventory:r.inventory.map(({roomKey,...i})=>clone(i)),suggested_counts:S.has.objects?suggested(S,r):[],provenance:clone(r.provenance)})),unplaced:[]};
 }
 const liveObjects=(S,doc)=>(S.objects[doc]||[]).filter(o=>!o.deleted_at);
 const OBJECT_OUT=[];// hooks: (output, stored object) => void (the PROPOSED section adds `exclusion`)
 // A plausible SAM 3 outline for a drawn box (the real API prompts SAM 3 with the box): a rounded,
 // slightly irregular shape filling most of the box, as MaskOut run lengths, zeros first.
 function outlineMask(box,W=320,H=240){
  const cx=(box.x1+box.x2)/2*W,cy=(box.y1+box.y2)/2*H,rx=Math.max(1,(box.x2-box.x1)/2*W*0.92),ry=Math.max(1,(box.y2-box.y1)/2*H*0.92);
  const counts=[];let run=0,on=false;
  for(let y=0;y<H;y++)for(let x=0;x<W;x++){
   const dx=(x+0.5-cx)/rx,dy=(y+0.5-cy)/ry,a=Math.atan2(dy,dx),r=1+0.06*Math.sin(3*a)+0.04*Math.cos(5*a);
   const inside=Math.pow(Math.abs(dx),2.6)+Math.pow(Math.abs(dy),2.6)<=r;
   if(inside===on)run++;else{counts.push(run);run=1;on=inside}
  }
  counts.push(run);return {height:H,width:W,counts};
 }
 function objectOut(S,doc,o){const out={id:o.id,document_id:doc,revision:S.revision,label:o.label,phrase:o.phrase,score:o.score,box:{...o.box},mask:o.mask?{...o.mask,counts:[...o.mask.counts]}:null,area:o.area,source:o.source,counted:o.counted,derived_from:o.derived_from,created_by:o.created_by,created_at:o.created_at,deleted_at:o.deleted_at,deleted_by:o.deleted_by,mask_source:o.mask_source,mask_reason:o.mask_reason};OBJECT_OUT.forEach(fn=>fn(out,o));return out}
 function objectsOut(S,doc){
  if(!S.has.objects)return {document_id:doc,revision:S.revision,localization:null,counts:{},items:[]};
  const items=liveObjects(S,doc).map(o=>objectOut(S,doc,o)),counts={};
  for(const o of items)if(o.counted)counts[o.label]=(counts[o.label]||0)+1;
  return {document_id:doc,revision:S.revision,localization:{status:'done',reason:null,instances:items.filter(o=>o.source==='model').length,model_version:'sample-localizer-1',carried_from_revision:null,created_at:S.created_at},counts,items};
 }

 /* ---------- assets, priced from the inventories ---------- */
 const unitCost=base=>Math.round(Math.round(Math.round(base*100)*COST.yearFactor)*COST.location);
 const buildingRcn=()=>{const base=Math.round(MODEL.sf*MODEL.perSf*100),year=Math.round(base*COST.yearFactor),loc=Math.round(year*COST.location);return {base,year,loc}};
 const yearTrail=()=>({cost_year:COST.costYear,factor:COST.yearFactor.toFixed(3),index:{year:COST.costYear,value:COST.indexYear,as_of:'2026-07-01',citation:'Sample cost index (illustrative), 2026',estimated:false,derived:null,store:{id:'5a3b0000-0000-4000-8000-000000002026',source:'sample',version:1}},base:{year:COST.baseYear,value:COST.indexBase,as_of:'2020-07-01',citation:'Sample cost index (illustrative), 2020',estimated:false,derived:null,store:null},assumption:null,conversion:null,citation:'Sample cost index (illustrative): 123.4 / 100.0',from:'cost_indices'});
 const locationTrail=()=>({factor:COST.location.toFixed(3),from:'state',lookup:'state',zip:null,lookup_from:'appraisal',state:'CA',city:null,zip_prefixes:null,cities:null,low:null,high:null,pages:null,table_year:COST.baseYear,origin:'sample',citation:'Sample location factors (illustrative), California',store:null});
 const METHOD={cost_basis:'total_incl_op',rcn_source:'square_foot_model',modifications:false,overhead_allocation:'pro_rata',specialties_residual:'row'};
 const COST_HOOKS=[];// hooks: (asset, context) => void (the PROPOSED section applies a unit price override)
 function provenanceOf(edit,base){return edit?{origin:'engineer_modified',correction_id:edit.correction_id,review_id:edit.review_id,stage:'assets_review',revision:edit.revision,at:edit.at,by:ME.id,before:edit.before,after:edit.after,changed:Object.keys(edit.after),carried_from:null}:base}
 function itemizedAssets(S){
  const out=[];
  for(const room of S.rooms)for(const item of room.inventory){
   const key=`${item.roomKey||room.key}:${item.label}`,rule={...(RULES[item.label]||{category:item.label.replace(/_/g,' ').replace(/^./,c=>c.toUpperCase()),bucket:'y39',irs:'1250',unit:'EA',base:null,comp:null,row:null}),...(RULE_BY_ROOM[key]||{})};
   const id=item.component_id.replace(/^5a34/,'5a36'),edit=S.assetEdits[id];
   const flags=edit?[]:(FLAGS[key]||[]).slice();
   // Takeoff
   let quantity,unit=rule.unit,formula=null,method='count',inputs={count:item.count},assumptions=[];
   if(edit?.quantity!==undefined){quantity=Number(edit.quantity);unit=edit.quantity_unit;formula=null;method='engineer_measured';inputs={quantity_source:edit.quantity_source}}
   else if(rule.qty==='room_area'){const area=room.area||200;quantity=Math.round(area*1.05*item.count);formula=`room_area_sf × waste_factor = ${area} × 1.05`;method='room_area';inputs={room_area_sf:String(area),waste_factor:'1.05',room_area_source:'typical_for_room_type'};assumptions=['Room area from the room type; not measured.']}
   else if(rule.qty==='count_lf'||rule.qty==='count_sf'){quantity=item.count*rule.per;formula=`count × ${rule.per} ${unit} per item`;method='count_scaled';inputs={count:item.count,per_item:String(rule.per)}}
   else if(rule.qty==='fixed'){quantity=rule.fixed;formula=`site area estimate ${rule.fixed} ${unit}`;method='site_estimate';inputs={estimate:String(rule.fixed)};assumptions=['Estimated from the exterior photo; not measured.']}
   else if(rule.qty==='needs_lf'){quantity=item.count;unit='EA';method='count'}
   else quantity=item.count;
   const priced=rule.base!==null&&unit===rule.unit;
   const ctx={id,componentId:item.component_id,S,quantity,unit};
   let unit_cost=priced?unitCost(rule.base):null,total=unit_cost===null?null:Math.round(unit_cost*quantity);
   const asset={id,component_id:item.component_id,label:item.label,count:item.count,room:{id:room.id,name:room.name,room_type:room.room_type,floor:room.floor},
    category:edit?.category??rule.category,bucket:edit?.bucket??rule.bucket,recovery_period:edit?.recovery_period??BUCKET_YEARS[edit?.bucket??rule.bucket],irs_section:edit?.irs_section??rule.irs,method:flags.length?'classifier':'rules',confidence:item.confidence,needs_review:flags.length>0,review_reasons:flags,
    takeoff:{quantity:String(quantity),unit,formula,method,inputs,assumptions},cost:null,citations:0,verified_citations:0,review_codes:codesFor(flags),provenance:provenanceOf(edit,null),convention:null};
   const qtyTrail={value:String(quantity),unit,formula,method,inputs,assumptions};
   asset.cost={unit_cost:unit_cost===null?null:money(unit_cost),total:total===null?null:money(total),source_row:rule.row,location_factor:COST.location.toFixed(3),cost_year:COST.costYear,
    trail:{version:1,kind:priced?'unit_price':'unpriced',quantity:qtyTrail,base:rule.row?{unit_cost:money(Math.round(rule.base*100)),unit:rule.unit,table:'sample_unit_costs',row:rule.row,tier:null,source:'Sample cost table (illustrative)',base_year:COST.baseYear,row_unit:rule.unit,conversion:null,assumption:null,basis:'total_incl_op',basis_how:null,markup:null}:null,year:priced?yearTrail():null,location:priced?locationTrail():null,structure:null,intake_version:S.intakeVersion,property_class:'nonresidential',method:{...METHOD},cost_year:COST.costYear,location_factor:COST.location.toFixed(3),unit_cost:unit_cost===null?null:money(unit_cost),total:total===null?null:money(total),rounding:priced?'cent':null,priced_per:null,missing:priced?null:rule.row?`The cost row prices per ${rule.unit}; the takeoff counts ${unit}. Measure it to price it.`:'No cost table row matches this component.'},trail_recorded:true};
   COST_HOOKS.forEach(fn=>fn(asset,ctx));
   const cite=CITATIONS[asset.bucket];asset.citations=cite?1:0;asset.verified_citations=cite&&!flags.includes('citation_unverified')?1:0;
   asset.comp=rule.comp;out.push(asset);
  }
  return out;
 }
 function structureRows(S,itemized){
  const {loc}=buildingRcn(),rows=[],components=[];
  const shares=apportion(COMPONENTS.map(c=>c[2]*1e6),loc);
  COMPONENTS.forEach(([code,name,share,label],i)=>{
   const top=shares[i],mine=itemized.filter(a=>a.comp===code),itemCents=mine.reduce((s,a)=>s+(a.cost.total?toCents(a.cost.total):0),0),residual=Math.max(0,top-itemCents),clamped=Math.max(0,itemCents-top);
   const id=S.uid(6,900+i),edit=S.assetEdits[id];
   const amount=money(residual),qty={value:'1',unit:'LS',formula:`${name} top-down share less its itemized rows`,method:'structure_residual',inputs:{component:code,share:share.toFixed(2),top_down:money(top),itemized:money(itemCents)},assumptions:[]};
   rows.push({id,component_id:null,label:null,count:null,room:null,category:'Building structure: '+name.toLowerCase(),bucket:edit?.bucket??'y39',recovery_period:edit?.recovery_period??'39',irs_section:edit?.irs_section??'1250',method:'structure_model',confidence:null,needs_review:false,review_reasons:[],
    takeoff:{quantity:'1',unit:'LS',formula:qty.formula,method:'structure_residual',inputs:qty.inputs,assumptions:[]},
    cost:{unit_cost:amount,total:amount,source_row:null,location_factor:COST.location.toFixed(3),cost_year:COST.costYear,trail:{version:1,kind:'structure_residual',quantity:qty,base:null,year:null,location:null,structure:{label,system:name,component:code,source:'square_foot_model',rcn:amount,building:{amount:money(loc),source:'square_foot_model',formula:buildingFormula(),inputs:buildingInputs(),assumptions:buildingAssumptions()}},intake_version:S.intakeVersion,property_class:'nonresidential',method:{...METHOD},cost_year:COST.costYear,location_factor:COST.location.toFixed(3),unit_cost:amount,total:amount,rounding:'cent',priced_per:null,missing:null},trail_recorded:true},
    citations:1,verified_citations:1,review_codes:[],provenance:provenanceOf(edit,null),convention:null,comp:code,structureLabel:label});
   components.push({component:code,name,share:share.toFixed(6),top_down:money(top),itemized:money(itemCents),itemized_assets:mine.filter(a=>a.cost.total).length,unpriced_assets:mine.filter(a=>!a.cost.total).length,residual:money(residual),clamped:money(clamped),concealed:money(residual)});
  });
  return {rows,components};
 }
 const buildingInputs=()=>({living_area_sf:String(MODEL.sf),base_per_sf:MODEL.perSf.toFixed(2),base_year:String(COST.baseYear),year_factor:COST.yearFactor.toFixed(3),location_factor:COST.location.toFixed(3),stories:'2',quality:'custom'});
 const buildingAssumptions=()=>['Living area from the appraisal (3,240 SF).','Quality class custom from the appraisal photos; confirm under Concealed systems.'];
 function buildingFormula(){const {base,year,loc}=buildingRcn();return `3,240 SF × $${MODEL.perSf.toFixed(2)}/SF (${COST.baseYear}) = $${money(base)}; × ${COST.yearFactor.toFixed(3)} (cost year ${COST.costYear}) = $${money(year)}; × ${COST.location.toFixed(3)} (location) = $${money(loc)}`}
 function assetsAll(S){const items=itemizedAssets(S),{rows,components}=structureRows(S,items);return {items:[...items,...rows],components}}
 const assetOut=a=>{const {comp,structureLabel,...rest}=a;return clone(rest)};

 /* ---------- the result: schedule, structure, reconciliation, allocation ---------- */
 const BUCKETS=['y5','y7','y15','y27_5','y39','land'];
 function resultOf(S){
  const {items,components}=assetsAll(S),rate=Number(DEPRECIATION.rate),priced=items.filter(a=>a.cost.total!==null),unpriced=items.filter(a=>a.cost.total===null);
  const rcn=a=>toCents(a.cost.total),rcnld=a=>Math.round(rcn(a)*(1-rate));
  const sumRcn=priced.reduce((s,a)=>s+rcn(a),0),sumLd=priced.reduce((s,a)=>s+rcnld(a),0);
  const price=toCents(INTAKE.purchase_price),land=toCents(INTAKE.appraisal_site_value),cc=INTAKE.acquisition_costs.reduce((s,c)=>s+toCents(c.amount),0),B=price-land,T=B+cc;
  const structureRowsOnly=items.filter(a=>a.method==='structure_model');
  const outside=priced.filter(a=>a.comp===null).reduce((s,a)=>s+rcn(a),0);
  const gap=B-sumLd,gapShare=gap/B,status=Math.abs(gapShare)<=0.1?'reconciled':'unreconciled';
  const worksheet={purchase_price:INTAKE.purchase_price,acquisition_costs:INTAKE.acquisition_costs.map(c=>({...c,authority:'Reg. 1.263(a)-2(f)(2)(ii): facilitative costs of acquiring property; Pub. 551'})),capitalized_costs:money(cc),land:money(land),land_method:'appraisal_site_value',land_source:"the appraisal's opinion of site value as of 2026-09-30",building_basis:money(B),total_basis:money(T),property_use:'nonresidential',recovery_period:'39',placed_in_service_date:INTAKE.placed_in_service_date,acquisition_date:INTAKE.acquisition_date,state:'CA',zip:null,location_source:'appraisal',
   checks:[{code:'land_share_of_price',severity:'ok',message:`land is ${(land/price*100).toFixed(1)} % of the price (expected 5–60 %)`},{code:'land_hierarchy',severity:'ok',message:'appraisal_site_value is the best available source'},{code:'land_vs_appraisal_site_value',severity:'ok',message:'land is the site value'},{code:'land_value_date',severity:'ok',message:'the land value is dated 2026-09-30, 15 days from 2026-10-15 (window 365 days)'},{code:'recovery_period',severity:'ok',message:'nonresidential real property, 39 years: transient lodging (average stay of 30 days or less), not a dwelling unit (§168(e)(2)(A))'},{code:'placed_in_service_date',severity:'ok',message:'placed in service 2026-11-01 (ready and available for its use; mid-month convention)'}]};
  const scheduleBuckets=BUCKETS.filter(b=>items.some(a=>a.bucket===b)).map(b=>{const all=items.filter(a=>a.bucket===b),p=all.filter(a=>a.cost.total!==null);return {bucket:b,assets:all.length,rcn:money(p.reduce((s,a)=>s+rcn(a),0)),rcnld:money(p.reduce((s,a)=>s+rcnld(a),0))}});
  const schedule={assets:items.length,priced:priced.length,rcn_total:money(sumRcn),rcnld_total:money(sumLd),concealed:structureRowsOnly.length,concealed_rcn:money(structureRowsOnly.reduce((s,a)=>s+rcn(a),0)),by_bucket:scheduleBuckets,unpriced:unpriced.map(a=>a.id),land:[]};
  const {loc}=buildingRcn();
  const structure={building_rcn:{amount:money(loc),source:'square_foot_model',formula:buildingFormula(),inputs:buildingInputs(),assumptions:buildingAssumptions(),checks:[{code:'building_rcn_per_sf',severity:'ok',message:`$${(loc/100/MODEL.sf).toFixed(2)} per SF is within the sample model's range for a custom two-story house`}],needs_review:false,model:{name:MODEL.name,cls:MODEL.cls,type:MODEL.type,source:'Sample square-foot model (illustrative)',class_source:'appraisal photos',type_source:'appraisal: two stories'}},
   components,concealed:structureRowsOnly.map(a=>({asset_id:a.id,label:a.structureLabel,system:a.structureLabel,category:a.category,bucket:a.bucket,component:a.comp,rcn:a.cost.total})),outside_model:money(outside)};
  const lines=components.map(c=>({component:c.component,top_down:c.top_down,itemized:c.itemized,concealed:c.concealed,clamped:c.clamped}));
  const reconciliation={status,building_basis:money(B),rcn_total:money(sumRcn),rcnld_total:money(sumLd),gap:money(gap),gap_share:gapShare.toFixed(4),tolerance:'0.10',implied_uniform_factor:(T/sumRcn).toFixed(9),by_component:lines,unassigned_remainders:[],outside_model:money(outside),unpriced_assets:unpriced.length,
   appraisal_check:{code:'appraisal_cost_approach',severity:'not_checked',message:'the appraisal has no cost approach to compare against'},
   causes:status==='reconciled'?[]:[{code:'missing_items',cause:'Items missing from the schedule',finding:`${unpriced.length} rows are unpriced`},{code:'quantities',cause:'Quantities',finding:'engineer quantities changed the itemized total'}],
   checks:[{code:'reconciliation_gap',severity:status==='reconciled'?'ok':'error',message:`the depreciated schedule is ${money(sumLd)} against a building basis of ${money(B)}: gap ${(gapShare*100).toFixed(1)} % (tolerance 10 %)`}]};
  let allocation=null;
  if(status==='reconciled'&&sumLd>0){
   const factor=T/sumLd,cents=apportion(priced.map(rcnld),T),raw=priced.map(a=>rcnld(a)*factor);
   const assets=priced.map((a,i)=>({asset_id:a.id,category:a.category,bucket:a.bucket,rcn:a.cost.total,rcnld:money(rcnld(a)),allocated:money(cents[i]),rounding_cents:cents[i]-Math.round(raw[i]),label:a.label,concealed:a.method==='structure_model'})).sort((x,y)=>x.asset_id.localeCompare(y.asset_id));
   const byBucket=BUCKETS.filter(b=>priced.some(a=>a.bucket===b)).map(b=>{const idx=priced.map((a,i)=>a.bucket===b?i:-1).filter(i=>i>=0);return {bucket:b,rows:idx.length,rcn:idx.reduce((s,i)=>s+rcn(priced[i]),0),rcnld:idx.reduce((s,i)=>s+rcnld(priced[i]),0),allocated:idx.reduce((s,i)=>s+cents[i],0)}});
   const building=apportion(byBucket.map(b=>b.allocated),B);
   allocation={building_basis:money(B),closing_costs:money(cc),total:money(T),rcn_total:money(sumRcn),rcnld_total:money(sumLd),depreciated:true,factor:factor.toFixed(9),rows_sum:money(cents.reduce((s,c)=>s+c,0)),residual_cents:0,
    by_bucket:byBucket.map((b,i)=>({bucket:b.bucket,rows:b.rows,rcn:money(b.rcn),rcnld:money(b.rcnld),allocated:money(b.allocated),percent:(b.allocated/T*100).toFixed(1),share:(b.allocated/T).toFixed(6),building:money(building[i]),closing_costs:money(b.allocated-building[i])})),
    by_system:null,by_system_reason:'the sample schedule is grouped by recovery class only',assets,
    checks:[{code:'rcnld_factor',severity:'ok',message:`one factor ${factor.toFixed(9)} (total basis ${money(T)} / depreciated schedule ${money(sumLd)}) on every priced row`}]};
  }
  return {revision:S.revision,intake:true,missing:allocation?[]:['allocation'],blockers:[],cost_index:{cost_year:COST.costYear,status:'published',factor:COST.yearFactor.toFixed(3),applied:'Sample cost index 123.4 (2026) over 100.0 (2020)',needed:null,how:null,assumption:null},
   worksheet,schedule,structure,depreciation:{rate:DEPRECIATION.rate,method:'age_life',source:`effective age ${DEPRECIATION.effectiveAge} years of a ${DEPRECIATION.life}-year economic life (built 1998, average condition)`,checks:[{code:'depreciation_age_life',severity:'ok',message:`age-life ${DEPRECIATION.effectiveAge}/${DEPRECIATION.life} = ${DEPRECIATION.rate}`}]},
   reconciliation,allocation,not_produced:[],pricing:{priced_with:pricedWith(S),current:pricedWith(S),stale:false,reasons:[],remedy:null}};
 }
 const pricedWith=S=>({recorded:true,cost_year:COST.costYear,year_factor:COST.yearFactor.toFixed(3),year_citation:'Sample cost index (illustrative): 123.4 / 100.0',year_store_version:1,location_factor:COST.location.toFixed(3),location_citation:'Sample location factors (illustrative), California',intake_version:S.intakeVersion,property_class:'nonresidential',model_class:MODEL.cls,building_type:MODEL.type,living_area_sf:String(MODEL.sf),method:{...METHOD}});
 function evidenceOut(S,assetId){
  const {items}=assetsAll(S);
  return items.filter(a=>!assetId||a.id===assetId).map(a=>{
   const room=a.room?S.rooms.find(r=>r.id===a.room.id):null,item=room?.inventory.find(i=>i.component_id===a.component_id);
   const photos=room?room.photos.map(doc=>({document_id:doc,filename:S.docs.find(d=>d.id===doc).filename,role:doc===item?.document_id?'source':'room',localization_status:S.has.objects?'done':null,instances:S.has.objects?liveObjects(S,doc).filter(o=>o.counted&&o.label===a.label).map(o=>objectOut(S,doc,o)):[]})):[];
   const cite=a.method==='structure_model'?CITATIONS.y39:CITATIONS[a.bucket];
   return {asset_id:a.id,category:a.category,bucket:a.bucket,recovery_period:a.recovery_period,method:a.method,needs_review:a.needs_review,component_id:a.component_id,component_label:a.label,room:a.room?{...a.room}:null,photos,
    citations:cite?[{id:a.id.replace(/^5a36/,'5a3c'),asset_id:a.id,chunk_id:cite[0],excerpt:cite[1],verified_at:a.verified_citations?S.created_at:null,verification_method:a.verified_citations?'excerpt_match':null,verified:Boolean(a.verified_citations),source:{doc_id:cite[0].startsWith('irc')?'irc-168':'rev-proc-87-56',page_start:cite[2][0],page_end:cite[2][1],section_path:cite[0]}}]:[]};
  });
 }

 /* ---------- report ---------- */
 function reportState(S,inst){
  const sig=S.signatures[S.revision]||null,exp=S.exports[S.revision];
  const changed=sig?[...(inst.profileVersion>sig.firm_profile_version?['firm']:[]),...(S.answersVersion>sig.report_answers_version?['study']:[])]:null;
  return {study_id:S.id,revision:S.revision,exported:Boolean(exp),skipped:exp?.skipped||null,files:exp&&!exp.skipped?['report.pdf','schedule.csv']:[],missing_for_final:[...(inst.profile?[]:['firm']),...(S.answers?[]:['study']),...(sig?[]:['signature'])],signature:sig?clone(sig):null,firm_profile_version:inst.profileVersion,report_answers_version:S.answersVersion,changed_since_signing:changed};
 }
 const previewTag=(S,inst)=>digest(`preview:${S.id}:${S.revision}:${inst.profileVersion}:${S.answersVersion}`);
 function revisionsOut(S){
  return {study_id:S.id,current_revision:S.revision,items:S.revisions.map(r=>{const exp=S.exports[r.revision],sig=S.signatures[r.revision];return {...clone(r),stage_reached:r.revision===S.revision?S.stage:r.stage_reached,ended_at:r.revision===S.revision?null:r.ended_at,export:exp&&!exp.skipped?{uri:exp.uri,sha256:exp.sha256,at:exp.at}:null,signature:sig?{id:sig.id,signed_by:sig.signed_by,signed_at:sig.signed_at}:null,current:r.revision===S.revision}})};
 }
 function newRevision(S,origin){
  const prev=S.revisions[S.revisions.length-1];if(prev){prev.ended_at=iso(S.clock());prev.stage_reached=S.stage}
  S.revisions.push({revision:S.revision,started_at:iso(S.clock()),ended_at:null,origin,corrections:{},decisions:[],stage_reached:null});
 }
 // Every view of the revision, kept when the next revision starts (GET ?revision=<older>).
 function snapshot(S){
  S.snapshots[S.revision]={fields:S.has.fields?fieldsOut(S):[],rooms:S.has.rooms?roomsOut(S):{items:[],unplaced:[]},objects:Object.fromEntries(S.docs.filter(d=>d.kind==='photo').map(d=>[d.id,objectsOut(S,d.id)])),assets:S.has.assets?assetsAll(S).items.map(assetOut):[],result:S.has.assets?resultOf(S):null,evidence:S.has.assets?evidenceOut(S):[]};
 }

 /* ---------- spend and calls (synthetic; no provider names) ---------- */
 function callsFor(S,stage){
  const kinds={appraisal_review:[['document_analysis','extract_fields',1,0.12],['chat','extract_fields',1,0.06],...photoKeys().map(()=>['chat','analyze_photo',1,0.05])],components_review:[['chat','reconcile_rooms',1,0.04],...photoKeys().map(()=>['localization','localize_photo',1,0.02])],assets_review:[['chat','process_assets',1,0.09]]}[stage]||[];
  return kinds.map(([kind,activity,qty,cost],i)=>{const at=iso(S.clock());return {id:S.next('b'),created_at:at,kind,status:'ok',error:null,run_id:S.runs[S.runs.length-1]?.run_id||null,stage:ORDER[ORDER.indexOf(stage)-1],revision:S.revision,activity,activity_attempt:1,component:activity==='analyze_photo'||activity==='localize_photo'?'vision':activity==='extract_fields'?'extraction':'classifier',entity_type:activity.includes('photo')?'document':null,entity_id:activity.includes('photo')?S.docs.filter(d=>d.kind==='photo')[i%5]?.id||null:null,provider:'sample',endpoint:null,requested_model:'sample-model-1',model:'sample-model-1',response_id:null,request_id:null,finish_reason:kind==='chat'?'stop':null,prompt_version:kind==='chat'?'v1':null,prompt_name:kind==='chat'?activity:null,prompt_sha256:null,reasoning_effort:null,max_output_tokens:kind==='chat'?4000:null,temperature:kind==='chat'?0:null,response_format:kind==='chat'?'json_schema':null,image_count:activity.includes('photo')?1:null,image_bytes:activity.includes('photo')?100000:null,request_params:null,input_tokens:kind==='chat'?2400:0,cache_read_input_tokens:0,cache_creation_input_tokens:0,output_tokens:kind==='chat'?600:0,reasoning_output_tokens:0,quantity:kind==='chat'?null:String(qty),unit:kind==='document_analysis'?'page':kind==='localization'?'image':null,unit_price_usd:kind==='chat'?null:cost.toFixed(4),cost_usd:cost.toFixed(6),price_row:null,pricing_version:'sample',latency_ms:1500+i*120,elapsed_ms:1600+i*120,build_sha:null,request_uri:null,request_sha256:null,response_uri:null,response_sha256:null}});
 }
 function spendOut(S){
  const line=(key,calls)=>({key,calls:calls.length,errors:0,input_tokens:calls.reduce((s,c)=>s+c.input_tokens,0),cache_read_input_tokens:0,cache_creation_input_tokens:0,output_tokens:calls.reduce((s,c)=>s+c.output_tokens,0),reasoning_output_tokens:0,cost_usd:calls.reduce((s,c)=>s+Number(c.cost_usd),0).toFixed(6),latency_ms_total:calls.reduce((s,c)=>s+c.latency_ms,0),latency_ms_max:calls.length?Math.max(...calls.map(c=>c.latency_ms)):null,quantity:null});
  const group=k=>[...new Set(S.calls.map(c=>c[k]))].map(v=>line(v,S.calls.filter(c=>c[k]===v)));
  return {study_id:S.id,total:line(null,S.calls),by_stage:group('stage'),by_activity:group('activity'),by_component:group('component'),by_model:group('model'),by_kind:group('kind')};
 }

 /* ---------- corrections ---------- */
 // One handler per correction type: validate (errors as [where, detail, type]) and apply.
 const CORRECTORS={};
 const err=(where,detail,type='correction_value')=>({where,detail,type});
 const findRoom=(S,id)=>S.rooms.find(r=>r.id===id);
 const findItem=(S,id)=>{for(const r of S.rooms){const i=r.inventory.find(x=>x.component_id===id);if(i)return {room:r,item:i}}return null};
 const onlyKeys=(after,keys)=>Object.keys(after).filter(k=>!keys.includes(k));
 CORRECTORS.extraction_field={
  validate(c,S){const f=S.fields.find(x=>x.id===c.entity_id);if(!f)return [err('entity_id','No appraisal field with this id in the decided revision.','correction_target')];const keys=Object.keys(c.after);if(keys.length!==1||!['value_text','value_num','value_date'].includes(keys[0]))return [err('after','Send exactly one of value_text, value_num or value_date.')];const v=c.after[keys[0]];if(keys[0]==='value_num'&&!Number.isFinite(Number(v)))return [err('after','value_num must be a number.')];if(keys[0]==='value_date'&&!/^\d{4}-\d{2}-\d{2}$/.test(String(v)))return [err('after','value_date must be YYYY-MM-DD.')];return []},
  apply(c,S,ctx){const f=S.fields.find(x=>x.id===c.entity_id),k=Object.keys(c.after)[0],before={[k]:f[k]};for(const x of ['value_text','value_num','value_date'])f[x]=null;f[k]=k==='value_num'?String(c.after[k]):String(c.after[k]);f.source='manual';f.provenance=ctx.prov(before,{[k]:f[k]},[k])},
 };
 CORRECTORS.extraction_field_add={
  validate(c,S){if(c.entity_id!==S.extractionId)return [err('entity_id','The entity is the extraction of the decided revision.','correction_target')];if(blank(c.reason))return [err('reason','A reason is required.','correction_reason')];const a=c.after;if(blank(a.key)||!/^[a-z_]+\.[a-z_]+$/.test(a.key))return [err('after','after.key is a field key such as improvements.units.')];if(S.fields.some(f=>f.key===a.key))return [err('after','The extraction has this field; correct it with extraction_field.','correction_field_exists')];const vals=['value_text','value_num','value_date'].filter(k=>a[k]!==undefined&&a[k]!==null);if(vals.length!==1)return [err('after','Send exactly one value.')];if(!Number.isInteger(a.page)||a.page<1||a.page>8)return [err('after','after.page is a page of the 8-page PDF.')];if(blank(a.evidence))return [err('after','after.evidence is the printed text.')];return []},
  apply(c,S,ctx){const a=c.after,[section,...rest]=a.key.split('.');S.fields.push({id:S.next(2),extraction_id:S.extractionId,document_id:docId(S,'appraisal'),pdf_document_id:docId(S,'appraisal'),key:a.key,section,field:rest.join('.'),label:null,critical:false,value_text:a.value_text??null,value_num:a.value_num!=null?String(a.value_num):null,value_date:a.value_date??null,confidence:null,source:'manual',page:a.page,regions:[],provenance:{...ctx.prov(null,a,Object.keys(a)),origin:'engineer_added'}})},
 };
 CORRECTORS.room={
  validate(c,S,ctx){
   if(!findRoom(S,c.entity_id)&&!ctx.newRooms.has(c.entity_id))return [err('entity_id','No room with this id in the decided revision.','correction_target')];
   const a=c.after;
   if('merge_into' in a){if(Object.keys(a).length!==1)return [err('after','merge_into takes no other field.')];if(a.merge_into===c.entity_id||!(findRoom(S,a.merge_into)||ctx.newRooms.has(a.merge_into)))return [err('after','merge_into is another room of the decided revision.')];return []}
   const extra=onlyKeys(a,AFTER_KEYS.room);if(extra.length||!Object.keys(a).length)return [err('after',`A room correction changes name, room_type or floor${extra.length?'; not '+extra.join(', '):''}.`)];
   if('name' in a&&blank(a.name))return [err('after','The name cannot be blank.')];
   if('room_type' in a&&!ROOM_TYPES.includes(a.room_type))return [err('after','room_type is one of '+ROOM_TYPES.join(', ')+'.')];
   if('floor' in a&&a.floor!==null&&typeof a.floor!=='string'&&!Number.isInteger(a.floor))return [err('after','floor is text, a whole number or null.')];
   return [];
  },
  apply(c,S,ctx){
   const r=findRoom(S,c.entity_id);if(!r)return;
   if('merge_into' in c.after){const into=findRoom(S,c.after.merge_into);if(!into)return;for(const p of r.photos)if(!into.photos.includes(p))into.photos.push(p);mergeInventory(into,r.inventory);r.photos=[];r.inventory=[];r.merged=true;into.provenance=ctx.prov({merged:r.id},{merge_into:into.id},['photos']);return}
   const before={name:r.name,room_type:r.room_type,floor:r.floor};for(const [k,v] of Object.entries(c.after))r[k]=k==='floor'&&v!==null?String(v):v;
   r.provenance=ctx.prov(before,clone(c.after),Object.keys(c.after));
  },
 };
 function mergeInventory(into,items){for(const i of items){const same=into.inventory.find(x=>x.label===i.label);if(same){same.count=Math.min(MAX_COUNT,same.count+i.count);same.seen_in=[...new Set([...same.seen_in,...i.seen_in])]}else into.inventory.push(i)}}
 CORRECTORS.photo_room={
  validate(c,S,ctx){const d=S.docs.find(x=>x.id===c.entity_id&&x.kind==='photo');if(!d)return [err('entity_id','No photo with this id in the study.','correction_target')];const keys=Object.keys(c.after);if(keys.length!==1||keys[0]!=='room_id'||!(findRoom(S,c.after.room_id)||ctx.newRooms.has(c.after.room_id)))return [err('after','after is exactly {room_id} naming a room of the decided revision.')];return []},
  apply(c,S){
   const doc=c.entity_id,to=findRoom(S,c.after.room_id),from=S.rooms.find(r=>r.photos.includes(doc));if(!to||from===to)return;
   if(from){from.photos=from.photos.filter(p=>p!==doc);const moving=from.inventory.filter(i=>i.seen_in.length===1&&i.seen_in[0]===doc);from.inventory=from.inventory.filter(i=>!moving.includes(i));for(const i of from.inventory)i.seen_in=i.seen_in.filter(p=>p!==doc);mergeInventory(to,moving)}
   if(!to.photos.includes(doc))to.photos.push(doc);
  },
 };
 function checkComponentAfter(a,partial){
  if(partial&&!Object.keys(a).length)return 'after names at least one field.';
  const extra=onlyKeys(a,AFTER_KEYS.component);if(extra.length)return 'Unknown field: '+extra.join(', ')+'.';
  if('label' in a&&!LABELS.includes(a.label))return 'label is a component of the taxonomy.';
  if('count' in a&&!(Number.isInteger(a.count)&&a.count>=1&&a.count<=MAX_COUNT))return `count is a whole number from 1 to ${MAX_COUNT}.`;
  if('material' in a&&a.material!==null&&!MATERIALS.includes(a.material))return 'material is one of '+MATERIALS.join(', ')+'.';
  if('attachment' in a&&a.attachment!==null&&!ATTACHMENTS.includes(a.attachment))return 'attachment is one of '+ATTACHMENTS.join(', ')+'.';
  return '';
 }
 const COMPONENT_CHECKS=[];// hooks: (correction, S) => error | null, run before the shared checks (PROPOSED: unit_cost)
 CORRECTORS.component={
  validate(c,S){if(!findItem(S,c.entity_id))return [err('entity_id','No component with this id in the decided revision.','correction_target')];for(const fn of COMPONENT_CHECKS){const e=fn(c,S,'component');if(e)return [e]}const m=checkComponentAfter(c.after,true);return m?[err('after',m)]:[]},
  apply(c,S,ctx){const {item}=findItem(S,c.entity_id),before={};for(const k of Object.keys(c.after).filter(k=>AFTER_KEYS.component.includes(k))){before[k]=item[k];item[k]=c.after[k]}if(Object.keys(before).length)item.provenance=ctx.prov(before,clone(c.after),Object.keys(c.after))},
 };
 CORRECTORS.component_remove={
  validate(c,S,ctx){const f=findItem(S,c.entity_id);if(!f)return [err('entity_id','No component with this id in the decided revision.','correction_target')];if(blank(c.reason))return [err('reason','A reason is required to remove a component.','correction_reason')];if(Object.keys(c.after).length)return [err('after','after is exactly {}.')];if(ctx.touched.get(c.entity_id)>1)return [err('entity_id','The same component is removed twice or removed and corrected.','correction_duplicate')];const removed=ctx.corrections.filter(x=>x.entity_type==='component_remove'&&findItem(S,x.entity_id)?.room===f.room).length;if(removed>=f.room.inventory.length)return [err('entity_id',"A room's last inventory row cannot be removed.",'correction_last_inventory')];return []},
  apply(c,S){const f=findItem(S,c.entity_id);if(f)f.room.inventory=f.room.inventory.filter(i=>i!==f.item)},
 };
 CORRECTORS.room_component={
  validate(c,S){if(!findRoom(S,c.entity_id))return [err('entity_id','No room with this id in the decided revision.','correction_target')];const a=c.after;if(!LABELS.includes(a.label)||!('count' in a))return [err('after','after is {label, count} with optional material and attachment.')];const m=checkComponentAfter(a,false);return m?[err('after',m)]:[]},
  apply(c,S,ctx){const r=findRoom(S,c.entity_id),a=c.after;r.inventory.push({component_id:S.next(4),label:a.label,count:a.count,material:a.material??null,attachment:a.attachment??null,confidence:null,document_id:null,seen_in:[],added:true,provenance:{...ctx.prov(null,clone(a),Object.keys(a)),origin:'engineer_added'},roomKey:r.key})},
 };
 CORRECTORS.photo={validate(c,S){return S.docs.some(d=>d.id===c.entity_id&&d.kind==='photo')?[]:[err('entity_id','No photo with this id in the study.','correction_target')]},apply(){}};
 const ASSET_CHECKS=[];// hooks like COMPONENT_CHECKS, for asset corrections
 CORRECTORS.asset={
  validate(c,S){
   const a=c.after,ids=assetsAll(S).items.map(x=>x.id);if(!ids.includes(c.entity_id))return [err('entity_id','No asset with this id in the decided revision.','correction_target')];
   for(const fn of ASSET_CHECKS){const e=fn(c,S,'asset');if(e)return [e]}
   const extra=onlyKeys(a,AFTER_KEYS.asset);if(extra.length)return [err('after','Unknown field: '+extra.join(', ')+'.')];
   if(!Object.keys(a).length)return [err('after','after names at least one field.')];
   if('bucket' in a&&!BUCKETS.includes(a.bucket))return [err('after','bucket is one of '+BUCKETS.join(', ')+'.')];
   if('recovery_period' in a&&'bucket' in a&&BUCKET_YEARS[a.bucket]!==String(a.recovery_period))return [err('after','recovery_period does not match the bucket.')];
   if('irs_section' in a&&!['1245','1250'].includes(String(a.irs_section)))return [err('after','irs_section is 1245 or 1250.')];
   const q=['quantity','quantity_unit','quantity_source'].filter(k=>k in a);
   if(q.length&&q.length!==3)return [err('after','quantity, quantity_unit and quantity_source go together.')];
   if(q.length){if(!(Number(a.quantity)>0))return [err('after','quantity is positive.')];if(!['EA','SF','LF','SY'].includes(a.quantity_unit))return [err('after','quantity_unit is EA, SF, LF or SY.')];if(blank(a.quantity_source))return [err('after','quantity_source says where it was measured.')]}
   return [];
  },
  apply(c,S,ctx){
   const cur=assetsAll(S).items.find(x=>x.id===c.entity_id),e=S.assetEdits[c.entity_id]||{},before={};
   for(const k of Object.keys(c.after).filter(k=>AFTER_KEYS.asset.includes(k))){before[k]=k==='quantity'?cur.takeoff?.quantity??null:k==='quantity_unit'?cur.takeoff?.unit??null:k==='quantity_source'?null:cur[k];e[k]=c.after[k]}
   if('bucket' in c.after&&!('recovery_period' in c.after))e.recovery_period=BUCKET_YEARS[c.after.bucket];
   if('quantity' in c.after)e.quantity=String(c.after.quantity);
   Object.assign(e,{correction_id:ctx.correctionId,review_id:ctx.reviewId,revision:S.revision+1,at:ctx.at,before,after:clone(c.after)});S.assetEdits[c.entity_id]=e;
  },
 };
 CORRECTORS.reprice={validate(){return []},apply(){}};
 CORRECTORS.structure_model={validate(){return []},apply(){}};

 /* ---------- request handling ---------- */
 class Problem{constructor(status,suffix,title,detail,code,errors){Object.assign(this,{status,suffix,title,detail,code,errors})}}
 const notFound=(detail,code=E.NOT_FOUND)=>new Problem(404,'not-found','Not Found',detail,code);
 const conflict=(suffix,detail,code)=>new Problem(409,suffix,'Conflict',detail,code);
 const invalid=(errors,code)=>new Problem(422,'validation','Unprocessable Content','The request did not pass validation; see errors.',code||(errors.length&&errors.every(e=>VALIDATION_CODE[e.type])?VALIDATION_CODE[[...new Set(errors.map(e=>e.type))].sort()[0]]:E.VALIDATION),errors);
 const fieldErr=(loc,detail,type)=>({loc,detail,type});
 const gateNotOpen=(S,code=E.GATE)=>conflict('gate-not-open',`${S.stage} revision ${S.revision} of study ${S.id} is not open for a decision yet (status ${S.status})`,code);

 function create(options={}){
  const inst={now:options.now||(()=>Date.now()),delayMs:options.delayMs??2000,fetchAsset:options.fetchAsset||null,assetBase:options.assetBase||null,seeding:false,profile:clone(PROFILE),profileVersion:1,profileUpdated:iso(T0-86400000*30),idem:new Map(),assetCache:new Map()};
  const state=STATES.includes(options.state)?options.state:'components';
  inst.state=state;
  inst.studies=STUDIES.map(spec=>seed(makeStudy(spec,inst),spec.main?state:spec.state,inst));
  return api(inst);
 }
 function seed(S,target,inst){
  inst.seeding=true;
  try{
   S.created_at=iso(S.clock());S.updated_at=S.created_at;event(S,'study.created',{actor:'engineer'});
   buildData(S);
   for(const d of S.docs){event(S,'document.uploaded',{actor:'engineer',entity_type:'document',entity_id:d.id,stage:'uploading'});audit(S,'document','uploaded',{entity_type:'document',entity_id:d.id,stage:'uploading',after:{filename:d.filename}})}
   event(S,'study.started',{actor:'engineer'});audit(S,'started','started');
   newRevision(S,{kind:'start',stage:null,actor_id:ME.id,at:iso(S.clock()),review_id:null,from_revision:null});
   startRun(S,'start');enterStage(S,'extracting',true);S.status='processing';
   openGate(S,'appraisal_review');
   const decideSeed=(decision,corrections=[])=>{const r=decide(S,inst,{stage:S.stage,revision:S.revision,decision,comment:null,corrections});if(r instanceof Problem)throw new Error('seed decision refused: '+r.detail+JSON.stringify(r.errors||[]));tick(S,inst)};
   const stop=target==='appraisal'?0:target==='rooms'?1:target==='components'?2:target==='assets'?3:target==='processing'?2:4;
   if(stop>=1)decideSeed('approve');
   if(stop>=2||target==='processing'){
    // The engineer renamed the model's "Family room" on the first pass (revision 2).
    const living=S.rooms.find(r=>r.key==='living');
    decideSeed('reject',[{entity_type:'room',entity_id:living.id,before:{name:living.name,room_type:living.room_type,floor:living.floor},after:{name:'Living room',room_type:'living_room'},reason:'The appraisal calls it the living room.'}]);
    if(target==='processing'){inst.seeding=false;decide(S,inst,{stage:S.stage,revision:S.revision,decision:'approve',comment:null,corrections:[]});S.pending.until=Infinity;return S}
    decideSeed('approve');
   }
   if(stop>=3)decideSeed('approve');
   if(stop>=4){
    decideSeed('approve');
    S.answers=clone(ANSWERS);S.answersVersion=1;audit(S,'report_answers','added',{after:clone(ANSWERS)});
    if(target==='signed'||target==='update-required'){const r=sign(S,inst,{revision:S.revision,reviewed_sha256:previewTag(S,inst),engineer:clone(PROFILE.engineer),attest:true,statement_version:1});if(r instanceof Problem)throw new Error('seed sign refused: '+r.detail)}
    if(target==='update-required'){inst.profile.phone='(555) 010-0199';inst.profileVersion++;inst.profileUpdated=iso(S.clock())}
   }
  }finally{inst.seeding=false}
  return S;
 }

 // POST /decisions
 function decide(S,inst,body){
  const errs=checkShape(body,{stage:v=>GATES.includes(v),revision:v=>Number.isInteger(v)&&v>=1,decision:v=>v==='approve'||v==='reject',comment:v=>v===null||typeof v==='string',corrections:v=>Array.isArray(v)},['stage','revision','decision']);
  if(errs.length)return invalid(errs);
  const corrections=body.corrections||[];
  corrections.forEach((c,i)=>{if(!c||typeof c!=='object'){errs.push(fieldErr(['body','corrections',i],'Input should be an object','model_type'));return}for(const k of Object.keys(c))if(!['entity_type','entity_id','before','after','reason'].includes(k))errs.push(fieldErr(['body','corrections',i,k],'Extra inputs are not permitted','extra_forbidden'));if(typeof c.entity_type!=='string'||!/^[a-z][a-z0-9_]*$/.test(c.entity_type))errs.push(fieldErr(['body','corrections',i,'entity_type'],'Field required','missing'));if(typeof c.entity_id!=='string')errs.push(fieldErr(['body','corrections',i,'entity_id'],'Field required','missing'));if(!c.after||typeof c.after!=='object'||Array.isArray(c.after))errs.push(fieldErr(['body','corrections',i,'after'],'Input should be a valid dictionary','dict_type'))});
  if(errs.length)return invalid(errs);
  if(body.stage!==S.stage||body.revision!==S.revision)return conflict('stale-state',`study ${S.id} is at ${S.stage} revision ${S.revision} (${S.status}); expected ${body.stage} revision ${body.revision}`,E.STALE);
  if(S.status!=='waiting_review')return gateNotOpen(S);
  if(body.decision==='approve'&&corrections.length)return invalid([fieldErr(['body','corrections'],'An approval takes no corrections; reject with them instead.','approval_with_corrections')]);
  const allowed=CORRECTION_TYPES[body.stage],ctx={newRooms:new Map(),touched:new Map(),corrections};
  for(const c of corrections)ctx.touched.set(c.entity_id,(ctx.touched.get(c.entity_id)||0)+1);
  const out=[];
  corrections.forEach((c,i)=>{if(!allowed.includes(c.entity_type)||!CORRECTORS[c.entity_type])out.push(fieldErr(['body','corrections',i,'entity_type'],`${body.stage} does not accept ${c.entity_type} corrections (accepted: ${allowed.join(', ')})`,'correction_type'))});
  if(out.length)return invalid(out);
  // Two passes: corrections that create entities first, so later ones may name them.
  const order=corrections.map((c,i)=>[c,i]).sort((a,b)=>(CORRECTORS[b[0].entity_type].creates?1:0)-(CORRECTORS[a[0].entity_type].creates?1:0));
  for(const [c,i] of order)for(const e of CORRECTORS[c.entity_type].validate(c,S,ctx,i)){out.push(fieldErr(['body','corrections',i,e.where],e.detail,e.type))}
  if(out.length)return invalid(out.sort((a,b)=>a.loc[2]-b.loc[2]));
  // Record the review and the corrections.
  const at=iso(S.clock()),review={id:S.next(7),study_id:S.id,stage:S.stage,revision:S.revision,decision:body.decision,reviewer_id:ME.id,comment:body.comment??null,created_at:at};
  S.reviews.push(review);const rev=S.revisions[S.revisions.length-1];rev.decisions.push({stage:review.stage,decision:review.decision,reviewer_id:ME.id,review_id:review.id,at});
  const stg=S.stages[S.stages.length-1];stg.decided_at=at;stg.decision=body.decision;stg.review_wait_ms=stg.gate_opened_at?Date.parse(at)-Date.parse(stg.gate_opened_at):null;
  event(S,'review.decided',{actor:'engineer',entity_type:'review',entity_id:review.id,after:{decision:body.decision},detail:{corrections:corrections.length}});
  audit(S,'decision',body.decision==='approve'?'approved':'rejected',{entity_type:'review',entity_id:review.id,review_id:review.id,reason:body.comment??null});
  const gate=S.stage;
  if(body.decision==='approve'){
   const [processing,nextGate]=AFTER_APPROVE[gate];
   enterStage(S,processing,true);schedule(S,inst,nextGate);
  }else{
   snapshot(S);
   const from=S.revision;
   corrections.forEach(c=>{const id=S.next(8);c._id=id});
   for(const [c] of order){
    const id=c._id,prov=(before,after,changed)=>({origin:'engineer_modified',correction_id:id,review_id:review.id,stage:gate,revision:from+1,at,by:ME.id,before,after,changed,carried_from:null});
    CORRECTORS[c.entity_type].apply(c,S,{...ctx,prov,correctionId:id,reviewId:review.id,at});
    rev.corrections[c.entity_type]=(rev.corrections[c.entity_type]||0)+1;
    event(S,'correction.recorded',{actor:'engineer',entity_type:c.entity_type,entity_id:c.entity_id,before:c.before??null,after:c.after,detail:{reason:c.reason??null}});
    audit(S,'correction',c.entity_type==='component_remove'?'removed':c.entity_type==='room_component'||c.entity_type==='extraction_field_add'||CORRECTORS[c.entity_type].creates?'added':c.after&&'merge_into' in c.after?'merged':'modified',{entity_type:c.entity_type,entity_id:c.entity_id,before:c.before??null,after:c.after,changed:Object.keys(c.after),reason:c.reason??null,review_id:review.id,correction_id:id});
    delete c._id;
   }
   // Rooms left with no photos are dropped (a merge, the last photo moved away).
   S.rooms=S.rooms.filter(r=>r.photos.length);
   S.revision=from+1;
   S.has={fields:gate!=='appraisal_review',rooms:gate==='components_review'||gate==='assets_review',objects:gate==='components_review'||gate==='assets_review',assets:false};
   newRevision(S,{kind:'rejection',stage:gate,actor_id:ME.id,at,review_id:review.id,from_revision:from});
   enterStage(S,AFTER_REJECT[gate],true);
   event(S,'run.continued',{detail:{applied_revision:S.revision}});
   activities(S,['apply_corrections']);
   schedule(S,inst,gate);
  }
  return {review:clone(review),current_stage:S.stage,revision:S.revision,corrections_recorded:corrections.length};
 }
 // A body's shape: required keys, allowed keys (additionalProperties false) and checks.
 function checkShape(body,checks,required){
  const errs=[];if(!body||typeof body!=='object'||Array.isArray(body))return [fieldErr(['body'],'Input should be a valid dictionary','dict_type')];
  for(const k of required)if(!(k in body))errs.push(fieldErr(['body',k],'Field required','missing'));
  for(const [k,v] of Object.entries(body)){if(!checks[k])errs.push(fieldErr(['body',k],'Extra inputs are not permitted','extra_forbidden'));else if(!checks[k](v))errs.push(fieldErr(['body',k],'Input is not valid','value_error'))}
  return errs;
 }
 function reopen(S,inst,body){
  const errs=checkShape(body,{stage:v=>GATES.includes(v),dry_run:v=>typeof v==='boolean'},['stage']);if(errs.length)return invalid(errs);
  if(S.status!=='completed')return conflict('study-not-completed',`study ${S.id} is at ${S.stage} revision ${S.revision} (${S.status}), not completed`,E.NOT_COMPLETED);
  const i=GATES.indexOf(body.stage),plan={stage:body.stage,from_revision:S.revision,revision:S.revision+1,recomputed:{appraisal_review:['components_processing','assets_processing','exporting'],rooms_review:['components_processing','assets_processing','exporting'],components_review:['assets_processing','exporting'],assets_review:['exporting']}[body.stage],reviews:GATES.slice(i),carried:['extractions','rooms','photo_rooms','components','photo_objects',...(i>=2?['room_inventories']:[]),...(i>=3?['assets']:[])]};
  if(body.dry_run)return {status:200,body:plan};
  snapshot(S);const from=S.revision;S.revision++;
  S.has={fields:true,rooms:true,objects:true,assets:body.stage==='assets_review'};
  newRevision(S,{kind:'reopen',stage:body.stage,actor_id:ME.id,at:iso(S.clock()),review_id:null,from_revision:from});
  enterStage(S,body.stage,true);
  event(S,'study.reopened',{actor:'engineer',detail:{from_revision:from,reason:'reopen',...plan}});audit(S,'reopened','reopened',{after:{stage:body.stage,revision:S.revision}});
  startRun(S,'reopen');activities(S,['carry_revision']);
  S.status='processing';S.pending={gate:body.stage,until:inst.now()+inst.delayMs};
  const run=S.runs[S.runs.length-1];
  return {status:202,body:{...plan,workflow_id:run.workflow_id,run_id:run.run_id,reason:'reopen'}};
 }
 function sign(S,inst,body){
  const errs=checkShape(body,{revision:v=>Number.isInteger(v)&&v>=1,reviewed_sha256:v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v),engineer:v=>validEngineer(v),attest:v=>v===true,statement_version:v=>v===1},['revision','reviewed_sha256','engineer','attest','statement_version']);
  if(errs.length)return invalid(errs);
  const st=reportState(S,inst);
  if(body.revision!==S.revision)return conflict('stale-state',`study ${S.id} is at revision ${S.revision}`,E.STALE);
  if(!st.exported)return conflict('conflict',`Revision ${S.revision} has not been exported yet: approve the assets review so the export runs.`,E.NOT_SIGNABLE);
  if(st.skipped)return conflict('conflict',`The export of revision ${S.revision} has no report: ${st.skipped.message}`,E.NOT_SIGNABLE);
  if(st.signature)return conflict('conflict',`Revision ${S.revision} is already signed.`,E.SIGNED);
  if(st.missing_for_final.some(k=>k!=='signature'))return conflict('conflict','The report needs the firm profile and the study details before it can be signed.',E.INPUTS_MISSING);
  if(body.reviewed_sha256!==previewTag(S,inst))return conflict('conflict','The report changed since the preview was opened: open the preview again and sign what you reviewed.',E.CHANGED);
  const at=iso(S.clock()),pdf=digest(`pdf:${S.id}:${S.revision}:${at}`);
  const sig={id:S.next(9),study_id:S.id,revision:S.revision,signed_by:ME.id,signed_by_name:ME.name,signed_by_email:ME.email,signed_at:at,engineer:clone(body.engineer),statement_version:1,export_sha256:S.exports[S.revision].sha256,reviewed_sha256:body.reviewed_sha256,pdf_sha256:pdf,firm_profile_version:inst.profileVersion,report_answers_version:S.answersVersion};
  S.signatures[S.revision]=sig;event(S,'report.signed',{actor:'engineer',entity_type:'signature',entity_id:sig.id});audit(S,'report_signed','signed',{entity_type:'signature',entity_id:sig.id});
  return {status:201,body:clone(sig)};
 }
 const TEXT=v=>typeof v==='string'&&v.length>=1&&v.length<=500&&/^[^\x00-\x1f\x7f-\x9f]*[^\x00-\x1f\x7f-\x9f\s][^\x00-\x1f\x7f-\x9f]*$/.test(v);
 const OPT=v=>v===null||v===undefined||TEXT(v);
 function validEngineer(v){return Boolean(v)&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).every(k=>['name','credentials','license_number','license_state','email'].includes(k))&&TEXT(v.name)&&TEXT(v.credentials)&&OPT(v.license_number)&&OPT(v.email)&&(v.license_state==null||/^[A-Z]{2}$/.test(v.license_state))}

 /* ---------- objects (the components review's photo page) ---------- */
 function objectGate(S){
  if(!(S.status==='waiting_review'&&S.stage==='components_review'))return conflict('gate-not-open','Objects change only while the study waits at components_review, on its current revision.',E.OBJ_GATE);
  if(!S.has.objects)return conflict('localization-pending','The photo outlines are still being prepared.',E.OBJ_PENDING);
  return null;
 }
 function addObject(S,doc,body){
  if(!S.docs.some(d=>d.id===doc&&d.kind==='photo'))return notFound('Photo not found.',E.OBJ_PHOTO);
  const kinds={box:{kind:v=>v==='box',label:v=>LABELS.includes(v),box:v=>validBox(v),allow_duplicate:v=>typeof v==='boolean'},point:{kind:v=>v==='point',label:v=>LABELS.includes(v),point:v=>v&&typeof v==='object'&&Object.keys(v).every(k=>k==='x'||k==='y')&&[v.x,v.y].every(n=>typeof n==='number'&&n>=0&&n<=1),allow_duplicate:v=>typeof v==='boolean'},candidate:{kind:v=>v==='candidate',candidate_id:v=>typeof v==='string',allow_duplicate:v=>typeof v==='boolean'}};
  const spec=kinds[body?.kind];if(!spec)return invalid([fieldErr(['body','kind'],"Input tag does not match any of the expected tags: 'point', 'box', 'candidate'",'union_tag_invalid')]);
  const errs=checkShape(body,spec,Object.keys(spec).filter(k=>k!=='allow_duplicate'));if(errs.length)return invalid(errs);
  const gate=objectGate(S);if(gate)return gate;
  const list=S.objects[doc],live=liveObjects(S,doc);
  let candidate=null,area=body.box;
  if(body.kind==='candidate'){candidate=live.find(o=>o.id===body.candidate_id);if(!candidate)return notFound('Object not found.',E.OBJ_NOT_FOUND);area=candidate.box}
  if(body.kind==='point'){const p=body.point,under=live.filter(o=>p.x>=o.box.x1&&p.x<=o.box.x2&&p.y>=o.box.y1&&p.y<=o.box.y2).sort((a,b)=>a.area-b.area);candidate=under.find(o=>o.label===body.label)||under[0];if(!candidate)return new Problem(422,'nothing-at-point','Unprocessable Content','Nothing is stored under this point: draw a box instead.',E.OBJ_POINT);area=candidate.box}
  const label=body.label||candidate?.label;
  if(!body.allow_duplicate){
   let best=null;for(const o of live.filter(o=>o.counted&&o.label===label)){const m=iou(o.box,area),score=Math.max(m.iou,m.smaller);if((m.iou>=0.5||m.smaller>=0.5)&&(!best||score>best[1]))best=[o,score]}
   if(best)return {status:200,body:{object:objectOut(S,doc,best[0]),created:false,duplicate_of:best[0].id,overlap:round4(best[1])}};
  }
  if(body.kind==='box'&&!candidate){const m=live.filter(o=>!o.counted&&o.label===label).map(o=>[o,iou(o.box,area).iou]).filter(x=>x[1]>=0.5).sort((a,b)=>b[1]-a[1])[0];if(m)candidate=m[0]}
  const at=iso(S.clock()),o={id:S.next(5),label,phrase:PHRASES[label]||label.replace(/_/g,' '),score:null,box:{...area},area:round4((area.x2-area.x1)*(area.y2-area.y1)),source:'manual',counted:true,derived_from:candidate?candidate.id:null,created_by:ME.id,created_at:at,deleted_at:null,deleted_by:null,mask:candidate?null:outlineMask(area),mask_source:candidate?'candidate':'sam_box_prompt',mask_reason:null};
  list.push(o);event(S,'object.added',{actor:'engineer',entity_type:'photo_object',entity_id:o.id,after:{label,box:o.box}});audit(S,'object_added','added',{entity_type:'photo_object',entity_id:o.id,after:{label,document_id:doc}});
  return {status:201,body:{object:objectOut(S,doc,o),created:true,duplicate_of:null,overlap:null}};
 }
 const validBox=v=>Boolean(v)&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).every(k=>['x1','y1','x2','y2'].includes(k))&&['x1','y1','x2','y2'].every(k=>typeof v[k]==='number'&&v[k]>=0&&v[k]<=1)&&v.x2>v.x1&&v.y2>v.y1;
 function deleteObject(S,doc,id){
  if(!S.docs.some(d=>d.id===doc&&d.kind==='photo'))return notFound('Photo not found.',E.OBJ_PHOTO);
  const o=liveObjects(S,doc).find(x=>x.id===id);if(!o)return notFound('Object not found.',E.OBJ_NOT_FOUND);
  const gate=objectGate(S);if(gate)return gate;
  if(o.source!=='manual')return conflict('not-manual',"A model instance is the photo's evidence and is not deleted.",E.OBJ_NOT_MANUAL);
  o.deleted_at=iso(S.clock());o.deleted_by=ME.id;event(S,'object.deleted',{actor:'engineer',entity_type:'photo_object',entity_id:o.id});audit(S,'object_deleted','deleted',{entity_type:'photo_object',entity_id:o.id});
  return {status:200,body:objectOut(S,doc,o)};
 }

 /* ---------- the router ---------- */
 const ROUTES=[];
 const route=(method,pattern,handler)=>ROUTES.push({method,re:new RegExp('^'+pattern.replace(/\{(\w+)\}/g,'(?<$1>[^/]+)')+'$'),handler});
 const withStudy=fn=>(ctx)=>{const S=ctx.i.studies.find(s=>s.id===ctx.p.study_id);if(!S)return notFound('Study not found.',E.STUDY_NOT_FOUND);tick(S,ctx.i);return fn(S,ctx)};
  // ?revision=: the current one, an older one's snapshot, or 404 for one not reached yet.
  const atRevision=(S,ctx,kind,current)=>{const q=ctx.q.get('revision');const r=q?Number(q):S.revision;if(!Number.isInteger(r)||r<1)return invalid([fieldErr(['query','revision'],'Input should be a valid integer','int_parsing')]);if(r>S.revision)return notFound(`The study has no revision ${r} yet.`,E.NO_REVISION);if(r<S.revision){const snap=S.snapshots[r];return kind(snap,r)}return current()};
  const page=(items,extra={})=>({...extra,items,next_cursor:null});
  const json=(status,body,headers={})=>({status,body,headers});
  function registerRoutes(){
   route('GET','/health',()=>json(200,{status:'ok',service:'basis-api',version:'sample',git_sha:'sample',env:'sample',checks:{db:{status:'ok',latency_ms:1.2},temporal:{status:'ok',latency_ms:2.4}}}));
   route('GET','/v1/me',()=>json(200,{user:{id:ME.id,email:ME.email,display_name:ME.name},email_verified:true,memberships:[{org_id:ORG_ID,org_name:FIRM.name,role:'owner'}],pending_invitations:[]}));
   route('GET','/v1/orgs/{org_id}/profile',ctx=>ctx.p.org_id!==ORG_ID?notFound('Firm not found.'):!ctx.i.profile?notFound('The firm has no report profile yet.','BASIS-REPORT-013'):json(200,{org_id:ORG_ID,profile:clone(ctx.i.profile),version:ctx.i.profileVersion,updated_at:ctx.i.profileUpdated},{etag:`"${ctx.i.profileVersion}"`}));
   route('PUT','/v1/orgs/{org_id}/profile',ctx=>{
    const I=ctx.i;if(ctx.p.org_id!==ORG_ID)return notFound('Firm not found.');
    const b=ctx.body,errs=checkShape(b,{name:TEXT,address:OPT,phone:OPT,logo_png:v=>v===null||typeof v==='string'&&v.length<=524288,engineer:v=>v===null||validEngineer(v),limiting_conditions:v=>v===null||Array.isArray(v)&&v.every(x=>typeof x==='string'&&x.trim()&&x.length<=4000)},['name']);
    if(errs.length)return invalid(errs);
    const m=ctx.h['if-match'];if(m&&m!=='*'&&m.replace(/"/g,'')!==String(I.profileVersion))return new Problem(412,'precondition-failed','Precondition Failed',`The profile is at version ${I.profileVersion}.`,E.PROFILE_412);
    I.profile={address:null,phone:null,logo_png:null,engineer:null,limiting_conditions:null,...clone(b)};I.profileVersion++;I.profileUpdated=iso(I.now());
    return json(200,{org_id:ORG_ID,profile:clone(I.profile),version:I.profileVersion,updated_at:I.profileUpdated},{etag:`"${I.profileVersion}"`});
   });
   route('GET','/v1/orgs/{org_id}/members',ctx=>ctx.p.org_id!==ORG_ID?notFound('Firm not found.'):json(200,{items:PEOPLE.map((u,i)=>({user_id:u.id,email:u.email,display_name:u.name,role:u.role,joined_at:iso(T0-86400000*(60-i*7)),last_seen_at:i===2?null:iso(T0+86400000*(7-i))}))}));
   route('GET','/v1/orgs/{org_id}/invitations',ctx=>ctx.p.org_id!==ORG_ID?notFound('Firm not found.'):json(200,{items:[{id:'5a3d0000-0000-4000-8000-000000000001',org_id:ORG_ID,org_name:FIRM.name,role:'engineer',status:'pending',invited_by_name:ME.name,invited_by_email:ME.email,created_at:iso(T0+86400000*5),expires_at:iso(T0+86400000*19),email:'casey.nguyen@example.test',created_by:ME.id,accepted_at:null,accepted_by:null,declined_at:null,revoked_at:null,revoked_by:null,delivery:'sent',email_queued_at:iso(T0+86400000*5),emailed_at:iso(T0+86400000*5+60000),email_error:null}]}));
   route('GET','/v1/studies',ctx=>{for(const S of ctx.i.studies)tick(S,ctx.i);const q=(ctx.q.get('q')||'').toLowerCase(),status=ctx.q.get('status');const items=ctx.i.studies.filter(S=>(!q||S.name.toLowerCase().includes(q))&&(!status||S.status===status)).map(studyOut).sort((a,b)=>b.updated_at.localeCompare(a.updated_at));return json(200,{items,total:items.length,next_cursor:null})});
   route('GET','/v1/studies/{study_id}',withStudy(S=>json(200,studyDetail(S))));
   route('GET','/v1/studies/{study_id}/workflow',withStudy(S=>{
    const run=S.runs[S.runs.length-1],done=S.status==='completed',gate=S.status==='waiting_review'?S.stage:null,processing=S.stage==='components_processing'&&S.status==='processing';
    return json(200,{workflow_id:run.workflow_id,run_id:run.run_id,execution_status:done?'COMPLETED':'RUNNING',status:{stage:S.stage,revision:S.revision,waiting_since:gate?S.stages[S.stages.length-1].gate_opened_at:null,pending_photos:processing?2:0,completed_photos:processing?3:5,decisions_applied:S.reviews.map(r=>r.id),stalled:false,budget_remaining_usd:Math.round((8-S.calls.reduce((s,c)=>s+Number(c.cost_usd),0))*100)/100,awaiting:gate,flagged_photos:[],needs_review_stages:gate?[gate]:[],failed_stage:null,failure:null,failure_code:null},runs:S.runs.map(clone)});
   }));
   route('GET','/v1/studies/{study_id}/documents/{document_id}/(?<what>image|content)',withStudy(async(S,ctx)=>{const d=S.docs.find(x=>x.id===ctx.p.document_id);if(!d)return notFound('Document not found.');if(ctx.p.what==='image'&&d.kind!=='photo')return notFound('Not a photo.');return {status:200,bytes:await asset(ctx.i,d.path),headers:{'content-type':d.content_type,'content-disposition':`inline; filename="${d.filename}"`,etag:`"${d.sha256}"`}}}));
   route('GET','/v1/studies/{study_id}/fields',withStudy((S,ctx)=>atRevision(S,ctx,(snap,r)=>json(200,page(snap?.fields||[],{revision:r})),()=>json(200,page(S.has.fields?fieldsOut(S):[],{revision:S.revision})))));
   route('GET','/v1/studies/{study_id}/rooms',withStudy((S,ctx)=>atRevision(S,ctx,(snap,r)=>json(200,{revision:r,...(snap?.rooms||{items:[],unplaced:[]}),next_cursor:null}),()=>json(200,{revision:S.revision,...(S.has.rooms?roomsOut(S):{items:[],unplaced:[]}),next_cursor:null}))));
   route('GET','/v1/studies/{study_id}/photos/{document_id}/objects',withStudy((S,ctx)=>{const doc=ctx.p.document_id;if(!S.docs.some(d=>d.id===doc&&d.kind==='photo'))return notFound('Photo not found.',E.OBJ_PHOTO);return atRevision(S,ctx,(snap,r)=>json(200,snap?.objects?.[doc]||{document_id:doc,revision:r,localization:null,counts:{},items:[]}),()=>json(200,objectsOut(S,doc)))}));
   route('POST','/v1/studies/{study_id}/photos/{document_id}/objects',withStudy((S,ctx)=>addObject(S,ctx.p.document_id,ctx.body)));
   route('DELETE','/v1/studies/{study_id}/photos/{document_id}/objects/{object_id}',withStudy((S,ctx)=>deleteObject(S,ctx.p.document_id,ctx.p.object_id)));
   route('GET','/v1/studies/{study_id}/assets',withStudy((S,ctx)=>atRevision(S,ctx,(snap,r)=>json(200,page(snap?.assets||[],{revision:r})),()=>{let items=S.has.assets?assetsAll(S).items.map(assetOut):[];const nr=ctx.q.get('needs_review');if(nr==='true'||nr==='false')items=items.filter(a=>a.needs_review===(nr==='true'));return json(200,page(items,{revision:S.revision}))})));
   route('GET','/v1/studies/{study_id}/evidence',withStudy((S,ctx)=>{const id=ctx.q.get('asset_id');if(!S.has.assets)return json(200,page([],{revision:S.revision}));const items=evidenceOut(S,id);if(id&&!items.length)return notFound('Asset not found.');return json(200,page(items,{revision:S.revision}))}));
   route('GET','/v1/studies/{study_id}/result',withStudy((S,ctx)=>atRevision(S,ctx,(snap,r)=>snap?.result?json(200,snap.result):notFound(`Revision ${r} has no assets.`),()=>S.has.assets?json(200,resultOf(S)):notFound(`Revision ${S.revision} has no assets yet.`))));
   route('POST','/v1/studies/{study_id}/decisions',withStudy((S,ctx)=>{const r=decide(S,ctx.i,ctx.body);return r instanceof Problem?r:json(202,r)}));
   route('POST','/v1/studies/{study_id}/reopen',withStudy((S,ctx)=>{const r=reopen(S,ctx.i,ctx.body);return r instanceof Problem?r:json(r.status,r.body)}));
   route('GET','/v1/studies/{study_id}/report/state',withStudy((S,ctx)=>json(200,reportState(S,ctx.i))));
   route('GET','/v1/studies/{study_id}/report-answers',withStudy(S=>json(200,{study_id:S.id,report_answers:clone(S.answers),report_answers_version:S.answersVersion},{etag:`"${S.answersVersion}"`})));
   route('PUT','/v1/studies/{study_id}/report-answers',withStudy((S,ctx)=>{
    const b=ctx.body,errs=checkShape(b,{taxpayer_name:TEXT,photographs:v=>v==='engineer_site_visit'||v==='taxpayer_supplied',photographed_by:TEXT,photographed_on:v=>typeof v==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(v),interviews:v=>Array.isArray(v),prior_depreciation:v=>v===null||typeof v==='object'},['taxpayer_name','photographs','photographed_by','photographed_on']);
    if(errs.length)return invalid(errs);
    const m=ctx.h['if-match'];if(m&&!(m==='*'&&!S.answers)&&m.replace(/"/g,'')!==String(S.answersVersion))return new Problem(412,'precondition-failed','Precondition Failed',`The report answers are at version ${S.answersVersion}.`,E.ANSWERS_412);
    const before=clone(S.answers);S.answers={interviews:[],prior_depreciation:null,...clone(b)};S.answersVersion++;audit(S,'report_answers',before?'modified':'added',{before,after:clone(S.answers)});event(S,'report_answers.saved',{actor:'engineer'});
    return json(200,{study_id:S.id,report_answers:clone(S.answers),report_answers_version:S.answersVersion},{etag:`"${S.answersVersion}"`});
   }));
   route('GET','/v1/studies/{study_id}/report/preview',withStudy(async(S,ctx)=>{const st=reportState(S,ctx.i);if(!st.exported||st.skipped)return conflict('conflict',`Revision ${S.revision} has no report to preview yet.`,E.NOT_SIGNABLE);return {status:200,bytes:await asset(ctx.i,REPORT_PATH),headers:{'content-type':'application/pdf','content-disposition':`inline; filename="study-${S.id.slice(0,8)}-rev-${S.revision}-report-preview.pdf"`,etag:`"${previewTag(S,ctx.i)}"`}}}));
   route('GET','/v1/studies/{study_id}/report',withStudy(async(S,ctx)=>{
    const fmt=ctx.q.get('format')||'pdf';if(!['pdf','csv','xlsx'].includes(fmt))return invalid([fieldErr(['query','format'],"Input should be 'pdf', 'csv' or 'xlsx'",'enum')]);
    const rq=ctx.q.get('revision'),rev=rq?Number(rq):Object.keys(S.exports).map(Number).filter(r=>!S.exports[r].skipped).sort((a,b)=>b-a)[0];
    if(!rev||!S.exports[rev]||S.exports[rev].skipped)return notFound('No export for this study and revision.',E.REPORT_NOT_FOUND);
    if(fmt==='xlsx')return notFound('The sample API writes the schedule as CSV only.',E.REPORT_NOT_FOUND);
    if(fmt==='csv'){const r=rev===S.revision?resultOf(S):S.snapshots[rev]?.result;const rows=[['asset_id','category','bucket','rcn','rcnld','allocated'],...(r?.allocation?.assets||[]).map(a=>[a.asset_id,a.category,a.bucket,a.rcn,a.rcnld,a.allocated])];return {status:200,bytes:new TextEncoder().encode(rows.map(x=>x.map(v=>/[",]/.test(String(v))?`"${String(v).replace(/"/g,'""')}"`:v).join(',')).join('\n')+'\n'),headers:{'content-type':'text/csv; charset=utf-8','content-disposition':`attachment; filename="study-${S.id.slice(0,8)}-rev-${rev}-schedule.csv"`}}}
    const signed=Boolean(S.signatures[rev])&&ctx.q.get('variant')!=='draft';
    return {status:200,bytes:await asset(ctx.i,REPORT_PATH),headers:{'content-type':'application/pdf','content-disposition':`attachment; filename="study-${S.id.slice(0,8)}-rev-${rev}-report${signed?'-signed':''}.pdf"`}};
   }));
   route('POST','/v1/studies/{study_id}/report/sign',withStudy((S,ctx)=>{const r=sign(S,ctx.i,ctx.body);return r instanceof Problem?r:json(r.status,r.body)}));
   route('GET','/v1/studies/{study_id}/revisions',withStudy(S=>json(200,revisionsOut(S))));
   route('GET','/v1/studies/{study_id}/timeline',withStudy((S,ctx)=>{const t=ctx.q.get('event_type');return json(200,{study_id:S.id,items:S.events.filter(e=>!t||e.event_type===t).slice().reverse().map(clone),next_cursor:null})}));
   route('GET','/v1/studies/{study_id}/timeline/steps',withStudy(S=>json(200,{study_id:S.id,steps:S.steps.map(clone),stages:S.stages.map(clone)})));
   route('GET','/v1/studies/{study_id}/audit',withStudy(S=>json(200,{study_id:S.id,items:S.audit.slice().reverse().map(clone),next_cursor:null})));
   route('GET','/v1/studies/{study_id}/spend',withStudy(S=>json(200,spendOut(S))));
   route('GET','/v1/studies/{study_id}/calls',withStudy((S,ctx)=>{const a=ctx.q.get('activity'),k=ctx.q.get('kind');return json(200,{items:S.calls.filter(c=>(!a||c.activity===a)&&(!k||c.kind===k)).slice().reverse().map(clone),next_cursor:null})}));
   route('GET','/v1/cost-indices',ctx=>{const items=[[2020,'100.0'],[2024,'118.9'],[2025,'121.0'],[2026,'123.4']].map(([year,value],i)=>({id:`5a3b0000-0000-4000-8000-00000000${year}`,source:'sample',series:'historical',year,region:'national',value,as_of:`${year}-07-01`,citation:`Sample cost index (illustrative), ${year}`,url:null,derived:null,version:1,obtained_at:iso(T0-86400000*(40-i)),obtained_by:null,obtained_by_name:'Sample loader',document_sha256:null,run_id:'5a3b0000-0000-4000-8000-0000000000aa',detail:null}));items.push({id:'5a3b0000-0000-4000-8000-000000000ca1',source:'sample',series:'city',year:2020,region:'CA',value:'1.180',as_of:'2020-07-01',citation:'Sample location factors (illustrative), California',url:null,derived:null,version:1,obtained_at:iso(T0-86400000*40),obtained_by:null,obtained_by_name:'Sample loader',document_sha256:null,run_id:'5a3b0000-0000-4000-8000-0000000000aa',detail:null});const y=ctx.q.get('year'),s=ctx.q.get('series');return json(200,{items:items.filter(x=>(!y||String(x.year)===y)&&(!s||x.series===s)),next_cursor:null})});
   route('GET','/v1/cost-indices/runs',()=>json(200,{items:[{id:'5a3b0000-0000-4000-8000-0000000000aa',source:'sample',trigger:'manual',actor_type:'system',actor_id:null,actor_name:'Sample loader',status:'succeeded',started_at:iso(T0-86400000*40),finished_at:iso(T0-86400000*40+4000),rows_added:5,years_wanted:[2020,2024,2025,2026],years_added:[2020,2024,2025,2026],failures:[],document_sha256:null,detail:null}]}));
   PROPOSED_ROUTES.forEach(fn=>fn({route,json,withStudy,notFound,conflict,invalid,fieldErr,Problem,E,objectGate,objectOut,liveObjects,iso,clone,TEXT,blank,event,audit}));
  }
  async function asset(I,path){
   if(I.assetCache.has(path))return I.assetCache.get(path);
   const load=(async()=>{if(I.fetchAsset)return new Uint8Array(await I.fetchAsset(path));const base=I.assetBase||globalThis.document?.baseURI||globalThis.location?.href;const r=await fetch(new URL(path,base));if(!r.ok)throw new Error(`${path}: ${r.status}`);return new Uint8Array(await r.arrayBuffer())})();
   I.assetCache.set(path,load);load.catch(()=>I.assetCache.delete(path));return load;
  }
 function api(inst){
  if(!ROUTES.length)registerRoutes();
  let rid=0;
  function problemBody(p,path){const b={type:PROBLEM+p.suffix,title:p.title,status:p.status,detail:p.detail,instance:path,request_id:'sample-'+(++rid)};if(p.errors)b.errors=p.errors;if(p.code)b.code=p.code;return b}
  async function handle(method,url,headers,rawBody){
   const u=new URL(url,BASE),path=u.pathname,h={};for(const [k,v] of Object.entries(headers||{}))h[k.toLowerCase()]=String(v);
   if(!/^Bearer \S+/.test(h.authorization||'')&&path!=='/health')return {status:401,problem:new Problem(401,'unauthorized','Unauthorized','No `Authorization: Bearer` header.','BASIS-API-001'),path};
   let body;if(rawBody!==undefined&&rawBody!==null&&rawBody!==''){try{body=JSON.parse(rawBody)}catch{return {problem:invalid([fieldErr(['body'],'JSON decode error','json_invalid')]),path}}}
   const match=ROUTES.map(r=>({r,m:r.method===method&&r.re.exec(path)})).find(x=>x.m);
   if(!match){const anyMethod=ROUTES.some(r=>r.re.test(path));return {problem:anyMethod?new Problem(405,'method-not-allowed','Method Not Allowed','Method Not Allowed','BASIS-API-025'):notFound('Not Found'),path}}
   // Idempotency-Key: a retry with the same key and body replays the first answer.
   const key=h['idempotency-key'];
   if(key&&(method==='POST'||method==='DELETE')){const hit=inst.idem.get(key);if(hit&&hit.fingerprint===method+path+(rawBody||''))return {...hit.result,replayed:true}}
   let out;
   try{out=await match.r.handler({p:match.m.groups||{},q:u.searchParams,h,body,i:inst,method,path})}catch(e){return {status:500,problem:new Problem(500,'internal','Internal Server Error','The sample API failed: '+(e?.message||e),'BASIS-API-024'),path}}
   const result=out instanceof Problem?{problem:out,path}:{...out,path};
   if(key&&(method==='POST'||method==='DELETE')&&!(out instanceof Problem&&out.status===422))inst.idem.set(key,{fingerprint:method+path+(rawBody||''),result});
   return result;
  }
  function toResponse(res){
   const headers={'x-request-id':'sample-'+(++rid),...(res.headers||{})};
   if(res.problem){const b=problemBody(res.problem,res.path);return new Response(JSON.stringify(b),{status:res.problem.status,headers:{...headers,'content-type':'application/problem+json'}})}
   if(res.bytes)return new Response(res.bytes,{status:res.status,headers});
   return new Response(JSON.stringify(res.body),{status:res.status,headers:{...headers,'content-type':'application/json'}});
  }
  return {
   active:true,base:BASE,
   async fetch(url,init={}){
    if(init.signal?.aborted)throw Object.assign(new Error('The operation was aborted.'),{name:'AbortError'});
    const headers=init.headers instanceof Headers?Object.fromEntries(init.headers.entries()):init.headers||{};
    return toResponse(await handle((init.method||'GET').toUpperCase(),String(url),headers,init.body));
   },
   // The raw answer (status, body or problem) for tests.
   async call(method,path,{body,headers={}}={}){const res=await handle(method,BASE+path,{Authorization:'Bearer sample-token',...headers},body===undefined?undefined:JSON.stringify(body));if(res.problem)return {status:res.problem.status,body:problemBody(res.problem,res.path),headers:res.headers||{}};return {status:res.status,body:res.bytes?res.bytes:res.body,headers:res.headers||{}}},
   get state(){return inst.state},
   studies:()=>inst.studies.map(S=>({id:S.id,name:S.name,status:S.status,stage:S.stage,revision:S.revision})),
   mainStudyId:()=>inst.studies[0].id,
   reset(name){const fresh=create({now:inst.now,delayMs:inst.delayMs,fetchAsset:inst.fetchAsset,assetBase:inst.assetBase,state:name||inst.state});Object.assign(inst,fresh._inst);return this},
   _inst:inst,
  };
 }

 /* =====================================================================================
  * PROPOSED: not in the real API. Three corrections the backend does not offer yet
  * (web/FRONTEND_BACKLOG.md, "Engineer overrides"; .ui-climb/override-contract.md P0-P3).
  * Deleting from here to "END PROPOSED" removes them; everything above answers as the
  * real API does (no /v1/capabilities: the client then shows none of the three).
  *
  *  P0  GET /v1/capabilities -> {object_exclusion, unit_cost_override, room_create}
  *  P1  PATCH .../photos/{doc}/objects/{id} {counted:false, reason} | {counted:true}; every
  *      object carries `exclusion` (null when counted).
  *  P2  unit_cost (+ unit_cost_source, reason) on asset and component corrections; the asset
  *      then reports cost.unit_cost = the override and cost.override.
  *  P3  room_create at rooms_review; photo_room corrections in the same decision may name it.
  * ===================================================================================== */
 const PROPOSED_ROUTES=[];
 (function proposed(){
  // P0
  PROPOSED_ROUTES.push(({route,json})=>route('GET','/v1/capabilities',()=>json(200,{object_exclusion:true,unit_cost_override:true,room_create:true})));
  // P1: exclusion on every object; PATCH excludes or restores.
  OBJECT_OUT.push((out,o)=>{out.exclusion=o.exclusion?{...o.exclusion}:null});
  PROPOSED_ROUTES.push(({route,json,withStudy,notFound,invalid,fieldErr,E,objectGate,objectOut,liveObjects,iso,TEXT,blank,event,audit})=>route('PATCH','/v1/studies/{study_id}/photos/{document_id}/objects/{object_id}',withStudy((S,ctx)=>{
   const doc=ctx.p.document_id;if(!S.docs.some(d=>d.id===doc&&d.kind==='photo'))return notFound('Photo not found.',E.OBJ_PHOTO);
   const o=liveObjects(S,doc).find(x=>x.id===ctx.p.object_id);if(!o)return notFound('Object not found.',E.OBJ_NOT_FOUND);
   const b=ctx.body,errs=[];
   if(!b||typeof b!=='object'||Array.isArray(b))errs.push(fieldErr(['body'],'Input should be a valid dictionary','dict_type'));
   else{for(const k of Object.keys(b))if(!['counted','reason'].includes(k))errs.push(fieldErr(['body',k],'Extra inputs are not permitted','extra_forbidden'));if(typeof b.counted!=='boolean')errs.push(fieldErr(['body','counted'],'Field required','missing'));if(b.counted===false&&(blank(b.reason)||b.reason.length>500))errs.push(fieldErr(['body','reason'],'A reason of 1 to 500 characters is required to exclude an instance.','value_error'));if(b.counted===true&&b.reason!==undefined&&b.reason!==null&&(typeof b.reason!=='string'||b.reason.length>500))errs.push(fieldErr(['body','reason'],'The reason is at most 500 characters.','value_error'))}
   if(errs.length)return invalid(errs);
   const gate=objectGate(S);if(gate)return gate;
   if(b.counted===false){o.counted=false;o.exclusion={by:'sample-user-1',by_name:'Alex Morgan',at:iso(S.clock()),reason:b.reason.trim()}}
   else{o.counted=true;o.exclusion=null}
   event(S,b.counted?'object.restored':'object.excluded',{actor:'engineer',entity_type:'photo_object',entity_id:o.id,detail:{reason:b.reason??null}});
   return json(200,objectOut(S,doc,o));
  })));
  // P2: a unit price override on asset and component corrections.
  AFTER_KEYS.component.push('unit_cost','unit_cost_source');AFTER_KEYS.asset.push('unit_cost','unit_cost_source');
  const checkPrice=c=>{
   const a=c.after;if(!('unit_cost' in a)&&!('unit_cost_source' in a))return null;
   const e=detail=>({where:'after',detail,type:'correction_value'});
   if(a.unit_cost!==null&&!(typeof a.unit_cost==='number'&&a.unit_cost>0&&a.unit_cost<=10000000))return e('unit_cost is a number above 0 and at most 10,000,000 (USD per takeoff unit), or null to clear an override.');
   if(a.unit_cost!==null&&(typeof a.unit_cost_source!=='string'||!a.unit_cost_source.trim()||a.unit_cost_source.length>300))return e('unit_cost_source (1 to 300 characters) says where the price comes from.');
   if(a.unit_cost===null&&'unit_cost_source' in a&&a.unit_cost_source!==null)return e('unit_cost_source goes with a price.');
   if(blank(c.reason))return {where:'reason',detail:'A reason is required for a unit price override.',type:'correction_reason'};
   return null;
  };
  COMPONENT_CHECKS.push(checkPrice);ASSET_CHECKS.push(checkPrice);
  const keepPrice=(store,key,c,ctx)=>{if(!('unit_cost' in c.after))return;if(c.after.unit_cost===null){delete store[key];return}store[key]={unit_cost:c.after.unit_cost,source:c.after.unit_cost_source.trim(),reason:c.reason.trim(),by:'sample-user-1',by_name:'Alex Morgan',at:ctx.at}};
  const baseComponent=CORRECTORS.component.apply,baseAsset=CORRECTORS.asset.apply;
  CORRECTORS.component.apply=(c,S,ctx)=>{baseComponent(c,S,ctx);keepPrice(S.compPrices,c.entity_id,c,ctx)};
  CORRECTORS.asset.apply=(c,S,ctx)=>{baseAsset(c,S,ctx);keepPrice(S.compPrices,'asset:'+c.entity_id,c,ctx)};
  COST_HOOKS.push((asset,{S,componentId,id,quantity})=>{
   const o=S.compPrices['asset:'+id]||S.compPrices[componentId];if(!o)return;
   const unit=Math.round(o.unit_cost*100),total=Math.round(unit*quantity);
   Object.assign(asset.cost,{unit_cost:money(unit),total:money(total),override:{...o}});
   Object.assign(asset.cost.trail,{kind:'unit_price',unit_cost:money(unit),total:money(total),missing:null,rounding:'cent'});
   asset.needs_review=false;asset.review_reasons=[];asset.review_codes=[];
  });
  // P3: room_create at rooms_review.
  CORRECTION_TYPES.rooms_review.push('room_create');
  CORRECTORS.room_create={creates:true,
   validate(c,S,ctx){
    const a=c.after;if(!c.entity_id||S.rooms.some(r=>r.id===c.entity_id)||ctx.newRooms.has(c.entity_id))return [err('entity_id','entity_id is a new client-generated id, once per decision.','correction_duplicate')];
    if(c.before!==null&&c.before!==undefined)return [err('after','before is null for a new room.')];
    const extra=Object.keys(a).filter(k=>!['name','room_type','floor'].includes(k));if(extra.length)return [err('after','Unknown field: '+extra.join(', ')+'.')];
    if(blank(a.name)||a.name.length>200)return [err('after','The room needs a name.')];
    if(!ROOM_TYPES.includes(a.room_type))return [err('after','room_type is one of '+ROOM_TYPES.join(', ')+'.')];
    if(a.floor!==undefined&&a.floor!==null&&!Number.isInteger(a.floor)&&typeof a.floor!=='string')return [err('after','floor is a whole number or null.')];
    ctx.newRooms.set(c.entity_id,a);return [];
   },
   apply(c,S,ctx){S.rooms.push({id:c.entity_id,key:'new-'+c.entity_id.slice(0,8),name:c.after.name.trim(),room_type:c.after.room_type,floor:c.after.floor==null?null:String(c.after.floor),area:null,source:'engineer',group_key:null,photos:[],inventory:[],provenance:{...ctx.prov(null,clone(c.after),Object.keys(c.after)),origin:'engineer_added'}})},
  };
 })();
 /* END PROPOSED ====================================================================== */

 /* ---------- the signed-in stub (same surface as live/auth.js) ---------- */
 function authStub(){
  const U={uid:ME.id,email:ME.email,displayName:ME.name,emailVerified:true,getIdToken:async()=>'sample-token'};
  let user=U;const listeners=new Set();const notify=()=>listeners.forEach(fn=>{try{fn(user)}catch{}});
  return {init:async()=>{notify();return null},signInWithGoogle:async()=>{user=U;notify();return U},signInWithEmail:async()=>{user=U;notify();return U},signOut:async()=>{user=null;notify()},getToken:async()=>user?'sample-token':null,onChange(fn){listeners.add(fn);return ()=>listeners.delete(fn)},get user(){return user},get redirectError(){return ''},describe:e=>e?.message||'Sign-in failed.'};
 }

 const exported={create,authStub,STATES,BASE};
 if(typeof module==='object'&&module.exports){module.exports=exported;return}
 const q=new URLSearchParams(scope.location?.search||'');
 if(q.get('api')!=='sample'){scope.BasisSampleApi={active:false};return}
 const instance=create({state:q.get('sample')||'components'});
 scope.BasisSampleApi=instance;
 scope.BasisAuth=authStub();
})(typeof globalThis==='object'?globalThis:this);
