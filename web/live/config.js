/* Live mode configuration. Mock mode (the design review, review-data.js) is the default.
 *
 *   index.html                          mock data, no network
 *   index.html?mode=live                the API at apiBase, Firebase Auth emulator
 *   index.html?mode=live&api=http://127.0.0.1:8000
 *
 * The query string survives hash navigation, so the mode holds while you move between
 * screens. Edit the defaults below for a deployed environment (web/README.md).
 */
(function(scope){
 'use strict';
 const defaults={
  mode:'mock',
  apiBase:'http://127.0.0.1:8000',
  firebase:{
   // Local: the Auth emulator in a demo project (never reaches real Firebase).
   // Deployed: projectId cost-seg-basis, its web apiKey and authDomain, emulatorHost null.
   apiKey:'demo-api-key',
   authDomain:'demo-basis.firebaseapp.com',
   projectId:'demo-basis',
   emulatorHost:'http://127.0.0.1:9099',
  },
  // Google sign-in: 'popup' (default) or 'redirect' (also ?signin=redirect).
  signInFlow:'popup',
  // Upload and polling limits (the API's own limits are the authority).
  uploadConcurrency:3,
  pollInitialMs:1500,
  pollMaxMs:15000,
 };
 function fromQuery(search){
  const q=new URLSearchParams(search||''),out={};
  if(q.get('mode')==='live'||q.get('mode')==='mock')out.mode=q.get('mode');
  if(q.get('signin')==='redirect'||q.get('signin')==='popup')out.signInFlow=q.get('signin');
  const api=q.get('api');if(api&&/^https?:\/\/[^\s]+$/.test(api))out.apiBase=api.replace(/\/+$/,'');
  return out;
 }
 function resolve(search,overrides={}){
  const merged={...defaults,...overrides,...fromQuery(search)};
  merged.firebase={...defaults.firebase,...(overrides.firebase||{})};
  return merged;
 }
 const api={defaults,fromQuery,resolve};
 if(typeof module==='object'&&module.exports)module.exports=api;
 else scope.BASIS_CONFIG=resolve(scope.location?.search,scope.BASIS_CONFIG_OVERRIDES||{});
})(typeof globalThis==='object'?globalThis:this);
