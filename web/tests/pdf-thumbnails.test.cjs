const {test}=require('node:test');
const assert=require('node:assert/strict');
const {isPdf,createCache,renderFirstPage}=require('../live/pdf-thumbnails.js');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('detects PDFs by media type or filename, leaving photos and other files alone',()=>{
 for(const d of [{content_type:'application/pdf'},{content_type:'application/pdf; charset=binary'},{filename:'APPRAISAL.PDF',content_type:'application/octet-stream'}])assert(isPdf(d));
 for(const d of [{filename:'photo.jpg',content_type:'image/jpeg'},{filename:'notes.pdf.csv'},{}])assert(!isPdf(d));
});
test('deduplicates concurrent thumbnails and bounds active renders',async()=>{
 const calls=[],pending=new Map();const cache=createCache(source=>{calls.push(source);const d=deferred();pending.set(source,d);return d.promise},{concurrency:2});
 const a=cache.get('a','a');assert.equal(a,cache.get('a','a'));
 const b=cache.get('b','b'),c=cache.get('c','c');await tick();assert.deepEqual(calls,['a','b']);
 pending.get('a').resolve({pages:2});await a;await tick();assert.deepEqual(calls,['a','b','c']);
 pending.get('b').resolve({pages:3});pending.get('c').resolve({pages:4});await Promise.all([b,c]);
 assert.equal((await cache.get('a','a')).pages,2);assert.equal(calls.length,3);
});
test('failed previews can retry and old completed thumbnails are evicted',async()=>{
 let calls=0;const cache=createCache(async source=>{calls++;if(calls===1)throw new Error('Offline');return {pages:source}},{limit:1});
 await assert.rejects(cache.get('a',1),/Offline/);assert.equal((await cache.get('a',1)).pages,1);
 await cache.get('b',2);assert.equal(cache.peek('a'),undefined);assert.equal(cache.peek('b').pages,2);
});
test('sign-out drops queued work and prevents an in-flight result entering the next session',async()=>{
 const running=deferred();let calls=0;const cache=createCache(()=>{calls++;return running.promise},{concurrency:1});
 const a=cache.get('a',1),b=cache.get('b',2);
 const rejectedA=assert.rejects(a,{name:'AbortError'}),rejectedB=assert.rejects(b,{name:'AbortError'});
 await tick();cache.clear();running.resolve({pages:4});await Promise.all([rejectedA,rejectedB]);
 assert.equal(calls,1);assert.equal(cache.peek('a'),undefined);assert.equal(cache.peek('b'),undefined);
});
test('renders the first actual page with bounded dimensions and always releases the PDF worker',async()=>{
 let destroyed=0,requested,rendered;const canvas={getContext:()=>({}),toDataURL:()=> 'data:image/png;base64,preview'};
 const page={getViewport:({scale})=>({width:1200*scale,height:1800*scale}),render:options=>{rendered=options;return {promise:Promise.resolve()}}};
 const pdfjs={getDocument:()=>({promise:Promise.resolve({numPages:24,getPage:async n=>{requested=n;return page}}),destroy:async()=>{destroyed++}})};
 assert.deepEqual(await renderFirstPage(pdfjs,{url:'blob:private'},()=>canvas),{src:'data:image/png;base64,preview',pages:24});
 assert.equal(requested,1);assert(rendered.viewport.width<=1280&&rendered.viewport.height<=1680);assert.equal(destroyed,1);assert.equal(canvas.width,0);
 page.render=()=>({promise:Promise.reject(new Error('Corrupt page'))});
 await assert.rejects(renderFirstPage(pdfjs,{},()=>canvas),/Corrupt page/);assert.equal(destroyed,2);
 pdfjs.getDocument=()=>({promise:Promise.reject(Object.assign(new Error('Locked'),{name:'PasswordException'})),destroy:async()=>{destroyed++}});
 await assert.rejects(renderFirstPage(pdfjs,{},()=>canvas),{name:'PasswordException'});assert.equal(destroyed,3);
});
