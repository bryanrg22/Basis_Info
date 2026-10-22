/* Live mode: SAM 3 masks as the API sends them (MaskOut), decoded in the browser.
 *
 * A mask is {height, width, counts}: run lengths over the row-major flattened mask,
 * zeros first ([0, ...] when the first pixel is set), covering every pixel
 * (basis.vision.localize.Rle). The mask covers the whole photo at the size the model saw
 * it, so it is drawn scaled to the displayed photo; boxes are normalized 0-1.
 */
(function(scope){
 'use strict';
 function check(mask){
  if(!mask||!Number.isInteger(mask.width)||!Number.isInteger(mask.height)||!Array.isArray(mask.counts))throw new Error('not a mask');
  let total=0;for(const run of mask.counts){if(!Number.isInteger(run)||run<0)throw new Error('a run length is not a whole number');total+=run}
  if(total!==mask.width*mask.height)throw new Error(`runs cover ${total} pixels, the mask has ${mask.width*mask.height}`);
 }
 /* One byte per pixel, 1 where the mask is set. */
 function decode(mask){
  check(mask);const out=new Uint8Array(mask.width*mask.height);let at=0;
  mask.counts.forEach((run,i)=>{if(i%2===1)out.fill(1,at,at+run);at+=run});
  return out;
 }
 /* Set pixels, their bounds (exclusive ends) and centroid, in mask pixels. */
 function stats(mask,bits=decode(mask)){
  const {width,height}=mask;let area=0,sx=0,sy=0,x1=width,y1=height,x2=0,y2=0;
  for(let y=0,i=0;y<height;y++)for(let x=0;x<width;x++,i++)if(bits[i]){area++;sx+=x;sy+=y;if(x<x1)x1=x;if(y<y1)y1=y;if(x>=x2)x2=x+1;if(y>=y2)y2=y+1}
  return area?{area,fraction:area/(width*height),cx:sx/area,cy:sy/area,bounds:{x1,y1,x2,y2}}:{area:0,fraction:0,cx:null,cy:null,bounds:null};
 }
 /* Pixels that are set and touch an unset 4-neighbour (or the frame): a 1 px outline. */
 function edges(mask,bits=decode(mask)){
  const {width,height}=mask,out=new Uint8Array(width*height);
  for(let y=0,i=0;y<height;y++)for(let x=0;x<width;x++,i++){
   if(!bits[i])continue;
   if(x===0||y===0||x===width-1||y===height-1||!bits[i-1]||!bits[i+1]||!bits[i-width]||!bits[i+width])out[i]=1;
  }
  return out;
 }
 /* Is the normalized point (0-1 of the photo) inside the mask? */
 function contains(mask,bits,u,v){
  if(!(u>=0&&u<1&&v>=0&&v<1))return false;
  return bits[Math.floor(v*mask.height)*mask.width+Math.floor(u*mask.width)]===1;
 }
 /* RGBA pixels for a canvas ImageData: colour where set, transparent elsewhere. */
 function rgba(bits,[r,g,b],alpha=255){
  const out=new Uint8ClampedArray(bits.length*4);
  for(let i=0,j=0;i<bits.length;i++,j+=4)if(bits[i]){out[j]=r;out[j+1]=g;out[j+2]=b;out[j+3]=alpha}
  return out;
 }
 const api={check,decode,stats,edges,contains,rgba};
 if(typeof module==='object'&&module.exports)module.exports=api;else scope.BasisRle=api;
})(typeof globalThis==='object'?globalThis:this);
