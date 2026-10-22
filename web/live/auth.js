/* Live mode sign-in with Firebase Authentication (the official JS SDK, loaded on demand).
 *
 * Google sign-in is the product flow. Locally the SDK talks to the Auth emulator
 * (BASIS_CONFIG.firebase.emulatorHost), whose Google popup offers test accounts, and the
 * API accepts the emulator's tokens only in BASIS_ENV=dev. Email and password is offered
 * only with the emulator, for local test accounts. The ID token goes to the API as a
 * bearer token; the SDK refreshes it before it expires.
 */
(function(scope){
 'use strict';
 const SDK='https://www.gstatic.com/firebasejs/10.14.1/';
 let sdk=null,auth=null,ready=null,user=null,redirectError='';const listeners=new Set();
 function init(config){
  if(ready)return ready;
  ready=(async()=>{
   const [{initializeApp},authSdk]=await Promise.all([import(SDK+'firebase-app.js'),import(SDK+'firebase-auth.js')]);
   sdk=authSdk;
   const app=initializeApp({apiKey:config.apiKey,authDomain:config.authDomain,projectId:config.projectId},'basis-web');
   auth=sdk.getAuth(app);
   if(config.emulatorHost)sdk.connectAuthEmulator(auth,config.emulatorHost,{disableWarnings:true});
   await sdk.setPersistence(auth,sdk.browserLocalPersistence);
   // Finishes a redirect sign-in (no-op otherwise); its error is shown on the sign-in page.
   try{await sdk.getRedirectResult(auth)}catch(e){redirectError=describe(e)}
   await new Promise(resolve=>{let first=true;sdk.onAuthStateChanged(auth,next=>{user=next;listeners.forEach(fn=>fn(next));if(first){first=false;resolve()}})});
   return auth;
  })();
  return ready;
 }
 const describe=error=>({
  'auth/popup-closed-by-user':'The sign-in window was closed before you finished.',
  'auth/popup-blocked':'Your browser blocked the sign-in window. Allow pop-ups for this page and try again.',
  'auth/invalid-credential':'That email and password do not match a test account.',
  'auth/invalid-email':'Enter a valid email address.',
  'auth/missing-password':'Enter the password.',
  'auth/weak-password':'Use a password of at least 6 characters.',
  'auth/email-already-in-use':'That test account already exists. Sign in instead.',
  'auth/network-request-failed':'The sign-in service could not be reached. Is the Auth emulator running?',
 })[error?.code]||error?.message||'Sign-in failed.';
 // Pop-up by default. 'redirect' (config signInFlow, or ?signin=redirect) leaves the page
 // and comes back; it suits browsers without pop-ups. In production a redirect needs the
 // authDomain served from the app's own domain (Firebase: "redirect best practices").
 async function signInWithGoogle(flow='popup'){
  await ready;const provider=new sdk.GoogleAuthProvider();
  try{
   if(flow==='redirect'){await sdk.signInWithRedirect(auth,provider);return null}
   return (await sdk.signInWithPopup(auth,provider)).user;
  }catch(e){throw new Error(describe(e))}
 }
 async function signInWithEmail(email,password,{create=false}={}){
  await ready;
  try{return (await (create?sdk.createUserWithEmailAndPassword:sdk.signInWithEmailAndPassword)(auth,email,password)).user}catch(e){throw new Error(describe(e))}
 }
 async function signOut(){await ready;await sdk.signOut(auth)}
 async function getToken(){await ready;return user?user.getIdToken():null}
 function onChange(fn){listeners.add(fn);return ()=>listeners.delete(fn)}
 scope.BasisAuth={init,signInWithGoogle,signInWithEmail,signOut,getToken,onChange,get user(){return user},get redirectError(){return redirectError},describe};
})(globalThis);
