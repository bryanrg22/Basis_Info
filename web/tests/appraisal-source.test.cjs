const {test}=require('node:test');const assert=require('node:assert/strict');
const A=require('../live/appraisal-source.js');
const zip=require('../assets/vendor/zipjs/zip.min.js');
const item=(name,size=10)=>({item:{kind:'appraisal',name,size},file:{size}});
class FixtureParser{parseFromString(text){return {documentElement:{localName:'MESSAGE',namespaceURI:'http://www.mismo.org/residential/2009/schemas'},getElementsByTagName:()=>[],getElementsByTagNameNS:()=>[{textContent:text.replace(/^<x>/,'')}]}}}
async function pack(files){const writer=new zip.ZipWriter(new zip.BlobWriter(),{useWebWorkers:false});for(const [name,text] of files)await writer.add(name,new zip.TextReader(text));return writer.close()}
test('intake accepts each backend format and XML plus companion, always XML first',()=>{
 for(const name of ['appraisal.pdf','delivery.ZIP','report.xml'])assert.deepEqual(A.plan([item(name)]).problems,[]);
 const pair=A.plan([item('report.pdf'),item('values.xml')]);assert.equal(pair.appraisals[0].item.name,'values.xml');assert.deepEqual(pair.problems,[]);
 for(const names of [[],['a.pdf','b.pdf'],['a.zip','b.xml'],['a.xml','b.pdf','c.pdf'],['a.jpg']])assert(A.plan(names.map(n=>item(n))).problems.length);
 assert(A.plan([item('a.pdf',50*1024*1024+1)]).problems.length);
});
test('companion is the latest confirmed appraisal PDF, not a photo or unconfirmed upload',()=>{
 const doc={id:'xml'},d=(id,date,extra={})=>({id,kind:'appraisal',content_type:'application/pdf',uploaded_at:date,created_at:date,...extra});
 assert.equal(A.companion(doc,[d('old','2026-01-01'),d('current','2026-02-01'),d('pending',null),d('other','2026-03-01',{kind:'other'})]).id,'current');
 assert.equal(A.companion(doc,[]),null);
});
test('recognizes actual bytes independently of a misleading MIME or filename',async()=>{
 assert.equal(await A.sniff(new Blob(['\n%PDF-1.7\n'])), 'pdf');assert.equal(await A.sniff(new Blob(['<MESSAGE/>'])), 'xml');
 await assert.rejects(()=>A.sniff(new Blob(['not a report'])),/not a readable/);
 await assert.rejects(()=>A.inspect(new Blob([new Uint8Array(50*1024*1024+1)])),/50 MB/);
});
test('regions preserve every valid page and reject out-of-range or degenerate rectangles',()=>{
 const valid=[{page:3,bbox:[.1,.2,.6,.3]},{page:5,bbox:[0,0,1,1]}];
 const field={page:1,regions:[...valid,{page:0,bbox:[0,0,1,1]},{page:2,bbox:[0,0,2,1]},{page:1,bbox:[1,0,.5,1]}]};
 assert.deepEqual(A.regions(field),valid);assert.equal(A.page(field),3);assert.equal(A.page({page:4}),4);assert.equal(A.page({}),null);
});
test('ZIP reader selects the XML-referenced report with nested folders and preserves all PDF bytes',async()=>{
 const blob=await pack([['appraisal.xml','<x>report.pdf'],['docs/report.pdf','%PDF-1.7 actual report'],['other.pdf','%PDF-1.7 wrong report']]);
 const result=await A.inspect(blob,{zip,Parser:FixtureParser});assert.equal(result.filename,'report.pdf');assert.equal(await result.pdf.text(),'%PDF-1.7 actual report');
});
test('ZIP reader handles XML only and rejects ambiguous reports, bad PDF bytes and unsafe entries',async()=>{
 const options={zip,Parser:FixtureParser};
 assert.equal((await A.inspect(await pack([['appraisal.xml','<x>no.pdf']]),options)).pdf,null);
 await assert.rejects(()=>A.inspect(packBlob,options));
 async function reject(files,pattern){const blob=await pack(files);await assert.rejects(()=>A.inspect(blob,options),pattern)}
 await reject([['appraisal.xml','<x>missing.pdf'],['one.pdf','%PDF-1'],['two.pdf','%PDF-1']],/multiple PDFs/);
 await reject([['appraisal.xml','<x>only.pdf'],['only.pdf','wrong bytes']],/not a readable|not a PDF/);
 await reject([['../appraisal.xml','<x>only.pdf']],/unsafe (path|filename)/i);
 await reject([['one.xml','<x>only.pdf'],['two.xml','<x>only.pdf']],/exactly one/);
});
const packBlob=new Blob(['PK\x03\x04invalid']);
test('XML refuses document type and entity declarations before invoking the parser',()=>{
 for(const s of ['<!DOCTYPE MESSAGE SYSTEM "file:///private">','<!ENTITY x "y">'])assert.throws(()=>A.xmlInfo(new TextEncoder().encode(s),FixtureParser),/DOCTYPE|entities/);
 assert.throws(()=>A.xmlInfo(new Uint8Array(25*1024*1024+1),FixtureParser),/25 MB/);
 for(const n of ['/x.xml','a/../x.xml','C:/x.xml','a\\x.xml'])assert.equal(A.safeName(n),false);
});
test('archive limits apply to actual streamed bytes, entry count and encryption',async()=>{
 function fake(entries){return {BlobReader:class{},ZipReader:class{async *getEntriesGenerator(){yield* entries}async close(){}}}}
 const base={filename:'appraisal.xml',uncompressedSize:1,getData:async stream=>{const w=stream.getWriter();await w.write({length:60*1024*1024+1});await w.close()}};
 await assert.rejects(()=>A.unpack(new Blob(),fake([base]),FixtureParser),/expanded size/);
 await assert.rejects(()=>A.unpack(new Blob(),fake([{...base,uncompressedSize:61*1024*1024}]),FixtureParser),/expanded size/);
 await assert.rejects(()=>A.unpack(new Blob(),fake([{...base,encrypted:true}]),FixtureParser),/encrypted/);
 await assert.rejects(()=>A.unpack(new Blob(),fake(Array.from({length:2001},(_,i)=>({filename:`${i}/`,directory:true}))),FixtureParser),/2,000/);
});
