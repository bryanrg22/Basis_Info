/* Read-only presentation models. API entities and staged corrections stay untouched. */
(function(global){
 'use strict';
 const buckets={y5:'5-year',y7:'7-year',y15:'15-year',y27_5:'27.5-year',y39:'39-year',land:'Land'};
 const human=value=>String(value||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());
 const compare=(a,b)=>a.localeCompare(b,undefined,{numeric:true,sensitivity:'base'});
 function cost(value){return value!==null&&value!==undefined&&String(value).trim()!==''&&Number.isFinite(Number(value))?Number(value):null}
 function summarize(items){
  return items.reduce((s,a)=>{const value=cost(a.cost?.total);s.count++;s.review+=a.needs_review?1:0;if(value===null)s.unpriced++;else{s.priced++;s.total+=value}return s},{count:0,review:0,unpriced:0,priced:0,total:0});
 }
 function assetView(items,options={}){
  const {query='',filter='all',groupBy='room',corrections=new Map()}=options;
  const all=items.map(a=>{const correction=corrections.get(a.id);return {...a,bucket:correction?.after?.bucket??a.bucket,category:correction?.after?.category??a.category,irs_section:correction?.after?.irs_section??a.irs_section,edited:!!correction}});
  const terms=query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const shown=all.filter(a=>{
   if(filter==='review'&&!a.needs_review||filter==='ok'&&a.needs_review||filter==='unpriced'&&cost(a.cost?.total)!==null)return false;
   const haystack=[a.label,human(a.label),a.category,a.room?.name,buckets[a.bucket]||a.bucket,a.irs_section].join(' ').toLowerCase();
   return terms.every(term=>haystack.includes(term));
  });
  const groups=new Map();
  for(const a of shown){
   const key=groupBy==='recovery'?'bucket:'+(a.bucket||'unknown'):'room:'+(a.room?.id??(a.room?.name?'name:'+a.room.name:'unassigned'));
   if(!groups.has(key))groups.set(key,{key,name:groupBy==='recovery'?(buckets[a.bucket]||human(a.bucket)||'Unclassified'):(a.room?.name||'Unassigned'),roomId:groupBy==='room'?a.room?.id:null,bucket:groupBy==='recovery'?a.bucket:null,items:[]});
   groups.get(key).items.push(a);
  }
  const order=Object.keys(buckets);
  const result=[...groups.values()].sort((a,b)=>groupBy==='recovery'?((order.indexOf(a.bucket)<0?99:order.indexOf(a.bucket))-(order.indexOf(b.bucket)<0?99:order.indexOf(b.bucket))||compare(a.name,b.name)):compare(a.name,b.name));
  for(const group of result){group.items.sort((a,b)=>Number(b.needs_review)-Number(a.needs_review)||compare(human(a.label)||a.category||'',human(b.label)||b.category||''));group.stats=summarize(group.items)}
  return {groups:result,summary:summarize(all),matching:summarize(shown),classes:order.concat([...new Set(all.map(a=>a.bucket))].filter(b=>!order.includes(b))).filter(b=>all.some(a=>a.bucket===b)).map(bucket=>({bucket,name:buckets[bucket]||human(bucket)||'Unclassified',...summarize(all.filter(a=>a.bucket===bucket))}))};
 }
 const overview=[
  ['subject.property_address','Property address'],['subject.city','City'],['subject.state','State'],['subject.zip','ZIP code'],
  ['improvements.total_rooms','Rooms'],['improvements.bedrooms','Bedrooms'],['improvements.bathrooms','Bathrooms'],['improvements.gla_sqft','Living area (sq ft)'],
  ['improvements.year_built','Year built'],['improvements.units','Units'],['reconciliation.final_market_value','Appraised value'],['listing_and_contract.contract_price','Purchase price']
 ];
 const sections=[
  ['property','Property & ownership',['subject']],
  ['building','Building & layout',['improvements']],
  ['valuation','Valuation & purchase',['reconciliation','listing_and_contract','sales_comparison','income_approach']],
  ['site','Land & utilities',['site']],
  ['cost','Cost approach',['cost_approach']],
  ['neighborhood','Neighborhood',['neighborhood']],
  ['comments','Additional comments',['additional_comments']],
  ['appraiser','Appraiser',['appraiser']]
 ];
 const key=f=>f.key||`${f.section}.${f.field}`;
 const fieldValue=f=>f.value_text??(f.value_num!==null&&f.value_num!==undefined?Number(f.value_num).toLocaleString('en-US'):null)??f.value_date??'—';
 const needsLook=f=>f.confidence!==null&&f.confidence!==undefined&&f.confidence<.7||f.source==='mismo_xml'&&fieldValue(f)!=='—'&&!(f.regions||[]).length;
 function fieldGroups(fields,options={}){
  const {query='',filter='all',corrections=new Map()}=options,terms=query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const groups=new Map([['overview',{key:'overview',name:'Property overview',fields:[]}]]);
  for(const [id,name] of sections)groups.set(id,{key:id,name,fields:[]});
  for(const f of fields){
   if(filter==='review'&&!needsLook(f)||filter==='critical'&&!f.critical)continue;
   const haystack=[f.label,f.field,human(f.field),f.section,human(f.section),key(f),fieldValue(f),corrections.get(f.id)?.display].join(' ').toLowerCase();
   if(!terms.every(t=>haystack.includes(t)))continue;
   const featured=overview.findIndex(([k])=>k===key(f));
   const id=featured>=0?'overview':sections.find(([, , sources])=>sources.includes(f.section))?.[0]||'source:'+(f.section||'other');
   if(!groups.has(id))groups.set(id,{key:id,name:human(f.section)||'Other fields',fields:[]});
   groups.get(id).fields.push(f);
  }
  const result=[...groups.values()].filter(g=>g.fields.length);
  for(const group of result){group.fields.sort((a,b)=>group.key==='overview'?overview.findIndex(([k])=>k===key(a))-overview.findIndex(([k])=>k===key(b)):Number(b.critical)-Number(a.critical)||compare(a.label||a.field||'',b.label||b.field||''));group.review=group.fields.filter(needsLook).length}
  return result;
 }
 const model={buckets,cost,summarize,assetView,fieldGroups,fieldValue,needsLook,fieldKey:key,fieldLabel:f=>overview.find(([k])=>k===key(f))?.[1]||f.label||human(f.field)};
 if(typeof module!=='undefined'&&module.exports)module.exports=model;else global.BasisReviewOrganization=model;
})(typeof globalThis!=='undefined'?globalThis:this);
