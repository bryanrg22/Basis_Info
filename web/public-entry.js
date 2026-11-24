/* Public marketing is a standalone document. Product deep links retain their routes.
   On a phone (site/landing/src/phone.js, loaded just before this file) the product does not
   load: every product route goes to the public sign-in page, which tells the visitor to sign in
   on a computer. Public routes still go to the marketing document, which works on phones. */
(function(){
 'use strict';
 const publicRoute=()=>!location.hash||location.hash==='#landing';
 const phone=typeof basisIsPhone==='function'&&basisIsPhone(self);
 const go=()=>{
  if(publicRoute()){location.replace(new URL('site/',location.href).href);return}
  if(phone){
   // Stop the parser first so none of the app's scripts run (no sign-in, no API calls), then
   // leave; stop() before replace(), because stop() would cancel the navigation.
   if(typeof stop==='function')stop();
   location.replace(new URL('site/landing/signin.html',location.href).href);
  }
 };
 addEventListener('hashchange',go);go();
})();
