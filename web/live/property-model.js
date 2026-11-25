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
 // Provenance (traced.py, EVAL_REPORT 104): who drew it, whether it was verified, the picture,
 // the measurement it replaced. The same keys and the same words as the server.
 const TOP_KEYS=['unit','space','shapes','scale','image','height_ft','drawn_by','verified','view','original'];
 const VIEW_KEYS=['kind','id','label','width','height'],VIEW_KINDS=['capture','public','document'];
 const MODEL_RE=/^model:[A-Za-z0-9][A-Za-z0-9._/-]{0,99}@[A-Za-z0-9][A-Za-z0-9._+-]{0,49}$/;
 // Python's repr of a JSON value, shortened as traced._short does (for the messages).
 function pyRepr(v){
  if(v===null||v===undefined)return 'None';if(v===true)return 'True';if(v===false)return 'False';
  if(typeof v==='string'){const q=v.includes("'")&&!v.includes('"')?'"':"'";return q+v.replace(/\\/g,'\\\\').replace(/\u0000/g,'\\x00').split(q).join('\\'+q)+q}
  if(typeof v==='number')return String(v);
  if(Array.isArray(v))return '['+v.map(pyRepr).join(', ')+']';
  return '{'+Object.entries(v).map(([k,x])=>pyRepr(k)+': '+pyRepr(x)).join(', ')+'}';
 }
 const short=v=>{const t=pyRepr(v);return t.length<=60?t:t.slice(0,57)+'...'};
 function provenance(m,add,nested,prefix){
  const out={};
  if(m.drawn_by!=null){if(typeof m.drawn_by==='string'&&(m.drawn_by==='engineer'||MODEL_RE.test(m.drawn_by)))out.drawn_by=m.drawn_by;else add('.drawn_by',`must be engineer or model:<name>@<version> (letters, digits and . _ - / +), got ${short(m.drawn_by)}`)}
  if(m.verified!=null){if(typeof m.verified==='boolean')out.verified=m.verified;else add('.verified',`must be true or false, got ${short(m.verified)}`)}
  const v=m.view;
  if(v!=null){
   if(typeof v!=='object'||Array.isArray(v))add('.view','must be an object with kind and an id or a label');
   else{
    const unknown=Object.keys(v).filter(k=>!VIEW_KEYS.includes(k)).sort();if(unknown.length)add('.view',`has unknown keys: ${unknown.join(', ')}`);
    if(!VIEW_KINDS.includes(v.kind))add('.view.kind',`must be capture, public or document, got ${short(v.kind)}`);
    for(const k of ['id','label'])if(v[k]!=null&&!(typeof v[k]==='string'&&v[k].trim()&&v[k].trim().length<=200&&!v[k].includes('\u0000')))add(`.view.${k}`,`must be text of 1 to 200 characters, got ${short(v[k])}`);
    if(v.id==null&&v.label==null)add('.view','must name the picture by its id or its label');
    if((v.width!=null)!==(v.height!=null))add('.view','takes width and height together (the pixel frame of the points)');
    else if(v.width!=null)for(const k of ['width','height']){const x=v[k];if(typeof x!=='number'||!Number.isFinite(x))add(`.view.${k}`,`must be a number, got ${short(x)}`);else if(!(x>0&&x<=100000))add(`.view.${k}`,`must be above 0 and at most 100000, got ${short(x)}`)}
    out.view=v;
   }
  }
  if(m.original!=null){
   if(nested)add('.original','cannot hold an original of its own: an edit keeps the first original only');
   else{try{out.original=measure(m.original,prefix+'.original',true)}catch(e){if(!(e instanceof MeasurementError))throw e;out.originalProblems=e.problems}}
  }
  return out;
 }
 // measure(body) -> the computed record the server stores beside the shape, or throws with every problem.
 function measure(m,prefix='measurement',nested=false){
  const problems=[],add=(w,t)=>problems.push(`${prefix}${w} ${t}`);
  if(!m||typeof m!=='object'||Array.isArray(m))throw new MeasurementError([`${prefix} must be an object (unit, space, shapes, image)`]);
  const unknownTop=Object.keys(m).filter(k=>!TOP_KEYS.includes(k)).sort();if(unknownTop.length)add('',`has unknown keys: ${unknownTop.join(', ')}`);
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
  const prov=provenance(m,add,nested,prefix);if(prov.originalProblems)problems.push(...prov.originalProblems);
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
  if(!(Number(quantity)>0)||Number(quantity)>MAX_QUANTITY)throw new MeasurementError([`${prefix} comes to ${raw.toFixed(4)} ${unit}; the quantity must be above 0 and at most 10,000,000 (after rounding to 0.01)`]);
  const pixelFt=space==='image'?feetPerPx:pixelSizeM===null?null:pixelSizeM*FT_PER_M;
  const out={unit,quantity,area_sf:areaSf===null?null:round2(areaSf),length_ft:lengthFt===null?null:round2(lengthFt),method:space==='image'?'image_scale':'local_tangent_plane_wgs84',
   expected_error_pct:expectedErrorPct({areaSf,lengthFt,edgesFt:edges,lines:shapes.length,pixelFt,scalePx}),shape_count:shapes.length,corner_count:corners,cutout_count:cutouts,scale_ft_per_px:feetPerPx};
  // The original's record is its body with what it computes (traced.Traced.record()).
  if(prov.original)out.original={...JSON.parse(JSON.stringify(m.original)),...prov.original};
  return out;
 }
 function problems(m){try{measure(m);return []}catch(e){if(e instanceof MeasurementError)return e.problems;throw e}}
 // The takeoff's sentence (traced.py formula), for the row before the server answers. A model's
 // draft reads "drafted by Basis" (the model's name is the record's, not the sentence's).
 const whoDrew=d=>d!=null&&d!=='engineer'?'drafted by Basis':'traced by the engineer';
 const qtext=q=>Number(q).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
 function formula(r,{source,captured_on,height_ft,drawn_by,verified}={}){
  const n=r.shape_count,kind=r.area_sf!==null?'area':'line',noun=kind+(n!==1?'s':'');
  let counts=kind==='area'?`${n} ${noun} of ${r.corner_count} corners${r.cutout_count?` (${r.cutout_count} cut out)`:''}`:`${n} ${noun} of ${r.corner_count} points${height_ft!=null?` x ${height_ft} ft high`:''}`;
  // Image space names its scale as the server does (`:.4g`: four significant digits, no trailing zeros).
  const scale=r.scale_ft_per_px!=null?`, scale ${Number(Number(r.scale_ft_per_px).toPrecision(4))} ft per pixel`:'';
  let tail='';if(verified!=null)tail+=verified?'; verified by the engineer':'; not verified';
  if(r.original)tail+=`; replaces ${qtext(r.original.quantity)} ${r.original.unit} ${whoDrew(r.original.drawn_by)}`;
  return `${qtext(r.quantity)} ${r.unit} ${whoDrew(drawn_by)} on ${source} (${captured_on?'captured '+captured_on:'undated'}), ${counts}${scale}; ${r.expected_error_pct===null?'expected error unknown (no pixel size)':`expected error up to ${r.expected_error_pct} %`}${tail}`;
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
 // Measuring, widened (EVAL_REPORT 104): needsMeasurement's aerial answer unchanged (`aerial`,
 // `needed`), plus any component with an area or a length, inside or out, on one of the study's
 // photos scaled by a known length (`photo`), `photoNeeded` for a row held for a quantity that
 // only a photo can size (inside the building), `any` for a row that should be measured at all,
 // and `stored`, the measurement a sent correction (or, later, a model's draft) left on the row.
 function measureOffer(a){
  const n=needsMeasurement(a),own=!!a?.label&&!a?.takeoff?.inputs?.measured_with_asset,photo=!!n.unit&&own;
  const raw=a?.takeoff?.inputs?.measurement,stored=raw&&typeof raw==='object'&&!Array.isArray(raw)&&Array.isArray(raw.shapes)?raw:null;
  const photoNeeded=!!n.reason&&photo&&!n.offer;
  return {...n,aerial:n.offer,photo,photoNeeded,any:n.offer||photo,anyNeeded:n.needed||photoNeeded,stored};
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

 /* ---------- the measure tool (EVAL_REPORT 104; docs/review_2026-10-15/AI_MEASUREMENT_PLAN.md section 3) ----------
  * Shapes are drawn in the picture's own pixels. A frame says how two pixels become feet, the
  * way the server measures the body the tool sends:
  *   {space:'lonlat', toLonLat}  a georeferenced view: each segment on the tangent plane at its own
  *                                middle latitude (traced.lonlat_path_length_ft), on the lon/lat the
  *                                body carries (rounded to 1e-9 degree, as measurementBody rounds);
  *   {space:'image', feetPerPx}  a photo or plan with a scale: planar pixels times the scale.
  * So a line's labels sum to the server's length_ft exactly, and a ring's to its perimeter (the
  * closed path measured the same way). Labels are feet and inches to the nearest inch; totals come
  * from the unrounded values (EagleView: totals "based on the non-rounded values"). */
 const LL_DIGITS=1e9;
 const roundLL=v=>Math.round(v*LL_DIGITS)/LL_DIGITS;
 function frameOf(view,{scale}={}){
  if(scale){const s=scaleFeetPerPx(scale);return s?{space:'image',feetPerPx:s}:null}
  const g=georef(view);if(!g)return null;
  return {space:'lonlat',toLonLat:(x,y)=>g.toLonLat(x,y).map(roundLL),toPixel:g.toPixel};
 }
 // {points:[[x,y],[x,y]], length, unit:'ft'|'m'} -> feet per pixel, or null.
 function scaleFeetPerPx(s){
  const p=s?.points;if(!Array.isArray(p)||p.length!==2)return null;
  const d=dist(p[0],p[1]),L=Number(s.length);if(!(d>=2)||!(L>0))return null;
  return (s.unit==='m'?L*FT_PER_M:L)/d;
 }
 function segmentFt(a,b,frame){
  if(frame.space==='image')return dist(a,b)*frame.feetPerPx;
  return lonlatPathFt([frame.toLonLat(a[0],a[1]),frame.toLonLat(b[0],b[1])]);
 }
 // The length of every edge, in order: a ring's closing edge last.
 function edgeLengths(points,closed,frame){
  const n=points.length,out=[];
  for(let i=0;i<(closed?n:n-1);i++)out.push(segmentFt(points[i],points[(i+1)%n],frame));
  return out;
 }
 const sum=a=>a.reduce((t,x)=>t+x,0);

 /* ----- lengths as people write them ----- */
 // 54'-3" (the nearest inch); short: 54' (the nearest foot).
 function ftIn(ft,{short=false}={}){
  if(!Number.isFinite(ft))return '—';
  if(short)return `${Math.round(ft).toLocaleString('en-US')}'`;
  const inches=Math.round(ft*12),f=Math.floor(inches/12),i=inches-f*12;
  return `${f.toLocaleString('en-US')}'-${i}"`;
 }
 const ftInWords=ft=>{const inches=Math.round(ft*12),f=Math.floor(inches/12),i=inches-f*12;return `${f.toLocaleString('en-US')} feet ${i} ${i===1?'inch':'inches'}`};
 const MAX_TYPED_FT=100000;
 // A typed length -> {ft} or {error}. Accepts 54'3", 54' 3", 54'-3", 54 ft 3 in, 54.25, 54.25 ft,
 // 54.25', 36", 36 in, 16.5 m, 1650 cm; curly quotes and primes; commas in thousands.
 function parseLength(raw){
  const s=String(raw??'').trim().toLowerCase().replace(/[’′‘`´]/g,"'").replace(/[”″“]/g,'"').replace(/''/g,'"').replace(/(\d),(?=\d{3}\b)/g,'$1').replace(/\s+/g,' ');
  const HINT='Type a length such as 54\'-3", 54.25 ft or 16.5 m.';
  if(!s)return {error:HINT};
  const N='(\\d+(?:\\.\\d*)?|\\.\\d+)';
  let ft=null,m;
  if((m=s.match(new RegExp(`^${N} ?(?:'|ft\\.?|feet|foot)(?: ?-? ?${N} ?(?:"|in\\.?|inch|inches)?)?$`)))){
   const inch=m[2]===undefined?0:Number(m[2]);
   if(inch>=12)return {error:`${m[2]} inches is a foot or more: write ${Number(m[1])+Math.floor(inch/12)}'-${+(inch%12).toFixed(4)}" instead.`};
   ft=Number(m[1])+inch/12;
  }
  else if((m=s.match(new RegExp(`^${N} ?(?:"|in\\.?|inch|inches)$`))))ft=Number(m[1])/12;
  else if((m=s.match(new RegExp(`^${N} ?(?:m|meters?|metres?)$`))))ft=Number(m[1])*FT_PER_M;
  else if((m=s.match(new RegExp(`^${N} ?(?:cm|centimeters?|centimetres?)$`))))ft=Number(m[1])*FT_PER_M/100;
  else if((m=s.match(new RegExp(`^${N}$`))))ft=Number(m[1]);
  else return {error:`“${String(raw).trim().slice(0,40)}” is not a length. ${HINT}`};
  if(!(ft>0))return {error:'A length is above zero.'};
  if(ft>MAX_TYPED_FT)return {error:`A length is at most ${MAX_TYPED_FT.toLocaleString('en-US')} ft.`};
  return {ft};
 }
 // Area as SF (SY too when the unit is SY), length as LF.
 const areaText=(sf,unit)=>unit==='SY'?`${(sf/SF_PER_SY).toLocaleString('en-US',{maximumFractionDigits:1})} SY`:`${sf.toLocaleString('en-US',{maximumFractionDigits:sf<100?1:0})} SF`;
 const lfText=ft=>`${ft.toLocaleString('en-US',{maximumFractionDigits:ft<100?1:0})} LF`;

 /* ----- where the labels go ----- */
 // One label per edge at its middle, outside the shape (a ring's outward normal; a line's outer
 // side, its points read as a ring), turned along the edge but never upside down (the angle in (-90, 90]), in full when the
 // edge is long enough on screen, as whole feet when only that fits, else hidden (the edge list
 // in the panel still has it). k: picture pixels per screen pixel; font: the label's size in
 // screen pixels.
 const CHAR_EM=0.62,LABEL_PAD=10,LABEL_GAP=6;
 const labelWidth=(text,font)=>text.length*CHAR_EM*font+LABEL_PAD;
 function edgeLabels(points,closed,lengthsFt,{k=1,font=12}={}){
  // A line's labels go on the side away from the bulk of its bends (its points read as a ring).
  const n=points.length,edges=closed?n:n-1,sign=shoelace(points)<0?-1:1,out=[];
  for(let i=0;i<edges;i++){
   const a=points[i],b=points[(i+1)%n],dx=b[0]-a[0],dy=b[1]-a[1],len=Math.hypot(dx,dy)||1e-12;
   const full=ftIn(lengthsFt[i]),shortText=ftIn(lengthsFt[i],{short:true}),onScreen=len/k;
   const mode=onScreen>=labelWidth(full,font)+LABEL_GAP?'full':onScreen>=labelWidth(shortText,font)+LABEL_GAP?'short':'hidden';
   // y grows downwards: with a positive shoelace the right-hand normal (dy, -dx) points out.
   const nx=dy/len*sign,ny=-dx/len*sign,off=font*0.95*k;
   let angle=Math.atan2(dy,dx)*180/Math.PI;if(angle>90)angle-=180;else if(angle<=-90)angle+=180;
   out.push({i,x:(a[0]+b[0])/2+nx*off,y:(a[1]+b[1])/2+ny*off,mx:(a[0]+b[0])/2,my:(a[1]+b[1])/2,angle,mode,text:mode==='full'?full:mode==='short'?shortText:'',full,words:ftInWords(lengthsFt[i]),ft:lengthsFt[i]});
  }
  return out;
 }
 // A point inside a ring for its area label: the centroid, or (a concave ring whose centroid is
 // outside) the middle of the widest stretch of the horizontal through it.
 function labelPoint(ring){
  const a=shoelace(ring);let cx=0,cy=0;
  if(Math.abs(a)>1e-12){const [x0,y0]=ring[0];for(let i=0;i<ring.length;i++){const [x1,y1]=ring[i],[x2,y2]=ring[(i+1)%ring.length],c=(x1-x0)*(y2-y0)-(x2-x0)*(y1-y0);cx+=(x1-x0+x2-x0)*c;cy+=(y1-y0+y2-y0)*c}cx=x0+cx/(6*a);cy=y0+cy/(6*a)}
  else{cx=sum(ring.map(p=>p[0]))/ring.length;cy=sum(ring.map(p=>p[1]))/ring.length}
  if(insideOrOn([cx,cy],ring))return [cx,cy];
  const xs=[];for(let i=0;i<ring.length;i++){const p=ring[i],q=ring[(i+1)%ring.length];if((p[1]>cy)!==(q[1]>cy))xs.push(p[0]+(cy-p[1])*(q[0]-p[0])/(q[1]-p[1]))}
  xs.sort((u,v)=>u-v);let best=null;for(let i=0;i+1<xs.length;i+=2)if(!best||xs[i+1]-xs[i]>best[1]-best[0])best=[xs[i],xs[i+1]];
  return best?[(best[0]+best[1])/2,cy]:[cx,cy];
 }
 // What a shape says at its middle (areas: area and perimeter; a wall face: length x height).
 function shapeSummary(shape,frame,{unit,heightFt=null}={}){
  if(shape.kind==='line'){const ft=sum(edgeLengths(shape.path,false,frame));return {lengthFt:ft,text:heightFt>0?`${ftIn(ft)} × ${ftIn(heightFt)} = ${areaText(ft*heightFt,unit)}`:lfText(ft)}}
  // The area as the server computes it: one tangent plane per shape (the ring's middle latitude,
  // from its first corner), the cutouts on the same plane; image space planar.
  const rings=[shape.ring,...(shape.cutouts||[])];let flat;
  if(frame.space==='image')flat=rings.map(r=>r.map(([x,y])=>[x*frame.feetPerPx,y*frame.feetPerPx]));
  else{const ll=rings.map(r=>r.map(([x,y])=>frame.toLonLat(x,y))),la=ll[0].map(p=>p[1]),lat0=(Math.min(...la)+Math.max(...la))/2;flat=ll.map(r=>plane(r,ll[0][0],lat0))}
  const areaSf=Math.abs(shoelace(flat[0]))-sum(flat.slice(1).map(c=>Math.abs(shoelace(c)))),perimeterFt=sum(edgeLengths(shape.ring,true,frame));
  return {areaSf,perimeterFt,text:`${areaText(areaSf,unit)} · ${lfText(perimeterFt)}`,area:areaText(areaSf,unit),perimeter:`${lfText(perimeterFt)} perimeter`};
 }

 /* ----- editing (every edit returns new points; the caller checks and records it) ----- */
 const movePoint=(points,i,p)=>points.map((q,j)=>j===i?[p[0],p[1]]:q);
 // A corner added in the middle of edge i (between corner i and the next).
 const insertPoint=(points,i,p)=>[...points.slice(0,i+1),[p[0],p[1]],...points.slice(i+1)];
 const removePoint=(points,i)=>points.filter((_,j)=>j!==i);
 const minPoints=closed=>closed?3:2;
 // Fusion's sketch dimension: edge i gets exactly `ft` by moving its far corner (the next one)
 // along the edge's own direction. Iterated on the frame's own length (lon/lat is not exactly
 // linear in pixels); image space is exact in one step. When the edge meets both its neighbours
 // at right angles (within RIGHT_TOL_DEG), the far side slides with the corner (the corner after
 // it moves by the same amount), so a rectangle stays a rectangle, as a constrained sketch keeps
 // its perpendicular constraints; otherwise only the far corner moves and the next edge stretches.
 // 3 degrees: a hand or model trace of a square corner is often 2 degrees off (the sample draft, 2.05).
 const RIGHT_TOL_DEG=3;
 function angleAt(prev,at,next){const a=[prev[0]-at[0],prev[1]-at[1]],b=[next[0]-at[0],next[1]-at[1]],d=Math.hypot(...a)*Math.hypot(...b);if(!d)return NaN;return Math.acos(Math.max(-1,Math.min(1,(a[0]*b[0]+a[1]*b[1])/d)))*180/Math.PI}
 const isSquare=(p,q,r)=>Math.abs(angleAt(p,q,r)-90)<=RIGHT_TOL_DEG;
 function setEdgeLength(points,i,ft,closed,frame){
  const n=points.length,j=(i+1)%n;if(i<0||i>=(closed?n:n-1))throw new Error('No such edge.');
  const a=points[i],b=points[j],len=dist(a,b),now=segmentFt(a,b,frame);if(!(len>0)||!(now>0))throw new Error('This edge has no length to change.');
  const u=[(b[0]-a[0])/len,(b[1]-a[1])/len];let s=len*ft/now;
  for(let it=0;it<6;it++){const L=segmentFt(a,[a[0]+u[0]*s,a[1]+u[1]*s],frame);if(Math.abs(L-ft)<=1e-9*ft)break;s*=ft/L}
  const to=[a[0]+u[0]*s,a[1]+u[1]*s],out=movePoint(points,j,to);
  if(closed&&n>=4){const h=(i-1+n)%n,k=(j+1)%n;
   if(k!==i&&h!==j&&isSquare(points[h],a,b)&&isSquare(a,b,points[k])){const d=[to[0]-b[0],to[1]-b[1]];out[k]=[points[k][0]+d[0],points[k][1]+d[1]]}}
  return out;
 }
 // The nearest of the shape's own corners, the parcel's corners, then the parcel's edges, within
 // `radius` picture pixels (a few screen pixels times k). Corners win over edges.
 function nearestOnSegment(p,a,b){const dx=b[0]-a[0],dy=b[1]-a[1],L=dx*dx+dy*dy;let t=L?((p[0]-a[0])*dx+(p[1]-a[1])*dy)/L:0;t=Math.max(0,Math.min(1,t));return [a[0]+t*dx,a[1]+t*dy]}
 function snapPoint(p,{corners=[],segments=[],radius=0}={}){
  let best=null;
  for(const c of corners){const d=dist(p,c);if(d<=radius&&(!best||d<best.d))best={point:[c[0],c[1]],d,to:'corner'}}
  if(best)return best;
  for(const [a,b] of segments){const q=nearestOnSegment(p,a,b),d=dist(p,q);if(d<=radius&&(!best||d<best.d))best={point:q,d,to:'edge'}}
  return best||{point:[p[0],p[1]],d:0,to:null};
 }
 const ringSegments=rings=>rings.flatMap(r=>r.map((a,i)=>[a,r[(i+1)%r.length]]));
 // A point off the picture cannot be seen or edited; the server refuses negative pixels too.
 const onPicture=(p,w,h)=>p[0]>=0&&p[1]>=0&&p[0]<=w&&p[1]<=h;

 /* ----- undo and redo: snapshots of what the engineer drew ----- */
 const HISTORY_LIMIT=200;
 const snapshotOf=t=>JSON.stringify({shapes:t.shapes,draft:t.draft,scale:t.scale?{points:t.scale.points,length:t.scale.length,unit:t.scale.unit}:null});
 function restoreSnapshot(t,snap){const o=JSON.parse(snap);t.shapes=o.shapes;t.draft=o.draft;if(o.scale&&t.scale)Object.assign(t.scale,o.scale);else if(o.scale)t.scale=o.scale}
 function remember(t){t.hist||={past:[],future:[]};t.hist.past.push(snapshotOf(t));if(t.hist.past.length>HISTORY_LIMIT)t.hist.past.shift();t.hist.future=[]}
 function undoTrace(t){if(!t.hist?.past.length)return false;t.hist.future.push(snapshotOf(t));restoreSnapshot(t,t.hist.past.pop());t.sel=null;return true}
 function redoTrace(t){if(!t.hist?.future.length)return false;t.hist.past.push(snapshotOf(t));restoreSnapshot(t,t.hist.future.pop());t.sel=null;return true}

 /* ----- the record, read back ----- */
 const COMPUTED=['quantity','method','expected_error_pct','shape_count','corner_count','cutout_count','area_sf','length_ft','scale_ft_per_px'];
 // A stored takeoff.inputs.measurement (the body plus what the server computed) -> the body
 // again, as the server would take it (an original keeps its body, without its own computed keys).
 function bodyOfRecord(rec){
  if(!rec||typeof rec!=='object')return null;
  const out={};for(const [k,v] of Object.entries(rec))if(!COMPUTED.includes(k))out[k]=k==='original'?bodyOfRecord(v):JSON.parse(JSON.stringify(v));
  return out;
 }
 // A body's shapes in a view's pixels: lon/lat through the view's georeference (any georeferenced
 // view: lon/lat is the ground), image pixels scaled from the frame they were drawn in (view.width).
 function pixelShapes(body,view,{width,height}={}){
  if(!body?.shapes)return null;
  let to;
  if(body.space==='lonlat'){const g=georef(view);if(!g)return null;to=([lon,lat])=>g.toPixel(lon,lat)}
  else{const fw=Number(body.view?.width)||width,fh=Number(body.view?.height)||height,sx=width&&fw?width/fw:1,sy=height&&fh?height/fh:sx;to=([x,y])=>[x*sx,y*sy]}
  const shapes=body.shapes.map(s=>s.kind==='line'?{kind:'line',path:s.path.map(to)}:{kind:'area',ring:dropClosing(s.ring).map(to),...(s.cutouts?.length?{cutouts:s.cutouts.map(c=>dropClosing(c).map(to))}:{})});
  const scale=body.scale?{points:body.scale.points.map(to),length:body.scale.length,unit:body.scale.unit}:null;
  return {shapes,scale};
 }
 // Who drew it, in words for the engineer (the model's name stays in the record).
 const drawnByText=d=>d&&d!=='engineer'?'Drafted by Basis':'Traced by an engineer';
 // The body the tool sends, with its provenance: an edit of a stored measurement keeps that
 // measurement (or its own original, the first one) as `original`.
 function withProvenance(body,{drawnBy='engineer',verified=null,view=null,replaces=null}={}){
  const out={...body,drawn_by:drawnBy};
  if(verified!==null)out.verified=!!verified;
  if(view)out.view=view;
  if(replaces){const first=replaces.original||replaces,clean={...first};delete clean.original;out.original=clean}
  return out;
 }
 // Two bodies draw the same shapes (an unchanged draft is not an edit).
 const sameShapes=(a,b)=>!!a&&!!b&&a.space===b.space&&a.unit===b.unit&&JSON.stringify(a.shapes)===JSON.stringify(b.shapes)&&JSON.stringify(a.scale||null)===JSON.stringify(b.scale||null)&&(a.height_ft??null)===(b.height_ft??null);
 // The view a measurement records: which picture, so it can be drawn again.
 function viewRef(view,{width,height}={}){
  if(!view)return null;
  if(view.kind==='document')return {kind:'document',id:String(view.id),...(view.label?{label:String(view.label).slice(0,200)}:{}),...(width&&height?{width,height}:{})};
  if(view.source_id)return {kind:'public',id:String(view.id),...(view.year_label||view.year?{label:String(view.year_label||view.year)}:{})};
  return {kind:'capture',id:String(view.id),...(view.label?{label:String(view.label)}:{})};
 }
 // A photo measured with a known length (space image): the body.
 function imageBody({unit,shapes,scale,doc,width,height,heightFt=null}){
  const r=p=>[Math.round(p[0]*1000)/1000,Math.round(p[1]*1000)/1000],R=pts=>pts.map(r);
  const out={unit,space:'image',shapes:shapes.map(s=>s.kind==='line'?{kind:'line',path:R(s.path)}:{kind:'area',ring:R(s.ring),...(s.cutouts?.length?{cutouts:s.cutouts.map(R)}:{})}),
   scale:{points:R(scale.points),length:Number(scale.length),unit:scale.unit==='m'?'m':'ft'},
   image:{source:`Photo ${String(doc.filename||doc.id).slice(0,200)} of this study, scaled from a known length`,document_id:doc.id}};
  if(heightFt!=null&&heightFt!=='')out.height_ft=Number(heightFt);
  return out;
 }
 // Known lengths for scaling a photo (Basis's hint list; the engineer types what they know).
 const KNOWN_LENGTHS=[
  {label:'Door, height',length:80,unit:'in',note:'a standard interior or entry door (6\'-8")'},
  {label:'Door, width',length:36,unit:'in',note:'an entry door; interior doors are often 30 or 32 in'},
  {label:'Base cabinet, height',length:34.5,unit:'in',note:'without the countertop'},
  {label:'Counter, height',length:36,unit:'in',note:'floor to the top of the countertop'},
  {label:'Base cabinet, depth',length:24,unit:'in',note:'front to the wall'},
 ];
 // The per-edge comparison with the original (Basis's addition: AI 54'-3", engineer 54'-0").
 // Edges are matched by position when both have the same count, else only the totals compare.
 function edgeComparison(now,was){
  if(!was||now.length!==was.length)return null;
  return now.map((ft,i)=>({i,now:ft,was:was[i],change:ft-was[i]}));
 }

 /* ---------- the diagram: a clean plan of a top-down view (EVAL_REPORT 104.1) ----------
  * Drawn from data the study already has, in the view's own pixels (the same width, height and
  * georeference as the picture), so the measure tool's overlay sits exactly where it sits on the
  * photo: the lot lines (the parcels, adjacent ones dashed), every lon/lat measurement on the
  * study's assets (stored, or pending in the assets review) projected with the view's
  * georeference, a colour per asset type with a legend, a north arrow, a scale bar from the
  * view's pixel size, and the picture's source and date. EagleView's report diagrams are the
  * model: shapes, lengths, a key, north, scale. */
 // Strokes that read on the diagram's near-white ground (#f7f8f5): each at least 4.5:1 (WCAG AA
 // for text), so the lengths written in them stay legible; the fill is the stroke at 16 %.
 const DIAGRAM_COLOURS=['#1f5f8b','#a3401c','#5b3a8e','#2c6b2f','#7a5200','#a3174f','#00635a','#4b5563'];
 const DIAGRAM_GROUND='#f7f8f5';
 // The largest round length (1, 2 or 5 x 10^n feet) whose bar is at most maxPx of this view's pixels.
 function scaleBar(pixelSizeM,{maxPx=200}={}){
  const ftPerPx=Number(pixelSizeM)*FT_PER_M;if(!(ftPerPx>0)||!(maxPx>0))return null;
  let best=null;for(let e=-1;e<=6;e++)for(const m of [1,2,5]){const ft=m*Math.pow(10,e),px=ft/ftPerPx;if(px<=maxPx&&(!best||px>best.px))best={ft:Number(ft.toPrecision(3)),px}}
  if(!best)return null;
  return {...best,label:`${best.ft.toLocaleString('en-US')} ft`};
 }
 // The study's lon/lat measurements that lie on this view, in its pixels: the pending correction's
 // measurement over the stored one; image-space ones (on a photo) skipped; `except` (the asset being
 // edited, which the tool draws on top) left out.
 function measuredOnView(items,view,{pending=null,except=null}={}){
  const g=georef(view);if(!g)return [];
  const w=Number(view.width),h=Number(view.height),out=[];
  for(const a of items||[]){
   if(!a||a.id===except)continue;
   const raw=pending?.get?.(a.id)?.after?.measurement||a.takeoff?.inputs?.measurement;
   if(!raw||typeof raw!=='object'||raw.space!=='lonlat'||!Array.isArray(raw.shapes))continue;
   const body=bodyOfRecord(raw);let r;try{r=measure(body)}catch{continue}
   const px=pixelShapes(body,view);if(!px)continue;
   const pts=px.shapes.flatMap(sh=>sh.kind==='line'?sh.path:sh.ring);
   if(!pts.some(q=>q[0]>=0&&q[1]>=0&&q[0]<=w&&q[1]<=h))continue;// not on this picture
   out.push({assetId:a.id,label:a.label||a.category||'measured',category:a.category||'',unit:r.unit,quantity:r.quantity,shapes:px.shapes,heightFt:body.height_ft??null,pending:!!pending?.get?.(a.id)?.after?.measurement,drawnBy:body.drawn_by||'engineer',verified:body.verified??null});
  }
  return out;
 }
 const typeName=l=>String(l||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());
 // The lidar's roof outline (GET .../property/public `lidar.building.outline`, EVAL_REPORT 104.2) in
 // a view's pixels, or null (no outline: a gather made before it was kept, no building, no georef).
 function buildingOnView(lidar,view){
  const g=georef(view),o=lidar?.building?.outline;if(!g||!Array.isArray(o)||o.length<3)return null;
  const points=o.filter(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite)).map(([lon,lat])=>g.toPixel(lon,lat));if(points.length<3)return null;
  const sqft=lidar.building.footprint_sqft!=null?Number(lidar.building.footprint_sqft):Number(lidar.building.footprint_m2)*FT_PER_M*FT_PER_M;
  return {points,year:lidar.year??null,footprintSqft:Number.isFinite(sqft)?sqft:null,label:`Building · roof outline from ${lidar.year?`${lidar.year} `:''}lidar`};
 }
 // editing: the tool draws the shape being measured on top, so "nothing measured" is not said.
 function diagramModel({view,parcels=[],measured=[],editing=false,building=null}){
  const g=georef(view);if(!g)return null;
  const w=Number(view.width),h=Number(view.height);
  const lots=(parcels||[]).flatMap(p=>parcelRings(p,g).map(points=>({points,adjacent:!p.under_pin})));
  // One colour per asset type, in the order of the type names (stable from render to render).
  const types=[...new Set(measured.map(m=>m.label))].sort((a,b)=>typeName(a).localeCompare(typeName(b)));
  const colourOf=l=>DIAGRAM_COLOURS[types.indexOf(l)%DIAGRAM_COLOURS.length];
  const legend=types.map(l=>{const of=measured.filter(m=>m.label===l),units=[...new Set(of.map(m=>m.unit))];
   const quantity=units.map(u=>`${of.filter(m=>m.unit===u).reduce((t,m)=>t+Number(m.quantity),0).toLocaleString('en-US',{maximumFractionDigits:2})} ${u}`).join(' + ');
   return {label:l,name:typeName(l),colour:colourOf(l),quantity,count:of.length}});
  const scale=scaleBar(view.pixel_size_m,{maxPx:w/4});
  const note=!lots.length&&!measured.length&&!editing?'No lot line and nothing measured on this picture yet. Trace a shape and it is drawn here.':!lots.length?'No lot line was found for this property.':!measured.length&&!editing?'Nothing measured on this picture yet.':null;
  const credit=[view.source_id?`${view.credit||'Public imagery'}, ${view.year_label||view.year||'year unknown'}`:`Picture of ${dateText(dateOf(view))}`,view.pixel_size_m?`${(Number(view.pixel_size_m)*100).toLocaleString('en-US',{maximumFractionDigits:1})} cm a pixel`:null,view.source_id?null:view.source].filter(Boolean).join(' · ');
  return {w,h,ground:DIAGRAM_GROUND,lots,building,shapes:measured.map(m=>({...m,colour:colourOf(m.label)})),legend,scale,north:{angle:-(Number(view.heading_deg)||0)},note,credit,pixelSizeM:Number(view.pixel_size_m)};
 }

 /* ---------- zoom about the middle of the view ----------
  * A zoomed picture is a canvas inside a scrolled pane. The picture point at the pane's middle,
  * as fractions of the picture, is read before a zoom and put back at the middle after it, the
  * scroll clamped to what the pane can scroll (at an edge the view stops there; the next zoom
  * starts from where the view really is, so nothing drifts). canvasLeft/Top: the canvas's offset in
  * the pane's scrolled content (a canvas narrower than the pane is centred in it). */
 function viewCentre({scrollLeft,scrollTop,clientWidth,clientHeight,canvasLeft=0,canvasTop=0,canvasWidth,canvasHeight}){
  return [(scrollLeft+clientWidth/2-canvasLeft)/canvasWidth,(scrollTop+clientHeight/2-canvasTop)/canvasHeight];
 }
 function scrollToCentre(f,{clientWidth,clientHeight,canvasLeft=0,canvasTop=0,canvasWidth,canvasHeight,scrollWidth,scrollHeight}){
  const clamp=(v,max)=>Math.max(0,Math.min(Math.max(0,max),v));
  return {scrollLeft:clamp(canvasLeft+f[0]*canvasWidth-clientWidth/2,scrollWidth-clientWidth),scrollTop:clamp(canvasTop+f[1]*canvasHeight-clientHeight/2,scrollHeight-clientHeight)};
 }

 /* ---------- labels clear of the pin ---------- */
 // A label box {w, h} centred at `at` meets the pin's circle (its halo, radius r)?
 function boxMeetsCircle(at,{w,h},c,r){const dx=Math.max(Math.abs(c[0]-at[0])-w/2,0),dy=Math.max(Math.abs(c[1]-at[1])-h/2,0);return dx*dx+dy*dy<r*r}
 // The box of a label turned by `angle` degrees, upright (for the test above).
 const turnedBox=({w,h},angle)=>{const a=angle*Math.PI/180,c=Math.abs(Math.cos(a)),s=Math.abs(Math.sin(a));return {w:w*c+h*s,h:w*s+h*c}};
 // Where a label goes so the pin never covers it: where it is when clear; else below the shape
 // (then above it) when the shape's box is given; else pushed straight away from the pin.
 function clearOfPin(at,box,pin,{gap=4,shape=null}={}){
  if(!pin||!boxMeetsCircle(at,box,pin.at,pin.r+gap))return at;
  if(shape)for(const c of [[shape.cx,shape.maxY+gap+box.h/2],[shape.cx,shape.minY-gap-box.h/2]])if(!boxMeetsCircle(c,box,pin.at,pin.r+gap))return c;
  let dx=at[0]-pin.at[0],dy=at[1]-pin.at[1],d=Math.hypot(dx,dy);if(d<1e-9){dx=0;dy=1;d=1}
  let t=pin.r+gap,p=[pin.at[0]+dx/d*t,pin.at[1]+dy/d*t];
  for(let i=0;i<200&&boxMeetsCircle(p,box,pin.at,pin.r+gap);i++){t+=1;p=[pin.at[0]+dx/d*t,pin.at[1]+dy/d*t]}
  return p;
 }
 // Two upright boxes {at, w, h} overlap (with a gap between them)?
 const boxesMeet=(a,b,gap=0)=>Math.abs(a.at[0]-b.at[0])*2<a.w+b.w+2*gap&&Math.abs(a.at[1]-b.at[1])*2<a.h+b.h+2*gap;
 // Does an upright box {at, w, h} (grown by `gap`) touch a polygon's area? A corner of either inside
 // the other, or an edge of the polygon crossing the box (segment against box, Liang-Barsky).
 function boxMeetsPolygon(at,{w,h},poly,gap=0){
  if(!poly||poly.length<3)return false;
  const x0=at[0]-w/2-gap,x1=at[0]+w/2+gap,y0=at[1]-h/2-gap,y1=at[1]+h/2+gap;
  if([[x0,y0],[x1,y0],[x1,y1],[x0,y1],at].some(q=>insideOrOn(q,poly)))return true;
  if(poly.some(([x,y])=>x>x0&&x<x1&&y>y0&&y<y1))return true;
  for(let i=0;i<poly.length;i++){const a=poly[i],b=poly[(i+1)%poly.length],dx=b[0]-a[0],dy=b[1]-a[1];let t0=0,t1=1,ok=true;
   for(const [pp,q] of [[-dx,a[0]-x0],[dx,x1-a[0]],[-dy,a[1]-y0],[dy,y1-a[1]]]){if(pp===0){if(q<0){ok=false;break}continue}const r=q/pp;if(pp<0){if(r>t1){ok=false;break}if(r>t0)t0=r}else{if(r<t0){ok=false;break}if(r<t1)t1=r}}
   if(ok&&t0<=t1)return true}
  return false;
 }
 // Where a shape's name goes, after its edge labels are placed (`boxes`: every label already on the
 // diagram, as upright boxes). A name sits inside its own shape, or outside every other shape
 // (`avoid`: the other shapes' polygons; never inside or over another shape's area, so a name is
 // never read as another shape's), clear of every label and of the pin, and on the picture
 // (`bounds` [w, h]). Tried in order: where it starts (the label point); the nearest place inside
 // the shape where the whole box fits; then around the shape (below, above, left, right, then the
 // corners), sliding outwards up to ten name heights. Returns {at, inside} (`at` the box's centre;
 // `inside` when the whole box is inside its shape), or null when nothing qualifies: the caller
 // leaves the name out of the drawing (it stays in the key and the title), never misplaces it.
 // `within: false` for a line (it has no inside).
 function placeName(points,box,{pin=null,boxes=[],gap=4,start=null,within=true,avoid=[],bounds=null}={}){
  const onPic=at=>!bounds||(at[0]-box.w/2>=0&&at[1]-box.h/2>=0&&at[0]+box.w/2<=bounds[0]&&at[1]+box.h/2<=bounds[1]);
  const clear=at=>onPic(at)&&!(pin&&boxMeetsCircle(at,box,pin.at,pin.r+gap))&&!boxes.some(b=>boxesMeet({at,w:box.w,h:box.h},b,gap))&&!avoid.some(q=>boxMeetsPolygon(at,box,q,gap/2));
  const fits=at=>within&&[[-1,-1],[1,-1],[1,1],[-1,1]].every(([sx,sy])=>insideOrOn([at[0]+sx*box.w/2,at[1]+sy*box.h/2],points));
  const found=at=>({at,inside:fits(at)});
  const first=start||labelPoint(points);
  if(clear(first))return found(first);
  const xs=points.map(q=>q[0]),ys=points.map(q=>q[1]),x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys),cx=(x0+x1)/2,cy=(y0+y1)/2;
  if(within){
   const inside=[];for(let i=0;i<=12;i++)for(let j=0;j<=12;j++){const q=[x0+(x1-x0)*i/12,y0+(y1-y0)*j/12];if(fits(q))inside.push(q)}
   inside.sort((a,b)=>Math.hypot(a[0]-first[0],a[1]-first[1])-Math.hypot(b[0]-first[0],b[1]-first[1]));
   for(const q of inside)if(clear(q))return found(q);
  }
  const step=Math.max(1,box.h/4),reach=40,bw=box.w/2+gap,bh=box.h/2+gap;
  for(let i=0;i<reach;i++){const d=i*step;
   for(const q of [[cx,y1+bh+d],[cx,y0-bh-d],[x0-bw-d,cy],[x1+bw+d,cy],[x0-bw-d,y1+bh+d],[x1+bw+d,y1+bh+d],[x0-bw-d,y0-bh-d],[x1+bw+d,y0-bh-d]])if(clear(q))return found(q)}
  return null;
 }
 // The short line from a name placed outside its shape to the shape's nearest edge: null when the
 // name's box already touches the shape (or, for a line, comes within `near` of it).
 function leaderFor(at,box,points,closed,near=0){
  if(closed&&boxMeetsPolygon(at,box,points))return null;
  let best=null;const n=points.length;
  for(let i=0;i<(closed?n:n-1);i++){const q=nearestOnSegment(at,points[i],points[(i+1)%n]),d=dist(at,q);if(!best||d<best.d)best={q,d}}
  if(!best)return null;
  const dx=best.q[0]-at[0],dy=best.q[1]-at[1],t=Math.min(box.w/2/Math.max(Math.abs(dx),1e-9),box.h/2/Math.max(Math.abs(dy),1e-9));
  if(t>=1)return null;
  const from=[at[0]+dx*t,at[1]+dy*t];
  return dist(from,best.q)<=near?null:{from,to:best.q};
 }
 // Where an edge's length label goes so it meets no label already placed (upright boxes) and not
 // the pin: its own place outside the edge; the other side of the edge; slid along the edge, its
 // middle kept within the edge's middle half; then nearer the edge on either side. Null when nothing
 // fits: the caller leaves that length out of the drawing (it stays in the edge list and the title).
 function placeEdgeLabel(l,points,closed,box,{pin=null,boxes=[],gap=2}={}){
  const n=points.length,a=points[l.i],b=points[(l.i+1)%n],len=dist(a,b);if(!(len>0))return null;
  const u=[(b[0]-a[0])/len,(b[1]-a[1])/len],off=Math.hypot(l.x-l.mx,l.y-l.my)||1,nn=[(l.x-l.mx)/off,(l.y-l.my)/off],tb=turnedBox(box,l.angle);
  const at=(s,o)=>[l.mx+u[0]*s*len+nn[0]*o,l.my+u[1]*s*len+nn[1]*o];
  const ok=p=>!(pin&&boxMeetsCircle(p,tb,pin.at,pin.r+gap))&&!boxes.some(o=>boxesMeet({at:p,w:tb.w,h:tb.h},o,gap));
  const slides=[0,0.125,-0.125,0.25,-0.25],tries=[[0,off],[0,-off],...slides.slice(1).flatMap(s=>[[s,off],[s,-off]]),...slides.flatMap(s=>[[s,off*0.6],[s,-off*0.6]])];
  for(const [sl,o] of tries){const p=at(sl,o);if(ok(p))return {at:p,w:tb.w,h:tb.h}}
  return null;
 }
 const pointsBox=pts=>{const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);return {cx:(Math.min(...xs)+Math.max(...xs))/2,minY:Math.min(...ys),maxY:Math.max(...ys)}};

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
  frameOf,scaleFeetPerPx,segmentFt,viewCentre,scrollToCentre,boxMeetsCircle,boxesMeet,boxMeetsPolygon,leaderFor,placeName,placeEdgeLabel,turnedBox,clearOfPin,pointsBox,buildingOnView,scaleBar,measuredOnView,diagramModel,DIAGRAM_COLOURS,DIAGRAM_GROUND,angleAt,isSquare,RIGHT_TOL_DEG,edgeLengths,ftIn,ftInWords,parseLength,areaText,lfText,edgeLabels,labelPoint,shapeSummary,movePoint,insertPoint,removePoint,minPoints,setEdgeLength,snapPoint,nearestOnSegment,ringSegments,onPicture,
  remember,undoTrace,redoTrace,snapshotOf,bodyOfRecord,pixelShapes,drawnByText,withProvenance,sameShapes,viewRef,imageBody,KNOWN_LENGTHS,edgeComparison,lonlatPathFt,shoelace,insideOrOn,
  needsMeasurement,measureOffer,measureUnit,captureStatus,gatherStatus,noteText,clock,viewsOf,historyStrip,defaultMeasureView,leafText,dateText,yearOnly,dateOf,SIDE_TEXT,LOOKING,compass,
  publicSeries,creditLine,measurementBody,imageOf,parcelRings,siteCheckText,lidarFacts,bracket,withBracket,caps,anyProperty,ringProblem,CAPTURE_FAILED,CAPTURE_PART};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisPropertyModel=api;
})(typeof globalThis==='object'?globalThis:this);
