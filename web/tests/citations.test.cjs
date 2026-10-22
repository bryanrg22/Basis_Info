const {test}=require('node:test');const assert=require('node:assert/strict');
const C=require('../live/citations.js');
// Shaped like CitationEvidenceOut (basis.api.schemas.evidence); the excerpts are public IRS text.
const cite=(over={})=>({id:'c1',asset_id:'a1',chunk_id:'k1',excerpt:'bridges, fences, landscaping, shrubbery',verified_at:'2026-10-19T12:30:00Z',verification_method:'rule:resolved+normalized_substring',verified:true,source:{doc_id:'IRS_REV_PROC_87_56',page_start:7,page_end:7,section_path:'Revenue Procedure 87-56 > 00.3 Land Improvements:'},...over});
const text=html=>html.replace(/<[^>]+>/g,' ').replace(/&gt;/g,'>').replace(/&lt;/g,'<').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&amp;/g,'&').replace(/\s+/g,' ').trim();

test('names each reference corpus v1 document by its printed title, and humanizes unknown ids',()=>{
 for(const id of ['IRS_IRS_COST_SEG_ATG__2024','IRS_IRS_PUB_527__2024','IRS_IRS_PUB_946__2024','IRS_REV_PROC_87_56','RSMEANS_RSMEANS_BUILDING_2020','RSMEANS_RSMEANS_RESIDENTIAL_2020'])assert.doesNotMatch(C.docTitle(id),/_/,id);
 assert.equal(C.docTitle('IRS_IRS_COST_SEG_ATG__2024'),'IRS Cost Segregation Audit Techniques Guide (Pub. 5653)');
 assert.equal(C.docTitle('IRS_PUB_535__2025'),'IRS PUB 535 2025');
 assert.equal(C.docTitle(null),'Reference');
});
test('one page reads "p. N", a range "pp. N–M", and missing pages print nothing',()=>{
 assert.equal(C.pageLabel(312,312),'p. 312');
 assert.equal(C.pageLabel(312,null),'p. 312');
 assert.equal(C.pageLabel(297,322),'pp. 297–322');
 assert.equal(C.pageLabel(null,null),'');
 assert.equal(C.pageLabel(5,3),'p. 5');// a reversed range is not shown as one
});
test('every verification method the backend writes reads as words',()=>{
 // basis.classify.router: the three paths and their check lists.
 assert.deepEqual(C.method('rule:resolved+normalized_substring'),{path:'Classification rule',checks:['the cited passage exists in the reference corpus','the excerpt appears word for word in it']});
 assert.deepEqual(C.method('llm:resolved+retrieved+normalized_substring').checks.length,3);
 assert.equal(C.method('llm:resolved+retrieved+normalized_substring').path,'Model citation');
 assert.deepEqual(C.method('cache:resolved+substring'),{path:'Cached classification',checks:['the cited passage exists in the reference corpus','the excerpt appears word for word in it']});
 // verify_citations' default (no path) and a missing method
 assert.deepEqual(C.method('resolved+normalized_substring').path,'');
 assert.deepEqual(C.method(null),{path:'',checks:[]});
 assert.deepEqual(C.method('manual'),{path:'',checks:['manual']});
});
test('a citation card shows the document, page, section, excerpt and how it was found',()=>{
 const t=text(C.card(cite()));
 assert.equal(t,'Revenue Procedure 87-56 · p. 7 Revenue Procedure 87-56 > 00.3 Land Improvements: Verified bridges, fences, landscaping, shrubbery Classification rule');
 assert.match(C.card(cite()),/class="badge complete" title="Verified: the cited passage exists in the reference corpus; the excerpt appears word for word in it \(rule:resolved\+normalized_substring\)"/);
});
test('an unverified citation is badged for review, and a card without section or pages still renders',()=>{
 const html=C.card(cite({verified:false,verified_at:null,verification_method:null,source:{doc_id:'IRS_IRS_PUB_946__2024',page_start:null,page_end:null,section_path:null}}));
 assert.match(html,/class="badge review" title="Not verified against the reference text">Not verified</);
 assert.equal(text(html),'IRS Publication 946 (2024), How To Depreciate Property Not verified bridges, fences, landscaping, shrubbery');
});
test('text from the API is escaped',()=>{
 const html=C.card(cite({excerpt:'<img src=x onerror=alert(1)> "q" & \'a\'',source:{doc_id:'<b>X</b>',page_start:1,page_end:1,section_path:'A > <i>B</i>'}}));
 assert.doesNotMatch(html,/<img|<b>|<i>/);
 assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt; &quot;q&quot; &amp; &#39;a&#39;/);
 assert.match(html,/A &gt; &lt;i&gt;B&lt;\/i&gt;/);
});
test('the list: cards in API order, a plain empty state, and the dropped-citation notes',()=>{
 const two=[cite(),cite({id:'c2',source:{doc_id:'IRS_IRS_COST_SEG_ATG__2024',page_start:312,page_end:312,section_path:'VII. Chapter 7'}})];
 const html=C.list(two,[]);
 assert.equal((html.match(/<article class="live-citation">/g)||[]).length,2);
 assert.ok(html.indexOf('Revenue Procedure 87-56')<html.indexOf('Audit Techniques Guide'));
 assert.doesNotMatch(html,/dropped/);
 assert.equal(text(C.list([],[])),'No citations for this asset.');
 assert.equal(text(C.list(undefined,undefined)),'No citations for this asset.');
 assert.match(text(C.list([],['cross:COST_ROW_MISSING','citation_unverified'])),/^No verified citation supports this classification\./);
 assert.match(text(C.list(two,['citation_unverified'])),/^Some proposed citations did not match the reference text and were dropped\./);
});
