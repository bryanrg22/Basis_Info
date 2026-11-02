/* UAD delivery adapter. Values stay in the API; this resolves only the report source.
 * Package selection and limits mirror docs/api/UAD36_INTAKE.md in PR #75. */
(function(scope){
 'use strict';
 const MB=1024*1024,MAX_FILE=50*MB,MAX_XML=25*MB,MAX_ENTRY=60*MB,MAX_TOTAL=120*MB;
 const NS='http://www.mismo.org/residential/2009/schemas';
 const types={pdf:'application/pdf',zip:'application/zip',xml:'application/xml'};
 const extension=name=>(name||'').split('.').pop().toLowerCase();
 const format=doc=>({'application/pdf':'pdf','application/zip':'zip','application/xml':'xml','text/xml':'xml'}[(doc.content_type||'').split(';')[0].trim().toLowerCase()]||extension(doc.filename||doc.name));
 const supported=doc=>Object.hasOwn(types,format(doc));
 function plan(items){
  const problems=[],files=items.filter(x=>x.item.kind==='appraisal'),formats=files.map(x=>format(x.item));
  if(!files.length)problems.push('Add an appraisal PDF, UAD 3.6 ZIP, or MISMO XML.');
  else if(formats.some(f=>!types[f]))problems.push('Appraisals must be PDF, UAD 3.6 ZIP, or MISMO XML.');
  else if(files.length>1&&!(files.length===2&&formats.includes('xml')&&formats.includes('pdf')))problems.push('Choose one appraisal, or one MISMO XML with its matching PDF.');
  for(const x of items)if((x.file?.size??x.item.size)>MAX_FILE)problems.push(`${x.item.name} exceeds the 50 MB per-file limit.`);
  // The backend chooses the first-created appraisal. Register XML before its companion PDF.
  const appraisals=[...files].sort((a,b)=>Number(format(b.item)==='xml')-Number(format(a.item)==='xml'));
  return {appraisals,problems};
 }
 function companion(doc,documents){
  return documents.filter(d=>d.id!==doc.id&&d.kind==='appraisal'&&d.content_type==='application/pdf'&&d.uploaded_at)
   .sort((a,b)=>String(b.uploaded_at).localeCompare(String(a.uploaded_at))||String(b.created_at).localeCompare(String(a.created_at)))[0]||null;
 }
 function safeName(name){return !!name&&!name.includes('\\')&&!name.startsWith('/')&&!name.split('/')[0].includes(':')&&!name.split('/').includes('..')}
 function xmlInfo(bytes,Parser=scope.DOMParser){
  if(bytes.length>MAX_XML)throw new Error('The appraisal XML exceeds 25 MB.');
  const utf16=bytes[0]===255&&bytes[1]===254?'utf-16le':bytes[0]===254&&bytes[1]===255?'utf-16be':bytes[0]===60&&bytes[1]===0?'utf-16le':bytes[0]===0&&bytes[1]===60?'utf-16be':'utf-8';
  const text=new TextDecoder(utf16,{fatal:true}).decode(bytes);
  if(/<!\s*(DOCTYPE|ENTITY)\b/i.test(text))throw new Error('XML declarations with DOCTYPE or entities are not supported.');
  const doc=new Parser().parseFromString(text,'application/xml'),root=doc.documentElement;
  if(doc.getElementsByTagName('parsererror').length||root?.localName!=='MESSAGE'||root?.namespaceURI!==NS)throw new Error('Use a valid MISMO appraisal XML.');
  const ref=[...doc.getElementsByTagNameNS(NS,'ObjectURL')].map(n=>n.textContent.trim()).find(s=>s.toLowerCase().endsWith('.pdf'));
  return {pdfName:ref?.replaceAll('\\','/').split('/').pop()||null};
 }
 async function sniff(blob){
  const head=new Uint8Array(await blob.slice(0,1024).arrayBuffer()),text=new TextDecoder().decode(head);
  if(text.includes('%PDF-'))return 'pdf';
  if(head[0]===80&&head[1]===75&&head[2]===3&&head[3]===4)return 'zip';
  if(text.replace(/^\uFEFF/,'').trimStart().startsWith('<')||head[0]===255&&head[1]===254||head[0]===254&&head[1]===255||head[0]===0&&head[1]===60||head[0]===60&&head[1]===0)return 'xml';
  throw new Error('This file is not a readable PDF, UAD ZIP, or MISMO XML.');
 }
 async function unpack(blob,zip,Parser){
  const reader=new zip.ZipReader(new zip.BlobReader(blob),{useWebWorkers:false,checkSignature:true});
  let total=0;
  async function read(entry,limit){
   if(entry.uncompressedSize>Math.min(limit,MAX_TOTAL-total))throw new Error('The UAD package exceeds its expanded size limit.');
   const chunks=[];let size=0;
   await entry.getData(new WritableStream({write(chunk){
    size+=chunk.length;total+=chunk.length;
    if(size>limit||total>MAX_TOTAL)throw new Error('The UAD package exceeds its expanded size limit.');
    chunks.push(chunk);
   }}),{checkSignature:true,useWebWorkers:false});
   return new Blob(chunks);
  }
  try{
   const entries=[];
   for await(const entry of reader.getEntriesGenerator()){
    if(entries.length>=2000)throw new Error('The UAD package contains more than 2,000 entries.');
    if(!safeName(entry.filename)||entry.encrypted)throw new Error('The UAD package contains an unsafe path or encrypted entry.');
    entries.push(entry);
   }
   const xmls=[];
   for(const entry of entries.filter(e=>!e.directory&&/\.xml$/i.test(e.filename))){
    const bytes=new Uint8Array(await (await read(entry,MAX_ENTRY)).arrayBuffer());
    // Unrelated manifest XMLs are allowed, as in the backend package reader.
    try{xmls.push(xmlInfo(bytes,Parser))}catch{}
   }
   if(xmls.length!==1)throw new Error('The UAD package must contain exactly one MISMO appraisal XML.');
   const pdfs=entries.filter(e=>!e.directory&&/\.pdf$/i.test(e.filename));
   const chosen=pdfs.find(e=>e.filename.split('/').pop()===xmls[0].pdfName)||(pdfs.length===1?pdfs[0]:null);
   if(!chosen&&pdfs.length)throw new Error('The package has multiple PDFs without a matching XML report reference.');
   if(!chosen)return {kind:'zip',pdf:null,message:'This UAD package contains XML data without a PDF report.'};
   const pdf=await read(chosen,MAX_ENTRY);
   if(await sniff(pdf)!=='pdf')throw new Error('The report inside this package is not a PDF.');
   return {kind:'zip',pdf:new Blob([pdf],{type:types.pdf}),filename:chosen.filename.split('/').pop()};
  }finally{await reader.close()}
 }
 async function inspect(blob,{zip,Parser}={}){
  if(blob.size>MAX_FILE)throw new Error('Appraisals must be 50 MB or smaller.');
  const kind=await sniff(blob);
  if(kind==='pdf')return {kind,pdf:blob};
  if(kind==='xml'){xmlInfo(new Uint8Array(await blob.arrayBuffer()),Parser);return {kind,pdf:null,message:'MISMO XML contains extracted data. A matching PDF is needed for a visual report.'}}
  return unpack(blob,zip,Parser);
 }
 function regions(field){
  return (field.regions||[]).filter(r=>Number.isInteger(r?.page)&&r.page>0&&Array.isArray(r.bbox)&&r.bbox.length===4&&r.bbox.every(n=>Number.isFinite(n)&&n>=0&&n<=1)&&r.bbox[2]>r.bbox[0]&&r.bbox[3]>r.bbox[1]);
 }
 const page=field=>regions(field)[0]?.page||(Number.isInteger(field.page)&&field.page>0?field.page:null);
 const api={types,format,supported,plan,companion,safeName,xmlInfo,sniff,unpack,inspect,regions,page};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisAppraisalSource=api;
})(globalThis);
