
const {test}=require('node:test');const assert=require('node:assert/strict');
const {matchField}=require('../live/pdf-source.js');
const text=value=>({value_text:value});const num=value=>({value_num:value});
test('finds a field across PDF runs with exact character offsets',()=>{
 assert.deepEqual(matchField(['Address:','1200 Harbor','Boulevard'],text('1200 Harbor Boulevard')),[[{index:1,start:0,end:11},{index:2,start:0,end:9}]]);
 assert.deepEqual(matchField(['Property: 1200 Harbor Boulevard, CA'],text('1200 Harbor Boulevard')),[[{index:0,start:10,end:31}]]);
});
test('compares complete numeric tokens including currency, commas and trailing precision',()=>{
 assert.equal(matchField(['$280,000'],num('280000.0000')).length,1);
 assert.equal(matchField(['90.90'],num('90.900000')).length,1);
 assert.equal(matchField(['0','100','8000','18','8.1','8'],num(8)).length,1);
 assert.equal(matchField(['-8','+8','8.0'],num(8)).length,2);
 assert.equal(matchField(['123.45'],num(45)).length,0);
 assert.equal(matchField(['A123'],num(123)).length,0);
});
test('reports all duplicates and never substitutes the label for a missing value',()=>{
 assert.equal(matchField(['90.90','90.90'],num('90.9')).length,2);
 assert.deepEqual(matchField(['Site value'],{label:'Site value',value_num:280000}),[]);
 assert.deepEqual(matchField([],text('missing')),[]);
 assert.deepEqual(matchField(['unset'],{}),[]);
});
test('matches supported source date formats and literal punctuation without regex interpretation',()=>{
 assert.equal(matchField(['September 30, 2026','09/30/2026','2026-09-30'],{value_date:'2026-09-30'}).length,3);
 assert.equal(matchField(['A+B (revised)'],text('A+B (revised)')).length,1);
 assert.equal(matchField(['theater'],text('heat')).length,0);
});
