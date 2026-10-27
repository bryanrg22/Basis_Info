# Appraisal field extraction for Basis: research before building

Date: 2026-10-26. Scope: web research plus a few local checks on public GSE sample files. No Basis code was changed.

Labels used throughout:
- **[Confirmed]**: read in a primary source (linked), or checked by running a command on a public file.
- **[Vendor claim]**: a company's statement about its own product, not independently tested.
- **[Inference]**: my reasoning from the confirmed facts. Not tested.

---

## 0. TL;DR

1. **Structured data exists, but it usually lives with the lender or AMC, not inside the PDF the taxpayer has.** In the legacy UAD 2.6 workflow the MISMO XML *contains* the PDF as base64, not the other way round. In UAD 3.6 the delivery is a ZIP holding the XML, the PDF and an Images folder. The PDF on its own carries no data layer. In the GSE's own UAD 3.6 sample PDF: no embedded files, and an AcroForm dictionary with 0 fields. **[Confirmed]** Cheap win: accept `.xml`/`.zip`/`.env` uploads and parse them losslessly; probe every PDF for `/EmbeddedFiles` and AcroForm fields. Expect that probe to be empty most of the time. **[Inference]**
2. **UAD 3.6 (the redesigned URAR) is mandatory for UCDP submissions on or after 2026-11-02, one week from today.** **[Confirmed]** The form layout changes completely. A legacy-1004 template or regex parser will not transfer. **[Inference]** The GSEs publish 12 sample scenarios as PDF and XML pairs. That is free, labeled ground truth for regression tests on the new format. **[Confirmed: downloaded and inspected]**
3. **For cost segregation, the appraisal's most important job is land vs. building allocation.** The IRS ATG says "Land value is always determined first", based on highest and best use. It also warns against valuing land by residual subtraction and against relying *solely* on assessed values. **[Confirmed]** But Fannie Mae does not require the cost approach, which is where the site value usually sits (except for manufactured homes). The legacy form header itself says "not required by Fannie Mae". So **site value is often legitimately absent.** **[Confirmed]** In Basis today, `cost_approach.site_value` is **not** a critical field, while rooms, bedrooms and bathrooms are. That priority looks inverted for cost seg. **[Confirmed in code; the judgment is inference]**
4. **Azure DI does have a prebuilt 1004 model** (`prebuilt-mortgage.us.1004`). **[Confirmed]** Its schema has **no** site value, cost-new, depreciation, GLA, site area or garage fields. It only has the cost-approach indicated value. **[Confirmed]** So it cannot replace Basis's current `prebuilt-layout` approach for the fields cost seg needs. AWS Textract Analyze Lending and Google Document AI have **no** appraisal or 1004 class. **[Confirmed]**
5. **Recommended architecture: a hybrid, not a switch.** (a) Lossless path first: XML/ZIP, embedded files, AcroForm. (b) Keep Azure DI layout plus text layer as the grounded candidate generator. (c) A page-image-plus-text multimodal LLM pass for gaps and conflicts, which must return a page and the verbatim quote it read from. (d) **Deterministic validators** such as cost-approach arithmetic, cross-section agreement and plausibility ranges. They decide review routing, rather than model-reported confidence. (e) An evaluation harness built on the GSE sample pairs plus hand-labeled client appraisals, with Wilson intervals. **[Inference, defended in section 5]**

---

## 1. Structured data that may already exist

### 1.1 UAD 2.6 (legacy) delivery: XML wraps the PDF

- Lenders must submit appraisals to the GSEs' **Uniform Collateral Data Portal (UCDP)** as an XML data stream produced by the appraisal-forms software, **with an embedded PDF** that includes all exhibits, addenda and photos. Format: **MISMO 2.6 Errata 1 "GSE Extended"** for UAD forms. **[Confirmed]** Sources: [Fannie Mae UCDP overview](https://singlefamily.fanniemae.com/media/document/pdf/ucdp-overview), [Freddie Mac UAD fact sheet](https://my.sf.freddiemac.com/docs/pdf/fact-sheet/uniform_appraisal_dataset_879.pdf), [Freddie Mac UCDP fact sheet](https://my.sf.freddiemac.com/docs/pdf/fact-sheet/uniform_collateral_data_portal_885_final.pdf).
- The PDF inside the XML is **base64-encoded**. The UCDP validation announcement requires no whitespace before the base64 data. **[Confirmed]** Sources: [Freddie Mac UCDP validation announcement](https://my.sf.freddiemac.com/docs/pdf/press-release/ucdp-validation-announcement.pdf), [a la mode UAD Reader: View the Embedded PDF](https://help.alamode.com/UADReader/UADReaderViewPDF.htm).
- Appraisal software can save the XML and the PDF separately, for example TOTAL's "Save XML and PDF to your PC". **[Confirmed]** Source: [a la mode TOTAL help](https://helpdrk.alamode.com/appraiser/total/SaveXMLAndPDF.htm). ACI calls its MISMO package "ENV" / "AI Ready". Source: [ACI](https://aciweb.com/news/author/jhook/page/12). I did not confirm the exact internal structure of ENV files. Treat them as vendor containers that hold MISMO XML. **[Inference]**
- Fannie Mae offers an **Appraisal File Retrieval API** that returns the full MISMO XML, including the embedded PDF, to lenders. **[Confirmed, via search summary]**
- **Implication [Inference]:** a Basis client is typically the property owner or their CPA. They usually hold only the PDF, which they get as the borrower. The XML sits with the lender, AMC or appraiser. For commercial and private non-GSE appraisals, there may be no XML at all. The lossless path is therefore *opportunistic*. It works when the client can ask their lender, AMC or appraiser for "the MISMO XML".

### 1.2 UAD 3.6 and the redesigned URAR (2025–2027)

- **Timeline [Confirmed]:** Limited Production started 2025-09-08. Broad Production started 2026-01-26. **Mandatory for initial UCDP submissions on or after 2026-11-02.** UAD 2.6 revisions are accepted until retirement. One secondary source says 2027-05-03; Fannie's page mentions a policy-exception window to 2027-05-19; Freddie's FAQ (exception announced 2026-09-30) gives no end date. Confirm the end date on the GSE pages before relying on it. Sources: [Freddie Mac UAD/forms redesign FAQ](https://sf.freddiemac.com/faqs/uad-and-forms-redesign), [McKissock summary](https://www.mckissock.com/blog/appraisal/the-future-is-now-fannie-mae-and-freddie-mac-announce-uad-3-6-implementation-timeline-and-policy-changes/), [Fannie Mae UAD page](https://fanniemae.com/uad).
- **Scope [Confirmed]:** one dynamic URAR replaces 1004/70, 1004 Desktop and Hybrid, 1073/465, 1075/466, 2055, 1004C/70B, 2090/2095, 1025/72, 1004D/442 (split into two reports) and 1007/1000. Form 216/998 is retired. Land-only appraisals are out of scope. Source: [Freddie Mac FAQ](https://sf.freddiemac.com/faqs/uad-and-forms-redesign).
- **Data standard [Confirmed]:** MISMO v3.6 (reference model `3.6.0366` in the samples). Delivery is a **ZIP containing the UAD 3.6 XML, a PDF of the report, and an Images folder**. Source: [Clear Capital UAD 3.6 explainer](https://www.clearcapital.com/8jb4), ICE/Encompass appraisal docs.
- **Specs published by the GSEs [Confirmed]:** see the [Freddie Mac UAD page](https://sf.freddiemac.com/tools-learning/uniform-mortgage-data-program/uad):
  - A-1 Delivery Specification (XML mapping, .xlsx)
  - B-1 Implementation Guide (PDF display logic)
  - C-1 URAR Layout
  - **D-1 Sample Scenarios and XML Files**
  - F-1 Reference Guide
  - **G-1 Redesign-to-Legacy cross-reference** (useful for mapping the Basis catalogue)
  - H-1 Compliance Rules (.xlsx). These are machine-readable consistency rules worth mining for validators.

#### Local verification on the D-1 sample ZIP (public, downloaded 2026-10-26)

`https://sf.freddiemac.com/docs/zip/requirements/appendix-d-1-urar-sample-scenarios-and-xml-files.zip` (64.8 MB). Files are kept privately under `BASIS_EVAL_DATA_DIR/appraisal/gse_d1/`.

- **Contents:** 12 scenarios: SF1–SF5, Condo1–2, Coop1, MH1–2 and two 2-to-4-unit. Each is a ZIP of `<name>.pdf`, `<name>.xml` and `Images/`, plus a scenario matrix .xlsx. **[Confirmed]**
- **PDF anatomy (SF1):**
  - InDesign-generated, 23 pages, 612×1008 pt (legal size), with a full text layer
  - `pdfdetach -list` gives **0 embedded files**
  - `/AcroForm` present with **0 fields**
  - **[Confirmed]** So even the GSE's own sample PDF carries no extractable data layer.
- Every sample PDF has a text layer (40–65k characters each). Each starts with a cover section that is "not part of the URAR", which must be skipped in evaluation. **[Confirmed]**
- **Cost approach in the samples:**
  - `CostApproachIndicator=true` only in MH1 and MH2 (manufactured homes)
  - `SiteValueIndicator=true` in SF3, where the cover says "Site value was developed at the request of the client"
  - The other 9 scenarios have no site value
  - **[Confirmed]** The SF3 case means that under UAD 3.6 a client can request a site value without a full cost approach. That is directly useful to a cost-seg buyer who orders an appraisal. **[Inference]**
- **Arithmetic is exact and checkable in the XML and PDF (MH1) [Confirmed]:**
  - Indicated value by cost approach = depreciated dwellings + depreciated outbuildings + as-is site improvements + site value: 154,495 + 23,500 + 31,500 + 250,000 = **459,495**. This matches `ValueIndicatedByCostApproachAmount`.
  - Barn: 15,000 − 9,000 = 6,000.
  - Garage: 15,554 + 9,446 − 7,500 = 17,500.
  - MH2: 201,410 + 38,500 + 31,500 = 271,410.
  - **But** dwelling 1,568 sq ft × $95.66 = 149,994.88, printed as **$149,995**. Validators need rounding tolerance (at least ±$1 per line, or ±0.5% on per-sq-ft lines).
- **Licensing:** each XML carries "Copyright 2026 Fannie Mae and Freddie Mac". The scenarios are "not representative of real appraisals" and use fabricated data. Source: [SFREP](https://blog.sfrep.com/2025/08/29/sfreps-uad-3-6-schedule/). **[Confirmed]** Using them for internal testing is very likely fine. Redistributing them, for example committing them to a public repo, should be checked against GSE terms of use, which I did not find. **[Inference]**

### 1.3 How a product can detect and use these before any OCR or LLM

Ordered cheapest and most reliable first. **[Inference: standard practice; the individual facts it relies on are confirmed above]**

1. **Upload type sniffing:** accept `.xml`, `.zip` (UAD 3.6 package) and `.env`/AI-Ready packages.
   - Detect MISMO by root `MESSAGE` with namespace `http://www.mismo.org/residential/2009/schemas` (3.x), or the 2.6 `VALUATION_RESPONSE` root.
   - Parse fields via XPath maps built from Appendix A-1 (3.6) or the UAD 2.6 spec.
   - Extract the base64 PDF from 2.6 XML so the reviewer still sees pages.
2. **PDF probes**, all lossless and fast (pypdf or pikepdf):
   - `/Root/Names/EmbeddedFiles` and file-attachment annotations, looking for `.xml`
   - `/AcroForm/Fields` with values. A non-empty dictionary means fillable fields survived; read them by field name.
   - `/AcroForm/XFA`
   - XMP metadata, for software producer fingerprinting (e.g. TOTAL, ACI, ClickFORMS) to pick a layout profile
3. **Text layer** (already done via pdfplumber) versus scanned. Basis already branches on this.
4. When structured data is found, it becomes the **reference value**, with provenance `source="mismo_xml"` and confidence 1.0, and **still** gets cross-checked against the PDF text. The XML and the PDF can disagree after revisions. Basis's revision model should treat a mismatch as an issue, not silently prefer one. **[Inference]**

Expected hit rate for the XML path on Basis's current inputs: low. Measure it: log how often each probe fires. **[Inference]**

---

## 2. What a cost segregation study needs from the appraisal

Primary source: **IRS Publication 5653, Cost Segregation Audit Techniques Guide, revision 2-6-2025** ([PDF](https://www.irs.gov/pub/irs-pdf/p5653.pdf); I downloaded it and read the text locally). Page numbers are the guide's printed pages.

What the ATG says **[Confirmed, quoted]**:
- p.37, Acquired or Used Properties: studies on used property should be done by qualified appraisers and should "properly allocate the purchase price between the non-depreciable land, building and personal property based on their value as of the date of purchase" (citing *AmeriSouth XXXII*, T.C. Memo. 2012-67).
- p.37: "Land value is always determined first and is based on 'highest and best use.'" The remainder goes to the building and other assets.
- p.38: when original construction documents are missing, a quality study uses "other corroborating evidence (e.g., purchase/lease agreements, appraisals)". It reviews the purchase agreement first.
- p.27, Chapter 3: when construction costs are unavailable, they "must be estimated using the construction cost data, methods, and techniques normally employed for a property appraisal". This means replacement cost new (RCN) adjusted for physical, functional and economic depreciation, so that **RCNLD plus land FMV should reasonably approximate the purchase price**.
- p.64, Real Estate Allocations:
  - Lump-sum allocation follows Treas. Reg. §1.167(a)-5.
  - Land FMV is at highest and best use "as though vacant".
  - It is "inappropriate to estimate the value of the land by subtracting the estimated value of the improvements from the lump real estate price".
  - Examiners should "be wary if a cost segregation study relies solely on local assessed values".
- p.66–67, examiner steps:
  - A written buyer/seller allocation in the contract is binding on the taxpayer (§1060, *Danielson*).
  - Substantiate the price with escrow and payment records.
  - "Request a copy of the appraisal … The appraisal should indicate the value of the land and improvements separately."
  - Older properties must be adjusted for depreciation.

Practitioner sources [secondary]: [EisnerAmper on land value](https://www.eisneramper.com/insights/real-estate/land-value-and-the-cost-segregation-study-0726/), [ETS on 80/20 vs assessor ratio](https://engineeredtaxservices.com/ets_resources/land-allocation-can-i-use-the-80-20-split-or-county-tax-value/), [WCG on DIY limits](https://wcginc.com/kb-rental-property/pushing-your-diy-cost-seg-envelope/). They agree that the assessor ratio is common, rule-of-thumb splits are weakest, and an appraisal with a separate land value is strongest. They also note that many appraisals value the whole property without a split.

GSE facts that constrain availability **[Confirmed]**:
- The [Fannie Mae Selling Guide B4-1.3-10](https://selling-guide.fanniemae.com/sel/b4-1.3-10/cost-and-income-approach-value) does not require the cost approach except for manufactured homes, unless USPAP credibility requires it.
- The legacy Form 70/1004 cost section header reads "COST APPROACH TO VALUE (not required by Fannie Mae)". Verified in the [Freddie Mac Form 70 PDF](https://sf.freddiemac.com/docs/pdf/forms/70.pdf), whose blank form has no AcroForm.

### Field priority for Basis (cost-seg view)

| Field | Tier | Why (source) |
|---|---|---|
| Site/land value (opinion of site value) and its method | **Critical when present; record "not developed" as a first-class state** | Land is valued first, and the appraisal "should indicate the value of land and improvements separately" (ATG p.37, p.67). Often absent (Fannie B4-1.3-10). |
| Contract price and contract date | **Critical** | Basis is the purchase price. Allocation is "as of the date of purchase" (ATG p.37). The appraisal is corroboration only; the closing statement is authoritative. **[Inference]** |
| Effective date of appraisal | **Critical** | Value must be as of the purchase date. The gap between effective date and contract date should be checked. **[Inference]** |
| Final market value (reconciled) | **Critical** | Denominator for proportional allocation under §1.167(a)-5 (ATG p.64). |
| Cost approach block: RCN per line (area × $/sf), garage, total cost new, physical/functional/external depreciation, depreciated cost of improvements, as-is site improvements, indicated value | **High** (critical if the study uses it) | RCN to RCNLD is the method the ATG names for acquired property (p.27). Site improvements are a direct input for 15-year land-improvement property. **[Inference]** |
| GLA, basement area/finish, garage area/type, outbuildings | **High** | Sizes the engineering take-off and cost model. Needed to reconcile RCN lines. **[Inference]** |
| Year built, effective age, remaining economic life, condition and quality ratings (C1–C6, Q1–Q6) | **High** | The ATG requires depreciation and condition adjustments for older property (p.67). |
| Property type, units, design, stories | **High** | Selects 27.5-year residential rental vs 39-year nonresidential, and the per-unit treatment. **[Inference]** |
| Site area (with units: sq ft vs acres), zoning, highest and best use | **Medium** | Supports the land value and HBU statement (ATG p.64). |
| Improvements list (amenities, pool, fencing, decks, appliances, HVAC) | **Medium** | Candidate §1245 and land-improvement items, cross-checked against photos and the vision pipeline. **[Inference]** |
| Assessor parcel number, taxes, assessed land/improvement split (often in addenda or public records) | **Medium** | Fallback allocation method. The ATG warns against relying *solely* on it (p.64). |
| Comparable sales grid, land comparables | **Nice-to-have** | Supports the defensibility of value and site value. Not used in the allocation arithmetic. |
| Rooms, bedrooms, bathrooms | **Low for cost seg** | Useful for validation (rooms ≥ bed + bath), but they do not move the allocation. |
| Placed-in-service date | **Not in the appraisal** | Comes from closing documents or when the property is ready and available for rent. The appraisal effective date is not a substitute. **[Inference, standard tax practice]** |

**Observation about Basis today [Confirmed by reading `backEnd/basis/extraction/field_mappings.py` and `validation.py`]:**
- Critical fields: address, contract price, year built, total rooms, bedrooms, bathrooms, GLA, final value, effective date.
- `cost_approach.site_value` exists in the catalogue but is not critical, and nothing in `backEnd/basis/` consumes it yet (grep).
- The only cost-approach rule is "final value within 50% of the cost approach". There is no line arithmetic.

---

## 3. State of the art for URAR-style extraction

### 3.1 Cloud document AI

**Azure Document Intelligence v4.0 (2024-11-30 GA)** [Confirmed]
- Prebuilt mortgage models: `prebuilt-mortgage.us.1003`, `.1004`, `.1005`, `.1008`, `.closingDisclosure`. English only. Up to 2,000 pages and 500 MB on S0. Source: [MS Learn: mortgage documents](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept-mortgage-documents).
- **1004 schema** ([GitHub schema](https://github.com/Azure-Samples/document-intelligence-code-samples/blob/main/schema/2024-11-30-ga/us-mortgage/1004.md)):
  - Has: Subject (address, APN, taxes, rights, assignment type), Contract (`ContractPrice`, `ContractDate`), Neighborhood checkboxes, Site (utilities, FEMA), Improvements (`YearBuilt`, `EffectiveAgeInYears`, `FoundationType`, `BasementArea`, `BasementFinish`, units, type), Sales comparison (**only the three comp sale prices and the indicated value**), Reconciliation (`IndicatedValueByCostApproach`, `AppraisedMarketValue`, `AppraisalEffectiveDate`), Appraiser block plus signature.
  - **Missing:** GLA, site area, **site value**, RCN lines, depreciation, as-is site improvements, garage, room counts, condition/quality.
  - **Conclusion:** useful as a cheap second opinion on roughly 15 overlapping fields. It does not cover the cost-seg core. **[Inference from confirmed schema]**
  - It is also trained on the legacy 1004 layout. I found no statement about UAD 3.6 URAR support, so expect it to degrade on the new layout. **[Inference: test it on D-1]**
- Custom models ([MS Learn: custom models](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/train/custom-model)):
  - Training needs about 5 labeled documents minimum.
  - Neural models are recommended over template models "for higher accuracy" and handle layout variation.
  - Template models suit fixed forms and need one model per variation.
  - Both support selection marks.
  - v4.0 neural adds table, row and cell confidence plus overlapping fields.
- **Azure Content Understanding** (GA late 2025; CU 1.0 GA, 2.0 preview): an LLM-based analyzer with **per-field grounding (source regions) and 0–1 confidence**, for both "extract" and "generate" fields. Sources: [MS Learn: CU document overview](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/overview), [GA blog](https://devblogs.microsoft.com/foundry/azure-content-understanding-is-now-generally-available/). I did not verify the exact parameter names.

**AWS Textract Analyze Lending** [Confirmed]
- Classifies and splits loan packages, then extracts per class.
- The class enumeration returned in `GetLendingAnalysisSummary.UndetectedDocumentTypes` lists 1003, 1005, 1008, W-2, paystubs, bank statements, HUD-92900-B and others. It has **no 1004 or appraisal class**.
- Source: [AWS docs](https://docs.aws.amazon.com/textract/latest/dg/lending-document-classification-extraction.html). Generic Textract Queries and Forms would be the only option.

**Google Document AI** [Confirmed]
- Lending parsers cover bank statements, W-2, paystubs and IDs. **No appraisal processor.**
- Options: Form Parser v2.0 (KV, checkboxes, tables), Layout Parser, and **Custom Extractor (generative; v1.5 = Gemini 2.5 Flash, v1.5-pro = Gemini 2.5 Pro, GA)**.
- Source: [processor list](https://docs.cloud.google.com/document-ai/docs/processors-list).

### 3.2 Specialized appraisal vendors

- **Veros**: historically the GSE-era PDF-to-XML translator. In 2011 it charged $3.50 per extraction (Level 1), $18 (Level 2 with inconsistency checks) and $12 per page for manual rekeying. It said PDFs remained "arguably the predominant format". [HousingWire 2011](https://www.housingwire.com/articles/slow-death-appraisal-pdf/). **[Vendor claim, dated]**
- **Clear Capital AURA / ClearCollateral Review**: converts **PDF-only URAR appraisals into MISMO 2.6 XML**, then runs 89 ClearQC consistency and completeness rules. Claims 50% less manual review. [Clear Capital](https://www.clearcapital.com/now-available-automated-collateral-review-on-pdf-only-appraisal-volume-in-aura). **[Vendor claim]** Notable because the industry pattern is *PDF to standard schema to rules engine*.
- **Sensible**: open SenseML configs for URAR 1004. Uses deterministic anchors on the fixed layout, with no LLM needed, and extracts the full comp grid. [Sensible blog](https://www.sensible.so/blog/how-to-extract-data-from-residential-appraisal-reports-with-sensible), [doc type page](https://www.sensible.so/doc-types/appraisals-title-reports). **[Vendor claim]**
- **LightBox (CRE)**: extraction from narrative commercial appraisals, "40+ fields per comp", ">300 data points per appraisal". [LightBox Fundamentals](https://www.lightboxre.com/lightbox-fundamentals/). **[Vendor claim]** Relevant if Basis gets commercial narrative appraisals, which have no standard form.
- Workflow and AMC platforms (Reggora, Mercury/Cotality, ICE Appraiser Connect, a la mode, ACI, SFREP) are where the XML lives. They are integration targets, not extractors. **[Confirmed for a la mode, SFREP, ICE docs; inference for the rest]**
- Not found or not verified: Ocrolus, ACI Analytics and Restb.ai claims about appraisal *field* extraction.

### 3.3 LLM / VLM approaches and grounding

- **Grounded extraction APIs:**
  - Reducto returns per-field citations with normalized bounding boxes and high/low plus numeric confidence ([docs](https://docs.reducto.ai/extraction/citations)).
  - LandingAI ADE attaches page plus box to parsed chunks and maps fields to chunk IDs. Its per-field confidence status is unclear. [ParseBench (arXiv 2604.08538)](https://arxiv.org/pdf/2604.08538) found its boxes coarse.
  - Azure Content Understanding: per-field source plus confidence (above).
  - **[Vendor docs]**
- **Anthropic Citations (if Basis routes Claude):** PDF citations are `page_location` (page range) over the text layer. Image-only scanned pages are not citable. **Citations cannot be combined with structured outputs (400 error).** [Claude docs](https://platform.claude.com/docs/en/build-with-claude/citations). **[Confirmed]** In practice, a common workaround is to ask the model to return `{value, page, quote}` in JSON and verify the quote yourself. **[Inference]**
- **Benchmarks (mixed, task-dependent):**
  - A 2026 public-sector study found VLMs generally beat OCR+LLM, but most configurations scored poorly, and layout-preserving OCR helped a lot ([arXiv 2608.18289](https://arxiv.org/pdf/2608.18289)).
  - MedRepBench: removing OCR text dropped field recall 10–20% ([arXiv 2508.16674](https://arxiv.org/pdf/2508.16674)).
  - Templated synthetic checks: fine-tuned open VLMs reached F1 > 0.98, above zero-shot commercial models. Claude Sonnet 4.5 "collapses on Date" ([arXiv 2609.15706](https://arxiv.org/abs/2609.15706)).
  - **Takeaway [Inference]:** page image **plus** text layer beats either alone, and per-field failure modes are model-specific. Measure per field and do not trust a headline number.
- **Calibration:** LLM verbalized confidence is systematically **overconfident**. Failure-prediction AUROC is only about 0.52–0.61 in Xiong et al., ICLR 2024 ([arXiv 2306.13063](https://arxiv.org/abs/2306.13063)). **[Confirmed]** So model-reported confidence must not drive review routing on its own. Basis already caps LLM-fallback confidence at 0.6, which is consistent with this.

### 3.4 Failure modes specific to appraisals [Inference unless noted]

- **Addenda override form values.** Examples: "see addendum" in a box, a corrected GLA in the sketch addendum, a site value in a "Supplemental" page. Text-layer label matching picks up the form box and misses the correction. *Mitigation:* search all pages for each field label and alias, and flag disagreement.
- **Repeated values with conflicts.** GLA appears in the improvements section, the sales grid subject column, the cost approach and the sketch. Effective date appears in the reconciliation and the certification. Appraised value appears in the reconciliation and the signature block. These are the **best free validators**: they should agree.
- **Grid columns.** The subject vs comp columns in the sales grid are a classic mis-assignment, where a comp's GLA is read as the subject's.
- **Checkboxes and selection marks** (occupant, property rights, condition): text layers often encode an "X" glyph near a label. Azure selection marks help. Template position matters.
- **Units:** site area in acres vs sq ft (the SF3 land comps are in acres). Basement "sf" vs "%finished". Currency strings like "$(9,000)" for negative depreciation, as seen in MH1.
- **Multi-unit** (1025 / 2-to-4 unit): per-unit GLA vs gross building area. The UAD 3.6 2-to-4 sample comment distinguishes "finished living area" per unit from "Gross Building Finished Area". **[Confirmed in sample XML]**
- **Scans:** no text layer, so OCR or VLM only. Basis routes these to review today.
- **"Not developed" vs "missing":** an empty cost approach is often legitimate (Fannie B4-1.3-10). An extractor that "fills" it from elsewhere is hallucinating.
- **Layout drift:** legacy 1004 (Form 70, March 2005) vs UAD 3.6 URAR vs software-specific addenda and vendor fonts.

---

## 4. Evaluating document field extraction properly

### 4.1 Metrics (per field, never a single global score)

- **Normalize before comparing** [standard practice]:
  - currency to integer cents
  - dates to ISO
  - areas to sq ft with an explicit unit
  - enumerations (C1–C6, Q1–Q6, occupant, etc.) to canonical codes
  - addresses to USPS-style components
- **Numeric fields:** exact match after normalization, plus a tolerance band only where the document itself rounds (e.g. ±$1 on computed lines; see MH1). Report both "exact" and "within tolerance".
- **Strings** (names, legal description): ANLS (normalized Levenshtein with a 0.5 threshold), or **ANLS*** for generative outputs, which handles lists and dicts and missing keys ([arXiv 2402.03848](https://arxiv.org/abs/2402.03848); `pip install anls_star`). **[Confirmed]**
- **Presence-aware scoring.** Fields can legitimately be absent, like site value. Use per-field confusion counts:
  - TP: correct value
  - FP: a value emitted when absent, or a wrong value
  - FN: present but not emitted
  - TN: absent and correctly reported absent

  Then report **precision, recall and "wrong-value rate"** separately. A wrong value is worse than a missing one for a reviewer. **[Inference: DocILE and KIE practice]**
- **Grounding accuracy:**
  - **page accuracy**: is the cited page one where the value appears?
  - **quote/box accuracy**: does the cited quote or box contain the value's characters?
  - DocILE uses a pseudo-character-center criterion (the predicted box must contain all the field's character centers and none of the others) instead of IoU ([arXiv 2302.05658](https://arxiv.org/pdf/2302.05658)). **[Confirmed]** For Basis, page accuracy plus "quote is a substring of that page's text layer" is a cheap, strict proxy. **[Inference]**
- **Calibration and routing:**
  - Reliability diagram and ECE per confidence bucket ([Guo et al. 2017](https://arxiv.org/abs/1706.04599)).
  - For routing, a **risk–coverage curve** ([Geifman & El-Yaniv 2017](https://arxiv.org/abs/1705.08500)): for each threshold, report % of fields auto-accepted (coverage) against the error rate among the auto-accepted.
  - The operating metric to publish to the owner is **"of the errors that occurred, what % were flagged for review (error recall of the review gate)" against "% of fields flagged (review load)"**. **[Standard]**

### 4.2 Dataset size (computed locally, Wilson 95% and exact binomial)

| Evidence | 95% interval on accuracy |
|---|---|
| 1/1 correct (one client appraisal) | 20.7%–100% |
| 12/12 correct (all D-1 samples) | 75.8%–100% |
| 30/30 | 88.7%–100% |
| 95/100 | 88.8%–97.9% |
| 290/300 | 94.0%–98.2% |

- Zero errors in *n* documents bounds the error rate at about 3/n (rule of three; one-sided 95%: n=59 gives ≤5%, n=300 gives ≤1%).
- So "verified" for a critical field needs **≥60 independent documents with that field present** to claim ≤5% error. One client appraisal supports a regression test, not an accuracy claim. **[Confirmed arithmetic; the interpretation is standard]**
- Documents, not fields, are the independent unit. Fields within one appraisal share layout and software, so per-field counts across one document are not independent. **[Inference]**

### 4.3 Test data sources

| Source | What | Ground truth | Licensing |
|---|---|---|---|
| **GSE UAD 3.6 Appendix D-1** ([Freddie](https://sf.freddiemac.com/docs/zip/requirements/appendix-d-1-urar-sample-scenarios-and-xml-files.zip), [Fannie](https://fanniemae.com/uad)) | 12 PDFs with matching XML, all property types, including 2 with a full cost approach and 1 with site value | **XML is exact labels** | © Fannie/Freddie; fabricated data; internal test use likely OK; check before redistributing **[Inference]** |
| Appendix D-2/D-3 | Update and Completion report samples with XML | XML | same |
| **Blank legacy forms** ([Form 70/1004](https://sf.freddiemac.com/docs/pdf/forms/70.pdf), Fannie 1004) | Layout reference; can be *synthetically filled* to make labeled legacy-format test PDFs (render known values into the form) | Generated | Government-sponsored forms are widely reproduced; synthetic filling avoids PII **[Inference]** |
| Azure DI Studio mortgage samples | Sample 1004 used by Microsoft | None published | MS sample terms |
| Real client appraisals (Basis's own) | Real software output, addenda, scans | **Hand-labeled by an engineer with page citations** | Private; keep out of git (per the existing Basis label-file practice) |

- I did not find a public, licensed corpus of *real* filled 1004s with labels. Scraped "sample appraisal" PDFs online are real people's property data with unclear licenses. Avoid them. **[Inference]**

---

## 5. Recommendation for Basis

Context: Basis currently runs pdfplumber text and tables, then Azure DI `prebuilt-layout` with KV pairs, then regex and label merge, then cleaning and validation, then one structured LLM call for missing or failed fields, then a review gate. Confirmed in `backEnd/basis/extraction/pipeline.py`. There is one real client appraisal today.

### Architecture: "lossless first, grounded candidates, LLM for gaps, validators decide"

1. **Stage 0: lossless intake (new, small).**
   - Accept MISMO XML (2.6 and 3.6) and the UAD 3.6 ZIP.
   - Probe PDFs for EmbeddedFiles and AcroForm values.
   - Map to the Basis catalogue via Appendix A-1 / G-1. When it hits, the values are reference values with confidence 1.0 and provenance; the PDF is still shown for review.
   - *Why:* zero model error, cheap. The GSEs designed the data to be machine-read.
   - *Risk:* a low hit rate on the PDFs taxpayers hold. Measure it, and add an intake prompt: "if you have the MISMO XML or ZIP from your lender or appraiser, upload it".
2. **Stage 1: layout profile detection (new).**
   - Classify the document as legacy 1004/Form 70, UAD 3.6 URAR, 1025, 1073, commercial narrative, or unknown, using a text-layer fingerprint ("Uniform Residential Appraisal Report" plus section headers; the UAD 3.6 summary page differs). An LLM is only needed for "unknown".
   - Route per profile. The legacy regex and label maps should not run against the UAD 3.6 layout.
3. **Stage 2: keep Azure DI layout plus text layer as the candidate generator** (existing).
   - It yields page numbers and polygons for free, which is the citation the reviewer UI needs.
   - Optionally add `prebuilt-mortgage.us.1004` as a *second voter* on the ~15 overlapping fields, but only if a measured agreement gain justifies the extra per-page cost. Test on D-1 and the client file first.
4. **Stage 3: multimodal LLM pass** (an upgrade of the current text-only fallback).
   - For missing, conflicting or validator-failed fields, send the **page image(s) plus that page's text**, chosen by the candidate pages.
   - Require JSON `{value, page, verbatim_quote, status: present|not_developed|absent}`.
   - **Verify mechanically**: the quote must be a substring of that page's text (after whitespace normalization), and the value must parse from the quote. Otherwise reject or route to review.
   - *Why:* this grounds the LLM without relying on provider citation features, which, for Claude, cannot be combined with structured outputs. It also catches hallucinated values. On scans (no text layer), the quote check falls back to Azure OCR text for that page.
   - Model choice per the project's cost-over-model rule: pick the cheapest model that matches measured per-field accuracy on the eval set.
5. **Stage 4: deterministic validators as the review router.** Strengthen `validation.py`:
   - **Cost-approach arithmetic:**
     - each area × $/sf ≈ line (±$1 or ±0.5%)
     - Σ lines = total cost new
     - total − (physical + functional + external) = depreciated cost of improvements
     - depreciated improvements + as-is site improvements + site value = indicated value (±$1–$5)
     - legacy 1004 and UAD 3.6 have different line structures (UAD 3.6 has dwellings + outbuildings + site improvements + site value, as verified in MH1 and MH2)
   - **Cross-section agreement:**
     - GLA in improvements = GLA in the sales grid subject column = GLA in the cost approach dwelling line
     - final value in the reconciliation = value on the appraiser certification page
     - effective date in the reconciliation = effective date at signature
     - indicated value by cost approach in the reconciliation = cost-approach section total
     - contract price in the contract section = contract price in the sales grid subject column
   - **Plausibility and ranges:**
     - site value / final value within a regional band (flag outside 5–60%)
     - effective age ≤ actual age
     - effective date within ±12 months of the contract date
     - $/sf of RCN within a band
     - acres vs sq ft sanity
   - **Allocation readiness:** if site value is absent and the cost approach is "not developed", emit an explicit `land_value_not_in_appraisal` issue telling the engineer to use another ATG-acceptable method. Do not let the LLM invent one.
   - *Why:* validators are model-independent, exact on the GSE samples, and target the numbers that move the tax result. Model-reported confidence is poorly calibrated (Xiong 2024).
6. **Re-tier critical fields for cost seg:**
   - promote site value (with an explicit "not developed" state), the cost-approach block when present, effective date and contract date
   - demote rooms, bedrooms and bathrooms to validators-only

   This is a product decision for Bryan. **[Inference]**
7. **Stage 5: evaluation harness** (before changing any of the above):
   - (a) D-1 12 scenarios with the XML as truth, for UAD 3.6 regression
   - (b) synthetic filled legacy Form 70 pages with known values
   - (c) the client appraisal(s) hand-labeled with page citations
   - Per-field TP/FP/FN/TN, wrong-value rate, page-grounding accuracy, review-gate error recall vs review load, Wilson CIs.
   - CI gate: no per-field regression on (a)–(c).
   - Report "verified" only for fields with ≥60 labeled documents. Until then, state the interval honestly. For example: "12/12 on GSE samples; 95% CI 76–100%".

### What not to do (and why)

- **Do not switch to VLM-only.** You would lose Azure's free word polygons and page geometry, and published results show text plus image beats image alone (MedRepBench). **[Inference from cited evidence]**
- **Do not adopt `prebuilt-mortgage.us.1004` as the primary extractor.** Its schema lacks the cost-seg fields. **[Confirmed schema]**
- **Do not train a custom Azure neural model yet.** With one real appraisal there is nothing to train on. Revisit at ~50 labeled documents per layout profile. Microsoft's minimum is 5, but that minimum says nothing about accuracy. **[Inference]**
- **Do not treat an absent cost approach as an extraction failure.** It is often absent by design (Fannie B4-1.3-10; the form header says "not required").

### Open questions to verify empirically (not done here)

- How `prebuilt-layout`, `prebuilt-mortgage.us.1004` and the current pipeline each score on the 12 D-1 PDFs (UAD 3.6 layout). I expect the legacy regex and labels to break; this is unverified.
- Whether the client's appraisal (legacy or 3.6?) has a developed cost approach and site value.
- The exact UAD 2.6 retirement and policy-exception end date (sources conflict: 2027-05-03 vs 2027-05-19).
- GSE terms of use for redistributing the D-1 files.
- Whether Azure Content Understanding's per-field grounding is worth the cost compared with the quote-substring check.

---

### Local artifacts (private eval data, not in the repository)
- IRS ATG, 2-6-2025 revision: `BASIS_EVAL_DATA_DIR/appraisal/forms/p5653.pdf`
- GSE UAD 3.6 D-1 sample scenarios (12 PDF/XML pairs): `BASIS_EVAL_DATA_DIR/appraisal/gse_d1/`
- Freddie Mac Form 70 (legacy 1004) blank: `BASIS_EVAL_DATA_DIR/appraisal/forms/form70_blank.pdf`
