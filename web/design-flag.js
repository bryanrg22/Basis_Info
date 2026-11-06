/* The "New design" preference (Settings > New design): the reworked Assets, Results and Report &
 * sign pages, off by default. Saved in this browser only; a blocked or cleared storage reads as
 * off, so the current pages always render. */
(function(scope){
 'use strict';
 const KEY='basis-new-design-v1';
 const read=()=>{try{return scope.localStorage?.getItem(KEY)==='on'}catch{return false}};
 let on=read();
 const apply=()=>{if(scope.document)scope.document.documentElement.dataset.design=on?'new':'current'};
 const api={
  KEY,
  enabled:()=>on,
  set(value){on=Boolean(value);try{on?scope.localStorage.setItem(KEY,'on'):scope.localStorage.removeItem(KEY)}catch{}apply();return on},
 };
 apply();
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisDesign=api;
})(typeof globalThis==='object'?globalThis:this);
