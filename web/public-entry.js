/* Public marketing is a standalone document. Product deep links retain their routes. */
(function(){
 'use strict';
 const publicRoute=()=>!location.hash||location.hash==='#landing';
 const go=()=>{if(publicRoute())location.replace(new URL('site/',location.href).href)};
 addEventListener('hashchange',go);go();
})();
