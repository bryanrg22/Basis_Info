const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../public-entry.js'),'utf8');
function boot(href){
 const location=new URL(href),replacements=[],events={};
 location.replace=url=>replacements.push(url);
 vm.runInNewContext(source,{location,URL,addEventListener:(name,fn)=>events[name]=fn});
 return {location,replacements,events};
}
test('public entry and legacy landing use the standalone marketing document',()=>{
 for(const href of ['https://basis.test/','https://basis.test/#landing','https://basis.test/?revision=old#landing','https://basis.test/web/index.html#landing']){
  const state=boot(href);
  assert.equal(state.replacements.length,1);
  assert.equal(state.replacements[0],new URL('site/',href).href);
 }
});
test('product, auth and study deep links stay in the app',()=>{
 for(const hash of ['#login','#dashboard','#upload','#library','#study','#viewer','#viewer/photo-123','#appraisal','#rooms','#assets','#schedule','#settings','#invite/token']){
  const state=boot('https://basis.test/?mode=live'+hash);
  assert.deepEqual(state.replacements,[],hash);
 }
});
test('returning from a product route to landing redirects without intercepting another study',()=>{
 const state=boot('https://basis.test/#dashboard');
 state.location.hash='#rooms';state.events.hashchange();assert.deepEqual(state.replacements,[]);
 state.location.hash='#landing';state.events.hashchange();assert.deepEqual(state.replacements,['https://basis.test/site/']);
});
test('public site does not load product controllers or link into a sample study',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../site/index.html'),'utf8');
 const scripts=[...html.matchAll(/<script[^>]*src="([^"]+)"/g)].map(m=>m[1]);
 assert.deepEqual(scripts.map(src=>new URL(src,'https://basis.test/site/').pathname),['/site/site.js']);
 const links=[...html.matchAll(/href="([^"]+)"/g)].map(m=>m[1]);
 const appLinks=links.filter(h=>h.startsWith('../?'));
 assert.equal(appLinks.length,3);
 assert(appLinks.every(h=>h==='../?mode=live#login'));
 assert(!links.some(h=>/#(dashboard|viewer|rooms|upload|study)/.test(h)));
});
