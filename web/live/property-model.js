/* The Property area's rules without the DOM (web/tests/property-model.test.cjs).
 *
 * * Traced measurements: the same computation as the server (backEnd/basis/domain/traced.py on
 *   the measurement branch, docs/api/OVERRIDES.md section 4): lon/lat shapes on the local
 *   tangent plane of the WGS84 ellipsoid (the meridional and prime-vertical radii of curvature
 *   at the shape's mean latitude; Snyder, USGS Professional Paper 1395, ch. 4), image shapes in
 *   pixels times a scale, the expected error (one pixel on every edge), and the quantity rounded
 *   half up to 0.01 the way Python's Decimal does. The live readout is what the server will
 *   compute, so the engineer sees the number that lands on the row.
 * * Georeference: a top-down property view is north up, `pixel_size_m` metres a pixel at its
 *   centre (docs/api/PROPERTY_CAPTURE.md section 3); a public view adds its exact Web Mercator
 *   box (docs/api/PUBLIC_GEO.md section 2). Pixels convert to lon/lat with the same ellipsoid
 *   radii the server measures with, so a traced rectangle measures as its pixels times the
 *   pixel size squared (the backlog's rounded 111,320 / 110,540 metres a degree would be off by
 *   about half a percent at 43 degrees north: web/DESIGN_NOTES.md, "Property").
 * * The capture's and the gather's status in plain words, the views sorted for the screen, the
 *   history strip's marks, the review flags a measurement will raise, the parcel outline in
 *   pixels, the site-area check, the renovation bracket, and the capabilities.
 *
 * Nothing here names an imagery provider: the texts come from the API (`message`, `source`,
 * `credit`) or say what the engineer does.
 */
(function(scope){
 'use strict';
 const FT_PER_M=1/0.3048,SF_PER_SY=9,MAX_QUANTITY=10000000;
 const WGS84_A=6378137.0,WGS84_F=1/298.257223563,WGS84_E2=WGS84_F*(2-WGS84_F);
 const MERCATOR_R=6378137.0;
 const rad=d=>d*Math.PI/180,deg=r=>r*180/Math.PI;
 const UNITS=['SF','SY','LF'];

 /* ---------- numbers ---------- */
 // Python's Decimal(repr(x)).quantize(Decimal('0.01'), ROUND_HALF_UP): JavaScript's
 // shortest round-trip string is Python's repr for these magnitudes, so the decimal digits are
 // the same and the tie rounds away from zero exactly as on the server. Returns a string.
 function round2(x){
  if(!Number.isFinite(x))return null;
  let s=String(Math.abs(x));
  if(/e/i.test(s))s=Math.abs(x).toFixed(20).replace(/0+$/,'');
  const [ip,fp='']=s.split('.'),digits=(fp+'000');
  let cents=BigInt(ip+digits.slice(0,2));
  if(digits.charCodeAt(2)>=53)cents+=1n;// '5'..'9': half up (away from zero)
  const neg=x<0&&cents!==0n,str=cents.toString().padStart(3,'0');
  return (neg?'-':'')+str.slice(0,-2)+'.'+str.slice(-2);
 }
 const num=v=>v===null||v===undefined||v===''?null:Number(v);

 /* ---------- WGS84 ---------- */
 function radii(latDeg){const s=Math.sin(rad(latDeg)),w=1-WGS84_E2*s*s;return [WGS84_A*(1-WGS84_E2)/Math.pow(w,1.5),WGS84_A/Math.sqrt(w)]}
 const unwrap=(points,ref)=>points.map(([lon,lat])=>[ref+((((lon-ref+180)%360)+360)%360)-180,lat]);
 function plane(points,origin,lat0){
  const [m,n]=radii(lat0),kx=rad(1)*n*Math.cos(rad(lat0))*FT_PER_M,ky=rad(1)*m*FT_PER_M;
  return points.map(([lon,lat])=>[(lon-origin[0])*kx,(lat-origin[1])*ky]);
 }
 function shoelace(p){const [x0,y0]=p[0];let t=0;for(let i=0;i<p.length;i++){const [x1,y1]=p[i],[x2,y2]=p[(i+1)%p.length];t+=(x1-x0)*(y2-y0)-(x2-x0)*(y1-y0)}return t/2}
 const dist=(a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1]);
 function perimeter(p){let t=0;for(let i=0;i<p.length;i++)t+=dist(p[i],p[(i+1)%p.length]);return t}
 function pathLength(p){let t=0;for(let i=0;i+1<p.length;i++)t+=dist(p[i],p[i+1]);return t}
 function lonlatPathFt(points){
  let t=0;for(let i=0;i+1<points.length;i++){const [lon1,lat1]=points[i],[lon2,lat2]=points[i+1],mid=(lat1+lat2)/2,[m,n]=radii(mid);
   t+=Math.hypot(rad(lon2-lon1)*n*Math.cos(rad(mid)),rad(lat2-lat1)*m)*FT_PER_M}
  return t;
 }

 /* ---------- shape checks (the server's ring rules, in a plane) ---------- */
 function ringProblem(p){
  const n=p.length;if(n<3)return 'needs at least 3 corners';
  const q=p.map((_,i)=>p[(i+1)%n]),seg=p.map((a,i)=>[q[i][0]-a[0],q[i][1]-a[1]]),len=seg.map(s=>Math.hypot(s[0],s[1])),scale=Math.max(...len);
  if(scale===0||len.some(l=>l<=scale*1e-12))return 'has a repeated corner (two consecutive corners at the same place)';
  const eps=scale*scale*1e-12,tol=1e-12*scale;
  for(let i=0;i<n;i++){const a=seg[(i-1+n)%n],b=seg[i],cross=a[0]*b[1]-a[1]*b[0],dot=a[0]*b[0]+a[1]*b[1];if(Math.abs(cross)<=eps&&dot<0)return 'folds back on itself (an edge runs back along the one before it)'}
  if(n===3)return null;
  const orient=(u,v,w)=>(v[0]-u[0])*(w[1]-u[1])-(v[1]-u[1])*(w[0]-u[0]);
  const on=(o,u,v,w)=>Math.abs(o)<=eps&&w[0]>=Math.min(u[0],v[0])-tol&&w[0]<=Math.max(u[0],v[0])+tol&&w[1]>=Math.min(u[1],v[1])-tol&&w[1]<=Math.max(u[1],v[1])+tol;
  for(let i=0;i<n;i++)for(let j=i+2;j<n;j++){
   if(i===0&&j===n-1)continue;
   const a=p[i],b=q[i],c=p[j],d=q[j],o1=orient(a,b,c),o2=orient(a,b,d),o3=orient(c,d,a),o4=orient(c,d,b);
   if((o1*o2<-eps*eps&&o3*o4<-eps*eps)||on(o1,a,b,c)||on(o2,a,b,d)||on(o3,c,d,a)||on(o4,c,d,b))return 'crosses or touches itself (two of its edges meet)';
  }
  return null;
 }
 // Even-odd inside test with points on an edge counting as inside (the server's outside_points).
 function insideOrOn(pt,ring){
  const tol=1e-9*Math.max(1,...ring.map(r=>Math.abs(r[0])+Math.abs(r[1])));let inside=false;
  for(let i=0,j=ring.length-1;i<ring.length;j=i++){
   const a=ring[j],b=ring[i],cross=(b[0]-a[0])*(pt[1]-a[1])-(b[1]-a[1])*(pt[0]-a[0]);
   if(Math.abs(cross)<=tol*dist(a,b)&&pt[0]>=Math.min(a[0],b[0])-tol&&pt[0]<=Math.max(a[0],b[0])+tol&&pt[1]>=Math.min(a[1],b[1])-tol&&pt[1]<=Math.max(a[1],b[1])+tol)return true;
   if((a[1]>pt[1])!==(b[1]>pt[1])&&pt[0]<(b[0]-a[0])*(pt[1]-a[1])/(b[1]-a[1])+a[0])inside=!inside;
  }
  return inside;
 }
 const dropClosing=r=>r.length>1&&r[0][0]===r[r.length-1][0]&&r[0][1]===r[r.length-1][1]?r.slice(0,-1):r;

 /* ---------- the measurement (OVERRIDES.md section 4) ---------- */
 class MeasurementError extends Error{constructor(problems){super(problems.join('; '));this.problems=problems}}
 function expectedErrorPct({areaSf,lengthFt,edgesFt,lines,pixelFt,scalePx}){
  if(pixelFt===null||pixelFt===undefined)return null;
  let rel;
  if(areaSf!==null&&areaSf!==undefined){rel=edgesFt*pixelFt/areaSf;if(scalePx)rel+=2*(2/scalePx)}
  else{rel=2*pixelFt*lines/lengthFt;if(scalePx)rel+=2/scalePx}
  return round2(rel*100);
 }
 // measure(body) -> the computed record the server stores beside the shape, or throws with every problem.
 function measure(m){
  const problems=[],add=(w,t)=>problems.push(`measurement${w} ${t}`);
  if(!m||typeof m!=='object'||Array.isArray(m))throw new MeasurementError(['measurement must be an object (unit, space, shapes, image)']);
  const {unit,space}=m;
  if(!UNITS.includes(unit))add('.unit',`must be SF, SY or LF (EA is counted, not traced), got ${JSON.stringify(unit)}`);
  if(!['lonlat','image'].includes(space))add('.space',`must be lonlat or image, got ${JSON.stringify(space)}`);
  const image=m.image;
  if(!image||typeof image!=='object'||typeof image.source!=='string'||!image.source.trim())add('.image.source','is required: what the image is and where it came from');
  else if(image.source.trim().length>300)add('.image.source','is longer than 300 characters');
  if(image?.captured_on!=null&&!/^\d{4}-\d{2}-\d{2}$/.test(image.captured_on))add('.image.captured_on','is not a YYYY-MM-DD date');
  const pixelSizeM=num(image?.pixel_size_m);
  if(pixelSizeM!==null&&!(pixelSizeM>0&&pixelSizeM<=100))add('.image.pixel_size_m','must be above 0 and at most 100');
  if(pixelSizeM!==null&&space==='image')add('.image.pixel_size_m','is for lon/lat imagery; an image in pixels has a scale');
  let feetPerPx=null,scalePx=null;
  if(space==='image'){
   const s=m.scale;if(!s)add('.scale','is required for an image: two pixels and the length between them');
   else{scalePx=dist(s.points[0],s.points[1]);if(scalePx<2)add('.scale.points','are under 2 pixels apart');else feetPerPx=(s.unit==='m'?s.length*FT_PER_M:s.length)/scalePx}
  }else if(space==='lonlat'&&m.scale!=null)add('.scale','is for an image in pixels; lon/lat points need no scale');
  const height=m.height_ft==null?null:Number(m.height_ft);
  if(height!==null&&!(height>0&&height<=100000))add('.height_ft','must be above 0 and at most 100,000');
  const shapes=Array.isArray(m.shapes)?m.shapes:[];
  if(!shapes.length||shapes.length>50)add('.shapes','must hold 1 to 50 shapes');
  const kinds=new Set(shapes.map(s=>s.kind));
  if(kinds.size>1)add('.shapes','mix area and line shapes; one measurement is all areas or all lines');
  const kind=[...kinds][0];
  if(unit==='LF'&&kind==='area')add('.shapes','are areas; LF is measured with line shapes');
  if(unit==='LF'&&height!==null)add('.height_ft','goes with SF or SY (a wall face); LF is a length alone');
  if((unit==='SF'||unit==='SY')&&kind==='line'&&height===null)add('.height_ft',`is required to measure ${unit} with lines (a wall face: length x height)`);
  if((unit==='SF'||unit==='SY')&&kind==='area'&&height!==null)add('.height_ft','goes with line shapes (a wall face), not with areas');
  if(space==='lonlat')for(const s of shapes)for(const r of [s.ring||s.path||[],...(s.cutouts||[])])for(const [lon,lat] of r)if(!(lon>=-180&&lon<=180&&lat>=-85&&lat<=85)){add('.shapes','hold a point outside longitude -180 to 180 or latitude -85 to 85');break}
  if(problems.length)throw new MeasurementError(problems);
  const ref=(shapes[0].ring||shapes[0].path)[0][0];
  let areaSf=null,lengthFt=null,edges=0,corners=0,cutouts=0;
  if(kind==='line'){
   lengthFt=0;
   shapes.forEach((s,i)=>{const path=s.path||[];corners+=path.length;if(path.length<2||path.length>500){add(`.shapes[${i}].path`,'needs 2 to 500 points');return}
    lengthFt+=space==='lonlat'?lonlatPathFt(unwrap(path,ref)):pathLength(path)*feetPerPx});
   edges=lengthFt;
  }else{
   areaSf=0;
   shapes.forEach((s,i)=>{
    const outer=dropClosing(s.ring||[]),cuts=(s.cutouts||[]).map(dropClosing);corners+=outer.length+cuts.reduce((n,c)=>n+c.length,0);cutouts+=cuts.length;
    if(outer.length<3||outer.length>500){add(`.shapes[${i}].ring`,'needs 3 to 500 corners');return}
    if(cuts.length>20){add(`.shapes[${i}].cutouts`,'are more than 20');return}
    let rings=[outer,...cuts],flat;
    if(space==='lonlat'){rings=rings.map(r=>unwrap(r,ref));const lats=rings[0].map(p=>p[1]),lat0=(Math.min(...lats)+Math.max(...lats))/2,origin=rings[0][0];flat=rings.map(r=>plane(r,origin,lat0))}
    else flat=rings.map(r=>r.map(([x,y])=>[x*feetPerPx,y*feetPerPx]));
    const names=['ring',...cuts.map((_,k)=>`cutouts[${k}]`)];let bad=false;
    flat.forEach((r,k)=>{let p=ringProblem(r);if(!p&&Math.abs(shoelace(r))<=1e-9*perimeter(r)**2)p='has no area (its corners lie on one line)';if(p){add(`.shapes[${i}].${names[k]}`,p);bad=true}});
    if(bad)return;
    flat.slice(1).forEach((c,k)=>{const out=c.findIndex(pt=>!insideOrOn(pt,flat[0]));if(out>=0){add(`.shapes[${i}].cutouts[${k}]`,`is not inside its ring (corner ${out} is outside)`);bad=true}});
    if(bad)return;
    const net=Math.abs(shoelace(flat[0]))-flat.slice(1).reduce((t,c)=>t+Math.abs(shoelace(c)),0);
    if(net<=0){add(`.shapes[${i}]`,'has no area left once its cutouts are taken out');return}
    areaSf+=net;edges+=flat.reduce((t,r)=>t+perimeter(r),0);
   });
  }
  if(problems.length)throw new MeasurementError(problems);
  const raw=areaSf!==null?(unit==='SY'?areaSf/SF_PER_SY:areaSf):lengthFt*(height||1)/(unit==='SY'?SF_PER_SY:1);
  const quantity=round2(raw);
  if(!(Number(quantity)>0)||Number(quantity)>MAX_QUANTITY)throw new MeasurementError([`measurement comes to ${raw.toFixed(4)} ${unit}; the quantity must be above 0 and at most 10,000,000 (after rounding to 0.01)`]);
  const pixelFt=space==='image'?feetPerPx:pixelSizeM===null?null:pixelSizeM*FT_PER_M;
  return {unit,quantity,area_sf:areaSf===null?null:round2(areaSf),length_ft:lengthFt===null?null:round2(lengthFt),method:space==='image'?'image_scale':'local_tangent_plane_wgs84',
   expected_error_pct:expectedErrorPct({areaSf,lengthFt,edgesFt:edges,lines:shapes.length,pixelFt,scalePx}),shape_count:shapes.length,corner_count:corners,cutout_count:cutouts,scale_ft_per_px:feetPerPx};
 }
 function problems(m){try{measure(m);return []}catch(e){if(e instanceof MeasurementError)return e.problems;throw e}}
 // The takeoff's sentence (traced.py formula), for the row before the server answers.
 function formula(r,{source,captured_on,height_ft}={}){
  const n=r.shape_count,kind=r.area_sf!==null?'area':'line',noun=kind+(n!==1?'s':'');
  let counts=kind==='area'?`${n} ${noun} of ${r.corner_count} corners${r.cutout_count?` (${r.cutout_count} cut out)`:''}`:`${n} ${noun} of ${r.corner_count} points${height_ft!=null?` x ${height_ft} ft high`:''}`;
  const q=Number(r.quantity).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  return `${q} ${r.unit} traced by the engineer on ${source} (${captured_on?'captured '+captured_on:'undated'}), ${counts}; ${r.expected_error_pct===null?'expected error unknown (no pixel size)':`expected error up to ${r.expected_error_pct} %`}`;
 }
 // The review flags the server will write (traced.review_flags): never a refusal.
 function reviewFlags(r,{captured_on,placedInService,afterServiceDays=365,errorFlagPct=10}={}){
  const out=[];
  if(!captured_on)out.push('measure:IMAGE_UNDATED');
  else if(placedInService&&(Date.parse(captured_on)-Date.parse(placedInService))/86400000>afterServiceDays)out.push('measure:IMAGE_AFTER_SERVICE');
  if(r.expected_error_pct===null)out.push('measure:ERROR_UNKNOWN');
  else if(Number(r.expected_error_pct)>errorFlagPct)out.push('measure:COARSE');
  return out;
 }

 /* ---------- georeference ---------- */
 const toMercator=(lon,lat)=>[MERCATOR_R*rad(lon),MERCATOR_R*Math.log(Math.tan(Math.PI/4+rad(lat)/2))];
 const fromMercator=(x,y)=>[deg(x/MERCATOR_R),deg(2*Math.atan(Math.exp(y/MERCATOR_R))-Math.PI/2)];
 // A view's pixel <-> lon/lat. Null for a view that must not be measured on (tilted, street level).
 function georef(view){
  if(!view)return null;
  const w=num(view.width),h=num(view.height),s=num(view.pixel_size_m);
  if(!(w>0&&h>0))return null;
  const bbox=Array.isArray(view.bbox)&&view.bbox.length===4&&(view.crs==null||view.crs==='EPSG:3857')?view.bbox.map(Number):null;
  if(bbox){
   const [xmin,ymin,xmax,ymax]=bbox;
   return {kind:'mercator',width:w,height:h,pixelSizeM:s,
    toLonLat:(x,y)=>fromMercator(xmin+x*(xmax-xmin)/w,ymax-y*(ymax-ymin)/h),
    toPixel:(lon,lat)=>{const [mx,my]=toMercator(lon,lat);return [(mx-xmin)*w/(xmax-xmin),(ymax-my)*h/(ymax-ymin)]}};
  }
  if(!(s>0)||view.lat==null||view.lon==null)return null;
  if(view.heading_deg!=null&&Math.abs(Number(view.heading_deg))>1e-9)return null;// the API's views are north up; anything else is not converted
  if(view.tilt_deg!=null&&Math.abs(Number(view.tilt_deg))>1e-9)return null;
  const lat0=Number(view.lat),lon0=Number(view.lon),[M,N]=radii(lat0),kLat=deg(1/M),kLon=deg(1/(N*Math.cos(rad(lat0))));
  return {kind:'centre',width:w,height:h,pixelSizeM:s,
   toLonLat:(x,y)=>[lon0+(x-w/2)*s*kLon,lat0+(h/2-y)*s*kLat],
   toPixel:(lon,lat)=>[w/2+rad(lon-lon0)*N*Math.cos(rad(lat0))/s,h/2-rad(lat-lat0)*M/s]};
 }
 const measurable=v=>!!georef(v)&&num(v.pixel_size_m)>0;

 /* ---------- what to measure ---------- */
 // An asset the aerial can size: a row held for a measured quantity (EVAL_REPORT 78) whose unit
 // is an area or a length, outside the building (an exterior room, or no room), that is a
 // component (it has a label: the structure model's building-wide rows, wiring, piping, framing,
 // have none and no room) and holds its own quantity (not a copy that points at the row the
 // property's measurement is on, takeoff.inputs.measured_with_asset). Live check 2026-11-24.
 const MEASURE_REASONS=['input:exterior_area','input:room','cross:COST_NEEDS_QUANTITY','unpriced'];
 function measureUnit(a){
  const cand=[a?.cost?.trail?.base?.unit,a?.cost?.trail?.base?.row_unit,a?.takeoff?.unit].find(u=>UNITS.includes(u));
  return cand||null;
 }
 function needsMeasurement(a){
  const reasons=a?.review_reasons||[],hit=reasons.find(r=>MEASURE_REASONS.some(p=>r===p||r.startsWith(p+':')))||null;
  const outside=(!a?.room||a.room.room_type==='exterior'||a.room.room_type==='parking')&&!!a?.label&&!a?.takeoff?.inputs?.measured_with_asset;
  const unit=measureUnit(a);
  return {needed:!!hit&&!!unit&&outside,offer:!!unit&&outside,reason:hit,unit};
 }

 /* ---------- capture status (docs/api/PROPERTY_CAPTURE.md section 2; basis/capture/messages.py) ---------- */
 const CAPTURE_FAILED={'BASIS-CAPTURE-006':'The address did not lead to a place. Check the address or drop the pin on the house, then take the pictures again.','BASIS-CAPTURE-005':'The pictures came out blank. Take them again later.','BASIS-CAPTURE-007':'Taking the pictures took too long. Take them again later.','BASIS-CAPTURE-008':'Taking the pictures stopped unexpectedly. Take them again later.','BASIS-CAPTURE-011':'Property pictures stayed paused too long and stopped. Take them again later.','BASIS-CAPTURE-017':'Property pictures could not start. Take them again in a minute.'};
 const CAPTURE_PART={'BASIS-CAPTURE-018':'Earlier pictures of the property could not be taken this time; today’s pictures are ready.','BASIS-CAPTURE-019':'There are no street-level pictures near this property.','BASIS-CAPTURE-020':'Street-level pictures are not available yet.','BASIS-CAPTURE-021':'The date of some pictures could not be read; they are marked undated.'};
 const FAILED_OTHER='Property pictures could not be taken. Take them again later.';
 // How long a capture takes (docs/review_2026-10-15/PROPERTY_CAPTURE.md section 7.1).
 const ESTIMATE='They are usually ready within two minutes; you can keep working.';
 function clock(iso,{now=Date.now(),timeZone,locale='en-US'}={}){
  const d=new Date(iso);if(isNaN(d))return null;
  const opt={hour:'numeric',minute:'2-digit',timeZone},sameDay=new Intl.DateTimeFormat(locale,{dateStyle:'short',timeZone}).format(d)===new Intl.DateTimeFormat(locale,{dateStyle:'short',timeZone}).format(new Date(now));
  const time=new Intl.DateTimeFormat(locale,opt).format(d).replace(/\s?([AP])M$/,(m,a)=>` ${a.toLowerCase()}m`);
  return sameDay?time:`${new Intl.DateTimeFormat(locale,{weekday:'short',month:'short',day:'numeric',timeZone}).format(d)}, ${time}`;
 }
 function captureStatus(capture,{pin=null,now=Date.now(),timeZone,locale}={}){
  if(!capture){
   if(!pin)return {state:'none',tone:'idle',title:'No property pictures yet',text:'Add the property’s address, or drop a pin on the house, and Basis takes dated pictures of it in the background.',action:'pin',polling:false};
   return {state:'none',tone:'idle',title:'No property pictures yet',text:'Take the pictures: Basis captures today’s views, the four sides and earlier dates in the background.',action:'start',polling:false};
  }
  const code=capture.code||null;
  switch(capture.status){
   case 'queued':return {state:'queued',tone:'progress',title:'Queued',text:`${capture.message||'Property pictures are queued.'} ${ESTIMATE}`,action:null,polling:true,code};
   case 'running':return {state:'running',tone:'progress',title:'Taking pictures',text:`${capture.message||'Taking property pictures.'} ${ESTIMATE}`,action:null,polling:true,code};
   case 'waiting':{
    const at=capture.resume_at?clock(capture.resume_at,{now,timeZone,locale}):null;
    const paused=code==='BASIS-CAPTURE-009'||code==='BASIS-CAPTURE-010'||(!!at&&!/another property/i.test(capture.message||''));
    if(paused)return {state:'paused',tone:'review',title:'Paused',text:at?`Property pictures are paused until ${at}; they will resume on their own.`:(capture.message||'Property pictures are paused; they will resume on their own.'),action:null,polling:true,code,resumeAt:capture.resume_at||null};
    return {state:'waiting',tone:'progress',title:'Waiting its turn',text:capture.message||'Waiting for another property’s pictures to finish; this one is next.',action:null,polling:true,code};
   }
   case 'failed':return {state:'failed',tone:'danger',title:'Not taken',text:capture.message||CAPTURE_FAILED[code]||FAILED_OTHER,action:'retake',polling:false,code};
   case 'succeeded':{
    const stale=(capture.views||[]).some(v=>v.stale)||(pin&&capture.pin_version!=null&&pin.version!==capture.pin_version&&pin.lat!=null);
    return {state:'ready',tone:stale?'review':'done',title:stale?'Taken around an earlier pin':'Ready',text:stale?'The pin moved after these pictures were taken. Take them again around the new pin.':(capture.message||'Property pictures are ready.'),action:stale?'retake':'retake-optional',polling:false,code,stale};
   }
   default:return {state:'unknown',tone:'idle',title:'Unknown',text:capture.message||'',action:null,polling:false,code};
  }
 }
 const noteText=n=>n?.message||CAPTURE_PART[n?.code]||FAILED_OTHER;
 function gatherStatus(gather){
  if(!gather)return {state:'none',tone:'idle',title:'Not gathered yet',text:'Public imagery, the parcel and the building’s heights are gathered once the pin is on the house.',polling:false};
  const failedAll=gather.status==='succeeded'&&(gather.sources||[]).length&&(gather.sources||[]).every(s=>s.status==='failed');
  switch(gather.status){
   case 'queued':case 'running':return {state:gather.status,tone:'progress',title:gather.status==='queued'?'Queued':'Gathering',text:gather.message||'Gathering public records of the property.',polling:true};
   case 'failed':return {state:'failed',tone:'danger',title:'Not gathered',text:gather.message||'Public records could not be gathered. Ask for them again later.',polling:false,code:gather.code};
   default:return {state:'ready',stale:!!gather.stale,tone:gather.stale?'review':'done',title:gather.stale?'Gathered around an earlier pin':'Ready',text:gather.stale?'The pin moved after these records were gathered. Gather them again around the new pin.':failedAll?'No public source answered this time. Gather them again later.':(gather.message||'Public records are ready.'),polling:false,stale:!!gather.stale};
  }
 }

 /* ---------- the views ---------- */
 const SIDES=['side-south','side-west','side-north','side-east'];
 const SIDE_TEXT={'side-south':'South face','side-west':'West face','side-north':'North face','side-east':'East face'};
 const LOOKING={'side-south':'looking north','side-west':'looking east','side-north':'looking south','side-east':'looking west'};
 const dateOf=v=>v?.imagery_on||(typeof v?.label==='string'&&/^history-(\d{4}-\d{2}-\d{2})$/.exec(v.label)?.[1])||null;
 function viewsOf(capture){
  const views=capture?.views||[],by=l=>views.find(v=>v.label===l)||null;
  const history=views.filter(v=>v.kind==='history').slice().sort((a,b)=>String(dateOf(b)||'').localeCompare(String(dateOf(a)||'')));
  return {close:by('aerial-close'),wide:by('aerial-wide'),sides:SIDES.map(by).filter(Boolean),history,street:views.find(v=>v.kind==='street')||null,all:views};
 }
 const yearOnly=d=>typeof d==='string'&&/-12-31$/.test(d);
 function leafText(leaf){return leaf==='leaf-off'?'Trees bare':leaf==='leaf-on'?'Trees in leaf':'Season unknown'}
 // "Jun 18, 2026" (UTC calendar date; an imagery date has no time), or "2021 (year only)".
 function dateText(d){
  if(!d)return 'Undated';
  if(yearOnly(d))return `${d.slice(0,4)} (year only)`;
  const [y,m,day]=d.split('-').map(Number);return new Date(Date.UTC(y,m-1,day)).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'});
 }
 // The history strip: newest first, each with its marks. `atService` is the dated view nearest the
 // placed-in-service date; `suggested` the capture's measure_on.
 function historyStrip(views,{placedInService=null,measureOn=null}={}){
  const items=views.map(v=>({view:v,id:v.id,date:dateOf(v),undated:!dateOf(v),yearOnly:yearOnly(dateOf(v)),leaf:v.leaf||'unknown',suggested:!!measureOn&&v.id===measureOn,atService:false,measurable:measurable(v)}));
  if(placedInService){
   const t=Date.parse(placedInService);let best=null;
   for(const it of items)if(it.date&&!it.yearOnly){const d=Math.abs(Date.parse(it.date)-t);if(!best||d<best.d)best={it,d}}
   if(best)best.it.atService=true;
  }
  return items.sort((a,b)=>String(b.date||'').localeCompare(String(a.date||'')));
 }
 // The view to measure on by default: measure_on, else today's close aerial.
 function defaultMeasureView(capture){const v=viewsOf(capture);return (capture?.measure_on&&v.all.find(x=>x.id===capture.measure_on&&measurable(x)))||(measurable(v.close)?v.close:null)}
 // Compass from a heading: "looking south from the street".
 function compass(h){if(h==null)return null;const names=['north','northeast','east','southeast','south','southwest','west','northwest'];return names[Math.round(((Number(h)%360)+360)%360/45)%8]}

 /* ---------- public views ---------- */
 const COARSE_PUBLIC_M=0.5;// NAIP-class pixels (0.6 to 1 m): for seeing what existed, not for measuring (FRONTEND_BACKLOG 19.2)
 function publicSeries(pub){
  const items=[...(pub?.history||[])];if(pub?.current&&!items.some(v=>v.id===pub.current.id))items.unshift(pub.current);
  return items.sort((a,b)=>(b.year||0)-(a.year||0)).map(v=>({view:v,id:v.id,year:v.year,yearLabel:v.year_label||(v.year?String(v.year):'Year unknown'),leafOff:v.leaf_off,coarse:num(v.pixel_size_m)>COARSE_PUBLIC_M,current:pub?.current?.id===v.id}));
 }
 const creditLine=v=>[v.credit,v.year_label||v.year].filter(Boolean).join(' · ');

 /* ---------- the measurement body ---------- */
 // Pixel shapes traced on a view -> the correction's `measurement` (lonlat).
 // shapes: [{kind:'area', ring:[[x,y]...], cutouts:[[[x,y]...]...]}] or [{kind:'line', path:[...]}]
 function measurementBody({unit,shapes,view,heightFt=null}){
  const g=georef(view);if(!g)throw new Error('This view cannot be measured on.');
  const ll=pts=>pts.map(([x,y])=>g.toLonLat(x,y).map(v=>Math.round(v*1e9)/1e9));
  const out={unit,space:'lonlat',shapes:shapes.map(s=>s.kind==='line'?{kind:'line',path:ll(s.path)}:{kind:'area',ring:ll(s.ring),...(s.cutouts?.length?{cutouts:s.cutouts.map(ll)}:{})}),image:imageOf(view)};
  if(heightFt!=null&&heightFt!=='')out.height_ft=Number(heightFt);
  return out;
 }
 // What the measurement says about its image: a property view's source line and imagery date; a
 // public view's credit and year (a year alone is not a date: captured_on is left out, and the
 // server flags measure:IMAGE_UNDATED, which the engineer is told before saving).
 function imageOf(view){
  const public_=!!view.source_id,s=num(view.pixel_size_m);
  const source=public_?`${view.credit||view.source_id}, ${view.year_label||view.year||'year unknown'} (public imagery ${view.source_id})`:view.source||'Aerial imagery';
  const image={source:source.slice(0,300)};
  if(!public_&&view.imagery_on&&/^\d{4}-\d{2}-\d{2}$/.test(view.imagery_on))image.captured_on=view.imagery_on;
  if(s>0)image.pixel_size_m=s;
  return image;
 }

 /* ---------- parcels, site check, lidar ---------- */
 function polygons(geometry){if(!geometry)return [];return geometry.type==='Polygon'?[geometry.coordinates]:geometry.type==='MultiPolygon'?geometry.coordinates:[]}
 // Every ring of a parcel as pixel points on a view (outer rings and holes).
 function parcelRings(parcel,g){if(!g)return [];return polygons(parcel?.geometry).flatMap(poly=>poly.map(ring=>ring.map(([lon,lat])=>g.toPixel(lon,lat))))}
 const sqft=v=>v==null?null:Number(v).toLocaleString('en-US',{maximumFractionDigits:0});
 function siteCheckText(sc){
  if(!sc)return null;
  const diff=sc.difference_pct==null?'':` (${Number(sc.difference_pct)>=0?'+':'−'}${Math.abs(Number(sc.difference_pct)).toFixed(1)} %)`;
  switch(sc.status){
   case 'agree':return {tone:'done',title:'Lot area agrees with the appraisal',text:sc.message||`The parcel’s ${sqft(sc.parcel_sqft)} SF is within ${sc.tolerance_pct} % of the appraisal’s ${sqft(sc.appraisal_sqft)} SF${diff}.`};
   case 'adjacent_match':return {tone:'done',title:'The site is several parcels',text:sc.message||`The owner’s adjacent parcels together (${sqft(sc.combined_sqft)} SF) match the appraisal’s ${sqft(sc.appraisal_sqft)} SF.`};
   case 'disagree':return {tone:'review',title:'Lot area differs from the appraisal',text:sc.message||`The parcel is ${sqft(sc.parcel_sqft)} SF and the appraisal says ${sqft(sc.appraisal_sqft)} SF${diff}; the limit is ${sc.tolerance_pct} %.`,flag:true};
   case 'no_appraisal':return null;
   case 'no_parcel':return {tone:'idle',title:'No parcel found',text:sc.message||'No parcel was found under the pin.'};
   default:return null;
  }
 }
 function lidarFacts(l){
  if(!l)return null;const b=l.building,m2ft=v=>v==null?null:Number(v)*FT_PER_M;
  if(!b)return {project:l.project,year:l.year,building:null,text:'No building was found under the pin in the elevation survey.'};
  return {project:l.project,year:l.year,building:{peakFt:m2ft(b.peak_m),eaveFt:m2ft(b.eave_m),maxFt:m2ft(b.max_m),footprintSf:b.footprint_sqft!=null?Number(b.footprint_sqft):Number(b.footprint_m2)*FT_PER_M*FT_PER_M,distanceM:b.distance_m}};
 }

 /* ---------- renovation evidence (docs/api/RENOVATIONS.md, `aerial`, `exterior_brackets`) ---------- */
 // Two dated views chosen in the compare: the earlier is the last without the work, the later the first with it.
 function bracket(a,b){
  const da=a?.date,db=b?.date;if(!da||!db)return {error:'Both views need a date.'};
  if(da===db)return {error:'Choose two different dates.'};
  const [before,after]=da<db?[a,b]:[b,a];
  return {bracket:{last_without:{date:before.date,reference:String(before.reference).slice(0,300)},first_with:{date:after.date,reference:String(after.reference).slice(0,300)}}};
 }
 // The renovations document with one bracket set (a project's `aerial`, or a component label's
 // entry in `exterior_brackets`), keeping everything else as it was.
 function withBracket(doc,target,br){
  const base=doc?JSON.parse(JSON.stringify(doc)):{projects:[],acquisition_bonus_rate:null,exterior_brackets:[]};
  base.projects||=[];base.exterior_brackets||=[];
  if(target.kind==='project'){const p=base.projects.find(x=>x.key===target.key);if(!p)throw new Error('No such project.');p.aerial=br}
  else{const i=base.exterior_brackets.findIndex(x=>x.label===target.label);if(i>=0)base.exterior_brackets[i]={label:target.label,bracket:br};else base.exterior_brackets.push({label:target.label,bracket:br})}
  return base;
 }

 /* ---------- capabilities ---------- */
 // GET /v1/capabilities: anything but true is false, so today's API (404, or a body without these
 // members) shows exactly what it shows now.
 function caps(body){const b=body&&typeof body==='object'?body:{};return {propertyCapture:b.property_capture===true,publicGeo:b.public_geo===true,tracedMeasurement:b.traced_measurement===true}}
 const anyProperty=c=>!!(c&&(c.propertyCapture||c.publicGeo));

 const api={FT_PER_M,radii,round2,measure,problems,formula,reviewFlags,expectedErrorPct,MeasurementError,georef,measurable,toMercator,fromMercator,
  needsMeasurement,measureUnit,captureStatus,gatherStatus,noteText,clock,viewsOf,historyStrip,defaultMeasureView,leafText,dateText,yearOnly,dateOf,SIDE_TEXT,LOOKING,compass,
  publicSeries,creditLine,measurementBody,imageOf,parcelRings,siteCheckText,lidarFacts,bracket,withBracket,caps,anyProperty,ringProblem,CAPTURE_FAILED,CAPTURE_PART};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisPropertyModel=api;
})(typeof globalThis==='object'?globalThis:this);
