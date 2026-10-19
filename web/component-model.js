/* Local component correction values; does not alter masks or backend records. */
(function(scope){
 'use strict';
 function validate(input){
  const label=String(input.label??'').trim(),material=String(input.material??'').trim(),raw=String(input.count??'').trim(),count=Number(raw),errors={};
  if(!label)errors.label='Enter a component label.';
  else if(label.length>120)errors.label='Use 120 characters or fewer.';
  if(!/^\d+$/.test(raw)||!Number.isSafeInteger(count)||count<1)errors.count='Enter a whole number of 1 or more. Use Remove to exclude a component.';
  if(material.length>120)errors.material='Use 120 characters or fewer.';
  return {value:{label,count,material},errors,valid:Object.keys(errors).length===0};
 }
 function correction(base,value){return ['label','count','material'].every(k=>base[k]===value[k])?null:{label:value.label,count:value.count,material:value.material}}
 const api={validate,correction};if(typeof module==='object'&&module.exports)module.exports=api;else scope.ComponentCorrection=api;
})(typeof globalThis==='object'?globalThis:this);
