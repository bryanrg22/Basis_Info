// Phones get the public pages only (site/landing/src/phone.js, public-entry.js): the device
// matrix, the app's routing on a phone and on a computer, and the two pages' wiring.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const phoneSource=fs.readFileSync(path.join(__dirname,'../site/landing/src/phone.js'),'utf8');
const entrySource=fs.readFileSync(path.join(__dirname,'../public-entry.js'),'utf8');

// A device: screen in CSS px as the browser reports it, touch points, and whether the primary
// pointer is coarse (null: no matchMedia at all).
function device({w,h,touch=0,coarse=touch>0}){
 const dataset={};
 const win={navigator:{maxTouchPoints:touch},screen:{width:w,height:h},document:{documentElement:{dataset}}};
 if(coarse!==null)win.matchMedia=q=>({matches:q==='(pointer: coarse)'&&coarse});
 win.self=win;
 vm.runInNewContext(phoneSource,win);
 return win;
}
const phones={
 'iPhone SE':{w:375,h:667,touch:5},
 'iPhone 17 Pro Max':{w:440,h:956,touch:5},
 'Pixel 9':{w:412,h:915,touch:5},
 'Galaxy S folded, landscape (Android swaps the screen)':{w:882,h:344,touch:5},
 'iPhone held sideways (iOS keeps portrait numbers)':{w:393,h:852,touch:5},
 'coarse pointer, no touch-points API':{w:390,h:844,touch:0,coarse:true},
};
const computersAndTablets={
 'iPad mini':{w:744,h:1133,touch:5},
 'iPad Pro 13':{w:1032,h:1376,touch:5},
 'Galaxy Z Fold open':{w:673,h:841,touch:5},
 'desktop':{w:1440,h:900},
 'touchscreen laptop':{w:1280,h:800,touch:10,coarse:false},
 'phone-sized screen with a mouse only':{w:390,h:844,touch:0,coarse:false},
 'old browser: no touch API, no matchMedia':{w:375,h:667,touch:0,coarse:null},
 'no screen numbers':{w:0,h:0,touch:5},
};
test('phones are touch-first devices with a screen under 600 px on its short side',()=>{
 for(const [name,d] of Object.entries(phones)){
  const win=device(d);
  assert.equal(win.basisIsPhone(win),true,name);
  assert.equal(win.document.documentElement.dataset.device,'phone',name);
 }
});
test('tablets, laptops, desktops and narrow desktop windows are not phones',()=>{
 for(const [name,d] of Object.entries(computersAndTablets)){
  const win=device(d);
  assert.equal(win.basisIsPhone(win),false,name);
  assert.equal(win.document.documentElement.dataset.device,undefined,name);
 }
});

function boot(href,isPhone){
 const location=new URL(href),replacements=[],events={};let stopped=0;
 location.replace=url=>replacements.push(url);
 const ctx={location,URL,addEventListener:(n,fn)=>events[n]=fn};
 if(isPhone!==undefined){ctx.basisIsPhone=()=>isPhone;ctx.stop=()=>{stopped++;assert.equal(replacements.length,0,'stop before replace')};}
 ctx.self=ctx;
 vm.runInNewContext(entrySource,ctx);
 return {location,replacements,events,get stopped(){return stopped}};
}
const product=['#login','#dashboard','#upload','#library','#study','#viewer/photo-123','#rooms','#assets','#settings','#invite/token'];
test('on a phone every product route stops the app and goes to the sign-in page',()=>{
 for(const hash of product){
  const s=boot('https://basis.test/?mode=live'+hash,true);
  assert.equal(s.stopped,1,hash);
  assert.deepEqual(s.replacements,['https://basis.test/site/landing/signin.html'],hash);
 }
 const nested=boot('https://basis.test/web/index.html?mode=live#login',true);
 assert.deepEqual(nested.replacements,['https://basis.test/web/site/landing/signin.html']);
});
test('on a phone the public routes still reach the marketing site, which works there',()=>{
 for(const href of ['https://basis.test/','https://basis.test/#landing']){
  const s=boot(href,true);
  assert.equal(s.stopped,0);
  assert.deepEqual(s.replacements,[new URL('site/',href).href]);
 }
 const s=boot('https://basis.test/#landing',true);
 s.replacements.length=0;s.location.hash='#rooms';s.events.hashchange();
 assert.deepEqual(s.replacements,['https://basis.test/site/landing/signin.html']);
});
test('on a computer, and without the phone check loaded, product routes stay in the app',()=>{
 for(const isPhone of [false,undefined]){
  for(const hash of product){
   const s=boot('https://basis.test/?mode=live'+hash,isPhone);
   assert.deepEqual(s.replacements,[],`${hash} ${isPhone}`);
   assert.equal(s.stopped,0);
  }
 }
});

const scripts=html=>[...html.matchAll(/<script\b([^>]*)>/g)].map(m=>m[1]);
test('the app loads the phone check first, synchronously, before the entry script',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
 const head=html.slice(0,html.indexOf('</head>'));
 const tags=scripts(head);
 assert.match(tags[0],/src="site\/landing\/src\/phone\.js/);
 assert.match(tags[1],/src="public-entry\.js/);
 for(const t of tags.slice(0,2))assert.doesNotMatch(t,/\b(defer|async|type="module")/);
});
test('the sign-in page sets the phone flag before its styles and shows the note on a phone',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../site/landing/signin.html'),'utf8');
 const head=html.slice(0,html.indexOf('</head>'));
 const phoneAt=head.indexOf('<script src="src/phone.js"></script>');
 assert(phoneAt>0&&phoneAt<head.indexOf('<link rel="stylesheet"'));
 const note=html.match(/<div class="phone-note" data-phone-note>([\s\S]*?)<\/div>/);
 assert(note,'phone note present');
 assert.match(note[1],/isn’t available on phones\. Sign in on your computer to open your workspace\./);
 assert.match(note[1],/<a href="demo\.html">Book a demo<\/a>/);
 const css=fs.readFileSync(path.join(__dirname,'../site/landing/src/entry.css'),'utf8');
 assert.match(css,/\.phone-note\{display:none/);
 assert.match(css,/html\[data-device=phone\] \.phone-note\{display:grid\}/);
 assert.match(css,/html\[data-device=phone\] \.entry-form \[data-signin\],html\[data-device=phone\] \.entry-form>\.sub\{display:none\}/);
});
test('the demo page and the landing page do not load the phone check: they work on phones',()=>{
 for(const page of ['index.html','demo.html']){
  const html=fs.readFileSync(path.join(__dirname,'../site/landing',page),'utf8');
  assert.doesNotMatch(html,/phone\.js/,page);
 }
});
