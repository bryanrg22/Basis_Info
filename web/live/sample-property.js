/* The sample API's property routes (PROPOSED; web/live/sample-api.js registers them from its
 * PROPOSED section, so deleting that section removes them):
 *
 *   GET  /v1/studies/{id}/property                       the pin, the newest capture, enabled
 *   PUT  /v1/studies/{id}/property/pin                   If-Match on the pin's version
 *   POST /v1/studies/{id}/property/captures              202 queued | 200 recent | 409 in progress | 422 no pin
 *   GET  /v1/studies/{id}/property/captures[/{capture}]
 *   GET  /v1/studies/{id}/property/views/{view}/image
 *   GET  /v1/studies/{id}/property/public                imagery, parcels, site check, lidar
 *   POST /v1/studies/{id}/property/public
 *   GET  /v1/studies/{id}/property/public/views/{view}/image
 *   GET  /v1/studies/{id}/renovations, PUT with If-Match   the aerial brackets (projects kept as sent)
 *
 * Shapes as docs/api/PROPERTY_CAPTURE.md, PUBLIC_GEO.md and RENOVATIONS.md say (on the open
 * PRs #180 and #181, the public geo sources branch, PR #183).
 *
 * Everything is synthetic. The pin is in open water (43.5 N, 87.0 W, in Lake Michigan): it is
 * no one's property, and its latitude is one where the API's leaf-off rule applies (37 to 49.5
 * N). The address is the sample's own Harbor House. The pictures are drawn here, as SVG, from
 * a made-up site plan in metres (house, garage, drive, walk, pool, fence, trees, a patio built
 * between two of the dated views), at each view's exact pixel size, so a shape traced on them
 * measures what was drawn: the patio is 20 x 40 ft, the back-yard fence 210 ft.
 *
 * &measure=draft puts a model's draft of the patio on its row (patioDraft, sample-api.js P10).
 *
 * &property= ready (default: the search's pin, not confirmed yet) | confirmed | running |
 * paused | waiting | failed | noplace (the address found no place) | nopin | site (the appraisal
 * prints a site area that disagrees with the parcel).
 */
(function(scope){
 'use strict';
 const PM=typeof module==='object'&&module.exports?require('./property-model.js'):scope.BasisPropertyModel;
 const HOUSE={lat:43.5,lon:-87.0};
 const ADDRESS='1200 Harbor Boulevard, Ventura, CA 93001';
 const SOURCE='Aerial imagery viewed online; date as the imagery shows it';
 const DAY=86400000,MIN=60000;
 const W=1440,H=900,CLOSE=0.0841,WIDE=0.2447;
 const FT=0.3048;

 /* ---------- the made-up site, in metres east and north of the house's centre ---------- */
 const PATIO_BUILT='2024-01-15';// between the 2023-07-30 and 2024-04-09 views
 const SITE={
  lot:[[-16,-24],[16,-24],[16,20],[-16,20]],
  side:[[16,-24],[27,-24],[27,20],[16,20]],// the owner's adjacent strip
  house:[[-7,-5],[5,-5],[5,7],[-7,7]],
  garage:[[5,-5],[12,-5],[12,2],[5,2]],
  drive:[[5.5,-24],[11.5,-24],[11.5,-5],[5.5,-5]],
  walk:[[-2.2,-24],[-0.8,-24],[-0.8,-5],[-2.2,-5]],
  patio:[[-8,8.5],[-8+40*FT,8.5],[-8+40*FT,8.5+20*FT],[-8,8.5+20*FT]],// 40 x 20 ft
  pool:[[6,9],[12,9],[12,14],[6,14]],
  fence:[[-16,4],[-16,20],[16,20],[16,4]],// 16 + 32 + 16 m = 64 m = 210 ft
  trees:[[-12,-15,3.4],[12.5,16.5,3],[-13,15,2.6],[-11,0,2.2]],
 };
 const toLonLat=(e,n,at=HOUSE)=>{const [m,N]=PM.radii(at.lat);return [at.lon+e/(N*Math.cos(at.lat*Math.PI/180))*180/Math.PI,at.lat+n/m*180/Math.PI]};
 const toMetres=(lon,lat)=>{const [m,N]=PM.radii(HOUSE.lat);return [(lon-HOUSE.lon)*Math.PI/180*N*Math.cos(HOUSE.lat*Math.PI/180),(lat-HOUSE.lat)*Math.PI/180*m]};

 /* ---------- drawing ---------- */
 const LEAF={on:{tree:'#3f6f3a',lawn:'#7fa65a',edge:'#2f5a2b'},off:{tree:'#8b7a63',lawn:'#a3a07a',edge:'#6b5e4b'},mid:{tree:'#6b7d45',lawn:'#93a46a',edge:'#4f5d33'}};
 const leafOf=d=>{if(!d||/-12-31$/.test(d))return 'mid';const m=Number(d.slice(5,7));return m>=11||m<=4?'off':m>=6&&m<=9?'on':'mid'};
 // An aerial (north up) of the site, centred on (ce, cn) metres, at s metres a pixel.
 function aerialSvg({w,h,s,ce,cn,date,coarse=0,wide=false}){
  const X=e=>(w/2+(e-ce)/s).toFixed(2),Y=n=>(h/2-(n-cn)/s).toFixed(2);
  const poly=(pts,fill,extra='')=>`<polygon points="${pts.map(([e,n])=>X(e)+','+Y(n)).join(' ')}" fill="${fill}" ${extra}/>`;
  const leaf=LEAF[leafOf(date)],patio=!date||date>=PATIO_BUILT;
  const parts=[`<rect width="${w}" height="${h}" fill="${leaf.lawn}"/>`];
  // Streets: the house's (south) and a grid around it on the wide view.
  const streets=wide?[-29,-120,62,153].map(n=>poly([[-400,n-5],[400,n-5],[400,n+5],[-400,n+5]],'#6d6f70')).join('')+[-70,58,-198,186].map(e=>poly([[e-5,-400],[e+5,-400],[e+5,400],[e-5,400]],'#6d6f70')).join(''):poly([[-200,-34],[200,-34],[200,-24.5],[-200,-24.5]],'#6d6f70')+`<line x1="${X(-200)}" y1="${Y(-29.2)}" x2="${X(200)}" y2="${Y(-29.2)}" stroke="#d9c76a" stroke-width="${Math.max(1,0.15/s)}" stroke-dasharray="${3/s},${3/s}"/>`;
  parts.push(streets);
  // Neighbours: plain roofs on their own lots.
  const neighbours=[];for(const de of wide?[-128,-64,0,64,128]:[-64,64])for(const dn of wide?[-91,0,91]:[0]){if(!de&&!dn)continue;const k=Math.abs(de*7+dn*3)%5;neighbours.push(poly([[de-6-k/2,dn-6],[de+6,dn-6],[de+6,dn+7+k/3],[de-6-k/2,dn+7+k/3]],['#8c8f93','#9b8f84','#7f8a8e','#a09a90','#8a8580'][k],'stroke="#5e6266" stroke-width="1"'),poly([[de+7,dn-6-12],[de+11,dn-6-12],[de+11,dn-6],[de+7,dn-6]],'#bdbdb6'))}
  parts.push(neighbours.join(''));
  parts.push(poly(SITE.drive,'#b9b6ae'),poly(SITE.walk,'#cfcabf'));
  if(patio)parts.push(poly(SITE.patio,'#c9b79a','stroke="#9e8c70" stroke-width="1"'));
  parts.push(poly(SITE.pool,'#5fa9c9','stroke="#e7e2d6" stroke-width="'+Math.max(1,0.5/s)+'"'));
  parts.push(poly(SITE.house,'#7d5b4b','stroke="#4b3a31" stroke-width="1.5"'),`<line x1="${X(-7)}" y1="${Y(1)}" x2="${X(5)}" y2="${Y(1)}" stroke="#5e463a" stroke-width="${Math.max(1,0.25/s)}"/>`,poly(SITE.garage,'#8d6b59','stroke="#4b3a31" stroke-width="1.5"'));
  parts.push(`<polyline points="${SITE.fence.map(([e,n])=>X(e)+','+Y(n)).join(' ')}" fill="none" stroke="#efe9dc" stroke-width="${Math.max(1.2,0.12/s)}"/>`);
  for(const [e,n,r] of SITE.trees)parts.push(`<circle cx="${X(e)}" cy="${Y(n)}" r="${(r/s).toFixed(1)}" fill="${leaf.tree}" stroke="${leaf.edge}" stroke-width="2" ${leafOf(date)==='off'?'fill-opacity="0.55"':''}/>`);
  const blur=coarse?`<filter id="c"><feGaussianBlur stdDeviation="${coarse}"/></filter>`:'';
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${blur}<g ${coarse?'filter="url(#c)"':''}>${parts.join('')}</g></svg>`;
 }
 // A side view: the face of the house seen from one side, tilted (not to scale: never measured on).
 function sideSvg(label){
  const k={'side-south':0,'side-west':1,'side-north':2,'side-east':3}[label]||0,wide=[720,560,720,560][k],wall=['#d8cfc2','#cfc7ba','#d3cbbf','#d1c9bc'][k];
  const x0=(W-wide)/2,y0=330,roof=`<polygon points="${x0-30},${y0} ${x0+wide/2},${y0-170} ${x0+wide+30},${y0}" fill="#6e5245" stroke="#4b3a31" stroke-width="3"/>`;
  const windows=[];for(let i=0;i<(k%2?3:4);i++){const x=x0+60+i*(wide-120)/((k%2?3:4)-1)-35;windows.push(`<rect x="${x}" y="${y0+60}" width="70" height="80" fill="#7f99a8" stroke="#f4f1ea" stroke-width="6"/><rect x="${x}" y="${y0+200}" width="70" height="80" fill="#7f99a8" stroke="#f4f1ea" stroke-width="6"/>`)}
  const door=k===0?`<rect x="${W/2-40}" y="${y0+230}" width="80" height="140" fill="#4b3a31"/>`:'';
  const garage=k===0?`<rect x="${x0+wide+10}" y="${y0+150}" width="220" height="220" fill="${wall}" stroke="#a79f92" stroke-width="3"/><rect x="${x0+wide+35}" y="${y0+210}" width="170" height="160" fill="#e9e5dc" stroke="#a79f92" stroke-width="2"/>`:'';
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#b9cfdc"/><stop offset="1" stop-color="#e5ece9"/></linearGradient></defs><rect width="${W}" height="${H}" fill="url(#sky)"/><polygon points="0,${y0+370} ${W},${y0+330} ${W},${H} 0,${H}" fill="#86a865"/>${k===0?`<polygon points="${x0+wide+40},${H} ${x0+wide+60},${y0+370} ${x0+wide+190},${y0+370} ${x0+wide+260},${H}" fill="#b9b6ae"/>`:''}<rect x="${x0}" y="${y0}" width="${wide}" height="370" fill="${wall}" stroke="#a79f92" stroke-width="3"/>${roof}${windows.join('')}${door}${garage}<circle cx="${x0-120}" cy="${y0+180}" r="150" fill="#4f7a43"/><rect x="${x0-130}" y="${y0+300}" width="20" height="80" fill="#5b4636"/></svg>`;
 }

 /* ---------- the study's property state ---------- */
 const id=(n,k)=>`5a3f${String(k).padStart(4,'0')}-0000-4000-8000-${String(n).padStart(12,'0')}`;
 const HISTORY=[['2026-06-18','leaf-on'],['2025-11-20','leaf-off'],['2024-04-09','leaf-off'],['2023-07-30','leaf-on'],['2021-12-31','unknown'],['2019-05-02','unknown']];
 const SIDES=[['side-south',0],['side-west',90],['side-north',180],['side-east',270]];
 const m2ft=d=>{const [y,m,day]=d.split('-');return `${Number(m)}/${Number(day)}/${y}`};
 function viewsFor(S,cap,pinVersion,at,center){
  const t=iso=>iso,base={lat:center.lat,lon:center.lon,ground_alt_m:177.2,width:W,height:H,source:SOURCE,captured_at:at,pin_version:pinVersion,camera_lat:null,camera_lon:null,green_share:null};
  const out=[];let n=0;
  const add=(o)=>{out.push({...base,id:id(++n,cap.seq*10+1),sha256:null,...o});};
  add({kind:'aerial',label:'aerial-close',range_m:165.3,fov_deg:30,heading_deg:0,tilt_deg:0,pixel_size_m:CLOSE,imagery_date:m2ft('2026-06-18'),imagery_on:'2026-06-18',imagery_date_note:'status-bar',leaf:'leaf-on',green_share:0.41});
  add({kind:'aerial',label:'aerial-wide',range_m:481.0,fov_deg:30,heading_deg:0,tilt_deg:0,pixel_size_m:WIDE,imagery_date:m2ft('2026-06-18'),imagery_on:'2026-06-18',imagery_date_note:'status-bar',leaf:'leaf-on',green_share:0.38});
  for(const [label,heading] of SIDES)add({kind:'oblique',label,range_m:206.7,fov_deg:30,heading_deg:heading,tilt_deg:60,pixel_size_m:null,imagery_date:m2ft('2026-06-18'),imagery_on:'2026-06-18',imagery_date_note:'status-bar',leaf:'leaf-on'});
  for(const [d,leaf] of HISTORY){const yearOnly=/-12-31$/.test(d);add({kind:'history',label:'history-'+d,range_m:165.3,fov_deg:30,heading_deg:0,tilt_deg:0,pixel_size_m:CLOSE,imagery_date:yearOnly?d.slice(0,4):m2ft(d),imagery_on:d,imagery_date_note:'status-bar, matches the address',leaf,green_share:leaf==='leaf-on'?0.44:0.19})}
  for(const v of out)v.sha256=sha(S.id+v.id+v.label);
  return out;
 }
 function sha(text){let out='';for(let r=0;r<8;r++){let h=0x811c9dc5^r;for(let i=0;i<text.length;i++){h^=text.charCodeAt(i);h=Math.imul(h,0x01000193)>>>0}out+=h.toString(16).padStart(8,'0')}return out}
 const isoAt=ms=>new Date(ms).toISOString();
 function variantOf(I){return I.propertyState||(scope.location?new URLSearchParams(scope.location.search||'').get('property'):null)||'ready'}
 function seed(S,I){
  const main=S===I.studies[0],v=variantOf(I),now=I.now(),P={pin:null,captures:[],gathers:[],renovations:null,renovationsVersion:0,seq:0};
  if(!main||v==='nopin')return P;
  // site: the appraisal prints a site area (12,400 SF) that the parcel does not match.
  if(v==='site'&&S.fields?.length&&!S.fields.some(f=>f.key==='site.area_sqft')){const f=S.fields.find(x=>x.key==='site.site_value');if(f)S.fields.push({...JSON.parse(JSON.stringify(f)),id:S.next(2),key:'site.area_sqft',field:'area_sqft',label:'Site area',critical:false,value_num:'12400',confidence:0.93,regions:[]})}
  P.pin={address:ADDRESS,lat:HOUSE.lat,lon:HOUSE.lon,source:'search',place_label:'1200 Harbor Blvd, Ventura, CA (sample match)',confirmed:v==='confirmed',confirmed_at:v==='confirmed'?isoAt(now-3*DAY):null,version:v==='confirmed'?2:1,updated_at:isoAt(now-4*DAY)};
  if(v==='noplace'){P.pin={...P.pin,lat:null,lon:null,source:'engineer',place_label:null,confirmed:false,confirmed_at:null};P.captures.push(capture(P,{status:'failed',code:'BASIS-CAPTURE-006',requested_at:now-20*MIN,finished_at:now-17*MIN,pin_version:1}));return P}
  const done=capture(P,{status:'succeeded',requested_at:now-4*DAY,finished_at:now-4*DAY+110000,pin_version:1});
  done.views=viewsFor(S,done,1,isoAt(now-4*DAY+100000),HOUSE);done.measure_on=done.views.find(x=>x.label==='history-2025-11-20').id;
  done.notes=[{part:'street',code:'BASIS-CAPTURE-020',message:PM.CAPTURE_PART['BASIS-CAPTURE-020']}];
  P.captures.push(done);
  if(v==='running')P.captures.push(capture(P,{status:'running',requested_at:now-40000,started_at:now-30000,pin_version:P.pin.version,hold:true}));
  if(v==='paused')P.captures.push(capture(P,{status:'waiting',code:'BASIS-CAPTURE-009',requested_at:now-6*MIN,resume_at:now+42*MIN,pin_version:P.pin.version,hold:true}));
  if(v==='waiting')P.captures.push(capture(P,{status:'waiting',requested_at:now-60000,pin_version:P.pin.version,hold:true,turn:true}));
  if(v==='failed')P.captures.push(capture(P,{status:'failed',code:'BASIS-CAPTURE-007',requested_at:now-30*MIN,finished_at:now-26*MIN,pin_version:P.pin.version}));
  P.gathers.push(gather(P,{status:'succeeded',requested_at:now-4*DAY,finished_at:now-4*DAY+67000,pin_version:1}));
  return P;
 }
 function capture(P,o){P.seq++;return {id:id(P.seq,9000),seq:P.seq,status:o.status,code:o.code||null,requested_at:o.requested_at,started_at:o.started_at||(o.status==='queued'?null:o.requested_at+5000),finished_at:o.finished_at||null,resume_at:o.resume_at||null,pin_version:o.pin_version,views:[],notes:[],measure_on:null,hold:!!o.hold,turn:!!o.turn,force:!!o.force}}
 function gather(P,o){P.seq++;return {id:id(P.seq,9100),status:o.status,requested_at:o.requested_at,finished_at:o.finished_at||null,pin_version:o.pin_version,hold:!!o.hold}}
 const MESSAGES={queued:'Property pictures are queued.',running:'Taking property pictures.',succeeded:'Property pictures are ready.',paused:'Property pictures are paused; they will resume on their own.',turn:'Waiting for another property’s pictures to finish; this one is next.'};
 // A capture asked for in the sample runs: queued for 3 s, taking pictures until 12 s, then ready
 // with views around the pin it was taken for (any request checks; no timers).
 function advance(S,I){
  const P=S.property,now=I.now();
  for(const c of P.captures){
   if(c.hold||['succeeded','failed'].includes(c.status))continue;
   const t=now-c.requested_at;
   if(t>=12000){c.status='succeeded';c.finished_at=c.requested_at+12000;const pin=P.pin;c.views=viewsFor(S,c,c.pin_version,isoAt(c.finished_at),{lat:pin.lat,lon:pin.lon});c.measure_on=c.views.find(x=>x.label==='history-2025-11-20').id;c.notes=[{part:'street',code:'BASIS-CAPTURE-020',message:PM.CAPTURE_PART['BASIS-CAPTURE-020']}]}
   else if(t>=3000){c.status='running';c.started_at=c.requested_at+3000}
  }
  for(const g of P.gathers)if(!g.hold&&g.status!=='succeeded'&&g.status!=='failed'){const t=now-g.requested_at;if(t>=8000){g.status='succeeded';g.finished_at=g.requested_at+8000}else if(t>=2000)g.status='running'}
 }
 function captureOut(S,c){
  const P=S.property,pv=P.pin?.version??0,msg=c.status==='waiting'?(c.turn?MESSAGES.turn:MESSAGES.paused):c.status==='failed'?PM.CAPTURE_FAILED[c.code]||'Property pictures could not be taken. Take them again later.':MESSAGES[c.status];
  return {id:c.id,status:c.status,code:c.code,message:msg,resume_at:c.resume_at?isoAt(c.resume_at):null,requested_at:isoAt(c.requested_at),started_at:c.started_at?isoAt(c.started_at):null,finished_at:c.finished_at?isoAt(c.finished_at):null,pin_version:c.pin_version,
   views:c.views.map(v=>({...v,stale:!!P.pin&&v.pin_version!==pv})),notes:c.notes.map(n=>({...n})),measure_on:c.measure_on};
 }
 const pinOut=p=>p?{address:p.address,lat:p.lat,lon:p.lon,source:p.source,place_label:p.place_label,confirmed:p.confirmed,confirmed_at:p.confirmed_at,version:p.version,updated_at:p.updated_at}:null;
 const stateOf=(S,I)=>{S.property||=seed(S,I);advance(S,I);return S.property};

 /* ---------- public records ---------- */
 const PUBLIC=[
  {key:'state-2024',kind:'current',source_id:'sample-state-ortho',year:2024,year_label:null,leaf_off:true,px:0.15,side:800,credit:'Sample state orthoimagery program (synthetic image)',terms_class:'public-domain',date:'2024-04-09'},
  {key:'naip-2022',kind:'history',source_id:'sample-national-ag',year:2022,year_label:null,leaf_off:false,px:0.6,side:200,credit:'Sample national agriculture imagery (synthetic image)',terms_class:'public-domain',date:'2022-07-14'},
  {key:'state-2020',kind:'history',source_id:'sample-state-ortho',year:2020,year_label:'2019-2021 by county',leaf_off:true,px:0.3,side:400,credit:'Sample state orthoimagery program (synthetic image)',terms_class:'public-domain',date:'2020-04-01'},
  {key:'naip-2018',kind:'history',source_id:'sample-national-ag',year:2018,year_label:null,leaf_off:false,px:0.6,side:200,credit:'Sample national agriculture imagery (synthetic image)',terms_class:'public-domain',date:'2018-08-02'},
 ];
 function publicViews(S,g){
  const pin=S.property.pin;return PUBLIC.map((p,i)=>{
   const scale=1/Math.cos(pin.lat*Math.PI/180),[cx,cy]=PM.toMercator(pin.lon,pin.lat),half=p.side*p.px*scale/2,bbox=[cx-half,cy-half,cx+half,cy+half];
   const [w,s]=PM.fromMercator(bbox[0],bbox[1]),[e,n]=PM.fromMercator(bbox[2],bbox[3]);
   return {id:id(i+1,9200+g.seq),kind:p.kind,label:`${p.source_id}-${p.year}`,source_id:p.source_id,year:p.year,year_label:p.year_label,leaf_off:p.leaf_off,lat:pin.lat,lon:pin.lon,pixel_size_m:p.px,native_pixel_m:p.px,crs:'EPSG:3857',bbox,bbox_lonlat:[w,s,e,n],width:p.side,height:p.side,content_type:'image/svg+xml',sha256:sha(S.id+p.key),credit:p.credit,terms_class:p.terms_class,terms_url:'https://sample.basis.invalid/terms/'+p.source_id,captured_at:isoAt(g.finished_at||g.requested_at),stale:g.pin_version!==S.property.pin.version,_key:p.key};
  });
 }
 const ring=pts=>{const r=pts.map(([e,n])=>toLonLat(e,n));r.push(r[0]);return r};
 const SQFT=10.763910416709722;
 const area=pts=>{let a=0;for(let i=0;i<pts.length;i++){const [x1,y1]=pts[i],[x2,y2]=pts[(i+1)%pts.length];a+=x1*y2-x2*y1}return Math.abs(a)/2};
 function parcels(){
  const lot=area(SITE.lot)*SQFT,side=area(SITE.side)*SQFT;
  const p=(pid,under,pts,sf,layerAcres,extra={})=>({parcel_id:pid,source_id:'sample-county-parcels',under_pin:under,owner:'Harbor House Holdings LLC (sample)',address:under?'1200 HARBOR BLVD':null,area_sqft:Math.round(sf*10)/10,area_acres:Math.round(sf/43560*10000)/10000,layer_acres:layerAcres,land_value:null,improvement_value:null,total_value:null,year_built:under?1998:null,building_area_sqft:under?3240:null,stories:under?2:null,geometry:{type:'Polygon',coordinates:[ring(pts)]},credit:'Sample county parcel layer (synthetic)',terms_class:'open-with-credit',...extra});
  return [p('071-0-224-115',true,SITE.lot,lot,0.35),p('071-0-224-116',false,SITE.side,side,0.11)];
 }
 function siteCheck(S,ps){
  const field=(S.fields||[]).find(f=>f.key==='site.area_sqft');
  const appraisal=field?Number(field.value_num):null,under=ps.find(x=>x.under_pin);
  if(!appraisal)return {status:'no_appraisal',flag:false,code:null,message:'The appraisal prints no site area to compare with the parcel.',appraisal_sqft:null,parcel_sqft:under.area_sqft,layer_sqft:Math.round(under.layer_acres*43560),polygon_sqft:under.area_sqft,difference_pct:null,parcels:[under.parcel_id],combined_sqft:null,tolerance_pct:5};
  const diff=(under.area_sqft-appraisal)/appraisal*100,combined=ps.reduce((t,x)=>t+x.area_sqft,0),adj=Math.abs(combined-appraisal)/appraisal*100<=5;
  const status=Math.abs(diff)<=5?'agree':adj?'adjacent_match':'disagree';
  return {status,flag:status==='disagree',code:status==='disagree'?'BASIS-GEO-012':status==='adjacent_match'?'BASIS-GEO-013':null,
   message:status==='agree'?`The parcel’s area agrees with the appraisal’s site area (within 5 %).`:status==='adjacent_match'?'The owner’s adjacent parcels together match the appraisal’s site area: the site is these parcels.':`The parcel under the pin is ${Math.round(under.area_sqft).toLocaleString('en-US')} SF; the appraisal’s site area is ${appraisal.toLocaleString('en-US')} SF (${diff>=0?'+':''}${diff.toFixed(1)} %). Check the appraisal’s site area or the pin.`,
   appraisal_sqft:appraisal,parcel_sqft:under.area_sqft,layer_sqft:Math.round(under.layer_acres*43560),polygon_sqft:under.area_sqft,difference_pct:Math.round(diff*10)/10,parcels:status==='adjacent_match'?ps.map(x=>x.parcel_id):[under.parcel_id],combined_sqft:status==='adjacent_match'?combined:null,tolerance_pct:5};
 }
 // The lidar's roof outline (EVAL_REPORT 104.2): the house and the garage as one roof, 0.2 m of
 // overhang beyond their walls (205.6 m2, the footprint's 205.3 within a cell's rounding).
 const ROOF_OUTLINE=[[-7.2,-5.2],[12.2,-5.2],[12.2,2.2],[5.2,2.2],[5.2,7.2],[-7.2,7.2]].map(([e,n])=>toLonLat(e,n).map(v=>Math.round(v*1e9)/1e9));
 const LIDAR={project:'Sample statewide lidar (synthetic)',year:2021,points:4812,density_per_m2:8.2,building:{footprint_m2:205.3,footprint_sqft:2209.9,peak_m:8.9,max_m:10.2,eave_m:5.6,ground_elevation_m:177.0,points:1684,distance_m:0,outline:ROOF_OUTLINE},code:null,credit:'Sample elevation program (synthetic)',terms_class:'public-domain',terms_url:'https://sample.basis.invalid/terms/lidar'};
 function publicOut(S,I){
  const P=stateOf(S,I),g=P.gathers[P.gathers.length-1];
  if(!g||!P.pin||P.pin.lat==null)return {enabled:true,gather:g?gatherOut(S,g):null,current:null,history:[],parcels:[],site_check:null,lidar:null};
  const ok=g.status==='succeeded'||P.gathers.some(x=>x.status==='succeeded'),views=ok?publicViews(S,P.gathers.filter(x=>x.status==='succeeded').pop()):[],ps=ok?parcels():[];
  const strip=v=>{const o={...v};delete o._key;return o};
  return {enabled:true,gather:gatherOut(S,g),current:views[0]?strip(views[0]):null,history:views.map(strip),parcels:ps,site_check:ok?siteCheck(S,ps):null,lidar:ok?JSON.parse(JSON.stringify(LIDAR)):null};
 }
 function gatherOut(S,g){
  const sources=[['sample-state-ortho','imagery','ok',2024],['sample-national-ag','imagery','ok',2022],['sample-county-parcels','parcel','ok',null],['sample-lidar','lidar','ok',2021]].map(([source,kind,status,year],i)=>({source,kind,status,code:null,seconds:[4.1,6.8,1.9,22.4][i],year}));
  return {id:g.id,status:g.status,code:null,message:g.status==='succeeded'?'Public records of the property are ready.':g.status==='failed'?'Public records could not be gathered. Ask for them again later.':'Gathering public records of the property.',requested_at:isoAt(g.requested_at),started_at:isoAt(g.requested_at+1000),finished_at:g.finished_at?isoAt(g.finished_at):null,pin_version:g.pin_version,stale:g.pin_version!==S.property.pin?.version,sources:g.status==='succeeded'?sources:[]};
 }

 /* ---------- routes ---------- */
 const TEXT=v=>typeof v==='string'&&v.trim().length>=1&&v.length<=300;
 // If-Match as the real API reads it (basis/api/preconditions.py), on a version.
 function ifMatch(header,version,exists){if(header==null||header==='')return true;return header.split(',').map(x=>x.trim()).some(t=>t==='*'?exists:t===`"${version}"`)}
 function register({route,json,withStudy,notFound,conflict,invalid,fieldErr,Problem,event,iso}){
  const prop=(S,ctx)=>stateOf(S,ctx.i);
  const engineerOnly=ctx=>ctx.i.me.role==='viewer'?new Problem(403,'forbidden','Forbidden','A viewer reads the firm’s studies and cannot change them.','BASIS-API-010'):null;
  route('GET','/v1/studies/{study_id}/property',withStudy((S,ctx)=>{const P=prop(S,ctx),c=P.captures[P.captures.length-1];return json(200,{pin:pinOut(P.pin),capture:c?captureOut(S,c):null,enabled:true},{etag:`"${P.pin?.version??0}"`})}));
  route('PUT','/v1/studies/{study_id}/property/pin',withStudy((S,ctx)=>{
   const P=prop(S,ctx),b=ctx.body,errs=[];
   if(!b||typeof b!=='object'||Array.isArray(b))return invalid([fieldErr(['body'],'Input should be a valid dictionary','dict_type')]);
   for(const k of Object.keys(b))if(!['address','lat','lon','confirmed'].includes(k))errs.push(fieldErr(['body',k],'Extra inputs are not permitted','extra_forbidden'));
   if(b.address!=null&&!TEXT(b.address))errs.push(fieldErr(['body','address'],'String should have 1 to 300 characters','string_too_long'));
   if(b.lat!=null&&!(typeof b.lat==='number'&&b.lat>=-90&&b.lat<=90))errs.push(fieldErr(['body','lat'],'Input should be between -90 and 90','less_than_equal'));
   if(b.lon!=null&&!(typeof b.lon==='number'&&b.lon>=-180&&b.lon<=180))errs.push(fieldErr(['body','lon'],'Input should be between -180 and 180','less_than_equal'));
   if((b.lat==null)!==(b.lon==null))errs.push(fieldErr(['body'],'Value error, send both lat and lon, or neither','value_error'));
   if(b.address==null&&b.lat==null)errs.push(fieldErr(['body'],'Value error, send an address, a point, or both','value_error'));
   if(b.confirmed===true&&b.lat==null)errs.push(fieldErr(['body'],'Value error, only a point can be confirmed','value_error'));
   if(errs.length)return invalid(errs);
   const version=P.pin?.version??0;
   if(!ifMatch(ctx.h['if-match'],version,!!P.pin))return new Problem(412,'precondition-failed','Precondition Failed','The pin changed since you read it; read it again and reapply your change.','BASIS-CAPTURE-016',undefined,{etag:`"${version}"`});
   const cur=P.pin,lat=b.lat??null,lon=b.lon??null,moved=!cur||cur.lat!==lat||cur.lon!==lon,address=b.address??cur?.address??null;
   const next={address,lat,lon,source:moved&&lat!=null?'engineer':cur?.source||'engineer',place_label:moved?null:cur?.place_label??null,confirmed:!!b.confirmed,confirmed_at:b.confirmed?(cur?.confirmed&&!moved?cur.confirmed_at:iso(ctx.i.now())):null};
   if(cur&&['address','lat','lon','source','place_label','confirmed'].every(k=>cur[k]===next[k]))return json(200,pinOut(cur),{etag:`"${version}"`});
   P.pin={...next,version:version+1,updated_at:iso(ctx.i.now())};
   event(S,'property.pin_set',{actor:'engineer',entity_type:'property_pin',before:pinOut(cur),after:pinOut(P.pin)});
   return json(200,pinOut(P.pin),{etag:`"${P.pin.version}"`});
  }));
  route('POST','/v1/studies/{study_id}/property/captures',withStudy((S,ctx)=>{
   const deny=engineerOnly(ctx);if(deny)return deny;const P=prop(S,ctx),force=!!ctx.body?.force,now=ctx.i.now();
   if(!P.pin)return new Problem(422,'capture-no-address','Unprocessable Content','There is no pin: give the property’s address or a point first.','BASIS-CAPTURE-015');
   const live=P.captures.find(c=>['queued','waiting','running'].includes(c.status));
   if(live)return conflict('capture-in-progress','Property pictures are already being taken for this study.','BASIS-CAPTURE-014');
   const recent=P.captures.slice().reverse().find(c=>c.status==='succeeded'&&c.pin_version===P.pin.version&&now-(c.finished_at||0)<12*3600000);
   if(recent&&!force)return json(200,captureOut(S,recent));
   const c=capture(P,{status:'queued',requested_at:now,pin_version:P.pin.version,force});P.captures.push(c);
   if(P.pin.lat!=null)P.gathers.push(gather(P,{status:'queued',requested_at:now,pin_version:P.pin.version}));
   event(S,'capture.requested',{actor:'engineer',entity_type:'property_capture',entity_id:c.id});
   return json(202,captureOut(S,c));
  }));
  route('GET','/v1/studies/{study_id}/property/captures',withStudy((S,ctx)=>{const P=prop(S,ctx),limit=Math.min(50,Number(ctx.q.get('limit')||20));return json(200,{items:P.captures.slice().reverse().slice(0,limit).map(c=>captureOut(S,c))})}));
  route('GET','/v1/studies/{study_id}/property/captures/{capture_id}',withStudy((S,ctx)=>{const c=prop(S,ctx).captures.find(x=>x.id===ctx.p.capture_id);return c?json(200,captureOut(S,c)):notFound('Capture not found.')}));
  route('GET','/v1/studies/{study_id}/property/views/{view_id}/image',withStudy((S,ctx)=>{
   const P=prop(S,ctx);let v=null;for(const c of P.captures){v=c.views.find(x=>x.id===ctx.p.view_id);if(v)break}
   if(!v)return notFound('View not found.');
   const [ce,cn]=toMetres(v.lon,v.lat);
   const svg=v.kind==='oblique'?sideSvg(v.label):aerialSvg({w:v.width,h:v.height,s:v.pixel_size_m,ce,cn,date:v.imagery_on,wide:v.label==='aerial-wide'});
   return {status:200,bytes:new TextEncoder().encode(svg),headers:{'content-type':'image/svg+xml',etag:`"${v.sha256}"`,'cache-control':'private, max-age=3600'}};
  }));
  route('GET','/v1/studies/{study_id}/property/public',withStudy((S,ctx)=>json(200,publicOut(S,ctx.i))));
  route('POST','/v1/studies/{study_id}/property/public',withStudy((S,ctx)=>{
   const deny=engineerOnly(ctx);if(deny)return deny;const P=prop(S,ctx),now=ctx.i.now();
   if(!P.pin||P.pin.lat==null)return new Problem(422,'geo-no-point','Unprocessable Content','The public sources are asked by point: drop the pin on the house first.','BASIS-GEO-002');
   if(P.gathers.some(g=>['queued','running'].includes(g.status)))return conflict('geo-in-progress','Public records are already being gathered for this study.','BASIS-GEO-003');
   const recent=P.gathers.slice().reverse().find(g=>g.status==='succeeded'&&g.pin_version===P.pin.version&&now-(g.finished_at||0)<12*3600000);
   if(recent&&!ctx.body?.force)return json(200,gatherOut(S,recent));
   const g=gather(P,{status:'queued',requested_at:now,pin_version:P.pin.version});P.gathers.push(g);return json(202,gatherOut(S,g));
  }));
  route('GET','/v1/studies/{study_id}/property/public/views/{view_id}/image',withStudy((S,ctx)=>{
   const P=prop(S,ctx),g=P.gathers.filter(x=>x.status==='succeeded').pop();if(!g)return notFound('View not found.');
   const v=publicViews(S,g).find(x=>x.id===ctx.p.view_id);if(!v)return notFound('View not found.');
   const p=PUBLIC.find(x=>x.key===v._key),[ce,cn]=toMetres(v.lon,v.lat);
   const svg=aerialSvg({w:v.width,h:v.height,s:v.pixel_size_m,ce,cn,date:p.date,coarse:p.px>=0.5?0.8:0});
   return {status:200,bytes:new TextEncoder().encode(svg),headers:{'content-type':'image/svg+xml',etag:`"${v.sha256}"`,'cache-control':'private, max-age=3600'}};
  }));
  // Renovations (docs/api/RENOVATIONS.md): the document as sent, If-Match on its version; the
  // sample checks the members the Property area writes (the brackets) and keeps the rest.
  route('GET','/v1/studies/{study_id}/renovations',withStudy((S,ctx)=>{const P=prop(S,ctx);return json(200,{study_id:S.id,renovations:P.renovations?JSON.parse(JSON.stringify(P.renovations)):null,renovations_version:P.renovationsVersion},{etag:`"${P.renovationsVersion}"`})}));
  route('PUT','/v1/studies/{study_id}/renovations',withStudy((S,ctx)=>{
   const P=prop(S,ctx),b=ctx.body,errs=[];
   if(['exporting','completed'].includes(S.stage))return conflict('renovations-locked','The current revision is exported: reopen the study at the assets review to change its renovations.','BASIS-STUDY-038');
   if(!ifMatch(ctx.h['if-match'],P.renovationsVersion,!!P.renovations))return new Problem(412,'precondition-failed','Precondition Failed','The renovations changed since you read them; read them again and reapply your change.','BASIS-STUDY-039',undefined,{etag:`"${P.renovationsVersion}"`});
   if(!b||typeof b!=='object'||!Array.isArray(b.projects))return invalid([fieldErr(['body','projects'],'Field required','missing')]);
   for(const k of Object.keys(b))if(!['projects','acquisition_bonus_rate','exterior_brackets'].includes(k))errs.push(fieldErr(['body',k],'Extra inputs are not permitted','extra_forbidden'));
   const checkBracket=(br,loc)=>{const ok=x=>x&&typeof x==='object'&&/^\d{4}-\d{2}-\d{2}$/.test(x.date||'')&&TEXT(x.reference);if(!br||!ok(br.last_without)||!ok(br.first_with))errs.push(fieldErr(loc,'A bracket is {last_without, first_with}, each {date, reference}.','value_error'));else if(br.first_with.date<=br.last_without.date)errs.push(fieldErr(loc,'Value error, the first view with the work must be after the last view without it','value_error'))};
   (b.exterior_brackets||[]).forEach((x,i)=>{if(!TEXT(x?.label))errs.push(fieldErr(['body','exterior_brackets',i,'label'],'Field required','missing'));checkBracket(x?.bracket,['body','exterior_brackets',i,'bracket'])});
   b.projects.forEach((p,i)=>{if(p?.aerial)checkBracket(p.aerial,['body','projects',i,'aerial'])});
   if(errs.length)return invalid(errs);
   const before=P.renovations;P.renovations=JSON.parse(JSON.stringify({projects:b.projects,acquisition_bonus_rate:b.acquisition_bonus_rate??null,exterior_brackets:b.exterior_brackets||[]}));P.renovationsVersion++;
   event(S,'study.renovations_set',{actor:'engineer',before,after:P.renovations,detail:{version_before:P.renovationsVersion-1,version_after:P.renovationsVersion,if_match:ctx.h['if-match']??null}});
   return json(200,{study_id:S.id,renovations:JSON.parse(JSON.stringify(P.renovations)),renovations_version:P.renovationsVersion},{etag:`"${P.renovationsVersion}"`});
  }));
 }
 // The site's true sizes, for tests and the verification notes.
 const truth={patioSf:40*20,fenceFt:64/FT,lotSqft:area(SITE.lot)*SQFT,patioBuilt:PATIO_BUILT};
 // A model's draft of the patio, for the measure tool's original and verified state (EVAL_REPORT
 // 104; no model drafts measurements yet, so this is a fixture): the 40 x 20 ft patio outlined a
 // little off, as a drafting method might, on the close aerial (the &measure=draft sample).
 function patioDraft(){
  const ring=[[-8.25,8.35],[4.05,8.5],[4.15,14.9],[-8.1,14.75]].map(([e,n])=>toLonLat(e,n).map(v=>Math.round(v*1e9)/1e9));
  return {unit:'SF',space:'lonlat',shapes:[{kind:'area',ring}],image:{source:SOURCE,captured_on:'2026-06-18',pixel_size_m:CLOSE},drawn_by:'model:sample-drafter@1',verified:false,view:{kind:'capture',label:'aerial-close'}};
 }
 const api={register,truth,HOUSE,aerialSvg,sideSvg,SITE,toLonLat,patioDraft};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisPropertySample=api;
})(typeof globalThis==='object'?globalThis:this);
