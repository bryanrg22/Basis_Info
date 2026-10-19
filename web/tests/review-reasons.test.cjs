const {test}=require('node:test');const assert=require('node:assert/strict');
const R=require('../live/review-reasons.js');
const text=html=>html.replace(/<[^>]+>/g,' ').replace(/&gt;/g,'>').replace(/&lt;/g,'<').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&amp;/g,'&').replace(/\s+/g,' ').trim();
// Every reason the backend can write on an asset (see the module comment for the sources).
const WRITTEN=[
 'citation_unverified','missing_evidence','classifier_unavailable',
 'no_answer','tool_cap_exceeded','invalid_output','iteration_cap','timeout','budget_exceeded','provider_error:ProviderBadResponse',
 'rule_contested:half_wall',
 ...['LAND_NOT_DEPRECIABLE','SECTION_MISSING','SECTION_UNKNOWN','SECTION_BUCKET_MISMATCH','RECOVERY_PERIOD_MISMATCH','BUILDING_BUCKET_PROPERTY_CLASS','STRUCTURAL_AS_PERSONAL','PERSONAL_AS_BUILDING'].map(c=>'validation:'+c),
 ...['SECTION_1245_SF_LARGE','COST_NEEDS_QUANTITY','COST_ROW_MISSING','COST_BELOW_RANGE','COST_ABOVE_RANGE','COST_TOTAL_MISMATCH'].map(c=>'cross:'+c),
];

test('every code the backend writes reads as a sentence, never as the code',()=>{
 const seen=new Set();
 for(const code of WRITTEN){
  const r=R.explain(code);
  assert.equal(r.kind,'basis',code);
  assert.match(r.text,/^[A-Z].*[.]$/,code);
  assert.doesNotMatch(r.text,/_|[A-Z]{4,}_|^cross:|^validation:/,code);
  seen.add(r.text);
 }
 // the fallback reasons share their second sentence but each says what went wrong
 assert.equal(seen.size,WRITTEN.length);
});
test('the reasons seen on the real study read as intended',()=>{
 assert.equal(R.explain('cross:COST_ROW_MISSING').text,'No cost table row matches it, so its cost needs a manual estimate.');
 assert.equal(R.explain('cross:COST_NEEDS_QUANTITY').text,'The cost table prices it by area or length, but the takeoff only counts it, so it has no cost yet. Measure it to price it.');
 assert.equal(R.explain('rule_contested:light_fixture').text,'Basis classified it by its rule for “light fixture”, but the reference documents do not settle that treatment. Confirm the classification.');
 assert.match(R.explain('provider_error:ProviderBadResponse').text,/^The classification model call failed \(ProviderBadResponse\)\. The asset was put in the building class/);
 assert.match(R.explain('citation_unverified').text,/could not be found in the reference documents/);
});
test('model: keeps the model’s own words',()=>{
 const said='The label is "other" with no material or notes, so the item can\'t be identified.';
 assert.deepEqual(R.explain('model:'+said),{code:'model:'+said,kind:'model',text:said});
 assert.equal(R.title(['model:'+said]),`The classification model asked for review: “${said}”`);
 assert.equal(text(R.items(['model:'+said])),`The classification model asked for review, in its words: ${said}`);
 // the router's default when the model gives no reason, and an empty one
 for(const c of ['model:requested review','model:','model:  ']){
  assert.equal(R.explain(c).text,'',c);
  assert.equal(R.title([c]),'The classification model asked for review.');
 }
});
test('model text the API split on "; " is joined back, and codes after it stay codes',()=>{
 // the API splits review_reason on "; " (basis.api.routes.outputs); seen on the real study
 const api=['citation_unverified','model:The label is other; an engineer should confirm what the item is.','if it is something else, such as furniture, the bucket could change.','cross:COST_ROW_MISSING'];
 const all=R.explainAll(api);
 assert.deepEqual(all.map(r=>r.kind),['basis','model','basis']);
 assert.equal(all[1].text,'The label is other; an engineer should confirm what the item is.; if it is something else, such as furniture, the bucket could change.');
 assert.equal(all[1].code,api.slice(1,3).join('; '));
 assert.equal(all[2].code,'cross:COST_ROW_MISSING');
 // free text with no model: item before it is not swallowed
 assert.deepEqual(R.explainAll(['cross:COST_ROW_MISSING','free text here']).map(r=>r.text),['No cost table row matches it, so its cost needs a manual estimate.','Free text here']);
});
test('model text cut at 300 characters ends with an ellipsis; complete text does not',()=>{
 const cut=('model:'+'The room name conflicts with a porch label and the IRS guidance retrieved is from the retail industry table '.repeat(4)).slice(0,300);
 assert.equal(cut.length,300);
 assert.match(R.explain(cut).text,/[^.]…$/);
 const whole=('model:'+'x'.repeat(293)+'.');
 assert.equal(whole.length,300);
 assert.doesNotMatch(R.explain(whole).text,/…$/);
 assert.doesNotMatch(R.explain('model:short and unpunctuated').text,/…$/);
 // joined back from fragments, the length is the stored one
 const parts=cut.split('; ');
 if(parts.length>1)assert.match(R.explainAll(parts)[0].text,/…$/);
});
test('an unknown code still reads as words',()=>{
 assert.equal(R.explain('some_new_reason').text,'Some new reason');
 assert.equal(R.explain('cross:NEW_CHECK_CODE').text,'Cost and quantity check: new check code.');
 assert.equal(R.explain('validation:NEW_RULE').text,'Classification check: new rule.');
 assert.equal(R.explain('dropped_objects:3').text,'Dropped objects: 3');
 assert.equal(R.explain('validation:').text,'Validation:');
 assert.equal(R.explain('cross:').text,'Cross:');
});
test('empty input and blank items render nothing',()=>{
 assert.deepEqual(R.explainAll(undefined),[]);
 assert.deepEqual(R.explainAll(['',' ',null]),[]);
 assert.equal(R.title([]),'');
 assert.equal(R.items(null),'');
});
test('the tooltip has one reason per line, in the API order',()=>{
 assert.equal(R.title(['rule_contested:furniture','cross:COST_ROW_MISSING']).split('\n').length,2);
 assert.match(R.title(['rule_contested:furniture','cross:COST_ROW_MISSING']),/^Basis classified it by its rule for “furniture”.*\nNo cost table row/);
});
test('the list items carry the raw code on hover and escape everything',()=>{
 const html=R.items(['cross:COST_ROW_MISSING','model:<img src=x onerror=alert(1)> "q" & \'a\'','<b>x</b>']);
 assert.match(html,/<li title="cross:COST_ROW_MISSING">No cost table row/);
 assert.doesNotMatch(html,/<img|<b>/);
 assert.match(html,/<q class="live-reason-model">&lt;img src=x onerror=alert\(1\)&gt; &quot;q&quot; &amp; &#39;a&#39;<\/q>/);
 assert.match(html,/&lt;b&gt;x&lt;\/b&gt;/);
 // a long model code is shortened in the title, not in the text
 const long='model:'+'word '.repeat(50);
 assert.ok(R.items([long]).includes(`title="${long.trim().slice(0,80)}…"`));
 assert.ok(R.items([long]).includes(long.trim().slice(6)));
});
