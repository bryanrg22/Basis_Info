const {test}=require('node:test');
const assert=require('node:assert/strict');
const {validate,correction}=require('../component-model.js');
const base={label:'Pendant lights',count:3,material:'Metal / glass',score:'94%',id:'pendants'};
test('component correction normalizes entered values and preserves unrelated source attributes',()=>{
 const result=validate({label:'  Pendant fixtures ',count:'04',material:' Brass '});
 assert.equal(result.valid,true);assert.deepEqual(result.value,{label:'Pendant fixtures',count:4,material:'Brass'});
 const edit=correction(base,result.value);assert.equal(edit.score,undefined);assert.equal(base.count,3);assert.equal(base.score,'94%');
});
test('component counts require positive whole instances, while blank material remains explicit',()=>{
 for(const count of ['', '0', '-1', '1.5', '1e3', 'NaN', '9007199254740992'])assert.equal(validate({...base,count}).valid,false,count);
 assert.equal(validate({...base,count:'2',material:''}).valid,true);
 assert.equal(validate({...base,label:'  '}).errors.label,'Enter a component label.');
 assert.equal(validate({...base,material:'x'.repeat(121)}).valid,false);
});
test('restoring source values removes the correction rather than leaving a false edited state',()=>{
 const result=validate(base);assert.equal(result.valid,true);assert.equal(correction(base,result.value),null);
});
