/* Exact text matches on the cited PDF page; never inferred field coordinates. */
(function(scope){
 'use strict';
 function matchField(strings,field){
  const offsets=[];let text='';
  for(const str of strings){offsets.push(text.length);text+=str+' ';}
  let matches=[];
  if(field.value_num!==null&&field.value_num!==undefined&&Number.isFinite(Number(field.value_num))){
   // Compare complete numeric tokens, including decimal precision and currency formatting.
   const token=/[-+]?[$£€]?\s*\d[\d,]*(?:\.\d+)?/g;
   for(const m of text.matchAll(token)){
    const start=m.index+m[0].search(/\S/),end=m.index+m[0].length;
    if(/[\p{L}\p{N}.]/u.test(text[start-1]||'')||/[\p{L}\p{N}.]/u.test(text[end]||''))continue;
    if(Number(m[0].replace(/[$£€,\s]/g,''))===Number(field.value_num))matches.push([start,end]);
   }
  }else{
   const values=[];if(field.value_text?.trim())values.push(field.value_text.trim());
   if(field.value_date){
    values.push(field.value_date);const m=/^(\d{4})-(\d{2})-(\d{2})$/.exec(field.value_date);
    if(m){const [,y,mm,dd]=m;values.push(`${Number(mm)}/${Number(dd)}/${y}`,`${mm}/${dd}/${y}`);const month=['January','February','March','April','May','June','July','August','September','October','November','December'][Number(mm)-1];if(month)values.push(`${month} ${Number(dd)}, ${y}`);}
   }
   for(const value of values){
    const escaped=value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&').replace(/\s+/g,'\\s+');
    for(const m of text.matchAll(new RegExp(escaped,'giu'))){
     const start=m.index,end=start+m[0].length;
     if(/[\p{L}\p{N}]/u.test(text[start-1]||'')||/[\p{L}\p{N}]/u.test(text[end]||''))continue;
     matches.push([start,end]);
    }
   }
  }
  matches=[...new Map(matches.map(m=>[m.join(':'),m])).values()];
  return matches.map(([start,end])=>strings.flatMap((str,i)=>{
   const from=Math.max(0,start-offsets[i]),to=Math.min(str.length,end-offsets[i]);
   return to>from?[{index:i,start:from,end:to}]:[];
  })).filter(parts=>parts.length);
 }
 const api={matchField};if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisPdfSource=api;
})(typeof globalThis==='object'?globalThis:this);
