const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../team-review.js'),'utf8');
const sidebarSource=source.slice(source.indexOf('let sidebarOpenTimer'),source.indexOf('const originalRender=render;'));
function setup(){
 const timers=new Map(),listeners=new Map();let timerId=0,popup=null;
 const state={sidebarExpanded:false,sidebarPeek:false};
 const classes=()=>({values:new Set(),toggle(name,on){on?this.values.add(name):this.values.delete(name)}});
 const sidebar={hover:false,isConnected:true,classList:classes(),matches:s=>s===':hover'?sidebar.hover:s==='.sidebar',contains:el=>el===toggle||el===link,closest:()=>sidebar};
 const element=(isToggle=false)=>({keyboard:false,attrs:{},matches(s){return s===':focus-visible'?this.keyboard:s==='.sidebar-toggle'&&isToggle},closest:s=>s==='.sidebar'?sidebar:null,setAttribute(k,v){this.attrs[k]=v},focus(){document.activeElement=this}});
 const toggle=element(true),link=element(),outside={};
 const workspace={classList:classes()},scrim={setAttribute(){}};
 const on=(target)=>(name,fn)=>{const key=target+':'+name;if(!listeners.has(key))listeners.set(key,[]);listeners.get(key).push(fn)};
 const document={hidden:false,activeElement:outside,getElementById:()=>popup,querySelector:()=>null,addEventListener:on('document')};
 const root={querySelector:s=>({'.sidebar':sidebar,'.workspace':workspace,'.sidebar-toggle':toggle,'.sidebar-scrim':scrim}[s]),addEventListener:on('root')};
 const media={matches:true,addEventListener:on('media')};
 const ctx=vm.createContext({state,root,document,window:{addEventListener:on('window')},matchMedia:()=>media,setTimeout(fn){timers.set(++timerId,fn);return timerId},clearTimeout(id){timers.delete(id)}});
 vm.runInContext(sidebarSource,ctx);
 const fire=(target,name,event={})=>(listeners.get(target+':'+name)||[]).forEach(fn=>fn(event));
 const flush=()=>{for(const [id,fn]of [...timers]){timers.delete(id);fn()}};
 const enter=()=>{sidebar.hover=true;fire('root','pointerenter',{target:sidebar,pointerType:'mouse'})};
 const leave=()=>{sidebar.hover=false;fire('root','pointerleave',{target:sidebar})};
 return {ctx,state,sidebar,toggle,link,document,workspace,media,timers,fire,flush,enter,leave,setPopup:p=>{popup=p}};
}
test('mouse focus left on the toggle does not trap an unpinned hover preview',()=>{
 const p=setup();p.ctx.setSidebarExpanded(true);p.ctx.setSidebarExpanded(false);
 p.enter();p.flush();assert.equal(p.state.sidebarPeek,true);
 p.leave();p.flush();assert.equal(p.state.sidebarPeek,false);assert.equal(p.document.activeElement,p.toggle);
});
test('quick pointer exit cancels opening and re-entry cancels closing',()=>{
 const p=setup();p.enter();p.leave();p.flush();assert.equal(p.state.sidebarPeek,false);
 p.enter();p.flush();p.leave();p.enter();p.flush();assert.equal(p.state.sidebarPeek,true);
});
test('keyboard navigation holds the preview until focus leaves the rail',()=>{
 const p=setup();p.link.keyboard=true;p.document.activeElement=p.link;
 p.fire('root','focusin',{target:p.link});assert.equal(p.state.sidebarPeek,true);
 p.leave();p.flush();assert.equal(p.state.sidebarPeek,true);
 p.document.activeElement={};p.fire('root','focusout',{target:p.link,relatedTarget:p.document.activeElement});p.flush();assert.equal(p.state.sidebarPeek,false);
});
test('Escape dismisses a keyboard preview without reopening on the toggle',()=>{
 const p=setup();p.state.sidebarPeek=true;p.toggle.keyboard=true;
 p.fire('root','keydown',{key:'Escape',preventDefault(){}});
 p.fire('root','focusin',{target:p.toggle});assert.equal(p.state.sidebarPeek,false);assert.equal(p.document.activeElement,p.toggle);
});
for(const event of ['blur','visibilitychange','pointercancel'])test(`${event} clears hover and pending timers, even when the browser retains :hover`,()=>{
 const p=setup();p.enter();p.flush();p.enter();p.document.hidden=event==='visibilitychange';
 p.fire(event==='blur'?'window':event==='visibilitychange'?'document':'root',event);p.flush();
 assert.equal(p.state.sidebarPeek,false);assert.equal(p.timers.size,0);assert.equal(p.sidebar.hover,true);
 p.document.hidden=false;p.fire('document','visibilitychange');assert.equal(p.state.sidebarPeek,false);
});
test('an explicitly pinned rail survives mouse exit and tab switches',()=>{
 const p=setup();p.ctx.setSidebarExpanded(true);p.enter();p.leave();p.flush();p.fire('window','blur');
 p.document.hidden=true;p.fire('document','visibilitychange');
 assert.equal(p.state.sidebarExpanded,true);assert.equal(p.sidebar.classList.values.has('is-expanded'),true);
});
test('stale open timer cannot expand a detached or hidden sidebar',()=>{
 for(const hidden of [false,true]){const p=setup();p.enter();p.sidebar.isConnected=hidden;p.document.hidden=hidden;p.flush();assert.equal(p.state.sidebarPeek,false)}
});
test('touch does not trigger hover, and a breakpoint change dismisses preview',()=>{
 const p=setup();p.sidebar.hover=true;p.fire('root','pointerenter',{target:p.sidebar,pointerType:'touch'});p.flush();assert.equal(p.state.sidebarPeek,false);
 p.enter();p.flush();p.fire('media','change');assert.equal(p.state.sidebarPeek,false);
});
test('account popup keeps the rail until dismissed, then mouse focus does not pin it',()=>{
 const p=setup();p.document.activeElement=p.toggle;p.enter();p.flush();p.setPopup({});p.leave();p.flush();assert.equal(p.state.sidebarPeek,true);
 p.setPopup(null);p.ctx.scheduleSidebarClose();p.flush();assert.equal(p.state.sidebarPeek,false);
});
