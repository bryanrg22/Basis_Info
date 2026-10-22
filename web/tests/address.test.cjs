const {test}=require('node:test');
const assert=require('node:assert/strict');
const {matches,format}=require('../address-lookup.js');
test('address suggestions match partial street, city, postal and punctuation without returning unrelated samples',()=>{
 assert.equal(matches('Harbor')[0].street,'1200 Harbor Boulevard');
 assert.equal(matches('1200, harbor')[0].city,'Ventura');
 assert.equal(matches('Pasadena 91101')[0].street,'480 Fairview Avenue');
 assert.equal(matches('64 Los Angeles')[0].street,'64 Lakewood Drive');
 assert.deepEqual(matches('Ventura Willow'),[]);
});
test('empty, very short and unknown addresses do not fabricate lookup results',()=>{
 for(const q of ['',null,'x','!!!','12345 New Road'])assert.deepEqual(matches(q),[]);
 assert.equal(format(matches('harbor')[0]),'1200 Harbor Boulevard, Ventura, CA 93001');
});
