const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createLoader}=require('../live/component-previews.js');
const source=(docId,revision=1)=>({studyId:'study',docId,revision});
const turn=()=>new Promise(resolve=>setImmediate(resolve));

test('compact and expanded previews share one request and the viewer cache',async()=>{
 const cache=new Map(),calls=[],settled=[];
 const load=createLoader(cache,async s=>{calls.push(s);return {items:[{id:'mask'}]}},{onSettled:s=>settled.push(s)});
 const first=load(source('photo')),second=load(source('photo'));
 assert.equal(first,second);const data=await first;await turn();
 assert.equal(calls.length,1);assert.equal(settled.length,1);
 assert.equal(cache.get('objects:study:1:photo').data,data);
 assert.equal(await load(source('photo')),data);
 cache.set('objects:study:1:viewer-photo',{done:true,data});
 assert.equal(await load(source('viewer-photo')),data);assert.equal(calls.length,1);
});

test('preview requests are bounded and never reuse a different revision',async()=>{
 const cache=new Map(),waiting=[];let active=0,maximum=0;
 const load=createLoader(cache,s=>new Promise(resolve=>{active++;maximum=Math.max(maximum,active);waiting.push(()=>{active--;resolve({items:[],revision:s.revision})})}),{concurrency:2});
 const requests=[load(source('a')),load(source('b')),load(source('c')),load(source('a',2))];
 await turn();assert.equal(waiting.length,2);
 waiting.splice(0).forEach(f=>f());await turn();assert.equal(waiting.length,2);
 waiting.splice(0).forEach(f=>f());const data=await Promise.all(requests);
 assert.equal(maximum,2);assert.equal(data[3].revision,2);
});

test('failed requests can retry and do not leave a rejected cache entry',async()=>{
 const cache=new Map();let attempts=0,settled=0;
 const load=createLoader(cache,async()=>{if(!attempts++)throw new Error('offline');return {items:[]}},{onSettled:()=>settled++});
 await assert.rejects(load(source('photo')),/offline/);
 await turn();assert.equal(settled,1); // A viewer waiting on the preview must leave its loading state.
 assert.equal(cache.size,0);assert.deepEqual(await load(source('photo')),{items:[]});
});

test('queued requests for a replaced session do not fetch or overwrite fresh data',async()=>{
 const cache=new Map(),calls=[];let finish,current=true;
 const load=createLoader(cache,s=>{calls.push(s.docId);return new Promise(resolve=>finish=resolve)},{concurrency:1});
 const first=load(source('a')),old=load(source('b'),()=>current);
 const rejected=assert.rejects(old,{name:'AbortError'});
 await turn();current=false;cache.set('objects:study:1:b',{done:true,data:{items:['fresh']}});
 finish({items:[]});await first;await rejected;
 assert.deepEqual(calls,['a']);assert.deepEqual(await load(source('b')),{items:['fresh']});
});

test('an already loading viewer request is reused rather than duplicated',async()=>{
 let resolve;const promise=new Promise(r=>resolve=r),cache=new Map([['objects:study:1:photo',{done:false,promise}]]);
 const load=createLoader(cache,()=>assert.fail('must reuse the viewer request'));
 assert.equal(load(source('photo')),promise);resolve({items:[]});await promise;
});
