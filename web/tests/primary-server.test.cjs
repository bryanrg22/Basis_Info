const {test}=require('node:test'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
test('primary server serves its own checkout from another cwd and bypasses stale conditional caches',{timeout:10000},async t=>{
 const root=path.resolve(__dirname,'..');
 const child=spawn('python3',['-u',path.join(root,'serve.py'),'--port','0'],{cwd:os.tmpdir(),stdio:['ignore','pipe','pipe']});
 t.after(()=>child.kill());
 const port=await new Promise((resolve,reject)=>{
  let output='';child.stdout.on('data',b=>{output+=b;const match=output.match(/http:\/\/127\.0\.0\.1:(\d+)/);if(match)resolve(Number(match[1]))});
  child.on('error',reject);child.on('exit',code=>reject(Error('Server exited '+code)));
 });
 const response=await fetch(`http://127.0.0.1:${port}/`,{headers:{'If-Modified-Since':'Mon, 01 Jan 2091 00:00:00 GMT'}});
 assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'no-store');
 assert.equal(await response.text(),fs.readFileSync(path.join(root,'index.html'),'utf8'));
 const module=await fetch(`http://127.0.0.1:${port}/live/room-placement.js`);
 assert.equal(module.status,200);assert.match(await module.text(),/placement-drop-active/);
});
